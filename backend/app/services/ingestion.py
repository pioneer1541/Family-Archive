import datetime as dt
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from app import crud
from app.celery_app import celery_app
from app.config import get_settings
from app.db import SessionLocal
from app.logging_utils import get_logger, sanitize_log_context
from app.models import (
    Chunk,
    Document,
    DocumentStatus,
    IngestionJob,
    IngestionJobStatus,
    MailIngestionEvent,
)
from app.runtime_config import get_model_setting
from app.services.bill_facts import upsert_bill_fact_for_document
from app.services.document_summary import build_document_summaries
from app.services.friendly_name import generate_friendly_names
from app.services.governance import apply_legacy_category_guard
from app.services.image_hash import compute_image_phash, hamming_distance, is_image_path
from app.services.llm_summary import (
    classify_category_from_summary,
    detect_summary_quality_flags,
    is_low_quality_summary,
    normalize_vehicle_insurance_summary,
    regenerate_friendly_name_from_summary,
)
from app.services.ocr_fallback import get_pdf_page_count
from app.services.parsing import (
    build_bilingual_title,
    chunk_text,
    compute_sha256,
    detect_lang_simple,
    extract_text_from_path,
    file_meta,
)
from app.services.path_scan import discover_files
from app.services.qdrant import (
    delete_records_by_point_ids,
    qdrant_payload,
    stable_point_id,
    upsert_records,
)
from app.services.source_tags import (
    DEFAULT_CATEGORY_PATH,
    category_labels_for_path,
    infer_source_type,
)
from app.services.tag_rules import infer_auto_tags

settings = get_settings()
logger = get_logger(__name__)
UPLOAD_TMP_ROOT = Path("/tmp/fkv_uploads").resolve()


def compact_error_code(raw: str | None) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return "unknown_error"
    text = re.sub(r"[^a-z0-9:_/\-\. ]+", "_", text)
    text = text.replace(" ", "_").replace(".", "_")
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "unknown_error")[:120]


def build_retry_error_code(error_code: str, retry_count: int, max_retries: int) -> str:
    code = compact_error_code(error_code)
    retry_count = max(0, int(retry_count))
    max_retries = max(0, int(max_retries))
    return f"retrying:{retry_count}/{max_retries}:{code}"[:120]


def parse_retry_meta(error_code: str | None) -> tuple[int, int]:
    raw = str(error_code or "").strip().lower()
    if not raw.startswith("retrying:"):
        return (0, 0)
    try:
        header = raw.split(":", 2)[1]
        left, right = header.split("/", 1)
        return (max(0, int(left)), max(0, int(right)))
    except Exception:
        return (0, 0)


