#!/usr/bin/env python3
"""AlphaTrace — intraday bars (5m, 15m, 1h) from Yahoo Finance.   Python 3.8+,  pip install yfinance

SYMBOLS  ALL stocks by default: intraday_symbols.csv if it exists (one symbol per line), otherwise every EQ/BE symbol in stocks.csv.
         To limit it, run `pick --top N` (highest float first) which writes intraday_symbols.csv.

COMMANDS
  pick   python intraday.py pick  --float STOCKS_FLOAT.csv --top 500 --out intraday_symbols.csv
  fetch  python intraday.py fetch [--symbols intraday_symbols.csv] [--intervals 5m,15m,1h] [--days 5]
           intraday/<iv>/YYYY-MM-DD.csv.gz   one file per IST trading day:  Time,Open,High,Low,Close,Volume,Symbol
                                             Time = UNIX seconds (UTC).  Close is split-adjusted by Yahoo.
           intraday/<iv>/index.json          {"interval":..., "dates":[...], "updated":...}
         Re-running merges into existing day files (same Symbol+Time is replaced), so a late run never loses data.
  Yahoo keeps 5m/15m for ~60 days and 1h for ~730 days; --days is clamped to that.  Daily run: --days 5.  First run: --days 59 (1h: --days 700).
  Retention: nothing is deleted by default (old days stay on GitHub). To prune:  --keep 5m=30,15m=60
"""
import argparse, csv, datetime as dt, glob, gzip, io, json, os, sys, time

HEAD = ["Time", "Open", "High", "Low", "Close", "Volume", "Symbol"]
LIMIT = {"5m": 59, "15m": 59, "1h": 729}
KEEP = {"5m": 36500, "15m": 36500, "1h": 36500}   # keep everything (set --keep 5m=30 to prune)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def fmt(x):
    return ("%.4f" % x).rstrip("0").rstrip(".") if x == x else ""


def read_symbols(path):
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.reader(fh):
            if not r: continue
            s = r[0].strip().upper()
            if s and s != "SYMBOL" and s not in out: out.append(s)
    return out


def cmd_pick(a):
    rows = list(csv.DictReader(open(a.float, newline="", encoding="utf-8-sig")))
    def fl(r):
        try: return float(r.get("Float Shares") or 0)
        except ValueError: return 0.0
    rows = [r for r in rows if r.get("Symbol")]
    rows.sort(key=fl, reverse=True)
    syms = [r["Symbol"].strip().upper() for r in rows[:a.top]]
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["Symbol"]); w.writerows([[s] for s in syms])
    print("pick: %d symbols -> %s" % (len(syms), a.out)); return 0


def frame_rows(df, sym):
    """DataFrame (Open High Low Close Volume, tz-aware DatetimeIndex) -> {istdate: [row...]}"""
    out = {}
    for ts, r in df.iterrows():
        try: c = float(r["Close"])
        except Exception: continue
        if c != c or c <= 0: continue
        def f(k):
            v = r.get(k); return float(v) if v is not None and v == v else c
        vol = r.get("Volume"); vol = int(vol) if vol is not None and vol == vol else 0
        t = int(ts.timestamp()); day = dt.datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d")
        out.setdefault(day, []).append([t, fmt(f("Open")), fmt(f("High")), fmt(f("Low")), fmt(c), vol, sym])
    return out


