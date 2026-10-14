"""Turn raw Yahoo statements into one clean, validated fundamentals record per stock.

Principles
  * Never invent a number. Anything missing or implausible becomes null (never 0) and a flag says why.
  * Everything in INR crore (1 Cr = 1e7), percentages as plain percent numbers (12.5 = 12.5 %).
  * We compute the ratios ourselves from the statements instead of trusting Yahoo's pre-computed
    ratios, so every number is traceable to a line item and a date.
  * Price-driven ratios (P/E, P/B, market cap, yield) are NOT stored as truth: the app derives
    them from the live Vault close. We only store the per-share inputs (EPS TTM, book value/share).
  * Banks / NBFCs / insurers: operating margin, ROCE, debt/equity and interest cover are
    meaningless for a lender, so they are left null for Financial Services.
"""
from __future__ import annotations
import math
from datetime import date, datetime

CR = 1e7
SCHEMA_VERSION = 1

ALIASES = {
    "sales": ["Total Revenue", "Operating Revenue", "Revenue"],
    "op_inc": ["Operating Income", "Total Operating Income As Reported"],
    "ebit": ["EBIT", "Operating Income"],
    "pretax": ["Pretax Income"],
    "ni": ["Net Income Common Stockholders", "Net Income", "Net Income From Continuing Operation Net Minority Interest"],
    "eps": ["Diluted EPS", "Basic EPS"],
    "interest": ["Interest Expense", "Interest Expense Non Operating"],
    "assets": ["Total Assets"],
    "cur_liab": ["Current Liabilities", "Total Current Liabilities"],
    "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
    "debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
    "cfo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure"],
    "fcf": ["Free Cash Flow"],
    "opcost": ["Total Expenses", "Operating Expense"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "pbt": ["Pretax Income"],
    "tax": ["Tax Provision"],
    "dep": ["Reconciled Depreciation"],
    "cfi": ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"],
    "cff": ["Financing Cash Flow", "Cash Flow From Continuing Financing Activities"],
    "netcash": ["Changes In Cash"],
}

CORE_FIELDS = ["SALES_TTM_CR", "NI_TTM_CR", "EPS_TTM", "BVPS", "ROE", "NPM", "SALES_YOY", "NI_YOY", "EQUITY_CR"]
SERIOUS_FLAGS = {"NOT_INR", "NO_STATEMENTS", "STALE", "EPS_MISMATCH", "BVPS_MISMATCH", "NEG_EQUITY", "TTM_FROM_ANNUAL"}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else v


def _d(s):
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def series(stmt: dict, key: str) -> dict:
    """stmt = {line item: {YYYY-MM-DD: value}} -> {date: float} using the first alias present."""
    for name in ALIASES[key]:
        row = (stmt or {}).get(name)
        if row:
            out = {_d(k): _num(v) for k, v in row.items()}
            out = {k: v for k, v in out.items() if v is not None}
            if out:
                return out
    return {}


def _cr(v):
    return None if v is None else round(v / CR, 2)


def _pct(a, b):
    return None if a is None or b in (None, 0) else round(a / b * 100, 2)


def _growth(cur, prev):
    # growth is only meaningful when the base is positive; otherwise null (never a fake huge %)
    if cur is None or prev is None or prev <= 0:
        return None
    return round((cur / prev - 1) * 100, 2)


def _cagr(cur, prev, years):
    if cur is None or prev is None or cur <= 0 or prev <= 0 or years <= 0:
        return None
    return round(((cur / prev) ** (1 / years) - 1) * 100, 2)


def _latest(s):
    if not s:
        return None, None
    d = max(s)
    return d, s[d]


def _near(s, target, tol=25):
    for d in s:
        if abs((d - target).days) <= tol:
            return d
    return None


def _four_consecutive(dates):
    """Newest first. True if the 4 newest quarter-ends are ~3 months apart (no gaps)."""
    if len(dates) < 4:
        return False
    ds = sorted(dates, reverse=True)[:4]
    return all(70 <= (ds[i] - ds[i + 1]).days <= 110 for i in range(3))


