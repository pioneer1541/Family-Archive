import re
from dataclasses import dataclass, field
from typing import Any

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
    "search_documents": {
        "action_type": "agent_command",
        "payload": {"command": "search"},
    },
    "queue_ops": {"action_type": "agent_command", "payload": {"command": "queue_view"}},
    "queue_view": {
        "action_type": "agent_command",
        "payload": {"command": "queue_view"},
    },
    "list_recent": {
        "action_type": "agent_command",
        "payload": {"command": "list_recent"},
    },
    "compare_docs": {
        "action_type": "agent_command",
        "payload": {"command": "compare_docs"},
    },
    "timeline_extract": {
        "action_type": "agent_command",
        "payload": {"command": "timeline_build"},
    },
    "extract_fields": {
        "action_type": "agent_command",
        "payload": {"command": "extract_fields"},
    },
    "extract_details": {
        "action_type": "agent_command",
        "payload": {"command": "extract_details"},
    },
    "fallback_search": {
        "action_type": "agent_command",
        "payload": {"command": "fallback_search"},
    },
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
    "home": (
        "房屋",
        "房产",
        "物业",
        "贷款",
        "mortgage",
        "维修",
        "maintenance",
        "maintain",
        "water tank",
        "rainwater tank",
        "产权",
    ),
    "appliances": (
        "家电",
        "洗衣机",
        "冰箱",
        "洗碗机",
        "空调",
        "热水器",
        "水箱",
        "appliance",
        "dishwasher",
        "air purifier",
        "water heater",
        "hot water",
    ),
    "pets": (
        "宠物",
        "疫苗",
        "兽医",
        "体检",
        "绝育",
        "pet",
        "vaccine",
        "vet",
        "birthday",
        "birth date",
        "dob",
        "生日",
        "猫",
        "狗",
    ),
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
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
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
    "pets": (
        "宠物",
        "pet",
        "vaccine",
        "疫苗",
        "vet",
        "兽医",
        "绝育",
        "birthday",
        "birth date",
        "dob",
        "生日",
        "出生日期",
    ),
    "appliances": (
        "家电",
        "appliance",
        "洗衣机",
        "冰箱",
        "空调",
        "热水器",
        "洗碗机",
        "dishwasher",
        "warranty",
    ),
    "home": (
        "房屋",
        "房产",
        "物业",
        "贷款",
        "mortgage",
        "maintenance",
        "maintain",
        "维修",
        "产权",
        "建造年份",
        "water tank",
        "rainwater tank",
    ),
    "insurance": ("保险", "policy", "保单", "理赔", "claim", "premium"),
    "bills": ("账单", "bill", "invoice", "电费", "水费", "燃气", "internet"),
}
_DOMAIN_CATEGORY_WHITELISTS = {
    "pets": ("home/pets", "health/medical_records", "home/insurance/pet"),
    "appliances": ("home/manuals", "home/appliances", "tech/hardware"),
    "home": (
        "home/property",
        "home/maintenance",
        "legal/property",
        "finance/bills/other",
    ),
    "insurance": ("home/insurance", "health/insurance", "legal/insurance"),
    "bills": ("finance/bills",),
}
_SUBJECT_ANCHOR_HINTS: dict[str, tuple[str, ...]] = {
    "birthday_birthdate": ("birthday", "birth date", "dob", "生日", "出生日期"),
    "life_insurance": ("人寿", "life insurance", "beneficiary", "受益人"),
    "vehicle_insurance": (
        "车险",
        "车辆保险",
        "motor insurance",
        "car insurance",
        "vehicle insurance",
    ),
    "pet_insurance": ("宠物保险", "pet insurance"),
    "health_insurance": (
        "医保",
        "医疗险",
        "health insurance",
        "private health",
        "hospital cover",
    ),
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

_ANSWERABILITY_CONTACT_TOKENS = (
    "联系方式",
    "电话",
    "邮箱",
    "contact",
    "phone",
    "email",
)
_ANSWERABILITY_AMOUNT_TOKENS = (
    "多少钱",
    "金额",
    "total",
    "sum",
    "费用",
    "花了",
    "cost",
    "price",
    "premium",
)
_ANSWERABILITY_DATE_TOKENS = (
    "什么时候",
    "日期",
    "到期",
    "when",
    "date",
    "expiry",
    "due",
    "birthday",
    "birth date",
    "dob",
    "生日",
    "出生日期",
)
_ANSWERABILITY_PRESENCE_TOKENS = (
    "有没有",
    "是否",
    "有无",
    "do we have",
    "did we",
    "have we",
)

_SEARCH_FALLBACK_BOILERPLATE_PATTERNS = (
    r"\bbpay\b",
    r"\bbanking-?bpay\b",
    r"\busage details\b",
    r"\bplan features\b",
    r"\bconditional pay\b",
    r"\bsome handy hints\b",
    r"\bmonthly billing\b",
)

# Maps RouterDecision.sub_intent -> PlannerDecision.task_kind
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

# Maps RouterDecision.sub_intent -> PlannerDecision.intent (for _execute_plan compat)
_SUB_INTENT_TO_PLANNER_INTENT: dict[str, str] = {
    # bill_attention route in _execute_plan checks: planner.intent == "list_recent"
    "bill_attention": "list_recent",
    # bill_monthly_total: pass intent directly so _execute_plan can route without query regex
    "bill_monthly_total": "bill_monthly_total",
    "chitchat": "search_semantic",
}
