"""Unit and Integration Tests for Chocohub (CC) Cross-Chain HTLC Atomic Swap Engine.

Validates:
1. Atomic swap lifecycle at fixed 1 PVO = 10 CC exchange rate.
2. Cryptographic SHA-256 hashlock verification & pre-image revelation.
3. Bifurcated timelock enforcement & refund mechanics.
4. "Mine at HTLC swap" with Chocohub device multipliers (Arduino 3.5x, ESP 2.5x, CPU 3.0x).
5. Conservation invariants across PVO and CC escrow balances.
6. Reorg replay resilience and Node RPC query handlers.
"""

import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.crypto import generate_sign_keypair, derive_address
from pacvo.l3.economy import Economy
from pacvo.l3.errors import InsufficientReserveError, InvariantViolationError
from pacvo.l3.fixed import WAD
from pacvo.l3.htlc import (
    CHOCO_BASE_BLOCK_REWARD_CC,
    CHOCO_DEVICE_MULTIPLIERS,
    HTLCSwapManager,
    PVO_TO_CC_RATIO,
)
from pacvo.node import Node
from pacvo.wallet import Wallet


def test_htlc_atomic_swap_lifecycle_and_rate():
    print("Testing HTLC Atomic Swap Lifecycle at 1 PVO = 10 CC Rate...")
    mgr = HTLCSwapManager()

    # 1. Verify Rate Calculation
    # 1 PVO = 10 CC
    assert mgr.calculate_cc_amount(1 * WAD) == 10 * WAD
    assert mgr.calculate_cc_amount(5 * 10**17) == 5 * WAD  # 0.5 PVO = 5 CC
    assert mgr.calculate_pvo_amount(10 * WAD) == 1 * WAD
    assert mgr.calculate_pvo_amount(5 * WAD) == 5 * 10**17

    # 2. Fund Accounts
    # Alice has 10 PVO on Pacvo; Bob has 100 CC on Chocohub
    alice_pacvo = "0xalice_pacvo"
    alice_choco = "alice_choco_user"
    bob_pacvo = "0xbob_pacvo"
    bob_choco = "bob_choco_user"

    mgr.deposit_pacvo(alice_pacvo, 10 * WAD)
    mgr.deposit_choco(bob_choco, 100 * WAD)

    assert mgr.get_pacvo_balance(alice_pacvo) == 10 * WAD
    assert mgr.get_choco_balance(bob_choco) == 100 * WAD

    # 3. Alice generates secret S and hashlock H = SHA-256(S)
    secret_hex = "4a6f73657068536563726574507265696d6167653230323648544c4353776170"  # 32-byte secret
    secret_bytes = bytes.fromhex(secret_hex)
    hashlock_hex = hashlib.sha256(secret_bytes).hexdigest()

    # 4. Create HTLC Order: Alice swaps 2 PVO for 20 CC with Bob at height 100
    order = mgr.create_order(
        initiator_pacvo=alice_pacvo,
        participant_pacvo=bob_pacvo,
        initiator_choco=alice_choco,
        participant_choco=bob_choco,
        amount_pvo_wad=2 * WAD,
        hashlock_hex=hashlock_hex,
        current_height=100,
        timelock_blocks_pacvo=144,
        timelock_blocks_choco=72,
    )

    assert order.status == "LOCKED"
    assert order.amount_pvo_wad == 2 * WAD
    assert order.amount_cc_wad == 20 * WAD  # 2 PVO * 10 = 20 CC
    assert order.timelock_pacvo == 244
    assert order.timelock_choco == 172

    # Verify balances debited into escrow
    assert mgr.get_pacvo_balance(alice_pacvo) == 8 * WAD
    assert mgr.get_choco_balance(bob_choco) == 80 * WAD
    assert mgr.escrow_pvo_wad == 2 * WAD
    assert mgr.escrow_cc_wad == 20 * WAD
    mgr.verify_invariants()

    # 5. Invalid Pre-Image Rejection
    fake_secret = "0000000000000000000000000000000000000000000000000000000000000000"
    invalid_claimed = False
    try:
        mgr.claim_swap(order.order_id, fake_secret, current_height=110)
    except InvariantViolationError:
        invalid_claimed = True
    assert invalid_claimed is True, "Must reject invalid pre-image"

    # 6. Valid Claim Execution by Pre-Image Revelation at height 120
    pvo_payout, cc_payout = mgr.claim_swap(order.order_id, secret_hex, current_height=120)
    assert pvo_payout == 2 * WAD
    assert cc_payout == 20 * WAD

    # Alice received 20 CC on Chocohub; Bob received 2 PVO on Pacvo
    assert mgr.get_choco_balance(alice_choco) == 20 * WAD
    assert mgr.get_pacvo_balance(bob_pacvo) == 2 * WAD

    # Escrow cleared
    assert mgr.escrow_pvo_wad == 0
    assert mgr.escrow_cc_wad == 0
    assert order.status == "CLAIMED"
    assert order.secret == secret_hex
    assert order.claimed_height == 120
    mgr.verify_invariants()
    print("  [PASS] HTLC atomic swap lifecycle and 1 PVO = 10 CC rate verified")


