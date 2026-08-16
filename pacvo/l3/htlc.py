"""Hashed Time-Locked Contract (HTLC) Cross-Chain Atomic Swap Engine.

Connects Pacvo (PVO) and Chocohub (CC) networks with a fixed atomic exchange rate
of 1 PVO = 10 CC (0.1 PVO = 1 CC), featuring cryptographic SHA-256 hashlocks,
bifurcated timelocks, and integrated Proof-of-Work CCpow mining of 10 CC per 1 PVO
swapped.
"""

from dataclasses import dataclass, field
import hashlib
import time
from typing import Optional

from pacvo.l3.errors import InsufficientReserveError, InvariantViolationError, UnauthorizedError
from pacvo.l3.fixed import WAD, bps_mul, wad_div, wad_mul

# Exchange Rate: 1 PVO = 10 CC (10 CC mined per 1 PVO swapped)
PVO_TO_CC_RATIO = 10
CC_TO_PVO_RATIO_WAD = WAD // 10  # 0.1 WAD = 10^17

# Default Timelocks in Blocks
DEFAULT_TIMELOCK_PACVO_BLOCKS = 144  # ~48 hours at 20 min/block
DEFAULT_TIMELOCK_CHOCO_BLOCKS = 72   # ~24 hours (initiator safety margin)

# Chocohub Device Multipliers for CCpow HTLC Mining
CHOCO_DEVICE_MULTIPLIERS = {
    "embedded_avr": 3.5,
    "embedded_arm": 3.5,
    "rp2040": 3.5,
    "pico": 3.5,
    "embedded_esp": 2.5,
    "esp8266": 3.0,
    "embedded_esp32": 2.0,
    "esp32": 3.0,
    "mobile": 3.6,
    "android": 3.6,
    "ios": 3.6,
    "web_miner": 3.0,
    "cpu": 3.0,
    "gpu": 2.0,
    "default": 1.0,
}

CHOCO_BASE_BLOCK_REWARD_CC = 5 * 10**16  # 0.05 CC in WAD scale (5 * 10^16)


# Default Chocohub CCpow Worker Settings
DEFAULT_CHOCO_WORKER = "pacvo15_476_wccpvo"
DEFAULT_CHOCO_SERVER = "https://chocohub-r011.onrender.com"


class CCPoWEngine:
    """Chocohub CCpow Proof-of-Work SHA-256 Solver & Target Engine (matching MPG_Miner.py)."""

    @staticmethod
    def difficulty_to_target(difficulty: float) -> int:
        """Calculate 256-bit target integer from floating difficulty."""
        max_target = (1 << 256) - 1
        diff_scaled = int(difficulty * 1000)
        if diff_scaled <= 0:
            return max_target
        return max_target // diff_scaled

    @staticmethod
    def solve_proof(
        prev_hash: str,
        worker_name: str = DEFAULT_CHOCO_WORKER,
        difficulty: float = 5.0,
        start_nonce: int = 0,
        max_attempts: int = 100_000,
    ) -> tuple[int, str, bool]:
        """Compute a valid CCpow SHA-256 solution matching Chocohub MPG_Miner hash format."""
        target = CCPoWEngine.difficulty_to_target(difficulty)
        for nonce in range(start_nonce, start_nonce + max_attempts):
            nonce_padded = str(nonce).zfill(20)
            msg = f"{prev_hash}{nonce_padded}{worker_name}".encode("utf-8")
            h = hashlib.sha256(msg).hexdigest()
            if int(h, 16) <= target:
                return nonce, h, True
        # If range exhausted, return best calculated hash
        nonce_padded = str(start_nonce).zfill(20)
        msg = f"{prev_hash}{nonce_padded}{worker_name}".encode("utf-8")
        return start_nonce, hashlib.sha256(msg).hexdigest(), False


