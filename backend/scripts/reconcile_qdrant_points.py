import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings


settings = get_settings()


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _db_path_from_url(database_url: str) -> str:
    raw = str(database_url or "").strip()
    if raw.startswith("sqlite:////"):
        return "/" + raw[len("sqlite:////") :]
    if raw.startswith("sqlite:///"):
        return raw[len("sqlite:///") :]
    raise ValueError(f"unsupported_database_url:{raw}")


def _load_chunk_doc_map(db_path: str) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("select id, document_id from chunks").fetchall()
    finally:
        conn.close()
    out: dict[str, str] = {}
    for chunk_id, doc_id in rows:
        cid = str(chunk_id or "").strip()
        did = str(doc_id or "").strip()
        if cid and did:
            out[cid] = did
    return out


def _iter_qdrant_points(
    *,
    qdrant_url: str,
    collection: str,
    page_size: int,
    timeout_sec: int,
):
    base = str(qdrant_url or "").rstrip("/")
    name = str(collection or "").strip()
    if (not base) or (not name):
        return
    url = f"{base}/collections/{name}/points/scroll"
    next_offset: Any = None
    while True:
        body: dict[str, Any] = {
            "limit": max(1, int(page_size)),
            "with_payload": True,
            "with_vector": False,
        }
        if next_offset is not None:
            body["offset"] = next_offset
        resp = requests.post(url, json=body, timeout=max(3, int(timeout_sec)))
        resp.raise_for_status()
        data = resp.json() if hasattr(resp, "json") else {}
        result = data.get("result") if isinstance(data, dict) else {}
        points = result.get("points") if isinstance(result, dict) else []
        if not isinstance(points, list) or not points:
            break
        for point in points:
            if isinstance(point, dict):
                yield point
        next_offset = result.get("next_page_offset") if isinstance(result, dict) else None
        if next_offset is None:
            break


def _delete_point_batch(
    *,
    qdrant_url: str,
    collection: str,
    point_ids: list[str],
    timeout_sec: int,
) -> int:
    ids = [str(x or "").strip() for x in point_ids if str(x or "").strip()]
    if not ids:
        return 0
    base = str(qdrant_url or "").rstrip("/")
    name = str(collection or "").strip()
    url = f"{base}/collections/{name}/points/delete?wait=true"
    resp = requests.post(url, json={"points": ids}, timeout=max(3, int(timeout_sec)))
    resp.raise_for_status()
    return len(ids)


def reconcile_qdrant_points(
    *,
    db_path: str,
    qdrant_url: str,
    collection: str,
    apply: bool,
    page_size: int = 256,
    delete_batch_size: int = 256,
    timeout_sec: int = 15,
    sample_limit: int = 40,
) -> dict[str, Any]:
    started_at = _now_iso()
    chunk_doc_map = _load_chunk_doc_map(db_path)

    to_delete: list[str] = []
    reasons: dict[str, int] = {}
    sample_orphans: list[dict[str, str]] = []
    scanned = 0
    kept = 0

    for point in _iter_qdrant_points(
        qdrant_url=qdrant_url,
        collection=collection,
        page_size=page_size,
        timeout_sec=timeout_sec,
    ):
        scanned += 1
        point_id = str(point.get("id") or "").strip()
        payload = point.get("payload") if isinstance(point.get("payload"), dict) else {}
        doc_id = str(payload.get("doc_id") or "").strip()
        chunk_id = str(payload.get("chunk_id") or "").strip()

        reason = ""
        if (not doc_id) or (not chunk_id):
            reason = "missing_payload_keys"
        else:
            expected_doc_id = chunk_doc_map.get(chunk_id, "")
            if not expected_doc_id:
                reason = "missing_chunk"
            elif expected_doc_id != doc_id:
                reason = "doc_mismatch"

        if not reason:
            kept += 1
            continue

        reasons[reason] = int(reasons.get(reason, 0)) + 1
        if point_id:
            to_delete.append(point_id)
        if len(sample_orphans) < max(0, int(sample_limit)):
            sample_orphans.append(
                {
                    "point_id": point_id,
                    "doc_id": doc_id,
                    "chunk_id": chunk_id,
                    "reason": reason,
                }
            )

    deleted = 0
    delete_batches = 0
    if apply and to_delete:
        batch_size = max(1, int(delete_batch_size))
        for i in range(0, len(to_delete), batch_size):
            batch = to_delete[i : i + batch_size]
            deleted += _delete_point_batch(
                qdrant_url=qdrant_url,
                collection=collection,
                point_ids=batch,
                timeout_sec=timeout_sec,
            )
            delete_batches += 1

    finished_at = _now_iso()
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "qdrant_url": str(qdrant_url),
        "collection": str(collection),
        "db_path": str(db_path),
        "apply": bool(apply),
        "scanned_points": int(scanned),
        "kept_points": int(kept),
        "orphan_points": int(len(to_delete)),
        "delete_candidates": int(len(to_delete)),
        "deleted_points": int(deleted),
        "delete_batches": int(delete_batches),
        "orphan_reasons": reasons,
        "sample_orphans": sample_orphans,
    }


def _parse_args() -> argparse.Namespace:
    default_db_path = _db_path_from_url(settings.database_url)
    default_output = (ROOT_DIR.parent / "data" / "qdrant_reconcile_report.json").resolve()
    parser = argparse.ArgumentParser(description="Reconcile Qdrant points against SQLite chunks table.")
    parser.add_argument("--db-path", default=default_db_path, help="SQLite database file path.")
    parser.add_argument("--qdrant-url", default=settings.qdrant_url, help="Qdrant base URL.")
    parser.add_argument("--collection", default=settings.qdrant_collection, help="Qdrant collection name.")
    parser.add_argument("--page-size", type=int, default=256, help="Qdrant scroll page size.")
    parser.add_argument("--delete-batch-size", type=int, default=256, help="Qdrant delete batch size.")
    parser.add_argument("--timeout-sec", type=int, default=15, help="HTTP timeout seconds.")
    parser.add_argument("--sample-limit", type=int, default=40, help="Max sample orphan rows in report.")
    parser.add_argument("--output", default=str(default_output), help="Report output JSON path.")
    parser.add_argument("--apply", action="store_true", help="Apply deletion. Default is dry-run.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = reconcile_qdrant_points(
        db_path=str(args.db_path),
        qdrant_url=str(args.qdrant_url),
        collection=str(args.collection),
        apply=bool(args.apply),
        page_size=int(args.page_size),
        delete_batch_size=int(args.delete_batch_size),
        timeout_sec=int(args.timeout_sec),
        sample_limit=int(args.sample_limit),
    )
    output_path = Path(str(args.output)).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "apply": report.get("apply"),
                "collection": report.get("collection"),
                "scanned_points": report.get("scanned_points"),
                "orphan_points": report.get("orphan_points"),
                "deleted_points": report.get("deleted_points"),
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
