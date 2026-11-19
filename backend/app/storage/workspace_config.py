"""
backend/app/storage/workspace_config.py
Thread-safe storage manager for Workspace Prompts, Skills, and MCP Connectors.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.app.security.path_sandbox import validate_safe_path, validate_safe_slug
from backend.app.storage.workspace_crypto import decrypt_map, encrypt_map
from backend.app.storage.workspace_models import (
    ConnectorItem,
    PromptItem,
    SkillItem,
    get_default_workspace_config,
)
from backend.app.storage.workspace_skill_parser import discover_all_skills

logger = logging.getLogger("gemini_workspace.config")


class WorkspaceConfigStore:
    def __init__(self, base_dir: Optional[Path] = None, project_root: Optional[Path] = None):
        if base_dir is None:
            if "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ:
                base_dir = Path(tempfile.gettempdir()) / f"gemini_local_ui_pytest_{os.getpid()}"
            else:
                base_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) / "gemini_local_ui"
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.base_dir / "workspace_config.json"
        self.custom_skills_dir = self.base_dir / "custom_skills"
        self.custom_skills_dir.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        self._lock = threading.Lock()
        self._key = f"{self.base_dir}_{os.getlogin() if hasattr(os, 'getlogin') else 'gemini'}"
        self._ensure_config()

    def _ensure_config(self) -> None:
        with self._lock:
            if not self.config_path.exists():
                cfg = get_default_workspace_config(sys.executable)
                self.config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def _load(self) -> Dict[str, Any]:
        with self._lock:
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = get_default_workspace_config(sys.executable)
                self.config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                return cfg

    def _save(self, cfg: Dict[str, Any]) -> None:
        with self._lock:
            self.config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def list_prompts(self) -> List[PromptItem]:
        cfg = self._load()
        active_id = cfg.get("prompts", {}).get("active_prompt_id")
        items = [PromptItem(**p) for p in cfg.get("prompts", {}).get("items", [])]
        for p in items:
            p.is_active = (p.id == active_id)
        return items

    def create_prompt(self, data: Dict[str, Any]) -> PromptItem:
        cfg = self._load()
        pid = str(data.get("id") or uuid.uuid4().hex[:8])
        item = PromptItem(
            id=pid, name=data["name"], description=data.get("description", ""),
            content=data.get("content", ""), apply_mode=data.get("apply_mode", "turn0"),
        )
        cfg.setdefault("prompts", {}).setdefault("items", []).append(item.model_dump())
        self._save(cfg)
        return item

    def update_prompt(self, pid: str, data: Dict[str, Any]) -> Optional[PromptItem]:
        cfg = self._load()
        items = cfg.setdefault("prompts", {}).setdefault("items", [])
        for i, p in enumerate(items):
            if p["id"] == pid:
                p.update({k: v for k, v in data.items() if k not in ("id", "is_builtin")})
                p["updated_at"] = int(time.time())
                items[i] = p
                self._save(cfg)
                return PromptItem(**p)
        return None

    def delete_prompt(self, pid: str) -> bool:
        cfg = self._load()
        items = cfg.setdefault("prompts", {}).setdefault("items", [])
        cfg["prompts"]["items"] = [p for p in items if p["id"] != pid]
        if cfg["prompts"].get("active_prompt_id") == pid:
            cfg["prompts"]["active_prompt_id"] = None
        if len(cfg["prompts"]["items"]) < len(items):
            self._save(cfg)
            return True
        return False

    def activate_prompt(self, pid: Optional[str], enabled: bool = True) -> bool:
        cfg = self._load()
        cfg.setdefault("prompts", {})["active_prompt_id"] = pid if enabled else None
        self._save(cfg)
        return True

    def get_active_prompt(self) -> Optional[PromptItem]:
        for p in self.list_prompts():
            if p.is_active:
                return p
        return None

    def list_skills(self) -> List[SkillItem]:
        cfg = self._load()
        enabled_ids = set(cfg.get("skills", {}).get("enabled_skill_ids", []))
        all_skills = discover_all_skills(self.custom_skills_dir, self.project_root)
        for s in all_skills:
            s.is_active = (s.id in enabled_ids)
        return all_skills

    def set_enabled_skills(self, skill_ids: List[str]) -> List[str]:
        cfg = self._load()
        cfg.setdefault("skills", {})["enabled_skill_ids"] = list(skill_ids)
        self._save(cfg)
        return list(skill_ids)

    def create_custom_skill(self, data: Dict[str, Any]) -> SkillItem:
        slug = (data.get("id") or data.get("slug") or data["name"].lower().replace(" ", "-")).strip("/")
        slug = validate_safe_slug(slug)
        skill_dir = validate_safe_path(self.custom_skills_dir / slug, allowed_roots=[self.custom_skills_dir])
        skill_dir.mkdir(parents=True, exist_ok=True)
        triggers = data.get("triggers", [f"/{slug}"])
        content = data.get("content") or data.get("body") or ""
        fm = [f"name: {data['name']}", f"description: {data.get('description', '')}", f"compatibility: {data.get('compatibility', 'native')}"]
        fm_text = "\n".join(fm) + "\ntriggers:\n" + "\n".join(f"  - {t}" for t in triggers)
        (skill_dir / "SKILL.md").write_text(f"---\n{fm_text}\n---\n\n{content}\n", encoding="utf-8")
        return SkillItem(
            id=slug, name=data["name"], description=data.get("description", ""), content=content,
            compatibility=data.get("compatibility", "native"), triggers=triggers, source="custom", is_builtin=False
        )

    def update_custom_skill(self, slug: str, data: Dict[str, Any]) -> Optional[SkillItem]:
        slug = validate_safe_slug(slug)
        skill_dir = validate_safe_path(self.custom_skills_dir / slug, allowed_roots=[self.custom_skills_dir])
        if not (skill_dir / "SKILL.md").is_file():
            return None
        name = data.get("name", slug.replace("-", " ").title())
        desc = data.get("description", "")
        compat = data.get("compatibility", "native")
        content = data.get("content") or data.get("body") or ""
        triggers = data.get("triggers", [f"/{slug}"])
        fm = [f"name: {name}", f"description: {desc}", f"compatibility: {compat}"]
        fm_text = "\n".join(fm) + "\ntriggers:\n" + "\n".join(f"  - {t}" for t in triggers)
        (skill_dir / "SKILL.md").write_text(f"---\n{fm_text}\n---\n\n{content}\n", encoding="utf-8")
        return SkillItem(
            id=slug, name=name, description=desc, content=content,
            compatibility=compat, triggers=triggers, source="custom", is_builtin=False
        )

    def delete_custom_skill(self, slug: str) -> bool:
        slug = validate_safe_slug(slug)
        skill_dir = validate_safe_path(self.custom_skills_dir / slug, allowed_roots=[self.custom_skills_dir])
        if skill_dir.is_dir():
            for f in skill_dir.iterdir():
                f.unlink(missing_ok=True)
            skill_dir.rmdir()
            cfg = self._load()
            e = cfg.setdefault("skills", {}).setdefault("enabled_skill_ids", [])
            if slug in e:
                e.remove(slug)
                self._save(cfg)
            return True
        return False

    def list_connectors(self, mask: bool = True) -> List[ConnectorItem]:
        cfg = self._load()
        conns: List[ConnectorItem] = []
        for c in cfg.get("connectors", {}).get("items", []):
            dec_env = decrypt_map(c.get("_encrypted_env", ""), self._key)
            dec_headers = decrypt_map(c.get("_encrypted_headers", ""), self._key)
            env_keys, header_keys = sorted(list(dec_env.keys())), sorted(list(dec_headers.keys()))
            conns.append(ConnectorItem(
                id=c["id"], name=c["name"], description=c.get("description", ""), transport=c.get("transport", "stdio"),
                command=c.get("command"), args=c.get("args", []), url=c.get("url"),
                env={k: "••••••••" for k in env_keys} if mask else dec_env, env_keys=env_keys,
                headers={k: "••••••••" for k in header_keys} if mask else dec_headers, header_keys=header_keys,
                enabled=c.get("enabled", False), timeout_seconds=c.get("timeout_seconds", 15),
                is_builtin=c.get("is_builtin", False),
                approval_policy=c.get("approval_policy", "auto_allow_readonly"),
                allowed_tools=c.get("allowed_tools", ["*"]),
                created_at=c.get("created_at", 0), updated_at=c.get("updated_at", 0),
            ))
        return conns

    def get_connector(self, cid: str, mask: bool = True) -> Optional[ConnectorItem]:
        return next((c for c in self.list_connectors(mask=mask) if c.id == cid), None)

    def create_connector(self, data: Dict[str, Any]) -> ConnectorItem:
        cfg = self._load()
        cid = str(data.get("id") or f"conn_{uuid.uuid4().hex[:6]}")
        item_dict = {
            "id": cid, "name": data["name"], "description": data.get("description", ""),
            "transport": data.get("transport", "stdio"), "command": data.get("command"),
            "args": data.get("args", []), "url": data.get("url"),
            "_encrypted_env": encrypt_map(data.get("env", {}), self._key),
            "_encrypted_headers": encrypt_map(data.get("headers", {}), self._key),
            "enabled": bool(data.get("enabled", False)), "timeout_seconds": data.get("timeout_seconds", 15),
            "is_builtin": False, "approval_policy": data.get("approval_policy", "auto_allow_readonly"),
            "allowed_tools": data.get("allowed_tools", ["*"]),
            "created_at": int(time.time()), "updated_at": int(time.time()),
        }
        cfg.setdefault("connectors", {}).setdefault("items", []).append(item_dict)
        self._save(cfg)
        return self.get_connector(cid, mask=True)  # type: ignore[return-value]

    def update_connector(self, cid: str, data: Dict[str, Any]) -> Optional[ConnectorItem]:
        cfg = self._load()
        items = cfg.setdefault("connectors", {}).setdefault("items", [])
        for i, c in enumerate(items):
            if c["id"] == cid:
                for k in ("name", "description", "transport", "command", "args", "url", "enabled", "timeout_seconds", "allowed_tools", "approval_policy"):
                    if k in data:
                        c[k] = data[k]
                if "env" in data:
                    cur = decrypt_map(c.get("_encrypted_env", ""), self._key)
                    cur.update({k: v for k, v in data["env"].items() if v != "••••••••"})
                    c["_encrypted_env"] = encrypt_map(cur, self._key)
                if "headers" in data:
                    cur_h = decrypt_map(c.get("_encrypted_headers", ""), self._key)
                    cur_h.update({k: v for k, v in data["headers"].items() if v != "••••••••"})
                    c["_encrypted_headers"] = encrypt_map(cur_h, self._key)
                c["updated_at"] = int(time.time())
                items[i] = c
                self._save(cfg)
                return self.get_connector(cid, mask=True)
        return None

    def delete_connector(self, cid: str) -> bool:
        cfg = self._load()
        items = cfg.setdefault("connectors", {}).setdefault("items", [])
        cfg["connectors"]["items"] = [c for c in items if c["id"] != cid]
        if len(cfg["connectors"]["items"]) < len(items):
            self._save(cfg)
            return True
        return False

    def get_templates(self) -> List[Dict[str, Any]]:
        return self._load().get("connectors", {}).get("templates", [])