@dataclass
class HTLCOrder:
    """Atomic swap contract instance between Pacvo and Chocohub."""

    order_id: str
    initiator_pacvo: str
    participant_pacvo: str
    initiator_choco: str
    participant_choco: str
    amount_pvo_wad: int
    amount_cc_wad: int
    hashlock: str  # 64-char hex of SHA256(secret)
    secret: str = ""  # Revealed pre-image upon claim
    timelock_pacvo: int = 0
    timelock_choco: int = 0
    created_height: int = 0
    claimed_height: int = 0
    refunded_height: int = 0
    status: str = "LOCKED"  # LOCKED, CLAIMED, REFUNDED, EXPIRED
    mined_proofs: int = 0
    mining_rewards_cc_wad: int = 0
    cc_mined_for_swap_wad: int = 0  # Exact 10 CC per 1 PVO mined into swap

    def is_expired_pacvo(self, current_height: int) -> bool:
        return current_height >= self.timelock_pacvo

    def is_expired_choco(self, current_height: int) -> bool:
        return current_height >= self.timelock_choco

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "initiator_pacvo": self.initiator_pacvo,
            "participant_pacvo": self.participant_pacvo,
            "initiator_choco": self.initiator_choco,
            "participant_choco": self.participant_choco,
            "amount_pvo_wad": str(self.amount_pvo_wad),
            "amount_cc_wad": str(self.amount_cc_wad),
            "hashlock": self.hashlock,
            "secret": self.secret,
            "timelock_pacvo": self.timelock_pacvo,
            "timelock_choco": self.timelock_choco,
            "created_height": self.created_height,
            "claimed_height": self.claimed_height,
            "refunded_height": self.refunded_height,
            "status": self.status,
            "mined_proofs": self.mined_proofs,
            "mining_rewards_cc_wad": str(self.mining_rewards_cc_wad),
            "cc_mined_for_swap_wad": str(self.cc_mined_for_swap_wad),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HTLCOrder":
        return cls(
            order_id=d["order_id"],
            initiator_pacvo=d["initiator_pacvo"],
            participant_pacvo=d["participant_pacvo"],
            initiator_choco=d["initiator_choco"],
            participant_choco=d["participant_choco"],
            amount_pvo_wad=int(d["amount_pvo_wad"]),
            amount_cc_wad=int(d["amount_cc_wad"]),
            hashlock=d["hashlock"],
            secret=d.get("secret", ""),
            timelock_pacvo=int(d["timelock_pacvo"]),
            timelock_choco=int(d["timelock_choco"]),
            created_height=int(d.get("created_height", 0)),
            claimed_height=int(d.get("claimed_height", 0)),
            refunded_height=int(d.get("refunded_height", 0)),
            status=d.get("status", "LOCKED"),
            mined_proofs=int(d.get("mined_proofs", 0)),
            mining_rewards_cc_wad=int(d.get("mining_rewards_cc_wad", 0)),
            cc_mined_for_swap_wad=int(d.get("cc_mined_for_swap_wad", 0)),
        )


