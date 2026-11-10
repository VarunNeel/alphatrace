#!/usr/bin/env python3
"""Upstox daily prices for every stock in stocks.csv (end-of-day only, no intraday).

  python upstox_ohlcv.py history --stocks stocks.csv --from-date 2010-01-01 [--to-date 2026-11-11]
  python upstox_ohlcv.py daily   --stocks stocks.csv            (the last 7 days, for the evening job)

OUTPUT  ohlcv/upstox/HIST_OHLCV_<year>.csv.gz   same columns as the Yahoo files:
        Date,Adj Close,Close,High,Low,Open,Volume,Symbol   (Adj Close = Close: Upstox prices are not split-adjusted)
        upstox/history_report.json              what was fetched, what was empty, suspicious one-day jumps (splits / bonus)
        upstox/progress.json                    symbols already done, so a stopped run carries on where it left off
Needs the repo secret UPSTOX_ANALYTICS_TOKEN (read-only, free). Rows in the requested range are replaced for the symbols
fetched; everything else in the yearly files is kept."""
import argparse, csv, datetime as dt, glob, gzip, json, os, sys, time, urllib.parse, urllib.request, urllib.error

TOKEN = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
API = "https://api.upstox.com"
INST = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
HEAD = ["Date", "Adj Close", "Close", "High", "Low", "Open", "Volume", "Symbol"]
OUT = "ohlcv/upstox"


def http(url, auth=True, tries=6):
    h = {"Accept": "application/json", "User-Agent": "alphatrace-upstox"}
    if auth: h["Authorization"] = "Bearer " + TOKEN
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code == 429 or e.code >= 500:
                time.sleep(min(60, 5 * (i + 1) * (3 if e.code == 429 else 1))); continue
            return e.code, body
        except Exception as e:
            time.sleep(3 * (i + 1)); last = str(e).encode()
    return 0, b"network error / too many retries"


def read_stocks(path, series="EQ,BE"):
    want = {s.strip().upper() for s in series.split(",")}
    rows = list(csv.reader(open(path, newline="", encoding="utf-8-sig")))
    hd = ["".join(c for c in h.lower() if c.isalnum()) for h in (rows[0] if rows else [])]
    has = "symbol" in hd; iS = hd.index("symbol") if has else 0; iSe = hd.index("series") if has and "series" in hd else -1
    out = []
    for r in rows[1 if has else 0:]:
        if len(r) <= iS: continue
        s = r[iS].strip().upper(); se = r[iSe].strip().upper() if iSe >= 0 and len(r) > iSe else "EQ"
        if s and se in want and s not in out: out.append(s)
    return out


def instrument_keys():
    import io
    st, body = http(INST, auth=False)
    if st != 200: sys.exit("instrument list download failed: HTTP %s" % st)
    ins = json.loads(gzip.decompress(body)); m = {}
    for r in ins:
        if r.get("segment") == "NSE_EQ" and r.get("instrument_type") in ("EQ", "BE", "SM", "ST", None):
            m.setdefault(str(r.get("trading_symbol", "")).upper(), r["instrument_key"])
    return m


def fmt(x):
    return ("%.4f" % x).rstrip("0").rstrip(".")


def slices(d0, d1, years=9):
    """[(from, to)] pieces of at most ~9 years, because one request may span at most a decade."""
    out = []; a = d0
    while a <= d1:
        b = min(d1, dt.date(a.year + years, a.month, min(a.day, 28)) - dt.timedelta(days=1)); out.append((a, b)); a = b + dt.timedelta(days=1)
    return out


def candles(key, d0, d1):
    rows = []
    for a, b in slices(d0, d1):
        url = "%s/v3/historical-candle/%s/days/1/%s/%s" % (API, urllib.parse.quote(key, safe=""), b, a)
        st, body = http(url)
        if st != 200: return None, "HTTP %s %s" % (st, body[:120].decode("utf-8", "replace"))
        try: rows += json.loads(body)["data"]["candles"]
        except Exception: return None, "unreadable reply"
    return rows, None


