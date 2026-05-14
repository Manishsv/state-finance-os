"""Tests for metric computation against hand-computable known values."""
from __future__ import annotations

import sqlite3

import pytest

from financeos.apps.compare import build_findings
from financeos.apps.metrics import (
    METRICS,
    MetricRow,
    TREND_METRICS,
    TrendMetricDef,
    _compute_cagr,
    _years_between,
    build_head_to_code,
    compute_metrics,
    compute_trend_metrics,
)
from financeos.os.storage.db import init_schema


@pytest.fixture
def conn_with_synthetic_data():
    """Build a small in-memory store with known values for one state."""
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)

    head_to_code = build_head_to_code()
    # Plant known values: KA 2024-25 BE
    plant = [
        # (appendix, head, value_in_crore, account_type)
        (("Appendix-1", "Total: TOTAL REVENUE (I+II)"), 1000.0, "revenue_receipt"),
        (("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)"), 700.0, "revenue_receipt"),
        (("Appendix-1", "I.B: Share in Central Taxes (i to ix)"), 200.0, "revenue_receipt"),
        (("Appendix-1", "II.D: Grants from the Centre (1 to 7)"), 50.0, "revenue_receipt"),
        (("Appendix-1", "II.C: State's Own Non-Tax Revenue (1 to 6)"), 50.0, "revenue_receipt"),
        (("Appendix-2", "Total: TOTAL EXPENDITURE (I+II+III)"), 1100.0, "revenue_exp"),
        (("Appendix-2", "I.A: Social Services (1 to 12)"), 500.0, "revenue_exp"),
        (("Appendix-2", "I.B: Economic Services (1 to 9)"), 200.0, "revenue_exp"),
        (("Appendix-2", "I: DEVELOPMENTAL EXPENDITURE (A + B)"), 700.0, "revenue_exp"),
        (("Appendix-2", "II: NON-DEVELOPMENTAL EXPENDITURE (General Services) (A to F)"), 400.0, "revenue_exp"),
        (("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"), 110.0, "revenue_exp"),
        (("Appendix-2", "II.E: Pensions"), 165.0, "revenue_exp"),
        (("Appendix-2", "I.A.9: Social Security and Welfare"), 88.0, "revenue_exp"),
        (("Appendix-2", "I.A.2: Medical and Public Health"), 66.0, "revenue_exp"),
        (("Appendix-2", "I.A.1: Education, Sports, Art and Culture"), 165.0, "revenue_exp"),
    ]
    for (head_key, value, account_type) in plant:
        code = head_to_code[head_key]
        c.execute(
            """INSERT INTO budget_signals
               (state, fiscal_year, major_head_code, account_type, signal,
                estimate_type, value, unit, data_confidence, source_id, ingested_at)
               VALUES ('KA', '2024-25', ?, ?, 'amount', 'BE', ?, 'INR_CRORE', 0.95,
                       'test.synthetic.2024-25.BE', '2026-05-14T00:00:00Z')""",
            (code, account_type, value),
        )
    yield c
    c.close()


def test_revenue_side_metrics(conn_with_synthetic_data):
    rows = compute_metrics(conn_with_synthetic_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}

    # Known values: own=700 / total=1000 = 70%
    assert by_id["own_tax_share_pct"].value == pytest.approx(70.0)
    # Central dependence = (200 + 50) / 1000 = 25%
    assert by_id["central_dependence_pct"].value == pytest.approx(25.0)
    # Non-tax own = 50 / 1000 = 5%
    assert by_id["non_tax_revenue_share_pct"].value == pytest.approx(5.0)


def test_expenditure_quality_metrics(conn_with_synthetic_data):
    rows = compute_metrics(conn_with_synthetic_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}

    # Social = 500 / 1100 ≈ 45.45%
    assert by_id["social_services_share_pct"].value == pytest.approx(500/1100*100)
    # Economic = 200 / 1100 ≈ 18.18%
    assert by_id["economic_services_share_pct"].value == pytest.approx(200/1100*100)
    # Developmental = 700 / 1100 ≈ 63.64%
    assert by_id["developmental_share_pct"].value == pytest.approx(700/1100*100)
    # General = 400 / 1100 ≈ 36.36%
    assert by_id["general_services_share_pct"].value == pytest.approx(400/1100*100)


