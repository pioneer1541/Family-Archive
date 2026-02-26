import os

from app.config import get_settings


settings = get_settings()

DEFAULT_CATEGORY_PATH = "archive/misc"

_CATEGORY_LABELS: dict[str, tuple[str, str]] = {
    "home": ("Home", "家庭"),
    "home/manuals": ("Home Manuals", "家庭说明书"),
    "home/appliances": ("Appliances", "家电资料"),
    "home/property": ("Home Property", "家庭房产"),
    "home/maintenance": ("Home Maintenance", "家庭维护"),
    "home/insurance": ("Home Insurance", "家庭保险"),
    "home/insurance/vehicle": ("Vehicle Insurance", "车辆保险"),
    "home/insurance/property": ("Property Insurance", "房屋财产保险"),
    "home/insurance/pet": ("Pet Insurance", "宠物保险"),
    "home/insurance/other": ("Other Home Insurance", "家庭保险其他"),
    "home/pets": ("Pets", "宠物资料"),
    "finance": ("Finance", "财务"),
    "finance/bills": ("Bills", "账单"),
    "finance/bills/electricity": ("Electricity Bills", "电费账单"),
    "finance/bills/water": ("Water Bills", "水费账单"),
    "finance/bills/gas": ("Gas Bills", "燃气账单"),
    "finance/bills/internet": ("Internet Bills", "网络账单"),
    "finance/bills/other": ("Other Bills", "其他账单"),
    "finance/banking": ("Banking", "银行资料"),
    "finance/tax": ("Tax", "税务资料"),
    "finance/investment": ("Investment", "投资资料"),
    "finance/receipts": ("Receipts", "收据报销"),
    "legal": ("Legal", "法律"),
    "legal/contracts": ("Contracts", "合同文件"),
    "legal/visa": ("Visa", "签证资料"),
    "legal/property": ("Legal Property", "房产法务"),
    "legal/insurance": ("Legal Insurance", "保险法务"),
    "legal/insurance/terms": ("Insurance Terms", "保单条款"),
    "legal/insurance/claim_dispute": ("Insurance Claim Dispute", "理赔争议"),
    "legal/identity": ("Identity", "身份资料"),
    "health": ("Health", "健康"),
    "health/medical_records": ("Medical Records", "病历资料"),
    "health/prescriptions": ("Prescriptions", "处方资料"),
    "health/reports": ("Health Reports", "健康报告"),
    "health/insurance": ("Health Insurance", "医保资料"),
    "health/insurance/private": ("Private Health Insurance", "私保资料"),
    "health/insurance/other": ("Other Health Insurance", "健康保险其他"),
    "work": ("Work", "工作"),
    "work/meeting_notes": ("Meeting Notes", "会议纪要"),
    "work/research": ("Research", "研究资料"),
    "work/projects": ("Projects", "项目资料"),
    "work/certifications": ("Certifications", "资质证书"),
    "tech": ("Tech", "技术"),
    "tech/documentation": ("Technical Documentation", "技术文档"),
    "tech/network": ("Network", "网络运维"),
    "tech/home_assistant": ("Home Assistant", "家庭自动化"),
    "tech/ai": ("AI", "人工智能"),
    "tech/devops": ("DevOps", "开发运维"),
    "tech/hardware": ("Hardware", "硬件资料"),
    "education": ("Education", "学习"),
    "education/courses": ("Courses", "课程资料"),
    "education/books": ("Books", "书籍资料"),
    "education/reference": ("Reference", "参考资料"),
    "education/tutorials": ("Tutorials", "教程资料"),
    "media": ("Media", "媒体"),
    "media/photos": ("Photos", "照片资料"),
    "media/videos": ("Videos", "视频资料"),
    "media/design": ("Design", "设计资料"),
    "media/creative": ("Creative", "创作资料"),
    "personal": ("Personal", "个人"),
    "personal/travel": ("Travel", "旅行资料"),
    "personal/plans": ("Plans", "计划资料"),
    "personal/notes": ("Notes", "个人笔记"),
    "personal/correspondence": ("Correspondence", "通信资料"),
    "archive": ("Archive", "归档"),
    "archive/old": ("Old Archive", "历史归档"),
    "archive/misc": ("Archive Misc", "归档杂项"),
}

CANONICAL_CATEGORY_PATHS = tuple(sorted(_CATEGORY_LABELS.keys()))


def is_leaf_category_path(path: str | None) -> bool:
    normalized = normalize_category_path(path)
    if not normalized:
        return False
    prefix = normalized.rstrip("/") + "/"
    for candidate in CANONICAL_CATEGORY_PATHS:
        if str(candidate).startswith(prefix):
            return False
    return True


def leaf_category_paths(*, include_archive_misc: bool = True) -> tuple[str, ...]:
    out: list[str] = []
    for path in CANONICAL_CATEGORY_PATHS:
        if (not include_archive_misc) and path == "archive/misc":
            continue
        if is_leaf_category_path(path):
            out.append(path)
    return tuple(sorted(set(out)))


def _real(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.realpath(raw)
    except Exception:
        return ""


def _is_within(path: str, root: str) -> bool:
    p = _real(path)
    r = _real(root)
    if not p or not r:
        return False
    try:
        return os.path.commonpath([p, r]) == r
    except Exception:
        return p == r or p.startswith(r.rstrip("/\\") + os.sep)


def infer_source_type(path: str) -> str:
    p = _real(path)
    if not p:
        return "file"
    if _is_within(p, settings.mail_attachment_root):
        return "mail"
    if _is_within(p, settings.nas_default_source_dir):
        return "nas"
    return "file"


def normalize_category_path(path: str | None) -> str:
    p = str(path or "").strip().lower()
    if p in _CATEGORY_LABELS:
        return p
    return DEFAULT_CATEGORY_PATH


def category_labels_for_path(path: str | None) -> tuple[str, str]:
    normalized = normalize_category_path(path)
    return _CATEGORY_LABELS.get(normalized, _CATEGORY_LABELS[DEFAULT_CATEGORY_PATH])


def infer_category(path: str, text: str, *, subject: str = "", from_addr: str = "") -> tuple[str, str, str]:
    # Category classification is model-driven after summary generation.
    # This function remains as a compatibility fallback and intentionally returns stable default.
    en, zh = category_labels_for_path(DEFAULT_CATEGORY_PATH)
    return (en, zh, DEFAULT_CATEGORY_PATH)
