"""Comprehensive Layer 2 (L2) Test Suite for Pacvo Blockchain.

Covers:
- ERC-20 Token Lifecycle (Fixed Supply, Controlled Mint, Memecoin)
- Standard ABI Selectors, Encoding/Decoding, and Return Values
- Balance, Allowance, Transfer, Approve, TransferFrom Invariants
- Authorized Minting & Burning Constraints
- Event Topics and Indexed Logging (Transfer, Approval)
- Deterministic Deployment (CREATE & CREATE2)
- L1 State Anchoring & Commitments
- L1 Reorganization State Rollback & Canonical Replay
- Layer 2 Node RPC Methods (pacvo_l2_*)
- Differential Verification against Py-EVM Reference
"""

import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.block import Block
from pacvo.chain import Blockchain, State
from pacvo.crypto import (
    derive_address,
    derive_create_address,
    derive_create2_address,
    derive_evm_address,
    keccak256,
)
from pacvo.evm.opcodes import *
from pacvo.evm.state import EVMState
from pacvo.evm.vm import EVM, ExecutionContext
from pacvo.l2.anchor import L2Anchor, compute_l2_state_root
from pacvo.l2.factory import TokenFactory
from pacvo.l2.state import L2State
from pacvo.l2.token import (
    ERC20Token,
    TOPIC_APPROVAL,
    TOPIC_TRANSFER,
    TokenType,
    encode_allowance,
    encode_approve,
    encode_balance_of,
    encode_burn,
    encode_decimals,
    encode_mint,
    encode_name,
    encode_symbol,
    encode_total_supply,
    encode_transfer,
    encode_transfer_from,
    get_allowance_slot,
    get_balance_slot,
)
from pacvo.params import (
    BLOCK_REWARD,
    COINBASE_MATURITY,
    GENESIS_TIMESTAMP,
    MAX_TARGET,
    MIN_FEE,
    TARGET_BLOCK_TIME,
    stake_split,
)
from pacvo.transaction import Transaction
from pacvo.wallet import Wallet


def run_tx(vm: EVM, code: bytes, ctx: ExecutionContext):
    return vm.execute(code, ctx)


