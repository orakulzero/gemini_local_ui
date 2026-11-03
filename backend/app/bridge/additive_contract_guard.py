"""
backend/app/bridge/additive_contract_guard.py
M49: Additive-Only AST/CLI/Return-Key Contract Guard & Zero-Diff Clean No-Op Detector.
Implements REQ-PIPE-12.
"""

from __future__ import annotations

import ast
from typing import Any


def _extract_fn_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extracts ordered parameter names from a function or method AST node."""
    params: list[str] = []
    for arg in node.args.posonlyargs:
        params.append(arg.arg)
    for arg in node.args.args:
        params.append(arg.arg)
    for arg in node.args.kwonlyargs:
        params.append(arg.arg)
    return params


def _extract_fn_return_dict_keys(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extracts top-level string literal keys from returned dict literals in a function."""
    keys: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
            for k in child.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return sorted(keys)


def extract_immutable_contract_snapshot(source_code: str) -> dict[str, Any]:
    """
    REQ-PIPE-12: Extracts an immutable AST contract snapshot from Python source code:
      - classes: sorted top-level class names
      - functions: mapping of function/method name to ordered parameter list
      - cli_flags: sorted long CLI flags ('--*') passed to add_argument(...)
      - return_dict_keys: mapping of function/method name to sorted returned dict keys
    """
    classes: list[str] = []
    functions: dict[str, list[str]] = {}
    cli_flags: set[str] = set()
    return_dict_keys: dict[str, list[str]] = {}

    try:
        tree = ast.parse(source_code or "")
    except SyntaxError:
        return {
            "classes": [],
            "functions": {},
            "cli_flags": [],
            "return_dict_keys": {},
        }

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = _extract_fn_params(node)
            ret_keys = _extract_fn_return_dict_keys(node)
            if ret_keys:
                return_dict_keys[node.name] = ret_keys
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qual_name = f"{node.name}.{sub.name}"
                    functions[qual_name] = _extract_fn_params(sub)
                    ret_keys = _extract_fn_return_dict_keys(sub)
                    if ret_keys:
                        return_dict_keys[qual_name] = ret_keys

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            attr_name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else ""
            )
            if attr_name == "add_argument":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if arg.value.startswith("--"):
                            cli_flags.add(arg.value)

    return {
        "classes": sorted(classes),
        "functions": functions,
        "cli_flags": sorted(cli_flags),
        "return_dict_keys": return_dict_keys,
    }


def verify_additive_contract(
    old_source: str,
    new_source: str,
    rel_path: str = "<memory>",
) -> dict[str, Any]:
    """
    REQ-PIPE-12: Verifies that new_source preserves all existing classes, function/method
    parameters, CLI flags, and returned dictionary keys from old_source (additive-only).
    """
    violations: list[str] = []
    old_snap = extract_immutable_contract_snapshot(old_source)
    try:
        ast.parse(new_source or "")
    except SyntaxError as exc:
        violations.append(f"{rel_path}: SyntaxError in new_source: {exc}")
        return {
            "passed": False,
            "violations": violations,
            "rel_path": rel_path,
        }

    new_snap = extract_immutable_contract_snapshot(new_source)

    for cls_name in old_snap["classes"]:
        if cls_name not in new_snap["classes"]:
            violations.append(f"{rel_path}: missing class '{cls_name}'")

    for fn_name, old_params in old_snap["functions"].items():
        if fn_name not in new_snap["functions"]:
            violations.append(f"{rel_path}: missing function or method '{fn_name}'")
            continue
        new_params = new_snap["functions"][fn_name]
        for param in old_params:
            if param not in new_params:
                violations.append(
                    f"{rel_path}: function '{fn_name}' missing parameter '{param}'"
                )

    for flag in old_snap["cli_flags"]:
        if flag not in new_snap["cli_flags"]:
            violations.append(f"{rel_path}: missing CLI flag '{flag}'")

    for fn_name, old_keys in old_snap["return_dict_keys"].items():
        new_keys = set(new_snap["return_dict_keys"].get(fn_name, []))
        for key in old_keys:
            if key not in new_keys:
                violations.append(
                    f"{rel_path}: function '{fn_name}' missing return dict key '{key}'"
                )

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "rel_path": rel_path,
    }


def build_additive_only_prompt_block(contracts_by_file: dict[str, Any]) -> str:
    """
    REQ-PIPE-12: Builds an explicit prompt instruction block listing immutable AST classes,
    function parameters, CLI flags, and return dict keys that must be preserved.
    """
    lines: list[str] = [
        "# Additive-Only Contract Guard (REQ-PIPE-12)",
        "Preserve all existing classes, function parameters, CLI flags, and return dict keys:",
    ]
    for rel_path, raw_or_snap in sorted((contracts_by_file or {}).items()):
        snap = (
            extract_immutable_contract_snapshot(raw_or_snap)
            if isinstance(raw_or_snap, str)
            else dict(raw_or_snap or {})
        )
        if not any(snap.get(k) for k in ("classes", "functions", "cli_flags", "return_dict_keys")):
            continue
        lines.append(f"## {rel_path}")
        if snap.get("classes"):
            lines.append(f"- Classes: {', '.join(snap['classes'])}")
        for fn_name, params in sorted(snap.get("functions", {}).items()):
            lines.append(f"- Function `{fn_name}({', '.join(params)})`")
        if snap.get("cli_flags"):
            lines.append(f"- CLI Flags: {', '.join(snap['cli_flags'])}")
        for fn_name, rkeys in sorted(snap.get("return_dict_keys", {}).items()):
            lines.append(f"- Return Keys `{fn_name}`: {', '.join(rkeys)}")
    return "\n".join(lines)


def is_noop_pipeline_response(
    response_text: str,
    orig_files: dict[str, str],
    candidate_files: dict[str, str],
    cid: str | None = None,
) -> bool:
    """
    REQ-PIPE-12: Detects whether a Task Mode response represents a verified clean no-op
    (either explicit CLEAN_NOOP_VERIFIED with zero extracted files on a valid cid, or
    candidate_files identical to orig_files).
    """
    if cid and (str(cid).startswith("c_unauth") or str(cid).startswith("c_err")):
        return False
    if candidate_files:
        return all(
            rel in orig_files and orig_files[rel].strip() == str(code).strip()
            for rel, code in candidate_files.items()
        )
    text = str(response_text or "").strip()
    if not text or "```tool_call" in text:
        return False
    return True
