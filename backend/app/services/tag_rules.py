import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from app.config import get_settings

settings = get_settings()

_TAG_PATTERN = re.compile(r"^[a-z0-9._-]+:[a-z0-9._-]+$")
_NON_VALUE_CHARS = re.compile(r"[^a-z0-9._-]+")
_MULTI_DASH = re.compile(r"-+")
_WHITESPACE = re.compile(r"\s+")

_GENERIC_MAIL_DOMAINS = {
    "gmail",
    "outlook",
    "hotmail",
    "yahoo",
    "icloud",
    "proton",
    "qq",
    "163",
    "126",
}

_FAMILY_LABELS = {
    "vendor": {"en": "Vendor", "zh": "供应商"},
    "account": {"en": "Account", "zh": "账户别名"},
    "person": {"en": "Person", "zh": "人物"},
    "pet": {"en": "Pet", "zh": "宠物"},
    "location": {"en": "Location", "zh": "地点"},
    "device": {"en": "Device", "zh": "设备"},
    "topic": {"en": "Topic", "zh": "主题"},
    "project": {"en": "Project", "zh": "项目"},
    "status": {"en": "Status", "zh": "状态"},
}

_VENDOR_KEYWORDS: dict[str, str] = {
    "agl": "agl",
    "agl energy": "agl",
    "telstra": "telstra",
    "yarra valley water": "yarra-valley-water",
    "australian gas networks": "australian-gas-networks",
    "origin energy": "origin-energy",
    "ausnet": "ausnet",
    "chemist warehouse": "chemist-warehouse",
    "medibank": "medibank",
    "bupa": "bupa",
}

_DEVICE_KEYWORDS: dict[str, str] = {
    "tp-link ax6000": "tp-link-ax6000",
    "ax6000": "tp-link-ax6000",
    "synology ds423 plus": "synology-ds423-plus",
    "ds423 plus": "synology-ds423-plus",
    "home assistant": "home-assistant",
    "zigbee": "zigbee-gateway",
}

_PROJECT_KEYWORDS: dict[str, str] = {
    "jarvis mcp stack": "jarvis-mcp-stack",
    "mcp stack": "jarvis-mcp-stack",
    "dispensary kpi": "dispensary-kpi",
    "family knowledge vault": "family-knowledge-vault",
}

_PERSON_KEYWORDS: dict[str, str] = {
    # Populated via Settings → Keywords (person names)
}

_PET_KEYWORDS: dict[str, str] = {
    # Populated via Settings → Keywords (pet names)
}

_LOCATION_KEYWORDS: dict[str, str] = {
    # Populated via Settings → Keywords (location names)
}


@dataclass(frozen=True)
class TagRules:
    allowed_families: tuple[str, ...]
    synonyms_alias_to_canonical: dict[str, str]
    topic_whitelist: tuple[str, ...]
    status_whitelist: tuple[str, ...]
    max_tags_per_doc: int
    max_topic_tags_per_doc: int
    label_map: dict[str, dict[str, str]]


def _candidate_rules_paths() -> list[Path]:
    env_path = str(settings.tag_rules_path or "").strip()
    backend_root = Path(__file__).resolve().parents[2]
    return [
        Path(env_path) if env_path else backend_root / "services/kb-worker/config/tag_rules.json",
        backend_root / "services/kb-worker/config/tag_rules.json",
    ]


def _load_raw_rules() -> dict:
    for path in _candidate_rules_paths():
        if not str(path):
            continue
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


