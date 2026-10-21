"""
backend/tests/test_openai_api.py
Automated conformance test suite verifying local OpenAI API proxy using official openai SDK v2.30.0.
"""

from __future__ import annotations

import asyncio
import json
import time
import httpx
from openai import AsyncOpenAI, BadRequestError, NotFoundError, OpenAI
import pytest

from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.openai.routes import router as openai_router

MOCK_PORT = 8999
MOCK_HOST = "127.0.0.1"
MOCK_BASE_URL = f"http://{MOCK_HOST}:{MOCK_PORT}"


@pytest.fixture(scope="module")
def mock_server():
    """Starts deterministic mock Gemini web server in a background thread."""
    server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
    server.start_background()

    for _ in range(50):
        try:
            r = httpx.get(f"{MOCK_BASE_URL}/app", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)

    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock(mock_server: MockGeminiServer):
    mock_server.fault.reset()


@pytest.fixture(scope="module")
def configured_app(mock_server: MockGeminiServer):
    """Configures FastAPI application with routes and mock session manager."""
    # Initialize session manager pointing to mock server
    sm = SessionManager(base_url=MOCK_BASE_URL)
    sm.context.cookies = {
        "__Secure-1PSID": "mock_psid_valid_test_token_12345",
        "__Secure-1PSIDTS": "mock_psidts_initial_timestamp_67890",
    }
    sm.context.access_token = "mock_snlm0e_access_token_abc123"

    import backend.app.main as main_mod
    main_mod.session_manager = sm
    app.state.session_manager = sm

    return app


@pytest.fixture
def openai_client(configured_app) -> OpenAI:
    """Creates a synchronous OpenAI SDK client connected via TestClient."""
    from starlette.testclient import TestClient
    http_client = TestClient(configured_app, base_url="http://testserver/v1")
    return OpenAI(
        base_url="http://testserver/v1",
        api_key="dummy-gemini-local-key",
        http_client=http_client,
    )


@pytest.fixture
def async_openai_client(configured_app) -> AsyncOpenAI:
    """Creates an asynchronous AsyncOpenAI SDK client connected via ASGITransport."""
    transport = httpx.ASGITransport(app=configured_app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver/v1")
    return AsyncOpenAI(
        base_url="http://testserver/v1",
        api_key="dummy-gemini-local-key",
        http_client=http_client,
    )


# ============================================================================
# 1. Models List Tests (/v1/models)
# ============================================================================

def test_openai_models_list(openai_client: OpenAI):
    """Verifies client.models.list() returns valid OpenAI model catalog."""
    models_page = openai_client.models.list()
    model_ids = [m.id for m in models_page.data]

    # Check canonical Gemini models present
    assert "gemini-2.5-flash" in model_ids
    assert "gemini-2.5-pro" in model_ids
    assert "gemini-2.0-flash-thinking" in model_ids

    # Check drop-in compatibility aliases present
    assert "gpt-4o" in model_ids
    assert "gpt-3.5-turbo" in model_ids
    assert "o1" in model_ids

    # Check model object attributes
    first_model = models_page.data[0]
    assert first_model.object == "model"
    assert first_model.created > 0
    assert first_model.owned_by in ("google-gemini", "google-gemini-alias", "google-gemini-gem")


# ============================================================================
# 2. Non-Streaming Chat Completion Tests (stream=False)
# ============================================================================

def test_openai_chat_completion_non_streaming(openai_client: OpenAI):
    """Verifies non-streaming chat completion produces standard ChatCompletion response."""
    response = openai_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "Explain Windows DPAPI"}],
        temperature=0.7,
        stream=False,
    )

    assert response.id.startswith("chatcmpl-")
    assert response.object == "chat.completion"
    assert response.model == "gemini-2.5-flash"
    assert len(response.choices) == 1

    choice = response.choices[0]
    assert choice.index == 0
    assert choice.message.role == "assistant"
    assert "Windows **DPAPI**" in choice.message.content
    assert choice.finish_reason == "stop"

    assert response.usage.prompt_tokens > 0
    assert response.usage.completion_tokens > 0
    assert response.usage.total_tokens == response.usage.prompt_tokens + response.usage.completion_tokens


def test_openai_alias_resolution_gpt4o(openai_client: OpenAI):
    """Verifies that calling gpt-4o resolves cleanly to gemini-2.5-pro."""
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False,
    )
    assert response.choices[0].message.role == "assistant"
    assert len(response.choices[0].message.content) > 0


def test_openai_task_mode_resolution(openai_client: OpenAI):
    """Verifies that calling model with task alias or @mode=task modifier succeeds."""
    response = openai_client.chat.completions.create(
        model="gemini-4-argon@mode=task",
        messages=[{"role": "user", "content": "Formulate a multi-step task plan"}],
        stream=False,
    )
    assert response.choices[0].message.role == "assistant"
    assert len(response.choices[0].message.content) > 0

    response_alias = openai_client.chat.completions.create(
        model="gemini-task",
        messages=[{"role": "user", "content": "Execute task plan"}],
        stream=False,
    )
    assert response_alias.choices[0].message.role == "assistant"
    assert len(response_alias.choices[0].message.content) > 0


