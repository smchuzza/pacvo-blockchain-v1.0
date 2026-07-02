import asyncio
import json
import logging
import os
import time

from pacvo.crypto import (
    canonical_json,
    decrypt_payload,
    encrypt_payload,
    generate_kem_keypair,
    generate_sign_keypair,
    identity_fingerprint,
    kem_decapsulate,
    kem_encapsulate,
    sha512,
    sign_message,
    verify_signature,
)
from pacvo.params import (
    HANDSHAKE_TIMEOUT,
    MAX_BLOCK_BATCH,
    MAX_CONNS_PER_IP,
    MAX_FRAME,
    MAX_MSG_RATE,
    MAX_PEERS,
)

logger = logging.getLogger("pacvo.p2p")

RPC_TIMEOUT = 30.0

_RESPONSE_FOR = {
    "new_tx": "ack",
    "get_balance": "balance",
    "get_headers": "headers",
    "get_blocks": "blocks",
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
    return "parent" in err or "unknown" in err or "ahead" in err or "prev_hash" in err


def _derive_session_keys(
    shared_secret: bytes,
    challenge_d: bytes,
    kem_pub: bytes,
    ciphertext: bytes,
    identity_pub_l: bytes,
    identity_pub_d: bytes,
) -> tuple[bytes, bytes]:
    transcript = sha512(
        challenge_d + kem_pub + ciphertext + identity_pub_l + identity_pub_d
    )
    key_listener_to_dialer = sha512(shared_secret + transcript + b"l2d")[:32]
    key_dialer_to_listener = sha512(shared_secret + transcript + b"d2l")[:32]
    return key_listener_to_dialer, key_dialer_to_listener


class PeerConnection:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        send_key: bytes,
        recv_key: bytes,
        remote_label: str,
        remote_fingerprint: str = "",
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._send_key = send_key
        self._recv_key = recv_key
        self.remote_label = remote_label
        self.remote_fingerprint = remote_fingerprint
        self.peer_height = -1
        self.peer_port = -1
        self._msg_times: list[float] = []

    async def send(self, msg_type: str, data: dict) -> None:
        payload = encrypt_payload(
            self._send_key,
            canonical_json({"type": msg_type, "data": data}),
        )
        await _write_frame(self._writer, payload)

    async def recv(self) -> dict | None:
        try:
            blob = await _read_frame(self._reader)
            plaintext = decrypt_payload(self._recv_key, blob)
            return json.loads(plaintext)
        except Exception:
            return None

    def rate_limit_exceeded(self) -> bool:
        now = time.monotonic()
        self._msg_times = [t for t in self._msg_times if now - t < 1.0]
        if len(self._msg_times) >= MAX_MSG_RATE:
            return True
        self._msg_times.append(now)
        return False

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
        self._inbound_by_ip: dict[str, int] = {}

    def _identity_keys(self) -> tuple[bytes, bytes]:
        return self.node.identity_public_key, self.node.identity_secret_key

    async def _listener_handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> tuple[bytes, bytes, str]:
        challenge_d = await _read_frame(reader)

        kem_pub, kem_sk = generate_kem_keypair()
        identity_pub_l, identity_sk_l = self._identity_keys()
        sig_l = sign_message(
            identity_sk_l, b"pacvo-hs-listener" + kem_pub + challenge_d
        )
        await _write_frame(writer, kem_pub)
        await _write_frame(writer, identity_pub_l)
        await _write_frame(writer, sig_l)

        ciphertext = await _read_frame(reader)
        identity_pub_d = await _read_frame(reader)
        sig_d = await _read_frame(reader)

        if not verify_signature(
            identity_pub_d,
            b"pacvo-hs-dialer" + ciphertext + kem_pub + challenge_d,
            sig_d,
        ):
            raise ValueError("dialer identity signature verification failed")

        shared_secret = kem_decapsulate(kem_sk, ciphertext)
        key_l2d, key_d2l = _derive_session_keys(
            shared_secret,
            challenge_d,
            kem_pub,
            ciphertext,
            identity_pub_l,
            identity_pub_d,
        )
        fingerprint = identity_fingerprint(identity_pub_d)
        logger.info(
            "inbound handshake ok peer=%s fingerprint=%s",
            self._remote_label(writer),
            fingerprint,
        )
        return key_l2d, key_d2l, fingerprint

    async def _dialer_handshake(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        identity_pub_d: bytes,
        identity_sk_d: bytes,
        remote_label: str,
        check_pin: bool,
    ) -> tuple[bytes, bytes, str]:
        challenge_d = os.urandom(32)
        await _write_frame(writer, challenge_d)

        kem_pub = await _read_frame(reader)
        identity_pub_l = await _read_frame(reader)
        sig_l = await _read_frame(reader)

        if not verify_signature(
            identity_pub_l,
            b"pacvo-hs-listener" + kem_pub + challenge_d,
            sig_l,
        ):
            raise ValueError("listener identity signature verification failed")

        fingerprint = identity_fingerprint(identity_pub_l)
        if check_pin:
            self.node.check_peer_pin(remote_label, fingerprint)

        ciphertext, shared_secret = kem_encapsulate(kem_pub)
        sig_d = sign_message(
            identity_sk_d,
            b"pacvo-hs-dialer" + ciphertext + kem_pub + challenge_d,
        )
        await _write_frame(writer, ciphertext)
        await _write_frame(writer, identity_pub_d)
        await _write_frame(writer, sig_d)

        key_l2d, key_d2l = _derive_session_keys(
            shared_secret,
            challenge_d,
            kem_pub,
            ciphertext,
            identity_pub_l,
            identity_pub_d,
        )
        self.node.record_peer_pin(remote_label, fingerprint)
        logger.info(
            "outbound handshake ok peer=%s fingerprint=%s",
            remote_label,
            fingerprint,
        )
        return key_d2l, key_l2d, fingerprint

    def _remote_label(self, writer: asyncio.StreamWriter) -> str:
        peername = writer.get_extra_info("peername")
        if peername:
            return f"{peername[0]}:{peername[1]}"
        return "unknown"

    def _remote_ip(self, writer: asyncio.StreamWriter) -> str:
        peername = writer.get_extra_info("peername")
        if peername:
            return peername[0]
        return "unknown"

    def _total_connections(self) -> int:
        return len(self.peers) + sum(self._inbound_by_ip.values())

    async def _remove_peer(self, peer: PeerConnection) -> None:
        if peer in self.peers:
            self.peers.remove(peer)
        await peer.close()

    def _clamp_block_batch(self, from_height: int, count: int) -> list[dict]:
        count = max(0, min(count, MAX_BLOCK_BATCH))
        blocks: list[dict] = []
        for block in self.node.chain.blocks[from_height:]:
            if len(blocks) >= count:
                break
            candidate = blocks + [block.to_dict()]
            payload = canonical_json({"type": "blocks", "data": {"blocks": candidate}})
            if len(payload) > MAX_FRAME // 2 and blocks:
                break
            blocks = candidate
        return blocks

    async def _handle_message(self, peer: PeerConnection, msg: dict) -> None:
        msg_type = msg.get("type")
        data = msg.get("data", {})

        if msg_type == "hello":
            peer.peer_height = data.get("height", 0)
            peer.peer_port = data.get("port", -1)
            if peer.peer_port != -1 and peer not in self.peers:
                self.peers.append(peer)
            if peer.peer_height > self.node.chain.height:
                asyncio.create_task(self.node.sync_from_peer(peer))
        elif msg_type == "get_headers":
            from_height = max(0, int(data.get("from_height", 0)))
            headers = [b.header_dict() for b in self.node.chain.blocks[from_height:]]
            await peer.send("headers", {"headers": headers})
        elif msg_type == "headers":
            await self.node.handle_headers(data.get("headers", []), peer)
        elif msg_type == "get_blocks":
            from_height = max(0, int(data.get("from_height", 0)))
            count = int(data.get("count", MAX_BLOCK_BATCH))
            blocks = self._clamp_block_batch(from_height, count)
            await peer.send("blocks", {"blocks": blocks})
        elif msg_type == "blocks":
            await self.node.handle_blocks(data.get("blocks", []), peer)
        elif msg_type == "new_block":
            ok, err = self.node.handle_new_block(data.get("block", {}), origin=peer)
            await peer.send("ack", {"ok": ok, "error": err})
            if not ok and err and _needs_chain_sync(err):
                asyncio.create_task(self.node.sync_from_peer(peer))
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
                if peer.rate_limit_exceeded():
                    logger.warning(
                        "dropping peer %s: message rate exceeded %s/sec",
                        peer.remote_label,
                        MAX_MSG_RATE,
                    )
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
        remote_ip = self._remote_ip(writer)
        try:
            if self._inbound_by_ip.get(remote_ip, 0) >= MAX_CONNS_PER_IP:
                logger.warning("rejecting inbound from %s: per-IP limit", remote_ip)
                writer.close()
                await writer.wait_closed()
                return
            if self._total_connections() >= MAX_PEERS:
                logger.warning("rejecting inbound: peer limit reached")
                writer.close()
                await writer.wait_closed()
                return

            self._inbound_by_ip[remote_ip] = self._inbound_by_ip.get(remote_ip, 0) + 1
            key_l2d, key_d2l, fingerprint = await asyncio.wait_for(
                self._listener_handshake(reader, writer),
                timeout=HANDSHAKE_TIMEOUT,
            )
            peer = PeerConnection(
                reader,
                writer,
                key_l2d,
                key_d2l,
                self._remote_label(writer),
                fingerprint,
            )
            await peer.send(
                "hello", {"height": self.node.chain.height, "port": self.port}
            )
            await self._message_loop(peer)
        except Exception:
            logger.exception("inbound connection error")
            if peer is not None:
                await self._remove_peer(peer)
        finally:
            self._inbound_by_ip[remote_ip] = max(
                0, self._inbound_by_ip.get(remote_ip, 1) - 1
            )
            if self._inbound_by_ip.get(remote_ip, 0) == 0:
                self._inbound_by_ip.pop(remote_ip, None)

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_inbound, self.host, self.port
        )
        logger.info("P2P listening on %s:%s", self.host, self.port)

    async def connect(self, host: str, port: int) -> PeerConnection | None:
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        remote_label = f"{host}:{port}"
        try:
            if self._total_connections() >= MAX_PEERS:
                logger.warning("not connecting to %s: peer limit reached", remote_label)
                return None
            reader, writer = await asyncio.open_connection(host, port)
            identity_pub, identity_sk = self._identity_keys()
            key_d2l, key_l2d, fingerprint = await asyncio.wait_for(
                self._dialer_handshake(
                    reader,
                    writer,
                    identity_pub,
                    identity_sk,
                    remote_label,
                    check_pin=True,
                ),
                timeout=HANDSHAKE_TIMEOUT,
            )
            peer = PeerConnection(
                reader, writer, key_d2l, key_l2d, remote_label, fingerprint
            )
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
                        asyncio.create_task(self.node.sync_from_peer(peer))
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


