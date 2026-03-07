import datetime as dt
import time
import uuid
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.runtime_config import get_runtime_setting

logger = get_logger(__name__)
settings = get_settings()
_collection_ready = False
_query_embedding_cache: dict[str, tuple[float, list[float]]] = {}
_QUERY_CACHE_TTL_SEC = 600.0
_QUERY_CACHE_MAX = 128


def qdrant_payload(
    *,
    doc_id: str,
    chunk_id: str,
    doc_lang: str,
    category_path: str,
    source_type: str,
    updated_at: dt.datetime,
    title_en: str,
    title_zh: str,
    text: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "doc_lang": doc_lang,
        "category_path": category_path,
        "source_type": source_type,
        "updated_at": updated_at.isoformat(),
        "title_en": title_en,
        "title_zh": title_zh,
        "tags": list(tags or []),
        "text": text,
    }


def _embed_text(text: str, db: Session | None = None) -> list[float]:
    return _embed_texts([text], db=db)[0]


def _embed_texts(
    texts: list[str], db: Session | None = None
) -> list[list[float]]:
    clean = [str(t or "") for t in texts]
    if not clean:
        return []
    url = settings.ollama_base_url.rstrip("/") + "/api/embed"
    input_payload: Any = (
        clean if (settings.qdrant_embed_batch_enable and len(clean) > 1) else clean[0]
    )
    r = requests.post(
        url,
        json={
            "model": get_runtime_setting("embed_model", db),
            "input": input_payload,
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json() if hasattr(r, "json") else {}
    embeddings = data.get("embeddings") if isinstance(data, dict) else []
    if not embeddings or not isinstance(embeddings, list):
        raise RuntimeError("embed_invalid")
    vectors: list[list[float]] = []
    for emb in embeddings:
        if not isinstance(emb, list):
            raise RuntimeError("embed_invalid")
        vec = [float(x) for x in emb]
        if len(vec) != int(settings.qdrant_vector_size):
            raise RuntimeError(f"embed_dim_mismatch:{len(vec)}")
        vectors.append(vec)
    if len(vectors) != len(clean):
        # Some runtimes may return a single vector even for list input; fall back per-item.
        if len(clean) == 1 and len(vectors) == 1:
            return vectors
        if settings.qdrant_embed_batch_enable and len(clean) > 1:
            return [_embed_texts([item], db=db)[0] for item in clean]
        raise RuntimeError(f"embed_count_mismatch:{len(vectors)}:{len(clean)}")
    return vectors


def _embed_query_cached(query: str, db: Session | None = None) -> list[float]:
    key = str(query or "").strip().lower()
    if not key:
        return _embed_text(query, db=db)

    now = time.time()
    hit = _query_embedding_cache.get(key)
    if hit and (now - float(hit[0]) <= _QUERY_CACHE_TTL_SEC):
        return hit[1]

    vec = _embed_text(query, db=db)
    _query_embedding_cache[key] = (now, vec)
    if len(_query_embedding_cache) > _QUERY_CACHE_MAX:
        oldest_key = min(
            _query_embedding_cache, key=lambda k: _query_embedding_cache[k][0]
        )
        _query_embedding_cache.pop(oldest_key, None)
    return vec


def _stable_point_id(doc_id: str, chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fkv|{doc_id}|{chunk_id}"))


def stable_point_id(doc_id: str, chunk_id: str) -> str:
    return _stable_point_id(doc_id, chunk_id)


def ensure_collection_exists(force: bool = False) -> None:
    global _collection_ready
    if _collection_ready and (not force):
        return

    base = settings.qdrant_url.rstrip("/")
    name = settings.qdrant_collection
    get_url = f"{base}/collections/{name}"
    r = requests.get(get_url, timeout=10)
    if int(getattr(r, "status_code", 0) or 0) == 200:
        _collection_ready = True
        return
    if int(getattr(r, "status_code", 0) or 0) != 404:
        raise RuntimeError(f"qdrant_collection_check_failed:{r.status_code}")

    create_url = f"{base}/collections/{name}"
    payload = {
        "vectors": {"size": int(settings.qdrant_vector_size), "distance": "Cosine"}
    }
    cr = requests.put(create_url, json=payload, timeout=15)
    if int(getattr(cr, "status_code", 0) or 0) >= 400:
        raise RuntimeError(f"qdrant_collection_create_failed:{cr.status_code}")
    _collection_ready = True


def upsert_records(records: list[dict[str, Any]], db: Session | None = None) -> None:
    if (not settings.qdrant_enable) or (not records):
        return
    ensure_collection_exists()

    clean_records = []
    for rec in records:
        text = str(rec.get("text") or "").strip()
        if not text:
            continue
        clean_records.append(rec)

    if not clean_records:
        return

    embed_batch_size = max(1, int(settings.qdrant_embed_batch_size or 16))
    upsert_batch_size = max(1, int(settings.qdrant_upsert_batch_size or 64))
    points: list[dict[str, Any]] = []
    for start in range(0, len(clean_records), embed_batch_size):
        batch = clean_records[start : start + embed_batch_size]
        vectors = _embed_texts(
            [str(item.get("text") or "").strip() for item in batch], db=db
        )
        for rec, vec in zip(batch, vectors):
            pid = _stable_point_id(str(rec.get("doc_id")), str(rec.get("chunk_id")))
            points.append({"id": pid, "vector": vec, "payload": rec})

    url = (
        settings.qdrant_url.rstrip("/")
        + f"/collections/{settings.qdrant_collection}/points?wait=true"
    )
    for start in range(0, len(points), upsert_batch_size):
        batch_points = points[start : start + upsert_batch_size]
        r = requests.put(url, json={"points": batch_points}, timeout=30)
        if int(getattr(r, "status_code", 0) or 0) >= 400:
            logger.warning(
                "qdrant_upsert_failed",
                extra=sanitize_log_context(
                    {
                        "status": r.status_code,
                        "error_code": "qdrant_upsert_http",
                        "doc_id": batch_points[0].get("payload", {}).get("doc_id"),
                        "batch_size": len(batch_points),
                    }
                ),
            )
            raise RuntimeError(f"qdrant_upsert_http:{r.status_code}")


def search_records(
    *,
    query: str,
    top_k: int,
    score_threshold: float = 0.0,
    category_path: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    if (not settings.qdrant_enable) or (not str(query or "").strip()) or top_k <= 0:
        return []

    ensure_collection_exists()

    url = (
        settings.qdrant_url.rstrip("/")
        + f"/collections/{settings.qdrant_collection}/points/search"
    )
    body: dict[str, Any] = {
        "vector": _embed_query_cached(query, db=db),
        "limit": int(top_k),
        "with_payload": True,
    }
    if category_path:
        body["filter"] = {
            "must": [
                {
                    "key": "category_path",
                    "match": {"value": category_path},
                }
            ]
        }

    r = requests.post(url, json=body, timeout=20)
    if int(getattr(r, "status_code", 0) or 0) >= 400:
        raise RuntimeError(f"qdrant_search_http:{r.status_code}")

    data = r.json() if hasattr(r, "json") else {}
    rows = data.get("result") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []

    hits: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        score = float(row.get("score") or 0.0)
        if score < float(score_threshold):
            continue
        text = str(payload.get("text") or "")
        hits.append(
            {
                "doc_id": str(payload.get("doc_id") or ""),
                "chunk_id": str(payload.get("chunk_id") or ""),
                "score": score,
                "text_snippet": text[:220].strip(),
                "doc_lang": str(payload.get("doc_lang") or "unknown"),
                "title_en": str(payload.get("title_en") or ""),
                "title_zh": str(payload.get("title_zh") or ""),
                "tags": payload.get("tags")
                if isinstance(payload.get("tags"), list)
                else [],
                "category_path": str(payload.get("category_path") or "archive/misc"),
                "source_type": str(payload.get("source_type") or "file"),
                "updated_at": payload.get("updated_at"),
            }
        )
    return hits


def delete_records_by_point_ids(
    point_ids: list[str], *, wait: bool = True
) -> dict[str, int]:
    ids = []
    seen: set[str] = set()
    for raw in point_ids:
        value = str(raw or "").strip()
        if (not value) or (value in seen):
            continue
        seen.add(value)
        ids.append(value)

    if not settings.qdrant_enable:
        return {"requested": len(ids), "deleted": 0}
    if not ids:
        return {"requested": 0, "deleted": 0}

    ensure_collection_exists()
    wait_q = "true" if wait else "false"
    url = (
        settings.qdrant_url.rstrip("/")
        + f"/collections/{settings.qdrant_collection}/points/delete?wait={wait_q}"
    )
    body = {"points": ids}
    r = requests.post(url, json=body, timeout=20)
    if int(getattr(r, "status_code", 0) or 0) >= 400:
        logger.warning(
            "qdrant_delete_points_failed",
            extra=sanitize_log_context(
                {
                    "status": r.status_code,
                    "error_code": "qdrant_delete_points_http",
                    "requested": len(ids),
                }
            ),
        )
        raise RuntimeError(f"qdrant_delete_points_http:{r.status_code}")
    return {"requested": len(ids), "deleted": len(ids)}


def delete_records_by_doc_id(doc_id: str, *, wait: bool = True) -> dict[str, int]:
    target_doc_id = str(doc_id or "").strip()
    if not settings.qdrant_enable:
        return {"requested": 1 if target_doc_id else 0, "deleted": 0}
    if not target_doc_id:
        return {"requested": 0, "deleted": 0}

    ensure_collection_exists()
    wait_q = "true" if wait else "false"
    url = (
        settings.qdrant_url.rstrip("/")
        + f"/collections/{settings.qdrant_collection}/points/delete?wait={wait_q}"
    )
    body = {
        "filter": {
            "must": [
                {
                    "key": "doc_id",
                    "match": {"value": target_doc_id},
                }
            ]
        }
    }
    r = requests.post(url, json=body, timeout=20)
    if int(getattr(r, "status_code", 0) or 0) >= 400:
        logger.warning(
            "qdrant_delete_doc_failed",
            extra=sanitize_log_context(
                {
                    "status": r.status_code,
                    "error_code": "qdrant_delete_doc_http",
                    "doc_id": target_doc_id,
                }
            ),
        )
        raise RuntimeError(f"qdrant_delete_doc_http:{r.status_code}")
    return {"requested": 1, "deleted": 1}
