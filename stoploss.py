"""主動式 ETF 持倉成本與停損預警：data/stoploss.json（build_site.py 會嵌進網站）。

持倉成本用先進先出（FIFO）從持股明細的每日股數變化推出來：
  股數增加 → 用當天收盤價買進一批；股數減少 → 從最早買的那批開始賣。
  剩下的批次加權平均就是持倉成本。除權（配股）、減資、分割造成的股數變化會照比例調整批次，不當成買賣。
停損門檻依各檔 ETF 過去跌破成本後有沒有減碼來判斷（見 THRESHOLDS 的說明），沒有明顯紀律的不列入預警。

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

LIMIT = 0.10       # 漲跌幅限制：隔天一根跌停就會觸發的算「隔天可能停損」
VOL_DAYS = 20
# 依各檔 ETF 的歷史行為判斷停損門檻：
#   跌破成本 -X% 之後 WIN 個資料日內，扣除申購買回後持股減少 >= CUT 的比例（樣本至少 MIN_N 次）
#   要 >= MIN_RATE，而且比平常（虧損不到 10% 的部位）高 MIN_LIFT 以上；取符合的最小 X。都不符合就是沒有明顯停損紀律。
THRESHOLDS = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
WIN, CUT = 10, 0.10
MIN_N, MIN_RATE, MIN_LIFT = 8, 0.70, 0.35


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
    pos, hist_events, coverage, rules, missing_px, used = [], [], {}, {}, 0, set()
    names = {}

    for etf, by_date in snaps.items():
        if etf not in etf_meta:
            continue
        dates = sorted(by_date)
        first = dates[0]
        coverage[etf] = [dates[0], dates[-1], len(dates)]
        # 每個資料日的申購買回倍數：共同持股股數比的中位數
        flow = {}
        for a, b_ in zip(dates, dates[1:]):
            r = sorted(by_date[b_][c][0] / by_date[a][c][0] for c in by_date[b_] if c in by_date[a] and by_date[a][c][0] > 0)
            flow[b_] = (r[len(r) // 2] if len(r) % 2 else (r[len(r) // 2 - 1] + r[len(r) // 2]) / 2) if r else 1
        books = defaultdict(Book)
        series = defaultdict(list)   # code -> [(第幾個資料日, date, qty, cost, close, 成本含首日持股)]
        prev = {}
        for k, d in enumerate(dates):
            cur = by_date[d]
            for code in set(cur) | set(prev):
                q1 = cur[code][0] if code in cur else 0
                if code in cur:
                    names[code] = cur[code][1]
                b = books[code]
                q0 = b.qty
                if k and q0 and q1:
                    # 近 60 天內有配股、減資、分割（新股常在除權後幾週才入帳），
                    # 而且這次股數變化符合倍數 → 照比例調整批次，不當成買賣
                    for ed, m in events.get(code, []):
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
                    series[code].append((k, d, b.qty, b.cost, px_on(code, d), b.lots[0][0] == first))
                elif code in prev:
                    series[code].append((k, d, 0, None, None, False))
            prev = cur

        def later_change(code, k, q):
            """第 k 個資料日之後 WIN 個資料日，扣除申購買回後的持股變化；還看不到就回傳 None"""
            if k + WIN > len(dates) - 1:
                return None
            f = 1
            for kk in range(k + 1, k + WIN + 1):
                f *= flow.get(dates[kk], 1)
            return by_date[dates[k + WIN]].get(code, (0,))[0] / q / f - 1

        def crossings(th):
            """每個部位第一次收盤跌破成本 -th（回升到 -th+5% 以上才重新計）-> [(code, i, s)]，依時間順序"""
            out = []
            for code, s_ in series.items():
                armed = True
                for i, (k, d, q, cost, c, init) in enumerate(s_):
                    if not q or not cost or not c:
                        armed = True
                        continue
                    loss = c / cost - 1
                    if loss > -th + 0.05:
                        armed = True
                    if armed and loss <= -th:
                        armed = False
                        out.append((code, i, s_))
            return out

        # 這檔 ETF 的歷史行為：各門檻跌破後 WIN 天內減碼 >= CUT 的比例，和平常（虧損不到 10%）的比例比較
        bn = bs = 0
        for code, s_ in series.items():
            for (k, d, q, cost, c, init) in s_:
                if q and cost and c and c / cost - 1 > -0.10:
                    ch = later_change(code, k, q)
                    if ch is not None:
                        bn += 1
                        bs += ch <= -CUT
        base = bs / bn if bn else 0
        table, chosen = {}, None
        for th in THRESHOLDS:
            n = sold = 0
            for code, i, s_ in crossings(th):
                k, d, q, cost, c, init = s_[i]
                ch = None if init else later_change(code, k, q)
                if ch is not None:
                    n += 1
                    sold += ch <= -CUT
            table[str(th)] = [n, sold]
            if chosen is None and n >= MIN_N and sold / n >= max(MIN_RATE, base + MIN_LIFT):
                chosen = th
        rules[etf] = {"th": chosen, "base": round(base, 3), "n": bn, "table": table}

        # 有紀律的 ETF：列出歷次跌破事件，並記下每個部位最近一次跌破的日子
        cross = {}
        if chosen:
            for code, i, s_ in crossings(chosen):
                k, d, q, cost, c, init = s_[i]
                ch = later_change(code, k, q)
                exit_day = next((s_[j][1] for j in range(i + 1, len(s_)) if s_[j][0] <= k + WIN and not s_[j][2]), None)
                hist_events.append([etf, code, d, round(c / cost - 1, 4), round(q), round(cost, 2), c,
                                    None if ch is None else round(ch, 4), exit_day, init])
                cross[code] = d

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
            since = None
            if chosen and c and c / cost - 1 <= -chosen:
                # 最後一個資料日也在這段跌破裡 -> 沿用跌破日；否則是資料日之後才跌破
                lk = series[code][-1]
                still = lk[3] and lk[4] and lk[4] / lk[3] - 1 <= -chosen + 0.05
                since = cross.get(code) if still and cross.get(code) else asof
            pos.append([etf, code, round(b.qty), round(cost, 3), c,
                        round(c / prev_c - 1, 4) if c and prev_c else None,
                        b.lots[0][0], round(avg) if avg else None, last,
                        round(b.lots[0][2], 2), since])

    out = {
        "asof": asof, "recentFrom": days[-2], "limit": LIMIT, "volDays": VOL_DAYS,
        "win": WIN, "cut": CUT, "minN": MIN_N, "minRate": MIN_RATE, "minLift": MIN_LIFT, "thresholds": THRESHOLDS,
        "built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "coverage": coverage,
        "rules": rules,
        "names": names,
        "pos": pos,
        "events": sorted(hist_events, key=lambda e: e[2]),
    }
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT_PATH)
    disc = {e: r["th"] for e, r in rules.items() if r["th"]}
    hit = [p for p in pos if p[10] and p[10] >= days[-2]]
    near = [p for p in pos if p[0] in disc and p[4] and not p[10] and p[4] * (1 - LIMIT) <= p[3] * (1 - disc[p[0]])]
    msg = (f"{asof}：有停損紀律的 ETF {len(disc)} 檔（" + "、".join(f"{e} -{round(t * 100)}%" for e, t in disc.items())
           + f"），新跌破 {len(hit)}、跌停就觸發 {len(near)}" + (f"；缺價格略過 {missing_px} 次" if missing_px else ""))
    print("停損預警已更新：" + msg)
    return msg


if __name__ == "__main__":
    main()
