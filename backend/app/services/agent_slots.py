import datetime as dt
import re
from typing import Any

from app.services import evidence_patterns as ep
from app.services.agent_queryspec import slot_query_terms


_SLOT_LABELS: dict[str, tuple[str, str]] = {
    "policy_no": ("Policy Number", "保单编号"),
    "beneficiary": ("Beneficiary", "受益人"),
    "premium_amount": ("Premium Amount", "保费金额"),
    "expiry_date": ("Expiry Date", "到期日期"),
    "coverage_scope": ("Coverage Scope", "保障范围"),
    "claim_status": ("Claim Status", "理赔状态"),
    "emergency_contact_phone": ("Emergency Contact Phone", "紧急联络电话"),
    "brand": ("Brand", "品牌"),
    "model": ("Model", "型号"),
    "purchase_date": ("Purchase Date", "购买日期"),
    "warranty_end": ("Warranty End", "保修截止"),
    "maintenance_interval": ("Maintenance Interval", "保养/清洁间隔"),
    "maintenance_steps": ("Maintenance Steps", "维护步骤"),
    "maintenance_notes": ("Maintenance Notes", "维护说明"),
    "maintenance_warning": ("Maintenance Warning", "维护警示"),
    "work_order_no": ("Work Order Number", "工单号"),
    "engineer_phone": ("Engineer Phone", "工程师电话"),
    "invoice_no": ("Invoice Number", "发票号码"),
    "monthly_payment": ("Monthly Payment", "月供金额"),
    "loan_bank": ("Loan Bank", "贷款银行"),
    "loan_term_years": ("Loan Term Years", "贷款年限"),
    "loan_start_date": ("Loan Start Date", "贷款开始日期"),
    "loan_maturity_date": ("Loan Maturity Date", "贷款到期日期"),
    "property_area": ("Property Area", "房屋面积"),
    "vaccine_date_last": ("Last Vaccine Date", "上次疫苗日期"),
    "vaccine_interval": ("Vaccine Interval", "疫苗间隔"),
    "vaccine_next_due": ("Next Vaccine Due", "下次补打日期"),
    "vet_name": ("Vet/Clinic Name", "宠物医院/兽医"),
    "vet_contact": ("Vet Contact", "宠物医院联系方式"),
    "surgery_record": ("Surgery Record", "手术记录"),
    "registration_no": ("Registration Number", "登记证号"),
    "bill_amount": ("Bill Amount", "账单金额"),
    "due_date": ("Due Date", "截止日期"),
    "billing_period": ("Billing Period", "计费周期"),
    "payment_status": ("Payment Status", "支付状态"),
    "vendor": ("Vendor/Provider", "供应商"),
    "contact_phone": ("Contact Phone", "联系方式电话"),
    "contact_email": ("Contact Email", "联系方式邮箱"),
    "provider": ("Provider", "提供方"),
    "reference_no": ("Reference Number", "参考编号"),
    "date": ("Date", "日期"),
    "amount": ("Amount", "金额"),
    "presence_evidence": ("Presence Evidence", "存在性证据"),
    "status_evidence": ("Status Evidence", "状态证据"),
}

_DATE_SLOTS = {
    "expiry_date",
    "purchase_date",
    "warranty_end",
    "loan_start_date",
    "loan_maturity_date",
    "vaccine_date_last",
    "vaccine_next_due",
    "due_date",
    "date",
}
_AMOUNT_SLOTS = {"premium_amount", "monthly_payment", "bill_amount", "amount"}
_PHONE_SLOTS = {"emergency_contact_phone", "engineer_phone", "contact_phone", "vet_contact"}
_EMAIL_SLOTS = {"contact_email"}
_REFERENCE_SLOTS = {"policy_no", "work_order_no", "invoice_no", "registration_no", "reference_no"}

_BRANDS = ("daikin", "rheem", "bosch", "smeg", "miele", "lg", "samsung", "tesla")
_BANKS = ("commonwealth bank", "westpac", "anz", "nab", "cba", "bank of melbourne")
_STATUS_POS = ("approved", "accepted", "paid", "completed", "获批", "已缴", "已支付", "同意")
_STATUS_NEG = ("declined", "rejected", "denied", "unpaid", "pending", "拒赔", "未缴", "待缴", "未批准")
_INTERNET_ANCHORS = ("internet", "broadband", "nbn", "superloop", "网费", "网络", "宽带")
_NEG_BILL_ANCHORS = ("electricity", "power", "gas", "water", "电费", "燃气", "水费", "yvw", "yarra valley water", "globird")
_BILL_CONTACT_SLOTS = {"vendor", "provider", "contact_phone", "contact_email", "bill_amount", "billing_period"}
_HOWTO_ACTION_TOKENS = (
    "check",
    "clean",
    "inspect",
    "replace",
    "flush",
    "close",
    "open",
    "维护",
    "保养",
    "清洁",
    "检查",
    "更换",
    "冲洗",
    "关闭",
    "打开",
)
_HOWTO_OBJECT_TOKENS = ("water tank", "tank", "filter", "过滤网", "水箱")
_HOWTO_NOTE_TOKENS = ("responsibility", "owner", "recommend", "建议", "责任", "需", "should")
_HOWTO_WARN_TOKENS = ("warning", "caution", "danger", "注意", "警告")


