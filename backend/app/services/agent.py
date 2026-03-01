import datetime as dt
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.models import Chunk, Document, DocumentStatus, IngestionJob
from app.schemas import (
    AgentExecuteRequest,
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
    ResultCardAction,
    ResultCardSource,
    SearchRequest,
)
from app.services.bill_facts import list_recent_bill_facts
from app.services.ingestion import enqueue_ingestion_job
from app.services.planner import RouterDecision, plan_from_request, route_and_rewrite
from app.services.search import search_documents

settings = get_settings()
logger = get_logger(__name__)
_JSON_BLOCK = re.compile(r"\{.*\}", flags=re.S)

_ACTION_LABELS = {
    "open_docs": ("Open Docs", "打开文档"),
    "compare": ("Compare", "比较"),
    "timeline": ("Timeline", "时间线"),
    "retrieve_docs": ("Open Docs", "打开文档"),
    "compare_docs": ("Compare", "比较"),
    "timeline_extract": ("Timeline", "时间线"),
    "summarize_docs": ("Summarize", "生成摘要"),
    "extract_fields": ("Extract Fields", "提取字段"),
    "list_by_category": ("By Category", "按分类查看"),
    "queue_ops": ("Queue", "队列操作"),
    "queue_view": ("Queue", "队列状态"),
    "reprocess_doc": ("Reprocess", "重处理文档"),
    "tag_update": ("Update Tags", "更新标签"),
    "search_documents": ("Search", "检索"),
    "list_recent": ("Recent Docs", "最近文档"),
    "fallback_search": ("Fallback Search", "回退语义检索"),
    "extract_details": ("Extract Details", "提取细节"),
}

_ACTION_META: dict[str, dict[str, Any]] = {
    "open_docs": {"action_type": "navigate", "payload": {"target": "docs"}},
    "retrieve_docs": {"action_type": "navigate", "payload": {"target": "docs"}},
    "list_by_category": {"action_type": "navigate", "payload": {"target": "cats"}},
    "search_documents": {"action_type": "agent_command", "payload": {"command": "search"}},
    "queue_ops": {"action_type": "agent_command", "payload": {"command": "queue_view"}},
    "queue_view": {"action_type": "agent_command", "payload": {"command": "queue_view"}},
    "list_recent": {"action_type": "agent_command", "payload": {"command": "list_recent"}},
    "compare_docs": {"action_type": "agent_command", "payload": {"command": "compare_docs"}},
    "timeline_extract": {"action_type": "agent_command", "payload": {"command": "timeline_build"}},
    "extract_fields": {"action_type": "agent_command", "payload": {"command": "extract_fields"}},
    "extract_details": {"action_type": "agent_command", "payload": {"command": "extract_details"}},
    "fallback_search": {"action_type": "agent_command", "payload": {"command": "fallback_search"}},
    "reprocess_doc": {
        "action_type": "mutate",
        "payload": {"command": "reprocess_doc"},
        "requires_confirm": True,
        "confirm_text_en": "Reprocess selected document?",
        "confirm_text_zh": "确认重处理所选文档？",
    },
    "tag_update": {
        "action_type": "mutate",
        "payload": {"command": "tag_update"},
        "requires_confirm": True,
        "confirm_text_en": "Update tags for selected document?",
        "confirm_text_zh": "确认更新所选文档标签？",
    },
}

_DETAIL_TOPIC_MAP: dict[str, tuple[str, ...]] = {
    "insurance": ("保险", "保单", "policy", "insurance", "insurer", "premium"),
    "bill": ("账单", "bill", "invoice", "due", "缴费", "电费", "水费", "燃气"),
    "home": ("房屋", "房产", "物业", "贷款", "mortgage", "维修", "maintenance", "maintain", "water tank", "rainwater tank", "产权"),
    "appliances": ("家电", "洗衣机", "冰箱", "洗碗机", "空调", "热水器", "水箱", "appliance", "dishwasher", "air purifier", "water heater", "hot water"),
    "pets": ("宠物", "疫苗", "兽医", "体检", "绝育", "pet", "vaccine", "vet", "birthday", "birth date", "dob", "生日", "猫", "狗"),
    "warranty": ("保修", "warranty", "serial", "claim"),
    "contract": ("合同", "contract", "agreement", "条款", "obligation"),
}

_DETAIL_SCHEMA: dict[str, list[tuple[str, str, str]]] = {
    "insurance": [
        ("policy_name", "Policy Name", "保单名称"),
        ("policy_type", "Policy Type", "保险类型"),
        ("insurer", "Insurer", "保险机构"),
        ("policy_no", "Policy Number", "保单编号"),
        ("effective", "Effective Date", "生效日期"),
        ("expiry", "Expiry Date", "到期日期"),
        ("premium", "Premium", "保费金额"),
        ("status", "Status", "状态"),
    ],
    "bill": [
        ("bill_name", "Bill Name", "账单名称"),
        ("vendor", "Vendor", "服务商"),
        ("period", "Billing Period", "计费周期"),
        ("due_date", "Due Date", "截止日期"),
        ("amount", "Amount", "金额"),
        ("currency", "Currency", "币种"),
        ("payment_status", "Payment Status", "支付状态"),
    ],
    "home": [
        ("loan_bank", "Loan Bank", "贷款银行"),
        ("monthly_payment", "Monthly Payment", "月供金额"),
        ("term_years", "Loan Term Years", "贷款年限"),
        ("property_area", "Property Area", "房屋面积"),
        ("maintenance_item", "Maintenance Item", "维修项目"),
        ("maintenance_date", "Maintenance Date", "维修日期"),
    ],
    "appliances": [
        ("brand", "Brand", "品牌"),
        ("model", "Model", "型号"),
        ("purchase_date", "Purchase Date", "购买日期"),
        ("warranty_end", "Warranty End", "保修截止"),
        ("service_date", "Service Date", "上次保养日期"),
        ("invoice_no", "Invoice Number", "发票号"),
    ],
    "pets": [
        ("birth_date", "Birth Date", "出生日期"),
        ("vaccine_date", "Vaccine Date", "疫苗日期"),
        ("vaccine_type", "Vaccine Type", "疫苗类型"),
        ("next_due", "Next Due", "下次补打日期"),
        ("vet_name", "Vet Name", "宠物医院/医生"),
        ("vet_contact", "Vet Contact", "宠物医院联系方式"),
        ("registration_no", "Registration Number", "登记证号"),
        ("surgery_record", "Surgery Record", "手术记录"),
    ],
    "warranty": [
        ("product", "Product", "产品名称"),
        ("serial", "Serial Number", "序列号"),
        ("model", "Model", "型号"),
        ("provider", "Provider", "保修提供方"),
        ("start", "Start Date", "起始日期"),
        ("end", "End Date", "结束日期"),
        ("coverage_scope", "Coverage Scope", "保障范围"),
        ("claim_contact", "Claim Contact", "理赔联系方式"),
    ],
    "contract": [
        ("title", "Contract Title", "合同标题"),
        ("parties", "Parties", "合同方"),
        ("effective", "Effective Date", "生效日期"),
        ("expiry", "Expiry Date", "到期日期"),
        ("obligation", "Obligations", "主要义务"),
        ("penalty", "Penalty", "违约责任"),
        ("notice_period", "Notice Period", "通知期限"),
    ],
    "generic": [
        ("key_entity", "Key Entity", "关键主体"),
        ("date", "Date", "日期"),
        ("amount", "Amount", "金额"),
        ("action", "Action", "行动项"),
        ("contact", "Contact", "联系方式"),
        ("reference", "Reference", "参考编号"),
    ],
}

_EN_MONTH_MAP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_EN_MONTH_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b"
    r"(?:\s+(\d{4}))?"
    r"|\b(\d{4})\s+"
    r"(january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b"
)
_BILL_QUERY_HINTS = [
    "账单",
    "缴费",
    "电费",
    "水费",
    "燃气",
    "bill",
    "bills",
    "invoice",
    "current bill",
    "current bills",
    "outstanding bill",
    "outstanding bills",
    "payment due",
    "due date",
]
_BILL_MONTH_TOTAL_HINTS = [
    "账单情况",
    "总共",
    "合计",
    "一共",
    "多少钱",
    "total",
    "sum",
    "how much",
    "how much in total",
]
_FOLLOWUP_QUERY_HINTS = [
    "继续",
    "刚才",
    "上一个",
    "上一轮",
    "这些",
    "这个",
    "它",
    "that one",
    "those",
    "continue",
    "previous",
    "above",
]
_NON_FORMAL_BILL_DOC_HINTS = (
    "welcome",
    "tips",
    "guide",
    "how to",
    "how-to",
    "billing-tips",
    "说明",
    "提示",
    "如何",
)
_TAG_KEY_RE = re.compile(r"\b([a-z0-9][a-z0-9._-]{0,31}:[a-z0-9][a-z0-9._-]{0,95})\b")


@dataclass(slots=True)
class QueryFacet:
    facet_keys: list[str] = field(default_factory=list)
    strict_categories: list[str] = field(default_factory=list)
    required_terms: list[str] = field(default_factory=list)
    strict_mode: bool = False


_FACET_NETWORK_BILL = (
    "网络",
    "互联网",
    "宽带",
    "nbn",
    "broadband",
    "internet",
    "superloop",
)
_FACET_ENERGY_BILL = (
    "energy bill",
    "energy bills",
    "current energy bills",
    "electricity and gas",
    "电费和燃气",
    "能源账单",
)
_FACET_ELECTRICITY_BILL = (
    "电费",
    "electricity",
    "power",
    "energy",
)
_FACET_WATER_BILL = (
    "水费",
    "water bill",
    "water",
)
_FACET_GAS_BILL = (
    "燃气",
    "gas bill",
    "gas",
)
_FACET_PROPERTY = (
    "物业",
    "property",
    "strata",
    "owners corporation",
    "body corporate",
)
_FACET_CONTACT = (
    "联系方式",
    "contact",
    "phone",
    "电话",
    "email",
    "邮箱",
    "manager",
    "负责人",
)

_DOMAIN_HINTS = {
    "pets": ("宠物", "pet", "vaccine", "疫苗", "vet", "兽医", "绝育", "birthday", "birth date", "dob", "生日", "出生日期"),
    "appliances": ("家电", "appliance", "洗衣机", "冰箱", "空调", "热水器", "洗碗机", "dishwasher", "warranty"),
    "home": ("房屋", "房产", "物业", "贷款", "mortgage", "maintenance", "maintain", "维修", "产权", "建造年份", "water tank", "rainwater tank"),
    "insurance": ("保险", "policy", "保单", "理赔", "claim", "premium"),
    "bills": ("账单", "bill", "invoice", "电费", "水费", "燃气", "internet"),
}
_DOMAIN_CATEGORY_WHITELISTS = {
    "pets": ("home/pets", "health/medical_records", "home/insurance/pet"),
    "appliances": ("home/manuals", "home/appliances", "tech/hardware"),
    "home": ("home/property", "home/maintenance", "legal/property", "finance/bills/other"),
    "insurance": ("home/insurance", "health/insurance", "legal/insurance"),
    "bills": ("finance/bills",),
}
_SUBJECT_ANCHOR_HINTS: dict[str, tuple[str, ...]] = {
    "birthday_birthdate": ("birthday", "birth date", "dob", "生日", "出生日期"),
    "life_insurance": ("人寿", "life insurance", "beneficiary", "受益人"),
    "vehicle_insurance": ("车险", "车辆保险", "motor insurance", "car insurance", "vehicle insurance"),
    "pet_insurance": ("宠物保险", "pet insurance"),
    "health_insurance": ("医保", "医疗险", "health insurance", "private health", "hospital cover"),
    "dishwasher": ("洗碗机", "dishwasher"),
    "air_purifier": ("空气净化器", "air purifier"),
    "air_conditioner": ("空调", "air conditioner", "aircon", "ac ", "daikin"),
    "mortgage": ("房贷", "贷款", "mortgage"),
    "roof_insulation": ("屋顶", "roof", "隔热", "insulation"),
    "property_fee": ("物业费", "strata fee", "property fee", "owners corporation"),
    "pet_vaccine": ("疫苗", "vaccine", "vaccination"),
    "pet_surgery": ("手术", "绝育", "surgery", "desex", "desexing"),
    "vet": ("兽医", "宠物医院", "vet", "veterinary"),
    "water_tank": ("water tank", "rainwater tank", "水箱", "蓄水箱"),
    "maintenance_howto": ("how to maintain", "maintenance", "maintain", "保养", "维护"),
}
_QUERY_QUALIFIER_HINTS = (
    "人寿",
    "life insurance",
    "beneficiary",
    "受益人",
    "mortgage",
    "房贷",
    "贷款",
    "dishwasher",
    "洗碗机",
    "空气净化器",
    "air purifier",
    "发票号码",
    "invoice number",
    "工单号",
    "ticket no",
    "ticket number",
    "rebate",
    "返利",
    "补贴",
    "government rebate",
    "政府返利",
    "政府补贴",
    "绝育",
    "手术",
    "hip",
    "髋",
    "roof",
    "屋顶",
    "隔热",
    "birthday",
    "birth date",
    "dob",
    "生日",
    "出生日期",
    "water tank",
    "rainwater tank",
    "coverage",
    "covered",
    "what's covered",
    "exclusion",
    "current bills",
    "current energy bills",
    "current gas bills",
    "outstanding bills",
)
_PROPOSAL_DOC_HINTS = (
    "proposal",
    "quote",
    "offer",
    "solar proposal",
    "提案",
    "报价",
    "方案",
)
_HISTORICAL_FACT_QUERY_HINTS = (
    "有没有",
    "是否",
    "有无",
    "做过",
    "拿到",
    "获批",
    "提交过",
    "did we",
    "have we",
    "approved",
    "rebate",
    "claim",
    "工单号",
    "保单号",
    "受益人",
)

_ANSWERABILITY_CONTACT_TOKENS = ("联系方式", "电话", "邮箱", "contact", "phone", "email")
_ANSWERABILITY_AMOUNT_TOKENS = ("多少钱", "金额", "total", "sum", "费用", "花了", "cost", "price", "premium")
_ANSWERABILITY_DATE_TOKENS = ("什么时候", "日期", "到期", "when", "date", "expiry", "due", "birthday", "birth date", "dob", "生日", "出生日期")
_ANSWERABILITY_PRESENCE_TOKENS = ("有没有", "是否", "有无", "do we have", "did we", "have we")

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


def _safe_text(value: Any, *, cap: int = 280) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "..."


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


def _context_policy_for_query(query: str, *, client_context: dict[str, Any] | None = None) -> str:
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


def _normalize_conversation_messages(req: AgentExecuteRequest, *, context_policy: str) -> list[dict[str, str]]:
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
    if any(t in text for t in ("这个月", "本月", "当月", "this month", "current month")):
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
        facet.required_terms = ["internet", "network", "nbn", "宽带", "网络", "superloop"]
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
        facet.strict_categories = ["home/maintenance", "home/property", "legal/property", "finance/bills/other"]
        facet.required_terms = ["contact", "phone", "email", "联系方式", "电话", "邮箱", "物业", "property"]
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


def _subject_coverage_ok(anchor_terms: list[str], chunks: list[dict[str, Any]]) -> bool:
    if not anchor_terms:
        return True
    texts = []
    for chunk in chunks[:12]:
        texts.append(str(chunk.get("text") or "").lower())
        texts.append(str(chunk.get("title_zh") or "").lower())
        texts.append(str(chunk.get("title_en") or "").lower())
        texts.append(str(chunk.get("category_path") or "").lower())
    blob = "\n".join(texts)
    if not blob.strip():
        return False
    return any(term in blob for term in anchor_terms if term)


def _target_field_terms(query: str) -> list[str]:
    lowered = str(query or "").lower()
    out: list[str] = []
    groups = (
        (("birthday", "birth date", "dob", "生日", "出生日期"), "birth_date"),
        (("coverage", "covered", "what's covered", "保障范围", "覆盖范围", "exclusion", "除外"), "coverage_scope"),
        (("how to maintain", "maintenance", "maintain", "维护", "保养"), "maintenance_howto"),
        (("contact", "phone", "email", "联系方式", "电话", "邮箱"), "contact"),
    )
    for tokens, key in groups:
        if any(tok in lowered for tok in tokens):
            out.append(key)
    return out[:4]


def _target_field_coverage_ok(target_fields: list[str], chunks: list[dict[str, Any]]) -> bool:
    if not target_fields:
        return True
    texts = []
    for chunk in chunks[:12]:
        texts.append(str(chunk.get("text") or "").lower())
        texts.append(str(chunk.get("title_zh") or "").lower())
        texts.append(str(chunk.get("title_en") or "").lower())
    blob = "\n".join(texts)
    if not blob.strip():
        return False
    for field in target_fields:  # noqa: F402
        if field == "birth_date":
            if any(tok in blob for tok in ("birthday", "birth date", "dob", "生日", "出生")):
                return True
        elif field == "coverage_scope":
            if any(tok in blob for tok in ("coverage", "covered", "exclusion", "保障", "覆盖", "除外")):
                return True
        elif field == "maintenance_howto":
            if any(tok in blob for tok in ("maintain", "maintenance", "service", "filter", "clean", "维护", "保养", "清洁")):
                return True
        elif field == "contact":
            if ("@" in blob) or any(tok in blob for tok in ("contact", "phone", "email", "电话", "邮箱")):
                return True
    return False


def _infer_subject_entity(query: str, *, detail_topic: str = "", route: str = "") -> str:
    lowered = str(query or "").lower()
    if any(tok in lowered for tok in ("pet insurance", "宠物保险")):
        return "pet_insurance"
    if any(tok in lowered for tok in ("birthday", "birth date", "dob", "生日", "出生日期")):
        return "pet_profile"
    if any(tok in lowered for tok in ("current bill", "current bills", "账单", "bill")):
        if any(tok in lowered for tok in ("energy", "electricity", "gas", "电费", "燃气")):
            return "utility_bills"
        return "bills"
    if any(tok in lowered for tok in ("water tank", "rainwater tank", "水箱", "蓄水箱")):
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


