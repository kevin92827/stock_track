"""00919 預測的檢查用 Excel：每一步一個工作頁，第一列就是欄位名稱。

02～06 頁的列順序都一樣（母體 300 檔，依市值名次），同一列就是同一檔股票，可以橫向對照。
原始資料是數值（藍字），判斷、比率、排名用 Excel 公式（黑字），引用別頁是綠字；
改「00_參數」的黃底儲存格，流動性、成長率上限、排名會跟著重算。
緩衝區、權重是 Python 算好的數值（規則有先後順序，公式寫不出來），每檔寫上理由。
說明另外寫在 00919_檢查說明.xlsx。

用法：python audit_00919.py 2026-05 | 2025-12 | 2026-12 [試算截止日]
"""
import os
import sys

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import predict_00940 as m
import predict_00919 as p

FONT = "Arial"
BLUE, BLACK, GREEN = "0000FF", "000000", "008000"
HEAD = PatternFill("solid", fgColor="1F4E78")
PARAM = PatternFill("solid", fgColor="FFFF00")
OK = PatternFill("solid", fgColor="C6EFCE")
NG = PatternFill("solid", fgColor="FFC7CE")
ADD = PatternFill("solid", fgColor="E2EFDA")
DEL = PatternFill("solid", fgColor="FCE4D6")


def v(x):
    if x is None:
        return None
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if np.isnan(x) else float(x)
    if isinstance(x, pd.Timestamp):
        return x.strftime("%Y-%m-%d")
    return x


def sheet(wb, name, cols, rows, fmt=None, widths=None, bool_cols=()):
    """cols：欄位名稱；rows：每列的值（以 = 開頭是公式）。公式引用別頁就用綠字。"""
    ws = wb.create_sheet(name)
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 34
    for r in rows:
        ws.append([v(x) for x in r])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            val = c.value
            color = BLACK if isinstance(val, str) and val.startswith("=") else BLUE
            if isinstance(val, str) and val.startswith("=") and "!" in val:
                color = GREEN
            if c.column <= 2:
                color = BLACK
            c.font = Font(name=FONT, color=color)
    n = ws.max_row
    for j, f in (fmt or {}).items():
        for i in range(2, n + 1):
            ws.cell(i, j).number_format = f
    for j, w in enumerate(widths or [], 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    for j in bool_cols:
        L = get_column_letter(j)
        rng = f"{L}2:{L}{n}"
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f"{L}2=TRUE"], fill=OK))
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f"{L}2=FALSE"], fill=NG))
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{n}"
    return ws