def _normalize_value(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = _WHITESPACE.sub("-", raw)
    raw = raw.replace("(", "").replace(")", "")
    raw = raw.replace("[", "").replace("]", "")
    raw = raw.replace("{", "").replace("}", "")
    raw = _NON_VALUE_CHARS.sub("-", raw)
    raw = _MULTI_DASH.sub("-", raw)
    raw = raw.strip("-._")
    return raw


def _build_synonyms_map(raw: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    payload = raw.get("synonyms_map") if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        return out

    for canonical, aliases in payload.items():
        canonical_key = str(canonical or "").strip().lower()
        if not canonical_key:
            continue

        if isinstance(aliases, str):
            alias_key = str(aliases or "").strip().lower()
            if alias_key:
                out[alias_key] = canonical_key
            continue

        if isinstance(aliases, list):
            for alias in aliases:
                alias_key = str(alias or "").strip().lower()
                if alias_key:
                    out[alias_key] = canonical_key

    return out


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if (not value) or (value in seen):
            continue
        seen.add(value)
        out.append(value)
    return out


def _safe_tuple(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(_dedupe_keep_order(str(v or "").strip().lower() for v in values if str(v or "").strip()))


def _safe_int(value: object, *, default: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(min_value, min(max_value, number))


@lru_cache(maxsize=1)
def load_tag_rules() -> TagRules:
    raw = _load_raw_rules()
    allowed_families = _safe_tuple(raw.get("allowed_families") or _FAMILY_LABELS.keys())
    topic_whitelist = _safe_tuple(raw.get("topic_whitelist") or ())
    status_whitelist = _safe_tuple(raw.get("status_whitelist") or ("important", "review", "todo", "archived"))
    label_map_raw = raw.get("label_map") if isinstance(raw, dict) else {}

    label_map: dict[str, dict[str, str]] = {}
    if isinstance(label_map_raw, dict):
        for key, labels in label_map_raw.items():
            k = str(key or "").strip().lower()
            if (not k) or (not isinstance(labels, dict)):
                continue
            label_map[k] = {
                "en": str(labels.get("en") or "").strip(),
                "zh": str(labels.get("zh") or "").strip(),
            }

    return TagRules(
        allowed_families=allowed_families,
        synonyms_alias_to_canonical=_build_synonyms_map(raw),
        topic_whitelist=topic_whitelist,
        status_whitelist=status_whitelist,
        max_tags_per_doc=_safe_int(raw.get("max_tags_per_doc"), default=12, min_value=1, max_value=64),
        max_topic_tags_per_doc=_safe_int(raw.get("max_topic_tags_per_doc"), default=3, min_value=1, max_value=20),
        label_map=label_map,
    )


def split_tag_key(tag_key: str) -> tuple[str, str]:
    raw = str(tag_key or "").strip().lower()
    if ":" not in raw:
        return ("", "")
    family_raw, value_raw = raw.split(":", 1)
    family = _normalize_value(family_raw)
    value = _normalize_value(value_raw)
    return (family, value)


def normalize_tag_key(tag_key: str, *, allow_unknown_family: bool = False) -> str:
    rules = load_tag_rules()
    family, value = split_tag_key(tag_key)
    if (not family) or (not value):
        return ""
    if (not allow_unknown_family) and (family not in set(rules.allowed_families)):
        return ""

    normalized = f"{family}:{value}"
    mapped = rules.synonyms_alias_to_canonical.get(normalized, normalized)
    if mapped != normalized:
        family2, value2 = split_tag_key(mapped)
        if (not family2) or (not value2):
            return ""
        if (not allow_unknown_family) and (family2 not in set(rules.allowed_families)):
            return ""
        normalized = f"{family2}:{value2}"

    if not _TAG_PATTERN.match(normalized):
        return ""

    if (family == "status") and (value not in set(rules.status_whitelist)):
        return ""

    return normalized[:128]


def normalize_tag_list(tag_keys: Iterable[str], *, strict: bool = False) -> tuple[list[str], list[str]]:
    out: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in tag_keys:
        norm = normalize_tag_key(str(raw or ""))
        if not norm:
            text = str(raw or "").strip()
            if text:
                invalid.append(text)
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)

    if strict and invalid:
        return ([], invalid)
    return (out, invalid)


def tag_family(tag_key: str) -> str:
    family, _value = split_tag_key(tag_key)
    return family


def tag_label(tag_key: str, *, ui_lang: str = "zh") -> str:
    key = normalize_tag_key(tag_key)
    if not key:
        return ""
    rules = load_tag_rules()
    labels = rules.label_map.get(key) or {}

    lang = "zh" if str(ui_lang or "").lower().startswith("zh") else "en"
    if labels.get(lang):
        return str(labels.get(lang) or "")

    family, value = split_tag_key(key)
    family_label = _FAMILY_LABELS.get(family, {}).get(lang, family)
    if lang == "zh":
        value_label = value.replace("-", " ")
        return f"{family_label}: {value_label}"
    value_label = value.replace("-", " ").title()
    return f"{family_label}: {value_label}"


def validate_tag_limits(tag_keys: Iterable[str]) -> tuple[bool, str]:
    rules = load_tag_rules()
    values = _dedupe_keep_order(tag_keys)
    if len(values) > int(rules.max_tags_per_doc):
        return (False, "too_many_tags")

    topic_count = sum(1 for key in values if tag_family(key) == "topic")
    if topic_count > int(rules.max_topic_tags_per_doc):
        return (False, "too_many_topic_tags")

    return (True, "")


def trim_tag_limits(tag_keys: Iterable[str]) -> list[str]:
    rules = load_tag_rules()
    limit_total = int(rules.max_tags_per_doc)
    limit_topic = int(rules.max_topic_tags_per_doc)

    out: list[str] = []
    seen: set[str] = set()
    topic_count = 0

    for raw in tag_keys:
        key = normalize_tag_key(raw)
        if (not key) or (key in seen):
            continue

        if tag_family(key) == "topic":
            if topic_count >= limit_topic:
                continue
            topic_count += 1

        out.append(key)
        seen.add(key)
        if len(out) >= limit_total:
            break

    return out


def _normalize_text_blob(*parts: str) -> str:
    text = " ".join(str(p or "") for p in parts if str(p or "").strip())
    text = text.lower()
    text = text.replace("_", " ").replace("/", " ").replace("\\", " ")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff\-\.\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _vendor_from_mail(from_addr: str) -> str:
    raw = str(from_addr or "").strip().lower()
    if "@" not in raw:
        return ""
    domain = raw.split("@", 1)[-1].strip()
    if not domain:
        return ""
    parts = [p for p in domain.split(".") if p]
    if len(parts) < 2:
        return ""
    vendor = parts[-2]
    if vendor in _GENERIC_MAIL_DOMAINS:
        return ""
    key = normalize_tag_key(f"vendor:{vendor}")
    return key


def _tags_from_keyword_map(text_blob: str, family: str, mapping: dict[str, str]) -> list[str]:
    out: list[str] = []
    for token, value in mapping.items():
        token_norm = _normalize_text_blob(token)
        if (not token_norm) or (token_norm not in text_blob):
            continue
        key = normalize_tag_key(f"{family}:{value}")
        if key:
            out.append(key)
    return out


def _topic_tags_from_whitelist(text_blob: str) -> list[str]:
    rules = load_tag_rules()
    out: list[str] = []
    for topic in rules.topic_whitelist:
        token = _normalize_text_blob(topic)
        if (not token) or (token not in text_blob):
            continue
        key = normalize_tag_key(f"topic:{topic}")
        if key:
            out.append(key)
    return out


def _is_redundant_topic_for_category(topic_key: str, category_path: str) -> bool:
    family, value = split_tag_key(topic_key)
    if family != "topic":
        return False

    path = str(category_path or "").strip().lower()
    if not path:
        return False

    if path.startswith("finance/bills/electricity") and value in {"electricity", "electricity-bill", "invoice", "bill"}:
        return True
    if path.startswith("finance/bills/water") and value in {"water", "water-bill", "invoice", "bill"}:
        return True
    if path.startswith("finance/bills/gas") and value in {"gas", "gas-bill", "invoice", "bill"}:
        return True
    if path.startswith("finance/bills/internet") and value in {"internet", "internet-bill", "invoice", "bill"}:
        return True
    return False


def infer_auto_tags(
    *,
    file_name: str,
    source_path: str,
    source_type: str,
    summary_en: str,
    summary_zh: str,
    content_excerpt: str,
    category_path: str,
    mail_from: str = "",
    mail_subject: str = "",
) -> list[str]:
    text_blob = _normalize_text_blob(file_name, source_path, summary_en, summary_zh, content_excerpt, mail_subject)

    candidates: list[str] = []

    if str(source_type or "").strip().lower() == "mail":
        vendor_key = _vendor_from_mail(mail_from)
        if vendor_key:
            candidates.append(vendor_key)

    candidates.extend(_tags_from_keyword_map(text_blob, "vendor", _VENDOR_KEYWORDS))
    candidates.extend(_tags_from_keyword_map(text_blob, "device", _DEVICE_KEYWORDS))
    candidates.extend(_tags_from_keyword_map(text_blob, "project", _PROJECT_KEYWORDS))
    candidates.extend(_tags_from_keyword_map(text_blob, "person", _PERSON_KEYWORDS))
    candidates.extend(_tags_from_keyword_map(text_blob, "pet", _PET_KEYWORDS))
    candidates.extend(_tags_from_keyword_map(text_blob, "location", _LOCATION_KEYWORDS))
    candidates.extend(_topic_tags_from_whitelist(text_blob))

    normalized = trim_tag_limits(candidates)
    cleaned: list[str] = []
    for key in normalized:
        if _is_redundant_topic_for_category(key, category_path):
            continue
        cleaned.append(key)
    return trim_tag_limits(cleaned)


def summarize_tag_families(tag_keys: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in tag_keys:
        fam = tag_family(key)
        if not fam:
            continue
        out[fam] = out.get(fam, 0) + 1
    return out


# Backward-compat helper for tests and scripts.
def reload_tag_rules_cache() -> None:
    load_tag_rules.cache_clear()
    os.environ["FAMILY_VAULT_TAG_RULES_RELOAD_TOKEN"] = str(os.times())
