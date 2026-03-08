import datetime as dt
from types import SimpleNamespace

from app.services import bills


def test_bill_attention_query_detection_for_zh_and_en():
    assert bills._is_bill_attention_query("这个月电费账单") is True
    assert bills._is_bill_attention_query("show current bills") is True
    assert bills._is_bill_attention_query("pet insurance details") is False


def test_format_amount_and_due_date_by_language():
    due = dt.datetime(2026, 3, 12, tzinfo=dt.UTC)
    assert bills._format_due_date(due, ui_lang="en") == "2026-03-12"
    assert bills._format_due_date(due, ui_lang="zh") == "2026年3月12日"

    assert bills._format_amount(109.5, "aud", ui_lang="en") == "AUD 109.50"
    assert bills._format_amount(109.5, "aud", ui_lang="zh") == "澳币109.50"
    assert bills._format_amount(9, "usd", ui_lang="zh") == "USD 9.00"


def test_bill_fact_month_pairs_and_month_range_generation():
    start = dt.datetime(2026, 1, 15, tzinfo=dt.UTC)
    end = dt.datetime(2026, 3, 2, tzinfo=dt.UTC)
    pairs = bills._month_pairs_between(start, end)
    assert pairs == {(2026, 1), (2026, 2), (2026, 3)}

    fact = SimpleNamespace(
        due_date=dt.datetime(2026, 4, 10, tzinfo=dt.UTC),
        billing_period_start=start,
        billing_period_end=end,
    )
    out = bills._bill_fact_month_pairs(fact)
    assert (2026, 4) in out
    assert (2026, 1) in out
    assert (2026, 2) in out
    assert (2026, 3) in out


def test_bill_fact_validation_and_doc_association_checks():
    formal_doc = SimpleNamespace(file_name="invoice.pdf", title_zh="电费账单", title_en="Electricity bill")
    non_formal_doc = SimpleNamespace(file_name="welcome-billing-tips.pdf", title_zh="使用说明", title_en="Guide")

    no_amount = SimpleNamespace(amount_due=None, due_date=dt.datetime(2026, 3, 1, tzinfo=dt.UTC))
    ok, reason = bills._is_monthly_eligible_bill_fact(no_amount, formal_doc)
    assert ok is False
    assert reason == "missing_amount"

    no_date = SimpleNamespace(amount_due=88.0, due_date=None, billing_period_start=None, billing_period_end=None)
    ok, reason = bills._is_monthly_eligible_bill_fact(no_date, formal_doc)
    assert ok is False
    assert reason == "missing_date_anchor"

    valid = SimpleNamespace(
        amount_due=88.0,
        due_date=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
        billing_period_start=None,
        billing_period_end=None,
    )
    ok, reason = bills._is_monthly_eligible_bill_fact(valid, non_formal_doc)
    assert ok is False
    assert reason == "non_formal_doc"

    ok, reason = bills._is_monthly_eligible_bill_fact(valid, formal_doc)
    assert ok is True
    assert reason == ""


def test_infer_latest_year_for_target_month_from_bill_facts():
    rows = [
        (
            SimpleNamespace(
                due_date=dt.datetime(2025, 2, 5, tzinfo=dt.UTC),
                billing_period_start=None,
                billing_period_end=None,
            ),
            object(),
        ),
        (
            SimpleNamespace(
                due_date=dt.datetime(2026, 2, 8, tzinfo=dt.UTC),
                billing_period_start=None,
                billing_period_end=None,
            ),
            object(),
        ),
    ]
    assert bills._infer_latest_year_for_month(rows, 2) == 2026
    assert bills._infer_latest_year_for_month(rows, None) is None
