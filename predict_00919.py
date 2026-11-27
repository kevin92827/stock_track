"""預測 00919 群益台灣精選高息（臺灣指數公司特選臺灣上市上櫃精選高息指數）的定期審核。

規則（公開說明書 p.41～42）：
  母體：上市櫃普通股，排除管理股票、變更交易、停止買賣；取發行市值前 300 大。
  篩選：日均成交值指標（近 1 月、近 6 月日均成交值取低）> 8,000 萬，既有成分股 > 6,000 萬，
        不足 150 檔依指標補齊；近四季稅後 ROE > 0；
        5 月另排除：年配股截止日前董事會還沒決議現金股利，或生效日前已除息。
  排序：5 月用股利率（近四季現金股利 ÷ 截止日股價），12 月用預估股利率
        = 股利率 × (1 + 今年前三季 EPS 年增率)；取 40 檔。
  緩衝區：前 20 名納入；既有成分股 61 名後刪除；21～60 名既有優先；
          至少 7 檔在母體市值前 50 大、20 檔在前 150 大；12 月最多換 8 檔。
  權重：股利率加權、依流動量指標（近 6 月日成交值中位數）調整；單檔 ≤15%、前五大 ≤65%、下限 0.5%；
        預擬規模 = 追蹤資產 × 1.2 無條件進位到 250 億倍數，投資金額 ≤ 發行市值 6%、≤ 自由流通市值 15%。
  時程：5 月資料截至第 10 個交易日、基準日第 17 個交易日；12 月資料截至 11 月底、基準日第 7 個交易日；
        基準日後間隔 5 個交易日生效。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predict_00940 as m  # 共用 TEJ 資料讀取

BASE = os.path.dirname(os.path.abspath(__file__))
N, TOP_IN, KEEP_OUT = 40, 20, 60
LIQ_NEW, LIQ_OLD, LIQ_MIN_N = 80_000, 60_000, 150          # 千元
W_MAX, TOP5_MAX, W_MIN = 0.15, 0.65, 0.005
AUM_STEP = 250e8

# 審核設定：roe_q = 截止日已公布的近四季；eps_now / eps_prev = 12 月審核的今年、去年前三季
REVIEWS = {
    "2026-05": dict(may=True, roe_q=["2025/03", "2025/06", "2025/09", "2025/12"],
                    old_date="2026-06-01", new_date="2026-06-12", hold_month="2026/04"),
    # 2025/12 考古題：新名單 = 2026/6/1 持股（5 月審核前）；舊名單 = 新名單 − 納入 + 剔除（新聞公告）
    "2025-12": dict(may=False, roe_q=["2024/12", "2025/03", "2025/06", "2025/09"],
                    eps_now=["2025/03", "2025/06", "2025/09"], eps_prev=["2024/03", "2024/06", "2024/09"],
                    old_date=None, new_date="2026-06-01", hold_month="2025/10",
                    adds="2887 2357 1215 3036 3702 2027 2211 8422", dels="1268 2371 2393 2439 2520 3592 5508 8299"),
    "2026-12": dict(may=False, roe_q=["2025/12", "2026/03", "2026/06", "2026/09"],
                    eps_now=["2026/03", "2026/06", "2026/09"], eps_prev=["2025/03", "2025/06", "2025/09"],
                    old_date=None, new_date=None, hold_month="2026/10"),
}


def trading_days(px):
    return sorted(px["date"].unique())


def schedule(tag, px, extra_days=()):
    """回傳 (截止日, 基準日, 生效日)。12 月的日期還沒到時，用平日推估。"""
    y, mo = int(tag[:4]), int(tag[5:])
    days = list(trading_days(px)) + [pd.Timestamp(d) for d in extra_days]
    days = sorted(set(days))
    future = pd.bdate_range(days[-1] + pd.Timedelta(days=1), periods=60)
    days = days + list(future)
    month = [d for d in days if d.year == y and d.month == mo]
    if mo == 5:
        cut, base = month[9], month[16]
    else:
        prev = [d for d in days if d.year == y and d.month == mo - 1]
        cut, base = prev[-1], month[6]
    eff = days[days.index(base) + 6]
    return cut, base, eff


def holdings(date):
    h = pd.read_csv(os.path.join(BASE, "data", "holdings.csv"), dtype=str, encoding="utf-8-sig")
    h = h[(h["etf"] == "00919") & (h["date"] == date) & (h["type"] == "stock")].copy()
    h["qty"] = pd.to_numeric(h["qty"]); h["weight"] = pd.to_numeric(h["weight"])
    return h.set_index("code")


def last_holdings_before(date):
    h = pd.read_csv(os.path.join(BASE, "data", "holdings.csv"), dtype=str, encoding="utf-8-sig")
    d = h[(h["etf"] == "00919") & (h["date"] <= date)]["date"].max()
    return d


def cap_weights(raw, cap):
    """權重上限（逐檔不同）＋下限，超出部分依未觸頂股票的權重比例重新分配。"""
    w = raw / raw.sum()
    for _ in range(100):
        over = w > cap + 1e-12
        if not over.any():
            break
        ex = (w[over] - cap[over]).sum()
        w[over] = cap[over]
        free = ~over & (w < cap - 1e-12)
        w[free] += ex * w[free] / w[free].sum()
    lo = w < W_MIN
    if lo.any():
        need = (W_MIN - w[lo]).sum()
        w[lo] = W_MIN
        hi = ~lo & (w > W_MIN)
        w[hi] -= need * w[hi] / w[hi].sum()
    for _ in range(50):
        t5 = w.nlargest(5).index
        s = w[t5].sum()
        if s <= TOP5_MAX + 1e-9:
            break
        ex = s - TOP5_MAX
        w[t5] *= TOP5_MAX / s
        rest = w.index.difference(t5)
        room = (cap[rest] - w[rest]).clip(lower=0)
        w[rest] += ex * w[rest] / w[rest].sum()
    return w / w.sum()


def run(tag="2026-05", opt=None, px_extra=None, div_extra=None, fin_extra=None, verbose=True):
    o = dict(weight="dy", growth_neg="exclude", g_cap=2.0, liq_window="1m_cal")
    o.update(opt or {})
    P = REVIEWS[tag]
    D = m.data()
    px, fin, div, ind, full = D["px"], D["fin"], D["div"], D["ind"], D["full"]
    if px_extra is not None:
        px = pd.concat([px, px_extra[~px_extra["date"].isin(px["date"].unique())]], ignore_index=True)
    if fin_extra is not None:
        fin = fin.combine_first(fin_extra)
    if div_extra is not None:
        div = pd.concat([div, div_extra], ignore_index=True)
    cut, base, eff = schedule(tag, px)
    if o.get("cut"):                       # 試算：截止日還沒到時改用最新資料日
        cut = pd.Timestamp(o["cut"])

    # --- 既有成分股 ---
    if P.get("adds"):
        new_ = holdings(P["new_date"])
        existing = (set(new_.index) - set(P["adds"].split())) | set(P["dels"].split())
        old = pd.DataFrame(index=sorted(existing), columns=["qty", "weight"], dtype=float)
        old_date = f"{P['new_date']} 持股反推"
    else:
        old_date = P["old_date"] or last_holdings_before(cut.strftime("%Y-%m-%d") if tag != "2026-12" else "2099-12-31")
        old = holdings(old_date)
        existing = set(old.index)

    # --- 母體：前 300 大 ---
    stocks = ind[ind["tse_ind"] != "存託憑證"].index
    snap = px[(px["date"] == cut) & px["code"].str.fullmatch(r"[1-9]\d{3}") & px["code"].isin(stocks)].set_index("code")
    snap = snap[~snap.index.isin(full.get(cut, set()))]
    snap = snap.sort_values("mcap", ascending=False)
    snap["市值名次"] = np.arange(1, len(snap) + 1)
    U = snap.head(300).copy()
    U["既有"] = U.index.isin(existing)

    # --- 流動性 ---
    h6 = px[px["code"].isin(U.index) & (px["date"] > cut - pd.DateOffset(months=6)) & (px["date"] <= cut)]
    h1 = h6[h6["date"] > cut - pd.DateOffset(months=1)]
    U["日均值1月"] = h1.groupby("code")["val"].mean()
    U["日均值6月"] = h6.groupby("code")["val"].mean()
    U["流動量指標"] = h6.groupby("code")["val"].median()
    U["日均成交值指標"] = U[["日均值1月", "日均值6月"]].min(axis=1)
    thr = np.where(U["既有"], LIQ_OLD, LIQ_NEW)
    liq = U["日均成交值指標"] > thr
    if liq.sum() < LIQ_MIN_N:
        extra = U[~liq].sort_values("日均成交值指標", ascending=False).index[:LIQ_MIN_N - liq.sum()]
        liq[extra] = True
    U["通過流動性"] = liq

    # --- ROE ---
    roe = fin["roe"].unstack("q").reindex(U.index).reindex(columns=P["roe_q"])
    U["近四季ROE%"] = roe.sum(axis=1, min_count=4)
    back = m.qshift(P["roe_q"], -1)
    roe_b = fin["roe"].unstack("q").reindex(U.index).reindex(columns=back).sum(axis=1, min_count=4)
    U["ROE用前一季"] = U["近四季ROE%"].isna() & roe_b.notna()
    U["近四季ROE%"] = U["近四季ROE%"].fillna(roe_b)
    U["通過ROE"] = U["近四季ROE%"] > 0

    # --- 股利 ---
    known = div[div["board"] <= cut]
    kg = {c: g.sort_values("board") for c, g in known.groupby("code")}
    dps, typ, latest_year, nmap = {}, {}, {}, {}
    for c in U.index:
        g = kg.get(c)
        if g is None or g.empty:
            dps[c], typ[c], latest_year[c] = 0.0, "", 0
            continue
        t = g.iloc[-1]["type"]
        # 配息次數用「最近兩個會計年度每年實際配幾次」判斷，不看 TEJ 標籤（光寶科半年配卻標 Q4、Q2）
        yc = g[g["cash"] > 0].groupby("year").size()
        n = int(min(4, max(1, yc.sort_index().tail(2).max()))) if len(yc) else 1
        dps[c] = g.tail(n)[o.get("dps_col", "cash")].sum()
        nmap[c] = n
        typ[c] = t
        latest_year[c] = max(int(y) for y in g["year"] if y)
    U["配息型態"] = pd.Series(typ)
    U["採用配息次數"] = pd.Series(nmap)
    U["近四季現金股利"] = pd.Series(dps)
    last_board = pd.Series({c: kg[c].iloc[-1]["board"] for c in U.index if c in kg and not kg[c].empty})
    sh = px[px["code"].isin(U.index) & (px["date"] <= cut)][["code", "date", "shares"]].dropna()
    ratio = {}
    for c, b in last_board.items():
        h_ = sh[(sh["code"] == c) & (sh["date"] <= b)]
        if len(h_):
            ratio[c] = h_.iloc[-1]["shares"] / U.loc[c, "shares"]
    ratio = pd.Series(ratio).reindex(U.index).fillna(1.0)
    ratio = ratio.where((ratio - 1).abs() > 0.05, 1.0)
    U["股數調整"] = ratio
    U["近四季現金股利"] = U["近四季現金股利"] * ratio
    U["股價"] = U["mcap"] * 1000 / U["shares"]
    U["股利率%"] = U["近四季現金股利"] / U["股價"] * 100

    if P["may"]:
        exd = m.ex_div_dates(pd.Timestamp(cut.year, 1, 1), px)
        exd = exd[(exd["date"] >= pd.Timestamp(cut.year, 1, 1)) & (exd["date"] < eff)]
        ex_set = set(exd["code"])
        annual = U["配息型態"] == "A0"
        no_decision = pd.Series({c: latest_year[c] < cut.year - 1 for c in U.index})
        U["5月排除"] = annual & (no_decision | U.index.isin(ex_set))
        U["排序值"] = U["股利率%"]
    else:
        U["5月排除"] = False
        en = fin["eps"].unstack("q").reindex(U.index)
        a = en.reindex(columns=P["eps_now"]).sum(axis=1, min_count=3)
        b = en.reindex(columns=P["eps_prev"]).sum(axis=1, min_count=3)
        a2 = en.reindex(columns=P["eps_now"][:2]).sum(axis=1, min_count=2)
        b2 = en.reindex(columns=P["eps_prev"][:2]).sum(axis=1, min_count=2)
        U["EPS用上半年"] = a.isna() & a2.notna()
        a, b = a.fillna(a2.where(a.isna())), b.where(a.notna() | a2.isna(), b2)
        b = np.where(U["EPS用上半年"], b2, b); b = pd.Series(b, index=U.index)
        g = (a - b) / b.abs()
        if o["growth_neg"] == "exclude":      # 去年同期 EPS ≤ 0：成長率無法定義，不列入排序
            g = g.where(b > 0)
        elif o["growth_neg"] == "zero":       # 去年同期 EPS ≤ 0：成長率當 0
            g = g.where(b > 0, 0.0)
        if o.get("g_cap") is not None:
            g = g.clip(upper=o["g_cap"])
        U["前三季EPS"] = a; U["去年前三季EPS"] = b; U["EPS成長率"] = g
        U["排序值"] = U["股利率%"] * (1 + g)

    U["合格"] = U["通過流動性"] & U["通過ROE"] & ~U["5月排除"] & (U["排序值"] > 0)
    C = U[U["合格"]].sort_values("排序值", ascending=False).copy()
    C["排名"] = np.arange(1, len(C) + 1)
    U["排名"] = C["排名"]

    # --- 緩衝區 ---
    rank = C["排名"]
    why = {}
    sel = list(C.index[rank <= TOP_IN])
    for c in sel:
        why[c] = "前 20 名直接納入"
    mid = C[(rank > TOP_IN) & (rank <= KEEP_OUT)]
    cand = list(mid.index[mid["既有"]]) + list(mid.index[~mid["既有"]])
    for c in cand:
        if len(sel) >= N:
            why.setdefault(c, "21～60 名候選，但 40 檔已滿")
            continue
        sel.append(c)
        why[c] = "21～60 名，既有成分股優先保留" if c in existing else "21～60 名，依排名補足 40 檔"
    rest = [c for c in C.index if c not in sel and rank[c] > KEEP_OUT and not C.loc[c, "既有"]]
    for c in rest:            # 名次 60 後只剩新股可補（理論上很少用到）
        if len(sel) >= N:
            break
        sel.append(c)
    note = []
    if not P["may"]:          # 12 月最多換 8 檔：保留排名最好的既有成分股
        new_in = [c for c in sel if c not in existing]
        if len(new_in) > 8:
            drop_new = sorted(new_in, key=lambda c: rank[c])[8:]
            keep_old = [c for c in C.index if c in existing and c not in sel]
            sel = [c for c in sel if c not in drop_new] + keep_old[:len(drop_new)]
            for c in drop_new:
                why[c] = "新股超過 8 檔上限，排名較差未納入"
            for c in keep_old[:len(drop_new)]:
                why[c] = "排名在 40 名外，但受 8 檔換股上限保留（既有成分股中排名較好）"
            note.append(f"換股超過 8 檔，刪掉排名最差的 {len(drop_new)} 檔新股")
    # 市值限制：至少 7 檔前 50 大、20 檔前 150 大
    for lim, need in ((50, 7), (150, 20)):
        for _ in range(40):
            big = [c for c in sel if U.loc[c, "市值名次"] <= lim]
            if len(big) >= need:
                break
            # 補進：依排名；換出：排名最差的新股優先，既有成分股最後
            add = [c for c in C.index if c not in sel and U.loc[c, "市值名次"] <= lim]
            out = [c for c in sorted(sel, key=lambda c: -rank[c]) if U.loc[c, "市值名次"] > lim]
            out = [c for c in out if c not in existing] + [c for c in out if c in existing]
            if not add or not out:
                break
            sel.remove(out[0]); sel.append(add[0])
            why[out[0]] = f"市值前 {lim} 大檔數不足，被換出"; why[add[0]] = f"市值前 {lim} 大檔數不足，補進"
            note.append(f"市值前 {lim} 大不足：{out[0]} 換成 {add[0]}")
    U["預測入選"] = U.index.isin(sel)
    for c in existing:
        if c in why:
            continue
        if c not in U.index:
            why[c] = "不在前 300 大母體"
        elif not U.loc[c, "通過流動性"]:
            why[c] = "流動性未達門檻"
        elif not U.loc[c, "通過ROE"]:
            why[c] = "近四季 ROE ≤ 0"
        elif U.loc[c, "5月排除"]:
            why[c] = "5 月排除（年配股未決議或生效前已除息）"
        elif not U.loc[c, "合格"]:
            why[c] = "排序值無法計算或 ≤ 0（去年同期 EPS ≤ 0 等）"
        elif c not in sel:
            why[c] = f"排名 {int(rank[c])}，刪除" + ("（61 名後）" if P["may"] else "（8 檔上限內排名最差的既有成分股）")

    # --- 權重 ---
    S = U.loc[sel].copy()
    stock_amt = (old["qty"] * U["股價"].reindex(old.index)).sum() if old["qty"].notna().any() else 0
    aum = stock_amt / (old["weight"].sum() / 100) if stock_amt > 0 else np.nan
    pre = math.ceil(aum * 1.2 / AUM_STEP) * AUM_STEP if aum == aum else np.nan
    hold = m.load_hold(P["hold_month"]) if P["hold_month"] in set(m.read_csv("HOLD.csv").iloc[:, 2]) else m.load_hold("2026/09")
    S["自由流通"] = hold["ff"].reindex(S.index).fillna(1)
    S["發行市值(元)"] = S["mcap"] * 1e6
    cap = pd.concat([pd.Series(W_MAX, index=S.index), 0.06 * S["發行市值(元)"] / pre,
                     0.15 * S["發行市值(元)"] * S["自由流通"] / pre], axis=1).min(axis=1)
    raw = {"dy": S["排序值"], "dy_raw": S["股利率%"],
           "dy_liq": S["排序值"] * S["流動量指標"],
           "dy_sqrtliq": S["排序值"] * np.sqrt(S["流動量指標"])}[o["weight"]]
    S["權重上限"] = cap
    S["預測權重%"] = cap_weights(raw.clip(lower=1e-9), cap) * 100
    U["預測權重%"] = S["預測權重%"]

    res = dict(U=U, C=C, S=S, sel=sel, why=why, P=P, o=o, fin=fin, div=div, existing=existing, cut=cut, base=base, eff=eff, aum=aum, pre=pre,
               note=note, old_date=old_date)
    if P["new_date"]:
        new = holdings(P["new_date"])
        res["new"] = new
        hit = len(set(sel) & set(new.index))
        aw = new["weight"] / new["weight"].sum() * 100
        both = S.index.intersection(aw.index)
        res["hit"] = hit
        res["mae"] = (S["預測權重%"].reindex(aw.index).fillna(0) - aw).abs().mean()
        res["corr"] = S.loc[both, "預測權重%"].corr(aw[both])
    if verbose:
        print(f"== {tag}  截止 {cut.date()}  基準日 {base.date()}  生效 {eff.date()}  舊成分股取自 {old_date}")
        print(f"   母體 300、通過流動性 {int(U['通過流動性'].sum())}、ROE>0 {int(U['通過ROE'].sum())}、"
              f"5月排除 {int(U['5月排除'].sum())}、合格 {len(C)}；基金規模約 {aum/1e8:,.0f} 億、預擬 {pre/1e8:,.0f} 億")
        for n_ in note:
            print("  ", n_)
        if "hit" in res:
            print(f"   命中 {res['hit']}/40、權重 MAE {res['mae']:.2f}pp、相關 {res['corr']:.3f}")
    return res




# ---- 12 月試算：TEJ 還沒更新的部分用官方 OpenAPI、每日收盤行情補 -------------------
import json
import requests

OPENAPI = {"L": "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_{}",
           "O": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_{}"}
KINDS = ["ci", "fh", "bd", "basi", "ins", "mim"]


def _get(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    try:
        return r.json()
    except ValueError:
        return []


def fetch_q_income(cache=True):
    """最新一季累計損益（上市＋上櫃各業別）：代號、年度、季別、累計 EPS、累計歸屬母公司淨利(千元)。"""
    path = os.path.join(m.PD, "openapi_income_latest.json")
    if cache and os.path.exists(path) and pd.Timestamp(os.path.getmtime(path), unit="s").date() == pd.Timestamp.now().date():
        rows = json.load(open(path, encoding="utf-8"))
    else:
        rows = []
        for mk, u in OPENAPI.items():
            for k in KINDS:
                for r in _get(u.format(k)):
                    code = r.get("公司代號") or r.get("SecuritiesCompanyCode")
                    yr, se = (r.get("年度") or r.get("Year")), (r.get("季別") or r.get("Season"))
                    if not code or not yr or not se:
                        continue
                    eps = next((v for kk, v in r.items() if kk.startswith("基本每股盈餘")), "")
                    ni = next((v for kk, v in r.items() if kk.startswith("淨利（淨損）歸屬於母公司業主")), "")
                    rows.append(dict(code=code, year=int(yr) + 1911, season=int(se), eps=eps, ni=ni, kind=k, market=mk))
        json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    d = pd.DataFrame(rows)
    d["eps"] = pd.to_numeric(d["eps"], errors="coerce"); d["ni"] = pd.to_numeric(d["ni"], errors="coerce")
    return d.drop_duplicates("code").set_index("code")


def fetch_twse_div(after, upto):
    """上市公司股利分派（董事會決議日在 after～upto 之間），轉成 TEJ 股利表的格式。"""
    rows = []
    for r in _get("https://openapi.twse.com.tw/v1/opendata/t187ap45_L"):
        b = r.get("董事會（擬議）股利分派日", "")
        if len(b) != 7:
            continue
        board = pd.Timestamp(int(b[:3]) + 1911, int(b[3:5]), int(b[5:]))
        if not (after < board <= upto):
            continue
        num = lambda k: float(r.get(k) or 0)
        cash = num("股東配發-盈餘分配之現金股利(元/股)") + num("股東配發-法定盈餘公積發放之現金(元/股)") + num("股東配發-資本公積發放之現金(元/股)")
        per = r.get("股利所屬年(季)度", "")
        typ = {"年度": "A0", "上半年": "H1", "下半年": "H2", "第1季": "Q1", "第2季": "Q2", "第3季": "Q3", "第4季": "Q4"}.get(per, "A0")
        rows.append(dict(code=r["公司代號"], name=r.get("公司名稱", ""), meet=pd.NaT, kind="D", type=typ, cash=cash, board=board,
                         year=str(int(r["股利年度"]) + 1911), cash_e=num("股東配發-盈餘分配之現金股利(元/股)"),
                         cash_c=num("股東配發-資本公積發放之現金(元/股)")))
    return pd.DataFrame(rows)


def daily_px(after):
    """etf_tracker 每日收盤行情（data/prices.csv）轉成 TEJ 股價表格式；成交值用 收盤×成交股數 近似。"""
    p = pd.read_csv(os.path.join(BASE, "data", "prices.csv"), dtype=str, encoding="utf-8-sig")
    p = p[p["date"] > after].copy()
    for c in ["close", "shares", "volume"]:
        p[c] = pd.to_numeric(p[c], errors="coerce")
    p["date"] = pd.to_datetime(p["date"])
    out = pd.DataFrame(dict(code=p["code"], name="", date=p["date"], adj=np.nan, vol=p["volume"] / 1000,
                            val=p["close"] * p["volume"] / 1000, turn=np.nan, shares=p["shares"] / 1000,
                            mcap=p["close"] * p["shares"] / 1e6, raw=p["close"]))
    return out.dropna(subset=["mcap"])


def december_extras(cut):
    D = m.data()
    tej_last = D["px"]["date"].max()
    px_x = daily_px(tej_last.strftime("%Y-%m-%d"))
    px_x = px_x[px_x["date"] <= cut]
    names = D["px"].drop_duplicates("code", keep="last").set_index("code")["name"]
    px_x["name"] = px_x["code"].map(names).fillna("")
    div_x = fetch_twse_div(D["div"]["board"].max(), cut)
    inc = fetch_q_income()
    inc = inc[(inc["year"] == 2026) & (inc["season"] == 3)]
    fin = D["fin"]
    # Q3 單季：EPS = 累計 − TEJ Q1、Q2；ROE ≈ Q3 淨利 ÷ 6 月底母公司權益
    e12 = fin["eps"].unstack("q").reindex(inc.index)[["2026/03", "2026/06"]]
    n12 = fin["ni"].unstack("q").reindex(inc.index)[["2026/03", "2026/06"]]
    eq = fin["eq_parent"].unstack("q").reindex(inc.index)["2026/06"]
    q3 = pd.DataFrame(index=inc.index)
    q3["eps"] = inc["eps"] - e12.sum(axis=1, min_count=2)
    q3["roe"] = (inc["ni"] - n12.sum(axis=1, min_count=2)) / eq * 100
    tej_has = fin.xs("2026/09", level="q").index if "2026/09" in fin.index.get_level_values("q") else []
    q3 = q3[~q3.index.isin(tej_has)].dropna(how="all")
    q3["q"] = "2026/09"
    fin_x = q3.reset_index().rename(columns={"index": "code"}).set_index(["code", "q"])[["eps", "roe"]]
    return px_x, div_x, fin_x


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
    if tag == "2026-12":
        cut = pd.Timestamp(sys.argv[2]) if len(sys.argv) > 2 else None
        cut = cut or pd.Timestamp(pd.read_csv(os.path.join(BASE, "data", "prices.csv"), usecols=["date"])["date"].max())
        px_x, div_x, fin_x = december_extras(cut)
        print(f"補充資料：股價到 {px_x['date'].max().date()}、11 月股利決議 {len(div_x)} 筆、Q3 財報 {len(fin_x)} 檔")
        r = run(tag, dict(cut=cut), px_extra=px_x, div_extra=div_x, fin_extra=fin_x)
    else:
        r = run(tag)
