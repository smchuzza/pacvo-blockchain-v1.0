import asyncio
import json
import logging
import os
import tempfile

from pacvo.block import Block
from pacvo.chain import Blockchain, State
from pacvo.crypto import generate_sign_keypair, identity_fingerprint
from pacvo.network import P2PNode
from pacvo.params import MAX_BLOCK_BATCH, MAX_MEMPOOL_TXS, MAX_REORG_DEPTH
from pacvo.transaction import Transaction

logger = logging.getLogger("pacvo.node")


def _atomic_write_json(path: str, data: object) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class Node:
    def __init__(
        self,
        wallet,
        data_dir: str,
        host: str,
        port: int,
        peers: list[tuple[str, int]],
        mine: bool,
    ) -> None:
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.wallet = wallet
        self.peers = peers
        self.mine = mine
        self.identity_public_key, self.identity_secret_key = self._load_or_create_identity(
            data_dir
        )
        self.known_peers = self._load_known_peers(data_dir)
        self.chain = Blockchain(data_file=os.path.join(data_dir, "chain.json"))
        self.mempool: dict[str, Transaction] = {}
        self.p2p = P2PNode(host, port, self)
        self._sync_lock = asyncio.Lock()
        self._pending_blocks: asyncio.Future | None = None
        self._pending_blocks_peer = None
        self._sim_state: State | None = None
        self._sim_height: int | None = None

    def _load_or_create_identity(self, data_dir: str) -> tuple[bytes, bytes]:
        path = os.path.join(data_dir, "identity.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return bytes.fromhex(data["sign_public_key"]), bytes.fromhex(
                data["sign_secret_key"]
            )
        public_key, secret_key = generate_sign_keypair()
        _atomic_write_json(
            path,
            {
                "sign_public_key": public_key.hex(),
                "sign_secret_key": secret_key.hex(),
            },
        )
        return public_key, secret_key

    def _known_peers_path(self) -> str:
        return os.path.join(self.data_dir, "known_peers.json")

    def _load_known_peers(self, data_dir: str) -> dict[str, str]:
        path = os.path.join(data_dir, "known_peers.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _save_known_peers(self) -> None:
        _atomic_write_json(self._known_peers_path(), self.known_peers)

    def check_peer_pin(self, remote_label: str, fingerprint: str) -> None:
        known = self.known_peers.get(remote_label)
        if known is not None and known != fingerprint:
            logger.error(
                "TOFU PIN MISMATCH for %s: expected %s got %s — aborting connection",
                remote_label,
                known,
                fingerprint,
            )
            raise ValueError("peer identity fingerprint mismatch")

    def record_peer_pin(self, remote_label: str, fingerprint: str) -> None:
        if remote_label not in self.known_peers:
            self.known_peers[remote_label] = fingerprint
            self._save_known_peers()

    async def start(self) -> None:
        await self.p2p.start()
        for peer_host, peer_port in self.peers:
            await self.p2p.connect(peer_host, peer_port)
        if self.mine:
            from pacvo.miner import mine_loop

            asyncio.create_task(mine_loop(self))
        await asyncio.Event().wait()

    def _invalidate_sim_state(self) -> None:
        self._sim_state = None
        self._sim_height = None

    def _get_sim_state(self) -> State:
        if self._sim_state is None or self._sim_height != self.chain.height:
            state = self.chain.state.copy()
            self.chain._release_matured(state, self.chain.height + 1)
            for tx in self.mempool.values():
                self.chain._apply_non_coinbase_tx(state, tx)
            self._sim_state = state
            self._sim_height = self.chain.height
        return self._sim_state

    def _admit_mempool_tx(self, tx: Transaction) -> tuple[bool, str, bool]:
        if len(self.mempool) < MAX_MEMPOOL_TXS:
            self.mempool[tx.txid] = tx
            return True, "", False
        lowest_txid = min(self.mempool, key=lambda tid: self.mempool[tid].fee)
        if tx.fee <= self.mempool[lowest_txid].fee:
            return False, "mempool full", False
        del self.mempool[lowest_txid]
        self.mempool[tx.txid] = tx
        return True, "", True

    def handle_new_tx(
        self, tx_dict: dict, origin=None, sig_ok: bool = False
    ) -> tuple[bool, str]:
        tx = Transaction.from_dict(tx_dict)
        if tx.txid in self.mempool:
            return True, "known"
        sim = self._get_sim_state()
        ok, err = self.chain.validate_transaction(tx, sim, sig_ok=sig_ok)
        if not ok:
            return False, err
        ok, err, evicted = self._admit_mempool_tx(tx)
        if not ok:
            return False, err
        if evicted:
            self._invalidate_sim_state()
        else:
            self.chain._apply_non_coinbase_tx(sim, tx)
        asyncio.create_task(
            self.p2p.broadcast("new_tx", {"tx": tx_dict}, exclude=origin)
        )
        return True, ""

    def handle_new_block(
        self, block_dict: dict, origin=None, sigs_ok: bool = False
    ) -> tuple[bool, str]:
        block = Block.from_dict(block_dict)
        if block.block_hash == self.chain.blocks[-1].block_hash:
            return True, "known"
        ok, err = self.chain.add_block(block, sigs_ok=sigs_ok)
        if ok:
            self._invalidate_sim_state()
            block_txids = {tx.txid for tx in block.transactions}
            for txid in list(self.mempool):
                tx = self.mempool[txid]
                if txid in block_txids:
                    del self.mempool[txid]
                elif tx.nonce < self.chain.state.next_nonce(tx.sender):
                    del self.mempool[txid]
            asyncio.create_task(
                self.p2p.broadcast(
                    "new_block", {"block": block.to_dict()}, exclude=origin
                )
            )
        return ok, err

    def submit_block(self, block) -> None:
        self.handle_new_block(block.to_dict(), origin=None)

    async def handle_headers(self, headers: list[dict], peer) -> None:
        if not headers:
            return
        async with self._sync_lock:
            fork_height = self.chain.find_fork_point(headers)
            if fork_height is None:
                logger.warning("could not locate fork point in header chain")
                return
            if self.chain.height - fork_height > MAX_REORG_DEPTH:
                logger.warning(
                    "rejecting headers: reorg depth %s exceeds MAX_REORG_DEPTH %s",
                    self.chain.height - fork_height,
                    MAX_REORG_DEPTH,
                )
                return
            ok, reason = self.chain.validate_header_chain(headers, fork_height)
            if not ok:
                logger.warning("invalid header chain: %s", reason)
                return
            peer_work = self.chain.cumulative_work_for_headers(headers, fork_height)
            if peer_work <= self.chain.cumulative_work():
                return
            tip_height = headers[-1]["height"]
            await self._fetch_and_reorg(peer, fork_height, tip_height)

    async def sync_from_peer(self, peer) -> None:
        async with self._sync_lock:
            from_height = max(0, self.chain.height - MAX_REORG_DEPTH)
            await peer.send("get_headers", {"from_height": from_height})

    async def _fetch_and_reorg(self, peer, fork_height: int, tip_height: int) -> None:
        from_height = fork_height + 1
        collected: list[Block] = []
        loop = asyncio.get_running_loop()
        try:
            while from_height <= tip_height:
                count = min(MAX_BLOCK_BATCH, tip_height - from_height + 1)
                future: asyncio.Future = loop.create_future()
                self._pending_blocks = future
                self._pending_blocks_peer = peer
                await peer.send("get_blocks", {"from_height": from_height, "count": count})
                try:
                    block_dicts = await asyncio.wait_for(future, timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning("timed out waiting for blocks from %s", peer.remote_label)
                    return
                if not block_dicts:
                    logger.warning("empty block batch from %s", peer.remote_label)
                    return
                for block_dict in block_dicts:
                    block = Block.from_dict(block_dict)
                    ok, reason = await loop.run_in_executor(
                        None, self.chain.validate_block_signatures, block
                    )
                    if not ok:
                        logger.warning("invalid block signatures from %s: %s", peer.remote_label, reason)
                        return
                    collected.append(block)
                from_height = collected[-1].height + 1

            ok, reason = await loop.run_in_executor(
                None, self._execute_reorg_verified, fork_height, collected
            )
            if ok:
                self._invalidate_sim_state()
                logger.info(
                    "reorged to height %s via peer %s",
                    self.chain.height,
                    peer.remote_label,
                )
            else:
                logger.warning("reorg failed: %s", reason)
        finally:
            self._pending_blocks = None
            self._pending_blocks_peer = None

    def _execute_reorg_verified(
        self, fork_height: int, collected: list[Block]
    ) -> tuple[bool, str]:
        working_blocks = list(self.chain.blocks[: fork_height + 1])
        working_state = self.chain._rebuild_state(fork_height)

        temp = Blockchain()
        temp.blocks = working_blocks
        temp.state = working_state

        for block in collected:
            ok, reason = temp.validate_block(block, sigs_ok=True)
            if not ok:
                return False, reason
            temp._apply_block_state(block)
            temp.blocks.append(block)

        if temp.cumulative_work() <= self.chain.cumulative_work():
            return False, "insufficient work"

        if self.chain.height - fork_height > MAX_REORG_DEPTH:
            return False, "reorg depth exceeds maximum"

        self.chain.blocks = temp.blocks
        self.chain.state = temp.state.copy()
        if self.chain.data_file:
            self.chain.save()
        return True, ""

    async def handle_blocks(self, blocks: list[dict], peer) -> None:
        if (
            self._pending_blocks is not None
            and not self._pending_blocks.done()
            and peer is self._pending_blocks_peer
        ):
            self._pending_blocks.set_result(blocks)
            return

    def get_balance(self, address: str) -> dict:
        return {
            "address": address,
            "spendable": self.chain.state.spendable(address),
            "staked": self.chain.state.staked(address),
            "immature": self.chain.state.immature(address),
            "stake_entries": self.chain.state.stakes.get(address, []),
            "locked_entries": self.chain.state.locked.get(address, []),
            "next_nonce": self.chain.state.next_nonce(address),
            "height": self.chain.height,
        }

    def identity_fingerprint(self) -> str:
        return identity_fingerprint(self.identity_public_key)
