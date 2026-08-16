"""Unit and Integration Tests for L3 Economy, 4 POL Polygon Reserve, and Treasury."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.l3.asset import Asset, AssetType
from pacvo.l3.economy import Economy
from pacvo.l3.errors import InsufficientReserveError, InvariantViolationError
from pacvo.l3.fixed import WAD, to_wad
from pacvo.l3.reserve import (
    DEFAULT_GENESIS_RESERVE_POL,
    DEFAULT_POLYGON_CHAIN_ID,
    DEFAULT_POLYGON_RESERVE_WALLET,
    ExternalReserveAdapter,
    PVOFiReserve,
    ReserveAttestation,
    ReserveTransaction,
)


def test_genesis_reserve_and_solvency():
    print("Testing Genesis 4 POL Polygon Reserve & Solvency Invariant...")
    eco = Economy(genesis_reserve_pol=4 * WAD)

    # Verify Initial State & Polygon Wallet Metadata
    assert eco.reserve.polygon_chain_id == 137
    assert eco.reserve.reserve_wallet_address == "0xe9D970937ba528245BAeD156aFe036e0Fa565218"
    assert eco.reserve.reserve_symbol == "POL"
    assert eco.reserve.genesis_reserve_target == 4 * 10**18
    assert eco.reserve.accounting_balance == 4 * 10**18
    assert eco.reserve.verified_onchain_balance == 4 * 10**18
    assert eco.reserve.total_reserve == 4 * 10**18
    assert eco.reserve.available_reserve == 4 * 10**18
    assert eco.reserve.locked_reserve == 0
    assert eco.reserve.issued_liabilities == 0
    assert eco.reserve.is_verified is True
    assert eco.reserve.calculate_backing_ratio() >= 100 * WAD

    # Allocate Backing for Liabilities (2 POL)
    eco.reserve.allocate_backing(2 * WAD)
    assert eco.reserve.locked_reserve == 2 * WAD
    assert eco.reserve.available_reserve == 2 * WAD
    assert eco.reserve.issued_liabilities == 2 * WAD
    assert eco.reserve.calculate_backing_ratio() == 2 * 10**18  # 2.0 WAD (200% backing)

    # Exceeding available reserve must raise InsufficientReserveError
    try:
        eco.reserve.allocate_backing(3 * WAD)
        assert False, "Should have raised InsufficientReserveError"
    except InsufficientReserveError:
        pass

    # Release Backing (1 POL)
    eco.reserve.release_backing(1 * WAD)
    assert eco.reserve.locked_reserve == 1 * WAD
    assert eco.reserve.available_reserve == 3 * WAD
    assert eco.reserve.issued_liabilities == 1 * WAD

    # Invariant Verification
    assert eco.reserve.verify_invariant()
    print("  [PASS] 4 POL Polygon reserve allocation, limits, and solvency verified")


def test_proof_of_reserve_attestations_and_txs():
    print("Testing Proof of Reserve Attestations & External On-Chain Tracking...")
    reserve = PVOFiReserve(
        polygon_chain_id=137,
        reserve_wallet_address="0xe9D970937ba528245BAeD156aFe036e0Fa565218",
        genesis_reserve_target=4 * WAD,
        accounting_balance=4 * WAD,
        verified_onchain_balance=4 * WAD,
    )
    assert len(reserve.attestations) == 1
    assert reserve.attestations[0].source == "genesis_config"

    # 1. Record new verified on-chain attestation (e.g. balance grew to 6 POL)
    att = reserve.record_attestation(
        verified_balance=6 * WAD,
        block_number=58900123,
        block_hash="0xabcdef1234567890",
        timestamp=1700000000,
        source="polygon_rpc_oracle",
    )
    assert reserve.verified_onchain_balance == 6 * WAD
    assert len(reserve.attestations) == 2
    assert att.block_number == 58900123

    # 2. Record external deposit transaction (+2 POL)
    reserve.record_deposit(
        amount=2 * WAD,
        tx_hash="0x112233445566778899aabbccddeeff",
        block_number=58900124,
        timestamp=1700000010,
        from_address="0xliquidity_provider",
    )
    assert reserve.accounting_balance == 6 * WAD
    assert len(reserve.transactions) == 1
    assert reserve.transactions[0].tx_type == "DEPOSIT"
    assert reserve.transactions[0].amount == 2 * WAD

    # 3. Record external withdrawal transaction (-1 POL)
    reserve.record_withdrawal(
        amount=1 * WAD,
        tx_hash="0x998877665544332211aabbccddeeff",
        block_number=58900200,
        timestamp=1700000100,
        to_address="0xcustodian_vault",
    )
    assert reserve.accounting_balance == 5 * WAD
    assert len(reserve.transactions) == 2
    assert reserve.transactions[1].tx_type == "WITHDRAWAL"

    # 4. Multi-asset external adapter (future wPVO-BTC, wPVO-XNO)
    btc_adapter = ExternalReserveAdapter(
        asset_symbol="wPVO-BTC",
        chain_name="Bitcoin",
        chain_id=0,
        wallet_address="bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
        target_reserve=1 * WAD,
        verified_onchain_balance=1 * WAD,
        accounting_balance=1 * WAD,
    )
    reserve.register_external_adapter(btc_adapter)
    retrieved = reserve.get_external_adapter("wPVO-BTC")
    assert retrieved is not None
    assert retrieved.chain_name == "Bitcoin"
    assert retrieved.wallet_address == "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"

    # Verify serialization roundtrip
    d = reserve.to_dict()
    assert d["polygon_chain_id"] == 137
    assert d["reserve_wallet_address"] == "0xe9D970937ba528245BAeD156aFe036e0Fa565218"
    assert "wPVO-BTC" in d["adapters"]

    res_from = PVOFiReserve.from_dict(d)
    assert res_from.polygon_chain_id == 137
    assert res_from.reserve_wallet_address == "0xe9D970937ba528245BAeD156aFe036e0Fa565218"
    assert res_from.accounting_balance == 5 * WAD
    assert "wPVO-BTC" in res_from.adapters

    print("  [PASS] Proof of reserve attestations, transactions, and multi-asset adapters verified")


def test_treasury_accounting_and_dividends():
    print("Testing Treasury Accounting & Dividend Declarations...")
    eco = Economy(genesis_reserve_pol=4 * WAD)

    # Deposit protocol funds to Treasury
    eco.treasury.deposit("POL", 100 * WAD)
    assert eco.treasury.get_balance("POL") == 100 * WAD

    # Register Equity
    eq = eco.register_equity(
        symbol="PVOA",
        name="Pacvo Alpha Shares",
        token_address="0x1111111111111111111111111111111111111111",
        issuer="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        total_supply=1_000_000 * WAD,
    )

    # Declare Dividend sponsored by Treasury
    d_index = eco.declare_dividend("PVOA", 10 * WAD)
    assert d_index == (10 * WAD * WAD) // (1_000_000 * WAD)
    assert eco.treasury.get_balance("POL") == 90 * WAD

    # User Dividend Claim
    user_bal = 100_000 * WAD  # 10% of total shares
    claimable = eq.dividend_pool.calculate_claimable("0xalice", user_bal)
    assert claimable == 1 * WAD  # 10% of 10 POL = 1 POL

    claimed = eq.claim_dividend("0xalice", user_bal)
    assert claimed == 1 * WAD

    # Double claim prevention
    assert eq.claim_dividend("0xalice", user_bal) == 0

    print("  [PASS] Treasury accounting and scalable dividend claims verified")


def test_epoch_transitions():
    print("Testing Epoch Transitions & Economic Clock...")
    eco = Economy(genesis_reserve_pol=4 * WAD)
    assert eco.epoch == 0

    eco.advance_to_height(50)
    assert eco.epoch == 0

    eco.advance_to_height(105)
    assert eco.epoch == 1
    assert eco.current_height == 105

    eco.advance_to_height(350)
    assert eco.epoch == 3

    print("  [PASS] Epoch transitions verified")


def main():
    test_genesis_reserve_and_solvency()
    test_proof_of_reserve_attestations_and_txs()
    test_treasury_accounting_and_dividends()
    test_epoch_transitions()
    print("ALL L3 ECONOMY TESTS PASSED!")


if __name__ == "__main__":
    main()
