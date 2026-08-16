"""Central PVO-Fi Economy Orchestrator."""

from dataclasses import dataclass, field
from typing import Optional

from pacvo.l3.asset import Asset, AssetRegistry, AssetType
from pacvo.l3.bond import BondAsset
from pacvo.l3.debt import DebtManager
from pacvo.l3.bridge import (
    DEFAULT_BTC_VAULT_ADDRESS,
    DEFAULT_CC_VAULT_ADDRESS,
    DEFAULT_XNO_VAULT_ADDRESS,
    BridgeManager,
)
from pacvo.l3.equity import EquityAsset
from pacvo.l3.errors import InvariantViolationError, UnauthorizedError
from pacvo.l3.fixed import WAD, to_wad
from pacvo.l3.fund import FundAsset
from pacvo.l3.htlc import HTLCSwapManager
from pacvo.l3.lending import LendingPool
from pacvo.l3.market import MarketManager
from pacvo.l3.pricing import PriceEngine
from pacvo.l3.reserve import ExternalReserveAdapter, PVOFiReserve
from pacvo.l3.synthetic import SyntheticAsset
from pacvo.l3.treasury import TreasuryManager

EPOCH_LENGTH_BLOCKS = 100


class Economy:
    """Central state machine orchestrating all L3 economic subsystems."""

    def __init__(
        self,
        genesis_reserve_pol: int = 4 * WAD,
        polygon_chain_id: int = 137,
        reserve_wallet_address: str = "0xe9D970937ba528245BAeD156aFe036e0Fa565218",
    ):
        self.registry = AssetRegistry()
        self.equities: dict[str, EquityAsset] = {}
        self.bonds: dict[str, BondAsset] = {}
        self.funds: dict[str, FundAsset] = {}
        self.synthetics: dict[str, SyntheticAsset] = {}
        self.lending_pools: dict[str, LendingPool] = {}
        self.debt_manager = DebtManager()
        self.market_manager = MarketManager()
        self.treasury = TreasuryManager()
        self.reserve = PVOFiReserve(
            polygon_chain_id=polygon_chain_id,
            reserve_wallet_address=reserve_wallet_address,
            genesis_reserve_target=genesis_reserve_pol,
            accounting_balance=genesis_reserve_pol,
            verified_onchain_balance=genesis_reserve_pol,
        )
        self.bridge = BridgeManager()
        self.htlc = HTLCSwapManager()
        self.price_engine = PriceEngine(base_currency="POL")
        self.current_height: int = 0
        self.epoch: int = 0

        # Register Base Settlement Asset (POL)
        pol_asset = Asset(
            symbol="POL",
            name="Pacvo On-chain Liquidity",
            token_address="0x0000000000000000000000000000000000000000",
            asset_type=AssetType.RESERVE,
            total_supply=genesis_reserve_pol,
        )
        self.registry.register_asset(pol_asset)

        # Register Native Cross-Chain Wrapped Assets (wPVO-BTC, wPVO-XNO, wCCPVO)
        btc_asset = Asset(
            symbol="wPVO-BTC",
            name="Wrapped Pacvo Bitcoin",
            token_address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            asset_type=AssetType.RESERVE,
            total_supply=0,
        )
        self.registry.register_asset(btc_asset)
        self.reserve.register_external_adapter(
            ExternalReserveAdapter(
                asset_symbol="wPVO-BTC",
                chain_name="Bitcoin",
                chain_id=0,
                wallet_address=DEFAULT_BTC_VAULT_ADDRESS,
            )
        )

        xno_asset = Asset(
            symbol="wPVO-XNO",
            name="Wrapped Pacvo Nano",
            token_address="0xcccccccccccccccccccccccccccccccccccccccc",
            asset_type=AssetType.RESERVE,
            total_supply=0,
        )
        self.registry.register_asset(xno_asset)
        self.reserve.register_external_adapter(
            ExternalReserveAdapter(
                asset_symbol="wPVO-XNO",
                chain_name="Nano",
                chain_id=0,
                wallet_address=DEFAULT_XNO_VAULT_ADDRESS,
            )
        )

        cc_asset = Asset(
            symbol="wCCPVO",
            name="Wrapped Chocohub CCpow",
            token_address="0xdddddddddddddddddddddddddddddddddddddddd",
            asset_type=AssetType.RESERVE,
            total_supply=0,
        )
        self.registry.register_asset(cc_asset)
        self.reserve.register_external_adapter(
            ExternalReserveAdapter(
                asset_symbol="wCCPVO",
                chain_name="Chocohub",
                chain_id=0,
                wallet_address=DEFAULT_CC_VAULT_ADDRESS,
            )
        )

    def advance_to_height(self, block_height: int) -> int:
        """Advance economic clock to current block height and process epoch transitions."""
        if block_height < self.current_height:
            raise InvariantViolationError(f"Cannot rewind height: {block_height} < {self.current_height}")
        self.current_height = block_height
        new_epoch = block_height // EPOCH_LENGTH_BLOCKS
        if new_epoch > self.epoch:
            self._process_epoch_transition(new_epoch)
            self.epoch = new_epoch
        return self.epoch

    def _process_epoch_transition(self, new_epoch: int) -> None:
        """Execute periodic epoch accounting updates."""
        self.reserve.verify_invariant()
        self.bridge.verify_bridge_invariants()

    # --- Equities & Dividends ---

    def register_equity(
        self,
        symbol: str,
        name: str,
        token_address: str,
        issuer: str,
        total_supply: int,
    ) -> EquityAsset:
        sym = symbol.upper()
        eq = EquityAsset.create(
            symbol=sym,
            name=name,
            token_address=token_address,
            issuer=issuer,
            total_supply=total_supply,
            creation_height=self.current_height,
        )
        self.registry.register_asset(eq.asset)
        self.equities[sym] = eq
        return eq

    def declare_dividend(self, symbol: str, payout_amount: int) -> int:
        eq = self.equities.get(symbol.upper())
        if eq is None:
            raise InvariantViolationError(f"Equity '{symbol}' not found")
        # Ensure treasury has adequate balance to sponsor dividend
        self.treasury.withdraw("POL", payout_amount)
        return eq.declare_dividend(payout_amount)

    # --- Bonds & Coupons ---

    def register_bond(
        self,
        symbol: str,
        name: str,
        token_address: str,
        issuer: str,
        total_supply: int,
        face_value: int,
        coupon_rate_bps: int,
        coupon_interval_blocks: int,
        maturity_height: int,
    ) -> BondAsset:
        sym = symbol.upper()
        bond = BondAsset.create(
            symbol=sym,
            name=name,
            token_address=token_address,
            issuer=issuer,
            total_supply=total_supply,
            face_value=face_value,
            coupon_rate_bps=coupon_rate_bps,
            coupon_interval_blocks=coupon_interval_blocks,
            issue_height=self.current_height,
            maturity_height=maturity_height,
        )
        self.registry.register_asset(bond.asset)
        self.bonds[sym] = bond
        return bond

    # --- Lending Pools ---

    def get_or_create_lending_pool(self, symbol: str) -> LendingPool:
        sym = symbol.upper()
        if sym not in self.lending_pools:
            self.lending_pools[sym] = LendingPool(symbol=sym)
        return self.lending_pools[sym]

    # --- Basket Funds ---

    def register_fund(
        self,
        symbol: str,
        name: str,
        token_address: str,
        issuer: str,
        allocations_bps: dict[str, int],
    ) -> FundAsset:
        sym = symbol.upper()
        fund = FundAsset.create(
            symbol=sym,
            name=name,
            token_address=token_address,
            issuer=issuer,
            allocations_bps=allocations_bps,
            creation_height=self.current_height,
        )
        self.registry.register_asset(fund.asset)
        self.funds[sym] = fund
        return fund

    def to_dict(self) -> dict:
        return {
            "current_height": self.current_height,
            "epoch": self.epoch,
            "registry": self.registry.to_dict(),
            "equities": {k: self.equities[k].to_dict() for k in sorted(self.equities.keys())},
            "bonds": {k: self.bonds[k].to_dict() for k in sorted(self.bonds.keys())},
            "funds": {k: self.funds[k].to_dict() for k in sorted(self.funds.keys())},
            "synthetics": {k: self.synthetics[k].to_dict() for k in sorted(self.synthetics.keys())},
            "lending_pools": {k: self.lending_pools[k].to_dict() for k in sorted(self.lending_pools.keys())},
            "debt": self.debt_manager.to_dict(),
            "markets": self.market_manager.to_dict(),
            "treasury": self.treasury.to_dict(),
            "reserve": self.reserve.to_dict(),
            "bridge": self.bridge.to_dict(),
            "htlc": self.htlc.to_dict(),
            "prices": self.price_engine.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Economy":
        eco = cls.__new__(cls)
        eco.current_height = int(d.get("current_height", 0))
        eco.epoch = int(d.get("epoch", 0))
        eco.registry = AssetRegistry.from_dict(d.get("registry", {}))
        eco.equities = {k: EquityAsset.from_dict(v) for k, v in d.get("equities", {}).items()}
        eco.bonds = {k: BondAsset.from_dict(v) for k, v in d.get("bonds", {}).items()}
        eco.funds = {k: FundAsset.from_dict(v) for k, v in d.get("funds", {}).items()}
        eco.synthetics = {k: SyntheticAsset.from_dict(v) for k, v in d.get("synthetics", {}).items()}
        eco.lending_pools = {k: LendingPool.from_dict(v) for k, v in d.get("lending_pools", {}).items()}
        eco.debt_manager = DebtManager.from_dict(d.get("debt", {}))
        eco.market_manager = MarketManager.from_dict(d.get("markets", {}))
        eco.treasury = TreasuryManager.from_dict(d.get("treasury", {}))
        eco.reserve = PVOFiReserve.from_dict(d.get("reserve", {}))
        eco.bridge = BridgeManager.from_dict(d.get("bridge", {}))
        eco.htlc = HTLCSwapManager.from_dict(d.get("htlc", {}))
        eco.price_engine = PriceEngine.from_dict(d.get("prices", {}))
        return eco

    def copy(self) -> "Economy":
        return Economy.from_dict(self.to_dict())
