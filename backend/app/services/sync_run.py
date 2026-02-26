import datetime as dt
import json
import os
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, IngestionJob, IngestionJobStatus, MailIngestionEvent, SyncRun, SyncRunItem
from app.services.mail_ingest import poll_mailbox_and_enqueue
from app.services.nas import run_nas_scan

_TERMINAL_ITEM_STAGES = {"completed", "failed", "duplicate", "skipped"}
_JOB_ACTIVE = {IngestionJobStatus.RUNNING.value, IngestionJobStatus.RETRYING.value, IngestionJobStatus.CREATED.value}
_ACTIVE_ITEM_STAGES = {"discovered", "queued", "pending", "processing"}


def _safe_json(value: dict) -> str:
    try:
        return json.dumps(value or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def _load_json(value: str | None) -> dict:
    try:
        raw = json.loads(value or "{}")
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _stage_from_doc_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"pending", "processing", "completed", "failed", "duplicate"}:
        return normalized
    return "queued"


def _item_name(path: str, fallback: str = "") -> str:
    from_path = os.path.basename(str(path or "").strip())
    if from_path:
        return from_path
    return str(fallback or "").strip() or "unknown"


def _item_size(path: str) -> int:
    target = str(path or "").strip()
    if not target:
        return 0
    try:
        return int(os.path.getsize(target))
    except Exception:
        return 0


def _find_doc_by_source_path(db: Session, source_path: str) -> Document | None:
    path = str(source_path or "").strip()
    if not path:
        return None
    return (
        db.execute(select(Document).where(Document.source_path == path).order_by(Document.updated_at.desc()).limit(1))
        .scalars()
        .first()
    )


