"""00940（臺灣價值高息指數）定期審核預測。

用 predict_data/ 裡的 TEJ 匯出檔，照編製規則重算一次選股和權重，
再跟 00940 實際持股（data/holdings.csv）比對，輸出成 Excel。

用法：python predict_00940.py            （預設算 2026/5 審核）
      python predict_00940.py 2026-10-30  （指定資料截止日）

公開規則沒寫清楚的地方都在 ASSUMPTIONS，會一起寫進 Excel 的「假設說明」頁。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
PD = os.path.join(BASE, "predict_data")

# ---- 這次審核的參數 ----------------------------------------------------------
REVIEWS = {
    # 截止日: (前次截止日, 生效日, 舊成分股取哪天, 實際新成分股取哪天, 近4季, 去年同期4季, 持股月份, 大陸投資季)
    "2026-04-30": dict(prev_cutoff="2025-10-31", effective="2026-05-20",
                       old_date="2026-05-15", new_date="2026-05-27",
                       q4=["2025/03", "2025/06", "2025/09", "2025/12"],
                       q4_prev=["2024/03", "2024/06", "2024/09", "2024/12"],
                       q_growth_base=["2022/03", "2022/06", "2022/09", "2022/12"],
                       hold_month="2026/03", cha_q="2025/12", may=True),
    "2026-10-30": dict(prev_cutoff="2026-04-30", effective="2026-11-18",
                       old_date="2026-11-13", new_date="2026-11-25",
                       q4=["2025/09", "2025/12", "2026/03", "2026/06"],
                       q4_prev=["2024/09", "2024/12", "2025/03", "2025/06"],
                       q_growth_base=["2022/09", "2022/12", "2023/03", "2023/06"],
                       hold_month="2026/09", cha_q="2026/06", may=False),
}

N_CONST = 50
TOP_IN, KEEP_OUT, MAX_REPLACE, MAX_KEEP = 35, 65, 10, 40
UNIVERSE_N, UNI_BUFFER = 300, 0.10
W_CAP = 0.08
AUM_STEP = 250e8

ASSUMPTIONS = [
    ("發行市值", "用 TEJ「市值(百萬元)」；原始股價 = 市值 ÷ 流通在外股數（TEJ 收盤價欄位是還原價）。"),
    ("前 300 大緩衝區", "新進股票要排在前 270 名；上一期母體（前次截止日的前 300 大，不含緩衝）排在 330 名內可保留；不足 300 檔依排名補滿。"),
    ("母體排除", "排除建材營造、創新板（名稱含「-創」）、存託憑證，以及截止日當天 TEJ 標記為全額交割股或變更交易的股票（MK25、MK26）。處置股票不排除（規則沒列）。"),
    ("自由流通量", "1 −（董監 + 經理人 + 大股東 + 董監親屬持股%），用審核前最近一個月（H1 用 2026/03）。臺灣指數公司的係數沒公開，這是近似值；低於 10% 不納入母體。"),
    ("百分位計算", "各項「前 X%」都在前 300 大母體內排名（測過在全部上市櫃裡排名，命中率較低）；負債比、ROA、ROE 金融業和非金融業分開排。資料缺漏視為不通過。"),
    ("流動性", "月週轉率 = 當月每日週轉率加總；日均成交金額 = 當月每日成交值平均。最近 1 個月、12 個月的排名都在母體內排。既有成分股免檢驗。"),
    ("近 3 年配息", "看最近 3 個盈餘分派年度的現金股利是否都 > 0（只算 4/30 前董事會已決議的）。最新年度還沒決議的，往前推一年。"),
    ("金融業資本公積", "近 5 個盈餘分派年度只要有一筆「現金股利_公積」> 0 就排除。"),
    ("5 月年配息除息", "年配息（最新一筆配息型態為 A0）且 1/1～生效日前一天已除息（data/corp_actions.csv）就排除，只在 5 月審核套用。"),
    ("ROA / ROE", "用 TEJ「ROA(A)稅後息前」「ROE(A)稅後」近 4 季單季值加總。"),
    ("益本比（近 2 季）", "近 2 季 EPS 加總 ÷ 截止日原始股價。"),
    ("稅後淨利", "近 4 季（稅前淨利 − 所得稅費用）加總，含少數股權。"),
    ("毛利率成長", "近 4 季毛利率（毛利合計 ÷ 營收合計）減去前一年同期 4 季毛利率。金融業沒有毛利，這項直接通過。"),
    ("對區域投資集中度", "TEJ 大陸投資「期末帳面值」÷ 母公司股東權益（最新年報季）。沒有資料當作 0。"),
    ("長期成長率", "⚠️ 規則沒定義。這版用稅後淨利 3 年 CAGR（近 4 季 vs 3 年前同 4 季），只套用在非既有成分股。可持續成長率會刪掉實際新增的台灣大、營收 CAGR 會刪掉凱基金，只有稅後淨利 CAGR 讓 10 檔新增股全部通過。"),
    ("第二階段放寬", "⚠️ 規則沒寫怎麼放寬。這版用 5 月實際結果反推：第一階段不到 90 檔時，所有百分位門檻一起乘以 1.1（前 90% → 前 99%、前 80% → 前 88%、前 75% → 前 82.5%、後 95% → 全部），還不夠再乘 1.2，依此類推。這樣實際入選的 50 檔全部在名單內，長榮（毛利率成長排 99.3%）會被刪。統一超負債比排 98.9%，剛好在 99% 內，所以門檻沒有太多誤差空間。改成「加 10 個百分點」會讓長榮通過，每次只放寬 1～2 個百分點則會刪掉統一超、萬海等實際保留的股票。"),
    ("下行風險", "截止日前 252 個交易日的還原股價日報酬，√(Σmin(0,R)²/N)×√252；倒數排名前 90%（等於排除下行風險最高的 10%）。"),
    ("自由現金流收益率", "近 4 季（營業現金流 + 購置不動產廠房設備）÷ 發行市值；金融業不算。"),
    ("股利率", "截止日前已決議的最近一整年現金股利 ÷ 截止日原始股價。年配取最新 1 筆、半年配取最近 2 筆、季配取最近 4 筆。"),
    ("綜合分數", "三項指標各自先縮尾到 2.5%～97.5% 分位，再標準化（z 分數），然後取平均（金融業只平均兩項）。"),
    ("權重", "主版本 = 純股利率加權（w ∝ 股利率）；另算「股利率 × 市值」版本對照。上限：8%、自由流通市值占比 ×5、近 3 個月日均成交值占比 ×5、預擬規模 × 權重 ≤ 發行市值 4% 且 ≤ 自由流通市值 10%。分配不下去時照規則放寬（倍數最多 1.5 倍，單檔上限最多 10%）。"),
    ("預擬資產管理規模", "00940 截止日持股市值（持股股數 × 收盤價 ÷ 股票權重合計），無條件進位到 250 億的倍數。只算 00940，沒有加其他追蹤同指數的基金。"),
    ("實際結果", "舊成分股 = 生效前一週的 00940 持股；實際新成分股與權重 = 調整期結束後第一天的 00940 持股（權重已含一週股價漂移，不是指數公司公告的權重）。"),
]


# ---- 讀檔 ------------------------------------------------------------------
def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.strip()
                         .replace({"": np.nan, "- -": np.nan, "nan": np.nan}), errors="coerce")


def read_csv(name, enc="utf-8-sig"):
    return pd.read_csv(os.path.join(PD, name), encoding=enc, dtype=str, keep_default_na=False)


def load_prices():
    parts = []
    for f in sorted(os.listdir(PD)):
        if f.startswith("price_o_") and f.endswith(".csv"):
            d = read_csv(f)
            d.columns = ["code", "name", "date", "adj", "vol", "val", "turn", "shares", "mcap"]
            parts.append(d)
    p = pd.concat(parts, ignore_index=True)
    for c in ["adj", "vol", "val", "turn", "shares", "mcap"]:
        p[c] = num(p[c])
    p["date"] = pd.to_datetime(p["date"])
    p["raw"] = p["mcap"] * 1000 / p["shares"]
    return p.drop_duplicates(["code", "date"]).sort_values(["code", "date"])


def load_fin():
    bs = read_csv("BS21_26.csv"); bs.columns = ["code", "name", "q", "assets", "liab", "eq_parent", "eq", "le", "capital"]
    pl = read_csv("PL21_26.csv"); pl.columns = ["code", "name", "q", "rev", "gp", "pti", "tax", "eps", "wsh", "wsh_d"]
    cf = read_csv("CF21_26.csv"); cf.columns = ["code", "name", "q", "ocf", "capex", "ppe_sale", "cfi"]
    ro = read_csv("ROE21_26.csv"); ro.columns = ["code", "name", "q", "roa_c", "roa", "roa_b", "roa_ci", "roe", "roe_b", "roe_ci"]
    out = {}
    for k, d in dict(bs=bs, pl=pl, cf=cf, ro=ro).items():
        for c in d.columns[3:]:
            d[c] = num(d[c])
        out[k] = d.drop(columns="name").set_index(["code", "q"])
    f = out["bs"].join(out["pl"], how="outer").join(out["cf"], how="outer").join(out["ro"], how="outer")
    f["ni"] = f["pti"] - f["tax"]
    return f


def load_div():
    d = pd.concat([read_csv("EA_split_20_22.csv"), read_csv("EA_split_23_26.csv")], ignore_index=True)
    d.columns = ["code", "name", "meet", "kind", "type", "cash", "board", "year", "cash_e", "cash_c"]
    for c in ["cash", "cash_e", "cash_c"]:
        d[c] = num(d[c]).fillna(0)
    d["meet"] = pd.to_datetime(d["meet"], errors="coerce")
    d["board"] = pd.to_datetime(d["board"], errors="coerce")
    d = d[d["type"] != ""].drop_duplicates()
    # 同一年度同一型態有多筆（董事會、股東會）時，留最後一次會議的
    return d.sort_values("meet").drop_duplicates(["code", "year", "type"], keep="last")


def load_hold(month):
    h = read_csv("HOLD.csv"); h.columns = ["code", "name", "m", "shares", "dir", "mgr", "big", "rel"]
    h = h[h["m"] == month].copy()
    for c in ["dir", "mgr", "big", "rel"]:
        h[c] = num(h[c]).fillna(0)
    h["ff"] = (1 - (h["dir"] + h["mgr"] + h["big"] + h["rel"]) / 100).clip(lower=0)
    return h.set_index("code")[["ff", "dir", "mgr", "big", "rel"]]


def load_cha(q):
    c = read_csv("CHA.csv")
    c = c[c.iloc[:, 2] == q]
    return pd.Series(num(c.iloc[:, 14]).values, index=c.iloc[:, 0].values).groupby(level=0).last()


def load_ind():
    d = pd.read_csv(os.path.join(PD, "IND.csv"), encoding="cp950", encoding_errors="replace", dtype=str)
    d.columns = ["code", "name", "sub_code", "sub", "sub2", "tej_ind", "tse_ind"]
    return d.set_index("code")


def load_full_delivery():
    """TEJ 交易屬性（MK*.csv）：每天被列為全額交割或變更交易的股票，回傳 {日期: set(代號)}。"""
    out = {}
    for f in sorted(os.listdir(PD)):
        if f.startswith("MK") and f.endswith(".csv"):
            d = read_csv(f)
            d.columns = ["code", "name", "date", "margin", "disposal", "short", "daytrade", "altered", "full"]
            d = d[(d["full"] == "Y") | (d["altered"] == "Y")]
            for dt, g in d.groupby("date"):
                out.setdefault(pd.Timestamp(dt), set()).update(g["code"])
    return out


def etf_holdings(date):
    h = pd.read_csv(os.path.join(BASE, "data", "holdings.csv"), dtype=str, encoding="utf-8-sig")
    h = h[(h["etf"] == "00940") & (h["date"] == date) & (h["type"] == "stock")].copy()
    h["weight"] = num(h["weight"])
    h["qty"] = num(h["qty"])
    h["w"] = h["weight"] / h["weight"].sum()
    return h.set_index("code")


def ex_div_dates():
    c = pd.read_csv(os.path.join(BASE, "data", "corp_actions.csv"), dtype=str, encoding="utf-8-sig")
    c = c[(c["kind"] == "dividend") & c["label"].str.contains("息")]
    c["date"] = pd.to_datetime(c["date"])
    return c


# ---- 工具 ------------------------------------------------------------------
def pct_rank(s, mask=None, ascending=False):
    """名次百分比（1/n～1）。預設值越大排越前面；mask 以外的股票不參與排名、結果為 NaN。"""
    x = s if mask is None else s.where(mask)
    return x.rank(ascending=ascending, method="min") / x.notna().sum()


def pct_top(s, mask=None):
    return pct_rank(s, mask)


def by_group(s, grp, ascending, mask=None):
    """金融業、非金融業分開排名。"""
    out = pd.Series(np.nan, index=s.index)
    for g in grp.unique():
        m = (grp == g) if mask is None else ((grp == g) & mask)
        out[m] = pct_rank(s[m], ascending=ascending)
    return out


def zscore_winsor(s):
    lo, hi = s.quantile(0.025), s.quantile(0.975)
    x = s.clip(lo, hi)
    return (x - x.mean()) / x.std(ddof=0)


def cap_weights(raw, cap):
    """依 raw 比例分配、超過上限的截掉並把多出來的再分給其他檔。分不完回傳 None。"""
    if cap.sum() < 1 - 1e-9:
        return None
    w = raw / raw.sum()
    fixed = pd.Series(False, index=raw.index)
    for _ in range(200):
        over = (w > cap + 1e-12) & ~fixed
        if not over.any():
            break
        fixed |= over
        w[fixed] = cap[fixed]
        rest = 1 - w[fixed].sum()
        free = ~fixed
        w[free] = raw[free] / raw[free].sum() * rest
    return w


# ---- 可切換的假設（用 5 月實際結果測過，預設值是命中率最高的組合）--------------
OPT = dict(
    pop="universe",   # 第一階段百分位在哪裡排名："all" = 全部上市櫃普通股，"universe" = 前 300 大母體
    debt=True,        # 是否套用負債比篩選
    growth="ni",      # 長期成長率："sgr" = ROE ×（1 − 配息率），"rev" = 營收 3 年 CAGR，"ni" = 稅後淨利 3 年 CAGR，"none" = 不篩
    relax_mode="mul", # 放寬方式："mul" = 門檻 ×(1 + 放寬幅度)，例如 90% → 99%；"add" = 門檻 + 放寬幅度
    relax_step=0.10,  # 第一階段不足 90 檔時，每次放寬多少（mul 是 10%，add 是 10 個百分點）
    no_relax=(),      # 放寬時維持原門檻的條件，例如 ("毛利率成長高",)
)

_CACHE = {}


def data():
    if not _CACHE:
        print("讀取資料…")
        _CACHE.update(px=load_prices(), fin=load_fin(), div=load_div(), ind=load_ind(), full=load_full_delivery())
    return _CACHE


def universe_chain(px, stocks, names, hold_ff, cutoff, full=None):
    """前 300 大母體，含 10% 緩衝區。從資料裡最早的審核截止日一路往後推，才有「上一期母體」可以用。"""
    dates = sorted(px["date"].unique())
    cuts = []
    for y in range(2020, 2030):
        for m in (4, 10):
            last = [d for d in dates if d.year == y and d.month == m]
            if last and last[-1] <= pd.Timestamp(cutoff):
                cuts.append(last[-1])
    prev, out = None, None
    lo_in, hi_keep = int(UNIVERSE_N * (1 - UNI_BUFFER)), int(UNIVERSE_N * (1 + UNI_BUFFER))
    for d in cuts:
        s = px[(px["date"] == d) & px["code"].isin(stocks)].set_index("code")
        s = s[~s.index.map(lambda c: "-創" in str(names.get(c, "")))]
        s = s[~s.index.isin((full or {}).get(d, set()))]
        r = s["mcap"].rank(ascending=False, method="first")
        if d == cuts[-1]:
            r = r.drop([c for c in r.index if hold_ff.get(c, 1) < 0.10])
            r = r.rank(method="first")
        r = r.sort_values()
        if prev is None:
            keep = list(r.index[:UNIVERSE_N])
        else:
            keep = [c for c in r.index if r[c] <= lo_in or (c in prev and r[c] <= hi_keep)]
            keep += [c for c in r.index if c not in keep][:max(0, UNIVERSE_N - len(keep))]
            keep = sorted(keep, key=lambda c: r[c])[:UNIVERSE_N]
        prev, out = set(keep), (keep, r)
    return out


# ---- 主流程 ----------------------------------------------------------------
def run(cutoff="2026-04-30", opt=None):
    o = dict(OPT, **(opt or {}))
    P = REVIEWS[cutoff]
    cut = pd.Timestamp(cutoff)
    D = data()
    px, fin, div, ind = D["px"], D["fin"], D["div"], D["ind"]
    hold = load_hold(P["hold_month"])
    cha = load_cha(P["cha_q"])
    old = etf_holdings(P["old_date"])
    new = etf_holdings(P["new_date"])
    existing = set(old.index)

    # --- 母體 ---
    stocks = ind[(ind["tse_ind"] != "建材營造") & (ind["tse_ind"] != "存託憑證")].index
    names = px.drop_duplicates("code", keep="last").set_index("code")["name"]
    ff = hold["ff"]
    full = D["full"]
    keep, r_now = universe_chain(px, stocks, names, ff, cutoff, full)
    full_now = full.get(cut, set())
    low_ff = set(c for c in stocks if ff.get(c, 1) < 0.10)
    prev_uni = set(universe_chain(px, stocks, names, ff, P["prev_cutoff"], full)[0])
    # 所有上市櫃普通股都算指標（百分位可能是在全部股票裡排），「在母體」標記前 300 大
    snap_all = px[(px["date"] == cut) & px["code"].isin(stocks)]
    raw_rank = snap_all.set_index("code")["mcap"].rank(ascending=False, method="first")
    U = pd.DataFrame(index=pd.Index(sorted(snap_all["code"]), name="code"))
    U["在母體"] = U.index.isin(keep)
    U["全額交割"] = U.index.isin(full_now)
    U["名稱"] = names.reindex(U.index)
    U["產業"] = ind["tse_ind"].reindex(U.index)
    U["金融業"] = U["產業"] == "金融業"
    U["既有成分股"] = U.index.isin(existing)
    U["市值排名"] = r_now.reindex(U.index)
    U["上期母體"] = U.index.isin(prev_uni)

    snap = px[px["date"] == cut].set_index("code")
    U["發行市值(億)"] = snap["mcap"].reindex(U.index) / 100
    U["原始股價"] = snap["raw"].reindex(U.index)
    U["自由流通量"] = ff.reindex(U.index)

    # --- 流動性 ---
    m_end = cut.to_period("M")
    months = pd.period_range(m_end - 11, m_end, freq="M")
    p12 = px[px["code"].isin(U.index) & (px["date"].dt.to_period("M") >= months[0]) & (px["date"] <= cut)].copy()
    p12["m"] = p12["date"].dt.to_period("M")
    g = p12.groupby(["code", "m"]).agg(val=("val", "sum"), turn=("turn", "sum"), avgval=("val", "mean"))
    val1 = g.xs(m_end, level="m")["val"].reindex(U.index)
    val12 = g["val"].groupby(level=0).sum().reindex(U.index)
    n_turn = (g["turn"] >= 3).groupby(level=0).sum().reindex(U.index).fillna(0)
    n_avg = (g["avgval"] >= 100_000).groupby(level=0).sum().reindex(U.index).fillna(0)
    U["近1月成交值(億)"] = val1 / 1e5
    U["近1月成交值_名次%"] = pct_top(val1, U["在母體"])
    U["近12月成交值(億)"] = val12 / 1e5
    U["近12月成交值_名次%"] = pct_top(val12, U["在母體"])
    U["週轉率≥3%月數"] = n_turn
    U["日均≥1億月數"] = n_avg
    liq = (U["近1月成交值_名次%"] <= 0.70) & ((U["近12月成交值_名次%"] <= 0.20) | (n_turn >= 8) | (n_avg >= 8))
    U["通過_流動性"] = liq | U["既有成分股"]

    # --- 財報 ---
    def fsum(col, qs):
        x = fin[col].unstack("q").reindex(U.index)
        x = x.reindex(columns=qs)
        return x.sum(axis=1, min_count=len(qs))

    def flast(col, q):
        return fin[col].unstack("q").reindex(U.index).get(q)

    q4, q4p, qb = P["q4"], P["q4_prev"], P["q_growth_base"]
    U["近4季EPS"] = fsum("eps", q4)
    U["近2季EPS"] = fsum("eps", q4[-2:])
    U["負債比率%"] = flast("liab", q4[-1]) / flast("assets", q4[-1]) * 100
    U["ROA近4季%"] = fsum("roa", q4)
    U["ROE近4季%"] = fsum("roe", q4)
    U["益本比_近2季%"] = U["近2季EPS"] / U["原始股價"] * 100
    U["近4季稅後淨利(億)"] = fsum("ni", q4) / 1e5
    gm_now = fsum("gp", q4) / fsum("rev", q4)
    gm_prev = fsum("gp", q4p) / fsum("rev", q4p)
    U["近4季毛利率%"] = gm_now * 100
    U["毛利率成長(百分點)"] = (gm_now - gm_prev) * 100
    eq_par = flast("eq_parent", q4[-1])
    U["大陸投資帳面值(億)"] = cha.reindex(U.index).fillna(0) / 1e5
    U["大陸投資/淨值%"] = cha.reindex(U.index).fillna(0) / eq_par * 100
    rev_now, rev_base = fsum("rev", q4), fsum("rev", qb)
    U["營收3年CAGR%"] = ((rev_now / rev_base) ** (1 / 3) - 1) * 100
    fcf = fsum("ocf", q4) + fsum("capex", q4)
    U["近4季自由現金流(億)"] = fcf / 1e5
    U["自由現金流收益率%"] = np.where(U["金融業"], np.nan, fcf / 1e3 / (U["發行市值(億)"] * 100) * 100)

    # 分母是 0 會算出無限大（例如前一年營收 0），當作沒有資料
    U = U.replace([np.inf, -np.inf], np.nan)

    # --- 股利 ---
    known = div[div["board"] <= cut]
    rows = {}
    kg = {c: g.sort_values("board") for c, g in known.groupby("code")}
    for code in U.index:
        d = kg.get(code, known.iloc[:0])
        if d.empty:
            rows[code] = dict(freq="", dps=np.nan, y3=False, cap5=False, years="")
            continue
        last_type = d.iloc[-1]["type"]
        n = 1 if last_type == "A0" else (2 if last_type.startswith("H") else 4)
        dps = d.tail(n)["cash"].sum()
        by_year = d.groupby("year")["cash"].sum()
        ys = sorted(int(y) for y in by_year.index if y)
        latest = max(ys) if ys else 0
        # 最新年度如果只是期中配息還沒有整年決議，仍算它有發放
        need = [latest - 2, latest - 1, latest]
        y3 = all(by_year.get(str(y), 0) > 0 for y in need)
        cap5 = d[d["year"].isin([str(y) for y in range(latest - 4, latest + 1)])]["cash_c"].sum() > 0
        rows[code] = dict(freq=last_type, dps=dps, y3=y3, cap5=cap5,
                          years="、".join(f"{y}:{by_year.get(str(y), 0):g}" for y in need))
    dv = pd.DataFrame(rows).T
    U["配息型態"] = dv["freq"]
    U["近3年度股利(元)"] = dv["years"]
    U["近一年現金股利(元)"] = dv["dps"].astype(float)
    U["股利率%"] = U["近一年現金股利(元)"] / U["原始股價"] * 100
    U["近3年皆配息"] = dv["y3"].astype(bool)
    U["金融業近5年含公積"] = dv["cap5"].astype(bool) & U["金融業"]

    eff = pd.Timestamp(P["effective"])
    exd = ex_div_dates()
    exd = exd[(exd["date"] >= pd.Timestamp(eff.year, 1, 1)) & (exd["date"] < eff)]
    U["生效前已除息日"] = exd.groupby("code")["date"].min().reindex(U.index).dt.strftime("%Y-%m-%d")
    U["5月排除_年配已除息"] = P["may"] & (U["配息型態"] == "A0") & U["生效前已除息日"].notna()

    payout = (U["近一年現金股利(元)"] / U["近4季EPS"]).clip(0, 1)
    U["可持續成長率%"] = np.where(U["近4季EPS"] > 0, U["ROE近4季%"] * (1 - payout), np.nan)
    ni_now, ni_base = fsum("ni", q4), fsum("ni", qb)
    U["稅後淨利3年CAGR%"] = np.where((ni_base > 0) & (ni_now > 0), ((ni_now / ni_base) ** (1 / 3) - 1) * 100, np.nan)
    U["長期成長率%"] = {"sgr": U["可持續成長率%"], "rev": U["營收3年CAGR%"], "ni": U["稅後淨利3年CAGR%"],
                     "none": pd.Series(1.0, index=U.index)}[o["growth"]]

    # --- 第一階段 ---
    grp = U["金融業"]
    M = U["在母體"] if o["pop"] == "universe" else None
    U["負債比_名次%"] = by_group(U["負債比率%"], grp, True, M)
    U["ROA_名次%"] = by_group(U["ROA近4季%"], grp, False, M)
    U["ROE_名次%"] = by_group(U["ROE近4季%"], grp, False, M)
    U["益本比_名次%"] = pct_top(U["益本比_近2季%"], M)
    U["稅後淨利_名次%"] = pct_top(U["近4季稅後淨利(億)"], M)
    U["毛利率成長_名次%"] = pct_top(U["毛利率成長(百分點)"], M)
    U["大陸投資_名次%(低到高)"] = pct_rank(U["大陸投資/淨值%"], M, ascending=True)
    U["長期成長_名次%"] = pct_top(U["長期成長率%"], M)

    def stage1(relax=0.0):
        c = pd.DataFrame(index=U.index)
        def t(x, key=None):
            if key in o["no_relax"]:
                return x
            return min(1.0, x * (1 + relax) if o["relax_mode"] == "mul" else x + relax)
        c["非KY"] = ~U["名稱"].str.contains("KY", na=False)
        c["EPS>0且近3年配息"] = (U["近4季EPS"] > 0) & U["近3年皆配息"] & ~U["金融業近5年含公積"] & ~U["5月排除_年配已除息"]
        c["負債比低"] = (U["負債比_名次%"] <= t(0.90, "負債比低")) if o["debt"] else True
        c["ROA、ROE高"] = (U["ROA_名次%"] <= t(0.90, "ROA、ROE高")) & (U["ROE_名次%"] <= t(0.90, "ROA、ROE高"))
        c["益本比高"] = U["益本比_名次%"] <= t(0.80, "益本比高")
        c["稅後淨利高"] = U["稅後淨利_名次%"] <= t(0.90, "稅後淨利高")
        c["毛利率成長高"] = (U["毛利率成長_名次%"] <= t(0.90, "毛利率成長高")) | U["金融業"]
        c["大陸投資低"] = U["大陸投資_名次%(低到高)"] <= t(0.95, "大陸投資低")
        c["長期成長高(新進者)"] = U["既有成分股"] | (U["長期成長_名次%"] <= t(0.75, "長期成長高(新進者)"))
        c["流動性"] = U["通過_流動性"]
        return c

    relax = 0.0
    while True:
        C = stage1(relax)
        passed = C.all(axis=1) & U["在母體"]
        if passed.sum() >= 90 or relax >= 0.5:
            break
        relax += o["relax_step"]
    n_before = int((stage1(0.0).all(axis=1) & U["在母體"]).sum())
    if relax == 0:
        relax_note = "不用放寬"
    elif o["relax_mode"] == "mul":
        relax_note = f"門檻 ×{1 + relax:.2f}，例如 90% → {min(1, 0.9 * (1 + relax)):.0%}"
    else:
        relax_note = f"門檻 +{relax:.0%}"
    for c in C.columns:
        U["通過_" + c] = C[c]
    U["通過第一階段"] = passed

    # --- 第二階段：下行風險 ---
    hist = px[px["code"].isin(U.index) & (px["date"] <= cut)].copy()
    hist["ret"] = hist.groupby("code")["adj"].pct_change()
    hist = hist.dropna(subset=["ret"]).groupby("code").tail(252)
    dr = hist.groupby("code")["ret"].apply(lambda r: math.sqrt((np.minimum(0, r) ** 2).sum() / len(r)) * math.sqrt(252))
    U["下行風險"] = dr.reindex(U.index)
    U["下行風險_天數"] = hist.groupby("code").size().reindex(U.index)
    s1 = U[U["通過第一階段"]]
    inv_rank = (1 / s1["下行風險"]).rank(ascending=False, method="min") / len(s1)
    U["通過第二階段"] = False
    U.loc[s1.index, "通過第二階段"] = inv_rank <= 0.90

    # --- 綜合分數 ---
    S = U[U["通過第二階段"]].copy()
    z = pd.DataFrame({
        "z_自由現金流": zscore_winsor(S["自由現金流收益率%"]),
        "z_股利率": zscore_winsor(S["股利率%"]),
        "z_益本比": zscore_winsor(S["益本比_近2季%"]),
    })
    S = S.join(z)
    S["綜合分數"] = z.mean(axis=1, skipna=True)
    S["分數排名"] = S["綜合分數"].rank(ascending=False, method="first").astype(int)
    S = S.sort_values("分數排名")

    # --- 緩衝區 ---
    sel = list(S.index[S["分數排名"] <= TOP_IN])
    mid = S[(S["分數排名"] > TOP_IN) & (S["分數排名"] <= KEEP_OUT)]
    for c in list(mid.index[mid["既有成分股"]]) + list(mid.index[~mid["既有成分股"]]):
        if len(sel) >= N_CONST:
            break
        sel.append(c)
    n_new = sum(c not in existing for c in sel)
    rule_note = f"一般緩衝區規則，換入 {n_new} 檔"
    if n_new > MAX_REPLACE:
        ex = [c for c in S.index if c in existing][:MAX_KEEP]
        nw = [c for c in S.index if c not in existing][:N_CONST - len(ex)]
        sel = ex + nw
        rule_note = f"緩衝區後換入 {n_new} 檔 > 10，改用「最多保留 40 檔既有 + 新股補滿 50」"
    U = U.join(S[["z_自由現金流", "z_股利率", "z_益本比", "綜合分數", "分數排名"]])
    U["預測入選"] = U.index.isin(sel)

    # --- 權重 ---
    W = U.loc[sel].copy()
    adv = px[px["code"].isin(sel) & (px["date"] > cut - pd.DateOffset(months=3)) & (px["date"] <= cut)].groupby("code")["val"].mean()
    W["近3月日均成交值(億)"] = adv.reindex(W.index) / 1e5
    W["自由流通市值(億)"] = W["發行市值(億)"] * W["自由流通量"].fillna(1)
    snap_old = old.join(snap[["raw"]])
    aum = (snap_old["qty"] * snap_old["raw"]).sum() / (old["weight"].sum() / 100)
    aum_h = math.ceil(aum / AUM_STEP) * AUM_STEP
    ff_share = W["自由流通市值(億)"] / W["自由流通市值(億)"].sum()
    adv_share = W["近3月日均成交值(億)"] / W["近3月日均成交值(億)"].sum()
    W["上限_自由流通×5"] = 5 * ff_share
    W["上限_成交值×5"] = 5 * adv_share
    W["上限_規模(市值4%)"] = 0.04 * W["發行市值(億)"] * 1e8 / aum_h
    W["上限_規模(流通10%)"] = 0.10 * W["自由流通市值(億)"] * 1e8 / aum_h

    def solve(raw):
        for top in [0.08, 0.085, 0.09, 0.095, 0.10]:
            for k in [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]:
                cap = pd.concat([pd.Series(top, index=W.index), W["上限_自由流通×5"] * k, W["上限_成交值×5"] * k,
                                 W["上限_規模(市值4%)"] * k, W["上限_規模(流通10%)"] * k], axis=1).min(axis=1)
                w = cap_weights(raw.copy(), cap)
                if w is not None:
                    return w, cap, f"單檔上限 {top:.1%}、倍數放寬 ×{k}"
        return raw / raw.sum(), cap, "無法完成分配"

    W["權重上限"] = 0.0
    w_dy, cap_dy, note_dy = solve(W["股利率%"].clip(lower=0).fillna(0))
    w_dm, _, note_dm = solve((W["股利率%"] * W["發行市值(億)"]).clip(lower=0).fillna(0))
    W["權重上限"] = cap_dy
    W["原始權重_股利率"] = W["股利率%"] / W["股利率%"].sum()
    W["預測權重_股利率"] = w_dy
    W["預測權重_股利率×市值"] = w_dm
    W["觸及上限"] = (w_dy >= cap_dy - 1e-9)
    W["實際權重(新)"] = new["w"].reindex(W.index)

    # --- 比對 ---
    allc = sorted(set(sel) | set(new.index) | existing, key=lambda c: (c not in sel, U["分數排名"].get(c, 999) if c in U.index else 999))
    R = pd.DataFrame(index=pd.Index(allc, name="code"))
    R["名稱"] = [U["名稱"].get(c) if c in U.index else (new["name"].get(c) if c in new.index else old["name"].get(c)) for c in allc]
    R["舊成分股"] = R.index.isin(existing)
    R["預測入選"] = R.index.isin(sel)
    R["實際入選"] = R.index.isin(new.index)

    def cat(r):
        if r["預測入選"] and r["實際入選"]:
            return "✅ 命中－保留" if r["舊成分股"] else "✅ 命中－新增"
        if not r["預測入選"] and not r["實際入選"]:
            return "✅ 命中－刪除"
        if r["預測入選"]:
            return "❌ 預測入選但實際沒有"
        return "❌ 實際入選但預測沒有"
    R["結果"] = R.apply(cat, axis=1)
    R["分數排名"] = U["分數排名"].reindex(R.index)
    R["綜合分數"] = U["綜合分數"].reindex(R.index)
    R["股利率%"] = U["股利率%"].reindex(R.index)
    R["預測權重%"] = W["預測權重_股利率"].reindex(R.index) * 100
    R["預測權重%(股利率×市值)"] = W["預測權重_股利率×市值"].reindex(R.index) * 100
    R["實際權重%(新)"] = new["w"].reindex(R.index) * 100
    R["權重差(預測−實際)"] = R["預測權重%"].fillna(0) - R["實際權重%(新)"].fillna(0)
    R["舊權重%"] = old["w"].reindex(R.index) * 100

    def why(c):
        if c not in U.index or not U.loc[c, "在母體"]:
            return "不在前 300 大母體（市值、產業或自由流通量）"
        u = U.loc[c]
        fails = [k[3:] for k in U.columns if k.startswith("通過_") and k != "通過_流動性" and not bool(u[k])]
        if fails:
            return "第一階段未過：" + "、".join(fails)
        if not u["通過第一階段"]:
            return "第一階段未過"
        if not u["通過第二階段"]:
            return "第二階段未過：下行風險太高"
        if not u["預測入選"]:
            return f"分數排名 {int(u['分數排名'])}，緩衝區外"
        return ""
    R["未入選原因(預測)"] = [why(c) if not R.loc[c, "預測入選"] else "" for c in R.index]

    hit = int((R["預測入選"] & R["實際入選"]).sum())
    act_add = set(new.index) - existing; act_del = existing - set(new.index)
    pr_add = set(sel) - existing; pr_del = existing - set(sel)
    both = W["實際權重(新)"].notna()
    mae = (W["預測權重_股利率"] - W["實際權重(新)"].fillna(0)).abs()
    mae2 = (W["預測權重_股利率×市值"] - W["實際權重(新)"].fillna(0)).abs()
    corr = W.loc[both, "預測權重_股利率"].corr(W.loc[both, "實際權重(新)"])
    corr2 = W.loc[both, "預測權重_股利率×市值"].corr(W.loc[both, "實際權重(新)"])

    summary = [
        ("資料截止日", cutoff), ("生效日", P["effective"]),
        ("舊成分股取自", f"00940 持股 {P['old_date']}（{len(existing)} 檔）"),
        ("實際新成分股取自", f"00940 持股 {P['new_date']}（{len(new)} 檔）"),
        ("", ""),
        ("母體檔數", int(U["在母體"].sum())), ("　自由流通量 < 10% 排除", len(low_ff)),
        ("　全額交割排除", f"{len(full_now & set(stocks))} 檔（全市場）；其中市值前 330 名：" + ("、".join(sorted(c for c in full_now if raw_rank.get(c, 999) <= 330)) or "無")),
        ("通過流動性", int(U["通過_流動性"].sum())),
        ("通過第一階段", f"{int(U['通過第一階段'].sum())}（放寬前 {n_before} 檔；{relax_note}）"),
        ("通過第二階段", int(U["通過第二階段"].sum())),
        ("緩衝區處理", rule_note),
        ("", ""),
        ("★ 選股命中", f"{hit} / {N_CONST}"),
        ("實際新增", f"{len(act_add)} 檔：" + "、".join(f"{c}{R.loc[c, '名稱']}" for c in sorted(act_add))),
        ("預測新增", f"{len(pr_add)} 檔：" + "、".join(f"{c}{R.loc[c, '名稱']}" for c in sorted(pr_add))),
        ("　新增命中", f"{len(act_add & pr_add)} 檔"),
        ("實際刪除", f"{len(act_del)} 檔：" + "、".join(f"{c}{R.loc[c, '名稱']}" for c in sorted(act_del))),
        ("預測刪除", f"{len(pr_del)} 檔：" + "、".join(f"{c}{R.loc[c, '名稱']}" for c in sorted(pr_del))),
        ("　刪除命中", f"{len(act_del & pr_del)} 檔"),
        ("", ""),
        ("00940 規模（截止日）", f"{aum / 1e8:,.0f} 億"), ("預擬資產管理規模", f"{aum_h / 1e8:,.0f} 億"),
        ("權重（股利率）", f"{note_dy}；觸及上限 {int(W['觸及上限'].sum())} 檔；與實際權重相關係數 {corr:.3f}；絕對誤差合計 {mae.sum() * 100:.1f} 個百分點"),
        ("權重（股利率×市值）", f"{note_dm}；與實際權重相關係數 {corr2:.3f}；絕對誤差合計 {mae2.sum() * 100:.1f} 個百分點"),
    ]
    return dict(summary=summary, R=R, U=U, W=W, relax=relax, liq_monthly=g, months=months, aum=aum, aum_h=aum_h, P=P,
                weight_note=note_dy)


# ---- 輸出 Excel ------------------------------------------------------------
def write_excel(res, path):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    U = res["U"]
    U = U[U["在母體"] | U["既有成分股"] | U.index.isin(res["R"].index)].copy()
    W = res["W"]
    U.insert(0, "預測入選", U.pop("預測入選"))
    w_cols = ["名稱", "產業", "既有成分股", "分數排名", "股利率%", "發行市值(億)", "自由流通量", "自由流通市值(億)",
              "近3月日均成交值(億)", "原始權重_股利率", "上限_自由流通×5", "上限_成交值×5", "上限_規模(市值4%)",
              "上限_規模(流通10%)", "權重上限", "觸及上限", "預測權重_股利率", "預測權重_股利率×市值", "實際權重(新)"]
    W = W[w_cols].sort_values("預測權重_股利率", ascending=False)

    green = PatternFill("solid", fgColor="E2F0D9"); red = PatternFill("solid", fgColor="FBE2E2")
    head = PatternFill("solid", fgColor="1F4E78"); hfont = Font(color="FFFFFF", bold=True)

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(res["summary"], columns=["項目", "內容"]).to_excel(xw, sheet_name="摘要", index=False)
        res["R"].reset_index().to_excel(xw, sheet_name="預測vs實際", index=False)
        W.reset_index().to_excel(xw, sheet_name="權重計算", index=False)
        U.reset_index().to_excel(xw, sheet_name="母體明細", index=False)
        pd.DataFrame(ASSUMPTIONS, columns=["項目", "這版的做法"]).to_excel(xw, sheet_name="假設說明", index=False)
        if "grid" in res:
            res["grid"].to_excel(xw, sheet_name="假設測試", index=False)

        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.fill, c.font = head, hfont
                c.alignment = Alignment(wrap_text=True, vertical="center")
            ws.freeze_panes = "C2" if ws.title in ("預測vs實際", "權重計算", "母體明細") else "A2"
            ws.auto_filter.ref = ws.dimensions
            hdr = [c.value for c in ws[1]]
            for i, h in enumerate(hdr, 1):
                L = get_column_letter(i)
                width = 12
                if ws.title in ("摘要", "假設說明"):
                    width = 24 if i == 1 else 110
                elif h in ("名稱", "產業"):
                    width = 12
                elif h and ("原因" in h or "結果" in h or h in ("近3年度股利(元)", "漏掉")):
                    width = 34
                ws.column_dimensions[L].width = width
                pct_col = h and (h.startswith("上限_") or h.startswith("預測權重_") or h in ("權重上限", "原始權重_股利率", "實際權重(新)", "自由流通量")
                                 or h.endswith("名次%"))
                for cell in ws[L][1:]:
                    v = cell.value
                    if isinstance(v, bool):
                        cell.value = "✔" if v else "✘"
                        cell.fill = green if v else red
                        cell.alignment = Alignment(horizontal="center")
                    elif isinstance(v, float):
                        cell.number_format = "0.00%" if pct_col else "#,##0.00"
                    if ws.title in ("摘要", "假設說明"):
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
            if ws.title == "預測vs實際":
                col = hdr.index("結果") + 1
                for row in ws.iter_rows(min_row=2):
                    c = row[col - 1]
                    c.fill = green if str(c.value).startswith("✅") else red
            ws.row_dimensions[1].height = 36


if __name__ == "__main__":
    cutoff = sys.argv[1] if len(sys.argv) > 1 else "2026-04-30"
    res = run(cutoff)
    if "--grid" in sys.argv:
        import itertools
        rows = []
        for pop, debt, growth, step in itertools.product(["universe", "all"], [True, False], ["sgr", "rev", "ni", "none"], [0.05, 0.10]):
            r = run(cutoff, dict(pop=pop, debt=debt, growth=growth, relax_step=step))
            R, sm = r["R"], dict(r["summary"])
            rows.append({"百分位範圍": {"universe": "前300大", "all": "全部上市櫃"}[pop], "負債比篩選": debt,
                         "長期成長率": {"sgr": "可持續成長率", "rev": "營收3年CAGR", "ni": "稅後淨利3年CAGR", "none": "不篩"}[growth],
                         "放寬幅度": step, "命中": int((R["預測入選"] & R["實際入選"]).sum()),
                         "第一階段通過": sm["通過第一階段"], "新增命中": sm["　新增命中"], "刪除命中": sm["　刪除命中"],
                         "漏掉": "、".join(f"{c}{R.loc[c, '名稱']}" for c in R.index[R["實際入選"] & ~R["預測入選"]])})
        res["grid"] = pd.DataFrame(rows).sort_values("命中", ascending=False)
    out = os.path.join(PD, f"00940_預測_{cutoff}.xlsx")
    try:
        write_excel(res, out)
    except PermissionError:  # 舊檔正在 Excel 裡開著
        out = out.replace(".xlsx", f"_{pd.Timestamp.now():%m%d_%H%M}.xlsx")
        write_excel(res, out)
    for k, v in res["summary"]:
        if k:
            print(f"{k}: {v}")
    print("輸出：", out)
