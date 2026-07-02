from pacvo.crypto import canonical_json, sha512_hex
from pacvo.transaction import Transaction


class Block:
    def __init__(
        self,
        height: int,
        prev_hash: str,
        merkle_root: str,
        timestamp: int,
        target: int,
        nonce: int,
        transactions: list[Transaction],
    ) -> None:
        self.height = height
        self.prev_hash = prev_hash
        self.merkle_root = merkle_root
        self.timestamp = timestamp
        self.target = target
        self.nonce = nonce
        self.transactions = transactions

    @staticmethod
    def compute_merkle_root(txids: list[str]) -> str:
        if not txids:
            return sha512_hex(b"")
        layer = list(txids)
        while len(layer) > 1:
            if len(layer) % 2 == 1:
                layer.append(layer[-1])
            next_layer = []
            for i in range(0, len(layer), 2):
                next_layer.append(sha512_hex((layer[i] + layer[i + 1]).encode()))
            layer = next_layer
        return layer[0]

    def header_dict(self) -> dict:
        return {
            "height": self.height,
            "prev_hash": self.prev_hash,
            "merkle_root": self.merkle_root,
            "timestamp": self.timestamp,
            "target": format(self.target, "x"),
            "nonce": self.nonce,
        }

    def header_bytes(self) -> bytes:
        return canonical_json(self.header_dict())

    @property
    def block_hash(self) -> str:
        return sha512_hex(self.header_bytes())

    def meets_target(self) -> bool:
        return int(self.block_hash, 16) <= self.target

    @property
    def work(self) -> int:
        return (2**512) // (self.target + 1)

    def to_dict(self) -> dict:
        d = self.header_dict()
        d["transactions"] = [tx.to_dict() for tx in self.transactions]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(
            height=d["height"],
            prev_hash=d["prev_hash"],
            merkle_root=d["merkle_root"],
            timestamp=d["timestamp"],
            target=int(d["target"], 16),
            nonce=d["nonce"],
            transactions=[Transaction.from_dict(tx) for tx in d["transactions"]],
        )
