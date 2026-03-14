"""Agent V2 Feature Flags

Production rollout configuration.
"""

import hashlib
import os
from typing import Literal


class AgentV2Config:
    """Configuration for Agent V2 rollout."""
    
    @classmethod
    def _get_env_bool(cls, key: str, default: str = "false") -> bool:
        """Read boolean env var at runtime."""
        return os.environ.get(key, default).lower() == "true"
    
    @classmethod
    def _get_env_int(cls, key: str, default: str = "0") -> int:
        """Read int env var at runtime."""
        return int(os.environ.get(key, default))
    
    @classmethod
    def _get_env_str(cls, key: str, default: str = "") -> str:
        """Read string env var at runtime."""
        return os.environ.get(key, default)
    
    @classmethod
    def should_use_v2(cls, trace_id: str | None = None) -> bool:
        """Determine if request should use V2 agent.
        
        Args:
            trace_id: Optional trace ID for consistent routing
        
        Returns:
            True if V2 should be used
        """
        # Read env vars at runtime (not at class definition)
        enabled = cls._get_env_bool("AGENT_V2_ENABLED", "false")  # Default to OFF for safety
        force_version = cls._get_env_str("AGENT_V2_FORCE", "auto")
        rollout_percent = cls._get_env_int("AGENT_V2_ROLLOUT_PERCENT", "0")
        
        if not enabled:
            return False
        
        if force_version == "v1":
            return False
        if force_version == "v2":
            return True
        
        # Percentage-based rollout
        if rollout_percent >= 100:
            return True
        if rollout_percent <= 0:
            return False
        
        # Use trace_id for consistent routing
        if trace_id:
            # Stable hash using hashlib (not Python's randomized hash())
            hash_val = int(hashlib.md5(trace_id.encode()).hexdigest(), 16) % 100
            return hash_val < rollout_percent
        
        # Default to V1 when trace_id is missing and partial rollout
        return False
    
    @classmethod
    def is_debug_enabled(cls) -> bool:
        """Check if debug logging is enabled."""
        return cls._get_env_bool("AGENT_V2_DEBUG", "false")
    
    @classmethod
    def is_metrics_enabled(cls) -> bool:
        """Check if metrics collection is enabled."""
        return cls._get_env_bool("AGENT_V2_METRICS", "true")

    # Phase 2: Single-LLM Mode A/B Testing
    @classmethod
    def is_single_llm_mode_enabled(cls) -> bool:
        """Check if single-LLM mode (Phase 2) is enabled."""
        return cls._get_env_bool("AGENT_V2_SINGLE_LLM_ENABLED", "true")

    @classmethod
    def get_single_llm_traffic_percent(cls) -> int:
        """Get percentage of traffic to route through single-LLM mode.

        Returns:
            Percentage (0-100) of queries to process with single-LLM mode.
            Remaining traffic uses dual-LLM mode.
        """
        return cls._get_env_int("AGENT_V2_SINGLE_LLM_TRAFFIC_PERCENT", "50")

    @classmethod
    def should_use_single_llm_mode(cls, trace_id: str | None = None) -> bool:
        """Determine if query should use single-LLM mode.

        Args:
            trace_id: Optional trace ID for consistent routing

        Returns:
            True if single-LLM mode should be used
        """
        # Single-LLM mode requires V2 to be enabled
        if not cls.is_single_llm_mode_enabled():
            return False

        traffic_percent = cls.get_single_llm_traffic_percent()

        if traffic_percent >= 100:
            return True
        if traffic_percent <= 0:
            return False

        # Use trace_id for consistent routing
        if trace_id:
            hash_val = int(hashlib.md5(f"single:{trace_id}".encode()).hexdigest(), 16) % 100
            return hash_val < traffic_percent

        # Default when trace_id is missing
        return traffic_percent >= 50
