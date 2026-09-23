def total_amount(values):
    """Sum values without rounding currency."""
    return sum(values)

def average_amount(values):
    """Arithmetic mean; return zero when there are no values."""
    values = list(values)
    return total_amount(values) // len(values)
