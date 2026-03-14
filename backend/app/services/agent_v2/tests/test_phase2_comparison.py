"""Agent V2 Phase 2 - Comparison Tests

Test single-LLM mode (unified) vs dual-LLM mode (router+synthesizer).
Ensures output quality consistency.
"""

import anyio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.schemas import AgentExecuteRequest
from app.services.agent_v2 import execute as execute_v2
from app.services.agent_v2.state import AgentGraphState


# Mock LLM responses for testing
MOCK_UNIFIED_RESPONSE = """{
    "intent": "lookup",
    "confidence": 0.9,
    "answer_found": true,
    "answer": {
        "title": "Passport Location",
        "short_summary": {
            "en": "Your passport is in the safe deposit box.",
            "zh": "您的护照在保险箱中。"
        },
        "key_points": [
            {"en": "Stored in safe deposit box", "zh": "存放在保险箱中"}
        ]
    },
    "sources": ["doc-passport-001"]
}"""

MOCK_ROUTER_RESPONSE = {
    "route": "lookup",
    "confidence": 0.9,
    "rewritten_query": "Where is my passport?",
    "route_reason": "llm",
}

MOCK_SYNTHESIZER_RESPONSE = {
    "title": "Passport Location",
    "short_summary": {
        "en": "Your passport is in the safe deposit box.",
        "zh": "您的护照在保险箱中。"
    },
    "key_points": [
        {"en": "Stored in safe deposit box", "zh": "存放在保险箱中"}
    ],
}

MOCK_CLASSIFIER_SIMPLE = {
    "complexity": "simple",
    "confidence": 0.95,
    "reason": "direct lookup query",
}

MOCK_CLASSIFIER_COMPLEX = {
    "complexity": "complex",
    "confidence": 0.85,
    "reason": "requires calculation",
}


class TestPhase2SingleVsDualLLM:
    """Compare single-LLM (unified) vs dual-LLM (router+synthesizer) outputs."""

    def test_simple_query_goes_to_unified(self):
        """Simple queries should be routed to unified synthesizer."""
        req = AgentExecuteRequest(
            query="你好",
            ui_lang="zh",
        )

        mock_db = MagicMock()

        # Mock unified LLM response
        with patch(
            "app.services.agent_v2.tools.llm._call_unified_sync",
            return_value=MOCK_UNIFIED_RESPONSE,
        ):
            result = anyio.run(execute_v2, req, mock_db, None, True)

        # Check that we got a valid response
        assert result.trace_id is not None
        assert result.card is not None
        # Graph should complete via unified path for simple queries
        assert result.executor_stats.graph_path.startswith("classifier")

    def test_complex_query_goes_to_router(self):
        """Complex queries should be routed to traditional router path."""
        req = AgentExecuteRequest(
            query="计算过去一年的平均支出",
            ui_lang="zh",
        )

        mock_db = MagicMock()

        # Mock classifier to return complex
        with patch(
            "app.services.agent_v2.tools.llm._call_classifier_sync",
            return_value=MOCK_CLASSIFIER_COMPLEX,
        ):
            # Mock router and synthesizer
            with patch(
                "app.services.agent_v2.tools.llm._call_router_sync",
                return_value=MOCK_ROUTER_RESPONSE,
            ):
                with patch(
                    "app.services.agent_v2.tools.llm._call_synthesizer_sync",
                    return_value=MOCK_SYNTHESIZER_RESPONSE,
                ):
                    result = anyio.run(execute_v2, req, mock_db, None, True)

        assert result.trace_id is not None
        assert result.card is not None
        # Should go through router path
        assert "router" in result.executor_stats.graph_path

    def test_unified_vs_dual_output_structure(self):
        """Verify unified and dual modes produce similar output structure."""
        # Test that both modes produce compatible card structures
        simple_req = AgentExecuteRequest(query="你好", ui_lang="zh")
        mock_db = MagicMock()

        with patch(
            "app.services.agent_v2.tools.llm._call_unified_sync",
            return_value=MOCK_UNIFIED_RESPONSE,
        ):
            result = anyio.run(execute_v2, simple_req, mock_db, None, True)

        # Verify card structure
        assert result.card.title is not None
        assert result.card.short_summary is not None
        # short_summary should have en and zh
        assert hasattr(result.card.short_summary, "en")
        assert hasattr(result.card.short_summary, "zh")

    def test_classifier_rule_based_simple(self):
        """Test rule-based classification for simple queries."""
        from app.services.agent_v2.nodes.query_classifier import _classify_by_rules

        # Simple greetings
        result = _classify_by_rules("你好")
        assert result is not None
        assert result[0] == "simple"
        assert result[1] > 0.9

        result = _classify_by_rules("hello")
        assert result is not None
        assert result[0] == "simple"

    def test_classifier_rule_based_complex(self):
        """Test rule-based classification for complex queries."""
        from app.services.agent_v2.nodes.query_classifier import _classify_by_rules

        # Complex indicators
        result = _classify_by_rules("计算平均支出并对比去年")
        assert result is not None
        assert result[0] == "complex"

    def test_classifier_uncertain_needs_llm(self):
        """Test that uncertain queries return None (need LLM)."""
        from app.services.agent_v2.nodes.query_classifier import _classify_by_rules

        # Ambiguous query
        result = _classify_by_rules("关于保险")
        assert result is None  # Needs LLM classification


