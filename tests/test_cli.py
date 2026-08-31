from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from nanominer_k3.cli import build_parser, _find_pe_project_root, _guard_output_path
from nanominer_k3.config import ConfigurationError


class OutputBoundaryTests(TestCase):
    def test_extract_accepts_hidden_key_prompt_mode(self) -> None:
        args = build_parser().parse_args(
            [
                "extract",
                "paper.pdf",
                "--output",
                "candidate.json",
                "--prompt-key",
            ]
        )
        self.assertTrue(args.prompt_key)

    def test_pe_output_stays_in_source_group_staging(self) -> None:
        project_root = _find_pe_project_root(Path.cwd().resolve())
        if project_root is None:
            self.skipTest("PE project root is not available")
        allowed = (
            project_root
            / "annotations"
            / "source_groups"
            / "g9"
            / "nanominer_kimi_k3"
            / "runs"
            / "test"
            / "candidate.json"
        )
        _guard_output_path(allowed.resolve(), "pe_crystal")
        with self.assertRaisesRegex(ConfigurationError, "must stay under"):
            _guard_output_path((project_root / "candidate.json").resolve(), "pe_crystal")

    def test_gold_output_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "gold directory"):
            _guard_output_path(
                Path("C:/data/gold/g1/candidate.json").resolve(),
                "pe_crystal",
            )

    def test_local_screen_command_needs_no_api_key_option(self) -> None:
        args = build_parser().parse_args(
            [
                "screen",
                "pdfs",
                "--output-dir",
                "screening-output",
                "--ocr-sparse-pages",
                "--copy-partitions",
            ]
        )
        self.assertTrue(args.ocr_sparse_pages)
        self.assertTrue(args.copy_partitions)
        self.assertFalse(hasattr(args, "prompt_key"))

    def test_structure_review_command_needs_no_api_key_option(self) -> None:
        args = build_parser().parse_args(
            [
                "structure-review-apply",
                "--manifest",
                "manifest.jsonl",
                "--reviews",
                "part-1.jsonl",
                "part-2.jsonl",
                "--output-dir",
                "structure-reviewed",
                "--copy-partitions",
                "--pdf-dir",
                "pdfs",
            ]
        )
        self.assertEqual([Path("part-1.jsonl"), Path("part-2.jsonl")], args.reviews)
        self.assertTrue(args.copy_partitions)
        self.assertFalse(hasattr(args, "prompt_key"))
