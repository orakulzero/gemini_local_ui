#!/usr/bin/env python3
"""
gemini_task.py
CLI bridge script for dispatching Task Mode with Gemini 4 Argon / Pro models
via the local Gemini UI & API Bridge daemon (http://127.0.0.1:8000).

Supports:
- Task mode (featureMode=40, Agency task container)
- Thinking levels (1=Low, 4=Medium, 2=High)
- Multi-account targeting with auto-selection of Ultra / Pro tier
- Context packaging: auto-packs project folder into zip archive and attaches to Task
- Materials extraction: extracts generated code files into target project directory
- Output archiving: bundles generated materials into an output .zip archive
- Real-time streaming with live thought process display
- Output saving to markdown files
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import zipfile
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

DEFAULT_BASE_URL = os.environ.get("GEMINI_LOCAL_URL", "http://127.0.0.1:8000")

# Standard noise patterns ignored when auto-zipping context directories
IGNORE_DIR_NAMES: Set[str] = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "target",
    ".next",
    ".cache",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

IGNORE_FILE_EXTENSIONS: Set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".log",
    ".tmp",
    ".swp",
    ".DS_Store",
    ".exe",
    ".dll",
    ".bin",
    ".so",
    ".dylib",
}

WINDOWS_RESERVED_NAMES: Set[str] = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

MAX_UNCOMPRESSED_SOURCE_BYTES: int = 25 * 1024 * 1024  # 25 MB


def map_error_to_exit_code(err_msg: str, status_code: Optional[int] = None) -> int:
    """
    Deterministic exit code mapping:
    - 0: Success
    - 1: CLI / general error
    - 2: Upstream Google 5xx / gateway crash
    - 3: Authentication failure (401/403/session drop)
    - 4: Rate limit / Quota lockout (429 / 1037)
    """
    if status_code in (401, 403):
        return 3
    if status_code == 429:
        return 4
    if status_code and status_code >= 500:
        return 2

    err_lower = err_msg.lower()
    if any(k in err_lower for k in ("1037", "429", "cooldown", "quota", "too many requests")):
        return 4
    if any(k in err_lower for k in ("401", "403", "unauthorized", "forbidden", "autherror", "auth_error", "session expired", "token expired", "cookie", "session drop")):
        return 3
    if any(k in err_lower for k in ("500", "502", "503", "504", "servererror", "server error", "gateway")):
        return 2
    return 1


def check_daemon_health(base_url: str = DEFAULT_BASE_URL) -> bool:
    """Verifies that the local Gemini UI daemon is reachable."""
    try:
        r = httpx.get(f"{base_url}/health", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def get_accounts_info(base_url: str = DEFAULT_BASE_URL) -> Dict[str, Any]:
    """Retrieves account registry and active account status."""
    try:
        r = httpx.get(f"{base_url}/api/accounts", timeout=5.0)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def print_status(base_url: str = DEFAULT_BASE_URL) -> None:
    """Prints daemon and account status."""
    is_healthy = check_daemon_health(base_url)
    status_str = "\033[92m● ONLINE\033[0m" if is_healthy else "\033[91m● OFFLINE\033[0m"
    print(f"Gemini Daemon: {status_str} ({base_url})")
    if not is_healthy:
        print("Start daemon: python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 (in gemini_local_ui)")
        return

    acc_info = get_accounts_info(base_url)
    active_id = acc_info.get("active_account_id", "default")
    print(f"Active Account ID: {active_id}")
    print("\nRegistered Accounts:")
    for a in acc_info.get("accounts", []):
        active_mark = " [*]" if a.get("is_active") else " [ ]"
        tier = a.get("tier", "free").upper()
        print(f"  {active_mark} {a.get('name', 'Account')} (ID: {a.get('id')}) | Tier: {tier} | Health: {a.get('health_status')}")


def pack_context_path(target_path: str) -> Tuple[bytes, str, int]:
    """
    Packs a file or directory into a clean in-memory zip archive.
    Returns (archive_bytes, archive_filename, file_count).
    """
    abs_path = os.path.abspath(target_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Context path not found: {target_path}")

    if os.path.isfile(abs_path):
        filename = os.path.basename(abs_path)
        with open(abs_path, "rb") as f:
            data = f.read()
        if filename.lower().endswith(".zip"):
            return data, filename, 1
        # Pack single file into a zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(filename, data)
        zip_name = f"{os.path.splitext(filename)[0]}.zip"
        return buf.getvalue(), zip_name, 1

    # Directory packaging
    dir_name = os.path.basename(abs_path.rstrip("\\/")) or "project"
    zip_name = f"{dir_name}_context.zip"
    buf = io.BytesIO()
    file_count = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(abs_path):
            # Prune excluded directories in-place
            dirs[:] = [d for d in dirs if d not in IGNORE_DIR_NAMES and not d.startswith(".")]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in IGNORE_FILE_EXTENSIONS:
                    continue
                file_path = os.path.join(root, file)
                try:
                    fsize = os.path.getsize(file_path)
                    total_uncompressed_bytes += fsize
                    if total_uncompressed_bytes > MAX_UNCOMPRESSED_SOURCE_BYTES:
                        raise ValueError(
                            f"Context size limit exceeded: uncompressed files exceed 25 MB "
                            f"({total_uncompressed_bytes / (1024 * 1024):.2f} MB). "
                            f"Prune unnecessary directories or files."
                        )
                except OSError:
                    pass
                rel_path = os.path.relpath(file_path, abs_path).replace("\\", "/")
                try:
                    zf.write(file_path, arcname=rel_path)
                    file_count += 1
                except Exception as e:
                    sys.stderr.write(f"Warning: could not add {rel_path} to archive: {e}\n")

    return buf.getvalue(), zip_name, file_count


def upload_attachment(
    data: bytes,
    filename: str,
    base_url: str = DEFAULT_BASE_URL,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Uploads a file or archive to the local daemon's /api/chat/upload endpoint.
    Returns attachment metadata dictionary for StreamGenerate.
    """
    headers = {}
    if account_id:
        headers["x-gemini-account"] = account_id

    files = {
        "file": (filename, data, "application/zip" if filename.endswith(".zip") else "application/octet-stream")
    }

    try:
        resp = httpx.post(
            f"{base_url}/api/chat/upload",
            files=files,
            headers=headers,
            timeout=60.0,
        )
        if resp.status_code == 200:
            return resp.json()
        err_msg = f"Upload failed (HTTP {resp.status_code}): {resp.text}"
        sys.stderr.write(f"{err_msg}\n")
        sys.exit(map_error_to_exit_code(resp.text, status_code=resp.status_code))
    except Exception as e:
        sys.stderr.write(f"Upload error: {e}\n")
        raise


