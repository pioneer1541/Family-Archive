"""Agent V2 Comparison Tests

Compare new LangGraph implementation with legacy execute_agent_v2.
"""

import anyio
from unittest.mock import MagicMock, patch

from app.schemas import AgentExecuteRequest
from app.services.agent import execute_agent_v2
from app.services.agent_v2 import execute as new_execute


class TestAgentV2Comparison:
    """Compare old vs new agent implementations."""
    
    def test_chitchat_comparison(self):
        """Compare chitchat responses between old and new."""
        req = AgentExecuteRequest(
            query="你好",
            ui_lang="zh",
        )
        
        # Mock DB for old implementation
        mock_db = MagicMock()
        
        # Run old implementation
        old_result = execute_agent_v2(mock_db, req)
        
        # Run new implementation (async) with force flag for testing
        new_result = anyio.run(new_execute, req, mock_db, None, True)
        
        # Both should return chitchat
        assert old_result.executor_stats.route == "chitchat"
        assert new_result.executor_stats.route == "chitchat" or new_result.card.get("type") == "chitchat"
        
        # Both should have trace_id
        assert old_result.trace_id is not None
        assert new_result.trace_id is not None
        
        # Both should have planner
        assert old_result.planner is not None
        assert new_result.planner is not None
    
    def test_lookup_with_context_comparison(self):
        """Compare lookup with context between old and new."""
        req = AgentExecuteRequest(
            query="insurance policy coverage",
            ui_lang="en",
            query_lang="en",
        )
        
        mock_db = MagicMock()
        
        # Mock search results for both
        mock_hit = MagicMock()
        mock_hit.doc_id = "doc-1"
        mock_hit.chunk_id = "chunk-1"
        mock_hit.text_snippet = "Insurance covers health and dental."
        mock_hit.score = 0.95
        mock_hit.source_type = "pdf"
        mock_hit.category_path = "insurance"
        mock_hit.title_en = "Insurance Policy"
        mock_hit.title_zh = "保险政策"
        mock_hit.updated_at = None  # Will be set to utcnow in retriever
        
        mock_result = MagicMock()
        mock_result.hits = [mock_hit]
        
        with patch("app.services.search.search_documents", return_value=mock_result):
            # Run old implementation
            old_result = execute_agent_v2(mock_db, req)
            
            # Run new implementation with force flag for testing
            new_result = anyio.run(new_execute, req, mock_db, None, True)
        
        # Both should have trace_id
        assert old_result.trace_id is not None
        assert new_result.trace_id is not None
        
        # Both should have response card
        assert old_result.card is not None
        assert new_result.card is not None
    
    def test_lookup_no_context_comparison(self):
        """Compare lookup without context (insufficient) between old and new."""
        req = AgentExecuteRequest(
            query="xyz nonexistent query",
            ui_lang="en",
            query_lang="en",
        )
        
        mock_db = MagicMock()
        
        # Mock empty search results
        mock_result = MagicMock()
        mock_result.hits = []
        
        with patch("app.services.search.search_documents", return_value=mock_result):
            # Run old implementation
            old_result = execute_agent_v2(mock_db, req)
            
            # Run new implementation with force flag for testing
            new_result = anyio.run(new_execute, req, mock_db, None, True)
        
        # Both should handle insufficient context gracefully
        assert old_result.card is not None
        assert new_result.card is not None
        assert old_result.trace_id is not None
        assert new_result.trace_id is not None
