"""
backend/app/services/__init__.py
Domain service layer extracted from chat HTTP routes (M6a Strangler-Fig).
"""

from backend.app.services.chat_orchestrator import (
    PreparedTurn,
    estimate_token_count,
    prepare_chat_turn,
    stream_chat_turn,
)
from backend.app.services.media_postprocessor import (
    MediaAccumulator,
    _filter_unlocalized_images_meta,
    _localize_generated_images,
    _needs_image_localization,
    filter_unlocalized_images_meta,
    localize_generated_images,
    needs_image_localization,
)
from backend.app.services.prompt_assembler import (
    AssembledPrompt,
    assemble_prompt,
    assemble_turn_prompts,
    normalize_bridge_attachments,
    resolve_prior_context_ids,
)
from backend.app.services.stream_events import (
    ChatStreamEvent,
    CitationEvent,
    DoneEvent,
    ErrorEvent,
    FailoverEvent,
    GroundingEvent,
    KeepaliveEvent,
    MediaEvent,
    SavedEvent,
    StartEvent,
    TextEvent,
    ThoughtEvent,
    ToolApprovalRequiredEvent,
    ToolCallEvent,
    format_ui_sse,
)
from backend.app.services.tool_runtime import (
    ToolExecutionOutcome,
    execute_tool_with_hitl,
    extract_tool_call_block,
    strip_tool_call_fences,
)

__all__ = [
    "AssembledPrompt",
    "ChatStreamEvent",
    "CitationEvent",
    "DoneEvent",
    "ErrorEvent",
    "FailoverEvent",
    "GroundingEvent",
    "KeepaliveEvent",
    "MediaAccumulator",
    "MediaEvent",
    "PreparedTurn",
    "SavedEvent",
    "StartEvent",
    "TextEvent",
    "ThoughtEvent",
    "ToolApprovalRequiredEvent",
    "ToolCallEvent",
    "ToolExecutionOutcome",
    "_filter_unlocalized_images_meta",
    "_localize_generated_images",
    "_needs_image_localization",
    "assemble_prompt",
    "assemble_turn_prompts",
    "estimate_token_count",
    "execute_tool_with_hitl",
    "extract_tool_call_block",
    "filter_unlocalized_images_meta",
    "format_ui_sse",
    "localize_generated_images",
    "needs_image_localization",
    "normalize_bridge_attachments",
    "prepare_chat_turn",
    "resolve_prior_context_ids",
    "stream_chat_turn",
    "strip_tool_call_fences",
]
