"""Agent V2 - Streaming Events

Event streaming support for progressive response display.
"""

import time
from typing import Any, AsyncIterator

from app.logging_utils import get_logger

logger = get_logger(__name__)


class AgentEvent:
    """Event emitted during agent execution."""

    def __init__(
        self,
        event_type: str,
        node: str,
        data: dict[str, Any],
        trace_id: str = "",
    ):
        self.event_type = event_type  # "start", "progress", "chunk", "end", "error"
        self.node = node
        self.data = data
        self.trace_id = trace_id
        self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "node": self.node,
            "data": self.data,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


async def stream_agent_execution(
    graph,
    initial_state: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> AsyncIterator[AgentEvent]:
    """Stream agent execution events.

    Yields events at each node start/end and for streaming content.

    Usage:
        async for event in stream_agent_execution(graph, state, config):
            yield event.to_dict()
    """
    trace_id = initial_state.get("trace_id", "unknown")

    # Emit start event
    yield AgentEvent(
        event_type="start",
        node="graph",
        data={"state_keys": list(initial_state.keys())},
        trace_id=trace_id,
    )

    try:
        # Run graph with streaming
        # Note: LangGraph doesn't natively support streaming events
        # We simulate by tracking node transitions
        current_node = "query_classifier"

        # Phase 4: QueryClassifier
        yield AgentEvent(
            event_type="start",
            node=current_node,
            data={"query": initial_state.get("req", {}).get("query", "")},
            trace_id=trace_id,
        )

        # Execute classifier
        from app.services.agent_v2.nodes.query_classifier import query_classifier_node

        state = dict(initial_state)
        classifier_result = await query_classifier_node(state)
        state.update(classifier_result)

        yield AgentEvent(
            event_type="end",
            node=current_node,
            data=classifier_result.get("classifier", {}),
            trace_id=trace_id,
        )

        # Check for chitchat short-circuit (Phase 3.2)
        if state.get("terminal") and state.get("terminal_reason") == "chitchat_complete":
            yield AgentEvent(
                event_type="chunk",
                node="chitchat",
                data={
                    "content": state.get("final_card_payload", {}).get("short_summary", {}),
                    "done": True,
                },
                trace_id=trace_id,
            )
            yield AgentEvent(
                event_type="end",
                node="graph",
                data={"reason": "chitchat_complete"},
                trace_id=trace_id,
            )
            return

        # Phase 4: Determine path and execute
        complexity = state.get("classifier", {}).get("complexity", "complex")

        if complexity == "simple":
            # Single-LLM path
            yield AgentEvent(
                event_type="start",
                node="unified_synthesize",
                data={"mode": "single_llm"},
                trace_id=trace_id,
            )

            # TODO: Implement streaming unified synthesizer
            from app.services.agent_v2.nodes.unified_synthesizer import unified_synthesizer_node

            result = await unified_synthesizer_node(state)
            state.update(result)

            yield AgentEvent(
                event_type="chunk",
                node="unified_synthesize",
                data={
                    "content": result.get("final_card_payload", {}).get("short_summary", {}),
                    "done": True,
                },
                trace_id=trace_id,
            )

            yield AgentEvent(
                event_type="end",
                node="unified_synthesize",
                data={"route": result.get("router", {}).get("route", "lookup")},
                trace_id=trace_id,
            )

        else:
            # Dual-LLM path
            # Router
            yield AgentEvent(
                event_type="start",
                node="router",
                data={"mode": "dual_llm"},
                trace_id=trace_id,
            )

            from app.services.agent_v2.nodes.router import router_node

            router_result = await router_node(state)
            state.update(router_result)

            yield AgentEvent(
                event_type="end",
                node="router",
                data={"route": router_result.get("route", "lookup")},
                trace_id=trace_id,
            )

            # Check for chitchat from router
            if router_result.get("route") == "chitchat":
                yield AgentEvent(
                    event_type="chunk",
                    node="chitchat",
                    data={
                        "content": {"en": "Hello!", "zh": "你好！"},
                        "done": True,
                    },
                    trace_id=trace_id,
                )
            else:
                # Retrieve
                yield AgentEvent(
                    event_type="start",
                    node="retrieve",
                    data={},
                    trace_id=trace_id,
                )

                from app.services.agent_v2.nodes.retriever import retriever_node

                retrieve_result = await retriever_node(state)
                state.update(retrieve_result)

                hit_count = len(retrieve_result.get("context_chunks", []))

                yield AgentEvent(
                    event_type="progress",
                    node="retrieve",
                    data={"hit_count": hit_count, "doc_count": len(set(c.get("doc_id") for c in retrieve_result.get("context_chunks", [])))},
                    trace_id=trace_id,
                )

                yield AgentEvent(
                    event_type="end",
                    node="retrieve",
                    data={"answerability": retrieve_result.get("answerability", "sufficient")},
                    trace_id=trace_id,
                )

                # Synthesize (TODO: streaming)
                yield AgentEvent(
                    event_type="start",
                    node="synthesize",
                    data={},
                    trace_id=trace_id,
                )

                from app.services.agent_v2.nodes.synthesizer import synthesizer_node

                synth_result = await synthesizer_node(state)
                state.update(synth_result)

                yield AgentEvent(
                    event_type="chunk",
                    node="synthesize",
                    data={
                        "content": synth_result.get("final_card_payload", {}).get("short_summary", {}),
                        "done": True,
                    },
                    trace_id=trace_id,
                )

                yield AgentEvent(
                    event_type="end",
                    node="synthesize",
                    data={},
                    trace_id=trace_id,
                )

        # Final end event
        yield AgentEvent(
            event_type="end",
            node="graph",
            data={
                "final_route": state.get("route", "unknown"),
                "terminal_reason": state.get("terminal_reason", "unknown"),
            },
            trace_id=trace_id,
        )

    except Exception as e:
        logger.error("stream_execution_error", extra={"trace_id": trace_id, "error": str(e)})
        yield AgentEvent(
            event_type="error",
            node="graph",
            data={"error": str(e), "error_type": type(e).__name__},
            trace_id=trace_id,
        )
