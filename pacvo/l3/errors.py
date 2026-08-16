"""Pacvo Layer 3 (L3) Domain Errors and Exceptions."""


class L3Error(Exception):
    """Base exception for all Layer 3 PVO-Fi errors."""
    pass


class InsufficientReserveError(L3Error):
    """Raised when available reserve backing is insufficient."""
    pass


class UndercollateralizedError(L3Error):
    """Raised when a position violates required collateralization thresholds."""
    pass


class UnauthorizedError(L3Error):
    """Raised when caller lacks required role permissions."""
    pass


class DivisionByZeroError(L3Error):
    """Raised on zero division in economic calculations."""
    pass


class SlippageExceededError(L3Error):
    """Raised when trade execution output is less than specified minimum."""
    pass


class MaturityError(L3Error):
    """Raised when accessing a bond before or after valid maturity bounds."""
    pass


class DuplicateAssetError(L3Error):
    """Raised when registering an asset with an already existing symbol."""
    pass


class InvariantViolationError(L3Error):
    """Raised when a core economic invariant is violated."""
    pass