def build(tag, cut=None):
    if tag == "2026-12":
        cut = pd.Timestamp(cut) if cut else pd.Timestamp(pd.read_csv(os.path.join(p.BASE, "data", "prices.csv"), usecols=["date"])["date"].max())
        px_x, div_x, fin_x = p.december_extras(cut)
        r = p.run(tag, dict(cut=cut), px_extra=px_x, div_extra=div_x, fin_extra=fin_x, verbose=False)
        variants = [dict(growth_neg="exclude", g_cap=c) for c in (1.0, 2.0, 3.0, None)] + [dict(growth_neg="zero", g_cap=2.0)]
        var_runs = [(vv, p.run(tag, dict(cut=cut, **vv), px_extra=px_x, div_extra=div_x, fin_extra=fin_x, verbose=False)) for vv in variants]
    else:
        r = p.run(tag, verbose=False)
        var_runs = []
    U, C, S, P, o, fin, div = r["U"], r["C"], r["S"], r["P"], r["o"], r["fin"], r["div"]
    ex, sel, why = r["existing"], set(r["sel"]), r["why"]
    cutd = r["cut"]
    new = r.get("new")
    D = m.data()
    px = D["px"] if tag != "2026-12" else pd.concat([D["px"], px_x], ignore_index=True)
    full = D["full"].get(cutd, set())

    wb = Workbook()
    wb.remove(wb.active)
    PS = "'00_參數'!"

    # 00 參數
    prm = [("新股流動性門檻(千元)", p.LIQ_NEW, "日均成交值指標要大於此值；8,000 萬元 = 80,000 千元"),
           ("既有成分股流動性門檻(千元)", p.LIQ_OLD, "6,000 萬元"),
           ("EPS 成長率上限(倍)", o.get("g_cap"), "空白 = 不設上限；2 = 最多 +200%（只影響 12 月）"),
           ("前 X 名直接納入", p.TOP_IN, "緩衝區由 Python 計算，改這裡不會重算 07 頁"),
           ("既有成分股刪除名次(大於)", p.KEEP_OUT, "同上"),
           ("成分股檔數", p.N, "同上"),
           ("12 月換股上限", 8, "同上"),
           ("資料截止日", cutd.strftime("%Y-%m-%d"), "試算版用最新資料日" if tag == "2026-12" else "5 月第 10 個交易日 / 11 月最後交易日")]
    ws = sheet(wb, "00_參數", ["參數", "值", "說明"], prm, widths=[28, 14, 60])
    for i in range(2, 2 + len(prm)):
        ws.cell(i, 2).fill = PARAM
        ws.cell(i, 2).font = Font(name=FONT, color=BLUE)

    # 01 母體（截止日市值前 340 名，含被排除的）
    stocks = D["ind"][D["ind"]["tse_ind"] != "存託憑證"].index
    snap = px[(px["date"] == cutd) & px["code"].str.fullmatch(r"[1-9]\d{3}") & px["code"].isin(stocks)].set_index("code")
    snap = snap[~snap.index.duplicated()].sort_values("mcap", ascending=False)
    names = D["px"].drop_duplicates("code", keep="last").set_index("code")["name"]
    rows, k = [], 0
    for c in snap.index[:340]:
        excl = c in full
        k += 0 if excl else 1
        i = len(rows) + 2
        rows.append([c, names.get(c, ""), snap.loc[c, "mcap"] / 100, excl, None if excl else k,
                     f'=AND(NOT(D{i}),E{i}<=300)', c in ex])
    sheet(wb, "01_母體", ["代號", "名稱", "截止日發行市值(億)", "全額交割或變更交易", "排除後市值名次", "前300大", "既有成分股"],
          rows, fmt={3: "#,##0"}, widths=[8, 12, 14, 12, 10, 10, 10], bool_cols=(6,))

    order = list(U.sort_values("市值名次").index)
    R = {c: i + 2 for i, c in enumerate(order)}
    nrow = len(order) + 1

    # 02 流動性
    rows = []
    for c in order:
        i = R[c]
        rows.append([c, U.loc[c, "name"], c in ex, U.loc[c, "日均值1月"], U.loc[c, "日均值6月"],
                     f"=MIN(D{i},E{i})", f"=IF(C{i},{PS}$B$3,{PS}$B$2)", f"=F{i}>G{i}"])
    sheet(wb, "02_流動性", ["代號", "名稱", "既有成分股", "近1月日均成交值(千元)", "近6月日均成交值(千元)", "日均成交值指標(千元)", "門檻(千元)", "通過流動性"],
          rows, fmt={4: "#,##0", 5: "#,##0", 6: "#,##0", 7: "#,##0"}, widths=[8, 12, 10, 14, 14, 14, 12, 10], bool_cols=(8,))

    # 03 ROE
    roeq = fin["roe"].unstack("q")
    rows = []
    for c in order:
        i = R[c]
        qs = m.qshift(P["roe_q"], -1) if bool(U.loc[c].get("ROE用前一季", False)) else P["roe_q"]
        vals = [roeq.at[c, q] if (c in roeq.index and q in roeq.columns) else None for q in qs]
        rows.append([c, U.loc[c, "name"], "、".join(qs)] + vals + [f'=IF(COUNT(D{i}:G{i})=4,SUM(D{i}:G{i}),"")', f'=AND(H{i}<>"",N(H{i})>0)'])
    sheet(wb, "03_ROE", ["代號", "名稱", "採用季別", "第1季ROE%", "第2季ROE%", "第3季ROE%", "第4季ROE%", "近四季ROE%", "通過ROE"],
          rows, fmt={k: "0.00" for k in range(4, 9)}, widths=[8, 12, 34, 10, 10, 10, 10, 11, 9], bool_cols=(9,))

    # 04 股利
    rows = []
    for c in order:
        i = R[c]
        adj = U.loc[c, "股數調整"]
        raw = U.loc[c, "近四季現金股利"] / adj if adj else U.loc[c, "近四季現金股利"]
        rows.append([c, U.loc[c, "name"], U.loc[c, "配息型態"], U.loc[c, "採用配息次數"], raw, adj,
                     f"=E{i}*F{i}", U.loc[c, "股價"], f'=IF(H{i}>0,G{i}/H{i}*100,"")'])
    sheet(wb, "04_股利", ["代號", "名稱", "最新配息型態(TEJ)", "每年配息次數(採用最近幾次)", "最近幾次現金股利合計(元)", "股數調整係數",
                         "近四季現金股利(元)", "截止日股價", "股利率%"],
          rows, fmt={5: "0.000", 6: "0.000", 7: "0.000", 8: "#,##0.00", 9: "0.00"}, widths=[8, 12, 12, 14, 14, 11, 13, 11, 9])

    # 05 EPS 成長（12 月）或 5 月排除
    if P["may"]:
        exd = m.ex_div_dates(pd.Timestamp(cutd.year, 1, 1), px)
        exd = exd[(exd["date"] >= pd.Timestamp(cutd.year, 1, 1)) & (exd["date"] < r["eff"])].groupby("code")["date"].min()
        known = div[div["board"] <= cutd]
        ly = known.groupby("code")["year"].apply(lambda s: max(int(y) for y in s if y))
        rows = []
        for c in order:
            i = R[c]
            rows.append([c, U.loc[c, "name"], U.loc[c, "配息型態"], f'=C{i}="A0"', ly.get(c), f"=N(E{i})>={cutd.year - 1}",
                         exd.get(c), f'=G{i}<>""', f"=AND(D{i},OR(NOT(F{i}),H{i}))"])
        sheet(wb, "05_5月排除", ["代號", "名稱", "最新配息型態(TEJ)", "年配股", "截止日前已決議的最新股利年度", "已決議去年度股利",
                               f"生效日({r['eff'].date()})前除息日", "生效日前已除息", "5月排除"],
              rows, widths=[8, 12, 12, 8, 14, 12, 14, 12, 9], bool_cols=())
        val_ref = lambda i: f"=IF('05_5月排除'!I{i},\"\",'04_股利'!I{i})"
        val_name = "排序值(股利率%)"
    else:
        en = fin["eps"].unstack("q")
        rows = []
        for c in order:
            i = R[c]
            g = lambda q: en.at[c, q] if (c in en.index and q in en.columns) else None
            rows.append([c, U.loc[c, "name"]] + [g(q) for q in P["eps_now"]] + [g(q) for q in P["eps_prev"]] +
                        [bool(U.loc[c].get("EPS用上半年", False)),
                         f'=IF(I{i},IF(COUNT(C{i}:D{i})=2,C{i}+D{i},""),IF(COUNT(C{i}:E{i})=3,SUM(C{i}:E{i}),""))',
                         f'=IF(I{i},IF(COUNT(F{i}:G{i})=2,F{i}+G{i},""),IF(COUNT(F{i}:H{i})=3,SUM(F{i}:H{i}),""))',
                         f'=IF(OR(J{i}="",K{i}=""),"",IF(K{i}>0,(J{i}-K{i})/K{i},""))',
                         f'=IF(L{i}="","",IF({PS}$B$4="",L{i},MIN(L{i},{PS}$B$4)))',
                         f"='04_股利'!I{i}",
                         f'=IF(OR(M{i}="",N{i}=""),"",N{i}*(1+M{i}))'])
        y1, y0 = P["eps_now"][0][:4], P["eps_prev"][0][:4]
        sheet(wb, "05_EPS成長", ["代號", "名稱", f"{y1}Q1 EPS", f"{y1}Q2 EPS", f"{y1}Q3 EPS", f"{y0}Q1 EPS", f"{y0}Q2 EPS", f"{y0}Q3 EPS",
                               "Q3缺資料改用上半年", "今年累計EPS", "去年同期累計EPS", "EPS成長率", "成長率(上限後)", "股利率%", "預估股利率%"],
              rows, fmt={**{k: "0.00" for k in range(3, 9)}, 10: "0.00", 11: "0.00", 12: "0.0%", 13: "0.0%", 14: "0.00", 15: "0.00"},
              widths=[8, 12, 10, 10, 10, 10, 10, 10, 12, 11, 12, 11, 12, 9, 11])
        val_ref = lambda i: f"='05_EPS成長'!O{i}"
        val_name = "排序值(預估股利率%)"

    # 06 排名
    rows = []
    for c in order:
        i = R[c]
        rows.append([c, U.loc[c, "name"], U.loc[c, "市值名次"], c in ex, f"='02_流動性'!H{i}", f"='03_ROE'!I{i}", val_ref(i),
                     f'=AND(E{i},F{i},G{i}<>"",N(G{i})>0)',
                     f'=IF(H{i},COUNTIFS($H$2:$H${nrow},TRUE,$G$2:$G${nrow},">"&G{i})+1,"")',
                     f'=IF(I{i}="","不合格",IF(I{i}<={PS}$B$5,"前20名",IF(I{i}<={PS}$B$6,"21~60名","61名後")))',
                     v(U.loc[c, "排名"])])
    sheet(wb, "06_排名", ["代號", "名稱", "市值名次", "既有成分股", "通過流動性", "通過ROE", val_name, "合格", "排名(公式)", "區間", "排名(Python)"],
          rows, fmt={7: "0.00"}, widths=[8, 12, 9, 10, 10, 9, 14, 8, 10, 10, 11], bool_cols=(8,))

    # 07 緩衝區
    show = [c for c in C.index if c in sel or c in ex or (C.loc[c, "排名"] <= 60)]
    show += [c for c in ex if c not in C.index]
    rows = []
    for c in show:
        inn = c in sel
        res_ = ("保留" if c in ex else "納入") if inn else ("剔除" if c in ex else "未納入")
        rows.append([c, U["name"].get(c, names.get(c, "")), v(U["排名"].get(c)), v(U["市值名次"].get(c)), c in ex, res_,
                     why.get(c, "排名 60 名外、非既有成分股" if c not in sel else "")])
    ws = sheet(wb, "07_緩衝區", ["代號", "名稱", "排名", "市值名次", "既有成分股", "結果", "理由"], rows, widths=[8, 12, 7, 9, 10, 8, 62])
    for row in ws.iter_rows(min_row=2):
        f = {"納入": ADD, "剔除": DEL}.get(row[5].value)
        if f:
            for cc in row[:6]:
                cc.fill = f

    # 08 換股結果
    rows = []
    act_add = set(new.index) - ex if new is not None else set()
    act_del = ex - set(new.index) if new is not None else set()
    freq = {}
    if var_runs:
        for _, rr in var_runs:
            for c in rr["sel"]:
                freq[c] = freq.get(c, 0) + 1 / len(var_runs)
    for kind, codes, act in (("納入", sel - ex, act_add), ("剔除", ex - sel, act_del)):
        for c in sorted(codes, key=lambda c: (U["排名"].get(c, 999) if pd.notna(U["排名"].get(c, np.nan)) else 999)):
            rows.append([c, U["name"].get(c, names.get(c, "")), kind, v(U["排名"].get(c)), why.get(c, ""),
                         (freq.get(c, 0) if kind == "納入" else 1 - freq.get(c, 0)) if var_runs else None,
                         (c in act) if new is not None else None])
    if new is not None:
        for kind, codes in (("實際納入（預測漏掉）", act_add - sel), ("實際剔除（預測漏掉）", act_del & sel)):
            for c in sorted(codes):
                rows.append([c, U["name"].get(c, names.get(c, "")), kind, v(U["排名"].get(c)), why.get(c, ""), None, True])
    ws = sheet(wb, "08_換股結果", ["代號", "名稱", "動作", "排名", "理由", "各種解讀下同結果的比例", "實際也是如此"],
               rows, fmt={6: "0%"}, widths=[8, 12, 18, 7, 58, 12, 11], bool_cols=(7,))

    # 09 權重
    rows = []
    aw = (new["weight"] / new["weight"].sum() * 100) if new is not None else pd.Series(dtype=float)
    for c in S.sort_values("預測權重%", ascending=False).index:
        rows.append([c, S.loc[c, "name"], S.loc[c, "排序值"], S.loc[c, "自由流通"], S.loc[c, "mcap"] / 100,
                     S.loc[c, "權重上限"] * 100, S.loc[c, "預測權重%"], v(aw.get(c)) if new is not None else None])
    sheet(wb, "09_權重", ["代號", "名稱", "排序值", "自由流通比例", "發行市值(億)", "權重上限%", "預測權重%", "實際權重%"],
          rows, fmt={3: "0.00", 4: "0.0%", 5: "#,##0", 6: "0.00", 7: "0.00", 8: "0.00"}, widths=[8, 12, 9, 11, 12, 10, 10, 10])

    # 10 敏感度（12 月）
    if var_runs:
        rows = []
        for vv, rr in var_runs:
            e = rr["existing"]
            nm = lambda c: f"{c} {U['name'].get(c, names.get(c, ''))}"
            rows.append([f"基期≤0：{'排除' if vv['growth_neg'] == 'exclude' else '成長率當0'}",
                         "不設上限" if vv["g_cap"] is None else f"{vv['g_cap']:g} 倍",
                         "、".join(nm(c) for c in sorted(set(rr["sel"]) - e)), "、".join(nm(c) for c in sorted(e - set(rr["sel"])))])
        sheet(wb, "10_敏感度", ["去年同期EPS≤0的處理", "成長率上限", "納入", "剔除"], rows, widths=[20, 12, 80, 80])
    out = os.path.join(m.PD, f"00919_檢查_{tag}.xlsx")
    try:
        wb.save(out)
    except PermissionError:
        out = out.replace(".xlsx", f"_{pd.Timestamp.now():%m%d_%H%M}.xlsx")
        wb.save(out)
    return out, r


