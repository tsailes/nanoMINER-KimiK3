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

## Local full-PDF screening (no Kimi call)

Run the deterministic first-pass screen before K3 extraction:

```powershell
.\.venv\Scripts\python -m nanominer_k3 screen `
  "C:\Users\CAI\Desktop\pdfs" `
  --output-dir "C:\Users\CAI\Desktop\PE_Crystal_Silver_v0_1_calibration\annotations\source_groups\library_screening\20260830_fulltext" `
  --ocr-sparse-pages `
  --copy-partitions
```

This command reads every page locally. Tesseract is used only for sparse,
image-only, or broken-font pages; no Kimi/API configuration is read. It keeps
a paper only when the body provides primary-work evidence for parseable atomic
coordinates, or for both a valid
space group and a numeric a/b/c unit cell. Generic terms such as `crystal
structure`, crystal system, XRD/WAXS, (hkl), and orientation do not pass by
themselves. Reviews, imported simulation inputs, compound theses, reference-only
hits, and damaged/unverified OCR are rejected or held for review.

With `--copy-partitions`, originals stay at the top level of `pdfs`; classified
copies go to `pdfs\通过` and `pdfs\未通过`. `needs_review` is deliberately placed
in `未通过` and remains distinguishable in `screening_manifest.jsonl`. Detailed
CSV/JSONL evidence and Chinese lists are written to the output directory, not
mixed into the source-PDF library. The screen is evidence-based and does not
silently impose a material-name filter.

See `docs/FULLTEXT_SCREENING.md` for the decision matrix, OCR quality gates,
resume semantics, calibration controls, and output schema.

After page-level review of every `needs_review` item, apply the completed JSONL
review files without changing the deterministic baseline manifest:

```powershell
.\.venv\Scripts\python -m nanominer_k3 review-apply `
  --manifest "C:\path\to\screening_manifest.jsonl" `
  --reviews "C:\path\to\review_part_1.jsonl" "C:\path\to\review_part_2.jsonl" `
  --output-dir "C:\path\to\reviewed" `
  --copy-partitions --pdf-dir "C:\path\to\pdfs"
```

This command accepts overrides only for baseline `needs_review` records,
requires a complete unique review set, and writes a separate reviewed manifest,
Chinese report/CSV/lists, and auditable page evidence before refreshing the two
copy-only folders.

The strict gate above answers whether a paper already contains directly
extractable primary crystallographic data. It is intentionally narrower than a
high-recall literature-library screen. To retain every paper that explicitly
discusses crystal structure or unit-cell information, first build local
full-text evidence drafts and then apply completed structure-relevance reviews:

```powershell
.\.venv\Scripts\python scripts\build_structure_review_drafts.py `
  --manifest "C:\path\to\screening_manifest.reviewed.jsonl" `
  --pdf-dir "C:\path\to\pdfs" `
  --output "C:\path\to\broad_evidence_index.jsonl" `
  --ocr-sparse-pages

.\.venv\Scripts\python -m nanominer_k3 structure-review-apply `
  --manifest "C:\path\to\screening_manifest.reviewed.jsonl" `
  --reviews "C:\path\to\review_part_1.jsonl" "C:\path\to\review_part_2.jsonl" `
  --output-dir "C:\path\to\structure_reviewed" `
  --copy-partitions --pdf-dir "C:\path\to\pdfs"
```

This second layer reviews every strict `exclude`. It accepts explicit partial
cell data, crystal-system or phase assignments, structure models, and
structure-bearing secondary/review content, while recording ownership and
information level separately. Generic crystallinity, crystallization kinetics,
DSC, XRD/WAXS measurements, morphology, or orientation without a material-bound
crystallographic fact still do not pass. The output preserves the original
strict result in `strict_final_decision` and uses `topic_decision` for the
broader library partition. Exclusions are indexed by case-insensitive relative
path order, every review carries the source PDF SHA-256, and the apply command
rejects stale evidence or a pre-existing output directory. Final reports are
published from a staging directory only after validation; two-folder updates
preflight every target and roll back committed moves if an operation fails.

Use the resulting allow-list for a later K3 batch:

```powershell
.\.venv\Scripts\python -m nanominer_k3 batch "C:\Users\CAI\Desktop\pdfs" `
  --screening-manifest "C:\path\to\reviewed\screening_manifest.reviewed.jsonl" `
  --profile pe_crystal `
  --output-dir "C:\path\to\staging-run"
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

## Build a literature-derived draft CIF (no Kimi call)

After a curator has transcribed a unit cell, a coordinate-compatible space-group
setting, and at least one independent atom site into a JSON build spec, generate
and validate a draft CIF locally:

```powershell
.\.venv\Scripts\python -m nanominer_k3 cif-build `
  "C:\path\to\structure-build-spec.json" `
  --output-dir "C:\path\to\annotations\source_groups\run\cif"
```

The command refuses to write into a `gold` directory. It verifies the source
PDF SHA-256 when a local path is present, resolves the build-setting space group
with Gemmi, expands its symmetry operations, checks expected multiplicities and
composition (including occupancy-weighted composition when supplied), rejects
overlapping atoms, checks optional bond-distance expectations, and parses the
generated CIF again. It writes both `*.draft.cif` and a machine-readable
`*.validation.json` report.

The CIF records both the symbol printed in the paper and the setting actually
used to build the coordinates. A passing report means the draft is internally
consistent and reproducible; it does not turn a hydrogen-free, disorder-limited,
or setting-interpreted historical model into a deposition-ready structure.
See `docs/CIF_BUILD_SPEC.md` for the input contract and occupancy-aware example.

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

K3 extraction output is a provenance-first staging object, not a PE database
row. The model does not generate record IDs, normalize crystal settings,
declare CIF readiness, or promote records. The local `cif-build` adapter handles
only curator-reviewed build specs and keeps every interpretation explicit; main
annotation-table promotion remains a separate human decision.

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
