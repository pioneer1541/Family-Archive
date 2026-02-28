import os

from sqlalchemy.orm import Session

from app import crud
from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.services.ingestion import enqueue_ingestion_job
from app.services.path_scan import collect_incremental_changes, discover_files, purge_source_states_outside_root

settings = get_settings()
logger = get_logger(__name__)


def _real(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.realpath(raw)
    except Exception:
        return ""


def _is_within(path: str, root: str) -> bool:
    p = _real(path)
    r = _real(root)
    if (not p) or (not r):
        return False
    try:
        return os.path.commonpath([p, r]) == r
    except Exception:
        return p == r or p.startswith(r.rstrip("/\\") + os.sep)


def _normalize_scan_paths(paths: list[str] | None) -> tuple[str, list[str]]:
    root = _real(settings.nas_default_source_dir)
    raw_paths = [str(p or "").strip() for p in (paths or []) if str(p or "").strip()]
    if (not root) or (not raw_paths):
        return (root, [root] if root else [])

    filtered: list[str] = []
    seen: set[str] = set()
    for p in raw_paths:
        rp = _real(p)
        if (not rp) or (rp in seen):
            continue
        if not _is_within(rp, root):
            continue
        seen.add(rp)
        filtered.append(rp)

    if not filtered:
        return (root, [root] if root else [])
    return (root, filtered)


def run_nas_scan(
    db: Session,
    *,
    paths: list[str] | None = None,
    recursive: bool = True,
    max_files: int | None = None,
) -> dict:
    root, scan_paths = _normalize_scan_paths(paths)
    if not scan_paths:
        return {
            "paths": [],
            "candidate_files": 0,
            "changed_files": 0,
            "queued": False,
            "queue_mode": "none",
            "job_id": "",
        }

    purged = purge_source_states_outside_root(db, source_type="nas", root=root)
    photo_max_bytes = max(0, int(settings.photo_max_size_mb or 0)) * 1024 * 1024

    cap = int(max_files or settings.ingestion_scan_max_files_per_run)
    files, stats = discover_files(
        scan_paths,
        allowed_extensions=settings.nas_allowed_extensions,
        exclude_dirs=settings.ingestion_scan_exclude_dirs,
        photo_extensions=settings.photo_file_extensions,
        photo_max_bytes=photo_max_bytes,
        recursive=bool(recursive),
        max_files=cap,
    )
    changed = collect_incremental_changes(db, files, source_type="nas")
    enqueue_paths = crud.filter_ignored_paths(db, changed)

    job_id = ""
    queue_mode = "none"
    queued = False
    if enqueue_paths:
        job = crud.create_ingestion_job(db, enqueue_paths)
        queue_mode = enqueue_ingestion_job(job.id)
        job_id = job.id
        queued = True
    else:
        db.commit()

    logger.info(
        "nas_scan_completed",
        extra=sanitize_log_context(
            {
                "status": "ok",
                "paths": scan_paths,
                "candidate_files": int(stats.get("discovered_files") or 0),
                "changed_files": len(enqueue_paths),
                "ignored_paths": max(0, len(changed) - len(enqueue_paths)),
                "queued": queued,
                "job_id": job_id,
                "purged_states": purged,
            }
        ),
    )
    return {
        "paths": scan_paths,
        "candidate_files": int(stats.get("discovered_files") or 0),
        "changed_files": len(enqueue_paths),
        "changed_paths": changed,
        "queued_paths": enqueue_paths,
        "missing_paths": int(stats.get("missing_paths") or 0),
        "queued": queued,
        "queue_mode": queue_mode,
        "job_id": job_id,
    }
