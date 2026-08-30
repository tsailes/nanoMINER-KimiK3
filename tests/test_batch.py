from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pymupdf

from nanominer_k3.batch import discover_pdfs, match_supplement, run_batch


class BatchDiscoveryTests(TestCase):
    def test_discovery_is_case_insensitive_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "b.PDF").touch()
            (root / "A.pdf").touch()
            (root / "ignore.txt").touch()
            self.assertEqual(
                ["A.pdf", "b.PDF"],
                [path.name for path in discover_pdfs(root)],
            )

    def test_supplement_suffix_is_matched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            article = root / "paper.pdf"
            article.touch()
            supplements = root / "supplements"
            supplements.mkdir()
            expected = supplements / "paper_si.pdf"
            expected.touch()
            self.assertEqual(expected.resolve(), match_supplement(article, supplements))

    def test_batch_manifest_allows_only_keep_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("keep.pdf", "exclude.pdf"):
                document = pymupdf.open()
                page = document.new_page()
                page.insert_text((72, 72), "readable article")
                document.save(root / name)
                document.close()
            manifest = root / "manifest.jsonl"
            manifest.write_text(
                json.dumps(
                    {"relative_path": "keep.pdf", "final_decision": "keep"}
                )
                + "\n"
                + json.dumps(
                    {"relative_path": "exclude.pdf", "final_decision": "exclude"}
                )
                + "\n",
                encoding="utf-8",
            )
            fake_run = MagicMock()
            fake_run.as_dict.return_value = {"status": "needs_review"}
            with patch("nanominer_k3.batch.run_extraction", return_value=fake_run):
                summary = run_batch(
                    client=MagicMock(),
                    settings=MagicMock(),
                    profile=MagicMock(),
                    articles_dir=root,
                    supplements_dir=None,
                    output_dir=root / "outputs",
                    enable_vision=False,
                    screening_manifest=manifest,
                )
            self.assertEqual(1, summary.processed)
            self.assertEqual(1, summary.succeeded)
            self.assertEqual("keep.candidate.json", Path(summary.outputs[0]).name)
