"""可轉債的承銷方式（公開競標 / 詢價圈購）。

「代收價款及存儲專戶行庫」公告本身不寫承銷方式，這裡從證券商公會的「證券商承銷公告」查詢系統
（https://web.twsa.org.tw/Edoc2/）對照：它一年一張表，列出每件承銷案的發行人全名、發行種類
（有/無擔保轉換公司債）、配售方式（競價拍賣 / 詢價圈購 / 公開申購配售 / 洽商銷售）。另一張「競拍公告」
表有投標期間與最低承銷價格。承銷公告通常在代收價款公告之後 1～3 週出現，所以新公告要過幾天才查得到。

    python cb_method.py          對還沒有承銷方式的公告再比對一次（update.py 每天會自動跑）
"""

import datetime as dt
import html
import os
import re
import sys

import mops
import sources

TWSA = "https://web.twsa.org.tw/Edoc2/Default.aspx"
TWSE_INFO = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_INFO = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
METHOD_NAMES = {"競價拍賣": "公開競標", "詢價圈購": "詢價圈購", "公開申購配售": "公開申購", "洽商銷售": "洽特定人"}
WINDOW_BEFORE, WINDOW_AFTER = 10, 150  # 承銷公告申報日相對於代收價款公告日的容許範圍（天）


def norm(name):
    """公司名稱正規化，讓觀測站、OpenAPI、公會三邊對得起來。
    KY 公司常寫成「JPP Holding Company Limited(經寶精密控股-KY)」，括號裡有中文就用括號裡的。"""
    s = name or ""
    m = re.search(r"[（(]([^（）()]*[一-鿿][^（）()]*)[）)]", s)
    if m and not re.search(r"[一-鿿]", re.sub(r"[（(].*?[）)]", "", s)):
        s = m.group(1)
    s = re.sub(r"[（(].*?[）)]", "", s)
    s = re.sub(r"股份有限公司|股份有公司|有限公司|公司|-KY|-創新版|-創|\*|\s", "", s)
    return s.replace("啓", "啟").replace("臺", "台")


def _rows(text, min_cols):
    tables = re.findall(r"<table.*?</table>", text, re.S)
    body = max(tables, key=len) if tables else ""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) >= min_cols:
            out.append([html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in tds])
    return out


def twsa_year(year):
    """承銷公告 -> [{seq, date, underwriter, issuer, kind, method}]，只留轉換／交換公司債"""
    r = sources._request("GET", TWSA, params={"Year": year})
    r.raise_for_status()
    out = []
    for c in _rows(r.text, 10):
        if "轉換公司債" in c[6] or "交換公司債" in c[6]:
            out.append({"seq": c[0], "date": c[1].replace("/", "-"), "underwriter": c[2], "issuer": c[3],
                        "kind": c[6], "method": [METHOD_NAMES.get(m, m) for m in (c[7], c[8]) if m]})
    return out, r.text


