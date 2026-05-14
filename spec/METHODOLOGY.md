# FinanceOS — Methodology

**Version:** 0.1
**Status:** Draft for expert review
**Audience:** Public-finance economists, fiscal-policy analysts, Finance-Commission /
PRS / NIPFP staff. Anyone willing to point out where the analysis is wrong.

This document is written to be critiqued. It privileges precision over polish and
flags weaknesses explicitly. Where a choice is contestable, the alternative is
named and the reason for the chosen path is stated. Reviewer pushback on any
section is welcome and useful.

---

## 1. Purpose and Scope

FinanceOS is a platform for ingesting, normalising, comparing, and reasoning over
Indian state government budget data. The v0 focus is the South-5 peer group
(Andhra Pradesh, Karnataka, Kerala, Tamil Nadu, Telangana) for the most recent
published year (RE 2024-25), with five years of historical actuals (2018-19 →
2022-23) loaded for trend metrics.

**Explicitly in scope:**
- Mechanical extraction of budget line items from authoritative RBI publications
- Normalisation to a canonical hierarchical cell with full provenance
- Peer comparison via quartile rankings and exemplar identification
- Multi-year trend metrics (CAGR over a 5-year actuals window)
- Per-capita and GSDP-normalised macro metrics
- Rule-based "findings" with structured numerator / denominator traceability
- LLM-generated narrative interpretation, post-validated against the structured
  findings to prevent number fabrication

**Explicitly out of scope (for v0):**
- Outcome overlay (IMR, NIRF rankings, ASER reading levels, NFHS indicators)
- Sub-Major / Minor / Detailed Head granularity
- Centrally Sponsored Scheme decomposition
- District-level fiscal data
- GSDP-deflator-adjusted real growth
- Inter-state migration of population (we project from Census 2011 baseline only)
- Tax buoyancy at sub-tax granularity
- Audit findings (CAG reports)
- Off-budget liabilities (state PSU debt, contingent liabilities)
- Political-economy context (election cycles, party platforms)

The platform is **not a substitute for departmental analysis**; it is a
plumbing-and-comparison layer. Every LLM-generated brief carries a disclaimer
to that effect.

---

## 2. Data Sources and Authority

| Source | Publisher | Format | Lag | Used for |
|---|---|---|---|---|
| **State Finances: A Study of Budgets**, e-STATES database (2025-26 edition) | RBI Department of Economic and Policy Research | XLSX, ~14 MB, 397K rows | Annual; published Jan 2026 covers BE 2025-26 + RE 2024-25 + ACT 2022-23 | Budget signals (all 28 states + UTs, 1990-91 onwards) |
| **Handbook of Statistics on Indian States, Table 21** | RBI DEPR | XLSX, 16 KB | Annual; Dec 2025 ed. covers up to 2024-25 GSDP | GSDP at current prices |
| Census of India 2011 | Office of the Registrar General | Static | Decadal (2021 census delayed) | Population baseline |
| State-specific compound growth rates (hardcoded) | Derived from 2001-2011 decadal Census growth, adjusted for post-2011 fertility decline | Constants in driver | n/a | Population projection 2011 → 2025 |

**Why RBI as primary source:** RBI's State Finances publication is the only
serial that *normalises* line items across 28 state budgets into a single
classification. State budget documents themselves use idiosyncratic
classifications, demand-for-grants numbering, and presentation conventions that
make direct comparison hazardous. The trade-off is a 1-2 year lag for actuals.

**What we lose:** RBI's classification is its own — not the CGA Major Head
codes used by the Controller General of Accounts. See §5.

**Reviewer questions:**
- Should the platform also ingest CAG audit reports for ACT figures earlier
  than RBI publishes them?
- Is the e-STATES dataset's "All States/UT" composition stable enough to
  compare against the per-state aggregates in the same file? (RBI's own Note
  acknowledges UTs were excluded pre-2017-18.)

---

## 3. Architectural Choices

The kernel pattern is borrowed from a sister project (AirOS — air-quality
observability platform). Three primitives:

- **Driver** — one per (source × domain). Implements `fetch(states, years)`
  and writes to the store via a conformance gate. See [DRIVER_INTERFACE.md](DRIVER_INTERFACE.md).
