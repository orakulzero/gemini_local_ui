---
name: architect-audit
description: Perform a brownfield architectural audit, 0-100 health scoring, anti-pattern detection, and EARS remediation blueprint using code-explorer, code-architect, and code-reviewer.
argument-hint: "[target module or directory to audit]"
---

# `/architect-audit` — Brownfield Architecture & Anti-Pattern Audit

Perform a rigorous architectural audit of **$ARGUMENTS** (or the current repository if omitted):

1. **Phase 1 — Structural Reconnaissance (`code-explorer` → Gemini 3 Flash)**:
   - Dispatch `code-explorer` via `Agent` (`subagent_type="code-explorer"`) to trace module boundaries, coupling, state management, and error handling across the target scope.
2. **Phase 2 — Deep Architectural Evaluation (`code-architect` → Gemini 4 Argon Task Mode)**:
   - Dispatch `code-architect` (`subagent_type="code-architect"`) or invoke `mcp__gemini-local__gemini_argon_task` to evaluate boundary violations, cyclic dependencies, concurrency bottlenecks, and security surfaces.
3. **Phase 3 — Confidence-Scored Review (`code-reviewer` → Gemini 4 Argon)**:
   - Dispatch `code-reviewer` (`subagent_type="code-reviewer"`) to score the architecture on a **0–100 scale**, list findings with `Confidence >= 80` (`file:line` + root cause + concrete fix), and emit an EARS remediation checklist.
4. **Phase 4 — Sync to NotebookLM**:
   - Record the audit summary in NotebookLM via `mcp__gemini-local__notebooklm_add_source`.
