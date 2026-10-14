AlphaTrace intraday kit — ALL stocks in stocks.csv (EQ + BE), 5m / 15m / 1h
  intraday.py                       next to yahoo_ohlcv.py (it reuses its stock reader)
  .github/workflows/intraday.yml    the scheduled job

First run (Actions -> "Intraday bars (Yahoo)" -> Run workflow):
  1) days=59   intervals=5m,15m      2) days=700  intervals=1h
Then it runs every weekday 16:15 IST with days=5.
Retention: NOTHING is deleted — every day file stays on GitHub forever (5m/15m start ~59 days back, 1h ~700 days, and grow each day).
Size: roughly 3-4 MB of new files per day for ~2,400 stocks (5m is the bulk), i.e. ~1 GB/year. To prune later add e.g. --keep 5m=90 to the fetch line.
To limit symbols instead:  python intraday.py pick --float STOCKS_FLOAT.csv --top 800  (writes intraday_symbols.csv, then used).
