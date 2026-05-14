"""PRS Legislative Research Budget Brief driver.

Source: PRS publishes one ~10-page brief per state per year at
https://prsindia.org/budgets/states. We don't parse the PDFs directly
(template varies year-to-year) — we pre-extract the headline numbers from
each state's brief landing page into a deterministic snapshot JSON
(`snapshots/<fiscal_year>.json`) and the driver ingests from there.

This driver is the second source for cells that RBI also covers. Its
purpose is **cross-validation** (confirming the numbers we extract from
RBI agree with PRS's independent extraction) and **early-publication
coverage** (PRS publishes BE briefs in Feb-March; RBI's e-STATES study
lands the following January).

Source precedence (enforced in `compute_metrics`): RBI > PRS. PRS rows
co-exist in the store via the source_id PK component; the read layer
prefers RBI when both are present.

Coverage: only the headline cells PRS publishes (~3-4 budget_signals
cells + GSDP per state-year). Detailed sectoral drill-down stays with
RBI.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from financeos.drivers.connectors.base import (
    BudgetDataSourceDriver,
    DriverFetchError,
)
from financeos.drivers.registries.loader import Registries, load_registries
from financeos.drivers.store.ingestor import Ingestor
from financeos.os.conformance import BudgetSignalRow, ConformanceResult

SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"
DATA_CONFIDENCE = 0.85   # web-scraped, single-source — lower than RBI's 0.95

# Map from PRS snapshot field name → (appendix, RBI-head, account_type)
# Only fields with a clean 1:1 RBI cell mapping are ingested as budget_signals.
# Fields like total_receipts_excl_borrowings and total_expenditure_excl_debt
# use broader definitions than any single RBI head — see methodology §3.
PRS_FIELD_TO_CELL: Dict[str, Tuple[str, str, str]] = {
    "own_tax_revenue":      ("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)", "revenue_receipt"),
    "total_revenue_exp":    ("Appendix-2", "Total: TOTAL EXPENDITURE (I+II+III)",   "revenue_exp"),
    "total_capital_outlay": ("Appendix-4", "I: Total Capital Outlay (1 + 2)",       "capital_exp"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_snapshots(directory: Path = SNAPSHOTS_DIR) -> List[str]:
    """Available fiscal years for which a snapshot exists."""
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))


def load_snapshot(fiscal_year: str, directory: Path = SNAPSHOTS_DIR) -> dict:
    p = directory / f"{fiscal_year}.json"
    if not p.exists():
        raise FileNotFoundError(f"PRS snapshot for {fiscal_year} not found at {p}")
    return json.loads(p.read_text())


class PrsBudgetBriefDriver(BudgetDataSourceDriver):
    domain = "prs_brief"
    cadence_hours = 8760.0
    produces_assessments = False
    signal_names = ["amount"]
    estimate_types = ["BE"]
    data_sources = ["PRS Legislative Research — State Budget Analyses"]
    source_id_template = "prs.brief.{state}.{fiscal_year}.{estimate_type}"

    def __init__(self, ingestor: Ingestor,
                 snapshots_dir: Optional[Path] = None,
                 registries: Optional[Registries] = None):
        self.ingestor = ingestor
        self.snapshots_dir = Path(snapshots_dir) if snapshots_dir else SNAPSHOTS_DIR
        self.registries = registries or load_registries()
        self._head_to_code = self._build_head_lookup()

    def _build_head_lookup(self) -> Dict[Tuple[str, str], str]:
        registry_path = (
            Path(__file__).resolve().parent.parent.parent
            / "registries" / "major_heads.json"
        )
        data = json.loads(registry_path.read_text())
        return {
            (e["rbi_appendix"], e["rbi_head"]): e["code"]
            for e in data["major_heads"]
            if e.get("rbi_appendix") and e.get("rbi_head")
        }

    def conformance_check(self) -> ConformanceResult:
        failures: List[str] = []
        warnings: List[str] = []
        if not self.snapshots_dir.exists() or not list_snapshots(self.snapshots_dir):
            failures.append(
                f"No PRS snapshots found at {self.snapshots_dir}. "
                "Re-run the scraper to populate at least one fiscal year."
            )
        return ConformanceResult(ok=not failures, failures=failures, warnings=warnings)

    def fetch(self, states: Sequence[str], fiscal_years: Sequence[str],
              force: bool = False) -> int:
        wanted_states = set(states)
        rows_to_write: List[BudgetSignalRow] = []
        gsdp_to_write: List[Tuple[str, str, float]] = []  # (state, fy, gsdp_crore)

        for fy in fiscal_years:
            try:
                snap = load_snapshot(fy, self.snapshots_dir)
            except FileNotFoundError:
                continue  # Skip fiscal years we don't have a snapshot for
            estimate_type = snap.get("estimate_type", "BE")
            for state, payload in snap.get("states", {}).items():
                if state not in wanted_states:
                    continue
                values = payload.get("values", {})

                # GSDP → budget_metadata
                gsdp = values.get("gsdp_inr_crore")
                if gsdp is not None:
                    gsdp_to_write.append((state, fy, float(gsdp)))

                # Mappable headline numbers → budget_signals
                for field, (appendix, head, account_type) in PRS_FIELD_TO_CELL.items():
                    v = values.get(field)
                    if v is None:
                        continue
                    code = self._head_to_code.get((appendix, head))
                    if code is None:
                        # Registry doesn't have this head — skip (will be caught by gate anyway)
                        continue
                    rows_to_write.append(BudgetSignalRow(
                        state=state, fiscal_year=fy, major_head_code=code,
                        account_type=account_type, signal="amount",
                        estimate_type=estimate_type, value=float(v),
                        unit="INR_CRORE", data_confidence=DATA_CONFIDENCE,
                        source_id=self.source_id_template.format(
                            state=state, fiscal_year=fy, estimate_type=estimate_type),
                    ))

        # Write GSDP to budget_metadata directly
        ingested_at = _now_iso()
        gsdp_written = 0
        for state, fy, gsdp_crore in gsdp_to_write:
            self.ingestor.conn.execute(
                """INSERT OR REPLACE INTO budget_metadata
                   (state, fiscal_year, metric, value, unit, source_id, ingested_at)
                   VALUES (?, ?, 'gsdp_inr_crore', ?, 'INR_CRORE', ?, ?)""",
                (state, fy, gsdp_crore, f"prs.brief.{state}.{fy}.BE", ingested_at),
            )
            gsdp_written += 1

        # Write signals through the ingestor (gets conformance gate)
        signals_written = 0
        if rows_to_write:
            for et in self.estimate_types:
                batch = [r for r in rows_to_write if r.estimate_type == et]
                if not batch:
                    continue
                written, result = self.ingestor.write_signals(
                    batch,
                    domain=self.domain,
                    declared_signal_names=self.signal_names,
                    declared_estimate_types=[et],
                    states_in_batch=sorted({r.state for r in batch}),
                    fiscal_years_in_batch=sorted({r.fiscal_year for r in batch}),
                )
                signals_written += written
                if not result.ok:
                    print(f"  [{self.domain}] {et}: BLOCKED — {result.failures}")

        return signals_written + gsdp_written
