"""Exercise every rule in spec/CONFORMANCE.md against synthetic batches."""
from __future__ import annotations

from financeos.os.conformance import BudgetSignalRow, check_batch

VALID_STATES = {"KA", "TN", "AP", "TG", "KL"}
VALID_MH = {"2210", "2202", "0040"}
DECLARED_SIGNALS = ["total_expenditure", "own_tax_revenue"]
DECLARED_ETS = ["BE"]


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
        data_confidence=0.9,
        source_id="rbi.state_finances.2024-25.BE",
    )
    base.update(overrides)
    return BudgetSignalRow(**base)


def _check(rows, **overrides):
    kwargs = dict(
        domain="test",
        declared_signal_names=DECLARED_SIGNALS,
        declared_estimate_types=DECLARED_ETS,
        valid_states=VALID_STATES,
        valid_major_heads=VALID_MH,
    )
    kwargs.update(overrides)
    return check_batch(rows, **kwargs)


# Rule 1
def test_rule1_passes_with_valid_confidence():
    result = _check([_row()])
    assert result.ok
    assert result.failures == []


def test_rule1_blocks_null_confidence():
    result = _check([_row(data_confidence=None)])
    assert not result.ok
    assert any("data_confidence" in f for f in result.failures)


def test_rule1_blocks_out_of_range_confidence():
    assert not _check([_row(data_confidence=1.5)]).ok
    assert not _check([_row(data_confidence=-0.1)]).ok


def test_rule1_blocks_nan_confidence():
    assert not _check([_row(data_confidence=float("nan"))]).ok


# Rule 2
def test_rule2_warns_on_missing_declared_signal():
    result = _check([_row(signal="total_expenditure")])
    assert result.ok
    assert any("own_tax_revenue" in w for w in result.warnings)


def test_rule2_no_warning_when_all_signals_present():
    rows = [
        _row(signal="total_expenditure"),
        _row(
            signal="own_tax_revenue",
            major_head_code="0040",
            account_type="revenue_receipt",
        ),
    ]
    result = _check(rows)
    assert result.ok
    assert not any("absent" in w for w in result.warnings)


# Rule 3
def test_rule3_blocks_unknown_major_head():
    result = _check([_row(major_head_code="9999")])
    assert not result.ok
    assert any("unknown major_head_code" in f for f in result.failures)


# Rule 4
def test_rule4_blocks_unknown_state():
    result = _check([_row(state="XX")])
    assert not result.ok
    assert any("unknown state code" in f for f in result.failures)


# Rule 5
def test_rule5_blocks_invalid_estimate_type():
    result = _check([_row(estimate_type="FOO")])
    assert not result.ok
    assert any("invalid estimate_type" in f for f in result.failures)


def test_rule5_blocks_undeclared_estimate_type():
    result = _check([_row(estimate_type="RE")])  # driver only declares BE
    assert not result.ok
    assert any("did not declare estimate_type" in f for f in result.failures)


# Rule 6
def test_rule6_blocks_invalid_account_type():
    result = _check([_row(account_type="weird")])
    assert not result.ok
    assert any("invalid account_type" in f for f in result.failures)


# Rule 7
def test_rule7_blocks_bad_fiscal_year_format():
    assert not _check([_row(fiscal_year="2024")]).ok
    assert not _check([_row(fiscal_year="24-25")]).ok


def test_rule7_blocks_inconsistent_fiscal_year():
    assert not _check([_row(fiscal_year="2024-26")]).ok


def test_rule7_handles_century_rollover():
    # 2099-00 must be valid (last two digits of 2100 are 00)
    assert _check([_row(fiscal_year="2099-00")]).ok


# Rule 8
def test_rule8_blocks_unknown_unit():
    assert not _check([_row(unit="USD")]).ok


def test_rule8_accepts_alternate_units():
    assert _check([_row(unit="INR_LAKH")]).ok
    assert _check([_row(unit="RATIO", value=0.15)]).ok


# Rule 9
def test_rule9_warns_on_high_null_fraction():
    rows = [_row(value=None) for _ in range(10)] + [_row()]
    result = _check(rows)
    assert result.ok
    assert any("null/NaN" in w for w in result.warnings)


def test_rule9_no_warning_below_threshold():
    rows = [_row(value=None)] + [_row() for _ in range(100)]
    result = _check(rows)
    assert result.ok
    assert not any("null/NaN" in w for w in result.warnings)


# Rule 10
def test_rule10_warns_on_out_of_range_value():
    result = _check(
        [_row(value=999999.0)],
        declared_value_ranges={"total_expenditure": (0.0, 1000.0)},
    )
    assert result.ok
    assert any("outside declared range" in w for w in result.warnings)


def test_rule10_ignores_signals_without_range():
    result = _check(
        [_row(value=999999.0)],
        declared_value_ranges={"some_other_signal": (0.0, 1.0)},
    )
    assert result.ok
    assert not any("outside declared range" in w for w in result.warnings)


def test_multiple_blocking_failures_aggregate():
    # Bad state AND bad major head AND bad account type
    result = _check([_row(state="XX", major_head_code="9999", account_type="weird")])
    assert not result.ok
    assert len(result.failures) >= 3
