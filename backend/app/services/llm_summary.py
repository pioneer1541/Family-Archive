import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.services.source_tags import category_labels_for_path, leaf_category_paths, normalize_category_path


settings = get_settings()
logger = get_logger(__name__)
_JSON_BLOCK = re.compile(r"\{.*\}", flags=re.S)
_DOLLAR_AMOUNT = re.compile(r"(?<![A-Za-z])(\$\s?\d[\d,]*(?:\.\d+)?)")

# Prompt v2
DOCUMENT_SUMMARY_PROMPT = (
    "You are a senior document analyst. Return JSON only with keys: summary_en, summary_zh. "
    "Primary output language is Chinese (summary_zh must be complete and natural), summary_en is concise mirror. "
    "Summarize the document as decisions and facts: purpose, key dates, key amounts, obligations/risks, and next actions. "
    "Never output ingestion/process terminology (ingestion, chunk, pipeline, map-reduce, page-section, source_type, status). "
    "Do not output boilerplate like 'document imported' or 'X chunks indexed'. "
    "Currency rule: only when '$' appears, treat it as AUD and label as AUD/澳币; if no '$' appears, do not invent currency."
)

PAGE_SUMMARY_PROMPT = (
    "You are a long-document page summarizer. Return JSON only with keys: summary_en, summary_zh. "
    "Summarize one page into 2-4 factual bullets worth of content in prose. Keep dates, amounts, obligations, and actions. "
    "Do not output process words (ingestion/chunk/pipeline/map-reduce/page-section). "
    "Currency rule: only when '$' appears, treat it as AUD and label as AUD/澳币; if no '$' appears, do not invent currency."
)

SECTION_SUMMARY_PROMPT = (
    "You are a section-level summarizer. Return JSON only with keys: summary_en, summary_zh. "
    "Merge 5-10 page summaries into a coherent section summary focused on conclusions, obligations, and next actions. "
    "Do not output process words (ingestion/chunk/pipeline/map-reduce/page-section). "
    "Currency rule: only when '$' appears, treat it as AUD and label as AUD/澳币; if no '$' appears, do not invent currency."
)

FINAL_SUMMARY_PROMPT = (
    "You are the final reducer for long-document analysis. Return JSON only with keys: summary_en, summary_zh. "
    "Produce final actionable summary using section summaries and semantic chunks. "
    "summary_zh must be Chinese-first, conclusion-oriented, and include: key facts, risks/obligations, and recommended actions. "
    "Never output process words (ingestion/chunk/pipeline/map-reduce/page-section/semantic_chunks). "
    "Currency rule: only when '$' appears, treat it as AUD and label as AUD/澳币; if no '$' appears, do not invent currency."
)

FRIENDLY_NAME_PROMPT = (
    "You are a constrained naming assistant. Return JSON only with keys: friendly_name_en, friendly_name_zh. "
    "Generate accurate friendly names from summary + category_path, with category-consistent topic wording. "
    "Use date prefix only when date is the primary identifier (e.g., bills/statements/dated notices). "
    "For manuals/specifications/warranties/general references, avoid date prefix unless strictly necessary. "
    "Prefer concise format like 'Electricity Bill' or when date-needed '2026-02 Electricity Bill'. "
    "Do not include hash, source path, technical ids, process words, or generic placeholders."
)

CATEGORY_PROMPT = (
    "You are a strict taxonomy classifier. Return JSON only with key: category_path. "
    "Use two internal steps: choose top-level, then choose leaf-level. "
    "Proposal/quote/offer/contract style documents are not utility bills; choose legal/contracts unless clear legal scope suggests another legal leaf. "
    "Vehicle insurance documents (car/vehicle/motor + policy/certificate/insurance, including AAMI/rego terms) must go to home/insurance/vehicle. "
    "Health insurance documents (hospital/extras/private health/medicare) must go to health/insurance/private. "
    "Never classify vehicle insurance into health/insurance*. "
    "Output only one LEAF category_path from allowed_category_paths. "
    "Never output top-level or intermediate path. Never invent any path outside allowed_category_paths."
)

_PROMPT_REGISTRY = {
    "document_summary": DOCUMENT_SUMMARY_PROMPT,
    "page_summary": PAGE_SUMMARY_PROMPT,
    "section_summary": SECTION_SUMMARY_PROMPT,
    "final_summary": FINAL_SUMMARY_PROMPT,
    "friendly_name": FRIENDLY_NAME_PROMPT,
    "category": CATEGORY_PROMPT,
}

_QUALITY_BANNED_PATTERNS = [
    re.compile(r"已\s*从.*入库", flags=re.IGNORECASE),
    re.compile(r"分块", flags=re.IGNORECASE),
    re.compile(r"section-level synthesis", flags=re.IGNORECASE),
    re.compile(r"semantic[_\s-]*chunks?", flags=re.IGNORECASE),
    re.compile(r"语义分块", flags=re.IGNORECASE),
    re.compile(r"第\s*\d+\s*页[:：]\s*重点涉及", flags=re.IGNORECASE),
    re.compile(r"\b(chunk|pipeline|ingestion|map[-\s]?reduce|source_type|status)\b", flags=re.IGNORECASE),
]

