#!/usr/bin/env python3
"""
backend/sidecar_entry.py
Tauri v2 Desktop Python Sidecar Entrypoint supporting dynamic `--port 0` socket
pre-binding and post-startup `GEMINI_SIDECAR_LISTENING=http://127.0.0.1:<port>` banner.
"""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing
from pathlib import Path
import socket
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _validate_loopback_host(host: str) -> str:
    cleaned = (host or "127.0.0.1").strip()
    if cleaned not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"Sidecar must bind strictly to loopback, got: {cleaned}")
    return "127.0.0.1" if cleaned == "localhost" else cleaned


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini Local UI Desktop Sidecar Daemon")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="TCP port (0 = OS ephemeral port)")
    parser.add_argument("--log-level", default="warning", help="Uvicorn log level")
    return parser.parse_args(argv)


async def _serve_with_ready_banner(
    sock: socket.socket,
    bind_host: str,
    bound_port: int,
    log_level: str,
) -> None:
    import uvicorn
    from backend.app.main import app

    config = uvicorn.Config(
        app=app,
        host=bind_host,
        port=bound_port,
        log_level=log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    config.load()
    server.lifespan = config.lifespan_class(config)
    await server.startup(sockets=[sock])
    if server.started:
        url_host = f"[{bind_host}]" if ":" in bind_host else bind_host
        print(f"GEMINI_SIDECAR_LISTENING=http://{url_host}:{bound_port}", flush=True)
        try:
            await server.main_loop()
        finally:
            await server.shutdown(sockets=[sock])


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = parse_args(argv)
    bind_host = _validate_loopback_host(args.host)

    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform != "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_host, int(args.port)))
    sock.listen(128)
    sock.setblocking(False)
    bound_port = int(sock.getsockname()[1])

    try:
        asyncio.run(_serve_with_ready_banner(sock, bind_host, bound_port, str(args.log_level)))
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
