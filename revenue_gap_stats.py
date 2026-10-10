"""營收公告生效日的「開盤漲幅」對「日內漲跌幅」統計，輸出 data/revenue_gap_stats.json 給網站的「營收統計」頁。

    python revenue_gap_stats.py

資料：
  公告日   monthly_revenue_export.csv（資料庫匯出檔，2015 年起每家公司每月的營收發布日）
  價格     punish0423 資料夾裡的 price_YYYY.csv（還原權息後的日 K：開高低收）
定義（生效日 = 公告日的下一個交易日，因為匯出檔沒有公告時間、96% 的公司在收盤後申報）：
  開盤漲幅   = 生效日開盤 / 前一交易日收盤 − 1
  日內漲跌幅 = 生效日收盤 / 生效日開盤 − 1
  全日漲跌幅 = 生效日收盤 / 前一交易日收盤 − 1
"""

import datetime as dt
import glob
import json
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(os.path.dirname(BASE), "monthly_revenue_export.csv")
PRICE_DIR = os.path.join(os.path.dirname(os.path.dirname(BASE)), "punish0423 - 複製")  # 桌面上的價格資料夾
OUT = os.path.join(BASE, "data", "revenue_gap_stats.json")
BINS = [-100, -7, -5, -3, -1, 0, 1, 3, 5, 7, 100]
LABELS = ["≤−7%", "−7~−5%", "−5~−3%", "−3~−1%", "−1~0%", "0~1%", "1~3%", "3~5%", "5~7%", ">7%"]
MIN_PRICE = 5  # 排除低價股的跳動雜訊


def load_prices():
    frames = []
    for path in sorted(glob.glob(os.path.join(PRICE_DIR, "price_*.csv"))):
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"代號": str},
                         usecols=["代號", "年月日", "開盤價(元)", "收盤價(元)"])
        frames.append(df)
    px = pd.concat(frames, ignore_index=True)
    px.columns = ["code", "date", "open", "close"]
    px = px[px["code"].str.fullmatch(r"\d{4}")]  # 只留普通股（排除指數、ETF）
    for c in ("open", "close"):  # 高價股有千分位逗號，會被讀成文字
        px[c] = pd.to_numeric(px[c].astype(str).str.replace(",", ""), errors="coerce")
    px["date"] = pd.to_datetime(px["date"], format="%Y/%m/%d")
    px = px.dropna(subset=["open", "close"]).sort_values(["code", "date"])
    px["prev_close"] = px.groupby("code")["close"].shift(1)
    return px.dropna(subset=["prev_close"])


def load_revenue():
    rev = pd.read_csv(EXPORT, encoding="utf-8-sig", dtype={"stock_id": str},
                      usecols=["stock_id", "年月", "營收發布日", "單月營收成長率％", "單月營收與上月比％", "創新高/低(歷史)"])
    rev.columns = ["code", "ym", "ann", "yoy", "mom", "high"]
    rev = rev.dropna(subset=["ann"])
    rev["ann"] = pd.to_datetime(rev["ann"])
    return rev


def stats(df):
    g = df.groupby("bin", observed=False)
    out = []
    for label in LABELS:
        s = df[df["bin"] == label]
        out.append({"bin": label, "n": int(len(s)),
                    "intraday_mean": round(float(s["intraday"].mean() * 100), 2) if len(s) else None,
                    "intraday_median": round(float(s["intraday"].median() * 100), 2) if len(s) else None,
                    "win": round(float((s["intraday"] > 0).mean() * 100), 1) if len(s) else None,
                    "daily_mean": round(float(s["daily"].mean() * 100), 2) if len(s) else None,
                    "drop3": round(float((s["intraday"] <= -0.03).mean() * 100), 1) if len(s) else None})
    return out


def main():
    px = load_prices()
    rev = load_revenue()
    # 生效日：公告日之後第一個該股有交易的日子（merge_asof 往後找，不含當天）
    rev = rev.sort_values("ann")
    px = px.sort_values("date")
    m = pd.merge_asof(rev, px.rename(columns={"date": "eff"}), left_on="ann", right_on="eff", by="code",
                      direction="forward", allow_exact_matches=False)
    m = m.dropna(subset=["eff"])
    m = m[((m["eff"] - m["ann"]).dt.days <= 7) & (m["prev_close"] >= MIN_PRICE)]
    m["gap"] = m["open"] / m["prev_close"] - 1
    m["intraday"] = m["close"] / m["open"] - 1
    m["daily"] = m["close"] / m["prev_close"] - 1
    m = m[(m["gap"].abs() <= 0.11) & (m["intraday"].abs() <= 0.25)]
    m["bin"] = pd.cut(m["gap"] * 100, bins=BINS, labels=LABELS, right=False)
    m["year"] = m["eff"].dt.year

    result = {
        "built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "range": [m["eff"].min().strftime("%Y-%m-%d"), m["eff"].max().strftime("%Y-%m-%d")],
        "n": int(len(m)), "companies": int(m["code"].nunique()),
        "bins": LABELS,
        "overall": stats(m),
        "by_year": {int(y): stats(s) for y, s in m.groupby("year")},
        "by_yoy": {"年增率 > 0": stats(m[m["yoy"] > 0]), "年增率 ≤ 0": stats(m[m["yoy"] <= 0])},
        "by_high": {"創歷史新高": stats(m[m["high"] == "H"]), "非新高": stats(m[m["high"] != "H"])},
        "corr": round(float(m["gap"].corr(m["intraday"])), 3),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"樣本 {result['n']} 筆（{result['companies']} 家，{result['range'][0]} ～ {result['range'][1]}），"
          f"開盤漲幅與日內漲跌幅相關係數 {result['corr']}")
    for b in result["overall"]:
        print(f"  {b['bin']:>8}  n={b['n']:>6}  日內平均 {b['intraday_mean']:>6}%  中位 {b['intraday_median']:>6}%  "
              f"收高於開 {b['win']}%  全日平均 {b['daily_mean']}%")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
