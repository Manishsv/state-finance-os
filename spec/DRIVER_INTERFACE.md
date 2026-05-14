# FinanceOS — Driver Interface Specification

**Version:** 1.0.0-draft
**Status:** Draft
**Component:** Drivers

---

## Purpose [INFORMATIVE]

This document defines the interface that every FinanceOS data source driver
must implement. The interface is language-agnostic: requirements are
expressed as logical contracts. The Python reference implementation
(`financeos.drivers.connectors.base.BudgetDataSourceDriver`) is one correct
realisation.

A "driver" is the unit of data acquisition and cell mapping. One driver
corresponds to one upstream source × one domain × one estimate type. For
example, one driver pulls RBI's State Finances annexure tables for Budget
Estimates; a separate driver pulls the same source's Revised Estimates.

---

## Driver Identity [NORMATIVE]

Every driver MUST declare the following identity fields. These MUST be
static — they MUST NOT change between calls to `fetch`.

### `domain`

- Type: string, lowercase ASCII letters, digits, underscores.
- Machine-readable domain identifier. MUST be unique across all drivers
  active in a deployment.
- Examples: `revenue_rbi`, `expenditure_rbi`, `revenue_state_pdf_ka`.
- SHOULD use a suffix when multiple drivers cover the same canonical domain.

### `cadence_hours`

- Type: positive number.
- The minimum elapsed time between successive `fetch` calls for the same
  `(state, fiscal_year)` partition.
- The Scheduler MUST NOT call `fetch` more frequently than `cadence_hours`
  unless `force=true`.
- Typical values for FinanceOS:
  - `8760` (annual) — for finalised RBI data
  - `2160` (quarterly) — for state expenditure dashboards
  - `720` (monthly) — for CGA monthly account reports

### `produces_assessments`

- Type: boolean.
- `true` if the driver writes computed `budget_assessments` rows (e.g.
  fiscal-stress classifications). `false` for raw-signal-only drivers.

### `signal_names`

- Type: list of strings.
- The canonical signal names this driver writes to `budget_signals.signal`.
- Used by the conformance gate. See [CONFORMANCE.md](CONFORMANCE.md).
- All signal names MUST be lowercase ASCII letters, digits, underscores.

### `estimate_types`

- Type: list of strings, subset of `{BE, RE, ACT}`.
- The estimate types this driver produces. A driver pulling Budget
  Estimates only declares `["BE"]`. A driver that pulls all three from a
  consolidated source declares `["BE", "RE", "ACT"]`.
- The conformance gate MUST reject rows whose `estimate_type` is not in
  this list.

### `data_sources`

- Type: list of human-readable strings.
- Descriptions of the upstream sources used. Used in dashboard provenance
  labels and evidence bundles passed to the LLM advisor.

### `source_id_template`

- Type: string. A template producing the per-row `source_id` value.
- Format placeholders: `{state}`, `{fiscal_year}`, `{estimate_type}`.
- Example: `rbi.state_finances.{fiscal_year}.{estimate_type}`.
- The conformance gate MUST verify the rendered `source_id` matches the
  template.

---

## Required Operations [NORMATIVE]

### `fetch(states, fiscal_years, force=false) → integer`

The primary operation. Pulls data from the upstream source, maps it to
cells, and writes signals to the Knowledge Store via the ingestor.

**Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `states` | list of string | State codes to fetch (ISO 3166-2:IN, no `IN-` prefix) |
| `fiscal_years` | list of string | Fiscal years to fetch, format `YYYY-YY` |
| `force` | boolean | If true, ignore cadence watermark |

**Returns:** integer — the number of `budget_signals` rows written. Zero is
a valid return value (no new data available). `-1` is reserved for "skipped
because cadence not elapsed" (only valid when `force=false`). MUST NOT
return any other negative number.

**MUST:**

- Call `record_ingest(states, fiscal_years, domain, rows_written, status)`
  before returning, whether the fetch succeeds, partially succeeds, or fails.
