"""
backend/tests/test_m5_rich_media_and_canvas_challenge.py
Adversarial challenge and empirical stress-test suite for Milestone M5 (Challenger 2):
1. Candidate[12] Robustness: Corrupted/missing citations, malformed images, missing dimensions, long alt text.
2. YouTube Parsing & Extraction: Timestamps, unusual query params, Shorts, structured vs regex deduplication.
3. Metadata Round-Trip: SQLite serialization/deserialization of rich streaming metadata (citations, grounding, media).
4. Gems API Schema: Verification of GET /api/gems (all/custom/system) and GemSerializer format handling.
5. Canvas Panel Auditing: Iframe sandbox security, SVG sanitization/script-stripping check, download MIME types.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from typing import Any, Dict, List
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.streaming_parser import StreamingDeltaAccumulator, _YOUTUBE_RE
from backend.app.bridge.models import Gem, GemSerializer
from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository


MOCK_PORT = 8996
MOCK_HOST = "127.0.0.1"
MOCK_BASE_URL = f"http://{MOCK_HOST}:{MOCK_PORT}"


@pytest.fixture(scope="module")
def mock_server():
    server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
    server.start_background()
    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock(mock_server: MockGeminiServer):
    mock_server.fault.reset()


@pytest.fixture
def m5_challenger_context(tmp_path: Path, mock_server: MockGeminiServer):
    db_file = tmp_path / "test_m5_challenger2.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(db.init_schema(schema_path))

    repo = ConversationRepository(db)

    sm = SessionManager(base_url=MOCK_BASE_URL)
    sm.context.cookies = {
        "__Secure-1PSID": "mock_psid_valid_test_token_12345",
        "__Secure-1PSIDTS": "mock_psidts_initial_timestamp_67890",
    }
    sm.context.access_token = "mock_token_abc"

    import backend.app.main as main_mod
    main_mod.db = db
    main_mod.repository = repo
    main_mod.session_manager = sm

    app.state.db = db
    app.state.repository = repo
    app.state.session_manager = sm

    client = TestClient(app, base_url="http://testserver")
    yield client, repo, db, sm
    asyncio.run(db.close())


# ============================================================================
# 1. Candidate[12] Robustness: Malformed & Corrupted Structures
# ============================================================================

class TestCandidate12Robustness:
    """Stress-test candidate[12] against malformed, partial, and hostile payloads."""

    @pytest.mark.parametrize("corrupted_c12", [
        None,
        12345,
        "unexpected_string",
        True,
        False,
        [],
        [None, None],
        [1, 2, 3],
        {"non_existent_key": "some_value"},
    ])
    def test_candidate_12_scalar_and_unsupported_structures(self, corrupted_c12: Any):
        """Accumulator must never raise an unhandled exception on arbitrary candidate[12] shapes."""
        acc = StreamingDeltaAccumulator()
        cand = ["rcid_1", ["Normal text response"], None, None, None, None, None, None, [2], None, None, None, corrupted_c12]
        inner = [None, ["c1", "r1", "rcid_1"], None, None, [cand]]
        frame = [["wrb.fr", None, json.dumps(inner)]]

        chunks = acc.process_frame(frame)
        text_chunk = next((c for c in chunks if c.type == "text"), None)
        assert text_chunk is not None
        assert "Normal text" in text_chunk.delta
        done_chunk = next((c for c in chunks if c.type == "done"), None)
        assert done_chunk is not None

    def test_citations_field_corrupted_and_partial(self):
        """Verify normalization of corrupted, incomplete, and varied citations in field '43'."""
        acc = StreamingDeltaAccumulator()
        weird_citations = [
            None,
            "corrupted_non_list_entry",
            [],
            [1],  # index only, missing URL
            [None, None, None],
            [1, "https://docs.python.org/3/library/asyncio.html", "Python AsyncIO", "docs.python.org", "Async I/O snippet"],
            [2, "https://developer.mozilla.org/en-US/docs/Web", "MDN Web Docs", None, None],  # missing domain (should extract)
            [3, "://broken-url-format", "Broken URL", None, None],
            {"index": 4, "url": "https://direct.dict.org", "title": "Direct Dict", "domain": "direct.dict.org"},
        ]

        cand = [
            "rcid_cit",
            ["Citations answer"],
            None, None, None, None, None, None,
            [2],
            None, None, None,
            {"43": weird_citations},
        ]
        inner = [None, ["c_cit", "r_cit", "rcid_cit"], None, None, [cand]]
        frame = [["wrb.fr", None, json.dumps(inner)]]

        chunks = acc.process_frame(frame)
        done_chunk = next(c for c in chunks if c.type == "done")
        cits = done_chunk.metadata["citations"]
        assert isinstance(cits, list)

        # MDN domain should be extracted from url
        mdn_cit = next((c for c in cits if "developer.mozilla.org" in c.get("url", "")), None)
        assert mdn_cit is not None
        assert mdn_cit["domain"] == "developer.mozilla.org"

        # Direct dict should be preserved
        dict_cit = next((c for c in cits if c.get("url") == "https://direct.dict.org"), None)
        assert dict_cit is not None
        assert dict_cit["title"] == "Direct Dict"

    def test_images_field_missing_dimensions_and_extreme_alt(self):
        """Verify normalization of image objects with missing dimensions, string dimensions, and 50KB alt text."""
        acc = StreamingDeltaAccumulator()
        giant_alt = "ALT_" + ("x" * 50_000)
        weird_images = [
            None,
            "corrupted_image_string",
            [],
            ["https://images.google.com/img1.png"],  # no alt, no width, no height
            ["https://images.google.com/img2.png", giant_alt],  # giant alt, no dimensions
            ["https://images.google.com/img3.png", "Valid Alt", "not_int_width", "not_int_height"],  # non-int dims
            ["https://images.google.com/img4.png", "Standard", 1920, 1080],  # valid dimensions
            {"url": "https://images.google.com/img5.png", "alt": "Direct Dict Image", "width": 800, "height": 600},
        ]

        cand = [
            "rcid_img",
            ["Image generation answer"],
            None, None, None, None, None, None,
            [2],
            None, None, None,
            {"7": weird_images},
        ]
        inner = [None, ["c_img", "r_img", "rcid_img"], None, None, [cand]]
        frame = [["wrb.fr", None, json.dumps(inner)]]

        chunks = acc.process_frame(frame)
        done_chunk = next(c for c in chunks if c.type == "done")
        imgs = done_chunk.metadata["images"]
        assert isinstance(imgs, list)

        # img1 has None for width and height
        img1 = next(i for i in imgs if i.get("url") == "https://images.google.com/img1.png")
        assert img1["width"] is None
        assert img1["height"] is None

        # img2 has giant alt text preserved
        img2 = next(i for i in imgs if i.get("url") == "https://images.google.com/img2.png")
        assert len(img2["alt"]) == 50_004

        # img3 with string dimensions has them normalized to None
        img3 = next(i for i in imgs if i.get("url") == "https://images.google.com/img3.png")
        assert img3["width"] is None
        assert img3["height"] is None

        # img4 has int dimensions
        img4 = next(i for i in imgs if i.get("url") == "https://images.google.com/img4.png")
        assert img4["width"] == 1920
        assert img4["height"] == 1080


# ============================================================================
# 2. YouTube Parsing & Regex Parameter Challenges
# ============================================================================

class TestYouTubeExtraction:
    """Stress-test regex and structured extraction of YouTube video links."""

    def test_youtube_url_parameters_and_variations(self):
        """Evaluate _YOUTUBE_RE coverage against timestamps, unusual params, and Shorts."""
        # 1. Standard watch URL
        m1 = _YOUTUBE_RE.search("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert m1 is not None and m1.group(1) == "dQw4w9WgXcQ"

        # 2. youtu.be short URL with timestamp
        m2 = _YOUTUBE_RE.search("https://youtu.be/dQw4w9WgXcQ?t=45s")
        assert m2 is not None and m2.group(1) == "dQw4w9WgXcQ"

        # 3. watch URL with timestamp following v parameter
        m3 = _YOUTUBE_RE.search("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=45s&feature=shared")
        assert m3 is not None and m3.group(1) == "dQw4w9WgXcQ"

        # 4. Unusual parameter order where feature=shared comes BEFORE v=
        m4 = _YOUTUBE_RE.search("https://www.youtube.com/watch?feature=shared&v=dQw4w9WgXcQ&t=45s")
        assert m4 is not None and m4.group(1) == "dQw4w9WgXcQ"

        # 5. YouTube Shorts URL
        m5 = _YOUTUBE_RE.search("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        assert m5 is not None and m5.group(1) == "dQw4w9WgXcQ"

    def test_youtube_structured_and_stream_deduplication(self):
        """Verify that identical video IDs appearing in text and rich candidate[12] are deduplicated."""
        acc = StreamingDeltaAccumulator()
        text = "Watch this video: https://www.youtube.com/watch?v=dQw4w9WgXcQ and again at https://youtu.be/dQw4w9WgXcQ"
        rich_yt = [
            {"video_id": "dQw4w9WgXcQ", "title": "Rickroll", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            {"video_id": "otherVid1234", "title": "Another Video"},
        ]

        cand = [
            "rcid_yt",
            [text],
            None, None, None, None, None, None,
            [2],
            None, None, None,
            {"youtube": rich_yt},
        ]
        inner = [None, ["c_yt", "r_yt", "rcid_yt"], None, None, [cand]]
        frame = [["wrb.fr", None, json.dumps(inner)]]

        chunks = acc.process_frame(frame)
        done_chunk = next(c for c in chunks if c.type == "done")
        yt_vids = done_chunk.metadata["youtube"]

        # dQw4w9WgXcQ should appear exactly once
        rickrolls = [y for y in yt_vids if y["video_id"] == "dQw4w9WgXcQ"]
        assert len(rickrolls) == 1, f"Expected 1 deduplicated entry for dQw4w9WgXcQ, got {len(rickrolls)}"
        assert any(y["video_id"] == "otherVid1234" for y in yt_vids)


# ============================================================================
# 3. Metadata Round-Trip SQLite Verification
# ============================================================================

class TestMetadataJsonRoundTrip:
    """Verify that messages.metadata_json round-trips with zero data loss or type corruption."""

    @pytest.mark.asyncio
    async def test_messages_metadata_json_lossless_roundtrip(self, m5_challenger_context):
        _, repo, db, _ = m5_challenger_context
        conv = await repo.create_conversation(title="Full Metadata Roundtrip Test")

        complex_metadata = {
            "cid": "c_complex_999",
            "rid": "r_complex_888",
            "rcid": "rcid_complex_777",
            "continuation_token": "cont_tok_666",
            "citations": [
                {
                    "index": 1,
                    "url": "https://en.wikipedia.org/wiki/Special:Search?search=test&go=Go",
                    "title": "Wikipedia — the free encyclopedia",
                    "domain": "ru.wikipedia.org",
                    "snippet": "Service snippet with quotes \" ' and special chars <>& \n \t",
                },
                {
                    "index": 2,
                    "url": "https://docs.python.org/3/",
                    "title": "Python Documentation 🚀",
                    "domain": "docs.python.org",
                    "snippet": None,
                },
            ],
            "images": [
                {
                    "url": "https://lh3.googleusercontent.com/img_highres_999",
                    "alt": "Architecture diagram ⚡",
                    "width": 3840,
                    "height": 2160,
                },
                {
                    "url": "https://lh3.googleusercontent.com/img_no_dims",
                    "alt": "",
                    "width": None,
                    "height": None,
                },
            ],
            "grounding": {
                "sources": [
                    {
                        "url": "https://grounding.example.com/item1",
                        "title": "Grounding Source 1",
                        "snippet": "Snippet 1",
                    }
                ],
                "queries": ["gemini local ui", "sqlite fts5", "neural networks 2026"],
            },
            "youtube": [
                {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Rick Astley - Never Gonna Give You Up",
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
                    "channel": "RickAstleyVEVO",
                }
            ],
            "canvas_artifacts": [
                {
                    "id": "art_svg_1",
                    "title": "chart.svg",
                    "language": "svg",
                    "content": '<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="red"/></svg>',
                }
            ],
            "custom_metadata_primitives": {
                "bool_true": True,
                "bool_false": False,
                "none_val": None,
                "float_val": 3.1415926535,
                "int_large": 9223372036854775800,
                "nested_array": [1, [2, [3, "deep"]]],
            },
        }

        # 1. Insert message
        msg = await repo.add_message(
            conversation_id=conv.id,
            role="assistant",
            content="Testing complex metadata round-trip.",
            metadata=complex_metadata,
        )

        # 2. Verify get_message
        m1 = await repo.get_message(msg.id)
        assert m1 is not None
        assert m1.metadata == complex_metadata

        # 3. Verify get_all_messages
        all_msgs = await repo.get_all_messages(conv.id)
        assert len(all_msgs) == 1
        assert all_msgs[0].metadata == complex_metadata

        # 4. Verify get_active_thread
        thread = await repo.get_active_thread(conv.id)
        assert len(thread) == 1
        assert thread[0].metadata == complex_metadata

        # 5. Direct raw SQL check on the metadata_json column
        raw_row = await db.fetch_one("SELECT metadata_json FROM messages WHERE id = ?", (msg.id,))
        assert raw_row is not None
        assert json.loads(raw_row["metadata_json"]) == complex_metadata


# ============================================================================
# 4. Gems API Schema & Serializer Verification
# ============================================================================

class TestGemsAPIAndSerialization:
    """Verify GET /api/gems endpoints, query filtering, and GemSerializer schemas."""

    def test_gems_api_filter_and_response_schema(self, m5_challenger_context):
        client, _, _, _ = m5_challenger_context

        for type_param in ("all", "custom", "system"):
            res = client.get(f"/api/gems?type={type_param}")
            assert res.status_code == 200, f"GET /api/gems?type={type_param} failed with {res.status_code}"
            data = res.json()
            assert "gems" in data
            assert "custom_gems" in data
            assert "system_gems" in data
            assert isinstance(data["gems"], list)
            assert isinstance(data["custom_gems"], list)
            assert isinstance(data["system_gems"], list)

            if type_param == "custom":
                assert len(data["system_gems"]) == 0
                assert data["gems"] == data["custom_gems"]
            elif type_param == "system":
                assert len(data["custom_gems"]) == 0
                assert data["gems"] == data["system_gems"]

    def test_gem_serializer_payload_and_parsing(self):
        """Verify GemSerializer generates correct CNgdBe payloads and parses envelopes."""
        # 1. Payload construction
        custom_payload = GemSerializer.build_list_payload(is_custom=True, language="ru")
        assert custom_payload == [2, ["ru"], 0]
        system_payload = GemSerializer.build_list_payload(is_custom=False, language="en")
        assert system_payload == [3, ["en"], 0]

        # 2. Parsing nested CNgdBe structure
        sample_response = [
            None,
            [
                ["gem_1", ["Code Helper", "Expert dev"], ["You write clean code."], 1700000000, "💻"],
                ["gem_2", "Math Solver", "Solves calculus", "Show full steps.", 1700000001, "📐"],
            ],
        ]
        gems = GemSerializer.parse_list_response(sample_response, is_builtin=False)
        assert len(gems) == 2
        assert gems[0].id == "gem_1"
        assert gems[0].name == "Code Helper"
        assert gems[0].description == "Expert dev"
        assert gems[0].prompt == "You write clean code."
        assert gems[0].icon == "💻"
        assert gems[0].is_builtin is False

        # Gem.to_dict schema includes both name/title and prompt/system_prompt
        d0 = gems[0].to_dict()
        assert d0["name"] == d0["title"] == "Code Helper"
        assert d0["prompt"] == d0["system_prompt"] == "You write clean code."
        assert d0["is_builtin"] is False


# ============================================================================
# 5. Canvas Panel Auditing: Security, Sandboxing, and MIME Types
# ============================================================================

class TestCanvasSecurityAndMimeAudit:
    """Empirically audit frontend/src/components/CanvasPanel.tsx for security and MIME compliance."""

    @pytest.fixture
    def canvas_panel_code(self) -> str:
        canvas_path = Path(__file__).parent.parent.parent / "frontend" / "src" / "components" / "CanvasPanel.tsx"
        assert canvas_path.exists(), f"CanvasPanel.tsx not found at {canvas_path}"
        return canvas_path.read_text(encoding="utf-8")

    def test_html_iframe_sandbox_security_policy(self, canvas_panel_code: str):
        """Confirm that HTML preview uses sandbox='allow-scripts' without 'allow-same-origin'."""
        assert 'sandbox="allow-scripts"' in canvas_panel_code, "CanvasPanel must specify sandbox='allow-scripts'"
        # CRUCIAL SECURITY CHECK: 'allow-same-origin' must NOT be present alongside 'allow-scripts'
        assert "allow-same-origin" not in canvas_panel_code, (
            "SECURITY VULNERABILITY: allow-same-origin combined with allow-scripts allows sandbox escape!"
        )

    def test_svg_preview_sanitization_vulnerability(self, canvas_panel_code: str):
        """
        Remediation Check: Verify SVG preview in CanvasPanel strips scripts or sandboxes.
        Verifies that dangerouslySetInnerHTML is NOT used directly on unsanitized SVG content.
        """
        # Look for SVG rendering in CanvasPanel.tsx
        has_dangerously_set_inner_html = "dangerouslySetInnerHTML={{ __html: activeArtifact.content }}" in canvas_panel_code
        has_svg_sanitizer = "DOMPurify" in canvas_panel_code or "sanitize" in canvas_panel_code
        has_svg_iframe = 'sandbox=' in canvas_panel_code and 'isSvg' in canvas_panel_code

        # Confirm remediation: SVG must be sandboxed or sanitized, not raw dangerouslySetInnerHTML
        assert not has_dangerously_set_inner_html, "CanvasPanel.tsx must not use dangerouslySetInnerHTML for SVG"
        assert has_svg_iframe or has_svg_sanitizer, "CanvasPanel.tsx must sandbox or sanitize SVG content"

    def test_canvas_download_mime_map_python_omission(self, canvas_panel_code: str):
        """
        Remediation Check: Verify MIME_MAP in CanvasPanel.tsx.
        Requirement R3 / Mission requires correct MIME types for:
        text/html, image/svg+xml, text/markdown, text/x-python.
        """
        # Extract MIME_MAP definition block
        match = re.search(r"const MIME_MAP: Record<string, string> = \{([^}]+)\};", canvas_panel_code)
        assert match is not None, "MIME_MAP not found in CanvasPanel.tsx"
        mime_map_text = match.group(1)

        assert "html: 'text/html'" in mime_map_text
        assert "svg: 'image/svg+xml'" in mime_map_text
        assert "markdown: 'text/markdown'" in mime_map_text

        # Check Python: 'python' and 'py' must be in MIME_MAP
        has_python_mime = "'text/x-python'" in mime_map_text or '"text/x-python"' in mime_map_text
        assert has_python_mime, (
            "CanvasPanel.tsx MIME_MAP must include text/x-python for python/py"
        )
