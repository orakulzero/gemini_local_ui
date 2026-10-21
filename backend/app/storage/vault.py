"""
backend/app/storage/vault.py
Hardware and OS-level credential vault isolating Gemini session tokens.
Uses Windows DPAPI on Windows and AES-256-GCM fallback on other operating systems.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("gemini_vault")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    CRYPTPROTECT_LOCAL_MACHINE = 0x04

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL

    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p


def win32_protect_data(data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """Encrypts bytes using Windows DPAPI (CurrentUser scope)."""
    if not IS_WINDOWS:
        raise OSError("win32_protect_data is only supported on Windows")

    in_buf = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))

    entropy_blob: Optional[DATA_BLOB] = None
    if entropy:
        entropy_buf = ctypes.create_string_buffer(entropy, len(entropy))
        entropy_blob = DATA_BLOB(len(entropy), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_byte)))

    out_blob = DATA_BLOB()

    success = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "GeminiLocalUI_Credential",
        ctypes.byref(entropy_blob) if entropy_blob else None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )

    if not success:
        err = ctypes.GetLastError()
        raise OSError(f"CryptProtectData failed with Win32 error code: {err}")

    try:
        encrypted_bytes = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return encrypted_bytes
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def win32_unprotect_data(cipher_data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """Decrypts bytes using Windows DPAPI (CurrentUser scope)."""
    if not IS_WINDOWS:
        raise OSError("win32_unprotect_data is only supported on Windows")

    in_buf = ctypes.create_string_buffer(cipher_data, len(cipher_data))
    in_blob = DATA_BLOB(len(cipher_data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))

    entropy_blob: Optional[DATA_BLOB] = None
    if entropy:
        entropy_buf = ctypes.create_string_buffer(entropy, len(entropy))
        entropy_blob = DATA_BLOB(len(entropy), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_byte)))

    out_blob = DATA_BLOB()

    success = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob) if entropy_blob else None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )

    if not success:
        err = ctypes.GetLastError()
        raise OSError(f"CryptUnprotectData failed with Win32 error code: {err}")

    try:
        decrypted_bytes = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return decrypted_bytes
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def get_machine_derived_key() -> bytes:
    """Derives a stable, host-specific 256-bit key using machine identifiers."""
    ident = ""
    if os.path.exists("/etc/machine-id"):
        try:
            with open("/etc/machine-id", "r", encoding="utf-8") as f:
                ident = f.read().strip()
        except Exception:
            pass
    elif os.path.exists("/var/lib/dbus/machine-id"):
        try:
            with open("/var/lib/dbus/machine-id", "r", encoding="utf-8") as f:
                ident = f.read().strip()
        except Exception:
            pass

    if not ident:
        user = os.environ.get("USER", os.environ.get("USERNAME", "default_user"))
        home = os.environ.get("HOME", os.environ.get("USERPROFILE", ""))
        ident = f"{user}_{home}_gemini_vault_machine_id"

    salt = b"gemini_local_ui_host_bound_salt_v1"
    return hashlib.pbkdf2_hmac("sha256", ident.encode("utf-8"), salt, 100_000, 32)


def fallback_protect_data(data: bytes) -> bytes:
    """Cross-platform AES-256-GCM encryption with 12-byte random nonce."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = get_machine_derived_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce + ciphertext


