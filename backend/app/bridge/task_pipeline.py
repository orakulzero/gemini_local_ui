"""
backend/app/bridge/task_pipeline.py
M30: Autonomous Gemini 4 Argon Task Mode Pipeline, Scheduled Orchestrator & Self-Healing Verifier.
Implements REQ-PIPE-01, REQ-PIPE-02, REQ-PIPE-03, REQ-PIPE-04, REQ-PIPE-05.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
import difflib
import inspect
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
import uuid
import zipfile

logger = logging.getLogger("gemini_backend.task_pipeline")

PRUNED_DIRS: set[str] = {
    ".git",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".supergraph",
    ".agents-learning",
    ".monitor",
}

REQ_TAG_PATTERN = re.compile(r"\bREQ-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04FF]")
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github_pat", re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("local_user_path", re.compile(r"(?:[A-Za-z]:\\Users\\[A-Za-z0-9_ .-]+|/home/[A-Za-z0-9_.-]+)")),
]


def _extract_python_ast_contract(source: str) -> str:
    """REQ-PIPE-01: Extracts top-level class and function signature contracts from Python source."""
    tree = ast.parse(source)
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            args_str = ast.unparse(node.args)
            ret_str = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
            lines.append(f"{prefix} {node.name}({args_str}){ret_str}:")
        elif isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            base_str = f"({', '.join(bases)})" if bases else ""
            lines.append(f"class {node.name}{base_str}:")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sprefix = "async def" if isinstance(sub, ast.AsyncFunctionDef) else "def"
                    sargs = ast.unparse(sub.args)
                    sret = f" -> {ast.unparse(sub.returns)}" if sub.returns is not None else ""
                    lines.append(f"    {sprefix} {sub.name}({sargs}){sret}:")
    return "\n".join(lines)


def pack_workspace_context(
    workspace_dir: str | Path,
    target_files: list[str] | None = None,
) -> dict[str, Any]:
    """
    REQ-PIPE-01: Bundles workspace files into an in-memory .zip archive, excluding noise
    directories, and extracts AST signature contracts and REQ-* traceability tags.
    """
    root = Path(workspace_dir).resolve()
    buf = io.BytesIO()
    ast_contracts: dict[str, str] = {}
    req_tags_set: set[str] = set()
    included_files: list[str] = []
    target_norm = {p.replace("\\", "/").lstrip("/") for p in (target_files or [])}

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in sorted(dirnames)
                if d not in PRUNED_DIRS and not d.endswith(".egg-info")
            ]
            for fname in sorted(filenames):
                if fname.endswith((".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe")):
                    continue
                fpath = Path(dirpath) / fname
                rel_path = fpath.relative_to(root).as_posix()
                if any(part in PRUNED_DIRS for part in Path(rel_path).parts):
                    continue
                try:
                    raw_bytes = fpath.read_bytes()
                except OSError:
                    continue
                zf.writestr(rel_path, raw_bytes)
                included_files.append(rel_path)

                try:
                    text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    continue

                for match in REQ_TAG_PATTERN.findall(text):
                    req_tags_set.add(match)

                if rel_path.endswith(".py") and (not target_norm or rel_path in target_norm or rel_path.startswith("src/")):
                    try:
                        contract = _extract_python_ast_contract(text)
                        if contract:
                            ast_contracts[rel_path] = contract
                    except SyntaxError:
                        pass

    zip_bytes = buf.getvalue()
    return {
        "zip_filename": f"{root.name or 'workspace'}_context.zip",
        "zip_bytes": zip_bytes,
        "size_bytes": len(zip_bytes),
        "file_count": len(included_files),
        "files": included_files,
        "ast_contracts": ast_contracts,
        "req_tags": sorted(req_tags_set),
    }


def extract_code_files_from_response(text: str) -> dict[str, str]:
    """
    REQ-PIPE-03: Extracts multi-file code blocks from Gemini Task Mode Markdown responses.
    Supports '### File: <path>', '**File:** <path>', '```lang:<path>', and '<file path="...">'.
    """
    extracted: dict[str, str] = {}
    if not text:
        return extracted

    header_Fence = re.compile(
        r"(?:^|\n)(?:#{1,4}\s*File:\s*|\*\*File:\*\*\s*)`?([^\n`*]+?)`?\s*\n```[^\n]*\n(.*?)```",
        re.DOTALL,
    )
    for match in header_Fence.finditer(text):
        rel = match.group(1).strip().replace("\\", "/").lstrip("/")
        code = match.group(2)
        if rel:
            extracted[rel] = code if code.endswith("\n") else code + "\n"

    inline_fence = re.compile(r"```[a-zA-Z0-9_+-]+:([^\n`]+)\n(.*?)```", re.DOTALL)
    for match in inline_fence.finditer(text):
        rel = match.group(1).strip().replace("\\", "/").lstrip("/")
        code = match.group(2)
        if rel and rel not in extracted:
            extracted[rel] = code if code.endswith("\n") else code + "\n"

    xml_file = re.compile(r'<file\s+path=["\']([^"\']+)["\']>\s*\n?(.*?)</file>', re.DOTALL)
    for match in xml_file.finditer(text):
        rel = match.group(1).strip().replace("\\", "/").lstrip("/")
        code = match.group(2)
        if rel and rel not in extracted:
            extracted[rel] = code if code.endswith("\n") else code + "\n"

    return extracted


def _extract_top_level_symbol_map(source: str) -> dict[str, tuple[str, int]]:
    """Extracts top-level function and class names with parameter counts from Python AST."""
    tree = ast.parse(source)
    symbols: dict[str, tuple[str, int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arg_count = len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
            symbols[node.name] = ("func", arg_count)
        elif isinstance(node, ast.ClassDef):
            symbols[node.name] = ("class", 0)
    return symbols


def verify_five_quality_gates(
    workspace_dir: str | Path,
    orig_files: dict[str, str],
    candidate_files: dict[str, str],
    run_pytest: bool = True,
    pytest_args: list[str] | None = None,
) -> dict[str, Any]:
    """
    REQ-PIPE-04: Evaluates the 5 Quality Gates in an isolated sandbox without modifying workspace_dir:
      1. gate1_pytest: pytest passes in an isolated temporary copy of workspace_dir.
      2. gate2_secrets: zero secrets (PATs, API keys, private keys) or hardcoded user paths.
      3. gate3_ast_req: valid Python AST, 100% REQ-* tag preservation, and top-level signature preservation.
      4. gate4_english: zero Cyrillic characters (\\u0400-\\u04FF).
      5. gate5_line_cap: >= 95% non-empty line retention on existing files with >= 20 non-empty lines.
    """
    diagnostics: list[str] = []
    gate2_secrets = True
    gate3_ast_req = True
    gate4_english = True
    gate5_line_cap = True

    if not candidate_files:
        diagnostics.append("gate3_ast_req: candidate_files is empty")
        gate3_ast_req = False

    for rel_path, new_code in candidate_files.items():
        # Gate 2: Secret & path hygiene
        for label, pat in SECRET_PATTERNS:
            if pat.search(new_code):
                gate2_secrets = False
                diagnostics.append(f"gate2_secrets: detected {label} in {rel_path}")

        # Gate 4: 100% English-only check
        if CYRILLIC_PATTERN.search(new_code):
            gate4_english = False
            diagnostics.append(f"gate4_english: detected Cyrillic characters in {rel_path}")

        # Gate 3 (part A): Python AST syntax check
        if rel_path.endswith(".py"):
            try:
                ast.parse(new_code)
            except SyntaxError as exc:
                gate3_ast_req = False
                diagnostics.append(f"gate3_ast_req: SyntaxError in {rel_path}: {exc}")

    for rel_path, old_code in orig_files.items():
        if rel_path not in candidate_files:
            continue
        new_code = candidate_files[rel_path]

        # Gate 3 (part B): REQ-* tag preservation
        old_reqs = set(REQ_TAG_PATTERN.findall(old_code))
        new_reqs = set(REQ_TAG_PATTERN.findall(new_code))
        missing_reqs = sorted(old_reqs - new_reqs)
        if missing_reqs:
            gate3_ast_req = False
            diagnostics.append(
                f"gate3_ast_req: missing requirement tags in {rel_path}: {', '.join(missing_reqs)}"
            )

        # Gate 3 (part C): Top-level function/class symbol preservation
        if rel_path.endswith(".py") and gate3_ast_req:
            try:
                old_syms = _extract_top_level_symbol_map(old_code)
                new_syms = _extract_top_level_symbol_map(new_code)
                for sym_name, (sym_kind, old_argc) in old_syms.items():
                    if sym_name not in new_syms:
                        gate3_ast_req = False
                        diagnostics.append(
                            f"gate3_ast_req: missing top-level {sym_kind} '{sym_name}' in {rel_path}"
                        )
                    elif sym_kind == "func" and new_syms[sym_name][1] < old_argc:
                        gate3_ast_req = False
                        diagnostics.append(
                            f"gate3_ast_req: signature regression on '{sym_name}' in {rel_path}"
                        )
            except SyntaxError:
                gate3_ast_req = False

        # Gate 5: Non-empty line count retention (>= 95% for files with >= 20 non-empty lines)
        old_nonempty = [ln for ln in old_code.splitlines() if ln.strip()]
        new_nonempty = [ln for ln in new_code.splitlines() if ln.strip()]
        if len(old_nonempty) >= 20:
            min_required = int(len(old_nonempty) * 0.95)
            if len(new_nonempty) < min_required:
                gate5_line_cap = False
                diagnostics.append(
                    f"gate5_line_cap: {rel_path} non-empty lines dropped from "
                    f"{len(old_nonempty)} to {len(new_nonempty)} (min required: {min_required})"
                )

    # Gate 1: Isolated sandbox pytest execution
    gate1_pytest = True
    pytest_output = ""
    if run_pytest:
        root = Path(workspace_dir).resolve()
        with tempfile.TemporaryDirectory(prefix="m30_Verify_") as tmp_sandbox:
            sandbox_root = Path(tmp_sandbox) / "workspace"
            shutil.copytree(
                root,
                sandbox_root,
                ignore=shutil.ignore_patterns(*PRUNED_DIRS, "*.pyc", "*.pyo"),
                dirs_exist_ok=True,
            )
            for rel_path, new_code in candidate_files.items():
                dest = sandbox_root / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(new_code, encoding="utf-8")

            env = os.environ.copy()
            env["PYTHONPATH"] = str(sandbox_root)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            cmd = [sys.executable, "-m", "pytest", "-q", *(pytest_args or ["tests"])]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(sandbox_root),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                pytest_output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
                if proc.returncode != 0:
                    gate1_pytest = False
                    diagnostics.append(f"gate1_pytest: pytest failed (exit {proc.returncode}):\n{pytest_output}")
            except Exception as exc:
                gate1_pytest = False
                pytest_output = str(exc)
                diagnostics.append(f"gate1_pytest: pytest execution error: {exc}")

    gates = {
        "gate1_pytest": gate1_pytest,
        "gate2_secrets": gate2_secrets,
        "gate3_ast_req": gate3_ast_req,
        "gate4_english": gate4_english,
        "gate5_line_cap": gate5_line_cap,
    }
    return {
        "passed": all(gates.values()),
        "gates": gates,
        "diagnostics": diagnostics,
        "pytest_output": pytest_output,
    }


def _compute_unified_diff(orig_files: dict[str, str], candidate_files: dict[str, str]) -> str:
    """REQ-PIPE-05: Computes a unified diff across all candidate files against original files."""
    diff_chunks: list[str] = []
    for rel_path, new_code in sorted(candidate_files.items()):
        old_code = orig_files.get(rel_path, "")
        old_lines = old_code.splitlines(keepends=True)
        new_lines = new_code.splitlines(keepends=True)
        udiff = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )
        if udiff:
            diff_chunks.append("".join(udiff))
    return "\n".join(diff_chunks)


class TaskPipelineOrchestrator:
    """
    REQ-PIPE-02, REQ-PIPE-03, REQ-PIPE-05:
    Scheduled Orchestrator, Stateful CID/RID/RCID Self-Healing Task-Mode Runner,
    and Stage-Only Human-in-the-Loop Approval Manager.
    """

    def __init__(self, storage_path: str | Path | None = None) -> None:
        if storage_path is None:
            base_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "gemini_local_ui"
            storage_path = base_dir / "task_pipeline_state.json"
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = {
            "schedules": {},
            "runs": {},
            "cloud_actions": [],
        }
        self._load_state()

    def _load_state(self) -> None:
        if self.storage_path.exists():
            try:
                raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._state["schedules"] = raw.get("schedules", {})
                    self._state["runs"] = raw.get("runs", {})
                    self._state["cloud_actions"] = raw.get("cloud_actions", [])
            except Exception as exc:
                logger.warning("Failed loading task pipeline state from %s: %s", self.storage_path, exc)

    def _save_state(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed saving task pipeline state to %s: %s", self.storage_path, exc)

    # ------------------------------------------------------------------
    # REQ-PIPE-02: Schedule CRUD & Due Trigger Checks
    # ------------------------------------------------------------------
    def create_schedule(
        self,
        name: str,
        workspace_dir: str,
        prompt: str,
        schedule_type: str = "interval",
        interval_seconds: int = 3600,
        cron_expr: str | None = None,
        target_files: list[str] | None = None,
        model: str = "gemini-4-argon",
        thinking: int = 2,
        max_heal_attempts: int = 2,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """REQ-PIPE-02: Creates a recurring Task-Mode schedule (cron or interval)."""
        now_ts = time.time()
        schedule_id = f"sched_{uuid.uuid4().hex[:10]}"
        record: dict[str, Any] = {
            "schedule_id": schedule_id,
            "name": name,
            "workspace_dir": str(workspace_dir),
            "prompt": prompt,
            "schedule_type": schedule_type,
            "interval_seconds": max(10, int(interval_seconds)),
            "cron_expr": cron_expr or "",
            "target_files": list(target_files or []),
            "model": model,
            "thinking": int(thinking),
            "max_heal_attempts": int(max_heal_attempts),
            "enabled": bool(enabled),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run_ts": 0.0,
            "next_run_ts": now_ts + max(10, int(interval_seconds)),
            "last_run_id": None,
        }
        self._state["schedules"][schedule_id] = record
        self._save_state()
        return dict(record)

    def list_schedules(self) -> list[dict[str, Any]]:
        """REQ-PIPE-02: Lists all configured Task-Mode schedules."""
        return [dict(v) for v in self._state["schedules"].values()]

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        rec = self._state["schedules"].get(schedule_id)
        return dict(rec) if rec else None

    def update_schedule(self, schedule_id: str, **updates: Any) -> dict[str, Any]:
        """REQ-PIPE-02: Updates or pauses/resumes an existing schedule."""
        if schedule_id not in self._state["schedules"]:
            raise KeyError(f"Schedule '{schedule_id}' not found")
        rec = self._state["schedules"][schedule_id]
        for k, v in updates.items():
            if v is not None and k in rec:
                rec[k] = v
        self._save_state()
        return dict(rec)

    def delete_schedule(self, schedule_id: str) -> bool:
        """REQ-PIPE-02: Deletes a schedule by ID."""
        removed = self._state["schedules"].pop(schedule_id, None)
        if removed is not None:
            self._save_state()
            return True
        return False

    def get_due_schedules(self, now_ts: float | None = None) -> list[dict[str, Any]]:
        """REQ-PIPE-02: Returns enabled schedules whose next_run_ts is due."""
        ts = now_ts if now_ts is not None else time.time()
        due: list[dict[str, Any]] = []
        for rec in self._state["schedules"].values():
            if rec.get("enabled", True) and ts >= float(rec.get("next_run_ts", 0.0)):
                due.append(dict(rec))
        return due


    # ------------------------------------------------------------------
    # REQ-PIPE-03 & REQ-PIPE-05: Stateful Self-Healing & Stage-Only Approval
    # ------------------------------------------------------------------
    def _read_original_files(self, workspace_dir: Path, target_files: list[str] | None) -> dict[str, str]:
        orig: dict[str, str] = {}
        if target_files:
            for rel in target_files:
                norm = rel.replace("\\", "/").lstrip("/")
                fpath = workspace_dir / norm
                if fpath.is_file():
                    orig[norm] = fpath.read_text(encoding="utf-8", errors="replace")
        else:
            for dirpath, dirnames, filenames in os.walk(workspace_dir):
                dirnames[:] = [d for d in dirnames if d not in PRUNED_DIRS]
                for fn in filenames:
                    if fn.endswith(".py"):
                        fpath = Path(dirpath) / fn
                        norm = fpath.relative_to(workspace_dir).as_posix()
                        orig[norm] = fpath.read_text(encoding="utf-8", errors="replace")
        return orig

    async def _invoke_runner(
        self,
        runner: Callable[..., Any],
        prompt: str,
        attachments: list[dict[str, Any]] | None,
        cid: str | None,
        rid: str | None,
        rcid: str | None,
    ) -> dict[str, Any]:
        res = runner(prompt=prompt, attachments=attachments, cid=cid, rid=rid, rcid=rcid)
        if inspect.isawaitable(res):
            res = await res
        return dict(res)

    async def execute_pipeline_run(
        self,
        workspace_dir: str | Path,
        prompt: str,
        target_files: list[str] | None = None,
        model: str = "gemini-4-argon",
        thinking: int = 2,
        max_heal_attempts: int = 2,
        run_pytest: bool = True,
        task_runner: Callable[..., Any] | None = None,
        mock_candidate_files: dict[str, str] | None = None,
        schedule_id: str | None = None,
    ) -> dict[str, Any]:
        """
        REQ-PIPE-03, REQ-PIPE-05: Executes an isolated Gemini 4 Argon Task Mode run with
        stateful CID/RID/RCID self-healing and stages verified diffs without writing to workspace_dir.
        """
        root = Path(workspace_dir).resolve()
        orig_files = self._read_original_files(root, target_files)
        bundle = pack_workspace_context(root, target_files=target_files)

        cid: str | None = None
        rid: str | None = None
        rcid: str | None = None
        heal_attempts = 0
        candidate_files: dict[str, str] = {}
        gates_result: dict[str, Any] = {}

        if mock_candidate_files is not None:
            candidate_files = {
                k.replace("\\", "/").lstrip("/"): v for k, v in mock_candidate_files.items()
            }
            gates_result = verify_five_quality_gates(
                workspace_dir=root,
                orig_files=orig_files,
                candidate_files=candidate_files,
                run_pytest=run_pytest,
            )
            cid = "c_mock_pipeline"
            rid = "r_mock_1"
            rcid = "rc_mock_1"
        else:
            attachments = [
                {
                    "filename": bundle["zip_filename"],
                    "size_bytes": bundle["size_bytes"],
                    "ast_contracts": bundle["ast_contracts"],
                    "req_tags": bundle["req_tags"],
                }
            ]
            current_prompt = prompt
            for attempt in range(max_heal_attempts + 1):
                if task_runner is not None:
                    resp = await self._invoke_runner(
                        task_runner,
                        prompt=current_prompt,
                        attachments=attachments if attempt == 0 else None,
                        cid=cid,
                        rid=rid,
                        rcid=rcid,
                    )
                else:
                    resp = {
                        "cid": cid or f"c_task_{uuid.uuid4().hex[:8]}",
                        "rid": f"r_turn_{attempt + 1}",
                        "rcid": f"rc_turn_{attempt + 1}",
                        "text": "",
                    }
                cid = resp.get("cid") or cid
                rid = resp.get("rid") or rid
                rcid = resp.get("rcid") or rcid
                extracted = extract_code_files_from_response(str(resp.get("text", "")))
                if extracted:
                    candidate_files.update(extracted)

                gates_result = verify_five_quality_gates(
                    workspace_dir=root,
                    orig_files=orig_files,
                    candidate_files=candidate_files,
                    run_pytest=run_pytest,
                )
                if gates_result["passed"]:
                    break
                if attempt < max_heal_attempts:
                    heal_attempts += 1
                    failed_names = [k for k, v in gates_result["gates"].items() if not v]
                    diag_block = "\n".join(gates_result["diagnostics"])
                    current_prompt = (
                        f"Self-Healing Verification Failed on Gates: {', '.join(failed_names)}.\n"
                        f"Diagnostics / Pytest Output (FAILED / AssertionError):\n{diag_block}\n\n"
                        "Return fixed complete file(s) using '### File: <rel_path>' fenced code blocks "
                        "preserving 100% of REQ-* tags, signatures, and >=95% non-empty lines."
                    )

        unified_diff = _compute_unified_diff(orig_files, candidate_files)
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        status_val = "STAGED_AWAITING_APPROVAL" if gates_result.get("passed") else "FAILED_GATES"
        run_record: dict[str, Any] = {
            "run_id": run_id,
            "schedule_id": schedule_id,
            "workspace_dir": str(root),
            "prompt": prompt,
            "model": model,
            "thinking": thinking,
            "status": status_val,
            "heal_attempts": heal_attempts,
            "cid": cid,
            "rid": rid,
            "rcid": rcid,
            "gates": gates_result,
            "unified_diff": unified_diff,
            "candidate_files": candidate_files,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rejection_reason": None,
        }
        self._state["runs"][run_id] = run_record
        if schedule_id and schedule_id in self._state["schedules"]:
            srec = self._state["schedules"][schedule_id]
            srec["last_run_ts"] = time.time()
            srec["next_run_ts"] = time.time() + int(srec.get("interval_seconds", 3600))
            srec["last_run_id"] = run_id
        self._save_state()
        return dict(run_record)


    def list_runs(self) -> list[dict[str, Any]]:
        """REQ-PIPE-05: Lists all pipeline runs."""
        return [dict(r) for r in self._state["runs"].values()]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rec = self._state["runs"].get(run_id)
        return dict(rec) if rec else None

    def approve_run(self, run_id: str) -> dict[str, Any]:
        """
        REQ-PIPE-05: Applies staged candidate_files to workspace_dir on disk only upon explicit human approval.
        """
        if run_id not in self._state["runs"]:
            raise KeyError(f"Run '{run_id}' not found")
        rec = self._state["runs"][run_id]
        root = Path(rec["workspace_dir"]).resolve()
        for rel_path, code in rec.get("candidate_files", {}).items():
            dest = root / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(code, encoding="utf-8")
        rec["status"] = "APPROVED_APPLIED"
        rec["approved_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return dict(rec)

    def reject_run(self, run_id: str, reason: str = "") -> dict[str, Any]:
        """
        REQ-PIPE-05: Marks a staged run as REJECTED and leaves workspace_dir completely untouched.
        """
        if run_id not in self._state["runs"]:
            raise KeyError(f"Run '{run_id}' not found")
        rec = self._state["runs"][run_id]
        rec["status"] = "REJECTED"
        rec["rejection_reason"] = reason
        rec["rejected_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return dict(rec)

    async def sync_cloud_scheduled_actions(self, client: Any = None) -> dict[str, Any]:
        """
        REQ-PIPE-02, REQ-PIPE-06: Harvests and reconciles Google Gemini Scheduled Action conversations
        via MaZiqc / hNvQHb when an authenticated GeminiWebClient is supplied.
        """
        cloud_items: list[dict[str, Any]] = list(self._state.get("cloud_actions", []))
        if client is not None and hasattr(client, "list_remote_conversations"):
            try:
                remote = await client.list_remote_conversations(limit=30)
                harvested: list[dict[str, Any]] = []
                for item in remote or []:
                    cid = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else "")
                    title = getattr(item, "title", None) or (item.get("title") if isinstance(item, dict) else "")
                    harvested.append(
                        {
                            "cid": str(cid),
                            "title": str(title),
                            "source": "MaZiqc",
                            "synced_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                if harvested:
                    cloud_items = harvested
                    self._state["cloud_actions"] = cloud_items
                    self._save_state()
            except Exception as exc:
                logger.debug("Cloud scheduled actions sync fallback: %s", exc)

        return {
            "ok": True,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "cloud_actions": cloud_items,
            "local_schedule_count": len(self._state["schedules"]),
        }


_DEFAULT_ORCHESTRATOR: TaskPipelineOrchestrator | None = None


def get_task_pipeline_orchestrator() -> TaskPipelineOrchestrator:
    """Returns singleton TaskPipelineOrchestrator instance."""
    global _DEFAULT_ORCHESTRATOR
    if _DEFAULT_ORCHESTRATOR is None:
        _DEFAULT_ORCHESTRATOR = TaskPipelineOrchestrator()
    return _DEFAULT_ORCHESTRATOR
