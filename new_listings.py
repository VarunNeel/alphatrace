#!/usr/bin/env python3
"""AlphaTrace — new listings (IPOs). Python 3.8+  (standard library; `pip install yfinance` for `backfill`)

Your stocks.csv is a fixed list, so a stock that lists after you made it never enters the pipeline. This closes that gap.

  python new_listings.py sync      NSE's official list (EQUITY_L.csv) vs stocks.csv  ->  new symbols are APPENDED to stocks.csv
                                   and logged in ohlcv/new/new_listings.csv (SYMBOL,SERIES,LISTING_DATE,NAME,FIRST_SEEN)
  python new_listings.py backfill  full Yahoo price history of every symbol listed in the last --days (default 120) days
                                   ->  ohlcv/new/NEW_OHLCV.csv   (Date,Adj Close,Close,High,Low,Open,Volume,Symbol)
  python new_listings.py universe  adds those recent listings to fundamentals/universe.csv (BSE often has no classification for a
                                   fresh IPO yet, so fundamentals.py would leave it out and the app would hide it)

Why a window: Yahoo usually has nothing on the listing day itself, so a symbol stays in the backfill for --days days and is
retried every run until Yahoo has it. After that the normal daily job (stocks.csv now includes it) carries it forward.
App: Import > Pull from GitHub > "New listings".
"""
import argparse, csv, datetime as dt, io, os, re, sys, time, urllib.request

UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
NSE_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_HDR = {"User-Agent": UA, "Accept": "text/csv,*/*", "Referer": "https://www.nseindia.com/"}
LOG_HEAD = ["SYMBOL", "SERIES", "LISTING_DATE", "NAME", "FIRST_SEEN"]
# rights entitlements (-RE, -RE1), partly-paid (-PP) and warrants (-W1) are temporary instruments, not stocks
JUNK = re.compile(r"-(RE|PP|W)\d*$")
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def norm(k): return "".join(ch for ch in str(k).lower() if ch.isalnum())


def parse_date(s):
    s = str(s or "").strip().upper()
    for f in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    try:  # DD-MON-YYYY with an unusual month spelling
        d, m, y = s.split("-"); return dt.date(int(y), MONTHS[m[:3]], int(d))
    except Exception:
        return None


def fetch_nse(path=None):
    if path:
        text = open(path, encoding="utf-8-sig").read()
    else:
        last = None
        for i in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(NSE_LIST, headers=NSE_HDR), timeout=40) as r:
                    text = r.read().decode("utf-8-sig"); break
            except Exception as e:  # noqa
                last = e; time.sleep(2 * (i + 1))
        else:
            raise SystemExit("could not download EQUITY_L.csv (%s). Pass --nse-list FILE if NSE refuses the download." % last)
    rows = list(csv.reader(io.StringIO(text)))
    hd = [norm(h) for h in rows[0]]
    def col(*names):
        for n in names:
            if n in hd: return hd.index(n)
        return -1
    iS, iN, iSe, iD = col("symbol"), col("nameofcompany", "name"), col("series"), col("dateoflisting", "listingdate")
    if iS < 0 or iSe < 0: raise SystemExit("EQUITY_L.csv has unexpected columns: %s" % rows[0])
    out = []
    for r in rows[1:]:
        if len(r) <= max(iS, iSe): continue
        sym = r[iS].strip().upper(); se = r[iSe].strip().upper()
        if sym and se in ("EQ", "BE") and not JUNK.search(sym):
            out.append({"SYMBOL": sym, "SERIES": se, "NAME": r[iN].strip() if iN >= 0 and len(r) > iN else "", "DATE": parse_date(r[iD]) if iD >= 0 and len(r) > iD else None})
    return out


def read_symbols(path):
    if not os.path.exists(path): return [], set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    hd = [norm(h) for h in (rows[0] if rows else [])]
    has = "symbol" in hd; iS = hd.index("symbol") if has else 0
    return rows, {r[iS].strip().upper() for r in rows[1 if has else 0:] if len(r) > iS and r[iS].strip()}


def read_log(path):
    if not os.path.exists(path): return []
    with open(path, newline="", encoding="utf-8") as fh: return list(csv.DictReader(fh))


