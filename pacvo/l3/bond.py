"""Tokenized Fixed-Income Bond and Coupon Accounting Engine."""

from dataclasses import dataclass, field
from typing import Optional

from pacvo.l3.asset import Asset, AssetType
from pacvo.l3.errors import MaturityError
from pacvo.l3.fixed import bps_mul, wad_mul


@dataclass
class BondAsset:
    """Tokenized Fixed-Income Bond with periodic coupon schedules and principal redemption."""

    asset: Asset
    face_value: int              # Par value per bond unit (e.g. 1000 WAD)
    coupon_rate_bps: int         # Annualized coupon rate in bps (e.g. 500 = 5.00%)
    coupon_interval_blocks: int  # Blocks between coupon distributions (e.g. 100 blocks)
    issue_height: int
    maturity_height: int
    user_last_claim_height: dict[str, int] = field(default_factory=dict)
    redeemed_principal: dict[str, int] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        symbol: str,
        name: str,
        token_address: str,
        issuer: str,
        total_supply: int,
        face_value: int,
        coupon_rate_bps: int,
        coupon_interval_blocks: int,
        issue_height: int,
        maturity_height: int,
    ) -> "BondAsset":
        asset = Asset(
            symbol=symbol,
            name=name,
            token_address=token_address,
            asset_type=AssetType.BOND,
            issuer=issuer,
            total_supply=total_supply,
            max_supply=total_supply,
            creation_height=issue_height,
            maturity_height=maturity_height,
            coupon_rate_bps=coupon_rate_bps,
        )
        return cls(
            asset=asset,
            face_value=face_value,
            coupon_rate_bps=coupon_rate_bps,
            coupon_interval_blocks=coupon_interval_blocks,
            issue_height=issue_height,
            maturity_height=maturity_height,
        )

    def calculate_claimable_coupons(self, user: str, balance: int, current_height: int) -> int:
        """Calculate uncollected coupon yield based on elapsed block intervals."""
        if balance <= 0 or current_height <= self.issue_height:
            return 0
        u = user.lower()
        last_height = self.user_last_claim_height.get(u, self.issue_height)
        effective_height = min(current_height, self.maturity_height)
        if effective_height <= last_height:
            return 0

        intervals = (effective_height - last_height) // self.coupon_interval_blocks
        if intervals <= 0:
            return 0

        # Coupon per bond per interval = (face_value * coupon_rate_bps) / 10000
        coupon_per_unit = bps_mul(self.face_value, self.coupon_rate_bps)
        total_coupon = coupon_per_unit * balance * intervals
        return total_coupon

    def claim_coupon(self, user: str, balance: int, current_height: int) -> tuple[int, int]:
        """Claim due coupons and advance claimant's claimed block height."""
        u = user.lower()
        total_coupon = self.calculate_claimable_coupons(u, balance, current_height)
        if total_coupon <= 0:
            return 0, self.user_last_claim_height.get(u, self.issue_height)

        last_height = self.user_last_claim_height.get(u, self.issue_height)
        effective_height = min(current_height, self.maturity_height)
        intervals = (effective_height - last_height) // self.coupon_interval_blocks
        new_claim_height = last_height + intervals * self.coupon_interval_blocks
        self.user_last_claim_height[u] = new_claim_height
        return total_coupon, new_claim_height

    def redeem_principal(self, user: str, balance: int, current_height: int) -> int:
        """Redeem face value principal after bond reaches maturity block height."""
        if current_height < self.maturity_height:
            raise MaturityError(f"Bond has not matured: current {current_height} < maturity {self.maturity_height}")
        if balance <= 0:
            return 0
        u = user.lower()
        already_redeemed = self.redeemed_principal.get(u, 0)
        unredeemed_balance = balance - already_redeemed
        if unredeemed_balance <= 0:
            return 0
        payout = unredeemed_balance * self.face_value
        self.redeemed_principal[u] = balance
        return payout

    def to_dict(self) -> dict:
        return {
            "asset": self.asset.to_dict(),
            "face_value": str(self.face_value),
            "coupon_rate_bps": self.coupon_rate_bps,
            "coupon_interval_blocks": self.coupon_interval_blocks,
            "issue_height": self.issue_height,
            "maturity_height": self.maturity_height,
            "user_last_claim_height": {k: v for k, v in sorted(self.user_last_claim_height.items())},
            "redeemed_principal": {k: str(v) for k, v in sorted(self.redeemed_principal.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BondAsset":
        return cls(
            asset=Asset.from_dict(d["asset"]),
            face_value=int(d["face_value"]),
            coupon_rate_bps=int(d["coupon_rate_bps"]),
            coupon_interval_blocks=int(d["coupon_interval_blocks"]),
            issue_height=int(d["issue_height"]),
            maturity_height=int(d["maturity_height"]),
            user_last_claim_height={k: int(v) for k, v in d.get("user_last_claim_height", {}).items()},
            redeemed_principal={k: int(v) for k, v in d.get("redeemed_principal", {}).items()},
        )
