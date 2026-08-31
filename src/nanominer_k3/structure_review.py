from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .screening import materialize_two_folders, validate_partition_sources


_DECISIONS = {"keep", "needs_review", "exclude"}
_CONFIDENCE = {"high", "medium", "low"}
_OWNERSHIP = {"primary", "secondary", "mixed", "unknown", "none"}
_LEVELS = {
    "complete_structure",
    "partial_crystallographic_data",
    "explicit_structure_discussion",
    "phase_or_indexing_assignment",
    "none",
    "ambiguous",
}
_INFORMATION_TYPES = {
    "atomic_coordinates",
    "wyckoff_sites",
    "occupancy",
    "space_group",
    "unit_cell_complete",
    "unit_cell_partial",
    "lattice_parameter",
    "crystal_system",
    "bravais_lattice",
    "crystal_phase_or_form",
    "structure_model",
    "structure_determination",
    "structure_refinement",
    "structure_comparison",
    "diffraction_indexing",
    "hkl_planes",
    "other_explicit_crystal_structure",
}
_REASON_CODE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def apply_structure_relevance_reviews(
    *,
    manifest_path: Path,
    review_paths: Sequence[Path],
    output_dir: Path,
    pdf_dir: Path | None = None,
    partition_root: Path | None = None,
    copy_partitions: bool = False,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Apply a recall-oriented crystal-structure layer to strict exclusions.

    The input manifest is never overwritten. Existing strict ``keep`` records
    remain kept. Every strict ``exclude`` must receive a separate topic review
    when ``require_complete`` is true. The resulting ``final_decision`` answers
    whether the paper contains explicit crystal-structure or unit-cell content;
    ``strict_final_decision`` preserves the earlier direct-extractability gate.
    """
    if copy_partitions and pdf_dir is None:
        raise ValueError("pdf_dir is required when copy_partitions is enabled")

    baseline_path = manifest_path.expanduser().resolve()
    baseline = _read_jsonl(baseline_path)
    if not baseline:
        raise ValueError("The screening manifest is empty")

    baseline_paths: dict[str, tuple[int, str]] = {}
    excluded_records: list[dict[str, Any]] = []
    for position, record in enumerate(baseline, start=1):
        relative_path = _relative_path(record, source="screening manifest")
        key = _key(relative_path)
        if key in baseline_paths:
            previous_position, previous_path = baseline_paths[key]
            raise ValueError(
                "Duplicate screening-manifest relative_path at records "
                f"{previous_position} and {position}: "
                f"{previous_path} / {relative_path}"
            )
        baseline_paths[key] = (position, relative_path)
        decision = record.get("final_decision")
        if decision not in _DECISIONS:
            raise ValueError(
                f"Invalid baseline final_decision for {relative_path}: {decision}"
            )
        if decision == "exclude":
            excluded_records.append(record)

    targets: dict[str, tuple[int, dict[str, Any]]] = {}
    for review_index, record in enumerate(
        sorted(
            excluded_records,
            key=lambda item: str(item["relative_path"]).casefold(),
        ),
        start=1,
    ):
        targets[_key(str(record["relative_path"]))] = (review_index, record)

    resolved_review_paths = [path.expanduser().resolve() for path in review_paths]
    if not resolved_review_paths:
        raise ValueError("At least one structure-review JSONL file is required")
    reviews: dict[str, dict[str, Any]] = {}
    for path in resolved_review_paths:
        for review in _read_jsonl(path):
            relative_path = _validate_review(review)
            key = _key(relative_path)
            if key in reviews:
                raise ValueError(f"Duplicate structure review for {relative_path}")
            target = targets.get(key)
            if target is None:
                raise ValueError(
                    "Structure reviews may only target baseline exclude records: "
                    f"{relative_path}"
                )
            expected_index, baseline_record = target
            if relative_path != str(baseline_record["relative_path"]):
                raise ValueError(
                    "Review path casing or separators differ from the manifest: "
                    f"{relative_path}"
                )
            supplied_index = review.get("review_index")
            if supplied_index != expected_index:
                raise ValueError(
                    f"Review index mismatch for {relative_path}: "
                    f"expected {expected_index}, got {supplied_index}"
                )
            _validate_review_against_baseline(review, baseline_record)
            reviews[key] = dict(review)

    missing = [
        str(record["relative_path"])
        for key, (_, record) in targets.items()
        if key not in reviews
    ]
    if require_complete and missing:
        preview = ", ".join(missing[:3])
        raise ValueError(
            f"Missing {len(missing)} exclude decisions; first: {preview}"
        )

    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    merged: list[dict[str, Any]] = []
    transitions: Counter[str] = Counter()
    for baseline_record in baseline:
        record = dict(baseline_record)
        strict_decision = str(record["final_decision"])
        record["strict_final_decision"] = strict_decision
        review = reviews.get(_key(str(record["relative_path"])))
        if review is not None:
            topic_decision = str(review["topic_decision"])
            record["structure_relevance_review"] = review
            record["structure_information_level"] = review[
                "structure_information_level"
            ]
            record["structure_information_types"] = review["information_types"]
            record["structure_ownership"] = review["ownership"]
            record["topic_decision"] = topic_decision
            record["final_decision"] = topic_decision
            record["confidence"] = review["confidence"]
            record["decision_reason"] = "structure_review_" + review["reason_code"]
            record["reviewed_at_utc"] = reviewed_at
            transitions[f"{strict_decision}->{topic_decision}"] += 1
        elif strict_decision == "keep":
            record["structure_information_level"] = "complete_structure"
            record["structure_information_types"] = _strict_information_types(record)
            record["structure_ownership"] = "primary"
            record["topic_decision"] = "keep"
            record["final_decision"] = "keep"
            transitions["keep->keep"] += 1
        else:
            record["structure_information_level"] = "ambiguous"
            record["structure_information_types"] = []
            record["structure_ownership"] = "unknown"
            record["topic_decision"] = "needs_review"
            record["final_decision"] = "needs_review"
            transitions[f"{strict_decision}->needs_review"] += 1
        record["decision_rules_version"] = (
            str(record.get("decision_rules_version", "unknown"))
            + "+structure_relevance_review_v1"
        )
        merged.append(record)

    destination = output_dir.expanduser().resolve()
    manifest_output = destination / "screening_manifest.structure-reviewed.jsonl"
    review_output = destination / "structure_review_overrides.jsonl"
    csv_output = destination / "晶体结构相关性复核清单.csv"
    report_output = destination / "晶体结构全面复核报告.md"
    summary_path = destination / "structure_review_summary.json"
    list_outputs = [
        destination / "结构相关_保留文献.txt",
        destination / "结构相关_待确认文献.txt",
        destination / "结构相关_排除文献.txt",
    ]
    input_paths = {baseline_path, *resolved_review_paths}
    output_paths = {
        path.resolve()
        for path in (
            manifest_output,
            review_output,
            csv_output,
            report_output,
            summary_path,
            *list_outputs,
        )
    }
    collisions = sorted(str(path) for path in input_paths & output_paths)
    if collisions:
        raise ValueError(
            "Structure-review output would overwrite an input file: "
            + ", ".join(collisions)
        )
    if destination.exists():
        raise ValueError(
            "Structure-review output directory must not already exist: "
            f"{destination}"
        )

    if copy_partitions:
        source_root = pdf_dir.expanduser().resolve()
        target_root = (
            partition_root if partition_root is not None else pdf_dir
        ).expanduser().resolve()
        for label, root in (
            ("PDF source directory", source_root),
            ("partition root", target_root),
        ):
            if _paths_overlap(destination, root):
                raise ValueError(
                    "Structure-review output directory must not overlap the "
                    f"{label}: {destination} / {root}"
                )
        validate_partition_sources(records=merged, pdf_dir=pdf_dir)

    final_counts = Counter(str(record["final_decision"]) for record in merged)
    level_counts = Counter(
        str(record["structure_information_level"]) for record in merged
    )
    ownership_counts = Counter(
        str(record["structure_ownership"]) for record in merged
    )
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "review_method": "codex_local_fulltext_structure_relevance_v1",
        "reviewed_at_utc": reviewed_at,
        "baseline_manifest": str(baseline_path),
        "baseline_manifest_sha256": _sha256(baseline_path),
        "review_files": [str(path) for path in resolved_review_paths],
        "review_file_sha256": {
            str(path): _sha256(path) for path in resolved_review_paths
        },
        "baseline_records": len(baseline),
        "baseline_exclude": len(targets),
        "reviews_applied": len(reviews),
        "missing_reviews": len(missing),
        "transitions": dict(sorted(transitions.items())),
        "final_counts": {
            decision: final_counts.get(decision, 0)
            for decision in ("keep", "needs_review", "exclude")
        },
        "structure_information_levels": dict(sorted(level_counts.items())),
        "structure_ownership": dict(sorted(ownership_counts.items())),
        "fulltext_checked": sum(
            review.get("fulltext_checked") is True for review in reviews.values()
        ),
        "visual_checked": sum(
            review.get("visual_checked") is True for review in reviews.values()
        ),
        "k3_or_external_api_used": False,
        "source_files_modified": False,
        "output_manifest": str(manifest_output),
        "review_overrides": str(review_output),
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=destination.parent,
        )
    )
    try:
        _write_jsonl(staging / manifest_output.name, merged)
        _write_jsonl(
            staging / review_output.name,
            sorted(reviews.values(), key=lambda item: int(item["review_index"])),
        )
        _write_csv(staging / csv_output.name, merged)
        _write_decision_lists(staging, merged)
        _write_report(staging / report_output.name, merged, summary)

        if copy_partitions:
            root = partition_root if partition_root is not None else pdf_dir
            summary["partition"] = materialize_two_folders(
                records=merged,
                pdf_dir=pdf_dir,
                partition_root=root,
            )

        (staging / summary_path.name).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            raise ValueError(
                "Structure-review output directory appeared during the run: "
                f"{destination}"
            )
        staging.replace(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return summary


def _validate_review(review: Mapping[str, Any]) -> str:
    relative_path = _relative_path(review, source="structure review")
    decision = review.get("topic_decision")
    if decision not in _DECISIONS:
        raise ValueError(f"Invalid topic_decision for {relative_path}: {decision}")
    level = review.get("structure_information_level")
    if level not in _LEVELS:
        raise ValueError(
            f"Invalid structure_information_level for {relative_path}: {level}"
        )
    ownership = review.get("ownership")
    if ownership not in _OWNERSHIP:
        raise ValueError(f"Invalid ownership for {relative_path}: {ownership}")
    confidence = review.get("confidence")
    if confidence not in _CONFIDENCE:
        raise ValueError(f"Invalid confidence for {relative_path}: {confidence}")
    reason_code = review.get("reason_code")
    if not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code):
        raise ValueError(f"Invalid reason_code for {relative_path}: {reason_code}")
    rationale = review.get("rationale_zh")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError(f"Missing rationale_zh for {relative_path}")
    if review.get("fulltext_checked") is not True:
        raise ValueError(f"fulltext_checked must be true for {relative_path}")
    if not isinstance(review.get("visual_checked"), bool):
        raise ValueError(f"visual_checked must be boolean for {relative_path}")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError(f"Missing reviewer for {relative_path}")
    review_index = review.get("review_index")
    if (
        not isinstance(review_index, int)
        or isinstance(review_index, bool)
        or review_index < 1
    ):
        raise ValueError(f"Invalid review_index for {relative_path}: {review_index}")

    pages = review.get("evidence_pages")
    quotes = review.get("evidence_quotes")
    information_types = review.get("information_types")
    if not isinstance(pages, list) or any(
        not isinstance(page, int) or isinstance(page, bool) or page < 1
        for page in pages
    ):
        raise ValueError(f"Invalid evidence_pages for {relative_path}")
    if not isinstance(quotes, list) or any(not isinstance(item, str) for item in quotes):
        raise ValueError(f"Invalid evidence_quotes for {relative_path}")
    if not isinstance(information_types, list) or any(
        item not in _INFORMATION_TYPES for item in information_types
    ):
        raise ValueError(f"Invalid information_types for {relative_path}")
    if len(set(information_types)) != len(information_types):
        raise ValueError(f"Duplicate information_types for {relative_path}")

    has_evidence = bool(pages) and any(quote.strip() for quote in quotes)
    if decision == "keep":
        if level in {"none", "ambiguous"}:
            raise ValueError(f"A kept paper needs a positive level: {relative_path}")
        if ownership == "none":
            raise ValueError(f"A kept paper needs non-none ownership: {relative_path}")
        if not information_types or not has_evidence:
            raise ValueError(f"A kept paper needs typed page evidence: {relative_path}")
    elif decision == "exclude":
        if level != "none" or information_types:
            raise ValueError(
                f"An excluded paper must use level none and no information types: "
                f"{relative_path}"
            )
    else:
        if level != "ambiguous":
            raise ValueError(
                f"A needs_review paper must use level ambiguous: {relative_path}"
            )
    return relative_path


def _validate_review_against_baseline(
    review: Mapping[str, Any], baseline: Mapping[str, Any]
) -> None:
    relative_path = str(review["relative_path"])
    page_count = baseline.get("page_count")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 0:
        raise ValueError(f"Invalid baseline page_count for {relative_path}: {page_count}")
    pages = review["evidence_pages"]
    if any(page > page_count for page in pages):
        raise ValueError(
            f"Review evidence page exceeds page_count for {relative_path}: "
            f"{max(pages)} > {page_count}"
        )
    source_sha256 = review.get("source_sha256")
    if not isinstance(source_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", source_sha256
    ):
        raise ValueError(
            f"Missing or invalid review source_sha256 for {relative_path}"
        )
    baseline_sha256 = str(baseline.get("sha256", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", baseline_sha256):
        raise ValueError(f"Missing or invalid baseline SHA-256 for {relative_path}")
    if source_sha256.lower() != baseline_sha256:
        raise ValueError(
            f"Review source_sha256 differs from the manifest for {relative_path}"
        )


def _strict_information_types(record: Mapping[str, Any]) -> list[str]:
    categories = set(record.get("qualifying_core_categories", []))
    types: list[str] = []
    if "atomic_coordinates" in categories:
        types.append("atomic_coordinates")
    if "space_group" in categories:
        types.append("space_group")
    if "numeric_unit_cell" in categories:
        types.append("unit_cell_complete")
    review = record.get("codex_fulltext_review") or {}
    reason = str(review.get("reason_code", ""))
    if not types and "wyckoff" in reason:
        types.extend(["wyckoff_sites", "occupancy"])
    if not types:
        types.append("other_explicit_crystal_structure")
    return types


def _write_csv(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "relative_path",
        "strict_final_decision",
        "topic_decision",
        "final_decision",
        "structure_information_level",
        "structure_information_types",
        "structure_ownership",
        "confidence",
        "evidence_pages",
        "rationale_zh",
        "reviewer",
        "sha256",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            review = record.get("structure_relevance_review") or {}
            writer.writerow(
                {
                    "relative_path": record.get("relative_path", ""),
                    "strict_final_decision": record.get("strict_final_decision", ""),
                    "topic_decision": record.get("topic_decision", ""),
                    "final_decision": record.get("final_decision", ""),
                    "structure_information_level": record.get(
                        "structure_information_level", ""
                    ),
                    "structure_information_types": ";".join(
                        record.get("structure_information_types", [])
                    ),
                    "structure_ownership": record.get("structure_ownership", ""),
                    "confidence": record.get("confidence", ""),
                    "evidence_pages": ";".join(
                        str(page) for page in review.get("evidence_pages", [])
                    ),
                    "rationale_zh": review.get("rationale_zh", ""),
                    "reviewer": review.get("reviewer", ""),
                    "sha256": record.get("sha256", ""),
                }
            )


def _write_decision_lists(
    output_dir: Path, records: Sequence[Mapping[str, Any]]
) -> None:
    filenames = {
        "keep": "结构相关_保留文献.txt",
        "needs_review": "结构相关_待确认文献.txt",
        "exclude": "结构相关_排除文献.txt",
    }
    for decision, filename in filenames.items():
        selected = [
            str(record["relative_path"])
            for record in records
            if record.get("final_decision") == decision
        ]
        (output_dir / filename).write_text(
            "\n".join(selected) + ("\n" if selected else ""),
            encoding="utf-8-sig",
        )


def _write_report(
    path: Path, records: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]
) -> None:
    counts = summary["final_counts"]
    transitions = summary["transitions"]
    newly_kept = [
        record
        for record in records
        if record.get("strict_final_decision") == "exclude"
        and record.get("topic_decision") == "keep"
    ]
    pending = [
        record for record in records if record.get("topic_decision") == "needs_review"
    ]
    lines = [
        "# 晶体结构与晶胞信息全面复核报告",
        "",
        "本轮使用本地 PDF 全文、必要的 OCR 和关键页目视检查；未调用 Kimi 或其他外部 API。",
        "严格的可直接抽取结论被保留在 `strict_final_decision`，本报告的 `topic_decision` 回答文章是否明确包含晶体结构或晶胞信息。",
        "",
        "## 新口径",
        "",
        "只要摘要或正文把具体材料、样品或晶相与明确的晶体/晶胞事实绑定，就纳入结构相关集合。原子坐标、Wyckoff 位点、空间群、完整或部分晶胞参数、晶系/点阵/晶型指认、结构模型或精修，以及用于结构定相的衍射索引都算。综述、引述数据和导入模型仍可保留，但单独标注信息归属。仅有结晶度、结晶动力学、DSC、一般 XRD/WAXS、层片、球晶、形貌或取向而没有结构事实的不纳入。",
        "",
        "## 结果",
        "",
        f"- 全库：{summary['baseline_records']} 篇",
        f"- 旧排除项逐篇复核：{summary['reviews_applied']} 篇",
        f"- 旧排除 → 结构相关：{transitions.get('exclude->keep', 0)} 篇",
        f"- 旧排除 → 仍待确认：{transitions.get('exclude->needs_review', 0)} 篇",
        f"- 旧排除 → 仍排除：{transitions.get('exclude->exclude', 0)} 篇",
        f"- 最终结构相关：{counts['keep']}，待确认：{counts['needs_review']}，排除：{counts['exclude']}",
        "",
        "## 信息层级",
        "",
    ]
    for level, count in summary["structure_information_levels"].items():
        lines.append(f"- `{level}`：{count} 篇")
    lines.extend(["", "## 新纳入文献", ""])
    if newly_kept:
        for record in newly_kept:
            review = record["structure_relevance_review"]
            pages = ", ".join(str(page) for page in review["evidence_pages"])
            lines.append(
                f"- `{record['relative_path']}`（第 {pages} 页；"
                f"{review['ownership']}）：{review['rationale_zh']}"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 仍待确认", ""])
    if pending:
        for record in pending:
            review = record.get("structure_relevance_review") or {}
            lines.append(
                f"- `{record['relative_path']}`："
                f"{review.get('rationale_zh', '尚未取得足够全文证据。')}"
            )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 可追溯文件",
            "",
            "- `structure_review_overrides.jsonl`：804 篇旧排除项的逐篇证据和判定。",
            "- `screening_manifest.structure-reviewed.jsonl`：保留严格判定并叠加结构相关性的全库清单。",
            "- `晶体结构相关性复核清单.csv`：便于人工查看的汇总表。",
            "- 旧的严格复核清单和源 PDF 均未被覆盖。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _relative_path(record: Mapping[str, Any], *, source: str) -> str:
    value = record.get("relative_path")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing relative_path in {source}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe relative_path in {source}: {value}")
    return value


def _key(relative_path: str) -> str:
    return relative_path.replace("\\", "/").casefold()


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(
        first
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"JSONL file not found: {path}")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Expected a JSON object at {path}:{line_number}")
        records.append(record)
    return records


def _write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
