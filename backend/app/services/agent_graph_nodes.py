import hashlib
import json
import re
import socket
import time
import uuid
from typing import Any

import requests
from sqlalchemy import select

from app import crud
from app.models import Chunk, Document, DocumentStatus
from app.schemas import (
    AgentExecuteResponse,
    AgentExecutorStats,
    AgentRelatedDoc,
    BilingualText,
    DetailCoverageStats,
    DetailEvidenceRef,
    DetailRow,
    DetailSection,
    PlannerDecision,
    PlannerRequest,
    ResultCard,
    ResultCardSource,
    SearchRequest,
)
from app.services.agent_graph_state import AgentGraphState
from app.services.agent_queryspec import (
    apply_query_spec_to_planner_fields,
    build_query_spec_from_query,
    build_subtasks_from_query_spec,
    estimate_queryspec_confidence,
    prefilter_router_candidate_categories,
    required_slots_from_query_spec,
    slot_query_terms,
)
from app.services.agent_slots import (
    derive_facts,
    extract_slots_from_chunks,
    judge_sufficiency,
    slot_results_to_detail_sections,
)
from app.services.search import search_documents

PROPOSAL_HINTS = ("proposal", "quote", "offer", "方案", "报价", "提案")
_JSON_BLOCK = re.compile(r"\{.*\}", flags=re.S)
_CATEGORY_CACHE_TTL_SEC_DEFAULT = 300
_CATEGORY_CACHE: dict[str, Any] = {"expires_at": 0.0, "categories": []}
_ROUTER_ASSIST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    m = _JSON_BLOCK.search(raw)
    if not m:
        return {}
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _rt(config: dict[str, Any] | None) -> dict[str, Any]:
    return dict(((config or {}).get("configurable") or {}))


def _legacy_agent_module():
    from app.services import agent as legacy_agent

    return legacy_agent


def _planner_obj(state: AgentGraphState) -> PlannerDecision:
    return PlannerDecision(**dict(state.get("planner") or {}))


def _raw_req(rt: dict[str, Any]):
    return rt.get("raw_req")


def _db(rt: dict[str, Any]):
    return rt.get("db")


def _logger(rt: dict[str, Any]):
    return rt.get("logger")


def _clamp_loop_budget(value: Any) -> int:
    try:
        num = int(value)
    except Exception:
        num = 2
    return max(0, min(2, num))


def _settings(rt: dict[str, Any]):
    return rt.get("settings")


def _router_assist_enabled(rt: dict[str, Any]) -> bool:
    settings = _settings(rt)
    return bool(getattr(settings, "agent_graph_llm_router_assist_enabled", False))


def _router_assist_mode(rt: dict[str, Any]) -> str:
    settings = _settings(rt)
    mode = str(getattr(settings, "agent_graph_llm_router_assist_trigger_mode", "low_confidence") or "low_confidence").strip().lower()
    return mode if mode in {"off", "low_confidence", "always", "experiment_only"} else "low_confidence"


def _now_ts() -> float:
    return time.time()


def _get_distinct_categories(db, *, ttl_sec: int) -> list[str]:
    now = _now_ts()
    if now < float(_CATEGORY_CACHE.get("expires_at") or 0.0):
        return list(_CATEGORY_CACHE.get("categories") or [])
    rows = db.execute(
        select(Document.category_path)
        .where(Document.status == DocumentStatus.COMPLETED.value, Document.category_path.is_not(None))
        .distinct()
    ).all()
    cats = sorted({str((row[0] if isinstance(row, tuple) else row) or "").strip() for row in rows if str((row[0] if isinstance(row, tuple) else row) or "").strip()})
    _CATEGORY_CACHE["categories"] = cats
    _CATEGORY_CACHE["expires_at"] = now + max(30, int(ttl_sec or _CATEGORY_CACHE_TTL_SEC_DEFAULT))
    return cats


def _router_assist_cache_key(*, query: str, candidates: list[str], model: str, ui_lang: str) -> str:
    norm_query = " ".join(str(query or "").split()).strip().lower()
    cats_blob = "\n".join(sorted(str(c) for c in candidates))
    cats_hash = hashlib.sha1(cats_blob.encode("utf-8")).hexdigest()[:12]
    return f"{model}|{ui_lang}|{cats_hash}|{norm_query}"


def _router_assist_cache_get(key: str) -> dict[str, Any] | None:
    item = _ROUTER_ASSIST_CACHE.get(key)
    if not item:
        return None
    exp, value = item
    if _now_ts() >= float(exp):
        _ROUTER_ASSIST_CACHE.pop(key, None)
        return None
    return dict(value or {})


def _router_assist_cache_put(key: str, value: dict[str, Any], *, ttl_sec: int) -> None:
    _ROUTER_ASSIST_CACHE[key] = (_now_ts() + max(10, int(ttl_sec or 600)), dict(value or {}))
    if len(_ROUTER_ASSIST_CACHE) > 512:  # simple bounded cache
        for k in list(_ROUTER_ASSIST_CACHE.keys())[:64]:
            _ROUTER_ASSIST_CACHE.pop(k, None)


def _linux_default_gateway_ip() -> str:
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as fh:
            for line in fh.read().splitlines()[1:]:
                cols = line.split()
                if len(cols) < 3:
                    continue
                # Destination == 00000000 means default route.
                if cols[1] != "00000000":
                    continue
                gw_hex = cols[2]
                if len(gw_hex) != 8:
                    continue
                parts = [str(int(gw_hex[i : i + 2], 16)) for i in range(0, 8, 2)]
                return ".".join(reversed(parts))
    except Exception:
        return ""
    return ""


