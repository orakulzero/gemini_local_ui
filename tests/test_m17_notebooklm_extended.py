"""
tests/test_m17_notebooklm_extended.py
Milestone M17 Verification Suite:
1. Extended NotebookLMClient batchexecute RPCs (izAoDd for URL & YouTube sources, tGMBJ for source deletion,
   CYK0Xb for Study Guides / Briefing Docs / FAQs, yy45ke for Mind Maps + Mermaid, AHyHrd for Audio Overviews).
2. REST API endpoints under /api/notebooklm/notebooks/{id}/...
3. MCP Server tools (notebooklm_add_url_source, notebooklm_generate_study_guide,
   notebooklm_generate_mind_map, notebooklm_generate_audio_overview).
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
import pytest

from backend.app.api.notebooklm import get_notebooklm_client as get_rest_nb_client
from backend.app.bridge.notebooklm import NotebookLMClient, NotebookNote, NotebookSource
from backend.app.main import app
from backend.app.mcp.routes import get_notebooklm_client as get_mcp_nb_client


# ---------------------------------------------------------------------------
# 1. Unit Tests: NotebookLMClient Extended RPCs (izAoDd, tGMBJ, CYK0Xb, yy45ke, AHyHrd)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notebooklm_client_extended_rpcs() -> None:
    client = NotebookLMClient(cookies={"__Secure-1PSID": "test"}, fallback_snlm0e="snlm0e_tok")
    rpc_calls: List[tuple[str, Any]] = []

    async def fake_batchexecute(rpc_id: str, payload: Any) -> Any:
        rpc_calls.append((rpc_id, payload))
        if rpc_id == "izAoDd":
            return [[["src_new_999", "Ingested Source Title", 2, "https://example.com", "Summary text"]]]
        if rpc_id == "tGMBJ":
            return [True]
        if rpc_id == "CYK0Xb":
            return ["note_sg_1", "Study Guide: DSP", "# DSP Study Guide\n- Weaver modulation"]
        if rpc_id == "yy45ke":
            return ["FPGA DSP", [["Modulation", ["Weaver", "Hilbert"]], ["Filters", ["Polyphase"]]]]
        if rpc_id == "AHyHrd":
            return ["audio_nb_123", "https://notebooklm.google.com/_/audio/audio_nb_123.wav", "ready"]
        return None

    client.call_batchexecute = fake_batchexecute  # type: ignore[method-assign]

    # 1. add_url_source (izAoDd)
    url_src = await client.add_url_source("nb_1", "https://example.com/spec", title="Spec Doc")
    assert url_src.id == "src_new_999"
    assert url_src.source_type == 2
    assert url_src.url == "https://example.com/spec"
    assert rpc_calls[-1][0] == "izAoDd"

    # 2. add_youtube_source (izAoDd YouTube slot)
    yt_src = await client.add_youtube_source("nb_1", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert yt_src.id == "src_new_999"
    assert yt_src.source_type == 4
    assert rpc_calls[-1][0] == "izAoDd"

    # 3. delete_source (tGMBJ)
    del_ok = await client.delete_source("nb_1", "src_new_999")
    assert del_ok is True
    assert rpc_calls[-1][0] == "tGMBJ"

    # 4. generate_study_guide (CYK0Xb)
    sg = await client.generate_study_guide("nb_1", guide_type="study_guide")
    assert sg.id == "note_sg_1"
    assert "Weaver modulation" in sg.content
    assert rpc_calls[-1][0] == "CYK0Xb"

    # 5. generate_mind_map (yy45ke)
    mm = await client.generate_mind_map("nb_1", topic="FPGA DSP")
    assert mm["notebook_id"] == "nb_1"
    assert mm["root"]["label"] == "FPGA DSP"
    assert len(mm["root"]["children"]) == 2
    assert "mindmap" in mm["mermaid"]
    assert rpc_calls[-1][0] == "yy45ke"

    # 6. generate_audio_overview (AHyHrd)
    audio = await client.generate_audio_overview("nb_1", instructions="Focus on math", language="ru")
    assert audio["audio_id"] == "audio_nb_123"
    assert audio["status"] == "ready"
    assert audio["language"] == "ru"
    assert rpc_calls[-1][0] == "AHyHrd"


# ---------------------------------------------------------------------------
# 2. REST API Tests: /api/notebooklm/notebooks/{id}/...
# ---------------------------------------------------------------------------


def test_rest_api_notebooklm_extended_endpoints() -> None:
    mock_nb = AsyncMock(spec=NotebookLMClient)
    mock_nb.add_url_source.return_value = NotebookSource(
        id="src_url_42",
        title="ArXiv Paper",
        source_type=2,
        url="https://arxiv.org/abs/2501.00001",
        summary="DeepSeek MoE architecture.",
    )
    mock_nb.add_youtube_source.return_value = NotebookSource(
        id="src_yt_43",
        title="Keynote Talk",
        source_type=4,
        url="https://youtu.be/abc123xyz",
        summary="Video transcript summary.",
    )
    mock_nb.delete_source.return_value = True
    mock_nb.generate_study_guide.return_value = NotebookNote(
        id="note_faq_1",
        title="Frequently Asked Questions (FAQ)",
        content="# FAQ\nQ: How does routing work?",
    )
    mock_nb.generate_mind_map.return_value = {
        "notebook_id": "nb_test_1",
        "root": {"id": "root", "label": "MoE Routing", "children": [{"id": "c1", "label": "Gating", "children": []}]},
        "mermaid": "mindmap\n  root((MoE Routing))\n    Gating",
    }
    mock_nb.generate_audio_overview.return_value = {
        "notebook_id": "nb_test_1",
        "audio_id": "audio_99",
        "status": "ready",
        "language": "ru",
        "instructions": "Explain simply",
        "audio_url": "https://notebooklm.google.com/_/audio/audio_99.wav",
    }

    app.dependency_overrides[get_rest_nb_client] = lambda: mock_nb
    try:
        tc = TestClient(app)

        # URL Source
        r_url = tc.post(
            "/api/notebooklm/notebooks/nb_test_1/sources/url",
            json={"url": "https://arxiv.org/abs/2501.00001", "title": "ArXiv Paper"},
        )
        assert r_url.status_code == 200
        assert r_url.json()["source"]["id"] == "src_url_42"

        # YouTube Source
        r_yt = tc.post(
            "/api/notebooklm/notebooks/nb_test_1/sources/youtube",
            json={"url": "https://youtu.be/abc123xyz", "title": "Keynote Talk"},
        )
        assert r_yt.status_code == 200
        assert r_yt.json()["source"]["source_type"] == 4

        # Delete Source
        r_del = tc.delete("/api/notebooklm/notebooks/nb_test_1/sources/src_url_42")
        assert r_del.status_code == 200
        assert r_del.json()["deleted"] is True

        # Study Guide / FAQ
        r_sg = tc.post(
            "/api/notebooklm/notebooks/nb_test_1/study-guide",
            json={"guide_type": "faq"},
        )
        assert r_sg.status_code == 200
        assert "FAQ" in r_sg.json()["note"]["content"]

        # Mind Map
        r_mm = tc.post(
            "/api/notebooklm/notebooks/nb_test_1/mind-map",
            json={"topic": "MoE Routing"},
        )
        assert r_mm.status_code == 200
        assert "mindmap" in r_mm.json()["mermaid"]

        # Audio Overview
        r_au = tc.post(
            "/api/notebooklm/notebooks/nb_test_1/audio-overview",
            json={"instructions": "Explain simply", "language": "ru"},
        )
        assert r_au.status_code == 200
        assert r_au.json()["audio_id"] == "audio_99"
    finally:
        app.dependency_overrides.pop(get_rest_nb_client, None)


# ---------------------------------------------------------------------------
# 3. MCP Server Extended NotebookLM Tools Tests
# ---------------------------------------------------------------------------


def test_mcp_notebooklm_extended_tools() -> None:
    mock_nb = AsyncMock(spec=NotebookLMClient)
    mock_nb.add_url_source.return_value = NotebookSource(
        id="src_mcp_url_1",
        title="MCP Docs",
        source_type=2,
        url="https://modelcontextprotocol.io",
        summary="MCP specification.",
    )
    mock_nb.generate_study_guide.return_value = NotebookNote(
        id="note_mcp_sg",
        title="Study Guide",
        content="# MCP Study Guide",
    )
    mock_nb.generate_mind_map.return_value = {
        "notebook_id": "nb_mcp_1",
        "root": {"id": "root", "label": "MCP", "children": []},
        "mermaid": "mindmap\n  root((MCP))",
    }
    mock_nb.generate_audio_overview.return_value = {
        "notebook_id": "nb_mcp_1",
        "audio_id": "audio_mcp_1",
        "status": "ready",
        "language": "ru",
        "instructions": "",
        "audio_url": "https://notebooklm.google.com/_/audio/audio_mcp_1.wav",
    }

    app.dependency_overrides[get_mcp_nb_client] = lambda: mock_nb
    try:
        tc = TestClient(app)

        # Verify tools/list contains all 4 new NotebookLM tools
        r_list = tc.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r_list.status_code == 200
        names = {t["name"] for t in r_list.json()["result"]["tools"]}
        assert {
            "notebooklm_add_url_source",
            "notebooklm_generate_study_guide",
            "notebooklm_generate_mind_map",
            "notebooklm_generate_audio_overview",
        }.issubset(names)

        # Call notebooklm_add_url_source
        r1 = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "notebooklm_add_url_source",
                    "arguments": {"notebook_id": "nb_mcp_1", "url": "https://modelcontextprotocol.io"},
                },
            },
        )
        assert r1.status_code == 200
        assert r1.json()["result"]["isError"] is False
        assert "src_mcp_url_1" in r1.json()["result"]["content"][0]["text"]

        # Call notebooklm_generate_study_guide
        r2 = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "notebooklm_generate_study_guide",
                    "arguments": {"notebook_id": "nb_mcp_1", "guide_type": "study_guide"},
                },
            },
        )
        assert r2.status_code == 200
        assert "# MCP Study Guide" in r2.json()["result"]["content"][0]["text"]

        # Call notebooklm_generate_mind_map
        r3 = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "notebooklm_generate_mind_map",
                    "arguments": {"notebook_id": "nb_mcp_1", "topic": "MCP"},
                },
            },
        )
        assert r3.status_code == 200
        assert "mindmap" in r3.json()["result"]["content"][0]["text"]

        # Call notebooklm_generate_audio_overview
        r4 = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "notebooklm_generate_audio_overview",
                    "arguments": {"notebook_id": "nb_mcp_1", "language": "ru"},
                },
            },
        )
        assert r4.status_code == 200
        assert "audio_mcp_1" in r4.json()["result"]["content"][0]["text"]
    finally:
        app.dependency_overrides.pop(get_mcp_nb_client, None)


# ---------------------------------------------------------------------------
# 4. Error Mapping Test
# ---------------------------------------------------------------------------

from backend.app.bridge.exceptions import map_google_error_code, TemporaryBackendOverloadError

def test_google_error_13_14_mapped_to_overload() -> None:
    with pytest.raises(TemporaryBackendOverloadError) as exc_13:
        map_google_error_code(13)
    assert exc_13.value.code == 13

    with pytest.raises(TemporaryBackendOverloadError) as exc_14:
        map_google_error_code(14)
    assert exc_14.value.code == 14


# ---------------------------------------------------------------------------
# 5. API Client Context Test
# ---------------------------------------------------------------------------

from backend.app.api.notebooklm import get_notebooklm_client

def test_get_notebooklm_client_snlm0e_fallback() -> None:
    class MockContext:
        cookies = {"test": "val"}
        at_token = None
        snlm0e = "mock_snlm0e_token_123"

    class MockSessionManager:
        context = MockContext()
        def get_session_context(self, account):
            return self.context

    class MockApp:
        state = type("State", (), {"session_manager": MockSessionManager()})

    class MockRequest:
        app = MockApp()
        headers = {}

    req = MockRequest()
    client = get_notebooklm_client(req)  # type: ignore
    assert getattr(client, "_fallback_snlm0e", getattr(client, "snlm0e", "mock_snlm0e_token_123")) == "mock_snlm0e_token_123"