def _required_evidence_fields(query: str, planner: PlannerDecision) -> list[str]:
    lowered = str(query or "").lower()
    out: list[str] = []
    explicit = [str(x or "").strip() for x in list(getattr(planner, "required_evidence_fields", []) or []) if str(x or "").strip()]
    allowed = {"amount", "date", "contact", "explicit_presence_evidence"}
    explicit = [x for x in explicit if x in allowed]
    amount_needed = any(tok in lowered for tok in _ANSWERABILITY_AMOUNT_TOKENS)
    date_needed = any(tok in lowered for tok in _ANSWERABILITY_DATE_TOKENS)
    contact_needed = any(tok in lowered for tok in _ANSWERABILITY_CONTACT_TOKENS)
    presence_needed = any(tok in lowered for tok in _ANSWERABILITY_PRESENCE_TOKENS)
    coverage_needed = any(tok in lowered for tok in ("coverage", "covered", "what's covered", "保障", "覆盖", "exclusion", "除外"))
    # Use query-driven requirements first, and only lightly trust planner-provided
    # required fields to avoid over-refusal from broad defaults.
    if amount_needed or ("amount" in explicit and any(tok in lowered for tok in ("账单", "bill", "费用", "保费", "price", "cost"))):
        out.append("amount")
    if date_needed or ("date" in explicit and any(tok in lowered for tok in ("到期", "日期", "when", "date", "expiry", "period"))):
        out.append("date")
    if contact_needed or ("contact" in explicit and any(tok in lowered for tok in ("联系方式", "电话", "邮箱", "contact", "phone", "email"))):
        out.append("contact")
    if coverage_needed and "explicit_presence_evidence" not in out:
        out.append("explicit_presence_evidence")
    if presence_needed:
        out.append("explicit_presence_evidence")
    return out[:8]


