"""End-to-end ingestor tests against an in-memory SQLite store."""
from __future__ import annotations

import sqlite3

import pytest

from financeos.drivers.registries.loader import load_registries
from financeos.drivers.store.ingestor import Ingestor
from financeos.os.conformance import BudgetSignalRow
from financeos.os.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def ingestor(conn):
    return Ingestor(conn, load_registries())


def _row(**overrides) -> BudgetSignalRow:
    base = dict(
        state="KA",
        fiscal_year="2024-25",
        major_head_code="2210",
        account_type="revenue_exp",
        signal="total_expenditure",
        estimate_type="BE",
        value=12345.67,
        unit="INR_CRORE",
        data_confidence=0.95,
        source_id="rbi.state_finances.2024-25.BE",
    )
    base.update(overrides)
    return BudgetSignalRow(**base)


def _write_kwargs():
    return dict(
        domain="test_driver",
        declared_signal_names=["total_expenditure"],
        declared_estimate_types=["BE"],
        states_in_batch=["KA"],
        fiscal_years_in_batch=["2024-25"],
    )


def test_round_trip_write(ingestor, conn):
    written, result = ingestor.write_signals([_row()], **_write_kwargs())
    assert written == 1
    assert result.ok

    stored = conn.execute("SELECT * FROM budget_signals").fetchall()
    assert len(stored) == 1
    assert stored[0]["state"] == "KA"
    assert stored[0]["value"] == pytest.approx(12345.67)
    assert stored[0]["ingested_at"] is not None


def test_blocking_failure_writes_zero_rows(ingestor, conn):
    written, result = ingestor.write_signals(
        [_row(state="XX")], **{**_write_kwargs(), "states_in_batch": ["XX"]}
    )
    assert written == 0
    assert not result.ok

    stored = conn.execute("SELECT * FROM budget_signals").fetchall()
    assert len(stored) == 0

    log = conn.execute("SELECT * FROM budget_ingest_log").fetchall()
    assert len(log) == 1
    assert log[0]["status"] == "error"
    assert log[0]["conformance_ok"] == 0


def test_idempotent_rewrite(ingestor, conn):
    ingestor.write_signals([_row(value=100.0)], **_write_kwargs())
    ingestor.write_signals([_row(value=200.0)], **_write_kwargs())

    stored = conn.execute("SELECT value FROM budget_signals").fetchall()
    assert len(stored) == 1
    assert stored[0]["value"] == pytest.approx(200.0)


def test_null_value_rows_are_skipped(ingestor, conn):
    rows = [_row(value=100.0), _row(value=None, major_head_code="2202")]
    written, result = ingestor.write_signals(rows, **_write_kwargs())
    assert written == 1
    assert result.ok  # Rule 9 is non-blocking

    stored = conn.execute("SELECT value FROM budget_signals").fetchall()
    assert len(stored) == 1


def test_successful_write_logged_with_ok_status(ingestor, conn):
    ingestor.write_signals([_row()], **_write_kwargs())
    log = conn.execute("SELECT * FROM budget_ingest_log").fetchall()
    assert len(log) == 1
    assert log[0]["status"] == "ok"
    assert log[0]["conformance_ok"] == 1
    assert log[0]["rows_written"] == 1
