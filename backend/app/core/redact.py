"""Secret/pattern redaction for logs and stored payloads.

Pure functions, no I/O. ``governance/dlp.py`` wraps these with policy
(audit-payload sanitization); the structlog processor in ``core/logging.py``
uses ``redact_text`` so secrets never reach log sinks.
"""
from __future__ import annotations

import re
from typing import Any

# Order matters: most specific first. Each maps to a <REDACTED:kind> token so
# operators can see *what kind* of secret was present without seeing the value.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("azienda_api_key", re.compile(r"azk_[A-Za-z0-9]{16,}")),
    ("bearer_token", re.compile(r"[Bb]earer\s+[A-Za-z0-9\-._~+/=]{16,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret", re.compile(
        r"(?i)aws[_-]?secret[_-]?access[_-]?key[\"'\s:=]+[A-Za-z0-9/+=]{32,}")),
    ("generic_api_key", re.compile(
        r"(?i)(api[_-]?key|apikey|client[_-]?secret|auth[_-]?token)[\"'\s:=]+[A-Za-z0-9\-._~+/=]{16,}")),
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]

# Structured-logging / payload keys whose VALUES are always redacted wholesale.
_SENSITIVE_KEYS = frozenset({
    "password", "password_hash", "secret", "client_secret", "api_key", "apikey",
    "access_token", "refresh_token", "id_token", "private_key", "seed_phrase",
    "authorization", "cookie", "set-cookie", "token", "key_hash",
})


def redact_text(text: str) -> str:
    """Replace secret-looking substrings with <REDACTED:kind> tokens."""
    if not text:
        return text
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"<REDACTED:{kind}>", text)
    return text


def redact_value(key: str, value: object) -> object:
    """Redact a single key/value pair by key name, else redact strings by pattern."""
    if key.lower() in _SENSITIVE_KEYS:
        return "<REDACTED>" if value not in (None, "") else value
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact a JSON-like mapping. Returns a new dict."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = redact_mapping(v)
        elif isinstance(v, list):
            out[k] = [redact_mapping(i) if isinstance(i, dict)
                      else redact_value(str(k), i) for i in v]
        else:
            out[k] = redact_value(str(k), v)
    return out