def twsa_auction(year, page_html):
    """競拍公告（同一頁的 postback）-> [{issuer, kind, bid_period, floor, auction_qty}]"""
    def hidden(name):
        m = re.search(r'id="%s"[^>]*value="([^"]*)"' % name, page_html)
        return m.group(1) if m else ""
    data = {"__EVENTTARGET": "ctl00$cphMain$rblReportType", "__EVENTARGUMENT": "",
            "__VIEWSTATE": hidden("__VIEWSTATE"), "__VIEWSTATEGENERATOR": hidden("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": hidden("__EVENTVALIDATION"),
            "ctl00$cphMain$rblReportType": "Auction", "ctl00$cphMain$ddlYear": str(year)}
    r = sources._request("POST", TWSA, params={"Year": year}, data=data)
    r.raise_for_status()
    return [{"issuer": c[1], "underwriter": c[2], "kind": c[3], "auction_qty": c[5], "bid_period": c[6], "floor": c[7]}
            for c in _rows(r.text, 8) if "轉換公司債" in c[3] or "交換公司債" in c[3]]


def full_names():
    """股票代號 -> 公司全名（上市用證交所 OpenAPI、上櫃用櫃買 OpenAPI）"""
    names = {}
    for url, code_key, name_key in ((TWSE_INFO, "公司代號", "公司名稱"), (TPEX_INFO, "SecuritiesCompanyCode", "CompanyName")):
        try:
            for x in sources._request("GET", url).json():
                names[str(x.get(code_key, "")).strip()] = x.get(name_key, "")
        except Exception:
            pass
    return names


def issues_in_subject(subject):
    """主旨裡有幾檔債券、各自的擔保別：'第四及第五次無擔保' -> [('無擔保'), ('無擔保')]"""
    s = re.sub(r"\s+", "", subject)
    guarantee = "有擔保" if "有擔保" in s else "無擔保" if "無擔保" in s else ""
    n = len(re.findall(r"第[一二三四五六七八九十0-9]+次", s)) or 1
    return [guarantee] * n


METHOD_ORDER = {"公開競標": 0, "詢價圈購": 1, "公開申購": 2, "洽特定人": 3}


def match(cb_rows, twsa, auctions, names):
    """把承銷案配給還沒有方式的公告。回傳更新的筆數。

    先用承銷公告表，沒有的話用競拍公告表（它比承銷公告早出現，只有競拍案）。同公司同擔保別的案件
    一案只用一次；先在 45 天內配對，剩下的再放寬到 150 天，避免早的公告搶走晚的公告該配的案。"""
    by_issuer = {}
    for t in twsa:
        by_issuer.setdefault(norm(t["issuer"]), []).append(t)
    auc_by_issuer = {}
    for i, a in enumerate(auctions):
        a = dict(a, seq=f"A{i}", date=a["bid_period"][:10].replace("/", "-"), method=["公開競標"])
        auc_by_issuer.setdefault(norm(a["issuer"]), []).append(a)
    used = {s for r in cb_rows if r.get("method_seq") for s in r["method_seq"].split(",")}
    pending = {id(r): {} for r in cb_rows if not r.get("method") or r["method"] == "未知"}
    updated = set()

    def pick(cands, d, limit):
        ok = [c for c in cands if c["seq"] not in used and -WINDOW_BEFORE <= (dt.date.fromisoformat(c["date"]) - d).days <= limit]
        return min(ok, key=lambda c: abs((dt.date.fromisoformat(c["date"]) - d).days)) if ok else None

    for limit in (45, WINDOW_AFTER):
        for r in sorted(cb_rows, key=lambda x: x["date"]):
            if id(r) not in pending:
                continue
            key = norm(names.get(r["code"], "")) or norm(r["name"])
            d = dt.date.fromisoformat(r["date"])
            slots = pending[id(r)]
            for i, g in enumerate(issues_in_subject(r["subject"])):
                if i in slots:
                    continue
                guard = lambda c: (not g or g in c["kind"])
                c = pick([x for x in by_issuer.get(key, []) if guard(x)], d, limit)                     or pick([x for x in auc_by_issuer.get(key, []) if guard(x)], d, limit)
                if not c:
                    continue
                used.add(c["seq"])
                m = "/".join(c["method"]) or "未知"
                if c["seq"].startswith("A"):
                    det = f"公會競拍公告（投標 {c['bid_period']}，底價 {c['floor']}，{c['underwriter']}）"
                else:
                    det = f"公會承銷公告 {c['seq']}（{c['date']}，{c['underwriter']}）"
                    if "公開競標" in m:
                        a = [x for x in auc_by_issuer.get(key, []) if guard(x)]
                        if a:
                            det += f"；投標 {a[0]['bid_period']}，底價 {a[0]['floor']}"
                slots[i] = (m, c["seq"], det)
            if slots:
                methods = sorted({m for m, _, _ in slots.values()}, key=lambda x: METHOD_ORDER.get(x, 9))
                r["method"] = "/".join(methods)
                r["method_seq"] = ",".join(s for _, s, _ in slots.values())
                r["method_detail"] = "；".join(det for _, _, det in slots.values())
                updated.add(id(r))
    return len(updated)


def update(max_age_days=200):
    """對最近 max_age_days 內還沒有承銷方式的公告再比對。回傳訊息。"""
    rows = mops.load_cb()
    today = dt.date.today()
    todo = [r for r in rows if (not r.get("method") or r["method"] == "未知")
            and (today - dt.date.fromisoformat(r["date"])).days <= max_age_days]
    if not todo:
        return "承銷方式：沒有待比對的公告"
    years = sorted({today.year, today.year - 1 if today.month <= 3 else today.year, min(dt.date.fromisoformat(r["date"]).year for r in todo)})
    twsa, auctions = [], []
    for y in years:
        t, page = twsa_year(y)
        twsa += t
        try:
            auctions += twsa_auction(y, page)
        except Exception:
            pass  # 競拍表只是補充投標期間，失敗不影響判斷
    n = match(rows, twsa, auctions, full_names())
    for r in rows:
        if not r.get("method"):
            r["method"] = "未知"
    mops.save_cb(rows)
    left = sum(1 for r in rows if r["method"] == "未知" and (today - dt.date.fromisoformat(r["date"])).days <= max_age_days)
    return f"承銷方式：新比對到 {n} 筆，還有 {left} 筆未知（承銷公告通常晚 1～3 週）"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(update())
