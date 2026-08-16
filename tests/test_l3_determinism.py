"""Determinism and Randomized Invariant Fuzzing for Pacvo L3."""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.l3.anchor import compute_l3_state_root
from pacvo.l3.economy import Economy
from pacvo.l3.fixed import WAD, wad_div, wad_mul


def run_deterministic_economic_cycle(seed: int = 42) -> str:
    """Execute a multi-step economic cycle and return the canonical state root."""
    eco = Economy(genesis_reserve_pol=4 * WAD)

    # 1. Register Assets
    eq = eco.register_equity(
        symbol="PVOA",
        name="Pacvo Alpha",
        token_address="0x1111111111111111111111111111111111111111",
        issuer="0xissuer",
        total_supply=1_000_000 * WAD,
    )
    bond = eco.register_bond(
        symbol="PVOBOND",
        name="Pacvo Treasury Bond",
        token_address="0x2222222222222222222222222222222222222222",
        issuer="0xtreasury",
        total_supply=10_000 * WAD,
        face_value=1_000 * WAD,
        coupon_rate_bps=500,
        coupon_interval_blocks=50,
        maturity_height=500,
    )

    # 2. Market Operations
    mkt = eco.market_manager.create_market("PVOA", "POL", fee_bps=30)
    mkt.add_liquidity("0xlp1", 50_000 * WAD, 25_000 * WAD)
    mkt.swap("0xtrader", 500 * WAD, "POL", min_amount_out=100 * WAD)

    # 3. Lending Operations
    pool = eco.get_or_create_lending_pool("POL")
    pool.deposit("0xlender", 10_000 * WAD)
    pool.borrow("0xborrower", 4_000 * WAD)

    # 4. Advance Heights & Process Coupons
    eco.advance_to_height(100)
    bond.claim_coupon("0xbondholder", 100 * WAD, 100)

    # 5. Treasury & Dividends
    eco.treasury.deposit("POL", 50_000 * WAD)
    eco.declare_dividend("PVOA", 5_000 * WAD)
    eq.claim_dividend("0xholder", 100_000 * WAD)

    return compute_l3_state_root(eco)


def test_100_deterministic_executions():
    print("Testing 100 Repeated Deterministic Economic Executions...")
    baseline_root = run_deterministic_economic_cycle(seed=42)

    for i in range(100):
        root = run_deterministic_economic_cycle(seed=42)
        assert root == baseline_root, f"Determinism failure on iteration {i}: {root} != {baseline_root}"

    print(f"  [PASS] 100/100 executions produced identical state root: {baseline_root}")


def test_randomized_invariant_fuzzing():
    print("Running Randomized Invariant Fuzzing (100 property cycles)...")
    rng = random.Random(0xCAFE1337)

    for cycle in range(100):
        eco = Economy(genesis_reserve_pol=4 * WAD)
        mkt = eco.market_manager.create_market("PVOA", "POL", fee_bps=30)
        mkt.add_liquidity("0xlp", 100_000 * WAD, 100_000 * WAD)

        pool = eco.get_or_create_lending_pool("POL")
        pool.deposit("0xlp", 50_000 * WAD)

        # Execute randomized swaps, deposits, borrows, repayments
        for _ in range(20):
            action = rng.choice(["swap_a", "swap_b", "borrow", "repay"])
            amt = rng.randint(1, 100) * WAD
            if action == "swap_a":
                mkt.swap("0xu", amt, "PVOA", min_amount_out=0)
            elif action == "swap_b":
                mkt.swap("0xu", amt, "POL", min_amount_out=0)
            elif action == "borrow":
                if amt <= pool.total_supplied - pool.total_borrowed:
                    pool.borrow("0xu", amt)
            elif action == "repay":
                pool.repay("0xu", amt)

        # Invariant checks
        assert mkt.reserve_a > 0
        assert mkt.reserve_b > 0
        assert pool.total_supplied >= pool.total_borrowed
        assert pool.total_borrowed >= 0
        assert eco.reserve.verify_invariant()

    print("  [PASS] 100 randomized property fuzzing cycles verified all conservation invariants")


def main():
    test_100_deterministic_executions()
    test_randomized_invariant_fuzzing()
    print("ALL L3 DETERMINISM & FUZZING TESTS PASSED!")


if __name__ == "__main__":
    main()