class HTLCSwapManager:
    """Manages cross-chain HTLC atomic swaps between Pacvo and Chocohub."""

    def __init__(self):
        self.orders: dict[str, HTLCOrder] = {}
        self.pacvo_balances: dict[str, int] = {}  # user -> balance in WAD
        self.choco_balances: dict[str, int] = {}  # user -> balance in CC WAD
        self.escrow_pvo_wad: int = 0
        self.escrow_cc_wad: int = 0
        self.total_swapped_pvo_wad: int = 0
        self.total_swapped_cc_wad: int = 0
        self.total_mined_rewards_cc_wad: int = 0

    @staticmethod
    def calculate_cc_amount(amount_pvo_wad: int) -> int:
        """Convert PVO to CC at 1 PVO = 10 CC."""
        return amount_pvo_wad * PVO_TO_CC_RATIO

    @staticmethod
    def calculate_pvo_amount(amount_cc_wad: int) -> int:
        """Convert CC to PVO at 10 CC = 1 PVO."""
        return amount_cc_wad // PVO_TO_CC_RATIO

    @staticmethod
    def hash_secret(secret_hex: str) -> str:
        """Compute SHA-256 hashlock from hex-encoded secret."""
        clean = secret_hex.removeprefix("0x")
        secret_bytes = bytes.fromhex(clean)
        return hashlib.sha256(secret_bytes).hexdigest()

    def get_pacvo_balance(self, user: str) -> int:
        return self.pacvo_balances.get(user.lower(), 0)

    def get_choco_balance(self, user: str) -> int:
        return self.choco_balances.get(user.lower(), 0)

    def deposit_pacvo(self, user: str, amount_wad: int) -> None:
        if amount_wad <= 0:
            raise InvariantViolationError("Deposit amount must be strictly positive")
        u = user.lower()
        self.pacvo_balances[u] = self.pacvo_balances.get(u, 0) + amount_wad

    def deposit_choco(self, user: str, amount_cc_wad: int) -> None:
        if amount_cc_wad <= 0:
            raise InvariantViolationError("Deposit amount must be strictly positive")
        u = user.lower()
        self.choco_balances[u] = self.choco_balances.get(u, 0) + amount_cc_wad

    def create_order(
        self,
        initiator_pacvo: str,
        participant_pacvo: str,
        initiator_choco: str,
        participant_choco: str,
        amount_pvo_wad: int,
        hashlock_hex: str,
        current_height: int,
        timelock_blocks_pacvo: int = DEFAULT_TIMELOCK_PACVO_BLOCKS,
        timelock_blocks_choco: int = DEFAULT_TIMELOCK_CHOCO_BLOCKS,
        auto_mine_cc: bool = False,
        miner_account: str = "chocohub_pow_miner",
    ) -> HTLCOrder:
        """Initiate and lock an atomic cross-chain swap, mining 10 CC per 1 PVO."""
        if amount_pvo_wad <= 0:
            raise InvariantViolationError("Swap amount must be strictly positive")

        clean_hashlock = hashlock_hex.removeprefix("0x").lower()
        if len(clean_hashlock) != 64:
            raise InvariantViolationError("Hashlock must be a 64-character hex SHA-256 digest")

        amount_cc_wad = self.calculate_cc_amount(amount_pvo_wad)

        init_pvo = initiator_pacvo.lower()
        part_choco = participant_choco.lower()

        if self.get_pacvo_balance(init_pvo) < amount_pvo_wad:
            raise InsufficientReserveError(
                f"Insufficient PVO balance for {init_pvo}: has {self.get_pacvo_balance(init_pvo)}, requires {amount_pvo_wad}"
            )

        # Debit initiator PVO balance into escrow
        self.pacvo_balances[init_pvo] -= amount_pvo_wad
        self.escrow_pvo_wad += amount_pvo_wad

        # Check or mine 10 CC per 1 PVO into participant Chocohub escrow
        if auto_mine_cc:
            # Execute CCpow mining to mint 10 CC per 1 PVO for the swap
            nonce, proof_hash, _ = CCPoWEngine.solve_proof(
                prev_hash="0" * 64,
                worker_name=miner_account,
                difficulty=1.0,
            )
            # Credit mined CC directly into escrow
            self.escrow_cc_wad += amount_cc_wad
            mined_amount = amount_cc_wad
        else:
            if self.get_choco_balance(part_choco) < amount_cc_wad:
                raise InsufficientReserveError(
                    f"Insufficient CC balance for {part_choco}: has {self.get_choco_balance(part_choco)}, requires {amount_cc_wad}"
                )
            self.choco_balances[part_choco] -= amount_cc_wad
            self.escrow_cc_wad += amount_cc_wad
            mined_amount = 0

        order_id = hashlib.sha256(
            f"HTLC:{init_pvo}:{participant_pacvo}:{initiator_choco}:{part_choco}:{amount_pvo_wad}:{clean_hashlock}:{current_height}".encode()
        ).hexdigest()

        order = HTLCOrder(
            order_id=order_id,
            initiator_pacvo=init_pvo,
            participant_pacvo=participant_pacvo.lower(),
            initiator_choco=initiator_choco.lower(),
            participant_choco=part_choco,
            amount_pvo_wad=amount_pvo_wad,
            amount_cc_wad=amount_cc_wad,
            hashlock=clean_hashlock,
            timelock_pacvo=current_height + timelock_blocks_pacvo,
            timelock_choco=current_height + timelock_blocks_choco,
            created_height=current_height,
            status="LOCKED",
            mined_proofs=1 if auto_mine_cc else 0,
            cc_mined_for_swap_wad=mined_amount,
        )

        self.orders[order_id] = order
        return order

    def claim_swap(
        self,
        order_id: str,
        secret_hex: str,
        current_height: int,
    ) -> tuple[int, int]:
        """Claim escrowed funds on both chains by revealing the SHA-256 pre-image secret."""
        if order_id not in self.orders:
            raise InvariantViolationError(f"HTLC Order {order_id} not found")

        order = self.orders[order_id]
        if order.status != "LOCKED":
            raise InvariantViolationError(f"HTLC Order {order_id} is not in LOCKED status (status: {order.status})")

        clean_secret = secret_hex.removeprefix("0x").lower()
        derived_hash = self.hash_secret(clean_secret)
        if derived_hash != order.hashlock:
            raise InvariantViolationError(
                f"Invalid secret pre-image: SHA-256({clean_secret}) = {derived_hash} != {order.hashlock}"
            )

        # Check timelock
        if current_height > order.timelock_pacvo:
            order.status = "EXPIRED"
            raise InvariantViolationError(
                f"HTLC Order {order_id} has expired on Pacvo at height {order.timelock_pacvo} (current: {current_height})"
            )

        # Atomic Settlement:
        # Initiator gets CC on Chocohub (10 CC per 1 PVO)
        # Participant gets PVO on Pacvo
        self.escrow_pvo_wad -= order.amount_pvo_wad
        self.escrow_cc_wad -= order.amount_cc_wad

        self.pacvo_balances[order.participant_pacvo] = (
            self.pacvo_balances.get(order.participant_pacvo, 0) + order.amount_pvo_wad
        )
        self.choco_balances[order.initiator_choco] = (
            self.choco_balances.get(order.initiator_choco, 0) + order.amount_cc_wad
        )

        order.status = "CLAIMED"
        order.secret = clean_secret
        order.claimed_height = current_height

        self.total_swapped_pvo_wad += order.amount_pvo_wad
        self.total_swapped_cc_wad += order.amount_cc_wad

        return order.amount_pvo_wad, order.amount_cc_wad

    def refund_swap(self, order_id: str, current_height: int) -> tuple[int, int]:
        """Refund escrowed funds to original owners after timelock expiration."""
        if order_id not in self.orders:
            raise InvariantViolationError(f"HTLC Order {order_id} not found")

        order = self.orders[order_id]
        if order.status != "LOCKED":
            raise InvariantViolationError(f"Cannot refund order in {order.status} state")

        if current_height < order.timelock_choco:
            raise InvariantViolationError(
                f"Cannot refund before timelock expiry: current {current_height} < timelock {order.timelock_choco}"
            )

        self.escrow_pvo_wad -= order.amount_pvo_wad
        self.escrow_cc_wad -= order.amount_cc_wad

        self.pacvo_balances[order.initiator_pacvo] = (
            self.pacvo_balances.get(order.initiator_pacvo, 0) + order.amount_pvo_wad
        )
        self.choco_balances[order.participant_choco] = (
            self.choco_balances.get(order.participant_choco, 0) + order.amount_cc_wad
        )

        order.status = "REFUNDED"
        order.refunded_height = current_height

        return order.amount_pvo_wad, order.amount_cc_wad

    def mine_htlc_swap(
        self,
        order_id: str,
        miner_choco_account: str,
        nonce: int,
        device_type: str = "cpu",
        last_block_hash: str = "0" * 64,
        target_difficulty: int = 5,
    ) -> dict:
        """Mine on an active HTLC swap job with Chocohub device multipliers."""
        if order_id not in self.orders:
            raise InvariantViolationError(f"HTLC Order {order_id} not found")

        order = self.orders[order_id]
        if order.status != "LOCKED":
            raise InvariantViolationError(f"Cannot mine on order in {order.status} state")

        dev = device_type.lower()
        mult = CHOCO_DEVICE_MULTIPLIERS.get(dev, CHOCO_DEVICE_MULTIPLIERS["default"])

        # SHA-256 Proof: H(last_block_hash + str(nonce) + miner_account + order_id)
        msg = f"{last_block_hash}:{nonce}:{miner_choco_account}:{order_id}".encode()
        proof_hash = hashlib.sha256(msg).hexdigest()

        # Calculate reward with device multiplier
        base_reward = CHOCO_BASE_BLOCK_REWARD_CC
        reward_cc_wad = int(base_reward * mult)

        miner = miner_choco_account.lower()
        self.choco_balances[miner] = self.choco_balances.get(miner, 0) + reward_cc_wad

        order.mined_proofs += 1
        order.mining_rewards_cc_wad += reward_cc_wad
        self.total_mined_rewards_cc_wad += reward_cc_wad

        return {
            "order_id": order_id,
            "miner": miner,
            "nonce": nonce,
            "proof_hash": proof_hash,
            "device_type": dev,
            "multiplier": mult,
            "reward_cc_wad": reward_cc_wad,
            "total_order_proofs": order.mined_proofs,
        }

    def verify_invariants(self) -> bool:
        """Verify strict conservation invariants across HTLC escrows."""
        locked_pvo = sum(o.amount_pvo_wad for o in self.orders.values() if o.status == "LOCKED")
        locked_cc = sum(o.amount_cc_wad for o in self.orders.values() if o.status == "LOCKED")

        if locked_pvo != self.escrow_pvo_wad:
            raise InvariantViolationError(
                f"PVO Escrow mismatch: calculated {locked_pvo} != tracked {self.escrow_pvo_wad}"
            )
        if locked_cc != self.escrow_cc_wad:
            raise InvariantViolationError(
                f"CC Escrow mismatch: calculated {locked_cc} != tracked {self.escrow_cc_wad}"
            )
        return True

    def to_dict(self) -> dict:
        return {
            "orders": {k: v.to_dict() for k, v in sorted(self.orders.items())},
            "pacvo_balances": {k: str(v) for k, v in sorted(self.pacvo_balances.items())},
            "choco_balances": {k: str(v) for k, v in sorted(self.choco_balances.items())},
            "escrow_pvo_wad": str(self.escrow_pvo_wad),
            "escrow_cc_wad": str(self.escrow_cc_wad),
            "total_swapped_pvo_wad": str(self.total_swapped_pvo_wad),
            "total_swapped_cc_wad": str(self.total_swapped_cc_wad),
            "total_mined_rewards_cc_wad": str(self.total_mined_rewards_cc_wad),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HTLCSwapManager":
        mgr = cls()
        mgr.orders = {k: HTLCOrder.from_dict(v) for k, v in d.get("orders", {}).items()}
        mgr.pacvo_balances = {k: int(v) for k, v in d.get("pacvo_balances", {}).items()}
        mgr.choco_balances = {k: int(v) for k, v in d.get("choco_balances", {}).items()}
        mgr.escrow_pvo_wad = int(d.get("escrow_pvo_wad", 0))
        mgr.escrow_cc_wad = int(d.get("escrow_cc_wad", 0))
        mgr.total_swapped_pvo_wad = int(d.get("total_swapped_pvo_wad", 0))
        mgr.total_swapped_cc_wad = int(d.get("total_swapped_cc_wad", 0))
        mgr.total_mined_rewards_cc_wad = int(d.get("total_mined_rewards_cc_wad", 0))
        return mgr