def _label(slot: str) -> tuple[str, str]:
    return _SLOT_LABELS.get(slot, (slot.replace("_", " ").title(), slot))


def _safe_text(value: Any, cap: int = 180) -> str:
    raw = " ".join(str(value or "").split())
    if len(raw) <= cap:
        return raw
    return raw[:cap].rstrip() + "..."


def _looks_like_raw_page_snippet(value: str) -> bool:
    raw = " ".join(str(value or "").split()).strip()
    if not raw:
        return False
    return bool(re.match(r"^\[page\s*\d+\]", raw, flags=re.I))


def _should_suppress_slot_value(*, slot: str, value: str, confidence: float, query_spec: dict[str, Any]) -> bool:
    _ = (slot, confidence, query_spec)  # keep signature extensible for future heuristics
    return _looks_like_raw_page_snippet(value)


def _split_sentences_loose(text: str) -> list[str]:
    raw = str(text or "")
    if not raw.strip():
        return []
    chunks = re.split(r"(?:[\r\n]+|(?<=[。！？.!?;；]))\s*", raw)
    out: list[str] = []
    for item in chunks:
        line = " ".join(str(item or "").split()).strip()
        if not line:
            continue
        out.append(line)
    return out


def _looks_low_quality_howto_snippet(text: str) -> bool:
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return True
    low = s.lower()
    if len(s) < 12:
        return True
    if low.startswith(("onsibility", "esponsibility")) or " t op " in low:
        return True
    # very weak semantic signal for how-to/interval snippets
    if not any(tok in low for tok in ("day", "week", "month", "year", "每", "定期", "regular", "check", "clean", "维护", "保养", "清洁", "步骤")):
        return True
    return False


def _looks_low_quality_howto_steps(text: str) -> bool:
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return True
    low = s.lower()
    if len(s) < 18:
        return True
    if "page " in low and not any(tok in low for tok in _HOWTO_OBJECT_TOKENS):
        return True
    if "owners corporation" in low and not any(tok in low for tok in ("check", "clean", "inspect", "replace", "检查", "清洁", "维护")):
        return True
    if "plumbing" in low and not any(tok in low for tok in _HOWTO_OBJECT_TOKENS):
        return True
    has_action = any(tok in low for tok in _HOWTO_ACTION_TOKENS)
    has_object = any(tok in low for tok in _HOWTO_OBJECT_TOKENS)
    if not has_action:
        return True
    if not has_object:
        return True
    return False


def _pick_howto_sentences(text: str, *, keywords: tuple[str, ...], max_items: int = 2) -> list[str]:
    out: list[str] = []
    for line in _split_sentences_loose(text):
        low = line.lower()
        score = 0
        if any(tok in low for tok in keywords):
            score += 2
        if any(tok in low for tok in _HOWTO_OBJECT_TOKENS):
            score += 1
        if re.search(r"(^|\s)(?:\d+[\.\)]|first|then|finally|首先|然后|最后)", low):
            score += 1
        if score <= 0:
            continue
        if _looks_low_quality_howto_snippet(line) and score < 3:
            continue
        if line not in out:
            out.append(line)
        if len(out) >= max_items:
            break
    return out


