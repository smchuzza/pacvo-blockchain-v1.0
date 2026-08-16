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


def is_valid_address(addr) -> bool:
    if not isinstance(addr, str) or not addr.startswith("pvo1") or len(addr) != 132:
        return False
    hexpart = addr[4:]
    return len(hexpart) == 128 and all(c in "0123456789abcdef" for c in hexpart)


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


# --- Keccak-256 and EVM / RLP Primitives ---

_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008
]

_KECCAK_ROT_SHIFTS = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14]
]


def keccak256(data: bytes) -> bytes:
    r = 136  # Rate for Keccak-256 = 1088 bits = 136 bytes
    padlen = r - (len(data) % r)
    if padlen == 1:
        padded = data + b"\x81"
    else:
        padded = data + b"\x01" + b"\x00" * (padlen - 2) + b"\x80"

    state = [0] * 25

    def _rot(x: int, n: int) -> int:
        n %= 64
        return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF

    for offset in range(0, len(padded), r):
        block = padded[offset : offset + r]
        for i in range(r // 8):
            val = int.from_bytes(block[i * 8 : (i + 1) * 8], "little")
            state[i] ^= val

        st = [[state[x + 5 * y] for y in range(5)] for x in range(5)]
        for round_idx in range(24):
            # Theta
            C = [st[x][0] ^ st[x][1] ^ st[x][2] ^ st[x][3] ^ st[x][4] for x in range(5)]
            D = [C[(x - 1) % 5] ^ _rot(C[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    st[x][y] ^= D[x]
            # Rho and Pi
            B = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    B[y][(2 * x + 3 * y) % 5] = _rot(st[x][y], _KECCAK_ROT_SHIFTS[x][y])
            # Chi
            for x in range(5):
                for y in range(5):
                    st[x][y] = B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y]) & 0xFFFFFFFFFFFFFFFF
            # Iota
            st[0][0] ^= _KECCAK_RC[round_idx]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = st[x][y]

    out = bytearray()
    for i in range(4):
        out.extend(state[i].to_bytes(8, "little"))
    return bytes(out)


def keccak256_hex(data: bytes) -> str:
    return keccak256(data).hex()


def rlp_encode(item) -> bytes:
    if isinstance(item, int):
        if item == 0:
            return b"\x80"
        b = item.to_bytes((item.bit_length() + 7) // 8, "big")
        return rlp_encode(b)
    elif isinstance(item, (bytes, bytearray)):
        b = bytes(item)
        if len(b) == 1 and b[0] < 0x80:
            return b
        elif len(b) <= 55:
            return bytes([0x80 + len(b)]) + b
        else:
            len_bytes = (len(b)).to_bytes(((len(b)).bit_length() + 7) // 8, "big")
            return bytes([0xb7 + len(len_bytes)]) + len_bytes + b
    elif isinstance(item, (list, tuple)):
        payload = b"".join(rlp_encode(x) for x in item)
        if len(payload) <= 55:
            return bytes([0xc0 + len(payload)]) + payload
        else:
            len_bytes = (len(payload)).to_bytes(((len(payload)).bit_length() + 7) // 8, "big")
            return bytes([0xf7 + len(len_bytes)]) + len_bytes + payload
    raise TypeError(f"Unsupported RLP type: {type(item)}")


def derive_evm_address(sign_public_key: bytes) -> str:
    digest = keccak256(sign_public_key)
    return "0x" + digest[-20:].hex()


def derive_create_address(sender_evm: str, nonce: int) -> str:
    clean_sender = sender_evm.lower()
    if clean_sender.startswith("0x"):
        clean_sender = clean_sender[2:]
    sender_bytes = bytes.fromhex(clean_sender)
    encoded = rlp_encode([sender_bytes, nonce])
    digest = keccak256(encoded)
    return "0x" + digest[-20:].hex()


def derive_create2_address(sender_evm: str, salt: bytes, init_code: bytes) -> str:
    clean_sender = sender_evm.lower()
    if clean_sender.startswith("0x"):
        clean_sender = clean_sender[2:]
    sender_bytes = bytes.fromhex(clean_sender)
    init_code_hash = keccak256(init_code)
    salt_32 = salt.rjust(32, b"\x00")[:32]
    payload = b"\xff" + sender_bytes + salt_32 + init_code_hash
    digest = keccak256(payload)
    return "0x" + digest[-20:].hex()


def is_valid_evm_address(addr) -> bool:
    if not isinstance(addr, str) or not addr.startswith("0x") or len(addr) != 42:
        return False
    hexpart = addr[2:]
    return len(hexpart) == 40 and all(c in "0123456789abcdefABCDEF" for c in hexpart)

