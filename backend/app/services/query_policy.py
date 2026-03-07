import datetime as dt
import re
from typing import Any

from app.config import get_settings
from app.schemas import AgentExecuteRequest
from app.services.agent_constants import (
    _BILL_QUERY_HINTS,
    _DOMAIN_CATEGORY_WHITELISTS,
    _DOMAIN_HINTS,
    _EN_MONTH_MAP,
    _EN_MONTH_RE,
    _FACET_CONTACT,
    _FACET_ELECTRICITY_BILL,
    _FACET_ENERGY_BILL,
    _FACET_GAS_BILL,
    _FACET_NETWORK_BILL,
    _FACET_PROPERTY,
    _FACET_WATER_BILL,
    _FOLLOWUP_QUERY_HINTS,
    _HISTORICAL_FACT_QUERY_HINTS,
    _PROPOSAL_DOC_HINTS,
    _QUERY_QUALIFIER_HINTS,
    _SUBJECT_ANCHOR_HINTS,
    QueryFacet,
)

settings = get_settings()


def _safe_text(value: Any, *, cap: int = 280) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "..."


def _has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    return any(token in lowered for token in tokens)


def _is_followup_query(query: str) -> bool:
    text = str(query or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in _FOLLOWUP_QUERY_HINTS)


def _context_policy_for_query(
    query: str, *, client_context: dict[str, Any] | None = None
) -> str:
    mode = str(settings.agent_context_mode or "smart_followup").strip().lower()
    if mode in {"off", "disabled", "fresh_only"}:
        return "fresh_turn"
    if mode in {"followup", "always_followup", "always"}:
        return "followup_turn"
    if isinstance(client_context, dict):
        forced = str(client_context.get("context_policy") or "").strip().lower()
        if forced in {"fresh_turn", "followup_turn"}:
            return forced
    return "followup_turn" if _is_followup_query(query) else "fresh_turn"


def _normalize_conversation_messages(
    req: AgentExecuteRequest, *, context_policy: str
) -> list[dict[str, str]]:
    if context_policy != "followup_turn":
        return []
    if not isinstance(req.conversation, list):
        return []

    max_turns = max(1, int(settings.agent_conversation_max_turns or 2))
    max_rows = max_turns * 2
    rows = req.conversation[-max_rows:]
    out: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = _safe_text(item.get("content"), cap=360)
        if role in {"user", "assistant"} and content:
            out.append({"role": role, "content": content})
    return out


def _extract_month_scope(query: str) -> tuple[int | None, int | None]:
    text = str(query or "").strip().lower()
    if not text:
        return (None, None)

    zh = re.search(r"(?:(20\d{2})\s*年)?\s*(1[0-2]|0?[1-9])\s*月", text)
    if zh:
        year = int(zh.group(1)) if zh.group(1) else None
        month = int(zh.group(2))
        return (year, month)

    iso = re.search(r"(20\d{2})[/-](1[0-2]|0?[1-9])", text)
    if iso:
        return (int(iso.group(1)), int(iso.group(2)))

    en = _EN_MONTH_RE.search(text)
    if en:
        if en.group(1):  # "January [2026]" form
            month = _EN_MONTH_MAP[en.group(1)]
            year = int(en.group(2)) if en.group(2) else None
        else:  # "2026 January" form
            month = _EN_MONTH_MAP[en.group(4)]
            year = int(en.group(3))
        return (year, month)

    # Relative month references — resolve against today
    _today = dt.date.today()
    if any(t in text for t in ("上个月", "上月", "last month", "previous month")):
        if _today.month == 1:
            return (_today.year - 1, 12)
        return (_today.year, _today.month - 1)
    if any(
        t in text for t in ("这个月", "本月", "当月", "this month", "current month")
    ):
        return (_today.year, _today.month)

    return (None, None)


def _is_bill_monthly_total_query(query: str) -> bool:
    text = str(query or "").strip().lower()
    if not text:
        return False
    _year, month = _extract_month_scope(text)
    if month is None:
        return False
    if not any(token in text for token in _BILL_QUERY_HINTS):
        return False
    # Any specific-month + bill-keyword query is a monthly-total query.
    # Explicit total/sum language ("合计", "how much") is not required.
    return True


