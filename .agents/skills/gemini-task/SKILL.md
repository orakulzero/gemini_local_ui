---
name: gemini-task
description: >
  Direct execution bridge to Google Gemini Task Mode and 2-Phase Deep Research (Agency / "Этапы плана" container)
  powered by Gemini 4 Argon with High Reasoning (thinking=2) via local daemon.
  Supports 2-turn autonomous Deep Research (-dr / --deep-research, --plan-only, --approve-prompt) with grounded
  citation synthesis, full context archive ingestion (-c/--context), automated output material extraction
  (-x/--extract-dir), and output packaging (-p/--pack-output).
  Use when asked for "/gemini-task", "/task-plan", "deep research", "глубокое исследование", "задача в gemini",
  "task mode", "план через argon", "gemini 4 argon", "gemini task", "разбей на этапы через gemini",
  "загрузить контекст в gemini", "архив контекста gemini", or when deep web research or multi-step reasoning
  over full codebases is needed via Ultra account.
---

# Gemini Task Mode & Deep Research (Gemini 4 Argon High Reasoning)

Native bridge to Google Gemini's **Task Mode & 2-Phase Deep Research** (`featureMode = 40`, Agency container) on **Gemini 4 Argon** (`95a4c0abcba24364`) with deep reasoning thoughts (`thinking=2`), routed through the local authenticated daemon on `http://127.0.0.1:8000`.

## Key Capabilities

0. **Autonomous 2-Phase Deep Research (`-dr`, `--deep-research`, `--mode deep-research`)**:
   - **Phase 1 (Research Plan)**: Sends the research topic to `gemini-4-argon` with `featureMode = 40` (`inner[49] = 40`), extracts the structured multi-step research plan from `candidate[12]["56"]`, and captures the session identifiers (`cid`, `rid`, `rcid`).
   - **Phase 2 (Auto-Approve & Web Synthesis)**: Automatically submits the plan approval in Turn 2 within the exact same Google session (`cid / rid / rcid`), streams live multi-source search progress from `candidate[12]["57"]` & `"58"`, unpacks the full Immersive Markdown report from `candidate[30]` (`immersive_entry_chip`), and appends deduplicated **Grounded Sources & Citations** (`candidate[12]["43"]` & `"16"/"23"/"27"`).
   - Supports `--plan-only` (stops after Phase 1 to inspect the plan) and `--approve-prompt "..."` (custom steering instructions for Phase 2).

1. **Native Context Archive Ingestion (`-c`, `--context`)**:
   - Accepts a directory path or existing `.zip`/`.tar.gz` archive.
   - If a directory is passed, automatically prunes noise (`.git`, `node_modules`, `__pycache__`, `.venv`, binaries) and bundles project files into a clean zip archive.
   - Uploads archive via `/api/chat/upload` to Google `content-push` and attaches as a native Task context attachment.
   - Gemini 4 Argon unpacks and analyzes the archive files directly in its Task container.

2. **Automated Output Materials Extraction (`-x`, `--extract-dir`)**:
   - Parses code blocks and file declarations (`file:path`, `### File: path`, ````path/file.ext````) from the model's response.
   - Creates directory structure and safely writes all generated files into the specified directory.

3. **Output Packaging (`-p`, `--pack-output`)**:
   - Packages all extracted code files and artifacts into a ready-to-deploy `.zip` archive.

4. **Markdown Response & Thinking Trace (`-o`, `--output`)**:
   - Saves full structured response, reasoning steps, and audit trails to markdown.

---

## Quick Start & CLI Examples

```bash
# 1. Send task with a full codebase context directory into isolated staging
python -u "%USERPROFILE%\.gemini\config\skills\gemini-task\scripts\gemini_task.py" \
  "Изучи структуру проекта и добавь модуль аутентификации" \
  -c ./my_project \
  -x .argon_staging/latest \
  -p ./my_project_updated.zip \
  -o ./task_plan.md

# 2. Planning with context from existing zip archive
python -u "%USERPROFILE%\.gemini\config\skills\gemini-task\scripts\gemini_task.py" \
  "Проанализируй уязвимости в прикреплённом коде" \
  -c project_backup.zip \
  -o security_audit.md

# 3. Direct task request (no archive)
python -u "%USERPROFILE%\.gemini\config\skills\gemini-task\scripts\gemini_task.py" \
  "Составь архитектурный план миграции БД" \
  -o migration_plan.md

# 4. Run 2-Phase Autonomous Deep Research with Gemini 4 Argon (Plan -> Web Synthesis + Citations)
python -u "%USERPROFILE%\.gemini\config\skills\gemini-task\scripts\gemini_task.py" \
  "Сравни архитектуры MoE-оффлоадинга (SGLang, vLLM, ktransformers) в 2026 году" \
  --deep-research \
  -o ./moe_deep_research_report.md

# 5. Deep Research combined with local codebase archive (-c)
python -u "%USERPROFILE%\.gemini\config\skills\gemini-task\scripts\gemini_task.py" \
  "Найди свежие SOTA-методы защиты MCP-серверов и сопоставь с нашим кодом" \
  -dr \
  -c ./src \
  -o ./mcp_security_research.md

# 6. Check daemon connection and active Ultra account
python -u "%USERPROFILE%\.gemini\config\skills\gemini-task\scripts\gemini_task.py" --status
```

