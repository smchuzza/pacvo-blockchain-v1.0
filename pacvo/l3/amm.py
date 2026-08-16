"""Constant-Product Automated Market Maker (AMM) Engine."""

from dataclasses import dataclass, field
from pacvo.l3.errors import DivisionByZeroError, InvariantViolationError, SlippageExceededError
from pacvo.l3.fixed import PERCENT_BPS, WAD, bps_mul, isqrt, wad_div, wad_mul


@dataclass
class ConstantProductAMM:
    """Pair Market with x * y = k invariant and fixed-point fee accounting."""

    pair_id: str
    token_a: str
    token_b: str
    reserve_a: int = 0
    reserve_b: int = 0
    total_lp_shares: int = 0
    fee_bps: int = 30  # 0.30% standard AMM swap fee
    lp_balances: dict[str, int] = field(default_factory=dict)

    def add_liquidity(self, provider: str, amount_a: int, amount_b: int) -> int:
        """Add liquidity to the AMM pool and mint LP tokens."""
        if amount_a <= 0 or amount_b <= 0:
            raise InvariantViolationError("Deposit amounts must be strictly positive")
        p = provider.lower()

        if self.total_lp_shares == 0 or self.reserve_a == 0 or self.reserve_b == 0:
            shares_minted = isqrt(amount_a * amount_b)
        else:
            share_a = (amount_a * self.total_lp_shares) // self.reserve_a
            share_b = (amount_b * self.total_lp_shares) // self.reserve_b
            shares_minted = min(share_a, share_b)

        if shares_minted <= 0:
            raise InvariantViolationError("Insufficient liquidity deposited to mint shares")

        self.reserve_a += amount_a
        self.reserve_b += amount_b
        self.total_lp_shares += shares_minted
        self.lp_balances[p] = self.lp_balances.get(p, 0) + shares_minted
        return shares_minted

    def remove_liquidity(self, provider: str, shares_to_burn: int) -> tuple[int, int]:
        """Burn LP shares and return proportional reserves."""
        p = provider.lower()
        user_shares = self.lp_balances.get(p, 0)
        actual_shares = min(user_shares, shares_to_burn)
        if actual_shares <= 0 or self.total_lp_shares <= 0:
            return 0, 0

        amount_a = (actual_shares * self.reserve_a) // self.total_lp_shares
        amount_b = (actual_shares * self.reserve_b) // self.total_lp_shares

        self.lp_balances[p] -= actual_shares
        self.total_lp_shares -= actual_shares
        self.reserve_a -= amount_a
        self.reserve_b -= amount_b
        return amount_a, amount_b

    def get_amount_out(self, amount_in: int, token_in: str) -> int:
        """Calculate swap output amount given input token and amount."""
        if amount_in <= 0:
            return 0
        is_a_in = token_in.upper() == self.token_a.upper()
        res_in = self.reserve_a if is_a_in else self.reserve_b
        res_out = self.reserve_b if is_a_in else self.reserve_a

        if res_in <= 0 or res_out <= 0:
            return 0

        # Amount after fee: amount_in * (10000 - fee_bps)
        amount_in_with_fee = amount_in * (PERCENT_BPS - self.fee_bps)
        numerator = amount_in_with_fee * res_out
        denominator = (res_in * PERCENT_BPS) + amount_in_with_fee
        if denominator <= 0:
            return 0
        return numerator // denominator

    def swap(self, user: str, amount_in: int, token_in: str, min_amount_out: int = 0) -> int:
        """Execute a swap, updating reserves atomically with slippage protection."""
        amount_out = self.get_amount_out(amount_in, token_in)
        if amount_out <= 0:
            raise InvariantViolationError("Calculated output is zero; swap aborted")
        if amount_out < min_amount_out:
            raise SlippageExceededError(
                f"Slippage limit breached: output {amount_out} < min_required {min_amount_out}"
            )

        is_a_in = token_in.upper() == self.token_a.upper()
        if is_a_in:
            self.reserve_a += amount_in
            self.reserve_b -= amount_out
        else:
            self.reserve_b += amount_in
            self.reserve_a -= amount_out

        return amount_out

    def get_spot_price_a_in_b(self) -> int:
        """Return the current spot price of Token A in terms of Token B in WAD."""
        if self.reserve_a <= 0 or self.reserve_b <= 0:
            return WAD
        return wad_div(self.reserve_b, self.reserve_a)

    def to_dict(self) -> dict:
        return {
            "pair_id": self.pair_id,
            "token_a": self.token_a,
            "token_b": self.token_b,
            "reserve_a": str(self.reserve_a),
            "reserve_b": str(self.reserve_b),
            "total_lp_shares": str(self.total_lp_shares),
            "fee_bps": self.fee_bps,
            "spot_price": str(self.get_spot_price_a_in_b()),
            "lp_balances": {k: str(v) for k, v in sorted(self.lp_balances.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConstantProductAMM":
        return cls(
            pair_id=d["pair_id"],
            token_a=d["token_a"],
            token_b=d["token_b"],
            reserve_a=int(d["reserve_a"]),
            reserve_b=int(d["reserve_b"]),
            total_lp_shares=int(d["total_lp_shares"]),
            fee_bps=int(d.get("fee_bps", 30)),
            lp_balances={k: int(v) for k, v in d.get("lp_balances", {}).items()},
        )
