"""
Runtime-configurable settings: DB > env var > config.py default.

Priority:
  1. app_settings DB row
  2. env var
  3. config.py default

An in-memory cache with a 60-second TTL avoids per-request DB reads.
Call invalidate_runtime_cache() after writing to app_settings.
"""

import json
import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Registry: key → (env_var_name, default_value)
# ---------------------------------------------------------------------------
_RUNTIME_CONFIGURABLE: dict[str, tuple[str, str]] = {
    # LLM models
    "planner_model": ("FAMILY_VAULT_PLANNER_MODEL", "qwen3:1.7b"),
    "synthesizer_model": ("FAMILY_VAULT_SYNTHESIZER_MODEL", "qwen3:4b-instruct"),
    "embed_model": ("FAMILY_VAULT_EMBED_MODEL", "qwen3-embedding:0.6b"),
    "summary_model": ("FAMILY_VAULT_SUMMARY_MODEL", "qwen3:4b-instruct"),
    "category_model": ("FAMILY_VAULT_CATEGORY_MODEL", "qwen3:4b-instruct"),
    "friendly_name_model": ("FAMILY_VAULT_FRIENDLY_NAME_MODEL", "lfm2:latest"),
    "vl_extract_model": ("FAMILY_VAULT_VL_EXTRACT_MODEL", "qwen3-vl:2b"),
    # Timeouts (seconds)
    "summary_timeout_page_sec": ("FAMILY_VAULT_SUMMARY_TIMEOUT_PAGE_SEC", "90"),
    "summary_timeout_section_sec": ("FAMILY_VAULT_SUMMARY_TIMEOUT_SECTION_SEC", "120"),
    "summary_timeout_final_sec": ("FAMILY_VAULT_SUMMARY_TIMEOUT_FINAL_SEC", "120"),
    "agent_synth_timeout_sec": ("FAMILY_VAULT_AGENT_SYNTH_TIMEOUT_SEC", "25"),
    # NAS
    "source_type": ("FAMILY_VAULT_SOURCE_TYPE", "local"),
    "local_source_dir": ("FAMILY_VAULT_LOCAL_SOURCE_DIR", ""),
    "nas_host": ("FAMILY_VAULT_NAS_HOST", ""),
    "nas_path": ("FAMILY_VAULT_NAS_PATH", ""),
    "nas_auto_scan_enabled": ("FAMILY_VAULT_NAS_AUTO_SCAN_ENABLED", "0"),
    "nas_scan_interval_sec": ("FAMILY_VAULT_NAS_SCAN_INTERVAL_SEC", "900"),
    "nas_default_source_dir": ("FAMILY_VAULT_NAS_DEFAULT_SOURCE_DIR", ""),
    # Mail
    "mail_poll_enabled": ("FAMILY_VAULT_MAIL_POLL_ENABLED", "0"),
    "mail_poll_interval_sec": ("FAMILY_VAULT_MAIL_POLL_INTERVAL_SEC", "300"),
    "mail_query": ("FAMILY_VAULT_MAIL_QUERY", "has:attachment newer_than:30d"),
    "mail_attachment_subdir": (
        "FAMILY_VAULT_MAIL_ATTACHMENT_SUBDIR",
        "email_attachments",
    ),
    # Ollama URL (deployment-time; env var only — DB can override for convenience)
    "ollama_base_url": (
        "FAMILY_VAULT_OLLAMA_BASE_URL",
        "http://host.docker.internal:11434",
    ),
    # User-defined tagging keywords (stored as JSON objects {"terms": {...}})
    "person_keywords": ("", '{"terms":{}}'),
    "pet_keywords": ("", '{"terms":{}}'),
    "location_keywords": ("", '{"terms":{}}'),
}

