"""由 data/holdings.csv 產生監控網站 site/index.html（單一檔案，直接用瀏覽器開啟）。"""

import csv
import datetime as dt
import json
import os
import statistics

from etfs import ETFS, ISSUER_NAMES
from compute_cutoff import HOLIDAYS  # 行情還沒有的日子，用休市日清單判斷下一個交易日

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, "data", "holdings.csv")
RUNS_PATH = os.path.join(BASE, "data", "runs.json")
REBAL_PATH = os.path.join(BASE, "data", "rebalance.json")
CB_PATH = os.path.join(BASE, "data", "cb_announcements.csv")
REV_PATH = os.path.join(BASE, "data", "monthly_revenue.csv")
PX_PATH = os.path.join(BASE, "data", "prices.csv")
CA_PATH = os.path.join(BASE, "data", "corp_actions.csv")
MAX_CA_DAYS = 400  # 除權息減資日曆載入最近幾天（未來的預告全部載入）
MAX_REV_MONTHS = 8  # 網站載入最近幾個營收月份
TEMPLATE_PATH = os.path.join(BASE, "site_template.html")
OUT_PATH = os.path.join(BASE, "docs", "index.html")  # GitHub Pages 從 docs/ 資料夾發布
MAX_DATES = 140  # 網站只載入最近這麼多個交易日（約半年多），CSV 仍保留全部
MAX_RUNS = 30    # 監控面板顯示的執行紀錄筆數
SCHEDULE = "18:30"  # 排程時間，與 Windows 工作排程器的設定一致；面板用它判斷排程有沒有跑


def num(s):
    if s == "":
        return None
    v = float(s)
    return int(v) if v.is_integer() else v


def fund_navs(rows):
    """估計每檔 ETF 每天的淨資產（元）-> {(etf, date): nav}。

    官網不一定給淨資產，但「部位市值 ÷ 權重」就是淨資產，取各持股算出來的中位數。
    沒給市值的投信，用其他 ETF 同一天同一檔股票的市值 ÷ 股數當股價來換算。
    """
    price = {}
    for r in rows:
        qty, amount = num(r["qty"]), num(r["amount"])
        if r["type"] == "stock" and qty and amount:
            price[(r["date"], r["code"])] = amount / qty
    samples = {}
    for r in rows:
        qty, weight, amount = num(r["qty"]), num(r["weight"]), num(r["amount"])
        if r["type"] != "stock" or not qty or not weight or weight < 0.1:
            continue
        value = amount or qty * price.get((r["date"], r["code"]), 0)
        if value:
            samples.setdefault((r["etf"], r["date"]), []).append(value / (weight / 100))
    return {k: statistics.median(v) for k, v in samples.items()}