def build_doc(results):
    wb = Workbook()
    ws = wb.active
    ws.title = "01_計算流程"
    flow = [
        ("0", "時程", "—", "5 月：資料截至第 10 個交易日、基準日第 17 個交易日。12 月：資料截至 11 月最後交易日、基準日 12 月第 7 個交易日。基準日後間隔 5 個交易日生效（之後 8 個交易日過渡期）。"),
        ("1", "母體", "01_母體", "上市櫃普通股（排除存託憑證），排除截止日全額交割／變更交易，依截止日發行市值取前 300 大。"),
        ("2", "流動性", "02_流動性", "日均成交值指標 = min(近 1 月日均成交值, 近 6 月日均成交值)。新股 > 8,000 萬、既有成分股 > 6,000 萬。通過不足 150 檔時依指標補滿（三期都沒觸發）。"),
        ("3", "ROE", "03_ROE", "截止日已公布的近四季單季 ROE 加總 > 0。最新一季還沒資料的公司退回前一組四季。"),
        ("4", "股利率", "04_股利", "截止日前董事會已決議的現金股利（盈餘＋公積發的現金）。近四季 = 最近 n 次配息，n = 最近兩年每年實際配息次數（1、2、4）。股利宣告後股數變動 > 5%（分割、面額變更）依比例換算。股利率 = 近四季現金股利 ÷ 截止日股價。"),
        ("5a", "5 月排除（5 月）", "05_5月排除", "年配股：截止日前還沒決議去年度股利，或生效日前已除息 → 排除。"),
        ("5b", "預估股利率（12 月）", "05_EPS成長", "EPS 成長率 = 今年前三季累計 ÷ 去年前三季累計 − 1；去年同期 ≤ 0 不列入排序；成長率上限預設 2 倍；Q3 缺資料用上半年對上半年。預估股利率 = 股利率 × (1 + 成長率)。"),
        ("6", "排名", "06_排名", "合格 = 通過流動性 + ROE > 0 + 排序值 > 0（5 月另扣除 5 月排除）。依排序值由高到低排名。"),
        ("7", "緩衝區", "07_緩衝區", "① 前 20 名納入 ② 21～60 名既有成分股優先，再依排名補新股到 40 檔 ③ 既有 61 名後刪除 ④ 12 月最多換 8 檔：只留排名最好的 8 檔新股，空位由排名最好的既有成分股保留 ⑤ 至少 7 檔前 50 大、20 檔前 150 大：不足時換掉排名最差的不符股（新股先換），補排名最好的符合股。"),
        ("8", "權重", "09_權重", "依排序值比例；單檔 ≤ 15%、投資金額 ≤ 發行市值 6% 及自由流通市值 15%（預擬規模 = 基金規模 × 1.2 進位到 250 億倍數）、前五大 ≤ 65%、下限 0.5%。「依流動量指標調整」方法不明，目前沒用。"),
        ("9", "信心", "08_換股結果、10_敏感度", "12 月用 5 種成長率處理方式各算一次，統計同結果的比例。"),
    ]
    _table(ws, ["步驟", "項目", "工作頁", "內容"], flow, [6, 18, 22, 110])

    ws = wb.create_sheet("02_欄位說明")
    cols = [
        ("00_參數", "值", "輸入", "黃底，可改；改 1～3 列會影響 02、05、06 頁的公式"),
        ("01_母體", "截止日發行市值(億)", "原始", "TEJ 市值；TEJ 之後的日期用每日收盤行情 收盤 × 發行股數"),
        ("01_母體", "全額交割或變更交易", "原始", "TEJ 交易屬性（MK25/MK26）截止日"),
        ("01_母體", "前300大", "公式", "=AND(NOT(全額交割), 排除後市值名次<=300)"),
        ("02_流動性", "近1月／近6月日均成交值", "原始", "截止日往前 1 個月、6 個月的每日成交值平均；TEJ 之後用 收盤 × 成交股數 近似"),
        ("02_流動性", "日均成交值指標", "公式", "=MIN(近1月, 近6月)"),
        ("02_流動性", "門檻", "公式", "=IF(既有成分股, 參數B3, 參數B2)"),
        ("02_流動性", "通過流動性", "公式", "=指標 > 門檻"),
        ("03_ROE", "第1～4季ROE%", "原始", "TEJ ROE21_26 單季 ROE；12 月試算的 2026Q3 = API 累計淨利 − TEJ Q1、Q2 淨利，除以 6 月底母公司權益"),
        ("03_ROE", "近四季ROE%", "公式", "=四季都有資料時 SUM，否則空白"),
        ("04_股利", "每年配息次數", "原始", "最近兩個股利年度每年實際配息筆數的最大值（1～4）"),
        ("04_股利", "最近幾次現金股利合計", "原始", "TEJ 股利表（EA_split）董事會日 ≤ 截止日、依董事會日排序取最後 n 筆；12 月試算的 11 月決議來自證交所股利 API"),
        ("04_股利", "股數調整係數", "原始", "最後一次股利董事會日的股數 ÷ 截止日股數；變動 ≤ 5% 時設 1"),
        ("04_股利", "股利率%", "公式", "=近四季現金股利 ÷ 截止日股價 × 100"),
        ("05_5月排除", "年配股、已決議、已除息、5月排除", "公式", "年配股 = 型態 A0；已決議 = 最新股利年度 ≥ 去年；5月排除 = 年配股 且 (未決議 或 生效日前已除息)。除息日：corp_actions，2025 年以前用還原價跳動推算"),
        ("05_EPS成長", "各季 EPS", "原始", "TEJ PL21_26 單季 EPS；2026Q3 = API 累計 EPS − TEJ Q1、Q2"),
        ("05_EPS成長", "EPS成長率", "公式", "=去年同期 > 0 時 (今年 − 去年) ÷ 去年，否則空白（不列入排序）"),
        ("05_EPS成長", "成長率(上限後)", "公式", "=MIN(成長率, 參數B4)；B4 空白 = 不設上限"),
        ("05_EPS成長", "預估股利率%", "公式", "=股利率 × (1 + 成長率上限後)"),
        ("06_排名", "合格", "公式", "=AND(通過流動性, 通過ROE, 排序值<>空白, 排序值>0)"),
        ("06_排名", "排名(公式)", "公式", "=合格時 COUNTIFS(合格=TRUE, 排序值>本檔) + 1"),
        ("06_排名", "排名(Python)", "原始", "模型算的排名，用來對照公式結果"),
        ("07_緩衝區", "結果、理由", "Python", "依步驟 7 的順序判斷，每檔寫出觸發的規則"),
        ("08_換股結果", "各種解讀下同結果的比例", "Python", "12 月：5 種成長率處理方式中，跟基準結果相同的比例"),
        ("08_換股結果", "實際也是如此", "原始", "考古題：比對換股後的實際持股"),
        ("09_權重", "權重上限%", "Python", "min(15%, 發行市值×6% ÷ 預擬規模, 自由流通市值×15% ÷ 預擬規模)"),
        ("09_權重", "實際權重%", "原始", "換股後 00919 持股權重（重新加總到 100%）"),
    ]
    _table(ws, ["工作頁", "欄位", "類型", "來源或公式"], cols, [14, 30, 8, 110])

    ws = wb.create_sheet("03_假設說明")
    asm = [
        ("規則版本", "2025/6 起：40 檔、20／60 名緩衝區、既有 6,000 萬、7 檔前 50 大、20 檔前 150 大、12 月最多換 8 檔、單檔 15%。2025/12、2026/5 兩期都在改版後。更早的考古題要用舊規則（30 檔、15／45 名、單檔 10%）。"),
        ("近四季現金股利", "用董事會決議的金額（含公積發放的現金），最近 n 次加總。測過只算盈餘分配：2026/5 可寧衛變對、寶成和來億變錯，命中數不變。"),
        ("去年同期 EPS ≤ 0", "不列入排序。依據：2025/12 群創（去年 −0.28）、友達（去年 −0.61）實際都沒入選；改成這樣命中 35 → 36。"),
        ("成長率上限", "預設 2 倍。依據：2025/12 南電成長 37 倍（基期 0.03 元）沒入選；上限 1、2、3 倍都能解釋，分不出來。12 月試算用 5 種處理方式算信心。"),
        ("市值限制的換出順序", "排名最差、不符合條件的新股先換，既有成分股最後；補進依排名。依據：2026/5 至上、裕民（既有）實際保留。"),
        ("8 檔換股上限", "超過時只留排名最好的 8 檔新股，由排名最好的既有成分股補回。"),
        ("權重的流動量調整", "說明書沒寫方法；5 月考古題加或不加結果一樣，所以沒用。"),
        ("12 月試算資料", "截止日暫用最新資料日；Q3 EPS 用證交所／櫃買 OpenAPI（部分金控還沒有，用上半年）；11 月股利只有上市公司；寶雅面額分割已換算。"),
    ]
    _table(ws, ["項目", "說明"], asm, [20, 130])

    ws = wb.create_sheet("04_考古題錯誤分析")
    rows = []
    for tag, r in results:
        if r.get("new") is None:
            continue
        U, sel, ex, new = r["U"], set(r["sel"]), r["existing"], set(r["new"].index)
        rows.append((tag, "命中", f"{len(sel & new)}/40", "", ""))
        for c in sorted(sel - new):
            rows.append((tag, "多選（預測有、實際沒有）", f"{c} {U['name'].get(c, '')}", v(U["排名"].get(c)), NOTES.get((tag, c), "")))
        for c in sorted(new - sel):
            rows.append((tag, "漏掉（實際有、預測沒有）", f"{c} {U['name'].get(c, '')}", v(U["排名"].get(c)), NOTES.get((tag, c), "")))
    _table(ws, ["期別", "類型", "股票", "模型排名", "研究結果"], rows, [10, 22, 16, 10, 110])
    tested = [
        ("股利率只算盈餘分配（排除公積）", "兩期命中數不變（2026/5 錯的股票換了）"),
        ("成長率上限 1、2、3 倍、不設上限", "2025/12 皆 36/40；只差在南電或聯強誰被多選"),
        ("去年同期 EPS ≤ 0 當 0 而不排除", "群創仍入選，比排除差"),
        ("規則版本不同", "兩期都在 2025/6 改版後，不適用"),
        ("投資金額上限讓股票裝不下", "亞泥、寶成的上限都在 1% 以上，不是原因"),
        ("市值限制補進時既有成分股優先", "會拉回陽明（66 名）、漢唐（71 名），命中 37 → 35，不採用"),
    ]
    r0 = ws.max_row + 2
    ws.cell(r0, 1, "測過的假設").font = Font(name=FONT, bold=True)
    for k_, (a, b) in enumerate(tested, r0 + 1):
        ws.cell(k_, 1, a).font = Font(name=FONT)
        ws.cell(k_, 3, b).font = Font(name=FONT)
    out = os.path.join(m.PD, "00919_檢查說明.xlsx")
    try:
        wb.save(out)
    except PermissionError:
        out = out.replace(".xlsx", f"_{pd.Timestamp.now():%m%d_%H%M}.xlsx")
        wb.save(out)
    return out


