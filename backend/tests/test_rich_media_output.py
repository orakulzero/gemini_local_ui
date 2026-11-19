"""
backend/tests/test_rich_media_output.py
Verification suite for rich media output:
1. StreamingDeltaAccumulator candidate[12]["7"] imagen_default.ultra wire payload normalization.
2. POST /api/chat/tts text-to-speech endpoint (Google RPC XqA3Ic -> audio/ogg).
3. POST /api/chat/generate image localization (/files/<hash>.png), GET /files/<hash>.png, & code execution persistence.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.image_downloader import GeminiImageDownloader
from backend.app.bridge.session import SessionManager
from backend.app.bridge.streaming_parser import StreamChunk, StreamingDeltaAccumulator
from backend.app.main import app
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository


@pytest.fixture
def rich_media_context(tmp_path: Path):
    db_file = tmp_path / "test_rich_media.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(db.init_schema(schema_path))
    repo = ConversationRepository(db)

    sm = SessionManager(base_url="http://127.0.0.1:8999")
    sm.context.cookies = {"__Secure-1PSID": "mock_psid", "__Secure-1PSIDTS": "mock_psidts"}
    sm.context.access_token = "mock_token"

    import backend.app.main as main_mod

    main_mod.db, main_mod.repository, main_mod.session_manager = db, repo, sm
    app.state.db, app.state.repository, app.state.session_manager = db, repo, sm
    app.state.image_data_dir = tmp_path / "images"

    client = TestClient(app, base_url="http://testserver")
    yield client, repo, db, sm, tmp_path / "images"
    asyncio.run(db.close())


def parse_sse_events(response_text: str) -> List[Dict[str, Any]]:
    return [
        json.loads(line[5:].strip())
        for line in response_text.splitlines()
        if line.strip().startswith("data:") and line[5:].strip()
    ]


def test_streaming_delta_accumulator_imagen_default_ultra_wire_payload():
    """Validates normalization of real Google imagen_default.ultra candidate[12]['7'] nested envelope."""
    acc = StreamingDeltaAccumulator()
    wire_payload = [[
        [None, None, None, [None, 1, "watermarked_img_1.jpg", "https://lh3.googleusercontent.com/gg-dl/test123",
                            None, "$sig", None, None, None, [1795001691, 0], None, "image/jpeg", None, None, None,
                            [1376, 768, 598036]]],
        ["http://googleusercontent.com/image_generation_content/0_979"],
        None, [23], None, None, None, None, "im_b601a42f243fe109",
    ]]

    cand = [
        "rcid_img",
        ["Here is your image: http://googleusercontent.com/image_generation_content/0_979 Enjoy!"],
        None, None, None, None, None, None, [2], None, None, None,
        {"7": [wire_payload, None]},
    ]
    inner = [None, ["c_img_1", "r_img_1", "rcid_img"], None, None, [cand]]
    chunks = acc.process_frame([["wrb.fr", None, json.dumps(inner)]])
    done_chunk = next(c for c in chunks if c.type == "done")
    images = done_chunk.metadata["images"]

    assert len(images) == 1
    assert images[0]["url"] == "https://lh3.googleusercontent.com/gg-dl/test123"
    assert images[0]["width"] == 1376
    assert images[0]["height"] == 768
    assert images[0]["image_id"] == "im_b601a42f243fe109"
    assert "http://googleusercontent.com/image_generation_content/0_979" not in acc.full_raw_text
    assert "image_generation_content/0_979" not in done_chunk.metadata["full_text"]


def test_post_chat_tts_endpoint(rich_media_context):
    """Validates POST /api/chat/tts returns HTTP 200 with audio/ogg OggS stream."""
    client, _, _, _, _ = rich_media_context
    mock_audio_bytes = b"OggS\x00\x02mock_opus_stream_data_test_12345678"
    mock_b64 = base64.b64encode(mock_audio_bytes).decode("ascii")

    with patch.object(
        GeminiWebClient, "call_batchexecute", new_callable=AsyncMock, return_value=[mock_b64]
    ):
        resp = client.post(
            "/api/chat/tts",
            json={"text": "Привет, это синтез речи Google Ultra.", "language": "ru-RU"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/ogg")
    assert resp.content.startswith(b"OggS")
    assert len(resp.content) == len(mock_audio_bytes)


def test_chat_generate_localizes_images_and_persists_code_executions(rich_media_context):
    """Validates /api/chat/generate localizes gg-dl images to /files/<hash>.png and persists code executions in DB."""
    client, repo, _, _, _ = rich_media_context
    mock_png_bytes = b"\x89PNG\r\n\x1a\nmock_rendered_png_bytes_42"

    async def mock_stream(*args, **kwargs) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(type="text", delta="Computing and generating visual...")
        yield StreamChunk(
            type="done",
            delta="",
            metadata={
                "cid": "c_rich_1",
                "rid": "r_rich_1",
                "rcid": "rc_rich_1",
                "full_text": "Computing and generating visual...",
                "images": [{
                    "url": "https://lh3.googleusercontent.com/gg-dl/mock_remote_img",
                    "image_id": "im_test_cube_1",
                    "placeholder": "http://googleusercontent.com/image_generation_content/0_1",
                    "alt": "Neon Blue Cube",
                }],
                "code_executions": [{
                    "language": "python",
                    "code": "print(42 * 10)",
                    "stdout": "420\n",
                    "stderr": "",
                    "outcome": "OUTCOME_OK",
                }],
            },
        )

    with patch.object(GeminiWebClient, "generate_stream", side_effect=mock_stream), patch.object(
        GeminiImageDownloader, "download_image", new_callable=AsyncMock, return_value=(mock_png_bytes, "image/png")
    ):
        resp = client.post(
            "/api/chat/generate",
            json={"prompt": "Generate a neon blue cube and run python code", "model": "gemini-2.5-pro"},
        )

    assert resp.status_code == 200
    events = parse_sse_events(resp.text)
    done_event = next(e for e in events if e.get("type") == "done")
    done_meta = done_event["metadata"]

    localized_images = done_meta["images"]
    assert len(localized_images) == 1
    local_url = localized_images[0]["url"]
    assert local_url.startswith("/files/")
    assert local_url.endswith(".png")
    assert localized_images[0]["original_url"] == "https://lh3.googleusercontent.com/gg-dl/mock_remote_img"

    assert "code_executions" in done_meta
    assert done_meta["code_executions"][0]["stdout"] == "420\n"

    get_file_resp = client.get(local_url)
    assert get_file_resp.status_code == 200
    assert get_file_resp.headers["content-type"].startswith("image/png")
    assert get_file_resp.content == mock_png_bytes

    conv_list, _ = asyncio.run(repo.list_conversations())
    messages = asyncio.run(repo.get_all_messages(conv_list[0].id))
    asst_msg = next(m for m in messages if m.role == "assistant")
    assert "code_executions" in asst_msg.metadata
    assert asst_msg.metadata["code_executions"][0]["code"] == "print(42 * 10)"
    assert asst_msg.metadata["images"][0]["url"] == local_url
