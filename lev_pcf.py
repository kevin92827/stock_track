"""元大槓桿／反向 ETF 每日 PCF（基金規模、流通單位、股票與期貨部位），存到 data/lev_pcf.csv。
用來推算每天收盤的實際曝險比例，以及隔天尾盤需要再平衡的台指期口數。

    python lev_pcf.py               補到今天（從檔案最後一天往後）
    python lev_pcf.py 2020-01-01    從指定日期回補
"""

import csv
import datetime as dt
import json
import os
import sys

import sources

BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "data", "lev_pcf.csv")
FUNDS = ["00631L", "00632R"]
FIELDS = ["date", "etf", "totalav", "osunit", "nav", "stkvalues", "futvalues", "futures", "stocks"]
API = "https://etfapi.yuantaetfs.com/ectranslation/api/bridge"


def fetch(fund, day):
    j = sources._request("GET", API, params={"APIType": "ETFAPI", "FuncId": "PCF/Daily", "ticker": fund,
                                             "ndate": day.strftime("%Y%m%d")}).json()
    p, fw = j.get("PCF") or {}, j.get("FundWeights") or {}
    s = fw.get("Summary") or {}
    # 非交易日也會回應，但股票、期貨市值是 0 或 trandate 不是查的那天
    if p.get("trandate") != day.strftime("%Y%m%d") or not (s.get("stkvalues") or s.get("futvalues")):
        return None
    return {"date": day.isoformat(), "etf": fund, "totalav": p.get("totalav"), "osunit": p.get("osunit"),
            "nav": p.get("nav"), "stkvalues": s.get("stkvalues"), "futvalues": s.get("futvalues"),
            "futures": json.dumps([[f"{x['code'].strip()}{x.get('ym') or ''}", x["qty"], x["weights"]]
                                   for x in fw.get("FutureWeights") or []]),
            "stocks": json.dumps([[x["code"].strip(), x["qty"], x["weights"]] for x in fw.get("StockWeights") or []])}


def load():
    if not os.path.exists(PATH):
        return {}
    with open(PATH, encoding="utf-8", newline="") as f:
        return {(r["date"], r["etf"]): r for r in csv.DictReader(f)}


def save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(data[k] for k in sorted(data))
    os.replace(tmp, PATH)


def update(since=None):
    data = load()
    have = {k[0] for k in data}
    start = dt.date.fromisoformat(since) if since else (
        dt.date.fromisoformat(max(have)) + dt.timedelta(days=1) if have else dt.date(2020, 1, 2))
    day, added = start, 0
    while day <= dt.date.today():
        if day.weekday() < 5 and day.isoformat() not in have:
            for fund in FUNDS:
                r = fetch(fund, day)
                if r:
                    data[(r["date"], fund)] = r
                    added += 1
            if added and day.day == 1:
                save(data)
                print(day, len(data), flush=True)
        day += dt.timedelta(days=1)
    save(data)
    return f"槓桿 ETF PCF：新增 {added} 筆，共 {len(data)} 筆"


# ---------------------------------------------------------------- 網站用
LEVER = {"00631L": 2, "00632R": -1}
TX_MULT = 200   # 大台每點 200 元


def _parse(r):
    """一天的部位 -> 規模、總曝險（反向為負）、曝險比例、台指期結算價（由權重回推）、台指期口數、流通單位、淨值"""
    L = LEVER[r["etf"]]
    A = float(r["totalav"])
    tx = [f for f in json.loads(r["futures"]) if f[0].startswith("TX")]
    n_tx = sum(f[1] for f in tx)
    w_tx = sum(abs(f[2]) for f in tx)
    X = (1 if L > 0 else -1) * (float(r["stkvalues"] or 0) + float(r["futvalues"] or 0))
    return {"date": r["date"], "A": A, "X": X, "e": X / A, "n_tx": n_tx,
            "P": (w_tx / 100 * A) / (abs(n_tx) * TX_MULT) if n_tx else None,
            "units": float(r["osunit"]), "nav": float(r["nav"])}


def stats(days=120):
    """-> {asof, mult, funds: {etf: {L, last: {...}, hist: [[日期, 台指期漲跌, 前一天曝險, 收盤曝險,
         再平衡需求口數, 申購贖回口數, 實際調整口數, 台指期口數增減], ...]}}}
    再平衡需求 = A·[(L − e0) + e0·(L − 1)·r] ÷ (台指期價格 × 200)，申購贖回 = L·F ÷ (台指期價格 × 200)"""
    rows = {}
    for (d, etf), r in sorted(load().items()):
        if etf in LEVER:
            rows.setdefault(etf, []).append(_parse(r))
    out = {"mult": TX_MULT, "funds": {}}
    for etf, ds in rows.items():
        L = LEVER[etf]
        hist = []
        for a, b in zip(ds, ds[1:]):
            # 中間缺日（例如只補了部分區間）就不算
            if not a["P"] or not b["P"] or (dt.date.fromisoformat(b["date"]) - dt.date.fromisoformat(a["date"])).days > 6:
                continue
            r = b["P"] / a["P"] - 1
            unit = b["P"] * TX_MULT
            reb = a["A"] * ((L - a["e"]) + a["e"] * (L - 1) * r)
            flow = L * (b["units"] - a["units"]) * b["nav"]
            act = b["X"] - a["X"] * (1 + r)
            hist.append([b["date"], round(r, 5), round(a["e"], 4), round(b["e"], 4), round(reb / unit),
                         round(flow / unit), round(act / unit), round(b["n_tx"] - a["n_tx"])])
        last = ds[-1]
        out["funds"][etf] = {"L": L, "hist": hist[-days:],
                             "last": {"date": last["date"], "A": last["A"], "e": round(last["e"], 5),
                                      "P": round(last["P"], 2) if last["P"] else None, "n_tx": last["n_tx"]}}
        out["asof"] = max(out.get("asof", ""), last["date"])
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(update(sys.argv[1] if len(sys.argv) > 1 else None))
