"""
backend/tests/test_notebooklm_bridge.py
Automated test suite for NotebookLM Bridge, Mock Engine & REST Workspace API (Milestone M6).
Covers:
- Token scraping from WIZ_global_data and fallback credentials
- All 6 RPC calls (wXbhsf, rLM1Ne, CCqFvf, WWINqb, s0tc2d, trScbf)
- Strongly-typed dataclass contracts (NotebookInfo, NotebookDetail, NotebookSource, NotebookNote)
- Offline mock server fault injection and error recovery
- REST API router endpoints (/api/notebooklm/*)
- Context attachment formatting (<notebooklm_reference>) and chat research export
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from typing import AsyncIterator, Dict, List
import httpx
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.exceptions import AuthenticationError, GeminiBridgeError
from backend.app.bridge.notebooklm import (
    NotebookDetail,
    NotebookInfo,
    NotebookLMClient,
    NotebookNote,
    NotebookSource,
)
from backend.app.bridge.session import SessionManager
from backend.app.main import app, db, repository
from backend.app.mock.fixtures import (
    HTML_NOTEBOOKLM_APP_PAGE,
    PAYLOAD_NOTEBOOKLM_ADD_SOURCE,
    PAYLOAD_NOTEBOOKLM_CREATE_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_DELETE_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_GET_NOTEBOOK,
    PAYLOAD_NOTEBOOKLM_LIST_NOTEBOOKS,
    PAYLOAD_NOTEBOOKLM_RENAME_NOTEBOOK,
    WIZ_GLOBAL_DATA_NOTEBOOKLM,
)
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository

MOCK_PORT = 8999
MOCK_HOST = "127.0.0.1"
MOCK_BASE_URL = f"http://{MOCK_HOST}:{MOCK_PORT}"


@pytest.fixture(scope="module")
def mock_server():
    """Starts deterministic mock server in a background thread."""
    server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
    server.start_background()

    # Wait for server ready
    for _ in range(50):
        try:
            r = httpx.get(f"{MOCK_BASE_URL}/", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)

    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock(mock_server: MockGeminiServer):
    """Resets mock faults before each test."""
    mock_server.fault.reset()


@pytest.fixture(scope="module")
def mock_cookies() -> Dict[str, str]:
    return {
        "__Secure-1PSID": "mock_psid_valid_test_token_12345",
        "__Secure-1PSIDTS": "mock_psidts_initial_timestamp_67890",
    }


@pytest.fixture(scope="module")
def test_app(tmp_path_factory, mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Configures FastAPI application state with mock session and database."""
    db_file = tmp_path_factory.mktemp("notebooklm_test") / "test_api.db"
    test_db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(test_db.init_schema(schema_path))

    repo = ConversationRepository(test_db)

    os.environ["NOTEBOOKLM_BASE_URL"] = MOCK_BASE_URL
    os.environ["GEMINI_BASE_URL"] = MOCK_BASE_URL

    sm = SessionManager(base_url=MOCK_BASE_URL)
    sm.context.cookies = dict(mock_cookies)
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
# 1. Dataclasses & Contract Verification
# ============================================================================

def test_notebook_dataclasses_structure():
    """Validates strong typing and field defaults on NotebookLM dataclasses."""
    source = NotebookSource(
        id="src_1",
        title="Paper.pdf",
        source_type=3,
        url="https://arxiv.org/pdf/1",
        summary="A paper on quantum computing.",
        token_count=1200,
        created_at=1729180000.0,
    )
    assert source.id == "src_1"
    assert source.source_type == 3
    assert source.summary == "A paper on quantum computing."

    note = NotebookNote(
        id="note_1",
        title="Notes",
        content="Research questions",
        created_at=1729180100.0,
        updated_at=1729180200.0,
    )
    assert note.id == "note_1"
    assert note.content == "Research questions"

    info = NotebookInfo(
        id="nb_1",
        title="Quantum Work",
        created_at=1729180000.0,
        updated_at=1729181000.0,
        source_count=1,
        emoji="🧪",
    )
    assert info.id == "nb_1"
    assert info.emoji == "🧪"

    detail = NotebookDetail(
        id="nb_1",
        title="Quantum Work",
        created_at=1729180000.0,
        updated_at=1729181000.0,
        source_count=1,
        emoji="🧪",
        sources=[source],
        notes=[note],
    )
    assert len(detail.sources) == 1
    assert len(detail.notes) == 1
    assert detail.sources[0].title == "Paper.pdf"


# ============================================================================
# 2. Token Scraping & Fallback
# ============================================================================

