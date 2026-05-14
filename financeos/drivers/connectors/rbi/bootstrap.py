"""Bootstrap script: extend major_heads.json with synthetic 9XXX codes
for every (appendix, head) pair in the cached RBI XLSX.

Drivers do not mutate registries — that responsibility lives here.

Idempotent: re-running only adds codes for new (appendix, head) pairs that
the registry has not seen before. Existing assignments are never reshuffled.
The registry is the source of truth for which synthetic code maps to which
RBI head; once persisted, an assignment is permanent.

Usage:
    python -m financeos.drivers.connectors.rbi.bootstrap
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Set, Tuple

import openpyxl

DEFAULT_XLSX_PATH = Path("data/raw/rbi/estates_2025-26.xlsx")
REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "registries" / "major_heads.json"
)
SYNTHETIC_CODE_START = 9001
SYNTHETIC_CODE_MAX = 9999


def extract_distinct_heads(xlsx_path: Path) -> Set[Tuple[str, str]]:
    """Return all distinct (appendix, head) pairs in the cached XLSX."""
    pairs: Set[Tuple[str, str]] = set()
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        ws = wb["Data"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                break
            ap, _, head = row[0], row[1], str(row[2])
            pairs.add((str(ap), head))
    finally:
        wb.close()
    return pairs


def bootstrap_registry(
    xlsx_path: Path = DEFAULT_XLSX_PATH,
    registry_path: Path = REGISTRY_PATH,
) -> int:
    """Add 9XXX synthetic codes for new (appendix, head) pairs.
    Returns the number of new codes added.
    """
    if not xlsx_path.exists():
        raise FileNotFoundError(f"RBI XLSX not cached: {xlsx_path}")

    registry = json.loads(registry_path.read_text())

    existing_pairs = {
        (e.get("rbi_appendix"), e.get("rbi_head"))
        for e in registry["major_heads"]
        if e.get("rbi_appendix") and e.get("rbi_head")
    }
    used_codes = {e["code"] for e in registry["major_heads"]}

    pairs = extract_distinct_heads(xlsx_path)
    new_pairs = sorted(pairs - existing_pairs)

    if not new_pairs:
        print("Registry is up to date — no new heads to add.")
        return 0

    next_code = SYNTHETIC_CODE_START
    added = 0
    for ap, head in new_pairs:
        while f"{next_code:04d}" in used_codes:
            next_code += 1
        if next_code > SYNTHETIC_CODE_MAX:
            raise RuntimeError(
                f"Exhausted 9XXX code space at {next_code}; "
                f"cannot add more synthetic heads."
            )
        code = f"{next_code:04d}"
        registry["major_heads"].append({
            "code": code,
            "description": f"{head} [RBI {ap}]",
            "section": "rbi_synthetic",
            "rbi_appendix": ap,
            "rbi_head": head,
        })
        used_codes.add(code)
        next_code += 1
        added += 1

    registry["major_heads"].sort(key=lambda e: e["code"])
    registry_path.write_text(json.dumps(registry, indent=2) + "\n")
    print(f"Added {added} new synthetic codes to {registry_path}")
    return added


if __name__ == "__main__":
    sys.exit(0 if bootstrap_registry() >= 0 else 1)
