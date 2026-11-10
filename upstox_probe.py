#!/usr/bin/env python3
"""Upstox connection test (step 1). Reads UPSTOX_ANALYTICS_TOKEN from the environment, never prints it.
Checks: instrument list download, one daily-candle call, option-contract + option-chain call, LTP call.
Writes upstox/probe.json (a plain report) and prints a readable log."""
import gzip, io, json, os, sys, time, datetime as dt, urllib.request, urllib.error, urllib.parse
TOKEN = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
API = "https://api.upstox.com"
INST = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
REPORT = {"when": dt.datetime.utcnow().isoformat() + "Z", "tests": {}}

def call(url, auth=True):
    h = {"Accept": "application/json", "User-Agent": "alphatrace-probe"}
    if auth: h["Authorization"] = "Bearer " + TOKEN
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()

def rec(name, ok, note, extra=None):
    REPORT["tests"][name] = {"ok": ok, "note": note, **(extra or {})}
    print(("PASS " if ok else "FAIL ") + name + " - " + note)

def short(b): return b[:300].decode("utf-8", "replace").replace(TOKEN, "***") if TOKEN else b[:300].decode("utf-8", "replace")

def main():
    if not TOKEN:
        rec("token", False, "UPSTOX_ANALYTICS_TOKEN secret is empty or not named exactly that"); return finish(1)
    rec("token", True, "secret found (%d characters)" % len(TOKEN))
    # 1. instrument master (public file, no token)
    st, body = call(INST, auth=False)
    ins = []
    if st == 200:
        try:
            ins = json.loads(gzip.decompress(body)); 
            seg = {}
            for r in ins: seg[r.get("segment")] = seg.get(r.get("segment"), 0) + 1
            rec("instruments", True, "%d NSE instruments" % len(ins), {"by_segment": seg, "sample_keys": sorted(ins[0].keys())})
        except Exception as e: rec("instruments", False, "could not read file: %s" % e)
    else: rec("instruments", False, "HTTP %s %s" % (st, short(body)))
    key = None
    for r in ins:
        if r.get("segment") == "NSE_EQ" and r.get("trading_symbol") == "RELIANCE": key = r["instrument_key"]; break
    key = key or "NSE_EQ|INE002A01018"
    # 2. daily candles (V3)
    to = dt.date.today(); frm = to - dt.timedelta(days=30)
    url = "%s/v3/historical-candle/%s/days/1/%s/%s" % (API, urllib.parse.quote(key, safe=""), to, frm)
    st, body = call(url)
    try:
        c = json.loads(body)["data"]["candles"]
        rec("daily_candles", st == 200 and len(c) > 0, "%d candles for RELIANCE, newest %s" % (len(c), c[0][0] if c else "-"), {"first_row": c[0] if c else None})
    except Exception: rec("daily_candles", False, "HTTP %s %s" % (st, short(body)))
    # 3. option contracts + chain for Nifty 50
    nk = "NSE_INDEX|Nifty 50"
    st, body = call("%s/v2/option/contract?instrument_key=%s" % (API, urllib.parse.quote(nk, safe="")))
    exp = None
    try:
        data = json.loads(body)["data"]; exps = sorted({d["expiry"] for d in data if d.get("expiry")})
        exp = next((e for e in exps if e >= str(to)), None)
        rec("option_contracts", bool(exps), "%d contracts, %d expiries, next %s" % (len(data), len(exps), exp))
    except Exception: rec("option_contracts", False, "HTTP %s %s" % (st, short(body)))
    if exp:
        st, body = call("%s/v2/option/chain?instrument_key=%s&expiry_date=%s" % (API, urllib.parse.quote(nk, safe=""), exp))
        try:
            ch = json.loads(body)["data"]
            rec("option_chain", st == 200 and len(ch) > 0, "%d strikes for %s" % (len(ch), exp), {"first_strike": ch[0] if ch else None})
        except Exception: rec("option_chain", False, "HTTP %s %s" % (st, short(body)))
    # 4. LTP
    st, body = call("%s/v2/market-quote/ltp?instrument_key=%s" % (API, urllib.parse.quote(key, safe="")))
    rec("ltp", st == 200, "HTTP %s %s" % (st, short(body)[:120]))
    finish(0)

def finish(code):
    os.makedirs("upstox", exist_ok=True)
    json.dump(REPORT, open("upstox/probe.json", "w"), indent=1)
    bad = [k for k, v in REPORT["tests"].items() if not v["ok"]]
    print("\nSUMMARY: %s" % ("all tests passed" if not bad else "failed: " + ", ".join(bad)))
    sys.exit(0)   # never fail the job; the report says what happened

if __name__ == "__main__": main()
