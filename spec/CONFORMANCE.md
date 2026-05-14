# FinanceOS — Conformance Gate Specification

**Version:** 1.0.0-draft
**Status:** Draft
**Component:** Drivers / Storage / Advisor

---

## Purpose [INFORMATIVE]

The conformance gate is the enforcement layer between Drivers and the
Knowledge Store. It runs automatically when a Driver calls `write_signals`.
Its job is to catch driver output that violates the cell schema contract
before bad data enters the store and propagates to App reasoning.

A second gate — the **Advisor Conformance Gate** (§ "Advisor" below) —
enforces that LLM-generated narrative cites only structured findings, with
no free-form numbers or claims.

The gate distinguishes **blocking** failures (write rejected) from
**non-blocking** warnings (write proceeds, problem logged in
`budget_ingest_log`).

This separation reflects the same asymmetry as AirOS: data that is
structurally wrong must never enter the store; data that is questionable
is better stored with a warning than silently dropped.

**Scope and limits:** the gate validates **structure and schema** — it does
not validate truth. A driver that extracts a wrong number from a PDF will
pass the gate. Truth-checking belongs to the Advisor layer (cross-source
comparison, year-over-year sanity, peer-state plausibility ranges).

---

## Definitions [NORMATIVE]

**Batch:** a single call to `write_signals`. All gate rules are evaluated
per batch. A batch contains all signal rows from one driver × one fetch
invocation — typically one driver × N states × 1 fiscal year × 1
estimate type.

---

## Gate Rules [NORMATIVE]

### Rule 1 — `data_confidence` must be populated for every row [BLOCKING]

For every row in the batch, `data_confidence` MUST be a float in `[0.0, 1.0]`.
Null, NaN, or out-of-range values cause the entire batch to be rejected.

**Rationale:** Every signal in the Knowledge Store must carry confidence
metadata. The Advisor weights evidence by reliability — a signal without
`data_confidence` cannot be reasoned about. (Mirrors AirOS Rule 1.)

**Error message pattern:**
```
[<domain>] data_confidence is null or out of [0,1] for <n> row(s):
sample: [<state, fiscal_year, major_head, signal>].
Every row must declare confidence in [0.0, 1.0].
```

---

### Rule 2 — Declared signals absent from rows [NON-BLOCKING]

If the driver declares `signal_names` and one or more declared signals are
absent from the submitted rows, a WARNING is logged.

**Rationale:** Absence may be legitimate — a state may not levy a
particular tax in a given year. Repeated absences SHOULD be investigated.

**Warning message pattern:**
```
[<domain>] Declared signal(s) absent from rows: [<signals>].
May be legitimate (state does not levy this) or a driver bug.
```

---

### Rule 3 — Major Head code not in canonical registry [BLOCKING]

If any row has a `major_head_code` not present in
`financeos/drivers/registries/major_heads.json`, the entire batch MUST be
rejected.

**Rationale:** Unknown Major Heads cannot be rolled up to functional
categories or compared across states. Storing them silently corrupts
cross-state comparison. (Equivalent to AirOS H3-resolution-8 enforcement.)

**Error message pattern:**
```
[<domain>] <n> row(s) reference unknown major_head_code(s):
[<codes>]. Add to registries/major_heads.json or fix the driver mapping.
```

---

### Rule 4 — Unknown state code [BLOCKING]

If any row has a `state` code not present in
`financeos/drivers/registries/states.json`, the entire batch MUST be
rejected.

**Error message pattern:**
```
[<domain>] <n> row(s) reference unknown state code(s): [<codes>].
Use ISO 3166-2:IN codes (e.g. KA, TN, AP, TG, KL).
```

---

### Rule 5 — Invalid `estimate_type` [BLOCKING]

If any row has `estimate_type` not in `{BE, RE, ACT}`, the entire batch
MUST be rejected.

**Rationale:** Estimate type is a category, not a free string. Treating BE
as ACT silently is a real-world hazard. (See [CELL_SCHEMA.md](CELL_SCHEMA.md)
§`estimate_type`.)

**Error message pattern:**
```
[<domain>] <n> row(s) have invalid estimate_type: [<values>].
Must be one of {BE, RE, ACT}.
```

---

### Rule 6 — Invalid `account_type` [BLOCKING]

If any row has `account_type` not in
`{revenue_receipt, capital_receipt, revenue_exp, capital_exp}`, the entire
batch MUST be rejected.

---

### Rule 7 — Invalid `fiscal_year` format [BLOCKING]

If any row has a `fiscal_year` not matching `^\d{4}-\d{2}$` or where the
two-digit suffix is not the last two digits of the year following the
four-digit year (`2024-25` valid, `2024-26` invalid, `2024` invalid), the
entire batch MUST be rejected.

---

