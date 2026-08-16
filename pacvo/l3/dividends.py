"""Scalable Cumulative-Per-Share Dividend Distribution System."""

from dataclasses import dataclass, field
from pacvo.l3.errors import InvariantViolationError, UnauthorizedError
from pacvo.l3.fixed import WAD, wad_div, wad_mul


@dataclass
class DividendPool:
    """Tracks global cumulative dividend distributions for a specific equity."""

    symbol: str
    total_distributed: int = 0
    cumulative_dividend_per_share: int = 0  # In WAD scale
    user_entry_indices: dict[str, int] = field(default_factory=dict)
    claimed_by_user: dict[str, int] = field(default_factory=dict)

    def declare_dividend(self, payout_amount: int, total_supply: int) -> int:
        """Declare a new dividend payout across all outstanding shares."""
        if total_supply <= 0 or payout_amount <= 0:
            return 0
        delta_index = wad_div(payout_amount, total_supply)
        self.cumulative_dividend_per_share += delta_index
        self.total_distributed += payout_amount
        return delta_index

    def calculate_claimable(self, user: str, balance: int) -> int:
        """Calculate claimable dividend balance for an individual holder."""
        u = user.lower()
        last_index = self.user_entry_indices.get(u, 0)
        if self.cumulative_dividend_per_share <= last_index or balance <= 0:
            return 0
        delta = self.cumulative_dividend_per_share - last_index
        return wad_mul(balance, delta)

    def claim(self, user: str, balance: int) -> int:
        """Process claim for a holder and update their entry index."""
        u = user.lower()
        amount = self.calculate_claimable(u, balance)
        self.user_entry_indices[u] = self.cumulative_dividend_per_share
        self.claimed_by_user[u] = self.claimed_by_user.get(u, 0) + amount
        return amount

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_distributed": str(self.total_distributed),
            "cumulative_dividend_per_share": str(self.cumulative_dividend_per_share),
            "user_entry_indices": {k: str(v) for k, v in sorted(self.user_entry_indices.items())},
            "claimed_by_user": {k: str(v) for k, v in sorted(self.claimed_by_user.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DividendPool":
        return cls(
            symbol=d["symbol"],
            total_distributed=int(d["total_distributed"]),
            cumulative_dividend_per_share=int(d["cumulative_dividend_per_share"]),
            user_entry_indices={k: int(v) for k, v in d.get("user_entry_indices", {}).items()},
            claimed_by_user={k: int(v) for k, v in d.get("claimed_by_user", {}).items()},
        )
