from invoice import Invoice, average_amount, summarize, total_amount

def test_existing_integer_invoice_report():
    rows = [Invoice("A", 4), Invoice("B", 8)]
    assert summarize(rows) == {"count": 2, "total": 12, "average": 6}
    assert total_amount([]) == 0
    assert average_amount([3]) == 3
