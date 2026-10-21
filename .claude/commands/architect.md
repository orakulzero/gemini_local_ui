---
name: architect
description: Design a self-contained EARS architecture blueprint using the hybrid Architect pipeline (code-explorer on Flash + code-architect on Argon Task Mode + NotebookLM Mind Map).
argument-hint: "[feature or system to architect]"
---

# `/architect` — Hybrid Architecture & Blueprint Generator

Execute the full **Architect** workflow for: **$ARGUMENTS**

1. **Load Skill**: Invoke the `Skill` tool with `skill: "architect"`.
2. **Phase 1 — Grounded Discovery (`Gemini 3 Flash`)**:
   - Query NotebookLM (`mcp__gemini-local__notebooklm_query`, `notebook_id="nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f"`) for relevant existing specs.
   - Spawn `code-explorer` via `Agent` (`subagent_type="code-explorer"`) to map entry points and `file:line` boundaries.
3. **Phase 2 — Blueprint Synthesis (`Gemini 4 Argon Task Mode`)**:
   - Spawn `code-architect` via `Agent` (`subagent_type="code-architect"`) or call `mcp__gemini-local__gemini_argon_task` to design the decisive architecture with EARS acceptance criteria (`WHEN ... THE SYSTEM SHALL ...` + `Verify:` command).
   - Write the complete blueprint to `./blueprints/<slug>-blueprint.md`.
4. **Phase 3 — Validation & Grounding**:
   - Ingest the blueprint into NotebookLM (`mcp__gemini-local__notebooklm_add_source`) and generate a Mermaid Mind Map (`mcp__gemini-local__notebooklm_generate_mind_map`).
   - Present the 6-line Architecture Card, EARS build checklist, and blueprint path to the user.
