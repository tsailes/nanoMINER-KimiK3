from __future__ import annotations

import argparse
import getpass
import json
import sys
from dataclasses import replace
from pathlib import Path

from openai import OpenAI

from .batch import run_batch
from .config import ConfigurationError, KimiSettings
from .documents import DocumentCorpus, PdfDocument
from .pipeline import run_extraction
from .profiles import load_profile
from .review import apply_fulltext_reviews
from .screening import screen_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nanominer-k3",
        description="Corrected nanoMINER reproduction with a Kimi K3 core agent",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local runtime configuration")
    doctor.set_defaults(func=_doctor)

    extract = subparsers.add_parser("extract", help="Extract evidence-backed candidates")
    extract.add_argument("article", type=Path)
    extract.add_argument("--supplement", type=Path)
    extract.add_argument(
        "--profile",
        default="pe_crystal",
        help="Built-in profile name or a profile JSON path",
    )
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument(
        "--reasoning-effort", choices=("low", "high", "max")
    )
    extract.add_argument(
        "--no-vision", action="store_true", help="Disable rendered-page vision calls"
    )
    extract.add_argument(
        "--prompt-key",
        action="store_true",
        help="Read the API key from a hidden prompt instead of the environment",
    )
    extract.set_defaults(func=_extract)

    batch = subparsers.add_parser("batch", help="Extract a directory of articles")
    batch.add_argument("articles_dir", type=Path)
    batch.add_argument("--supplements-dir", type=Path)
    batch.add_argument("--profile", default="pe_crystal")
    batch.add_argument("--output-dir", type=Path, required=True)
    batch.add_argument("--reasoning-effort", choices=("low", "high", "max"))
    batch.add_argument("--no-vision", action="store_true")
    batch.add_argument("--limit", type=int)
    batch.add_argument(
        "--screening-manifest",
        type=Path,
        help="Only extract PDFs marked final_decision=keep by the local screener",
    )
    batch.add_argument(
        "--prompt-key",
        action="store_true",
        help="Read the API key from a hidden prompt instead of the environment",
    )
    batch.set_defaults(func=_batch)

    screen = subparsers.add_parser(
        "screen",
        help="Locally screen complete PDF text for core crystallographic evidence",
    )
    screen.add_argument("pdf_dir", type=Path)
    screen.add_argument("--output-dir", type=Path, required=True)
    screen.add_argument(
        "--ocr-sparse-pages",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use local Tesseract OCR on pages with little or no native text",
    )
    screen.add_argument("--ocr-language", default="eng")
    screen.add_argument("--ocr-dpi", type=int, default=150)
    screen.add_argument("--limit", type=int)
    screen.add_argument(
        "--resume", action=argparse.BooleanOptionalAction, default=True
    )
    screen.add_argument(
        "--copy-partitions",
        action="store_true",
        help="Copy PDFs into 通过 and 未通过 while preserving originals",
    )
    screen.add_argument(
        "--partition-root",
        type=Path,
        help="Parent for 通过 and 未通过 (defaults to pdf_dir)",
    )
    screen.set_defaults(func=_screen)

    review_apply = subparsers.add_parser(
        "review-apply",
        help="Apply completed local full-text reviews to a screening manifest",
    )
    review_apply.add_argument("--manifest", type=Path, required=True)
    review_apply.add_argument("--reviews", type=Path, nargs="+", required=True)
    review_apply.add_argument("--output-dir", type=Path, required=True)
    review_apply.add_argument("--copy-partitions", action="store_true")
    review_apply.add_argument("--pdf-dir", type=Path)
    review_apply.add_argument("--partition-root", type=Path)
    review_apply.set_defaults(func=_review_apply)
    return parser


def _doctor(_: argparse.Namespace) -> int:
    settings = KimiSettings.from_env(require_api_key=False)
    summary = settings.public_summary()
    summary["python"] = sys.version.split()[0]
    try:
        import pymupdf

        summary["pymupdf"] = getattr(pymupdf, "version", ("unknown",))[0]
    except Exception as exc:
        summary["pymupdf_error"] = str(exc)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["api_key_configured"] else 1


