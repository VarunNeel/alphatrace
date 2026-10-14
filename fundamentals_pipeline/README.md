# AlphaTrace fundamentals pipeline

Daily-price repo pattern, applied to fundamentals: a scheduled GitHub Action fetches statements from Yahoo Finance,
cleans and validates them, and commits static files that Helios pulls from `raw.githubusercontent.com`.

## Install (3 steps)
1. Copy `fundamentals_pipeline/` and `.github/workflows/fundamentals.yml` into the root of your repo (`VarunNeel/alphatrace`).
2. Repo → Settings → Actions → General → Workflow permissions → **Read and write**.
3. Actions → *fundamentals* → Run workflow. First run: set `limit` = 20 to check it, then run again with `limit` = 0.

Needs `fundamentals/universe.csv` and `fundamentals/nse_classification.csv` (you already have both). The universe list is the filter; nothing is hard-coded.

## What it writes (`fundamentals/data/`)
| file | what |
|---|---|
| `latest.csv.gz` | one row per stock, ~50 clean columns, with SECTOR / INDUSTRY / BASIC_INDUSTRY |
| `sector_summary.csv`, `industry_summary.csv` | medians per group (growth, margins, ROE, ROCE, D/E, payout, promoter %) |
| `stock/<SYMBOL>.json` | full record: metrics, up to 8 quarters and 5 years (sales, operating cost, operating profit, PAT, EPS, growth, margins; years add cash flows, debt, equity), quality grade, flags. Feeds Helios's charts |
| `manifest.json` | counts, quality split, symbols with no record, units |

## Clean-data rules
- INR crore; percentages as plain percent. Missing = empty, never 0.
- Ratios are computed from statement line items (TTM = last 4 consecutive quarters; otherwise last FY, flagged `TTM_FROM_ANNUAL`).
- Growth from a zero/negative base is empty, not a huge number. Margins over 100 % and ROE over 200 % are dropped and flagged.
- Banks/NBFCs/insurers: no operating margin, ROCE, debt/equity or interest cover (meaningless for lenders).
- Cross-checks against Yahoo's own EPS, book value and market cap; mismatches are flagged.
- **Quality**: A = complete and no serious flag, B = usable (e.g. TTM from the last FY), C = don't screen on it. `STATUS` ok/partial.
- P/E, P/B, market cap and yield are **not stored**: Helios derives them from the live Vault close using `EPS_TTM`, `BVPS`, `SHARES`.

## Honest limits
Yahoo is an unofficial source: small caps can have gaps, and its coverage changes without notice. Records that fail checks are graded C rather than silently "fixed".
Fetch is resumable and rate-limit aware (8 shards, retries with back-off, records younger than `max_age_days` are skipped), but Yahoo may still throttle a run; re-running only does the remaining stocks.

## Tests
`pip install -r fundamentals_pipeline/requirements.txt && pytest fundamentals_pipeline/tests` (20 tests, no network).
