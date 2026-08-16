"""Deterministic Internal Price Evaluation Engine."""

from pacvo.l3.fixed import WAD, wad_div, wad_mul


class PriceEngine:
    """Provides deterministic valuation for simulated L3 assets."""

    def __init__(self, base_currency: str = "POL"):
        self.base_currency = base_currency
        self._reference_prices: dict[str, int] = {
            base_currency.upper(): WAD, # Base currency is always 1.0 WAD
        }

    def set_reference_price(self, symbol: str, price_in_base_wad: int) -> None:
        """Set or update internal reference price."""
        self._reference_prices[symbol.upper()] = price_in_base_wad

    def get_price(self, symbol: str) -> int:
        """Return the current spot/reference price in WAD scale."""
        sym = symbol.upper()
        if sym in self._reference_prices:
            return self._reference_prices[sym]
        # Default unconfigured assets to par value (1.0 WAD)
        return WAD

    def get_value(self, symbol: str, amount: int) -> int:
        """Return total valuation in base currency: wad_mul(amount, price)."""
        price = self.get_price(symbol)
        return wad_mul(amount, price)

    def to_dict(self) -> dict:
        return {
            "base_currency": self.base_currency,
            "prices": {k: str(v) for k, v in sorted(self._reference_prices.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PriceEngine":
        pe = cls(base_currency=d.get("base_currency", "POL"))
        pe._reference_prices = {k: int(v) for k, v in d.get("prices", {}).items()}
        return pe
