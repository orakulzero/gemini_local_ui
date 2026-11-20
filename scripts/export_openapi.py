#!/usr/bin/env python3
"""
scripts/export_openapi.py
CLI tool to export OpenAPI schema from backend.app.main:app as deterministic UTF-8 JSON.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.main import app  # noqa: E402


def export_openapi(out_path: Path) -> None:
    schema = app.openapi()
    content = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"Exported OpenAPI schema to {out_path} ({len(content)} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export FastAPI OpenAPI JSON schema")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("openapi.json"),
        help="Destination path for openapi.json (default: openapi.json)",
    )
    args = parser.parse_args()
    export_openapi(args.out)


if __name__ == "__main__":
    main()
