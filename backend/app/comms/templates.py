"""Jinja-sandboxed template rendering for comms message templates.

Uses ``jinja2.sandbox.SandboxedEnvironment`` with ``StrictUndefined``:
- Sandboxing blocks access to unsafe attributes/methods on rendered objects.
- StrictUndefined makes missing variables a hard error instead of silent blanks.

Limitations (documented, not hidden): no custom filters/tags; ``autoescape`` is
off because most channels are plaintext (SMS) — HTML email rendering must pass
through an HTML-sanitizing step before send (future work, noted in README).
"""
from __future__ import annotations

from typing import Any

import jinja2
from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment


class TemplateRenderError(ValueError):
    """Raised when a template cannot be rendered (syntax error or missing variable)."""


_env = SandboxedEnvironment(undefined=jinja2.StrictUndefined, autoescape=False)


def render_template_string(body: str, variables: dict[str, Any]) -> str:
    """Render a template body with variables under the Jinja sandbox."""
    try:
        template = _env.from_string(body)
    except jinja2.TemplateSyntaxError as exc:
        msg = f"template syntax error at line {exc.lineno}: {exc.message}"
        raise TemplateRenderError(msg) from exc
    try:
        return template.render(**variables)
    except jinja2.UndefinedError as exc:
        raise TemplateRenderError(f"missing template variable: {exc}") from exc
    except jinja2.TemplateError as exc:  # pragma: no cover - defensive
        raise TemplateRenderError(f"template render failed: {exc}") from exc


def extract_variables(body: str) -> list[str]:
    """Best-effort list of undeclared variable names used by the template."""
    try:
        ast = _env.parse(body)
    except jinja2.TemplateSyntaxError as exc:
        msg = f"template syntax error at line {exc.lineno}: {exc.message}"
        raise TemplateRenderError(msg) from exc
    return sorted(meta.find_undeclared_variables(ast))