@pytest.mark.asyncio
async def test_scrape_tokens_success(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies scraping of SNlM0e, cfb2h, and FdrFJe from NotebookLM application."""
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL)
    try:
        ok = await client.scrape_tokens()
        assert ok is True
        assert client.access_token == WIZ_GLOBAL_DATA_NOTEBOOKLM["SNlM0e"]
        assert client.build_label == WIZ_GLOBAL_DATA_NOTEBOOKLM["cfb2h"]
        assert client.session_id == WIZ_GLOBAL_DATA_NOTEBOOKLM["FdrFJe"]
        assert client.is_offline_fallback is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_scrape_tokens_fallback_on_unreachable():
    """Verifies graceful fallback to fallback_snlm0e when NotebookLM host is unreachable."""
    client = NotebookLMClient(
        cookies={"__Secure-1PSID": "mock"},
        base_url="http://127.0.0.1:59999",  # unreachable port
        fallback_snlm0e="fallback_gemini_snlm0e_token_xyz",
        timeout=0.5,
    )
    try:
        ok = await client.scrape_tokens()
        assert ok is True
        assert client.access_token == "fallback_gemini_snlm0e_token_xyz"
        assert client.is_offline_fallback is True
    finally:
        await client.close()


# ============================================================================
# 3. RPC Calls (wXbhsf, rLM1Ne, CCqFvf, WWINqb, s0tc2d, trScbf)
# ============================================================================

@pytest.mark.asyncio
async def test_rpc_list_notebooks_wXbhsf(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies RPC wXbhsf enumerating user notebooks."""
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL, fallback_snlm0e="token")
    try:
        notebooks = await client.list_notebooks()
        assert len(notebooks) >= 2
        assert any("Quantum" in nb.title for nb in notebooks)
        assert any("Rust" in nb.title for nb in notebooks)
        q_nb = next(nb for nb in notebooks if "Quantum" in nb.title)
        assert q_nb.source_count == 2
        assert q_nb.emoji == "🧪"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rpc_get_notebook_rLM1Ne(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies RPC rLM1Ne fetching notebook details with attached sources and notes."""
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL, fallback_snlm0e="token")
    try:
        detail = await client.get_notebook("nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d")
        assert detail.id == "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"
        assert "Quantum Computing" in detail.title
        assert len(detail.sources) == 2
        assert len(detail.notes) == 1

        # Check source content
        pdf_src = detail.sources[0]
        assert pdf_src.id == "src_qcomp_01"
        assert "Introduction to Quantum Algorithms.pdf" in pdf_src.title
        assert pdf_src.source_type == 3
        assert pdf_src.url == "https://arxiv.org/pdf/quant-ph/0001001.pdf"
        assert "superposition" in pdf_src.summary

        # Check note content
        note = detail.notes[0]
        assert note.id == "note_qcomp_n1"
        assert "Open Research Questions" in note.title
        assert "qubit fidelity" in note.content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rpc_create_notebook_CCqFvf(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies RPC CCqFvf creating a new notebook."""
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL, fallback_snlm0e="token")
    try:
        new_nb = await client.create_notebook("Deep Learning Architectures")
        assert new_nb.id.startswith("nb_")
        assert new_nb.title == "Deep Learning Architectures"
        assert new_nb.source_count == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rpc_delete_notebook_WWINqb(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies RPC WWINqb deleting a notebook."""
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL, fallback_snlm0e="token")
    try:
        ok = await client.delete_notebook("nb_to_delete_123")
        assert ok is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rpc_rename_notebook_s0tc2d(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies RPC s0tc2d renaming a notebook."""
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL, fallback_snlm0e="token")
    try:
        ok = await client.rename_notebook("nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d", "Updated Quantum Title")
        assert ok is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rpc_add_text_source_trScbf(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies RPC trScbf adding custom text or research transcripts into a notebook."""
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL, fallback_snlm0e="token")
    try:
        source = await client.add_text_source(
            notebook_id="nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
            title="Custom Research Notes",
            content="Summary of Shor's quantum factoring performance.",
        )
        assert source.id.startswith("src_")
        assert source.title == "Custom Research Notes"
        assert source.source_type == 1
        assert "Shor" in source.summary
    finally:
        await client.close()


# ============================================================================
# 4. Mock Fault Injection & Error Recovery
# ============================================================================

@pytest.mark.asyncio
async def test_notebooklm_expired_session_code_7(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies that Code 7 from Google batchexecute raises AuthenticationError."""
    mock_server.fault.force_expired_session = True
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL, fallback_snlm0e="token")
    try:
        with pytest.raises(AuthenticationError):
            await client.list_notebooks()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_notebooklm_service_unavailable(mock_server: MockGeminiServer, mock_cookies: Dict[str, str]):
    """Verifies graceful detection when NotebookLM returns HTTP 503."""
    mock_server.fault.force_notebooklm_offline = True
    client = NotebookLMClient(cookies=mock_cookies, base_url=MOCK_BASE_URL, fallback_snlm0e="token")
    try:
        with pytest.raises(GeminiBridgeError):
            await client.list_notebooks()
    finally:
        await client.close()


