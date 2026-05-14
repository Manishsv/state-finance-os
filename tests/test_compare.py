"""Tests for peer ranking and BudgetFinding construction."""
from __future__ import annotations

from financeos.apps.compare import build_findings
from financeos.apps.metrics import MetricRow


def _row(state: str, value: float, *, metric_id="own_tax_share_pct",
         family="revenue_side", higher_is_better=True) -> MetricRow:
    return MetricRow(
        state=state, fiscal_year="2024-25", estimate_type="BE",
        metric_id=metric_id, family=family,
        value=value, unit="PCT", label="Test metric",
        higher_is_better=higher_is_better,
    )


def test_ranking_higher_is_better():
    rows = [_row("KA", 70.0), _row("TN", 80.0), _row("KL", 60.0),
            _row("AP", 65.0), _row("TG", 75.0)]
    findings = build_findings(rows)
    by_state = {f.state: f for f in findings}

    # TN has the highest value; rank 1 since higher_is_better
    assert by_state["TN"].rank_in_peers == 1
    assert by_state["TN"].peer_exemplar_state == "TN"
    assert by_state["KL"].rank_in_peers == 5  # lowest

    # Median of [60, 65, 70, 75, 80] = 70
    assert by_state["KA"].peer_median == 70.0


def test_ranking_lower_is_better():
    rows = [_row("KA", 30.0, metric_id="interest_burden_pct",
                 family="fiscal_health", higher_is_better=False),
            _row("TN", 10.0, metric_id="interest_burden_pct",
                 family="fiscal_health", higher_is_better=False),
            _row("KL", 50.0, metric_id="interest_burden_pct",
                 family="fiscal_health", higher_is_better=False),
            _row("AP", 20.0, metric_id="interest_burden_pct",
                 family="fiscal_health", higher_is_better=False),
            _row("TG", 25.0, metric_id="interest_burden_pct",
                 family="fiscal_health", higher_is_better=False)]
    findings = build_findings(rows)
    by_state = {f.state: f for f in findings}

    # TN has lowest interest burden -> rank 1 (best, since lower-is-better)
    assert by_state["TN"].rank_in_peers == 1
    assert by_state["TN"].peer_exemplar_state == "TN"
    assert by_state["KL"].rank_in_peers == 5


def test_quartile_flags_higher_is_better():
    rows = [_row(f"S{i}", float(v)) for i, v in enumerate([10, 20, 30, 40, 50, 60, 70, 80])]
    findings = build_findings(rows)
    by_state = {f.state: f for f in findings}
    # S7=80 should be top quartile (above_peers); S0=10 should be bottom quartile
    assert by_state["S7"].flag == "above_peers"
    assert by_state["S0"].flag == "below_peers"


def test_null_metric_rows_dropped():
    # A null-valued metric row should produce no finding
    rows = [_row("KA", 70.0), MetricRow(
        state="TN", fiscal_year="2024-25", estimate_type="BE",
        metric_id="own_tax_share_pct", family="revenue_side",
        value=None, unit="PCT", label="x", higher_is_better=True,
    )]
    findings = build_findings(rows)
    assert len(findings) == 1
    assert findings[0].state == "KA"
    # n_peers reflects only non-null rows
    assert findings[0].n_peers == 1


def test_finding_fields_present_for_advisor_consumption():
    """Sanity-check: every field required by spec/CONFORMANCE.md §A1 is set."""
    rows = [_row("KA", 70.0), _row("TN", 80.0), _row("KL", 60.0)]
    findings = build_findings(rows)
    f = findings[0]
    required = ("state", "fiscal_year", "metric_id", "value", "unit",
                "peer_median", "peer_exemplar_state", "peer_exemplar_value",
                "rank_in_peers", "flag")
    for field_name in required:
        assert hasattr(f, field_name), f"BudgetFinding missing {field_name}"
