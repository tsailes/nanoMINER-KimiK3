from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase

import pymupdf

from nanominer_k3.documents import DocumentCorpus, DocumentError, PdfDocument


class DocumentCorpusTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pdf_path = Path(self.temp_dir.name) / "fixture.pdf"
        with pymupdf.open() as pdf:
            first = pdf.new_page()
            first.insert_text((72, 72), "Orthorhombic polyethylene unit cell a 7.4 A")
            second = pdf.new_page()
            second.insert_text(
                (72, 72),
                "References remain searchable. Space group Pnam is reported here.",
            )
            pdf.save(self.pdf_path)
        self.document = PdfDocument.load(self.pdf_path, role="article")
        self.corpus = DocumentCorpus([self.document])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_pdf_pages_are_one_based_and_references_are_preserved(self) -> None:
        self.assertEqual(2, len(self.document.pages))
        self.assertIn("Orthorhombic", self.document.page(1).text)
        self.assertIn("References remain", self.document.page(2).text)

    def test_search_returns_real_pdf_page(self) -> None:
        hits = self.corpus.search("space group Pnam")
        self.assertEqual(2, hits[0]["page"])
        self.assertEqual("article", hits[0]["role"])

    def test_page_limit_is_enforced(self) -> None:
        with self.assertRaises(DocumentError):
            self.corpus.read_pages("article", range(1, 10), max_pages=8)