def test_fiscal_health_metrics(conn_with_synthetic_data):
    rows = compute_metrics(conn_with_synthetic_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}

    # Revenue balance = (1000 - 1100) / 1000 * 100 = -10% (revenue deficit)
    assert by_id["revenue_balance_pct"].value == pytest.approx(-10.0)
    # Interest = 110 / 1100 = 10%
    assert by_id["interest_burden_pct"].value == pytest.approx(10.0)
    # Pensions = 165 / 1100 = 15%
    assert by_id["pension_burden_pct"].value == pytest.approx(15.0)
    # Committed = (110 + 165) / 1100 = 25%
    assert by_id["committed_expenditure_pct"].value == pytest.approx(25.0)


def test_welfare_metrics(conn_with_synthetic_data):
    rows = compute_metrics(conn_with_synthetic_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    assert by_id["welfare_share_pct"].value == pytest.approx(88/1100*100)
    assert by_id["health_share_pct"].value == pytest.approx(66/1100*100)
    assert by_id["education_share_pct"].value == pytest.approx(165/1100*100)


def test_missing_head_yields_null(conn_with_synthetic_data):
    """If the source data lacks a required head, the metric is None, not zero."""
    # Add a different state with only partial data
    c = conn_with_synthetic_data
    head_to_code = build_head_to_code()
    code = head_to_code[("Appendix-1", "Total: TOTAL REVENUE (I+II)")]
    c.execute(
        """INSERT INTO budget_signals
           (state, fiscal_year, major_head_code, account_type, signal,
            estimate_type, value, unit, data_confidence, source_id, ingested_at)
           VALUES ('TN', '2024-25', ?, 'revenue_receipt', 'amount', 'BE', 500, 'INR_CRORE',
                   0.95, 'test.synthetic.2024-25.BE', '2026-05-14T00:00:00Z')""",
        (code,),
    )

    rows = compute_metrics(c, ["TN"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}

    # TN has total revenue but no own-tax breakdown -> metric is None
    assert by_id["own_tax_share_pct"].value is None
    # But the row still exists in the output (for completeness)
    assert by_id["own_tax_share_pct"].state == "TN"


def test_all_metrics_have_distinct_ids():
    ids = [m.id for m in METRICS]
    assert len(ids) == len(set(ids)), "Duplicate metric IDs"


def test_all_metric_families_known():
    valid = {"revenue_side", "expenditure_quality", "fiscal_health", "welfare",
             "tax_mix", "social_composition", "borrowing_mix", "subsidy_proxy",
             "revenue_load", "capex_split", "macro"}
    for m in METRICS:
        assert m.family in valid, f"{m.id} has unknown family {m.family}"


# --- Macro / per-capita metric tests (use budget_metadata) ---

def test_macro_metrics_with_planted_gsdp_and_population():
    """tax_to_gsdp, capex_to_gsdp, per-capita all use budget_metadata."""
    import sqlite3
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)

    head_to_code = build_head_to_code()
    plant_signals = [
        # own_tax = 1000 crore
        (("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)"), 1000.0, "revenue_receipt"),
        # total_revenue = 1500 crore
        (("Appendix-1", "Total: TOTAL REVENUE (I+II)"), 1500.0, "revenue_receipt"),
        # rev_exp = 1800 crore
        (("Appendix-2", "Total: TOTAL EXPENDITURE (I+II+III)"), 1800.0, "revenue_exp"),
        # interest = 200 crore
        (("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"), 200.0, "revenue_exp"),
        # social services = 600 crore
        (("Appendix-2", "I.A: Social Services (1 to 12)"), 600.0, "revenue_exp"),
        # capex = 400 crore
        (("Appendix-4", "I: Total Capital Outlay (1 + 2)"), 400.0, "capital_exp"),
    ]
    for (head_key, value, account_type) in plant_signals:
        code = head_to_code[head_key]
        c.execute(
            """INSERT INTO budget_signals
               (state, fiscal_year, major_head_code, account_type, signal,
                estimate_type, value, unit, data_confidence, source_id, ingested_at)
               VALUES ('KA', '2024-25', ?, ?, 'amount', 'BE', ?, 'INR_CRORE', 0.95,
                       'test.x', '2026-05-14T00:00:00Z')""",
            (code, account_type, value),
        )
    # Plant GSDP = 50000 crore, population = 10,000,000 (10 million)
    c.execute(
        """INSERT INTO budget_metadata (state, fiscal_year, metric, value, unit,
           source_id, ingested_at) VALUES ('KA', '2024-25', 'gsdp_inr_crore',
           50000, 'INR_CRORE', 'test.x', '2026-05-14T00:00:00Z')"""
    )
    c.execute(
        """INSERT INTO budget_metadata (state, fiscal_year, metric, value, unit,
           source_id, ingested_at) VALUES ('KA', '2024-25', 'population_count',
           10000000, 'COUNT', 'test.x', '2026-05-14T00:00:00Z')"""
    )

    rows = compute_metrics(c, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}

    # GSDP ratios: own_tax 1000 / GSDP 50000 = 2%
    assert by_id["tax_to_gsdp_pct"].value == pytest.approx(2.0)
    assert by_id["revenue_exp_to_gsdp_pct"].value == pytest.approx(3.6)   # 1800/50000
    assert by_id["capex_to_gsdp_pct"].value == pytest.approx(0.8)          # 400/50000
    assert by_id["debt_service_to_gsdp_pct"].value == pytest.approx(0.4)   # 200/50000

    # Per-capita: 1500 cr × 1e7 INR/cr / 1e7 people = 1500 INR/capita
    assert by_id["revenue_per_capita_inr"].value == pytest.approx(1500.0)
    assert by_id["revenue_exp_per_capita_inr"].value == pytest.approx(1800.0)
    assert by_id["capex_per_capita_inr"].value == pytest.approx(400.0)
    assert by_id["social_services_per_capita_inr"].value == pytest.approx(600.0)


def test_macro_metric_returns_none_when_metadata_missing():
    """Without GSDP/population in budget_metadata, macro metrics are None."""
    import sqlite3
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    head_to_code = build_head_to_code()
    code = head_to_code[("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)")]
    c.execute(
        """INSERT INTO budget_signals
           (state, fiscal_year, major_head_code, account_type, signal,
            estimate_type, value, unit, data_confidence, source_id, ingested_at)
           VALUES ('KA', '2024-25', ?, 'revenue_receipt', 'amount', 'BE', 1000.0,
                   'INR_CRORE', 0.95, 'test.x', '2026-05-14T00:00:00Z')""",
        (code,),
    )
    rows = compute_metrics(c, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    assert by_id["tax_to_gsdp_pct"].value is None
    assert by_id["revenue_per_capita_inr"].value is None


# --- Trend infrastructure tests ---

def test_years_between_inclusive():
    assert _years_between("2018-19", "2018-19") == ["2018-19"]
    assert _years_between("2018-19", "2022-23") == [
        "2018-19", "2019-20", "2020-21", "2021-22", "2022-23",
    ]


def test_years_between_handles_century_rollover():
    assert "2099-00" in _years_between("2099-00", "2099-00")


def test_compute_cagr_simple_doubling():
    # 100 → 200 over 4 years → 2^(1/4) - 1 ≈ 18.92%
    assert _compute_cagr([100.0, 0, 0, 0, 200.0], 4) == pytest.approx(18.9207, abs=0.001)


def test_compute_cagr_no_growth():
    assert _compute_cagr([100.0, 100.0, 100.0], 2) == pytest.approx(0.0)


def test_compute_cagr_returns_none_for_zero_start():
    assert _compute_cagr([0.0, 100.0], 1) is None


def test_compute_cagr_returns_none_for_negative_value():
    assert _compute_cagr([100.0, -50.0], 1) is None


def test_compute_cagr_returns_none_for_too_few_points():
    assert _compute_cagr([100.0], 0) is None


def test_compute_trend_metrics_with_planted_series():
    """End-to-end trend calc against synthetic 5-year time series."""
    import sqlite3
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)

    head_to_code = build_head_to_code()
    own_tax_code = head_to_code[("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)")]

    # Plant own_tax doubling from 1000 to 2000 over 5 years (4 intervals)
    series = {"2018-19": 1000, "2019-20": 1189.2, "2020-21": 1414.2,
              "2021-22": 1681.8, "2022-23": 2000}
    for fy, v in series.items():
        c.execute(
            """INSERT INTO budget_signals
               (state, fiscal_year, major_head_code, account_type, signal,
                estimate_type, value, unit, data_confidence, source_id, ingested_at)
               VALUES ('KA', ?, ?, 'revenue_receipt', 'amount', 'ACT', ?,
                       'INR_CRORE', 0.95, 'test.x', '2026-05-14T00:00:00Z')""",
            (fy, own_tax_code, v),
        )

    only_own_tax = (TREND_METRICS[0],)  # own_tax_cagr
    rows = compute_trend_metrics(c, ["KA"], defs=only_own_tax)
    assert len(rows) == 1
    r = rows[0]
    assert r.metric_id == "own_tax_cagr"
    assert r.fiscal_year == "2018-19→2022-23"
    assert r.estimate_type == "ACT"
    # CAGR of doubling over 4 years ≈ 18.92%
    assert r.value == pytest.approx(18.92, abs=0.05)


def test_compute_trend_metrics_returns_none_when_year_missing():
    """If any year in the window is missing from store, value is None (not partial CAGR)."""
    import sqlite3
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)

    head_to_code = build_head_to_code()
    own_tax_code = head_to_code[("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)")]
    # Plant only 2 of the 5 years
    for fy, v in [("2018-19", 1000), ("2019-20", 1100)]:
        c.execute(
            """INSERT INTO budget_signals
               (state, fiscal_year, major_head_code, account_type, signal,
                estimate_type, value, unit, data_confidence, source_id, ingested_at)
               VALUES ('KA', ?, ?, 'revenue_receipt', 'amount', 'ACT', ?,
                       'INR_CRORE', 0.95, 'test.x', '2026-05-14T00:00:00Z')""",
            (fy, own_tax_code, v),
        )
    rows = compute_trend_metrics(c, ["KA"], defs=(TREND_METRICS[0],))
    assert rows[0].value is None


