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


async def query_classifier_node(state: AgentGraphState) -> dict[str, Any]:
    """
    Classify query complexity to determine processing strategy.

    State updates:
    - classifier.complexity: "simple" | "complex"
    - classifier.confidence: float
    - classifier.method: "rule" | "llm"

    Routing:
    - "simple" -> unified_synthesizer_node (1 LLM call)
    - "complex" -> router_node (2 LLM calls)
    """
    req = state.get("req", {})
    query = req.get("query", "") if isinstance(req, dict) else getattr(req, "query", "")
    trace_id = state.get("trace_id", "unknown")

    logger.info(
        "query_classifier_start",
        extra={"trace_id": trace_id, "query": query[:100]}
    )

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
