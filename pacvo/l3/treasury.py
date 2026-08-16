"""Protocol Treasury Accounting and Liquidity Custody."""

from dataclasses import dataclass, field
from pacvo.l3.errors import InvariantViolationError


@dataclass
class TreasuryManager:
    """Tracks protocol fee reserves, bond obligations, and liquidity custody."""

    balances: dict[str, int] = field(default_factory=dict)
    collected_fees: dict[str, int] = field(default_factory=dict)
    bond_obligations: dict[str, int] = field(default_factory=dict)
    dividend_obligations: dict[str, int] = field(default_factory=dict)

    def deposit(self, symbol: str, amount: int) -> int:
        if amount <= 0:
            return 0
        sym = symbol.upper()
        self.balances[sym] = self.balances.get(sym, 0) + amount
        return self.balances[sym]

    def withdraw(self, symbol: str, amount: int) -> int:
        sym = symbol.upper()
        current = self.balances.get(sym, 0)
        if amount > current:
            raise InvariantViolationError(f"Treasury withdrawal of {amount} exceeds available balance {current}")
        self.balances[sym] = current - amount
        return amount

    def credit_fee(self, symbol: str, fee_amount: int) -> None:
        sym = symbol.upper()
        self.collected_fees[sym] = self.collected_fees.get(sym, 0) + fee_amount
        self.balances[sym] = self.balances.get(sym, 0) + fee_amount

    def get_balance(self, symbol: str) -> int:
        return self.balances.get(symbol.upper(), 0)

    def to_dict(self) -> dict:
        return {
            "balances": {k: str(v) for k, v in sorted(self.balances.items())},
            "collected_fees": {k: str(v) for k, v in sorted(self.collected_fees.items())},
            "bond_obligations": {k: str(v) for k, v in sorted(self.bond_obligations.items())},
            "dividend_obligations": {k: str(v) for k, v in sorted(self.dividend_obligations.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TreasuryManager":
        tm = cls()
        tm.balances = {k: int(v) for k, v in d.get("balances", {}).items()}
        tm.collected_fees = {k: int(v) for k, v in d.get("collected_fees", {}).items()}
        tm.bond_obligations = {k: int(v) for k, v in d.get("bond_obligations", {}).items()}
        tm.dividend_obligations = {k: int(v) for k, v in d.get("dividend_obligations", {}).items()}
        return tm
