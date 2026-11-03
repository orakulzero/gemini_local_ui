"""Cross-Repository ABI, Pydantic, TypedDict, and JSON Schema Boundary Synchronizer.

Requirement Traceability:
- REQ-SYNC-SCHEMA-01: Cross-Repository ABI, Pydantic, TypedDict, and JSON Schema
  Boundary Synchronizer (`cross_repo_schema_sync.py`).
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any


class _DictAccessMixin:
    """Mixin enabling both attribute and dictionary-style field lookup."""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[arg-type]


@dataclass
class ContractSchema(_DictAccessMixin):
    """Extracted repository contract schema with deterministic SHA-256 digest (REQ-SYNC-SCHEMA-01)."""

    repo_name: str
    models: dict[str, dict[str, dict[str, Any]]]
    schema_hash: str


@dataclass
class BoundaryCompatibilityResult(_DictAccessMixin):
    """Producer/consumer boundary compatibility evaluation result (REQ-SYNC-SCHEMA-01)."""

    producer_repo: str
    consumer_repo: str
    compatible: bool
    missing_fields: list[str] = field(default_factory=list)
    type_mismatches: list[dict[str, Any]] = field(default_factory=list)
    drift_score: float = 0.0


@dataclass
class SyncMigrationPlan(_DictAccessMixin):
    """Actionable schema synchronization plan across drifted repository boundaries (REQ-SYNC-SCHEMA-01)."""

    producer_repo: str
    consumer_repo: str
    compatible: bool
    drift_score: float
    actions: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    type_mismatches: list[dict[str, Any]] = field(default_factory=list)


class CrossRepoSchemaSynchronizer:
    """AST-based cross-repository Pydantic and TypedDict schema boundary synchronizer (REQ-SYNC-SCHEMA-01)."""

    CONTRACT_BASES = {"BaseModel", "TypedDict", "Schema", "Contract"}

    @staticmethod
    def _base_name(expr: ast.expr) -> str:
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            return expr.attr
        if isinstance(expr, ast.Subscript):
            return CrossRepoSchemaSynchronizer._base_name(expr.value)
        return ast.unparse(expr)

    def extract_contract_schema(
        self, source_code: str, repo_name: str = "default_repo"
    ) -> ContractSchema:
        """Parse BaseModel and TypedDict classes from Python source into a deterministic schema (REQ-SYNC-SCHEMA-01)."""
        tree = ast.parse(source_code)
        models: dict[str, dict[str, dict[str, Any]]] = {}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {self._base_name(b) for b in node.bases}
            if bases and not (bases & self.CONTRACT_BASES) and len(node.bases) == 0:
                continue
            fields_map: dict[str, dict[str, Any]] = {}
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fname = stmt.target.id
                    if fname.startswith("_"):
                        continue
                    ftype = ast.unparse(stmt.annotation).strip()
                    has_default = stmt.value is not None
                    is_optional = ftype.startswith("Optional[") or "| None" in ftype
                    fields_map[fname] = {"type": ftype, "required": not (has_default or is_optional)}
            models[node.name] = fields_map

        canonical = json.dumps(models, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return ContractSchema(repo_name=repo_name, models=models, schema_hash=digest)

    def verify_boundary_compatibility(
        self, producer_schema: Any, consumer_schema: Any
    ) -> BoundaryCompatibilityResult:
        """Verify producer schema satisfies all consumer schema field and type contracts (REQ-SYNC-SCHEMA-01)."""
        prod_repo = producer_schema["repo_name"] if isinstance(producer_schema, dict) else producer_schema.repo_name
        cons_repo = consumer_schema["repo_name"] if isinstance(consumer_schema, dict) else consumer_schema.repo_name
        prod_models = producer_schema["models"] if isinstance(producer_schema, dict) else producer_schema.models
        cons_models = consumer_schema["models"] if isinstance(consumer_schema, dict) else consumer_schema.models

        missing_fields: list[str] = []
        type_mismatches: list[dict[str, Any]] = []
        total_checked = 0
        for model_name, cons_fields in cons_models.items():
            prod_fields = prod_models.get(model_name, {})
            for fname, fmeta in cons_fields.items():
                total_checked += 1
                if fname not in prod_fields:
                    if fmeta.get("required", True):
                        missing_fields.append(f"{model_name}.{fname}")
                elif prod_fields[fname]["type"] != fmeta["type"]:
                    type_mismatches.append(
                        {
                            "model": model_name,
                            "field": fname,
                            "field_name": fname,
                            "producer_type": prod_fields[fname]["type"],
                            "consumer_type": fmeta["type"],
                        }
                    )

        issues_count = len(missing_fields) + len(type_mismatches)
        drift_score = 0.0 if issues_count == 0 else round(min(1.0, issues_count / max(1, total_checked)), 4)
        return BoundaryCompatibilityResult(
            prod_repo, cons_repo, issues_count == 0, missing_fields, type_mismatches, drift_score
        )

    def generate_sync_migration_plan(
        self, producer_schema: Any, consumer_schema: Any
    ) -> SyncMigrationPlan:
        """Generate concrete add_field and align_type migration actions for drifted schemas (REQ-SYNC-SCHEMA-01)."""
        compat = self.verify_boundary_compatibility(producer_schema, consumer_schema)
        cons_models = consumer_schema["models"] if isinstance(consumer_schema, dict) else consumer_schema.models
        actions: list[dict[str, Any]] = []
        for missing in compat.missing_fields:
            model_name, fname = missing.split(".", 1) if "." in missing else ("Model", missing)
            expected_type = cons_models.get(model_name, {}).get(fname, {}).get("type", "Any")
            actions.append(
                {"action": "add_field", "type": "add_field", "model": model_name, "field": fname, "target_type": expected_type}
            )
        for mismatch in compat.type_mismatches:
            actions.append({"action": "align_type", "type": "align_type", **mismatch})
        return SyncMigrationPlan(
            compat.producer_repo, compat.consumer_repo, compat.compatible, compat.drift_score, actions, compat.missing_fields, compat.type_mismatches
        )

    def audit_repo_pair_sources(
        self, producer_code: str, consumer_code: str, producer_repo: str = "producer_repo", consumer_repo: str = "consumer_repo"
    ) -> SyncMigrationPlan:
        """Audit raw Python source strings across a producer/consumer repository boundary (REQ-SYNC-SCHEMA-01)."""
        prod_schema = self.extract_contract_schema(producer_code, repo_name=producer_repo)
        cons_schema = self.extract_contract_schema(consumer_code, repo_name=consumer_repo)
        return self.generate_sync_migration_plan(prod_schema, cons_schema)
