import time

from pacvo.crypto import canonical_json, derive_address, sha512_hex, sign_message, verify_signature


class Transaction:
    def __init__(
        self,
        sender_public_key: bytes = b"",
        recipient: str = "",
        amount: int = 0,
        fee: int = 0,
        nonce: int = 0,
        timestamp: int = 0,
        stake_amount: int = 0,
        signature: bytes = b"",
    ) -> None:
        self.sender_public_key = sender_public_key
        self.recipient = recipient
        self.amount = amount
        self.fee = fee
        self.nonce = nonce
        self.timestamp = timestamp
        self.stake_amount = stake_amount
        self.signature = signature

    @property
    def sender(self) -> str:
        if self.sender_public_key == b"":
            return "COINBASE"
        return derive_address(self.sender_public_key)

    @property
    def is_coinbase(self) -> bool:
        return self.sender_public_key == b""

    def to_dict(self) -> dict:
        return {
            "sender_public_key": self.sender_public_key.hex(),
            "recipient": self.recipient,
            "amount": self.amount,
            "fee": self.fee,
            "nonce": self.nonce,
            "timestamp": self.timestamp,
            "stake_amount": self.stake_amount,
            "signature": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        return cls(
            sender_public_key=bytes.fromhex(d["sender_public_key"]),
            recipient=d["recipient"],
            amount=d["amount"],
            fee=d["fee"],
            nonce=d["nonce"],
            timestamp=d["timestamp"],
            stake_amount=d["stake_amount"],
            signature=bytes.fromhex(d["signature"]),
        )

    def signing_payload(self) -> bytes:
        payload = self.to_dict()
        del payload["signature"]
        return canonical_json(payload)

    @property
    def txid(self) -> str:
        return sha512_hex(canonical_json(self.to_dict()))

    def sign(self, secret_key: bytes) -> None:
        self.signature = sign_message(secret_key, self.signing_payload())

    def verify_signature(self) -> bool:
        if self.is_coinbase:
            return self.signature == b""
        return verify_signature(self.sender_public_key, self.signing_payload(), self.signature)

    @classmethod
    def coinbase(cls, recipient: str, spendable: int, stake: int, height: int) -> "Transaction":
        return cls(
            sender_public_key=b"",
            recipient=recipient,
            amount=spendable,
            fee=0,
            nonce=height,
            timestamp=int(time.time()),
            stake_amount=stake,
            signature=b"",
        )
