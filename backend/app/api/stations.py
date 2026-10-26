"""
backend/app/api/stations.py
Re-exports router and components from backend.routes.stations.
"""

from backend.routes.stations import (
    STATIONS_V1_3_MATRIX,
    build_merkle_audit_tree,
    compute_rfc6962_leaf_hash,
    compute_rfc6962_node_hash,
    generate_merkle_inclusion_proof,
    legacy_router,
    router,
    verify_merkle_inclusion_proof,
)

__all__ = [
    "STATIONS_V1_3_MATRIX",
    "build_merkle_audit_tree",
    "compute_rfc6962_leaf_hash",
    "compute_rfc6962_node_hash",
    "generate_merkle_inclusion_proof",
    "legacy_router",
    "router",
    "verify_merkle_inclusion_proof",
]
