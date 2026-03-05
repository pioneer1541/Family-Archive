import datetime as dt
import os
import re
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import BillFact, Document

_AMOUNT_PATTERNS: list[tuple[int, re.Pattern[str], int]] = [
    (
        110,
        re.compile(
            r"(?i)(?:amount\s+due|total\s+due|balance(?:\s+due)?|total\s+amount|amount\s+payable|amount\s+owing)\s*(?:[:：=]|is)?\s*(?:AUD\s*)?\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:AUD|澳币|元)?"
        ),
        1,
    ),
    (
        108,
        re.compile(
            r"(?i)(?:应付总额|应缴总额|应缴金额|应付金额|总应付|应付|总额)\s*(?:[:：=]|为)?\s*(?:澳币|AUD)?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:澳币|AUD|元)?"
        ),
        1,
    ),
    (
        86,
        re.compile(
            r"(?i)(?:amount|total|sum|合计|总计|金额|总额)\s*(?:[:：=]|为)?\s*(?:澳币|AUD)?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:澳币|AUD|元)?"
        ),
        1,
    ),
    (
        35,
        re.compile(r"(?i)\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"),
        1,
    ),
]

_DUE_PATTERNS = [
    re.compile(
        r"(?i)(?:due\s*date|payment\s*due|due\s*by|deadline)\s*[:：]?\s*([0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[A-Za-z]{3,9}\s+[0-9]{1,2},?\s+[0-9]{4}|[0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4}|[0-9]{1,2}-[A-Za-z]{3,9}-[0-9]{2,4})"
    ),
    re.compile(
        r"(?:到期日|截止日期|缴费截止(?:日期)?)\s*(?:[:：=]|为)?\s*([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日|[0-9]{1,2}-[A-Za-z]{3,9}-[0-9]{2,4})"
    ),
]

_PERIOD_PATTERNS = [
    re.compile(
        r"(?i)(?:period|billing\s*period|usage\s*period)\s*[:：]?\s*([0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})\s*(?:to|~|-|–)\s*([0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})"
    ),
    re.compile(r"([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)\s*(?:至|到|~|-|–)\s*([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)"),
    re.compile(
        r"(?i)(?:from\s+)?([0-9]{1,2}-[A-Za-z]{3,9}-[0-9]{2,4}|[0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2})\s*(?:to|~|-|–|至|到)\s*([0-9]{1,2}-[A-Za-z]{3,9}-[0-9]{2,4}|[0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2})"
    ),
]

_NON_FORMAL_BILL_HINTS = (
    "welcome",
    "tips",
    "guide",
    "how to",
    "how-to",
    "billing-tips",
    "proposal",
    "quote",
    "quotation",
    "contract",
    "agreement",
    "signed",
    "offer",
    "deposit",
    "stc",
    "说明",
    "提示",
    "如何",
    "方案",
    "报价",
    "合同",
    "协议",
    "签署",
    "定金",
)
_INSURANCE_POLICY_EXCLUDE_TOKENS = (
    "car policy",
    "policy account",
    "certificate of insurance",
    "vehicle insurance",
    "motor insurance",
    "aami car",
    "保险证明",
    "车辆保险",
    "机动车保险",
    "保单账户",
)
_POSITIVE_AMOUNT_HINTS = (
    "amount due",
    "total due",
    "balance due",
    "amount payable",
    "应缴",
    "应付",
    "总额",
    "合计",
    "总计",
    "到期",
    "截止",
)
_NEGATIVE_AMOUNT_HINTS = (
    "单价",
    "rate",
    "/kwh",
    "per kwh",
    "per mj",
    "unit price",
    "每千瓦时",
    "每兆焦",
)

_PAID_MARKERS = ["已缴", "已支付", "完成缴费", "paid", "payment received", "settled"]
_UNPAID_MARKERS = [
    "未缴",
    "待缴",
    "欠费",
    "逾期",
    "overdue",
    "outstanding",
    "unpaid",
    "past due",
]


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _parse_date(raw: str) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace("年", "-").replace("月", "-").replace("日", "").replace(".", "-")
    compact_month = re.fullmatch(r"([0-9]{1,2})([A-Za-z]{3,9})([0-9]{2,4})", text)
    if compact_month:
        text = f"{compact_month.group(1)} {compact_month.group(2)} {compact_month.group(3)}"
    text = re.sub(r"\s+", " ", text).strip()

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d-%b-%y",
        "%m/%d/%Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            parsed = dt.datetime.strptime(text.replace(",", ""), fmt)
            return parsed.replace(tzinfo=dt.UTC)
        except Exception:
            continue
    return None


def _looks_non_formal_bill(document: Document) -> bool:
    text = "\n".join(
        [
            str(document.file_name or ""),
            str(document.title_zh or ""),
            str(document.title_en or ""),
        ]
    ).lower()
    return any(token in text for token in _NON_FORMAL_BILL_HINTS)


