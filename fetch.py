#!/usr/bin/env python3
"""Fetch US macro series from FRED public CSV endpoints into data.json."""
import csv, io, json, sys, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
# key, label, series, transform, unit, group, decimals
IND = [
 ("cpi","CPI YoY","CPIAUCNS","yoy12","%","Inflation",1),
 ("core_cpi","Core CPI YoY","CPILFENS","yoy12","%","Inflation",1),
 ("core_pce","Core PCE YoY","PCEPILFE","yoy12","%","Inflation",1),
 ("unrate","Unemployment rate","UNRATE","level","%","Labor",1),
 ("nfp","Nonfarm payrolls change","PAYEMS","diff","K","Labor",0),
 ("claims","Initial jobless claims","ICSA","thousands","K","Labor",0),
 ("gdp","Real GDP growth (QoQ SAAR)","A191RL1Q225SBEA","level","%","Growth",1),
 ("retail","Retail sales MoM","RSAFS","mom","%","Growth",1),
 ("sentiment","UMich consumer sentiment","UMCSENT","level","","Growth",1),
 ("effr","Fed funds effective","DFF","level","%","Rates",2),
 ("ust2","2-year Treasury","DGS2","level","%","Rates",2),
 ("ust10","10-year Treasury","DGS10","level","%","Rates",2),
 ("ust30","30-year Treasury","DGS30","level","%","Rates",2),
 ("s2s10","2s10s spread","T10Y2Y","bp","bp","Rates",0),
 ("usd","Trade-weighted dollar (broad)","DTWEXBGS","level","","Markets",2),
 ("wti","WTI crude oil","DCOILWTICO","level","$","Markets",2),
 ("mortgage30","30-year fixed mortgage (Freddie Mac PMMS)","MORTGAGE30US","level","%","Mortgage rates",2),
 ("mortgage15","15-year fixed mortgage (Freddie Mac PMMS)","MORTGAGE15US","level","%","Mortgage rates",2),
]

def get(sid):
    req = urllib.request.Request(URL.format(sid), headers={"User-Agent": "curl/8.5.0"})
    for attempt in range(3):
        try:
            txt = urllib.request.urlopen(req, timeout=20).read().decode(); break
        except Exception:
            if attempt == 2: raise
    out = []
    for r in list(csv.reader(io.StringIO(txt)))[1:]:
        try: out.append((r[0], float(r[1])))
        except (ValueError, IndexError): pass  # '.' = missing
    if not out: raise ValueError("no data")
    return out

def shift(d, months):
    y, m = int(d[:4]), int(d[5:7]) - months
    while m < 1: y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}{d[7:]}"

def transform(pts, t):
    # Date-based lookups so gaps in FRED data (e.g. missing months) never misalign comparisons
    v = dict(pts)
    def rel(fn, back):
        return [(d, fn(x, v[shift(d, back)])) for d, x in pts if shift(d, back) in v]
    if t == "yoy12": return rel(lambda a, b: (a / b - 1) * 100, 12)
    if t == "mom": return rel(lambda a, b: (a / b - 1) * 100, 1)
    if t == "diff": return rel(lambda a, b: a - b, 1)
    if t == "thousands": return [(d, x / 1000) for d, x in pts]
    if t == "bp": return [(d, x * 100) for d, x in pts]
    return list(pts)

CLE = "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_year.json"
CLE_PAGE = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
CLE_MAP = {"cpi": "CPI Inflation", "core_cpi": "Core CPI Inflation", "core_pce": "Core PCE Inflation"}
MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

def add_expectations(res):
    by = {r["key"]: r for r in res}
    # Cleveland Fed inflation nowcast (YoY) for the month after the latest FRED print
    try:
        req = urllib.request.Request(CLE, headers={"User-Agent": "Mozilla/5.0"})
        blocks = json.loads(urllib.request.urlopen(req, timeout=60).read())
        months = {}
        for b in blocks:
            y, m = b["chart"]["subcaption"].split("-")
            vals = {}
            for ds in b["dataset"]:
                v = [x.get("value") for x in ds["data"] if x.get("value") not in (None, "")]
                if v: vals[ds["seriesname"]] = float(v[-1])
            months[(int(y), int(m))] = vals
        for key, name in CLE_MAP.items():
            r = by.get(key)
            if not r: continue
            y, m = int(r["date"][:4]), int(r["date"][5:7])
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            v = months.get((y, m), {})
            if name in v and "Actual " + name not in v:
                r["expected"] = dict(value=round(v[name], 2), period=f"{MON[m-1]} {y}",
                    source="Cleveland Fed Inflation Nowcasting", url=CLE_PAGE)
    except Exception as e:
        print("WARN cleveland", e, file=sys.stderr)
    # Atlanta Fed GDPNow via FRED (GDPNOW) for the quarter after the latest GDP print
    try:
        r = by.get("gdp")
        g = get("GDPNOW")[-1]
        if r and g[0] > r["date"]:
            q = (int(g[0][5:7]) - 1) // 3 + 1
            r["expected"] = dict(value=round(g[1], 2), period=f"Q{q} {g[0][:4]}",
                source="Atlanta Fed GDPNow (via FRED)", url="https://fred.stlouisfed.org/series/GDPNOW")
    except Exception as e:
        print("WARN gdpnow", e, file=sys.stderr)

def main():
    et = ZoneInfo("America/New_York")
    res, failed = [], []
    for key, label, sid, t, unit, group, dec in IND:
        try:
            s = transform(get(sid), t)
            cutoff = (datetime.fromisoformat(s[-1][0]) - timedelta(days=3*365)).date().isoformat()
            hist = [p for p in s if p[0] >= cutoff][-60:]
            res.append(dict(key=key, label=label, series=sid, unit=unit, group=group, decimals=dec,
                latest=round(s[-1][1], 4), date=s[-1][0], prior=round(s[-2][1], 4), prior_date=s[-2][0],
                change=round(s[-1][1]-s[-2][1], 4), spark=[round(p[1], 4) for p in hist],
                source=f"https://fred.stlouisfed.org/series/{sid}"))
        except Exception as e:
            failed.append(dict(key=key, series=sid, error=str(e))); print("FAIL", sid, e, file=sys.stderr)
    add_expectations(res)
    try:  # manual, sourced forecasts (e.g. Fannie Mae mortgage rate path)
        fc = json.load(open("forecasts.json"))
        for r in res:
            if r["key"] in fc: r["forecast"] = fc[r["key"]]
    except Exception as e:
        print("WARN forecasts.json", e, file=sys.stderr)
    # Fed target range
    try:
        lo, hi = get("DFEDTARL"), get("DFEDTARU")
        res.append(dict(key="target", label="Fed funds target range", series="DFEDTARL/DFEDTARU", unit="%",
            group="Rates", decimals=2, range=[lo[-1][1], hi[-1][1]], date=hi[-1][0],
            source="https://fred.stlouisfed.org/series/DFEDTARU"))
    except Exception as e:
        failed.append(dict(key="target", series="DFEDTAR*", error=str(e)))
    try:
        import calendar_fetch
        cal = calendar_fetch.build(datetime.now(et).date())
    except Exception as e:
        cal = dict(events=[], notes=[f"calendar failed: {e}"]); print("WARN calendar", e, file=sys.stderr)
    json.dump(dict(calendar=cal, updated_et=datetime.now(et).strftime("%Y-%m-%d %H:%M ET"), indicators=res, failed=failed),
              open("data.json", "w"), indent=1)
    print(f"ok {len(res)} indicators, {len(failed)} failed")
    if not res: sys.exit(1)

if __name__ == "__main__": main()
