from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest import TestCase

from nanominer_k3.review import apply_fulltext_reviews
from nanominer_k3.screening import materialize_two_folders


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _review(
    relative_path: str,
    *,
    index: int,
    decision: str,
    ownership: str,
) -> dict[str, object]:
    return {
        "review_index": index,
        "relative_path": relative_path,
        "original_decision": "needs_review",
        "review_decision": decision,
        "confidence": "high",
        "reason_code": "verified_primary_core" if decision == "keep" else "no_core",
        "rationale_zh": "已核查全文和证据页。",
        "evidence_pages": [1] if decision == "keep" else [],
        "evidence_quotes": ["space group Pnam; a, b, c"] if decision == "keep" else [],
        "ownership": ownership,
        "fulltext_checked": True,
        "visual_checked": False,
        "reviewer": "test-reviewer",
    }


class ApplyFullTextReviewTests(TestCase):
    def test_applies_complete_reviews_without_overwriting_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "screening_manifest.jsonl"
            records = [
                {
                    "relative_path": "already.pdf",
                    "local_decision": "keep",
                    "final_decision": "keep",
                    "page_count": 1,
                    "sha256": "a",
                },
                {
                    "relative_path": "candidate.pdf",
                    "local_decision": "needs_review",
                    "final_decision": "needs_review",
                    "page_count": 1,
                    "sha256": "b",
                },
                {
                    "relative_path": "reject.pdf",
                    "local_decision": "needs_review",
                    "final_decision": "needs_review",
                    "page_count": 1,
                    "sha256": "c",
                },
            ]
            _write_jsonl(manifest, records)
            original = manifest.read_bytes()
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [
                    _review(
                        "candidate.pdf", index=1, decision="keep", ownership="primary"
                    ),
                    _review(
                        "reject.pdf", index=2, decision="exclude", ownership="none"
                    ),
                ],
            )

            summary = apply_fulltext_reviews(
                manifest_path=manifest,
                review_paths=[reviews],
                output_dir=root / "reviewed",
            )

            self.assertEqual(original, manifest.read_bytes())
            self.assertEqual(
                {"keep": 2, "needs_review": 0, "exclude": 1},
                summary["final_counts"],
            )
            reviewed = [
                json.loads(line)
                for line in (root / "reviewed" / "screening_manifest.reviewed.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            candidate = reviewed[1]
            self.assertEqual("needs_review", candidate["local_decision"])
            self.assertEqual("needs_review", candidate["pre_review_decision"])
            self.assertEqual("keep", candidate["final_decision"])
            self.assertEqual("test-reviewer", candidate["codex_fulltext_review"]["reviewer"])

    def test_rejects_incomplete_review_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "a.pdf",
                        "final_decision": "needs_review",
                        "page_count": 1,
                    },
                    {
                        "relative_path": "b.pdf",
                        "final_decision": "needs_review",
                        "page_count": 1,
                    },
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [_review("a.pdf", index=1, decision="exclude", ownership="none")],
            )
            with self.assertRaisesRegex(ValueError, "Missing 1"):
                apply_fulltext_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=root / "out",
                )

    def test_rejects_keep_without_primary_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "a.pdf",
                        "final_decision": "needs_review",
                        "page_count": 1,
                    }
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [_review("a.pdf", index=1, decision="keep", ownership="secondary")],
            )
            with self.assertRaisesRegex(ValueError, "primary ownership"):
                apply_fulltext_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=root / "out",
                )

    def test_rejects_output_collision_with_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "out"
            output.mkdir()
            manifest = output / "screening_manifest.reviewed.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "a.pdf",
                        "final_decision": "needs_review",
                        "page_count": 1,
                    }
                ],
            )
            original = manifest.read_bytes()
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [_review("a.pdf", index=1, decision="exclude", ownership="none")],
            )
            with self.assertRaisesRegex(ValueError, "overwrite an input"):
                apply_fulltext_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=output,
                )
            self.assertEqual(original, manifest.read_bytes())

    def test_rejects_duplicate_baseline_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "A.pdf",
                        "final_decision": "needs_review",
                        "page_count": 1,
                    },
                    {
                        "relative_path": "a.pdf",
                        "final_decision": "needs_review",
                        "page_count": 1,
                    },
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [_review("A.pdf", index=1, decision="exclude", ownership="none")],
            )
            with self.assertRaisesRegex(ValueError, "Duplicate screening-manifest"):
                apply_fulltext_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=root / "out",
                )

    def test_rejects_keep_with_empty_quote_or_out_of_range_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "a.pdf",
                        "final_decision": "needs_review",
                        "page_count": 2,
                    }
                ],
            )
            for invalid_quote, invalid_page, message in (
                ("", 1, "page evidence"),
                ("valid", 3, "exceeds page_count"),
            ):
                review = _review(
                    "a.pdf", index=1, decision="keep", ownership="primary"
                )
                review["evidence_quotes"] = [invalid_quote]
                review["evidence_pages"] = [invalid_page]
                reviews = root / f"reviews-{invalid_page}-{len(invalid_quote)}.jsonl"
                _write_jsonl(reviews, [review])
                with self.assertRaisesRegex(ValueError, message):
                    apply_fulltext_reviews(
                        manifest_path=manifest,
                        review_paths=[reviews],
                        output_dir=root / "out",
                    )

    def test_changed_source_stops_review_before_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_root = root / "pdfs"
            pdf_root.mkdir()
            source = pdf_root / "a.pdf"
            source.write_bytes(b"OLD")
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "a.pdf",
                        "final_decision": "needs_review",
                        "page_count": 1,
                        "source_size_bytes": 3,
                        "sha256": hashlib.sha256(b"OLD").hexdigest(),
                    }
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [_review("a.pdf", index=1, decision="exclude", ownership="none")],
            )
            source.write_bytes(b"NEW")
            output = root / "out"
            with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
                apply_fulltext_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=output,
                    copy_partitions=True,
                    pdf_dir=pdf_root,
                )
            self.assertFalse(output.exists())

    def test_zero_page_read_error_can_be_reviewed_without_page_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "unreadable.pdf",
                        "final_decision": "needs_review",
                        "page_count": 0,
                    }
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [
                    _review(
                        "unreadable.pdf",
                        index=1,
                        decision="exclude",
                        ownership="none",
                    )
                ],
            )
            summary = apply_fulltext_reviews(
                manifest_path=manifest,
                review_paths=[reviews],
                output_dir=root / "out",
            )
            self.assertEqual(1, summary["final_counts"]["exclude"])


class PartitionSourceValidationTests(TestCase):
    def test_existing_source_requires_size_and_sha_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_root = root / "pdfs"
            pdf_root.mkdir()
            (pdf_root / "a.pdf").write_bytes(b"PDF")
            partitions = root / "partitions"
            with self.assertRaisesRegex(ValueError, "source_size_bytes"):
                materialize_two_folders(
                    records=[{"relative_path": "a.pdf", "final_decision": "keep"}],
                    pdf_dir=pdf_root,
                    partition_root=partitions,
                )
            self.assertFalse(partitions.exists())

    def test_missing_source_is_an_error_before_partition_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_root = root / "pdfs"
            pdf_root.mkdir()
            partitions = root / "partitions"
            with self.assertRaisesRegex(ValueError, "source file not found"):
                materialize_two_folders(
                    records=[
                        {"relative_path": "missing.pdf", "final_decision": "keep"}
                    ],
                    pdf_dir=pdf_root,
                    partition_root=partitions,
                )
            self.assertFalse(partitions.exists())
