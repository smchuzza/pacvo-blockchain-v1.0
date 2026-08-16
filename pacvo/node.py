import asyncio
import json
import logging
import os
import tempfile

from pacvo.block import Block
from pacvo.chain import Blockchain, State
from pacvo.crypto import generate_sign_keypair, identity_fingerprint
from pacvo.evm.vm import EVM, ExecutionContext
from pacvo.network import P2PNode
from pacvo.params import BLOCK_GAS_LIMIT, EVM_CHAIN_ID, MAX_BLOCK_BATCH, MAX_MEMPOOL_TXS, MAX_REORG_DEPTH
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
        from pacvo.l3.economy import Economy
        self.economy = Economy()
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

    # --- Ethereum JSON-RPC Compatible Node Methods ---

    def eth_chain_id(self) -> dict:
        return {"chain_id": hex(EVM_CHAIN_ID), "chain_id_dec": EVM_CHAIN_ID}

    def eth_block_number(self) -> dict:
        return {"block_number": hex(self.chain.height), "number": self.chain.height}

    def eth_get_balance(self, address: str) -> dict:
        bal = self.chain.state.evm_state.get_balance(address)
        return {"address": address, "balance": hex(bal), "balance_dec": bal}

    def eth_get_code(self, address: str) -> dict:
        code = self.chain.state.evm_state.get_code(address)
        return {"address": address, "code": "0x" + code.hex()}

    def eth_get_storage_at(self, address: str, position: int) -> dict:
        val = self.chain.state.evm_state.get_storage(address, position)
        return {"address": address, "position": hex(position), "storage": "0x" + format(val, "064x")}

    def eth_call(self, tx_data: dict) -> dict:
        caller = tx_data.get("from", "0x0000000000000000000000000000000000000000")
        target = tx_data.get("to", "")
        data = bytes.fromhex(tx_data.get("data", "").removeprefix("0x"))
        value = int(tx_data.get("value", 0)) if isinstance(tx_data.get("value", 0), int) else int(str(tx_data.get("value", "0")), 16)
        gas_limit = int(tx_data.get("gas", BLOCK_GAS_LIMIT)) if isinstance(tx_data.get("gas", BLOCK_GAS_LIMIT), int) else int(str(tx_data.get("gas", "0")), 16)

        sim_evm_state = self.chain.state.evm_state.copy()
        ctx = ExecutionContext(
            caller=caller,
            address=target,
            origin=caller,
            value=value,
            data=data,
            gas_price=0,
            gas_limit=gas_limit,
            block_number=self.chain.height,
            block_timestamp=self.chain.blocks[-1].timestamp if self.chain.blocks else 0,
            block_difficulty=self.chain.blocks[-1].target if self.chain.blocks else 0,
            block_gas_limit=BLOCK_GAS_LIMIT,
            chain_id=EVM_CHAIN_ID,
            is_static=True,
        )
        vm = EVM(sim_evm_state)
        code = sim_evm_state.get_code(target)
        res = vm.execute(code, ctx)
        return {
            "success": res.success,
            "return_data": "0x" + res.return_data.hex(),
            "gas_used": hex(res.gas_used),
            "error": res.error,
        }

    def eth_estimate_gas(self, tx_data: dict) -> dict:
        call_res = self.eth_call(tx_data)
        return {"gas": call_res.get("gas_used", hex(21000))}

    def eth_get_transaction_receipt(self, tx_hash: str) -> dict:
        receipt = self.chain.receipts.get(tx_hash)
        if receipt is None:
            return {"receipt": None}
        return {"receipt": receipt.to_dict()}

    def eth_get_logs(self, filter_params: dict) -> dict:
        target_addr = filter_params.get("address")
        target_topics = filter_params.get("topics", [])
        matched = []
        for r in self.chain.receipts.values():
            for log in r.logs:
                if target_addr and log.address.lower() != target_addr.lower():
                    continue
                if target_topics:
                    if len(log.topics) < len(target_topics):
                        continue
                    if not all(log.topics[i].lower() == target_topics[i].lower() for i in range(len(target_topics)) if target_topics[i]):
                        continue
                matched.append(log.to_dict())
        return {"logs": matched}

    # --- Pacvo Layer 2 (L2) RPC Methods ---

    def pacvo_l2_get_token(self, token_address: str) -> dict:
        from pacvo.l2.state import L2State
        l2_state = L2State(self.chain.state.evm_state)
        return l2_state.get_token_metadata(token_address)

    def pacvo_l2_get_token_balance(self, token_address: str, owner_address: str) -> dict:
        from pacvo.l2.state import L2State
        l2_state = L2State(self.chain.state.evm_state)
        bal = l2_state.get_token_balance(token_address, owner_address)
        return {
            "token": token_address,
            "address": owner_address,
            "balance": hex(bal),
            "balance_dec": bal,
        }

    def pacvo_l2_get_token_allowance(self, token_address: str, owner_address: str, spender_address: str) -> dict:
        from pacvo.l2.state import L2State
        l2_state = L2State(self.chain.state.evm_state)
        allowance = l2_state.get_token_allowance(token_address, owner_address, spender_address)
        return {
            "token": token_address,
            "owner": owner_address,
            "spender": spender_address,
            "allowance": hex(allowance),
            "allowance_dec": allowance,
        }

    def pacvo_l2_get_state_root(self) -> dict:
        from pacvo.l2.anchor import compute_l2_state_root
        root = compute_l2_state_root(self.chain.state.evm_state)
        return {"height": self.chain.height, "state_root": root}

    def pacvo_l2_get_anchor(self) -> dict:
        from pacvo.l2.anchor import compute_l2_state_root
        root = compute_l2_state_root(self.chain.state.evm_state)
        tip_hash = self.chain.blocks[-1].block_hash if self.chain.blocks else "0" * 128
        return {
            "l1_height": self.chain.height,
            "l1_block_hash": tip_hash,
            "l2_sequence": self.chain.height,
            "state_root": root,
        }

    def pacvo_l2_get_nft(self, contract: str, token_id: int) -> dict:
        from pacvo.l2.state import L2State
        addr = contract.lower()
        l2_state = L2State(self.chain.state.evm_state)
        owner = l2_state.get_nft_owner(addr, token_id)
        approval = l2_state.get_nft_approval(addr, token_id)
        return {
            "contract": addr,
            "token_id": token_id,
            "exists": owner is not None,
            "owner": owner,
            "approved": approval,
        }

    def pacvo_l2_get_nft_collection(self, contract: str) -> dict:
        from pacvo.l2.state import L2State
        return L2State(self.chain.state.evm_state).get_nft_metadata(contract.lower())

    # --- Layer 3 (L3) PVO-Fi RPC Methods ---

    def pacvo_l3_get_asset(self, symbol: str) -> dict:
        asset = self.economy.registry.get_asset(symbol)
        if asset is None:
            return {"error": f"Asset '{symbol}' not found"}
        return asset.to_dict()

    def pacvo_l3_get_equity(self, symbol: str) -> dict:
        eq = self.economy.equities.get(symbol.upper())
        if eq is None:
            return {"error": f"Equity '{symbol}' not found"}
        return eq.to_dict()

    def pacvo_l3_get_bond(self, symbol: str) -> dict:
        bond = self.economy.bonds.get(symbol.upper())
        if bond is None:
            return {"error": f"Bond '{symbol}' not found"}
        return bond.to_dict()

    def pacvo_l3_get_debt(self, borrower: str) -> dict:
        pos = self.economy.debt_manager.get_position(borrower)
        if pos is None:
            return {"error": f"No debt position found for borrower '{borrower}'"}
        return pos.to_dict()

    def pacvo_l3_get_fund(self, symbol: str) -> dict:
        fund = self.economy.funds.get(symbol.upper())
        if fund is None:
            return {"error": f"Fund '{symbol}' not found"}
        return fund.to_dict()

    def pacvo_l3_get_market(self, token_a: str, token_b: str) -> dict:
        m = self.economy.market_manager.get_market(token_a, token_b)
        if m is None:
            return {"error": f"Market for pair '{token_a}/{token_b}' not found"}
        return m.to_dict()

    def pacvo_l3_get_position(self, owner: str) -> dict:
        pos = self.economy.debt_manager.get_position(owner)
        if pos is None:
            return {"error": f"No position found for owner '{owner}'"}
        return pos.to_dict()

    def pacvo_l3_get_treasury(self) -> dict:
        return self.economy.treasury.to_dict()

    def pacvo_l3_get_reserve(self) -> dict:
        return self.economy.reserve.to_dict()

    def pacvo_l3_get_nav(self, symbol: str) -> dict:
        fund = self.economy.funds.get(symbol.upper())
        if fund is None:
            return {"error": f"Fund '{symbol}' not found"}
        nav = fund.calculate_nav(self.economy.price_engine._reference_prices)
        return {"symbol": symbol.upper(), "nav_wad": str(nav), "nav_dec": nav / 10**18}

    def pacvo_l3_get_price(self, symbol: str) -> dict:
        p = self.economy.price_engine.get_price(symbol)
        return {"symbol": symbol.upper(), "price_wad": str(p), "price_dec": p / 10**18}

    def pacvo_l3_get_epoch(self) -> dict:
        return {
            "epoch": self.economy.epoch,
            "current_height": self.economy.current_height,
        }

    def pacvo_l3_get_state_root(self) -> dict:
        from pacvo.l3.anchor import compute_l3_state_root
        root = compute_l3_state_root(self.economy)
        return {
            "epoch": self.economy.epoch,
            "height": self.economy.current_height,
            "state_root": root,
        }

    def pacvo_l3_get_economy(self) -> dict:
        return self.economy.to_dict()

    def pacvo_l3_get_anchor(self) -> dict:
        from pacvo.l3.anchor import compute_l3_state_root
        root = compute_l3_state_root(self.economy)
        tip_hash = self.chain.blocks[-1].block_hash if self.chain.blocks else "0" * 128
        return {
            "l1_height": self.chain.height,
            "l1_block_hash": tip_hash,
            "l3_epoch": self.economy.epoch,
            "state_root": root,
        }

    # --- Native Cross-Chain Bridge RPC Handlers ---

    def pacvo_bridge_status(self) -> dict:
        return self.economy.bridge.to_dict()

    def pacvo_bridge_get_vault(self, symbol: str) -> dict:
        sym = symbol.upper()
        if sym in ("BTC", "WPVO-BTC"):
            return {
                "symbol": "wPVO-BTC",
                "chain": "Bitcoin",
                "vault_address": self.economy.bridge.btc_adapter.vault_address,
                "locked_satoshis": str(self.economy.bridge.btc_adapter.total_locked_satoshis),
                "minted_wad": str(self.economy.bridge.btc_adapter.total_minted_wad),
            }
        elif sym in ("XNO", "WPVO-XNO"):
            return {
                "symbol": "wPVO-XNO",
                "chain": "Nano",
                "vault_address": self.economy.bridge.xno_adapter.vault_address,
                "locked_raw": str(self.economy.bridge.xno_adapter.total_locked_raw),
                "minted_wad": str(self.economy.bridge.xno_adapter.total_minted_wad),
            }
        elif sym in ("CC", "WCCPVO", "WPVO-CC"):
            return {
                "symbol": "wCCPVO",
                "chain": "Chocohub",
                "vault_address": self.economy.bridge.cc_adapter.vault_address,
                "locked_raw": str(self.economy.bridge.cc_adapter.total_locked_raw),
                "minted_wad": str(self.economy.bridge.cc_adapter.total_minted_wad),
            }
        return {"error": f"Unsupported bridge asset '{symbol}'"}

    def pacvo_bridge_get_balance(self, symbol: str, user: str) -> dict:
        bal = self.economy.bridge.get_balance(symbol, user)
        return {
            "symbol": symbol.upper(),
            "user": user,
            "balance_wad": str(bal),
            "balance_dec": bal / 10**18,
        }

    def pacvo_bridge_deposit(
        self,
        symbol: str,
        external_tx_hash: str,
        external_from: str,
        pacvo_recipient: str,
        raw_amount: int,
    ) -> dict:
        sym = symbol.upper()
        if sym in ("BTC", "WPVO-BTC"):
            record = self.economy.bridge.process_btc_deposit(
                external_tx_hash=external_tx_hash,
                external_from=external_from,
                pacvo_recipient=pacvo_recipient,
                satoshis=raw_amount,
                block_height=self.chain.height,
            )
            return record.to_dict()
        elif sym in ("XNO", "WPVO-XNO"):
            record = self.economy.bridge.process_xno_deposit(
                external_block_hash=external_tx_hash,
                external_from=external_from,
                pacvo_recipient=pacvo_recipient,
                raw_amount=raw_amount,
                block_height=self.chain.height,
            )
            return record.to_dict()
        elif sym in ("CC", "WCCPVO", "WPVO-CC"):
            record = self.economy.bridge.process_cc_deposit(
                external_tx_hash=external_tx_hash,
                external_from=external_from,
                pacvo_recipient=pacvo_recipient,
                raw_amount=raw_amount,
                block_height=self.chain.height,
            )
            return record.to_dict()
        return {"error": f"Unsupported bridge asset '{symbol}'"}

    def pacvo_bridge_burn(
        self,
        symbol: str,
        pacvo_sender: str,
        external_destination: str,
        amount_wad: int,
    ) -> dict:
        sym = symbol.upper()
        if sym in ("BTC", "WPVO-BTC"):
            record = self.economy.bridge.process_btc_burn(
                pacvo_sender=pacvo_sender,
                external_btc_destination=external_destination,
                amount_wad=amount_wad,
                block_height=self.chain.height,
            )
            return record.to_dict()
        elif sym in ("XNO", "WPVO-XNO"):
            record = self.economy.bridge.process_xno_burn(
                pacvo_sender=pacvo_sender,
                external_nano_destination=external_destination,
                amount_wad=amount_wad,
                block_height=self.chain.height,
            )
            return record.to_dict()
        elif sym in ("CC", "WCCPVO", "WPVO-CC"):
            record = self.economy.bridge.process_cc_burn(
                pacvo_sender=pacvo_sender,
                external_choco_destination=external_destination,
                amount_wad=amount_wad,
                block_height=self.chain.height,
            )
            return record.to_dict()
        return {"error": f"Unsupported bridge asset '{symbol}'"}

    # --- Chocohub HTLC Atomic Swap RPC Methods ---

    def pacvo_htlc_create(
        self,
        initiator_pacvo: str,
        participant_pacvo: str,
        initiator_choco: str,
        participant_choco: str,
        amount_pvo_wad: int,
        hashlock: str,
        timelock_pacvo: int = 144,
        timelock_choco: int = 72,
    ) -> dict:
        order = self.economy.htlc.create_order(
            initiator_pacvo=initiator_pacvo,
            participant_pacvo=participant_pacvo,
            initiator_choco=initiator_choco,
            participant_choco=participant_choco,
            amount_pvo_wad=amount_pvo_wad,
            hashlock_hex=hashlock,
            current_height=self.chain.height,
            timelock_blocks_pacvo=timelock_pacvo,
            timelock_blocks_choco=timelock_choco,
        )
        return order.to_dict()

    def pacvo_htlc_claim(self, order_id: str, secret: str) -> dict:
        pvo_amt, cc_amt = self.economy.htlc.claim_swap(
            order_id=order_id,
            secret_hex=secret,
            current_height=self.chain.height,
        )
        return {"order_id": order_id, "status": "CLAIMED", "amount_pvo_wad": str(pvo_amt), "amount_cc_wad": str(cc_amt)}

    def pacvo_htlc_refund(self, order_id: str) -> dict:
        pvo_amt, cc_amt = self.economy.htlc.refund_swap(
            order_id=order_id,
            current_height=self.chain.height,
        )
        return {"order_id": order_id, "status": "REFUNDED", "refunded_pvo_wad": str(pvo_amt), "refunded_cc_wad": str(cc_amt)}

    def pacvo_htlc_mine(
        self,
        order_id: str,
        miner_choco: str,
        nonce: int,
        device_type: str = "cpu",
    ) -> dict:
        tip_hash = self.chain.blocks[-1].block_hash if self.chain.blocks else "0" * 64
        res = self.economy.htlc.mine_htlc_swap(
            order_id=order_id,
            miner_choco_account=miner_choco,
            nonce=nonce,
            device_type=device_type,
            last_block_hash=tip_hash,
        )
        return res

    def pacvo_htlc_get(self, order_id: str) -> dict:
        order = self.economy.htlc.orders.get(order_id)
        if order is None:
            return {"error": f"HTLC order '{order_id}' not found"}
        return order.to_dict()

    def pacvo_htlc_list(self) -> dict:
        return {
            "orders": [o.to_dict() for o in self.economy.htlc.orders.values()],
            "escrow_pvo_wad": str(self.economy.htlc.escrow_pvo_wad),
            "escrow_cc_wad": str(self.economy.htlc.escrow_cc_wad),
            "rate": "1 PVO = 10 CC",
        }

    def pacvo_node_get_status(self) -> dict:
        tip = self.chain.blocks[-1] if self.chain.blocks else None
        target = self.chain.next_target()
        recent_blocks = [
            {
                "height": b.height,
                "block_hash": b.block_hash,
                "prev_hash": b.prev_hash,
                "timestamp": b.timestamp,
                "nonce": b.nonce,
                "tx_count": len(b.transactions),
            }
            for b in reversed(self.chain.blocks[-10:])
        ]
        return {
            "height": self.chain.height,
            "tip_hash": tip.block_hash if tip else "0" * 64,
            "target": target,
            "target_hex": f"{target:064x}",
            "cumulative_work": self.chain.cumulative_work(),
            "mempool_size": len(self.mempool),
            "peer_count": len(self.p2p.peers),
            "is_mining": self.mine,
            "miner_address": self.wallet.address,
            "miner_spendable": self.chain.state.spendable(self.wallet.address),
            "miner_staked": self.chain.state.staked(self.wallet.address),
            "miner_immature": self.chain.state.immature(self.wallet.address),
            "miner_locked_entries": [
                {
                    "amount": entry[0],
                    "unlocks_at": entry[1],
                    "remaining_blocks": max(0, entry[1] - self.chain.height),
                }
                for entry in self.chain.state.locked.get(self.wallet.address, [])
            ],
            "miner_stake_entries": [
                {
                    "amount": entry[0],
                    "unlocks_at": entry[1],
                }
                for entry in self.chain.state.stakes.get(self.wallet.address, [])
            ],
            "recent_blocks": recent_blocks,
        }

    def pacvo_node_mine_block(self) -> dict:
        from pacvo.miner import build_candidate, _search_nonces
        candidate = build_candidate(
            self.chain, list(self.mempool.values()), self.wallet.address
        )
        nonce = 0
        while True:
            winning = _search_nonces(candidate, nonce, 2000)
            if winning is not None:
                candidate.nonce = winning
                ok, err = self.chain.add_block(candidate)
                if not ok:
                    return {"error": f"Mined block rejected: {err}"}
                self.submit_block(candidate)
                return {
                    "status": "MINED",
                    "height": candidate.height,
                    "block_hash": candidate.block_hash,
                    "nonce": candidate.nonce,
                    "tx_count": len(candidate.transactions),
                    "timestamp": candidate.timestamp,
                }
            nonce += 2000



