import time

from pacvo.crypto import canonical_json, derive_address, derive_evm_address, sha512_hex, sign_message, verify_signature


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
        evm_to: str = "",
        evm_data: bytes = b"",
        evm_gas_limit: int = 0,
        evm_value: int = 0,
    ) -> None:
        self.sender_public_key = sender_public_key
        self.recipient = recipient
        self.amount = amount
        self.fee = fee
        self.nonce = nonce
        self.timestamp = timestamp
        self.stake_amount = stake_amount
        self.signature = signature
        self.evm_to = evm_to
        self.evm_data = evm_data
        self.evm_gas_limit = evm_gas_limit
        self.evm_value = evm_value

    @property
    def sender(self) -> str:
        if self.sender_public_key == b"":
            return "COINBASE"
        return derive_address(self.sender_public_key)

    @property
    def sender_evm(self) -> str:
        if self.sender_public_key == b"":
            return ""
        return derive_evm_address(self.sender_public_key)

    @property
    def is_coinbase(self) -> bool:
        return self.sender_public_key == b""

    @property
    def is_evm(self) -> bool:
        return len(self.evm_data) > 0 or self.evm_gas_limit > 0 or bool(self.evm_to)

    def to_dict(self) -> dict:
        d = {
            "sender_public_key": self.sender_public_key.hex(),
            "recipient": self.recipient,
            "amount": self.amount,
            "fee": self.fee,
            "nonce": self.nonce,
            "timestamp": self.timestamp,
            "stake_amount": self.stake_amount,
            "signature": self.signature.hex(),
        }
        if self.is_evm:
            d["evm_to"] = self.evm_to
            d["evm_data"] = self.evm_data.hex()
            d["evm_gas_limit"] = self.evm_gas_limit
            d["evm_value"] = self.evm_value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        return cls(
            sender_public_key=bytes.fromhex(d["sender_public_key"]),
            recipient=d.get("recipient", ""),
            amount=d.get("amount", 0),
            fee=d.get("fee", 0),
            nonce=d.get("nonce", 0),
            timestamp=d.get("timestamp", 0),
            stake_amount=d.get("stake_amount", 0),
            signature=bytes.fromhex(d["signature"]),
            evm_to=d.get("evm_to", ""),
            evm_data=bytes.fromhex(d.get("evm_data", "")),
            evm_gas_limit=d.get("evm_gas_limit", 0),
            evm_value=d.get("evm_value", 0),
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
