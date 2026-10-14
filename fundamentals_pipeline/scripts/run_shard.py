#!/usr/bin/env python3
"""Fetch + clean one shard of the universe. Resumable: a stock whose existing record is younger than
--max-age-days is skipped, so re-running after a rate-limit stop only does the remaining work."""
import argparse, csv, hashlib, json, os, sys, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))
from fund.clean import clean_record


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_universe(uni_path, cls_path):
    uni = [r["SYMBOL"].strip() for r in read_csv(uni_path) if r.get("SYMBOL", "").strip()
           and (r.get("SERIES", "").strip() in ("", "EQ", "BE"))]
    cls = {r["SYMBOL"].strip(): r for r in read_csv(cls_path)} if cls_path and os.path.exists(cls_path) else {}
    meta = {s: {"name": cls.get(s, {}).get("COMPANY_NAME", ""), "isin": cls.get(s, {}).get("ISIN", ""),
                "macro": cls.get(s, {}).get("MACRO_ECONOMIC_SECTOR", ""), "sector": cls.get(s, {}).get("SECTOR", ""),
                "industry": cls.get(s, {}).get("INDUSTRY", ""), "basic_industry": cls.get(s, {}).get("BASIC_INDUSTRY", "")}
            for s in uni}
    return sorted(set(uni)), meta


def shard_of(symbol, n):  # stable across runs/machines, independent of list order
    return int(hashlib.sha1(symbol.encode()).hexdigest(), 16) % n


def is_fresh(path, max_age_days, now):
    try:
        with open(path, encoding="utf-8") as f:
            ts = json.load(f).get("fetched_at")
        return (now - datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")).total_seconds() < max_age_days * 86400
    except Exception:
        return False


def run(universe, meta, shard, of, data_dir, out_dir, provider, max_age_days=6, delay=0.6, limit=0, now=None, sleep=time.sleep, log=print):
    now = now or datetime.utcnow()
    mine = [s for s in universe if shard_of(s, of) == shard]
    os.makedirs(out_dir, exist_ok=True)
    done = skipped = failed = 0
    errors = []
    for s in mine:
        if limit and done >= limit:
            break
        if is_fresh(os.path.join(data_dir, s + ".json"), max_age_days, now):
            skipped += 1
            continue
        try:
            raw = provider.get(s)
            rec = clean_record(s, raw, meta.get(s, {}), now)
        except LookupError as e:          # Yahoo has no such ticker: record that, don't retry forever
            rec = clean_record(s, {}, meta.get(s, {}), now)
            rec["flags"] = ["NOT_ON_YAHOO"]
        except Exception as e:
            failed += 1
            errors.append({"symbol": s, "error": str(e)[:200]})
            sleep(delay)
            continue
        with open(os.path.join(out_dir, s + ".json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, separators=(",", ":"), sort_keys=True)
        done += 1
        if done % 25 == 0:
            log(f"shard {shard}/{of}: {done} fetched, {skipped} fresh, {failed} failed")
        sleep(delay)
    with open(os.path.join(out_dir, "_errors.json"), "w") as f:
        json.dump(errors, f)
    log(f"shard {shard}/{of} finished: {done} fetched, {skipped} fresh, {failed} failed, {len(mine)} in shard")
    return {"fetched": done, "skipped": skipped, "failed": failed, "total": len(mine)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="fundamentals/universe.csv")
    ap.add_argument("--classification", default="fundamentals/nse_classification.csv")
    ap.add_argument("--data-dir", default="fundamentals/data/stock")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--max-age-days", type=float, default=6)
    ap.add_argument("--delay", type=float, default=0.6)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    from fund.fetch import YahooProvider
    uni, meta = load_universe(a.universe, a.classification)
    run(uni, meta, a.shard, a.of, a.data_dir, a.out, YahooProvider(), a.max_age_days, a.delay, a.limit)
