# nanoMINER corrected reproduction: Kimi K3 core

This branch reproduces the useful nanoMINER workflow while correcting the parts
that prevent reliable scientific extraction. It is based on upstream commit
`94dbab2` and runs the coordinating agent and visual analyst on `kimi-k3`.

The active implementation is the `nanominer_k3` package under `src/`.
Upstream notebooks plus `llm-extraction/`, `graph_processing/`, and
`data_preproccessing/` remain as non-installed comparison artifacts; they are
not part of the corrected runtime. The two documented upstream entry points
now call the K3 implementation.

## What changed

- Replaced the legacy LangChain text-ReAct loop with native Kimi K3 tool calls.
- Preserves the complete assistant message across tool turns, as K3 requires.
- Omits fixed sampling parameters that K3 rejects.
- Separates evidence collection from strict JSON-schema finalization.
- Preserves real 1-based PDF page numbers and never truncates the References
  section.
- Uses Kimi K3 native vision for tables, figures, and scan-only pages.
- Forces all generated records to `needs_review` in program code.
- Does not log API keys or model reasoning.
- Emits metadata-only progress events during long K3/tool calls.
- Validates cited roles/pages and flags numeric/OCR disagreements for review.
- Keeps PE-crystal output out of `gold/` and the main annotation tables.

Two extraction profiles are included:

- `pe_crystal` for this project's polyethylene crystal literature workflow.
- `nanozyme` for a corrected reproduction of the upstream case study.

## Install

Python 3.10 through 3.13 is supported.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
$env:KIMI_API_KEY = "your-key"
```

The core model is intentionally pinned to `kimi-k3`. Configuration variables
are documented in `.env.example`; the program does not automatically read a
local `.env` file.

The default service is the China Open Platform that matches keys created at
<https://platform.kimi.com/console/projects/api-keys>:

- Base URL: `https://api.moonshot.cn/v1`
- Model ID: `kimi-k3`

Global Open Platform accounts may explicitly set `KIMI_BASE_URL` to
`https://api.moonshot.ai/v1`. Kimi Code membership keys are a separate product;
they require `https://api.kimi.com/coding/v1` and model ID `k3`.
For credential safety, the runtime rejects non-official API base URLs.

Run the offline checks:

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m nanominer_k3 doctor
```

Optional Streamlit app:

```powershell
.\.venv\Scripts\python -m pip install -e ".[app]"
.\.venv\Scripts\python -m streamlit run agent_app.py
```

## Extract one PE-crystal paper

Candidate output must go to the source-group staging area, never directly to
`annotations/*.jsonl`, `annotations/gold/`, or an external `gold/gN` PDF folder.

From this repository:

```powershell
.\.venv\Scripts\python -m nanominer_k3 extract `
  "C:\path\to\article.pdf" `
  --profile pe_crystal `
  --output "..\..\annotations\source_groups\gN\nanominer_kimi_k3\runs\RUN_ID\candidate_extraction.json"
```

Add `--supplement C:\path\to\supplement.pdf` when needed. Add `--no-vision`
only for a text-only diagnostic run; scan-only PDFs require vision.
Use `--prompt-key` to enter a key through a hidden terminal prompt instead of
placing it in an environment variable or command-line argument.

For a directory pilot, use a separate fresh K3 conversation per article:

```powershell
.\.venv\Scripts\python -m nanominer_k3 batch "C:\path\to\articles" `
  --supplements-dir "C:\path\to\supplements" `
  --profile pe_crystal `
  --limit 4 `
  --output-dir "..\..\annotations\source_groups\gN\nanominer_kimi_k3\runs\RUN_ID"
```

`doctor` reports key presence without printing the key. Live contract tests are
opt-in and always read credentials from a hidden prompt.

## Scientific curation boundary

The output is a provenance-first staging object, not a PE database row. The
model does not generate record IDs, normalize crystal settings, declare CIF
readiness, or promote records. A separate deterministic adapter and validation
step must map reviewed candidates into the project's schemas.

Recommended first regression set:

1. BUNN1939: positive crystallographic record.
2. SHEARER1954: must remain `needs_review`.
3. SMITH1980: negative/non-target boundary.
4. One image-only g3 PDF: OCR/vision stress case.

See `docs/REPRODUCTION_AND_CORRECTIONS.md` for the audit and next phases.

## Upstream and licensing note

Upstream: <https://github.com/ai-chem/nanoMINER>

The upstream README says MIT, but commit `94dbab2` does not contain a LICENSE
file. Confirm redistribution terms before publishing a fork or packaged build.
