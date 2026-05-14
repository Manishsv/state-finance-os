"""Abstract base for budget data source drivers (DRIVER_INTERFACE.md)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Sequence

from financeos.os.conformance import ConformanceResult


class BudgetDataSourceDriver(ABC):
    """The contract every FinanceOS data source driver must implement.

    Identity fields are class attributes (static, never change between calls).
    Runtime operations are methods. See spec/DRIVER_INTERFACE.md.
    """

    domain: str
    cadence_hours: float
    produces_assessments: bool
    signal_names: List[str]
    estimate_types: List[str]
    data_sources: List[str]
    source_id_template: str

    @abstractmethod
    def fetch(
        self,
        states: Sequence[str],
        fiscal_years: Sequence[str],
        force: bool = False,
    ) -> int:
        """Pull from upstream, map to cells, write via Ingestor. Returns rows written.

        Returns -1 if skipped due to cadence watermark (force=False only).
        Raises DriverFetchError on unrecoverable failure (after retries).
        """
        ...

    @abstractmethod
    def conformance_check(self) -> ConformanceResult:
        """Static load-time validation. No network calls. Under 2 seconds."""
        ...


class DriverFetchError(Exception):
    """Raised by a driver when fetch fails unrecoverably after retries."""