def _extract(args: argparse.Namespace) -> int:
    settings = _settings_for_run(args)
    profile = load_profile(args.profile)
    output = args.output.expanduser().resolve()
    _guard_output_path(output, profile.profile_id)

    documents = [PdfDocument.load(args.article, role="article")]
    if args.supplement:
        documents.append(PdfDocument.load(args.supplement, role="supplement"))
    corpus = DocumentCorpus(documents)
    client = _openai_client(settings)
    run = run_extraction(
        client=client,
        settings=settings,
        corpus=corpus,
        profile=profile,
        enable_vision=not args.no_vision,
        progress_handler=_progress,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(run.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


def _batch(args: argparse.Namespace) -> int:
    settings = _settings_for_run(args)
    profile = load_profile(args.profile)
    output_dir = args.output_dir.expanduser().resolve()
    _guard_output_path(output_dir / "batch_summary.json", profile.profile_id)
    client = _openai_client(settings)
    summary = run_batch(
        client=client,
        settings=settings,
        profile=profile,
        articles_dir=args.articles_dir,
        supplements_dir=args.supplements_dir,
        output_dir=output_dir,
        enable_vision=not args.no_vision,
        limit=args.limit,
        screening_manifest=args.screening_manifest,
        progress_handler=_progress,
    )
    print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))
    return 0 if not summary.failed else 1


def _screen(args: argparse.Namespace) -> int:
    summary = screen_directory(
        pdf_dir=args.pdf_dir,
        output_dir=args.output_dir,
        ocr_sparse_pages=args.ocr_sparse_pages,
        ocr_language=args.ocr_language,
        ocr_dpi=args.ocr_dpi,
        limit=args.limit,
        resume=args.resume,
        reviewer=None,
        partition_root=args.partition_root,
        copy_partitions=args.copy_partitions,
        progress_handler=_progress,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _review_apply(args: argparse.Namespace) -> int:
    summary = apply_fulltext_reviews(
        manifest_path=args.manifest,
        review_paths=args.reviews,
        output_dir=args.output_dir,
        pdf_dir=args.pdf_dir,
        partition_root=args.partition_root,
        copy_partitions=args.copy_partitions,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _guard_output_path(output: Path, profile_id: str) -> None:
    if profile_id != "pe_crystal":
        return
    lowered_parts = {part.casefold() for part in output.parts}
    if "gold" in lowered_parts:
        raise ConfigurationError(
            "PE candidate extraction must not write into a gold directory"
        )
    if output.suffix.lower() != ".json":
        raise ConfigurationError("Staging extraction output must use a .json file")
    project_root = _find_pe_project_root(Path.cwd().resolve())
    if project_root is None:
        project_root = _find_pe_project_root(Path(__file__).resolve())
    if project_root is not None:
        staging_root = (project_root / "annotations" / "source_groups").resolve()
        if not output.is_relative_to(staging_root):
            raise ConfigurationError(
                "PE extraction output must stay under "
                f"{staging_root} (for example gN/nanominer_kimi_k3/runs/<run_id>)"
            )


def _openai_client(settings: KimiSettings) -> OpenAI:
    return OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.request_timeout_seconds,
        max_retries=settings.transport_retries,
    )


def _settings_for_run(args: argparse.Namespace) -> KimiSettings:
    prompt_key = bool(getattr(args, "prompt_key", False))
    settings = KimiSettings.from_env(require_api_key=not prompt_key)
    if prompt_key:
        key = getpass.getpass("API key (hidden): ").strip()
        if not key:
            raise ConfigurationError("No API key was supplied")
        settings = replace(settings, api_key=key)
        settings.validate()
    return settings.with_reasoning_effort(args.reasoning_effort)


def _find_pe_project_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / "annotations").is_dir() and (candidate / "schema").is_dir():
            return candidate
    return None


def _progress(event: dict[str, object]) -> None:
    print(
        "[nanominer-k3] " + json.dumps(event, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigurationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
