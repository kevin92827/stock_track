"""產業地圖網頁 docs/industry.html：把 data/industry_map.json（人工整理的上下游關係）
接上月營收、股價、ETF 持股，嵌進 industry_template.html。

    python build_industry.py     （update.py 在 build_site 之後呼叫）
"""

import csv
import datetime as dt
import json
import os

from etfs import ETFS

BASE = os.path.dirname(os.path.abspath(__file__))
MAP_PATH = os.path.join(BASE, "data", "industry_map.json")
REV_PATH = os.path.join(BASE, "data", "industry_revenue.csv")
PX_PATH = os.path.join(BASE, "data", "prices.csv")
HOLD_PATH = os.path.join(BASE, "data", "holdings.csv")
TEMPLATE_PATH = os.path.join(BASE, "industry_template.html")
OUT_PATH = os.path.join(BASE, "docs", "industry.html")


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def main():
    with open(MAP_PATH, encoding="utf-8") as f:
        m = json.load(f)
    codes = {n["id"] for n in m["nodes"] if n.get("tw")}

    # 月營收：{代號: [[月份, 營收(千元), 年增%], ...]}
    rev = {}
    if os.path.exists(REV_PATH):
        with open(REV_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r["code"] in codes:
                    rev.setdefault(r["code"], []).append([r["month"], int(float(r["revenue"])), num(r["yoy"])])
    for v in rev.values():
        v.sort()

    # 股價：{代號: {"d": [日期], "c": [收盤]}, "shares": 發行股數}
    px = {}
    if os.path.exists(PX_PATH):
        with open(PX_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r["code"] in codes and r["close"]:
                    p = px.setdefault(r["code"], {"d": [], "c": [], "pct": [], "shares": None})
                    p["d"].append(r["date"])
                    p["c"].append(num(r["close"]))
                    p["pct"].append(num(r["pct"]))
                    p["shares"] = num(r["shares"]) or p["shares"]
    for p in px.values():
        order = sorted(range(len(p["d"])), key=p["d"].__getitem__)
        for k in ("d", "c", "pct"):
            p[k] = [p[k][i] for i in order]

    # ETF 持股：每檔 ETF 最新一天，持有這些公司的部位
    etf_name = {c: n for c, n, *_ in ETFS}
    latest, held = {}, {}
    if os.path.exists(HOLD_PATH):
        with open(HOLD_PATH, encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["type"] == "stock"]
        for r in rows:
            latest[r["etf"]] = max(latest.get(r["etf"], ""), r["date"])
        for r in rows:
            if r["code"] in codes and r["date"] == latest[r["etf"]]:
                held.setdefault(r["code"], []).append([r["etf"], etf_name.get(r["etf"], r["etf_name"]), num(r["weight"]), num(r["qty"]), r["date"]])
    for v in held.values():
        v.sort(key=lambda x: -(x[2] or 0))

    data = {"built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "map": m, "rev": rev, "px": px, "held": held}
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(template.replace("/*__DATA__*/null", payload))
    os.replace(tmp, OUT_PATH)
    print(f"產業地圖已更新：{OUT_PATH}（{len(m['chains'])} 條產業鏈、{len(m['nodes'])} 家公司、{len(m['edges'])} 條關係）")


if __name__ == "__main__":
    main()
