# Local full-text crystallographic screening

`nanominer-k3 screen` is a deterministic gate before any Kimi K3 extraction.
It reads every PDF page locally, records page-level evidence, and never reads an
API key.

## Decision boundary

The high-precision `keep` decision requires all of the following:

1. The evidence occurs before the detected References section.
2. The evidence is not explicitly attributed to earlier literature.
3. The paper has a primary-work claim rather than only a review or imported
   simulation model.
4. Native text contains either parseable atomic coordinates, or both a valid
   space-group symbol and numeric `a`, `b`, and `c` cell parameters.

OCR-only core evidence is `needs_review`, because OCR can corrupt decimal
points, atom-site columns, and Hermann-Mauguin symbols. A generic mention of
`crystal structure`, a crystal system, (hkl), XRD/WAXS, diffraction indexing,
or orientation is context only and cannot produce `keep` by accumulation.

The three machine decisions are preserved in `screening_manifest.jsonl`:

- `keep`: passes the local gate; still not Gold.
- `needs_review`: plausible evidence, incomplete coverage, OCR dependence, or
  a compound-document boundary prevents automatic acceptance.
- `exclude`: all content pages were readable but no qualifying primary core
  crystallographic evidence was confirmed, or the document is only a review
  or imported simulation input.

When the optional two-folder view is requested, only `keep` goes to `通过`.
Both `needs_review` and `exclude` go to `未通过`, while the manifest keeps them
distinguishable.

## Text and OCR handling

PyMuPDF supplies native page text. Local Tesseract OCR is attempted when a page
has fewer than 40 characters, when it has fewer than 200 characters and an
image covers at least 70% of the page, or when the embedded font mapping yields
punctuation/glyph-code garbage. OCR text replaces damaged native text even when
it is shorter. Read errors and OCR errors are recorded rather than silently
discarded.

The exclusion gate requires 100% readable non-sparse pages. This deliberately
favors recall: a document with an unread page is held for review instead of
being safely excluded.

## Reproducible outputs

- `screening_manifest.jsonl`: one auditable record per PDF, including SHA-256,
  page counts, OCR pages, evidence quotes, ownership, and reason code.
- `筛查清单.csv`: Excel-friendly summary.
- `保留文献.txt`, `待人工复核.txt`, `排除文献.txt`: relative-path lists.
- `summary.json`: counts, run mode, and partition status.
- `README_先看我.md`: short Chinese handoff.

Interrupted runs write `screening_manifest.partial.jsonl`. `--resume` reuses a
record only when schema version, file size, mtime, and OCR mode still match.

## Safety and reuse

`--copy-partitions` copies files and preserves the top-level originals. It
checks every resolved source/target path and available free space before the
first copy. Existing targets are reused only when their byte size matches.
Generated evidence stays in the project's `annotations/source_groups/` area,
separate from source PDFs and Gold annotations.

For later extraction, pass the allow-list explicitly:

```powershell
nanominer-k3 batch C:\path\to\pdfs `
  --screening-manifest C:\path\to\screening_manifest.jsonl `
  --profile pe_crystal `
  --output-dir C:\path\to\staging
```

The local gate is intentionally material-name agnostic. If a project needs a
PE-only, PP-only, or other chemistry scope, apply that as a separate auditable
filter rather than mixing it into the crystallographic evidence test.

## Calibration controls

The rule boundary was checked against the existing g2 controls:

- Bunn 1939: `keep` (primary atom positions and Pnam evidence).
- Shearer 1954: `needs_review` (compound thesis and damaged coordinate OCR).
- Smith and Lemstra 1980: `exclude` (orientation/diffraction context only).
- Tashiro 1997: `exclude` (secondary review).
- Fan and Cagin 1995: `exclude` (imported molecular-dynamics structure input).