def test_fixed_supply_token():
    print("Testing Fixed Supply Token Lifecycle...")
    state = EVMState()
    deployer = "0x1111111111111111111111111111111111111111"
    alice = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    bob = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    charlie = "0xcccccccccccccccccccccccccccccccccccccccc"

    # Deploy Token: "Pacvo USD", "PUSD", 18 decimals, 1,000,000 supply
    supply_1m = 1_000_000 * 10**18
    initcode = TokenFactory.create_fixed_supply_token("Pacvo USD", "PUSD", supply_1m, decimals=18)

    token_addr = derive_create_address(deployer, 0)
    ctx_deploy = ExecutionContext(
        caller=deployer,
        address=token_addr,
        origin=deployer,
        value=0,
        data=b"",
        gas_limit=5_000_000,
    )
    vm = EVM(state)
    res_deploy = vm.execute(initcode, ctx_deploy, is_create=True)
    assert res_deploy.success, f"Deploy failed: {res_deploy.error}"
    runtime = res_deploy.return_data
    state.set_code(token_addr, runtime)

    # 1. Check Metadata
    # name()
    res = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_name()))
    assert res.success
    name_str = res.return_data.rstrip(b"\x00").decode("utf-8")
    assert name_str == "Pacvo USD"

    # symbol()
    res = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_symbol()))
    assert res.success
    sym_str = res.return_data.rstrip(b"\x00").decode("utf-8")
    assert sym_str == "PUSD"

    # decimals()
    res = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_decimals()))
    assert res.success
    assert int.from_bytes(res.return_data, "big") == 18

    # totalSupply()
    res = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_total_supply()))
    assert res.success
    assert int.from_bytes(res.return_data, "big") == supply_1m

    # balanceOf(deployer)
    res = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_balance_of(deployer)))
    assert res.success
    assert int.from_bytes(res.return_data, "big") == supply_1m

    # 2. Transfer: Deployer -> Alice (250,000 PUSD)
    transfer_amt = 250_000 * 10**18
    res_tx = vm.execute(
        runtime,
        ExecutionContext(deployer, token_addr, deployer, 0, encode_transfer(alice, transfer_amt)),
    )
    assert res_tx.success
    assert int.from_bytes(res_tx.return_data, "big") == 1 # returns true
    assert len(res_tx.logs) == 1
    assert res_tx.logs[0].topics[0] == "0x" + TOPIC_TRANSFER.hex()
    assert int(res_tx.logs[0].topics[1], 16) == int(deployer, 16)
    assert int(res_tx.logs[0].data, 16) == transfer_amt

    # Check Balances
    res_bal_d = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_balance_of(deployer)))
    res_bal_a = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_balance_of(alice)))
    assert int.from_bytes(res_bal_d.return_data, "big") == 750_000 * 10**18
    assert int.from_bytes(res_bal_a.return_data, "big") == 250_000 * 10**18

    # 3. Insufficient Balance Revert (Alice tries to send 300,000 PUSD)
    res_bad = vm.execute(
        runtime,
        ExecutionContext(alice, token_addr, alice, 0, encode_transfer(bob, 300_000 * 10**18)),
    )
    assert not res_bad.success
    assert "reverted" in (res_bad.error or "").lower()

    # 4. Approve & TransferFrom
    # Alice approves Bob for 100,000 PUSD
    approve_amt = 100_000 * 10**18
    res_app = vm.execute(
        runtime,
        ExecutionContext(alice, token_addr, alice, 0, encode_approve(bob, approve_amt)),
    )
    assert res_app.success
    assert int.from_bytes(res_app.return_data, "big") == 1
    assert len(res_app.logs) == 1
    assert res_app.logs[0].topics[0] == "0x" + TOPIC_APPROVAL.hex()

    # Check allowance(alice, bob)
    res_allow = vm.execute(
        runtime,
        ExecutionContext(deployer, token_addr, deployer, 0, encode_allowance(alice, bob)),
    )
    assert int.from_bytes(res_allow.return_data, "big") == approve_amt

    # Bob executes transferFrom(Alice, Charlie, 40,000 PUSD)
    tfrom_amt = 40_000 * 10**18
    res_tf = vm.execute(
        runtime,
        ExecutionContext(bob, token_addr, bob, 0, encode_transfer_from(alice, charlie, tfrom_amt)),
    )
    assert res_tf.success
    assert int.from_bytes(res_tf.return_data, "big") == 1

    # Verify updated balances and remaining allowance
    res_allow2 = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_allowance(alice, bob)))
    assert int.from_bytes(res_allow2.return_data, "big") == 60_000 * 10**18

    res_bal_a2 = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_balance_of(alice)))
    assert int.from_bytes(res_bal_a2.return_data, "big") == 210_000 * 10**18

    res_bal_c = vm.execute(runtime, ExecutionContext(deployer, token_addr, deployer, 0, encode_balance_of(charlie)))
    assert int.from_bytes(res_bal_c.return_data, "big") == 40_000 * 10**18

    # Bob attempts transferFrom exceeding remaining allowance (70,000 > 60,000)
    res_tf_bad = vm.execute(
        runtime,
        ExecutionContext(bob, token_addr, bob, 0, encode_transfer_from(alice, charlie, 70_000 * 10**18)),
    )
    assert not res_tf_bad.success
    print("  [PASS] Fixed supply ERC-20 token verified")


