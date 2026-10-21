"""
backend/app/bridge package:
Gemini Web Protocol bridge, session manager, browser extractor,
models, custom gems, streaming parser, and file uploader.
"""

from .browser_extractor import (
    AppBoundEncryptionError,
    BrowserCookieExtractor,
    BrowserExtractorError,
    BrowserType,
    CookieValidationResult,
    ProfileNotFoundError,
)
from .client import GeminiWebClient
from .exceptions import (
    AuthenticationError,
    GeminiBridgeError,
    IPBlockedError,
    ModelHeaderInvalidError,
    ModelInconsistentError,
    TemporaryBackendOverloadError,
    UsageLimitExceededError,
    map_google_error_code,
)
from .models import (
    DEFAULT_MODELS,
    OPENAI_MODEL_ALIASES,
    Gem,
    GemSerializer,
    HeaderBuilder,
    ModelCapabilities,
    ModelInfo,
    ModelTier,
    resolve_model,
)
from .session import (
    SessionContext,
    SessionHealthReport,
    SessionHealthStatus,
    SessionManager,
)
from .streaming_parser import (
    StreamChunk,
    StreamingDeltaAccumulator,
    StreamingFrameParser,
)
from .uploader import (
    GeminiUploader,
    ResumableUploader,
)

__all__ = [
    "AppBoundEncryptionError",
    "AuthenticationError",
    "BrowserCookieExtractor",
    "BrowserExtractorError",
    "BrowserType",
    "CookieValidationResult",
    "DEFAULT_MODELS",
    "Gem",
    "GemSerializer",
    "GeminiBridgeError",
    "GeminiUploader",
    "GeminiWebClient",
    "HeaderBuilder",
    "IPBlockedError",
    "ModelCapabilities",
    "ModelHeaderInvalidError",
    "ModelInconsistentError",
    "ModelInfo",
    "ModelTier",
    "OPENAI_MODEL_ALIASES",
    "ProfileNotFoundError",
    "ResumableUploader",
    "SessionContext",
    "SessionHealthReport",
    "SessionHealthStatus",
    "SessionManager",
    "StreamChunk",
    "StreamingDeltaAccumulator",
    "StreamingFrameParser",
    "TemporaryBackendOverloadError",
    "UsageLimitExceededError",
    "map_google_error_code",
    "resolve_model",
]
