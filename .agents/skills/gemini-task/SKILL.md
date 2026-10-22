---
name: gemini-task
description: >
  Direct execution bridge to Google Gemini Task Mode (Agency / Plan Steps container)
  powered by Gemini 4 Argon with High Reasoning (thinking=2) via local daemon.
  Supports full context archive ingestion (-c/--context), automated output material extraction
  (-x/--extract-dir), and output packaging (-p/--pack-output).
  Use when asked for "/gemini-task", "/task-plan", "gemini task mode", "task mode",
  "plan via argon", "gemini 4 argon", "gemini task", "break into stages via gemini",
  "upload context to gemini", "gemini context archive", or when complex multi-step reasoning
  and code generation over full codebases is needed via Ultra account.
---

# Gemini Task Mode (Gemini 4 Argon High Reasoning)

Native bridge to Google Gemini's **Task Mode** (`featureMode = 40`, Agency container) on **Gemini 4 Argon** (`95a4c0abcba24364`) with deep reasoning thoughts (`thinking=2`), routed through the local authenticated daemon on `http://127.0.0.1:8000`.

## Key Capabilities

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
# 1. Send task with a full codebase context directory
python .agents/skills/gemini-task/scripts/gemini_task.py \
  "Inspect the project structure and implement the authentication module" \
  -c ./my_project \
  -x ./my_project \
  -p ./my_project_updated.zip \
  -o ./task_plan.md

# 2. Planning with context from existing zip archive
python .agents/skills/gemini-task/scripts/gemini_task.py \
  "Analyze security vulnerabilities in the attached codebase" \
  -c project_backup.zip \
  -o security_audit.md

# 3. Direct task request (no archive)
python .agents/skills/gemini-task/scripts/gemini_task.py \
  "Draft an architectural database migration plan" \
  -o migration_plan.md

# 4. Continue existing session (multi-turn task execution)
python .agents/skills/gemini-task/scripts/gemini_task.py \
  "Now implement auth.py based on the plan above" \
  -s conv_4b9a8f21 \
  -x ./my_project

# 5. Check daemon connection and active Ultra account
python .agents/skills/gemini-task/scripts/gemini_task.py --status
```

---

## CLI Parameters Reference

| Flag | Long Flag | Description | Default |
|------|-----------|-------------|---------|
| `prompt` | - | The instruction or task prompt to execute | Positional |
| `-f` | `--file` | Path to markdown/text file containing prompt | None |
| `-c` | `--context` | **Path to project directory or .zip archive to upload as context** | None |
| `-s` | `--session` | **Conversation / session ID to continue multi-turn Task execution** | None (new session) |
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
    "prompt": "Task description",
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