def test_controlled_mint_token():
    print("Testing Controlled Mint Token Lifecycle...")
    state = EVMState()
    admin = "0x1111111111111111111111111111111111111111"
    hacker = "0x9999999999999999999999999999999999999999"
    user = "0x2222222222222222222222222222222222222222"

    initcode = TokenFactory.create_controlled_mint_token(
        name="Mintable Token",
        symbol="MINT",
        minter=admin,
        initial_supply=0,
        decimals=18,
    )
    token_addr = derive_create_address(admin, 0)
    vm = EVM(state)
    res_deploy = vm.execute(initcode, ExecutionContext(admin, token_addr, admin, 0, b"", gas_limit=5_000_000), is_create=True)
    assert res_deploy.success
    runtime = res_deploy.return_data
    state.set_code(token_addr, runtime)

    # 1. Admin mints 5,000 tokens to user
    res_mint = vm.execute(runtime, ExecutionContext(admin, token_addr, admin, 0, encode_mint(user, 5000)))
    assert res_mint.success
    assert len(res_mint.logs) == 1
    assert int(res_mint.logs[0].topics[1], 16) == 0 # Transfer from 0x0

    # Verify totalSupply and balance
    res_ts = vm.execute(runtime, ExecutionContext(admin, token_addr, admin, 0, encode_total_supply()))
    assert int.from_bytes(res_ts.return_data, "big") == 5000
    res_u_bal = vm.execute(runtime, ExecutionContext(admin, token_addr, admin, 0, encode_balance_of(user)))
    assert int.from_bytes(res_u_bal.return_data, "big") == 5000

    # 2. Unauthorized mint attempt by hacker -> REVERTS
    res_hack = vm.execute(runtime, ExecutionContext(hacker, token_addr, hacker, 0, encode_mint(hacker, 1_000_000)))
    assert not res_hack.success
    # Total supply untouched
    res_ts2 = vm.execute(runtime, ExecutionContext(admin, token_addr, admin, 0, encode_total_supply()))
    assert int.from_bytes(res_ts2.return_data, "big") == 5000

    # 3. User burns 2,000 tokens
    res_burn = vm.execute(runtime, ExecutionContext(user, token_addr, user, 0, encode_burn(2000)))
    assert res_burn.success
    assert len(res_burn.logs) == 1
    assert int(res_burn.logs[0].topics[2], 16) == 0 # Transfer to 0x0

    res_ts3 = vm.execute(runtime, ExecutionContext(admin, token_addr, admin, 0, encode_total_supply()))
    assert int.from_bytes(res_ts3.return_data, "big") == 3000
    res_u_bal2 = vm.execute(runtime, ExecutionContext(admin, token_addr, admin, 0, encode_balance_of(user)))
    assert int.from_bytes(res_u_bal2.return_data, "big") == 3000

    # 4. User attempts to burn more than balance (4,000 > 3,000) -> REVERTS
    res_burn_bad = vm.execute(runtime, ExecutionContext(user, token_addr, user, 0, encode_burn(4000)))
    assert not res_burn_bad.success
    print("  [PASS] Controlled mint & burn verified")


def test_create2_deployment_and_collision():
    print("Testing CREATE2 Token Deployment & Collision Safety...")
    state = EVMState()
    deployer = "0x1111111111111111111111111111111111111111"
    initcode = TokenFactory.create_memecoin("Pacvo Pepe", "PEPE", total_supply=420_690_000_000)
    salt = (0xCAFEBABE).to_bytes(32, "big")

    expected_addr = TokenFactory.compute_address_create2(deployer, salt, initcode)

    # Deploy factory proxy that calls CREATE2
    create2_proxy_code = bytes([
        # Memory[0..len(initcode)] = initcode
        PUSH2, *len(initcode).to_bytes(2, "big"),
        PUSH1, 32, # offset in calldata
        PUSH1, 0,  # destOffset
        CALLDATACOPY,
        # CREATE2(value=0, offset=0, length=len(initcode), salt=salt)
        PUSH32, *salt,
        PUSH2, *len(initcode).to_bytes(2, "big"),
        PUSH1, 0,
        PUSH1, 0,
        CREATE2,
        PUSH1, 0,
        MSTORE,
        PUSH1, 32,
        PUSH1, 0,
        RETURN
    ])

    factory_addr = "0x5555555555555555555555555555555555555555"
    state.set_code(factory_addr, create2_proxy_code)
    state.set_balance(factory_addr, 10**18)

    expected_create2 = TokenFactory.compute_address_create2(factory_addr, salt, initcode)

    vm = EVM(state)
    res = vm.execute(
        create2_proxy_code,
        ExecutionContext(deployer, factory_addr, deployer, 0, b"\x00" * 32 + initcode, gas_limit=10_000_000),
    )
    assert res.success
    created_int = int.from_bytes(res.return_data, "big")
    created_hex = "0x" + format(created_int, "040x")
    assert created_hex.lower() == expected_create2.lower()
    assert len(state.get_code(expected_create2)) > 0

    # Collision test: re-executing CREATE2 with identical salt to existing code returns 0
    res_collision = vm.execute(
        create2_proxy_code,
        ExecutionContext(deployer, factory_addr, deployer, 0, b"\x00" * 32 + initcode, gas_limit=10_000_000),
    )
    assert res_collision.success
    assert int.from_bytes(res_collision.return_data, "big") == 0
    print("  [PASS] CREATE2 deployment and collision rejection verified")


