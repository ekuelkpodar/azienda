"""Shared API schemas: pagination envelope and common shapes (API.md §1)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Page[T](BaseModel):
    items: list[T]
    next_page_token: str | None = None


def paginate[T](items: list[T], page_size: int, offset: int) -> Page[T]:
    chunk = items[offset:offset + page_size]
    next_token = str(offset + page_size) if offset + page_size < len(items) else None
    return Page(items=chunk, next_page_token=next_token)


class PaginationParams(BaseModel):
    page_size: int = Field(default=50, ge=1, le=200)
    page_token: str | None = None

    def offset(self) -> int:
        try:
            return max(0, int(self.page_token)) if self.page_token else 0
        except ValueError:
            return 0
