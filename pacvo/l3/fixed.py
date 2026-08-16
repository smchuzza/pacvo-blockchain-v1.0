"""Fixed-Point Math Engine (18-decimal WAD & 27-decimal RAY precision).

Guarantees 100% deterministic integer arithmetic across all platforms.
Prohibits floating-point math in consensus-critical economic state.
"""

from pacvo.l3.errors import DivisionByZeroError

WAD = 10**18
HALF_WAD = WAD // 2

RAY = 10**27
HALF_RAY = RAY // 2

PERCENT_BPS = 10_000 # 100.00% = 10,000 bps


def to_wad(n: int) -> int:
    """Convert an integer to WAD scale (e.g. 5 -> 5 * 10^18)."""
    return n * WAD


def from_wad(n: int) -> int:
    """Convert WAD scale to floor integer."""
    return n // WAD


def wad_mul(x: int, y: int) -> int:
    """Fixed-point multiplication with standard rounding: floor((x * y + WAD/2) / WAD)."""
    return (x * y + HALF_WAD) // WAD


def wad_mul_ceil(x: int, y: int) -> int:
    """Fixed-point multiplication rounded UP (favors protocol solvency): ceil((x * y) / WAD)."""
    if x == 0 or y == 0:
        return 0
    return (x * y + WAD - 1) // WAD


def wad_div(x: int, y: int) -> int:
    """Fixed-point division with standard rounding: floor((x * WAD + y/2) / y)."""
    if y == 0:
        raise DivisionByZeroError("Division by zero in wad_div")
    return (x * WAD + y // 2) // y


def wad_div_ceil(x: int, y: int) -> int:
    """Fixed-point division rounded UP (favors protocol solvency): ceil((x * WAD) / y)."""
    if y == 0:
        raise DivisionByZeroError("Division by zero in wad_div_ceil")
    if x == 0:
        return 0
    return (x * WAD + y - 1) // y


def ray_mul(x: int, y: int) -> int:
    """27-decimal RAY fixed-point multiplication."""
    return (x * y + HALF_RAY) // RAY


def ray_div(x: int, y: int) -> int:
    """27-decimal RAY fixed-point division."""
    if y == 0:
        raise DivisionByZeroError("Division by zero in ray_div")
    return (x * RAY + y // 2) // y


def bps_mul(amount: int, bps: int) -> int:
    """Multiply an integer amount by basis points (1 bp = 0.01%)."""
    return (amount * bps) // PERCENT_BPS


def isqrt(n: int) -> int:
    """Deterministic integer square root (Newton's method)."""
    if n < 0:
        raise ValueError("Square root of negative number")
    if n == 0:
        return 0
    x = int(1) << ((n.bit_length() + 1) // 2)
    while True:
        y = (x + n // x) // 2
        if y >= x:
            return x
        x = y


def wad_sqrt(x: int) -> int:
    """Fixed-point square root: sqrt(x * WAD)."""
    return isqrt(x * WAD)
