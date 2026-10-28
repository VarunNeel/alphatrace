#!/usr/bin/env python3
"""AlphaTrace — news + financials collector (runs on GitHub Actions; no phone needed).

READS   fundamentals/nse_classification.csv   (SYMBOL, COMPANY_NAME, MACRO_ECONOMIC_SECTOR, SECTOR, INDUSTRY, BASIC_INDUSTRY)
        fundamentals/universe.csv             (optional: the clean stock universe)
        info/priority.txt                     (optional: one SYMBOL per line, refreshed first)
WRITES  info/news/<A..Z|0>.json     { "SYMBOL": [[epoch_seconds, headline, source, url, tag], ...] }       newest first
        info/fund/<A..Z|0>.json     { "SYMBOL": { "u": epoch, "q": [[period, revenue_cr, profit_cr, ebitda_cr, eps], ...], "r": {ratios} } }
        info/groups.json            { "SECTOR:Name" | "INDUSTRY:Name" | "BASIC:Name": [[t, headline, source, url, tag], ...] }
        info/status.json            what worked and what did not (the app shows this)
        info/state.json             when each symbol was last fetched (so every run continues where the last stopped)
SOURCES Google News RSS (headlines + links only) and Yahoo Finance via yfinance (quarterly results + ratios).
USAGE   python news_fund.py run  [--news-limit 600] [--fund-limit 300] [--groups 1]
        python news_fund.py test                      # one tiny request per source, writes info/status.json
"""
import argparse, csv, json, os, re, sys, time, threading, html
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
ROOT = os.environ.get("NF_ROOT", ".")
INFO = os.path.join(ROOT, "info")
NEWS_DAYS, KEEP_NEWS, KEEP_Q = 45, 14, 12
TAGS = [("results", r"\b(results?|q[1-4]\b|quarter(ly)?|net profit|profit (rises|jumps|falls|drops|declines|surges)|revenue|earnings|ebitda|margin)"),
        ("order", r"\b(order|orders|contract|bags|wins|secures|awarded|lo[ai])\b"),
        ("rating", r"\b(upgrade[sd]?|downgrade[sd]?|target price|price target|rating|initiates|brokerage|overweight|underweight|outperform)"),
        ("deal", r"\b(block deal|bulk deal|stake|acquir\w+|acquisition|merger|demerger|buyback|open offer|takeover|fund ?raise|qip|ipo)"),
        ("corp", r"\b(dividend|bonus|stock split|split|rights issue|record date|board meeting|agm|egm)"),
        ("risk", r"\b(sebi|nclt|probe|penalty|fine[sd]?|raid|fraud|default|insolvency|downgrade|ed |cbi|notice)"),
        ("mgmt", r"\b(resigns?|appoints?|ceo|cfo|chairman|md |promoter|management)")]
TAGRE = [(t, re.compile(p, re.I)) for t, p in TAGS]


def tag_of(h):
    for t, rx in TAGRE:
        if rx.search(h):
            return t
    return ""


def get(url, tries=3, timeout=25):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"}), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 503):
                time.sleep(4 * (i + 1))
            elif e.code in (404, 400):
                break
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last if last else RuntimeError("request failed")


def clean_name(n):
    n = re.sub(r"\b(limited|ltd\.?|incorporated|inc\.?|corporation|corp\.?|company|co\.|pvt\.?|private)\b", " ", n or "", flags=re.I)
    return re.sub(r"\s+", " ", n).strip(" .,-")


