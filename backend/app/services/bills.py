import datetime as dt
from typing import Any

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
