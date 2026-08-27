from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "profile": {"type": "string", "minLength": 1},
        "summary": {"type": "string"},
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "file_role": {"type": "string", "minLength": 1},
                    "source_file": {"type": "string", "minLength": 1},
                    "page_count": {"type": "integer", "minimum": 1},
                },
                "required": ["file_role", "source_file", "page_count"],
            },
        },
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "record_type": {"type": "string", "minLength": 1},
                    "material": {"type": "string", "minLength": 1},
                    "sample": {"type": ["string", "null"]},
                    "fields": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "value_text": {"type": ["string", "null"]},
                                "value_number": {"type": ["number", "null"]},
                                "unit": {"type": ["string", "null"]},
                                "source_value": {"type": "string", "minLength": 1},
                                "value_type": {
                                    "type": "string",
                                    "enum": [
                                        "explicit",
                                        "reported_inside_figure",
                                        "estimated_from_graph",
                                        "calculated",
                                        "inferred",
                                        "unresolved",
                                    ],
                                },
                            },
                            "required": [
                                "name",
                                "value_text",
                                "value_number",
                                "unit",
                                "source_value",
                                "value_type",
                            ],
                        },
                    },
                    "evidence": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "file_role": {"type": "string", "minLength": 1},
                                "page": {"type": "integer", "minimum": 1},
                                "modality": {
                                    "type": "string",
                                    "enum": ["text", "figure", "table", "equation", "scan"],
                                },
                                "quote": {"type": "string", "minLength": 1},
                                "locator": {"type": ["string", "null"]},
                                "bbox_pt_top_left": {
                                    "type": ["array", "null"],
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "items": {"type": "number", "minimum": 0},
                                },
                            },
                            "required": [
                                "file_role",
                                "page",
                                "modality",
                                "quote",
                                "locator",
                                "bbox_pt_top_left",
                            ],
                        },
                    },
                    "review_status": {
                        "type": "string",
                        "enum": ["needs_review"],
                    },
                    "notes": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "record_type",
                    "material",
                    "sample",
                    "fields",
                    "evidence",
                    "review_status",
                    "notes",
                ],
            },
        },
        "unresolved": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["profile", "summary", "documents", "records", "unresolved"],
}


def response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "nanominer_evidence_extraction",
            "strict": True,
            "schema": EXTRACTION_SCHEMA,
        },
    }


def parse_and_guard(content: str, *, expected_profile: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Kimi K3 final response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Kimi K3 final response must be a JSON object")
    if payload.get("profile") != expected_profile:
        raise ValueError(
            f"Profile mismatch: expected {expected_profile!r}, got {payload.get('profile')!r}"
        )
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Extraction records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Record {index} must be an object")
        # This is a programmatic curation boundary, not a model decision.
        record["review_status"] = "needs_review"
        evidence = record.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"Record {index} lacks page-level evidence")
    try:
        Draft202012Validator(EXTRACTION_SCHEMA).validate(payload)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ValueError(
            f"Structured extraction failed local schema validation at {location}: "
            f"{exc.message}"
        ) from exc
    return payload
