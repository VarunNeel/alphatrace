#!/usr/bin/env python3
import argparse, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from fund.build import build_outputs
from run_shard import load_universe
ap = argparse.ArgumentParser()
ap.add_argument("--universe", default="fundamentals/universe.csv")
ap.add_argument("--classification", default="fundamentals/nse_classification.csv")
ap.add_argument("--stock-dir", default="fundamentals/data/stock")
ap.add_argument("--out", default="fundamentals/data")
a = ap.parse_args()
uni, _ = load_universe(a.universe, a.classification)
m = build_outputs(a.stock_dir, a.out, uni)
print({k: m[k] for k in ("universe", "with_record", "in_latest_csv", "status", "quality")})
