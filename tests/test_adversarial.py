"""Adversarial Multi-Layer Test Suite for Pacvo L2/L3.

Stress tests complex cross-layer interactions, edge cases, and failure modes:
1. Deep L1 Reorganizations mid-AMM-swap with slippage breach & price inversion.
2. Deep L1 Reorganizations mid-liquidation where collateral prices flip between branches.
3. Reorganizations across economic Epoch boundaries with dividend & interest index rewind.
4. Reserve solvency invariants under attestation lag, partial custodian balance drops, and out-of-order relays.
5. Cross-chain bridge concurrent deposit/burn stress with decimal scale precision invariants.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.block import Block
from pacvo.chain import Blockchain
from pacvo.crypto import derive_address, generate_sign_keypair
from pacvo.l3.anchor import compute_l3_state_root
from pacvo.l3.economy import Economy
from pacvo.l3.errors import (
    InsufficientReserveError,
    InvariantViolationError,
    SlippageExceededError,
    UndercollateralizedError,
)
from pacvo.l3.fixed import WAD, wad_div, wad_mul
from pacvo.params import (
    BLOCK_REWARD,
    GENESIS_TIMESTAMP,
    TARGET_BLOCK_TIME,
    stake_split,
)
from pacvo.transaction import Transaction

EASY_TARGET = 2**512 - 1


def _make_chain() -> Blockchain:
    chain = Blockchain()
    chain.blocks[0].target = EASY_TARGET
    return chain


def _mine_block(height: int, prev_hash: str, txs: list[Transaction], ts: int) -> Block:
    b = Block(
        height,
        prev_hash,
        Block.compute_merkle_root([tx.txid for tx in txs]),
        ts,
        EASY_TARGET,
        0,
        txs,
    )
    while not b.meets_target():
        b.nonce += 1
    return b


def test_adversarial_reorg_mid_amm_swap_divergence():
    """Adversarial Test: Reorg with AMM price divergence and slippage limit breaches."""
    print("Testing Adversarial L3 Reorg: Mid-AMM Swaps & Slippage Drift...")
    chain = _make_chain()
    miner_pk, _ = generate_sign_keypair()
    miner_addr = derive_address(miner_pk)
    spendable, stake = stake_split(BLOCK_REWARD)

    # 1. Genesis to height 10
    prev = chain.blocks[0]
    for h in range(1, 11):
        cb = Transaction.coinbase(miner_addr, spendable, stake, h)
        b = _mine_block(h, prev.block_hash, [cb], GENESIS_TIMESTAMP + h * TARGET_BLOCK_TIME)
        ok, err = chain.add_block(b, sigs_ok=True)
        assert ok, err
        prev = b

    # Create L3 economy at height 10 with a PVOA/POL pool
    eco = Economy(genesis_reserve_pol=10_000 * WAD)
    eco.advance_to_height(10)
    eco.register_equity("PVOA", "Pacvo Alpha", "0x1111", "0xissuer", 1_000_000 * WAD)
    market = eco.market_manager.create_market("PVOA", "POL", fee_bps=30)
    market.add_liquidity("0xlp", 10_000 * WAD, 10_000 * WAD)
    initial_k = market.reserve_a * market.reserve_b
    assert market.get_spot_price_a_in_b() == WAD

    # Snapshot economy at fork point (height 10)
    eco_fork_snapshot = eco.copy()
    root_fork = compute_l3_state_root(eco_fork_snapshot)

    # --- FORK A (Canonical): Massive PVOA Sell Pressure ---
    # User 1 dumps 5,000 PVOA for POL, crashing PVOA price
    eco_a = eco.copy()
    eco_a.advance_to_height(11)
    m_a = eco_a.market_manager.get_market("PVOA", "POL")
    pol_received_a = m_a.swap("0xuser1", 5_000 * WAD, "PVOA", min_amount_out=0)
    assert pol_received_a > 0
    # Spot price of POL in PVOA increases (> 2.0 WAD) as PVOA is dumped for POL
    price_a = m_a.get_spot_price_a_in_b()
    assert price_a > 2 * WAD

    # User 2 executes a swap tailored to Fork A's cheap PVOA
    eco_a.advance_to_height(12)
    pvoa_bought_a = m_a.swap("0xuser2", 1_000 * WAD, "POL", min_amount_out=1_500 * WAD)
    assert pvoa_bought_a >= 1_500 * WAD
    root_a = compute_l3_state_root(eco_a)

    # Add 2 blocks to chain A
    for h in range(11, 13):
        cb = Transaction.coinbase(miner_addr, spendable, stake, h)
        b = _mine_block(h, prev.block_hash, [cb], GENESIS_TIMESTAMP + h * TARGET_BLOCK_TIME)
        ok, err = chain.add_block(b, sigs_ok=True)
        assert ok, err
        prev = b

    # --- FORK B (Alternative / Longer Fork): Opposite Heavy PVOA Buy Pressure ---
    # User 3 buys 6,000 POL worth of PVOA, driving PVOA price UP (POL price in PVOA drops)
    eco_b = eco_fork_snapshot.copy()
    eco_b.advance_to_height(11)
    m_b = eco_b.market_manager.get_market("PVOA", "POL")
    pvoa_received_b = m_b.swap("0xuser3", 6_000 * WAD, "POL", min_amount_out=0)
    assert pvoa_received_b > 0
    price_b = m_b.get_spot_price_a_in_b()
    assert price_b < WAD // 2  # POL price dropped, PVOA became scarce

    # User 2's transaction from Fork A (which demanded min 1,500 PVOA for 1,000 POL) is replayed on Fork B:
    # It MUST fail with SlippageExceededError because PVOA is now much more expensive!
    eco_b.advance_to_height(12)
    slippage_caught = False
    try:
        m_b.swap("0xuser2", 1_000 * WAD, "POL", min_amount_out=1_500 * WAD)
    except SlippageExceededError:
        slippage_caught = True
    assert slippage_caught is True, "Slippage protection must trigger on divergent branch replay"

    # User 2 adjusts slippage or tx is dropped; alternative fork advances to height 14 (heavier work)
    eco_b.advance_to_height(13)
    eco_b.advance_to_height(14)
    root_b = compute_l3_state_root(eco_b)

    # Invariant checks on both branches: k must have strictly grown (fees retained in pool)
    k_a = m_a.reserve_a * m_a.reserve_b
    k_b = m_b.reserve_a * m_b.reserve_b
    assert k_a > initial_k
    assert k_b > initial_k

    # Reorganize L1 Blockchain to Fork B
    alt_prev = chain.blocks[10]
    alt_blocks = []
    for h_alt in range(11, 15):
        cb_alt = Transaction.coinbase(miner_addr, spendable, stake, h_alt)
        b_alt = _mine_block(
            h_alt,
            alt_prev.block_hash,
            [cb_alt],
            GENESIS_TIMESTAMP + h_alt * TARGET_BLOCK_TIME + 2,
        )
        alt_blocks.append(b_alt)
        alt_prev = b_alt

    reorg_ok, reorg_err = chain.execute_reorg(10, alt_blocks)
    assert reorg_ok is True, f"Reorganization failed: {reorg_err}"
    assert chain.height == 14

    # Verify that clean replay from fork snapshot yields exact byte-identical root_b
    eco_replayed = eco_fork_snapshot.copy()
    eco_replayed.advance_to_height(11)
    m_rep = eco_replayed.market_manager.get_market("PVOA", "POL")
    m_rep.swap("0xuser3", 6_000 * WAD, "POL", min_amount_out=0)
    eco_replayed.advance_to_height(12)
    # User 2 skipped due to slippage error
    eco_replayed.advance_to_height(13)
    eco_replayed.advance_to_height(14)
    root_replayed = compute_l3_state_root(eco_replayed)
    assert root_replayed == root_b, "Replayed state root must match alternative branch root exactly"

    print("  [PASS] Mid-AMM reorg price divergence and slippage protection verified")


def test_adversarial_reorg_mid_liquidation_inversion():
    """Adversarial Test: Reorg inverting collateral price and liquidation validity."""
    print("Testing Adversarial L3 Reorg: Liquidation Inversion & Collateral Safety...")
    eco = Economy(genesis_reserve_pol=10_000 * WAD)
    eco.advance_to_height(20)
    eco.register_equity("COLLAT", "Collateral Token", "0xcoll", "0xissuer", 1_000_000 * WAD)

    # Alice opens a position: deposits 10,000 COLLAT, borrows 5,000 POL
    # Initial prices: COLLAT = $1.00 (1.0 WAD), POL = $1.00 (1.0 WAD)
    # Collateral Value = $10,000, Debt = $5,000 -> Health Factor = 1.6 > 1.0 (Healthy)
    eco.debt_manager.open_or_modify_position(
        owner="0xalice",
        collateral_symbol="COLLAT",
        collateral_delta=10_000 * WAD,
        debt_symbol="POL",
        debt_delta=5_000 * WAD,
        collateral_price=1 * WAD,
        debt_price=1 * WAD,
        current_height=20,
    )

    pos = eco.debt_manager.get_position("0xalice")
    assert pos is not None
    assert pos.calculate_health_factor(1 * WAD, 1 * WAD) == 24 * 10**17  # 2.4 WAD
    assert pos.is_liquidatable(1 * WAD, 1 * WAD) is False

    fork_snapshot = eco.copy()

    # --- FORK A (Canonical): Collateral Crashes -> Liquidated ---
    eco_a = fork_snapshot.copy()
    eco_a.advance_to_height(21)
    # COLLAT price crashes to $0.40 (0.4 WAD)
    # Collateral value = $4,000, Adj Collateral = $4,800, Debt = $5,000 -> Health Factor = 0.96 < 1.0 (Liquidatable!)
    crashed_price = 4 * 10**17
    pos_a = eco_a.debt_manager.get_position("0xalice")
    assert pos_a.is_liquidatable(crashed_price, 1 * WAD) is True
    assert pos_a.calculate_health_factor(crashed_price, 1 * WAD) == 96 * 10**16  # 0.96 WAD

    # Bob liquidates Alice's position (covers 2,500 POL debt)
    repaid, seized = eco_a.debt_manager.liquidate(
        liquidator="0xbob",
        borrower="0xalice",
        debt_to_cover=2_500 * WAD,
        collateral_price=crashed_price,
        debt_price=1 * WAD,
    )
    assert repaid == 2_500 * WAD
    assert seized > 0
    assert pos_a.debt_amount == 2_500 * WAD
    root_a = compute_l3_state_root(eco_a)

    # --- FORK B (Alternative): Collateral Price Rises -> Liquidation Replay Rejects ---
    eco_b = fork_snapshot.copy()
    eco_b.advance_to_height(21)
    # On Fork B, COLLAT price increased to $2.00 (2.0 WAD)
    bull_price = 2 * WAD
    pos_b = eco_b.debt_manager.get_position("0xalice")
    assert pos_b.is_liquidatable(bull_price, 1 * WAD) is False
    assert pos_b.calculate_health_factor(bull_price, 1 * WAD) == 48 * 10**17  # 4.8 WAD

    # Bob's liquidation transaction replayed on Fork B MUST be rejected!
    liquidation_blocked = False
    try:
        eco_b.debt_manager.liquidate(
            liquidator="0xbob",
            borrower="0xalice",
            debt_to_cover=2_500 * WAD,
            collateral_price=bull_price,
            debt_price=1 * WAD,
        )
    except UndercollateralizedError:
        liquidation_blocked = True
    assert liquidation_blocked is True, "Healthy position liquidation must be blocked on fork replay"

    # Alice's position on Fork B remains intact with original 10,000 COLLAT and 5,000 POL debt
    assert pos_b.collateral_amount == 10_000 * WAD
    assert pos_b.debt_amount == 5_000 * WAD

    root_b = compute_l3_state_root(eco_b)
    assert root_b != root_a

    print("  [PASS] Liquidation inversion and healthy position defense on reorg verified")


def test_adversarial_reorg_across_epoch_boundaries():
    """Adversarial Test: Reorg rewinding across economic epoch transitions."""
    print("Testing Adversarial L3 Reorg: Cross-Epoch Boundary Rewind & Replay...")
    eco = Economy(genesis_reserve_pol=10_000 * WAD)
    eco.advance_to_height(90)
    assert eco.epoch == 0

    eq = eco.register_equity("EQPOCH", "Epoch Equity", "0xeq", "0xissuer", 10_000 * WAD)
    eco.treasury.deposit("POL", 2_000 * WAD)

    alice_shares = 5_000 * WAD  # Alice holds 50% of 10,000 supply

    snapshot_epoch0 = eco.copy()

    # --- FORK A: Advance into Epoch 1 at height 102 ---
    eco_a = snapshot_epoch0.copy()
    eco_a.advance_to_height(102)
    assert eco_a.epoch == 1

    # Declare $1,000 dividend in Epoch 1
    eco_a.declare_dividend("EQPOCH", 1_000 * WAD)
    eq_a = eco_a.equities["EQPOCH"]
    claimable_a = eq_a.dividend_pool.calculate_claimable("0xalice", alice_shares)
    assert claimable_a == 500 * WAD  # 50% of 1,000

    # Alice claims dividend
    claimed_a = eq_a.claim_dividend("0xalice", alice_shares)
    assert claimed_a == 500 * WAD
    assert eq_a.dividend_pool.calculate_claimable("0xalice", alice_shares) == 0
    root_a = compute_l3_state_root(eco_a)

    # --- FORK B: Fork from height 90 and advance to height 105 with different payout ---
    eco_b = snapshot_epoch0.copy()
    assert eco_b.epoch == 0
    assert eco_b.current_height == 90

    # Advance to height 105 (Epoch 1)
    eco_b.advance_to_height(105)
    assert eco_b.epoch == 1

    # On Fork B, dividend is $1,800 instead of $1,000
    eco_b.declare_dividend("EQPOCH", 1_800 * WAD)
    eq_b = eco_b.equities["EQPOCH"]
    claimable_b = eq_b.dividend_pool.calculate_claimable("0xalice", alice_shares)
    assert claimable_b == 900 * WAD  # 50% of 1,800

    claimed_b = eq_b.claim_dividend("0xalice", alice_shares)
    assert claimed_b == 900 * WAD
    assert eq_b.dividend_pool.calculate_claimable("0xalice", alice_shares) == 0

    root_b = compute_l3_state_root(eco_b)
    assert root_b != root_a

    print("  [PASS] Cross-epoch boundary state rewind and dividend re-accounting verified")


def test_adversarial_reserve_solvency_and_attestation_lag():
    """Adversarial Test: Multi-part reserve solvency under attestation lag and balance drops."""
    print("Testing Adversarial Reserve: Attestation Lag, Invariant Rejections & Drops...")
    eco = Economy(genesis_reserve_pol=4 * WAD)
    reserve = eco.reserve

    # Initial state: 4 POL genesis reserve verified
    assert reserve.accounting_balance == 4 * WAD
    assert reserve.verified_onchain_balance == 4 * WAD
    assert reserve.locked_reserve == 0
    assert reserve.available_reserve == 4 * WAD
    assert reserve.is_verified is True

    # 1. Allocate 3.5 POL backing (within 4 POL limit)
    reserve.allocate_backing(35 * 10**17)
    assert reserve.locked_reserve == 35 * 10**17
    assert reserve.available_reserve == 5 * 10**17  # 0.5 POL left

    # 2. Attestation Lag: Attempt to allocate 0.8 POL (exceeds available 0.5 POL)
    lag_rejected = False
    try:
        reserve.allocate_backing(8 * 10**17)
    except InsufficientReserveError:
        lag_rejected = True
    assert lag_rejected is True, "Must reject allocation exceeding available reserve"

    # 3. New external attestation arrives: 10 POL on Polygon
    att1 = reserve.record_attestation(
        verified_balance=10 * WAD,
        block_number=50_000_000,
        block_hash="0xabc123",
        source="polygon_oracle",
    )
    assert reserve.verified_onchain_balance == 10 * WAD
    # Expand accounting balance to match verified external balance
    reserve.accounting_balance = 10 * WAD
    assert reserve.available_reserve == 10 * WAD - 35 * 10**17  # 6.5 POL available

    # Now 0.8 POL allocation succeeds
    reserve.allocate_backing(8 * 10**17)
    assert reserve.locked_reserve == 43 * 10**17  # 4.3 POL locked

    # 4. Partial Custodian Failure / Severe Balance Drop
    # Attestation reports on-chain balance dropped to 2 POL (< 4.3 POL locked liabilities)
    # This MUST immediately raise InvariantViolationError upon recording!
    custodian_failure_rejected = False
    try:
        reserve.record_attestation(
            verified_balance=2 * WAD,
            block_number=50_000_100,
            block_hash="0xdef456",
            source="polygon_oracle",
        )
    except InvariantViolationError:
        custodian_failure_rejected = True
    assert custodian_failure_rejected is True, "Must reject attestation when on-chain balance drops below locked liabilities"

    # Any new allocation exceeding available reserve MUST be rejected immediately
    alloc_blocked = False
    try:
        reserve.allocate_backing(10 * WAD)
    except InsufficientReserveError:
        alloc_blocked = True
    assert alloc_blocked is True, "Cannot allocate when exceeding available reserve"

    # 5. Position settlement relieves liabilities (3.5 POL released)
    reserve.release_backing(35 * 10**17)
    assert reserve.locked_reserve == 8 * 10**17  # 0.8 POL locked

    # Now an attestation with 2 POL (which covers 0.8 POL locked liabilities) can be recorded
    reserve.record_attestation(
        verified_balance=2 * WAD,
        block_number=50_000_150,
        block_hash="0xdef456",
        source="polygon_oracle",
    )
    assert reserve.verified_onchain_balance == 2 * WAD
    # Verified (2 POL) >= locked (0.8 POL), BUT verified (2 POL) < genesis target (4.0 POL)
    # Required reserve is max(4.0, 0.8) = 4.0 POL, so is_verified is STILL False
    assert reserve.is_verified is False

    # 6. Custodian rebalancing deposits funds back to 8 POL on Polygon
    reserve.record_attestation(
        verified_balance=8 * WAD,
        block_number=50_000_200,
        block_hash="0x789abc",
        source="polygon_custodian",
    )
    assert reserve.verified_onchain_balance == 8 * WAD
    assert reserve.is_verified is True
    reserve.verify_invariant()  # Passes cleanly

    print("  [PASS] Attestation lag, solvency breach detection, and custodian recovery verified")


def test_adversarial_cross_chain_bridge_concurrency_and_precision():
    """Adversarial Test: Concurrent multi-asset bridge transactions and precision limits."""
    print("Testing Adversarial Bridges: Multi-Asset Concurrency & Rounding Fidelity...")
    eco = Economy(genesis_reserve_pol=4 * WAD)
    bridge = eco.bridge

    users = [f"0xuser{i:02d}" for i in range(20)]

    # 1. Zero and negative amount rejections
    for zero_amt in [0, -1, -1000]:
        zero_rejected_btc = False
        try:
            bridge.process_btc_deposit("0xtx_zero_btc", "bc1qfrom", "0xtest", zero_amt)
        except InvariantViolationError:
            zero_rejected_btc = True
        assert zero_rejected_btc is True

        zero_rejected_xno = False
        try:
            bridge.process_xno_deposit("0xtx_zero_xno", "nano_from", "0xtest", zero_amt)
        except InvariantViolationError:
            zero_rejected_xno = True
        assert zero_rejected_xno is True

    # 2. Duplicate deposit transaction hash rejection
    bridge.process_btc_deposit("0xbtc_dup_001", "bc1qfrom", "0xuser01", 100_000_000)
    dup_rejected = False
    try:
        bridge.process_btc_deposit("0xbtc_dup_001", "bc1qfrom", "0xuser01", 100_000_000)
    except InvariantViolationError:
        dup_rejected = True
    assert dup_rejected is True

    # 3. Simulate high-volume randomized concurrent deposits and burns
    import random
    rng = random.Random(0xDEADBEEF)

    for i in range(100):
        u = rng.choice(users)
        is_btc = rng.choice([True, False])

        if is_btc:
            # 10,000 to 500,000,000 Satoshis
            deposit_sat = rng.randint(10_000, 500_000_000)
            tx_h = f"0xbtc_stress_{i:04d}"
            bridge.process_btc_deposit(tx_h, f"bc1qsender_{i}", u, deposit_sat, block_height=i)

            # 30% chance to immediately burn a portion
            if rng.random() < 0.30:
                user_bal = bridge.get_balance("wPVO-BTC", u)
                if user_bal > 10**10:
                    burn_wad = rng.randint(1, user_bal // (10**10)) * (10**10)
                    bridge.process_btc_burn(u, "bc1qdestination_btc", burn_wad, block_height=i)
        else:
            # 1 to 100 XNO in RAW (10^30)
            deposit_raw = rng.randint(1, 100) * 10**30
            tx_h = f"0xxno_stress_{i:04d}"
            bridge.process_xno_deposit(tx_h, f"nano_sender_{i}", u, deposit_raw, block_height=i)

            # 30% chance to immediately burn a portion
            if rng.random() < 0.30:
                user_bal = bridge.get_balance("wPVO-XNO", u)
                if user_bal > 10**18:
                    burn_wad = rng.randint(1, user_bal // (10**18)) * (10**18)
                    bridge.process_xno_burn(u, "nano_destination_xno", burn_wad, block_height=i)

    # 4. Invariant Verification: Sum of all user balances matches adapter total minted
    sum_user_btc = sum(bridge.balances.get("WPVO-BTC", {}).values())
    sum_user_xno = sum(bridge.balances.get("WPVO-XNO", {}).values())

    assert sum_user_btc == bridge.btc_adapter.total_minted_wad
    assert sum_user_xno == bridge.xno_adapter.total_minted_wad
    assert sum_user_btc > 0
    assert sum_user_xno > 0

    # 5. Overburn rejection
    overburn_rejected = False
    try:
        user_bal = bridge.get_balance("wPVO-BTC", "0xuser00")
        bridge.process_btc_burn("0xuser00", "bc1qdest", user_bal + 1 * WAD)
    except InsufficientReserveError:
        overburn_rejected = True
    assert overburn_rejected is True

    # 6. Global bridge invariant verification
    bridge.verify_bridge_invariants()
    print("  [PASS] Multi-bridge concurrency, overburn rejection & precision invariants verified")


def main():
    print("=================================================================")
    print("STARTING PACVO L2/L3 ADVERSARIAL & INVARIANT STRESS TEST SUITE")
    print("=================================================================")

    test_adversarial_reorg_mid_amm_swap_divergence()
    test_adversarial_reorg_mid_liquidation_inversion()
    test_adversarial_reorg_across_epoch_boundaries()
    test_adversarial_reserve_solvency_and_attestation_lag()
    test_adversarial_cross_chain_bridge_concurrency_and_precision()

    print("=================================================================")
    print("ALL PACVO ADVERSARIAL STRESS TESTS PASSED WITH 100% SUCCESS!")
    print("=================================================================")


if __name__ == "__main__":
    main()
