"""上市櫃公司每月營收與公告日，存在 data/monthly_revenue.csv。

    python revenue.py              每日更新（update.py 會自動呼叫）
    python revenue.py --finmind    用 FinMind 補目前營收月份裡「公告日不明」的公司（約略日期）

資料來源：
  數字   公開資訊觀測站的營收彙總表 CSV（單位千元），含月增率、年增率。
  公告日 觀測站沒有提供歷史的逐公司公告日，只有「今天申報的公司」清單（t51sb10），所以要每天抓才累積得起來。
         date_source 記錄公告日怎麼來的：
           mops      當天從 t51sb10 抓到，有確切時間
           snapshot  昨天執行後才申報、今天才在彙總表出現，記為昨天（沒有時間）
           finmind   FinMind 的入庫日，約略值，下午三點後申報的會晚一天
           空白      不知道（例如第一次執行前就已申報）
"""

import csv
import datetime as dt
import io
import json
import os
import sys
import time

import requests

import mops

BASE = os.path.dirname(os.path.abspath(__file__))
REV_PATH = os.path.join(BASE, "data", "monthly_revenue.csv")
STATE_PATH = os.path.join(BASE, "data", "revenue_state.json")
FIELDS = ["revenue_month", "code", "name", "market", "industry", "revenue", "last_month", "last_year",
          "mom", "yoy", "cum_revenue", "cum_yoy", "announce_date", "announce_time", "date_source"]
MARKETS = ("sii", "otc")
TODAY_API = "https://mops.twse.com.tw/mops/api/home_page/t51sb10"
CSV_URL = "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{month}.csv"
FINMIND = "https://api.finmindtrade.com/api/v4/data"


def load():
    """-> {(revenue_month, code): row}"""
    if not os.path.exists(REV_PATH):
        return {}
    with open(REV_PATH, encoding="utf-8-sig", newline="") as f:
        return {(r["revenue_month"], r["code"]): r for r in csv.DictReader(f)}


def save(rows):
    os.makedirs(os.path.dirname(REV_PATH), exist_ok=True)
    tmp = REV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    os.replace(tmp, REV_PATH)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def blank(month, code, name, market):
    return dict.fromkeys(FIELDS, "") | {"revenue_month": month, "code": code, "name": name, "market": market}


def filed_today():
    """今天申報營收的公司 -> (今天日期, {(營收月份, 代號): (時間, 名稱, 市場)})"""
    out, today = {}, None
    for market in MARKETS:
        j = mops.request("POST", TODAY_API, json={"count": "0", "marketKind": market}).json()
        y, m, d = j["datetime"].split(" ")[0].split("/")
        today = f"{int(y) + 1911}-{m}-{d}"
        for x in (j.get("result") or {}).get("data") or []:
            ym, _, kind = x.get("subject", "").partition("-")
            if kind != "營業收入資訊" or not ym[:-2].isdigit():
                continue  # 同一張清單還有財報、各項產品營收統計表等
            month = f"{int(ym[:-2]) + 1911}-{ym[-2:]}"
            out[(month, str(x["companyId"]).strip())] = (x.get("time", ""), x.get("companyAbbreviation", "").strip(), market)
    return today, out


def summary(month, market):
    """某營收月份的彙總表 -> [dict]；該月份還沒有檔案時回空清單"""
    y, m = map(int, month.split("-"))
    try:
        r = mops.request("GET", CSV_URL.format(market=market, roc=y - 1911, month=m))
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise
    text = r.content.decode("utf-8-sig", errors="replace")
    if "公司代號" not in text[:500]:
        return []
    num = lambda s: s.strip().replace(",", "")
    rows = []
    for x in csv.DictReader(io.StringIO(text)):
        rows.append({"code": x["公司代號"].strip(), "name": x["公司名稱"].strip(), "industry": x["產業別"].strip(),
                     "revenue": num(x["營業收入-當月營收"]), "last_month": num(x["營業收入-上月營收"]),
                     "last_year": num(x["營業收入-去年當月營收"]), "mom": num(x["營業收入-上月比較增減(%)"]),
                     "yoy": num(x["營業收入-去年同月增減(%)"]), "cum_revenue": num(x["累計營業收入-當月累計營收"]),
                     "cum_yoy": num(x["累計營業收入-前期比較增減(%)"])})
    return rows


