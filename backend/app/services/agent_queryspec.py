import re
from typing import Any

_TASK_KIND_BY_INTENT = {
    "entity_fact_lookup": "fact_lookup",
    "detail_extract": "detail_extract",
    "period_aggregate": "aggregate_lookup",
    "summarize_docs": "summarize",
    "compare_docs": "compare",
    "timeline_build": "timeline",
    "list_recent": "list",
    "list_by_category": "list",
    "open_document": "list",
    "queue_view": "queue",
    "reprocess_doc": "mutate",
    "tag_update": "mutate",
    "extract_fields": "detail_extract",
    "search_keyword": "fact_lookup",
    "search_semantic": "fact_lookup",
}

_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "appliances": (
        "洗碗机",
        "洗衣机",
        "冰箱",
        "空调",
        "热水器",
        "空气净化器",
        "dishwasher",
        "washer",
        "washing machine",
        "fridge",
        "refrigerator",
        "air conditioner",
        "aircon",
        "water heater",
        "air purifier",
        "appliance",
    ),
    "insurance": (
        "保险",
        "保单",
        "理赔",
        "claim",
        "insurance",
        "policy",
        "premium",
        "beneficiary",
        "受益人",
    ),
    "bills": (
        "账单",
        "电费",
        "水费",
        "燃气",
        "网费",
        "网络",
        "宽带",
        "nbn",
        "superloop",
        "网络提供商",
        "宽带运营商",
        "网络费",
        "bill",
        "invoice",
        "electricity",
        "water bill",
        "gas bill",
        "internet bill",
        "energy",
    ),
    "pets": (
        "宠物",
        "疫苗",
        "兽医",
        "绝育",
        "体检",
        "pet",
        "vaccine",
        "vaccination",
        "vet",
        "veterinary",
        "desex",
        "surgery",
    ),
    "home": (
        "房屋",
        "房产",
        "物业",
        "贷款",
        "房贷",
        "mortgage",
        "loan",
        "维修",
        "维护",
        "water tank",
        "rainwater tank",
        "产权",
        "面积",
    ),
}

_SLOT_HINTS: dict[str, tuple[str, ...]] = {
    "policy_no": ("保单号", "policy number", "policy no"),
    "beneficiary": ("受益人", "beneficiary"),
    "premium_amount": ("保费", "premium", "年保费"),
    "expiry_date": ("到期", "expiry", "expire", "due date", "有效期"),
    "coverage_scope": ("保障范围", "覆盖范围", "coverage", "covered", "exclusion", "除外"),
    "claim_status": ("理赔", "获批", "同意", "claim status", "approved"),
    "emergency_contact_phone": ("紧急联络电话", "emergency contact", "hotline", "紧急电话"),
    "brand": ("品牌", "brand"),
    "model": ("型号", "model"),
    "purchase_date": ("什么时候买", "购买日期", "purchase date", "bought"),
    "warranty_end": ("保修期", "warranty", "warranty end", "保修截止"),
    "maintenance_interval": ("多久", "间隔", "how often", "every", "过滤网", "清洁", "保养", "维护"),
    "work_order_no": ("工单号", "work order", "ticket number", "ticket no"),
    "engineer_phone": ("工程师手机号", "engineer phone", "technician phone"),
    "invoice_no": ("发票号", "发票号码", "invoice number"),
    "monthly_payment": ("月供", "monthly payment", "repayment"),
    "loan_bank": ("贷款银行", "银行", "lender", "bank"),
    "loan_term_years": ("贷款年限", "loan term", "term years", "多少年还完"),
    "loan_start_date": ("贷款开始", "loan start", "settlement date"),
    "loan_maturity_date": ("还完日期", "loan end", "maturity"),
    "property_area": ("面积", "平方米", "sqm", "m2", "㎡"),
    "vaccine_date_last": ("上次打疫苗", "last vaccine", "vaccination date"),
    "vaccine_interval": ("疫苗间隔", "booster interval", "有效期"),
    "vaccine_next_due": ("下次补打", "next vaccine", "next due"),
    "vet_name": ("宠物医院", "兽医", "vet name", "clinic"),
    "vet_contact": ("宠物医院联系方式", "vet contact", "vet phone", "vet email"),
    "surgery_record": ("手术", "绝育", "surgery", "desex"),
    "registration_no": ("登记证号", "registration number", "rego no"),
    "bill_amount": ("金额", "多少钱", "amount", "total", "sum"),
    "due_date": ("截止日期", "due date", "什么时候", "到期"),
    "billing_period": ("计费周期", "billing period"),
    "payment_status": ("已缴", "未缴", "payment status", "paid", "unpaid"),
    "vendor": ("供应商", "哪家", "提供商", "服务商", "运营商", "网络提供商", "宽带提供商", "provider", "vendor", "客服电话"),
    "contact_phone": ("电话", "手机号", "联系方式", "联系电话", "联络方式", "contact phone", "phone", "hotline"),
    "contact_email": ("邮箱", "联系方式", "联系邮箱", "email", "e-mail"),
    "provider": ("哪家", "提供商", "服务商", "运营商", "provider", "company"),
    "reference_no": ("编号", "reference", "号码", "no."),
    "date": ("日期", "什么时候", "when", "date"),
    "amount": ("金额", "多少钱", "amount", "cost", "price"),
}

