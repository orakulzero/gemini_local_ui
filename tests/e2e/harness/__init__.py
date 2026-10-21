# tests/e2e/harness/__init__.py
"""Test Harness and Protocol Doubles for Gemini Local UI E2E Test Suite."""

from .protocol_harness import (
    WIZDataExtractor,
    CookieParser,
    RotateCookiesClient,
    HeaderSynthesizer,
    BatchexecuteEncoder,
    StreamGenerateEncoder,
    StreamingFrameParser,
    StreamingDeltaAccumulator,
    ThoughtParser,
    MultimodalUploadHandler,
    MockGeminiServer,
)

from .storage_harness import (
    StorageEngine,
    FTS5SearchEngine,
    ConversationTree,
    ThreadExporter,
    RemoteSyncEngine,
)

from .openai_harness import (
    OpenAIModelRegistry,
    OpenAIChatAdapter,
    OpenAISSEChunkEncoder,
    OpenAIMultimodalParser,
)

from .ui_sim_harness import (
    VirtualListSimulator,
    IncrementalMarkdownParser,
    KaTeXMathExtractor,
    PromptInputSimulator,
    LongTaskPerformanceObserver,
)

from .crypto_vault_harness import (
    DPAPIVault,
    BrowserProfileExtractor,
    LocalSecurityGuard,
)

__all__ = [
    "WIZDataExtractor",
    "CookieParser",
    "RotateCookiesClient",
    "HeaderSynthesizer",
    "BatchexecuteEncoder",
    "StreamGenerateEncoder",
    "StreamingFrameParser",
    "StreamingDeltaAccumulator",
    "ThoughtParser",
    "MultimodalUploadHandler",
    "MockGeminiServer",
    "StorageEngine",
    "FTS5SearchEngine",
    "ConversationTree",
    "ThreadExporter",
    "RemoteSyncEngine",
    "OpenAIModelRegistry",
    "OpenAIChatAdapter",
    "OpenAISSEChunkEncoder",
    "OpenAIMultimodalParser",
    "VirtualListSimulator",
    "IncrementalMarkdownParser",
    "KaTeXMathExtractor",
    "PromptInputSimulator",
    "LongTaskPerformanceObserver",
    "DPAPIVault",
    "BrowserProfileExtractor",
    "LocalSecurityGuard",
]