def extract_files_from_response(text: str, extract_dir: str) -> List[Dict[str, Any]]:
    """
    Parses code blocks and file annotations from Gemini's response and writes them to extract_dir.
    Supported patterns:
    1. ```<lang>:<path> ... ``` or ```filepath=<path> ... ```
    2. ### File: <path> ... ```<lang> ... ```
    3. **File:** `<path>` ... ```<lang> ... ```
    4. ```<path/with/extension> ... ```
    """
    abs_extract_dir = os.path.abspath(extract_dir)
    os.makedirs(abs_extract_dir, exist_ok=True)
    extracted_items: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()

    # Pattern 1: Header above code block: (### File: path | **File:** `path` | File: path)
    header_pattern = re.compile(
        r"(?:###|\*\*|####|#)?\s*(?:File|Файл):\s*[`\"']?([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)[`\"']?\s*[\r\n]+```(?:[\w+\-#]+)?[\r\n]([\s\S]*?)```",
        re.IGNORECASE,
    )
    for match in header_pattern.finditer(text):
        rel_path = match.group(1).strip().replace("\\", "/").lstrip("/")
        code_content = match.group(2)
        if rel_path not in seen_paths:
            seen_paths.add(rel_path)
            extracted_items.append({"rel_path": rel_path, "content": code_content})

    # Pattern 2: Code block with lang:path or file:path or filepath=path
    inline_pattern = re.compile(
        r"```(?:[\w+\-#]+)?(?:[:=]|(?:path[:=]))([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)[\r\n]([\s\S]*?)```",
        re.IGNORECASE,
    )
    for match in inline_pattern.finditer(text):
        rel_path = match.group(1).strip().replace("\\", "/").lstrip("/")
        code_content = match.group(2)
        if rel_path not in seen_paths:
            seen_paths.add(rel_path)
            extracted_items.append({"rel_path": rel_path, "content": code_content})

    # Pattern 3: Code block tag that directly represents a file path (e.g. ```src/app.py)
    tag_path_pattern = re.compile(
        r"```([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)[\r\n]([\s\S]*?)```"
    )
    for match in tag_path_pattern.finditer(text):
        rel_path = match.group(1).strip().replace("\\", "/").lstrip("/")
        code_content = match.group(2)
        if rel_path not in seen_paths:
            seen_paths.add(rel_path)
            extracted_items.append({"rel_path": rel_path, "content": code_content})

    # Write files to disk safely
    written_files: List[Dict[str, Any]] = []
    for item in extracted_items:
        raw_rel_path = item["rel_path"]
        content = item["content"]

        # Sanitize path components and guard against Windows reserved names
        parts = raw_rel_path.replace("\\", "/").strip("/").split("/")
        sanitized_parts = []
        for part in parts:
            clean_part = re.sub(r'[:*?"<>|]', '_', part)
            stem = clean_part.split(".")[0].upper()
            if stem in WINDOWS_RESERVED_NAMES:
                clean_part = f"_{clean_part}"
            sanitized_parts.append(clean_part)
        rel_path = "/".join(sanitized_parts)

        # Prevent path traversal outside target directory
        full_dest = os.path.abspath(os.path.join(abs_extract_dir, rel_path))
        try:
            dest_path = Path(full_dest).resolve()
            target_path = Path(abs_extract_dir).resolve()
            if not dest_path.is_relative_to(target_path) or dest_path == target_path:
                sys.stderr.write(f"Warning: Skipping unsafe path traversal: {raw_rel_path}\n")
                continue
        except (ValueError, Exception):
            sys.stderr.write(f"Warning: Skipping unsafe path traversal: {raw_rel_path}\n")
            continue

        os.makedirs(os.path.dirname(full_dest), exist_ok=True)
        try:
            with open(full_dest, "w", encoding="utf-8") as f:
                f.write(content)
            file_size = os.path.getsize(full_dest)
            written_files.append({"path": rel_path, "abs_path": full_dest, "size": file_size})
            sys.stderr.write(f"  [📄 Extracted: {rel_path} ({file_size} bytes)]\n")
        except Exception as e:
            sys.stderr.write(f"Failed writing {rel_path}: {e}\n")

    return written_files