_SLOT_DOMAIN_PRIORITIES: dict[str, tuple[str, ...]] = {
    "policy_no": ("insurance",),
    "beneficiary": ("insurance",),
    "premium_amount": ("insurance",),
    "expiry_date": ("insurance", "appliances", "bills"),
    "coverage_scope": ("insurance",),
    "claim_status": ("insurance",),
    "emergency_contact_phone": ("insurance",),
    "maintenance_interval": ("appliances", "home"),
    "work_order_no": ("appliances", "home"),
    "engineer_phone": ("appliances", "home"),
    "monthly_payment": ("home",),
    "loan_bank": ("home",),
    "loan_term_years": ("home",),
    "property_area": ("home",),
    "vaccine_next_due": ("pets",),
    "vaccine_date_last": ("pets",),
    "vet_name": ("pets",),
    "vet_contact": ("pets",),
    "surgery_record": ("pets",),
    "registration_no": ("pets",),
    "bill_amount": ("bills",),
    "billing_period": ("bills",),
    "payment_status": ("bills",),
    "vendor": ("bills", "insurance"),
}

_SUBJECT_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "insurance": {
        "generic": ("insurance", "policy", "保单", "保险"),
        "health": ("health insurance", "private health", "hospital cover", "医疗险", "医保"),
        "life": ("life insurance", "人寿保险", "人寿"),
        "vehicle": ("car insurance", "vehicle insurance", "motor insurance", "车险", "车辆保险"),
        "property": ("property insurance", "home insurance", "财产险", "家庭财产险"),
    },
    "appliances": {
        "dishwasher": ("dishwasher", "洗碗机"),
        "air_purifier": ("air purifier", "空气净化器"),
        "air_conditioner": ("air conditioner", "aircon", "空调"),
        "washer": ("washing machine", "washer", "洗衣机"),
        "fridge": ("fridge", "refrigerator", "冰箱"),
        "water_heater": ("water heater", "hot water", "热水器"),
    },
    "home": {
        "mortgage": ("mortgage", "loan", "房贷", "贷款"),
        "property": ("property", "房产", "房屋", "产权"),
        "maintenance": ("maintenance", "repair", "维修", "维护"),
        "water_tank": ("water tank", "rainwater tank", "水箱", "蓄水箱"),
    },
    "pets": {
        "generic": ("pet", "pets", "宠物"),
        "vaccine": ("vaccine", "vaccination", "疫苗"),
        "vet": ("vet", "veterinary", "兽医", "宠物医院"),
    },
    "bills": {
        "generic": ("bill", "invoice", "账单"),
        "energy": ("energy", "electricity", "gas", "电费", "燃气"),
        "water": ("water bill", "水费"),
        "internet": ("internet bill", "internet provider", "网络费", "网费", "网络账单", "网络提供商", "宽带", "宽带费", "宽带运营商", "broadband", "nbn", "superloop"),
    },
}

