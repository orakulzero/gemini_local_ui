---
name: git-oss-release-audit
description: Auto-distilled SOP skill via Trace2Skill / Voyager from verified W3C PROV-DM execution trace.
---

# Distilled SOP: git-oss-release-audit

- **Provenance Nodes Distilled**: 0
- **Active ExpeL Rules Embedded**: 0

## Standard Operating Procedure
1. Freeze baseline contract (`.supergraph/frozen_baseline.yaml`).
2. Design isolated verification tests (`TEST_DESIGN`) prior to synthesis.
3. Synthesize implementation within `artifact_scope` boundaries.
4. Verify via deterministic oracle and seal `SHA-256 Audit Chain`.
