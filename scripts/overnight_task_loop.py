"""
Overnight Continuous Autonomous Task Loop Runner (Milestone M8)
Executes real-world multi-stage engineering tasks against local Gemini Bridge daemon
using the authenticated Ultra account (acc_21aac20f) with Gemini 4 Argon High Reasoning.

Features:
- Multi-tier synthetic context generator (1MB, 3MB, 5MB, 10MB, 15MB)
- Isolated uvicorn daemon RSS memory tracking via psutil on port 8000
- Token heuristics (prompt, completion, thinking, total) and duration tracking
- Cookie freshness and session health monitoring from /api/session/status
- Automatic artifact extraction (-x) and zip packaging (-p) with CRC checks
- Real-time NDJSON logging to logs/overnight_loop.jsonl and dashboard to OVERNIGHT_REPORT.md
"""

import os
import sys
import time
import json
import random
import shutil
import signal
import zipfile
import argparse
import urllib.request
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
CLI_PATH = Path(os.environ.get("GEMINI_TASK_CLI", str(WORKSPACE_DIR / ".agents" / "skills" / "gemini-task" / "scripts" / "gemini_task.py")))
LOGS_DIR = WORKSPACE_DIR / "logs"
RUNS_DIR = WORKSPACE_DIR / "overnight_runs"
REPORT_PATH = WORKSPACE_DIR / "OVERNIGHT_REPORT.md"
METRICS_JSONL = LOGS_DIR / "overnight_loop.jsonl"
DEFAULT_ACCOUNT = "acc_21aac20f"
DAEMON_URL = "http://127.0.0.1:8000"

# Multi-tier context scale configurations (1MB to 15MB)
CONTEXT_TIERS = [
    {"tier": "1MB", "target_bytes": 1 * 1024 * 1024},
    {"tier": "3MB", "target_bytes": 3 * 1024 * 1024},
    {"tier": "5MB", "target_bytes": 5 * 1024 * 1024},
    {"tier": "10MB", "target_bytes": 10 * 1024 * 1024},
    {"tier": "15MB", "target_bytes": 15 * 1024 * 1024},
]