# ---------------------------------------------------------------------------
# Metadata for the settings UI
# ---------------------------------------------------------------------------
SETTING_META: dict[str, dict[str, Any]] = {
    "planner_model": {
        "type": "model",
        "category": "llm",
        "label_zh": "规划模型",
        "label_en": "Planner Model",
    },
    "synthesizer_model": {
        "type": "model",
        "category": "llm",
        "label_zh": "合成模型",
        "label_en": "Synthesizer Model",
    },
    "embed_model": {
        "type": "model",
        "category": "llm",
        "label_zh": "嵌入模型",
        "label_en": "Embedding Model",
    },
    "summary_model": {
        "type": "model",
        "category": "llm",
        "label_zh": "摘要生成模型",
        "label_en": "Summary Model",
    },
    "category_model": {
        "type": "model",
        "category": "llm",
        "label_zh": "分类模型",
        "label_en": "Category Model",
    },
    "friendly_name_model": {
        "type": "model",
        "category": "llm",
        "label_zh": "友好标题模型",
        "label_en": "Friendly Title Model",
    },
    "vl_extract_model": {
        "type": "model",
        "category": "llm",
        "label_zh": "图像识别模型",
        "label_en": "Vision Model",
    },
    "summary_timeout_page_sec": {
        "type": "int",
        "category": "timeout",
        "label_zh": "摘要页超时(s)",
        "label_en": "Summary Page Timeout (s)",
    },
    "summary_timeout_section_sec": {
        "type": "int",
        "category": "timeout",
        "label_zh": "摘要章节超时(s)",
        "label_en": "Summary Section Timeout (s)",
    },
    "summary_timeout_final_sec": {
        "type": "int",
        "category": "timeout",
        "label_zh": "摘要合并超时(s)",
        "label_en": "Summary Final Timeout (s)",
    },
    "agent_synth_timeout_sec": {
        "type": "int",
        "category": "timeout",
        "label_zh": "AI问答超时(s)",
        "label_en": "Agent Synth Timeout (s)",
    },
    "nas_auto_scan_enabled": {
        "type": "bool",
        "category": "nas",
        "label_zh": "自动扫描NAS",
        "label_en": "NAS Auto Scan",
    },
    "source_type": {
        "type": "string",
        "category": "nas",
        "label_zh": "源类型",
        "label_en": "Source Type",
    },
    "local_source_dir": {
        "type": "path",
        "category": "nas",
        "label_zh": "本地源目录",
        "label_en": "Local Source Directory",
    },
    "nas_host": {
        "type": "string",
        "category": "nas",
        "label_zh": "NAS 主机地址",
        "label_en": "NAS Host",
    },
    "nas_path": {
        "type": "path",
        "category": "nas",
        "label_zh": "NAS 共享路径",
        "label_en": "NAS Share Path",
    },
    "nas_scan_interval_sec": {
        "type": "int",
        "category": "nas",
        "label_zh": "扫描间隔(s)",
        "label_en": "Scan Interval (s)",
    },
    "nas_default_source_dir": {
        "type": "path",
        "category": "nas",
        "label_zh": "NAS源目录",
        "label_en": "NAS Source Directory",
    },
    "mail_poll_enabled": {
        "type": "bool",
        "category": "mail",
        "label_zh": "启用邮件轮询",
        "label_en": "Mail Poll Enabled",
    },
    "mail_poll_interval_sec": {
        "type": "int",
        "category": "mail",
        "label_zh": "轮询间隔(s)",
        "label_en": "Poll Interval (s)",
    },
    "mail_query": {
        "type": "string",
        "category": "mail",
        "label_zh": "Gmail查询表达式",
        "label_en": "Gmail Query",
    },
    "mail_attachment_subdir": {
        "type": "string",
        "category": "mail",
        "label_zh": "邮件附件子目录",
        "label_en": "Mail Attachment Subdirectory",
    },
    "ollama_base_url": {
        "type": "string",
        "category": "advanced",
        "label_zh": "Ollama地址",
        "label_en": "Ollama Base URL",
    },
    "person_keywords": {
        "type": "json",
        "category": "keywords",
        "label_zh": "家庭成员名",
        "label_en": "Person Names",
    },
    "pet_keywords": {
        "type": "json",
        "category": "keywords",
        "label_zh": "宠物名",
        "label_en": "Pet Names",
    },
    "location_keywords": {
        "type": "json",
        "category": "keywords",
        "label_zh": "地址关键字",
        "label_en": "Location Keywords",
    },
}

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
_CACHE_TTL = 60.0  # seconds
_cache: dict[str, tuple[str, float]] = {}  # key → (value_str, expiry)
_cache_lock = threading.Lock()


