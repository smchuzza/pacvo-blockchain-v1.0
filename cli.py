#!/usr/bin/env python3
import argparse
import asyncio
import getpass
import json
import logging
import os
import time

from pacvo.block import Block
from pacvo.crypto import derive_address, derive_evm_address, is_valid_address, sha512_hex
from pacvo.network import rpc_call
from pacvo.params import COIN
from pacvo.transaction import Transaction
from pacvo.wallet import Wallet, WalletError


def format_pvo(amount: int) -> str:
    return f"{amount / COIN:.8f} PVO"


def parse_host_port(value: str) -> tuple[str, int]:
    host, port = value.rsplit(":", 1)
    return host, int(port)


def parse_peers(value: str) -> list[tuple[str, int]]:
    if not value:
        return []
    return [parse_host_port(part.strip()) for part in value.split(",") if part.strip()]


def get_passphrase(prompt: str = "Wallet passphrase: ") -> str:
    env = os.environ.get("PACVO_WALLET_PASSPHRASE")
    if env is not None:
        return env
    return getpass.getpass(prompt)


def load_wallet(path: str) -> Wallet:
    passphrase = get_passphrase()
    try:
        return Wallet.load(path, passphrase)
    except WalletError as exc:
        raise SystemExit(str(exc)) from exc


