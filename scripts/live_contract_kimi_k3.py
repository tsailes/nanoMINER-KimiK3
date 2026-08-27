"""Opt-in live Kimi K3 contract test using a generated one-page PDF.

The script never prints or writes the API key. By default it reads
MOONSHOT_API_KEY; --key-stdin is provided for ephemeral CI/agent injection.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from openai import OpenAI

from nanominer_k3.config import KimiSettings
from nanominer_k3.documents import DocumentCorpus, PdfDocument
from nanominer_k3.pipeline import run_extraction
from nanominer_k3.profiles import load_profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an opt-in, low-cost Kimi K3 end-to-end contract test"
    )
    parser.add_argument(
        "--key-stdin",
        action="store_true",
        help="Read the API key from one stdin line instead of the environment",
    )
    parser.add_argument(
        "--service",
        choices=("open-platform", "open-platform-cn", "kimi-code"),
        default="open-platform-cn",
        help="Select the endpoint family matching the key source",
    )
    return parser


def _synthetic_pdf(path: Path) -> None:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text(
            (72, 72),
            (
                "Synthetic Kimi K3 contract fixture.\n"
                "The material is polyethylene.\n"
                "The crystal system is explicitly reported as orthorhombic.\n"
                "The source-reported unit-cell parameter is a = 7.40 angstrom.\n"
                "These statements are test evidence on PDF page 1."
            ),
        )
        pdf.save(path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    api_key = (
        getpass.getpass("API key (hidden): ").strip()
        if args.key_stdin
        else (os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY"))
    )
    if not api_key:
        print("error: no API key supplied", file=sys.stderr)
        return 2

    is_code = args.service == "kimi-code"
    is_cn = args.service == "open-platform-cn"
    settings = KimiSettings(
        api_key=api_key,
        base_url=(
            "https://api.kimi.com/coding/v1"
            if is_code
            else (
                "https://api.moonshot.cn/v1"
                if is_cn
                else "https://api.moonshot.ai/v1"
            )
        ),
        model="k3" if is_code else "kimi-k3",
        reasoning_effort="low",
        max_agent_turns=6,
        max_completion_tokens=8192,
    )
    settings.validate()
    client = OpenAI(
        api_key=api_key,
        base_url=settings.base_url,
        timeout=settings.request_timeout_seconds,
        max_retries=settings.transport_retries,
    )
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="nanominer_k3_contract_") as temp:
            source = Path(temp) / "synthetic_pe_contract.pdf"
            _synthetic_pdf(source)
            run = run_extraction(
                client=client,
                settings=settings,
                corpus=DocumentCorpus(
                    [PdfDocument.load(source, role="article")]
                ),
                profile=load_profile("pe_crystal"),
                enable_vision=False,
            )
        records = run.extraction["records"]
        print(
            json.dumps(
                {
                    "ok": True,
                    "model": run.run_metadata["model"],
                    "reasoning_effort": run.run_metadata["reasoning_effort"],
                    "agent_turns": run.run_metadata["agent_turns"],
                    "tool_calls": run.run_metadata["tool_calls"],
                    "record_count": len(records),
                    "record_types": [record["record_type"] for record in records],
                    "all_needs_review": all(
                        record["review_status"] == "needs_review"
                        for record in records
                    ),
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                    "error_code": getattr(exc, "code", None),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        api_key = ""


if __name__ == "__main__":
    raise SystemExit(main())
