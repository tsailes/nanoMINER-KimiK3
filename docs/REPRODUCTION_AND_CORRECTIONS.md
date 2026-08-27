# Reproduction baseline and correction decisions

## Baseline

- Upstream repository: `https://github.com/ai-chem/nanoMINER`
- Upstream branch: `main`
- Frozen starting commit: `94dbab2d543779abf4906ea349ded65aae4856e0`
- Local correction branch: `repro/kimi-k3-core-agent`

There are no upstream tags or releases. The upstream `vision-agent` branch has
12 commits not merged into `main`; this reproduction deliberately targets the
default `main` commit above rather than silently mixing branches.

The parent PE-crystal folder is not a Git repository. The nanoMINER clone is an
independent nested repository so its history and branch do not mix with
literature data.

## Why a literal rerun is not a valid reproduction

The upstream code has no tests or included example/model artifacts and pins an
old, GPU-specific dependency set. Its README clones a different repository
name. The main code hard-codes GPT-4o and legacy LangChain text ReAct, writes
free-form Markdown, and later relies on notebooks for structure.

Correctness issues found during audit:

1. `python = "3.10.12"` is an exact patch pin, so Poetry refuses installation
   on Python 3.12; after relaxing it, the build still fails because the declared
   `nanozyme` package directory does not exist.
2. The lock installs the unrelated `fitz==0.0.1.dev2` package alongside
   PyMuPDF, reproducing an `import fitz` failure. The corrected package depends
   only on PyMuPDF.
3. The locked OpenAI client is too old for APIs used by the executed notebooks,
   showing that the notebook results came from an unrecorded environment.
4. Both main entry points log the complete OpenAI API key.
5. `temperature=0` is incompatible with Kimi K3's fixed sampling contract.
6. Legacy ReAct scratchpads cannot guarantee preservation of K3's complete
   assistant message and `reasoning_content` across tool turns.
7. PDF text is flattened without stable page provenance and is truncated at the
   first `References` heading.
8. Image descriptions use enumeration order rather than the original PDF page
   number.
9. The optional YOLO path calls `process_images_with_yolo` with the wrong
   keyword and ignores the function argument.
10. Batch extraction claims multimodal processing but does not expose the image
   tool.
11. Supplemental-file matching, output directory creation, encoding, retries,
   and error records are brittle.
12. The NER helper calls its formatter with the wrong type, uses `eval()` on
    model output, and drops a final partial batch.
13. Defaulting missing surface chemistry or other values creates unsupported
    claims.
14. Free-form answers cannot be validated or safely merged into scientific
    tables.
15. The PDFs, Excel gold tables, annotations, YOLO weights, local-model
    adapters, train/test split, and expected outputs needed to reproduce the
    published notebook metrics are absent.

## Corrected architecture

The correction uses a small native provider layer rather than a model-name
substitution:

1. `PdfDocument` preserves source file, file role, real 1-based pages, text
   presence, and image presence.
2. The core Kimi K3 agent searches and reads bounded page sets through local
   tools.
3. Kimi K3 vision receives page images as base64 data URLs and returns page-
   scoped observations.
4. The agent loop returns every complete assistant message unchanged, and every
   tool call receives a matching tool result.
5. When evidence gathering stops, a separate `tool_choice=none` call produces a
   strict JSON-schema result.
6. Local guards reject truncated responses, missing evidence, profile mismatch,
   Gold output paths, and non-K3 core model configuration.
7. Program code overwrites any model-proposed review state with `needs_review`.

No chain-of-thought is printed or written to run artifacts.

## PE project boundary

For PE work, source PDFs remain in external `gold/gN` folders. Candidate output
belongs under:

`annotations/source_groups/gN/nanominer_kimi_k3/runs/<run_id>/`

It must not be merged directly into `annotations/*.jsonl` and must never be
written to `annotations/gold/`. Gold records may be used only as offline scoring
oracles; putting them in prompts would leak the evaluation target.

The first implementation intentionally emits generic evidence-backed candidate
records. The next adapter phase will deterministically assign IDs and map
reviewed fields into `paper_metadata`, `page_objects`, `entities`, `relations`,
`records_structure`, `records_observation`, and `records_orientation`.

## Verification completed in the initial branch

- Configuration and secret-redaction checks.
- K3 request contract excludes fixed sampling parameters.
- First evidence turn uses `tool_choice=required`; later turns use `auto`.
- Complete assistant object remains in the next request.
- Tool errors retain their matching `tool_call_id`.
- Structured output is a separate final call with `tool_choice=none`.
- `finish_reason=length` is rejected.
- Page numbering, search, and References preservation are tested with a
  generated two-page PDF.
- Candidate review status is enforced locally.

Live API and scientific regression tests remain pending until a Moonshot key is
available. They should run at least three repeats per paper because K3's
sampling parameters are fixed and cannot be made deterministic with
`temperature=0`.
