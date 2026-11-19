"""
backend/app/bridge/exceptions.py
Shared exception hierarchy and Google RPC error status code mapper.
"""

from __future__ import annotations


def _clean_error_message(message: str | None, fallback: str) -> str:
    """Normalizes Google RPC error messages so None / empty / literal 'None' never leak."""
    if message is None:
        return fallback
    cleaned = str(message).strip()
    if not cleaned or cleaned.lower() == "none":
        return fallback
    return cleaned


class GeminiBridgeError(Exception):
    """Base exception for all Gemini web bridge failures."""

    def __init__(self, message: str | None, code: int | None = None) -> None:
        fallback = f"Google internal error code: {code}" if code is not None else "Gemini bridge error"
        clean_msg = _clean_error_message(message, fallback)
        super().__init__(clean_msg)
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
        raise TemporaryBackendOverloadError(
            _clean_error_message(message, "Google backend temporary overload"),
            code=code,
        )
    if code in (1037, 429, 8):
        raise UsageLimitExceededError(
            _clean_error_message(message, "Account generation quota exceeded")
        )
    if code == 1050:
        raise ModelInconsistentError(
            _clean_error_message(message, "Model changed within an active multi-turn thread")
        )
    if code == 1052:
        raise ModelHeaderInvalidError(
            _clean_error_message(message, "Invalid model identifier or capacity flag")
        )
    if code == 1060:
        raise IPBlockedError(
            _clean_error_message(message, "Source IP temporarily blocked by Google bot mitigation")
        )
    if code in (7, 16, 401, 403):
        raise AuthenticationError(
            _clean_error_message(message, "Permission denied: Session cookies revoked or expired"),
            code=code,
        )

    clean_msg = _clean_error_message(message, f"Google internal error code: {code}")
    if str(code) not in clean_msg:
        clean_msg = f"{clean_msg} (code {code})"
    raise GeminiBridgeError(clean_msg, code=code)
