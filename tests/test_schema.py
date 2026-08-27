from __future__ import annotations

import json
from unittest import TestCase

from jsonschema import Draft202012Validator

from nanominer_k3.schema import EXTRACTION_SCHEMA, parse_and_guard


class SchemaGuardTests(TestCase):
    def test_response_schema_is_valid_draft_2020_12(self) -> None:
        Draft202012Validator.check_schema(EXTRACTION_SCHEMA)

    def test_program_forces_candidate_review_boundary(self) -> None:
        payload = {
            "profile": "pe_crystal",
            "summary": "candidate",
            "documents": [],
            "records": [
                {
                    "record_type": "crystal_structure",
                    "material": "PE",
                    "sample": None,
                    "fields": [
                        {
                            "name": "crystal_system",
                            "value_text": "orthorhombic",
                            "value_number": None,
                            "unit": None,
                            "source_value": "orthorhombic",
                            "value_type": "explicit",
                        }
                    ],
                    "evidence": [
                        {
                            "file_role": "article",
                            "page": 1,
                            "modality": "text",
                            "quote": "orthorhombic",
                            "locator": None,
                            "bbox_pt_top_left": None,
                        }
                    ],
                    "review_status": "gold_reviewed",
                    "notes": [],
                }
            ],
            "unresolved": [],
        }
        guarded = parse_and_guard(
            json.dumps(payload), expected_profile="pe_crystal"
        )
        self.assertEqual(
            "needs_review", guarded["records"][0]["review_status"]
        )
