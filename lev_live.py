"""盤中（13:31）用台指期即時價格，估算今天槓桿／反向 ETF 尾盤要調整的台指期口數，寫到 data/lev_live.json 並重建網站。
排程：工作排程器「槓桿再平衡盤中」每個平日 13:31 執行 run_lev_live.bat（之後會推上 GitHub）。

    python lev_live.py
"""

import datetime as dt
import json
import os
import subprocess
import sys

import requests

import lev_pcf

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "lev_live.json")
QUOTE = "https://mis.taifex.com.tw/futures/api/getQuoteList"
MONTH_CODE = "ABCDEFGHIJKL"


def quotes():
    body = {"MarketType": "0", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "",
            "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}
    r = requests.post(QUOTE, json=body, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    return {q["SymbolID"]: q for q in r.json()["RtData"]["QuoteList"]}


def held_month():
    """00631L 目前持有的台指期月份（例如 TX202612 -> TXFL6-F）"""
    rows = [r for (d, e), r in sorted(lev_pcf.load().items()) if e == "00631L"]
    tx = [f[0] for f in json.loads(rows[-1]["futures"]) if f[0].startswith("TX")]
    ym = max(tx, key=lambda c: abs(next(f[1] for f in json.loads(rows[-1]["futures"]) if f[0] == c)))[2:]
    return f"TXF{MONTH_CODE[int(ym[4:6]) - 1]}{ym[3]}-F"


def main():
    now = dt.datetime.now()
    today = now.date().isoformat()
    if now.weekday() >= 5:
        return "週末，不執行"
    s = lev_pcf.stats()
    if s["asof"] >= today:
        return f"PCF 已經是 {s['asof']}，不需要盤中估算"
    q = quotes()
    sym = held_month()
    t = q.get(sym)
    if not t or t["CDate"] != today.replace("-", "") or not t["CLastPrice"]:
        return f"{sym} 今天沒有報價（休市？）"
    last, ref = float(t["CLastPrice"]), float(t["CRefPrice"])
    r = last / ref - 1
    funds, total, total_flow = {}, 0, 0
    for etf, f in s["funds"].items():
        a = f["last"]
        need = a["A"] * ((f["L"] - a["e"]) + a["e"] * (f["L"] - 1) * r) / (last * s["mult"])
        flow = f["hist"][-1][5] if f["hist"] else 0
        funds[etf] = {"need": round(need), "flow": flow, "e": a["e"], "A": a["A"], "base_P": a["P"]}
        total += need
        total_flow += flow
    spot = q.get("TXF-S") or {}
    out = {"date": today, "time": now.strftime("%H:%M:%S"), "qtime": t["CTime"], "base": s["asof"], "symbol": sym,
           "name": t["DispCName"], "last": last, "ref": ref, "r": round(r, 5),
           "ceil": float(t["CCeilPrice"]), "floor": float(t["CFloorPrice"]),
           "spot": float(spot["CLastPrice"]) if spot.get("CLastPrice") else None,
           "spot_r": float(spot["CDiffRate"]) / 100 if spot.get("CDiffRate") else None,
           "funds": funds, "total": round(total), "total_flow": total_flow}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    subprocess.run([sys.executable, os.path.join(BASE, "build_site.py")], cwd=BASE, check=True)
    return (f"{today} {out['time']} {out['name']} {last:,.0f}（{r:+.2%}）→ 尾盤需調整 {round(total):+,} 口"
            f"（{'、'.join(f'{k} {v["need"]:+,}' for k, v in funds.items())}），不含申購贖回")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(main())
