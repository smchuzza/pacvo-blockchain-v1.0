import json
import os
import time

from pacvo.block import Block
from pacvo.params import (
    BLOCK_REWARD,
    GENESIS_TIMESTAMP,
    INITIAL_TARGET,
    MAX_TARGET,
    RETARGET_INTERVAL,
    STAKE_LOCK_BLOCKS,
    TARGET_BLOCK_TIME,
    stake_split,
)
from pacvo.transaction import Transaction


class State:
    def __init__(self) -> None:
        self.balances: dict[str, int] = {}
        self.nonces: dict[str, int] = {}
        self.stakes: dict[str, list[dict]] = {}

    def spendable(self, address: str) -> int:
        return self.balances.get(address, 0)

    def staked(self, address: str) -> int:
        return sum(entry["amount"] for entry in self.stakes.get(address, []))

    def next_nonce(self, address: str) -> int:
        return self.nonces.get(address, 0)

    def copy(self) -> "State":
        other = State()
        other.balances = dict(self.balances)
        other.nonces = dict(self.nonces)
        other.stakes = {k: [dict(e) for e in v] for k, v in self.stakes.items()}
        return other


class Blockchain:
    def __init__(self, data_file: str | None = None) -> None:
        self.data_file = data_file
        self.blocks: list[Block] = []
        self.state = State()
        if data_file and os.path.exists(data_file):
            self._load(data_file)
        else:
            self.blocks = [self.create_genesis()]
            self.state = State()

    @property
    def height(self) -> int:
        return self.blocks[-1].height

    @staticmethod
    def create_genesis() -> Block:
        return Block(
            0,
            "0" * 128,
            Block.compute_merkle_root([]),
            GENESIS_TIMESTAMP,
            INITIAL_TARGET,
            0,
            [],
        )

    def next_target(self) -> int:
        h = self.height + 1
        if h % RETARGET_INTERVAL != 0 or h == 0:
            return self.blocks[-1].target
        tip = self.blocks[-1]
        prev = self.blocks[h - RETARGET_INTERVAL]
        elapsed = max(1, tip.timestamp - prev.timestamp)
        expected = TARGET_BLOCK_TIME * RETARGET_INTERVAL
        new_target = tip.target * elapsed // expected
        lower = tip.target // 4
        upper = tip.target * 4
        new_target = max(lower, min(upper, new_target))
        new_target = min(new_target, MAX_TARGET)
        return max(1, new_target)

    def validate_transaction(self, tx: Transaction, state: State) -> tuple[bool, str]:
        if tx.is_coinbase:
            return False, "coinbase cannot be validated as regular transaction"
        if not tx.verify_signature():
            return False, "invalid signature"
        if tx.amount <= 0:
            return False, "amount must be positive"
        if tx.fee < 0:
            return False, "fee must be non-negative"
        if tx.stake_amount != 0:
            return False, "stake_amount must be zero"
        if tx.nonce != state.next_nonce(tx.sender):
            return False, "invalid nonce"
        if state.spendable(tx.sender) < tx.amount + tx.fee:
            return False, "insufficient balance"
        return True, ""

    def validate_block(self, block: Block) -> tuple[bool, str]:
        if block.height != self.height + 1:
            return False, "invalid height"
        if block.prev_hash != self.blocks[-1].block_hash:
            return False, "invalid prev_hash"
        if block.target != self.next_target():
            return False, "invalid target"
        if not block.meets_target():
            return False, "insufficient proof of work"
        tip = self.blocks[-1]
        now = int(time.time())
        if block.timestamp < tip.timestamp:
            return False, "timestamp too early"
        if block.timestamp > now + 7200:
            return False, "timestamp too far in future"
        txids = [tx.txid for tx in block.transactions]
        if block.merkle_root != Block.compute_merkle_root(txids):
            return False, "invalid merkle root"
        if len(block.transactions) < 1:
            return False, "block must contain coinbase"
        if not block.transactions[0].is_coinbase:
            return False, "first transaction must be coinbase"
        for tx in block.transactions[1:]:
            if tx.is_coinbase:
                return False, "multiple coinbase transactions"
        spendable_reward, stake = stake_split(BLOCK_REWARD)
        fees = sum(tx.fee for tx in block.transactions[1:])
        coinbase = block.transactions[0]
        if coinbase.amount != spendable_reward + fees:
            return False, "invalid coinbase amount"
        if coinbase.stake_amount != stake:
            return False, "invalid coinbase stake amount"
        if coinbase.nonce != block.height:
            return False, "invalid coinbase nonce"
        working = self.state.copy()
        self._release_matured_stakes(working, block.height)
        for tx in block.transactions[1:]:
            ok, reason = self.validate_transaction(tx, working)
            if not ok:
                return False, reason
            self._apply_non_coinbase_tx(working, tx)
        return True, ""

    def _release_matured_stakes(self, state: State, height: int) -> None:
        for address in list(state.stakes):
            remaining = []
            for entry in state.stakes[address]:
                if entry["unlock_height"] <= height:
                    state.balances[address] = state.balances.get(address, 0) + entry["amount"]
                else:
                    remaining.append(entry)
            if remaining:
                state.stakes[address] = remaining
            else:
                del state.stakes[address]

    def _apply_non_coinbase_tx(self, state: State, tx: Transaction) -> None:
        sender = tx.sender
        state.balances[sender] = state.balances.get(sender, 0) - tx.amount - tx.fee
        state.balances[tx.recipient] = state.balances.get(tx.recipient, 0) + tx.amount
        state.nonces[sender] = state.nonces.get(sender, 0) + 1

    def _apply_coinbase(self, state: State, tx: Transaction, height: int) -> None:
        miner = tx.recipient
        state.balances[miner] = state.balances.get(miner, 0) + tx.amount
        if tx.stake_amount > 0:
            state.stakes.setdefault(miner, []).append(
                {"amount": tx.stake_amount, "unlock_height": height + STAKE_LOCK_BLOCKS}
            )

    def _apply_block_state(self, block: Block) -> None:
        self._release_matured_stakes(self.state, block.height)
        for tx in block.transactions:
            if tx.is_coinbase:
                continue
            self._apply_non_coinbase_tx(self.state, tx)
        if block.transactions and block.transactions[0].is_coinbase:
            self._apply_coinbase(self.state, block.transactions[0], block.height)

    def add_block(self, block: Block) -> tuple[bool, str]:
        ok, reason = self.validate_block(block)
        if not ok:
            return False, reason
        self._apply_block_state(block)
        self.blocks.append(block)
        if self.data_file:
            self.save()
        return True, ""

    def cumulative_work(self) -> int:
        return sum(block.work for block in self.blocks)

    def replace_if_better(self, block_dicts: list[dict]) -> bool:
        candidate = Blockchain()
        genesis = Block.from_dict(block_dicts[0])
        if genesis.block_hash != self.blocks[0].block_hash:
            return False
        candidate.blocks = [genesis]
        candidate.state = State()
        for block_dict in block_dicts[1:]:
            block = Block.from_dict(block_dict)
            ok, _ = candidate.add_block(block)
            if not ok:
                return False
        if candidate.cumulative_work() <= self.cumulative_work():
            return False
        self.blocks = candidate.blocks
        self.state = candidate.state.copy()
        if self.data_file:
            self.save()
        return True

    def save(self) -> None:
        if not self.data_file:
            return
        parent = os.path.dirname(self.data_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = {"blocks": [block.to_dict() for block in self.blocks]}
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.blocks = [Block.from_dict(b) for b in data["blocks"]]
        self.state = State()
        for block in self.blocks:
            if block.height == 0:
                continue
            self._apply_block_state(block)
