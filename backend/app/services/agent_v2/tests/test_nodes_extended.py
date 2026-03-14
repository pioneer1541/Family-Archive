"""Agent V2 Tests - Extended

Extended unit tests for agent_v2 nodes (M2).
"""

import anyio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent_v2.state import AgentGraphState
from app.services.agent_v2.nodes.retriever import retriever_node
from app.services.agent_v2.nodes.synthesizer import synthesizer_node


class TestRetrieverNode:
    """Test retriever node with mocked search."""
    
    def test_retriever_no_db(self):
        """Test retriever returns insufficient when no db provided."""
        state: AgentGraphState = {
            "req": {"query": "test query", "ui_lang": "zh"},
            "trace_id": "test-123",
        }
        
        result = anyio.run(retriever_node, state)
        
        assert result["answerability"] == "insufficient"
        assert result["context_chunks"] == []
        assert result["candidate_docs"] == []
    
    def test_retriever_with_hits(self):
        """Test retriever with search hits."""
        state: AgentGraphState = {
            "req": {"query": "insurance", "ui_lang": "zh", "top_k": 5},
            "trace_id": "test-456",
        }
        
        # Mock search result
        mock_hit = MagicMock()
        mock_hit.doc_id = "doc-1"
        mock_hit.chunk_index = 0
        mock_hit.content = "Insurance policy content"
        mock_hit.score = 0.95
        mock_hit.source = "test.pdf"
        mock_hit.category = "insurance"
        mock_hit.title = "Test Insurance Doc"
        
        mock_result = MagicMock()
        mock_result.hits = [mock_hit] * 5  # 5 hits
        
        with patch("app.services.agent_v2.nodes.retriever.search_documents", return_value=mock_result):
            # Mock db session
            mock_db = MagicMock()
            config = {"configurable": {"db": mock_db}}
            result = anyio.run(retriever_node, state, config)
        
        assert result["answerability"] == "sufficient"
        assert len(result["context_chunks"]) == 5
        assert len(result["candidate_docs"]) == 5
        assert result["context_chunks"][0]["doc_id"] == "doc-1"
    
    def test_retriever_low_hits(self):
        """Test retriever with few hits returns partial answerability."""
        state: AgentGraphState = {
            "req": {"query": "rare query", "ui_lang": "zh"},
            "trace_id": "test-789",
        }
        
        mock_hit = MagicMock()
        mock_hit.doc_id = "doc-1"
        mock_hit.chunk_index = 0
        mock_hit.content = "Content"
        mock_hit.score = 0.8
        mock_hit.source = "test.pdf"
        mock_hit.category = "other"
        mock_hit.title = "Test Doc"
        
        mock_result = MagicMock()
        mock_result.hits = [mock_hit]  # Only 1 hit
        
        with patch("app.services.agent_v2.nodes.retriever.search_documents", return_value=mock_result):
            mock_db = MagicMock()
            config = {"configurable": {"db": mock_db}}
            result = anyio.run(retriever_node, state, config)
        
        assert result["answerability"] == "partial"
        assert len(result["context_chunks"]) == 1


class TestSynthesizerNode:
    """Test synthesizer node."""
    
    def test_synthesizer_no_context(self):
        """Test synthesizer returns fallback when no context."""
        state: AgentGraphState = {
            "req": {"query": "test", "ui_lang": "zh"},
            "trace_id": "test-abc",
            "context_chunks": [],
            "router": {"route": "lookup"},
        }
        
        result = anyio.run(synthesizer_node, state)
        
        assert result["terminal"] == True
        assert "没有找到相关信息" in result["final_card_payload"]["short_summary"]["zh"]
    
    def test_synthesizer_with_context(self):
        """Test synthesizer with context chunks."""
        state: AgentGraphState = {
            "req": {"query": "What is my insurance?", "ui_lang": "en"},
            "trace_id": "test-def",
            "context_chunks": [
                {
                    "doc_id": "doc-1",
                    "chunk_index": 0,
                    "content": "Your insurance policy covers health and dental.",
                    "score": 0.95,
                    "source": "insurance.pdf",
                    "category": "insurance",
                }
            ],
            "router": {"route": "lookup", "confidence": 0.9},
            "answerability": "sufficient",
        }
        
        # Mock the LLM call
        mock_response = {
            "title": "Insurance Information",
            "short_summary": {
                "en": "Your insurance covers health and dental.",
                "zh": "您的保险涵盖健康和牙科。"
            },
            "key_points": [
                {"en": "Health coverage included", "zh": "包含健康保险"}
            ]
        }
        
        async def mock_llm(*args, **kwargs):
            return mock_response
        
        with patch("app.services.agent_v2.nodes.synthesizer.call_synthesizer_llm", mock_llm):
            result = anyio.run(synthesizer_node, state)
        
        assert result["terminal"] == True
        assert result["terminal_reason"] == "answer_complete"
        assert result["final_card_payload"]["title"] == "Insurance Information"
