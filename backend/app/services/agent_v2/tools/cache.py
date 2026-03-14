"""Agent V2 Tools - Cache

Simple cache abstraction.
"""

from typing import Any, Optional


# In-memory cache (will be replaced with Redis/SQLite in production)
_cache: dict[str, tuple[Any, float]] = {}


async def get_cache(key: str) -> Optional[Any]:
    """Get value from cache if not expired."""
    import time
    if key in _cache:
        value, expires_at = _cache[key]
        if time.time() < expires_at:
            return value
        del _cache[key]
    return None


async def set_cache(key: str, value: Any, ttl: int = 300) -> None:
    """Set value in cache with TTL (seconds)."""
    import time
    _cache[key] = (value, time.time() + ttl)
