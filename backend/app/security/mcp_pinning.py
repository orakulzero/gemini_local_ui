"""
backend/app/security/mcp_pinning.py
Cryptographic schema pinning for MCP tools to detect and block rug-pull drift.
"""

from __future__ import annotations

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
        if pin_file_path:
            self.pin_file = Path(pin_file_path)
        elif "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            self.pin_file = Path(tempfile.gettempdir()) / "gemini_local_ui_test_pins.json"
        else:
            local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            self.pin_file = Path(local_app) / "gemini_local_ui" / "mcp_pins.json"
        self._pins: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if self.pin_file.exists():
            try:
                return json.loads(self.pin_file.read_text("utf-8"))
            except Exception as e:
                logger.warning("Failed loading MCP pins from %s: %s", self.pin_file, e)
        return {}

    def _save(self) -> None:
        try:
            self.pin_file.parent.mkdir(parents=True, exist_ok=True)
            self.pin_file.write_text(json.dumps(self._pins, indent=2, sort_keys=True), "utf-8")
        except Exception as e:
            logger.warning("Failed writing MCP pins to %s: %s", self.pin_file, e)

    @staticmethod
    def _normalize_tool(t: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "input_schema": t.get("input_schema") or t.get("inputSchema") or {},
        }

    def compute_tools_digest(self, tools: List[Dict[str, Any]]) -> str:
        norm = [self._normalize_tool(t) for t in sorted(tools, key=lambda x: x.get("name", ""))]
        serialized = json.dumps(norm, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def verify_or_pin(
        self, connector_id: str, tools: List[Dict[str, Any]]
    ) -> Tuple[str, str, List[str]]:
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
        changed_tools: List[str] = []
        all_names = set(old_map.keys()) | set(current_tools_map.keys())
        for name in sorted(all_names):
            if name not in old_map or name not in current_tools_map:
                changed_tools.append(name)
            elif old_map[name] != current_tools_map[name]:
                changed_tools.append(name)

        return ("drift_detected", pinned_sha, changed_tools)

    def assert_unmodified(self, connector_id: str, tools: List[Dict[str, Any]]) -> None:
        status, _, changed = self.verify_or_pin(connector_id, tools)
        if status == "drift_detected":
            raise MCPSchemaDriftError(
                f"Schema drift detected for connector '{connector_id}': changed tools {changed}"
            )

    def repin(self, connector_id: str, tools: List[Dict[str, Any]]) -> str:
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
