from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

import pymupdf

from .documents import _normalize_text


SCREENING_SCHEMA_VERSION = "1.2"
DECISION_RULES_VERSION = "1.1"
SCREENING_MANIFEST = "screening_manifest.jsonl"
PARTIAL_MANIFEST = "screening_manifest.partial.jsonl"


@dataclass(frozen=True, slots=True)
class SignalRule:
    category: str
    pattern: re.Pattern[str]
    strength: str


def _rule(category: str, pattern: str, strength: str) -> SignalRule:
    return SignalRule(category, re.compile(pattern, re.IGNORECASE | re.DOTALL), strength)


_RULES = (
    _rule(
        "space_group",
        r"(?:\bspace\s+group\b|空间群)\s*(?:is|was|:|=|为)?\s*"
        r"(?:"
        r"(?-i:[PIFCRABpifcrab])\s*"
        r"(?:"
        r"[1-6](?:[0-9_./\-]*[mabcned]?)|"
        r"(?:nam|nma|bcm|mcm|mmm|mmn|mna|cmm|mma|bam|cab|cba|"
        r"ca|na|ma|ba|ac|bc|cc|nc|mc)"
        r")"
        r")(?![A-Za-z])",
        "strong",
    ),
    _rule(
        "numeric_unit_cell",
        r"(?:\bunit[-\s]?cell\b|\bcell\s+(?:parameters?|constants?|dimensions?)\b|"
        r"\blattice\s+(?:parameters?|constants?)\b|晶胞参数|晶格参数)"
        r".{0,700}?\ba\s*(?:=|:)\s*\d+(?:[.,]\d+)?"
        r".{0,220}?\bb\s*(?:=|:)\s*\d+(?:[.,]\d+)?"
        r".{0,220}?\bc\s*(?:=|:)\s*\d+(?:[.,]\d+)?",
        "strong",
    ),
    _rule(
        "atomic_coordinates",
        r"(?:\bfractional\s+(?:atomic\s+)?coordinates?\b|"
        r"\batomic\s+(?:coordinates?|positions?)\b|\batom\s+sites?\b|"
        r"分数坐标|原子坐标|原子位置)"
        r".{0,260}?(?:"
        r"\bx\s*(?:=|:)\s*-?(?:\d+(?:\.\d+)?|\.\d+)"
        r".{0,160}?\by\s*(?:=|:)\s*-?(?:\d+(?:\.\d+)?|\.\d+)|"
        r"(?:-?(?:\d+\.\d+|\.\d+)[\s,;]+){2}"
        r"-?(?:\d+\.\d+|\.\d+)"
        r")",
        "strong",
    ),
    _rule(
        "structure_determination",
        r"(?:(?:\bcrystal|\bmolecular)\s+structure\b.{0,100}?"
        r"(?:determined|solved|refined|established|elucidated)|"
        r"(?:determined|solved|refined|established|elucidated).{0,60}?"
        r"(?:\bcrystal|\bmolecular)\s+structure\b|"
        r"晶体结构.{0,40}?(?:测定|解析|精修|确定))",
        "candidate",
    ),
    _rule(
        "structure_refinement",
        r"(?:\bRietveld\s+(?:refinement|analysis)\b|\bstructure\s+refinement\b|"
        r"\bsingle[-\s]?crystal\s+(?:X[-\s]?ray|neutron)\s+diffraction\b|"
        r"里特维尔德.{0,20}?(?:精修|分析)|结构精修)",
        "candidate",
    ),
    _rule(
        "crystal_structure_phrase",
        r"(?:\bcrystal(?:line)?\s+structure\b|晶体结构)",
        "context",
    ),
    _rule(
        "crystal_system",
        r"(?:(?:\bcrystal\s+system\b|晶系).{0,80}?"
        r"(?:triclinic|monoclinic|orthorhombic|tetragonal|trigonal|hexagonal|"
        r"rhombohedral|cubic|三斜|单斜|正交|四方|三方|六方|菱方|立方)|"
        r"(?:triclinic|monoclinic|orthorhombic|tetragonal|trigonal|hexagonal|"
        r"rhombohedral|cubic).{0,50}?(?:crystal|phase|cell|system))",
        "context",
    ),
    _rule(
        "diffraction_indexing",
        r"(?:(?:\bindexed|\bindexing)\b.{0,100}?(?:\bhkl\b|\bunit[-\s]?cell\b)|"
        r"\bMiller\s+indices\b|衍射.{0,30}?(?:指标化|指数化|晶面指数))",
        "context",
    ),
)

