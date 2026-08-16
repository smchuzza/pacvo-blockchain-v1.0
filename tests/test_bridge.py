"""Unit and Integration Tests for Native Cross-Chain Bridges (wPVO-BTC and wPVO-XNO)."""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.crypto import generate_sign_keypair
from pacvo.l3.bridge import (
    DEFAULT_BTC_VAULT_ADDRESS,
    DEFAULT_CC_VAULT_ADDRESS,
    DEFAULT_XNO_VAULT_ADDRESS,
    BitcoinBridgeAdapter,
    BridgeManager,
    ChocohubBridgeAdapter,
    NanoBridgeAdapter,
)
from pacvo.l3.economy import Economy
from pacvo.l3.errors import InsufficientReserveError, InvariantViolationError
from pacvo.l3.fixed import WAD
from pacvo.node import Node
from pacvo.wallet import Wallet


def test_bitcoin_bridge_unit_conversions_and_lifecycle():
    print("Testing Bitcoin Bridge (wPVO-BTC) Conversions & Lifecycle...")
    bridge = BridgeManager()

    # 1. Test Unit Conversions (8 decimals -> 18 decimals)
    # 1 BTC = 100,000,000 Satoshis = 10^18 WAD (1.0 WAD)
    assert BitcoinBridgeAdapter.satoshis_to_wad(100_000_000) == 1 * WAD
    assert BitcoinBridgeAdapter.wad_to_satoshis(1 * WAD) == 100_000_000

    # 0.5 BTC = 50,000,000 Satoshis = 0.5 WAD
    assert BitcoinBridgeAdapter.satoshis_to_wad(50_000_000) == 5 * 10**17
    assert BitcoinBridgeAdapter.wad_to_satoshis(5 * 10**17) == 50_000_000

    # 2. Deposit 1.0 BTC (100,000,000 satoshis) -> Mint wPVO-BTC
    # Fee: 15 bps (0.15%) = 0.0015 BTC = 150,000 satoshis (1.5 * 10^15 WAD)
    # Net Mint: 0.9985 BTC = 99,850,000 satoshis (9.985 * 10^17 WAD)
    rec = bridge.process_btc_deposit(
        external_tx_hash="0xbtc_tx_001_deposit",
        external_from="bc1qsender_alice",
        pacvo_recipient="0xalice",
        satoshis=100_000_000,
        block_height=100,
    )
    assert rec.status == "CONFIRMED"
    assert rec.mint_amount_wad == 998_500_000_000_000_000  # 0.9985 WAD
    assert rec.fee_wad == 1_500_000_000_000_000  # 0.0015 WAD
    assert bridge.get_balance("wPVO-BTC", "0xalice") == 998_500_000_000_000_000
    assert bridge.btc_adapter.total_locked_satoshis == 100_000_000
    assert bridge.btc_adapter.total_minted_wad == 998_500_000_000_000_000

    # Duplicate tx rejection
    try:
        bridge.process_btc_deposit(
            external_tx_hash="0xbtc_tx_001_deposit",
            external_from="bc1qsender_alice",
            pacvo_recipient="0xalice",
            satoshis=100_000_000,
        )
        assert False, "Should reject duplicate Bitcoin tx"
    except InvariantViolationError:
        pass

    # 3. Burn 0.5 wPVO-BTC to withdraw to Bitcoin address
    # 0.5 WAD burn -> fee 15 bps = 0.00075 WAD. Net: 0.49925 WAD = 49,925,000 satoshis.
    burn_rec = bridge.process_btc_burn(
        pacvo_sender="0xalice",
        external_btc_destination="bc1qrecipient_alice",
        amount_wad=5 * 10**17,
        block_height=105,
    )
    assert burn_rec.status == "COMMITTED"
    assert burn_rec.raw_unlock_amount == 49_925_000  # satoshis
    assert bridge.get_balance("wPVO-BTC", "0xalice") == 998_500_000_000_000_000 - 5 * 10**17

    # Complete release settlement
    bridge.complete_burn_release(burn_rec.burn_id, "0xbtc_claim_tx_hash_123")
    assert bridge.burns[burn_rec.burn_id].status == "SETTLED"

    # Invariant check
    assert bridge.verify_bridge_invariants() is True
    print("  [PASS] Bitcoin bridge conversions, lock/mint, and burn/unlock verified")


