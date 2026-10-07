"""回補某個營收月份：先從觀測站彙總表拿全市場數字，再用 FinMind 慢慢補約略公告日。

    python backfill_revenue.py 2026-08

FinMind 免登入有每小時額度，額度用完會等 20 分鐘再試，直到該月份全部補完為止。
補到的公告日是 FinMind 的入庫日（下午三點後申報的會晚一天），網站上會標「約」。
"""

import datetime as dt
import sys
import time

import revenue


def main(month):
    rows = revenue.load()
    added = 0
    for market in revenue.MARKETS:
        for s in revenue.summary(month, market):
            key = (month, s["code"])
            if key not in rows:
                rows[key] = revenue.blank(month, s["code"], s["name"], market)
                added += 1
            rows[key].update(s, market=market)
    revenue.save(rows)
    print(f"{month} 彙總表：{sum(k[0] == month for k in rows)} 家（新增 {added}）", flush=True)

    while True:
        msg = revenue.backfill_finmind(month, limit=60)  # 小批次，縮短和每日排程同時寫檔的時間
        print(f"{dt.datetime.now():%H:%M} {msg}", flush=True)
        if "還有 0 家" in msg:
            break
        if "補了 0 家" in msg:  # 額度用完
            time.sleep(20 * 60)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1] if len(sys.argv) > 1 else revenue.prev_month(dt.date.today().replace(day=1) - dt.timedelta(days=1)))
