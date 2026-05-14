# FinanceOS — Canonical Cell Schema

**Version:** 1.0.0-draft
**Status:** Draft
**Component:** Storage / Drivers

---

## Purpose [INFORMATIVE]

This document defines the canonical unit of analysis in FinanceOS — the
"cell" — and the schema for signals attached to it. Every driver writes
signals against this schema. Every app reads signals against this schema.
The conformance gate enforces it.

The cell is the equivalent of the H3 hexagon in AirOS: a discrete, addressable
unit that all evidence is attached to and all reasoning is performed on.

---

## The Cell [NORMATIVE]

A cell is the tuple:

```
(state, fiscal_year, major_head_code, account_type)
```

### `state`

- Type: string, exactly 2 uppercase ASCII letters.
- ISO 3166-2:IN subdivision code without the `IN-` prefix.
- Examples: `KA` (Karnataka), `TN` (Tamil Nadu), `AP` (Andhra Pradesh),
  `TG` (Telangana), `KL` (Kerala).
- The canonical list of valid state codes lives in
  `financeos/drivers/registries/states.json` and MUST be the only source of
  truth for what is a valid state.
- Drivers MUST NOT write rows with a state code not present in the registry.
- The conformance gate MUST reject batches containing unknown state codes.

### `fiscal_year`

- Type: string, format `YYYY-YY`, exactly 7 characters.
- Indian fiscal year, April 1 to March 31.
- Examples: `2024-25` (April 2024 to March 2025), `2023-24`.
- The two-digit suffix MUST be the last two digits of the year following the
  four-digit year. `2024-26` is invalid.
- Drivers MUST NOT use single-integer years.

### `major_head_code`

- Type: string, exactly 4 ASCII digits.
- A CGA Major Head code from the *List of Major and Minor Heads of Account
  of the Union and States* maintained by the Controller General of Accounts.
- Examples: `2210` (Medical and Public Health), `2202` (General Education),
  `0020` (Corporation Tax), `6003` (Internal Debt of the State Government).
- The canonical list of valid Major Head codes lives in
  `financeos/drivers/registries/major_heads.json`.
- Codes are NOT prefixed (`MH2210` is invalid) and NOT suffixed
  (`2210.00` is invalid).
- See [MAJOR_HEADS.md](MAJOR_HEADS.md) for the structure of the coding system.

### `account_type`

- Type: string, one of:
  - `revenue_receipt`
  - `capital_receipt`
  - `revenue_exp`
  - `capital_exp`
- This dimension exists because the same Major Head can appear under both
  Receipts and Expenditure (rare) and under both Revenue and Capital
  account (common). The pair must be disambiguated.
- Drivers MUST NOT use any other value. The conformance gate MUST reject
  batches containing unknown account types.

### Cell Identity

A cell is uniquely identified by the four fields above. There is no surrogate
key. Two rows with the same `(state, fiscal_year, major_head_code,
account_type)` and the same `signal` name and the same `estimate_type`
qualifier MUST be considered duplicates.

---

## The Signal Row [NORMATIVE]

Every cell can have many signals attached to it. A signal row is:

```
budget_signals(
  state           text not null,
  fiscal_year     text not null,
  major_head_code text not null,
  account_type    text not null,
  signal          text not null,
  estimate_type   text not null,   -- BE | RE | ACT
  value           real,            -- in INR Crore unless unit overridden
  unit            text not null,   -- default 'INR_CRORE'
  data_confidence real,            -- 0.0 to 1.0; see Rule 1 of CONFORMANCE
  source_id       text not null,   -- FK to driver registry
  ingested_at     text not null,   -- ISO-8601 UTC timestamp
  primary key (state, fiscal_year, major_head_code, account_type,
               signal, estimate_type, source_id)
)
```

### `signal`

- Type: string, lowercase ASCII letters, digits, underscores.
- Examples: `total_expenditure`, `own_tax_revenue`, `interest_payments`,
  `capital_outlay`.