@pytest.fixture
def conn_with_sectoral_data():
    """In-memory store with synthetic data for the sectoral metrics."""
    import sqlite3
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)

    head_to_code = build_head_to_code()
    plant = [
        # Tax mix: own tax = 1000, SGST=400, Excise=200, Stamps=130, Sales=180, Vehicles=70
        (("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)"), 1000.0, "revenue_receipt"),
        (("Appendix-1", "I.A.3.vii: State Goods and Services Tax"), 400.0, "revenue_receipt"),
        (("Appendix-1", "I.A.3.ii: State Excise"), 200.0, "revenue_receipt"),
        (("Appendix-1", "I.A.2.ii: Stamps and Registration Fees"), 130.0, "revenue_receipt"),
        (("Appendix-1", "I.A.3.i: Sales Tax (a to e)"), 180.0, "revenue_receipt"),
        (("Appendix-1", "I.A.3.iii: Taxes on Vehicles"), 70.0, "revenue_receipt"),
        # Social composition: social services = 500; education=200, health=100, sc/st=50, social_sec=80, housing=30
        (("Appendix-2", "I.A: Social Services (1 to 12)"), 500.0, "revenue_exp"),
        (("Appendix-2", "I.A.1: Education, Sports, Art and Culture"), 200.0, "revenue_exp"),
        (("Appendix-2", "I.A.2: Medical and Public Health"), 100.0, "revenue_exp"),
        (("Appendix-2", "I.A.7: Welfare of Scheduled Castes, Scheduled Tribes  and Other Backward Classes"), 50.0, "revenue_exp"),
        (("Appendix-2", "I.A.9: Social Security and Welfare"), 80.0, "revenue_exp"),
        (("Appendix-2", "I.A.5: Housing"), 30.0, "revenue_exp"),
        # Total rev exp for net subsidy denominator
        (("Appendix-2", "Total: TOTAL EXPENDITURE (I+II+III)"), 1100.0, "revenue_exp"),
        # Energy exp = 80, Power receipts = 30 → net subsidy = 50 → 50/1100 ≈ 4.55%
        (("Appendix-2", "I.B.5: Energy"), 80.0, "revenue_exp"),
        (("Appendix-1", "II.C.6.x: Power"), 30.0, "revenue_receipt"),
        # Borrowing: total cap rec = 2000; market=800, NSSF=300, centre=400, WMA=100, internal=1500
        (("Appendix-3", "total: TOTAL CAPITAL RECEIPTS (I to XII)"), 2000.0, "capital_receipt"),
        (("Appendix-3", "I.1: Market Loans"), 800.0, "capital_receipt"),
        (("Appendix-3", "I.7: Special Securities issued to NSSF"), 300.0, "capital_receipt"),
        (("Appendix-3", "II: Loans and Advances from the Centre (1 to 8)"), 400.0, "capital_receipt"),
        (("Appendix-3", "I.6: WMA from RBI"), 100.0, "capital_receipt"),
        (("Appendix-3", "I: Internal Debt (1 to 8)"), 1500.0, "capital_receipt"),
    ]
    for (head_key, value, account_type) in plant:
        code = head_to_code[head_key]
        c.execute(
            """INSERT INTO budget_signals
               (state, fiscal_year, major_head_code, account_type, signal,
                estimate_type, value, unit, data_confidence, source_id, ingested_at)
               VALUES ('KA', '2024-25', ?, ?, 'amount', 'BE', ?, 'INR_CRORE', 0.95,
                       'test.synthetic.2024-25.BE', '2026-05-14T00:00:00Z')""",
            (code, account_type, value),
        )
    yield c
    c.close()


