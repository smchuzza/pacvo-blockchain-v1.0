"""EVM Stack, Memory, Context, and Core Virtual Machine Engine."""

from dataclasses import dataclass, field
import typing

from pacvo.crypto import keccak256
from pacvo.evm.opcodes import *
from pacvo.evm.precompiles import execute_precompile, is_precompile
from pacvo.evm.receipt import LogEntry
from pacvo.evm.state import EVMState


class VMError(Exception):
    pass


class OutOfGasError(VMError):
    pass


class StackUnderflowError(VMError):
    pass


class StackOverflowError(VMError):
    pass


class InvalidOpcodeError(VMError):
    pass


class InvalidJumpError(VMError):
    pass


class StaticModeViolationError(VMError):
    pass


class RevertError(VMError):
    def __init__(self, return_data: bytes) -> None:
        super().__init__("execution reverted")
        self.return_data = return_data


def to_signed256(val: int) -> int:
    val = val % UINT256_MOD
    if val > INT256_MAX:
        return val - UINT256_MOD
    return val


def from_signed256(val: int) -> int:
    return val % UINT256_MOD


@dataclass
class ExecutionContext:
    caller: str
    address: str
    origin: str
    value: int
    data: bytes
    gas_price: int = 0
    gas_limit: int = 30_000_000
    block_number: int = 0
    block_timestamp: int = 0
    block_coinbase: str = "0x0000000000000000000000000000000000000000"
    block_difficulty: int = 0
    block_gas_limit: int = 30_000_000
    block_base_fee: int = 0
    chain_id: int = 9333
    is_static: bool = False
    depth: int = 0


@dataclass
class ExecutionResult:
    success: bool
    gas_used: int
    gas_remaining: int
    return_data: bytes
    error: str | None = None
    created_address: str | None = None
    logs: list[LogEntry] = field(default_factory=list)