def prev_month(day):
    first = day.replace(day=1)
    last = first - dt.timedelta(days=1)
    return f"{last.year}-{last.month:02d}"


def update():
    """每日更新。回傳訊息字串。"""
    rows, state = load(), load_state()
    today_str, filed = filed_today()
    today = dt.date.fromisoformat(today_str)
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    ran_yesterday = state.get("last_run") in (yesterday, today_str)

    months = sorted({prev_month(today)} | {m for m, _ in filed})
    new_dates = 0
    for month in months:
        had_month = any(k[0] == month for k in rows)  # 這個營收月份以前抓過，才分得出誰是新出現的
        for market in MARKETS:
            for s in summary(month, market):
                key = (month, s["code"])
                is_new = key not in rows
                row = rows.setdefault(key, blank(month, s["code"], s["name"], market))
                row.update(s, market=market)
                # 昨天執行之後才申報的公司：今天才出現在彙總表，又不在今天的申報清單裡
                if is_new and key not in filed and had_month and ran_yesterday and not row["announce_date"]:
                    row.update(announce_date=yesterday, announce_time="", date_source="snapshot")
                    new_dates += 1
    for key, (tm, name, market) in filed.items():
        row = rows.setdefault(key, blank(key[0], key[1], name, market))  # 彙總表可能隔天才有它的數字
        if row["date_source"] != "mops":
            new_dates += 1
        row.update(announce_date=today_str, announce_time=tm, date_source="mops")
    save(rows)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_run": today_str}, f)
    month = prev_month(today)
    n = sum(k[0] == month for k in rows)
    dated = sum(k[0] == month and bool(r["announce_date"]) for k, r in rows.items())
    msg = f"{month} 營收 {n} 家（{dated} 家有公告日），今天申報 {len(filed)} 家"
    if dated < n:  # 還有公告日不明的（例如首次執行前就申報的），用 FinMind 補約略日期，額度不夠就下次再補
        try:
            msg += "；" + backfill_finmind(month, limit=250)
        except Exception as e:
            msg += f"；FinMind 補日期失敗（{type(e).__name__}），下次再試"
    return msg


def backfill_finmind(month=None, limit=280):
    """用 FinMind 的入庫日補公告日不明的公司（約略值）。免登入有額度，一次最多查 limit 家。"""
    rows = load()
    month = month or prev_month(dt.date.today())
    y, m = map(int, month.split("-"))
    start = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"  # FinMind 的 date 是營收月份的次月 1 日
    todo = [k for k, r in sorted(rows.items()) if k[0] == month and not r["announce_date"]][:limit]
    # 這些日子有觀測站的當日申報清單。FinMind 說某家是這天，但它不在清單裡，
    # 代表其實是前一天下午三點後申報、被 FinMind 記成隔天，所以往前修正一天。
    covered = {r["announce_date"] for r in rows.values() if r["date_source"] == "mops"}
    done = 0
    for _, code in todo:
        r = requests.get(FINMIND, params={"dataset": "TaiwanStockMonthRevenue", "data_id": code, "start_date": start},
                         timeout=30)
        if r.status_code != 200:
            print(f"FinMind 回應 {r.status_code}，停止（可能額度用完）")
            break
        for x in r.json().get("data") or []:
            if x.get("revenue_year") == y and x.get("revenue_month") == m and x.get("create_time"):
                date = str(x["create_time"])[:10]
                if date in covered:
                    date = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
                rows[(month, code)].update(announce_date=date, announce_time="", date_source="finmind")
                done += 1
        time.sleep(1.0)
    save(rows)
    left = sum(k[0] == month and not r["announce_date"] for k, r in rows.items())
    return f"補了 {done} 家約略公告日，還有 {left} 家不明"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--finmind" in sys.argv:
        print(backfill_finmind())
    else:
        print(update())
