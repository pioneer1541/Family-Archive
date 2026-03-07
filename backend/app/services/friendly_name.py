import os
import re

_EN_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _clean_base(file_name: str) -> str:
    base = os.path.splitext(str(file_name or "").strip())[0]
    base = re.sub(r"[_\-]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base or "Untitled"


def _extract_year_month(text: str) -> tuple[int | None, int | None]:
    raw = str(text or "")

    zh = re.search(r"(20\d{2})\s*年\s*([01]?\d)\s*月", raw)
    if zh:
        y = int(zh.group(1))
        m = int(zh.group(2))
        if 1 <= m <= 12:
            return (y, m)

    ymd = re.search(r"(20\d{2})[\/\-.](0?[1-9]|1[0-2])(?:[\/\-.](?:0?[1-9]|[12]\d|3[01]))?", raw)
    if ymd:
        return (int(ymd.group(1)), int(ymd.group(2)))

    mdy = re.search(r"(0?[1-9]|1[0-2])[\/\-.](20\d{2})", raw)
    if mdy:
        return (int(mdy.group(2)), int(mdy.group(1)))

    en = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b[\s,/-]*(20\d{2})",
        raw,
        flags=re.IGNORECASE,
    )
    if en:
        m = _EN_MONTHS.get(str(en.group(1) or "").strip().lower())
        y = int(en.group(2))
        if m:
            return (y, m)

    y = re.search(r"\b(20\d{2})\b", raw)
    if y:
        return (int(y.group(1)), None)
    return (None, None)


def _kind_by_category(category_path: str) -> tuple[str, str] | None:
    cp = str(category_path or "").lower().strip()
    if cp.startswith("finance/bills/electricity"):
        return ("电费账单", "Electricity Bill")
    if cp.startswith("finance/bills/water"):
        return ("水费账单", "Water Bill")
    if cp.startswith("finance/bills/gas"):
        return ("燃气账单", "Gas Bill")
    if cp.startswith("finance/bills/internet"):
        return ("网络账单", "Internet Bill")
    if cp.startswith("finance/bills"):
        return ("账单", "Bill Statement")
    if cp.startswith("work/meeting_notes"):
        return ("会议纪要", "Meeting Notes")
    if cp.startswith("tech/"):
        return ("技术文档", "Technical Document")
    if cp.startswith("legal/"):
        return ("法律文件", "Legal Document")
    return None


def _pick_kind(hay: str, category_path: str) -> tuple[str, str]:
    h = str(hay or "").lower()

    # Category-first to keep naming consistent with selected taxonomy.
    category_pick = _kind_by_category(category_path)
    if category_pick is not None:
        return category_pick

    rules = [
        (
            (
                "agm",
                "annual general meeting",
                "meeting notice",
                "会议通知",
                "业主大会",
                "年会",
            ),
            ("AGM会议通知", "AGM Meeting Notice"),
        ),
        (
            ("electricity", "energy", "kwh", "usage details", "电费"),
            ("电费账单", "Electricity Bill"),
        ),
        (
            (
                "water bill",
                "yarra valley water",
                "water usage",
                "water rates",
                "sewerage",
                "水费",
            ),
            ("水费账单", "Water Bill"),
        ),
        (
            ("gas", "燃气", "煤气"),
            ("燃气账单", "Gas Bill"),
        ),
        (
            ("internet", "nbn", "broadband", "superloop", "宽带", "网络"),
            ("网络通信账单", "Internet Bill"),
        ),
        (
            ("invoice", "bill", "statement", "账单", "发票"),
            ("账单", "Bill Statement"),
        ),
        (
            ("warranty", "保修"),
            ("保修文件", "Warranty Document"),
        ),
    ]
    for keys, target in rules:
        if any(k in h for k in keys):
            return target

    return ("资料文档", "Reference Document")


def _should_prefix_date(*, category_path: str, hay: str) -> bool:
    cp = str(category_path or "").lower().strip()
    h = str(hay or "").lower()
    if cp.startswith("finance/"):
        return True
    if cp.startswith("work/meeting_notes"):
        return True
    strong_date_needed = [
        "invoice",
        "bill",
        "statement",
        "fee notice",
        "due date",
        "payment",
        "账单",
        "发票",
        "费用通知",
        "应付",
        "到期",
        "缴费",
    ]
    return any(token in h for token in strong_date_needed)


def generate_friendly_names(
    *,
    file_name: str,
    text: str,
    category_path: str,
    source_type: str,
    mail_subject: str = "",
) -> tuple[str, str]:
    base = _clean_base(file_name)
    hay = " ".join(
        [
            base,
            str(text or "")[:5000],
            str(mail_subject or ""),
            str(category_path or ""),
            str(source_type or ""),
        ]
    )
    year, month = _extract_year_month(hay)
    kind_zh, kind_en = _pick_kind(hay, category_path)
    keep_date = _should_prefix_date(category_path=category_path, hay=hay)

    if keep_date and year and month:
        zh = f"{year}年{month}月{kind_zh}"
        en = f"{year}-{month:02d} {kind_en}"
    elif keep_date and year:
        zh = f"{year}年{kind_zh}"
        en = f"{year} {kind_en}"
    else:
        zh = f"{kind_zh}（{base}）" if kind_zh else base
        en = f"{kind_en} ({base})" if kind_en else base

    zh = re.sub(r"\s+", " ", zh).strip()[:512]
    en = re.sub(r"\s+", " ", en).strip()[:512]
    if not zh:
        zh = base[:512]
    if not en:
        en = base[:512]
    return (en, zh)
