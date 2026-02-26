from pathlib import Path

from app.db import SessionLocal
from app.models import BillFact, Chunk, Document, DocumentStatus
from scripts import backfill_bill_facts as script


def _insert_bill_doc(path: Path, *, file_name: str) -> str:
    db = SessionLocal()
    try:
        doc = Document(
            source_path=str(path),
            file_name=file_name,
            file_ext="pdf",
            file_size=int(path.stat().st_size) if path.exists() else 0,
            sha256="d" * 64,
            status=DocumentStatus.COMPLETED.value,
            category_path="finance/bills/internet",
            category_label_en="Bills",
            category_label_zh="账单与缴费",
            title_zh="2026年2月互联网账单",
            summary_zh="金额109.00澳币，到期日2026年2月23日。",
        )
        db.add(doc)
        db.flush()
        db.add(
            Chunk(
                document_id=doc.id,
                chunk_index=0,
                content="Superloop invoice amount due AUD 109.00 due date 2026-02-23",
                token_count=10,
                embedding_status="ready",
            )
        )
        db.commit()
        return str(doc.id)
    finally:
        db.close()


def test_backfill_bill_facts_dry_run(tmp_path: Path):
    target = tmp_path / "bill_doc.pdf"
    target.write_bytes(b"pdf-bytes")
    doc_id = _insert_bill_doc(target, file_name=target.name)

    output = tmp_path / "report_dry.json"
    report = script.backfill_bill_facts(apply=False, doc_id=doc_id, output=output)
    assert int(report.get("candidate_count") or 0) == 1
    assert int(report.get("upserted") or 0) == 1
    assert int(report.get("failed") or 0) == 0

    db = SessionLocal()
    try:
        found = db.query(BillFact).filter(BillFact.document_id == doc_id).first()
        assert found is None
    finally:
        db.close()


def test_backfill_bill_facts_apply(tmp_path: Path):
    target = tmp_path / "bill_doc_apply.pdf"
    target.write_bytes(b"pdf-bytes")
    doc_id = _insert_bill_doc(target, file_name=target.name)

    output = tmp_path / "report_apply.json"
    report = script.backfill_bill_facts(apply=True, doc_id=doc_id, output=output)
    assert int(report.get("candidate_count") or 0) == 1
    assert int(report.get("upserted") or 0) == 1
    assert int(report.get("failed") or 0) == 0

    db = SessionLocal()
    try:
        found = db.query(BillFact).filter(BillFact.document_id == doc_id).first()
        assert found is not None
        assert float(found.amount_due or 0.0) == 109.0
        assert str(found.currency or "") == "AUD"
    finally:
        db.close()
