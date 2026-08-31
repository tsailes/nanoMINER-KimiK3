from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from nanominer_k3.screening import _read_all_pages, _reference_start


WHITESPACE = re.compile(r"\s+")
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "atomic_coordinates",
        re.compile(
            r"\b(?:fractional\s+)?(?:atomic\s+)?coordinates?\b|"
            r"\batomic\s+positions?\b|\batom(?:ic)?\s+sites?\b|"
            r"分数坐标|原子坐标|原子位置",
            re.IGNORECASE,
        ),
    ),
    (
        "wyckoff_sites",
        re.compile(
            r"\bWyckoff\b|\b(?:occup(?:ancy|ation)|site)\s+(?:parameter|factor)\b|"
            r"占位(?:率|因子)?|等效位置",
            re.IGNORECASE,
        ),
    ),
    (
        "space_group",
        re.compile(
            r"\bspace\s+group\b|\bHermann[-\s]?Mauguin\b|空间群",
            re.IGNORECASE,
        ),
    ),
    (
        "unit_cell_complete",
        re.compile(
            r"(?:unit[-\s]?cell|cell\s+(?:parameter|constant|dimension)|"
            r"lattice\s+(?:parameter|constant)|晶胞参数|晶格参数)"
            r".{0,900}?\ba\s*(?:=|:)\s*\d+(?:[.,]\d+)?"
            r".{0,320}?\bb\s*(?:=|:)\s*\d+(?:[.,]\d+)?"
            r".{0,320}?\bc\s*(?:=|:)\s*\d+(?:[.,]\d+)?",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "unit_cell_partial",
        re.compile(
            r"\b(?:unit[-\s]?cell|cell\s+(?:parameter|constant|dimension)|"
            r"lattice\s+(?:parameter|constant|spacing)|repeat\s+distance)s?\b|"
            r"晶胞|晶格常数|晶胞参数|晶格参数",
            re.IGNORECASE,
        ),
    ),
    (
        "crystal_system",
        re.compile(
            r"\b(?:orthorhombic|monoclinic|triclinic|tetragonal|trigonal|"
            r"hexagonal|rhombohedral|cubic)\b|"
            r"(?:正交|斜方|单斜|三斜|四方|三方|六方|菱方|立方)(?:晶系|晶胞|相)",
            re.IGNORECASE,
        ),
    ),
    (
        "structure_determination",
        re.compile(
            r"\b(?:crystal|molecular)\s+structure\b.{0,100}?"
            r"\b(?:determin|solv|refin|establish|elucidat|predict|model)\w*\b|"
            r"\b(?:determin|solv|refin|establish|elucidat)\w*\b.{0,80}?"
            r"\b(?:crystal|molecular)\s+structure\b|"
            r"晶体结构.{0,50}?(?:测定|解析|精修|确定|预测|模型)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "structure_model",
        re.compile(
            r"\b(?:crystal(?:lographic)?|unit[-\s]?cell)\s+(?:model|structure)\b|"
            r"\bchain\s+packing\b|\bpacking\s+(?:model|arrangement)\b|"
            r"\bcrystal\s+conformation\b|晶体模型|晶体中的链堆积",
            re.IGNORECASE,
        ),
    ),
    (
        "phase_assignment",
        re.compile(
            r"(?:\b(?:alpha|beta|gamma|delta|mesomorphic|paracrystalline)\b|"
            r"[αβγδ])\s*(?:[-\s]?(?:crystal(?:line)?|phase|form|modification))|"
            r"\b(?:crystal(?:line)?|phase|form|modification)\s+"
            r"(?:alpha|beta|gamma|delta)\b|(?:α|β|γ|δ)[晶相型]",
            re.IGNORECASE,
        ),
    ),
    (
        "diffraction_indexing",
        re.compile(
            r"\b(?:index(?:ed|ing)|assign(?:ed|ment))\b.{0,120}?"
            r"(?:\bhkl\b|\(\s*-?\d\s*[, ]?\s*-?\d\s*[, ]?\s*-?\d\s*\)|"
            r"reflection|peak|unit[-\s]?cell|phase)|"
            r"(?:reflection|peak).{0,80}?"
            r"\(\s*-?\d\s*[, ]?\s*-?\d\s*[, ]?\s*-?\d\s*\)|"
            r"衍射.{0,40}?(?:指标化|指数化|晶面指数|归属)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "hkl_planes",
        re.compile(
            r"\(\s*-?\d\s*[, ]?\s*-?\d\s*[, ]?\s*-?\d\s*\)"
        ),
    ),
    (
        "crystal_structure_phrase",
        re.compile(r"\bcrystal(?:line)?\s+structure\b|晶体结构", re.IGNORECASE),
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build local full-text drafts for broad crystal-structure review"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--end-index", type=int)
    parser.add_argument("--ocr-sparse-pages", action="store_true")
    args = parser.parse_args()

    manifest = args.manifest.expanduser().resolve()
    root = args.pdf_dir.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output == manifest:
        raise ValueError("Draft output may not overwrite the screening manifest")
    if output.is_relative_to(root):
        raise ValueError(
            "Draft output may not be written inside the PDF source directory"
        )

    rows = _read_jsonl(manifest)
    exclusions = sorted(
        (row for row in rows if row.get("final_decision") == "exclude"),
        key=lambda row: str(row["relative_path"]).casefold(),
    )
    end = args.end_index or len(exclusions)
    if args.start_index < 1 or end < args.start_index or end > len(exclusions):
        raise ValueError(
            f"Invalid review range {args.start_index}-{end}; "
            f"there are {len(exclusions)} exclusions"
        )

    output.parent.mkdir(parents=True, exist_ok=True)

    drafted: list[dict[str, Any]] = []
    for review_index in range(args.start_index, end + 1):
        baseline = exclusions[review_index - 1]
        relative_path = str(baseline["relative_path"])
        source = (root / relative_path).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError(f"PDF not found under source root: {relative_path}")
        stat = source.stat()
        if stat.st_size != baseline.get("source_size_bytes"):
            raise ValueError(f"PDF size differs from the manifest: {relative_path}")
        expected_sha = str(baseline.get("sha256", "")).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise ValueError(f"Missing or invalid baseline SHA-256: {relative_path}")
        if _sha256(source) != expected_sha:
            raise ValueError(f"PDF SHA-256 differs from the manifest: {relative_path}")
        pages, ocr_pages, ocr_errors = _read_all_pages(
            source,
            ocr_sparse_pages=args.ocr_sparse_pages,
            ocr_language="eng",
            ocr_dpi=150,
            sparse_page_chars=200,
        )
        reference_start = _reference_start(pages)
        evidence, reference_hits = _find_evidence(pages, reference_start)
        categories = sorted({item["information_type"] for item in evidence})
        drafted.append(
            {
                "review_index": review_index,
                "relative_path": relative_path,
                "source_sha256": expected_sha,
                "baseline_page_count": baseline.get("page_count"),
                "page_count": len(pages),
                "text_pages": sum(len(text) >= 40 for text in pages),
                "text_char_count": sum(len(text) for text in pages),
                "ocr_pages": ocr_pages,
                "ocr_errors": ocr_errors,
                "reference_start_page": reference_start[0] if reference_start else None,
                "body_categories": categories,
                "body_evidence": evidence,
                "reference_hit_count": reference_hits,
                "draft_decision": _draft_decision(
                    categories, pages, ocr_errors
                ),
                "fulltext_scanned": True,
            }
        )
        completed = review_index - args.start_index + 1
        total = end - args.start_index + 1
        if completed == 1 or completed % 25 == 0 or completed == total:
            print(
                json.dumps(
                    {
                        "event": "structure_draft_progress",
                        "completed": completed,
                        "total": total,
                        "relative_path": relative_path,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                file=sys.stderr,
                flush=True,
            )

    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in drafted),
        encoding="utf-8",
    )
    counts = Counter(row["draft_decision"] for row in drafted)
    print(
        json.dumps(
            {
                "output": str(output),
                "reviewed": len(drafted),
                "range": [args.start_index, end],
                "draft_counts": dict(sorted(counts.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _find_evidence(
    pages: list[str], reference_start: tuple[int, int] | None
) -> tuple[list[dict[str, Any]], int]:
    evidence: list[dict[str, Any]] = []
    reference_hits = 0
    per_category: Counter[str] = Counter()
    for page_number, text in enumerate(pages, start=1):
        for information_type, pattern in RULES:
            for match in pattern.finditer(text):
                if _in_references(page_number, match.start(), reference_start):
                    reference_hits += 1
                    continue
                if per_category[information_type] >= 8:
                    continue
                per_category[information_type] += 1
                evidence.append(
                    {
                        "information_type": information_type,
                        "page": page_number,
                        "matched_text": WHITESPACE.sub(
                            " ", match.group(0)
                        ).strip()[:500],
                        "quote": _snippet(text, match.start(), match.end()),
                    }
                )
    evidence.sort(key=lambda item: (item["page"], item["information_type"]))
    return evidence[:60], reference_hits


def _draft_decision(
    categories: list[str],
    pages: list[str],
    ocr_errors: list[dict[str, Any]],
) -> str:
    strong = {
        "atomic_coordinates",
        "wyckoff_sites",
        "space_group",
        "unit_cell_complete",
        "unit_cell_partial",
        "structure_determination",
        "structure_model",
        "diffraction_indexing",
    }
    if strong.intersection(categories):
        return "likely_keep"
    if categories:
        return "manual_context_review"
    coverage = sum(len(text) >= 40 for text in pages) / len(pages) if pages else 0
    if coverage < 1 or ocr_errors:
        return "manual_text_quality_review"
    return "likely_exclude"


def _in_references(
    page_number: int, offset: int, reference_start: tuple[int, int] | None
) -> bool:
    if reference_start is None:
        return False
    page, page_offset = reference_start
    return page_number > page or (page_number == page and offset >= page_offset)


def _snippet(text: str, start: int, end: int, radius: int = 360) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return WHITESPACE.sub(" ", text[left:right]).strip()[:1200]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Expected a JSON object at {path}:{line_number}")
        rows.append(row)
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
