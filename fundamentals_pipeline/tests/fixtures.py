CR = 1e7
def stmt(items):
    return {k: {d: v for d, v in row.items()} for k, row in items.items()}

QD = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]   # newest first
AD = ["2026-03-31", "2025-03-31", "2024-03-31", "2023-03-31"]

def good_raw(**over):
    raw = {
        "info": {"financialCurrency": "INR", "currentPrice": 500.0, "marketCap": 500.0 * 100_000_000, "sharesOutstanding": 100_000_000,
                 "bookValue": 250.0, "trailingEps": 11.1, "heldPercentInsiders": 0.55, "heldPercentInstitutions": 0.2,
                 "dividendRate": 5.0, "payoutRatio": 0.12, "longName": "Test Co"},
        "q_income": stmt({
            "Total Revenue": dict(zip(QD, [300*CR, 280*CR, 270*CR, 260*CR, 250*CR])),
            "Net Income": dict(zip(QD, [30*CR, 28*CR, 27*CR, 26*CR, 25*CR])),
            "Operating Income": dict(zip(QD, [60*CR, 56*CR, 54*CR, 52*CR, 50*CR])),
            "EBIT": dict(zip(QD, [58*CR, 54*CR, 52*CR, 50*CR, 48*CR])),
            "Interest Expense": dict(zip(QD, [-4*CR, -4*CR, -4*CR, -4*CR, -4*CR])),
            "Diluted EPS": dict(zip(QD, [3.0, 2.8, 2.7, 2.6, 2.5])),
        }),
        "a_income": stmt({
            "Total Revenue": dict(zip(AD, [1050*CR, 900*CR, 800*CR, 700*CR])),
            "Net Income": dict(zip(AD, [105*CR, 90*CR, 75*CR, 60*CR])),
            "Diluted EPS": dict(zip(AD, [10.5, 9.0, 7.5, 6.0])),
            "Operating Income": dict(zip(AD, [210*CR, 180*CR, 150*CR, 120*CR])),
            "Total Expenses": dict(zip(AD, [840*CR, 720*CR, 650*CR, 580*CR])),
        }),
        "q_balance": {}, 
        "a_balance": stmt({
            "Stockholders Equity": {"2026-03-31": 2500*CR, "2025-03-31": 2200*CR},
            "Total Debt": {"2026-03-31": 500*CR, "2025-03-31": 450*CR},
            "Cash Cash Equivalents And Short Term Investments": {"2026-03-31": 200*CR},
            "Total Assets": {"2026-03-31": 4000*CR},
            "Current Liabilities": {"2026-03-31": 1000*CR},
            "Ordinary Shares Number": {"2026-03-31": 100_000_000},
        }),
        "a_cash": stmt({
            "Operating Cash Flow": {"2026-03-31": 120*CR, "2025-03-31": 100*CR},
            "Investing Cash Flow": {"2026-03-31": -70*CR, "2025-03-31": -60*CR},
            "Financing Cash Flow": {"2026-03-31": -30*CR, "2025-03-31": -20*CR},
            "Capital Expenditure": {"2026-03-31": -40*CR, "2025-03-31": -35*CR},
        }),
    }
    for k, v in over.items():
        raw[k] = v
    return raw

META = {"name": "Test Co Ltd", "isin": "INE000A01010", "macro": "Industrials", "sector": "Capital Goods", "industry": "Machinery"}
