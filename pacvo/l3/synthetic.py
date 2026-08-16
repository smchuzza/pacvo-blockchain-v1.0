"""Synthetic Asset Representation and Reference Index Tracking."""

from dataclasses import dataclass
from pacvo.l3.asset import Asset, AssetType


@dataclass
class SyntheticAsset:
    """Represents a simulated synthetic instrument tracking a deterministic price index."""

    asset: Asset
    reference_index_symbol: str
    target_collateral_ratio_bps: int = 15000 # 150% overcollateralization

    @classmethod
    def create(
        cls,
        symbol: str,
        name: str,
        token_address: str,
        issuer: str,
        reference_index_symbol: str,
        initial_supply: int = 0,
        creation_height: int = 0,
    ) -> "SyntheticAsset":
        asset = Asset(
            symbol=symbol,
            name=name,
            token_address=token_address,
            asset_type=AssetType.SYNTHETIC,
            issuer=issuer,
            total_supply=initial_supply,
            creation_height=creation_height,
        )
        return cls(
            asset=asset,
            reference_index_symbol=reference_index_symbol,
        )

    def to_dict(self) -> dict:
        return {
            "asset": self.asset.to_dict(),
            "reference_index_symbol": self.reference_index_symbol,
            "target_collateral_ratio_bps": self.target_collateral_ratio_bps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SyntheticAsset":
        return cls(
            asset=Asset.from_dict(d["asset"]),
            reference_index_symbol=d["reference_index_symbol"],
            target_collateral_ratio_bps=int(d.get("target_collateral_ratio_bps", 15000)),
        )