- **Conformance gate** — 10 BLOCKING + WARNING rules enforced on every
  `write_signals` call. See [CONFORMANCE.md](CONFORMANCE.md).
- **SDK** — `discover` (registry inspection), `query` (read-only store access).

This structure means **adding a new state-budget data source is one driver**,
not a refactor. Outcome data (NFHS, ASER) would be additional drivers writing
to a parallel `outcomes_signals` table.

---

## 4. Canonical Unit of Analysis

A "cell" is the tuple:

```
(state, fiscal_year, major_head_code, account_type)
```

with the following constraints:

| Field | Type | Format | Source |
|---|---|---|---|
| `state` | string, 2 chars | ISO 3166-2:IN code (KA, TN, AP, TG, KL, …) | Registry |
| `fiscal_year` | string, 7 chars | `YYYY-YY` (Indian convention) | Driver-supplied |
| `major_head_code` | string, 4 chars (digits) | CGA Major Head code OR synthetic 9XXX (see §5) | Registry |
| `account_type` | enum | `revenue_receipt`, `capital_receipt`, `revenue_exp`, `capital_exp` | Driver-supplied |

Each cell carries one or more **signals** (typically just `amount` in INR Crore),
qualified by `estimate_type` ∈ `{BE, RE, ACT}` and `source_id` for full provenance.

**Why this granularity:**

- **State** is the natural unit for fiscal comparison given India's federal
  structure and the 28-state / UT classification.
- **Fiscal year** in `YYYY-YY` form is the Indian government convention and
  unambiguous about April-March boundaries.
- **Major Head** is the canonical accounting classification used by CGA and
  every State Finance Department's Annual Financial Statement. Going below
  Major Head (to Minor / Sub-Minor / Detailed Head) was deferred — see §5
  for the trade-off.
- **Account type** is split out because a single Major Head can appear under
  multiple account types (e.g. `2210: Medical and Public Health` has both
  revenue-expenditure and capital-outlay variants `4210`). Without this
  qualifier, sums across account types would silently double-count.

**Reviewer questions:**

- Is Major Head the right granularity, or should v1 immediately move to
  Minor Head (~3,000 codes)? The trade-off: cardinality vs. cross-state
  classification consistency at finer levels.
- Should `fiscal_year` include calendar-year metadata for cases where state
  budgets are revised mid-year (e.g. Andhra Pradesh post-bifurcation)?
- The current design treats Telangana pre-2014 as null. Should there be an
  explicit "successor state" relationship in the schema?

---

## 5. Classification Mapping: RBI → Synthetic Major Heads

The single most consequential design choice — and the most contestable.

**The problem:** RBI's e-STATES database uses RBI's own hierarchical
classification (`I.A.3.vii: State Goods and Services Tax`) — not CGA Major Head
codes (`0040: Sales Tax`). The classifications overlap conceptually but are
not byte-identical.

**The choice:**

1. We pre-allocated synthetic Major Head codes in the `9XXX` range
   (`9001`-`9357`) for every distinct `(appendix, RBI-head)` pair encountered
   in the e-STATES file.
2. Allocation is sequential, sorted alphabetically by `(appendix, head)` for
   determinism, and persisted to `financeos/drivers/registries/major_heads.json`.
3. Subsequent ingestions reuse existing assignments (idempotent — additions
   only).
4. The `9XXX` codes co-exist with hand-curated CGA codes (`0020`-`6004`) in
   the same registry, distinguished by a `section` attribute.

**What this preserves:**
- The cell schema's "4-digit code" invariant
- Cross-driver comparison if a future driver writes the actual CGA codes
  (the registry has both)
- Full RBI provenance — every synthetic code carries the original RBI head
  string and appendix in the registry