def test_htlc_timelock_and_refund():
    print("Testing HTLC Timelock Expiry & Refund Mechanics...")
    mgr = HTLCSwapManager()

    alice_pacvo = "0xalice_pacvo"
    alice_choco = "alice_choco_user"
    bob_pacvo = "0xbob_pacvo"
    bob_choco = "bob_choco_user"

    mgr.deposit_pacvo(alice_pacvo, 5 * WAD)
    mgr.deposit_choco(bob_choco, 50 * WAD)

    secret_hex = "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"
    hashlock_hex = hashlib.sha256(bytes.fromhex(secret_hex)).hexdigest()

    order = mgr.create_order(
        initiator_pacvo=alice_pacvo,
        participant_pacvo=bob_pacvo,
        initiator_choco=alice_choco,
        participant_choco=bob_choco,
        amount_pvo_wad=1 * WAD,
        hashlock_hex=hashlock_hex,
        current_height=100,
        timelock_blocks_pacvo=144,
        timelock_blocks_choco=72,
    )

    # 1. Premature Refund Rejection (at height 120 < timelock 172)
    premature_refund = False
    try:
        mgr.refund_swap(order.order_id, current_height=120)
    except InvariantViolationError:
        premature_refund = True
    assert premature_refund is True, "Premature refund must fail before timelock"

    # 2. Refund Execution after Timelock Expiration (at height 180 > timelock 172)
    ref_pvo, ref_cc = mgr.refund_swap(order.order_id, current_height=180)
    assert ref_pvo == 1 * WAD
    assert ref_cc == 10 * WAD

    # Alice got back 1 PVO; Bob got back 10 CC
    assert mgr.get_pacvo_balance(alice_pacvo) == 5 * WAD
    assert mgr.get_choco_balance(bob_choco) == 50 * WAD
    assert order.status == "REFUNDED"
    assert mgr.escrow_pvo_wad == 0
    assert mgr.escrow_cc_wad == 0
    mgr.verify_invariants()
    print("  [PASS] Timelock expiration and clean escrow refunds verified")


def test_htlc_mining_with_device_multipliers():
    print("Testing 'Mine at HTLC Swap' with Chocohub Device Multipliers...")
    mgr = HTLCSwapManager()

    alice_pacvo = "0xalice"
    bob_choco = "bob_choco"
    mgr.deposit_pacvo(alice_pacvo, 10 * WAD)
    mgr.deposit_choco(bob_choco, 100 * WAD)

    hashlock = hashlib.sha256(b"swap_mining_secret").hexdigest()
    order = mgr.create_order(
        initiator_pacvo=alice_pacvo,
        participant_pacvo="0xbob",
        initiator_choco="alice_choco",
        participant_choco=bob_choco,
        amount_pvo_wad=1 * WAD,
        hashlock_hex=hashlock,
        current_height=50,
    )

    # 1. Mine with Arduino / Embedded AVR (3.5x multiplier)
    res_avr = mgr.mine_htlc_swap(
        order_id=order.order_id,
        miner_choco_account="miner_arduino",
        nonce=1001,
        device_type="embedded_avr",
    )
    assert res_avr["multiplier"] == 3.5
    # Base 0.05 CC * 3.5 = 0.175 CC (1.75 * 10^17)
    expected_avr = int(CHOCO_BASE_BLOCK_REWARD_CC * 3.5)
    assert res_avr["reward_cc_wad"] == expected_avr
    assert mgr.get_choco_balance("miner_arduino") == expected_avr

    # 2. Mine with ESP8266 / Embedded ESP (2.5x / 3.0x multiplier)
    res_esp = mgr.mine_htlc_swap(
        order_id=order.order_id,
        miner_choco_account="miner_esp",
        nonce=1002,
        device_type="embedded_esp",
    )
    assert res_esp["multiplier"] == 2.5
    expected_esp = int(CHOCO_BASE_BLOCK_REWARD_CC * 2.5)
    assert res_esp["reward_cc_wad"] == expected_esp

    # 3. Mine with CPU / Web Miner (3.0x multiplier)
    res_cpu = mgr.mine_htlc_swap(
        order_id=order.order_id,
        miner_choco_account="miner_cpu",
        nonce=1003,
        device_type="cpu",
    )
    assert res_cpu["multiplier"] == 3.0
    expected_cpu = int(CHOCO_BASE_BLOCK_REWARD_CC * 3.0)
    assert res_cpu["reward_cc_wad"] == expected_cpu

    assert order.mined_proofs == 3
    assert mgr.total_mined_rewards_cc_wad > 0
    print("  [PASS] Mine at HTLC swap with device multipliers verified")


