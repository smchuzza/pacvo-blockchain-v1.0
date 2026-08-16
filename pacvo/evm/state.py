"""Journaled EVM World State and Account Storage."""

from dataclasses import dataclass, field
import json


@dataclass
class Account:
    nonce: int = 0
    balance: int = 0
    code: bytes = b""
    storage: dict[int, int] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self.nonce == 0 and self.balance == 0 and len(self.code) == 0

    def copy(self) -> "Account":
        return Account(
            nonce=self.nonce,
            balance=self.balance,
            code=bytes(self.code),
            storage=dict(self.storage),
        )

    def to_dict(self) -> dict:
        return {
            "nonce": self.nonce,
            "balance": self.balance,
            "code": self.code.hex(),
            "storage": {format(k, "x"): format(v, "x") for k, v in self.storage.items() if v != 0},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        storage = {int(k, 16): int(v, 16) for k, v in d.get("storage", {}).items() if int(v, 16) != 0}
        return cls(
            nonce=d.get("nonce", 0),
            balance=d.get("balance", 0),
            code=bytes.fromhex(d.get("code", "")),
            storage=storage,
        )


class EVMState:
    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}
        self._journal: list[list[tuple]] = []
        self._selfdestructs: set[str] = set()

    def _normalize_address(self, addr: str) -> str:
        if not isinstance(addr, str):
            raise TypeError("Address must be a string")
        addr = addr.lower()
        if not addr.startswith("0x"):
            addr = "0x" + addr
        return addr

    def account_exists(self, address: str) -> bool:
        addr = self._normalize_address(address)
        return addr in self.accounts

    def get_account(self, address: str) -> Account:
        addr = self._normalize_address(address)
        if addr not in self.accounts:
            self.accounts[addr] = Account()
            if self._journal:
                self._journal[-1].append(("create_account", addr, False, None))
        return self.accounts[addr]

    def checkpoint(self) -> int:
        self._journal.append([])
        return len(self._journal) - 1

    def commit(self, checkpoint_id: int) -> None:
        while len(self._journal) > checkpoint_id:
            frame = self._journal.pop()
            if self._journal:
                self._journal[-1].extend(frame)

    def rollback(self, checkpoint_id: int) -> None:
        while len(self._journal) > checkpoint_id:
            frame = self._journal.pop()
            # Apply changes in reverse order
            for op in reversed(frame):
                op_type = op[0]
                if op_type == "create_account":
                    addr, existed, _ = op[1], op[2], op[3]
                    if not existed and addr in self.accounts:
                        del self.accounts[addr]
                elif op_type == "balance":
                    addr, old_val, _ = op[1], op[2], op[3]
                    if addr in self.accounts:
                        self.accounts[addr].balance = old_val
                elif op_type == "nonce":
                    addr, old_val, _ = op[1], op[2], op[3]
                    if addr in self.accounts:
                        self.accounts[addr].nonce = old_val
                elif op_type == "code":
                    addr, old_val, _ = op[1], op[2], op[3]
                    if addr in self.accounts:
                        self.accounts[addr].code = old_val
                elif op_type == "storage":
                    addr, slot, old_val, _ = op[1], op[2], op[3], op[4]
                    if addr in self.accounts:
                        if old_val == 0:
                            self.accounts[addr].storage.pop(slot, None)
                        else:
                            self.accounts[addr].storage[slot] = old_val
                elif op_type == "selfdestruct":
                    addr, was_in, _ = op[1], op[2], op[3]
                    if not was_in:
                        self._selfdestructs.discard(addr)

    def get_balance(self, address: str) -> int:
        addr = self._normalize_address(address)
        if addr not in self.accounts:
            return 0
        return self.accounts[addr].balance

    def set_balance(self, address: str, balance: int) -> None:
        addr = self._normalize_address(address)
        acct = self.get_account(addr)
        if self._journal:
            self._journal[-1].append(("balance", addr, acct.balance, balance))
        acct.balance = balance

    def add_balance(self, address: str, amount: int) -> None:
        if amount == 0:
            return
        self.set_balance(address, self.get_balance(address) + amount)

    def sub_balance(self, address: str, amount: int) -> None:
        if amount == 0:
            return
        current = self.get_balance(address)
        if current < amount:
            raise ValueError("Insufficient EVM balance")
        self.set_balance(address, current - amount)

    def get_nonce(self, address: str) -> int:
        addr = self._normalize_address(address)
        if addr not in self.accounts:
            return 0
        return self.accounts[addr].nonce

    def set_nonce(self, address: str, nonce: int) -> None:
        addr = self._normalize_address(address)
        acct = self.get_account(addr)
        if self._journal:
            self._journal[-1].append(("nonce", addr, acct.nonce, nonce))
        acct.nonce = nonce

    def increment_nonce(self, address: str) -> None:
        self.set_nonce(address, self.get_nonce(address) + 1)

    def get_code(self, address: str) -> bytes:
        addr = self._normalize_address(address)
        if addr not in self.accounts:
            return b""
        return self.accounts[addr].code

    def set_code(self, address: str, code: bytes) -> None:
        addr = self._normalize_address(address)
        acct = self.get_account(addr)
        if self._journal:
            self._journal[-1].append(("code", addr, acct.code, bytes(code)))
        acct.code = bytes(code)

    def get_storage(self, address: str, slot: int) -> int:
        addr = self._normalize_address(address)
        if addr not in self.accounts:
            return 0
        return self.accounts[addr].storage.get(slot, 0)

    def set_storage(self, address: str, slot: int, value: int) -> None:
        addr = self._normalize_address(address)
        acct = self.get_account(addr)
        old_val = acct.storage.get(slot, 0)
        if self._journal:
            self._journal[-1].append(("storage", addr, slot, old_val, value))
        if value == 0:
            acct.storage.pop(slot, None)
        else:
            acct.storage[slot] = value

    def selfdestruct(self, address: str, recipient: str) -> None:
        addr = self._normalize_address(address)
        rec = self._normalize_address(recipient)
        bal = self.get_balance(addr)
        if bal > 0:
            self.sub_balance(addr, bal)
            self.add_balance(rec, bal)
        if self._journal:
            self._journal[-1].append(("selfdestruct", addr, addr in self._selfdestructs, None))
        self._selfdestructs.add(addr)

    def finalize_block(self) -> None:
        for addr in self._selfdestructs:
            if addr in self.accounts:
                del self.accounts[addr]
        self._selfdestructs.clear()

    def copy(self) -> "EVMState":
        other = EVMState()
        other.accounts = {k: v.copy() for k, v in self.accounts.items()}
        other._selfdestructs = set(self._selfdestructs)
        return other

    def to_dict(self) -> dict:
        return {
            "accounts": {k: v.to_dict() for k, v in self.accounts.items() if not (v.is_empty() and not v.storage)},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EVMState":
        state = cls()
        for k, v in d.get("accounts", {}).items():
            state.accounts[k.lower()] = Account.from_dict(v)
        return state
