from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import gemmi


class CifBuildError(ValueError):
    """Raised when an extraction draft cannot be converted into a safe CIF."""


_SAFE_BLOCK_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def build_cif_from_spec(spec_path: Path, output_dir: Path) -> dict[str, Any]:
    """Build and validate one draft CIF from a provenance-bearing JSON spec.

    The JSON file is deliberately small and permissive: it captures only the
    fields needed to construct a useful crystal model plus enough provenance to
    distinguish reported values from curator-derived choices.
    """

    resolved_spec = spec_path.expanduser().resolve()
    try:
        spec = json.loads(resolved_spec.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CifBuildError(f"Cannot read CIF build spec {resolved_spec}: {exc}") from exc
    if not isinstance(spec, dict):
        raise CifBuildError("CIF build spec must be a JSON object")

    _validate_spec(spec)
    source_check = _verify_source(spec["source"], resolved_spec.parent)
    supporting_source_checks = [
        _verify_supporting_source(source, resolved_spec.parent, index)
        for index, source in enumerate(spec.get("supporting_sources", []))
    ]
    space_group = gemmi.find_spacegroup_by_name(
        str(spec["space_group"]["build_setting"])
    )
    if space_group is None:
        raise CifBuildError(
            "Unknown build-setting space group: "
            f"{spec['space_group']['build_setting']!r}"
        )

    cif_text = _render_cif(
        spec, space_group, source_check, supporting_source_checks
    )
    validation = _validate_generated_cif(
        spec,
        cif_text,
        space_group,
        source_check,
        supporting_source_checks,
    )

    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    structure_id = str(spec["structure_id"])
    cif_path = destination / f"{structure_id}.draft.cif"
    validation_path = destination / f"{structure_id}.validation.json"
    cif_path.write_text(cif_text, encoding="utf-8", newline="\n")
    validation["outputs"] = {
        "cif": str(cif_path),
        "validation": str(validation_path),
    }
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return validation


def _validate_spec(spec: Mapping[str, Any]) -> None:
    for required in (
        "structure_id",
        "title",
        "source",
        "cell",
        "space_group",
        "atom_sites",
        "model_scope",
    ):
        if required not in spec:
            raise CifBuildError(f"Missing required spec field: {required}")

    structure_id = str(spec["structure_id"])
    if not structure_id or _SAFE_BLOCK_NAME.sub("_", structure_id) != structure_id:
        raise CifBuildError(
            "structure_id may contain only letters, numbers, underscore, dot, and dash"
        )

    source = spec["source"]
    if not isinstance(source, Mapping):
        raise CifBuildError("source must be an object")
    for required in ("file_name", "sha256"):
        if not str(source.get(required, "")).strip():
            raise CifBuildError(f"source.{required} is required")
    digest = str(source["sha256"]).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CifBuildError("source.sha256 must be a 64-character hexadecimal digest")

    supporting_sources = spec.get("supporting_sources", [])
    if not isinstance(supporting_sources, list):
        raise CifBuildError("supporting_sources must be a list")
    for index, supporting_source in enumerate(supporting_sources):
        if not isinstance(supporting_source, Mapping):
            raise CifBuildError(f"supporting_sources[{index}] must be an object")
        for required in ("file_name", "sha256"):
            if not str(supporting_source.get(required, "")).strip():
                raise CifBuildError(
                    f"supporting_sources[{index}].{required} is required"
                )
        supporting_digest = str(supporting_source["sha256"]).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", supporting_digest):
            raise CifBuildError(
                f"supporting_sources[{index}].sha256 must be a "
                "64-character hexadecimal digest"
            )

    cell = spec["cell"]
    if not isinstance(cell, Mapping):
        raise CifBuildError("cell must be an object")
    for key in ("a", "b", "c", "alpha", "beta", "gamma"):
        value = _finite_number(cell.get(key), f"cell.{key}")
        if value <= 0:
            raise CifBuildError(f"cell.{key} must be positive")
    for key in ("alpha", "beta", "gamma"):
        angle = float(cell[key])
        if not 0 < angle < 180:
            raise CifBuildError(f"cell.{key} must be between 0 and 180 degrees")

    group = spec["space_group"]
    if not isinstance(group, Mapping):
        raise CifBuildError("space_group must be an object")
    for required in ("reported", "build_setting"):
        if not str(group.get(required, "")).strip():
            raise CifBuildError(f"space_group.{required} is required")

    sites = spec["atom_sites"]
    if not isinstance(sites, list) or not sites:
        raise CifBuildError("atom_sites must be a non-empty list")
    labels: set[str] = set()
    for index, site in enumerate(sites):
        if not isinstance(site, Mapping):
            raise CifBuildError(f"atom_sites[{index}] must be an object")
        label = str(site.get("label", "")).strip()
        symbol = str(site.get("type_symbol", "")).strip()
        if not label or not symbol:
            raise CifBuildError(
                f"atom_sites[{index}] needs non-empty label and type_symbol"
            )
        if gemmi.Element(symbol).atomic_number == 0:
            raise CifBuildError(
                f"atom_sites[{index}].type_symbol is not a recognized element: "
                f"{symbol!r}"
            )
        if label in labels:
            raise CifBuildError(f"Duplicate atom-site label: {label}")
        labels.add(label)
        for axis in ("fract_x", "fract_y", "fract_z"):
            _finite_number(site.get(axis), f"atom_sites[{index}].{axis}")
        occupancy = _finite_number(
            site.get("occupancy", 1.0), f"atom_sites[{index}].occupancy"
        )
        if not 0 < occupancy <= 1:
            raise CifBuildError(
                f"atom_sites[{index}].occupancy must be in the interval (0, 1]"
            )
        if site.get("b_iso") is not None and site.get("u_iso") is not None:
            raise CifBuildError(
                f"atom_sites[{index}] must use either b_iso or u_iso, not both"
            )
        for displacement in ("b_iso", "u_iso"):
            if site.get(displacement) is not None:
                value = _finite_number(
                    site[displacement], f"atom_sites[{index}].{displacement}"
                )
                if value < 0:
                    raise CifBuildError(
                        f"atom_sites[{index}].{displacement} cannot be negative"
                    )
        if site.get("shared_site_group") is not None and not str(
            site["shared_site_group"]
        ).strip():
            raise CifBuildError(
                f"atom_sites[{index}].shared_site_group cannot be blank"
            )
        disorder_assembly = site.get("disorder_assembly")
        disorder_group = site.get("disorder_group")
        if (disorder_assembly is None) != (disorder_group is None):
            raise CifBuildError(
                f"atom_sites[{index}] must supply disorder_assembly and "
                "disorder_group together"
            )
        for field, value in (
            ("disorder_assembly", disorder_assembly),
            ("disorder_group", disorder_group),
        ):
            if value is not None and not str(value).strip():
                raise CifBuildError(f"atom_sites[{index}].{field} cannot be blank")


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise CifBuildError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CifBuildError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise CifBuildError(f"{field} must be finite")
    return number


def _verify_source(source: Mapping[str, Any], spec_dir: Path) -> dict[str, Any]:
    expected = str(source["sha256"]).lower()
    supplied_path = str(source.get("pdf_path", "")).strip()
    if not supplied_path:
        return {
            "status": "not_checked_no_local_path",
            "file_name": str(source["file_name"]),
            "sha256_expected": expected,
        }
    path = Path(supplied_path).expanduser()
    if not path.is_absolute():
        path = spec_dir / path
    path = path.resolve()
    if not path.is_file():
        raise CifBuildError(f"Source PDF does not exist: {path}")
    actual = _sha256(path)
    if actual != expected:
        raise CifBuildError(
            f"Source PDF SHA-256 mismatch for {path}: expected {expected}, got {actual}"
        )
    return {
        "status": "pass",
        "file_name": str(source["file_name"]),
        "path": str(path),
        "sha256_expected": expected,
        "sha256_actual": actual,
    }


def _verify_supporting_source(
    source: Mapping[str, Any], spec_dir: Path, index: int
) -> dict[str, Any]:
    expected = str(source["sha256"]).lower()
    supplied_path = str(source.get("file_path", "")).strip()
    if not supplied_path:
        return {
            "status": "not_checked_no_local_path",
            "file_name": str(source["file_name"]),
            "sha256_expected": expected,
        }
    path = Path(supplied_path).expanduser()
    if not path.is_absolute():
        path = spec_dir / path
    path = path.resolve()
    if not path.is_file():
        raise CifBuildError(
            f"Supporting source {index} does not exist: {path}"
        )
    actual = _sha256(path)
    if actual != expected:
        raise CifBuildError(
            f"Supporting source SHA-256 mismatch for {path}: "
            f"expected {expected}, got {actual}"
        )
    return {
        "status": "pass",
        "file_name": str(source["file_name"]),
        "path": str(path),
        "sha256_expected": expected,
        "sha256_actual": actual,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_cif(
    spec: Mapping[str, Any],
    space_group: gemmi.SpaceGroup,
    source_check: Mapping[str, Any],
    supporting_source_checks: Sequence[Mapping[str, Any]],
) -> str:
    cell = spec["cell"]
    source = spec["source"]
    group = spec["space_group"]
    lines = [
        f"data_{spec['structure_id']}",
        "",
        "_audit_creation_method "
        + gemmi.cif.quote("nanoMINER-K3 deterministic literature CIF builder"),
        "_audit_creation_date " + gemmi.cif.quote(str(spec.get("build_date", "?"))),
        "_chemical_name_common " + gemmi.cif.quote(str(spec["title"])),
        "_chemical_formula_sum "
        + gemmi.cif.quote(str(spec.get("formula_sum", "?"))),
        f"_cell_formula_units_Z {spec.get('formula_units_z', '?')}",
        f"_cell_length_a {_format_number(cell['a'])}",
        f"_cell_length_b {_format_number(cell['b'])}",
        f"_cell_length_c {_format_number(cell['c'])}",
        f"_cell_angle_alpha {_format_number(cell['alpha'])}",
        f"_cell_angle_beta {_format_number(cell['beta'])}",
        f"_cell_angle_gamma {_format_number(cell['gamma'])}",
        "_space_group_crystal_system "
        + gemmi.cif.quote(str(spec.get("crystal_system", "?"))),
        f"_space_group_IT_number {space_group.number}",
        "_space_group_name_H-M_alt " + gemmi.cif.quote(space_group.xhm()),
        "_nanominer_record_status draft_reconstructed_not_deposition_ready",
        "_nanominer_model_scope " + gemmi.cif.quote(str(spec["model_scope"])),
        "_nanominer_space_group_reported "
        + gemmi.cif.quote(str(group["reported"])),
        "_nanominer_space_group_build_setting "
        + gemmi.cif.quote(str(group["build_setting"])),
        "_nanominer_source_file " + gemmi.cif.quote(str(source["file_name"])),
        "_nanominer_source_sha256 " + gemmi.cif.quote(str(source["sha256"]).lower()),
        "_nanominer_source_hash_check "
        + gemmi.cif.quote(str(source_check["status"])),
    ]
    if spec.get("temperature_kelvin") is not None:
        lines.append(
            "_diffrn_ambient_temperature "
            + _format_number(spec["temperature_kelvin"])
        )
    if source.get("citation"):
        lines.append(
            "_citation_title " + gemmi.cif.quote(str(source["citation"]))
        )
    pages = source.get("pages")
    if pages:
        page_text = ",".join(str(page) for page in pages)
        lines.append("_nanominer_source_pages " + gemmi.cif.quote(page_text))
    if group.get("setting_note"):
        lines.append(
            "_nanominer_setting_note "
            + gemmi.cif.quote(str(group["setting_note"]))
        )
    notes = [str(note) for note in spec.get("notes", [])]
    if notes:
        lines.append("_nanominer_reconstruction_notes " + gemmi.cif.quote(" | ".join(notes)))

    if supporting_source_checks:
        lines.extend(
            [
                "",
                "loop_",
                "_nanominer_supporting_source_file",
                "_nanominer_supporting_source_sha256",
                "_nanominer_supporting_source_hash_check",
            ]
        )
        for source, check in zip(
            spec.get("supporting_sources", []), supporting_source_checks
        ):
            lines.append(
                " ".join(
                    [
                        gemmi.cif.quote(str(source["file_name"])),
                        gemmi.cif.quote(str(source["sha256"]).lower()),
                        gemmi.cif.quote(str(check["status"])),
                    ]
                )
            )

    lines.extend(
        [
            "",
            "loop_",
            "_space_group_symop_id",
            "_space_group_symop_operation_xyz",
        ]
    )
    for index, operation in enumerate(space_group.operations(), start=1):
        lines.append(f"{index} {gemmi.cif.quote(operation.triplet())}")

    lines.extend(
        [
            "",
            "loop_",
            "_atom_site_label",
            "_atom_site_type_symbol",
            "_atom_site_fract_x",
            "_atom_site_fract_y",
            "_atom_site_fract_z",
            "_atom_site_occupancy",
            "_atom_site_B_iso_or_equiv",
            "_atom_site_U_iso_or_equiv",
            "_atom_site_disorder_assembly",
            "_atom_site_disorder_group",
        ]
    )
    for site in spec["atom_sites"]:
        b_iso = "." if site.get("b_iso") is None else _format_number(site["b_iso"])
        u_iso = "." if site.get("u_iso") is None else _format_number(site["u_iso"])
        disorder_assembly = (
            "."
            if site.get("disorder_assembly") is None
            else gemmi.cif.quote(str(site["disorder_assembly"]))
        )
        disorder_group = (
            "."
            if site.get("disorder_group") is None
            else gemmi.cif.quote(str(site["disorder_group"]))
        )
        lines.append(
            " ".join(
                [
                    gemmi.cif.quote(str(site["label"])),
                    gemmi.cif.quote(str(site["type_symbol"])),
                    _format_fraction(site["fract_x"]),
                    _format_fraction(site["fract_y"]),
                    _format_fraction(site["fract_z"]),
                    _format_number(site.get("occupancy", 1.0), places=4),
                    b_iso,
                    u_iso,
                    disorder_assembly,
                    disorder_group,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _format_number(value: Any, places: int = 6) -> str:
    number = float(value)
    rendered = f"{number:.{places}f}".rstrip("0").rstrip(".")
    return rendered if "." in rendered else rendered + ".0"


def _format_fraction(value: Any) -> str:
    number = float(value) % 1.0
    if math.isclose(number, 1.0, abs_tol=1e-10):
        number = 0.0
    return f"{number:.8f}".rstrip("0").rstrip(".") or "0"


def _validate_generated_cif(
    spec: Mapping[str, Any],
    cif_text: str,
    space_group: gemmi.SpaceGroup,
    source_check: Mapping[str, Any],
    supporting_source_checks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        document = gemmi.cif.read_string(cif_text)
        block = document.sole_block()
    except Exception as exc:
        raise CifBuildError(f"Gemmi could not parse generated CIF: {exc}") from exc

    required_tags = (
        "_cell_length_a",
        "_cell_length_b",
        "_cell_length_c",
        "_cell_angle_alpha",
        "_cell_angle_beta",
        "_cell_angle_gamma",
        "_space_group_name_H-M_alt",
        "_atom_site_label",
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
        "_atom_site_occupancy",
    )
    missing_tags = [tag for tag in required_tags if not block.find_values(tag)]
    if missing_tags:
        raise CifBuildError(
            "Generated CIF is missing required tags: " + ", ".join(missing_tags)
        )
    try:
        parsed_structure = gemmi.make_small_structure_from_block(block)
    except Exception as exc:
        raise CifBuildError(
            f"Gemmi could not construct a crystal structure from the CIF: {exc}"
        ) from exc
    if len(parsed_structure.sites) != len(spec["atom_sites"]):
        raise CifBuildError(
            "Gemmi structure reader recovered "
            f"{len(parsed_structure.sites)} independent sites; "
            f"expected {len(spec['atom_sites'])}"
        )

    cell_spec = spec["cell"]
    cell = gemmi.UnitCell(
        float(cell_spec["a"]),
        float(cell_spec["b"]),
        float(cell_spec["c"]),
        float(cell_spec["alpha"]),
        float(cell_spec["beta"]),
        float(cell_spec["gamma"]),
    )
    if not cell.is_crystal():
        raise CifBuildError("Unit cell is not crystallographically valid")
    if not cell.is_compatible_with_spacegroup(space_group):
        raise CifBuildError(
            f"Unit cell is incompatible with space group {space_group.xhm()}"
        )

    expanded, multiplicities = _expand_sites(spec["atom_sites"], space_group)
    expected_count = spec.get("expected_expanded_sites")
    if expected_count is not None and len(expanded) != int(expected_count):
        raise CifBuildError(
            f"Symmetry expansion produced {len(expanded)} sites; "
            f"expected {expected_count}"
        )

    composition = Counter(atom["type_symbol"] for atom in expanded)
    occupancy_weighted: dict[str, float] = {}
    for atom in expanded:
        symbol = str(atom["type_symbol"])
        occupancy_weighted[symbol] = (
            occupancy_weighted.get(symbol, 0.0) + float(atom["occupancy"])
        )
    expected_composition = spec.get("expected_expanded_composition")
    if expected_composition is not None:
        normalized_expected = {
            str(symbol): int(count) for symbol, count in expected_composition.items()
        }
        if dict(composition) != normalized_expected:
            raise CifBuildError(
                "Expanded composition mismatch: "
                f"expected {normalized_expected}, got {dict(composition)}"
            )
    expected_weighted = spec.get("expected_occupancy_weighted_composition")
    if expected_weighted is not None:
        normalized_weighted = {
            str(symbol): float(count) for symbol, count in expected_weighted.items()
        }
        if set(occupancy_weighted) != set(normalized_weighted) or any(
            not math.isclose(
                occupancy_weighted[symbol],
                normalized_weighted[symbol],
                rel_tol=1e-8,
                abs_tol=1e-8,
            )
            for symbol in normalized_weighted
        ):
            raise CifBuildError(
                "Occupancy-weighted composition mismatch: "
                f"expected {normalized_weighted}, got {occupancy_weighted}"
            )

    geometry = _geometry_summary(expanded, cell)
    threshold = float(spec.get("minimum_distance_threshold", 0.5))
    if geometry["minimum_distance_angstrom"] < threshold:
        raise CifBuildError(
            "Expanded model has an implausible interatomic distance: "
            f"{geometry['minimum_distance_angstrom']:.4f} A < {threshold:.4f} A"
        )
    expectation_results = _check_distance_expectations(
        geometry["nearest_by_element_pair"], spec.get("distance_expectations", [])
    )

    return {
        "structure_id": str(spec["structure_id"]),
        "status": "pass",
        "record_status": "draft_reconstructed_not_deposition_ready",
        "parser": {
            "name": "gemmi",
            "version": gemmi.__version__,
            "cif_syntax_status": "pass",
            "small_structure_status": "pass",
            "independent_sites_recovered": len(parsed_structure.sites),
            "status": "pass",
        },
        "source_check": dict(source_check),
        "supporting_source_checks": [
            dict(check) for check in supporting_source_checks
        ],
        "space_group": {
            "reported": str(spec["space_group"]["reported"]),
            "build_setting": space_group.xhm(),
            "it_number": space_group.number,
            "hall": space_group.hall,
            "operation_count": len(space_group.operations()),
        },
        "cell": {
            **{key: float(cell_spec[key]) for key in ("a", "b", "c", "alpha", "beta", "gamma")},
            "volume_angstrom_cubed": cell.volume,
        },
        "atom_sites": {
            "asymmetric_unit_count": len(spec["atom_sites"]),
            "multiplicity_by_label": multiplicities,
            "expanded_count": len(expanded),
            "expanded_composition": dict(composition),
            "occupancy_weighted_composition": occupancy_weighted,
        },
        "geometry": geometry,
        "distance_expectations": expectation_results,
        "model_scope": str(spec["model_scope"]),
        "known_issues": [str(item) for item in spec.get("known_issues", [])],
    }


def _expand_sites(
    sites: Iterable[Mapping[str, Any]], space_group: gemmi.SpaceGroup
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    expanded: list[dict[str, Any]] = []
    multiplicities: dict[str, int] = {}
    for site in sites:
        start = (
            float(site["fract_x"]),
            float(site["fract_y"]),
            float(site["fract_z"]),
        )
        orbit: list[tuple[float, float, float]] = []
        for operation in space_group.operations():
            transformed = operation.apply_to_xyz(start)
            wrapped = tuple(_wrap_fraction(value) for value in transformed)
            if not any(_same_fractional(wrapped, existing) for existing in orbit):
                orbit.append(wrapped)
        multiplicities[str(site["label"])] = len(orbit)
        for sym_index, coordinate in enumerate(orbit, start=1):
            expanded.append(
                {
                    "label": str(site["label"]),
                    "symmetry_index": sym_index,
                    "type_symbol": str(site["type_symbol"]),
                    "occupancy": float(site.get("occupancy", 1.0)),
                    "shared_site_group": (
                        str(site["shared_site_group"])
                        if site.get("shared_site_group") is not None
                        else None
                    ),
                    "disorder_assembly": (
                        str(site["disorder_assembly"])
                        if site.get("disorder_assembly") is not None
                        else None
                    ),
                    "disorder_group": (
                        str(site["disorder_group"])
                        if site.get("disorder_group") is not None
                        else None
                    ),
                    "fract": coordinate,
                }
            )

    for left_index, left in enumerate(expanded):
        for right in expanded[left_index + 1 :]:
            if _same_fractional(left["fract"], right["fract"]):
                left_group = left.get("shared_site_group")
                right_group = right.get("shared_site_group")
                if left_group and left_group == right_group:
                    combined = float(left["occupancy"]) + float(right["occupancy"])
                    if combined > 1.0 + 1e-8:
                        raise CifBuildError(
                            f"Shared-site occupancies exceed 1.0 for group {left_group}: "
                            f"{left['label']} + {right['label']} = {combined}"
                        )
                    continue
                raise CifBuildError(
                    "Two asymmetric-unit sites expand onto the same position: "
                    f"{left['label']} and {right['label']}"
                )
    return expanded, multiplicities


def _wrap_fraction(value: float) -> float:
    wrapped = value % 1.0
    if math.isclose(wrapped, 1.0, abs_tol=1e-8) or math.isclose(
        wrapped, 0.0, abs_tol=1e-8
    ):
        return 0.0
    return wrapped


def _same_fractional(
    left: Sequence[float], right: Sequence[float], tolerance: float = 1e-7
) -> bool:
    return all(
        min(abs(a - b), 1.0 - abs(a - b)) <= tolerance
        for a, b in zip(left, right)
    )


def _geometry_summary(
    expanded: Sequence[Mapping[str, Any]], cell: gemmi.UnitCell
) -> dict[str, Any]:
    if len(expanded) < 2:
        raise CifBuildError("At least two symmetry-expanded atoms are required")
    pair_distances: dict[str, list[dict[str, Any]]] = {}
    all_pairs: list[dict[str, Any]] = []
    shared_position_pairs_skipped = 0
    exclusive_disorder_pairs_skipped = 0
    for left_index, left in enumerate(expanded):
        for right in expanded[left_index + 1 :]:
            if (
                _same_fractional(left["fract"], right["fract"])
                and left.get("shared_site_group")
                and left.get("shared_site_group") == right.get("shared_site_group")
            ):
                shared_position_pairs_skipped += 1
                continue
            if _mutually_exclusive_disorder(left, right):
                exclusive_disorder_pairs_skipped += 1
                continue
            distance = _periodic_distance(left["fract"], right["fract"], cell)
            key = "-".join(sorted((str(left["type_symbol"]), str(right["type_symbol"]))))
            record = {
                "pair": f"{left['label']}:{left['symmetry_index']}-{right['label']}:{right['symmetry_index']}",
                "elements": key,
                "distance_angstrom": distance,
            }
            pair_distances.setdefault(key, []).append(record)
            all_pairs.append(record)
    all_pairs.sort(key=lambda item: item["distance_angstrom"])
    nearest_by_pair = {
        key: min(records, key=lambda item: item["distance_angstrom"])
        for key, records in sorted(pair_distances.items())
    }
    return {
        "minimum_distance_angstrom": all_pairs[0]["distance_angstrom"],
        "nearest_pair": all_pairs[0],
        "nearest_by_element_pair": nearest_by_pair,
        "ten_shortest_pairs": all_pairs[:10],
        "shared_position_pairs_skipped": shared_position_pairs_skipped,
        "exclusive_disorder_pairs_skipped": exclusive_disorder_pairs_skipped,
    }


def _mutually_exclusive_disorder(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    assembly = left.get("disorder_assembly")
    return bool(
        assembly
        and assembly == right.get("disorder_assembly")
        and left.get("disorder_group")
        and right.get("disorder_group")
        and left.get("disorder_group") != right.get("disorder_group")
    )


def _periodic_distance(
    left: Sequence[float], right: Sequence[float], cell: gemmi.UnitCell
) -> float:
    base = [float(right[index]) - float(left[index]) for index in range(3)]
    best = math.inf
    for tx in (-1, 0, 1):
        for ty in (-1, 0, 1):
            for tz in (-1, 0, 1):
                delta = gemmi.Fractional(base[0] + tx, base[1] + ty, base[2] + tz)
                position = cell.orthogonalize(delta)
                distance = math.sqrt(
                    position.x * position.x
                    + position.y * position.y
                    + position.z * position.z
                )
                best = min(best, distance)
    return best


def _check_distance_expectations(
    nearest: Mapping[str, Mapping[str, Any]], expectations: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for expectation in expectations:
        pair = expectation.get("element_pair")
        if not isinstance(pair, list) or len(pair) != 2:
            raise CifBuildError("distance_expectations.element_pair must have two symbols")
        key = "-".join(sorted((str(pair[0]), str(pair[1]))))
        if key not in nearest:
            raise CifBuildError(f"No expanded atom pair is available for {key}")
        expected = float(expectation["value_angstrom"])
        tolerance = float(expectation.get("tolerance_angstrom", 0.1))
        observed = float(nearest[key]["distance_angstrom"])
        passed = abs(observed - expected) <= tolerance
        result = {
            "element_pair": key,
            "expected_angstrom": expected,
            "tolerance_angstrom": tolerance,
            "observed_angstrom": observed,
            "status": "pass" if passed else "fail",
        }
        results.append(result)
        if not passed:
            raise CifBuildError(
                f"Nearest {key} distance {observed:.4f} A is outside "
                f"{expected:.4f} +/- {tolerance:.4f} A"
            )
    return results
