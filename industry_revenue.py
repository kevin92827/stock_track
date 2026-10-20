"""產業地圖用的長期月營收，存在 data/industry_revenue.csv（全部上市櫃公司，近 MONTHS 個月）。

    python industry_revenue.py     補還沒有的月份（update.py 每天呼叫，平常只會補最新一個月）

來源和 revenue.py 一樣是公開資訊觀測站的營收彙總表 CSV（千元）；這裡只留數字，不管公告日。
當月還有公司沒申報時彙總表會缺那幾家，所以最近兩個月每次都重抓。
"""

import csv
import datetime as dt
import os
import sys

import revenue

BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "data", "industry_revenue.csv")
FIELDS = ["month", "code", "name", "revenue", "mom", "yoy"]
MONTHS = 37  # 三年再多一個月，算得出完整三年的年增率


def load():
    if not os.path.exists(PATH):
        return {}
    with open(PATH, encoding="utf-8-sig", newline="") as f:
        return {(r["month"], r["code"]): r for r in csv.DictReader(f)}


def save(rows):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    os.replace(tmp, PATH)


def months_back(n):
    t = dt.date.today().replace(day=1)
    out = []
    for _ in range(n):
        t = (t - dt.timedelta(days=1)).replace(day=1)
        out.append(t.strftime("%Y-%m"))
    return sorted(out)


def update():
    rows = load()
    want = months_back(MONTHS)
    have = {k[0] for k in rows}
    todo = [m for m in want if m not in have] + [m for m in want[-2:] if m in have]
    got = []
    for month in todo:
        n = 0
        for market in revenue.MARKETS:
            for x in revenue.summary(month, market):
                if x["revenue"]:
                    rows[(month, x["code"])] = {"month": month, "code": x["code"], "name": x["name"], "revenue": x["revenue"],
                                               "mom": x["mom"], "yoy": x["yoy"]}
                    n += 1
        if n:
            got.append(month)
        print(month, n, flush=True)
    rows = {k: v for k, v in rows.items() if k[0] >= want[0]}
    save(rows)
    return f"更新 {len(got)} 個月份（{', '.join(got)}）" if got else "沒有新的營收月份"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(update())
