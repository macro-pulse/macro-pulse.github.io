"""Build calendar events (macro releases, FOMC, big-cap earnings) for data.json."""
import json, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
# FRED release id -> (short label, linked indicator keys for "Exp.")
RELEASES = {10: ("CPI", ["cpi", "core_cpi"]), 46: ("PPI", []), 50: ("Jobs report", []),
            192: ("JOLTS", []), 180: ("Jobless claims", []), 9: ("Retail sales", []),
            53: ("GDP", ["gdp"]), 54: ("PCE / income", ["core_pce"]), 91: ("UMich sentiment", [])}
MIN_CAP = 100e9

def http(url, accept="text/html", tries=3, timeout=20):
    ua = "curl/8.5.0" if "stlouisfed" in url else UA  # FRED stalls browser-like UAs
    for attempt in range(tries):
        try: return _http(url, accept, ua, timeout)
        except Exception:
            if attempt == tries - 1: raise

def _http(url, accept, ua, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")

def ct_to_et(t):
    m = re.match(r"(\d+):(\d+) (am|pm)", t.strip())
    if not m: return ""
    h = int(m[1]) % 12 + (12 if m[3] == "pm" else 0) + 1  # FRED lists Central Time
    return f"{(h - 1) % 12 + 1}:{m[2]} {'am' if h < 12 else 'pm'} ET"

def fred_releases(start, end, notes):
    ev = []
    for rid, (label, keys) in RELEASES.items():
        try:
            t = http(f"https://fred.stlouisfed.org/releases/calendar?rid={rid}&vs={start}&ve={end}&ob=rd&od=asc")
            day = None
            for d, tm in re.findall(r'font-weight: bold;">\w+ (\w+ \d+, \d{4})</span>|<td nowrap[^>]*>\s*([^<]*?)\s*</td>\s*<td[^>]*>\s*<a href="/release\?rid=' + str(rid) + '"', t):
                if d: day = datetime.strptime(d, "%B %d, %Y").date().isoformat()
                elif day:
                    ev.append(dict(date=day, type="macro", title=label, time=ct_to_et(tm), keys=keys,
                                   url=f"https://fred.stlouisfed.org/releases/calendar?rid={rid}"))
                    day = None
        except Exception as e:
            notes.append(f"FRED release calendar ({label}) failed"); print("WARN cal", rid, e, file=sys.stderr)
    return ev

def fomc(notes):
    ev = []
    try:
        t = http("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
        months = "January February March April May June July August September October November December".split()
        for y in re.findall(r"(\d{4}) FOMC Meetings", t):
            i = t.find(f"{y} FOMC Meetings"); j = t.find("FOMC Meetings", i + 20)
            txt = re.sub(r"\s+", " ", re.sub("<[^>]+>", "|", t[i:j if j > 0 else None]))
            for m in re.finditer(r"\|(" + "|".join(months) + r")(?:/(" + "|".join(months) + r"))?\|[| ]*(\d+)-(\d+)(\*?)\|", txt):
                mo = months.index(m[2] or m[1]) + 1
                dd = date(int(y), mo, int(m[4])).isoformat()
                ev.append(dict(date=dd, type="macro", title="FOMC decision" + (" + SEP" if m[5] else ""),
                               time="2:00 pm ET", keys=[], url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"))
            for r in re.findall(r"Minutes:.{0,200}?\(Released (\w+ \d+, \d{4})\)", txt):
                ev.append(dict(date=datetime.strptime(r, "%B %d, %Y").date().isoformat(), type="macro",
                               title="FOMC minutes", time="2:00 pm ET", keys=[],
                               url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"))
    except Exception as e:
        notes.append("Federal Reserve FOMC calendar failed"); print("WARN fomc", e, file=sys.stderr)
    return ev

def _earn_day(d):
    rows = json.loads(http(f"https://api.nasdaq.com/api/calendar/earnings?date={d}", "application/json", 2, 10))["data"]["rows"] or []
    out = []
    for r in rows:
        cap = int(re.sub(r"[^\d]", "", r.get("marketCap") or "") or 0)
        if cap >= MIN_CAP:
            tm = {"time-pre-market": "BMO", "time-after-hours": "AMC"}.get(r.get("time"), "")
            eps = r.get("epsForecast") or ""
            out.append(dict(date=d.isoformat(), type="earnings", title=r["symbol"], name=r.get("name", ""),
                            time=tm, eps_est=eps if eps.strip() not in ("", "N/A") else "", cap=cap,
                            url=f"https://www.nasdaq.com/market-activity/stocks/{r['symbol'].lower()}/earnings"))
    return out

def earnings(start, end, notes):
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    ev, fails = [], 0
    def safe(d):
        try: return _earn_day(d)
        except Exception as e: print("WARN earnings", d, e, file=sys.stderr); return None
    with ThreadPoolExecutor(6) as ex:
        for r in ex.map(safe, days):
            if r is None: fails += 1
            else: ev += r
    if fails: notes.append(f"Nasdaq earnings calendar failed for {fails} day(s)")
    seen, out = set(), []
    for e in sorted(ev, key=lambda e: (e["date"], -e["cap"])):
        k = (e["date"], re.sub(r"\s*(Class [A-Z]|Inc\.?|Corp\.?).*$", "", e["name"]))
        if k not in seen: seen.add(k); out.append(e)  # drop duplicate share classes (GOOG/GOOGL)
    return out

def build(today):
    start = today.replace(day=1)
    end = (start + timedelta(days=62)).replace(day=1) - timedelta(days=1)  # end of next month
    notes = []
    ev = fred_releases(start, end, notes) + fomc(notes)
    ev = [e for e in ev if start.isoformat() <= e["date"] <= end.isoformat()]
    ev += earnings(start, end, notes)
    ev.sort(key=lambda e: (e["date"], e["type"] != "macro"))
    return dict(start=start.isoformat(), end=end.isoformat(), events=ev, notes=notes,
                sources=["FRED release calendar (BLS, BEA, Census, DOL, UMich release dates)",
                         "Federal Reserve FOMC calendar", "Nasdaq earnings calendar (market cap >= $100B)"])

MAP = {"strong buy": "Bullish", "buy": "Bullish", "outperform": "Bullish", "overweight": "Bullish",
       "hold": "Neutral", "neutral": "Neutral", "sell": "Bearish", "strong sell": "Bearish",
       "underperform": "Bearish", "underweight": "Bearish"}

def analyst(sym):
    """Nasdaq consensus analyst rating, counts, price target and last price; {} if unavailable."""
    out = {}
    s = sym.upper().replace(".", "-")
    try:
        d = json.loads(http(f"https://api.nasdaq.com/api/analyst/{s}/ratings", "application/json", 1, 10))["data"] or {}
        m = (d.get("meanRatingType") or "").strip()
        if m.lower() in MAP: out.update(rating=m, sentiment=MAP[m.lower()])
    except Exception as e:
        print("WARN rating", sym, e, file=sys.stderr)
    try:
        c = (json.loads(http(f"https://api.nasdaq.com/api/analyst/{s}/targetprice", "application/json", 1, 10))["data"] or {}).get("consensusOverview") or {}
        if c.get("buy") is not None: out.update(buy=c.get("buy"), hold=c.get("hold"), sell=c.get("sell"))
        if c.get("priceTarget"): out["target"] = c["priceTarget"]
    except Exception as e:
        print("WARN target", sym, e, file=sys.stderr)
    try:
        p = json.loads(http(f"https://api.nasdaq.com/api/quote/{s}/info?assetclass=stocks", "application/json", 1, 10))["data"]["primaryData"]
        out["price"] = float(p["lastSalePrice"].replace("$", "").replace(",", ""))
        out["price_time"] = p.get("lastTradeTimestamp", "")
    except Exception as e:
        print("WARN quote", sym, e, file=sys.stderr)
    return out

def enrich_upcoming(cal, today, days=14, prev=None):
    """Analyst data for the next `days`. prev: {(date,title): analyst} from last data.json.
    Ratings fetched earlier the same ET day are reused (refresh once a day); if Nasdaq fails, last data is kept."""
    end = (today + timedelta(days=days)).isoformat()
    prev = prev or {}
    todo = [e for e in cal.get("events", []) if e["type"] == "earnings" and today.isoformat() <= e["date"] < end]
    need = [e for e in todo if prev.get((e["date"], e["title"]), {}).get("fetched") != today.isoformat()]
    def one(e):
        a = analyst(e["title"])
        if a: a["fetched"] = today.isoformat()
        return a
    with ThreadPoolExecutor(6) as ex:
        got = dict(zip([id(e) for e in need], ex.map(one, need)))
    n = 0
    for e in todo:
        a = got.get(id(e)) or prev.get((e["date"], e["title"]), {})
        if a: e["analyst"] = a; n += 1
    print(f"analyst: {len(need)} fetched, {len(todo) - len(need)} cached today")
    return n

def ipos(today, notes):
    """Nasdaq IPO calendar for current + next month: upcoming, priced, filed."""
    up, pr, fi, ok = [], [], [], 0
    m1 = today.replace(day=1); m2 = (m1 + timedelta(days=32)).replace(day=1)
    pm = (m1 - timedelta(days=1)).replace(day=1)  # previous month too, for 'recently priced'
    def md(s):
        try: return datetime.strptime(s, "%m/%d/%Y").date().isoformat()
        except Exception: return ""
    seen = set()
    for m in (pm, m1, m2):
        try:
            d = json.loads(http(f"https://api.nasdaq.com/api/ipo/calendar?date={m:%Y-%m}", "application/json", 2, 15))["data"]; ok += 1
        except Exception as e:
            print("WARN ipo", m, e, file=sys.stderr); continue
        def rows(k):
            v = d.get(k) or {}
            return v.get("rows") or (v.get("upcomingTable") or {}).get("rows") or []
        for r in rows("upcoming"):
            if ("u", r["dealID"]) in seen: continue
            seen.add(("u", r["dealID"]))
            up.append(dict(date=md(r.get("expectedPriceDate") or ""), ticker=r.get("proposedTickerSymbol") or "", name=r.get("companyName") or "",
                           exchange=r.get("proposedExchange") or "", price=r.get("proposedSharePrice") or "",
                           shares=r.get("sharesOffered") or "", size=r.get("dollarValueOfSharesOffered") or "", deal=r["dealID"]))
        for r in rows("priced"):
            if ("p", r["dealID"]) in seen: continue
            seen.add(("p", r["dealID"]))
            pr.append(dict(date=md(r.get("pricedDate") or ""), ticker=r.get("proposedTickerSymbol") or "", name=r.get("companyName") or "",
                           exchange=r.get("proposedExchange") or "", price=r.get("proposedSharePrice") or "",
                           shares=r.get("sharesOffered") or "", size=r.get("dollarValueOfSharesOffered") or "", deal=r["dealID"]))
        if m == m1 or m == pm:
            for r in rows("filed"):
                if ("f", r["dealID"]) in seen: continue
                seen.add(("f", r["dealID"]))
                fi.append(dict(date=md(r.get("filedDate") or ""), ticker=r.get("proposedTickerSymbol") or "", name=r.get("companyName") or "",
                               size=r.get("dollarValueOfSharesOffered") or "", deal=r["dealID"]))
    if not ok: return None
    cut = (today - timedelta(days=14)).isoformat()
    pr = [p for p in pr if p["date"] >= cut]
    def quote(p):
        if not p["ticker"]: return
        try:
            q = json.loads(http(f"https://api.nasdaq.com/api/quote/{p['ticker']}/info?assetclass=stocks", "application/json", 1, 10))["data"]["primaryData"]
            p["last"] = float(q["lastSalePrice"].replace("$", "").replace(",", "")); p["last_time"] = q.get("lastTradeTimestamp", "")
        except Exception as e: print("WARN ipo quote", p["ticker"], e, file=sys.stderr)
    with ThreadPoolExecutor(6) as ex: list(ex.map(quote, pr))
    up.sort(key=lambda r: (r["date"] or "9", r["name"])); pr.sort(key=lambda r: r["date"], reverse=True)
    fi.sort(key=lambda r: r["date"], reverse=True)
    return dict(upcoming=up, priced=pr, filed=fi, as_of=datetime.now().strftime("%Y-%m-%d"),
                source="https://www.nasdaq.com/market-activity/ipos")
