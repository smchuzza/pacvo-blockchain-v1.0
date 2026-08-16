"""Collateralized Debt Positions and Liquidation Engine."""

from dataclasses import dataclass
from typing import Optional

from pacvo.l3.errors import UndercollateralizedError
from pacvo.l3.fixed import WAD, bps_mul, wad_div, wad_mul


@dataclass
class CollateralPosition:
    """Represents a borrower's collateralized debt position."""

    owner: str
    collateral_symbol: str
    collateral_amount: int
    debt_symbol: str
    debt_amount: int
    min_collateral_ratio_bps: int = 15000  # 150.00% initial collateral ratio
    liquidation_threshold_bps: int = 12000 # 120.00% liquidation trigger
    liquidation_bonus_bps: int = 500       # 5.00% liquidator incentive bonus
    last_update_height: int = 0

    def calculate_health_factor(self, collateral_price: int, debt_price: int) -> int:
        """Calculate health factor in WAD scale: (collateral_val * liq_threshold) / debt_val."""
        if self.debt_amount <= 0:
            return 100 * WAD # Infinite health
        collateral_val = wad_mul(self.collateral_amount, collateral_price)
        debt_val = wad_mul(self.debt_amount, debt_price)
        if debt_val <= 0:
            return 100 * WAD

        # Collateral adjusted by liquidation threshold: (collateral_val * liquidation_threshold_bps) / 10000
        adj_collateral = bps_mul(collateral_val, self.liquidation_threshold_bps)
        return wad_div(adj_collateral, debt_val)

    def is_liquidatable(self, collateral_price: int, debt_price: int) -> bool:
        """Position is subject to liquidation if health factor < 1.0 WAD."""
        if self.debt_amount <= 0:
            return False
        return self.calculate_health_factor(collateral_price, debt_price) < WAD

    def to_dict(self) -> dict:
        return {
            "owner": self.owner,
            "collateral_symbol": self.collateral_symbol,
            "collateral_amount": str(self.collateral_amount),
            "debt_symbol": self.debt_symbol,
            "debt_amount": str(self.debt_amount),
            "min_collateral_ratio_bps": self.min_collateral_ratio_bps,
            "liquidation_threshold_bps": self.liquidation_threshold_bps,
            "liquidation_bonus_bps": self.liquidation_bonus_bps,
            "last_update_height": self.last_update_height,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CollateralPosition":
        return cls(
            owner=d["owner"],
            collateral_symbol=d["collateral_symbol"],
            collateral_amount=int(d["collateral_amount"]),
            debt_symbol=d["debt_symbol"],
            debt_amount=int(d["debt_amount"]),
            min_collateral_ratio_bps=int(d.get("min_collateral_ratio_bps", 15000)),
            liquidation_threshold_bps=int(d.get("liquidation_threshold_bps", 12000)),
            liquidation_bonus_bps=int(d.get("liquidation_bonus_bps", 500)),
            last_update_height=int(d.get("last_update_height", 0)),
        )