# ============================================================================
# 3. Streaming Chat Completion Tests (stream=True)
# ============================================================================

def test_openai_chat_completion_streaming(openai_client: OpenAI):
    """Verifies streaming chat completion yields ChatCompletionChunk events culminating in stop."""
    stream = openai_client.chat.completions.create(
        model="gemini-2.0-flash-thinking",
        messages=[{"role": "user", "content": "Stream explanation"}],
        stream=True,
    )

    accumulated_content = []
    finish_reasons = []

    for chunk in stream:
        assert chunk.object == "chat.completion.chunk"
        assert chunk.id.startswith("chatcmpl-")
        if chunk.choices:
            c = chunk.choices[0]
            if c.delta.content:
                accumulated_content.append(c.delta.content)
            if c.finish_reason:
                finish_reasons.append(c.finish_reason)

    full_text = "".join(accumulated_content)
    assert "Windows **DPAPI**" in full_text
    assert "stop" in finish_reasons


# ============================================================================
# 4. System Message & Multi-Turn Tests
# ============================================================================

def test_openai_system_message_and_multi_turn(openai_client: OpenAI, mock_server: MockGeminiServer):
    """Verifies system instruction is extracted and prepended to prompt context."""
    mock_server.fault.recorded_requests.clear()
    response = openai_client.chat.completions.create(
        model="gemini-2.5-pro",
        messages=[
            {"role": "system", "content": "You are a cyber security architect."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "What is DPAPI?"},
        ],
        stream=False,
    )
    assert response.choices[0].message.content

    recorded = mock_server.fault.recorded_requests
    stream_req = [r for r in recorded if "StreamGenerate" in r["path"]][-1]
    f_req = json.loads(stream_req["body_params"]["f.req"])
    inner = json.loads(f_req[1])
    prompt_sent = inner[0][0]
    assert "System Instruction" in prompt_sent or "cyber security architect" in prompt_sent


def test_openai_consecutive_same_role_coalescing(openai_client: OpenAI):
    """Verifies consecutive user turns are coalesced into a single user turn."""
    response = openai_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[
            {"role": "user", "content": "First line"},
            {"role": "user", "content": "Second line"},
        ],
        stream=False,
    )
    assert response.choices[0].message.content


# ============================================================================
# 5. Multimodal Vision Tests (Data URI and Remote URL)
# ============================================================================

def test_openai_multimodal_vision_base64(openai_client: OpenAI, mock_server: MockGeminiServer):
    """Verifies that OpenAI image_url base64 data URI triggers upload and attachment formatting."""
    mock_server.fault.recorded_requests.clear()
    # 1x1 transparent PNG
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    data_uri = f"data:image/png;base64,{png_b64}"

    response = openai_client.chat.completions.create(
        model="gemini-2.5-pro",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image."},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }],
        stream=False,
    )
    assert response.choices[0].message.content

    # Verify upload was called on mock server
    recorded = mock_server.fault.recorded_requests
    upload_req = [r for r in recorded if "/upload" in r["path"]][-1]
    assert upload_req is not None

    # Verify StreamGenerate received the attachment URI in inner[0][3]
    stream_req = [r for r in recorded if "StreamGenerate" in r["path"]][-1]
    f_req = json.loads(stream_req["body_params"]["f.req"])
    inner = json.loads(f_req[1])
    attachments_block = inner[0][3]
    assert attachments_block is not None
    assert "/contrib_service/" in attachments_block[0][0][0]


# ============================================================================
# 6. Error Handling & Validation Tests
# ============================================================================

def test_openai_unknown_model_not_found(openai_client: OpenAI):
    """Verifies unknown model triggers HTTP 404 NotFoundError."""
    with pytest.raises(NotFoundError) as exc_info:
        openai_client.chat.completions.create(
            model="non-existent-fantasy-model",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert exc_info.value.status_code == 404


def test_openai_empty_messages_bad_request(openai_client: OpenAI):
    """Verifies empty messages array triggers HTTP 400 BadRequestError."""
    with pytest.raises(BadRequestError) as exc_info:
        openai_client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[],
        )
    assert exc_info.value.status_code == 400


# ============================================================================
# 7. Asynchronous OpenAI Client Tests (AsyncOpenAI)
# ============================================================================

@pytest.mark.asyncio
async def test_async_openai_streaming_and_non_streaming(async_openai_client: AsyncOpenAI):
    """Verifies AsyncOpenAI client works for both non-streaming and streaming."""
    # 1. Non-streaming
    resp = await async_openai_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "Async test"}],
        stream=False,
    )
    assert resp.choices[0].message.role == "assistant"

    # 2. Streaming
    stream = await async_openai_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "Async stream"}],
        stream=True,
    )
    tokens = []
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            tokens.append(chunk.choices[0].delta.content)
    assert len(tokens) > 0
