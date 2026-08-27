from __future__ import annotations

from typing import Any, Iterable

from .config import KimiSettings
from .documents import PdfDocument


class KimiVisionAnalyzer:
    """Analyze rendered PDF pages with Kimi K3 native vision."""

    def __init__(self, client: Any, settings: KimiSettings):
        self._client = client
        self._settings = settings

    def analyze(
        self,
        document: PdfDocument,
        page_numbers: Iterable[int],
        *,
        focus: str,
    ) -> dict[str, object]:
        pages = list(dict.fromkeys(int(number) for number in page_numbers))
        if not pages:
            raise ValueError("At least one page is required for visual analysis")
        if len(pages) > 4:
            raise ValueError("At most four pages can be analyzed per vision call")
        if not focus.strip():
            raise ValueError("A concrete visual-analysis focus is required")

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Analyze the following PDF page renderings as scientific evidence. "
                    "Report only directly visible values, labels, trends, tables, and "
                    "uncertainties relevant to this focus: "
                    f"{focus}. Keep each finding tied to its 1-based PDF page number. "
                    "Do not estimate graph coordinates unless explicitly requested; label "
                    "any estimate as estimated_from_graph."
                ),
            }
        ]
        for number in pages:
            content.append(
                {"type": "text", "text": f"PDF page {number} ({document.role})"}
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": document.render_data_url(number)},
                }
            )

        response = self._client.chat.completions.create(
            model=self._settings.model,
            reasoning_effort=self._settings.reasoning_effort,
            messages=[{"role": "user", "content": content}],
            max_completion_tokens=min(
                self._settings.max_completion_tokens, 16_384
            ),
        )
        if not getattr(response, "choices", None):
            raise RuntimeError("Kimi K3 vision call returned no choices")
        answer = response.choices[0].message.content
        if not answer:
            raise RuntimeError("Kimi K3 vision call returned no content")
        return {
            "role": document.role,
            "pages": pages,
            "focus": focus,
            "analysis": answer,
        }
