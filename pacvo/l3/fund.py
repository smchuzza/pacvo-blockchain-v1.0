"""Tokenized Basket Index Funds and Net Asset Value (NAV) Calculation."""

from dataclasses import dataclass, field
from pacvo.l3.asset import Asset, AssetType
from pacvo.l3.errors import DivisionByZeroError, InvariantViolationError
from pacvo.l3.fixed import WAD, wad_div, wad_mul


@dataclass
class FundAsset:
    """Tokenized Fund representing an asset basket with deterministic NAV pricing."""

    asset: Asset
    basket_allocations_bps: dict[str, int] = field(default_factory=dict) # e.g. {"PVOA": 5000, "POL": 5000}
    holdings: dict[str, int] = field(default_factory=dict)               # actual asset quantities in custody
    user_shares: dict[str, int] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        symbol: str,
        name: str,
        token_address: str,
        issuer: str,
        allocations_bps: dict[str, int],
        creation_height: int = 0,
    ) -> "FundAsset":
        total_bps = sum(allocations_bps.values())
        if total_bps != 10_000:
            raise InvariantViolationError(f"Basket allocations must sum to 100% (10,000 bps); got {total_bps}")

        asset = Asset(
            symbol=symbol,
            name=name,
            token_address=token_address,
            asset_type=AssetType.FUND,
            issuer=issuer,
            total_supply=0,
            creation_height=creation_height,
        )
        return cls(
            asset=asset,
            basket_allocations_bps=allocations_bps,
            holdings={sym: 0 for sym in allocations_bps},
        )

    def calculate_nav(self, prices_wad: dict[str, int]) -> int:
        """Calculate Net Asset Value (NAV) per share in WAD scale.
        
        NAV = Total Asset Value / Total Shares (default 1.0 WAD if 0 shares).
        """
        if self.asset.total_supply <= 0:
            return WAD # Par value 1.0 WAD on initialization

        total_value = 0
        for sym, qty in self.holdings.items():
            p = prices_wad.get(sym.upper(), WAD)
            total_value += wad_mul(qty, p)

        return wad_div(total_value, self.asset.total_supply)

    def deposit_and_mint(self, user: str, asset_deposits: dict[str, int], prices_wad: dict[str, int]) -> int:
        """Deposit asset basket and mint corresponding fund shares."""
        deposit_val = 0
        for sym, qty in asset_deposits.items():
            if qty > 0:
                p = prices_wad.get(sym.upper(), WAD)
                deposit_val += wad_mul(qty, p)
                self.holdings[sym] = self.holdings.get(sym, 0) + qty

        if deposit_val <= 0:
            return 0

        current_nav = self.calculate_nav(prices_wad)
        shares_to_mint = wad_div(deposit_val, current_nav)
        if shares_to_mint <= 0:
            raise InvariantViolationError("Zero shares minted from deposit")

        u = user.lower()
        self.asset.total_supply += shares_to_mint
        self.user_shares[u] = self.user_shares.get(u, 0) + shares_to_mint
        return shares_to_mint

    def redeem_and_withdraw(self, user: str, shares_to_burn: int, prices_wad: dict[str, int]) -> dict[str, int]:
        """Burn fund shares and return proportional basket assets."""
        u = user.lower()
        user_bal = self.user_shares.get(u, 0)
        actual_shares = min(user_bal, shares_to_burn)
        if actual_shares <= 0 or self.asset.total_supply <= 0:
            return {}

        withdrawn_assets = {}
        for sym, qty in self.holdings.items():
            portion = (actual_shares * qty) // self.asset.total_supply
            self.holdings[sym] -= portion
            withdrawn_assets[sym] = portion

        self.user_shares[u] -= actual_shares
        self.asset.total_supply -= actual_shares
        return withdrawn_assets

    def to_dict(self) -> dict:
        return {
            "asset": self.asset.to_dict(),
            "basket_allocations_bps": self.basket_allocations_bps,
            "holdings": {k: str(v) for k, v in sorted(self.holdings.items())},
            "user_shares": {k: str(v) for k, v in sorted(self.user_shares.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FundAsset":
        return cls(
            asset=Asset.from_dict(d["asset"]),
            basket_allocations_bps=d["basket_allocations_bps"],
            holdings={k: int(v) for k, v in d.get("holdings", {}).items()},
            user_shares={k: int(v) for k, v in d.get("user_shares", {}).items()},
        )
