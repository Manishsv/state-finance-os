# Project guidelines for Claude

This file is loaded into every Claude Code session in this repo. Keep it short.

## What FinanceOS is

A platform for ingesting and reasoning over Indian state budget data. The
architecture mirrors AirOS (sibling project at `~/Documents/Projects/AirPollution`).
The canonical unit is `(state, fiscal_year, major_head_code, account_type)` —
read [spec/CELL_SCHEMA.md](spec/CELL_SCHEMA.md) before writing any driver or
storage code.

## Project conventions

- **Python 3.9+** (Anaconda 3.9 is the dev env).
- **Package layout:** all code under `financeos/`. Tests under `tests/`. Specs
  under `spec/`. Data under `data/` (gitignored).
- **State codes:** ISO 3166-2:IN (`KA`, `TN`, `AP`, `TG`, `KL`, ...). Never use
  full state names as keys — they are unstable.
- **Fiscal years:** `YYYY-YY` strings (`2024-25`). Indian fiscal year is
  April-March. Never use a single integer year — it is ambiguous.
- **Major Head codes:** 4-digit strings (`2210`, not `2210.0` or `MH2210`).
- **Money:** all amounts stored in INR Crore (1 Crore = 10 million). The unit
  is part of the schema — do not mix Lakh, Crore, or Rupee in storage.

## Behavioural guidelines

These are inherited from the user's standard guidelines — apply them here:

1. **Think before coding.** State assumptions. Ask if uncertain. Surface
   tradeoffs. If multiple interpretations exist, present them — don't pick
   silently.
2. **Simplicity first.** Minimum code that solves the problem. No
   speculative features, no abstractions for single-use code, no error
   handling for impossible scenarios.
3. **Surgical changes.** Touch only what you must. Don't refactor adjacent
   code. Match existing style. Mention unrelated dead code; don't delete it.
4. **Goal-driven execution.** Define a verification check for every step.
   For multi-step tasks, state a brief plan with verify steps.

## Things to NOT do

- Do not silently translate budget data between units (Lakh ↔ Crore). Reject
  at the conformance gate or convert explicitly with provenance.
- Do not mix Budget Estimates, Revised Estimates, and Actuals in a single
  signal without an `estimate_type` qualifier — they are different things.
- Do not write driver code that reads from `budget_signals` as input. Drivers
  produce signals; they do not consume them. (Mirrors AirOS rule.)
- Do not feed raw extracted text to the LLM advisor. The advisor consumes
  only structured findings emitted by the rule layer. See
  [spec/CONFORMANCE.md](spec/CONFORMANCE.md) §Advisor for the guardrail.