def _detect_query_facet(query: str) -> QueryFacet:
    lowered = str(query or "").lower()
    facet = QueryFacet()

    if _has_any_token(lowered, _FACET_ENERGY_BILL):
        facet.facet_keys.append("energy_bill")
        facet.strict_categories = ["finance/bills/electricity", "finance/bills/gas"]
        facet.required_terms = ["energy", "electricity", "power", "gas", "电费", "燃气"]
        facet.strict_mode = True
        return facet

    if _has_any_token(lowered, _FACET_NETWORK_BILL):
        facet.facet_keys.append("network_bill")
        facet.strict_categories = ["finance/bills/internet"]
        facet.required_terms = [
            "internet",
            "network",
            "nbn",
            "宽带",
            "网络",
            "superloop",
        ]
        facet.strict_mode = True
        return facet

    if _has_any_token(lowered, _FACET_ELECTRICITY_BILL):
        facet.facet_keys.append("electricity_bill")
        facet.strict_categories = ["finance/bills/electricity"]
        facet.required_terms = ["electricity", "power", "energy", "电费", "用电"]
        facet.strict_mode = True
        return facet

    if _has_any_token(lowered, _FACET_WATER_BILL):
        facet.facet_keys.append("water_bill")
        facet.strict_categories = ["finance/bills/water"]
        facet.required_terms = ["water", "水费", "用水"]
        facet.strict_mode = True
        return facet

    if _has_any_token(lowered, _FACET_GAS_BILL):
        facet.facet_keys.append("gas_bill")
        facet.strict_categories = ["finance/bills/gas"]
        facet.required_terms = ["gas", "燃气", "天然气"]
        facet.strict_mode = True
        return facet

    has_property = _has_any_token(lowered, _FACET_PROPERTY)
    has_contact = _has_any_token(lowered, _FACET_CONTACT)
    if has_property and has_contact:
        facet.facet_keys.append("property_contact")
        facet.strict_categories = [
            "home/maintenance",
            "home/property",
            "legal/property",
            "finance/bills/other",
        ]
        facet.required_terms = [
            "contact",
            "phone",
            "email",
            "联系方式",
            "电话",
            "邮箱",
            "物业",
            "property",
        ]
        facet.strict_mode = True
        return facet

    return facet


def _domain_category_whitelist(query: str, facet: QueryFacet) -> tuple[str, ...]:
    if facet.strict_mode:
        return tuple()
    lowered = str(query or "").lower()
    out: list[str] = []
    seen: set[str] = set()
    for domain, hints in _DOMAIN_HINTS.items():
        if not any(token in lowered for token in hints):
            continue
        for path in _DOMAIN_CATEGORY_WHITELISTS.get(domain, tuple()):
            key = str(path or "").strip().lower()
            if (not key) or (key in seen):
                continue
            seen.add(key)
            out.append(key)
    return tuple(out)


def _query_required_terms(query: str) -> list[str]:
    lowered = str(query or "").lower()
    out: list[str] = []
    for token in _QUERY_QUALIFIER_HINTS:
        key = str(token or "").strip().lower()
        if key and key in lowered and key not in out:
            out.append(key)
    return out[:6]


def _subject_anchor_terms(query: str) -> list[str]:
    lowered = str(query or "").lower()
    out: list[str] = []
    seen: set[str] = set()
    for _group, terms in _SUBJECT_ANCHOR_HINTS.items():
        if not any(str(t).lower() in lowered for t in terms):
            continue
        for term in terms:
            key = str(term).lower()
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out[:12]


def _target_field_terms(query: str) -> list[str]:
    lowered = str(query or "").lower()
    out: list[str] = []
    groups = (
        (("birthday", "birth date", "dob", "生日", "出生日期"), "birth_date"),
        (
            (
                "coverage",
                "covered",
                "what's covered",
                "保障范围",
                "覆盖范围",
                "exclusion",
                "除外",
            ),
            "coverage_scope",
        ),
        (
            ("how to maintain", "maintenance", "maintain", "维护", "保养"),
            "maintenance_howto",
        ),
        (("contact", "phone", "email", "联系方式", "电话", "邮箱"), "contact"),
    )
    for tokens, key in groups:
        if any(tok in lowered for tok in tokens):
            out.append(key)
    return out[:4]


def _infer_subject_entity(
    query: str, *, detail_topic: str = "", route: str = ""
) -> str:
    lowered = str(query or "").lower()
    if any(tok in lowered for tok in ("pet insurance", "宠物保险")):
        return "pet_insurance"
    if any(
        tok in lowered for tok in ("birthday", "birth date", "dob", "生日", "出生日期")
    ):
        return "pet_profile"
    if any(tok in lowered for tok in ("current bill", "current bills", "账单", "bill")):
        if any(
            tok in lowered for tok in ("energy", "electricity", "gas", "电费", "燃气")
        ):
            return "utility_bills"
        return "bills"
    if any(
        tok in lowered for tok in ("water tank", "rainwater tank", "水箱", "蓄水箱")
    ):
        return "home_maintenance"
    if any(tok in lowered for tok in ("insurance", "保单", "保险")):
        return "insurance"
    if any(tok in lowered for tok in ("warranty", "保修")):
        return "warranty"
    if detail_topic:
        return f"{detail_topic}_details"
    return route or "generic"


def _is_historical_fact_query(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(tok in lowered for tok in _HISTORICAL_FACT_QUERY_HINTS)


def _looks_planned_or_proposal_doc(text_blob: str) -> bool:
    lowered = str(text_blob or "").lower()
    return any(tok in lowered for tok in _PROPOSAL_DOC_HINTS)
