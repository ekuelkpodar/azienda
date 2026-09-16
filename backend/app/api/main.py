"""API composition root: ``backend/app/api/main.py``.

The application factory lives in ``app.main`` (the runnable entry point:
``uvicorn app.main:app``). This module re-exports it under the path the API
contract assigns, so both ``app.main:app`` and ``app.api.main:app`` resolve to
the same application object.
"""
from __future__ import annotations

from app.main import app, create_app  # noqa: F401

__all__ = ["app", "create_app"]
