"""FinanceOS CLI entrypoint."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, MutableMapping, Optional

from financeos.os import sdk
from financeos.os.storage.db import connect, init_schema


def _clear_empty_dotenv_keys(env_path: Path,
                             environ: MutableMapping[str, str]) -> List[str]:
    """Remove env vars that are empty strings AND defined in the dotenv file.

    Why this exists: `load_dotenv(override=False)` treats `KEY=""` in the
    process environment as "already set" and refuses to load the value from
    .env. That breaks setups where a parent shell exports an empty-string
    placeholder. Treating empty-string as unset makes the .env value win in
    that specific case while still preserving the AirOS "explicit non-empty
    shell export wins over .env" semantics for everything else.

    Returns the list of keys that were cleared (for logging/testing).
    """
    from dotenv import dotenv_values
    cleared: List[str] = []
    for k in dotenv_values(env_path):
        if k in environ and environ[k] == "":
            del environ[k]
            cleared.append(k)
    return cleared


def _load_dotenv_if_available() -> None:
    """Load `.env` from the repo root before any provider SDK sees os.environ.

    AirOS pattern: `dotenv.load_dotenv()` runs once at the entrypoint, never
    inside library code. `override=False` so an explicit shell export wins
    over the .env file (useful for one-off overrides without editing .env).

    FinanceOS divergence: empty-string env vars are treated as unset
    (see `_clear_empty_dotenv_keys`), so a stray `export KEY=""` in a parent
    shell does not block the .env value from taking effect.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for parent in Path(__file__).resolve().parents:
        env_path = parent / ".env"
        if env_path.exists():
            _clear_empty_dotenv_keys(env_path, os.environ)
            load_dotenv(dotenv_path=env_path, override=False)
            return


def main(argv: Optional[List[str]] = None) -> int:
    _load_dotenv_if_available()

    parser = argparse.ArgumentParser(prog="financeos", description="FinanceOS CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Print registry summary, drivers, and store row counts")
    sub.add_parser("schema", help="Print canonical cell schema")

    sub.add_parser("rbi-bootstrap",
                   help="Add synthetic 9XXX codes for RBI heads to the registry")

    ing = sub.add_parser("rbi-ingest", help="Ingest RBI State Finances data into the store")
    ing.add_argument("--states", default="KA,TN,AP,TG,KL",
                     help="Comma-separated state codes (default: South-5)")
    ing.add_argument("--years", required=True,
                     help="Comma-separated fiscal years YYYY-YY (e.g. 2024-25,2023-24)")
    ing.add_argument("--db", default=None, help="Override DB path")

    hb = sub.add_parser("rbi-handbook-ingest",
                        help="Ingest GSDP + population from RBI Handbook into budget_metadata")
    hb.add_argument("--states", default="KA,TN,AP,TG,KL",
                    help="Comma-separated state codes (default: South-5)")
    hb.add_argument("--years", required=True,
                    help="Comma-separated fiscal years YYYY-YY")
    hb.add_argument("--db", default=None, help="Override DB path")

    rep = sub.add_parser("report", help="Compute metrics, rank peers, write per-state briefs and a comparison CSV")
    rep.add_argument("--states", default="KA,TN,AP,TG,KL", help="Comma-separated state codes")
    rep.add_argument("--year", required=True, help="Fiscal year YYYY-YY")
    rep.add_argument("--estimate-type", default="BE", choices=["BE", "RE", "ACT"],
                     help="Which estimate type to compare on (default: BE)")
    rep.add_argument("--out-dir", default="reports", help="Where to write briefs and CSV")
    rep.add_argument("--db", default=None, help="Override DB path")
    rep.add_argument("--with-narrative", action="store_true",
                     help="Add LLM-generated prose narrative to each state brief "
                          "(requires ANTHROPIC_API_KEY). Output is post-validated to "
                          "ensure no fabricated numbers; see spec/CONFORMANCE.md §A2.")
    rep.add_argument("--model", default=None,
                     help="Override the Claude model (default: claude-sonnet-4-6)")

    args = parser.parse_args(argv)

    if args.cmd == "status":
        return _cmd_status()
    if args.cmd == "schema":
        print(json.dumps(sdk.get_cell_schema(), indent=2))
        return 0
    if args.cmd == "rbi-bootstrap":
        from financeos.drivers.connectors.rbi.bootstrap import bootstrap_registry
        bootstrap_registry()
        return 0
    if args.cmd == "rbi-ingest":
        return _cmd_rbi_ingest(args)
    if args.cmd == "rbi-handbook-ingest":
        return _cmd_rbi_handbook_ingest(args)
    if args.cmd == "report":
        return _cmd_report(args)

    return 1


def _cmd_status() -> int:
    regs = sdk.get_registries()
    drivers = sdk.list_drivers()
    print(f"States in registry:        {len(regs.state_codes)}")
    print(f"Major heads in registry:   {len(regs.major_head_codes)}")
    print(f"Functional categories:     {len(regs.functional_categories)}")
    print(f"Trusted drivers:           {len(drivers)}")
    for d in drivers:
        print(f"  - {d.get('domain', '?')} ({d.get('trust_level', '?')})")
    db_path = Path("data/budgets/knowledge.sqlite")
    if db_path.exists():
        conn = connect(db_path)
        try:
            try:
                signal_count = conn.execute(
                    "SELECT COUNT(*) FROM budget_signals"
                ).fetchone()[0]
                ingest_count = conn.execute(
                    "SELECT COUNT(*) FROM budget_ingest_log"
                ).fetchone()[0]
                print(f"\nStore: {db_path}")
                print(f"  budget_signals rows:    {signal_count:,}")
                print(f"  budget_ingest_log rows: {ingest_count:,}")
                state_summary = conn.execute(
                    "SELECT state, COUNT(*) c FROM budget_signals "
                    "GROUP BY state ORDER BY c DESC"
                ).fetchall()
                if state_summary:
                    print(f"  Rows per state:")
                    for r in state_summary:
                        print(f"    {r['state']}: {r['c']:,}")
            finally:
                conn.close()
        except Exception as e:
            print(f"\nStore exists but unreadable: {e}")
    else:
        print(f"\nStore: not yet created ({db_path})")
    return 0


