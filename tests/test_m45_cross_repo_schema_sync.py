"""Locked Agile V (P30) Verification Suite for Milestone M45 Cross-Repo Schema Synchronizer.

Requirement Traceability:
- REQ-SYNC-SCHEMA-01: Cross-Repository ABI, Pydantic, TypedDict, and JSON Schema
  Boundary Synchronizer (`cross_repo_schema_sync.py`)

Hygiene: 100% English-only (0 Cyrillic characters).
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.bridge.cross_repo_schema_sync import (  # noqa: E402
    CrossRepoSchemaSynchronizer,
)


def _field(obj: Any, name: str) -> Any:
    """Extract field from dict or dataclass/object transparently."""
    return obj[name] if isinstance(obj, dict) else getattr(obj, name)


def _contains_field_token(items: Any, token: str) -> bool:
    """Return True if token appears as string, suffix, or dict field in items."""
    for item in items:
        if isinstance(item, str) and (item == token or item.endswith(f".{token}") or token in item):
            return True
        if isinstance(item, dict) and (
            item.get("field") == token
            or item.get("field_name") == token
            or str(item.get("field", "")).endswith(f".{token}")
        ):
            return True
    return False


def test_extract_contract_schema_from_pydantic_and_typeddict() -> None:
    """REQ-SYNC-SCHEMA-01: Verify extract_contract_schema parses BaseModel and TypedDict classes into deterministic SHA-256 schema."""
    sync = CrossRepoSchemaSynchronizer()
    source_code = (
        "from typing import Optional, TypedDict\n"
        "from pydantic import BaseModel\n\n"
        "class PayloadModel(BaseModel):\n"
        "    task_id: str\n"
        "    priority: int\n"
        "    tag: Optional[str] = None\n\n"
        "class ConfigDict(TypedDict):\n"
        "    endpoint: str\n"
        "    timeout_ms: int\n"
    )
    schema = sync.extract_contract_schema(source_code, repo_name="gemini_local_ui")
    assert _field(schema, "repo_name") == "gemini_local_ui"
    models = _field(schema, "models")
    assert isinstance(models, dict)
    assert "PayloadModel" in models and "ConfigDict" in models
    schema_hash = _field(schema, "schema_hash")
    assert isinstance(schema_hash, str) and len(schema_hash) == 64
    assert all(c in "0123456789abcdefABCDEF" for c in schema_hash)


def test_verify_boundary_compatibility_matching_schemas() -> None:
    """REQ-SYNC-SCHEMA-01: Verify compatible producer/consumer schemas return compatible=True and drift_score=0.0."""
    sync = CrossRepoSchemaSynchronizer()
    producer_src = (
        "from pydantic import BaseModel\n\n"
        "class PayloadModel(BaseModel):\n"
        "    task_id: str\n"
        "    count: int\n"
        "    extra_trace: str = 'ok'\n"
    )
    consumer_src = (
        "from pydantic import BaseModel\n\n"
        "class PayloadModel(BaseModel):\n"
        "    task_id: str\n"
        "    count: int\n"
    )
    prod_schema = sync.extract_contract_schema(producer_src, repo_name="producer_repo")
    cons_schema = sync.extract_contract_schema(consumer_src, repo_name="consumer_repo")
    res = sync.verify_boundary_compatibility(prod_schema, cons_schema)

    assert _field(res, "compatible") is True
    assert _field(res, "missing_fields") == []
    assert _field(res, "type_mismatches") == []
    assert float(_field(res, "drift_score")) == 0.0


def test_verify_boundary_compatibility_detects_missing_fields() -> None:
    """REQ-SYNC-SCHEMA-01: Verify missing consumer-required field auth_token is flagged with drift_score > 0.0."""
    sync = CrossRepoSchemaSynchronizer()
    producer_src = "from pydantic import BaseModel\nclass PayloadModel(BaseModel):\n    task_id: str\n"
    consumer_src = (
        "from pydantic import BaseModel\n"
        "class PayloadModel(BaseModel):\n"
        "    task_id: str\n"
        "    auth_token: str\n"
    )
    prod_schema = sync.extract_contract_schema(producer_src, repo_name="producer_repo")
    cons_schema = sync.extract_contract_schema(consumer_src, repo_name="consumer_repo")
    res = sync.verify_boundary_compatibility(prod_schema, cons_schema)

    assert _field(res, "compatible") is False
    assert _contains_field_token(_field(res, "missing_fields"), "auth_token")
    assert float(_field(res, "drift_score")) > 0.0


def test_verify_boundary_compatibility_detects_type_mismatch() -> None:
    """REQ-SYNC-SCHEMA-01: Verify producer count: str vs consumer count: int is flagged in type_mismatches."""
    sync = CrossRepoSchemaSynchronizer()
    producer_src = "from pydantic import BaseModel\nclass PayloadModel(BaseModel):\n    task_id: str\n    count: str\n"
    consumer_src = "from pydantic import BaseModel\nclass PayloadModel(BaseModel):\n    task_id: str\n    count: int\n"
    prod_schema = sync.extract_contract_schema(producer_src, repo_name="producer_repo")
    cons_schema = sync.extract_contract_schema(consumer_src, repo_name="consumer_repo")
    res = sync.verify_boundary_compatibility(prod_schema, cons_schema)

    assert _field(res, "compatible") is False
    assert _contains_field_token(_field(res, "type_mismatches"), "count")


def test_generate_sync_migration_plan_for_drifted_boundary() -> None:
    """REQ-SYNC-SCHEMA-01: Verify generate_sync_migration_plan emits concrete add_field and align_type actions."""
    sync = CrossRepoSchemaSynchronizer()
    producer_src = "from pydantic import BaseModel\nclass PayloadModel(BaseModel):\n    count: str\n"
    consumer_src = "from pydantic import BaseModel\nclass PayloadModel(BaseModel):\n    count: int\n    auth_token: str\n"
    prod_schema = sync.extract_contract_schema(producer_src, repo_name="producer_repo")
    cons_schema = sync.extract_contract_schema(consumer_src, repo_name="consumer_repo")
    plan = sync.generate_sync_migration_plan(prod_schema, cons_schema)

    actions = _field(plan, "actions") if isinstance(plan, dict) or hasattr(plan, "actions") else plan
    assert isinstance(actions, list) and len(actions) >= 2
    action_types = {
        (a.get("action") or a.get("type") if isinstance(a, dict) else getattr(a, "action", None))
        for a in actions
    }
    assert "add_field" in action_types and "align_type" in action_types


def test_end_to_end_multi_repo_source_pair_audit() -> None:
    """REQ-SYNC-SCHEMA-01: Verify audit_repo_pair_sources returns full compatibility and migration report."""
    sync = CrossRepoSchemaSynchronizer()
    producer_code = "from pydantic import BaseModel\nclass SyncEnvelope(BaseModel):\n    session_id: str\n    epoch: int\n"
    consumer_code = "from pydantic import BaseModel\nclass SyncEnvelope(BaseModel):\n    session_id: str\n    epoch: int\n"
    report = sync.audit_repo_pair_sources(
        producer_code,
        consumer_code,
        producer_repo="gemini_local_ui",
        consumer_repo="agy-golden-stack",
    )
    assert _field(report, "producer_repo") == "gemini_local_ui"
    assert _field(report, "consumer_repo") == "agy-golden-stack"
    assert _field(report, "compatible") is True
    assert float(_field(report, "drift_score")) == 0.0
