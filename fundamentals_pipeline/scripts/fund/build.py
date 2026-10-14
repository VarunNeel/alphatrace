"""Assemble per-stock JSON records into the files the app pulls."""
from __future__ import annotations
import csv, gzip, io, json, os, re, statistics
from datetime import datetime

COLUMNS = [
    "SYMBOL", "COMPANY", "MACRO", "SECTOR", "INDUSTRY", "BASIC_INDUSTRY", "QUALITY", "STATUS", "COMPLETENESS",
    "LQ_DATE", "LQ_SALES_CR", "LQ_NI_CR", "SALES_YOY", "NI_YOY", "SALES_QOQ", "NI_QOQ", "LQ_OPM", "LQ_NPM",
    "TTM_END", "TTM_BASIS", "SALES_TTM_CR", "NI_TTM_CR", "OPM", "NPM", "EPS_TTM",
    "FY_DATE", "FY_SALES_CR", "FY_NI_CR", "SALES_CAGR3", "NI_CAGR3",
    "BS_DATE", "EQUITY_CR", "BVPS", "SHARES", "ROE", "ROCE", "DE", "DEBT_CR", "CASH_CR", "NET_DEBT_CR", "INT_COVER",
    "CFO_CR", "FCF_CR", "CFO_TO_NI", "PROMOTER_PCT", "INSTITUTION_PCT", "DPS", "PAYOUT", "FLAGS", "FETCHED_AT",
]
SUMMARY_METRICS = ["SALES_YOY", "NI_YOY", "SALES_CAGR3", "NI_CAGR3", "OPM", "NPM", "ROE", "ROCE", "DE", "PAYOUT", "PROMOTER_PCT"]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "unknown"


def flat_row(rec: dict) -> dict:
    m = rec.get("metrics", {})
    r = {"SYMBOL": rec["symbol"], "COMPANY": rec.get("name", ""), "MACRO": rec.get("macro", ""),
         "SECTOR": rec.get("sector", ""), "INDUSTRY": rec.get("industry", ""), "BASIC_INDUSTRY": rec.get("basic_industry", ""),
         "QUALITY": rec.get("quality", "C"), "STATUS": rec.get("status", ""), "FLAGS": "|".join(rec.get("flags", [])),
         "FETCHED_AT": rec.get("fetched_at", "")}
    for c in COLUMNS:
        if c not in r:
            r[c] = m.get(c, "")
    return r


def _csv_bytes(rows, cols) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return buf.getvalue().encode("utf-8")


def _write_gz(path, data: bytes):
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as g:  # mtime=0 -> byte-stable, no noise commits
        g.write(data)


def summaries(rows, key):
    groups = {}
    for r in rows:
        groups.setdefault((r["SECTOR"], r["INDUSTRY"]) if key == "INDUSTRY" else (r["SECTOR"],), []).append(r)
    out = []
    for g, rs in sorted(groups.items()):
        good = [r for r in rs if r["QUALITY"] in ("A", "B")]
        o = {"SECTOR": g[0], "INDUSTRY": g[1] if key == "INDUSTRY" else "", "STOCKS": len(rs), "STOCKS_WITH_DATA": len(good)}
        for k in SUMMARY_METRICS:
            vals = [float(r[k]) for r in good if r.get(k) not in ("", None)]
            o["MEDIAN_" + k] = round(statistics.median(vals), 2) if len(vals) >= 3 else ""
        out.append(o)
    return out


def build_outputs(stock_dir: str, out_dir: str, universe: list[str], now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    recs = []
    for fn in sorted(os.listdir(stock_dir)):
        if fn.endswith(".json"):
            with open(os.path.join(stock_dir, fn), encoding="utf-8") as f:
                recs.append(json.load(f))
    uni = set(universe)
    recs = [r for r in recs if r["symbol"] in uni]          # the universe list is the filter
    rows = sorted((flat_row(r) for r in recs if r.get("status") in ("ok", "partial")), key=lambda r: r["SYMBOL"])
    os.makedirs(out_dir, exist_ok=True)
    _write_gz(os.path.join(out_dir, "latest.csv.gz"), _csv_bytes(rows, COLUMNS))
    sec = summaries(rows, "SECTOR")
    ind = summaries(rows, "INDUSTRY")
    scols = ["SECTOR", "INDUSTRY", "STOCKS", "STOCKS_WITH_DATA"] + ["MEDIAN_" + k for k in SUMMARY_METRICS]
    with open(os.path.join(out_dir, "sector_summary.csv"), "wb") as f:
        f.write(_csv_bytes(sec, scols))
    with open(os.path.join(out_dir, "industry_summary.csv"), "wb") as f:
        f.write(_csv_bytes(ind, scols))
    have = {r["symbol"] for r in recs}
    by_status, by_quality = {}, {}
    for r in recs:
        by_status[r.get("status")] = by_status.get(r.get("status"), 0) + 1
        by_quality[r.get("quality")] = by_quality.get(r.get("quality"), 0) + 1
    manifest = {
        "schema": 1, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "yahoo",
        "universe": len(uni), "with_record": len(have), "in_latest_csv": len(rows),
        "status": by_status, "quality": by_quality,
        "missing": sorted(uni - have)[:500],
        "columns": COLUMNS, "units": "INR crore unless the name says otherwise; percentages are plain percent",
        "note": "P/E, P/B and market cap are not stored; derive them from the live price using EPS_TTM, BVPS and SHARES.",
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    return manifest