def _upsert_item(
    db: Session,
    *,
    run_id: str,
    source_type: str,
    source_path: str,
    file_name: str,
    file_size: int,
    stage: str,
    detail: str = "",
) -> SyncRunItem:
    existing = (
        db.execute(
            select(SyncRunItem)
            .where(SyncRunItem.run_id == run_id, SyncRunItem.source_type == source_type, SyncRunItem.source_path == source_path)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if existing is None:
        existing = SyncRunItem(
            run_id=run_id,
            source_type=str(source_type or "nas")[:16],
            source_path=str(source_path or ""),
            file_name=str(file_name or "")[:512],
            file_size=max(0, int(file_size or 0)),
            stage=str(stage or "discovered")[:24],
            detail=str(detail or "")[:240],
        )
        db.add(existing)
        return existing

    existing.file_name = str(file_name or existing.file_name or "")[:512]
    existing.file_size = max(0, int(file_size or existing.file_size or 0))
    existing.stage = str(stage or existing.stage or "queued")[:24]
    existing.detail = str(detail or existing.detail or "")[:240]
    return existing


def start_sync_run(
    db: Session,
    *,
    nas_paths: list[str] | None = None,
    recursive: bool = True,
    mail_max_results: int | None = None,
) -> SyncRun:
    run = create_sync_run(db)
    return execute_sync_run(db, run.id, nas_paths=nas_paths, recursive=recursive, mail_max_results=mail_max_results)


def create_sync_run(db: Session) -> SyncRun:
    now = dt.datetime.now(dt.UTC)
    run = SyncRun(status="running", started_at=now)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def execute_sync_run(
    db: Session,
    run_id: str,
    *,
    nas_paths: list[str] | None = None,
    recursive: bool = True,
    mail_max_results: int | None = None,
) -> SyncRun:
    run = db.get(SyncRun, str(run_id or "").strip())
    if run is None:
        raise ValueError("sync_run_not_found")
    run.status = "running"
    run.error_code = None
    run.finished_at = None

    nas_out = run_nas_scan(db, paths=nas_paths, recursive=recursive)
    mail_out = poll_mailbox_and_enqueue(db, max_results=mail_max_results, sync_run_id=run.id)

    run.nas_job_id = str(nas_out.get("job_id") or "")[:36]
    run.mail_job_id = str(mail_out.get("job_id") or "")[:36]
    run.nas_summary_json = _safe_json(nas_out)
    run.mail_summary_json = _safe_json(mail_out)

    nas_paths_to_track = [str(p or "").strip() for p in (nas_out.get("queued_paths") or []) if str(p or "").strip()]
    for source_path in nas_paths_to_track:
        _upsert_item(
            db,
            run_id=run.id,
            source_type="nas",
            source_path=source_path,
            file_name=_item_name(source_path),
            file_size=_item_size(source_path),
            stage="queued",
            detail="queued",
        )

    mail_events = (
        db.execute(
            select(MailIngestionEvent)
            .where(MailIngestionEvent.sync_run_id == run.id)
            .order_by(MailIngestionEvent.created_at.asc())
        )
        .scalars()
        .all()
    )
    for event in mail_events:
        attachment_path = str(event.attachment_path or "").strip()
        source_path = attachment_path or f"mail://{event.message_id}/{event.id}"
        status = str(event.status or "").strip().lower()
        if status == "downloaded":
            stage = "queued"
        elif status == "skipped":
            stage = "skipped"
        elif status == "failed":
            stage = "failed"
        else:
            stage = "discovered"
        _upsert_item(
            db,
            run_id=run.id,
            source_type="mail",
            source_path=source_path,
            file_name=_item_name(attachment_path, fallback=event.attachment_name),
            file_size=_item_size(attachment_path),
            stage=stage,
            detail=str(event.detail or "")[:240],
        )

    db.commit()
    db.refresh(run)
    return run


def _job_is_active(db: Session, job_id: str) -> bool:
    safe_id = str(job_id or "").strip()
    if not safe_id:
        return False
    job = db.get(IngestionJob, safe_id)
    if job is None:
        return False
    return str(job.status or "") in _JOB_ACTIVE


def refresh_sync_run_status(db: Session, run_id: str) -> SyncRun | None:
    run = db.get(SyncRun, str(run_id or "").strip())
    if run is None:
        return None

    items = (
        db.execute(select(SyncRunItem).where(SyncRunItem.run_id == run.id).order_by(SyncRunItem.updated_at.desc()))
        .scalars()
        .all()
    )

    source_paths = {
        str(item.source_path or "").strip()
        for item in items
        if str(item.source_path or "").strip() and (not str(item.source_path or "").strip().startswith("mail://"))
    }
    docs_by_path: dict[str, Document] = {}
    if source_paths:
        doc_rows = (
            db.execute(select(Document).where(Document.source_path.in_(source_paths)).order_by(Document.updated_at.desc()))
            .scalars()
            .all()
        )
        for doc in doc_rows:
            path = str(doc.source_path or "").strip()
            if path and path not in docs_by_path:
                docs_by_path[path] = doc

    for item in items:
        if str(item.doc_id or "").strip() and str(item.stage or "") in _TERMINAL_ITEM_STAGES:
            continue
        source_path = str(item.source_path or "").strip()
        if source_path.startswith("mail://"):
            continue
        doc = docs_by_path.get(source_path)
        if doc is None:
            continue
        item.doc_id = str(doc.id)
        item.stage = _stage_from_doc_status(str(doc.status or ""))
        if not item.file_name:
            item.file_name = str(doc.file_name or "")[:512]
        if int(item.file_size or 0) <= 0:
            item.file_size = int(doc.file_size or 0)

    counts = Counter(str(item.stage or "discovered") for item in items)
    active_jobs = _job_is_active(db, run.nas_job_id) or _job_is_active(db, run.mail_job_id)
    all_terminal = all(str(item.stage or "") in _TERMINAL_ITEM_STAGES for item in items) if items else True
    active_count = sum(int(counts.get(key, 0)) for key in _ACTIVE_ITEM_STAGES)
    is_active = bool(active_jobs) or (active_count > 0)

    if is_active:
        run.status = "running"
        run.finished_at = None
    elif not items:
        run.status = "completed"
        run.finished_at = run.finished_at or dt.datetime.now(dt.UTC)
    elif all_terminal:
        run.finished_at = run.finished_at or dt.datetime.now(dt.UTC)
        run.status = "failed" if int(counts.get("failed", 0)) > 0 and int(counts.get("completed", 0)) == 0 else "completed"
    else:
        run.status = "running"
        run.finished_at = None

    db.commit()
    db.refresh(run)
    return run


def get_sync_summary(db: Session, run: SyncRun) -> dict[str, int]:
    items = db.execute(select(SyncRunItem.stage).where(SyncRunItem.run_id == run.id)).all()
    counts = Counter(str(row[0] or "discovered") for row in items)
    total = len(items)
    active_count = sum(int(counts.get(key, 0)) for key in _ACTIVE_ITEM_STAGES)
    terminal_count = sum(int(counts.get(key, 0)) for key in _TERMINAL_ITEM_STAGES)
    active_jobs = _job_is_active(db, run.nas_job_id) or _job_is_active(db, run.mail_job_id)
    progress_pct = 100 if total <= 0 else max(0, min(100, int(round((terminal_count / total) * 100))))
    return {
        "total": total,
        "discovered": int(counts.get("discovered", 0)),
        "queued": int(counts.get("queued", 0)),
        "pending": int(counts.get("pending", 0)),
        "processing": int(counts.get("processing", 0)),
        "completed": int(counts.get("completed", 0)),
        "failed": int(counts.get("failed", 0)),
        "duplicate": int(counts.get("duplicate", 0)),
        "skipped": int(counts.get("skipped", 0)),
        "active_count": active_count,
        "terminal_count": terminal_count,
        "progress_pct": progress_pct,
        "is_active": bool(active_jobs) or (active_count > 0),
    }


def get_sync_last(db: Session) -> SyncRun | None:
    return db.execute(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1)).scalars().first()


def get_sync_source_summary(run: SyncRun) -> tuple[dict, dict]:
    return (_load_json(run.nas_summary_json), _load_json(run.mail_summary_json))
