import datetime as dt
from pathlib import Path
from types import SimpleNamespace

from app import models
from app.db import SessionLocal
from app.services import docs as docs_service


def _create_document(
    db,
    *,
    source_path: str,
    file_name: str,
    status: str = models.DocumentStatus.COMPLETED.value,
    category_path: str = "archive/misc",
) -> models.Document:
    doc = models.Document(
        source_path=source_path,
        file_name=file_name,
        file_ext=Path(file_name).suffix.lstrip(".") or "txt",
        file_size=128,
        sha256=(file_name.encode("utf-8").hex() * 4)[:64].ljust(64, "a"),
        status=status,
        title_en=file_name,
        title_zh=f"文档 {file_name}",
        summary_en="summary",
        summary_zh="摘要",
        category_path=category_path,
        category_label_en="Archive",
        category_label_zh="归档",
        source_available_cached=True,
        source_checked_at=dt.datetime.now(dt.UTC),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_document_create_update_delete_and_status_management(tmp_path: Path):
    src = tmp_path / "doc-create-update-delete.txt"
    src.write_text("sample", encoding="utf-8")

    with SessionLocal() as db:
        doc = _create_document(
            db,
            source_path=str(src),
            file_name="doc-create-update-delete.txt",
            status=models.DocumentStatus.PENDING.value,
        )

        fetched = db.get(models.Document, doc.id)
        assert fetched is not None
        assert fetched.status == models.DocumentStatus.PENDING.value

        fetched.status = models.DocumentStatus.COMPLETED.value
        fetched.title_en = "Updated Title"
        db.commit()
        db.refresh(fetched)
        assert fetched.status == models.DocumentStatus.COMPLETED.value
        assert fetched.title_en == "Updated Title"

        db.delete(fetched)
        db.commit()
        assert db.get(models.Document, doc.id) is None


def test_build_related_docs_filters_completed_dedupes_and_keeps_order(tmp_path: Path):
    src1 = tmp_path / "available-1.txt"
    src1.write_text("a", encoding="utf-8")
    src2 = tmp_path / "available-2.txt"
    src2.write_text("b", encoding="utf-8")

    with SessionLocal() as db:
        completed = _create_document(
            db,
            source_path=str(src1),
            file_name="completed.txt",
            status=models.DocumentStatus.COMPLETED.value,
        )
        pending = _create_document(
            db,
            source_path=str(src2),
            file_name="pending.txt",
            status=models.DocumentStatus.PENDING.value,
        )
        db.add(
            models.DocumentTag(
                document_id=completed.id,
                tag_key="vendor:agl",
                family="vendor",
                value="agl",
                origin="manual",
            )
        )
        db.commit()

        related = docs_service._build_related_docs(
            db,
            [pending.id, completed.id, completed.id],
            cap=6,
        )

        assert len(related) == 1
        assert related[0].doc_id == completed.id
        assert related[0].file_name == "completed.txt"
        assert related[0].tags == ["vendor:agl"]
        assert related[0].source_available is True


def test_fill_chunks_from_doc_scope_filters_by_status_and_source_and_dedupes(tmp_path: Path):
    src_ok = tmp_path / "chunk-ok.txt"
    src_ok.write_text("ok", encoding="utf-8")

    with SessionLocal() as db:
        good = _create_document(
            db,
            source_path=str(src_ok),
            file_name="good.txt",
            status=models.DocumentStatus.COMPLETED.value,
        )
        missing = _create_document(
            db,
            source_path=str(tmp_path / "missing.txt"),
            file_name="missing.txt",
            status=models.DocumentStatus.COMPLETED.value,
        )
        _create_document(
            db,
            source_path=str(src_ok),
            file_name="pending.txt",
            status=models.DocumentStatus.PENDING.value,
        )

        c1 = models.Chunk(document_id=good.id, chunk_index=0, content="chunk one", token_count=2)
        c2 = models.Chunk(document_id=good.id, chunk_index=1, content="chunk two", token_count=2)
        c3 = models.Chunk(document_id=missing.id, chunk_index=0, content="chunk missing", token_count=2)
        db.add_all([c1, c2, c3])
        db.commit()
        db.refresh(c1)

        existing = {c1.id}
        out = docs_service._fill_chunks_from_doc_scope(db, [good.id, missing.id], existing, cap=5)

        assert len(out) == 1
        assert out[0]["doc_id"] == good.id
        assert out[0]["chunk_id"] == c2.id
        assert out[0]["text"] == "chunk two"


def test_related_docs_selection_uses_evidence_only_mode():
    related_docs = [SimpleNamespace(doc_id="d1"), SimpleNamespace(doc_id="d2")]
    bundle = {
        "route": "bill_attention",
        "related_docs": related_docs,
        "evidence_map": {
            "amount": [
                {"doc_id": "d2", "chunk_id": "c2"},
                {"doc_id": "d2", "chunk_id": "c3"},
            ]
        },
    }

    mode, count = docs_service._apply_related_docs_selection(bundle)

    assert mode == "evidence_only"
    assert count == 1
    assert bundle["evidence_backed_doc_ids"] == ["d2"]
    assert [d.doc_id for d in bundle["related_docs"]] == ["d2"]


def test_related_docs_selection_non_target_route_keeps_existing_mode():
    bundle = {
        "route": "general_qa",
        "related_docs": [SimpleNamespace(doc_id="d1")],
    }

    mode, count = docs_service._apply_related_docs_selection(bundle)

    assert mode == "evidence_plus_candidates"
    assert count == 1
