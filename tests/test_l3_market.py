"""Unit and Integration Tests for L3 Constant-Product AMM Markets."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.l3.amm import ConstantProductAMM
from pacvo.l3.errors import InvariantViolationError, SlippageExceededError
from pacvo.l3.fixed import WAD
from pacvo.l3.market import MarketManager


def test_amm_liquidity_and_swaps():
    print("Testing AMM Liquidity Provision & Swaps...")
    mkt = ConstantProductAMM(
        pair_id="PVOA/POL",
        token_a="PVOA",
        token_b="POL",
        fee_bps=30, # 0.30%
    )

    # 1. Initial Liquidity Provision
    # Deposit 10,000 PVOA and 5,000 POL
    lp_shares = mkt.add_liquidity("0xalice", 10_000 * WAD, 5_000 * WAD)
    assert lp_shares > 0
    assert mkt.reserve_a == 10_000 * WAD
    assert mkt.reserve_b == 5_000 * WAD
    assert mkt.total_lp_shares == lp_shares

    # Spot Price of A in terms of B = 5000 / 10000 = 0.5 POL
    spot_price = mkt.get_spot_price_a_in_b()
    assert spot_price == 5 * 10**17 # 0.5 WAD

    # 2. Swap Execution
    # Swap 100 POL for PVOA
    amount_in = 100 * WAD
    expected_out = mkt.get_amount_out(amount_in, "POL")
    assert expected_out > 0

    # Execute swap with acceptable slippage
    out = mkt.swap("0xbob", amount_in, "POL", min_amount_out=expected_out)
    assert out == expected_out
    assert mkt.reserve_b == 5_100 * WAD
    assert mkt.reserve_a == 10_000 * WAD - out

    # 3. Slippage Protection Rejection
    try:
        mkt.swap("0xbob", 50 * WAD, "POL", min_amount_out=500 * WAD)
        assert False, "Should have raised SlippageExceededError"
    except SlippageExceededError:
        pass

    # 4. Remove Liquidity
    # Alice removes half of her LP shares
    rem_a, rem_b = mkt.remove_liquidity("0xalice", lp_shares // 2)
    assert rem_a > 0 and rem_b > 0
    assert mkt.lp_balances["0xalice"] == lp_shares - (lp_shares // 2)

    print("  [PASS] AMM liquidity, constant-product swaps, and slippage protection verified")


def test_market_manager():
    print("Testing Market Manager Pair Routing...")
    mm = MarketManager()
    m1 = mm.create_market("PVOA", "POL", fee_bps=30)
    assert m1.pair_id == "POL/PVOA"

    m2 = mm.get_market("POL", "PVOA")
    assert m2 is m1

    assert len(mm.list_markets()) == 1
    print("  [PASS] Market manager pair routing verified")


def main():
    test_amm_liquidity_and_swaps()
    test_market_manager()
    print("ALL L3 MARKET TESTS PASSED!")


if __name__ == "__main__":
    main()
