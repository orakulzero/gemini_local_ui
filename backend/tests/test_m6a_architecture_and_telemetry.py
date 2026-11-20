"""backend/tests/test_m6a_architecture_and_telemetry.py - M6a architecture, observability, and services tests."""
from __future__ import annotations
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import pytest
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.mcp.approval_gate import get_approval_gate
from backend.app.observability.telemetry import (
    configure_telemetry,
    redact_sensitive_processor,
    reset_telemetry_for_tests,
)
from backend.app.services.media_postprocessor import (
    filter_unlocalized_images_meta,
    needs_image_localization,
)
from backend.app.services.prompt_assembler import assemble_turn_prompts
from backend.app.services.tool_runtime import ToolExecutionOutcome, execute_tool_with_hitl


def test_strangler_fig_boundary_and_reexports():
    chat_file = Path(__file__).resolve().parents[2] / "backend" / "app" / "api" / "chat.py"
    content = chat_file.read_text("utf-8")
    lines = content.splitlines()
    assert len(lines) <= 240, f"chat.py has {len(lines)} lines; expected <= 240"
    for sql in ["SELECT ", "INSERT INTO ", "UPDATE conversations", "DELETE FROM "]:
        assert sql not in content, f"chat.py contains raw SQL: {sql}"

    import backend.app.api.chat as chat_mod
    assert hasattr(chat_mod, "stream_with_disconnect_check")
    assert hasattr(chat_mod, "_needs_image_localization")
    assert hasattr(chat_mod, "format_ui_sse")


def test_prompt_assembler_and_media_postprocessor():
    class MockConfig:
        def get_active_prompt(self):
            return {"content": "You are helpful."}
        def list_skills(self):
            return [{"id": "caveman-concise", "triggers": ["/caveman-concise"], "content": "Talk short."}]
        def get_skill(self, slug):
            return {"id": "caveman-concise", "content": "Talk short."} if slug == "caveman-concise" else None

    prompt_res = assemble_turn_prompts(
        raw_prompt="/caveman-concise Explain Rust",
        system_instruction="Be concise.",
        use_global_prompt=True,
        gem_id=None,
        workspace_config=MockConfig(),
        active_tools=[],
    )
    assert "[Skill: caveman-concise]" in prompt_res.wire_prompt or "Talk short." in prompt_res.wire_prompt
    assert "Explain Rust" in prompt_res.clean_storage_text
    assert not prompt_res.clean_storage_text.startswith("/caveman-concise")
    assert "Talk short." not in prompt_res.clean_storage_text

    assert needs_image_localization("https://lh3.googleusercontent.com/gg-dl/abc") is True
    assert needs_image_localization("/files/local_123.png") is False
    filtered = filter_unlocalized_images_meta({"images": [{"url": "https://lh3.googleusercontent.com/gg-dl/abc"}]})
    assert len(filtered.get("images", [])) == 0


@pytest.mark.asyncio
async def test_hitl_sse_disconnect_rapid_abort():
    gate = get_approval_gate()
    cancel_event = asyncio.Event()
    outcome = ToolExecutionOutcome()
    mcp_mock = MagicMock()
    mcp_mock.call_tool = AsyncMock()

    t0 = time.monotonic()
    async def cancel_soon():
        await asyncio.sleep(0.3)
        cancel_event.set()

    asyncio.create_task(cancel_soon())
    events = []
    async for ev in execute_tool_with_hitl(
        tool_name="test_tool",
        tool_args={"arg": 1},
        active_tools=[{"name": "test_tool"}],
        effective_prompt="Run",
        cancel_event=cancel_event,
        outcome=outcome,
        mcp_manager=mcp_mock,
        approval_gate=gate,
        approval_timeout=120.0,
    ):
        events.append(ev)

    elapsed = time.monotonic() - t0
    assert elapsed < 1.5, f"Abort took {elapsed:.2f}s; expected < 1.5s"
    assert outcome.cancelled is True and outcome.denied is True
    assert mcp_mock.call_tool.call_count == 0
    assert len(gate._pending) == 0


def test_structlog_redaction_and_otel_trace_propagation():
    scrubbed = redact_sensitive_processor(
        None, "test", {"event": "login", "cookie": "secret_cookie", "nested": {"api_key": "sk-123", "normal": "ok"}}
    )
    assert scrubbed["cookie"] == "[REDACTED]"
    assert scrubbed["nested"]["api_key"] == "[REDACTED]"
    assert scrubbed["nested"]["normal"] == "ok"

    exporter = InMemorySpanExporter()
    configure_telemetry(app, span_exporter=exporter)
    test_trace_id = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

    try:
        client = TestClient(app, raise_server_exceptions=False)
        with patch("backend.app.api.chat.get_gemini_client") as mock_cli:
            mock_inst = MagicMock()
            async def fake_stream(*a, **k):
                from backend.app.bridge.streaming_parser import StreamChunk
                yield StreamChunk(type="text", delta="Hello")
                yield StreamChunk(type="done", delta="", metadata={"cid": "c1", "rid": "r1", "rcid": "rc1", "full_text": "Hello"})
            mock_inst.generate_stream = fake_stream
            mock_cli.return_value = mock_inst

            resp = client.post(
                "/api/chat/generate",
                json={"prompt": "Ping", "model": "gemini-2.5-pro"},
                headers={"X-Trace-Id": test_trace_id},
            )
            assert resp.headers.get("X-Trace-Id") == test_trace_id
            events = [json.loads(line[5:].strip()) for line in resp.text.splitlines() if line.strip().startswith("data:")]
            start_event = next((e for e in events if e.get("type") == "start"), None)
            assert start_event is not None and start_event.get("trace_id") == test_trace_id

            spans = exporter.get_finished_spans()
            span_names = [s.name for s in spans]
            assert any("POST /api/chat/generate" in n for n in span_names)
    finally:
        reset_telemetry_for_tests()
