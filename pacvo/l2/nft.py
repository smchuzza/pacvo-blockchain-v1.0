"""ERC-721 Compliant L2 NFT Bytecode Generator, ABI Encoders, and Standard Interface.

Provides bytecode compilation, ABI encoding/decoding, and deployment utilities for
Solidity ERC-721 Non-Fungible Tokens on Pacvo Layer 2.
"""

from pacvo.crypto import keccak256
from pacvo.evm.opcodes import *
from pacvo.l2.token import Assembler

# --- Standard ERC-721 4-Byte Selectors ---
SEL_NFT_BALANCE_OF          = bytes.fromhex("70a08231")  # balanceOf(address)
SEL_NFT_OWNER_OF            = bytes.fromhex("6352211e")  # ownerOf(uint256)
SEL_NFT_TRANSFER_FROM       = bytes.fromhex("23b872dd")  # transferFrom(address,address,uint256)
SEL_NFT_SAFE_TRANSFER_FROM  = bytes.fromhex("42842e0e")  # safeTransferFrom(address,address,uint256)
SEL_NFT_APPROVE             = bytes.fromhex("095ea7b3")  # approve(address,uint256)
SEL_NFT_GET_APPROVED        = bytes.fromhex("081812fc")  # getApproved(uint256)
SEL_NFT_SET_APPROVAL_FOR_ALL= bytes.fromhex("a22cb465")  # setApprovalForAll(address,bool)
SEL_NFT_IS_APPROVED_FOR_ALL = bytes.fromhex("e985e9c5")  # isApprovedForAll(address,address)
SEL_NFT_MINT                = bytes.fromhex("40c10f19")  # mint(address,uint256)
SEL_NFT_BURN                = bytes.fromhex("42966c68")  # burn(uint256)
SEL_NFT_NAME                = bytes.fromhex("06fdde03")  # name()
SEL_NFT_SYMBOL              = bytes.fromhex("95d89b41")  # symbol()
SEL_NFT_TOTAL_SUPPLY        = bytes.fromhex("18160ddd")  # totalSupply()
SEL_NFT_TOKEN_URI           = bytes.fromhex("c87b56dd")  # tokenURI(uint256)

# --- Standard Event Topics (Keccak-256) ---
TOPIC_NFT_TRANSFER          = bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
TOPIC_NFT_APPROVAL          = bytes.fromhex("8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925")
TOPIC_NFT_APPROVAL_FOR_ALL  = bytes.fromhex("17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31")

# --- Storage Slots ---
SLOT_NFT_TOTAL_SUPPLY = 0
SLOT_NFT_MINTER       = 1
SLOT_NFT_NAME         = 2
SLOT_NFT_SYMBOL       = 3
SLOT_NFT_OWNERS       = 10
SLOT_NFT_BALANCES     = 11
SLOT_NFT_APPROVALS    = 12
SLOT_NFT_OPERATORS    = 13
SLOT_NFT_URIS         = 14


# --- ABI Encoding Helpers ---

def encode_nft_balance_of(owner: str) -> bytes:
    addr_bytes = bytes.fromhex(owner.removeprefix("0x").lower().rjust(40, "0"))
    return SEL_NFT_BALANCE_OF + addr_bytes.rjust(32, b"\x00")


def encode_nft_owner_of(token_id: int) -> bytes:
    return SEL_NFT_OWNER_OF + token_id.to_bytes(32, "big")


