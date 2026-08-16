"""ERC-20 Compliant L2 Token Bytecode Generator, ABI Encoders, and Standard Interface."""

from enum import Enum
from pacvo.crypto import keccak256
from pacvo.evm.opcodes import *


class TokenType(Enum):
    FIXED_SUPPLY = "FIXED_SUPPLY"
    CONTROLLED_MINT = "CONTROLLED_MINT"


# --- Standard ERC-20 4-Byte Selectors ---
SEL_TOTAL_SUPPLY   = bytes.fromhex("18160ddd") # totalSupply()
SEL_BALANCE_OF     = bytes.fromhex("70a08231") # balanceOf(address)
SEL_TRANSFER       = bytes.fromhex("a9059cbb") # transfer(address,uint256)
SEL_ALLOWANCE      = bytes.fromhex("dd62ed3e") # allowance(address,address)
SEL_APPROVE        = bytes.fromhex("095ea7b3") # approve(address,uint256)
SEL_TRANSFER_FROM  = bytes.fromhex("23b872dd") # transferFrom(address,address,uint256)
SEL_NAME           = bytes.fromhex("06fdde03") # name()
SEL_SYMBOL         = bytes.fromhex("95d89b41") # symbol()
SEL_DECIMALS       = bytes.fromhex("313ce567") # decimals()
SEL_MINT           = bytes.fromhex("40c10f19") # mint(address,uint256)
SEL_BURN           = bytes.fromhex("42966c68") # burn(uint256)

# --- Standard Event Topics (Keccak-256) ---
TOPIC_TRANSFER = bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
TOPIC_APPROVAL = bytes.fromhex("8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925")


# --- ABI Encoding Helpers ---

def encode_total_supply() -> bytes:
    return SEL_TOTAL_SUPPLY

def encode_balance_of(owner: str) -> bytes:
    addr_bytes = bytes.fromhex(owner.removeprefix("0x").lower().rjust(40, "0"))
    return SEL_BALANCE_OF + addr_bytes.rjust(32, b"\x00")

def encode_transfer(to: str, amount: int) -> bytes:
    addr_bytes = bytes.fromhex(to.removeprefix("0x").lower().rjust(40, "0"))
    return SEL_TRANSFER + addr_bytes.rjust(32, b"\x00") + amount.to_bytes(32, "big")

