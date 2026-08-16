"""Lending Protocol and Utilization-Based Dynamic Interest Engine."""

from dataclasses import dataclass, field
from pacvo.l3.errors import InvariantViolationError
from pacvo.l3.fixed import WAD, bps_mul, wad_div, wad_mul


@dataclass
class LendingPool:
    """Represents a decentralized liquidity pool with dynamic borrow interest rates."""

    symbol: str
    total_supplied: int = 0
    total_borrowed: int = 0
    total_shares: int = 0
    base_rate_bps: int = 200     # 2.00% base interest rate
    slope_rate_bps: int = 1000   # 10.00% slope multiplier at 100% utilization
    user_shares: dict[str, int] = field(default_factory=dict)
    user_borrows: dict[str, int] = field(default_factory=dict)

    def calculate_utilization(self) -> int:
        """Returns utilization U in WAD scale (e.g. 0.80 * 10^18 for 80%)."""
        if self.total_supplied <= 0:
            return 0
        return wad_div(self.total_borrowed, self.total_supplied)

    def calculate_borrow_rate_bps(self) -> int:
        """Dynamic interest rate: base_rate + utilization * slope_rate."""
        u = self.calculate_utilization()
        slope_contribution = (u * self.slope_rate_bps) // WAD
        return self.base_rate_bps + slope_contribution

    def deposit(self, user: str, amount: int) -> int:
        """Deposit assets into the lending pool and receive LP shares."""
        if amount <= 0:
            return 0
        u = user.lower()
        if self.total_shares == 0 or self.total_supplied == 0:
            shares_minted = amount
        else:
            shares_minted = wad_div(wad_mul(amount, self.total_shares), self.total_supplied)

        self.total_supplied += amount
        self.total_shares += shares_minted
        self.user_shares[u] = self.user_shares.get(u, 0) + shares_minted
        return shares_minted

    def withdraw(self, user: str, shares: int) -> int:
        """Burn LP shares and withdraw underlying liquidity."""
        u = user.lower()
        user_bal = self.user_shares.get(u, 0)
        actual_shares = min(user_bal, shares)
        if actual_shares <= 0 or self.total_shares <= 0:
            return 0

        underlying_amount = wad_div(wad_mul(actual_shares, self.total_supplied), self.total_shares)
        available_cash = self.total_supplied - self.total_borrowed
        if underlying_amount > available_cash:
            raise InvariantViolationError("Insufficient available liquidity in pool to withdraw")

        self.user_shares[u] -= actual_shares
        self.total_shares -= actual_shares
        self.total_supplied -= underlying_amount
        return underlying_amount

    def borrow(self, user: str, amount: int) -> int:
        """Borrow liquidity from the pool."""
        available_cash = self.total_supplied - self.total_borrowed
        if amount > available_cash:
            raise InvariantViolationError("Borrow amount exceeds available pool liquidity")
        u = user.lower()
        self.total_borrowed += amount
        self.user_borrows[u] = self.user_borrows.get(u, 0) + amount
        return amount

    def repay(self, user: str, amount: int) -> int:
        """Repay borrowed liquidity."""
        u = user.lower()
        borrowed = self.user_borrows.get(u, 0)
        actual_repay = min(borrowed, amount)
        if actual_repay <= 0:
            return 0
        self.user_borrows[u] -= actual_repay
        self.total_borrowed -= actual_repay
        return actual_repay

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_supplied": str(self.total_supplied),
            "total_borrowed": str(self.total_borrowed),
            "total_shares": str(self.total_shares),
            "base_rate_bps": self.base_rate_bps,
            "slope_rate_bps": self.slope_rate_bps,
            "utilization": str(self.calculate_utilization()),
            "borrow_rate_bps": self.calculate_borrow_rate_bps(),
            "user_shares": {k: str(v) for k, v in sorted(self.user_shares.items())},
            "user_borrows": {k: str(v) for k, v in sorted(self.user_borrows.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LendingPool":
        return cls(
            symbol=d["symbol"],
            total_supplied=int(d["total_supplied"]),
            total_borrowed=int(d["total_borrowed"]),
            total_shares=int(d["total_shares"]),
            base_rate_bps=int(d.get("base_rate_bps", 200)),
            slope_rate_bps=int(d.get("slope_rate_bps", 1000)),
            user_shares={k: int(v) for k, v in d.get("user_shares", {}).items()},
            user_borrows={k: int(v) for k, v in d.get("user_borrows", {}).items()},
        )
