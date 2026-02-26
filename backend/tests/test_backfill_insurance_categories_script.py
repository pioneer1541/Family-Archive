from pathlib import Path

from app import crud
from app.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus
from scripts import backfill_insurance_categories as script


def _insert_insurance_doc(path: Path, *, file_name: str, category_path: str) -> str:
    db = SessionLocal()
    try:
        doc = Document(
            source_path=str(path),
            file_name=file_name,
            file_ext="pdf",
            file_size=int(path.stat().st_size) if path.exists() else 0,
            sha256="e" * 64,
            status=DocumentStatus.COMPLETED.value,
            category_path=category_path,
            category_label_en="Health Insurance",
            category_label_zh="医保资料",
            title_zh="旧标题",
            summary_zh="AAMI车辆保险保单，包含保险证明和保费信息。",
            summary_en="AAMI car policy account and certificate of insurance.",
        )
        db.add(doc)
        db.flush()
        db.add(
            Chunk(
                document_id=doc.id,
                chunk_index=0,
                content="AAMI Car Policy Account Certificate of Insurance",
                token_count=10,
                embedding_status="ready",
            )
        )
        db.commit()
        return str(doc.id)
    finally:
        db.close()


def test_backfill_insurance_categories_dry_run(tmp_path: Path, monkeypatch):
    target = tmp_path / "AAMI_Car_Policy_Account_MPA167699547.pdf"
    target.write_bytes(b"pdf")
    doc_id = _insert_insurance_doc(target, file_name=target.name, category_path="health/insurance")

    monkeypatch.setattr(
        script,
        "classify_category_from_summary",
        lambda **_kwargs: ("Vehicle Insurance", "车辆保险", "home/insurance/vehicle"),
    )
    monkeypatch.setattr(script, "regenerate_friendly_name_from_summary", lambda **_kwargs: ("AAMI Car Policy", "AAMI车辆保险保单"))
    monkeypatch.setattr(script, "infer_auto_tags", lambda **_kwargs: ["topic:insurance"])
    monkeypatch.setattr(script, "upsert_records", lambda _records: None)

    output = tmp_path / "insurance_backfill_dry.json"
    report = script.backfill_insurance_categories(apply=False, output=output)
    assert int(report.get("candidate_count") or 0) >= 1
    assert int(report.get("changed") or 0) >= 1

    db = SessionLocal()
    try:
        found = crud.get_document(db, doc_id)
        assert found is not None
        assert str(found.category_path or "") == "health/insurance"
    finally:
        db.close()


def test_backfill_insurance_categories_apply(tmp_path: Path, monkeypatch):
    target = tmp_path / "AAMI_Car_Certificate_of_Insurance.pdf"
    target.write_bytes(b"pdf")
    doc_id = _insert_insurance_doc(target, file_name=target.name, category_path="health/insurance")

    monkeypatch.setattr(
        script,
        "classify_category_from_summary",
        lambda **_kwargs: ("Vehicle Insurance", "车辆保险", "home/insurance/vehicle"),
    )
    monkeypatch.setattr(script, "regenerate_friendly_name_from_summary", lambda **_kwargs: ("AAMI Car Certificate", "AAMI车辆保险证明"))
    monkeypatch.setattr(script, "infer_auto_tags", lambda **_kwargs: ["topic:insurance", "vendor:aami"])
    monkeypatch.setattr(script, "upsert_records", lambda _records: None)

    output = tmp_path / "insurance_backfill_apply.json"
    report = script.backfill_insurance_categories(apply=True, output=output)
    assert int(report.get("candidate_count") or 0) >= 1
    assert int(report.get("changed") or 0) >= 1

    db = SessionLocal()
    try:
        found = crud.get_document(db, doc_id)
        assert found is not None
        assert str(found.category_path or "") == "home/insurance/vehicle"
        tags = crud.get_document_tag_keys(db, doc_id)
        assert "topic:insurance" in tags
        assert "vendor:aami" in tags
    finally:
        db.close()
