"""
core/decimal_utils.py — Financial Precision Utilities.

STEP 5.2: Decimal System Migration
Provides consistent Decimal precision for all financial calculations.

Financial precision is mandatory to avoid:
- Floating-point drift in position calculations
- Accumulated rounding errors in PnL
- Mismatched order sizes

DecimalContext(precision=18) provides:
- 18 significant digits (more than sufficient for crypto/finance)
- Consistent rounding behavior across the codebase
- No floating-point representation errors
"""

from decimal import Decimal, Context, ROUND_HALF_UP, setcontext
from typing import Union

# ═══════════════════════════════════════════════════════════════════════════════
# FINANCIAL PRECISION CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════

# STEP 5.2: Financial precision context
# - 18 significant digits (sufficient for 8+ decimal place cryptos)
# - ROUND_HALF_UP for standard financial rounding
FINANCIAL_PRECISION = Context(prec=18, rounding=ROUND_HALF_UP)

# Global decimal context for all financial operations
setcontext(FINANCIAL_PRECISION)


# ═══════════════════════════════════════════════════════════════════════════════
# CONVERSION UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def to_decimal(value: Union[str, float, int, Decimal]) -> Decimal:
    """
    Convert any numeric value to Decimal with financial precision.
    
    CRITICAL: Always use this for financial conversions to avoid
    floating-point representation errors.
    
    Args:
        value: String, float, int, or Decimal to convert
        
    Returns:
        Decimal with 18-digit precision
        
    Examples:
        >>> to_decimal("100.50")
        Decimal('100.50')
        >>> to_decimal(0.1)  # float 0.1 is actually 0.10000000000000000555...
        Decimal('0.1')  # But we get clean Decimal
        >>> to_decimal(100)
        Decimal('100')
    """
    if isinstance(value, Decimal):
        return value
    elif isinstance(value, str):
        return Decimal(value, FINANCIAL_PRECISION)
    elif isinstance(value, float):
        # Convert via string to avoid float representation errors
        return Decimal(str(value), FINANCIAL_PRECISION)
    elif isinstance(value, int):
        return Decimal(value, FINANCIAL_PRECISION)
    else:
        raise TypeError(f"Cannot convert {type(value)} to Decimal")


def to_str(value: Decimal, normalize: bool = False) -> str:
    """
    Convert Decimal to string for storage/serialization.
    
    Args:
        value: Decimal value
        normalize: If True, remove trailing zeros
        
    Returns:
        String representation
    """
    if normalize:
        return format(value.normalize(), 'f')
    return format(value, 'f')


def quantize(value: Decimal, exponent: str = '0.00000001') -> Decimal:
    """
    Quantize Decimal to specific precision (e.g., 8 decimal places for crypto).
    
    Args:
        value: Decimal to quantize
        exponent: Target precision as string (default: 8 decimal places)
        
    Returns:
        Quantized Decimal
    """
    return value.quantize(Decimal(exponent), rounding=ROUND_HALF_UP)


# ═══════════════════════════════════════════════════════════════════════════════
# FINANCIAL CALCULATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_pnl(
    entry_price: Union[str, float, Decimal],
    exit_price: Union[str, float, Decimal],
    size: Union[str, float, Decimal],
    side: str  # "long" or "short"
) -> Decimal:
    """
    Calculate realized PnL with financial precision.
    
    Long:  (exit - entry) * size
    Short: (entry - exit) * size
    
    Args:
        entry_price: Entry price
        exit_price: Exit price
        size: Position size
        side: "long" or "short"
        
    Returns:
        Decimal PnL value
    """
    entry = to_decimal(entry_price)
    exit = to_decimal(exit_price)
    qty = to_decimal(size)
    
    if side.lower() == "long":
        return (exit - entry) * qty
    else:  # short
        return (entry - exit) * qty


def calculate_notional(
    price: Union[str, float, Decimal],
    size: Union[str, float, Decimal]
) -> Decimal:
    """
    Calculate notional value with financial precision.
    
    Args:
        price: Price per unit
        size: Quantity
        
    Returns:
        Decimal notional value
    """
    return to_decimal(price) * to_decimal(size)


def calculate_position_size_from_notional(
    notional: Union[str, float, Decimal],
    price: Union[str, float, Decimal]
) -> Decimal:
    """
    Calculate position size from notional value.
    
    Args:
        notional: Total notional value
        price: Price per unit
        
    Returns:
        Decimal position size
    """
    notional_dec = to_decimal(notional)
    price_dec = to_decimal(price)
    
    if price_dec == 0:
        return Decimal('0')
    
    return notional_dec / price_dec


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def is_positive(value: Union[str, float, Decimal]) -> bool:
    """Check if value is positive (> 0)."""
    return to_decimal(value) > 0


def is_non_negative(value: Union[str, float, Decimal]) -> bool:
    """Check if value is non-negative (>= 0)."""
    return to_decimal(value) >= 0


def validate_price(value: Union[str, float, Decimal]) -> Decimal:
    """
    Validate and convert price to Decimal.
    
    Raises:
        ValueError: If price is not positive
    """
    price = to_decimal(value)
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}")
    return price


def validate_size(value: Union[str, float, Decimal]) -> Decimal:
    """
    Validate and convert size to Decimal.
    
    Raises:
        ValueError: If size is not positive
    """
    size = to_decimal(value)
    if size <= 0:
        raise ValueError(f"Size must be positive, got {size}")
    return size


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Common precision levels
PRECISION_2DP = Decimal('0.01')           # 2 decimal places (fiat currencies)
PRECISION_4DP = Decimal('0.0001')        # 4 decimal places
PRECISION_6DP = Decimal('0.000001')      # 6 decimal places
PRECISION_8DP = Decimal('0.00000001')    # 8 decimal places (most cryptos)
PRECISION_18DP = Decimal('0.000000000000000001')  # 18 decimal places (ETH, ERC-20)

# Zero as Decimal
ZERO = Decimal('0')
ONE = Decimal('1')