- The canonical signal list per driver is declared in the driver's
  `signal_names` identity field. See [DRIVER_INTERFACE.md](DRIVER_INTERFACE.md).
- Every driver MUST include `data_confidence` as a populated column on every
  row it writes. See [CONFORMANCE.md](CONFORMANCE.md) Rule 1.

### `estimate_type`

- Type: string, exactly one of:
  - `BE` — Budget Estimate (presented at start of fiscal year)
  - `RE` — Revised Estimate (presented in February of the same fiscal year)
  - `ACT` — Actuals (audited, typically published 18-24 months later by CAG)
- These three are NOT interchangeable. Treating BE as ACT silently is a
  category error that has caused real-world policy mistakes. Drivers MUST
  populate this field. The conformance gate MUST reject rows where it is null.

### `value` and `unit`

- `value` is a floating-point number, the magnitude of the signal.
- `unit` is the unit of measure. Default is `INR_CRORE` (1 Crore = 10 million
  rupees). All cross-state comparison code SHOULD assume `INR_CRORE` and
  refuse to operate on mixed-unit batches.
- Other allowed units (used by specific signals only):
  - `RATIO` — dimensionless (e.g. tax-to-GSDP ratio)
  - `PCT` — percentage (e.g. share of own tax revenue)
  - `INR_LAKH` — for line items below 1 Crore where source publishes Lakh
- Drivers MUST NOT silently convert between units. Conversion is the app
  layer's responsibility and MUST be logged.

### `data_confidence`

- Type: float in `[0.0, 1.0]`.
- A driver-supplied estimate of the reliability of the value. Examples:
  - `1.0` — directly extracted from RBI annexure XLSX, no transformation
  - `0.8` — extracted from a state PDF table with structure verification
  - `0.5` — extracted from PDF prose via regex
  - `0.2` — derived by interpolation or imputation
- See [CONFORMANCE.md](CONFORMANCE.md) §Rule 1 for enforcement.

### `source_id`

- Type: string, machine-readable identifier of the driver that produced this
  row. Format: `<source>.<dataset>.<version>`.
- Examples: `rbi.state_finances.2024-25`, `prs.budget_brief.kl.2024-25`,
  `state_pdf.kl.bag.2024-25`.
- This is the provenance pointer. Every signal in the store MUST be
  attributable to exactly one source.

### `ingested_at`

- Type: string, ISO-8601 UTC timestamp with `Z` suffix.
- Set by the ingestor at write time, not by the driver.

---

## Cardinality Expectations [INFORMATIVE]

For the v0 scope (South-5 × latest year × RBI-only), expected row counts:

| Dimension | Cardinality |
|---|---|
| `state` | 5 |
| `fiscal_year` | 1 |
| `major_head_code` | ~250 used (out of ~600 defined) |
| `account_type` | 4 |
| `signal` per cell | ~3 (value, growth, share) |
| `estimate_type` | up to 3 (BE, RE, ACT) |

Upper bound on rows for v0: `5 × 1 × 250 × 4 × 3 × 3 = ~45,000 rows`.
SQLite handles this comfortably.

For the eventual all-states × 10-year scope:
`28 × 10 × 250 × 4 × 3 × 3 = ~2.5 M rows`. Still well within SQLite limits.

---

## What this schema does NOT cover [INFORMATIVE]

- **Sub-Major / Minor / Sub-Minor / Detailed Heads.** v1 stays at Major Head
  granularity. Finer granularity will be a separate `budget_signals_minor`
  table with the same identity tuple plus `minor_head_code`.
- **Schemes.** Centrally Sponsored Schemes and state schemes cut across
  Major Heads. They will be a separate `budget_schemes` table joined by
  `(state, fiscal_year, scheme_code)`.
- **District-level data.** District treasury data exists for some states but
  is patchy. Out of scope until v2.
- **Outcomes.** Health/education outcomes that budget allocations should be
  evaluated against are out of scope. Budget data is allocation data; outcome
  joining is an app concern, not a kernel concern.