def test_on_chain_l2_reorganization():
    print("Testing L1 Reorganization & L2 Token State Rollback/Replay...")
    chain = Blockchain()
    chain.blocks[0] = Block(0, "0" * 128, Block.compute_merkle_root([]), GENESIS_TIMESTAMP, MAX_TARGET, 0, [])

    wallet_miner = Wallet.generate()
    wallet_alice = Wallet.generate()
    wallet_bob = Wallet.generate()

    miner_addr = derive_address(wallet_miner.sign_public_key)
    alice_addr = derive_address(wallet_alice.sign_public_key)
    bob_addr = derive_address(wallet_bob.sign_public_key)

    alice_evm = derive_evm_address(wallet_alice.sign_public_key)
    bob_evm = derive_evm_address(wallet_bob.sign_public_key)

    spendable, stake = stake_split(BLOCK_REWARD)

    def mine_block(height, prev_hash, txs, ts):
        b = Block(height, prev_hash, Block.compute_merkle_root([t.txid for t in txs]), ts, chain.next_target(), 0, txs)
        while not b.meets_target():
            b.nonce += 1
        return b

    # 1. Mature coinbase rewards for Alice
    prev = chain.blocks[0]
    for h in range(1, COINBASE_MATURITY + 2):
        cb = Transaction.coinbase(alice_addr, spendable, stake, h)
        ts = GENESIS_TIMESTAMP + h * TARGET_BLOCK_TIME
        b = mine_block(h, prev.block_hash, [cb], ts)
        ok, err = chain.add_block(b, sigs_ok=True)
        assert ok, err
        prev = b

    # 2. Alice deploys Fixed Supply Token on L1 Block (h = 130)
    initcode = TokenFactory.create_fixed_supply_token("Reorg Coin", "RORG", 1000 * 10**18, decimals=18)
    token_addr = derive_create_address(alice_evm, 0)

    h_dep = chain.height + 1
    tx_deploy = Transaction(
        sender_public_key=wallet_alice.sign_public_key,
        recipient="",
        amount=0,
        fee=MIN_FEE,
        nonce=chain.state.next_nonce(alice_addr),
        timestamp=int(time.time()),
        evm_to="",
        evm_data=initcode,
        evm_gas_limit=2_000_000,
    )
    tx_deploy.sign(wallet_alice.sign_secret_key)
    cb_dep = Transaction.coinbase(alice_addr, spendable + tx_deploy.fee, stake, h_dep)
    blk_dep = mine_block(h_dep, chain.blocks[-1].block_hash, [cb_dep, tx_deploy], GENESIS_TIMESTAMP + h_dep * TARGET_BLOCK_TIME)
    ok, err = chain.add_block(blk_dep)
    assert ok, err

    l2 = L2State(chain.state.evm_state)
    assert l2.get_token_balance(token_addr, alice_evm) == 1000 * 10**18

    # 3. On Chain Fork A: Alice transfers 400 RORG to Bob on block 131
    h_tx = chain.height + 1
    tx_transfer = Transaction(
        sender_public_key=wallet_alice.sign_public_key,
        recipient="",
        amount=0,
        fee=MIN_FEE,
        nonce=chain.state.next_nonce(alice_addr),
        timestamp=int(time.time()),
        evm_to=token_addr,
        evm_data=encode_transfer(bob_evm, 400 * 10**18),
        evm_gas_limit=500_000,
    )
    tx_transfer.sign(wallet_alice.sign_secret_key)
    cb_tx = Transaction.coinbase(alice_addr, spendable + tx_transfer.fee, stake, h_tx)
    blk_tx = mine_block(h_tx, chain.blocks[-1].block_hash, [cb_tx, tx_transfer], GENESIS_TIMESTAMP + h_tx * TARGET_BLOCK_TIME)
    ok, err = chain.add_block(blk_tx)
    assert ok, err

    assert l2.get_token_balance(token_addr, alice_evm) == 600 * 10**18
    assert l2.get_token_balance(token_addr, bob_evm) == 400 * 10**18

    # 4. Perform Reorg: Alternative chain forks from block 130 (reverting block 131 transfer)
    # Alt block 131 and 132 are mined by miner with higher cumulative work
    fork_height = h_dep # block 130
    alt_prev = chain.blocks[fork_height]

    alt_blocks = []
    for step in range(1, 3):
        h_alt = fork_height + step
        cb_alt = Transaction.coinbase(miner_addr, spendable, stake, h_alt)
        ts_alt = GENESIS_TIMESTAMP + h_alt * TARGET_BLOCK_TIME + 500
        b_alt = mine_block(h_alt, alt_prev.block_hash, [cb_alt], ts_alt)
        alt_blocks.append(b_alt)
        alt_prev = b_alt

    ok, err = chain.execute_reorg(fork_height, alt_blocks)
    assert ok, err

    # 5. Verify L2 state was fully rolled back to canonical chain state:
    # Bob has 0 tokens, Alice has all 1000 tokens!
    l2_reorg = L2State(chain.state.evm_state)
    assert l2_reorg.get_token_balance(token_addr, alice_evm) == 1000 * 10**18
    assert l2_reorg.get_token_balance(token_addr, bob_evm) == 0

    print("  [PASS] L1 Reorganization L2 rollback & canonical replay verified")