_ENTITY_PATTERNS = [
    re.compile(r"\b20\d{2}[/-](?:0?[1-9]|1[0-2])(?:[/-](?:0?[1-9]|[12]\d|3[01]))?\b"),
    re.compile(r"\b(?:0?[1-9]|1[0-2])[/-]20\d{2}\b"),
    re.compile(r"20\d{2}\s*年\s*(?:0?[1-9]|1[0-2])\s*月"),
    re.compile(r"\$\s?\d"),
    re.compile(r"\b(?:aud|amount\s+due|invoice|bill|tax\s+invoice|kwh|usage)\b", flags=re.IGNORECASE),
    re.compile(r"\b(?:due\s+date|deadline|must|required|obligation|risk|action)\b", flags=re.IGNORECASE),
    re.compile(r"\b(?:到期|应付|金额|义务|风险|建议|行动项|下一步)\b"),
]

_BILL_DOC_TOKENS = [
    "invoice",
    "tax invoice",
    "bill",
    "statement",
    "fee notice",
    "账单",
    "发票",
    "费用通知",
    "对账单",
]

_BILL_PAYMENT_TOKENS = [
    "due",
    "amount due",
    "total due",
    "payment",
    "overdue",
    "interest",
    "bpay",
    "deft",
    "to pay",
    "到期",
    "应付",
    "付款",
    "支付",
    "逾期",
    "利息",
]

_MANUAL_TECH_TOKENS = [
    "manual",
    "guide",
    "installation",
    "connection",
    "outlet",
    "inlet",
    "series",
    "model",
    "heater",
    "specification",
    "warranty",
    "技术说明",
    "说明书",
    "安装",
    "连接",
    "参数",
    "型号",
    "热水器",
    "保修",
    "操作手册",
]

_BILL_AMOUNT_PATTERNS = [
    re.compile(r"\$\s?\d"),
    re.compile(r"\baud\s*\$?\s?\d", flags=re.IGNORECASE),
    re.compile(r"\bamount\s+due\b.*\d", flags=re.IGNORECASE),
    re.compile(r"\btotal\s+due\b.*\d", flags=re.IGNORECASE),
    re.compile(r"金额.*\d"),
    re.compile(r"应付.*\d"),
]

_BILL_DATE_PATTERNS = [
    re.compile(r"\bdue\s+date\b", flags=re.IGNORECASE),
    re.compile(r"\bpayment\s+due\b", flags=re.IGNORECASE),
    re.compile(r"到期"),
    re.compile(r"截止"),
]

_NON_BILL_COMMERCIAL_TOKENS = [
    "proposal",
    "solar proposal",
    "quote",
    "quotation",
    "offer",
    "contract",
    "agreement",
    "signed",
    "signed by",
    "acceptance",
    "deposit",
    "stc",
    "方案",
    "报价",
    "合同",
    "协议",
    "签署",
    "定金",
]

_NON_BILL_PRIMARY_TOKENS = [
    "proposal",
    "solar proposal",
    "quote",
    "quotation",
    "contract",
    "agreement",
    "signed",
    "签署",
    "方案",
    "报价",
    "合同",
    "协议",
]

_INSURANCE_GENERAL_TOKENS = [
    "insurance",
    "insurer",
    "policy",
    "certificate of insurance",
    "cover",
    "premium",
    "aami",
    "保单",
    "保险",
    "保费",
]
_INSURANCE_GENERAL_WORD_RE = re.compile(r"\b(insurance|insurer|policy|certificate of insurance|cover|premium)\b", flags=re.IGNORECASE)
_INSURANCE_GENERAL_ZH_RE = re.compile(r"(保单|保险|保费)")

_VEHICLE_INSURANCE_SUBJECT_TOKENS = [
    "car",
    "vehicle",
    "motor",
    "auto",
    "rego",
    "registration",
    "aami car",
    "车险",
    "车辆",
    "机动车",
]

_VEHICLE_INSURANCE_DOC_TOKENS = [
    "policy",
    "certificate",
    "insurance",
    "policy account",
    "certificate of insurance",
    "保单",
    "保险",
    "保险证明",
]

_HEALTH_INSURANCE_TOKENS = [
    "hospital",
    "extras",
    "private health",
    "medicare",
    "health insurance",
    "bronze plus",
    "住院",
    "医保",
    "医疗保险",
    "健康保险",
]

_VEHICLE_INSURANCE_STRONG_WORD_RE = re.compile(r"\b(vehicle|motor|rego|registration)\b", flags=re.IGNORECASE)
_VEHICLE_INSURANCE_CAR_WORD_RE = re.compile(r"\bcar\b", flags=re.IGNORECASE)
_VEHICLE_INSURANCE_DOC_RE = re.compile(r"\b(policy|certificate|insurance|policy account|certificate of insurance)\b", flags=re.IGNORECASE)
_VEHICLE_INSURANCE_ZH_RE = re.compile(r"(车险|车辆|机动车|车牌|行驶证|驾照)")
_VEHICLE_INSURANCE_DOC_ZH_RE = re.compile(r"(保单|保险证明)")
_NEGATED_CAR_CONTEXT_RE = re.compile(
    r"(not\s+include\s+car|does\s+not\s+include\s+car|exclude\s+car|without\s+car\s+insurance|不包含汽车|不含汽车|不包含车险|不含车险)",
    flags=re.IGNORECASE,
)
_HEALTH_INSURANCE_WORD_RE = re.compile(r"\b(hospital|extras|private health|medicare|health insurance|bronze plus)\b", flags=re.IGNORECASE)
_HEALTH_INSURANCE_ZH_RE = re.compile(r"(住院|医保|医疗保险|健康保险|私保)")

