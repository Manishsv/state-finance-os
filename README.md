# FinanceOS

A platform for ingesting, normalising, comparing, and reasoning over Indian
state government budget data — modelled on the AirOS architecture, with budget
classification heads as the unit of analysis instead of H3 hexagonal cells.

## Status

**Pre-alpha.** v0 scope: South-5 peer group (Tamil Nadu, Karnataka, Andhra
Pradesh, Telangana, Kerala), latest published year, RBI "State Finances: A
Study of Budgets" as the sole source.

## Why a platform, not a script

Budget analysis at the Indian state level is bottlenecked by data plumbing:
every state publishes Budget-at-a-Glance, Demands for Grants, and Annual
Financial Statements in a different format; classifications drift across
states despite a shared CGA accounting code; and most analyses are one-off
spreadsheets that cannot be re-run when revised estimates land.

FinanceOS treats the plumbing as the product. A driver downloads from one
source, normalises to a canonical cell schema, and writes through a
conformance gate to a SQLite store. Apps read from the store via an SDK.
Adding a new state, a new year, or a new source means writing one driver.

## Canonical cell

The unit of analysis is `(state, fiscal_year, major_head_code, account_type)`:

| Field | Example | Notes |
|---|---|---|
| `state` | `KA` | ISO 3166-2:IN code (KA, TN, AP, TG, KL, ...) |
| `fiscal_year` | `2024-25` | Indian fiscal year, April-March |
| `major_head_code` | `2210` | CGA Major Head — `2210` = Medical & Public Health |
| `account_type` | `revenue_exp` | One of `revenue_receipt`, `capital_receipt`, `revenue_exp`, `capital_exp` |

See [spec/CELL_SCHEMA.md](spec/CELL_SCHEMA.md) for the full definition.

## Architecture (mirrors AirOS)

```
financeos/
├── os/             # Kernel: SDK, scheduler, storage, conformance
├── drivers/        # Data acquisition (RBI, PRS, state PDFs, CAG, ...)
│   ├── connectors/ # One driver per (source × domain)
│   ├── store/      # SQLite KnowledgeStore + ingestor + conformance gate
│   └── registries/ # Canonical lookup tables (Major Heads, state codes)
├── apps/           # Comparison, ranking, narrative generation
└── network/cli/    # Command-line entrypoints
```

## Specifications

- [Cell schema](spec/CELL_SCHEMA.md) — canonical unit of analysis
- [Driver interface](spec/DRIVER_INTERFACE.md) — what every driver must implement
- [Conformance gate](spec/CONFORMANCE.md) — what the store enforces on every write
- [Major Heads](spec/MAJOR_HEADS.md) — reference to CGA Major Head codes

## Acknowledgement

The architecture is a direct port of patterns from AirOS (sister project).
Where the air-quality domain enforces an H3 resolution, FinanceOS enforces a
Major Head granularity; where AirOS requires `DATA_CONFIDENCE` per H3 cell,
FinanceOS requires it per budget cell. The kernel shape is the same.
