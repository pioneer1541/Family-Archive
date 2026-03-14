"""Agent V2 Integration Tests

Test the full graph execution.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas import AgentExecuteRequest
from app.services.agent_v2 import execute


class TestAgentV2Execute:
    """Test the main execute function."""
    
    @pytest.mark.asyncio
    async def test_execute_chitchat(self):
        """Test executing a chitchat query."""
        req = AgentExecuteRequest(
            query="你好",
            ui_lang="zh",
        )
        
        # Execute
        response = await execute(req)
        
        # Verify response structure
        assert response.card is not None
        assert response.trace_id is not None
        assert response.trace_id.startswith("agt-")
    
    @pytest.mark.asyncio
    async def test_execute_lookup(self):
        """Test executing a lookup query (with mocked retrieval)."""
        req = AgentExecuteRequest(
            query="查询我的保险",
            ui_lang="zh",
        )
        
        # Mock the search_documents to return empty results
        with patch("app.services.agent_v2.nodes.retriever.search_documents") as mock_search:
            mock_search.return_value = MagicMock(hits=[])
            
            # Execute
            response = await execute(req)
        
        # Verify response
        assert response.card is not None
        assert response.trace_id is not None
