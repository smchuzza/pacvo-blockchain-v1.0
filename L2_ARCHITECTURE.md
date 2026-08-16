# Pacvo Layer 2 (L2) Architecture Specification

## 1. Architectural Scope & Taxonomy

Pacvo Layer 2 (L2) is the programmable token, asset, and application execution layer built directly on top of the Pacvo Layer 1 (L1) consensus protocol.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Layer 3: PVO-Fi Financial Economy                    │
│           (Simulated assets, equity, debt funds, capital markets)       │
│                              [FUTURE PHASE]                             │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────┐
│                    Layer 2: Programmable Asset Layer                    │
│   • ERC-20 Fungible Tokens (Fixed Supply & Controlled Mint)             │
│   • Memecoins & Programmable Digital Assets                             │
│   • EVM Smart Contract State Execution & Rollback Journal               │
│   • Periodic L1 State Anchoring & Deterministic Sequence Roots          │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────┐
│                     Layer 1: Consensus & Settlement                     │
│   • SPHINCS+ Post-Quantum Transaction Signatures                        │
│   • Kyber-1024 / ChaCha20-Poly1305 Encrypted P2P Transport             │
│   • Proof-of-Work / Proof-of-Stake-Locking Hybrid Consensus             │
│   • Native PVO Ledger & Block Ordering                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### Protocol Classification Disclaimer
Pacvo L2 in this phase is strictly an **L1-Anchored Programmable Asset & Contract Layer**.
- It is **not** a ZK-Rollup (no zero-knowledge validity proofs are generated).
- It is **not** an Optimistic Rollup (no fraud-proof dispute period is enforced against an external bridge).
- It is an EVM-native execution and token state engine whose canonical ordering, state mutations, and finality are strictly anchored to and governed by Pacvo L1 blocks.

---

## 2. Responsibilities Breakdown

### 2.1 Layer 1 (L1) Responsibilities
1. **Consensus & Ordering**: Produces canonical, cryptographically ordered blocks via PoW and PoS-Locking.
2. **Post-Quantum Security**: Authenticates all incoming transactions via SPHINCS+ post-quantum signatures.
3. **Settlement**: Manages native PVO balances, block rewards, staking maturity locks, and peer-to-peer network security.
4. **Data Availability**: Broadcasts and stores all raw transaction calldata and execution parameters on-chain.

### 2.2 Layer 2 (L2) Responsibilities
1. **Asset Virtualization**: Manages fungible tokens, memecoins, and custom application state via standard ERC-20 interfaces.
2. **EVM Execution**: Deterministically processes bytecode instructions, maintains stack, memory, and persistent contract storage.
3. **State Anchoring**: Computes sequential state commitments and anchors L2 state snapshots to canonical L1 block heights and block hashes.
4. **Reorganization Replay**: Provides deterministic state rollback and replay mechanics whenever an L1 reorganization occurs.

---

## 3. Transaction Flow & State Ownership

```
[User / Wallet]
       │
       │ 1. Signs Transaction with SPHINCS+ (containing evm_to, evm_data, gas_limit)
       ▼
[Pacvo L1 Node P2P Mempool]
       │
       │ 2. Validates Signature, Nonce, Balance, & Admits to Block
       ▼
[Block Inclusion & Mining]
       │
       │ 3. Block validated and appended to canonical Blockchain
       ▼
[Pacvo L2 Execution Engine]
       │
       ├─► Executes Contract Bytecode via Pacvo EVM
       ├─► Mutates L2 Account Storage & Token Balances
       ├─► Emits Event Logs (Transfer, Approval)
       └─► Generates Deterministic Execution Receipt
       │
       ▼
[L1 State Anchor & Sequence Update]
       │
       └─► Links L2 State Root to L1 (Height, Block Hash, Sequence)
```

1. **Transaction Submission**: A user constructs an L2 transaction targeting a token contract (or deployment initcode). The transaction is signed using the sender's SPHINCS+ private key.
2. **L1 Ordering**: The transaction is broadcast across the P2P network and included into an L1 block.
3. **EVM Execution**: Upon block application, `_apply_non_coinbase_tx` delegates execution to the Pacvo EVM engine with the caller mapped from the SPHINCS+ public key to their canonical 20-byte EVM address ($\text{Keccak-256}(PK)[12:]$).
4. **L2 State Transition**: The EVM executes the bytecode, updating storage slots, token ledger mappings, nonces, and emitting indexed event logs.

---

## 4. Token Standards & Lifecycle

All L2 tokens conform to the standard ERC-20 ABI interface:

### 4.1 Interface Specification
| Method | Signature | Selector |
| :--- | :--- | :--- |
| `totalSupply()` | `0x18160ddd` | `totalSupply()` |
| `balanceOf(address)` | `0x70a08231` | `balanceOf(address)` |
| `transfer(address,uint256)` | `0xa9059cbb` | `transfer(address,uint256)` |
| `allowance(address,address)` | `0xdd62ed3e` | `allowance(address,address)` |
| `approve(address,uint256)` | `0x095ea7b3` | `approve(address,uint256)` |
| `transferFrom(address,address,uint256)` | `0x23b872dd` | `transferFrom(address,address,uint256)` |
| `name()` | `0x06fdde03` | `name()` |
| `symbol()` | `0x95d89b41` | `symbol()` |
| `decimals()` | `0x313ce567` | `decimals()` |
| `mint(address,uint256)` | `0x40c10f19` | `mint(address,uint256)` |
| `burn(uint256)` | `0x42966c68` | `burn(uint256)` |

### 4.2 Supported Token Archetypes
1. **Fixed Supply (`FIXED_SUPPLY`)**:
   - Initial supply is minted entirely to the deployer in the constructor.
   - The minting mechanism is absent from the runtime bytecode; total supply is immutable.
2. **Controlled Mint (`CONTROLLED_MINT`)**:
   - Stores an authorized `minter` address in contract storage (Slot 1).
   - Only the designated `minter` can invoke `mint(to, amount)`.
   - Any token holder can invoke `burn(amount)` to destroy their tokens.
3. **Memecoins**:
   - Parameterized tokens with custom ticker, name, and total supply.
   - Fully tradable and compatible with standard automated market makers and DEX vaults.

---

## 5. Reorganization & Replay Mechanics

L2 state is strictly a pure function of the canonical L1 block sequence:

$$\text{L2State}_N = \mathcal{F}(\text{GenesisState}, \text{Block}_1, \text{Block}_2, \dots, \text{Block}_N)$$

When an L1 fork reorganization occurs (up to `MAX_REORG_DEPTH = 128` blocks):
1. **Orphan Reversion**: Transactions in orphaned L1 blocks are discarded from canonical state.
2. **State Rollback**: The blockchain rewinds state to the fork height `fork_height` via `_rebuild_state()`.
3. **Canonical Replay**: Transactions in the new canonical chain are sequentially executed through the EVM.
4. **Deterministic Convergence**: Because EVM execution is completely deterministic, the resulting L2 token balances, allowances, and contract storage match across all synced nodes without state divergence.

---

## 6. Future Integration Points

- **DEX & Automated Market Makers (AMM)**: Factory contracts, liquidity pair contracts (`Pair`), and swap routers (`Router`) will execute as native L2 EVM smart contracts.
- **Layer 3 (PVO-Fi Economy)**: Tokenized financial instruments, debt notes, credit facilities, and synthetic equities will settle against L2 ERC-20 token reserves.
