import datetime as dt
import re
from typing import Any

from app.schemas import DetailEvidenceRef, DetailRow
from app.services.agent_constants import _DETAIL_SCHEMA, _DETAIL_TOPIC_MAP


def _cap_detail_sections(sections: list, *, max_total_chars: int = 2500) -> list:
    """Limit the serialised size of detail_sections to max_total_chars.

    Without this cap, detail_sections grows unbounded when the slot extractor
    returns many fields, which can push the synthesiser prompt over the
    effective context window of small local models (1.7-4b).

    Handles both plain dicts and Pydantic model instances (DetailSection).
    """
    import json as _json

    def _to_dict(obj: Any) -> dict:
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
            try:
                return obj.model_dump()
            except Exception:
                pass
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        return {}

    result = []
    remaining = max_total_chars
    for section_raw in sections:
        if remaining <= 0:
            break
        section = _to_dict(section_raw)
        rows = list(section.get("rows") or [])
        capped_rows = []
        for row_raw in rows:
            if remaining <= 0:
                break
            row = _to_dict(row_raw)
            row_json = _json.dumps(row, ensure_ascii=False)
            if len(row_json) > remaining:
                # Truncate the value fields to fit
                row = dict(row)
                for k in ("value_en", "value_zh", "value"):
                    if k in row and isinstance(row[k], str) and len(row[k]) > 60:
                        row[k] = row[k][:60] + "…"
            row_json = _json.dumps(row, ensure_ascii=False)
            capped_rows.append(row)
            remaining -= len(row_json)
        if capped_rows:
            result.append({**section, "rows": capped_rows})
    return result


def _json_safe_value(value: Any) -> Any:
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        try:
            return _json_safe_value(value.model_dump())
        except Exception:
            return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    return value


def _resolve_detail_topic(query: str, planner_scope: dict[str, Any] | None = None) -> str:
    hint = str((planner_scope or {}).get("topic_hint") or "").strip().lower()
    if hint in {
        "insurance",
        "bill",
        "warranty",
        "contract",
        "generic",
        "pets",
        "home",
        "appliances",
    }:
        return hint
    lowered = str(query or "").lower()
    for topic, tokens in _DETAIL_TOPIC_MAP.items():
        if any(token in lowered for token in tokens):
            return topic
    return "generic"


