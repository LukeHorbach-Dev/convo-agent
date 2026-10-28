"""Structured errors. Each carries a category; recovery decisions are made from the category, not the type name."""
from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RATE_LIMITED = "rate_limited"
    PLATFORM_UNAVAILABLE = "platform_unavailable"
    SELECTOR_DRIFT = "selector_drift"
    AMBIGUOUS_STATE = "ambiguous_state"
    DELIVERY_UNKNOWN = "delivery_unknown"
    DATA_INTEGRITY = "data_integrity"
    MODEL_FAILURE = "model_failure"
    OPERATOR_INTERVENTION_REQUIRED = "operator_intervention_required"


class AgentError(Exception):
    category: ErrorCategory = ErrorCategory.PERMANENT

    def __init__(self, message: str, *, category: ErrorCategory | None = None, retry_after_s: float | None = None):
        super().__init__(message)
        if category is not None:
            self.category = category
        self.retry_after_s = retry_after_s


class TransientError(AgentError):
    category = ErrorCategory.TRANSIENT


class AuthenticationRequired(AgentError):
    """Login expired or a challenge (2FA, captcha, checkpoint) is shown. Never bypassed: the app pauses."""
    category = ErrorCategory.AUTHENTICATION


class PermissionRestricted(AgentError):
    """Messaging unavailable for this account (no Message control, request-only, restricted)."""
    category = ErrorCategory.PERMISSION


class RateLimited(AgentError):
    category = ErrorCategory.RATE_LIMITED


class PlatformUnavailable(AgentError):
    category = ErrorCategory.PLATFORM_UNAVAILABLE


class SelectorDrift(AgentError):
    """An expected UI element could not be resolved: the page layout likely changed."""
    category = ErrorCategory.SELECTOR_DRIFT


class AmbiguousState(AgentError):
    """The page or conversation can't be identified uniquely."""
    category = ErrorCategory.AMBIGUOUS_STATE


class DeliveryUnknown(AgentError):
    """A send was attempted and its outcome can't be established."""
    category = ErrorCategory.DELIVERY_UNKNOWN


class DataIntegrityError(AgentError):
    category = ErrorCategory.DATA_INTEGRITY


class ModelFailure(AgentError):
    category = ErrorCategory.MODEL_FAILURE


class BrowserCrashed(TransientError):
    """The browser process or connection died. Transient for the app; the supervisor restarts it within a budget."""


class InterventionRequired(AgentError):
    """Something only the operator can fix (browser keeps dying, API key rejected). The app pauses with an explanation."""
    category = ErrorCategory.OPERATOR_INTERVENTION_REQUIRED


class OperatorStop(AgentError):
    """An operator stop or pause is in force. Raised by Controls.guard before consequential actions."""
    category = ErrorCategory.OPERATOR_INTERVENTION_REQUIRED


class LeaseLost(AgentError):
    """This worker no longer owns the job (the watchdog or restart recovery took it over). The worker must stop
    touching the job; whoever owns it now decides what happens next."""
    category = ErrorCategory.TRANSIENT


class QueueFull(AgentError):
    category = ErrorCategory.TRANSIENT


class PolicyViolation(AgentError):
    """An action was refused by policy (e.g. sending on a real platform without human approval)."""
    category = ErrorCategory.PERMISSION
