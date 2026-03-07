import datetime as dt
import re
from typing import Any

_MONTH_MAP = {
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

RE_DATE_ISO = re.compile(r"\b(20\d{2})[-/](1[0-2]|0?[1-9])[-/](3[01]|[12]\d|0?[1-9])\b")
RE_DATE_ZH = re.compile(r"(20\d{2})\s*年\s*(1[0-2]|0?[1-9])\s*月\s*(3[01]|[12]\d|0?[1-9])\s*日?")
RE_DATE_DMY = re.compile(r"\b(3[01]|[12]\d|0?[1-9])/(1[0-2]|0?[1-9])/(20\d{2})\b")
RE_DATE_MONTHNAME_1 = re.compile(
    r"\b("
    + "|".join(sorted(_MONTH_MAP.keys(), key=len, reverse=True))
    + r")\.?\s+(3[01]|[12]\d|0?[1-9]),?\s+(20\d{2})\b",
    flags=re.I,
)
RE_DATE_MONTHNAME_2 = re.compile(
    r"\b(3[01]|[12]\d|0?[1-9])\s+("
    + "|".join(sorted(_MONTH_MAP.keys(), key=len, reverse=True))
    + r")\.?\s+(20\d{2})\b",
    flags=re.I,
)
RE_DATE_YEAR_MONTH = re.compile(r"\b(20\d{2})[-/](1[0-2]|0?[1-9])\b")
RE_DATE_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(sorted(_MONTH_MAP.keys(), key=len, reverse=True)) + r")\.?\s+(20\d{2})\b",
    flags=re.I,
)

RE_PHONE_AU_GENERIC = re.compile(
    r"(?<!\d)(?:\+?61\s?[2-478](?:[\s-]?\d){8}|0[23478](?:[\s-]?\d){8}|13\d{2}(?:[\s-]?\d){2}|1300(?:[\s-]?\d){6}|1800(?:[\s-]?\d){6})(?!\d)"
)
RE_PHONE_FREEFORM = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{7,17}\d)(?!\d)")
RE_EMAIL = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b")
RE_AMOUNT = re.compile(
    r"(?:(AUD|USD)\s*)?\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(AUD|USD|澳币|元|美元)?",
    flags=re.I,
)
RE_REFERENCE = re.compile(
    r"\b(?:policy|invoice|inv|work\s*order|ticket|claim|ref(?:erence)?)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{4,})\b",
    flags=re.I,
)
RE_SQM = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:sqm|m2)\b|(?<!\d)(\d+(?:\.\d+)?)\s*(?:㎡|平方米)")
RE_MONTHLY_INTERVAL = re.compile(
    r"(?:every\s+(\d+)\s+(day|week|month|year)s?)|(?:每\s*(\d+)\s*(天|周|星期|月|年))",
    flags=re.I,
)
RE_MONTHLY_PAYMENT = re.compile(
    r"(?:monthly(?:\s+repayment|\s+payment)?|月供).{0,20}?(?:aud|\$|澳币)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)",
    flags=re.I,
)


