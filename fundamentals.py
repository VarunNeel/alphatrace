#!/usr/bin/env python3
"""AlphaTrace — classification + shares/float builder.   Python 3.8+  (standard library; `pip install yfinance` is optional but recommended)

INPUT   stocks.csv                 SYMBOL,SERIES  (your NSE list — EQ and BE rows are used, anything else is ignored)
OUTPUT  fundamentals/
          nse_classification.csv   SYMBOL,COMPANY_NAME,ISIN,SERIES,MACRO_ECONOMIC_SECTOR,SECTOR,INDUSTRY,BASIC_INDUSTRY,SOURCE
                                   -> app: Vault > Import Classification
          STOCKS_FLOAT.csv         Symbol,Short Name,Shares Outstanding,Float Shares
                                   -> app: Vault > Import Shares/Float
          universe.csv             SYMBOL,SERIES  — only the symbols that came out classified. This is your clean stock universe
                                   (ETFs, bonds, funds and anything BSE does not classify are NOT in it)
                                   -> app: Vault > Import stock universe list, then "Remove symbols not on the list"
          unmatched.csv            every symbol that was left out, and why
          cache/                   raw replies, so a re-run only fetches what is missing (safe to stop and resume)

HOW
  classification : NSE EQUITY_L.csv (symbol -> ISIN, company name)  +  BSE scrip list (ISIN -> BSE code)  +  BSE ComHeader per code.
                   ComHeader's four-level hierarchy maps one-to-one onto the four columns:
                     Sector -> MACRO_ECONOMIC_SECTOR   IndustryNew -> SECTOR   IGroup -> INDUSTRY   ISubgroup -> BASIC_INDUSTRY
  shares / float : --float-source yahoo | bse | auto (default).
                     yahoo : Yahoo Finance sharesOutstanding / floatShares / shortName  (exact counts; needs `pip install yfinance`)
                     bse   : market cap / price and free-float market cap / price from ComHeader (an estimate)
                     auto  : Yahoo first, BSE estimate for whatever Yahoo does not have

USAGE
  python fundamentals.py probe 500325                      # print the raw ComHeader reply for one BSE code — run this FIRST
  python fundamentals.py run --stocks stocks.csv           # full build
  python fundamentals.py run --stocks stocks.csv --limit 30        # quick trial
  python fundamentals.py run --stocks stocks.csv --eod eod         # use the latest EOD close for the BSE estimate
If a probe shows different ComHeader field names, override with --macro-key / --sector-key / --industry-key / --basic-key /
--mcap-key / --mcapff-key / --price-key.
"""
import argparse, csv, glob, io, json, os, re, sys, time, urllib.request, urllib.error, urllib.parse

UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
BSE_HDR = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Referer": "https://www.bseindia.com/",
           "Origin": "https://www.bseindia.com", "Accept-Language": "en-US,en;q=0.9"}
NSE_HDR = {"User-Agent": UA, "Accept": "text/csv,*/*", "Referer": "https://www.nseindia.com/"}
NSE_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
BSE_LIST = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active"
BSE_HEAD = "https://api.bseindia.com/BseIndiaAPI/api/ComHeader/w?quotetype=EQ&scripcode=%s&seriesid="
CLS_HEAD = ["SYMBOL", "COMPANY_NAME", "ISIN", "SERIES", "MACRO_ECONOMIC_SECTOR", "SECTOR", "INDUSTRY", "BASIC_INDUSTRY", "SOURCE"]
FLT_HEAD = ["Symbol", "Short Name", "Shares Outstanding", "Float Shares"]


def http(url, headers, tries=3, timeout=30):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa
            last = e; time.sleep(1.5 * (i + 1))
    raise RuntimeError("%s -> %s" % (url[:90], last))


def norm(k): return "".join(ch for ch in str(k).lower() if ch.isalnum())


