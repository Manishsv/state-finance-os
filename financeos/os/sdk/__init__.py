"""FinanceOS SDK — DISCOVER, QUERY, and INGEST modes.

DISCOVER: inspect drivers and the cell schema without a store.
QUERY:    read from the store (added in Stage C+).
INGEST:   trigger drivers via the scheduler (added in Stage C+).

This module is the stable user-facing API. Internal modules SHOULD NOT be
imported directly by Apps.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Union

from financeos.drivers.registries.loader import Registries, load_registries

__all__ = ["list_drivers", "get_cell_schema", "get_registries"]


def list_drivers(
    registry_path: Optional[Union[Path, str]] = None,
) -> List[dict]:
    """DISCOVER: list trusted drivers from the driver registry."""
    if registry_path:
        p = Path(registry_path)
    else:
        p = (
            Path(__file__).resolve().parent.parent.parent
            / "drivers" / "registries" / "driver_registry.json"
        )
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    return [d for d in data.get("drivers", []) if d.get("trusted")]


def get_cell_schema() -> dict:
    """DISCOVER: return the canonical cell schema as a dict."""
    return {
        "cell": ["state", "fiscal_year", "major_head_code", "account_type"],
        "spec": "spec/CELL_SCHEMA.md",
        "version": "1.0.0-draft",
    }


def get_registries() -> Registries:
    """DISCOVER: load and return the canonical registries."""
    return load_registries()