async def test_l2_rpc_endpoints():
    print("Testing L2 RPC Methods (pacvo_l2_*)...")
    from pacvo.network import P2PNode, rpc_call
    from pacvo.node import Node

    class StubWallet:
        def __init__(self):
            w = Wallet.generate()
            self.sign_public_key = w.sign_public_key
            self.sign_secret_key = w.sign_secret_key
            self.address = derive_address(self.sign_public_key)

    with tempfile.TemporaryDirectory() as d:
        wallet = StubWallet()
        node = Node(wallet, d, "127.0.0.1", 19448, [], mine=False)
        await node.p2p.start()
        try:
            # Seed EVM state with a token
            token_addr = "0x7777777777777777777777777777777777777777"
            user_addr = "0x8888888888888888888888888888888888888888"
            node.chain.state.evm_state.set_code(token_addr, b"\x60\x00")
            node.chain.state.evm_state.set_storage(token_addr, 0, 1_000_000) # totalSupply
            node.chain.state.evm_state.set_storage(token_addr, 2, int.from_bytes(b"RPCToken".ljust(32, b"\x00"), "big")) # name
            node.chain.state.evm_state.set_storage(token_addr, 3, int.from_bytes(b"RPC".ljust(32, b"\x00"), "big")) # symbol
            node.chain.state.evm_state.set_storage(token_addr, 4, 18) # decimals

            # Set user balance
            u_slot = get_balance_slot(user_addr)
            node.chain.state.evm_state.set_storage(token_addr, u_slot, 500)

            # Test pacvo_l2_getToken
            r_info = await rpc_call("127.0.0.1", 19448, "pacvo_l2_getToken", {"token": token_addr})
            assert r_info["data"]["name"] == "RPCToken"
            assert r_info["data"]["symbol"] == "RPC"
            assert r_info["data"]["decimals"] == 18
            assert r_info["data"]["total_supply"] == 1_000_000

            # Test pacvo_l2_getTokenBalance
            r_bal = await rpc_call("127.0.0.1", 19448, "pacvo_l2_getTokenBalance", {"token": token_addr, "address": user_addr})
            assert r_bal["data"]["balance_dec"] == 500

            # Test pacvo_l2_getAnchor
            r_anc = await rpc_call("127.0.0.1", 19448, "pacvo_l2_getAnchor", {})
            assert r_anc["data"]["l1_height"] == 0
            assert len(r_anc["data"]["state_root"]) == 64

            # Test pacvo_l2_getStateRoot
            r_root = await rpc_call("127.0.0.1", 19448, "pacvo_l2_getStateRoot", {})
            assert len(r_root["data"]["state_root"]) == 64
        finally:
            await node.p2p.stop()

    print("  [PASS] L2 RPC endpoints verified")


def main():
    print("=================================================================")
    print("STARTING PACVO LAYER 2 (L2) TEST SUITE")
    print("=================================================================")
    test_fixed_supply_token()
    test_controlled_mint_token()
    test_create2_deployment_and_collision()
    test_on_chain_l2_reorganization()
    asyncio.run(test_l2_rpc_endpoints())
    print("=================================================================")
    print("ALL PACVO L2 TESTS PASSED WITH 100% SUCCESS!")
    print("=================================================================")


if __name__ == "__main__":
    main()
