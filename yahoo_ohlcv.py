#!/usr/bin/env python3
"""AlphaTrace — daily + historical price files from Yahoo Finance, in one fixed format.   Python 3.8+,  pip install yfinance

INPUT   stocks.csv                 SYMBOL,SERIES  (EQ and BE rows are used; NSE symbols are fetched as <SYMBOL>.NS)

FORMAT  Date,Adj Close,Close,High,Low,Open,Volume,Symbol          (the app's "Import OHLCV CSV" reads it as is)
        Yahoo's Close is already adjusted for splits and bonuses (which is what the app calls adjusted); Adj Close also removes
        dividends and is kept in the file for reference only — the app uses Close.

COMMANDS
  daily    python yahoo_ohlcv.py daily   --stocks stocks.csv            [--days 7] [--out ohlcv] [--keep-days 90]
             ohlcv/DAILY_OHLCV.csv        the latest trading day, every symbol (import this one every day)
             ohlcv/daily/YYYY-MM-DD.csv   one file per trading day in the window (new days are added, old ones pruned)
             ohlcv/daily/index.json       {"dates": [...], "updated": ...}
  history  python yahoo_ohlcv.py history --stocks stocks.csv --from-year 2010 [--to-year 2026] [--out ohlcv/history]
           or only a date range:  history --stocks stocks.csv --from-date 2026-10-14 --to-date 2026-10-23   (downloads just those days from Yahoo and merges them into the yearly files)
             ohlcv/history/HIST_OHLCV_<year>.csv.gz   one gzip per calendar year (the app opens .gz directly; select all of them at once)
             Splits are applied retroactively by Yahoo, so re-run `history --from-year <this year - 2>` now and then to refresh recent years.
  Both write ohlcv/missing.csv — symbols Yahoo returned nothing for (new listings, very illiquid BE names).
"""
import argparse, csv, datetime as dt, glob, gzip, io, json, os, sys, time

HEAD = ["Date", "Adj Close", "Close", "High", "Low", "Open", "Volume", "Symbol"]


def read_stocks(path, series="EQ,BE"):
    want = {s.strip().upper() for s in series.split(",") if s.strip()}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    hd = ["".join(c for c in h.lower() if c.isalnum()) for h in (rows[0] if rows else [])]
    has = "symbol" in hd; iS = hd.index("symbol") if has else 0; iSe = hd.index("series") if has and "series" in hd else -1
    out = []
    for r in rows[1 if has else 0:]:
        if len(r) <= iS: continue
        sym = r[iS].strip().upper(); se = r[iSe].strip().upper() if iSe >= 0 and len(r) > iSe else "EQ"
        if sym and se in want and sym not in out: out.append(sym)
    return out


def fmt(x):
    return ("%.4f" % x).rstrip("0").rstrip(".") if x == x else ""


def frame_rows(df, sym):
    """One ticker's DataFrame (columns Open High Low Close Adj Close Volume, DatetimeIndex) -> output rows; unfinished (no Close) days dropped."""
    rows = []
    for ts, r in df.iterrows():
        try:
            c = float(r["Close"])
        except Exception:
            continue
        if c != c or c <= 0: continue
        ac = r.get("Adj Close", c)
        ac = c if ac != ac else float(ac)
        def f(k):
            v = r.get(k); return float(v) if v is not None and v == v else c
        vol = r.get("Volume"); vol = int(vol) if vol is not None and vol == vol else 0
        rows.append([ts.strftime("%Y-%m-%d"), fmt(ac), fmt(c), fmt(f("High")), fmt(f("Low")), fmt(f("Open")), vol, sym])
    return rows


