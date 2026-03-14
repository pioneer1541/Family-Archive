"""Agent V2 Nodes - Chitchat

Simple greeting and small talk responses without LLM calls.
"""

import random
from typing import Any

from app.logging_utils import get_logger
from app.services.agent_v2.state import AgentGraphState

logger = get_logger(__name__)

# Chitchat response templates
_CHITCHAT_TEMPLATES = {
    "zh": {
        "greeting": [
            "你好！我是Family Vault助手，有什么可以帮您的吗？",
            "您好！很高兴为您服务，请问有什么需要帮助的？",
            "你好呀！我是您的家庭档案助手，随时为您解答问题。",
        ],
        "thanks": [
            "不客气！很高兴能帮到您。",
            "不用谢，有问题随时找我。",
            "这是应该的，还有其他可以帮您的吗？",
        ],
        "bye": [
            "再见！祝您有愉快的一天。",
            "拜拜！有问题随时回来找我。",
            "好的，再见！期待下次为您服务。",
        ],
        "ok": [
            "好的，明白了。",
            "收到！",
            "没问题。",
        ],
    },
    "en": {
        "greeting": [
            "Hello! I'm your Family Vault assistant. How can I help you today?",
            "Hi there! Ready to help you with your family documents.",
            "Hello! What can I do for you today?",
        ],
        "thanks": [
            "You're welcome! Glad I could help.",
            "No problem at all. Feel free to ask anytime.",
            "My pleasure! Anything else I can help with?",
        ],
        "bye": [
            "Goodbye! Have a wonderful day.",
            "Bye! Come back anytime you need help.",
            "See you later! Take care.",
        ],
        "ok": [
            "Got it.",
            "Okay, understood.",
            "Sure thing.",
        ],
    },
}


def _classify_chitchat_intent(query: str) -> str:
    """Classify the chitchat intent from query."""
    q = query.lower().strip()
    
    # Thanks patterns
    if any(word in q for word in ["谢谢", "感谢", "thanks", "thank you", "thx"]):
        return "thanks"
    
    # Goodbye patterns
    if any(word in q for word in ["再见", "拜拜", "bye", "goodbye", "see you"]):
        return "bye"
    
    # OK patterns (substring match for flexibility)
    if any(word in q for word in ["ok", "好的", "嗯", "okay"]):
        return "ok"
    
    # Default to greeting
    return "greeting"


def chitchat_node(state: AgentGraphState) -> dict[str, Any]:
    """Chitchat node: generate contextual greeting responses.
    
    No LLM call - uses templates for efficiency.
    Selects appropriate response based on query intent.
    """
    req = state.get("req", {})
    ui_lang = req.get("ui_lang", "zh")
    query = req.get("query", "")
    trace_id = state.get("trace_id", "")
    
    # Classify intent
    intent = _classify_chitchat_intent(query)
    
    # Get templates for language
    lang_templates = _CHITCHAT_TEMPLATES.get(ui_lang, _CHITCHAT_TEMPLATES["en"])
    intent_templates = lang_templates.get(intent, lang_templates["greeting"])
    
    # Randomly select response for variety
    content = random.choice(intent_templates)
    
    logger.info("chitchat_response: trace_id=%s intent=%s lang=%s", trace_id, intent, ui_lang)
    
    # Build short_summary with proper language separation
    short_summary = {"en": "", "zh": ""}
    short_summary[ui_lang] = content
    
    return {
        "final_card_payload": {
            "title": "Family Vault",
            "short_summary": short_summary,
            "content": content,
            "type": "chitchat",
            "intent": intent,
        },
        "terminal": True,
        "terminal_reason": "chitchat_complete",
    }
