import datetime as dt
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document, DocumentStatus
from app.services.source_tags import DEFAULT_CATEGORY_PATH, category_labels_for_path

LEGACY_CATEGORY_PATHS: tuple[str, ...] = (
    "general",
    "finance/utilities",
    "finance/telecom",
    "property/strata",
    "property",
)


def normalize_category_path_value(path: str | None) -> str:
    return str(path or "").strip().lower()


def is_legacy_category_path(path: str | None) -> bool:
    return normalize_category_path_value(path) in LEGACY_CATEGORY_PATHS


def apply_legacy_category_guard(path: str | None) -> tuple[str, bool]:
    normalized = normalize_category_path_value(path)
    if is_legacy_category_path(normalized):
        return (DEFAULT_CATEGORY_PATH, True)
    if not normalized:
        return (DEFAULT_CATEGORY_PATH, False)
    return (normalized, False)


def _status_totals(db: Session) -> dict[str, int]:
    rows = db.execute(select(Document.status, func.count()).group_by(Document.status)).all()
    out: dict[str, int] = {}
    for status, count in rows:
        key = str(status or "").strip().lower() or "unknown"
        out[key] = int(count or 0)
    return out


def _legacy_counts_by_status(db: Session) -> dict[str, int]:
    rows = (
        db.execute(
            select(Document.status, func.count())
            .where(func.lower(Document.category_path).in_(LEGACY_CATEGORY_PATHS))
            .group_by(Document.status)
        )
        .all()
    )
    out: dict[str, int] = {}
    for status, count in rows:
        key = str(status or "").strip().lower() or "unknown"
        out[key] = int(count or 0)
    return out


def build_category_debt_snapshot(db: Session, *, top_limit: int = 20) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    safe_top = max(1, min(100, int(top_limit)))

    total_all = int(db.scalar(select(func.count()).select_from(Document)) or 0)
    total_completed = int(
        db.scalar(select(func.count()).select_from(Document).where(Document.status == DocumentStatus.COMPLETED.value)) or 0
    )

    legacy_all = int(
        db.scalar(select(func.count()).select_from(Document).where(func.lower(Document.category_path).in_(LEGACY_CATEGORY_PATHS))) or 0
    )
    legacy_completed = int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.status == DocumentStatus.COMPLETED.value,
                func.lower(Document.category_path).in_(LEGACY_CATEGORY_PATHS),
            )
        )
        or 0
    )

    status_totals = _status_totals(db)
    legacy_by_status = _legacy_counts_by_status(db)
    ratio_by_status: dict[str, float] = {}
    for key, total in status_totals.items():
        if total <= 0:
            ratio_by_status[key] = 0.0
            continue
        ratio_by_status[key] = round(float(legacy_by_status.get(key, 0)) / float(total), 6)

    top_rows = (
        db.execute(
            select(Document.id, Document.file_name, Document.status, Document.category_path, Document.updated_at)
            .where(func.lower(Document.category_path).in_(LEGACY_CATEGORY_PATHS))
            .order_by(Document.updated_at.desc())
            .limit(safe_top)
        )
        .all()
    )
    top_files: list[dict[str, Any]] = []
    for doc_id, file_name, status, category_path, updated_at in top_rows:
        safe_name = str(file_name or "")
        top_files.append(
            {
                "doc_id": str(doc_id or ""),
                "file_name": safe_name[:240],
                "status": str(status or ""),
                "category_path": str(category_path or ""),
                "updated_at": updated_at.isoformat() if updated_at else "",
            }
        )

    return {
        "snapshot_at": now.isoformat(),
        "legacy_paths": list(LEGACY_CATEGORY_PATHS),
        "scope_prod": {
            "status_filter": DocumentStatus.COMPLETED.value,
            "total_docs": total_completed,
            "legacy_docs": legacy_completed,
            "legacy_ratio": round(float(legacy_completed) / float(total_completed), 6) if total_completed > 0 else 0.0,
        },
        "scope_audit": {
            "status_filter": "all",
            "total_docs": total_all,
            "legacy_docs": legacy_all,
            "legacy_ratio": round(float(legacy_all) / float(total_all), 6) if total_all > 0 else 0.0,
        },
        "legacy_counts_by_status": legacy_by_status,
        "legacy_ratio_by_status": ratio_by_status,
        "top_legacy_files": top_files,
    }


def ensure_non_legacy_labels(path: str | None) -> tuple[str, str, str]:
    safe_path, _blocked = apply_legacy_category_guard(path)
    label_en, label_zh = category_labels_for_path(safe_path)
    return (label_en, label_zh, safe_path)


def load_snapshots_from_dir(*, data_dir: Path, days: int = 30) -> list[dict[str, Any]]:
    safe_days = max(1, min(365, int(days)))
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=safe_days)
    files = sorted(data_dir.glob("category_debt_snapshot_*.json"))
    out: list[dict[str, Any]] = []
    for file in files:
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = str(payload.get("snapshot_at") or "").strip()
        if not ts:
            continue
        try:
            snap_dt = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if snap_dt.tzinfo is None:
            snap_dt = snap_dt.replace(tzinfo=dt.UTC)
        if snap_dt < cutoff:
            continue
        out.append(payload)
    out.sort(key=lambda item: str(item.get("snapshot_at") or ""))
    return out


def compute_debt_trend(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for row in snapshots:
        points.append(
            {
                "snapshot_at": str(row.get("snapshot_at") or ""),
                "prod_legacy_docs": int(((row.get("scope_prod") or {}).get("legacy_docs") or 0)),
                "prod_total_docs": int(((row.get("scope_prod") or {}).get("total_docs") or 0)),
                "audit_legacy_docs": int(((row.get("scope_audit") or {}).get("legacy_docs") or 0)),
                "audit_total_docs": int(((row.get("scope_audit") or {}).get("total_docs") or 0)),
            }
        )

    if len(points) >= 8:
        curr = points[-1]["audit_legacy_docs"]
        prev = points[-8]["audit_legacy_docs"]
        week_change = int(curr) - int(prev)
    elif len(points) >= 2:
        week_change = int(points[-1]["audit_legacy_docs"]) - int(points[0]["audit_legacy_docs"])
    else:
        week_change = 0

    return {
        "points": points,
        "week_over_week_change": week_change,
    }
