"""回補主動式 ETF 在 holdings.csv 開始之前（2026-04-07 以前）的每日持股，存到 data/active_history.csv。
持倉成本（stoploss.py）要從上市第一天算起，才能用先進先出推出成本。

    python backfill_active.py          從 holdings.csv 第一天往回抓，連續 30 個平日查無資料就當作上市前
已經抓過的日子會跳過，中斷後重跑會接著抓。
"""

import csv
import datetime as dt
import os
from concurrent.futures import ThreadPoolExecutor

from etfs import ETFS
from sources import FETCHERS
from update import FIELDS, to_rows

BASE = os.path.dirname(os.path.abspath(__file__))
HOLD_PATH = os.path.join(BASE, "data", "holdings.csv")
OUT_PATH = os.path.join(BASE, "data", "active_history.csv")
STOP_AFTER = 30


def first_dates():
    first = {}
    with open(HOLD_PATH, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["kind"] == "主動" and (r["etf"] not in first or r["date"] < first[r["etf"]]):
                first[r["etf"]] = r["date"]
    return first


def load_done():
    done = set()
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                done.add((r["etf"], r["date"]))
    return done


def walk(etf, start, done, write):
    """從 start 前一天往回抓，直到連續 STOP_AFTER 個平日查無資料。"""
    fetch, day, misses, got = FETCHERS[etf[2]], dt.date.fromisoformat(start), 0, 0
    while misses < STOP_AFTER:
        day -= dt.timedelta(days=1)
        if day.weekday() >= 5:
            continue
        try:
            res = fetch(etf[4], day)
        except Exception as e:
            print(f"{etf[0]} {day} 錯誤：{e}"[:120], flush=True)
            res = None
        if not res or not any(r[0] == "stock" for r in res[1]):
            misses += 1
            continue
        misses = 0
        # 有些官網查 X 日回傳的是 X 前一個交易日的持股，所以每個平日都要查，重複的不寫
        date, rows = res
        if date >= start or (etf[0], date) in done:
            continue
        done.add((etf[0], date))
        write(to_rows(etf, date, rows))
        got += 1
    print(f"{etf[0]} 完成：新增 {got} 天，最早 {min((d for e, d in done if e == etf[0]), default='-')}", flush=True)


def main():
    first, done = first_dates(), load_done()
    new = not os.path.exists(OUT_PATH)
    f = open(OUT_PATH, "a", encoding="utf-8-sig" if new else "utf-8", newline="")
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if new:
        w.writeheader()

    import threading
    lock = threading.Lock()

    def write(rows):
        with lock:
            w.writerows(rows)
            f.flush()

    by_issuer = {}
    for e in ETFS:
        if e[3] == "主動" and e[0] in first:
            by_issuer.setdefault(e[2], []).append(e)
    with ThreadPoolExecutor(len(by_issuer)) as pool:
        for fut in [pool.submit(lambda g: [walk(e, first[e[0]], done, write) for e in g], g) for g in by_issuer.values()]:
            fut.result()
    f.close()


if __name__ == "__main__":
    main()
