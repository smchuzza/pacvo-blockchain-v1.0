import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pqcrypto.kem import ml_kem_768
from pqcrypto.sign import sphincs_sha2_256s_simple

_NONCE_SIZE = 12


def sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def sha512_hex(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def generate_sign_keypair() -> tuple[bytes, bytes]:
    return sphincs_sha2_256s_simple.generate_keypair()


def sign_message(secret_key: bytes, message: bytes) -> bytes:
    return sphincs_sha2_256s_simple.sign(secret_key, message)


def verify_signature(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        return sphincs_sha2_256s_simple.verify(public_key, message, signature)
    except Exception:
        return False


def generate_kem_keypair() -> tuple[bytes, bytes]:
    return ml_kem_768.generate_keypair()


def kem_encapsulate(public_key: bytes) -> tuple[bytes, bytes]:
    return ml_kem_768.encrypt(public_key)


def kem_decapsulate(secret_key: bytes, ciphertext: bytes) -> bytes:
    return ml_kem_768.decrypt(secret_key, ciphertext)


def derive_address(sign_public_key: bytes) -> str:
    return "pvo1" + sha512(sign_public_key).hex()


def identity_fingerprint(public_key: bytes) -> str:
    return sha512(public_key)[:16].hex()


def encrypt_payload(key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def decrypt_payload(key: bytes, blob: bytes) -> bytes:
    nonce = blob[:_NONCE_SIZE]
    ciphertext = blob[_NONCE_SIZE:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)
