#!/usr/bin/env python3
"""
Daily EOD feed for AlphaTrace ChartView.

Downloads NSE's official daily bhavcopy (sec_bhavdata_full_DDMMYYYY.csv), keeps the series you care about, and writes
    eod/YYYY-MM-DD.csv   symbol,series,open,high,low,close,prev_close,volume
    eod/index.json       {"dates": ["2026-10-08", "2026-10-09", ...], "updated": "..."}
The app's Vault tab ("Update from feed") reads those two things.

Usage
  python fetch_eod.py                      # today + the last few days (skips ones already published, holidays just 404)
  python fetch_eod.py --days 10            # look back further
  python fetch_eod.py --file sec_bhavdata_full_10102026.csv    # ingest a file you downloaded yourself (e.g. from Termux)

Environment: EOD_SERIES (default EQ,BE)  EOD_OUT (default eod)  EOD_KEEP_DAYS (default 150)
"""
import argparse, csv, datetime as dt, io, json, os, sys, time, urllib.error, urllib.request

SERIES = {s.strip().upper() for s in os.environ.get("EOD_SERIES", "EQ,BE").split(",") if s.strip()}
OUT = os.environ.get("EOD_OUT", "eod")
KEEP = int(os.environ.get("EOD_KEEP_DAYS", "150"))
URLS = [
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv",
    "https://archives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Referer": "https://www.nseindia.com/", "Accept": "text/csv,*/*"}


def fetch_day(day):
    """Returns the file text, or None when NSE has no file for that date (holiday / not published yet)."""
    stamp = day.strftime("%d%m%Y")
    for tmpl in URLS:
        url = tmpl.format(d=stamp)
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=40) as r:
                    data = r.read().decode("utf-8", "replace")
                if data.lstrip().upper().startswith("SYMBOL"):
                    return data
                break  # an HTML error page, try the next host
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                print(f"  {url}: HTTP {e.code}", file=sys.stderr)
            except Exception as e:  # network trouble: retry
                print(f"  {url}: {e}", file=sys.stderr)
            time.sleep(2 + attempt * 3)
    return None


def num(x):
    try:
        v = float(str(x).strip().replace(",", ""))
        return v if v == v else None
    except ValueError:
        return None


def parse(text):
    """NSE's header and values carry stray spaces ('SYMBOL, SERIES, DATE1, ...'), so everything is stripped."""
    rd = csv.reader(io.StringIO(text))
    head = [h.strip().upper() for h in next(rd)]
    ix = {h: i for i, h in enumerate(head)}
    need = ["SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY"]
    missing = [n for n in need if n not in ix]
    if missing:
        raise SystemExit(f"Unexpected bhavcopy layout, missing columns: {missing}")
    rows, date = [], None
    for r in rd:
        if len(r) < len(head):
            continue
        r = [c.strip() for c in r]
        series = r[ix["SERIES"]].upper()
        if series not in SERIES:
            continue
        o, h, l, c = (num(r[ix[k]]) for k in ("OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"))
        pc, v = num(r[ix["PREV_CLOSE"]]), num(r[ix["TTL_TRD_QNTY"]])
        if None in (o, h, l, c) or c <= 0 or h <= 0 or l <= 0:
            continue
        d = dt.datetime.strptime(r[ix["DATE1"]], "%d-%b-%Y").date()
        date = date or d
        if d != date:
            continue
        rows.append([r[ix["SYMBOL"]].upper(), series, o, h, l, c, pc if pc else "", int(v or 0)])
    rows.sort(key=lambda x: (x[0], x[1]))
    return date, rows


def write_day(date, rows):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, date.isoformat() + ".csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "series", "open", "high", "low", "close", "prev_close", "volume"])
        w.writerows(rows)
    return path


def rebuild_index():
    days = sorted(f[:-4] for f in os.listdir(OUT) if len(f) == 14 and f.endswith(".csv") and f[4] == "-")
    for old in days[:-KEEP] if KEEP > 0 else []:
        os.remove(os.path.join(OUT, old + ".csv"))
    days = days[-KEEP:] if KEEP > 0 else days
    with open(os.path.join(OUT, "index.json"), "w") as f:
        json.dump({"dates": days, "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, f)
    return days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5, help="how many calendar days back to look (default 5)")
    ap.add_argument("--file", help="ingest a bhavcopy file you already downloaded")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    added = 0
    if a.file:
        date, rows = parse(open(a.file, encoding="utf-8", errors="replace").read())
        if not date or not rows:
            raise SystemExit("No usable rows in that file")
        write_day(date, rows); added += 1
        print(f"{date}: {len(rows)} rows from {a.file}")
    else:
        today = dt.date.today()
        for back in range(a.days, -1, -1):
            day = today - dt.timedelta(days=back)
            if day.weekday() >= 5 or os.path.exists(os.path.join(OUT, day.isoformat() + ".csv")):
                continue
            text = fetch_day(day)
            if not text:
                print(f"{day}: no file (holiday or not published yet)")
                continue
            date, rows = parse(text)
            if not date or not rows:
                print(f"{day}: file had no usable rows"); continue
            write_day(date, rows); added += 1
            print(f"{date}: {len(rows)} rows")
    days = rebuild_index()
    print(f"index: {len(days)} day(s), latest {days[-1] if days else '—'}; added {added}")


if __name__ == "__main__":
    main()
