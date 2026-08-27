from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExtractionProfile:
    profile_id: str
    title: str
    instructions: str


def load_profile(name_or_path: str) -> ExtractionProfile:
    candidate = Path(name_or_path)
    if candidate.is_file():
        source = candidate
    else:
        source = files("nanominer_k3").joinpath("profiles", f"{name_or_path}.json")
        if not source.is_file():
            raise ValueError(f"Unknown extraction profile: {name_or_path}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    return ExtractionProfile(
        profile_id=str(payload["profile_id"]),
        title=str(payload["title"]),
        instructions=str(payload["instructions"]),
    )
