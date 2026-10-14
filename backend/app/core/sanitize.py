"""Payload sanitization for ingested traces.

AIMONITOR stores *usage metadata*, never conversation content. Callers routinely
hand their whole request object to a tracing SDK, so the ingestion path strips
anything that looks like prompt or response text before it reaches the database,
and caps the remaining free-form fields so one caller cannot bloat the store.
"""
import json
from itertools import islice
from typing import Any, Dict, List, Optional

from backend.app.core.config import settings

REDACTED = "[redacted]"

#: Substrings that mark a metadata key as carrying model input/output text.
_SENSITIVE_KEY_PARTS = (
    "prompt",
    "completion",
    "response",
    "message",
    "content",
    "answer",
    "question",
    "query",
    "input_text",
    "output_text",
    "system",
    "choices",
    "transcript",
    "document",
    "context_text",
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def redact_metadata(metadata: Optional[Dict[str, Any]], _depth: int = 0) -> Optional[Dict[str, Any]]:
    """Recursively replace prompt/response-shaped values with a redaction marker."""
    if metadata is None:
        return None
    return _redact_value(metadata, _depth)


def _redact_value(value: Any, depth: int) -> Any:
    if isinstance(value, (dict, list)) and depth > 6:
        return {"_truncated": "max depth exceeded"}
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in islice(value.items(), 50):
            key_str = str(key)
            cleaned[key_str] = REDACTED if _is_sensitive_key(key_str) else _redact_value(item, depth + 1)
        return cleaned
    if isinstance(value, list):
        return [_redact_value(item, depth + 1) for item in value[:50]]
    return value


def serialize_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    """Redact, serialize, and size-cap a metadata payload."""
    if not metadata:
        return None
    cleaned = redact_metadata(metadata)
    try:
        encoded = json.dumps(cleaned, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"_error": "metadata was not JSON-serializable"})

    if len(encoded.encode("utf-8")) > settings.MAX_METADATA_BYTES:
        return json.dumps(
            {"_truncated": f"metadata exceeded {settings.MAX_METADATA_BYTES} bytes"}
        )
    return encoded


def normalize_tags(tags: Optional[List[str]]) -> Optional[str]:
    """Trim, de-duplicate, and cap tags into the comma-separated stored form."""
    if not tags:
        return None
    seen: List[str] = []
    for tag in tags:
        cleaned = str(tag).strip().replace(",", " ")[: settings.MAX_TAG_LENGTH]
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= settings.MAX_TAGS:
            break
    return ",".join(seen) if seen else None


def truncate_error_message(message: Optional[str]) -> Optional[str]:
    """Cap an upstream error string so a provider cannot write unbounded data."""
    if message is None:
        return None
    text = str(message).strip()
    if not text:
        return None
    limit = settings.MAX_ERROR_MESSAGE_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + "... [truncated]"
