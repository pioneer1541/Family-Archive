"""Agent V2 Phase 2 - A/B Testing Metrics

Collect and compare metrics between single-LLM and dual-LLM modes.
"""

import time
from typing import Any
from dataclasses import dataclass, field

from app.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class ABTestMetrics:
    """Metrics for a single query execution."""

    # Identification
    trace_id: str
    complexity: str  # "simple" | "complex"
    method: str  # "rule" | "llm" | "ab_test"

    # Timing
    start_time: float = field(default_factory=time.time)
    llm_calls: int = 0
    total_duration_ms: float = 0.0

    # Quality indicators
    success: bool = True
    error_type: str = ""
    answer_found: bool = False

    def finish(self, success: bool = True, error: str = "") -> dict[str, Any]:
        """Finalize metrics and return summary."""
        self.total_duration_ms = (time.time() - self.start_time) * 1000
        self.success = success
        self.error_type = error

        return {
            "trace_id": self.trace_id,
            "complexity": self.complexity,
            "method": self.method,
            "llm_calls": self.llm_calls,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "success": self.success,
            "error_type": self.error_type,
            "answer_found": self.answer_found,
        }


class ABTestMetricsCollector:
    """Collect and aggregate A/B test metrics.

    Usage:
        collector = ABTestMetricsCollector()

        # Single-LLM mode
        metrics = collector.start(trace_id, "simple", "ab_test")
        # ... execute query ...
        summary = collector.finish(metrics, llm_calls=1)

        # Dual-LLM mode
        metrics = collector.start(trace_id, "complex", "ab_test")
        # ... execute query ...
        summary = collector.finish(metrics, llm_calls=2)
    """

    def __init__(self):
        self._metrics: list[dict[str, Any]] = []

    def start(self, trace_id: str, complexity: str, method: str) -> ABTestMetrics:
        """Start tracking metrics for a query."""
        return ABTestMetrics(
            trace_id=trace_id,
            complexity=complexity,
            method=method,
        )

    def finish(
        self,
        metrics: ABTestMetrics,
        llm_calls: int = 0,
        success: bool = True,
        error: str = "",
        answer_found: bool = False,
    ) -> dict[str, Any]:
        """Finish tracking and store metrics."""
        metrics.llm_calls = llm_calls
        metrics.answer_found = answer_found
        summary = metrics.finish(success=success, error=error)
        self._metrics.append(summary)

        # Log for analysis
        logger.info(
            "ab_test_metrics",
            extra={
                "trace_id": metrics.trace_id,
                "complexity": metrics.complexity,
                "llm_calls": llm_calls,
                "duration_ms": summary["total_duration_ms"],
                "success": success,
            }
        )

        return summary

    def get_comparison_report(self) -> dict[str, Any]:
        """Generate A/B test comparison report.

        Returns:
            Aggregated metrics comparing single vs dual LLM mode
        """
        single_metrics = [m for m in self._metrics if m["llm_calls"] == 1]
        dual_metrics = [m for m in self._metrics if m["llm_calls"] == 2]

        def _avg(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        def _success_rate(metrics: list[dict]) -> float:
            if not metrics:
                return 0.0
            successful = sum(1 for m in metrics if m["success"])
            return successful / len(metrics) * 100

        return {
            "single_llm": {
                "count": len(single_metrics),
                "avg_duration_ms": round(_avg([m["total_duration_ms"] for m in single_metrics]), 2),
                "success_rate": round(_success_rate(single_metrics), 2),
                "avg_llm_calls": 1.0,
            },
            "dual_llm": {
                "count": len(dual_metrics),
                "avg_duration_ms": round(_avg([m["total_duration_ms"] for m in dual_metrics]), 2),
                "success_rate": round(_success_rate(dual_metrics), 2),
                "avg_llm_calls": 2.0,
            },
            "cost_saving_estimate": self._estimate_cost_saving(),
        }

    def _estimate_cost_saving(self) -> dict[str, float]:
        """Estimate cost savings from single-LLM mode."""
        single_count = sum(1 for m in self._metrics if m["llm_calls"] == 1)
        dual_count = sum(1 for m in self._metrics if m["llm_calls"] == 2)
        total = single_count + dual_count

        if total == 0:
            return {"percent": 0.0, "single_ratio": 0.0}

        # Assuming equal cost per LLM call
        actual_calls = single_count * 1 + dual_count * 2
        baseline_calls = total * 2  # If all were dual-LLM
        saved_calls = baseline_calls - actual_calls

        return {
            "percent": round(saved_calls / baseline_calls * 100, 2),
            "single_ratio": round(single_count / total * 100, 2),
        }

    def reset(self) -> None:
        """Clear all collected metrics."""
        self._metrics.clear()


# Global collector instance
_ab_test_collector: ABTestMetricsCollector | None = None


def get_ab_test_collector() -> ABTestMetricsCollector:
    """Get or create global A/B test metrics collector."""
    global _ab_test_collector
    if _ab_test_collector is None:
        _ab_test_collector = ABTestMetricsCollector()
    return _ab_test_collector