_VEHICLE_SUBTYPE_MOTORCYCLE_RE = re.compile(r"\b(motorcycle|motorbike|bike insurance)\b", flags=re.IGNORECASE)
_VEHICLE_SUBTYPE_MOTORCYCLE_ZH_RE = re.compile(r"摩托车")
_VEHICLE_SUBTYPE_CAR_RE = re.compile(r"\b(car|tesla|model\s*y)\b", flags=re.IGNORECASE)
_VEHICLE_SUBTYPE_CAR_ZH_RE = re.compile(r"(汽车|轿车)")
_VEHICLE_SUBTYPE_MOTOR_WEAK_RE = re.compile(r"\bmotor(?:\s+vehicle|\s+insurance)?\b", flags=re.IGNORECASE)

_BILL_NAME_TERMS_ZH = ["电费账单", "电费单", "水费账单", "水费单", "燃气账单", "燃气单", "网络账单", "互联网账单", "账单"]
_BILL_NAME_TERMS_EN = ["electricity bill", "water bill", "gas bill", "internet bill", "utility bill", "bill", "invoice", "statement"]

_NAME_KEEP_DATE_HINTS = [
    "invoice",
    "bill",
    "statement",
    "fee notice",
    "tax invoice",
    "payment due",
    "due date",
    "账单",
    "发票",
    "费用通知",
    "应付",
    "到期",
    "缴费",
]

_NAME_EN_DATE_PREFIX = re.compile(r"^\s*20\d{2}(?:[-/.](?:0?[1-9]|1[0-2]))?\s*[-_:/ ]*")
_NAME_ZH_DATE_PREFIX = re.compile(r"^\s*20\d{2}\s*年(?:\s*(?:0?[1-9]|1[0-2])\s*月)?\s*[-_:/： ]*")


@dataclass
class LlmJsonCallResult:
    ok: bool
    error_type: str
    error_detail: str
    raw_text: str
    parsed_json: dict[str, Any]
    latency_ms: int
    model: str
    timeout_sec: int
    attempts: int


@dataclass
class VehicleSubtypeDecision:
    subtype: str = "generic_vehicle"  # car | motorcycle | generic_vehicle
    confidence: str = "low"  # high | medium | low
    conflict: bool = False
    signals: dict[str, list[str]] | None = None


