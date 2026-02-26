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

from app import crud
from app.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus, MailIngestionEvent
from app.services.bill_facts import upsert_bill_fact_for_document
from app.services.llm_summary import classify_category_from_summary, regenerate_friendly_name_from_summary
from app.services.qdrant import qdrant_payload, upsert_records
from app.services.source_tags import infer_source_type
from app.services.tag_rules import infer_auto_tags


_INSURANCE_HINTS = (
    "insurance",
    "policy",
    "certificate of insurance",
    "aami",
    "medicare",
    "hospital",
    "extras",
    "保单",
    "保险",
    "医保",
    "医疗保险",
    "车辆保险",
    "车险",
)


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _default_output() -> Path:
    return (ROOT_DIR.parent / "data" / "insurance_category_backfill_report.json").resolve()


def _is_insurance_candidate(doc: Document) -> bool:
    if str(doc.status or "") != DocumentStatus.COMPLETED.value:
        return False
    cp = str(doc.category_path or "").lower()
    if "insurance" in cp:
        return True
    merged = "\n".join(
        [
            str(doc.file_name or ""),
            str(doc.title_en or ""),
            str(doc.title_zh or ""),
        ]
    ).lower()
    return any(token in merged for token in _INSURANCE_HINTS)


def _excerpt_for_doc(db, doc_id: str, *, limit: int = 24, cap: int = 4000) -> str:
    rows = (
        db.execute(select(Chunk.content).where(Chunk.document_id == str(doc_id)).order_by(Chunk.chunk_index.asc()).limit(int(limit)))
        .scalars()
        .all()
    )
    return "\n".join(str(item or "") for item in rows)[: int(cap)]


def _mail_context_for_attachment(db, path: str) -> tuple[str, str]:
    row = (
        db.execute(
            select(MailIngestionEvent)
            .where(MailIngestionEvent.attachment_path == str(path or ""))
            .order_by(MailIngestionEvent.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return ("", "")
    return (str(row.subject or ""), str(row.from_addr or ""))


def backfill_insurance_categories(*, apply: bool, output: Path, limit: int = 0) -> dict[str, Any]:
    db = SessionLocal()
    started = _now_iso()
    try:
        rows = db.execute(select(Document).order_by(Document.updated_at.desc())).scalars().all()
        docs = [doc for doc in rows if _is_insurance_candidate(doc)]
        if int(limit) > 0:
            docs = docs[: int(limit)]

        items: list[dict[str, Any]] = []
        changed = 0
        skipped = 0
        failed = 0

        for doc in docs:
            before_category = str(doc.category_path or "")
            before_title_zh = str(doc.title_zh or "")
            before_tags = crud.get_document_tag_keys(db, doc.id)
            excerpt = _excerpt_for_doc(db, doc.id)
            source_type = infer_source_type(str(doc.source_path or ""))

            item = {
                "doc_id": str(doc.id),
                "file_name": str(doc.file_name or ""),
                "before": {
                    "category_path": before_category,
                    "title_zh": before_title_zh,
                    "tags": before_tags,
                },
                "after": {},
                "applied": False,
                "reason": "",
            }

            try:
                classified = classify_category_from_summary(
                    file_name=doc.file_name,
                    source_type=source_type,
                    summary_en=doc.summary_en,
                    summary_zh=doc.summary_zh,
                    content_excerpt=excerpt,
                )
                if classified is None:
                    item["reason"] = "classify_none"
                    skipped += 1
                    items.append(item)
                    continue

                cat_en, cat_zh, cat_path = classified
                new_category = str(cat_path or "").strip()
                renamed = regenerate_friendly_name_from_summary(
                    file_name=doc.file_name,
                    category_path=new_category,
                    summary_en=doc.summary_en,
                    summary_zh=doc.summary_zh,
                    fallback_en=doc.title_en,
                    fallback_zh=doc.title_zh,
                )
                if renamed is not None:
                    doc.title_en = str(renamed[0] or doc.title_en)[:512]
                    doc.title_zh = str(renamed[1] or doc.title_zh)[:512]
                    doc.name_version = "name-v2"

                mail_subject = ""
                mail_from = ""
                if source_type == "mail":
                    mail_subject, mail_from = _mail_context_for_attachment(db, str(doc.source_path or ""))

                auto_tags = infer_auto_tags(
                    file_name=doc.file_name,
                    source_path=doc.source_path,
                    source_type=source_type,
                    summary_en=doc.summary_en,
                    summary_zh=doc.summary_zh,
                    content_excerpt=excerpt,
                    category_path=new_category,
                    mail_from=mail_from,
                    mail_subject=mail_subject,
                )

                if apply:
                    doc.category_label_en = str(cat_en or doc.category_label_en)[:128]
                    doc.category_label_zh = str(cat_zh or doc.category_label_zh)[:128]
                    doc.category_path = new_category[:256]
                    doc.category_version = "taxonomy-v1"
                    crud.sync_auto_tags_for_document(db, document_id=doc.id, auto_tag_keys=auto_tags)
                    upsert_bill_fact_for_document(db, doc, content_excerpt=excerpt)
                    doc.updated_at = dt.datetime.now(dt.UTC)

                    chunk_rows = db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc())).scalars().all()
                    tag_keys = crud.get_document_tag_keys(db, doc.id)
                    payload_records = [
                        qdrant_payload(
                            doc_id=doc.id,
                            chunk_id=chunk.id,
                            doc_lang=doc.doc_lang,
                            category_path=doc.category_path,
                            source_type=source_type,
                            updated_at=doc.updated_at,
                            title_en=doc.title_en,
                            title_zh=doc.title_zh,
                            tags=tag_keys,
                            text=chunk.content,
                        )
                        for chunk in chunk_rows
                    ]
                    if payload_records:
                        upsert_records(payload_records)
                    db.commit()
                    db.refresh(doc)

                after_tags = auto_tags if not apply else crud.get_document_tag_keys(db, doc.id)
                item["after"] = {
                    "category_path": new_category,
                    "title_zh": str((renamed[1] if renamed else doc.title_zh) or ""),
                    "tags": after_tags,
                }
                changed_fields = (
                    before_category != new_category
                    or before_title_zh != str(item["after"]["title_zh"])
                    or sorted(before_tags) != sorted(after_tags)
                )
                if changed_fields:
                    changed += 1
                    item["applied"] = bool(apply)
                    item["reason"] = "updated" if apply else "would_update"
                else:
                    skipped += 1
                    item["reason"] = "no_change"
                items.append(item)
            except Exception as exc:
                failed += 1
                if apply:
                    db.rollback()
                item["reason"] = f"error:{type(exc).__name__}"
                items.append(item)

        if not apply:
            db.rollback()

        report = {
            "generated_at": _now_iso(),
            "apply": bool(apply),
            "candidate_count": len(docs),
            "changed": int(changed),
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
    parser = argparse.ArgumentParser(description="Backfill insurance categories with new leaf taxonomy.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Dry run only (default).")
    group.add_argument("--apply", action="store_true", help="Apply updates.")
    parser.add_argument("--limit", type=int, default=0, help="Limit documents for trial run; 0 means all.")
    parser.add_argument("--output", type=str, default=str(_default_output()), help="Output report path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = backfill_insurance_categories(
        apply=bool(args.apply),
        output=Path(str(args.output)).resolve(),
        limit=max(0, int(args.limit)),
    )
    print(
        json.dumps(
            {
                "apply": report.get("apply"),
                "candidate_count": report.get("candidate_count"),
                "changed": report.get("changed"),
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