def _uniq(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def find_dates(text: str, *, include_partial: bool = True) -> list[str]:
    raw = str(text or "")
    out: list[str] = []
    for y, m, d in RE_DATE_ISO.findall(raw):
        out.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
    for y, m, d in RE_DATE_ZH.findall(raw):
        out.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
    for d, m, y in RE_DATE_DMY.findall(raw):
        out.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
    for mon, d, y in RE_DATE_MONTHNAME_1.findall(raw):
        month = _MONTH_MAP.get(mon.lower().rstrip("."))
        if month:
            out.append(f"{int(y):04d}-{month:02d}-{int(d):02d}")
    for d, mon, y in RE_DATE_MONTHNAME_2.findall(raw):
        month = _MONTH_MAP.get(mon.lower().rstrip("."))
        if month:
            out.append(f"{int(y):04d}-{month:02d}-{int(d):02d}")
    if include_partial:
        for y, m in RE_DATE_YEAR_MONTH.findall(raw):
            out.append(f"{int(y):04d}-{int(m):02d}")
        for mon, y in RE_DATE_MONTH_YEAR.findall(raw):
            month = _MONTH_MAP.get(mon.lower().rstrip("."))
            if month:
                out.append(f"{int(y):04d}-{month:02d}")
    return _uniq(out)


def parse_date(value: str) -> dt.date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            if fmt == "%Y-%m":
                return dt.date(parsed.year, parsed.month, 1)
            return parsed.date()
        except Exception:
            continue
    return None


def find_phones(text: str) -> list[str]:
    raw = str(text or "")
    out = [" ".join(m.group(0).split()) for m in RE_PHONE_AU_GENERIC.finditer(raw)]
    if not out:
        out = [" ".join(m.group(0).split()) for m in RE_PHONE_FREEFORM.finditer(raw)]
    return _uniq(out)


def find_emails(text: str) -> list[str]:
    return _uniq([m.group(0) for m in RE_EMAIL.finditer(str(text or ""))])


def find_amounts(text: str) -> list[str]:
    raw = str(text or "")
    out: list[str] = []
    for m in RE_AMOUNT.finditer(raw):
        prefix, num, suffix = m.groups()
        if not num:
            continue
        # Require a currency marker or context word to reduce false positives.
        full = m.group(0)
        if not any(tok in full.lower() for tok in ("$", "aud", "usd", "澳币", "元", "美元")):
            window = raw[max(0, m.start() - 12) : min(len(raw), m.end() + 12)].lower()
            if not any(
                tok in window
                for tok in (
                    "amount",
                    "total",
                    "premium",
                    "费用",
                    "金额",
                    "due",
                    "price",
                    "cost",
                    "月供",
                )
            ):
                continue
        clean_num = num.replace(",", "")
        currency = (
            (prefix or suffix or "AUD").upper().replace("澳币", "AUD").replace("美元", "USD").replace("元", "CNY")
        )
        out.append(f"{currency} {clean_num}")
    return _uniq(out)


def find_references(text: str) -> list[str]:
    raw = str(text or "")
    out = [m.group(1).strip() for m in RE_REFERENCE.finditer(raw)]
    # Filter obvious false positives.
    out = [x for x in out if len(x) >= 5 and not x.isdigit()]
    return _uniq(out)


def find_area_sqm(text: str) -> list[str]:
    out: list[str] = []
    for m in RE_SQM.finditer(str(text or "")):
        num = m.group(1) or m.group(2)
        if num:
            out.append(f"{num} m2")
    return _uniq(out)


def find_interval_phrases(text: str) -> list[str]:
    raw = str(text or "")
    out: list[str] = []
    for m in RE_MONTHLY_INTERVAL.finditer(raw):
        if m.group(1) and m.group(2):
            out.append(f"every {m.group(1)} {m.group(2)}")
        elif m.group(3) and m.group(4):
            out.append(f"每{m.group(3)}{m.group(4)}")
    if not out:
        lowered = raw.lower()
        if "every month" in lowered:
            out.append("every 1 month")
        if "每月" in raw:
            out.append("每1月")
        if "每周" in raw:
            out.append("每1周")
    return _uniq(out)


def find_monthly_payment(text: str) -> list[str]:
    raw = str(text or "")
    out: list[str] = []
    for m in RE_MONTHLY_PAYMENT.finditer(raw):
        out.append(f"AUD {m.group(1).replace(',', '')}")
    return _uniq(out)


def contains_presence_evidence(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        tok in lowered
        for tok in (
            "有",
            "没有",
            "无",
            "未见",
            "未找到",
            "has",
            "have",
            "not found",
            "contains",
        )
    )


def contains_status_evidence(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        tok in lowered
        for tok in (
            "approved",
            "declined",
            "rejected",
            "paid",
            "unpaid",
            "pending",
            "获批",
            "拒赔",
            "已缴",
            "待缴",
            "同意",
        )
    )


def best_snippet(text: str, keywords: list[str], *, cap: int = 160) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    lowered = raw.lower()
    for kw in keywords:
        idx = lowered.find(str(kw or "").lower())
        if idx >= 0:
            start = max(0, idx - 40)
            end = min(len(raw), idx + max(40, len(kw) + 80))
            return " ".join(raw[start:end].split())[:cap]
    return " ".join(raw.split())[:cap]


def now_utc_date(ref: Any = None) -> dt.date:
    if isinstance(ref, dt.datetime):
        return ref.date()
    if isinstance(ref, dt.date):
        return ref
    return dt.datetime.now(dt.UTC).date()
