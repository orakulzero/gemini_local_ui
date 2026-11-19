"""
backend/app/mcp/builtin_tools_server.py
Standard In-Process MCP Server exposing safe local utilities (stdio).
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import html
import operator
import re
import sys
import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("builtin_local_utils")
SAFE_ANNOTATIONS = ToolAnnotations(read_only_hint=True, destructive_hint=False)

SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_ast_node(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_OPERATORS:
        return SAFE_OPERATORS[type(node.op)](_eval_ast_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in SAFE_OPERATORS:
        left = _eval_ast_node(node.left)
        right = _eval_ast_node(node.right)
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError("Division by zero")
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1000000):
            raise ValueError("Exponent too large")
        return SAFE_OPERATORS[type(node.op)](left, right)
    raise ValueError(f"Unsupported syntax: {type(node).__name__}")


@server.tool(annotations=SAFE_ANNOTATIONS)
def get_current_datetime(timezone_offset_hours: int = 0) -> str:
    """Return the current date and time in ISO 8601 format."""
    now = datetime.now(timezone.utc)
    return now.isoformat()


@server.tool(annotations=SAFE_ANNOTATIONS)
def calculate_expression(expression: str) -> str:
    """Safely evaluate a mathematical expression (e.g. '21 * 2', '(100 + 45) / 5')."""
    try:
        expr = expression.strip()
        parsed = ast.parse(expr, mode="eval")
        val = _eval_ast_node(parsed.body)
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        return str(val)
    except Exception as e:
        return f"Error: {e}"


@server.tool(annotations=SAFE_ANNOTATIONS)
def fetch_webpage_text(url: str, max_chars: int = 4000) -> str:
    """Fetch and extract visible text from a webpage URL."""
    try:
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "GeminiLocalUI/1.0"})
            resp.raise_for_status()
            text = resp.text
            text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:max_chars]
    except Exception as e:
        return f"Error fetching webpage: {e}"


if __name__ == "__main__":
    server.run(transport="stdio")