def mark_job_retrying(job_id: str, *, error_code: str, retry_count: int, max_retries: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(IngestionJob, job_id)
        if job is None:
            return
        job.status = IngestionJobStatus.RETRYING.value
        job.error_code = build_retry_error_code(error_code, retry_count=retry_count, max_retries=max_retries)
        db.commit()
        logger.warning(
            "ingestion_job_retrying",
            extra=sanitize_log_context(
                {
                    "job_id": job_id,
                    "status": job.status,
                    "retry_count": retry_count,
                    "max_retries": max_retries,
                    "error_code": job.error_code,
                }
            ),
        )
    finally:
        db.close()


def mark_job_terminal_failure(job_id: str, *, error_code: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(IngestionJob, job_id)
        if job is None:
            return
        job.status = IngestionJobStatus.FAILED.value
        job.error_code = compact_error_code(error_code)
        job.finished_at = dt.datetime.now(dt.UTC)
        db.commit()
        logger.error(
            "ingestion_job_failed",
            extra=sanitize_log_context(
                {
                    "job_id": job_id,
                    "status": job.status,
                    "error_code": job.error_code,
                }
            ),
        )
    finally:
        db.close()


def enqueue_ingestion_job(job_id: str, force_reprocess: bool = False, reprocess_doc_id: str | None = None) -> str:
    if settings.celery_task_always_eager:
        try:
            process_ingestion_job(
                job_id,
                force_reprocess=force_reprocess,
                reprocess_doc_id=reprocess_doc_id,
            )
        except Exception as exc:
            code = compact_error_code(f"sync_failed:{type(exc).__name__}")
            mark_job_terminal_failure(job_id, error_code=code)
        return "sync"

    try:
        celery_app.send_task(
            "fkv.ingestion.process_job",
            args=[job_id],
            kwargs={
                "force_reprocess": force_reprocess,
                "reprocess_doc_id": reprocess_doc_id,
            },
        )
        return "celery"
    except Exception:
        try:
            process_ingestion_job(
                job_id,
                force_reprocess=force_reprocess,
                reprocess_doc_id=reprocess_doc_id,
            )
        except Exception as exc:
            code = compact_error_code(f"sync_fallback_failed:{type(exc).__name__}")
            mark_job_terminal_failure(job_id, error_code=code)
        return "sync"


def _is_under_upload_tmp_root(path: str) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        resolved = Path(raw).resolve()
    except Exception:
        return False
    return str(resolved).startswith(f"{UPLOAD_TMP_ROOT}{os.sep}")


def _cleanup_upload_dirs(paths: list[str]) -> int:
    cleaned_dirs: set[str] = set()
    for raw in paths:
        if not _is_under_upload_tmp_root(raw):
            continue
        try:
            upload_dir = Path(raw).resolve().parent
        except Exception:
            continue
        upload_dir_text = str(upload_dir)
        if upload_dir_text in cleaned_dirs:
            continue
        try:
            shutil.rmtree(upload_dir, ignore_errors=True)
            cleaned_dirs.add(upload_dir_text)
        except Exception:
            continue
    return len(cleaned_dirs)


@celery_app.task(name="fkv.ingestion.cleanup_uploaded_files")
def cleanup_uploaded_files_task(job_id: str, paths: list[str]) -> dict[str, Any]:
    cleaned_dirs = _cleanup_upload_dirs(paths)
    logger.info(
        "ingestion_uploaded_files_cleaned",
        extra=sanitize_log_context(
            {
                "job_id": str(job_id or ""),
                "cleaned_dirs": int(cleaned_dirs),
            }
        ),
    )
    return {"ok": True, "job_id": str(job_id or ""), "cleaned_dirs": int(cleaned_dirs)}


def enqueue_cleanup_uploaded_files(job_id: str, paths: list[str]) -> None:
    safe_paths = [str(item or "").strip() for item in (paths or []) if str(item or "").strip()]
    if not safe_paths:
        return
    if not any(_is_under_upload_tmp_root(path) for path in safe_paths):
        return
    try:
        cleanup_uploaded_files_task.delay(job_id, safe_paths)
    except Exception as exc:
        logger.warning(
            "ingestion_cleanup_task_dispatch_failed",
            extra=sanitize_log_context(
                {
                    "job_id": str(job_id or ""),
                    "error_code": compact_error_code(f"cleanup_dispatch_failed:{type(exc).__name__}"),
                }
            ),
        )
        _cleanup_upload_dirs(safe_paths)


def _status_after_run(success_count: int, failed_count: int, duplicate_count: int) -> str:
    if success_count <= 0 and duplicate_count <= 0 and failed_count > 0:
        return IngestionJobStatus.FAILED.value
    return IngestionJobStatus.COMPLETED.value


def _mark_doc_failed(doc: Document, error_code: str, *, detail: str | None = None) -> None:
    doc.status = DocumentStatus.FAILED.value
    safe_code = compact_error_code(error_code)
    doc.error_code = safe_code[:120]
    logger.warning(
        "ingestion_document_failed",
        extra=sanitize_log_context(
            {
                "doc_id": doc.id,
                "file_name": doc.file_name,
                "status": doc.status,
                "error_code": safe_code,
                "detail": str(detail or "")[:240],
            }
        ),
    )


def _append_doc_error_code(doc: Document, error_code: str) -> None:
    code = compact_error_code(error_code)
    prev = str(doc.error_code or "").strip()
    if not prev:
        doc.error_code = code
        return
    parts = [item.strip() for item in prev.split(",") if item.strip()]
    if code in parts:
        return
    parts.append(code)
    doc.error_code = ",".join(parts)[:120]


def _metadata_fallback_text(
    *,
    file_name: str,
    source_type: str,
    source_path: str,
    mail_subject: str = "",
    mail_from: str = "",
) -> str:
    parts = [
        "Metadata-only record",
        f"file_name: {file_name}",
        f"source_type: {source_type}",
        f"source_path: {source_path}",
    ]
    if mail_subject:
        parts.append(f"mail_subject: {mail_subject}")
    if mail_from:
        parts.append(f"mail_from: {mail_from}")
    return "\n".join(parts).strip()


def _photo_max_bytes() -> int:
    return max(0, int(settings.photo_max_size_mb or 0)) * 1024 * 1024


def _is_photo_ext(ext: str) -> bool:
    photo_exts = {str(x or "").strip().lower().lstrip(".") for x in settings.photo_file_extensions}
    return str(ext or "").strip().lower().lstrip(".") in photo_exts


def _is_photo_too_large(*, file_ext: str, file_size: int) -> bool:
    cap = _photo_max_bytes()
    if cap <= 0:
        return False
    if not _is_photo_ext(file_ext):
        return False
    return int(file_size or 0) > cap


def _document_exists_by_sha(db, sha256: str) -> Document | None:
    return db.scalar(
        select(Document).where(Document.sha256 == sha256, Document.status == DocumentStatus.COMPLETED.value).limit(1)
    )


def _document_exists_by_phash(db, phash: str, *, threshold: int) -> Document | None:
    safe_hash = str(phash or "").strip().lower()
    if not safe_hash:
        return None
    limit = max(0, int(threshold))
    rows = (
        db.execute(
            select(Document).where(
                Document.status == DocumentStatus.COMPLETED.value,
                Document.phash.is_not(None),
            )
        )
        .scalars()
        .all()
    )

    best_doc: Document | None = None
    best_distance: int | None = None
    for row in rows:
        if not is_image_path(row.source_path):
            continue
        dist = hamming_distance(safe_hash, str(row.phash or ""))
        if dist > limit:
            continue
        if best_doc is None or best_distance is None or dist < best_distance:
            best_doc = row
            best_distance = dist
    return best_doc


def _collect_payload_records(
    document: Document,
    chunks: list[Chunk],
    *,
    source_type: str,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in chunks:
        out.append(
            qdrant_payload(
                doc_id=document.id,
                chunk_id=c.id,
                doc_lang=document.doc_lang,
                category_path=document.category_path,
                source_type=source_type,
                updated_at=document.updated_at,
                title_en=document.title_en,
                title_zh=document.title_zh,
                tags=tags or [],
                text=c.content,
            )
        )
    return out


def _mail_context_for_attachment(db, path: str) -> tuple[str, str]:
    row = db.execute(
        select(MailIngestionEvent)
        .where(MailIngestionEvent.attachment_path == str(path or ""))
        .order_by(MailIngestionEvent.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return ("", "")
    return (str(row.subject or ""), str(row.from_addr or ""))


def _process_single_path(
    *,
    db,
    path: str,
    force_reprocess: bool,
    reprocess_doc_id: str | None,
) -> tuple[int, int, int]:
    success_count = 0
    failed_count = 0
    duplicate_count = 0

    if not os.path.exists(path):
        failed_count += 1
        return (success_count, failed_count, duplicate_count)

    file_name, file_ext, file_size = file_meta(path)
    if _is_photo_too_large(file_ext=file_ext, file_size=file_size):
        sha256 = compute_sha256(path)
        if reprocess_doc_id:
            document = db.get(Document, reprocess_doc_id)
            if document is None:
                failed_count += 1
                return (success_count, failed_count, duplicate_count)
            document.sha256 = sha256
            document.phash = None
            document.source_available_cached = True
            document.source_checked_at = dt.datetime.now(dt.UTC)
        else:
            document = Document(
                source_path=path,
                file_name=file_name,
                file_ext=file_ext,
                file_size=file_size,
                sha256=sha256,
                phash=None,
                status=DocumentStatus.PENDING.value,
                source_available_cached=True,
                source_checked_at=dt.datetime.now(dt.UTC),
            )
        db.add(document)
        db.flush()
        _mark_doc_failed(document, "photo_too_large", detail=f"size_bytes={file_size}")
        failed_count += 1
        return (success_count, failed_count, duplicate_count)

    image_phash = ""
    if bool(settings.ingestion_phash_dedup_enabled) and is_image_path(path):
        image_phash = compute_image_phash(path)

    sha256 = compute_sha256(path)

    if reprocess_doc_id:
        document = db.get(Document, reprocess_doc_id)
        if document is None:
            return (0, 1, 0)
        document.phash = image_phash or None
        document.source_available_cached = True
        document.source_checked_at = dt.datetime.now(dt.UTC)
    else:
        existing = _document_exists_by_sha(db, sha256)
        if existing and not force_reprocess:
            duplicate_doc = Document(
                source_path=path,
                file_name=file_name,
                file_ext=file_ext,
                file_size=file_size,
                sha256=sha256,
                phash=image_phash or existing.phash,
                status=DocumentStatus.DUPLICATE.value,
                duplicate_of=existing.id,
                doc_lang=existing.doc_lang,
                title_en=existing.title_en,
                title_zh=existing.title_zh,
                summary_en=existing.summary_en,
                summary_zh=existing.summary_zh,
                category_label_en=existing.category_label_en,
                category_label_zh=existing.category_label_zh,
                category_path=existing.category_path,
                source_available_cached=True,
                source_checked_at=dt.datetime.now(dt.UTC),
            )
            db.add(duplicate_doc)
            db.flush()
            duplicate_count += 1
            return (success_count, failed_count, duplicate_count)

        if bool(settings.ingestion_phash_dedup_enabled) and image_phash and (not force_reprocess):
            similar = _document_exists_by_phash(
                db,
                image_phash,
                threshold=int(settings.ingestion_phash_hamming_threshold),
            )
            if similar is not None:
                duplicate_doc = Document(
                    source_path=path,
                    file_name=file_name,
                    file_ext=file_ext,
                    file_size=file_size,
                    sha256=sha256,
                    phash=image_phash,
                    status=DocumentStatus.DUPLICATE.value,
                    duplicate_of=similar.id,
                    doc_lang=similar.doc_lang,
                    title_en=similar.title_en,
                    title_zh=similar.title_zh,
                    summary_en=similar.summary_en,
                    summary_zh=similar.summary_zh,
                    category_label_en=similar.category_label_en,
                    category_label_zh=similar.category_label_zh,
                    category_path=similar.category_path,
                    error_code="phash_similar",
                    source_available_cached=True,
                    source_checked_at=dt.datetime.now(dt.UTC),
                )
                db.add(duplicate_doc)
                db.flush()
                duplicate_count += 1
                return (success_count, failed_count, duplicate_count)

        document = Document(
            source_path=path,
            file_name=file_name,
            file_ext=file_ext,
            file_size=file_size,
            sha256=sha256,
            phash=image_phash or None,
            status=DocumentStatus.PENDING.value,
            source_available_cached=True,
            source_checked_at=dt.datetime.now(dt.UTC),
        )

    db.add(document)
    db.flush()
    old_chunk_ids: list[str] = []

    try:
        document.status = DocumentStatus.PROCESSING.value
        document.error_code = None

        source_type = infer_source_type(path)
        mail_subject = ""
        mail_from = ""
        if source_type == "mail":
            mail_subject, mail_from = _mail_context_for_attachment(db, path)

        text = extract_text_from_path(path, db=db)
        if not str(text or "").strip():
            if bool(settings.ingestion_metadata_fallback_enabled):
                text = _metadata_fallback_text(
                    file_name=document.file_name,
                    source_type=source_type,
                    source_path=document.source_path,
                    mail_subject=mail_subject,
                    mail_from=mail_from,
                )
                document.error_code = "parse_empty_fallback"
            else:
                _mark_doc_failed(document, "parse_empty")
                failed_count += 1
                return (success_count, failed_count, duplicate_count)

        if reprocess_doc_id:
            old_chunk_ids = [
                str(item)
                for item in db.execute(select(Chunk.id).where(Chunk.document_id == document.id)).scalars().all()
            ]
            db.execute(delete(Chunk).where(Chunk.document_id == document.id))

        chunks = chunk_text(
            text,
            target_tokens=settings.ingestion_chunk_target_tokens,
            overlap_tokens=settings.ingestion_chunk_overlap_tokens,
        )
        if not chunks:
            _mark_doc_failed(document, "chunk_empty")
            failed_count += 1
            return (success_count, failed_count, duplicate_count)

        # Category is model-assigned after summary generation; start with stable default.
        category_path = DEFAULT_CATEGORY_PATH
        category_label_en, category_label_zh = category_labels_for_path(category_path)

        title_en, title_zh = build_bilingual_title(document.file_name)
        document.doc_lang = detect_lang_simple(text)
        friendly_en, friendly_zh = generate_friendly_names(
            file_name=document.file_name,
            text=text,
            category_path=category_path,
            source_type=source_type,
            mail_subject=mail_subject,
        )
        fallback_title_en = friendly_en or title_en
        fallback_title_zh = friendly_zh or title_zh
        document.title_en = fallback_title_en
        document.title_zh = fallback_title_zh
        summary_en, summary_zh = build_document_summaries(
            text=text,
            doc_lang=document.doc_lang,
            category_label_en=category_label_en,
            category_label_zh=category_label_zh,
            title_en=document.title_en,
            title_zh=document.title_zh,
            db=db,
        )
        summary_flags = detect_summary_quality_flags(summary_en, summary_zh)
        document.summary_quality_state = "needs_regen" if is_low_quality_summary(summary_en, summary_zh) else "ok"
        document.summary_last_error = ",".join(summary_flags)[:240] if document.summary_quality_state != "ok" else ""
        document.summary_model = str(get_model_setting("summary_model", db) or "")[:64]
        document.summary_version = "prompt-v2"

        classified = classify_category_from_summary(
            file_name=document.file_name,
            source_type=source_type,
            summary_en=summary_en,
            summary_zh=summary_zh,
            content_excerpt=text[:1200],
            db=db,
        )
        if classified is not None:
            category_label_en, category_label_zh, category_path = classified
            document.category_version = "taxonomy-v1"
        safe_category_path, blocked_legacy_path = apply_legacy_category_guard(category_path)
        if blocked_legacy_path:
            logger.warning(
                "legacy_category_blocked",
                extra=sanitize_log_context(
                    {
                        "event": "legacy_category_blocked",
                        "doc_id": document.id,
                        "original_path": category_path,
                        "rewritten_path": safe_category_path,
                        "pipeline_stage": "ingestion_classify",
                    }
                ),
            )
        category_path = safe_category_path
        category_label_en, category_label_zh = category_labels_for_path(category_path)
        summary_en, summary_zh = normalize_vehicle_insurance_summary(
            category_path=category_path,
            file_name=document.file_name,
            summary_en=summary_en,
            summary_zh=summary_zh,
            content_excerpt=text[:2400],
        )
        rename = regenerate_friendly_name_from_summary(
            file_name=document.file_name,
            category_path=category_path,
            summary_en=summary_en,
            summary_zh=summary_zh,
            fallback_en=fallback_title_en,
            fallback_zh=fallback_title_zh,
            content_excerpt=text[:2400],
            db=db,
        )
        if rename is not None:
            renamed_en, renamed_zh = rename
            document.title_en = str(renamed_en or fallback_title_en)[:512]
            document.title_zh = str(renamed_zh or fallback_title_zh)[:512]
            document.name_version = "name-v2"
        document.summary_en = summary_en
        document.summary_zh = summary_zh
        document.category_label_en = category_label_en
        document.category_label_zh = category_label_zh
        document.category_path = category_path

        # Record OCR page count for PDFs so UI can warn when content was truncated
        if file_ext.lower() == "pdf":
            pdf_page_count = get_pdf_page_count(path)
            if pdf_page_count is not None:
                ocr_limit = int(settings.ingestion_ocr_pdf_max_pages)
                document.ocr_pages_total = pdf_page_count
                document.ocr_pages_processed = min(pdf_page_count, ocr_limit)

        upsert_bill_fact_for_document(db, document, content_excerpt=text[:2400])

        auto_tags = infer_auto_tags(
            file_name=document.file_name,
            source_path=document.source_path,
            source_type=source_type,
            summary_en=document.summary_en,
            summary_zh=document.summary_zh,
            content_excerpt=text[:1200],
            category_path=document.category_path,
            mail_from=mail_from,
            mail_subject=mail_subject,
        )
        crud.sync_auto_tags_for_document(db, document_id=document.id, auto_tag_keys=auto_tags)
        document_tags = crud.get_document_tag_keys(db, document.id)

        chunk_rows: list[Chunk] = []
        for idx, content in enumerate(chunks):
            row = Chunk(
                document_id=document.id,
                chunk_index=idx,
                content=content,
                token_count=len(content.split()),
                embedding_status="pending",
            )
            db.add(row)
            chunk_rows.append(row)

        document.status = DocumentStatus.COMPLETED.value
        db.flush()

        payload_records = _collect_payload_records(document, chunk_rows, source_type=source_type, tags=document_tags)
        try:
            upsert_records(payload_records, db=db)
            for c in chunk_rows:
                c.embedding_status = "ready" if settings.qdrant_enable else "pending"
            if old_chunk_ids:
                old_point_ids = [stable_point_id(document.id, chunk_id) for chunk_id in old_chunk_ids if chunk_id]
                try:
                    delete_records_by_point_ids(old_point_ids, wait=True)
                except Exception as exc:
                    _append_doc_error_code(document, "qdrant_cleanup_pending")
                    logger.warning(
                        "qdrant_cleanup_error",
                        extra=sanitize_log_context(
                            {
                                "doc_id": document.id,
                                "error_code": "qdrant_cleanup_pending",
                                "old_chunk_count": len(old_chunk_ids),
                                "detail": str(exc),
                            }
                        ),
                    )
        except Exception as exc:
            logger.warning(
                "qdrant_upsert_error",
                extra=sanitize_log_context(
                    {
                        "doc_id": document.id,
                        "error_code": "qdrant_upsert_error",
                        "detail": str(exc),
                    }
                ),
            )

        success_count += 1
        return (success_count, failed_count, duplicate_count)
    except ValueError as exc:
        _mark_doc_failed(document, str(exc))
        failed_count += 1
        return (success_count, failed_count, duplicate_count)
    except Exception as exc:
        _mark_doc_failed(document, "ingestion_failed", detail=type(exc).__name__)
        failed_count += 1
        return (success_count, failed_count, duplicate_count)


def process_ingestion_job(
    job_id: str, force_reprocess: bool = False, reprocess_doc_id: str | None = None
) -> dict[str, Any]:
    db = SessionLocal()
    paths: list[str] = []
    try:
        job = db.get(IngestionJob, job_id)
        if job is None:
            return {"ok": False, "error": "job_not_found", "job_id": job_id}
        try:
            paths = json.loads(job.input_paths or "[]")
        except Exception:
            paths = []

        job.status = IngestionJobStatus.RUNNING.value
        job.error_code = None
        job.started_at = dt.datetime.now(dt.UTC)
        db.commit()

        success_count = 0
        failed_count = 0
        duplicate_count = 0

        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path:
                failed_count += 1
                continue

            # Directory path is expanded into supported document files for recursive ingestion.
            if os.path.isdir(path):
                expanded, _stats = discover_files(
                    [path],
                    allowed_extensions=settings.ingestion_allowed_extensions,
                    exclude_dirs=settings.ingestion_scan_exclude_dirs,
                    photo_extensions=settings.photo_file_extensions,
                    photo_max_bytes=_photo_max_bytes(),
                    recursive=True,
                    max_files=settings.ingestion_scan_max_files_per_run,
                )
                if not expanded:
                    failed_count += 1
                    continue
                for sub_path in expanded:
                    s, f, d = _process_single_path(
                        db=db,
                        path=sub_path,
                        force_reprocess=force_reprocess,
                        reprocess_doc_id=reprocess_doc_id,
                    )
                    success_count += s
                    failed_count += f
                    duplicate_count += d
                    db.commit()
                continue

            s, f, d = _process_single_path(
                db=db,
                path=path,
                force_reprocess=force_reprocess,
                reprocess_doc_id=reprocess_doc_id,
            )
            success_count += s
            failed_count += f
            duplicate_count += d
            db.commit()

        job.success_count = success_count
        job.failed_count = failed_count
        job.duplicate_count = duplicate_count
        job.status = _status_after_run(success_count, failed_count, duplicate_count)
        if job.status == IngestionJobStatus.FAILED.value:
            job.error_code = "job_all_failed"
        elif failed_count > 0:
            job.error_code = "partial_failed"
        else:
            job.error_code = None
        job.finished_at = dt.datetime.now(dt.UTC)
        db.commit()

        logger.info(
            "ingestion_job_finished",
            extra=sanitize_log_context(
                {
                    "job_id": job.id,
                    "status": job.status,
                    "success": success_count,
                    "failed": failed_count,
                    "duplicate": duplicate_count,
                }
            ),
        )
        return {
            "ok": True,
            "job_id": job.id,
            "status": job.status,
            "success_count": success_count,
            "failed_count": failed_count,
            "duplicate_count": duplicate_count,
            "error_code": job.error_code,
        }
    finally:
        db.close()
        enqueue_cleanup_uploaded_files(job_id, paths)
