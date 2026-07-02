import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.crypto import (
    generate_kem_keypair,
    generate_sign_keypair,
    kem_decapsulate,
    kem_encapsulate,
    sign_message,
    verify_signature,
)
from pacvo.network import P2PNode, _read_frame, _write_frame, rpc_call
from pacvo.node import Node
from pacvo.wallet import Wallet


class _StubWallet:
    address = "pvo1" + "00" * 64

    def __init__(self) -> None:
        self.sign_public_key, self.sign_secret_key = generate_sign_keypair()


async def _start_node(port: int, data_dir: str) -> Node:
    wallet = _StubWallet()
    node = Node(wallet, data_dir, "127.0.0.1", port, [], mine=False)
    await node.p2p.start()
    return node


async def _stop_node(node: Node) -> None:
    await node.p2p.stop()


async def test_honest_handshake_and_balance() -> None:
    with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
        node_a = await _start_node(19440, da)
        node_b = await _start_node(19441, db)
        try:
            peer = await node_a.p2p.connect("127.0.0.1", 19441)
            assert peer is not None
            await asyncio.sleep(0.5)

            balance = await rpc_call(
                "127.0.0.1", 19441, "get_balance", {"address": node_b.wallet.address}
            )
            assert balance["type"] == "balance"
            assert balance["data"]["height"] == 0
            assert balance["data"]["spendable"] == 0
        finally:
            await _stop_node(node_a)
            await _stop_node(node_b)


async def test_tampered_listener_signature_aborts() -> None:
    identity_pub_l, identity_sk_l = generate_sign_keypair()

    async def bad_listener(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        challenge_d = await _read_frame(reader)
        kem_pub, _kem_sk = generate_kem_keypair()
        sig_l = sign_message(identity_sk_l, b"pacvo-hs-listener" + kem_pub + challenge_d)
        sig_l = sig_l[:-1] + bytes([sig_l[-1] ^ 0xFF])
        await _write_frame(writer, kem_pub)
        await _write_frame(writer, identity_pub_l)
        await _write_frame(writer, sig_l)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(bad_listener, "127.0.0.1", 19442)
    with tempfile.TemporaryDirectory() as d:
        node = await _start_node(19443, d)
        try:
            peer = await node.p2p.connect("127.0.0.1", 19442)
            assert peer is None
        finally:
            await _stop_node(node)
    server.close()
    await server.wait_closed()


async def test_per_ip_connection_limit() -> None:
    with tempfile.TemporaryDirectory() as d:
        node = await _start_node(19444, d)
        connections = []
        try:
            for _ in range(3):
                reader, writer = await asyncio.open_connection("127.0.0.1", 19444)
                challenge_d = os.urandom(32)
                await _write_frame(writer, challenge_d)
                kem_pub = await _read_frame(reader)
                identity_pub_l = await _read_frame(reader)
                sig_l = await _read_frame(reader)
                assert verify_signature(
                    identity_pub_l,
                    b"pacvo-hs-listener" + kem_pub + challenge_d,
                    sig_l,
                )
                ct, ss = kem_encapsulate(kem_pub)
                id_pub, id_sk = generate_sign_keypair()
                sig_d = sign_message(
                    id_sk, b"pacvo-hs-dialer" + ct + kem_pub + challenge_d
                )
                await _write_frame(writer, ct)
                await _write_frame(writer, id_pub)
                await _write_frame(writer, sig_d)
                connections.append((reader, writer))
            await asyncio.sleep(0.2)

            reader4, writer4 = await asyncio.open_connection("127.0.0.1", 19444)
            challenge_d = os.urandom(32)
            await _write_frame(writer4, challenge_d)
            rejected = False
            try:
                await asyncio.wait_for(_read_frame(reader4), timeout=2.0)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ConnectionResetError):
                rejected = True
            assert rejected
            try:
                writer4.close()
                await writer4.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass
        finally:
            for _, writer in connections:
                writer.close()
                await writer.wait_closed()
            await _stop_node(node)


async def main() -> None:
    await test_honest_handshake_and_balance()
    await test_tampered_listener_signature_aborts()
    await test_per_ip_connection_limit()
    print("test_network: all tests passed")


if __name__ == "__main__":
    asyncio.run(main())
