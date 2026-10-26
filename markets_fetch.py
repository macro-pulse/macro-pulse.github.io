"""Bonds + Volatility data. Real free sources only; anything that fails is omitted (or kept stale from last run)."""
import csv, io, json, re, sys
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor

KEEP = 400  # days of history kept per series

def _cut(h, days=KEEP):
    if not h: return h
    c = (date.fromisoformat(h[-1][0]) - timedelta(days=days)).isoformat()
    return [p for p in h if p[0] >= c]

def fred(http, sid, days=KEEP):
    txt = http(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", "text/csv", 3, 25)
    h = []
    for r in list(csv.reader(io.StringIO(txt)))[1:]:
        try: h.append([r[0], float(r[1])])
        except (ValueError, IndexError): pass
    return _cut(h, days)

def cboe(http, sym):
    txt = http(f"https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv", "text/csv", 2, 25)
    h = []
    for r in list(csv.reader(io.StringIO(txt)))[1:]:
        try:
            m, d, y = r[0].split("/"); h.append([f"{y}-{m}-{d}", float(r[-1])])
        except (ValueError, IndexError): pass
    return _cut(h)

def cnbc_hist(http, sym):
    d = json.loads(http(f"https://ts-api.cnbc.com/harmony/app/charts/1Y.json?symbol={sym}", "application/json", 2, 20))
    h = []
    for b in d["barData"]["priceBars"]:
        t = b["tradeTime"]; h.append([f"{t[:4]}-{t[4:6]}-{t[6:8]}", float(b["close"])])
    return _cut(h)

def cnbc_quotes(http, syms):
    d = json.loads(http("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols="
                        + "|".join(syms) + "&requestMethod=itv&noform=1&output=json", "application/json", 2, 20))
    out = {}
    for q in d["FormattedQuoteResult"]["FormattedQuote"]:
        try: out[q["symbol"]] = (q["last_time"][:10], float(q["last"].rstrip("%").replace(",", "")))
        except Exception: pass
    return out

def nasdaq_etf(http, sym, today):
    fr = (today - timedelta(days=KEEP)).isoformat()
    d = json.loads(http(f"https://api.nasdaq.com/api/quote/{sym}/historical?assetclass=etf&fromdate={fr}&limit=400",
                        "application/json", 2, 20))
    h = []
    for r in d["data"]["tradesTable"]["rows"]:
        m, dd, y = r["date"].split("/"); h.append([f"{y}-{m}-{dd}", float(r["close"].replace("$", "").replace(",", ""))])
    return sorted(h)

def _et(ms):
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ms / 1000, ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")

def deribit(http, cur):
    import time
    e = int(time.time() * 1000); s0 = e - KEEP * 86400000
    u = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={}&resolution={}&start_timestamp={}&end_timestamp={}"
    d = json.loads(http(u.format(cur, "1D", s0, e), "application/json", 2, 20))["result"]["data"]
    h = [[datetime.utcfromtimestamp(r[0] / 1000).date().isoformat(), r[4]] for r in d]
    if not h: raise ValueError("no data")
    ts = d[-1][0]
    try:  # latest hourly close = current reading
        x = json.loads(http(u.format(cur, "3600", e - 3 * 3600000, e), "application/json", 1, 15))["result"]["data"]
        if x:
            ts = x[-1][0]; dd = datetime.utcfromtimestamp(ts / 1000).date().isoformat()
            if dd == h[-1][0]: h[-1][1] = x[-1][4]
            elif dd > h[-1][0]: h.append([dd, x[-1][4]])
    except Exception: pass
    return h, _et(ts)

def coinbase(http, prod):
    d = json.loads(http(f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity=86400", "application/json", 2, 20))
    import time
    d = [r for r in d if r[0] + 86400 <= time.time()]  # completed UTC days only
    return sorted([datetime.utcfromtimestamp(r[0]).date().isoformat(), float(r[4])] for r in d), _et(max(r[0] for r in d) * 1000 + 86400000)

def rvol(h, n=30):
    import math
    r = [(h[i][0], math.log(h[i][1] / h[i - 1][1])) for i in range(1, len(h))]
    out = []
    for i in range(n - 1, len(r)):
        w = [x for _, x in r[i - n + 1:i + 1]]; m = sum(w) / n
        out.append([r[i][0], round(math.sqrt(sum((x - m) ** 2 for x in w) / (n - 1)) * math.sqrt(365) * 100, 2)])
    return out

def boe(http):
    fr = (date.today() - timedelta(days=KEEP)).strftime("%d/%b/%Y")
    txt = http("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom="
               f"{fr}&Dateto=now&SeriesCodes=IUDMNPY&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N", "text/csv", 2, 25)
    h = []
    for r in list(csv.reader(io.StringIO(txt)))[1:]:
        try: h.append([datetime.strptime(r[0], "%d %b %Y").date().isoformat(), float(r[1])])
        except (ValueError, IndexError): pass
    return h

def mof(http):
    h = {}
    for u in ["https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv",
              "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"]:
        try: txt = http(u, "text/csv", 2, 30)
        except Exception as e: print("WARN mof", u, e, file=sys.stderr); continue
        for r in csv.reader(io.StringIO(txt)):
            try:
                y, m, d = r[0].split("/"); h[date(int(y), int(m), int(d)).isoformat()] = float(r[10])
            except (ValueError, IndexError): pass
    if not h: raise ValueError("no MOF data")
    return _cut(sorted([k, v] for k, v in h.items()))

def boc(http):
    fr = (date.today() - timedelta(days=KEEP)).isoformat()
    d = json.loads(http(f"https://www.bankofcanada.ca/valet/observations/BD.CDN.10YR.DQ.YLD/json?start_date={fr}", "application/json", 2, 20))
    return [[o["d"], float(o["BD.CDN.10YR.DQ.YLD"]["v"])] for o in d["observations"] if o.get("BD.CDN.10YR.DQ.YLD", {}).get("v")]

def rba(http):
    txt = http("https://www.rba.gov.au/statistics/tables/csv/f2-data.csv", "text/csv", 2, 25)
    h = []
    for r in csv.reader(io.StringIO(txt)):
        try: h.append([datetime.strptime(r[0], "%d-%b-%Y").date().isoformat(), float(r[4])])
        except (ValueError, IndexError): pass
    return _cut(h)

def stoxx(http):
    txt = http("https://www.stoxx.com/document/Indices/Current/HistoricalData/h_v2tx.txt", "text/plain", 2, 25)
    h = []
    for ln in txt.splitlines():
        p = ln.split(";")
        try: d, m, y = p[0].split("."); h.append([f"{y}-{m}-{d}", float(p[2])])
        except (ValueError, IndexError): pass
    return _cut(h)

def chinabond(http):
    """MoF China / ChinaBond treasury curve table: yield, daily, monthly, annual change (bp)."""
    s = http("https://yield.chinabond.com.cn/cbweb-czb-web/czb/moreInfo?locale=en_US&nameType=1", "text/html", 2, 25)
    dt = re.search(r'rowspan="\d+">(\d{4}-\d\d-\d\d)<', s)[1]
    t = re.findall(r"<td>([^<]*)</td>", s); i = t.index("3Mo"); out = {}
    while i + 4 < len(t) and re.match(r"\d+(Mo|Yr)$", t[i]):
        out[t[i]] = dict(v=float(t[i+1]), d=float(t[i+2]), m=float(t[i+3]), y=float(t[i+4])); i += 5
    return dt, out

def _accum(prev_h, d, v):
    h = [p for p in (prev_h or []) if p[0] != d] + [[d, v]]
    return _cut(sorted(h))

def build(http, today, prev=None):
    prev = prev or {}
    pv = {it["key"]: it for sec in ("bonds", "vol") for it in (prev.get(sec) or {}).get("items", [])}
    F = "FRED (St. Louis Fed)"
    jobs = {}  # key -> (callable, meta)
    def J(key, fn, label, sec, unit, src, url, freq="daily", **kw):
        jobs[key] = (fn, dict(key=key, label=label, sec=sec, unit=unit, src=src, url=url, freq=freq, **kw))
    for t, s in [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1Y", "DGS1"), ("2Y", "DGS2"), ("5Y", "DGS5"),
                 ("7Y", "DGS7"), ("10Y", "DGS10"), ("20Y", "DGS20"), ("30Y", "DGS30")]:
        J("us"+t, lambda s=s: fred(http, s), f"US {t} Treasury", "ust", "%", F, f"https://fred.stlouisfed.org/series/{s}", tenor=t)
    J("s2s10", lambda: fred(http, "T10Y2Y"), "2s10s spread (10Y − 2Y)", "ust", "pp", F, "https://fred.stlouisfed.org/series/T10Y2Y")
    J("s3m10", lambda: fred(http, "T10Y3M"), "3m10y spread (10Y − 3M)", "ust", "pp", F, "https://fred.stlouisfed.org/series/T10Y3M")
    for k, s, l in [("tips5", "DFII5", "5Y TIPS real yield"), ("tips10", "DFII10", "10Y TIPS real yield"), ("tips30", "DFII30", "30Y TIPS real yield"),
                    ("be5", "T5YIE", "5Y breakeven inflation"), ("be10", "T10YIE", "10Y breakeven inflation")]:
        J(k, lambda s=s: fred(http, s), l, "tips", "%", F, f"https://fred.stlouisfed.org/series/{s}")
    CN = "CNBC quote data"
    for k, s, l in [("de10", "DE10Y-DE", "Germany 10Y Bund"), ("fr10", "FR10Y-FR", "France 10Y OAT"), ("it10", "IT10Y-IT", "Italy 10Y BTP"),
                    ("cn10", "CN10Y-CN", "China 10Y")]:
        J(k, lambda s=s: cnbc_hist(http, s), l, "glob", "%", CN, f"https://www.cnbc.com/quotes/{s}")
    J("gb10", lambda: boe(http), "UK 10Y gilt (par yield)", "glob", "%", "Bank of England", "https://www.bankofengland.co.uk/boeapps/database/")
    J("jp10", lambda: mof(http), "Japan 10Y JGB", "glob", "%", "Japan Ministry of Finance", "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/")
    J("ca10", lambda: boc(http), "Canada 10Y benchmark", "glob", "%", "Bank of Canada Valet", "https://www.bankofcanada.ca/rates/interest-rates/")
    J("au10", lambda: rba(http), "Australia 10Y", "glob", "%", "Reserve Bank of Australia (F2)", "https://www.rba.gov.au/statistics/tables/")
    J("in10m", lambda: fred(http, "INDIRLTLT01STM", 3*365), "India 10Y G-Sec (monthly, OECD)", "glob", "%", F,
      "https://fred.stlouisfed.org/series/INDIRLTLT01STM", freq="monthly")
    ETF = [("SHY", "1–3Y Treasury"), ("IEI", "3–7Y Treasury"), ("IEF", "7–10Y Treasury"), ("TLT", "20+Y Treasury"), ("VGLT", "Long-term Treasury"),
           ("ZROZ", "25+Y zero coupon Treasury"), ("GOVT", "US Treasury bond"), ("BIL", "1–3 month T-bill"), ("SGOV", "0–3 month T-bill"),
           ("AGG", "US aggregate bond"), ("BND", "Total bond market"), ("LQD", "Inv. grade corporate"), ("HYG", "High yield corporate"),
           ("JNK", "High yield bond"), ("TIP", "TIPS"), ("SCHP", "US TIPS"), ("MUB", "National muni"), ("EMB", "EM USD sovereign")]
    for s, l in ETF:
        J("etf"+s, lambda s=s: nasdaq_etf(http, s, today), f"{s} · {l}", "etf", "$", "Nasdaq (closing prices)",
          f"https://www.nasdaq.com/market-activity/etf/{s.lower()}/historical", ticker=s)
    # India / China extras
    J("in3m", lambda: fred(http, "INDIR3TIB01STM", 3*365), "India 3-month interbank rate (monthly, OECD)", "india", "%", F,
      "https://fred.stlouisfed.org/series/INDIR3TIB01STM", freq="monthly")
    J("inr", lambda: fred(http, "DEXINUS"), "USD/INR", "india", "₹", F, "https://fred.stlouisfed.org/series/DEXINUS")
    J("cn2", lambda: cnbc_hist(http, "CN2Y-CN"), "China 2Y CGB", "china", "%", CN, "https://www.cnbc.com/quotes/CN2Y-CN", tenor="2Y")
    J("cny", lambda: fred(http, "DEXCHUS"), "USD/CNY", "china", "¥", F, "https://fred.stlouisfed.org/series/DEXCHUS")
    J("usm10", lambda: fred(http, "IRLTLT01USM156N", 3*365), "", "_", "%", F, "")
    # Volatility
    CB = "Cboe (daily close)"
    for s, l, g in [("VIX9D", "VIX9D · 9-day S&P 500 vol", "eq"), ("VIX", "VIX · 30-day S&P 500 vol", "eq"), ("VIX3M", "VIX3M · 3-month", "eq"),
                    ("VIX6M", "VIX6M · 6-month", "eq"), ("VIX1Y", "VIX1Y · 1-year", "eq"), ("VVIX", "VVIX · vol of VIX", "eq"),
                    ("VXN", "VXN · Nasdaq-100 vol", "eq"), ("RVX", "RVX · Russell 2000 vol", "eq"), ("VXD", "VXD · Dow vol", "eq"),
                    ("SKEW", "SKEW · S&P 500 tail risk", "eq"), ("OVX", "OVX · crude oil vol", "x"), ("GVZ", "GVZ · gold vol", "x"),
("VXEEM", "VXEEM · emerging markets vol", "x")]:
        J("v"+s, lambda s=s: cboe(http, s), l, "vol_"+g, "", CB,
          f"https://www.cboe.com/us/indices/dashboard/{s}/", ticker=s)
    J("vMOVE", lambda: cnbc_hist(http, ".MOVE"), "MOVE · ICE BofA Treasury vol", "vol_x", "", CN, "https://www.cnbc.com/quotes/.MOVE", ticker="MOVE")
    J("vV2TX", lambda: stoxx(http), "VSTOXX · Euro Stoxx 50 vol", "vol_g", "", "STOXX", "https://www.stoxx.com/index/v2tx/", ticker="V2TX")
    J("vINVIX", lambda: cnbc_hist(http, "INDIAVIX"), "India VIX (NSE)", "vol_g", "", CN, "https://www.cnbc.com/quotes/INDIAVIX", ticker="INDIAVIX")

    items, failed = {}, []
    def run(k):
        try: return k, jobs[k][0](), None
        except Exception as e: return k, None, str(e)[:120]
    with ThreadPoolExecutor(16) as ex:
        res = list(ex.map(run, jobs))
    def snap():
        try: return cnbc_quotes(http, ["IN10Y-IN", "IN30Y-IN"]), None
        except Exception as e: return {}, str(e)
    def cbt():
        try: return chinabond(http), None
        except Exception as e: return None, str(e)
    with ThreadPoolExecutor(2) as ex:
        fq, fc = ex.submit(snap), ex.submit(cbt)
        (q, qe), (cb, cbe) = fq.result(), fc.result()
    for k, h, err in res:
        meta = jobs[k][1]
        if h:
            items[k] = dict(meta, h=[[d, round(v, 4)] for d, v in h])
        elif k in pv and pv[k].get("h"):
            items[k] = dict(pv[k], stale=True); failed.append(f"{meta['label'] or k}: {err} (showing last saved data)")
        else:
            failed.append(f"{meta['label'] or k}: {err}")
    # India daily snapshots (CNBC publishes no free history for India tenors -> history accumulates run by run)
    for k, s, l, t in [("in10", "IN10Y-IN", "India 10Y G-Sec", "10Y"), ("in30", "IN30Y-IN", "India 30Y G-Sec", "30Y")]:
        if s in q:
            items[k] = dict(key=k, label=l, sec="india", unit="%", src=CN, url=f"https://www.cnbc.com/quotes/{s}", tenor=t,
                            freq="daily snapshot (chart history builds up from Oct 26, 2026)", h=_accum(pv.get(k, {}).get("h"), *q[s]))
        elif k in pv: items[k] = dict(pv[k], stale=True); failed.append(f"{l}: quote failed ({qe})")
    if cb:
        dt, tab = cb
        for t in ["1Yr", "2Yr", "5Yr", "10Yr", "30Yr"]:
            if t in tab:
                k = "cb" + t[:-2]; r = tab[t]
                items[k] = dict(key=k, label=f"China {t[:-2]}Y CGB (ChinaBond curve)", sec="china", unit="%", src="ChinaBond / MoF China",
                                url="https://yield.chinabond.com.cn/cbweb-czb-web/czb/moreInfo?locale=en_US&nameType=1", tenor=t[:-2] + "Y",
                                freq="daily snapshot (chart history builds up from Oct 26, 2026)", src_chg=dict(d=r["d"], m=r["m"], y=r["y"]),
                                h=_accum(pv.get(k, {}).get("h"), dt, r["v"]))
        curve = dict(date=dt, tenors=list(tab), now=[r["v"] for r in tab.values()],
                     m1=[round(r["v"] - r["m"] / 100, 4) for r in tab.values()], y1=[round(r["v"] - r["y"] / 100, 4) for r in tab.values()])
    else:
        curve = (prev.get("bonds") or {}).get("cn_curve"); failed.append(f"ChinaBond curve: {cbe}")
    # spreads computed from matching dates
    def spread(a, b, key, label, sec, freq="daily", asof=False):
        if a in items and b in items:
            hb = items[b]["h"]; mb = dict(hb)
            def bv(d):
                if d in mb or not asof: return mb.get(d)
                c = [v for x, v in hb if x <= d and (date.fromisoformat(d) - date.fromisoformat(x)).days <= 5]
                return c[-1] if c else None
            h = [[d, round(v - bv(d), 4)] for d, v in items[a]["h"] if bv(d) is not None]
            if h: items[key] = dict(key=key, label=label, sec=sec, unit="pp", src="computed from the series above", url=items[a]["url"], freq=freq, h=h)
    spread("in10", "us10Y", "inus", "India − US 10Y spread", "india", "daily (builds up; US = latest FRED close)", asof=True)
    spread("in10m", "usm10", "inusm", "India − US 10Y spread (monthly, OECD)", "india", "monthly")
    spread("cn10", "us10Y", "cnus", "China − US 10Y spread", "china")
    items.pop("usm10", None)
    # Crypto volatility (24/7)
    for c in ("BTC", "ETH"):
        try:
            h, ts = deribit(http, c)
            items["dv" + c] = dict(key="dv" + c, ticker=c + " DVOL", label=f"Deribit {c} DVOL · 30-day implied vol", sec="vol_c", unit="",
                                  src="Deribit", url=f"https://www.deribit.com/statistics/{c}/volatility-index", freq="24/7 · latest " + ts, h=h)
        except Exception as e: failed.append(f"Deribit {c} DVOL: {str(e)[:80]}")
        try:
            px, ts2 = coinbase(http, c + "-USD"); rv = rvol(px)
            items["rv" + c] = dict(key="rv" + c, ticker=c + " RV30", label=f"{c} realized vol 30D (realized, computed)", sec="vol_c", unit="",
                                  src="computed from Coinbase daily closes (UTC)", url=f"https://exchange.coinbase.com/trade/{c}-USD",
                                  freq="daily UTC closes · latest close " + ts2, h=rv)
        except Exception as e: failed.append(f"{c} realized vol (Coinbase): {str(e)[:80]}")
        if "dv" + c in items and "rv" + c in items:
            m = dict(items["rv" + c]["h"])
            hs = [[d, round(v - m[d], 2)] for d, v in items["dv" + c]["h"] if d in m]
            if hs: items["ir" + c] = dict(key="ir" + c, ticker=c + " IV−RV", label=f"{c} implied − realized (DVOL − RV30)", sec="vol_c", unit="vp",
                                        src="computed (Deribit DVOL − Coinbase RV30)", url=items["dv" + c]["url"], freq="daily, computed", h=hs)
    if "dvBTC" in items and "vVIX" in items:
        m = dict(items["vVIX"]["h"])
        hs = [[d, round(v / m[d], 3)] for d, v in items["dvBTC"]["h"] if d in m]
        if hs: items["btcvix"] = dict(key="btcvix", ticker="DVOL/VIX", label="BTC DVOL / VIX ratio", sec="vol_c", unit="×",
                                     src="computed (Deribit DVOL ÷ Cboe VIX, same-date closes)", url="https://www.cboe.com/us/indices/dashboard/VIX/", freq="US trading days, computed", h=hs)
    bonds = dict(items=[v for v in items.values() if not v["sec"].startswith("vol")], cn_curve=curve,
                 failed=[f for f in failed if not f.startswith(("VIX", "VVIX", "VX", "RVX", "SKEW", "OVX", "GVZ", "EVZ", "MOVE", "VSTOXX", "India VIX", "Deribit", "BTC", "ETH"))])
    vol = dict(items=[v for v in items.values() if v["sec"].startswith("vol")], failed=[f for f in failed if f not in bonds["failed"]])
    return bonds, vol

if __name__ == "__main__":
    import time, calendar_fetch
    t = time.time(); b, v = build(calendar_fetch.http, date.today())
    print(f"{time.time()-t:.1f}s", len(b["items"]), len(v["items"]), b["failed"], v["failed"])
    for it in b["items"] + v["items"]: print(it["key"], it["h"][-1], len(it["h"]))
    json.dump(dict(bonds=b, vol=v), open("/tmp/mk.json", "w"))
