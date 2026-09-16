"""tasks package public interface."""

from app.core.models import Base as TasksBase  # shared metadata (alembic/env.py)

from .service import (
    STATUSES,
    TERMINAL,
    DependencyCycleError,
    IllegalTransitionError,
    PolicyDeniedError,
    TaskError,
    TaskNotFound,
    TaskService,
    TaskValidationError,
    can_transition,
)

__all__ = [
    "TasksBase",
    "STATUSES",
    "TERMINAL",
    "DependencyCycleError",
    "IllegalTransitionError",
    "PolicyDeniedError",
    "TaskError",
    "TaskNotFound",
    "TaskService",
    "TaskValidationError",
    "can_transition",
]
