import asyncio
import json
import logging
import os

from pacvo.block import Block
from pacvo.chain import Blockchain
from pacvo.crypto import generate_sign_keypair, identity_fingerprint
from pacvo.network import P2PNode
from pacvo.params import MAX_BLOCK_BATCH, MAX_MEMPOOL_TXS, MAX_REORG_DEPTH
from pacvo.transaction import Transaction

logger = logging.getLogger("pacvo.node")


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

    def _load_or_create_identity(self, data_dir: str) -> tuple[bytes, bytes]:
        path = os.path.join(data_dir, "identity.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return bytes.fromhex(data["sign_public_key"]), bytes.fromhex(
                data["sign_secret_key"]
            )
        public_key, secret_key = generate_sign_keypair()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "sign_public_key": public_key.hex(),
                    "sign_secret_key": secret_key.hex(),
                },
                f,
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
        with open(self._known_peers_path(), "w", encoding="utf-8") as f:
            json.dump(self.known_peers, f)

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

    def _simulated_state(self) -> object:
        state = self.chain.state.copy()
        self.chain._release_matured_stakes(state, self.chain.height + 1)
        for tx in self.mempool.values():
            self.chain._apply_non_coinbase_tx(state, tx)
        return state

    def _admit_mempool_tx(self, tx: Transaction) -> tuple[bool, str]:
        if len(self.mempool) < MAX_MEMPOOL_TXS:
            self.mempool[tx.txid] = tx
            return True, ""
        lowest_txid = min(self.mempool, key=lambda tid: self.mempool[tid].fee)
        if tx.fee <= self.mempool[lowest_txid].fee:
            return False, "mempool full"
        del self.mempool[lowest_txid]
        self.mempool[tx.txid] = tx
        return True, ""

    def handle_new_tx(self, tx_dict: dict, origin=None) -> tuple[bool, str]:
        tx = Transaction.from_dict(tx_dict)
        if tx.txid in self.mempool:
            return True, "known"
        state = self._simulated_state()
        ok, err = self.chain.validate_transaction(tx, state)
        if not ok:
            return False, err
        ok, err = self._admit_mempool_tx(tx)
        if not ok:
            return False, err
        asyncio.create_task(
            self.p2p.broadcast("new_tx", {"tx": tx_dict}, exclude=origin)
        )
        return True, ""

    def handle_new_block(self, block_dict: dict, origin=None) -> tuple[bool, str]:
        block = Block.from_dict(block_dict)
        if block.block_hash == self.chain.blocks[-1].block_hash:
            return True, "known"
        ok, err = self.chain.add_block(block)
        if ok:
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
            # headers response handled asynchronously via handle_headers

    async def _fetch_and_reorg(self, peer, fork_height: int, tip_height: int) -> None:
        from_height = fork_height + 1
        collected: list[Block] = []
        while from_height <= tip_height:
            count = min(MAX_BLOCK_BATCH, tip_height - from_height + 1)
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._pending_blocks = future
            await peer.send("get_blocks", {"from_height": from_height, "count": count})
            try:
                block_dicts = await asyncio.wait_for(future, timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("timed out waiting for blocks from %s", peer.remote_label)
                return
            finally:
                self._pending_blocks = None
            if not block_dicts:
                logger.warning("empty block batch from %s", peer.remote_label)
                return
            for block_dict in block_dicts:
                collected.append(Block.from_dict(block_dict))
            from_height = collected[-1].height + 1

        ok, reason = self.chain.execute_reorg(fork_height, collected)
        if ok:
            logger.info(
                "reorged to height %s via peer %s",
                self.chain.height,
                peer.remote_label,
            )
        else:
            logger.warning("reorg failed: %s", reason)

    async def handle_blocks(self, blocks: list[dict], peer) -> None:
        if self._pending_blocks is not None and not self._pending_blocks.done():
            self._pending_blocks.set_result(blocks)
            return
        for block_dict in blocks:
            self.handle_new_block(block_dict, origin=peer)

    def get_balance(self, address: str) -> dict:
        return {
            "address": address,
            "spendable": self.chain.state.spendable(address),
            "staked": self.chain.state.staked(address),
            "stake_entries": self.chain.state.stakes.get(address, []),
            "next_nonce": self.chain.state.next_nonce(address),
            "height": self.chain.height,
        }

    def identity_fingerprint(self) -> str:
        return identity_fingerprint(self.identity_public_key)
