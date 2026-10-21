"""
backend/app/mcp/stdio_server.py
Standalone Stdio transport runner for Model Context Protocol (MCP).
Reads line-delimited JSON-RPC 2.0 from sys.stdin, dispatches to MCPServer (or proxies to the live local daemon),
and writes responses to sys.stdout. All diagnostic logs are sent strictly to sys.stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, Optional

# Ensure project root is on sys.path regardless of working directory
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.notebooklm import NotebookLMClient
from backend.app.bridge.session import SessionManager
from backend.app.mcp.server import MCPServer
from backend.app.storage.vault import CredentialVault

# Configure root logger strictly to sys.stderr to avoid polluting sys.stdout
logging.basicConfig(
    stream=sys.stderr,
    level=logging.WARNING,
    format="[%(asctime)s] [%(levelname)s] [gemini-mcp-stdio] %(message)s",
)
logger = logging.getLogger("gemini_mcp.stdio")


async def run_stdio_server(daemon_url: Optional[str] = "http://127.0.0.1:8000") -> None:
    """
    Main event loop for stdio transport.
    - Responds instantaneously (<1ms) to `initialize`, `ping`, `tools/list`, `resources/list`.
    - For `tools/call` and `resources/read`, proxies to the live local daemon (`http://127.0.0.1:8000/mcp`)
      if reachable, or lazily initializes the local DPAPI Vault session in-process.
    """
    server = MCPServer()
    session_manager: Optional[SessionManager] = None
    client: Optional[GeminiWebClient] = None
    notebooklm_client: Optional[NotebookLMClient] = None
    http_proxy_client = httpx.AsyncClient(timeout=180.0)

    async def _ensure_local_clients() -> tuple[Optional[GeminiWebClient], Optional[NotebookLMClient]]:
        nonlocal session_manager, client, notebooklm_client
        if client is not None:
            return client, notebooklm_client
        vault_path = PROJECT_ROOT / "data" / "vault.enc"
        vault = CredentialVault(vault_path=vault_path) if vault_path.exists() else CredentialVault()
        session_manager = SessionManager(base_url="https://gemini.google.com", vault=vault)
        try:
            await session_manager.initialize_all()
        except Exception as e:
            logger.warning("Could not initialize all vault accounts: %s", e)

        if session_manager.context:
            client = GeminiWebClient(session_context=session_manager.context, base_url=session_manager.base_url)
            cookies = dict(session_manager.context.cookies)
            at_token = getattr(session_manager.context, "at_token", None)
            notebooklm_client = NotebookLMClient(cookies=cookies, fallback_snlm0e=at_token)
        return client, notebooklm_client

    while True:
        line_str = await asyncio.to_thread(sys.stdin.readline)
        if not line_str:
            break

        line_str = line_str.strip()
        if not line_str:
            continue

        try:
            req_json = json.loads(line_str)
        except json.JSONDecodeError as err:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {str(err)}"},
            }
            sys.stdout.write(json.dumps(err_resp, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            continue

        method = req_json.get("method") if isinstance(req_json, dict) else None
        needs_live_execution = method in ("tools/call", "resources/read") or isinstance(req_json, list)

        try:
            resp_json = None
            if needs_live_execution and daemon_url:
                try:
                    r = await http_proxy_client.post(f"{daemon_url.rstrip('/')}/mcp", json=req_json)
                    if r.status_code == 200:
                        resp_json = r.json()
                    elif r.status_code == 204:
                        resp_json = None
                except Exception:
                    resp_json = None

            if resp_json is None and not (needs_live_execution and method == "notifications/initialized"):
                if needs_live_execution:
                    c, nb = await _ensure_local_clients()
                else:
                    c, nb = None, None
                resp_json = await server.handle_message(req_json, client=c, notebooklm_client=nb)

            if resp_json is not None:
                sys.stdout.write(json.dumps(resp_json, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            logger.error("Unhandled error processing message: %s", exc, exc_info=True)
            err_resp = {
                "jsonrpc": "2.0",
                "id": req_json.get("id") if isinstance(req_json, dict) else None,
                "error": {"code": -32603, "message": f"Internal error: {str(exc)}"},
            }
            sys.stdout.write(json.dumps(err_resp, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    await http_proxy_client.aclose()
    if client:
        await client.close()
    if notebooklm_client:
        await notebooklm_client.close()
    if session_manager:
        await session_manager.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini Local UI Model Context Protocol (MCP) Stdio Server")
    parser.add_argument(
        "--daemon-url",
        type=str,
        default="http://127.0.0.1:8000",
        help="Local daemon URL (default: http://127.0.0.1:8000)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_stdio_server(daemon_url=args.daemon_url))
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
