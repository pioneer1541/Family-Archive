#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _default_output() -> Path:
    return (ROOT_DIR.parent / "data" / "mail_attachment_migration_report.json").resolve()


def _count_files_and_size(root: str) -> tuple[int, int]:
    total_files = 0
    total_bytes = 0
    r = str(root or "").strip()
    if (not r) or (not os.path.isdir(r)):
        return (0, 0)
    for cur, _, files in os.walk(r):
        for name in files:
            path = os.path.join(cur, name)
            try:
                st = os.stat(path)
            except Exception:
                continue
            total_files += 1
            total_bytes += int(getattr(st, "st_size", 0) or 0)
    return (total_files, total_bytes)


def _copy_tree(src_root: str, dst_root: str, *, apply: bool) -> dict[str, Any]:
    src = str(src_root or "").strip()
    dst = str(dst_root or "").strip()
    copied = 0
    skipped = 0
    failed = 0
    copied_bytes = 0
    items: list[dict[str, Any]] = []

    if (not src) or (not os.path.isdir(src)):
        return {
            "src_root": src,
            "dst_root": dst,
            "status": "source_missing",
            "copied_count": 0,
            "copied_bytes": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "errors": [{"path": src, "error": "source_missing"}],
        }

    os.makedirs(dst, exist_ok=True)
    for cur, _, files in os.walk(src):
        rel = os.path.relpath(cur, src)
        dst_dir = dst if rel == "." else os.path.join(dst, rel)
        if apply:
            os.makedirs(dst_dir, exist_ok=True)
        for name in files:
            src_path = os.path.join(cur, name)
            dst_path = os.path.join(dst_dir, name)
            try:
                src_st = os.stat(src_path)
                src_size = int(getattr(src_st, "st_size", 0) or 0)
                src_mtime = float(getattr(src_st, "st_mtime", 0.0) or 0.0)
            except Exception:
                failed += 1
                items.append({"src_path": src_path, "dst_path": dst_path, "action": "failed", "error": "stat_failed"})
                continue

            action = "copy"
            if os.path.exists(dst_path):
                try:
                    dst_st = os.stat(dst_path)
                    dst_size = int(getattr(dst_st, "st_size", 0) or 0)
                    dst_mtime = float(getattr(dst_st, "st_mtime", 0.0) or 0.0)
                    if dst_size == src_size and dst_mtime >= src_mtime:
                        action = "skip"
                except Exception:
                    action = "copy"

            if action == "skip":
                skipped += 1
                items.append({"src_path": src_path, "dst_path": dst_path, "action": "skip"})
                continue

            if not apply:
                copied += 1
                copied_bytes += src_size
                items.append({"src_path": src_path, "dst_path": dst_path, "action": "would_copy"})
                continue

            try:
                shutil.copy2(src_path, dst_path)
                copied += 1
                copied_bytes += src_size
                items.append({"src_path": src_path, "dst_path": dst_path, "action": "copied"})
            except Exception as exc:
                failed += 1
                items.append({"src_path": src_path, "dst_path": dst_path, "action": "failed", "error": type(exc).__name__})

    return {
        "src_root": src,
        "dst_root": dst,
        "status": "ok",
        "copied_count": int(copied),
        "copied_bytes": int(copied_bytes),
        "skipped_count": int(skipped),
        "failed_count": int(failed),
        "items": items,
    }


def _db_counts(db, old_root: str, new_root: str) -> dict[str, int]:
    queries = {
        "documents_old_root": "select count(*) from documents where source_path like :prefix",
        "documents_new_root": "select count(*) from documents where source_path like :prefix_new",
        "events_old_root": "select count(*) from mail_ingestion_events where attachment_path like :prefix",
        "events_new_root": "select count(*) from mail_ingestion_events where attachment_path like :prefix_new",
        "sync_items_old_root": "select count(*) from sync_run_items where source_type='mail' and source_path like :prefix",
        "sync_items_new_root": "select count(*) from sync_run_items where source_type='mail' and source_path like :prefix_new",
    }
    out: dict[str, int] = {}
    params = {"prefix": f"{old_root.rstrip('/')}/%", "prefix_new": f"{new_root.rstrip('/')}/%"}
    for key, sql in queries.items():
        out[key] = int(db.execute(text(sql), params).scalar() or 0)
    return out


