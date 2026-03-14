"""Agent V2 Nodes - Retriever

Document retrieval and context building.
"""

from typing import Any

import structlog

from app import crud
from app.schemas import SearchRequest
from app.services.agent_v2.state import AgentGraphState
from app.services.search import search_documents

logger = structlog.get_logger()


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
        logger.error("retriever_no_db", trace_id=trace_id)
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
            doc_scope=req.get("doc_scope"),
            category_filter=req.get("category_filter"),
        )
        
        # Call existing search service
        search_result = search_documents(db, search_req)
        
        hits = search_result.hits if search_result else []
        
        logger.info(
            "retriever_success",
            trace_id=trace_id,
            query=query,
            hits_count=len(hits),
        )
        
        # Convert hits to context chunks format
        context_chunks = []
        for hit in hits:
            context_chunks.append({
                "doc_id": hit.doc_id,
                "chunk_index": hit.chunk_index,
                "content": hit.content,
                "score": hit.score,
                "source": hit.source,
                "category": hit.category,
            })
        
        # Determine answerability based on hit count
        answerability = "sufficient" if len(hits) >= 3 else ("partial" if len(hits) >= 1 else "insufficient")
        
        return {
            "context_chunks": context_chunks,
            "candidate_docs": [
                {"id": h.doc_id, "title": h.title, "score": h.score}
                for h in hits[:5]  # Top 5 for related docs
            ],
            "answerability": answerability,
        }
        
    except Exception as e:
        logger.error("retriever_failed", trace_id=trace_id, query=query, error=str(e))
        return {
            "context_chunks": [],
            "candidate_docs": [],
            "answerability": "insufficient",
        }
