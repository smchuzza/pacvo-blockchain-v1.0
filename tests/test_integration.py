"""End-to-end integration test: two nodes, P2P sync, mining, and transfers."""

import asyncio
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.network import rpc_call
from pacvo.node import Node
from pacvo.params import BLOCK_REWARD, COIN, STAKE_LOCK_BLOCKS, stake_split
from pacvo.transaction import Transaction
from pacvo.wallet import Wallet

HOST = "127.0.0.1"
PORT_A = 19701
PORT_B = 19702
SYNC_TIMEOUT = 30.0
MINE_TIMEOUT = 30.0


async def wait_for_height(node: Node, target: int, timeout: float = SYNC_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while node.chain.height < target:
        if time.monotonic() > deadline:
            raise TimeoutError(f"height {target} not reached (stuck at {node.chain.height})")
        await asyncio.sleep(0.05)


async def run_integration() -> None:
    tmp = tempfile.mkdtemp(prefix="pacvo-integ-")
    node_a = node_b = None
    task_a = task_b = None
    try:
        data_a = os.path.join(tmp, "data_a")
        data_b = os.path.join(tmp, "data_b")
        wallet_a = Wallet.generate()
        wallet_b = Wallet.generate()

        node_a = Node(wallet_a, data_a, HOST, PORT_A, peers=[], mine=True)
        node_b = Node(wallet_b, data_b, HOST, PORT_B, peers=[(HOST, PORT_A)], mine=False)

        task_a = asyncio.create_task(node_a.start())
        task_b = asyncio.create_task(node_b.start())
        await asyncio.sleep(0.3)

        target_height = 3
        await wait_for_height(node_a, target_height)
        await wait_for_height(node_b, target_height)
        h = node_a.chain.height
        assert h == node_b.chain.height and h >= target_height

        _, stake_per_block = stake_split(BLOCK_REWARD)
        miner_stakes = node_a.chain.state.stakes.get(wallet_a.address, [])
        assert len(miner_stakes) == h
        for i, entry in enumerate(miner_stakes, start=1):
            assert entry["amount"] == stake_per_block
            assert entry["unlock_height"] == i + STAKE_LOCK_BLOCKS

        bal_a_before = node_a.get_balance(wallet_a.address)
        assert bal_a_before["spendable"] == h * (BLOCK_REWARD - stake_per_block)
        assert bal_a_before["staked"] == h * stake_per_block

        tx = Transaction(
            sender_public_key=wallet_a.sign_public_key,
            recipient=wallet_b.address,
            amount=10 * COIN,
            fee=COIN,
            nonce=0,
            timestamp=int(time.time()),
        )
        tx.sign(wallet_a.sign_secret_key)

        ack = await rpc_call(HOST, PORT_B, "new_tx", {"tx": tx.to_dict()})
        assert ack["data"]["ok"], ack["data"].get("error", "")

        deadline = time.monotonic() + MINE_TIMEOUT
        while True:
            bal_b = node_b.get_balance(wallet_b.address)
            if bal_b["spendable"] >= 10 * COIN:
                break
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"transaction not mined in time "
                    f"(A_h={node_a.chain.height} B_h={node_b.chain.height} "
                    f"mempoolA={len(node_a.mempool)} mempoolB={len(node_b.mempool)})"
                )
            await asyncio.sleep(0.05)

        bal_b_final = node_b.get_balance(wallet_b.address)
        assert bal_b_final["spendable"] == 10 * COIN

        bal_a_after = node_a.get_balance(wallet_a.address)
        assert node_a.chain.state.next_nonce(wallet_a.address) == 1
        assert bal_a_after["spendable"] + 10 * COIN + COIN > bal_a_before["spendable"]

        print("test_integration: all assertions passed")
    finally:
        if task_a is not None:
            task_a.cancel()
        if task_b is not None:
            task_b.cancel()
        for t in (task_a, task_b):
            if t is None:
                continue
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        for node in (node_a, node_b):
            if node is None:
                continue
            try:
                await asyncio.wait_for(node.p2p.stop(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_integration())
