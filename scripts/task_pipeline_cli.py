"""
scripts/task_pipeline_cli.py
Standalone CLI for M30: Autonomous Gemini 4 Argon Task Mode Pipeline & Scheduled Orchestrator.
Supports REQ-PIPE-01 through REQ-PIPE-06.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.bridge.task_pipeline import (
    get_task_pipeline_orchestrator,
    pack_workspace_context,
)


def main(argv: list[str] | None = None) -> int:
    """REQ-PIPE-06: CLI entry point for Task-Mode pipeline management."""
    parser = argparse.ArgumentParser(description="Gemini 4 Argon Task-Mode Pipeline CLI (M30)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pack = sub.add_parser("pack", help="Pack workspace context and extract AST/REQ contracts")
    p_pack.add_argument("--workspace", default=".", help="Target workspace directory")

    sub.add_parser("schedules", help="List configured Task-Mode schedules")
    sub.add_parser("runs", help="List Task-Mode pipeline runs")

    p_diff = sub.add_parser("diff", help="Show unified diff for a pipeline run")
    p_diff.add_argument("run_id", help="Target run_id")

    p_approve = sub.add_parser("approve", help="Approve and apply a staged pipeline run")
    p_approve.add_argument("run_id", help="Target run_id")

    p_reject = sub.add_parser("reject", help="Reject a staged pipeline run")
    p_reject.add_argument("run_id", help="Target run_id")
    p_reject.add_argument("--reason", default="CLI rejection", help="Rejection reason")

    p_exec = sub.add_parser("execute", help="Execute a Task-Mode pipeline run")
    p_exec.add_argument("--workspace", default=".", help="Target workspace directory")
    p_exec.add_argument("--prompt", required=True, help="Task instruction prompt")
    p_exec.add_argument("--no-pytest", action="store_true", help="Skip Gate 1 pytest execution")

    args = parser.parse_args(argv)
    orch = get_task_pipeline_orchestrator()

    if args.cmd == "pack":
        bundle = pack_workspace_context(args.workspace)
        summary = {
            "zip_filename": bundle["zip_filename"],
            "size_bytes": bundle["size_bytes"],
            "file_count": bundle["file_count"],
            "req_tags": bundle["req_tags"],
            "ast_contract_files": list(bundle["ast_contracts"].keys()),
        }
        print(json.dumps(summary, indent=2), flush=True)
        return 0

    if args.cmd == "schedules":
        print(json.dumps(orch.list_schedules(), indent=2), flush=True)
        return 0

    if args.cmd == "runs":
        runs = [
            {k: v for k, v in r.items() if k != "candidate_files"}
            for r in orch.list_runs()
        ]
        print(json.dumps(runs, indent=2), flush=True)
        return 0

    if args.cmd == "diff":
        run = orch.get_run(args.run_id)
        if not run:
            print(f"Run '{args.run_id}' not found", file=sys.stderr, flush=True)
            return 1
        print(run.get("unified_diff", ""), flush=True)
        return 0

    if args.cmd == "approve":
        res = orch.approve_run(args.run_id)
        print(json.dumps({"run_id": res["run_id"], "status": res["status"]}, indent=2), flush=True)
        return 0

    if args.cmd == "reject":
        res = orch.reject_run(args.run_id, reason=args.reason)
        print(json.dumps({"run_id": res["run_id"], "status": res["status"]}, indent=2), flush=True)
        return 0

    if args.cmd == "execute":
        res = asyncio.run(
            orch.execute_pipeline_run(
                workspace_dir=args.workspace,
                prompt=args.prompt,
                run_pytest=not args.no_pytest,
            )
        )
        print(json.dumps({k: v for k, v in res.items() if k != "candidate_files"}, indent=2), flush=True)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
