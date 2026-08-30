from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

import pymupdf

from nanominer_k3.screening import (
    discover_pdf_files,
    load_kept_relative_paths,
    materialize_two_folders,
    screen_pdf,
)


def _write_pdf(path: Path, pages: list[str]) -> None:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(
            pymupdf.Rect(50, 50, 545, 790),
            text,
            fontsize=10,
        )
    document.save(path)
    document.close()


class FullTextScreeningTests(TestCase):
    def test_atomic_coordinates_from_primary_work_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "primary.pdf"
            _write_pdf(
                pdf,
                [
                    "The crystal structure of polyethylene. We report the refined "
                    "structure in this work. Precise atomic positions are x = 0.038 "
                    "and y = 0.065 for the carbon site. " + "Evidence text. " * 20
                ],
            )
            record = screen_pdf(pdf, root=root)
            self.assertEqual("keep", record["final_decision"])
            self.assertEqual(
                ["atomic_coordinates"], record["qualifying_core_categories"]
            )

    def test_space_group_and_three_axis_cell_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "cell.pdf"
            _write_pdf(
                pdf,
                [
                    "Crystal structure of the present material. We report this work. "
                    "Unit cell parameters are a = 7.40 Angstrom, b = 4.93 Angstrom, "
                    "and c = 2.534 Angstrom. The space group is Pnam. "
                    + "Experimental evidence. " * 20
                ],
            )
            record = screen_pdf(pdf, root=root)
            self.assertEqual("keep", record["final_decision"])
            self.assertEqual(
                ["numeric_unit_cell", "space_group"],
                record["qualifying_core_categories"],
            )

    def test_context_terms_alone_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "orientation.pdf"
            _write_pdf(
                pdf,
                [
                    "The orthorhombic crystal structure is discussed while the (110) "
                    "and (200) reflections describe fibre orientation. "
                    + "Complete readable discussion. " * 20
                ],
            )
            record = screen_pdf(pdf, root=root)
            self.assertEqual("exclude", record["final_decision"])
            self.assertEqual(
                "context_only_without_core_crystallographic_data",
                record["decision_reason"],
            )

    def test_literature_attributed_cell_does_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "imported-cell.pdf"
            _write_pdf(
                pdf,
                [
                    "We report morphology in this work. The crystal structure and unit "
                    "cell parameters were determined by Natta et al.: a = 7.40, "
                    "b = 4.93, c = 2.534. The space group is Pnam. "
                    + "Readable discussion. " * 20
                ],
            )
            record = screen_pdf(pdf, root=root)
            self.assertNotEqual("keep", record["final_decision"])
            self.assertTrue(
                any(
                    item["rejection_reason"] == "secondary_attribution"
                    for item in record["evidence"]
                    if item["strength"] == "strong"
                )
            )

    def test_reference_only_core_terms_do_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "references.pdf"
            _write_pdf(
                pdf,
                ["Readable body without target evidence. " * 20],
                # A late standalone heading establishes the reference boundary.
            )
            document = pymupdf.open(pdf)
            page = document.new_page()
            page.insert_textbox(
                pymupdf.Rect(50, 50, 545, 790),
                "References\nCrystal structure of a cited solid. Atomic coordinates "
                "x = 0.10 and y = 0.20. " + "Citation details. " * 20,
                fontsize=10,
            )
            rewritten = root / "rewritten.pdf"
            document.save(rewritten)
            document.close()
            record = screen_pdf(rewritten, root=root)
            self.assertEqual("exclude", record["final_decision"])
            self.assertGreater(record["reference_only_hit_count"], 0)

    def test_sparse_or_compound_candidate_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "thesis.pdf"
            _write_pdf(
                pdf,
                [
                    "A thesis submitted for the degree. Part I chemistry. Part II "
                    "crystal structure. Structure refinement was performed. "
                    + "Scanned evidence text. " * 20
                ],
            )
            record = screen_pdf(pdf, root=root)
            self.assertEqual("needs_review", record["final_decision"])
            self.assertIn("thesis_or_multipart_document", record["document_flags"])

    def test_broken_font_glyph_text_is_not_safely_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "broken-font.pdf"
            _write_pdf(pdf, [("@@@ ### $$$ %%% 12345 !!! " * 60)])
            record = screen_pdf(pdf, root=root)
            self.assertEqual("needs_review", record["final_decision"])
            self.assertIn("broken_font_glyph_mapping", record["text_quality_flags"])


class PartitionTests(TestCase):
    def test_partition_copies_and_preserves_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            passed = root / "pass.pdf"
            failed = root / "fail.pdf"
            _write_pdf(passed, ["Readable source. " * 20])
            _write_pdf(failed, ["Readable source. " * 20])
            records = [
                {"relative_path": passed.name, "final_decision": "keep"},
                {"relative_path": failed.name, "final_decision": "needs_review"},
            ]
            summary = materialize_two_folders(
                records=records,
                pdf_dir=root,
                partition_root=root,
            )
            self.assertTrue(passed.exists())
            self.assertTrue(failed.exists())
            self.assertTrue((root / "通过" / passed.name).is_file())
            self.assertTrue((root / "未通过" / failed.name).is_file())
            self.assertEqual(1, summary["passed"])
            self.assertEqual(1, summary["failed_including_review"])
            self.assertEqual(
                ["fail.pdf", "pass.pdf"],
                [path.name for path in discover_pdf_files(root)],
            )
            reversed_records = [
                {"relative_path": passed.name, "final_decision": "exclude"},
                {"relative_path": failed.name, "final_decision": "keep"},
            ]
            refreshed = materialize_two_folders(
                records=reversed_records,
                pdf_dir=root,
                partition_root=root,
            )
            self.assertFalse((root / "通过" / passed.name).exists())
            self.assertTrue((root / "未通过" / passed.name).is_file())
            self.assertTrue((root / "通过" / failed.name).is_file())
            self.assertFalse((root / "未通过" / failed.name).exists())
            self.assertEqual(2, refreshed["stale_opposite_removed"])

    def test_manifest_loader_returns_only_keep_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "screening_manifest.jsonl"
            manifest.write_text(
                json.dumps({"relative_path": "A.pdf", "final_decision": "keep"})
                + "\n"
                + json.dumps(
                    {"relative_path": "B.pdf", "final_decision": "needs_review"}
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual({"a.pdf"}, load_kept_relative_paths(manifest))
