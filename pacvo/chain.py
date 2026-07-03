import json
import logging
import os
import tempfile
import time

from pacvo.block import Block
from pacvo.crypto import canonical_json, is_valid_address
from pacvo.params import (
    BLOCK_REWARD,
    COINBASE_MATURITY,
    GENESIS_TIMESTAMP,
    INITIAL_TARGET,
    MAX_BLOCK_BYTES,
    MAX_BLOCK_TXS,
    MAX_FUTURE_TIMESTAMP,
    MAX_REORG_DEPTH,
    MAX_TARGET,
    MIN_FEE,
    MTP_WINDOW,
    RETARGET_INTERVAL,
    STAKE_LOCK_BLOCKS,
    TARGET_BLOCK_TIME,
    stake_split,
)
from pacvo.transaction import Transaction

logger = logging.getLogger("pacvo.chain")


def median_time_past(blocks: list[Block]) -> int:
    window = blocks[-min(MTP_WINDOW, len(blocks)) :]
    timestamps = sorted(block.timestamp for block in window)
    mid = len(timestamps) // 2
    if len(timestamps) % 2 == 1:
        return timestamps[mid]
    return (timestamps[mid - 1] + timestamps[mid]) // 2


class State:
    def __init__(self) -> None:
        self.balances: dict[str, int] = {}
        self.nonces: dict[str, int] = {}
        self.stakes: dict[str, list[dict]] = {}
        self.locked: dict[str, list[dict]] = {}

    def spendable(self, address: str) -> int:
        return self.balances.get(address, 0)

    def staked(self, address: str) -> int:
        return sum(entry["amount"] for entry in self.stakes.get(address, []))

    def immature(self, address: str) -> int:
        return sum(entry["amount"] for entry in self.locked.get(address, []))

    def next_nonce(self, address: str) -> int:
        return self.nonces.get(address, 0)

    def copy(self) -> "State":
        other = State()
        other.balances = dict(self.balances)
        other.nonces = dict(self.nonces)
        other.stakes = {k: [dict(e) for e in v] for k, v in self.stakes.items()}
        other.locked = {k: [dict(e) for e in v] for k, v in self.locked.items()}
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

    def validate_transaction(
        self, tx: Transaction, state: State, sig_ok: bool = False
    ) -> tuple[bool, str]:
        if tx.is_coinbase:
            return False, "coinbase cannot be validated as regular transaction"
        if not sig_ok and not tx.verify_signature():
            return False, "invalid signature"
        if not is_valid_address(tx.recipient):
            return False, "invalid recipient address"
        if tx.amount <= 0:
            return False, "amount must be positive"
        if tx.fee < MIN_FEE:
            return False, "fee below minimum"
        if tx.stake_amount != 0:
            return False, "stake_amount must be zero"
        if tx.nonce != state.next_nonce(tx.sender):
            return False, "invalid nonce"
        if state.spendable(tx.sender) < tx.amount + tx.fee:
            return False, "insufficient balance"
        return True, ""

    def _validate_timestamp(self, timestamp: int, prior_blocks: list[Block]) -> tuple[bool, str]:
        mtp = median_time_past(prior_blocks)
        if timestamp <= mtp:
            return False, "timestamp not greater than median-time-past"
        now = int(time.time())
        if timestamp > now + MAX_FUTURE_TIMESTAMP:
            return False, "timestamp too far in future"
        return True, ""

    def header_matches_block(self, header: dict, block: Block) -> bool:
        return (
            header["height"] == block.height
            and header["prev_hash"] == block.prev_hash
            and header["merkle_root"] == block.merkle_root
            and header["timestamp"] == block.timestamp
            and int(header["target"], 16) == block.target
            and header["nonce"] == block.nonce
        )

    def find_fork_point(self, headers: list[dict]) -> int | None:
        fork_height: int | None = None
        for header in headers:
            height = header["height"]
            if height >= len(self.blocks):
                continue
            local_block = self.blocks[height]
            if self.header_matches_block(header, local_block):
                fork_height = height
            else:
                break
        return fork_height

    def validate_header(self, header: dict, prior_blocks: list[Block]) -> tuple[bool, str]:
        prev = prior_blocks[-1]
        height = header["height"]
        if height != prev.height + 1:
            return False, "invalid height"
        if header["prev_hash"] != prev.block_hash:
            return False, "invalid prev_hash"

        temp = Blockchain()
        temp.blocks = list(prior_blocks)
        expected_target = temp.next_target()
        if int(header["target"], 16) != expected_target:
            return False, "invalid target"

        header_block = Block(
            height,
            header["prev_hash"],
            header["merkle_root"],
            header["timestamp"],
            int(header["target"], 16),
            header["nonce"],
            [],
        )
        if not header_block.meets_target():
            return False, "insufficient proof of work"

        ok, reason = self._validate_timestamp(header["timestamp"], prior_blocks)
        if not ok:
            return False, reason
        return True, ""

    def validate_header_chain(self, headers: list[dict], fork_height: int) -> tuple[bool, str]:
        new_headers = [h for h in headers if h["height"] > fork_height]
        if not new_headers:
            return False, "no new headers"

        prior_blocks = list(self.blocks[: fork_height + 1])
        for header in new_headers:
            if header["height"] != prior_blocks[-1].height + 1:
                return False, "non-consecutive heights"
            ok, reason = self.validate_header(header, prior_blocks)
            if not ok:
                return False, reason
            prior_blocks.append(
                Block(
                    header["height"],
                    header["prev_hash"],
                    header["merkle_root"],
                    header["timestamp"],
                    int(header["target"], 16),
                    header["nonce"],
                    [],
                )
            )
        return True, ""

    def cumulative_work_for_headers(self, headers: list[dict], fork_height: int) -> int:
        local_work = sum(block.work for block in self.blocks[: fork_height + 1])
        peer_work = 0
        for header in headers:
            if header["height"] <= fork_height:
                continue
            target = int(header["target"], 16)
            peer_work += (2**512) // (target + 1)
        return local_work + peer_work

    def _rebuild_state(self, upto_height: int) -> State:
        state = State()
        for block in self.blocks[: upto_height + 1]:
            if block.height == 0:
                continue
            self._release_matured(state, block.height)
            for tx in block.transactions[1:]:
                self._apply_non_coinbase_tx(state, tx)
            if block.transactions and block.transactions[0].is_coinbase:
                self._apply_coinbase(state, block.transactions[0], block.height)
        return state

    def execute_reorg(self, fork_height: int, new_blocks: list[Block]) -> tuple[bool, str]:
        if self.height - fork_height > MAX_REORG_DEPTH:
            logger.warning(
                "rejecting reorg: depth %s exceeds MAX_REORG_DEPTH %s",
                self.height - fork_height,
                MAX_REORG_DEPTH,
            )
            return False, "reorg depth exceeds maximum"

        working_blocks = list(self.blocks[: fork_height + 1])
        working_state = self._rebuild_state(fork_height)

        temp = Blockchain()
        temp.blocks = working_blocks
        temp.state = working_state

        for block in new_blocks:
            ok, reason = temp.validate_block(block)
            if not ok:
                return False, reason

        if temp.cumulative_work() <= self.cumulative_work():
            return False, "insufficient work"

        self.blocks = temp.blocks
        self.state = temp.state.copy()
        if self.data_file:
            self.save()
        return True, ""

    def validate_block_signatures(self, block: Block) -> tuple[bool, str]:
        for tx in block.transactions:
            if tx.is_coinbase:
                if tx.signature != b"":
                    return False, "invalid coinbase signature"
            elif not tx.verify_signature():
                return False, "invalid signature"
        return True, ""

    def validate_block(self, block: Block, sigs_ok: bool = False) -> tuple[bool, str]:
        if block.height != self.height + 1:
            return False, "invalid height"
        if block.prev_hash != self.blocks[-1].block_hash:
            return False, "invalid prev_hash"
        if block.target != self.next_target():
            return False, "invalid target"
        if not block.meets_target():
            return False, "insufficient proof of work"

        ok, reason = self._validate_timestamp(block.timestamp, self.blocks)
        if not ok:
            return False, reason

        if len(block.transactions) > MAX_BLOCK_TXS:
            return False, "too many transactions"
        if len(canonical_json(block.to_dict())) > MAX_BLOCK_BYTES:
            return False, "block too large"

        txids = [tx.txid for tx in block.transactions]
        if block.merkle_root != Block.compute_merkle_root(txids):
            return False, "invalid merkle root"
        if len(block.transactions) < 1:
            return False, "block must contain coinbase"
        if not block.transactions[0].is_coinbase:
            return False, "first transaction must be coinbase"
        coinbase = block.transactions[0]
        if not is_valid_address(coinbase.recipient):
            return False, "invalid coinbase recipient address"
        if not sigs_ok:
            ok, reason = self.validate_block_signatures(block)
            if not ok:
                return False, reason
        for tx in block.transactions[1:]:
            if tx.is_coinbase:
                return False, "multiple coinbase transactions"
        spendable_reward, stake = stake_split(BLOCK_REWARD)
        fees = sum(tx.fee for tx in block.transactions[1:])
        if coinbase.amount != spendable_reward + fees:
            return False, "invalid coinbase amount"
        if coinbase.stake_amount != stake:
            return False, "invalid coinbase stake amount"
        if coinbase.nonce != block.height:
            return False, "invalid coinbase nonce"
        working = self.state.copy()
        self._release_matured(working, block.height)
        for tx in block.transactions[1:]:
            ok, reason = self.validate_transaction(tx, working, sig_ok=sigs_ok)
            if not ok:
                return False, reason
            self._apply_non_coinbase_tx(working, tx)
        return True, ""

    def _release_matured(self, state: State, height: int) -> None:
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
        for address in list(state.locked):
            remaining = []
            for entry in state.locked[address]:
                if entry["unlock_height"] <= height:
                    state.balances[address] = state.balances.get(address, 0) + entry["amount"]
                else:
                    remaining.append(entry)
            if remaining:
                state.locked[address] = remaining
            else:
                del state.locked[address]

    def _release_matured_stakes(self, state: State, height: int) -> None:
        self._release_matured(state, height)

    def _apply_non_coinbase_tx(self, state: State, tx: Transaction) -> None:
        sender = tx.sender
        state.balances[sender] = state.balances.get(sender, 0) - tx.amount - tx.fee
        state.balances[tx.recipient] = state.balances.get(tx.recipient, 0) + tx.amount
        state.nonces[sender] = state.nonces.get(sender, 0) + 1

    def _apply_coinbase(self, state: State, tx: Transaction, height: int) -> None:
        miner = tx.recipient
        if tx.amount > 0:
            state.locked.setdefault(miner, []).append(
                {"amount": tx.amount, "unlock_height": height + COINBASE_MATURITY}
            )
        if tx.stake_amount > 0:
            state.stakes.setdefault(miner, []).append(
                {"amount": tx.stake_amount, "unlock_height": height + STAKE_LOCK_BLOCKS}
            )

    def _apply_block_state(self, block: Block) -> None:
        self._release_matured(self.state, block.height)
        for tx in block.transactions:
            if tx.is_coinbase:
                continue
            self._apply_non_coinbase_tx(self.state, tx)
        if block.transactions and block.transactions[0].is_coinbase:
            self._apply_coinbase(self.state, block.transactions[0], block.height)

    def add_block(self, block: Block, sigs_ok: bool = False) -> tuple[bool, str]:
        ok, reason = self.validate_block(block, sigs_ok=sigs_ok)
        if not ok:
            return False, reason
        self._apply_block_state(block)
        self.blocks.append(block)
        if self.data_file:
            self.save()
        return True, ""

    def cumulative_work(self) -> int:
        return sum(block.work for block in self.blocks)

    def save(self) -> None:
        if not self.data_file:
            return
        parent = os.path.dirname(self.data_file) or "."
        os.makedirs(parent, exist_ok=True)
        data = {"blocks": [block.to_dict() for block in self.blocks]}
        fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, self.data_file)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.blocks = [Block.from_dict(b) for b in data["blocks"]]
        self.state = State()
        for block in self.blocks:
            if block.height == 0:
                continue
            self._apply_block_state(block)
