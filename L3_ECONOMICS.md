# Pacvo Layer 3 (L3) — PVO-Fi Economics & Mathematical Specifications

## 1. Fixed-Point Arithmetic Model

Consensus-critical financial computations must be 100% deterministic across all platforms. PVO-Fi uses fixed-point integer mathematics with the standard Ethereum WAD and RAY scales:

$$\text{WAD} = 10^{18} = 1,000,000,000,000,000,000$$
$$\text{RAY} = 10^{27} = 1,000,000,000,000,000,000,000,000,000$$

### 1.1 Fixed-Point Multiplication & Division
$$\text{wad\_mul}(x, y) = \left\lfloor \frac{x \cdot y + \frac{\text{WAD}}{2}}{\text{WAD}} \right\rfloor$$
$$\text{wad\_div}(x, y) = \left\lfloor \frac{x \cdot \text{WAD} + \frac{y}{2}}{y} \right\rfloor$$

---

## 2. Automated Market Maker (AMM) Mechanics

PVO-Fi implements a constant-product automated market maker with a default 30 bps (0.30%) fee.

### 2.1 Swap Output Formula
Given input $\Delta x$ into token reserve $x$ to receive token $y$ from reserve $y$:
$$\Delta x_{\text{fee}} = \Delta x \cdot (10000 - \text{fee\_bps})$$
$$\Delta y = \left\lfloor \frac{\Delta x_{\text{fee}} \cdot y}{x \cdot 10000 + \Delta x_{\text{fee}}} \right\rfloor$$

### 2.2 Liquidity Minting
When depositing $(a, b)$ into an AMM pool with total share supply $S$:
* **Initial Deposit ($S = 0$)**:
  $$S_{\text{minted}} = \lfloor \sqrt{a \cdot b} \rfloor$$
* **Subsequent Deposits**:
  $$S_{\text{minted}} = \min\left( \left\lfloor \frac{a \cdot S}{x} \right\rfloor, \left\lfloor \frac{b \cdot S}{y} \right\rfloor \right)$$

---

## 3. Lending & Dynamic Interest Rate Model

The lending protocol calculates interest rates dynamically as a function of utilization $U$:

### 3.1 Utilization Rate
$$U = \text{wad\_div}(\text{total\_borrowed}, \text{total\_supplied})$$

### 3.2 Borrow Rate Curve
$$R_{\text{borrow}} = R_{\text{base}} + \text{wad\_mul}(U, R_{\text{slope}})$$
* Example Parameters: $R_{\text{base}} = 2\% = 0.02 \times 10^{18}$, $R_{\text{slope}} = 10\% = 0.10 \times 10^{18}$.
* At $U = 80\%$, $R_{\text{borrow}} = 2\% + (0.80 \times 10\%) = 10.0\%$.

### 3.3 Interest Accrual over Block Intervals
$$\Delta \text{Interest} = \left\lfloor \frac{\text{Principal} \times R_{\text{borrow}} \times \Delta \text{Blocks}}{\text{BLOCKS\_PER\_YEAR} \times \text{WAD}} \right\rfloor$$

---

## 4. Scalable Cumulative Dividend Index

To distribute a dividend payout $P$ across total shares $S_{\text{total}}$:
1. **Dividend Declaration**:
   $$\Delta D = \text{wad\_div}(P, S_{\text{total}})$$
   $$D_{\text{cumulative}} = D_{\text{cumulative}} + \Delta D$$
2. **Claiming**:
   When user $i$ with share balance $B_i$ and recorded index $D_i$ claims:
   $$\text{Payout} = \text{wad\_mul}(B_i, D_{\text{cumulative}} - D_i)$$
   $$D_i \leftarrow D_{\text{cumulative}}$$

---

## 5. Tokenized Fund Net Asset Value (NAV)

A tokenized fund holds quantities $Q_1, Q_2, \dots, Q_n$ of underlying assets with prices $P_1, P_2, \dots, P_n$.
$$\text{Total Fund Value} = \sum_{j=1}^n \text{wad\_mul}(Q_j, P_j)$$
$$\text{NAV} = \text{wad\_div}(\text{Total Fund Value}, \text{Total Fund Shares})$$

---

## 6. PVO-Fi Genesis Reserve

* Default Genesis Reserve: **5 POL** ($5 \times 10^{18}$ base units).
* Backs initial liquidity bootstrapping, base swap pools, and reserve-backed synthetic liabilities.