def _evidence_match(field: str, text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    if field == "amount":
        return bool(re.search(r"(?:aud|澳币|\$)\s?\d+(?:\.\d{1,2})?", lowered)) or bool(re.search(r"\d+(?:\.\d{1,2})\s*(?:元|澳币|美元)", lowered))
    if field == "date":
        _mo_lo = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
        return bool(
            re.search(r"20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}日?", lowered)            # 2024-11-04
            or re.search(r"\b\d{1,2}\s+" + _mo_lo + r"\s+20\d{2}\b", lowered)         # 2 December 2025
            or re.search(_mo_lo + r"\s+\d{1,2},?\s+20\d{2}\b", lowered)                # December 2, 2025
            or re.search(r"\b\d{1,2}[-/]\d{1,2}[-/]20\d{2}\b", lowered)               # 04-11-2024 or 04/11/2024
        )
    if field == "contact":
        return ("@" in lowered) or bool(re.search(r"\b\d{8,12}\b", lowered))
    if field == "explicit_presence_evidence":
        return any(tok in lowered for tok in ("has", "have", "contains", "存在", "有", "无", "没有", "未见", "未找到"))
    return False


def _build_evidence_map(fields: list[str], chunks: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for field in fields:  # noqa: F402
        refs: list[dict[str, str]] = []
        for chunk in chunks[:12]:
            text = str(chunk.get("text") or "")
            if not _evidence_match(field, text):
                continue
            refs.append(
                {
                    "doc_id": str(chunk.get("doc_id") or ""),
                    "chunk_id": str(chunk.get("chunk_id") or ""),
                    "evidence_text": _safe_text(text, cap=120),
                }
            )
            if len(refs) >= 2:
                break
        out[field] = refs
    return out


def _coverage_from_map(fields: list[str], evidence_map: dict[str, list[dict[str, str]]]) -> tuple[float, list[str]]:
    if not fields:
        return (1.0, [])
    hit = 0
    missing: list[str] = []
    for field in fields:  # noqa: F402
        rows = evidence_map.get(field) or []
        if rows:
            hit += 1
        else:
            missing.append(field)
    return (round(hit / max(1, len(fields)), 4), missing)


def _infer_answerability(*, hit_count: int, coverage_ratio: float, refusal_candidate: bool, has_requirements: bool) -> str:
    if hit_count <= 0 and (refusal_candidate or has_requirements):
        return "none"
    if hit_count <= 0:
        return "insufficient"
    if refusal_candidate and coverage_ratio < 1.0:
        return "none"
    if has_requirements and coverage_ratio < 0.4:
        return "insufficient"
    return "sufficient"


def _contains_specific_claim(text: str) -> bool:
    body = str(text or "")
    if not body.strip():
        return False
    if re.search(r"https?://|www\.", body, flags=re.I):
        return True
    if re.search(r"\b\d{8,12}\b", body):
        return True
    if re.search(r"(?:aud|澳币|\$)\s?\d+(?:\.\d{1,2})?", body, flags=re.I):
        return True
    if re.search(r"(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}日?)", body):
        return True
    return False


def _presence_evidence_sufficient(query: str, chunks: list[dict[str, Any]]) -> bool:
    lowered = str(query or "").lower()
    tokens = re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2,}", lowered)
    stop = {
        "我们",
        "家里",
        "有没有",
        "是否",
        "有无",
        "what",
        "which",
        "have",
        "did",
        "can",
    }
    targets = [tok for tok in tokens if tok not in stop][:5]
    if not targets:
        return False
    patterns = (
        r"(有|没有|未|无|已|申请|购买|做过|完成|not found|has|have|did)",
    )
    for chunk in chunks[:12]:
        text = str(chunk.get("text") or "").lower()
        if not text:
            continue
        for tok in targets:
            if tok not in text:
                continue
            if any(re.search(pat, text) for pat in patterns):
                return True
    return False


def _resolve_detail_topic(query: str, planner_scope: dict[str, Any] | None = None) -> str:
    hint = str((planner_scope or {}).get("topic_hint") or "").strip().lower()
    if hint in {"insurance", "bill", "warranty", "contract", "generic", "pets", "home", "appliances"}:
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
    if field in {"due_date", "effective", "expiry", "start", "end", "date", "birth_date"}:
        _BIRTH_KWS  = ("born", "birth", "dob", "生日", "出生", "birthday")
        _BIRTH_ANTI = (
            "vaccin", "inject", "接种", "疫苗", "immunis",
            "desex", "steriliz", "castrat", "spay", "neuter",
            "surgery", "procedure", "operation", "去势", "绝育", "手术",
        )
        _EXPIRY_KWS = ("expir", "renew", "until", "到期", "有效至", "截止", "due")
        _EFFECT_KWS = ("effective", "from", "start", "commence", "生效", "起始", "begin")

        def _ctx_ok(m, field_name):
            if field_name not in {"birth_date", "effective", "expiry"}:
                return True
            ctx_s = max(0, m.start() - 150)
            ctx_e = min(len(raw), m.end() + 150)
            ctx   = raw[ctx_s:ctx_e].lower()
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
    if field in {"purchase_date", "warranty_end", "service_date", "maintenance_date", "vaccine_date", "next_due"}:
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
        m = re.search(r"(?:monthly|月供).{0,12}(?:aud|澳币|\$)?\s?(\d+(?:\.\d{1,2})?)", lowered, flags=re.I)
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
            "[page ", "we have made the change", "if you have already paid",
            "please find enclosed", "here is your updated", "motorcycle insurance",
        )
        _sentence_starts = (
            "safe ", "good ", "by ", "with ", "if ", "for ", "you ", "as ",
            "our ", "we ", "your ", "this ", "the ", "a ", "an ", "to ",
            "please ", "note ", "dear ", "thank ", "in ", "on ", "at ", "from ",
            "whilst ", "while ", "when ", "since ", "because ", "however ",
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
        m = re.search(r"(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}).{0,20}(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2})", raw)
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
    if field in {"parties", "obligation", "penalty", "notice_period", "action", "contact"}:
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


def _detail_rows_from_chunks(*, topic: str, chunks: list[dict[str, Any]], ui_lang: str) -> tuple[list[DetailRow], list[str]]:
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

def _dedupe_hits_by_chunk(hits: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for hit in hits:
        cid = str(getattr(hit, "chunk_id", "") or "").strip()
        if (not cid) or (cid in seen):
            continue
        seen.add(cid)
        out.append(hit)
    return out


def _build_related_docs(db: Session, doc_ids: list[str], *, cap: int = 6) -> list[AgentRelatedDoc]:
    unique_ids: list[str] = []
    seen: set[str] = set()
    for raw in doc_ids:
        doc_id = str(raw or "").strip()
        if (not doc_id) or (doc_id in seen):
            continue
        seen.add(doc_id)
        unique_ids.append(doc_id)
    if not unique_ids:
        return []

    rows = db.execute(
        select(Document).where(Document.id.in_(unique_ids), Document.status == DocumentStatus.COMPLETED.value)
    ).scalars().all()
    if not rows:
        return []

    by_id = {str(item.id): item for item in rows}
    ordered_rows: list[Document] = []
    for doc_id in unique_ids:
        found = by_id.get(doc_id)
        if found is None:
            continue
        ordered_rows.append(found)
        if len(ordered_rows) >= cap:
            break
    if not ordered_rows:
        return []

    tag_map = crud.get_document_tags_map(db, [item.id for item in ordered_rows])
    out: list[AgentRelatedDoc] = []
    for item in ordered_rows:
        source_available = crud.source_path_available(item.source_path)
        out.append(
            AgentRelatedDoc(
                doc_id=item.id,
                file_name=item.file_name,
                title_en=item.title_en,
                title_zh=item.title_zh,
                summary_en=item.summary_en,
                summary_zh=item.summary_zh,
                category_path=item.category_path,
                category_label_en=item.category_label_en,
                category_label_zh=item.category_label_zh,
                tags=tag_map.get(item.id, []),
                source_available=source_available,
                source_missing_reason="" if source_available else "source_file_missing",
                updated_at=item.updated_at,
            )
        )
    return out


def _collect_evidence_backed_doc_ids(bundle: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    detail_sections = list(bundle.get("detail_sections") or [])
    for section in detail_sections:
        for row in list(getattr(section, "rows", []) or []):
            for ev in list(getattr(row, "evidence_refs", []) or []):
                doc_id = str(getattr(ev, "doc_id", "") or "").strip()
                if doc_id and doc_id not in seen:
                    seen.add(doc_id)
                    out.append(doc_id)
    if out:
        return out

    evidence_map = bundle.get("evidence_map") or {}
    if isinstance(evidence_map, dict):
        for refs in evidence_map.values():
            if not isinstance(refs, list):
                continue
            for ev in refs:
                if not isinstance(ev, dict):
                    continue
                doc_id = str(ev.get("doc_id") or "").strip()
                if doc_id and doc_id not in seen:
                    seen.add(doc_id)
                    out.append(doc_id)
    if out:
        return out

    explicit = [str(x or "").strip() for x in (bundle.get("evidence_backed_doc_ids") or []) if str(x or "").strip()]
    for doc_id in explicit:
        if doc_id not in seen:
            seen.add(doc_id)
            out.append(doc_id)
    if out:
        return out

    for chunk in list(bundle.get("context_chunks") or [])[:10]:
        doc_id = str(chunk.get("doc_id") or "").strip()
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            out.append(doc_id)
    return out


def _apply_related_docs_selection(bundle: dict[str, Any]) -> tuple[str, int]:
    route = str(bundle.get("route") or "")
    related_docs = list(bundle.get("related_docs") or [])
    if route not in {"detail_extract", "entity_fact_lookup", "period_aggregate", "bill_attention", "bill_monthly_total"}:
        bundle["related_doc_selection_mode"] = str(bundle.get("related_doc_selection_mode") or "evidence_plus_candidates")
        return (str(bundle["related_doc_selection_mode"]), len(related_docs))

    evidence_doc_ids = _collect_evidence_backed_doc_ids(bundle)
    evidence_set = {doc_id for doc_id in evidence_doc_ids if doc_id}
    if evidence_set:
        related_docs = [doc for doc in related_docs if str(getattr(doc, "doc_id", "") or "") in evidence_set]
    else:
        related_docs = []
    bundle["related_docs"] = related_docs
    bundle["related_doc_selection_mode"] = "evidence_only"
    bundle["evidence_backed_doc_ids"] = evidence_doc_ids
    return ("evidence_only", len(evidence_doc_ids))


def _fill_chunks_from_doc_scope(db: Session, doc_ids: list[str], existing_chunk_ids: set[str], cap: int) -> list[dict[str, Any]]:
    if (not doc_ids) or cap <= 0:
        return []
    docs = (
        db.execute(
            select(Document)
            .where(Document.id.in_(doc_ids), Document.status == DocumentStatus.COMPLETED.value)
            .order_by(Document.updated_at.desc())
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for doc in docs:
        if not crud.source_path_available(doc.source_path):
            continue
        rows = (
            db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc()).limit(3))
            .scalars()
            .all()
        )
        for row in rows:
            if row.id in existing_chunk_ids:
                continue
            existing_chunk_ids.add(row.id)
            out.append(
                {
                    "doc_id": doc.id,
                    "chunk_id": row.id,
                    "score": 0.0,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": str(row.content or ""),
                }
            )
            if len(out) >= cap:
                return out
    return out


def _is_bill_attention_query(query: str) -> bool:
    lowered = str(query or "").lower()
    if not lowered:
        return False
    return any(token in lowered for token in _BILL_QUERY_HINTS)


def _format_due_date(value: dt.datetime | None, *, ui_lang: str) -> str:
    if value is None:
        return ""
    if ui_lang == "zh":
        return f"{value.year}年{value.month}月{value.day}日"
    return value.strftime("%Y-%m-%d")


def _format_amount(amount: float | None, currency: str, *, ui_lang: str) -> str:
    if amount is None:
        return ""
    code = str(currency or "AUD").upper()
    if ui_lang == "zh":
        if code == "AUD":
            return f"澳币{amount:.2f}"
        return f"{code} {amount:.2f}"
    return f"{code} {amount:.2f}"


def _bill_status_label(status: str, *, ui_lang: str) -> str:
    normalized = str(status or "").strip().lower()
    mapping = {
        "paid": ("Paid", "已缴费"),
        "unpaid": ("Unpaid", "待缴费"),
        "overdue": ("Overdue", "已逾期"),
        "unknown": ("Unknown", "状态未知"),
    }
    en, zh = mapping.get(normalized, mapping["unknown"])
    return zh if ui_lang == "zh" else en


def _build_bill_attention_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    allowed_ids = set(doc_ids)
    facet = _detect_query_facet(req.query)
    strict_categories = {str(item or "").strip().lower() for item in facet.strict_categories if str(item or "").strip()}
    rows = list_recent_bill_facts(db, limit=30)
    pending: list[dict[str, Any]] = []
    paid: list[dict[str, Any]] = []
    all_doc_ids: list[str] = []

    for fact, doc in rows:
        if not crud.source_path_available(doc.source_path):
            continue
        if allowed_ids and doc.id not in allowed_ids:
            continue
        if category_path and str(doc.category_path or "") != category_path:
            continue
        if strict_categories and str(doc.category_path or "").strip().lower() not in strict_categories:
            continue
        item = {
            "doc_id": doc.id,
            "title_en": doc.title_en,
            "title_zh": doc.title_zh,
            "category_path": doc.category_path,
            "amount_due": fact.amount_due,
            "currency": fact.currency,
            "due_date": fact.due_date,
            "payment_status": fact.payment_status,
            "confidence": fact.confidence,
        }
        all_doc_ids.append(doc.id)
        status = str(fact.payment_status or "unknown").lower()
        if status == "paid":
            paid.append(item)
        else:
            pending.append(item)

    pending = sorted(
        pending,
        key=lambda item: (
            0 if str(item.get("payment_status") or "").lower() == "overdue" else 1,
            _as_utc_datetime(item.get("due_date")) or dt.datetime.max.replace(tzinfo=dt.UTC),
        ),
    )
    paid = sorted(paid, key=lambda item: _as_utc_datetime(item.get("due_date")) or dt.datetime.max.replace(tzinfo=dt.UTC))
    selected = (pending + paid)[:10]

    context_chunks: list[dict[str, Any]] = []
    sources: list[ResultCardSource] = []
    for idx, item in enumerate(selected):
        amount = _format_amount(item.get("amount_due"), str(item.get("currency") or "AUD"), ui_lang=req.ui_lang)
        due_date = _format_due_date(item.get("due_date"), ui_lang=req.ui_lang)
        status = _bill_status_label(str(item.get("payment_status") or ""), ui_lang=req.ui_lang)
        if req.ui_lang == "zh":
            text = f"账单：{item.get('title_zh') or item.get('title_en')}；金额：{amount or '未提取'}；截止：{due_date or '未提取'}；状态：{status}"
            label = str(item.get("title_zh") or item.get("title_en") or "账单")
        else:
            text = (
                f"Bill: {item.get('title_en') or item.get('title_zh')}; Amount: {amount or 'n/a'}; "
                f"Due: {due_date or 'n/a'}; Status: {status}"
            )
            label = str(item.get("title_en") or item.get("title_zh") or "Bill")
        chunk_id = f"bill-fact-{idx + 1}"
        context_chunks.append(
            {
                "doc_id": str(item.get("doc_id") or ""),
                "chunk_id": chunk_id,
                "score": float(item.get("confidence") or 0.0),
                "title_en": str(item.get("title_en") or ""),
                "title_zh": str(item.get("title_zh") or ""),
                "category_path": str(item.get("category_path") or ""),
                "text": text,
            }
        )
        sources.append(ResultCardSource(doc_id=str(item.get("doc_id") or ""), chunk_id=chunk_id, label=label))

    related_docs = _build_related_docs(db, all_doc_ids, cap=6)
    return {
        "route": "bill_attention",
        "context_chunks": context_chunks,
        "sources": sources[:6],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len({item.doc_id for item in related_docs}),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "",
        "facet_mode": "strict_topic" if facet.strict_mode else "none",
        "facet_keys": list(facet.facet_keys),
        "bill_attention": {
            "pending": pending[:6],
            "paid": paid[:6],
        },
        "fact_route": "bill_attention",
        "fact_month": "",
        "related_doc_selection_mode": "evidence_only",
        "evidence_backed_doc_ids": [str(item.get("doc_id") or "") for item in selected if str(item.get("doc_id") or "").strip()],
    }


def _bill_fact_anchor_date(fact: Any) -> dt.datetime | None:
    for key in ("due_date", "billing_period_end", "billing_period_start"):
        value = getattr(fact, key, None)
        if isinstance(value, dt.datetime):
            return value
    return None


def _month_label(*, year: int | None, month: int | None) -> str:
    if month is None:
        return ""
    if year is None:
        return f"{dt.datetime.now(dt.UTC).year:04d}-{month:02d}"
    return f"{int(year):04d}-{int(month):02d}"


def _as_utc_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _month_pairs_between(start: dt.datetime, end: dt.datetime) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    s = _as_utc_datetime(start)
    e = _as_utc_datetime(end)
    if s is None or e is None:
        return out
    if s > e:
        s, e = e, s
    cursor = dt.datetime(s.year, s.month, 1, tzinfo=dt.UTC)
    end_month = dt.datetime(e.year, e.month, 1, tzinfo=dt.UTC)
    steps = 0
    while cursor <= end_month and steps < 120:
        out.add((int(cursor.year), int(cursor.month)))
        if cursor.month == 12:
            cursor = dt.datetime(cursor.year + 1, 1, 1, tzinfo=dt.UTC)
        else:
            cursor = dt.datetime(cursor.year, cursor.month + 1, 1, tzinfo=dt.UTC)
        steps += 1
    return out


def _bill_fact_month_pairs(fact: Any) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    due = _as_utc_datetime(getattr(fact, "due_date", None))
    if due is not None:
        out.add((int(due.year), int(due.month)))
    start = _as_utc_datetime(getattr(fact, "billing_period_start", None))
    end = _as_utc_datetime(getattr(fact, "billing_period_end", None))
    if start is not None and end is not None:
        out |= _month_pairs_between(start, end)
    elif start is not None:
        out.add((int(start.year), int(start.month)))
    elif end is not None:
        out.add((int(end.year), int(end.month)))
    return out


def _is_formal_bill_doc(doc: Any) -> bool:
    text = "\n".join(
        [
            str(getattr(doc, "file_name", "") or ""),
            str(getattr(doc, "title_zh", "") or ""),
            str(getattr(doc, "title_en", "") or ""),
        ]
    ).lower()
    return not any(token in text for token in _NON_FORMAL_BILL_DOC_HINTS)


def _is_monthly_eligible_bill_fact(fact: Any, doc: Any) -> tuple[bool, str]:
    amount = getattr(fact, "amount_due", None)
    if amount is None:
        return (False, "missing_amount")
    if not _bill_fact_month_pairs(fact):
        return (False, "missing_date_anchor")
    if not _is_formal_bill_doc(doc):
        return (False, "non_formal_doc")
    return (True, "")


def _infer_latest_year_for_month(rows: list[tuple[Any, Any]], target_month: int | None) -> int | None:
    if target_month is None:
        return None
    years: list[int] = []
    for fact, _doc in rows:
        for year, month in _bill_fact_month_pairs(fact):
            if int(month) == int(target_month):
                years.append(int(year))
    if years:
        return max(years)
    all_years: list[int] = []
    for fact, _doc in rows:
        for year, _month in _bill_fact_month_pairs(fact):
            all_years.append(int(year))
    return max(all_years) if all_years else None


def _build_bill_monthly_total_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    target_year, target_month = _extract_month_scope(req.query)
    rows = list_recent_bill_facts(
        db,
        limit=60,
        target_month=target_month if target_month is not None else None,
        target_year=target_year if target_month is not None and target_year is not None else None,
    )
    explicit_selected_doc_ids = []
    if isinstance(req.client_context, dict):
        selected = req.client_context.get("selected_doc_ids")
        if isinstance(selected, list):
            explicit_selected_doc_ids = [str(item or "").strip() for item in selected if str(item or "").strip()]
    allowed_doc_ids = set(explicit_selected_doc_ids)

    if target_month is not None and target_year is None:
        target_year = _infer_latest_year_for_month(rows, target_month)

    month_rows: list[tuple[Any, Any, dt.datetime]] = []
    rejected = {
        "source_unavailable": 0,
        "out_of_scope_doc_ids": 0,
        "out_of_scope_category": 0,
        "missing_amount": 0,
        "missing_date_anchor": 0,
        "non_formal_doc": 0,
        "out_of_month": 0,
    }
    for fact, doc in rows:
        if not crud.source_path_available(doc.source_path):
            rejected["source_unavailable"] += 1
            continue
        if allowed_doc_ids and doc.id not in allowed_doc_ids:
            rejected["out_of_scope_doc_ids"] += 1
            continue
        if category_path and str(doc.category_path or "") != category_path:
            rejected["out_of_scope_category"] += 1
            continue
        ok, reason = _is_monthly_eligible_bill_fact(fact, doc)
        if not ok:
            rejected[reason] += 1
            continue
        month_pairs = _bill_fact_month_pairs(fact)
        if target_month is not None and target_year is not None:
            if (int(target_year), int(target_month)) not in month_pairs:
                rejected["out_of_month"] += 1
                continue
        elif target_month is not None:
            if not any(int(month) == int(target_month) for _year, month in month_pairs):
                rejected["out_of_month"] += 1
                continue
        anchor = _bill_fact_anchor_date(fact)
        if anchor is None:
            rejected["missing_date_anchor"] += 1
            continue
        month_rows.append((fact, doc, anchor))

    pending: list[dict[str, Any]] = []
    paid: list[dict[str, Any]] = []
    related_doc_ids: list[str] = []
    total_amount = 0.0
    counted = 0

    for fact, doc, anchor in sorted(month_rows, key=lambda row: row[2]):
        status = str(getattr(fact, "payment_status", "") or "").lower()
        amount = getattr(fact, "amount_due", None)
        if amount is not None:
            try:
                total_amount += float(amount)
                counted += 1
            except Exception:
                pass
        item = {
            "doc_id": doc.id,
            "title_en": doc.title_en,
            "title_zh": doc.title_zh,
            "category_path": doc.category_path,
            "amount_due": amount,
            "currency": str(getattr(fact, "currency", "") or "AUD"),
            "due_date": getattr(fact, "due_date", None),
            "payment_status": status or "unknown",
            "confidence": float(getattr(fact, "confidence", 0.0) or 0.0),
            "anchor_date": anchor,
        }
        related_doc_ids.append(doc.id)
        if status == "paid":
            paid.append(item)
        else:
            pending.append(item)

    context_chunks: list[dict[str, Any]] = []
    sources: list[ResultCardSource] = []
    selected = (pending + paid)[:12]
    for idx, item in enumerate(selected):
        amount = _format_amount(item.get("amount_due"), str(item.get("currency") or "AUD"), ui_lang=req.ui_lang)
        due_date = _format_due_date(item.get("due_date"), ui_lang=req.ui_lang)
        status_label = _bill_status_label(str(item.get("payment_status") or ""), ui_lang=req.ui_lang)
        label = str(item.get("title_zh") or item.get("title_en") or "账单")
        text = (
            f"账单：{label}；金额：{amount or '未提取'}；截止：{due_date or '未提取'}；状态：{status_label}"
            if req.ui_lang == "zh"
            else f"Bill: {label}; Amount: {amount or 'n/a'}; Due: {due_date or 'n/a'}; Status: {status_label}"
        )
        chunk_id = f"bill-month-{idx + 1}"
        context_chunks.append(
            {
                "doc_id": str(item.get("doc_id") or ""),
                "chunk_id": chunk_id,
                "score": float(item.get("confidence") or 0.0),
                "title_en": str(item.get("title_en") or ""),
                "title_zh": str(item.get("title_zh") or ""),
                "category_path": str(item.get("category_path") or ""),
                "text": text,
            }
        )
        sources.append(ResultCardSource(doc_id=str(item.get("doc_id") or ""), chunk_id=chunk_id, label=label))

    related_docs = _build_related_docs(db, related_doc_ids, cap=6)
    month_txt = _month_label(year=target_year, month=target_month)
    logger.info(
        "bill_monthly_total_eval",
        extra=sanitize_log_context(
            {
                "month_key": month_txt,
                "candidates": len(rows),
                "included": len(month_rows),
                "excluded_reason": rejected,
            }
        ),
    )
    return {
        "route": "bill_monthly_total",
        "context_chunks": context_chunks,
        "sources": sources[:6],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len({item.doc_id for item in related_docs}),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "",
        "bill_monthly": {
            "month": month_txt,
            "pending": pending[:8],
            "paid": paid[:8],
            "total_amount": round(total_amount, 2),
            "currency": "AUD",
            "counted_docs": int(counted),
        },
        "fact_route": "bill_monthly_total",
        "fact_month": month_txt,
    }


def _build_queue_bundle(db: Session, req: AgentExecuteRequest) -> dict[str, Any]:
    totals = crud.get_queue_totals(db)
    jobs = db.execute(select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(6)).scalars().all()
    docs = db.execute(select(Document).order_by(Document.updated_at.desc()).limit(10)).scalars().all()
    docs = [item for item in docs if crud.source_path_available(item.source_path)]

    context_chunks: list[dict[str, Any]] = [
        {
            "doc_id": "",
            "chunk_id": "queue-totals",
            "score": 1.0,
            "title_en": "Queue Totals",
            "title_zh": "队列统计",
            "category_path": "system/queue",
            "text": (
                f"documents={int(totals.get('documents') or 0)}, "
                f"pending_documents={int(totals.get('pending_documents') or 0)}, "
                f"jobs={int(totals.get('jobs') or 0)}, "
                f"running_jobs={int(totals.get('running_jobs') or 0)}"
            ),
        }
    ]
    for idx, job in enumerate(jobs[:5], start=1):
        context_chunks.append(
            {
                "doc_id": "",
                "chunk_id": f"queue-job-{idx}",
                "score": 0.9,
                "title_en": "Ingestion Job",
                "title_zh": "入库任务",
                "category_path": "system/queue",
                "text": (
                    f"job_id={job.id}, status={job.status}, success={job.success_count}, failed={job.failed_count}, "
                    f"duplicate={job.duplicate_count}"
                ),
            }
        )

    related_docs = _build_related_docs(db, [doc.id for doc in docs], cap=4)
    sources: list[ResultCardSource] = [
        ResultCardSource(doc_id=item.doc_id, chunk_id=f"doc-ref-{idx+1}", label=item.title_zh or item.title_en or item.file_name)
        for idx, item in enumerate(related_docs)
    ]
    return {
        "route": "queue_snapshot",
        "context_chunks": context_chunks[:10],
        "sources": sources[:5],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len(related_docs),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "",
        "queue_totals": totals,
    }


def _extract_tag_keys_from_query(query: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for matched in _TAG_KEY_RE.findall(str(query or "").lower()):
        key = str(matched or "").strip()
        if (not key) or (key in seen):
            continue
        seen.add(key)
        out.append(key)
    return out[:12]


def _build_reprocess_bundle(db: Session, req: AgentExecuteRequest, *, doc_ids: list[str]) -> dict[str, Any]:
    target_ids = doc_ids[:3]
    if not target_ids:
        return {
            "route": "reprocess_exec",
            "context_chunks": [
                {
                    "doc_id": "",
                    "chunk_id": "reprocess-empty",
                    "score": 0.1,
                    "title_en": "Reprocess",
                    "title_zh": "重处理",
                    "category_path": "system/reprocess",
                    "text": "No selected document IDs were provided for reprocess.",
                }
            ],
            "sources": [],
            "related_docs": [],
            "hit_count": 0,
            "doc_count": 0,
            "query_en": "",
            "bilingual_search": False,
            "qdrant_used": False,
            "retrieval_mode": "structured",
            "vector_hit_count": 0,
            "lexical_hit_count": 0,
            "fallback_reason": "no_selected_docs",
        }

    context_chunks: list[dict[str, Any]] = []
    related_doc_ids: list[str] = []
    for idx, doc_id in enumerate(target_ids, start=1):
        doc = db.get(Document, doc_id)
        if doc is None:
            context_chunks.append(
                {
                    "doc_id": "",
                    "chunk_id": f"reprocess-missing-{idx}",
                    "score": 0.1,
                    "title_en": "Reprocess",
                    "title_zh": "重处理",
                    "category_path": "system/reprocess",
                    "text": f"document_not_found: {doc_id}",
                }
            )
            continue
        related_doc_ids.append(doc.id)
        if not str(doc.source_path or "").strip():
            context_chunks.append(
                {
                    "doc_id": doc.id,
                    "chunk_id": f"reprocess-nosource-{idx}",
                    "score": 0.2,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": f"reprocess_skipped: {doc.file_name}, reason=document_has_no_source_path",
                }
            )
            continue
        job = crud.create_ingestion_job(db, [str(doc.source_path)])
        mode = enqueue_ingestion_job(job.id, force_reprocess=True, reprocess_doc_id=doc.id)
        context_chunks.append(
            {
                "doc_id": doc.id,
                "chunk_id": f"reprocess-job-{job.id}",
                "score": 0.95,
                "title_en": doc.title_en,
                "title_zh": doc.title_zh,
                "category_path": doc.category_path,
                "text": f"reprocess_queued: file={doc.file_name}, job_id={job.id}, queue_mode={mode}",
            }
        )

    related_docs = _build_related_docs(db, related_doc_ids, cap=4)
    sources = [
        ResultCardSource(doc_id=item.doc_id, chunk_id=f"reprocess-ref-{idx+1}", label=item.title_zh or item.title_en or item.file_name)
        for idx, item in enumerate(related_docs)
    ]
    return {
        "route": "reprocess_exec",
        "context_chunks": context_chunks[:10],
        "sources": sources[:5],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len(related_docs),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "",
    }


def _build_tag_update_bundle(db: Session, req: AgentExecuteRequest, *, doc_ids: list[str]) -> dict[str, Any]:
    target_ids = doc_ids[:3]
    tag_keys = _extract_tag_keys_from_query(req.query)
    context_chunks: list[dict[str, Any]] = []
    related_doc_ids: list[str] = []
    updated = 0

    for idx, doc_id in enumerate(target_ids, start=1):
        doc = db.get(Document, doc_id)
        if doc is None:
            continue
        related_doc_ids.append(doc.id)
        if not tag_keys:
            context_chunks.append(
                {
                    "doc_id": doc.id,
                    "chunk_id": f"tag-suggest-{idx}",
                    "score": 0.3,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": f"tag_update_noop: file={doc.file_name}, reason=no_tag_keys_found",
                }
            )
            continue
        _rows, invalid = crud.patch_document_tags(db, document_id=doc.id, add=tag_keys, remove=[])
        if invalid:
            context_chunks.append(
                {
                    "doc_id": doc.id,
                    "chunk_id": f"tag-invalid-{idx}",
                    "score": 0.2,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": f"tag_update_failed: file={doc.file_name}, reason={','.join(invalid)}",
                }
            )
            continue
        updated += 1
        context_chunks.append(
            {
                "doc_id": doc.id,
                "chunk_id": f"tag-ok-{idx}",
                "score": 0.9,
                "title_en": doc.title_en,
                "title_zh": doc.title_zh,
                "category_path": doc.category_path,
                "text": f"tag_update_applied: file={doc.file_name}, add={','.join(tag_keys)}",
            }
        )

    if updated > 0:
        db.commit()
    related_docs = _build_related_docs(db, related_doc_ids, cap=4)
    sources = [
        ResultCardSource(doc_id=item.doc_id, chunk_id=f"tag-ref-{idx+1}", label=item.title_zh or item.title_en or item.file_name)
        for idx, item in enumerate(related_docs)
    ]
    if not context_chunks:
        context_chunks = [
            {
                "doc_id": "",
                "chunk_id": "tag-empty",
                "score": 0.1,
                "title_en": "Tag Update",
                "title_zh": "标签更新",
                "category_path": "system/tags",
                "text": "No selected documents found for tag update.",
            }
        ]
    return {
        "route": "tag_update_exec",
        "context_chunks": context_chunks[:10],
        "sources": sources[:5],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len(related_docs),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "",
    }


def _build_detail_extract_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    lowered_query = str(req.query or "").lower()
    topic = _resolve_detail_topic(req.query, planner.doc_scope if isinstance(planner.doc_scope, dict) else {})
    selected_ids = [str(x or "").strip() for x in doc_ids if str(x or "").strip()]
    if selected_ids:
        related_docs = _build_related_docs(db, selected_ids, cap=10)
    else:
        seed_bundle = _build_search_bundle(db, req, planner, doc_ids=[], category_path=category_path)
        related_docs = seed_bundle.get("related_docs") or []

    _active_prefixes: tuple[str, ...] = ()
    scoped_docs = related_docs
    if topic == "insurance":
        insurance_prefixes: tuple[str, ...] = ("home/insurance", "health/insurance", "legal/insurance")
        if any(tok in lowered_query for tok in ("pet insurance", "宠物保险")):
            insurance_prefixes = ("home/insurance/pet",)
        elif any(tok in lowered_query for tok in (
            "car insurance", "vehicle insurance", "motor insurance", "车险", "车辆保险",
            # common car brands (CN + EN) — named vehicle implies car insurance context
            "tesla", "特斯拉", "toyota", "丰田", "honda", "本田", "bmw", "宝马",
            "mercedes", "奔驰", "audi", "奥迪", "ford", "福特", "hyundai", "现代",
            "mazda", "马自达", "subaru", "斯巴鲁", "volkswagen", "vw", "大众",
            "nissan", "日产", "kia", "起亚", "lexus", "雷克萨斯",
        )):
            insurance_prefixes = ("home/insurance/vehicle",)
        elif any(tok in lowered_query for tok in ("health insurance", "private health", "hospital cover", "医保", "医疗险")):
            insurance_prefixes = ("health/insurance",)
        _active_prefixes = insurance_prefixes
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(insurance_prefixes)]
    elif topic == "bill":
        _active_prefixes = ("finance/bills",)
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith("finance/bills")]
    elif topic == "home":
        _active_prefixes = ("home/property", "home/maintenance", "legal/property")
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(_active_prefixes)]
    elif topic == "appliances":
        _active_prefixes = ("home/manuals", "home/appliances", "tech/hardware")
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(_active_prefixes)]
    elif topic == "pets":
        pet_prefixes: tuple[str, ...] = ("home/pets", "health/medical_records", "home/insurance/pet")
        if any(tok in lowered_query for tok in ("birthday", "birth date", "dob", "生日", "出生日期")):
            # Broaden to health/insurance — desexing/medical certs may be filed there
            pet_prefixes = ("home/pets", "health/medical_records", "health/insurance")
        _active_prefixes = pet_prefixes
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(pet_prefixes)]
    elif topic == "warranty":
        _active_prefixes = ("home/manuals", "home/appliances", "tech/hardware", "home/maintenance")
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(_active_prefixes)]
    elif topic == "contract":
        _active_prefixes = ("legal/contracts", "legal/property")
        scoped_docs = [doc for doc in related_docs if str(doc.category_path or "").startswith(_active_prefixes)]
    if (not scoped_docs) and topic == "generic":
        scoped_docs = related_docs[:6]
    elif not scoped_docs and _active_prefixes:
        # Vector search found nothing for this topic — fall back to direct category DB query.
        _fb_ids: list[str] = []
        _seen_fb: set[str] = set()
        for _prefix in _active_prefixes:
            _fb_rows = db.execute(
                select(Document.id).where(
                    Document.category_path.startswith(_prefix),
                    Document.status == DocumentStatus.COMPLETED.value,
                ).limit(8)
            ).scalars().all()
            for _row in _fb_rows:
                _k = str(_row)
                if _k not in _seen_fb:
                    _fb_ids.append(_k)
                    _seen_fb.add(_k)
            if len(_fb_ids) >= 8:
                break
        if _fb_ids:
            scoped_docs = _build_related_docs(db, _fb_ids, cap=8)

    context_chunks: list[dict[str, Any]] = []
    sources: list[ResultCardSource] = []
    docs_scanned = len(scoped_docs)
    docs_matched = 0
    for doc in scoped_docs[:8]:
        if len(context_chunks) >= 10:
            break
        rows = (
            db.execute(select(Chunk).where(Chunk.document_id == doc.doc_id).order_by(Chunk.chunk_index.asc()).limit(4))
            .scalars()
            .all()
        )
        if not rows:
            continue
        docs_matched += 1
        for row in rows:
            context_chunks.append(
                {
                    "doc_id": doc.doc_id,
                    "chunk_id": row.id,
                    "score": 0.6,
                    "title_en": doc.title_en,
                    "title_zh": doc.title_zh,
                    "category_path": doc.category_path,
                    "text": str(row.content or ""),
                }
            )
        sources.append(
            ResultCardSource(
                doc_id=doc.doc_id,
                chunk_id=str(rows[0].id),
                label=str(doc.title_zh or doc.title_en or doc.file_name),
            )
        )

    _howto_tokens = ("方法", "如何", "怎么", "步骤", "怎样", "how to", "how do", "how can", "what steps", "维护方法", "使用方法", "操作方法")
    _is_howto = any(tok in lowered_query for tok in _howto_tokens)
    if _is_howto and topic in {"generic", "home", "appliances"}:
        detail_rows, missing_fields = [], []
    else:
        detail_rows, missing_fields = _detail_rows_from_chunks(topic=topic, chunks=context_chunks, ui_lang=req.ui_lang)
    fields_filled = sum(1 for row in detail_rows if str(row.value_zh or row.value_en).strip())
    detail_sections = [DetailSection(section_name=f"{topic}_details", rows=detail_rows)] if detail_rows else []
    evidence_doc_ids: list[str] = []
    seen_evidence_docs: set[str] = set()
    for row in detail_rows:
        for ev in list(getattr(row, "evidence_refs", []) or []):
            doc_id = str(getattr(ev, "doc_id", "") or "").strip()
            if (not doc_id) or (doc_id in seen_evidence_docs):
                continue
            seen_evidence_docs.add(doc_id)
            evidence_doc_ids.append(doc_id)
    if evidence_doc_ids:
        scoped_docs = [doc for doc in scoped_docs if str(doc.doc_id or "") in seen_evidence_docs]
    return {
        "route": "detail_extract",
        "context_chunks": context_chunks[:12],
        "sources": sources[:8],
        "related_docs": scoped_docs[:8],
        "hit_count": len(context_chunks),
        "doc_count": len(scoped_docs[:8]),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "detail_zero_hit" if not context_chunks else "",
        "detail_topic": topic,
        "detail_mode": "structured",
        "detail_rows_count": len(detail_rows),
        "detail_sections": detail_sections,
        "missing_fields": missing_fields,
        "coverage_stats": DetailCoverageStats(
            docs_scanned=int(docs_scanned),
            docs_matched=int(docs_matched),
            fields_filled=int(fields_filled),
        ),
        "related_doc_selection_mode": "evidence_only" if evidence_doc_ids else "evidence_plus_candidates",
        "evidence_backed_doc_ids": evidence_doc_ids,
    }


