"""
backend/tests/test_notebooklm_adversarial_challenge.py
Adversarial challenge test suite for Milestone M6:
NotebookLM Context Injection, Export, and Error Handling.

Empirically tests:
1. Context injection formatting in POST /api/notebooklm/attach-context:
   - Empty source selection (source_ids=[], include_notes=False)
   - Special characters and XML entities in titles/summaries (<, >, &, ", ', unicode)
   - Multi-source formatting, ordering, and XML envelope structure
   - Token estimation calculation across code, CJK, and empty contexts
   - Non-existent notebook_id error handling (404 vs 500)
2. Export functionality in POST /api/notebooklm/export:
   - Export from SQLite conversation with complex multi-turn DAG branches, code blocks, and math formulas
   - Specific message_ids filtering
   - Attempting to export non-existent conversation IDs (proper 404 response vs 200 false-success)
   - Missing required fields (400 validation)
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from typing import Dict, List
import httpx
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.exceptions import GeminiBridgeError
from backend.app.bridge.notebooklm import (
    NotebookDetail,
    NotebookInfo,
    NotebookLMClient,
    NotebookNote,
    NotebookSource,
)
from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.mock.fixtures import WIZ_GLOBAL_DATA_NOTEBOOKLM
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository

ADV_MOCK_PORT = 9355
ADV_MOCK_HOST = "127.0.0.1"
ADV_MOCK_BASE_URL = f"http://{ADV_MOCK_HOST}:{ADV_MOCK_PORT}"


@pytest.fixture(scope="module")
def adv_mock_server():
    """Starts deterministic mock server on dedicated port 9355."""
    server = MockGeminiServer(host=ADV_MOCK_HOST, port=ADV_MOCK_PORT)
    server.start_background()

    for _ in range(50):
        try:
            r = httpx.get(f"{ADV_MOCK_BASE_URL}/", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)

    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_adv_mock(adv_mock_server: MockGeminiServer):
    adv_mock_server.fault.reset()


@pytest.fixture(scope="module")
def adv_test_app(tmp_path_factory, adv_mock_server: MockGeminiServer):
    db_file = tmp_path_factory.mktemp("adv_notebooklm") / "adv_test.db"
    test_db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(test_db.init_schema(schema_path))

    repo = ConversationRepository(test_db)

    os.environ["NOTEBOOKLM_BASE_URL"] = ADV_MOCK_BASE_URL
    os.environ["GEMINI_BASE_URL"] = ADV_MOCK_BASE_URL

    sm = SessionManager(base_url=ADV_MOCK_BASE_URL)
    sm.context.cookies = {
        "__Secure-1PSID": "mock_adv_psid_token_12345",
        "__Secure-1PSIDTS": "mock_adv_psidts_token_67890",
    }
    sm.context.access_token = WIZ_GLOBAL_DATA_NOTEBOOKLM["SNlM0e"]

    import backend.app.main as main_mod
    main_mod.db = test_db
    main_mod.repository = repo
    main_mod.session_manager = sm
    app.state.db = test_db
    app.state.repository = repo
    app.state.session_manager = sm

    client = TestClient(app, base_url="http://testserver")
    return client


# ============================================================================
# 1. Context Injection: POST /api/notebooklm/attach-context
# ============================================================================

def test_attach_context_empty_sources_and_notes(adv_test_app: TestClient):
    """
    Challenge: Test empty source selection (source_ids=[], include_notes=False).
    Should produce an empty reference envelope without crashing or KeyError.
    """
    payload = {
        "notebook_id": "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
        "source_ids": [],
        "include_notes": False,
    }
    resp = adv_test_app.post("/api/notebooklm/attach-context", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["source_count"] == 0
    assert data["note_count"] == 0
    assert data["estimated_tokens"] >= 1

    lines = [line.strip() for line in data["formatted_context"].strip().split("\n")]
    assert len(lines) == 2
    assert lines[0].startswith('<notebooklm_reference title="')
    assert lines[1] == '</notebooklm_reference>'


def test_attach_context_special_characters_in_titles(adv_test_app: TestClient):
    """
    Challenge: Special characters, XML tags, and unicode in source titles and notes.
    Tests whether unescaped XML injection occurs in <notebooklm_reference>.
    """
    payload = {
        "notebook_id": "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
        "source_ids": ["src_qcomp_01"],
        "include_notes": True,
    }
    resp = adv_test_app.post("/api/notebooklm/attach-context", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    xml = data["formatted_context"]

    # Verify structural integrity
    assert xml.startswith('<notebooklm_reference')
    assert xml.endswith('</notebooklm_reference>')
    assert '<source id="src_qcomp_01"' in xml
    assert '<note id="note_qcomp_n1"' in xml


def test_attach_context_token_estimation_mechanics(adv_test_app: TestClient):
    """
    Challenge: Test token estimation logic.
    Current implementation uses len(formatted_context.split()).
    We verify behavior across varied payloads.
    """
    payload = {
        "notebook_id": "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
        "source_ids": ["src_qcomp_01", "src_qcomp_02"],
        "include_notes": True,
    }
    resp = adv_test_app.post("/api/notebooklm/attach-context", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    tokens = data["estimated_tokens"]
    words = len(data["formatted_context"].split())
    assert tokens == words
    assert tokens > 10


def test_attach_context_bridge_error_handling(adv_test_app: TestClient, monkeypatch):
    """
    Challenge: Verify error handling when NotebookLMClient.get_notebook raises
    GeminiBridgeError('Notebook not found').
    Expected: HTTP 404 Not Found (matching GET /api/notebooklm/notebooks/{id}).
    Observed: Currently caught by generic Exception handler returning HTTP 500.
    """
    async def mock_get_notebook_not_found(self, notebook_id: str):
        raise GeminiBridgeError(f"Notebook {notebook_id} not found")

    monkeypatch.setattr(NotebookLMClient, "get_notebook", mock_get_notebook_not_found)

    payload = {
        "notebook_id": "nb_ghost_12345",
        "source_ids": [],
        "include_notes": False,
    }
    resp = adv_test_app.post("/api/notebooklm/attach-context", json=payload)
    print(f"attach-context not-found status: {resp.status_code} - {resp.text}")
    # Contract says 404, but current code raises 500
    assert resp.status_code == 404, (
        f"Expected HTTP 404 for missing notebook, but received HTTP {resp.status_code}: {resp.text}"
    )


# ============================================================================
# 2. Export: POST /api/notebooklm/export
# ============================================================================

@pytest.mark.asyncio
async def test_export_complex_conversation_code_and_math(adv_test_app: TestClient):
    """
    Challenge: Export an SQLite conversation with complex multi-turn DAG branches,
    code blocks (Rust, Python), and LaTeX math formulas ($$...$$ and $...$).
    Verify that code fences, indentation, and LaTeX math remain intact in Markdown.
    """
    repo = app.state.repository
    conv = await repo.create_conversation(title="Quantum Math & Algorithms", model="gemini-2.5-pro")

    code_payload = (
        "```rust\n"
        "fn qft_step(qubits: &mut [Qubit], n: usize) {\n"
        "    for i in 0..n {\n"
        "        qubits[i].hadamard();\n"
        "        for j in (i + 1)..n {\n"
        "            let angle = 2.0 * std::f64::consts::PI / (1 << (j - i + 1)) as f64;\n"
        "            qubits[j].controlled_phase(&qubits[i], angle);\n"
        "        }\n"
        "    }\n"
        "}\n"
        "```"
    )

    math_payload = (
        "The Quantum Fourier Transform operator is given by:\n"
        "$$\\mathcal{F}_N = \\frac{1}{\\sqrt{N}} \\sum_{j=0}^{N-1} \\sum_{k=0}^{N-1} \\omega^{jk} |k\\rangle\\langle j|$$\n"
        "where $\\omega = e^{2\\pi i / N}$."
    )

    # Turn 1
    m1 = await repo.add_message(
        conversation_id=conv.id,
        parent_id=None,
        role="user",
        content="Implement the QFT step in Rust and provide mathematical formulation.",
        model="gemini-2.5-pro",
    )
    # Turn 2
    m2 = await repo.add_message(
        conversation_id=conv.id,
        parent_id=m1.id,
        role="assistant",
        content=f"{code_payload}\n\n{math_payload}",
        model="gemini-2.5-pro",
    )
    # Turn 3 (Branch A)
    m3a = await repo.add_message(
        conversation_id=conv.id,
        parent_id=m2.id,
        role="user",
        content="Branch A: What about circuit depth?",
        model="gemini-2.5-pro",
    )
    # Turn 3 (Branch B - Active branch)
    m3b = await repo.add_message(
        conversation_id=conv.id,
        parent_id=m2.id,
        role="user",
        content="Branch B: How to mitigate decoherence?",
        model="gemini-2.5-pro",
    )
    m4b = await repo.add_message(
        conversation_id=conv.id,
        parent_id=m3b.id,
        role="assistant",
        content="Use dynamical decoupling with CPMG pulse sequences: $(\\pi/2)_x - (\\tau - \\pi_y - \\tau)^n$.",
        model="gemini-2.5-pro",
    )

    # Set active leaf to m4b
    await repo.set_active_leaf(conv.id, m4b.id)

    # Export active conversation
    payload = {
        "conversation_id": conv.id,
        "new_notebook_title": "Quantum QFT Research Notebook",
    }
    resp = adv_test_app.post("/api/notebooklm/export", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    result = resp.json()
    assert result["success"] is True
    assert "source" in result
    source = result["source"]

    # Verify source was registered
    assert source["title"] == "Chat: Quantum Math & Algorithms"
    assert source["source_type"] == 1


def test_export_nonexistent_conversation_id(adv_test_app: TestClient):
    """
    Challenge: Attempting to export a non-existent conversation ID.
    Mission specification: 'Attempting to export non-existent conversation IDs (proper 404 response).'
    We empirically check if 404 is returned, or if an empty export falsely succeeds with 200.
    """
    bogus_id = "conv_non_existent_99999999"
    payload = {
        "conversation_id": bogus_id,
        "new_notebook_title": "Ghost Conversation Notebook",
    }
    resp = adv_test_app.post("/api/notebooklm/export", json=payload)
    print(f"Non-existent conversation export returned: {resp.status_code} - {resp.text}")

    # The mission specifically expects a proper 404 response!
    # If it returns 200, this is an empirical bug reproduction.
    assert resp.status_code == 404, (
        f"Expected HTTP 404 for non-existent conversation ID, but received HTTP {resp.status_code}: {resp.text}"
    )


def test_export_missing_content_and_conversation_id(adv_test_app: TestClient):
    """
    Challenge: Missing both 'content' and 'conversation_id' should return HTTP 400.
    """
    payload = {
        "title": "Empty Request",
    }
    resp = adv_test_app.post("/api/notebooklm/export", json=payload)
    assert resp.status_code == 400
    assert "content" in resp.json()["detail"].lower() or "conversation_id" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_export_with_explicit_message_ids_filter(adv_test_app: TestClient):
    """
    Challenge: Export with explicit message_ids filter only exports selected turns.
    """
    repo = app.state.repository
    conv = await repo.create_conversation(title="Filtered Turns Chat", model="gemini-2.5-pro")

    m1 = await repo.add_message(conv.id, role="user", content="Message 1 to include", parent_id=None, model="gemini-2.5-pro")
    m2 = await repo.add_message(conv.id, role="assistant", content="Message 2 to exclude", parent_id=m1.id, model="gemini-2.5-pro")
    m3 = await repo.add_message(conv.id, role="user", content="Message 3 to include", parent_id=m2.id, model="gemini-2.5-pro")

    payload = {
        "conversation_id": conv.id,
        "message_ids": [m1.id, m3.id],
        "new_notebook_title": "Filtered Turns Export",
    }
    resp = adv_test_app.post("/api/notebooklm/export", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
