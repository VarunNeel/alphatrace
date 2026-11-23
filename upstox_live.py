#!/usr/bin/env python3
"""Upstox live quotes for today's provisional candle (fills the stocks Yahoo's 15-minute job does not cover).

  python upstox_live.py --stocks stocks.csv --out live_out/upstox

OUTPUT  <out>/today.csv.gz   Date,Time,Open,High,Low,Close,Volume,Symbol   (Time = last trade, epoch seconds; Close = last price)
        <out>/index.json     {"date", "updated", "n", "missing"}
Needs the repo secret UPSTOX_ANALYTICS_TOKEN (read-only). Up to 500 stocks per request, so a full list takes a few requests.
The app uses these only until the end-of-day file arrives; nothing here is ever stored as daily data."""
import argparse, csv, datetime as dt, gzip, io, json, os, sys, time, urllib.parse, urllib.request, urllib.error

TOKEN = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
API = "https://api.upstox.com"
INST = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def http(url, auth=True, tries=6):
    h = {"Accept": "application/json", "User-Agent": "alphatrace-upstox-live"}
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
            time.sleep(3 * (i + 1))
    return 0, b"network error / too many retries"


def read_stocks(path, series="EQ,BE,SM,ST"):
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
    st, body = http(INST, auth=False)
    if st != 200: sys.exit("instrument list download failed: HTTP %s" % st)
    m = {}
    for r in json.loads(gzip.decompress(body)):
        if r.get("segment") == "NSE_EQ" and r.get("instrument_type") in ("EQ", "BE", "SM", "ST", None):
            m.setdefault(str(r.get("trading_symbol", "")).upper(), r["instrument_key"])
    return m


def ist_now():
    return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", default="stocks.csv"); ap.add_argument("--out", default="live_out/upstox")
    a = ap.parse_args()
    if not TOKEN:
        print("No UPSTOX_ANALYTICS_TOKEN secret - skipping the Upstox live quotes."); return 0
    syms = read_stocks(a.stocks); keys = instrument_keys()
    pairs = [(s, keys[s]) for s in syms if s in keys]; missing = [s for s in syms if s not in keys]
    today = ist_now().date(); rows = []
    for i in range(0, len(pairs), 500):
        part = pairs[i:i + 500]
        url = API + "/v2/market-quote/quotes?instrument_key=" + urllib.parse.quote(",".join(k for _, k in part), safe=",|")
        st, body = http(url)
        if st in (401, 403): print("token rejected (HTTP %s) - stopping" % st); break
        if st != 200: print("quotes request failed: HTTP %s %s" % (st, body[:200])); continue
        data = (json.loads(body) or {}).get("data") or {}
        by_token = {}
        for q in data.values():
            tok = q.get("instrument_token") or ""; by_token[tok] = q
        for sym, key in part:
            q = by_token.get(key) or data.get("NSE_EQ:" + sym)
            if not q: missing.append(sym); continue
            o = q.get("ohlc") or {}; ltp = q.get("last_price") or 0
            if not ltp: missing.append(sym); continue
            try: t = int(int(q.get("last_trade_time") or 0) / 1000)
            except Exception: t = 0
            if not t: t = int(time.time())
            day = (dt.datetime.utcfromtimestamp(t) + dt.timedelta(hours=5, minutes=30)).date()
            if day != today: continue          # no trade today yet (holiday, suspended, pre-open): skip
            op = o.get("open") or ltp; hi = max(o.get("high") or ltp, ltp, op); lo = min(o.get("low") or ltp, ltp, op)
            rows.append([day.isoformat(), t, op, hi, lo, ltp, int(q.get("volume") or 0), sym])
        time.sleep(0.4)
    if not rows: print("No quotes for today (holiday or before the open)."); return 0
    os.makedirs(a.out, exist_ok=True)
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["Date", "Time", "Open", "High", "Low", "Close", "Volume", "Symbol"]); w.writerows(rows)
    with gzip.open(os.path.join(a.out, "today.csv.gz"), "wt", encoding="utf-8", compresslevel=9) as fh: fh.write(buf.getvalue())
    json.dump({"date": today.isoformat(), "updated": dt.datetime.utcnow().isoformat() + "Z", "n": len(rows), "missing": len(missing)}, open(os.path.join(a.out, "index.json"), "w"))
    print("Upstox live: %d stocks, %d without a quote" % (len(rows), len(missing))); return 0


if __name__ == "__main__":
    sys.exit(main())
