"""L3 Market Registry and Order Routing Engine."""

from typing import Optional
from pacvo.l3.amm import ConstantProductAMM
from pacvo.l3.errors import InvariantViolationError


class MarketManager:
    """Manages all registered AMM liquidity pairs in the PVO-Fi economy."""

    def __init__(self):
        self._markets: dict[str, ConstantProductAMM] = {}

    @staticmethod
    def format_pair_id(token_a: str, token_b: str) -> str:
        t_a, t_b = sorted([token_a.upper(), token_b.upper()])
        return f"{t_a}/{t_b}"

    def create_market(self, token_a: str, token_b: str, fee_bps: int = 30) -> ConstantProductAMM:
        pair_id = self.format_pair_id(token_a, token_b)
        if pair_id in self._markets:
            return self._markets[pair_id]
        t_a, t_b = sorted([token_a.upper(), token_b.upper()])
        market = ConstantProductAMM(
            pair_id=pair_id,
            token_a=t_a,
            token_b=t_b,
            fee_bps=fee_bps,
        )
        self._markets[pair_id] = market
        return market

    def get_market(self, token_a: str, token_b: str) -> Optional[ConstantProductAMM]:
        pair_id = self.format_pair_id(token_a, token_b)
        return self._markets.get(pair_id)

    def list_markets(self) -> list[ConstantProductAMM]:
        return sorted(self._markets.values(), key=lambda m: m.pair_id)

    def to_dict(self) -> dict:
        return {k: self._markets[k].to_dict() for k in sorted(self._markets.keys())}

    @classmethod
    def from_dict(cls, d: dict) -> "MarketManager":
        mgr = cls()
        for k, v in d.items():
            mgr._markets[k] = ConstantProductAMM.from_dict(v)
        return mgr