_REFERENCE_HEADING = re.compile(
    r"(?im)^\s*(?:references|bibliography|literature\s+cited|参考文献)\s*[:.]?\s*$"
)
_DOCUMENT_FLAG_PATTERNS = (
    (
        "review_or_overview",
        re.compile(
            r"\b(?:review(?:ed|s|ing)?|overview|recent\s+developments?)\b|综述",
            re.IGNORECASE,
        ),
    ),
    (
        "simulation_or_imported_model",
        re.compile(
            r"\b(?:molecular\s+dynamics|monte\s+carlo)\s+simulations?\b|"
            r"\bmodel\s+surfaces?\b|"
            r"\b(?:built|constructed|generated)\s+from\b.{0,120}?"
            r"\b(?:reported|experimental|literature)\b.{0,80}?\bunit\s+cell\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "thesis_or_multipart_document",
        re.compile(
            r"\b(?:thesis|dissertation)\b|\bsubmitted\b.{0,120}?\bdegree\b|"
            r"\bpart\s+i\b.{0,160}?\bpart\s+ii\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)
_PRIMARY_CONTRIBUTION_PATTERNS = (
    re.compile(
        r"\b(?:crystal\s+structure|structure\s+(?:determination|refinement))"
        r"\s+(?:of|for)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwe\s+(?:have\s+)?(?:report|present|determin|refin|solv|establish)\w*\b|"
        r"\b(?:present\s+(?:work|study|investigation)|this\s+(?:work|study))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:crystal\s+)?structure\s+(?:has\s+been|was|is)\s+"
        r"(?:determined|refined|solved|established)\b|"
        r"\bprecise\s+atomic\s+positions?\b",
        re.IGNORECASE,
    ),
)
_SECONDARY_ATTRIBUTION = re.compile(
    r"\b(?:previously\s+(?:reported|observed|determined)|"
    r"(?:reported|observed|determined|proposed|assumed)\s+by|"
    r"(?:these|other)\s+authors|according\s+to|taken\s+from|"
    r"from\s+the\s+literature|used\s+to\s+build|built\s+from)\b",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
_PARTITION_NAMES = frozenset({"通过", "未通过"})


class ScreeningReviewer(Protocol):
    def review(self, record: Mapping[str, Any]) -> dict[str, Any]: ...


ProgressHandler = Callable[[dict[str, Any]], None]


def discover_pdf_files(directory: Path) -> list[Path]:
    root = directory.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"PDF directory not found: {root}")
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.casefold() == ".pdf"
            and not any(part in _PARTITION_NAMES for part in path.relative_to(root).parts)
        ),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )


def screen_pdf(
    path: Path,
    *,
    root: Path,
    ocr_sparse_pages: bool = False,
    ocr_language: str = "eng",
    ocr_dpi: int = 150,
    sparse_page_chars: int = 200,
    max_evidence: int = 24,
) -> dict[str, Any]:
    source = path.expanduser().resolve()
    root = root.expanduser().resolve()
    stat = source.stat()
    base: dict[str, Any] = {
        "schema_version": SCREENING_SCHEMA_VERSION,
        "decision_rules_version": DECISION_RULES_VERSION,
        "relative_path": source.relative_to(root).as_posix(),
        "source_file": source.name,
        "source_size_bytes": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256(source),
        "screened_at_utc": datetime.now(timezone.utc).isoformat(),
        "screening_method": "local_full_text_rules_v2",
        "ocr_requested": ocr_sparse_pages,
        "k3_review": None,
        "duplicate_of": None,
        "error": None,
    }
    try:
        pages, ocr_pages, ocr_errors = _read_all_pages(
            source,
            ocr_sparse_pages=ocr_sparse_pages,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
            sparse_page_chars=sparse_page_chars,
        )
    except Exception as exc:
        return {
            **base,
            "page_count": 0,
            "full_text_pages_examined": 0,
            "text_pages": 0,
            "empty_text_pages": [],
            "ocr_pages": [],
            "ocr_errors": [],
            "text_char_count": 0,
            "text_coverage_ratio": 0.0,
            "reference_start_page": None,
            "body_hit_count": 0,
            "reference_only_hit_count": 0,
            "categories": [],
            "document_flags": [],
            "text_quality_flags": [],
            "primary_contribution_signals": [],
            "qualifying_core_categories": [],
            "evidence": [],
            "local_decision": "needs_review",
            "final_decision": "needs_review",
            "confidence": "low",
            "decision_reason": "pdf_read_error",
            "error": f"{type(exc).__name__}: {exc}",
        }

    reference_start = _reference_start(pages)
    body_evidence: list[dict[str, Any]] = []
    reference_hit_count = 0
    body_hit_count = 0
    for page_number, text in enumerate(pages, start=1):
        for rule in _RULES:
            category_hits = 0
            for match in rule.pattern.finditer(text):
                in_references = _is_in_references(
                    page_number,
                    match.start(),
                    reference_start,
                )
                if in_references:
                    reference_hit_count += 1
                    continue
                body_hit_count += 1
                category_hits += 1
                if len(body_evidence) < max_evidence and category_hits <= 3:
                    quote = _snippet(text, match.start(), match.end())
                    if _SECONDARY_ATTRIBUTION.search(quote):
                        ownership = "secondary"
                        qualifies = False
                        rejection_reason = "secondary_attribution"
                    elif _contains_primary_signal(quote):
                        ownership = "this_work"
                        qualifies = rule.strength == "strong"
                        rejection_reason = None
                    else:
                        ownership = "unknown"
                        qualifies = rule.strength == "strong"
                        rejection_reason = None
                    body_evidence.append(
                        {
                            "evidence_index": len(body_evidence),
                            "category": rule.category,
                            "strength": rule.strength,
                            "page": page_number,
                            "matched_text": _WHITESPACE.sub(" ", match.group(0)).strip(),
                            "quote": quote,
                            "text_source": (
                                "local_ocr" if page_number in ocr_pages else "native_text"
                            ),
                            "section_type": "body",
                            "claim_ownership": ownership,
                            "qualifies": qualifies,
                            "rejection_reason": rejection_reason,
                        }
                    )

    page_count = len(pages)
    text_pages = sum(len(text) >= 40 for text in pages)
    empty_pages = [
        index for index, text in enumerate(pages, start=1) if len(text) < 40
    ]
    text_chars = sum(len(text) for text in pages)
    coverage = text_pages / page_count if page_count else 0.0
    text_complete = (
        coverage == 1.0
        and text_chars >= max(200, page_count * 80)
        and not ocr_errors
    )
    document_flags = _document_flags(pages)
    text_quality_flags = _text_quality_flags(pages)
    primary_signals = _primary_contribution_signals(pages, reference_start)
    categories = sorted({item["category"] for item in body_evidence})
    usable_evidence = [
        item for item in body_evidence if item["claim_ownership"] != "secondary"
    ]
    strong_categories = {
        item["category"] for item in usable_evidence if item["strength"] == "strong"
    }
    candidate_categories = {
        item["category"]
        for item in usable_evidence
        if item["strength"] == "candidate"
    }
    context_categories = {
        item["category"]
        for item in usable_evidence
        if item["strength"] == "context"
    }
    native_strong_categories = {
        item["category"]
        for item in usable_evidence
        if item["strength"] == "strong" and item["page"] not in ocr_pages
    }
    qualifying_core_categories = _qualifying_core_categories(strong_categories)
    native_qualifying_categories = _qualifying_core_categories(
        native_strong_categories
    )
    has_primary_claim = bool(primary_signals) or any(
        item["claim_ownership"] == "this_work" for item in usable_evidence
    )
    if native_qualifying_categories and document_flags:
        decision = "needs_review"
        confidence = "low"
        reason = "document_boundary_or_imported_structure_requires_review"
    elif native_qualifying_categories and text_quality_flags:
        decision = "needs_review"
        confidence = "low"
        reason = "critical_text_quality_requires_review"
    elif native_qualifying_categories and has_primary_claim:
        decision = "keep"
        confidence = "high"
        reason = "verified_primary_core_crystallographic_evidence"
    elif native_qualifying_categories:
        decision = "needs_review"
        confidence = "low"
        reason = "core_evidence_ownership_unresolved"
    elif qualifying_core_categories:
        decision = "needs_review"
        confidence = "low"
        reason = "core_evidence_depends_on_unverified_ocr"
    elif text_quality_flags or (
        "thesis_or_multipart_document" in document_flags and categories
    ):
        decision = "needs_review"
        confidence = "low"
        reason = "compound_document_or_text_quality_requires_review"
    elif document_flags and text_complete:
        decision = "exclude"
        confidence = "medium"
        reason = "secondary_review_simulation_or_compound_document_only"
    elif strong_categories or candidate_categories:
        decision = "needs_review"
        confidence = "low"
        reason = "unverified_core_crystallographic_candidate"
    elif text_complete:
        decision = "exclude"
        confidence = "medium"
        reason = (
            "context_only_without_core_crystallographic_data"
            if context_categories
            else "complete_text_without_core_crystallographic_evidence"
        )
    else:
        decision = "needs_review"
        confidence = "low"
        reason = "incomplete_or_sparse_text_without_explicit_evidence"

    return {
        **base,
        "page_count": page_count,
        "full_text_pages_examined": page_count,
        "text_pages": text_pages,
        "empty_text_pages": empty_pages,
        "ocr_pages": ocr_pages,
        "ocr_errors": ocr_errors,
        "text_char_count": text_chars,
        "text_coverage_ratio": round(coverage, 6),
        "reference_start_page": reference_start[0] if reference_start else None,
        "body_hit_count": body_hit_count,
        "reference_only_hit_count": reference_hit_count,
        "categories": categories,
        "document_flags": document_flags,
        "text_quality_flags": text_quality_flags,
        "primary_contribution_signals": primary_signals,
        "qualifying_core_categories": sorted(qualifying_core_categories),
        "evidence": body_evidence,
        "local_decision": decision,
        "final_decision": decision,
        "confidence": confidence,
        "decision_reason": reason,
    }


def screen_directory(
    *,
    pdf_dir: Path,
    output_dir: Path,
    ocr_sparse_pages: bool = False,
    ocr_language: str = "eng",
    ocr_dpi: int = 150,
    limit: int | None = None,
    resume: bool = True,
    reviewer: ScreeningReviewer | None = None,
    review_scope: str = "needs_review",
    progress_handler: ProgressHandler | None = None,
    partition_root: Path | None = None,
    copy_partitions: bool = False,
) -> dict[str, Any]:
    root = pdf_dir.expanduser().resolve()
    destination = output_dir.expanduser().resolve()
    files = discover_pdf_files(root)
    if limit is not None:
        if limit < 1:
            raise ValueError("Screening limit must be positive")
        files = files[:limit]
    if not files:
        raise ValueError(f"No PDF files found in {root}")
    destination.mkdir(parents=True, exist_ok=True)

    prior = _load_resume_records(destination) if resume else {}
    records: list[dict[str, Any]] = []
    partial_path = destination / PARTIAL_MANIFEST
    partial_path.write_text("", encoding="utf-8")
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        cached = prior.get(relative)
        if cached and _same_source(cached, stat, ocr_sparse_pages=ocr_sparse_pages):
            record = _reclassify_record(dict(cached))
            _emit(
                progress_handler,
                "screening_resumed",
                index=index,
                total=len(files),
                source_file=relative,
            )
        else:
            _emit(
                progress_handler,
                "screening_started",
                index=index,
                total=len(files),
                source_file=relative,
            )
            record = screen_pdf(
                path,
                root=root,
                ocr_sparse_pages=ocr_sparse_pages,
                ocr_language=ocr_language,
                ocr_dpi=ocr_dpi,
            )
            if reviewer is not None and _should_review(record, review_scope):
                review = reviewer.review(record)
                record["k3_review"] = review
                record["final_decision"] = _reconcile_decision(record, review)
                if record["final_decision"] != record["local_decision"]:
                    record["decision_reason"] = "local_k3_reconciled"
            _emit(
                progress_handler,
                "screening_completed",
                index=index,
                total=len(files),
                source_file=relative,
                decision=record["final_decision"],
            )
        records.append(record)
        _append_jsonl(partial_path, record)

    _mark_duplicates(records)
    partition_summary: dict[str, Any] | None = None
    if copy_partitions:
        target_root = (partition_root or root).expanduser().resolve()
        partition_summary = materialize_two_folders(
            records=records,
            pdf_dir=root,
            partition_root=target_root,
            progress_handler=progress_handler,
        )
    summary = _write_outputs(
        records=records,
        pdf_dir=root,
        output_dir=destination,
        ocr_sparse_pages=ocr_sparse_pages,
        k3_review_enabled=reviewer is not None,
        partition_summary=partition_summary,
    )
    if partial_path.exists():
        partial_path.unlink()
    return summary


def load_kept_relative_paths(manifest_path: Path) -> set[str]:
    path = manifest_path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Screening manifest not found: {path}")
    kept: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid screening manifest JSON at line {line_number}"
            ) from exc
        if record.get("final_decision") == "keep":
            kept.add(str(record["relative_path"]).replace("\\", "/").casefold())
    return kept


def materialize_two_folders(
    *,
    records: Iterable[Mapping[str, Any]],
    pdf_dir: Path,
    partition_root: Path,
    progress_handler: ProgressHandler | None = None,
) -> dict[str, Any]:
    """Copy screened PDFs into reversible pass/fail partitions.

    Non-keep decisions, including needs_review and read errors, go to 未通过.
    Original PDFs are never moved or deleted by this function.
    """
    source_root = pdf_dir.expanduser().resolve()
    target_root = partition_root.expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    passed_root = (target_root / "通过").resolve()
    failed_root = (target_root / "未通过").resolve()
    if not passed_root.is_relative_to(target_root) or not failed_root.is_relative_to(
        target_root
    ):
        raise ValueError("Screening partition paths escaped their target root")

    materialized = list(records)
    missing_bytes = 0
    for record in materialized:
        source = (source_root / str(record["relative_path"])).resolve()
        target_base = passed_root if record.get("final_decision") == "keep" else failed_root
        target = (target_base / str(record["relative_path"])).resolve()
        if not source.is_relative_to(source_root) or not target.is_relative_to(target_base):
            raise ValueError("A screening record resolved outside its expected root")
        if not target.exists() and source.is_file():
            missing_bytes += source.stat().st_size
    free_bytes = shutil.disk_usage(target_root).free
    if free_bytes < missing_bytes + 256 * 1024 * 1024:
        raise ValueError(
            "Insufficient free space for reversible PDF partition copies: "
            f"need at least {missing_bytes + 256 * 1024 * 1024} bytes"
        )

    copied = 0
    reused = 0
    failed = 0
    stale_opposite_removed = 0
    for index, record in enumerate(materialized, start=1):
        source = (source_root / str(record["relative_path"])).resolve()
        target_base = passed_root if record.get("final_decision") == "keep" else failed_root
        opposite_base = failed_root if target_base == passed_root else passed_root
        target = (target_base / str(record["relative_path"])).resolve()
        opposite = (opposite_base / str(record["relative_path"])).resolve()
        if not opposite.is_relative_to(opposite_base):
            raise ValueError("An opposite partition path escaped its expected root")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not source.is_file():
            failed += 1
            continue
        if target.exists():
            expected_sha = str(record.get("sha256", ""))
            target_matches = target.stat().st_size == source.stat().st_size
            if target_matches and expected_sha:
                target_matches = _sha256(target) == expected_sha
            if target_matches:
                reused += 1
            else:
                raise ValueError(
                    f"Partition target already exists with different content: {target}"
                )
        else:
            shutil.copy2(source, target)
            copied += 1
            _emit(
                progress_handler,
                "partition_file_copied",
                index=index,
                total=len(materialized),
                decision=record.get("final_decision"),
                source_file=record["relative_path"],
            )
        if opposite.is_file():
            expected_sha = str(record.get("sha256", ""))
            if expected_sha and _sha256(opposite) != expected_sha:
                raise ValueError(
                    f"Stale opposite partition has unexpected content: {opposite}"
                )
            opposite.unlink()
            stale_opposite_removed += 1
    return {
        "partition_root": str(target_root),
        "passed_folder": str(passed_root),
        "failed_folder": str(failed_root),
        "copy_mode": "copy_preserve_originals",
        "passed": sum(
            record.get("final_decision") == "keep" for record in materialized
        ),
        "failed_including_review": sum(
            record.get("final_decision") != "keep" for record in materialized
        ),
        "copied": copied,
        "reused": reused,
        "stale_opposite_removed": stale_opposite_removed,
        "missing_sources": failed,
    }


def _read_all_pages(
    path: Path,
    *,
    ocr_sparse_pages: bool,
    ocr_language: str,
    ocr_dpi: int,
    sparse_page_chars: int,
) -> tuple[list[str], list[int], list[dict[str, Any]]]:
    pages: list[str] = []
    ocr_pages: list[int] = []
    ocr_errors: list[dict[str, Any]] = []
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError("PDF is password protected")
        if pdf.page_count < 1:
            raise ValueError("PDF contains no pages")
        for index, page in enumerate(pdf, start=1):
            text = _normalize_text(page.get_text("text", sort=True))
            image_coverage = _largest_image_coverage(page)
            broken_glyph_text = _looks_like_broken_glyph_text(text)
            should_ocr = broken_glyph_text or len(text) < 40 or (
                len(text) < sparse_page_chars and image_coverage >= 0.70
            )
            if ocr_sparse_pages and should_ocr:
                try:
                    text_page = page.get_textpage_ocr(
                        language=ocr_language,
                        dpi=ocr_dpi,
                        full=True,
                    )
                    ocr_text = _normalize_text(
                        page.get_text("text", textpage=text_page, sort=True)
                    )
                    if broken_glyph_text or len(ocr_text) > len(text):
                        text = ocr_text
                    ocr_pages.append(index)
                except Exception as exc:
                    ocr_errors.append(
                        {
                            "page": index,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
            pages.append(text)
    return pages, ocr_pages, ocr_errors


def _largest_image_coverage(page: pymupdf.Page) -> float:
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    largest = 0.0
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:
        return 0.0
    for block in blocks:
        if block.get("type") != 1:
            continue
        x0, y0, x1, y1 = block.get("bbox", (0, 0, 0, 0))
        area = max(float(x1 - x0), 0.0) * max(float(y1 - y0), 0.0)
        largest = max(largest, area)
    return min(largest / page_area, 1.0)


def _looks_like_broken_glyph_text(text: str) -> bool:
    visible = [character for character in text if not character.isspace()]
    if len(visible) < 250:
        return False
    alphabetic = sum(character.isalpha() for character in visible)
    alphabetic_ratio = alphabetic / len(visible)
    word_count = len(re.findall(r"[A-Za-z]{3,}", text))
    return alphabetic_ratio < 0.35 and word_count < max(5, len(visible) // 250)


def _reference_start(pages: list[str]) -> tuple[int, int] | None:
    first_allowed_page = max(2, math.ceil(len(pages) * 0.45))
    for page_number, text in enumerate(pages, start=1):
        if page_number < first_allowed_page:
            continue
        match = _REFERENCE_HEADING.search(text)
        if match:
            return page_number, match.start()
    return None


def _is_in_references(
    page_number: int,
    offset: int,
    reference_start: tuple[int, int] | None,
) -> bool:
    if reference_start is None:
        return False
    start_page, start_offset = reference_start
    return page_number > start_page or (
        page_number == start_page and offset >= start_offset
    )


def _snippet(text: str, start: int, end: int, context: int = 260) -> str:
    left = max(0, start - context)
    right = min(len(text), end + context)
    return _WHITESPACE.sub(" ", text[left:right]).strip()


def _contains_primary_signal(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PRIMARY_CONTRIBUTION_PATTERNS)


def _primary_contribution_signals(
    pages: list[str], reference_start: tuple[int, int] | None
) -> list[str]:
    if reference_start is None:
        searchable = "\n".join(pages)
    else:
        page_number, offset = reference_start
        searchable = "\n".join([*pages[: page_number - 1], pages[page_number - 1][:offset]])
    names = (
        "primary_structure_title_or_scope",
        "first_person_or_present_work_claim",
        "explicit_structure_determination_claim",
    )
    return [
        name
        for name, pattern in zip(names, _PRIMARY_CONTRIBUTION_PATTERNS)
        if pattern.search(searchable)
    ]


def _document_flags(pages: list[str]) -> list[str]:
    early_text = "\n".join(pages[: min(2, len(pages))])[:12000]
    return [
        name for name, pattern in _DOCUMENT_FLAG_PATTERNS if pattern.search(early_text)
    ]


def _text_quality_flags(pages: list[str]) -> list[str]:
    text = "\n".join(pages)
    flags: list[str] = []
    if _looks_like_broken_glyph_text(text):
        flags.append("broken_font_glyph_mapping")
    replacements = text.count("\ufffd")
    if replacements >= max(20, int(max(len(text), 1) * 0.002)):
        flags.append("replacement_character_corruption")
    spaced_letter_runs = len(
        re.findall(r"(?:\b[A-Za-z]\s+){6,}[A-Za-z]\b", text)
    )
    if spaced_letter_runs >= 8:
        flags.append("spaced_letter_ocr_corruption")
    if len(re.findall(r"\d\*\d", text)) >= 2:
        flags.append("numeric_ocr_corruption")
    return flags


def _qualifying_core_categories(categories: set[str]) -> set[str]:
    if "atomic_coordinates" in categories:
        return {"atomic_coordinates"}
    if {"space_group", "numeric_unit_cell"}.issubset(categories):
        return {"space_group", "numeric_unit_cell"}
    return set()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _should_review(record: Mapping[str, Any], scope: str) -> bool:
    if record.get("error"):
        return False
    if scope == "needs_review":
        return record.get("local_decision") == "needs_review"
    if scope == "candidates":
        return record.get("local_decision") in {"keep", "needs_review"}
    raise ValueError("K3 review scope must be 'needs_review' or 'candidates'")


def _reconcile_decision(
    record: Mapping[str, Any], review: Mapping[str, Any]
) -> str:
    local = str(record["local_decision"])
    model = str(review.get("decision", "needs_review"))
    if local == "exclude":
        return "exclude"
    if local == "needs_review":
        return "keep" if model == "keep" else "needs_review"
    return "keep" if model == "keep" else "needs_review"


def _reclassify_record(record: dict[str, Any]) -> dict[str, Any]:
    """Refresh deterministic decisions without reopening an unchanged PDF."""
    evidence = list(record.get("evidence", []))
    for item in evidence:
        quote = str(item.get("quote", ""))
        if _SECONDARY_ATTRIBUTION.search(quote):
            item["claim_ownership"] = "secondary"
            item["qualifies"] = False
            item["rejection_reason"] = "secondary_attribution"
        elif _contains_primary_signal(quote):
            item["claim_ownership"] = "this_work"
            item["qualifies"] = item.get("strength") == "strong"
            item["rejection_reason"] = None
        else:
            item["claim_ownership"] = "unknown"
            item["qualifies"] = item.get("strength") == "strong"
            item["rejection_reason"] = None

    usable = [item for item in evidence if item.get("claim_ownership") != "secondary"]
    strong = {
        str(item["category"])
        for item in usable
        if item.get("strength") == "strong"
    }
    native_strong = {
        str(item["category"])
        for item in usable
        if item.get("strength") == "strong"
        and item.get("text_source") != "local_ocr"
    }
    candidate = {
        str(item["category"])
        for item in usable
        if item.get("strength") == "candidate"
    }
    context = {
        str(item["category"])
        for item in usable
        if item.get("strength") == "context"
    }
    core = _qualifying_core_categories(strong)
    native_core = _qualifying_core_categories(native_strong)
    document_flags = list(record.get("document_flags", []))
    quality_flags = list(record.get("text_quality_flags", []))
    primary_signals = list(record.get("primary_contribution_signals", []))
    has_primary = bool(primary_signals) or any(
        item.get("claim_ownership") == "this_work" for item in usable
    )
    page_count = int(record.get("page_count", 0))
    text_complete = (
        float(record.get("text_coverage_ratio", 0.0)) == 1.0
        and int(record.get("text_char_count", 0)) >= max(200, page_count * 80)
        and not record.get("ocr_errors")
    )

    if native_core and document_flags:
        decision, confidence, reason = (
            "needs_review",
            "low",
            "document_boundary_or_imported_structure_requires_review",
        )
    elif native_core and quality_flags:
        decision, confidence, reason = (
            "needs_review",
            "low",
            "critical_text_quality_requires_review",
        )
    elif native_core and has_primary:
        decision, confidence, reason = (
            "keep",
            "high",
            "verified_primary_core_crystallographic_evidence",
        )
    elif native_core:
        decision, confidence, reason = (
            "needs_review",
            "low",
            "core_evidence_ownership_unresolved",
        )
    elif core:
        decision, confidence, reason = (
            "needs_review",
            "low",
            "core_evidence_depends_on_unverified_ocr",
        )
    elif quality_flags or (
        "thesis_or_multipart_document" in document_flags and record.get("categories")
    ):
        decision, confidence, reason = (
            "needs_review",
            "low",
            "compound_document_or_text_quality_requires_review",
        )
    elif document_flags and text_complete:
        decision, confidence, reason = (
            "exclude",
            "medium",
            "secondary_review_simulation_or_compound_document_only",
        )
    elif strong or candidate:
        decision, confidence, reason = (
            "needs_review",
            "low",
            "unverified_core_crystallographic_candidate",
        )
    elif text_complete:
        decision, confidence, reason = (
            "exclude",
            "medium",
            (
                "context_only_without_core_crystallographic_data"
                if context
                else "complete_text_without_core_crystallographic_evidence"
            ),
        )
    else:
        decision, confidence, reason = (
            "needs_review",
            "low",
            "incomplete_or_sparse_text_without_explicit_evidence",
        )

    record.update(
        {
            "decision_rules_version": DECISION_RULES_VERSION,
            "evidence": evidence,
            "qualifying_core_categories": sorted(core),
            "local_decision": decision,
            "final_decision": decision,
            "confidence": confidence,
            "decision_reason": reason,
        }
    )
    if record.get("k3_review"):
        record["final_decision"] = _reconcile_decision(record, record["k3_review"])
    return record


def _mark_duplicates(records: list[dict[str, Any]]) -> None:
    first_by_sha: dict[str, str] = {}
    for record in records:
        sha256 = str(record.get("sha256", ""))
        if not sha256:
            continue
        relative = str(record["relative_path"])
        if sha256 in first_by_sha:
            record["duplicate_of"] = first_by_sha[sha256]
        else:
            first_by_sha[sha256] = relative


def _same_source(
    record: Mapping[str, Any], stat: Any, *, ocr_sparse_pages: bool
) -> bool:
    return (
        record.get("schema_version") == SCREENING_SCHEMA_VERSION
        and int(record.get("source_size_bytes", -1)) == stat.st_size
        and int(record.get("source_mtime_ns", -1)) == stat.st_mtime_ns
        and bool(record.get("ocr_requested")) == ocr_sparse_pages
    )


def _load_resume_records(output_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name in (SCREENING_MANIFEST, PARTIAL_MANIFEST):
        path = output_dir / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            records[str(record["relative_path"])] = record
    return records


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_outputs(
    *,
    records: list[dict[str, Any]],
    pdf_dir: Path,
    output_dir: Path,
    ocr_sparse_pages: bool,
    k3_review_enabled: bool,
    partition_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    manifest = output_dir / SCREENING_MANIFEST
    manifest.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    counts = Counter(str(record["final_decision"]) for record in records)
    errors = sum(bool(record.get("error")) for record in records)
    duplicates = sum(bool(record.get("duplicate_of")) for record in records)
    summary = {
        "schema_version": SCREENING_SCHEMA_VERSION,
        "decision_rules_version": DECISION_RULES_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pdf_dir": str(pdf_dir),
        "total": len(records),
        "keep": counts["keep"],
        "exclude": counts["exclude"],
        "needs_review": counts["needs_review"],
        "errors": errors,
        "duplicates": duplicates,
        "ocr_sparse_pages": ocr_sparse_pages,
        "k3_review_enabled": k3_review_enabled,
        "source_files_modified": False,
        "partition": dict(partition_summary) if partition_summary else None,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(records, output_dir / "筛查清单.csv")
    _write_path_list(records, output_dir / "保留文献.txt", "keep")
    _write_path_list(records, output_dir / "待人工复核.txt", "needs_review")
    _write_path_list(records, output_dir / "排除文献.txt", "exclude")
    (output_dir / "README_先看我.md").write_text(
        _readme(summary),
        encoding="utf-8",
    )
    return summary


def _write_csv(records: Iterable[Mapping[str, Any]], path: Path) -> None:
    columns = [
        "relative_path",
        "source_file",
        "final_decision",
        "confidence",
        "decision_reason",
        "page_count",
        "full_text_pages_examined",
        "text_coverage_ratio",
        "ocr_pages",
        "categories",
        "qualifying_core_categories",
        "document_flags",
        "text_quality_flags",
        "primary_contribution_signals",
        "body_hit_count",
        "reference_only_hit_count",
        "duplicate_of",
        "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            row = {column: record.get(column) for column in columns}
            row["ocr_pages"] = json.dumps(record.get("ocr_pages", []))
            row["categories"] = json.dumps(
                record.get("categories", []), ensure_ascii=False
            )
            for column in (
                "qualifying_core_categories",
                "document_flags",
                "text_quality_flags",
                "primary_contribution_signals",
            ):
                row[column] = json.dumps(
                    record.get(column, []), ensure_ascii=False
                )
            writer.writerow(row)


def _write_path_list(
    records: Iterable[Mapping[str, Any]], path: Path, decision: str
) -> None:
    selected = [
        str(record["relative_path"])
        for record in records
        if record.get("final_decision") == decision
    ]
    path.write_text("\n".join(selected) + ("\n" if selected else ""), encoding="utf-8")


def _readme(summary: Mapping[str, Any]) -> str:
    return f"""# PDF 全文晶体结构筛查结果

本次筛查只读取原始 PDF，没有删除、移动或重命名源文件。
筛查完全在本机运行，没有调用 Kimi 或其他在线模型。

- PDF 总数：{summary['total']}
- 保留（发现明确晶体结构证据）：{summary['keep']}
- 待人工复核（文本层不足或证据模糊）：{summary['needs_review']}
- 排除（完整文本中未发现明确证据）：{summary['exclude']}
- 读取错误：{summary['errors']}
- SHA256 重复副本：{summary['duplicates']}

请先阅读 `保留文献.txt` 和 `待人工复核.txt`。`排除文献.txt` 不是删除清单；
它只表示本轮规则没有确认“原子坐标”，或“有效空间群 + 数值晶胞”这两类
核心晶体学证据。仅出现 crystal structure、晶系、(hkl)、XRD/WAXS 或取向
等上下文词不会自动通过。综述、导入模拟结构、复合学位论文和未核验 OCR
会被排除或进入待人工复核。

详细页码、原文片段和判定原因位于 `screening_manifest.jsonl`，便于复核和恢复运行。
只有 `final_decision=keep` 的条目才能作为后续 nanoMINER K3 批量抽取输入；
筛查结果本身不代表 Gold 或人工验证。
"""


def _emit(handler: ProgressHandler | None, event: str, **details: Any) -> None:
    if handler is not None:
        handler({"event": event, **details})