**What this hides:**
- The fact that RBI's classification is sometimes *more* aggregated than CGA's
  (e.g., RBI's "I.A.3.i: Sales Tax (a to e)" lumps multiple CGA Minor Heads).
- Year-on-year RBI re-classification — if RBI restructures a head between
  editions, the synthetic-code mapping must be updated; current bootstrap
  script appends but does not migrate.

**Reviewer questions:**

- **Is this a defensible mapping or should we hold our nose and use RBI's
  string codes (`I.A.3.vii`) directly as the major_head_code field, breaking
  the 4-digit invariant?** The current approach prioritises schema cleanliness
  but introduces an indirection that a CGA accountant would find unfamiliar.
- Should the `section` attribute be more granular (currently
  `receipt_revenue`, `expenditure_revenue`, `expenditure_capital`,
  `public_debt`, `rbi_synthetic`)?
- The Appendix → account_type mapping (Appendix-1 → revenue_receipt,
  Appendix-2 → revenue_exp, Appendix-3 → capital_receipt, Appendix-4 →
  capital_exp) is asserted by us, not stated by RBI. Is this correct? In
  particular, Appendix-3 contains both *borrowings* (which are receipts) and
  *Public Account* movements (which are technically neither receipt nor
  expenditure). Are we silently misclassifying any of these?

---

## 6. Metric Catalog

54 metrics across 11 families. All are deterministic, computed from
`budget_signals` and `budget_metadata` via four formula types:

| Formula | Definition |
|---|---|
| `ratio_pct` | `numerator_head / denominator_head × 100` |
| `sum_ratio_pct` | `sum(numerator_components) / denominator_head × 100` |
| `diff_ratio_pct` | `(a − b) / a × 100` where a is the first component |
| `net_ratio_pct` | `(a − b) / denominator × 100` (e.g. net subsidy = exp − receipts) |
| `ratio_to_metadata_pct` | `numerator_head / metadata_value × 100` (e.g. tax/GSDP) |
| `per_capita_inr` | `(numerator_head × 10⁷) / population_count` |
| `cagr_pct` | Compound annual growth rate of a single head's actuals over a fixed window |

The full catalog is in [financeos/apps/metrics.py](../financeos/apps/metrics.py).
Every metric is tagged with `higher_is_better: bool` so peer ranking knows
which direction is the "good" tail.

**Selected definitions for review:**

| Metric | Numerator | Denominator | Notes |
|---|---|---|---|
| `tax_to_gsdp_pct` | I.A: State's Own Tax Revenue | GSDP at current prices | Canonical fiscal-effort. GSDP is current-price; an inflation-adjusted variant would need a deflator. |
| `committed_per_own_tax` | II.C Interest + II.E Pensions | I.A Own Tax | "Above 100% means own tax can't even cover committed expenditure" — flagged in description |
| `net_power_subsidy_pct` | I.B.5 Energy (Appendix-2) − II.C.6.x Power (Appendix-1) | Total revenue exp | A *gross* power-spending share would also be useful — reviewer should flag if this net number is misleading |
| `revenue_balance_pct` | (Total Revenue − Total Revenue Exp) | Total Revenue | Positive = revenue surplus. Note: doesn't include capital account |
| `capex_to_gsdp_pct` | Total Capital Outlay (Appendix-4) | GSDP | Excludes loan repayments and debt discharge — those sit in Appendix-4 too but are not "outlay" |
| `own_tax_cagr` | Own Tax Revenue ACT, 2018-19 → 2022-23 | n/a (CAGR) | 4-interval geometric growth. See §7 |

**Reviewer questions:**

- **Is "Total Capital Outlay" (Appendix-4 head `I: Total Capital Outlay (1+2)`)
  the right capex measure?** It excludes loans-given and Public Account
  capital movements, which is conventional but the RBI document also reports a
  broader "Total Capital Disbursements" — should we surface both?
- **`committed_expenditure_pct`** counts Interest + Pensions only. Salary
  expenditure is the third leg of "committed" expenditure in most state
  finance literature. We don't have salary as a clean head in the RBI dataset
  — the closest is II.D Administrative Services, which conflates salary and
  non-salary admin spending. Is this omission acceptable, or is the metric
  misleadingly named?
- **`net_power_subsidy_pct`** uses Appendix-1 II.C.6.x: Power as receipts
  proxy. This is *non-tax revenue from the power sector* — likely
  PSU dividends/transfers, not consumer tariffs. Is this a defensible
  receipts proxy, or is the "subsidy" calculation wrong because the wrong
  receipts head is being used?

---

## 7. Trend Computation

Trends use a 5-year compound annual growth rate over actuals:

```
CAGR = ((value_end / value_start) ^ (1 / n_intervals)) − 1
n_intervals = 4    (2018-19, 2019-20, 2020-21, 2021-22, 2022-23 = 5 years, 4 gaps)
```

Window is **fixed**: start = 2018-19, end = 2022-23, estimate_type = ACT.

**Why this window:**

- Long enough for stable CAGR (3-year windows are dominated by COVID base
  effects).
- Short enough that pre-2017 GST regime doesn't dominate.
- End-point 2022-23 is the latest year for which RBI's e-STATES has
  consistent ACT data across all states.

**Known issues:**

- **2020-21 COVID anomaly is *included* in the window.** Both 2019-20 and
  2020-21 anchor the geometric mean, but 2020-21's contraction biases CAGR
  downward relative to the structural trend. Reviewer may prefer a
  COVID-excluded window (e.g., 2018-19 → 2019-20, then 2021-22 → 2022-23
  separately) but that complicates interpretation.
- **CAGR is undefined for non-positive start or end values** — handled by
  returning `None` (the metric is dropped from peer ranking for that state).
  This affects ~1 in 25 trend cells in the South-5 sample, mostly because
  one state had a missing year.
- **No real-vs-nominal distinction.** All values are nominal. A 10% nominal
  CAGR during 6% inflation is ~4% real. We do not deflate. v1 should add a
  GSDP-deflator-based real series.

**Reviewer questions:**

- Is fixed-window CAGR the right metric, or should we report year-over-year
  growth rates (5 numbers, not 1) and let the reader compute their own
  summary?
- Should the COVID year be excluded? If yes, by what rule?

---

## 8. Peer Comparison and Flagging

For each metric, given a peer set (typically South-5), we compute:

- **Median** of the metric across the peer set (using `statistics.median`)
- **Quartile flag** for each state: `above_peers` (≥ Q3), `below_peers` (≤ Q1),
  `within_peers` (otherwise)
- **Rank** within peers (1 = best, where "best" is `higher_is_better`-aware)
- **Peer exemplar** = the rank-1 state's value

**Known issues:**

- **N=5 is small.** With only 5 peers, Q1 and Q3 are interpolated from very
  thin distributions. The "top-quartile" / "bottom-quartile" labels are
  effectively just "best one" and "worst one" most of the time.
- **Median may equal the state's own value** when the peer set has only 5
  members and the state is the median. We label this `mid-range` even though
  it's literally tied with the median.
- **Ranking ties** are broken by Python sort stability — alphabetical state
  code. This is arbitrary and not communicated to readers. Should ties show
  as `=2/5` or similar?

**Reviewer questions:**

- Is N=5 too small for meaningful quartile flags? Should we use a different
  partition (top-2 / middle-1 / bottom-2)?
- Should peer rankings be weighted (e.g. by GSDP, by population) rather than
  treating all peers equally?

---

## 9. Per-Capita and GSDP Normalisation

GSDP is loaded directly from the RBI Handbook of Statistics on Indian States,
Table 21 (Gross State Domestic Product at Current Prices). One value per
(state, year), in ₹ Lakh, converted to ₹ Crore at write time.

**Population is the weakest link in the entire pipeline.**

- Census 2011 figures are used as a baseline.
- A state-specific compound annual growth rate (0.3% for KL → 1.0% for KA)
  is applied to project forward.
- Growth rates are derived from 2001-2011 decadal Census growth, adjusted
  downward by judgment for post-2011 fertility decline (no formal source).
- For Andhra Pradesh and Telangana, the post-bifurcation Census 2011 figures
  are used (reconstructed from district-level data).

**Estimated error:** ±5% by 2024-25, with KA likely overestimated and KL
likely underestimated. This propagates directly into per-capita metrics.

**Why this is acceptable for v0:** Per-capita figures are used for *peer
comparison*, where systematic errors of similar magnitude across states
partially cancel. Absolute per-capita figures should not be quoted out of
context.

**Reviewer questions:**

- Should we use NCO/UN/MoSPI projections instead of our own?
- Should per-capita metrics carry an error band rather than a point estimate?
- Population is annual; should we instead store as a separate signal with its
  own provenance and let the metric layer pick the closest year?

---

## 10. LLM Advisor Layer

The advisor receives **only structured findings** (one `BudgetFinding` object
per state-metric pair) plus a fixed system prompt. It produces a 3-paragraph
narrative that is then post-validated.

**System prompt** ([advise.py SYSTEM_PROMPT](../financeos/apps/advise.py)):
demands hypothesis + named lever + risk per flagged finding. Forbids vague
hedges ("could examine", "may benefit from"). Allows qualitative naming of
policy levers from the LLM's general knowledge (raising fuel VAT, shifting
power subsidies to DBT, etc.) but disallows fabricated numbers.

