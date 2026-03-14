"""Agent V2 Nodes - Query Classifier

Determines query complexity to decide between single-LLM (simple) vs dual-LLM (complex) mode.
"""

from typing import Any

from app.logging_utils import get_logger
from app.services.agent_v2.state import AgentGraphState

logger = get_logger(__name__)

# Simple query patterns - rule-based fast path
_SIMPLE_PATTERNS = {
    # Greetings and chitchat
    "你好", "您好", "嗨", "hello", "hi", "hey",
    "早安", "晚安", "morning", "evening",
    "谢谢", "感谢", "thanks", "thank you",
    "再见", "拜拜", "bye", "goodbye",
    "好的", "嗯", "ok", "okay", "是的", "没错",

    # Simple lookups with clear keywords
    "在哪里", "在哪", "where is", "where are",
    "是什么", "what is", "what are",
    "什么时候", "when is", "when are",
    "多少钱", "how much",
}

# Complex query indicators - require dual-LLM mode
_COMPLEX_INDICATORS = {
    # Calculation/analysis keywords
    "计算", "平均", "总和", "统计", "对比", "比较",
    "calculate", "compute", "average", "sum", "total", "compare", "contrast",

    # Complex extraction
    "提取", "分析", "总结", "归纳",
    "extract", "analyze", "summarize", "derive",

    # Multi-step reasoning
    "为什么", "原因", "理由",
    "why", "reason", "cause",

    # Complex conditions
    "如果", "假设", "条件",
    "if", "assuming", "condition",

    # Document-level operations
    "所有", "全部", "列表", "列出",
    "all", "every", "list all", "show all",
}


def _classify_by_rules(query: str) -> tuple[str, float] | None:
    """
    Rule-based classification for fast path.

    Returns:
        ("simple", confidence) if clearly simple
        ("complex", confidence) if clearly complex
        None if uncertain (need LLM classification)
    """
    q = query.lower().strip()

    # Empty or very short = simple (chitchat)
    if len(q) <= 10:
        # Check if it's a greeting/chitchat pattern
        for pattern in _SIMPLE_PATTERNS:
            if pattern in q:
                return ("simple", 0.95)
        # Very short but not matching patterns - likely simple
        return ("simple", 0.8)

    # Check for complex indicators
    complex_score = 0
    for indicator in _COMPLEX_INDICATORS:
        if indicator in q:
            complex_score += 1

    if complex_score >= 2:
        return ("complex", 0.9)
    if complex_score >= 1:
        return ("complex", 0.75)

    # Check for simple patterns in longer queries
    simple_matches = sum(1 for p in _SIMPLE_PATTERNS if p in q)
    if simple_matches >= 1 and len(q) < 50:
        return ("simple", 0.7)

    # Uncertain - need LLM
    return None


CLASSIFIER_PROMPT = """You are a query complexity classifier for an AI document assistant.

Analyze the user's query and classify it as either "simple" or "complex".

**Simple queries** (can be answered with 1 LLM call):
- Direct lookups: "Where is my passport?", "What is the insurance number?"
- Greetings: "Hello", "Thanks"
- Single fact retrieval from documents
- No calculation or analysis needed

**Complex queries** (need 2 LLM calls - router + synthesizer):
- Calculations: "Calculate average cost", "Sum all expenses"
- Comparisons: "Compare these two contracts"
- Analysis: "What are the risks in this document?"
- Multi-step reasoning required
- "Extract all key terms" from a document

Respond in JSON format:
{{
    "complexity": "simple" | "complex",
    "confidence": 0.0-1.0,
    "reason": "brief explanation"
}}

User query: {query}
"""


async def _classify_with_llm(query: str) -> tuple[str, float]:
    """
    Use lightweight LLM to classify query complexity.

    Returns:
        (complexity, confidence) where complexity is "simple" or "complex"
    """
    from app.services.agent_v2.tools.llm import call_classifier_llm

    prompt = CLASSIFIER_PROMPT.format(query=query)

    try:
        result = await call_classifier_llm(prompt)

        complexity = result.get("complexity", "complex")  # Default to safe (complex)
        confidence = float(result.get("confidence", 0.5))

        # Validate
        if complexity not in ("simple", "complex"):
            complexity = "complex"

        return (complexity, confidence)

    except Exception as exc:
        logger.warning(
            "query_classifier_llm_failed",
            extra={"query": query[:50], "error": str(exc)}
        )
        # Fail safe - assume complex
        return ("complex", 0.5)


# Chitchat patterns - quick detection before any LLM calls
_CHITCHAT_PATTERNS = {
    "你好", "您好", "嗨", "hello", "hi", "hey",
    "早安", "晚安", "morning", "evening",
    "谢谢", "感谢", "thanks", "thank you",
    "再见", "拜拜", "bye", "goodbye",
    "好的", "嗯", "ok", "okay", "是的", "没错",
}

