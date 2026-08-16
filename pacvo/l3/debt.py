"""Debt and Collateralized Position Manager."""

from pacvo.l3.collateral import CollateralPosition
from pacvo.l3.errors import UndercollateralizedError
from pacvo.l3.fixed import WAD, bps_mul, wad_div, wad_mul


class DebtManager:
    """Manages active debt positions and deterministic liquidation executions."""

    def __init__(self):
        self._positions: dict[str, CollateralPosition] = {}

    def get_position(self, owner: str) -> CollateralPosition | None:
        return self._positions.get(owner.lower())

    def open_or_modify_position(
        self,
        owner: str,
        collateral_symbol: str,
        collateral_delta: int,
        debt_symbol: str,
        debt_delta: int,
        collateral_price: int,
        debt_price: int,
        current_height: int,
    ) -> CollateralPosition:
        """Deposit collateral and/or borrow debt, ensuring minimum collateralization."""
        u = owner.lower()
        pos = self._positions.get(u)
        if pos is None:
            pos = CollateralPosition(
                owner=u,
                collateral_symbol=collateral_symbol,
                collateral_amount=0,
                debt_symbol=debt_symbol,
                debt_amount=0,
                last_update_height=current_height,
            )
            self._positions[u] = pos

        pos.collateral_amount += collateral_delta
        pos.debt_amount += debt_delta
        pos.last_update_height = current_height

        if pos.debt_amount > 0:
            collateral_val = wad_mul(pos.collateral_amount, collateral_price)
            debt_val = wad_mul(pos.debt_amount, debt_price)
            req_collateral = bps_mul(debt_val, pos.min_collateral_ratio_bps)
            if collateral_val < req_collateral:
                raise UndercollateralizedError(
                    f"Position undercollateralized: value {collateral_val} < required {req_collateral}"
                )

        return pos

    def repay_debt(self, owner: str, repay_amount: int) -> int:
        """Repay principal debt, reducing borrower obligation."""
        u = owner.lower()
        pos = self._positions.get(u)
        if pos is None or pos.debt_amount <= 0:
            return 0
        actual_repaid = min(pos.debt_amount, repay_amount)
        pos.debt_amount -= actual_repaid
        return actual_repaid

    def liquidate(
        self,
        liquidator: str,
        borrower: str,
        debt_to_cover: int,
        collateral_price: int,
        debt_price: int,
    ) -> tuple[int, int]:
        """Liquidate undercollateralized debt position.
        
        Returns: (repaid_debt, seized_collateral)
        """
        u = borrower.lower()
        pos = self._positions.get(u)
        if pos is None or not pos.is_liquidatable(collateral_price, debt_price):
            raise UndercollateralizedError("Position is healthy or does not exist; cannot liquidate")

        # Allow liquidating up to 50% of debt in a single call (close factor)
        max_liquidatable = max(1, pos.debt_amount // 2)
        actual_debt = min(max_liquidatable, debt_to_cover)

        # Seized collateral value = debt_value * (1 + bonus)
        debt_val = wad_mul(actual_debt, debt_price)
        bonus_val = bps_mul(debt_val, pos.liquidation_bonus_bps)
        total_seized_val = debt_val + bonus_val

        seized_collateral = wad_div(total_seized_val, collateral_price)
        seized_collateral = min(seized_collateral, pos.collateral_amount)

        pos.debt_amount -= actual_debt
        pos.collateral_amount -= seized_collateral

        return actual_debt, seized_collateral

    def to_dict(self) -> dict:
        return {k: self._positions[k].to_dict() for k in sorted(self._positions.keys())}

    @classmethod
    def from_dict(cls, d: dict) -> "DebtManager":
        mgr = cls()
        for k, v in d.items():
            mgr._positions[k.lower()] = CollateralPosition.from_dict(v)
        return mgr
