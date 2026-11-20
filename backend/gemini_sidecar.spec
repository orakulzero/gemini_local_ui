# -*- mode: python ; coding: utf-8 -*-
"""
backend/gemini_sidecar.spec
PyInstaller onedir directory bundle (COLLECT) specification for the Gemini Local UI
Tauri v2 Python backend sidecar (`gemini-backend-x86_64-pc-windows-msvc.exe`).
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parent

datas = [
    (str(SPEC_DIR / "app" / "storage" / "schema.sql"), "backend/app/storage"),
]
binaries = []

for pkg in ("sqlite_vec", "wreq"):
    try:
        binaries.extend(collect_dynamic_libs(pkg))
    except Exception:
        pass
    try:
        datas.extend(collect_data_files(pkg))
    except Exception:
        pass

hiddenimports = [
    "aiosqlite",
    "fastapi",
    "mcp",
    "numpy",
    "sqlite_vec",
    "structlog",
    "uvicorn",
    "uvicorn.lifespan.on",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "websockets",
    "wreq",
]
try:
    hiddenimports.extend(collect_submodules("backend"))
except Exception:
    pass

a = Analysis(
    [str(SPEC_DIR / "sidecar_entry.py")],
    pathex=[str(REPO_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure, noarchive=False)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gemini-backend-x86_64-pc-windows-msvc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="gemini-backend",
)