_PREFERRED_CATEGORIES: dict[str, tuple[str, ...]] = {
    "home": (
        "home/property",
        "home/maintenance",
        "legal/property",
        "legal/contracts",
        "finance/bills/other",
        "finance/loans",
        "finance/mortgage",
    ),
    "insurance": (
        "home/insurance",
        "health/insurance",
        "legal/insurance",
    ),
    "appliances": (
        "home/appliances",
        "home/manuals",
        "tech/hardware",
    ),
    "pets": (
        "home/pets",
        "health/medical_records",
        "home/insurance/pet",
    ),
    "bills": (
        "finance/bills",
    ),
}

_BILL_STRICT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "electricity": ("finance/bills/electricity",),
    "gas": ("finance/bills/gas",),
    "water": ("finance/bills/water",),
    "internet": ("finance/bills/internet",),
}

_MONTH_SCOPE_RE = re.compile(r"过去\s*(\d{1,2})\s*个?月|last\s*(\d{1,2})\s*months?", flags=re.I)


def _norm(text: str) -> str:
    return str(text or "").strip().lower()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(tok.lower() in text for tok in tokens)


def _add_unique(out: list[str], value: str) -> None:
    item = str(value or "").strip()
    if item and item not in out:
        out.append(item)


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(tok.lower() in text for tok in tokens)


def _is_explicit_list_request(text: str) -> bool:
    return _has_any(
        text,
        (
            "有哪些",
            "哪几项",
            "列出",
            "清单",
            "list all",
            "what are the",
            "哪些账单",
        ),
    )


def _is_bill_scalar_fact_query(text: str) -> bool:
    has_bill_signal = _has_any(text, ("账单", "bill", "invoice", "网费", "网络费", "宽带", "internet", "nbn"))
    has_scalar_signal = _has_any(text, ("多少", "多少钱", "金额", "amount", "cost", "price"))
    if not (has_bill_signal and has_scalar_signal):
        return False
    if _is_explicit_list_request(text):
        return False
    return True


def _detect_task_kind(query: str, planner_intent: str = "") -> str:
    text = _norm(query)
    if planner_intent in _TASK_KIND_BY_INTENT:
        base = _TASK_KIND_BY_INTENT[planner_intent]
    else:
        base = "fact_lookup"

    if any(tok in text for tok in ("总结", "摘要", "summar", "summary")):
        return "summarize"
    if any(tok in text for tok in ("比较", "对比", "compare")):
        return "compare"
    if any(tok in text for tok in ("时间线", "timeline")):
        return "timeline"
    if any(tok in text for tok in ("队列", "queue")):
        return "queue"
    if any(tok in text for tok in ("重处理", "reprocess", "标签", "tag")):
        return "mutate"
    if any(tok in text for tok in ("how to", "如何", "怎么", "步骤", "清洁", "过滤网", "维护", "保养")):
        return "howto_lookup"
    if any(tok in text for tok in ("过去", "平均", "总共", "合计", "一共", "上季度", "半年", "last quarter", "average", "total", "sum")):
        return "aggregate_lookup"
    if any(tok in text for tok in ("有没有", "有无", "是否", "did we", "have we", "已", "获批", "同意", "到期了吗", "expired", "due next")):
        return "status_check"
    if _is_bill_scalar_fact_query(text):
        return "fact_lookup"
    if any(tok in text for tok in ("列出", "最近", "latest", "recent", "open document", "打开")) and ("?" not in text and "？" not in text):
        return "list"
    return base or "fact_lookup"


