"""Tests for the RBI Handbook driver — GSDP parsing + population projection.

The integration test against the real cached XLSX is skipped when the file
isn't present.
"""
from __future__ import annotations

import sqlite3

import pytest

from financeos.drivers.connectors.rbi.handbook import (
    ANNUAL_GROWTH_RATES,
    CENSUS_2011_POPULATION,
    DEFAULT_XLSX_PATH,
    HANDBOOK_STATE_NAME_TO_CODE,
    RbiHandbookDriver,
    parse_gsdp_xlsx,
    project_population,
)
from financeos.drivers.registries.loader import load_registries
from financeos.drivers.store.ingestor import Ingestor
from financeos.os.storage.db import init_schema


def test_population_projection_returns_baseline_at_2011():
    pop_2011 = project_population("KA", "2011-12")
    assert pop_2011 == CENSUS_2011_POPULATION["KA"]


def test_population_projection_grows_over_time():
    pop_2011 = project_population("KA", "2011-12")
    pop_2024 = project_population("KA", "2024-25")
    assert pop_2024 > pop_2011
    # KA growth is 1%/yr × 13 years ≈ 13.8% growth
    expected_ratio = (1 + ANNUAL_GROWTH_RATES["KA"]) ** 13
    assert pop_2024 / pop_2011 == pytest.approx(expected_ratio, abs=0.005)


def test_population_unknown_state_returns_none():
    assert project_population("XX", "2024-25") is None


def test_state_name_mapping_covers_south_5():
    for name, code in {"Karnataka": "KA", "Tamil Nadu": "TN", "Andhra Pradesh": "AP",
                       "Telangana": "TG", "Kerala": "KL"}.items():
        assert HANDBOOK_STATE_NAME_TO_CODE[name] == code


def test_state_name_mapping_handles_jk_asterisk():
    """Handbook flags J&K with '*' footnote — both forms must resolve."""
    assert HANDBOOK_STATE_NAME_TO_CODE["Jammu & Kashmir"] == "JK"
    assert HANDBOOK_STATE_NAME_TO_CODE["Jammu & Kashmir*"] == "JK"


@pytest.mark.skipif(not DEFAULT_XLSX_PATH.exists(),
                    reason="Handbook GSDP XLSX not cached")
def test_parse_gsdp_xlsx_against_real_file():
    """Smoke: parse the real Handbook XLSX, verify shape and a known value."""
    rows = parse_gsdp_xlsx(DEFAULT_XLSX_PATH)
    assert len(rows) > 100  # ~30 states × ~14 years
    # Karnataka 2024-25 should be ~28.84 lakh crore = 288 million lakh
    ka_2024 = [r for r in rows if r.state_name == "Karnataka"
               and r.fiscal_year == "2024-25"]
    assert len(ka_2024) == 1
    # Stored as Lakh in raw — value should be in the hundreds of millions
    assert 200_000_000 < ka_2024[0].gsdp_lakh < 350_000_000


@pytest.mark.skipif(not DEFAULT_XLSX_PATH.exists(),
                    reason="Handbook GSDP XLSX not cached")
def test_handbook_driver_writes_metadata_rows():
    """End-to-end: driver writes both GSDP and population to budget_metadata."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    regs = load_registries()
    ingestor = Ingestor(conn, regs)
    driver = RbiHandbookDriver(ingestor=ingestor, registries=regs)

    written = driver.fetch(states=["KA"], fiscal_years=["2024-25"])
    assert written >= 2  # at least gsdp + population

    rows = conn.execute(
        "SELECT * FROM budget_metadata WHERE state='KA' AND fiscal_year='2024-25'"
    ).fetchall()
    metrics = {r["metric"]: r["value"] for r in rows}
    assert "gsdp_inr_crore" in metrics
    assert "population_count" in metrics
    # Karnataka GSDP should be ~28 lakh crore (2.8M crore)
    assert 2_500_000 < metrics["gsdp_inr_crore"] < 3_000_000
    # Karnataka population ~70M
    assert 60_000_000 < metrics["population_count"] < 80_000_000