# Simple chitchat responses - no LLM needed
_CHITCHAT_RESPONSES = {
    "zh": {
        "你好": "你好！有什么可以帮助您的吗？",
        "您好": "您好！请问有什么需要帮助的？",
        "谢谢": "不客气！很高兴能帮到你。",
        "再见": "再见！有需要随时找我。",
        "好的": "好的，还有其他问题吗？",
        "早安": "早安！祝您今天愉快！",
        "晚安": "晚安！好梦！",
    },
    "en": {
        "hello": "Hello! How can I help you today?",
        "hi": "Hi there! What can I do for you?",
        "thanks": "You're welcome! Glad I could help.",
        "thank you": "You're welcome! Happy to assist.",
        "bye": "Goodbye! Feel free to come back anytime.",
        "ok": "OK! Anything else?",
        "okay": "Okay! Let me know if you need more help.",
    }
}


def _is_chitchat_quick(query: str) -> bool:
    """Quick chitchat detection - zero LLM cost."""
    q = query.lower().strip()
    return len(q) <= 15 and any(p in q for p in _CHITCHAT_PATTERNS)


def _generate_chitchat_response(query: str, ui_lang: str) -> dict:
    """Generate chitchat response without LLM."""
    q = query.lower().strip()
    lang = ui_lang if ui_lang in ("zh", "en") else "en"

    # Find matching response
    content = "Hello! How can I help you?"  # Default
    for pattern, response in _CHITCHAT_RESPONSES.get(lang, {}).items():
        if pattern in q:
            content = response
            break

    return {
        "title": "Family Vault",
        "short_summary": {
            "en": content if lang == "en" else "Hello! How can I help you?",
            "zh": content if lang == "zh" else "你好！有什么可以帮助您的吗？",
        },
        "key_points": [],
        "type": "chitchat",
    }


async def query_classifier_node(state: AgentGraphState) -> dict[str, Any]:
    """
    Classify query complexity to determine processing strategy.

    State updates:
    - classifier.complexity: "simple" | "complex"
    - classifier.confidence: float
    - classifier.method: "rule" | "llm" | "ab_test" | "chitchat"

    Routing:
    - "simple" -> unified_synthesizer_node (1 LLM call)
    - "complex" -> router_node (2 LLM calls)
    - "chitchat" -> direct response (0 LLM calls)

    A/B Testing:
    - When AGENT_V2_SINGLE_LLM_TRAFFIC_PERCENT is set, uses traffic-based routing
    - This allows comparing single vs dual LLM mode on identical queries
    """
    from app.services.agent_v2.config import AgentV2Config

    req = state.get("req", {})
    query = req.get("query", "") if isinstance(req, dict) else getattr(req, "query", "")
    ui_lang = req.get("ui_lang", "zh") if isinstance(req, dict) else getattr(req, "ui_lang", "zh")
    trace_id = state.get("trace_id", "unknown")

    logger.info(
        "query_classifier_start",
        extra={"trace_id": trace_id, "query": query[:100]}
    )

    # Phase 3.2: Chitchat short-circuit - zero LLM cost
    if _is_chitchat_quick(query):
        logger.info(
            "query_classifier_chitchat_shortcircuit",
            extra={"trace_id": trace_id, "query": query[:50]}
        )
        # Return chitchat classification - will route directly to chitchat_node
        return {
            "classifier": {
                "complexity": "simple",
                "confidence": 1.0,
                "method": "chitchat",
            },
            "route": "chitchat",
            "route_reason": "chitchat_shortcircuit",
            "terminal": True,
            "terminal_reason": "chitchat_complete",
            "final_card_payload": _generate_chitchat_response(query, ui_lang),
        }

    # Phase 2 A/B Testing: Check if we should force mode based on traffic split
    # This overrides the normal classification for A/B testing purposes
    if AgentV2Config.is_single_llm_mode_enabled():
        traffic_percent = AgentV2Config.get_single_llm_traffic_percent()
        # Only apply A/B override when not 0% or 100% (partial rollout)
        if 0 < traffic_percent < 100:
            use_single = AgentV2Config.should_use_single_llm_mode(trace_id)
            complexity = "simple" if use_single else "complex"
            logger.info(
                "query_classifier_ab_test_override",
                extra={
                    "trace_id": trace_id,
                    "complexity": complexity,
                    "traffic_percent": traffic_percent,
                    "method": "ab_test",
                }
            )
            return {
                "classifier": {
                    "complexity": complexity,
                    "confidence": 1.0,  # A/B test has deterministic routing
                    "method": "ab_test",
                }
            }

    # Try rule-based classification first
    rule_result = _classify_by_rules(query)

    if rule_result:
        complexity, confidence = rule_result
        method = "rule"
        logger.info(
            "query_classifier_rule_result",
            extra={
                "trace_id": trace_id,
                "complexity": complexity,
                "confidence": confidence,
            }
        )
    else:
        # Uncertain - use LLM
        complexity, confidence = await _classify_with_llm(query)
        method = "llm"
        logger.info(
            "query_classifier_llm_result",
            extra={
                "trace_id": trace_id,
                "complexity": complexity,
                "confidence": confidence,
            }
        )

    return {
        "classifier": {
            "complexity": complexity,
            "confidence": confidence,
            "method": method,
        }
    }
