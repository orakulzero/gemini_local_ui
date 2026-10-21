"""
Mock package:
Deterministic mock server and golden fixtures replicating Google Gemini Web RPCs.
"""

from .fixtures import (
    HTML_APP_PAGE,
    HTML_APP_PAGE_GUEST,
    HTML_APP_PAGE_MALFORMED,
    PAYLOAD_CNGDBE_GEMS,
    PAYLOAD_CODE_7_PERMISSION_DENIED,
    PAYLOAD_HNVQHB_TURNS,
    PAYLOAD_MAZIQC_CONVERSATIONS,
    PAYLOAD_OTAQ7B_USER_STATUS,
    WIZ_GLOBAL_DATA_GOLDEN,
    build_golden_stream_sequence,
    build_stream_error_frame,
    format_batchexecute_envelope,
    format_stream_frame,
)
from .mock_server import MockFaultConfig, MockGeminiServer

__all__ = [
    "HTML_APP_PAGE",
    "HTML_APP_PAGE_GUEST",
    "HTML_APP_PAGE_MALFORMED",
    "MockFaultConfig",
    "MockGeminiServer",
    "PAYLOAD_CNGDBE_GEMS",
    "PAYLOAD_CODE_7_PERMISSION_DENIED",
    "PAYLOAD_HNVQHB_TURNS",
    "PAYLOAD_MAZIQC_CONVERSATIONS",
    "PAYLOAD_OTAQ7B_USER_STATUS",
    "WIZ_GLOBAL_DATA_GOLDEN",
    "build_golden_stream_sequence",
    "build_stream_error_frame",
    "format_batchexecute_envelope",
    "format_stream_frame",
]
