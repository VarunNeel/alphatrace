#!/usr/bin/env python3
"""AlphaTrace - US stocks for the Global tab (S&P 500 + Nasdaq 100).   Python 3.9+,  pip install yfinance pandas lxml requests

US stocks are NOT mixed with your Indian stocks: they go into the same global/ folder as the other world markets and are read by the Global tab.
Every symbol is written as <TICKER>.US  (BRK.B -> BRK-B.US).

COMMANDS
  universe  python us_feed.py universe              global/us_stocks_list.csv           (the S&P 500 + Nasdaq 100 list, refreshed daily)
  daily     python us_feed.py daily [--days 7]      global/US_STOCKS_LATEST.csv         (the last few trading days)
  history   python us_feed.py history [--years 3]   global/US_STOCKS_DAILY.csv.gz       (full history, run once)

FORMAT  Date,Adj Close,Close,High,Low,Open,Volume,SYMBOL,NAME,GROUP,COUNTRY   (same as global_markets.py; Close is split-adjusted)
"""
import sys, os, io, csv, gzip, argparse, datetime as dt

OUT = 'global'
LATEST = OUT + '/US_STOCKS_LATEST.csv'
FULL = OUT + '/US_STOCKS_DAILY.csv.gz'
GROUP, COUNTRY = 'US Stocks', 'United States'
HEAD = ['Date', 'Adj Close', 'Close', 'High', 'Low', 'Open', 'Volume', 'SYMBOL', 'NAME', 'GROUP', 'COUNTRY']
WIKI_SP = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
WIKI_NQ = 'https://en.wikipedia.org/wiki/Nasdaq-100'
UA = {'User-Agent': 'Mozilla/5.0 (AlphaTrace feed job)'}
# GICS (S&P / MSCI, 2023 structure): sub-industry -> (sector, industry).  Older names are kept so a stale Wikipedia row still lands right.
GICS = {
    'oil & gas drilling': ('Energy', 'Energy Equipment & Services'),
    'oil & gas equipment & services': ('Energy', 'Energy Equipment & Services'),
    'integrated oil & gas': ('Energy', 'Oil, Gas & Consumable Fuels'),
    'oil & gas exploration & production': ('Energy', 'Oil, Gas & Consumable Fuels'),
    'oil & gas refining & marketing': ('Energy', 'Oil, Gas & Consumable Fuels'),
    'oil & gas storage & transportation': ('Energy', 'Oil, Gas & Consumable Fuels'),
    'coal & consumable fuels': ('Energy', 'Oil, Gas & Consumable Fuels'),
    'commodity chemicals': ('Materials', 'Chemicals'),
    'diversified chemicals': ('Materials', 'Chemicals'),
    'fertilizers & agricultural chemicals': ('Materials', 'Chemicals'),
    'industrial gases': ('Materials', 'Chemicals'),
    'specialty chemicals': ('Materials', 'Chemicals'),
    'construction materials': ('Materials', 'Construction Materials'),
    'metal, glass & plastic containers': ('Materials', 'Containers & Packaging'),
    'paper & plastic packaging products & materials': ('Materials', 'Containers & Packaging'),
    'metal & glass containers': ('Materials', 'Containers & Packaging'),
    'paper packaging': ('Materials', 'Containers & Packaging'),
    'aluminum': ('Materials', 'Metals & Mining'),
    'diversified metals & mining': ('Materials', 'Metals & Mining'),
    'copper': ('Materials', 'Metals & Mining'),
    'gold': ('Materials', 'Metals & Mining'),
    'precious metals & minerals': ('Materials', 'Metals & Mining'),
    'silver': ('Materials', 'Metals & Mining'),
    'steel': ('Materials', 'Metals & Mining'),
    'forest products': ('Materials', 'Paper & Forest Products'),
    'paper products': ('Materials', 'Paper & Forest Products'),
    'aerospace & defense': ('Industrials', 'Aerospace & Defense'),
    'building products': ('Industrials', 'Building Products'),
    'construction & engineering': ('Industrials', 'Construction & Engineering'),
    'electrical components & equipment': ('Industrials', 'Electrical Equipment'),
    'heavy electrical equipment': ('Industrials', 'Electrical Equipment'),
    'industrial conglomerates': ('Industrials', 'Industrial Conglomerates'),
    'construction machinery & heavy transportation equipment': ('Industrials', 'Machinery'),
    'construction machinery & heavy trucks': ('Industrials', 'Machinery'),
    'agricultural & farm machinery': ('Industrials', 'Machinery'),
    'industrial machinery & supplies & components': ('Industrials', 'Machinery'),
    'industrial machinery': ('Industrials', 'Machinery'),
    'trading companies & distributors': ('Industrials', 'Trading Companies & Distributors'),
    'commercial printing': ('Industrials', 'Commercial Services & Supplies'),
    'environmental & facilities services': ('Industrials', 'Commercial Services & Supplies'),
    'office services & supplies': ('Industrials', 'Commercial Services & Supplies'),
    'diversified support services': ('Industrials', 'Commercial Services & Supplies'),
    'security & alarm services': ('Industrials', 'Commercial Services & Supplies'),
    'human resource & employment services': ('Industrials', 'Professional Services'),
    'research & consulting services': ('Industrials', 'Professional Services'),
    'data processing & outsourced services': ('Industrials', 'Professional Services'),
    'air freight & logistics': ('Industrials', 'Air Freight & Logistics'),
    'passenger airlines': ('Industrials', 'Passenger Airlines'),
    'airlines': ('Industrials', 'Passenger Airlines'),
    'marine transportation': ('Industrials', 'Marine Transportation'),
    'marine': ('Industrials', 'Marine Transportation'),
    'rail transportation': ('Industrials', 'Ground Transportation'),
    'railroads': ('Industrials', 'Ground Transportation'),
    'cargo ground transportation': ('Industrials', 'Ground Transportation'),
    'passenger ground transportation': ('Industrials', 'Ground Transportation'),
    'trucking': ('Industrials', 'Ground Transportation'),
    'airport services': ('Industrials', 'Transportation Infrastructure'),
    'highways & railtracks': ('Industrials', 'Transportation Infrastructure'),
    'marine ports & services': ('Industrials', 'Transportation Infrastructure'),
    'automotive parts & equipment': ('Consumer Discretionary', 'Automobile Components'),
    'tires & rubber': ('Consumer Discretionary', 'Automobile Components'),
    'automobile manufacturers': ('Consumer Discretionary', 'Automobiles'),
    'motorcycle manufacturers': ('Consumer Discretionary', 'Automobiles'),
    'consumer electronics': ('Consumer Discretionary', 'Household Durables'),
    'home furnishings': ('Consumer Discretionary', 'Household Durables'),
    'homebuilding': ('Consumer Discretionary', 'Household Durables'),
    'household appliances': ('Consumer Discretionary', 'Household Durables'),
    'housewares & specialties': ('Consumer Discretionary', 'Household Durables'),
    'leisure products': ('Consumer Discretionary', 'Leisure Products'),
    'apparel, accessories & luxury goods': ('Consumer Discretionary', 'Textiles, Apparel & Luxury Goods'),
    'footwear': ('Consumer Discretionary', 'Textiles, Apparel & Luxury Goods'),
    'textiles': ('Consumer Discretionary', 'Textiles, Apparel & Luxury Goods'),
    'casinos & gaming': ('Consumer Discretionary', 'Hotels, Restaurants & Leisure'),
    'hotels, resorts & cruise lines': ('Consumer Discretionary', 'Hotels, Restaurants & Leisure'),
    'leisure facilities': ('Consumer Discretionary', 'Hotels, Restaurants & Leisure'),
    'restaurants': ('Consumer Discretionary', 'Hotels, Restaurants & Leisure'),
    'education services': ('Consumer Discretionary', 'Diversified Consumer Services'),
    'specialized consumer services': ('Consumer Discretionary', 'Diversified Consumer Services'),
    'distributors': ('Consumer Discretionary', 'Distributors'),
    'broadline retail': ('Consumer Discretionary', 'Broadline Retail'),
    'internet & direct marketing retail': ('Consumer Discretionary', 'Broadline Retail'),
    'general merchandise stores': ('Consumer Discretionary', 'Broadline Retail'),
    'apparel retail': ('Consumer Discretionary', 'Specialty Retail'),
    'computer & electronics retail': ('Consumer Discretionary', 'Specialty Retail'),
    'home improvement retail': ('Consumer Discretionary', 'Specialty Retail'),
    'other specialty retail': ('Consumer Discretionary', 'Specialty Retail'),
    'automotive retail': ('Consumer Discretionary', 'Specialty Retail'),
    'homefurnishing retail': ('Consumer Discretionary', 'Specialty Retail'),
    'drug retail': ('Consumer Staples', 'Consumer Staples Distribution & Retail'),
    'food distributors': ('Consumer Staples', 'Consumer Staples Distribution & Retail'),
    'food retail': ('Consumer Staples', 'Consumer Staples Distribution & Retail'),
    'consumer staples merchandise retail': ('Consumer Staples', 'Consumer Staples Distribution & Retail'),
    'hypermarkets & super centers': ('Consumer Staples', 'Consumer Staples Distribution & Retail'),
    'brewers': ('Consumer Staples', 'Beverages'),
    'distillers & vintners': ('Consumer Staples', 'Beverages'),
    'soft drinks & non-alcoholic beverages': ('Consumer Staples', 'Beverages'),
    'soft drinks': ('Consumer Staples', 'Beverages'),
    'agricultural products & services': ('Consumer Staples', 'Food Products'),
    'agricultural products': ('Consumer Staples', 'Food Products'),
    'packaged foods & meats': ('Consumer Staples', 'Food Products'),
    'tobacco': ('Consumer Staples', 'Tobacco'),
    'household products': ('Consumer Staples', 'Household Products'),
    'personal care products': ('Consumer Staples', 'Personal Care Products'),
    'personal products': ('Consumer Staples', 'Personal Care Products'),
    'health care equipment': ('Health Care', 'Health Care Equipment & Supplies'),
    'health care supplies': ('Health Care', 'Health Care Equipment & Supplies'),
    'health care distributors': ('Health Care', 'Health Care Providers & Services'),
    'health care services': ('Health Care', 'Health Care Providers & Services'),
    'health care facilities': ('Health Care', 'Health Care Providers & Services'),
    'managed health care': ('Health Care', 'Health Care Providers & Services'),
    'health care technology': ('Health Care', 'Health Care Technology'),
    'biotechnology': ('Health Care', 'Biotechnology'),
    'pharmaceuticals': ('Health Care', 'Pharmaceuticals'),
    'life sciences tools & services': ('Health Care', 'Life Sciences Tools & Services'),
    'diversified banks': ('Financials', 'Banks'),
    'regional banks': ('Financials', 'Banks'),
    'diversified financial services': ('Financials', 'Financial Services'),
    'other diversified financial services': ('Financials', 'Financial Services'),
    'multi-sector holdings': ('Financials', 'Financial Services'),
    'specialized finance': ('Financials', 'Financial Services'),
    'commercial & residential mortgage finance': ('Financials', 'Financial Services'),
    'transaction & payment processing services': ('Financials', 'Financial Services'),
    'consumer finance': ('Financials', 'Consumer Finance'),
    'asset management & custody banks': ('Financials', 'Capital Markets'),
    'investment banking & brokerage': ('Financials', 'Capital Markets'),
    'diversified capital markets': ('Financials', 'Capital Markets'),
    'financial exchanges & data': ('Financials', 'Capital Markets'),
    'mortgage reits': ('Financials', 'Mortgage Real Estate Investment Trusts (REITs)'),
    'insurance brokers': ('Financials', 'Insurance'),
    'life & health insurance': ('Financials', 'Insurance'),
    'multi-line insurance': ('Financials', 'Insurance'),
    'property & casualty insurance': ('Financials', 'Insurance'),
    'reinsurance': ('Financials', 'Insurance'),
    'it consulting & other services': ('Information Technology', 'IT Services'),
    'internet services & infrastructure': ('Information Technology', 'IT Services'),
    'application software': ('Information Technology', 'Software'),
    'systems software': ('Information Technology', 'Software'),
    'communications equipment': ('Information Technology', 'Communications Equipment'),
    'technology hardware, storage & peripherals': ('Information Technology', 'Technology Hardware, Storage & Peripherals'),
    'electronic equipment & instruments': ('Information Technology', 'Electronic Equipment, Instruments & Components'),
    'electronic components': ('Information Technology', 'Electronic Equipment, Instruments & Components'),
    'electronic manufacturing services': ('Information Technology', 'Electronic Equipment, Instruments & Components'),
    'technology distributors': ('Information Technology', 'Electronic Equipment, Instruments & Components'),
    'semiconductor materials & equipment': ('Information Technology', 'Semiconductors & Semiconductor Equipment'),
    'semiconductor equipment': ('Information Technology', 'Semiconductors & Semiconductor Equipment'),
    'semiconductors': ('Information Technology', 'Semiconductors & Semiconductor Equipment'),
    'alternative carriers': ('Communication Services', 'Diversified Telecommunication Services'),
    'integrated telecommunication services': ('Communication Services', 'Diversified Telecommunication Services'),
    'wireless telecommunication services': ('Communication Services', 'Wireless Telecommunication Services'),
    'advertising': ('Communication Services', 'Media'),
    'broadcasting': ('Communication Services', 'Media'),
    'cable & satellite': ('Communication Services', 'Media'),
    'publishing': ('Communication Services', 'Media'),
    'movies & entertainment': ('Communication Services', 'Entertainment'),
    'interactive home entertainment': ('Communication Services', 'Entertainment'),
    'interactive media & services': ('Communication Services', 'Interactive Media & Services'),
    'electric utilities': ('Utilities', 'Electric Utilities'),
    'gas utilities': ('Utilities', 'Gas Utilities'),
    'multi-utilities': ('Utilities', 'Multi-Utilities'),
    'water utilities': ('Utilities', 'Water Utilities'),
    'independent power producers & energy traders': ('Utilities', 'Independent Power and Renewable Electricity Producers'),
    'renewable electricity': ('Utilities', 'Independent Power and Renewable Electricity Producers'),
    'diversified reits': ('Real Estate', 'Diversified REITs'),
    'industrial reits': ('Real Estate', 'Industrial REITs'),
    'hotel & resort reits': ('Real Estate', 'Hotel & Resort REITs'),
    'office reits': ('Real Estate', 'Office REITs'),
    'health care reits': ('Real Estate', 'Health Care REITs'),
    'multi-family residential reits': ('Real Estate', 'Residential REITs'),
    'single-family residential reits': ('Real Estate', 'Residential REITs'),
    'residential reits': ('Real Estate', 'Residential REITs'),
    'retail reits': ('Real Estate', 'Retail REITs'),
    'other specialized reits': ('Real Estate', 'Specialized REITs'),
    'self-storage reits': ('Real Estate', 'Specialized REITs'),
    'telecom tower reits': ('Real Estate', 'Specialized REITs'),
    'timber reits': ('Real Estate', 'Specialized REITs'),
    'data center reits': ('Real Estate', 'Specialized REITs'),
    'specialized reits': ('Real Estate', 'Specialized REITs'),
    'diversified real estate activities': ('Real Estate', 'Real Estate Management & Development'),
    'real estate operating companies': ('Real Estate', 'Real Estate Management & Development'),
    'real estate development': ('Real Estate', 'Real Estate Management & Development'),
    'real estate services': ('Real Estate', 'Real Estate Management & Development'),
}


