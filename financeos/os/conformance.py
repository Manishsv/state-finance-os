"""Conformance gate for the Knowledge Store.

Implements the 10 rules defined in spec/CONFORMANCE.md. The gate is the only
validator for batches submitted to the ingestor. Blocking failures cause the
entire batch to be rejected (zero rows written). Warnings are logged but do
not block the write.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

VALID_ACCOUNT_TYPES = frozenset({
    "revenue_receipt", "capital_receipt", "revenue_exp", "capital_exp",
})
VALID_ESTIMATE_TYPES = frozenset({"BE", "RE", "ACT"})
VALID_UNITS = frozenset({"INR_CRORE", "INR_LAKH", "RATIO", "PCT"})
FISCAL_YEAR_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass
class BudgetSignalRow:
    """One row destined for budget_signals. Field semantics per CELL_SCHEMA.md."""

    state: str
    fiscal_year: str
    major_head_code: str
    account_type: str
    signal: str
    estimate_type: str
    value: Optional[float]
    unit: str
    data_confidence: float
    source_id: str
    ingested_at: Optional[str] = None  # set by ingestor, not the driver


@dataclass
class ConformanceResult:
    ok: bool
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def messages(self) -> List[str]:
        """All messages with [FAIL] / [WARN] prefixes for ingest_log storage."""
        return (
            [f"[FAIL] {m}" for m in self.failures]
            + [f"[WARN] {m}" for m in self.warnings]
        )


def fiscal_year_valid(fy: str) -> bool:
    """`YYYY-YY` where YY is the last two digits of the year following YYYY."""
    m = FISCAL_YEAR_PATTERN.match(fy)
    if not m:
        return False
    yyyy, yy = int(m.group(1)), int(m.group(2))
    return yy == (yyyy + 1) % 100


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def check_batch(
    rows: List[BudgetSignalRow],
    *,
    domain: str,
    declared_signal_names: Iterable[str],
    declared_estimate_types: Iterable[str],
    valid_states: Iterable[str],
    valid_major_heads: Iterable[str],
    declared_value_ranges: Optional[dict] = None,
) -> ConformanceResult:
    """Apply CONFORMANCE.md rules 1-10 to a batch.

    The caller (ingestor) decides what to do with the result: blocking
    failures mean no write; warnings mean write but log.
    """
    failures: List[str] = []
    warnings: List[str] = []

    declared_signal_names_set = set(declared_signal_names)
    declared_estimate_types_set = set(declared_estimate_types)
    valid_states_set = set(valid_states)
    valid_major_heads_set = set(valid_major_heads)
    declared_value_ranges = declared_value_ranges or {}

    # Rule 1 — data_confidence populated and in [0, 1]
    bad_conf = [
        r for r in rows
        if r.data_confidence is None
        or _is_nan(r.data_confidence)
        or not (0.0 <= r.data_confidence <= 1.0)
    ]
    if bad_conf:
        sample = [
            (r.state, r.fiscal_year, r.major_head_code, r.signal)
            for r in bad_conf[:3]
        ]
        failures.append(
            f"[{domain}] data_confidence is null or out of [0,1] for "
            f"{len(bad_conf)} row(s): sample: {sample}. "
            "Every row must declare confidence in [0.0, 1.0]."
        )

    # Rule 2 — declared signals absent (warning)
    actual_signals = {r.signal for r in rows}
    missing = declared_signal_names_set - actual_signals
    if missing:
        warnings.append(
            f"[{domain}] Declared signal(s) absent from rows: {sorted(missing)}. "
            "May be legitimate (state does not levy this) or a driver bug."
        )

    # Rule 3 — Major Head in canonical registry
    bad_mh = sorted({
        r.major_head_code for r in rows
        if r.major_head_code not in valid_major_heads_set
    })
    if bad_mh:
        failures.append(
            f"[{domain}] {len(bad_mh)} row(s) reference unknown major_head_code(s): "
            f"{bad_mh[:5]}. Add to registries/major_heads.json or fix the driver mapping."
        )

    # Rule 4 — state code valid
    bad_states = sorted({r.state for r in rows if r.state not in valid_states_set})
    if bad_states:
        failures.append(
            f"[{domain}] {len(bad_states)} row(s) reference unknown state code(s): "
            f"{bad_states}. Use ISO 3166-2:IN codes (e.g. KA, TN, AP, TG, KL)."
        )

    # Rule 5 — estimate_type valid AND in driver's declared list
    bad_et_global = sorted({
        r.estimate_type for r in rows
        if r.estimate_type not in VALID_ESTIMATE_TYPES
    })
    if bad_et_global:
        failures.append(
            f"[{domain}] row(s) have invalid estimate_type: {bad_et_global}. "
            "Must be one of {BE, RE, ACT}."
        )
    bad_et_driver = sorted({
        r.estimate_type for r in rows
        if r.estimate_type in VALID_ESTIMATE_TYPES
        and r.estimate_type not in declared_estimate_types_set
    })
    if bad_et_driver:
        failures.append(
            f"[{domain}] driver did not declare estimate_type(s) {bad_et_driver}; "
            f"declared: {sorted(declared_estimate_types_set)}."
        )

    # Rule 6 — account_type valid
    bad_at = sorted({
        r.account_type for r in rows if r.account_type not in VALID_ACCOUNT_TYPES
    })
    if bad_at:
        failures.append(
            f"[{domain}] row(s) have invalid account_type: {bad_at}. "
            f"Must be one of {sorted(VALID_ACCOUNT_TYPES)}."
        )

    # Rule 7 — fiscal_year format
    bad_fy = sorted({r.fiscal_year for r in rows if not fiscal_year_valid(r.fiscal_year)})
    if bad_fy:
        failures.append(
            f"[{domain}] row(s) have invalid fiscal_year format: {bad_fy}. "
            "Must be YYYY-YY where YY = last two digits of YYYY+1."
        )

    # Rule 8 — unit valid
    bad_units = sorted({r.unit for r in rows if r.unit not in VALID_UNITS})
    if bad_units:
        failures.append(
            f"[{domain}] row(s) have unknown unit: {bad_units}. "
            f"Must be one of {sorted(VALID_UNITS)}."
        )

    # Rule 9 — null/NaN values (warning; ingestor will skip them)
    null_count = sum(1 for r in rows if r.value is None or _is_nan(r.value))
    if null_count and len(rows) and (null_count / len(rows)) > 0.05:
        warnings.append(
            f"[{domain}] {null_count}/{len(rows)} rows have null/NaN value. "
            "These rows will be skipped by write_signals()."
        )

    # Rule 10 — value range violations (warning)
    for signal_name, bounds in declared_value_ranges.items():
        vmin, vmax = bounds
        out_of_range = [
            r for r in rows
            if r.signal == signal_name
            and r.value is not None
            and not _is_nan(r.value)
            and not (vmin <= r.value <= vmax)
        ]
        if out_of_range:
            samples = [(r.state, r.fiscal_year, r.value) for r in out_of_range[:3]]
            warnings.append(
                f"[{domain}] {len(out_of_range)} rows for signal '{signal_name}' "
                f"have values outside declared range [{vmin}, {vmax}]: {samples}."
            )

    return ConformanceResult(
        ok=len(failures) == 0,
        failures=failures,
        warnings=warnings,
    )
