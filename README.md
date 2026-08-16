# Pacvo (PVO) — Post-Quantum Multi-Layer Blockchain & PVO-Fi Engine

Pacvo is a post-quantum cryptocurrency full node and financial ecosystem written in Python. It implements a quantum-resistant Proof-of-Work blockchain, an embedded EVM bytecode execution environment (Layer 2), an automated financial economy (Layer 3 PVO-Fi), trustless cross-chain Hash Time-Locked Contracts (HTLC), and a modern Web Console interface.

---

## Architecture Overview

```
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      Interactive Web Console & CLI                     │
 │          (Progressive Disclosure UI, JSON-RPC Gateway, Miner)          │
 └────────────────────────────────────┬───────────────────────────────────┘
                                      │
 ┌────────────────────────────────────▼───────────────────────────────────┐
 │               Layer 3: PVO-Fi Decentralized Economy                    │
 │    • Constant-Product AMM DEX Pools (x * y = k)                        │
 │    • Collateralized Debt Positions (CDP) & Lending (150% MCR)          │
 │    • Native Proof-of-Reserve Bridges (wPVO-BTC, wPVO-XNO, wCCPVO)      │
 │    • HTLC Cross-Chain Atomic Swaps & CCpow Proof Solver                │
 └────────────────────────────────────┬───────────────────────────────────┘
                                      │
 ┌────────────────────────────────────▼───────────────────────────────────┐
 │                 Layer 2: EVM & State Anchoring Engine                  │
 │    • Full EVM Bytecode Interpreter (ERC-20 Fungible & ERC-721 NFTs)    │
 │    • Deterministic CREATE2 Address Derivation                          │
 │    • Periodic State Commitment Merkle Anchors                          │
 └────────────────────────────────────┬───────────────────────────────────┘
                                      │
 ┌────────────────────────────────────▼───────────────────────────────────┐
 │              Layer 1: Quantum-Resistant Base Ledger                    │
 │    • SPHINCS+-SHA2-256s Stateful Signatures (~30 KB)                   │
 │    • ML-KEM-768 Ephemeral Key Encapsulation (P2P Handshake)            │
 │    • SHA-512 Hashcash Proof-of-Work Consensus                          │
 │    • 128-Block Immature Coinbase Lockup & Auto-Staking Engine          │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## Cryptography Stack

| Layer | Algorithm | Role |
|---|---|---|
| **Signatures** | SPHINCS+-SHA2-256s (`pqcrypto.sign.sphincs_sha2_256s_simple`) | Post-quantum transaction authorization |
| **P2P Identity** | SPHINCS+-SHA2-256s | Long-lived node identity & handshake signing |
| **P2P Key Exchange** | ML-KEM-768 (`pqcrypto.kem.ml_kem_768`) | Ephemeral session key encapsulation |
| **P2P Transport** | AES-256-GCM | Encrypted directional tunnel with transcript binding |
| **Wallet Security** | bcrypt KDF (100 rounds) + AES-256-GCM | Encrypted passphrase-protected keystore |
| **Consensus / PoW** | SHA-512 | Block hashes, Merkle root, difficulty validation |
| **L2 / EVM Keccak** | Keccak-256 / SHA3 | EVM state trie, contract storage slots, CREATE2 |

Addresses use the `pvo1` prefix followed by the full 64-byte `SHA-512(sign_public_key)` digest in lowercase hex (128 hex characters). EVM compatibility addresses use standard `0x` 20-byte derived hex strings.

---

## Consensus & Economic Parameters

| Parameter | Value | Description |
|---|---|---|
| **Block Reward** | 3.0 PVO | 2.7 PVO coinbase reward + 0.3 PVO auto-staked |
| **Coin Unit** | 1 PVO = 10^8 Base Units | 8-decimal precision base ledger |
| **Minimum Fee** | 0.0001 PVO (10,000 base units) | Anti-spam fee floor |
| **Coinbase Maturity** | 128 Blocks (~1.8 days) | Immature mining rewards locked until deep confirmation |
| **Staking Lockup** | 128 Blocks | 10% auto-staked reward locked alongside maturity |
| **Max Reorg Depth** | 128 Blocks | Hard consensus boundary preventing deep reorg attacks |
| **Target Block Time** | 20 Minutes (1,200s) | Target inter-block creation interval |
| **Difficulty Retarget** | Every 32 Blocks | Clamped to a maximum 4x adjustment factor |
| **HTLC Atomic Swap** | 1 PVO = 10 CC | Deterministic swap peg with Chocohub |

---

## Project Layout

```
pacvo-blockchain-v1.0/
├── cli.py                  # Multi-subcommand CLI (run, wallet, evm, l2, l3, bridge, htlc, web, ccpow-miner)
├── MPG_Miner.py            # Chocohub CCpow standalone miner (CPU/GPU OpenCL support)
├── pacvo/
│   ├── params.py           # Core consensus parameters and resource limits
│   ├── crypto.py           # SPHINCS+, ML-KEM, AES-GCM, and addressing
│   ├── wallet.py           # bcrypt + AES-256-GCM encrypted keystore
│   ├── transaction.py      # Signed transactions and coinbase validation
│   ├── block.py            # Block structure, Merkle tree, and PoW hashing
│   ├── chain.py            # 128-block maturity tracker, headers-first reorg engine
│   ├── network.py          # Authenticated P2P protocol, TOFU pinning, JSON-RPC
│   ├── node.py             # Full node daemon, mempool simulation cache, sync loop
│   ├── miner.py            # SHA-512 block candidate builder & mining thread
│   ├── evm/                # Layer 2 EVM Execution Engine
│   │   ├── vm.py           # Bytecode interpreter, gas accounting, stack/memory
│   │   ├── opcodes.py      # Full opcode table, arithmetic, control flow, LOG, CREATE2
│   │   ├── precompiles.py  # EIP-198 ModExp, SHA256, Keccak256 precompiles
│   │   ├── state.py        # Journaled contract storage and rollback state trie
│   │   └── receipt.py      # Execution receipts and event logs
│   ├── l2/                 # Layer 2 Token & Asset Factory
│   │   ├── token.py        # ERC-20 token standard implementation
│   │   ├── nft.py          # ERC-721 NFT minting & transfer engine
│   │   ├── factory.py      # Deterministic contract deployment & management
│   │   └── anchor.py       # Layer 1 block root state commitments
│   └── l3/                 # Layer 3 PVO-Fi Financial Economy
│       ├── amm.py          # Constant-Product Automated Market Maker (DEX)
│       ├── lending.py      # Collateralized Debt Positions (150% MCR) & Liquidation
│       ├── bridge.py       # Multi-asset reserve bridges (BTC, Nano, Chocohub)
│       ├── htlc.py         # Trustless HTLC atomic swaps & CCpow solver
│       ├── reserve.py      # Proof-of-Reserve attestations (Genesis 4 POL Polygon)
│       ├── equity.py       # Tokenized equity shares & dividend claims
│       └── treasury.py     # Protocol fee management & reserve vaults
├── web/                    # Interactive Web Console Interface
│   ├── index.html          # Progressive disclosure dashboard & management console
│   ├── style.css           # Modern aesthetic design tokens & dark theme
│   ├── app.js              # Application state, RPC client, and financial modules
│   └── crypto_util.js      # Client-side Keccak256, CREATE2, and CCpow math
├── contracts/              # Solidity Smart Contracts
│   └── PacvoNFT.sol        # Genesis NFT ERC-721 collection contract
└── tests/                  # Complete Comprehensive Test Suite
    ├── test_crypto.py      # Post-quantum cryptographic primitive verification
    ├── test_chain.py       # 128-block maturity & consensus reorg tests
    ├── test_network.py     # P2P handshake, TOFU pinning, and sync tests
    ├── test_evm.py         # EVM opcode, stack, storage, and precompile tests
    ├── test_differential_evm.py # EVM fuzzing & determinism validation
    ├── test_l2.py          # ERC-20 & ERC-721 token factory tests
    ├── test_l3_economy.py  # AMM, lending, and proof-of-reserve tests
    ├── test_bridge.py      # Bitcoin, Nano, and Chocohub bridge adapter tests
    └── test_htlc.py        # Chocohub HTLC atomic swap & CCpow tests