# Task prompts catalog covering diverse engineering domains
TASK_CATALOG = [
    {
        "id": "T01_passive_cookie_audit",
        "title": "Audit and architecture of passive cookie rotation",
        "prompt": (
            "You are acting as a lead system architect. Analyze the attached "
            "code of session.py and client.py. Design a passive cookie harvesting implementation via "
            "Set-Cookie headers in HTTPX response hooks without calling the deprecated RotateCookiesPage. "
            "Write a complete working module backend/app/bridge/cookie_harvester.py with thread-safe "
            "DPAPI vault synchronization, and unit tests in tests/test_cookie_harvester.py.\n\n"
            "Output the created files in the following format:\n"
            "### File: backend/app/bridge/cookie_harvester.py\n"
            "```python\n# code\n```\n\n"
            "### File: tests/test_cookie_harvester.py\n"
            "```python\n# tests\n```"
        ),
        "context_files": [
            "backend/app/bridge/session.py",
            "backend/app/bridge/client.py",
        ]
    },
    {
        "id": "T02_feature_mode_cache",
        "title": "RTT optimization and Task Mode 1173 incompatibility caching",
        "prompt": (
            "Analyze error 1173 (BardErrorInfo) handling when sending feature_mode=40 in client.py. "
            "Design and implement a FeatureModeCompatibilityCache class that caches unsupported features "
            "for 24 hours per account_id. Write unit tests in tests/test_feature_mode_cache.py verifying "
            "instant routing to the standard stream without the 250ms RTT penalty.\n\n"
            "Output the created files in the following format:\n"
            "### File: backend/app/bridge/feature_cache.py\n"
            "```python\n# code\n```\n\n"
            "### File: tests/test_feature_mode_cache.py\n"
            "```python\n# tests\n```"
        ),
        "context_files": [
            "backend/app/bridge/client.py",
            "backend/app/api/chat.py",
        ]
    },
    {
        "id": "T03_disconnect_detection",
        "title": "Non-blocking SSE disconnect detector during extended Reasoning",
        "prompt": (
            "Inspect the chat_stream_endpoint generator in chat.py. During Gemini 4 Argon reasoning the socket "
            "can remain silent for 60-180 seconds. Design an async iterator that periodically polls "
            "request.is_disconnected() via asyncio.wait_for with a 1.0s interval. "
            "Write an isolated module backend/app/api/stream_guardian.py and tests in tests/test_stream_guardian.py "
            "verifying stream cancellation upon client disconnect.\n\n"
            "Output the created files in the following format:\n"
            "### File: backend/app/api/stream_guardian.py\n"
            "```python\n# code\n```\n\n"
            "### File: tests/test_stream_guardian.py\n"
            "```python\n# tests\n```"
        ),
        "context_files": [
            "backend/app/api/chat.py",
            "backend/app/bridge/streaming_parser.py",
        ]
    },
    {
        "id": "T04_surrogate_latching",
        "title": "Buffering UTF-16 surrogate pairs across network chunk boundaries",
        "prompt": (
            "Analyze frame decoding in streaming_parser.py. If a network chunk splits across an unpaired "
            "high surrogate (0xD800..0xDBFF), serialization errors can occur. "
            "Write a utility class SurrogateAwareBuffer that buffers lone surrogates until the matching "
            "low surrogate arrives, and tests in tests/test_surrogate_latching.py checking multi-byte emojis and formulas.\n\n"
            "Output the created files in the following format:\n"
            "### File: backend/app/bridge/surrogate_buffer.py\n"
            "```python\n# code\n```\n\n"
            "### File: tests/test_surrogate_latching.py\n"
            "```python\n# tests\n```"
        ),
        "context_files": [
            "backend/app/bridge/streaming_parser.py",
        ]
    },
    {
        "id": "T05_wal_stress_benchmark",
        "title": "SQLite WAL concurrent access and session integrity benchmark",
        "prompt": (
            "Design a high-concurrency stress test for SQLite WAL mode on gemini_local.db. "
            "Write a benchmark script tests/test_wal_high_concurrency.py emulating 40 concurrent readers "
            "and 10 transactional conversation message writers measuring p95/p99 latency with zero SQLITE_BUSY errors.\n\n"
            "Output the created files in the following format:\n"
            "### File: tests/test_wal_high_concurrency.py\n"
            "```python\n# code\n```"
        ),
        "context_files": [
            "backend/app/storage/database.py",
            "backend/app/storage/repository.py",
        ]
    }
]

running = True

def handle_sigint(signum, frame):
    global running
    print("\n[!] Received termination signal. Finalizing overnight loop...")
    running = False

signal.signal(signal.SIGINT, handle_sigint)
signal.signal(signal.SIGTERM, handle_sigint)


def get_daemon_pid(port: int = 8000) -> Optional[int]:
    """Finds the PID of the process listening on the specified TCP port."""
    try:
        import psutil
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                return conn.pid
    except Exception:
        pass
    return None


def get_daemon_rss_mb(daemon_pid: Optional[int]) -> float:
    """Accurately reads RSS memory of the isolated uvicorn daemon process."""
    if not daemon_pid:
        return 0.0
    try:
        import psutil
        p = psutil.Process(daemon_pid)
        return round(p.memory_info().rss / (1024 * 1024), 2)
    except Exception:
        return 0.0


