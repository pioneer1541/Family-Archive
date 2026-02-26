import datetime as dt
import hashlib
import json
import os
import re
import time
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.services.tag_rules import normalize_tag_list, split_tag_key, tag_label, validate_tag_limits


def _real(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.realpath(raw)
    except Exception:
        return ""


def source_path_available(path: str | None) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return False
    return os.path.isfile(raw)


def document_source_available_cached(doc: models.Document | None) -> bool:
    if doc is None:
        return False
    if getattr(doc, "source_checked_at", None) is None:
        return source_path_available(getattr(doc, "source_path", ""))
    return bool(getattr(doc, "source_available_cached", True))


def set_document_source_available_cached(db: Session, doc: models.Document, *, available: bool) -> None:
    doc.source_available_cached = bool(available)
    doc.source_checked_at = dt.datetime.now(dt.UTC)
    db.flush()


def refresh_document_source_available_cached(db: Session, doc: models.Document) -> bool:
    available = source_path_available(getattr(doc, "source_path", ""))
    set_document_source_available_cached(db, doc, available=available)
    return available


def _reconcile_unchecked_document_sources(db: Session) -> None:
    # Legacy rows or test inserts may leave source cache unset; refresh before cache-based filtering.
    rows = db.execute(select(models.Document).where(models.Document.source_checked_at.is_(None))).scalars().all()
    for doc in rows:
        refresh_document_source_available_cached(db, doc)


def _slug_token(value: str, *, max_len: int = 22) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\.[a-z0-9]{1,8}$", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if text:
        return text[:max_len]

    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits:
        return f"n{digits}"[:max_len]

    digest = hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:8]
    return f"ref-{digest}"[:max_len]


def _task_anchor_from_docset(db: Session, doc_set: list[str]) -> str:
    for raw_doc_id in doc_set:
        doc_id = str(raw_doc_id or "").strip()
        if not doc_id:
            continue
        doc = db.get(models.Document, doc_id)
        if doc is None:
            continue
        for item in [doc.title_en, doc.title_zh, doc.file_name]:
            text = str(item or "").strip()
            if text:
                return text
    return ""


def _build_task_id(db: Session, *, title: str, task_type: str, doc_set: list[str]) -> str:
    anchor = _task_anchor_from_docset(db, doc_set) or str(title or "").strip() or "task"
    prefix = _slug_token(anchor, max_len=22)
    seed = f"{anchor}|{task_type}|{time.time_ns()}"

    # Keep task IDs <= 36 chars to match schema while preserving readable anchor linkage.
    for idx in range(10):
        source = f"{seed}|{idx}" if idx > 0 else seed
        suffix = hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
        task_id = f"task-{prefix}-{suffix}"[:36]
        if db.get(models.Task, task_id) is None:
            return task_id
    return f"task-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:31]}"[:36]


def create_ingestion_job(db: Session, file_paths: list[str]) -> models.IngestionJob:
    job = models.IngestionJob(input_paths=json.dumps(file_paths, ensure_ascii=False), status=models.IngestionJobStatus.CREATED.value)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_ingestion_job(db: Session, job_id: str) -> models.IngestionJob | None:
    return db.get(models.IngestionJob, job_id)


def delete_ingestion_job(db: Session, job: models.IngestionJob) -> None:
    db.delete(job)
    db.commit()


def upsert_ignored_paths(db: Session, paths: list[str], *, reason: str = "queue_deleted") -> int:
    now = dt.datetime.now(dt.UTC)
    safe_reason = str(reason or "queue_deleted").strip()[:120] or "queue_deleted"
    created = 0
    seen: set[str] = set()
    for raw in paths:
        rp = _real(raw)
        if (not rp) or (rp in seen):
            continue
        seen.add(rp)
        row = db.get(models.IgnoredIngestionPath, rp)
        if row is None:
            db.add(models.IgnoredIngestionPath(path=rp, reason=safe_reason, created_at=now))
            created += 1
        else:
            row.reason = safe_reason
    return created


