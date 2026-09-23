from .statistics import average_amount, total_amount

def summarize(invoices):
    invoices = list(invoices)
    values = [invoice.amount for invoice in invoices]
    return {"count": len(values), "total": total_amount(values),
            "average": average_amount(values)}
