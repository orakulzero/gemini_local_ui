"""
backend/app/bridge/exceptions.py
Shared exception hierarchy and Google RPC error status code mapper.
"""

from __future__ import annotations


class GeminiBridgeError(Exception):
    """Base exception for all Gemini web bridge failures."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class AuthenticationError(GeminiBridgeError):
    """Raised when cookies or SNlM0e tokens are missing, expired, or revoked (Code 7 / HTTP 401)."""

    def __init__(self, message: str = "Session credentials revoked or expired", code: int = 7) -> None:
        super().__init__(message, code=code)


class TemporaryBackendOverloadError(GeminiBridgeError):
    """Raised on Google transient backend overload (Code 1013 / HTTP 503). Retries allowed."""

    def __init__(self, message: str = "Google backend temporary overload", code: int = 1013) -> None:
        super().__init__(message, code=code)


class UsageLimitExceededError(GeminiBridgeError):
    """Raised when account hourly/daily quota is exhausted (Code 1037)."""

    def __init__(self, message: str = "Account generation quota exceeded", reset_timestamp: int | None = None) -> None:
        super().__init__(message, code=1037)
        self.reset_timestamp = reset_timestamp


class ModelInconsistentError(GeminiBridgeError):
    """Raised when the requested model differs from the model of an existing conversation (Code 1050)."""

    def __init__(self, message: str = "Model changed within an active multi-turn thread") -> None:
        super().__init__(message, code=1050)


class ModelHeaderInvalidError(GeminiBridgeError):
    """Raised when the synthesized model header is invalid or capacity mismatched (Code 1052)."""

    def __init__(self, message: str = "Invalid model identifier or capacity flag") -> None:
        super().__init__(message, code=1052)


class IPBlockedError(GeminiBridgeError):
    """Raised when Google bot mitigation temporarily blocks the source IP (Code 1060)."""

    def __init__(self, message: str = "Source IP temporarily blocked by Google bot mitigation") -> None:
        super().__init__(message, code=1060)


def map_google_error_code(code: int, message: str | None = None) -> None:
    """Maps Google internal RPC status code to a concrete exception."""
    if code in (1013, 13, 14):
        raise TemporaryBackendOverloadError(message or "Google backend temporary overload", code=code)
    if code == 1037:
        raise UsageLimitExceededError(message or "Account generation quota exceeded")
    if code == 1050:
        raise ModelInconsistentError(message or "Model changed within an active multi-turn thread")
    if code == 1052:
        raise ModelHeaderInvalidError(message or "Invalid model identifier or capacity flag")
    if code == 1060:
        raise IPBlockedError(message or "Source IP temporarily blocked by Google bot mitigation")
    if code == 7:
        raise AuthenticationError(message or "Permission denied: Session cookies revoked or expired", code=7)
    
    raise GeminiBridgeError(message or f"Google internal error code: {code}", code=code)