def clean_record(symbol: str, raw: dict, meta: dict, now: datetime | None = None) -> dict:
    """raw = {'info': {...}, 'q_income': stmt, 'a_income': stmt, 'q_balance': stmt, 'a_balance': stmt, 'a_cash': stmt}
    meta = {'name','isin','sector','industry','basic_industry','macro'} from nse_classification.csv"""
    now = now or datetime.utcnow()
    info = raw.get("info") or {}
    flags: list[str] = []
    is_fin = (meta.get("sector") or "").strip().lower() == "financial services"

    rec = {
        "schema": SCHEMA_VERSION, "symbol": symbol, "name": meta.get("name") or info.get("longName") or "",
        "isin": meta.get("isin", ""), "macro": meta.get("macro", ""), "sector": meta.get("sector", ""),
        "industry": meta.get("industry", ""), "basic_industry": meta.get("basic_industry", ""),
        "is_financial": is_fin, "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "yahoo",
    }

    cur = info.get("financialCurrency")
    if cur and str(cur).upper() != "INR":
        rec.update(status="rejected", quality="C", flags=["NOT_INR"], metrics={}, quarters=[], years=[])
        return rec

    qi, ai = raw.get("q_income") or {}, raw.get("a_income") or {}
    qb, ab = raw.get("q_balance") or {}, raw.get("a_balance") or {}
    ac = raw.get("a_cash") or {}

    q_sales, q_ni, q_op, q_eps = series(qi, "sales"), series(qi, "ni"), series(qi, "op_inc"), series(qi, "eps")
    q_ebit, q_int = series(qi, "ebit"), series(qi, "interest")
    a_sales, a_ni, a_eps = series(ai, "sales"), series(ai, "ni"), series(ai, "eps")
    a_ebit = series(ai, "ebit")

    if not q_sales and not a_sales:
        rec.update(status="empty", quality="C", flags=["NO_STATEMENTS"], metrics={}, quarters=[], years=[])
        return rec

    m: dict = {}

    # ---- latest quarter + YoY ----
    lq_d, lq_sales = _latest(q_sales)
    if lq_d:
        lq_ni = q_ni.get(lq_d)
        lq_op = q_op.get(lq_d)
        m["LQ_DATE"] = lq_d.isoformat()
        m["LQ_SALES_CR"] = _cr(lq_sales)
        m["LQ_NI_CR"] = _cr(lq_ni)
        py = _near(q_sales, date(lq_d.year - 1, lq_d.month, min(lq_d.day, 28)))
        m["SALES_YOY"] = _growth(lq_sales, q_sales.get(py)) if py else None
        pn = _near(q_ni, date(lq_d.year - 1, lq_d.month, min(lq_d.day, 28)))
        m["NI_YOY"] = _growth(lq_ni, q_ni.get(pn)) if pn else None
        if pn and q_ni.get(pn) is not None and q_ni[pn] <= 0:
            flags.append("NI_YOY_BASE_NONPOSITIVE")
        prev_q = sorted([d for d in q_sales if d < lq_d], reverse=True)
        if prev_q and 70 <= (lq_d - prev_q[0]).days <= 110:
            m["SALES_QOQ"] = _growth(lq_sales, q_sales[prev_q[0]])
            m["NI_QOQ"] = _growth(lq_ni, q_ni.get(prev_q[0]))
        m["LQ_NPM"] = _pct(lq_ni, lq_sales)
        m["LQ_OPM"] = None if is_fin else _pct(lq_op, lq_sales)

    # ---- TTM ----
    ttm_ok = _four_consecutive(list(q_sales))
    if ttm_ok:
        last4 = sorted(q_sales, reverse=True)[:4]
        ttm = lambda s: (sum(s[d] for d in last4) if all(d in s for d in last4) else None)
        t_sales, t_ni, t_op, t_ebit = ttm(q_sales), ttm(q_ni), ttm(q_op), ttm(q_ebit)
        t_eps = ttm(q_eps)
        m["TTM_BASIS"] = "4Q"
        m["TTM_END"] = last4[0].isoformat()
    else:
        d, t_sales = _latest(a_sales)
        t_ni, t_ebit, t_eps = a_ni.get(d), a_ebit.get(d), a_eps.get(d)
        t_op = series(ai, "op_inc").get(d)
        m["TTM_BASIS"] = "FY"
        m["TTM_END"] = d.isoformat() if d else None
        flags.append("TTM_FROM_ANNUAL")
    m["SALES_TTM_CR"], m["NI_TTM_CR"] = _cr(t_sales), _cr(t_ni)
    m["NPM"] = _pct(t_ni, t_sales)
    m["OPM"] = None if is_fin else _pct(t_op if t_op is not None else t_ebit, t_sales)
    for k in ("NPM", "OPM"):
        if m.get(k) is not None and abs(m[k]) > 100:
            flags.append("MARGIN_RANGE")
            m[k] = None

    # ---- annual: 3-year CAGR ----
    ly_d, ly_sales = _latest(a_sales)
    if ly_d:
        m["FY_DATE"] = ly_d.isoformat()
        m["FY_SALES_CR"] = _cr(ly_sales)
        m["FY_NI_CR"] = _cr(a_ni.get(ly_d))
        base = _near(a_sales, date(ly_d.year - 3, ly_d.month, min(ly_d.day, 28)), 40)
        if base:
            m["SALES_CAGR3"] = _cagr(ly_sales, a_sales[base], 3)
            nb = _near(a_ni, base, 5)
            m["NI_CAGR3"] = _cagr(a_ni.get(ly_d), a_ni.get(nb), 3) if nb else None

    # ---- balance sheet (newest of annual/quarterly) ----
    bs = {k: {} for k in ("equity", "assets", "cur_liab", "debt", "cash", "shares")}
    for src in (ab, qb):
        for k in bs:
            for d, v in series(src, k).items():
                bs[k][d] = v
    # use ONE consistent date for the whole balance sheet: the newest date that has equity
    bd = max(bs["equity"]) if bs["equity"] else None
    eq = bs["equity"].get(bd) if bd else None
    if bd:
        m["BS_DATE"] = bd.isoformat()
        m["EQUITY_CR"] = _cr(eq)
        if eq is not None and eq <= 0:
            flags.append("NEG_EQUITY")
        m["ROE"] = _pct(t_ni, eq) if eq and eq > 0 else None
        debt, cash = bs["debt"].get(bd), bs["cash"].get(bd)
        if not is_fin:
            m["DEBT_CR"], m["CASH_CR"] = _cr(debt), _cr(cash)
            m["DE"] = round(debt / eq, 2) if debt is not None and eq and eq > 0 else None
            m["NET_DEBT_CR"] = _cr(debt - cash) if debt is not None and cash is not None else None
            ta, cl = bs["assets"].get(bd), bs["cur_liab"].get(bd)
            cap = (ta - cl) if ta is not None and cl is not None else None
            ebit_ttm = t_ebit if t_ebit is not None else t_op
            m["ROCE"] = _pct(ebit_ttm, cap) if cap and cap > 0 else None
            ints = sorted(q_int, reverse=True)[:4]
            i_ttm = sum(abs(q_int[d]) for d in ints) if ttm_ok and len(ints) == 4 else None
            m["INT_COVER"] = round(ebit_ttm / i_ttm, 2) if ebit_ttm is not None and i_ttm and i_ttm > 0 else None
    if m.get("ROE") is not None and abs(m["ROE"]) > 200:
        flags.append("ROE_RANGE")
        m["ROE"] = None

    # ---- per share (inputs the app needs for live P/E, P/B) ----
    shares = bs["shares"].get(bd) if bd else None
    shares = shares or _num(info.get("sharesOutstanding"))
    m["SHARES"] = int(shares) if shares else None
    bvps = round(eq / shares, 2) if eq and shares and eq > 0 else None
    y_bv = _num(info.get("bookValue"))
    if bvps and y_bv and abs(bvps / y_bv - 1) > 0.25:
        flags.append("BVPS_MISMATCH")
    m["BVPS"] = bvps if bvps is not None else (y_bv if y_bv and y_bv > 0 else None)
    y_eps = _num(info.get("trailingEps"))
    eps = round(t_eps, 2) if t_eps is not None else y_eps
    if t_eps is not None and y_eps not in (None, 0) and abs(t_eps / y_eps - 1) > 0.30 and abs(t_eps - y_eps) > 1:
        flags.append("EPS_MISMATCH")
    m["EPS_TTM"] = eps
    price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    mcap = _num(info.get("marketCap"))
    if price and shares and mcap and abs(price * shares / mcap - 1) > 0.25:
        flags.append("PRICE_MCAP_MISMATCH")

    # ---- cash flow (annual) ----
    cfo, capex, fcf = series(ac, "cfo"), series(ac, "capex"), series(ac, "fcf")
    cd, cv = _latest(cfo)
    if cd:
        m["CFO_CR"] = _cr(cv)
        f = fcf.get(cd)
        if f is None and capex.get(cd) is not None:
            f = cv + capex[cd]  # Yahoo reports capex as a negative number
        m["FCF_CR"] = _cr(f)
        m["CFO_TO_NI"] = round(cv / a_ni[cd], 2) if a_ni.get(cd) and a_ni[cd] > 0 else None

    # ---- ownership / dividend (Yahoo: insiders ~ promoters) ----
    hi, hn = _num(info.get("heldPercentInsiders")), _num(info.get("heldPercentInstitutions"))
    m["PROMOTER_PCT"] = round(hi * 100, 2) if hi is not None and 0 <= hi <= 1 else None
    m["INSTITUTION_PCT"] = round(hn * 100, 2) if hn is not None and 0 <= hn <= 1 else None
    dr, pr = _num(info.get("dividendRate")), _num(info.get("payoutRatio"))
    m["DPS"] = round(dr, 2) if dr is not None and dr >= 0 else None
    m["PAYOUT"] = round(pr * 100, 1) if pr is not None and 0 <= pr <= 3 else None

    # ---- freshness ----
    newest = lq_d or ly_d
    if newest and (now.date() - newest).days > 200:
        flags.append("STALE")

    # ---- final status / quality ----
    present = sum(1 for k in CORE_FIELDS if m.get(k) is not None)
    frac = present / len(CORE_FIELDS)
    status = "ok" if frac >= 0.7 else "partial"
    serious = [f for f in flags if f in SERIOUS_FLAGS]
    if status == "ok" and not serious and frac >= 0.9:
        quality = "A"
    elif status == "ok" and len([f for f in serious if f != "TTM_FROM_ANNUAL"]) == 0:
        quality = "B"
    else:
        quality = "C"
    m["COMPLETENESS"] = round(frac * 100)

    rec["metrics"] = {k: v for k, v in m.items() if v is not None}
    def _g(s_, d, step_days, tol):
        """Growth of period d versus the period ~step_days earlier (None unless that base exists and is positive)."""
        cur_ = s_.get(d)
        b = _near(s_, date(d.year - 1, d.month, min(d.day, 28)), tol) if step_days == 365 else None
        return _growth(cur_, s_.get(b)) if b else None

    def _ccr(x):  # crore, but keep None
        return _cr(x)

    q_pbt, q_tax, q_ebitda, q_opc, q_dep = (series(qi, k) for k in ("pbt", "tax", "ebitda", "opcost", "dep"))
    rec["quarters"] = []
    for d in sorted(q_sales, reverse=True)[:8]:
        opc = q_opc.get(d)
        if opc is None and q_op.get(d) is not None:
            opc = q_sales[d] - q_op[d]
        rec["quarters"].append({
            "d": d.isoformat(), "sales": _cr(q_sales[d]), "opcost": _cr(opc), "op": _cr(q_op.get(d)), "ebitda": _cr(q_ebitda.get(d)),
            "int": _cr(abs(q_int[d])) if q_int.get(d) is not None else None, "pbt": _cr(q_pbt.get(d)), "ni": _cr(q_ni.get(d)), "eps": q_eps.get(d),
            "opm": None if is_fin else _pct(q_op.get(d), q_sales[d]), "npm": _pct(q_ni.get(d), q_sales[d]),
            "sales_g": _g(q_sales, d, 365, 25), "ni_g": _g(q_ni, d, 365, 25), "op_g": _g(q_op, d, 365, 25),
        })
    a_pbt, a_opc, a_op, a_ebitda, a_int = (series(ai, k) for k in ("pbt", "opcost", "op_inc", "ebitda", "interest"))
    a_cfo, a_cfi, a_cff, a_net, a_capex, a_fcf = (series(ac, k) for k in ("cfo", "cfi", "cff", "netcash", "capex", "fcf"))
    rec["years"] = []
    for d in sorted(a_sales, reverse=True)[:5]:
        opc = a_opc.get(d)
        if opc is None and a_op.get(d) is not None:
            opc = a_sales[d] - a_op[d]
        f_ = a_fcf.get(d)
        if f_ is None and a_cfo.get(d) is not None and a_capex.get(d) is not None:
            f_ = a_cfo[d] + a_capex[d]
        net_ = a_net.get(d)
        if net_ is None and None not in (a_cfo.get(d), a_cfi.get(d), a_cff.get(d)):
            net_ = a_cfo[d] + a_cfi[d] + a_cff[d]
        prev_ = _near(a_sales, date(d.year - 1, d.month, min(d.day, 28)), 40)
        rec["years"].append({
            "d": d.isoformat(), "sales": _cr(a_sales[d]), "opcost": _cr(opc), "op": _cr(a_op.get(d)), "ebitda": _cr(a_ebitda.get(d)),
            "int": _cr(abs(a_int[d])) if a_int.get(d) is not None else None, "pbt": _cr(a_pbt.get(d)), "ni": _cr(a_ni.get(d)), "eps": a_eps.get(d),
            "opm": None if is_fin else _pct(a_op.get(d), a_sales[d]), "npm": _pct(a_ni.get(d), a_sales[d]),
            "sales_g": _growth(a_sales[d], a_sales.get(prev_)) if prev_ else None,
            "ni_g": _growth(a_ni.get(d), a_ni.get(_near(a_ni, prev_, 5))) if prev_ and _near(a_ni, prev_, 5) else None,
            "op_g": _growth(a_op.get(d), a_op.get(_near(a_op, prev_, 5))) if prev_ and _near(a_op, prev_, 5) else None,
            "cfo": _cr(a_cfo.get(d)), "cfi": _cr(a_cfi.get(d)), "cff": _cr(a_cff.get(d)), "net_cash": _cr(net_), "capex": _cr(a_capex.get(d)), "fcf": _cr(f_),
            "equity": _cr(bs["equity"].get(d)), "debt": None if is_fin else _cr(bs["debt"].get(d)),
        })
    rec["status"], rec["quality"], rec["flags"] = status, quality, sorted(set(flags))
    return rec