def write_log(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(LOG_HEAD); w.writerows([[r.get(h, "") for h in LOG_HEAD] for r in rows])


def cmd_sync(a):
    nse = fetch_nse(a.nse_list)
    if len(nse) < 1000: raise SystemExit("EQUITY_L.csv has only %d EQ/BE rows — looks like a bad download; stocks.csv left untouched" % len(nse))
    rows, have = read_symbols(a.stocks)
    new = [r for r in nse if r["SYMBOL"] not in have]
    today = dt.date.today().isoformat(); log_path = os.path.join(a.out, "new_listings.csv"); log = read_log(log_path); logged = {r["SYMBOL"] for r in log}
    print("NSE list: %d EQ/BE symbols; stocks.csv: %d; new: %d" % (len(nse), len(have), len(new)))
    if new:
        with open(a.stocks, "a", newline="", encoding="utf-8") as fh:
            if rows and rows[-1] and not open(a.stocks, "rb").read().endswith(b"\n"): fh.write("\n")
            w = csv.writer(fh)
            for r in new: w.writerow([r["SYMBOL"], r["SERIES"]])
        for r in new:
            if r["SYMBOL"] not in logged:
                log.append({"SYMBOL": r["SYMBOL"], "SERIES": r["SERIES"], "LISTING_DATE": r["DATE"].isoformat() if r["DATE"] else "", "NAME": r["NAME"], "FIRST_SEEN": today})
        for r in new[:40]: print("  + %-14s %s  listed %s  %s" % (r["SYMBOL"], r["SERIES"], r["DATE"] or "?", r["NAME"]))
        if len(new) > 40: print("  … and %d more" % (len(new) - 40))
    junk = {r["SYMBOL"] for r in log if JUNK.search(r["SYMBOL"])}
    if junk:  # an earlier version let these in — take them back out of the log and out of stocks.csv
        log = [r for r in log if r["SYMBOL"] not in junk]
        with open(a.stocks, newline="", encoding="utf-8-sig") as fh: keep = [r for r in csv.reader(fh) if not (r and r[0].strip().upper() in junk)]
        with open(a.stocks, "w", newline="", encoding="utf-8") as fh: csv.writer(fh).writerows(keep)
        print("removed %d temporary instruments: %s" % (len(junk), ", ".join(sorted(junk))))
    # listing dates for symbols already logged but missing one (EQUITY_L fills them in later)
    dates = {r["SYMBOL"]: r["DATE"] for r in nse if r["DATE"]}
    for r in log:
        if not r.get("LISTING_DATE") and r["SYMBOL"] in dates: r["LISTING_DATE"] = dates[r["SYMBOL"]].isoformat()
    write_log(log_path, log)
    prune_nonstocks(a, nse)  # ETFs and funds never enter the price jobs
    return 0


# ---- not stocks: ETFs, index funds, gold / silver / liquid / gilt funds, rights entitlements ----
ETF_LIST = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"
SEED_NONSTOCK = set(["ABGSEC", "ABSL10BANK", "ABSLBANETF", "ABSLLIQUID", "ABSLMSCIN", "ABSLNN50ET", "ABSLPSE", "ALPHA", "ALPHAETF", "ALPL30IETF", "AONEGOLD", "AONELIQUID", "AONENIFTY", "AONESILVER", "AONETMMQ50", "AONETOTAL", "AUTOBEES", "AUTOIETF", "BANK10ADD", "BANK10BETF", "BANKA", "BANKADD", "BANKBEES", "BANKBETA", "BANKBETF", "BANKETF", "BANKIETF", "BANKNIFTY1", "BANKPSU", "BBETF0432", "BBNPNBETF", "BBNPPGOLD", "BFSI", "BNKETFAXIS", "BSE500IETF", "BSLGOLDETF", "BSLNIFTY", "BSLSENETFG", "CASHIETF", "CEMNTGROWW", "CENTEXT-RE", "CHEMICAL", "CHOICEGOLD", "COMMOIETF", "CONS", "CONSUMAXIS", "CONSUMBEES", "CONSUMER", "CONSUMIETF", "CPSEETF", "DEFENCE", "DIVIDEND", "DIVOPPBEES", "EBANK10", "EBANKNIFTY", "EBBETF0430", "EBBETF0431", "EBBETF0433", "ECAPINSURE", "EGOLD", "ELIQUID", "ELM250", "ELMDIV", "EMETAL", "EMULTIMQ", "ENERGY", "ENERGYAXIS", "ENEXT50", "ENIFTY", "EQUAL200", "EQUAL50", "EQUAL50ADD", "ESENSEX", "ESG", "ESILVER", "EVIETF", "EVINDIA", "FINIETF", "FLEXIADD", "FMCGADD", "FMCGIETF", "GILT10BETA", "GILT5BETA", "GILT5YBEES", "GOLD1", "GOLD360", "GOLDADD", "GOLDAXIS", "GOLDBEES", "GOLDBETA", "GOLDBND", "GOLDCASE", "GOLDETF", "GOLDIETF", "GROWWCAPM", "GROWWCHEM", "GROWWDEFNC", "GROWWEV", "GROWWGOLD", "GROWWHOSPI", "GROWWLIQID", "GROWWLOVOL", "GROWWMC150", "GROWWMETAL", "GROWWMOM50", "GROWWN200", "GROWWNET", "GROWWNIFTY", "GROWWNXT50", "GROWWPOWER", "GROWWPSE", "GROWWPSUBK", "GROWWRAIL", "GROWWRLTY", "GROWWSC250", "GROWWSLVR", "GSEC10ABSL", "GSEC10ADD", "GSEC10IETF", "GSEC10YEAR", "GSEC5IETF", "HDFCBSE500", "HDFCGOLD", "HDFCGROWTH", "HDFCLIQUID", "HDFCLOWVOL", "HDFCMID150", "HDFCMOMENT", "HDFCNEXT50", "HDFCNIF100", "HDFCNIFBAN", "HDFCNIFIT", "HDFCNIFTY", "HDFCNIMEG", "HDFCPSUBK", "HDFCPVTBAN", "HDFCQUAL", "HDFCSENSEX", "HDFCSILVER", "HDFCSML250", "HDFCVALUE", "HEALTHADD", "HEALTHAXIS", "HEALTHCARE", "HEALTHIETF", "HEALTHY", "HNGSNGBEES", "HSBCGOLD", "HYBRIDETF", "ICICIB22", "IDFNIFTYET", "INFRA", "INFRABEES", "INFRAIETF", "INSURADD", "INSUREIETF", "INTERNET", "IT", "ITADD", "ITAXIS", "ITBEES", "ITBETA", "ITETF", "ITIETF", "IVZBANKNF", "IVZINGOLD", "IVZINNIFTY", "IVZSENSEX", "JUNIORBEES", "LARGEMID50", "LICMFGOLD", "LICNETFGSC", "LICNETFN50", "LICNETFSEN", "LICNFNHGP", "LICNMID100", "LIQGRWBEES", "LIQUID", "LIQUID1", "LIQUIDADD", "LIQUIDBEES", "LIQUIDBETA", "LIQUIDBETF", "LIQUIDCASE", "LIQUIDETF", "LIQUIDIETF", "LIQUIDPLUS", "LIQUIDSBI", "LIQUIDSHRI", "LOWVOL", "LOWVOL1", "LOWVOLIETF", "LTGILTBEES", "LTGILTCASE", "MAFANG", "MAKEINDIA", "MANUFGBEES", "MASPTOP50", "METAL", "METALIETF", "MID150", "MID150BEES", "MID150CASE", "MIDBANKADD", "MIDCAP", "MIDCAPADD", "MIDCAPBETA", "MIDCAPETF", "MIDCAPIETF", "MIDQ50ADD", "MIDSELIETF", "MIDSMALL", "MNC", "MOALPHA50", "MOBANK10", "MOCAPITAL", "MODEFENCE", "MOENERGY", "MOGOLD", "MOGSEC", "MOHEALTH", "MOINFRA", "MOIPO", "MOLOWVOL", "MOM100", "MOM30IETF", "MOM50", "MOMENTUM", "MOMENTUM30", "MOMENTUM50", "MOMETAL", "MOMGF", "MOMIDMTM", "MOMMIDCAP", "MOMNC", "MOMOMENTUM", "MON100", "MON50EQUAL", "MONEXT50", "MONIFTY100", "MONIFTY500", "MONQ50", "MOOILGAS", "MOPSE", "MOQUALITY", "MOREALTY", "MOSERVICE", "MOSILVER", "MOSMALL250", "MOTOUR", "MOVALUE", "MSCI360", "MSCIADD", "MSCIINDIA", "MULTICAP", "NETF", "NEXT30ADD", "NEXT50", "NEXT50ADD", "NEXT50BETA", "NEXT50ETF", "NEXT50IETF", "NIF100BEES", "NIF100IETF", "NIF500BETA", "NIFTY1", "NIFTY100EW", "NIFTYADD", "NIFTYAXIS", "NIFTYBEES", "NIFTYBETA", "NIFTYBETF", "NIFTYCASE", "NIFTYETF", "NIFTYIETF", "NIFTYJBLK", "NIFTYQLITY", "NPBET", "NV20", "NV20BEES", "NV20IETF", "OILIETF", "PHARMABEES", "PSUBANK", "PSUBANKADD", "PSUBNKBEES", "PSUBNKIETF", "PVTBANIETF", "PVTBANK", "PVTBANKADD", "PVTBKGROWW", "QGOLDHALF", "QNIFTY", "QUAL30IETF", "QUALITY30", "SBIBPB", "SBIETFCON", "SBIETFIT", "SBIETFPB", "SBIETFQLTY", "SBILIQETF", "SBIMIDMOM", "SBINEQWETF", "SBINMID150", "SBISILVER", "SBISMLETF", "SBIVALETF", "SECLEDBETA", "SELECTIPO", "SENSEXADD", "SENSEXAXIS", "SENSEXBETA", "SENSEXETF", "SENSEXIETF", "SETF10GILT", "SETFGOLD", "SETFNIF50", "SETFNIFBK", "SETFNN50", "SHARIABEES", "SILVER", "SILVER001", "SILVER1", "SILVER360", "SILVERADD", "SILVERAG", "SILVERAXIS", "SILVERBEES", "SILVERBETA", "SILVERBND", "SILVERCASE", "SILVERIETF", "SMALL250", "SMALLADD", "SMALLCAP", "SMALLGROWW", "SMALLIETF", "SML100CASE", "SNXT30BEES", "SNXT50BETA", "TATAGOLD", "TATSILV", "TECH", "TNIDETF", "TOP100CASE", "TOP10ADD", "TOP15IETF", "TOP20", "TWCGOLDETF", "UNIONGOLD", "VAL30IETF", "VALUE", "VALUEAXIS"])
STRONG = re.compile(r"(ETF|BEES|IETF|ETFG)$|^(LIQUID|GILT|GSEC|SILVER|GOLDBEES|NIFTYBEES)")


def nonstocks(nse_syms, have):
    """Symbols that are not company shares. NSE's own ETF list when it downloads, the known list, rights / warrants,
    and fund-style names that are NOT on NSE's equity list (a real company with such a name is on the equity list, so it stays)."""
    out = set(SEED_NONSTOCK)
    try:
        with urllib.request.urlopen(urllib.request.Request(ETF_LIST, headers=NSE_HDR), timeout=40) as r:
            rows = list(csv.reader(io.StringIO(r.read().decode("utf-8-sig", "replace"))))
        hd = [norm(h) for h in rows[0]]; iS = hd.index("symbol") if "symbol" in hd else 0
        etf = {x[iS].strip().upper() for x in rows[1:] if len(x) > iS and x[iS].strip()}
        if len(etf) > 50: out |= etf; print("NSE ETF list: %d symbols" % len(etf))
    except Exception as e:  # noqa
        print("NSE ETF list not available (%s) — using the known list and name rules" % e)
    for s in have:
        if s in nse_syms: continue
        if JUNK.search(s) or STRONG.search(s): out.add(s)
    return out - set(nse_syms)


def prune_nonstocks(a, nse):
    nse_syms = {r["SYMBOL"] for r in nse}
    with open(a.stocks, newline="", encoding="utf-8-sig") as fh: rows = list(csv.reader(fh))
    have = {r[0].strip().upper() for r in rows[1:] if r and r[0].strip()}
    bad = nonstocks(nse_syms, have)
    path = os.path.join("fundamentals", "non_stocks.csv"); old = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh: old = {x[0].strip().upper() for x in list(csv.reader(fh))[1:] if x}
    allbad = sorted((bad | old) - nse_syms)
    os.makedirs("fundamentals", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["SYMBOL"]); w.writerows([[x] for x in allbad])
    gone = [r for r in rows[1:] if r and r[0].strip().upper() in bad]
    if gone:
        keep = [rows[0]] + [r for r in rows[1:] if not (r and r[0].strip().upper() in bad)]
        with open(a.stocks, "w", newline="", encoding="utf-8") as fh: csv.writer(fh).writerows(keep)
    print("not stocks: %d removed from stocks.csv, %d listed in fundamentals/non_stocks.csv" % (len(gone), len(allbad)))


def recent(a):
    cutoff = (dt.date.today() - dt.timedelta(days=a.days)).isoformat(); out = []
    for r in read_log(os.path.join(a.out, "new_listings.csv")):
        d = r.get("LISTING_DATE") or r.get("FIRST_SEEN") or ""
        if d >= cutoff: out.append((r["SYMBOL"], d))
    return out


def cmd_backfill(a):
    import yahoo_ohlcv as Y
    rec = recent(a)
    if not rec: print("backfill: no listings in the last %d days — nothing to do" % a.days); return 0
    try: import yfinance as yf
    except ImportError: raise SystemExit("pip install yfinance")
    syms = [s for s, _ in rec]; start = min(parse_date(d) or dt.date.today() for _, d in rec) - dt.timedelta(days=5)
    print("backfill: %d symbols listed since %s" % (len(syms), start))
    rows, got = [], set()
    for s, rr in Y.download(yf, syms, start.isoformat(), (dt.date.today() + dt.timedelta(days=1)).isoformat(), a.batch, a.delay):
        got.add(s); rows.extend(rr)
    miss = [s for s in syms if s not in got]
    if miss: print("backfill: Yahoo has nothing yet for %s — retried on the next run" % ", ".join(miss[:30]))
    if not rows: print("backfill: no data returned — previous NEW_OHLCV.csv kept"); return 0
    rows.sort(key=lambda r: (r[7], r[0])); Y.write_csv(os.path.join(a.out, "NEW_OHLCV.csv"), rows)
    print("backfill: %d bars for %d symbols -> %s" % (len(rows), len(got), os.path.join(a.out, "NEW_OHLCV.csv"))); return 0


def cmd_universe(a):
    rec = {s for s, _ in recent(a) if not JUNK.search(s)}
    if not os.path.exists(a.universe): print("universe: nothing to add"); return 0
    with open(a.universe, newline="", encoding="utf-8") as fh: rows = list(csv.reader(fh))
    clean = [r for i, r in enumerate(rows) if i == 0 or not (r and JUNK.search(r[0].strip().upper()))]
    if len(clean) != len(rows):
        with open(a.universe, "w", newline="", encoding="utf-8") as fh: csv.writer(fh).writerows(clean)
        print("universe: removed %d temporary instruments (rights entitlements etc.)" % (len(rows) - len(clean))); rows = clean
    if not rec: print("universe: nothing to add"); return 0
    have = {r[0].strip().upper() for r in rows[1:] if r}; ser = {r["SYMBOL"]: r["SERIES"] for r in read_log(os.path.join(a.out, "new_listings.csv"))}
    add = sorted(s for s in rec if s not in have)
    if not add: print("universe: all recent listings already present"); return 0
    with open(a.universe, "a", newline="", encoding="utf-8") as fh:
        if not open(a.universe, "rb").read().endswith(b"\n"): fh.write("\n")
        w = csv.writer(fh)
        for s in add: w.writerow([s, ser.get(s, "EQ")])
    print("universe: added %d recent listings (%s)" % (len(add), ", ".join(add[:20]))); return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); s = p.add_subparsers(dest="cmd", required=True)
    for name in ("sync", "backfill", "universe"):
        q = s.add_parser(name); q.add_argument("--stocks", default="stocks.csv"); q.add_argument("--out", default="ohlcv/new")
        q.add_argument("--days", type=int, default=120, help="how long a new listing stays in the backfill window")
        if name == "sync": q.add_argument("--nse-list", default=None, help="local EQUITY_L.csv if NSE refuses the download")
        if name == "backfill": q.add_argument("--batch", type=int, default=100); q.add_argument("--delay", type=float, default=1.0)
        if name == "universe": q.add_argument("--universe", default="fundamentals/universe.csv")
    a = p.parse_args(); sys.exit({"sync": cmd_sync, "backfill": cmd_backfill, "universe": cmd_universe}[a.cmd](a))


if __name__ == "__main__":
    main()
