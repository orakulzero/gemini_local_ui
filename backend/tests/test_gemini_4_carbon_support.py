"""Agile V locked tests for Gemini 4 Carbon routing & dynamic hex preservation (REQ-FORGE-CARBON-01)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import (
    DEFAULT_MODELS,
    HeaderBuilder,
    OPENAI_MODEL_ALIASES,
    register_discovered_carbon_model,
    resolve_model,
)
from backend.app.bridge.session import SessionContext
from backend.app.openai.adapter import OpenAIAdapter


def test_req_forge_carbon_01_aliases_and_no_argon_tag_leak() -> None:
    """REQ-FORGE-CARBON-01: Carbon aliases resolve cleanly with model_number=8, thinking_flag=2, and no Argon4p0 tag."""
    for alias in ("gemini-4-carbon", "4-carbon", "4 Carbon", "carbon"):
        assert alias in OPENAI_MODEL_ALIASES or alias in DEFAULT_MODELS
        info = resolve_model(alias)
        assert info.model_number == 8
        assert info.thinking_flag == 2
        assert info.capacity_tail == 15
        assert info.mode_tag is None

    canonical, gem_id = OpenAIAdapter.resolve_model("carbon@mode=deep-research")
    assert gem_id is None
    assert canonical.endswith("@mode=deep-research")
    dr_info = resolve_model(canonical)
    assert dr_info.feature_mode == 40
    assert dr_info.model_number == 8
    assert dr_info.mode_tag is None


def test_req_forge_carbon_01_compact_header_preserves_discovered_16hex() -> None:
    """REQ-FORGE-CARBON-01: Dynamic 16-hex IDs with model_number=8 must NOT be overwritten to Argon's 95a4c0abcba24364."""
    discovered_carbon_hex = "c4ab0900deadbeef"
    register_discovered_carbon_model(
        discovered_carbon_hex,
        display_name="4 Carbon",
        description="Most complex work",
        model_number=8,
        thinking_flag=2,
    )

    compact_raw = HeaderBuilder.build_compact_model_jspb_header("carbon")
    parsed_compact = json.loads(compact_raw)
    assert parsed_compact[4] == discovered_carbon_hex
    assert parsed_compact[4] != "95a4c0abcba24364"

    stream_hdr_raw = HeaderBuilder.build_model_jspb_header("4 Carbon", "SESS-UUID-1")
    parsed_stream = json.loads(stream_hdr_raw)
    assert parsed_stream[4] == discovered_carbon_hex
    assert parsed_stream[14] == 8
    assert parsed_stream[15] == 2
    assert len(parsed_stream) == 17  # 17 elements (no trailing Argon4p0 field 25)


@pytest.mark.asyncio
async def test_req_forge_carbon_01_live_otaq7b_discovery_binds_carbon() -> None:
    """REQ-FORGE-CARBON-01: GeminiWebClient.list_models binds live otAQ7b 4 Carbon entry to canonical registry."""
    ctx = SessionContext(cookies={"__Secure-1PSID": "test_psid"})
    client = GeminiWebClient(session_context=ctx, base_url="https://gemini.google.com")
    fake_carbon_hex = "a1b2c3d4e5f60718"
    client.get_user_status = AsyncMock(
        return_value={
            "raw": [
                *([None] * 15),
                [
                    ["95a4c0abcba24364", "Argon", "Code & analysis", [1, 2, 3], 8, "4 Argon"],
                    [fake_carbon_hex, "Carbon", "Most complex work", [1, 2, 3], 8, "4 Carbon"],
                ],
                *([None] * 8),
                ["carbon_multi_ultra_policy"],
            ]
        }
    )
    try:
        discovered = await client.list_models()
        by_id = {m.id: m for m in discovered}
        assert fake_carbon_hex in by_id
        carbon_info = by_id[fake_carbon_hex]
        assert carbon_info.display_name == "4 Carbon"
        assert carbon_info.model_number == 8
        assert carbon_info.thinking_flag == 2
        assert carbon_info.mode_tag is None
        assert resolve_model("gemini-4-carbon").id == fake_carbon_hex
    finally:
        await client.close()
