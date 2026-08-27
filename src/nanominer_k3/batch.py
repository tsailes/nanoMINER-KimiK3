from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import ProgressHandler
from .config import KimiSettings
from .documents import DocumentCorpus, PdfDocument
from .pipeline import run_extraction
from .profiles import ExtractionProfile


@dataclass(frozen=True, slots=True)
class BatchResult:
    processed: int
    succeeded: int
    failed: int
    outputs: tuple[str, ...]
    errors: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "outputs": list(self.outputs),
            "errors": list(self.errors),
        }


def discover_pdfs(directory: Path) -> list[Path]:
    root = directory.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"PDF directory not found: {root}")
    return sorted(
        (path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name.casefold(),
    )


def match_supplement(article: Path, supplements_dir: Path | None) -> Path | None:
    if supplements_dir is None:
        return None
    supplements = discover_pdfs(supplements_dir)
    names = {
        article.name.casefold(),
        f"{article.stem}_si.pdf".casefold(),
        f"{article.stem}_supplement.pdf".casefold(),
    }
    matches = [path for path in supplements if path.name.casefold() in names]
    if len(matches) > 1:
        rendered = ", ".join(path.name for path in matches)
        raise ValueError(f"Ambiguous supplements for {article.name}: {rendered}")
    return matches[0] if matches else None


def run_batch(
    *,
    client: Any,
    settings: KimiSettings,
    profile: ExtractionProfile,
    articles_dir: Path,
    supplements_dir: Path | None,
    output_dir: Path,
    enable_vision: bool,
    limit: int | None = None,
    progress_handler: ProgressHandler | None = None,
) -> BatchResult:
    articles = discover_pdfs(articles_dir)
    if limit is not None:
        if limit < 1:
            raise ValueError("Batch limit must be positive")
        articles = articles[:limit]
    if not articles:
        raise ValueError(f"No PDF files found in {articles_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    errors: list[dict[str, str]] = []
    for article in articles:
        if progress_handler is not None:
            progress_handler({"event": "article_started", "article": article.name})
        try:
            documents = [PdfDocument.load(article, role="article")]
            supplement = match_supplement(article, supplements_dir)
            if supplement is not None:
                documents.append(PdfDocument.load(supplement, role="supplement"))
            run = run_extraction(
                client=client,
                settings=settings,
                corpus=DocumentCorpus(documents),
                profile=profile,
                enable_vision=enable_vision,
                progress_handler=progress_handler,
            )
            destination = output_dir / f"{_safe_stem(article.stem)}.candidate.json"
            destination.write_text(
                json.dumps(run.as_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            outputs.append(str(destination))
            if progress_handler is not None:
                progress_handler(
                    {"event": "article_completed", "article": article.name}
                )
        except Exception as exc:
            errors.append(
                {
                    "article": str(article),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            if progress_handler is not None:
                progress_handler(
                    {
                        "event": "article_failed",
                        "article": article.name,
                        "error_type": type(exc).__name__,
                    }
                )
    summary = BatchResult(
        processed=len(articles),
        succeeded=len(outputs),
        failed=len(errors),
        outputs=tuple(outputs),
        errors=tuple(errors),
    )
    (output_dir / "batch_summary.json").write_text(
        json.dumps(summary.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _safe_stem(stem: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return cleaned or "document"
