import csv, gzip, io, json, os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
from fund.clean import clean_record
from fund.build import build_outputs, COLUMNS, slug
import run_shard
from fixtures import good_raw, META

NOW = datetime(2026, 8, 20)

def write(d, sym, meta=META, raw=None):
    r = clean_record(sym, raw or good_raw(), meta, NOW)
    json.dump(r, open(os.path.join(d, sym + ".json"), "w"))

def test_outputs_universe_filter_summary_and_manifest():
    with tempfile.TemporaryDirectory() as t:
        sd = os.path.join(t, "stock"); os.makedirs(sd)
        for s in ["AAA", "BBB", "CCC", "DDD"]: write(sd, s)
        write(sd, "OLD")                                   # delisted: not in universe -> must not appear
        write(sd, "EMPTYCO", raw={"info": {"financialCurrency": "INR"}})
        man = build_outputs(sd, os.path.join(t, "out"), ["AAA", "BBB", "CCC", "DDD", "EMPTYCO", "NEVER"], NOW)
        rows = list(csv.DictReader(io.StringIO(gzip.open(os.path.join(t, "out", "latest.csv.gz")).read().decode())))
        assert [r["SYMBOL"] for r in rows] == ["AAA", "BBB", "CCC", "DDD"]
        assert list(rows[0].keys()) == COLUMNS and rows[0]["SECTOR"] == "Capital Goods" and rows[0]["ROE"] == "4.44"
        assert man["universe"] == 6 and man["in_latest_csv"] == 4 and "NEVER" in man["missing"] and "OLD" not in man["missing"]
        sec = list(csv.DictReader(open(os.path.join(t, "out", "sector_summary.csv"))))
        assert sec[0]["SECTOR"] == "Capital Goods" and sec[0]["STOCKS"] == "4" and sec[0]["MEDIAN_ROE"] == "4.44"
        ind = list(csv.DictReader(open(os.path.join(t, "out", "industry_summary.csv"))))
        assert ind[0]["INDUSTRY"] == "Machinery"

def test_output_is_byte_stable_between_runs():
    with tempfile.TemporaryDirectory() as t:
        sd = os.path.join(t, "stock"); os.makedirs(sd); write(sd, "AAA")
        build_outputs(sd, os.path.join(t, "o1"), ["AAA"], NOW); build_outputs(sd, os.path.join(t, "o2"), ["AAA"], NOW)
        for f in ("latest.csv.gz", "sector_summary.csv", "manifest.json"):
            assert open(os.path.join(t, "o1", f), "rb").read() == open(os.path.join(t, "o2", f), "rb").read()

def test_shards_partition_the_universe_exactly_once():
    uni = [f"SYM{i}" for i in range(500)] + ["M&M", "BAJAJ-AUTO"]
    seen = [s for k in range(8) for s in uni if run_shard.shard_of(s, 8) == k]
    assert sorted(seen) == sorted(uni) and len(seen) == len(set(seen))
    sizes = [sum(1 for s in uni if run_shard.shard_of(s, 8) == k) for k in range(8)]
    assert min(sizes) > 30

class Fake:
    def __init__(self, fail=()): self.calls = []; self.fail = set(fail)
    def get(self, s):
        self.calls.append(s)
        if s in self.fail: raise RuntimeError("429")
        if s == "GONE": raise LookupError("no data")
        return good_raw()

def test_run_shard_resumes_skips_fresh_and_survives_failures():
    with tempfile.TemporaryDirectory() as t:
        data, out = os.path.join(t, "data"), os.path.join(t, "out"); os.makedirs(data)
        uni = ["AAA", "BBB", "CCC", "GONE"]; meta = {s: META for s in uni}
        write(data, "AAA")                                  # fresh -> skipped
        p = Fake(fail=["BBB"])
        res = run_shard.run(uni, meta, 0, 1, data, out, p, max_age_days=6, delay=0, now=NOW, sleep=lambda x: None, log=lambda *a: None)
        assert "AAA" not in p.calls and res["skipped"] == 1 and res["failed"] == 1 and res["fetched"] == 2
        assert not os.path.exists(os.path.join(out, "BBB.json"))        # failure never writes a bad record
        gone = json.load(open(os.path.join(out, "GONE.json")))
        assert gone["flags"] == ["NOT_ON_YAHOO"]

def test_slug():
    assert slug("Oil, Gas & Consumable Fuels") == "oil-gas-consumable-fuels"
