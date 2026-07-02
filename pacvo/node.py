import asyncio
import os

from pacvo.block import Block
from pacvo.chain import Blockchain
from pacvo.network import P2PNode
from pacvo.transaction import Transaction


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
        self.wallet = wallet
        self.peers = peers
        self.mine = mine
        self.chain = Blockchain(data_file=os.path.join(data_dir, "chain.json"))
        self.mempool: dict[str, Transaction] = {}
        self.p2p = P2PNode(host, port, self)

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

    def handle_new_tx(self, tx_dict: dict, origin=None) -> tuple[bool, str]:
        tx = Transaction.from_dict(tx_dict)
        if tx.txid in self.mempool:
            return True, "known"
        state = self._simulated_state()
        ok, err = self.chain.validate_transaction(tx, state)
        if not ok:
            return False, err
        self.mempool[tx.txid] = tx
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

    def get_balance(self, address: str) -> dict:
        return {
            "address": address,
            "spendable": self.chain.state.spendable(address),
            "staked": self.chain.state.staked(address),
            "stake_entries": self.chain.state.stakes.get(address, []),
            "next_nonce": self.chain.state.next_nonce(address),
            "height": self.chain.height,
        }

    def get_chain_dicts(self) -> list[dict]:
        return [b.to_dict() for b in self.chain.blocks]
