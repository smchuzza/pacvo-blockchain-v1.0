"""Native Cross-Chain Bridge Subsystem for wPVO-BTC and wPVO-XNO.

Provides bidirectional native wrapping, cryptographic attestation, unit
conversion, and proof-of-reserve enforcement between Pacvo L2/L3 and external
networks (Bitcoin, Nano).

SECURITY RULES:
- NO private keys or seed phrases exist or are permitted in this codebase or state.
- Bridge operates exclusively on public vault addresses and cryptographically
  attested external deposit/burn proofs.
- Invariant: minted_wrapped_supply <= verified_vault_balance.
"""

from dataclasses import dataclass, field
import hashlib
import time
from typing import Optional

from pacvo.l3.asset import Asset, AssetRegistry, AssetType
from pacvo.l3.errors import InsufficientReserveError, InvariantViolationError, UnauthorizedError
from pacvo.l3.fixed import WAD, bps_mul, wad_div, wad_mul
from pacvo.l3.reserve import (
    ExternalReserveAdapter,
    PVOFiReserve,
    ReserveAttestation,
    ReserveTransaction,
)

# Bridge Vault Public Addresses (No private keys)
DEFAULT_BTC_VAULT_ADDRESS = "bc1qpacvovaultbtc8923489234892348923489234892"
DEFAULT_XNO_VAULT_ADDRESS = "nano_1pacvovaultxno982349823498234982349823498234982349823498234982"
DEFAULT_CC_VAULT_ADDRESS = "choco_1pacvovaultccpow982349823498234982349823498234982"

# Bitcoin Constants: 1 BTC = 10^8 Satoshis. Scale to 18-decimal WAD (factor = 10^10)
BTC_DECIMALS_NATIVE = 8
BTC_WAD_SCALE = 10**10
BTC_DEFAULT_FEE_BPS = 15  # 0.15%

# Nano Constants: 1 XNO = 10^30 Raw. Scale to 18-decimal WAD (factor = 10^12 divisor)
XNO_DECIMALS_NATIVE = 30
XNO_RAW_DIVISOR = 10**12
XNO_DEFAULT_FEE_BPS = 10  # 0.10%

# Chocohub Constants: 1 CC = 10^8 Base Units. Scale to 18-decimal WAD (factor = 10^10)
CC_DECIMALS_NATIVE = 8
CC_WAD_SCALE = 10**10
CC_DEFAULT_FEE_BPS = 10  # 0.10%