**Numeric guardrail** (Rule A2): every numeric token in the LLM output is
extracted via regex and matched against the union of:
- All `value`, `peer_median`, and `peer_exemplar_value` fields in the input
  findings (rounded to 1 and 2 decimals)
- All `rank_in_peers` and `n_peers` integers
- A small whitelist `{0, 1, 2, 3, 4, 5, 100}` (for percentages and ranks)
- Absolute values of negative finding values (to allow paraphrase of
  "−15.8% deficit" as "15.8% deficit")

**Tolerance:** ±0.1 (absorbs 1-decimal rounding).

**Pre-processing:** fiscal-year-shaped substrings (`\d{4}-\d{2,4}`) are
stripped before extraction so `2024-25` does not parse as the integers
`2024` and `−25`.

**Retry logic:** if any number in the output isn't in the allowed set, the
LLM is called again with a stricter prompt naming the offending values. After
2 failures, the narrative is discarded and the brief shows the structured
tables with a "guardrail rejected" notice in place of prose.

**Honest limitations of the guardrail:**

- It catches *obvious* fabrications (`75% by 2030`, `₹26,500 crore`) but
  cannot catch *misattribution* — the LLM citing a real number from the
  findings but applying it to the wrong metric (e.g., quoting the median
  CAGR as if it were a level percentage).