def merge_year(year, new_rows, d0, d1, fetched):
    """Yearly gz: keep old rows unless (date in range and symbol re-fetched); add the new rows."""
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "HIST_OHLCV_%s.csv.gz" % year); keep = []
    if os.path.exists(dst):
        with gzip.open(dst, "rt", newline="", encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for r in rd:
                if len(r) == 8 and not (str(d0) <= r[0] <= str(d1) and r[7] in fetched): keep.append(r)
    allr = keep + new_rows; allr.sort(key=lambda r: (r[7], r[0]))
    with gzip.open(dst, "wt", newline="", encoding="utf-8", compresslevel=9) as fh:
        w = csv.writer(fh); w.writerow(HEAD); w.writerows(allr)
    return len(allr)


def run(a, d0, d1):
    if not TOKEN: sys.exit("UPSTOX_ANALYTICS_TOKEN is empty - add the repo secret first")
    syms = read_stocks(a.stocks, a.series); keys = instrument_keys()
    todo = [s for s in syms if s in keys]; nokey = [s for s in syms if s not in keys]
    sig = "%s|%s" % (d0, d1); prog_p = "upstox/progress.json"; done = set()
    os.makedirs("upstox", exist_ok=True)
    try:
        pg = json.load(open(prog_p))
        if pg.get("sig") == sig: done = set(pg.get("done", []))
    except Exception: pass
    print("upstox %s -> %s: %d stocks, %d without an Upstox key, %d already done in an earlier run" % (d0, d1, len(todo), len(nokey), len(done)))
    by_year, fetched, empty, errs, jumps, bars = {}, set(), [], [], [], 0
    t0 = time.time(); stopped = False
    for i, s in enumerate(todo):
        if s in done: continue
        if (time.time() - t0) / 60 > a.max_minutes: stopped = True; break
        rows, err = candles(keys[s], d0, d1)
        if err:
            errs.append("%s: %s" % (s, err))
            if "401" in err or "403" in err: print("token rejected - stopping"); stopped = True; break
        elif not rows: empty.append(s); done.add(s)
        else:
            rows.sort(key=lambda c: c[0]); prev = None
            for c in rows:
                d = c[0][:10]; o, h, l, cl, v = float(c[1]), float(c[2]), float(c[3]), float(c[4]), int(c[5] or 0)
                if cl <= 0: continue
                if prev and abs(cl / prev - 1) > 0.35: jumps.append({"symbol": s, "date": d, "move_pct": round((cl / prev - 1) * 100, 1)})
                prev = cl
                by_year.setdefault(d[:4], []).append([d, fmt(cl), fmt(cl), fmt(h), fmt(l), fmt(o), v, s]); bars += 1
            fetched.add(s); done.add(s)
        if a.pace: time.sleep(a.pace)
        if (i + 1) % 100 == 0: print("  %d/%d  %d bars  %.1f min" % (i + 1, len(todo), bars, (time.time() - t0) / 60))
    for y, rows in sorted(by_year.items()): merge_year(y, rows, d0, d1, fetched)
    complete = not stopped and all(s in done for s in todo)
    json.dump({"sig": sig, "done": [] if complete else sorted(done)}, open(prog_p, "w"))
    rep = {"when": dt.datetime.utcnow().isoformat() + "Z", "range": [str(d0), str(d1)], "stocks_requested": len(syms), "fetched": len(fetched),
           "empty": empty[:200], "no_upstox_key": nokey[:200], "errors": errs[:50], "bars": bars, "complete": complete, "stopped_early": stopped,
           "years_written": sorted(by_year), "big_jumps_over_35pct": sorted(jumps, key=lambda j: -abs(j["move_pct"]))[:60],
           "note": "Upstox prices are not adjusted for splits/bonus: a big one-day jump that is really a split is listed in big_jumps."}
    json.dump(rep, open("upstox/history_report.json", "w"), indent=1)
    print("done: %d stocks, %d bars, %d empty, %d errors, %d suspicious jumps%s" % (len(fetched), bars, len(empty), len(errs), len(jumps), "  (stopped early - run again to continue)" if stopped else ""))


def main():
    p = argparse.ArgumentParser(); sp = p.add_subparsers(dest="cmd", required=True)
    for n in ("history", "daily"):
        q = sp.add_parser(n); q.add_argument("--stocks", default="stocks.csv"); q.add_argument("--series", default="EQ,BE")
        q.add_argument("--pace", type=float, default=0.12); q.add_argument("--max-minutes", type=float, default=300)
        if n == "history": q.add_argument("--from-date", default="2010-01-01"); q.add_argument("--to-date", default="")
    a = p.parse_args(); today = dt.date.today()
    if a.cmd == "daily": d0, d1 = today - dt.timedelta(days=7), today
    else: d0 = dt.date.fromisoformat(a.from_date); d1 = dt.date.fromisoformat(a.to_date) if a.to_date else today
    run(a, d0, d1)


if __name__ == "__main__": main()