def _extract_evidence_value(text: str, topic: str, field: str) -> str:
    raw = str(text or "")
    lowered = raw.lower()
    if not raw.strip():
        return ""
    if field in {
        "due_date",
        "effective",
        "expiry",
        "start",
        "end",
        "date",
        "birth_date",
    }:
        _BIRTH_KWS = ("born", "birth", "dob", "生日", "出生", "birthday")
        _BIRTH_ANTI = (
            "vaccin",
            "inject",
            "接种",
            "疫苗",
            "immunis",
            "desex",
            "steriliz",
            "castrat",
            "spay",
            "neuter",
            "surgery",
            "procedure",
            "operation",
            "去势",
            "绝育",
            "手术",
        )
        _EXPIRY_KWS = ("expir", "renew", "until", "到期", "有效至", "截止", "due")
        _EFFECT_KWS = (
            "effective",
            "from",
            "start",
            "commence",
            "生效",
            "起始",
            "begin",
        )

        def _ctx_ok(m, field_name):
            if field_name not in {"birth_date", "effective", "expiry"}:
                return True
            ctx_s = max(0, m.start() - 150)
            ctx_e = min(len(raw), m.end() + 150)
            ctx = raw[ctx_s:ctx_e].lower()
            if field_name == "birth_date":
                # Labels always PRECEDE their values; only check pre-context (≤35 chars)
                pre_ctx = raw[max(0, m.start() - 35) : m.start()].lower()
                if not any(kw in pre_ctx for kw in _BIRTH_KWS):
                    return False
                # Anti-keywords: check only the immediate label (25 chars before date)
                # Narrower than birth window so a distant procedure label can't poison DOB check
                anti_ctx = raw[max(0, m.start() - 25) : m.start()].lower()
                if any(kw in anti_ctx for kw in _BIRTH_ANTI):
                    return False
                return True
            if field_name == "expiry":
                return any(kw in ctx for kw in _EXPIRY_KWS)
            if field_name == "effective":
                return any(kw in ctx for kw in _EFFECT_KWS)
            return True

        # Use finditer so rejected matches are skipped and the next occurrence is tried
        for m in re.finditer(r"(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}日?)", raw):
            if _ctx_ok(m, field):
                return m.group(1)
        _mo = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        for m in re.finditer(r"\b(\d{1,2})\s+" + _mo + r"\s+(20\d{2})\b", raw, re.I):
            if _ctx_ok(m, field):
                return m.group(0).strip()
        for m in re.finditer(_mo + r"\s+(\d{1,2}),?\s+(20\d{2})\b", raw, re.I):
            if _ctx_ok(m, field):
                return m.group(0).strip()
        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", raw):
            if _ctx_ok(m, field):
                return m.group(0)
        # Pattern 4b: DD-MM-YYYY with dashes (Australian format: "04-11-2024")
        for m in re.finditer(r"\b(\d{1,2})-(\d{1,2})-(20\d{2})\b", raw):
            if _ctx_ok(m, field):
                return m.group(0)
    if field in {
        "purchase_date",
        "warranty_end",
        "service_date",
        "maintenance_date",
        "vaccine_date",
        "next_due",
    }:
        m = re.search(r"(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}日?)", raw)
        if m:
            return m.group(1)
        _mo = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        m = re.search(r"\b(\d{1,2})\s+" + _mo + r"\s+(20\d{2})\b", raw, re.I)
        if m:
            return m.group(0).strip()
        m = re.search(_mo + r"\s+(\d{1,2}),?\s+(20\d{2})\b", raw, re.I)
        if m:
            return m.group(0).strip()
        m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", raw)
        if m:
            return m.group(0)
        # Pattern 4b: DD-MM-YYYY with dashes (Australian format: "04-11-2024")
        m = re.search(r"\b(\d{1,2})-(\d{1,2})-(20\d{2})\b", raw)
        if m:
            return m.group(0)
    if field == "currency":
        if re.search(r"\baud\b", lowered):
            return "AUD"
        if re.search(r"\busd\b", lowered):
            return "USD"
        if re.search(r"\bnzd\b", lowered):
            return "NZD"
        if re.search(r"\bgbp\b", lowered):
            return "GBP"
        if "$" in raw:
            return "AUD"
    if field in {"amount", "premium"}:
        m = re.search(r"(?:aud|澳币|\$)\s?(\d+(?:\.\d{1,2})?)", lowered, flags=re.I)
        if m:
            amount = m.group(1)
            return f"AUD {amount}"
    if field == "monthly_payment":
        m = re.search(
            r"(?:monthly|月供).{0,12}(?:aud|澳币|\$)?\s?(\d+(?:\.\d{1,2})?)",
            lowered,
            flags=re.I,
        )
        if m:
            return f"AUD {m.group(1)}"
        m = re.search(r"(?:aud|澳币|\$)\s?(\d+(?:\.\d{1,2})?)", lowered, flags=re.I)
        if m and any(t in lowered for t in ("loan", "mortgage", "月供")):
            return f"AUD {m.group(1)}"
    if field in {"policy_no", "reference", "serial"}:
        m = re.search(r"\b([A-Z0-9][A-Z0-9\-]{5,})\b", raw)
        if m:
            return m.group(1)
    if field in {"insurer", "provider", "vendor"}:
        for token in ("aami", "vmia", "superloop", "agl", "telstra", "daikin", "rheem"):
            if token in lowered:
                return token.upper() if token != "superloop" else "Superloop"
        if field == "vendor":
            if "nbn" in lowered:
                return "NBN"
    if field == "loan_bank":
        for token in ("cba", "commonwealth bank", "anz", "nab", "westpac", "bank"):
            if token in lowered:
                return token.upper() if token in {"cba", "anz", "nab"} else token.title()
    if field in {"payment_status", "status"}:
        if any(token in lowered for token in ("paid", "已缴", "已支付")):
            return "Paid"
        if any(token in lowered for token in ("unpaid", "未缴", "待缴", "due")):
            return "Unpaid"
    if field in {"policy_type", "coverage_scope"}:
        if "vehicle" in lowered or "car" in lowered or "motor" in lowered or "车" in lowered:
            return "Vehicle"
        if "pet" in lowered or "宠物" in lowered:
            return "Pet"
        if "health" in lowered or "hospital" in lowered or "医保" in lowered or "医疗" in lowered:
            return "Health"
    if field in {"brand"}:
        for token in ("daikin", "rheem", "tesla", "bosch", "lg", "samsung", "miele"):
            if token in lowered:
                return token.title()
    if field in {"term_years"}:
        m = re.search(r"(\d{1,2})\s*(?:years|year|年)", lowered)
        if m and any(t in lowered for t in ("loan", "mortgage", "term", "贷款")):
            return m.group(1)
    if field in {"property_area"}:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:sqm|m2|㎡|平方米)", lowered)
        if m:
            return m.group(1) + " m2"
    if field in {"maintenance_item"} and any(t in lowered for t in ("repair", "maintenance", "维修", "保养")):
        line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
        return line[:120]
    if field in {"vaccine_type"} and any(t in lowered for t in ("vaccine", "vaccination", "疫苗")):
        line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
        return line[:120]
    if field in {"vet_name"}:
        if any(t in lowered for t in ("vet", "veterinary", "兽医", "宠物医院")):
            line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
            return line[:120]
    if field in {"vet_contact"}:
        if ("@" in lowered) or re.search(r"\b\d{8,12}\b", lowered):
            line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
            return line[:120]
    if field in {"registration_no", "invoice_no"}:
        m = re.search(r"\b([A-Z0-9][A-Z0-9\-]{4,})\b", raw)
        if m:
            return m.group(1)
    if field == "surgery_record":
        if any(t in lowered for t in ("surgery", "desex", "绝育", "手术")):
            line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
            return line[:120]
    if field in {"bill_name", "policy_name", "title", "product", "model"}:
        _skip_prefixes = (
            "[page ",
            "we have made the change",
            "if you have already paid",
            "please find enclosed",
            "here is your updated",
            "motorcycle insurance",
        )
        _sentence_starts = (
            "safe ",
            "good ",
            "by ",
            "with ",
            "if ",
            "for ",
            "you ",
            "as ",
            "our ",
            "we ",
            "your ",
            "this ",
            "the ",
            "a ",
            "an ",
            "to ",
            "please ",
            "note ",
            "dear ",
            "thank ",
            "in ",
            "on ",
            "at ",
            "from ",
            "whilst ",
            "while ",
            "when ",
            "since ",
            "because ",
            "however ",
        )
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if len(ln) > 80:
                continue
            ln_lower = ln.lower()
            if any(ln_lower.startswith(s) for s in _skip_prefixes):
                continue
            if any(ln_lower.startswith(s) for s in _sentence_starts):
                continue
            return ln[:120]
        return ""
    if field == "period":
        m = re.search(
            r"(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}).{0,20}(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2})",
            raw,
        )
        if m:
            return f"{m.group(1)} - {m.group(2)}"
        _mp = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})"
        m = re.search(_mp + r".{0,15}" + _mp, raw, re.I)
        if m:
            return f"{m.group(1)} {m.group(2)} - {m.group(3)} {m.group(4)}"
        m = re.search(_mp, raw, re.I)
        if m:
            return f"{m.group(1)} {m.group(2)}"
        m = re.search(r"(\d{1,2}/20\d{2}).{0,10}(\d{1,2}/20\d{2})", raw)
        if m:
            return f"{m.group(1)} - {m.group(2)}"
    if field == "bill_name":
        line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
        return line[:120]
    if field in {
        "parties",
        "obligation",
        "penalty",
        "notice_period",
        "action",
        "contact",
    }:
        # Keep short but useful snippet around keyword.
        keywords = {
            "parties": ["party", "parties", "甲方", "乙方"],
            "obligation": ["obligation", "must", "应当", "需"],
            "penalty": ["penalty", "违约", "罚款"],
            "notice_period": ["notice", "通知", "days", "day"],
            "action": ["next", "action", "建议", "需处理"],
            "contact": ["phone", "email", "电话", "邮箱"],
        }
        for kw in keywords.get(field, []):
            idx = lowered.find(kw)
            if idx >= 0:
                start = max(0, idx - 24)
                end = min(len(raw), idx + 84)
                return " ".join(raw[start:end].split())[:120]
    if topic == "generic":
        return " ".join(raw.split())[:80]
    return ""


def _detail_rows_from_chunks(
    *, topic: str, chunks: list[dict[str, Any]], ui_lang: str
) -> tuple[list[DetailRow], list[str]]:
    schema = _DETAIL_SCHEMA.get(topic, _DETAIL_SCHEMA["generic"])
    rows: list[DetailRow] = []
    missing: list[str] = []
    for field, label_en, label_zh in schema:  # noqa: F402
        value = ""
        evidence: list[DetailEvidenceRef] = []
        for chunk in chunks[:10]:
            text = str(chunk.get("text") or "")
            found = _extract_evidence_value(text, topic, field)
            if not found:
                continue
            value = found
            evidence.append(
                DetailEvidenceRef(
                    doc_id=str(chunk.get("doc_id") or ""),
                    chunk_id=str(chunk.get("chunk_id") or ""),
                    evidence_text=" ".join(text.split())[:180],
                )
            )
            break
        if not value:
            missing.append(label_zh if ui_lang == "zh" else label_en)
        rows.append(
            DetailRow(
                field=field,
                label_en=label_en,
                label_zh=label_zh,
                value_en=value,
                value_zh=value,
                evidence_refs=evidence[:1],
            )
        )
    return (rows, missing)