def cmd_web(args: argparse.Namespace) -> None:
    import http.server
    import socketserver

    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    if not os.path.exists(web_dir):
        raise SystemExit(f"web directory not found at {web_dir}")

    class PacvoWebHTTPHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=web_dir, **kw)

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            super().end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            if self.path == "/api/ccpow/config":
                pin_configured = bool(os.environ.get("CHOCO_PIN") or os.environ.get("PACVO_CHOCO_PIN"))
                cfg = {
                    "server": "configured",
                    "worker": os.environ.get("CHOCO_WORKER", "pacvo15_476_wccpvo"),
                    "authenticated": pin_configured,
                    "miner": "ready",
                }
                res_bytes = json.dumps(cfg).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(res_bytes)))
                self.end_headers()
                self.wfile.write(res_bytes)
            else:
                super().do_GET()

        def do_POST(self):
            if self.path == "/api/rpc":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    method = body.get("method", "get_balance")
                    params = body.get("params", {})
                    node_addr = body.get("node", "127.0.0.1:9442")
                    host, port = parse_host_port(node_addr)
                    res = asyncio.run(rpc_call(host, port, method, params))
                    res_bytes = json.dumps(res).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(res_bytes)))
                    self.end_headers()
                    self.wfile.write(res_bytes)
                except Exception as exc:
                    err_bytes = json.dumps({"status": "error", "error": str(exc)}).encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err_bytes)))
                    self.end_headers()
                    self.wfile.write(err_bytes)
            elif self.path == "/api/wallet/create":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    passphrase = body.get("passphrase", "")
                    if not passphrase:
                        raise ValueError("Passphrase must not be empty")
                    wallet = Wallet.generate()
                    salt = os.urandom(16)
                    key = Wallet._derive_key(passphrase, salt)
                    from pacvo.crypto import encrypt_payload
                    enc_secret = encrypt_payload(key, wallet.sign_secret_key)
                    wallet_dict = {
                        "sign_public_key": wallet.sign_public_key.hex(),
                        "kdf": "bcrypt",
                        "salt": salt.hex(),
                        "rounds": 100,
                        "enc_secret_key": enc_secret.hex(),
                    }
                    evm_addr = derive_evm_address(wallet.sign_public_key)
                    res = {
                        "status": "ok",
                        "address": wallet.address,
                        "evm_address": evm_addr,
                        "public_key": wallet.sign_public_key.hex(),
                        "wallet_json": wallet_dict,
                    }
                    res_bytes = json.dumps(res).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(res_bytes)))
                    self.end_headers()
                    self.wfile.write(res_bytes)
                except Exception as exc:
                    err_bytes = json.dumps({"status": "error", "error": str(exc)}).encode("utf-8")
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err_bytes)))
                    self.end_headers()
                    self.wfile.write(err_bytes)
            elif self.path == "/api/wallet/unlock":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    passphrase = body.get("passphrase", "")
                    wdata = body.get("wallet_json", {})
                    salt = bytes.fromhex(wdata["salt"])
                    key = Wallet._derive_key(passphrase, salt)
                    from pacvo.crypto import decrypt_payload
                    sec_key = decrypt_payload(key, bytes.fromhex(wdata["enc_secret_key"]))
                    pub_key = bytes.fromhex(wdata["sign_public_key"])
                    wallet = Wallet(pub_key, sec_key)
                    evm_addr = derive_evm_address(wallet.sign_public_key)
                    res = {
                        "status": "ok",
                        "address": wallet.address,
                        "evm_address": evm_addr,
                        "public_key": wallet.sign_public_key.hex(),
                    }
                    res_bytes = json.dumps(res).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(res_bytes)))
                    self.end_headers()
                    self.wfile.write(res_bytes)
                except Exception as exc:
                    err_bytes = json.dumps({"status": "error", "error": str(exc)}).encode("utf-8")
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err_bytes)))
                    self.end_headers()
                    self.wfile.write(err_bytes)
            else:
                self.send_error(404, "Not Found")

    print(f"Starting Pacvo Web Interface at http://{args.host}:{args.port}")

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableTCPServer((args.host, args.port), PacvoWebHTTPHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down web server...")


def cmd_ccpow_miner(args: argparse.Namespace) -> None:
    import subprocess
    import sys

    pin = args.pin or os.environ.get("CHOCO_PIN") or os.environ.get("PACVO_CHOCO_PIN")
    if not pin:
        pin = getpass.getpass("Chocohub Account PIN (never echoed): ").strip()
    if not pin:
        raise SystemExit("Account PIN is required for Chocohub authentication")

    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MPG_Miner.py")
    cmd = [
        sys.executable,
        script_path,
        "--server",
        args.server,
        "--worker",
        args.worker,
        "--pin",
        pin,
        "--threads",
        str(args.threads),
    ]
    if args.gpu:
        cmd.append("--gpu")
    print(f"Starting Chocohub CCpow Miner for worker: {args.worker}...")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nStopping CCpow Miner...")


def cmd_wallet_create(args: argparse.Namespace) -> None:
    env = os.environ.get("PACVO_WALLET_PASSPHRASE")
    if env is not None:
        passphrase = env
        confirm = env
    else:
        passphrase = getpass.getpass("Enter passphrase: ")
        confirm = getpass.getpass("Confirm passphrase: ")
    if not passphrase:
        raise SystemExit("passphrase must not be empty")
    if passphrase != confirm:
        raise SystemExit("passphrases do not match")
    wallet = Wallet.generate()
    wallet.save(args.out, passphrase)
    print(wallet.address)


def cmd_wallet_show(args: argparse.Namespace) -> None:
    wallet = load_wallet(args.wallet)
    print(wallet.address)


def cmd_run(args: argparse.Namespace) -> None:
    from pacvo.node import Node

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    wallet = load_wallet(args.wallet)
    peers = parse_peers(args.peers)
    node = Node(wallet, args.data, args.host, args.port, peers, args.mine)
    asyncio.run(node.start())


async def _send(args: argparse.Namespace) -> None:
    if not is_valid_address(args.to):
        raise SystemExit(f"invalid recipient address: {args.to}")
    wallet = load_wallet(args.wallet)
    host, port = parse_host_port(args.node)
    response = await rpc_call(host, port, "get_balance", {"address": wallet.address})
    balance = response["data"]
    tx = Transaction(
        sender_public_key=wallet.sign_public_key,
        recipient=args.to,
        amount=int(round(args.amount * COIN)),
        fee=int(round(args.fee * COIN)),
        nonce=balance["next_nonce"],
        timestamp=int(time.time()),
    )
    tx.sign(wallet.sign_secret_key)
    ack = await rpc_call(host, port, "new_tx", {"tx": tx.to_dict()})
    print(tx.txid)
    print(ack["data"])


def cmd_send(args: argparse.Namespace) -> None:
    asyncio.run(_send(args))


async def _balance(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    response = await rpc_call(host, port, "get_balance", {"address": args.address})
    data = response["data"]
    print(f"Address: {data['address']}")
    print(f"Spendable: {format_pvo(data['spendable'])}")
    print(f"Immature (coinbase): {format_pvo(data.get('immature', 0))}")
    print(f"Staked: {format_pvo(data['staked'])}")
    print(f"Next nonce: {data['next_nonce']}")
    print(f"Height: {data['height']}")
    for entry in data.get("locked_entries", []):
        print(
            f"  Locked entry: {format_pvo(entry['amount'])} "
            f"(unlock height {entry['unlock_height']})"
        )
    for entry in data.get("stake_entries", []):
        print(
            f"  Stake entry: {format_pvo(entry['amount'])} "
            f"(unlock height {entry['unlock_height']})"
        )


def cmd_balance(args: argparse.Namespace) -> None:
    asyncio.run(_balance(args))


def _header_hash(header: dict) -> str:
    from pacvo.crypto import canonical_json

    return sha512_hex(canonical_json(header))


async def _chain(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    from_height = 0
    response = await rpc_call(host, port, "get_headers", {"from_height": from_height})
    headers = response["data"]["headers"]
    if not headers:
        print("Chain height: -1")
        return
    height = headers[-1]["height"]
    print(f"Chain height: {height}")

    display_from = max(0, height - args.last + 1)
    blocks_resp = await rpc_call(
        host,
        port,
        "get_blocks",
        {"from_height": display_from, "count": args.last},
    )
    blocks = blocks_resp["data"]["blocks"]
    block_by_height = {b["height"]: b for b in blocks}

    for header in headers[-args.last :]:
        block = block_by_height.get(header["height"])
        tx_count = len(block.get("transactions", [])) if block else "?"
        block_hash = _header_hash(header)
        print(
            f"  height={header['height']} hash={block_hash[:16]} "
            f"txs={tx_count} ts={header['timestamp']}"
        )


def cmd_chain(args: argparse.Namespace) -> None:
    asyncio.run(_chain(args))


async def _evm_deploy(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    with open(args.wallet) as f:
        wallet = json.load(f)
    sk = bytes.fromhex(wallet["secret_key"])
    pk = bytes.fromhex(wallet["public_key"])
    sender_addr = derive_address(pk)

    bal_resp = await rpc_call(host, port, "get_balance", {"address": sender_addr})
    nonce = bal_resp["data"]["next_nonce"]

    code_hex = args.code.removeprefix("0x")
    init_code = bytes.fromhex(code_hex)
    gas_limit = args.gas if args.gas else 3_000_000
    fee_base = int(args.fee * 100_000_000)

    tx = Transaction(
        sender_public_key=pk,
        recipient="",
        amount=0,
        fee=fee_base,
        nonce=nonce,
        timestamp=int(time.time()),
        evm_to="",
        evm_data=init_code,
        evm_gas_limit=gas_limit,
        evm_value=0,
    )
    tx.sign(sk)
    ack = await rpc_call(host, port, "new_tx", {"tx": tx.to_dict()})
    print("Deploy Tx Broadcasted:", ack["data"])
    print(f"TxID: {tx.txid}")
    sender_evm = derive_evm_address(pk)
    print(f"Deployer EVM Address: {sender_evm}")


async def _evm_call(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    with open(args.wallet) as f:
        wallet = json.load(f)
    sk = bytes.fromhex(wallet["secret_key"])
    pk = bytes.fromhex(wallet["public_key"])
    sender_addr = derive_address(pk)

    bal_resp = await rpc_call(host, port, "get_balance", {"address": sender_addr})
    nonce = bal_resp["data"]["next_nonce"]

    data_hex = args.data.removeprefix("0x") if args.data else ""
    call_data = bytes.fromhex(data_hex)
    gas_limit = args.gas if args.gas else 500_000
    fee_base = int(args.fee * 100_000_000)
    val_base = int(args.value * 100_000_000) if args.value else 0

    tx = Transaction(
        sender_public_key=pk,
        recipient="",
        amount=0,
        fee=fee_base,
        nonce=nonce,
        timestamp=int(time.time()),
        evm_to=args.to,
        evm_data=call_data,
        evm_gas_limit=gas_limit,
        evm_value=val_base,
    )
    tx.sign(sk)
    ack = await rpc_call(host, port, "new_tx", {"tx": tx.to_dict()})
    print("Call Tx Broadcasted:", ack["data"])
    print(f"TxID: {tx.txid}")


async def _evm_receipt(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    resp = await rpc_call(host, port, "eth_getTransactionReceipt", {"tx_hash": args.tx})
    print(json.dumps(resp["data"], indent=2))


async def _evm_query(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    payload = json.loads(args.payload) if args.payload else {}
    resp = await rpc_call(host, port, args.method, payload)
    print(json.dumps(resp["data"], indent=2))


def cmd_evm_deploy(args: argparse.Namespace) -> None:
    asyncio.run(_evm_deploy(args))


def cmd_evm_call(args: argparse.Namespace) -> None:
    asyncio.run(_evm_call(args))


def cmd_evm_receipt(args: argparse.Namespace) -> None:
    asyncio.run(_evm_receipt(args))


def cmd_evm_query(args: argparse.Namespace) -> None:
    asyncio.run(_evm_query(args))


async def _l2_token_deploy(args: argparse.Namespace) -> None:
    from pacvo.l2.factory import TokenFactory
    host, port = parse_host_port(args.node)
    with open(args.wallet) as f:
        wallet = json.load(f)
    sk = bytes.fromhex(wallet["secret_key"])
    pk = bytes.fromhex(wallet["public_key"])
    sender_addr = derive_address(pk)
    sender_evm = derive_evm_address(pk)

    bal_resp = await rpc_call(host, port, "get_balance", {"address": sender_addr})
    nonce = bal_resp["data"]["next_nonce"]

    decimals = args.decimals
    raw_supply = int(args.supply * (10**decimals)) if args.supply else 0

    if args.type == "controlled":
        initcode = TokenFactory.create_controlled_mint_token(
            name=args.name,
            symbol=args.symbol,
            minter=sender_evm,
            initial_supply=raw_supply,
            decimals=decimals,
        )
    else:
        initcode = TokenFactory.create_fixed_supply_token(
            name=args.name,
            symbol=args.symbol,
            initial_supply=raw_supply,
            decimals=decimals,
        )

    fee_base = int(args.fee * 100_000_000)
    tx = Transaction(
        sender_public_key=pk,
        recipient="",
        amount=0,
        fee=fee_base,
        nonce=nonce,
        timestamp=int(time.time()),
        evm_to="",
        evm_data=initcode,
        evm_gas_limit=3_000_000,
        evm_value=0,
    )
    tx.sign(sk)
    ack = await rpc_call(host, port, "new_tx", {"tx": tx.to_dict()})
    print("Token Deploy Tx Broadcasted:", ack["data"])
    print(f"TxID: {tx.txid}")
    print(f"Deployer EVM Address: {sender_evm}")


async def _l2_token_info(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    resp = await rpc_call(host, port, "pacvo_l2_getToken", {"token": args.token})
    print(json.dumps(resp["data"], indent=2))


async def _l2_token_balance(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    resp = await rpc_call(
        host, port, "pacvo_l2_getTokenBalance", {"token": args.token, "address": args.address}
    )
    print(json.dumps(resp["data"], indent=2))


async def _l2_token_transfer(args: argparse.Namespace) -> None:
    from pacvo.l2.token import encode_transfer
    host, port = parse_host_port(args.node)
    with open(args.wallet) as f:
        wallet = json.load(f)
    sk = bytes.fromhex(wallet["secret_key"])
    pk = bytes.fromhex(wallet["public_key"])
    sender_addr = derive_address(pk)

    bal_resp = await rpc_call(host, port, "get_balance", {"address": sender_addr})
    nonce = bal_resp["data"]["next_nonce"]

    # fetch token decimals
    t_info = await rpc_call(host, port, "pacvo_l2_getToken", {"token": args.token})
    decimals = t_info["data"].get("decimals", 18) if t_info["data"].get("exists") else 18
    raw_amount = int(args.amount * (10**decimals))

    call_data = encode_transfer(args.to, raw_amount)
    fee_base = int(args.fee * 100_000_000)

    tx = Transaction(
        sender_public_key=pk,
        recipient="",
        amount=0,
        fee=fee_base,
        nonce=nonce,
        timestamp=int(time.time()),
        evm_to=args.token,
        evm_data=call_data,
        evm_gas_limit=500_000,
        evm_value=0,
    )
    tx.sign(sk)
    ack = await rpc_call(host, port, "new_tx", {"tx": tx.to_dict()})
    print("Token Transfer Tx Broadcasted:", ack["data"])
    print(f"TxID: {tx.txid}")


async def _l2_anchor(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    resp = await rpc_call(host, port, "pacvo_l2_getAnchor", {})
    print(json.dumps(resp["data"], indent=2))


def cmd_l2_token_deploy(args: argparse.Namespace) -> None:
    asyncio.run(_l2_token_deploy(args))


def cmd_l2_token_info(args: argparse.Namespace) -> None:
    asyncio.run(_l2_token_info(args))


def cmd_l2_token_balance(args: argparse.Namespace) -> None:
    asyncio.run(_l2_token_balance(args))


def cmd_l2_token_transfer(args: argparse.Namespace) -> None:
    asyncio.run(_l2_token_transfer(args))


def cmd_l2_anchor(args: argparse.Namespace) -> None:
    asyncio.run(_l2_anchor(args))


async def _l2_nft_deploy(args: argparse.Namespace) -> None:
    from pacvo.l2.factory import TokenFactory
    host, port = parse_host_port(args.node)
    with open(args.wallet) as f:
        wallet = json.load(f)
    sk = bytes.fromhex(wallet["secret_key"])
    pk = bytes.fromhex(wallet["public_key"])
    sender_addr = derive_address(pk)

    bal_resp = await rpc_call(host, port, "get_balance", {"address": sender_addr})
    nonce = bal_resp["data"]["next_nonce"]

    initcode = TokenFactory.create_nft_collection(
        name=args.name,
        symbol=args.symbol,
        minter=sender_addr,
    )
    predicted_addr = TokenFactory.compute_address(sender_addr, nonce)
    fee_base = int(args.fee * 100_000_000)

    tx = Transaction(
        sender_public_key=pk,
        recipient="",
        amount=0,
        fee=fee_base,
        nonce=nonce,
        timestamp=int(time.time()),
        evm_to="",
        evm_data=initcode,
        evm_gas_limit=3_000_000,
        evm_value=0,
    )
    tx.sign(sk)
    ack = await rpc_call(host, port, "new_tx", {"tx": tx.to_dict()})
    print("NFT Collection Deploy Tx Broadcasted:", ack["data"])
    print(f"Predicted NFT Contract Address: {predicted_addr}")
    print(f"TxID: {tx.txid}")


async def _l2_nft_mint(args: argparse.Namespace) -> None:
    from pacvo.l2.nft import encode_nft_mint
    host, port = parse_host_port(args.node)
    with open(args.wallet) as f:
        wallet = json.load(f)
    sk = bytes.fromhex(wallet["secret_key"])
    pk = bytes.fromhex(wallet["public_key"])
    sender_addr = derive_address(pk)

    bal_resp = await rpc_call(host, port, "get_balance", {"address": sender_addr})
    nonce = bal_resp["data"]["next_nonce"]

    call_data = encode_nft_mint(args.to, args.token_id)
    fee_base = int(args.fee * 100_000_000)

    tx = Transaction(
        sender_public_key=pk,
        recipient="",
        amount=0,
        fee=fee_base,
        nonce=nonce,
        timestamp=int(time.time()),
        evm_to=args.contract,
        evm_data=call_data,
        evm_gas_limit=500_000,
        evm_value=0,
    )
    tx.sign(sk)
    ack = await rpc_call(host, port, "new_tx", {"tx": tx.to_dict()})
    print("NFT Mint Tx Broadcasted:", ack["data"])
    print(f"Token ID #{args.token_id} minted to {args.to}")
    print(f"TxID: {tx.txid}")


async def _l2_nft_info(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    resp = await rpc_call(host, port, "pacvo_l2_getNFTCollection", {"contract": args.contract})
    print(json.dumps(resp["data"], indent=2))


async def _l2_nft_owner(args: argparse.Namespace) -> None:
    host, port = parse_host_port(args.node)
    resp = await rpc_call(host, port, "pacvo_l2_getNFT", {"contract": args.contract, "token_id": args.token_id})
    print(json.dumps(resp["data"], indent=2))


def cmd_l2_nft_deploy(args: argparse.Namespace) -> None:
    asyncio.run(_l2_nft_deploy(args))


def cmd_l2_nft_mint(args: argparse.Namespace) -> None:
    asyncio.run(_l2_nft_mint(args))


def cmd_l2_nft_info(args: argparse.Namespace) -> None:
    asyncio.run(_l2_nft_info(args))


def cmd_l2_nft_owner(args: argparse.Namespace) -> None:
    asyncio.run(_l2_nft_owner(args))


async def _l3_query(args: argparse.Namespace, msg_type: str, payload: dict) -> None:
    host, port = parse_host_port(args.node)
    resp = await rpc_call(host, port, msg_type, payload)
    print(json.dumps(resp["data"], indent=2))


def cmd_l3_query(args: argparse.Namespace) -> None:
    payload = {}
    for attr in (
        "symbol", "tokenA", "tokenB", "owner", "user", "order_id", "order",
        "secret", "hashlock", "miner", "nonce", "device", "init_pacvo",
        "part_pacvo", "init_choco", "part_choco", "pvo"
    ):
        if hasattr(args, attr) and getattr(args, attr) is not None:
            payload[attr] = getattr(args, attr)
    asyncio.run(_l3_query(args, args.msg_type, payload))


def main() -> None:
    parser = argparse.ArgumentParser(prog="pacvo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    wallet_parser = subparsers.add_parser("wallet")
    wallet_sub = wallet_parser.add_subparsers(dest="wallet_command", required=True)

    create_parser = wallet_sub.add_parser("create")
    create_parser.add_argument("--out", required=True)
    create_parser.set_defaults(func=cmd_wallet_create)

    show_parser = wallet_sub.add_parser("show")
    show_parser.add_argument("--wallet", required=True)
    show_parser.set_defaults(func=cmd_wallet_show)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--wallet", default="wallet.json")
    run_parser.add_argument("--data", "--data-dir", dest="data", default="./data")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=9442)
    run_parser.add_argument("--peers", default="")
    run_parser.add_argument("--mine", action="store_true")
    run_parser.add_argument("--miner-address", default="")
    run_parser.set_defaults(func=cmd_run)

    tx_parser = subparsers.add_parser("tx")
    tx_sub = tx_parser.add_subparsers(dest="tx_command", required=True)

    send_parser = tx_sub.add_parser("send")
    send_parser.add_argument("--wallet", required=True)
    send_parser.add_argument("--to", "--recipient", dest="to", required=True)
    send_parser.add_argument("--amount", type=float, required=True)
    send_parser.add_argument("--fee", type=float, default=0.0001)
    send_parser.add_argument("--node", required=True)
    send_parser.set_defaults(func=cmd_send)

    chain_parser = subparsers.add_parser("chain")
    chain_sub = chain_parser.add_subparsers(dest="chain_command", required=True)

    status_parser = chain_sub.add_parser("status")
    status_parser.add_argument("--last", type=int, default=10)
    status_parser.add_argument("--node", required=True)
    status_parser.set_defaults(func=cmd_chain)

    evm_parser = subparsers.add_parser("evm")
    evm_sub = evm_parser.add_subparsers(dest="evm_command", required=True)

    evm_deploy_p = evm_sub.add_parser("deploy")
    evm_deploy_p.add_argument("--wallet", required=True)
    evm_deploy_p.add_argument("--bytecode", required=True)
    evm_deploy_p.add_argument("--fee", type=float, default=0.0001)
    evm_deploy_p.add_argument("--gas-limit", type=int, default=3_000_000)
    evm_deploy_p.add_argument("--node", required=True)
    evm_deploy_p.set_defaults(func=cmd_evm_deploy)

    evm_call_p = evm_sub.add_parser("call")
    evm_call_p.add_argument("--wallet", required=True)
    evm_call_p.add_argument("--to", required=True)
    evm_call_p.add_argument("--data", default="")
    evm_call_p.add_argument("--value", type=float, default=0.0)
    evm_call_p.add_argument("--fee", type=float, default=0.0001)
    evm_call_p.add_argument("--gas-limit", type=int, default=3_000_000)
    evm_call_p.add_argument("--node", required=True)
    evm_call_p.set_defaults(func=cmd_evm_call)

    evm_receipt_p = evm_sub.add_parser("receipt")
    evm_receipt_p.add_argument("--txhash", required=True)
    evm_receipt_p.add_argument("--node", required=True)
    evm_receipt_p.set_defaults(func=cmd_evm_receipt)

    evm_query_p = evm_sub.add_parser("query")
    evm_query_p.add_argument("--method", required=True)
    evm_query_p.add_argument("--payload", default="{}")
    evm_query_p.add_argument("--node", required=True)
    evm_query_p.set_defaults(func=cmd_evm_query)

    l2_parser = subparsers.add_parser("l2")
    l2_sub = l2_parser.add_subparsers(dest="l2_command", required=True)

    l2_token_p = l2_sub.add_parser("token")
    l2_token_sub = l2_token_p.add_subparsers(dest="token_command", required=True)

    l2_t_deploy = l2_token_sub.add_parser("deploy")
    l2_t_deploy.add_argument("--wallet", required=True)
    l2_t_deploy.add_argument("--name", required=True)
    l2_t_deploy.add_argument("--symbol", required=True)
    l2_t_deploy.add_argument("--supply", type=float, default=1000000)
    l2_t_deploy.add_argument("--decimals", type=int, default=18)
    l2_t_deploy.add_argument("--type", choices=["fixed", "controlled"], default="fixed")
    l2_t_deploy.add_argument("--fee", type=float, default=0.0001)
    l2_t_deploy.add_argument("--node", required=True)
    l2_t_deploy.set_defaults(func=cmd_l2_token_deploy)

    l2_t_info = l2_token_sub.add_parser("info")
    l2_t_info.add_argument("--token", required=True)
    l2_t_info.add_argument("--node", required=True)
    l2_t_info.set_defaults(func=cmd_l2_token_info)

    l2_t_bal = l2_token_sub.add_parser("balance")
    l2_t_bal.add_argument("--token", required=True)
    l2_t_bal.add_argument("--address", required=True)
    l2_t_bal.add_argument("--node", required=True)
    l2_t_bal.set_defaults(func=cmd_l2_token_balance)

    l2_t_tx = l2_token_sub.add_parser("transfer")
    l2_t_tx.add_argument("--wallet", required=True)
    l2_t_tx.add_argument("--token", required=True)
    l2_t_tx.add_argument("--to", required=True)
    l2_t_tx.add_argument("--amount", type=float, required=True)
    l2_t_tx.add_argument("--fee", type=float, default=0.0001)
    l2_t_tx.add_argument("--node", required=True)
    l2_t_tx.set_defaults(func=cmd_l2_token_transfer)

    l2_anchor_p = l2_sub.add_parser("anchor")
    l2_anchor_p.add_argument("--node", required=True)
    l2_anchor_p.set_defaults(func=cmd_l2_anchor)

    # --- L2 NFT Subparser ---
    l2_nft_p = l2_sub.add_parser("nft")
    l2_nft_sub = l2_nft_p.add_subparsers(dest="nft_command", required=True)

    l2_nft_deploy_p = l2_nft_sub.add_parser("deploy")
    l2_nft_deploy_p.add_argument("--wallet", required=True)
    l2_nft_deploy_p.add_argument("--name", required=True)
    l2_nft_deploy_p.add_argument("--symbol", required=True)
    l2_nft_deploy_p.add_argument("--fee", type=float, default=0.0001)
    l2_nft_deploy_p.add_argument("--node", required=True)
    l2_nft_deploy_p.set_defaults(func=cmd_l2_nft_deploy)

    l2_nft_mint_p = l2_nft_sub.add_parser("mint")
    l2_nft_mint_p.add_argument("--wallet", required=True)
    l2_nft_mint_p.add_argument("--contract", required=True)
    l2_nft_mint_p.add_argument("--to", required=True)
    l2_nft_mint_p.add_argument("--token-id", type=int, required=True)
    l2_nft_mint_p.add_argument("--fee", type=float, default=0.0001)
    l2_nft_mint_p.add_argument("--node", required=True)
    l2_nft_mint_p.set_defaults(func=cmd_l2_nft_mint)

    l2_nft_info_p = l2_nft_sub.add_parser("info")
    l2_nft_info_p.add_argument("--contract", required=True)
    l2_nft_info_p.add_argument("--node", required=True)
    l2_nft_info_p.set_defaults(func=cmd_l2_nft_info)

    l2_nft_owner_p = l2_nft_sub.add_parser("owner")
    l2_nft_owner_p.add_argument("--contract", required=True)
    l2_nft_owner_p.add_argument("--token-id", type=int, required=True)
    l2_nft_owner_p.add_argument("--node", required=True)
    l2_nft_owner_p.set_defaults(func=cmd_l2_nft_owner)

    # --- L3 PVO-Fi Subparsers ---
    l3_parser = subparsers.add_parser("l3")
    l3_sub = l3_parser.add_subparsers(dest="l3_command", required=True)

    l3_res_p = l3_sub.add_parser("reserve")
    l3_res_sub = l3_res_p.add_subparsers(dest="reserve_command", required=True)
    l3_res_info = l3_res_sub.add_parser("info")
    l3_res_info.add_argument("--node", required=True)
    l3_res_info.set_defaults(func=cmd_l3_query, msg_type="pacvo_l3_getReserve")

    l3_eco_p = l3_sub.add_parser("economy")
    l3_eco_sub = l3_eco_p.add_subparsers(dest="economy_command", required=True)
    l3_eco_status = l3_eco_sub.add_parser("status")
    l3_eco_status.add_argument("--node", required=True)
    l3_eco_status.set_defaults(func=cmd_l3_query, msg_type="pacvo_l3_getEconomy")

    l3_anc_p = l3_sub.add_parser("anchor")
    l3_anc_sub = l3_anc_p.add_subparsers(dest="anchor_command", required=True)
    l3_anc_info = l3_anc_sub.add_parser("info")
    l3_anc_info.add_argument("--node", required=True)
    l3_anc_info.set_defaults(func=cmd_l3_query, msg_type="pacvo_l3_getAnchor")

    l3_eq_p = l3_sub.add_parser("equity")
    l3_eq_sub = l3_eq_p.add_subparsers(dest="equity_command", required=True)
    l3_eq_info = l3_eq_sub.add_parser("info")
    l3_eq_info.add_argument("--symbol", required=True)
    l3_eq_info.add_argument("--node", required=True)
    l3_eq_info.set_defaults(func=cmd_l3_query, msg_type="pacvo_l3_getEquity")

    l3_bnd_p = l3_sub.add_parser("bond")
    l3_bnd_sub = l3_bnd_p.add_subparsers(dest="bond_command", required=True)
    l3_bnd_info = l3_bnd_sub.add_parser("info")
    l3_bnd_info.add_argument("--symbol", required=True)
    l3_bnd_info.add_argument("--node", required=True)
    l3_bnd_info.set_defaults(func=cmd_l3_query, msg_type="pacvo_l3_getBond")

    l3_mkt_p = l3_sub.add_parser("market")
    l3_mkt_sub = l3_mkt_p.add_subparsers(dest="market_command", required=True)
    l3_mkt_info = l3_mkt_sub.add_parser("info")
    l3_mkt_info.add_argument("--tokenA", required=True)
    l3_mkt_info.add_argument("--tokenB", required=True)
    l3_mkt_info.add_argument("--node", required=True)
    l3_mkt_info.set_defaults(func=cmd_l3_query, msg_type="pacvo_l3_getMarket")

    l3_fnd_p = l3_sub.add_parser("fund")
    l3_fnd_sub = l3_fnd_p.add_subparsers(dest="fund_command", required=True)
    l3_fnd_info = l3_fnd_sub.add_parser("info")
    l3_fnd_info.add_argument("--symbol", required=True)
    l3_fnd_info.add_argument("--node", required=True)
    l3_fnd_info.set_defaults(func=cmd_l3_query, msg_type="pacvo_l3_getFund")
    l3_fnd_nav = l3_fnd_sub.add_parser("nav")
    l3_fnd_nav.add_argument("--symbol", required=True)
    l3_fnd_nav.add_argument("--node", required=True)
    l3_fnd_nav.set_defaults(func=cmd_l3_query, msg_type="pacvo_l3_getNAV")

    # --- Bridge Commands ---
    br_parser = subparsers.add_parser("bridge")
    br_sub = br_parser.add_subparsers(dest="bridge_command", required=True)

    br_status = br_sub.add_parser("status")
    br_status.add_argument("--node", required=True)
    br_status.set_defaults(func=cmd_l3_query, msg_type="pacvo_bridge_status")

    br_vault = br_sub.add_parser("vault")
    br_vault.add_argument("--symbol", required=True)
    br_vault.add_argument("--node", required=True)
    br_vault.set_defaults(func=cmd_l3_query, msg_type="pacvo_bridge_getVault")

    br_bal = br_sub.add_parser("balance")
    br_bal.add_argument("--symbol", required=True)
    br_bal.add_argument("--user", required=True)
    br_bal.add_argument("--node", required=True)
    br_bal.set_defaults(func=cmd_l3_query, msg_type="pacvo_bridge_getBalance")

    # --- Chocohub HTLC Atomic Swap Commands ---
    htlc_p = subparsers.add_parser("htlc", help="Cross-chain HTLC atomic swaps (1 PVO = 10 CC)")
    htlc_sub = htlc_p.add_subparsers(dest="htlc_command", required=True)

    htlc_list_p = htlc_sub.add_parser("list", help="List all HTLC orders")
    htlc_list_p.add_argument("--node", required=True)
    htlc_list_p.set_defaults(func=cmd_l3_query, msg_type="pacvo_htlc_list")

    htlc_get_p = htlc_sub.add_parser("get", help="Get HTLC order details")
    htlc_get_p.add_argument("--order", required=True, dest="order_id")
    htlc_get_p.add_argument("--node", required=True)
    htlc_get_p.set_defaults(func=cmd_l3_query, msg_type="pacvo_htlc_get")

    htlc_create_p = htlc_sub.add_parser("create", help="Create an HTLC atomic swap order")
    htlc_create_p.add_argument("--init-pacvo", required=True, dest="init_pacvo")
    htlc_create_p.add_argument("--part-pacvo", required=True, dest="part_pacvo")
    htlc_create_p.add_argument("--init-choco", required=True, dest="init_choco")
    htlc_create_p.add_argument("--part-choco", required=True, dest="part_choco")
    htlc_create_p.add_argument("--pvo", type=float, required=True)
    htlc_create_p.add_argument("--hashlock", required=True)
    htlc_create_p.add_argument("--node", required=True)
    htlc_create_p.set_defaults(func=cmd_l3_query, msg_type="pacvo_htlc_create")

    htlc_claim_p = htlc_sub.add_parser("claim", help="Claim HTLC order with secret pre-image")
    htlc_claim_p.add_argument("--order", required=True, dest="order_id")
    htlc_claim_p.add_argument("--secret", required=True)
    htlc_claim_p.add_argument("--node", required=True)
    htlc_claim_p.set_defaults(func=cmd_l3_query, msg_type="pacvo_htlc_claim")

    htlc_refund_p = htlc_sub.add_parser("refund", help="Refund expired HTLC order")
    htlc_refund_p.add_argument("--order", required=True, dest="order_id")
    htlc_refund_p.add_argument("--node", required=True)
    htlc_refund_p.set_defaults(func=cmd_l3_query, msg_type="pacvo_htlc_refund")

    htlc_mine_p = htlc_sub.add_parser("mine", help="Mine SHA-256 proofs on an active HTLC swap")
    htlc_mine_p.add_argument("--order", required=True, dest="order_id")
    htlc_mine_p.add_argument("--miner", required=True)
    htlc_mine_p.add_argument("--nonce", type=int, default=0)
    htlc_mine_p.add_argument("--device", default="cpu")
    htlc_mine_p.add_argument("--node", required=True)
    htlc_mine_p.set_defaults(func=cmd_l3_query, msg_type="pacvo_htlc_mine")

    # --- Web Console Server ---
    web_p = subparsers.add_parser("web", help="Start the Pacvo Web Console HTTP Server")
    web_p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    web_p.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    web_p.set_defaults(func=cmd_web)

    # --- Chocohub CCpow MPG Miner ---
    ccpow_p = subparsers.add_parser("ccpow-miner", help="Run Chocohub CCpow MPG_Miner")
    ccpow_p.add_argument("--server", default="https://chocohub-r011.onrender.com", help="Chocohub server URL")
    ccpow_p.add_argument("--worker", default="pacvo15_476_wccpvo", help="Worker name / wallet")
    ccpow_p.add_argument(
        "--pin",
        default=None,
        help="Account PIN / password (defaults to CHOCO_PIN environment variable or prompt)",
    )
    ccpow_p.add_argument("--threads", type=int, default=2, help="CPU threads")
    ccpow_p.add_argument("--gpu", action="store_true", default=False, help="Enable GPU mining")
    ccpow_p.set_defaults(func=cmd_ccpow_miner)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
