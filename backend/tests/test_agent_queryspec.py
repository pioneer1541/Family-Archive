import pytest

from app.services.agent_queryspec import (
    build_query_spec_from_query,
    estimate_queryspec_confidence,
    prefilter_router_candidate_categories,
)

pytestmark = pytest.mark.no_db_reset


def test_queryspec_prefers_appliances_for_dishwasher_repair_ticket_query():
    spec = build_query_spec_from_query("洗碗机维修工单号和工程师手机号是多少？", planner_intent="entity_fact_lookup")
    assert spec["task_kind"] in {"fact_lookup", "status_check"}
    assert spec["subject_domain"] == "appliances"
    assert "work_order_no" in spec["target_slots"]
    assert "engineer_phone" in spec["target_slots"] or "contact_phone" in spec["target_slots"]


def test_queryspec_detects_howto_maintenance_interval():
    spec = build_query_spec_from_query("洗碗机的用户手册里说多久需要清洁过滤网？", planner_intent="search_semantic")
    assert spec["task_kind"] == "howto_lookup"
    assert spec["subject_domain"] == "appliances"
    assert "maintenance_interval" in spec["target_slots"]


def test_queryspec_detects_loan_status_derivation():
    spec = build_query_spec_from_query("贷款还有多少年还完？", planner_intent="search_semantic")
    assert spec["subject_domain"] == "home"
    assert spec["task_kind"] in {"fact_lookup", "status_check"}
    assert any(slot in spec["target_slots"] for slot in ["loan_term_years", "loan_maturity_date", "loan_start_date"])
    assert "compute_remaining_loan_years" in spec["derivations"]


def test_queryspec_treats_recent_internet_bill_amount_as_fact_lookup():
    spec = build_query_spec_from_query("最近的网费账单是多少？", planner_intent="search_semantic")
    assert spec["subject_domain"] == "bills"
    assert spec["task_kind"] == "fact_lookup"
    assert "bill_amount" in spec["target_slots"]
    assert any(str(x).startswith("finance/bills/internet") for x in spec.get("preferred_categories", []))


def test_queryspec_detects_network_provider_contact_slots():
    spec = build_query_spec_from_query("家里网络提供商的联系方式是什么？", planner_intent="search_semantic")
    assert spec["subject_domain"] == "bills"
    assert "vendor" in spec["target_slots"]
    assert any(slot in spec["target_slots"] for slot in ["contact_phone", "contact_email"])
    aliases = [str(x).lower() for x in (spec.get("subject_aliases") or [])]
    assert any(tok in aliases for tok in ["internet bill", "nbn", "superloop"])
    # strict_domain_filter must be True for fact_lookup with explicit internet service tokens
    assert spec["strict_domain_filter"] is True
    # generic aliases must not pollute the first 4 alias slots used by query_variant_node
    assert "bill" not in aliases[:4]
    assert "invoice" not in aliases[:4]
    assert "账单" not in aliases[:4]


def test_queryspec_internet_fact_lookup_no_generic_alias_pollution():
    """entity_fact_lookup (联系方式) with internet tokens → strict filter + no generic alias pollution."""
    spec = build_query_spec_from_query("家里网络提供商的联系方式是什么？", planner_intent="entity_fact_lookup")
    assert spec["subject_domain"] == "bills"
    assert spec["task_kind"] == "fact_lookup"
    assert spec["strict_domain_filter"] is True
    assert spec["preferred_categories"] == ["finance/bills/internet"]
    aliases = [str(x).lower() for x in (spec.get("subject_aliases") or [])]
    # internet-specific aliases must appear
    assert any(tok in aliases for tok in ["internet bill", "nbn", "superloop", "网络提供商"])
    # generic aliases must not occupy the first 4 slots (query_variant combined query)
    assert "bill" not in aliases[:4]
    assert "invoice" not in aliases[:4]
    assert "账单" not in aliases[:4]


def test_queryspec_keeps_monthly_bill_summary_as_aggregate_lookup():
    spec = build_query_spec_from_query("2月份的账单有哪些？一共多少钱？", planner_intent="search_semantic")
    assert spec["subject_domain"] == "bills"
    assert spec["task_kind"] == "aggregate_lookup"


def test_queryspec_keeps_recent_bill_list_as_list():
    spec = build_query_spec_from_query("最近有哪些账单需要处理", planner_intent="search_semantic")
    assert spec["subject_domain"] == "bills"
    assert spec["task_kind"] == "list"


def test_queryspec_confidence_penalizes_sparse_generic_spec():
    spec = {
        "task_kind": "fact_lookup",
        "subject_domain": "generic",
        "target_slots": [],
        "preferred_categories": ["finance/bills"],
    }
    conf = estimate_queryspec_confidence("家里网络提供商的联系方式是什么？", spec)
    assert conf["score"] < 0.65
    assert conf["signals_negative"]


def test_queryspec_confidence_scores_q1_spec_as_reasonable():
    spec = build_query_spec_from_query("最近的网费账单是多少？", planner_intent="search_semantic")
    conf = estimate_queryspec_confidence("最近的网费账单是多少？", spec)
    assert conf["score"] >= 0.6
    assert "subject_domain_non_generic" in conf["signals_positive"]


def test_prefilter_router_candidate_categories_prefers_domain_and_rule_categories():
    spec = {
        "subject_domain": "bills",
        "preferred_categories": ["finance/bills/internet", "finance/bills"],
    }
    cats = [
        "home/appliances",
        "finance/bills/electricity",
        "finance/bills/internet",
        "finance/bills/gas",
        "finance/bills",
        "home/pets",
    ]
    out = prefilter_router_candidate_categories(spec, cats, max_candidates=4)
    assert out[0] == "finance/bills/internet"
    assert any(c.startswith("finance/bills") for c in out)
