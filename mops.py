"""公開資訊觀測站（MOPS）重大訊息：抓「轉換公司債代收價款及存儲專戶行庫」這類公告。

資料存在 data/cb_announcements.csv。MOPS 會封鎖頻繁請求的 IP，所以每次請求至少間隔 3.5 秒，
一遇到封鎖頁就停手。
"""

import csv
import datetime as dt
import os
import re
import ssl
import time

import requests
from requests.adapters import HTTPAdapter

BASE = os.path.dirname(os.path.abspath(__file__))
CB_PATH = os.path.join(BASE, "data", "cb_announcements.csv")
CB_FIELDS = ["date", "time", "code", "name", "market", "subject", "enter_date", "serial",
             "method", "method_seq", "method_detail"]  # 承銷方式由 cb_method.py 填
API = "https://mops.twse.com.tw/mops/api/t05st02"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MIN_INTERVAL = 3.5
BLOCK_MARKS = ("因為安全性考量", "THE PAGE CANNOT BE ACCESSED")


class Blocked(RuntimeError):
    """MOPS 回了封鎖頁，這次執行不要再對它發請求"""


class _TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kw["ssl_context"] = ctx
        return super().init_poolmanager(*a, **kw)


_session = None
_last = 0.0


def request(method, url, **kw):
    """對 MOPS（含 mopsov）發請求：全程共用同一個節流，遇到封鎖頁丟 Blocked。"""
    global _session, _last
    if _session is None:
        _session = requests.Session()
        _session.mount("https://", _TLSAdapter())
        _session.headers["User-Agent"] = UA
    for attempt in range(3):
        wait = MIN_INTERVAL * (1 + 5 * attempt) - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = _session.request(method, url, timeout=60, **kw)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise
            continue
        finally:
            _last = time.monotonic()
        if any(m.encode() in r.content[:4000] for m in BLOCK_MARKS):
            raise Blocked("公開資訊觀測站回應封鎖頁，已停止請求")
        if r.status_code in (502, 503, 504) and attempt < 2:
            continue
        r.raise_for_status()
        return r


def _post(url, body):
    return request("POST", url, json=body).json()


def is_cb(subject):
    """各公司用語不一（代收價款行庫及存儲專戶行庫、代收股款行庫…），主旨中間還會換行，所以放寬比對"""
    s = re.sub(r"\s+", "", subject)
    return "轉換公司債" in s and ("代收" in s or "存儲" in s)


def announcements(day):
    """某一天全市場（上市、上櫃、興櫃、公開發行）的重大訊息。清單可能夾帶前一天的資料。"""
    j = _post(API, {"year": str(day.year - 1911), "month": f"{day.month:02d}", "day": f"{day.day:02d}"})
    if not j or j.get("code") != 200:
        return []
    out = []
    for row in (j.get("result") or {}).get("data") or []:
        date, tm, code, name, subject, det = row[:6]
        y, m, d = date.split("/")
        p = (det or {}).get("parameters") or {}
        out.append({"date": f"{int(y) + 1911}-{m}-{d}", "time": tm, "code": str(code).strip(), "name": name.strip(),
                    "market": p.get("marketKind", ""), "subject": re.sub(r"\s+", "", subject),
                    "enter_date": str(p.get("enterDate", "")), "serial": str(p.get("serialNumber", ""))})
    return out


def _key(r):
    return (r["date"], r["time"], r["code"], r["serial"])


def load_cb():
    if not os.path.exists(CB_PATH):
        return []
    with open(CB_PATH, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_cb(rows):
    os.makedirs(os.path.dirname(CB_PATH), exist_ok=True)
    rows = sorted({_key(r): r for r in rows}.values(), key=_key)
    tmp = CB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CB_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in CB_FIELDS} for r in rows)
    os.replace(tmp, CB_PATH)
    return rows


def update_cb(days):
    """抓 days 這些日期的公告，把可轉債代收價款相關的併進 CSV。回傳 (新增筆數, 目前總筆數)。"""
    rows = load_cb()
    have = {_key(r) for r in rows}
    added = 0
    for day in days:
        for r in announcements(day):
            if is_cb(r["subject"]) and _key(r) not in have:
                rows.append(r)
                have.add(_key(r))
                added += 1
    return added, len(save_cb(rows))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    today = dt.date.today()
    print("新增 %d 筆，共 %d 筆" % update_cb([today - dt.timedelta(days=i) for i in range(n - 1, -1, -1)]))
