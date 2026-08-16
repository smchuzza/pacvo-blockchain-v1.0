# Pacvo Layer 3 (L3) — PVO-Fi Security & Invariant Review

## 1. Threat Modeling & Attack Vectors

The PVO-Fi simulated economic layer handles multi-asset state transitions, financial derivatives, automated market making, and credit facilities. This document defines the security boundaries, role-based access control, and mathematical invariant checks.

---

## 2. Access Control & Role System

PVO-Fi implements explicit, permissioned roles across all administrative actions:

| Role | Scope / Permissions |
| :--- | :--- |
| `GOVERNANCE` | Global economic parameters, epoch duration, fee structures, protocol pause/resume. |
| `TREASURY` | Managing protocol reserves, declaring dividends, funding coupon obligations. |
| `ISSUER` | Deploying authorized equities, issuing tokenized bonds, and registering basket funds. |
| `MARKET_OPERATOR` | Initializing AMM liquidity pairs and configuring allowable slippage parameters. |
| `LIQUIDATOR` | Triggering liquidation on undercollateralized positions. |

Unauthorized invocations of role-protected functions revert immediately with `UnauthorizedError`.

---

## 3. Core Invariant Verifications

### 3.1 Token Conservation Invariant
For every token $T$ in the system:
$$\sum_{a \in \text{Accounts}} \text{balanceOf}(a) = \text{totalSupply}(T)$$
Tokens cannot be arbitrarily created or burned without explicit, authenticated protocol operations.

### 3.2 AMM Constant-Product Invariant
For every active trading pair $(A, B)$ with reserves $(R_A, R_B)$:
$$(R_A + \Delta A \cdot (1 - \text{fee})) \cdot (R_B - \Delta B) \ge R_A \cdot R_B$$
Reserves $R_A, R_B > 0$ must remain strictly positive. Trades with zero liquidity, division by zero, or negative outputs revert.

### 3.3 Lending Pool Solvency Invariant
For every lending pool:
$$\text{Supplied Liquidity} = \text{Available Cash} + \text{Total Borrowed}$$
$$\text{Available Cash} \ge 0, \quad \text{Total Borrowed} \ge 0$$
Borrowing cannot exceed available unreserved cash in the pool.

### 3.4 Collateral & Health Factor Invariant
For any active borrow position:
$$\text{Health Factor} = \frac{\text{Collateral Value} \times \text{Liquidation Threshold}}{\text{Total Debt Value}}$$
A position with $\text{Health Factor} \ge 1.0$ is immune from liquidation. If $\text{Health Factor} < 1.0$, a liquidator may repay up to 50% (close factor) of the debt to seize an equivalent value of collateral plus a deterministic liquidation bonus (e.g. 5%).

### 3.5 PVO-Fi Reserve Solvency Invariant
$$\text{Available Reserve} = \text{Total Reserve (5 POL default)} - \text{Locked Commitments} \ge 0$$
$$\text{Backing Ratio} = \frac{\text{Total Reserve Value}}{\text{Total Issued Liabilities}} \ge 1.0$$
No issuance of reserve-backed obligations may proceed if the available reserve is insufficient.

### 3.6 Scalable Dividend & Coupon Claim Invariant
* Cumulative indices prevent double claiming: $\text{claimed} \le \text{claimable}$.
* Claims atomically decrement the distribution pool and credit the claimant's EVM token balance.

---

## 4. Arithmetic & Rounding Discipline

* Fixed-point calculations operate on 18-decimal integers ($1\text{ WAD} = 10^{18}$).
* Divisions favor protocol solvency:
  - When minting shares (LP tokens, fund shares): round **down** (floor division).
  - When burning shares (withdrawing underlying collateral): round **down** (floor division).
  - When calculating debt/interest owed to protocol: round **up** (ceil division).

---

## 5. Reorganization Determinism

During L1 reorganizations, L3 state is rolled back and replayed through the canonical EVM transaction log. Because no external mutable state (system clocks, floating point numbers, random seeds, or external APIs) is used, state roots converge with 100% determinism.
