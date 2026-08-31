from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


def _load_script_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_structure_review_drafts.py"
    spec = importlib.util.spec_from_file_location("build_structure_review_drafts", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StructureReviewDraftRuleTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script_module()

    def test_hkl_rule_accepts_three_index_plane_not_four_digit_year(self) -> None:
        rules = dict(self.module.RULES)
        self.assertIsNotNone(rules["hkl_planes"].search("the (110) reflection"))
        self.assertIsNotNone(rules["hkl_planes"].search("the (1 1 0) plane"))
        self.assertIsNone(rules["hkl_planes"].search("published in (2014)"))

    def test_rejects_manifest_as_output_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            pdf_dir = temporary_root / "pdfs"
            pdf_dir.mkdir()
            source = pdf_dir / "source.pdf"
            source.write_bytes(b"source PDF bytes")
            manifest = self._write_manifest(temporary_root, source)
            original_manifest = manifest.read_bytes()

            with patch.object(
                sys,
                "argv",
                [
                    "build_structure_review_drafts.py",
                    "--manifest",
                    str(manifest),
                    "--pdf-dir",
                    str(pdf_dir),
                    "--output",
                    str(manifest),
                ],
            ):
                with self.assertRaisesRegex(ValueError, "screening manifest"):
                    self.module.main()

            self.assertEqual(manifest.read_bytes(), original_manifest)

    def test_rejects_outputs_inside_pdf_dir_before_overwriting_or_creating(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            pdf_dir = temporary_root / "pdfs"
            pdf_dir.mkdir()
            source = pdf_dir / "source.pdf"
            original_source = b"source PDF bytes"
            source.write_bytes(original_source)
            manifest = self._write_manifest(temporary_root, source)
            escaped_output = (
                temporary_root
                / "draft-output"
                / ".."
                / "pdfs"
                / "generated"
                / "drafts.jsonl"
            )

            for output in (source, escaped_output):
                with self.subTest(output=output):
                    with patch.object(
                        sys,
                        "argv",
                        [
                            "build_structure_review_drafts.py",
                            "--manifest",
                            str(manifest),
                            "--pdf-dir",
                            str(pdf_dir),
                            "--output",
                            str(output),
                        ],
                    ), patch.object(
                        self.module,
                        "_read_all_pages",
                        return_value=(["full text"], [], []),
                    ) as read_all_pages:
                        with self.assertRaisesRegex(
                            ValueError, "PDF source directory"
                        ):
                            self.module.main()

                    read_all_pages.assert_not_called()
                    self.assertEqual(source.read_bytes(), original_source)

            self.assertFalse((pdf_dir / "generated").exists())

    def test_draft_emits_canonical_source_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            pdf_dir = temporary_root / "pdfs"
            pdf_dir.mkdir()
            source = pdf_dir / "source.pdf"
            source.write_bytes(b"source PDF bytes")
            manifest = self._write_manifest(temporary_root, source)
            output = temporary_root / "drafts" / "reviews.jsonl"

            with patch.object(
                sys,
                "argv",
                [
                    "build_structure_review_drafts.py",
                    "--manifest",
                    str(manifest),
                    "--pdf-dir",
                    str(pdf_dir),
                    "--output",
                    str(output),
                ],
            ), patch.object(
                self.module,
                "_read_all_pages",
                return_value=(["No crystallographic evidence."], [], []),
            ):
                self.assertEqual(0, self.module.main())

            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                row["source_sha256"],
            )
            self.assertNotIn("sha256", row)

    @staticmethod
    def _write_manifest(temporary_root: Path, source: Path) -> Path:
        manifest = temporary_root / "manifest.jsonl"
        row = {
            "final_decision": "exclude",
            "relative_path": source.name,
            "source_size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "page_count": 1,
        }
        manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
        return manifest
