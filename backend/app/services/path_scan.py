import datetime as dt
import os
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models


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


def _normalize_exts(exts: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for raw in exts:
        item = str(raw or "").strip().lower().lstrip(".")
        if item:
            out.add(item)
    return out


def _normalize_excludes(excludes: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for raw in excludes:
        item = str(raw or "").strip().lower()
        if item:
            out.add(item)
    return out


def _is_excluded_file_name(path: str) -> bool:
    name = str(os.path.basename(str(path or "")).strip()).lower()
    if not name:
        return True
    if name in {".ds_store", "thumbs.db"}:
        return True
    if name.startswith("._"):
        return True
    if name.startswith("~$"):
        return True
    return False


def _is_supported_file(
    path: str, allowed_exts: set[str], *, photo_exts: set[str], photo_max_bytes: int
) -> bool:
    if not os.path.isfile(path):
        return False
    if _is_excluded_file_name(path):
        return False
    ext = os.path.splitext(path)[1].strip().lower().lstrip(".")
    if allowed_exts and (ext not in allowed_exts):
        return False
    if ext in photo_exts and photo_max_bytes > 0:
        try:
            if os.path.getsize(path) > photo_max_bytes:
                return False
        except Exception:
            return False
    return True


def discover_files(
    paths: list[str],
    *,
    allowed_extensions: Iterable[str],
    exclude_dirs: Iterable[str],
    photo_extensions: Iterable[str] | None = None,
    photo_max_bytes: int = 0,
    recursive: bool = True,
    max_files: int = 5000,
) -> tuple[list[str], dict[str, int]]:
    allowed_exts = _normalize_exts(allowed_extensions)
    photo_exts = _normalize_exts(photo_extensions or [])
    photo_cap = max(0, int(photo_max_bytes or 0))
    excluded = _normalize_excludes(exclude_dirs)

    files: list[str] = []
    seen: set[str] = set()
    stats = {
        "input_paths": len(paths),
        "missing_paths": 0,
        "directories": 0,
        "raw_files": 0,
        "discovered_files": 0,
    }

    cap = max(1, int(max_files))
    for raw in paths:
        p = str(raw or "").strip()
        if not p:
            continue
        if not os.path.exists(p):
            stats["missing_paths"] += 1
            continue
        if os.path.isfile(p):
            stats["raw_files"] += 1
            rp = os.path.realpath(p)
            if (
                _is_supported_file(
                    rp, allowed_exts, photo_exts=photo_exts, photo_max_bytes=photo_cap
                )
                and rp not in seen
            ):
                seen.add(rp)
                files.append(rp)
            if len(files) >= cap:
                break
            continue

        if not os.path.isdir(p):
            continue

        stats["directories"] += 1
        root = os.path.realpath(p)
        if not recursive:
            try:
                for fn in sorted(os.listdir(root)):
                    full = os.path.realpath(os.path.join(root, fn))
                    if full in seen:
                        continue
                    if _is_supported_file(
                        full,
                        allowed_exts,
                        photo_exts=photo_exts,
                        photo_max_bytes=photo_cap,
                    ):
                        seen.add(full)
                        files.append(full)
                    if len(files) >= cap:
                        break
            except Exception:
                continue
            if len(files) >= cap:
                break
            continue

        try:
            for cur, dirs, fns in os.walk(root):
                dirs[:] = [d for d in dirs if d.lower() not in excluded]
                for fn in sorted(fns):
                    full = os.path.realpath(os.path.join(cur, fn))
                    if full in seen:
                        continue
                    if _is_supported_file(
                        full,
                        allowed_exts,
                        photo_exts=photo_exts,
                        photo_max_bytes=photo_cap,
                    ):
                        seen.add(full)
                        files.append(full)
                    if len(files) >= cap:
                        break
                if len(files) >= cap:
                    break
            if len(files) >= cap:
                break
        except Exception:
            continue

    stats["discovered_files"] = len(files)
    return (files, stats)


def collect_incremental_changes(
    db: Session, files: list[str], *, source_type: str
) -> list[str]:
    changed: list[str] = []
    now = dt.datetime.now(dt.UTC)
    src_type = str(source_type or "nas").strip() or "nas"
    for path in files:
        p = str(path or "").strip()
        if not p:
            continue
        try:
            st = os.stat(p)
        except Exception:
            continue
        mtime_ns = int(
            getattr(st, "st_mtime_ns", int(float(st.st_mtime) * 1_000_000_000))
        )
        size = int(getattr(st, "st_size", 0) or 0)
        row = db.get(models.SourceFileState, p)
        if row is None:
            row = models.SourceFileState(
                path=p,
                source_type=src_type,
                mtime_ns=mtime_ns,
                size=size,
                last_seen_at=now,
            )
            db.add(row)
            changed.append(p)
            continue
        if (int(row.mtime_ns or 0) != mtime_ns) or (int(row.size or 0) != size):
            row.source_type = src_type
            row.mtime_ns = mtime_ns
            row.size = size
            row.last_seen_at = now
            changed.append(p)
            continue
        row.last_seen_at = now
    return changed


def purge_source_states_outside_root(
    db: Session, *, source_type: str, root: str
) -> int:
    src = str(source_type or "").strip()
    rt = _real(root)
    if (not src) or (not rt):
        return 0

    rows = (
        db.execute(
            select(models.SourceFileState).where(
                models.SourceFileState.source_type == src
            )
        )
        .scalars()
        .all()
    )
    removed = 0
    for row in rows:
        path = str(getattr(row, "path", "") or "").strip()
        if _is_within(path, rt):
            continue
        db.delete(row)
        removed += 1
    return removed
