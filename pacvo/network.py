import asyncio
import json
import logging
import time

from pacvo.crypto import (
    canonical_json,
    decrypt_payload,
    encrypt_payload,
    generate_kem_keypair,
    kem_decapsulate,
    kem_encapsulate,
    sha512,
)

logger = logging.getLogger("pacvo.p2p")

MAX_FRAME = 64 * 1024 * 1024
RPC_TIMEOUT = 30.0

_RESPONSE_FOR = {
    "new_tx": "ack",
    "get_balance": "balance",
    "get_chain": "chain",
}


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(4)
    length = int.from_bytes(header, "big")
    if length > MAX_FRAME:
        raise ValueError("frame exceeds maximum size")
    return await reader.readexactly(length)


async def _write_frame(writer: asyncio.StreamWriter, payload: bytes) -> None:
    writer.write(len(payload).to_bytes(4, "big") + payload)
    await writer.drain()


def _needs_chain_sync(error: str) -> bool:
    err = error.lower()
    return "parent" in err or "unknown" in err or "ahead" in err


class PeerConnection:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        session_key: bytes,
        remote_label: str,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._session_key = session_key
        self.remote_label = remote_label
        self.peer_height = -1
        self.peer_port = -1

    async def send(self, msg_type: str, data: dict) -> None:
        payload = encrypt_payload(
            self._session_key,
            canonical_json({"type": msg_type, "data": data}),
        )
        await _write_frame(self._writer, payload)

    async def recv(self) -> dict | None:
        try:
            blob = await _read_frame(self._reader)
            plaintext = decrypt_payload(self._session_key, blob)
            return json.loads(plaintext)
        except Exception:
            return None

    async def close(self) -> None:
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass


class P2PNode:
    def __init__(self, host: str, port: int, node) -> None:
        self.host = host
        self.port = port
        self.node = node
        self.peers: list[PeerConnection] = []
        self._server: asyncio.Server | None = None

    async def _listener_handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> bytes:
        public_key, secret_key = generate_kem_keypair()
        await _write_frame(writer, public_key)
        ciphertext = await _read_frame(reader)
        shared_secret = kem_decapsulate(secret_key, ciphertext)
        return sha512(shared_secret)[:32]

    async def _dialer_handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> bytes:
        public_key = await _read_frame(reader)
        ciphertext, shared_secret = kem_encapsulate(public_key)
        await _write_frame(writer, ciphertext)
        return sha512(shared_secret)[:32]

    def _remote_label(self, writer: asyncio.StreamWriter) -> str:
        peername = writer.get_extra_info("peername")
        if peername:
            return f"{peername[0]}:{peername[1]}"
        return "unknown"

    async def _remove_peer(self, peer: PeerConnection) -> None:
        if peer in self.peers:
            self.peers.remove(peer)
        await peer.close()

    async def _handle_message(self, peer: PeerConnection, msg: dict) -> None:
        msg_type = msg.get("type")
        data = msg.get("data", {})

        if msg_type == "hello":
            peer.peer_height = data.get("height", 0)
            peer.peer_port = data.get("port", -1)
            if peer.peer_port != -1 and peer not in self.peers:
                self.peers.append(peer)
            if peer.peer_height > self.node.chain.height:
                await peer.send("get_chain", {})
        elif msg_type == "get_chain":
            await peer.send("chain", {"blocks": self.node.get_chain_dicts()})
        elif msg_type == "chain":
            self.node.chain.replace_if_better(data.get("blocks", []))
        elif msg_type == "new_block":
            ok, err = self.node.handle_new_block(data.get("block", {}), origin=peer)
            await peer.send("ack", {"ok": ok, "error": err})
            if not ok and err and _needs_chain_sync(err):
                await peer.send("get_chain", {})
        elif msg_type == "new_tx":
            ok, err = self.node.handle_new_tx(data.get("tx", {}), origin=peer)
            await peer.send("ack", {"ok": ok, "error": err})
        elif msg_type == "get_balance":
            address = data.get("address", "")
            await peer.send("balance", self.node.get_balance(address))

    async def _message_loop(self, peer: PeerConnection) -> None:
        try:
            while True:
                msg = await peer.recv()
                if msg is None:
                    break
                await self._handle_message(peer, msg)
        except Exception:
            logger.exception("message loop error with %s", peer.remote_label)
        finally:
            await self._remove_peer(peer)

    async def _handle_inbound(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer: PeerConnection | None = None
        try:
            session_key = await self._listener_handshake(reader, writer)
            peer = PeerConnection(reader, writer, session_key, self._remote_label(writer))
            await peer.send(
                "hello", {"height": self.node.chain.height, "port": self.port}
            )
            await self._message_loop(peer)
        except Exception:
            logger.exception("inbound connection error")
            if peer is not None:
                await self._remove_peer(peer)

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_inbound, self.host, self.port
        )
        logger.info("P2P listening on %s:%s", self.host, self.port)

    async def connect(self, host: str, port: int) -> PeerConnection | None:
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_connection(host, port)
            session_key = await self._dialer_handshake(reader, writer)
            peer = PeerConnection(reader, writer, session_key, f"{host}:{port}")
            await peer.send(
                "hello", {"height": self.node.chain.height, "port": self.port}
            )
            while True:
                msg = await peer.recv()
                if msg is None:
                    await peer.close()
                    return None
                if msg.get("type") == "hello":
                    hello_data = msg.get("data", {})
                    peer.peer_height = hello_data.get("height", 0)
                    peer.peer_port = hello_data.get("port", -1)
                    if peer.peer_height > self.node.chain.height:
                        await peer.send("get_chain", {})
                    break
            self.peers.append(peer)
            asyncio.create_task(self._message_loop(peer))
            return peer
        except Exception:
            logger.exception("failed to connect to %s:%s", host, port)
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            return None

    async def broadcast(
        self, msg_type: str, data: dict, exclude: PeerConnection | None = None
    ) -> None:
        dead: list[PeerConnection] = []
        for peer in self.peers:
            if peer is exclude:
                continue
            try:
                await peer.send(msg_type, data)
            except Exception:
                dead.append(peer)
        for peer in dead:
            await self._remove_peer(peer)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for peer in list(self.peers):
            await self._remove_peer(peer)


async def rpc_call(host: str, port: int, msg_type: str, data: dict) -> dict:
    expected = _RESPONSE_FOR.get(msg_type)
    if expected is None:
        raise ValueError(f"unsupported rpc message type: {msg_type}")

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=RPC_TIMEOUT
    )
    peer: PeerConnection | None = None
    try:
        public_key = await asyncio.wait_for(_read_frame(reader), timeout=RPC_TIMEOUT)
        ciphertext, shared_secret = kem_encapsulate(public_key)
        await _write_frame(writer, ciphertext)
        session_key = sha512(shared_secret)[:32]
        peer = PeerConnection(reader, writer, session_key, f"{host}:{port}")

        await peer.send("hello", {"height": -1, "port": -1})
        await peer.send(msg_type, data)

        deadline = time.monotonic() + RPC_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"rpc_call {msg_type} timed out")
            msg = await asyncio.wait_for(peer.recv(), timeout=remaining)
            if msg is None:
                raise ConnectionError("connection closed before response")
            incoming = msg.get("type")
            if incoming in ("hello", "new_block", "new_tx"):
                continue
            if incoming == expected:
                return msg
    finally:
        if peer is not None:
            await peer.close()