def fallback_unprotect_data(cipher_data: bytes) -> bytes:
    """Cross-platform AES-256-GCM decryption."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(cipher_data) < 28:
        raise ValueError("Ciphertext too short for AES-GCM (nonce + tag required)")

    key = get_machine_derived_key()
    aesgcm = AESGCM(key)
    nonce = cipher_data[:12]
    ciphertext = cipher_data[12:]
    return aesgcm.decrypt(nonce, ciphertext, None)


from dataclasses import asdict, dataclass, field
import time


@dataclass
class AccountProfile:
    id: str
    name: str
    email: str = ""
    tier: str = "free"  # "free" | "pro" | "ultra"
    priority: int = 1
    cookies: dict[str, str] = field(default_factory=dict)
    snlm0e: Optional[str] = None
    cfb2h: str = "boq_assistant-bard-web-server_20261017.00_p0"
    fdrfje: Optional[str] = None
    qkiaye: str = "feeds/mcudyrk2a4khkz"
    tux5cc: str = "en"
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    last_rotated_at: float = field(default_factory=time.time)
    health_status: str = "healthy"
    cooldown_until: Optional[float] = None
    cooldown_reason: Optional[str] = None


@dataclass
class FailoverConfig:
    enabled: bool = True
    strategy: str = "priority_tier"  # "priority_tier" | "round_robin" | "strict_chain"
    cooldown_duration_seconds: int = 3600
    fallback_chain: list[str] = field(default_factory=list)
    notify_client_on_failover: bool = True


@dataclass
class VaultData:
    version: int = 2
    active_account_id: str = "default"
    failover_config: FailoverConfig = field(default_factory=FailoverConfig)
    accounts: dict[str, AccountProfile] = field(default_factory=dict)


class CredentialVault:
    """
    Manages persistent, encrypted storage of Google Gemini credentials.
    Uses Windows DPAPI on Windows with CurrentUser scope, and machine-bound AES-256-GCM
    fallback on Linux/macOS with POSIX 0600 file permissions.
    Supports V2 multi-account profile isolation and automated migration from legacy V1 vaults.
    """

    def __init__(
        self,
        vault_path: Optional[Path | str] = None,
        entropy: Optional[bytes] = None,
        force_fallback: bool = False,
    ) -> None:
        self.force_fallback = force_fallback
        self.entropy = entropy or b"gemini_ui_local_entropy"

        if vault_path is None:
            if IS_WINDOWS:
                base_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "gemini_local_ui"
            else:
                base_dir = Path.home() / ".gemini_local_ui"
            base_dir.mkdir(parents=True, exist_ok=True)
            self.vault_path = base_dir / "vault.enc"
        else:
            self.vault_path = Path(vault_path)
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_path = self.vault_path.parent / f"{self.vault_path.name}.bak"
        self._lock: Optional[asyncio.Lock] = None
        self._thread_lock = threading.RLock()

    @property
    def lock(self) -> asyncio.Lock:
        """Lazy-initialized asyncio.Lock bound to the active running event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def encrypt_bytes(self, raw: bytes) -> bytes:
        if IS_WINDOWS and not self.force_fallback:
            return win32_protect_data(raw, self.entropy)
        return fallback_protect_data(raw)

    def decrypt_bytes(self, encrypted: bytes) -> bytes:
        if IS_WINDOWS and not self.force_fallback:
            return win32_unprotect_data(encrypted, self.entropy)
        return fallback_unprotect_data(encrypted)

    def load_vault_data(self) -> VaultData:
        """Loads and decrypts the entire V2 vault envelope, migrating legacy V1 formats automatically."""
        if not self.vault_path.exists():
            if self.backup_path.exists():
                logger.warning("Primary vault %s missing; attempting recovery from backup %s", self.vault_path, self.backup_path)
                if self.rollback_from_backup():
                    return self.load_vault_data()
            return VaultData()
        try:
            with self._thread_lock:
                with open(self.vault_path, "rb") as f:
                    cipher = f.read()
            raw = self.decrypt_bytes(cipher)
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                return VaultData()

            # Check if V2
            if parsed.get("version") == 2 and "accounts" in parsed:
                failover_raw = parsed.get("failover_config") or {}
                failover_cfg = FailoverConfig(
                    enabled=bool(failover_raw.get("enabled", True)),
                    strategy=str(failover_raw.get("strategy", "priority_tier")),
                    cooldown_duration_seconds=int(failover_raw.get("cooldown_duration_seconds", 3600)),
                    fallback_chain=list(failover_raw.get("fallback_chain", [])),
                    notify_client_on_failover=bool(failover_raw.get("notify_client_on_failover", True)),
                )
                accounts: dict[str, AccountProfile] = {}
                for acc_id, acc_raw in parsed.get("accounts", {}).items():
                    if isinstance(acc_raw, dict):
                        accounts[acc_id] = AccountProfile(
                            id=acc_raw.get("id", acc_id),
                            name=acc_raw.get("name", acc_id),
                            email=acc_raw.get("email", ""),
                            tier=acc_raw.get("tier", "free"),
                            priority=int(acc_raw.get("priority", 1)),
                            cookies=dict(acc_raw.get("cookies", {})),
                            snlm0e=acc_raw.get("snlm0e"),
                            cfb2h=acc_raw.get("cfb2h", "boq_assistant-bard-web-server_20261017.00_p0"),
                            fdrfje=acc_raw.get("fdrfje"),
                            qkiaye=acc_raw.get("qkiaye", "feeds/mcudyrk2a4khkz"),
                            tux5cc=acc_raw.get("tux5cc", "en"),
                            created_at=float(acc_raw.get("created_at", time.time())),
                            last_used_at=float(acc_raw.get("last_used_at", time.time())),
                            last_rotated_at=float(acc_raw.get("last_rotated_at", time.time())),
                            health_status=str(acc_raw.get("health_status", "healthy")),
                            cooldown_until=acc_raw.get("cooldown_until"),
                            cooldown_reason=acc_raw.get("cooldown_reason"),
                        )
                active_id = str(parsed.get("active_account_id", "default"))
                if active_id not in accounts and accounts:
                    active_id = next(iter(accounts.keys()))
                return VaultData(
                    version=2,
                    active_account_id=active_id,
                    failover_config=failover_cfg,
                    accounts=accounts,
                )

            # Legacy V1 Migration: flat dictionary of cookies or version=1
            legacy_cookies = parsed
            default_acc = AccountProfile(
                id="default",
                name="Default Account",
                tier="free",
                priority=1,
                cookies=legacy_cookies,
            )
            migrated = VaultData(
                version=2,
                active_account_id="default",
                failover_config=FailoverConfig(),
                accounts={"default": default_acc},
            )
            self.store_vault_data(migrated)
            logger.info("Migrated legacy V1 credential vault to V2 multi-account schema")
            return migrated
        except Exception as e:
            logger.error("Failed to load/decrypt vault data from %s: %s", self.vault_path, e)
            if self.backup_path.exists():
                logger.warning("Attempting automatic recovery from shadow backup %s", self.backup_path)
                try:
                    if self.rollback_from_backup():
                        logger.info("Recovered vault data from shadow backup after primary corruption")
                        with self._thread_lock:
                            with open(self.vault_path, "rb") as f:
                                cipher = f.read()
                        raw = self.decrypt_bytes(cipher)
                        parsed = json.loads(raw.decode("utf-8"))
                        if isinstance(parsed, dict) and parsed.get("version") == 2 and "accounts" in parsed:
                            return self.load_vault_data()
                except Exception as rec_err:
                    logger.error("Rollback from shadow backup failed: %s", rec_err)
            return VaultData()

    def _write_vault_file_atomic(self, cipher: bytes, target_path: Optional[Path] = None) -> None:
        """Atomically writes encrypted payload to disk using NamedTemporaryFile, fsync, and replace."""
        dest_path = Path(target_path) if target_path else self.vault_path
        vault_dir = dest_path.parent
        vault_dir.mkdir(parents=True, exist_ok=True)

        with self._thread_lock:
            tmp_file = tempfile.NamedTemporaryFile(
                dir=vault_dir,
                delete=False,
                prefix="vault_",
                suffix=".tmp",
            )
            tmp_path = Path(tmp_file.name)
            try:
                tmp_file.write(cipher)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
                tmp_file.close()

                if not IS_WINDOWS:
                    try:
                        os.chmod(tmp_path, 0o600)
                    except Exception:
                        pass

                # Windows retry loop to defend against transient [WinError 32] file sharing collisions
                last_err = None
                for attempt in range(5):
                    try:
                        os.replace(tmp_path, dest_path)
                        break
                    except PermissionError as e:
                        last_err = e
                        if attempt < 4:
                            time.sleep(0.015 * (attempt + 1))
                        else:
                            raise
            except Exception:
                try:
                    tmp_file.close()
                except Exception:
                    pass
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                raise

    @staticmethod
    def is_healthy_profile(profile: AccountProfile) -> bool:
        """Determines if an account profile holds valid, healthy authentication credentials."""
        if not profile or not profile.cookies or not isinstance(profile.cookies, dict):
            return False
        psid = profile.cookies.get("__Secure-1PSID")
        if not psid or not isinstance(psid, str) or len(psid.strip()) < 5:
            return False
        if str(getattr(profile, "health_status", "")).lower() == "expired":
            return False
        return True

    def is_vault_healthy(self, data: VaultData) -> bool:
        """Checks if vault contains at least one account with healthy credentials."""
        if not data or not data.accounts:
            return False
        return any(self.is_healthy_profile(acc) for acc in data.accounts.values())

    def store_vault_data(self, data: VaultData) -> None:
        """Encrypts and atomically writes V2 VaultData to disk and maintains shadow backup if healthy."""
        payload = {
            "version": 2,
            "active_account_id": data.active_account_id,
            "failover_config": asdict(data.failover_config),
            "accounts": {acc_id: asdict(acc) for acc_id, acc in data.accounts.items()},
        }
        payload_bytes = json.dumps(payload, indent=2).encode("utf-8")
        cipher = self.encrypt_bytes(payload_bytes)
        self._write_vault_file_atomic(cipher, self.vault_path)
        logger.info("V2 Vault saved to %s (accounts=%d, active=%s)", self.vault_path, len(data.accounts), data.active_account_id)

        # Automated shadow backup on every successful update of healthy credentials
        if self.is_vault_healthy(data):
            try:
                self._write_vault_file_atomic(cipher, self.backup_path)
                logger.info("Updated shadow vault backup: %s", self.backup_path)
            except Exception as e:
                logger.warning("Failed to create shadow backup %s: %s", self.backup_path, e)

    async def load_vault_data_async(self) -> VaultData:
        """Loads and decrypts VaultData off-thread."""
        return await asyncio.to_thread(self.load_vault_data)

    async def store_vault_data_async(self, data: VaultData) -> None:
        """Encrypts and atomically writes VaultData off-thread with async lock."""
        async with self.lock:
            await asyncio.to_thread(self.store_vault_data, data)

    async def save_account_async(self, profile: AccountProfile) -> None:
        """Saves or updates account profile asynchronously with async lock and off-thread execution."""
        async with self.lock:
            vdata = await asyncio.to_thread(self.load_vault_data)
            vdata.accounts[profile.id] = profile
            if not vdata.active_account_id or vdata.active_account_id not in vdata.accounts:
                vdata.active_account_id = profile.id
            await asyncio.to_thread(self.store_vault_data, vdata)

    async def update_account_cookies_async(self, account_id: str, new_cookies: dict[str, str]) -> None:
        """Updates account cookies without lost updates, fully protected by async lock and off-thread execution."""
        if not account_id or not new_cookies:
            return
        async with self.lock:
            vdata = await asyncio.to_thread(self.load_vault_data)
            acc = vdata.accounts.get(account_id)
            if not acc:
                if account_id == vdata.active_account_id and vdata.accounts:
                    acc = next(iter(vdata.accounts.values()))
            if acc:
                if not isinstance(acc.cookies, dict):
                    acc.cookies = {}
                acc.cookies.update(new_cookies)
                acc.last_rotated_at = time.time()
                acc.last_used_at = time.time()
                await asyncio.to_thread(self.store_vault_data, vdata)

    async def store_credentials_async(self, creds: dict[str, Any]) -> None:
        """Asynchronously updates credentials for active account."""
        async with self.lock:
            await asyncio.to_thread(self.store_credentials, creds)


    def store_credentials(self, creds: dict[str, Any]) -> None:
        """Backwards-compatible credentials setter updating the active account's cookies."""
        vdata = self.load_vault_data()
        act_id = vdata.active_account_id or "default"
        psid = creds.get("__Secure-1PSID") if isinstance(creds, dict) else None
        is_valid_psid = bool(psid and isinstance(psid, str) and len(psid.strip()) >= 5)
        if act_id in vdata.accounts:
            vdata.accounts[act_id].cookies = dict(creds)
            vdata.accounts[act_id].last_used_at = time.time()
            if is_valid_psid:
                vdata.accounts[act_id].health_status = "healthy"
        else:
            vdata.accounts[act_id] = AccountProfile(
                id=act_id,
                name="Default Account",
                tier="free",
                priority=1,
                cookies=dict(creds),
                health_status="healthy" if is_valid_psid else "unauthenticated",
            )
            vdata.active_account_id = act_id
        self.store_vault_data(vdata)

    def load_credentials(self) -> Optional[dict[str, Any]]:
        """Backwards-compatible credentials getter returning the active account's cookies."""
        vdata = self.load_vault_data()
        if not vdata.accounts:
            return None
        active = vdata.accounts.get(vdata.active_account_id)
        if not active:
            active = next(iter(vdata.accounts.values()))
        return dict(active.cookies)

    def list_accounts(self) -> list[AccountProfile]:
        """Returns all configured account profiles."""
        return list(self.load_vault_data().accounts.values())

    def get_account(self, account_id: str) -> Optional[AccountProfile]:
        """Retrieves an account profile by ID."""
        return self.load_vault_data().accounts.get(account_id)

    def save_account(self, profile: AccountProfile) -> None:
        """Creates or updates an account profile."""
        vdata = self.load_vault_data()
        vdata.accounts[profile.id] = profile
        if not vdata.active_account_id or vdata.active_account_id not in vdata.accounts:
            vdata.active_account_id = profile.id
        self.store_vault_data(vdata)

    def delete_account(self, account_id: str) -> bool:
        """Deletes an account profile by ID."""
        vdata = self.load_vault_data()
        if account_id not in vdata.accounts:
            return False
        del vdata.accounts[account_id]
        if vdata.active_account_id == account_id:
            vdata.active_account_id = next(iter(vdata.accounts.keys())) if vdata.accounts else "default"
        self.store_vault_data(vdata)
        return True

    def set_active_account(self, account_id: str) -> bool:
        """Sets the active primary account ID."""
        vdata = self.load_vault_data()
        if account_id not in vdata.accounts:
            return False
        vdata.active_account_id = account_id
        vdata.accounts[account_id].last_used_at = time.time()
        self.store_vault_data(vdata)
        return True

    def get_active_account_id(self) -> str:
        """Returns current active account ID."""
        return self.load_vault_data().active_account_id

    def get_failover_config(self) -> FailoverConfig:
        """Returns current auto-failover configuration."""
        return self.load_vault_data().failover_config

    def update_failover_config(self, config: FailoverConfig) -> None:
        """Updates auto-failover configuration."""
        vdata = self.load_vault_data()
        vdata.failover_config = config
        self.store_vault_data(vdata)

    def rollback_from_backup(self) -> bool:
        """
        Restores primary vault.enc from shadow backup vault.enc.bak.
        Returns True if rollback succeeded and produced healthy vault credentials.
        """
        if not self.backup_path.exists():
            logger.warning("Rollback requested but backup file %s does not exist", self.backup_path)
            return False

        with self._thread_lock:
            try:
                with open(self.backup_path, "rb") as f:
                    backup_cipher = f.read()

                if not backup_cipher:
                    logger.error("Backup file %s is empty", self.backup_path)
                    return False

                # Verify backup decrypts cleanly and contains valid structure
                raw = self.decrypt_bytes(backup_cipher)
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict) or "accounts" not in parsed:
                    logger.error("Backup file %s does not contain valid V2 vault structure", self.backup_path)
                    return False

                accounts = parsed.get("accounts", {})
                has_cookies = any(
                    isinstance(acc, dict)
                    and isinstance(acc.get("cookies", {}).get("__Secure-1PSID"), str)
                    and len(acc.get("cookies", {}).get("__Secure-1PSID", "").strip()) >= 5
                    and str(acc.get("health_status", "")).lower() != "expired"
                    for acc in accounts.values()
                )
                if not has_cookies:
                    logger.error("Backup file %s contains no healthy credentials", self.backup_path)
                    return False

                # Atomically write backup cipher to primary vault path
                self._write_vault_file_atomic(backup_cipher, self.vault_path)
                logger.info("Successfully rolled back vault from backup %s to %s", self.backup_path, self.vault_path)
                return True
            except Exception as e:
                logger.error("Failed to rollback vault from %s: %s", self.backup_path, e)
                return False

    async def rollback_from_backup_async(self) -> bool:
        """Asynchronously rolls back vault from shadow backup with lock and off-thread execution."""
        async with self.lock:
            return await asyncio.to_thread(self.rollback_from_backup)

    def clear(self) -> None:
        """Deletes encrypted credentials file and shadow backup file."""
        if self.vault_path.exists():
            try:
                self.vault_path.unlink()
                logger.info("Credentials file cleared: %s", self.vault_path)
            except Exception as e:
                logger.warning("Could not delete credentials file %s: %s", self.vault_path, e)
        if self.backup_path.exists():
            try:
                self.backup_path.unlink()
                logger.info("Credentials backup file cleared: %s", self.backup_path)
            except Exception as e:
                logger.warning("Could not delete credentials backup file %s: %s", self.backup_path, e)
