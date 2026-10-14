AlphaTrace kit v4 — everything fresh (daily EOD, daily Yahoo, history, new listings, fundamentals, intraday)

Install on the phone (Termux):   bash push_fresh.sh            (repo assumed at ~/alphatrace; or:  bash push_fresh.sh /path/to/repo)
Or by hand: copy the files to the repo root and the 6 .yml files to .github/workflows/ . Data folders are never touched.
A push that changes .github/workflows needs a token with Workflows: read & write.

After pushing, run these once from GitHub -> Actions (Run workflow), in this order:
  1) "Daily OHLCV (Yahoo)"            -> refreshes ohlcv/DAILY_OHLCV.csv (all stocks, latest day)
  2) "Historical OHLCV (Yahoo)"       -> only if the year files look incomplete
  3) "Intraday bars (Yahoo)"  days=59 intervals=5m,15m   then   days=700 intervals=1h
Intraday: nothing is ever deleted; ~3-4 MB/day for all stocks. Schedule: weekdays 16:15 IST.
In the app: Vault -> Pull from GitHub -> Historical "All" -> then "Pull latest".
