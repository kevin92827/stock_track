"""各投信官網的持股抓取。

每個 fetch_xxx(fund_id, day) 回傳 (資料日, rows) 或 None（該日無資料）。
  day  : datetime.date，None 表示抓最新一期。
  資料日: 'YYYY-MM-DD'，持股實際所屬的日期（不是公告日），一律以官網回傳的為準。
  rows : [(type, code, name, qty, weight, amount)]，type 為 stock/future/option/other。

這些都是官網前端使用的非公開介面，網站改版時可能失效。
"""

import datetime as dt
import html
import re
import ssl
import threading
import time

import requests
from requests.adapters import HTTPAdapter

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MIN_INTERVAL = 1.2  # 同一家投信兩次請求的最小間隔（秒）


class _TLSAdapter(HTTPAdapter):
    # 用作業系統憑證庫（大華銀沒送中繼憑證），並關掉 Python 3.13+ 的 X509 strict
    # （元大、野村、凱基的憑證缺 Subject Key Identifier）。憑證鏈與主機名仍會驗證。
    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kw["ssl_context"] = ctx
        return super().init_poolmanager(*a, **kw)


_local = threading.local()


def _session():
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.mount("https://", _TLSAdapter())
        s.headers["User-Agent"] = UA
        _local.s = s
        _local.last = 0.0
    return _local.s


def _request(method, url, **kw):
    s = _session()
    for attempt in range(3):
        wait = MIN_INTERVAL * (1 + 4 * attempt) - (time.monotonic() - _local.last)
        if wait > 0:
            time.sleep(wait)
        try:
            return s.request(method, url, timeout=30, **kw)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise
        finally:
            _local.last = time.monotonic()