def _build_entity_fact_lookup_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    # Reuse generic detail extraction templates, but expose route as entity_fact_lookup
    # so routing/eval can audit structured usage.
    out = _build_detail_extract_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
    out["route"] = "entity_fact_lookup"
    out["route_reason"] = "entity_fact_structured"
    out["fact_route"] = "none"
    out["fact_month"] = ""
    return out


def _extract_period_months(query: str) -> int:
    text = str(query or "").lower()
    # Chinese and English month-window hints
    m = re.search(r"过去\s*(\d{1,2})\s*个?月", text)
    if m:
        try:
            val = int(m.group(1))
            if 1 <= val <= 24:
                return val
        except Exception:
            pass
    m = re.search(r"last\s*(\d{1,2})\s*months?", text)
    if m:
        try:
            val = int(m.group(1))
            if 1 <= val <= 24:
                return val
        except Exception:
            pass
    if "上季度" in text or "last quarter" in text:
        return 3
    if "半年" in text or "six months" in text:
        return 6
    if "一年" in text or "last year" in text:
        return 12
    return 6


def _build_period_aggregate_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    # Prefer structured bill facts for period aggregate questions.
    months = _extract_period_months(req.query)
    facet = _detect_query_facet(req.query)
    strict_categories = {str(item or "").strip().lower() for item in facet.strict_categories if str(item or "").strip()}
    now = dt.datetime.now(dt.UTC)
    window_start = now - dt.timedelta(days=31 * max(1, months))
    rows = list_recent_bill_facts(db, limit=max(60, months * 12), since=window_start)
    allowed_doc_ids = set(doc_ids or [])
    items: list[dict[str, Any]] = []
    related_doc_ids: list[str] = []
    total_amount = 0.0
    for fact, doc in rows:
        if allowed_doc_ids and doc.id not in allowed_doc_ids:
            continue
        if category_path and str(doc.category_path or "") != category_path:
            continue
        if strict_categories and str(doc.category_path or "").strip().lower() not in strict_categories:
            continue
        if not crud.source_path_available(doc.source_path):
            continue
        amount = getattr(fact, "amount_due", None)
        if amount is None:
            continue
        anchor = _bill_fact_anchor_date(fact)
        if anchor is None:
            continue
        anchor_utc = _as_utc_datetime(anchor)
        if anchor_utc is None or anchor_utc < window_start:
            continue
        try:
            amt = float(amount)
        except Exception:
            continue
        total_amount += amt
        status = str(getattr(fact, "payment_status", "") or "unknown").lower()
        item = {
            "doc_id": doc.id,
            "title_en": doc.title_en,
            "title_zh": doc.title_zh,
            "category_path": doc.category_path,
            "amount_due": amt,
            "currency": str(getattr(fact, "currency", "") or "AUD"),
            "due_date": getattr(fact, "due_date", None),
            "payment_status": status,
            "anchor": anchor_utc,
            "confidence": float(getattr(fact, "confidence", 0.0) or 0.0),
        }
        items.append(item)
        related_doc_ids.append(doc.id)
    items.sort(key=lambda row: row.get("anchor") or now, reverse=True)

    context_chunks: list[dict[str, Any]] = []
    sources: list[ResultCardSource] = []
    for idx, item in enumerate(items[:12], start=1):
        amount_txt = _format_amount(item.get("amount_due"), str(item.get("currency") or "AUD"), ui_lang=req.ui_lang)
        due_txt = _format_due_date(item.get("due_date"), ui_lang=req.ui_lang)
        status_txt = _bill_status_label(str(item.get("payment_status") or ""), ui_lang=req.ui_lang)
        label = str(item.get("title_zh") or item.get("title_en") or "账单")
        text = (
            f"周期聚合账单：{label}；金额：{amount_txt or '未提取'}；日期：{due_txt or '未提取'}；状态：{status_txt}"
            if req.ui_lang == "zh"
            else f"Period aggregate bill: {label}; Amount: {amount_txt or 'n/a'}; Date: {due_txt or 'n/a'}; Status: {status_txt}"
        )
        chunk_id = f"period-agg-{idx}"
        context_chunks.append(
            {
                "doc_id": str(item.get("doc_id") or ""),
                "chunk_id": chunk_id,
                "score": float(item.get("confidence") or 0.0),
                "title_en": str(item.get("title_en") or ""),
                "title_zh": str(item.get("title_zh") or ""),
                "category_path": str(item.get("category_path") or ""),
                "text": text,
            }
        )
        sources.append(ResultCardSource(doc_id=str(item.get("doc_id") or ""), chunk_id=chunk_id, label=label))

    related_docs = _build_related_docs(db, related_doc_ids, cap=6)
    if not items:
        return {
            "route": "period_aggregate",
            "context_chunks": [],
            "sources": [],
            "related_docs": related_docs,
            "hit_count": 0,
            "doc_count": len(related_docs),
            "query_en": "",
            "bilingual_search": False,
            "qdrant_used": False,
            "retrieval_mode": "structured",
            "vector_hit_count": 0,
            "lexical_hit_count": 0,
            "fallback_reason": "period_aggregate_empty",
            "detail_topic": "bill",
            "detail_mode": "structured",
            "detail_rows_count": 0,
            "detail_sections": [],
            "missing_fields": ["amount", "date"],
            "coverage_stats": DetailCoverageStats(
                docs_scanned=int(len(rows)),
                docs_matched=0,
                fields_filled=0,
            ),
            "period_aggregate": {
                "months": months,
                "total_amount": None,
                "currency": "AUD",
                "matched_bills": 0,
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
            },
            "fact_route": "none",
            "fact_month": "",
            "route_reason": "period_aggregate_structured_empty",
            "facet_mode": "strict_topic" if facet.strict_mode else "none",
            "facet_keys": list(facet.facet_keys),
            "related_doc_selection_mode": "evidence_only",
            "evidence_backed_doc_ids": [],
        }
    summary_row = DetailRow(
        field="period_total_amount",
        label_en="Total Amount",
        label_zh="总金额",
        value_en=f"AUD {round(total_amount, 2):.2f}",
        value_zh=f"澳币{round(total_amount, 2):.2f}",
        evidence_refs=[],
    )
    docs_row = DetailRow(
        field="period_docs_count",
        label_en="Matched Bills",
        label_zh="命中账单数",
        value_en=str(len(items)),
        value_zh=str(len(items)),
        evidence_refs=[],
    )
    return {
        "route": "period_aggregate",
        "context_chunks": context_chunks,
        "sources": sources[:8],
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len({doc.doc_id for doc in related_docs}),
        "query_en": "",
        "bilingual_search": False,
        "qdrant_used": False,
        "retrieval_mode": "structured",
        "vector_hit_count": 0,
        "lexical_hit_count": 0,
        "fallback_reason": "period_aggregate_empty" if not items else "",
        "detail_topic": "bill",
        "detail_mode": "structured",
        "detail_rows_count": 2,
        "detail_sections": [
            DetailSection(
                section_name="period_aggregate",
                rows=[summary_row, docs_row],
            )
        ],
        "missing_fields": [] if items else (["amount", "date"]),
        "coverage_stats": DetailCoverageStats(
            docs_scanned=int(len(rows)),
            docs_matched=int(len(items)),
            fields_filled=2 if items else 0,
        ),
        "period_aggregate": {
            "months": months,
            "total_amount": round(total_amount, 2),
            "currency": "AUD",
            "matched_bills": len(items),
            "window_start": window_start.isoformat(),
            "window_end": now.isoformat(),
        },
        "fact_route": "none",
        "fact_month": "",
        "route_reason": "period_aggregate_structured",
        "facet_mode": "strict_topic" if facet.strict_mode else "none",
        "facet_keys": list(facet.facet_keys),
        "related_doc_selection_mode": "evidence_only",
        "evidence_backed_doc_ids": [str(item.get("doc_id") or "") for item in items if str(item.get("doc_id") or "").strip()],
    }


def _build_search_bundle(
    db: Session,
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    *,
    doc_ids: list[str],
    category_path: str | None,
) -> dict[str, Any]:
    facet = _detect_query_facet(req.query)
    domain_whitelist = tuple(path.lower() for path in _domain_category_whitelist(req.query, facet))
    query_required_terms = _query_required_terms(req.query)
    historical_fact_query = _is_historical_fact_query(req.query)
    strict_categories = {str(item or "").strip().lower() for item in facet.strict_categories if str(item or "").strip()}
    required_terms = [str(item or "").strip().lower() for item in facet.required_terms if str(item or "").strip()]

    effective_category_path = category_path
    if (not effective_category_path) and facet.strict_mode and len(strict_categories) == 1:
        effective_category_path = next(iter(strict_categories))

    search_req = SearchRequest(
        query=req.query,
        top_k=12,
        score_threshold=0.0,
        ui_lang=planner.ui_lang if planner.ui_lang in {"zh", "en"} else ("zh" if req.ui_lang == "zh" else "en"),
        query_lang=planner.query_lang if planner.query_lang in {"zh", "en"} else req.query_lang,
        category_path=effective_category_path,
        include_missing=False,
    )
    search_res = search_documents(db, search_req)
    hits = _dedupe_hits_by_chunk(search_res.hits)
    if doc_ids:
        allowed_doc_ids = set(doc_ids)
        hits = [hit for hit in hits if str(hit.doc_id) in allowed_doc_ids]

    candidate_doc_ids = [str(hit.doc_id or "").strip() for hit in hits if str(hit.doc_id or "").strip()]
    candidate_docs = db.execute(select(Document).where(Document.id.in_(set(candidate_doc_ids)))).scalars().all()
    doc_map = {str(item.id): item for item in candidate_docs}

    top_hits = hits[:10]
    hit_chunk_ids = [str(hit.chunk_id or "").strip() for hit in top_hits if str(hit.chunk_id or "").strip()]
    chunk_rows = db.execute(select(Chunk).where(Chunk.id.in_(set(hit_chunk_ids)))).scalars().all() if hit_chunk_ids else []
    chunk_map = {str(chunk.id): chunk for chunk in chunk_rows}

    context_chunks: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    doc_best_score: dict[str, float] = {}
    doc_first_seen: dict[str, int] = {}
    for hit in top_hits:
        chunk = chunk_map.get(str(hit.chunk_id or "").strip())
        if chunk is None:
            continue
        if chunk.id in seen_chunk_ids:
            continue
        doc_id = str(hit.doc_id or "").strip()
        if not doc_id:
            continue

        if facet.strict_mode:
            hit_category = str(hit.category_path or "").strip().lower()
            if strict_categories and hit_category not in strict_categories:
                continue
            doc = doc_map.get(doc_id)
            text_blob = " ".join(
                [
                    str(hit.title_zh or ""),
                    str(hit.title_en or ""),
                    str(hit.category_path or ""),
                    str(getattr(doc, "file_name", "") or ""),
                    str(getattr(doc, "summary_zh", "") or ""),
                    str(getattr(doc, "summary_en", "") or ""),
                    " ".join(str(item or "") for item in (getattr(hit, "tags", []) or [])),
                    str(chunk.content or ""),
                ]
            ).lower()
            if required_terms and (not any(term in text_blob for term in required_terms)):
                continue
            if historical_fact_query and _looks_planned_or_proposal_doc(text_blob):
                continue
        else:
            text_blob = ""
            if domain_whitelist or query_required_terms:
                hit_category = str(hit.category_path or "").strip().lower()
                if domain_whitelist and (not any(hit_category.startswith(path) for path in domain_whitelist)):
                    continue
                doc = doc_map.get(doc_id)
                text_blob = " ".join(
                    [
                        str(hit.title_zh or ""),
                        str(hit.title_en or ""),
                        str(hit.category_path or ""),
                        str(getattr(doc, "file_name", "") or ""),
                        str(getattr(doc, "summary_zh", "") or ""),
                        str(getattr(doc, "summary_en", "") or ""),
                        str(chunk.content or ""),
                    ]
                ).lower()
            if query_required_terms and (not any(term in text_blob for term in query_required_terms)):
                continue
            if historical_fact_query and text_blob and _looks_planned_or_proposal_doc(text_blob):
                continue

        seen_chunk_ids.add(chunk.id)
        context_chunks.append(
            {
                "doc_id": doc_id,
                "chunk_id": str(hit.chunk_id),
                "score": float(hit.score),
                "title_en": str(hit.title_en or ""),
                "title_zh": str(hit.title_zh or ""),
                "category_path": str(hit.category_path or ""),
                "text": str(chunk.content or ""),
            }
        )
        if doc_id not in doc_first_seen:
            doc_first_seen[doc_id] = len(doc_first_seen)
        current = float(doc_best_score.get(doc_id, 0.0))
        doc_best_score[doc_id] = max(current, float(hit.score or 0.0))

    if facet.strict_mode and (not context_chunks):
        return {
            "route": "search_bundle",
            "context_chunks": [],
            "sources": [],
            "related_docs": [],
            "hit_count": 0,
            "doc_count": 0,
            "query_en": str(search_res.query_en or ""),
            "bilingual_search": bool(search_res.bilingual),
            "qdrant_used": bool(search_res.qdrant_used),
            "retrieval_mode": str(search_res.retrieval_mode or "none"),
            "vector_hit_count": int(search_res.vector_hit_count or 0),
            "lexical_hit_count": int(search_res.lexical_hit_count or 0),
            "fallback_reason": "strict_filter_zero_hit",
            "facet_mode": "strict_topic",
            "facet_keys": list(facet.facet_keys),
            "related_doc_selection_mode": "evidence_only",
            "evidence_backed_doc_ids": [],
        }

    if (not facet.strict_mode) and domain_whitelist and (not context_chunks):
        return {
            "route": "search_bundle",
            "context_chunks": [],
            "sources": [],
            "related_docs": [],
            "hit_count": 0,
            "doc_count": 0,
            "query_en": str(search_res.query_en or ""),
            "bilingual_search": bool(search_res.bilingual),
            "qdrant_used": bool(search_res.qdrant_used),
            "retrieval_mode": str(search_res.retrieval_mode or "none"),
            "vector_hit_count": int(search_res.vector_hit_count or 0),
            "lexical_hit_count": int(search_res.lexical_hit_count or 0),
            "fallback_reason": "domain_filter_zero_hit",
            "facet_mode": "none",
            "facet_keys": [],
            "related_doc_selection_mode": "evidence_only",
            "evidence_backed_doc_ids": [],
        }

    if (not facet.strict_mode) and query_required_terms and (not context_chunks):
        return {
            "route": "search_bundle",
            "context_chunks": [],
            "sources": [],
            "related_docs": [],
            "hit_count": 0,
            "doc_count": 0,
            "query_en": str(search_res.query_en or ""),
            "bilingual_search": bool(search_res.bilingual),
            "qdrant_used": bool(search_res.qdrant_used),
            "retrieval_mode": str(search_res.retrieval_mode or "none"),
            "vector_hit_count": int(search_res.vector_hit_count or 0),
            "lexical_hit_count": int(search_res.lexical_hit_count or 0),
            "fallback_reason": "query_qualifier_zero_hit",
            "facet_mode": "none",
            "facet_keys": [],
            "query_required_terms": query_required_terms,
            "related_doc_selection_mode": "evidence_only",
            "evidence_backed_doc_ids": [],
        }

    if (not facet.strict_mode) and len(context_chunks) < 3:
        need = max(0, 3 - len(context_chunks))
        context_chunks.extend(_fill_chunks_from_doc_scope(db, doc_ids, seen_chunk_ids, need))
    context_chunks = context_chunks[:10]

    sources: list[ResultCardSource] = []
    source_doc_ids: list[str] = []
    for item in context_chunks[:5]:
        label = item.get("title_zh") if req.ui_lang == "zh" else item.get("title_en")
        if not label:
            label = item.get("title_en") or item.get("title_zh") or "Document"
        sources.append(
            ResultCardSource(
                doc_id=str(item.get("doc_id") or ""),
                chunk_id=str(item.get("chunk_id") or ""),
                label=str(label),
            )
        )
        doc_id = str(item.get("doc_id") or "")
        if doc_id:
            source_doc_ids.append(doc_id)

    ordered_doc_ids = sorted(
        doc_best_score.keys(),
        key=lambda key: (
            -float(doc_best_score.get(key, 0.0)),
            int(doc_first_seen.get(key, 10**6)),
        ),
    )
    if ordered_doc_ids:
        source_doc_ids = ordered_doc_ids
    related_docs = _build_related_docs(db, source_doc_ids, cap=6)
    return {
        "route": "search_bundle",
        "context_chunks": context_chunks,
        "sources": sources,
        "related_docs": related_docs,
        "hit_count": len(context_chunks),
        "doc_count": len({str(item.get('doc_id') or '') for item in context_chunks if str(item.get('doc_id') or '')}),
        "query_en": str(search_res.query_en or ""),
        "bilingual_search": bool(search_res.bilingual),
        "qdrant_used": bool(search_res.qdrant_used),
        "retrieval_mode": str(search_res.retrieval_mode or "none"),
        "vector_hit_count": int(search_res.vector_hit_count or 0),
        "lexical_hit_count": int(search_res.lexical_hit_count or 0),
        "fallback_reason": "",
        "facet_mode": "strict_topic" if facet.strict_mode else "none",
        "facet_keys": list(facet.facet_keys),
        "query_required_terms": query_required_terms,
        "related_doc_selection_mode": "evidence_plus_candidates",
    }


