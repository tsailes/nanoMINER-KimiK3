from __future__ import annotations

from copy import deepcopy
from unittest import TestCase

from nanominer_k3.validation import (
    bind_trusted_documents,
    numeric_source_alignment,
    validate_evidence_locations,
)


class DeterministicValidationTests(TestCase):
    def setUp(self) -> None:
        self.extraction = {
            "documents": [
                {
                    "file_role": "hallucinated",
                    "source_file": "wrong.pdf",
                    "page_count": 999,
                }
            ],
            "records": [
                {
                    "record_type": "crystal_structure",
                    "fields": [
                        {
                            "name": "a_axis",
                            "value_number": 7.4,
                            "source_value": "a = 7-40 A",
                        },
                        {
                            "name": "d_211",
                            "value_number": 1.925,
                            "source_value": "211 1.935 1.924",
                        },
                        {
                            "name": "d_020",
                            "value_number": 2.467,
                            "source_value": "2'467 2'467 020 226",
                        },
                    ],
                    "evidence": [{"file_role": "article", "page": 3}],
                }
            ],
        }
        self.manifest = [
            {"role": "article", "source_file": "1939.pdf", "page_count": 10}
        ]

    def test_binds_corpus_metadata_and_accepts_real_page(self) -> None:
        trusted = bind_trusted_documents(self.extraction, self.manifest)
        validate_evidence_locations(self.extraction, trusted)
        self.assertEqual(
            [
                {
                    "file_role": "article",
                    "source_file": "1939.pdf",
                    "page_count": 10,
                }
            ],
            self.extraction["documents"],
        )

    def test_rejects_out_of_range_evidence_page(self) -> None:
        extraction = deepcopy(self.extraction)
        extraction["records"][0]["evidence"][0]["page"] = 11
        trusted = bind_trusted_documents(extraction, self.manifest)
        with self.assertRaisesRegex(ValueError, "outside 1..10"):
            validate_evidence_locations(extraction, trusted)

    def test_flags_ocr_mismatch_but_understands_old_decimal_hyphen(self) -> None:
        report = numeric_source_alignment(self.extraction)
        self.assertEqual(3, report["checked_numeric_fields"])
        self.assertEqual(1, report["warning_count"])
        warning = report["warnings"][0]
        self.assertEqual("d_211", warning["field_name"])
        self.assertEqual("numeric_mismatch", warning["reason"])