def _augment_howto_chunks_with_neighbors(context_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Build per-doc ordered lists by chunk_index, then attach a neighbor-merged text for how-to slot extraction only.
    by_doc: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for pos, chunk in enumerate(context_chunks or []):
        doc_id = str(chunk.get("doc_id") or "")
        if not doc_id:
            continue
        try:
            idx = int(chunk.get("chunk_index") or 0)
        except Exception:
            idx = 0
        by_doc.setdefault(doc_id, []).append((idx, pos, chunk))
    neighbors: dict[int, str] = {}
    for _doc_id, rows in by_doc.items():
        rows_sorted = sorted(rows, key=lambda x: (x[0], x[1]))
        for i, (_idx, pos, cur) in enumerate(rows_sorted):
            parts: list[str] = []
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(rows_sorted):
                    parts.append(str(rows_sorted[j][2].get("text") or ""))
            merged = "\n".join(p for p in parts if p.strip())
            merged = re.sub(r"[ \t]+", " ", merged).strip()
            if len(merged) > 800:
                merged = merged[:800]
            neighbors[pos] = merged
    out: list[dict[str, Any]] = []
    for pos, chunk in enumerate(context_chunks or []):
        copied = dict(chunk)
        if pos in neighbors:
            copied["howto_candidate_text"] = neighbors[pos]
        out.append(copied)
    return out


def _add_result(results: list[dict[str, Any]], *, slot: str, value: str, normalized_value: str = "", confidence: float = 0.8, chunk: dict[str, Any] | None = None, status: str = "found") -> None:
    if not str(value or "").strip() and status == "found":
        return
    if any(str(item.get("slot") or "") == slot and str(item.get("normalized_value") or item.get("value") or "") == (normalized_value or value) for item in results):
        return
    label_en, label_zh = _label(slot)
    evidence_refs = []
    source_doc_ids = []
    if isinstance(chunk, dict):
        doc_id = str(chunk.get("doc_id") or "")
        chunk_id = str(chunk.get("chunk_id") or "")
        if doc_id:
            source_doc_ids.append(doc_id)
        evidence_refs.append(
            {
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "evidence_text": _safe_text(chunk.get("text"), cap=180),
            }
        )
    results.append(
        {
            "slot": slot,
            "label_en": label_en,
            "label_zh": label_zh,
            "value": str(value or ""),
            "normalized_value": str(normalized_value or value or ""),
            "status": status,
            "confidence": float(confidence),
            "evidence_refs": evidence_refs,
            "source_doc_ids": source_doc_ids,
        }
    )


def _keyword_near(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(k in lowered for k in keywords)


def _numeric_from_amount_text(value: str) -> float | None:
    raw = str(value or "")
    m = re.search(r"(-?\d[\d,]*\.?\d*)", raw)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def _prefer_bill_amount_values(vals: list[str]) -> list[str]:
    def _score(v: str) -> tuple[float, str]:
        s = str(v or "")
        low = s.lower()
        n = _numeric_from_amount_text(s)
        score = 0.0
        if "." in s:
            score += 2.0
        if any(tok in low for tok in ("aud", "$", "usd", "eur")):
            score += 1.0
        if n is not None and float(n).is_integer() and 1900 <= n <= 2100 and "." not in s:
            score -= 5.0
        if n is not None and n <= 0:
            score -= 0.5
        return (score, s)

    return [v for v in sorted(vals, key=_score, reverse=True)]


def _bill_amount_candidate_confidence(text: str, value: str, all_vals: list[str]) -> float:
    low = str(text or "").lower()
    v = str(value or "")
    base = 0.75
    n = _numeric_from_amount_text(v)
    if n is not None and "." in v:
        base += 0.03
    idx = low.find(v.lower())
    if idx >= 0:
        window = low[max(0, idx - 48) : idx + max(len(v), 1) + 48]
        if any(tok in window for tok in ("total", "total amount", "amount due", "bill total", "summary")):
            base += 0.18
        if any(tok in window for tok in ("amount", "due")):
            base += 0.08
        if any(tok in window for tok in ("gst", "fee", "charge", "surcharge", "credit", "discount")):
            base -= 0.14
    if n is not None:
        bigger = [(_numeric_from_amount_text(x) or 0.0) for x in all_vals if (_numeric_from_amount_text(x) or 0.0) > n + 5]
        if n < 20 and bigger:
            base -= 0.12
        if n >= 50:
            base += 0.04
    return max(0.2, min(0.95, base))


def _chunk_haystack(chunk: dict[str, Any]) -> str:
    return " | ".join(
        [
            str(chunk.get("title_zh") or ""),
            str(chunk.get("title_en") or ""),
            str(chunk.get("file_name") or ""),
            str(chunk.get("category_path") or ""),
            str(chunk.get("text") or ""),
        ]
    ).lower()


def _queryspec_internet_bill_context(query_spec: dict[str, Any]) -> bool:
    spec = dict(query_spec or {})
    if str(spec.get("subject_domain") or "") != "bills":
        return False
    preferred = [str(x or "").lower() for x in (spec.get("preferred_categories") or [])]
    aliases = [str(x or "").lower() for x in (spec.get("subject_aliases") or [])]
    slots = {str(x or "") for x in (spec.get("target_slots") or [])}
    if any(c.startswith("finance/bills/internet") for c in preferred):
        return True
    if any(any(tok in a for tok in _INTERNET_ANCHORS) for a in aliases):
        return True
    if {"vendor", "contact_phone", "contact_email"} & slots:
        return True
    return False


def _slot_candidate_context_bonus(*, slot: str, query_spec: dict[str, Any], chunk: dict[str, Any], candidate: dict[str, Any]) -> float:
    if slot not in _BILL_CONTACT_SLOTS or not _queryspec_internet_bill_context(query_spec):
        return 0.0
    hay = _chunk_haystack(chunk)
    cat = str(chunk.get("category_path") or "").lower()
    value = str(candidate.get("value") or "").lower()
    bonus = 0.0
    has_internet_anchor = any(tok in hay for tok in _INTERNET_ANCHORS)
    has_neg_anchor = any(tok in hay for tok in _NEG_BILL_ANCHORS)

    if cat.startswith("finance/bills/internet"):
        bonus += 0.35
    elif cat.startswith("finance/bills/"):
        bonus += 0.08
    if has_internet_anchor:
        bonus += 0.22
    if has_neg_anchor and not has_internet_anchor:
        bonus -= 0.28
    if cat.startswith(("finance/bills/water", "finance/bills/electricity", "finance/bills/gas")):
        bonus -= 0.22

    if slot in {"vendor", "provider"}:
        if "superloop" in hay or "superloop" in value:
            bonus += 0.45
        if any(x in hay for x in ("provider", "供应商", "服务商", "运营商")):
            bonus += 0.08
        if any(x in value for x in ("yvw", "globird")):
            bonus -= 0.35
    elif slot == "contact_email":
        if "superloop" in value or "superloop" in hay:
            bonus += 0.5
        if any(x in value for x in ("yvw.com.au", "globird")):
            bonus -= 0.4
    elif slot == "contact_phone":
        if "superloop" in hay or "billing@" in hay:
            bonus += 0.22
        if any(x in hay for x in ("yvw.com.au", "yarra valley water", "globird")):
            bonus -= 0.28
    elif slot == "bill_amount":
        if "superloop" in hay or any(x in hay for x in ("internet bill", "网络账单", "宽带")):
            bonus += 0.28
        if any(x in hay for x in ("previous bill", "summarypreviousbill")) and not has_internet_anchor:
            bonus -= 0.12
    elif slot == "billing_period":
        if has_internet_anchor:
            bonus += 0.18
    return bonus


def _extract_generic_slot(slot: str, chunk: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(chunk.get("text") or "")
    howto_text = str(chunk.get("howto_candidate_text") or text)
    lowered = text.lower()
    results: list[dict[str, Any]] = []

    if slot in _DATE_SLOTS:
        for val in ep.find_dates(text):
            _add_result(results, slot=slot, value=val, normalized_value=val, confidence=0.82, chunk=chunk)
            break
        return results

    if slot in _AMOUNT_SLOTS:
        if slot == "monthly_payment":
            vals = ep.find_monthly_payment(text) or ep.find_amounts(text)
        elif slot == "bill_amount":
            vals = _prefer_bill_amount_values(ep.find_amounts(text))
        else:
            vals = ep.find_amounts(text)
        if slot == "bill_amount":
            for val in vals[:4]:
                _add_result(
                    results,
                    slot=slot,
                    value=val,
                    normalized_value=val,
                    confidence=_bill_amount_candidate_confidence(text, val, vals),
                    chunk=chunk,
                )
            return results
        for val in vals:
            _add_result(results, slot=slot, value=val, normalized_value=val, confidence=0.85, chunk=chunk)
            break
        return results

    if slot in _PHONE_SLOTS:
        phones = ep.find_phones(text)
        if slot == "engineer_phone" and phones and not _keyword_near(text, ("engineer", "technician", "维修", "工程师", "service")):
            return results
        if slot == "emergency_contact_phone" and phones and not _keyword_near(text, ("emergency", "urgent", "hotline", "紧急", "assistance")):
            return results
        for val in phones:
            _add_result(results, slot=slot, value=val, normalized_value=val, confidence=0.88, chunk=chunk)
            break
        return results

    if slot in _EMAIL_SLOTS:
        for val in ep.find_emails(text):
            _add_result(results, slot=slot, value=val, normalized_value=val.lower(), confidence=0.9, chunk=chunk)
            break
        return results

    if slot in _REFERENCE_SLOTS:
        refs = ep.find_references(text)
        if slot == "policy_no":
            refs = [x for x in refs if any(ch.isdigit() for ch in x)]
            refs = [x for x in refs if _keyword_near(text, ("policy", "保单"))] or refs
        if slot == "invoice_no":
            refs = [x for x in refs if any(ch.isdigit() for ch in x)]
            refs = [x for x in refs if _keyword_near(text, ("invoice", "发票", "inv"))] or refs
        if slot == "work_order_no":
            refs = [x for x in refs if any(ch.isdigit() for ch in x)]
            refs = [x for x in refs if _keyword_near(text, ("work order", "ticket", "工单", "维修"))] or refs
        for val in refs:
            _add_result(results, slot=slot, value=val, normalized_value=val, confidence=0.84, chunk=chunk)
            break
        return results

    if slot == "property_area":
        for val in ep.find_area_sqm(text):
            _add_result(results, slot=slot, value=val, normalized_value=val, confidence=0.9, chunk=chunk)
            break
        return results

    if slot == "maintenance_interval":
        vals = ep.find_interval_phrases(howto_text)
        from_interval_regex = bool(vals)
        if not vals and _keyword_near(howto_text, ("filter", "过滤网", "clean", "清洁", "maintenance", "保养", "维护", "tank", "水箱")):
            vals = [ep.best_snippet(howto_text, ["filter", "过滤网", "clean", "清洁", "maintenance", "保养", "维护", "tank", "水箱"], cap=140)]
        for val in vals:
            low_quality = _looks_low_quality_howto_snippet(val)
            if low_quality and not from_interval_regex:
                continue
            conf = 0.74 if not low_quality else 0.46
            _add_result(results, slot=slot, value=val, normalized_value=val, confidence=conf, chunk=chunk)
        return results

    if slot == "maintenance_steps":
        steps = _pick_howto_sentences(howto_text, keywords=_HOWTO_ACTION_TOKENS, max_items=2)
        if steps:
            joined = "；".join(steps)
            _add_result(results, slot=slot, value=joined, normalized_value=joined, confidence=0.82, chunk=chunk)
        return results

    if slot == "maintenance_notes":
        notes = _pick_howto_sentences(howto_text, keywords=_HOWTO_NOTE_TOKENS, max_items=2)
        if notes:
            joined = "；".join(notes)
            _add_result(results, slot=slot, value=joined, normalized_value=joined, confidence=0.66, chunk=chunk)
        return results

    if slot == "maintenance_warning":
        warns = _pick_howto_sentences(howto_text, keywords=_HOWTO_WARN_TOKENS, max_items=2)
        if warns:
            joined = "；".join(warns)
            _add_result(results, slot=slot, value=joined, normalized_value=joined, confidence=0.7, chunk=chunk)
        return results

    if slot == "brand":
        for brand in _BRANDS:
            if brand in lowered:
                _add_result(results, slot=slot, value=brand.title(), normalized_value=brand, confidence=0.88, chunk=chunk)
                break
        return results

    if slot == "model":
        m = re.search(r"\b(?:model|型号)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,24})\b", text, flags=re.I)
        if not m:
            m = re.search(r"\b([A-Z]{1,4}-?\d{2,}[A-Z0-9-]*)\b", text)
        if m:
            val = m.group(1)
            _add_result(results, slot=slot, value=val, normalized_value=val.upper(), confidence=0.78, chunk=chunk)
        return results

    if slot == "loan_bank":
        for bank in _BANKS:
            if bank in lowered:
                pretty = bank.upper() if bank in {"anz", "nab", "cba"} else bank.title()
                _add_result(results, slot=slot, value=pretty, normalized_value=bank, confidence=0.86, chunk=chunk)
                break
        return results

    if slot == "loan_term_years":
        m = re.search(r"(\d{1,2})\s*(?:years?|年)", lowered)
        if m and _keyword_near(text, ("loan", "mortgage", "term", "贷款")):
            _add_result(results, slot=slot, value=m.group(1), normalized_value=m.group(1), confidence=0.82, chunk=chunk)
        return results

    if slot == "billing_period":
        dates = ep.find_dates(text)
        if len(dates) >= 2:
            val = f"{dates[0]} - {dates[1]}"
            _add_result(results, slot=slot, value=val, normalized_value=val, confidence=0.78, chunk=chunk)
        return results

    if slot in {"payment_status", "claim_status"}:
        low = lowered
        for tok in _STATUS_POS:
            if tok in low:
                _add_result(results, slot=slot, value="Approved" if slot == "claim_status" else "Paid", normalized_value=tok, confidence=0.72, chunk=chunk)
                return results
        for tok in _STATUS_NEG:
            if tok in low:
                _add_result(results, slot=slot, value="Rejected" if slot == "claim_status" else "Unpaid", normalized_value=tok, confidence=0.72, chunk=chunk)
                return results
        return results

    if slot in {"coverage_scope", "surgery_record", "vet_name", "provider", "vendor", "beneficiary"}:
        if slot in {"provider", "vendor"}:
            if "superloop" in lowered:
                _add_result(results, slot=slot, value="Superloop", normalized_value="superloop", confidence=0.95, chunk=chunk)
                return results
            if "yvw.com.au" in lowered or "yarra valley water" in lowered or re.search(r"\byvw\b", lowered):
                _add_result(results, slot=slot, value="Yarra Valley Water", normalized_value="yarra valley water", confidence=0.9, chunk=chunk)
                return results
            if "globird" in lowered:
                _add_result(results, slot=slot, value="GloBird Energy", normalized_value="globird energy", confidence=0.9, chunk=chunk)
                return results
        keywords = slot_query_terms(slot) or [slot.replace("_", " ")]
        if any(kw.lower() in lowered for kw in keywords):
            _add_result(results, slot=slot, value=ep.best_snippet(text, keywords, cap=140), normalized_value="", confidence=0.65, chunk=chunk)
        elif slot == "vendor":
            # bills provider fallback from title snippet.
            _add_result(results, slot=slot, value=ep.best_snippet(text, ["bill", "invoice", "provider", "supplier"], cap=80), confidence=0.45, chunk=chunk)
        return results

    if slot == "contact_email":
        for email in ep.find_emails(text):
            _add_result(results, slot=slot, value=email, normalized_value=email.lower(), confidence=0.9, chunk=chunk)
            break
        return results

    if slot == "contact_phone":
        for phone in ep.find_phones(text):
            _add_result(results, slot=slot, value=phone, normalized_value=phone, confidence=0.88, chunk=chunk)
            break
        return results

    if slot == "presence_evidence":
        if ep.contains_presence_evidence(text):
            _add_result(results, slot=slot, value="present", normalized_value="present", confidence=0.55, chunk=chunk)
        return results

    if slot == "status_evidence":
        if ep.contains_status_evidence(text):
            _add_result(results, slot=slot, value="status_found", normalized_value="status_found", confidence=0.55, chunk=chunk)
        return results

    return results


def extract_slots_from_chunks(
    *,
    query_spec: dict[str, Any],
    context_chunks: list[dict[str, Any]],
    subtasks: list[dict[str, Any]] | None = None,
    include_generic_fallback: bool = True,
) -> list[dict[str, Any]]:
    target_slots = [str(item or "").strip() for item in (query_spec.get("target_slots") or []) if str(item or "").strip()]
    if not target_slots and subtasks:
        target_slots = [str(task.get("slot") or "").strip() for task in subtasks if str(task.get("slot") or "").strip()]
    if include_generic_fallback:
        if bool(query_spec.get("needs_presence_evidence")) and "presence_evidence" not in target_slots:
            target_slots.append("presence_evidence")
        if bool(query_spec.get("needs_status_evidence")) and "status_evidence" not in target_slots:
            target_slots.append("status_evidence")
    task_kind = str(query_spec.get("task_kind") or "")
    is_howto = task_kind == "howto_lookup" or ("maintenance_interval" in target_slots)
    if is_howto:
        for extra in ("maintenance_steps", "maintenance_notes", "maintenance_warning"):
            if extra not in target_slots:
                target_slots.append(extra)

    candidate_chunks = _augment_howto_chunks_with_neighbors(context_chunks) if is_howto else list(context_chunks or [])

    results: list[dict[str, Any]] = []
    for slot in target_slots[:16]:
        slot_candidates: list[tuple[float, int, dict[str, Any], dict[str, Any]]] = []
        for chunk in candidate_chunks[:20]:
            candidates = _extract_generic_slot(slot, chunk)
            if not candidates:
                continue
            for item in candidates:
                base_conf = float(item.get("confidence") or 0.5)
                score = base_conf + _slot_candidate_context_bonus(slot=slot, query_spec=query_spec, chunk=chunk, candidate=item)
                if slot == "maintenance_steps":
                    if _looks_low_quality_howto_steps(str(item.get("value") or "")):
                        score -= 0.2
                    else:
                        score += 0.08
                elif slot == "maintenance_interval" and _looks_low_quality_howto_snippet(str(item.get("value") or "")):
                    score -= 0.18
                slot_candidates.append((score, -len(slot_candidates), item, chunk))
        if slot_candidates:
            slot_candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
            best_score, _ord, best_item, best_chunk = slot_candidates[0]
            best_slot = str(best_item.get("slot") or slot)
            best_value = str(best_item.get("value") or "")
            best_norm = str(best_item.get("normalized_value") or "")
            best_conf = max(0.0, min(1.0, best_score))
            best_status = str(best_item.get("status") or "found")
            if _should_suppress_slot_value(slot=best_slot, value=best_value, confidence=best_conf, query_spec=query_spec):
                label_en, label_zh = _label(best_slot)
                results.append(
                    {
                        "slot": best_slot,
                        "label_en": label_en,
                        "label_zh": label_zh,
                        "value": "",
                        "normalized_value": "",
                        "status": "missing",
                        "confidence": 0.0,
                        "evidence_refs": [],
                        "source_doc_ids": [],
                    }
                )
            else:
                _add_result(
                    results,
                    slot=best_slot,
                    value=best_value,
                    normalized_value=best_norm,
                    confidence=best_conf,
                    chunk=best_chunk,
                    status=best_status,
                )
        else:
            label_en, label_zh = _label(slot)
            results.append(
                {
                    "slot": slot,
                    "label_en": label_en,
                    "label_zh": label_zh,
                    "value": "",
                    "normalized_value": "",
                    "status": "missing",
                    "confidence": 0.0,
                    "evidence_refs": [],
                    "source_doc_ids": [],
                }
            )
    return results


def derive_facts(*, slot_results: list[dict[str, Any]], derivations: list[str], now: dt.date | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now_date = now or ep.now_utc_date()
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for row in slot_results:
        by_slot.setdefault(str(row.get("slot") or ""), []).append(row)

    def first_val(slot: str) -> str:
        for row in by_slot.get(slot, []):
            if str(row.get("status") or "") == "found" and str(row.get("value") or "").strip():
                return str(row.get("value") or "")
        return ""

    for name in [str(x or "").strip() for x in derivations if str(x or "").strip()]:
        if name == "compare_expiry_to_next_month":
            expiry = first_val("expiry_date") or first_val("due_date") or first_val("warranty_end")
            parsed = ep.parse_date(expiry)
            if parsed is None:
                out.append({"name": name, "status": "partial", "value": "", "depends_on_slots": ["expiry_date"], "evidence_refs": [], "explanation": "missing_expiry_date"})
                continue
            month_after = (now_date.month % 12) + 1
            year_after = now_date.year + (1 if now_date.month == 12 else 0)
            is_next_month = parsed.year == year_after and parsed.month == month_after
            out.append(
                {
                    "name": name,
                    "status": "derived",
                    "value": "yes" if is_next_month else "no",
                    "depends_on_slots": ["expiry_date"],
                    "evidence_refs": [],
                    "explanation": f"expiry={parsed.isoformat()}, next_month={year_after:04d}-{month_after:02d}",
                }
            )
        elif name == "compute_remaining_loan_years":
            maturity = ep.parse_date(first_val("loan_maturity_date"))
            if maturity is None:
                start = ep.parse_date(first_val("loan_start_date"))
                term_raw = first_val("loan_term_years")
                try:
                    term_years = int(float(term_raw)) if term_raw else 0
                except Exception:
                    term_years = 0
                if start and term_years > 0:
                    try:
                        maturity = dt.date(start.year + term_years, start.month, min(start.day, 28))
                    except Exception:
                        maturity = dt.date(start.year + term_years, start.month, 1)
            if maturity is None:
                out.append({"name": name, "status": "partial", "value": "", "depends_on_slots": ["loan_term_years", "loan_start_date", "loan_maturity_date"], "evidence_refs": [], "explanation": "missing_maturity_inputs"})
                continue
            delta_days = (maturity - now_date).days
            years = max(0.0, round(delta_days / 365.25, 1))
            out.append(
                {
                    "name": name,
                    "status": "derived",
                    "value": str(years),
                    "depends_on_slots": ["loan_term_years", "loan_start_date", "loan_maturity_date"],
                    "evidence_refs": [],
                    "explanation": f"maturity={maturity.isoformat()} now={now_date.isoformat()}",
                }
            )
        elif name == "estimate_next_vaccine_due":
            nxt = first_val("vaccine_next_due")
            if nxt:
                out.append({"name": name, "status": "derived", "value": nxt, "depends_on_slots": ["vaccine_next_due"], "evidence_refs": [], "explanation": "explicit_next_due"})
                continue
            last = ep.parse_date(first_val("vaccine_date_last"))
            interval = first_val("vaccine_interval")
            months = 0
            m = re.search(r"(\d+)\s*(?:month|月)", str(interval or ""), flags=re.I)
            if m:
                months = int(m.group(1))
            elif str(interval or "").strip():
                months = 12 if any(tok in str(interval).lower() for tok in ("year", "annual", "年")) else 0
            if last is None or months <= 0:
                out.append({"name": name, "status": "partial", "value": "", "depends_on_slots": ["vaccine_date_last", "vaccine_interval"], "evidence_refs": [], "explanation": "missing_vaccine_interval_or_last_date"})
                continue
            year = last.year + ((last.month - 1 + months) // 12)
            month = ((last.month - 1 + months) % 12) + 1
            day = min(last.day, 28)
            due = dt.date(year, month, day)
            out.append({"name": name, "status": "derived", "value": due.isoformat(), "depends_on_slots": ["vaccine_date_last", "vaccine_interval"], "evidence_refs": [], "explanation": f"last={last.isoformat()} months={months}"})
    return out


def slot_coverage(slot_results: list[dict[str, Any]], required_slots: list[str], critical_slots: list[str]) -> dict[str, Any]:
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for row in slot_results:
        by_slot.setdefault(str(row.get("slot") or ""), []).append(row)

    def _covered(slot: str) -> bool:
        rows = by_slot.get(slot, [])
        return any(str(r.get("status") or "") in {"found", "derived"} and str(r.get("value") or "").strip() for r in rows)

    missing_required = [slot for slot in required_slots if not _covered(slot)]
    missing_critical = [slot for slot in critical_slots if not _covered(slot)]
    req_ratio = 1.0 if not required_slots else round((len(required_slots) - len(missing_required)) / max(1, len(required_slots)), 4)
    crit_ratio = 1.0 if not critical_slots else round((len(critical_slots) - len(missing_critical)) / max(1, len(critical_slots)), 4)
    return {
        "slot_coverage_ratio": req_ratio,
        "critical_slot_coverage_ratio": crit_ratio,
        "coverage_missing_slots": missing_required,
        "critical_missing_slots": missing_critical,
    }


def judge_sufficiency(
    *,
    query_spec: dict[str, Any],
    slot_results: list[dict[str, Any]],
    derivations: list[dict[str, Any]],
    context_chunks: list[dict[str, Any]],
    required_slots: list[str],
    critical_slots: list[str],
) -> dict[str, Any]:
    coverage = slot_coverage(slot_results, required_slots, critical_slots)
    derived_success = any(str(item.get("status") or "") == "derived" and str(item.get("value") or "").strip() for item in derivations)
    hit_count = len(context_chunks or [])
    partial_evidence_signals: list[str] = []
    refusal_blockers: list[str] = []

    subject_aliases = [str(x).lower() for x in (query_spec.get("subject_aliases") or []) if str(x or "").strip()]
    parts: list[str] = []
    for item in (context_chunks or [])[:12]:
        parts.extend(
            [
                str(item.get("title_zh") or ""),
                str(item.get("title_en") or ""),
                str(item.get("category_path") or ""),
                str(item.get("text") or ""),
            ]
        )
    flat = "\n".join(parts).lower()
    subject_coverage_ok = True
    if subject_aliases:
        subject_coverage_ok = any(alias in flat for alias in subject_aliases)

    target_field_coverage_ok = True
    target_slots = [str(x or "") for x in (query_spec.get("target_slots") or []) if str(x or "").strip()]
    slot_map = {str(item.get("slot") or ""): item for item in slot_results}
    covered_slots: set[str] = set()
    for row in slot_results:
        if str(row.get("status") or "") in {"found", "derived"} and str(row.get("value") or "").strip():
            covered_slots.add(str(row.get("slot") or ""))
    has_any_slot_value = bool(covered_slots)
    if target_slots:
        target_field_coverage_ok = any(
            str(slot_map.get(slot, {}).get("status") or "") in {"found", "derived"} and str(slot_map.get(slot, {}).get("value") or "").strip()
            for slot in target_slots
        )
        if target_field_coverage_ok:
            partial_evidence_signals.append("target_slot_covered")

    def _slot_family(slot: str) -> str:
        if slot in _DATE_SLOTS:
            return "date"
        if slot in _AMOUNT_SLOTS:
            return "amount"
        if slot in _PHONE_SLOTS:
            return "phone"
        if slot in _EMAIL_SLOTS:
            return "email"
        if slot in _REFERENCE_SLOTS:
            return "reference"
        if slot in {"provider", "vendor"}:
            return "provider"
        return slot

    family_support_ok = False
    if target_slots and covered_slots:
        covered_families = {_slot_family(slot) for slot in covered_slots}
        missing_targets = [slot for slot in target_slots if slot not in covered_slots]
        if any(_slot_family(slot) in covered_families for slot in missing_targets):
            family_support_ok = True
            partial_evidence_signals.append("same_family_slot_support")

    needs_presence = bool(query_spec.get("needs_presence_evidence"))
    needs_status = bool(query_spec.get("needs_status_evidence"))
    missing_critical = list(coverage.get("critical_missing_slots") or [])

    if hit_count <= 0 and (required_slots or critical_slots):
        answerability = "none"
        refusal_blockers.append("zero_hit_with_requirements")
    elif hit_count <= 0 and not derived_success:
        answerability = "none"
        refusal_blockers.append("zero_hit")
    elif not missing_critical or derived_success:
        answerability = "sufficient"
        if derived_success:
            partial_evidence_signals.append("derived_success")
    elif hit_count > 0 and (
        coverage["critical_slot_coverage_ratio"] >= 0.34
        or (coverage["slot_coverage_ratio"] >= 0.25 and subject_coverage_ok)
        or target_field_coverage_ok
        or family_support_ok
        or derived_success
        or has_any_slot_value
    ):
        answerability = "partial"
        if coverage["critical_slot_coverage_ratio"] >= 0.34:
            partial_evidence_signals.append("critical_ratio_ge_0.34")
        if coverage["slot_coverage_ratio"] >= 0.25 and subject_coverage_ok:
            partial_evidence_signals.append("slot_ratio_ge_0.25_subject_ok")
        if has_any_slot_value:
            partial_evidence_signals.append("any_slot_value_found")
    elif hit_count > 0:
        answerability = "insufficient"
        refusal_blockers.append("hit_but_no_slot_support")
    else:
        answerability = "none"
        refusal_blockers.append("no_context")

    presence_status_missing = [slot for slot in ("presence_evidence", "status_evidence") if slot in missing_critical]
    if (needs_presence or needs_status) and presence_status_missing:
        refusal_blockers.extend([f"missing_{slot}" for slot in presence_status_missing])
        if hit_count <= 0:
            answerability = "none"
        else:
            # Keep boundary-safe behavior by disallowing direct conclusion, but allow evidence-backed partial.
            if answerability == "sufficient":
                answerability = "partial"
            elif answerability == "none":
                answerability = "partial" if (has_any_slot_value or subject_coverage_ok or target_field_coverage_ok) else "none"
            elif answerability == "insufficient" and (has_any_slot_value or subject_coverage_ok or target_field_coverage_ok):
                answerability = "partial"
            partial_evidence_signals.append("presence_or_status_gate_partial_only")

    # How-to queries should not become direct/sufficient from obviously broken snippets.
    task_kind = str(query_spec.get("task_kind") or "")
    if task_kind == "howto_lookup":
        maint_row = slot_map.get("maintenance_interval") or {}
        steps_row = slot_map.get("maintenance_steps") or {}
        maint_val = str(maint_row.get("value") or "").strip()
        maint_status = str(maint_row.get("status") or "")
        steps_val = str(steps_row.get("value") or "").strip()
        steps_status = str(steps_row.get("status") or "")
        if maint_status in {"found", "derived"} and maint_val:
            low_quality = _looks_low_quality_howto_snippet(maint_val)
            if low_quality:
                refusal_blockers.append("low_quality_howto_interval")
                partial_evidence_signals.append("howto_interval_low_quality")
                if answerability == "sufficient":
                    answerability = "partial" if hit_count > 0 else "none"
        if steps_status in {"found", "derived"} and steps_val and (maint_status not in {"found", "derived"} or _looks_low_quality_howto_snippet(maint_val)):
            partial_evidence_signals.append("howto_steps_without_interval")
            if hit_count > 0 and answerability in {"none", "insufficient"}:
                answerability = "partial"
            if answerability == "sufficient":
                answerability = "partial"
        if steps_status in {"found", "derived"} and steps_val and _looks_low_quality_howto_steps(steps_val):
            refusal_blockers.append("low_quality_howto_steps")
            partial_evidence_signals.append("howto_steps_low_quality")
            if answerability == "sufficient":
                answerability = "partial" if hit_count > 0 else "none"

    return {
        **coverage,
        "subject_coverage_ok": bool(subject_coverage_ok),
        "target_field_coverage_ok": bool(target_field_coverage_ok),
        "answerability": answerability,
        "partial_evidence_signals": sorted({x for x in partial_evidence_signals if str(x).strip()}),
        "refusal_blockers": sorted({x for x in refusal_blockers if str(x).strip()}),
    }


def slot_results_to_detail_sections(*, query_spec: dict[str, Any], slot_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in slot_results[:30]:
        raw_value = str(item.get("value") or "")
        display_value = "" if _looks_like_raw_page_snippet(raw_value) else raw_value
        evidences = []
        for ev in (item.get("evidence_refs") or [])[:2]:
            evidences.append(
                {
                    "doc_id": str(ev.get("doc_id") or ""),
                    "chunk_id": str(ev.get("chunk_id") or ""),
                    "evidence_text": _safe_text(ev.get("evidence_text"), cap=180),
                }
            )
        rows.append(
            {
                "field": str(item.get("slot") or ""),
                "label_en": str(item.get("label_en") or ""),
                "label_zh": str(item.get("label_zh") or ""),
                "value_en": display_value,
                "value_zh": display_value,
                "evidence_refs": evidences,
            }
        )
    section_name = str(query_spec.get("task_kind") or "slot_results")
    return [{"section_name": section_name, "rows": rows}] if rows else []


def slot_result_map(slot_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in slot_results:
        slot = str(row.get("slot") or "").strip()
        if not slot:
            continue
        current = out.get(slot)
        if current is None or float(row.get("confidence") or 0.0) > float(current.get("confidence") or 0.0):
            out[slot] = row
    return out
