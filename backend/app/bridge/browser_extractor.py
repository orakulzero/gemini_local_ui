"""
backend/app/bridge/browser_extractor.py
Local browser profile cookie extractor (Chromium DPAPI WAL temp copy,
Firefox moz_cookies reader, manual string/dict/extension JSON parser).
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from backend.app.storage.vault import IS_WINDOWS, win32_unprotect_data

logger = logging.getLogger("gemini_bridge.browser_extractor")


class BrowserType(str, Enum):
    CHROME = "chrome"
    EDGE = "edge"
    BRAVE = "brave"
    FIREFOX = "firefox"
    MANUAL = "manual"


@dataclass
class CookieValidationResult:
    is_valid: bool
    has_psid: bool
    has_psidts: bool
    missing_critical: list[str]
    cookie_count: int
    source: BrowserType
    error_message: str | None = None


class BrowserExtractorError(Exception):
    """Base exception for cookie extraction failures."""


class AppBoundEncryptionError(BrowserExtractorError):
    """Raised when Chromium v20 App-Bound Encryption is detected."""


class ProfileNotFoundError(BrowserExtractorError):
    """Raised when browser user data or profile directory is not found."""


class BrowserCookieExtractor:
    """Extracts session cookies from installed desktop browsers and raw inputs."""

    @staticmethod
    def get_chromium_master_key(local_state_path: Path) -> bytes:
        """Reads Local State JSON and decrypts the AES-256 master key via DPAPI."""
        if not local_state_path.exists():
            raise ProfileNotFoundError(f"Chromium Local State file not found: {local_state_path}")

        try:
            with open(local_state_path, encoding="utf-8") as f:
                state_data = json.load(f)

            encrypted_key_b64 = state_data.get("os_crypt", {}).get("encrypted_key")
            if not encrypted_key_b64:
                raise BrowserExtractorError("os_crypt.encrypted_key missing from Local State")

            raw_key_blob = base64.b64decode(encrypted_key_b64)
            if not raw_key_blob.startswith(b"DPAPI"):
                raise BrowserExtractorError("Expected DPAPI prefix on encrypted master key")

            dpapi_payload = raw_key_blob[5:]
            if not IS_WINDOWS:
                raise BrowserExtractorError("DPAPI decryption is only supported on Windows")

            return win32_unprotect_data(dpapi_payload)
        except Exception as e:
            if isinstance(e, BrowserExtractorError):
                raise
            raise BrowserExtractorError(f"Failed to extract Chromium master key: {e}") from e

    @classmethod
    def extract_from_chromium(
        cls,
        user_data_path: Path,
        profile_name: str = "Default",
    ) -> dict[str, str]:
        """Extracts and decrypts Google cookies from a Chromium-based browser profile."""
        if not user_data_path.exists():
            raise ProfileNotFoundError(f"User data path does not exist: {user_data_path}")

        local_state_path = user_data_path / "Local State"
        cookies_db_path = user_data_path / profile_name / "Network" / "Cookies"

        # Some older or customized Chromium builds store Cookies directly under profile_name
        if not cookies_db_path.exists():
            alt_path = user_data_path / profile_name / "Cookies"
            if alt_path.exists():
                cookies_db_path = alt_path
            else:
                raise ProfileNotFoundError(f"Cookies database not found at {cookies_db_path}")

        master_key = cls.get_chromium_master_key(local_state_path)

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        extracted_cookies: dict[str, str] = {}
        has_v20 = False

        # SQLite concurrency isolation via temporary directory copy
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "Cookies"
            shutil.copy2(cookies_db_path, tmp_db)

            # Copy WAL and SHM files if present
            for ext in ("-wal", "-shm"):
                wal_file = cookies_db_path.parent / f"{cookies_db_path.name}{ext}"
                if wal_file.exists():
                    try:
                        shutil.copy2(wal_file, Path(tmp_dir) / f"Cookies{ext}")
                    except Exception as e:
                        logger.debug("Could not copy %s: %s", wal_file, e)

            conn = sqlite3.connect(f"file:{tmp_db.as_posix()}?mode=ro", uri=True)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%google.com'"
                )
                rows = cursor.fetchall()

                aesgcm = AESGCM(master_key)

                for name, enc_val in rows:
                    if not enc_val:
                        continue

                    # Check prefix
                    if enc_val.startswith((b"v10", b"v11")):
                        try:
                            nonce = enc_val[3:15]
                            ciphertext = enc_val[15:]
                            decrypted = aesgcm.decrypt(nonce, ciphertext, None)
                            extracted_cookies[name] = decrypted.decode("utf-8", errors="replace")
                        except Exception as e:
                            logger.debug("Failed decrypting cookie %s: %s", name, e)
                    elif enc_val.startswith(b"v20"):
                        has_v20 = True
                    else:
                        # Fallback for unencrypted values
                        with contextlib.suppress(Exception):
                            extracted_cookies[name] = enc_val.decode("utf-8")
            finally:
                conn.close()

        if has_v20 and not extracted_cookies:
            raise AppBoundEncryptionError(
                "Chrome 127+ App-Bound Encryption detected (v20 prefix). "
                "Chromium protected these cookies with system service isolation. "
                "Please use Firefox or manual cookie paste."
            )

        return extracted_cookies

    @classmethod
    def extract_from_firefox(
        cls,
        profiles_dir: Path | None = None,
    ) -> dict[str, str]:
        """Extracts plaintext Google cookies from the freshest Firefox profile containing a valid session."""
        if profiles_dir is None:
            if IS_WINDOWS:
                appdata = os.environ.get("APPDATA")
                if not appdata:
                    raise ProfileNotFoundError("APPDATA environment variable not found")
                profiles_dir = Path(appdata) / "Mozilla" / "Firefox" / "Profiles"
            elif sys.platform == "darwin":
                profiles_dir = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"
            else:
                profiles_dir = Path.home() / ".mozilla" / "firefox"

        if not profiles_dir.exists():
            raise ProfileNotFoundError(f"Firefox profiles directory not found: {profiles_dir}")

        candidate_profiles = [
            p for p in profiles_dir.iterdir()
            if p.is_dir() and (p / "cookies.sqlite").exists()
        ]
        if not candidate_profiles:
            raise ProfileNotFoundError("No valid Firefox profile with cookies.sqlite found")

        best_cookies: dict[str, str] = {}
        best_score: tuple[int, int, int] = (-1, -1, -1)

        for profile_dir in candidate_profiles:
            cookies_db = profile_dir / "cookies.sqlite"
            profile_cookies: dict[str, str] = {}
            max_psid_access = 0

            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_db = Path(tmp_dir) / "cookies.sqlite"
                with contextlib.suppress(Exception):
                    shutil.copy2(cookies_db, tmp_db)
                if not (Path(tmp_dir) / "cookies.sqlite").exists():
                    continue

                for ext in ("-wal", "-shm"):
                    wal_file = profile_dir / f"cookies.sqlite{ext}"
                    if wal_file.exists():
                        with contextlib.suppress(Exception):
                            shutil.copy2(wal_file, Path(tmp_dir) / f"cookies.sqlite{ext}")

                conn = sqlite3.connect(f"file:{tmp_db.as_posix()}?mode=ro", uri=True)
                try:
                    with contextlib.suppress(Exception):
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT name, value, lastAccessed FROM moz_cookies "
                            "WHERE host IN ('.google.com', 'google.com', 'gemini.google.com') ORDER BY lastAccessed ASC"
                        )
                        for name, val, last_accessed in cursor.fetchall():
                            if val:
                                profile_cookies[name] = val
                                if name == "__Secure-1PSID" and isinstance(last_accessed, int):
                                    max_psid_access = max(max_psid_access, last_accessed)
                finally:
                    conn.close()

            has_psid = 1 if "__Secure-1PSID" in profile_cookies else 0
            is_default_release = 1 if "default-release" in profile_dir.name.lower() else 0
            score = (has_psid, max_psid_access, is_default_release)
            if score > best_score:
                best_score = score
                best_cookies = profile_cookies

        return best_cookies

    @classmethod
    def parse_manual(cls, raw_input: str) -> dict[str, str]:
        """
        Parses raw Cookie headers, newline-delimited pairs, or JSON arrays/objects.
        Supports:
        1. Raw HTTP Header string ('__Secure-1PSID=val; __Secure-1PSIDTS=val2')
        2. Browser extension JSON export ([{"name": "...", "value": "..."}, ...])
        3. Key-Value dictionary/JSON object ({"__Secure-1PSID": "..."})
        """
        raw = raw_input.strip()
        if not raw:
            return {}

        # 1. Attempt JSON parsing
        if (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]")):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return {str(k).strip(): str(v).strip() for k, v in parsed.items() if v is not None}
                if isinstance(parsed, list):
                    result: dict[str, str] = {}
                    for item in parsed:
                        if isinstance(item, dict):
                            name = item.get("name")
                            val = item.get("value")
                            if name and val is not None:
                                result[str(name).strip()] = str(val).strip()
                    return result
            except json.JSONDecodeError:
                pass

        # 2. Raw Header format
        # Strip optional "Cookie: " or "cookie: " prefix
        if raw.lower().startswith("cookie:"):
            raw = raw[7:].strip()

        result = {}
        # Support semicolon or newline delimiter
        normalized = raw.replace("\r\n", ";").replace("\n", ";")
        for part in normalized.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, val = part.split("=", 1)
            key = key.strip()
            val = val.strip()
            # Strip enclosing quotes if present
            if len(val) >= 2 and ((val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'"))):
                val = val[1:-1]
            if key:
                result[key] = val

        return result

    @classmethod
    def auto_extract(cls) -> tuple[dict[str, str], BrowserType]:
        """Attempts auto-extraction in priority order: Firefox -> Chrome -> Edge -> Brave."""
        # 1. Firefox
        try:
            cookies = cls.extract_from_firefox()
            res = cls.validate_cookies(cookies, BrowserType.FIREFOX)
            if res.is_valid:
                return cookies, BrowserType.FIREFOX
        except Exception as e:
            logger.debug("Firefox cookie extraction skipped: %s", e)

        # 2. Chromium paths
        if IS_WINDOWS:
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            chromium_targets = [
                (BrowserType.CHROME, Path(local_appdata) / "Google" / "Chrome" / "User Data"),
                (BrowserType.EDGE, Path(local_appdata) / "Microsoft" / "Edge" / "User Data"),
                (BrowserType.BRAVE, Path(local_appdata) / "BraveSoftware" / "Brave-Browser" / "User Data"),
            ]
        elif sys.platform == "darwin":
            chromium_targets = [
                (BrowserType.CHROME, Path.home() / "Library" / "Application Support" / "Google" / "Chrome"),
                (BrowserType.EDGE, Path.home() / "Library" / "Application Support" / "Microsoft Edge"),
                (BrowserType.BRAVE, Path.home() / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"),
            ]
        else:
            chromium_targets = [
                (BrowserType.CHROME, Path.home() / ".config" / "google-chrome"),
                (BrowserType.EDGE, Path.home() / ".config" / "microsoft-edge"),
                (BrowserType.BRAVE, Path.home() / ".config" / "BraveSoftware" / "Brave-Browser"),
            ]

        def try_extract(b_type, p_path):
            try:
                cookies = cls.extract_from_chromium(p_path)
                res = cls.validate_cookies(cookies, b_type)
                if res.is_valid:
                    return cookies, b_type
            except AppBoundEncryptionError:
                logger.warning("%s has App-Bound Encryption enabled", b_type.value)
            except Exception as e:
                logger.debug("%s cookie extraction skipped: %s", b_type.value, e)
            return None, None

        for b_type, p_path in chromium_targets:
            c, b = try_extract(b_type, p_path)
            if c is not None:
                return c, b

        raise BrowserExtractorError("Could not automatically extract valid Google cookies from installed browsers")

    @classmethod
    def validate_cookies(
        cls,
        cookies: dict[str, str],
        source: BrowserType = BrowserType.MANUAL,
    ) -> CookieValidationResult:
        """Validates presence of essential Gemini tokens (__Secure-1PSID and __Secure-1PSIDTS)."""
        has_psid = bool(cookies.get("__Secure-1PSID"))
        has_psidts = bool(cookies.get("__Secure-1PSIDTS"))

        missing: list[str] = []
        if not has_psid:
            missing.append("__Secure-1PSID")
        if not has_psidts:
            missing.append("__Secure-1PSIDTS")

        is_valid = has_psid  # __Secure-1PSID is the hard requirement; PSIDTS can be acquired or rotated

        error_msg = None
        if not is_valid:
            error_msg = f"Missing critical authentication cookie: {', '.join(missing)}"

        return CookieValidationResult(
            is_valid=is_valid,
            has_psid=has_psid,
            has_psidts=has_psidts,
            missing_critical=missing,
            cookie_count=len(cookies),
            source=source,
            error_message=error_msg,
        )