- Respect the cadence watermark unless `force=true`.
- Write only to `budget_signals`, `budget_assessments`, and `budget_metadata`
  tables. MUST NOT write to `budget_findings`, `budget_advisor_outputs`,
  or any App table.
- Write at Major Head granularity. Sub-major / Minor / Detailed Heads are a
  separate table not yet defined.
- Populate `data_confidence` on every row. (See [CONFORMANCE.md](CONFORMANCE.md)
  Rule 1.)
- Populate `estimate_type` on every row.
- Be idempotent: two calls for the same `(state, fiscal_year, estimate_type)`
  MUST NOT produce duplicate rows. Re-runs MUST `INSERT OR REPLACE`.

**MUST NOT:**

- Read from `budget_signals` or `budget_assessments` as input to the fetch
  computation. (Drivers produce signals; they do not consume them.)
- Write rows for states not in the `states` parameter.
- Write rows whose Major Head code is not in
  `financeos/drivers/registries/major_heads.json`.
- Silently convert units. If the source publishes in INR Lakh, the driver
  MUST EITHER convert to INR Crore explicitly AND set `unit='INR_CRORE'`,
  OR write with `unit='INR_LAKH'` — never both, never neither.
- Mix BE/RE/ACT in a single row. Each row carries exactly one
  `estimate_type`.
- Raise an exception for transient upstream errors. Implement internal
  retry; only raise after exhausting retries.

**Error handling:** on unrecoverable error the driver MUST call
`record_ingest` with `status=error` and then raise `DriverFetchError`. The
Scheduler catches this and continues with other drivers — a single driver
failure MUST NOT abort the ingest run.

---

### `conformance_check() → ConformanceResult`

Static validation of the driver's configuration. Called once at load time.

**MUST:**

- Complete in under 2 seconds.
- Not make live network calls to upstream APIs.
- Check that all required environment variables, credentials, and bundled
  registries (e.g. `major_heads.json`) are present.
- Return a `ConformanceResult` with `ok=true` if ready, `ok=false` with
  failures otherwise.

**SHOULD:**

- Return `warnings` for non-blocking issues (degraded confidence expected,
  optional credentials missing).

A driver that returns `ok=false` MUST NOT be added to the active pool. The
Scheduler MUST NOT call `fetch` on it.

---

## `ConformanceResult` Shape [NORMATIVE]

```
ConformanceResult {
  ok:        boolean         — true if driver is ready to fetch
  failures:  list of string  — blocking problems; ok MUST be false if non-empty
  warnings:  list of string  — non-blocking observations
}
```

---

## Driver Discovery [NORMATIVE]

Drivers are discovered through the **Driver Registry**: a deployment
configuration file (`financeos/drivers/registries/driver_registry.json`).

**Trust rule:** A driver discovered via Python entry points but NOT listed
in the Driver Registry with `trusted: true` MUST be quarantined — logged as
a warning and not loaded. The deployment operator MUST opt in to each
driver explicitly. (Mirrors AirOS.)

**Entry point convention (Python):**

```toml
[project.entry-points."financeos.drivers"]
revenue_rbi = "financeos.drivers.connectors.rbi.state_finances:RbiStateFinancesDriver"
```

---

## Driver Registry Format [NORMATIVE]

For each active driver:

| Field | Required | Description |
|---|---|---|
| `trusted` | YES | Boolean. Only `true` drivers load. |
| `trust_level` | YES | `core` / `verified` / `local` |
| `builtin_class` or `package` | YES | Driver class location |
| `cadence_hint` | NO | Documentation cadence string |
| `notes` | NO | Operator notes |

For third-party drivers (RECOMMENDED):

| Field | Description |
|---|---|
| `version_pin` | e.g. `>=1.2,<2.0` |
| `added_by` | Operator identifier |
| `added_at` | ISO-8601 date |

---

## Stability Guarantee [NORMATIVE]

Driver Interface v1.x.y is stable from v1.0.0:

- New optional identity fields → minor version bump.
- Signature changes to `fetch` or `conformance_check` → major version bump.
- Removing a required field → major version bump + migration guide.
