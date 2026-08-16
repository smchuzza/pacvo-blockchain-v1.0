"""L2 Application State Abstraction and Token Registry."""

import time
from typing import Optional

from pacvo.evm.state import EVMState
from pacvo.l2.anchor import L2Anchor, compute_l2_state_root
from pacvo.l2.token import (
    SLOT_DECIMALS,
    SLOT_MINTER,
    SLOT_NAME,
    SLOT_SYMBOL,
    SLOT_TOTAL_SUPPLY,
    get_allowance_slot,
    get_balance_slot,
)


class L2State:
    """High-level L2 State layer anchored to Pacvo EVM execution."""

    def __init__(self, evm_state: Optional[EVMState] = None):
        self.evm_state = evm_state if evm_state is not None else EVMState()
        self.anchors: list[L2Anchor] = []

    def get_token_metadata(self, token_address: str) -> dict:
        """Read ERC-20 metadata directly from contract storage."""
        addr = token_address.lower()
        if not self.evm_state.account_exists(addr):
            return {"exists": False}

        raw_name = self.evm_state.get_storage(addr, SLOT_NAME)
        raw_symbol = self.evm_state.get_storage(addr, SLOT_SYMBOL)
        decimals = self.evm_state.get_storage(addr, SLOT_DECIMALS)
        total_supply = self.evm_state.get_storage(addr, SLOT_TOTAL_SUPPLY)
        minter_int = self.evm_state.get_storage(addr, SLOT_MINTER)

        name_str = (
            raw_name.to_bytes(32, "big").rstrip(b"\x00").decode("utf-8", errors="ignore")
            if raw_name > 0
            else ""
        )
        symbol_str = (
            raw_symbol.to_bytes(32, "big").rstrip(b"\x00").decode("utf-8", errors="ignore")
            if raw_symbol > 0
            else ""
        )
        minter_addr = (
            "0x" + format(minter_int, "040x") if minter_int != 0 else None
        )

        return {
            "exists": True,
            "address": addr,
            "name": name_str,
            "symbol": symbol_str,
            "decimals": decimals,
            "total_supply": total_supply,
            "minter": minter_addr,
        }

    def get_token_balance(self, token_address: str, owner_address: str) -> int:
        """Fetch token balance from EVM state mapping slot."""
        addr = token_address.lower()
        slot = get_balance_slot(owner_address)
        return self.evm_state.get_storage(addr, slot)

    def get_token_allowance(self, token_address: str, owner_address: str, spender_address: str) -> int:
        """Fetch token allowance from EVM state mapping slot."""
        addr = token_address.lower()
        slot = get_allowance_slot(owner_address, spender_address)
        return self.evm_state.get_storage(addr, slot)

    # --- ERC-721 NFT Queries ---

    def get_nft_metadata(self, nft_address: str) -> dict:
        """Read ERC-721 collection metadata directly from contract storage."""
        from pacvo.l2.nft import SLOT_NFT_MINTER, SLOT_NFT_NAME, SLOT_NFT_SYMBOL, SLOT_NFT_TOTAL_SUPPLY
        addr = nft_address.lower()
        if not self.evm_state.account_exists(addr):
            return {"exists": False}

        raw_name = self.evm_state.get_storage(addr, SLOT_NFT_NAME)
        raw_symbol = self.evm_state.get_storage(addr, SLOT_NFT_SYMBOL)
        total_supply = self.evm_state.get_storage(addr, SLOT_NFT_TOTAL_SUPPLY)
        minter_int = self.evm_state.get_storage(addr, SLOT_NFT_MINTER)

        name_str = (
            raw_name.to_bytes(32, "big").rstrip(b"\x00").decode("utf-8", errors="ignore")
            if raw_name > 0
            else ""
        )
        symbol_str = (
            raw_symbol.to_bytes(32, "big").rstrip(b"\x00").decode("utf-8", errors="ignore")
            if raw_symbol > 0
            else ""
        )
        minter_addr = (
            "0x" + format(minter_int, "040x") if minter_int != 0 else None
        )

        return {
            "exists": True,
            "address": addr,
            "name": name_str,
            "symbol": symbol_str,
            "total_supply": total_supply,
            "minter": minter_addr,
        }

    def get_nft_owner(self, nft_address: str, token_id: int) -> Optional[str]:
        """Fetch token owner address from ERC-721 _owners mapping slot."""
        from pacvo.crypto import keccak256
        from pacvo.l2.nft import SLOT_NFT_OWNERS
        addr = nft_address.lower()
        slot_bytes = keccak256(token_id.to_bytes(32, "big") + SLOT_NFT_OWNERS.to_bytes(32, "big"))
        slot = int.from_bytes(slot_bytes, "big")
        owner_int = self.evm_state.get_storage(addr, slot)
        return "0x" + format(owner_int, "040x") if owner_int != 0 else None

    def get_nft_balance(self, nft_address: str, owner_address: str) -> int:
        """Fetch token count from ERC-721 _balances mapping slot."""
        from pacvo.crypto import keccak256
        from pacvo.l2.nft import SLOT_NFT_BALANCES
        addr = nft_address.lower()
        owner_clean = owner_address.removeprefix("0x").lower().rjust(40, "0")
        slot_bytes = keccak256(bytes.fromhex(owner_clean).rjust(32, b"\x00") + SLOT_NFT_BALANCES.to_bytes(32, "big"))
        slot = int.from_bytes(slot_bytes, "big")
        return self.evm_state.get_storage(addr, slot)

    def get_nft_approval(self, nft_address: str, token_id: int) -> Optional[str]:
        """Fetch approved address for a token ID from _tokenApprovals mapping slot."""
        from pacvo.crypto import keccak256
        from pacvo.l2.nft import SLOT_NFT_APPROVALS
        addr = nft_address.lower()
        slot_bytes = keccak256(token_id.to_bytes(32, "big") + SLOT_NFT_APPROVALS.to_bytes(32, "big"))
        slot = int.from_bytes(slot_bytes, "big")
        appr_int = self.evm_state.get_storage(addr, slot)
        return "0x" + format(appr_int, "040x") if appr_int != 0 else None

    def create_anchor(self, l1_height: int, l1_block_hash: str) -> L2Anchor:
        """Generate and append an L2 state anchor linked to the current L1 block."""
        seq = len(self.anchors)
        root = compute_l2_state_root(self.evm_state)
        anchor = L2Anchor(
            l1_height=l1_height,
            l1_block_hash=l1_block_hash,
            l2_sequence=seq,
            state_root=root,
            timestamp=int(time.time()),
        )
        self.anchors.append(anchor)
        return anchor

    def get_latest_anchor(self) -> Optional[L2Anchor]:
        return self.anchors[-1] if self.anchors else None

    def copy(self) -> "L2State":
        new_state = L2State(self.evm_state.copy())
        new_state.anchors = list(self.anchors)
        return new_state
