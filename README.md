# Pacvo (PVO)

Pacvo is an educational post-quantum cryptocurrency full node written in Python. It implements a proof-of-work blockchain with account-based transfers, automatic staking of mining rewards, and an encrypted peer-to-peer network. The design goal is to demonstrate how modern post-quantum primitives can be composed into a working ledger—not to serve as production financial infrastructure.

## Cryptography stack

| Layer | Algorithm | Role |
|-------|-----------|------|
| Signatures | SPHINCS+-SHA2-128f (`pqcrypto.sign.sphincs_sha2_128f_simple`) | Transaction authorization |
| P2P key exchange | ML-KEM-768 (`pqcrypto.kem.ml_kem_768`) | Session key establishment |
| P2P transport | AES-256-GCM | Encrypted message payloads |
| Hashing / PoW | SHA-512 | Block IDs, Merkle tree, hashcash mining |

Addresses use the `pvo1` prefix followed by the first 20 bytes of `SHA-512(sign_public_key)` as hex (40 characters).

## Consensus parameters

| Parameter | Value |
|-----------|-------|
| Block reward | 3 PVO |
| Coin unit | 1 PVO = 10^8 base units |
| Staking | 10% of each block reward auto-staked |
| Stake lock | 128 blocks (~1.8 days at target block time) |
| Target block time | 20 minutes (1200 seconds) |
| Difficulty retarget | Every 32 blocks, clamped to 4x adjustment |
| Initial difficulty | `2^486` at launch (~20 min blocks on a typical CPU) |

Each mined block pays the miner 2.7 PVO spendable immediately and locks 0.3 PVO as stake until `unlock_height = block_height + 128`.

## Installation

```bash
git clone <repository-url>
cd pacvo-blockchain
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running tests

```bash
.venv/bin/python tests/test_crypto.py
.venv/bin/python tests/test_chain.py
```

## Two-node demo

This walkthrough starts a mining node and a syncing peer on localhost, mines blocks, checks balances, and sends a transfer.

### 1. Create wallets and data directories

```bash
mkdir -p /tmp/pacvo-demo/data-a /tmp/pacvo-demo/data-b

.venv/bin/python cli.py wallet create --out /tmp/pacvo-demo/wa.json
.venv/bin/python cli.py wallet show --wallet /tmp/pacvo-demo/wa.json

.venv/bin/python cli.py wallet create --out /tmp/pacvo-demo/wb.json
.venv/bin/python cli.py wallet show --wallet /tmp/pacvo-demo/wb.json
```

Save the printed addresses as `ADDR_A` and `ADDR_B`.

### 2. Start node A (miner) and node B (peer)

In separate terminals:

```bash
# Terminal 1 — miner (first block takes ~20 minutes at launch difficulty)
.venv/bin/python cli.py run \
  --wallet /tmp/pacvo-demo/wa.json \
  --data /tmp/pacvo-demo/data-a \
  --host 127.0.0.1 --port 9333 --mine
```

```bash
# Terminal 2 — syncing peer
.venv/bin/python cli.py run \
  --wallet /tmp/pacvo-demo/wb.json \
  --data /tmp/pacvo-demo/data-b \
  --host 127.0.0.1 --port 9334 \
  --peers 127.0.0.1:9333
```

Wait for the miner to find the first block (on the order of 20 minutes), then for subsequent blocks to propagate.

### 3. Confirm sync on node B

```bash
.venv/bin/python cli.py chain --node 127.0.0.1:9334 --last 5
```

Example output (after mining has progressed):

```
Chain height: 3
  height=1 hash=000... txs=1 ts=...
  height=2 hash=000... txs=1 ts=...
  height=3 hash=000... txs=1 ts=...
```

### 4. Check miner balance on node A

```bash
.venv/bin/python cli.py balance --address ADDR_A --node 127.0.0.1:9333
```

Example output (after several blocks):

```
Address: pvo1...
Spendable: 8.10000000 PVO
Staked: 0.90000000 PVO
Next nonce: 0
Height: 3
  Stake entry: 0.30000000 PVO (unlock height 129)
  ...
```

### 5. Send PVO from wallet A to wallet B

Submit the transaction through node B (it gossips to the miner):

```bash
.venv/bin/python cli.py send \
  --wallet /tmp/pacvo-demo/wa.json \
  --to ADDR_B \
  --amount 2.5 --fee 0.01 \
  --node 127.0.0.1:9334
```

Example output:

```
85ecfbad29f75627ff639820755a2daa1c848d6e52053d188ba4f3aedb7cd60558aa9ea432560fbc9dc82e4e907bb867cece2b943163a3bec04f1e3c89ae5a48
{'error': '', 'ok': True}
```

Wait for the miner to include the transaction in a new block (another ~20 minutes per block at launch difficulty).

### 6. Confirm recipient balance on node B

```bash
.venv/bin/python cli.py balance --address ADDR_B --node 127.0.0.1:9334
```

Example output:

```
Address: pvo1af991eb1259abcd16c878b3d0f2c9b2caff1a041
Spendable: 2.50000000 PVO
Staked: 0.00000000 PVO
Next nonce: 0
Height: 4
```

Stop both node processes with Ctrl+C when finished.

## Project layout

```
pacvo/
  params.py       # Chain constants and stake_split()
  crypto.py       # PQ primitives, AES-GCM, addressing
  wallet.py       # Key generation and persistence
  transaction.py  # Signed transfers and coinbase
  block.py        # Block header, Merkle root, PoW check
  chain.py        # State, validation, persistence, reorg
  network.py      # ML-KEM + AES-GCM P2P and rpc_call()
  node.py         # Mempool, handlers, gossip
  miner.py        # Candidate builder and mining loop
cli.py            # Command-line interface
tests/            # Unit tests (no live mining)
```

## Security disclaimer

Pacvo is an educational prototype. It has not been audited, does not implement production-grade network hardening, and must not be used to secure real funds. Post-quantum algorithms used here are correct library bindings, but the surrounding consensus, networking, and wallet tooling are simplified for learning purposes.