def _in_test_mode() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _stage_timeout(stage: str, default_sec: int) -> int:
    key = f"FAMILY_VAULT_SUMMARY_TIMEOUT_{str(stage or '').strip().upper()}_SEC"
    raw = str(os.getenv(key, "") or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except Exception:
            pass
    return int(default_sec)


def _extract_json(text: str) -> dict[str, Any]:
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


def prompt_snapshot() -> dict[str, Any]:
    serialized = json.dumps(_PROMPT_REGISTRY, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return {
        "version": "prompt-v2",
        "hash": digest,
        "items": dict(_PROMPT_REGISTRY),
    }


def _call_json_result(
    system_prompt: str,
    user_payload: dict[str, Any],
    *,
    timeout_sec: int | None = None,
    model_name: str | None = None,
    retry_count: int = 2,
    call_name: str = "llm_json_call",
) -> LlmJsonCallResult:
    model = str(model_name or settings.summary_model).strip() or settings.summary_model
    timeout = int(timeout_sec) if timeout_sec is not None else int(settings.summary_timeout_sec)
    attempts = max(1, int(retry_count) + 1)

    if _in_test_mode():
        return LlmJsonCallResult(
            ok=False,
            error_type="test_mode",
            error_detail="test_mode_disabled_remote_llm",
            raw_text="",
            parsed_json={},
            latency_ms=0,
            model=model,
            timeout_sec=timeout,
            attempts=0,
        )

    url = settings.ollama_base_url.rstrip("/") + "/api/chat"
    last_error_type = "unknown"
    last_error_detail = ""
    last_raw = ""
    last_latency = 0

    for idx in range(attempts):
        t0 = time.time()
        try:
            payload = {
                "model": model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": str(system_prompt or "").strip()},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                "options": {"temperature": 0.05},
            }
            r = requests.post(url, json=payload, timeout=max(4, timeout))
            r.raise_for_status()
            body = r.json() if hasattr(r, "json") else {}
            last_raw = str((body.get("message") or {}).get("content") or "")
            parsed = _extract_json(last_raw)
            last_latency = int((time.time() - t0) * 1000)
            if parsed:
                return LlmJsonCallResult(
                    ok=True,
                    error_type="",
                    error_detail="",
                    raw_text=last_raw,
                    parsed_json=parsed,
                    latency_ms=last_latency,
                    model=model,
                    timeout_sec=timeout,
                    attempts=idx + 1,
                )
            last_error_type = "invalid_json"
            last_error_detail = "empty_or_unparseable_json"
        except requests.Timeout:
            last_latency = int((time.time() - t0) * 1000)
            last_error_type = "timeout"
            last_error_detail = f"timeout>{timeout}s"
        except Exception as exc:
            last_latency = int((time.time() - t0) * 1000)
            last_error_type = "request_error"
            last_error_detail = str(type(exc).__name__)

        if idx < attempts - 1:
            time.sleep(2**idx)

    logger.warning(
        "llm_json_call_failed",
        extra=sanitize_log_context(
            {
                "call_name": call_name,
                "model": model,
                "timeout_sec": timeout,
                "attempts": attempts,
                "error_type": last_error_type,
                "error_detail": last_error_detail,
                "latency_ms": last_latency,
            }
        ),
    )
    return LlmJsonCallResult(
        ok=False,
        error_type=last_error_type,
        error_detail=last_error_detail,
        raw_text=last_raw,
        parsed_json={},
        latency_ms=last_latency,
        model=model,
        timeout_sec=timeout,
        attempts=attempts,
    )


def _call_json(
    system_prompt: str,
    user_payload: dict[str, Any],
    *,
    timeout_sec: int | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    out = _call_json_result(
        system_prompt,
        user_payload,
        timeout_sec=timeout_sec,
        model_name=model_name,
        retry_count=2,
    )
    return out.parsed_json if out.ok else {}


def _enforce_aud_in_en(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""

    def repl(m):
        amount = str(m.group(1) or "").strip()
        left = raw[max(0, m.start() - 8) : m.start()].upper()
        if "AUD" in left:
            return amount
        return f"AUD {amount}"

    return _DOLLAR_AMOUNT.sub(repl, raw)


def _enforce_aud_in_zh(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""

    def repl(m):
        amount = str(m.group(1) or "").strip()
        left = raw[max(0, m.start() - 6) : m.start()]
        if ("澳币" in left) or ("澳元" in left):
            return amount
        return f"澳币{amount}"

    return _DOLLAR_AMOUNT.sub(repl, raw)


def enforce_aud_currency(en: str, zh: str) -> tuple[str, str]:
    return (_enforce_aud_in_en(en), _enforce_aud_in_zh(zh))


def detect_summary_quality_flags(summary_en: str, summary_zh: str) -> list[str]:
    en = str(summary_en or "").strip()
    zh = str(summary_zh or "").strip()
    merged = f"{en}\n{zh}".strip()
    merged_lower = merged.lower()

    flags: list[str] = []
    if not merged:
        flags.append("empty_summary")
        return flags

    for pattern in _QUALITY_BANNED_PATTERNS:
        if pattern.search(merged_lower):
            flags.append("contains_process_terms")
            break

    if len(merged.replace(" ", "")) < 40:
        flags.append("too_short")

    has_entity = any(pattern.search(merged) for pattern in _ENTITY_PATTERNS)
    if not has_entity:
        flags.append("missing_entity_signals")

    dedup: list[str] = []
    seen: set[str] = set()
    for flag in flags:
        if flag in seen:
            continue
        seen.add(flag)
        dedup.append(flag)
    return dedup


def is_low_quality_summary(summary_en: str, summary_zh: str) -> bool:
    flags = set(detect_summary_quality_flags(summary_en, summary_zh))
    return bool({"empty_summary", "contains_process_terms", "missing_entity_signals"} & flags)


def _normalize_summary_output(
    en: str,
    zh: str,
    *,
    strict_entities: bool,
) -> tuple[str, str] | None:
    en_text = str(en or "").strip()
    zh_text = str(zh or "").strip()
    if (not en_text) and (not zh_text):
        return None

    en_text, zh_text = enforce_aud_currency(en_text, zh_text)
    flags = detect_summary_quality_flags(en_text, zh_text)
    if "contains_process_terms" in flags:
        return None
    if strict_entities and "missing_entity_signals" in flags:
        return None
    return (en_text, zh_text)


def summarize_document_with_model(
    *,
    text: str,
    title_en: str,
    title_zh: str,
    category_label_en: str,
    category_label_zh: str,
) -> tuple[str, str] | None:
    out = _call_json_result(
        DOCUMENT_SUMMARY_PROMPT,
        {
            "title_en": title_en,
            "title_zh": title_zh,
            "category_en": category_label_en,
            "category_zh": category_label_zh,
            "content": str(text or "")[:9000],
            "constraints": {
                "max_chars_per_lang": 650,
                "style_zh": "中文为主，提炼重点，给出可执行下一步",
                "style_en": "concise analytical summary",
            },
        },
        timeout_sec=_stage_timeout("DOC", 30),
        retry_count=2,
        call_name="summarize_document",
    )
    if not out.ok:
        return None
    normalized = _normalize_summary_output(
        str(out.parsed_json.get("summary_en") or ""),
        str(out.parsed_json.get("summary_zh") or ""),
        strict_entities=True,
    )
    if normalized is None:
        return None
    en_text, zh_text = normalized
    return (en_text[:700], zh_text[:700])


def summarize_page_with_model(*, page_text: str, page_index: int, total_pages: int, title: str) -> tuple[str, str] | None:
    out = _call_json_result(
        PAGE_SUMMARY_PROMPT,
        {
            "title": title,
            "page_index": int(page_index),
            "total_pages": int(total_pages),
            "page_text": str(page_text or "")[:5500],
            "constraints": {"max_chars_per_lang": 320},
        },
        timeout_sec=_stage_timeout("PAGE", 25),
        retry_count=2,
        call_name="summarize_page",
    )
    if not out.ok:
        return None
    normalized = _normalize_summary_output(
        str(out.parsed_json.get("summary_en") or ""),
        str(out.parsed_json.get("summary_zh") or ""),
        strict_entities=False,
    )
    if normalized is None:
        return None
    en_text, zh_text = normalized
    return (en_text[:380], zh_text[:380])


def summarize_section_with_model(
    *,
    section_index: int,
    page_start: int,
    page_end: int,
    page_summaries_en: list[str],
    page_summaries_zh: list[str],
    title: str,
) -> tuple[str, str] | None:
    out = _call_json_result(
        SECTION_SUMMARY_PROMPT,
        {
            "title": title,
            "section_index": int(section_index),
            "page_range": f"{int(page_start)}-{int(page_end)}",
            "page_summaries_en": [str(x or "")[:240] for x in page_summaries_en[:20]],
            "page_summaries_zh": [str(x or "")[:240] for x in page_summaries_zh[:20]],
            "constraints": {"max_chars_per_lang": 420},
        },
        timeout_sec=_stage_timeout("SECTION", 35),
        retry_count=2,
        call_name="summarize_section",
    )
    if not out.ok:
        return None
    normalized = _normalize_summary_output(
        str(out.parsed_json.get("summary_en") or ""),
        str(out.parsed_json.get("summary_zh") or ""),
        strict_entities=False,
    )
    if normalized is None:
        return None
    en_text, zh_text = normalized
    return (en_text[:480], zh_text[:480])


def summarize_final_with_model(
    *,
    title: str,
    section_summaries_en: list[str],
    section_summaries_zh: list[str],
    semantic_chunks: list[str],
) -> tuple[str, str] | None:
    out = _call_json_result(
        FINAL_SUMMARY_PROMPT,
        {
            "title": title,
            "section_summaries_en": [str(x or "")[:260] for x in section_summaries_en[:24]],
            "section_summaries_zh": [str(x or "")[:260] for x in section_summaries_zh[:24]],
            "semantic_chunks": [str(x or "")[:420] for x in semantic_chunks[:8]],
            "constraints": {"max_chars_per_lang": 620},
        },
        timeout_sec=_stage_timeout("FINAL", 45),
        retry_count=2,
        call_name="summarize_final",
    )
    if not out.ok:
        return None
    normalized = _normalize_summary_output(
        str(out.parsed_json.get("summary_en") or ""),
        str(out.parsed_json.get("summary_zh") or ""),
        strict_entities=True,
    )
    if normalized is None:
        return None
    en_text, zh_text = normalized
    return (en_text[:700], zh_text[:700])


def _enforce_name_category_consistency(en: str, zh: str, category_path: str) -> tuple[str, str]:
    safe_en = str(en or "").strip()
    safe_zh = str(zh or "").strip()
    cp = str(category_path or "").strip().lower()

    replacements = {
        "finance/bills/electricity": (("水费", "Water"), ("电费", "Electricity")),
        "finance/bills/water": (("电费", "Electricity"), ("水费", "Water")),
        "finance/bills/gas": (("水费", "Water"), ("燃气", "Gas")),
    }
    if cp in replacements:
        old_pair, new_pair = replacements[cp]
        old_zh, old_en = old_pair
        new_zh, new_en = new_pair
        if old_zh in safe_zh and new_zh not in safe_zh:
            safe_zh = safe_zh.replace(old_zh, new_zh)
        if re.search(rf"\b{re.escape(old_en)}\b", safe_en, flags=re.IGNORECASE) and not re.search(
            rf"\b{re.escape(new_en)}\b", safe_en, flags=re.IGNORECASE
        ):
            safe_en = re.sub(rf"\b{re.escape(old_en)}\b", new_en, safe_en, flags=re.IGNORECASE)

    if cp and (not cp.startswith("finance/bills/")):
        for token in _BILL_NAME_TERMS_ZH:
            safe_zh = safe_zh.replace(token, "").strip()
        for token in _BILL_NAME_TERMS_EN:
            safe_en = re.sub(rf"\b{re.escape(token)}\b", "", safe_en, flags=re.IGNORECASE).strip()
        safe_zh = re.sub(r"\s{2,}", " ", safe_zh).strip(" -_/")
        safe_en = re.sub(r"\s{2,}", " ", safe_en).strip(" -_/")
        if cp == "legal/contracts":
            merged = f"{safe_en}\n{safe_zh}".lower()
            if ("solar" in merged) or ("太阳能" in merged):
                if not safe_en:
                    safe_en = "Solar Proposal Contract"
                if not safe_zh:
                    safe_zh = "太阳能方案合同"
            else:
                if not safe_en:
                    safe_en = "Contract Document"
                if not safe_zh:
                    safe_zh = "合同文件"

    return (safe_en, safe_zh)


def _contains_any(text: str, tokens: list[str]) -> bool:
    raw = str(text or "").lower()
    return any(str(token or "").lower() in raw for token in tokens if str(token or "").strip())


def _has_billing_evidence(*, file_name: str, summary_en: str, summary_zh: str, content_excerpt: str) -> bool:
    merged = "\n".join(
        [
            str(file_name or ""),
            str(summary_en or ""),
            str(summary_zh or ""),
            str(content_excerpt or ""),
        ]
    )
    merged_lower = merged.lower()

    has_doc_token = _contains_any(merged_lower, _BILL_DOC_TOKENS)
    has_payment_token = _contains_any(merged_lower, _BILL_PAYMENT_TOKENS)
    has_amount = any(p.search(merged) for p in _BILL_AMOUNT_PATTERNS)
    has_due_date = any(p.search(merged) for p in _BILL_DATE_PATTERNS)
    has_digits = bool(re.search(r"\d", merged))

    # Guardrail for finance/bills*: require financial/payment evidence,
    # not only "water/gas/electricity" lexical mentions from technical manuals.
    if has_doc_token and has_amount:
        return True
    if has_doc_token and has_payment_token and has_digits:
        return True
    if has_doc_token and has_due_date:
        return True
    return False


def _has_non_bill_commercial_evidence(*, file_name: str, summary_en: str, summary_zh: str, content_excerpt: str) -> bool:
    merged = "\n".join(
        [
            str(file_name or ""),
            str(summary_en or ""),
            str(summary_zh or ""),
            str(content_excerpt or ""),
        ]
    )
    merged_lower = merged.lower()
    has_primary = _contains_any(merged_lower, _NON_BILL_PRIMARY_TOKENS)
    if not has_primary:
        return False
    has_signal = _contains_any(merged_lower, _NON_BILL_COMMERCIAL_TOKENS)
    if not has_signal:
        return False
    has_billing_anchor = _has_billing_evidence(
        file_name=file_name,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    )
    return not has_billing_anchor


def _fallback_non_billing_category(*, file_name: str, summary_en: str, summary_zh: str, content_excerpt: str) -> str:
    if _has_non_bill_commercial_evidence(
        file_name=file_name,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    ):
        return "legal/contracts"
    merged = "\n".join(
        [
            str(file_name or ""),
            str(summary_en or ""),
            str(summary_zh or ""),
            str(content_excerpt or ""),
        ]
    ).lower()
    if _contains_any(merged, _MANUAL_TECH_TOKENS):
        return "home/manuals"
    return "archive/misc"


def _has_vehicle_insurance_evidence(*, file_name: str, summary_en: str, summary_zh: str, content_excerpt: str) -> bool:
    merged = "\n".join([str(file_name or ""), str(summary_en or ""), str(summary_zh or ""), str(content_excerpt or "")])
    lowered = merged.lower()
    has_strong_subject = bool(_VEHICLE_INSURANCE_STRONG_WORD_RE.search(lowered) or _VEHICLE_INSURANCE_ZH_RE.search(merged) or ("aami" in lowered))
    has_car_subject = bool(_VEHICLE_INSURANCE_CAR_WORD_RE.search(lowered))
    car_negated = bool(_NEGATED_CAR_CONTEXT_RE.search(merged))
    has_subject = bool(has_strong_subject or (has_car_subject and (not car_negated)))
    has_doc = bool(_VEHICLE_INSURANCE_DOC_RE.search(lowered) or _VEHICLE_INSURANCE_DOC_ZH_RE.search(merged))
    has_general = _contains_any(lowered, _INSURANCE_GENERAL_TOKENS)
    return bool((has_subject and has_doc) or (has_subject and has_general))


def _has_health_insurance_evidence(*, file_name: str, summary_en: str, summary_zh: str, content_excerpt: str) -> bool:
    merged = "\n".join([str(file_name or ""), str(summary_en or ""), str(summary_zh or ""), str(content_excerpt or "")])
    lowered = merged.lower()
    if _HEALTH_INSURANCE_WORD_RE.search(lowered) or _HEALTH_INSURANCE_ZH_RE.search(merged):
        return True
    return _contains_any(lowered, _HEALTH_INSURANCE_TOKENS)


def _insurance_fallback_path(allowed_set: set[str]) -> str:
    for candidate in ("home/insurance/other", "home/insurance", "archive/misc"):
        if candidate in allowed_set:
            return candidate
    return "archive/misc"


def _insurance_no_evidence_fallback(*, allowed_set: set[str], file_name: str, summary_en: str, summary_zh: str, content_excerpt: str) -> str:
    fallback = _fallback_non_billing_category(
        file_name=file_name,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    )
    if fallback in allowed_set:
        return fallback
    if "home/manuals" in allowed_set:
        merged = "\n".join([str(file_name or ""), str(summary_en or ""), str(summary_zh or ""), str(content_excerpt or "")]).lower()
        if _contains_any(merged, _MANUAL_TECH_TOKENS):
            return "home/manuals"
    return "archive/misc"


def _apply_insurance_category_guard(
    *,
    path: str,
    allowed_set: set[str],
    file_name: str,
    summary_en: str,
    summary_zh: str,
    content_excerpt: str,
) -> str:
    merged = "\n".join([str(file_name or ""), str(summary_en or ""), str(summary_zh or ""), str(content_excerpt or "")])
    lowered = merged.lower()
    has_general_insurance = bool(_INSURANCE_GENERAL_WORD_RE.search(lowered) or _INSURANCE_GENERAL_ZH_RE.search(merged))

    if _has_vehicle_insurance_evidence(
        file_name=file_name,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    ):
        for candidate in ("home/insurance/vehicle", "home/insurance"):
            if candidate in allowed_set:
                return candidate
        return _insurance_fallback_path(allowed_set)

    if _has_health_insurance_evidence(
        file_name=file_name,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    ):
        for candidate in ("health/insurance/private", "health/insurance", "health/insurance/other"):
            if candidate in allowed_set:
                return candidate
        return _insurance_fallback_path(allowed_set)

    if has_general_insurance:
        if path.startswith("finance/bills/"):
            return _insurance_fallback_path(allowed_set)
        if path in {"archive/misc", ""}:
            return _insurance_fallback_path(allowed_set)
        if path == "home/insurance" and "home/insurance/other" in allowed_set:
            return "home/insurance/other"
        if path == "health/insurance":
            if "health/insurance/private" in allowed_set:
                return "health/insurance/private"
            if "health/insurance/other" in allowed_set:
                return "health/insurance/other"
    if "/insurance" in str(path or "") and (not has_general_insurance):
        return _insurance_no_evidence_fallback(
            allowed_set=allowed_set,
            file_name=file_name,
            summary_en=summary_en,
            summary_zh=summary_zh,
            content_excerpt=content_excerpt,
        )
    return path


def _vehicle_subtype_signals_from_text(text: str) -> set[str]:
    raw = str(text or "")
    lowered = raw.lower()
    out: set[str] = set()
    if _VEHICLE_SUBTYPE_MOTORCYCLE_RE.search(lowered) or _VEHICLE_SUBTYPE_MOTORCYCLE_ZH_RE.search(raw):
        out.add("motorcycle")
    # "motor"/"motor vehicle"/"motor insurance" are weak signals and must not imply motorcycle.
    if _VEHICLE_SUBTYPE_CAR_RE.search(lowered) or _VEHICLE_SUBTYPE_CAR_ZH_RE.search(raw):
        out.add("car")
    return out


def resolve_vehicle_insurance_subtype(
    *,
    file_name: str,
    summary_en: str,
    summary_zh: str,
    content_excerpt: str = "",
) -> VehicleSubtypeDecision:
    filename_signals = _vehicle_subtype_signals_from_text(str(file_name or ""))
    summary_signals = _vehicle_subtype_signals_from_text("\n".join([str(summary_en or ""), str(summary_zh or "")]))
    content_signals = _vehicle_subtype_signals_from_text(str(content_excerpt or ""))
    all_signals = set().union(filename_signals, summary_signals, content_signals)

    conflict = "car" in all_signals and "motorcycle" in all_signals
    if conflict:
        return VehicleSubtypeDecision(
            subtype="generic_vehicle",
            confidence="high",
            conflict=True,
            signals={
                "filename": sorted(filename_signals),
                "summary": sorted(summary_signals),
                "content": sorted(content_signals),
            },
        )
    if "motorcycle" in all_signals:
        confidence = "high" if ("motorcycle" in content_signals or "motorcycle" in summary_signals) else "medium"
        return VehicleSubtypeDecision(
            subtype="motorcycle",
            confidence=confidence,
            conflict=False,
            signals={
                "filename": sorted(filename_signals),
                "summary": sorted(summary_signals),
                "content": sorted(content_signals),
            },
        )
    if "car" in all_signals:
        confidence = "high" if ("car" in content_signals or "car" in summary_signals) else "medium"
        return VehicleSubtypeDecision(
            subtype="car",
            confidence=confidence,
            conflict=False,
            signals={
                "filename": sorted(filename_signals),
                "summary": sorted(summary_signals),
                "content": sorted(content_signals),
            },
        )
    return VehicleSubtypeDecision(
        subtype="generic_vehicle",
        confidence="low",
        conflict=False,
        signals={
            "filename": sorted(filename_signals),
            "summary": sorted(summary_signals),
            "content": sorted(content_signals),
        },
    )


def _rewrite_vehicle_terms_to_generic(en: str, zh: str) -> tuple[str, str]:
    safe_en = str(en or "").strip()
    safe_zh = str(zh or "").strip()

    en_replacements = [
        (r"\bmotorcycle insurance certificate\b", "Vehicle Insurance Certificate"),
        (r"\bmotorbike insurance certificate\b", "Vehicle Insurance Certificate"),
        (r"\bcar insurance certificate\b", "Vehicle Insurance Certificate"),
        (r"\bmotorcycle insurance policy\b", "Vehicle Insurance Policy"),
        (r"\bcar insurance policy\b", "Vehicle Insurance Policy"),
        (r"\bmotorcycle insurance\b", "Vehicle Insurance"),
        (r"\bmotorbike insurance\b", "Vehicle Insurance"),
        (r"\bcar insurance\b", "Vehicle Insurance"),
        (r"\bmotorcycle\b", "vehicle"),
        (r"\bmotorbike\b", "vehicle"),
    ]
    for pattern, repl in en_replacements:
        safe_en = re.sub(pattern, repl, safe_en, flags=re.IGNORECASE)
    safe_en = re.sub(r"\s{2,}", " ", safe_en).strip(" -_/")

    zh_replacements = [
        ("摩托车保险证书", "车辆保险证书"),
        ("摩托车保险单", "车辆保险单"),
        ("汽车保险证书", "车辆保险证书"),
        ("汽车保险单", "车辆保险单"),
        ("摩托车保险", "车辆保险"),
        ("汽车保险", "车辆保险"),
        ("摩托车", "车辆"),
    ]
    for old, new in zh_replacements:
        safe_zh = safe_zh.replace(old, new)
    safe_zh = re.sub(r"\s{2,}", " ", safe_zh).strip(" -_/")
    return (safe_en, safe_zh)


def normalize_vehicle_insurance_summary(
    *,
    category_path: str,
    file_name: str,
    summary_en: str,
    summary_zh: str,
    content_excerpt: str = "",
) -> tuple[str, str]:
    if str(category_path or "").strip().lower() != "home/insurance/vehicle":
        return (str(summary_en or "").strip(), str(summary_zh or "").strip())
    decision = resolve_vehicle_insurance_subtype(
        file_name=file_name,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    )
    if not decision.conflict:
        return (str(summary_en or "").strip(), str(summary_zh or "").strip())
    return _rewrite_vehicle_terms_to_generic(summary_en, summary_zh)


def normalize_vehicle_insurance_name(
    *,
    category_path: str,
    file_name: str,
    title_en: str,
    title_zh: str,
    summary_en: str,
    summary_zh: str,
    content_excerpt: str = "",
) -> tuple[str, str]:
    if str(category_path or "").strip().lower() != "home/insurance/vehicle":
        return (str(title_en or "").strip(), str(title_zh or "").strip())
    decision = resolve_vehicle_insurance_subtype(
        file_name=file_name,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    )
    if not decision.conflict:
        return (str(title_en or "").strip(), str(title_zh or "").strip())
    return _rewrite_vehicle_terms_to_generic(title_en, title_zh)


def _should_keep_date_prefix_in_name(*, category_path: str, summary_en: str, summary_zh: str, file_name: str) -> bool:
    cp = str(category_path or "").strip().lower()
    merged = "\n".join([str(summary_en or ""), str(summary_zh or ""), str(file_name or "")]).lower()
    if cp.startswith("finance/"):
        return True
    if cp.startswith("work/meeting_notes"):
        return True
    if _contains_any(merged, _NAME_KEEP_DATE_HINTS):
        return True
    return False


def _strip_leading_date_prefix(en: str, zh: str) -> tuple[str, str]:
    out_en = _NAME_EN_DATE_PREFIX.sub("", str(en or "").strip()).strip()
    out_zh = _NAME_ZH_DATE_PREFIX.sub("", str(zh or "").strip()).strip()
    return (out_en, out_zh)


def _normalize_name_date_prefix(
    *,
    en: str,
    zh: str,
    category_path: str,
    summary_en: str,
    summary_zh: str,
    file_name: str,
) -> tuple[str, str]:
    if _should_keep_date_prefix_in_name(
        category_path=category_path,
        summary_en=summary_en,
        summary_zh=summary_zh,
        file_name=file_name,
    ):
        return (str(en or "").strip(), str(zh or "").strip())

    stripped_en, stripped_zh = _strip_leading_date_prefix(en, zh)
    final_en = stripped_en or str(en or "").strip()
    final_zh = stripped_zh or str(zh or "").strip()
    return (final_en, final_zh)


def regenerate_friendly_name_from_summary(
    *,
    file_name: str,
    category_path: str,
    summary_en: str,
    summary_zh: str,
    fallback_en: str,
    fallback_zh: str,
    content_excerpt: str = "",
) -> tuple[str, str] | None:
    out = _call_json_result(
        FRIENDLY_NAME_PROMPT,
        {
            "file_name": file_name,
            "category_path": category_path,
            "summary_en": str(summary_en or "")[:1600],
            "summary_zh": str(summary_zh or "")[:1600],
            "fallback_en": str(fallback_en or ""),
            "fallback_zh": str(fallback_zh or ""),
            "constraints": {
                "max_chars": 80,
                "must_match_category": True,
            },
        },
        timeout_sec=_stage_timeout("NAME", 20),
        retry_count=2,
        model_name=settings.friendly_name_model,
        call_name="friendly_name",
    )
    if not out.ok:
        return None

    en = str(out.parsed_json.get("friendly_name_en") or "").strip()
    zh = str(out.parsed_json.get("friendly_name_zh") or "").strip()
    if (not en) and (not zh):
        return None
    if not en:
        en = str(fallback_en or "").strip()
    if not zh:
        zh = str(fallback_zh or "").strip()
    en, zh = _enforce_name_category_consistency(en, zh, category_path)
    en, zh = _normalize_name_date_prefix(
        en=en,
        zh=zh,
        category_path=category_path,
        summary_en=summary_en,
        summary_zh=summary_zh,
        file_name=file_name,
    )
    en, zh = normalize_vehicle_insurance_name(
        category_path=category_path,
        file_name=file_name,
        title_en=en,
        title_zh=zh,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    )
    return (en[:80], zh[:80])


def classify_category_from_summary(
    *,
    file_name: str,
    source_type: str,
    summary_en: str,
    summary_zh: str,
    content_excerpt: str = "",
) -> tuple[str, str, str] | None:
    allowed = [path for path in leaf_category_paths(include_archive_misc=False) if path != "archive/misc"]
    if not allowed:
        return None
    allowed_set = set(allowed)

    out = _call_json_result(
        CATEGORY_PROMPT,
        {
            "file_name": file_name,
            "source_type": source_type,
            "summary_en": str(summary_en or "")[:1600],
            "summary_zh": str(summary_zh or "")[:1600],
            "content_excerpt": str(content_excerpt or "")[:2200],
            "allowed_category_paths": allowed,
            "default_if_uncertain": "archive/misc",
            "must_return_leaf": True,
        },
        timeout_sec=_stage_timeout("CATEGORY", 20),
        retry_count=2,
        model_name=settings.category_model,
        call_name="classify_category",
    )
    if not out.ok:
        return None

    raw_path = str(out.parsed_json.get("category_path") or "").strip().lower()
    if not raw_path:
        return None

    path = normalize_category_path(raw_path)
    if path not in allowed_set:
        path = "archive/misc"
    if path.startswith("finance/bills/") and (
        _has_non_bill_commercial_evidence(
            file_name=file_name,
            summary_en=summary_en,
            summary_zh=summary_zh,
            content_excerpt=content_excerpt,
        )
    ):
        path = "legal/contracts" if "legal/contracts" in allowed_set else "archive/misc"
    elif path.startswith("finance/bills/") and (
        not _has_billing_evidence(
            file_name=file_name,
            summary_en=summary_en,
            summary_zh=summary_zh,
            content_excerpt=content_excerpt,
        )
    ):
        fallback_path = _fallback_non_billing_category(
            file_name=file_name,
            summary_en=summary_en,
            summary_zh=summary_zh,
            content_excerpt=content_excerpt,
        )
        path = fallback_path if fallback_path in allowed_set else "archive/misc"
    path = _apply_insurance_category_guard(
        path=path,
        allowed_set=allowed_set,
        file_name=file_name,
        summary_en=summary_en,
        summary_zh=summary_zh,
        content_excerpt=content_excerpt,
    )
    en, zh = category_labels_for_path(path)
    return (en, zh, path)
