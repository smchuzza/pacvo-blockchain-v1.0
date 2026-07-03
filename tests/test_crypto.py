import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.crypto import (
    decrypt_payload,
    derive_address,
    encrypt_payload,
    generate_kem_keypair,
    generate_sign_keypair,
    is_valid_address,
    kem_decapsulate,
    kem_encapsulate,
    sha512,
    sha512_hex,
    sign_message,
    verify_signature,
)
from pacvo.params import BLOCK_REWARD, stake_split
from pacvo.transaction import Transaction
from pacvo.wallet import Wallet, WalletError

SHA512_EMPTY = (
    "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
)

assert sha512_hex(b"") == SHA512_EMPTY
assert sha512(b"").hex() == SHA512_EMPTY

public_key, secret_key = generate_sign_keypair()
message = b"pacvo test message"
signature = sign_message(secret_key, message)
assert verify_signature(public_key, message, signature)
assert not verify_signature(public_key, b"tampered", signature)
assert not verify_signature(public_key, message, signature[:-1] + b"\x00")

kem_pk, kem_sk = generate_kem_keypair()
ciphertext, shared_secret_enc = kem_encapsulate(kem_pk)
shared_secret_dec = kem_decapsulate(kem_sk, ciphertext)
assert shared_secret_enc == shared_secret_dec

aes_key = os.urandom(32)
plaintext = b"encrypted payload data"
blob = encrypt_payload(aes_key, plaintext)
assert decrypt_payload(aes_key, blob) == plaintext

address = derive_address(public_key)
assert address.startswith("pvo1")
assert len(address) == 4 + 128
assert is_valid_address(address)
assert not is_valid_address("pvo1" + "ZZ" * 64)

wallet = Wallet.generate()
address = wallet.address
with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
    wallet_path = f.name
try:
    wallet.save(wallet_path, "test-passphrase")
    loaded = Wallet.load(wallet_path, "test-passphrase")
    assert loaded.address == address
    assert loaded.sign_public_key == wallet.sign_public_key
    try:
        Wallet.load(wallet_path, "wrong-passphrase")
        raise AssertionError("expected WalletError for wrong passphrase")
    except WalletError:
        pass
finally:
    os.unlink(wallet_path)

recipient = derive_address(generate_sign_keypair()[0])
tx = Transaction(
    sender_public_key=wallet.sign_public_key,
    recipient=recipient,
    amount=1000,
    fee=10_000,
    nonce=1,
    timestamp=1751452800,
)
tx.sign(wallet.sign_secret_key)
assert tx.verify_signature()

tx.amount = 9999
assert not tx.verify_signature()

spendable, stake = stake_split(BLOCK_REWARD)
cb = Transaction.coinbase(recipient, spendable, stake, 0)
assert cb.verify_signature()
assert cb.is_coinbase
assert cb.sender == "COINBASE"

print("All tests passed.")
