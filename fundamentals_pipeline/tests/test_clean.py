import copy, sys, os
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
from fund.clean import clean_record
from fixtures import good_raw, META, CR

NOW = datetime(2026, 8, 20)

def rec(raw=None, meta=META):
    return clean_record("TEST", raw or good_raw(), meta, NOW)

def test_good_stock_is_grade_A_and_numbers_trace_to_statements():
    r = rec(); m = r["metrics"]
    assert r["status"] == "ok" and r["quality"] == "A" and r["flags"] == []
    assert m["TTM_BASIS"] == "4Q" and m["TTM_END"] == "2026-06-30"
    assert m["SALES_TTM_CR"] == 1110.0            # 300+280+270+260
    assert m["NI_TTM_CR"] == 111.0                # 30+28+27+26
    assert m["NPM"] == round(111/1110*100, 2)
    assert m["OPM"] == round((60+56+54+52)/1110*100, 2)
    assert m["EPS_TTM"] == 11.1                   # 3.0+2.8+2.7+2.6
    assert m["SALES_YOY"] == 20.0                 # 300 vs 250
    assert m["NI_YOY"] == 20.0
    assert m["EQUITY_CR"] == 2500.0 and m["BVPS"] == 250.0
    assert m["ROE"] == round(111/2500*100, 2)
    assert m["DE"] == 0.2 and m["NET_DEBT_CR"] == 300.0
    assert m["ROCE"] == round((58+54+52+50)/3000*100, 2)
    assert m["INT_COVER"] == 13.38
    assert m["FCF_CR"] == 80.0 and m["CFO_TO_NI"] == round(120/105, 2)
    assert m["SALES_CAGR3"] == round(((1050/700)**(1/3)-1)*100, 2)
    assert m["PROMOTER_PCT"] == 55.0 and m["PAYOUT"] == 12.0

def test_pe_pb_marketcap_are_not_stored():
    m = rec()["metrics"]
    for k in ("PE", "PB", "MCAP_CR", "PE_FETCH"):
        assert k not in m

def test_missing_is_null_never_zero():
    raw = good_raw(); raw["a_cash"] = {}; raw["info"]["dividendRate"] = None
    m = rec(raw)["metrics"]
    assert "FCF_CR" not in m and "DPS" not in m
    assert all(v != 0 or k in ("DE",) for k, v in m.items() if isinstance(v, (int, float)) and k not in ("DE",)) 

def test_non_inr_is_rejected():
    raw = good_raw(); raw["info"]["financialCurrency"] = "USD"
    r = rec(raw)
    assert r["status"] == "rejected" and r["flags"] == ["NOT_INR"] and r["metrics"] == {}

def test_no_statements_is_empty():
    r = rec({"info": {"financialCurrency": "INR"}})
    assert r["status"] == "empty" and "NO_STATEMENTS" in r["flags"]

def test_growth_from_loss_base_is_null_not_infinite():
    raw = good_raw(); raw["q_income"]["Net Income"]["2025-06-30"] = -5*CR
    r = rec(raw)
    assert "NI_YOY" not in r["metrics"] and "NI_YOY_BASE_NONPOSITIVE" in r["flags"]

def test_gap_in_quarters_falls_back_to_annual_and_is_flagged():
    raw = good_raw()
    for k in raw["q_income"]: raw["q_income"][k].pop("2025-12-31")
    r = rec(raw)
    assert r["metrics"]["TTM_BASIS"] == "FY" and "TTM_FROM_ANNUAL" in r["flags"] and r["quality"] == "B"
    assert r["metrics"]["SALES_TTM_CR"] == 1050.0

def test_stale_data_flagged():
    r = clean_record("TEST", good_raw(), META, datetime(2027, 6, 1))
    assert "STALE" in r["flags"] and r["quality"] == "C"

def test_bank_has_no_operating_margin_roce_or_debt_equity():
    meta = dict(META, sector="Financial Services", industry="Banks")
    m = rec(meta=meta)["metrics"]
    for k in ("OPM", "LQ_OPM", "ROCE", "DE", "DEBT_CR", "INT_COVER"): assert k not in m
    assert "ROE" in m and "NPM" in m and rec(meta=meta)["is_financial"] is True

def test_negative_equity_gives_no_roe_and_is_flagged():
    raw = good_raw(); raw["a_balance"]["Stockholders Equity"]["2026-03-31"] = -100*CR
    r = rec(raw)
    assert "ROE" not in r["metrics"] and "NEG_EQUITY" in r["flags"] and "BVPS" not in r["metrics"] or r["metrics"].get("BVPS") == 250.0

def test_absurd_margin_removed():
    raw = good_raw(); raw["q_income"]["Net Income"] = {d: 900*CR for d in raw["q_income"]["Net Income"]}
    r = rec(raw)
    assert "NPM" not in r["metrics"] and "MARGIN_RANGE" in r["flags"]

def test_eps_cross_check_against_yahoo():
    raw = good_raw(); raw["info"]["trailingEps"] = 30.0     # ours 11.1 vs 30
    assert "EPS_MISMATCH" in rec(raw)["flags"]

def test_quarter_and_year_history_kept_newest_first():
    r = rec()
    assert [q["d"] for q in r["quarters"]][:2] == ["2026-06-30", "2026-03-31"] and len(r["quarters"]) == 5
    assert r["years"][0]["d"] == "2026-03-31" and len(r["years"]) == 4

def test_chart_series_growth_cost_and_cashflow_are_traceable():
    r = rec(); q0, y0, y1 = r["quarters"][0], r["years"][0], r["years"][1]
    assert q0["sales_g"] == 20.0 and q0["ni_g"] == 20.0 and q0["opcost"] == 240.0 and q0["opm"] == 20.0 and q0["npm"] == 10.0
    assert q0["sales_g"] is not None and r["quarters"][1]["sales_g"] is None      # no year-ago quarter in the data -> no growth, not a guess
    assert y0["sales_g"] == round((1050/900-1)*100, 2) and y0["ni_g"] == round((105/90-1)*100, 2)
    assert y0["opcost"] == 840.0 and y0["op"] == 210.0 and y0["opm"] == 20.0
    assert (y0["cfo"], y0["cfi"], y0["cff"]) == (120.0, -70.0, -30.0) and y0["net_cash"] == 20.0   # derived: 120-70-30
    assert y0["fcf"] == 80.0 and y1["fcf"] == 65.0 and y0["equity"] == 2500.0 and y0["debt"] == 500.0
    assert y1["cfo"] == 100.0 and r["years"][2]["cfo"] is None                       # earlier years with no cash-flow data stay empty

def test_bank_years_have_no_debt_or_opm():
    r = rec(meta=dict(META, sector="Financial Services", industry="Banks"))
    assert r["years"][0]["debt"] is None and r["years"][0]["opm"] is None and r["years"][0]["ni"] == 105.0
