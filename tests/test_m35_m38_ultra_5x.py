"""
tests/test_m35_m38_ultra_5x.py
Locked Agile V (P30) Verification Suite for Milestones M35, M36, M37, and M38:
- REQ-ULTRA-01: Ultra 5x Family Member Profile, 5x Telemetry Thresholds & Shared Family Credits Pool
- REQ-ULTRA-02: Deep Think Parallel Tree-of-Thought Mode (thinking_flag=3)
- REQ-ULTRA-03: Ultra 2M Token Full-Repo Packager & Long-Form Video/Audio Scotty Injector
- REQ-ULTRA-04: Veo 3.1 Ultra HQ 1080p, First/Last Frame Interpolation & Video Scene Extension
- REQ-ULTRA-05: NotebookLM Plus/Ultra 300-Source Capacity & Critique/Debate/Deep-Dive Audio Modes
- REQ-ULTRA-06: FastAPI /api/v1/capabilities/ultra/* Endpoints & MCP gemini_capabilities Ultra Actions
"""

from __future__ import annotations

import asyncio
import base64
import inspect
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

from fastapi.testclient import TestClient

from backend.app.bridge.models import resolve_model
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.bridge.ultra_capabilities import (
    NotebookLMUltraEngine,
    UltraSubscriptionManager,
    VeoUltraStudioEngine,
    analyze_ultra_media_with_timecodes,
    pack_ultra_2m_repository_context,
    run_deep_think_reasoning,
)
from backend.app.main import app
from backend.app.mcp.server import MCPServer


def _resolve(val: Any) -> Any:
    """Resolves either synchronous return values or coroutines transparently."""
    if inspect.isawaitable(val):
        return asyncio.run(val)
    return val


