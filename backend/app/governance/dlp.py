"""DLP (data loss prevention) basics for the governance rail.

Two jobs:
1. ``sanitize_audit_payload`` — redact secrets from payloads BEFORE they are
   hashed into the append-only audit ledger (documented: the chain covers the
   redacted record, which is what auditors and the API ever see).
2. Re-export of the core redaction primitives for log pipelines.

Pattern catalogue lives in ``core/redact.py`` (pure functions, no I/O) so the
structlog processor can use it without importing governance.
"""
from __future__ import annotations

from typing import Any

from app.core.redact import redact_mapping, redact_text, redact_value

__all__ = ["sanitize_audit_payload", "redact_text", "redact_value", "redact_mapping"]


def sanitize_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of an audit payload. Never mutates the input."""
    return redact_mapping(payload)
