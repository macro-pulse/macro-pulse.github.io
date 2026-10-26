"""Analyst rating actions (MarketBeat daily ratings table) for the Analyst U/D tab."""
import html, http.cookiejar, json, re, sys, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
URL = "https://www.marketbeat.com/ratings/"
BIG, NOTABLE = 100e9, 10e9   # all actions for >= $100B; upgrades/downgrades/initiations for >= $10B
ACT = [("upgrad", "Upgrade"), ("downgrad", "Downgrade"), ("initiat", "Initiated"),
       ("reiterat", "Reiterated"), ("reaffirm", "Reiterated"), ("target", "Target change")]

def _day(d):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def get(data=None):
        h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
        if data: h["Content-Type"] = "application/x-www-form-urlencoded"
        return op.open(urllib.request.Request(URL, data=data, headers=h), timeout=45).read().decode("utf-8", "replace")
    f = dict(re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', get()))
    f["ctl00$cphPrimaryContent$tlsFilter$txtStartDate"] = d.isoformat()
    f["__EVENTTARGET"] = "ctl00$cphPrimaryContent$tlsFilter$btnApplyDelayPostbackFilters"
    h = get(urllib.parse.urlencode(f).encode())
    shown = re.findall(r'txtStartDate" type="date" value="([^"]+)"', h)
    if not shown or shown[0] != d.isoformat(): raise RuntimeError(f"date not applied ({shown})")
    tb = h[h.find("<tbody"):h.find("</tbody>")]
    out = []
    for r in re.findall(r"<tr.*?</tr>", tb, re.S):
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", r, re.S)
        if len(cells) < 7: continue
        clean = [html.unescape((re.search(r'data-clean="([^"]*)"', a) or [0, ""])[1]) for a, _ in cells]
        text = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for _, c in cells]
        sym, _, name = clean[0].partition("|")
        act_raw = text[1].replace(" by", "").strip()
        action = next((v for k, v in ACT if k in act_raw.lower()), act_raw)
        firm = clean[2].split("|")[0]
        def money(s):
            s = s.strip()
            return "" if s in ("", "0", "$0.00", "N/A") else s
        price = money(clean[4].split("|")[0])
        pt = (clean[5].split("|") + [""])[:2]
        rt = (clean[6].split("|") + [""])[:2]
        rt = ["" if x.strip() in ("N/A", "") else x.strip() for x in rt]
        if rt[0] == rt[1] and action not in ("Upgrade", "Downgrade"): rt[0] = ""
        pid = re.search(r"details/(\d+)/", r)
        out.append(dict(date=d.isoformat(), ticker=sym, company=name, action=action, action_raw=act_raw, firm=firm,
                        rating_old=rt[0], rating_new=rt[1], pt_old=money(pt[0]), pt_new=money(pt[1]),
                        price=price, id=pid[1] if pid else ""))
    return out

def caps(http):
    d = json.loads(http("https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true", "application/json", 2, 30))
    c = {}
    for r in d["data"]["rows"]:
        try: c[r["symbol"].replace("/", ".")] = float(r["marketCap"] or 0)
        except ValueError: pass
    return c

def build(today, http, prev=None, days=7):
    """prev = previous data.json 'analysts' block (past days are reused, today & yesterday refetched)."""
    dates, d = [], today
    while len(dates) < days:
        if d.weekday() < 5: dates.append(d)
        d -= timedelta(days=1)
    old = {}
    for a in (prev or {}).get("actions", []): old.setdefault(a["date"], []).append(a)
    cached = set((prev or {}).get("days_ok", []))
    todo = [x for x in dates if x.isoformat() not in cached or x >= dates[min(1, len(dates)-1)]]
    def safe(x):
        try: return x, _day(x)
        except Exception as e: print("WARN analysts", x, e, file=sys.stderr); return x, None
    with ThreadPoolExecutor(4) as ex: got = dict(ex.map(safe, todo))
    capmap = caps(http)
    rows, ok, failed = [], [], []
    for x in dates:
        k = x.isoformat()
        if x in got and got[x] is not None:
            day = []
            for a in got[x]:
                cap = capmap.get(a["ticker"], 0)
                if cap >= BIG or (cap >= NOTABLE and a["action"] in ("Upgrade", "Downgrade", "Initiated")):
                    a["cap"] = cap; day.append(a)
            rows += day; ok.append(k)
        elif k in cached:
            rows += old.get(k, []); ok.append(k)
        else:
            failed.append(k)
    seen, uniq = set(), []
    for a in rows:
        key = a["id"] or (a["date"], a["ticker"], a["firm"], a["action_raw"])
        if key in seen: continue
        seen.add(key); uniq.append(a)
    uniq.sort(key=lambda a: (a["date"], a.get("cap", 0)), reverse=True)
    print(f"analysts: fetched {len(todo)} days, {len(uniq)} actions kept, failed {failed}")
    return dict(actions=uniq, days_ok=ok, days_failed=failed, source=URL,
                note="All actions for stocks with market cap >= $100B; upgrades, downgrades and initiations for >= $10B (caps from Nasdaq).")
