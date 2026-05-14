"""Tests for the cross-source disagreement check."""
from __future__ import annotations

import sqlite3

import pytest

from financeos.apps.cross_source import (
    CellDisagreement,
    find_disagreements,
    render_disagreement_report,
)
from financeos.os.storage.db import init_schema


def _plant(conn, source_id: str, code: str, value: float, state: str = "KA",
           year: str = "2024-25", et: str = "BE", account: str = "revenue_receipt"):
    conn.execute(
        """INSERT INTO budget_signals (state, fiscal_year, major_head_code,
           account_type, signal, estimate_type, value, unit, data_confidence,
           source_id, ingested_at)
           VALUES (?, ?, ?, ?, 'amount', ?, ?, 'INR_CRORE', 0.9, ?, 'now')""",
        (state, year, code, account, et, value, source_id),
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def test_no_disagreement_when_only_one_source(conn):
    _plant(conn, "rbi.estates.x", "9023", 100.0)
    out = find_disagreements(conn, ["KA"], "2024-25")
    assert out == []


def test_no_disagreement_when_sources_agree(conn):
    _plant(conn, "rbi.estates.x", "9023", 100.0)
    _plant(conn, "prs.brief.x", "9023", 100.0)
    out = find_disagreements(conn, ["KA"], "2024-25")
    assert out == []


def test_disagreement_above_threshold_is_flagged(conn):
    _plant(conn, "rbi.estates.x", "9023", 100.0)
    _plant(conn, "prs.brief.x", "9023", 110.0)  # 10% spread
    out = find_disagreements(conn, ["KA"], "2024-25", threshold_pct=1.0)
    assert len(out) == 1
    d = out[0]
    assert d.major_head_code == "9023"
    assert sorted(d.values) == [100.0, 110.0]
    assert d.spread_abs == 10.0
    assert d.spread_pct == pytest.approx(10.0)


def test_disagreement_below_threshold_is_not_flagged(conn):
    _plant(conn, "rbi.estates.x", "9023", 100.0)
    _plant(conn, "prs.brief.x", "9023", 100.5)  # 0.5% spread
    out = find_disagreements(conn, ["KA"], "2024-25", threshold_pct=1.0)
    assert out == []


def test_threshold_zero_returns_all_disagreements(conn):
    _plant(conn, "rbi.estates.x", "9023", 100.0)
    _plant(conn, "prs.brief.x", "9023", 100.5)
    out = find_disagreements(conn, ["KA"], "2024-25", threshold_pct=0.0)
    assert len(out) == 1


def test_disagreements_grouped_per_cell(conn):
    """Two cells with disagreements should produce two CellDisagreement objects."""
    _plant(conn, "rbi.estates.x", "9023", 100.0)
    _plant(conn, "prs.brief.x", "9023", 110.0)
    _plant(conn, "rbi.estates.x", "9174", 200.0, account="revenue_exp")
    _plant(conn, "prs.brief.x", "9174", 220.0, account="revenue_exp")
    out = find_disagreements(conn, ["KA"], "2024-25")
    assert len(out) == 2
    codes = {d.major_head_code for d in out}
    assert codes == {"9023", "9174"}


def test_render_no_disagreements_message(conn):
    out = render_disagreement_report([])
    assert "No disagreements found" in out


def test_render_disagreement_table():
    d = CellDisagreement(
        state="KA", fiscal_year="2024-25", major_head_code="9023",
        account_type="revenue_receipt", signal="amount", estimate_type="BE",
        sources=["rbi.estates.2025-26.BE", "prs.brief.KA.2024-25.BE"],
        values=[189893.0, 189000.0], spread_abs=893.0, spread_pct=0.47,
    )
    text = render_disagreement_report([d])
    assert "9023" in text
    assert "rbi" in text and "prs" in text
    assert "189,893" in text or "189893" in text
    assert "0.47" in text
