"""除權息、減資、面額變更、ETF 分割：會讓參考價跳動的事件，存在 data/corp_actions.csv。

每筆的 date 是「生效日」：除權息交易日，或減資／面額變更／分割後的恢復買賣日，也就是第一個用新價格交易的日子。
來源是證交所與櫃買中心的預告表和計算結果表。預告的（status=forecast）還沒有參考價，
到了生效日交易所公布參考價後變成 final。每次更新 11 次請求。

除權息另外從公開資訊觀測站的「股利分派公告資料彙總表」補上公司公告的日期與時間（announce_date、announce_time），
以及現金增資的認購價；ETF、減資、面額變更沒有這份資料。現金增資造成的除權，標籤改成「增資」。

    python corp_actions.py              更新最近兩週與未來的預告（每日執行用）
    python corp_actions.py 2025-10-01   從指定日期開始回補
"""

import csv
import datetime as dt
import os
import re
import sys

import mops
import prices

BASE = os.path.dirname(os.path.abspath(__file__))
CA_PATH = os.path.join(BASE, "data", "corp_actions.csv")
FIELDS = ["date", "code", "name", "market", "kind", "label", "prev_close", "ref_price",
          "cash", "stock", "ratio", "refund", "reason", "stop_date", "status",
          "announce_date", "announce_time"]
TWSE = "https://www.twse.com.tw/rwd/zh/"
TPEX = "https://www.tpex.org.tw/www/zh-tw/bulletin/"
TPEX_FORECAST = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
AHEAD = 180  # 往後查幾天的預告
LABELS = {"息": "息", "權": "權", "權息": "權息", "除息": "息", "除權": "權", "除權息": "權息"}


def roc(s):
    """民國日期（115年10月08日、115/10/12、1151012）-> 2026-10-08；看不懂回空字串"""
    s = str(s).strip()
    p = re.findall(r"\d+", s)
    if len(p) == 1 and len(p[0]) == 7:
        p = [p[0][:3], p[0][3:5], p[0][5:]]
    if len(p) < 3:
        return ""
    return f"{int(p[0]) + 1911}-{int(p[1]):02d}-{int(p[2]):02d}"


def ymd(s):
    """20250619 -> 2025-06-19"""
    s = str(s).strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else ""


def val(s):
    """數字欄位；「-」「--」「NA」或夾帶 HTML 的說明文字都當作沒有"""
    v = prices.num(s)
    return v if v else None


def per_share(s):
    """無償配股率：有的表是每股配幾股，有的是每仟股配幾股，一律換成每股"""
    v = val(s)
    if v is None:
        return None
    return v / 1000 if v >= 1 else v


def detail(html, key):
    """櫃買「詳細資料」欄是一段 HTML 表格，取出某一列的值"""
    m = re.search(re.escape(key) + r"[^<]*</th><td>([^<]*)", html or "")
    return m.group(1).replace("&nbsp", " ").strip() if m else ""


def row(date, code, name, market, kind, label, **kw):
    r = dict.fromkeys(FIELDS, "")
    r.update(date=date, code=str(code).strip(), name=str(name).strip(), market=market, kind=kind, label=label)
    for k, v in kw.items():
        r[k] = "" if v is None else v
    r["status"] = "final" if r["ref_price"] != "" else "forecast"
    return r


def _twse(path, **params):
    j = prices._get(TWSE + path, response="json", **params)
    return j.get("data") or []


def _tpex(path, **params):
    j = prices._get(TPEX + path, response="json", **params)
    return ((j.get("tables") or [{}])[0]).get("data") or []


def _rights(ratio, price):
    """現金增資造成的除權：預告表只有配股率和認購價，記在 reason 欄"""
    if not val(ratio):
        return ""
    return "現金增資" + (f"（認購價 {val(price):g} 元）" if val(price) else "")


# ---- 各來源：都回傳 [row]。start、end 是 datetime.date ----

def twse_dividend(start, end):
    """上市除權息：計算結果表（已發生，有參考價）"""
    return [row(roc(r[0]), r[1], r[2], "sii", "dividend", LABELS.get(r[6].strip(), r[6].strip()),
                prev_close=val(r[3]), ref_price=val(r[4]))
            for r in _twse("exRight/TWT49U", startDate=start.strftime("%Y%m%d"), endDate=min(end, dt.date.today()).strftime("%Y%m%d"))]


