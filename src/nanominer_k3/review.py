from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .screening import materialize_two_folders, validate_partition_sources


_DECISIONS = {"keep", "needs_review", "exclude"}
_CONFIDENCE = {"high", "medium", "low"}
_OWNERSHIP = {"primary", "secondary", "none", "unresolved"}
_REASON_CODE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def apply_fulltext_reviews(
    *,
    manifest_path: Path,
    review_paths: Sequence[Path],
    output_dir: Path,
    pdf_dir: Path | None = None,
    partition_root: Path | None = None,
    copy_partitions: bool = False,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Apply auditable full-text decisions only to ``needs_review`` records."""
    if copy_partitions and pdf_dir is None:
        raise ValueError("pdf_dir is required when copy_partitions is enabled")

    baseline_path = manifest_path.expanduser().resolve()
    baseline = _read_jsonl(baseline_path)
    if not baseline:
        raise ValueError("The screening manifest is empty")

    targets: dict[str, tuple[int, dict[str, Any]]] = {}
    baseline_paths: dict[str, tuple[int, str]] = {}
    review_index = 0
    for position, record in enumerate(baseline):
        relative_path = _relative_path(record, source="screening manifest")
        path_key = _key(relative_path)
        previous = baseline_paths.get(path_key)
        if previous is not None:
            previous_position, previous_path = previous
            raise ValueError(
                "Duplicate screening-manifest relative_path at records "
                f"{previous_position} and {position + 1}: "
                f"{previous_path} / {relative_path}"
            )
        baseline_paths[path_key] = (position + 1, relative_path)
        if record.get("final_decision") == "needs_review":
            review_index += 1
            targets[path_key] = (review_index, record)

    reviews: dict[str, dict[str, Any]] = {}
    resolved_review_paths = [path.expanduser().resolve() for path in review_paths]
    if not resolved_review_paths:
        raise ValueError("At least one review JSONL file is required")
    for path in resolved_review_paths:
        for record in _read_jsonl(path):
            relative_path = _validate_review(record)
            key = _key(relative_path)
            if key in reviews:
                raise ValueError(f"Duplicate review for {relative_path}")
            target = targets.get(key)
            if target is None:
                raise ValueError(
                    "Reviews may only target baseline needs_review records: "
                    f"{relative_path}"
                )
            expected_index, baseline_record = target
            if relative_path != str(baseline_record["relative_path"]):
                raise ValueError(
                    "Review path casing or separators differ from the manifest: "
                    f"{relative_path}"
                )
            if record.get("original_decision") != "needs_review":
                raise ValueError(
                    f"Review original_decision must be needs_review: {relative_path}"
                )
            supplied_index = record.get("review_index")
            if supplied_index is not None and supplied_index != expected_index:
                raise ValueError(
                    f"Review index mismatch for {relative_path}: "
                    f"expected {expected_index}, got {supplied_index}"
                )
            _validate_review_against_baseline(record, baseline_record)
            reviews[key] = dict(record)

    missing = [
        str(record["relative_path"])
        for key, (_, record) in targets.items()
        if key not in reviews
    ]
    if require_complete and missing:
        preview = ", ".join(missing[:3])
        raise ValueError(
            f"Missing {len(missing)} needs_review decisions; first: {preview}"
        )

    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    merged: list[dict[str, Any]] = []
    transitions: Counter[str] = Counter()
    for baseline_record in baseline:
        record = dict(baseline_record)
        path_key = _key(str(record["relative_path"]))
        review = reviews.get(path_key)
        if review is not None:
            before = str(record["final_decision"])
            after = str(review["review_decision"])
            record["pre_review_decision"] = before
            record["codex_fulltext_review"] = review
            record["reviewed_at_utc"] = reviewed_at
            record["final_decision"] = after
            record["confidence"] = review["confidence"]
            record["decision_reason"] = "codex_review_" + review["reason_code"]
            record["decision_rules_version"] = "1.1+codex_fulltext_review_v1"
            transitions[f"{before}->{after}"] += 1
        merged.append(record)

    baseline_sha256 = _sha256(baseline_path)
    review_sha256 = {str(path): _sha256(path) for path in resolved_review_paths}

    destination = output_dir.expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"Review output path is not a directory: {destination}")
    manifest_output = destination / "screening_manifest.reviewed.jsonl"
    review_output = destination / "codex_review_overrides.jsonl"
    csv_output = destination / "筛查清单_复核后.csv"
    report_output = destination / "复核报告.md"
    summary_path = destination / "review_summary.json"
    list_outputs = [
        destination / "复核后保留文献.txt",
        destination / "复核后待确认文献.txt",
        destination / "复核后排除文献.txt",
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
            "Review output would overwrite an input file: " + ", ".join(collisions)
        )

    if copy_partitions:
        validate_partition_sources(records=merged, pdf_dir=pdf_dir)

    destination.mkdir(parents=True, exist_ok=True)
    _write_jsonl(manifest_output, merged)
    _write_jsonl(
        review_output,
        sorted(
            reviews.values(), key=lambda item: int(item.get("review_index") or 0)
        ),
    )

    final_counts = Counter(str(record["final_decision"]) for record in merged)
    confidence_counts = Counter(
        str(review["confidence"]) for review in reviews.values()
    )
    ownership_counts = Counter(str(review["ownership"]) for review in reviews.values())
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "review_method": "codex_local_fulltext_manual_v1",
        "reviewed_at_utc": reviewed_at,
        "baseline_manifest": str(baseline_path),
        "baseline_manifest_sha256": baseline_sha256,
        "review_files": [str(path) for path in resolved_review_paths],
        "review_file_sha256": review_sha256,
        "baseline_records": len(baseline),
        "baseline_needs_review": len(targets),
        "reviews_applied": len(reviews),
        "missing_reviews": len(missing),
        "transitions": dict(sorted(transitions.items())),
        "final_counts": {
            decision: final_counts.get(decision, 0)
            for decision in ("keep", "needs_review", "exclude")
        },
        "review_confidence": dict(sorted(confidence_counts.items())),
        "review_ownership": dict(sorted(ownership_counts.items())),
        "fulltext_checked": sum(
            review.get("fulltext_checked") is True for review in reviews.values()
        ),
        "visual_checked": sum(
            review.get("visual_checked") is True for review in reviews.values()
        ),
        "k3_or_external_api_used": False,
        "output_manifest": str(manifest_output),
        "review_overrides": str(review_output),
    }

    _write_csv(csv_output, merged)
    _write_decision_lists(destination, merged)
    _write_report(report_output, merged, summary)

    if copy_partitions:
        root = partition_root if partition_root is not None else pdf_dir
        summary["partition"] = materialize_two_folders(
            records=merged,
            pdf_dir=pdf_dir,
            partition_root=root,
        )

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _validate_review(record: Mapping[str, Any]) -> str:
    relative_path = _relative_path(record, source="review file")
    decision = record.get("review_decision")
    if decision not in _DECISIONS:
        raise ValueError(f"Invalid review_decision for {relative_path}: {decision}")
    confidence = record.get("confidence")
    if confidence not in _CONFIDENCE:
        raise ValueError(f"Invalid confidence for {relative_path}: {confidence}")
    ownership = record.get("ownership")
    if ownership not in _OWNERSHIP:
        raise ValueError(f"Invalid ownership for {relative_path}: {ownership}")
    reason_code = record.get("reason_code")
    if not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code):
        raise ValueError(f"Invalid reason_code for {relative_path}: {reason_code}")
    if not isinstance(record.get("rationale_zh"), str) or not record[
        "rationale_zh"
    ].strip():
        raise ValueError(f"Missing rationale_zh for {relative_path}")
    if record.get("fulltext_checked") is not True:
        raise ValueError(f"fulltext_checked must be true for {relative_path}")
    if not isinstance(record.get("visual_checked"), bool):
        raise ValueError(f"visual_checked must be boolean for {relative_path}")
    if not isinstance(record.get("reviewer"), str) or not record["reviewer"].strip():
        raise ValueError(f"Missing reviewer for {relative_path}")
    review_index = record.get("review_index")
    if review_index is not None and (
        not isinstance(review_index, int)
        or isinstance(review_index, bool)
        or review_index < 1
    ):
        raise ValueError(f"Invalid review_index for {relative_path}: {review_index}")

    pages = record.get("evidence_pages")
    if not isinstance(pages, list) or any(
        not isinstance(page, int) or isinstance(page, bool) or page < 1
        for page in pages
    ):
        raise ValueError(f"Invalid evidence_pages for {relative_path}")
    quotes = record.get("evidence_quotes")
    if not isinstance(quotes, list) or any(not isinstance(item, str) for item in quotes):
        raise ValueError(f"Invalid evidence_quotes for {relative_path}")
    if decision == "keep":
        if ownership != "primary":
            raise ValueError(f"A keep review must have primary ownership: {relative_path}")
        if not pages or not any(quote.strip() for quote in quotes):
            raise ValueError(f"A keep review needs page evidence: {relative_path}")
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"JSONL file not found: {path}")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
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


def _write_csv(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "relative_path",
        "local_decision",
        "pre_review_decision",
        "final_decision",
        "confidence",
        "decision_reason",
        "reviewer",
        "evidence_pages",
        "rationale_zh",
        "sha256",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            review = record.get("codex_fulltext_review") or {}
            writer.writerow(
                {
                    "relative_path": record.get("relative_path", ""),
                    "local_decision": record.get("local_decision", ""),
                    "pre_review_decision": record.get("pre_review_decision", ""),
                    "final_decision": record.get("final_decision", ""),
                    "confidence": record.get("confidence", ""),
                    "decision_reason": record.get("decision_reason", ""),
                    "reviewer": review.get("reviewer", ""),
                    "evidence_pages": ";".join(
                        str(page) for page in review.get("evidence_pages", [])
                    ),
                    "rationale_zh": review.get("rationale_zh", ""),
                    "sha256": record.get("sha256", ""),
                }
            )


def _write_decision_lists(
    output_dir: Path, records: Sequence[Mapping[str, Any]]
) -> None:
    names = {
        "keep": "复核后保留文献.txt",
        "needs_review": "复核后待确认文献.txt",
        "exclude": "复核后排除文献.txt",
    }
    for decision, filename in names.items():
        paths = [
            str(record["relative_path"])
            for record in records
            if record.get("final_decision") == decision
        ]
        (output_dir / filename).write_text(
            "\n".join(paths) + ("\n" if paths else ""), encoding="utf-8-sig"
        )


def _write_report(
    path: Path, records: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]
) -> None:
    counts = summary["final_counts"]
    transitions = summary["transitions"]
    reviewed_keeps = [
        record
        for record in records
        if record.get("pre_review_decision") == "needs_review"
        and record.get("final_decision") == "keep"
    ]
    still_pending = [
        record
        for record in records
        if record.get("pre_review_decision") == "needs_review"
        and record.get("final_decision") == "needs_review"
    ]
    lines = [
        "# Codex 全文复核报告",
        "",
        "本轮仅使用本地 PDF、文本提取、OCR 和原页目视检查；未调用 Kimi 或其他外部 API。",
        "初筛清单保持不变，复核结论以独立覆盖记录和复核后清单保存。",
        "",
        "## 结果",
        "",
        f"- 共复核：{summary['reviews_applied']} 篇",
        f"- 待确认 → 通过：{transitions.get('needs_review->keep', 0)} 篇",
        f"- 待确认 → 排除：{transitions.get('needs_review->exclude', 0)} 篇",
        f"- 仍待确认：{transitions.get('needs_review->needs_review', 0)} 篇",
        f"- 全库复核后：通过 {counts['keep']}，待确认 {counts['needs_review']}，排除 {counts['exclude']}",
        "",
        "## 判据",
        "",
        "通过只适用于原始研究，并且正文明确给出可解析原子坐标，或明确空间群并同时给出数值 a、b、c 晶胞参数；引文转述、综述汇总、导入模拟结构、仅有 XRD/晶型/取向/结晶度信息均不通过。",
        "",
        "## 新增通过",
        "",
    ]
    if reviewed_keeps:
        for record in reviewed_keeps:
            review = record["codex_fulltext_review"]
            pages = ", ".join(str(page) for page in review["evidence_pages"])
            lines.append(
                f"- `{record['relative_path']}`（第 {pages} 页）：{review['rationale_zh']}"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 仍待确认", ""])
    if still_pending:
        for record in still_pending:
            review = record["codex_fulltext_review"]
            lines.append(f"- `{record['relative_path']}`：{review['rationale_zh']}")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 可追溯文件",
            "",
            "- `codex_review_overrides.jsonl`：逐篇复核理由、页码和短证据。",
            "- `screening_manifest.reviewed.jsonl`：应用覆盖后的全库清单。",
            "- `筛查清单_复核后.csv`：便于人工查看的表格。",
            "- 原始 `screening_manifest.jsonl` 未被覆盖。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
