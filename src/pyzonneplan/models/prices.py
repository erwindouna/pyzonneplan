"""Models for the consumer price chart endpoint."""

from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
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

    def prices_for_day(self, day: date, tz: tzinfo) -> list[PricePoint]:
        """Return the price points that start on the given local day."""
        return [point for point in self.prices if point.start_date.astimezone(tz).date() == day]

    def extreme_price(self, day: date, tz: tzinfo, *, lowest: bool) -> PricePoint | None:
        """Return the cheapest (``lowest=True``) or most expensive price point for a local day."""
        prices = self.prices_for_day(day, tz)
        if not prices:
            return None

        def _amount(point: PricePoint) -> int:
            return point.price_tax_included.amount

        return min(prices, key=_amount) if lowest else max(prices, key=_amount)

    def price_block(self, day: date, tz: tzinfo, *, lowest: bool, deviation: Decimal = Decimal("0.05")) -> tuple[PricePoint, PricePoint] | None:
        """Return the bounds of the contiguous block of hours around the day's extreme price."""
        extreme = self.extreme_price(day, tz, lowest=lowest)
        if extreme is None:
            return None

        prices = sorted(self.prices_for_day(day, tz), key=lambda point: point.start_date)
        extreme_amount = extreme.price_tax_included.amount
        max_deviation = abs(extreme_amount) * deviation

        def _in_block(point: PricePoint) -> bool:
            return abs(point.price_tax_included.amount - extreme_amount) <= max_deviation

        index = prices.index(extreme)
        start = end = index
        while start > 0 and _in_block(prices[start - 1]):
            start -= 1
        while end < len(prices) - 1 and _in_block(prices[end + 1]):
            end += 1

        return prices[start], prices[end]
