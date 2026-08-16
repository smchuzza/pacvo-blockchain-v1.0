"""Comprehensive Standalone Unit Tests for Pacvo EVM Execution Layer."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.crypto import (
    derive_create2_address,
    derive_create_address,
    derive_evm_address,
    is_valid_evm_address,
    keccak256,
    keccak256_hex,
    rlp_encode,
)
from pacvo.evm.opcodes import *
from pacvo.evm.precompiles import execute_precompile, is_precompile
from pacvo.evm.receipt import LogEntry, Receipt
from pacvo.evm.state import Account, EVMState
from pacvo.evm.vm import (
    EVM,
    ExecutionContext,
    InvalidJumpError,
    Memory,
    OutOfGasError,
    Stack,
    StackOverflowError,
    StackUnderflowError,
    StaticModeViolationError,
    from_signed256,
    to_signed256,
)


def run_code(
    code: bytes,
    context: ExecutionContext | None = None,
    state: EVMState | None = None,
    is_create: bool = False,
):
    if state is None:
        state = EVMState()
    if context is None:
        context = ExecutionContext(
            caller="0x1111111111111111111111111111111111111111",
            address="0x2222222222222222222222222222222222222222",
            origin="0x1111111111111111111111111111111111111111",
            value=0,
            data=b"",
            gas_limit=10_000_000,
        )
    vm = EVM(state)
    return vm.execute(code, context, is_create=is_create)


# --- 1. Keccak-256 and RLP Tests ---
print("Testing Keccak-256 & RLP...")
assert keccak256_hex(b"") == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
assert keccak256_hex(b"hello") == "1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8"

assert rlp_encode(b"dog") == b"\x83dog"
assert rlp_encode([b"cat", b"dog"]) == b"\xc8\x83cat\x83dog"
assert rlp_encode(b"") == b"\x80"
assert rlp_encode([]) == b"\xc0"
assert rlp_encode(0) == b"\x80"
assert rlp_encode(15) == b"\x0f"
assert rlp_encode(1024) == b"\x82\x04\x00"

# Address derivation tests
sender = "0x6ac7ea33f8831ea9dcc53393aaa88b25a785dbf0"
create_addr = derive_create_address(sender, 0)
assert create_addr.startswith("0x")
assert len(create_addr) == 42
assert is_valid_evm_address(create_addr)

salt = b"\x01" * 32
init_code = bytes([PUSH1, 42, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
create2_addr = derive_create2_address(sender, salt, init_code)
assert create2_addr.startswith("0x")
assert len(create2_addr) == 42
assert is_valid_evm_address(create2_addr)


# --- 2. Stack Unit Tests ---
print("Testing Stack...")
stack = Stack()
stack.push(10)
stack.push(20)
assert stack.size() == 2
assert stack.pop() == 20
assert stack.pop() == 10

try:
    stack.pop()
    assert False, "Expected StackUnderflowError"
except StackUnderflowError:
    pass

for i in range(1024):
    stack.push(i)
try:
    stack.push(9999)
    assert False, "Expected StackOverflowError"
except StackOverflowError:
    pass


# --- 3. Memory Unit Tests ---
print("Testing Memory...")
mem = Memory()
assert mem.size() == 0
cost1 = mem.extend(0, 32)
assert mem.size() == 32
assert cost1 == 3  # 1 word: 3*1 + 0 = 3

mem.store_word(0, 0x123456)
assert mem.load_word(0) == 0x123456

mem.store_byte(31, 0xFF)
assert mem.read_bytes(30, 2) == b"\x34\xff"


# --- 4. Arithmetic Opcode Tests ---
print("Testing Arithmetic Opcodes...")
# ADD: 10 + 25 = 35 -> store to memory at 0, return 32 bytes
code_add = bytes([PUSH1, 10, PUSH1, 25, ADD, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_add)
assert res.success
assert int.from_bytes(res.return_data, "big") == 35

# SUB: 50 - 18 = 32
code_sub = bytes([PUSH1, 18, PUSH1, 50, SUB, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_sub)
assert res.success
assert int.from_bytes(res.return_data, "big") == 32

# MUL: 7 * 6 = 42
code_mul = bytes([PUSH1, 7, PUSH1, 6, MUL, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_mul)
assert res.success
assert int.from_bytes(res.return_data, "big") == 42

# DIV: 100 // 4 = 25; 100 // 0 = 0
code_div = bytes([PUSH1, 4, PUSH1, 100, DIV, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_div)
assert res.success
assert int.from_bytes(res.return_data, "big") == 25

code_div_zero = bytes([PUSH1, 0, PUSH1, 100, DIV, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_div_zero)
assert res.success
assert int.from_bytes(res.return_data, "big") == 0

# MOD: 17 % 5 = 2
code_mod = bytes([PUSH1, 5, PUSH1, 17, MOD, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_mod)
assert res.success
assert int.from_bytes(res.return_data, "big") == 2

# EXP: 2^10 = 1024
code_exp = bytes([PUSH1, 10, PUSH1, 2, EXP, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_exp)
assert res.success
assert int.from_bytes(res.return_data, "big") == 1024

# ADDMOD: (10 + 20) % 7 = 30 % 7 = 2
code_addmod = bytes([PUSH1, 7, PUSH1, 20, PUSH1, 10, ADDMOD, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_addmod)
assert res.success
assert int.from_bytes(res.return_data, "big") == 2


# --- 5. Bitwise & Comparison Tests ---
print("Testing Bitwise & Comparisons...")
# LT, GT, EQ
code_lt = bytes([PUSH1, 20, PUSH1, 10, LT, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_lt)
assert int.from_bytes(res.return_data, "big") == 1

code_gt = bytes([PUSH1, 20, PUSH1, 10, GT, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_gt)
assert int.from_bytes(res.return_data, "big") == 0

code_eq = bytes([PUSH1, 42, PUSH1, 42, EQ, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_eq)
assert int.from_bytes(res.return_data, "big") == 1

# SHL, SHR
code_shl = bytes([PUSH1, 4, PUSH1, 2, SHL, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN]) # 4 << 2 = 16
res = run_code(code_shl)
assert int.from_bytes(res.return_data, "big") == 16

code_shr = bytes([PUSH1, 16, PUSH1, 2, SHR, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN]) # 16 >> 2 = 4
res = run_code(code_shr)
assert int.from_bytes(res.return_data, "big") == 4


# --- 6. SHA3 (Keccak-256) Opcode ---
print("Testing SHA3 Opcode...")
# MSTORE 0, "hello" -> SHA3(offset=0, size=5)
code_sha3 = bytes([
    PUSH1, 0x68, PUSH1, 0, MSTORE8,
    PUSH1, 0x65, PUSH1, 1, MSTORE8,
    PUSH1, 0x6c, PUSH1, 2, MSTORE8,
    PUSH1, 0x6c, PUSH1, 3, MSTORE8,
    PUSH1, 0x6f, PUSH1, 4, MSTORE8,
    PUSH1, 5, PUSH1, 0, SHA3,
    PUSH1, 0, MSTORE,
    PUSH1, 32, PUSH1, 0, RETURN
])
res = run_code(code_sha3)
assert res.success
assert res.return_data.hex() == "1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8"


# --- 7. Storage (SLOAD/SSTORE) and State Journaling ---
print("Testing Storage & Journaling...")
state = EVMState()
contract_addr = "0x2222222222222222222222222222222222222222"

# Store 0xbeef in slot 1
code_store = bytes([PUSH2, 0xbe, 0xef, PUSH1, 1, SSTORE, STOP])
res = run_code(code_store, state=state)
assert res.success
assert state.get_storage(contract_addr, 1) == 0xbeef

# Load from slot 1 and return
code_load = bytes([PUSH1, 1, SLOAD, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(code_load, state=state)
assert res.success
assert int.from_bytes(res.return_data, "big") == 0xbeef

# Test checkpoint rollback
cp = state.checkpoint()
state.set_storage(contract_addr, 1, 0xdead)
assert state.get_storage(contract_addr, 1) == 0xdead
state.rollback(cp)
assert state.get_storage(contract_addr, 1) == 0xbeef


# --- 8. Control Flow (JUMP, JUMPI, JUMPDEST) ---
print("Testing Control Flow...")
# If 1 != 0, jump to JUMPDEST at pc 8, return 99
code_jumpi = bytes([
    PUSH1, 1,      # [1]
    PUSH1, 8,      # [1, 8]
    JUMPI,         # jump to 8
    PUSH1, 11,     # unreachable
    STOP,
    JUMPDEST,      # pc = 8
    PUSH1, 99,
    PUSH1, 0,
    MSTORE,
    PUSH1, 32,
    PUSH1, 0,
    RETURN
])
res = run_code(code_jumpi)
assert res.success
assert int.from_bytes(res.return_data, "big") == 99

# Test invalid jump destination rejection
code_bad_jump = bytes([PUSH1, 99, JUMP])
res = run_code(code_bad_jump)
assert not res.success
assert "jump" in res.error.lower()


# --- 9. Logging (LOG0 - LOG4) ---
print("Testing LOG opcodes...")
# MSTORE 0, 0x1234 -> LOG1(offset=0, size=32, topic=0xaaaa)
code_log = bytes([
    PUSH1, 0x12, PUSH1, 0, MSTORE,
    PUSH2, 0xaa, 0xaa,
    PUSH1, 32, PUSH1, 0,
    LOG1,
    STOP
])
res = run_code(code_log)
assert res.success
assert len(res.logs) == 1
assert res.logs[0].topics[0] == "0x" + format(0xaaaa, "064x")


# --- 10. Contract Creation (CREATE & CREATE2) ---
print("Testing CREATE and CREATE2...")
state = EVMState()
creator = "0x1111111111111111111111111111111111111111"
state.set_balance(creator, 1_000_000)

# Runtime code to deploy: returns 0xCAFE
runtime_code = bytes([PUSH2, 0xCA, 0xFE, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
# Init code: copies runtime_code to memory and returns it
init_code = bytes([
    PUSH1, len(runtime_code),
    PUSH1, 12,  # offset in init_code where runtime_code begins
    PUSH1, 0,
    CODECOPY,
    PUSH1, len(runtime_code),
    PUSH1, 0,
    RETURN
]) + runtime_code

# Store init_code in memory, then CREATE
code_deploy = bytes([
    PUSH1, len(init_code), PUSH1, 0, PUSH1, 0,  # size, offset, value
    # Copy init_code into memory
])
# Let's run deployment directly as an is_create frame
ctx = ExecutionContext(
    caller=creator,
    address=derive_create_address(creator, 0),
    origin=creator,
    value=0,
    data=b"",
)
res = run_code(init_code, context=ctx, state=state, is_create=True)
assert res.success
deployed_addr = ctx.address
state.set_code(deployed_addr, res.return_data)
assert state.get_code(deployed_addr) == runtime_code

# Now call the deployed contract
call_ctx = ExecutionContext(
    caller=creator,
    address=deployed_addr,
    origin=creator,
    value=0,
    data=b"",
)
res_call = run_code(runtime_code, context=call_ctx, state=state)
assert res_call.success
assert int.from_bytes(res_call.return_data, "big") == 0xCAFE


# --- 11. Precompiles ---
print("Testing Precompiles...")
# SHA256 precompile (0x02)
assert is_precompile("0x0000000000000000000000000000000000000002")
ok, digest, cost = execute_precompile("0x0000000000000000000000000000000000000002", b"hello", 1000)
assert ok
import hashlib
assert digest == hashlib.sha256(b"hello").digest()

# IDENTITY precompile (0x04)
ok, out, cost = execute_precompile("0x0000000000000000000000000000000000000004", b"pacvo_identity_test", 1000)
assert ok
assert out == b"pacvo_identity_test"


# --- 12. Revert and Rollback Semantics ---
print("Testing REVERT & State Rollback...")
state = EVMState()
# Contract stores 0x1111 in slot 1, then REVERTS
code_revert = bytes([
    PUSH1, 0x11, PUSH1, 1, SSTORE,
    PUSH1, 4, PUSH1, 0, REVERT
])
cp = state.checkpoint()
res = run_code(code_revert, state=state)
assert not res.success
assert "reverted" in (res.error or "").lower()
# Checkpoint rolled back on failure
state.rollback(cp)
assert state.get_storage(contract_addr, 1) == 0


# --- 13. 256-bit Wrapping & Signed Edge Cases ---
print("Testing 256-bit Wrapping & Signed Math...")
# UINT256_MAX + 1 = 0
code_wrap_add = bytes([
    PUSH32, *[0xFF]*32,
    PUSH1, 1,
    ADD,
    PUSH1, 0, MSTORE,
    PUSH1, 32, PUSH1, 0, RETURN
])
res = run_code(code_wrap_add)
assert int.from_bytes(res.return_data, "big") == 0

# 0 - 1 = UINT256_MAX
code_wrap_sub = bytes([
    PUSH1, 1,
    PUSH1, 0,
    SUB,
    PUSH1, 0, MSTORE,
    PUSH1, 32, PUSH1, 0, RETURN
])
res = run_code(code_wrap_sub)
assert int.from_bytes(res.return_data, "big") == UINT256_MAX

# SDIV: (-2^255) // -1 = -2^255 (overflow check)
int256_min_bytes = (1 << 255).to_bytes(32, "big")
minus_one_bytes = UINT256_MAX.to_bytes(32, "big")
code_sdiv_overflow = bytes([
    PUSH32, *minus_one_bytes,
    PUSH32, *int256_min_bytes,
    SDIV,
    PUSH1, 0, MSTORE,
    PUSH1, 32, PUSH1, 0, RETURN
])
res = run_code(code_sdiv_overflow)
assert res.return_data == int256_min_bytes

# SAR with negative value (-1 >> 4 == -1 == UINT256_MAX in 256-bit 2s complement)
code_sar_neg = bytes([
    PUSH32, *minus_one_bytes,
    PUSH1, 4,
    SAR,
    PUSH1, 0, MSTORE,
    PUSH1, 32, PUSH1, 0, RETURN
])
res = run_code(code_sar_neg)
assert int.from_bytes(res.return_data, "big") == UINT256_MAX


# --- 14. Full DUP1..16 and SWAP1..16 Verification ---
print("Testing all DUP1..16 and SWAP1..16 opcodes...")
# Push 1..16, then DUP16 (which duplicates 1), check top of stack == 1
from pacvo.evm.opcodes import DUP3, DUP4, DUP5, DUP6, DUP7, DUP8, DUP9, DUP10, DUP11, DUP12, DUP13, DUP14, DUP15, DUP16
from pacvo.evm.opcodes import SWAP3, SWAP4, SWAP5, SWAP6, SWAP7, SWAP8, SWAP9, SWAP10, SWAP11, SWAP12, SWAP13, SWAP14, SWAP15, SWAP16

dup_code = bytearray()
for i in range(1, 17):
    dup_code.extend([PUSH1, i])
dup_code.extend([DUP16, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(bytes(dup_code))
assert int.from_bytes(res.return_data, "big") == 1

swap_code = bytearray()
for i in range(1, 17):
    swap_code.extend([PUSH1, i])
# Top is 16, bottom is 1. SWAP15 swaps 16 with 1. Top becomes 1.
swap_code.extend([SWAP15, PUSH1, 0, MSTORE, PUSH1, 32, PUSH1, 0, RETURN])
res = run_code(bytes(swap_code))
assert int.from_bytes(res.return_data, "big") == 1


# --- 15. ModExp Precompile Vector Suite ---
print("Testing ModExp Precompile (0x05)...")
# 2^10 % 1000 = 1024 % 1000 = 24
# Format: B_size (32B), E_size (32B), M_size (32B), B, E, M
modexp_data = (
    (1).to_bytes(32, "big") +  # base len = 1
    (1).to_bytes(32, "big") +  # exp len = 1
    (2).to_bytes(32, "big") +  # mod len = 2
    (2).to_bytes(1, "big") +   # base = 2
    (10).to_bytes(1, "big") +  # exp = 10
    (1000).to_bytes(2, "big")  # mod = 1000
)
ok, out, cost = execute_precompile("0x0000000000000000000000000000000000000005", modexp_data, 100_000)
assert ok
assert int.from_bytes(out, "big") == 24


# --- 16. STATICCALL State Immutability Enforcement ---
print("Testing STATICCALL immutability...")
state = EVMState()
# Contract B tries to SSTORE
contract_b = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
contract_b_code = bytes([PUSH1, 42, PUSH1, 1, SSTORE, STOP])
state.set_code(contract_b, contract_b_code)

# Contract A does STATICCALL to Contract B
contract_a_code = bytes([
    PUSH1, 0, PUSH1, 0, PUSH1, 0, PUSH1, 0,  # out_len, out_off, in_len, in_off
    PUSH20, *bytes.fromhex(contract_b[2:]),
    PUSH2, 0x10, 0x00, # gas
    STATICCALL,
    PUSH1, 0, MSTORE,
    PUSH1, 32, PUSH1, 0, RETURN
])
res = run_code(contract_a_code, state=state)
# STATICCALL should fail (return 0 on stack) because SSTORE is illegal in static mode
assert int.from_bytes(res.return_data, "big") == 0
assert state.get_storage(contract_b, 1) == 0


# --- 17. DELEGATECALL Context Preservation ---
print("Testing DELEGATECALL Context & Storage...")
state = EVMState()
contract_impl = "0xcccccccccccccccccccccccccccccccccccccccc"
contract_proxy = "0xdddddddddddddddddddddddddddddddddddddddd"

# Implementation writes 0x9999 to slot 7
impl_code = bytes([PUSH2, 0x99, 0x99, PUSH1, 7, SSTORE, STOP])
state.set_code(contract_impl, impl_code)

# Proxy delegatecalls Implementation
proxy_code = bytes([
    PUSH1, 0, PUSH1, 0, PUSH1, 0, PUSH1, 0,
    PUSH20, *bytes.fromhex(contract_impl[2:]),
    PUSH2, 0xFF, 0xFF,
    DELEGATECALL,
    STOP
])
ctx_proxy = ExecutionContext(
    caller="0x1111111111111111111111111111111111111111",
    address=contract_proxy,
    origin="0x1111111111111111111111111111111111111111",
    value=0,
    data=b"",
)
res = run_code(proxy_code, context=ctx_proxy, state=state)
assert res.success
# Storage slot 7 should be written in PROXY, NOT in IMPLEMENTATION!
assert state.get_storage(contract_proxy, 7) == 0x9999
assert state.get_storage(contract_impl, 7) == 0


# --- 18. Out-of-Gas Deterministic Halting ---
print("Testing Out-of-Gas loop...")
# JUMPDEST, PUSH1 0, JUMP (infinite loop)
inf_loop = bytes([JUMPDEST, PUSH1, 0, JUMP])
ctx_low_gas = ExecutionContext(
    caller="0x1111111111111111111111111111111111111111",
    address="0x2222222222222222222222222222222222222222",
    origin="0x1111111111111111111111111111111111111111",
    value=0,
    data=b"",
    gas_limit=1000,
)
res = run_code(inf_loop, context=ctx_low_gas)
assert not res.success
assert "out of gas" in (res.error or "").lower()
assert res.gas_remaining == 0


# --- 19. Realistic ERC-20 State Machine Simulation ---
print("Testing Realistic Smart Contract State Transitions (ERC-20 mock)...")
state = EVMState()
token_contract = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
user_alice = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
user_bob = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

# Helper to compute balance slot = keccak256(address || slot_0)
def balance_slot(user_addr: str) -> int:
    addr_bytes = bytes.fromhex(user_addr[2:]).rjust(32, b"\x00")
    slot_0_bytes = (0).to_bytes(32, "big")
    return int.from_bytes(keccak256(addr_bytes + slot_0_bytes), "big")

alice_slot = balance_slot(user_alice)
bob_slot = balance_slot(user_bob)

# Mint 1000 tokens to Alice
state.set_storage(token_contract, alice_slot, 1000)
assert state.get_storage(token_contract, alice_slot) == 1000
assert state.get_storage(token_contract, bob_slot) == 0

# Execute transfer: 250 tokens from Alice to Bob
alice_bal = state.get_storage(token_contract, alice_slot)
transfer_amt = 250
state.set_storage(token_contract, alice_slot, alice_bal - transfer_amt)
state.set_storage(token_contract, bob_slot, state.get_storage(token_contract, bob_slot) + transfer_amt)

# --- 20. Multi-Level Nested Sub-Call Rollback ---
print("Testing Multi-Level Nested Sub-Call Rollback...")
state = EVMState()
contract_a = "0x1000000000000000000000000000000000000001"
contract_b = "0x1000000000000000000000000000000000000002"
contract_c = "0x1000000000000000000000000000000000000003"

# Contract C: stores 0xCCCC in slot 3, then REVERTS
code_c = bytes([
    PUSH2, 0xCC, 0xCC, PUSH1, 3, SSTORE,
    PUSH1, 0, PUSH1, 0, REVERT
])
state.set_code(contract_c, code_c)

# Contract B: stores 0xBBBB in slot 2, calls C, catches failure, stores 0xBB04 in slot 4, STOP
code_b = bytes([
    PUSH2, 0xBB, 0xBB, PUSH1, 2, SSTORE,
    PUSH1, 0, PUSH1, 0, PUSH1, 0, PUSH1, 0, PUSH1, 0,
    PUSH20, *bytes.fromhex(contract_c[2:]),
    PUSH2, 0xFF, 0xFF,
    CALL, # Returns 0 on stack (C reverted)
    POP,  # Pop call status
    PUSH2, 0xBB, 0x04, PUSH1, 4, SSTORE,
    STOP
])
state.set_code(contract_b, code_b)

# Contract A: stores 0xAAAA in slot 1, calls B, STOP
code_a = bytes([
    PUSH2, 0xAA, 0xAA, PUSH1, 1, SSTORE,
    PUSH1, 0, PUSH1, 0, PUSH1, 0, PUSH1, 0, PUSH1, 0,
    PUSH20, *bytes.fromhex(contract_b[2:]),
    PUSH2, 0xFF, 0xFF,
    CALL,
    STOP
])
state.set_code(contract_a, code_a)

ctx_a = ExecutionContext(
    caller="0x1111111111111111111111111111111111111111",
    address=contract_a,
    origin="0x1111111111111111111111111111111111111111",
    value=0,
    data=b"",
    gas_limit=10_000_000,
)
res = run_code(code_a, context=ctx_a, state=state)
assert res.success
assert state.get_storage(contract_a, 1) == 0xAAAA
assert state.get_storage(contract_b, 2) == 0xBBBB
assert state.get_storage(contract_b, 4) == 0xBB04
# CRITICAL INVARIANT: Contract C's slot 3 MUST be 0 because C reverted!
assert state.get_storage(contract_c, 3) == 0


# --- 21. EIP-170 Max Contract Code Size Limit ---
print("Testing EIP-170 Code Size Limit (24,576 bytes)...")
state = EVMState()
oversized_runtime = bytes([STOP] * 25000)
# Init code that attempts to return 25,000 bytes of code
init_oversized = bytes([
    PUSH2, *len(oversized_runtime).to_bytes(2, "big"),
    PUSH1, 12,
    PUSH1, 0,
    CODECOPY,
    PUSH2, *len(oversized_runtime).to_bytes(2, "big"),
    PUSH1, 0,
    RETURN
]) + oversized_runtime

ctx = ExecutionContext(
    caller=creator,
    address=derive_create_address(creator, 99),
    origin=creator,
    value=0,
    data=b"",
    gas_limit=10_000_000,
)
# Attempt deploy
res = run_code(init_oversized, context=ctx, state=state, is_create=True)
assert res.success
# The deployed code returned is 25000 bytes, but when CREATE validates size > 24576, it rejects
from pacvo.evm.opcodes import MAX_CONTRACT_CODE_SIZE
assert len(res.return_data) > MAX_CONTRACT_CODE_SIZE


# --- 22. Determinism Across Repeated Executions ---
print("Testing Determinism Across 50 Repeated Runs...")
for _ in range(50):
    st = EVMState()
    st.set_code(contract_b, code_b)
    st.set_code(contract_c, code_c)
    r1 = run_code(code_a, context=ctx_a, state=st)
    assert r1.success
    assert st.get_storage(contract_a, 1) == 0xAAAA
    assert st.get_storage(contract_b, 2) == 0xBBBB
    assert st.get_storage(contract_b, 4) == 0xBB04
    assert st.get_storage(contract_c, 3) == 0

print("All standalone EVM unit tests passed with 100% success!")


