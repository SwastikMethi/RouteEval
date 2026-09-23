from decimal import Decimal
from random import Random
from invoice import average_amount

def test_fractional_and_negative_means():
    rng = Random(107)
    for _ in range(25):
        values = [rng.randrange(-80, 100) for _ in range(rng.randrange(2, 8))]
        assert average_amount(values) == sum(values) / len(values)
    assert average_amount([1.25, 2.5]) == 1.875

def test_decimal_precision():
    values = [Decimal("999999999999.01"), Decimal("0.02")]
    result = average_amount(values)
    assert isinstance(result, Decimal)
    assert result == sum(values) / len(values)