def _apply_db_rewrite(db, old_root: str, new_root: str) -> dict[str, int]:
    sqls = {
        "documents_updated": (
            "update documents set source_path = replace(source_path, :old_root, :new_root) "
            "where source_path like :prefix"
        ),
        "events_updated": (
            "update mail_ingestion_events set attachment_path = replace(attachment_path, :old_root, :new_root) "
            "where attachment_path like :prefix"
        ),
        "sync_items_updated": (
            "update sync_run_items set source_path = replace(source_path, :old_root, :new_root) "
            "where source_type='mail' and source_path like :prefix"
        ),
    }
    out: dict[str, int] = {}
    params = {
        "old_root": old_root.rstrip("/"),
        "new_root": new_root.rstrip("/"),
        "prefix": f"{old_root.rstrip('/')}/%",
    }
    for key, sql in sqls.items():
        res = db.execute(text(sql), params)
        out[key] = int(res.rowcount or 0)
    return out


def _verify_paths(db, new_root: str, host_new_root: str, *, sample_limit: int = 20) -> dict[str, Any]:
    rows = db.execute(
        text(
            "select id, source_path from documents "
            "where source_path like :prefix order by updated_at desc"
        ),
        {"prefix": f"{new_root.rstrip('/')}/%"},
    ).fetchall()

    missing = 0
    samples: list[dict[str, str]] = []
    for row in rows:
        doc_id = str(row[0] or "")
        source_path = str(row[1] or "")
        host_path = source_path.replace(new_root.rstrip("/"), host_new_root.rstrip("/"), 1)
        if not os.path.isfile(host_path):
            missing += 1
            if len(samples) < sample_limit:
                samples.append({"doc_id": doc_id, "source_path": source_path, "host_path": host_path})
    return {"checked_count": len(rows), "missing_count": int(missing), "missing_samples": samples}


def _clear_old_root(old_host_root: str, *, apply: bool) -> dict[str, Any]:
    root = str(old_host_root or "").strip()
    if (not root) or (not os.path.isdir(root)):
        return {"status": "source_missing", "root": root, "deleted_files": 0, "deleted_dirs": 0, "errors": []}

    deleted_files = 0
    deleted_dirs = 0
    errors: list[dict[str, str]] = []

    for cur, dirs, files in os.walk(root, topdown=False):
        for name in files:
            path = os.path.join(cur, name)
            if not apply:
                deleted_files += 1
                continue
            try:
                os.remove(path)
                deleted_files += 1
            except Exception as exc:
                errors.append({"path": path, "error": type(exc).__name__})
        for name in dirs:
            dpath = os.path.join(cur, name)
            if not apply:
                deleted_dirs += 1
                continue
            try:
                os.rmdir(dpath)
                deleted_dirs += 1
            except OSError:
                # Not empty is acceptable.
                pass
            except Exception as exc:
                errors.append({"path": dpath, "error": type(exc).__name__})
    return {
        "status": "ok",
        "root": root,
        "deleted_files": int(deleted_files),
        "deleted_dirs": int(deleted_dirs),
        "errors": errors,
    }