def twse_dividend_forecast(start, end):
    """上市除權息預告表：現金股利還沒公告的（ETF 常見）金額留空"""
    return [row(roc(r[0]), r[1], r[2], "sii", "dividend", LABELS.get(r[3].strip(), r[3].strip()),
                cash=val(r[7]), stock=per_share(r[4]), reason=_rights(r[5], r[6]))
            for r in _twse("exRight/TWT48U")]


def twse_reduction(start, end):
    """上市減資恢復買賣參考價；還沒恢復買賣的也會先列出來，只是沒有價格"""
    return [row(roc(r[0]), r[1], r[2], "sii", "reduction", "減資",
                prev_close=val(r[3]), ref_price=val(r[4]), reason=r[9].strip())
            for r in _twse("reducation/TWTAUU", startDate=start.strftime("%Y%m%d"), endDate=end.strftime("%Y%m%d"))]


def twse_reduction_forecast(start, end):
    """上市減資預告表：停止買賣日、換股率、每股退還股款"""
    return [row(roc(r[3]), r[1], r[2], "sii", "reduction", "減資",
                ratio=val(r[4]), refund=val(r[6]), reason=r[5].strip(), stop_date=roc(r[0]))
            for r in _twse("reducation/TWTAVU")]


def twse_par(start, end):
    """上市變更股票面額恢復買賣參考價；詳細資料欄是「代號,停止買賣日,恢復買賣日」"""
    out = []
    for r in _twse("change/TWTB8U", startDate=start.strftime("%Y%m%d"), endDate=end.strftime("%Y%m%d")):
        prev, ref = val(r[3]), val(r[4])
        parts = str(r[8]).split(",")
        out.append(row(roc(r[0]), r[1], r[2], "sii", "par", "面額變更", prev_close=prev, ref_price=ref,
                       ratio=round(prev / ref, 4) if prev and ref else None,
                       stop_date=ymd(parts[1]) if len(parts) > 1 else ""))
    return out


def twse_split(start, end):
    """上市 ETF 分割、反分割"""
    out = []
    for r in _twse("split/TWTCAU", startDate=start.strftime("%Y%m%d"), endDate=end.strftime("%Y%m%d")):
        prev, ref = val(r[4]), val(r[5])
        label = r[3].strip() or ("反分割" if prev and ref and ref > prev else "分割")  # 偶爾沒填
        out.append(row(roc(r[0]), r[1], r[2], "sii", "split", label, prev_close=prev, ref_price=ref,
                       ratio=round(prev / ref, 4) if prev and ref else None))
    return out


def tpex_dividend(start, end):
    """上櫃除權除息計算結果表"""
    return [row(roc(r[0]), r[1], r[2], "otc", "dividend", LABELS.get(r[8].strip(), r[8].strip()),
                prev_close=val(r[3]), ref_price=val(r[4]), cash=val(r[13]), stock=per_share(r[14]),
                reason=_rights(r[20], r[16]))
            for r in _tpex("exDailyQ", startDate=start.strftime("%Y/%m/%d"), endDate=min(end, dt.date.today()).strftime("%Y/%m/%d"))]


def tpex_dividend_forecast(start, end):
    """上櫃除權除息預告表（OpenAPI）"""
    return [row(roc(r["ExRrightsExDividendDate"]), r["SecuritiesCompanyCode"], r["CompanyName"], "otc", "dividend",
                LABELS.get(r["ExRrightsExDividend"].strip(), r["ExRrightsExDividend"].strip()),
                cash=val(r.get("CashDividend")), stock=per_share(r.get("StockDividendRatio")),
                reason=_rights(r.get("SubscriptionRatioToNewSharesIssued"), r.get("SubscriptionPricePerShare")))
            for r in prices._get(TPEX_FORECAST)]


def tpex_reduction(start, end):
    """上櫃減資恢復買賣參考價；換股率和退還股款在詳細資料裡"""
    out = []
    for r in _tpex("revivt", startDate=start.strftime("%Y/%m/%d"), endDate=end.strftime("%Y/%m/%d")):
        new = val(detail(r[10], "每壹仟股換發新股票").split()[0] if detail(r[10], "每壹仟股換發新股票") else "")
        refund = val(detail(r[10], "每股退還股款").split()[0] if detail(r[10], "每股退還股款") else "")
        out.append(row(roc(r[0]), r[1], r[2], "otc", "reduction", "減資", prev_close=val(r[3]), ref_price=val(r[4]),
                       ratio=round(new / 1000, 8) if new else None, refund=refund, reason=r[9].strip(),
                       stop_date=roc(detail(r[10], "停止買賣日期"))))
    return out