def get_runtime_setting(key: str, db: Session | None = None) -> str:
    """Return the string value for a runtime-configurable key.

    Resolution order: memory cache → DB → env var → default.
    db may be None (falls back to env var / default).
    """
    if key not in _RUNTIME_CONFIGURABLE:
        raise KeyError(f"Unknown runtime setting: {key!r}")

    env_var, default = _RUNTIME_CONFIGURABLE[key]

    # 1. Memory cache
    now = time.monotonic()
    with _cache_lock:
        if key in _cache:
            value, expiry = _cache[key]
            if now < expiry:
                return value

    # 2. DB (if available)
    if db is not None:
        try:
            from app.models import AppSetting  # avoid circular import at module level

            row = db.get(AppSetting, key)
            if row is not None:
                _set_cache(key, row.value, now)
                return row.value
        except Exception:
            pass  # DB unavailable — fall through

    # 3. Env var (non-empty env var overrides default; empty env var is ignored)
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val:
            _set_cache(key, env_val, now)
            return env_val

    # 4. Default
    _set_cache(key, default, now)
    return default


def get_model_setting(model_key: str, db: Session | None = None) -> str:
    """Semantic wrapper for model settings.

    Supports model values in these formats:
      - Legacy plain model name: `qwen3:4b-instruct`
      - Explicit local prefix: `local:qwen3:4b-instruct`
      - Cloud prefix: `cloud:provider/model`
      - Provider-id prefix: `{provider_id}:{model_name}`

    For compatibility with legacy call-sites that still need only the model name,
    this helper returns the model-name part for prefixed formats.
    """
    raw = str(get_runtime_setting(model_key, db) or "").strip()
    if not raw:
        return ""
    if raw.startswith("local:"):
        return raw.split(":", 1)[1].strip()
    if raw.startswith("cloud:"):
        rest = raw.split(":", 1)[1].strip()
        if "/" in rest:
            return rest.split("/", 1)[1].strip()
        return rest

    # Provider-id prefixed form: {provider_id}:{model_name}
    # Keep legacy model strings like qwen3:4b-instruct unchanged.
    if len(raw) > 37 and raw[8:9] == "-" and raw[13:14] == "-" and raw[18:19] == "-" and raw[23:24] == "-":
        prefix, model_name = raw.split(":", 1)
        if len(prefix) == 36:
            return model_name.strip()
    return raw


def get_runtime_bool(key: str, db: Session | None = None) -> bool:
    """Convenience wrapper — interprets "1"/"true"/"yes" as True."""
    val = get_runtime_setting(key, db).strip().lower()
    return val in ("1", "true", "yes", "on")


def get_runtime_int(key: str, db: Session | None = None) -> int:
    """Convenience wrapper — returns int, falls back to 0 on parse error."""
    try:
        return int(get_runtime_setting(key, db))
    except (ValueError, TypeError):
        return 0


def get_runtime_json(key: str, db: Session | None = None) -> Any:
    """Convenience wrapper — JSON-decodes the value."""
    try:
        return json.loads(get_runtime_setting(key, db))
    except (ValueError, TypeError):
        return {}


def invalidate_runtime_cache(*keys: str) -> None:
    """Evict one or more keys from the cache (call after DB write)."""
    with _cache_lock:
        if keys:
            for k in keys:
                _cache.pop(k, None)
        else:
            _cache.clear()


def set_runtime_setting(key: str, value: str, db: Session) -> str:
    """Persist a runtime setting and invalidate in-memory cache for the key."""
    if key not in _RUNTIME_CONFIGURABLE:
        raise KeyError(f"Unknown runtime setting: {key!r}")
    if db is None:
        raise ValueError("db session is required")

    from app.models import AppSetting  # avoid circular import at module level

    str_value = str(value)
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=str_value, updated_at=datetime.now(UTC))
        db.add(row)
    else:
        row.value = str_value
        row.updated_at = datetime.now(UTC)
    db.commit()
    invalidate_runtime_cache(key)
    return str_value


def _set_cache(key: str, value: str, now: float) -> None:
    with _cache_lock:
        _cache[key] = (value, now + _CACHE_TTL)
