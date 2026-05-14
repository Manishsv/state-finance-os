"""Render BudgetFinding lists as per-state markdown briefs and a comparison CSV.

Structured rendering is deterministic. The optional `narrative` argument
to `render_state_brief` carries LLM-generated prose that has already
passed the Advisor Conformance Gate (spec/CONFORMANCE.md §A2).
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import List, Optional, Sequence

from financeos.apps.compare import BudgetFinding

FAMILY_ORDER = (
    "macro",
    "revenue_side", "tax_mix",
    "expenditure_quality", "social_composition",
    "fiscal_health", "borrowing_mix",
    "welfare", "subsidy_proxy",
    "capex_split",
    "revenue_load",
    "trends",
)
FAMILY_LABEL = {
    "macro":                "Macro & Per-Capita (vs GSDP and population)",
    "revenue_side":         "Revenue Side",
    "tax_mix":              "Tax Mix",
    "expenditure_quality":  "Expenditure Quality",
    "social_composition":   "Social Services Composition",
    "fiscal_health":        "Fiscal Health",
    "borrowing_mix":        "Borrowing Source Mix",
    "welfare":              "Welfare & Human Development",
    "subsidy_proxy":        "Sector Burden",
    "capex_split":          "Capital Outlay Sectoral Split",
    "revenue_load":         "Revenue Load (vs Own Tax Revenue)",
    "trends":               "Multi-Year Trends (5-yr CAGR, 2018-19 → 2022-23 ACT)",
}
FLAG_LABEL = {
    "above_peers": "top quartile",
    "below_peers": "bottom quartile",
    "within_peers": "mid-range",
}


def _fmt_value(v: float, unit: str) -> str:
    if unit == "PCT":
        return f"{v:.1f}%"
    if unit == "INR_CRORE":
        return f"₹{v:,.0f} cr"
    if unit == "INR_PER_CAPITA":
        return f"₹{v:,.0f}/capita"
    return f"{v:.2f}"


def _flag_arrow(flag: str, higher_is_better: bool) -> str:
    if flag == "above_peers":
        return "▲" if higher_is_better else "▼"
    if flag == "below_peers":
        return "▼" if higher_is_better else "▲"
    return "•"


def render_state_brief(
    state: str,
    findings: Sequence[BudgetFinding],
    narrative: Optional[str] = None,
    narrative_attempts: Optional[int] = None,
) -> str:
    """Markdown brief for one state, grouped by metric family.

    If `narrative` is provided, it is appended as an "Analyst Brief" section.
    The narrative is assumed to have already passed the Advisor Conformance
    Gate (spec/CONFORMANCE.md §A2).
    """
    if not findings:
        return f"# {state}\n\n_No findings._\n"

    fy = findings[0].fiscal_year
    et = findings[0].estimate_type
    et_label = {"BE": "Budget Estimates", "RE": "Revised Estimates", "ACT": "Actuals"}.get(et, et)

    lines: List[str] = []
    lines.append(f"# {state} — Budget Brief")
    lines.append("")
    lines.append(f"**Fiscal Year:** {fy}  |  **Source:** RBI State Finances 2025-26 ({et_label})")
    lines.append("")
    lines.append(f"_Compared against {findings[0].n_peers - 1} peer states._")
    lines.append("")

    by_family = defaultdict(list)
    for f in findings:
        by_family[f.family].append(f)

    for family in FAMILY_ORDER:
        items = by_family.get(family, [])
        if not items:
            continue
        lines.append(f"## {FAMILY_LABEL[family]}")
        lines.append("")
        lines.append("| Metric | Value | Peer Median | Rank | Flag | Best in peers |")
        lines.append("|---|---:|---:|---:|---|---|")
        for f in items:
            arrow = _flag_arrow(f.flag, f.higher_is_better) if f.flag else " "
            flag_text = FLAG_LABEL.get(f.flag or "", "")
            value_str = _fmt_value(f.value, f.unit)
            median_str = _fmt_value(f.peer_median, f.unit) if f.peer_median is not None else "—"
            rank_str = f"{f.rank_in_peers}/{f.n_peers}" if f.rank_in_peers else "—"
            best = (f"{f.peer_exemplar_state} ({_fmt_value(f.peer_exemplar_value, f.unit)})"
                    if f.peer_exemplar_state else "—")
            flag_cell = f"{arrow} {flag_text}".strip()
            lines.append(f"| {f.label} | {value_str} | {median_str} | {rank_str} | {flag_cell} | {best} |")
        lines.append("")

    if narrative:
        lines.append("## Analyst Brief")
        lines.append("")
        lines.append(narrative)
        lines.append("")
        lines.append("> ⚠ **AI-generated hypothesis & policy-lever analysis.** Numeric claims "
                     "are gate-validated against the structured findings above (spec/CONFORMANCE.md §A2). "
                     "Causal hypotheses and named policy levers are derived from general public-finance "
                     "knowledge in the model, not from this state's institutional context. **Validate "
                     "with the Finance Department before acting.**")
        lines.append("")
        if narrative_attempts and narrative_attempts > 1:
            lines.append(f"_(LLM narrative regenerated {narrative_attempts - 1} time(s) "
                         f"to satisfy the Advisor Conformance Gate.)_")
            lines.append("")
    elif narrative_attempts:  # narrative requested but gate rejected it
        lines.append("## Analyst Brief")
        lines.append("")
        lines.append("_LLM narrative was discarded by the Advisor Conformance Gate_ "
                     "_(spec/CONFORMANCE.md §A2): the model introduced numeric values not_ "
                     "_present in the structured findings on every attempt._")
        lines.append("")

    return "\n".join(lines)


def render_comparison_csv(findings: Sequence[BudgetFinding], path: Path) -> None:
    """Write all findings to a CSV — one row per (state, metric)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "state", "fiscal_year", "estimate_type", "family", "metric_id", "label",
        "value", "unit", "peer_median", "rank_in_peers", "n_peers",
        "peer_exemplar_state", "peer_exemplar_value", "flag", "higher_is_better",
    ]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for f in findings:
            row = f.to_dict()
            w.writerow({k: row.get(k) for k in fieldnames})
