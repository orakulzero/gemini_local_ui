"""Locked Agile V verification suite for Remote Drive Bridge & 168-Hour Weekly Quota Pacer (REQ-ULTRA-WEEKLY-01..02, REQ-REMOTE-DRIVE-01..02)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
import pytest

from backend.app.bridge.ultra_capabilities import UltraSubscriptionManager, calibrate_quota_units_from_tokens
from backend.app.bridge.remote_drive_bridge import (
    RemoteDriveBridge,
    build_remote_agent_bootstrap_command,
    compute_adaptive_poll_interval_seconds,
    reassemble_chunked_job,
    stage_chunked_job_to_drive,
)


def _field(obj: Any, key: str) -> Any:
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def test_req_ultra_weekly_01_quota_pacer_and_weekend_shield(tmp_path: Path) -> None:
    """Verify REQ-ULTRA-WEEKLY-01 168-hour weekly quota pacer, GREEN/AMBER/RED zones, and 20% interactive reserve."""
    storage_file = tmp_path / "ultra_quota_state.json"
    tuesday_10_utc = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
    saturday_14_utc = datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc)

    mgr = UltraSubscriptionManager(storage_path=storage_file)
    rec = mgr.record_weekly_quota_usage("acc_ultra_01", 100.0, tokens=50_000, job_id="job-green-01", now_utc=tuesday_10_utc)
    assert rec["used_units"] == pytest.approx(100.0) and storage_file.exists()

    reloaded = UltraSubscriptionManager(storage_path=storage_file)
    green_eval = reloaded.evaluate_weekly_quota_pacer(
        "acc_ultra_01", estimated_units=10.0, priority="P3_BACKGROUND_24X7", now_utc=tuesday_10_utc
    )
    for key in (
        "allowed", "zone", "burn_fraction", "alpha_multiplier",
        "hourly_cap_units", "remaining_total_units", "remaining_remote_units", "should_terminate_pre_invocation",
    ):
        assert key in green_eval
    assert green_eval["zone"] == "GREEN" and green_eval["allowed"] is True
    assert green_eval["burn_fraction"] == pytest.approx(0.10) and green_eval["alpha_multiplier"] == pytest.approx(1.0)
    assert green_eval["should_terminate_pre_invocation"] is False

    weekend_eval = reloaded.evaluate_weekly_quota_pacer(
        "acc_ultra_01", estimated_units=5.0, priority="P3_BACKGROUND_24X7", now_utc=saturday_14_utc
    )
    assert weekend_eval["alpha_multiplier"] == pytest.approx(0.40)

    reloaded.record_weekly_quota_usage("acc_ultra_01", 600.0, tokens=300_000, job_id="job-amber-02", now_utc=tuesday_10_utc)
    amber_eval = reloaded.evaluate_weekly_quota_pacer(
        "acc_ultra_01", estimated_units=5.0, priority="P2_BATCH", now_utc=tuesday_10_utc
    )
    assert amber_eval["zone"] == "AMBER" and amber_eval["burn_fraction"] == pytest.approx(0.70)

    reloaded.record_weekly_quota_usage("acc_ultra_01", 110.0, tokens=55_000, job_id="job-red-03", now_utc=tuesday_10_utc)
    for prio in ("P3_BACKGROUND_24X7", "P2_BATCH"):
        red_eval = reloaded.evaluate_weekly_quota_pacer(
            "acc_ultra_01", estimated_units=10.0, priority=prio, now_utc=tuesday_10_utc
        )
        assert red_eval["zone"] == "RED" and red_eval["allowed"] is False and red_eval["should_terminate_pre_invocation"] is True

    red_interactive = reloaded.evaluate_weekly_quota_pacer(
        "acc_ultra_01", estimated_units=50.0, priority="P1_INTERACTIVE", now_utc=tuesday_10_utc
    )
    assert red_interactive["zone"] == "RED" and red_interactive["allowed"] is True and red_interactive["should_terminate_pre_invocation"] is False


def test_req_remote_drive_01_stage_lease_outbox_and_bootstrap(tmp_path: Path) -> None:
    """Verify REQ-REMOTE-DRIVE-01 Drive inbox staging, optimistic lease, SHA-256 outbox, backoff, and bootstrap CLI."""
    bridge_root = tmp_path / "gdrive_sync"
    bridge = RemoteDriveBridge(bridge_root)
    assert RemoteDriveBridge.MAX_UPLOAD_FILE_BYTES == 7_864_320 and RemoteDriveBridge.MAX_STAGED_FILES == 35
    for subdir in ("drive_inbox", "drive_leases", "drive_outbox", "drive_state"):
        assert (bridge_root / subdir).is_dir()

    manifest = bridge.stage_job_to_drive(
        job_id="job-101", payload_bytes=b"context-archive-bytes", entrypoint_prompt="Execute verification suite",
        priority="P2_BATCH", github_repo="org/repo", estimated_quota_units=12.0,
    )
    assert len(manifest["sha256"]) == 64
    assert (bridge_root / "drive_inbox" / "job-101.payload.bin").exists()
    assert (bridge_root / "drive_inbox" / "job-101.manifest.json").exists()

    with pytest.raises(ValueError):
        bridge.stage_job_to_drive("job-oversize", b"x" * (RemoteDriveBridge.MAX_UPLOAD_FILE_BYTES + 1), "Too large")

    lease_1 = bridge.acquire_optimistic_lease("job-101", worker_id="worker-1", remote_conv_id="conv-aaa", ttl_seconds=900)
    assert lease_1["acquired"] is True and lease_1["worker_id"] == "worker-1"

    lease_2 = bridge.acquire_optimistic_lease("job-101", worker_id="worker-2", remote_conv_id="conv-bbb", ttl_seconds=900)
    assert lease_2["acquired"] is False and lease_2["holder_worker_id"] == "worker-1"

    receipt = bridge.complete_job_to_outbox(
        "job-101", worker_id="worker-1", result_bytes=b"verified-artifact-bytes", status="COMPLETED", summary_markdown="## All checks green"
    )
    assert receipt["status"] == "COMPLETED"
    collected = bridge.verify_and_collect_outbox_result("job-101")
    assert collected["result_bytes"] == b"verified-artifact-bytes"

    (bridge_root / "drive_outbox" / "job-101.result.bin").write_bytes(b"tampered-bytes")
    with pytest.raises(ValueError):
        bridge.verify_and_collect_outbox_result("job-101")

    assert [compute_adaptive_poll_interval_seconds(i) for i in (0, 1, 2, 3, 6)] == [1800, 3600, 7200, 21600, 21600]
    assert build_remote_agent_bootstrap_command("my-proj", new_project=False, effort="high") == [
        "agy", "--remote-agent", "--project", "my-proj", "--effort", "high"
    ]
    assert "--new-project" in build_remote_agent_bootstrap_command("my-proj", new_project=True, effort="max")
    with pytest.raises(ValueError):
        build_remote_agent_bootstrap_command("my-proj", effort="invalid")
    with pytest.raises(ValueError):
        build_remote_agent_bootstrap_command("   ", effort="high")


def test_req_remote_drive_02_chunked_multi_volume_streaming(tmp_path: Path) -> None:
    """Verify REQ-REMOTE-DRIVE-02 multi-volume chunked Drive staging and SHA-256 reassembly."""
    bridge_root = tmp_path / "gdrive_chunked"
    payload_bytes = b"0123456789abcdef" * 1250  # 20,000 bytes
    expected_sha256 = hashlib.sha256(payload_bytes).hexdigest()

    manifest = stage_chunked_job_to_drive(
        bridge_root, "job-chunk-202", payload_bytes, {"entrypoint_prompt": "Chunked job", "priority": "P2_BATCH"}, max_chunk_bytes=6000
    )
    inbox = bridge_root / "drive_inbox"
    manifest_disk = json.loads((inbox / "job-chunk-202.manifest.json").read_text(encoding="utf-8"))
    for doc in (manifest, manifest_disk):
        assert doc["chunked"] is True and doc["chunk_count"] == 4
        assert len(doc["chunk_hashes"]) == 4 and doc["sha256"] == expected_sha256
    for idx in range(4):
        assert (inbox / f"job-chunk-202.part{idx:03d}.bin").exists()

    reassembled = reassemble_chunked_job(bridge_root, "job-chunk-202")
    raw_bytes = reassembled if isinstance(reassembled, (bytes, bytearray)) else reassembled["payload_bytes"]
    assert raw_bytes == payload_bytes

    part1 = inbox / "job-chunk-202.part001.bin"
    orig_part1 = part1.read_bytes()
    part1.write_bytes(b"corrupted-chunk-data")
    with pytest.raises(ValueError):
        reassemble_chunked_job(bridge_root, "job-chunk-202")

    part1.unlink()
    with pytest.raises(ValueError):
        reassemble_chunked_job(bridge_root, "job-chunk-202")
    part1.write_bytes(orig_part1)


def test_req_ultra_weekly_02_adaptive_token_calibrator() -> None:
    """Verify REQ-ULTRA-WEEKLY-02 adaptive token-to-quota unit calibrator weights across tiers."""
    base_prompt = calibrate_quota_units_from_tokens(
        prompt_tokens=10_000, completion_tokens=2_000, thinking_tokens=0, model_tier="argon_high", observed_latency_sec=4.5
    )
    with_thinking = calibrate_quota_units_from_tokens(
        prompt_tokens=0, completion_tokens=2_000, thinking_tokens=10_000, model_tier="argon_high", observed_latency_sec=4.5
    )
    deep_think = calibrate_quota_units_from_tokens(
        prompt_tokens=10_000, completion_tokens=2_000, thinking_tokens=10_000, model_tier="deep_think", observed_latency_sec=12.0
    )
    flash_tier = calibrate_quota_units_from_tokens(
        prompt_tokens=10_000, completion_tokens=2_000, thinking_tokens=0, model_tier="flash", observed_latency_sec=1.2
    )

    assert _field(base_prompt, "estimated_units") > 0.0 and _field(base_prompt, "total_tokens") == 12_000
    assert _field(base_prompt, "model_tier") == "argon_high"
    assert _field(with_thinking, "estimated_units") > _field(base_prompt, "estimated_units")
    assert _field(deep_think, "effective_weight") > _field(base_prompt, "effective_weight")
    assert _field(base_prompt, "effective_weight") > _field(flash_tier, "effective_weight")
