"""主動式 ETF 持倉成本與停損預警：data/stoploss.json（build_site.py 會嵌進網站）。

持倉成本用先進先出（FIFO）從持股明細的每日股數變化推出來：
  股數增加 → 用當天收盤價買進一批；股數減少 → 從最早買的那批開始賣。
  剩下的批次加權平均就是持倉成本。除權（配股）、減資、分割造成的股數變化會照比例調整批次，不當成買賣。
停損假設：收盤價跌破持倉成本 −30%（即收盤 ≤ 成本 × 0.7）就會被停損。

    python stoploss.py
"""

import csv
import datetime as dt
import glob
import json
import os
from collections import defaultdict

from etfs import ETFS, ISSUER_NAMES

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
HOLD_PATHS = [os.path.join(DATA, "active_history.csv"), os.path.join(DATA, "holdings.csv")]
PX_PATH = os.path.join(DATA, "prices.csv")
PX_HIST_PATH = os.path.join(DATA, "active_px_hist.csv")   # prices.csv 開始以前的收盤價（由 predict_data 換算，只做一次）
CA_PATH = os.path.join(DATA, "corp_actions.csv")
PREDICT_DIR = os.path.join(BASE, "predict_data")
OUT_PATH = os.path.join(DATA, "stoploss.json")

STOP = -0.30       # 跌破成本 30% 停損
LIMIT = 0.10       # 漲跌幅限制：隔天一根跌停就會觸發的算「隔天可能停損」
VOL_DAYS = 20


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def load_holdings():
    """-> {etf: {date: {code: (qty, name)}}}，只取主動式的股票部位"""
    snaps = defaultdict(dict)
    for path in HOLD_PATHS:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r["kind"] != "主動" or r["type"] != "stock":
                    continue
                q = num(r["qty"])
                if q:
                    snaps[r["etf"]].setdefault(r["date"], {})[r["code"]] = (q, r["name"])
    return snaps


