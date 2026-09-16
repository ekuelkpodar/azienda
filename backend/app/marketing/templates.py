"""Jinja-sandboxed rendering for marketing campaign content.

Deliberately NOT imported from ``app.comms.templates``: cross-package imports
go through ``core/contracts.py`` protocols or a package's public interface, and
a shared template helper would couple marketing to comms internals. The ~15
lines of duplication are the documented cost of the seam (ARCHITECTURE.md §4).

Same guarantees as comms: ``jinja2.sandbox.SandboxedEnvironment`` +
``StrictUndefined`` — missing variables are hard errors, unsafe attribute
access is blocked.
"""
from __future__ import annotations

from typing import Any

import jinja2
from jinja2.sandbox import SandboxedEnvironment


class TemplateRenderError(ValueError):
    """Raised when campaign content cannot be rendered."""


_env = SandboxedEnvironment(undefined=jinja2.StrictUndefined, autoescape=False)


def render_campaign_content(body: str, variables: dict[str, Any]) -> str:
    """Render campaign body content with variables under the Jinja sandbox."""
    try:
        template = _env.from_string(body)
    except jinja2.TemplateSyntaxError as exc:
        raise TemplateRenderError(
            f"template syntax error at line {exc.lineno}: {exc.message}") from exc
    try:
        return template.render(**variables)
    except jinja2.UndefinedError as exc:
        raise TemplateRenderError(f"missing template variable: {exc}") from exc
    except jinja2.TemplateError as exc:  # pragma: no cover - defensive
        raise TemplateRenderError(f"template render failed: {exc}") from exc
