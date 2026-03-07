import json
import re
from typing import Any

from app.services.query_policy import _safe_text

_JSON_BLOCK = re.compile(r"\{.*\}", flags=re.S)

_SEARCH_FALLBACK_BOILERPLATE_PATTERNS = (
    r"\bbpay\b",
    r"\bbanking-?bpay\b",
    r"\busage details\b",
    r"\bplan features\b",
    r"\bconditional pay\b",
    r"\bsome handy hints\b",
    r"\bmonthly billing\b",
)


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    matched = _JSON_BLOCK.search(raw)
    if not matched:
        return {}
    try:
        parsed = json.loads(matched.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _clean_search_fallback_snippet(value: Any, *, cap: int = 280) -> str:
    raw = str(value or "")
    collapsed = " ".join(raw.split())
    if not collapsed:
        return ""
    cleaned = collapsed
    for pattern in _SEARCH_FALLBACK_BOILERPLATE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ;,.-")
    base = cleaned if len(cleaned) >= 24 else collapsed
    return _safe_text(base, cap=cap)


def _doc_ids_from_scope(scope: dict[str, Any], *, client_context: dict[str, Any] | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    values = scope.get("doc_ids")
    if isinstance(values, list):
        for raw in values:
            doc_id = str(raw or "").strip()
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                out.append(doc_id)

    selected_docs = scope.get("selected_docs")
    if isinstance(selected_docs, list):
        for item in selected_docs:
            if not isinstance(item, dict):
                continue
            doc_id = str(item.get("doc_id") or "").strip()
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                out.append(doc_id)

    selected_doc_ids = (client_context or {}).get("selected_doc_ids")
    if isinstance(selected_doc_ids, list):
        for raw in selected_doc_ids:
            doc_id = str(raw or "").strip()
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                out.append(doc_id)

    return out


def _category_from_scope(scope: dict[str, Any]) -> str | None:
    value = str(scope.get("category_path") or "").strip()
    return value or None
