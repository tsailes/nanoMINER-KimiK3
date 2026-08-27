from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pymupdf


class DocumentError(ValueError):
    """Raised for invalid document requests."""


@dataclass(frozen=True, slots=True)
class Page:
    number: int
    text: str
    embedded_image_count: int


@dataclass(frozen=True, slots=True)
class PdfDocument:
    role: str
    path: Path
    sha256: str
    pages: tuple[Page, ...]

    @classmethod
    def load(cls, path: str | Path, *, role: str) -> "PdfDocument":
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise DocumentError(f"PDF not found: {source}")
        if source.suffix.lower() != ".pdf":
            raise DocumentError(f"Expected a PDF file: {source}")

        pages: list[Page] = []
        with pymupdf.open(source) as pdf:
            for index, raw_page in enumerate(pdf):
                text = _normalize_text(raw_page.get_text("text", sort=True))
                pages.append(
                    Page(
                        number=index + 1,
                        text=text,
                        embedded_image_count=len(raw_page.get_images(full=True)),
                    )
                )
        if not pages:
            raise DocumentError(f"PDF contains no pages: {source}")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return cls(
            role=role,
            path=source,
            sha256=digest.hexdigest(),
            pages=tuple(pages),
        )

    def page(self, number: int) -> Page:
        if number < 1 or number > len(self.pages):
            raise DocumentError(
                f"Page {number} is outside 1..{len(self.pages)} for {self.role}"
            )
        return self.pages[number - 1]

    def render_data_url(self, number: int, *, dpi: int = 160) -> str:
        self.page(number)
        if dpi < 72 or dpi > 240:
            raise DocumentError("Render DPI must be between 72 and 240")
        with pymupdf.open(self.path) as pdf:
            pixmap = pdf.load_page(number - 1).get_pixmap(dpi=dpi, alpha=False)
            encoded = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def manifest(self) -> dict[str, object]:
        return {
            "role": self.role,
            "source_file": self.path.name,
            "sha256": self.sha256,
            "page_count": len(self.pages),
            "text_pages": sum(bool(page.text) for page in self.pages),
            "empty_text_pages": [
                page.number for page in self.pages if not page.text
            ],
            "pages_with_embedded_images": [
                page.number for page in self.pages if page.embedded_image_count
            ],
        }


class DocumentCorpus:
    def __init__(self, documents: Iterable[PdfDocument]):
        by_role: dict[str, PdfDocument] = {}
        for document in documents:
            if document.role in by_role:
                raise DocumentError(f"Duplicate document role: {document.role}")
            by_role[document.role] = document
        if not by_role:
            raise DocumentError("At least one PDF document is required")
        self._documents = by_role

    @property
    def documents(self) -> tuple[PdfDocument, ...]:
        return tuple(self._documents.values())

    def document(self, role: str) -> PdfDocument:
        try:
            return self._documents[role]
        except KeyError as exc:
            available = ", ".join(self._documents)
            raise DocumentError(
                f"Unknown document role '{role}'. Available: {available}"
            ) from exc

    def manifest(self) -> list[dict[str, object]]:
        return [document.manifest() for document in self.documents]

    def read_pages(
        self, role: str, page_numbers: Iterable[int], *, max_pages: int = 8
    ) -> list[dict[str, object]]:
        requested = list(dict.fromkeys(int(number) for number in page_numbers))
        if not requested:
            raise DocumentError("At least one page number is required")
        if len(requested) > max_pages:
            raise DocumentError(f"At most {max_pages} pages can be read per tool call")
        document = self.document(role)
        return [
            {"role": role, "page": number, "text": document.page(number).text}
            for number in requested
        ]

    def search(
        self,
        query: str,
        *,
        role: str | None = None,
        max_results: int = 12,
        snippet_chars: int = 900,
    ) -> list[dict[str, object]]:
        query = query.strip()
        if not query:
            raise DocumentError("Search query must not be empty")
        if max_results < 1 or max_results > 30:
            raise DocumentError("max_results must be between 1 and 30")

        terms = [term.casefold() for term in re.findall(r"[\w.+-]+", query)]
        documents = (self.document(role),) if role else self.documents
        hits: list[tuple[int, dict[str, object]]] = []
        for document in documents:
            for page in document.pages:
                folded = page.text.casefold()
                score = sum(folded.count(term) for term in terms)
                if not score:
                    continue
                positions = [folded.find(term) for term in terms if term in folded]
                start = max(0, min(positions) - snippet_chars // 3)
                end = min(len(page.text), start + snippet_chars)
                snippet = page.text[start:end].strip()
                hits.append(
                    (
                        score,
                        {
                            "role": document.role,
                            "page": page.number,
                            "score": score,
                            "snippet": snippet,
                        },
                    )
                )
        hits.sort(key=lambda item: (-item[0], item[1]["role"], item[1]["page"]))
        return [payload for _, payload in hits[:max_results]]


def _normalize_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()
