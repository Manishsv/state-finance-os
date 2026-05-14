"""Loaders for the canonical registries used by the conformance gate."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Union

REGISTRY_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Registries:
    state_codes: FrozenSet[str]
    state_names: Dict[str, str]
    major_head_codes: FrozenSet[str]
    major_head_descriptions: Dict[str, str]
    functional_categories: Dict[str, List[str]]


def load_registries(directory: Optional[Union[Path, str]] = None) -> Registries:
    """Load all registries from disk. Caller-supplied dir overrides default."""
    d = Path(directory) if directory else REGISTRY_DIR

    states_data = _load_json(d / "states.json")
    mh_data = _load_json(d / "major_heads.json")
    func_data = _load_json(d / "functional_categories.json")

    state_codes = frozenset(s["code"] for s in states_data["states"])
    state_names = {s["code"]: s["name"] for s in states_data["states"]}

    mh_codes = frozenset(mh["code"] for mh in mh_data["major_heads"])
    mh_desc = {mh["code"]: mh["description"] for mh in mh_data["major_heads"]}

    func_cats = {
        fc["category"]: list(fc["major_head_codes"])
        for fc in func_data["functional_categories"]
    }

    return Registries(
        state_codes=state_codes,
        state_names=state_names,
        major_head_codes=mh_codes,
        major_head_descriptions=mh_desc,
        functional_categories=func_cats,
    )


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Registry file not found: {path}")
    return json.loads(path.read_text())
