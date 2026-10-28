"""Money helpers.

Amounts are stored as integer cents so balances never suffer floating point
rounding errors. The API accepts and returns amounts in major units
(e.g. 10.50) with at most 2 decimal places.
"""
from decimal import Decimal


def to_cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def from_cents(cents: int) -> float:
    return cents / 100
