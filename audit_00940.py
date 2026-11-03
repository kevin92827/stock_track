"""00940 預測的檢查用 Excel：每一項判斷一個工作頁。

原始資料用數值（藍字），比率、排名、門檻、是否通過都用 Excel 公式（黑字），
跨頁引用是綠字。改黃底的門檻參數，後面的通過檔數、選股會跟著重算。
下行風險、母體、權重分配是 Python 算好的數值（公式寫不出來或太大），頁首有註明。

用法：python audit_00940.py [資料截止日]
"""
import os
import re
import sys

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import predict_00940 as m

FONT = "Arial"
BLUE, BLACK, GREEN = "0000FF", "000000", "008000"
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
NOTE_FILL = PatternFill("solid", fgColor="DDEBF7")
PARAM_FILL = PatternFill("solid", fgColor="FFFF00")
OK_FILL = PatternFill("solid", fgColor="C6EFCE")
NG_FILL = PatternFill("solid", fgColor="FFC7CE")
EPS_ = "1E-9"  # 浮點誤差容許：0.9 × 1.1 在電腦裡是 0.99000000000000002

PCT, PCT2, NUM0, NUM2, NUM4 = "0.0%", "0.00%", "#,##0", "#,##0.00", "0.0000"


def v(x):
    """pandas 值轉成可以寫進儲存格的值。"""
    if x is None:
        return None
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if (np.isnan(x) or np.isinf(x)) else float(x)
    if isinstance(x, pd.Timestamp):
        return x.strftime("%Y-%m-%d")
    if isinstance(x, str) and x in ("", "nan"):
        return None
    return x


# ---- 工作頁版面 --------------------------------------------------------------
class Sheet:
    """先決定欄位位置，全部工作頁排好後才寫公式（公式會互相引用）。"""

    def __init__(self, name, title, rule, method, source, rows, params=(), cols=(), sort_note=""):
        self.name, self.title, self.rule, self.method, self.source = name, title, rule, method, source
        self.rows = list(rows)
        self.params = list(params)   # (key, 標籤, 值或公式, 格式, 說明, 可調整?)
        self.cols = list(cols)       # dict(h, v|f, fmt, w, note, kind)
        self.sort_note = sort_note
        self.param_row = {}
        for p in self.params:          # 參數放在「參數」頁，列號全部工作頁接續
            PARAM_ROWS.append((self, p))
            self.param_row[p[0]] = len(PARAM_ROWS) + 1
        self.head_row = 1
        self.first = 2
        self.last = self.first + max(len(self.rows), 1) - 1
        self.letter = {c["h"]: get_column_letter(i + 1) for i, c in enumerate(self.cols)}

    def q(self):
        return f"'{self.name}'"

    def col_range(self, h, sheet_prefix=False):
        L = self.letter[h]
        rng = f"${L}${self.first}:${L}${self.last}"
        return f"{self.q()}!{rng}" if sheet_prefix else rng

    def param(self, key, sheet_prefix=True):
        return f"'{PARAM_SHEET}'!$C${self.param_row[key]}"


SHEETS = {}
PARAM_ROWS = []
PARAM_SHEET = "參數"
TOKEN = re.compile(r"\{([a-zA-Z0-9]+):([^{}]+?)\}")


def resolve(tpl, sh, r, ext=False):
    """模板代換：
    {c:欄名} 同列儲存格　{R:欄名} 本頁整欄　{p:參數} 本頁參數
    {P:頁:參數} 別頁參數　{L:頁:欄名} 用代號查別頁同一檔股票的值　{X:頁:欄名} 別頁整欄
    """
    def rep(mo):
        kind, arg = mo.group(1), mo.group(2)
        if kind == "c":
            return f"{sh.letter[arg]}{r}"
        if kind == "R":
            return sh.col_range(arg, ext)
        if kind == "p":
            return sh.param(arg)
        page, key = arg.split(":", 1)
        o = SHEETS[page]
        if kind == "P":
            return o.param(key, True)
        if kind == "X":
            return o.col_range(key, True)
        if kind == "L":
            return f"INDEX({o.col_range(key, True)},MATCH($A{r},{o.col_range('代號', True)},0))"
        raise ValueError(kind)
    return "=" + TOKEN.sub(rep, tpl)


def render_params(wb):
    ws = wb.create_sheet(PARAM_SHEET)
    for j, h in enumerate(["工作頁", "參數", "值"], 1):
        c = ws.cell(1, j, h)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
    for i, (sh, (key, lab, val, fmt, note, editable)) in enumerate(PARAM_ROWS, 2):
        ws.cell(i, 1, sh.name).font = Font(name=FONT)
        ws.cell(i, 2, lab).font = Font(name=FONT)
        c = ws.cell(i, 3)
        if isinstance(val, str) and val.startswith("="):
            c.value = resolve(val[1:], sh, i, ext=True)
            c.font = Font(name=FONT, color=BLACK)
        else:
            c.value = v(val)
            c.font = Font(name=FONT, color=BLUE)
        if editable:
            c.fill = PARAM_FILL
        if fmt:
            c.number_format = fmt
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:C{len(PARAM_ROWS) + 1}"
    for col, w in zip("ABC", [20, 34, 12]):
        ws.column_dimensions[col].width = w


def render(wb, sh):
    ws = wb.create_sheet(sh.name)

    # 表頭
    for j, col in enumerate(sh.cols, start=1):
        hc = ws.cell(sh.head_row, j, col["h"])
        hc.font = Font(name=FONT, bold=True, color="FFFFFF")
        hc.fill = HEAD_FILL
        hc.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = col.get("w", 11)
    ws.row_dimensions[sh.head_row].height = 32

    for i, code in enumerate(sh.rows):
        r = sh.first + i
        for j, col in enumerate(sh.cols, start=1):
            c = ws.cell(r, j)
            if "f" in col:
                c.value = resolve(col["f"], sh, r)
                c.font = Font(name=FONT, color=GREEN if col.get("kind") == "link" else BLACK)
            else:
                val = col["v"]
                x = val.get(code) if isinstance(val, (pd.Series, dict)) else val
                c.value = v(x)
                c.font = Font(name=FONT, color=BLUE if col.get("kind", "input") == "input" else BLACK)
            if col.get("fmt"):
                c.number_format = col["fmt"]

    # 通過欄位：TRUE 綠、FALSE 紅
    for j, col in enumerate(sh.cols, start=1):
        if col.get("flag"):
            L = get_column_letter(j)
            rng = f"{L}{sh.first}:{L}{sh.last}"
            ws.conditional_formatting.add(rng, FormulaRule(formula=[f"{L}{sh.first}=TRUE"], fill=OK_FILL))
            ws.conditional_formatting.add(rng, FormulaRule(formula=[f"{L}{sh.first}=FALSE"], fill=NG_FILL))
    ws.freeze_panes = ws.cell(sh.first, 3)

    if sh.cols:
        ws.auto_filter.ref = f"A{sh.head_row}:{get_column_letter(len(sh.cols))}{sh.last}"
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 10)
    return ws


# ---- 共用欄位 ----------------------------------------------------------------
def rank_f(val_h, desc=True, group_h=None, among=None):
    """百分位名次（method=min）：比自己好的檔數 +1，除以有數值的檔數。
    group_h：金融／非金融分開排；among：只在這欄為 TRUE 的股票裡排。"""
    cmp = ">" if desc else "<"
    cond = f"ISNUMBER({{R:{val_h}}})"
    if group_h:
        cond = f"({{R:{group_h}}}={{c:{group_h}}})*" + cond
    if among:
        cond = f"({{R:{among}}}=TRUE)*" + cond
    return (f'IF(NOT(ISNUMBER({{c:{val_h}}})),"",'
            f"(SUMPRODUCT({cond}*({{R:{val_h}}}{cmp}{{c:{val_h}}}))+1)/SUMPRODUCT(1*{cond}))")


def need_f(rank_h, t_key="t0"):
    """這檔要放寬到幾倍才會通過（沒資料就永遠不會過，記 999）。"""
    return f'IF({{c:{rank_h}}}="",999,{{c:{rank_h}}}/{{p:{t_key}}})'


def pass_f(rank_h, t_key="t0", exempt=None):
    base = f'IF({{c:{rank_h}}}="",FALSE,{{c:{rank_h}}}<=MIN(1,{{p:{t_key}}}*{{p:k}})+{EPS_})'
    return f"OR({{c:{exempt}}},{base[0:]})" if exempt else base


def k_param():
    return ("k", "放寬倍數（第一階段彙整頁算出）", "={P:12_第一階段彙整:k}", "0.00", "門檻 = MIN(100%, 原門檻 × 倍數)", False)


def base_cols(U, rows, extra=()):
    cols = [dict(h="代號", v=pd.Series(rows, index=rows), w=8, note="TEJ 代號"),
            dict(h="名稱", v=U["名稱"], w=10),
            dict(h="產業", v=U["產業"], w=10, note="IND.csv TSE產業_名稱"),
            dict(h="金融業", v=U["金融業"], w=7, note="產業 = 金融業"),
            dict(h="既有成分股", v=U["既有成分股"], w=7, note="00940 生效前持股"),
            dict(h="實際結果", v=ACT, w=8, note="00940 調整後持股比對：保留／新增／刪除")]
    return cols + list(extra)