def create_output_zip(files: List[Dict[str, Any]], zip_destination: str) -> None:
    """Creates a zip archive containing the extracted output files."""
    dest_path = os.path.abspath(zip_destination)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in files:
            zf.write(item["abs_path"], arcname=item["path"])

    total_bytes = os.path.getsize(dest_path)
    sys.stderr.write(f"[📦 Output archive created: {zip_destination} ({len(files)} files, {total_bytes} bytes)]\n")


def format_grounded_sources_markdown(done_meta: Dict[str, Any], existing_text: str = "") -> str:
    """Formats deduplicated citations, grounding sources, and search queries as a Markdown appendix."""
    citations = list(done_meta.get("citations") or [])
    g_sources = list(done_meta.get("grounding_sources") or [])
    if not g_sources and isinstance(done_meta.get("grounding"), dict):
        g_sources = list(done_meta["grounding"].get("sources") or [])
    g_queries = list(done_meta.get("grounding_queries") or [])
    if not g_queries and isinstance(done_meta.get("grounding"), dict):
        g_queries = list(done_meta["grounding"].get("queries") or [])

    seen_urls: Set[str] = set()
    source_lines: List[str] = []
    for item in [*citations, *g_sources]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("uri") or "").strip()
        if not url or url in seen_urls or url in existing_text:
            continue
        seen_urls.add(url)
        title = str(item.get("title") or item.get("domain") or url).strip()
        snippet = str(item.get("snippet") or "").strip()
        idx = len(source_lines) + 1
        line = f"{idx}. **[{title}]({url})**"
        if snippet:
            line += f" — {snippet}"
        source_lines.append(line)

    blocks: List[str] = []
    if source_lines:
        blocks.append("## 🌐 Grounded Sources & Citations\n\n" + "\n".join(source_lines))
    if g_queries:
        q_lines = [f"- `{q}`" for q in g_queries if isinstance(q, str) and q.strip()]
        if q_lines:
            blocks.append("### 🔍 Search Queries Executed\n\n" + "\n".join(q_lines))
    return "\n\n".join(blocks)