- The ±0.1 tolerance lets the LLM round-shift a number by up to 0.5
  percentage points. We've seen one observed case (AP brief: "education
  floor of 13.0%" matched by tolerance to an unrelated trend metric of
  12.9%). Tightening tolerance to 0.05 would catch this but might break
  legitimate rounding.
- It does not check causal claims, lever feasibility, or politico-economic
  judgments. Those rest on the LLM's training data and are flagged as such
  in the brief disclaimer.

**Output disclaimer (rendered in every narrative-bearing brief):**

> ⚠ AI-generated hypothesis & policy-lever analysis. Numeric claims are
> gate-validated. Causal hypotheses and named policy levers are derived
> from general public-finance knowledge in the model, not from this state's
> institutional context. Validate with the Finance Department before acting.

**Reviewer questions:**

- Is the prompt structure (strengths / weakness #1 / weakness #2) right?
  Should we instead force the LLM to address every flagged finding?
- The LLM names policy levers (fuel VAT rate hikes, DBT for power) from
  training data. Should we instead curate an explicit lever catalog
  per metric, and only allow the LLM to pick from it?
- Is "qualitative levers from training" an acceptable epistemic compromise,
  or should the brief contain *no* policy advice at all and only state the
  structural facts?

---

## 11. Provenance and Reproducibility

- Every signal carries a `source_id` (e.g. `rbi.estates.2025-26.RE`) traceable
  to a specific publication.
- Every raw download is cached under `data/raw/<source>/` with a
  `manifest.json` recording URL, SHA-256, byte count, and retrieval timestamp.
- Ingestion is idempotent (`INSERT OR REPLACE` on the cell+signal+source
  primary key) — re-running with the same source produces the same store.
- The synthetic-code registry is checked into git; new codes are appended
  on bootstrap, never reshuffled.
- All metric definitions are code (`metrics.py`); no hidden Excel formulas,
  no spreadsheet tabs to lose track of.

**Reviewer questions:**

- Should the registry's synthetic-code allocations be signed / versioned
  more rigorously (e.g., a content hash of the (appendix, head) → code map,
  asserted at load time)?
- Should each metric definition carry a SemVer-style version so that
  consumers of the comparison CSV can detect when a definition changed?

---

## 12. Validation: Spot Checks Against Published Sources

A sample of validation checks performed during development:

| Claim | FinanceOS value | Published source | Match |
|---|---:|---|---|
| KA 2024-25 BE Total Revenue | ₹2,63,178 cr | KA Budget at a Glance 2024-25 | ✓ within rounding |
| KA 2024-25 RE SGST | ₹80,116 cr | KA Receipt Budget 2025-26 | ✓ |
| KA 2024-25 GSDP (current prices) | ₹28,83,903 cr (= ₹28.84 lakh cr) | RBI Handbook Table 21 (source) | ✓ exact |
| TG 2024-25 RE Revenue Surplus | +2.9% of revenue | TG Medium-Term Fiscal Statement | ✓ direction and magnitude |
| KL Net Power Subsidy | 0.4% of rev exp | KSEB cost-recovery reports | ✓ qualitative match (KSEB known to be near-full recovery) |

**Validation gaps:**
- We have not done a comprehensive line-item validation against state
  Annual Financial Statements. A reviewer who can do this for one state
  would be the highest-value contribution.

---

## 13. Summary of Limitations

For an expert reader who skips the rest of this document:

1. **Population data is a weak link.** Census 2011 + simple growth rates.
   Per-capita metrics inherit ±5% uncertainty.
2. **Nominal-only.** No inflation-adjusted real series. CAGR comparisons
   are nominal.
3. **Major Head granularity.** Sub-Major / Minor / Detailed not yet ingested.
4. **No outcomes.** Cannot compute "expenditure efficiency" (allocation per
   unit of outcome) — which is what the policy questions usually want.
5. **Synthetic 9XXX codes** are the platform's own invention, not standard
   accounting codes. They preserve provenance but a CGA accountant would
   not recognise them.
6. **N=5 peer set** is too small for true quartile statistics; flags
   are essentially "best / middle / worst" labels.
7. **LLM levers come from training data**, not from a curated catalog.
   The disclaimer reflects this.
8. **No off-budget liabilities** (state PSU debt, contingent liabilities,
   guarantees). Headline debt figures understate true exposure.
9. **No CAG audit findings** integrated. Material misstatements identified
   by audit are not visible.
10. **BE/RE/ACT comparability** is treated correctly (separate `estimate_type`
    qualifier) but reports default to RE which is itself estimate, not actual.

---

## 14. Open Questions Where Reviewer Input is Most Valuable

Listed in priority order for the reviewer's time:

1. **Is `net_power_subsidy_pct` definitionally correct?** (See §6 reviewer
   question.) This is the headline subsidy metric and we may be using the
   wrong receipts head.

2. **Is `committed_expenditure_pct` (Interest + Pensions) misnamed without
   Salaries?** (See §6.) The "committed" label has a precise meaning in
   state finance literature.

3. **Should the Appendix → account_type mapping be re-validated?** (See §5.)
   Specifically: is Appendix-3's "Public Account" content correctly classified
   as `capital_receipt`?

4. **Is Major Head granularity the right stopping point?** (See §4.) Or
   should v1 immediately go to Minor Head?

5. **Is fixed-window CAGR with COVID included the right trend metric?**
   (See §7.)

6. **Is the LLM advisor's output epistemically acceptable** given the
   disclaimer, or should the prose be removed entirely from briefs?
   (See §10.)

7. **What's missing from the metric catalog** that a Finance Secretary or
   PRS analyst would expect to see at first glance?

---

## 15. How to Critique

The codebase is at `~/Documents/Projects/FinanceOS`. To reproduce:

```bash
cd ~/Documents/Projects/FinanceOS
PYTHONPATH=. python3 -m financeos.network.cli.main rbi-bootstrap
PYTHONPATH=. python3 -m financeos.network.cli.main rbi-ingest \
    --states KA,TN,AP,TG,KL --years 2018-19,2019-20,2020-21,2021-22,2022-23,2024-25,2025-26
PYTHONPATH=. python3 -m financeos.network.cli.main rbi-handbook-ingest \
    --states KA,TN,AP,TG,KL --years 2018-19,2019-20,2020-21,2021-22,2022-23,2024-25
PYTHONPATH=. python3 -m financeos.network.cli.main report \
    --states KA,TN,AP,TG,KL --year 2024-25 --estimate-type RE
```

Briefs land in `reports/`. The structured comparison CSV is at
`reports/comparison_2024-25_RE.csv` — start there if you prefer to critique
numbers before prose. Every metric in the CSV is defined in
[metrics.py](../financeos/apps/metrics.py) with a one-line description and
explicit numerator / denominator references.

Pull requests, issues, and "this metric is wrong because…" emails are all
welcome. Specific corrections beat general observations.
