#!/usr/bin/env python3
"""
AGY-Ultra-Cloud-24x7 — Cross-Platform Session Vault Bridge (Windows DPAPI <-> Cloud Linux AES-256-GCM)
========================================================================================================
Solves MAJOR-06 from cloud_readiness_egm_review.md:
1. Windows Local Host (`--export`):
   - Reads `%LOCALAPPDATA%\\gemini_local_ui\\vault.enc` via `CredentialVault` (Windows DPAPI)
   - Encrypts the V2 VaultData JSON with a portable 256-bit AES-GCM bridge key (`vault_bridge.key`)
   - Outputs `cloud_session_bundle.enc` and `cloud_session_bundle.enc.sha256` for `drive_state/`
2. Cloud Linux Container (`--import-cloud`):
   - Fetches `cloud_session_bundle.enc` from Google Drive `drive_state/` (or `/home/antigravity/.gemini/cloud_runtime/`)
   - Decrypts with `vault_bridge.key`
   - Populates `/home/antigravity/.gemini_local_ui/vault.enc` via `CredentialVault(force_fallback=True)`
     bound to the current container's `/etc/machine-id`, enabling live `GeminiWebClient` (`gemini-4-argon`,
     Deep Think `thinking_flag=3`, `/gemini-task`) and `NotebookLMClient` execution in the cloud.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BRIDGE_MAGIC = b"AGY_BRIDGE_V1:"
DRIVE_STATE_FOLDER_ID = "1-yiUoG9KqKMPzI0pl7YwI3-iuviMyplR"

CLOUD_HOME = Path("/home/antigravity")
CLOUD_RUNTIME_DIR = CLOUD_HOME / ".gemini" / "cloud_runtime"
DEFAULT_KEY_PATH = CLOUD_RUNTIME_DIR / "vault_bridge.key"
DEFAULT_CLOUD_BUNDLE_PATH = CLOUD_RUNTIME_DIR / "cloud_session_bundle.enc"
DRIVE_MIRROR_STATE_DIR = CLOUD_HOME / "AGY_CLOUD_BRIDGE" / "drive_state"


def _add_gemini_local_ui_to_syspath() -> Optional[Path]:
    candidates = [
        CLOUD_HOME / "orakulzero" / "gemini_local_ui",
        Path(__file__).resolve().parent / "orakulzero" / "gemini_local_ui",
        Path(__file__).resolve().parent.parent,
        Path.cwd(),
    ]
    for cand in candidates:
        if (cand / "backend" / "app" / "storage" / "vault.py").exists():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            return cand
    return None


def load_or_create_bridge_key(key_path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Loads a 32-byte key from AGY_CLOUD_VAULT_KEY env var or key_path (generating one if missing)."""
    env_hex = os.environ.get("AGY_CLOUD_VAULT_KEY", "").strip()
    if env_hex:
        raw = bytes.fromhex(env_hex)
        if len(raw) != 32:
            raise ValueError("AGY_CLOUD_VAULT_KEY must be 64 hex chars (32 bytes)")
        return raw

    if key_path.exists():
        hex_str = key_path.read_text(encoding="utf-8").strip()
        raw = bytes.fromhex(hex_str)
        if len(raw) != 32:
            raise ValueError(f"Key file {key_path} must contain 64 hex chars (32 bytes)")
        return raw

    key_path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(32)
    key_path.write_text(raw.hex() + "\n", encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return raw


def encrypt_portable_bundle(payload_dict: Dict[str, Any], key: bytes) -> bytes:
    """Encrypts a V2 vault dictionary with AES-256-GCM using the portable 32-byte bridge key."""
    plaintext = json.dumps(payload_dict, indent=2, ensure_ascii=False).encode("utf-8")
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, BRIDGE_MAGIC)
    return BRIDGE_MAGIC + nonce + ciphertext


