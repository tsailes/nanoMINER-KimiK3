# Literature CIF build specification

`nanominer-k3 cif-build` converts one curator-reviewed JSON object into a
traceable draft CIF. The format is intentionally smaller than the project's
full literature schema: a record only needs enough information to place atoms
in a periodic crystal model and explain every reconstruction choice.

## Required fields

```json
{
  "structure_id": "stable_ascii_identifier",
  "title": "human-readable model name",
  "source": {
    "file_name": "paper.pdf",
    "pdf_path": "C:/optional/local/path/paper.pdf",
    "sha256": "64 lowercase or uppercase hex characters",
    "pages": [2, 5],
    "citation": "optional citation text"
  },
  "cell": {
    "a": 7.4,
    "b": 4.93,
    "c": 2.534,
    "alpha": 90,
    "beta": 90,
    "gamma": 90
  },
  "space_group": {
    "reported": "Pnam",
    "build_setting": "P n a m",
    "setting_note": "why this coordinate-compatible setting was selected"
  },
  "atom_sites": [
    {
      "label": "C1",
      "type_symbol": "C",
      "fract_x": 0.038,
      "fract_y": 0.935,
      "fract_z": 0.25,
      "occupancy": 1.0,
      "b_iso": 0.7,
      "shared_site_group": "optional_exact_mixed_site",
      "disorder_assembly": "optional_conformational_disorder_assembly",
      "disorder_group": "optional_alternative_state"
    }
  ],
  "model_scope": "reported_carbon_backbone_only"
}
```

`pdf_path` may be omitted when the source is not present on the current host;
the expected digest is still written to the CIF, but the validation report will
say `not_checked_no_local_path`. If the file is available, a digest mismatch is
a hard error.

Native CIF, PDB, reflection, or other supporting files may be recorded and
verified independently from the article PDF:

```json
{
  "supporting_sources": [
    {
      "file_name": "100K.cif",
      "file_path": "C:/local/path/100K.cif",
      "sha256": "64 hexadecimal characters"
    }
  ]
}
```

Each checked supporting file is serialized in a `_nanominer_supporting_source_*`
loop and returned in `supporting_source_checks`. A digest mismatch is a hard
error. `file_path` may be omitted when only the published digest is available.

`reported` preserves the paper's symbol verbatim. `build_setting` must name the
exact Gemmi setting used with the coordinates. They may differ, but the reason
must be stated; this is common in historical Pnam/Pnma and nonstandard polymer
settings.

`occupancy` defaults to 1.0. Use either `b_iso` or `u_iso` when the source value
or a documented conversion is defensible; do not supply both. Two elements may
share exactly the same crystallographic position only when both carry the same
non-empty `shared_site_group` and their pairwise occupancies do not exceed 1.0.
This represents a mixed average site, not two overlapping atoms.

For mutually exclusive alternative conformers, supply both `disorder_assembly`
and `disorder_group` on every affected atom. Atoms in different groups of the
same assembly are serialized using the standard CIF disorder columns and their
cross-conformer distances are excluded from geometry checks because those atoms
never coexist. Contacts within one conformer remain fully validated. Atom labels
must still be unique, for example `C1A` and `C1B`.

## Recommended validation assertions

```json
{
  "formula_sum": "C H2",
  "formula_units_z": 4,
  "crystal_system": "orthorhombic",
  "temperature_kelvin": 4.0,
  "expected_expanded_sites": 4,
  "expected_expanded_composition": {"C": 4},
  "expected_occupancy_weighted_composition": {"C": 4},
  "minimum_distance_threshold": 0.5,
  "distance_expectations": [
    {
      "element_pair": ["C", "C"],
      "value_angstrom": 1.53,
      "tolerance_angstrom": 0.03
    }
  ],
  "notes": ["reported/derived/reconstructed provenance"],
  "known_issues": ["hydrogen atoms are absent"]
}
```

`expected_expanded_composition` counts symmetry-generated positions without
occupancy weighting. `expected_occupancy_weighted_composition` is the chemical
count after occupancy and is important for average disorder models. For
example, 72 generated C positions at occupancy 0.5 must yield 36 C.

## What `pass` means

A passing build confirms:

- the local source hash, when checked;
- CIF parsing by Gemmi;
- a valid unit cell compatible with the selected space group;
- symmetry multiplicities and optional composition assertions;
- absence of coincident expanded sites and implausibly short contacts;
- optional literature bond-distance assertions.

It does not prove that a historical origin choice, disorder interpretation,
hydrogen reconstruction, or transferred coordinate model is scientifically
unique. Such limitations belong in `model_scope`, `notes`, and `known_issues`,
and the output remains `draft_reconstructed_not_deposition_ready`.