def _router_assist_candidate_chat_urls(settings) -> list[str]:
    base = str(getattr(settings, "ollama_base_url", "") or "").strip()
    if not base:
        return []
    urls: list[str] = [base.rstrip("/") + "/api/chat"]
    if "host.docker.internal" in base:
        try:
            socket.gethostbyname("host.docker.internal")
        except Exception:
            gw = _linux_default_gateway_ip()
            if gw:
                urls.append(base.replace("host.docker.internal", gw).rstrip("/") + "/api/chat")
    # dedupe while preserving order
    out: list[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def _router_assist_model_name(settings) -> str:
    model = str(getattr(settings, "agent_graph_llm_router_assist_model", "") or "").strip()
    if model:
        return model
    return str(getattr(settings, "planner_model", "") or "").strip()


def _router_assist_prompt(
    *,
    query: str,
    ui_lang: str,
    query_spec: dict[str, Any],
    confidence: dict[str, Any],
    candidate_categories: list[str],
) -> list[dict[str, str]]:
    compact_conf = {
        "score": float((confidence or {}).get("score") or 0.0),
        "ambiguity_flags": list((confidence or {}).get("ambiguity_flags") or [])[:4],
    }
    compact_spec = {
        "task_kind": str(query_spec.get("task_kind") or ""),
        "subject_domain": str(query_spec.get("subject_domain") or ""),
        "target_slots": list(query_spec.get("target_slots") or [])[:4],
        "preferred_categories_rule": list(query_spec.get("preferred_categories") or [])[:4],
        "subject_aliases": list(query_spec.get("subject_aliases") or [])[:6],
    }
    candidate_items = list(candidate_categories)[:12]
    return [
        {
            "role": "system",
            "content": (
                "/no_think\n"
                "Return JSON only. "
                "Task: choose retrieval categories for the query. "
                "Rules: select ONLY from candidate_categories; prefer specific sub-categories; do not answer the question. "
                "Output keys exactly: selected_categories(list), confidence(number), reason_tags(list), keep_rule_categories(bool)."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "q": query,
                    "ui_lang": ui_lang,
                    "spec": compact_spec,
                    "rule_confidence": compact_conf,
                    "candidate_categories": candidate_items,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _router_assist_llm_call(
    *,
    rt: dict[str, Any],
    query: str,
    ui_lang: str,
    query_spec: dict[str, Any],
    confidence: dict[str, Any],
    candidate_categories: list[str],
) -> tuple[dict[str, Any], int, bool, dict[str, Any]]:
    settings = _settings(rt)
    timeout_ms = int(getattr(settings, "agent_graph_llm_router_assist_timeout_ms", 1200) or 1200)
    cache_ttl = int(getattr(settings, "agent_graph_llm_router_assist_cache_ttl_sec", 600) or 600)
    key = _router_assist_cache_key(
        query=query,
        candidates=candidate_categories,
        model=_router_assist_model_name(settings),
        ui_lang=ui_lang,
    )
    cached = _router_assist_cache_get(key)
    if cached is not None:
        return (cached, 0, True, {"error_code": "", "raw_preview": "", "used_url": "", "used_url_fallback": False})

    payload = {
        "model": _router_assist_model_name(settings),
        "stream": False,
        "format": "json",
        "think": False,
        "messages": _router_assist_prompt(
            query=query,
            ui_lang=ui_lang,
            query_spec=query_spec,
            confidence=confidence,
            candidate_categories=candidate_categories,
        ),
        "options": {"temperature": 0.0, "num_predict": 160},
    }
    started = time.perf_counter()
    urls = _router_assist_candidate_chat_urls(settings)
    last_diag = {"error_code": "router_assist_no_url", "raw_preview": "", "used_url": "", "used_url_fallback": False}
    parsed: dict[str, Any] = {}
    for idx, url in enumerate(urls):
        try:
            resp = requests.post(url, json=payload, timeout=max(0.2, timeout_ms / 1000.0))
            resp.raise_for_status()
            body = resp.json() if hasattr(resp, "json") else {}
            text = str((body.get("message") or {}).get("content") or "")
            parsed = _extract_json_object(text)
            if parsed:
                last_diag = {
                    "error_code": "",
                    "raw_preview": "",
                    "used_url": url,
                    "used_url_fallback": bool(idx > 0),
                }
                break
            last_diag = {
                "error_code": "router_assist_parse_error",
                "raw_preview": str(text)[:180],
                "used_url": url,
                "used_url_fallback": bool(idx > 0),
            }
        except requests.Timeout:
            last_diag = {
                "error_code": "router_assist_timeout",
                "raw_preview": "",
                "used_url": url,
                "used_url_fallback": bool(idx > 0),
            }
        except requests.HTTPError as exc:
            last_diag = {
                "error_code": "router_assist_http_error",
                "raw_preview": str(exc)[:180],
                "used_url": url,
                "used_url_fallback": bool(idx > 0),
            }
        except requests.RequestException as exc:
            last_diag = {
                "error_code": "router_assist_request_error",
                "raw_preview": f"{type(exc).__name__}: {exc}"[:180],
                "used_url": url,
                "used_url_fallback": bool(idx > 0),
            }
        except Exception as exc:
            last_diag = {
                "error_code": "router_assist_unknown_error",
                "raw_preview": f"{type(exc).__name__}: {exc}"[:180],
                "used_url": url,
                "used_url_fallback": bool(idx > 0),
            }
    ms = int((time.perf_counter() - started) * 1000)
    if parsed:
        _router_assist_cache_put(key, parsed, ttl_sec=cache_ttl)
    return (parsed, ms, False, last_diag)


def _router_assist_should_trigger(*, query: str, spec: dict[str, Any], confidence: dict[str, Any], mode: str, threshold: float = 0.65) -> tuple[bool, str]:
    if mode in {"off", "experiment_only"}:
        return (False, "disabled")
    if mode == "always":
        return (True, "mode_always")
    text = str(query or "").lower()
    target_slots = [str(x or "").strip() for x in (spec.get("target_slots") or []) if str(x or "").strip()]
    preferred_categories = [str(x or "").strip() for x in (spec.get("preferred_categories") or []) if str(x or "").strip()]
    subject_domain = str(spec.get("subject_domain") or "generic")
    if not target_slots:
        return (True, "empty_target_slots")
    if any(slot in {"contact", "contact_info", "contact_method"} for slot in target_slots):
        return (True, "noncanonical_target_slots")
    if subject_domain == "generic" and any(tok in text for tok in ("账单", "保险", "家电", "宠物", "房贷", "网络", "宽带", "nbn")):
        return (True, "generic_domain_with_domain_cues")
    if not preferred_categories or (len(preferred_categories) == 1 and preferred_categories[0].count("/") < 2):
        return (True, "broad_or_empty_categories")
    if (
        any(tok in text for tok in ("提供商", "provider", "vendor", "运营商", "服务商"))
        and any(tok in text for tok in ("联系方式", "电话", "邮箱", "contact"))
    ):
        if float((confidence or {}).get("score") or 0.0) < max(float(threshold), 0.8):
            return (True, "provider_contact_high_value_pattern")
    if (
        any(tok in text for tok in ("最近", "latest", "recent"))
        and any(tok in text for tok in ("账单", "bill", "invoice"))
        and any(tok in text for tok in ("多少", "多少钱", "金额", "amount", "cost", "price"))
    ):
        if float((confidence or {}).get("score") or 0.0) < max(float(threshold), 0.8):
            return (True, "recent_bill_amount_high_value_pattern")
    score = float((confidence or {}).get("score") or 0.0)
    if score < float(threshold):
        return (True, "low_rule_confidence")
    return (False, "high_rule_confidence")


def _router_assist_apply_result(*, spec: dict[str, Any], raw_result: dict[str, Any], candidate_categories: list[str], confidence: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = dict(spec or {})
    out_spec = dict(spec)
    allowed = {str(c) for c in candidate_categories}
    raw_selected = [str(x or "").strip() for x in (raw_result.get("selected_categories") or []) if str(x or "").strip()]
    selected = [c for c in raw_selected if c in allowed]

    llm_conf = 0.0
    try:
        llm_conf = max(0.0, min(1.0, float(raw_result.get("confidence") or 0.0)))
    except Exception:
        llm_conf = 0.0
    raw_keep_rule = raw_result.get("keep_rule_categories", True)
    if isinstance(raw_keep_rule, bool):
        keep_rule = raw_keep_rule
    elif isinstance(raw_keep_rule, (int, float)):
        keep_rule = bool(raw_keep_rule)
    elif isinstance(raw_keep_rule, str):
        keep_rule = raw_keep_rule.strip().lower() not in {"false", "0", "no", "off", ""}
    else:
        keep_rule = True

    rule_categories = [str(x or "").strip() for x in (spec.get("preferred_categories") or []) if str(x or "").strip()]
    if not selected:
        # Recovery: model sometimes omits selected_categories but provides confidence/reason_tags.
        reason_tags = [str(x or "").strip().lower() for x in (raw_result.get("reason_tags") or []) if str(x or "").strip()]
        preferred = [str(x or "").strip() for x in (spec.get("preferred_categories") or []) if str(x or "").strip()]
        aliases = [str(x or "").lower() for x in (spec.get("subject_aliases") or []) if str(x or "").strip()]
        slots = {str(x or "") for x in (spec.get("target_slots") or [])}
        if llm_conf >= 0.55:
            if any("specific_sub_category" in tag or "billing" in tag for tag in reason_tags):
                if any(tok in " ".join(aliases) for tok in ("internet", "nbn", "superloop", "broadband")) or {"vendor", "contact_phone", "contact_email"} & slots:
                    selected = [c for c in candidate_categories if str(c).startswith("finance/bills/internet")][:1]
            if not selected and preferred:
                # Prefer the first specific rule category if LLM indicates confidence but omitted selection.
                selected = [c for c in preferred if c in allowed and c.count("/") >= 2][:1]
            if not selected:
                # Last resort: keep broadest valid rule category to make output non-empty.
                selected = [c for c in preferred if c in allowed][:1]

    if not selected:
        final_categories = list(rule_categories)
        fallback_used = True
    elif keep_rule:
        final_categories = list(dict.fromkeys([*selected[:2], *rule_categories]))[:6]
        fallback_used = False
    else:
        final_categories = selected[:2]
        fallback_used = False

    if final_categories:
        out_spec["preferred_categories"] = final_categories
        if llm_conf >= 0.85 and str(out_spec.get("subject_domain") or "generic") == "generic":
            if any(str(c).startswith("finance/bills") for c in final_categories):
                out_spec["subject_domain"] = "bills"
            elif any(str(c).startswith("home/appliances") for c in final_categories):
                out_spec["subject_domain"] = "appliances"
            elif any(str(c).startswith(("home/insurance", "health/insurance", "legal/insurance")) for c in final_categories):
                out_spec["subject_domain"] = "insurance"
            elif any(str(c).startswith("home/pets") for c in final_categories):
                out_spec["subject_domain"] = "pets"
            elif any(str(c).startswith(("home/property", "home/maintenance", "finance/mortgage", "finance/loans")) for c in final_categories):
                out_spec["subject_domain"] = "home"

    diag = {
        "triggered": True,
        "rule_confidence": float((confidence or {}).get("score") or 0.0),
        "llm_confidence": llm_conf,
        "llm_selected_categories": selected[:4],
        "keep_rule_categories": keep_rule,
        "fallback_used": fallback_used,
        "reason_tags": [str(x or "").strip() for x in (raw_result.get("reason_tags") or []) if str(x or "").strip()][:8],
    }
    out_spec["router_assist"] = diag
    return (out_spec, diag)


def _repair_query_spec_with_rules(*, query: str, planner_intent: str, planner_doc_scope: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    repaired = dict(spec or {})
    rule_spec = build_query_spec_from_query(query, planner_intent=planner_intent, doc_scope=planner_doc_scope or {})

    def _known_slot(slot: str) -> bool:
        slot = str(slot or "").strip()
        if not slot:
            return False
        if slot in {"presence_evidence", "status_evidence"}:
            return True
        return bool(slot_query_terms(slot))

    target_slots = [str(x or "").strip() for x in (repaired.get("target_slots") or []) if str(x or "").strip()]
    noncanonical = [slot for slot in target_slots if not _known_slot(slot)]
    if (not target_slots) or (target_slots and len(noncanonical) == len(target_slots)):
        repaired["target_slots"] = list(rule_spec.get("target_slots") or [])
    elif any(slot in {"contact", "contact_info", "contact_method"} for slot in target_slots) and any(
        str(x or "").startswith("contact_") for x in (rule_spec.get("target_slots") or [])
    ):
        # Replace generic contact pseudo-slot with canonical contact slots.
        repaired["target_slots"] = list(rule_spec.get("target_slots") or [])

    if str(repaired.get("subject_domain") or "generic") == "generic" and str(rule_spec.get("subject_domain") or "generic") != "generic":
        repaired["subject_domain"] = str(rule_spec.get("subject_domain") or "generic")

    if not list(repaired.get("subject_aliases") or []) and list(rule_spec.get("subject_aliases") or []):
        repaired["subject_aliases"] = list(rule_spec.get("subject_aliases") or [])

    if not list(repaired.get("preferred_categories") or []) and list(rule_spec.get("preferred_categories") or []):
        repaired["preferred_categories"] = list(rule_spec.get("preferred_categories") or [])

    # Developer/property/vendor queries: override incorrect bill categories to legal/contracts.
    # The LLM sometimes maps "开发商" (developer/vendor) queries to finance/bills which is
    # completely wrong; the relevant evidence is in legal/contracts (Vendor's Statement /
    # Residential Sale Contract).  We enable strict_domain_filter so Qdrant restricts the
    # semantic search to legal/contracts chunks only, ensuring the Vendor's Statement is
    # ranked and returned.  The legal/contracts category in Qdrant has 100+ points so
    # there is no risk of zero-hit.
    _developer_tokens = ("开发商", "developer", "vendor statement", "建商", "地产开发", "房产开发")
    _current_cats = [str(c or "").strip().rstrip("/") for c in list(repaired.get("preferred_categories") or [])]
    if (any(tok in query.lower() for tok in _developer_tokens)
            and _current_cats
            and all(c.startswith("finance") for c in _current_cats)):
        repaired["preferred_categories"] = ["legal/contracts"]
        repaired["strict_domain_filter"] = True

    # Health insurance extras queries asking for 额度/limits: add annual_limit to target_slots
    # so the slot extraction specifically looks for dollar amounts in the retrieved chunks.
    _health_limit_tokens = ("额度", "报销", "limit", "reimburse", "annual limit", "benefit")
    _health_ins_cats = [str(c or "").strip() for c in list(repaired.get("preferred_categories") or [])]
    if (str(repaired.get("subject_domain") or "") in {"insurance", "health"}
            and any(tok in query.lower() for tok in _health_limit_tokens)
            and any("health" in c or "insurance" in c for c in _health_ins_cats)):
        _existing_slots = list(repaired.get("target_slots") or [])
        for _slot in ("annual_limit", "benefit_amount", "coverage_limit"):
            if _slot not in _existing_slots:
                _existing_slots.append(_slot)
        repaired["target_slots"] = _existing_slots

    # Only promote strict_domain_filter when the effective preferred_categories are specific
    # subcategories (e.g. "finance/bills/internet"), not the broad parent "finance/bills".
    # Exact-match retrieval on "finance/bills" returns 0 because all bills are stored at
    # finance/bills/* — promoting strict on a broad parent would cause strict_domain_zero_hit.
    _override_cats = list(rule_spec.get("preferred_categories") or [])
    _effective_cats = _override_cats or list(repaired.get("preferred_categories") or [])
    _cats_are_specific = bool(_effective_cats) and not any(
        str(c or "").strip().rstrip("/") == "finance/bills" for c in _effective_cats
    )
    if not bool(repaired.get("strict_domain_filter")) and bool(rule_spec.get("strict_domain_filter")) and str(repaired.get("subject_domain") or "") == "bills" and _cats_are_specific:
        repaired["strict_domain_filter"] = bool(rule_spec.get("strict_domain_filter"))
        # When promoting strict_domain_filter, also replace broad LLM categories with
        # the rule-based specific subcategory (e.g. "finance/bills" → "finance/bills/internet"),
        # so _needs_llm_assist doesn't trigger on a still-broad preferred_categories.
        if _override_cats:
            repaired["preferred_categories"] = _override_cats

    # Safety: never apply strict_domain_filter with the broad parent "finance/bills" category.
    # All bills are stored at finance/bills/* subcategories; exact-match on the parent always
    # returns 0. This handles both LLM-generated strict=True and rule-override cases.
    if bool(repaired.get("strict_domain_filter")) and str(repaired.get("subject_domain") or "") == "bills":
        _final_cats = list(repaired.get("preferred_categories") or [])
        if not _final_cats or any(str(c or "").strip().rstrip("/") == "finance/bills" for c in _final_cats):
            repaired["strict_domain_filter"] = False

    # Q1-like bill scalar queries should not stay as "list" when rule-spec resolved scalar slots.
    if str(repaired.get("task_kind") or "") == "list" and str(rule_spec.get("task_kind") or "") == "fact_lookup":
        if "bill_amount" in list(repaired.get("target_slots") or []) or "bill_amount" in list(rule_spec.get("target_slots") or []):
            repaired["task_kind"] = "fact_lookup"

    # For bill aggregate monthly queries, override task_kind to "aggregate_lookup" so
    # route_node correctly delegates to structured_fastpath (bill_monthly_total DB path).
    # Runs AFTER Q1-like conversion so it catches list→fact_lookup cases too.
    # Applies when: bills domain, task_kind is fact_lookup or list (not yet promoted),
    # query has a specific month token, and no single bill type is specified.
    _bill_specific_tokens = ("电费", "燃气费", "燃气账单", "网费", "宽带", "水费",
                             "electricity", "gas bill", "internet bill", "water bill", "superloop")
    if (str(repaired.get("subject_domain") or "") == "bills"
            and str(repaired.get("task_kind") or "") in {"fact_lookup", "list"}
            and re.search(r"\d{1,2}月份?|\d{4}年?\d{1,2}月", query)
            and not any(tok in query for tok in _bill_specific_tokens)):
        repaired["task_kind"] = "aggregate_lookup"

    repaired.setdefault("version", str(rule_spec.get("version") or "v2"))
    repaired.setdefault("time_scope", dict(rule_spec.get("time_scope") or {}))
    repaired.setdefault("derivations", list(rule_spec.get("derivations") or []))
    repaired.setdefault("needs_presence_evidence", bool(rule_spec.get("needs_presence_evidence")))
    repaired.setdefault("needs_status_evidence", bool(rule_spec.get("needs_status_evidence")))
    return repaired


def planner_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    rt = _rt(config)
    req = _raw_req(rt)
    db = _db(rt)
    legacy_agent = _legacy_agent_module()

    started = time.perf_counter()
    if req.planner is None:
        from app.services.planner import plan_from_request

        planner = plan_from_request(
            PlannerRequest(
                query=req.query,
                ui_lang=req.ui_lang,
                query_lang=req.query_lang,
                doc_scope=req.doc_scope,
            )
        )
    else:
        planner = req.planner
        if (not planner.doc_scope) and isinstance(req.doc_scope, dict) and req.doc_scope:
            planner = PlannerDecision(
                intent=planner.intent,
                confidence=planner.confidence,
                doc_scope=req.doc_scope,
                actions=planner.actions,
                fallback=planner.fallback,
                ui_lang=planner.ui_lang,
                query_lang=planner.query_lang,
                route_reason=planner.route_reason,
                required_evidence_fields=list(planner.required_evidence_fields or []),
                refusal_candidate=bool(planner.refusal_candidate),
                task_kind=getattr(planner, "task_kind", "") or "",
                subject_domain=getattr(planner, "subject_domain", "") or "",
                target_slots=list(getattr(planner, "target_slots", []) or []),
                query_spec=dict(getattr(planner, "query_spec", {}) or {}),
                query_spec_version=getattr(planner, "query_spec_version", "") or "",
            )

    spec = dict(getattr(planner, "query_spec", {}) or {})
    if not spec:
        spec = build_query_spec_from_query(req.query, planner_intent=str(planner.intent or ""), doc_scope=planner.doc_scope or {})
    else:
        spec = _repair_query_spec_with_rules(
            query=req.query,
            planner_intent=str(planner.intent or ""),
            planner_doc_scope=dict(planner.doc_scope or {}),
            spec=spec,
        )

    # Post-spec guard: ensure bill aggregate monthly queries route to structured_fastpath.
    # Applied unconditionally here so it covers both the repair and the build-from-scratch
    # paths above. When the LLM assigns fact_lookup/list/search_bundle etc. to
    # "2月份的账单有哪些？" style queries, the route_node condition
    # `task_kind in {"aggregate_lookup","list"}` would miss them; overriding to
    # aggregate_lookup fixes that without touching other queries.
    # We use a blacklist (not in safe-to-keep set) rather than a whitelist to
    # catch any novel task_kind string the LLM might produce (e.g. "search_bundle").
    _bill_agg_specific_tokens = ("电费", "燃气费", "燃气账单", "网费", "宽带", "水费",
                                 "electricity", "gas bill", "internet bill", "water bill", "superloop")
    if (str(spec.get("subject_domain") or "") == "bills"
            and str(spec.get("task_kind") or "") not in {"aggregate_lookup", "queue", "mutate"}
            and re.search(r"\d{1,2}月份?|\d{4}年?\d{1,2}月", req.query)
            and not any(tok in req.query for tok in _bill_agg_specific_tokens)):
        spec["task_kind"] = "aggregate_lookup"

    router_conf = estimate_queryspec_confidence(req.query, spec)
    router_triggered = False
    router_reason = ""
    router_llm_conf = 0.0
    router_selected_categories: list[str] = []
    router_keep_rule_categories = False
    router_assist_latency_ms = 0
    router_assist_cache_hit = False
    router_assist_llm_calls = 0
    router_assist_error_code = ""
    router_assist_error_detail = ""
    router_assist_used_url_fallback = False
    mode = _router_assist_mode(rt)
    if _router_assist_enabled(rt) and db is not None and mode != "off":
        threshold = float(getattr(_settings(rt), "agent_graph_llm_router_assist_confidence_threshold", 0.65) or 0.65)
        should_trigger, router_reason = _router_assist_should_trigger(
            query=req.query, spec=spec, confidence=router_conf, mode=mode, threshold=threshold
        )
        if should_trigger:
            router_triggered = True
            settings = _settings(rt)
            max_candidates = int(getattr(settings, "agent_graph_llm_router_assist_max_categories", 12) or 12)
            ttl = int(getattr(settings, "agent_graph_llm_router_assist_cache_ttl_sec", 600) or 600)
            all_categories = _get_distinct_categories(db, ttl_sec=min(ttl, 300))
            candidates = prefilter_router_candidate_categories(spec, all_categories, max_candidates=max_candidates)
            if len(candidates) >= 2:
                raw_router_result, router_assist_latency_ms, router_assist_cache_hit, router_assist_diag = _router_assist_llm_call(
                    rt=rt,
                    query=req.query,
                    ui_lang=str(req.ui_lang or "zh"),
                    query_spec=spec,
                    confidence=router_conf,
                    candidate_categories=candidates,
                )
                router_assist_error_code = str(router_assist_diag.get("error_code") or "")
                router_assist_error_detail = str(router_assist_diag.get("raw_preview") or "")
                router_assist_used_url_fallback = bool(router_assist_diag.get("used_url_fallback"))
                if raw_router_result:
                    if not router_assist_cache_hit:
                        router_assist_llm_calls = 1
                    spec, diag = _router_assist_apply_result(
                        spec=spec,
                        raw_result=raw_router_result,
                        candidate_categories=candidates,
                        confidence=router_conf,
                    )
                    router_llm_conf = float(diag.get("llm_confidence") or 0.0)
                    router_selected_categories = list(diag.get("llm_selected_categories") or [])
                    router_keep_rule_categories = bool(diag.get("keep_rule_categories"))
                else:
                    spec["router_assist"] = {
                        "triggered": True,
                        "rule_confidence": float(router_conf.get("score") or 0.0),
                        "llm_confidence": 0.0,
                        "llm_selected_categories": [],
                        "keep_rule_categories": True,
                        "fallback_used": True,
                        "reason_tags": [router_assist_error_code or "llm_failed_or_timeout"],
                    }
            else:
                spec["router_assist"] = {
                    "triggered": True,
                    "rule_confidence": float(router_conf.get("score") or 0.0),
                    "llm_confidence": 0.0,
                    "llm_selected_categories": [],
                    "keep_rule_categories": True,
                    "fallback_used": True,
                    "reason_tags": ["insufficient_candidates"],
                }
        else:
            spec["router_assist"] = {
                "triggered": False,
                "rule_confidence": float(router_conf.get("score") or 0.0),
                "llm_confidence": 0.0,
                "llm_selected_categories": [],
                "keep_rule_categories": True,
                "fallback_used": False,
                "reason_tags": [router_reason],
            }
    else:
        router_reason = "disabled"
        spec["router_assist"] = {
            "triggered": False,
            "rule_confidence": float(router_conf.get("score") or 0.0),
            "llm_confidence": 0.0,
            "llm_selected_categories": [],
            "keep_rule_categories": True,
            "fallback_used": False,
            "reason_tags": [router_reason],
        }

    # Always backfill/normalize planner convenience fields from the final spec.
    planner_dict = apply_query_spec_to_planner_fields(spec, planner.model_dump())
    planner = PlannerDecision(**planner_dict)

    subtasks = build_subtasks_from_query_spec(spec)
    required_slots, critical_slots = required_slots_from_query_spec(spec)
    context_policy = legacy_agent._context_policy_for_query(req.query, client_context=(req.client_context if isinstance(req.client_context, dict) else {}))
    loop_budget = _clamp_loop_budget(rt.get("settings").agent_graph_loop_budget if rt.get("settings") else 2)
    planner_latency_ms = int((time.perf_counter() - started) * 1000)
    timing = {
        "planner_latency_ms": planner_latency_ms,
        "graph_router_assist_latency_ms": int(router_assist_latency_ms),
    }
    executor_stats_payload = {
        "graph_router_assist_triggered": bool(router_triggered),
        "graph_router_assist_reason": str(router_reason or ""),
        "graph_router_rule_confidence": float(router_conf.get("score") or 0.0),
        "graph_router_llm_confidence": float(router_llm_conf),
        "graph_router_selected_categories": list(router_selected_categories),
        "graph_router_kept_rule_categories": bool(router_keep_rule_categories),
        "graph_router_assist_latency_ms": int(router_assist_latency_ms),
        "graph_router_assist_cache_hit": bool(router_assist_cache_hit),
        "graph_router_assist_error_code": str(router_assist_error_code),
        "graph_router_assist_error_detail": str(router_assist_error_detail),
        "graph_router_assist_used_url_fallback": bool(router_assist_used_url_fallback),
        "graph_llm_calls_planner": 1 + int(router_assist_llm_calls),
        "graph_llm_calls_synth": 0,
        "graph_llm_calls_total": 1 + int(router_assist_llm_calls),
    }

    return {
        "req": {
            "query": req.query,
            "ui_lang": req.ui_lang,
            "query_lang": req.query_lang,
            "doc_scope": req.doc_scope or {},
            "client_context": req.client_context or {},
            "conversation": req.conversation or [],
        },
        "planner": planner.model_dump(),
        "query_spec": spec,
        "subtasks": subtasks,
        "required_slots": required_slots,
        "critical_slots": critical_slots,
        "trace_id": f"agtg-{uuid.uuid4().hex[:12]}",
        "context_policy": context_policy,
        "loop_budget": loop_budget,
        "loop_count": 0,
        "loop_progress_history": [],
        "timing": timing,
        "executor_stats_payload": executor_stats_payload,
        "terminal": False,
        "terminal_reason": "",
    }


def route_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = dict(state.get("query_spec") or {})
    planner = dict(state.get("planner") or {})
    task_kind = str(spec.get("task_kind") or planner.get("task_kind") or "fact_lookup")
    subject_domain = str(spec.get("subject_domain") or planner.get("subject_domain") or "generic")
    intent = str(planner.get("intent") or "")

    route = "query_retrieval"
    reason = "graph_query_retrieval"
    if task_kind in {"queue", "mutate"} or intent in {"queue_view", "reprocess_doc", "tag_update"}:
        route = "structured_fastpath"
        reason = "graph_structured_queue_mutate"
    elif subject_domain == "bills" and task_kind in {"aggregate_lookup", "list"}:
        route = "structured_fastpath"
        reason = "graph_structured_bills"
    elif task_kind in {"summarize", "compare", "timeline"}:
        route = "query_retrieval"
        reason = "graph_slot_pipeline_summary_compare_timeline"
    elif task_kind in {"fact_lookup", "status_check", "howto_lookup", "detail_extract", "aggregate_lookup", "list"}:
        route = "query_retrieval"
        reason = "graph_slot_pipeline"
    return {"route": route, "route_reason": reason}


def route_decision(state: AgentGraphState) -> str:
    return str(state.get("route") or "query_retrieval")


def structured_fastpath_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    rt = _rt(config)
    req = _raw_req(rt)
    db = _db(rt)
    logger = _logger(rt)
    legacy_agent = _legacy_agent_module()

    started = time.perf_counter()
    req_delegate = req
    planner_injected = False
    planner_for_delegate = _planner_obj(state)
    try:
        if hasattr(req, "model_copy"):
            req_delegate = req.model_copy(update={"planner": planner_for_delegate})
            planner_injected = True
        elif hasattr(req, "copy"):
            req_delegate = req.copy(deep=True)  # type: ignore[attr-defined]
            setattr(req_delegate, "planner", planner_for_delegate)
            planner_injected = True
    except Exception as exc:  # pragma: no cover - defensive runtime path
        if logger is not None:
            try:
                logger.warning("graph_delegate_planner_injection_failed", extra={"detail": str(exc), "exc_type": type(exc).__name__})
            except Exception:
                pass
    bundle = legacy_agent._execute_plan(db, req_delegate, planner_for_delegate)
    route_name = str(bundle.get("route") or "")
    card = legacy_agent._synthesize_fallback(req_delegate, planner_for_delegate, bundle)
    ms = int((time.perf_counter() - started) * 1000)
    planner_stats = dict(state.get("executor_stats_payload") or {})
    trace_id = str(state.get("trace_id") or f"agtg-{uuid.uuid4().hex[:12]}")
    hit_count = int(bundle.get("hit_count") or 0)
    answerability = str(bundle.get("answerability") or ("sufficient" if hit_count > 0 else "none"))
    answer_mode = "structured" if route_name in {"bill_attention", "bill_monthly_total", "period_aggregate", "queue_snapshot", "reprocess_exec", "tag_update_exec"} else "search_summary"
    stats_kwargs: dict[str, Any] = {
        "hit_count": hit_count,
        "doc_count": int(bundle.get("doc_count") or 0),
        "used_chunk_count": len(bundle.get("context_chunks") or []),
        "route": route_name,
        "bilingual_search": bool(bundle.get("bilingual_search")),
        "qdrant_used": bool(bundle.get("qdrant_used")),
        "retrieval_mode": str(bundle.get("retrieval_mode") or "structured"),
        "vector_hit_count": int(bundle.get("vector_hit_count") or 0),
        "lexical_hit_count": int(bundle.get("lexical_hit_count") or 0),
        "fallback_reason": str(bundle.get("fallback_reason") or ""),
        "facet_mode": str(bundle.get("facet_mode") or "none"),
        "facet_keys": [str(x) for x in (bundle.get("facet_keys") or []) if str(x or "").strip()],
        "context_policy": str(state.get("context_policy") or "graph"),
        "fact_route": str(bundle.get("fact_route") or "none"),
        "fact_month": str(bundle.get("fact_month") or ""),
        "detail_topic": str(bundle.get("detail_topic") or ""),
        "detail_mode": str(bundle.get("detail_mode") or "structured"),
        "detail_rows_count": int(bundle.get("detail_rows_count") or 0),
        "answerability": answerability,
        "coverage_ratio": float(bundle.get("coverage_ratio") or 0.0),
        "field_coverage_ratio": float(bundle.get("field_coverage_ratio") or 0.0),
        "coverage_missing_fields": [str(x) for x in (bundle.get("coverage_missing_fields") or []) if str(x or "").strip()],
        "query_required_terms": [str(x) for x in (bundle.get("query_required_terms") or []) if str(x or "").strip()],
        "subject_anchor_terms": [str(x) for x in (bundle.get("subject_anchor_terms") or []) if str(x or "").strip()],
        "subject_coverage_ok": bool(bundle.get("subject_coverage_ok", True)),
        "target_field_terms": [str(x) for x in (bundle.get("target_field_terms") or []) if str(x or "").strip()],
        "target_field_coverage_ok": bool(bundle.get("target_field_coverage_ok", True)),
        "infra_guard_applied": bool(bundle.get("infra_guard_applied", False)),
        "locale_response_mode": "zh_native" if str((card.short_summary.zh if card and card.short_summary else "") or "").strip() else "bilingual_fallback",
        "answer_mode": answer_mode,
        "evidence_backed_doc_count": int(len(bundle.get("evidence_backed_doc_ids") or [])),
        "related_doc_selection_mode": str(bundle.get("related_doc_selection_mode") or "evidence_only"),
        "subject_entity": str(bundle.get("subject_entity") or ""),
        "route_reason": str(bundle.get("route_reason") or state.get("route_reason") or ""),
        "answer_posture": "direct" if hit_count > 0 else "refusal",
        "evidence_link_quality": "slot_evidence_first" if hit_count > 0 else "context_only",
        "graph_enabled": True,
        "graph_path": "planner->route->structured_fastpath->finalize",
        "graph_loop_budget": int(state.get("loop_budget") or 0),
        "graph_loops_used": 0,
        "graph_terminal_reason": "structured_fastpath_native",
        "graph_planner_reused_in_delegate": bool(planner_injected),
        "graph_llm_calls_planner": int(planner_stats.get("graph_llm_calls_planner") or 1),
        "graph_llm_calls_synth": 0,
        "graph_llm_calls_total": int(planner_stats.get("graph_llm_calls_planner") or 1),
        "planner_latency_ms": int((state.get("timing") or {}).get("planner_latency_ms") or 0),
        "executor_latency_ms": ms,
        "synth_latency_ms": 0,
    }
    for key in (
        "answer_posture",
        "force_refusal_reason",
        "slot_fallback_used",
        "slot_evidence_doc_count",
        "evidence_link_quality",
        "partial_evidence_signals",
        "refusal_blockers",
        "graph_router_assist_triggered",
        "graph_router_assist_reason",
        "graph_router_rule_confidence",
        "graph_router_llm_confidence",
        "graph_router_selected_categories",
        "graph_router_kept_rule_categories",
        "graph_router_assist_latency_ms",
        "graph_router_assist_cache_hit",
        "graph_router_assist_error_code",
        "graph_router_assist_error_detail",
        "graph_router_assist_used_url_fallback",
    ):
        if key in planner_stats:
            stats_kwargs[key] = planner_stats.get(key)
    resp = AgentExecuteResponse(
        planner=planner_for_delegate,
        card=card,
        related_docs=bundle.get("related_docs") or [],
        trace_id=trace_id,
        executor_stats=AgentExecutorStats(**stats_kwargs),
    )
    timing = dict(state.get("timing") or {})
    timing["graph_structured_fastpath_latency_ms"] = ms
    timing["graph_planner_reused_in_delegate"] = bool(planner_injected)
    return {"response": resp, "terminal": True, "terminal_reason": "structured_fastpath_native", "timing": timing}


def query_variant_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    req = dict(state.get("req") or {})
    spec = dict(state.get("query_spec") or {})
    loop_count = int(state.get("loop_count") or 0)
    query = str(req.get("query") or "").strip()
    out: list[str] = []

    def _add(item: str) -> None:
        item = " ".join(str(item or "").split()).strip()
        if item and item not in out:
            out.append(item)

    _add(query)

    aliases = [str(x or "").strip() for x in (spec.get("subject_aliases") or []) if str(x or "").strip()]
    if aliases:
        _add(" ".join(aliases[:4]))
        for alias in aliases[: min(4 + loop_count, 6)]:
            _add(alias)

    slots = [str(x or "").strip() for x in (spec.get("target_slots") or []) if str(x or "").strip()]
    slot_terms: list[str] = []
    for slot in slots[:6]:
        slot_terms.extend(slot_query_terms(slot)[:4])
    for term in slot_terms[: min(4 + loop_count * 2, 10)]:
        _add(f"{query} {term}" if len(term) < 18 else term)

    return {"query_variants": out[:8]}


def retrieve_candidates_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    rt = _rt(config)
    db = _db(rt)
    req = dict(state.get("req") or {})
    planner = dict(state.get("planner") or {})
    spec = dict(state.get("query_spec") or {})
    loop_count = int(state.get("loop_count") or 0)
    variants = [str(x or "").strip() for x in (state.get("query_variants") or []) if str(x or "").strip()]
    if not variants:
        variants = [str(req.get("query") or "")]

    top_k = 12 if loop_count <= 0 else 20
    legacy_agent = _legacy_agent_module()
    planner_obj = _planner_obj(state)
    doc_ids = legacy_agent._doc_ids_from_scope(dict(planner_obj.doc_scope or {}), client_context=(req.get("client_context") or {}))
    allowed_doc_ids = {str(x) for x in doc_ids if str(x or "").strip()}
    preferred_categories = [str(x or "").strip() for x in (spec.get("preferred_categories") or []) if str(x or "").strip()]
    strict_domain_filter = bool(spec.get("strict_domain_filter"))
    category_path = preferred_categories[0] if strict_domain_filter and len(preferred_categories) == 1 else None

    by_chunk: dict[str, dict[str, Any]] = {}
    candidate_doc_ids: set[str] = set()
    vector_hit_count = 0
    lexical_hit_count = 0
    qdrant_used = False
    retrieval_modes: set[str] = set()
    search_calls = 0

    for variant in variants:
        if not variant:
            continue
        search_calls += 1
        variant_query_lang = "en"
        if any("\u4e00" <= ch <= "\u9fff" for ch in variant):
            variant_query_lang = str(planner.get("query_lang") or req.get("query_lang") or "auto")
        sreq = SearchRequest(
            query=variant,
            top_k=top_k,
            score_threshold=0.0,
            ui_lang=str(planner.get("ui_lang") or req.get("ui_lang") or "zh"),
            query_lang=variant_query_lang,
            category_path=category_path,
            include_missing=False,
        )
        sres = search_documents(db, sreq)
        qdrant_used = qdrant_used or bool(getattr(sres, "qdrant_used", False))
        vector_hit_count += int(getattr(sres, "vector_hit_count", 0) or 0)
        lexical_hit_count += int(getattr(sres, "lexical_hit_count", 0) or 0)
        retrieval_modes.add(str(getattr(sres, "retrieval_mode", "none") or "none"))
        for hit in list(getattr(sres, "hits", []) or []):
            doc_id = str(getattr(hit, "doc_id", "") or "").strip()
            if allowed_doc_ids and doc_id not in allowed_doc_ids:
                continue
            cid = str(getattr(hit, "chunk_id", "") or "").strip()
            if not cid:
                continue
            payload = {
                "doc_id": doc_id,
                "chunk_id": cid,
                "score": float(getattr(hit, "score", 0.0) or 0.0),
                "title_en": str(getattr(hit, "title_en", "") or ""),
                "title_zh": str(getattr(hit, "title_zh", "") or ""),
                "category_path": str(getattr(hit, "category_path", "") or ""),
                "text_snippet": str(getattr(hit, "text_snippet", "") or ""),
                "matched_query": str(getattr(hit, "matched_query", "") or variant),
                "tags": list(getattr(hit, "tags", []) or []),
            }
            current = by_chunk.get(cid)
            if current is None or payload["score"] > float(current.get("score") or 0.0):
                by_chunk[cid] = payload
            if doc_id:
                candidate_doc_ids.add(doc_id)

    docs: list[dict[str, Any]] = []
    if candidate_doc_ids:
        rows = (
            db.execute(select(Document).where(Document.id.in_(candidate_doc_ids), Document.status == DocumentStatus.COMPLETED.value))
            .scalars()
            .all()
        )
        for doc in rows:
            docs.append(
                {
                    "doc_id": str(doc.id),
                    "file_name": str(doc.file_name or ""),
                    "title_en": str(doc.title_en or ""),
                    "title_zh": str(doc.title_zh or ""),
                    "summary_en": str(doc.summary_en or ""),
                    "summary_zh": str(doc.summary_zh or ""),
                    "category_path": str(doc.category_path or ""),
                    "updated_at": doc.updated_at.isoformat() if getattr(doc, "updated_at", None) else "",
                    "source_available": bool(crud.source_path_available(doc.source_path)),
                }
            )

    hits = sorted(by_chunk.values(), key=lambda x: float(x.get("score") or 0.0), reverse=True)
    timing = dict(state.get("timing") or {})
    timing["graph_search_calls"] = int(timing.get("graph_search_calls") or 0) + int(search_calls)
    return {
        "candidate_hits": hits[: max(24, top_k * 2)],
        "candidate_docs": docs,
        "timing": timing,
        "executor_stats_payload": {
            **dict(state.get("executor_stats_payload") or {}),
            "qdrant_used": bool(qdrant_used),
            "vector_hit_count": int(vector_hit_count),
            "lexical_hit_count": int(lexical_hit_count),
            "retrieval_mode": "+".join(sorted(x for x in retrieval_modes if x and x != "none")) or (next(iter(retrieval_modes)) if retrieval_modes else "none"),
        },
    }


def rerank_candidates_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = dict(state.get("query_spec") or {})
    hits = list(state.get("candidate_hits") or [])
    docs = list(state.get("candidate_docs") or [])
    loop_count = int(state.get("loop_count") or 0)

    doc_map = {str(item.get("doc_id") or ""): item for item in docs}
    by_doc: dict[str, dict[str, Any]] = {}
    subject_aliases = [str(x or "").lower() for x in (spec.get("subject_aliases") or []) if str(x or "").strip()]
    target_slots = [str(x or "").strip() for x in (spec.get("target_slots") or []) if str(x or "").strip()]
    slot_terms: list[str] = []
    for slot in target_slots:
        slot_terms.extend(slot_query_terms(slot))
    preferred_categories = [str(x or "").strip().lower() for x in (spec.get("preferred_categories") or []) if str(x or "").strip()]
    historical_query = bool(spec.get("needs_status_evidence")) or bool(spec.get("needs_presence_evidence"))

    for hit in hits:
        doc_id = str(hit.get("doc_id") or "")
        if not doc_id:
            continue
        doc = doc_map.get(doc_id, {})
        text_blob = " ".join(
            [
                str(hit.get("title_zh") or ""),
                str(hit.get("title_en") or ""),
                str(hit.get("category_path") or ""),
                str(hit.get("text_snippet") or ""),
                str(doc.get("file_name") or ""),
                str(doc.get("summary_zh") or ""),
                str(doc.get("summary_en") or ""),
            ]
        ).lower()
        score = float(hit.get("score") or 0.0)
        if any(alias in text_blob for alias in subject_aliases):
            score += 0.18
        if any(term.lower() in text_blob for term in slot_terms[:12]):
            score += 0.12
        cp = str(hit.get("category_path") or "").lower()
        if preferred_categories and any(cp.startswith(path) for path in preferred_categories):
            score += 0.14
        if any(tok in text_blob for tok in PROPOSAL_HINTS):
            score -= 0.25 if historical_query else 0.15
        if loop_count >= 1 and preferred_categories and not any(cp.startswith(path) for path in preferred_categories):
            # After recovery #1 we allow non-preferred docs but keep them ranked lower.
            score -= 0.05
        row = by_doc.get(doc_id)
        if row is None:
            by_doc[doc_id] = {
                "doc_id": doc_id,
                "score": score,
                "category_path": str(doc.get("category_path") or hit.get("category_path") or ""),
                "title_en": str(doc.get("title_en") or hit.get("title_en") or ""),
                "title_zh": str(doc.get("title_zh") or hit.get("title_zh") or ""),
                "file_name": str(doc.get("file_name") or ""),
                "hit_chunk_ids": [str(hit.get("chunk_id") or "")],
                "raw_hits": [hit],
            }
        else:
            row["score"] = max(float(row.get("score") or 0.0), score)
            cid = str(hit.get("chunk_id") or "")
            if cid and cid not in row["hit_chunk_ids"]:
                row["hit_chunk_ids"].append(cid)
            row["raw_hits"].append(hit)

    ranked = sorted(by_doc.values(), key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return {"ranked_docs": ranked}


def expand_context_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    rt = _rt(config)
    db = _db(rt)
    spec = dict(state.get("query_spec") or {})
    loop_count = int(state.get("loop_count") or 0)
    ranked_docs = list(state.get("ranked_docs") or [])
    max_docs = 5 if loop_count <= 0 else (7 if loop_count == 1 else 9)
    neighbor_radius = 1 if loop_count <= 1 else 2
    max_chunks = int((rt.get("settings").agent_graph_max_context_chunks_recovery if loop_count >= 1 else rt.get("settings").agent_graph_max_context_chunks) or (16 if loop_count >= 1 else 12))
    max_chunks = max(4, min(24, max_chunks))

    slot_terms: list[str] = []
    for slot in [str(x or "").strip() for x in (spec.get("target_slots") or []) if str(x or "").strip()]:
        slot_terms.extend(slot_query_terms(slot))
    slot_terms = [x.lower() for x in slot_terms if x]

    doc_ids = [str(item.get("doc_id") or "") for item in ranked_docs[:max_docs] if str(item.get("doc_id") or "")]
    docs = (
        db.execute(select(Document).where(Document.id.in_(set(doc_ids)), Document.status == DocumentStatus.COMPLETED.value)).scalars().all()
        if doc_ids
        else []
    )
    doc_map = {str(doc.id): doc for doc in docs}

    context_chunks: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()

    for doc_row in ranked_docs[:max_docs]:
        doc_id = str(doc_row.get("doc_id") or "")
        doc = doc_map.get(doc_id)
        if doc is None or not crud.source_path_available(doc.source_path):
            continue
        seed_ids = [str(x or "") for x in doc_row.get("hit_chunk_ids") or [] if str(x or "")]
        if not seed_ids:
            continue
        seed_chunks = db.execute(select(Chunk).where(Chunk.id.in_(set(seed_ids)))).scalars().all()
        seed_indices = sorted({int(getattr(row, "chunk_index", 0) or 0) for row in seed_chunks})
        target_indices: set[int] = set()
        for idx in seed_indices[:6]:
            for off in range(-neighbor_radius, neighbor_radius + 1):
                target_indices.add(max(0, idx + off))
        rows = (
            db.execute(
                select(Chunk)
                .where(Chunk.document_id == doc_id, Chunk.chunk_index.in_(target_indices))
                .order_by(Chunk.chunk_index.asc())
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.id in seen_chunk_ids:
                continue
            text = str(row.content or "")
            score = float(doc_row.get("score") or 0.0)
            if slot_terms and any(term in text.lower() for term in slot_terms):
                score += 0.05
            context_chunks.append(
                {
                    "doc_id": doc_id,
                    "chunk_id": str(row.id),
                    "chunk_index": int(row.chunk_index or 0),
                    "score": score,
                    "title_en": str(getattr(doc, "title_en", "") or ""),
                    "title_zh": str(getattr(doc, "title_zh", "") or ""),
                    "category_path": str(getattr(doc, "category_path", "") or ""),
                    "text": text,
                }
            )
            seen_chunk_ids.add(row.id)
            if len(context_chunks) >= max_chunks:
                break
        if len(context_chunks) >= max_chunks:
            break

    # Recovery #2 regex-only pass on top docs if still sparse.
    if loop_count >= 2 and len(context_chunks) < max_chunks and ranked_docs:
        for doc_row in ranked_docs[:max_docs]:
            if len(context_chunks) >= max_chunks:
                break
            doc_id = str(doc_row.get("doc_id") or "")
            doc = doc_map.get(doc_id)
            if doc is None:
                continue
            rows = (
                db.execute(select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.chunk_index.asc()).limit(12))
                .scalars()
                .all()
            )
            for row in rows:
                if row.id in seen_chunk_ids:
                    continue
                text = str(row.content or "")
                lowered = text.lower()
                if slot_terms and not any(term in lowered for term in slot_terms):
                    continue
                context_chunks.append(
                    {
                        "doc_id": doc_id,
                        "chunk_id": str(row.id),
                        "chunk_index": int(row.chunk_index or 0),
                        "score": float(doc_row.get("score") or 0.0),
                        "title_en": str(getattr(doc, "title_en", "") or ""),
                        "title_zh": str(getattr(doc, "title_zh", "") or ""),
                        "category_path": str(getattr(doc, "category_path", "") or ""),
                        "text": text,
                    }
                )
                seen_chunk_ids.add(row.id)
                if len(context_chunks) >= max_chunks:
                    break

    context_chunks.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return {"context_chunks": context_chunks[:max_chunks]}


def extract_slots_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = dict(state.get("query_spec") or {})
    chunks = list(state.get("context_chunks") or [])
    subtasks = list(state.get("subtasks") or [])
    loop_count = int(state.get("loop_count") or 0)

    slot_results = extract_slots_from_chunks(query_spec=spec, context_chunks=chunks, subtasks=subtasks, include_generic_fallback=True)
    # Recovery #2: generic second pass already represented by extractor; annotate via state only.
    extractor_mode = "domain_plus_generic" if loop_count >= 2 else "domain_generic"
    return {"slot_results": slot_results, "executor_stats_payload": {**dict(state.get("executor_stats_payload") or {}), "detail_mode": extractor_mode}}


def derive_facts_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = dict(state.get("query_spec") or {})
    rows = list(state.get("slot_results") or [])
    derivs = derive_facts(slot_results=rows, derivations=[str(x or "") for x in (spec.get("derivations") or [])])
    # Derived values are appended as synthetic slots to improve coverage if slot matches known target.
    augmented = list(rows)
    for item in derivs:
        if str(item.get("status") or "") != "derived":
            continue
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        if name == "compare_expiry_to_next_month":
            augmented.append(
                {
                    "slot": "status_evidence",
                    "label_en": "Status Evidence",
                    "label_zh": "状态证据",
                    "value": value,
                    "normalized_value": value,
                    "status": "derived",
                    "confidence": 0.6,
                    "evidence_refs": [],
                    "source_doc_ids": [],
                }
            )
        elif name == "compute_remaining_loan_years":
            augmented.append(
                {
                    "slot": "loan_term_years",
                    "label_en": "Loan Term Years",
                    "label_zh": "贷款年限",
                    "value": value,
                    "normalized_value": value,
                    "status": "derived",
                    "confidence": 0.55,
                    "evidence_refs": [],
                    "source_doc_ids": [],
                }
            )
        elif name == "estimate_next_vaccine_due":
            augmented.append(
                {
                    "slot": "vaccine_next_due",
                    "label_en": "Next Vaccine Due",
                    "label_zh": "下次补打日期",
                    "value": value,
                    "normalized_value": value,
                    "status": "derived",
                    "confidence": 0.6,
                    "evidence_refs": [],
                    "source_doc_ids": [],
                }
            )
    return {"derivations": derivs, "slot_results": augmented}


def sufficiency_judge_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = dict(state.get("query_spec") or {})
    slot_results = list(state.get("slot_results") or [])
    derivations = list(state.get("derivations") or [])
    context_chunks = list(state.get("context_chunks") or [])
    required_slots = [str(x or "") for x in (state.get("required_slots") or []) if str(x or "").strip()]
    critical_slots = [str(x or "") for x in (state.get("critical_slots") or []) if str(x or "").strip()]

    judged = judge_sufficiency(
        query_spec=spec,
        slot_results=slot_results,
        derivations=derivations,
        context_chunks=context_chunks,
        required_slots=required_slots,
        critical_slots=critical_slots,
    )

    history = list(state.get("loop_progress_history") or [])
    history.append(
        {
            "loop_count": int(state.get("loop_count") or 0),
            "slot_coverage_ratio": float(judged.get("slot_coverage_ratio") or 0.0),
            "critical_slot_coverage_ratio": float(judged.get("critical_slot_coverage_ratio") or 0.0),
            "answerability": str(judged.get("answerability") or "insufficient"),
            "hit_count": len(context_chunks),
        }
    )
    return {**judged, "loop_progress_history": history}


def recovery_plan_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    if state.get("response") is not None:
        return {"terminal": True, "terminal_reason": str(state.get("terminal_reason") or "response_ready")}

    answerability = str(state.get("answerability") or "insufficient")
    loop_count = int(state.get("loop_count") or 0)
    loop_budget = int(state.get("loop_budget") or 0)
    critical_missing = [str(x or "") for x in (state.get("critical_missing_slots") or []) if str(x or "").strip()]
    strict_domain = bool((state.get("query_spec") or {}).get("strict_domain_filter"))
    candidate_hits = list(state.get("candidate_hits") or [])
    history = list(state.get("loop_progress_history") or [])

    if answerability == "sufficient":
        return {"terminal": True, "terminal_reason": "sufficient"}
    if answerability == "partial" and not critical_missing:
        return {"terminal": True, "terminal_reason": "partial_critical_complete"}
    # Fix 1C: after at least one recovery attempt, accept a partial result
    # rather than exhausting the loop budget.  Avoids loop_budget_exhausted
    # for questions where the critical slots genuinely cannot be extracted
    # from any chunk (non-canonical slot names, multi-entity aggregates, etc.)
    if answerability == "partial" and loop_count >= 1:
        return {"terminal": True, "terminal_reason": "partial_after_recovery"}
    if loop_count >= loop_budget:
        return {"terminal": True, "terminal_reason": "loop_budget_exhausted"}
    if strict_domain and not candidate_hits:
        return {"terminal": True, "terminal_reason": "strict_domain_zero_hit"}

    if len(history) >= 3:
        last3 = history[-3:]
        covs = [float(item.get("slot_coverage_ratio") or 0.0) for item in last3]
        if covs[-1] <= covs[-2] <= covs[-3]:
            return {"terminal": True, "terminal_reason": "no_progress_two_recoveries"}

    next_loop = loop_count + 1
    if next_loop == 1:
        actions = ["expand_query_variants", "relax_non_bill_filter", "increase_candidate_limit", "neighbor_expand_1"]
    else:
        actions = ["dual_extractor", "relax_subject_anchor", "neighbor_expand_2", "regex_only_second_scan"]
    return {
        "terminal": False,
        "terminal_reason": "",
        "recovery_plan": {"next_loop": next_loop, "actions": actions},
    }


def recovery_decision(state: AgentGraphState) -> str:
    return "answer" if bool(state.get("terminal")) else "recover"


def recovery_apply_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = dict(state.get("recovery_plan") or {})
    loop_count = int(state.get("loop_count") or 0)
    next_loop = int(plan.get("next_loop") or (loop_count + 1))
    next_loop = max(loop_count + 1, min(2, next_loop))

    history = list(state.get("loop_progress_history") or [])
    if history:
        history[-1] = {**history[-1], "recovery_actions": list(plan.get("actions") or [])}
    return {"loop_count": next_loop, "loop_progress_history": history}


def _graph_has_slot_values(slot_results: list[dict[str, Any]]) -> bool:
    return any(str(item.get("status") or "") in {"found", "derived"} and str(item.get("value") or "").strip() for item in (slot_results or []))


def _graph_has_derived_values(derivations: list[dict[str, Any]]) -> bool:
    return any(str(item.get("status") or "") == "derived" and str(item.get("value") or "").strip() for item in (derivations or []))


def _resolve_answer_posture(
    *,
    answerability: str,
    query_spec: dict[str, Any],
    critical_missing: list[str],
    hit_count: int,
    slot_results: list[dict[str, Any]],
    derivations: list[dict[str, Any]],
    subject_coverage_ok: bool,
    target_field_coverage_ok: bool,
) -> tuple[str, str]:
    has_slot_values = _graph_has_slot_values(slot_results)
    has_derived_values = _graph_has_derived_values(derivations)
    needs_presence = bool(query_spec.get("needs_presence_evidence"))
    needs_status = bool(query_spec.get("needs_status_evidence"))
    missing_presence_or_status = any(slot in {"presence_evidence", "status_evidence"} for slot in critical_missing)

    if answerability == "sufficient":
        if (needs_presence or needs_status) and missing_presence_or_status:
            if hit_count > 0 or has_slot_values or has_derived_values or subject_coverage_ok or target_field_coverage_ok:
                return ("partial", "presence_or_status_gate_partial_only")
            return ("refusal", "missing_presence_or_status_evidence")
        return ("direct", "")

    if answerability == "none":
        return ("refusal", "answerability_none")

    if (needs_presence or needs_status) and missing_presence_or_status:
        if hit_count > 0 or has_slot_values or has_derived_values or subject_coverage_ok or target_field_coverage_ok:
            return ("partial", "presence_or_status_gate_partial_only")
        return ("refusal", "missing_presence_or_status_evidence")

    if answerability in {"partial", "insufficient"} and (
        (hit_count > 0 and subject_coverage_ok) or has_slot_values or has_derived_values or target_field_coverage_ok
    ):
        if not (has_slot_values or has_derived_values):
            return ("evidence_only", "indirect_evidence_only")
        return ("partial", "")

    if hit_count <= 0 and not (has_slot_values or has_derived_values):
        return ("refusal", "zero_hit_no_slot_evidence")

    return ("refusal", "insufficient_without_actionable_evidence")


def _graph_snippet(text: str, cap: int = 72) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    return raw[: cap - 1] + "…" if len(raw) > cap else raw


def _collect_graph_evidence_refs(
    *,
    slot_results: list[dict[str, Any]],
    detail_sections: list[DetailSection],
    derivations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(ev_doc_id: Any, ev_chunk_id: Any, ev_text: Any) -> None:
        doc_id = str(ev_doc_id or "").strip()
        chunk_id = str(ev_chunk_id or "").strip()
        evidence_text = str(ev_text or "").strip()
        if not doc_id and not chunk_id:
            return
        key = (doc_id, chunk_id, evidence_text[:80])
        if key in seen:
            return
        seen.add(key)
        out.append({"doc_id": doc_id, "chunk_id": chunk_id, "evidence_text": evidence_text})

    for item in slot_results:
        for ev in list(item.get("evidence_refs") or []):
            _add((ev or {}).get("doc_id"), (ev or {}).get("chunk_id"), (ev or {}).get("evidence_text"))
    for section in detail_sections:
        for row in list(getattr(section, "rows", []) or []):
            for ev in list(getattr(row, "evidence_refs", []) or []):
                if hasattr(ev, "doc_id"):
                    _add(getattr(ev, "doc_id", ""), getattr(ev, "chunk_id", ""), getattr(ev, "evidence_text", ""))
                else:
                    _add((ev or {}).get("doc_id"), (ev or {}).get("chunk_id"), (ev or {}).get("evidence_text"))
    for item in derivations:
        for ev in list(item.get("evidence_refs") or []):
            _add((ev or {}).get("doc_id"), (ev or {}).get("chunk_id"), (ev or {}).get("evidence_text"))
    return out


def _build_graph_evidence_outputs(
    *,
    db,
    legacy_agent,
    slot_results: list[dict[str, Any]],
    detail_sections: list[DetailSection],
    derivations: list[dict[str, Any]],
    context_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    ctx_by_chunk = {str(item.get("chunk_id") or ""): item for item in (context_chunks or []) if str(item.get("chunk_id") or "").strip()}
    ctx_by_doc: dict[str, dict[str, Any]] = {}
    for item in (context_chunks or []):
        doc_id = str(item.get("doc_id") or "").strip()
        if doc_id and doc_id not in ctx_by_doc:
            ctx_by_doc[doc_id] = item

    evidence_refs = _collect_graph_evidence_refs(slot_results=slot_results, detail_sections=detail_sections, derivations=derivations)
    slot_sources: list[ResultCardSource] = []
    slot_evidence_doc_ids: list[str] = []
    seen_source_keys: set[tuple[str, str]] = set()
    for ev in evidence_refs:
        doc_id = str(ev.get("doc_id") or "").strip()
        chunk_id = str(ev.get("chunk_id") or "").strip()
        if doc_id and doc_id not in slot_evidence_doc_ids:
            slot_evidence_doc_ids.append(doc_id)
        ctx = ctx_by_chunk.get(chunk_id) or ctx_by_doc.get(doc_id) or {}
        label = str(ctx.get("title_zh") or ctx.get("title_en") or ev.get("evidence_text") or "Document")
        label = _graph_snippet(label, cap=64) or "Document"
        key = (doc_id, chunk_id)
        if key in seen_source_keys:
            continue
        seen_source_keys.add(key)
        slot_sources.append(ResultCardSource(doc_id=doc_id, chunk_id=chunk_id, label=label))

    seen_doc_ids: list[str] = []
    for doc_id in slot_evidence_doc_ids:
        if doc_id and doc_id not in seen_doc_ids:
            seen_doc_ids.append(doc_id)
    for item in slot_results:
        for doc_id in list(item.get("source_doc_ids") or []):
            s = str(doc_id or "").strip()
            if s and s not in seen_doc_ids:
                seen_doc_ids.append(s)
    for item in context_chunks[:10]:
        s = str(item.get("doc_id") or "").strip()
        if s and s not in seen_doc_ids:
            seen_doc_ids.append(s)

    related_docs = legacy_agent._build_related_docs(db, seen_doc_ids, cap=6)

    sources = list(slot_sources[:5])
    if len(sources) < 5:
        for item in context_chunks[:8]:
            doc_id = str(item.get("doc_id") or "")
            chunk_id = str(item.get("chunk_id") or "")
            key = (doc_id, chunk_id)
            if key in seen_source_keys:
                continue
            seen_source_keys.add(key)
            sources.append(
                ResultCardSource(
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    label=str(item.get("title_zh") or item.get("title_en") or "Document"),
                )
            )
            if len(sources) >= 5:
                break

    has_slot_doc_only = any(str(doc_id or "").strip() for item in slot_results for doc_id in list(item.get("source_doc_ids") or []))
    if slot_sources:
        evidence_link_quality = "slot_evidence_first"
        related_doc_selection_mode = "slot_evidence_first" if len(sources) <= len(slot_sources[:5]) else "slot_evidence_plus_context"
    elif has_slot_doc_only:
        evidence_link_quality = "mixed"
        related_doc_selection_mode = "slot_evidence_plus_context"
    else:
        evidence_link_quality = "context_only"
        related_doc_selection_mode = "context_only"

    return {
        "evidence_refs": evidence_refs,
        "slot_evidence_doc_ids": slot_evidence_doc_ids,
        "seen_doc_ids": seen_doc_ids,
        "related_docs": related_docs,
        "sources": sources,
        "related_doc_selection_mode": related_doc_selection_mode,
        "evidence_link_quality": evidence_link_quality,
    }


def _build_graph_slot_fallback_card(
    *,
    req,
    planner: PlannerDecision,
    query_spec: dict[str, Any],
    slot_results: list[dict[str, Any]],
    derivations: list[dict[str, Any]],
    detail_sections: list[DetailSection],
    context_chunks: list[dict[str, Any]],
    sources: list[ResultCardSource],
    missing_slots: list[str],
    critical_missing: list[str],
    cov_stats: DetailCoverageStats,
    answer_posture: str,
) -> ResultCard:
    target_slots = [str(x or "").strip() for x in (query_spec.get("target_slots") or []) if str(x or "").strip()]
    ordered_slots = list(dict.fromkeys(target_slots + [str(item.get("slot") or "") for item in slot_results]))
    by_slot = {}
    for item in slot_results:
        slot = str(item.get("slot") or "")
        if slot and slot not in by_slot and str(item.get("status") or "") in {"found", "derived"} and str(item.get("value") or "").strip():
            by_slot[slot] = item

    confirmed_zh: list[str] = []
    confirmed_en: list[str] = []
    for slot in ordered_slots[:12]:
        item = by_slot.get(slot)
        if not item:
            continue
        label_zh = str(item.get("label_zh") or slot)
        label_en = str(item.get("label_en") or slot.replace("_", " "))
        value = str(item.get("value") or "").strip()
        confirmed_zh.append(f"{label_zh}：{value}")
        confirmed_en.append(f"{label_en}: {value}")
        if len(confirmed_zh) >= 6:
            break

    for item in derivations:
        if str(item.get("status") or "") != "derived" or not str(item.get("value") or "").strip():
            continue
        name = str(item.get("name") or "derived")
        value = str(item.get("value") or "")
        line_zh = f"{name}：{value}"
        line_en = f"{name}: {value}"
        if line_zh not in confirmed_zh:
            confirmed_zh.append(line_zh)
            confirmed_en.append(line_en)
        if len(confirmed_zh) >= 8:
            break

    indirect_zh: list[str] = []
    indirect_en: list[str] = []
    for chunk in context_chunks[:3]:
        title_zh = str(chunk.get("title_zh") or chunk.get("title_en") or "相关文档")
        title_en = str(chunk.get("title_en") or chunk.get("title_zh") or "Related document")
        snippet_zh = _graph_snippet(str(chunk.get("text") or ""), cap=80)
        snippet_en = _graph_snippet(str(chunk.get("text") or ""), cap=110)
        if snippet_zh:
            indirect_zh.append(f"{title_zh}：{snippet_zh}")
        if snippet_en:
            indirect_en.append(f"{title_en}: {snippet_en}")

    key_points: list[BilingualText] = []
    if confirmed_zh:
        key_points.append(
            BilingualText(
                en=f"Confirmed: {'; '.join(confirmed_en[:4])}",
                zh=f"已确认：{'；'.join(confirmed_zh[:4])}",
            )
        )
    if indirect_zh:
        key_points.append(
            BilingualText(
                en=f"Indirect evidence: {'; '.join(indirect_en[:2])}",
                zh=f"可能相关/间接证据：{'；'.join(indirect_zh[:2])}",
            )
        )
    if missing_slots or critical_missing:
        miss = list(dict.fromkeys([*critical_missing, *missing_slots]))[:6]
        key_points.append(
            BilingualText(
                en=f"Still missing evidence for: {', '.join(miss)}",
                zh=f"尚缺证据：{', '.join(miss)}",
            )
        )
    if answer_posture in {"partial", "evidence_only"}:
        key_points.append(
            BilingualText(
                en="Use this as a partial answer only. More specific documents or keywords can improve accuracy.",
                zh="当前仅能给出部分答案；如需精确结论，请补充更具体文档或关键词。",
            )
        )
    elif not key_points:
        key_points.append(BilingualText(en="Relevant evidence was found.", zh="已找到相关证据。"))

    if answer_posture == "direct":
        short_en = "I found enough evidence to answer this question directly."
        short_zh = f"已根据资料确认答案。{'；'.join(confirmed_zh[:2])}" if confirmed_zh else "已根据资料确认答案。"
    else:
        short_en = "Based on the current documents, I can confirm part of the answer, but the evidence is incomplete."
        short_zh = "根据现有资料，已确认部分信息；以下结论存在不完整证据，请谨慎使用。"

    evidence_summary = []
    for slot in target_slots[:8]:
        evidence_summary.append(f"{slot}:{1 if slot in by_slot else 0}")

    return ResultCard(
        title="Structured Evidence Result",
        short_summary=BilingualText(en=short_en, zh=short_zh),
        key_points=key_points[:6],
        sources=sources[:5],
        actions=_legacy_agent_module()._default_actions(planner),
        detail_sections=detail_sections,
        missing_fields=list(missing_slots),
        coverage_stats=cov_stats,
        evidence_summary=evidence_summary,
        insufficient_evidence=False,
    )


def answer_build_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    rt = _rt(config)
    db = _db(rt)
    req = _raw_req(rt)
    legacy_agent = _legacy_agent_module()
    planner = _planner_obj(state)
    context_chunks = list(state.get("context_chunks") or [])
    slot_results = list(state.get("slot_results") or [])
    derivations = list(state.get("derivations") or [])
    answerability = str(state.get("answerability") or "insufficient")
    query_spec = dict(state.get("query_spec") or {})
    missing_slots = [str(x or "") for x in (state.get("coverage_missing_slots") or []) if str(x or "").strip()]
    critical_missing = [str(x or "") for x in (state.get("critical_missing_slots") or []) if str(x or "").strip()]
    partial_evidence_signals = [str(x or "") for x in (state.get("partial_evidence_signals") or []) if str(x or "").strip()]
    refusal_blockers = [str(x or "") for x in (state.get("refusal_blockers") or []) if str(x or "").strip()]
    hit_count = len(context_chunks)
    subject_coverage_ok = bool(state.get("subject_coverage_ok", True))
    target_field_coverage_ok = bool(state.get("target_field_coverage_ok", True))

    detail_section_dicts = slot_results_to_detail_sections(query_spec=query_spec, slot_results=slot_results)
    detail_sections: list[DetailSection] = []
    for section in detail_section_dicts:
        rows: list[DetailRow] = []
        for row in list(section.get("rows") or []):
            evs: list[DetailEvidenceRef] = []
            for ev in list(row.get("evidence_refs") or [])[:2]:
                evs.append(
                    DetailEvidenceRef(
                        doc_id=str(ev.get("doc_id") or ""),
                        chunk_id=str(ev.get("chunk_id") or ""),
                        evidence_text=str(ev.get("evidence_text") or "")[:180],
                    )
                )
            rows.append(
                DetailRow(
                    field=str(row.get("field") or ""),
                    label_en=str(row.get("label_en") or ""),
                    label_zh=str(row.get("label_zh") or ""),
                    value_en=str(row.get("value_en") or ""),
                    value_zh=str(row.get("value_zh") or ""),
                    evidence_refs=evs,
                )
            )
        if rows:
            detail_sections.append(DetailSection(section_name=str(section.get("section_name") or "slot_results"), rows=rows))

    evidence_outputs = _build_graph_evidence_outputs(
        db=db,
        legacy_agent=legacy_agent,
        slot_results=slot_results,
        detail_sections=detail_sections,
        derivations=derivations,
        context_chunks=context_chunks,
    )
    seen_doc_ids = list(evidence_outputs.get("seen_doc_ids") or [])
    related_docs = list(evidence_outputs.get("related_docs") or [])
    sources = list(evidence_outputs.get("sources") or [])
    slot_evidence_doc_ids = [str(x or "") for x in (evidence_outputs.get("slot_evidence_doc_ids") or []) if str(x or "").strip()]
    related_doc_selection_mode = str(evidence_outputs.get("related_doc_selection_mode") or "context_only")
    evidence_link_quality = str(evidence_outputs.get("evidence_link_quality") or "context_only")

    cov_stats = DetailCoverageStats(
        docs_scanned=max(len({str(x.get('doc_id') or '') for x in (state.get('candidate_hits') or []) if str(x.get('doc_id') or '')}), len({str(x.get('doc_id') or '') for x in context_chunks if str(x.get('doc_id') or '')})),
        docs_matched=len({str(x.get('doc_id') or '') for x in context_chunks if str(x.get('doc_id') or '')}),
        fields_filled=sum(1 for row in slot_results if str(row.get("status") or "") in {"found", "derived"} and str(row.get("value") or "").strip()),
    )

    synth_conversation = legacy_agent._normalize_conversation_messages(req, context_policy=str(state.get("context_policy") or "fresh_turn"))
    bundle = {
        "route": "entity_fact_lookup" if str(query_spec.get("task_kind") or "") in {"fact_lookup", "status_check", "howto_lookup", "detail_extract"} else "search_bundle",
        "route_reason": str(state.get("route_reason") or "graph_slot_pipeline"),
        "context_chunks": context_chunks,
        "sources": sources,
        "related_docs": related_docs,
        "hit_count": hit_count,
        "doc_count": len({str(item.get('doc_id') or '') for item in context_chunks if str(item.get('doc_id') or '')}),
        "bilingual_search": False,
        "qdrant_used": bool((state.get("executor_stats_payload") or {}).get("qdrant_used", False)),
        "retrieval_mode": str((state.get("executor_stats_payload") or {}).get("retrieval_mode") or "graph"),
        "vector_hit_count": int((state.get("executor_stats_payload") or {}).get("vector_hit_count") or 0),
        "lexical_hit_count": int((state.get("executor_stats_payload") or {}).get("lexical_hit_count") or 0),
        "fallback_reason": "",
        "detail_topic": str(query_spec.get("subject_domain") or "generic"),
        "detail_mode": "graph_slots",
        "detail_rows_count": len(slot_results),
        "detail_sections": detail_sections,
        "missing_fields": list(missing_slots),
        "coverage_stats": cov_stats,
        "answerability": answerability,
        "required_evidence_fields": [],
        "coverage_ratio": float(state.get("slot_coverage_ratio") or 0.0),
        "field_coverage_ratio": float(state.get("critical_slot_coverage_ratio") or 0.0),
        "coverage_missing_fields": list(missing_slots),
        "evidence_map": {},
        "refusal_candidate": bool(query_spec.get("needs_presence_evidence") or query_spec.get("needs_status_evidence")),
        "query_variants": list(state.get("query_variants") or []),
        "required_slots": list(state.get("required_slots") or []),
        "critical_missing_slots": list(critical_missing),
        "slot_coverage_ratio": float(state.get("slot_coverage_ratio") or 0.0),
        "critical_slot_coverage_ratio": float(state.get("critical_slot_coverage_ratio") or 0.0),
        "derivations": derivations,
        "subject_coverage_ok": subject_coverage_ok,
        "target_field_coverage_ok": target_field_coverage_ok,
        "related_doc_selection_mode": related_doc_selection_mode,
        "slot_results": slot_results,
        "answer_posture": "",
        "partial_evidence_signals": partial_evidence_signals,
        "refusal_blockers": refusal_blockers,
    }

    answer_posture, force_refusal_reason = _resolve_answer_posture(
        answerability=answerability,
        query_spec=query_spec,
        critical_missing=critical_missing,
        hit_count=hit_count,
        slot_results=slot_results,
        derivations=derivations,
        subject_coverage_ok=subject_coverage_ok,
        target_field_coverage_ok=target_field_coverage_ok,
    )
    bundle["answer_posture"] = answer_posture
    force_refusal = answer_posture == "refusal"
    card: ResultCard | None = None
    synth_error_code = ""
    synth_latency_ms = 0
    synth_fallback_used = False
    slot_fallback_used = False
    answer_mode = (
        "structured"
        if answer_posture == "direct"
        else ("partial_structured" if answer_posture == "partial" else ("evidence_only_structured" if answer_posture == "evidence_only" else "refusal"))
    )

    if not force_refusal:
        synth_started = time.perf_counter()
        card, synth_error_code = legacy_agent._synthesize_with_model(
            req,
            planner,
            bundle,
            trace_id=str(state.get("trace_id") or ""),
            conversation=synth_conversation,
        )
        synth_latency_ms = int((time.perf_counter() - synth_started) * 1000)
        if card is None:
            synth_fallback_used = True
            if _graph_has_slot_values(slot_results) or detail_sections or (answer_posture in {"partial", "evidence_only"} and hit_count > 0):
                slot_fallback_used = True
                card = _build_graph_slot_fallback_card(
                    req=req,
                    planner=planner,
                    query_spec=query_spec,
                    slot_results=slot_results,
                    derivations=derivations,
                    detail_sections=detail_sections,
                    context_chunks=context_chunks,
                    sources=sources,
                    missing_slots=missing_slots,
                    critical_missing=critical_missing,
                    cov_stats=cov_stats,
                    answer_posture=answer_posture,
                )
            else:
                card = legacy_agent._synthesize_fallback(req, planner, bundle)
            if not list(getattr(card, "detail_sections", []) or []):
                card.detail_sections = detail_sections
            if not list(getattr(card, "missing_fields", []) or []):
                card.missing_fields = list(missing_slots)
            card.coverage_stats = cov_stats
    if force_refusal:
        synth_fallback_used = True
        synth_error_code = synth_error_code or "insufficient_evidence"
        card = ResultCard(
            title="Insufficient Evidence",
            short_summary=BilingualText(
                en="Not enough evidence was found in the knowledge base to answer this question safely.",
                zh="资料中没有相关信息，且缺少足够证据，暂时无法确认。",
            ),
            key_points=[
                BilingualText(
                    en="Please provide more specific documents or keywords.",
                    zh="请补充更具体的文档或关键词后重试。",
                ),
                BilingualText(
                    en=f"Missing slots: {', '.join(missing_slots) if missing_slots else 'n/a'}",
                    zh=f"缺失字段：{', '.join(missing_slots) if missing_slots else '无'}",
                ),
            ],
            sources=sources,
            actions=legacy_agent._default_actions(planner),
            detail_sections=detail_sections,
            missing_fields=list(missing_slots),
            coverage_stats=cov_stats,
            evidence_summary=[f"{slot}:0" for slot in critical_missing[:8]] + [f"blocker:{x}" for x in refusal_blockers[:4]],
            insufficient_evidence=True,
        )
    else:
        card.insufficient_evidence = False
        card.evidence_summary = [f"{slot}:{1 if slot not in missing_slots else 0}" for slot in list(state.get("required_slots") or [])[:8]]
        if answer_posture in {"partial", "evidence_only"} and evidence_link_quality == "context_only":
            exists = any((kp.zh or "").startswith("当前答案主要基于相关文档片段") for kp in list(card.key_points or []))
            if not exists:
                card.key_points = list(card.key_points or []) + [
                    BilingualText(
                        en="This answer is mainly based on related document snippets and lacks field-level evidence binding.",
                        zh="当前答案主要基于相关文档片段，缺少字段级证据绑定。",
                    )
                ]

    timings = dict(state.get("timing") or {})
    timings["synth_latency_ms"] = int(synth_latency_ms)
    total_ms = int((time.perf_counter() - float(rt.get("started_at") or time.perf_counter())) * 1000)
    timings["total_latency_ms"] = total_ms

    recovery_actions_applied: list[str] = []
    for item in list(state.get("loop_progress_history") or []):
        for action in list(item.get("recovery_actions") or []):
            act = str(action or "").strip()
            if act and act not in recovery_actions_applied:
                recovery_actions_applied.append(act)

    prev_stats = dict(state.get("executor_stats_payload") or {})
    planner_llm_calls = int(prev_stats.get("graph_llm_calls_planner") or 1)
    synth_llm_calls = 0 if force_refusal else 1
    executor_stats_payload = {
        "hit_count": hit_count,
        "doc_count": len({str(item.get('doc_id') or '') for item in context_chunks if str(item.get('doc_id') or '')}),
        "used_chunk_count": len(context_chunks),
        "route": str(bundle.get("route") or "entity_fact_lookup"),
        "bilingual_search": False,
        "qdrant_used": bool(bundle.get("qdrant_used")),
        "retrieval_mode": str(bundle.get("retrieval_mode") or "graph"),
        "vector_hit_count": int(bundle.get("vector_hit_count") or 0),
        "lexical_hit_count": int(bundle.get("lexical_hit_count") or 0),
        "fallback_reason": str(bundle.get("fallback_reason") or ""),
        "facet_mode": "none",
        "facet_keys": [],
        "context_policy": str(state.get("context_policy") or "fresh_turn"),
        "fact_route": "none",
        "fact_month": "",
        "synth_fallback_used": bool(synth_fallback_used),
        "synth_error_code": str(synth_error_code or ""),
        "detail_topic": str(query_spec.get("subject_domain") or ""),
        "detail_mode": "graph_slots",
        "detail_rows_count": len(slot_results),
        "answerability": answerability,
        "coverage_ratio": float(state.get("slot_coverage_ratio") or 0.0),
        "field_coverage_ratio": float(state.get("critical_slot_coverage_ratio") or 0.0),
        "coverage_missing_fields": list(missing_slots),
        "query_required_terms": [],
        "subject_anchor_terms": list(query_spec.get("subject_aliases") or [])[:8],
        "subject_coverage_ok": subject_coverage_ok,
        "target_field_terms": list(query_spec.get("target_slots") or [])[:8],
        "target_field_coverage_ok": target_field_coverage_ok,
        "infra_guard_applied": False,
        "locale_response_mode": "zh_native" if req.ui_lang == "zh" and str(card.short_summary.zh or "").strip() else ("en_native" if str(card.short_summary.en or "").strip() else "bilingual_fallback"),
        "answer_mode": answer_mode,
        "evidence_backed_doc_count": len(slot_evidence_doc_ids) or len(seen_doc_ids),
        "related_doc_selection_mode": related_doc_selection_mode,
        "subject_entity": str(query_spec.get("subject_domain") or ""),
        "route_reason": str(state.get("route_reason") or "graph_slot_pipeline"),
        "graph_enabled": True,
        "graph_path": "planner->route->query_variant->retrieve->rerank->expand->extract->derive->judge->recovery->answer",
        "graph_loop_budget": int(state.get("loop_budget") or 0),
        "graph_loops_used": int(state.get("loop_count") or 0),
        "graph_terminal_reason": str(state.get("terminal_reason") or ""),
        "required_slots": list(state.get("required_slots") or []),
        "critical_missing_slots": list(critical_missing),
        "slot_coverage_ratio": float(state.get("slot_coverage_ratio") or 0.0),
        "critical_slot_coverage_ratio": float(state.get("critical_slot_coverage_ratio") or 0.0),
        "query_variants": list(state.get("query_variants") or []),
        "recovery_actions_applied": recovery_actions_applied,
        "answer_posture": answer_posture,
        "force_refusal_reason": force_refusal_reason,
        "slot_fallback_used": bool(slot_fallback_used),
        "slot_evidence_doc_count": len(slot_evidence_doc_ids),
        "evidence_link_quality": evidence_link_quality,
        "partial_evidence_signals": partial_evidence_signals,
        "refusal_blockers": refusal_blockers,
        "graph_planner_reused_in_delegate": False,
        "graph_llm_calls_planner": planner_llm_calls,
        "graph_llm_calls_synth": synth_llm_calls,
        "graph_llm_calls_total": planner_llm_calls + synth_llm_calls,
        "graph_router_assist_triggered": bool(prev_stats.get("graph_router_assist_triggered")),
        "graph_router_assist_reason": str(prev_stats.get("graph_router_assist_reason") or ""),
        "graph_router_rule_confidence": float(prev_stats.get("graph_router_rule_confidence") or 0.0),
        "graph_router_llm_confidence": float(prev_stats.get("graph_router_llm_confidence") or 0.0),
        "graph_router_selected_categories": list(prev_stats.get("graph_router_selected_categories") or []),
        "graph_router_kept_rule_categories": bool(prev_stats.get("graph_router_kept_rule_categories")),
        "graph_router_assist_latency_ms": int(prev_stats.get("graph_router_assist_latency_ms") or 0),
        "graph_router_assist_cache_hit": bool(prev_stats.get("graph_router_assist_cache_hit")),
        "graph_router_assist_error_code": str(prev_stats.get("graph_router_assist_error_code") or ""),
        "graph_router_assist_error_detail": str(prev_stats.get("graph_router_assist_error_detail") or ""),
        "graph_router_assist_used_url_fallback": bool(prev_stats.get("graph_router_assist_used_url_fallback")),
    }

    related_docs_payload = [doc.model_dump() if hasattr(doc, "model_dump") else doc for doc in related_docs]
    return {
        "final_card_payload": card.model_dump(),
        "executor_stats_payload": executor_stats_payload,
        "related_docs_payload": related_docs_payload,
        "timing": timings,
        "answer_posture": answer_posture,
        "force_refusal_reason": force_refusal_reason,
    }


def response_finalize_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(state.get("response"), AgentExecuteResponse):
        return {"response": state.get("response")}

    planner = PlannerDecision(**dict(state.get("planner") or {}))
    card = ResultCard(**dict(state.get("final_card_payload") or {}))
    related_docs = [AgentRelatedDoc(**item) if not isinstance(item, AgentRelatedDoc) else item for item in (state.get("related_docs_payload") or [])]
    stats = AgentExecutorStats(**dict(state.get("executor_stats_payload") or {}))
    resp = AgentExecuteResponse(
        planner=planner,
        card=card,
        related_docs=related_docs,
        trace_id=str(state.get("trace_id") or ""),
        executor_stats=stats,
    )
    return {"response": resp}