def download(yf, syms, start, end, batch, delay, log=print):
    """Yield (symbol, rows) for every symbol Yahoo has data for; batches keep each request a sane size."""
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]; tick = [s + ".NS" for s in chunk]; data = None
        for attempt in range(3):
            try:
                data = yf.download(tick, start=start, end=end, interval="1d", auto_adjust=False, group_by="ticker", threads=True, progress=False)
                break
            except Exception as e:  # noqa
                log("    batch %d failed (%s) — retrying" % (i // batch + 1, str(e)[:60])); time.sleep(5 * (attempt + 1))
        log("  %d/%d" % (min(i + batch, len(syms)), len(syms)))
        if data is None or len(data) == 0: continue
        multi = hasattr(data.columns, "levels") and len(getattr(data.columns, "levels", [])) > 1
        for s, t in zip(chunk, tick):
            try:
                df = data[t] if multi else data
            except KeyError:
                continue
            df = df.dropna(how="all")
            if len(df): yield s, frame_rows(df, s)
        time.sleep(delay)


def write_csv(path, rows, gz=False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    op = (lambda p: gzip.open(p, "wt", newline="", encoding="utf-8")) if gz else (lambda p: open(p, "w", newline="", encoding="utf-8"))
    with op(path) as fh:
        w = csv.writer(fh); w.writerow(HEAD); w.writerows(rows)


def write_missing(out, syms, got):
    miss = [s for s in syms if s not in got]
    with open(os.path.join(out, "missing.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["Symbol"]); w.writerows([[s] for s in miss])
    return miss


def cmd_daily(a, yf):
    syms = read_stocks(a.stocks, a.series); print("daily: %d symbols" % len(syms))
    today = dt.date.today(); start = today - dt.timedelta(days=a.days); end = today + dt.timedelta(days=1)
    by_date, got = {}, set()
    for s, rows in download(yf, syms, start.isoformat(), end.isoformat(), a.batch, a.delay):
        got.add(s)
        for r in rows: by_date.setdefault(r[0], []).append(r)
    if not by_date: print("Yahoo returned no data — nothing written."); return 1
    ddir = os.path.join(a.out, "daily"); os.makedirs(ddir, exist_ok=True)
    for d, rows in by_date.items():
        p = os.path.join(ddir, d + ".csv")
        if a.force or not os.path.exists(p) or d == max(by_date): write_csv(p, sorted(rows, key=lambda r: r[7]))
    latest = max(by_date); write_csv(os.path.join(a.out, "DAILY_OHLCV.csv"), sorted(by_date[latest], key=lambda r: r[7]))
    files = sorted(glob.glob(os.path.join(ddir, "????-??-??.csv")))
    cutoff = (today - dt.timedelta(days=a.keep_days)).isoformat()
    for f in files:
        if os.path.basename(f)[:10] < cutoff: os.remove(f)
    dates = [os.path.basename(f)[:10] for f in sorted(glob.glob(os.path.join(ddir, "????-??-??.csv")))]
    json.dump({"dates": dates, "updated": dt.datetime.utcnow().isoformat() + "Z"}, open(os.path.join(ddir, "index.json"), "w"))
    miss = write_missing(a.out, syms, got)
    print("daily: latest %s, %d symbols; %d days kept; %d symbols without data (missing.csv)" % (latest, len(by_date[latest]), len(dates), len(miss)))
    return 0


def cmd_range(a, yf):
    """Download only [from-date, to-date] and merge it into the yearly HIST_OHLCV_<year>.csv.gz files (rows in the range are replaced, the rest is kept)."""
    syms = read_stocks(a.stocks, a.series); f = a.from_date; t = a.to_date or dt.date.today().isoformat()
    end = (dt.date.fromisoformat(t) + dt.timedelta(days=1)).isoformat()
    print("history range: %d symbols, %s to %s" % (len(syms), f, t))
    new, got = {}, set()
    for s, rows in download(yf, syms, f, end, a.batch, a.delay):
        got.add(s)
        for r in rows:
            if f <= r[0] <= t: new.setdefault(r[0][:4], []).append(r)
    if not new: print("Yahoo returned no data for that range - nothing written."); return 1
    os.makedirs(a.out, exist_ok=True); n = 0
    for y in sorted(new):
        dst = os.path.join(a.out, "HIST_OHLCV_%s.csv.gz" % y); keep = []
        if os.path.exists(dst):
            with gzip.open(dst, "rt", newline="", encoding="utf-8") as fh:
                rd = csv.reader(fh); next(rd, None)
                for r in rd:
                    if r and not (f <= r[0] <= t): keep.append(r)
        allr = keep + new[y]; allr.sort(key=lambda r: (r[0], r[-1]))
        write_csv(dst, allr, gz=True); n += 1
        print("  %s: %d new rows merged into %d existing -> %d" % (y, len(new[y]), len(keep), len(allr)))
    miss = write_missing(os.path.dirname(a.out.rstrip("/")) or ".", syms, got)
    print("history range: %d year file(s) updated; %d symbols without data" % (n, len(miss)))
    return 0


def cmd_history(a, yf):
    if a.from_date: return cmd_range(a, yf)
    syms = read_stocks(a.stocks, a.series); to_year = a.to_year or dt.date.today().year
    print("history: %d symbols, %d-%d" % (len(syms), a.from_year, to_year))
    os.makedirs(a.out, exist_ok=True); tmp = os.path.join(a.out, ".tmp"); os.makedirs(tmp, exist_ok=True)
    for f in glob.glob(os.path.join(tmp, "*")): os.remove(f)
    got, handles = set(), {}
    try:
        for s, rows in download(yf, syms, "%d-01-01" % a.from_year, "%d-01-01" % (to_year + 1), a.batch, a.delay):
            got.add(s)
            for r in rows:
                y = r[0][:4]
                if y not in handles:
                    p = os.path.join(tmp, "HIST_OHLCV_%s.csv" % y); handles[y] = open(p, "w", newline="", encoding="utf-8"); csv.writer(handles[y]).writerow(HEAD)
                csv.writer(handles[y]).writerow(r)
    finally:
        for h in handles.values(): h.close()
    n = 0
    for y in sorted(handles):
        src = os.path.join(tmp, "HIST_OHLCV_%s.csv" % y); dst = os.path.join(a.out, "HIST_OHLCV_%s.csv.gz" % y)
        with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=9) as fo: fo.write(fi.read())
        os.remove(src); n += 1
    try: os.rmdir(tmp)
    except OSError: pass
    miss = write_missing(os.path.dirname(a.out.rstrip("/")) or ".", syms, got)
    print("history: %d year files in %s; %d symbols without data" % (n, a.out, len(miss)))
    return 0 if n else 1


def make_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    for name in ("daily", "history"):
        q = s.add_parser(name); q.add_argument("--stocks", default="stocks.csv"); q.add_argument("--series", default="EQ,BE")
        q.add_argument("--batch", type=int, default=100, help="tickers per Yahoo request"); q.add_argument("--delay", type=float, default=1.0)
        if name == "daily":
            q.add_argument("--out", default="ohlcv"); q.add_argument("--days", type=int, default=7); q.add_argument("--keep-days", type=int, default=90); q.add_argument("--force", action="store_true")
        else:
            q.add_argument("--out", default="ohlcv/history"); q.add_argument("--from-year", type=int, default=2010); q.add_argument("--to-year", type=int, default=0)
            q.add_argument("--from-date", default=""); q.add_argument("--to-date", default="")
    return p


def main():
    a = make_parser().parse_args()
    try: import yfinance as yf
    except Exception:
        print("pip install yfinance first"); sys.exit(1)
    sys.exit(cmd_daily(a, yf) if a.cmd == "daily" else cmd_history(a, yf))


if __name__ == "__main__":
    main()
