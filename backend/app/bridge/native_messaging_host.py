"""
backend/app/bridge/native_messaging_host.py
Chrome/Edge MV3 Native Messaging Host for zero-friction cookie synchronization.
"""

from __future__ import annotations

import io
import json
import logging
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Union

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.app.storage.vault import AccountProfile, CredentialVault  # noqa: E402

logger = logging.getLogger("gemini_bridge.nmh")
MAX_FRAME_BYTES = 1024 * 1024  # 1 MB


def read_native_message(stream: Union[BinaryIO, io.BytesIO]) -> Optional[Dict[str, Any]]:
    """Reads a 4-byte uint32 length-prefixed UTF-8 JSON message from stream."""
    raw_len = stream.read(4)
    if not raw_len or len(raw_len) < 4:
        return None
    msg_len = struct.unpack("<I", raw_len)[0]
    if msg_len > MAX_FRAME_BYTES:
        raise ValueError(f"Message length exceeds maximum allowed ({msg_len} > {MAX_FRAME_BYTES})")
    raw_payload = stream.read(msg_len)
    if len(raw_payload) < msg_len:
        raise IOError("Unexpected EOF while reading message payload")
    return json.loads(raw_payload.decode("utf-8"))


def write_native_message(stream: Union[BinaryIO, io.BytesIO], payload: Dict[str, Any]) -> None:
    """Writes a 4-byte uint32 length-prefixed UTF-8 JSON message to stream."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = struct.pack("<I", len(data))
    stream.write(header + data)
    stream.flush()


def handle_native_message(
    msg: Dict[str, Any],
    vault_path: Optional[Union[Path, str]] = None,
    notify_gateway: bool = True,
) -> Dict[str, Any]:
    """Processes incoming native message and returns response dict (zero secrets emitted)."""
    msg_type = msg.get("type", "")

    if msg_type == "ping":
        return {
            "ok": True,
            "host": "com.orakulzero.gemini_local_ui",
            "version": "1.0.0",
        }

    if msg_type == "sync_session":
        raw_cookie = msg.get("cookie", "")
        cookies: Dict[str, str] = {}
        for part in raw_cookie.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    cookies[k] = v

        if not ("SAPISID" in cookies or "__Secure-1PSID" in cookies):
            return {"ok": False, "error": "Missing required authentication cookies (SAPISID or __Secure-1PSID)"}

        notified = False
        saved = False

        if notify_gateway:
            try:
                import httpx
                headers: Dict[str, str] = {}
                token = os.environ.get("GEMINI_LOCAL_API_TOKEN", "").strip()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                forward_payload: Dict[str, Any] = {"cookie": raw_cookie}
                for opt_key in ("sapisid", "auth_user", "xsrf_token", "gemini_bl", "account_id", "notebooklm_cookies", "service_cookies"):
                    if msg.get(opt_key) is not None:
                        forward_payload[opt_key] = msg[opt_key]
                with httpx.Client(timeout=5.0) as http_client:
                    r = http_client.post("http://127.0.0.1:8000/admin/session", json=forward_payload, headers=headers)
                    if r.status_code == 200:
                        notified = True
                        saved = True
            except Exception:
                notified = False

        if not saved:
            try:
                vault = CredentialVault(vault_path=vault_path)
                vdata = vault.load_vault_data()
                target_acc_id = vdata.active_account_id if vdata.active_account_id in vdata.accounts else (
                    next(iter(vdata.accounts.keys())) if vdata.accounts else "acc_21aac20f"
                )
                nb_cookies = msg.get("notebooklm_cookies")
                nb_map = (
                    {str(k): str(v) for k, v in nb_cookies.items() if k and v}
                    if isinstance(nb_cookies, dict)
                    else {}
                )
                if target_acc_id in vdata.accounts:
                    acc = vdata.accounts[target_acc_id]
                    acc.cookies.update(cookies)
                    if nb_map:
                        acc.service_cookies["notebooklm"] = nb_map
                    if isinstance(msg.get("xsrf_token"), str) and msg["xsrf_token"].strip():
                        acc.snlm0e = msg["xsrf_token"].strip()
                    if isinstance(msg.get("gemini_bl"), str) and msg["gemini_bl"].strip():
                        acc.cfb2h = msg["gemini_bl"].strip()
                    acc.last_rotated_at = time.time()
                    acc.health_status = "healthy"
                else:
                    vdata.accounts[target_acc_id] = AccountProfile(
                        id=target_acc_id,
                        name="Primary Account",
                        tier="ultra",
                        cookies=cookies,
                        service_cookies={"notebooklm": nb_map} if nb_map else {},
                        snlm0e=msg.get("xsrf_token") if isinstance(msg.get("xsrf_token"), str) else None,
                        health_status="healthy",
                    )
                    vdata.active_account_id = target_acc_id
                vault.store_vault_data(vdata)
                saved = True
            except Exception as e:
                sys.stderr.write(f"Vault save error: {type(e).__name__}\n")
                sys.stderr.flush()
                return {"ok": False, "saved_to_vault": False, "error": "Failed persisting to vault"}

        return {
            "ok": True,
            "saved_to_vault": saved,
            "cookie_count": len(cookies),
            "gateway_notified": notified,
        }

    return {"ok": False, "error": f"Unknown message type: {msg_type}"}


def main() -> None:
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

    while True:
        try:
            msg = read_native_message(sys.stdin.buffer)
            if msg is None:
                break
            resp = handle_native_message(msg)
            write_native_message(sys.stdout.buffer, resp)
        except Exception as exc:
            sys.stderr.write(f"NMH unhandled exception: {type(exc).__name__}\n")
            sys.stderr.flush()
            break


if __name__ == "__main__":
    main()