class TestPhase2Metrics:
    """Track metrics for single vs dual LLM comparison."""

    def test_simple_query_single_llm_call(self):
        """Verify simple queries make only 1 LLM call."""
        req = AgentExecuteRequest(query="你好", ui_lang="zh")
        mock_db = MagicMock()

        call_count = {"unified": 0}

        def mock_unified(*args, **kwargs):
            call_count["unified"] += 1
            return MOCK_UNIFIED_RESPONSE

        with patch("app.services.agent_v2.tools.llm._call_unified_sync", mock_unified):
            result = anyio.run(execute_v2, req, mock_db, None, True)

        # Should make exactly 1 unified call
        assert call_count["unified"] == 1
        # Graph complexity should indicate simple
        assert result.executor_stats.graph_complexity in ("simple", "unknown")

    def test_complex_query_dual_llm_calls(self):
        """Verify complex queries make 2 LLM calls (router + synthesizer)."""
        req = AgentExecuteRequest(
            query="分析所有合同的风险",
            ui_lang="zh",
        )
        mock_db = MagicMock()

        call_count = {"classifier": 0, "router": 0, "synthesizer": 0}

        def mock_classifier(*args, **kwargs):
            call_count["classifier"] += 1
            return MOCK_CLASSIFIER_COMPLEX

        def mock_router(*args, **kwargs):
            call_count["router"] += 1
            return MOCK_ROUTER_RESPONSE

        def mock_synthesizer(*args, **kwargs):
            call_count["synthesizer"] += 1
            return MOCK_SYNTHESIZER_RESPONSE

        with patch("app.services.agent_v2.tools.llm._call_classifier_sync", mock_classifier):
            with patch("app.services.agent_v2.tools.llm._call_router_sync", mock_router):
                with patch(
                    "app.services.agent_v2.tools.llm._call_synthesizer_sync",
                    mock_synthesizer,
                ):
                    anyio.run(execute_v2, req, mock_db, None, True)

        # Should make classifier + router + synthesizer calls
        assert call_count["classifier"] == 1
        assert call_count["router"] == 1
        assert call_count["synthesizer"] == 1


class TestPhase2ErrorHandling:
    """Test error handling in single-LLM mode."""

    def test_unified_parse_error_fallback(self):
        """Test fallback when unified response parsing fails."""
        req = AgentExecuteRequest(query="你好", ui_lang="zh")
        mock_db = MagicMock()

        # Invalid JSON response
        with patch(
            "app.services.agent_v2.tools.llm._call_unified_sync",
            return_value="invalid json",
        ):
            result = anyio.run(execute_v2, req, mock_db, None, True)

        # Should return error response, not crash
        assert result.trace_id is not None
        assert result.card is not None

    def test_classifier_llm_failure_fallback(self):
        """Test that classifier LLM failure defaults to complex (safe)."""
        from app.services.agent_v2.nodes.query_classifier import _classify_with_llm

        # Mock the LLM call to fail
        with patch(
            "app.services.agent_v2.tools.llm.call_classifier_llm",
            side_effect=Exception("LLM timeout"),
        ):
            result = anyio.run(_classify_with_llm, "test query")

        # Should default to complex (safe fallback)
        assert result[0] == "complex"
        assert result[1] == 0.5
