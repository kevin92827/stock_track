"""可轉債的承銷方式（公開競標 / 詢價圈購）。

「代收價款及存儲專戶行庫」公告本身不寫承銷方式，這裡用兩個來源對照：
1. 公司自己的「董事會決議發行…轉換公司債」重大訊息（主要來源）：內文第 11 點「承銷方式:採詢價圈購方式辦理公開承銷」
   直接寫明，而且比代收價款公告早 1～3 個月。用觀測站的主旨關鍵字檢索找出來，內文抓一次後存在 data/cb_board.csv。
2. 證券商公會的「證券商承銷公告」查詢系統（https://web.twsa.org.tw/Edoc2/ ，備援）：一年一張表，列出每件承銷案的
   發行人全名、發行種類、配售方式；另一張「競拍公告」表有投標期間與最低承銷價格（公開競標的會附在說明裡）。

    python cb_method.py          對還沒有承銷方式的公告再比對一次（update.py 每天會自動跑）
"""

import datetime as dt
import html
import os
import re
import sys

import csv
import html as html_lib

import mops
import sources

BOARD_PATH = os.path.join(mops.BASE, "data", "cb_board.csv")
BOARD_FIELDS = ["date", "time", "code", "name", "market", "subject", "enter_date", "serial", "issues", "guarantee",
                "method", "underwriter"]
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

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


def cn2int(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        a, b = s.split("十", 1)
        return (CN_NUM.get(a, 1) if a else 1) * 10 + (CN_NUM.get(b, 0) if b else 0)
    return CN_NUM.get(s)


def issue_numbers(subject):
    """主旨裡的「第X次」-> [X,...]（'第一次暨第二次' -> [1, 2]）"""
    s = re.sub(r"\s+", "", subject or "")
    return [n for n in (cn2int(x) for x in re.findall(r"第([一二三四五六七八九十0-9]+)次", s)) if n]


def method_words(text):
    out = []
    for kw, name in (("競價拍賣", "公開競標"), ("詢價圈購", "詢價圈購"), ("公開申購", "公開申購"), ("洽特定人", "洽特定人"),
                     ("洽商", "洽特定人")):
        if kw in text and name not in out:
            out.append(name)
    return out


def parse_board_body(body):
    """董事會決議內文 -> (承銷方式 {第X次: 方式} 或 {None: 方式}, 承銷商)"""
    lines = body.split("\n")
    method_line = next((l for l in lines if "承銷方式" in l), "")
    # 若同一行寫了多檔不同方式（第一次採競價拍賣、第二次採詢價圈購），逐檔拆
    per = {}
    for m in re.finditer(r"第([一二三四五六七八九十0-9]+)次[^，、；;,。]*?(競價拍賣|詢價圈購|公開申購|洽特定人|洽商)", method_line):
        n = cn2int(m.group(1))
        if n:
            per[n] = method_words(m.group(2))[0]
    if not per:
        ws = method_words(method_line)
        per = {None: "/".join(ws)} if ws else {}
    uw_line = next((l for l in lines if "承銷或代銷機構" in l or "承銷商" in l), "")
    uw = uw_line.split(":", 1)[-1].split("：", 1)[-1].strip()
    return per, uw


def _search_page(kind, year, page):
    """觀測站主旨關鍵字檢索：「董事會決議」且含「轉換公司債」，一頁 100 筆"""
    params = {"encodeURIComponent": 1, "step": 1, "Stp": 4, "firstin": True, "off": 1, "go": False, "r1": "1",
              "KIND": kind, "CODE": "", "keyWord": "董事會決議", "Condition2": "1", "keyWord2": "轉換公司債",
              "year": str(year), "month1": "0", "begin_day": "", "end_day": "", "Orderby": "1", "PCount": "100",
              "pagenum": str(page)}
    j = mops._post("https://mops.twse.com.tw/mops/api/redirectToOld", {"apiName": "ajax_t51sb10", "parameters": params})
    r = mops.request("GET", j["result"]["url"])
    r.encoding = "utf-8"
    rows = {}
    for name, val in re.findall(r"<input type='hidden' name='h(\d+)' value='([^']*)'", r.text):
        rows.setdefault(name[:-1], {})[int(name[-1])] = html_lib.unescape(val)
    out = []
    for x in rows.values():
        if not x.get(1) or not x.get(8):
            continue
        d, e = x[2], x[8]
        out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "time": x.get(3, ""), "code": x[1].strip(), "name": x.get(0, "").strip(),
                    "market": x.get(6, ""), "subject": re.sub(r"\s+", "", x.get(4, "")),
                    "enter_date": f"{int(e[:4]) - 1911}{e[4:]}", "serial": x.get(5, "")})
    return out, len(rows) >= 100


def load_board():
    if not os.path.exists(BOARD_PATH):
        return {}
    with open(BOARD_PATH, encoding="utf-8-sig", newline="") as f:
        return {(r["date"], r["code"], r["serial"]): r for r in csv.DictReader(f)}


def save_board(board):
    os.makedirs(os.path.dirname(BOARD_PATH), exist_ok=True)
    tmp = BOARD_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BOARD_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in BOARD_FIELDS} for _, r in sorted(board.items()))
    os.replace(tmp, BOARD_PATH)


