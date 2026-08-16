"""Unit and Integration Tests for L3 Lending, Debt, and Liquidation."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pacvo.l3.debt import DebtManager
from pacvo.l3.errors import UndercollateralizedError
from pacvo.l3.fixed import WAD
from pacvo.l3.lending import LendingPool


def test_lending_pool_utilization_and_interest():
    print("Testing Lending Pool Utilization & Dynamic Interest...")
    pool = LendingPool(symbol="POL", base_rate_bps=200, slope_rate_bps=1000)

    # 1. Deposit 1,000 POL
    shares = pool.deposit("0xalice", 1_000 * WAD)
    assert shares == 1_000 * WAD
    assert pool.total_supplied == 1_000 * WAD
    assert pool.calculate_utilization() == 0
    assert pool.calculate_borrow_rate_bps() == 200 # 2.00% base rate

    # 2. Borrow 500 POL (50% utilization)
    borrowed = pool.borrow("0xbob", 500 * WAD)
    assert borrowed == 500 * WAD
    assert pool.total_borrowed == 500 * WAD
    assert pool.calculate_utilization() == 5 * 10**17 # 0.5 WAD (50%)
    # Rate = 200 + 50% * 1000 = 700 bps (7.00%)
    assert pool.calculate_borrow_rate_bps() == 700

    # 3. Repay 200 POL
    repaid = pool.repay("0xbob", 200 * WAD)
    assert repaid == 200 * WAD
    assert pool.total_borrowed == 300 * WAD
    # Rate at 30% utilization = 200 + 30% * 1000 = 500 bps (5.00%)
    assert pool.calculate_borrow_rate_bps() == 500

    # 4. Withdraw
    withdrawn = pool.withdraw("0xalice", 200 * WAD)
    assert withdrawn == 200 * WAD

    print("  [PASS] Lending pool utilization and dynamic interest rates verified")


def test_debt_manager_and_liquidation():
    print("Testing Collateralized Positions & Deterministic Liquidation...")
    dm = DebtManager()

    # Deposit 150 POL collateral at $1.00 each to borrow 80 PUSD at $1.00 each
    # Collateral value = $150, Debt = $80. Ratio = 150 / 80 = 187.5% (> 150% min)
    pos = dm.open_or_modify_position(
        owner="0xborrower",
        collateral_symbol="POL",
        collateral_delta=150 * WAD,
        debt_symbol="PUSD",
        debt_delta=80 * WAD,
        collateral_price=WAD, # $1.00
        debt_price=WAD,       # $1.00
        current_height=100,
    )
    assert pos.collateral_amount == 150 * WAD
    assert pos.debt_amount == 80 * WAD
    assert not pos.is_liquidatable(collateral_price=WAD, debt_price=WAD)

    # Price of POL drops from $1.00 to $0.50!
    # Collateral value is now 150 * 0.50 = $75.
    # Adjusted collateral at 120% liq threshold = $75 * 1.20 = $90.
    # Debt value = $80. Health factor = 90 / 80 = 1.125 (> 1.0) -> still healthy
    assert not pos.is_liquidatable(collateral_price=5 * 10**17, debt_price=WAD)

    # Price of POL drops further to $0.40!
    # Collateral value is now 150 * 0.40 = $60.
    # Adjusted collateral = $60 * 1.20 = $72.
    # Health factor = 72 / 80 = 0.90 (< 1.0) -> LIQUIDATABLE!
    assert pos.is_liquidatable(collateral_price=4 * 10**17, debt_price=WAD)

    # Liquidator liquidates position: covers up to 50% of debt (40 PUSD)
    repaid_debt, seized_collateral = dm.liquidate(
        liquidator="0xliquidator",
        borrower="0xborrower",
        debt_to_cover=40 * WAD,
        collateral_price=4 * 10**17,
        debt_price=WAD,
    )
    assert repaid_debt == 40 * WAD
    # Seized collateral value = $40 * 1.05 = $42. Collateral at $0.40 = 42 / 0.40 = 105 POL
    assert seized_collateral == 105 * WAD
    assert pos.debt_amount == 40 * WAD
    assert pos.collateral_amount == 45 * WAD

    print("  [PASS] Collateralized positions, health factors, and liquidation verified")


def main():
    test_lending_pool_utilization_and_interest()
    test_debt_manager_and_liquidation()
    print("ALL L3 LENDING TESTS PASSED!")


if __name__ == "__main__":
    main()
