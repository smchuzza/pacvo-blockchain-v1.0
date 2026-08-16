"""L3 Economic State Commitment and L1 Anchoring Protocol."""

from dataclasses import dataclass
import json
import time

from pacvo.crypto import canonical_json, keccak256_hex
from pacvo.l3.economy import Economy


@dataclass
class L3Anchor:
    """Links canonical L3 economic state to an L1 block position."""

    l1_height: int
    l1_block_hash: str
    l3_epoch: int
    state_root: str
    timestamp: int = 0

    def to_dict(self) -> dict:
        return {
            "l1_height": self.l1_height,
            "l1_block_hash": self.l1_block_hash,
            "l3_epoch": self.l3_epoch,
            "state_root": self.state_root,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "L3Anchor":
        return cls(
            l1_height=d["l1_height"],
            l1_block_hash=d["l1_block_hash"],
            l3_epoch=d["l3_epoch"],
            state_root=d["state_root"],
            timestamp=d.get("timestamp", 0),
        )


def compute_l3_state_root(economy: Economy) -> str:
    """Compute a deterministic 32-byte Keccak-256 state commitment over all L3 subsystems."""
    payload = economy.to_dict()
    serialized = canonical_json(payload)
    return keccak256_hex(serialized)
