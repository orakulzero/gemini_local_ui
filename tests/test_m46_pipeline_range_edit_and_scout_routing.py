"""Locked Agile V (P30) Verification Suite for Milestone M46 Pipeline RangeEdit & Scout Routing.

Requirement Traceability:
- REQ-PIPE-11: AST-Safe RangeEdit Boundary Snapping, Default Line-Numbered Target Selection,
  Scout Schedule Sweep Routing, and Sandbox Git Staging (`task_pipeline.py`).

Hygiene: 100% English-only (0 Cyrillic characters).
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.bridge.task_pipeline import (  # noqa: E402
    TaskPipelineOrchestrator,
    extract_code_files_from_response,
    select_default_target_files_with_line_numbers,
    snap_range_edit_to_ast_boundary,
    verify_five_quality_gates,
)

SAMPLE_MULTILINE_MODULE = (
    "import math\n"
    "\n"
    "CONST_VAL = 10\n"
    "\n"
    "def alpha(\n"
    "    a: int,\n"
    "    b: int,\n"
    "    c: int,\n"
    ") -> int:\n"
    '    """Compute sum."""\n'
    "    total = a + b + c\n"
    "    return total\n"
    "\n"
    "def beta(x: int) -> int:\n"
    "    return x * 2\n"
)


def test_select_default_target_files_with_line_numbers() -> None:
    """REQ-PIPE-11: Verify select_default_target_files_with_line_numbers prioritizes <=3 non-test .py files with 1-indexed line numbers."""
    orig_files = {
        "tests/test_x.py": "def test_x():\n    assert True\n",
        "README.md": "# Documentation\n",
        "src/mod_a.py": "def func_a() -> int:\n    return 1\n",
        "src/mod_b.py": "def func_b() -> int:\n    return 2\n",
        "src/mod_c.py": "def func_c() -> int:\n    return 3\n",
        "src/mod_d.py": "def func_d() -> int:\n    return 4\n",
    }
    selected = select_default_target_files_with_line_numbers(orig_files, max_files=3)
    if isinstance(selected, dict):
        assert 1 <= len(selected) <= 3
        assert "tests/test_x.py" not in selected
        assert "README.md" not in selected
        assert all(k.endswith(".py") and not k.startswith("tests/") for k in selected)
        assert all("1: " in v for v in selected.values())
    else:
        items = list(selected)
        assert 1 <= len(items) <= 3
        joined = "\n".join(str(x) for x in items)
        assert "tests/test_x.py" not in joined
        assert "1: " in joined


def test_snap_range_edit_to_ast_boundary_prevents_mid_function_cut() -> None:
    """REQ-PIPE-11: Verify snap_range_edit_to_ast_boundary expands partial function line ranges (5..6) to full AST symbol boundary (5..12)."""
    snapped = snap_range_edit_to_ast_boundary(
        SAMPLE_MULTILINE_MODULE, start_line=5, end_line=6
    )
    assert tuple(snapped) == (5, 12)


def test_extract_code_files_from_response_ast_safe_range_edit() -> None:
    """REQ-PIPE-11: Verify extract_code_files_from_response snaps RangeEdit to AST boundary and preserves trailing functions without SyntaxError."""
    response_text = (
        "### RangeEdit: src/mod.py:5-6\n"
        "```python\n"
        "def alpha(a, b, c):\n"
        "    return a + b + c\n"
        "```\n"
    )
    extracted = extract_code_files_from_response(
        response_text, orig_files={"src/mod.py": SAMPLE_MULTILINE_MODULE}
    )
    assert "src/mod.py" in extracted
    patched_code = extracted["src/mod.py"]
    tree = ast.parse(patched_code)
    func_names = [
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert "alpha" in func_names
    assert "beta" in func_names
    assert "return a + b + c" in patched_code


def test_run_schedule_now_routes_scout_schedule_to_intelligence_sweep(
    tmp_path: Path,
) -> None:
    """REQ-PIPE-11: Verify run_schedule_now routes Ultra5x-Ecosystem-Intelligence-Scout-6h to EcosystemIntelligenceScout sweep."""
    orch = TaskPipelineOrchestrator(storage_path=tmp_path / "pipe.json")
    sched = orch.create_schedule(
        name="Ultra5x-Ecosystem-Intelligence-Scout-6h",
        workspace_dir=str(tmp_path),
        prompt="Execute recurring 6-hour ecosystem intelligence sweep.",
    )
    run_rec = asyncio.run(orch.run_schedule_now(sched["schedule_id"]))
    assert run_rec["status"] == "SCOUT_SWEEP_COMPLETED"
    assert isinstance(run_rec.get("sweep_id"), str) and len(run_rec["sweep_id"]) > 0


def test_verify_five_quality_gates_stages_git_files_in_sandbox(
    tmp_path: Path,
) -> None:
    """REQ-PIPE-11: Verify verify_five_quality_gates runs git add -A in sandbox when .git exists so git status tests pass."""
    repo_dir = tmp_path / "sandbox_git_repo"
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "tests").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    test_git_clean = (
        "import subprocess\n\n"
        "def test_git_working_tree_has_no_untracked_files() -> None:\n"
        '    proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True)\n'
        "    untracked = [ln for ln in proc.stdout.splitlines() if ln.startswith('??')]\n"
        "    assert untracked == [], f'Untracked files in sandbox: {untracked}'\n"
    )
    (repo_dir / "tests" / "test_git_clean.py").write_text(test_git_clean, encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=str(repo_dir), check=True, timeout=15)
    subprocess.run(
        ["git", "config", "user.email", "test@antigravity.org"],
        cwd=str(repo_dir),
        check=True,
        timeout=15,
    )
    subprocess.run(
        ["git", "config", "user.name", "Agile V Tester"],
        cwd=str(repo_dir),
        check=True,
        timeout=15,
    )
    subprocess.run(["git", "add", "-A"], cwd=str(repo_dir), check=True, timeout=15)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit", "-q"],
        cwd=str(repo_dir),
        check=True,
        timeout=15,
    )

    res = verify_five_quality_gates(
        workspace_dir=repo_dir,
        orig_files={"src/a.py": "x = 1\n"},
        candidate_files={"src/a.py": "x = 2\n", "src/new_mod.py": "y = 3\n"},
        run_pytest=True,
    )
    assert res["gates"]["gate1_pytest"] is True
    assert res["passed"] is True


def test_req_pipe_11_traceability_and_english_hygiene() -> None:
    """REQ-PIPE-11: Verify task_pipeline.py contains REQ-PIPE-11 and 0 Cyrillic characters."""
    target_file = REPO_ROOT / "backend" / "app" / "bridge" / "task_pipeline.py"
    assert target_file.is_file()
    content = target_file.read_text(encoding="utf-8")
    assert "REQ-PIPE-11" in content
    for ch in content:
        assert not (0x0400 <= ord(ch) <= 0x04FF), f"Cyrillic char found: {ch!r}"
