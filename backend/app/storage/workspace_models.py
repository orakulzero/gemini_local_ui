"""
backend/app/storage/workspace_models.py
Data models and defaults for Workspace Customization (Prompts, Skills, Connectors).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class PromptItem(BaseModel):
    id: str
    name: str
    description: str = ""
    content: str
    apply_mode: Literal["turn0", "every_turn"] = "turn0"
    is_active: bool = False
    is_builtin: bool = False
    created_at: int = Field(default_factory=lambda: int(time.time()))
    updated_at: int = Field(default_factory=lambda: int(time.time()))


class SkillItem(BaseModel):
    id: str
    name: str
    description: str = ""
    content: str = ""
    compatibility: Literal["native", "ide-only", "mcp-bridged"] = "native"
    triggers: List[str] = Field(default_factory=list)
    source: Literal["gemini-pack", "custom", "claude-repo", "user-gemini"] = "gemini-pack"
    is_active: bool = False
    is_builtin: bool = False
    created_at: int = Field(default_factory=lambda: int(time.time()))
    updated_at: int = Field(default_factory=lambda: int(time.time()))


class ConnectorItem(BaseModel):
    id: str
    name: str
    description: str = ""
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio"
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    env_keys: List[str] = Field(default_factory=list)
    headers: Dict[str, str] = Field(default_factory=dict)
    header_keys: List[str] = Field(default_factory=list)
    enabled: bool = False
    timeout_seconds: int = 15
    is_builtin: bool = False
    approval_policy: Literal["auto_allow_readonly", "always_ask", "auto_allow_all", "deny"] = "auto_allow_readonly"
    allowed_tools: List[str] = Field(default_factory=lambda: ["*"])
    created_at: int = Field(default_factory=lambda: int(time.time()))
    updated_at: int = Field(default_factory=lambda: int(time.time()))


def get_default_workspace_config(python_bin: str) -> Dict[str, Any]:
    now = int(time.time())
    return {
        "version": 1,
        "prompts": {
            "enabled": True,
            "active_prompt_id": None,
            "items": [
                {
                    "id": "concise_engineer",
                    "name": "Concise Engineer",
                    "description": "Direct, production-grade technical answers without filler.",
                    "content": "You are an expert software engineer. Provide direct, production-grade, concise implementations. Explain reasoning only when non-obvious. Avoid conversational filler.",
                    "apply_mode": "turn0",
                    "is_active": False,
                    "is_builtin": True,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "systems_architect",
                    "name": "Systems Architect",
                    "description": "End-to-end distributed systems & architectural rigor.",
                    "content": "You are a principal systems architect. Focus on fault tolerance, clear component boundaries, scale, edge cases, and failure modes. Favor structured specs and concrete architecture patterns.",
                    "apply_mode": "turn0",
                    "is_active": False,
                    "is_builtin": True,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "direct_russian",
                    "name": "Direct Russian",
                    "description": "Четкие и лаконичные ответы на русском языке без воды.",
                    "content": "Отвечай на русском языке строго по существу, без вводных фраз и шаблонных вежливостей. Код оформляй готовыми блоками с типизацией.",
                    "apply_mode": "every_turn",
                    "is_active": False,
                    "is_builtin": True,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        },
        "skills": {
            "enabled_skill_ids": [],
        },
        "connectors": {
            "items": [
                {
                    "id": "builtin_local_utils",
                    "name": "Local Utilities",
                    "description": "Safe local utilities for datetime, math expressions, and webpage text retrieval.",
                    "transport": "stdio",
                    "command": python_bin,
                    "args": ["-u", "-m", "backend.app.mcp.builtin_tools_server"],
                    "url": None,
                    "env": {},
                    "_encrypted_env": "",
                    "headers": {},
                    "_encrypted_headers": "",
                    "enabled": False,
                    "timeout_seconds": 15,
                    "is_builtin": True,
                    "allowed_tools": ["*"],
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            "templates": [
                {
                    "id": "tpl_local_utils",
                    "name": "Local Utils",
                    "transport": "stdio",
                    "command": python_bin,
                    "args": ["-u", "-m", "backend.app.mcp.builtin_tools_server"],
                    "env": {},
                    "description": "In-process Python standard library utilities",
                },
                {
                    "id": "tpl_stdio",
                    "name": "Stdio MCP",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-everything"],
                    "env": {},
                    "description": "Standard I/O CLI sub-process MCP server",
                },
                {
                    "id": "tpl_sse",
                    "name": "HTTP/SSE MCP",
                    "transport": "sse",
                    "url": "http://127.0.0.1:8080/sse",
                    "headers": {},
                    "description": "Remote or containerized Server-Sent Events MCP endpoint",
                },
            ],
        },
    }