### Antigravity Asynchronous Execution Pattern (Watchdog Guard)

Because deep reasoning on Gemini 4 Argon (`thinking=2`) typically spans 180–300 seconds, synchronous execution inside an agent turn can trigger the 240s Cloud Code SSE stream disconnection. Always dispatch long tasks as detached background jobs with an armed Dead-Man watchdog:

```python
# Launch background command
run_command(
    CommandLine='python -u "%USERPROFILE%\\.gemini\\config\\skills\\gemini-task\\scripts\\gemini_task.py" "..." -c ./src -x .argon_staging/task_01 -o plan.md',
    WaitMsBeforeAsync=500,
    toolSummary="Argon offload task"
)

# Arm watchdog in the same turn
schedule(
    DurationSeconds=60,
    TimerCondition="<conv-id>/<task-id>",
    Prompt="Watchdog: inspect task status; re-arm if streaming"
)
```

---

## CLI Parameters Reference

| Flag | Long Flag | Description | Default |
|------|-----------|-------------|---------|
| `prompt` | - | The instruction or task prompt to execute | Positional |
| `-f` | `--file` | Path to markdown/text file containing prompt | None |
| `-c` | `--context` | **Path to project directory or .zip archive to upload as context** | None |
| `-x` | `--extract-dir` | **Target directory to extract generated files into** | None |
| `-p` | `--pack-output` | **Path to output .zip archive of generated materials** | None |
| `-o` | `--output` | Destination path to write generated markdown plan | None (stdout) |
| `-m` | `--model` | Target model name or internal hex ID | `gemini-4-argon` (`95a4c0abcba24364`) |
| `-t` | `--thinking` | Reasoning budget: `1` (Low), `4` (Medium), `2` (High) | `2` |
| `--mode` | - | Envelope mode: `task` (featureMode=40) or `chat` | `task` |
| `-a` | `--account` | Specific account ID override | Auto-resolved to active Ultra/Pro account |
| `--no-thoughts` | - | Hide raw reasoning thoughts stream from console | False |
| `--json` | - | Output full JSON envelope with metadata | False |
| `--status` | - | Check daemon status and registered accounts | - |
| `--base-url` | - | Backend daemon URL | `http://127.0.0.1:8000` |

---

## REST Endpoints for Upload & Context

The local daemon exposes standard endpoints for programmatic file uploads:

### 1. Upload File / Archive
- **URL**: `POST http://127.0.0.1:8000/api/chat/upload` (or `POST /v1/files`)
- **Headers**: `x-gemini-account: acc_1164ad7c` (Optional)
- **Form-Data**: `file=@project.zip`
- **Response**:
  ```json
  {
    "uri": "/contrib_service/ttl_1d/...",
    "filename": "project.zip",
    "mime_type": "application/zip",
    "file_size": 1048576
  }
  ```

### 2. Generate Turn with Context
- **URL**: `POST http://127.0.0.1:8000/api/chat/generate`
- **Payload**:
  ```json
  {
    "prompt": "Текст задачи",
    "model": "gemini-4-task",
    "feature_mode": "task",
    "attachments": [
      {
        "uri": "/contrib_service/ttl_1d/...",
        "filename": "project.zip"
      }
    ]
  }
  ```

---

## Rules for Pair Programming Agents

1. **Context First**: When He provides a project codebase or asks to work on an existing repository in Gemini Task Mode, pack and upload the context using `-c <path>`.
2. **Materials Management**: When Gemini produces implementation code, use `-x <dir>` to automatically write the files to disk, and `-p <path.zip>` if He asks for an archive of the results.
3. **Keep Context Compact**: Write large plans directly to target files via `-o <path>` to preserve conversational token budget.
