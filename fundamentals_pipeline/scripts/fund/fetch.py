"""Yahoo Finance provider. Kept tiny and swappable: anything with .get(symbol) -> raw dict works
(tests inject a fake). Statements are converted to plain {line item: {YYYY-MM-DD: value}} dicts."""
from __future__ import annotations
import time

def frame_to_dict(df) -> dict:
    out = {}
    if df is None or getattr(df, "empty", True):
        return out
    for item in df.index:
        row = {}
        for col in df.columns:
            v = df.at[item, col]
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f == f:  # not NaN
                row[str(col)[:10]] = f
        if row:
            out[str(item)] = row
    return out


class RateLimited(Exception):
    pass


class YahooProvider:
    def __init__(self, suffix=".NS", retries=4, base_delay=3.0, sleep=time.sleep):
        import yfinance as yf
        self.yf, self.suffix, self.retries, self.base_delay, self.sleep = yf, suffix, retries, base_delay, sleep

    def _once(self, symbol):
        t = self.yf.Ticker(symbol + self.suffix)
        info = t.info or {}
        if not info or (info.get("quoteType") in (None, "NONE") and not info.get("longName")):
            raise LookupError("no data for " + symbol)
        return {
            "info": {k: info.get(k) for k in (
                "financialCurrency", "currentPrice", "regularMarketPrice", "marketCap", "sharesOutstanding",
                "bookValue", "trailingEps", "heldPercentInsiders", "heldPercentInstitutions",
                "dividendRate", "payoutRatio", "longName", "quoteType")},
            "q_income": frame_to_dict(t.quarterly_income_stmt),
            "a_income": frame_to_dict(t.income_stmt),
            "q_balance": frame_to_dict(t.quarterly_balance_sheet),
            "a_balance": frame_to_dict(t.balance_sheet),
            "a_cash": frame_to_dict(t.cashflow),
        }

    def get(self, symbol):
        last = None
        for i in range(self.retries):
            try:
                return self._once(symbol)
            except LookupError:
                raise
            except Exception as e:  # network / 429 / transient parse failures
                last = e
                self.sleep(self.base_delay * (2 ** i))
        raise RateLimited(f"{symbol}: {last}")
