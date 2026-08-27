from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping


_NUMBER = re.compile(
    r"(?<![\w.])[-+]?(?:\d+(?:[.,·'’]\d+)?|\.\d+)(?:[eE][-+]?\d+)?"
)
_OLD_DECIMAL = re.compile(r"(?<![\d.])(\d+)-(\d{2,3})(?![\d.])")


def bind_trusted_documents(
    extraction: dict[str, Any], manifest: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Replace model-repeated document metadata with corpus-derived values."""
    trusted = [
        {
            "file_role": str(document["role"]),
            "source_file": str(document["source_file"]),
            "page_count": int(document["page_count"]),
        }
        for document in manifest
    ]
    extraction["documents"] = trusted
    return trusted


def validate_evidence_locations(
    extraction: Mapping[str, Any], documents: Iterable[Mapping[str, Any]]
) -> None:
    page_counts = {
        str(document["file_role"]): int(document["page_count"])
        for document in documents
    }
    for record_index, record in enumerate(extraction.get("records", [])):
        for evidence_index, evidence in enumerate(record.get("evidence", [])):
            role = str(evidence.get("file_role", ""))
            page = evidence.get("page")
            if role not in page_counts:
                raise ValueError(
                    f"Record {record_index} evidence {evidence_index} cites "
                    f"unknown file role {role!r}"
                )
            if not isinstance(page, int) or not 1 <= page <= page_counts[role]:
                raise ValueError(
                    f"Record {record_index} evidence {evidence_index} cites page "
                    f"{page!r} outside 1..{page_counts[role]} for {role}"
                )


def numeric_source_alignment(extraction: Mapping[str, Any]) -> dict[str, Any]:
    """Flag numeric fields whose machine-readable value lacks lexical support.

    This is a conservative triage check, not scientific validation. A warning can
    indicate an OCR/table-transcription disagreement that needs visual review.
    """
    checked = 0
    warnings: list[dict[str, Any]] = []
    for record_index, record in enumerate(extraction.get("records", [])):
        for field_index, field in enumerate(record.get("fields", [])):
            expected = field.get("value_number")
            if expected is None or isinstance(expected, bool):
                continue
            checked += 1
            source_numbers = _numbers(str(field.get("source_value", "")))
            if not source_numbers:
                warnings.append(
                    _warning(
                        record_index,
                        field_index,
                        record,
                        field,
                        expected,
                        "no_numeric_lexeme",
                        [],
                    )
                )
                continue
            if not any(_close(float(expected), candidate) for candidate in source_numbers):
                warnings.append(
                    _warning(
                        record_index,
                        field_index,
                        record,
                        field,
                        expected,
                        "numeric_mismatch",
                        source_numbers[:12],
                    )
                )
    return {
        "status": "warnings" if warnings else "pass",
        "checked_numeric_fields": checked,
        "warning_count": len(warnings),
        "warnings": warnings,
        "interpretation": (
            "Triage only; warnings require source-page review and do not prove "
            "that either OCR text or the extracted value is correct."
        ),
    }


def _numbers(source: str) -> list[float]:
    candidates: list[float] = []
    for match in _NUMBER.finditer(source.replace("−", "-")):
        token = (
            match.group(0)
            .replace(",", ".")
            .replace("·", ".")
            .replace("'", ".")
            .replace("’", ".")
        )
        try:
            candidates.append(float(token))
        except ValueError:
            continue
    # Older scanned papers commonly OCR decimal points as hyphens (for example,
    # 7-40). Add those interpretations without replacing range punctuation.
    for match in _OLD_DECIMAL.finditer(source):
        candidates.append(float(f"{match.group(1)}.{match.group(2)}"))
    return list(dict.fromkeys(candidates))


def _close(expected: float, observed: float) -> bool:
    return math.isclose(expected, observed, rel_tol=1e-7, abs_tol=1e-9)


def _warning(
    record_index: int,
    field_index: int,
    record: Mapping[str, Any],
    field: Mapping[str, Any],
    expected: Any,
    reason: str,
    source_numbers: list[float],
) -> dict[str, Any]:
    return {
        "record_index": record_index,
        "record_type": str(record.get("record_type", "")),
        "field_index": field_index,
        "field_name": str(field.get("name", "")),
        "value_number": expected,
        "reason": reason,
        "source_numbers": source_numbers,
    }