def _looks_insurance_policy_doc(document: Document, text: str) -> bool:
    merged = "\n".join(
        [
            str(document.file_name or ""),
            str(document.title_zh or ""),
            str(document.title_en or ""),
            str(document.summary_zh or ""),
            str(document.summary_en or ""),
            str(text or ""),
        ]
    ).lower()
    if not any(token in merged for token in ("insurance", "保单", "保险")):
        return False
    return any(token in merged for token in _INSURANCE_POLICY_EXCLUDE_TOKENS)


def _extract_amount_and_currency(text: str) -> tuple[float | None, str]:
    raw = str(text or "")
    if not raw.strip():
        return (None, "AUD")

    candidates: list[tuple[float, float, str]] = []
    lowered = raw.lower()
    for base_score, pattern, group_idx in _AMOUNT_PATTERNS:
        for match in pattern.finditer(raw):
            numeric = str(match.group(group_idx) or "")
            if not numeric:
                continue
            amount = None
            try:
                amount = float(numeric.replace(",", ""))
            except Exception:
                amount = None
            if amount is None:
                continue

            start = max(0, int(match.start()) - 60)
            end = min(len(raw), int(match.end()) + 60)
            context = raw[start:end].lower()

            score = float(base_score)
            if any(token in context for token in _POSITIVE_AMOUNT_HINTS):
                score += 16.0
            if any(token in context for token in _NEGATIVE_AMOUNT_HINTS):
                score -= 78.0
            if any(token in context for token in ("discount", "rebate", "credit", "折扣", "抵扣", "优惠")):
                score -= 8.0
            if amount <= 0:
                score -= 50.0
            elif amount < 1:
                score -= 24.0
            elif amount < 5:
                score -= 10.0
            if amount > 1_000_000:
                score -= 140.0
            elif amount > 50_000:
                score -= 95.0
            elif amount > 20_000:
                score -= 60.0

            digits = re.sub(r"[^0-9]", "", numeric)
            if ("." not in numeric) and len(digits) >= 7:
                score -= 120.0

            if amount >= 50:
                score += 10.0
            elif amount >= 20:
                score += 8.0
            if amount >= 1000:
                score += 4.0

            if "$" in match.group(0) or "aud" in context or "australia" in lowered or "澳币" in context:
                currency = "AUD"
            elif "人民币" in context or "元" in context:
                currency = "CNY"
            else:
                currency = "AUD"
            candidates.append((score, amount, currency))

    if not candidates:
        return (None, "AUD")

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    score, amount, currency = candidates[0]
    if score < 10:
        return (None, "AUD")
    return (amount, currency)


def _extract_due_date(text: str) -> dt.datetime | None:
    raw = str(text or "")
    for pattern in _DUE_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        parsed = _parse_date(match.group(1))
        if parsed is not None:
            return parsed
    return None


def _extract_billing_period(text: str) -> tuple[dt.datetime | None, dt.datetime | None]:
    raw = str(text or "")
    zh_partial = re.search(
        r"([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日\s*(?:至|到|~|-|–)\s*(?:([0-9]{4})年)?([0-9]{1,2})月([0-9]{1,2})日",
        raw,
    )
    if zh_partial:
        y1 = int(zh_partial.group(1))
        m1 = int(zh_partial.group(2))
        d1 = int(zh_partial.group(3))
        y2 = int(zh_partial.group(4)) if zh_partial.group(4) else y1
        m2 = int(zh_partial.group(5))
        d2 = int(zh_partial.group(6))
        try:
            start = dt.datetime(y1, m1, d1, tzinfo=dt.UTC)
            end = dt.datetime(y2, m2, d2, tzinfo=dt.UTC)
            return (start, end)
        except Exception:
            pass
    for pattern in _PERIOD_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        start = _parse_date(match.group(1))
        end = _parse_date(match.group(2))
        if start is not None or end is not None:
            return (start, end)
    return (None, None)


def _has_date_anchor(
    *,
    due_date: dt.datetime | None,
    period_start: dt.datetime | None,
    period_end: dt.datetime | None,
) -> bool:
    return due_date is not None or period_start is not None or period_end is not None


def _infer_payment_status(text: str, due_date: dt.datetime | None) -> str:
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in _PAID_MARKERS):
        return "paid"
    if any(marker in lowered for marker in _UNPAID_MARKERS):
        if due_date is not None and due_date < dt.datetime.now(dt.UTC):
            return "overdue"
        return "unpaid"
    if due_date is not None and due_date < dt.datetime.now(dt.UTC):
        return "overdue"
    return "unknown"


def _infer_vendor(file_name: str) -> str:
    base = os.path.splitext(str(file_name or "").strip())[0]
    if not base:
        return ""
    text = re.sub(r"[_\-]+", " ", base)
    text = re.sub(r"\s+", " ", text).strip()
    lowered = text.lower()
    for token in ("invoice", "bill", "statement", "notice"):
        idx = lowered.find(token)
        if idx > 0:
            return text[:idx].strip()[:160]
    return text[:160]


