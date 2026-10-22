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
from typing import Any, Dict, List, Optional, Set, Tuple, Union
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

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(abs_path):
            # Prune excluded directories in-place
            dirs[:] = [d for d in dirs if d not in IGNORE_DIR_NAMES and not d.startswith(".")]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in IGNORE_FILE_EXTENSIONS:
                    continue
                file_path = os.path.join(root, file)
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
        raise RuntimeError(f"Upload failed (HTTP {resp.status_code}): {resp.text}")
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
        r"(?:###|\*\*|####|#)?\s*(?:File):\s*[`\"']?([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)[`\"']?\s*[\r\n]+```(?:[\w+\-#]+)?[\r\n]([\s\S]*?)```",
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
        rel_path = item["rel_path"]
        content = item["content"]

        # Prevent path traversal outside target directory
        full_dest = os.path.abspath(os.path.join(abs_extract_dir, rel_path))
        try:
            dest_path = Path(full_dest).resolve()
            target_path = Path(abs_extract_dir).resolve()
            if not dest_path.is_relative_to(target_path) or dest_path == target_path:
                sys.stderr.write(f"Warning: Skipping unsafe path traversal: {rel_path}\n")
                continue
        except (ValueError, Exception):
            sys.stderr.write(f"Warning: Skipping unsafe path traversal: {rel_path}\n")
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