def check_daemon_health(daemon_url: str = DAEMON_URL) -> Dict[str, Any]:
    """Queries /api/session/status and returns health dictionary."""
    try:
        url = f"{daemon_url}/api/session/status"
        req = urllib.request.Request(url, headers={"User-Agent": "OvernightLoop/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e), "authenticated": False}
    return {"authenticated": False}


def get_session_health(daemon_url: str = DAEMON_URL) -> Dict[str, Any]:
    """Retrieves session health and cookie freshness telemetry."""
    try:
        raw = check_daemon_health(daemon_url)
        health = raw.get("health", {})
        last_refresh = health.get("last_refresh_timestamp")
        time_since = round(time.time() - last_refresh, 1) if last_refresh else None
        return {
            "active_account": raw.get("active_account"),
            "status": health.get("status", "unknown"),
            "has_psid": health.get("has_psid", False),
            "has_psidts": health.get("has_psidts", False),
            "has_access_token": health.get("has_access_token", False),
            "last_refresh_timestamp": last_refresh,
            "time_since_refresh_s": time_since,
            "build_label": health.get("build_label"),
            "account_tier": health.get("account_tier", "unknown"),
        }
    except Exception as e:
        return {"error": str(e), "status": "unreachable"}


def estimate_tokens(text: str) -> int:
    """Calibrated token estimation heuristic handling Cyrillic and code structures."""
    if not text:
        return 0
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    if cyrillic > len(text) * 0.3:
        return max(1, int(len(text) / 1.8))
    return max(1, int(len(text) / 3.8))


def generate_context_payload(target_bytes: int, target_dir: Path, base_files: List[str]) -> Tuple[int, int]:
    """
    Generates a structured multi-module codebase directory matching the target size tier (1MB - 15MB).
    Copies real project files first, then synthesizes structured modules with valid Python,
    TypeScript, Markdown, and JSON schemas until target_bytes is satisfied.
    Returns (total_bytes, total_files).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    total_written = 0
    file_count = 0

    # 1. Copy authentic codebase files
    for rel_path in base_files:
        src = WORKSPACE_DIR / rel_path
        if src.exists():
            dst = target_dir / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            total_written += dst.stat().st_size
            file_count += 1

    # 2. Synthesize structured multi-module architecture
    module_idx = 1
    while total_written < target_bytes:
        mod_dir = target_dir / "src" / f"module_{module_idx:03d}"
        mod_dir.mkdir(parents=True, exist_ok=True)

        # Python service
        py_code = (
            f'"""Module {module_idx:03d} - High-Throughput Stream Pipeline Service."""\n\n'
            f'import asyncio\n'
            f'import time\n'
            f'from typing import Dict, List, Any, Optional\n\n'
            f'class PipelineService_{module_idx}:\n'
            f'    """Handles chunked aggregation for pipeline cluster {module_idx}."""\n'
            f'    def __init__(self, node_id: str = "node_{module_idx}", max_buffer: int = 5000) -> None:\n'
            f'        self.node_id = node_id\n'
            f'        self.max_buffer = max_buffer\n'
            f'        self.records: List[Dict[str, Any]] = []\n\n'
            f'    async def ingest_batch(self, batch: List[Dict[str, Any]]) -> int:\n'
            f'        # Simulated high-throughput payload block for load test validation {module_idx}\n'
            + ('        # Structural payload line ensuring realistic file density and compressibility\n' * 45)
            + f'        self.records.extend(batch)\n'
            f'        return len(self.records)\n'
        )
        py_path = mod_dir / "service.py"
        py_path.write_text(py_code, encoding="utf-8")
        total_written += py_path.stat().st_size
        file_count += 1

        # TypeScript schemas
        ts_code = (
            f'export interface PipelineTelemetry_{module_idx} {{\n'
            f'  nodeId: string;\n'
            f'  timestampMs: number;\n'
            f'  bufferOccupancy: number;\n'
            f'  status: "nominal" | "degraded" | "critical";\n'
            f'  metrics: {{\n'
            f'    throughputRps: number;\n'
            f'    p99LatencyMs: number;\n'
            f'    errorRate: number;\n'
            f'  }};\n'
            f'}}\n\n'
            + ('// Telemetry schema payload filler line for realistic distribution\n' * 30)
        )
        ts_path = mod_dir / "types.ts"
        ts_path.write_text(ts_code, encoding="utf-8")
        total_written += ts_path.stat().st_size
        file_count += 1

        # Architecture documentation
        docs_dir = target_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        doc_content = (
            f"# Architecture Specification: Subsystem {module_idx:03d}\n\n"
            f"## Overview\n"
            f"This subsystem coordinates state distribution across node cluster {module_idx}.\n\n"
            f"## Contract Invariants\n"
            f"- Non-blocking async queue consumption\n"
            f"- Zero CPU thrashing during idle SSE reasoning frames\n"
            f"- Strictly bounded memory footprints with proactive garbage collection.\n\n"
            + ("- Verified invariant: continuous monotonic stream processing with zero leaks.\n" * 25)
        )
        doc_path = docs_dir / f"architecture_{module_idx:03d}.md"
        doc_path.write_text(doc_content, encoding="utf-8")
        total_written += doc_path.stat().st_size
        file_count += 1

        module_idx += 1

    return total_written, file_count


def update_markdown_report(iterations_data: List[Dict[str, Any]], daemon_pid: Optional[int] = None):
    """Rewrites OVERNIGHT_REPORT.md with live telemetry, memory leak analysis, and execution log."""
    total_runs = len(iterations_data)
    successful_runs = sum(1 for d in iterations_data if d["status"] == "SUCCESS")
    failed_runs = sum(1 for d in iterations_data if d["status"] == "FAILED")
    timeout_runs = sum(1 for d in iterations_data if d["status"] == "TIMEOUT")
    success_rate = (successful_runs / total_runs * 100) if total_runs > 0 else 0.0

    durations = [d["duration_s"] for d in iterations_data if d["status"] == "SUCCESS"]
    avg_latency = (sum(durations) / len(durations)) if durations else 0.0
    total_files_extracted = sum(d.get("extracted_count", 0) for d in iterations_data)
    total_tokens = sum(d.get("total_tokens_est", 0) for d in iterations_data)

    # Memory telemetry
    current_rss = get_daemon_rss_mb(daemon_pid)
    initial_rss = iterations_data[0].get("daemon_rss_before_mb", current_rss) if iterations_data else current_rss
    peak_rss = max([d.get("daemon_rss_after_mb", 0) for d in iterations_data] + [current_rss])
    rolling_deltas = [d.get("daemon_rss_delta_mb", 0) for d in iterations_data[-5:]]
    avg_recent_delta = sum(rolling_deltas) / len(rolling_deltas) if rolling_deltas else 0.0

    memory_status = "🟢 HEALTHY (No Leaks Detected)" if (current_rss - initial_rss) < 60.0 else "🟡 ELEVATED (Monitor Closely)"

    health_info = get_session_health()

    lines = [
        "# Overnight Continuous Testing & Autonomous Hardening Dashboard",
        "",
        f"> **Last Updated**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        f"> **Target Daemon**: `{DAEMON_URL}` (PID: `{daemon_pid or 'N/A'}`)",
        f"> **Active Profile**: `{DEFAULT_ACCOUNT}` (ULTRA / Pro)",
        f"> **Loop Status**: `{'ACTIVE RUNNING' if running else 'STOPPED'}`",
        "",
        "## Overall Metrics",
        f"- **Total Iterations Executed**: `{total_runs}`",
        f"- **Success Rate**: `{success_rate:.1f}%` ({successful_runs} passed / {failed_runs} failed / {timeout_runs} timeouts)",
        f"- **Average Task Latency**: `{avg_latency:.1f}s`",
        f"- **Total Materials & Artifacts Extracted**: `{total_files_extracted}`",
        f"- **Total Estimated Tokens Processed**: `{total_tokens:,}`",
        "",
        "## Isolated Daemon Memory Tracking",
        f"- **Initial Daemon RSS**: `{initial_rss:.2f} MB`",
        f"- **Current Daemon RSS**: `{current_rss:.2f} MB`",
        f"- **Peak Daemon RSS**: `{peak_rss:.2f} MB`",
        f"- **Recent Rolling Delta (avg last 5)**: `{avg_recent_delta:+.2f} MB`",
        f"- **Memory Leak Assessment**: {memory_status}",
        "",
        "## Cookie Freshness & Session Vault Telemetry",
        f"- **Active Account**: `{health_info.get('active_account', DEFAULT_ACCOUNT)}`",
        f"- **Health Status**: `{health_info.get('status')}`",
        f"- **PSID / PSIDTS Available**: `{health_info.get('has_psid')}` / `{health_info.get('has_psidts')}`",
        f"- **Time Since Last Token Refresh**: `{health_info.get('time_since_refresh_s')}s`",
        f"- **Server Build Label**: `{health_info.get('build_label')}`",
        "",
        "## Iteration Log",
        "| # | Task ID | Tier | Duration | Status | Extracted Files | Zip Size | Memory RSS (Delta) | Tokens (P/C/T) | Timestamp |",
        "|---|---------|------|----------|--------|-----------------|----------|---------------------|----------------|-----------|"
    ]

    for d in reversed(iterations_data[-30:]):
        if d["status"] == "SUCCESS":
            status_icon = "🟢 PASS"
        elif d["status"] == "TIMEOUT":
            status_icon = "🟡 TIMEOUT"
        else:
            status_icon = "🔴 FAIL"

        tier = d.get("context_tier", "1MB")
        delta = d.get("daemon_rss_delta_mb", 0.0)
        rss_str = f"{d.get('daemon_rss_after_mb', d.get('memory_mb', 0.0)):.1f}MB ({delta:+.1f}MB)"
        tokens_str = f"{d.get('prompt_tokens_est', 0)}/{d.get('completion_tokens_est', 0)}/{d.get('total_tokens_est', 0)}"

        lines.append(
            f"| {d['iteration']} | `{d['task_id']}` | `{tier}` | {d['duration_s']:.1f}s | {status_icon} | "
            f"{d.get('extracted_count', 0)} | {d.get('output_zip_size_kb', d.get('zip_size_kb', 0)):.1f} KB | "
            f"{rss_str} | {tokens_str} | {d['timestamp']} |"
        )

    lines.append("")
    lines.append("## Raw Health Telemetry")
    lines.append(f"```json\n{json.dumps(check_daemon_health(), indent=2)}\n```\n")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def run_loop(max_iterations: Optional[int] = None, sleep_interval: int = 20, forced_tier: Optional[str] = None, cli_mode: str = "chat"):
    """Main execution loop for continuous autonomous testing."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 75)
    print("🚀 Starting Overnight Continuous Autonomous Stress Loop (Milestone M8)")
    print(f"Working Dir: {WORKSPACE_DIR}")
    print(f"Task CLI:    {CLI_PATH}")
    print(f"Account:     {DEFAULT_ACCOUNT} (ULTRA)")
    print(f"Daemon URL:  {DAEMON_URL}")
    print("=" * 75)

    daemon_pid = get_daemon_pid(8000)
    if daemon_pid:
        print(f"[+] Found isolated uvicorn daemon process (PID: {daemon_pid}, RSS: {get_daemon_rss_mb(daemon_pid)} MB)")
    else:
        print("[!] Warning: Could not locate listening process on port 8000 via psutil.")

    health = check_daemon_health()
    if not health.get("authenticated"):
        print(f"[!] Warning: Daemon at {DAEMON_URL} not authenticated: {health}")
    else:
        print(f"[+] Daemon authenticated. Active Account: {health.get('active_account')}")

    iterations_data: List[Dict[str, Any]] = []
    iteration = 1
    executed_count = 0

    # Load existing logs if resuming
    if METRICS_JSONL.exists():
        try:
            with open(METRICS_JSONL, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        iterations_data.append(json.loads(line))
            if iterations_data:
                iteration = iterations_data[-1]["iteration"] + 1
                print(f"[*] Resuming from existing log: {len(iterations_data)} prior iterations recorded.")
        except Exception as e:
            print(f"[!] Warning reading existing log: {e}")

    while running:
        if max_iterations and executed_count >= max_iterations:
            print(f"[+] Reached maximum requested iterations ({max_iterations}). Exiting loop.")
            break

        task_def = TASK_CATALOG[(iteration - 1) % len(TASK_CATALOG)]

        # Select context tier
        if forced_tier:
            current_tier = next((t for t in CONTEXT_TIERS if t["tier"].lower() == forced_tier.lower()), CONTEXT_TIERS[0])
        else:
            current_tier = CONTEXT_TIERS[(iteration - 1) % len(CONTEXT_TIERS)]

        iter_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_folder = RUNS_DIR / f"iter_{iteration:04d}_{task_def['id']}_{current_tier['tier']}_{iter_stamp}"
        ctx_dir = run_folder / "context"
        extract_dir = run_folder / "extracted"
        output_zip = run_folder / "output_materials.zip"
        prompt_file = run_folder / "prompt.md"
        response_file = run_folder / "response.md"

        run_folder.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(task_def["prompt"], encoding="utf-8")

        print(f"\n--- [Iteration {iteration}] Task: {task_def['id']} | Context Tier: {current_tier['tier']} ---")

        # Stage multi-tier context payload
        staged_bytes, staged_count = generate_context_payload(
            target_bytes=current_tier["target_bytes"],
            target_dir=ctx_dir,
            base_files=task_def["context_files"]
        )
        print(f"Context Staged: {staged_count} files, {staged_bytes / (1024 * 1024):.2f} MB -> {ctx_dir}")

        # Sample memory before task execution
        daemon_pid = get_daemon_pid(8000)
        mem_before = get_daemon_rss_mb(daemon_pid)
        start_time = time.time()

        cmd = [
            sys.executable,
            str(CLI_PATH),
            "-c", str(ctx_dir),
            "-x", str(extract_dir),
            "-p", str(output_zip),
            "-f", str(prompt_file),
            "-o", str(response_file),
            "-a", DEFAULT_ACCOUNT,
            "-t", "2",
            "--mode", cli_mode
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            duration = time.time() - start_time
            mem_after = get_daemon_rss_mb(daemon_pid)
            mem_delta = round(mem_after - mem_before, 2)

            response_text = response_file.read_text(encoding="utf-8") if response_file.exists() else ""
            success = (res.returncode == 0) and bool(response_text) and len(response_text) > 50

            extracted_files = list(extract_dir.rglob("*.*")) if extract_dir.exists() else []
            extracted_count = len(extracted_files)
            output_zip_size_kb = (output_zip.stat().st_size / 1024.0) if output_zip.exists() else 0.0

            # Verify zip validity
            if output_zip.exists() and output_zip.stat().st_size > 0:
                try:
                    with zipfile.ZipFile(output_zip, "r") as zf:
                        if zf.testzip() is not None:
                            success = False
                            print("[!] Output zip CRC check failed!")
                except Exception as ze:
                    success = False
                    print(f"[!] Output zip corrupt: {ze}")

            # Token metrics
            prompt_tokens_est = estimate_tokens(task_def["prompt"]) + int(staged_bytes / 4)
            completion_tokens_est = estimate_tokens(response_text)
            thinking_tokens_est = estimate_tokens(res.stderr) if res.stderr else 0
            total_tokens_est = prompt_tokens_est + completion_tokens_est + thinking_tokens_est

            health_telemetry = get_session_health()

            record = {
                "iteration": iteration,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "task_id": task_def["id"],
                "task_title": task_def["title"],
                "status": "SUCCESS" if success else "FAILED",
                "returncode": res.returncode,
                "duration_s": round(duration, 2),
                "context_tier": current_tier["tier"],
                "context_staged_bytes": staged_bytes,
                "context_staged_files": staged_count,
                "extracted_count": extracted_count,
                "output_zip_size_kb": round(output_zip_size_kb, 2),
                "response_size_bytes": len(response_text.encode("utf-8")),
                "prompt_tokens_est": prompt_tokens_est,
                "completion_tokens_est": completion_tokens_est,
                "thinking_tokens_est": thinking_tokens_est,
                "total_tokens_est": total_tokens_est,
                "daemon_rss_before_mb": mem_before,
                "daemon_rss_after_mb": mem_after,
                "daemon_rss_delta_mb": mem_delta,
                "cookie_health": health_telemetry.get("status"),
                "cookie_time_since_refresh_s": health_telemetry.get("time_since_refresh_s"),
                "active_account": health_telemetry.get("active_account") or DEFAULT_ACCOUNT,
            }

            if not success:
                record["error_stderr"] = res.stderr[-500:] if res.stderr else "Empty stderr"
                print(f"[x] Iteration {iteration} FAILED in {duration:.1f}s. Returncode: {res.returncode}")
                if res.stderr:
                    print(f"Stderr: {res.stderr[-300:]}")
            else:
                print(
                    f"[✓] Iteration {iteration} PASSED in {duration:.1f}s! "
                    f"Extracted: {extracted_count} files, Zip: {output_zip_size_kb:.1f} KB, "
                    f"RSS: {mem_after:.1f} MB (Delta: {mem_delta:+.1f} MB), Tokens: ~{total_tokens_est}"
                )

            iterations_data.append(record)

            # Append NDJSON log
            with open(METRICS_JSONL, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            # Update markdown report
            update_markdown_report(iterations_data, daemon_pid=daemon_pid)

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            print(f"[x] Iteration {iteration} TIMED OUT after {duration:.1f}s")
            record = {
                "iteration": iteration,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "task_id": task_def["id"],
                "status": "TIMEOUT",
                "duration_s": round(duration, 2),
                "context_tier": current_tier["tier"],
            }
            iterations_data.append(record)
            with open(METRICS_JSONL, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            update_markdown_report(iterations_data, daemon_pid=daemon_pid)

        except Exception as e:
            print(f"[!] Unexpected error during iteration {iteration}: {e}")

        iteration += 1
        executed_count += 1

        if running and (not max_iterations or executed_count < max_iterations):
            jitter = random.uniform(-3.0, 5.0)
            nap_time = max(5.0, sleep_interval + jitter)
            print(f"[*] Sleeping {nap_time:.1f}s before next iteration...")
            time.sleep(nap_time)

    print("\n[+] Overnight loop terminated gracefully. Summary report written to OVERNIGHT_REPORT.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Overnight Autonomous Stress Loop Runner")
    parser.add_argument("max_it_pos", nargs="?", type=int, default=None, help="Max iterations (positional)")
    parser.add_argument("interval_pos", nargs="?", type=int, default=20, help="Interval in seconds (positional)")
    parser.add_argument("-n", "--iterations", type=int, default=None, help="Max iterations to execute")
    parser.add_argument("-i", "--interval", type=int, default=None, help="Sleep interval in seconds between runs")
    parser.add_argument("--tier", type=str, default=None, help="Force specific context tier (1MB, 3MB, 5MB, 10MB, 15MB)")
    parser.add_argument("--mode", type=str, default="chat", choices=["chat", "task"], help="Execution mode (chat or task, default: chat)")

    args = parser.parse_args()

    max_iterations = args.iterations or args.max_it_pos
    interval = args.interval or args.interval_pos or 20

    run_loop(max_iterations=max_iterations, sleep_interval=interval, forced_tier=args.tier, cli_mode=args.mode)
