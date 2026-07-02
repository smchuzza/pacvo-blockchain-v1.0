import json
import os

import bcrypt

from pacvo.crypto import decrypt_payload, derive_address, encrypt_payload, generate_sign_keypair, sign_message

BCRYPT_ROUNDS = 100


class WalletError(Exception):
    pass


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

    @staticmethod
    def _derive_key(passphrase: str, salt: bytes) -> bytes:
        return bcrypt.kdf(
            password=passphrase.encode(),
            salt=salt,
            desired_key_bytes=32,
            rounds=BCRYPT_ROUNDS,
        )

    def save(self, path: str, passphrase: str) -> None:
        salt = os.urandom(16)
        key = self._derive_key(passphrase, salt)
        enc_secret_key = encrypt_payload(key, self.sign_secret_key)
        data = {
            "sign_public_key": self.sign_public_key.hex(),
            "kdf": "bcrypt",
            "salt": salt.hex(),
            "rounds": BCRYPT_ROUNDS,
            "enc_secret_key": enc_secret_key.hex(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    @classmethod
    def load(cls, path: str, passphrase: str) -> "Wallet":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("kdf") != "bcrypt":
            raise WalletError("unsupported wallet format")
        salt = bytes.fromhex(data["salt"])
        key = cls._derive_key(passphrase, salt)
        try:
            secret_key = decrypt_payload(key, bytes.fromhex(data["enc_secret_key"]))
        except Exception as exc:
            raise WalletError("wrong passphrase or corrupted wallet file") from exc
        public_key = bytes.fromhex(data["sign_public_key"])
        return cls(public_key, secret_key)

    def sign(self, message: bytes) -> bytes:
        return sign_message(self.sign_secret_key, message)
