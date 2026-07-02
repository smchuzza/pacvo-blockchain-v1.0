# Pacvo (PVO)

Pacvo is an educational post-quantum cryptocurrency full node written in Python. It implements a proof-of-work blockchain with account-based transfers, automatic staking of mining rewards, and an authenticated encrypted peer-to-peer network. The design goal is to demonstrate how modern post-quantum primitives can be composed into a working ledger—not to serve as production financial infrastructure.

## Cryptography stack

| Layer | Algorithm | Role |
|-------|-----------|------|
| Signatures | SPHINCS+-SHA2-256s (`pqcrypto.sign.sphincs_sha2_256s_simple`) | Transaction authorization (~30 KB signatures) |
| P2P identity | SPHINCS+-SHA2-256s | Per-node long-lived handshake authentication |
| P2P key exchange | ML-KEM-768 (`pqcrypto.kem.ml_kem_768`) | Ephemeral per-connection KEM |
| P2P transport | AES-256-GCM | Directional session keys bound to handshake transcript |
| Wallet encryption | bcrypt KDF + AES-256-GCM | Passphrase-protected secret keys |
| Hashing / PoW | SHA-512 | Block IDs, Merkle tree, hashcash mining |

Addresses use the `pvo1` prefix followed by the full 64-byte `SHA-512(sign_public_key)` digest as hex (128 hex characters).

## Consensus parameters

| Parameter | Value |
|-----------|-------|
| Block reward | 3 PVO |
| Coin unit | 1 PVO = 10^8 base units |
| Minimum fee | 0.0001 PVO (10,000 base units) |
| Staking | 10% of each block reward auto-staked |
| Stake lock | 128 blocks (~1.8 days at target block time) |
| Target block time | 20 minutes (1200 seconds) |
| Difficulty retarget | Every 32 blocks, clamped to 4x adjustment |
| Initial difficulty | `2^486` at launch (~20 min blocks on a typical CPU) |
| Max reorg depth | 128 blocks |
| Timestamps | Strictly greater than median-time-past (11 blocks); at most 600 s ahead of local clock |

Each mined block pays the miner 2.7 PVO spendable immediately and locks 0.3 PVO as stake until `unlock_height = block_height + 128`.

## Resource limits

| Limit | Value |
|-------|-------|
| Max transactions per block | 100 |
| Max block size (canonical JSON) | 4 MiB |
| Max mempool transactions | 1,000 (evict lowest fee when full) |
| Max P2P frame size | 8 MiB |
| Max peers | 32 total connections |
| Max inbound per IP | 3 |
| Handshake timeout | 20 seconds |
| Inbound message rate | 50 messages/second per connection |
| Header sync batch | up to 64 blocks per `get_blocks` request |

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
.venv/bin/python tests/test_network.py
```

SPHINCS+-256s signing is slow (on the order of seconds per signature); network tests use only a handful of handshakes.

## Two-node demo

This walkthrough starts a mining node and a syncing peer on localhost, mines blocks, checks balances, and sends a transfer.

### 1. Create wallets and data directories

```bash
mkdir -p /tmp/pacvo-demo/data-a /tmp/pacvo-demo/data-b

.venv/bin/python cli.py wallet create --out /tmp/pacvo-demo/wa.json
# Enter and confirm a passphrase when prompted

.venv/bin/python cli.py wallet show --wallet /tmp/pacvo-demo/wa.json
# Enter passphrase

.venv/bin/python cli.py wallet create --out /tmp/pacvo-demo/wb.json
.venv/bin/python cli.py wallet show --wallet /tmp/pacvo-demo/wb.json
```

Save the printed addresses as `ADDR_A` and `ADDR_B`.

Non-interactive passphrase (less secure; useful for scripts):

```bash
export PACVO_WALLET_PASSPHRASE='your-passphrase'
```

### 2. Start node A (miner) and node B (peer)

Each node stores a plaintext `identity.json` in its data directory. This key authenticates the node on the P2P network; it does not hold funds.

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

Wait for the miner to find the first block (on the order of 20 minutes), then for subsequent blocks to propagate via headers-first sync.

### 3. Confirm sync on node B

```bash
.venv/bin/python cli.py chain --node 127.0.0.1:9334 --last 5
```

### 4. Check miner balance on node A

```bash
.venv/bin/python cli.py balance --address ADDR_A --node 127.0.0.1:9333
```

### 5. Send PVO from wallet A to wallet B

```bash
.venv/bin/python cli.py send \
  --wallet /tmp/pacvo-demo/wa.json \
  --to ADDR_B \
  --amount 2.5 --fee 0.01 \
  --node 127.0.0.1:9334
```

You will be prompted for the wallet passphrase (or `PACVO_WALLET_PASSPHRASE`).

### 6. Confirm recipient balance on node B

```bash
.venv/bin/python cli.py balance --address ADDR_B --node 127.0.0.1:9334
```

Stop both node processes with Ctrl+C when finished.

## Project layout

```
pacvo/
  params.py       # Chain constants and resource limits
  crypto.py       # PQ primitives, AES-GCM, addressing
  wallet.py       # bcrypt-encrypted key persistence
  transaction.py  # Signed transfers and coinbase
  block.py        # Block header, Merkle root, PoW check
  chain.py        # State, validation, headers-first reorg
  network.py      # Authenticated ML-KEM P2P and rpc_call()
  node.py         # Mempool, sync, identity, TOFU pinning
  miner.py        # Candidate builder and mining loop
cli.py            # Command-line interface
tests/            # Unit tests (no live mining)
```

## Security (v2)

**Authenticated P2P handshake.** Outbound connections use a challenge–response protocol: the dialer sends a random 32-byte challenge; the listener responds with a fresh ML-KEM public key, its SPHINCS+ identity public key, and a signature over `pacvo-hs-listener || kem_pub || challenge`. The dialer verifies, encapsulates, and responds with ciphertext, its identity key, and a signature over `pacvo-hs-dialer || ct || kem_pub || challenge`. Shared secrets derive directional AES-256-GCM keys from the KEM output and a transcript hash over all handshake material.

**TOFU pinning.** Outbound peers are recorded in `known_peers.json` as `host:port → sha512(identity_pub)[:16]`. A changed identity on reconnect aborts with an error log. Inbound connections and one-shot `rpc_call` clients use ephemeral identities (no pinning).

**Encrypted wallets.** Wallet secret keys are encrypted with AES-256-GCM after key derivation via `bcrypt.kdf` (100 rounds, 16-byte salt from `os.urandom`, which is seeded from hardware timing jitter and other kernel entropy sources on Linux). Wrong passphrases raise a clear error.

**Headers-first sync.** Peers exchange header chains via `get_headers` / `headers`, validate proof-of-work and retarget rules without bodies, then fetch block bodies in batches via `get_blocks` / `blocks`. Reorgs deeper than 128 blocks are rejected.

**Full-width addresses.** The entire 512-bit SHA-512 digest of the signing public key is used, eliminating truncation collision surface.

**Median-time-past.** Block timestamps must be strictly greater than the median of the previous 11 block timestamps and no more than 600 seconds in the future.

## Security disclaimer

Pacvo is an educational prototype. It has not been audited and must not be used to secure real funds. Post-quantum algorithm bindings come from maintained libraries, but the surrounding consensus, networking, and wallet tooling are simplified for learning purposes.
