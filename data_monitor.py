#!/usr/bin/env python3
"""
AlphaTrace data monitor.

Watches the price files in this repo and writes a health report. Optionally repairs the
faults that can be repaired safely, using ONLY this repo's own daily files
(ohlcv/daily/<date>.csv, the NSE-sourced end-of-day files). It never uses Upstox, never
guesses a split, and never edits a price on its own judgment.

  python data_monitor.py                 check only, write ohlcv/health.json + ohlcv/quarantine.csv
  python data_monitor.py --repair        also fix the safe faults (see below) and log every change

Safe repairs (all from ohlcv/daily):
  1. A date whose history rows are incomplete (far fewer stocks than the daily file) is completed.
  2. A date whose history rows are a mid-day snapshot (many closes differ from the final daily
     file) is replaced by the daily rows.
  3. A stock missing a trading day inside its own history gets that bar from the daily file.
Every changed row is appended to ohlcv/repair_log.csv (old and new value, source, time).

Suspect bars (one-day moves beyond -40% / +60%, High < Low, Close outside High-Low, zero or
negative prices) are never edited: they are listed in ohlcv/quarantine.csv for a human.

Standard library only.
"""
import argparse, csv, datetime as dt, glob, gzip, io, json, os, statistics, sys

COLS = ["Date", "Adj Close", "Close", "High", "Low", "Open", "Volume", "Symbol"]
IST = dt.timedelta(hours=5, minutes=30)


def fnum(x):
    try:
        return float(x)
    except Exception:
        return None


def read_csv(path, gz=False):
    op = gzip.open if gz else open
    with op(path, "rt", newline="") as f:
        r = csv.reader(f)
        head = next(r, None)
        if not head:
            return []
        ix = {c: head.index(c) for c in COLS if c in head}
        if any(c not in ix for c in COLS):
            return []
        return [tuple(row[ix[c]] for c in COLS) for row in r if len(row) >= len(head)]