### Rule 8 — Unknown unit [BLOCKING]

If any row has `unit` not in `{INR_CRORE, INR_LAKH, RATIO, PCT}`, the
entire batch MUST be rejected.

**Rationale:** Units are part of the schema. Unknown units silently break
all aggregation.

---

### Rule 9 — Null or NaN values [NON-BLOCKING]

Rows with `value = null` or `value = NaN` are silently skipped by the
write operation. If a significant fraction of rows have null values, a
WARNING is logged.

---

### Rule 10 — Implausible value range [NON-BLOCKING]

If a signal's declared `range: [min, max]` is exceeded, a WARNING is
logged. Write proceeds — legitimate values sometimes exceed expected
ranges (e.g. natural disaster relief expenditure spikes).

---

## Conformance Check at Load Time [NORMATIVE]

In addition to the per-fetch gate, the Driver Interface requires
`conformance_check()` at load time. See
[DRIVER_INTERFACE.md](DRIVER_INTERFACE.md#conformance_check--conformanceresult).

A driver that fails `conformance_check()` MUST NOT be loaded.

---

## Gate Result Recording [NORMATIVE]

The outcome of every conformance gate evaluation MUST be recorded in
`budget_ingest_log`:

| Field | Value |
|---|---|
| `conformance_ok` | `true` if no blocking failures; `false` otherwise |
| `conformance_failures` | JSON array of all messages — `[FAIL]` and `[WARN]` prefixed |

This record MUST be written even when the write is blocked
(`rows_written = 0`).

---

## Gate Bypass [NORMATIVE]

The conformance gate MUST NOT be bypassable in production. An
implementation MAY expose a `skip_conformance` flag for test harnesses, but
this flag MUST default to `false` and MUST require an environment variable
(`FINANCEOS_SKIP_CONFORMANCE=true`) absent from all production deployment
manifests. (Mirrors AirOS.)

---

## Advisor Conformance Gate [NORMATIVE]

A second gate enforces honesty in LLM-generated narrative.

### Rule A1 — Findings are the only input to narrative [BLOCKING]

The Advisor MUST pass the LLM only structured `BudgetFinding` objects (see
schema below) plus a static system prompt. The Advisor MUST NOT pass raw
signal rows, raw extracted PDF text, or free-form context.

```
BudgetFinding {
  state:           string
  fiscal_year:     string
  metric:          string         — e.g. "tax_to_gsdp_ratio"
  value:           number
  unit:            string
  peer_median:     number | null
  peer_exemplar:   { state: string, value: number } | null
  rank_in_peers:   integer | null
  flag:            "above_peers" | "below_peers" | "within_peers" | null
}
```

### Rule A2 — No number in narrative absent from findings [BLOCKING]

The Advisor MUST scan the LLM output for numeric tokens. Every numeric
token in the output MUST be findable as a value within the `BudgetFinding`
list passed in (after rounding to declared precision). If any number in
the output is not present, the output MUST be rejected and the Advisor
MUST retry with a stricter prompt; after 2 retries the output is discarded
and the user is shown findings only.

**Rationale:** This is the central honesty guarantee. LLM narrative
plausibly states wrong numbers; deterministic post-checking is the only
defence.

### Rule A3 — Recommendations are flagged as recommendations [NORMATIVE]

The Advisor's prompt MUST instruct the LLM to label every recommendation
as a recommendation (e.g. "Karnataka could consider..."), never as a
factual claim about what the state will do or has decided.

---

## Compliance Levels [INFORMATIVE]

| Rule | Severity | Conformance claim requires |
|---|---|---|
| 1 — `data_confidence` populated | BLOCKING | Driver MUST set on every row in [0,1] |
| 2 — Declared signals match rows | WARNING | Driver SHOULD write all declared signals |
| 3 — Major Head in registry | BLOCKING | Driver MUST map all values to canonical codes |
| 4 — State code valid | BLOCKING | Driver MUST use ISO 3166-2:IN codes |
| 5 — `estimate_type` valid | BLOCKING | Driver MUST set BE / RE / ACT |
| 6 — `account_type` valid | BLOCKING | Driver MUST set one of the four canonical values |
| 7 — `fiscal_year` format | BLOCKING | Driver MUST use `YYYY-YY` |
| 8 — Unit valid | BLOCKING | Driver MUST set a known unit |
| 9 — No null values | WARNING | Driver SHOULD filter nulls before submitting |
| 10 — Values in range | WARNING | Driver SHOULD validate upstream |
| A1 — Findings-only LLM input | BLOCKING | Advisor MUST NOT pass raw signals to LLM |
| A2 — No new numbers from LLM | BLOCKING | Advisor MUST post-validate numeric tokens |
| A3 — Recommendations labelled | NORMATIVE | Advisor system prompt MUST require it |