def gnews(query, days=NEWS_DAYS, limit=KEEP_NEWS):
    q = urllib.parse.quote_plus(query + " when:%dd" % days)
    raw = get("https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % q)
    root = ET.fromstring(raw)
    out, seen = [], set()
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        src = ""
        se = it.find("source")
        if se is not None and se.text:
            src = se.text.strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        elif " - " in title and not src:
            title, src = title.rsplit(" - ", 1)
        key = re.sub(r"\W+", "", title.lower())[:80]
        if not title or key in seen:
            continue
        seen.add(key)
        try:
            t = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception:
            continue
        out.append([t, title[:220], src[:40], (it.findtext("link") or "")[:400], tag_of(title)])
    out.sort(key=lambda x: -x[0])
    return out[:limit]


def num(v, nd=2):
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return round(f, nd)
    except Exception:
        return None


def yahoo(sym):
    import yfinance as yf
    t = yf.Ticker(sym + ".NS")
    q = []
    try:
        df = t.quarterly_income_stmt
        if df is not None and len(df.columns):
            cols = sorted(df.columns, reverse=True)[:KEEP_Q]

            def row(*names):
                for n in names:
                    if n in df.index:
                        return df.loc[n]
                return None
            rv, npf, eb, ep = row("Total Revenue", "Operating Revenue"), row("Net Income", "Net Income Common Stockholders"), row("EBITDA", "Normalized EBITDA"), row("Basic EPS", "Diluted EPS")
            for c in cols:
                cr = lambda s: None if s is None else (None if num(s[c]) is None else round(float(s[c]) / 1e7, 2))
                q.append([str(c)[:7], cr(rv), cr(npf), cr(eb), None if ep is None else num(ep[c])])
    except Exception:
        pass
    r = {}
    try:
        info = t.get_info() or {}
        mp = {"pe": "trailingPE", "fpe": "forwardPE", "pb": "priceToBook", "roe": "returnOnEquity", "roa": "returnOnAssets", "de": "debtToEquity", "dy": "dividendYield",
              "pm": "profitMargins", "om": "operatingMargins", "rg": "revenueGrowth", "eg": "earningsGrowth", "beta": "beta"}
        for k, y in mp.items():
            v = num(info.get(y), 4)
            if v is not None:
                r[k] = v
        mc = num(info.get("marketCap"), 0)
        if mc:
            r["mc"] = round(mc / 1e7, 0)
        for k, y in (("hi", "fiftyTwoWeekHigh"), ("lo", "fiftyTwoWeekLow")):
            v = num(info.get(y), 2)
            if v is not None:
                r[k] = v
        if info.get("heldPercentInsiders") is not None:
            r["ins"] = num(info.get("heldPercentInsiders"), 4)
        if info.get("heldPercentInstitutions") is not None:
            r["inst"] = num(info.get("heldPercentInstitutions"), 4)
    except Exception:
        pass
    return q, r


def shard_of(sym):
    c = sym[:1].upper()
    return c if c.isalpha() else "0"


def jload(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d


def jsave(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, separators=(",", ":"))


def load_universe():
    cls = {}
    p = os.path.join(ROOT, "fundamentals", "nse_classification.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                s = (r.get("SYMBOL") or "").strip().upper()
                if s:
                    cls[s] = dict(name=(r.get("COMPANY_NAME") or "").strip(), macro=(r.get("MACRO_ECONOMIC_SECTOR") or "").strip(), sector=(r.get("SECTOR") or "").strip(),
                                  industry=(r.get("INDUSTRY") or "").strip(), basic=(r.get("BASIC_INDUSTRY") or "").strip())
    uni = []
    p = os.path.join(ROOT, "fundamentals", "universe.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                s = (r.get("SYMBOL") or r.get("Symbol") or "").strip().upper()
                if s:
                    uni.append(s)
    if not uni:
        uni = sorted(cls)
    return uni, cls


def pick(symbols, state, key, limit, priority):
    pri = [s for s in priority if s in set(symbols)]
    rest = sorted((s for s in symbols if s not in set(pri)), key=lambda s: state.get(s, {}).get(key, 0))
    return (pri + rest)[:limit]


def status_write(st):
    st["at"] = int(time.time())
    jsave(os.path.join(INFO, "status.json"), st)


def cmd_test(a):
    st = {"mode": "test", "sources": {}}
    try:
        n = gnews("Reliance Industries")
        st["sources"]["gnews"] = {"ok": bool(n), "items": len(n), "msg": "" if n else "no items"}
    except Exception as e:
        st["sources"]["gnews"] = {"ok": False, "msg": str(e)[:160]}
    try:
        q, r = yahoo("RELIANCE")
        st["sources"]["yahoo"] = {"ok": bool(q or r), "quarters": len(q), "ratios": len(r), "msg": "" if (q or r) else "empty reply (rate-limited or blocked)"}
    except Exception as e:
        st["sources"]["yahoo"] = {"ok": False, "msg": str(e)[:160]}
    status_write(st)
    print(json.dumps(st, indent=1))
    return 0


def cmd_run(a):
    uni, cls = load_universe()
    if not uni:
        print("No symbols: fundamentals/nse_classification.csv or universe.csv is missing."); return 1
    state = jload(os.path.join(INFO, "state.json"), {})
    prio = []
    pp = os.path.join(INFO, "priority.txt")
    if os.path.exists(pp):
        prio = [l.strip().upper() for l in open(pp, encoding="utf-8") if l.strip()]
    st = {"mode": "run", "universe": len(uni), "sources": {}, "news": {"ok": 0, "fail": 0, "empty": 0}, "fund": {"ok": 0, "fail": 0}, "groups": {"ok": 0, "fail": 0}}
    now = int(time.time())
    lock = threading.Lock()
    # ---------- news ----------
    todo = pick(uni, state, "n", a.news_limit, prio)
    news_new = {}
    fails = {"n": 0}

    def do_news(s):
        if fails["n"] >= 25:
            return
        nm = clean_name(cls.get(s, {}).get("name")) or s
        q = '"%s"' % nm if len(nm) > 3 else '"%s" NSE stock' % s
        try:
            items = gnews(q + " stock") if False else gnews(q)
            with lock:
                news_new[s] = items
                st["news"]["ok" if items else "empty"] += 1
                state.setdefault(s, {})["n"] = now
                fails["n"] = 0
        except Exception as e:
            with lock:
                st["news"]["fail"] += 1
                fails["n"] += 1
                st["sources"]["gnews"] = {"ok": False, "msg": str(e)[:160]}
        time.sleep(0.35)
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(do_news, todo))
    if fails["n"] >= 25:
        st["sources"]["gnews"] = {"ok": False, "msg": "stopped after 25 failures in a row (rate-limited?)"}
    elif "gnews" not in st["sources"]:
        st["sources"]["gnews"] = {"ok": st["news"]["ok"] > 0, "msg": ""}
    by_shard = {}
    for s, items in news_new.items():
        by_shard.setdefault(shard_of(s), {})[s] = items
    cutoff = now - 60 * 86400
    for sh, upd in by_shard.items():
        p = os.path.join(INFO, "news", sh + ".json")
        cur = jload(p, {})
        for s, items in upd.items():
            old = [x for x in cur.get(s, []) if x[0] >= cutoff]
            seen = {re.sub(r"\W+", "", x[1].lower())[:80] for x in items}
            merged = items + [x for x in old if re.sub(r"\W+", "", x[1].lower())[:80] not in seen]
            merged.sort(key=lambda x: -x[0])
            cur[s] = merged[:KEEP_NEWS + 6]
        jsave(p, cur)
    # ---------- groups ----------
    if a.groups:
        names = {}
        for c in cls.values():
            for k, v in (("SECTOR", c["sector"]), ("INDUSTRY", c["industry"]), ("BASIC", c["basic"])):
                if v and v.lower() not in ("unclassified", "na", "-"):
                    names[k + ":" + v] = v
        g = jload(os.path.join(INFO, "groups.json"), {})
        gl = list(names)[: a.groups_limit]

        def do_group(key):
            try:
                items = gnews("India %s sector stocks" % names[key] if key.startswith("SECTOR") else "India %s industry stocks" % names[key], limit=KEEP_NEWS + 6)
                with lock:
                    g[key] = items
                    st["groups"]["ok"] += 1
            except Exception:
                with lock:
                    st["groups"]["fail"] += 1
            time.sleep(0.35)
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(do_group, gl))
        jsave(os.path.join(INFO, "groups.json"), g)
    # ---------- fundamentals ----------
    todo = pick(uni, state, "f", a.fund_limit, prio)
    fund_new = {}
    yfails = {"n": 0}

    def do_fund(s):
        if yfails["n"] >= 25:
            return
        try:
            q, r = yahoo(s)
            with lock:
                if q or r:
                    fund_new[s] = {"u": now, "q": q, "r": r}
                    st["fund"]["ok"] += 1
                    yfails["n"] = 0
                else:
                    st["fund"]["fail"] += 1
                    yfails["n"] += 1
                state.setdefault(s, {})["f"] = now
        except Exception as e:
            with lock:
                st["fund"]["fail"] += 1
                yfails["n"] += 1
                st["sources"]["yahoo"] = {"ok": False, "msg": str(e)[:160]}
        time.sleep(0.6)
    if todo:
        try:
            import yfinance  # noqa
            with ThreadPoolExecutor(max_workers=3) as ex:
                list(ex.map(do_fund, todo))
        except ImportError:
            st["sources"]["yahoo"] = {"ok": False, "msg": "yfinance is not installed"}
    if yfails["n"] >= 25:
        st["sources"]["yahoo"] = {"ok": False, "msg": "stopped after 25 empty replies in a row (rate-limited?)"}
    elif "yahoo" not in st["sources"]:
        st["sources"]["yahoo"] = {"ok": st["fund"]["ok"] > 0, "msg": ""}
    by_shard = {}
    for s, o in fund_new.items():
        by_shard.setdefault(shard_of(s), {})[s] = o
    for sh, upd in by_shard.items():
        p = os.path.join(INFO, "fund", sh + ".json")
        cur = jload(p, {})
        cur.update(upd)
        jsave(p, cur)
    jsave(os.path.join(INFO, "state.json"), state)
    status_write(st)
    print(json.dumps(st, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")
    r = sp.add_parser("run")
    r.add_argument("--news-limit", type=int, default=600)
    r.add_argument("--fund-limit", type=int, default=300)
    r.add_argument("--groups", type=int, default=1)
    r.add_argument("--groups-limit", type=int, default=400)
    sp.add_parser("test")
    a = ap.parse_args()
    if a.cmd == "test":
        return cmd_test(a)
    if a.cmd == "run":
        return cmd_run(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
