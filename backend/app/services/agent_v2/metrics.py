"""Agent V2 Metrics

Performance and usage metrics collection.
"""

import time
from typing import Any

from app.logging_utils import get_logger

logger = get_logger(__name__)


class AgentV2Metrics:
    """Metrics collector for Agent V2."""
    
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.start_time = time.perf_counter()
        self.node_latencies: dict[str, float] = {}
        self.current_node: str | None = None
        self.node_start: float | None = None
    
    def start_node(self, node_name: str):
        """Mark start of a graph node."""
        self.current_node = node_name
        self.node_start = time.perf_counter()
        logger.debug("agent_v2_node_start: trace_id=%s node=%s", self.trace_id, node_name)
    
    def end_node(self, node_name: str | None = None):
        """Mark end of current graph node."""
        if self.node_start is None or self.current_node is None:
            return
        
        node = node_name or self.current_node
        elapsed = time.perf_counter() - self.node_start
        self.node_latencies[node] = elapsed
        
        logger.info(
            "agent_v2_node_latency: trace_id=%s node=%s latency_ms=%.2f",
            self.trace_id,
            node,
            elapsed * 1000
        )
        
        self.current_node = None
        self.node_start = None
    
    def record(self, key: str, value: Any):
        """Record a metric value."""
        logger.info("agent_v2_metric: trace_id=%s key=%s value=%s", self.trace_id, key, value)
    
    def finish(self, success: bool = True) -> dict[str, Any]:
        """Finish metrics collection and return summary."""
        total_elapsed = time.perf_counter() - self.start_time
        
        summary = {
            "trace_id": self.trace_id,
            "total_latency_ms": total_elapsed * 1000,
            "node_latencies_ms": {k: v * 1000 for k, v in self.node_latencies.items()},
            "success": success,
        }
        
        logger.info(
            "agent_v2_complete: trace_id=%s total_latency_ms=%.2f nodes=%s success=%s",
            self.trace_id,
            summary["total_latency_ms"],
            list(self.node_latencies.keys()),
            success
        )
        
        return summary


# Global metrics store (for aggregation)
_metrics_store: list[dict[str, Any]] = []


def record_metrics(metrics: dict[str, Any]):
    """Record metrics to global store."""
    _metrics_store.append(metrics)
    # Limit store size
    if len(_metrics_store) > 10000:
        _metrics_store.pop(0)


def get_metrics_summary() -> dict[str, Any]:
    """Get aggregated metrics summary."""
    if not _metrics_store:
        return {"count": 0}
    
    total_latency = sum(m.get("total_latency_ms", 0) for m in _metrics_store)
    success_count = sum(1 for m in _metrics_store if m.get("success"))
    
    return {
        "count": len(_metrics_store),
        "avg_latency_ms": total_latency / len(_metrics_store),
        "success_rate": success_count / len(_metrics_store),
    }
