"""Agent V2 Nodes

Node implementations for the agent graph.
"""

from app.services.agent_v2.nodes.router import router_node
from app.services.agent_v2.nodes.chitchat import chitchat_node
from app.services.agent_v2.nodes.retriever import retriever_node
from app.services.agent_v2.nodes.synthesizer import synthesizer_node

__all__ = [
    "router_node",
    "chitchat_node", 
    "retriever_node",
    "synthesizer_node",
]

# Expose simplified names for graph definition
router = router_node
chitchat = chitchat_node
retrieve = retriever_node
synthesize = synthesizer_node