def pick(d, keys):
    for k in keys:
        v = d.get(norm(k))
        if v not in (None, "", "-", "--"): return v
    return ""


def num(v):
    try:
        x = float(str(v).replace(",", "").strip()); return x if x == x else None
    except Exception:
        return None


def clean(s): return " ".join(str(s or "").replace(",", " ").replace('"', "").split())


def flat(obj):
    if isinstance(obj, list): obj = obj[0] if obj else {}
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(v, (dict, list)): out[norm(k)] = v
    return out


def read_stocks(path, series):
    """stocks.csv -> {SYMBOL: SERIES} for the wanted series (a file with no SERIES column is taken as all-EQ)."""
    want = {s.strip().upper() for s in series.split(",") if s.strip()}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows: return {}
    hd = [norm(h) for h in rows[0]]
    has_head = "symbol" in hd
    iS = hd.index("symbol") if has_head else 0
    iSe = hd.index("series") if has_head and "series" in hd else -1
    out = {}
    for r in rows[1 if has_head else 0:]:
        if len(r) <= iS: continue
        sym = r[iS].strip().upper()
        se = r[iSe].strip().upper() if iSe >= 0 and len(r) > iSe else "EQ"
        if sym and se in want: out[sym] = se
    return out


def load_nse_list(path):
    raw = open(path, "rb").read() if path else http(NSE_LIST, NSE_HDR)
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8", "replace"))))
    hd = [norm(h) for h in rows[0]]
    ix = lambda *n: next((hd.index(norm(x)) for x in n if norm(x) in hd), -1)
    iS, iN, iI = ix("symbol"), ix("nameofcompany"), ix("isinnumber", "isin")
    out = {}
    for r in rows[1:]:
        if len(r) <= max(iS, iI): continue
        out[r[iS].strip().upper()] = {"name": clean(r[iN]) if iN >= 0 else "", "isin": r[iI].strip().upper()}
    return out


def load_bse_list():
    data = json.loads(http(BSE_LIST, BSE_HDR, timeout=60).decode("utf-8", "replace"))
    if isinstance(data, dict): data = data.get("Table") or data.get("data") or list(data.values())[0]
    by_isin = {}
    for r in data:
        f = flat(r); isin = str(pick(f, ["ISIN_NUMBER", "ISIN"])).strip().upper(); code = str(pick(f, ["SCRIP_CD", "scripcode"])).strip()
        if isin and code: by_isin[isin] = {"code": code, "name": clean(pick(f, ["Scrip_Name", "Issuer_Name"]))}
    return by_isin


def latest_prices(eod):
    px = {}
    if not eod: return px
    for f in sorted(glob.glob(os.path.join(eod, "????-??-??.csv")), reverse=True)[:10]:
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                s = (r.get("symbol") or "").upper(); c = num(r.get("close"))
                if s and c and c > 0 and s not in px: px[s] = c
    return px