```

---

## Installation & Setup

### 1. Prerequisites
- Python 3.11+
- Virtual environment (`venv`)
- Standard build tools

```bash
# Clone the repository
git clone https://github.com/smchuzza/pacvo-blockchain-v1.0.git
cd pacvo-blockchain-v1.0

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

---

## Quick Start & Usage

### 1. Create a Post-Quantum Wallet
```bash
.venv/bin/python cli.py wallet create --out wallet.json
# Enter passphrase when prompted
```

### 2. Start the Pacvo Full Node & Miner
```bash
export PACVO_WALLET_PASSPHRASE='your-passphrase'
.venv/bin/python cli.py run \
  --wallet wallet.json \
  --data data_node1 \
  --host 127.0.0.1 \
  --port 9442 \
  --mine
```

### 3. Launch the Web Console Interface
```bash
.venv/bin/python cli.py web --host 127.0.0.1 --port 8080
```
Open **[http://localhost:8080](http://localhost:8080)** in your browser to access the Web Console.

### 4. Run Chocohub CCpow Miner
```bash
# Run CCpow miner using environment variable for authentication
export CHOCO_PIN="your-account-pin"
.venv/bin/python cli.py ccpow-miner --worker pacvo15_476_wccpvo --threads 2
```

---

## Running the Automated Test Suite

Execute the full suite of unit and integration tests:

```bash
# Core Cryptography & Consensus Tests
.venv/bin/python tests/test_crypto.py
.venv/bin/python tests/test_chain.py
.venv/bin/python tests/test_network.py

# Layer 2 EVM & Token Factory Tests
.venv/bin/python tests/test_evm.py
.venv/bin/python tests/test_differential_evm.py
.venv/bin/python tests/test_l2.py
.venv/bin/python tests/test_nft.py

# Layer 3 PVO-Fi Economy, Bridges & Atomic Swap Tests
.venv/bin/python tests/test_l3_economy.py
.venv/bin/python tests/test_bridge.py
.venv/bin/python tests/test_htlc.py
```

---

## Security & Design Principles

- **Quantum Resistance**: SPHINCS+-SHA2-256s and ML-KEM-768 protect all signing and peer-to-peer transport against quantum cryptanalysis.
- **128-Block Reorg & Lockup Invariant**: Spendable coinbase rewards and auto-stakes remain strictly immature for 128 blocks (`COINBASE_MATURITY = MAX_REORG_DEPTH = 128`), preventing reward reversal vulnerabilities during chain reorganizations.
- **Strict Credential Isolation**: Account PINs, private keys, and passphrases remain isolated server-side/environment-side (`.gitignore` protected) and are never transmitted over frontend APIs.
- **Non-Blocking Threadpool Offloading**: CPU-intensive SPHINCS+ signing and verification tasks are dispatched to worker thread pools, preserving event loop responsiveness.

---

## License & Disclaimer

Pacvo is released for research and educational purposes. On networks with small aggregate hashrate, Proof-of-Work blockchains are subject to reorganizations up to `MAX_REORG_DEPTH`. Always exercise operational security when managing cryptographic keys.
