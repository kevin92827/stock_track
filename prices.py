"""上市櫃股票每日收盤價、漲跌幅與發行股數，存在 data/prices.csv（營收日曆算當日漲跌幅與市值用）。

來源：證交所每日收盤行情（MI_INDEX）、櫃買中心上櫃每日收盤行情；上市公司的發行股數來自證交所 OpenAPI 公司基本資料，
上櫃的直接在行情表裡。每個交易日 3 次請求。
"""

import csv
import datetime as dt
import os
import re
import ssl
import sys
import time

import requests
from requests.adapters import HTTPAdapter

BASE = os.path.dirname(os.path.abspath(__file__))
PX_PATH = os.path.join(BASE, "data", "prices.csv")
FIELDS = ["date", "code", "close", "pct", "shares"]
TWSE = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
TWSE_SHARES = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class _TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kw["ssl_context"] = ctx
        return super().init_poolmanager(*a, **kw)


_session = None


def _get(url, **params):
    global _session
    if _session is None:
        _session = requests.Session()
        _session.mount("https://", _TLSAdapter())
        _session.headers["User-Agent"] = UA
    time.sleep(1.5)
    r = _session.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def num(s):
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def twse_shares():
    """上市公司已發行普通股數 -> {code: shares}"""
    out = {}
    for x in _get(TWSE_SHARES):
        n = num(x.get("已發行普通股數或TDR原股發行股數"))
        if n:
            out[x["公司代號"].strip()] = int(n)
    return out


def quotes(day, shares_l=None):
    """某交易日全部上市櫃股票 -> {code: {close, pct, shares}}；休市日回空 dict"""
    out = {}
    j = _get(TWSE, date=day.strftime("%Y%m%d"), type="ALLBUT0999", response="json")
    for t in j.get("tables") or []:
        if "每日收盤行情" not in t.get("title", ""):
            continue
        for row in t.get("data") or []:
            code, close, sign, diff = row[0].strip(), num(row[8]), row[9], num(row[10])
            if not close or diff is None:
                continue
            signed = -diff if "-" in re.sub(r"<[^>]+>", "", sign) else diff if "+" in sign else 0.0
            prev = close - signed
            out[code] = {"close": close, "pct": round(signed / prev * 100, 2) if prev else None,
                         "shares": (shares_l or {}).get(code)}
    j = _get(TPEX, date=day.strftime("%Y/%m/%d"), type="EW", response="json")
    for t in j.get("tables") or []:
        for row in t.get("data") or []:
            code, close, chg, shares = row[0].strip(), num(row[2]), num(row[3]), num(row[14]) if len(row) > 14 else None
            if not close or chg is None:
                continue
            prev = close - chg
            out[code] = {"close": close, "pct": round(chg / prev * 100, 2) if prev else None,
                         "shares": int(shares) if shares else None}
    return out


def load():
    if not os.path.exists(PX_PATH):
        return {}
    with open(PX_PATH, encoding="utf-8-sig", newline="") as f:
        return {(r["date"], r["code"]): r for r in csv.DictReader(f)}


def save(rows):
    os.makedirs(os.path.dirname(PX_PATH), exist_ok=True)
    tmp = PX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    os.replace(tmp, PX_PATH)


def update(days):
    """抓還沒有的日期。回傳訊息。"""
    rows = load()
    have = {k[0] for k in rows}
    todo = sorted({d for d in days if d.isoformat() not in have and d <= dt.date.today()})
    if not todo:
        return f"沒有需要補的日期，共 {len(have)} 個交易日"
    shares_l = twse_shares()
    fetched, empty = [], []
    for day in todo:
        q = quotes(day, shares_l)
        if not q:
            empty.append(day.isoformat())
            continue
        for code, v in q.items():
            rows[(day.isoformat(), code)] = {"date": day.isoformat(), "code": code, "close": v["close"],
                                            "pct": "" if v["pct"] is None else v["pct"],
                                            "shares": v["shares"] or ""}
        fetched.append(day.isoformat())
    save(rows)
    msg = f"補了 {len(fetched)} 個交易日（{', '.join(fetched)}）" if fetched else "沒有新的交易日"
    if empty:
        msg += f"；{', '.join(empty)} 無行情（休市或尚未公布）"
    return msg


def needed_days():
    """營收公告日（含隔一個交易日）與可轉債公告日，最近 200 天內"""
    days = {dt.date.today()}
    cutoff = dt.date.today() - dt.timedelta(days=200)
    for path, col in ((os.path.join(BASE, "data", "monthly_revenue.csv"), "announce_date"),
                      (os.path.join(BASE, "data", "cb_announcements.csv"), "date")):
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    if r.get(col):
                        d = dt.date.fromisoformat(r[col])
                        if d >= cutoff:
                            days.add(d)
                            days.add(d + dt.timedelta(days=1))  # 收盤後公告的，隔天才反映
                            if d.weekday() == 4:
                                days.add(d + dt.timedelta(days=3))
    return sorted(d for d in days if d.weekday() < 5)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(update(needed_days()))
