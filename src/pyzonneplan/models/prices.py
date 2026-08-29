"""Models for the consumer price chart endpoint."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from mashumaro.mixins.orjson import DataClassORJSONMixin

from pyzonneplan.const import MONEY_FACTOR


@dataclass
class Money(DataClassORJSONMixin):
    """A monetary amount, expressed in the API's 1e-7 EUR unit."""

    amount: int

    @property
    def euro(self) -> Decimal:
        """Return the amount converted to euro."""
        return Decimal(self.amount) * MONEY_FACTOR


@dataclass
class SustainabilityScore(DataClassORJSONMixin):
    """How sustainable the energy is during a price interval, in permille."""

    permille: int

    @property
    def fraction(self) -> Decimal:
        """Return the score as a fraction between 0 and 1."""
        return Decimal(self.permille) / 1000


@dataclass
class PricePoint(DataClassORJSONMixin):
    """A single price interval within a chart.

    ``tariff_group`` is only present on the hourly electricity chart, not on
    the quarter-hourly one.
    """

    start_date: datetime
    end_date: datetime
    price_tax_included: Money
    price_tax_excluded: Money
    sustainability_score: SustainabilityScore | None = None
    tariff_group: str | None = None


@dataclass
class PriceRange(DataClassORJSONMixin):
    """The date range a chart covers."""

    start_date: datetime
    end_date: datetime


@dataclass
class PriceSeries(DataClassORJSONMixin):
    """The list of price points making up a chart."""

    prices: list[PricePoint] = field(default_factory=list)


@dataclass
class PriceChartData(DataClassORJSONMixin):
    """The ``chart`` object of a consumer price chart response."""

    range: PriceRange
    series: PriceSeries


@dataclass
class ConsumerPrices(DataClassORJSONMixin):
    """Response of /api/consumer-prices/charts/{chart_name}."""

    chart: PriceChartData

    @property
    def prices(self) -> list[PricePoint]:
        """Return the price points, flattened out of the nested chart/series structure."""
        return self.chart.series.prices