# ---- 主程式 ------------------------------------------------------------------
def build(cutoff):
    global ACT
    res = m.run(cutoff)
    U, R, W, P = res["U"], res["R"], res["W"], res["P"]
    D = m.data()
    px, fin, div, ind = D["px"], D["fin"], D["div"], D["ind"]
    cut = pd.Timestamp(cutoff)
    q4, q4p, qb = P["q4"], P["q4_prev"], P["q_growth_base"]
    old = m.etf_holdings(P["old_date"])
    new = m.etf_holdings(P["new_date"]) if P.get("new_date") else pd.DataFrame()
    has_actual = len(new) > 0
    uni = sorted(U.index[U["在母體"]])

    def act(c):
        if not has_actual:
            return None
        if c in old.index:
            return "保留" if c in new.index else "刪除"
        return "新增" if c in new.index else None
    ACT = {c: act(c) for c in set(U.index) | set(old.index) | set(new.index)}

    def fq(col, q):
        return fin[col].unstack("q").reindex(U.index).get(q, pd.Series(np.nan, index=U.index))

    snap = px[px["date"] == cut].set_index("code")
    k_used = 1 + res["relax"]
    SHEETS.clear()
    PARAM_ROWS.clear()

    # ---------- 02 母體 ----------
    allstk = ind.index
    sa = snap[snap.index.isin(allstk)].copy()
    sa["mcap_rank_raw"] = sa["mcap"].rank(ascending=False, method="first")
    show = sorted(set(sa.index[sa["mcap_rank_raw"] <= 450]) | set(old.index) | set(uni),
                  key=lambda c: sa["mcap_rank_raw"].get(c, 9999))
    hold = m.load_hold(P["hold_month"])
    names = px.drop_duplicates("code", keep="last").set_index("code")["name"]
    tse = ind["tse_ind"]

    def reason(c):
        why = []
        if tse.get(c) == "建材營造": why.append("建材營造")
        if tse.get(c) == "存託憑證": why.append("存託憑證")
        if "-創" in str(names.get(c, "")): why.append("創新板")
        if c in U.index and U.loc[c, "全額交割"]: why.append("全額交割／變更交易")
        if hold["ff"].get(c, 1) < 0.10: why.append("自由流通量 < 10%")
        if c in U.index and U.loc[c, "在母體"]: return ""
        if not why: why.append("市值排名不夠前面")
        return "、".join(why)
    s02 = Sheet("02_母體", "02 母體：前 300 大（含 10% 緩衝區）",
                "上市櫃普通股，排除管理股票、變更交易、全額交割、創新板、建材營造；依市值取前 300 大，10% 緩衝區；自由流通係數 < 10% 不納入。",
                "緩衝區是一期一期往後推的（2025/04 → 2025/10 → 2026/04），用 Python 算，這頁是結果。新進要排前 270，上一期母體排 330 內可保留。",
                "price_o_*.csv（市值(百萬元)）、IND.csv（TSE產業_名稱）、MK25/26.csv（全額交割股、變更交易）、HOLD.csv（董監、經理人、大股東、親屬持股%）",
                show,
                params=[("n", "在母體檔數", "=COUNTIF({R:在母體},TRUE)", NUM0, "應為 300", False)],
                cols=[dict(h="代號", v=pd.Series(show, index=show), w=8),
                      dict(h="名稱", v=names, w=10),
                      dict(h="產業", v=tse, w=10, note="IND.csv TSE產業_名稱"),
                      dict(h="既有成分股", v={c: c in old.index for c in show}, w=7),
                      dict(h="實際結果", v=ACT, w=8),
                      dict(h="發行市值(百萬)", v=sa["mcap"], fmt=NUM0, w=12, note="price_o 截止日 市值(百萬元)"),
                      dict(h="市值排名(全部)", v=sa["mcap_rank_raw"], fmt=NUM0, w=8, note="還沒排除任何股票前的名次"),
                      dict(h="董監%", v=hold["dir"], fmt=NUM2, w=7, note=f"HOLD.csv {P['hold_month']} 董監總持股%"),
                      dict(h="經理人%", v=hold["mgr"], fmt=NUM2, w=7),
                      dict(h="大股東%", v=hold["big"], fmt=NUM2, w=7),
                      dict(h="親屬%", v=hold["rel"], fmt=NUM2, w=7),
                      dict(h="自由流通量", f="IF(COUNT({c:董監%}:{c:親屬%})=0,\"\",MAX(0,1-SUM({c:董監%}:{c:親屬%})/100))", fmt=PCT, w=8,
                           note="1 −（董監 + 經理人 + 大股東 + 親屬）÷ 100。臺灣指數公司的係數沒公開，這是近似值"),
                      dict(h="全額交割/變更交易", v={c: bool(U["全額交割"].get(c, False)) for c in show}, w=8, note="MK 檔截止日當天"),
                      dict(h="母體排名(排除後)", v=U["市值排名"], fmt=NUM0, w=8, kind="calc", note="排除產業、創新板、全額交割、流通量 < 10% 後的市值名次"),
                      dict(h="上期母體", v=U["上期母體"], w=7, kind="calc", note="前次截止日的前 300 大"),
                      dict(h="在母體", v={c: bool(U["在母體"].get(c, False)) for c in show}, w=7, kind="calc", flag=True,
                           note="新進 ≤ 270 名，或上期母體且 ≤ 330 名；不足 300 依名次補滿（Python 計算）"),
                      dict(h="不在母體原因", v={c: reason(c) for c in show}, w=22, kind="calc")])
    SHEETS[s02.name] = s02

    # ---------- 03 流動性 ----------
    g = res["liq_monthly"]
    months = list(res["months"])
    turn = g["turn"].unstack("m").reindex(U.index)
    avgv = (g["avgval"] / 1e5).unstack("m").reindex(U.index)
    mcols = []
    for mo in months:
        mcols.append(dict(h=f"週轉{mo.strftime('%y/%m')}", v=turn.get(mo), fmt=NUM2, w=6,
                          note="當月每日週轉率％加總" if mo == months[0] else ""))
    for mo in months:
        mcols.append(dict(h=f"日均{mo.strftime('%y/%m')}", v=avgv.get(mo), fmt=NUM2, w=6,
                          note="當月每日成交值平均（億）" if mo == months[0] else ""))
    t0, t1 = mcols[0]["h"], mcols[11]["h"]
    a0, a1 = mcols[12]["h"], mcols[23]["h"]
    s03 = Sheet("03_流動性", "03 流動性",
                "非成分股：最近 1 個月成交值排前 70%，且（最近 12 個月成交值前 20%，或 12 個月中至少 8 個月週轉率 ≥ 3%，或至少 8 個月日均成交值 ≥ 1 億）。既有成分股免檢驗。",
                "排名在前 300 大母體內排。月週轉率 = 當月每日週轉率加總；日均成交值 = 當月每日成交值平均。",
                "price_o_*.csv：成交值(千元)、週轉率％",
                uni,
                params=[("t1", "近 1 月成交值門檻", 0.70, PCT, "前 70%", True),
                        ("t12", "近 12 月成交值門檻", 0.20, PCT, "前 20%", True),
                        ("nm", "月數門檻", 8, NUM0, "至少幾個月", True),
                        ("turn", "週轉率門檻（%）", 3, NUM2, "", True),
                        ("avg", "日均成交值門檻（億）", 1, NUM2, "", True),
                        ("n", "通過檔數", "=COUNTIF({R:通過_流動性},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, [
                    dict(h="近1月成交值(億)", v=U["近1月成交值(億)"], fmt=NUM2, w=10, note=f"{months[-1]} 成交值加總"),
                    dict(h="近1月_名次%", f=rank_f("近1月成交值(億)"), fmt=PCT, w=8, note="母體內由大到小的名次 ÷ 檔數"),
                    dict(h="近12月成交值(億)", v=U["近12月成交值(億)"], fmt=NUM0, w=10, note=f"{months[0]}～{months[-1]}"),
                    dict(h="近12月_名次%", f=rank_f("近12月成交值(億)"), fmt=PCT, w=8),
                    dict(h="週轉達標月數", f=f"COUNTIF({{c:{t0}}}:{{c:{t1}}},\">=\"&{{p:turn}})", fmt=NUM0, w=7),
                    dict(h="日均達標月數", f=f"COUNTIF({{c:{a0}}}:{{c:{a1}}},\">=\"&{{p:avg}})", fmt=NUM0, w=7),
                    dict(h="通過_流動性", flag=True, w=8,
                         f="OR({c:既有成分股},AND({c:近1月_名次%}<={p:t1},OR({c:近12月_名次%}<={p:t12},{c:週轉達標月數}>={p:nm},{c:日均達標月數}>={p:nm})))",
                         note="既有成分股直接通過")] + mcols))
    SHEETS[s03.name] = s03

    # ---------- 04 非KY、EPS、配息 ----------
    known = div[div["board"] <= cut]
    dd = {}
    for code in uni:
        d = known[known["code"] == code]
        if d.empty:
            dd[code] = dict(); continue
        d = d.sort_values("board")
        by_year = d.groupby("year")["cash"].sum()
        ys = sorted(int(y) for y in by_year.index if y)
        latest = max(ys) if ys else 0
        lt = d.iloc[-1]["type"]
        n = 1 if lt == "A0" else (2 if lt.startswith("H") else 4)
        dd[code] = dict(latest=latest, y2=by_year.get(str(latest - 2), 0), y1=by_year.get(str(latest - 1), 0), y0=by_year.get(str(latest), 0),
                        cap5=d[d["year"].isin([str(y) for y in range(latest - 4, latest + 1)])]["cash_c"].sum(),
                        type=lt, last_n="、".join(f"{r.year}{r.type}:{r.cash:g}" for r in d.tail(n).itertuples()),
                        dps=d.tail(n)["cash"].sum())
    DD = pd.DataFrame(dd).T
    eps_cols = [dict(h=f"EPS {q}", v=fq("eps", q), fmt=NUM2, w=7, note="PL21_26 每股盈餘（單季）" if q == q4[0] else "") for q in q4]
    s04 = Sheet("04_非KY_EPS_配息", "04 非 KY、近 4 季 EPS、近 3 年配息、金融業公積、5 月年配已除息",
                "排除 KY；近 4 季 EPS 合計 > 0；近 3 年每年都有配現金股利；金融業近 5 年有用資本公積配息就排除；5 月審核排除生效日前已除息的年配股。",
                "近 3 年 = 截止日前董事會已決議的最近 3 個盈餘分派年度；每年度把各次現金股利加總。5 月排除：最新一筆配息型態為 A0，且當年 1/1～生效日前一天有除息紀錄。",
                "PL21_26.csv（每股盈餘）、EA_split_*.csv（盈餘分派_年度、股息分配型態、現金股利(元)、現金股利(元)_公積、董事會日期）、data/corp_actions.csv（除息日）",
                uni,
                params=[("may", "是否 5 月審核", bool(P["may"]), None, "TRUE 才套用年配已除息排除", False),
                        ("eff", "生效日", P["effective"], None, "", False),
                        ("n", "兩項都通過的檔數", "=COUNTIFS({R:通過_非KY},TRUE,{R:通過_EPS與配息},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, [
                    dict(h="通過_非KY", flag=True, f='NOT(ISNUMBER(SEARCH("KY",{c:名稱})))', w=7, note="名稱含 KY 就是第一上市櫃外國公司"),
                    *eps_cols,
                    dict(h="近4季EPS", f=f'IF(COUNT({{c:EPS {q4[0]}}}:{{c:EPS {q4[-1]}}})<4,"",SUM({{c:EPS {q4[0]}}}:{{c:EPS {q4[-1]}}}))', fmt=NUM2, w=8, note="4 季都要有資料"),
                    dict(h="最新分派年度", v=DD.get("latest"), fmt="0", w=7),
                    dict(h="股利 年度-2", v=DD.get("y2"), fmt=NUM2, w=7, note="該年度各次現金股利加總（元）"),
                    dict(h="股利 年度-1", v=DD.get("y1"), fmt=NUM2, w=7),
                    dict(h="股利 最新年度", v=DD.get("y0"), fmt=NUM2, w=7),
                    dict(h="近3年皆配息", f="AND(N({c:股利 年度-2})>0,N({c:股利 年度-1})>0,N({c:股利 最新年度})>0)", w=7),
                    dict(h="近5年公積配息合計", v=DD.get("cap5"), fmt=NUM2, w=8, note="現金股利(元)_公積，最新年度往前 5 年"),
                    dict(h="金融業公積排除", f="AND({c:金融業},N({c:近5年公積配息合計})>0)", w=7),
                    dict(h="最新配息型態", v=DD.get("type"), w=6, note="A0 年配、H 半年配、Q 季配"),
                    dict(h="生效前已除息日", v=U["生效前已除息日"], w=10),
                    dict(h="5月年配已除息", f='AND({p:may},{c:最新配息型態}="A0",{c:生效前已除息日}<>"")', w=7),
                    dict(h="通過_EPS與配息", flag=True, w=8,
                         f='AND(ISNUMBER({c:近4季EPS}),N({c:近4季EPS})>0,{c:近3年皆配息},NOT({c:金融業公積排除}),NOT({c:5月年配已除息}))')]))
    SHEETS[s04.name] = s04

    # ---------- 05 負債比 ----------
    ql = q4[-1]
    s05 = Sheet("05_負債比", "05 負債比率",
                "負債比率排名前 90%（金融業、非金融業分開排）。",
                f"負債比 = 負債總額 ÷ 資產總額（{ql} 單季資產負債表），由低到高排名，金融與非金融分開。",
                "BS21_26.csv：負債總額、資產總額",
                uni,
                params=[("t0", "原門檻", 0.90, PCT, "前 90%", True), k_param(),
                        ("t", "實際門檻", "=MIN(1,{p:t0}*{p:k})", PCT, "", False),
                        ("n", "通過檔數", "=COUNTIF({R:通過_負債比},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, [
                    dict(h="負債總額", v=fq("liab", ql), fmt=NUM0, w=12, note=f"{ql}（千元）"),
                    dict(h="資產總額", v=fq("assets", ql), fmt=NUM0, w=12, note=f"{ql}（千元）"),
                    dict(h="負債比", f='IF(OR(NOT(ISNUMBER({c:負債總額})),NOT(ISNUMBER({c:資產總額}))),"",{c:負債總額}/{c:資產總額})', fmt=PCT, w=8),
                    dict(h="名次%", f=rank_f("負債比", desc=False, group_h="金融業"), fmt=PCT, w=8, note="同組（金融／非金融）由低到高"),
                    dict(h="需要倍數", f=need_f("名次%"), fmt="0.000", w=7, note="名次 ÷ 原門檻；> 1 代表要放寬才會過"),
                    dict(h="通過_負債比", f=pass_f("名次%"), flag=True, w=8)]))
    SHEETS[s05.name] = s05

    # ---------- 06 ROA ROE ----------
    roa_c = [dict(h=f"ROA {q}", v=fq("roa", q), fmt=NUM2, w=7, note="ROE21_26 ROA(A)稅後息前（單季 %）" if q == q4[0] else "") for q in q4]
    roe_c = [dict(h=f"ROE {q}", v=fq("roe", q), fmt=NUM2, w=7, note="ROE21_26 ROE(A)－稅後（單季 %）" if q == q4[0] else "") for q in q4]
    s06 = Sheet("06_ROA_ROE", "06 ROA、ROE",
                "資產報酬率、股東權益報酬率都要排名前 90%（金融業、非金融業分開排）。",
                "近 4 季單季 ROA、ROE 加總，由高到低排名；兩項都要過。",
                "ROE21_26.csv：ROA(A)稅後息前、ROE(A)－稅後",
                uni,
                params=[("t0", "原門檻", 0.90, PCT, "前 90%", True), k_param(),
                        ("t", "實際門檻", "=MIN(1,{p:t0}*{p:k})", PCT, "", False),
                        ("n", "通過檔數", "=COUNTIF({R:通過_ROA_ROE},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, roa_c + [
                    dict(h="ROA近4季", f=f'IF(COUNT({{c:ROA {q4[0]}}}:{{c:ROA {q4[-1]}}})<4,"",SUM({{c:ROA {q4[0]}}}:{{c:ROA {q4[-1]}}}))', fmt=NUM2, w=8)] + roe_c + [
                    dict(h="ROE近4季", f=f'IF(COUNT({{c:ROE {q4[0]}}}:{{c:ROE {q4[-1]}}})<4,"",SUM({{c:ROE {q4[0]}}}:{{c:ROE {q4[-1]}}}))', fmt=NUM2, w=8),
                    dict(h="ROA名次%", f=rank_f("ROA近4季", group_h="金融業"), fmt=PCT, w=8),
                    dict(h="ROE名次%", f=rank_f("ROE近4季", group_h="金融業"), fmt=PCT, w=8),
                    dict(h="需要倍數", f='IF(OR({c:ROA名次%}="",{c:ROE名次%}=""),999,MAX({c:ROA名次%},{c:ROE名次%})/{p:t0})', fmt="0.000", w=7),
                    dict(h="通過_ROA_ROE", flag=True, w=8,
                         f=f'IF(OR({{c:ROA名次%}}="",{{c:ROE名次%}}=""),FALSE,MAX({{c:ROA名次%}},{{c:ROE名次%}})<=MIN(1,{{p:t0}}*{{p:k}})+{EPS_})')]))
    SHEETS[s06.name] = s06

    # ---------- 07 益本比 ----------
    q2 = q4[-2:]
    s07 = Sheet("07_益本比", "07 益本比（近 2 季）",
                "最近 2 季益本比排名前 80%。",
                "益本比 = 近 2 季 EPS 加總 ÷ 截止日原始股價。原始股價 = 市值 ÷ 流通在外股數（TEJ 收盤價欄位是還原價，不能直接用）。",
                "PL21_26.csv（每股盈餘）、price_o_*.csv（市值(百萬元)、流通在外股數(千股)）",
                uni,
                params=[("t0", "原門檻", 0.80, PCT, "前 80%", True), k_param(),
                        ("t", "實際門檻", "=MIN(1,{p:t0}*{p:k})", PCT, "", False),
                        ("n", "通過檔數", "=COUNTIF({R:通過_益本比},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, [
                    dict(h=f"EPS {q2[0]}", v=fq("eps", q2[0]), fmt=NUM2, w=7),
                    dict(h=f"EPS {q2[1]}", v=fq("eps", q2[1]), fmt=NUM2, w=7),
                    dict(h="市值(百萬)", v=snap["mcap"], fmt=NUM0, w=11, note=f"{cutoff}"),
                    dict(h="流通股數(千股)", v=snap["shares"], fmt=NUM0, w=11, note=f"{cutoff}"),
                    dict(h="原始股價", f='IF(N({c:流通股數(千股)})=0,"",{c:市值(百萬)}*1000/{c:流通股數(千股)})', fmt=NUM2, w=8, note="市值 × 1000 ÷ 股數"),
                    dict(h="益本比", f=f'IF(OR(COUNT({{c:EPS {q2[0]}}}:{{c:EPS {q2[1]}}})<2,NOT(ISNUMBER({{c:原始股價}}))),"",SUM({{c:EPS {q2[0]}}}:{{c:EPS {q2[1]}}})/{{c:原始股價}})', fmt=PCT2, w=8),
                    dict(h="名次%", f=rank_f("益本比"), fmt=PCT, w=8, note="由高到低，不分金融"),
                    dict(h="需要倍數", f=need_f("名次%"), fmt="0.000", w=7),
                    dict(h="通過_益本比", f=pass_f("名次%"), flag=True, w=8)]))
    SHEETS[s07.name] = s07

    # ---------- 08 稅後淨利 ----------
    pti_c = [dict(h=f"稅前 {q}", v=fq("pti", q), fmt=NUM0, w=10, note="PL21_26 稅前淨利（千元）" if q == q4[0] else "") for q in q4]
    tax_c = [dict(h=f"所得稅 {q}", v=fq("tax", q), fmt=NUM0, w=9, note="PL21_26 所得稅費用（千元）" if q == q4[0] else "") for q in q4]
    s08 = Sheet("08_稅後淨利", "08 稅後淨利（近 4 季）",
                "最近 4 季稅後淨利排名前 90%。",
                "稅後淨利 = Σ（稅前淨利 − 所得稅費用），含少數股權，由高到低排名。",
                "PL21_26.csv：稅前淨利、所得稅費用",
                uni,
                params=[("t0", "原門檻", 0.90, PCT, "前 90%", True), k_param(),
                        ("t", "實際門檻", "=MIN(1,{p:t0}*{p:k})", PCT, "", False),
                        ("n", "通過檔數", "=COUNTIF({R:通過_稅後淨利},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, pti_c + tax_c + [
                    dict(h="稅後淨利(億)", f=f'IF(COUNT({{c:稅前 {q4[0]}}}:{{c:所得稅 {q4[-1]}}})<8,"",(SUM({{c:稅前 {q4[0]}}}:{{c:稅前 {q4[-1]}}})-SUM({{c:所得稅 {q4[0]}}}:{{c:所得稅 {q4[-1]}}}))/100000)', fmt=NUM2, w=9),
                    dict(h="名次%", f=rank_f("稅後淨利(億)"), fmt=PCT, w=8),
                    dict(h="需要倍數", f=need_f("名次%"), fmt="0.000", w=7),
                    dict(h="通過_稅後淨利", f=pass_f("名次%"), flag=True, w=8)]))
    SHEETS[s08.name] = s08

    # ---------- 09 毛利率成長 ----------
    def qcols(prefix, col, qs, note):
        return [dict(h=f"{prefix} {q}", v=fq(col, q), fmt=NUM0, w=10, note=note if i == 0 else "") for i, q in enumerate(qs)]
    s09 = Sheet("09_毛利率成長", "09 毛利率成長（近 4 季）",
                "最近 4 季毛利率成長排名前 90%。",
                "近 4 季毛利率（毛利合計 ÷ 營收合計）減前一年同期 4 季毛利率（百分點），由高到低排名。金融業直接通過。",
                "PL21_26.csv：營業收入淨額、營業毛利",
                uni,
                params=[("t0", "原門檻", 0.90, PCT, "前 90%", True), k_param(),
                        ("t", "實際門檻", "=MIN(1,{p:t0}*{p:k})", PCT, "", False),
                        ("n", "通過檔數", "=COUNTIF({R:通過_毛利率成長},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni,
                    qcols("營收", "rev", q4, "近 4 季營業收入淨額（千元）") + qcols("毛利", "gp", q4, "近 4 季營業毛利") +
                    qcols("營收", "rev", q4p, "前一年同期 4 季") + qcols("毛利", "gp", q4p, "") + [
                    dict(h="近4季毛利率", f=f'IFERROR(IF(OR(COUNT({{c:營收 {q4[0]}}}:{{c:營收 {q4[-1]}}})<4,COUNT({{c:毛利 {q4[0]}}}:{{c:毛利 {q4[-1]}}})<4),"",SUM({{c:毛利 {q4[0]}}}:{{c:毛利 {q4[-1]}}})/SUM({{c:營收 {q4[0]}}}:{{c:營收 {q4[-1]}}})),"")', fmt=PCT2, w=8),
                    dict(h="前期毛利率", f=f'IFERROR(IF(OR(COUNT({{c:營收 {q4p[0]}}}:{{c:營收 {q4p[-1]}}})<4,COUNT({{c:毛利 {q4p[0]}}}:{{c:毛利 {q4p[-1]}}})<4),"",SUM({{c:毛利 {q4p[0]}}}:{{c:毛利 {q4p[-1]}}})/SUM({{c:營收 {q4p[0]}}}:{{c:營收 {q4p[-1]}}})),"")', fmt=PCT2, w=8),
                    dict(h="毛利率成長(百分點)", f='IF(OR(NOT(ISNUMBER({c:近4季毛利率})),NOT(ISNUMBER({c:前期毛利率}))),"",({c:近4季毛利率}-{c:前期毛利率})*100)', fmt=NUM2, w=9),
                    dict(h="名次%", f=rank_f("毛利率成長(百分點)"), fmt=PCT, w=8),
                    dict(h="需要倍數", f='IF({c:金融業},0,' + need_f("名次%") + ")", fmt="0.000", w=7, note="金融業記 0"),
                    dict(h="通過_毛利率成長", f=pass_f("名次%", exempt="金融業"), flag=True, w=8)]))
    SHEETS[s09.name] = s09

    # ---------- 10 大陸投資 ----------
    cha = m.load_cha(P["cha_q"])
    s10 = Sheet("10_大陸投資", "10 對區域投資集中度（大陸投資）",
                "對區域投資集中度指標排名後 95%（考量投資轉投資大陸的金額與占淨值比例）。",
                f"大陸投資帳面值 ÷ 母公司股東權益（{P['cha_q']}），由低到高排名；沒有大陸投資資料當作 0。",
                "CHA.csv：期末帳面值(NTD)；BS21_26.csv：母公司股東權益合計",
                uni,
                params=[("t0", "原門檻", 0.95, PCT, "後 95%（排除最高的 5%）", True), k_param(),
                        ("t", "實際門檻", "=MIN(1,{p:t0}*{p:k})", PCT, "", False),
                        ("n", "通過檔數", "=COUNTIF({R:通過_大陸投資},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, [
                    dict(h="大陸投資帳面值", v=cha.reindex(uni).fillna(0), fmt=NUM0, w=12, note=f"CHA.csv {P['cha_q']} 期末帳面值；沒資料填 0"),
                    dict(h="母公司股東權益", v=fq("eq_parent", ql), fmt=NUM0, w=12, note=f"{ql}（千元）"),
                    dict(h="大陸投資/淨值", f='IF(N({c:母公司股東權益})=0,"",{c:大陸投資帳面值}/{c:母公司股東權益})', fmt=PCT2, w=9),
                    dict(h="名次%(低到高)", f=rank_f("大陸投資/淨值", desc=False), fmt=PCT, w=8),
                    dict(h="需要倍數", f=need_f("名次%(低到高)"), fmt="0.000", w=7),
                    dict(h="通過_大陸投資", f=pass_f("名次%(低到高)"), flag=True, w=8)]))
    SHEETS[s10.name] = s10

    # ---------- 11 長期成長 ----------
    pb = [dict(h=f"稅前 {q}", v=fq("pti", q), fmt=NUM0, w=10, note="3 年前同期稅前淨利" if i == 0 else "") for i, q in enumerate(qb)]
    tb = [dict(h=f"所得稅 {q}", v=fq("tax", q), fmt=NUM0, w=9, note="3 年前同期所得稅" if i == 0 else "") for i, q in enumerate(qb)]
    s11 = Sheet("11_長期成長", "11 長期成長率（只看非既有成分股）",
                "非既有成分股的長期成長率排名前 75%。",
                "⚠️ 規則沒定義長期成長率。這版用稅後淨利 3 年 CAGR = (近 4 季稅後淨利 ÷ 3 年前同期 4 季)^(1/3) − 1，兩期都要 > 0。名次在整個母體內排（含既有成分股），但只有非既有成分股要過門檻。",
                "PL21_26.csv：稅前淨利、所得稅費用；近 4 季稅後淨利從 08 頁帶入",
                uni,
                params=[("t0", "原門檻", 0.75, PCT, "前 75%", True), k_param(),
                        ("t", "實際門檻", "=MIN(1,{p:t0}*{p:k})", PCT, "", False),
                        ("n", "通過檔數", "=COUNTIF({R:通過_長期成長},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, [dict(h="近4季稅後淨利(億)", f="{L:08_稅後淨利:稅後淨利(億)}", kind="link", fmt=NUM2, w=9)] + pb + tb + [
                    dict(h="3年前稅後淨利(億)", f=f'IF(COUNT({{c:稅前 {qb[0]}}}:{{c:所得稅 {qb[-1]}}})<8,"",(SUM({{c:稅前 {qb[0]}}}:{{c:稅前 {qb[-1]}}})-SUM({{c:所得稅 {qb[0]}}}:{{c:所得稅 {qb[-1]}}}))/100000)', fmt=NUM2, w=9),
                    dict(h="稅後淨利3年CAGR", f='IF(AND(ISNUMBER({c:近4季稅後淨利(億)}),ISNUMBER({c:3年前稅後淨利(億)})),IF(AND({c:近4季稅後淨利(億)}>0,{c:3年前稅後淨利(億)}>0),({c:近4季稅後淨利(億)}/{c:3年前稅後淨利(億)})^(1/3)-1,""),"")', fmt=PCT2, w=9),
                    dict(h="名次%", f=rank_f("稅後淨利3年CAGR"), fmt=PCT, w=8),
                    dict(h="需要倍數", f='IF({c:既有成分股},0,' + need_f("名次%") + ")", fmt="0.000", w=7, note="既有成分股記 0"),
                    dict(h="通過_長期成長", f=pass_f("名次%", exempt="既有成分股"), flag=True, w=8)]))
    SHEETS[s11.name] = s11

    # ---------- 12 第一階段彙整 + 放寬 ----------
    ks = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]
    crit = [("03_流動性", "通過_流動性", None), ("04_非KY_EPS_配息", "通過_非KY", None), ("04_非KY_EPS_配息", "通過_EPS與配息", None),
            ("05_負債比", "通過_負債比", "需要倍數"), ("06_ROA_ROE", "通過_ROA_ROE", "需要倍數"), ("07_益本比", "通過_益本比", "需要倍數"),
            ("08_稅後淨利", "通過_稅後淨利", "需要倍數"), ("09_毛利率成長", "通過_毛利率成長", "需要倍數"),
            ("10_大陸投資", "通過_大陸投資", "需要倍數"), ("11_長期成長", "通過_長期成長", "需要倍數")]
    short = {"05_負債比": "負債比", "06_ROA_ROE": "ROA_ROE", "07_益本比": "益本比", "08_稅後淨利": "稅後淨利",
             "09_毛利率成長": "毛利率成長", "10_大陸投資": "大陸投資", "11_長期成長": "長期成長"}
    c12 = base_cols(U, uni)
    for page, h, _ in crit[:3]:
        c12.append(dict(h=h, f=f"{{L:{page}:{h}}}", kind="link", flag=True, w=8))
    c12.append(dict(h="基本條件都過", f="AND({c:通過_流動性},{c:通過_非KY},{c:通過_EPS與配息})", flag=True, w=8, note="流動性、非 KY、EPS 與配息不會放寬"))
    for page, h, nh in crit[3:]:
        c12.append(dict(h=f"倍數_{short[page]}", f=f"{{L:{page}:{nh}}}", kind="link", fmt="0.000", w=7,
                        note="這項要放寬到幾倍才會過" if page == "05_負債比" else ""))
    c12.append(dict(h="需要倍數", f=f"MAX({{c:倍數_負債比}}:{{c:倍數_長期成長}})", fmt="0.000", w=7, note="各項取最大：放寬到這個倍數才會全部通過"))
    c12.append(dict(h="通過第一階段", f=f"AND({{c:基本條件都過}},{{c:需要倍數}}<={{p:k}}+{EPS_})", flag=True, w=8))
    prm = [("min", "至少要幾檔", 90, NUM0, "不到 90 檔才放寬", True)]
    for i, kk in enumerate(ks):
        prm.append((f"k{i}", f"倍數 {kk:.1f} 時通過檔數", f"=COUNTIFS({{R:基本條件都過}},TRUE,{{R:需要倍數}},\"<=\"&({kk}+{EPS_}))", NUM0, f"門檻 × {kk:.1f}（90% → {min(1, .9 * kk):.0%}）", False))
    cnt_refs = ",".join(f"{{p:k{i}}}" for i in range(len(ks)))
    prm.append(("k", "★ 採用的放寬倍數", "=IF({p:k0}>={p:min},1," + "".join(f"IF({{p:k{i}}}>={{p:min}},{kk}," for i, kk in enumerate(ks[1:], 1)) + "1.5" + ")" * (len(ks) - 1) + ")",
                "0.00", "第一個讓通過檔數 ≥ 90 的倍數（最多 1.5）", False))
    prm.append(("n", "通過第一階段檔數", "=COUNTIF({R:通過第一階段},TRUE)", NUM0, "", False))
    s12 = Sheet("12_第一階段彙整", "12 第一階段彙整與放寬",
                "第一階段通過不到 90 檔時，放寬篩選條件直到超過 90 檔。",
                "⚠️ 規則沒寫怎麼放寬。這版：所有百分位門檻一起乘以同一個倍數（1.0、1.1、1.2…），取第一個讓通過檔數 ≥ 90 的倍數。每檔的「需要倍數」= 各項名次 ÷ 原門檻 的最大值，倍數 ≥ 它就全部通過。",
                "05～11 頁的名次；03、04 頁的基本條件",
                uni, params=prm, cols=c12)
    SHEETS[s12.name] = s12

    # ---------- 13 下行風險 ----------
    s13 = Sheet("13_下行風險", "13 第二階段：下行風險",
                "最近 252 天下行風險的倒數排名前 90%。下行風險 = √(Σmin(0,Rt)²/N) × √252，Rt 是還原股價日報酬。",
                "下行風險用 Python 從日資料算好（252 天 × 300 檔放不進公式）。倒數排名前 90% = 在通過第一階段的股票裡，下行風險由低到高排前 90%。",
                "price_o_*.csv：收盤價(元)（TEJ 這欄是還原價）",
                uni,
                params=[("t0", "門檻", 0.90, PCT, "前 90%（第二階段不放寬）", True),
                        ("n1", "第一階段檔數", "={P:12_第一階段彙整:n}", NUM0, "", False),
                        ("n", "通過第二階段檔數", "=COUNTIF({R:通過第二階段},TRUE)", NUM0, "", False)],
                cols=base_cols(U, uni, [
                    dict(h="通過第一階段", f="{L:12_第一階段彙整:通過第一階段}", kind="link", flag=True, w=8),
                    dict(h="報酬天數N", v=U["下行風險_天數"], fmt=NUM0, w=7, kind="calc"),
                    dict(h="下行風險", v=U["下行風險"], fmt=NUM4, w=9, kind="calc", note="Python 計算"),
                    dict(h="名次%(低到高)", f=f'IF(AND({{c:通過第一階段}},ISNUMBER({{c:下行風險}})),(SUMPRODUCT(({{R:通過第一階段}}=TRUE)*ISNUMBER({{R:下行風險}})*({{R:下行風險}}<{{c:下行風險}}))+1)/COUNTIF({{R:通過第一階段}},TRUE),"")', fmt=PCT, w=8,
                         note="只在通過第一階段的股票裡排"),
                    dict(h="通過第二階段", f=f'IF({{c:名次%(低到高)}}="",FALSE,{{c:名次%(低到高)}}<={{p:t0}}+{EPS_})', flag=True, w=8)]))
    SHEETS[s13.name] = s13

    # ---------- 14 綜合分數與選股 ----------
    ocf_c = [dict(h=f"營業現金流 {q}", v=fq("ocf", q), fmt=NUM0, w=10, note="CF21_26 來自營運之現金流量（千元）" if q == q4[0] else "") for q in q4]
    cap_c = [dict(h=f"購置PPE {q}", v=fq("capex", q), fmt=NUM0, w=10, note="CF21_26 購置不動產廠房設備（負數）" if q == q4[0] else "") for q in q4]
    dps_txt = DD.get("last_n")
    metrics = [("自由現金流收益率", "FCF"), ("股利率", "DY"), ("益本比", "EP")]
    win_params = []
    for h, key in metrics:
        win_params += [(f"lo{key}", f"{h} 2.5% 分位", f"=PERCENTILE({{R:{h}_樣本}},0.025)", NUM4, "縮尾下限（只用通過第二階段的）", False),
                       (f"hi{key}", f"{h} 97.5% 分位", f"=PERCENTILE({{R:{h}_樣本}},0.975)", NUM4, "縮尾上限", False),
                       (f"mu{key}", f"{h} 縮尾後平均", f"=AVERAGE({{R:{h}_縮尾}})", NUM4, "", False),
                       (f"sd{key}", f"{h} 縮尾後標準差", f"=STDEVP({{R:{h}_縮尾}})", NUM4, "母體標準差", False)]
    mcols14 = []
    for h, key in metrics:
        mcols14 += [dict(h=f"{h}_樣本", f=f'IF(AND({{c:通過第二階段}},ISNUMBER({{c:{h}}})),{{c:{h}}},"")', fmt=NUM4, w=8, note="只留通過第二階段的值"),
                    dict(h=f"{h}_縮尾", f=f'IF({{c:{h}_樣本}}="","",MIN(MAX({{c:{h}_樣本}},{{p:lo{key}}}),{{p:hi{key}}}))', fmt=NUM4, w=8),
                    dict(h=f"z_{h}", f=f'IF({{c:{h}_縮尾}}="","",({{c:{h}_縮尾}}-{{p:mu{key}}})/{{p:sd{key}}})', fmt="0.000", w=7)]
    sel_params = [("top", "直接入選名次", m.TOP_IN, NUM0, "前 35 名直接入選", True),
                  ("out", "既有剔除名次", m.KEEP_OUT, NUM0, "既有成分股排 65 名外剔除", True),
                  ("N", "成分股數", m.N_CONST, NUM0, "", True),
                  ("maxr", "最多換幾檔", m.MAX_REPLACE, NUM0, "", True),
                  ("maxk", "超過時最多保留既有", m.MAX_KEEP, NUM0, "", True),
                  ("ntop", "前 35 名檔數", "=COUNTIF({R:前35名},TRUE)", NUM0, "", False),
                  ("nrep", "一般規則換入檔數", "=COUNTIFS({R:一般規則入選},TRUE,{R:既有成分股},FALSE)", NUM0, "", False),
                  ("nex", "通過第二階段的既有成分股", "=COUNTIFS({R:通過第二階段},TRUE,{R:既有成分股},TRUE)", NUM0, "", False),
                  ("fb", "是否改用備案", "={p:nrep}>{p:maxr}", None, "換入超過 10 檔 → 保留最多 40 檔既有，新股補滿 50", False),
                  ("n", "最終入選檔數", "=COUNTIF({R:最終入選},TRUE)", NUM0, "應為 50", False)]
    s14 = Sheet("14_綜合分數_選股", "14 綜合分數與緩衝區選股",
                "自由現金流收益率（金融業不適用）、股利率、益本比各自縮尾 95% 後標準化，合成綜合分數取前 50。緩衝區：前 35 名入選；既有成分股排 65 名外剔除；36～65 名優先保留既有，最多換 10 檔；仍超過 10 檔時，依分數保留最多 40 檔既有，再補新股到 50。",
                "縮尾 = 夾在 2.5%～97.5% 分位之間；z = (值 − 平均) ÷ 母體標準差；綜合分數 = 有值的 z 取平均（金融業只平均股利率、益本比）。",
                "CF21_26.csv（營業現金流、購置不動產廠房設備）、EA_split_*.csv（現金股利）、07 頁原始股價與益本比、02 頁市值",
                uni, params=win_params + sel_params,
                cols=base_cols(U, uni, [
                    dict(h="通過第二階段", f="{L:13_下行風險:通過第二階段}", kind="link", flag=True, w=8)] + ocf_c + cap_c + [
                    dict(h="發行市值(億)", v=U["發行市值(億)"], fmt=NUM0, w=10),
                    dict(h="自由現金流收益率", f=f'IF(OR({{c:金融業}},COUNT({{c:營業現金流 {q4[0]}}}:{{c:購置PPE {q4[-1]}}})<8),"",(SUM({{c:營業現金流 {q4[0]}}}:{{c:營業現金流 {q4[-1]}}})+SUM({{c:購置PPE {q4[0]}}}:{{c:購置PPE {q4[-1]}}}))/1000/({{c:發行市值(億)}}*100))', fmt=PCT2, w=9,
                         note="（營業現金流 + 購置PPE）÷ 市值；金融業空白"),
                    dict(h="採用的股利", v=dps_txt, w=18, note="年配取最新 1 筆、半年配 2 筆、季配 4 筆（截止日前董事會已決議）"),
                    dict(h="近一年現金股利", v=DD.get("dps"), fmt=NUM2, w=8),
                    dict(h="原始股價", f="{L:07_益本比:原始股價}", kind="link", fmt=NUM2, w=8),
                    dict(h="股利率", f='IF(OR(NOT(ISNUMBER({c:近一年現金股利})),NOT(ISNUMBER({c:原始股價}))),"",{c:近一年現金股利}/{c:原始股價})', fmt=PCT2, w=8),
                    dict(h="益本比", f="{L:07_益本比:益本比}", kind="link", fmt=PCT2, w=8)] + mcols14 + [
                    dict(h="綜合分數", f='IF({c:通過第二階段},IFERROR(AVERAGE({c:z_自由現金流收益率},{c:z_股利率},{c:z_益本比}),""),"")', fmt="0.000", w=8),
                    dict(h="分數排序用", f='IF(ISNUMBER({c:綜合分數}),{c:綜合分數},-1E+9)', fmt="0.000", w=7, note="沒分數的填 −10 億，方便排名"),
                    dict(h="分數排名", f='IF(ISNUMBER({c:綜合分數}),SUMPRODUCT(1*({R:分數排序用}>{c:分數排序用}))+1,"")', fmt=NUM0, w=7),
                    dict(h="前35名", f='IF({c:分數排名}="",FALSE,{c:分數排名}<={p:top})', w=7),
                    dict(h="36～65名順位", f='IF({c:分數排名}="","",IF(AND({c:分數排名}>{p:top},{c:分數排名}<={p:out}),IF({c:既有成分股},0,1000)+{c:分數排名},""))', fmt=NUM0, w=8,
                         note="既有成分股排前面（+0），新股排後面（+1000）"),
                    dict(h="一般規則入選", f='OR({c:前35名},AND(ISNUMBER({c:36～65名順位}),SUMPRODUCT(ISNUMBER({R:36～65名順位})*({R:36～65名順位}<{c:36～65名順位}))+1<={p:N}-{p:ntop}))', w=8),
                    dict(h="既有中排名", f='IF(AND({c:通過第二階段},{c:既有成分股}),SUMPRODUCT(({R:通過第二階段}=TRUE)*({R:既有成分股}=TRUE)*({R:分數排序用}>{c:分數排序用}))+1,"")', fmt=NUM0, w=7),
                    dict(h="新股中排名", f='IF(AND({c:通過第二階段},NOT({c:既有成分股})),SUMPRODUCT(({R:通過第二階段}=TRUE)*({R:既有成分股}=FALSE)*({R:分數排序用}>{c:分數排序用}))+1,"")', fmt=NUM0, w=7),
                    dict(h="備案入選", f='OR(AND(ISNUMBER({c:既有中排名}),N({c:既有中排名})<={p:maxk}),AND(ISNUMBER({c:新股中排名}),N({c:新股中排名})<={p:N}-MIN({p:maxk},{p:nex})))', w=7),
                    dict(h="最終入選", f="IF({p:fb},{c:備案入選},{c:一般規則入選})", flag=True, w=8)]))
    SHEETS[s14.name] = s14

    # ---------- 15 權重 ----------
    wrows = list(W.sort_values("預測權重_股利率", ascending=False).index)
    s15 = Sheet("15_權重", "15 權重",
                "股利率加權。單檔 ≤ 8%，且 ≤ 自由流通市值占比 × 5、≤ 近 3 個月成交值占比 × 5、預擬規模 × 權重 ≤ 發行市值 4% 且 ≤ 自由流通市值 10%。分不下去時倍數最多放寬到 1.5 倍、單檔上限最多 10%。",
                f"上限用公式算；最終權重是 Python 把超過上限的部分重新分給其他檔（反覆分配）算出來的（{res['weight_note']}）。這頁是 Python 預設選出的 50 檔，改了前面門檻不會跟著重算。",
                "14 頁股利率、02 頁市值、02 頁自由流通量、price_o 近 3 個月成交值、00940 截止日持股（算規模）",
                wrows,
                params=[("aum", "00940 截止日規模(億)", res["aum"] / 1e8, NUM0, "持股股數 × 原始股價 ÷ 股票權重合計", False),
                        ("aumh", "預擬資產管理規模(億)", res["aum_h"] / 1e8, NUM0, "無條件進位到 250 億的倍數", False),
                        ("cap", "單檔上限", m.W_CAP, PCT, "", False),
                        ("chk", "跟 14 頁最終入選不一致的檔數", "=SUMPRODUCT(1*({R:14頁仍入選}=FALSE))", NUM0, "0 才代表這頁跟目前門檻一致", False)],
                cols=[dict(h="代號", v=pd.Series(wrows, index=wrows), w=8), dict(h="名稱", v=U["名稱"], w=10),
                      dict(h="既有成分股", v=U["既有成分股"], w=7), dict(h="實際結果", v=ACT, w=8),
                      dict(h="14頁仍入選", f="{L:14_綜合分數_選股:最終入選}", kind="link", flag=True, w=7),
                      dict(h="股利率", f="{L:14_綜合分數_選股:股利率}", kind="link", fmt=PCT2, w=8),
                      dict(h="發行市值(億)", v=W["發行市值(億)"], fmt=NUM0, w=10),
                      dict(h="自由流通量", v=W["自由流通量"], fmt=PCT, w=8),
                      dict(h="自由流通市值(億)", f="{c:發行市值(億)}*IF(ISNUMBER({c:自由流通量}),{c:自由流通量},1)", fmt=NUM0, w=10),
                      dict(h="近3月日均成交值(億)", v=W["近3月日均成交值(億)"], fmt=NUM2, w=10),
                      dict(h="原始權重(股利率占比)", f="{c:股利率}/SUM({R:股利率})", fmt=PCT2, w=9),
                      dict(h="上限_8%", f="{p:cap}", fmt=PCT2, w=7),
                      dict(h="上限_流通市值×5", f="5*{c:自由流通市值(億)}/SUM({R:自由流通市值(億)})", fmt=PCT2, w=9),
                      dict(h="上限_成交值×5", f="5*{c:近3月日均成交值(億)}/SUM({R:近3月日均成交值(億)})", fmt=PCT2, w=9),
                      dict(h="上限_規模≤市值4%", f="0.04*{c:發行市值(億)}/{p:aumh}", fmt=PCT2, w=9),
                      dict(h="上限_規模≤流通10%", f="0.1*{c:自由流通市值(億)}/{p:aumh}", fmt=PCT2, w=9),
                      dict(h="權重上限", f="MIN({c:上限_8%}:{c:上限_規模≤流通10%})", fmt=PCT2, w=8),
                      dict(h="預測權重", v=W["預測權重_股利率"], fmt=PCT2, w=8, kind="calc", note="Python 反覆分配的結果"),
                      dict(h="沒超過上限", f=f"{{c:預測權重}}<={{c:權重上限}}+{EPS_}", flag=True, w=7),
                      dict(h="觸及上限", f="ABS({c:預測權重}-{c:權重上限})<1E-6", w=7),
                      dict(h="實際權重", v=W["實際權重(新)"], fmt=PCT2, w=8, note=f"00940 {P.get('new_date')} 持股，股票部位重新加總成 100%"),
                      dict(h="差(預測−實際)", f="{c:預測權重}-N({c:實際權重})", fmt=PCT2, w=8)])
    SHEETS[s15.name] = s15

    # ---------- 16 預測 vs 實際 ----------
    rr = list(R.index)
    s16 = Sheet("16_預測vs實際", "16 預測 vs 實際",
                "", "預測入選從 14 頁「最終入選」帶過來（會跟著門檻改變）；實際入選是 00940 調整後持股。未入選原因是 Python 依預設門檻寫的。",
                f"data/holdings.csv：舊成分股 {P['old_date']}、新成分股 {P.get('new_date')}",
                rr,
                params=[("hit", "★ 命中檔數", "=COUNTIFS({R:預測入選},TRUE,{R:實際入選},TRUE)", NUM0, "50 檔中猜對幾檔", False),
                        ("add", "新增命中", "=COUNTIFS({R:預測入選},TRUE,{R:實際入選},TRUE,{R:舊成分股},FALSE)", NUM0, "", False),
                        ("del", "刪除命中", "=COUNTIFS({R:預測入選},FALSE,{R:實際入選},FALSE,{R:舊成分股},TRUE)", NUM0, "", False)],
                cols=[dict(h="代號", v=pd.Series(rr, index=rr), w=8), dict(h="名稱", v=R["名稱"], w=10),
                      dict(h="舊成分股", v=R["舊成分股"], w=7),
                      dict(h="預測入選", f='IFERROR({L:14_綜合分數_選股:最終入選},FALSE)', kind="link", flag=True, w=7),
                      dict(h="實際入選", v=R["實際入選"], w=7),
                      dict(h="結果", w=16, f='IF({c:預測入選}=({c:實際入選}=TRUE),"✅ 命中"&IF({c:實際入選},IF({c:舊成分股},"－保留","－新增"),"－刪除"),IF({c:預測入選},"❌ 預測入選實際沒有","❌ 實際入選預測沒有"))'),
                      dict(h="分數排名", f='IFERROR({L:14_綜合分數_選股:分數排名},"")', kind="link", fmt=NUM0, w=7),
                      dict(h="綜合分數", f='IFERROR({L:14_綜合分數_選股:綜合分數},"")', kind="link", fmt="0.000", w=8),
                      dict(h="未入選原因(預設門檻)", v=R["未入選原因(預測)"], w=40, kind="calc"),
                      dict(h="預測權重", v=R["預測權重%"] / 100, fmt=PCT2, w=8, kind="calc"),
                      dict(h="實際權重", v=R["實際權重%(新)"] / 100, fmt=PCT2, w=8),
                      dict(h="舊權重", v=R["舊權重%"] / 100, fmt=PCT2, w=8)])
    SHEETS[s16.name] = s16

    # ---------- 00 流程、01 欄位 ----------
    flow = [
        ("02_母體", "前 300 大母體", "{P:02_母體:n}"),
        ("03_流動性", "流動性（既有免檢驗）", "{P:03_流動性:n}"),
        ("04_非KY_EPS_配息", "非 KY、EPS > 0、近 3 年配息、金融公積、5 月年配已除息", "{P:04_非KY_EPS_配息:n}"),
        ("05_負債比", "負債比 前 90%", "{P:05_負債比:n}"),
        ("06_ROA_ROE", "ROA、ROE 前 90%", "{P:06_ROA_ROE:n}"),
        ("07_益本比", "近 2 季益本比 前 80%", "{P:07_益本比:n}"),
        ("08_稅後淨利", "近 4 季稅後淨利 前 90%", "{P:08_稅後淨利:n}"),
        ("09_毛利率成長", "毛利率成長 前 90%（金融免）", "{P:09_毛利率成長:n}"),
        ("10_大陸投資", "大陸投資集中度 後 95%", "{P:10_大陸投資:n}"),
        ("11_長期成長", "長期成長 前 75%（只看新進）", "{P:11_長期成長:n}"),
        ("12_第一階段彙整", "第一階段全部通過（含放寬）", "{P:12_第一階段彙整:n}"),
        ("13_下行風險", "第二階段：下行風險 前 90%", "{P:13_下行風險:n}"),
        ("14_綜合分數_選股", "綜合分數 + 緩衝區，選出 50 檔", "{P:14_綜合分數_選股:n}"),
        ("15_權重", "股利率加權 + 上限", ""),
        ("16_預測vs實際", "跟 00940 實際持股比對：命中檔數", "{P:16_預測vs實際:hit}"),
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "00_流程總覽"
    hdr = ["步驟", "工作頁", "判斷", "檔數（公式）"]
    for j, h in enumerate(hdr, 1):
        c = ws.cell(1, j, h); c.font = Font(name=FONT, bold=True, color="FFFFFF"); c.fill = HEAD_FILL
    for i, (page, desc, ref) in enumerate(flow, 1):
        r = 1 + i
        ws.cell(r, 1, i).font = Font(name=FONT)
        c = ws.cell(r, 2, page); c.font = Font(name=FONT, color="0563C1", underline="single"); c.hyperlink = f"#'{page}'!A1"
        ws.cell(r, 3, desc).font = Font(name=FONT)
        if ref:
            c = ws.cell(r, 4, resolve(ref, None, r)); c.font = Font(name=FONT, color=GREEN); c.number_format = NUM0
    r = 2 + len(flow)
    c = ws.cell(r, 2, "12_第一階段彙整"); c.font = Font(name=FONT, color="0563C1", underline="single"); c.hyperlink = f"#'12_第一階段彙整'!A1"
    ws.cell(r, 3, "放寬倍數（第一階段門檻一起乘的倍數）").font = Font(name=FONT)
    c = ws.cell(r, 4, resolve("{P:12_第一階段彙整:k}", None, r)); c.font = Font(name=FONT, color=GREEN); c.number_format = "0.00"
    ws.freeze_panes = "A2"
    for col, w in zip("ABCD", [6, 20, 60, 14]):
        ws.column_dimensions[col].width = w

    src = [
        ("price_o_*.csv", "市值(百萬元)", "02 母體排名、15 權重上限、14 自由現金流收益率"),
        ("price_o_*.csv", "流通在外股數(千股)", "07 原始股價 = 市值 ÷ 股數"),
        ("price_o_*.csv", "收盤價(元)（還原價）", "13 下行風險的日報酬"),
        ("price_o_*.csv", "成交值(千元)、週轉率％", "03 流動性、15 近 3 月日均成交值"),
        ("IND.csv", "TSE產業_名稱", "排除建材營造、存託憑證；金融業分組"),
        ("MK25.csv、MK26.csv", "全額交割股、變更交易", "02 母體排除"),
        ("HOLD.csv", f"董監總持股%、經理人總持股%、大股東持股(TSE)%、董監事親屬持股%（{P['hold_month']}）", "02 自由流通量"),
        ("BS21_26.csv", f"負債總額、資產總額（{ql}）", "05 負債比"),
        ("BS21_26.csv", f"母公司股東權益合計（{ql}）", "10 大陸投資 ÷ 淨值"),
        ("PL21_26.csv", f"每股盈餘（{q4[0]}～{q4[-1]}）", "04 近 4 季 EPS、07 近 2 季益本比"),
        ("PL21_26.csv", "稅前淨利、所得稅費用", "08 稅後淨利、11 長期成長（3 年前同期）"),
        ("PL21_26.csv", f"營業收入淨額、營業毛利（{q4p[0]}～{q4[-1]}）", "09 毛利率成長"),
        ("ROE21_26.csv", "ROA(A)稅後息前、ROE(A)－稅後", "06 ROA、ROE"),
        ("CF21_26.csv", "來自營運之現金流量、購置不動產廠房設備", "14 自由現金流收益率"),
        ("CHA.csv", f"期末帳面值(NTD)（{P['cha_q']}）", "10 大陸投資"),
        ("EA_split_20_22、EA_split_23_26.csv", "盈餘分派_年度、股息分配型態、現金股利(元)、現金股利(元)_公積、董事會日期", "04 近 3 年配息、金融公積；14 股利率"),
        ("data/corp_actions.csv", "除息日", "04 5 月年配已除息"),
        ("data/holdings.csv", "00940 每日持股", "既有成分股、實際結果、15 規模與實際權重"),
        ("（沒有用到）", "PL 加權平均股數、BS 股東權益總額／普通股股本、CF 處分PPE／投資活動現金流、ROE 其他定義、CHA 其他欄位、MK 融資融券／處置", "保留給測試其他定義"),
    ]
    render_params(wb)
    for name in SHEETS:
        render(wb, SHEETS[name])

    doc = build_doc(cutoff, P, k_used, src, flow)
    return wb, doc, res



# ---- 說明檔（跟數值分開）------------------------------------------------------
def human(tpl):
    """把公式模板轉成看得懂的寫法：[欄名] = 同一列，[欄名](整欄) = 整欄，〈參數〉。"""
    def rep(mo):
        kind, arg = mo.group(1), mo.group(2)
        if kind == "c":
            return f"[{arg}]"
        if kind == "R":
            return f"[{arg}](整欄)"
        if kind == "p":
            return f"〈{arg}〉"
        page, key = arg.split(":", 1)
        if kind == "P":
            return f"{page}〈{key}〉"
        if kind == "X":
            return f"{page}[{key}](整欄)"
        if kind == "L":
            return f"{page} 同一檔的[{key}]"
        return mo.group(0)
    return "=" + TOKEN.sub(rep, tpl)


def _head(ws, r, labels, widths=None):
    for j, h in enumerate(labels, 1):
        c = ws.cell(r, j, h)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(vertical="center")
    if widths:
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w


def _row(ws, r, vals):
    for j, x in enumerate(vals, 1):
        c = ws.cell(r, j, x)
        c.font = Font(name=FONT)
        c.alignment = Alignment(wrap_text=True, vertical="top")


def build_doc(cutoff, P, k_used, src, flow):
    wb = Workbook()
    ws = wb.active
    ws.title = "閱讀方式"
    ws["A1"] = f"00940 預測檢查表：說明（資料截止日 {cutoff}，生效日 {P['effective']}）"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    legend = [
        ("對照的數值檔", f"00940_檢查_{cutoff}.xlsx；工作頁名稱跟這份一樣，可以並排看"),
        ("藍字", "原始資料數值，直接從 TEJ 匯出檔或 00940 持股抄過來，可以拿去跟 TEJ 對"),
        ("黑字", "Excel 公式（比率、排名、門檻、是否通過），點儲存格可以看公式"),
        ("綠字", "從別的工作頁帶過來的值（用代號查同一檔股票）"),
        ("參數頁", "所有門檻和中間結果（通過檔數、放寬倍數、縮尾上下限…）集中在數值檔的「參數」頁；其他工作頁第 1 列就是欄位名稱"),
        ("黃底", "「參數」頁裡可以自己改的參數。改了之後放寬倍數、第一階段檔數、最後 50 檔都會重算"),
        ("綠底 TRUE／紅底 FALSE", "通過／沒通過"),
        ("Python 算好的數值", "02 母體（緩衝區要一期一期往後推）、13 下行風險（252 天 × 300 檔太大）、15 最終權重（反覆分配）。改了門檻這三個不會跟著變"),
        ("名次%", "由好到差的名次 ÷ 有資料的檔數。例如前 90% = 名次% ≤ 90%。同值同名次（取最前面）"),
        ("需要倍數", "名次% ÷ 原門檻，代表這檔要放寬到幾倍才會過；12 頁取各項最大值"),
        ("放寬倍數", f"第一階段不到 90 檔時所有門檻一起乘的倍數，這次是 {k_used:.2f}"),
        ("公式寫法（本說明檔）", "[欄名] = 同一列那一欄；[欄名](整欄) = 整欄；〈參數〉= 參數頁裡這一頁的參數；「某頁 同一檔的[欄名]」= 用代號去那一頁查"),
    ]
    _head(ws, 3, ["項目", "說明"], [24, 110])
    for i, row in enumerate(legend, 4):
        _row(ws, i, row)
    r0 = 5 + len(legend)
    _head(ws, r0, ["步驟", "工作頁", "判斷"])
    for i, (page, desc, _) in enumerate(flow, 1):
        _row(ws, r0 + i, [i, page, desc])

    ws2 = wb.create_sheet("01_欄位來源")
    _head(ws2, 1, ["檔案", "欄位", "用在哪一頁"], [30, 70, 45])
    for i, row in enumerate(src, 2):
        _row(ws2, i, row)

    for sh in SHEETS.values():
        w = wb.create_sheet(sh.name)
        w["A1"] = sh.title
        w["A1"].font = Font(name=FONT, bold=True, size=14)
        _head(w, 3, ["項目", "內容"], [22, 40, 70, 90])
        for i, (lab, text) in enumerate([("規則", sh.rule), ("這版做法", sh.method), ("資料來源", sh.source),
                                         ("列出的股票", f"{len(sh.rows)} 檔")], 4):
            _row(w, i, [lab, text])
            w.merge_cells(start_row=i, start_column=2, end_row=i, end_column=4)
            w.row_dimensions[i].height = 48 if len(str(text)) > 90 else 18
        r = 9
        if sh.params:
            _head(w, r, ["參數", "預設值／公式", "說明", "可以改？"])
            for key, lab, val, fmt, note, editable in sh.params:
                r += 1
                shown = human(val[1:]) if isinstance(val, str) and val.startswith("=") else val
                _row(w, r, [lab, shown, note, "可以（黃底）" if editable else ""])
            r += 2
        _head(w, r, ["欄位", "類型", "說明", "公式"])
        for col in sh.cols:
            r += 1
            if "f" in col:
                kind = "從別頁帶入" if col.get("kind") == "link" else "公式"
                formula = human(col["f"])
            else:
                kind = {"input": "原始資料", "calc": "Python 計算"}[col.get("kind", "input")]
                formula = ""
            _row(w, r, [col["h"], kind, col.get("note", ""), formula])

    ws3 = wb.create_sheet("17_假設說明")
    _head(ws3, 1, ["項目", "這版的做法"], [20, 120])
    for i, row in enumerate(m.ASSUMPTIONS, 2):
        _row(ws3, i, row)
    return wb


if __name__ == "__main__":
    cutoff = sys.argv[1] if len(sys.argv) > 1 else "2026-04-30"
    wb, doc, res = build(cutoff)
    for book, name in [(wb, f"00940_檢查_{cutoff}.xlsx"), (doc, f"00940_檢查說明_{cutoff}.xlsx")]:
        out = os.path.join(m.PD, name)
        try:
            book.save(out)
        except PermissionError:  # 檔案正在 Excel 裡開著
            out = out.replace(".xlsx", f"_{pd.Timestamp.now():%m%d_%H%M}.xlsx")
            book.save(out)
        print("輸出：", out)
