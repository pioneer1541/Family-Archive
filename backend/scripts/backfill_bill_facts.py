#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal
from app.models import BillFact, Chunk, Document, DocumentStatus
from app.services.bill_facts import extract_bill_fact_payload, upsert_bill_fact_for_document


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _default_output() -> Path:
    return (ROOT_DIR.parent / "data" / "bill_facts_backfill_report.json").resolve()


def _excerpt_for_doc(db, doc_id: str, *, limit: int = 24, cap: int = 5000) -> str:
    rows = (
        db.execute(select(Chunk.content).where(Chunk.document_id == str(doc_id)).order_by(Chunk.chunk_index.asc()).limit(int(limit)))
        .scalars()
        .all()
    )
    return "\n".join(str(item or "") for item in rows)[: int(cap)]


def _fact_to_dict(fact: BillFact | None) -> dict[str, Any]:
    if fact is None:
        return {}
    return {
        "amount_due": float(fact.amount_due) if fact.amount_due is not None else None,
        "currency": str(fact.currency or ""),
        "due_date": fact.due_date.isoformat() if fact.due_date else None,
        "billing_period_start": fact.billing_period_start.isoformat() if fact.billing_period_start else None,
        "billing_period_end": fact.billing_period_end.isoformat() if fact.billing_period_end else None,
        "payment_status": str(fact.payment_status or ""),
        "confidence": float(fact.confidence or 0.0),
        "extraction_version": str(fact.extraction_version or ""),
    }


def _payload_to_dict(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        "amount_due": float(payload.get("amount_due")) if payload.get("amount_due") is not None else None,
        "currency": str(payload.get("currency") or ""),
        "due_date": payload.get("due_date").isoformat() if payload.get("due_date") else None,
        "billing_period_start": payload.get("billing_period_start").isoformat() if payload.get("billing_period_start") else None,
        "billing_period_end": payload.get("billing_period_end").isoformat() if payload.get("billing_period_end") else None,
        "payment_status": str(payload.get("payment_status") or ""),
        "confidence": float(payload.get("confidence") or 0.0),
        "extraction_version": str(payload.get("extraction_version") or ""),
    }


def _load_fact(db, doc_id: str) -> BillFact | None:
    return db.execute(select(BillFact).where(BillFact.document_id == str(doc_id))).scalars().first()


def backfill_bill_facts(*, apply: bool, doc_id: str | None, output: Path) -> dict[str, Any]:
    started = _now_iso()
    db = SessionLocal()
    try:
        stmt = select(Document).where(
            Document.status == DocumentStatus.COMPLETED.value,
            Document.category_path.like("finance/bills/%"),
        )
        if str(doc_id or "").strip():
            stmt = stmt.where(Document.id == str(doc_id).strip())
        docs = db.execute(stmt.order_by(Document.updated_at.desc())).scalars().all()

        items: list[dict[str, Any]] = []
        upserted = 0
        skipped = 0
        failed = 0

        for doc in docs:
            before_fact = _load_fact(db, doc.id)
            before = _fact_to_dict(before_fact)
            excerpt = _excerpt_for_doc(db, doc.id)
            payload = extract_bill_fact_payload(doc, content_excerpt=excerpt)

            item = {
                "doc_id": str(doc.id),
                "file_name": str(doc.file_name or ""),
                "category_path": str(doc.category_path or ""),
                "before": before,
                "after": {},
                "status": "",
                "error": "",
            }

            if not apply:
                if payload is None:
                    item["status"] = "would_skip"
                    skipped += 1
                    item["after"] = {}
                else:
                    item["status"] = "would_upsert"
                    upserted += 1
                    item["after"] = _payload_to_dict(payload)
                items.append(item)
                continue

            try:
                out = upsert_bill_fact_for_document(db, doc, content_excerpt=excerpt)
                db.commit()
                if out is None:
                    item["status"] = "skipped"
                    skipped += 1
                    item["after"] = {}
                else:
                    item["status"] = "upserted"
                    upserted += 1
                    item["after"] = _fact_to_dict(out)
            except Exception as exc:  # pragma: no cover - defensive
                db.rollback()
                item["status"] = "failed"
                item["error"] = type(exc).__name__
                failed += 1
            items.append(item)

        if not apply:
            db.rollback()

        report = {
            "started_at": started,
            "finished_at": _now_iso(),
            "apply": bool(apply),
            "doc_id": str(doc_id or ""),
            "candidate_count": len(docs),
            "upserted": int(upserted),
            "skipped": int(skipped),
            "failed": int(failed),
            "items": items,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        db.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill bill_facts for completed finance/bills documents.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Dry run only (default).")
    group.add_argument("--apply", action="store_true", help="Apply backfill to database.")
    parser.add_argument("--doc-id", type=str, default="", help="Optional document id to process.")
    parser.add_argument("--output", type=str, default=str(_default_output()), help="Output report path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    apply = bool(args.apply)
    report = backfill_bill_facts(
        apply=apply,
        doc_id=str(args.doc_id or "").strip() or None,
        output=Path(str(args.output)).resolve(),
    )
    print(
        json.dumps(
            {
                "apply": report.get("apply"),
                "candidate_count": report.get("candidate_count"),
                "upserted": report.get("upserted"),
                "skipped": report.get("skipped"),
                "failed": report.get("failed"),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
