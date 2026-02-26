from app.models import Document, DocumentStatus
from app.services.bill_facts import extract_bill_fact_payload


def _doc(*, file_name: str, title_zh: str, summary_zh: str, category_path: str = "finance/bills/electricity") -> Document:
    return Document(
        source_path=f"/tmp/{file_name}",
        file_name=file_name,
        file_ext="pdf",
        file_size=123,
        sha256="a" * 64,
        status=DocumentStatus.COMPLETED.value,
        title_zh=title_zh,
        title_en="",
        summary_zh=summary_zh,
        summary_en="",
        category_path=category_path,
        category_label_en="Bills",
        category_label_zh="账单与缴费",
    )


def test_bill_fact_extract_prefers_total_due_not_unit_rate():
    doc = _doc(
        file_name="Invoice_202602.pdf",
        title_zh="2026年2月电费账单",
        summary_zh=(
            "高峰时段17.53千瓦时，单价0.3400澳币/千瓦时；"
            "应付总额109.00澳币；到期日2026年2月23日。"
        ),
    )
    payload = extract_bill_fact_payload(doc)
    assert payload is not None
    assert float(payload.get("amount_due") or 0.0) == 109.0
    assert str(payload.get("currency") or "") == "AUD"
    assert payload.get("due_date") is not None


def test_bill_fact_extract_skips_non_formal_welcome_doc():
    doc = _doc(
        file_name="Welcome-billing-tips-v0.3.pdf",
        title_zh="账单使用说明",
        summary_zh="支付提示与步骤说明，可访问官网了解更多。",
    )
    payload = extract_bill_fact_payload(doc)
    assert payload is None


def test_bill_fact_extract_requires_amount_and_date_anchor():
    doc = _doc(
        file_name="Invoice_without_due_date.pdf",
        title_zh="账单通知",
        summary_zh="本期金额109.00澳币。",
    )
    payload = extract_bill_fact_payload(doc)
    assert payload is None


def test_bill_fact_extract_skips_proposal_contract_docs():
    doc = _doc(
        file_name="Signed_Solar_Proposal_for_YUN_XIE.pdf",
        title_zh="太阳能方案合同",
        summary_zh="签署的太阳能方案，定金2000澳币，总价10300澳币，施工完成后支付尾款。",
    )
    payload = extract_bill_fact_payload(doc)
    assert payload is None


def test_bill_fact_extract_skips_vehicle_insurance_policy_docs_even_if_misclassified_bill():
    doc = _doc(
        file_name="AAMI_Car_Policy_Account_MPA167699547.pdf",
        title_zh="AAMI车辆保险保单",
        summary_zh="保险保费109.00澳币，含车辆保险证明与保单条款。",
        category_path="finance/bills/other",
    )
    payload = extract_bill_fact_payload(doc, content_excerpt="Certificate of Insurance Car Policy Account")
    assert payload is None


def test_bill_fact_extract_supports_dash_month_name_dates():
    doc = _doc(
        file_name="Invoice9804231.pdf",
        title_zh="2026年2月电费账单",
        summary_zh=(
            "账单覆盖03-Feb-2026至14-Feb-2026，应付总额为4.47澳币，"
            "到期日为02-Mar-2026。"
        ),
    )
    payload = extract_bill_fact_payload(doc)
    assert payload is not None
    assert float(payload.get("amount_due") or 0.0) == 4.47
    assert payload.get("due_date") is not None
    assert payload.get("billing_period_start") is not None
    assert payload.get("billing_period_end") is not None


def test_bill_fact_extract_supports_zh_partial_year_period():
    doc = _doc(
        file_name="Invoice_2289677.pdf",
        title_zh="2026年1月5日至2月2日电费账单",
        summary_zh="该账单覆盖2026年1月5日至2月2日，账单总额为51.09澳币。",
    )
    payload = extract_bill_fact_payload(doc)
    assert payload is not None
    assert float(payload.get("amount_due") or 0.0) == 51.09
    start = payload.get("billing_period_start")
    end = payload.get("billing_period_end")
    assert start is not None and int(start.year) == 2026 and int(start.month) == 1
    assert end is not None and int(end.year) == 2026 and int(end.month) == 2