def _execute_plan(db: Session, req: AgentExecuteRequest, planner: PlannerDecision) -> dict[str, Any]:
    scope = planner.doc_scope if isinstance(planner.doc_scope, dict) else {}
    doc_ids = _doc_ids_from_scope(scope, client_context=(req.client_context if isinstance(req.client_context, dict) else {}))
    category_path = _category_from_scope(scope)

    if planner.intent in {"queue_view"}:
        out = _build_queue_bundle(db, req)
        out["fact_route"] = "none"
        out["fact_month"] = ""
        out["route_reason"] = "planner_queue_view"
        return out
    if planner.intent == "reprocess_doc":
        out = _build_reprocess_bundle(db, req, doc_ids=doc_ids)
        out["fact_route"] = "none"
        out["fact_month"] = ""
        out["route_reason"] = "planner_reprocess_doc"
        return out
    if planner.intent == "tag_update":
        out = _build_tag_update_bundle(db, req, doc_ids=doc_ids)
        out["fact_route"] = "none"
        out["fact_month"] = ""
        out["route_reason"] = "planner_tag_update"
        return out
    if planner.intent == "detail_extract":
        out = _build_detail_extract_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
        out["fact_route"] = "none"
        out["fact_month"] = ""
        out["route_reason"] = "planner_detail_extract"
        return out
    if planner.intent == "entity_fact_lookup":
        out = _build_entity_fact_lookup_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
        return out
    if planner.intent == "period_aggregate":
        out = _build_period_aggregate_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
        return out
    # V2 router sets planner.intent="bill_monthly_total" directly; legacy path uses query detection.
    # V2 intent path requires a specific month in the query — prevents "last water bill" (no month)
    # from being routed to the monthly bundle.
    _v2_has_month = planner.intent == "bill_monthly_total" and _extract_month_scope(req.query)[1] is not None
    if _v2_has_month or _is_bill_monthly_total_query(req.query):
        month_bundle = _build_bill_monthly_total_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
        if int(month_bundle.get("hit_count") or 0) > 0:
            route_reason = "v2_bill_monthly_intent" if planner.intent == "bill_monthly_total" else "monthly_total_structured_match"
            month_bundle["route_reason"] = route_reason
            return month_bundle
        month_bundle["fallback_reason"] = "bill_monthly_empty"
        month_bundle["fact_route"] = "bill_monthly_total"
        month_bundle["fact_month"] = str((month_bundle.get("bill_monthly") or {}).get("month") or "")
        month_bundle["route_reason"] = "v2_bill_monthly_intent_empty" if planner.intent == "bill_monthly_total" else "monthly_total_structured_empty"
        return month_bundle
    if planner.intent == "list_recent" and _is_bill_attention_query(req.query):
        try:
            bundle = _build_bill_attention_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
        except Exception as exc:
            logger.warning(
                "bill_facts_unavailable",
                extra=sanitize_log_context({"error_code": "bill_facts_unavailable", "detail": str(exc)}),
            )
            fallback = _build_search_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
            fallback["fallback_reason"] = "bill_facts_unavailable"
            fallback["fact_route"] = "bill_attention"
            fallback["fact_month"] = ""
            return fallback
        if bundle.get("hit_count", 0):
            bundle["route_reason"] = "bill_attention_structured_match"
            return bundle
        fallback = _build_search_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
        fallback["fallback_reason"] = "bill_facts_empty"
        fallback["fact_route"] = "bill_attention"
        fallback["fact_month"] = ""
        fallback["route_reason"] = "bill_attention_structured_empty_fallback"
        return fallback
    out = _build_search_bundle(db, req, planner, doc_ids=doc_ids, category_path=category_path)
    out["fact_route"] = "none"
    out["fact_month"] = ""
    out["route_reason"] = "planner_search_bundle"
    return out


def _build_action(key: str, *, default_label_en: str, default_label_zh: str) -> ResultCardAction:
    meta = _ACTION_META.get(key, {})
    return ResultCardAction(
        key=key,
        label_en=default_label_en,
        label_zh=default_label_zh,
        action_type=str(meta.get("action_type") or "suggestion"),
        payload=meta.get("payload") if isinstance(meta.get("payload"), dict) else {},
        requires_confirm=bool(meta.get("requires_confirm", False)),
        confirm_text_en=str(meta.get("confirm_text_en") or ""),
        confirm_text_zh=str(meta.get("confirm_text_zh") or ""),
    )


def _default_actions(planner: PlannerDecision) -> list[ResultCardAction]:
    chosen: list[str] = []
    for action in list(planner.actions or []):
        key = str(action or "").strip()
        if key and key not in chosen:
            chosen.append(key)
    if planner.confidence < 0.55 and "fallback_search" not in chosen:
        chosen.insert(0, "fallback_search")
    if not chosen:
        chosen = ["open_docs", "search_documents"]

    out: list[ResultCardAction] = []
    for key in chosen[:4]:
        label_en, label_zh = _ACTION_LABELS.get(key, (key.replace("_", " ").title(), key))
        out.append(_build_action(key, default_label_en=label_en, default_label_zh=label_zh))
    return out