def _stream_single_turn(
    base_url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    stream: bool,
    show_thoughts: bool,
    phase_label: str = "",
) -> Tuple[str, str, Optional[str], Dict[str, Any]]:
    """Executes one streaming turn against /api/chat/generate and returns (text, thoughts, conv_id, done_meta)."""
    thoughts_accum: List[str] = []
    text_accum: List[str] = []
    stream_citations: List[Any] = []
    stream_grounding_sources: List[Any] = []
    stream_grounding_queries: List[str] = []
    done_meta: Dict[str, Any] = {}
    conv_id: Optional[str] = payload.get("conversation_id")

    thought_header_printed = False
    text_header_printed = False
    hdr_tag = f" ({phase_label})" if phase_label else ""

    try:
        with httpx.stream(
            "POST",
            f"{base_url}/api/chat/generate",
            json=payload,
            headers=headers,
            timeout=600.0,
        ) as resp:
            if resp.status_code != 200:
                err_body = resp.read().decode("utf-8", errors="replace")
                sys.stderr.write(f"HTTP Error {resp.status_code}: {err_body}\n")
                sys.exit(map_error_to_exit_code(err_body, status_code=resp.status_code))

            for line in resp.iter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                try:
                    ev = json.loads(data_str)
                except Exception:
                    continue

                ev_type = ev.get("type")
                if ev_type == "start":
                    conv_id = ev.get("conversation_id") or conv_id
                elif ev_type == "thought":
                    delta = ev.get("delta", "")
                    thoughts_accum.append(delta)
                    if stream and show_thoughts:
                        if not thought_header_printed:
                            sys.stderr.write(f"\033[90m┌── 🧠 [THINKING PROCESS{hdr_tag}] ──────────────────────────\n")
                            thought_header_printed = True
                        sys.stderr.write(f"\033[90m{delta}\033[0m")
                        sys.stderr.flush()
                elif ev_type == "text":
                    delta = ev.get("delta", "")
                    text_accum.append(delta)
                    if stream:
                        if thought_header_printed and not text_header_printed:
                            sys.stderr.write("\033[90m\n└── [END THINKING] ────────────────────────────────\n\033[0m\n")
                            text_header_printed = True
                        sys.stdout.write(delta)
                        sys.stdout.flush()
                elif ev_type == "citation":
                    c_meta = ev.get("metadata") or {}
                    if c_meta.get("citations"):
                        stream_citations = list(c_meta["citations"])
                elif ev_type == "grounding":
                    g_meta = ev.get("metadata") or {}
                    if g_meta.get("grounding_sources"):
                        stream_grounding_sources = list(g_meta["grounding_sources"])
                    if g_meta.get("queries"):
                        stream_grounding_queries = list(g_meta["queries"])
                elif ev_type == "done":
                    done_meta = dict(ev.get("metadata") or {})
                elif ev_type == "error":
                    err_payload = ev.get("error", "Unknown upstream error")
                    sys.stderr.write(f"\n[Upstream Stream Error]: {err_payload}\n")
                    sys.exit(map_error_to_exit_code(str(err_payload)))

    except KeyboardInterrupt:
        sys.stderr.write("\n\n[Task generation interrupted by user]\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"\nRequest error: {e}\n")
        sys.exit(1)

    if thought_header_printed and not text_header_printed:
        sys.stderr.write("\033[90m\n└── [END THINKING] ────────────────────────────────\n\033[0m\n")

    full_text = "".join(text_accum)
    if not full_text and done_meta.get("full_text"):
        full_text = str(done_meta["full_text"])
        if stream:
            sys.stdout.write(full_text)
            sys.stdout.flush()

    full_thoughts = "".join(thoughts_accum) or str(done_meta.get("full_thoughts") or "")
    if stream_citations and not done_meta.get("citations"):
        done_meta["citations"] = stream_citations
    if stream_grounding_sources and not done_meta.get("grounding_sources"):
        done_meta["grounding_sources"] = stream_grounding_sources
    if stream_grounding_queries and not done_meta.get("grounding_queries"):
        done_meta["grounding_queries"] = stream_grounding_queries

    return full_text, full_thoughts, conv_id, done_meta


def _build_default_approve_prompt(topic: str, research_plan: Optional[Dict[str, Any]]) -> str:
    has_cyr = any("\u0400" <= c <= "\u04FF" for c in topic)
    steps_block = ""
    if isinstance(research_plan, dict) and isinstance(research_plan.get("steps"), list) and research_plan["steps"]:
        lines = []
        for idx, st in enumerate(research_plan["steps"], 1):
            if isinstance(st, dict):
                t_s = str(st.get("title") or "").strip()
                d_s = str(st.get("description") or "").strip()
                lines.append(f"{idx}. {t_s}" + (f" — {d_s}" if d_s else ""))
        if lines:
            steps_block = "\n" + "\n".join(lines) + "\n\n"
    if has_cyr:
        return (
            f"Подтверждаю план исследования по теме «{topic}». Начни исследование прямо сейчас:\n"
            f"{steps_block}"
            "Выполни глубокий многопоточный поиск по всем пунктам и синтезируй полный структурированный отчёт с проверяемыми цитатами и ссылками на источники."
        )
    return (
        f"Approve and start the Deep Research plan for '{topic}':\n"
        f"{steps_block}"
        "Execute comprehensive web research across all sub-questions and synthesize a detailed markdown report with grounded citations."
    )


def run_task(
    prompt: str,
    model: str = "gemini-4-argon",
    thinking: int = 2,
    mode: str = "task",
    account: Optional[str] = None,
    context_path: Optional[str] = None,
    extract_dir: Optional[str] = None,
    pack_output: Optional[str] = None,
    stream: bool = True,
    base_url: str = DEFAULT_BASE_URL,
    save_path: Optional[str] = None,
    show_thoughts: bool = True,
    plan_only: bool = False,
    approve_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Submits a Task Mode or 2-Phase Deep Research request to /api/chat/generate with SSE streaming,
    optional context zip attachment, automatic Phase 1 -> Phase 2 plan approval, and materials extraction.
    """
    if not check_daemon_health(base_url):
        sys.stderr.write(f"Error: Gemini Local Daemon not reachable at {base_url}\n")
        sys.stderr.write("Start backend: cd ~/teamwork_projects/gemini_local_ui && python -m uvicorn backend.app.main:app --port 8000\n")
        sys.exit(1)

    if not account:
        acc_info = get_accounts_info(base_url)
        active_id = acc_info.get("active_account_id")
        accounts = acc_info.get("accounts", [])
        ultra_acc = next((a["id"] for a in accounts if a.get("tier") in ("ultra", "pro") and a.get("health_status") == "healthy"), None)
        account = active_id if active_id and active_id != "default" else (ultra_acc or active_id or "acc_1164ad7c")

    attachments_payload: List[Dict[str, Any]] = []
    if context_path:
        try:
            zip_bytes, zip_filename, count = pack_context_path(context_path)
            size_kb = round(len(zip_bytes) / 1024, 1)
            sys.stderr.write(f"[📦 Packed context: '{context_path}' -> {zip_filename} ({count} files, {size_kb} KB)]\n")
            upload_meta = upload_attachment(zip_bytes, zip_filename, base_url=base_url, account_id=account)
            attachments_payload.append({
                "uri": upload_meta["uri"],
                "filename": zip_filename,
                "mime_type": upload_meta.get("mime_type", "application/zip"),
                "file_size": len(zip_bytes),
            })
            sys.stderr.write(f"[📎 Attached to Task: {zip_filename} ({upload_meta['uri']})]\n")
            prompt = (
                f"[Контекст задачи: прикреплён архив {zip_filename} с полным кодом и структурой проекта. "
                f"Изучи и используй файлы из архива для выполнения задачи.]\n\n"
                f"{prompt}"
            )
        except Exception as e:
            sys.stderr.write(f"Error processing context archive {context_path}: {e}\n")
            sys.exit(1)

    norm_mode = "deep-research" if mode.lower() in ("deep-research", "deep_research", "research", "dr") else mode.lower()
    is_deep_research = norm_mode == "deep-research"
    modes_to_try = [norm_mode]
    if norm_mode in ("task", "deep-research"):
        modes_to_try.append("chat")

    full_text = ""
    full_thoughts = ""
    effective_model = ""
    conv_id: Optional[str] = None
    done_meta: Dict[str, Any] = {}
    research_plan: Optional[Dict[str, Any]] = None
    t0 = time.time()

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if account:
        headers["x-gemini-account"] = account

    for attempt_mode in modes_to_try:
        effective_model = f"{model}@thinking={thinking}@mode={attempt_mode}"
        t1_prompt = prompt
        if is_deep_research and attempt_mode == "deep-research":
            has_cyr = any("\u0400" <= c <= "\u04FF" for c in prompt)
            t1_prompt = (
                f"Режим Deep Research. Тема исследования:\n{prompt}\n\nСоставь структурированный план исследования (3–6 ключевых направлений) для запуска глубокого поиска."
                if has_cyr
                else f"Deep Research Mode. Topic:\n{prompt}\n\nCreate a structured Deep Research plan (3-6 sub-questions) to initiate grounded web investigation."
            )

        payload_t1: Dict[str, Any] = {
            "prompt": t1_prompt,
            "model": effective_model,
            "feature_mode": attempt_mode,
            "incognito": False,
        }
        if account:
            payload_t1["account_id"] = account
        if attachments_payload:
            payload_t1["attachments"] = attachments_payload

        phase1_lbl = "PHASE 1: RESEARCH PLAN" if is_deep_research else ""
        t1_text, t1_thoughts, conv_id, t1_meta = _stream_single_turn(
            base_url=base_url,
            payload=payload_t1,
            headers=headers,
            stream=stream,
            show_thoughts=show_thoughts,
            phase_label=phase1_lbl,
        )
        research_plan = t1_meta.get("research_plan") if isinstance(t1_meta.get("research_plan"), dict) else None
        cid = t1_meta.get("cid")
        rid = t1_meta.get("rid")
        rcid = t1_meta.get("rcid")
        has_plan_card = bool(research_plan) or ("### 📋" in t1_thoughts)

        if plan_only:
            full_text = t1_text.strip() or t1_thoughts.strip()
            full_thoughts = t1_thoughts
            done_meta = t1_meta
            break

        needs_phase2 = bool(cid) and (
            (is_deep_research and attempt_mode == "deep-research")
            or (attempt_mode == "task" and not t1_text.strip() and has_plan_card)
        )
        if needs_phase2:
            sys.stderr.write(
                f"\n[🔬 Phase 1 Plan Ready (cid={cid}). Auto-approving Phase 2 Deep Research execution on {model}...]\n"
            )
            p2_prompt = approve_prompt or _build_default_approve_prompt(prompt, research_plan)
            payload_t2: Dict[str, Any] = {
                "conversation_id": conv_id,
                "cid": cid,
                "rid": rid,
                "rcid": rcid,
                "prompt": p2_prompt,
                "model": effective_model,
                "feature_mode": attempt_mode,
                "incognito": False,
            }
            if account:
                payload_t2["account_id"] = account
            t2_text, t2_thoughts, conv_id_2, t2_meta = _stream_single_turn(
                base_url=base_url,
                payload=payload_t2,
                headers=headers,
                stream=stream,
                show_thoughts=show_thoughts,
                phase_label="PHASE 2: DEEP RESEARCH EXECUTION",
            )
            conv_id = conv_id_2 or conv_id
            full_text = t2_text.strip() or t1_text.strip()
            full_thoughts = "\n\n===\n\n".join(p for p in (t1_thoughts.strip(), t2_thoughts.strip()) if p)
            done_meta = {**t1_meta, **t2_meta}
            if research_plan and not done_meta.get("research_plan"):
                done_meta["research_plan"] = research_plan
        else:
            full_text = t1_text
            full_thoughts = t1_thoughts
            done_meta = t1_meta

        if full_text.strip():
            break

        if attempt_mode in ("task", "deep-research") and len(modes_to_try) > 1:
            sys.stderr.write(f"\n[Notice: {attempt_mode} container returned empty text. Retrying with mode='chat'...]\n")

    duration = time.time() - t0
    sources_md = format_grounded_sources_markdown(done_meta, existing_text=full_text)
    if sources_md and (is_deep_research or done_meta.get("citations") or done_meta.get("grounding_sources")):
        full_text = f"{full_text.rstrip()}\n\n---\n\n{sources_md}\n"
        if stream:
            sys.stdout.write(f"\n\n---\n\n{sources_md}\n")
            sys.stdout.flush()

    extracted_files: List[Dict[str, Any]] = []
    # Output Materials Extraction
    if extract_dir:
        sys.stderr.write(f"\n[Extracting generated materials to: {extract_dir}]\n")
        extracted_files = extract_files_from_response(full_text, extract_dir)
        sys.stderr.write(f"[Extracted {len(extracted_files)} files]\n")

    # Output Materials Archiving
    if pack_output:
        if not extract_dir:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_extracted = extract_files_from_response(full_text, tmp_dir)
                if tmp_extracted:
                    create_output_zip(tmp_extracted, pack_output)
                else:
                    sys.stderr.write("No files found in response to pack into zip.\n")
        else:
            if extracted_files:
                create_output_zip(extracted_files, pack_output)
            else:
                sys.stderr.write("No files found to pack into zip.\n")

    result = {
        "conversation_id": conv_id,
        "model": effective_model,
        "feature_mode": norm_mode,
        "duration_seconds": round(duration, 2),
        "text": full_text,
        "thoughts": full_thoughts,
        "research_plan": research_plan,
        "extracted_files": extracted_files,
        "metadata": done_meta,
    }

    if save_path:
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            sys.stderr.write(f"\n[Saved task output to: {save_path}]\n")
        except Exception as e:
            sys.stderr.write(f"\nFailed saving output to {save_path}: {e}\n")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gemini Task Mode & Deep Research CLI: Dispatch planning, 2-phase web research, and deep reasoning to Gemini 4 Argon"
    )
    parser.add_argument("prompt", nargs="?", default="", help="Task prompt or research topic")
    parser.add_argument("-f", "--file", help="Read prompt from markdown/text file")
    parser.add_argument("-c", "--context", help="Directory or zip archive to upload and attach as context")
    parser.add_argument("-x", "--extract-dir", help="Directory to extract generated code files/materials into")
    parser.add_argument("-p", "--pack-output", help="Save extracted materials into a .zip archive")
    parser.add_argument("-m", "--model", default="gemini-4-argon", help="Model ID (default: gemini-4-argon)")
    parser.add_argument("-t", "--thinking", type=int, default=2, choices=[1, 2, 4], help="Reasoning level: 1=Low, 4=Medium, 2=High (default: 2)")
    parser.add_argument(
        "--mode",
        default="task",
        choices=["task", "chat", "deep-research", "deep_research"],
        help="Feature mode: task (Agency container), deep-research (2-phase Plan -> Execute), or chat (default: task)",
    )
    parser.add_argument("-dr", "--deep-research", action="store_true", help="Shortcut for --mode deep-research (2-phase Argon Deep Research)")
    parser.add_argument("--plan-only", action="store_true", help="Stop after Phase 1 research plan generation without auto-approving Phase 2")
    parser.add_argument("--approve-prompt", help="Custom approval/steering prompt for Phase 2 of Deep Research")
    parser.add_argument("-a", "--account", help="Specific account ID or alias (default: active Ultra/Pro account)")
    parser.add_argument("-o", "--output", help="Save final plan/response to markdown file")
    parser.add_argument("--no-thoughts", action="store_true", help="Hide thinking process from stderr")
    parser.add_argument("--json", action="store_true", help="Output full JSON envelope")
    parser.add_argument("--status", action="store_true", help="Check daemon and account status")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Daemon URL (default: {DEFAULT_BASE_URL})")

    args = parser.parse_args()

    if args.status:
        print_status(args.base_url)
        return

    prompt = args.prompt
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                prompt = f.read()
        except Exception as e:
            sys.stderr.write(f"Error reading prompt file {args.file}: {e}\n")
            sys.exit(1)
    elif not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read()

    if not prompt.strip() and not args.context:
        parser.print_help()
        sys.exit(0)

    if not prompt.strip() and args.context:
        prompt = "Изучи прикреплённый архив контекста проекта и опиши его архитектуру и структуру."

    effective_mode = "deep-research" if args.deep_research else args.mode
    res = run_task(
        prompt=prompt.strip(),
        model=args.model,
        thinking=args.thinking,
        mode=effective_mode,
        account=args.account,
        context_path=args.context,
        extract_dir=args.extract_dir,
        pack_output=args.pack_output,
        stream=not args.json,
        base_url=args.base_url,
        save_path=args.output,
        show_thoughts=not args.no_thoughts,
        plan_only=args.plan_only,
        approve_prompt=args.approve_prompt,
    )

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
