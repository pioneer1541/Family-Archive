import pytest

from app.schemas import PlannerDecision
from app.services import evidence

pytestmark = pytest.mark.no_db_reset


def _planner(required: list[str]) -> PlannerDecision:
    return PlannerDecision(
        intent="entity_fact_lookup",
        confidence=0.8,
        doc_scope={},
        actions=[],
        fallback="none",
        ui_lang="zh",
        query_lang="zh",
        required_evidence_fields=required,
    )


def test_required_evidence_fields_is_query_driven():
    fields = evidence._required_evidence_fields("这张账单多少钱，到期日期是什么？", _planner(["contact", "date"]))
    assert "amount" in fields
    assert "date" in fields
    assert "contact" not in fields


def test_build_evidence_map_extracts_and_keeps_doc_association():
    chunks = [
        {"doc_id": "d1", "chunk_id": "c1", "text": "Amount due AUD 109.00, due on 2026-03-21"},
        {"doc_id": "d2", "chunk_id": "c2", "text": "Amount due AUD 88.50"},
        {"doc_id": "d3", "chunk_id": "c3", "text": "Amount due AUD 70.00"},
    ]

    out = evidence._build_evidence_map(["amount"], chunks)

    assert "amount" in out
    assert len(out["amount"]) == 2
    assert out["amount"][0]["doc_id"] == "d1"
    assert out["amount"][1]["doc_id"] == "d2"


def test_evidence_query_and_coverage_helpers():
    assert evidence._evidence_match("date", "due date is 04/11/2024") is True
    assert evidence._evidence_match("contact", "email: a@b.com") is True
    assert evidence._evidence_match("amount", "no money info") is False

    ratio, missing = evidence._coverage_from_map(
        ["amount", "date"],
        {
            "amount": [{"doc_id": "d1", "chunk_id": "c1", "evidence_text": "AUD 9.00"}],
            "date": [],
        },
    )
    assert ratio == 0.5
    assert missing == ["date"]


def test_infer_answerability_with_requirements_and_refusal_flag():
    assert (
        evidence._infer_answerability(
            hit_count=0,
            coverage_ratio=0.0,
            refusal_candidate=True,
            has_requirements=True,
        )
        == "none"
    )
    assert (
        evidence._infer_answerability(
            hit_count=1,
            coverage_ratio=0.2,
            refusal_candidate=False,
            has_requirements=True,
        )
        == "insufficient"
    )
    assert (
        evidence._infer_answerability(
            hit_count=2,
            coverage_ratio=1.0,
            refusal_candidate=False,
            has_requirements=True,
        )
        == "sufficient"
    )


def test_presence_and_subject_coverage_checks():
    chunks = [{"text": "我们有太阳能，已经安装完成", "title_zh": "太阳能安装", "category_path": "home/energy"}]
    assert evidence._presence_evidence_sufficient("我们家里有没有太阳能", chunks) is True
    assert evidence._subject_coverage_ok(["太阳能"], chunks) is True
    assert evidence._target_field_coverage_ok(["coverage_scope"], chunks) is False


def test_contains_specific_claim():
    assert evidence._contains_specific_claim("See https://example.com") is True
    assert evidence._contains_specific_claim("AUD 20.00 due") is True
    assert evidence._contains_specific_claim("general statement only") is False