def yahoo_info(sym, cache_dir, delay, yf):
    """-> {'name','shares','float'} from Yahoo (cached), or None."""
    cf = os.path.join(cache_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", sym) + ".json")
    if os.path.exists(cf):
        try:
            res = json.load(open(cf, encoding="utf-8")) or None
            if res is None or "ys" in res: return res   # replies cached before v2 carry no Yahoo sector / industry — fetched again below
        except Exception: pass
    res = None
    for attempt in range(2):
        try:
            info = yf.Ticker(sym + ".NS").get_info() or {}
            sh, fl = num(info.get("sharesOutstanding")), num(info.get("floatShares"))
            ys, yi = clean(info.get("sector") or ""), clean(info.get("industry") or "")
            if (sh and sh > 0) or ys or yi:
                res = {"name": clean(info.get("shortName") or info.get("longName") or ""), "shares": int(round(sh)) if sh and sh > 0 else 0,
                       "float": int(round(fl)) if fl and fl > 0 else None, "ys": ys, "yi": yi}
            break
        except Exception as e:  # noqa
            if "429" in str(e) or "Too Many" in str(e): time.sleep(8 * (attempt + 1))
            else: break
    json.dump(res, open(cf, "w", encoding="utf-8")); time.sleep(delay)
    return res



NSE_HOME = "https://www.nseindia.com/"
NSE_QUOTE = "https://www.nseindia.com/api/quote-equity?symbol=%s"
_nse = {"opener": None, "fails": 0}


def nse_industry(sym, cache_dir, delay=0.6):
    """Second classification source #1 — NSE's own four-level classification for one symbol ('industryInfo' of its quote page).
    Returns {'macro','sector','industry','basic'} or None. NSE often refuses cloud servers; after 6 refusals in a row it is skipped for the rest of the run."""
    import http.cookiejar
    cf = os.path.join(cache_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", sym) + ".json")
    if os.path.exists(cf):
        try: return json.load(open(cf, encoding="utf-8")) or None
        except Exception: pass
    if _nse["fails"] >= 6: return None
    if _nse["opener"] is None:
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try: op.open(urllib.request.Request(NSE_HOME, headers={"User-Agent": UA, "Accept": "text/html"}), timeout=25).read(1000)
        except Exception: pass
        _nse["opener"] = op
    res = None
    try:
        rq = urllib.request.Request(NSE_QUOTE % urllib.parse.quote(sym), headers={"User-Agent": UA, "Accept": "application/json", "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=" + urllib.parse.quote(sym)})
        obj = json.loads(_nse["opener"].open(rq, timeout=25).read().decode("utf-8", "replace"))
        ii = (obj or {}).get("industryInfo") or {}
        m, s_, i_, b_ = clean(ii.get("macro")), clean(ii.get("sector")), clean(ii.get("industry")), clean(ii.get("basicIndustry"))
        if s_ and b_: res = {"macro": m or s_, "sector": s_, "industry": i_ or s_, "basic": b_}
        _nse["fails"] = 0
    except Exception:
        _nse["fails"] += 1
        time.sleep(1.0); return None
    json.dump(res, open(cf, "w", encoding="utf-8")); time.sleep(delay)
    return res


def learn_yahoo_map(cls_rows, ycache):
    """Second classification source #2 — learns, from the stocks BSE DID classify, which BSE sector / industry each Yahoo industry usually means.
    -> {yahoo_industry: [(macro, sector, industry, basic) most common, share_of_basic, share_of_sector, n]}"""
    from collections import Counter, defaultdict
    by_y = defaultdict(list)
    for r in cls_rows:
        cf = os.path.join(ycache, re.sub(r"[^A-Za-z0-9_.-]", "_", r[0]) + ".json")
        try: y = json.load(open(cf, encoding="utf-8")) or {}
        except Exception: continue
        if y.get("yi"): by_y[y["yi"]].append((r[4], r[5], r[6], r[7]))
    out = {}
    for yi, rows in by_y.items():
        c4 = Counter(rows); top, n4 = c4.most_common(1)[0]
        c2 = Counter((a, b) for a, b, _, _ in rows); (m2, s2), ns = c2.most_common(1)[0]
        out[yi] = (top, n4 / len(rows), ns / len(rows), len(rows), (m2, s2))
    return out


def second_source(a, unmatched, nse, stocks, cls, ycache, yf, cache_dir):
    """Try to classify the stocks BSE could not. Returns (new_cls_rows, new_flt_rows, still_unmatched, counts)."""
    ncache = os.path.join(cache_dir, "nse"); os.makedirs(ncache, exist_ok=True)
    mode = a.second_source
    recover = ("no BSE listing", "ComHeader failed", "ComHeader had no")
    todo = [u for u in unmatched if any(u[2].startswith(x) for x in recover) and u[0] in nse]
    keep = [u for u in unmatched if u not in todo]
    new_cls, new_flt, cnt = [], [], {"nse": 0, "yahoo": 0, "yahoo_sector_only": 0, "failed": 0}
    from collections import Counter
    _c = {}
    for r_ in cls: _c.setdefault(r_[7].lower(), Counter())[(r_[4], r_[5], r_[6], r_[7])] += 1
    bnames = {k: v.most_common(1)[0][0] for k, v in _c.items()}
    ymap = learn_yahoo_map(cls, ycache) if (mode in ("auto", "yahoo") and yf is not None) else {}
    print("second source: %d stocks BSE could not classify; learned %d Yahoo-industry mappings from the %d BSE-classified stocks" % (len(todo), len(ymap), len(cls)))
    for i, (sym, isin, why) in enumerate(todo, 1):
        n = nse.get(sym, {}); row = None; ys = yi = ""
        if mode in ("auto", "nse"):
            r = nse_industry(sym, ncache, a.nse_delay)
            if r:
                snap = bnames.get(r["basic"].lower())   # same basic-industry name already used by BSE-classified stocks -> reuse BSE's parents so groups do not split in two
                lv = snap if snap else (r["macro"], r["sector"], r["industry"], r["basic"])
                row = [sym, clean(n.get("name")), isin, stocks.get(sym, "EQ"), lv[0], lv[1], lv[2], lv[3], "nse:industryInfo" + (" (matched to BSE group)" if snap else "")]; cnt["nse"] += 1
        y = None
        if yf is not None and mode in ("auto", "yahoo"):
            y = yahoo_info(sym, ycache, a.ydelay, yf)
            if y: ys, yi = y.get("ys", ""), y.get("yi", "")
        if row is None and yi and yi in ymap:
            (top, p4, p2, nn, (m2, s2)) = ymap[yi]
            if p4 >= a.min_share and nn >= 2:
                row = [sym, clean(n.get("name")), isin, stocks.get(sym, "EQ"), top[0], top[1], top[2], top[3], "yahoo:%s (matches %d%% of %d BSE-classified stocks)" % (yi, round(p4 * 100), nn)]; cnt["yahoo"] += 1
            elif p2 >= a.min_share and nn >= 2:
                row = [sym, clean(n.get("name")), isin, stocks.get(sym, "EQ"), m2, s2, "Unclassified", "Unclassified", "yahoo:%s (sector only, %d%% of %d)" % (yi, round(p2 * 100), nn)]; cnt["yahoo_sector_only"] += 1
        if row:
            new_cls.append(row)
            if y and y.get("shares"): new_flt.append([sym, (y.get("name") or n.get("name") or "").upper(), y["shares"], y["float"] or ""])
        else:
            cnt["failed"] += 1; keep.append((sym, isin, "second source found nothing (NSE " + ("blocked/empty" if mode != "yahoo" else "off") + "; Yahoo industry '%s' not mappable)" % yi if yi else why))
        if i % 100 == 0: print("    second source %d/%d" % (i, len(todo)))
    print("second source: NSE %d, Yahoo %d, Yahoo sector-only %d, still unclassified %d" % (cnt["nse"], cnt["yahoo"], cnt["yahoo_sector_only"], cnt["failed"]))
    return new_cls, new_flt, keep, cnt


def cmd_probe(a):
    txt = http(BSE_HEAD % a.code, BSE_HDR).decode("utf-8", "replace")
    try: obj = json.loads(txt)
    except Exception:
        print("Not JSON (BSE may be blocking this network). First 300 chars:\n", txt[:300]); return 1
    print(json.dumps(obj, indent=2)[:6000])
    f = flat(obj)
    print("\n--- fields the builder will use (normalised key -> value) ---")
    for k in sorted(f):
        if any(t in k for t in ("sector", "industry", "igroup", "isub", "mkt", "cap", "rate", "price", "ltp", "isin", "face")): print("  %-22s %s" % (k, f[k]))
    return 0


def build(a, stocks, nse, bse, px, getj, yf):
    """Pure pipeline (network passed in) so it can be tested. Returns (cls, flt, universe, unmatched)."""
    out = a.out; cache = os.path.join(out, "cache"); ycache = os.path.join(cache, "yahoo")
    os.makedirs(cache, exist_ok=True); os.makedirs(ycache, exist_ok=True)
    K = lambda s, dflt: [s] if s else dflt
    unmatched, todo = [], []
    for sym in sorted(stocks):
        n = nse.get(sym)
        if not n or not n["isin"]: unmatched.append((sym, "", "not in NSE's equity list (ETF / fund / bond / other instrument)")); continue
        b = bse.get(n["isin"])
        if not b: unmatched.append((sym, n["isin"], "no BSE listing for this ISIN, so no classification")); continue
        todo.append((sym, n, b))
    if a.limit: todo = todo[:a.limit]
    print("BSE ComHeader for %d symbols (cached ones are skipped) ..." % len(todo))
    cls, flt, fails, src = [], [], 0, {"yahoo": 0, "bse": 0, "none": 0}
    for i, (sym, n, b) in enumerate(todo, 1):
        code = b["code"]; cf = os.path.join(cache, code + ".json")
        try:
            if os.path.exists(cf): obj = json.load(open(cf, encoding="utf-8"))
            else:
                obj = getj(code); json.dump(obj, open(cf, "w", encoding="utf-8")); time.sleep(a.delay)
            fails = 0
        except Exception as e:  # noqa
            fails += 1; unmatched.append((sym, n["isin"], "ComHeader failed: %s" % str(e)[:80]))
            if fails >= 25: print("Too many failures in a row — BSE is blocking or offline. Re-run later (progress is cached)."); break
            continue
        f = flat(obj)
        macro = clean(pick(f, K(a.macro_key, ["Sector"])))
        sector = clean(pick(f, K(a.sector_key, ["IndustryNew"])))
        ind = clean(pick(f, K(a.industry_key, ["IGroup"])))
        basic = clean(pick(f, K(a.basic_key, ["ISubgroup"])))
        if sector and ind and basic:
            cls.append([sym, clean(n["name"] or b["name"]), n["isin"], stocks[sym], macro, sector, ind, basic, "bse:ComHeader"])
        else:
            unmatched.append((sym, n["isin"], "ComHeader had no sector / industry / basic-industry (run probe)")); continue
        shares = fl = None; name = ""
        if a.float_source in ("yahoo", "auto") and yf is not None:
            y = yahoo_info(sym, ycache, a.ydelay, yf)
            if y and y.get("shares"): shares, fl, name = y["shares"], y["float"], y["name"]; src["yahoo"] += 1
        if not shares and a.float_source in ("bse", "auto"):
            mc, mff = num(pick(f, K(a.mcap_key, ["MktCapFull", "MarketCap", "Mcap"]))), num(pick(f, K(a.mcapff_key, ["MktCapFF", "FreeFloatMktCap"])))
            price = px.get(sym) or num(pick(f, K(a.price_key, ["CurrRate", "LTP", "Ltp", "Price", "CurrentValue"])))
            if mc and price and price > 0:
                shares = int(round(mc * a.unit / price)); fl = int(round(mff * a.unit / price)) if mff else None; name = (b["name"] or n["name"]).upper(); src["bse"] += 1
        if shares: flt.append([sym, name or (n["name"] or "").upper(), shares, fl if fl else ""])
        else: src["none"] += 1; unmatched.append((sym, n["isin"], "classified, but no shares/float from any source"))
        if i % 100 == 0: print("    %d/%d" % (i, len(todo)))
    if a.second_source != "off" and unmatched:
        n_cls, n_flt, unmatched, cnt = second_source(a, unmatched, nse, stocks, cls, ycache, yf, cache)
        cls += n_cls; flt += n_flt
    universe = [[r[0], r[3]] for r in cls]
    def w(name, head, rows):
        with open(os.path.join(out, name), "w", newline="", encoding="utf-8") as fh:
            cw = csv.writer(fh); cw.writerow(head); cw.writerows(rows)
    w("nse_classification.csv", CLS_HEAD, cls); w("STOCKS_FLOAT.csv", FLT_HEAD, flt); w("universe.csv", ["SYMBOL", "SERIES"], universe); w("unmatched.csv", ["Symbol", "ISIN", "Reason"], unmatched)
    print("done: %d classified, %d with shares/float (yahoo %d, bse estimate %d, none %d), %d left out (unmatched.csv) -> %s" %
          (len(cls), len(flt), src["yahoo"], src["bse"], src["none"], len(unmatched), os.path.abspath(out)))
    return cls, flt, universe, unmatched


def cmd_run(a):
    os.makedirs(a.out, exist_ok=True)
    stocks = read_stocks(a.stocks, a.series); print("1/4 stocks.csv: %d symbols (series %s)" % (len(stocks), a.series))
    if not stocks: print("No EQ/BE symbols found in %s" % a.stocks); return 1
    print("2/4 NSE equity list ..."); nse = load_nse_list(a.nse_list)
    print("3/4 BSE scrip list ..."); bse = load_bse_list(); print("    %d BSE scrips" % len(bse))
    yf = None
    if a.float_source in ("yahoo", "auto") or a.second_source in ("auto", "yahoo"):
        try:
            import yfinance as yf  # noqa
        except Exception:
            print("    yfinance is not installed (pip install yfinance) — using the BSE estimate for shares/float" if a.float_source == "auto" else "yfinance missing"); yf = None
            if a.float_source == "yahoo": return 1
    px = latest_prices(a.eod)
    print("4/4 building ...")
    build(a, stocks, nse, bse, px, lambda code: json.loads(http(BSE_HEAD % code, BSE_HDR).decode("utf-8", "replace")), yf)
    print("sanity check: open STOCKS_FLOAT.csv, find RELIANCE — shares should be about 13.5 billion (1.35e10).")
    return 0


def make_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    pr = s.add_parser("probe"); pr.add_argument("code", help="BSE scrip code, e.g. 500325")
    ru = s.add_parser("run")
    ru.add_argument("--stocks", default="stocks.csv", help="SYMBOL,SERIES list (EQ and BE rows are used)")
    ru.add_argument("--out", default="fundamentals"); ru.add_argument("--eod", default="eod", help="folder of daily CSVs (price for the BSE estimate)")
    ru.add_argument("--nse-list", default=None, help="local EQUITY_L.csv if the download is refused")
    ru.add_argument("--series", default="EQ,BE"); ru.add_argument("--limit", type=int, default=0)
    ru.add_argument("--delay", type=float, default=0.3, help="seconds between BSE calls"); ru.add_argument("--ydelay", type=float, default=0.25, help="seconds between Yahoo calls")
    ru.add_argument("--float-source", choices=["auto", "yahoo", "bse"], default="auto")
    ru.add_argument("--second-source", choices=["auto", "nse", "yahoo", "off"], default="auto", help="classify stocks BSE does not list: NSE industryInfo first, then Yahoo industry learned from the BSE-classified stocks")
    ru.add_argument("--min-share", type=float, default=0.6, help="a Yahoo industry must map to one BSE group for at least this share of the stocks already classified")
    ru.add_argument("--nse-delay", type=float, default=0.6)
    ru.add_argument("--unit", type=float, default=1e7, help="market-cap unit: 1e7 = Rs crore (BSE default)")
    for k in ("macro", "sector", "industry", "basic", "mcap", "mcapff", "price"): ru.add_argument("--%s-key" % k, default=None)
    return p


def main():
    a = make_parser().parse_args()
    sys.exit(cmd_probe(a) if a.cmd == "probe" else cmd_run(a))


if __name__ == "__main__":
    main()