def num(v):
    """'TWD 366,870,989.00' / '9.41%' / 'NT$640,909' -> float；無法解析回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def norm_date(v):
    """各種日期格式 -> 'YYYY-MM-DD'；無法解析回 None。"""
    if not v:
        return None
    s = str(v)
    m = re.search(r"/Date\((\d+)", s)
    if m:
        d = dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=int(m.group(1)), hours=8)
        return d.date().isoformat()
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _cells(fragment):
    """HTML -> 每個 <tr> 的儲存格文字。"""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", fragment, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if cells:
            rows.append(cells)
    return rows


def _walk_back(fetch, fund_id, days=10):
    """沒有「最新」模式的來源：從今天往回找最近一個有資料的日子。"""
    today = dt.date.today()
    for i in range(days):
        res = fetch(fund_id, today - dt.timedelta(days=i))
        if res:
            return res
    return None


# ---------------------------------------------------------------- 元大
def fetch_yuanta(fund_id, day=None):
    p = {"APIType": "ETFAPI", "FuncId": "PCF/Daily", "ticker": fund_id}
    if day:
        p["ndate"] = day.strftime("%Y%m%d")
    r = _request("GET", "https://etfapi.yuantaetfs.com/ectranslation/api/bridge", params=p)
    r.raise_for_status()
    j = r.json()
    pcf = j.get("PCF") or {}
    date = norm_date(pcf.get("trandate"))
    fw = j.get("FundWeights") or {}
    if not date or not fw:
        return None
    # 非交易日仍會回應，但股票、期貨市值都是 0，內容是殘缺的
    summary = fw.get("Summary") or {}
    if not (summary.get("stkvalues") or summary.get("futvalues")):
        return None
    rows = []
    for x in fw.get("StockWeights") or []:
        rows.append(("stock", x["code"].strip(), x["name"], num(x["qty"]), num(x["weights"]), None))
    for x in fw.get("FutureWeights") or []:
        rows.append(("future", f"{x['code'].strip()}{x.get('ym') or ''}", x["name"],
                     num(x["qty"]), num(x["weights"]), None))
    cash = j.get("Cash") or {}
    for x in cash.get("CashPosition") or []:
        rows.append(("other", "", x["name"], None, num(x.get("rto")), num(x.get("amt"))))
    for x in cash.get("Margin") or []:
        rows.append(("other", "", x["name"], None, None, num(x.get("amt"))))
    return date, rows


# ---------------------------------------------------------------- 群益
def fetch_capital(fund_id, day=None):
    # date 對應公告日；持股屬於回應中的 date2
    body = {"fundId": fund_id, "date": day.isoformat() if day else None}
    r = _request("POST", "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback", json=body)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 200 or not j.get("data"):
        return None
    d = j["data"]
    date = norm_date((d.get("pcf") or {}).get("date2"))
    if not date:
        return None
    rows = []
    for x in d.get("stocks") or []:
        rows.append(("stock", x["stocNo"].strip(), x["stocName"], num(x["share"]), num(x["weight"]), None))
    for x in d.get("futures") or []:
        rows.append(("future", x["txEname"], x["txDesc"], num(x["lot"]), num(x["weight"]), None))
    for x in d.get("assets") or []:
        rows.append(("other", "", x["asDesc"], None, None, num(x["asMoney"])))
    return date, rows


# ---------------------------------------------------------------- 國泰
_CATHAY = "https://cwapi.cathaysite.com.tw/api/ETF/"


def _cathay(endpoint, **params):
    r = _request("GET", _CATHAY + endpoint, params=params)
    r.raise_for_status()
    j = r.json()
    return j.get("result") if j.get("returnCode") == "2000" else None


def fetch_cathay(fund_id, day=None):
    p = {"FundCode": fund_id}
    if day:
        p["SearchDate"] = day.isoformat()
    assets = _cathay("GetETFAssets", **p)
    date = norm_date((assets or {}).get("preDate"))
    if not date or (day and date != day.isoformat()):
        return None
    p["SearchDate"] = date
    rows = []
    for x in _cathay("GetETFDetailStockList", **p) or []:
        rows.append(("stock", x["stockCode"].strip(), x["stockName"].strip(),
                     num(x["volumn"]), num(x["weights"]), None))
    for x in _cathay("GetETFDetailFutureList", **p) or []:
        ym = re.sub(r"\D", "", x.get("ftDate") or "")
        rows.append(("future", f"{x['ftNo'].strip()}{ym}", x["ftName"], num(x["volumn"]),
                     num(x["ntMkval"]), None))
    for x in _cathay("GetETFDetailBalList", **p) or []:
        if x["item"].startswith(("股票", "期貨")):  # 類別合計，不是部位
            continue
        rows.append(("other", "", x["item"], None, num(x.get("ntMkval")), num(x["amount"])))
    return (date, rows) if rows else None


# ---------------------------------------------------------------- 富邦
def fetch_fubon(fund_id, day=None):
    # 非交易日會自動回退到前一個交易日，資料日以頁面上的為準
    day = day or dt.date.today()
    r = _request("GET", "https://websys.fsit.com.tw/FubonETF/Trade/Assets.aspx",
                 params={"stkId": fund_id, "ddate": day.strftime("%Y/%m/%d"), "lan": "TW"})
    r.raise_for_status()
    r.encoding = "utf-8"
    m = re.search(r"資料日期：\s*([\d/]+)", r.text)
    if not m:
        return None
    rows = []
    for c in _cells(r.text):
        if any("合計" in x for x in c):
            continue
        if len(c) == 5 and num(c[2]) is not None and num(c[4]) is not None:
            kind = "stock" if re.fullmatch(r"\d{4,6}[A-Z]?", c[0]) else "future"
            rows.append((kind, c[0], c[1], num(c[2]), num(c[4]), num(c[3])))
        elif len(c) == 2 and re.search(r"\d", c[1]) and not re.search(r"\d{4}/\d{2}", c[1]):
            rows.append(("other", "", re.sub(r"\s*\(TWD\)", "", c[0]), None, None, num(c[1])))
    if not any(x[0] == "stock" for x in rows):
        return None
    return norm_date(m.group(1)), rows


# ---------------------------------------------------------------- 統一
def fetch_uni(fund_id, day=None):
    # date 是民國年的公告日；持股屬於回應中的 TranDate。現金部位此端點不提供。
    d = day or dt.date.today()
    body = {"fundCode": fund_id, "date": f"{d.year - 1911}/{d:%m/%d}", "specificDate": bool(day)}
    r = _request("POST", "https://www.ezmoney.com.tw/ETF/Transaction/GetPCF", json=body)
    r.raise_for_status()
    rows, date = [], None
    for a in r.json().get("asset") or []:
        kind = {"ST": "stock", "GD": "future"}.get(a.get("AssetCode"))
        if not kind:
            continue
        for x in a.get("Details") or []:
            date = date or norm_date(x.get("TranDate"))
            code = x["DetailCode"].strip()
            if kind == "future":
                code += re.sub(r"\D", "", str(x.get("MTH") or ""))
            rows.append((kind, code, x["DetailName"].strip(), num(x["Share"]),
                         num(x["NavRate"]), num(x.get("Amount"))))
    return (date, rows) if date and rows else None


# ---------------------------------------------------------------- 復華
def fetch_fuhhwa(fund_id, day=None):
    if day is None:
        return _walk_back(fetch_fuhhwa, fund_id)
    r = _request("GET", "https://www.fhtrust.com.tw/api/assets",
                 params={"fundID": fund_id, "qDate": day.strftime("%Y/%m/%d")})
    r.raise_for_status()
    if "json" not in r.headers.get("Content-Type", ""):
        return None
    res = (r.json().get("result") or [None])[0]
    if not res or not res.get("dDate"):
        return None
    kinds = {"股票": "stock", "期貨": "future"}
    rows = []
    for x in res.get("detail") or []:
        rows.append((kinds.get(x["ftype"], "other"), (x.get("stockid") or "").strip(),
                     x["stockname"].strip(), num(x.get("qshare")),
                     num(x.get("prate_addaccint")), num(x.get("mvalue"))))
    return (norm_date(res["dDate"]), rows) if rows else None


# ---------------------------------------------------------------- 中信
_CTBC = "https://www.ctbcinvestments.com.tw/API/"
_CTBC_KINDS = {"STOCK": "stock", "FUTURE": "future", "OPTION": "option"}


def _ctbc(path, **body):
    token = getattr(_local, "ctbc_token", "www.ctbcinvestments.com")
    r = _request("POST", _CTBC + path, params={"token": token}, json={"token": token, **body})
    r.raise_for_status()
    j = r.json()
    if j.get("ResultCode") != 0:
        raise RuntimeError(f"ctbc {path}: {j.get('ResultMsg')}")
    return j["Data"]


def fetch_ctbc(fund_id, day=None):
    # StartDate 是公告日；持股屬於回應中的「淨值日期」
    if day is None:
        today = dt.date.today()
        for i in range(-4, 10):  # 公告日可能在今天之後（下一個營業日）
            res = fetch_ctbc(fund_id, today - dt.timedelta(days=i))
            if res:
                return res
        return None
    if not hasattr(_local, "ctbc_token"):
        _local.ctbc_token = _ctbc("home/AuthToken")["token"]
    d = _ctbc("etf/Buyback", FID=fund_id, StartDate=day.isoformat())
    if not d.get("Data") or not d.get("Detail"):
        return None
    date = norm_date(d["Data"][0].get("淨值日期"))
    rows = []
    for sec in d["Detail"]:
        kind = _CTBC_KINDS.get(sec.get("Code"), "other")
        for x in sec.get("Data") or []:
            code = (x.get("code_") or "").strip()
            if kind == "future":
                code += re.sub(r"\D", "", x.get("ym_") or "")
            elif kind == "option":
                code = f"{code}{re.sub(r'\D', '', x.get('ym_') or '')}{(x.get('ename_') or '').strip()}"
            elif kind == "other":
                code = ""
            rows.append((kind, code, (x.get("name_") or "").strip(), num(x.get("qty_")),
                         num(x.get("weights_")), num(x.get("amount_"))))
    return (date, rows) if date and rows else None


# ---------------------------------------------------------------- 凱基
def fetch_kgi(fund_id, day=None):
    # queryDate 是公告日；持股屬於「(日期)每受益權單位淨資產價值」那一天
    r = _request("POST", "https://www.kgifund.com.tw/Fund/RedemptionVC",
                 data={"fundID": fund_id, "queryDate": day.strftime("%Y/%m/%d") if day else ""})
    if r.status_code == 500:  # 非營業日
        return None
    r.raise_for_status()
    text = html.unescape(r.text)
    m = re.search(r"\((\d{4}/\d{2}/\d{2})\)\s*每受益權單位淨資產價值", text)
    if not m:
        return None
    rows = []
    for body in re.findall(r"<table[^>]*>(.*?)</table>", text, re.S):
        cells = _cells(body)
        if not cells:
            continue
        head, data = cells[0], cells[1:]
        if "股票代號" in head:
            for c in data:
                rows.append(("stock", c[0], c[1], num(c[2]), num(c[3]), None))
        elif "期貨代號" in head:
            for c in data:
                ym = re.sub(r"\D", "", c[4]) if len(c) > 4 else ""
                rows.append(("future", f"{c[0]}{ym}", c[1], num(c[2]), num(c[3]), None))
    return (norm_date(m.group(1)), rows) if rows else None


# ---------------------------------------------------------------- 大華銀
def fetch_uob(fund_id, day=None):
    # pcfDate 是公告日；查無資料時會回最新一期，所以資料日一律用回應中的 datadate
    p = {"fundID": fund_id}
    if day:
        p["pcfDate"] = day.strftime("%Y/%m/%d")
    r = _request("GET", "https://www.uobam.com.tw/json/reply/WebSitePcfRequest", params=p,
                 headers={"Accept": "application/json"})
    r.raise_for_status()
    j = r.json()
    date = norm_date(j.get("datadate"))
    if day and norm_date(j.get("announce")) != day.isoformat():
        return None
    rows = []
    for x in j.get("result") or []:
        kind = x.get("kind")
        if kind == "stock":
            rows.append(("stock", x["code"].strip(), x["cName"], num(x["qty"]),
                         num(x["weight"]), num(x["amt"])))
        elif kind == "other":
            rows.append(("other", "", re.sub(r"\(TWD\)", "", x["cName"]).strip(), None, None,
                         num(x["amt"])))
        else:  # 期貨等其他類別（00918 測試期間未出現）
            rows.append(("future", (x.get("code") or "").strip(), x.get("cName") or "",
                         num(x.get("qty")), num(x.get("weight")), num(x.get("amt"))))
    return (date, rows) if date and rows else None


# ---------------------------------------------------------------- 野村
def fetch_nomura(fund_id, day=None):
    r = _request("POST", "https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets",
                 json={"FundID": fund_id, "SearchDate": day.isoformat() if day else None})
    r.raise_for_status()
    j = r.json()
    if j.get("StatusCode") != 0:
        return None
    d = j["Entries"]["Data"]
    date = norm_date((d.get("FundAsset") or {}).get("NavDate"))
    rows = []
    for t in d.get("Table") or []:
        title = t.get("TableTitle") or ""
        for c in t.get("Rows") or []:
            if title == "股票":
                rows.append(("stock", c[0].strip(), c[1], num(c[2]), num(c[3]), None))
            elif title == "期貨":
                rows.append(("future", c[0].strip(), c[1], num(c[2]), num(c[3]), None))
            elif title == "" and c[0] not in ("股票", "期貨"):
                rows.append(("other", "", c[0], None, None, num(c[-1])))
    return (date, rows) if date and rows else None


FETCHERS = {
    "yuanta": fetch_yuanta, "capital": fetch_capital, "cathay": fetch_cathay,
    "fubon": fetch_fubon, "uni": fetch_uni, "fuhhwa": fetch_fuhhwa, "ctbc": fetch_ctbc,
    "kgi": fetch_kgi, "uob": fetch_uob, "nomura": fetch_nomura,
}
