"""Agent V2 Nodes - Chitchat

Simple greeting and small talk responses without LLM calls.
"""

from typing import Any

from app.services.agent_v2.state import AgentGraphState


def chitchat_node(state: AgentGraphState) -> dict[str, Any]:
    """Chitchat node: generate simple greeting responses.
    
    No LLM call - uses templates for efficiency.
    """
    ui_lang = state.get("req", {}).get("ui_lang", "zh")
    
    # Simple bilingual responses
    if ui_lang == "zh":
        content = "你好！我是Family Vault助手，有什么可以帮您的吗？"
    else:
        content = "Hello! I'm your Family Vault assistant. How can I help you today?"
    
    return {
        "final_card_payload": {
            "title": "Family Vault",
            "content": content,
            "type": "chitchat",
        },
        "terminal": True,
        "terminal_reason": "chitchat_complete",
    }