def write_hist(path, rows):
    rows = sorted(rows, key=lambda t: (t[0], t[7]))
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", newline="", compresslevel=6) as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--window", type=int, default=15, help="trading days checked for incomplete/stale history")
    ap.add_argument("--hole-days", type=int, default=120, help="trading days checked for holes")
    ap.add_argument("--years", type=int, default=2, help="history years loaded for the suspect-bar scan")
    ap.add_argument("--now", default="", help="test only: ISO UTC time to pretend it is")
    ap.add_argument("--issue-body", default="", help="write the text of the GitHub Issue here")
    a = ap.parse_args()

    root = a.root
    oh = os.path.join(root, "ohlcv")
    now = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.utcnow()
    ist = now + IST
    today = ist.date().isoformat()
    after_close = ist.hour * 60 + ist.minute >= 20 * 60 + 30  # today's daily file counts as final after 20:30 IST

    # ---- load history (last N years) and daily files (last 60 days) -------------------------
    yr = ist.year
    hist_files = {}
    hist = []
    for y in range(yr - a.years + 1, yr + 1):
        p = os.path.join(oh, "history", "HIST_OHLCV_%d.csv.gz" % y)
        if os.path.exists(p):
            rows = read_csv(p, gz=True)
            hist_files[y] = p
            hist.extend(rows)
    if not hist:
        print("No history files found under", oh)
        return

    daily = {}  # date -> {symbol: row}
    cutoff = (ist.date() - dt.timedelta(days=75)).isoformat()
    for p in sorted(glob.glob(os.path.join(oh, "daily", "*.csv"))):
        if os.path.basename(p)[:10] < cutoff:
            continue
        for r in read_csv(p):
            if r[0] >= cutoff and dt.date.fromisoformat(r[0]).weekday() < 5:
                daily.setdefault(r[0], {})[r[7]] = r

    hbyd = {}
    for r in hist:
        hbyd.setdefault(r[0], {})[r[7]] = r

    recent_counts = [len(v) for k, v in sorted(hbyd.items())[-60:]]
    U = int(statistics.median(recent_counts)) if recent_counts else 0
    if U == 0:
        print("History is empty")
        return

    # trading calendar: weekdays where either source has at least half the usual number of stocks
    cal = sorted(d for d in set(hbyd) | set(daily)
                 if dt.date.fromisoformat(d).weekday() < 5 and max(len(hbyd.get(d, {})), len(daily.get(d, {}))) >= 0.5 * U)

    def eligible(d):
        return d < today or (d == today and after_close)

    issues = []
    repairs = []  # (date, symbol, kind, old_close, new_close)
    new_hist_rows = {}  # (date, symbol) -> row to put in history (replaces or adds)

    def close_diff(h, d):
        x, y = fnum(h[2]), fnum(d[2])
        return x is not None and y is not None and x > 0 and abs(y / x - 1) > 0.005

    win = [d for d in cal if eligible(d)][-a.window:]
    day_report = []
    for d in win:
        hd, dd = hbyd.get(d, {}), daily.get(d, {})
        hc, dc = len(hd), len(dd)
        state = "ok"
        daily_ok = dc >= 0.9 * U
        if hc < 0.9 * U:
            if daily_ok:
                state = "history incomplete"
                issues.append({"kind": "history_incomplete", "date": d, "detail": "history has %d of ~%d stocks; the daily file has %d" % (hc, U, dc), "fixable": True})
                for s, r in dd.items():
                    if s not in hd:
                        new_hist_rows[(d, s)] = r
                        repairs.append((d, s, "add", "", r[2]))
            else:
                state = "history incomplete, no daily to repair from"
                issues.append({"kind": "history_incomplete", "date": d, "detail": "history has %d of ~%d stocks and the daily file has only %d" % (hc, U, dc), "fixable": False})
        elif daily_ok:
            both = [s for s in hd if s in dd]
            diff = [s for s in both if close_diff(hd[s], dd[s])]
            if both and len(diff) > 0.05 * len(both):
                state = "history is a snapshot"
                issues.append({"kind": "history_stale", "date": d, "detail": "%d of %d closes differ from the final daily file (mid-day snapshot)" % (len(diff), len(both)), "fixable": True})
                for s in diff:
                    new_hist_rows[(d, s)] = dd[s]
                    repairs.append((d, s, "replace", hd[s][2], dd[s][2]))
                for s, r in dd.items():
                    if s not in hd:
                        new_hist_rows[(d, s)] = r
                        repairs.append((d, s, "add", "", r[2]))
        day_report.append({"date": d, "history": hc, "daily": dc, "state": state})

    # ---- holes: a stock misses a trading day between two days it has ----------------------
    hole_cal = [d for d in cal if eligible(d)][-a.hole_days:]
    idx = {d: i for i, d in enumerate(hole_cal)}
    by_sym = {}
    for r in hist:
        if r[0] in idx:
            by_sym.setdefault(r[7], set()).add(idx[r[0]])
    holes = []
    for s, have in by_sym.items():
        lo, hi = min(have), max(have)
        for i in range(lo + 1, hi):
            if i not in have:
                holes.append((hole_cal[i], s))
    unfixable_holes = []
    hole_by_stock = {}
    for d, s in holes:
        r = daily.get(d, {}).get(s)
        if r and (d, s) not in new_hist_rows:
            new_hist_rows[(d, s)] = r
            repairs.append((d, s, "fill hole", "", r[2]))
        elif not r:
            if len(daily.get(d, {})) >= 0.9 * U:
                continue  # the complete daily file has no bar either: the stock did not trade that day
            unfixable_holes.append((d, s))
        hole_by_stock.setdefault(s, []).append(d)
    whole_day = {i["date"] for i in issues if i["kind"] in ("history_incomplete", "history_stale")}
    unfix = set(unfixable_holes)
    shown = 0
    for s, ds in sorted(hole_by_stock.items(), key=lambda kv: -len(kv[1])):
        ds = [d for d in ds if d not in whole_day]  # a whole-day problem is already reported once
        if not ds:
            continue
        miss = [d for d in ds if (d, s) in unfix]
        if shown < 40 or miss:
            issues.append({"kind": "hole", "date": ds[-1], "symbol": s, "detail": "%s is missing %d trading day(s): %s%s" % (s, len(ds), ", ".join(ds[:4]), "…" if len(ds) > 4 else ""), "fixable": not miss})
            shown += 1

    # ---- apply repairs -----------------------------------------------------------------------
    applied = 0
    if a.repair and new_hist_rows:
        byyear = {}
        for (d, s), r in new_hist_rows.items():
            byyear.setdefault(int(d[:4]), {})[(d, s)] = r
        for y, chg in byyear.items():
            p = hist_files.get(y)
            if not p:
                continue
            rows = read_csv(p, gz=True)
            out = []
            seen = set()
            for r in rows:
                k = (r[0], r[7])
                if k in chg:
                    out.append(chg[k])
                    seen.add(k)
                else:
                    out.append(r)
            for k, r in chg.items():
                if k not in seen:
                    out.append(r)
            write_hist(p, out)
            applied += len(chg)
        log = os.path.join(oh, "repair_log.csv")
        new_file = not os.path.exists(log)
        with open(log, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["run_utc", "Date", "Symbol", "action", "old_close", "new_close", "source"])
            for d, s, k, o, n in repairs:
                w.writerow([now.strftime("%Y-%m-%dT%H:%M:%SZ"), d, s, k, o, n, "ohlcv/daily/%s.csv" % d])
        # reload and re-check what is left
        hist = []
        for y, p in hist_files.items():
            hist.extend(read_csv(p, gz=True))
        hbyd = {}
        for r in hist:
            hbyd.setdefault(r[0], {})[r[7]] = r
        for dr in day_report:
            dr["history"] = len(hbyd.get(dr["date"], {}))
        fixed_dates = {d for d, _, _, _, _ in repairs}
        for dr in day_report:
            if dr["date"] in fixed_dates:
                dr["state"] = "repaired"
        issues = [i for i in issues if not i.get("fixable")]
        # a hole or day that is still open after the repair stays listed

    # ---- suspect bars (never edited) ----------------------------------------------------------
    sus = []
    last_close = {}
    for r in sorted(hist, key=lambda t: (t[7], t[0])):
        d, s = r[0], r[7]
        o, h, l, c, v = fnum(r[5]), fnum(r[3]), fnum(r[4]), fnum(r[2]), fnum(r[6])
        why = []
        if c is None or h is None or l is None or o is None or min(c, h, l, o) <= 0:
            why.append("zero/negative/empty price")
        else:
            if l > h * 1.0005:
                why.append("Low above High")
            if c > h * 1.0005 or c < l * 0.9995:
                why.append("Close outside High-Low")
            prev = last_close.get(s)
            if prev:
                ratio = c / prev
                if ratio < 0.6 or ratio > 1.6:
                    why.append("1-day move %+.0f%% (possible unadjusted split/bonus)" % ((ratio - 1) * 100))
            last_close[s] = c
        if why:
            sus.append((d, s, "; ".join(why), r[2]))
    qpath = os.path.join(oh, "quarantine.csv")
    sus.sort()
    qtext = io.StringIO()
    w = csv.writer(qtext, lineterminator="\n")
    w.writerow(["Date", "Symbol", "Reason", "Close"])
    w.writerows(sus)
    old = open(qpath).read() if os.path.exists(qpath) else ""
    if old != qtext.getvalue():
        open(qpath, "w", newline="").write(qtext.getvalue())

    recent_cut = cal[-10:][0] if len(cal) >= 10 else cal[0]
    q_recent = [x for x in sus if x[0] >= recent_cut]

    # ---- health.json ------------------------------------------------------------------------
    open_issues = [i for i in issues]
    status = "ok"
    if open_issues:
        status = "bad" if any(i["kind"] == "history_incomplete" and not i["fixable"] for i in open_issues) else "warn"
    health = {
        "status": status,
        "last_history_date": max(hbyd) if hbyd else None,
        "universe": U,
        "days": day_report,
        "issues": open_issues[:60],
        "unfixable_holes": len(unfixable_holes),
        "quarantine_total": len(sus),
        "quarantine_recent": [list(x) for x in q_recent[:30]],
        "repaired_rows": applied,
        "repair_mode": bool(a.repair),
    }
    hpath = os.path.join(oh, "health.json")
    prev = None
    try:
        prev = json.load(open(hpath))
        prev.pop("generated_utc", None)
    except Exception:
        pass
    if prev != health or applied:
        health["generated_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump(health, open(hpath, "w"), indent=1)

    # ---- calendar.json: stocks per day on GitHub, for the app's Vault calendar ---------------------
    cal_h, cal_d = {}, {}
    for p in sorted(glob.glob(os.path.join(oh, "history", "HIST_OHLCV_*.csv.gz"))):
        try:
            with gzip.open(p, "rt", newline="") as f:
                head = f.readline().strip().split(",")
                di = [h.strip().lower() for h in head].index("date")
                for ln in f:
                    d = ln.split(",", di + 1)[di] if di else ln[:10]
                    cal_h[d] = cal_h.get(d, 0) + 1
        except Exception as e:
            print("calendar: could not read", p, e)
    for p in sorted(glob.glob(os.path.join(oh, "daily", "*.csv"))):
        try:
            for r in read_csv(p):
                if dt.date.fromisoformat(r[0]).weekday() < 5:
                    cal_d[r[0]] = cal_d.get(r[0], 0) + 1
        except Exception:
            pass
    cal = {"history": dict(sorted(cal_h.items())), "daily": dict(sorted(cal_d.items()))}
    cpath = os.path.join(oh, "calendar.json")
    try:
        prevc = json.load(open(cpath))
        prevc.pop("generated_utc", None)
    except Exception:
        prevc = None
    if prevc != cal:
        cal["generated_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump(cal, open(cpath, "w"), separators=(",", ":"))

    # ---- console + issue text -----------------------------------------------------------------
    print("Data monitor %s IST · history %s · ~%d stocks · status %s" % (ist.strftime("%Y-%m-%d %H:%M"), health["last_history_date"], U, status.upper()))
    for dr in day_report[-8:]:
        print("  %s  history %5d  daily %5d  %s" % (dr["date"], dr["history"], dr["daily"], dr["state"]))
    print("  holes:", len(holes), "(unfixable %d)" % len(unfixable_holes), "· suspect bars:", len(sus), "(last 10 days: %d)" % len(q_recent), "· rows repaired:", applied)
    if a.issue_body:
        lines = ["Automatic report from `data_monitor.py` (%s IST).\n" % ist.strftime("%Y-%m-%d %H:%M")]
        if open_issues:
            lines.append("**Open problems**\n")
            for i in open_issues[:40]:
                lines.append("- `%s` %s%s" % (i["kind"], i["detail"], "" if i.get("fixable") else " — cannot be repaired automatically"))
            lines.append("\nRun the **Data monitor** workflow with *repair* switched on to fix the repairable ones from `ohlcv/daily`.")
        else:
            lines.append("No open problems.")
        if q_recent:
            lines.append("\n**Suspect bars in the last 10 trading days** (listed in `ohlcv/quarantine.csv`, never edited): %d" % len(q_recent))
        open(a.issue_body, "w").write("\n".join(lines) + "\n")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("status=%s\n" % status)


if __name__ == "__main__":
    main()