async def rpc_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> tuple[PeerConnection, bytes, bytes]:
    identity_pub, identity_sk = generate_sign_keypair()
    challenge_d = os.urandom(32)
    await _write_frame(writer, challenge_d)

    kem_pub = await asyncio.wait_for(_read_frame(reader), timeout=HANDSHAKE_TIMEOUT)
    identity_pub_l = await asyncio.wait_for(_read_frame(reader), timeout=HANDSHAKE_TIMEOUT)
    sig_l = await asyncio.wait_for(_read_frame(reader), timeout=HANDSHAKE_TIMEOUT)

    if not verify_signature(
        identity_pub_l,
        b"pacvo-hs-listener" + kem_pub + challenge_d,
        sig_l,
    ):
        raise ValueError("listener identity signature verification failed")

    ciphertext, shared_secret = kem_encapsulate(kem_pub)
    sig_d = sign_message(
        identity_sk,
        b"pacvo-hs-dialer" + ciphertext + kem_pub + challenge_d,
    )
    await _write_frame(writer, ciphertext)
    await _write_frame(writer, identity_pub)
    await _write_frame(writer, sig_d)

    key_l2d, key_d2l = _derive_session_keys(
        shared_secret,
        challenge_d,
        kem_pub,
        ciphertext,
        identity_pub_l,
        identity_pub,
    )
    return (
        PeerConnection(reader, writer, key_d2l, key_l2d, "rpc"),
        key_d2l,
        key_l2d,
    )


async def rpc_call(host: str, port: int, msg_type: str, data: dict) -> dict:
    expected = _RESPONSE_FOR.get(msg_type)
    if expected is None:
        raise ValueError(f"unsupported rpc message type: {msg_type}")

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=RPC_TIMEOUT
    )
    peer: PeerConnection | None = None
    try:
        peer, _, _ = await rpc_handshake(reader, writer)
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
