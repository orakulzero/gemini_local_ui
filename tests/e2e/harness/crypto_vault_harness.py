# tests/e2e/harness/crypto_vault_harness.py
"""Windows DPAPI credential isolation, browser profile extractors, and localhost security guard."""

from __future__ import annotations
import os
import sys
import re
import ctypes
import json
import sqlite3
import base64
from typing import Any, Dict, List, Optional, Tuple

class DPAPIVault:
    """Credential vault providing Windows DPAPI encryption at rest with fallback test cipher."""

    @staticmethod
    def _is_windows() -> bool:
        return sys.platform == "win32"

    @classmethod
    def encrypt_data(cls, plaintext: bytes) -> bytes:
        if cls._is_windows():
            try:
                # Use Windows CryptProtectData
                class DATA_BLOB(ctypes.Structure):
                    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

                crypt32 = ctypes.windll.crypt32
                blob_in = DATA_BLOB(len(plaintext), ctypes.cast(ctypes.c_char_p(plaintext), ctypes.POINTER(ctypes.c_char)))
                blob_out = DATA_BLOB()
                if crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
                    out_bytes = ctypes.string_at(blob_out.pbData, blob_out.cbData)
                    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                    return b"DPAPI:" + out_bytes
            except Exception:
                pass

        # Portable fallback for testing/isolation
        # Obfuscation layer prefix
        encoded = base64.b64encode(bytes(b ^ 0x5A for b in plaintext))
        return b"MOCK_VAULT:" + encoded

    @classmethod
    def decrypt_data(cls, ciphertext: bytes) -> bytes:
        if not ciphertext:
            return b""

        if ciphertext.startswith(b"DPAPI:") and cls._is_windows():
            try:
                class DATA_BLOB(ctypes.Structure):
                    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

                crypt32 = ctypes.windll.crypt32
                raw = ciphertext[6:]
                blob_in = DATA_BLOB(len(raw), ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)))
                blob_out = DATA_BLOB()
                if crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
                    out_bytes = ctypes.string_at(blob_out.pbData, blob_out.cbData)
                    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                    return out_bytes
            except Exception:
                raise ValueError("DPAPI Decryption failed: invalid context or corrupted ciphertext")

        if ciphertext.startswith(b"MOCK_VAULT:"):
            raw = base64.b64decode(ciphertext[11:])
            return bytes(b ^ 0x5A for b in raw)

        raise ValueError("Unknown or corrupted ciphertext envelope")


class BrowserProfileExtractor:
    """Extracts session cookies from Chromium and Firefox profile databases."""

    @classmethod
    def extract_from_firefox_db(cls, db_path: str) -> Dict[str, str]:
        if not os.path.exists(db_path):
            return {}

        cookies = {}
        conn = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%google.com' AND name IN ('__Secure-1PSID', '__Secure-1PSIDTS')"
            )
            for name, val in cursor.fetchall():
                cookies[name] = val
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        return cookies

    @classmethod
    def create_mock_firefox_profile(cls, temp_dir: str, cookies: Dict[str, str]) -> str:
        db_path = os.path.join(temp_dir, "cookies.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER);")
        for k, v in cookies.items():
            conn.execute("INSERT INTO moz_cookies VALUES (?, ?, '.google.com', '/', 1800000000)", (k, v))
        conn.commit()
        conn.close()
        return db_path


class LocalSecurityGuard:
    """Enforces strict localhost binding, Host header verification, CORS, and anti-CSRF."""

    ALLOWED_HOST_PATTERNS = [
        re.compile(r"^127\.0\.0\.1(:\d+)?$"),
        re.compile(r"^localhost(:\d+)?$"),
        re.compile(r"^\[::1\](:\d+)?$"),
    ]

    ALLOWED_ORIGINS = [
        "http://127.0.0.1",
        "http://localhost",
    ]

    @classmethod
    def verify_host(cls, host_header: Optional[str]) -> bool:
        if not host_header:
            return False
        host = host_header.strip().lower()
        for pat in cls.ALLOWED_HOST_PATTERNS:
            if pat.match(host):
                return True
        return False

    @classmethod
    def verify_origin(cls, origin_header: Optional[str]) -> bool:
        if not origin_header:
            # Direct non-browser calls (e.g. curl, python sdk) often omit Origin
            return True
        origin = origin_header.strip().lower()
        for allowed in cls.ALLOWED_ORIGINS:
            if origin == allowed or origin.startswith(f"{allowed}:"):
                return True
        return False

    @classmethod
    def verify_csrf(cls, method: str, csrf_token_header: Optional[str], session_csrf_token: str) -> bool:
        if method.upper() in ("GET", "HEAD", "OPTIONS"):
            return True
        if not csrf_token_header or not session_csrf_token:
            return False
        return csrf_token_header.strip() == session_csrf_token.strip()