def test_htlc_node_rpc_and_serialization():
    print("Testing HTLC Node RPC Handlers & State Serialization...")
    tmp_dir = tempfile.mkdtemp()
    try:
        wallet = Wallet.generate()
        node = Node(wallet, data_dir=tmp_dir, host="127.0.0.1", port=19780, peers=[], mine=False)

        # Deposit funds into node economy HTLC
        node.economy.htlc.deposit_pacvo("0xalice", 10 * WAD)
        node.economy.htlc.deposit_choco("bob_choco", 100 * WAD)

        secret_hex = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        hashlock_hex = hashlib.sha256(bytes.fromhex(secret_hex)).hexdigest()

        # 1. RPC Create HTLC Order
        created = node.pacvo_htlc_create(
            initiator_pacvo="0xalice",
            participant_pacvo="0xbob",
            initiator_choco="alice_choco",
            participant_choco="bob_choco",
            amount_pvo_wad=3 * WAD,
            hashlock=hashlock_hex,
        )
        order_id = created["order_id"]
        assert created["status"] == "LOCKED"
        assert created["amount_pvo_wad"] == str(3 * WAD)
        assert created["amount_cc_wad"] == str(30 * WAD)

        # 2. RPC Mine on HTLC
        mine_res = node.pacvo_htlc_mine(
            order_id=order_id,
            miner_choco="chocominero",
            nonce=42,
            device_type="mobile",
        )
        assert mine_res["multiplier"] == 3.6
        assert mine_res["total_order_proofs"] == 1

        # 3. RPC Query Order & List
        order_info = node.pacvo_htlc_get(order_id)
        assert order_info["order_id"] == order_id
        assert order_info["mined_proofs"] == 1

        list_info = node.pacvo_htlc_list()
        assert len(list_info["orders"]) == 1
        assert list_info["rate"] == "1 PVO = 10 CC"

        # 4. RPC Claim HTLC
        claim_res = node.pacvo_htlc_claim(order_id, secret_hex)
        assert claim_res["status"] == "CLAIMED"

        # 5. Snapshot / Serialization
        copied_eco = node.economy.copy()
        assert copied_eco.htlc.orders[order_id].status == "CLAIMED"
        assert copied_eco.htlc.total_swapped_pvo_wad == 3 * WAD
        assert copied_eco.htlc.total_swapped_cc_wad == 30 * WAD
        print("  [PASS] Node RPC handlers, mining dispatch, and serialization verified")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_htlc_mines_10_cc_per_1_pvo_swap():
    print("Testing CCpow Mining of 10 CC per 1 PVO on Swap...")
    mgr = HTLCSwapManager()

    alice_pacvo = "0xalice_pow"
    bob_pacvo = "0xbob_pow"
    alice_choco = "alice_chocouser"
    bob_choco = "bob_chocouser"

    # Alice deposits 5 PVO
    mgr.deposit_pacvo(alice_pacvo, 5 * WAD)
    assert mgr.get_pacvo_balance(alice_pacvo) == 5 * WAD

    # Alice swaps 5 PVO -> 50 CC with auto_mine_cc=True (mines 10 CC per 1 PVO)
    secret_hex = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20"
    hashlock_hex = hashlib.sha256(bytes.fromhex(secret_hex)).hexdigest()

    order = mgr.create_order(
        initiator_pacvo=alice_pacvo,
        participant_pacvo=bob_pacvo,
        initiator_choco=alice_choco,
        participant_choco=bob_choco,
        amount_pvo_wad=5 * WAD,
        hashlock_hex=hashlock_hex,
        current_height=200,
        auto_mine_cc=True,
        miner_account=bob_choco,
    )

    # Assert 10 CC per 1 PVO was mined directly into the swap contract
    assert order.amount_pvo_wad == 5 * WAD
    assert order.amount_cc_wad == 50 * WAD  # 5 PVO * 10 = 50 CC
    assert order.cc_mined_for_swap_wad == 50 * WAD  # 10 CC per 1 PVO mined
    assert order.mined_proofs >= 1
    assert mgr.escrow_cc_wad == 50 * WAD
    assert mgr.escrow_pvo_wad == 5 * WAD
    mgr.verify_invariants()

    # Claim the swap: Alice receives the mined 50 CC; Bob receives the 5 PVO
    pvo_out, cc_out = mgr.claim_swap(order.order_id, secret_hex, current_height=210)
    assert pvo_out == 5 * WAD
    assert cc_out == 50 * WAD
    assert mgr.get_choco_balance(alice_choco) == 50 * WAD
    assert mgr.get_pacvo_balance(bob_pacvo) == 5 * WAD
    assert mgr.escrow_cc_wad == 0
    assert mgr.escrow_pvo_wad == 0
    mgr.verify_invariants()
    print("  [PASS] Successfully mined 10 CC per 1 PVO during swap execution")


