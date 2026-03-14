"""Agent V2 Nodes

Node implementations for the agent graph.
"""

from app.services.agent_v2.nodes.router import router_node
from app.services.agent_v2.nodes.chitchat import chitchat_node
from app.services.agent_v2.nodes.retriever import retriever_node
from app.services.agent_v2.nodes.synthesizer import synthesizer_node
from app.services.agent_v2.nodes.query_classifier import query_classifier_node
from app.services.agent_v2.nodes.unified_synthesizer import unified_synthesizer_node

__all__ = [
    "router_node",
    "chitchat_node",
    "retriever_node",
    "synthesizer_node",
    "query_classifier_node",
    "unified_synthesizer_node",
]

# Expose simplified names for graph definition
router = router_node
chitchat = chitchat_node
retrieve = retriever_node
synthesize = synthesizer_node
query_classifier = query_classifier_node
unified_synthesize = unified_synthesizer_node
