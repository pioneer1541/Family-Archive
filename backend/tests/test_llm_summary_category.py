from app.services import llm_summary


def test_classify_category_from_summary_uses_model_and_allowed_path(monkeypatch):
    seen: dict[str, str] = {}

    def _fake_call_json_result(_prompt, _payload, *, timeout_sec=None, model_name=None, retry_count=None, call_name=None, db=None):
        seen["model_name"] = str(model_name or "")
        return llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"finance/bills/electricity"}',
            parsed_json={"category_path": "finance/bills/electricity"},
            latency_ms=1,
            model=str(model_name or ""),
            timeout_sec=int(timeout_sec or 0),
            attempts=1,
        )

    monkeypatch.setattr(llm_summary, "_call_json_result", _fake_call_json_result)

    out = llm_summary.classify_category_from_summary(
        file_name="bill.pdf",
        source_type="mail",
        summary_en="Electricity bill with due date",
        summary_zh="电费账单，含到期日。",
    )
    assert out == ("Electricity Bills", "电费账单", "finance/bills/electricity")
    assert seen["model_name"] == llm_summary.settings.category_model


def test_classify_category_from_summary_rejects_disallowed_root(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"archive"}',
            parsed_json={"category_path": "archive"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.classify_category_from_summary(
        file_name="unknown.bin",
        source_type="file",
        summary_en="uncertain",
        summary_zh="不确定",
    )
    assert out == ("Archive Misc", "归档杂项", "archive/misc")


def test_classify_category_from_summary_rejects_unknown_path(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"email/inbox"}',
            parsed_json={"category_path": "email/inbox"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.classify_category_from_summary(
        file_name="mail.eml",
        source_type="mail",
        summary_en="mail text",
        summary_zh="邮件正文",
    )
    assert out == ("Archive Misc", "归档杂项", "archive/misc")


def test_classify_category_from_summary_rejects_non_leaf_path(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"finance/bills"}',
            parsed_json={"category_path": "finance/bills"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.classify_category_from_summary(
        file_name="nested.pdf",
        source_type="mail",
        summary_en="bill",
        summary_zh="账单",
    )
    assert out == ("Archive Misc", "归档杂项", "archive/misc")


def test_classify_category_from_summary_rejects_bill_without_payment_evidence(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"finance/bills/water"}',
            parsed_json={"category_path": "finance/bills/water"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.classify_category_from_summary(
        file_name="Rheem-CF-12-26-876A-874A-Series-TD.pdf",
        source_type="nas",
        summary_en="Technical layout with hot water outlet, cold water inlet, gas connection and power lead.",
        summary_zh="该文件是热水器连接示意图，包含热水出口、冷水入口、燃气连接和电源连接。",
        content_excerpt="HOT WATER OUTLET COLD WATER INLET GAS CONNECTION POWER LEAD CONNECTION",
    )
    assert out == ("Home Manuals", "家庭说明书", "home/manuals")


def test_classify_category_from_summary_keeps_bill_with_payment_evidence(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"finance/bills/water"}',
            parsed_json={"category_path": "finance/bills/water"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.classify_category_from_summary(
        file_name="invoice_202602.pdf",
        source_type="mail",
        summary_en="Water bill total due AUD $166.20, due date 2026-02-28.",
        summary_zh="水费账单应付金额澳币$166.20，到期日为2026-02-28。",
        content_excerpt="Tax Invoice Amount Due 166.20 AUD BPAY payment",
    )
    assert out == ("Water Bills", "水费账单", "finance/bills/water")


def test_classify_category_from_summary_redirects_solar_proposal_to_contracts(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"finance/bills/electricity"}',
            parsed_json={"category_path": "finance/bills/electricity"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.classify_category_from_summary(
        file_name="Signed_Solar_Proposal_for_YUN_XIE.pdf",
        source_type="nas",
        summary_en=(
            "Signed solar proposal agreement with deposit terms and total payable amount AUD 10,300. "
            "Customer may accept revised installation costs within 5 business days."
        ),
        summary_zh="签署的太阳能方案合同，包含定金、总价、施工条款与接受期限。",
        content_excerpt="SOLAR POWER OUTLET Proposal Agreement Deposit Required Offer valid until 22 Jun 2025",
    )
    assert out == ("Contracts", "合同文件", "legal/contracts")


def test_classify_category_from_summary_forces_vehicle_insurance_leaf(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"health/insurance"}',
            parsed_json={"category_path": "health/insurance"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.classify_category_from_summary(
        file_name="AAMI_Car_Policy_Account_MPA167699547.pdf",
        source_type="mail",
        summary_en="AAMI car policy account and certificate of insurance for vehicle cover renewal.",
        summary_zh="AAMI车辆保险保单账户及保险证明，包含保费和免赔额信息。",
        content_excerpt="Car Policy Account Certificate of Insurance motor vehicle rego",
    )
    assert out == ("Vehicle Insurance", "车辆保险", "home/insurance/vehicle")


def test_classify_category_from_summary_forces_health_private_insurance_leaf(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"home/insurance/vehicle"}',
            parsed_json={"category_path": "home/insurance/vehicle"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )

    out = llm_summary.classify_category_from_summary(
        file_name="Bronze_Plus_Simple_Hospital_750_Excess.pdf",
        source_type="mail",
        summary_en="Private health insurance cover for hospital and extras, with Medicare details.",
        summary_zh="私保住院与附加险保障，含Medicare相关信息。",
        content_excerpt="Hospital cover Extras private health insurance",
    )
    assert out == ("Private Health Insurance", "私保资料", "health/insurance/private")


def test_classify_category_from_summary_health_doc_with_negated_car_phrase_stays_health(monkeypatch):
    monkeypatch.setattr(
        llm_summary,
        "_call_json_result",
        lambda *_args, **_kwargs: llm_summary.LlmJsonCallResult(
            ok=True,
            error_type="",
            error_detail="",
            raw_text='{"category_path":"home/insurance/vehicle"}',
            parsed_json={"category_path": "home/insurance/vehicle"},
            latency_ms=1,
            model="qwen3:4b-instruct",
            timeout_sec=1,
            attempts=1,
        ),
    )
    out = llm_summary.classify_category_from_summary(
        file_name="Starter_Extras_VIC.pdf",
        source_type="mail",
        summary_en="This private health insurance cover does not include car or home insurance.",
        summary_zh="该计划为独立健康保险，不包含汽车或房屋保险。",
        content_excerpt="Hospital cover extras private health insurance",
    )
    assert out == ("Private Health Insurance", "私保资料", "health/insurance/private")
