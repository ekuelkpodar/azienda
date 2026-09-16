"""Router: tools. Contract: /API.md §tools.

Skeleton only — routes are defined by builders against API.md.
No business logic in this file; call domain service interfaces.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/tools", tags=["tools"])
