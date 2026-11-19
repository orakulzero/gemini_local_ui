"""
backend/app/storage/workspace_crypto.py
DPAPI and machine-bound encryption helpers for connector secrets with legacy fallback.
"""

from __future__ import annotations

import base64
import json
from typing import Dict

from backend.app.storage.vault import CredentialVault

_DPAPI_PREFIX = "dpapi_v2:"


def _xor_crypt(data_bytes: bytes, key_str: str) -> bytes:
    kb = key_str.encode("utf-8") if key_str else b"gemini_legacy_key"
    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data_bytes))


def encrypt_map(m: Dict[str, str], key: str = "") -> str:
    if not m:
        return ""
    raw = json.dumps(m).encode("utf-8")
    cipher = CredentialVault().encrypt_bytes(raw)
    return _DPAPI_PREFIX + base64.b64encode(cipher).decode("ascii")


def decrypt_map(enc_str: str, key: str = "") -> Dict[str, str]:
    if not enc_str:
        return {}
    if enc_str.startswith(_DPAPI_PREFIX):
        b64_part = enc_str[len(_DPAPI_PREFIX):]
        try:
            cipher = base64.b64decode(b64_part.encode("ascii"))
            raw = CredentialVault().decrypt_bytes(cipher)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
    # Legacy XOR fallback for non-dpapi_v2 strings
    try:
        raw = _xor_crypt(base64.b64decode(enc_str.encode("ascii")), key)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
