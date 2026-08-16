"""Tokenized Simulated Equity Asset Protocol."""

from dataclasses import dataclass
from typing import Optional

from pacvo.l3.asset import Asset, AssetType
from pacvo.l3.dividends import DividendPool


@dataclass
class EquityAsset:
    """Tokenized Equity holding simulated corporate shares and dividend distributions."""

    asset: Asset
    dividend_pool: DividendPool

    @classmethod
    def create(
        cls,
        symbol: str,
        name: str,
        token_address: str,
        issuer: str,
        total_supply: int,
        creation_height: int = 0,
    ) -> "EquityAsset":
        asset = Asset(
            symbol=symbol,
            name=name,
            token_address=token_address,
            asset_type=AssetType.EQUITY,
            issuer=issuer,
            total_supply=total_supply,
            max_supply=total_supply,
            creation_height=creation_height,
        )
        pool = DividendPool(symbol=symbol)
        return cls(asset=asset, dividend_pool=pool)

    def declare_dividend(self, payout_amount: int) -> int:
        return self.dividend_pool.declare_dividend(payout_amount, self.asset.total_supply)

    def claim_dividend(self, user: str, balance: int) -> int:
        return self.dividend_pool.claim(user, balance)

    def to_dict(self) -> dict:
        return {
            "asset": self.asset.to_dict(),
            "dividend_pool": self.dividend_pool.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EquityAsset":
        return cls(
            asset=Asset.from_dict(d["asset"]),
            dividend_pool=DividendPool.from_dict(d["dividend_pool"]),
        )
