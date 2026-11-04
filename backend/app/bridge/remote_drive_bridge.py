"""Google Drive Desktop File-Queue Bridge & Remote Agent Bootstrap (REQ-REMOTE-DRIVE-01)."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

REQUIREMENT_ID = "REQ-REMOTE-DRIVE-01"
MAX_UPLOAD_FILE_BYTES = 7_864_320
MAX_STAGED_FILES = 35
_ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "max"})
_POLL_BACKOFF_SCHEDULE_SECONDS = (1800, 3600, 7200, 21600)


def compute_adaptive_poll_interval_seconds(idle_streak: int) -> int:
    """Return adaptive poll interval in seconds (30m -> 1h -> 2h -> 6h) for idle_streak."""
    idx = max(0, int(idle_streak))
    if idx >= len(_POLL_BACKOFF_SCHEDULE_SECONDS):
        return _POLL_BACKOFF_SCHEDULE_SECONDS[-1]
    return _POLL_BACKOFF_SCHEDULE_SECONDS[idx]


def build_remote_agent_bootstrap_command(
    project: str,
    new_project: bool = False,
    effort: str = "high",
) -> list[str]:
    """Build verified AGY v2.29.0 remote agent bootstrap CLI arguments (REQ-REMOTE-DRIVE-01)."""
    if not isinstance(project, str) or not project.strip():
        raise ValueError("Project identifier must be a non-empty string.")
    clean_effort = str(effort).strip().lower()
    if clean_effort not in _ALLOWED_EFFORTS:
        raise ValueError(f"Unsupported effort level: {effort!r}")

    cmd = ["agy", "--remote-agent"]
    if new_project:
        cmd.append("--new-project")
    cmd.extend(["--project", project.strip(), "--effort", clean_effort])
    return cmd


class RemoteDriveBridge:
    """Zero-port local-to-remote Google Drive synchronization queue with optimistic leases and SHA-256 verification."""

    MAX_UPLOAD_FILE_BYTES: int = MAX_UPLOAD_FILE_BYTES
    MAX_STAGED_FILES: int = MAX_STAGED_FILES

    def __init__(self, bridge_root: Path | str) -> None:
        self.bridge_root = Path(bridge_root)
        self.inbox_dir = self.bridge_root / "drive_inbox"
        self.leases_dir = self.bridge_root / "drive_leases"
        self.outbox_dir = self.bridge_root / "drive_outbox"
        self.state_dir = self.bridge_root / "drive_state"
        for directory in (self.inbox_dir, self.leases_dir, self.outbox_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def stage_job_to_drive(
        self,
        job_id: str,
        payload_bytes: bytes,
        entrypoint_prompt: str,
        priority: str = "P2_BATCH",
        github_repo: str = "",
        estimated_quota_units: float = 10.0,
    ) -> dict[str, Any]:
        """Stage a job payload and manifest into drive_inbox enforcing the 7.5 MiB upload ceiling."""
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string.")
        if len(payload_bytes) > self.MAX_UPLOAD_FILE_BYTES:
            raise ValueError(
                f"Payload size {len(payload_bytes)} exceeds MAX_UPLOAD_FILE_BYTES ({self.MAX_UPLOAD_FILE_BYTES})."
            )
        existing_manifests = list(self.inbox_dir.glob("*.manifest.json"))
        if len(existing_manifests) >= self.MAX_STAGED_FILES:
            raise ValueError(f"Staged job count exceeds MAX_STAGED_FILES ({self.MAX_STAGED_FILES}).")

        sha256_hex = hashlib.sha256(payload_bytes).hexdigest()
        payload_path = self.inbox_dir / f"{job_id}.payload.bin"
        manifest_path = self.inbox_dir / f"{job_id}.manifest.json"

        payload_path.write_bytes(payload_bytes)
        manifest: dict[str, Any] = {
            "requirement_id": REQUIREMENT_ID,
            "job_id": job_id,
            "sha256": sha256_hex,
            "size_bytes": len(payload_bytes),
            "entrypoint_prompt": str(entrypoint_prompt),
            "priority": str(priority),
            "github_repo": str(github_repo),
            "estimated_quota_units": float(estimated_quota_units),
            "staged_at": time.time(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def acquire_optimistic_lease(
        self,
        job_id: str,
        worker_id: str,
        remote_conv_id: str = "",
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        """Acquire or contest an optimistic worker lease in drive_leases."""
        lease_path = self.leases_dir / f"{job_id}.lease.json"
        now = time.time()
        if lease_path.exists():
            try:
                existing = json.loads(lease_path.read_text(encoding="utf-8"))
                if float(existing.get("expires_at", 0.0)) > now and existing.get("worker_id") != worker_id:
                    return {
                        "acquired": False,
                        "job_id": job_id,
                        "holder_worker_id": str(existing.get("worker_id", "")),
                        "remote_conv_id": str(existing.get("remote_conv_id", "")),
                    }
            except (OSError, ValueError):
                pass

        lease_doc: dict[str, Any] = {
            "acquired": True,
            "job_id": job_id,
            "worker_id": worker_id,
            "holder_worker_id": worker_id,
            "remote_conv_id": remote_conv_id,
            "acquired_at": now,
            "expires_at": now + max(1, int(ttl_seconds)),
        }
        lease_path.write_text(json.dumps(lease_doc, indent=2), encoding="utf-8")
        return lease_doc

    def complete_job_to_outbox(
        self,
        job_id: str,
        worker_id: str,
        result_bytes: bytes,
        status: str = "COMPLETED",
        summary_markdown: str = "",
    ) -> dict[str, Any]:
        """Write completed result binary and SHA-256 receipt to drive_outbox."""
        if len(result_bytes) > self.MAX_UPLOAD_FILE_BYTES:
            raise ValueError("Result size exceeds MAX_UPLOAD_FILE_BYTES.")
        sha256_hex = hashlib.sha256(result_bytes).hexdigest()
        result_path = self.outbox_dir / f"{job_id}.result.bin"
        receipt_path = self.outbox_dir / f"{job_id}.receipt.json"

        result_path.write_bytes(result_bytes)
        receipt: dict[str, Any] = {
            "requirement_id": REQUIREMENT_ID,
            "job_id": job_id,
            "worker_id": worker_id,
            "status": status,
            "sha256": sha256_hex,
            "size_bytes": len(result_bytes),
            "summary_markdown": summary_markdown,
            "completed_at": time.time(),
        }
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return receipt

    def verify_and_collect_outbox_result(self, job_id: str) -> dict[str, Any]:
        """Verify SHA-256 integrity of outbox result against receipt and return payload."""
        result_path = self.outbox_dir / f"{job_id}.result.bin"
        receipt_path = self.outbox_dir / f"{job_id}.receipt.json"
        if not result_path.exists() or not receipt_path.exists():
            raise ValueError(f"Outbox artifacts missing for job {job_id!r}.")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        result_bytes = result_path.read_bytes()
        actual_sha256 = hashlib.sha256(result_bytes).hexdigest()
        if actual_sha256 != receipt.get("sha256"):
            raise ValueError(f"Outbox SHA-256 mismatch for job {job_id!r}.")
        return {
            "job_id": job_id,
            "receipt": receipt,
            "result_bytes": result_bytes,
            "sha256": actual_sha256,
        }
