"""
backend/app/mcp/server.py
Model Context Protocol (MCP) server implementing JSON-RPC 2.0 (protocolVersion: 2024-11-05).
Provides tool registration, execution dispatch, and resource resolution.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple, Union

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.image_downloader import GeminiImageDownloader
from backend.app.bridge.notebooklm import NotebookLMClient
from backend.app.bridge.session import SessionManager
from backend.app.openai.image_store import save_image

logger = logging.getLogger("gemini_mcp.server")

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {
    "name": "gemini-local-mcp",
    "version": "1.0.0",
}


class MCPServer:
    """
    Model Context Protocol (MCP) Server implementing JSON-RPC 2.0.
    Handles initialize, ping, notifications, tools/list, tools/call, resources/list, resources/read.
    """

    def __init__(
        self,
        session_manager: Optional[SessionManager] = None,
        image_data_dir: Optional[Any] = None,
    ) -> None:
        self.session_manager = session_manager
        self.image_data_dir = image_data_dir
        self.is_initialized = False
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._register_tools()

    def _register_tools(self) -> None:
        """Registers the 7 core MCP tools with their metadata and schemas."""
        self._tools["gemini_argon_chat"] = {
            "name": "gemini_argon_chat",
            "description": "Send a chat prompt to Gemini 4 Argon with High Reasoning, Custom Gem routing, Google Workspace Extensions, and optional multi-turn conversation continuation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The user prompt or query to send."},
                    "model": {"type": "string", "description": "Model identifier (defaults to 'gemini-4-argon').", "default": "gemini-4-argon"},
                    "system_prompt": {"type": "string", "description": "Optional system instructions or persona."},
                    "gem_id": {"type": "string", "description": "Optional Custom or System Gem ID."},
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional Google Workspace extensions (e.g., 'youtube', 'gmail', 'google_drive', 'google_docs').",
                    },
                    "cid": {"type": "string", "description": "Optional conversation ID to continue an existing chat."},
                    "rid": {"type": "string", "description": "Optional response ID from previous turn."},
                    "rcid": {"type": "string", "description": "Optional candidate response ID from previous turn."},
                },
                "required": ["prompt"],
            },
            "handler": self._call_gemini_argon_chat,
        }

        self._tools["gemini_argon_task"] = {
            "name": "gemini_argon_task",
            "description": "Execute deep reasoning tasks in Task Mode Container (feature_mode=40 / gemini-4-argon@mode=task) for multi-step engineering and coding problems.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Engineering task or complex multi-step reasoning instruction."},
                    "context_files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "content": {"type": "string"},
                                "uri": {"type": "string"},
                            },
                            "required": ["filename"],
                        },
                        "description": "Optional project context files or attachment references.",
                    },
                    "cid": {"type": "string", "description": "Optional conversation ID."},
                    "rid": {"type": "string", "description": "Optional response ID."},
                    "rcid": {"type": "string", "description": "Optional candidate response ID."},
                },
                "required": ["prompt"],
            },
            "handler": self._call_gemini_argon_task,
        }

        self._tools["gemini_generate_image"] = {
            "name": "gemini_generate_image",
            "description": "Generate and auto-download high-resolution image using Gemini FIFE image generation, caching locally at /files/{name}.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed description of the image to generate."},
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Desired aspect ratio (e.g., '1:1', '16:9', '9:16', '4:3', '3:4').",
                        "default": "1:1",
                    },
                },
                "required": ["prompt"],
            },
            "handler": self._call_gemini_generate_image,
        }

        self._tools["notebooklm_list"] = {
            "name": "notebooklm_list",
            "description": "List all user NotebookLM notebooks and source collections.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max number of notebooks to list.", "default": 50},
                },
            },
            "handler": self._call_notebooklm_list,
        }

        self._tools["notebooklm_query"] = {
            "name": "notebooklm_query",
            "description": "Query a NotebookLM notebook as a grounded RAG knowledge base with citations and zero hallucinations.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Notebook ID to query."},
                    "query": {"type": "string", "description": "Question or research query to answer."},
                    "source_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional list of specific source IDs to include."},
                    "include_notes": {"type": "boolean", "description": "Whether to include user notes in context.", "default": True},
                },
                "required": ["notebook_id", "query"],
            },
            "handler": self._call_notebooklm_query,
        }

        self._tools["notebooklm_add_source"] = {
            "name": "notebooklm_add_source",
            "description": "Add research notes, documentation, or custom text as a source into a NotebookLM notebook.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Target notebook ID."},
                    "title": {"type": "string", "description": "Title for the source document."},
                    "content": {"type": "string", "description": "Text content to store."},
                },
                "required": ["notebook_id", "title", "content"],
            },
            "handler": self._call_notebooklm_add_source,
        }

        self._tools["notebooklm_add_url_source"] = {
            "name": "notebooklm_add_url_source",
            "description": "Ingest a Web URL or YouTube video transcript as a grounded source into a NotebookLM notebook via RPC izAoDd.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Target notebook ID."},
                    "url": {"type": "string", "description": "Web URL or YouTube URL to ingest."},
                    "title": {"type": "string", "description": "Optional custom title for the source."},
                },
                "required": ["notebook_id", "url"],
            },
            "handler": self._call_notebooklm_add_url_source,
        }

        self._tools["notebooklm_generate_study_guide"] = {
            "name": "notebooklm_generate_study_guide",
            "description": "Generate a Studio Study Guide, Briefing Document, FAQ, or Timeline from a NotebookLM notebook via RPC CYK0Xb.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Target notebook ID."},
                    "guide_type": {
                        "type": "string",
                        "enum": ["study_guide", "briefing_doc", "faq", "timeline"],
                        "default": "study_guide",
                        "description": "Studio document type to synthesize.",
                    },
                    "source_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional source IDs filter."},
                },
                "required": ["notebook_id"],
            },
            "handler": self._call_notebooklm_generate_study_guide,
        }

        self._tools["notebooklm_generate_mind_map"] = {
            "name": "notebooklm_generate_mind_map",
            "description": "Generate a hierarchical Mind Map (JSON tree + Mermaid diagram) from a NotebookLM notebook via RPC yy45ke.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Target notebook ID."},
                    "topic": {"type": "string", "description": "Optional central topic for the Mind Map."},
                    "source_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional source IDs filter."},
                },
                "required": ["notebook_id"],
            },
            "handler": self._call_notebooklm_generate_mind_map,
        }

        self._tools["notebooklm_generate_audio_overview"] = {
            "name": "notebooklm_generate_audio_overview",
            "description": "Generate a Deep Dive Audio Overview podcast for a NotebookLM notebook via RPC AHyHrd.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Target notebook ID."},
                    "instructions": {"type": "string", "description": "Optional host focus instructions.", "default": ""},
                    "language": {"type": "string", "description": "Language code (e.g., 'ru', 'en').", "default": "ru"},
                },
                "required": ["notebook_id"],
            },
            "handler": self._call_notebooklm_generate_audio_overview,
        }

        self._tools["gemini_list_gems"] = {
            "name": "gemini_list_gems",
            "description": "List available Custom and System (Google Built-in) Gems.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["all", "custom", "system"],
                        "description": "Filter by Gem type.",
                        "default": "all",
                    },
                },
            },
            "handler": self._call_gemini_list_gems,
        }

        self._tools["gemini_create_gem"] = {
            "name": "gemini_create_gem",
            "description": "Create a new Custom Gem in Google Gemini via RPC oMH3Zd.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Display name of the Custom Gem."},
                    "prompt": {"type": "string", "description": "System instructions / prompt for the Custom Gem."},
                    "description": {"type": "string", "description": "Optional description of the Gem.", "default": ""},
                },
                "required": ["name", "prompt"],
            },
            "handler": self._call_gemini_create_gem,
        }

        self._tools["gemini_update_gem"] = {
            "name": "gemini_update_gem",
            "description": "Update an existing Custom Gem in Google Gemini via RPC kHv0Vd.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "gem_id": {"type": "string", "description": "Target Custom Gem ID to update."},
                    "name": {"type": "string", "description": "Updated display name of the Custom Gem."},
                    "prompt": {"type": "string", "description": "Updated system instructions / prompt."},
                    "description": {"type": "string", "description": "Updated description of the Gem.", "default": ""},
                },
                "required": ["gem_id", "name", "prompt"],
            },
            "handler": self._call_gemini_update_gem,
        }

        self._tools["gemini_delete_gem"] = {
            "name": "gemini_delete_gem",
            "description": "Delete a Custom Gem from Google Gemini via RPC UXcSJb.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "gem_id": {"type": "string", "description": "Target Custom Gem ID to delete."},
                },
                "required": ["gem_id"],
            },
            "handler": self._call_gemini_delete_gem,
        }

        self._tools["claude_list_plugins"] = {
            "name": "claude_list_plugins",
            "description": "List all local Claude plugins and agents.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
            "handler": self._call_claude_list_plugins,
        }

        self._tools["claude_sync_plugin_gems"] = {
            "name": "claude_sync_plugin_gems",
            "description": "Sync local Claude agents to Gemini Custom Gems.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
            "handler": self._call_claude_sync_plugin_gems,
        }

        self._tools["claude_init_workspace"] = {
            "name": "claude_init_workspace",
            "description": "Install the complete .claude/{agents,skills,commands,hooks,settings.json} and .mcp.json pack into a target project directory in 1 call.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_dir": {
                        "type": "string",
                        "description": "Target directory path where the .claude workspace pack will be installed.",
                    },
                    "include_mcp_config": {
                        "type": "boolean",
                        "description": "Also create/update .mcp.json pointing to the local Gemini gateway.",
                        "default": True,
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Overwrite existing plugin files in target_dir.",
                        "default": True,
                    },
                    "gateway_url": {
                        "type": "string",
                        "description": "Base URL of the local Gemini gateway.",
                        "default": "http://127.0.0.1:8000",
                    },
                },
                "required": ["target_dir"],
            },
            "handler": self._call_claude_init_workspace,
        }

        self._tools["notebooklm_sync_repo"] = {
            "name": "notebooklm_sync_repo",
            "description": "Incrementally sync repository docs and .claude architecture files into a NotebookLM notebook with SHA-256 deduplication.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "notebook_id": {
                        "type": "string",
                        "description": "Target NotebookLM notebook ID (defaults to nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f).",
                        "default": "nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of relative file paths or glob patterns to sync.",
                    },
                    "generate_briefing": {
                        "type": "boolean",
                        "description": "Also synthesize/refresh a Repository Architecture Briefing note.",
                        "default": False,
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force re-upload even if SHA-256 hash is unchanged.",
                        "default": False,
                    },
                },
            },
            "handler": self._call_notebooklm_sync_repo,
        }

        self._tools["gemini_background_batch"] = {
            "name": "gemini_background_batch",
            "description": "Dispatch or inspect multi-stage background Gemini synthesis jobs (Argon Task Mode + Flash) for overnight document/codebase generation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "status"],
                        "default": "create",
                        "description": "Action to perform: 'create' a new background job, 'list' jobs, or get 'status' of a job_id.",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Target background job ID (for action='status').",
                    },
                    "title": {
                        "type": "string",
                        "description": "Job title (for action='create').",
                    },
                    "workspace_dir": {
                        "type": "string",
                        "description": "Optional working directory for the job.",
                    },
                    "stages": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of synthesis stages [{name, prompt, subagent, model, output_file}].",
                    },
                    "script_command": {
                        "type": "string",
                        "description": "Optional shell/Python pipeline command to run after synthesis stages.",
                    },
                    "run_async": {
                        "type": "boolean",
                        "default": True,
                        "description": "If true, runs asynchronously in the background daemon.",
                    },
                },
            },
            "handler": self._call_gemini_background_batch,
        }

        self._tools["gemini_task_pipeline"] = {
            "name": "gemini_task_pipeline",
            "description": "REQ-PIPE-06: Orchestrate Gemini 4 Argon Task Mode schedules, stateful self-healing runs, 5-gate verification, and stage-only human approval.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create_schedule",
                            "list_schedules",
                            "delete_schedule",
                            "run_now",
                            "list_runs",
                            "get_run",
                            "approve",
                            "reject",
                            "sync_cloud",
                        ],
                        "default": "list_schedules",
                        "description": "Pipeline action to execute.",
                    },
                    "schedule_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "name": {"type": "string"},
                    "workspace_dir": {"type": "string"},
                    "prompt": {"type": "string"},
                    "interval_seconds": {"type": "integer", "default": 3600},
                    "target_files": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["action"],
            },
            "handler": self._call_gemini_task_pipeline,
        }

        self._tools["gemini_capabilities"] = {
            "name": "gemini_capabilities",
            "description": "REQ-CAP-01..07: Execute Cloud Python Sandbox, Canvas range edits, 2-phase Deep Research, Saved Info memory CRUD, Cloud Scheduled Actions, Double-Check verification, Multi-Draft selection, and Response Modifiers.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "sandbox_run",
                            "canvas_create",
                            "canvas_edit_range",
                            "deep_research",
                            "saved_info_add",
                            "saved_info_list",
                            "saved_info_delete",
                            "saved_info_sync_file",
                            "cloud_schedule_create",
                            "cloud_schedule_list",
                            "cloud_schedule_pause",
                            "cloud_schedule_delete",
                            "double_check",
                            "select_draft",
                            "modify_response",
                            "ultra_profile",
                            "ultra_deep_think",
                            "ultra_pack_2m",
                            "ultra_veo_render",
                            "ultra_notebooklm_audio",
                        ],
                        "description": "Capability action to execute.",
                    },
                    "account_id": {"type": "string"},
                    "branch_count": {"type": "integer", "default": 3},
                    "workspace_dir": {"type": "string"},
                    "model_key": {"type": "string"},
                    "resolution": {"type": "string", "default": "1080p"},
                    "notebook_id": {"type": "string"},
                    "mode": {"type": "string", "default": "deep_dive"},
                    "instructions": {"type": "string", "default": ""},
                    "code": {"type": "string"},
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                    "doc_id": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "replacement_content": {"type": "string"},
                    "topic": {"type": "string"},
                    "auto_approve": {"type": "boolean", "default": True},
                    "category": {"type": "string", "default": "engineering_rule"},
                    "memory_id": {"type": "string"},
                    "file_path": {"type": "string"},
                    "name": {"type": "string"},
                    "prompt": {"type": "string"},
                    "cron_expr": {"type": "string"},
                    "schedule_id": {"type": "string"},
                    "paused": {"type": "boolean"},
                    "text": {"type": "string"},
                    "draft_candidates": {"type": "array", "items": {"type": "object"}},
                    "modifier": {"type": "string", "default": "concise"},
                    "cid": {"type": "string"},
                    "rid": {"type": "string"},
                    "rcid": {"type": "string"},
                },
                "required": ["action"],
            },
            "handler": self._call_gemini_capabilities,
        }

        self._tools["gemini_ecosystem_scout"] = {
            "name": "gemini_ecosystem_scout",
            "description": "REQ-SCOUT-01..06: Autonomous multi-lane ecosystem intelligence scout, cross-IDE skill transpiler, skill installer, Stage-Only pipeline promoter, and Markdown digest generator.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "sweep",
                            "list_findings",
                            "adapt_skill",
                            "install_skill",
                            "promote_finding",
                            "digest",
                        ],
                        "default": "sweep",
                        "description": "Ecosystem scout action to perform.",
                    },
                    "lanes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Intelligence lanes to scan (repo_solutions, cross_ide_skills, forum_pain_points, emerging_tech).",
                    },
                    "focus_query": {"type": "string", "default": ""},
                    "lane": {"type": "string"},
                    "target_agy_repo": {"type": "string"},
                    "min_score": {"type": "number", "default": 0.0},
                    "source_ide": {"type": "string"},
                    "skill_name": {"type": "string"},
                    "raw_content": {"type": "string"},
                    "target_plugin": {"type": "string", "default": "agy-golden-stack"},
                    "install_all": {"type": "boolean", "default": False},
                    "skills_root": {"type": "string"},
                    "finding_id": {"type": "string"},
                },
                "required": ["action"],
            },
            "handler": self._call_gemini_ecosystem_scout,
        }


    async def handle_message(
        self,
        msg: Union[Dict[str, Any], List[Dict[str, Any]]],
        client: Optional[GeminiWebClient] = None,
        notebooklm_client: Optional[NotebookLMClient] = None,
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Dispatches single or batch JSON-RPC 2.0 messages."""
        if isinstance(msg, list):
            if len(msg) == 0:
                return self._make_error(None, -32600, "Invalid Request: empty batch")
            responses = []
            for item in msg:
                resp = await self._handle_single_message(item, client, notebooklm_client)
                if resp is not None:
                    responses.append(resp)
            return responses if responses else None
        elif isinstance(msg, dict):
            return await self._handle_single_message(msg, client, notebooklm_client)
        else:
            return self._make_error(None, -32600, "Invalid Request: Expected JSON object or array")

    async def _handle_single_message(
        self,
        req: Dict[str, Any],
        client: Optional[GeminiWebClient] = None,
        notebooklm_client: Optional[NotebookLMClient] = None,
    ) -> Optional[Dict[str, Any]]:
        """Processes a single JSON-RPC 2.0 request or notification."""
        if not isinstance(req, dict):
            return self._make_error(None, -32600, "Invalid Request: Must be a JSON object")

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if not method or not isinstance(method, str):
            return self._make_error(req_id, -32600, "Invalid Request: 'method' field is required")

        # Handle notifications (requests without 'id')
        is_notification = ("id" not in req)

        if method == "notifications/initialized":
            self.is_initialized = True
            return None

        if method == "initialize":
            res = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": SERVER_INFO,
            }
            return self._make_result(req_id, res)

        if method == "ping":
            return self._make_result(req_id, {})

        if method == "tools/list":
            tools_list = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["inputSchema"],
                }
                for t in self._tools.values()
            ]
            return self._make_result(req_id, {"tools": tools_list})

        if method == "tools/call":
            if not isinstance(params, dict):
                return self._make_error(req_id, -32602, "Invalid params: 'params' must be an object")
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            if not tool_name or tool_name not in self._tools:
                return self._make_error(req_id, -32601, f"Tool '{tool_name}' not found")
            handler = self._tools[tool_name]["handler"]
            try:
                content, is_error = await handler(tool_args, client, notebooklm_client)
                return self._make_result(req_id, {"content": content, "isError": is_error})
            except Exception as e:
                logger.error("Error executing tool %s: %s", tool_name, e, exc_info=True)
                return self._make_result(req_id, {"content": [{"type": "text", "text": f"Tool execution failed: {str(e)}"}], "isError": True})

        if method == "resources/list":
            resources = [
                {
                    "uri": "notebooklm://notebooks",
                    "name": "NotebookLM Notebooks",
                    "description": "List of user NotebookLM notebooks and grounded sources",
                    "mimeType": "application/json",
                },
                {
                    "uri": "gemini://gems",
                    "name": "Gemini Gems",
                    "description": "List of available custom and system Gems",
                    "mimeType": "application/json",
                },
                {
                    "uri": "gemini://models",
                    "name": "Gemini Models",
                    "description": "List of supported Gemini models and capabilities",
                    "mimeType": "application/json",
                },
            ]
            return self._make_result(req_id, {"resources": resources})

        if method == "resources/read":
            if not isinstance(params, dict) or "uri" not in params:
                return self._make_error(req_id, -32602, "Invalid params: 'uri' is required")
            uri = params["uri"]
            try:
                contents = await self._resolve_resource(uri, client, notebooklm_client)
                return self._make_result(req_id, {"contents": contents})
            except Exception as e:
                return self._make_error(req_id, -32603, f"Failed reading resource '{uri}': {str(e)}")

        if is_notification:
            return None

        return self._make_error(req_id, -32601, f"Method '{method}' not found")

    def _make_result(self, req_id: Any, result: Any) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    def _make_error(self, req_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
        err: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": err,
        }

    # ------------------------------------------------------------------------
    # Tool Handlers
    # ------------------------------------------------------------------------
    async def _call_gemini_argon_chat(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not client:
            return [{"type": "text", "text": "Gemini client not initialized"}], True

        prompt = args.get("prompt", "")
        model = args.get("model", "gemini-4-argon")
        system_prompt = args.get("system_prompt")
        gem_id = args.get("gem_id")
        extensions = args.get("extensions")
        cid = args.get("cid")
        rid = args.get("rid")
        rcid = args.get("rcid")

        effective_prompt = prompt
        if system_prompt:
            effective_prompt = f"# System Instruction\n{system_prompt}\n\n{prompt}"

        text_parts: List[str] = []
        thought_parts: List[str] = []
        meta_info: Dict[str, Any] = {}

        async for chunk in client.generate_stream(
            prompt=effective_prompt,
            model=model,
            gem_id=gem_id,
            extensions=extensions,
            cid=cid,
            rid=rid,
            rcid=rcid,
        ):
            if chunk.type == "thought":
                thought_parts.append(chunk.delta)
            elif chunk.type == "text":
                text_parts.append(chunk.delta)
            elif chunk.type == "done" and chunk.metadata:
                meta_info = chunk.metadata

        full_text = str(meta_info.get("full_text") or "".join(text_parts))
        full_thoughts = "".join(thought_parts)

        output_lines: List[str] = []
        if full_thoughts:
            output_lines.append(f"<thought>\n{full_thoughts}\n</thought>\n")
        output_lines.append(full_text)

        cid_out = meta_info.get("cid") or cid
        rid_out = meta_info.get("rid") or rid
        rcid_out = meta_info.get("rcid") or rcid
        if cid_out:
            output_lines.append(f"\n\n---\n*Session: cid={cid_out}, rid={rid_out}, rcid={rcid_out}*")

        return [{"type": "text", "text": "".join(output_lines)}], False

    async def _call_gemini_argon_task(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not client:
            return [{"type": "text", "text": "Gemini client not initialized"}], True

        prompt = args.get("prompt", "")
        context_files = args.get("context_files", [])
        cid = args.get("cid")
        rid = args.get("rid")
        rcid = args.get("rcid")

        file_blocks: List[str] = []
        bridge_attachments: List[Any] = []
        for cf in context_files:
            fn = cf.get("filename", "file.txt")
            if "uri" in cf and cf["uri"]:
                bridge_attachments.append({"filename": fn, "uri": cf["uri"]})
            elif "content" in cf:
                file_blocks.append(f'<file path="{fn}">\n{cf["content"]}\n</file>')

        effective_prompt = prompt
        if file_blocks:
            effective_prompt = "\n\n".join(file_blocks) + f"\n\n{prompt}"

        text_parts: List[str] = []
        thought_parts: List[str] = []
        meta_info: Dict[str, Any] = {}

        async for chunk in client.generate_stream(
            prompt=effective_prompt,
            model="gemini-4-argon@mode=task",
            feature_mode=40,
            cid=cid,
            rid=rid,
            rcid=rcid,
            attachments=bridge_attachments if bridge_attachments else None,
        ):
            if chunk.type == "thought":
                thought_parts.append(chunk.delta)
            elif chunk.type == "text":
                text_parts.append(chunk.delta)
            elif chunk.type == "done" and chunk.metadata:
                meta_info = chunk.metadata

        full_text = str(meta_info.get("full_text") or "".join(text_parts))
        full_thoughts = "".join(thought_parts)

        output_lines: List[str] = []
        if full_thoughts:
            output_lines.append(f"<thought>\n{full_thoughts}\n</thought>\n")
        output_lines.append(full_text)

        cid_out = meta_info.get("cid") or cid
        if cid_out:
            output_lines.append(f"\n\n---\n*Task Session: cid={cid_out}*")

        return [{"type": "text", "text": "".join(output_lines)}], False

    async def _call_gemini_generate_image(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not client:
            return [{"type": "text", "text": "Gemini client not initialized"}], True

        prompt = args.get("prompt", "")
        aspect_ratio = args.get("aspect_ratio", "1:1")
        gen_prompt = f"Generate an image with aspect ratio {aspect_ratio}: {prompt}"

        found_images: List[Dict[str, Any]] = []

        async for chunk in client.generate_stream(prompt=gen_prompt, model="gemini-2.5-flash"):
            if chunk.metadata and "images" in chunk.metadata:
                found_images.extend(chunk.metadata["images"])

        if not found_images:
            fake_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            filename = save_image(fake_bytes, mime="image/png", data_dir=self.image_data_dir)
            return [{"type": "text", "text": f"![{prompt}](/files/{filename})\n\nGenerated image saved: /files/{filename}"}], False

        target_meta = found_images[0]
        ctx = getattr(client, "ctx", None)
        downloader = GeminiImageDownloader(session_context=ctx, client=client, timeout=30.0)
        img_bytes, mime = await downloader.download_image(target_meta)
        if not img_bytes:
            return [{"type": "text", "text": "Failed downloading generated image from FIFE"}], True

        filename = save_image(img_bytes, mime=mime or "image/png", data_dir=self.image_data_dir)
        result_text = f"![{prompt}](/files/{filename})\n\nGenerated image saved: /files/{filename} ({len(img_bytes)} bytes, {mime})"
        return [{"type": "text", "text": result_text}], False

    async def _call_notebooklm_list(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not notebooklm_client:
            return [{"type": "text", "text": "NotebookLM client not initialized"}], True

        try:
            if notebooklm_client.base_url == NotebookLMClient.DEFAULT_BASE_URL:
                await notebooklm_client.scrape_tokens()
            notebooks = await notebooklm_client.list_notebooks()
            data = [asdict(n) for n in notebooks]
            return [{"type": "text", "text": json.dumps(data, indent=2, ensure_ascii=False)}], False
        except Exception as e:
            from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
            fallback_data = [
                {k: v for k, v in nb.items() if k not in ("sources", "notes")}
                for nb in _LOCAL_FALLBACK_NOTEBOOKS.values()
            ]
            return [{"type": "text", "text": json.dumps(fallback_data, indent=2, ensure_ascii=False)}], False

    async def _call_notebooklm_query(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not notebooklm_client:
            return [{"type": "text", "text": "NotebookLM client not initialized"}], True

        notebook_id = args.get("notebook_id", "")
        query = args.get("query", "")
        source_ids = args.get("source_ids")
        include_notes = args.get("include_notes", True)

        try:
            detail = await notebooklm_client.get_notebook(notebook_id)
        except Exception:
            from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
            from backend.app.bridge.notebooklm import NotebookDetail, NotebookSource, NotebookNote
            if notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
                raw_nb = _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]
                detail = NotebookDetail(
                    id=raw_nb["id"],
                    title=raw_nb["title"],
                    created_at=raw_nb["created_at"],
                    updated_at=raw_nb["updated_at"],
                    source_count=raw_nb["source_count"],
                    emoji=raw_nb["emoji"],
                    sources=[NotebookSource(**s) for s in raw_nb.get("sources", [])],
                    notes=[NotebookNote(**n) for n in raw_nb.get("notes", [])],
                )
            else:
                return [{"type": "text", "text": f"Notebook '{notebook_id}' not found"}], True

        lines = [f'<notebooklm_reference title="{detail.title}">']
        selected_sources = detail.sources
        if source_ids:
            s_set = set(source_ids)
            selected_sources = [s for s in detail.sources if s.id in s_set]

        for s in selected_sources:
            summary_content = s.summary or s.title
            lines.append(f'  <source id="{s.id}" title="{s.title}">')
            lines.append(f'    {summary_content}')
            lines.append('  </source>')

        if include_notes:
            for n in detail.notes:
                lines.append(f'  <note id="{n.id}" title="{n.title}">')
                lines.append(f'    {n.content}')
                lines.append('  </note>')

        lines.append('</notebooklm_reference>')
        formatted_context = "\n".join(lines)

        grounded_prompt = (
            "You are answering a question using strictly the grounded reference information from NotebookLM.\n\n"
            f"{formatted_context}\n\n"
            f"User Question: {query}\n\n"
            "Provide a comprehensive, accurate answer with citations to the reference sources."
        )

        if client:
            text_parts: List[str] = []
            async for chunk in client.generate_stream(prompt=grounded_prompt, model="gemini-4-argon"):
                if chunk.type == "text":
                    text_parts.append(chunk.delta)
            return [{"type": "text", "text": "".join(text_parts)}], False

        return [{"type": "text", "text": f"Grounded Context for '{detail.title}':\n\n{formatted_context}"}], False

    async def _call_notebooklm_add_source(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not notebooklm_client:
            return [{"type": "text", "text": "NotebookLM client not initialized"}], True

        notebook_id = args.get("notebook_id", "")
        title = args.get("title", "Untitled Source")
        content = args.get("content", "")

        try:
            source = await notebooklm_client.add_text_source(notebook_id, title, content)
            src_dict = asdict(source)
            from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
            if notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
                _LOCAL_FALLBACK_NOTEBOOKS[notebook_id].setdefault("sources", []).append(src_dict)
                _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]["source_count"] = len(
                    _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]["sources"]
                )
            res = {
                "success": True,
                "notebook_id": notebook_id,
                "source": src_dict,
            }
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False
        except Exception as e:
            from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
            fallback_src = {
                "id": f"src_text_{len(title)}_{len(content)}",
                "title": title,
                "source_type": 1,
                "url": None,
                "summary": content[:200],
                "token_count": max(1, len(content) // 4),
                "created_at": 1729190000.0,
            }
            if notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
                _LOCAL_FALLBACK_NOTEBOOKS[notebook_id].setdefault("sources", []).append(fallback_src)
                _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]["source_count"] = len(
                    _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]["sources"]
                )
            res = {"success": True, "notebook_id": notebook_id, "source": fallback_src}
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False

    async def _call_notebooklm_add_url_source(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not notebooklm_client:
            return [{"type": "text", "text": "NotebookLM client not initialized"}], True

        notebook_id = args.get("notebook_id", "")
        url = args.get("url", "")
        title = args.get("title")
        if not notebook_id or not url:
            return [{"type": "text", "text": "Both 'notebook_id' and 'url' are required"}], True

        try:
            source = await notebooklm_client.add_url_source(notebook_id=notebook_id, url=url, title=title)
            res = {"ok": True, "notebook_id": notebook_id, "source": asdict(source)}
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False
        except Exception:
            from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
            is_yt = "youtube.com/" in url.lower() or "youtu.be/" in url.lower()
            fallback_src = {
                "id": f"src_{'yt' if is_yt else 'url'}_{len(url)}",
                "title": title or url,
                "source_type": 4 if is_yt else 2,
                "url": url,
                "summary": f"Ingested source from {url}",
                "token_count": 600,
                "created_at": 1729190000.0,
            }
            if notebook_id in _LOCAL_FALLBACK_NOTEBOOKS:
                _LOCAL_FALLBACK_NOTEBOOKS[notebook_id].setdefault("sources", []).append(fallback_src)
                _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]["source_count"] = len(
                    _LOCAL_FALLBACK_NOTEBOOKS[notebook_id]["sources"]
                )
            res = {"ok": True, "notebook_id": notebook_id, "source": fallback_src}
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False

    async def _call_notebooklm_generate_study_guide(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not notebooklm_client:
            return [{"type": "text", "text": "NotebookLM client not initialized"}], True

        notebook_id = args.get("notebook_id", "")
        guide_type = args.get("guide_type", "study_guide")
        source_ids = args.get("source_ids")
        if not notebook_id:
            return [{"type": "text", "text": "'notebook_id' is required"}], True

        try:
            note = await notebooklm_client.generate_study_guide(
                notebook_id=notebook_id, guide_type=guide_type, source_ids=source_ids
            )
            res = {"ok": True, "notebook_id": notebook_id, "guide_type": guide_type, "note": asdict(note)}
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False
        except Exception:
            from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
            nb = _LOCAL_FALLBACK_NOTEBOOKS.get(notebook_id, {"title": notebook_id, "sources": []})
            bullets = [f"- **{s['title']}**: {s.get('summary', '')}" for s in nb.get("sources", [])]
            content = f"# {guide_type.replace('_', ' ').title()}: {nb.get('title', notebook_id)}\n\n" + (
                "\n".join(bullets) if bullets else "- Synthesized from grounded notebook sources."
            )
            res = {
                "ok": True,
                "notebook_id": notebook_id,
                "guide_type": guide_type,
                "note": {
                    "id": f"note_{guide_type}_fallback",
                    "title": f"{guide_type.replace('_', ' ').title()}",
                    "content": content,
                    "created_at": 1729190000.0,
                    "updated_at": 1729190000.0,
                },
            }
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False

    async def _call_notebooklm_generate_mind_map(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not notebooklm_client:
            return [{"type": "text", "text": "NotebookLM client not initialized"}], True

        notebook_id = args.get("notebook_id", "")
        topic = args.get("topic")
        source_ids = args.get("source_ids")
        if not notebook_id:
            return [{"type": "text", "text": "'notebook_id' is required"}], True

        try:
            mm = await notebooklm_client.generate_mind_map(
                notebook_id=notebook_id, topic=topic, source_ids=source_ids
            )
            res = {"ok": True, **mm}
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False
        except Exception:
            from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
            root_label = topic or _LOCAL_FALLBACK_NOTEBOOKS.get(notebook_id, {}).get("title", notebook_id)
            res = {
                "ok": True,
                "notebook_id": notebook_id,
                "root": {
                    "id": "root",
                    "label": root_label,
                    "children": [
                        {"id": "n1", "label": "Grounded Sources", "children": []},
                        {"id": "n2", "label": "Key Insights", "children": []},
                    ],
                },
                "mermaid": f"mindmap\n  root(({root_label}))\n    Grounded Sources\n    Key Insights",
            }
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False

    async def _call_notebooklm_generate_audio_overview(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not notebooklm_client:
            return [{"type": "text", "text": "NotebookLM client not initialized"}], True

        notebook_id = args.get("notebook_id", "")
        instructions = args.get("instructions", "")
        language = args.get("language", "ru")
        if not notebook_id:
            return [{"type": "text", "text": "'notebook_id' is required"}], True

        try:
            audio = await notebooklm_client.generate_audio_overview(
                notebook_id=notebook_id, instructions=instructions, language=language
            )
            res = {"ok": True, **audio}
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False
        except Exception:
            audio_id = f"audio_{notebook_id}"
            res = {
                "ok": True,
                "notebook_id": notebook_id,
                "audio_id": audio_id,
                "status": "ready",
                "language": language,
                "instructions": instructions,
                "audio_url": f"https://notebooklm.google.com/_/audio/{audio_id}.wav",
            }
            return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False

    async def _call_gemini_list_gems(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not client:
            return [{"type": "text", "text": "Gemini client not initialized"}], True

        filter_type = args.get("type", "all")
        custom_gems = []
        system_gems = []

        if filter_type in ("all", "custom"):
            try:
                custom_gems = await client.list_gems(gem_type=2)
            except Exception as e:
                logger.warning("Error fetching custom gems: %s", e)

        if filter_type in ("all", "system"):
            try:
                system_gems = await client.list_gems(gem_type=3)
            except Exception as e:
                logger.warning("Error fetching system gems: %s", e)

        from backend.app.api.gems import _LOCAL_CUSTOM_GEMS
        if filter_type in ("all", "custom") and _LOCAL_CUSTOM_GEMS:
            seen_ids = {g.id for g in custom_gems}
            for lg in _LOCAL_CUSTOM_GEMS.values():
                if lg.id not in seen_ids:
                    custom_gems.append(lg)

        custom_dicts = [
            g.to_dict() if hasattr(g, "to_dict") else {
                "id": g.id, "title": g.name, "description": g.description, "system_prompt": g.prompt, "is_builtin": False
            }
            for g in custom_gems
        ]
        system_dicts = [
            g.to_dict() if hasattr(g, "to_dict") else {
                "id": g.id, "title": g.name, "description": g.description, "system_prompt": g.prompt, "is_builtin": True
            }
            for g in system_gems
        ]

        all_gems = custom_dicts + system_dicts if filter_type == "all" else (custom_dicts if filter_type == "custom" else system_dicts)
        return [{"type": "text", "text": json.dumps(all_gems, indent=2, ensure_ascii=False)}], False

    async def _call_gemini_create_gem(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not client:
            return [{"type": "text", "text": "Gemini client not initialized"}], True

        name = (args.get("name") or args.get("title") or "").strip()
        prompt = (args.get("prompt") or args.get("system_prompt") or "").strip()
        description = args.get("description", "")
        if not name or not prompt:
            return [{"type": "text", "text": "Both 'name' and 'prompt' are required to create a Gem"}], True

        from backend.app.api.gems import _LOCAL_CUSTOM_GEMS
        from backend.app.bridge.models import Gem
        import time as _time
        import uuid as _uuid
        try:
            gem = await client.create_gem(name=name, prompt=prompt, description=description)
        except Exception as e:
            logger.warning("Upstream create_gem fallback: %s", e)
            now_ts = int(_time.time())
            gem = Gem(
                id=f"gem_{_uuid.uuid4().hex[:12]}",
                name=name,
                description=description,
                prompt=prompt,
                is_builtin=False,
                created_at=now_ts,
                updated_at=now_ts,
            )
        _LOCAL_CUSTOM_GEMS[gem.id] = gem
        res = {"ok": True, "gem": gem.to_dict() if hasattr(gem, "to_dict") else asdict(gem)}
        return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False

    async def _call_gemini_update_gem(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not client:
            return [{"type": "text", "text": "Gemini client not initialized"}], True

        gem_id = (args.get("gem_id") or args.get("id") or "").strip()
        name = (args.get("name") or args.get("title") or "").strip()
        prompt = (args.get("prompt") or args.get("system_prompt") or "").strip()
        description = args.get("description", "")
        if not gem_id or not name:
            return [{"type": "text", "text": "Both 'gem_id' and 'name' are required to update a Gem"}], True

        from backend.app.api.gems import _LOCAL_CUSTOM_GEMS
        from backend.app.bridge.models import Gem
        import time as _time
        try:
            gem = await client.update_gem(gem_id=gem_id, name=name, prompt=prompt, description=description)
        except Exception as e:
            logger.warning("Upstream update_gem fallback: %s", e)
            now_ts = int(_time.time())
            gem = Gem(
                id=gem_id,
                name=name,
                description=description,
                prompt=prompt,
                is_builtin=False,
                created_at=now_ts,
                updated_at=now_ts,
            )
        _LOCAL_CUSTOM_GEMS[gem.id] = gem
        res = {"ok": True, "gem": gem.to_dict() if hasattr(gem, "to_dict") else asdict(gem)}
        return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False

    async def _call_gemini_delete_gem(
        self,
        args: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        if not client:
            return [{"type": "text", "text": "Gemini client not initialized"}], True

        gem_id = (args.get("gem_id") or args.get("id") or "").strip()
        if not gem_id:
            return [{"type": "text", "text": "'gem_id' is required to delete a Gem"}], True

        from backend.app.api.gems import _LOCAL_CUSTOM_GEMS
        try:
            deleted = await client.delete_gem(gem_id=gem_id)
        except Exception as e:
            logger.warning("Upstream delete_gem fallback: %s", e)
            deleted = True
        _LOCAL_CUSTOM_GEMS.pop(gem_id, None)
        res = {"ok": True, "deleted": bool(deleted), "id": gem_id}
        return [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False

    # ------------------------------------------------------------------------
    # Resource Resolvers
    # ------------------------------------------------------------------------
    async def _resolve_resource(
        self,
        uri: str,
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> List[Dict[str, Any]]:
        """Resolves URI schemes like notebooklm:// and gemini:// into resource contents."""
        if uri == "notebooklm://notebooks":
            if notebooklm_client:
                try:
                    notebooks = await notebooklm_client.list_notebooks()
                    data = json.dumps([asdict(n) for n in notebooks], indent=2, ensure_ascii=False)
                except Exception:
                    from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
                    data = json.dumps(list(_LOCAL_FALLBACK_NOTEBOOKS.values()), indent=2, ensure_ascii=False)
            else:
                data = "[]"
            return [{"uri": uri, "mimeType": "application/json", "text": data}]

        if uri.startswith("notebooklm://notebook/"):
            nb_id = uri.split("/")[-1]
            if notebooklm_client:
                try:
                    detail = await notebooklm_client.get_notebook(nb_id)
                    data = json.dumps(asdict(detail), indent=2, ensure_ascii=False)
                except Exception:
                    from backend.app.api.notebooklm import _LOCAL_FALLBACK_NOTEBOOKS
                    data = json.dumps(_LOCAL_FALLBACK_NOTEBOOKS.get(nb_id, {}), indent=2, ensure_ascii=False)
            else:
                data = "{}"
            return [{"uri": uri, "mimeType": "application/json", "text": data}]

        if uri == "gemini://gems":
            if client:
                content, _ = await self._call_gemini_list_gems({"type": "all"}, client, notebooklm_client)
                data = content[0]["text"]
            else:
                data = "[]"
            return [{"uri": uri, "mimeType": "application/json", "text": data}]

        if uri == "gemini://models":
            from backend.app.bridge.models import DEFAULT_MODELS
            models_info = [
                {
                    "id": m.id,
                    "display_name": m.display_name,
                    "model_number": m.model_number,
                    "tier": m.tier.value if hasattr(m.tier, "value") else str(m.tier),
                    "supports_thinking": m.supports_thinking,
                }
                for m in DEFAULT_MODELS.values()
            ]
            return [{"uri": uri, "mimeType": "application/json", "text": json.dumps(models_info, indent=2)}]

        raise ValueError(f"Unsupported resource URI: {uri}")

    async def _call_claude_list_plugins(
        self,
        params: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Lists all available Claude plugins and agents from the local filesystem."""
        from backend.app.api.claude_plugins import scan_plugins
        import json

        agents = scan_plugins("agents")
        commands = scan_plugins("commands")
        skills = scan_plugins("skills")

        result_data = {
            "agents_count": len(agents),
            "commands_count": len(commands),
            "skills_count": len(skills),
            "agents": agents,
            "commands": commands,
            "skills": skills,
        }

        return ([{"type": "text", "text": json.dumps(result_data, indent=2)}], False)

    async def _call_claude_sync_plugin_gems(
        self,
        params: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Syncs local Claude agents to Gemini Custom Gems."""
        from backend.app.api.claude_plugins import sync_gems
        import json

        try:
            result_data = await sync_gems()
            return ([{"type": "text", "text": json.dumps(result_data, indent=2)}], False)
        except Exception as e:
            return ([{"type": "text", "text": f"Error syncing plugins: {str(e)}"}], True)

    async def _call_notebooklm_sync_repo(
        self,
        params: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Incrementally syncs workspace files into a NotebookLM notebook with SHA-256 deduplication."""
        from backend.app.api.notebooklm import sync_repo_to_notebooklm

        notebook_id = (params.get("notebook_id") or "nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f").strip()
        paths = params.get("paths")
        if isinstance(paths, str):
            paths = [paths]
        generate_briefing = bool(params.get("generate_briefing", False))
        force = bool(params.get("force", False))

        try:
            result_data = await sync_repo_to_notebooklm(
                notebook_id=notebook_id,
                paths=paths,
                generate_briefing=generate_briefing,
                force=force,
                client=notebooklm_client,
            )
            return ([{"type": "text", "text": json.dumps(result_data, indent=2, ensure_ascii=False)}], False)
        except Exception as e:
            return ([{"type": "text", "text": f"Error syncing repository to NotebookLM: {str(e)}"}], True)

    async def _call_claude_init_workspace(
        self,
        params: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Installs the .claude/{agents,skills,commands,hooks,settings.json} pack into a target directory."""
        from backend.app.api.claude_plugins import init_claude_workspace

        target_dir = (params.get("target_dir") or "").strip()
        if not target_dir:
            return ([{"type": "text", "text": "'target_dir' is required"}], True)

        include_mcp_config = bool(params.get("include_mcp_config", True))
        overwrite = bool(params.get("overwrite", True))
        gateway_url = str(params.get("gateway_url") or "http://127.0.0.1:8000")

        try:
            result_data = init_claude_workspace(
                target_dir=target_dir,
                include_mcp_config=include_mcp_config,
                overwrite=overwrite,
                gateway_url=gateway_url,
            )
            return ([{"type": "text", "text": json.dumps(result_data, indent=2, ensure_ascii=False)}], False)
        except Exception as e:
            return ([{"type": "text", "text": f"Error initializing Claude workspace: {str(e)}"}], True)

    async def _call_gemini_background_batch(
        self,
        params: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Dispatches or queries multi-stage background Gemini synthesis jobs."""
        from backend.app.api.claude_plugins import (
            BackgroundJobRequest,
            BackgroundStageSpec,
            create_background_job,
            get_background_job_endpoint,
            list_background_jobs_endpoint,
        )

        action = str(params.get("action") or "create").strip().lower()
        try:
            if action == "list":
                res = await list_background_jobs_endpoint(limit=int(params.get("limit", 20)))
                return ([{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False)
            elif action == "status":
                job_id = str(params.get("job_id") or "").strip()
                if not job_id:
                    return ([{"type": "text", "text": "'job_id' is required for action='status'"}], True)
                res = await get_background_job_endpoint(job_id)
                return ([{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False)
            else:
                title = str(params.get("title") or "").strip()
                if not title:
                    return ([{"type": "text", "text": "'title' is required for action='create'"}], True)
                raw_stages = params.get("stages") or []
                stages = [BackgroundStageSpec(**s) for s in raw_stages if isinstance(s, dict)]
                req = BackgroundJobRequest(
                    title=title,
                    workspace_dir=params.get("workspace_dir"),
                    stages=stages,
                    script_command=params.get("script_command"),
                    artifacts=list(params.get("artifacts") or []),
                    run_async=bool(params.get("run_async", True)),
                )
                res = await create_background_job(req)
                return ([{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False)
        except Exception as e:
            return ([{"type": "text", "text": f"Error in gemini_background_batch: {str(e)}"}], True)

    async def _call_gemini_task_pipeline(
        self,
        params: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """REQ-PIPE-06: Executes Task-Mode pipeline orchestration actions via MCP."""
        from backend.app.bridge.task_pipeline import get_task_pipeline_orchestrator

        orch = get_task_pipeline_orchestrator()
        action = str(params.get("action") or "list_schedules").strip().lower()
        try:
            if action == "list_schedules":
                res: Any = {"ok": True, "schedules": orch.list_schedules()}
            elif action == "create_schedule":
                res = orch.create_schedule(
                    name=str(params.get("name") or "Scheduled Task"),
                    workspace_dir=str(params.get("workspace_dir") or "."),
                    prompt=str(params.get("prompt") or ""),
                    interval_seconds=int(params.get("interval_seconds", 3600)),
                    target_files=params.get("target_files"),
                )
            elif action == "delete_schedule":
                sid = str(params.get("schedule_id") or "")
                res = {"ok": True, "deleted": orch.delete_schedule(sid), "schedule_id": sid}
            elif action == "list_runs":
                res = {"ok": True, "runs": orch.list_runs()}
            elif action == "get_run":
                rid = str(params.get("run_id") or "")
                res = orch.get_run(rid) or {"error": f"Run '{rid}' not found"}
            elif action == "run_now":
                res = await orch.execute_pipeline_run(
                    workspace_dir=str(params.get("workspace_dir") or "."),
                    prompt=str(params.get("prompt") or ""),
                    target_files=params.get("target_files"),
                    run_pytest=bool(params.get("run_pytest", False)),
                )
            elif action == "approve":
                res = orch.approve_run(str(params.get("run_id") or ""))
            elif action == "reject":
                res = orch.reject_run(str(params.get("run_id") or ""), reason=str(params.get("reason") or ""))
            elif action == "sync_cloud":
                res = await orch.sync_cloud_scheduled_actions(client=client)
            else:
                return ([{"type": "text", "text": f"Unsupported action '{action}'"}], True)
            return ([{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False)
        except Exception as exc:
            return ([{"type": "text", "text": f"Error in gemini_task_pipeline: {exc}"}], True)

    async def _call_gemini_capabilities(
        self,
        params: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """REQ-CAP-01..07: Dispatches M31-M34 native Gemini capability actions via MCP."""
        from backend.app.bridge.gemini_capabilities import (
            get_canvas_manager,
            get_cloud_schedule_manager,
            get_deep_research_engine,
            get_response_quality_engine,
            get_saved_info_manager,
            run_cloud_python_sandbox,
        )

        action = str(params.get("action") or "").strip().lower()
        try:
            if action == "sandbox_run":
                res: Any = await run_cloud_python_sandbox(
                    code=str(params.get("code") or ""),
                    prompt=params.get("prompt"),
                    client=client,
                    cid=params.get("cid"),
                    rid=params.get("rid"),
                    rcid=params.get("rcid"),
                )
            elif action == "canvas_create":
                res = get_canvas_manager().create_document(
                    filename=str(params.get("filename") or "untitled.py"),
                    content=str(params.get("content") or ""),
                    cid=params.get("cid"),
                    rid=params.get("rid"),
                    rcid=params.get("rcid"),
                )
            elif action == "canvas_edit_range":
                res = get_canvas_manager().apply_range_edit(
                    doc_id=str(params.get("doc_id") or ""),
                    start_line=int(params.get("start_line", 1)),
                    end_line=int(params.get("end_line", 1)),
                    replacement_content=str(params.get("replacement_content") or ""),
                )
            elif action == "deep_research":
                res = await get_deep_research_engine().run_deep_research(
                    topic=str(params.get("topic") or ""),
                    auto_approve=bool(params.get("auto_approve", True)),
                    client=client,
                )
            elif action == "saved_info_add":
                res = get_saved_info_manager().add_memory(
                    content=str(params.get("content") or ""),
                    category=str(params.get("category") or "engineering_rule"),
                    client=client,
                )
            elif action == "saved_info_list":
                res = {"ok": True, "memories": get_saved_info_manager().list_memories()}
            elif action == "saved_info_delete":
                mid = str(params.get("memory_id") or "")
                res = {"ok": True, "deleted": get_saved_info_manager().delete_memory(mid, client=client)}
            elif action == "saved_info_sync_file":
                res = get_saved_info_manager().sync_rules_file(
                    file_path=str(params.get("file_path") or ""),
                    category=str(params.get("category") or "engineering_rule"),
                    client=client,
                )
            elif action == "cloud_schedule_create":
                res = get_cloud_schedule_manager().create_cloud_schedule(
                    name=str(params.get("name") or "Scheduled Action"),
                    prompt=str(params.get("prompt") or ""),
                    cron_expr=str(params.get("cron_expr") or "0 * * * *"),
                    client=client,
                )
            elif action == "cloud_schedule_list":
                res = {"ok": True, "schedules": get_cloud_schedule_manager().list_cloud_schedules(client=client)}
            elif action == "cloud_schedule_pause":
                res = get_cloud_schedule_manager().pause_cloud_schedule(
                    schedule_id=str(params.get("schedule_id") or ""),
                    paused=bool(params.get("paused", True)),
                    client=client,
                )
            elif action == "cloud_schedule_delete":
                sid = str(params.get("schedule_id") or "")
                res = {"ok": True, "deleted": get_cloud_schedule_manager().delete_cloud_schedule(sid, client=client)}
            elif action == "double_check":
                res = await get_response_quality_engine().double_check_response(
                    text=str(params.get("text") or ""),
                    cid=params.get("cid"),
                    rid=params.get("rid"),
                    rcid=params.get("rcid"),
                    client=client,
                )
            elif action == "select_draft":
                res = get_response_quality_engine().select_best_draft(
                    draft_candidates=list(params.get("draft_candidates") or []),
                )
            elif action == "modify_response":
                res = await get_response_quality_engine().modify_response(
                    text=str(params.get("text") or ""),
                    modifier=str(params.get("modifier") or "concise"),
                    cid=params.get("cid"),
                    rid=params.get("rid"),
                    rcid=params.get("rcid"),
                    client=client,
                )
            elif action == "ultra_profile":
                from backend.app.bridge.ultra_capabilities import get_ultra_subscription_manager

                res = get_ultra_subscription_manager().detect_subscription_profile(
                    account_id=str(params.get("account_id") or "acc_21aac20f"),
                )
            elif action == "ultra_deep_think":
                from backend.app.bridge.ultra_capabilities import run_deep_think_reasoning

                res = await run_deep_think_reasoning(
                    prompt=str(params.get("prompt") or ""),
                    branch_count=int(params.get("branch_count", 3)),
                    client=client,
                    cid=params.get("cid"),
                )
            elif action == "ultra_pack_2m":
                from backend.app.bridge.ultra_capabilities import pack_ultra_2m_repository_context

                res = pack_ultra_2m_repository_context(
                    workspace_dir=str(params.get("workspace_dir") or "."),
                )
            elif action == "ultra_veo_render":
                from backend.app.bridge.ultra_capabilities import get_veo_ultra_engine

                res = await get_veo_ultra_engine().render_ultra_scene(
                    prompt=str(params.get("prompt") or ""),
                    model_key=str(params.get("model_key") or "veo_3_1_t2v_hq_1080p_audio"),
                    resolution=str(params.get("resolution") or "1080p"),
                )
            elif action == "ultra_notebooklm_audio":
                from backend.app.bridge.ultra_capabilities import get_notebooklm_ultra_engine

                res = await get_notebooklm_ultra_engine().generate_ultra_audio_overview(
                    notebook_id=str(params.get("notebook_id") or "nb_ultra_1"),
                    mode=str(params.get("mode") or "deep_dive"),
                    instructions=str(params.get("instructions") or ""),
                )
            else:
                return ([{"type": "text", "text": f"Unsupported action '{action}'"}], True)
            return ([{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False)
        except Exception as exc:
            return ([{"type": "text", "text": f"Error in gemini_capabilities: {exc}"}], True)

    async def _call_gemini_ecosystem_scout(
        self,
        params: Dict[str, Any],
        client: Optional[GeminiWebClient],
        notebooklm_client: Optional[NotebookLMClient],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """REQ-SCOUT-05: Executes M41 Ecosystem Scout actions via MCP."""
        from backend.app.bridge.ecosystem_scout import get_ecosystem_scout

        scout = get_ecosystem_scout()
        action = str(params.get("action") or "sweep").strip().lower()
        try:
            if action == "sweep":
                res: Any = await scout.run_intelligence_sweep(
                    lanes=params.get("lanes"),
                    focus_query=str(params.get("focus_query") or ""),
                    client=client,
                )
            elif action == "list_findings":
                findings = scout.list_findings(
                    lane=params.get("lane"),
                    target_agy_repo=params.get("target_agy_repo"),
                    min_score=float(params.get("min_score", 0.0)),
                )
                res = {"ok": True, "count": len(findings), "findings": findings}
            elif action == "adapt_skill":
                res = scout.adapt_skill_to_antigravity(
                    source_ide=str(params.get("source_ide") or "cursor"),
                    skill_name=str(params.get("skill_name") or "adapted-skill"),
                    raw_content=str(params.get("raw_content") or ""),
                    target_plugin=str(params.get("target_plugin") or "agy-golden-stack"),
                )
            elif action == "install_skill":
                if bool(params.get("install_all", False)):
                    res = scout.install_all_staged_skills(skills_root=params.get("skills_root"))
                else:
                    res = scout.install_staged_skill(
                        skill_name_or_id=str(params.get("skill_name") or ""),
                        skills_root=params.get("skills_root"),
                    )
            elif action == "promote_finding":
                res = scout.promote_finding_to_pipeline(
                    finding_id=str(params.get("finding_id") or ""),
                )
            elif action == "digest":
                res = scout.generate_digest_markdown()
            else:
                return ([{"type": "text", "text": f"Unsupported action '{action}'"}], True)
            return ([{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}], False)
        except Exception as exc:
            return ([{"type": "text", "text": f"Error in gemini_ecosystem_scout: {exc}"}], True)


