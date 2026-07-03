#!/usr/bin/env python3
import argparse
import asyncio
import getpass
import logging
import os
import time

from pacvo.block import Block
from pacvo.crypto import is_valid_address, sha512_hex
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
    run_parser.add_argument("--wallet", required=True)
    run_parser.add_argument("--data", required=True)
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=9333)
    run_parser.add_argument("--peers", default="")
    run_parser.add_argument("--mine", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    send_parser = subparsers.add_parser("send")
    send_parser.add_argument("--wallet", required=True)
    send_parser.add_argument("--to", required=True)
    send_parser.add_argument("--amount", type=float, required=True)
    send_parser.add_argument("--fee", type=float, default=0.0001)
    send_parser.add_argument("--node", required=True)
    send_parser.set_defaults(func=cmd_send)

    balance_parser = subparsers.add_parser("balance")
    balance_parser.add_argument("--address", required=True)
    balance_parser.add_argument("--node", required=True)
    balance_parser.set_defaults(func=cmd_balance)

    chain_parser = subparsers.add_parser("chain")
    chain_parser.add_argument("--node", required=True)
    chain_parser.add_argument("--last", type=int, default=5)
    chain_parser.set_defaults(func=cmd_chain)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