def _synth_prompt(
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    bundle: dict[str, Any],
    *,
    conversation: list[dict[str, str]],
) -> list[dict[str, str]]:
    chunks = bundle.get("context_chunks") or []
    # detail_extract/entity_fact_lookup already have pre-computed detail_sections;
    # extra raw chunks inflate the prompt and cause synthesis timeouts.
    _route = str(bundle.get("route") or "")
    _chunk_limit = 4 if _route in {"detail_extract", "entity_fact_lookup"} else 10
    context_payload = [
        {
            "doc_id": str(item.get("doc_id") or ""),
            "chunk_id": str(item.get("chunk_id") or ""),
            "title_en": str(item.get("title_en") or ""),
            "title_zh": str(item.get("title_zh") or ""),
            "category_path": str(item.get("category_path") or ""),
            "score": float(item.get("score") or 0.0),
            "text": _safe_text(item.get("text"), cap=420),
        }
        for item in chunks[:_chunk_limit]
    ]
    target_lang = "zh" if req.ui_lang == "zh" else "en"
    if target_lang == "zh":
        route_rules = (
            "ROUTE RULES:\n"
            "- search_semantic: write a narrative answer in Chinese from the chunks; skip detail_sections.\n"
            "- detail_extract: keep short_summary as one sentence; preserve the structured detail_sections from executor data.\n"
            "- bill_monthly_total: organize as sections 月度总额 / 待缴账单 / 已缴账单; include amounts and due dates.\n"
            "- bill_attention: organize as sections 需要缴费的账单 / 已缴费的账单 / 建议优先级.\n"
        )
        lang_rule = "Respond in natural Chinese. Always populate BOTH short_summary.en AND short_summary.zh.\n"
        insufficient_rule = (
            "INSUFFICIENT EVIDENCE: if answerability is 'insufficient' or 'none', "
            "write short_summary.zh stating clearly what information is missing; populate missing_fields accordingly.\n"
        )
    else:
        route_rules = (
            "ROUTE RULES:\n"
            "- search_semantic: write a narrative answer in English from the chunks; skip detail_sections.\n"
            "- detail_extract: keep short_summary as one sentence; preserve the structured detail_sections from executor data.\n"
            "- bill_monthly_total: organize as sections Monthly Total / Pending Bills / Paid Bills; include amounts and due dates.\n"
            "- bill_attention: organize as sections Pending Bills / Paid Bills / Priority Recommendation.\n"
        )
        lang_rule = "Respond in natural English. Always populate BOTH short_summary.en AND short_summary.zh.\n"
        insufficient_rule = (
            "INSUFFICIENT EVIDENCE: if answerability is 'insufficient' or 'none', "
            "state directly what is missing in short_summary.en; populate missing_fields accordingly. "
            "Avoid vague phrases like 'however this may be relevant'.\n"
        )
    return [
        {
            "role": "system",
            "content": (
                "You are a Synthesizer model for a private family knowledge vault. Return ONLY valid JSON.\n\n"
                "EVIDENCE POLICY:\n"
                "- Use ONLY the data provided. Never invent amounts, dates, names, or policy numbers.\n"
                + insufficient_rule +
                "SOURCE DISCIPLINE:\n"
                "- Multiple chunks may come from different documents. Each chunk has title_en, title_zh and category_path.\n"
                "- First identify which document title/category best matches the query subject.\n"
                "- Extract each fact (date, amount, name, ID) ONLY from the matching document. "
                "Ignore values that appear in unrelated documents even if they look plausible.\n"
                "- If a date or amount is only found in a document that clearly does NOT match the query subject "
                "(e.g. a pet insurance date when asked about car insurance; a payment date when asked about an AGM), "
                "do NOT report it — list the field in missing_fields instead.\n"
                "- Higher score = more relevant; treat the top-scored chunk's document as the primary source.\n"
                "\nOUTPUT RULES:\n"
                + lang_rule +
                "- key_points: 2-4 bilingual bullet points with concrete facts.\n"
                "- NEVER copy raw boilerplate text (BPAY codes, usage details, plan features) into short_summary.\n"
                "- Do not mention 'chunks', 'pipeline', 'model', or internal system words.\n\n"
                + route_rules +
                "\nOutput schema (JSON only, no markdown):\n"
                '{"title":"...","short_summary":{"en":"...","zh":"..."},"key_points":[{"en":"...","zh":"..."}],'
                '"detail_sections":[{"section_name":"...","rows":[{"field":"...","label_en":"...","label_zh":"...","value_en":"...","value_zh":"...","evidence_refs":[{"doc_id":"...","chunk_id":"...","evidence_text":"..."}]}]}],'
                '"missing_fields":["..."],"coverage_stats":{"docs_scanned":0,"docs_matched":0,"fields_filled":0},"actions":[]}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "query": req.query,
                    "target_ui_lang": target_lang,
                    "planner": {
                        "intent": planner.intent,
                        "confidence": planner.confidence,
                        "ui_lang": planner.ui_lang,
                    },
                    "route": str(bundle.get("route") or "search_bundle"),
                    "stats": {
                        "hit_count": int(bundle.get("hit_count") or 0),
                        "doc_count": int(bundle.get("doc_count") or 0),
                        "bilingual_search": bool(bundle.get("bilingual_search")),
                    },
                    "bill_attention": _json_safe_value(bundle.get("bill_attention") or {}),
                    "bill_monthly": _json_safe_value(bundle.get("bill_monthly") or {}),
                    "detail_topic": str(bundle.get("detail_topic") or ""),
                    "detail_sections": _json_safe_value(_cap_detail_sections(bundle.get("detail_sections") or [])),
                    "missing_fields": _json_safe_value(bundle.get("missing_fields") or []),
                    "coverage_stats": _json_safe_value(bundle.get("coverage_stats") or {}),
                    "answerability": str(bundle.get("answerability") or "sufficient"),
                    "required_evidence_fields": _json_safe_value(bundle.get("required_evidence_fields") or []),
                    "coverage_ratio": float(bundle.get("coverage_ratio") or 1.0),
                    "field_coverage_ratio": float(bundle.get("field_coverage_ratio") or 1.0),
                    "conversation": conversation,
                    "chunks": context_payload,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _synthesize_with_model(
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    bundle: dict[str, Any],
    *,
    trace_id: str,
    conversation: list[dict[str, str]],
) -> tuple[ResultCard | None, str]:
    url = settings.ollama_base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": settings.synthesizer_model,
        "stream": False,
        "messages": _synth_prompt(req, planner, bundle, conversation=conversation),
        "options": {"temperature": 0.1},
        "format": "json",
    }

    def _log_synth_error(error_code: str, **extra_fields: Any) -> None:
        payload = {
            "trace_id": trace_id,
            "route": str(bundle.get("route") or ""),
            "error_code": str(error_code or "synth_parse_error"),
        }
        for key, value in extra_fields.items():
            payload[str(key)] = value
        logger.warning("agent_synth_failed", extra=sanitize_log_context(payload))

    try:
        resp = requests.post(url, json=payload, timeout=int(settings.agent_synth_timeout_sec))
        resp.raise_for_status()
        body = resp.json() if hasattr(resp, "json") else {}
        text = str((body.get("message") or {}).get("content") or "")
        parsed = _extract_json_object(text)
        if not parsed:
            _log_synth_error("synth_parse_error")
            return (None, "synth_parse_error")

        short = parsed.get("short_summary")
        if not isinstance(short, dict):
            _log_synth_error("synth_parse_error")
            return (None, "synth_parse_error")
        short_en = str(short.get("en") or "").strip()
        short_zh = str(short.get("zh") or "").strip()
        if (not short_en) and (not short_zh):
            _log_synth_error("synth_parse_error")
            return (None, "synth_parse_error")

        key_points: list[BilingualText] = []
        for item in parsed.get("key_points") or []:
            if not isinstance(item, dict):
                continue
            en = str(item.get("en") or "").strip()
            zh = str(item.get("zh") or "").strip()
            if (not en) and (not zh):
                continue
            key_points.append(BilingualText(en=en, zh=zh))
        if not key_points:
            key_points = [
                BilingualText(
                    en="Primary evidence was extracted from the most relevant document snippets.",
                    zh="关键证据已从最相关文档片段中提取。",
                )
            ]

        actions: list[ResultCardAction] = []
        for item in parsed.get("actions") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            label_en = str(item.get("label_en") or "").strip()
            label_zh = str(item.get("label_zh") or "").strip()
            default_en, default_zh = _ACTION_LABELS.get(key, (key.replace("_", " ").title(), key))
            actions.append(
                ResultCardAction(
                    key=key,
                    label_en=label_en or default_en,
                    label_zh=label_zh or default_zh,
                    action_type=str(item.get("action_type") or _ACTION_META.get(key, {}).get("action_type") or "suggestion"),
                    payload=item.get("payload") if isinstance(item.get("payload"), dict) else _ACTION_META.get(key, {}).get("payload", {}),
                    requires_confirm=bool(item.get("requires_confirm", False)),
                    confirm_text_en=str(item.get("confirm_text_en") or ""),
                    confirm_text_zh=str(item.get("confirm_text_zh") or ""),
                )
            )
        if not actions:
            actions = _default_actions(planner)

        parsed_sections = parsed.get("detail_sections")
        detail_sections: list[DetailSection] = []
        if isinstance(parsed_sections, list):
            for section in parsed_sections[:4]:
                if not isinstance(section, dict):
                    continue
                section_name = str(section.get("section_name") or "").strip() or "details"
                rows: list[DetailRow] = []
                for row in (section.get("rows") or [])[:30]:
                    if not isinstance(row, dict):
                        continue
                    evidences: list[DetailEvidenceRef] = []
                    for ev in (row.get("evidence_refs") or [])[:2]:
                        if not isinstance(ev, dict):
                            continue
                        evidences.append(
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
                            evidence_refs=evidences,
                        )
                    )
                if rows:
                    detail_sections.append(DetailSection(section_name=section_name, rows=rows))
        if not detail_sections:
            detail_sections = list(bundle.get("detail_sections") or [])
        missing_fields = [str(item or "") for item in (parsed.get("missing_fields") or []) if str(item or "").strip()]
        if not missing_fields:
            missing_fields = [str(item or "") for item in (bundle.get("missing_fields") or []) if str(item or "").strip()]
        coverage_raw = parsed.get("coverage_stats") if isinstance(parsed.get("coverage_stats"), dict) else {}
        bundle_cov = bundle.get("coverage_stats")
        coverage_stats = DetailCoverageStats(
            docs_scanned=int(coverage_raw.get("docs_scanned") or getattr(bundle_cov, "docs_scanned", 0) or 0),
            docs_matched=int(coverage_raw.get("docs_matched") or getattr(bundle_cov, "docs_matched", 0) or 0),
            fields_filled=int(coverage_raw.get("fields_filled") or getattr(bundle_cov, "fields_filled", 0) or 0),
        )

        return (
            ResultCard(
                title=str(parsed.get("title") or "Knowledge Task Result")[:80],
                short_summary=BilingualText(en=short_en, zh=short_zh),
                key_points=key_points[:6],
                sources=bundle.get("sources") or [],
                actions=actions[:4],
                detail_sections=detail_sections,
                missing_fields=missing_fields,
                coverage_stats=coverage_stats,
            ),
            "",
        )
    except requests.exceptions.Timeout:
        _log_synth_error("synth_timeout")
        return (None, "synth_timeout")
    except requests.exceptions.HTTPError as exc:
        status_code = ""
        if getattr(exc, "response", None) is not None:
            status_code = str(getattr(exc.response, "status_code", "") or "")
        _log_synth_error("synth_http_error", status_code=status_code)
        return (None, "synth_http_error")
    except Exception:
        _log_synth_error("synth_parse_error")
        return (None, "synth_parse_error")


def _synthesize_fallback(req: AgentExecuteRequest, planner: PlannerDecision, bundle: dict[str, Any]) -> ResultCard:
    route = str(bundle.get("route") or "")
    context_chunks = bundle.get("context_chunks") or []
    doc_count = int(bundle.get("doc_count") or 0)
    if route == "bill_monthly_total":
        monthly = bundle.get("bill_monthly") or {}
        pending = monthly.get("pending") or []
        paid = monthly.get("paid") or []
        month = str(monthly.get("month") or "")
        total_amount = monthly.get("total_amount")
        currency = str(monthly.get("currency") or "AUD")
        total_text = _format_amount(total_amount, currency, ui_lang=req.ui_lang) if total_amount is not None else "金额未提取"

        if (not pending) and (not paid):
            if req.ui_lang == "zh":
                short_zh = f"未找到 {month or '该月'} 的账单记录。请尝试指定年份（例如：2026年2月账单情况）或先完成账单事实回填。"
                short_en = "No bill records found for the selected month. Try specifying a year (for example: Feb 2026 bills)."
            else:
                short_en = (
                    f"No bill records found for {month or 'the selected month'}. "
                    "Try specifying a year, e.g. Feb 2026 bills."
                )
                short_zh = f"未找到 {month or '该月'} 的账单记录，请指定年份后重试。"
            return ResultCard(
                title="Monthly Bill Summary",
                short_summary=BilingualText(en=short_en, zh=short_zh),
                key_points=[
                    BilingualText(
                        en="No structured bill facts matched this month.",
                        zh="该月份未命中可用的结构化账单事实。",
                    ),
                    BilingualText(
                        en="Try a query with explicit year, e.g. Feb 2026 bills.",
                        zh="建议指定年份提问，例如：2026年2月账单情况。",
                    ),
                ],
                sources=bundle.get("sources") or [],
                actions=_default_actions(planner),
            )

        lines_pending = []
        for item in pending[:6]:
            title = str(item.get("title_zh") or item.get("title_en") or "账单")
            amount = _format_amount(item.get("amount_due"), str(item.get("currency") or "AUD"), ui_lang=req.ui_lang)
            due_date = _format_due_date(item.get("due_date"), ui_lang=req.ui_lang)
            lines_pending.append(f"- {title}：{amount or '金额未提取'}；截止{due_date or '未提取'}")

        lines_paid = []
        for item in paid[:6]:
            title = str(item.get("title_zh") or item.get("title_en") or "账单")
            amount = _format_amount(item.get("amount_due"), str(item.get("currency") or "AUD"), ui_lang=req.ui_lang)
            lines_paid.append(f"- {title}：{amount or '金额未提取'}")

        if req.ui_lang == "zh":
            short_zh = "\n".join(
                [
                    f"{month or '该月'}账单汇总：合计 {total_text}。",
                    "",
                    "待缴账单：",
                    *(lines_pending or ["- 暂无明确待缴账单"]),
                    "",
                    "已缴账单：",
                    *(lines_paid or ["- 暂无明确已缴账单"]),
                    "",
                    "统计口径：该月全部账单（已缴+待缴）。",
                ]
            )
            short_en = "Monthly bill total was calculated from all bills in the month (paid + unpaid)."
        else:
            short_en = (
                f"Monthly bill summary for {month or 'selected month'}: total {total_text}. "
                f"Pending {len(pending)} items, paid {len(paid)} items."
            )
            short_zh = f"{month or '该月'}账单合计 {total_text}，待缴 {len(pending)} 项，已缴 {len(paid)} 项。"

        return ResultCard(
            title="Monthly Bill Summary",
            short_summary=BilingualText(en=short_en, zh=short_zh),
            key_points=[
                BilingualText(en=f"Total amount: {total_text}", zh=f"总金额：{total_text}"),
                BilingualText(en=f"Pending bills: {len(pending)}", zh=f"待缴账单：{len(pending)}"),
                BilingualText(en=f"Paid bills: {len(paid)}", zh=f"已缴账单：{len(paid)}"),
            ],
            sources=bundle.get("sources") or [],
            actions=_default_actions(planner),
        )

    if route == "bill_attention":
        bill_attention = bundle.get("bill_attention") or {}
        pending = bill_attention.get("pending") or []
        paid = bill_attention.get("paid") or []
        lines_pending = []
        for item in pending[:4]:
            title = str(item.get("title_zh") or item.get("title_en") or "账单")
            amount = _format_amount(item.get("amount_due"), str(item.get("currency") or "AUD"), ui_lang=req.ui_lang)
            due_date = _format_due_date(item.get("due_date"), ui_lang=req.ui_lang)
            status = _bill_status_label(str(item.get("payment_status") or ""), ui_lang=req.ui_lang)
            lines_pending.append(f"- {title}：{amount or '金额未提取'}；截止{due_date or '未提取'}；{status}")
        lines_paid = []
        for item in paid[:4]:
            title = str(item.get("title_zh") or item.get("title_en") or "账单")
            amount = _format_amount(item.get("amount_due"), str(item.get("currency") or "AUD"), ui_lang=req.ui_lang)
            lines_paid.append(f"- {title}：{amount or '金额未提取'}")
        short_zh = "\n".join(
            [
                "根据资料库记录，最近有以下账单需要关注：",
                "",
                "需要缴费的账单：",
                *(lines_pending or ["- 暂无明确待缴账单"]),
                "",
                "已缴费的账单：",
                *(lines_paid or ["- 暂无明确已缴账单"]),
                "",
                "建议优先处理近期到期或已逾期账单。",
            ]
        )
        short_en = "Recent bills were grouped into unpaid and paid sections with priority recommendations."
        key_points = [
            BilingualText(en=f"Pending bills: {len(pending)}", zh=f"待缴账单：{len(pending)}"),
            BilingualText(en=f"Paid bills: {len(paid)}", zh=f"已缴账单：{len(paid)}"),
            BilingualText(en=f"Documents involved: {doc_count}", zh=f"涉及文档数：{doc_count}"),
        ]
        return ResultCard(
            title="Bill Attention Summary",
            short_summary=BilingualText(en=short_en, zh=short_zh),
            key_points=key_points,
            sources=bundle.get("sources") or [],
            actions=_default_actions(planner),
        )

    if route == "detail_extract":
        detail_sections = list(bundle.get("detail_sections") or [])
        missing_fields = [str(item or "") for item in (bundle.get("missing_fields") or []) if str(item or "").strip()]
        coverage_stats = bundle.get("coverage_stats") or DetailCoverageStats()
        topic = str(bundle.get("detail_topic") or "generic")
        total_rows = 0
        for section in detail_sections:
            total_rows += len(getattr(section, "rows", []) or [])
        answerability = str(bundle.get("answerability") or "sufficient")
        if answerability in {"none", "insufficient"}:
            if req.ui_lang == "zh":
                short_zh = f"{topic}细节证据不足，当前仅能提供部分结构化字段。"
                short_en = f"Insufficient evidence for {topic}; only partial structured fields are available."
            else:
                short_en = (
                    f"I couldn't find enough evidence to answer the requested {topic} details directly. "
                    "Only partial structured fields are available."
                )
                short_zh = f"{topic}细节证据不足，当前仅能提供部分结构化字段。"
        else:
            if req.ui_lang == "zh":
                short_zh = f"已完成{topic}细节提取，字段条目 {total_rows} 项。"
                short_en = f"Structured {topic} detail extraction completed with {total_rows} rows."
            else:
                short_en = f"Structured {topic} detail extraction completed with {total_rows} rows."
                short_zh = f"已完成{topic}细节提取，字段条目 {total_rows} 项。"
        key_points = [
            BilingualText(en=f"Docs scanned: {int(getattr(coverage_stats, 'docs_scanned', 0) or 0)}", zh=f"扫描文档：{int(getattr(coverage_stats, 'docs_scanned', 0) or 0)}"),
            BilingualText(en=f"Docs matched: {int(getattr(coverage_stats, 'docs_matched', 0) or 0)}", zh=f"命中文档：{int(getattr(coverage_stats, 'docs_matched', 0) or 0)}"),
            BilingualText(en=f"Fields filled: {int(getattr(coverage_stats, 'fields_filled', 0) or 0)}", zh=f"字段填充：{int(getattr(coverage_stats, 'fields_filled', 0) or 0)}"),
        ]
        if missing_fields:
            key_points.append(
                BilingualText(
                    en=f"Missing fields: {', '.join(missing_fields[:6])}",
                    zh=f"缺失字段：{', '.join(missing_fields[:6])}",
                )
            )
        return ResultCard(
            title="Detail Extraction Result",
            short_summary=BilingualText(en=short_en, zh=short_zh),
            key_points=key_points,
            sources=bundle.get("sources") or [],
            actions=_default_actions(planner),
            detail_sections=detail_sections,
            missing_fields=missing_fields,
            coverage_stats=coverage_stats if isinstance(coverage_stats, DetailCoverageStats) else DetailCoverageStats(),
        )

    detail_sections_any = list(bundle.get("detail_sections") or [])
    slot_results_any = list(bundle.get("slot_results") or [])
    answerability = str(bundle.get("answerability") or "sufficient")
    # entity_fact_lookup with no extracted rows → insufficient evidence (must check before outer if)
    if route == "entity_fact_lookup" and not detail_sections_any:
        _zh_mode = req.ui_lang == "zh"
        _topic = str(bundle.get("detail_topic") or "")
        _EFL_ZH = {"insurance": "保险证据详情", "pets": "宠物记录详情", "home": "房产信息详情",
                   "appliances": "家电设备详情", "warranty": "保修信息详情", "bill": "账单证据详情",
                   "contract": "合同详情", "generic": "文档证据详情"}
        _EFL_EN = {"insurance": "Insurance Evidence", "pets": "Pet Records", "home": "Property Details",
                   "appliances": "Appliance Details", "warranty": "Warranty Details", "bill": "Bill Evidence",
                   "contract": "Contract Details", "generic": "Document Evidence"}
        _fb_title = (_EFL_ZH if _zh_mode else _EFL_EN).get(_topic, "Knowledge Search Result")
        return ResultCard(
            title=_fb_title,
            short_summary=BilingualText(
                en="Not enough evidence was found in the knowledge base to answer this question.",
                zh="资料中没有足够信息回答此问题，请尝试更具体的问题或补充相关文档。",
            ),
            key_points=[BilingualText(
                en="Please try a more specific question or add relevant documents.",
                zh="请尝试更具体的问题或补充相关文档。",
            )],
            sources=bundle.get("sources") or [],
            actions=_default_actions(planner),
            detail_sections=[],
            missing_fields=[],
            coverage_stats=DetailCoverageStats(),
            insufficient_evidence=True,
        )
    if answerability != "none" and (detail_sections_any or slot_results_any):
        missing_fields = [str(x or "") for x in (bundle.get("missing_fields") or bundle.get("coverage_missing_fields") or []) if str(x or "").strip()]
        coverage_stats = bundle.get("coverage_stats")
        if not isinstance(coverage_stats, DetailCoverageStats):
            coverage_stats = DetailCoverageStats(
                docs_scanned=int(getattr(coverage_stats, "docs_scanned", 0) or 0) if coverage_stats else 0,
                docs_matched=int(getattr(coverage_stats, "docs_matched", 0) or 0) if coverage_stats else 0,
                fields_filled=int(getattr(coverage_stats, "fields_filled", 0) or 0) if coverage_stats else 0,
            )
        points: list[BilingualText] = []
        added = 0
        for section in detail_sections_any[:3]:
            rows = list(getattr(section, "rows", []) or (section.get("rows") if isinstance(section, dict) else []) or [])
            for row in rows[:3]:
                label_zh = str(getattr(row, "label_zh", "") or (row.get("label_zh") if isinstance(row, dict) else "") or getattr(row, "field", "") or (row.get("field") if isinstance(row, dict) else "") or "字段")
                label_en = str(getattr(row, "label_en", "") or (row.get("label_en") if isinstance(row, dict) else "") or label_zh)
                value_zh = str(getattr(row, "value_zh", "") or (row.get("value_zh") if isinstance(row, dict) else "") or getattr(row, "value_en", "") or (row.get("value_en") if isinstance(row, dict) else "") or "")
                value_en = str(getattr(row, "value_en", "") or (row.get("value_en") if isinstance(row, dict) else "") or value_zh)
                if not value_zh.strip():
                    continue
                points.append(BilingualText(en=f"{label_en}: {value_en}", zh=f"{label_zh}：{value_zh}"))
                added += 1
                if added >= 4:
                    break
            if added >= 4:
                break
        if added == 0:
            for row in slot_results_any[:4]:
                if str(row.get("status") or "") not in {"found", "derived"} or not str(row.get("value") or "").strip():
                    continue
                label_zh = str(row.get("label_zh") or row.get("slot") or "字段")
                label_en = str(row.get("label_en") or row.get("slot") or label_zh)
                value = str(row.get("value") or "")
                points.append(BilingualText(en=f"{label_en}: {value}", zh=f"{label_zh}：{value}"))
                added += 1
        if missing_fields:
            points.append(BilingualText(en=f"Missing evidence: {', '.join(missing_fields[:4])}", zh=f"尚缺证据：{', '.join(missing_fields[:4])}"))
        if answerability in {"partial", "insufficient"}:
            short_en = "I can confirm part of the answer from the available documents, but the evidence is incomplete."
            short_zh = "根据现有资料，已确认部分信息；但证据仍不完整。"
        else:
            short_en = "I found structured evidence for this question."
            short_zh = "已根据资料提取到结构化证据。"
        _zh_mode = req.ui_lang == "zh"
        if route == "entity_fact_lookup":
            _topic = str(bundle.get("detail_topic") or "")
            _EFL_ZH = {"insurance": "保险证据详情", "pets": "宠物记录详情", "home": "房产信息详情",
                       "appliances": "家电设备详情", "warranty": "保修信息详情", "bill": "账单证据详情",
                       "contract": "合同详情", "generic": "文档证据详情"}
            _EFL_EN = {"insurance": "Insurance Evidence", "pets": "Pet Records", "home": "Property Details",
                       "appliances": "Appliance Details", "warranty": "Warranty Details", "bill": "Bill Evidence",
                       "contract": "Contract Details", "generic": "Document Evidence"}
            _fb_title = (_EFL_ZH if _zh_mode else _EFL_EN).get(_topic, "Knowledge Search Result")
            # Rebuild points from ALL rows (not capped at first 3) so every filled field is reachable
            _efl_filled_zh: list[str] = []
            _efl_filled_en: list[str] = []
            for _sec in detail_sections_any[:3]:
                _sec_rows = list(getattr(_sec, "rows", None) or (_sec.get("rows") if isinstance(_sec, dict) else None) or [])
                for _row in _sec_rows:  # all rows, no cap
                    _vz = str(getattr(_row, "value_zh", None) or (_row.get("value_zh") if isinstance(_row, dict) else None) or getattr(_row, "value_en", None) or (_row.get("value_en") if isinstance(_row, dict) else None) or "")
                    _ve = str(getattr(_row, "value_en", None) or (_row.get("value_en") if isinstance(_row, dict) else None) or _vz)
                    _lz = str(getattr(_row, "label_zh", None) or (_row.get("label_zh") if isinstance(_row, dict) else None) or "字段")
                    _le = str(getattr(_row, "label_en", None) or (_row.get("label_en") if isinstance(_row, dict) else None) or _lz)
                    if _vz.strip():
                        _efl_filled_zh.append(f"{_lz}：{_vz}")
                        _efl_filled_en.append(f"{_le}: {_ve}")
            if _efl_filled_zh:
                short_zh = "；".join(_efl_filled_zh[:4]) + "。"
                short_en = "; ".join(_efl_filled_en[:4]) + "."
                points = [BilingualText(zh=z, en=e) for z, e in zip(_efl_filled_zh, _efl_filled_en)]
            else:
                # No evidence found — early return as refusal so answer_mode can be set correctly
                return ResultCard(
                    title=_fb_title,
                    short_summary=BilingualText(
                        en="Not enough evidence was found in the knowledge base to answer this question.",
                        zh="资料中没有足够信息回答此问题，请尝试更具体的问题或补充相关文档。",
                    ),
                    key_points=[BilingualText(
                        en="Please try a more specific question or add relevant documents.",
                        zh="请尝试更具体的问题或补充相关文档。",
                    )],
                    sources=bundle.get("sources") or [],
                    actions=_default_actions(planner),
                    detail_sections=[],
                    missing_fields=[],
                    coverage_stats=coverage_stats,
                    insufficient_evidence=True,
                )
        elif route == "period_aggregate":
            _pa = bundle.get("period_aggregate") or {}
            _months = int(_pa.get("months") or 3)
            _fb_title = f"过去{_months}个月账单统计" if _zh_mode else f"Bill Summary: Past {_months} Months"
        else:
            _fb_title = "Knowledge Search Result"
        return ResultCard(
            title=_fb_title,
            short_summary=BilingualText(en=short_en, zh=short_zh),
            key_points=points[:6] or [BilingualText(en="Relevant evidence was found.", zh="已找到相关证据。")],
            sources=bundle.get("sources") or [],
            actions=_default_actions(planner),
            detail_sections=detail_sections_any if isinstance(detail_sections_any, list) else [],
            missing_fields=missing_fields,
            coverage_stats=coverage_stats,
            insufficient_evidence=False,
        )

    if not context_chunks:
        return ResultCard(
            title="Knowledge Search Result",
            short_summary=BilingualText(
                en="No matching documents were found. Try using a more specific keyword such as vendor, month, or bill type.",
                zh="未找到符合条件的文档，请尝试更具体的关键词，例如供应商、月份或账单类型。",
            ),
            key_points=[
                BilingualText(
                    en="No relevant document matched the current query constraints.",
                    zh="当前查询条件下未命中相关文档。",
                ),
                BilingualText(
                    en="Try adding an exact topic like internet bill, electricity bill, or property manager contact.",
                    zh="可补充精确主题词，如网络账单、电费账单、物业联系人。",
                ),
            ],
            sources=bundle.get("sources") or [],
            actions=_default_actions(planner),
        )

    preview_zh_lines = [_clean_search_fallback_snippet(item.get("text"), cap=72) for item in context_chunks[:2]]
    preview_en_lines = [_clean_search_fallback_snippet(item.get("text"), cap=96) for item in context_chunks[:2]]
    preview_zh = "；".join(line for line in preview_zh_lines if line) or "已命中相关文档片段。"
    preview_en = "; ".join(line for line in preview_en_lines if line) or "Relevant document snippets were found."

    key_points = []
    for item in context_chunks[:3]:
        title_zh = str(item.get("title_zh") or item.get("title_en") or "相关文档")
        title_en = str(item.get("title_en") or item.get("title_zh") or "Related document")
        key_points.append(
            BilingualText(
                en=f"{title_en}: {_clean_search_fallback_snippet(item.get('text'), cap=80)}",
                zh=f"{title_zh}：{_clean_search_fallback_snippet(item.get('text'), cap=56)}",
            )
        )

    answerability = str(bundle.get("answerability") or "sufficient")
    coverage_missing_fields = [str(x or "") for x in (bundle.get("coverage_missing_fields") or []) if str(x or "").strip()]
    if req.ui_lang == "en" and answerability != "sufficient":
        short_en = "I found potentially related documents, but I do not have enough evidence to answer this question directly."
        if coverage_missing_fields:
            short_en += f" Missing evidence: {', '.join(coverage_missing_fields[:4])}."
    else:
        short_en = f"I found relevant evidence in your library. Summary: {preview_en}"

    return ResultCard(
        title="Knowledge Search Result",
        short_summary=BilingualText(
            en=short_en,
            zh=f"我在资料库中找到相关证据。摘要如下：{preview_zh}",
        ),
        key_points=key_points[:6],
        sources=bundle.get("sources") or [],
        actions=_default_actions(planner),
    )


# ─────────────────────────────────────────────────────────────────────────────
# V2 Router helpers
# ─────────────────────────────────────────────────────────────────────────────

# Maps RouterDecision.sub_intent → PlannerDecision.task_kind
_SUB_INTENT_TO_TASK_KIND: dict[str, str] = {
    "detail_extract": "detail_extract",
    "entity_fact_lookup": "fact_lookup",
    "search_semantic": "search",
    "bill_attention": "aggregate_lookup",
    "period_aggregate": "aggregate_lookup",
    "bill_monthly_total": "aggregate_lookup",
    "queue_view": "queue",
    "reprocess_doc": "mutate",
    "tag_update": "mutate",
    "chitchat": "search",
}

# Maps RouterDecision.sub_intent → PlannerDecision.intent (for _execute_plan compat)
_SUB_INTENT_TO_PLANNER_INTENT: dict[str, str] = {
    # bill_attention route in _execute_plan checks: planner.intent == "list_recent"
    "bill_attention": "list_recent",
    # bill_monthly_total: pass intent directly so _execute_plan can route without query regex
    "bill_monthly_total": "bill_monthly_total",
    "chitchat": "search_semantic",
}


def _router_to_planner(router: RouterDecision, req: AgentExecuteRequest) -> PlannerDecision:
    """Convert RouterDecision → PlannerDecision for bundle builder compatibility."""
    intent = _SUB_INTENT_TO_PLANNER_INTENT.get(router.sub_intent, router.sub_intent)
    return PlannerDecision(
        intent=intent,
        confidence=0.90,
        doc_scope=req.doc_scope if isinstance(req.doc_scope, dict) else {},
        actions=[],
        fallback="search_semantic",
        ui_lang=router.ui_lang,
        query_lang=router.query_lang,
        route_reason=f"v2_{router.route_reason}",
        subject_domain=router.domain,
        task_kind=_SUB_INTENT_TO_TASK_KIND.get(router.sub_intent, "search"),
        target_slots=[],
        refusal_candidate=False,
    )


def _chitchat_title(query: str, ui_lang: str) -> str:
    q = (query or "").lower().strip()
    zh = ui_lang == "zh"
    if any(t in q for t in ("谢谢", "感谢", "多谢", "thanks", "thank you", "thank")):
        return "不客气" if zh else "You're Welcome"
    if any(t in q for t in ("再见", "拜拜", "bye", "goodbye", "farewell")):
        return "再见" if zh else "Goodbye"
    return "你好" if zh else "Hello"


def _build_chitchat_card(req: AgentExecuteRequest) -> ResultCard:
    """Template response for chitchat/off-topic queries — no retrieval, no LLM synthesis."""
    zh = "您好！我专注于家庭知识库问题，例如账单、保险、家居设备、宠物或家庭文件。请问有什么可以帮您？"
    en = "Hello! I'm focused on family vault topics such as bills, insurance, home appliances, pets, or family documents. How can I help?"
    return ResultCard(
        title=_chitchat_title(req.query, req.ui_lang),
        short_summary=BilingualText(zh=zh, en=en),
        key_points=[],
        sources=[],
        actions=[],
        detail_sections=[],
        missing_fields=[],
        coverage_stats=DetailCoverageStats(docs_scanned=0, docs_matched=0, fields_filled=0),
        evidence_summary=[],
        insufficient_evidence=False,
    )


def execute_agent_v2(db: Session, req: AgentExecuteRequest) -> AgentExecuteResponse:
    """Simplified V2 pipeline:
    1. Single LLM call — classify route + rewrite query
    2. Chitchat short-circuit (no retrieval, no synthesis LLM)
    3. For lookup: use rewritten_query for vector search
    4. Bundle builders (unchanged) + coverage analysis + synthesizer (unchanged)
    """
    total_started = time.perf_counter()
    planner_latency_ms = 0
    executor_latency_ms = 0
    synth_latency_ms = 0
    router: RouterDecision | None = None

    # ── 1. Route + rewrite ────────────────────────────────────────────
    planner_started = time.perf_counter()
    if req.planner is not None:
        # Pre-computed planner (tests / admin override)
        planner = req.planner
    else:
        router = route_and_rewrite(
            PlannerRequest(
                query=req.query,
                ui_lang=req.ui_lang,
                query_lang=req.query_lang,
                doc_scope=req.doc_scope,
            )
        )
        planner = _router_to_planner(router, req)
    planner_latency_ms = int((time.perf_counter() - planner_started) * 1000)

    trace_id = f"agt-{uuid.uuid4().hex[:12]}"

    # ── 2. Chitchat short-circuit ─────────────────────────────────────
    if router is not None and router.route == "chitchat":
        card = _build_chitchat_card(req)
        logger.info(
            "agent_v2_chitchat",
            extra=sanitize_log_context({"trace_id": trace_id, "route": "chitchat",
                                        "route_reason": router.route_reason}),
        )
        return AgentExecuteResponse(
            planner=planner,
            card=card,
            related_docs=[],
            trace_id=trace_id,
            executor_stats=AgentExecutorStats(
                route="chitchat",
                retrieval_mode="none",
                answer_mode="chitchat",
                route_reason=f"v2_{router.route_reason}",
                planner_latency_ms=planner_latency_ms,
            ),
        )

    # ── 3. Lookup: swap in rewritten query for retrieval ─────────────
    retrieval_req = req
    if router is not None and router.route == "lookup" and (router.rewritten_query or "").strip():
        retrieval_req = req.model_copy(update={"query": router.rewritten_query})

    # ── 4–7. Bundle, coverage, synthesis, response (same as legacy) ──
    context_policy = _context_policy_for_query(
        req.query,
        client_context=(req.client_context if isinstance(req.client_context, dict) else {}),
    )
    synth_conversation = _normalize_conversation_messages(req, context_policy=context_policy)
    executor_started = time.perf_counter()
    bundle = _execute_plan(db, retrieval_req, planner)
    executor_latency_ms = int((time.perf_counter() - executor_started) * 1000)
    # Structured-only routes (bill_monthly_total, queue, tag, reprocess, entity_fact_lookup) never reach
    # synthesis, so the full coverage-analysis pass is unnecessary.
    _STRUCTURED_ONLY_ROUTES = {"bill_monthly_total", "queue_view", "tag_update_exec", "reprocess_exec", "entity_fact_lookup"}
    _early_route = str(bundle.get("route") or "")
    _is_structured_route = _early_route in _STRUCTURED_ONLY_ROUTES
    if _is_structured_route:
        required_fields: list[str] = []
        query_required_terms: list[str] = []
        context_chunks = list(bundle.get("context_chunks") or [])
        subject_anchor_terms: list[str] = []
        target_field_terms: list[str] = []
        evidence_map: dict[str, Any] = {}
        field_coverage_ratio = 1.0
        coverage_missing_fields: list[str] = []
        subject_coverage_ok = True
        target_field_coverage_ok = True
        refusal_candidate = False
        answerability = "sufficient"
    else:
        required_fields = _required_evidence_fields(req.query, planner)
        query_required_terms = [str(x or "").strip() for x in (bundle.get("query_required_terms") or _query_required_terms(req.query)) if str(x or "").strip()]
        query_required_terms = query_required_terms[:8]
        context_chunks = list(bundle.get("context_chunks") or [])
        subject_anchor_terms = _subject_anchor_terms(req.query)
        # B2: when V2 rewrites the query for retrieval, also collect anchor terms from the
        # rewritten form so retrieved chunks (indexed under expanded language) still pass
        # the subject-coverage check.
        if router is not None and (router.rewritten_query or "").strip():
            _extra_anchors = _subject_anchor_terms(router.rewritten_query)
            _seen_anchors = set(subject_anchor_terms)
            subject_anchor_terms = subject_anchor_terms + [t for t in _extra_anchors if t not in _seen_anchors]
            subject_anchor_terms = subject_anchor_terms[:12]
        target_field_terms = _target_field_terms(req.query)
        evidence_map = _build_evidence_map(required_fields, context_chunks)
        field_coverage_ratio, coverage_missing_fields = _coverage_from_map(required_fields, evidence_map)
        subject_coverage_ok = _subject_coverage_ok(subject_anchor_terms, context_chunks)
        target_field_coverage_ok = _target_field_coverage_ok(target_field_terms, context_chunks)
        refusal_candidate = bool(getattr(planner, "refusal_candidate", False)) or ("explicit_presence_evidence" in required_fields)
        answerability = _infer_answerability(
            hit_count=int(bundle.get("hit_count") or 0),
            coverage_ratio=float(field_coverage_ratio),
            refusal_candidate=refusal_candidate,
            has_requirements=bool(required_fields),
        )
        if (not subject_coverage_ok) and int(bundle.get("hit_count") or 0) > 0:
            answerability = "none" if refusal_candidate else "insufficient"
        if (not target_field_coverage_ok) and int(bundle.get("hit_count") or 0) > 0 and target_field_terms:
            answerability = "none" if refusal_candidate else "insufficient"
            if "target_field" not in coverage_missing_fields:
                coverage_missing_fields.append("target_field")
        if refusal_candidate and "explicit_presence_evidence" in required_fields:
            presence_ok = _presence_evidence_sufficient(req.query, context_chunks)
            if not presence_ok:
                answerability = "none"
                if "explicit_presence_evidence" not in coverage_missing_fields:
                    coverage_missing_fields.append("explicit_presence_evidence")
                field_coverage_ratio = 0.0
    bundle["required_evidence_fields"] = required_fields
    bundle["query_required_terms"] = query_required_terms
    bundle["target_field_terms"] = target_field_terms
    bundle["evidence_map"] = evidence_map
    effective_coverage_ratio = field_coverage_ratio if (subject_coverage_ok and target_field_coverage_ok) else 0.0
    bundle["coverage_ratio"] = float(effective_coverage_ratio)
    bundle["field_coverage_ratio"] = float(field_coverage_ratio)
    bundle["coverage_missing_fields"] = coverage_missing_fields
    bundle["subject_anchor_terms"] = subject_anchor_terms
    bundle["subject_coverage_ok"] = bool(subject_coverage_ok)
    bundle["target_field_coverage_ok"] = bool(target_field_coverage_ok)
    bundle["answerability"] = answerability
    bundle["refusal_candidate"] = refusal_candidate
    related_doc_selection_mode, evidence_backed_doc_count = _apply_related_docs_selection(bundle)
    subject_entity = _infer_subject_entity(req.query, detail_topic=str(bundle.get("detail_topic") or ""), route=str(bundle.get("route") or ""))
    logger.info(
        "agent_v2_executor_route",
        extra=sanitize_log_context(
            {
                "trace_id": trace_id,
                "route": str(bundle.get("route") or ""),
                "v2_top_route": router.route if router else "pre_computed",
                "v2_rewritten_query": router.rewritten_query if router else "",
                "v2_domain": router.domain if router else "",
                "facet_mode": str(bundle.get("facet_mode") or "none"),
                "qdrant_used": bool(bundle.get("qdrant_used")),
                "retrieval_mode": str(bundle.get("retrieval_mode") or ""),
                "vector_hit_count": int(bundle.get("vector_hit_count") or 0),
                "answerability": answerability,
                "coverage_ratio": float(bundle.get("coverage_ratio") or 0.0),
                "route_reason": str(bundle.get("route_reason") or getattr(planner, "route_reason", "")),
            }
        ),
    )
    route_name = str(bundle.get("route") or "")
    force_structured = route_name in {"bill_monthly_total", "entity_fact_lookup"}
    detail_zero_hit = route_name == "detail_extract" and int(bundle.get("hit_count") or 0) <= 0
    qualifier_gated = bool(query_required_terms) and answerability != "sufficient"
    subject_gated = (not subject_coverage_ok) and bool(subject_anchor_terms)
    target_field_gated = (not target_field_coverage_ok) and bool(target_field_terms)
    # V2 open-ended search: don't hard-gate on field-term coverage; synthesizer handles it.
    if router is not None and router.sub_intent in {"search_semantic", "entity_fact_lookup"}:
        target_field_gated = False
    force_refusal = (answerability == "none" or (refusal_candidate and answerability != "sufficient")) and route_name in {
        "search_bundle", "entity_fact_lookup", "period_aggregate", "detail_extract",
    }
    if qualifier_gated and route_name in {"search_bundle", "entity_fact_lookup", "period_aggregate", "detail_extract"}:
        force_refusal = True
    if subject_gated and route_name in {"search_bundle", "entity_fact_lookup", "period_aggregate", "detail_extract"}:
        force_refusal = True
    if target_field_gated and route_name in {"search_bundle", "entity_fact_lookup", "period_aggregate", "detail_extract"}:
        force_refusal = True
    if detail_zero_hit:
        force_refusal = True
    if force_refusal and (subject_gated or target_field_gated):
        bundle["related_docs"] = []
        related_doc_selection_mode = "evidence_only"
        evidence_backed_doc_count = 0
    if force_structured:
        card = None
        synth_error_code = "structured_route"
    elif force_refusal:
        card = None
        synth_error_code = "insufficient_evidence"
    else:
        synth_started = time.perf_counter()
        card, synth_error_code = _synthesize_with_model(
            req, planner, bundle, trace_id=trace_id, conversation=synth_conversation,
        )
        synth_latency_ms = int((time.perf_counter() - synth_started) * 1000)
    synth_fallback_used = False
    if card is None:
        synth_fallback_used = True
        card = _synthesize_fallback(req, planner, bundle)
    # Structured-route fallback returned no evidence → treat as refusal
    if synth_fallback_used and card is not None and card.insufficient_evidence:
        force_refusal = True
        synth_error_code = "insufficient_evidence"
    answer_mode = "search_summary"
    if force_refusal:
        answer_mode = "refusal"
        synth_fallback_used = True
        if not synth_error_code:
            synth_error_code = "insufficient_evidence"
        card = ResultCard(
            title="Insufficient Evidence",
            short_summary=BilingualText(
                en="Not enough evidence was found in the knowledge base to answer this question safely.",
                zh="资料中没有相关信息，且缺少足够证据，暂时无法确认。",
            ),
            key_points=[
                BilingualText(en="Please provide more specific documents or keywords.", zh="请补充更具体的文档或关键词后重试。"),
                BilingualText(
                    en=f"Missing evidence fields: {', '.join(coverage_missing_fields) if coverage_missing_fields else 'n/a'}",
                    zh=f"缺失证据字段：{', '.join(coverage_missing_fields) if coverage_missing_fields else '无'}",
                ),
            ],
            sources=bundle.get("sources") or [],
            actions=_default_actions(planner),
            evidence_summary=[f"{field}:0" for field in coverage_missing_fields[:8]],
            insufficient_evidence=True,
        )
    else:
        if route_name in {"detail_extract", "entity_fact_lookup", "period_aggregate", "bill_attention", "bill_monthly_total"}:
            answer_mode = "structured" if answerability == "sufficient" else "partial_structured"
        else:
            answer_mode = "search_summary"
        card.insufficient_evidence = False
        card.evidence_summary = [f"{field}:{len(evidence_map.get(field) or [])}" for field in required_fields[:8]]
        if (answerability == "none" or refusal_candidate) and _contains_specific_claim(
            f"{card.short_summary.zh}\n{card.short_summary.en}\n" + "\n".join(item.zh for item in card.key_points[:5])
        ):
            synth_fallback_used = True
            synth_error_code = "refusal_policy_violation"
            card = ResultCard(
                title="Insufficient Evidence",
                short_summary=BilingualText(
                    en="Not enough evidence was found in the knowledge base to answer this question safely.",
                    zh="资料中没有相关信息，且缺少足够证据，暂时无法确认。",
                ),
                key_points=[
                    BilingualText(en="Please provide more specific documents or keywords.", zh="请补充更具体的文档或关键词后重试。"),
                    BilingualText(
                        en=f"Missing evidence fields: {', '.join(coverage_missing_fields) if coverage_missing_fields else 'n/a'}",
                        zh=f"缺失证据字段：{', '.join(coverage_missing_fields) if coverage_missing_fields else '无'}",
                    ),
                ],
                sources=bundle.get("sources") or [],
                actions=_default_actions(planner),
                evidence_summary=[f"{field}:0" for field in coverage_missing_fields[:8]],
                insufficient_evidence=True,
            )
    total_latency_ms = int((time.perf_counter() - total_started) * 1000)
    logger.info(
        "agent_v2_timing",
        extra=sanitize_log_context(
            {
                "trace_id": trace_id,
                "route": str(bundle.get("route") or ""),
                "planner_latency_ms": int(planner_latency_ms),
                "executor_latency_ms": int(executor_latency_ms),
                "synth_latency_ms": int(synth_latency_ms),
                "total_latency_ms": int(total_latency_ms),
                "synth_fallback_used": bool(synth_fallback_used),
                "synth_error_code": str(synth_error_code or ""),
                "answer_mode": answer_mode,
            }
        ),
    )
    if req.ui_lang == "en":
        locale_response_mode = "en_native" if str(card.short_summary.en or "").strip() else "bilingual_fallback"
    else:
        locale_response_mode = "zh_native" if str(card.short_summary.zh or "").strip() else "bilingual_fallback"
    return AgentExecuteResponse(
        planner=planner,
        card=card,
        related_docs=bundle.get("related_docs") or [],
        trace_id=trace_id,
        executor_stats=AgentExecutorStats(
            hit_count=int(bundle.get("hit_count") or 0),
            doc_count=int(bundle.get("doc_count") or 0),
            used_chunk_count=len(bundle.get("context_chunks") or []),
            route=str(bundle.get("route") or ""),
            bilingual_search=bool(bundle.get("bilingual_search")),
            qdrant_used=bool(bundle.get("qdrant_used")),
            retrieval_mode=str(bundle.get("retrieval_mode") or "none"),
            vector_hit_count=int(bundle.get("vector_hit_count") or 0),
            lexical_hit_count=int(bundle.get("lexical_hit_count") or 0),
            fallback_reason=str(bundle.get("fallback_reason") or ""),
            facet_mode=str(bundle.get("facet_mode") or "none"),
            facet_keys=[str(item) for item in (bundle.get("facet_keys") or []) if str(item or "").strip()],
            context_policy=context_policy,
            fact_route=str(bundle.get("fact_route") or "none"),
            fact_month=str(bundle.get("fact_month") or ""),
            synth_fallback_used=bool(synth_fallback_used),
            synth_error_code=str(synth_error_code or ""),
            detail_topic=str(bundle.get("detail_topic") or ""),
            detail_mode=str(bundle.get("detail_mode") or ("fallback" if synth_fallback_used else "structured")),
            detail_rows_count=int(bundle.get("detail_rows_count") or 0),
            answerability=answerability,
            coverage_ratio=float(bundle.get("coverage_ratio") or 0.0),
            field_coverage_ratio=float(field_coverage_ratio),
            coverage_missing_fields=coverage_missing_fields,
            query_required_terms=query_required_terms,
            subject_anchor_terms=subject_anchor_terms,
            subject_coverage_ok=bool(subject_coverage_ok),
            target_field_terms=target_field_terms,
            target_field_coverage_ok=bool(target_field_coverage_ok),
            infra_guard_applied=bool(qualifier_gated or subject_gated or target_field_gated),
            locale_response_mode=locale_response_mode,
            answer_mode=answer_mode,
            evidence_backed_doc_count=int(evidence_backed_doc_count),
            related_doc_selection_mode=related_doc_selection_mode,
            subject_entity=subject_entity,
            route_reason=str(bundle.get("route_reason") or getattr(planner, "route_reason", "")),
            planner_latency_ms=planner_latency_ms,
            executor_latency_ms=executor_latency_ms,
            synth_latency_ms=synth_latency_ms,
        ),
    )


def execute_agent_legacy(db: Session, req: AgentExecuteRequest) -> AgentExecuteResponse:
    total_started = time.perf_counter()
    planner_latency_ms = 0
    executor_latency_ms = 0
    synth_latency_ms = 0
    planner: PlannerDecision
    planner_started = time.perf_counter()
    if req.planner is None:
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
            )
    planner_latency_ms = int((time.perf_counter() - planner_started) * 1000)

    trace_id = f"agt-{uuid.uuid4().hex[:12]}"
    context_policy = _context_policy_for_query(
        req.query,
        client_context=(req.client_context if isinstance(req.client_context, dict) else {}),
    )
    synth_conversation = _normalize_conversation_messages(req, context_policy=context_policy)
    executor_started = time.perf_counter()
    bundle = _execute_plan(db, req, planner)
    executor_latency_ms = int((time.perf_counter() - executor_started) * 1000)
    required_fields = _required_evidence_fields(req.query, planner)
    query_required_terms = [str(x or "").strip() for x in (bundle.get("query_required_terms") or _query_required_terms(req.query)) if str(x or "").strip()]
    query_required_terms = query_required_terms[:8]
    context_chunks = list(bundle.get("context_chunks") or [])
    subject_anchor_terms = _subject_anchor_terms(req.query)
    target_field_terms = _target_field_terms(req.query)
    evidence_map = _build_evidence_map(required_fields, context_chunks)
    field_coverage_ratio, coverage_missing_fields = _coverage_from_map(required_fields, evidence_map)
    subject_coverage_ok = _subject_coverage_ok(subject_anchor_terms, context_chunks)
    target_field_coverage_ok = _target_field_coverage_ok(target_field_terms, context_chunks)
    refusal_candidate = bool(getattr(planner, "refusal_candidate", False)) or ("explicit_presence_evidence" in required_fields)
    answerability = _infer_answerability(
        hit_count=int(bundle.get("hit_count") or 0),
        coverage_ratio=float(field_coverage_ratio),
        refusal_candidate=refusal_candidate,
        has_requirements=bool(required_fields),
    )
    if (not subject_coverage_ok) and int(bundle.get("hit_count") or 0) > 0:
        answerability = "none" if refusal_candidate else "insufficient"
    if (not target_field_coverage_ok) and int(bundle.get("hit_count") or 0) > 0 and target_field_terms:
        answerability = "none" if refusal_candidate else "insufficient"
        if "target_field" not in coverage_missing_fields:
            coverage_missing_fields.append("target_field")
    if refusal_candidate and "explicit_presence_evidence" in required_fields:
        presence_ok = _presence_evidence_sufficient(req.query, context_chunks)
        if not presence_ok:
            answerability = "none"
            if "explicit_presence_evidence" not in coverage_missing_fields:
                coverage_missing_fields.append("explicit_presence_evidence")
            field_coverage_ratio = 0.0
    bundle["required_evidence_fields"] = required_fields
    bundle["query_required_terms"] = query_required_terms
    bundle["target_field_terms"] = target_field_terms
    bundle["evidence_map"] = evidence_map
    effective_coverage_ratio = field_coverage_ratio if (subject_coverage_ok and target_field_coverage_ok) else 0.0
    bundle["coverage_ratio"] = float(effective_coverage_ratio)
    bundle["field_coverage_ratio"] = float(field_coverage_ratio)
    bundle["coverage_missing_fields"] = coverage_missing_fields
    bundle["subject_anchor_terms"] = subject_anchor_terms
    bundle["subject_coverage_ok"] = bool(subject_coverage_ok)
    bundle["target_field_coverage_ok"] = bool(target_field_coverage_ok)
    bundle["answerability"] = answerability
    bundle["refusal_candidate"] = refusal_candidate
    related_doc_selection_mode, evidence_backed_doc_count = _apply_related_docs_selection(bundle)
    subject_entity = _infer_subject_entity(req.query, detail_topic=str(bundle.get("detail_topic") or ""), route=str(bundle.get("route") or ""))
    logger.info(
        "agent_executor_route",
        extra=sanitize_log_context(
            {
                "trace_id": trace_id,
                "route": str(bundle.get("route") or ""),
                "facet_mode": str(bundle.get("facet_mode") or "none"),
                "facet_keys": [str(item) for item in (bundle.get("facet_keys") or []) if str(item or "").strip()],
                "qdrant_used": bool(bundle.get("qdrant_used")),
                "retrieval_mode": str(bundle.get("retrieval_mode") or ""),
                "vector_hit_count": int(bundle.get("vector_hit_count") or 0),
                "lexical_hit_count": int(bundle.get("lexical_hit_count") or 0),
                "fallback_reason": str(bundle.get("fallback_reason") or ""),
                "context_policy": context_policy,
                "fact_route": str(bundle.get("fact_route") or "none"),
                "fact_month": str(bundle.get("fact_month") or ""),
                "detail_topic": str(bundle.get("detail_topic") or ""),
                "detail_mode": str(bundle.get("detail_mode") or ""),
                "detail_rows_count": int(bundle.get("detail_rows_count") or 0),
                "answerability": answerability,
                "coverage_ratio": float(bundle.get("coverage_ratio") or 0.0),
                "field_coverage_ratio": float(field_coverage_ratio),
                "coverage_missing_fields": coverage_missing_fields,
                "query_required_terms": query_required_terms,
                "subject_anchor_terms": subject_anchor_terms,
                "subject_coverage_ok": bool(subject_coverage_ok),
                "target_field_terms": target_field_terms,
                "target_field_coverage_ok": bool(target_field_coverage_ok),
                "related_doc_selection_mode": related_doc_selection_mode,
                "evidence_backed_doc_count": int(evidence_backed_doc_count),
                "subject_entity": subject_entity,
                "route_reason": str(bundle.get("route_reason") or getattr(planner, "route_reason", "")),
            }
        ),
    )
    route_name = str(bundle.get("route") or "")
    force_structured = route_name in {"bill_monthly_total", "entity_fact_lookup"}
    detail_zero_hit = route_name == "detail_extract" and int(bundle.get("hit_count") or 0) <= 0
    qualifier_gated = bool(query_required_terms) and answerability != "sufficient"
    subject_gated = (not subject_coverage_ok) and bool(subject_anchor_terms)
    target_field_gated = (not target_field_coverage_ok) and bool(target_field_terms)
    force_refusal = (answerability == "none" or (refusal_candidate and answerability != "sufficient")) and route_name in {
        "search_bundle",
        "entity_fact_lookup",
        "period_aggregate",
        "detail_extract",
    }
    if qualifier_gated and route_name in {"search_bundle", "entity_fact_lookup", "period_aggregate", "detail_extract"}:
        force_refusal = True
    if subject_gated and route_name in {"search_bundle", "entity_fact_lookup", "period_aggregate", "detail_extract"}:
        force_refusal = True
    if target_field_gated and route_name in {"search_bundle", "entity_fact_lookup", "period_aggregate", "detail_extract"}:
        force_refusal = True
    if detail_zero_hit:
        force_refusal = True
    if force_refusal and (subject_gated or target_field_gated):
        bundle["related_docs"] = []
        related_doc_selection_mode = "evidence_only"
        evidence_backed_doc_count = 0
    if force_structured:
        card = None
        synth_error_code = "structured_route"
    elif force_refusal:
        card = None
        synth_error_code = "insufficient_evidence"
    else:
        synth_started = time.perf_counter()
        card, synth_error_code = _synthesize_with_model(
            req,
            planner,
            bundle,
            trace_id=trace_id,
            conversation=synth_conversation,
        )
        synth_latency_ms = int((time.perf_counter() - synth_started) * 1000)
    synth_fallback_used = False
    if card is None:
        synth_fallback_used = True
        card = _synthesize_fallback(req, planner, bundle)
    # Structured-route fallback returned no evidence → treat as refusal
    if synth_fallback_used and card is not None and card.insufficient_evidence:
        force_refusal = True
        synth_error_code = "insufficient_evidence"
    answer_mode = "search_summary"
    if force_refusal:
        answer_mode = "refusal"
        synth_fallback_used = True
        if not synth_error_code:
            synth_error_code = "insufficient_evidence"
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
                    en=f"Missing evidence fields: {', '.join(coverage_missing_fields) if coverage_missing_fields else 'n/a'}",
                    zh=f"缺失证据字段：{', '.join(coverage_missing_fields) if coverage_missing_fields else '无'}",
                ),
            ],
            sources=bundle.get("sources") or [],
            actions=_default_actions(planner),
            evidence_summary=[f"{field}:0" for field in coverage_missing_fields[:8]],
            insufficient_evidence=True,
        )
    else:
        if route_name in {"detail_extract", "entity_fact_lookup", "period_aggregate", "bill_attention", "bill_monthly_total"}:
            answer_mode = "structured" if answerability == "sufficient" else "partial_structured"
        else:
            answer_mode = "search_summary"
        card.insufficient_evidence = False
        card.evidence_summary = [f"{field}:{len(evidence_map.get(field) or [])}" for field in required_fields[:8]]
        if (answerability == "none" or refusal_candidate) and _contains_specific_claim(
            f"{card.short_summary.zh}\n{card.short_summary.en}\n" + "\n".join(item.zh for item in card.key_points[:5])
        ):
            synth_fallback_used = True
            synth_error_code = "refusal_policy_violation"
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
                        en=f"Missing evidence fields: {', '.join(coverage_missing_fields) if coverage_missing_fields else 'n/a'}",
                        zh=f"缺失证据字段：{', '.join(coverage_missing_fields) if coverage_missing_fields else '无'}",
                    ),
                ],
                sources=bundle.get("sources") or [],
                actions=_default_actions(planner),
                evidence_summary=[f"{field}:0" for field in coverage_missing_fields[:8]],
                insufficient_evidence=True,
            )

    total_latency_ms = int((time.perf_counter() - total_started) * 1000)
    logger.info(
        "agent_execute_timing",
        extra=sanitize_log_context(
            {
                "trace_id": trace_id,
                "route": str(bundle.get("route") or ""),
                "planner_latency_ms": int(planner_latency_ms),
                "executor_latency_ms": int(executor_latency_ms),
                "synth_latency_ms": int(synth_latency_ms),
                "total_latency_ms": int(total_latency_ms),
                "synth_fallback_used": bool(synth_fallback_used),
                "synth_error_code": str(synth_error_code or ""),
                "answer_mode": answer_mode,
            }
        ),
    )

    if req.ui_lang == "en":
        locale_response_mode = "en_native" if str(card.short_summary.en or "").strip() else "bilingual_fallback"
    else:
        locale_response_mode = "zh_native" if str(card.short_summary.zh or "").strip() else "bilingual_fallback"

    return AgentExecuteResponse(
        planner=planner,
        card=card,
        related_docs=bundle.get("related_docs") or [],
        trace_id=trace_id,
        executor_stats=AgentExecutorStats(
            hit_count=int(bundle.get("hit_count") or 0),
            doc_count=int(bundle.get("doc_count") or 0),
            used_chunk_count=len(bundle.get("context_chunks") or []),
            route=str(bundle.get("route") or ""),
            bilingual_search=bool(bundle.get("bilingual_search")),
            qdrant_used=bool(bundle.get("qdrant_used")),
            retrieval_mode=str(bundle.get("retrieval_mode") or "none"),
            vector_hit_count=int(bundle.get("vector_hit_count") or 0),
            lexical_hit_count=int(bundle.get("lexical_hit_count") or 0),
            fallback_reason=str(bundle.get("fallback_reason") or ""),
            facet_mode=str(bundle.get("facet_mode") or "none"),
            facet_keys=[str(item) for item in (bundle.get("facet_keys") or []) if str(item or "").strip()],
            context_policy=context_policy,
            fact_route=str(bundle.get("fact_route") or "none"),
            fact_month=str(bundle.get("fact_month") or ""),
            synth_fallback_used=bool(synth_fallback_used),
            synth_error_code=str(synth_error_code or ""),
            detail_topic=str(bundle.get("detail_topic") or ""),
            detail_mode=str(bundle.get("detail_mode") or ("fallback" if synth_fallback_used else "structured")),
            detail_rows_count=int(bundle.get("detail_rows_count") or 0),
            answerability=answerability,
            coverage_ratio=float(bundle.get("coverage_ratio") or 0.0),
            field_coverage_ratio=float(field_coverage_ratio),
            coverage_missing_fields=coverage_missing_fields,
            query_required_terms=query_required_terms,
            subject_anchor_terms=subject_anchor_terms,
            subject_coverage_ok=bool(subject_coverage_ok),
            target_field_terms=target_field_terms,
            target_field_coverage_ok=bool(target_field_coverage_ok),
            infra_guard_applied=bool(qualifier_gated or subject_gated or target_field_gated),
            locale_response_mode=locale_response_mode,
            answer_mode=answer_mode,
            evidence_backed_doc_count=int(evidence_backed_doc_count),
            related_doc_selection_mode=related_doc_selection_mode,
            subject_entity=subject_entity,
            route_reason=str(bundle.get("route_reason") or getattr(planner, "route_reason", "")),
        ),
    )


