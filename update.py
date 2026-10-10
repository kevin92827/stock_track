"""抓取 ETF 持股並更新 data/holdings.csv，完成後重建監控網站。

    python update.py                抓每檔最新一期（每日執行用）
    python update.py --backfill 30  另外回補最近 30 天
    python update.py --only 0050 00981A

每次執行的結果會記在 data/runs.json，監控面板的燈號就是讀這個檔。
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

from etfs import ETFS, ISSUER_NAMES
from sources import FETCHERS

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, "data", "holdings.csv")
RUNS_PATH = os.path.join(BASE, "data", "runs.json")
LOG_PATH = os.path.join(BASE, "data", "update_log.txt")
FIELDS = ["date", "etf", "etf_name", "issuer", "kind", "type", "code", "name",
          "qty", "weight", "amount"]
TYPE_ORDER = {"stock": 0, "future": 1, "option": 2, "other": 3}
MAX_RUNS = 120


def fmt(v):
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else repr(round(v, 6))


def load():
    """-> {(date, etf): [row dict]}"""
    data = {}
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                data.setdefault((row["date"], row["etf"]), []).append(row)
    return data


def save(data):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    order = {e[0]: i for i, e in enumerate(ETFS)}
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for key in sorted(data, key=lambda k: (k[0], order.get(k[1], 999))):
            w.writerows(data[key])
    os.replace(tmp, CSV_PATH)


def load_runs():
    try:
        with open(RUNS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_runs(runs):
    os.makedirs(os.path.dirname(RUNS_PATH), exist_ok=True)
    tmp = RUNS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(runs[-MAX_RUNS:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, RUNS_PATH)


def to_rows(etf, date, rows):
    code, name, issuer, kind, _ = etf
    # 有些官網會把已出清的股票以 0 股列出，不算持股
    rows = [r for r in rows if not (r[0] == "stock" and not r[3])]
    rows = sorted(rows, key=lambda r: (TYPE_ORDER[r[0]], -(r[4] or 0), r[1]))
    return [{"date": date, "etf": code, "etf_name": name, "issuer": ISSUER_NAMES[issuer],
             "kind": kind, "type": r[0], "code": r[1], "name": r[2],
             "qty": "" if r[0] == "other" else fmt(r[3]),
             "weight": fmt(r[4]), "amount": fmt(r[5])} for r in rows]


def stock_count(rows):
    return sum(r["type"] == "stock" for r in rows)


def run_issuer(issuer, etfs, days):
    """同一家投信的請求在同一條執行緒內依序送出，由 sources 控制間隔。

    回傳 [(etf, day, 結果或 None, 錯誤訊息或 None)]，day 為 None 表示最新一期。
    """
    fetch = FETCHERS[issuer]
    out = []
    for etf in etfs:
        for day in days:
            try:
                out.append((etf, day, fetch(etf[4], day), None))
            except Exception as e:
                out.append((etf, day, None, f"{type(e).__name__}: {e}"[:200]))
                if os.environ.get("ETF_DEBUG"):
                    traceback.print_exc()
    return out


def fetch_and_store(args, run):
    today = dt.date.today()
    days = [today - dt.timedelta(days=i) for i in range(args.backfill, -1, -1)] if args.backfill else []
    days += [dt.date.fromisoformat(d) for d in args.dates]
    days = [d for d in days if d.weekday() < 5] + [None]  # None = 最新一期

    etfs = [e for e in ETFS if not args.only or e[0] in args.only]
    by_issuer = {}
    for e in etfs:
        by_issuer.setdefault(e[2], []).append(e)
    with ThreadPoolExecutor(len(by_issuer)) as pool:
        futures = [pool.submit(run_issuer, issuer, group, days) for issuer, group in by_issuer.items()]
        outcomes = [x for f in futures for x in f.result()]

    data = load()
    for key in [k for k in data if k[0] in args.drop and (not args.only or k[1] in args.only)]:
        del data[key]

    added, problems = set(), []
    status = {e[0]: {"ok": False, "date": None, "stocks": None, "msg": "沒有執行"} for e in etfs}
    for etf, day, res, err in sorted(outcomes, key=lambda o: (o[1] is None, str(o[1]))):
        code, label = etf[0], day or "最新"
        msg = err
        if not err and res:
            date, rows = res
            new_rows = to_rows(etf, date, rows)
            # 不檢查股票檔數的增減：指數調整期間會先納入新成分股、舊的還沒賣完，檔數大幅變動是正常的
            if not any(r[0] in ("stock", "future") for r in rows):
                msg = "回傳內容沒有股票或期貨部位"
            else:
                if (date, code) not in data:
                    added.add((date, code))
                data[(date, code)] = new_rows
                if day is None:
                    status[code] = {"ok": True, "date": date, "stocks": stock_count(new_rows), "msg": ""}
        elif not err and day is None:
            msg = "官網查無資料"
        if msg:
            problems.append(f"{code} {label}: {msg}")
            if day is None:
                status[code]["msg"] = msg

    newest = max((s["date"] for s in status.values() if s["date"]), default=None)
    for code, s in status.items():
        if s["ok"] and s["date"] != newest:
            s.update(ok=False, msg=f"資料日落後（最新為 {newest}）")
            problems.append(f"{code} 最新: {s['msg']}")
    failed = [c for c, s in status.items() if not s["ok"]]
    run["etfs"], run["newest"], run["problems"] = status, newest, problems
    run["steps"]["fetch"] = {"ok": not failed, "msg": f"{len(status) - len(failed)} / {len(status)} 檔成功"}

    try:
        save(data)
        run["steps"]["csv"] = {"ok": True, "msg": f"新增 {len(added)} 筆快照，共 {len(data)} 筆"}
    except Exception as e:
        run["steps"]["csv"] = {"ok": False, "msg": f"{type(e).__name__}: {e}"[:200]}
        raise

    print(f"新增 {len(added)} 筆快照，目前共 {len(data)} 筆；最新資料日 {newest}")
    for e in etfs:
        s = status[e[0]]
        print(f"  {'OK  ' if s['ok'] else 'FAIL'} {e[0]:<7}{e[1]:<12} {s['date'] or '無資料'}  "
              f"股票 {s['stocks'] if s['stocks'] is not None else '-'} 檔  {s['msg']}")
    if problems:
        print(f"\n{len(problems)} 個問題：")
        for p in problems:
            print("  " + p)


def extra_steps(run):
    """持股以外的每日資料：可轉債公告、月營收。各自獨立，一個失敗不影響其他步驟。"""
    today = dt.date.today()
    steps = run["steps"]
    site = steps.pop("site")  # 讓網站建置維持在最後一步

    try:
        import mops
        # 清單偶爾隔天才補齊，所以連前兩天一起重抓
        added, total = mops.update_cb([today - dt.timedelta(days=i) for i in (2, 1, 0)])
        steps["cb"] = {"ok": True, "msg": f"新增 {added} 筆，共 {total} 筆"}
        try:
            import cb_method
            steps["cb"]["msg"] += "；" + cb_method.update()
        except Exception as e:  # 承銷方式查不到不算抓取失敗
            steps["cb"]["msg"] += f"；承銷方式比對失敗（{type(e).__name__}）"
    except Exception as e:
        steps["cb"] = {"ok": False, "msg": f"{type(e).__name__}: {e}"[:200]}
        run["problems"].append(f"可轉債公告: {steps['cb']['msg']}")
    print(f"可轉債公告：{steps['cb']['msg']}")

    try:
        import revenue
        steps["revenue"] = {"ok": True, "msg": revenue.update()}
    except Exception as e:
        steps["revenue"] = {"ok": False, "msg": f"{type(e).__name__}: {e}"[:200]}
        run["problems"].append(f"月營收: {steps['revenue']['msg']}")
    print(f"月營收：{steps['revenue']['msg']}")

    try:
        import prices
        steps["prices"] = {"ok": True, "msg": prices.update(prices.needed_days())}
    except Exception as e:
        steps["prices"] = {"ok": False, "msg": f"{type(e).__name__}: {e}"[:200]}
        run["problems"].append(f"每日行情: {steps['prices']['msg']}")
    print(f"每日行情：{steps['prices']['msg']}")

    steps["site"] = site


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, metavar="DAYS")
    ap.add_argument("--dates", nargs="*", default=[], metavar="YYYY-MM-DD", help="補抓指定日期")
    ap.add_argument("--drop", nargs="*", default=[], metavar="YYYY-MM-DD", help="刪除指定資料日的快照")
    ap.add_argument("--only", nargs="*", metavar="ETF")
    ap.add_argument("--no-site", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    pending = {"ok": False, "msg": "未執行"}
    run = {"time": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           # 手動補抓或只抓部分 ETF 的執行不算每日排程，監控面板不拿它來判斷燈號
           "partial": bool(args.only or args.dates or args.backfill or args.drop),
           "steps": {"fetch": dict(pending), "csv": dict(pending), "site": dict(pending)},
           "etfs": {}, "newest": None, "problems": []}
    runs = load_runs() + [run]
    try:
        fetch_and_store(args, run)
    except Exception as e:
        traceback.print_exc()
        run["problems"].append(f"執行中斷: {type(e).__name__}: {e}"[:300])
        if run["steps"]["fetch"]["msg"] == "未執行":
            run["steps"]["fetch"]["msg"] = f"執行中斷: {type(e).__name__}"

    if not run["partial"]:
        extra_steps(run)

    if args.no_site:
        run["steps"]["site"] = {"ok": True, "msg": "本次略過"}
        save_runs(runs)
    else:
        # 網站內嵌 runs.json，所以先當作成功寫入再建置；建置失敗才改回失敗
        run["steps"]["site"] = {"ok": True, "msg": "建置完成"}
        save_runs(runs)
        try:
            import build_site
            build_site.main()
        except Exception as e:
            traceback.print_exc()
            run["steps"]["site"] = {"ok": False, "msg": f"{type(e).__name__}: {e}"[:200]}
            run["problems"].append(f"網站建置失敗: {type(e).__name__}: {e}"[:300])
            save_runs(runs)

    ok = all(s["ok"] for s in run["steps"].values())
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{run['time']} {'OK' if ok else 'FAIL'} 最新 {run['newest']}，問題 {len(run['problems'])}\n")
        for p in run["problems"]:
            f.write(f"    {p}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
