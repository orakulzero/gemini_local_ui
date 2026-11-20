"""
backend/app/security/mcp_pinning.py
Cryptographic schema pinning for MCP tools to detect and block rug-pull drift.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("gemini_security.mcp_pinning")


class MCPSchemaDriftError(Exception):
    """Raised when an MCP connector's tool schema drifts from its pinned digest."""


class MCPSchemaPinStore:
    def __init__(self, pin_file_path: Optional[Union[Path, str]] = None) -> None:
        self._tampered: bool = False
        if pin_file_path:
            self.pin_file = Path(pin_file_path)
        elif "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            self.pin_file = Path(tempfile.gettempdir()) / f"gemini_local_ui_pytest_{os.getpid()}" / "mcp_pins.json"
        else:
            local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            self.pin_file = Path(local_app) / "gemini_local_ui" / "mcp_pins.json"
        self._pins: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.pin_file.exists():
            return {}
        try:
            content = self.pin_file.read_text("utf-8").strip()
            if not content:
                return {}
            if content.startswith("dpapi_v2:"):
                try:
                    from backend.app.storage.vault import CredentialVault
                    b64_part = content[len("dpapi_v2:"):].strip()
                    raw = CredentialVault().decrypt_bytes(base64.b64decode(b64_part))
                    return json.loads(raw.decode("utf-8"))
                except Exception as dec_err:
                    logger.warning("Decryption failed for tampered MCP pins %s: %s", self.pin_file, dec_err)
                    self._tampered = True
                    return {}
            return json.loads(content)
        except Exception as e:
            logger.warning("Failed loading MCP pins from %s: %s", self.pin_file, e)
            return {}

    def _save(self) -> None:
        try:
            self.pin_file.parent.mkdir(parents=True, exist_ok=True)
            from backend.app.storage.vault import CredentialVault
            raw_bytes = json.dumps(self._pins, indent=2, sort_keys=True).encode("utf-8")
            cipher = CredentialVault().encrypt_bytes(raw_bytes)
            enc_str = "dpapi_v2:" + base64.b64encode(cipher).decode("ascii")
            tmp_fd, tmp_path = tempfile.mkstemp(dir=self.pin_file.parent, prefix="pins_tmp_")
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(enc_str)
            os.replace(tmp_path, self.pin_file)
        except Exception as e:
            logger.warning("Failed writing encrypted MCP pins to %s: %s", self.pin_file, e)

    @staticmethod
    def _normalize_tool(t: Dict[str, Any]) -> Dict[str, Any]:
        raw_ann = t.get("annotations") or {}
        norm_ann = {
            "read_only_hint": bool(
                raw_ann.get("read_only_hint")
                or raw_ann.get("readOnlyHint")
                or t.get("read_only_hint")
                or t.get("readOnlyHint")
            ),
            "destructive_hint": bool(
                raw_ann.get("destructive_hint")
                or raw_ann.get("destructiveHint")
                or t.get("destructive_hint")
                or t.get("destructiveHint")
            ),
            "idempotent_hint": bool(
                raw_ann.get("idempotent_hint")
                or raw_ann.get("idempotentHint")
                or t.get("idempotent_hint")
                or t.get("idempotentHint")
            ),
            "open_world_hint": bool(
                raw_ann.get("open_world_hint")
                or raw_ann.get("openWorldHint")
                or t.get("open_world_hint")
                or t.get("openWorldHint")
            ),
        }
        return {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "input_schema": t.get("input_schema") or t.get("inputSchema") or {},
            "output_schema": t.get("output_schema") or t.get("outputSchema") or {},
            "annotations": norm_ann,
        }

    def compute_tools_digest(self, tools: List[Dict[str, Any]]) -> str:
        norm = [self._normalize_tool(t) for t in sorted(tools, key=lambda x: x.get("name", ""))]
        serialized = json.dumps(norm, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def verify_or_pin(
        self, connector_id: str, tools: List[Dict[str, Any]]
    ) -> Tuple[str, str, List[str]]:
        if self._tampered:
            return ("drift_detected", "tampered", ["*"])

        current_sha = self.compute_tools_digest(tools)
        current_tools_map = {t.get("name", ""): self._normalize_tool(t) for t in tools}

        pinned_info = self._pins.get(connector_id)
        if not pinned_info:
            self._pins[connector_id] = {
                "sha256": current_sha,
                "tools": current_tools_map,
            }
            self._save()
            return ("pinned", current_sha, [])

        pinned_sha = pinned_info.get("sha256", "")
        if current_sha == pinned_sha:
            return ("verified", current_sha, [])

        old_map = pinned_info.get("tools", {})
        if old_map and any("annotations" not in v for v in old_map.values()):
            if set(old_map.keys()) == set(current_tools_map.keys()):
                v1_match = True
                for name, old_t in old_map.items():
                    cur_t = current_tools_map[name]
                    if (
                        old_t.get("name", "") != cur_t.get("name", "")
                        or old_t.get("description", "") != cur_t.get("description", "")
                        or (old_t.get("input_schema") or {}) != (cur_t.get("input_schema") or {})
                    ):
                        v1_match = False
                        break
                if v1_match:
                    self._pins[connector_id] = {
                        "sha256": current_sha,
                        "tools": current_tools_map,
                    }
                    self._save()
                    return ("verified", current_sha, [])

        changed_tools: List[str] = []
        all_names = set(old_map.keys()) | set(current_tools_map.keys())
        for name in sorted(all_names):
            if name not in old_map or name not in current_tools_map:
                changed_tools.append(name)
            elif old_map[name] != current_tools_map[name]:
                changed_tools.append(name)

        return ("drift_detected", pinned_sha, changed_tools)

    def assert_unmodified(self, connector_id: str, tools: List[Dict[str, Any]]) -> None:
        if self._tampered:
            raise MCPSchemaDriftError(f"Schema pin store tampered or corrupted for '{connector_id}'")
        status, _, changed = self.verify_or_pin(connector_id, tools)
        if status == "drift_detected":
            raise MCPSchemaDriftError(
                f"Schema drift detected for connector '{connector_id}': changed tools {changed}"
            )

    def repin(self, connector_id: str, tools: List[Dict[str, Any]]) -> str:
        self._tampered = False
        current_sha = self.compute_tools_digest(tools)
        current_tools_map = {t.get("name", ""): self._normalize_tool(t) for t in tools}
        self._pins[connector_id] = {
            "sha256": current_sha,
            "tools": current_tools_map,
        }
        self._save()
        return current_sha


_pin_store: Optional[MCPSchemaPinStore] = None


def get_schema_pin_store() -> MCPSchemaPinStore:
    global _pin_store
    if _pin_store is None:
        _pin_store = MCPSchemaPinStore()
    return _pin_store