NOTES = {
    ("2026-05", "1102"): "股利率排第 8，所有篩選都通過，找不到排除原因。證交所把它的股利標成「第 4 季」，TEJ 標年配；指數公司對配息頻率的認定可能不同。",
    ("2026-05", "8422"): "市值排第 291 名，可能在官方資料裡掉出前 300；另外有 0.2 元資本公積配息，只算盈餘配息時會變對。",
    ("2026-05", "2886"): "市值前 50 大不足時補進來的。官方補的是台新新光金、凱基金。",
    ("2026-05", "2887"): "排名 63，官方為了市值前 50 大保留它。我的模型補位依排名，選了兆豐金（52 名）。",
    ("2026-05", "2883"): "我的資料市值排第 51 名，不算前 50 大，所以沒被補進；官方應該算它在前 50。",
    ("2026-05", "2855"): "因市值前 150 大不足被換出（排名最差的非前 150 新股）；官方把亞泥的位置留給它。",
    ("2025-12", "2382"): "排名 21，前 50 大不足時補進。官方補的是台新新光金（我算 135 名）。",
    ("2025-12", "2887"): "我算排名 135，官方卻納入。可能是合併（2025/7 台新、新光合併）後的 EPS 或股利基期跟 TEJ 不同。",
    ("2025-12", "2347"): "8 檔新股名額中排第 8；官方選了文曄、可寧衛。",
    ("2025-12", "2006"): "同上，排名 17 的新股；官方沒選。",
    ("2025-12", "2371"): "既有成分股排名 158，我因 8 檔上限保留它、刪材料-KY（178 名）；官方相反。大同證交所股利標「下半年 0 元」，股利率可能被高估。",
    ("2025-12", "4763"): "EPS −63% 排名 178，被我刪除；官方保留。",
    ("2025-12", "3036"): "排名 36，新股名額已滿。",
    ("2025-12", "8422"): "排名 22，新股名額已滿（8 檔上限）。",
}


def _table(ws, head, rows, widths):
    ws.append(head)
    for c in ws[1]:
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEAD
    for r in rows:
        ws.append([v(x) for x in r])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=FONT)
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "A2"


if __name__ == "__main__":
    tags = sys.argv[1:2] or ["2026-05", "2025-12", "2026-12"]
    results = []
    for t in tags:
        out, r = build(t, sys.argv[2] if len(sys.argv) > 2 else None)
        results.append((t, r))
        print("輸出", out)
    print("輸出", build_doc(results))
