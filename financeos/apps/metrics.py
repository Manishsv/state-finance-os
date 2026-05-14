"""Metric definitions and computation.

A metric is a named, derived quantity computed from raw budget signals
(e.g. "own tax revenue as % of total revenue"). Metrics are defined here
in terms of (appendix, head) tuples, NOT 9XXX codes — codes are resolved
to heads at compute time via the registry. This keeps metric definitions
stable across RBI publication revisions.

Each metric belongs to one of four families:
    revenue_side          — composition of revenue receipts
    expenditure_quality   — composition of revenue expenditure
    fiscal_health         — balance and committed expenditure
    welfare               — welfare and social-security spending

The computation is deterministic and rule-based. There is no LLM here.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from financeos.drivers.registries.loader import load_registries

# (appendix, rbi_head) tuple — used to look up the synthetic 9XXX code.
HeadKey = Tuple[str, str]


@dataclass(frozen=True)
class MetricDef:
    id: str
    family: str
    label: str
    description: str
    formula: str  # ratio_pct | sum_ratio_pct | diff_ratio_pct | net_ratio_pct
                  # | ratio_to_metadata_pct | sum_ratio_to_metadata_pct | per_capita_inr
    numerator: Optional[HeadKey] = None
    numerator_components: Tuple[HeadKey, ...] = ()
    denominator: HeadKey = ("Appendix-1", "Total: TOTAL REVENUE (I+II)")
    denominator_metadata: Optional[str] = None  # e.g. "gsdp_inr_crore" — overrides head denom
    higher_is_better: bool = True
    unit: str = "PCT"


# --- Anchor heads used as numerators or denominators repeatedly ---
TOTAL_REVENUE: HeadKey       = ("Appendix-1", "Total: TOTAL REVENUE (I+II)")
TOTAL_REV_EXP: HeadKey       = ("Appendix-2", "Total: TOTAL EXPENDITURE (I+II+III)")
OWN_TAX_REVENUE: HeadKey     = ("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)")
SOCIAL_SERVICES_EXP: HeadKey = ("Appendix-2", "I.A: Social Services (1 to 12)")
TOTAL_CAPITAL_RECEIPTS: HeadKey = ("Appendix-3", "total: TOTAL CAPITAL RECEIPTS (I to XII)")
TOTAL_CAPITAL_OUTLAY: HeadKey   = ("Appendix-4", "I: Total Capital Outlay (1 + 2)")

METRICS: Tuple[MetricDef, ...] = (
    MetricDef(
        id="own_tax_share_pct",
        family="revenue_side",
        label="Own Tax Revenue (% of Total Revenue)",
        description="Share of revenue raised from the state's own taxes — higher means more fiscal autonomy.",
        formula="ratio_pct",
        numerator=("Appendix-1", "I.A: State's Own Tax Revenue (1 to 3)"),
        higher_is_better=True,
    ),
    MetricDef(
        id="central_dependence_pct",
        family="revenue_side",
        label="Central Dependence (% of Total Revenue)",
        description="Share in central taxes + grants from centre, as % of total revenue. Lower means less dependence on the centre.",
        formula="sum_ratio_pct",
        numerator_components=(
            ("Appendix-1", "I.B: Share in Central Taxes (i to ix)"),
            ("Appendix-1", "II.D: Grants from the Centre (1 to 7)"),
        ),
        higher_is_better=False,
    ),
    MetricDef(
        id="non_tax_revenue_share_pct",
        family="revenue_side",
        label="Own Non-Tax Revenue (% of Total Revenue)",
        description="Revenue from PSU dividends, royalties, fees, etc.",
        formula="ratio_pct",
        numerator=("Appendix-1", "II.C: State's Own Non-Tax Revenue (1 to 6)"),
        higher_is_better=True,
    ),

    # --- Family 2: Expenditure quality (composition of revenue expenditure) ---
    MetricDef(
        id="social_services_share_pct",
        family="expenditure_quality",
        label="Social Services (% of Revenue Expenditure)",
        description="Education, health, water, housing, social security, etc.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A: Social Services (1 to 12)"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="economic_services_share_pct",
        family="expenditure_quality",
        label="Economic Services (% of Revenue Expenditure)",
        description="Agriculture, irrigation, energy, industry, transport.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.B: Economic Services (1 to 9)"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="developmental_share_pct",
        family="expenditure_quality",
        label="Developmental Expenditure (% of Revenue Expenditure)",
        description="Social + Economic Services together; the part of spend that builds capacity (vs general administration).",
        formula="ratio_pct",
        numerator=("Appendix-2", "I: DEVELOPMENTAL EXPENDITURE (A + B)"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="general_services_share_pct",
        family="expenditure_quality",
        label="General Services (% of Revenue Expenditure)",
        description="Administration, organs of state, fiscal services, pensions, interest, miscellaneous.",
        formula="ratio_pct",
        numerator=("Appendix-2", "II: NON-DEVELOPMENTAL EXPENDITURE (General Services) (A to F)"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=False,
    ),

    # --- Family 3: Fiscal health ---
    MetricDef(
        id="revenue_balance_pct",
        family="fiscal_health",
        label="Revenue Balance (% of Total Revenue)",
        description="(Revenue receipts − Revenue expenditure) ÷ Revenue receipts × 100. Positive = revenue surplus.",
        formula="diff_ratio_pct",
        numerator_components=(TOTAL_REVENUE, TOTAL_REV_EXP),
        higher_is_better=True,
    ),
    MetricDef(
        id="interest_burden_pct",
        family="fiscal_health",
        label="Interest Payments (% of Revenue Expenditure)",
        description="Cost of servicing existing debt. High interest burden crowds out development spending.",
        formula="ratio_pct",
        numerator=("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=False,
    ),
    MetricDef(
        id="pension_burden_pct",
        family="fiscal_health",
        label="Pensions (% of Revenue Expenditure)",
        description="Retirement benefits — a committed expenditure that grows with the retiree base.",
        formula="ratio_pct",
        numerator=("Appendix-2", "II.E: Pensions"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=False,
    ),
    MetricDef(
        id="committed_expenditure_pct",
        family="fiscal_health",
        label="Committed Expenditure (Interest + Pensions, % of Revenue Expenditure)",
        description="The non-discretionary share of revenue expenditure. Higher = less fiscal flexibility.",
        formula="sum_ratio_pct",
        numerator_components=(
            ("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"),
            ("Appendix-2", "II.E: Pensions"),
        ),
        denominator=TOTAL_REV_EXP,
        higher_is_better=False,
    ),

    # --- Family 4: Welfare ---
    MetricDef(
        id="welfare_share_pct",
        family="welfare",
        label="Welfare & Social Security (% of Revenue Expenditure)",
        description="Social security and welfare spending (Major head I.A.9 in RBI's classification).",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A.9: Social Security and Welfare"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="health_share_pct",
        family="welfare",
        label="Health (% of Revenue Expenditure)",
        description="Medical and Public Health revenue spending.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A.2: Medical and Public Health"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="education_share_pct",
        family="welfare",
        label="Education (% of Revenue Expenditure)",
        description="Education, Sports, Art and Culture revenue spending.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A.1: Education, Sports, Art and Culture"),
        denominator=TOTAL_REV_EXP,
        higher_is_better=True,
    ),

    # --- Family 5: Tax Mix (composition of own tax revenue) ---
    MetricDef(
        id="sgst_share_of_own_tax",
        family="tax_mix",
        label="SGST (% of Own Tax Revenue)",
        description="State Goods and Services Tax — the GST floor every state collects.",
        formula="ratio_pct",
        numerator=("Appendix-1", "I.A.3.vii: State Goods and Services Tax"),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=True,
    ),
    MetricDef(
        id="state_excise_share_of_own_tax",
        family="tax_mix",
        label="State Excise (% of Own Tax Revenue)",
        description="Alcohol-related taxes. High share = strong dependence on alcohol revenue.",
        formula="ratio_pct",
        numerator=("Appendix-1", "I.A.3.ii: State Excise"),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=True,
    ),
    MetricDef(
        id="sales_tax_share_of_own_tax",
        family="tax_mix",
        label="Sales Tax / Fuel VAT (% of Own Tax Revenue)",
        description="Residual sales tax post-GST — overwhelmingly fuel VAT (petrol/diesel are outside GST).",
        formula="ratio_pct",
        numerator=("Appendix-1", "I.A.3.i: Sales Tax (a to e)"),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=True,
    ),
    MetricDef(
        id="stamps_share_of_own_tax",
        family="tax_mix",
        label="Stamps & Registration (% of Own Tax Revenue)",
        description="Real-estate transaction taxes. Tracks property-market velocity.",
        formula="ratio_pct",
        numerator=("Appendix-1", "I.A.2.ii: Stamps and Registration Fees"),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=True,
    ),
    MetricDef(
        id="vehicles_share_of_own_tax",
        family="tax_mix",
        label="Vehicle Taxes (% of Own Tax Revenue)",
        description="Motor-vehicle registration and road taxes.",
        formula="ratio_pct",
        numerator=("Appendix-1", "I.A.3.iii: Taxes on Vehicles"),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=True,
    ),

    # --- Family 6: Social Services Composition (within social services exp) ---
    MetricDef(
        id="education_in_social_pct",
        family="social_composition",
        label="Education (% of Social Services Exp)",
        description="Within social-sector spending, share going to Education, Sports, Art and Culture.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A.1: Education, Sports, Art and Culture"),
        denominator=SOCIAL_SERVICES_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="health_in_social_pct",
        family="social_composition",
        label="Health (% of Social Services Exp)",
        description="Within social-sector spending, share on Medical and Public Health.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A.2: Medical and Public Health"),
        denominator=SOCIAL_SERVICES_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="sc_st_obc_welfare_in_social_pct",
        family="social_composition",
        label="SC/ST/OBC Welfare (% of Social Services Exp)",
        description="Targeted welfare for Scheduled Castes, Scheduled Tribes, and Other Backward Classes.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A.7: Welfare of Scheduled Castes, Scheduled Tribes  and Other Backward Classes"),
        denominator=SOCIAL_SERVICES_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="social_security_in_social_pct",
        family="social_composition",
        label="Social Security & Welfare (% of Social Services Exp)",
        description="Pensions for the elderly/widowed/disabled, food subsidies, cash transfers.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A.9: Social Security and Welfare"),
        denominator=SOCIAL_SERVICES_EXP,
        higher_is_better=True,
    ),
    MetricDef(
        id="housing_in_social_pct",
        family="social_composition",
        label="Housing (% of Social Services Exp)",
        description="Public housing programmes within social-sector spending.",
        formula="ratio_pct",
        numerator=("Appendix-2", "I.A.5: Housing"),
        denominator=SOCIAL_SERVICES_EXP,
        higher_is_better=True,
    ),

    # --- Family 7: Borrowing Source Mix (within total capital receipts) ---
    MetricDef(
        id="market_loans_share",
        family="borrowing_mix",
        label="Market Loans (% of Total Capital Receipts)",
        description="Bonds raised in the open market — the most market-disciplined borrowing source.",
        formula="ratio_pct",
        numerator=("Appendix-3", "I.1: Market Loans"),
        denominator=TOTAL_CAPITAL_RECEIPTS,
        higher_is_better=True,
    ),
    MetricDef(
        id="nssf_share",
        family="borrowing_mix",
        label="NSSF Special Securities (% of Total Capital Receipts)",
        description="Borrowing from National Small Savings Fund. Cheap historically but fixed-quota and fading.",
        formula="ratio_pct",
        numerator=("Appendix-3", "I.7: Special Securities issued to NSSF"),
        denominator=TOTAL_CAPITAL_RECEIPTS,
        higher_is_better=True,
    ),
    MetricDef(
        id="centre_loans_share",
        family="borrowing_mix",
        label="Loans from Centre (% of Total Capital Receipts)",
        description="Borrowing from the Union Government. High share = dependence on central allocation.",
        formula="ratio_pct",
        numerator=("Appendix-3", "II: Loans and Advances from the Centre (1 to 8)"),
        denominator=TOTAL_CAPITAL_RECEIPTS,
        higher_is_better=False,
    ),
    MetricDef(
        id="wma_rbi_share",
        family="borrowing_mix",
        label="Ways & Means Advances from RBI (% of Total Capital Receipts)",
        description="Short-term overdraft from RBI. Persistently large share = chronic liquidity stress.",
        formula="ratio_pct",
        numerator=("Appendix-3", "I.6: WMA from RBI"),
        denominator=TOTAL_CAPITAL_RECEIPTS,
        higher_is_better=False,
    ),
    MetricDef(
        id="internal_debt_share",
        family="borrowing_mix",
        label="Internal Debt Total (% of Total Capital Receipts)",
        description="All sources of internal borrowing combined (market + LIC + NABARD + NSSF + WMA + others).",
        formula="ratio_pct",
        numerator=("Appendix-3", "I: Internal Debt (1 to 8)"),
        denominator=TOTAL_CAPITAL_RECEIPTS,
        higher_is_better=True,
    ),

    # --- Family 8: Sector Burden ---
    MetricDef(
        id="net_power_subsidy_pct",
        family="subsidy_proxy",
        label="Net Power Subsidy (% of Revenue Expenditure)",
        description="(Energy revenue exp − Power-sector receipts) ÷ Total revenue exp × 100. "
                    "Captures the net fiscal cost of the power sector after netting receipts. "
                    "Higher = greater net subsidy burden.",
        formula="net_ratio_pct",
        numerator_components=(
            ("Appendix-2", "I.B.5: Energy"),
            ("Appendix-1", "II.C.6.x: Power"),
        ),
        denominator=TOTAL_REV_EXP,
        higher_is_better=False,
    ),

    # --- Family 9: Revenue Load (cross-family ratios vs Own Tax Revenue) ---
    # These ask "how heavy is X relative to the controllable revenue base?"
    MetricDef(
        id="interest_per_own_tax",
        family="revenue_load",
        label="Interest Payments (% of Own Tax Revenue)",
        description="Interest as % of own tax — how much of every controllable revenue rupee goes to debt service.",
        formula="ratio_pct",
        numerator=("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=False,
    ),
    MetricDef(
        id="committed_per_own_tax",
        family="revenue_load",
        label="Interest + Pensions (% of Own Tax Revenue)",
        description="Mandatory commitments relative to controllable revenue. Above 100% = own tax cannot cover even committed expenditure.",
        formula="sum_ratio_pct",
        numerator_components=(
            ("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"),
            ("Appendix-2", "II.E: Pensions"),
        ),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=False,
    ),
    MetricDef(
        id="central_dep_per_own_tax",
        family="revenue_load",
        label="Central Transfers (% of Own Tax Revenue)",
        description="Centre-sourced revenue relative to own-tax base. >100% = state raises less from own taxes than it gets from the Centre.",
        formula="sum_ratio_pct",
        numerator_components=(
            ("Appendix-1", "I.B: Share in Central Taxes (i to ix)"),
            ("Appendix-1", "II.D: Grants from the Centre (1 to 7)"),
        ),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=False,
    ),
    MetricDef(
        id="capex_per_own_tax",
        family="revenue_load",
        label="Capital Outlay (% of Own Tax Revenue)",
        description="Investment intensity vs controllable revenue. Higher = more long-term-asset-building per rupee of own tax.",
        formula="ratio_pct",
        numerator=TOTAL_CAPITAL_OUTLAY,
        denominator=OWN_TAX_REVENUE,
        higher_is_better=True,
    ),
    MetricDef(
        id="net_subsidy_per_own_tax",
        family="revenue_load",
        label="Net Power Subsidy (% of Own Tax Revenue)",
        description="Power-sector net cost relative to controllable revenue. Higher = more own-tax effort effectively absorbed by power subsidy.",
        formula="net_ratio_pct",
        numerator_components=(
            ("Appendix-2", "I.B.5: Energy"),
            ("Appendix-1", "II.C.6.x: Power"),
        ),
        denominator=OWN_TAX_REVENUE,
        higher_is_better=False,
    ),

    # --- Family 10: Capital Outlay Sectoral Split (% of Total Capital Outlay) ---
    MetricDef(
        id="capex_education_share",
        family="capex_split",
        label="Education (% of Total Capital Outlay)",
        description="Share of capital investment going to school/college infrastructure.",
        formula="ratio_pct",
        numerator=("Appendix-4", "I.1.a.1: Education, Sports, Art and Culture"),
        denominator=TOTAL_CAPITAL_OUTLAY,
        higher_is_better=True,
    ),
    MetricDef(
        id="capex_health_share",
        family="capex_split",
        label="Health (% of Total Capital Outlay)",
        description="Share of capital investment going to medical and public-health infrastructure.",
        formula="ratio_pct",
        numerator=("Appendix-4", "I.1.a.2: Medical and Public Health"),
        denominator=TOTAL_CAPITAL_OUTLAY,
        higher_is_better=True,
    ),
    MetricDef(
        id="capex_water_share",
        family="capex_split",
        label="Water Supply & Sanitation (% of Total Capital Outlay)",
        description="Share going to water/sanitation infrastructure.",
        formula="ratio_pct",
        numerator=("Appendix-4", "I.1.a.4: Water Supply and Sanitation"),
        denominator=TOTAL_CAPITAL_OUTLAY,
        higher_is_better=True,
    ),
    MetricDef(
        id="capex_irrigation_share",
        family="capex_split",
        label="Irrigation & Flood Control (% of Total Capital Outlay)",
        description="Share going to irrigation, dams, and flood control.",
        formula="ratio_pct",
        numerator=("Appendix-4", "I.1.b.4: Irrigation and Flood Control"),
        denominator=TOTAL_CAPITAL_OUTLAY,
        higher_is_better=True,
    ),
    MetricDef(
        id="capex_energy_share",
        family="capex_split",
        label="Energy (% of Total Capital Outlay)",
        description="Share going to power generation, transmission, distribution.",
        formula="ratio_pct",
        numerator=("Appendix-4", "I.1.b.5: Energy"),
        denominator=TOTAL_CAPITAL_OUTLAY,
        higher_is_better=True,
    ),
    MetricDef(
        id="capex_roads_share",
        family="capex_split",
        label="Roads & Bridges (% of Total Capital Outlay)",
        description="Share going specifically to roads and bridges (excludes broader transport).",
        formula="ratio_pct",
        numerator=("Appendix-4", "I.1.b.7.i: Roads and Bridges"),
        denominator=TOTAL_CAPITAL_OUTLAY,
        higher_is_better=True,
    ),

    # --- Family 11: Macro & Per-Capita (denominators from budget_metadata) ---
    # GSDP and population are not budget signals — they sit in budget_metadata,
    # populated by the rbi_handbook driver (Census 2011 + projections for pop).
    MetricDef(
        id="tax_to_gsdp_pct",
        family="macro",
        label="Own Tax Revenue (% of GSDP)",
        description="The canonical fiscal-effort metric. Higher = state extracts more tax revenue from its economy. South-Indian states historically run 6-8%; national average ~6%.",
        formula="ratio_to_metadata_pct",
        numerator=OWN_TAX_REVENUE,
        denominator_metadata="gsdp_inr_crore",
        higher_is_better=True,
    ),
    MetricDef(
        id="revenue_exp_to_gsdp_pct",
        family="macro",
        label="Revenue Expenditure (% of GSDP)",
        description="Fiscal footprint: how large the state government's recurrent spending is relative to the economy.",
        formula="ratio_to_metadata_pct",
        numerator=TOTAL_REV_EXP,
        denominator_metadata="gsdp_inr_crore",
        higher_is_better=True,
    ),
    MetricDef(
        id="capex_to_gsdp_pct",
        family="macro",
        label="Capital Outlay (% of GSDP)",
        description="Investment intensity normalised by economy size. The Finance-Commission target floor is typically ~3-4% for states.",
        formula="ratio_to_metadata_pct",
        numerator=TOTAL_CAPITAL_OUTLAY,
        denominator_metadata="gsdp_inr_crore",
        higher_is_better=True,
    ),
    MetricDef(
        id="debt_service_to_gsdp_pct",
        family="macro",
        label="Interest Payments (% of GSDP)",
        description="Debt-servicing burden on the economy. Above ~2% is a fiscal-stress flag.",
        formula="ratio_to_metadata_pct",
        numerator=("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"),
        denominator_metadata="gsdp_inr_crore",
        higher_is_better=False,
    ),
    MetricDef(
        id="revenue_per_capita_inr",
        family="macro",
        label="Total Revenue per Capita (₹)",
        description="State revenue per resident. Normalises for population size; comparable across small and large states.",
        formula="per_capita_inr",
        numerator=TOTAL_REVENUE,
        denominator_metadata="population_count",
        higher_is_better=True,
        unit="INR_PER_CAPITA",
    ),
    MetricDef(
        id="revenue_exp_per_capita_inr",
        family="macro",
        label="Revenue Expenditure per Capita (₹)",
        description="Per-resident recurrent state spending.",
        formula="per_capita_inr",
        numerator=TOTAL_REV_EXP,
        denominator_metadata="population_count",
        higher_is_better=True,
        unit="INR_PER_CAPITA",
    ),
    MetricDef(
        id="capex_per_capita_inr",
        family="macro",
        label="Capital Outlay per Capita (₹)",
        description="Per-resident capital investment by the state government.",
        formula="per_capita_inr",
        numerator=TOTAL_CAPITAL_OUTLAY,
        denominator_metadata="population_count",
        higher_is_better=True,
        unit="INR_PER_CAPITA",
    ),
    MetricDef(
        id="social_services_per_capita_inr",
        family="macro",
        label="Social Services Exp per Capita (₹)",
        description="Per-resident spending on health, education, welfare combined.",
        formula="per_capita_inr",
        numerator=SOCIAL_SERVICES_EXP,
        denominator_metadata="population_count",
        higher_is_better=True,
        unit="INR_PER_CAPITA",
    ),
)


# ---------------------------------------------------------------------------
# Trend metrics — multi-year compound growth rates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrendMetricDef:
    """A trend metric is a single head's CAGR over a fixed historical window.

    Independent of the requested --year flag because trends are about the
    past, not the present. ACT (actuals) is the appropriate estimate type.
    """
    id: str
    family: str        # always "trends" for now
    label: str
    description: str
    head: HeadKey
    start_fy: str      # e.g. "2018-19"
    end_fy: str        # e.g. "2022-23"
    estimate_type: str = "ACT"
    higher_is_better: bool = True
    unit: str = "PCT"


TREND_WINDOW_START = "2018-19"
TREND_WINDOW_END   = "2022-23"

TREND_METRICS: Tuple[TrendMetricDef, ...] = (
    TrendMetricDef(
        id="own_tax_cagr",
        family="trends",
        label="Own Tax Revenue (5-yr CAGR, ACT)",
        description="Compound annual growth rate of state's own tax revenue, FY19-FY23 actuals. "
                    "Measures revenue base expansion. Above ~10% = strong growth; below ~5% = stagnation in nominal terms.",
        head=OWN_TAX_REVENUE,
        start_fy=TREND_WINDOW_START,
        end_fy=TREND_WINDOW_END,
        higher_is_better=True,
    ),
    TrendMetricDef(
        id="revenue_exp_cagr",
        family="trends",
        label="Revenue Expenditure (5-yr CAGR, ACT)",
        description="Growth rate of revenue expenditure, FY19-FY23 actuals. "
                    "If consistently above own-tax CAGR, the gap is being funded by borrowing or central transfers.",
        head=TOTAL_REV_EXP,
        start_fy=TREND_WINDOW_START,
        end_fy=TREND_WINDOW_END,
        higher_is_better=False,
    ),
    TrendMetricDef(
        id="capex_cagr",
        family="trends",
        label="Capital Outlay (5-yr CAGR, ACT)",
        description="Growth rate of capital investment, FY19-FY23 actuals. Volatile by nature — single big projects can swing it.",
        head=TOTAL_CAPITAL_OUTLAY,
        start_fy=TREND_WINDOW_START,
        end_fy=TREND_WINDOW_END,
        higher_is_better=True,
    ),
    TrendMetricDef(
        id="interest_cagr",
        family="trends",
        label="Interest Payments (5-yr CAGR, ACT)",
        description="Growth rate of debt service. Reflects accumulated borrowing × interest rate effect.",
        head=("Appendix-2", "II.C: Interest Payments and Servicing of Debt (1 + 2)"),
        start_fy=TREND_WINDOW_START,
        end_fy=TREND_WINDOW_END,
        higher_is_better=False,
    ),
    TrendMetricDef(
        id="social_services_cagr",
        family="trends",
        label="Social Services Exp (5-yr CAGR, ACT)",
        description="Growth rate of social-sector spending — education + health + welfare combined.",
        head=SOCIAL_SERVICES_EXP,
        start_fy=TREND_WINDOW_START,
        end_fy=TREND_WINDOW_END,
        higher_is_better=True,
    ),
)


def _years_between(start_fy: str, end_fy: str) -> List[str]:
    """Inclusive list of fiscal-year strings between start and end."""
    s = int(start_fy[:4])
    e = int(end_fy[:4])
    return [f"{y}-{(y+1) % 100:02d}" for y in range(s, e + 1)]


def _compute_cagr(values: List[float], n_intervals: int) -> Optional[float]:
    """Compound annual growth rate as a percentage.

    Returns None if start or end value is non-positive (CAGR undefined for
    zero/negative bases — common when a state didn't levy a tax in the start
    year, or budget head was reorganised).
    """
    if n_intervals <= 0 or len(values) < 2:
        return None
    start, end = values[0], values[-1]
    if start <= 0 or end <= 0:
        return None
    return (((end / start) ** (1.0 / n_intervals)) - 1.0) * 100.0


def compute_trend_metrics(
    conn: sqlite3.Connection,
    states: Sequence[str],
    defs: Sequence[TrendMetricDef] = TREND_METRICS,
) -> List[MetricRow]:
    """Compute every trend metric for every state.

    Loads the time series from the store. Returns MetricRow objects with
    `fiscal_year` set to the trend period (e.g. '2018-19→2022-23'), so that
    `build_findings` groups them separately from point-in-time metrics.
    """
    head_to_code = build_head_to_code()
    rows: List[MetricRow] = []

    for state in states:
        for td in defs:
            period = f"{td.start_fy}→{td.end_fy}"
            code = head_to_code.get(td.head)

            def _row(value: Optional[float], num: Optional[float] = None) -> MetricRow:
                return MetricRow(
                    state=state, fiscal_year=period, estimate_type=td.estimate_type,
                    metric_id=td.id, family=td.family, value=value,
                    unit=td.unit, label=td.label,
                    higher_is_better=td.higher_is_better,
                    numerator_value=num, denominator_value=None,
                )

            if code is None:
                rows.append(_row(None))
                continue

            years = _years_between(td.start_fy, td.end_fy)
            placeholders = ",".join("?" for _ in years)
            cur = conn.execute(
                f"SELECT fiscal_year, value FROM budget_signals "
                f"WHERE state=? AND major_head_code=? AND estimate_type=? "
                f"AND signal='amount' AND fiscal_year IN ({placeholders})",
                (state, code, td.estimate_type, *years),
            )
            by_year = {r["fiscal_year"]: r["value"] for r in cur.fetchall()}
            series = [by_year.get(fy) for fy in years]
            if any(v is None for v in series):
                rows.append(_row(None))
                continue

            cagr = _compute_cagr(series, len(series) - 1)
            rows.append(_row(cagr, num=series[-1]))

    return rows


@dataclass
class MetricRow:
    state: str
    fiscal_year: str
    estimate_type: str
    metric_id: str
    family: str
    value: Optional[float]    # None if any required head is missing
    unit: str
    label: str
    higher_is_better: bool
    numerator_value: Optional[float] = None
    denominator_value: Optional[float] = None


def build_head_to_code() -> Dict[HeadKey, str]:
    """Read major_heads.json and return (appendix, rbi_head) -> code."""
    import json
    from pathlib import Path
    p = (Path(__file__).resolve().parent.parent
         / "drivers" / "registries" / "major_heads.json")
    data = json.loads(p.read_text())
    return {
        (e["rbi_appendix"], e["rbi_head"]): e["code"]
        for e in data["major_heads"]
        if e.get("rbi_appendix") and e.get("rbi_head")
    }


def load_signal_values(
    conn: sqlite3.Connection,
    states: Sequence[str],
    fiscal_year: str,
    estimate_type: str,
) -> Dict[Tuple[str, str], float]:
    """Return {(state, major_head_code): value} for the given slice."""
    placeholders = ",".join("?" for _ in states)
    q = f"""
        SELECT state, major_head_code, value
        FROM budget_signals
        WHERE state IN ({placeholders})
          AND fiscal_year = ?
          AND estimate_type = ?
          AND signal = 'amount'
    """
    out: Dict[Tuple[str, str], float] = {}
    for r in conn.execute(q, (*states, fiscal_year, estimate_type)):
        out[(r[0], r[1])] = r[2]
    return out


def load_metadata_values(
    conn: sqlite3.Connection,
    states: Sequence[str],
    fiscal_year: str,
) -> Dict[Tuple[str, str], float]:
    """Return {(state, metric): value} from budget_metadata for the given year.

    Used as denominator source for macro-ratio metrics (tax-to-GSDP, etc.)
    and per-capita metrics. Aggregated across source_ids by taking the most
    recent ingest per (state, metric).
    """
    placeholders = ",".join("?" for _ in states)
    q = f"""
        SELECT state, metric, value
        FROM budget_metadata
        WHERE state IN ({placeholders}) AND fiscal_year = ?
    """
    out: Dict[Tuple[str, str], float] = {}
    for r in conn.execute(q, (*states, fiscal_year)):
        out[(r[0], r[1])] = r[2]
    return out


def _head_value(
    signals: Dict[Tuple[str, str], float],
    state: str,
    head_to_code: Dict[HeadKey, str],
    head: HeadKey,
) -> Optional[float]:
    code = head_to_code.get(head)
    if code is None:
        return None
    return signals.get((state, code))


def _compute_one(
    md: MetricDef,
    state: str,
    signals: Dict[Tuple[str, str], float],
    head_to_code: Dict[HeadKey, str],
    metadata: Optional[Dict[Tuple[str, str], float]] = None,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Returns (metric_value, numerator_value, denominator_value)."""
    metadata = metadata or {}

    # Macro-ratio metrics: numerator from budget heads, denominator from
    # budget_metadata (e.g. GSDP). Distinguished by `denominator_metadata`.
    if md.formula in ("ratio_to_metadata_pct", "per_capita_inr",
                      "sum_ratio_to_metadata_pct"):
        meta_key = md.denominator_metadata
        if meta_key is None:
            return None, None, None
        denom = metadata.get((state, meta_key))
        if denom is None or denom == 0:
            return None, None, denom

        if md.formula == "ratio_to_metadata_pct":
            num = _head_value(signals, state, head_to_code, md.numerator) if md.numerator else None
            if num is None:
                return None, None, denom
            return num / denom * 100.0, num, denom

        if md.formula == "sum_ratio_to_metadata_pct":
            parts = [_head_value(signals, state, head_to_code, h)
                     for h in md.numerator_components]
            if any(p is None for p in parts):
                return None, None, denom
            num = sum(parts)  # type: ignore[arg-type]
            return num / denom * 100.0, num, denom

        if md.formula == "per_capita_inr":
            # Numerator from heads is in Crore; convert to INR (×10^7), divide by population.
            num = _head_value(signals, state, head_to_code, md.numerator) if md.numerator else None
            if num is None:
                return None, None, denom
            return (num * 1e7) / denom, num, denom

    # Head-only metrics (existing path)
    denom = _head_value(signals, state, head_to_code, md.denominator)
    if denom is None or denom == 0:
        return None, None, denom

    if md.formula == "ratio_pct":
        num = _head_value(signals, state, head_to_code, md.numerator) if md.numerator else None
        if num is None:
            return None, None, denom
        return num / denom * 100.0, num, denom

    if md.formula == "sum_ratio_pct":
        parts = [_head_value(signals, state, head_to_code, h) for h in md.numerator_components]
        if any(p is None for p in parts):
            return None, None, denom
        num = sum(parts)  # type: ignore[arg-type]
        return num / denom * 100.0, num, denom

    if md.formula == "diff_ratio_pct":
        # numerator_components = (a, b); metric = (a - b) / a * 100
        a_head, b_head = md.numerator_components
        a = _head_value(signals, state, head_to_code, a_head)
        b = _head_value(signals, state, head_to_code, b_head)
        if a is None or b is None or a == 0:
            return None, None, denom
        return (a - b) / a * 100.0, a - b, denom

    if md.formula == "net_ratio_pct":
        # numerator_components = (a, b); metric = (a - b) / denominator * 100
        a_head, b_head = md.numerator_components
        a = _head_value(signals, state, head_to_code, a_head)
        b = _head_value(signals, state, head_to_code, b_head)
        if a is None or b is None:
            return None, None, denom
        return (a - b) / denom * 100.0, a - b, denom

    raise ValueError(f"Unknown formula: {md.formula}")


def compute_metrics(
    conn: sqlite3.Connection,
    states: Sequence[str],
    fiscal_year: str,
    estimate_type: str,
) -> List[MetricRow]:
    """Compute every defined metric for every state in the slice."""
    head_to_code = build_head_to_code()
    signals = load_signal_values(conn, states, fiscal_year, estimate_type)
    metadata = load_metadata_values(conn, states, fiscal_year)

    rows: List[MetricRow] = []
    for state in states:
        for md in METRICS:
            value, num, denom = _compute_one(md, state, signals, head_to_code, metadata)
            rows.append(MetricRow(
                state=state,
                fiscal_year=fiscal_year,
                estimate_type=estimate_type,
                metric_id=md.id,
                family=md.family,
                value=value,
                unit=md.unit,
                label=md.label,
                higher_is_better=md.higher_is_better,
                numerator_value=num,
                denominator_value=denom,
            ))
    return rows