def _cmd_report(args) -> int:
    from financeos.apps.compare import build_findings
    from financeos.apps.metrics import compute_metrics, compute_trend_metrics
    from financeos.apps.report import render_comparison_csv, render_state_brief

    states = [s.strip() for s in args.states.split(",") if s.strip()]
    db_path = Path(args.db) if args.db else Path("data/budgets/knowledge.sqlite")
    if not db_path.exists():
        print(f"Store not found: {db_path}. Run `financeos rbi-ingest` first.")
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        point_rows = compute_metrics(conn, states=states, fiscal_year=args.year,
                                     estimate_type=args.estimate_type)
        trend_rows = compute_trend_metrics(conn, states=states)
        rows = point_rows + trend_rows
    finally:
        conn.close()

    findings = build_findings(rows)
    if not findings:
        print(f"No findings produced for {args.states} {args.year} {args.estimate_type}. "
              f"Check the store has data for this slice.")
        return 3

    by_state: dict = {}
    for f in findings:
        by_state.setdefault(f.state, []).append(f)

    advisor = None
    if args.with_narrative:
        from financeos.apps.advise import BudgetAdvisor, make_llm_chat_callable
        from financeos.os.llm import LLMClient, load_config
        try:
            cfg = load_config(overrides={"model": args.model} if args.model else None)
            client = LLMClient(cfg)
            chat_callable = make_llm_chat_callable(client=client)
        except (ValueError, ImportError) as e:
            print(f"Cannot generate narratives: {e}")
            return 4
        print(f"  LLM provider: {cfg.label} | model: {cfg.model}")
        advisor = BudgetAdvisor(chat_callable)

    total_cost_in = total_cost_out = 0
    for state in states:
        state_findings = by_state.get(state, [])
        narrative_text = None
        narrative_attempts = None
        if advisor and state_findings:
            ar = advisor.advise(state, args.year, args.estimate_type, state_findings)
            narrative_text = ar.narrative
            narrative_attempts = ar.attempts
            total_cost_in += ar.cost_input_tokens
            total_cost_out += ar.cost_output_tokens
            if narrative_text is None:
                print(f"  [{state}] narrative REJECTED after {ar.attempts} attempt(s); "
                      f"invented numbers: {ar.rejected_numbers}")
            else:
                print(f"  [{state}] narrative OK (attempt {ar.attempts}, "
                      f"{ar.cost_input_tokens} in / {ar.cost_output_tokens} out tokens)")

        brief = render_state_brief(
            state, state_findings,
            narrative=narrative_text,
            narrative_attempts=narrative_attempts,
        )
        brief_path = out_dir / f"{state}_{args.year}_{args.estimate_type}.md"
        brief_path.write_text(brief)
        print(f"  wrote {brief_path}")

    csv_path = out_dir / f"comparison_{args.year}_{args.estimate_type}.csv"
    render_comparison_csv(findings, csv_path)
    print(f"  wrote {csv_path}")
    print(f"Done: {len(findings)} findings across {len(by_state)} states.")
    if advisor:
        print(f"  LLM tokens: {total_cost_in} in / {total_cost_out} out")
    return 0


def _cmd_rbi_handbook_ingest(args) -> int:
    from financeos.drivers.connectors.rbi.handbook import RbiHandbookDriver
    from financeos.drivers.registries.loader import load_registries
    from financeos.drivers.store.ingestor import Ingestor

    states = [s.strip() for s in args.states.split(",") if s.strip()]
    years = [y.strip() for y in args.years.split(",") if y.strip()]
    db_path = Path(args.db) if args.db else Path("data/budgets/knowledge.sqlite")
    conn = connect(db_path)
    init_schema(conn)
    try:
        regs = load_registries()
        ingestor = Ingestor(conn, regs)
        driver = RbiHandbookDriver(ingestor=ingestor, registries=regs)
        cr = driver.conformance_check()
        if not cr.ok:
            for f in cr.failures: print(f"  [FAIL] {f}")
            return 2
        print(f"Ingesting RBI Handbook (GSDP + population) for {states} years={years} ...")
        written = driver.fetch(states=states, fiscal_years=years)
        print(f"Done. Metadata rows written: {written:,}")
    finally:
        conn.close()
    return 0


def _cmd_rbi_ingest(args) -> int:
    from financeos.drivers.connectors.rbi.state_finances import (
        RbiStateFinancesDriver,
    )
    from financeos.drivers.registries.loader import load_registries
    from financeos.drivers.store.ingestor import Ingestor

    states = [s.strip() for s in args.states.split(",") if s.strip()]
    years = [y.strip() for y in args.years.split(",") if y.strip()]

    db_path = Path(args.db) if args.db else Path("data/budgets/knowledge.sqlite")
    conn = connect(db_path)
    init_schema(conn)
    try:
        regs = load_registries()
        ingestor = Ingestor(conn, regs)
        driver = RbiStateFinancesDriver(ingestor=ingestor, registries=regs)

        cr = driver.conformance_check()
        if not cr.ok:
            print("Driver conformance failed:")
            for f in cr.failures:
                print(f"  [FAIL] {f}")
            return 2

        print(f"Ingesting RBI data for states={states} years={years} ...")
        written = driver.fetch(states=states, fiscal_years=years)
        print(f"Done. Rows written: {written:,}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
