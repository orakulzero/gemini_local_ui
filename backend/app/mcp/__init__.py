"""
backend/app/mcp/__init__.py
Model Context Protocol (MCP) server package for Gemini Local UI.
"""

from backend.app.mcp.routes import router as mcp_router
from backend.app.mcp.server import MCPServer

__all__ = ["MCPServer", "mcp_router"]
