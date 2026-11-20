"""backend/tests/test_m9_tauri_desktop_and_job_kill.py - M9 Tauri Desktop CORS/Token, Packaging & Win32 Job Kill Proof."""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx
from starlette.testclient import TestClient

from backend.app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    return False


def _is_tcp_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def _resolve_desktop_binary() -> Path:
    target_dir = REPO_ROOT / "src-tauri" / "target"
    for profile in ("debug", "release"):
        candidate = target_dir / profile / "gemini-local-ui-desktop.exe"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"gemini-local-ui-desktop.exe not found under {target_dir}")


def test_tauri_webview2_cors_and_token_guard(monkeypatch):
    client = TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False)

    for origin in ("http://tauri.localhost", "https://tauri.localhost"):
        r_opt = client.options(
            "/api/session/status",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert r_opt.status_code == 200
        assert r_opt.headers.get("access-control-allow-origin") == origin

        r_health = client.get("/health", headers={"Origin": origin})
        assert r_health.status_code == 200
        assert r_health.headers.get("access-control-allow-origin") == origin

    monkeypatch.setenv("GEMINI_LOCAL_API_TOKEN", "tok_m9_secret_val")
    assert client.get("/api/session/status").status_code == 401
    assert client.get("/api/session/status", headers={"X-Gemini-Local-Token": "wrong_tok"}).status_code == 401
    assert client.get("/api/session/status", headers={"X-Gemini-Local-Token": "tok_m9_secret_val"}).status_code == 200


def test_m9_packaging_artifacts_integrity():
    tauri_conf_path = REPO_ROOT / "src-tauri" / "tauri.conf.json"
    hooks_path = REPO_ROOT / "src-tauri" / "windows" / "hooks.nsh"
    spec_path = REPO_ROOT / "backend" / "gemini_sidecar.spec"

    conf = json.loads(tauri_conf_path.read_text("utf-8"))
    bundle = conf.get("bundle", {})
    targets = bundle.get("targets", [])
    assert "nsis" in targets or targets == "nsis"
    assert "binaries/gemini-backend" in bundle.get("externalBin", [])
    assert "windows/hooks.nsh" in json.dumps(bundle)

    hooks_text = hooks_path.read_text("utf-8")
    assert "NSIS_HOOK_PREINSTALL" in hooks_text
    assert "NSIS_HOOK_POSTINSTALL" in hooks_text
    assert "--register-nmh" in hooks_text

    spec_text = spec_path.read_text("utf-8")
    assert "COLLECT(" in spec_text and "sidecar_entry.py" in spec_text


def test_real_taskkill_f_win32_job_zero_zombie_proof():
    desktop_exe = _resolve_desktop_binary()
    proc = subprocess.Popen(
        [str(desktop_exe), "--headless-supervisor", "--python-cmd", sys.executable],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    sidecar_pid = -1
    try:
        assert proc.stdout is not None
        ready_payload: dict | None = None
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    err_out = proc.stderr.read() if proc.stderr else ""
                    raise RuntimeError(f"Supervisor exited early ({proc.returncode}): {err_out}")
                continue
            stripped = line.strip()
            if stripped.startswith("DESKTOP_SUPERVISOR_READY="):
                ready_payload = json.loads(stripped.split("=", 1)[1])
                break

        assert ready_payload is not None, "Timed out waiting for DESKTOP_SUPERVISOR_READY"
        desktop_pid = int(ready_payload["desktop_pid"])
        sidecar_pid = int(ready_payload["sidecar_pid"])
        sidecar_url = str(ready_payload["sidecar_url"])
        parsed_url = urlparse(sidecar_url)
        sidecar_port = int(parsed_url.port or 0)

        assert ready_payload.get("job_bound") is True
        assert desktop_pid == proc.pid and sidecar_pid > 0 and sidecar_port > 0
        assert _is_pid_alive(sidecar_pid) is True

        health_resp = httpx.get(f"{sidecar_url}/health", timeout=5.0)
        assert health_resp.status_code == 200

        # Abruptly kill supervisor parent via taskkill /F (NO /T tree flag -> kernel Win32 Job Object must reap child!)
        subprocess.run(["taskkill", "/F", "/PID", str(desktop_pid)], timeout=10.0, check=True, capture_output=True)

        reap_deadline = time.monotonic() + 3.0
        while time.monotonic() < reap_deadline:
            if not _is_pid_alive(sidecar_pid) and not _is_tcp_port_open("127.0.0.1", sidecar_port):
                break
            time.sleep(0.05)

        assert not _is_pid_alive(sidecar_pid), f"Zombie Python sidecar PID {sidecar_pid} survived taskkill /F!"
        assert not _is_tcp_port_open("127.0.0.1", sidecar_port), f"Sidecar TCP port {sidecar_port} remained bound!"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3.0)
        if sidecar_pid > 0 and _is_pid_alive(sidecar_pid):
            subprocess.run(["taskkill", "/F", "/PID", str(sidecar_pid)], capture_output=True)