def migrate_mail_attachments(
    *,
    apply: bool,
    old_root: str,
    new_root: str,
    old_host_root: str,
    new_host_root: str,
    clear_old_host_root: bool,
    output: Path,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "started_at": _now_iso(),
        "apply": bool(apply),
        "roots": {
            "old_root": str(old_root),
            "new_root": str(new_root),
            "old_host_root": str(old_host_root),
            "new_host_root": str(new_host_root),
        },
    }

    pre_local_count, pre_local_bytes = _count_files_and_size(old_host_root)
    pre_nas_count, pre_nas_bytes = _count_files_and_size(new_host_root)
    report["pre_counts"] = {
        "old_host_files": int(pre_local_count),
        "old_host_bytes": int(pre_local_bytes),
        "new_host_files": int(pre_nas_count),
        "new_host_bytes": int(pre_nas_bytes),
    }

    db = SessionLocal()
    try:
        report["db_counts_before"] = _db_counts(db, old_root, new_root)
        report["copy"] = _copy_tree(old_host_root, new_host_root, apply=apply)
        if apply:
            report["db_rewrite"] = _apply_db_rewrite(db, old_root, new_root)
            db.commit()
        else:
            report["db_rewrite"] = {"documents_updated": 0, "events_updated": 0, "sync_items_updated": 0}
            db.rollback()
        report["db_counts_after"] = _db_counts(db, old_root, new_root)
        report["verify"] = _verify_paths(db, new_root, new_host_root)
    except Exception as exc:
        db.rollback()
        report["status"] = "failed"
        report["error"] = type(exc).__name__
    finally:
        db.close()

    if clear_old_host_root:
        report["clear_old_root"] = _clear_old_root(old_host_root, apply=apply)
        post_local_count, post_local_bytes = _count_files_and_size(old_host_root)
    else:
        report["clear_old_root"] = {"status": "skipped"}
        post_local_count, post_local_bytes = _count_files_and_size(old_host_root)

    post_nas_count, post_nas_bytes = _count_files_and_size(new_host_root)
    report["post_counts"] = {
        "old_host_files": int(post_local_count),
        "old_host_bytes": int(post_local_bytes),
        "new_host_files": int(post_nas_count),
        "new_host_bytes": int(post_nas_bytes),
    }

    if "status" not in report:
        missing_after = int(report.get("verify", {}).get("missing_count") or 0)
        copy_failed = int(report.get("copy", {}).get("failed_count") or 0)
        report["status"] = "ok" if (missing_after == 0 and copy_failed == 0) else "partial"

    report["finished_at"] = _now_iso()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate mail attachments from local data dir to NAS and rewrite DB source paths."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Dry run only (default).")
    mode.add_argument("--apply", action="store_true", help="Apply migration.")
    parser.add_argument("--old-root", type=str, default="/app/data/mail_attachments", help="Old root stored in DB.")
    parser.add_argument(
        "--new-root",
        type=str,
        default="/volume1/Family_Archives/mail_attachments",
        help="New root stored in DB.",
    )
    parser.add_argument(
        "--old-host-root",
        type=str,
        default="/app/data/mail_attachments",
        help="Filesystem path used to read old files during migration run.",
    )
    parser.add_argument(
        "--new-host-root",
        type=str,
        default="/volume1/Family_Archives/mail_attachments",
        help="Filesystem path used to write new files during migration run.",
    )
    parser.add_argument(
        "--clear-old-host-root",
        action="store_true",
        help="After successful copy/rewrite, remove files under old host root.",
    )
    parser.add_argument("--output", type=str, default=str(_default_output()), help="Report output path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    apply = bool(args.apply)
    report = migrate_mail_attachments(
        apply=apply,
        old_root=str(args.old_root or "").strip(),
        new_root=str(args.new_root or "").strip(),
        old_host_root=str(args.old_host_root or "").strip(),
        new_host_root=str(args.new_host_root or "").strip(),
        clear_old_host_root=bool(args.clear_old_host_root),
        output=Path(str(args.output)).resolve(),
    )
    print(
        json.dumps(
            {
                "apply": report.get("apply"),
                "status": report.get("status"),
                "db_before": report.get("db_counts_before"),
                "db_after": report.get("db_counts_after"),
                "db_rewrite": report.get("db_rewrite"),
                "copy_failed": report.get("copy", {}).get("failed_count"),
                "verify_missing": report.get("verify", {}).get("missing_count"),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