def _build_confidence(
    *,
    amount_due: float | None,
    due_date: dt.datetime | None,
    payment_status: str,
    period_start: dt.datetime | None,
    period_end: dt.datetime | None,
) -> float:
    score = 0.15
    if amount_due is not None:
        score += 0.45
    if due_date is not None:
        score += 0.2
    if period_start is not None or period_end is not None:
        score += 0.12
    if payment_status in {"paid", "unpaid", "overdue"}:
        score += 0.08
    return max(0.0, min(1.0, round(score, 2)))


def extract_bill_fact_payload(document: Document, *, content_excerpt: str = "") -> dict[str, Any] | None:
    category_path = str(document.category_path or "").strip().lower()
    if not category_path.startswith("finance/bills"):
        return None
    if _looks_non_formal_bill(document):
        return None

    merged = "\n".join(
        [
            str(document.file_name or ""),
            str(document.title_zh or ""),
            str(document.title_en or ""),
            str(document.summary_zh or ""),
            str(document.summary_en or ""),
            str(content_excerpt or ""),
        ]
    )
    if not merged.strip():
        return None
    if _looks_insurance_policy_doc(document, merged):
        return None

    amount_due, currency = _extract_amount_and_currency(merged)
    due_date = _extract_due_date(merged)
    period_start, period_end = _extract_billing_period(merged)
    if amount_due is None or (
        not _has_date_anchor(due_date=due_date, period_start=period_start, period_end=period_end)
    ):
        return None

    payment_status = _infer_payment_status(merged, due_date)
    vendor = _infer_vendor(document.file_name)
    confidence = _build_confidence(
        amount_due=amount_due,
        due_date=due_date,
        payment_status=payment_status,
        period_start=period_start,
        period_end=period_end,
    )
    evidence_text = _clean_text(f"{document.summary_zh}\n{document.summary_en}")[:800]

    return {
        "vendor": vendor,
        "amount_due": amount_due,
        "currency": currency,
        "due_date": due_date,
        "billing_period_start": period_start,
        "billing_period_end": period_end,
        "payment_status": payment_status,
        "payment_date": None,
        "confidence": confidence,
        "evidence_text": evidence_text,
        "extraction_version": "bill-facts-v2",
    }


def upsert_bill_fact_for_document(db: Session, document: Document, *, content_excerpt: str = "") -> BillFact | None:
    payload = extract_bill_fact_payload(document, content_excerpt=content_excerpt)
    existing = db.execute(select(BillFact).where(BillFact.document_id == document.id)).scalars().first()
    if payload is None:
        if existing is not None:
            db.delete(existing)
            db.flush()
        return None

    if existing is None:
        existing = BillFact(document_id=document.id)
        db.add(existing)

    existing.vendor = str(payload["vendor"] or "")[:160]
    existing.amount_due = payload["amount_due"]
    existing.currency = str(payload["currency"] or "AUD")[:12]
    existing.due_date = payload["due_date"]
    existing.billing_period_start = payload["billing_period_start"]
    existing.billing_period_end = payload["billing_period_end"]
    existing.payment_status = str(payload["payment_status"] or "unknown")[:24]
    existing.payment_date = payload["payment_date"]
    existing.confidence = float(payload["confidence"] or 0.0)
    existing.evidence_text = str(payload["evidence_text"] or "")[:1200]
    existing.extraction_version = str(payload["extraction_version"] or "bill-facts-v1")[:32]
    existing.updated_at = dt.datetime.now(dt.UTC)
    db.flush()
    return existing


def list_recent_bill_facts(
    db: Session,
    *,
    limit: int = 24,
    since: dt.datetime | None = None,
    target_year: int | None = None,
    target_month: int | None = None,
) -> list[tuple[BillFact, Document]]:
    safe_limit = max(1, min(1000, int(limit)))
    anchor_expr = func.coalesce(
        BillFact.billing_period_end,
        BillFact.due_date,
        BillFact.payment_date,
        BillFact.updated_at,
    )
    stmt = (
        select(BillFact, Document)
        .join(Document, Document.id == BillFact.document_id)
        .where(Document.status == "completed")
    )
    if since is not None:
        stmt = stmt.where(anchor_expr >= since)
    if target_month is not None:
        month_str = f"{int(target_month):02d}"
        date_cols = [
            BillFact.billing_period_end,
            BillFact.billing_period_start,
            BillFact.due_date,
        ]
        if target_year is not None:
            year_str = str(int(target_year))
            stmt = stmt.where(
                or_(
                    *(
                        and_(
                            func.strftime("%m", col) == month_str,
                            func.strftime("%Y", col) == year_str,
                        )
                        for col in date_cols
                    )
                )
            )
        else:
            stmt = stmt.where(or_(*(func.strftime("%m", col) == month_str for col in date_cols)))
    rows = db.execute(stmt.order_by(anchor_expr.desc().nullslast(), BillFact.updated_at.desc()).limit(safe_limit)).all()
    return [(bill, doc) for bill, doc in rows]