def tpex_par(start, end):
    """上櫃變更股票面額恢復買賣參考價"""
    return [row(roc(r[0]), r[1], r[2], "otc", "par", "面額變更", prev_close=val(r[3]), ref_price=val(r[4]),
                ratio=val(detail(r[8], "變更股票面額換股率")), stop_date=roc(detail(r[8], "停止買賣日期")))
            for r in _tpex("pvChgRslt", startDate=start.strftime("%Y/%m/%d"), endDate=end.strftime("%Y/%m/%d"))]


def tpex_split(start, end):
    """上櫃 ETF 分割、反分割：[代號, 名稱, 停止買賣日, 分割率, 分割前淨值, 分割後淨值, 恢復買賣日, 參考價]"""
    out = []
    for r in _tpex("etfSplit", startDate=start.strftime("%Y/%m/%d"), endDate=end.strftime("%Y/%m/%d")):
        ratio = val(r[3])
        out.append(row(roc(r[6]), r[0], r[1], "otc", "split", "反分割" if ratio and ratio < 1 else "分割",
                       ref_price=val(r[7]), ratio=ratio, stop_date=roc(r[2])))
    return out


# 預告表排前面，結果表排後面：同一筆事件後面的欄位會蓋掉前面的
SOURCES = [("上市除權息預告", twse_dividend_forecast), ("上櫃除權息預告", tpex_dividend_forecast),
           ("上市減資預告", twse_reduction_forecast),
           ("上市除權息", twse_dividend), ("上櫃除權息", tpex_dividend),
           ("上市減資", twse_reduction), ("上櫃減資", tpex_reduction),
           ("上市面額變更", twse_par), ("上櫃面額變更", tpex_par),
           ("上市 ETF 分割", twse_split), ("上櫃 ETF 分割", tpex_split)]


# ---- 公開資訊觀測站：公司公告除權息的日期 ----

