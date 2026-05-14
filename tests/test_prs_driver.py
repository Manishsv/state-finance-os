"""Tests for the PRS Budget Brief driver and source-precedence behavior."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from financeos.drivers.connectors.prs.budget_brief import (
    PRS_FIELD_TO_CELL,
    PrsBudgetBriefDriver,
    SNAPSHOTS_DIR,
    list_snapshots,
    load_snapshot,
)
from financeos.drivers.registries.loader import load_registries
from financeos.drivers.store.ingestor import Ingestor
from financeos.os.storage.db import init_schema


def _make_snapshot(tmp_path: Path, fiscal_year: str = "2024-25") -> Path:
    """Write a synthetic PRS snapshot to tmp_path/snapshots/."""
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    snap = {
        "snapshot_version": "1.0.0",
        "estimate_type": "BE",
        "fiscal_year": fiscal_year,
        "states": {
            "KA": {
                "url": "https://example.test/ka",
                "values": {
                    "gsdp_inr_crore": 2_500_000,
                    "own_tax_revenue": 180_000,
                    "total_revenue_exp": 280_000,
                    "total_capital_outlay": 50_000,
                },
            },
        },
    }
    (snap_dir / f"{fiscal_year}.json").write_text(json.dumps(snap))
    return snap_dir


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def test_field_mapping_covers_three_overlapping_cells():
    """Sanity: PRS_FIELD_TO_CELL covers exactly the cells that map cleanly to RBI."""
    assert "own_tax_revenue" in PRS_FIELD_TO_CELL
    assert "total_revenue_exp" in PRS_FIELD_TO_CELL
    assert "total_capital_outlay" in PRS_FIELD_TO_CELL
    # Combined sectoral fields don't map cleanly, so should NOT be in the mapping
    assert "education_total" not in PRS_FIELD_TO_CELL
    assert "health_total" not in PRS_FIELD_TO_CELL


def test_real_snapshot_exists_and_loads():
    """The shipped 2024-25 snapshot should load and contain South-5 data."""
    years = list_snapshots()
    assert "2024-25" in years
    snap = load_snapshot("2024-25")
    states = snap["states"]
    for code in ("KA", "TN", "AP", "TG", "KL"):
        assert code in states
        assert "gsdp_inr_crore" in states[code]["values"]
        assert "own_tax_revenue" in states[code]["values"]


def test_driver_writes_signals_and_metadata(conn, tmp_path):
    snap_dir = _make_snapshot(tmp_path)
    regs = load_registries()
    ingestor = Ingestor(conn, regs)
    driver = PrsBudgetBriefDriver(ingestor=ingestor, snapshots_dir=snap_dir, registries=regs)

    written = driver.fetch(states=["KA"], fiscal_years=["2024-25"])
    # 3 signals + 1 metadata = 4
    assert written == 4

    # Check signals — should be 3 PRS rows for KA 2024-25 BE
    sigs = conn.execute(
        "SELECT major_head_code, value, source_id FROM budget_signals "
        "WHERE state='KA' AND source_id LIKE 'prs.%'"
    ).fetchall()
    assert len(sigs) == 3
    by_code = {r["major_head_code"]: r["value"] for r in sigs}
    # Verify mapped values
    head_to_code = driver._head_to_code
    assert by_code[head_to_code[("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)")]] == 180_000
    assert by_code[head_to_code[("Appendix-2", "Total: TOTAL EXPENDITURE (I+II+III)")]] == 280_000
    assert by_code[head_to_code[("Appendix-4", "I: Total Capital Outlay (1 + 2)")]] == 50_000

    # Check metadata — GSDP from PRS
    meta = conn.execute(
        "SELECT value, source_id FROM budget_metadata "
        "WHERE state='KA' AND fiscal_year='2024-25' AND metric='gsdp_inr_crore' "
        "AND source_id LIKE 'prs.%'"
    ).fetchone()
    assert meta is not None
    assert meta["value"] == 2_500_000


def test_driver_passes_conformance_gate(conn, tmp_path):
    """PRS rows go through the same gate as RBI rows."""
    snap_dir = _make_snapshot(tmp_path)
    regs = load_registries()
    ingestor = Ingestor(conn, regs)
    driver = PrsBudgetBriefDriver(ingestor=ingestor, snapshots_dir=snap_dir, registries=regs)
    driver.fetch(states=["KA"], fiscal_years=["2024-25"])

    # The ingest log should show conformance OK for the PRS write
    log = conn.execute(
        "SELECT * FROM budget_ingest_log WHERE domain='prs_brief'"
    ).fetchall()
    assert len(log) >= 1
    assert all(r["conformance_ok"] == 1 for r in log)


def test_source_precedence_rbi_wins_over_prs(conn, tmp_path):
    """When both RBI and PRS write the same cell, compute_metrics uses RBI."""
    from financeos.apps.metrics import build_head_to_code, load_signal_values

    h2c = build_head_to_code()
    own_tax_code = h2c[("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)")]
    rev_exp_code = h2c[("Appendix-2", "Total: TOTAL EXPENDITURE (I+II+III)")]

    # Plant RBI values (the canonical numbers)
    for code, value in [(own_tax_code, 100_000), (rev_exp_code, 200_000)]:
        conn.execute(
            """INSERT INTO budget_signals (state, fiscal_year, major_head_code,
               account_type, signal, estimate_type, value, unit, data_confidence,
               source_id, ingested_at)
               VALUES ('KA', '2024-25', ?, 'revenue_receipt', 'amount', 'BE', ?,
                       'INR_CRORE', 0.95, 'rbi.estates.2025-26.BE', 'now')""",
            (code, value),
        )

    # Plant PRS values that disagree
    for code, value in [(own_tax_code, 99_500), (rev_exp_code, 200_500)]:
        conn.execute(
            """INSERT INTO budget_signals (state, fiscal_year, major_head_code,
               account_type, signal, estimate_type, value, unit, data_confidence,
               source_id, ingested_at)
               VALUES ('KA', '2024-25', ?, 'revenue_receipt', 'amount', 'BE', ?,
                       'INR_CRORE', 0.85, 'prs.brief.KA.2024-25.BE', 'now')""",
            (code, value),
        )

    # load_signal_values must return RBI's values, not PRS's
    values = load_signal_values(conn, ["KA"], "2024-25", "BE")
    assert values[("KA", own_tax_code)] == 100_000   # RBI wins
    assert values[("KA", rev_exp_code)] == 200_000   # RBI wins


def test_source_precedence_prs_used_when_rbi_absent(conn, tmp_path):
    """If only PRS has a value for the cell, PRS is used."""
    from financeos.apps.metrics import build_head_to_code, load_signal_values

    h2c = build_head_to_code()
    own_tax_code = h2c[("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)")]

    # Only PRS has a value for this cell (RBI did not publish)
    conn.execute(
        """INSERT INTO budget_signals (state, fiscal_year, major_head_code,
           account_type, signal, estimate_type, value, unit, data_confidence,
           source_id, ingested_at)
           VALUES ('KA', '2025-26', ?, 'revenue_receipt', 'amount', 'BE', 195000,
                   'INR_CRORE', 0.85, 'prs.brief.KA.2025-26.BE', 'now')""",
        (own_tax_code,),
    )

    values = load_signal_values(conn, ["KA"], "2025-26", "BE")
    assert values[("KA", own_tax_code)] == 195_000   # PRS as fallback
