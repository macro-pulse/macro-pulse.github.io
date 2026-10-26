"""Major equity indexes (CNBC quote data) + index ETFs (Nasdaq). Real sources only; failures omitted and listed."""
from concurrent.futures import ThreadPoolExecutor
from markets_fetch import cnbc_hist, cnbc_quotes, nasdaq_etf

CN, NQ = "CNBC quote data", "Nasdaq"
# key, symbol, label, section, market (tz, open, close local, country code)
IX = [("spx", ".SPX", "S&P 500", "us", "US"), ("dji", ".DJI", "Dow Jones Industrial Avg", "us", "US"),
      ("ixic", ".IXIC", "Nasdaq Composite", "us", "US"), ("ndx", ".NDX", "Nasdaq-100", "us", "US"),
      ("rut", ".RUT", "Russell 2000", "us", "US"), ("mid", ".MID", "S&P MidCap 400", "us", "US"),
      ("spxew", ".SPXEW", "S&P 500 Equal Weight", "us", "US"),
      ("nifty", ".NSEI", "Nifty 50", "india", "IN"), ("sensex", ".BSESN", "BSE Sensex", "india", "IN"),
      ("bank", ".NSEBANK", "Nifty Bank", "india", "IN"),
      ("sx5e", ".STOXX50E", "Euro Stoxx 50", "eu", "EU"), ("dax", ".GDAXI", "DAX", "eu", "DE"),
      ("ftse", ".FTSE", "FTSE 100", "eu", "UK"), ("cac", ".FCHI", "CAC 40", "eu", "FR"),
      ("ibex", ".IBEX", "IBEX 35", "eu", "ES"), ("mib", ".FTMIB", "FTSE MIB", "eu", "IT"), ("smi", ".SSMI", "SMI", "eu", "CH"),
      ("n225", ".N225", "Nikkei 225", "asia", "JP"), ("topix", ".TOPX", "TOPIX", "asia", "JP"),
      ("hsi", ".HSI", "Hang Seng", "asia", "HK"), ("ssec", ".SSEC", "Shanghai Composite", "asia", "CN"),
      ("csi", ".CSI300", "CSI 300", "asia", "CN"), ("kospi", ".KS11", "KOSPI", "asia", "KR"),
      ("asx", ".AXJO", "S&P/ASX 200", "asia", "AU"), ("twii", ".TWII", "Taiwan TAIEX", "asia", "TW"),
      ("tsx", ".GSPTSE", "S&P/TSX Composite", "am", "CA"), ("bvsp", ".BVSP", "Bovespa", "am", "BR"),
      ("mxx", ".MXX", "Mexico IPC", "am", "MX")]
ETF = [("SPY", "SPDR S&P 500 ETF", "etf"), ("QQQ", "Invesco QQQ (Nasdaq-100)", "etf"), ("DIA", "SPDR Dow Jones ETF", "etf"),
       ("IWM", "iShares Russell 2000 ETF", "etf"), ("URTH", "iShares MSCI World ETF", "glob"),
       ("ACWI", "iShares MSCI ACWI ETF", "glob"), ("EEM", "iShares MSCI Emerging Markets ETF", "glob")]

def build(http, today):
    items, failed = [], []
    def one(x):
        k, s, l, sec, m = x
        try: return dict(key=k, ticker=s.lstrip("."), label=l, sec=sec, mkt=m, src=CN, url=f"https://www.cnbc.com/quotes/{s}", h=cnbc_hist(http, s))
        except Exception as e: failed.append(f"{l}: {str(e)[:80]}")
    def etf(x):
        s, l, sec = x
        try: return dict(key=s.lower(), ticker=s, label=l, sec=sec, mkt="US", src=NQ,
                         url=f"https://www.nasdaq.com/market-activity/etf/{s.lower()}/historical", h=nasdaq_etf(http, s, today))
        except Exception as e: failed.append(f"{s}: {str(e)[:80]}")
    with ThreadPoolExecutor(16) as ex:
        res = list(ex.map(one, IX)) + list(ex.map(etf, ETF))
    items = [r for r in res if r and r["h"]]
    # latest quote (may be intraday / newer than the last daily bar)
    try:
        q = cnbc_quotes(http, [x[1] for x in IX])
        for it in items:
            if it["src"] == CN and "." + it["ticker"] in q:
                d, v = q["." + it["ticker"]]
                if abs(v / it["h"][-1][1] - 1) > 0.08:  # implausible jump vs last close -> keep daily history only
                    failed.append(f"{it['label']} latest quote {v:,.2f} rejected (>8% from last close)"); continue
                if d > it["h"][-1][0]: it["h"].append([d, v]); it["live"] = d
                elif d == it["h"][-1][0]: it["h"][-1][1] = v
    except Exception as e: failed.append(f"CNBC latest quotes: {str(e)[:80]}")
    return dict(items=items, failed=failed)