def filter_ignored_paths(db: Session, paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        rp = _real(raw)
        if (not rp) or (rp in seen):
            continue
        seen.add(rp)
        if db.get(models.IgnoredIngestionPath, rp) is not None:
            continue
        out.append(rp)
    return out


def create_task(db: Session, payload: dict[str, Any]) -> models.Task:
    title = str(payload.get("title") or "").strip()
    task_type = str(payload.get("task_type") or "").strip()
    doc_set = list(payload.get("doc_set") or [])
    task_id = _build_task_id(db, title=title, task_type=task_type, doc_set=doc_set)

    task = models.Task(
        id=task_id,
        title=title,
        task_type=task_type,
        doc_set=json.dumps(doc_set, ensure_ascii=False),
        filters=json.dumps(payload.get("filters") or {}, ensure_ascii=False),
        summary_en="",
        summary_zh="",
        status=models.TaskStatus.CREATED.value,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: str) -> models.Task | None:
    return db.get(models.Task, task_id)


def list_tasks(db: Session, *, limit: int = 50, offset: int = 0) -> tuple[list[models.Task], int]:
    safe_limit = max(1, min(200, int(limit)))
    safe_offset = max(0, int(offset))
    rows = (
        db.execute(select(models.Task).order_by(models.Task.updated_time.desc()).offset(safe_offset).limit(safe_limit))
        .scalars()
        .all()
    )
    total = int(db.scalar(select(func.count()).select_from(models.Task)) or 0)
    return (rows, total)


def get_document(db: Session, doc_id: str) -> models.Document | None:
    doc = db.get(models.Document, doc_id)
    if doc is not None and getattr(doc, "source_checked_at", None) is None:
        refresh_document_source_available_cached(db, doc)
    return doc


def list_documents(
    db: Session,
    *,
    status: str | None = None,
    category_path: str | None = None,
    tags_all: list[str] | None = None,
    tags_any: list[str] | None = None,
    include_missing: bool = False,
    source_state: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[models.Document], int]:
    _reconcile_unchecked_document_sources(db)
    stmt = select(models.Document)
    count_stmt = select(func.count()).select_from(models.Document)

    if status:
        stmt = stmt.where(models.Document.status == status)
        count_stmt = count_stmt.where(models.Document.status == status)
    if category_path:
        stmt = stmt.where(models.Document.category_path == category_path)
        count_stmt = count_stmt.where(models.Document.category_path == category_path)

    normalized_source_state = str(source_state or "").strip().lower()
    if normalized_source_state not in {"available", "missing", "all"}:
        normalized_source_state = "all" if include_missing else "available"
    if normalized_source_state == "available":
        stmt = stmt.where(models.Document.source_available_cached.is_(True))
        count_stmt = count_stmt.where(models.Document.source_available_cached.is_(True))
    elif normalized_source_state == "missing":
        stmt = stmt.where(models.Document.source_available_cached.is_(False))
        count_stmt = count_stmt.where(models.Document.source_available_cached.is_(False))

    tags_all_norm, _ = normalize_tag_list(tags_all or [])
    tags_any_norm, _ = normalize_tag_list(tags_any or [])

    for tag in tags_all_norm:
        doc_ids_for_tag = select(models.DocumentTag.document_id).where(models.DocumentTag.tag_key == tag)
        stmt = stmt.where(models.Document.id.in_(doc_ids_for_tag))
        count_stmt = count_stmt.where(models.Document.id.in_(doc_ids_for_tag))

    if tags_any_norm:
        doc_ids_any = select(models.DocumentTag.document_id).where(models.DocumentTag.tag_key.in_(tags_any_norm))
        stmt = stmt.where(models.Document.id.in_(doc_ids_any))
        count_stmt = count_stmt.where(models.Document.id.in_(doc_ids_any))

    q_text = str(q or "").strip()
    if q_text:
        pattern = f"%{q_text}%"
        q_clause = or_(
            models.Document.file_name.ilike(pattern),
            models.Document.title_en.ilike(pattern),
            models.Document.title_zh.ilike(pattern),
            models.Document.summary_en.ilike(pattern),
            models.Document.summary_zh.ilike(pattern),
            models.Document.category_path.ilike(pattern),
            models.Document.id.in_(select(models.DocumentTag.document_id).where(models.DocumentTag.tag_key.ilike(pattern))),
        )
        stmt = stmt.where(q_clause)
        count_stmt = count_stmt.where(q_clause)

    safe_limit = max(1, min(200, int(limit)))
    safe_offset = max(0, int(offset))
    rows = (
        db.execute(stmt.order_by(models.Document.updated_at.desc()).offset(safe_offset).limit(safe_limit))
        .scalars()
        .all()
    )
    total = int(db.scalar(count_stmt) or 0)
    return (rows, total)


def list_categories(db: Session, *, limit: int = 100, include_missing: bool = False) -> list[dict[str, Any]]:
    _reconcile_unchecked_document_sources(db)
    safe_limit = max(1, min(500, int(limit)))
    stmt = (
        select(
            models.Document.category_path,
            models.Document.category_label_en,
            models.Document.category_label_zh,
            func.count().label("doc_count"),
        )
        .where(models.Document.status == models.DocumentStatus.COMPLETED.value)
        .group_by(
            models.Document.category_path,
            models.Document.category_label_en,
            models.Document.category_label_zh,
        )
    )
    if not include_missing:
        stmt = stmt.where(models.Document.source_available_cached.is_(True))
    rows = db.execute(stmt.order_by(func.count().desc(), models.Document.category_path.asc()).limit(safe_limit)).all()
    out: list[dict[str, Any]] = []
    for category_path, label_en, label_zh, doc_count in rows:
        out.append(
            {
                "category_path": str(category_path or "archive/misc"),
                "label_en": str(label_en or "Archive Misc"),
                "label_zh": str(label_zh or "归档杂项"),
                "doc_count": int(doc_count or 0),
            }
        )
    return out


def get_queue_totals(db: Session) -> dict[str, int]:
    doc_total = db.scalar(select(func.count()).select_from(models.Document)) or 0
    pending_docs = (
        db.scalar(select(func.count()).select_from(models.Document).where(models.Document.status.in_(["pending", "processing", "failed"])))
        or 0
    )
    jobs_total = db.scalar(select(func.count()).select_from(models.IngestionJob)) or 0
    running_jobs = db.scalar(select(func.count()).select_from(models.IngestionJob).where(models.IngestionJob.status == "running")) or 0
    return {
        "documents": int(doc_total),
        "pending_documents": int(pending_docs),
        "jobs": int(jobs_total),
        "running_jobs": int(running_jobs),
    }


def get_document_tag_rows(db: Session, doc_id: str) -> list[models.DocumentTag]:
    return (
        db.execute(
            select(models.DocumentTag)
            .where(models.DocumentTag.document_id == str(doc_id or ""))
            .order_by(models.DocumentTag.family.asc(), models.DocumentTag.tag_key.asc())
        )
        .scalars()
        .all()
    )


def get_document_tag_keys(db: Session, doc_id: str) -> list[str]:
    return [row.tag_key for row in get_document_tag_rows(db, doc_id)]


def get_document_tags_map(db: Session, doc_ids: list[str]) -> dict[str, list[str]]:
    keys = [str(x or "").strip() for x in doc_ids if str(x or "").strip()]
    if not keys:
        return {}
    rows = (
        db.execute(
            select(models.DocumentTag.document_id, models.DocumentTag.tag_key)
            .where(models.DocumentTag.document_id.in_(set(keys)))
            .order_by(models.DocumentTag.document_id.asc(), models.DocumentTag.family.asc(), models.DocumentTag.tag_key.asc())
        )
        .all()
    )
    out: dict[str, list[str]] = {doc_id: [] for doc_id in keys}
    for doc_id, tag_key in rows:
        sid = str(doc_id or "").strip()
        key = str(tag_key or "").strip()
        if (not sid) or (not key):
            continue
        out.setdefault(sid, []).append(key)
    return out


def serialize_document_tags(rows: list[models.DocumentTag]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        out.append(
            {
                "key": str(row.tag_key or ""),
                "family": str(row.family or ""),
                "value": str(row.value or ""),
                "origin": str(row.origin or "auto"),
                "label_en": tag_label(str(row.tag_key or ""), ui_lang="en"),
                "label_zh": tag_label(str(row.tag_key or ""), ui_lang="zh"),
            }
        )
    return out


def _new_tag_row(document_id: str, tag_key: str, *, origin: str) -> models.DocumentTag | None:
    family, value = split_tag_key(tag_key)
    if (not family) or (not value):
        return None
    return models.DocumentTag(
        document_id=document_id,
        tag_key=tag_key,
        family=family,
        value=value,
        origin=str(origin or "auto")[:16],
    )


def sync_auto_tags_for_document(db: Session, *, document_id: str, auto_tag_keys: list[str]) -> list[models.DocumentTag]:
    normalized, _ = normalize_tag_list(auto_tag_keys or [])
    rows = get_document_tag_rows(db, document_id)
    by_key = {str(row.tag_key): row for row in rows}
    manual_keys = {str(row.tag_key) for row in rows if str(row.origin or "").lower() == "manual"}
    target_keys = [key for key in normalized if key not in manual_keys]

    for row in rows:
        key = str(row.tag_key or "")
        origin = str(row.origin or "").lower()
        if (origin != "manual") and (key not in target_keys):
            db.delete(row)

    for key in target_keys:
        row = by_key.get(key)
        if row is None:
            new_row = _new_tag_row(document_id, key, origin="auto")
            if new_row is not None:
                db.add(new_row)
            continue
        if str(row.origin or "").lower() != "manual":
            family, value = split_tag_key(key)
            row.family = family
            row.value = value
            row.origin = "auto"

    db.flush()
    return get_document_tag_rows(db, document_id)


def patch_document_tags(
    db: Session,
    *,
    document_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> tuple[list[models.DocumentTag], list[str]]:
    add_norm, add_invalid = normalize_tag_list(add or [], strict=True)
    remove_norm, remove_invalid = normalize_tag_list(remove or [], strict=True)
    invalid = add_invalid + remove_invalid
    if invalid:
        return ([], invalid)

    rows = get_document_tag_rows(db, document_id)
    by_key = {str(row.tag_key): row for row in rows}
    current = [str(row.tag_key) for row in rows]
    current_set = set(current)

    next_keys: list[str] = []
    seen: set[str] = set()
    remove_set = set(remove_norm)
    for key in current:
        if key in remove_set:
            continue
        if key in seen:
            continue
        seen.add(key)
        next_keys.append(key)
    for key in add_norm:
        if key in seen:
            continue
        seen.add(key)
        next_keys.append(key)

    ok, reason = validate_tag_limits(next_keys)
    if not ok:
        return ([], [reason])

    for key in remove_set:
        row = by_key.get(key)
        if row is not None:
            db.delete(row)

    for key in add_norm:
        row = by_key.get(key)
        if row is None:
            new_row = _new_tag_row(document_id, key, origin="manual")
            if new_row is not None:
                db.add(new_row)
            continue
        row.origin = "manual"

    if add_norm or remove_norm or (current_set != set(next_keys)):
        doc = db.get(models.Document, document_id)
        if doc is not None:
            doc.updated_at = dt.datetime.now(dt.UTC)
    db.flush()
    return (get_document_tag_rows(db, document_id), [])


def list_tag_catalog(db: Session, *, family: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    safe_limit = max(1, min(500, int(limit)))
    stmt = (
        select(
            models.DocumentTag.tag_key,
            models.DocumentTag.family,
            models.DocumentTag.value,
            func.count().label("doc_count"),
        )
        .group_by(models.DocumentTag.tag_key, models.DocumentTag.family, models.DocumentTag.value)
        .order_by(func.count().desc(), models.DocumentTag.tag_key.asc())
        .limit(safe_limit)
    )
    if family:
        stmt = stmt.where(models.DocumentTag.family == str(family or "").strip().lower())

    rows = db.execute(stmt).all()
    out: list[dict[str, Any]] = []
    for key, fam, value, count in rows:
        k = str(key or "")
        out.append(
            {
                "key": k,
                "family": str(fam or ""),
                "value": str(value or ""),
                "label_en": tag_label(k, ui_lang="en"),
                "label_zh": tag_label(k, ui_lang="zh"),
                "doc_count": int(count or 0),
            }
        )
    return out


def select_document_ids_for_filters(
    db: Session,
    *,
    status: str | None = None,
    category_path: str | None = None,
    tags_all: list[str] | None = None,
    tags_any: list[str] | None = None,
    include_missing: bool = True,
) -> set[str]:
    _reconcile_unchecked_document_sources(db)
    stmt = select(models.Document.id)
    if status:
        stmt = stmt.where(models.Document.status == status)
    if category_path:
        stmt = stmt.where(models.Document.category_path == category_path)

    tags_all_norm, _ = normalize_tag_list(tags_all or [])
    tags_any_norm, _ = normalize_tag_list(tags_any or [])
    for tag in tags_all_norm:
        sub = select(models.DocumentTag.document_id).where(models.DocumentTag.tag_key == tag)
        stmt = stmt.where(models.Document.id.in_(sub))

    if tags_any_norm:
        sub_any = select(models.DocumentTag.document_id).where(models.DocumentTag.tag_key.in_(tags_any_norm))
        stmt = stmt.where(models.Document.id.in_(sub_any))

    if not include_missing:
        stmt = stmt.where(models.Document.source_available_cached.is_(True))

    rows = db.execute(stmt).all()
    out: set[str] = set()
    for (doc_id,) in rows:
        sid = str(doc_id or "").strip()
        if not sid:
            continue
        out.add(sid)
    return out


def replace_document_tags_manual(db: Session, *, document_id: str, tags: list[str]) -> tuple[list[models.DocumentTag], list[str]]:
    normalized, invalid = normalize_tag_list(tags or [], strict=True)
    if invalid:
        return ([], invalid)
    ok, reason = validate_tag_limits(normalized)
    if not ok:
        return ([], [reason])

    db.execute(delete(models.DocumentTag).where(models.DocumentTag.document_id == document_id))
    for key in normalized:
        row = _new_tag_row(document_id, key, origin="manual")
        if row is not None:
            db.add(row)
    doc = db.get(models.Document, document_id)
    if doc is not None:
        doc.updated_at = dt.datetime.now(dt.UTC)
    db.flush()
    return (get_document_tag_rows(db, document_id), [])
