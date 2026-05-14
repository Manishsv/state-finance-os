"""Knowledge Store ingestor — runs the conformance gate and writes batches.

The ingestor is the only path that should write to budget_signals. It enforces:
- Conformance gate (CONFORMANCE.md rules 1-10) on every batch
- Atomic ingest log entry for every attempt (success, failure, or skip)
- Idempotent writes (INSERT OR REPLACE on the cell+signal+source PK)
- Server-side ingested_at timestamp (drivers do not set it)
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Sequence, Tuple

from financeos.drivers.registries.loader import Registries
from financeos.os.conformance import (
    BudgetSignalRow,
    ConformanceResult,
    check_batch,
)

SKIP_CONFORMANCE_ENV = "FINANCEOS_SKIP_CONFORMANCE"


class Ingestor:
    """Writes batches of BudgetSignalRow to the Knowledge Store via the gate."""

    def __init__(self, conn: sqlite3.Connection, registries: Registries):
        self.conn = conn
        self.registries = registries

    def write_signals(
        self,
        rows: Sequence[BudgetSignalRow],
        *,
        domain: str,
        declared_signal_names: Sequence[str],
        declared_estimate_types: Sequence[str],
        states_in_batch: Sequence[str],
        fiscal_years_in_batch: Sequence[str],
        declared_value_ranges: Optional[dict] = None,
    ) -> Tuple[int, ConformanceResult]:
        """Apply gate, write rows, log result. Returns (rows_written, result)."""
        started_at = _now_iso()

        if os.environ.get(SKIP_CONFORMANCE_ENV) == "true":
            # Per CONFORMANCE.md §"Gate Bypass" — test harnesses only.
            result = ConformanceResult(
                ok=True, warnings=["conformance gate bypassed via env var"],
            )
        else:
            result = check_batch(
                list(rows),
                domain=domain,
                declared_signal_names=declared_signal_names,
                declared_estimate_types=declared_estimate_types,
                valid_states=self.registries.state_codes,
                valid_major_heads=self.registries.major_head_codes,
                declared_value_ranges=declared_value_ranges,
            )

        if not result.ok:
            self._log_ingest(
                domain=domain,
                states=states_in_batch,
                fiscal_years=fiscal_years_in_batch,
                rows_written=0,
                status="error",
                conformance=result,
                started_at=started_at,
            )
            return 0, result

        ingested_at = _now_iso()
        rows_written = 0
        for r in rows:
            if r.value is None or (isinstance(r.value, float) and math.isnan(r.value)):
                continue  # Rule 9: silently skip null/NaN values
            self.conn.execute(
                """
                INSERT OR REPLACE INTO budget_signals
                (state, fiscal_year, major_head_code, account_type, signal,
                 estimate_type, value, unit, data_confidence, source_id, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.state, r.fiscal_year, r.major_head_code, r.account_type,
                    r.signal, r.estimate_type, float(r.value), r.unit,
                    r.data_confidence, r.source_id, ingested_at,
                ),
            )
            rows_written += 1

        self._log_ingest(
            domain=domain,
            states=states_in_batch,
            fiscal_years=fiscal_years_in_batch,
            rows_written=rows_written,
            status="ok",
            conformance=result,
            started_at=started_at,
        )
        return rows_written, result

    def _log_ingest(
        self,
        *,
        domain: str,
        states: Sequence[str],
        fiscal_years: Sequence[str],
        rows_written: int,
        status: str,
        conformance: ConformanceResult,
        started_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO budget_ingest_log
            (domain, states, fiscal_years, rows_written, status,
             conformance_ok, conformance_failures, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                domain,
                json.dumps(sorted(set(states))),
                json.dumps(sorted(set(fiscal_years))),
                rows_written,
                status,
                1 if conformance.ok else 0,
                json.dumps(conformance.messages),
                started_at,
                _now_iso(),
            ),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
