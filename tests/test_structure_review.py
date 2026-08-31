from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest import TestCase

from nanominer_k3.structure_review import apply_structure_relevance_reviews


SHA_A = "a" * 64


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
    ownership: str = "secondary",
    source_sha256: str = SHA_A,
) -> dict[str, object]:
    keep = decision == "keep"
    pending = decision == "needs_review"
    return {
        "review_index": index,
        "relative_path": relative_path,
        "source_sha256": source_sha256,
        "topic_decision": decision,
        "structure_information_level": (
            "partial_crystallographic_data"
            if keep
            else "ambiguous" if pending else "none"
        ),
        "ownership": ownership if keep else "unknown" if pending else "none",
        "information_types": ["space_group"] if keep else [],
        "evidence_pages": [1] if keep else [],
        "evidence_quotes": ["The reported phase has space group Pnam."] if keep else [],
        "fulltext_checked": True,
        "visual_checked": False,
        "confidence": "high" if not pending else "low",
        "reason_code": "explicit_space_group" if keep else "ambiguous" if pending else "no_structure_fact",
        "rationale_zh": "已逐页核对全文。",
        "reviewer": "test-reviewer",
    }


class ApplyStructureRelevanceReviewTests(TestCase):
    def test_keeps_secondary_structure_content_and_preserves_strict_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "strict.pdf",
                        "final_decision": "keep",
                        "page_count": 1,
                        "qualifying_core_categories": [
                            "space_group",
                            "numeric_unit_cell",
                        ],
                    },
                    {
                        "relative_path": "review.pdf",
                        "final_decision": "exclude",
                        "page_count": 2,
                        "sha256": SHA_A,
                    },
                ],
            )
            original = manifest.read_bytes()
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [_review("review.pdf", index=1, decision="keep")],
            )

            summary = apply_structure_relevance_reviews(
                manifest_path=manifest,
                review_paths=[reviews],
                output_dir=root / "out",
            )

            self.assertEqual(original, manifest.read_bytes())
            self.assertEqual(
                {"keep": 2, "needs_review": 0, "exclude": 0},
                summary["final_counts"],
            )
            rows = [
                json.loads(line)
                for line in (root / "out" / "screening_manifest.structure-reviewed.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual("keep", rows[0]["strict_final_decision"])
            self.assertEqual("complete_structure", rows[0]["structure_information_level"])
            self.assertEqual("exclude", rows[1]["strict_final_decision"])
            self.assertEqual("secondary", rows[1]["structure_ownership"])

    def test_requires_every_strict_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {"relative_path": "a.pdf", "final_decision": "exclude", "page_count": 1, "sha256": SHA_A},
                    {"relative_path": "b.pdf", "final_decision": "exclude", "page_count": 1, "sha256": SHA_A},
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [_review("a.pdf", index=1, decision="exclude")],
            )
            with self.assertRaisesRegex(ValueError, "Missing 1 exclude"):
                apply_structure_relevance_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=root / "out",
                )

    def test_rejects_positive_decision_without_typed_page_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [{"relative_path": "a.pdf", "final_decision": "exclude", "page_count": 1, "sha256": SHA_A}],
            )
            review = _review("a.pdf", index=1, decision="keep")
            review["information_types"] = []
            reviews = root / "reviews.jsonl"
            _write_jsonl(reviews, [review])
            with self.assertRaisesRegex(ValueError, "typed page evidence"):
                apply_structure_relevance_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=root / "out",
                )

    def test_review_indices_follow_sorted_paths_not_manifest_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {"relative_path": "z.pdf", "final_decision": "exclude", "page_count": 1, "sha256": SHA_A},
                    {"relative_path": "A.pdf", "final_decision": "exclude", "page_count": 1, "sha256": SHA_A},
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [
                    _review("A.pdf", index=1, decision="exclude"),
                    _review("z.pdf", index=2, decision="exclude"),
                ],
            )
            summary = apply_structure_relevance_reviews(
                manifest_path=manifest,
                review_paths=[reviews],
                output_dir=root / "out",
            )
            self.assertEqual(2, summary["reviews_applied"])

    def test_rejects_review_bound_to_a_different_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [{"relative_path": "a.pdf", "final_decision": "exclude", "page_count": 1, "sha256": SHA_A}],
            )
            review = _review("a.pdf", index=1, decision="exclude")
            review["source_sha256"] = "b" * 64
            reviews = root / "reviews.jsonl"
            _write_jsonl(reviews, [review])
            output = root / "out"
            with self.assertRaisesRegex(ValueError, "differs from the manifest"):
                apply_structure_relevance_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_changed_source_stops_before_outputs(self) -> None:
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
                        "final_decision": "exclude",
                        "page_count": 1,
                        "source_size_bytes": 3,
                        "sha256": hashlib.sha256(b"OLD").hexdigest(),
                    }
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [
                    _review(
                        "a.pdf",
                        index=1,
                        decision="exclude",
                        source_sha256=hashlib.sha256(b"OLD").hexdigest(),
                    )
                ],
            )
            source.write_bytes(b"NEW")
            output = root / "out"
            with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
                apply_structure_relevance_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=output,
                    copy_partitions=True,
                    pdf_dir=pdf_root,
                )
            self.assertFalse(output.exists())

    def test_partition_conflict_leaves_review_output_unpublished(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_root = root / "pdfs"
            pdf_root.mkdir()
            source = pdf_root / "a.pdf"
            source.write_bytes(b"PDF")
            source_sha = hashlib.sha256(b"PDF").hexdigest()
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "a.pdf",
                        "final_decision": "exclude",
                        "page_count": 1,
                        "source_size_bytes": 3,
                        "sha256": source_sha,
                    }
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [
                    _review(
                        "a.pdf",
                        index=1,
                        decision="exclude",
                        source_sha256=source_sha,
                    )
                ],
            )
            failed = pdf_root / "未通过"
            failed.mkdir()
            (failed / "a.pdf").write_bytes(b"CORRUPT")
            output = root / "out"

            with self.assertRaisesRegex(ValueError, "different content"):
                apply_structure_relevance_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=output,
                    copy_partitions=True,
                    pdf_dir=pdf_root,
                )

            self.assertFalse(output.exists())
            self.assertEqual(b"CORRUPT", (failed / "a.pdf").read_bytes())

    def test_rejects_output_and_partition_path_overlap_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_root = root / "pdfs"
            pdf_root.mkdir()
            source = pdf_root / "a.pdf"
            source.write_bytes(b"PDF")
            source_sha = hashlib.sha256(b"PDF").hexdigest()
            manifest = root / "manifest.jsonl"
            _write_jsonl(
                manifest,
                [
                    {
                        "relative_path": "a.pdf",
                        "final_decision": "exclude",
                        "page_count": 1,
                        "source_size_bytes": 3,
                        "sha256": source_sha,
                    }
                ],
            )
            reviews = root / "reviews.jsonl"
            _write_jsonl(
                reviews,
                [
                    _review(
                        "a.pdf",
                        index=1,
                        decision="exclude",
                        source_sha256=source_sha,
                    )
                ],
            )
            overlapping = root / "result"

            with self.assertRaisesRegex(ValueError, "must not overlap"):
                apply_structure_relevance_reviews(
                    manifest_path=manifest,
                    review_paths=[reviews],
                    output_dir=overlapping,
                    copy_partitions=True,
                    pdf_dir=pdf_root,
                    partition_root=overlapping,
                )

            self.assertFalse(overlapping.exists())