class MockUltraClient:
    """Deterministic mock Gemini Web client for M35-M38 unit verification."""

    async def generate_stream(
        self,
        prompt: str,
        model: str = "gemini-4-argon-deep-think",
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        text_out = (
            "### Deep Think Synthesis\n"
            "- Branch 1: Polyphase FIR decimation achieves M-fold rate reduction.\n"
            "- Branch 2: Noble identities commute downsampling with delay chains.\n"
            "- Branch 3: Optimal stopband attenuation bound verified.\n"
            "Timestamps: [00:01:15] System Architecture, [00:14:30] Polyphase Filter Bank.\n"
        )
        yield StreamChunk(type="thinking", delta="Evaluating parallel hypothesis tree branches 1..3")
        yield StreamChunk(type="text", delta=text_out)
        yield StreamChunk(
            type="done",
            delta="",
            metadata={
                "cid": kwargs.get("cid") or "c_ultra_501",
                "rid": "r_ultra_501",
                "rcid": "rc_ultra_501",
                "full_text": text_out,
                "model": model,
            },
        )


def test_req_ultra_01_family_member_profile_and_credits_pool(tmp_path: Path) -> None:
    """REQ-ULTRA-01: UltraSubscriptionManager detects launch_ultra_5x_family_member_policy and tracks family credits."""
    mgr = UltraSubscriptionManager(storage_path=tmp_path / "ultra_credits.json")
    profile = mgr.detect_subscription_profile(
        policy_str="launch_ultra_5x_family_member_policy",
        account_id="acc_21aac20f",
    )
    assert profile["tier"] == "ultra_5x"
    assert profile["is_family_member"] is True
    assert profile["multiplier"] == 5
    assert profile["daily_request_threshold"] == 1250
    assert profile["daily_token_threshold"] == 2_500_000
    assert profile["hourly_request_threshold"] == 300
    assert profile["context_window_tokens"] == 2_000_000
    assert profile["privacy_isolated"] is True

    credits_before = mgr.get_family_credits_telemetry(account_id="acc_21aac20f")
    assert credits_before["ok"] is True
    assert credits_before["account_id"] == "acc_21aac20f"
    assert credits_before["tier"] == "ultra_5x"
    assert credits_before["individual_chat_quota_isolated"] is True
    total_credits = int(credits_before["monthly_ai_credits_total"])
    rem_before = int(credits_before["monthly_ai_credits_remaining"])
    assert total_credits >= 1000
    assert rem_before == total_credits

    consumed = mgr.consume_ai_credits(
        amount=10,
        feature="veo_3_1_hq_1080p",
        account_id="acc_21aac20f",
    )
    assert consumed["ok"] is True
    assert int(consumed["monthly_ai_credits_remaining"]) == rem_before - 10
    assert len(consumed.get("ledger", [])) >= 1


def test_req_ultra_02_deep_think_model_resolution_and_reasoning() -> None:
    """REQ-ULTRA-02: resolve_model supports thinking_flag=3 and run_deep_think_reasoning synthesizes branches."""
    m1 = resolve_model("gemini-4-argon-deep-think")
    m2 = resolve_model("gemini-4-argon@mode=deep-think")
    for m in (m1, m2):
        assert m.thinking_flag == 3
        assert m.supports_thinking is True
        ctx_win = (
            getattr(m, "context_window", None)
            or getattr(m, "max_input_tokens", None)
            or m.capabilities.context_window_tokens
        )
        assert ctx_win == 2_000_000

    mock_client = MockUltraClient()
    res = _resolve(
        run_deep_think_reasoning(
            prompt="Prove optimal polyphase FIR decimation bound",
            branch_count=3,
            client=mock_client,
        )
    )
    assert res["ok"] is True
    assert res["thinking_flag"] == 3
    assert isinstance(res["branches"], list) and len(res["branches"]) >= 1
    assert isinstance(res["synthesis"], str) and res["synthesis"].strip() != ""
    assert res["cid"]


def test_req_ultra_03_2m_repo_packager_and_media_timecodes(tmp_path: Path) -> None:
    """REQ-ULTRA-03: pack_ultra_2m_repository_context & analyze_ultra_media_with_timecodes."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(5):
        f = src_dir / f"module_{idx}.py"
        lines = [f"def func_{idx}_{line_idx}(x: int) -> int:\n    return x + {line_idx}\n" for line_idx in range(100)]
        f.write_text("".join(lines), encoding="utf-8")

    pack_res = pack_ultra_2m_repository_context(
        workspace_dir=tmp_path,
        max_total_bytes=7_500_000,
        max_file_bytes=1_000_000,
    )
    assert isinstance(pack_res["bundle_text"], str) and "module_0.py" in pack_res["bundle_text"]
    assert pack_res["file_count"] == 5
    assert pack_res["total_lines"] >= 1000
    assert pack_res["total_bytes"] > 0
    assert pack_res["estimated_tokens"] > 0
    assert pack_res["max_context_tokens"] == 2_000_000

    mock_client = MockUltraClient()
    media_res = _resolve(
        analyze_ultra_media_with_timecodes(
            media_bytes=b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
            filename="lecture.mp4",
            prompt="Extract key architectural timestamps",
            client=mock_client,
        )
    )
    assert media_res["ok"] is True
    assert media_res["filename"] == "lecture.mp4"
    assert media_res["mime_type"] == "video/mp4"
    assert isinstance(media_res["timecodes"], list) and len(media_res["timecodes"]) >= 1
    assert isinstance(media_res["summary"], str) and media_res["summary"].strip() != ""


def test_req_ultra_04_veo_3_1_ultra_studio_engine(tmp_path: Path) -> None:
    """REQ-ULTRA-04: VeoUltraStudioEngine supports 1080p HQ, first/last frame interpolation, and scene extension."""
    engine = VeoUltraStudioEngine(storage_path=tmp_path / "veo_ultra.json")
    assert "veo_3_1_t2v_hq_1080p_audio" in engine.SUPPORTED_MODELS
    assert "veo_3_1_i2v_first_last_frame" in engine.SUPPORTED_MODELS
    assert "veo_3_1_video_extend" in engine.SUPPORTED_MODELS

    dummy_frame = base64.b64encode(b"fake_png_bytes").decode("ascii")
    s1 = _resolve(
        engine.render_ultra_scene(
            prompt="3D chip die shot with optical interconnects",
            model_key="veo_3_1_t2v_hq_1080p_audio",
            resolution="1080p",
            first_frame_b64=dummy_frame,
            last_frame_b64=dummy_frame,
        )
    )
    assert s1["ok"] is True
    assert s1["model_key"] == "veo_3_1_t2v_hq_1080p_audio"
    assert s1["resolution"] == "1080p"
    assert int(s1["credits_used"]) > 0
    assert s1["scene_id"]

    s2 = _resolve(
        engine.render_ultra_scene(
            prompt="Camera zoom into SRAM array",
            model_key="veo_3_1_video_extend",
            resolution="1080p",
            extend_from_scene_id=s1["scene_id"],
        )
    )
    assert s2["ok"] is True
    assert s2["extend_from_scene_id"] == s1["scene_id"]


def test_req_ultra_05_notebooklm_ultra_300_sources_and_audio_modes(tmp_path: Path) -> None:
    """REQ-ULTRA-05: NotebookLMUltraEngine enforces 300-source cap and supports critique/debate/deep_dive/brief."""
    nlm = NotebookLMUltraEngine()
    assert nlm.MAX_SOURCES_PER_NOTEBOOK == 300
    assert nlm.MAX_NOTEBOOKS == 500

    docs_dir = tmp_path / "docs_repo"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for i in range(65):
        (docs_dir / f"doc_{i:03d}.md").write_text(f"# Document {i}\nContent block {i}\n", encoding="utf-8")

    sync_res = _resolve(
        nlm.sync_ultra_repo_sources(
            notebook_id="nb_ultra_300",
            workspace_dir=docs_dir,
            max_sources=300,
        )
    )
    assert sync_res["ok"] is True
    assert sync_res["ingested_count"] == 65
    assert sync_res["max_sources_limit"] == 300

    for mode in ("critique", "debate", "deep_dive", "brief"):
        audio_res = _resolve(
            nlm.generate_ultra_audio_overview(
                notebook_id="nb_ultra_300",
                mode=mode,
                instructions="Audit architecture",
            )
        )
        assert audio_res["ok"] is True
        assert audio_res["notebook_id"] == "nb_ultra_300"
        assert audio_res["mode"] == mode
        assert audio_res["max_sources_limit"] == 300
        assert audio_res["audio_url"]
        assert audio_res["script_preview"]


def test_req_ultra_06_fastapi_endpoints_and_mcp_actions(tmp_path: Path) -> None:
    """REQ-ULTRA-06: FastAPI /api/v1/capabilities/ultra/* endpoints and MCPServer gemini_capabilities Ultra actions."""
    (tmp_path / "sample.py").write_text("x = 42\n", encoding="utf-8")
    client = TestClient(app)

    r_prof = client.get("/api/v1/capabilities/ultra/profile")
    assert r_prof.status_code == 200 and r_prof.json()["tier"] == "ultra_5x"

    r_cred = client.get("/api/v1/capabilities/ultra/credits")
    assert r_cred.status_code == 200 and r_cred.json()["ok"] is True

    r_dt = client.post(
        "/api/v1/capabilities/ultra/deep-think",
        json={"prompt": "Prove FIR bound", "branch_count": 3},
    )
    assert r_dt.status_code == 200 and r_dt.json()["thinking_flag"] == 3

    r_pack = client.post(
        "/api/v1/capabilities/ultra/pack-2m",
        json={"workspace_dir": str(tmp_path)},
    )
    assert r_pack.status_code == 200 and r_pack.json()["max_context_tokens"] == 2_000_000

    r_media = client.post(
        "/api/v1/capabilities/ultra/media-analyze",
        json={
            "media_b64": base64.b64encode(b"fake_mp4").decode("ascii"),
            "filename": "lecture.mp4",
            "prompt": "Extract timestamps",
        },
    )
    assert r_media.status_code == 200 and r_media.json()["ok"] is True

    r_veo = client.post(
        "/api/v1/capabilities/ultra/veo-render",
        json={
            "prompt": "3D chip",
            "model_key": "veo_3_1_t2v_hq_1080p_audio",
            "resolution": "1080p",
        },
    )
    assert r_veo.status_code == 200 and r_veo.json()["ok"] is True

    r_nlm = client.post(
        "/api/v1/capabilities/ultra/notebooklm-audio",
        json={
            "notebook_id": "nb_ultra_1",
            "mode": "critique",
            "instructions": "Audit architecture",
        },
    )
    assert r_nlm.status_code == 200 and r_nlm.json()["mode"] == "critique"

    mcp = MCPServer()
    ultra_actions = [
        {"action": "ultra_profile", "account_id": "acc_21aac20f"},
        {"action": "ultra_deep_think", "prompt": "Prove bound", "branch_count": 3},
        {"action": "ultra_pack_2m", "workspace_dir": str(tmp_path)},
        {"action": "ultra_veo_render", "prompt": "3D chip", "model_key": "veo_3_1_t2v_hq_1080p_audio"},
        {"action": "ultra_notebooklm_audio", "notebook_id": "nb_ultra_1", "mode": "debate"},
    ]
    for idx, args in enumerate(ultra_actions, start=1):
        resp = _resolve(
            mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 900 + idx,
                    "method": "tools/call",
                    "params": {"name": "gemini_capabilities", "arguments": args},
                },
                client=MockUltraClient(),
            )
        )
        assert resp is not None
        assert resp["result"]["isError"] is False
