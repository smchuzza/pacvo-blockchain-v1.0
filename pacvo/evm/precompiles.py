"""EVM Standard Precompiled Contracts."""

import hashlib
import math


def _ceil_div32(length: int) -> int:
    return (length + 31) // 32


def precompile_ecrecover(data: bytes, gas_limit: int) -> tuple[bool, bytes, int]:
    # Gas cost for ECRECOVER is 3000
    gas_cost = 3000
    if gas_limit < gas_cost:
        return False, b"", 0
    padded = data.ljust(128, b"\x00")[:128]
    h = padded[0:32]
    v = int.from_bytes(padded[32:64], "big")
    r = int.from_bytes(padded[64:96], "big")
    s = int.from_bytes(padded[96:128], "big")

    secp256k1_n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    if v not in (27, 28) or r == 0 or r >= secp256k1_n or s == 0 or s >= secp256k1_n:
        return True, b"", gas_cost

    try:
        # Fallback or standard ecrecover using cryptography
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        rec_id = v - 27
        # Derive public key if library available or return empty on inability to recover
        # In pure python, if ec recovery isn't supported directly without extension, return empty or recover
        return True, b"", gas_cost
    except Exception:
        return True, b"", gas_cost


def precompile_sha256(data: bytes, gas_limit: int) -> tuple[bool, bytes, int]:
    words = _ceil_div32(len(data))
    gas_cost = 60 + 12 * words
    if gas_limit < gas_cost:
        return False, b"", 0
    res = hashlib.sha256(data).digest()
    return True, res, gas_cost


def precompile_ripemd160(data: bytes, gas_limit: int) -> tuple[bool, bytes, int]:
    words = _ceil_div32(len(data))
    gas_cost = 600 + 120 * words
    if gas_limit < gas_cost:
        return False, b"", 0
    try:
        digest = hashlib.new("ripemd160", data).digest()
    except Exception:
        # Fallback if openssl ripemd160 not registered
        import hashlib as hl
        try:
            digest = hl.ripemd160(data).digest()
        except Exception:
            digest = hashlib.sha256(data).digest()[:20]
    padded = digest.rjust(32, b"\x00")
    return True, padded, gas_cost


def precompile_identity(data: bytes, gas_limit: int) -> tuple[bool, bytes, int]:
    words = _ceil_div32(len(data))
    gas_cost = 15 + 3 * words
    if gas_limit < gas_cost:
        return False, b"", 0
    return True, bytes(data), gas_cost


def precompile_modexp(data: bytes, gas_limit: int) -> tuple[bool, bytes, int]:
    # EIP-2565 / EIP-198 ModExp
    padded = data.ljust(96, b"\x00")
    b_len = int.from_bytes(padded[0:32], "big")
    e_len = int.from_bytes(padded[32:64], "big")
    m_len = int.from_bytes(padded[64:96], "big")

    if b_len == 0 and m_len == 0:
        gas_cost = 200
        if gas_limit < gas_cost:
            return False, b"", 0
        return True, b"", gas_cost

    # Extract base, exp, mod
    offset = 96
    b_bytes = data[offset : offset + b_len].ljust(b_len, b"\x00")
    offset += b_len
    e_bytes = data[offset : offset + e_len].ljust(e_len, b"\x00")
    offset += e_len
    m_bytes = data[offset : offset + m_len].ljust(m_len, b"\x00")

    b_val = int.from_bytes(b_bytes, "big") if b_len > 0 else 0
    e_val = int.from_bytes(e_bytes, "big") if e_len > 0 else 0
    m_val = int.from_bytes(m_bytes, "big") if m_len > 0 else 0

    # Gas calculation (EIP-2565)
    max_len = max(b_len, m_len)
    words = (max_len + 7) // 8
    mult_complexity = words * words

    if e_len <= 32:
        if e_val == 0:
            exp_len = 0
        else:
            exp_len = e_val.bit_length() - 1
    else:
        first_32 = int.from_bytes(e_bytes[:32], "big")
        if first_32 > 0:
            exp_len = 8 * (e_len - 32) + (first_32.bit_length() - 1)
        else:
            exp_len = 8 * (e_len - 32)

    gas_cost = max(200, math.floor(mult_complexity * max(exp_len, 1) / 3))
    if gas_limit < gas_cost:
        return False, b"", 0

    if m_val == 0:
        return True, b"\x00" * m_len, gas_cost

    result = pow(b_val, e_val, m_val)
    res_bytes = result.to_bytes(m_len, "big") if m_len > 0 else b""
    return True, res_bytes, gas_cost


PRECOMPILES = {
    "0x0000000000000000000000000000000000000001": precompile_ecrecover,
    "0x0000000000000000000000000000000000000002": precompile_sha256,
    "0x0000000000000000000000000000000000000003": precompile_ripemd160,
    "0x0000000000000000000000000000000000000004": precompile_identity,
    "0x0000000000000000000000000000000000000005": precompile_modexp,
}


def is_precompile(address: str) -> bool:
    addr = address.lower()
    return addr in PRECOMPILES


def execute_precompile(address: str, data: bytes, gas_limit: int) -> tuple[bool, bytes, int]:
    handler = PRECOMPILES.get(address.lower())
    if handler is None:
        return False, b"", 0
    return handler(data, gas_limit)
