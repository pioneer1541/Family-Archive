import datetime as dt
import hashlib
from pathlib import Path

from app import crud, models
from app.db import SessionLocal


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _create_document(
    db,
    *,
    tmp_path: Path,
    file_name: str,
    status: str = models.DocumentStatus.COMPLETED.value,
    category_path: str = "finance/bills/electricity",
    summary_en: str = "monthly electricity bill",
) -> models.Document:
    file_path = tmp_path / file_name
    file_path.write_text("sample", encoding="utf-8")

    doc = models.Document(
        source_path=str(file_path),
        file_name=file_name,
        file_ext="txt",
        file_size=6,
        sha256=_sha(file_name),
        status=status,
        category_path=category_path,
        summary_en=summary_en,
        source_available_cached=True,
        source_checked_at=dt.datetime.now(dt.UTC),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_create_read_delete_ingestion_job():
    with SessionLocal() as db:
        job = crud.create_ingestion_job(db, ["/tmp/a.txt", "/tmp/b.txt"])
        fetched = crud.get_ingestion_job(db, job.id)
        assert fetched is not None
        assert fetched.id == job.id
        assert "a.txt" in fetched.input_paths

        crud.delete_ingestion_job(db, fetched)
        assert crud.get_ingestion_job(db, job.id) is None


def test_create_and_read_tasks_with_pagination_and_empty_result():
    with SessionLocal() as db:
        rows, total = crud.list_tasks(db, limit=10, offset=0)
        assert rows == []
        assert total == 0

        first = crud.create_task(
            db,
            {
                "title": "Task A",
                "task_type": "summarize_docs",
                "doc_set": [],
                "filters": {},
            },
        )
        second = crud.create_task(
            db,
            {
                "title": "Task B",
                "task_type": "summarize_docs",
                "doc_set": [],
                "filters": {},
            },
        )

        by_id = crud.get_task(db, first.id)
        assert by_id is not None
        assert by_id.id == first.id

        page, total = crud.list_tasks(db, limit=1, offset=0)
        assert total == 2
        assert len(page) == 1
        assert page[0].id in {first.id, second.id}


def test_read_documents_pagination_and_filters(tmp_path: Path):
    with SessionLocal() as db:
        doc1 = _create_document(
            db,
            tmp_path=tmp_path,
            file_name="bill-electricity.txt",
            status=models.DocumentStatus.COMPLETED.value,
            category_path="finance/bills/electricity",
            summary_en="electricity bill from AGL",
        )
        _create_document(
            db,
            tmp_path=tmp_path,
            file_name="policy-home.txt",
            status=models.DocumentStatus.PENDING.value,
            category_path="insurance/home",
            summary_en="home insurance policy",
        )

        db.add(models.DocumentTag(document_id=doc1.id, tag_key="vendor:agl", family="vendor", value="agl", origin="manual"))
        db.commit()

        rows, total = crud.list_documents(
            db,
            status=models.DocumentStatus.COMPLETED.value,
            category_path="finance/bills/electricity",
            tags_all=["vendor:agl"],
            q="electricity",
            limit=1,
            offset=0,
            source_state="all",
        )
        assert total == 1
        assert len(rows) == 1
        assert rows[0].file_name == "bill-electricity.txt"


def test_update_document_tags_and_boundary_duplicate_upsert(tmp_path: Path):
    with SessionLocal() as db:
        doc = _create_document(db, tmp_path=tmp_path, file_name="tags-update.txt")

        updated, invalid = crud.patch_document_tags(
            db,
            document_id=doc.id,
            add=["vendor:agl", "status:important"],
            remove=[],
        )
        assert invalid == []
        assert {row.tag_key for row in updated} == {"vendor:agl", "status:important"}

        updated, invalid = crud.patch_document_tags(
            db,
            document_id=doc.id,
            add=[],
            remove=["status:important"],
        )
        assert invalid == []
        assert {row.tag_key for row in updated} == {"vendor:agl"}

        created = crud.upsert_ignored_paths(
            db,
            ["/tmp/dup-path.txt", "/tmp/dup-path.txt", "/tmp/dup-path.txt"],
            reason="test",
        )
        db.commit()
        assert created == 1