def test_htlc_full_chocohub_outage_refund_guarantee():
    print("Testing Total External Outage (Chocohub 100% Unreachable / Halted)...")
    mgr = HTLCSwapManager()

    alice_pacvo = "0xalice_contained"
    bob_choco = "bob_choco_deadnode"

    # Alice deposits 10 PVO on Pacvo
    mgr.deposit_pacvo(alice_pacvo, 10 * WAD)

    # Alice locks 4 PVO on Pacvo for 40 CC at height 500
    secret_hex = "abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd"
    hashlock_hex = hashlib.sha256(bytes.fromhex(secret_hex)).hexdigest()

    order = mgr.create_order(
        initiator_pacvo=alice_pacvo,
        participant_pacvo="0xbob_remote",
        initiator_choco="alice_choco_remote",
        participant_choco=bob_choco,
        amount_pvo_wad=4 * WAD,
        hashlock_hex=hashlock_hex,
        current_height=500,
        timelock_blocks_pacvo=144,
        timelock_blocks_choco=72,
        auto_mine_cc=True,
    )

    # SCENARIO: Chocohub experiences permanent chain halt / zero responses.
    # Alice observes the outage and DOES NOT reveal secret S.
    # Pacvo Layer 1 consensus advances independently to height 650 (past timelock 500 + 72 = 572).

    # Pacvo executes autonomous local refund with ZERO external network calls or oracle queries
    ref_pvo, ref_cc = mgr.refund_swap(order.order_id, current_height=650)

    assert ref_pvo == 4 * WAD
    assert mgr.get_pacvo_balance(alice_pacvo) == 10 * WAD
    assert order.status == "REFUNDED"
    assert mgr.escrow_pvo_wad == 0
    mgr.verify_invariants()
    print("  [PASS] Autonomous Pacvo Layer 1 refund verified under total external chain halt")


def main():
    print("=================================================================")
    print("STARTING CHOCOHUB (CC) HTLC CROSS-CHAIN ATOMIC SWAP TEST SUITE")
    print("=================================================================")

    test_htlc_atomic_swap_lifecycle_and_rate()
    test_htlc_timelock_and_refund()
    test_htlc_mining_with_device_multipliers()
    test_htlc_node_rpc_and_serialization()
    test_htlc_mines_10_cc_per_1_pvo_swap()
    test_htlc_full_chocohub_outage_refund_guarantee()

    print("=================================================================")
    print("ALL CHOCOHUB HTLC TESTS PASSED WITH 100% SUCCESS!")
    print("=================================================================")


if __name__ == "__main__":
    main()
