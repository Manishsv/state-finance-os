"""RBI State Finances driver — reads the e-STATES database XLSX.

Source: RBI publication "State Finances: A Study of Budgets",
e-STATES database (annual XLSX). See data/raw/rbi/manifest.json for
provenance of cached files.

Mapping decisions:
- Each Appendix maps to one account_type:
    Appendix-1 -> revenue_receipt  (revenue receipts breakdown)
    Appendix-2 -> revenue_exp      (revenue expenditure by function)
    Appendix-3 -> capital_receipt  (capital receipts / borrowings)
    Appendix-4 -> capital_exp      (capital outlay by function)
- Each (appendix, head) pair maps to a synthetic 9XXX major_head_code,
  pre-allocated by financeos.drivers.connectors.rbi.bootstrap.
- Year format YYYY-YYYY -> YYYY-YY.
- Three columns (Account, Revised, Budget) -> three rows with
  estimate_type ACT, RE, BE respectively. Null columns are skipped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import openpyxl

from financeos.drivers.connectors.base import (
    BudgetDataSourceDriver,
    DriverFetchError,
)
from financeos.drivers.registries.loader import Registries, load_registries
from financeos.drivers.store.ingestor import Ingestor
from financeos.os.conformance import BudgetSignalRow, ConformanceResult

# RBI's state names -> ISO 3166-2:IN codes used by FinanceOS.
# "All States/UT" is excluded — it's an aggregate, not a state.
RBI_STATE_NAME_TO_CODE: Dict[str, str] = {
    "Andhra Pradesh": "AP",
    "Arunachal Pradesh": "AR",
    "Assam": "AS",
    "Bihar": "BR",
    "Chhattisgarh": "CT",
    "Goa": "GA",
    "Gujarat": "GJ",
    "Haryana": "HR",
    "Himachal Pradesh": "HP",
    "Jammu and Kashmir": "JK",
    "Jharkhand": "JH",
    "Karnataka": "KA",
    "Kerala": "KL",
    "Madhya Pradesh": "MP",
    "Maharashtra": "MH",
    "Manipur": "MN",
    "Meghalaya": "ML",
    "Mizoram": "MZ",
    "NCT Delhi": "DL",
    "Nagaland": "NL",
    "Odisha": "OR",
    "Puducherry": "PY",
    "Punjab": "PB",
    "Rajasthan": "RJ",
    "Sikkim": "SK",
    "Tamil Nadu": "TN",
    "Telangana": "TG",
    "Tripura": "TR",
    "Uttar Pradesh": "UP",
    "Uttarakhand": "UT",
    "West Bengal": "WB",
}

APPENDIX_TO_ACCOUNT_TYPE: Dict[str, str] = {
    "Appendix-1": "revenue_receipt",
    "Appendix-2": "revenue_exp",
    "Appendix-3": "capital_receipt",
    "Appendix-4": "capital_exp",
}

# 0-based column index in the XLSX 'Data' sheet
ESTIMATE_COL_INDEX: Dict[str, int] = {
    "ACT": 4,  # "Account"
    "RE": 5,   # "Revised"
    "BE": 6,   # "Budget"
}

DEFAULT_XLSX_PATH = Path("data/raw/rbi/estates_2025-26.xlsx")
EDITION = "2025-26"
DATA_CONFIDENCE = 0.95  # directly from RBI's published machine-readable DB


def convert_fiscal_year(rbi_year: str) -> str:
    """Convert RBI 'YYYY-YYYY' to FinanceOS 'YYYY-YY'."""
    parts = rbi_year.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 4:
        raise ValueError(f"Bad RBI fiscal year: {rbi_year!r}")
    return f"{parts[0]}-{parts[1][-2:]}"


def fy_financeos_to_rbi(fy: str) -> str:
    """Inverse: 'YYYY-YY' -> 'YYYY-YYYY'."""
    yyyy, _ = fy.split("-")
    return f"{yyyy}-{int(yyyy) + 1}"


class RbiStateFinancesDriver(BudgetDataSourceDriver):
    domain = "rbi_estates"
    cadence_hours = 8760.0  # annual
    produces_assessments = False
    signal_names = ["amount"]
    estimate_types = ["BE", "RE", "ACT"]
    data_sources = ["RBI State Finances: A Study of Budgets, e-STATES database"]
    source_id_template = "rbi.estates." + EDITION + ".{estimate_type}"

    def __init__(
        self,
        ingestor: Ingestor,
        xlsx_path: Optional[Path] = None,
        registries: Optional[Registries] = None,
    ):
        self.ingestor = ingestor
        self.xlsx_path = Path(xlsx_path) if xlsx_path else DEFAULT_XLSX_PATH
        self.registries = registries or load_registries()
        self._head_to_code = self._build_head_lookup()

    def _build_head_lookup(self) -> Dict[Tuple[str, str], str]:
        """Map (appendix, head) -> synthetic major_head_code from registry.

        The registry must have been bootstrapped first.
        """
        lookup: Dict[Tuple[str, str], str] = {}
        registry_path = (
            Path(__file__).resolve().parent.parent.parent
            / "registries" / "major_heads.json"
        )
        data = json.loads(registry_path.read_text())
        for entry in data["major_heads"]:
            ap = entry.get("rbi_appendix")
            head = entry.get("rbi_head")
            if ap and head:
                lookup[(ap, head)] = entry["code"]
        return lookup

    def conformance_check(self) -> ConformanceResult:
        failures: List[str] = []
        warnings: List[str] = []
        if not self.xlsx_path.exists():
            failures.append(
                f"RBI XLSX not found at {self.xlsx_path}. "
                "Cache the file first (download script in data/raw/rbi/)."
            )
        if not self._head_to_code:
            failures.append(
                "Registry has no RBI head mappings. Run "
                "`python -m financeos.drivers.connectors.rbi.bootstrap` first."
            )
        return ConformanceResult(ok=not failures, failures=failures, warnings=warnings)

    def fetch(
        self,
        states: Sequence[str],
        fiscal_years: Sequence[str],
        force: bool = False,
    ) -> int:
        if not self.xlsx_path.exists():
            raise DriverFetchError(f"RBI XLSX missing: {self.xlsx_path}")

        wanted_states = set(states)
        wanted_fy_rbi = {fy_financeos_to_rbi(fy) for fy in fiscal_years}

        batches: Dict[str, List[BudgetSignalRow]] = {"BE": [], "RE": [], "ACT": []}
        skipped_unknown_head = 0

        wb = openpyxl.load_workbook(self.xlsx_path, read_only=True, data_only=True)
        try:
            ws = wb["Data"]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[0] is None:
                    break
                ap = str(row[0])
                state_name = row[1]
                head = str(row[2])
                rbi_year = str(row[3])

                state_code = RBI_STATE_NAME_TO_CODE.get(state_name)
                if state_code is None or state_code not in wanted_states:
                    continue
                if rbi_year not in wanted_fy_rbi:
                    continue

                account_type = APPENDIX_TO_ACCOUNT_TYPE.get(ap)
                if account_type is None:
                    continue

                code = self._head_to_code.get((ap, head))
                if code is None:
                    skipped_unknown_head += 1
                    continue

                fiscal_year = convert_fiscal_year(rbi_year)

                for et, col_idx in ESTIMATE_COL_INDEX.items():
                    raw = row[col_idx]
                    if raw is None or raw == "":
                        continue
                    try:
                        value = float(str(raw).replace(",", ""))
                    except (TypeError, ValueError):
                        continue
                    batches[et].append(BudgetSignalRow(
                        state=state_code,
                        fiscal_year=fiscal_year,
                        major_head_code=code,
                        account_type=account_type,
                        signal="amount",
                        estimate_type=et,
                        value=value,
                        unit="INR_CRORE",
                        data_confidence=DATA_CONFIDENCE,
                        source_id=self.source_id_template.format(estimate_type=et),
                    ))
        finally:
            wb.close()

        if skipped_unknown_head:
            print(
                f"  [{self.domain}] skipped {skipped_unknown_head} rows with "
                f"unknown (appendix, head) — re-run bootstrap if XLSX changed."
            )

        total_written = 0
        for et, rows in batches.items():
            if not rows:
                continue
            written, result = self.ingestor.write_signals(
                rows,
                domain=self.domain,
                declared_signal_names=self.signal_names,
                declared_estimate_types=[et],
                states_in_batch=sorted({r.state for r in rows}),
                fiscal_years_in_batch=sorted({r.fiscal_year for r in rows}),
            )
            if not result.ok:
                print(f"  [{self.domain}] {et}: BLOCKED — {result.failures}")
            total_written += written
        return total_written