def main():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    dates = sorted({r["date"] for r in rows})[-MAX_DATES:]
    keep = set(dates)
    rows = [r for r in rows if r["date"] in keep]
    navs = fund_navs(rows)

    names, snaps = {}, {}
    for r in rows:
        snap = snaps.setdefault(r["etf"], {}).setdefault(r["date"], {"s": [], "f": [], "o": []})
        qty, weight, amount = num(r["qty"]), num(r["weight"]), num(r["amount"])
        if r["type"] == "stock":
            snap["s"].append([r["code"], qty, weight])
            # 各投信用的名稱不同（台積電／台灣積體電路製造），取最短的當顯示名稱
            if r["code"] not in names or len(r["name"]) < len(names[r["code"]]):
                names[r["code"]] = r["name"]
        elif r["type"] in ("future", "option"):
            snap["f"].append([r["code"], r["name"], qty, weight])
        else:
            snap["o"].append([r["name"], amount, weight])
    for (etf, date), nav in navs.items():
        snaps[etf][date]["n"] = round(nav / 1e8, 2)  # 億元

    try:
        with open(RUNS_PATH, encoding="utf-8") as f:
            runs = json.load(f)[-MAX_RUNS:]
    except (OSError, ValueError):
        runs = []
    try:  # 被動式 ETF 的指數定期審核時程（公告日、生效日）
        with open(REBAL_PATH, encoding="utf-8") as f:
            rebal = json.load(f)
    except (OSError, ValueError):
        rebal = []

    cb = []  # 可轉債代收價款公告：[日期, 時間, 代號, 名稱, 市場, 主旨, 承銷方式, 承銷方式依據]
    if os.path.exists(CB_PATH):
        with open(CB_PATH, encoding="utf-8-sig", newline="") as f:
            cb = [[r["date"], r["time"], r["code"], r["name"], r["market"], r["subject"],
                   r.get("method") or "未知", r.get("method_detail") or ""] for r in csv.DictReader(f)]
        cb.sort()

    # 每日行情 {date: {code: (close, pct, shares)}}，給營收日曆算當日漲跌幅與市值
    px = {}
    if os.path.exists(PX_PATH):
        with open(PX_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                px.setdefault(r["date"], {})[r["code"]] = (num(r["close"]), num(r["pct"]), num(r["shares"]))
    px_dates = sorted(px)
    # 除權息、減資恢復買賣這些日子交易所不算漲跌，改用官方參考價算當天漲跌幅
    if os.path.exists(CA_PATH):
        with open(CA_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                q = px.get(r["date"], {}).get(r["code"])
                if q and q[0] and r["ref_price"]:
                    px[r["date"]][r["code"]] = (q[0], round((q[0] / float(r["ref_price"]) - 1) * 100, 2), q[2])

    trading = set(px_dates) | set(dates)

    def next_trading_day(day):
        """day 之後第一個交易日；超出已知範圍就用下一個平日"""
        later = [d for d in px_dates if d > day]
        if later:
            return later[0]
        d = dt.date.fromisoformat(day) + dt.timedelta(days=1)
        while d.weekday() >= 5 or d.isoformat() in HOLIDAYS:
            d += dt.timedelta(days=1)
        return d.isoformat()

    def effective_day(day, time_):
        """市場能反應這則公告的第一個交易日：13:30 收盤前公告且當天有交易 → 當天；否則下一個交易日。
        沒有時間的（匯入的歷史資料）一律當作收盤後公告，因為實際上 96% 的公司都在收盤後申報。"""
        if time_ and time_ <= "13:30" and day in trading:
            return day
        return next_trading_day(day)

    def price_info(code, day, time_):
        """-> [生效日漲跌%, 生效日市值(億), 生效日, None]"""
        eff = effective_day(day, time_)
        q = px.get(eff, {}).get(code)
        pct = q[1] if q else None
        mcap = round(q[0] * q[2] / 1e8, 1) if q and q[0] and q[2] else None
        return [pct, mcap, eff, None]

    # 月營收：[營收月份, 代號, 名稱, 市場, 產業, 營收(千元), 月增%, 年增%, 累計年增%, 公告日, 時間, 公告日來源,
    #          生效日漲跌%, 生效日市值(億), 生效日(市場能反應的第一個交易日), 保留, 是否歷史新高, 之前最高單月營收(千元), 其月份]
    rev = []
    if os.path.exists(REV_PATH):
        with open(REV_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                pct = lambda s: round(float(s), 2) if s not in ("", None) else None
                rev.append([r["revenue_month"], r["code"], r["name"], r["market"], r["industry"],
                            int(float(r["revenue"])) if r["revenue"] else None, pct(r["mom"]), pct(r["yoy"]),
                            pct(r["cum_yoy"]), r["announce_date"], r["announce_time"], r["date_source"]]
                           + (price_info(r["code"], r["announce_date"], r["announce_time"]) if r["announce_date"] else [None] * 4)
                           + [r.get("record_high") == "1", int(float(r["prev_high"])) if r.get("prev_high") else None, r.get("prev_high_month") or None])
        keep_months = sorted({r[0] for r in rev})[-MAX_REV_MONTHS:]
        rev = [r for r in rev if r[0] in keep_months]

    def last_close(code, day):
        """day 之前最近一個交易日的收盤價（只往回找 10 個交易日，停牌太久的不估）"""
        for d in [x for x in px_dates if x < day][-10:][::-1]:
            q = px[d].get(code)
            if q and q[0]:
                return q[0]
        return None

    # 除權息、減資、面額變更、ETF 分割：[生效日, 代號, 名稱, 市場, 種類, 標籤, 前收盤, 參考價, 參考價調整%, 是否估算,
    #   現金股利, 每股配股, 換股率, 每股退還股款, 說明, 停止買賣日, 是否預告, 生效日收盤相對參考價%]
    ca = []
    if os.path.exists(CA_PATH):
        since = (dt.date.today() - dt.timedelta(days=MAX_CA_DAYS)).isoformat()
        with open(CA_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r["date"] < since:
                    continue
                prev, ref = num(r["prev_close"]), num(r["ref_price"])
                cash, stock, ratio, refund = (num(r[k]) for k in ("cash", "stock", "ratio", "refund"))
                est = False
                if not ref:  # 還沒有官方參考價：用最近的收盤價估一個
                    prev = prev or last_close(r["code"], r["stop_date"] or r["date"])
                    if prev and r["kind"] == "dividend" and (cash or stock) and not r["reason"]:
                        ref, est = (prev - (cash or 0)) / (1 + (stock or 0)), True
                    elif prev and r["kind"] != "dividend" and ratio:
                        ref, est = (prev - (refund or 0)) / ratio, True
                adj = round((ref / prev - 1) * 100, 2) if ref and prev else None
                q = px.get(r["date"], {}).get(r["code"])
                day_pct = round((q[0] / ref - 1) * 100, 2) if q and q[0] and ref and not est else None
                ca.append([r["date"], r["code"], r["name"], r["market"], r["kind"], r["label"], prev,
                           round(ref, 2) if ref else None, adj, est, cash, stock, ratio, refund, r["reason"],
                           r["stop_date"], r["status"] == "forecast", day_pct])

    data = {
        "built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "schedule": SCHEDULE,
        "dates": dates,
        "etfs": [{"code": c, "name": n, "issuer": ISSUER_NAMES[i], "kind": k}
                 for c, n, i, k, _ in ETFS],
        "names": names,
        "snaps": snaps,
        "runs": runs,
        "rebal": rebal,
        "cb": cb,
        "rev": rev,
        "ca": ca,
    }
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(template.replace("/*__DATA__*/null", payload))
    os.replace(tmp, OUT_PATH)
    print(f"網站已更新：{OUT_PATH}（{len(dates)} 個交易日）")


if __name__ == "__main__":
    main()
