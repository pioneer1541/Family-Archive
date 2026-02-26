import pytest

from app.services.agent_graph_nodes import recovery_apply_node, recovery_plan_node
from app.services.agent_slots import judge_sufficiency

pytestmark = pytest.mark.no_db_reset


def test_judge_sufficiency_returns_partial_for_noncritical_missing_slots():
    spec = {
        "target_slots": ["monthly_payment", "loan_bank"],
        "subject_aliases": ["mortgage", "loan", "贷款"],
        "needs_presence_evidence": False,
        "needs_status_evidence": False,
    }
    slot_results = [
        {"slot": "monthly_payment", "value": "AUD 2300", "status": "found", "confidence": 0.9},
        {"slot": "loan_bank", "value": "", "status": "missing", "confidence": 0.0},
    ]
    judged = judge_sufficiency(
        query_spec=spec,
        slot_results=slot_results,
        derivations=[],
        context_chunks=[{"title_zh": "贷款合同", "title_en": "Loan Contract", "category_path": "legal/contracts", "text": "Monthly repayment AUD 2300"}],
        required_slots=["monthly_payment", "loan_bank"],
        critical_slots=["monthly_payment"],
    )
    assert judged["answerability"] in {"partial", "sufficient"}
    assert judged["critical_slot_coverage_ratio"] == 1.0
    assert isinstance(judged.get("partial_evidence_signals"), list)


def test_judge_sufficiency_allows_partial_for_status_query_without_status_evidence():
    spec = {
        "target_slots": ["claim_status"],
        "subject_aliases": ["insurance", "claim", "理赔"],
        "needs_presence_evidence": False,
        "needs_status_evidence": True,
    }
    slot_results = [
        {"slot": "provider", "value": "Bupa", "status": "found", "confidence": 0.8},
        {"slot": "status_evidence", "value": "", "status": "missing", "confidence": 0.0},
    ]
    judged = judge_sufficiency(
        query_spec=spec,
        slot_results=slot_results,
        derivations=[],
        context_chunks=[{"title_zh": "理赔材料", "title_en": "Claim Notes", "category_path": "insurance/claims", "text": "Bupa claim documents"}],
        required_slots=["claim_status", "status_evidence"],
        critical_slots=["status_evidence"],
    )
    assert judged["answerability"] == "partial"
    assert "presence_or_status_gate_partial_only" in judged.get("partial_evidence_signals", [])


def test_judge_sufficiency_returns_none_on_zero_hit_with_requirements():
    judged = judge_sufficiency(
        query_spec={"target_slots": ["expiry_date"], "subject_aliases": ["insurance"]},
        slot_results=[],
        derivations=[],
        context_chunks=[],
        required_slots=["expiry_date"],
        critical_slots=["expiry_date"],
    )
    assert judged["answerability"] == "none"
    assert "zero_hit_with_requirements" in judged.get("refusal_blockers", [])


def test_judge_sufficiency_returns_sufficient_when_derivation_succeeds():
    judged = judge_sufficiency(
        query_spec={"target_slots": ["vaccine_next_due"], "subject_aliases": ["Lucky"]},
        slot_results=[{"slot": "vaccine_next_due", "value": "", "status": "missing", "confidence": 0.0}],
        derivations=[{"name": "estimate_next_vaccine_due", "status": "derived", "value": "2026-05-01"}],
        context_chunks=[{"title_zh": "宠物疫苗记录", "title_en": "Pet Vaccine Record", "category_path": "pets/health", "text": "Lucky vaccine"}],
        required_slots=["vaccine_next_due"],
        critical_slots=["vaccine_next_due"],
    )
    assert judged["answerability"] == "sufficient"
    assert judged["critical_slot_coverage_ratio"] == 0.0


def test_recovery_plan_stops_when_loop_budget_exhausted_and_increments_loop_count():
    state = {
        "answerability": "insufficient",
        "loop_count": 2,
        "loop_budget": 2,
        "critical_missing_slots": ["policy_no"],
        "query_spec": {"strict_domain_filter": False},
        "candidate_hits": [{"chunk_id": "c1"}],
        "loop_progress_history": [
            {"slot_coverage_ratio": 0.0},
            {"slot_coverage_ratio": 0.2},
            {"slot_coverage_ratio": 0.2},
        ],
    }
    out = recovery_plan_node(state)
    assert out["terminal"] is True
    assert out["terminal_reason"] == "loop_budget_exhausted"

    state2 = {
        "answerability": "insufficient",
        "loop_count": 0,
        "loop_budget": 2,
        "critical_missing_slots": ["policy_no"],
        "query_spec": {"strict_domain_filter": False},
        "candidate_hits": [{"chunk_id": "c1"}],
        "loop_progress_history": [{"slot_coverage_ratio": 0.0}],
        "recovery_plan": {"next_loop": 1, "actions": ["expand_query_variants"]},
    }
    out2 = recovery_apply_node(state2)
    assert out2["loop_count"] == 1
    assert out2["loop_progress_history"][-1]["recovery_actions"] == ["expand_query_variants"]


def test_judge_sufficiency_howto_steps_without_interval_returns_partial():
    judged = judge_sufficiency(
        query_spec={"task_kind": "howto_lookup", "target_slots": ["maintenance_interval"], "subject_aliases": ["water tank", "maintenance"]},
        slot_results=[
            {"slot": "maintenance_interval", "value": "", "status": "missing", "confidence": 0.0},
            {"slot": "maintenance_steps", "value": "Check the tank filter monthly and clean debris if blocked.", "status": "found", "confidence": 0.88},
        ],
        derivations=[],
        context_chunks=[{"title_en": "Water Tank Maintenance", "category_path": "home/maintenance", "text": "Check the tank filter monthly and clean debris if blocked."}],
        required_slots=["maintenance_interval"],
        critical_slots=["maintenance_interval"],
    )
    assert judged["answerability"] == "partial"
    assert "howto_steps_without_interval" in judged.get("partial_evidence_signals", [])


def test_judge_sufficiency_howto_low_quality_steps_downgrades_sufficient():
    judged = judge_sufficiency(
        query_spec={"task_kind": "howto_lookup", "target_slots": ["maintenance_interval"], "subject_aliases": ["water tank"]},
        slot_results=[
            {"slot": "maintenance_interval", "value": "every 1 month", "status": "found", "confidence": 0.8},
            {"slot": "maintenance_steps", "value": "[Page 12] Page I 11 PLUMBING A copy of your Plumbing Industry Com", "status": "found", "confidence": 0.7},
        ],
        derivations=[],
        context_chunks=[{"title_en": "Maintenance", "category_path": "home/maintenance", "text": "water tank maintenance"}],
        required_slots=["maintenance_interval"],
        critical_slots=["maintenance_interval"],
    )
    assert judged["answerability"] == "partial"
    assert "howto_steps_low_quality" in judged.get("partial_evidence_signals", [])
