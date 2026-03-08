import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.logging_utils import get_logger, sanitize_log_context
from app.models import Chunk, Document, MailIngestionEvent
from app.services.bill_facts import upsert_bill_fact_for_document
from app.services.governance import apply_legacy_category_guard
from app.services.llm_summary import (
    classify_category_from_summary,
    regenerate_friendly_name_from_summary,
)
from app.services.qdrant import qdrant_payload, upsert_records
from app.services.source_tags import category_labels_for_path, infer_source_type
from app.services.tag_rules import infer_auto_tags

logger = get_logger(__name__)


def apply_summary_to_doc(doc: Document, out: Any) -> tuple[bool, str]:
    summary_en = str((out.short_summary.en if out.short_summary else "") or "").strip()
    summary_zh = str((out.short_summary.zh if out.short_summary else "") or "").strip()
    if out.quality_state == "ok" and (summary_en or summary_zh):
        doc.summary_en = summary_en[:2000]
        doc.summary_zh = summary_zh[:2000]
        doc.summary_last_error = ""
        return (True, "ok")
    detail = ",".join(str(x or "").strip() for x in out.quality_flags if str(x or "").strip())
    doc.summary_last_error = (detail or str(out.quality_state or "needs_regen"))[:240]
    return (False, str(out.quality_state or "quality_not_ok"))


def load_chunk_excerpt(db: Session, doc_id: str, limit: int = 20) -> str:
    chunks = (
        db.execute(select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.chunk_index.asc()).limit(int(limit)))
        .scalars()
        .all()
    )
    return "\n".join(str(item.content or "") for item in chunks)[:3200]


def recompute_category(db: Session, doc: Document, excerpt: str) -> bool:
    source_type = infer_source_type(str(doc.source_path or ""))
    category_recomputed = False
    classified = classify_category_from_summary(
        file_name=doc.file_name,
        source_type=source_type,
        summary_en=doc.summary_en,
        summary_zh=doc.summary_zh,
        content_excerpt=excerpt,
        db=db,
    )
    if classified is not None:
        cat_en, cat_zh, cat_path = classified
        guarded_path, blocked_legacy = apply_legacy_category_guard(cat_path)
        if blocked_legacy:
            logger.warning(
                "legacy_category_blocked",
                extra=sanitize_log_context(
                    {
                        "event": "legacy_category_blocked",
                        "doc_id": doc.id,
                        "original_path": cat_path,
                        "rewritten_path": guarded_path,
                        "pipeline_stage": "map_reduce_classify",
                    }
                ),
            )
        doc.category_label_en = str(cat_en or "")[:128]
        doc.category_label_zh = str(cat_zh or "")[:128]
        doc.category_path = str(guarded_path or "")[:256]
        if blocked_legacy:
            safe_en, safe_zh = category_labels_for_path(doc.category_path)
            doc.category_label_en = str(safe_en or "")[:128]
            doc.category_label_zh = str(safe_zh or "")[:128]
        doc.category_version = "taxonomy-v1"
        category_recomputed = True
    elif (not str(doc.category_label_en or "").strip()) or (not str(doc.category_label_zh or "").strip()):
        default_en, default_zh = category_labels_for_path(doc.category_path)
        doc.category_label_en = str(default_en or "")[:128]
        doc.category_label_zh = str(default_zh or "")[:128]

    guarded_existing, blocked_existing = apply_legacy_category_guard(doc.category_path)
    if blocked_existing:
        logger.warning(
            "legacy_category_blocked",
            extra=sanitize_log_context(
                {
                    "event": "legacy_category_blocked",
                    "doc_id": doc.id,
                    "original_path": doc.category_path,
                    "rewritten_path": guarded_existing,
                    "pipeline_stage": "map_reduce_finalize",
                }
            ),
        )
        safe_en, safe_zh = category_labels_for_path(guarded_existing)
        doc.category_path = str(guarded_existing or "")[:256]
        doc.category_label_en = str(safe_en or "")[:128]
        doc.category_label_zh = str(safe_zh or "")[:128]
    return category_recomputed


def recompute_name_and_facts(db: Session, doc: Document, excerpt: str) -> None:
    renamed = regenerate_friendly_name_from_summary(
        file_name=doc.file_name,
        category_path=doc.category_path,
        summary_en=doc.summary_en,
        summary_zh=doc.summary_zh,
        fallback_en=doc.title_en,
        fallback_zh=doc.title_zh,
        content_excerpt=excerpt,
        db=db,
    )
    if renamed is not None:
        title_en, title_zh = renamed
        doc.title_en = str(title_en or doc.title_en)[:512]
        doc.title_zh = str(title_zh or doc.title_zh)[:512]
        doc.name_version = "name-v2"
    try:
        with db.begin_nested():
            upsert_bill_fact_for_document(db, doc, content_excerpt=excerpt)
    except Exception as exc:
        logger.warning(
            "bill_fact_upsert_error",
            extra=sanitize_log_context(
                {
                    "doc_id": doc.id,
                    "error_code": "bill_fact_upsert_error",
                    "detail": str(exc),
                }
            ),
        )


def recompute_tags(db: Session, doc: Document, excerpt: str, source_type: str) -> tuple[bool, list]:
    mail_subject = ""
    mail_from = ""
    if source_type == "mail":
        row = (
            db.execute(
                select(MailIngestionEvent)
                .where(MailIngestionEvent.attachment_path == str(doc.source_path or ""))
                .order_by(MailIngestionEvent.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is not None:
            mail_subject = str(row.subject or "")
            mail_from = str(row.from_addr or "")
    auto_tags = infer_auto_tags(
        file_name=doc.file_name,
        source_path=doc.source_path,
        source_type=source_type,
        summary_en=doc.summary_en,
        summary_zh=doc.summary_zh,
        content_excerpt=excerpt,
        category_path=doc.category_path,
        mail_from=mail_from,
        mail_subject=mail_subject,
    )
    try:
        with db.begin_nested():
            crud.sync_auto_tags_for_document(db, document_id=doc.id, auto_tag_keys=auto_tags)
        tags_recomputed = True
        doc_tags = crud.get_document_tag_keys(db, doc.id)
    except Exception as exc:
        logger.warning(
            "tags_sync_error",
            extra=sanitize_log_context(
                {
                    "doc_id": doc.id,
                    "error_code": "tags_sync_error",
                    "detail": str(exc),
                }
            ),
        )
        tags_recomputed = False
        doc_tags = []
    doc.updated_at = dt.datetime.now(dt.UTC)
    return (tags_recomputed, doc_tags)


def sync_to_qdrant(db: Session, doc: Document, source_type: str, doc_tags: list) -> bool:
    chunks_for_vector = (
        db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc())).scalars().all()
    )
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
            tags=doc_tags,
            text=chunk.content,
        )
        for chunk in chunks_for_vector
    ]
    if payload_records:
        try:
            upsert_records(payload_records, db=db)
            return True
        except Exception as exc:
            logger.warning(
                "qdrant_upsert_error",
                extra=sanitize_log_context(
                    {
                        "doc_id": doc.id,
                        "error_code": "qdrant_upsert_error",
                        "detail": str(exc),
                    }
                ),
            )
            return False
    return True
