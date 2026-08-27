from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase

from nanominer_k3.batch import discover_pdfs, match_supplement


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
