# OCR Router

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![CLI](https://img.shields.io/badge/Interface-CLI-111111)
![Sanitize%20Gate](https://img.shields.io/badge/Sanitize%20Gate-Enabled-2EA043)
![Status](https://img.shields.io/badge/Status-Production%20Ready-22863A)

OCR Router is a Python CLI that processes PDF documents in batches, extracts metadata, classifies document types, and routes files into structured folders.

It is configuration-driven: categories, issuers, naming, and folder routing are defined in YAML so each user can tailor behavior without changing source code.

## About (Verbose)

OCR Router is designed for high-volume document organization where PDFs arrive mixed (bank statements, credit cards, bills, tax forms, notices, receipts, paystubs). The pipeline performs OCR when needed, extracts key fields (date, amount, account hints, issuer), classifies by keyword scoring, proposes standardized filenames, and routes files into deterministic folder structures.

The tool emphasizes safety and reviewability:

- Interactive review before moves/renames
- Dry-run mode for zero-risk previews
- CSV/JSONL manifests for traceability
- Sanitization checks to prevent accidental personal-data publication

## Features

- Interactive review before file operations
- OCR support for scanned/image-only PDFs via PDF24
- JPEG/JPG ingestion with conversion to searchable PDF (OCR applied)
- Metadata extraction (date, amount, issuer, account)
- Rule-based classification and routing templates
- Deterministic filename normalization
- Density-aware folder resolver
- Manifest output in CSV and JSONL
- Privacy guardrails for public repositories
- **Optional local LLM second-opinion classifier** (Ollama, no cloud, no API keys) — see Section 6
- **Feedback log + embedding store** — pipeline learns from your past corrections
- **Eval mode** — measure classifier accuracy against your organized tree

---

## 1. Installation

### Prerequisites

- Python 3.10+
- Windows PowerShell
- Optional OCR engine: PDF24 Creator (recommended)
- Optional local LLM stack: Ollama (recommended — see Section 6)

Install PDF24 (optional but recommended):

| Option | Command / Link |
|---|---|
| Winget | `winget install SoftwareAG.PDF24Creator` |
| Chocolatey | `choco install pdf24creator` |
| Download | https://tools.pdf24.org/en/creator |

### One-line install (recommended for end users)

If you just want to **use** the tool, install it as a standalone CLI with [pipx](https://pipx.pypa.io/):

```powershell
# One-time setup (install pipx)
python -m pip install --user pipx
python -m pipx ensurepath

# Install ocr-router from GitHub (or a local clone)
pipx install git+https://github.com/oscarzamora/ocr-docs.git

# Verify
ocr-router --help
```

This puts `ocr-router` on your PATH so you can run it from any terminal in any folder.
To upgrade later: `pipx upgrade ocr-router`. To uninstall cleanly: `pipx uninstall ocr-router`.

### Clone and install (for development / customizing)

```powershell
git clone https://github.com/oscarzamora/ocr-docs.git
cd ocr-docs

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .[dev]
```

### Create your local private config

```powershell
# Inside the cloned repo:
Copy-Item config/routing-config.yaml config/routing-config.local.yaml
# Or anywhere outside the repo if installed via pipx:
ocr-router --help    # shows OCR_CONFIG_PATH env var usage
```

Edit your local config with your own categories, issuers, and route templates.

---

## 2. How To Use

Main command (interactive mode):

```powershell
python -m ocr_router.cli process `
  --input  "C:\path\to\input" `
  --output "C:\path\to\output" `
  --config config/routing-config.local.yaml
```

Interactive flow (PDF + JPEG inputs):

1. Analyze all input files
2. Convert JPEG/JPG files to searchable PDFs through OCR preprocessing
3. Show proposal table (category, issuer, filename, route)
4. Ask move vs rename-in-place
5. Ask file selection (all, include-list, skip-list)
6. Execute and write history/manifest

---

## 3. Two Ways to Use the LLM (TL;DR)

The pipeline is **deterministic by default** (keyword scoring). The local LLM (`llama3.2:3b`
via Ollama) is an **opt-in second opinion**. There are two distinct ways to use it:

| Mode | What you run | Where the LLM helps | When to pick this |
|---|---|---|---|
| **(A) Standalone CLI with `--llm`** | `ocr-router process ... --llm` in any terminal | Classifies each doc, parses your free-form replies at the confirm prompt | You like the terminal, want a single command, scripting, cron jobs |
| **(B) `@OCR Router` agent in VS Code Chat** | Type `@OCR Router process ...` in Copilot chat | Same as above PLUS the agent translates natural-language goals into the right CLI commands and confirms each step in chat | You want conversational HITL, you're already in VS Code, multi-step asks |

Both modes share the **exact same pipeline, same feedback log, same embedding store**. Mode B is a
thin chat wrapper over Mode A — nothing magical, just a friendlier surface.

Sections 6 (standalone) and 7 (agent mode) below give the full setup and examples for each.

---

## 4. Git: About, Tags, and Release Flow

### Verbose About text (for GitHub repo "About")

Use this in GitHub repository description/about field:

`OCR Router is a privacy-aware Python CLI for OCR, metadata extraction, deterministic document classification, smart naming, and folder routing with review-first workflows and manifest traceability.`

Suggested Topics (GitHub tags):

- `python`
- `ocr`
- `pdf`
- `document-management`
- `cli`
- `automation`
- `privacy`
- `yaml-config`

### Version tags (annotated)

```powershell
git tag -a v0.2.0 -m "v0.2.0: README overhaul, sanitize gate, release documentation"
git push origin v0.2.0
```

List tags:

```powershell
git tag --list
```

Show tag details:

```powershell
git show v0.2.0
```

---

## 5. Run Examples

### Example A: Interactive processing

```powershell
python -m ocr_router.cli process `
  --input  "C:\Docs\Incoming" `
  --output "C:\Docs\Sorted" `
  --config config/routing-config.local.yaml
```

### Example B: Dry-run (no file changes)

```powershell
python -m ocr_router.cli process `
  --input "C:\Docs\Incoming" `
  --output "C:\Docs\Sorted" `
  --config config/routing-config.local.yaml `
  --dry-run
```

### Example C: Batch mode (non-interactive)

```powershell
python -m ocr_router.cli process `
  --input "C:\Docs\Incoming" `
  --output "C:\Docs\Sorted" `
  --config config/routing-config.local.yaml `
  --no-interactive
```

### Example D: Review existing manifest

```powershell
python -m ocr_router.cli review --manifest "C:\Docs\Sorted\manifest.jsonl"
```

### Example E: Validate privacy before push

```powershell
python scripts/sanitize_check.py
```

### Example F: Process with LLM second opinion (after Section 6 setup)

```powershell
python -m ocr_router process `
  --input  "C:\Users\<user>\Documents\__downloads__" `
  --output "C:\Users\<user>\Documents" `
  --config config/routing-config.local.yaml `
  --llm
```

### Example G: Measure accuracy (eval mode, read-only)

```powershell
python -m ocr_router eval `
  --root "C:\Users\<user>\Documents" `
  --sample 200 --llm
```

---

## 6. Local LLM + Feedback Loop (Optional)

OCR Router ships with an opt-in **local-first LLM stack** that runs entirely on your machine.
**No cloud, no API keys, no document data leaves your computer.**

With it enabled the pipeline:

1. Runs the keyword router (default behavior — fully deterministic).
2. Asks `llama3.2:3b` via [Ollama](https://ollama.com/) for a second opinion, with the
   `k` most-similar past confirmed decisions injected as few-shot exemplars.
3. Applies a simple decision rule: agreement → confident, disagreement → flag for HITL,
   low LLM confidence → keep keyword + show hint.
4. **At the confirm prompt, parses your free-form English** ("skip 2 because I haven't paid")
   into structured actions (`park_some [2]`, note "haven't paid") with a transparent
   `Understood: …` recap.
5. Logs every decision (and every correction you make) to a JSONL feedback log so the
   classifier learns from your taxonomy over time.

### One-time setup

```powershell
# 1. Install Ollama (https://ollama.com), then pull the two models
ollama pull llama3.2:3b           # ~2 GB — chat model
ollama pull nomic-embed-text       # ~270 MB — embeddings for few-shot

# 2. Enable LLM in your local config
#    Add to config/routing-config.local.yaml:
#       llm:
#         enabled: true
#         confidence_threshold: 0.6
#         fewshot_k: 5

# 3. (One time) Bootstrap the feedback log from your existing organized tree
python -m ocr_router feedback bootstrap-tree --root "C:\Users\<user>\Documents"

# 4. (One time) Embed all bootstrapped records into the local SQLite vector store
python -m ocr_router feedback embed

# 5. Verify the stack is healthy
python -m ocr_router llm doctor
```

### Daily workflow (standalone CLI)

```powershell
# Always dry-run first — see what would happen, nothing moves yet
python -m ocr_router process `
  --input  "C:\Users\<user>\Documents\__downloads__" `
  --output "C:\Users\<user>\Documents" `
  --config config\routing-config.local.yaml `
  --llm --dry-run

# When the proposal table looks right, run for real (no --dry-run)
python -m ocr_router process `
  --input  "C:\Users\<user>\Documents\__downloads__" `
  --output "C:\Users\<user>\Documents" `
  --config config\routing-config.local.yaml `
  --llm
```

At the confirm prompt you can type either deterministic syntax OR natural language:

```text
# Deterministic (always works, no LLM required):
Enter             move ALL files
1,3,5             move ONLY those numbers
skip 2,4          move all EXCEPT those numbers
park 7            keep those files in place permanently (never re-propose)
park 7 note: <r>  same as park, capture the reason verbatim
q                 quit without moving anything

# Natural language (requires --llm; uses LLM to parse intent):
skip 2 because I haven't paid yet              → park 2 + note (per unpaid convention)
park the FPL one, it's a duplicate             → asks for the file number if ambiguous
move 1 3 5, the others are for Luciana         → moves 1,3,5; skipped get rule prompt
4 is actually FPL not AT&T                     → adds issuer rule to local YAML
nevermind / cancel                             → quit
```

### Inspect what the pipeline has learned

```powershell
python -m ocr_router feedback stats                # counts by event/category/backend
python -m ocr_router feedback show --limit 20      # most recent records (with Note column)
python -m ocr_router feedback search "AMEX credit card statement"
python -m ocr_router feedback parked list          # files marked "keep in place"
python -m ocr_router eval --root "C:\Users\<user>\Documents" --sample 200 --llm
```

### How the data layers fit together

| Layer | Default location | Purpose | Built by |
|---|---|---|---|
| Feedback log | `data/_feedback/corrections.jsonl` (project-local) | Audit trail of every classify / skip / park / correction | `process`, `feedback bootstrap*` |
| Embedding store | `data/_feedback/examples.sqlite` (project-local) | Vector index of past confirmed decisions | `feedback embed` |
| Eval audit log | `data/_feedback/eval-<ts>.jsonl` (project-local) | Per-file accuracy record from one eval run | `eval` |

All three live **inside the project folder** (in `data/_feedback/`, which is
gitignored). They never touch the Documents tree you point `--output` at —
the Documents tree holds only your filed PDFs.

Override locations (when you want them elsewhere):
- Env vars: `OCR_FEEDBACK_DIR`, `OCR_FEEDBACK_LOG`, `OCR_EMBEDDINGS_DB`
- Config keys: `feedback.path`, `feedback.embeddings_db`

### Privacy

- Document text **never leaves your machine** — Ollama runs locally; the codebase has no
  cloud fallback by design.
- The feedback log stores a configurable text excerpt per record (default 2000 chars).
  It is gitignored.
- The embedding store contains those same excerpts plus their 768-dim vectors — same
  privacy posture as the log, same gitignore.
- The sanitize gate (`scripts/sanitize_check.py`) blocks any commit that contains
  personal names, real Windows user paths, or OneDrive references. Same gate runs in CI.

### Rollback

Three independent ways to disable the LLM stack:

```powershell
# Per-run override (keeps config as-is)
python -m ocr_router process ... --no-llm

# Disable in config
#   llm:
#     enabled: false

# Full revert to pre-L4 keyword-only baseline (preserved as an annotated git tag)
git checkout pre-l4-baseline
```

---

## 7. Agent Mode (`@OCR Router` in VS Code Copilot Chat)

Same pipeline as Section 6, but driven through chat instead of a terminal. The repo
ships with a workspace agent definition at `.github/agents/ocr-router.agent.md`.

### Why agent mode (vs the standalone CLI)

| Capability | Standalone CLI (`--llm`) | `@OCR Router` agent mode |
|---|---|---|
| Same pipeline, same feedback log, same SQLite store | ✓ | ✓ |
| Local LLM (`llama3.2:3b` via Ollama) | ✓ | ✓ |
| Natural-language confirm replies | ✓ (intent parser) | ✓ (intent parser + chat agent translates the broader request) |
| Multi-step asks (`scan and run an eval after, show me parked`) | ❌ separate commands | ✓ agent stitches them |
| Renders proposal tables as clean Markdown in chat | ❌ terminal box-drawing | ✓ |
| Per-session memory of input/output folders | ❌ | ✓ (asks once, remembers) |
| Honors the **routing conventions** in the agent playbook (owner-namespaced files, unpaid-statements stay parked, tax forms → Tax Returns) | partial (config only) | ✓ (agent applies the conventions even when keyword/LLM disagree) |
| Works from any chat client (Cursor, Claude Desktop, Windsurf, …) | n/a | only VS Code Copilot today; MCP server is the natural next step |

Use **CLI** for scripts, cron jobs, terminal-only workflows. Use **agent mode** for
conversational HITL and when you want the agent to apply conventions that don't fit
neatly into the YAML config.

### Setup (assumes Section 6 is already done)

1. Open this workspace in VS Code with the GitHub Copilot extension installed.
2. Restart the chat window once so the agent gets picked up.
3. In the **chat input mode picker** (bottom-left of the chat panel), pick **Agent**.
4. Click the agent dropdown → **OCR Router**.

### Use it

```text
You:         Process my downloads with LLM

@OCR Router: Which folder should I scan and which folder is your organized documents root?

You:         C:\Users\me\Documents\__downloads__ → C:\Users\me\Documents

@OCR Router: [runs `process --llm --dry-run`, posts a Markdown table of proposals
              with the Backend column — agree ✓ / LLM ✱ / kw / llm err]

You:         park 2 because I haven't paid, the rest go

@OCR Router: Understood: park #2 — "I haven't paid", move the rest.
             [runs without --dry-run, applies the selection, writes feedback log
              with the note attached, appends Notes block to PROCESSED_PDFS.md]
             Moved 3, parked 1, skipped 0. ✓
```

The agent reuses the same `ocr-router` CLI under the hood, so every move is
logged to `corrections.jsonl` and feeds future runs through the embedding store.
**Your personalization stays local** — the agent never edits the playbook file
or sends data anywhere outside your machine.

### Make it available in every workspace

Copy the agent file once to your VS Code user profile so `@OCR Router` works in
any project you open:

```powershell
# VS Code user prompts folder (Windows)
$dst = "$env:APPDATA\Code\User\prompts"
New-Item -ItemType Directory -Path $dst -Force | Out-Null
Copy-Item .github\agents\ocr-router.agent.md $dst\
```

After the copy, the agent is discoverable everywhere — even workspaces that don't
contain this repo. Make sure `ocr-router` is on your PATH (install via `pipx` as
shown in Section 1).

### What the agent does NOT do

- **No silent moves** — every run starts with `--dry-run` and waits for your confirmation
- **No cloud calls** — the pipeline is local-only, by design (no cloud backend exists)
- **No code edits** — the agent only invokes the CLI, never writes Python
- **No bypass of `park`** — files you parked stay parked until you `unpark` them
- **Never modifies its own playbook** (`.github/agents/ocr-router.agent.md`) — that file
  is static. Learnings go to `corrections.jsonl`, `routing-config.local.yaml`, or
  `PROCESSED_PDFS.md`, all of which are gitignored / your own
- **No personal info in the repo** — the sanitize gate blocks any commit that contains
  names or real Windows user paths; same gate runs in CI

---

## Naming Convention

| Document type | Format |
|---|---|
| Monthly statement | `YYYY.MM - Issuer DocType.pdf` |
| Monthly with account | `YYYY.MM - Issuer DocType - (Last4 XXXX) - $Amount.pdf` |
| Dated transaction / receipt | `YYYY.MM.DD - Issuer DocType - $Amount.pdf` |
| Paystub | `YYYY.MM.DD - Issuer Paycheck - $NetPay.pdf` |
| Reference / policy form | `YYYY - Issuer DocType.pdf` |

Missing metadata fields are omitted instead of using placeholder tokens.

---

## Project Structure

```text
ocr-docs/
  src/ocr_router/
    cli.py
    ocr_engine.py
    extractor.py
    router.py
    folder_resolver.py
    manifest.py
    config.py
    feedback/                  # L1-L3: feedback log, bootstrap, embeddings
      log.py
      bootstrap.py
      store.py
    llm/                       # L4: local LLM classifier
      schema.py
      backends.py
      prompts.py
      classifier.py
    eval/                      # L6: accuracy harness
      runner.py
  config/
    routing-config.yaml        # tracked default template (no PII)
    routing-config.local.yaml  # local-only, gitignored
  scripts/
    dry_run.py
    sanitize_check.py
  tests/
```

---

## Environment Variables

Create `.env` locally (never commit):

```dotenv
PDF24_PATH=C:\Program Files\PDF24\pdf24-Ocr.exe
OCR_CONFIG_PATH=config/routing-config.local.yaml
LOG_LEVEL=INFO
DEBUG=false
```

---

## Public vs Private Data

1. Keep generic templates in git:
   - `config/routing-config.yaml`
   - `.env.example`
2. Keep personal files local and ignored:
   - `config/routing-config.local.yaml`
   - `.env`
   - manifests and local logs
3. Run the sanitization gate before push:

```powershell
python scripts/sanitize_check.py
```

The same check runs in GitHub Actions on pull requests and pushes.
