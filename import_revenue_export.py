"""把資料庫匯出的月營收檔（monthly_revenue_export.csv）併入 data/monthly_revenue.csv。

    python import_revenue_export.py <匯出檔路徑> [起始營收月份 YYYY-MM]

匯出檔提供的是「營收發布日」與「歷史新高」資訊；營收數字、市場別、產業別仍以公開資訊觀測站的
彙總表為準（每個月份 2 次請求），確保和每天抓的資料一致。匯出檔裡最後一個月份的「歷史最高單月營收」
會寫進 data/revenue_high.json 當基準，之後的月份由 revenue.py 自己判斷是否創新高。
"""

import csv
import datetime as dt
import json
import os
import sys

import revenue

COLS = {"month": "年月", "date": "營收發布日", "high_flag": "創新高/低(歷史)",
        "high": "歷史最高單月營收(千元)", "high_month": "歷史最高單月營收-年月"}


def ym(s):
    return f"{s[:4]}-{s[4:6]}"


def main(path, start):
    export = {}  # (month, code) -> 匯出列
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            export[(ym(r[COLS["month"]]), r["stock_id"].strip())] = r
    months = sorted({m for m, _ in export if m >= start})
    last_month = max(m for m, _ in export)
    print(f"匯出檔：{len(export)} 列，匯入月份 {months[0]} ～ {months[-1]}")

    rows = revenue.load()
    added = dated = 0
    for month in months:
        for market in revenue.MARKETS:
            for s in revenue.summary(month, market):
                key = (month, s["code"])
                ex = export.get(key)
                if key not in rows:
                    rows[key] = revenue.blank(month, s["code"], s["name"], market)
                    added += 1
                row = rows[key]
                row.update(s, market=market)
                if ex and ex[COLS["date"]]:
                    row.update(announce_date=ex[COLS["date"]], announce_time="", date_source="export")
                    dated += 1
                # 歷史新高：用匯出檔的判斷。匯出檔的「歷史最高單月營收」是不含本月的之前最高值
                # （創新高那個月的這一欄仍是前一個高點），正好就是我們要顯示的「前高」。
                if ex:
                    row["record_high"] = "1" if ex[COLS["high_flag"]] == "H" else ""
                    row["prev_high"] = int(float(ex[COLS["high"]])) if ex[COLS["high"]] else ""
                    row["prev_high_month"] = ym(ex[COLS["high_month"]]) if ex[COLS["high_month"]] else ""
        print(f"  {month} 完成", flush=True)

    # 基準 = 截至最後一個月的歷史最高：若最後一個月本身創新高，就是它的營收；否則是它的「之前最高」
    highs = {}
    for (month, code), r in export.items():
        if month != last_month:
            continue
        rev = float(r["單月營收(千元)"]) if r["單月營收(千元)"] else None
        if r[COLS["high_flag"]] == "H" and rev is not None:
            highs[code] = {"high": rev, "month": month}
        elif r[COLS["high"]]:
            highs[code] = {"high": float(r[COLS["high"]]), "month": ym(r[COLS["high_month"]])}
    with open(revenue.HIGH_PATH, "w", encoding="utf-8") as f:
        json.dump({"asof": last_month, "highs": highs}, f, ensure_ascii=False)
    n = revenue.mark_record_highs(rows)
    revenue.save(rows)
    print(f"新增 {added} 筆、寫入 {dated} 個發布日；歷史新高基準 {len(highs)} 家（截至 {last_month}），"
          f"{last_month} 之後的月份標記了 {n} 筆新高")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    default_start = (dt.date.today().replace(day=1) - dt.timedelta(days=200)).strftime("%Y-%m")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else default_start)
