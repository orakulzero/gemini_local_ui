"""
backend/app/security/path_sandbox.py
Path sanitization preventing traversal, NTFS Alternate Data Streams, and UNC escaping.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Union

SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_safe_slug(slug: str) -> str:
    """Validates that a slug contains only alphanumeric characters, dashes, and underscores."""
    if not slug or not SLUG_PATTERN.match(slug):
        raise ValueError(f"Invalid slug format: '{slug}' (must be alphanumeric, dash, or underscore)")
    return slug


def validate_safe_path(
    raw_path: Union[str, Path],
    allowed_roots: Optional[List[Union[str, Path]]] = None,
) -> Path:
    """
    Validates that raw_path does not contain traversal, ADS, or UNC sequences,
    and resides within one of allowed_roots if specified.
    """
    s_path = str(raw_path)

    # 1. Reject NUL byte injection
    if "\x00" in s_path:
        raise ValueError("NUL byte injection detected in path")

    # 2. Reject UNC paths (\\server\share or //server/share)
    if s_path.startswith(("\\\\", "//")):
        raise ValueError("UNC paths are not permitted in local sandboxing")

    # 3. Reject NTFS Alternate Data Streams (file:stream or path:stream)
    path_without_drive = (
        s_path[2:]
        if (len(s_path) >= 2 and s_path[1] == ":" and s_path[0].isalpha())
        else s_path
    )
    if ":" in path_without_drive:
        raise ValueError("NTFS Alternate Data Streams (':') are not permitted")

    # 4. Reject explicit directory traversal tokens
    path_obj = Path(s_path)
    if ".." in path_obj.parts:
        raise ValueError("Directory traversal sequence ('..') is not permitted")

    try:
        resolved = path_obj.resolve()
    except Exception as e:
        raise ValueError(f"Failed to resolve path '{s_path}': {e}")

    # 5. Restrict to allowed root directories if specified
    if allowed_roots:
        is_contained = False
        for root in allowed_roots:
            root_res = Path(root).resolve()
            try:
                resolved.relative_to(root_res)
                is_contained = True
                break
            except ValueError:
                continue
        if not is_contained:
            raise ValueError(f"Path '{resolved}' is outside allowed sandboxed roots")

    return resolved
