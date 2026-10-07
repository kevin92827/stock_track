"""由 data/holdings.csv 產生監控網站 site/index.html（單一檔案，直接用瀏覽器開啟）。"""

import csv
import datetime as dt
import json
import os
import statistics

from etfs import ETFS, ISSUER_NAMES

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, "data", "holdings.csv")
RUNS_PATH = os.path.join(BASE, "data", "runs.json")
REBAL_PATH = os.path.join(BASE, "data", "rebalance.json")
CB_PATH = os.path.join(BASE, "data", "cb_announcements.csv")
REV_PATH = os.path.join(BASE, "data", "monthly_revenue.csv")
PX_PATH = os.path.join(BASE, "data", "prices.csv")
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

    cb = []  # 可轉債代收價款公告：[日期, 時間, 代號, 名稱, 市場, 主旨]
    if os.path.exists(CB_PATH):
        with open(CB_PATH, encoding="utf-8-sig", newline="") as f:
            cb = [[r["date"], r["time"], r["code"], r["name"], r["market"], r["subject"]] for r in csv.DictReader(f)]
        cb.sort()

    # 每日行情 {date: {code: (close, pct, shares)}}，給營收日曆算當日漲跌幅與市值
    px = {}
    if os.path.exists(PX_PATH):
        with open(PX_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                px.setdefault(r["date"], {})[r["code"]] = (num(r["close"]), num(r["pct"]), num(r["shares"]))
    px_dates = sorted(px)

    def price_info(code, day, time_):
        """-> [公告日漲跌%, 市值(億), 次一交易日漲跌%, 次一交易日]；收盤後公告的才附次一交易日"""
        q = px.get(day, {}).get(code)
        pct = q[1] if q else None
        mcap = round(q[0] * q[2] / 1e8, 1) if q and q[0] and q[2] else None
        nxt_pct = nxt = None
        if time_ and time_ > "13:30":
            later = [d for d in px_dates if d > day]
            if later:
                nq = px.get(later[0], {}).get(code)
                if nq:
                    nxt, nxt_pct = later[0], nq[1]
        return [pct, mcap, nxt_pct, nxt]

    # 月營收：[營收月份, 代號, 名稱, 市場, 產業, 營收(千元), 月增%, 年增%, 累計年增%, 公告日, 時間, 公告日來源,
    #          公告日漲跌%, 市值(億), 次一交易日漲跌%, 次一交易日]
    rev = []
    if os.path.exists(REV_PATH):
        with open(REV_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                pct = lambda s: round(float(s), 2) if s not in ("", None) else None
                rev.append([r["revenue_month"], r["code"], r["name"], r["market"], r["industry"],
                            int(float(r["revenue"])) if r["revenue"] else None, pct(r["mom"]), pct(r["yoy"]),
                            pct(r["cum_yoy"]), r["announce_date"], r["announce_time"], r["date_source"]]
                           + (price_info(r["code"], r["announce_date"], r["announce_time"]) if r["announce_date"] else [None] * 4))
        keep_months = sorted({r[0] for r in rev})[-MAX_REV_MONTHS:]
        rev = [r for r in rev if r[0] in keep_months]

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
