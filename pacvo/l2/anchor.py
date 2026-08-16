"""L2 State Commitment and L1 Anchoring Protocol."""

from dataclasses import dataclass
import json
import time

from pacvo.crypto import canonical_json, keccak256_hex
from pacvo.evm.state import EVMState


@dataclass
class L2Anchor:
    """Commitment linking canonical L2 state to an L1 block position."""

    l1_height: int
    l1_block_hash: str
    l2_sequence: int
    state_root: str
    timestamp: int = 0

    def to_dict(self) -> dict:
        return {
            "l1_height": self.l1_height,
            "l1_block_hash": self.l1_block_hash,
            "l2_sequence": self.l2_sequence,
            "state_root": self.state_root,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "L2Anchor":
        return cls(
            l1_height=d["l1_height"],
            l1_block_hash=d["l1_block_hash"],
            l2_sequence=d["l2_sequence"],
            state_root=d["state_root"],
            timestamp=d.get("timestamp", 0),
        )


def compute_l2_state_root(evm_state: EVMState) -> str:
    """Compute a deterministic 32-byte Keccak-256 state commitment over all L2 contract storage."""
    payload = {}
    for addr in sorted(evm_state.accounts.keys()):
        acc = evm_state.accounts[addr]
        storage_map = {str(k): str(v) for k, v in sorted(acc.storage.items()) if v != 0}
        payload[addr] = {
            "balance": acc.balance,
            "nonce": acc.nonce,
            "code_hash": keccak256_hex(acc.code),
            "storage": storage_map,
        }
    serialized = canonical_json(payload)
    return keccak256_hex(serialized)
