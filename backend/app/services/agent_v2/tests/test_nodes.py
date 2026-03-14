"""Agent V2 Tests

Unit tests for agent_v2 nodes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent_v2.state import AgentGraphState
from app.services.agent_v2.nodes.router import router_node, _is_chitchat_rule_based
from app.services.agent_v2.nodes.chitchat import chitchat_node


class TestRouterNode:
    """Test router node."""
    
    def test_is_chitchat_rule_based(self):
        """Test rule-based chitchat detection."""
        assert _is_chitchat_rule_based("你好") == True
        assert _is_chitchat_rule_based("hello") == True
        assert _is_chitchat_rule_based("thanks") == True
        assert _is_chitchat_rule_based("查询我的保险") == False
        assert _is_chitchat_rule_based("This is a long query about insurance") == False
    
    @pytest.mark.asyncio
    async def test_router_chitchat_rule(self):
        """Test router returns chitchat for greeting."""
        state: AgentGraphState = {
            "req": {"query": "你好", "ui_lang": "zh"},
            "trace_id": "test-123",
        }
        
        result = await router_node(state)
        
        assert result["route"] == "chitchat"
        assert result["route_reason"] == "rule_based"
        assert result["router"]["confidence"] == 1.0
    
    @pytest.mark.asyncio
    async def test_router_fallback_on_error(self):
        """Test router falls back to lookup on error."""
        state: AgentGraphState = {
            "req": {"query": "some complex query", "ui_lang": "zh"},
            "trace_id": "test-456",
        }
        
        # Mock the LLM call to fail
        with patch("app.services.agent_v2.nodes.router.call_router_llm", side_effect=Exception("LLM error")):
            result = await router_node(state)
        
        assert result["route"] == "lookup"
        assert "error_fallback" in result["route_reason"]


class TestChitchatNode:
    """Test chitchat node."""
    
    def test_chitchat_zh(self):
        """Test chitchat in Chinese."""
        state: AgentGraphState = {
            "req": {"query": "你好", "ui_lang": "zh"},
            "trace_id": "test-789",
        }
        
        result = chitchat_node(state)
        
        assert result["terminal"] == True
        assert result["terminal_reason"] == "chitchat_complete"
        assert "你好" in result["final_card_payload"]["content"]
    
    def test_chitchat_en(self):
        """Test chitchat in English."""
        state: AgentGraphState = {
            "req": {"query": "hello", "ui_lang": "en"},
            "trace_id": "test-abc",
        }
        
        result = chitchat_node(state)
        
        assert result["terminal"] == True
        assert "Hello" in result["final_card_payload"]["content"]