def _detect_target_slots(query: str, *, task_kind: str, subject_domain: str) -> list[str]:
    text = _norm(query)
    scores: dict[str, int] = {}
    for slot, hints in _SLOT_HINTS.items():
        score = sum(1 for h in hints if h.lower() in text)
        if score <= 0:
            continue
        slot_score = score * 2
        for domain in _SLOT_DOMAIN_PRIORITIES.get(slot, tuple()):
            if domain == subject_domain:
                slot_score += 2
        scores[slot] = slot_score

    # Additional disambiguation for common natural-language asks.
    if any(tok in text for tok in ("紧急联络电话", "emergency", "hotline")):
        scores["emergency_contact_phone"] = max(scores.get("emergency_contact_phone", 0), 5)
    if any(tok in text for tok in ("工单号", "work order", "ticket number", "ticket no")):
        scores["work_order_no"] = max(scores.get("work_order_no", 0), 5)
    if any(tok in text for tok in ("过滤网", "filter")) and any(tok in text for tok in ("清洁", "clean", "多久", "how often")):
        scores["maintenance_interval"] = max(scores.get("maintenance_interval", 0), 6)
    if any(tok in text for tok in ("月供", "monthly payment")):
        scores["monthly_payment"] = max(scores.get("monthly_payment", 0), 6)
        scores["loan_bank"] = max(scores.get("loan_bank", 0), 3)
    if any(tok in text for tok in ("多少年还完", "remaining years", "loan term")):
        scores["loan_term_years"] = max(scores.get("loan_term_years", 0), 5)
        scores["loan_start_date"] = max(scores.get("loan_start_date", 0), 2)
        scores["loan_maturity_date"] = max(scores.get("loan_maturity_date", 0), 3)
    if any(tok in text for tok in ("面积", "平方米", "sqm", "m2", "㎡")):
        scores["property_area"] = max(scores.get("property_area", 0), 6)
    if any(tok in text for tok in ("下次", "next", "补打")) and any(tok in text for tok in ("疫苗", "vaccine")):
        scores["vaccine_next_due"] = max(scores.get("vaccine_next_due", 0), 6)
        scores["vaccine_date_last"] = max(scores.get("vaccine_date_last", 0), 4)
        scores["vaccine_interval"] = max(scores.get("vaccine_interval", 0), 3)
    if any(tok in text for tok in ("网费", "网络费", "宽带", "internet", "nbn")) and any(tok in text for tok in ("多少", "多少钱", "金额", "amount")):
        scores["bill_amount"] = max(scores.get("bill_amount", 0), 6)
        scores["vendor"] = max(scores.get("vendor", 0), 3)
        scores["billing_period"] = max(scores.get("billing_period", 0), 2)
    if (
        any(tok in text for tok in ("网络", "宽带", "internet", "nbn", "superloop"))
        and any(tok in text for tok in ("提供商", "provider", "vendor", "运营商", "服务商"))
        and any(tok in text for tok in ("联系方式", "联系电话", "电话", "邮箱", "contact"))
    ):
        scores["vendor"] = max(scores.get("vendor", 0), 6)
        scores["contact_phone"] = max(scores.get("contact_phone", 0), 5)
        scores["contact_email"] = max(scores.get("contact_email", 0), 4)
        scores["provider"] = max(scores.get("provider", 0), 3)

    ordered = [slot for slot, _score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
    if ordered:
        if any(tok in text for tok in ("网费", "网络费", "宽带", "internet", "nbn")) and any(tok in text for tok in ("多少", "多少钱", "金额", "amount")):
            preferred_order = ["bill_amount", "vendor", "billing_period"]
            ordered = [slot for slot in preferred_order if slot in ordered] + [slot for slot in ordered if slot not in preferred_order]
        if (
            any(tok in text for tok in ("网络", "宽带", "internet", "nbn", "superloop"))
            and any(tok in text for tok in ("提供商", "provider", "vendor", "运营商", "服务商"))
            and any(tok in text for tok in ("联系方式", "联系电话", "电话", "邮箱", "contact"))
        ):
            preferred_order = ["vendor", "contact_phone", "contact_email", "provider"]
            ordered = [slot for slot in preferred_order if slot in ordered] + [slot for slot in ordered if slot not in preferred_order]

    if not ordered:
        if task_kind == "aggregate_lookup" and subject_domain == "bills":
            return ["bill_amount", "due_date"]
        if task_kind in {"fact_lookup", "status_check"}:
            if subject_domain == "insurance":
                return ["policy_no"]
            if subject_domain == "appliances":
                return ["model"]
            if subject_domain == "pets":
                return ["date"]
            if subject_domain == "home":
                return ["reference_no"]
    # Cap to keep extraction bounded while preserving order.
    return ordered[:6]


def _score_domains(query: str, target_slots: list[str]) -> dict[str, int]:
    text = _norm(query)
    scores = {key: 0 for key in ("home", "insurance", "appliances", "bills", "pets", "generic")}
    for domain, hints in _DOMAIN_HINTS.items():
        for hint in hints:
            if hint.lower() in text:
                scores[domain] += 2 if len(hint) >= 3 else 1

    # Bias from slot-domain mapping to avoid collisions like "洗碗机维修" -> home.
    for slot in target_slots:
        for domain in _SLOT_DOMAIN_PRIORITIES.get(slot, tuple()):
            scores[domain] = scores.get(domain, 0) + 3

    if any(tok in text for tok in ("洗碗机", "dishwasher", "空气净化器", "air purifier", "洗衣机", "冰箱", "空调", "热水器")):
        scores["appliances"] += 4
    if any(tok in text for tok in ("房贷", "贷款", "mortgage")):
        scores["home"] += 4
    if any(tok in text for tok in ("保险", "保单", "insurance", "policy")):
        scores["insurance"] += 3
    if any(tok in text for tok in ("账单", "bill", "invoice")):
        scores["bills"] += 3
    if any(tok in text for tok in ("宠物", "pet", "疫苗", "vet", "兽医")):
        scores["pets"] += 3
    return scores


def _detect_subject_domain(query: str, target_slots: list[str]) -> str:
    scores = _score_domains(query, target_slots)
    best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    if best[1] <= 0:
        return "generic"
    return best[0]


def _subject_aliases_for_query(query: str, subject_domain: str) -> list[str]:
    text = _norm(query)
    out: list[str] = []
    # 预扫描：若有任何 non-generic 组命中，则跳过 generic，防止通用词（bill/invoice/账单）
    # 占据 aliases[:4] 槽位，导致 query_variant_node 的组合查询缺乏区分力
    specific_matched = any(
        group_name != "generic" and any(t.lower() in text for t in terms)
        for group_name, terms in _SUBJECT_ALIASES.get(subject_domain, {}).items()
    )
    for group_name, terms in _SUBJECT_ALIASES.get(subject_domain, {}).items():
        if group_name == "generic":
            if specific_matched:
                continue  # 已命中特定服务组，跳过 generic 以避免 alias 槽位污染
            for t in terms:
                _add_unique(out, t)
            continue
        if any(t.lower() in text for t in terms):
            for t in terms:
                _add_unique(out, t)
    # fallback generic aliases for chosen domain
    if not out:
        for terms in _SUBJECT_ALIASES.get(subject_domain, {}).values():
            for t in terms[:2]:
                _add_unique(out, t)
    return out[:12]


def _detect_time_scope(query: str) -> dict[str, Any]:
    text = _norm(query)
    out: dict[str, Any] = {
        "kind": "none",
        "start": "",
        "end": "",
        "relative_window_months": 0,
        "reference": "",
    }
    m = _MONTH_SCOPE_RE.search(text)
    if m:
        val = m.group(1) or m.group(2)
        try:
            months = max(1, min(24, int(val)))
        except Exception:
            months = 0
        out["kind"] = "relative_month_window"
        out["relative_window_months"] = months
        return out
    if any(tok in text for tok in ("上季度", "last quarter")):
        out["kind"] = "relative_month_window"
        out["relative_window_months"] = 3
        return out
    if any(tok in text for tok in ("半年", "six months")):
        out["kind"] = "relative_month_window"
        out["relative_window_months"] = 6
        return out
    if any(tok in text for tok in ("下个月", "next month")):
        out["kind"] = "relative_month"
        out["reference"] = "next_month"
        return out
    if any(tok in text for tok in ("上个月", "last month")):
        out["kind"] = "relative_month"
        out["reference"] = "last_month"
        return out
    if any(tok in text for tok in ("今年", "this year")):
        out["kind"] = "calendar_year"
        out["reference"] = "this_year"
    return out


def _detect_derivations(query: str, target_slots: list[str], task_kind: str) -> list[str]:
    text = _norm(query)
    out: list[str] = []
    if any(tok in text for tok in ("下个月", "next month")) and any(slot in target_slots for slot in ("expiry_date", "due_date", "warranty_end")):
        _add_unique(out, "compare_expiry_to_next_month")
    if any(tok in text for tok in ("多少年还完", "remaining years", "还完")) and any(
        slot in target_slots for slot in ("loan_term_years", "loan_start_date", "loan_maturity_date")
    ):
        _add_unique(out, "compute_remaining_loan_years")
    if any(tok in text for tok in ("下次补打", "next vaccine", "booster")) and any(
        slot in target_slots for slot in ("vaccine_next_due", "vaccine_date_last", "vaccine_interval")
    ):
        _add_unique(out, "estimate_next_vaccine_due")
    if task_kind == "status_check" and any(tok in text for tok in ("到期", "expiry", "expire")):
        _add_unique(out, "compare_expiry_to_next_month")
    return out[:4]


def _detect_needs_presence(query: str) -> bool:
    text = _norm(query)
    return any(tok in text for tok in ("有没有", "有无", "是否", "do we have", "did we", "have we"))


def _detect_needs_status(query: str) -> bool:
    text = _norm(query)
    return any(tok in text for tok in ("获批", "同意", "approved", "status", "已", "是否已"))


def _preferred_categories(query: str, subject_domain: str, task_kind: str) -> tuple[list[str], bool]:
    text = _norm(query)
    preferred = list(_PREFERRED_CATEGORIES.get(subject_domain, tuple()))
    strict = False
    internet_bill_tokens = ("网费", "网络费", "网络账单", "网络提供商", "宽带", "宽带费", "宽带运营商", "internet", "nbn", "broadband", "superloop")
    electricity_tokens = ("电费", "electricity", "power")
    gas_tokens = ("燃气", "gas")
    water_tokens = ("水费", "water")
    if subject_domain == "bills" and task_kind in {"aggregate_lookup", "list"}:
        # Only apply strict filter when a specific bill type is identified.
        # For generic "all bills" queries (no specific type token), keep strict=False so
        # we don't apply an exact-match filter on the broad "finance/bills" path — all
        # bills are stored at finance/bills/*, so exact match on "finance/bills" returns 0.
        if any(tok in text for tok in electricity_tokens):
            strict = True
            preferred = list(_BILL_STRICT_CATEGORIES["electricity"])
        elif any(tok in text for tok in gas_tokens):
            strict = True
            preferred = list(_BILL_STRICT_CATEGORIES["gas"])
        elif any(tok in text for tok in water_tokens):
            strict = True
            preferred = list(_BILL_STRICT_CATEGORIES["water"])
        elif any(tok in text for tok in internet_bill_tokens):
            strict = True
            preferred = list(_BILL_STRICT_CATEGORIES["internet"])
    elif subject_domain == "bills" and task_kind == "fact_lookup":
        # entity_fact_lookup（联系方式、账单金额）若明确指定服务商类型，也应严格过滤，
        # 防止跨服务 chunk 污染（如网络提供商查询召回电费账单）
        if any(tok in text for tok in internet_bill_tokens):
            strict = True
            preferred = list(_BILL_STRICT_CATEGORIES["internet"])
        elif any(tok in text for tok in electricity_tokens):
            strict = True
            preferred = list(_BILL_STRICT_CATEGORIES["electricity"])
        elif any(tok in text for tok in gas_tokens):
            strict = True
            preferred = list(_BILL_STRICT_CATEGORIES["gas"])
        elif any(tok in text for tok in water_tokens):
            strict = True
            preferred = list(_BILL_STRICT_CATEGORIES["water"])
    elif subject_domain == "bills":
        if any(tok in text for tok in internet_bill_tokens):
            preferred = list(dict.fromkeys([*_BILL_STRICT_CATEGORIES["internet"], *preferred]))
        elif any(tok in text for tok in electricity_tokens):
            preferred = list(dict.fromkeys([*_BILL_STRICT_CATEGORIES["electricity"], *preferred]))
        elif any(tok in text for tok in gas_tokens):
            preferred = list(dict.fromkeys([*_BILL_STRICT_CATEGORIES["gas"], *preferred]))
        elif any(tok in text for tok in water_tokens):
            preferred = list(dict.fromkeys([*_BILL_STRICT_CATEGORIES["water"], *preferred]))
    return (preferred, strict)


def build_subtasks_from_query_spec(spec: dict[str, Any]) -> list[dict[str, Any]]:
    target_slots = [str(item or "").strip() for item in (spec.get("target_slots") or []) if str(item or "").strip()]
    if not target_slots:
        return []
    out: list[dict[str, Any]] = []
    for idx, slot in enumerate(target_slots, start=1):
        out.append(
            {
                "id": f"slot-{idx}",
                "type": "slot_extract",
                "slot": slot,
                "required": True,
            }
        )
    return out


def required_slots_from_query_spec(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    target_slots = [str(item or "").strip() for item in (spec.get("target_slots") or []) if str(item or "").strip()]
    required = list(target_slots)
    critical = list(target_slots)
    # Presence/status checks can still be answered partially if at least one factual slot is found.
    if bool(spec.get("needs_presence_evidence")) and "presence_evidence" not in required:
        required.append("presence_evidence")
        critical.append("presence_evidence")
    if bool(spec.get("needs_status_evidence")) and "status_evidence" not in required:
        required.append("status_evidence")
        critical.append("status_evidence")
    return (required[:12], critical[:12])


def slot_query_terms(slot: str) -> list[str]:
    return [str(item) for item in _SLOT_HINTS.get(str(slot or "").strip(), tuple())][:6]


def estimate_queryspec_confidence(query: str, spec: dict[str, Any]) -> dict[str, Any]:
    text = _norm(query)
    spec = dict(spec or {})
    score = 0.0
    positive: list[str] = []
    negative: list[str] = []
    ambiguity: list[str] = []

    subject_domain = str(spec.get("subject_domain") or "generic")
    task_kind = str(spec.get("task_kind") or "")
    target_slots = [str(x or "").strip() for x in (spec.get("target_slots") or []) if str(x or "").strip()]
    preferred_categories = [str(x or "").strip() for x in (spec.get("preferred_categories") or []) if str(x or "").strip()]

    canonical_slots = set(_SLOT_HINTS.keys()) | {"presence_evidence", "status_evidence"}
    canonical_target_slots = [s for s in target_slots if s in canonical_slots]
    noncanonical_target_slots = [s for s in target_slots if s not in canonical_slots]

    if subject_domain != "generic":
        score += 0.25
        positive.append("subject_domain_non_generic")
    else:
        negative.append("subject_domain_generic")

    if canonical_target_slots:
        score += 0.20
        positive.append("canonical_target_slots_present")
    elif target_slots:
        negative.append("target_slots_noncanonical_only")
    else:
        negative.append("target_slots_empty")

    if any(cat.count("/") >= 2 for cat in preferred_categories):
        score += 0.20
        positive.append("specific_preferred_category")
    elif preferred_categories:
        negative.append("preferred_category_too_broad")
    else:
        negative.append("preferred_categories_empty")

    task_match = False
    if task_kind == "aggregate_lookup" and any(tok in text for tok in ("总共", "合计", "一共", "平均", "total", "sum", "average")):
        task_match = True
    elif task_kind == "list" and _is_explicit_list_request(text):
        task_match = True
    elif task_kind in {"fact_lookup", "status_check"} and any(
        tok in text for tok in ("多少", "多少钱", "金额", "是什么", "哪家", "联系方式", "电话", "邮箱", "when", "contact", "phone", "email")
    ):
        task_match = True
    elif task_kind == "howto_lookup" and any(tok in text for tok in ("如何", "怎么", "how to", "步骤", "维护", "保养", "清洁")):
        task_match = True
    if task_match:
        score += 0.15
        positive.append("task_kind_matches_query_surface")
    else:
        negative.append("task_kind_surface_mismatch")

    domain_scores = _score_domains(text, canonical_target_slots or target_slots)
    sorted_domains = sorted(domain_scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(sorted_domains) >= 2 and sorted_domains[0][1] > 0 and sorted_domains[1][1] > 0:
        gap = sorted_domains[0][1] - sorted_domains[1][1]
        if gap <= 2:
            score -= 0.20
            ambiguity.append(f"multi_domain_conflict:{sorted_domains[0][0]}~{sorted_domains[1][0]}")

    if (not canonical_target_slots) and subject_domain == "generic":
        score -= 0.15
        negative.append("generic_and_no_canonical_slots")

    if any("\u4e00" <= ch <= "\u9fff" for ch in text) and not canonical_target_slots and not preferred_categories:
        score -= 0.15
        negative.append("zh_query_sparse_spec")

    if noncanonical_target_slots:
        ambiguity.append("noncanonical_target_slots")

    score = max(0.0, min(1.0, round(score, 3)))
    return {
        "score": score,
        "signals_positive": positive[:12],
        "signals_negative": negative[:12],
        "ambiguity_flags": ambiguity[:12],
    }


def prefilter_router_candidate_categories(spec: dict[str, Any], available_categories: list[str], *, max_candidates: int = 12) -> list[str]:
    spec = dict(spec or {})
    max_candidates = max(1, min(24, int(max_candidates or 12)))
    preferred = [str(x or "").strip() for x in (spec.get("preferred_categories") or []) if str(x or "").strip()]
    subject_domain = str(spec.get("subject_domain") or "generic")

    out: list[str] = []

    def _push(cat: str) -> None:
        c = str(cat or "").strip()
        if c and c not in out:
            out.append(c)

    available = [str(x or "").strip() for x in (available_categories or []) if str(x or "").strip()]
    for cat in preferred:
        _push(cat)

    domain_prefixes: dict[str, tuple[str, ...]] = {
        "bills": ("finance/bills",),
        "insurance": ("home/insurance", "health/insurance", "legal/insurance"),
        "home": ("home/property", "home/maintenance", "finance/mortgage", "finance/loans", "legal/property", "legal/contracts"),
        "appliances": ("home/appliances", "home/manuals", "tech/hardware"),
        "pets": ("home/pets", "home/insurance/pet", "health/medical_records"),
    }
    for prefix in domain_prefixes.get(subject_domain, tuple()):
        for cat in available:
            if cat.startswith(prefix):
                _push(cat)
                if len(out) >= max_candidates:
                    return out[:max_candidates]

    for cat in available:
        if len(out) >= max_candidates:
            break
        # Keep only plausible structured categories to avoid prompt bloat.
        if "/" in cat:
            _push(cat)
    return out[:max_candidates]


def build_query_spec_from_query(query: str, *, planner_intent: str = "", doc_scope: dict[str, Any] | None = None) -> dict[str, Any]:
    text = _norm(query)
    task_kind = _detect_task_kind(text, planner_intent=planner_intent)
    # Pre-pass slots to influence domain selection; then recompute slots with chosen domain.
    pre_slots = _detect_target_slots(text, task_kind=task_kind, subject_domain="generic")
    subject_domain = _detect_subject_domain(text, pre_slots)
    target_slots = _detect_target_slots(text, task_kind=task_kind, subject_domain=subject_domain)
    subject_aliases = _subject_aliases_for_query(text, subject_domain)
    preferred_categories, strict_domain_filter = _preferred_categories(text, subject_domain, task_kind)
    derivations = _detect_derivations(text, target_slots, task_kind)

    if task_kind == "howto_lookup" and not target_slots:
        target_slots = ["maintenance_interval"]
    if task_kind == "status_check" and not target_slots and subject_domain == "insurance":
        target_slots = ["expiry_date"]

    return {
        "version": "v2",
        "task_kind": task_kind,
        "subject_domain": subject_domain,
        "subject_aliases": subject_aliases,
        "target_slots": target_slots,
        "time_scope": _detect_time_scope(text),
        "derivations": derivations,
        "needs_presence_evidence": _detect_needs_presence(text),
        "needs_status_evidence": _detect_needs_status(text),
        "strict_domain_filter": bool(strict_domain_filter),
        "preferred_categories": preferred_categories,
        "doc_scope_hint": doc_scope or {},
    }


def apply_query_spec_to_planner_fields(spec: dict[str, Any], planner_dict: dict[str, Any]) -> dict[str, Any]:
    planner_dict = dict(planner_dict or {})
    planner_dict["task_kind"] = str(spec.get("task_kind") or "")
    planner_dict["subject_domain"] = str(spec.get("subject_domain") or "")
    planner_dict["target_slots"] = [str(x) for x in (spec.get("target_slots") or []) if str(x or "").strip()]
    planner_dict["query_spec"] = spec
    planner_dict["query_spec_version"] = str(spec.get("version") or "v2")
    return planner_dict