# ============================================================================
# 5. REST API Router Endpoints (/api/notebooklm/*)
# ============================================================================

def test_api_get_status(test_app: TestClient):
    """GET /api/notebooklm/status returns bridge health."""
    resp = test_app.get("/api/notebooklm/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["authenticated"] is True


def test_api_list_notebooks(test_app: TestClient):
    """GET /api/notebooklm/notebooks lists user notebooks."""
    resp = test_app.get("/api/notebooklm/notebooks")
    assert resp.status_code == 200
    notebooks = resp.json()
    assert isinstance(notebooks, list)
    assert len(notebooks) >= 2
    assert "id" in notebooks[0]
    assert "title" in notebooks[0]
    assert "source_count" in notebooks[0]


def test_api_get_notebook_details(test_app: TestClient):
    """GET /api/notebooklm/notebooks/{id} returns notebook with sources and notes."""
    resp = test_app.get("/api/notebooklm/notebooks/nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"
    assert "sources" in detail
    assert "notes" in detail
    assert len(detail["sources"]) == 2
    assert len(detail["notes"]) == 1


def test_api_create_notebook(test_app: TestClient):
    """POST /api/notebooklm/notebooks creates a new notebook."""
    resp = test_app.post("/api/notebooklm/notebooks", json={"title": "FastAPI & Microservices"})
    assert resp.status_code == 201
    created = resp.json()
    assert created["title"] == "FastAPI & Microservices"
    assert created["id"].startswith("nb_")


def test_api_delete_notebook(test_app: TestClient):
    """DELETE /api/notebooklm/notebooks/{id} deletes a notebook."""
    resp = test_app.delete("/api/notebooklm/notebooks/nb_to_remove")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_api_rename_notebook(test_app: TestClient):
    """PATCH /api/notebooklm/notebooks/{id} renames a notebook."""
    resp = test_app.patch("/api/notebooklm/notebooks/nb_123", json={"title": "Updated Name"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["title"] == "Updated Name"


def test_api_attach_context(test_app: TestClient):
    """
    POST /api/notebooklm/attach-context packages sources into structured
    <notebooklm_reference> prompt block.
    """
    payload = {
        "notebook_id": "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
        "source_ids": ["src_qcomp_01"],
        "include_notes": True,
    }
    resp = test_app.post("/api/notebooklm/attach-context", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "formatted_context" in data
    assert data["source_count"] == 1
    assert data["note_count"] == 1
    assert data["estimated_tokens"] > 0

    xml = data["formatted_context"]
    assert '<notebooklm_reference title="' in xml
    assert '<source id="src_qcomp_01"' in xml
    assert "Introduction to Quantum Algorithms.pdf" in xml
    assert "</notebooklm_reference>" in xml


def test_api_export_direct_content(test_app: TestClient):
    """POST /api/notebooklm/export with explicit content adds text source."""
    payload = {
        "notebook_id": "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
        "title": "Quantum Error Mitigation Summary",
        "content": "Zero noise extrapolation and probabilistic error cancellation benchmarks.",
    }
    resp = test_app.post("/api/notebooklm/export", json=payload)
    assert resp.status_code == 200
    res = resp.json()
    assert res["success"] is True
    assert res["source"]["title"] == "Quantum Error Mitigation Summary"


@pytest.mark.asyncio
async def test_api_export_from_conversation(test_app: TestClient):
    """POST /api/notebooklm/export bundles SQLite conversation turns into a new notebook source."""
    # Seed a conversation in SQLite
    repo = app.state.repository
    conv = await repo.create_conversation(title="Rust Concurrency Benchmark", model="gemini-2.5-pro")

    u_msg = await repo.add_message(
        conversation_id=conv.id,
        parent_id=None,
        role="user",
        content="Explain Rust Rayon parallel iterators.",
        model="gemini-2.5-pro",
    )
    await repo.add_message(
        conversation_id=conv.id,
        parent_id=u_msg.id,
        role="assistant",
        content="Rayon provides work-stealing parallel iterators with into_par_iter().",
        model="gemini-2.5-pro",
    )

    payload = {
        "new_notebook_title": "Rust Concurrency Notebook",
        "conversation_id": conv.id,
    }
    resp = test_app.post("/api/notebooklm/export", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "notebook_id" in data
    assert "source" in data
    assert "Rust" in data["source"]["title"]
