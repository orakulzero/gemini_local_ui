"""backend/app/security/runtime_discovery.py - DPAPI-sealed local IPC discovery."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

from backend.app.storage.vault import (
    IS_WINDOWS, fallback_protect_data, fallback_unprotect_data, win32_protect_data, win32_unprotect_data,
)

logger = logging.getLogger("gemini_security.runtime_discovery")
_DISCOVERY_ENTROPY = b"gemini_local_ui_runtime_discovery_v21"


def _apply_user_only_acl(path: Path) -> None:
    if not IS_WINDOWS:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return
    with contextlib.suppress(Exception):
        user, domain = os.environ.get("USERNAME", "").strip(), os.environ.get("USERDOMAIN", "").strip()
        principal = f"{domain}\\{user}" if domain and user else user
        if principal:
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(F)"], capture_output=True, timeout=3.0, check=False)


def write_runtime_discovery(
    path: Path, *, base_url: str, acp_ws_url: str | None = None, api_token: str | None = None, pid: int | None = None
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tok_sha, tok_b64 = None, None
    if api_token:
        raw_tok = api_token.encode("utf-8")
        tok_sha = hashlib.sha256(raw_tok).hexdigest()
        cipher = win32_protect_data(raw_tok, _DISCOVERY_ENTROPY) if IS_WINDOWS else fallback_protect_data(raw_tok)
        tok_b64 = base64.b64encode(cipher).decode("ascii")
    payload = {
        "pid": int(pid) if pid is not None else os.getpid(),
        "base_url": str(base_url),
        "acp_ws_url": str(acp_ws_url) if acp_ws_url is not None else None,
        "token_sha256": tok_sha,
        "token_dpapi_b64": tok_b64,
        "updated_at": time.time(),
    }
    tmp_fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix="rt_disc_", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(json.dumps(payload, indent=2).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
        _apply_user_only_acl(target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def read_runtime_discovery(path: Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.is_file():
        return None
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            return None
        api_token: str | None = None
        tok_b64 = parsed.get("token_dpapi_b64")
        if isinstance(tok_b64, str) and tok_b64:
            plain = win32_unprotect_data(base64.b64decode(tok_b64), _DISCOVERY_ENTROPY) if IS_WINDOWS else fallback_unprotect_data(base64.b64decode(tok_b64))
            exp_sha = parsed.get("token_sha256")
            if isinstance(exp_sha, str) and exp_sha and not hmac.compare_digest(hashlib.sha256(plain).hexdigest(), exp_sha.lower()):
                return None
            api_token = plain.decode("utf-8")
        return {k: parsed.get(k) for k in ("pid", "base_url", "acp_ws_url", "token_sha256", "updated_at")} | {"api_token": api_token}
    except Exception as exc:
        logger.warning("Failed reading runtime discovery %s: %s", target, exc)
        return None


def clear_runtime_discovery(path: Path) -> None:
    with contextlib.suppress(OSError):
        Path(path).unlink(missing_ok=True)
