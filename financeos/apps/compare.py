"""Peer comparison: rank states per metric, build BudgetFinding objects.

Implements the BudgetFinding shape from spec/CONFORMANCE.md §"Advisor
Conformance Gate". Findings are the only input the LLM Advisor will be
permitted to consume in Stage E. Today they feed the deterministic report.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence

from financeos.apps.metrics import MetricRow


@dataclass
class BudgetFinding:
    """One finding per (state, metric). See spec/CONFORMANCE.md §A1."""
    state: str
    fiscal_year: str
    estimate_type: str
    metric_id: str
    family: str
    label: str
    value: float
    unit: str
    peer_median: Optional[float]
    peer_exemplar_state: Optional[str]
    peer_exemplar_value: Optional[float]
    rank_in_peers: Optional[int]
    n_peers: int
    flag: Optional[str]              # 'above_peers' | 'below_peers' | 'within_peers'
    higher_is_better: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _quartile_flag(value: float, peer_values: Sequence[float]) -> str:
    """Bucket a value relative to its peer distribution."""
    if not peer_values:
        return "within_peers"
    q1, q3 = statistics.quantiles(peer_values, n=4)[0], statistics.quantiles(peer_values, n=4)[2]
    if value >= q3:
        return "above_peers"
    if value <= q1:
        return "below_peers"
    return "within_peers"


def build_findings(metric_rows: List[MetricRow]) -> List[BudgetFinding]:
    """For each (metric_id, fiscal_year, estimate_type) group, rank states.

    Returns one BudgetFinding per (state, metric) where the metric value is
    not null. Null-valued rows are dropped (they have no peer comparison).
    """
    # Group by metric × year × estimate_type
    groups: Dict[tuple, List[MetricRow]] = {}
    for r in metric_rows:
        if r.value is None:
            continue
        key = (r.metric_id, r.fiscal_year, r.estimate_type)
        groups.setdefault(key, []).append(r)

    findings: List[BudgetFinding] = []
    for (metric_id, fy, et), rows in groups.items():
        values = [r.value for r in rows]  # type: ignore[misc]
        if len(values) < 2:
            # Not enough peers for ranking
            for r in rows:
                findings.append(BudgetFinding(
                    state=r.state, fiscal_year=fy, estimate_type=et,
                    metric_id=metric_id, family=r.family, label=r.label,
                    value=r.value, unit=r.unit,  # type: ignore[arg-type]
                    peer_median=None, peer_exemplar_state=None,
                    peer_exemplar_value=None, rank_in_peers=None,
                    n_peers=len(rows), flag=None,
                    higher_is_better=r.higher_is_better,
                ))
            continue

        peer_median = statistics.median(values)
        # Rank: 1 = best
        if rows[0].higher_is_better:
            sorted_rows = sorted(rows, key=lambda r: -r.value)  # type: ignore[arg-type, operator]
        else:
            sorted_rows = sorted(rows, key=lambda r: r.value)   # type: ignore[arg-type]
        rank_by_state = {r.state: i + 1 for i, r in enumerate(sorted_rows)}
        exemplar = sorted_rows[0]

        for r in rows:
            findings.append(BudgetFinding(
                state=r.state,
                fiscal_year=fy,
                estimate_type=et,
                metric_id=metric_id,
                family=r.family,
                label=r.label,
                value=r.value,  # type: ignore[arg-type]
                unit=r.unit,
                peer_median=peer_median,
                peer_exemplar_state=exemplar.state,
                peer_exemplar_value=exemplar.value,
                rank_in_peers=rank_by_state[r.state],
                n_peers=len(rows),
                flag=_quartile_flag(r.value, values) if r.higher_is_better
                     else _quartile_flag(-r.value, [-v for v in values]),  # type: ignore[arg-type]
                higher_is_better=r.higher_is_better,
            ))
    return findings