def run_task(
    prompt: str,
    model: str = "gemini-4-argon",
    thinking: int = 2,
    mode: str = "task",
    account: Optional[str] = None,
    conversation_id: Optional[str] = None,
    context_path: Optional[str] = None,
    extract_dir: Optional[str] = None,
    pack_output: Optional[str] = None,
    stream: bool = True,
    base_url: str = DEFAULT_BASE_URL,
    save_path: Optional[str] = None,
    show_thoughts: bool = True,
) -> Dict[str, Any]:
    """
    Submits a Task mode request to /api/chat/generate with SSE streaming,
    optional context zip attachment, and materials extraction.
    """
    if not check_daemon_health(base_url):
        sys.stderr.write(f"Error: Gemini Local Daemon not reachable at {base_url}\n")
        sys.stderr.write("Start backend: cd ~/teamwork_projects/gemini_local_ui && python -m uvicorn backend.app.main:app --port 8000\n")
        sys.exit(1)

    # Auto-resolve Ultra account if not specified
    if not account:
        acc_info = get_accounts_info(base_url)
        active_id = acc_info.get("active_account_id")
        accounts = acc_info.get("accounts", [])
        ultra_acc = next((a["id"] for a in accounts if a.get("tier") in ("ultra", "pro") and a.get("health_status") == "healthy"), None)
        account = active_id if active_id and active_id != "default" else (ultra_acc or active_id or "acc_1164ad7c")

    attachments_payload: List[Dict[str, Any]] = []

    # Handle Context Attachment Packaging
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
                f"[Task context: attached archive {zip_filename} with the full project code and structure. "
                f"Inspect and use the files from the archive to complete the task.]\n\n"
                f"{prompt}"
            )
        except Exception as e:
            sys.stderr.write(f"Error processing context archive {context_path}: {e}\n")
            sys.exit(1)

    modes_to_try = [mode]
    if mode == "task":
        modes_to_try.append("chat")

    full_text = ""
    full_thoughts = ""
    effective_model = ""
    active_mode = mode
    conv_id = None
    done_meta: Dict[str, Any] = {}
    duration = 0.0

    for attempt_mode in modes_to_try:
        active_mode = attempt_mode
        effective_model = f"{model}@thinking={thinking}@mode={attempt_mode}"
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "model": effective_model,
            "feature_mode": attempt_mode,
            "incognito": False,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if account:
            payload["account_id"] = account
        if attachments_payload:
            payload["attachments"] = attachments_payload

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if account:
            headers["x-gemini-account"] = account

        thoughts_accum: List[str] = []
        text_accum: List[str] = []
        done_meta = {}
        conv_id = None
        t0 = time.time()

        thought_header_printed = False
        text_header_printed = False

        try:
            with httpx.stream(
                "POST",
                f"{base_url}/api/chat/generate",
                json=payload,
                headers=headers,
                timeout=600.0,
            ) as resp:
                if resp.status_code != 200:
                    sys.stderr.write(f"HTTP Error {resp.status_code}: {resp.read().decode('utf-8', errors='replace')}\n")
                    sys.exit(1)

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
                        conv_id = ev.get("conversation_id")
                    elif ev_type == "thought":
                        delta = ev.get("delta", "")
                        thoughts_accum.append(delta)
                        if stream and show_thoughts:
                            if not thought_header_printed:
                                sys.stderr.write("\033[90m┌── 🧠 [THINKING PROCESS] ──────────────────────────\n")
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
                    elif ev_type == "done":
                        done_meta = ev.get("metadata", {})

        except KeyboardInterrupt:
            sys.stderr.write("\n\n[Task generation interrupted by user]\n")
            sys.exit(1)
        except Exception as e:
            sys.stderr.write(f"\nRequest error: {e}\n")
            sys.exit(1)

        if thought_header_printed and not text_header_printed:
            sys.stderr.write("\033[90m\n└── [END THINKING] ────────────────────────────────\n\033[0m\n")

        duration = time.time() - t0
        full_text = "".join(text_accum)
        meta_full = done_meta.get("full_text") or ""
        if len(meta_full) > len(full_text):
            missing_tail = meta_full[len(full_text):] if meta_full.startswith(full_text) else meta_full
            full_text = meta_full
            if stream:
                if thought_header_printed and not text_header_printed:
                    sys.stderr.write("\033[90m\n└── [END THINKING] ────────────────────────────────\n\033[0m\n")
                    text_header_printed = True
                sys.stdout.write(missing_tail)
                sys.stdout.flush()

        full_thoughts = "".join(thoughts_accum) or done_meta.get("full_thoughts", "")

        is_error_stub = (
            "I seem to be encountering an error" in full_text
            or "Can I try something else for you?" in full_text
            or "Sorry, something went wrong" in full_text
            or "I encountered an error" in full_text
            or "I'm having a hard time fulfilling your request" in full_text
        )
        if full_text.strip() and not is_error_stub:
            break

        if attempt_mode == "task" and len(modes_to_try) > 1:
            sys.stderr.write("\n[Notice: Task mode container returned empty/error stub. Retrying with mode='chat' on Gemini 4 Argon...]\n")

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
        "feature_mode": mode,
        "duration_seconds": round(duration, 2),
        "text": full_text,
        "thoughts": full_thoughts,
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
        description="Gemini Task Mode CLI: Dispatch planning, research, and deep reasoning to Gemini 4 Argon / Pro"
    )
    parser.add_argument("prompt", nargs="?", default="", help="Task prompt or goal")
    parser.add_argument("-f", "--file", help="Read prompt from markdown/text file")
    parser.add_argument("-c", "--context", help="Directory or zip archive to upload and attach as context to the Task")
    parser.add_argument("-x", "--extract-dir", help="Directory to extract generated code files/materials into")
    parser.add_argument("-p", "--pack-output", help="Save extracted materials into a .zip archive")
    parser.add_argument("-m", "--model", default="gemini-4-argon", help="Model ID (default: gemini-4-argon)")
    parser.add_argument("-t", "--thinking", type=int, default=2, choices=[1, 2, 4], help="Reasoning level: 1=Low, 4=Medium, 2=High (default: 2)")
    parser.add_argument("--mode", default="task", choices=["task", "chat"], help="Feature mode: task (Agency container) or chat (default: task)")
    parser.add_argument("-a", "--account", help="Specific account ID or alias (default: active Ultra/Pro account)")
    parser.add_argument("-s", "--session", "--conversation-id", dest="session_id", help="Continue existing conversation / session ID")
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
        prompt = "Inspect the attached project context archive and describe its architecture and structure."

    res = run_task(
        prompt=prompt.strip(),
        model=args.model,
        thinking=args.thinking,
        mode=args.mode,
        account=args.account,
        conversation_id=args.session_id,
        context_path=args.context,
        extract_dir=args.extract_dir,
        pack_output=args.pack_output,
        stream=not args.json,
        base_url=args.base_url,
        save_path=args.output,
        show_thoughts=not args.no_thoughts,
    )

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
