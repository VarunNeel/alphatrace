#!/usr/bin/env python3
"""Upstox end-of-day option-chain snapshot: Nifty, Bank Nifty, Fin Nifty, Midcap Nifty - the nearest expiries.

  python upstox_options.py snapshot [--expiries 3]

Run once after the close (the workflow does it at 16:30 IST on weekdays). Saves
  options/snapshots/YYYY-MM-DD.csv.gz   every underlying, expiry, strike and CE/PE of that trading day
  options/latest.csv                    the same rows for the newest day (small, easy to open)
  upstox/options_report.json            what was fetched / what failed
Columns: date,underlying,expiry,strike,type,spot,ltp,close,volume,oi,prev_oi,bid,bid_qty,ask,ask_qty,iv,delta,gamma,theta,vega,pcr,lot (lot = contract size)
The date is the last trading day on Nifty's daily chart, so a holiday run just re-saves the previous day.
Needs the repo secret UPSTOX_ANALYTICS_TOKEN (read-only)."""
import argparse, csv, datetime as dt, gzip, json, os, sys, time, urllib.parse, urllib.request, urllib.error

TOKEN = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
API = "https://api.upstox.com"
INST = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
# label -> (fallback instrument key, words that identify it in Upstox's own index list)
UNDERLYINGS = {
    "NIFTY": ("NSE_INDEX|Nifty 50", ["nifty 50"]),
    "BANKNIFTY": ("NSE_INDEX|Nifty Bank", ["nifty bank"]),
    "FINNIFTY": ("NSE_INDEX|Nifty Fin Service", ["nifty fin service", "nifty financial services"]),
    "MIDCPNIFTY": ("NSE_INDEX|NIFTY MID SELECT", ["nifty mid select", "nifty midcap select"]),
}
HEAD = ["date", "underlying", "expiry", "strike", "type", "spot", "ltp", "close", "volume", "oi", "prev_oi", "bid", "bid_qty", "ask", "ask_qty", "iv", "delta", "gamma", "theta", "vega", "pcr", "lot"]


def http(url, auth=True, tries=5):
    h = {"Accept": "application/json", "User-Agent": "alphatrace-upstox"}
    if auth: h["Authorization"] = "Bearer " + TOKEN
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code == 429 or e.code >= 500: time.sleep(5 * (i + 1)); continue
            return e.code, body
        except Exception:
            time.sleep(3 * (i + 1))
    return 0, b"network error"


def q(s): return urllib.parse.quote(s, safe="")


def index_keys():
    """Use Upstox's own index list when it names the index, else the fallback key."""
    keys = {k: v[0] for k, v in UNDERLYINGS.items()}
    st, body = http(INST, auth=False)
    if st != 200: return keys
    try: ins = json.loads(gzip.decompress(body))
    except Exception: return keys
    for r in ins:
        if r.get("segment") != "NSE_INDEX": continue
        nm = str(r.get("name") or r.get("trading_symbol") or "").strip().lower()
        for k, (_, words) in UNDERLYINGS.items():
            if nm in words: keys[k] = r["instrument_key"]
    return keys


def trading_day(today):
    """Date of Nifty's newest daily bar (so holidays do not create fake days)."""
    st, body = http("%s/v3/historical-candle/%s/days/1/%s/%s" % (API, q(UNDERLYINGS["NIFTY"][0]), today, today - dt.timedelta(days=10)))
    try: return dt.date.fromisoformat(json.loads(body)["data"]["candles"][0][0][:10])
    except Exception: return today


def num(x):
    return "" if x is None else ("%.4f" % x).rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def main():
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["snapshot"]); p.add_argument("--expiries", type=int, default=3); a = p.parse_args()
    if not TOKEN: sys.exit("UPSTOX_ANALYTICS_TOKEN is empty - add the repo secret first")
    ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30); today = ist.date()
    day = trading_day(today); keys = index_keys(); rows, rep = [], {"when_ist": ist.isoformat(timespec="minutes"), "trading_day": str(day), "underlyings": {}}
    for name, key in keys.items():
        info = rep["underlyings"][name] = {"key": key, "expiries": [], "rows": 0, "errors": []}
        st, body = http("%s/v2/option/contract?instrument_key=%s" % (API, q(key)))
        try:
            cdata = json.loads(body)["data"]; exps = sorted({d["expiry"] for d in cdata if d.get("expiry")})
            lots = {}
            for d in cdata:
                if d.get("expiry") and d.get("lot_size"): lots.setdefault(d["expiry"], d["lot_size"])
        except Exception: info["errors"].append("contracts: HTTP %s %s" % (st, body[:100].decode("utf-8", "replace"))); continue
        exps = [e for e in exps if e >= str(today)][:a.expiries]
        for e in exps:
            st, body = http("%s/v2/option/chain?instrument_key=%s&expiry_date=%s" % (API, q(key), e))
            try: chain = json.loads(body)["data"]
            except Exception: info["errors"].append("chain %s: HTTP %s %s" % (e, st, body[:100].decode("utf-8", "replace"))); continue
            n0 = len(rows)
            for c in chain:
                for side, blk in (("CE", c.get("call_options") or {}), ("PE", c.get("put_options") or {})):
                    md, gr = blk.get("market_data") or {}, blk.get("option_greeks") or {}
                    rows.append([str(day), name, e, num(c.get("strike_price")), side, num(c.get("underlying_spot_price")), num(md.get("ltp")), num(md.get("close_price")),
                                 num(md.get("volume")), num(md.get("oi")), num(md.get("prev_oi")), num(md.get("bid_price")), num(md.get("bid_qty")), num(md.get("ask_price")),
                                 num(md.get("ask_qty")), num(gr.get("iv")), num(gr.get("delta")), num(gr.get("gamma")), num(gr.get("theta")), num(gr.get("vega")), num(c.get("pcr")), num(lots.get(e))])
            info["expiries"].append({"expiry": e, "rows": len(rows) - n0}); time.sleep(0.3)
        info["rows"] = sum(x["rows"] for x in info["expiries"])
        print("%-10s %d expiries, %d rows%s" % (name, len(info["expiries"]), info["rows"], ("  ERRORS: " + "; ".join(info["errors"])) if info["errors"] else ""))
    os.makedirs("options/snapshots", exist_ok=True); os.makedirs("upstox", exist_ok=True)
    if rows:
        for path, opener in (("options/snapshots/%s.csv.gz" % day, lambda p: gzip.open(p, "wt", newline="", encoding="utf-8")), ("options/latest.csv", lambda p: open(p, "w", newline="", encoding="utf-8"))):
            with opener(path) as fh: w = csv.writer(fh); w.writerow(HEAD); w.writerows(rows)
        idx = sorted(f[:10] for f in os.listdir("options/snapshots") if f.endswith(".csv.gz"))
        json.dump({"dates": idx, "updated": rep["when_ist"]}, open("options/snapshots/index.json", "w"))
    rep["total_rows"] = len(rows); json.dump(rep, open("upstox/options_report.json", "w"), indent=1)
    print("snapshot for %s: %d rows" % (day, len(rows)))
    if not rows: sys.exit(1)


if __name__ == "__main__": main()
