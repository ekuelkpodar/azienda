"""Error envelope: {"error": {"code", "message", "details", "trace_id"}}.

See API.md §1 (error conventions). Codes are stable snake_case strings;
HTTP mapping lives in the exception handlers below.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode(str, Enum):  # noqa: UP042
    # auth / tenancy
    unauthenticated = "unauthenticated"
    forbidden = "forbidden"
    cross_tenant = "cross_tenant"
    token_expired = "token_expired"  # noqa: S105
    token_revoked = "token_revoked"  # noqa: S105
    # validation / state
    bad_request = "bad_request"
    not_found = "not_found"
    conflict = "conflict"
    validation_error = "validation_error"
    rate_limited = "rate_limited"
    idempotency_conflict = "idempotency_conflict"
    # governance
    policy_denied = "policy_denied"
    approval_required = "approval_required"
    approval_expired = "approval_expired"
    budget_exceeded = "budget_exceeded"
    spend_frozen = "spend_frozen"
    # billing
    billing_error = "billing_error"
    spend_cap_hit = "spend_cap_hit"
    # platform
    internal_error = "internal_error"
    service_unavailable = "service_unavailable"


class AziendaError(Exception):
    """Base domain error. Carries a stable code, HTTP status, and details."""

    code: ErrorCode = ErrorCode.internal_error
    status_code: int = 500

    def __init__(self, message: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message or self.code.value
        self.details = details or {}


class UnauthenticatedError(AziendaError):
    code = ErrorCode.unauthenticated
    status_code = 401


class ForbiddenError(AziendaError):
    code = ErrorCode.forbidden
    status_code = 403


class CrossTenantError(ForbiddenError):
    code = ErrorCode.cross_tenant

    def __init__(self, message: str = "Cross-tenant access is forbidden",
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details)


class NotFoundError(AziendaError):
    code = ErrorCode.not_found
    status_code = 404


class ConflictError(AziendaError):
    code = ErrorCode.conflict
    status_code = 409


class BadRequestError(AziendaError):
    code = ErrorCode.bad_request
    status_code = 400


class RateLimitedError(AziendaError):
    code = ErrorCode.rate_limited
    status_code = 429


class PolicyDeniedError(AziendaError):
    code = ErrorCode.policy_denied
    status_code = 403


class ApprovalRequiredError(AziendaError):
    code = ErrorCode.approval_required
    status_code = 403


class ApprovalExpiredError(AziendaError):
    code = ErrorCode.approval_expired
    status_code = 410


class BudgetExceededError(AziendaError):
    code = ErrorCode.budget_exceeded
    status_code = 402


class SpendFrozenError(AziendaError):
    code = ErrorCode.spend_frozen
    status_code = 403


class SpendCapHitError(AziendaError):
    code = ErrorCode.spend_cap_hit
    status_code = 402


def error_envelope(code: str, message: str, details: dict[str, Any] | None,
                   trace_id: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message,
                      "details": details or {}, "trace_id": trace_id}}


def install_error_handlers(app: FastAPI) -> None:
    """Every error leaves the API in the API.md §1 envelope."""

    def _trace_id(request: Request) -> str:
        return getattr(request.state, "request_id", "unknown")

    @app.exception_handler(AziendaError)
    async def _azienda_handler(request: Request, exc: AziendaError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(exc.code.value, exc.message, exc.details,
                                   _trace_id(request)),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            400: ErrorCode.bad_request, 401: ErrorCode.unauthenticated,
            403: ErrorCode.forbidden, 404: ErrorCode.not_found,
            409: ErrorCode.conflict, 422: ErrorCode.validation_error,
            429: ErrorCode.rate_limited,
        }.get(exc.status_code, ErrorCode.internal_error)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(code.value, str(exc.detail), {}, _trace_id(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request,
                                  exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_envelope(ErrorCode.validation_error.value,
                                   "Request validation failed",
                                   {"errors": exc.errors()}, _trace_id(request)),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content=error_envelope(ErrorCode.internal_error.value,
                                   "An unexpected error occurred", {},
                                   _trace_id(request)),
        )