def build_px_hist(codes, before):
    """predict_data 的 price_o 是還原股價；市值(百萬) ÷ 流通股數(千股) 才是實際收盤價。"""
    rows = []
    for path in sorted(glob.glob(os.path.join(PREDICT_DIR, "price_o_*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.reader(f):
                if len(r) < 9 or r[0] not in codes:
                    continue
                d = r[2].replace("/", "-")
                if d >= before or d < "2025-01-01":
                    continue
                mcap, sh = num(r[8]), num(r[7])
                if mcap and sh:
                    rows.append((d, r[0], round(mcap * 1000 / sh, 2)))
    rows.sort()
    with open(PX_HIST_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "code", "close"])
        w.writerows(rows)
    print(f"已由 predict_data 換算 {before} 以前的收盤價：{len(rows)} 筆")


def load_prices(codes):
    """-> close {code: {date: 收盤}}, vol {code: {date: 成交股數}}, 交易日清單"""
    close, vol, days = defaultdict(dict), defaultdict(dict), set()
    first = None
    with open(PX_PATH, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            days.add(r["date"])
            first = r["date"] if first is None or r["date"] < first else first
            if r["code"] in codes:
                c, v = num(r["close"]), num(r.get("volume"))
                if c:
                    close[r["code"]][r["date"]] = c
                if v is not None:
                    vol[r["code"]][r["date"]] = v
    # 有新的股票（例如回補到更早的持股）就重新換算一次；試過的代號記在 _codes.json，查不到的不會每次重跑
    tried_path = PX_HIST_PATH.replace(".csv", "_codes.json")
    tried = set(json.load(open(tried_path, encoding="utf-8"))) if os.path.exists(tried_path) else set()
    if (not os.path.exists(PX_HIST_PATH) or not codes <= tried) and os.path.isdir(PREDICT_DIR):
        build_px_hist(codes, first)
        with open(tried_path, "w", encoding="utf-8") as f:
            json.dump(sorted(codes), f)
    if os.path.exists(PX_HIST_PATH):
        with open(PX_HIST_PATH, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r["date"] < first:
                    days.add(r["date"])
                    close[r["code"]].setdefault(r["date"], float(r["close"]))
    return close, vol, sorted(days)


def load_share_events():
    """會改變股數的權利事件：{code: [(除權日, 股數倍數)]}"""
    ev = defaultdict(list)
    if not os.path.exists(CA_PATH):
        return ev
    with open(CA_PATH, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            prev, ref = num(r["prev_close"]), num(r["ref_price"])
            if not prev or not ref or r["status"] != "final":
                continue
            if r["kind"] == "dividend" and not num(r["stock"]):
                continue
            if r["kind"] == "dividend" and r["label"] == "增資":
                continue
            m = (prev - (num(r["cash"]) or 0) - (num(r["refund"]) or 0)) / ref
            if abs(m - 1) > 0.01:
                ev[r["code"]].append((r["date"], m))
    return ev


class Book:
    """一檔 ETF 持有一檔股票的 FIFO 批次"""

    def __init__(self):
        self.lots = []   # [買進日, 股數, 單價]

    @property
    def qty(self):
        return sum(l[1] for l in self.lots)

    @property
    def cost(self):
        q = self.qty
        return sum(l[1] * l[2] for l in self.lots) / q if q else None

    def buy(self, date, q, price):
        self.lots.append([date, q, price])

    def sell(self, q):
        while q > 1e-9 and self.lots:
            take = min(q, self.lots[0][1])
            self.lots[0][1] -= take
            q -= take
            if self.lots[0][1] <= 1e-9:
                self.lots.pop(0)

    def rescale(self, m):
        for l in self.lots:
            l[1] *= m
            l[2] /= m


def main():
    snaps = load_holdings()
    codes = {c for s in snaps.values() for day in s.values() for c in day}
    close, vol, days = load_prices(codes)
    events = load_share_events()
    day_idx = {d: i for i, d in enumerate(days)}

    def px_on(code, date):
        """date 當天的收盤價，沒有成交就往回找最近一天"""
        p = close.get(code, {})
        if date in p:
            return p[date]
        i = day_idx.get(date)
        if i is None:
            i = max((j for j, d in enumerate(days) if d <= date), default=None)
            if i is None:
                return None
        for j in range(i, max(i - 15, -1), -1):
            if days[j] in p:
                return p[days[j]]
        return None

    asof = days[-1]
    etf_meta = {c: (n, ISSUER_NAMES[i]) for c, n, i, k, _ in ETFS}
    pos, hist_events, coverage, missing_px, used = [], [], {}, 0, set()
    names = {}

    for etf, by_date in snaps.items():
        if etf not in etf_meta:
            continue
        dates = sorted(by_date)
        coverage[etf] = [dates[0], dates[-1], len(dates)]
        books = defaultdict(Book)
        series = defaultdict(list)   # code -> [(date, qty, cost, close)]
        prev_date, prev = None, {}
        for d in dates:
            cur = by_date[d]
            for code in set(cur) | set(prev):
                q1 = cur[code][0] if code in cur else 0
                if code in cur:
                    names[code] = cur[code][1]
                b = books[code]
                q0 = b.qty
                if prev_date and q0 and q1:
                    # 近 60 天內有配股、減資、分割（新股常在除權後幾週才入帳），
                    # 而且這次股數變化符合倍數 → 照比例調整批次，不當成買賣
                    for ev in events.get(code, []):
                        ed, m = ev
                        if (etf, code, ed) in used or ed > d:
                            continue
                        if (dt.date.fromisoformat(d) - dt.date.fromisoformat(ed)).days <= 60 and abs(q1 / q0 / m - 1) < 0.03:
                            used.add((etf, code, ed))
                            b.rescale(m)
                            q0 = b.qty
                            break
                dq = q1 - q0
                if dq > 0.5:
                    p = px_on(code, d)
                    if p is None:
                        missing_px += 1
                        continue
                    b.buy(d, dq, p)
                elif dq < -0.5:
                    b.sell(-dq)
                if b.qty > 0.5:
                    c = px_on(code, d)
                    series[code].append((d, b.qty, b.cost, c))
                elif code in prev:
                    series[code].append((d, 0, None, px_on(code, d)))
            prev_date, prev = d, cur

        # 歷史驗證：第一次收盤跌破成本 −30% 的那天之後，持股有沒有減少
        cross = {}   # 目前這段跌破是從哪一天開始（還沒回升到 −25% 以上）
        for code, s in series.items():
            armed = True
            cross[code] = None
            for i, (d, q, cost, c) in enumerate(s):
                if not q or not cost or not c:
                    armed, cross[code] = True, None
                    continue
                loss = c / cost - 1
                if loss > STOP + 0.05:
                    armed, cross[code] = True, None
                if armed and loss <= STOP:
                    armed, cross[code] = False, d
                    after = [x[1] for x in s[i + 1:i + 6]]
                    chg = lambda k: (after[k - 1] / q - 1) if len(after) >= k else None
                    exit_day = next((s[j][0] for j in range(i + 1, min(i + 21, len(s))) if not s[j][1]), None)
                    hist_events.append([etf, code, d, round(loss, 4), round(q), round(cost, 2), c,
                                        *[None if chg(k) is None else round(chg(k), 4) for k in (1, 3, 5)], exit_day])

        # 目前部位
        last = dates[-1]
        for code, b in books.items():
            if b.qty < 0.5 or code not in by_date[last]:
                continue
            c = px_on(code, asof)
            cost = b.cost
            vs = [vol[code][x] for x in days[-VOL_DAYS:] if x in vol.get(code, {})]
            avg = sum(vs) / len(vs) if vs else None
            prev_c = px_on(code, days[-2]) if len(days) > 1 else None
            pos.append([etf, code, round(b.qty), round(cost, 3), c,
                        round(c / prev_c - 1, 4) if c and prev_c else None,
                        b.lots[0][0], round(avg) if avg else None, last,
                        # 最舊那一批的成本（FIFO 下一個要賣的）與加權成本的差異，給明細參考
                        round(b.lots[0][2], 2),
                        # 跌破停損價是從哪一天開始（以資料日收盤計）；今天才跌破就是 asof
                        (cross.get(code) or asof) if c and c / cost - 1 <= STOP else None])

    out = {
        "asof": asof, "recentFrom": days[-2], "stop": STOP, "limit": LIMIT, "volDays": VOL_DAYS,
        "built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "coverage": coverage,
        "names": names,
        "pos": pos,
        "events": sorted(hist_events, key=lambda e: e[2]),
    }
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT_PATH)
    recent = days[-2:]
    hit = [p for p in pos if p[10] and p[10] >= recent[0]]
    near = [p for p in pos if p[4] and STOP < p[4] / p[3] - 1 and p[4] * (1 - LIMIT) <= p[3] * (1 + STOP)]
    msg = (f"{asof}：{len(pos)} 個部位，已跌破 {len(hit)}、跌停就觸發 {len(near)}；"
           f"歷史跌破事件 {len(hist_events)} 次" + (f"；缺價格略過 {missing_px} 次" if missing_px else ""))
    print("停損預警已更新：" + msg)
    return msg


if __name__ == "__main__":
    main()