def clean(x):
    """the app reads CSV without quote handling, so no commas or quotes inside names"""
    return str(x).replace(',', ' ').replace('"', '').strip()


def app_sym(t):
    return t.strip().upper().replace('.', '-') + '.US'


def yahoo_sym(t):
    return t.strip().upper().replace('.', '-')


def read_tables(url):
    import requests, pandas as pd
    r = requests.get(url, headers=UA, timeout=60); r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def pick(tables, need):
    for t in tables:
        cols = [str(c).strip().lower() for c in t.columns]
        if all(any(n in c for c in cols) for n in need):
            t = t.copy(); t.columns = cols; return t
    raise RuntimeError('table not found: ' + ','.join(need))


def find(cols, *names):
    for n in names:
        for c in cols:
            if n in c: return c
    return None


def build_universe():
    rows = {}
    t = pick(read_tables(WIKI_SP), ['symbol', 'gics sector'])
    sy, nm, se, su = find(t.columns, 'symbol'), find(t.columns, 'security'), find(t.columns, 'gics sector'), find(t.columns, 'sub-industry')
    for _, r in t.iterrows():
        rows[yahoo_sym(str(r[sy]))] = (clean(r[nm]), clean(r[se]), clean(r[su]))
    try:
        t = pick(read_tables(WIKI_NQ), ['ticker', 'gics sector'])
        sy, nm, se, su = find(t.columns, 'ticker'), find(t.columns, 'company'), find(t.columns, 'gics sector'), find(t.columns, 'sub-industry')
        for _, r in t.iterrows():
            y = yahoo_sym(str(r[sy]))
            if y not in rows: rows[y] = (clean(r[nm]), clean(r[se]), clean(r[su]))
    except Exception as e:
        print('Nasdaq-100 table skipped:', e)
    if len(rows) < 400: raise RuntimeError('universe too small (%d) — keeping the old files' % len(rows))
    return rows