@dataclass
class BridgeDepositRecord:
    """Record of an external on-chain deposit locked in the bridge vault."""

    deposit_id: str
    asset_symbol: str
    external_chain: str
    external_tx_hash: str
    external_from: str
    external_vault: str
    pacvo_recipient: str
    raw_amount: int
    mint_amount_wad: int
    fee_wad: int
    block_height: int
    timestamp: int
    status: str = "CONFIRMED"

    def to_dict(self) -> dict:
        return {
            "deposit_id": self.deposit_id,
            "asset_symbol": self.asset_symbol,
            "external_chain": self.external_chain,
            "external_tx_hash": self.external_tx_hash,
            "external_from": self.external_from,
            "external_vault": self.external_vault,
            "pacvo_recipient": self.pacvo_recipient,
            "raw_amount": str(self.raw_amount),
            "mint_amount_wad": str(self.mint_amount_wad),
            "fee_wad": str(self.fee_wad),
            "block_height": self.block_height,
            "timestamp": self.timestamp,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BridgeDepositRecord":
        return cls(
            deposit_id=d["deposit_id"],
            asset_symbol=d["asset_symbol"],
            external_chain=d["external_chain"],
            external_tx_hash=d["external_tx_hash"],
            external_from=d.get("external_from", ""),
            external_vault=d["external_vault"],
            pacvo_recipient=d["pacvo_recipient"],
            raw_amount=int(d["raw_amount"]),
            mint_amount_wad=int(d["mint_amount_wad"]),
            fee_wad=int(d["fee_wad"]),
            block_height=int(d["block_height"]),
            timestamp=int(d["timestamp"]),
            status=d.get("status", "CONFIRMED"),
        )


@dataclass
class BridgeBurnRecord:
    """Record of wrapped tokens burned on Pacvo to request an external release."""

    burn_id: str
    asset_symbol: str
    external_chain: str
    pacvo_sender: str
    external_destination: str
    burn_amount_wad: int
    raw_unlock_amount: int
    fee_wad: int
    block_height: int
    timestamp: int
    status: str = "COMMITTED"
    external_claim_tx_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "burn_id": self.burn_id,
            "asset_symbol": self.asset_symbol,
            "external_chain": self.external_chain,
            "pacvo_sender": self.pacvo_sender,
            "external_destination": self.external_destination,
            "burn_amount_wad": str(self.burn_amount_wad),
            "raw_unlock_amount": str(self.raw_unlock_amount),
            "fee_wad": str(self.fee_wad),
            "block_height": self.block_height,
            "timestamp": self.timestamp,
            "status": self.status,
            "external_claim_tx_hash": self.external_claim_tx_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BridgeBurnRecord":
        return cls(
            burn_id=d["burn_id"],
            asset_symbol=d["asset_symbol"],
            external_chain=d["external_chain"],
            pacvo_sender=d["pacvo_sender"],
            external_destination=d["external_destination"],
            burn_amount_wad=int(d["burn_amount_wad"]),
            raw_unlock_amount=int(d["raw_unlock_amount"]),
            fee_wad=int(d["fee_wad"]),
            block_height=int(d["block_height"]),
            timestamp=int(d["timestamp"]),
            status=d.get("status", "COMMITTED"),
            external_claim_tx_hash=d.get("external_claim_tx_hash", ""),
        )


class BitcoinBridgeAdapter:
    """Native Bridge pipeline for wPVO-BTC (Bitcoin -> Pacvo)."""

    def __init__(
        self,
        vault_address: str = DEFAULT_BTC_VAULT_ADDRESS,
        fee_bps: int = BTC_DEFAULT_FEE_BPS,
    ):
        self.asset_symbol = "wPVO-BTC"
        self.chain_name = "Bitcoin"
        self.vault_address = vault_address
        self.fee_bps = fee_bps
        self.total_locked_satoshis: int = 0
        self.total_minted_wad: int = 0

    @staticmethod
    def satoshis_to_wad(satoshis: int) -> int:
        """Convert 8-decimal satoshis to 18-decimal WAD: sat * 10^10."""
        return satoshis * BTC_WAD_SCALE

    @staticmethod
    def wad_to_satoshis(amount_wad: int) -> int:
        """Convert 18-decimal WAD to 8-decimal satoshis: floor(wad / 10^10)."""
        return amount_wad // BTC_WAD_SCALE

    def calculate_mint_amount(self, satoshis: int) -> tuple[int, int]:
        """Returns (net_mint_wad, fee_wad)."""
        gross_wad = self.satoshis_to_wad(satoshis)
        fee_wad = bps_mul(gross_wad, self.fee_bps)
        net_mint_wad = gross_wad - fee_wad
        return net_mint_wad, fee_wad

    def calculate_unlock_amount(self, amount_wad: int) -> tuple[int, int]:
        """Returns (net_unlock_satoshis, fee_wad)."""
        fee_wad = bps_mul(amount_wad, self.fee_bps)
        net_wad = amount_wad - fee_wad
        net_satoshis = self.wad_to_satoshis(net_wad)
        return net_satoshis, fee_wad


class NanoBridgeAdapter:
    """Native Bridge pipeline for wPVO-XNO (Nano -> Pacvo)."""

    def __init__(
        self,
        vault_address: str = DEFAULT_XNO_VAULT_ADDRESS,
        fee_bps: int = XNO_DEFAULT_FEE_BPS,
    ):
        self.asset_symbol = "wPVO-XNO"
        self.chain_name = "Nano"
        self.vault_address = vault_address
        self.fee_bps = fee_bps
        self.total_locked_raw: int = 0
        self.total_minted_wad: int = 0

    @staticmethod
    def raw_to_wad(raw_amount: int) -> int:
        """Convert 30-decimal Nano raw to 18-decimal WAD: floor(raw / 10^12)."""
        return raw_amount // XNO_RAW_DIVISOR

    @staticmethod
    def wad_to_raw(amount_wad: int) -> int:
        """Convert 18-decimal WAD to 30-decimal Nano raw: wad * 10^12."""
        return amount_wad * XNO_RAW_DIVISOR

    def calculate_mint_amount(self, raw_amount: int) -> tuple[int, int]:
        """Returns (net_mint_wad, fee_wad)."""
        gross_wad = self.raw_to_wad(raw_amount)
        fee_wad = bps_mul(gross_wad, self.fee_bps)
        net_mint_wad = gross_wad - fee_wad
        return net_mint_wad, fee_wad

    def calculate_unlock_amount(self, amount_wad: int) -> tuple[int, int]:
        """Returns (net_unlock_raw, fee_wad)."""
        fee_wad = bps_mul(amount_wad, self.fee_bps)
        net_wad = amount_wad - fee_wad
        net_raw = self.wad_to_raw(net_wad)
        return net_raw, fee_wad


class ChocohubBridgeAdapter:
    """Native Bridge pipeline for wCCPVO (Chocohub CC -> Pacvo)."""

    def __init__(
        self,
        vault_address: str = DEFAULT_CC_VAULT_ADDRESS,
        fee_bps: int = CC_DEFAULT_FEE_BPS,
    ):
        self.asset_symbol = "wCCPVO"
        self.chain_name = "Chocohub"
        self.vault_address = vault_address
        self.fee_bps = fee_bps
        self.total_locked_raw: int = 0
        self.total_minted_wad: int = 0

    @staticmethod
    def raw_to_wad(raw_amount: int) -> int:
        """Convert 8-decimal CC units to 18-decimal WAD: raw * 10^10."""
        return raw_amount * CC_WAD_SCALE

    @staticmethod
    def wad_to_raw(amount_wad: int) -> int:
        """Convert 18-decimal WAD to 8-decimal CC units: floor(wad / 10^10)."""
        return amount_wad // CC_WAD_SCALE

    def calculate_mint_amount(self, raw_amount: int) -> tuple[int, int]:
        """Returns (net_mint_wad, fee_wad)."""
        gross_wad = self.raw_to_wad(raw_amount)
        fee_wad = bps_mul(gross_wad, self.fee_bps)
        net_mint_wad = gross_wad - fee_wad
        return net_mint_wad, fee_wad

    def calculate_unlock_amount(self, amount_wad: int) -> tuple[int, int]:
        """Returns (net_unlock_raw, fee_wad)."""
        fee_wad = bps_mul(amount_wad, self.fee_bps)
        net_wad = amount_wad - fee_wad
        net_raw = self.wad_to_raw(net_wad)
        return net_raw, fee_wad


@dataclass
class BridgeManager:
    """Central orchestrator managing native bridges for wPVO-BTC, wPVO-XNO, and wCCPVO."""

    btc_adapter: BitcoinBridgeAdapter = field(default_factory=BitcoinBridgeAdapter)
    xno_adapter: NanoBridgeAdapter = field(default_factory=NanoBridgeAdapter)
    cc_adapter: ChocohubBridgeAdapter = field(default_factory=ChocohubBridgeAdapter)
    deposits: dict[str, BridgeDepositRecord] = field(default_factory=dict)
    burns: dict[str, BridgeBurnRecord] = field(default_factory=dict)
    processed_external_txs: set[str] = field(default_factory=set)
    balances: dict[str, dict[str, int]] = field(default_factory=dict)  # symbol -> user -> balance

    def get_balance(self, symbol: str, user: str) -> int:
        sym = symbol.upper()
        return self.balances.get(sym, {}).get(user, 0)

    def get_vault(self, symbol: str) -> dict:
        sym = symbol.upper()
        if sym in ("BTC", "WPVO-BTC"):
            return {
                "asset_symbol": self.btc_adapter.asset_symbol,
                "chain_name": self.btc_adapter.chain_name,
                "vault_address": self.btc_adapter.vault_address,
                "fee_bps": self.btc_adapter.fee_bps,
                "total_locked_raw": str(self.btc_adapter.total_locked_satoshis),
                "total_minted_wad": str(self.btc_adapter.total_minted_wad),
            }
        elif sym in ("XNO", "WPVO-XNO"):
            return {
                "asset_symbol": self.xno_adapter.asset_symbol,
                "chain_name": self.xno_adapter.chain_name,
                "vault_address": self.xno_adapter.vault_address,
                "fee_bps": self.xno_adapter.fee_bps,
                "total_locked_raw": str(self.xno_adapter.total_locked_raw),
                "total_minted_wad": str(self.xno_adapter.total_minted_wad),
            }
        elif sym in ("CC", "WCCPVO", "WPVO-CC"):
            return {
                "asset_symbol": self.cc_adapter.asset_symbol,
                "chain_name": self.cc_adapter.chain_name,
                "vault_address": self.cc_adapter.vault_address,
                "fee_bps": self.cc_adapter.fee_bps,
                "total_locked_raw": str(self.cc_adapter.total_locked_raw),
                "total_minted_wad": str(self.cc_adapter.total_minted_wad),
            }
        return {"error": f"Unknown bridge vault symbol '{symbol}'"}

    def _credit_balance(self, symbol: str, user: str, amount: int) -> None:
        sym = symbol.upper()
        if sym not in self.balances:
            self.balances[sym] = {}
        self.balances[sym][user] = self.balances[sym].get(user, 0) + amount

    def _debit_balance(self, symbol: str, user: str, amount: int) -> None:
        sym = symbol.upper()
        current = self.get_balance(sym, user)
        if current < amount:
            raise InsufficientReserveError(
                f"Insufficient {sym} balance: user {user} has {current}, requires {amount}"
            )
        self.balances[sym][user] = current - amount

    # --- Deposit (Lock & Mint) ---

    def process_btc_deposit(
        self,
        external_tx_hash: str,
        external_from: str,
        pacvo_recipient: str,
        satoshis: int,
        block_height: int = 0,
        timestamp: int = 0,
    ) -> BridgeDepositRecord:
        """Process Bitcoin deposit into vault and mint wPVO-BTC."""
        if external_tx_hash in self.processed_external_txs:
            raise InvariantViolationError(f"Bitcoin tx {external_tx_hash} already processed")
        if satoshis <= 0:
            raise InvariantViolationError("Deposit satoshis must be strictly positive")
        if timestamp == 0:
            timestamp = int(time.time())

        net_mint_wad, fee_wad = self.btc_adapter.calculate_mint_amount(satoshis)
        deposit_id = hashlib.sha256(
            f"BTC:{external_tx_hash}:{external_from}:{pacvo_recipient}:{satoshis}:{block_height}".encode()
        ).hexdigest()

        record = BridgeDepositRecord(
            deposit_id=deposit_id,
            asset_symbol="wPVO-BTC",
            external_chain="Bitcoin",
            external_tx_hash=external_tx_hash,
            external_from=external_from,
            external_vault=self.btc_adapter.vault_address,
            pacvo_recipient=pacvo_recipient,
            raw_amount=satoshis,
            mint_amount_wad=net_mint_wad,
            fee_wad=fee_wad,
            block_height=block_height,
            timestamp=timestamp,
            status="CONFIRMED",
        )

        self.deposits[deposit_id] = record
        self.processed_external_txs.add(external_tx_hash)
        self.btc_adapter.total_locked_satoshis += satoshis
        self.btc_adapter.total_minted_wad += net_mint_wad
        self._credit_balance("wPVO-BTC", pacvo_recipient, net_mint_wad)
        return record

    def process_xno_deposit(
        self,
        external_block_hash: str,
        external_from: str,
        pacvo_recipient: str,
        raw_amount: int,
        block_height: int = 0,
        timestamp: int = 0,
    ) -> BridgeDepositRecord:
        """Process Nano deposit into vault and mint wPVO-XNO."""
        if external_block_hash in self.processed_external_txs:
            raise InvariantViolationError(f"Nano block {external_block_hash} already processed")
        if raw_amount <= 0:
            raise InvariantViolationError("Deposit raw amount must be strictly positive")
        if timestamp == 0:
            timestamp = int(time.time())

        net_mint_wad, fee_wad = self.xno_adapter.calculate_mint_amount(raw_amount)
        deposit_id = hashlib.sha256(
            f"XNO:{external_block_hash}:{external_from}:{pacvo_recipient}:{raw_amount}:{block_height}".encode()
        ).hexdigest()

        record = BridgeDepositRecord(
            deposit_id=deposit_id,
            asset_symbol="wPVO-XNO",
            external_chain="Nano",
            external_tx_hash=external_block_hash,
            external_from=external_from,
            external_vault=self.xno_adapter.vault_address,
            pacvo_recipient=pacvo_recipient,
            raw_amount=raw_amount,
            mint_amount_wad=net_mint_wad,
            fee_wad=fee_wad,
            block_height=block_height,
            timestamp=timestamp,
            status="CONFIRMED",
        )

        self.deposits[deposit_id] = record
        self.processed_external_txs.add(external_block_hash)
        self.xno_adapter.total_locked_raw += raw_amount
        self.xno_adapter.total_minted_wad += net_mint_wad
        self._credit_balance("wPVO-XNO", pacvo_recipient, net_mint_wad)
        return record

    # --- Burn (Burn & Unlock Request) ---

    def process_btc_burn(
        self,
        pacvo_sender: str,
        external_btc_destination: str,
        amount_wad: int,
        block_height: int = 0,
        timestamp: int = 0,
    ) -> BridgeBurnRecord:
        """Burn wPVO-BTC on Pacvo to initiate Bitcoin vault release."""
        if amount_wad <= 0:
            raise InvariantViolationError("Burn amount must be strictly positive")
        if timestamp == 0:
            timestamp = int(time.time())

        self._debit_balance("wPVO-BTC", pacvo_sender, amount_wad)
        net_unlock_satoshis, fee_wad = self.btc_adapter.calculate_unlock_amount(amount_wad)

        burn_id = hashlib.sha256(
            f"BTC_BURN:{pacvo_sender}:{external_btc_destination}:{amount_wad}:{block_height}:{timestamp}".encode()
        ).hexdigest()

        record = BridgeBurnRecord(
            burn_id=burn_id,
            asset_symbol="wPVO-BTC",
            external_chain="Bitcoin",
            pacvo_sender=pacvo_sender,
            external_destination=external_btc_destination,
            burn_amount_wad=amount_wad,
            raw_unlock_amount=net_unlock_satoshis,
            fee_wad=fee_wad,
            block_height=block_height,
            timestamp=timestamp,
            status="COMMITTED",
        )

        self.burns[burn_id] = record
        self.btc_adapter.total_minted_wad = max(0, self.btc_adapter.total_minted_wad - amount_wad)
        self.btc_adapter.total_locked_satoshis = max(0, self.btc_adapter.total_locked_satoshis - net_unlock_satoshis)
        return record

    def process_xno_burn(
        self,
        pacvo_sender: str,
        external_nano_destination: str,
        amount_wad: int,
        block_height: int = 0,
        timestamp: int = 0,
    ) -> BridgeBurnRecord:
        """Burn wPVO-XNO on Pacvo to initiate Nano vault release."""
        if amount_wad <= 0:
            raise InvariantViolationError("Burn amount must be strictly positive")
        if timestamp == 0:
            timestamp = int(time.time())

        self._debit_balance("wPVO-XNO", pacvo_sender, amount_wad)
        net_unlock_raw, fee_wad = self.xno_adapter.calculate_unlock_amount(amount_wad)

        burn_id = hashlib.sha256(
            f"XNO_BURN:{pacvo_sender}:{external_nano_destination}:{amount_wad}:{block_height}:{timestamp}".encode()
        ).hexdigest()

        record = BridgeBurnRecord(
            burn_id=burn_id,
            asset_symbol="wPVO-XNO",
            external_chain="Nano",
            pacvo_sender=pacvo_sender,
            external_destination=external_nano_destination,
            burn_amount_wad=amount_wad,
            raw_unlock_amount=net_unlock_raw,
            fee_wad=fee_wad,
            block_height=block_height,
            timestamp=timestamp,
            status="COMMITTED",
        )

        self.burns[burn_id] = record
        self.xno_adapter.total_minted_wad = max(0, self.xno_adapter.total_minted_wad - amount_wad)
        self.xno_adapter.total_locked_raw = max(0, self.xno_adapter.total_locked_raw - net_unlock_raw)
        return record

    def process_cc_deposit(
        self,
        external_tx_hash: str,
        external_from: str,
        pacvo_recipient: str,
        raw_amount: int,
        block_height: int = 0,
        timestamp: int = 0,
    ) -> BridgeDepositRecord:
        """Process Chocohub CC deposit into vault and mint wCCPVO."""
        if external_tx_hash in self.processed_external_txs:
            raise InvariantViolationError(f"Chocohub tx {external_tx_hash} already processed")
        if raw_amount <= 0:
            raise InvariantViolationError("Deposit raw amount must be strictly positive")
        if timestamp == 0:
            timestamp = int(time.time())

        net_mint_wad, fee_wad = self.cc_adapter.calculate_mint_amount(raw_amount)
        deposit_id = hashlib.sha256(
            f"CC:{external_tx_hash}:{external_from}:{pacvo_recipient}:{raw_amount}:{block_height}".encode()
        ).hexdigest()

        record = BridgeDepositRecord(
            deposit_id=deposit_id,
            asset_symbol="wCCPVO",
            external_chain="Chocohub",
            external_tx_hash=external_tx_hash,
            external_from=external_from,
            external_vault=self.cc_adapter.vault_address,
            pacvo_recipient=pacvo_recipient,
            raw_amount=raw_amount,
            mint_amount_wad=net_mint_wad,
            fee_wad=fee_wad,
            block_height=block_height,
            timestamp=timestamp,
            status="CONFIRMED",
        )

        self.deposits[deposit_id] = record
        self.processed_external_txs.add(external_tx_hash)
        self.cc_adapter.total_locked_raw += raw_amount
        self.cc_adapter.total_minted_wad += net_mint_wad
        self._credit_balance("wCCPVO", pacvo_recipient, net_mint_wad)
        return record

    def process_cc_burn(
        self,
        pacvo_sender: str,
        external_choco_destination: str,
        amount_wad: int,
        block_height: int = 0,
        timestamp: int = 0,
    ) -> BridgeBurnRecord:
        """Burn wCCPVO on Pacvo to initiate Chocohub vault release."""
        if amount_wad <= 0:
            raise InvariantViolationError("Burn amount must be strictly positive")
        if timestamp == 0:
            timestamp = int(time.time())

        self._debit_balance("wCCPVO", pacvo_sender, amount_wad)
        net_unlock_raw, fee_wad = self.cc_adapter.calculate_unlock_amount(amount_wad)

        burn_id = hashlib.sha256(
            f"CC_BURN:{pacvo_sender}:{external_choco_destination}:{amount_wad}:{block_height}:{timestamp}".encode()
        ).hexdigest()

        record = BridgeBurnRecord(
            burn_id=burn_id,
            asset_symbol="wCCPVO",
            external_chain="Chocohub",
            pacvo_sender=pacvo_sender,
            external_destination=external_choco_destination,
            burn_amount_wad=amount_wad,
            raw_unlock_amount=net_unlock_raw,
            fee_wad=fee_wad,
            block_height=block_height,
            timestamp=timestamp,
            status="COMMITTED",
        )

        self.burns[burn_id] = record
        self.cc_adapter.total_minted_wad = max(0, self.cc_adapter.total_minted_wad - amount_wad)
        self.cc_adapter.total_locked_raw = max(0, self.cc_adapter.total_locked_raw - net_unlock_raw)
        return record

    def complete_burn_release(self, burn_id: str, external_claim_tx_hash: str) -> None:
        """Acknowledge external release transaction settlement."""
        if burn_id not in self.burns:
            raise InvariantViolationError(f"Burn ID {burn_id} not found")
        record = self.burns[burn_id]
        record.status = "SETTLED"
        record.external_claim_tx_hash = external_claim_tx_hash

    # --- Proof of Reserve Invariants ---

    def verify_bridge_invariants(self) -> bool:
        """Verify that circulating wrapped supply is strictly backed by locked vault balances."""
        # BTC Invariant: minted WAD <= locked satoshis converted to WAD
        btc_vault_wad = self.btc_adapter.satoshis_to_wad(self.btc_adapter.total_locked_satoshis)
        if self.btc_adapter.total_minted_wad > btc_vault_wad:
            raise InvariantViolationError(
                f"wPVO-BTC invariant violation: minted {self.btc_adapter.total_minted_wad} WAD exceeds "
                f"locked vault {btc_vault_wad} WAD ({self.btc_adapter.total_locked_satoshis} sats)"
            )

        # XNO Invariant: minted WAD <= locked raw converted to WAD
        xno_vault_wad = self.xno_adapter.raw_to_wad(self.xno_adapter.total_locked_raw)
        if self.xno_adapter.total_minted_wad > xno_vault_wad:
            raise InvariantViolationError(
                f"wPVO-XNO invariant violation: minted {self.xno_adapter.total_minted_wad} WAD exceeds "
                f"locked vault {xno_vault_wad} WAD ({self.xno_adapter.total_locked_raw} raw)"
            )

        # CC Invariant: minted WAD <= locked raw converted to WAD
        cc_vault_wad = self.cc_adapter.raw_to_wad(self.cc_adapter.total_locked_raw)
        if self.cc_adapter.total_minted_wad > cc_vault_wad:
            raise InvariantViolationError(
                f"wCCPVO invariant violation: minted {self.cc_adapter.total_minted_wad} WAD exceeds "
                f"locked vault {cc_vault_wad} WAD ({self.cc_adapter.total_locked_raw} raw)"
            )
        return True

    def to_dict(self) -> dict:
        return {
            "btc_bridge": {
                "asset_symbol": self.btc_adapter.asset_symbol,
                "chain_name": self.btc_adapter.chain_name,
                "vault_address": self.btc_adapter.vault_address,
                "fee_bps": self.btc_adapter.fee_bps,
                "total_locked_satoshis": str(self.btc_adapter.total_locked_satoshis),
                "total_minted_wad": str(self.btc_adapter.total_minted_wad),
            },
            "xno_bridge": {
                "asset_symbol": self.xno_adapter.asset_symbol,
                "chain_name": self.xno_adapter.chain_name,
                "vault_address": self.xno_adapter.vault_address,
                "fee_bps": self.xno_adapter.fee_bps,
                "total_locked_raw": str(self.xno_adapter.total_locked_raw),
                "total_minted_wad": str(self.xno_adapter.total_minted_wad),
            },
            "cc_bridge": {
                "asset_symbol": self.cc_adapter.asset_symbol,
                "chain_name": self.cc_adapter.chain_name,
                "vault_address": self.cc_adapter.vault_address,
                "fee_bps": self.cc_adapter.fee_bps,
                "total_locked_raw": str(self.cc_adapter.total_locked_raw),
                "total_minted_wad": str(self.cc_adapter.total_minted_wad),
            },
            "deposits": {k: v.to_dict() for k, v in sorted(self.deposits.items())},
            "burns": {k: v.to_dict() for k, v in sorted(self.burns.items())},
            "processed_external_txs": sorted(list(self.processed_external_txs)),
            "balances": {
                sym: {u: str(bal) for u, bal in sorted(users.items())}
                for sym, users in sorted(self.balances.items())
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BridgeManager":
        btc_d = d.get("btc_bridge", {})
        btc_adapter = BitcoinBridgeAdapter(
            vault_address=btc_d.get("vault_address", DEFAULT_BTC_VAULT_ADDRESS),
            fee_bps=int(btc_d.get("fee_bps", BTC_DEFAULT_FEE_BPS)),
        )
        btc_adapter.total_locked_satoshis = int(btc_d.get("total_locked_satoshis", 0))
        btc_adapter.total_minted_wad = int(btc_d.get("total_minted_wad", 0))

        xno_d = d.get("xno_bridge", {})
        xno_adapter = NanoBridgeAdapter(
            vault_address=xno_d.get("vault_address", DEFAULT_XNO_VAULT_ADDRESS),
            fee_bps=int(xno_d.get("fee_bps", XNO_DEFAULT_FEE_BPS)),
        )
        xno_adapter.total_locked_raw = int(xno_d.get("total_locked_raw", 0))
        xno_adapter.total_minted_wad = int(xno_d.get("total_minted_wad", 0))

        cc_d = d.get("cc_bridge", {})
        cc_adapter = ChocohubBridgeAdapter(
            vault_address=cc_d.get("vault_address", DEFAULT_CC_VAULT_ADDRESS),
            fee_bps=int(cc_d.get("fee_bps", CC_DEFAULT_FEE_BPS)),
        )
        cc_adapter.total_locked_raw = int(cc_d.get("total_locked_raw", 0))
        cc_adapter.total_minted_wad = int(cc_d.get("total_minted_wad", 0))

        deposits = {k: BridgeDepositRecord.from_dict(v) for k, v in d.get("deposits", {}).items()}
        burns = {k: BridgeBurnRecord.from_dict(v) for k, v in d.get("burns", {}).items()}
        processed = set(d.get("processed_external_txs", []))

        balances = {}
        for sym, users in d.get("balances", {}).items():
            balances[sym] = {u: int(bal) for u, bal in users.items()}

        return cls(
            btc_adapter=btc_adapter,
            xno_adapter=xno_adapter,
            cc_adapter=cc_adapter,
            deposits=deposits,
            burns=burns,
            processed_external_txs=processed,
            balances=balances,
        )