class Memory:
    def __init__(self) -> None:
        self._bytes = bytearray()

    def size(self) -> int:
        return len(self._bytes)

    def _calc_expansion_cost(self, offset: int, size: int) -> tuple[int, int]:
        if size == 0:
            return 0, len(self._bytes)
        new_size = offset + size
        # Round up to 32-byte word boundary
        words = (new_size + 31) // 32
        new_bytes_len = words * 32
        if new_bytes_len <= len(self._bytes):
            return 0, len(self._bytes)
        if new_bytes_len > MAX_MEMORY_BYTES:
            raise OutOfGasError("Memory expansion exceeded safety limit")
        old_words = len(self._bytes) // 32
        new_words = words
        old_cost = (old_words * 3) + (old_words * old_words // 512)
        new_cost = (new_words * 3) + (new_words * new_words // 512)
        return new_cost - old_cost, new_bytes_len

    def extend(self, offset: int, size: int) -> int:
        cost, new_len = self._calc_expansion_cost(offset, size)
        if new_len > len(self._bytes):
            self._bytes.extend(b"\x00" * (new_len - len(self._bytes)))
        return cost

    def load_word(self, offset: int) -> int:
        self.extend(offset, 32)
        return int.from_bytes(self._bytes[offset : offset + 32], "big")

    def store_word(self, offset: int, value: int) -> None:
        self.extend(offset, 32)
        val_bytes = (value % UINT256_MOD).to_bytes(32, "big")
        self._bytes[offset : offset + 32] = val_bytes

    def store_byte(self, offset: int, value: int) -> None:
        self.extend(offset, 1)
        self._bytes[offset] = value & 0xFF

    def read_bytes(self, offset: int, size: int) -> bytes:
        if size == 0:
            return b""
        self.extend(offset, size)
        return bytes(self._bytes[offset : offset + size])

    def write_bytes(self, offset: int, data: bytes) -> None:
        if not data:
            return
        self.extend(offset, len(data))
        self._bytes[offset : offset + len(data)] = data


class Stack:
    def __init__(self) -> None:
        self._items: list[int] = []

    def push(self, value: int) -> None:
        if len(self._items) >= MAX_STACK_DEPTH:
            raise StackOverflowError(f"Stack overflow (max depth {MAX_STACK_DEPTH})")
        self._items.append(value % UINT256_MOD)

    def pop(self) -> int:
        if not self._items:
            raise StackUnderflowError("Stack underflow")
        return self._items.pop()

    def peek(self, depth: int = 0) -> int:
        if len(self._items) <= depth:
            raise StackUnderflowError("Stack underflow on peek")
        return self._items[-1 - depth]

    def swap(self, depth: int) -> None:
        if len(self._items) <= depth:
            raise StackUnderflowError("Stack underflow on swap")
        self._items[-1], self._items[-1 - depth] = self._items[-1 - depth], self._items[-1]

    def dup(self, depth: int) -> None:
        if len(self._items) < depth:
            raise StackUnderflowError("Stack underflow on dup")
        val = self._items[-depth]
        self.push(val)

    def size(self) -> int:
        return len(self._items)


class EVM:
    def __init__(self, state: EVMState) -> None:
        self.state = state

    def compute_create_address(self, sender: str, nonce: int) -> str:
        from pacvo.crypto import derive_create_address
        return derive_create_address(sender, nonce)

    def compute_create2_address(self, sender: str, salt: bytes, init_code: bytes) -> str:
        from pacvo.crypto import derive_create2_address
        return derive_create2_address(sender, salt, init_code)

    def _find_jumpdests(self, code: bytes) -> set[int]:
        valid = set()
        pc = 0
        code_len = len(code)
        while pc < code_len:
            op = code[pc]
            if op == JUMPDEST:
                valid.add(pc)
            if PUSH1 <= op <= PUSH32:
                n = op - PUSH1 + 1
                pc += n
            pc += 1
        return valid

    def execute(
        self,
        code: bytes,
        context: ExecutionContext,
        is_create: bool = False,
    ) -> ExecutionResult:
        if context.depth > MAX_CALL_DEPTH:
            return ExecutionResult(
                success=False,
                gas_used=context.gas_limit,
                gas_remaining=0,
                return_data=b"",
                error=f"Max call depth {MAX_CALL_DEPTH} exceeded",
            )

        # Check precompiles for regular calls
        if not is_create and is_precompile(context.address):
            ok, out, gas_cost = execute_precompile(
                context.address, context.data, context.gas_limit
            )
            if not ok or gas_cost > context.gas_limit:
                return ExecutionResult(
                    success=False,
                    gas_used=context.gas_limit,
                    gas_remaining=0,
                    return_data=b"",
                    error="Precompile execution failed or out of gas",
                )
            return ExecutionResult(
                success=True,
                gas_used=gas_cost,
                gas_remaining=context.gas_limit - gas_cost,
                return_data=out,
            )

        stack = Stack()
        memory = Memory()
        gas_remaining = context.gas_limit
        pc = 0
        last_return_data = b""
        logs: list[LogEntry] = []
        code_len = len(code)
        jumpdests = self._find_jumpdests(code)

        def consume_gas(amount: int) -> None:
            nonlocal gas_remaining
            if gas_remaining < amount:
                raise OutOfGasError(f"Out of gas: required {amount}, available {gas_remaining}")
            gas_remaining -= amount

        # Execute instruction loop
        while pc < code_len:
            op = code[pc]
            pc += 1

            try:
                # --- STOP ---
                if op == STOP:
                    break

                # --- Arithmetic (0x01 - 0x0B) ---
                elif op == ADD:
                    consume_gas(GAS_VERY_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push((a + b) % UINT256_MOD)
                elif op == MUL:
                    consume_gas(GAS_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push((a * b) % UINT256_MOD)
                elif op == SUB:
                    consume_gas(GAS_VERY_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push((a - b) % UINT256_MOD)
                elif op == DIV:
                    consume_gas(GAS_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push(0 if b == 0 else (a // b) % UINT256_MOD)
                elif op == SDIV:
                    consume_gas(GAS_LOW)
                    a, b = to_signed256(stack.pop()), to_signed256(stack.pop())
                    if b == 0:
                        stack.push(0)
                    elif a == INT256_MIN and b == -1:
                        stack.push(from_signed256(INT256_MIN))
                    else:
                        res = abs(a) // abs(b)
                        if (a < 0) ^ (b < 0):
                            res = -res
                        stack.push(from_signed256(res))
                elif op == MOD:
                    consume_gas(GAS_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push(0 if b == 0 else a % b)
                elif op == SMOD:
                    consume_gas(GAS_LOW)
                    a, b = to_signed256(stack.pop()), to_signed256(stack.pop())
                    if b == 0:
                        stack.push(0)
                    else:
                        res = abs(a) % abs(b)
                        if a < 0:
                            res = -res
                        stack.push(from_signed256(res))
                elif op == ADDMOD:
                    consume_gas(GAS_MID)
                    a, b, N = stack.pop(), stack.pop(), stack.pop()
                    stack.push(0 if N == 0 else (a + b) % N)
                elif op == MULMOD:
                    consume_gas(GAS_MID)
                    a, b, N = stack.pop(), stack.pop(), stack.pop()
                    stack.push(0 if N == 0 else (a * b) % N)
                elif op == EXP:
                    base, exp_val = stack.pop(), stack.pop()
                    exp_bytes_len = (exp_val.bit_length() + 7) // 8 if exp_val > 0 else 0
                    consume_gas(GAS_EXP_BASE + GAS_EXP_BYTE * exp_bytes_len)
                    stack.push(pow(base, exp_val, UINT256_MOD))
                elif op == SIGNEXTEND:
                    consume_gas(GAS_LOW)
                    byte_idx, val = stack.pop(), stack.pop()
                    if byte_idx < 31:
                        bit_pos = int(byte_idx * 8 + 7)
                        mask = (1 << (bit_pos + 1)) - 1
                        sign_bit = (val >> bit_pos) & 1
                        if sign_bit:
                            res = val | (~mask & UINT256_MAX)
                        else:
                            res = val & mask
                        stack.push(res % UINT256_MOD)
                    else:
                        stack.push(val)

                # --- Comparisons & Bitwise (0x10 - 0x1D) ---
                elif op == LT:
                    consume_gas(GAS_VERY_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push(1 if a < b else 0)
                elif op == GT:
                    consume_gas(GAS_VERY_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push(1 if a > b else 0)
                elif op == SLT:
                    consume_gas(GAS_VERY_LOW)
                    a, b = to_signed256(stack.pop()), to_signed256(stack.pop())
                    stack.push(1 if a < b else 0)
                elif op == SGT:
                    consume_gas(GAS_VERY_LOW)
                    a, b = to_signed256(stack.pop()), to_signed256(stack.pop())
                    stack.push(1 if a > b else 0)
                elif op == EQ:
                    consume_gas(GAS_VERY_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push(1 if a == b else 0)
                elif op == ISZERO:
                    consume_gas(GAS_VERY_LOW)
                    a = stack.pop()
                    stack.push(1 if a == 0 else 0)
                elif op == AND:
                    consume_gas(GAS_VERY_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push(a & b)
                elif op == OR:
                    consume_gas(GAS_VERY_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push(a | b)
                elif op == XOR:
                    consume_gas(GAS_VERY_LOW)
                    a, b = stack.pop(), stack.pop()
                    stack.push(a ^ b)
                elif op == NOT:
                    consume_gas(GAS_VERY_LOW)
                    a = stack.pop()
                    stack.push((~a) & UINT256_MAX)
                elif op == BYTE:
                    consume_gas(GAS_VERY_LOW)
                    idx, val = stack.pop(), stack.pop()
                    if idx < 32:
                        res = (val >> (8 * (31 - idx))) & 0xFF
                        stack.push(res)
                    else:
                        stack.push(0)
                elif op == SHL:
                    consume_gas(GAS_VERY_LOW)
                    shift, val = stack.pop(), stack.pop()
                    if shift >= 256:
                        stack.push(0)
                    else:
                        stack.push((val << shift) % UINT256_MOD)
                elif op == SHR:
                    consume_gas(GAS_VERY_LOW)
                    shift, val = stack.pop(), stack.pop()
                    if shift >= 256:
                        stack.push(0)
                    else:
                        stack.push(val >> shift)
                elif op == SAR:
                    consume_gas(GAS_VERY_LOW)
                    shift, val = stack.pop(), to_signed256(stack.pop())
                    if shift >= 256:
                        stack.push(UINT256_MAX if val < 0 else 0)
                    else:
                        stack.push(from_signed256(val >> shift))

                # --- SHA3 / Keccak-256 (0x20) ---
                elif op == SHA3:
                    offset, length = stack.pop(), stack.pop()
                    mem_cost = memory.extend(offset, length)
                    words = (length + 31) // 32
                    consume_gas(GAS_SHA3_BASE + GAS_SHA3_WORD * words + mem_cost)
                    data = memory.read_bytes(offset, length)
                    digest = keccak256(data)
                    stack.push(int.from_bytes(digest, "big"))

                # --- Environmental & Context (0x30 - 0x3F) ---
                elif op == ADDRESS:
                    consume_gas(GAS_BASE)
                    stack.push(int(context.address, 16))
                elif op == BALANCE:
                    addr_int = stack.pop()
                    addr = format(addr_int & UINT256_MAX, "040x")[-40:]
                    consume_gas(GAS_WARM_ACCOUNT_ACCESS)
                    stack.push(self.state.get_balance("0x" + addr))
                elif op == ORIGIN:
                    consume_gas(GAS_BASE)
                    stack.push(int(context.origin, 16))
                elif op == CALLER:
                    consume_gas(GAS_BASE)
                    stack.push(int(context.caller, 16))
                elif op == CALLVALUE:
                    consume_gas(GAS_BASE)
                    stack.push(context.value)
                elif op == CALLDATALOAD:
                    consume_gas(GAS_VERY_LOW)
                    offset = stack.pop()
                    if offset >= len(context.data):
                        stack.push(0)
                    else:
                        chunk = context.data[offset : offset + 32].ljust(32, b"\x00")
                        stack.push(int.from_bytes(chunk, "big"))
                elif op == CALLDATASIZE:
                    consume_gas(GAS_BASE)
                    stack.push(len(context.data))
                elif op == CALLDATACOPY:
                    dest_offset, offset, length = stack.pop(), stack.pop(), stack.pop()
                    mem_cost = memory.extend(dest_offset, length)
                    words = (length + 31) // 32
                    consume_gas(GAS_VERY_LOW + GAS_COPY_WORD * words + mem_cost)
                    if offset < len(context.data):
                        copied = context.data[offset : offset + length].ljust(length, b"\x00")
                    else:
                        copied = b"\x00" * length
                    memory.write_bytes(dest_offset, copied)
                elif op == CODESIZE:
                    consume_gas(GAS_BASE)
                    stack.push(code_len)
                elif op == CODECOPY:
                    dest_offset, offset, length = stack.pop(), stack.pop(), stack.pop()
                    mem_cost = memory.extend(dest_offset, length)
                    words = (length + 31) // 32
                    consume_gas(GAS_VERY_LOW + GAS_COPY_WORD * words + mem_cost)
                    if offset < code_len:
                        copied = code[offset : offset + length].ljust(length, b"\x00")
                    else:
                        copied = b"\x00" * length
                    memory.write_bytes(dest_offset, copied)
                elif op == GASPRICE:
                    consume_gas(GAS_BASE)
                    stack.push(context.gas_price)
                elif op == EXTCODESIZE:
                    addr_int = stack.pop()
                    addr = "0x" + format(addr_int & UINT256_MAX, "040x")[-40:]
                    consume_gas(GAS_WARM_ACCOUNT_ACCESS)
                    stack.push(len(self.state.get_code(addr)))
                elif op == EXTCODECOPY:
                    addr_int, dest_offset, offset, length = (
                        stack.pop(), stack.pop(), stack.pop(), stack.pop()
                    )
                    addr = "0x" + format(addr_int & UINT256_MAX, "040x")[-40:]
                    target_code = self.state.get_code(addr)
                    mem_cost = memory.extend(dest_offset, length)
                    words = (length + 31) // 32
                    consume_gas(GAS_WARM_ACCOUNT_ACCESS + GAS_COPY_WORD * words + mem_cost)
                    if offset < len(target_code):
                        copied = target_code[offset : offset + length].ljust(length, b"\x00")
                    else:
                        copied = b"\x00" * length
                    memory.write_bytes(dest_offset, copied)
                elif op == RETURNDATASIZE:
                    consume_gas(GAS_BASE)
                    stack.push(len(last_return_data))
                elif op == RETURNDATACOPY:
                    dest_offset, offset, length = stack.pop(), stack.pop(), stack.pop()
                    if offset + length > len(last_return_data):
                        raise OutOfGasError("Return data out of bounds")
                    mem_cost = memory.extend(dest_offset, length)
                    words = (length + 31) // 32
                    consume_gas(GAS_VERY_LOW + GAS_COPY_WORD * words + mem_cost)
                    memory.write_bytes(dest_offset, last_return_data[offset : offset + length])
                elif op == EXTCODEHASH:
                    addr_int = stack.pop()
                    addr = "0x" + format(addr_int & UINT256_MAX, "040x")[-40:]
                    consume_gas(GAS_WARM_ACCOUNT_ACCESS)
                    if not self.state.account_exists(addr) and self.state.get_balance(addr) == 0:
                        stack.push(0)
                    else:
                        c = self.state.get_code(addr)
                        stack.push(int.from_bytes(keccak256(c), "big"))

                # --- Block Information (0x40 - 0x48) ---
                elif op == BLOCKHASH:
                    consume_gas(GAS_HIGH)
                    block_num = stack.pop()
                    # In EVM, only last 256 block hashes are accessible
                    stack.push(0)
                elif op == COINBASE:
                    consume_gas(GAS_BASE)
                    stack.push(int(context.block_coinbase, 16))
                elif op == TIMESTAMP:
                    consume_gas(GAS_BASE)
                    stack.push(context.block_timestamp)
                elif op == NUMBER:
                    consume_gas(GAS_BASE)
                    stack.push(context.block_number)
                elif op == DIFFICULTY:
                    consume_gas(GAS_BASE)
                    stack.push(context.block_difficulty)
                elif op == GASLIMIT:
                    consume_gas(GAS_BASE)
                    stack.push(context.block_gas_limit)
                elif op == CHAINID:
                    consume_gas(GAS_BASE)
                    stack.push(context.chain_id)
                elif op == SELFBALANCE:
                    consume_gas(GAS_LOW)
                    stack.push(self.state.get_balance(context.address))
                elif op == BASEFEE:
                    consume_gas(GAS_BASE)
                    stack.push(context.block_base_fee)

                # --- Stack, Memory, Storage (0x50 - 0x5B) ---
                elif op == POP:
                    consume_gas(GAS_BASE)
                    stack.pop()
                elif op == MLOAD:
                    offset = stack.pop()
                    mem_cost = memory.extend(offset, 32)
                    consume_gas(GAS_VERY_LOW + mem_cost)
                    stack.push(memory.load_word(offset))
                elif op == MSTORE:
                    offset, val = stack.pop(), stack.pop()
                    mem_cost = memory.extend(offset, 32)
                    consume_gas(GAS_VERY_LOW + mem_cost)
                    memory.store_word(offset, val)
                elif op == MSTORE8:
                    offset, val = stack.pop(), stack.pop()
                    mem_cost = memory.extend(offset, 1)
                    consume_gas(GAS_VERY_LOW + mem_cost)
                    memory.store_byte(offset, val)
                elif op == SLOAD:
                    slot = stack.pop()
                    consume_gas(GAS_WARM_STORAGE_READ)
                    stack.push(self.state.get_storage(context.address, slot))
                elif op == SSTORE:
                    if context.is_static:
                        raise StaticModeViolationError("SSTORE not permitted in static context")
                    slot, val = stack.pop(), stack.pop()
                    current = self.state.get_storage(context.address, slot)
                    if current == val:
                        gas_cost = GAS_WARM_STORAGE_READ
                    elif current == 0:
                        gas_cost = GAS_SSTORE_SET
                    else:
                        gas_cost = GAS_SSTORE_RESET
                    consume_gas(gas_cost)
                    self.state.set_storage(context.address, slot, val)
                elif op == JUMP:
                    consume_gas(GAS_MID)
                    dest = stack.pop()
                    if dest not in jumpdests:
                        raise InvalidJumpError(f"Invalid jump destination: {dest}")
                    pc = dest
                elif op == JUMPI:
                    consume_gas(GAS_HIGH)
                    dest, cond = stack.pop(), stack.pop()
                    if cond != 0:
                        if dest not in jumpdests:
                            raise InvalidJumpError(f"Invalid jump destination: {dest}")
                        pc = dest
                elif op == PC:
                    consume_gas(GAS_BASE)
                    stack.push(pc - 1)
                elif op == MSIZE:
                    consume_gas(GAS_BASE)
                    stack.push(memory.size())
                elif op == GAS:
                    consume_gas(GAS_BASE)
                    stack.push(gas_remaining)
                elif op == JUMPDEST:
                    consume_gas(GAS_ZERO)

                # --- PUSH Operations (0x5F, 0x60 - 0x7F) ---
                elif op == PUSH0:
                    consume_gas(GAS_BASE)
                    stack.push(0)
                elif PUSH1 <= op <= PUSH32:
                    n = op - PUSH1 + 1
                    consume_gas(GAS_VERY_LOW)
                    push_bytes = code[pc : pc + n].ljust(n, b"\x00")
                    pc += n
                    stack.push(int.from_bytes(push_bytes, "big"))

                # --- DUP Operations (0x80 - 0x8F) ---
                elif DUP1 <= op <= DUP16:
                    depth = op - DUP1 + 1
                    consume_gas(GAS_VERY_LOW)
                    stack.dup(depth)

                # --- SWAP Operations (0x90 - 0x9F) ---
                elif SWAP1 <= op <= SWAP16:
                    depth = op - SWAP1 + 1
                    consume_gas(GAS_VERY_LOW)
                    stack.swap(depth)

                # --- LOG Operations (0xA0 - 0xA4) ---
                elif LOG0 <= op <= LOG4:
                    if context.is_static:
                        raise StaticModeViolationError("LOG not permitted in static context")
                    num_topics = op - LOG0
                    offset, length = stack.pop(), stack.pop()
                    topics = []
                    for _ in range(num_topics):
                        t = stack.pop()
                        topics.append("0x" + format(t & UINT256_MAX, "064x"))
                    mem_cost = memory.extend(offset, length)
                    consume_gas(GAS_LOG_BASE + num_topics * GAS_LOG_TOPIC + length * GAS_LOG_DATA_BYTE + mem_cost)
                    log_data = memory.read_bytes(offset, length).hex()
                    logs.append(LogEntry(address=context.address, topics=topics, data=log_data, log_index=len(logs)))

                # --- Sub-calls and Contract Creation (0xF0 - 0xFF) ---
                elif op == CREATE:
                    if context.is_static:
                        raise StaticModeViolationError("CREATE not permitted in static context")
                    val, offset, length = stack.pop(), stack.pop(), stack.pop()
                    mem_cost = memory.extend(offset, length)
                    consume_gas(GAS_CREATE + mem_cost)
                    init_code = memory.read_bytes(offset, length)
                    
                    sender_bal = self.state.get_balance(context.address)
                    if sender_bal < val or len(init_code) > MAX_INITCODE_SIZE:
                        stack.push(0)
                        last_return_data = b""
                        continue

                    nonce = self.state.get_nonce(context.address)
                    new_addr = self.compute_create_address(context.address, nonce)
                    self.state.increment_nonce(context.address)

                    # EIP-684 collision check
                    if len(self.state.get_code(new_addr)) > 0 or self.state.get_nonce(new_addr) > 0:
                        stack.push(0)
                        last_return_data = b""
                        continue

                    # EIP-150 63/64 gas rule
                    create_gas = gas_remaining - (gas_remaining // 64)
                    consume_gas(create_gas)

                    cp = self.state.checkpoint()
                    self.state.sub_balance(context.address, val)
                    self.state.add_balance(new_addr, val)

                    child_context = ExecutionContext(
                        caller=context.address,
                        address=new_addr,
                        origin=context.origin,
                        value=val,
                        data=b"",
                        gas_price=context.gas_price,
                        gas_limit=create_gas,
                        block_number=context.block_number,
                        block_timestamp=context.block_timestamp,
                        block_coinbase=context.block_coinbase,
                        block_difficulty=context.block_difficulty,
                        block_gas_limit=context.block_gas_limit,
                        block_base_fee=context.block_base_fee,
                        chain_id=context.chain_id,
                        is_static=False,
                        depth=context.depth + 1,
                    )

                    child_res = self.execute(init_code, child_context, is_create=True)
                    gas_remaining += child_res.gas_remaining

                    if child_res.success:
                        deployed_code = child_res.return_data
                        deposit_cost = len(deployed_code) * GAS_CODE_DEPOSIT
                        if len(deployed_code) > MAX_CONTRACT_CODE_SIZE or gas_remaining < deposit_cost:
                            self.state.rollback(cp)
                            stack.push(0)
                            last_return_data = b""
                        else:
                            consume_gas(deposit_cost)
                            self.state.set_code(new_addr, deployed_code)
                            self.state.commit(cp)
                            logs.extend(child_res.logs)
                            stack.push(int(new_addr, 16))
                            last_return_data = b""
                    else:
                        self.state.rollback(cp)
                        stack.push(0)
                        last_return_data = child_res.return_data

                elif op == CREATE2:
                    if context.is_static:
                        raise StaticModeViolationError("CREATE2 not permitted in static context")
                    val, offset, length, salt_val = stack.pop(), stack.pop(), stack.pop(), stack.pop()
                    salt_bytes = salt_val.to_bytes(32, "big")
                    mem_cost = memory.extend(offset, length)
                    words = (length + 31) // 32
                    consume_gas(GAS_CREATE + GAS_SHA3_WORD * words + mem_cost)
                    init_code = memory.read_bytes(offset, length)

                    sender_bal = self.state.get_balance(context.address)
                    if sender_bal < val or len(init_code) > MAX_INITCODE_SIZE:
                        stack.push(0)
                        last_return_data = b""
                        continue

                    new_addr = self.compute_create2_address(context.address, salt_bytes, init_code)
                    self.state.increment_nonce(context.address)

                    # EIP-684 collision check
                    if len(self.state.get_code(new_addr)) > 0 or self.state.get_nonce(new_addr) > 0:
                        stack.push(0)
                        last_return_data = b""
                        continue

                    create_gas = gas_remaining - (gas_remaining // 64)
                    consume_gas(create_gas)

                    cp = self.state.checkpoint()
                    self.state.sub_balance(context.address, val)
                    self.state.add_balance(new_addr, val)

                    child_context = ExecutionContext(
                        caller=context.address,
                        address=new_addr,
                        origin=context.origin,
                        value=val,
                        data=b"",
                        gas_price=context.gas_price,
                        gas_limit=create_gas,
                        block_number=context.block_number,
                        block_timestamp=context.block_timestamp,
                        block_coinbase=context.block_coinbase,
                        block_difficulty=context.block_difficulty,
                        block_gas_limit=context.block_gas_limit,
                        block_base_fee=context.block_base_fee,
                        chain_id=context.chain_id,
                        is_static=False,
                        depth=context.depth + 1,
                    )

                    child_res = self.execute(init_code, child_context, is_create=True)
                    gas_remaining += child_res.gas_remaining

                    if child_res.success:
                        deployed_code = child_res.return_data
                        deposit_cost = len(deployed_code) * GAS_CODE_DEPOSIT
                        if len(deployed_code) > MAX_CONTRACT_CODE_SIZE or gas_remaining < deposit_cost:
                            self.state.rollback(cp)
                            stack.push(0)
                            last_return_data = b""
                        else:
                            consume_gas(deposit_cost)
                            self.state.set_code(new_addr, deployed_code)
                            self.state.commit(cp)
                            logs.extend(child_res.logs)
                            stack.push(int(new_addr, 16))
                            last_return_data = b""
                    else:
                        self.state.rollback(cp)
                        stack.push(0)
                        last_return_data = child_res.return_data

                elif op in (CALL, CALLCODE, DELEGATECALL, STATICCALL):
                    gas_req = stack.pop()
                    target_int = stack.pop()
                    target_addr = "0x" + format(target_int & UINT256_MAX, "040x")[-40:]

                    call_val = 0
                    if op in (CALL, CALLCODE):
                        call_val = stack.pop()
                        if call_val > 0 and context.is_static:
                            raise StaticModeViolationError("Value transfer not allowed in static call")

                    in_offset, in_len = stack.pop(), stack.pop()
                    out_offset, out_len = stack.pop(), stack.pop()

                    mem_cost = max(
                        memory.extend(in_offset, in_len),
                        memory.extend(out_offset, out_len)
                    )

                    base_call_gas = GAS_WARM_ACCOUNT_ACCESS + mem_cost
                    if call_val > 0:
                        base_call_gas += GAS_CALL_VALUE
                        if not self.state.account_exists(target_addr):
                            base_call_gas += GAS_CALL_NEW_ACCOUNT

                    consume_gas(base_call_gas)

                    # EIP-150 63/64 rule
                    available_gas = gas_remaining - (gas_remaining // 64)
                    sub_gas = min(gas_req, available_gas)
                    consume_gas(sub_gas)

                    call_data = memory.read_bytes(in_offset, in_len)
                    sender_bal = self.state.get_balance(context.address)

                    if call_val > sender_bal:
                        stack.push(0)
                        gas_remaining += sub_gas
                        last_return_data = b""
                        continue

                    # Setup child context
                    child_caller = context.address if op != DELEGATECALL else context.caller
                    child_target = context.address if op in (DELEGATECALL, CALLCODE) else target_addr
                    child_value = call_val if op != DELEGATECALL else context.value
                    child_static = context.is_static or (op == STATICCALL)

                    cp = self.state.checkpoint()
                    if call_val > 0 and op == CALL:
                        self.state.sub_balance(context.address, call_val)
                        self.state.add_balance(target_addr, call_val)

                    target_code = self.state.get_code(target_addr)

                    child_context = ExecutionContext(
                        caller=child_caller,
                        address=child_target,
                        origin=context.origin,
                        value=child_value,
                        data=call_data,
                        gas_price=context.gas_price,
                        gas_limit=sub_gas,
                        block_number=context.block_number,
                        block_timestamp=context.block_timestamp,
                        block_coinbase=context.block_coinbase,
                        block_difficulty=context.block_difficulty,
                        block_gas_limit=context.block_gas_limit,
                        block_base_fee=context.block_base_fee,
                        chain_id=context.chain_id,
                        is_static=child_static,
                        depth=context.depth + 1,
                    )

                    child_res = self.execute(target_code, child_context)
                    gas_remaining += child_res.gas_remaining
                    last_return_data = child_res.return_data

                    if out_len > 0 and last_return_data:
                        to_copy = last_return_data[:out_len]
                        memory.write_bytes(out_offset, to_copy)

                    if child_res.success:
                        self.state.commit(cp)
                        logs.extend(child_res.logs)
                        stack.push(1)
                    else:
                        self.state.rollback(cp)
                        stack.push(0)

                elif op == RETURN:
                    offset, length = stack.pop(), stack.pop()
                    mem_cost = memory.extend(offset, length)
                    consume_gas(mem_cost)
                    ret_bytes = memory.read_bytes(offset, length)
                    return ExecutionResult(
                        success=True,
                        gas_used=context.gas_limit - gas_remaining,
                        gas_remaining=gas_remaining,
                        return_data=ret_bytes,
                        logs=logs,
                    )

                elif op == REVERT:
                    offset, length = stack.pop(), stack.pop()
                    mem_cost = memory.extend(offset, length)
                    consume_gas(mem_cost)
                    revert_bytes = memory.read_bytes(offset, length)
                    return ExecutionResult(
                        success=False,
                        gas_used=context.gas_limit - gas_remaining,
                        gas_remaining=gas_remaining,
                        return_data=revert_bytes,
                        error="Execution reverted",
                        logs=[],
                    )

                elif op == INVALID:
                    raise InvalidOpcodeError(f"Invalid opcode 0x{op:02x}")

                elif op == SELFDESTRUCT:
                    if context.is_static:
                        raise StaticModeViolationError("SELFDESTRUCT not permitted in static context")
                    rec_int = stack.pop()
                    rec_addr = "0x" + format(rec_int & UINT256_MAX, "040x")[-40:]
                    consume_gas(GAS_SELFDESTRUCT)
                    self.state.selfdestruct(context.address, rec_addr)
                    break

                else:
                    raise InvalidOpcodeError(f"Unknown or unsupported opcode 0x{op:02x}")

            except (StackUnderflowError, StackOverflowError, InvalidJumpError,
                    StaticModeViolationError, InvalidOpcodeError, OutOfGasError) as exc:
                return ExecutionResult(
                    success=False,
                    gas_used=context.gas_limit,
                    gas_remaining=0,
                    return_data=b"",
                    error=str(exc),
                    logs=[],
                )
            except Exception as exc:
                return ExecutionResult(
                    success=False,
                    gas_used=context.gas_limit,
                    gas_remaining=0,
                    return_data=b"",
                    error=f"Runtime error: {exc}",
                    logs=[],
                )

        return ExecutionResult(
            success=True,
            gas_used=context.gas_limit - gas_remaining,
            gas_remaining=gas_remaining,
            return_data=last_return_data,
            logs=logs,
        )
