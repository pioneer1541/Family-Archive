import pytest

from app.schemas import DetailCoverageStats, PlannerDecision, ResultCardSource
from app.services import agent_graph_nodes as agn

pytestmark = pytest.mark.no_db_reset


def test_graph_slot_fallback_partial_is_not_generic_template(monkeypatch):
    class _LegacyStub:
        @staticmethod
        def _default_actions(_planner):
            return []

    monkeypatch.setattr(agn, "_legacy_agent_module", lambda: _LegacyStub)

    card = agn._build_graph_slot_fallback_card(
        req=type("R", (), {"ui_lang": "zh"})(),
        planner=PlannerDecision(
            intent="entity_fact_lookup",
            confidence=0.9,
            doc_scope={},
            actions=[],
            fallback="none",
            ui_lang="zh",
            query_lang="zh",
        ),
        query_spec={"target_slots": ["contact_phone"]},
        slot_results=[
            {
                "slot": "contact_phone",
                "label_zh": "联系电话",
                "label_en": "Contact Phone",
                "value": "1300 123 456",
                "status": "found",
                "confidence": 0.9,
            }
        ],
        derivations=[],
        detail_sections=[],
        context_chunks=[
            {
                "doc_id": "doc-1",
                "chunk_id": "c1",
                "title_zh": "网络账单",
                "title_en": "Internet Bill",
                "text": "Support contact 1300 123 456 available for billing enquiries.",
            }
        ],
        sources=[ResultCardSource(doc_id="doc-1", chunk_id="c1", label="网络账单")],
        missing_slots=["contact_email"],
        critical_missing=[],
        cov_stats=DetailCoverageStats(docs_scanned=1, docs_matched=1, fields_filled=1),
        answer_posture="partial",
    )

    assert "我在资料库中找到相关证据。摘要如下" not in (card.short_summary.zh or "")
    joined = "\n".join([kp.zh or "" for kp in card.key_points])
    assert "已确认" in joined
    assert "尚缺证据" in joined
    assert "1300 123 456" in joined
    assert card.insufficient_evidence is False
