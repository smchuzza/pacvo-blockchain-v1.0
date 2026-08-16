# Pacvo Layer 3 (L3) — PVO-Fi RPC & CLI Reference Manual

## 1. JSON-RPC P2P Methods (`pacvo_l3_*`)

All L3 methods are accessible over the encrypted P2P RPC interface.

### 1.1 `pacvo_l3_getAsset`
* **Request**: `{"asset_id": "PVOA"}`
* **Response**: Metadata including `symbol`, `name`, `token_address`, `asset_type`, `total_supply`, `issuer`, and status.

### 1.2 `pacvo_l3_getEquity`
* **Request**: `{"symbol": "PVOA"}`
* **Response**: Equity metadata, cumulative dividend per share index, and pending dividend pool balances.

### 1.3 `pacvo_l3_getBond`
* **Request**: `{"symbol": "PVOBOND"}`
* **Response**: Face value, principal, coupon rate, coupon interval, issue height, and maturity height.

### 1.4 `pacvo_l3_getDebt`
* **Request**: `{"borrower": "0x...", "asset": "PVOUSD"}`
* **Response**: Total borrowed principal, accrued interest, collateral asset, and collateral amount.

### 1.5 `pacvo_l3_getFund`
* **Request**: `{"symbol": "PVOFUND"}`
* **Response**: Basket allocations, asset quantities, total fund shares, and current NAV.

### 1.6 `pacvo_l3_getMarket`
* **Request**: `{"pair": "PVOA/POL"}`
* **Response**: Reserve A, Reserve B, total LP shares, fee bps, and spot price.

### 1.7 `pacvo_l3_getPosition`
* **Request**: `{"owner": "0x..."}`
* **Response**: Active collateralized lending/borrowing positions, health factor, and liquidation risk.

### 1.8 `pacvo_l3_getTreasury`
* **Request**: `{}`
* **Response**: Treasury cash reserves, accumulated protocol fees, and outstanding bond/dividend commitments.

### 1.9 `pacvo_l3_getReserve`
* **Request**: `{}`
* **Response**: PVO-Fi 5 POL reserve balance, total issued liabilities, backing ratio, and available liquidity.

### 1.10 `pacvo_l3_getNAV`
* **Request**: `{"symbol": "PVOFUND"}`
* **Response**: Calculated Net Asset Value per share formatted in fixed-point integer and decimal.

### 1.11 `pacvo_l3_getEpoch`
* **Request**: `{}`
* **Response**: Current economic epoch index, epoch duration (blocks), and epoch transition height.

### 1.12 `pacvo_l3_getStateRoot`
* **Request**: `{}`
* **Response**: Canonical 32-byte Keccak-256 state commitment over all L3 economic subsystems.

### 1.13 `pacvo_l3_getEconomy`
* **Request**: `{}`
* **Response**: Full summary snapshot of all registered assets, markets, lending pools, and reserve statistics.

---

## 2. Command Line Interface (`cli.py`)

### 2.1 Asset & Economy Management
```bash
# Query full economic status
python cli.py l3 economy status --node 127.0.0.1:9333

# Query PVO-Fi Reserve Backing (5 POL default)
python cli.py l3 reserve info --node 127.0.0.1:9333

# Query L3 State Root Commitment
python cli.py l3 anchor info --node 127.0.0.1:9333
```

### 2.2 Equities & Dividends
```bash
# Issue simulated equity
python cli.py l3 equity issue --wallet wallet.json --symbol PVOA --name "Pacvo Alpha Equity" --shares 1000000 --node 127.0.0.1:9333

# Declare dividend
python cli.py l3 equity dividend --wallet wallet.json --symbol PVOA --amount 10000 --node 127.0.0.1:9333

# Claim earned dividend
python cli.py l3 equity claim --wallet wallet.json --symbol PVOA --node 127.0.0.1:9333
```

### 2.3 Bonds & Coupons
```bash
# Issue bond
python cli.py l3 bond issue --wallet wallet.json --symbol PVOBOND --face-value 1000 --rate 500 --interval 100 --maturity 1000 --node 127.0.0.1:9333

# Claim coupon yield
python cli.py l3 bond coupon --wallet wallet.json --symbol PVOBOND --node 127.0.0.1:9333

# Redeem matured principal
python cli.py l3 bond redeem --wallet wallet.json --symbol PVOBOND --node 127.0.0.1:9333
```

### 2.4 AMM Markets & Trading
```bash
# Query market liquidity and spot price
python cli.py l3 market info --pair PVOA/POL --node 127.0.0.1:9333

# Add liquidity
python cli.py l3 market add-liquidity --wallet wallet.json --tokenA PVOA --tokenB POL --amountA 1000 --amountB 500 --node 127.0.0.1:9333

# Execute swap
python cli.py l3 market swap --wallet wallet.json --tokenIn POL --tokenOut PVOA --amountIn 50 --minOut 90 --node 127.0.0.1:9333
```

### 2.5 Lending & Borrowing
```bash
# Deposit collateral
python cli.py l3 lend deposit --wallet wallet.json --asset POL --amount 100 --node 127.0.0.1:9333

# Borrow against collateral
python cli.py l3 borrow take --wallet wallet.json --asset PUSD --amount 50 --collateral POL --collateral-amount 100 --node 127.0.0.1:9333

# Repay debt
python cli.py l3 borrow repay --wallet wallet.json --asset PUSD --amount 50 --node 127.0.0.1:9333
```
