"""
backend/app/security/path_sandbox.py
Path sanitization preventing traversal, NTFS Alternate Data Streams, and UNC escaping.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Union

SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DOS_DEVICE_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _check_dos_device(name: str) -> None:
    stem = name.split(".")[0].strip().upper()
    if stem in DOS_DEVICE_STEMS:
        raise ValueError(f"Reserved Windows DOS device stem is not permitted: '{name}'")


def validate_safe_slug(slug: str) -> str:
    """Validates that a slug is a safe identifier."""
    if not slug or not SLUG_PATTERN.match(slug):
        raise ValueError(f"Invalid slug format: '{slug}'")
    if slug.endswith((".", " ")):
        raise ValueError(f"Slug cannot end with a dot or space: '{slug}'")
    _check_dos_device(slug)
    return slug


def validate_safe_path(
    raw_path: Union[str, Path],
    allowed_roots: Optional[List[Union[str, Path]]] = None,
) -> Path:
    """
    Validates that raw_path does not contain traversal, ADS, DOS devices, symlinks,
    or UNC sequences, and resides within one of allowed_roots if specified.
    """
    s_path = str(raw_path)

    # 1. Reject NUL byte injection
    if "\x00" in s_path:
        raise ValueError("NUL byte injection detected in path")

    # 2. Reject extended-length and device prefixes
    for pfx in ("\\\\?\\", "//?/", "\\\\.\\", "//./", "\\\\?", "//?"):
        if s_path.startswith(pfx):
            raise ValueError(f"Extended-length/device prefix '{pfx}' is not permitted")

    # 3. Reject UNC paths (\\server\share or //server/share)
    if s_path.startswith(("\\\\", "//")):
        raise ValueError("UNC paths are not permitted in local sandboxing")

    # 4. Reject NTFS Alternate Data Streams (file:stream or path:stream)
    path_without_drive = (
        s_path[2:]
        if (len(s_path) >= 2 and s_path[1] == ":" and s_path[0].isalpha())
        else s_path
    )
    if ":" in path_without_drive:
        raise ValueError("NTFS Alternate Data Streams (':') are not permitted")

    # 5. Reject explicit directory traversal tokens and inspect segments
    path_obj = Path(s_path)
    for part in path_obj.parts:
        if part == "..":
            raise ValueError("Directory traversal sequence ('..') is not permitted")
        if part not in (".", "..") and not (len(part) == 2 and part[1] == ":" and part[0].isalpha()):
            if part.endswith((".", " ")):
                raise ValueError(f"Path segment cannot end with dot or space: '{part}'")
            _check_dos_device(part)

    # 6. Check for symlinks / junctions along existing components
    curr = Path(path_obj.anchor) if path_obj.is_absolute() else Path(".")
    rel_parts = path_obj.parts[1:] if path_obj.is_absolute() else path_obj.parts
    for part in rel_parts:
        curr = curr / part
        try:
            if curr.is_symlink():
                raise ValueError(f"Symlinks and junctions are not permitted: '{curr}'")
        except OSError:
            pass

    try:
        resolved = path_obj.resolve()
    except Exception as e:
        raise ValueError(f"Failed to resolve path '{s_path}': {e}")

    # 7. Restrict to allowed root directories if specified
    if allowed_roots:
        is_contained = False
        for root in allowed_roots:
            root_res = Path(root).resolve()
            try:
                if hasattr(resolved, "is_relative_to"):
                    if resolved.is_relative_to(root_res):
                        is_contained = True
                        break
                else:
                    resolved.relative_to(root_res)
                    is_contained = True
                    break
            except ValueError:
                continue
        if not is_contained:
            raise ValueError(f"Path '{resolved}' is outside allowed sandboxed roots")

    return resolved