def mops_announcements(market, year, month):
    """某個月公告的股利分派（market 是 sii 或 otc）-> [{code, dates, announce_date, announce_time, cash_issue, price}]
    dates 是這則公告裡的除權交易日和除息交易日。"""
    j = mops._post("https://mops.twse.com.tw/mops/api/redirectToOld",
                   {"apiName": "ajax_t108sb27", "parameters": {
                       "encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "TYPEK": market,
                       "year": str(year - 1911), "month": f"{month:02d}", "type": "1", "co_id": "", "b_date": "", "e_date": ""}})
    r = mops.request("GET", j["result"]["url"])
    r.encoding = "utf-8"
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, flags=re.S | re.I):
        c = [re.sub(r"<[^>]+>|&nbsp;?|\s+", "", x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(c) < 18 or not re.fullmatch(r"\w{4,6}", c[0]) or not roc(c[16]):
            continue
        out.append({"code": c[0], "dates": {d for d in (roc(c[6]), roc(c[10])) if d}, "announce_date": roc(c[16]),
                    "announce_time": c[17][:5], "cash_issue": val(c[12]), "price": val(c[14])})
    return out


def add_announcements(rows, months):
    """把公告日併進 rows（同一筆有多則公告時取最早的）；回傳失敗訊息的 list"""
    index = {}
    for k, r in rows.items():
        if r["kind"] == "dividend":
            index.setdefault((r["code"], r["date"]), []).append(r)
    errors = []
    for market in ("sii", "otc"):
        for y, m in months:
            try:
                found = mops_announcements(market, y, m)
            except mops.Blocked:
                return errors + ["公告日（公開資訊觀測站暫時封鎖）"]
            except Exception as e:
                errors.append(f"公告日 {market} {y}-{m:02d}（{type(e).__name__}）")
                continue
            for a in found:
                for d in a["dates"]:
                    for r in index.get((a["code"], d), []):
                        when = (a["announce_date"], a["announce_time"])
                        if not r["announce_date"] or when < (r["announce_date"], r["announce_time"]):
                            r["announce_date"], r["announce_time"] = when
                        if a["cash_issue"] and not r["reason"]:
                            r["reason"] = "現金增資" + (f"（認購價 {a['price']:g} 元）" if a["price"] else "")
    return errors


def twse_rights_detail(code, date):
    """上市除權的明細（結果表沒有）：-> (每股無償配股, 說明)"""
    j = prices._get(TWSE + "exRight/TWT49UDetail", response="json", STK_NO=code, T1=date.replace("-", ""))
    d = (j.get("data") or [[]])[0]
    if len(d) < 8:
        return None, ""
    free, bonus, issue, price = (val((x.split() or [""])[0]) for x in d[4:8])
    reason = "現金增資" + (f"（認購價 {price:g} 元）" if price else "") if issue else "員工紅利轉增資" if bonus else ""
    return (free / 1000 if free else None), reason


def add_rights_detail(rows):
    """上市的「權」如果既沒有配股率也沒有說明，就查明細分辨是無償配股還是現金增資"""
    errors = 0
    for r in rows.values():
        if (r["market"] == "sii" and r["kind"] == "dividend" and "權" in r["label"] and r["status"] == "final"
                and r["stock"] == "" and r["reason"] == ""):
            try:
                stock, reason = twse_rights_detail(r["code"], r["date"])
            except Exception:
                errors += 1
                continue
            r["stock"] = "" if stock is None else stock
            r["reason"] = reason or ("" if stock else "其他除權")
    return [f"上市除權明細 {errors} 筆"] if errors else []


def relabel(r):
    """現金增資造成的除權不叫「權」：沒有無償配股的改標「增資」（同時配息的標「息＋增資」）"""
    if r["kind"] == "dividend" and r["reason"].startswith("現金增資") and not val(r["stock"]):
        r["label"] = {"權": "增資", "權息": "息＋增資"}.get(r["label"], r["label"])


def _key(r):
    return (r["date"], r["code"], r["kind"])


def load():
    if not os.path.exists(CA_PATH):
        return {}
    with open(CA_PATH, encoding="utf-8-sig", newline="") as f:
        return {_key(r): {k: r.get(k) or "" for k in FIELDS} for r in csv.DictReader(f)}


def save(rows):
    os.makedirs(os.path.dirname(CA_PATH), exist_ok=True)
    tmp = CA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    os.replace(tmp, CA_PATH)


def update(start=None):
    """抓 start（預設兩週前）到未來 AHEAD 天的事件併進 CSV。回傳訊息；有來源失敗時丟 RuntimeError。"""
    today = dt.date.today()
    backfill = start is not None
    start = start or today - dt.timedelta(days=14)
    end = today + dt.timedelta(days=AHEAD)
    rows = load()
    before = len(rows)
    fresh, errors = {}, []
    for name, fn in SOURCES:
        try:
            for r in fn(start, end):
                if not r["date"] or not r["code"]:
                    continue
                old = fresh.get(_key(r), {})
                fresh[_key(r)] = {k: r[k] if r[k] != "" else old.get(k, "") for k in FIELDS}
        except Exception as e:
            errors.append(f"{name}（{type(e).__name__}）")
    # 還沒發生的預告如果從預告表消失（改期或取消）就拿掉；有來源失敗時不刪，免得誤刪
    if not errors:
        for k in [k for k, r in rows.items() if r["status"] == "forecast" and r["date"] >= today.isoformat() and k not in fresh]:
            del rows[k]
    for k, r in fresh.items():
        old = rows.get(k, {})
        merged = {f: r[f] if r[f] != "" else old.get(f, "") for f in FIELDS}
        merged["status"] = "final" if merged["ref_price"] != "" else "forecast"
        rows[k] = merged
    # 公告日：平常查這個月和上個月公告的；回補時從 start 往前多抓三個月（公告通常比除權息日早幾週）
    first = (start - dt.timedelta(days=95)) if backfill else (today.replace(day=1) - dt.timedelta(days=1))
    months, d = [], first.replace(day=1)
    while d <= today:
        months.append((d.year, d.month))
        d = (d + dt.timedelta(days=32)).replace(day=1)
    errors += add_announcements(rows, months)
    errors += add_rights_detail(rows)
    for r in rows.values():
        relabel(r)
    save(rows)
    upcoming = sum(r["date"] >= today.isoformat() for r in rows.values())
    msg = f"新增 {len(rows) - before} 筆，共 {len(rows)} 筆，今天起 {upcoming} 筆"
    if errors:
        raise RuntimeError(f"{msg}；抓取失敗：{'、'.join(errors)}")
    return msg


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(update(dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None))
