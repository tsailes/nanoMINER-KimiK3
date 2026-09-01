from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest import TestCase

import gemmi

from nanominer_k3.cif_builder import CifBuildError, build_cif_from_spec


class CifBuilderTests(TestCase):
    def _spec(self, root: Path) -> Path:
        source = root / "source.pdf"
        source.write_bytes(b"minimal source fixture")
        sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        spec = {
            "structure_id": "PE_TEST",
            "title": "polyethylene carbon backbone test",
            "build_date": "2026-09-01",
            "formula_sum": "C H2",
            "formula_units_z": 4,
            "crystal_system": "orthorhombic",
            "source": {
                "file_name": source.name,
                "pdf_path": str(source),
                "sha256": sha256,
                "pages": [1],
            },
            "cell": {
                "a": 7.4,
                "b": 4.93,
                "c": 2.534,
                "alpha": 90,
                "beta": 90,
                "gamma": 90,
            },
            "space_group": {
                "reported": "Pnam",
                "build_setting": "P n a m",
            },
            "atom_sites": [
                {
                    "label": "C1",
                    "type_symbol": "C",
                    "fract_x": 0.038,
                    "fract_y": 0.935,
                    "fract_z": 0.25,
                    "occupancy": 1.0,
                }
            ],
            "model_scope": "carbon_backbone_only",
            "expected_expanded_sites": 4,
            "expected_expanded_composition": {"C": 4},
            "expected_occupancy_weighted_composition": {"C": 4},
            "distance_expectations": [
                {
                    "element_pair": ["C", "C"],
                    "value_angstrom": 1.53,
                    "tolerance_angstrom": 0.03,
                }
            ],
        }
        spec_path = root / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        return spec_path

    def test_builds_parseable_symmetry_expanded_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = build_cif_from_spec(self._spec(root), root / "out")

            self.assertEqual("pass", result["status"])
            self.assertEqual("pass", result["parser"]["small_structure_status"])
            self.assertEqual(4, result["atom_sites"]["expanded_count"])
            self.assertEqual({"C": 4}, result["atom_sites"]["expanded_composition"])
            self.assertEqual(
                {"C": 4.0},
                result["atom_sites"]["occupancy_weighted_composition"],
            )
            cif_path = Path(result["outputs"]["cif"])
            block = gemmi.cif.read_file(str(cif_path)).sole_block()
            self.assertEqual(
                "P n a m",
                gemmi.cif.as_string(block.find_value("_space_group_name_H-M_alt")),
            )
            self.assertEqual(["C1"], list(block.find_values("_atom_site_label")))

    def test_rejects_source_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = self._spec(root)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec["source"]["sha256"] = "0" * 64
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            with self.assertRaisesRegex(CifBuildError, "SHA-256 mismatch"):
                build_cif_from_spec(spec_path, root / "out")

    def test_rejects_unknown_element_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = self._spec(root)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec["atom_sites"][0]["type_symbol"] = "C1"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            with self.assertRaisesRegex(CifBuildError, "recognized element"):
                build_cif_from_spec(spec_path, root / "out")

    def test_allows_complementary_mixed_occupancy_on_one_site(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = self._spec(root)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec["structure_id"] = "MIXED_SITE_TEST"
            spec["cell"] = {
                "a": 10,
                "b": 10,
                "c": 10,
                "alpha": 90,
                "beta": 90,
                "gamma": 90,
            }
            spec["space_group"] = {"reported": "P1", "build_setting": "P 1"}
            spec["atom_sites"] = [
                {
                    "label": "Li1",
                    "type_symbol": "Li",
                    "fract_x": 0,
                    "fract_y": 0,
                    "fract_z": 0,
                    "occupancy": 0.25,
                    "shared_site_group": "M1",
                },
                {
                    "label": "Na1",
                    "type_symbol": "Na",
                    "fract_x": 0,
                    "fract_y": 0,
                    "fract_z": 0,
                    "occupancy": 0.75,
                    "shared_site_group": "M1",
                },
                {
                    "label": "O1",
                    "type_symbol": "O",
                    "fract_x": 0.2,
                    "fract_y": 0.2,
                    "fract_z": 0.2,
                    "occupancy": 1.0,
                    "u_iso": 0.02,
                },
            ]
            spec["expected_expanded_sites"] = 3
            spec["expected_expanded_composition"] = {"Li": 1, "Na": 1, "O": 1}
            spec["expected_occupancy_weighted_composition"] = {
                "Li": 0.25,
                "Na": 0.75,
                "O": 1,
            }
            spec["distance_expectations"] = []
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            result = build_cif_from_spec(spec_path, root / "out")

            self.assertEqual(1, result["geometry"]["shared_position_pairs_skipped"])
            self.assertEqual(
                {"Li": 0.25, "Na": 0.75, "O": 1.0},
                result["atom_sites"]["occupancy_weighted_composition"],
            )
