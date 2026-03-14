"""Agent V2 Nodes - Retriever

Document retrieval and context building.
"""

import asyncio
from typing import Any

from app.logging_utils import get_logger
from app.schemas import SearchRequest
from app.services.agent_v2.state import AgentGraphState
from app.services.search import search_documents

logger = get_logger(__name__)


async def retriever_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Retriever node: fetch relevant documents and build context.
    
    Integrates with existing search_documents service.
    """
    req = state.get("req", {})
    query = req.get("query", "")
    trace_id = state.get("trace_id", "")
    
    # Get DB session from config
    db = config.get("configurable", {}).get("db") if config else None
    
    if not db:
        logger.error("retriever_no_db: trace_id=%s", trace_id)
        return {
            "context_chunks": [],
            "candidate_docs": [],
            "answerability": "insufficient",
        }
    
    try:
        # Build search request from state
        search_req = SearchRequest(
            query=query,
            top_k=req.get("top_k", 10),
            category_path=req.get("category_path"),
            tags_all=req.get("tags_all", []),
            tags_any=req.get("tags_any", []),
            ui_lang=req.get("ui_lang", "zh"),
            query_lang=req.get("query_lang", "auto"),
        )
        
        # Call existing search service in thread pool to avoid blocking
        loop = asyncio.get_running_loop()
        search_result = await loop.run_in_executor(None, search_documents, db, search_req)
        
        hits = search_result.hits if search_result else []
        
        logger.info(
            "retriever_success: trace_id=%s query=%s hits_count=%d",
            trace_id,
            query,
            len(hits),
        )
        
        # Convert hits to context chunks format
        context_chunks = []
        for hit in hits:
            context_chunks.append({
                "doc_id": hit.doc_id,
                "chunk_id": hit.chunk_id,
                "content": hit.text_snippet,
                "score": hit.score,
                "source": hit.source_type,
                "category": hit.category_path,
                "title": hit.title_en or hit.title_zh,
            })
        
        # Determine answerability based on hit count
        answerability = "sufficient" if len(hits) >= 3 else ("partial" if len(hits) >= 1 else "insufficient")
        
        return {
            "context_chunks": context_chunks,
            "candidate_docs": [
                {"id": h.doc_id, "title": h.title_en or h.title_zh, "score": h.score}
                for h in hits[:5]  # Top 5 for related docs
            ],
            "answerability": answerability,
        }
        
    except Exception as e:
        logger.error("retriever_failed: trace_id=%s query=%s error=%s", trace_id, query, str(e))
        return {
            "context_chunks": [],
            "candidate_docs": [],
            "answerability": "insufficient",
        }
