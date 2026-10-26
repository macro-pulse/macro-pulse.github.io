#!/usr/bin/env python3
"""Fetch US macro series from FRED public CSV endpoints into data.json."""
import csv, io, json, sys, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
# key, label, series, transform, unit, group, decimals
IND = [
 ("cpi","CPI YoY","CPIAUCSL","yoy12","%","Inflation",1),
 ("core_cpi","Core CPI YoY","CPILFESL","yoy12","%","Inflation",1),
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

def transform(pts, t):
    d = [p[0] for p in pts]; v = [p[1] for p in pts]
    if t == "yoy12": return list(zip(d[12:], [(v[i]/v[i-12]-1)*100 for i in range(12, len(v))]))
    if t == "mom": return list(zip(d[1:], [(v[i]/v[i-1]-1)*100 for i in range(1, len(v))]))
    if t == "diff": return list(zip(d[1:], [v[i]-v[i-1] for i in range(1, len(v))]))
    if t == "thousands": return list(zip(d, [x/1000 for x in v]))
    if t == "bp": return list(zip(d, [x*100 for x in v]))
    return list(zip(d, v))

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
    # Fed target range
    try:
        lo, hi = get("DFEDTARL"), get("DFEDTARU")
        res.append(dict(key="target", label="Fed funds target range", series="DFEDTARL/DFEDTARU", unit="%",
            group="Rates", decimals=2, range=[lo[-1][1], hi[-1][1]], date=hi[-1][0],
            source="https://fred.stlouisfed.org/series/DFEDTARU"))
    except Exception as e:
        failed.append(dict(key="target", series="DFEDTAR*", error=str(e)))
    json.dump(dict(updated_et=datetime.now(et).strftime("%Y-%m-%d %H:%M ET"), indicators=res, failed=failed),
              open("data.json", "w"), indent=1)
    print(f"ok {len(res)} indicators, {len(failed)} failed")
    if not res: sys.exit(1)

if __name__ == "__main__": main()
