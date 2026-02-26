from app.services import llm_summary


def test_regenerate_name_strips_date_prefix_for_manual(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"friendly_name_en":"2026-02 Rheem Water Heater Diagram","friendly_name_zh":"2026年2月Rheem热水器示意图"}',
            parsed_json={
                "friendly_name_en": "2026-02 Rheem Water Heater Diagram",
                "friendly_name_zh": "2026年2月Rheem热水器示意图",
            },
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.regenerate_friendly_name_from_summary(
        file_name="Rheem-CF-12-26-876A-874A-Series-TD.pdf",
        category_path="home/manuals",
        summary_en="Rheem installation diagram with water outlet and gas connection.",
        summary_zh="该文件是热水器安装示意图，包含热水出口和燃气连接。",
        fallback_en="Rheem Diagram",
        fallback_zh="Rheem示意图",
    )
    assert out is not None
    en, zh = out
    assert not en.startswith("2026")
    assert not zh.startswith("2026年")


def test_regenerate_name_keeps_date_prefix_for_bill(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"friendly_name_en":"2026-02 Water Bill","friendly_name_zh":"2026年2月水费账单"}',
            parsed_json={
                "friendly_name_en": "2026-02 Water Bill",
                "friendly_name_zh": "2026年2月水费账单",
            },
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.regenerate_friendly_name_from_summary(
        file_name="invoice_202602.pdf",
        category_path="finance/bills/water",
        summary_en="Water bill amount due AUD $166.20 due date 2026-02-28.",
        summary_zh="水费账单应付澳币$166.20，到期日2026-02-28。",
        fallback_en="Water Bill",
        fallback_zh="水费账单",
    )
    assert out is not None
    en, zh = out
    assert en.startswith("2026-02")
    assert zh.startswith("2026年2月")


def test_regenerate_name_removes_bill_terms_for_contract_category(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"friendly_name_en":"Electricity Bill","friendly_name_zh":"电费账单"}',
            parsed_json={"friendly_name_en": "Electricity Bill", "friendly_name_zh": "电费账单"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.regenerate_friendly_name_from_summary(
        file_name="Signed_Solar_Proposal_for_YUN_XIE.pdf",
        category_path="legal/contracts",
        summary_en="Signed solar proposal contract for installation with deposit and payment terms.",
        summary_zh="签署的太阳能方案合同，包含定金与付款条款。",
        fallback_en="Contract Document",
        fallback_zh="合同文件",
    )
    assert out is not None
    en, zh = out
    assert "Bill" not in en
    assert "账单" not in zh


def test_vehicle_insurance_conflict_name_downgrades_to_generic_vehicle(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"friendly_name_en":"Motorcycle Insurance Certificate 2025-2026","friendly_name_zh":"摩托车保险单 2025-2026"}',
            parsed_json={
                "friendly_name_en": "Motorcycle Insurance Certificate 2025-2026",
                "friendly_name_zh": "摩托车保险单 2025-2026",
            },
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.regenerate_friendly_name_from_summary(
        file_name="AAMI_Car_Certificate_of_Insurance_MPA167699547.pdf",
        category_path="home/insurance/vehicle",
        summary_en="Motorcycle Insurance certificate for AAMI car policy account.",
        summary_zh="AAMI车辆保险证明，但摘要中被写成摩托车保险。",
        fallback_en="Vehicle Insurance Certificate",
        fallback_zh="车辆保险证书",
        content_excerpt="[Page 1] Motorcycle Insurance ... your car is used on average ...",
    )
    assert out is not None
    en, zh = out
    assert "Motorcycle" not in en
    assert "摩托车" not in zh
    assert "Vehicle" in en
    assert "车辆保险" in zh


def test_vehicle_insurance_summary_conflict_downgrades_to_generic_vehicle():
    en, zh = llm_summary.normalize_vehicle_insurance_summary(
        category_path="home/insurance/vehicle",
        file_name="AAMI_Car_Certificate_of_Insurance_MPA167699547.pdf",
        summary_en="Motorcycle Insurance Certificate 2025-2026 for AAMI.",
        summary_zh="该摩托车保险单于2025年签发。",
        content_excerpt="[Page 1] Motorcycle Insurance ... your car is used on average ...",
    )
    assert "Motorcycle" not in en
    assert "摩托车" not in zh
    assert "Vehicle Insurance" in en
    assert "车辆保险" in zh