def refresh_board(years, kinds=("L", "O", "R"), need_codes=None):
    """抓董事會決議公告清單並補內文（只對 need_codes 裡的公司抓內文，None = 全部）。回傳 board dict。"""
    board = load_board()
    for y in years:
        for kind in kinds:
            page = 1
            while True:
                items, more = _search_page(kind, y - 1911, page)
                for it in items:
                    key = (it["date"], it["code"], it["serial"])
                    if key not in board:
                        board[key] = dict(it, issues="", guarantee="", method="", underwriter="")
                if not more or page >= 5:
                    break
                page += 1
    for key, r in board.items():
        if r.get("method") or (need_codes is not None and r["code"] not in need_codes):
            continue
        if "轉換公司債" not in r["subject"]:
            continue
        try:
            j = mops._post("https://mops.twse.com.tw/mops/api/t05st02_detail",
                           {"companyId": r["code"], "marketKind": r["market"], "enterDate": r["enter_date"],
                            "serialNumber": int(r["serial"])})
            body = j["result"]["data"][0][-1]
        except Exception:
            continue
        per, uw = parse_board_body(body)
        r["issues"] = ",".join(str(n) for n in issue_numbers(r["subject"]))
        r["guarantee"] = "有擔保" if "有擔保" in r["subject"] else "無擔保" if "無擔保" in r["subject"] else ""
        r["method"] = ";".join(f"{k or ''}:{v}" for k, v in per.items()) if per else "none"
        r["underwriter"] = uw
    save_board(board)
    return board


def match_board(cb_rows, board):
    """用董事會決議公告決定承銷方式（只處理還沒有方式的公告）。回傳更新筆數。"""
    by_code = {}
    for r in board.values():
        if r.get("method") and r["method"] != "none":
            by_code.setdefault(r["code"], []).append(r)
    n = 0
    for r in cb_rows:
        if r.get("method") and r["method"] != "未知":
            continue
        d = dt.date.fromisoformat(r["date"])
        # 董事會決議通常在代收價款公告前 1～3 個月，最多往前找 200 天，往後容許 5 天
        cands = [b for b in by_code.get(r["code"], []) if -5 <= (d - dt.date.fromisoformat(b["date"])).days <= 200]
        if not cands:
            continue
        wanted = issue_numbers(r["subject"])
        methods, used, details = [], [], []
        for num in wanted or [None]:
            best = None
            for b in sorted(cands, key=lambda x: x["date"], reverse=True):
                issues = [int(x) for x in b["issues"].split(",") if x]
                if num is None or num in issues or not issues:
                    best = b
                    break
            if not best:
                continue
            per = dict(item.split(":", 1) for item in best["method"].split(";"))
            m = per.get(str(num)) or per.get("") or "/".join(dict.fromkeys(per.values()))
            if m:
                methods.append(m)
                used.append(f"B:{best['date']}:{best['code']}:{best['serial']}")
                details.append(f"{best['date']} 董事會決議公告「{best['subject'][:40]}」承銷方式：{m}"
                               + (f"，{best['underwriter']}" if best["underwriter"] else ""))
        if methods:
            ms = sorted({x for m in methods for x in m.split("/")}, key=lambda x: METHOD_ORDER.get(x, 9))
            r["method"] = "/".join(ms)
            r["method_seq"] = ",".join(dict.fromkeys(used))
            r["method_detail"] = "；".join(dict.fromkeys(details))
            n += 1
    return n


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
    """對最近 max_age_days 內還沒有承銷方式的公告比對：先找公司的董事會決議公告，再用公會承銷公告。回傳訊息。"""
    rows = mops.load_cb()
    today = dt.date.today()
    todo = [r for r in rows if (not r.get("method") or r["method"] == "未知")
            and (today - dt.date.fromisoformat(r["date"])).days <= max_age_days]
    if not todo:
        return "承銷方式：沒有待比對的公告"
    first = min(dt.date.fromisoformat(r["date"]) for r in todo)
    years = sorted({today.year, (first - dt.timedelta(days=200)).year})
    n1 = n2 = 0
    try:
        board = refresh_board(years, need_codes={r["code"] for r in todo})
        n1 = match_board(rows, board)
    except mops.Blocked as e:
        return f"承銷方式：{e}"
    except Exception as e:
        print(f"董事會決議公告查詢失敗：{type(e).__name__}: {e}")
    still = [r for r in todo if not r.get("method") or r["method"] == "未知"]
    if still:
        twsa, auctions = [], []
        for y in years:
            t, page = twsa_year(y)
            twsa += t
            try:
                auctions += twsa_auction(y, page)
            except Exception:
                pass
        n2 = match(rows, twsa, auctions, full_names())
    for r in rows:
        if not r.get("method"):
            r["method"] = "未知"
    mops.save_cb(rows)
    left = sum(1 for r in rows if r["method"] == "未知" and (today - dt.date.fromisoformat(r["date"])).days <= max_age_days)
    return f"承銷方式：董事會決議比對到 {n1} 筆、公會公告比對到 {n2} 筆，還有 {left} 筆未知"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(update())
