import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.block import Block
from pacvo.chain import Blockchain, State, median_time_past
from pacvo.crypto import canonical_json, derive_address
from pacvo.params import (
    BLOCK_REWARD,
    GENESIS_TIMESTAMP,
    INITIAL_TARGET,
    MAX_BLOCK_BYTES,
    MAX_BLOCK_TXS,
    MAX_REORG_DEPTH,
    MAX_TARGET,
    MIN_FEE,
    RETARGET_INTERVAL,
    STAKE_LOCK_BLOCKS,
    TARGET_BLOCK_TIME,
    stake_split,
)
from pacvo.transaction import Transaction
from pacvo.wallet import Wallet

MINER = "pvo1" + "aa" * 64
RECIPIENT = "pvo1" + "bb" * 64


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

# --- median-time-past ---
mtp_chain = Blockchain()
for h in range(1, 4):
    prev = mtp_chain.blocks[-1]
    ts = GENESIS_TIMESTAMP + h
    cb = Transaction.coinbase(MINER, 1, 0, h)
    mtp_chain.blocks.append(
        make_block(h, prev.block_hash, MAX_TARGET, 0, [cb], ts)
    )
mtp = median_time_past(mtp_chain.blocks)
assert mtp == GENESIS_TIMESTAMP + 1

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
    fee=MIN_FEE,
    nonce=0,
    timestamp=int(time.time()),
)
good_tx.sign(wallet.sign_secret_key)
ok, reason = chain.validate_transaction(good_tx, state)
assert ok, reason

low_fee_tx = Transaction(
    sender_public_key=wallet.sign_public_key,
    recipient=RECIPIENT,
    amount=1,
    fee=MIN_FEE - 1,
    nonce=0,
    timestamp=int(time.time()),
)
low_fee_tx.sign(wallet.sign_secret_key)
ok, reason = chain.validate_transaction(low_fee_tx, state)
assert not ok
assert "fee" in reason

bad_nonce_tx = Transaction(
    sender_public_key=wallet.sign_public_key,
    recipient=RECIPIENT,
    amount=1,
    fee=MIN_FEE,
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
    fee=MIN_FEE,
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
    fee=MIN_FEE,
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
cb.timestamp = GENESIS_TIMESTAMP + 1
weak_block = make_block(1, chain.blocks[0].block_hash, INITIAL_TARGET, 0, [cb], cb.timestamp)
ok, reason = chain.validate_block(weak_block)
assert not ok
assert "proof of work" in reason

# MTP timestamp rules
easy_ts = median_time_past(chain.blocks) + 1
ok, reason = chain._validate_timestamp(easy_ts, chain.blocks)
assert ok, reason
ok, reason = chain._validate_timestamp(median_time_past(chain.blocks), chain.blocks)
assert not ok
assert "median-time-past" in reason

easy_block = Block(
    1, chain.blocks[0].block_hash, weak_block.merkle_root, easy_ts, MAX_TARGET, 0, [cb]
)
chain2 = Blockchain()
chain2.blocks[0] = Block(
    0,
    "0" * 128,
    Block.compute_merkle_root([]),
    GENESIS_TIMESTAMP,
    MAX_TARGET,
    0,
    [],
)
chain2.blocks.append(easy_block)
too_many = [Transaction.coinbase(MINER, 1, 0, 2)]
recipient_addr = derive_address(Wallet.generate().sign_public_key)
for i in range(MAX_BLOCK_TXS):
    tx = Transaction.coinbase(recipient_addr, 1, 0, 2 + i)
    too_many.append(tx)
big_block = make_block(
    2,
    easy_block.block_hash,
    MAX_TARGET,
    0,
    too_many,
    easy_ts + 1,
)
ok, reason = chain2.validate_block(big_block)
assert not ok
assert "too many" in reason

# MAX_BLOCK_BYTES
chain3 = Blockchain()
chain3.blocks.append(easy_block)
oversized_dict = easy_block.to_dict()
oversized_dict["height"] = 2
oversized_dict["prev_hash"] = easy_block.block_hash
oversized_dict["timestamp"] = easy_ts + 1
oversized_dict["transactions"][0]["nonce"] = 2
oversized_dict["transactions"].append(
    {
        "sender_public_key": wallet.sign_public_key.hex(),
        "recipient": RECIPIENT,
        "amount": 1,
        "fee": MIN_FEE,
        "nonce": 0,
        "timestamp": int(time.time()),
        "stake_amount": 0,
        "signature": "ff" * (MAX_BLOCK_BYTES // 2),
    }
)
oversized = Block.from_dict(oversized_dict)
assert len(canonical_json(oversized.to_dict())) > MAX_BLOCK_BYTES
ok, reason = chain3.validate_block(oversized)
assert not ok
assert "too large" in reason

# --- Bounded reorg rejection ---
reorg_chain = Blockchain()
parent = reorg_chain.blocks[0]
for h in range(1, 200):
    cb_h = Transaction.coinbase(MINER, spendable, stake, h)
    blk = make_block(
        h, parent.block_hash, MAX_TARGET, 0, [cb_h], GENESIS_TIMESTAMP + h
    )
    reorg_chain.blocks.append(blk)
    reorg_chain._apply_block_state(blk)
    parent = blk

fork_height = 0
alt_blocks = []
prev = reorg_chain.blocks[0]
for h in range(1, 210):
    cb_h = Transaction.coinbase(RECIPIENT, spendable, stake, h)
    blk = make_block(
        h, prev.block_hash, MAX_TARGET, 0, [cb_h], GENESIS_TIMESTAMP + h + 1000
    )
    alt_blocks.append(blk)
    prev = blk

ok, reason = reorg_chain.execute_reorg(fork_height, alt_blocks)
assert not ok
assert "reorg depth" in reason

# meets_target helper
easy_pow = Block(1, "0" * 128, weak_block.merkle_root, cb.timestamp, MAX_TARGET, 0, [cb])
assert weak_block.meets_target() is False
assert easy_pow.meets_target() is True

print("test_chain: all assertions passed")
