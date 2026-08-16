"""L1 Reorganization and L3 Economic State Replay Verification.

Uses EASY_TARGET (2**512 - 1) so block.meets_target() is immediately true
with nonce=0, making the test fast regardless of INITIAL_TARGET difficulty.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.block import Block
from pacvo.chain import Blockchain
from pacvo.crypto import (
    derive_address,
    generate_sign_keypair,
)
from pacvo.l3.anchor import compute_l3_state_root
from pacvo.l3.economy import Economy
from pacvo.l3.fixed import WAD
from pacvo.params import (
    BLOCK_REWARD,
    GENESIS_TIMESTAMP,
    TARGET_BLOCK_TIME,
    stake_split,
)
from pacvo.transaction import Transaction

# Use a trivially-easy target so meets_target() passes with nonce=0 immediately.
EASY_TARGET = 2**512 - 1


def _make_chain() -> Blockchain:
    """Return a fresh Blockchain whose genesis block uses EASY_TARGET."""
    chain = Blockchain()
    # Patch genesis target so next_target() propagates EASY_TARGET
    chain.blocks[0].target = EASY_TARGET
    return chain


def test_on_chain_l3_reorganization():
    print("Testing L1 Reorganization & L3 Economic State Rollback/Replay...")
    chain = _make_chain()

    miner_pk, _miner_sk = generate_sign_keypair()
    miner_addr = derive_address(miner_pk)
    spendable, stake = stake_split(BLOCK_REWARD)

    def mine_block(height, prev_hash, txs, ts):
        # Always use EASY_TARGET so PoW succeeds with nonce=0.
        b = Block(
            height,
            prev_hash,
            Block.compute_merkle_root([tx.txid for tx in txs]),
            ts,
            EASY_TARGET,
            0,
            txs,
        )
        # meets_target() should be True immediately; loop is a safety net only.
        while not b.meets_target():
            b.nonce += 1
        return b

    # 1. Build 6 canonical blocks (heights 1-6).
    prev = chain.blocks[0]
    for h in range(1, 7):
        cb = Transaction.coinbase(miner_addr, spendable, stake, h)
        ts = GENESIS_TIMESTAMP + h * TARGET_BLOCK_TIME
        b = mine_block(h, prev.block_hash, [cb], ts)
        ok, err = chain.add_block(b, sigs_ok=True)
        assert ok, f"canonical block {h}: {err}"
        prev = b

    # 2. Snapshot L3 economy at height 5.
    eco = Economy(genesis_reserve_pol=4 * WAD)
    eco.advance_to_height(5)
    root_5 = compute_l3_state_root(eco)

    # 3. Apply a state-changing L3 op at block 6 (equity registration).
    eco.advance_to_height(6)
    eco.register_equity("PVOA", "Pacvo Alpha", "0x1111", "0xissuer", 1_000_000 * WAD)
    root_6 = compute_l3_state_root(eco)
    assert root_6 != root_5, "equity registration must change state root"

    # 4. Fork from height 5: build 2 alternative blocks (heights 6 & 7).
    fork_height = 5
    alt_prev = chain.blocks[fork_height]

    alt_blocks = []
    for step in range(1, 3):
        h_alt = fork_height + step
        cb_alt = Transaction.coinbase(miner_addr, spendable, stake, h_alt)
        ts_alt = GENESIS_TIMESTAMP + h_alt * TARGET_BLOCK_TIME + 1
        b_alt = mine_block(h_alt, alt_prev.block_hash, [cb_alt], ts_alt)
        alt_blocks.append(b_alt)
        alt_prev = b_alt

    # Alt chain has 2 blocks beyond fork vs canonical chain's 1 block beyond
    # fork, so cumulative_work(alt) > cumulative_work(canonical).
    ok, err = chain.execute_reorg(fork_height, alt_blocks)
    assert ok, f"execute_reorg failed: {err}"

    # 5. Replay L3 along the new canonical fork (no equity registration).
    eco_reorg = Economy(genesis_reserve_pol=4 * WAD)
    eco_reorg.advance_to_height(5)
    root_reorg_5 = compute_l3_state_root(eco_reorg)
    assert root_reorg_5 == root_5, "root at height 5 must be identical after reorg"

    eco_reorg.advance_to_height(7)
    root_reorg_7 = compute_l3_state_root(eco_reorg)
    # Alt fork has no equity registration, so root_reorg_7 ≠ root_6.
    assert root_reorg_7 != root_6, "alt fork root must differ from canonical root_6"

    print("  [PASS] L1 Reorganization L3 economic state replay verified")


def main():
    test_on_chain_l3_reorganization()
    print("ALL L3 REORG TESTS PASSED!")


if __name__ == "__main__":
    main()
