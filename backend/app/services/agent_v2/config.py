"""Agent V2 Feature Flags

Production rollout configuration.
"""

import os
from typing import Literal


class AgentV2Config:
    """Configuration for Agent V2 rollout."""
    
    # Feature flag: enable new agent
    ENABLED: bool = os.environ.get("AGENT_V2_ENABLED", "true").lower() == "true"
    
    # Rollout percentage (0-100)
    ROLLOUT_PERCENT: int = int(os.environ.get("AGENT_V2_ROLLOUT_PERCENT", "100"))
    
    # Force specific implementation
    FORCE_VERSION: Literal["auto", "v1", "v2"] = os.environ.get("AGENT_V2_FORCE", "auto")
    
    # Enable detailed graph logging
    DEBUG_GRAPH: bool = os.environ.get("AGENT_V2_DEBUG", "false").lower() == "true"
    
    # Metrics collection
    COLLECT_METRICS: bool = os.environ.get("AGENT_V2_METRICS", "true").lower() == "true"
    
    @classmethod
    def should_use_v2(cls, trace_id: str | None = None) -> bool:
        """Determine if request should use V2 agent.
        
        Args:
            trace_id: Optional trace ID for consistent routing
        
        Returns:
            True if V2 should be used
        """
        if not cls.ENABLED:
            return False
        
        if cls.FORCE_VERSION == "v1":
            return False
        if cls.FORCE_VERSION == "v2":
            return True
        
        # Percentage-based rollout
        if cls.ROLLOUT_PERCENT >= 100:
            return True
        if cls.ROLLOUT_PERCENT <= 0:
            return False
        
        # Use trace_id for consistent routing
        if trace_id:
            # Simple hash-based routing
            hash_val = hash(trace_id) % 100
            return hash_val < cls.ROLLOUT_PERCENT
        
        # Default to V2 for auto mode
        return True