def test_tax_mix_metrics(conn_with_sectoral_data):
    rows = compute_metrics(conn_with_sectoral_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    assert by_id["sgst_share_of_own_tax"].value == pytest.approx(40.0)         # 400/1000
    assert by_id["state_excise_share_of_own_tax"].value == pytest.approx(20.0)  # 200/1000
    assert by_id["sales_tax_share_of_own_tax"].value == pytest.approx(18.0)     # 180/1000
    assert by_id["stamps_share_of_own_tax"].value == pytest.approx(13.0)        # 130/1000
    assert by_id["vehicles_share_of_own_tax"].value == pytest.approx(7.0)       # 70/1000


def test_social_composition_metrics(conn_with_sectoral_data):
    rows = compute_metrics(conn_with_sectoral_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    assert by_id["education_in_social_pct"].value == pytest.approx(40.0)         # 200/500
    assert by_id["health_in_social_pct"].value == pytest.approx(20.0)            # 100/500
    assert by_id["sc_st_obc_welfare_in_social_pct"].value == pytest.approx(10.0)  # 50/500
    assert by_id["social_security_in_social_pct"].value == pytest.approx(16.0)    # 80/500
    assert by_id["housing_in_social_pct"].value == pytest.approx(6.0)             # 30/500


def test_borrowing_mix_metrics(conn_with_sectoral_data):
    rows = compute_metrics(conn_with_sectoral_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    assert by_id["market_loans_share"].value == pytest.approx(40.0)    # 800/2000
    assert by_id["nssf_share"].value == pytest.approx(15.0)            # 300/2000
    assert by_id["centre_loans_share"].value == pytest.approx(20.0)    # 400/2000
    assert by_id["wma_rbi_share"].value == pytest.approx(5.0)          # 100/2000
    assert by_id["internal_debt_share"].value == pytest.approx(75.0)   # 1500/2000


def test_net_power_subsidy_metric(conn_with_sectoral_data):
    rows = compute_metrics(conn_with_sectoral_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    # (energy 80 - power receipts 30) / total_rev_exp 1100 * 100 ≈ 4.545%
    assert by_id["net_power_subsidy_pct"].value == pytest.approx(50/1100*100)


def test_revenue_load_metrics(conn_with_sectoral_data):
    """Cross-family ratios use own tax revenue (1000) as denominator."""
    rows = compute_metrics(conn_with_sectoral_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    # Need to plant interest + pensions in conn_with_sectoral_data
    # The fixture doesn't include those — so these would be None. Add the
    # required heads via this test's setup.
    head_to_code = build_head_to_code()
    plant = [
        (("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"), 250.0, "revenue_exp"),
        (("Appendix-2", "II.E: Pensions"), 150.0, "revenue_exp"),
        (("Appendix-1", "I.B: Share in Central Taxes (i to ix)"), 200.0, "revenue_receipt"),
        (("Appendix-1", "II.D: Grants from the Centre (1 to 7)"), 100.0, "revenue_receipt"),
        (("Appendix-4", "I: Total Capital Outlay (1 + 2)"), 800.0, "capital_exp"),
    ]
    for (head_key, value, account_type) in plant:
        code = head_to_code[head_key]
        conn_with_sectoral_data.execute(
            """INSERT INTO budget_signals
               (state, fiscal_year, major_head_code, account_type, signal,
                estimate_type, value, unit, data_confidence, source_id, ingested_at)
               VALUES ('KA', '2024-25', ?, ?, 'amount', 'BE', ?, 'INR_CRORE', 0.95,
                       'test.synthetic.2024-25.BE', '2026-05-14T00:00:00Z')""",
            (code, account_type, value),
        )

    rows = compute_metrics(conn_with_sectoral_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    assert by_id["interest_per_own_tax"].value == pytest.approx(25.0)         # 250/1000
    assert by_id["committed_per_own_tax"].value == pytest.approx(40.0)        # (250+150)/1000
    assert by_id["central_dep_per_own_tax"].value == pytest.approx(30.0)      # (200+100)/1000
    assert by_id["capex_per_own_tax"].value == pytest.approx(80.0)            # 800/1000
    # net power subsidy already planted: energy 80 - power 30 = 50 → 50/1000 = 5%
    assert by_id["net_subsidy_per_own_tax"].value == pytest.approx(5.0)


def test_capex_split_metrics(conn_with_sectoral_data):
    """Capex sectoral metrics use Total Capital Outlay as denominator."""
    head_to_code = build_head_to_code()
    plant = [
        (("Appendix-4", "I: Total Capital Outlay (1 + 2)"), 2000.0, "capital_exp"),
        (("Appendix-4", "I.1.a.1: Education, Sports, Art and Culture"), 200.0, "capital_exp"),
        (("Appendix-4", "I.1.a.2: Medical and Public Health"), 100.0, "capital_exp"),
        (("Appendix-4", "I.1.a.4: Water Supply and Sanitation"), 50.0, "capital_exp"),
        (("Appendix-4", "I.1.b.4: Irrigation and Flood Control"), 400.0, "capital_exp"),
        (("Appendix-4", "I.1.b.5: Energy"), 300.0, "capital_exp"),
        (("Appendix-4", "I.1.b.7.i: Roads and Bridges"), 600.0, "capital_exp"),
    ]
    for (head_key, value, account_type) in plant:
        code = head_to_code[head_key]
        conn_with_sectoral_data.execute(
            """INSERT INTO budget_signals
               (state, fiscal_year, major_head_code, account_type, signal,
                estimate_type, value, unit, data_confidence, source_id, ingested_at)
               VALUES ('KA', '2024-25', ?, ?, 'amount', 'BE', ?, 'INR_CRORE', 0.95,
                       'test.synthetic.2024-25.BE', '2026-05-14T00:00:00Z')""",
            (code, account_type, value),
        )
    rows = compute_metrics(conn_with_sectoral_data, ["KA"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    assert by_id["capex_education_share"].value == pytest.approx(10.0)   # 200/2000
    assert by_id["capex_health_share"].value == pytest.approx(5.0)        # 100/2000
    assert by_id["capex_water_share"].value == pytest.approx(2.5)         # 50/2000
    assert by_id["capex_irrigation_share"].value == pytest.approx(20.0)   # 400/2000
    assert by_id["capex_energy_share"].value == pytest.approx(15.0)       # 300/2000
    assert by_id["capex_roads_share"].value == pytest.approx(30.0)        # 600/2000


def test_net_subsidy_can_be_negative(conn_with_sectoral_data):
    """Net subsidy goes negative when receipts exceed expenditure (rare but valid)."""
    c = conn_with_sectoral_data
    head_to_code = build_head_to_code()
    # Add TN with energy=10, power_receipts=50 → net = -40 → negative subsidy
    plant = [
        (("Appendix-2", "I.B.5: Energy"), 10.0, "revenue_exp"),
        (("Appendix-1", "II.C.6.x: Power"), 50.0, "revenue_receipt"),
        (("Appendix-2", "Total: TOTAL EXPENDITURE (I+II+III)"), 1000.0, "revenue_exp"),
    ]
    for (head_key, value, account_type) in plant:
        code = head_to_code[head_key]
        c.execute(
            """INSERT INTO budget_signals
               (state, fiscal_year, major_head_code, account_type, signal,
                estimate_type, value, unit, data_confidence, source_id, ingested_at)
               VALUES ('TN', '2024-25', ?, ?, 'amount', 'BE', ?, 'INR_CRORE', 0.95,
                       'test.synthetic.2024-25.BE', '2026-05-14T00:00:00Z')""",
            (code, account_type, value),
        )
    rows = compute_metrics(c, ["TN"], "2024-25", "BE")
    by_id = {r.metric_id: r for r in rows}
    assert by_id["net_power_subsidy_pct"].value == pytest.approx(-4.0)  # -40/1000*100