def encode_nft_transfer_from(from_: str, to: str, token_id: int) -> bytes:
    f_bytes = bytes.fromhex(from_.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    t_bytes = bytes.fromhex(to.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    return SEL_NFT_TRANSFER_FROM + f_bytes + t_bytes + token_id.to_bytes(32, "big")


def encode_nft_approve(to: str, token_id: int) -> bytes:
    addr_bytes = bytes.fromhex(to.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    return SEL_NFT_APPROVE + addr_bytes + token_id.to_bytes(32, "big")


def encode_nft_get_approved(token_id: int) -> bytes:
    return SEL_NFT_GET_APPROVED + token_id.to_bytes(32, "big")


def encode_nft_set_approval_for_all(operator: str, approved: bool) -> bytes:
    addr_bytes = bytes.fromhex(operator.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    appr_bytes = (1 if approved else 0).to_bytes(32, "big")
    return SEL_NFT_SET_APPROVAL_FOR_ALL + addr_bytes + appr_bytes


def encode_nft_is_approved_for_all(owner: str, operator: str) -> bytes:
    o_bytes = bytes.fromhex(owner.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    op_bytes = bytes.fromhex(operator.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    return SEL_NFT_IS_APPROVED_FOR_ALL + o_bytes + op_bytes


def encode_nft_mint(to: str, token_id: int) -> bytes:
    addr_bytes = bytes.fromhex(to.removeprefix("0x").lower().rjust(40, "0")).rjust(32, b"\x00")
    return SEL_NFT_MINT + addr_bytes + token_id.to_bytes(32, "big")


def encode_nft_burn(token_id: int) -> bytes:
    return SEL_NFT_BURN + token_id.to_bytes(32, "big")


def encode_nft_name() -> bytes:
    return SEL_NFT_NAME


def encode_nft_symbol() -> bytes:
    return SEL_NFT_SYMBOL


def encode_nft_total_supply() -> bytes:
    return SEL_NFT_TOTAL_SUPPLY


# --- ERC-721 Bytecode Generator ---

class ERC721Token:
    """Bytecode builder and interface for ERC-721 Non-Fungible Tokens on Pacvo L2."""

    @staticmethod
    def build_runtime() -> bytes:
        """Construct deterministic EVM runtime bytecode implementing ERC-721."""
        asm = Assembler()

        # Extract 4-byte selector: calldata[0..32] >> 224
        asm.push(0).op(CALLDATALOAD).push(224).op(SHR)

        # Dispatch Table
        def check_sel(sel: bytes, label: str):
            asm.op(DUP1).push(sel).op(EQ).push_label(label).op(JUMPI)

        check_sel(SEL_NFT_TOTAL_SUPPLY,         "fn_total_supply")
        check_sel(SEL_NFT_NAME,                 "fn_name")
        check_sel(SEL_NFT_SYMBOL,               "fn_symbol")
        check_sel(SEL_NFT_BALANCE_OF,           "fn_balance_of")
        check_sel(SEL_NFT_OWNER_OF,             "fn_owner_of")
        check_sel(SEL_NFT_GET_APPROVED,         "fn_get_approved")
        check_sel(SEL_NFT_IS_APPROVED_FOR_ALL,  "fn_is_approved_for_all")
        check_sel(SEL_NFT_APPROVE,              "fn_approve")
        check_sel(SEL_NFT_SET_APPROVAL_FOR_ALL, "fn_set_approval_for_all")
        check_sel(SEL_NFT_MINT,                 "fn_mint")
        check_sel(SEL_NFT_TRANSFER_FROM,        "fn_transfer_from")
        check_sel(SEL_NFT_SAFE_TRANSFER_FROM,   "fn_transfer_from")
        check_sel(SEL_NFT_BURN,                 "fn_burn")

        # Fallback / Revert
        asm.push(0).push(0).op(REVERT)

        # --- 1. totalSupply() -> uint256 ---
        asm.label("fn_total_supply")
        asm.push(SLOT_NFT_TOTAL_SUPPLY).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 2. name() -> string ---
        asm.label("fn_name")
        asm.push(SLOT_NFT_NAME).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 3. symbol() -> string ---
        asm.label("fn_symbol")
        asm.push(SLOT_NFT_SYMBOL).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 4. balanceOf(owner) -> uint256 ---
        asm.label("fn_balance_of")
        asm.push(4).op(CALLDATALOAD).push(0).op(MSTORE)
        asm.push(SLOT_NFT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 5. ownerOf(tokenId) -> address ---
        asm.label("fn_owner_of")
        asm.push(4).op(CALLDATALOAD).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OWNERS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD)
        asm.op(DUP1).op(ISZERO).push_label("revert_branch").op(JUMPI)
        asm.push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 6. getApproved(tokenId) -> address ---
        asm.label("fn_get_approved")
        asm.push(4).op(CALLDATALOAD).push(0).op(MSTORE)
        asm.push(SLOT_NFT_APPROVALS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 7. isApprovedForAll(owner, operator) -> bool ---
        asm.label("fn_is_approved_for_all")
        asm.push(4).op(CALLDATALOAD).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OPERATORS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.push(36).op(CALLDATALOAD).push(0).op(MSTORE)
        asm.push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 8. approve(to, tokenId) ---
        asm.label("fn_approve")
        asm.push(4).op(CALLDATALOAD)   # [to]
        asm.push(36).op(CALLDATALOAD)  # [tokenId, to]
        # Fetch owner
        asm.op(DUP1).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OWNERS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD) # [owner, tokenId, to]
        asm.op(DUP1).op(ISZERO).push_label("revert_branch").op(JUMPI)
        # Check caller == owner
        asm.op(DUP1).op(CALLER).op(EQ).push_label("do_set_approve").op(JUMPI)
        # Check isApprovedForAll(owner, caller)
        asm.op(DUP1).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OPERATORS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.op(CALLER).push(0).op(MSTORE)
        asm.push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD)
        asm.op(ISZERO).push_label("revert_branch").op(JUMPI)

        asm.label("do_set_approve")
        # Store _tokenApprovals[tokenId] = to
        asm.op(DUP3).op(DUP3).push(0).op(MSTORE)
        asm.push(SLOT_NFT_APPROVALS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SSTORE)
        # Emit Approval(owner, to, tokenId)
        asm.op(DUP2).push(0).op(MSTORE) # memory[0..32] = tokenId
        asm.op(DUP3) # [to]
        asm.op(DUP2) # [owner]
        asm.push(TOPIC_NFT_APPROVAL)
        asm.push(32).push(0).op(LOG3)
        asm.push(1).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 9. setApprovalForAll(operator, approved) ---
        asm.label("fn_set_approval_for_all")
        asm.push(4).op(CALLDATALOAD)   # [operator]
        asm.push(36).op(CALLDATALOAD)  # [approved, operator]
        asm.op(CALLER).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OPERATORS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.op(DUP3).push(0).op(MSTORE)
        asm.push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [slot, approved, operator]
        asm.op(DUP2).op(SWAP1).op(SSTORE)
        # Emit ApprovalForAll(caller, operator, approved)
        asm.op(DUP1).push(0).op(MSTORE)
        asm.op(DUP2) # [operator]
        asm.op(CALLER) # [caller]
        asm.push(TOPIC_NFT_APPROVAL_FOR_ALL)
        asm.push(32).push(0).op(LOG3)
        asm.push(1).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 10. mint(to, tokenId) ---
        asm.label("fn_mint")
        # Verify caller == minter (slot 1)
        asm.push(SLOT_NFT_MINTER).op(SLOAD).op(CALLER).op(EQ).op(ISZERO).push_label("revert_branch").op(JUMPI)
        asm.push(4).op(CALLDATALOAD)   # [to]
        asm.push(36).op(CALLDATALOAD)  # [tokenId, to]
        # Check _owners[tokenId] == 0
        asm.op(DUP1).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OWNERS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [owner_slot, tokenId, to]
        asm.op(DUP1).op(SLOAD).op(ISZERO).op(ISZERO).push_label("revert_branch").op(JUMPI)
        # _owners[tokenId] = to
        asm.op(DUP3).op(SWAP1).op(SSTORE) # stack: [tokenId, to]
        # _balances[to] += 1
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_NFT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3) # [bal_slot, tokenId, to]
        asm.op(DUP1).op(SLOAD).push(1).op(ADD).op(SWAP1).op(SSTORE) # stack: [tokenId, to]
        # totalSupply += 1
        asm.push(SLOT_NFT_TOTAL_SUPPLY).op(SLOAD).push(1).op(ADD).push(SLOT_NFT_TOTAL_SUPPLY).op(SSTORE)
        # Emit Transfer(0, to, tokenId)
        asm.op(DUP1).push(0).op(MSTORE) # memory[0..32] = tokenId
        asm.op(DUP2) # [to]
        asm.push(0)  # [0]
        asm.push(TOPIC_NFT_TRANSFER)
        asm.push(32).push(0).op(LOG3)
        asm.push(1).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 11. transferFrom(from, to, tokenId) ---
        asm.label("fn_transfer_from")
        asm.push(4).op(CALLDATALOAD)   # [from]
        asm.push(36).op(CALLDATALOAD)  # [to, from]
        asm.push(68).op(CALLDATALOAD)  # [tokenId, to, from]
        # Fetch owner
        asm.op(DUP1).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OWNERS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD) # [owner, tokenId, to, from]
        # Check owner == from
        asm.op(DUP1).op(DUP5).op(EQ).op(ISZERO).push_label("revert_branch").op(JUMPI)
        # Check auth
        asm.op(DUP1).op(CALLER).op(EQ).push_label("do_transfer").op(JUMPI)
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_NFT_APPROVALS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD)
        asm.op(CALLER).op(EQ).push_label("do_transfer").op(JUMPI)
        asm.op(DUP1).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OPERATORS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.op(CALLER).push(0).op(MSTORE)
        asm.push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD)
        asm.op(ISZERO).push_label("revert_branch").op(JUMPI)

        asm.label("do_transfer")
        # Clear _tokenApprovals[tokenId] = 0
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_NFT_APPROVALS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.push(0).op(SWAP1).op(SSTORE)
        # _balances[from] -= 1
        asm.op(DUP4).push(0).op(MSTORE)
        asm.push(SLOT_NFT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.op(DUP1).op(SLOAD).push(1).op(SWAP1).op(SUB).op(SWAP1).op(SSTORE)
        # _balances[to] += 1
        asm.op(DUP3).push(0).op(MSTORE)
        asm.push(SLOT_NFT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.op(DUP1).op(SLOAD).push(1).op(ADD).op(SWAP1).op(SSTORE)
        # _owners[tokenId] = to
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OWNERS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.op(DUP4).op(SWAP1).op(SSTORE)
        # Emit Transfer(from, to, tokenId)
        asm.op(DUP2).push(0).op(MSTORE) # memory[0..32] = tokenId
        asm.op(DUP3) # [to]
        asm.op(DUP5) # [from]
        asm.push(TOPIC_NFT_TRANSFER)
        asm.push(32).push(0).op(LOG3)
        asm.push(1).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # --- 12. burn(tokenId) ---
        asm.label("fn_burn")
        asm.push(4).op(CALLDATALOAD) # [tokenId]
        # Fetch owner
        asm.op(DUP1).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OWNERS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD) # [owner, tokenId]
        asm.op(DUP1).op(ISZERO).push_label("revert_branch").op(JUMPI)
        # Check auth
        asm.op(DUP1).op(CALLER).op(EQ).push_label("do_burn").op(JUMPI)
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_NFT_APPROVALS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD)
        asm.op(CALLER).op(EQ).push_label("do_burn").op(JUMPI)
        asm.op(DUP1).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OPERATORS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.op(CALLER).push(0).op(MSTORE)
        asm.push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3).op(SLOAD)
        asm.op(ISZERO).push_label("revert_branch").op(JUMPI)

        asm.label("do_burn")
        # _balances[owner] -= 1
        asm.op(DUP1).push(0).op(MSTORE)
        asm.push(SLOT_NFT_BALANCES).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.op(DUP1).op(SLOAD).push(1).op(SWAP1).op(SUB).op(SWAP1).op(SSTORE)
        # _owners[tokenId] = 0
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_NFT_OWNERS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.push(0).op(SWAP1).op(SSTORE)
        # _tokenApprovals[tokenId] = 0
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(SLOT_NFT_APPROVALS).push(32).op(MSTORE)
        asm.push(64).push(0).op(SHA3)
        asm.push(0).op(SWAP1).op(SSTORE)
        # totalSupply -= 1
        asm.push(SLOT_NFT_TOTAL_SUPPLY).op(SLOAD).push(1).op(SWAP1).op(SUB).push(SLOT_NFT_TOTAL_SUPPLY).op(SSTORE)
        # Emit Transfer(owner, 0, tokenId)
        asm.op(DUP2).push(0).op(MSTORE)
        asm.push(0)  # [0]
        asm.op(DUP2) # [owner]
        asm.push(TOPIC_NFT_TRANSFER)
        asm.push(32).push(0).op(LOG3)
        asm.push(1).push(0).op(MSTORE)
        asm.push(32).push(0).op(RETURN)

        # Common Revert Target
        asm.label("revert_branch")
        asm.push(0).push(0).op(REVERT)

        return asm.assemble()

    @staticmethod
    def build_initcode(name: str, symbol: str, minter: str) -> bytes:
        """Construct standard ERC-721 deployment initcode storing metadata and minter."""
        runtime = ERC721Token.build_runtime()

        name_bytes = name.encode("utf-8")[:31].ljust(32, b"\x00")
        sym_bytes = symbol.encode("utf-8")[:31].ljust(32, b"\x00")
        minter_int = int(minter.removeprefix("0x").lower(), 16) if minter else 0

        asm = Assembler()

        # 1. Total supply = 0
        asm.push(0).push(SLOT_NFT_TOTAL_SUPPLY).op(SSTORE)

        # 2. Minter
        if minter_int != 0:
            asm.push(minter_int).push(SLOT_NFT_MINTER).op(SSTORE)

        # 3. Name & Symbol
        asm.push(name_bytes).push(SLOT_NFT_NAME).op(SSTORE)
        asm.push(sym_bytes).push(SLOT_NFT_SYMBOL).op(SSTORE)

        # Copy runtime code to memory and return
        base_header = asm.assemble()
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
