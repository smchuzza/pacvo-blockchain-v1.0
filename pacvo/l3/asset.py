"""L3 Asset Registry and Taxonomy System."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pacvo.l3.errors import DuplicateAssetError


class AssetType(Enum):
    RESERVE   = "RESERVE"   # Base settlement/reserve currency (e.g. POL / PVO)
    EQUITY    = "EQUITY"    # Tokenized simulated corporate equity
    BOND      = "BOND"      # Tokenized fixed-income debt instrument
    DEBT      = "DEBT"      # Credit line or borrowing obligation
    FUND      = "FUND"      # Tokenized basket index fund
    SYNTHETIC = "SYNTHETIC" # Simulated synthetic asset
    TREASURY  = "TREASURY"  # Protocol treasury instrument


@dataclass
class Asset:
    """Core metadata describing an L3 simulated financial asset."""

    symbol: str
    name: str
    token_address: str
    asset_type: AssetType
    decimals: int = 18
    issuer: str = ""
    total_supply: int = 0
    max_supply: int = 0
    creation_height: int = 0
    maturity_height: int = 0
    coupon_rate_bps: int = 0
    dividend_rate_bps: int = 0
    collateral_ratio_bps: int = 0
    status: str = "ACTIVE"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "token_address": self.token_address,
            "asset_type": self.asset_type.value,
            "decimals": self.decimals,
            "issuer": self.issuer,
            "total_supply": str(self.total_supply),
            "max_supply": str(self.max_supply),
            "creation_height": self.creation_height,
            "maturity_height": self.maturity_height,
            "coupon_rate_bps": self.coupon_rate_bps,
            "dividend_rate_bps": self.dividend_rate_bps,
            "collateral_ratio_bps": self.collateral_ratio_bps,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Asset":
        return cls(
            symbol=d["symbol"],
            name=d["name"],
            token_address=d["token_address"],
            asset_type=AssetType(d["asset_type"]),
            decimals=d.get("decimals", 18),
            issuer=d.get("issuer", ""),
            total_supply=int(d.get("total_supply", 0)),
            max_supply=int(d.get("max_supply", 0)),
            creation_height=int(d.get("creation_height", 0)),
            maturity_height=int(d.get("maturity_height", 0)),
            coupon_rate_bps=int(d.get("coupon_rate_bps", 0)),
            dividend_rate_bps=int(d.get("dividend_rate_bps", 0)),
            collateral_ratio_bps=int(d.get("collateral_ratio_bps", 0)),
            status=d.get("status", "ACTIVE"),
        )


class AssetRegistry:
    """Registry maintaining all approved and issued L3 economic assets."""

    def __init__(self):
        self._assets: dict[str, Asset] = {}

    def register_asset(self, asset: Asset) -> None:
        sym = asset.symbol.upper()
        if sym in self._assets:
            raise DuplicateAssetError(f"Asset '{sym}' is already registered")
        self._assets[sym] = asset

    def get_asset(self, symbol: str) -> Optional[Asset]:
        return self._assets.get(symbol.upper())

    def list_assets(self, asset_type: Optional[AssetType] = None) -> list[Asset]:
        if asset_type is None:
            return sorted(self._assets.values(), key=lambda a: a.symbol)
        return sorted([a for a in self._assets.values() if a.asset_type == asset_type], key=lambda a: a.symbol)

    def to_dict(self) -> dict:
        return {sym: self._assets[sym].to_dict() for sym in sorted(self._assets.keys())}

    @classmethod
    def from_dict(cls, d: dict) -> "AssetRegistry":
        reg = cls()
        for sym, asset_dict in d.items():
            reg._assets[sym.upper()] = Asset.from_dict(asset_dict)
        return reg
