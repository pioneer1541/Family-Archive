import logging
import re
from typing import Any, Dict

from app.config import get_settings

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ACCOUNT_RE = re.compile(r"\b\d{8,20}\b")

_FORBIDDEN_KEYS = {
    "chunk",
    "content",
    "raw_text",
    "address",
    "email",
    "account",
    "full_text",
    "page_text",
}

_LOGGING_CONFIGURED = False
_BASE_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__.keys())


def sanitize_log_value(value: Any) -> Any:
    if isinstance(value, str):
        out = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
        out = _ACCOUNT_RE.sub("[REDACTED_ACCOUNT]", out)
        if len(out) > 240:
            return out[:240] + "..."
        return out
    if isinstance(value, dict):
        return sanitize_log_context(value)
    if isinstance(value, list):
        return [sanitize_log_value(item) for item in value[:20]]
    return value


def sanitize_log_context(context: Dict[str, Any] | None) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in (context or {}).items():
        low = str(key).strip().lower()
        if low in _FORBIDDEN_KEYS:
            safe[key] = "[REDACTED]"
            continue
        safe[key] = sanitize_log_value(value)
    return safe


def get_logger(name: str) -> logging.Logger:
    global _LOGGING_CONFIGURED
    settings = get_settings()
    if not _LOGGING_CONFIGURED:

        class SafeExtraFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                message = super().format(record)
                extras: dict[str, Any] = {}
                for key, value in record.__dict__.items():
                    if key in _BASE_RECORD_FIELDS:
                        continue
                    if key.startswith("_"):
                        continue
                    extras[key] = sanitize_log_value(value)
                if not extras:
                    return message
                extras_text = " ".join(f"{k}={extras[k]!r}" for k in sorted(extras))
                return f"{message} {extras_text}"

        handler = logging.StreamHandler()
        handler.setFormatter(SafeExtraFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            handlers=[handler],
            force=False,
        )
        _LOGGING_CONFIGURED = True
    return logging.getLogger(name)
