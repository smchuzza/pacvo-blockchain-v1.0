import json

from pacvo.crypto import derive_address, generate_sign_keypair, sign_message


class Wallet:
    def __init__(self, sign_public_key: bytes, sign_secret_key: bytes) -> None:
        self.sign_public_key = sign_public_key
        self.sign_secret_key = sign_secret_key

    @property
    def address(self) -> str:
        return derive_address(self.sign_public_key)

    @classmethod
    def generate(cls) -> "Wallet":
        public_key, secret_key = generate_sign_keypair()
        return cls(public_key, secret_key)

    def save(self, path: str) -> None:
        data = {
            "sign_public_key": self.sign_public_key.hex(),
            "sign_secret_key": self.sign_secret_key.hex(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    @classmethod
    def load(cls, path: str) -> "Wallet":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(bytes.fromhex(data["sign_public_key"]), bytes.fromhex(data["sign_secret_key"]))

    def sign(self, message: bytes) -> bytes:
        return sign_message(self.sign_secret_key, message)
