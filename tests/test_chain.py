import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.block import Block
from pacvo.chain import Blockchain
from pacvo.params import BLOCK_REWARD, MAX_TARGET, STAKE_LOCK_BLOCKS, stake_split
from pacvo.transaction import Transaction
from pacvo.wallet import Wallet

MINER = "pvo1miner00000000000000000000000000000000"
RECIPIENT = "pvo1recipient00000000000000000000000000"


def mine_block(chain, miner, extra_txs=None, timestamp=None):
    height = chain.height + 1
    target = chain.next_target()
    prev_hash = chain.blocks[-1].block_hash
    ts = timestamp if timestamp is not None else int(time.time())
    spendable, stake = stake_split(BLOCK_REWARD)
    fees = sum(tx.fee for tx in (extra_txs or []))
    coinbase = Transaction.coinbase(miner, spendable + fees, stake, height)
    coinbase.timestamp = ts
    txs = [coinbase] + (extra_txs or [])
    merkle_root = Block.compute_merkle_root([tx.txid for tx in txs])
    for nonce in range(10_000_000):
        block = Block(height, prev_hash, merkle_root, ts, target, nonce, txs)
        if block.meets_target():
            return block
    raise RuntimeError("failed to mine block")


chain = Blockchain()
chain.blocks[0] = Block(
    0,
    "0" * 128,
    Block.compute_merkle_root([]),
    chain.blocks[0].timestamp,
    MAX_TARGET,
    0,
    [],
)

block1 = mine_block(chain, MINER)
ok, reason = chain.add_block(block1)
assert ok, reason
assert chain.height == 1

spendable, stake = stake_split(BLOCK_REWARD)
assert chain.state.spendable(MINER) == spendable
assert len(chain.state.stakes.get(MINER, [])) == 1
assert chain.state.stakes[MINER][0]["amount"] == stake
assert chain.state.stakes[MINER][0]["unlock_height"] == 1 + STAKE_LOCK_BLOCKS
assert chain.state.staked(MINER) == stake

block2 = mine_block(chain, MINER, timestamp=block1.timestamp + 1)
ok, reason = chain.add_block(block2)
assert ok, reason
assert chain.height == 2

block3 = mine_block(chain, MINER, timestamp=block2.timestamp + 1)
ok, reason = chain.add_block(block3)
assert ok, reason
assert chain.height == 3

chain.state.stakes[MINER] = [{"amount": 5 * 10**8, "unlock_height": 3}]
chain.state.balances[MINER] = 0
release_block = mine_block(chain, MINER, timestamp=block3.timestamp + 1)
ok, reason = chain.add_block(release_block)
assert ok, reason
assert chain.state.spendable(MINER) > 0
assert chain.state.staked(MINER) == stake

wallet = Wallet.generate()
sender = wallet.address
chain.state.balances[sender] = 10**8
chain.state.nonces[sender] = 0

bad_nonce_tx = Transaction(
    sender_public_key=wallet.sign_public_key,
    recipient=RECIPIENT,
    amount=1,
    fee=0,
    nonce=99,
    timestamp=int(time.time()),
)
bad_nonce_tx.sign(wallet.sign_secret_key)
bad_block = mine_block(chain, MINER, extra_txs=[bad_nonce_tx], timestamp=release_block.timestamp + 1)
ok, reason = chain.add_block(bad_block)
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
overspend_block = mine_block(chain, MINER, extra_txs=[overspend_tx], timestamp=release_block.timestamp + 1)
ok, reason = chain.add_block(overspend_block)
assert not ok
assert "balance" in reason

short_chain = Blockchain()
short_chain.blocks[0] = Block(
    0,
    "0" * 128,
    Block.compute_merkle_root([]),
    short_chain.blocks[0].timestamp,
    MAX_TARGET,
    0,
    [],
)
short_chain.add_block(mine_block(short_chain, MINER))

long_chain = Blockchain()
long_chain.blocks[0] = Block(
    0,
    "0" * 128,
    Block.compute_merkle_root([]),
    long_chain.blocks[0].timestamp,
    MAX_TARGET,
    0,
    [],
)
b1 = mine_block(long_chain, MINER)
long_chain.add_block(b1)
b2 = mine_block(long_chain, MINER, timestamp=b1.timestamp + 1)
long_chain.add_block(b2)
b3 = mine_block(long_chain, MINER, timestamp=b2.timestamp + 1)
long_chain.add_block(b3)

assert long_chain.cumulative_work() > short_chain.cumulative_work()
assert short_chain.replace_if_better([b.to_dict() for b in long_chain.blocks])
assert short_chain.height == long_chain.height
assert short_chain.cumulative_work() == long_chain.cumulative_work()

weaker = Blockchain()
weaker.blocks[0] = Block(
    0,
    "0" * 128,
    Block.compute_merkle_root([]),
    weaker.blocks[0].timestamp,
    MAX_TARGET,
    0,
    [],
)
weaker.add_block(mine_block(weaker, MINER))
assert not long_chain.replace_if_better([b.to_dict() for b in weaker.blocks])

print("test_chain: all assertions passed")