def encode_allowance(owner: str, spender: str) -> bytes:
    o_bytes = bytes.fromhex(owner.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    s_bytes = bytes.fromhex(spender.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    return SEL_ALLOWANCE + o_bytes + s_bytes

def encode_approve(spender: str, amount: int) -> bytes:
    addr_bytes = bytes.fromhex(spender.removeprefix("0x").lower().rjust(40, "0"))
    return SEL_APPROVE + addr_bytes.rjust(32, b"\x00") + amount.to_bytes(32, "big")

def encode_transfer_from(from_: str, to: str, amount: int) -> bytes:
    f_bytes = bytes.fromhex(from_.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    t_bytes = bytes.fromhex(to.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    return SEL_TRANSFER_FROM + f_bytes + t_bytes + amount.to_bytes(32, "big")

def encode_mint(to: str, amount: int) -> bytes:
    addr_bytes = bytes.fromhex(to.removeprefix("0x").lower().rjust(40, "0"))
    return SEL_MINT + addr_bytes.rjust(32, b"\x00") + amount.to_bytes(32, "big")

def encode_burn(amount: int) -> bytes:
    return SEL_BURN + amount.to_bytes(32, "big")

def encode_name() -> bytes:
    return SEL_NAME

def encode_symbol() -> bytes:
    return SEL_SYMBOL

def encode_decimals() -> bytes:
    return SEL_DECIMALS


# --- Storage Slot Layout ---
SLOT_TOTAL_SUPPLY = 0
SLOT_MINTER       = 1
SLOT_NAME         = 2
SLOT_SYMBOL       = 3
SLOT_DECIMALS     = 4
SLOT_BALANCES     = 5
SLOT_ALLOWANCES   = 6


def get_balance_slot(owner_addr: str) -> int:
    addr_clean = bytes.fromhex(owner_addr.removeprefix("0x").lower().rjust(40, "0"))
    key = addr_clean.rjust(32, b"\x00") + (SLOT_BALANCES).to_bytes(32, "big")
    return int.from_bytes(keccak256(key), "big")


def get_allowance_slot(owner_addr: str, spender_addr: str) -> int:
    o_bytes = bytes.fromhex(owner_addr.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    s_bytes = bytes.fromhex(spender_addr.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    inner_hash = keccak256(o_bytes + (SLOT_ALLOWANCES).to_bytes(32, "big"))
    outer_hash = keccak256(s_bytes + inner_hash)
    return int.from_bytes(outer_hash, "big")


# --- EVM Micro-Assembler ---

class Assembler:
    """Two-pass label-resolving EVM bytecode assembler."""

    def __init__(self):
        self.items = []

    def op(self, opcode: int):
        self.items.append(("op", opcode))
        return self

    def push(self, val: int | bytes):
        if isinstance(val, int):
            if val == 0:
                self.items.append(("op", PUSH0))
                return self
            length = max(1, (val.bit_length() + 7) // 8)
            val_bytes = val.to_bytes(length, "big")
        else:
            val_bytes = val
            length = len(val_bytes)
        self.items.append(("push", length, val_bytes))
        return self

    def push_label(self, label: str):
        # 2-byte push for label addresses
        self.items.append(("push_label", label))
        return self

    def label(self, name: str):
        self.items.append(("label", name))
        return self

    def assemble(self) -> bytes:
        # Pass 1: compute bytecode size and label offsets
        labels = {}
        offset = 0
        for item in self.items:
            itype = item[0]
            if itype == "label":
                labels[item[1]] = offset
                offset += 1
            elif itype == "op":
                offset += 1
            elif itype == "push":
                offset += 1 + item[1]
            elif itype == "push_label":
                offset += 3 # PUSH2 + 2 bytes

        # Pass 2: emit bytecode
        out = bytearray()
        for item in self.items:
            itype = item[0]
            if itype == "label":
                out.append(JUMPDEST)
            elif itype == "op":
                out.append(item[1])
            elif itype == "push":
                length = item[1]
                val_bytes = item[2]
                out.append(PUSH1 + length - 1)
                out.extend(val_bytes)
            elif itype == "push_label":
                target_offset = labels[item[1]]
                out.append(PUSH2)
                out.extend(target_offset.to_bytes(2, "big"))

        return bytes(out)


class ERC20Token:
    """Generator for production ERC-20 token bytecode and constructor initcode."""

    @staticmethod
    def build_runtime(token_type: TokenType = TokenType.FIXED_SUPPLY) -> bytes:
        asm = Assembler()

        # Extract 4-byte selector: calldata[0..32] >> 224
        asm.push(0).op(CALLDATALOAD).push(224).op(SHR)

        # Dispatch Table
        def check_sel(sel: bytes, label: str):
            asm.op(DUP1).push(sel).op(EQ).push_label(label).op(JUMPI)

        check_sel(SEL_TOTAL_SUPPLY,  "fn_total_supply")
        check_sel(SEL_BALANCE_OF,    "fn_balance_of")
        check_sel(SEL_TRANSFER,      "fn_transfer")
        check_sel(SEL_ALLOWANCE,     "fn_allowance")
        check_sel(SEL_APPROVE,       "fn_approve")
        check_sel(SEL_TRANSFER_FROM, "fn_transfer_from")
        check_sel(SEL_NAME,          "fn_name")
        check_sel(SEL_SYMBOL,        "fn_symbol")
        check_sel(SEL_DECIMALS,      "fn_decimals")

        if token_type == TokenType.CONTROLLED_MINT:
            check_sel(SEL_MINT, "fn_mint")
            check_sel(SEL_BURN, "fn_burn")

        # Fallback / No Match -> REVERT
        asm.push(0).push(0).op(REVERT)

        # --- 1. totalSupply() -> uint256 ---
        asm.label("fn_total_supply")
        asm.push(SLOT_TOTAL_SUPPLY).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 2. balanceOf(address) -> uint256 ---
        asm.label("fn_balance_of")
        # slot = keccak256(calldata[4..36] || SLOT_BALANCES)
        asm.push(4).op(CALLDATALOAD).push(0).op(MSTORE)
        asm.push(SLOT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 3. transfer(address to, uint256 amount) -> bool ---
        asm.label("fn_transfer")
        asm.push(4).op(CALLDATALOAD)   # [to]
        asm.push(36).op(CALLDATALOAD)  # [amount, to]

        # Calculate sender balance slot = keccak256(caller || SLOT_BALANCES)
        asm.op(CALLER).push(0).op(MSTORE)
        asm.push(SLOT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [sender_slot, amount, to]

        # Check sender_bal >= amount
        asm.op(DUP2)                  # [amount, sender_slot, amount, to]
        asm.op(DUP2).op(SLOAD)        # [sender_bal, amount, sender_slot, amount, to]
        asm.op(DUP2).op(DUP2).op(LT)  # [sender_bal < amount, sender_bal, amount, sender_slot, amount, to]
        asm.push_label("revert_branch").op(JUMPI)

        # Deduct sender: sstore(sender_slot, sender_bal - amount)
        asm.op(SUB)                   # [sender_bal - amount, sender_slot, amount, to]
        asm.op(DUP2).op(SSTORE)       # sstore(sender_slot, new_bal) -> [sender_slot, amount, to]
        asm.op(POP)                   # [amount, to]

        # Calculate recipient balance slot = keccak256(to || SLOT_BALANCES)
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [to_slot, amount, to]

        # Add recipient: sstore(to_slot, sload(to_slot) + amount)
        asm.op(DUP1).op(SLOAD).op(DUP3).op(ADD).op(DUP2).op(SSTORE)
        asm.op(POP)                   # [amount, to]

        # Emit Transfer(caller, to, amount)
        asm.push(0).op(MSTORE)        # memory[0..32] = amount, stack: [to]
        asm.op(CALLER)                # [caller, to]
        asm.push(TOPIC_TRANSFER)      # [topic, caller, to]
        asm.push(32).push(0).op(LOG3) # LOG3(offset=0, len=32, topic0, from, to)

        # Return true (1)
        asm.push(1).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 4. allowance(address owner, address spender) -> uint256 ---
        asm.label("fn_allowance")
        # slot = keccak256(spender || keccak256(owner || SLOT_ALLOWANCES))
        asm.push(4).op(CALLDATALOAD).push(0).op(MSTORE)
        asm.push(SLOT_ALLOWANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [owner_hash]
        asm.push(36).op(CALLDATALOAD).push(0).op(MSTORE) # memory[0..32] = spender
        asm.push(32).op(MSTORE)       # memory[32..64] = owner_hash
        asm.push(64).push(0).op(SHA3).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 5. approve(address spender, uint256 amount) -> bool ---
        asm.label("fn_approve")
        asm.push(4).op(CALLDATALOAD)  # [spender]
        asm.push(36).op(CALLDATALOAD) # [amount, spender]

        # Compute allowance slot: keccak256(spender || keccak256(caller || SLOT_ALLOWANCES))
        asm.op(CALLER).push(0).op(MSTORE)
        asm.push(SLOT_ALLOWANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [caller_hash, amount, spender]
        asm.op(DUP3).push(0).op(MSTORE)
        asm.push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [allowance_slot, amount, spender]

        # Store allowance: sstore(allowance_slot, amount)
        asm.op(DUP2).op(DUP2).op(SSTORE)
        asm.op(POP) # [amount, spender]

        # Emit Approval(caller, spender, amount)
        asm.push(0).op(MSTORE)
        asm.op(CALLER)
        asm.push(TOPIC_APPROVAL)
        asm.push(32).push(0).op(LOG3)

        # Return true
        asm.push(1).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 6. transferFrom(address from, address to, uint256 amount) -> bool ---
        asm.label("fn_transfer_from")
        asm.push(4).op(CALLDATALOAD)   # [from]
        asm.push(36).op(CALLDATALOAD)  # [to, from]
        asm.push(68).op(CALLDATALOAD)  # [amount, to, from]

        # Check allowance: sload(keccak256(caller || keccak256(from || SLOT_ALLOWANCES)))
        asm.op(DUP3).push(0).op(MSTORE)
        asm.push(SLOT_ALLOWANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [from_hash, amount, to, from]
        asm.op(CALLER).push(0).op(MSTORE)
        asm.push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [allowance_slot, amount, to, from]

        # Check allowance >= amount
        asm.op(DUP2)                  # [amount, allowance_slot, amount, to, from]
        asm.op(DUP2).op(SLOAD)        # [cur_allowance, amount, allowance_slot, amount, to, from]
        asm.op(DUP2).op(DUP2).op(LT)  # [cur_allowance < amount, ...]
        asm.push_label("revert_branch").op(JUMPI)

        # Deduct allowance: sstore(allowance_slot, cur_allowance - amount)
        asm.op(SUB)
        asm.op(DUP2).op(SSTORE)
        asm.op(POP)                   # [amount, to, from]

        # Check & Deduct from_bal: sload(keccak256(from || SLOT_BALANCES))
        asm.op(DUP3).push(0).op(MSTORE)
        asm.push(SLOT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [from_bal_slot, amount, to, from]

        asm.op(DUP2)                  # [amount, from_bal_slot, amount, to, from]
        asm.op(DUP2).op(SLOAD)        # [from_bal, amount, from_bal_slot, amount, to, from]
        asm.op(DUP2).op(DUP2).op(LT)  # [from_bal < amount, ...]
        asm.push_label("revert_branch").op(JUMPI)

        # Deduct from_bal: sstore(from_bal_slot, from_bal - amount)
        asm.op(SUB)
        asm.op(DUP2).op(SSTORE)
        asm.op(POP)                   # [amount, to, from]

        # Add to_bal: sload(keccak256(to || SLOT_BALANCES))
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [to_bal_slot, amount, to, from]
        asm.op(DUP1).op(SLOAD).op(DUP3).op(ADD).op(DUP2).op(SSTORE)
        asm.op(POP)                   # [amount, to, from]

        # Emit Transfer(from, to, amount)
        asm.push(0).op(MSTORE)        # memory[0..32] = amount, stack: [to, from]
        asm.push(TOPIC_TRANSFER)      # [topic, to, from]
        asm.op(SWAP2)                 # [from, to, topic]
        asm.op(SWAP1)                 # [to, from, topic]
        asm.op(SWAP2)                 # [topic, from, to]
        asm.push(32).push(0).op(LOG3) # LOG3(0, 32, topic, from, to)

        # Return true
        asm.push(1).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 7. Metadata Getters (name, symbol, decimals) ---
        asm.label("fn_name")
        asm.push(SLOT_NAME).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        asm.label("fn_symbol")
        asm.push(SLOT_SYMBOL).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        asm.label("fn_decimals")
        asm.push(SLOT_DECIMALS).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 8. Controlled Mint & Burn (if enabled) ---
        if token_type == TokenType.CONTROLLED_MINT:
            asm.label("fn_mint")
            # Verify caller == sload(SLOT_MINTER)
            asm.push(SLOT_MINTER).op(SLOAD).op(CALLER).op(EQ).op(ISZERO)
            asm.push_label("revert_branch").op(JUMPI)

            asm.push(4).op(CALLDATALOAD)  # [to]
            asm.push(36).op(CALLDATALOAD) # [amount, to]

            # Add to totalSupply: sload(SLOT_TOTAL_SUPPLY) + amount
            asm.push(SLOT_TOTAL_SUPPLY).op(SLOAD).op(DUP2).op(ADD) # [new_supply, amount, to]
            asm.push(SLOT_TOTAL_SUPPLY).op(SSTORE) # sstore(0, new_supply) -> [amount, to]

            # Add to to_bal
            asm.op(DUP2).push(0).op(MSTORE)
            asm.push(SLOT_BALANCES).push(32).op(MSTORE)
            asm.push(64).push(0).op(SHA3) # [to_slot, amount, to]
            asm.op(DUP1).op(SLOAD).op(DUP3).op(ADD).op(DUP2).op(SSTORE)
            asm.op(POP) # [amount, to]

            # Emit Transfer(address(0), to, amount)
            asm.push(0).op(MSTORE) # memory[0..32] = amount, stack: [to]
            asm.push(0)            # [0, to] (to is topic2, 0 is topic1)
            asm.push(TOPIC_TRANSFER) # [topic0, 0, to]
            asm.push(32).push(0).op(LOG3) # LOG3(0, 32, topic0, 0, to) -> stack: []

            asm.push(1).push(0).op(MSTORE)
            asm.push(32).push(0).op(RETURN)

            asm.label("fn_burn")
            asm.push(4).op(CALLDATALOAD) # [amount]

            # Check caller_bal >= amount
            asm.op(CALLER).push(0).op(MSTORE)
            asm.push(SLOT_BALANCES).push(32).op(MSTORE)
            asm.push(64).push(0).op(SHA3) # [caller_slot, amount]

            asm.op(DUP2)                  # [amount, caller_slot, amount]
            asm.op(DUP2).op(SLOAD)        # [caller_bal, amount, caller_slot, amount]
            asm.op(DUP2).op(DUP2).op(LT)  # [caller_bal < amount, ...]
            asm.push_label("revert_branch").op(JUMPI)

            # Deduct caller_bal
            asm.op(SUB)                   # [caller_bal - amount, caller_slot, amount]
            asm.op(DUP2).op(SSTORE)
            asm.op(POP)                   # [amount]

            # Deduct totalSupply: sload(SLOT_TOTAL_SUPPLY) - amount
            asm.push(SLOT_TOTAL_SUPPLY).op(SLOAD) # [totalSupply, amount]
            asm.op(SUB)                   # [totalSupply - amount]
            asm.push(SLOT_TOTAL_SUPPLY).op(SSTORE) # sstore(0, new_supply) -> stack: []

            # Emit Transfer(caller, address(0), amount)
            asm.push(0).op(MSTORE) # memory[0..32] = amount, stack: []
            asm.push(0)            # [0] (topic2 = 0)
            asm.op(CALLER)         # [caller, 0] (topic1 = caller)
            asm.push(TOPIC_TRANSFER) # [topic0, caller, 0]
            asm.push(32).push(0).op(LOG3) # LOG3(0, 32, topic0, caller, 0) -> stack: []

            asm.push(1).push(0).op(MSTORE)
            asm.push(32).push(0).op(RETURN)

        # Common Revert Target
        asm.label("revert_branch")
        asm.push(0).push(0).op(REVERT)

        return asm.assemble()

    @staticmethod
    def build_initcode(
        name: str,
        symbol: str,
        decimals: int,
        initial_supply: int,
        token_type: TokenType = TokenType.FIXED_SUPPLY,
        minter: str = "",
    ) -> bytes:
        """Build constructor initcode that initializes state and deploys runtime."""
        runtime = ERC20Token.build_runtime(token_type)

        # Format 32-byte short strings for name and symbol
        name_bytes = name.encode("utf-8")[:31].ljust(32, b"\x00")
        symbol_bytes = symbol.encode("utf-8")[:31].ljust(32, b"\x00")

        minter_int = int(minter.removeprefix("0x").lower(), 16) if minter else 0

        asm = Assembler()

        # Store metadata in constructor
        asm.push(name_bytes).push(SLOT_NAME).op(SSTORE)
        asm.push(symbol_bytes).push(SLOT_SYMBOL).op(SSTORE)
        asm.push(decimals).push(SLOT_DECIMALS).op(SSTORE)

        # Store minter (if controlled)
        if token_type == TokenType.CONTROLLED_MINT and minter_int != 0:
            asm.push(minter_int).push(SLOT_MINTER).op(SSTORE)

        # Store initial supply to deployer (if > 0)
        if initial_supply > 0:
            asm.push(initial_supply).push(SLOT_TOTAL_SUPPLY).op(SSTORE)
            # balance[caller] = initial_supply
            asm.op(CALLER).push(0).op(MSTORE)
            asm.push(SLOT_BALANCES).push(32).op(MSTORE)
            asm.push(64).push(0).op(SHA3) # [caller_slot]
            asm.push(initial_supply).op(SWAP1).op(SSTORE)

            # Emit Transfer(0x0, caller, initial_supply)
            asm.push(initial_supply).push(0).op(MSTORE)
            asm.op(CALLER).push(0).push(TOPIC_TRANSFER)
            asm.push(32).push(0).op(LOG3)

        # Copy runtime code to memory and return
        base_header = asm.assemble()
        # Footer: PUSH2 len(runtime), PUSH2 header_len, PUSH1 0, CODECOPY, PUSH2 len(runtime), PUSH1 0, RETURN
        footer_len = 15
        header_len = len(base_header) + footer_len
        footer = bytes([
            PUSH2, *len(runtime).to_bytes(2, "big"),
            PUSH2, *header_len.to_bytes(2, "big"),
            PUSH1, 0,
            CODECOPY,
            PUSH2, *len(runtime).to_bytes(2, "big"),
            PUSH1, 0,
            RETURN,
        ])
        assert len(footer) == footer_len
        return base_header + footer + runtime