def gics(sector, sub):
    """-> (sector, industry, sub-industry); a sub-industry GICS does not list keeps itself as its industry (never invented)"""
    k = sub.strip().lower().replace(' and ', ' & ')
    if k in GICS: return GICS[k][0], GICS[k][1], sub
    return sector or 'Unclassified', sub or 'Unclassified', sub or 'Unclassified'


def write_universe(rows):
    """us_stocks_list.csv (job's own list) + us_classification.csv (read by the app: same columns as NSE's classification file,
    SECTOR > INDUSTRY > BASIC_INDUSTRY = GICS Sector > Industry > Sub-industry)"""
    os.makedirs(OUT, exist_ok=True)
    with open(OUT + '/us_stocks_list.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['YAHOO', 'NAME', 'SECTOR', 'INDUSTRY', 'SUB_INDUSTRY'])
        for y, (n, s, u) in sorted(rows.items()): w.writerow([y, n] + [x.replace(',', '') for x in gics(s, u)])
    miss = sorted({u for (n, s, u) in rows.values() if u.strip().lower().replace(' and ', ' & ') not in GICS})
    if miss: print('sub-industries not in the GICS table (kept as their own industry):', '; '.join(miss))
    with open(OUT + '/us_classification.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['SYMBOL', 'COMPANY_NAME', 'MACRO_ECONOMIC_SECTOR', 'SECTOR', 'INDUSTRY', 'BASIC_INDUSTRY'])
        for y, (n, s, u) in sorted(rows.items()):
            se, ind, sub = (x.replace(',', '').replace('  ', ' ') for x in gics(s, u)); w.writerow([app_sym(y), clean(n), se, se, ind, sub])  # no commas: the app reads CSV without quotes (NSE writes 'Oil Gas & Consumable Fuels' the same way)


def read_universe():
    with open(OUT + '/us_stocks_list.csv', newline='') as f:
        rd = list(csv.DictReader(f))
    return [r['YAHOO'] for r in rd], {r['YAHOO']: r['NAME'] for r in rd}


def download(tickers, names, start=None, end=None, period=None):
    """-> rows in the Global-tab format; Yahoo is asked in batches of 80."""
    import yfinance as yf
    out = []
    for i in range(0, len(tickers), 80):
        batch = tickers[i:i + 80]
        kw = dict(tickers=' '.join(batch), group_by='ticker', auto_adjust=False, actions=False, progress=False, threads=True)
        if period: kw['period'] = period
        else: kw['start'] = start; kw['end'] = end
        df = yf.download(**kw)
        for t in batch:
            try: d = df[t] if len(batch) > 1 else df
            except Exception: continue
            d = d.dropna(subset=['Close'])
            for idx, r in d.iterrows():
                out.append([str(idx)[:10], round(float(r['Adj Close']), 4), round(float(r['Close']), 4), round(float(r['High']), 4), round(float(r['Low']), 4), round(float(r['Open']), 4), int(r['Volume'] or 0), t + '.US', clean(names.get(t, t)), GROUP, COUNTRY])
    return out


def write_rows(path, rows, gz=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO(); w = csv.writer(buf, lineterminator='\n'); w.writerow(HEAD); w.writerows(sorted(rows, key=lambda r: (r[0], r[7])))
    if gz:
        with gzip.open(path, 'wt', newline='') as f: f.write(buf.getvalue())
    else:
        with open(path, 'w', newline='') as f: f.write(buf.getvalue())


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['universe', 'daily', 'history'])
    ap.add_argument('--days', type=int, default=7); ap.add_argument('--years', type=int, default=3)
    a = ap.parse_args()
    if a.cmd == 'universe':
        try: write_universe(build_universe()); print('universe written')
        except Exception as e: print('universe NOT updated:', e); sys.exit(0 if os.path.exists(OUT + '/us_stocks_list.csv') else 1)
        return
    if not os.path.exists(OUT + '/us_stocks_list.csv'): write_universe(build_universe())
    tk, nm = read_universe()
    if a.cmd == 'daily':
        rows = download(tk, nm, period='%dd' % max(3, a.days))
        if len(rows) < 1000: print('too few rows (%d) - not overwriting' % len(rows)); sys.exit(1)
        keep = sorted({r[0] for r in rows})[-5:]
        write_rows(LATEST, [r for r in rows if r[0] in keep]); print('latest rows', len(rows), 'days', keep)
    else:
        y = dt.date.today(); rows = download(tk, nm, start='%d-01-01' % (y.year - max(1, a.years) + 1), end=(y + dt.timedelta(days=1)).isoformat())
        if len(rows) < 10000: print('too few rows (%d) - not overwriting' % len(rows)); sys.exit(1)
        write_rows(FULL, rows, gz=True); print('history rows', len(rows))


if __name__ == '__main__':
    main()
