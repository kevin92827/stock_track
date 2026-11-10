"""庫藏股買回：公開資訊觀測站「庫藏股統計彙總表」（t35sc09），存到 data/buyback.csv。

一筆 = 一次董事會決議的買回計畫。執行結果（已買回股數、平均價格…）要等期限屆滿或執行完畢後才會填上，
所以每天重抓最近 150 天決議的資料來更新狀態。

    python buyback.py               每日更新（最近 150 天）
    python buyback.py --backfill    回補 2010 年以來全部
"""

import csv
import datetime as dt
import html
import os
import re
import sys

import mops

BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "data", "buyback.csv")
FIELDS = ["decide", "code", "name", "market", "purpose", "cap", "plan", "low", "high", "start", "end",
          "done", "bought", "retired", "ratio", "amount", "avg", "pct_issued", "reason"]
MARKETS = ("sii", "otc")
REDIRECT = "https://mops.twse.com.tw/mops/api/redirectToOld"


def roc(d):
    return f"{d.year - 1911}{d.month:02d}{d.day:02d}"


def ad(s):
    y, m, d = s.strip().split("/")
    return f"{int(y) + 1911}-{m}-{d}"


def num(s):
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def fetch(market, d1, d2):
    """某市場、某段董事會決議日期內的全部買回計畫"""
    params = {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "TYPEK": market,
              "d1": roc(d1), "d2": roc(d2), "RD": "1", "chBox": ""}
    j = mops.request("POST", REDIRECT, json={"apiName": "ajax_t35sc09", "parameters": params}).json()
    if j.get("code") != 200:
        raise RuntimeError(f"t35sc09 查詢失敗：{j.get('message')}")
    t = mops.request("GET", j["result"]["url"]).content.decode("utf-8", "replace")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        c = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(c) < 20 or not re.fullmatch(r"\d+/\d+/\d+", c[3]):
            continue
        f = lambda i: "" if num(c[i]) is None else (str(int(num(c[i]))) if num(c[i]).is_integer() else str(num(c[i])))
        rows.append({"decide": ad(c[3]), "code": c[1], "name": c[2], "market": market, "purpose": c[4],
                     "cap": f(5), "plan": f(6), "low": f(7), "high": f(8), "start": ad(c[9]), "end": ad(c[10]),
                     "done": c[11], "bought": f(13), "retired": f(14), "ratio": f(15), "amount": f(16),
                     "avg": f(17), "pct_issued": f(18), "reason": re.sub(r"\s+", " ", c[19])})
    return rows


def key(r):
    return (r["code"], r["decide"], r["start"])


def load():
    if not os.path.exists(PATH):
        return {}
    with open(PATH, encoding="utf-8-sig", newline="") as f:
        return {key(r): r for r in csv.DictReader(f)}


def save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(data.values(), key=lambda r: (r["decide"], r["code"])))
    os.replace(tmp, PATH)


def update(days=150, since=None):
    data = load()
    today = dt.date.today()
    spans = []
    if since:
        y = since
        while y <= today.year:
            spans.append((dt.date(y, 1, 1), min(dt.date(y, 12, 31), today)))
            y += 1
    else:
        spans.append((today - dt.timedelta(days=days), today))
    added = changed = 0
    for market in MARKETS:
        for d1, d2 in spans:
            for r in fetch(market, d1, d2):
                old = data.get(key(r))
                if old is None:
                    added += 1
                elif old != r:
                    changed += 1
                data[key(r)] = r
    save(data)
    return f"新增 {added} 筆、更新 {changed} 筆，共 {len(data)} 筆"


# ---------------------------------------------------------------- 網站用的統計
PX_PATH = os.path.join(BASE, "data", "prices.csv")
PX_HIST_PATH = os.path.join(BASE, "data", "buyback_px_hist.csv")  # prices.csv 以前的行情（由 predict_data 換算，只取需要的區段）
PREDICT_DIR = os.path.join(BASE, "predict_data")
VOL_DAYS = 20