def decrypt_portable_bundle(bundle_bytes: bytes, key: bytes) -> Dict[str, Any]:
    """Decrypts an AGY_BRIDGE_V1 bundle back into a V2 vault dictionary."""
    if not bundle_bytes.startswith(BRIDGE_MAGIC):
        raise ValueError("Invalid bundle header: expected AGY_BRIDGE_V1:")
    body = bundle_bytes[len(BRIDGE_MAGIC) :]
    if len(body) < 28:
        raise ValueError("Truncated AGY_BRIDGE_V1 bundle")
    nonce = body[:12]
    ciphertext = body[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, BRIDGE_MAGIC)
    parsed = json.loads(plaintext.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Decrypted bundle payload is not a JSON object")
    return parsed


def export_local_vault_to_bundle(
    key_path: Path,
    out_bundle_path: Path,
    vault_path: Optional[Path] = None,
    force_fallback: bool = False,
) -> Dict[str, Any]:
    """Reads the local OS CredentialVault (Windows DPAPI or Linux fallback) and writes cloud_session_bundle.enc."""
    _add_gemini_local_ui_to_syspath()
    from backend.app.storage.vault import CredentialVault

    vault = CredentialVault(vault_path=vault_path, force_fallback=force_fallback)
    vdata = vault.load_vault_data()
    if not vdata.accounts:
        raise RuntimeError(
            f"No accounts found in local vault ({vault.vault_path}). "
            "Log in or extract browser cookies in gemini_local_ui first."
        )

    payload = {
        "version": 2,
        "exported_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_platform": sys.platform,
        "active_account_id": vdata.active_account_id,
        "failover_config": asdict(vdata.failover_config),
        "accounts": {acc_id: asdict(acc) for acc_id, acc in vdata.accounts.items()},
    }

    key = load_or_create_bridge_key(key_path)
    bundle_bytes = encrypt_portable_bundle(payload, key)
    sha256_hex = hashlib.sha256(bundle_bytes).hexdigest()

    out_bundle_path.parent.mkdir(parents=True, exist_ok=True)
    out_bundle_path.write_bytes(bundle_bytes)
    sha_path = Path(str(out_bundle_path) + ".sha256")
    sha_path.write_text(f"{sha256_hex}  {out_bundle_path.name}\n", encoding="utf-8")

    healthy_count = sum(1 for acc in vdata.accounts.values() if vault.is_healthy_profile(acc))
    return {
        "status": "exported",
        "source_vault": str(vault.vault_path),
        "bundle_path": str(out_bundle_path),
        "sha256_path": str(sha_path),
        "sha256": sha256_hex,
        "accounts_total": len(vdata.accounts),
        "accounts_healthy": healthy_count,
        "active_account_id": vdata.active_account_id,
    }


def _find_gdrive_bin() -> str:
    PersistentBin = CLOUD_HOME / ".gemini" / "jetski" / "plugin_data" / "gdrive" / "bin"
    if PersistentBin.exists():
        bins = sorted(PersistentBin.glob("gdrive-*"))
        if bins:
            return str(bins[-1])
    return "gdrive"


def fetch_bundle_from_drive_if_present(dest_bundle_path: Path) -> Optional[str]:
    """Checks Google Drive drive_state/ for cloud_session_bundle.enc and downloads it if present."""
    gdrive_bin = _find_gdrive_bin()
    try:
        res = subprocess.run(
            [gdrive_bin, "readonly", "ls", DRIVE_STATE_FOLDER_ID, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return None
        items = json.loads(res.stdout)
        if not isinstance(items, list):
            return None
        file_id = None
        for item in items:
            if isinstance(item, dict) and item.get("name") == "cloud_session_bundle.enc":
                file_id = item.get("id")
                break
        if not file_id:
            return None

        dest_bundle_path.parent.mkdir(parents=True, exist_ok=True)
        dl = subprocess.run(
            [gdrive_bin, "readonly", "download", file_id, "--out", str(dest_bundle_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if dl.returncode == 0 and dest_bundle_path.exists() and dest_bundle_path.stat().st_size > 32:
            return str(file_id)
    except Exception:
        return None
    return None


def import_bundle_to_cloud_vault(
    key_path: Path = DEFAULT_KEY_PATH,
    bundle_path: Optional[Path] = None,
    target_vault_path: Optional[Path] = None,
    poll_drive: bool = True,
) -> Dict[str, Any]:
    """
    Imports cloud_session_bundle.enc into the Cloud Linux CredentialVault (~/.gemini_local_ui/vault.enc)
    bound to the current container's /etc/machine-id, and caches the portable bundle in ~/.gemini/cloud_runtime/.
    """
    _add_gemini_local_ui_to_syspath()
    from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig, VaultData

    drive_file_id: Optional[str] = None
    candidate_paths = []
    if bundle_path:
        candidate_paths.append(Path(bundle_path))
    else:
        candidate_paths.extend(
            [
                DRIVE_MIRROR_STATE_DIR / "cloud_session_bundle.enc",
                DEFAULT_CLOUD_BUNDLE_PATH,
            ]
        )

    if poll_drive:
        drive_target = DRIVE_MIRROR_STATE_DIR / "cloud_session_bundle.enc"
        drive_file_id = fetch_bundle_from_drive_if_present(drive_target)

    resolved_bundle: Optional[Path] = None
    for p in candidate_paths:
        if p.exists() and p.stat().st_size > 32:
            resolved_bundle = p
            break

    if resolved_bundle is None:
        return {
            "status": "awaiting_bundle",
            "message": "No cloud_session_bundle.enc found in Google Drive drive_state/ or local runtime cache.",
            "expected_drive_folder_id": DRIVE_STATE_FOLDER_ID,
            "key_path": str(key_path),
        }

    key = load_or_create_bridge_key(key_path)
    bundle_bytes = resolved_bundle.read_bytes()
    sha256_hex = hashlib.sha256(bundle_bytes).hexdigest()

    parsed = decrypt_portable_bundle(bundle_bytes, key)

    # Persist verified portable bundle in ~/.gemini/cloud_runtime/ on production imports
    if target_vault_path is None and resolved_bundle.resolve() != DEFAULT_CLOUD_BUNDLE_PATH.resolve():
        DEFAULT_CLOUD_BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_CLOUD_BUNDLE_PATH.write_bytes(bundle_bytes)

    failover_raw = parsed.get("failover_config") or {}
    failover_cfg = FailoverConfig(
        enabled=bool(failover_raw.get("enabled", True)),
        strategy=str(failover_raw.get("strategy", "priority_tier")),
        cooldown_duration_seconds=int(failover_raw.get("cooldown_duration_seconds", 3600)),
        fallback_chain=list(failover_raw.get("fallback_chain", [])),
        notify_client_on_failover=bool(failover_raw.get("notify_client_on_failover", True)),
    )

    accounts: Dict[str, AccountProfile] = {}
    for acc_id, acc_raw in parsed.get("accounts", {}).items():
        if isinstance(acc_raw, dict):
            accounts[acc_id] = AccountProfile(
                id=acc_raw.get("id", acc_id),
                name=acc_raw.get("name", acc_id),
                email=acc_raw.get("email", ""),
                tier=acc_raw.get("tier", "ultra"),
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

    vdata = VaultData(
        version=2,
        active_account_id=active_id,
        failover_config=failover_cfg,
        accounts=accounts,
    )

    vault = CredentialVault(vault_path=target_vault_path, force_fallback=True)
    vault.store_vault_data(vdata)

    # Verify round-trip read from Linux machine-bound vault
    reloaded = vault.load_vault_data()
    healthy_count = sum(1 for acc in reloaded.accounts.values() if vault.is_healthy_profile(acc))

    return {
        "status": "imported",
        "bundle_source": str(resolved_bundle),
        "drive_file_id": drive_file_id,
        "bundle_sha256": sha256_hex,
        "linux_vault_path": str(vault.vault_path),
        "accounts_total": len(reloaded.accounts),
        "accounts_healthy": healthy_count,
        "active_account_id": reloaded.active_account_id,
    }


def run_self_test() -> Dict[str, Any]:
    """Runs an isolated round-trip cryptographic verification of Windows->Bundle->Linux vault hydration."""
    import tempfile

    _add_gemini_local_ui_to_syspath()
    from backend.app.storage.vault import AccountProfile, CredentialVault

    with tempfile.TemporaryDirectory(prefix="agy_vault_bridge_test_") as tmp_dir:
        tmp = Path(tmp_dir)
        test_key_path = tmp / "vault_bridge.key"
        src_vault_path = tmp / "source_windows_sim_vault.enc"
        bundle_path = tmp / "cloud_session_bundle.enc"
        dst_vault_path = tmp / "dest_linux_cloud_vault.enc"

        # 1. Populate simulated source vault with Ultra profile + cookies + SNlM0e
        src_vault = CredentialVault(vault_path=src_vault_path, force_fallback=True)
        src_vault.save_account(
            AccountProfile(
                id="ultra_primary",
                name="AGY Ultra Cloud Account",
                email="ultra@example.com",
                tier="ultra",
                priority=1,
                cookies={
                    "__Secure-1PSID": "g.a000test_ultra_psid_token_value_1234567890",
                    "__Secure-1PSIDTS": "sidts-test_ultra_psidts_value_0987654321",
                },
                snlm0e="SNlM0e_test_csrf_token:1730000000",
                health_status="healthy",
            )
        )

        # 2. Export to portable AES-256-GCM bundle
        exp_res = export_local_vault_to_bundle(
            key_path=test_key_path,
            out_bundle_path=bundle_path,
            vault_path=src_vault_path,
            force_fallback=True,
        )

        # 3. Import into isolated target Linux vault
        imp_res = import_bundle_to_cloud_vault(
            key_path=test_key_path,
            bundle_path=bundle_path,
            target_vault_path=dst_vault_path,
            poll_drive=False,
        )

        # 4. Verify decrypted fields match byte-for-byte
        dst_vault = CredentialVault(vault_path=dst_vault_path, force_fallback=True)
        loaded = dst_vault.load_vault_data()
        acc = loaded.accounts["ultra_primary"]
        assert acc.tier == "ultra"
        assert acc.cookies["__Secure-1PSID"] == "g.a000test_ultra_psid_token_value_1234567890"
        assert acc.snlm0e == "SNlM0e_test_csrf_token:1730000000"
        assert dst_vault.is_vault_healthy(loaded) is True

        return {
            "self_test": "PASSED",
            "export_result": exp_res,
            "import_result": imp_res,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-Platform Session Vault Bridge (Windows DPAPI <-> Cloud Linux AES-256-GCM)"
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export local OS CredentialVault (Windows DPAPI) to portable cloud_session_bundle.enc",
    )
    parser.add_argument(
        "--import-cloud",
        action="store_true",
        help="Import cloud_session_bundle.enc into Cloud Linux CredentialVault (~/.gemini_local_ui/vault.enc)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run round-trip cryptographic self-test and print JSON report",
    )
    parser.add_argument(
        "--key-path",
        type=Path,
        default=DEFAULT_KEY_PATH,
        help=f"Path to 256-bit hex bridge key (default: {DEFAULT_KEY_PATH})",
    )
    parser.add_argument(
        "--bundle-path",
        type=Path,
        default=None,
        help="Path to cloud_session_bundle.enc (for export output or import source)",
    )
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=None,
        help="Override source/target vault.enc path",
    )
    parser.add_argument(
        "--no-drive-poll",
        action="store_true",
        help="Do not query Google Drive via gdrive CLI during --import-cloud",
    )
    args = parser.parse_args()

    if args.self_test:
        res = run_self_test()
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    if args.export:
        out_bundle = args.bundle_path or Path("cloud_session_bundle.enc")
        res = export_local_vault_to_bundle(
            key_path=args.key_path,
            out_bundle_path=out_bundle,
            vault_path=args.vault_path,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    if args.import_cloud:
        res = import_bundle_to_cloud_vault(
            key_path=args.key_path,
            bundle_path=args.bundle_path,
            target_vault_path=args.vault_path,
            poll_drive=not args.no_drive_poll,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
