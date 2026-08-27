from __future__ import annotations

from typing import Any

from .agent import ToolSpec
from .documents import DocumentCorpus
from .vision import KimiVisionAnalyzer


def build_document_tools(
    corpus: DocumentCorpus,
    *,
    vision: KimiVisionAnalyzer | None = None,
) -> list[ToolSpec]:
    def list_documents(_: dict[str, Any]) -> list[dict[str, object]]:
        return corpus.manifest()

    def search_documents(arguments: dict[str, Any]) -> list[dict[str, object]]:
        return corpus.search(
            str(arguments["query"]),
            role=arguments.get("file_role"),
            max_results=int(arguments.get("max_results", 12)),
        )

    def read_pages(arguments: dict[str, Any]) -> list[dict[str, object]]:
        return corpus.read_pages(
            str(arguments["file_role"]),
            arguments["pages"],
        )

    tools = [
        ToolSpec(
            name="list_documents",
            description=(
                "List the article and supplement manifests, including exact source "
                "filenames, 1-based PDF page counts, and pages containing images."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=list_documents,
        ),
        ToolSpec(
            name="search_documents",
            description=(
                "Search extracted PDF text and return ranked snippets with exact "
                "1-based PDF page numbers. Use this before reading full pages."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "file_role": {
                        "type": ["string", "null"],
                        "description": "article, supplement, or null for all",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 12,
                    },
                },
                "required": ["query", "file_role", "max_results"],
                "additionalProperties": False,
            },
            handler=search_documents,
        ),
        ToolSpec(
            name="read_pages",
            description=(
                "Read the complete extracted text for up to eight specified PDF "
                "pages. Page numbers are 1-based and must come from search results."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_role": {"type": "string", "minLength": 1},
                    "pages": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "integer", "minimum": 1},
                    },
                },
                "required": ["file_role", "pages"],
                "additionalProperties": False,
            },
            handler=read_pages,
        ),
    ]

    if vision is not None:
        def analyze_pages(arguments: dict[str, Any]) -> dict[str, object]:
            document = corpus.document(str(arguments["file_role"]))
            return vision.analyze(
                document,
                arguments["pages"],
                focus=str(arguments["focus"]),
            )

        tools.append(
            ToolSpec(
                name="analyze_pages_visually",
                description=(
                    "Render and inspect up to four PDF pages with Kimi K3 vision. "
                    "Use for figures, tables, equations, scans, or pages whose text "
                    "layer is empty. Provide a narrow scientific focus."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "file_role": {"type": "string", "minLength": 1},
                        "pages": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 4,
                            "items": {"type": "integer", "minimum": 1},
                        },
                        "focus": {"type": "string", "minLength": 1},
                    },
                    "required": ["file_role", "pages", "focus"],
                    "additionalProperties": False,
                },
                handler=analyze_pages,
            )
        )
    return tools
