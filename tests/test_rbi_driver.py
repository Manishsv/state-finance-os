"""Tests for the RBI State Finances driver.

Pure-logic tests run unconditionally. The integration test that exercises
the full driver against the real cached XLSX is skipped if the file isn't
present and skipped if the registry hasn't been bootstrapped.
"""
from __future__ import annotations

import sqlite3

import pytest

from financeos.drivers.connectors.rbi.state_finances import (
    APPENDIX_TO_ACCOUNT_TYPE,
    DEFAULT_XLSX_PATH,
    RBI_STATE_NAME_TO_CODE,
    RbiStateFinancesDriver,
    convert_fiscal_year,
    fy_financeos_to_rbi,
)
from financeos.drivers.registries.loader import load_registries
from financeos.drivers.store.ingestor import Ingestor
from financeos.os.storage.db import init_schema


def test_fiscal_year_round_trip():
    assert convert_fiscal_year("2024-2025") == "2024-25"
    assert convert_fiscal_year("1990-1991") == "1990-91"
    assert convert_fiscal_year("2099-2100") == "2099-00"
    assert fy_financeos_to_rbi("2024-25") == "2024-2025"
    assert fy_financeos_to_rbi("1990-91") == "1990-1991"


def test_state_mapping_covers_south_5():
    for code in ("KA", "TN", "AP", "TG", "KL"):
        assert code in RBI_STATE_NAME_TO_CODE.values(), f"{code} missing"


def test_appendix_mapping_complete_and_canonical():
    assert APPENDIX_TO_ACCOUNT_TYPE["Appendix-1"] == "revenue_receipt"
    assert APPENDIX_TO_ACCOUNT_TYPE["Appendix-2"] == "revenue_exp"
    assert APPENDIX_TO_ACCOUNT_TYPE["Appendix-3"] == "capital_receipt"
    assert APPENDIX_TO_ACCOUNT_TYPE["Appendix-4"] == "capital_exp"


@pytest.mark.skipif(not DEFAULT_XLSX_PATH.exists(),
                    reason="RBI XLSX not cached at data/raw/rbi/")
def test_driver_smoke_run_against_real_xlsx():
    """End-to-end: driver -> conformance gate -> store, against real XLSX."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    regs = load_registries()
    ingestor = Ingestor(conn, regs)
    driver = RbiStateFinancesDriver(ingestor=ingestor, registries=regs)

    cr = driver.conformance_check()
    if not cr.ok:
        pytest.skip(f"Driver not ready: {cr.failures}")

    written = driver.fetch(states=["KA"], fiscal_years=["2024-25"])
    assert written > 0, "Driver should have written some rows for KA 2024-25"

    rows = conn.execute(
        "SELECT * FROM budget_signals WHERE state='KA' AND fiscal_year='2024-25'"
    ).fetchall()
    assert len(rows) > 0

    account_types = {r["account_type"] for r in rows}
    # All four account_types should appear if all 4 appendices have data
    assert "revenue_receipt" in account_types
    assert "revenue_exp" in account_types

    # Every row carries valid confidence and a known unit
    for r in rows:
        assert 0.0 <= r["data_confidence"] <= 1.0
        assert r["unit"] == "INR_CRORE"
        assert r["estimate_type"] in {"BE", "RE", "ACT"}
