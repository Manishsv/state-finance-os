"""Cross-source disagreement check.

For every (state, fiscal_year, major_head_code, account_type, signal,
estimate_type) cell that has values from multiple source_ids, compute the
spread and flag cases where sources disagree by more than a threshold.

Use cases:
- Ongoing data-quality monitoring (have any cells diverged since last run?)
- Validation of new drivers (do their numbers agree with established sources?)
- Trust calibration (which sources tend to agree, which often diverge?)

The platform is **not** opinionated about who is "right" when sources
disagree — it surfaces the disagreement and lets the analyst investigate.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass
class CellDisagreement:
    state: str
    fiscal_year: str
    major_head_code: str
    account_type: str
    signal: str
    estimate_type: str
    sources: List[str]              # ['rbi.estates.2025-26.BE', 'prs.brief.KA.2024-25.BE']
    values: List[float]             # parallel to sources
    spread_abs: float               # max - min
    spread_pct: Optional[float]     # (max - min) / |min| * 100; None if min is 0


def find_disagreements(
    conn: sqlite3.Connection,
    states: Sequence[str],
    fiscal_year: str,
    estimate_type: Optional[str] = None,
    threshold_pct: float = 1.0,
) -> List[CellDisagreement]:
    """Return cells where multiple sources disagree by > threshold_pct.

    threshold_pct is applied to abs(spread/min). Cells with only one source
    are not returned.
    """
    placeholders = ",".join("?" for _ in states)
    et_filter = "AND estimate_type = ?" if estimate_type else ""
    q = f"""
        SELECT state, fiscal_year, major_head_code, account_type, signal, estimate_type,
               GROUP_CONCAT(source_id, '||') AS sources,
               GROUP_CONCAT(value,    '||') AS values_str,
               COUNT(DISTINCT source_id) AS n_sources,
               MAX(value) - MIN(value) AS spread_abs,
               MIN(value) AS min_v
        FROM budget_signals
        WHERE state IN ({placeholders})
          AND fiscal_year = ?
          AND signal = 'amount'
          {et_filter}
        GROUP BY state, fiscal_year, major_head_code, account_type, signal, estimate_type
        HAVING n_sources > 1
        ORDER BY spread_abs DESC
    """
    params: List = list(states) + [fiscal_year]
    if estimate_type:
        params.append(estimate_type)

    out: List[CellDisagreement] = []
    for r in conn.execute(q, params):
        sources = r["sources"].split("||")
        values = [float(v) for v in r["values_str"].split("||")]
        min_v = float(r["min_v"])
        spread_abs = float(r["spread_abs"])
        spread_pct = (spread_abs / abs(min_v) * 100.0) if min_v != 0 else None

        if spread_pct is None or spread_pct >= threshold_pct:
            out.append(CellDisagreement(
                state=r["state"], fiscal_year=r["fiscal_year"],
                major_head_code=r["major_head_code"], account_type=r["account_type"],
                signal=r["signal"], estimate_type=r["estimate_type"],
                sources=sources, values=values,
                spread_abs=spread_abs, spread_pct=spread_pct,
            ))
    return out


def render_disagreement_report(
    disagreements: List[CellDisagreement],
    head_code_to_description: Optional[dict] = None,
) -> str:
    """Render disagreements as a markdown table for human review."""
    head_code_to_description = head_code_to_description or {}
    lines = []
    lines.append("# Cross-Source Disagreement Report")
    lines.append("")
    if not disagreements:
        lines.append("**No disagreements found.** Either only one source has data for the slice, "
                     "or all multi-source cells agree within the threshold.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"Found **{len(disagreements)}** cells where multiple sources have values "
                 f"that disagree beyond the threshold.")
    lines.append("")
    lines.append("| State | Year | Head | Account | ET | Sources & Values | Spread (₹ cr) | Spread % |")
    lines.append("|---|---|---|---|---|---|---:|---:|")
    for d in disagreements:
        head_desc = head_code_to_description.get(d.major_head_code, d.major_head_code)
        head_label = f"{d.major_head_code} ({head_desc[:40]})" if head_desc != d.major_head_code else d.major_head_code
        # Show source-value pairs with enough decimals to expose tiny spreads
        sv = "<br/>".join(
            f"{s.split('.')[0]}: ₹{v:,.4f}" if d.spread_abs < 1 else f"{s.split('.')[0]}: ₹{v:,.0f}"
            for s, v in zip(d.sources, d.values)
        )
        # Spread display: <1 cr → 4 decimals; <100 cr → 2 decimals; else integer
        if d.spread_abs < 1:
            spread_str = f"{d.spread_abs:.4f}"
        elif d.spread_abs < 100:
            spread_str = f"{d.spread_abs:.2f}"
        else:
            spread_str = f"{d.spread_abs:,.0f}"
        spread_pct_str = (
            "—" if d.spread_pct is None else
            f"{d.spread_pct:.4f}%" if d.spread_pct < 0.01 else
            f"{d.spread_pct:.2f}%"
        )
        # Tag obvious rounding artifacts
        rounding_tag = " _(rounding)_" if d.spread_abs < 1 else ""
        lines.append(
            f"| {d.state} | {d.fiscal_year} | {head_label} | {d.account_type} | "
            f"{d.estimate_type} | {sv} | {spread_str}{rounding_tag} | {spread_pct_str} |"
        )
    lines.append("")
    lines.append("_Spreads tagged `(rounding)` are sub-rupee-crore differences between sources "
                 "that publish to different precisions (RBI carries decimals; PRS publishes integers). "
                 "Set a higher --threshold-pct (e.g. 0.5) to exclude them._")
    lines.append("")
    return "\n".join(lines)
