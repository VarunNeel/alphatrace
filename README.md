# AlphaTrace GitHub kit

Put these in ONE public GitHub repo, together with your `stocks.csv` (columns SYMBOL,SERIES).

| File | Where it goes | What it makes |
|---|---|---|
| `stocks.csv` | repo root | your input list (EQ and BE rows are used) |
| `fundamentals.py` + `fundamentals.yml` | root + `.github/workflows/` | `fundamentals/nse_classification.csv`, `STOCKS_FLOAT.csv`, `universe.csv`, `unmatched.csv` (monthly) |
| `yahoo_ohlcv.py` + `ohlcv_daily.yml` | root + `.github/workflows/` | `ohlcv/DAILY_OHLCV.csv` (latest day) + `ohlcv/daily/YYYY-MM-DD.csv` (weekdays 9 pm IST) |
| `yahoo_ohlcv.py` + `ohlcv_history.yml` | root + `.github/workflows/` | `ohlcv/history/HIST_OHLCV_<year>.csv.gz` (run once by hand; the app opens .gz directly) |
| `fetch_eod.py` + `eod.yml` | root + `.github/workflows/` | optional NSE bhavcopy feed (`eod/`), the app's "Update from feed" |

Order: 1) fundamentals  2) history  3) daily.   Then in the app: Import Classification, Import Shares/Float, Import stock universe list
(`universe.csv`), "Remove symbols not on the list", then Import OHLCV (select all the history .gz files at once), then the daily file each day.

Yahoo's Close is already adjusted for splits/bonuses; the app uses Close. Re-run the history job for the last 2 years now and then,
because Yahoo applies new splits retroactively.