def download(yf, syms, iv, days, batch, delay, log=print):
    end = dt.datetime.utcnow() + dt.timedelta(days=1); start = end - dt.timedelta(days=days + 1)
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]; tick = [s + ".NS" for s in chunk]; data = None
        for attempt in range(3):
            try:
                data = yf.download(tick, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), interval=iv, auto_adjust=False,
                                   group_by="ticker", threads=True, progress=False, prepost=False)
                break
            except Exception as e:  # noqa
                log("    batch %d failed (%s) — retrying" % (i // batch + 1, str(e)[:60])); time.sleep(5 * (attempt + 1))
        log("  %s %d/%d" % (iv, min(i + batch, len(syms)), len(syms)))
        if data is None or len(data) == 0: continue
        multi = hasattr(data.columns, "levels") and len(getattr(data.columns, "levels", [])) > 1
        for s, t in zip(chunk, tick):
            try: df = data[t] if multi else data
            except KeyError: continue
            df = df.dropna(how="all")
            if len(df): yield s, frame_rows(df, s)
        time.sleep(delay)


def read_day(path):
    if not os.path.exists(path): return {}
    d = {}
    with gzip.open(path, "rt", newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh); next(rd, None)
        for r in rd:
            if len(r) >= 7: d[(r[6], int(r[0]))] = r
    return d


def write_day(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = sorted(d.values(), key=lambda r: (r[6], int(r[0])))
    with gzip.open(path, "wt", newline="", encoding="utf-8", compresslevel=9) as fh:
        w = csv.writer(fh); w.writerow(HEAD); w.writerows(rows)


def fetch_interval(yf, syms, iv, a, keep):
    days = min(a.days, LIMIT[iv]); by_day = {}; got = set()
    for s, perday in download(yf, syms, iv, days, a.batch, a.delay):
        got.add(s)
        for day, rows in perday.items(): by_day.setdefault(day, []).extend(rows)
    if not by_day: print("%s: Yahoo returned no data." % iv); return 0
    base = os.path.join(a.out, iv)
    for day, rows in by_day.items():
        p = os.path.join(base, day + ".csv.gz"); d = read_day(p)
        for r in rows: d[(r[6], int(r[0]))] = [str(x) for x in r]
        write_day(p, d)
    cutoff = (dt.date.today() - dt.timedelta(days=keep)).isoformat()
    for f in glob.glob(os.path.join(base, "????-??-??.csv.gz")):
        if os.path.basename(f)[:10] < cutoff: os.remove(f)
    dates = [os.path.basename(f)[:10] for f in sorted(glob.glob(os.path.join(base, "????-??-??.csv.gz")))]
    json.dump({"interval": iv, "dates": dates, "updated": dt.datetime.utcnow().isoformat() + "Z"}, open(os.path.join(base, "index.json"), "w"))
    print("%s: %d symbols, %d day files kept (latest %s)" % (iv, len(got), len(dates), dates[-1] if dates else "-")); return len(got)


def cmd_fetch(a, yf):
    if os.path.exists(a.symbols): syms = read_symbols(a.symbols)
    elif os.path.exists(a.stocks):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from yahoo_ohlcv import read_stocks
        syms = read_stocks(a.stocks, "EQ,BE")
    else: print("no %s and no %s" % (a.symbols, a.stocks)); return 1
    print("intraday: %d symbols" % len(syms))
    keep = dict(KEEP)
    for kv in (a.keep or "").split(","):
        if "=" in kv:
            k, v = kv.split("="); keep[k.strip()] = int(v)
    n = 0
    for iv in [x.strip() for x in a.intervals.split(",") if x.strip()]:
        if iv not in LIMIT: print("skip unknown interval", iv); continue
        n += fetch_interval(yf, syms, iv, a, keep[iv])
    return 0 if n else 1


def make_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    q = s.add_parser("pick"); q.add_argument("--float", default="STOCKS_FLOAT.csv"); q.add_argument("--top", type=int, default=500); q.add_argument("--out", default="intraday_symbols.csv")
    q = s.add_parser("fetch"); q.add_argument("--symbols", default="intraday_symbols.csv"); q.add_argument("--stocks", default="stocks.csv")
    q.add_argument("--intervals", default="5m,15m,1h"); q.add_argument("--days", type=int, default=5); q.add_argument("--out", default="intraday")
    q.add_argument("--keep", default=""); q.add_argument("--batch", type=int, default=100); q.add_argument("--delay", type=float, default=1.5)
    return p


def main():
    a = make_parser().parse_args()
    if a.cmd == "pick": sys.exit(cmd_pick(a))
    try: import yfinance as yf
    except Exception:
        print("pip install yfinance first"); sys.exit(1)
    sys.exit(cmd_fetch(a, yf))


if __name__ == "__main__":
    main()
