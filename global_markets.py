#!/usr/bin/env python3
"""AlphaTrace - global markets feed (indices, commodities, currencies, crypto) from Yahoo Finance, one fixed format.
Python 3.8+,  pip install yfinance

FORMAT  Date,Adj Close,Close,High,Low,Open,Volume,SYMBOL,NAME,GROUP,COUNTRY      (the app's Global tab reads it as is)

COMMANDS
  daily   python global_markets.py daily  [--days 150] [--out global]
            global/GLOBAL_LATEST.csv                    the last --days calendar days, every symbol (the app pulls this every day)
  full    python global_markets.py full   [--from 2016-08-01] [--out global]
            global/GLOBAL_MARKETS_10Y_DAILY.csv.gz      the whole history (the app pulls this the first time)
            global/GLOBAL_LATEST.csv                    also refreshed
  Both write global/missing.txt - symbols Yahoo returned nothing for (so a wrong code never stops the run).
"""
import argparse, csv, datetime as dt, gzip, io, os, sys, time

HEAD = ["Date", "Adj Close", "Close", "High", "Low", "Open", "Volume", "SYMBOL", "NAME", "GROUP", "COUNTRY"]
# code, name, group, country
SYMBOLS = [
    ("^NSEI", "Nifty 50", "Index", "India"), ("^CRSLDX", "Nifty 500", "Index", "India"), ("^NSEBANK", "Nifty Bank", "Index", "India"),
    ("^BSESN", "S&P BSE Sensex", "Index", "India"), ("^CNXIT", "Nifty IT", "Index", "India"), ("^CRSMID", "Nifty Midcap 100", "Index", "India"),
    ("^CNXSC", "Nifty Smallcap 100", "Index", "India"), ("^INDIAVIX", "India VIX", "Index", "India"),
    ("^GSPC", "S&P 500", "Index", "United States"), ("^DJI", "Dow Jones Industrial Average", "Index", "United States"), ("^NDX", "NASDAQ 100", "Index", "United States"),
    ("^IXIC", "NASDAQ Composite", "Index", "United States"), ("^RUT", "Russell 2000", "Index", "United States"), ("^OEX", "S&P 100", "Index", "United States"),
    ("^VIX", "CBOE Volatility Index", "Index", "United States"),
    ("^FTSE", "FTSE 100", "Index", "United Kingdom"), ("^FTMC", "FTSE 250", "Index", "United Kingdom"), ("^GDAXI", "DAX", "Index", "Germany"),
    ("^FCHI", "CAC 40", "Index", "France"), ("^STOXX50E", "Euro Stoxx 50", "Index", "Eurozone"),
    ("^N225", "Nikkei 225", "Index", "Japan"), ("^KS11", "KOSPI", "Index", "South Korea"), ("^HSI", "Hang Seng", "Index", "Hong Kong"),
    ("000001.SS", "Shanghai Composite", "Index", "China"), ("^AXJO", "S&P/ASX 200", "Index", "Australia"), ("^TWII", "Taiwan Weighted", "Index", "Taiwan"),
    ("GC=F", "Gold", "Futures", "Global"), ("SI=F", "Silver", "Futures", "Global"), ("HG=F", "Copper", "Futures", "Global"), ("PL=F", "Platinum", "Futures", "Global"),
    ("BZ=F", "Brent Crude Oil", "Futures", "Global"), ("CL=F", "WTI Crude Oil", "Futures", "Global"), ("NG=F", "Natural Gas", "Futures", "Global"),
    ("INR=X", "USD/INR", "Forex", "India"), ("EURUSD=X", "EUR/USD", "Forex", "Global"), ("GBPUSD=X", "GBP/USD", "Forex", "Global"), ("JPY=X", "USD/JPY", "Forex", "Global"),
    ("DX-Y.NYB", "US Dollar Index", "Macro", "United States"), ("^TNX", "US 10-Year Yield", "Macro", "United States"),
    ("BTC-USD", "Bitcoin", "Crypto", "Global"), ("ETH-USD", "Ethereum", "Crypto", "Global"),
]


def fetch(code, start, end):
    import yfinance as yf
    df = yf.Ticker(code).history(start=start, end=end, auto_adjust=False, actions=False)
    out = []
    if df is None or len(df) == 0:
        return out
    for ix, r in df.iterrows():
        try:
            c = float(r["Close"])
        except Exception:
            continue
        if c != c or c <= 0:
            continue
        def g(k):
            try:
                v = float(r[k]);  return v if v == v and v > 0 else c
            except Exception:
                return c
        vol = r.get("Volume", 0)
        try:
            vol = int(vol) if vol == vol else 0
        except Exception:
            vol = 0
        out.append((ix.strftime("%Y-%m-%d"), round(g("Adj Close"), 4), round(c, 4), round(g("High"), 4), round(g("Low"), 4), round(g("Open"), 4), vol))
    return out


def collect(start, end):
    rows, missing = [], []
    for code, name, grp, ctry in SYMBOLS:
        got = []
        for attempt in range(3):
            try:
                got = fetch(code, start, end)
                if got: break
            except Exception as e:
                print("  %s: %s" % (code, e), file=sys.stderr)
            time.sleep(1.5)
        if not got:
            missing.append(code); print("MISSING", code); continue
        for d, ac, c, h, l, o, v in got:
            rows.append([d, ac, c, h, l, o, v, code, name, grp, ctry])
        print("%-10s %5d rows  %s -> %s" % (code, len(got), got[0][0], got[-1][0]))
        time.sleep(0.4)
    rows.sort(key=lambda r: (r[7], r[0]))
    return rows, missing


def write_csv(path, rows, gz=False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    buf = io.StringIO(); w = csv.writer(buf, lineterminator="\n"); w.writerow(HEAD); w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    if gz:
        with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as fh: fh.write(data)
    else:
        with open(path, "wb") as fh: fh.write(data)
    print("wrote", path, len(rows), "rows")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("mode", choices=["daily", "full"])
    ap.add_argument("--days", type=int, default=150); ap.add_argument("--from", dest="frm", default="2016-08-01"); ap.add_argument("--out", default="global")
    a = ap.parse_args(); today = dt.date.today(); end = (today + dt.timedelta(days=2)).isoformat()
    if a.mode == "full":
        rows, missing = collect(a.frm, end)
        write_csv(os.path.join(a.out, "GLOBAL_MARKETS_10Y_DAILY.csv.gz"), rows, gz=True)
        cut = (today - dt.timedelta(days=a.days)).isoformat()
        write_csv(os.path.join(a.out, "GLOBAL_LATEST.csv"), [r for r in rows if r[0] >= cut])
    else:
        rows, missing = collect((today - dt.timedelta(days=a.days)).isoformat(), end)
        write_csv(os.path.join(a.out, "GLOBAL_LATEST.csv"), rows)
    with open(os.path.join(a.out, "missing.txt"), "w") as fh: fh.write("\n".join(missing) + ("\n" if missing else ""))
    if len(rows) < 100: sys.exit("Almost nothing downloaded - Yahoo may be blocking this run")


if __name__ == "__main__":
    main()
