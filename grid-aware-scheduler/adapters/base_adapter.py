"""
Base interface for all market adapters.

Every market (GB, CAISO, ERCOT, etc.) gets its own adapter file that
implements this interface. The scheduler NEVER talks to a market's API
directly - it only ever calls methods on a MarketAdapter and receives
data in the common format defined below.

Do not add market-specific logic anywhere outside an adapter file.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class GridDataPoint:
    """Common format every adapter must return. Nothing else in the
    system should need to know which market this data came from."""
    timestamp: datetime
    carbon_intensity: float  # gCO2/kWh
    price: float             # currency/MWh, in the market's native currency


class MarketAdapter(ABC):
    """Every market adapter must implement this interface."""

    @abstractmethod
    def get_data(self, start: datetime, end: datetime) -> list[GridDataPoint]:
        """Return carbon intensity and price data for the given range,
        translated into the common GridDataPoint format."""
        raise NotImplementedError

    @property
    @abstractmethod
    def market_name(self) -> str:
        """Short identifier, e.g. 'GB', 'CAISO', 'ERCOT'."""
        raise NotImplementedError