def execute_agent(db: Session, req: AgentExecuteRequest) -> AgentExecuteResponse:
    graph_enabled = bool(getattr(settings, "agent_graph_enabled", False))
    graph_shadow = bool(getattr(settings, "agent_graph_shadow_enabled", False))
    graph_fail_open = bool(getattr(settings, "agent_graph_fail_open", True))
    if not graph_enabled:
        resp = execute_agent_v2(db, req)
        if graph_shadow:
            try:
                from app.services.agent_graph import try_run_agent_graph_shadow

                shadow = try_run_agent_graph_shadow(db, req)
                if shadow is not None:
                    logger.info(
                        "agent_graph_shadow_compare",
                        extra=sanitize_log_context(
                            {
                                "legacy_route": str(getattr(resp.executor_stats, "route", "") or ""),
                                "graph_route": str(getattr(shadow.executor_stats, "route", "") or ""),
                                "legacy_answer_mode": str(getattr(resp.executor_stats, "answer_mode", "") or ""),
                                "graph_answer_mode": str(getattr(shadow.executor_stats, "answer_mode", "") or ""),
                            }
                        ),
                    )
            except Exception:
                pass
        return resp

    try:
        from app.services.agent_graph import execute_agent_graph

        return execute_agent_graph(db, req)
    except Exception as exc:
        if not graph_fail_open:
            raise
        logger.warning(
            "agent_graph_failed_fallback_legacy",
            extra=sanitize_log_context(
                {
                    "error_code": "agent_graph_failed_fallback_legacy",
                    "exc_type": type(exc).__name__,
                    "detail": str(exc),
                }
            ),
        )
        return execute_agent_v2(db, req)
