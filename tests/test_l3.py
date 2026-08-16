"""Comprehensive Pacvo Layer 3 (L3) — PVO-Fi Test Suite."""

import asyncio
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.l3.anchor import L3Anchor, compute_l3_state_root
from pacvo.l3.economy import Economy
from pacvo.l3.errors import InvariantViolationError
from pacvo.l3.fixed import WAD
from pacvo.network import rpc_call
from pacvo.node import Node


def test_fund_basket_and_nav():
    print("Testing Tokenized Basket Fund & NAV Calculation...")
    eco = Economy(genesis_reserve_pol=4 * WAD)

    # Register Fund holding 50% PVOA and 50% POL
    fund = eco.register_fund(
        symbol="PVOFUND",
        name="Pacvo Balanced Growth Fund",
        token_address="0x3333333333333333333333333333333333333333",
        issuer="0xmanager",
        allocations_bps={"PVOA": 5000, "POL": 5000},
    )
    assert fund.asset.total_supply == 0

    # Pricing: PVOA is $2.00 (2.0 WAD), POL is $1.00 (1.0 WAD)
    prices = {"PVOA": 2 * WAD, "POL": 1 * WAD}
    initial_nav = fund.calculate_nav(prices)
    assert initial_nav == WAD  # 1.0 WAD

    # Alice deposits 500 PVOA ($1,000 value) and 1,000 POL ($1,000 value) -> Total deposit $2,000
    shares_minted = fund.deposit_and_mint(
        "0xalice",
        {"PVOA": 500 * WAD, "POL": 1_000 * WAD},
        prices,
    )
    assert shares_minted == 2_000 * WAD  # 2,000 fund shares
    assert fund.asset.total_supply == 2_000 * WAD

    # PVOA price doubles to $4.00!
    # Total holdings: 500 PVOA * $4.00 = $2,000. 1,000 POL * $1.00 = $1,000. Total Fund Value = $3,000.
    # NAV = $3,000 / 2,000 shares = $1.50 (1.5 WAD)
    updated_prices = {"PVOA": 4 * WAD, "POL": 1 * WAD}
    new_nav = fund.calculate_nav(updated_prices)
    assert new_nav == 15 * 10**17  # 1.5 WAD

    # Alice redeems 1,000 shares (50% of fund)
    withdrawn = fund.redeem_and_withdraw("0xalice", 1_000 * WAD, updated_prices)
    assert withdrawn["PVOA"] == 250 * WAD
    assert withdrawn["POL"] == 500 * WAD
    assert fund.asset.total_supply == 1_000 * WAD

    print("  [PASS] Tokenized basket fund and dynamic NAV valuation verified")


def test_l3_rpc_endpoints():
    """Test L3 RPC handler methods directly (in-process, no network round-trip)."""
    print("Testing L3 RPC Methods (pacvo_l3_*)...")
    import tempfile, shutil
    from pacvo.wallet import Wallet
    from pacvo.crypto import generate_sign_keypair as _gsk

    tmpdir = tempfile.mkdtemp()
    try:
        _pk, _sk = _gsk()
        wallet = Wallet(_pk, _sk)
        node = Node(wallet, tmpdir, "127.0.0.1", 0, [], False)

        # 1. pacvo_l3_getReserve
        r_res = node.pacvo_l3_get_reserve()
        assert int(r_res["total_reserve"]) == 4 * 10**18
        assert int(r_res["available_reserve"]) == 4 * 10**18
        assert int(r_res["polygon_chain_id"]) == 137
        assert r_res["reserve_wallet_address"] == "0xe9D970937ba528245BAeD156aFe036e0Fa565218"
        assert r_res["is_verified"] is True

        # 2. pacvo_l3_getEconomy
        r_eco = node.pacvo_l3_get_economy()
        assert "registry" in r_eco
        assert "reserve" in r_eco

        # 3. pacvo_l3_getStateRoot
        r_root = node.pacvo_l3_get_state_root()
        assert len(r_root["state_root"]) == 64

        # 4. pacvo_l3_getAnchor
        r_anc = node.pacvo_l3_get_anchor()
        assert len(r_anc["state_root"]) == 64
        assert r_anc["l3_epoch"] == 0

        # 5. pacvo_l3_getPrice
        r_prc = node.pacvo_l3_get_price("POL")
        assert int(r_prc["price_wad"]) == 10**18

        print("  [PASS] L3 RPC query endpoints verified")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



def main():
    print("=================================================================")
    print("STARTING PACVO LAYER 3 (L3) PVO-FI TEST SUITE")
    print("=================================================================")
    from tests.test_l3_economy import main as test_economy_main
    from tests.test_l3_market import main as test_market_main
    from tests.test_l3_lending import main as test_lending_main
    from tests.test_l3_determinism import main as test_determinism_main
    from tests.test_l3_reorg import main as test_reorg_main

    test_economy_main()
    test_market_main()
    test_lending_main()
    test_fund_basket_and_nav()
    test_determinism_main()
    test_reorg_main()
    test_l3_rpc_endpoints()

    print("=================================================================")
    print("ALL PACVO L3 TESTS PASSED WITH 100% SUCCESS!")
    print("=================================================================")


if __name__ == "__main__":
    main()
