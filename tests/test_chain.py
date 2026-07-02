import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.block import Block
from pacvo.chain import Blockchain, State
from pacvo.params import (
    BLOCK_REWARD,
    GENESIS_TIMESTAMP,
    INITIAL_TARGET,
    MAX_TARGET,
    RETARGET_INTERVAL,
    STAKE_LOCK_BLOCKS,
    TARGET_BLOCK_TIME,
    stake_split,
)
from pacvo.transaction import Transaction
from pacvo.wallet import Wallet

MINER = "pvo1miner00000000000000000000000000000000"
RECIPIENT = "pvo1recipient00000000000000000000000000"


def make_block(height, prev_hash, target, nonce, txs, timestamp=None):
    ts = timestamp if timestamp is not None else int(time.time())
    merkle_root = Block.compute_merkle_root([tx.txid for tx in txs])
    return Block(height, prev_hash, merkle_root, ts, target, nonce, txs)


# --- Merkle root ---
empty_root = Block.compute_merkle_root([])
assert empty_root == Block.compute_merkle_root([])
tx_a = Transaction.coinbase(MINER, 1, 0, 1)
tx_b = Transaction.coinbase(RECIPIENT, 2, 0, 2)
root_two = Block.compute_merkle_root([tx_a.txid, tx_b.txid])
assert root_two != tx_a.txid
assert root_two != tx_b.txid
root_odd = Block.compute_merkle_root([tx_a.txid, tx_b.txid, tx_a.txid])
assert root_odd != root_two

# --- Header hashing and serialization roundtrip ---
genesis = Blockchain.create_genesis()
assert len(genesis.block_hash) == 128
assert genesis.block_hash == genesis.block_hash
restored = Block.from_dict(genesis.to_dict())
assert restored.block_hash == genesis.block_hash
assert restored.height == genesis.height
assert restored.target == INITIAL_TARGET

# --- next_target retarget math (chain built without PoW validation) ---
chain = Blockchain()
base_ts = GENESIS_TIMESTAMP
for h in range(1, RETARGET_INTERVAL):
    prev = chain.blocks[-1]
    spendable, stake = stake_split(BLOCK_REWARD)
    cb = Transaction.coinbase(MINER, spendable, stake, h)
    cb.timestamp = base_ts + h
    block = make_block(h, prev.block_hash, INITIAL_TARGET, 0, [cb], cb.timestamp)
    chain.blocks.append(block)

tip = chain.blocks[-1]
prev_retarget = chain.blocks[chain.height - RETARGET_INTERVAL + 1]
elapsed = tip.timestamp - prev_retarget.timestamp
expected = TARGET_BLOCK_TIME * RETARGET_INTERVAL
raw_target = tip.target * elapsed // expected
clamped = max(tip.target // 4, min(tip.target * 4, raw_target))
assert chain.next_target() == max(1, min(clamped, MAX_TARGET))

fresh = Blockchain()
assert fresh.next_target() == INITIAL_TARGET

# --- Stake maturity release ---
state = State()
stake_amt = 30_000_000
state.stakes[MINER] = [{"amount": stake_amt, "unlock_height": 5}]
state.balances[MINER] = 0
chain._release_matured_stakes(state, 4)
assert state.spendable(MINER) == 0
assert state.staked(MINER) == stake_amt
chain._release_matured_stakes(state, 5)
assert state.spendable(MINER) == stake_amt
assert state.staked(MINER) == 0

# --- validate_transaction rules ---
wallet = Wallet.generate()
sender = wallet.address
state = State()
state.balances[sender] = 10**8
state.nonces[sender] = 0

good_tx = Transaction(
    sender_public_key=wallet.sign_public_key,
    recipient=RECIPIENT,
    amount=1,
    fee=0,
    nonce=0,
    timestamp=int(time.time()),
)
good_tx.sign(wallet.sign_secret_key)
ok, reason = chain.validate_transaction(good_tx, state)
assert ok, reason

bad_nonce_tx = Transaction(
    sender_public_key=wallet.sign_public_key,
    recipient=RECIPIENT,
    amount=1,
    fee=0,
    nonce=99,
    timestamp=int(time.time()),
)
bad_nonce_tx.sign(wallet.sign_secret_key)
ok, reason = chain.validate_transaction(bad_nonce_tx, state)
assert not ok
assert "nonce" in reason

overspend_tx = Transaction(
    sender_public_key=wallet.sign_public_key,
    recipient=RECIPIENT,
    amount=10**8,
    fee=1,
    nonce=0,
    timestamp=int(time.time()),
)
overspend_tx.sign(wallet.sign_secret_key)
ok, reason = chain.validate_transaction(overspend_tx, state)
assert not ok
assert "balance" in reason

bad_sig_tx = Transaction(
    sender_public_key=wallet.sign_public_key,
    recipient=RECIPIENT,
    amount=1,
    fee=0,
    nonce=0,
    timestamp=int(time.time()),
)
bad_sig_tx.sign(wallet.sign_secret_key)
bad_sig_tx.signature = bad_sig_tx.signature[:-1] + bytes([bad_sig_tx.signature[-1] ^ 0x01])
ok, reason = chain.validate_transaction(bad_sig_tx, state)
assert not ok
assert "signature" in reason

# --- Block validation rejects insufficient PoW ---
chain = Blockchain()
spendable, stake = stake_split(BLOCK_REWARD)
cb = Transaction.coinbase(MINER, spendable, stake, 1)
cb.timestamp = int(time.time())
weak_block = make_block(1, chain.blocks[0].block_hash, INITIAL_TARGET, 0, [cb], cb.timestamp)
ok, reason = chain.validate_block(weak_block)
assert not ok
assert "proof of work" in reason

# meets_target helper
assert weak_block.meets_target() is False
easy_block = Block(1, "0" * 128, weak_block.merkle_root, cb.timestamp, MAX_TARGET, 0, [cb])
assert easy_block.meets_target() is True

print("test_chain: all assertions passed")
