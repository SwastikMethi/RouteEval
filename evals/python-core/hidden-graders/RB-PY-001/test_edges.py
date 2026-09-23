from invoice import Invoice, average_amount, summarize

def test_empty_and_single_pass_iterables():
    assert average_amount([]) == 0
    assert average_amount(iter(())) == 0
    assert average_amount(value for value in [1, 2, 5]) == 8 / 3

def test_input_and_report_invariants():
    values = [4, -2, 7]
    before = values[:]
    assert average_amount(values) == 3
    assert values == before
    assert summarize([]) == {"count": 0, "total": 0, "average": 0}
    assert summarize([Invoice("a", 2), Invoice("b", 3)])["average"] == 2.5