def test_nano_bridge_unit_conversions_and_lifecycle():
    print("Testing Nano Bridge (wPVO-XNO) Conversions & Lifecycle...")
    bridge = BridgeManager()

    # 1. Test Unit Conversions (30 decimals -> 18 decimals)
    # 1 XNO = 10^30 raw = 10^18 WAD (1.0 WAD)
    assert NanoBridgeAdapter.raw_to_wad(10**30) == 1 * WAD
    assert NanoBridgeAdapter.wad_to_raw(1 * WAD) == 10**30

    # 100 XNO = 100 * 10^30 raw = 100 * 10^18 WAD
    assert NanoBridgeAdapter.raw_to_wad(100 * 10**30) == 100 * WAD
    assert NanoBridgeAdapter.wad_to_raw(100 * 10**30 // 10**12) == 100 * 10**30

    # 2. Deposit 10 XNO (10 * 10^30 raw) -> Mint wPVO-XNO
    # Fee: 10 bps (0.10%) = 0.01 XNO. Net Mint: 9.99 XNO = 9.99 * 10^18 WAD
    rec = bridge.process_xno_deposit(
        external_block_hash="0xxno_block_001_send",
        external_from="nano_1sender_bob",
        pacvo_recipient="0xbob",
        raw_amount=10 * 10**30,
        block_height=200,
    )
    assert rec.status == "CONFIRMED"
    assert rec.mint_amount_wad == 999 * 10**16  # 9.99 WAD
    assert rec.fee_wad == 1 * 10**16  # 0.01 WAD
    assert bridge.get_balance("wPVO-XNO", "0xbob") == 999 * 10**16
    assert bridge.xno_adapter.total_locked_raw == 10 * 10**30
    assert bridge.xno_adapter.total_minted_wad == 999 * 10**16

    # Duplicate block hash rejection
    try:
        bridge.process_xno_deposit(
            external_block_hash="0xxno_block_001_send",
            external_from="nano_1sender_bob",
            pacvo_recipient="0xbob",
            raw_amount=10 * 10**30,
        )
        assert False, "Should reject duplicate Nano block hash"
    except InvariantViolationError:
        pass

    # 3. Burn 5 wPVO-XNO to withdraw to Nano address
    # 5 WAD burn -> fee 10 bps = 0.005 WAD. Net: 4.995 WAD = 4.995 * 10^30 raw.
    burn_rec = bridge.process_xno_burn(
        pacvo_sender="0xbob",
        external_nano_destination="nano_1recipient_bob",
        amount_wad=5 * 10**18,
        block_height=205,
    )
    assert burn_rec.status == "COMMITTED"
    assert burn_rec.raw_unlock_amount == 4_995 * 10**27  # raw
    assert bridge.get_balance("wPVO-XNO", "0xbob") == (999 * 10**16) - (5 * 10**18)

    # Complete release settlement
    bridge.complete_burn_release(burn_rec.burn_id, "0xxno_receive_block_hash_456")
    assert bridge.burns[burn_rec.burn_id].status == "SETTLED"

    # Invariant check
    assert bridge.verify_bridge_invariants() is True
    print("  [PASS] Nano bridge conversions, lock/mint, and burn/unlock verified")


def test_chocohub_bridge_unit_conversions_and_lifecycle():
    print("Testing Chocohub Bridge (wCCPVO) Conversions & Lifecycle...")
    bridge = BridgeManager()

    # 1. Test Unit Conversions (8 decimals -> 18 decimals)
    # 1 CC = 100,000,000 Base Units = 10^18 WAD (1.0 WAD)
    assert ChocohubBridgeAdapter.raw_to_wad(100_000_000) == 1 * WAD
    assert ChocohubBridgeAdapter.wad_to_raw(1 * WAD) == 100_000_000

    # 50 CC = 5,000,000,000 Base Units = 50 WAD
    assert ChocohubBridgeAdapter.raw_to_wad(5_000_000_000) == 50 * WAD
    assert ChocohubBridgeAdapter.wad_to_raw(50 * WAD) == 5_000_000_000

    # 2. Deposit 100 CC (10,000,000,000 units) -> Mint wCCPVO
    # Fee: 10 bps (0.10%) = 0.1 CC = 10,000,000 units (10^17 WAD)
    # Net Mint: 99.9 CC = 99.9 * 10^18 WAD
    rec = bridge.process_cc_deposit(
        external_tx_hash="0xcc_tx_001_deposit",
        external_from="choco_sender_carol",
        pacvo_recipient="0xcarol",
        raw_amount=10_000_000_000,
        block_height=300,
    )
    assert rec.status == "CONFIRMED"
    assert rec.mint_amount_wad == 99_900_000_000_000_000_000  # 99.9 WAD
    assert rec.fee_wad == 100_000_000_000_000_000  # 0.1 WAD
    assert bridge.get_balance("wCCPVO", "0xcarol") == 99_900_000_000_000_000_000
    assert bridge.cc_adapter.total_locked_raw == 10_000_000_000
    assert bridge.cc_adapter.total_minted_wad == 99_900_000_000_000_000_000

    # Duplicate tx rejection
    try:
        bridge.process_cc_deposit(
            external_tx_hash="0xcc_tx_001_deposit",
            external_from="choco_sender_carol",
            pacvo_recipient="0xcarol",
            raw_amount=10_000_000_000,
        )
        assert False, "Should reject duplicate Chocohub tx"
    except InvariantViolationError:
        pass

    # 3. Burn 50 wCCPVO to withdraw to Chocohub address
    # 50 WAD burn -> fee 10 bps = 0.05 WAD. Net: 49.95 WAD = 4,995,000,000 units.
    burn_rec = bridge.process_cc_burn(
        pacvo_sender="0xcarol",
        external_choco_destination="choco_recipient_carol",
        amount_wad=50 * WAD,
        block_height=305,
    )
    assert burn_rec.status == "COMMITTED"
    assert burn_rec.raw_unlock_amount == 4_995_000_000  # raw units
    assert bridge.get_balance("wCCPVO", "0xcarol") == 99_900_000_000_000_000_000 - 50 * WAD

    # Complete release settlement
    bridge.complete_burn_release(burn_rec.burn_id, "0xcc_release_tx_789")
    assert bridge.burns[burn_rec.burn_id].status == "SETTLED"

    # Invariant check
    assert bridge.verify_bridge_invariants() is True
    print("  [PASS] Chocohub bridge conversions, lock/mint, and burn/unlock verified")


def test_bridge_economy_integration_and_serialization():
    print("Testing Bridge Integration with L3 Economy & Serialization...")
    eco = Economy(genesis_reserve_pol=4 * WAD)

    # Verify Assets Registered in AssetRegistry
    assert eco.registry.get_asset("wPVO-BTC") is not None
    assert eco.registry.get_asset("wPVO-XNO") is not None
    assert eco.registry.get_asset("wCCPVO") is not None
    assert eco.registry.get_asset("wPVO-BTC").symbol == "wPVO-BTC"
    assert eco.registry.get_asset("wPVO-XNO").symbol == "wPVO-XNO"
    assert eco.registry.get_asset("wCCPVO").symbol == "wCCPVO"

    # Verify External Reserve Adapters in PVOFiReserve
    btc_adapter = eco.reserve.get_external_adapter("wPVO-BTC")
    xno_adapter = eco.reserve.get_external_adapter("wPVO-XNO")
    cc_adapter = eco.reserve.get_external_adapter("wCCPVO")
    assert btc_adapter is not None
    assert xno_adapter is not None
    assert cc_adapter is not None
    assert btc_adapter.wallet_address == DEFAULT_BTC_VAULT_ADDRESS
    assert xno_adapter.wallet_address == DEFAULT_XNO_VAULT_ADDRESS
    assert cc_adapter.wallet_address == DEFAULT_CC_VAULT_ADDRESS

    # Process deposits via Economy.bridge
    eco.bridge.process_btc_deposit(
        external_tx_hash="0xbtc_deposit_tx_999",
        external_from="bc1quser",
        pacvo_recipient="0xuser1",
        satoshis=50_000_000,  # 0.5 BTC
        block_height=10,
    )
    eco.bridge.process_xno_deposit(
        external_block_hash="0xxno_deposit_block_999",
        external_from="nano_1user",
        pacvo_recipient="0xuser2",
        raw_amount=20 * 10**30,  # 20 XNO
        block_height=10,
    )
    eco.bridge.process_cc_deposit(
        external_tx_hash="0xcc_deposit_tx_999",
        external_from="choco_1user",
        pacvo_recipient="0xuser3",
        raw_amount=50_000_000_000,  # 500 CC
        block_height=10,
    )

    # Verify serialization roundtrip
    d = eco.bridge.to_dict()
    assert "btc_bridge" in d
    assert "xno_bridge" in d
    assert "cc_bridge" in d
    assert len(d["deposits"]) == 3

    restored = BridgeManager.from_dict(d)
    assert restored.btc_adapter.total_locked_satoshis == 50_000_000
    assert restored.xno_adapter.total_locked_raw == 20 * 10**30
    assert restored.cc_adapter.total_locked_raw == 50_000_000_000
    assert restored.get_balance("wPVO-BTC", "0xuser1") == eco.bridge.get_balance("wPVO-BTC", "0xuser1")
    assert restored.get_balance("wPVO-XNO", "0xuser2") == eco.bridge.get_balance("wPVO-XNO", "0xuser2")
    assert restored.get_balance("wCCPVO", "0xuser3") == eco.bridge.get_balance("wCCPVO", "0xuser3")
    assert restored.verify_bridge_invariants() is True

    print("  [PASS] Bridge economy integration, asset registration, and serialization verified")


def test_bridge_rpc_methods():
    print("Testing Bridge RPC Methods...")
    tmpdir = tempfile.mkdtemp()
    try:
        pk, sk = generate_sign_keypair()
        wallet = Wallet(pk, sk)
        node = Node(wallet, tmpdir, "127.0.0.1", 0, [], False)

        # 1. Bridge Status
        status = node.pacvo_bridge_status()
        assert "btc_bridge" in status
        assert "xno_bridge" in status
        assert "cc_bridge" in status

        # 2. Bridge Vault Queries
        btc_vault = node.pacvo_bridge_get_vault("wPVO-BTC")
        assert btc_vault["chain"] == "Bitcoin"
        assert btc_vault["vault_address"] == DEFAULT_BTC_VAULT_ADDRESS

        xno_vault = node.pacvo_bridge_get_vault("wPVO-XNO")
        assert xno_vault["chain"] == "Nano"
        assert xno_vault["vault_address"] == DEFAULT_XNO_VAULT_ADDRESS

        cc_vault = node.pacvo_bridge_get_vault("wCCPVO")
        assert cc_vault["chain"] == "Chocohub"
        assert cc_vault["vault_address"] == DEFAULT_CC_VAULT_ADDRESS

        # 3. Deposit via RPC
        dep_cc = node.pacvo_bridge_deposit(
            symbol="wCCPVO",
            external_tx_hash="0xrpc_cc_deposit_001",
            external_from="choco_rpc_sender",
            pacvo_recipient="0xrpc_carol",
            raw_amount=20_000_000_000,  # 200 CC
        )
        assert dep_cc["status"] == "CONFIRMED"

        # 4. Balance Query via RPC
        bal_cc = node.pacvo_bridge_get_balance("wCCPVO", "0xrpc_carol")
        assert int(bal_cc["balance_wad"]) > 0

        # 5. Burn via RPC
        burn_cc = node.pacvo_bridge_burn(
            symbol="wCCPVO",
            pacvo_sender="0xrpc_carol",
            external_destination="choco_rpc_withdraw",
            amount_wad=50 * WAD,  # 50 wCCPVO
        )
        assert burn_cc["status"] == "COMMITTED"
        assert int(burn_cc["raw_unlock_amount"]) > 0

        print("  [PASS] Bridge RPC query and operation endpoints verified")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_bridge_fuzzing_dust_and_rounding_conservation():
    print("Testing Bridge Fuzzing: Dust Accumulation & Rounding Conservation (500 cycles)...")
    import random
    rng = random.Random(42)

    bridge = BridgeManager()
    user = "0xfuzz_user"

    total_deposited_raw_cc = 0
    total_extracted_raw_cc = 0

    for i in range(500):
        # 1. Random deposit: ranging from 1 base unit (micro-dust) to 1,000,000,000 units (10 CC)
        deposit_raw = rng.randint(1, 1_000_000_000)
        rec = bridge.process_cc_deposit(
            external_tx_hash=f"0xfuzz_cc_tx_{i}",
            external_from="choco_fuzzer",
            pacvo_recipient=user,
            raw_amount=deposit_raw,
            block_height=i,
        )
        total_deposited_raw_cc += deposit_raw

        # Invariant 1: Minted WAD must strictly not exceed vault balance
        assert bridge.verify_bridge_invariants() is True

        # 2. Random burn: burn a fraction or all of current WAD balance
        current_bal_wad = bridge.get_balance("wCCPVO", user)
        if current_bal_wad > 0 and rng.random() > 0.3:
            # Burn between 1 WAD sub-unit and full balance
            burn_wad = rng.randint(1, current_bal_wad)
            burn_rec = bridge.process_cc_burn(
                pacvo_sender=user,
                external_choco_destination="choco_fuzzer_dest",
                amount_wad=burn_wad,
                block_height=i,
            )
            total_extracted_raw_cc += burn_rec.raw_unlock_amount

            # Invariant 2: Total extracted raw CC can NEVER exceed total deposited raw CC
            assert total_extracted_raw_cc <= total_deposited_raw_cc, (
                f"Dust exploit detected: extracted {total_extracted_raw_cc} > deposited {total_deposited_raw_cc}"
            )
            # Invariant 3: Protocol invariant holds after burn
            assert bridge.verify_bridge_invariants() is True

    # Final verification after all 500 cycles
    assert total_extracted_raw_cc <= total_deposited_raw_cc
    assert bridge.verify_bridge_invariants() is True
    print("  [PASS] 500-cycle dust and rounding fuzzing verified zero extraction exploit")


def main():
    print("=================================================================")
    print("STARTING PACVO NATIVE CROSS-CHAIN BRIDGE TEST SUITE")
    print("=================================================================")
    test_bitcoin_bridge_unit_conversions_and_lifecycle()
    test_nano_bridge_unit_conversions_and_lifecycle()
    test_chocohub_bridge_unit_conversions_and_lifecycle()
    test_bridge_fuzzing_dust_and_rounding_conservation()
    test_bridge_economy_integration_and_serialization()
    test_bridge_rpc_methods()
    print("=================================================================")
    print("ALL PACVO NATIVE BRIDGE TESTS PASSED WITH 100% SUCCESS!")
    print("=================================================================")


if __name__ == "__main__":
    main()