def build_px_hist(rows, before):
    """predict_data 的 price_o（2023 年起）：市值(百萬) ÷ 流通股數(千股) 換回實際收盤價，成交量(千股) × 1000。
    只留每筆買回計畫決議日前 40 天到期限後 15 天的資料。"""
    import glob
    win = {}
    for r in rows:
        if r["decide"] >= before or r["decide"] < "2023-02-01":
            continue
        a = (dt.date.fromisoformat(r["decide"]) - dt.timedelta(days=40)).isoformat()
        b = (dt.date.fromisoformat(r["end"]) + dt.timedelta(days=15)).isoformat()
        win.setdefault(r["code"], []).append((a, b))
    out = []
    for path in sorted(glob.glob(os.path.join(PREDICT_DIR, "price_o_*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for c in csv.reader(f):
                if len(c) < 9 or c[0] not in win:
                    continue
                d = c[2].replace("/", "-")
                if d >= before or not any(a <= d <= b for a, b in win[c[0]]):
                    continue
                mcap, sh, v = num(c[8]), num(c[7]), num(c[4])
                if mcap and sh:
                    out.append((d, c[0], round(mcap * 1000 / sh, 2), int(v * 1000) if v is not None else ""))
    out.sort()
    with open(PX_HIST_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "code", "close", "volume"])
        w.writerows(out)
    print(f"已由 predict_data 換算庫藏股用的歷史行情：{len(out)} 筆")


def stats():
    """-> 網站用的清單，每筆：
    [決議日, 代號, 名稱, 市場, 目的, 預定股數, 價格下限, 價格上限, 期間起, 期間迄, 是否完畢,
     已買回股數, 執行率%, 買回總金額, 平均價格, 占已發行%, 未執行完畢原因,
     決議前 20 日均量(股), 期間交易日數, 決議日收盤, 公告後 5 個交易日漲跌, 期間漲跌（進行中算到最新）, 最新收盤]"""
    from compute_cutoff import HOLIDAYS
    data = load()
    rows = list(data.values())
    code_set = {r["code"] for r in rows}
    close, vol = {}, {}
    first = None
    if os.path.exists(PX_PATH):
        with open(PX_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                first = r["date"] if first is None or r["date"] < first else first
                if r["code"] in code_set and r["close"]:
                    close.setdefault(r["code"], {})[r["date"]] = float(r["close"])
                    if r.get("volume"):
                        vol.setdefault(r["code"], {})[r["date"]] = int(r["volume"])
    if first and not os.path.exists(PX_HIST_PATH) and os.path.isdir(PREDICT_DIR):
        build_px_hist(rows, first)
    if os.path.exists(PX_HIST_PATH):
        with open(PX_HIST_PATH, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if first and r["date"] >= first:
                    continue
                close.setdefault(r["code"], {})[r["date"]] = float(r["close"])
                if r["volume"]:
                    vol.setdefault(r["code"], {})[r["date"]] = int(r["volume"])
    known = sorted({d for p in close.values() for d in p})
    last_known = known[-1] if known else None

    known_set = set(known)

    def trading_days(a, b):
        """a～b 之間的交易日數：有行情的範圍用實際交易日，範圍以外用平日扣休市日估"""
        n, day, end = 0, dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        while day <= end:
            s_ = day.isoformat()
            if s_ in known_set or ((not known or s_ < known[0] or s_ > known[-1]) and day.weekday() < 5 and s_ not in HOLIDAYS):
                n += 1
            day += dt.timedelta(days=1)
        return n

    out = []
    for r in sorted(rows, key=lambda r: (r["decide"], r["code"])):
        p, v = close.get(r["code"], {}), vol.get(r["code"], {})
        ds = sorted(p)
        before = [d for d in ds if d <= r["decide"]]
        after = [d for d in ds if d > r["decide"]]
        c0 = p[before[-1]] if before and before[-1] >= (dt.date.fromisoformat(r["decide"]) - dt.timedelta(days=10)).isoformat() else None
        vs = [v[d] for d in before[-VOL_DAYS:] if d in v]
        vol20 = round(sum(vs) / len(vs)) if len(vs) >= 10 else None
        r5 = round(p[after[4]] / c0 - 1, 4) if c0 and len(after) >= 5 else None
        pre = [d for d in ds if d < r["start"]]
        upto = [d for d in ds if d <= r["end"]]
        rp = None
        if pre and upto and upto[-1] >= r["start"] and pre[-1] >= (dt.date.fromisoformat(r["start"]) - dt.timedelta(days=10)).isoformat():
            rp = round(p[upto[-1]] / p[pre[-1]] - 1, 4)
        f = lambda k: float(r[k]) if r[k] else None
        out.append([r["decide"], r["code"], r["name"], r["market"], r["purpose"], f("plan"), f("low"), f("high"),
                    r["start"], r["end"], r["done"] == "Y", f("bought"), f("ratio"), f("amount"), f("avg"), f("pct_issued"),
                    r["reason"] or None, vol20, trading_days(r["start"], r["end"]), c0, r5, rp,
                    p[ds[-1]] if ds and ds[-1] >= r["decide"] else None])
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(update(since=2010) if "--backfill" in sys.argv else update())
