import asyncio
import logging
import time

from pacvo.block import Block
from pacvo.chain import median_time_past
from pacvo.crypto import canonical_json
from pacvo.params import BLOCK_REWARD, MAX_BLOCK_BYTES, MAX_BLOCK_TXS, stake_split
from pacvo.transaction import Transaction

logger = logging.getLogger("pacvo.miner")

NONCE_CHUNK = 2000


def build_candidate(chain, mempool_txs: list, miner_address: str) -> Block:
    state = chain.state.copy()
    chain._release_matured_stakes(state, chain.height + 1)
    sorted_txs = sorted(mempool_txs, key=lambda tx: tx.fee, reverse=True)
    selected = []
    fees = 0
    for tx in sorted_txs:
        if len(selected) >= MAX_BLOCK_TXS - 1:
            break
        ok, _ = chain.validate_transaction(tx, state)
        if not ok:
            continue
        chain._apply_non_coinbase_tx(state, tx)
        selected.append(tx)
        fees += tx.fee
    spendable, stake = stake_split(BLOCK_REWARD)
    txs = [
        Transaction.coinbase(miner_address, spendable + fees, stake, chain.height + 1)
    ] + selected
    while len(txs) > MAX_BLOCK_TXS:
        txs.pop()
    tip = chain.blocks[-1]
    mtp = median_time_past(chain.blocks)
    timestamp = max(mtp + 1, int(time.time()))
    candidate = Block(
        chain.height + 1,
        tip.block_hash,
        Block.compute_merkle_root([t.txid for t in txs]),
        timestamp,
        chain.next_target(),
        0,
        txs,
    )
    while len(canonical_json(candidate.to_dict())) > MAX_BLOCK_BYTES and len(txs) > 1:
        txs.pop()
        candidate = Block(
            candidate.height,
            candidate.prev_hash,
            Block.compute_merkle_root([t.txid for t in txs]),
            candidate.timestamp,
            candidate.target,
            candidate.nonce,
            txs,
        )
    return candidate


def _search_nonces(candidate: Block, start_nonce: int, count: int) -> int | None:
    for i in range(count):
        nonce = start_nonce + i
        candidate.nonce = nonce
        if candidate.meets_target():
            return nonce
    return None


async def mine_loop(node) -> None:
    loop = asyncio.get_running_loop()
    while True:
        candidate = build_candidate(
            node.chain, list(node.mempool.values()), node.wallet.address
        )
        start_height = node.chain.height
        nonce = 0
        found = False
        while not found:
            winning = await loop.run_in_executor(
                None, _search_nonces, candidate, nonce, NONCE_CHUNK
            )
            if winning is not None:
                candidate.nonce = winning
                node.submit_block(candidate)
                logger.info(
                    "mined block height=%s hash=%s",
                    candidate.height,
                    candidate.block_hash,
                )
                found = True
                break
            if node.chain.height != start_height:
                break
            nonce += NONCE_CHUNK
            await asyncio.sleep(0)
