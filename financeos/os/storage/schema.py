"""SQLite schema for the FinanceOS Knowledge Store.

Mirrors the row layout documented in spec/CELL_SCHEMA.md. The DDL strings
here are the only definition of the on-disk schema — code that reads or
writes the store should refer to these, not embed column lists inline.
"""
from __future__ import annotations

BUDGET_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS budget_signals (
    state           TEXT NOT NULL,
    fiscal_year     TEXT NOT NULL,
    major_head_code TEXT NOT NULL,
    account_type    TEXT NOT NULL,
    signal          TEXT NOT NULL,
    estimate_type   TEXT NOT NULL,
    value           REAL,
    unit            TEXT NOT NULL DEFAULT 'INR_CRORE',
    data_confidence REAL NOT NULL,
    source_id       TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    PRIMARY KEY (state, fiscal_year, major_head_code, account_type,
                 signal, estimate_type, source_id)
);
"""

BUDGET_INGEST_LOG_DDL = """
CREATE TABLE IF NOT EXISTS budget_ingest_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    domain               TEXT NOT NULL,
    states               TEXT NOT NULL,
    fiscal_years         TEXT NOT NULL,
    rows_written         INTEGER NOT NULL,
    status               TEXT NOT NULL,
    conformance_ok       INTEGER NOT NULL,
    conformance_failures TEXT,
    started_at           TEXT NOT NULL,
    finished_at          TEXT NOT NULL
);
"""

BUDGET_METADATA_DDL = """
CREATE TABLE IF NOT EXISTS budget_metadata (
    state        TEXT NOT NULL,
    fiscal_year  TEXT NOT NULL,
    metric       TEXT NOT NULL,
    value        REAL,
    unit         TEXT,
    source_id    TEXT NOT NULL,
    ingested_at  TEXT NOT NULL,
    PRIMARY KEY (state, fiscal_year, metric, source_id)
);
"""

INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_signals_state_year ON budget_signals(state, fiscal_year);",
    "CREATE INDEX IF NOT EXISTS idx_signals_mh ON budget_signals(major_head_code);",
    "CREATE INDEX IF NOT EXISTS idx_log_domain_finished ON budget_ingest_log(domain, finished_at);",
]

ALL_DDL = [BUDGET_SIGNALS_DDL, BUDGET_INGEST_LOG_DDL, BUDGET_METADATA_DDL, *INDICES]
