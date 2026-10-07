"""替 data/rebalance.json 的每次指數審核算出「審核資料截止日」。

    python compute_cutoff.py

截止日不在指數公司公布的日程表裡，這裡是依各指數編製規則（見 rebalance.json 的 rule 欄）計算。
已經過去的交易日用 holdings.csv 的實際資料日；未來的交易日用「平日扣掉下面的休市日」估算，
所以未來的截止日都標成推算（cutoff_est）。證交所公布新年度行事曆後請更新 HOLIDAYS 再重跑。
重新執行 merge_rebalance.py 之後也要再跑一次這支程式。
"""

import csv
import datetime as dt
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
REBAL = os.path.join(BASE, "data", "rebalance.json")
CSV_PATH = os.path.join(BASE, "data", "holdings.csv")

# 資料範圍以外用來估算交易日的休市日（2026 依證交所公告；2027 依行政院辦公日曆表，春節前兩日假設不交易）
HOLIDAYS = {
    "2026-04-03", "2026-04-06", "2026-10-09", "2026-10-26", "2026-12-25",
    "2027-01-01", "2027-02-02", "2027-02-03", "2027-02-04", "2027-02-05", "2027-02-08", "2027-02-09",
    "2027-02-10", "2027-03-01", "2027-04-05", "2027-04-06", "2027-04-30", "2027-06-09", "2027-09-15",
    "2027-09-28", "2027-10-11", "2027-10-25", "2027-12-24", "2027-12-31",
}


def trading_days():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        actual = sorted({r["date"] for r in csv.DictReader(f)})
    days, d = set(actual), dt.date(2026, 3, 1)
    while d <= dt.date(2028, 1, 31):
        s = d.isoformat()
        if not (actual[0] <= s <= actual[-1]) and d.weekday() < 5 and s not in HOLIDAYS:
            days.add(s)
        d += dt.timedelta(days=1)
    return sorted(days), actual[-1]


DAYS, LAST_ACTUAL = trading_days()


def month_days(year, month):
    return [d for d in DAYS if d.startswith(f"{year}-{month:02d}")]


def last_td(year, month):
    return month_days(year, month)[-1], f"{month} 月最後一個交易日"


def nth_td(year, month, n):
    return month_days(year, month)[n - 1], f"{month} 月第 {n} 個交易日"


def prev_month_last(eff):
    y, m = (eff.year, eff.month - 1) if eff.month > 1 else (eff.year - 1, 12)
    return last_td(y, m)


def review_year(eff, december_like):
    """12 月的審核有時到隔年 1 月才生效，資料截止日仍算在前一年"""
    return eff.year - 1 if eff.month == 1 and december_like else eff.year


def ftse(eff, ev):
    d = eff - dt.timedelta(days=28)
    return d.isoformat(), "生效日四週前的星期一"


def tip_last_prev(eff, ev):          # 00713、00940、00881、00935、009816：審核月前一個月最後一個交易日
    return prev_month_last(eff)


def tip_00919(eff, ev):              # 5 月審核：5 月第 10 個交易日；12 月審核：11 月最後一個交易日
    if eff.month in (5, 6):
        return nth_td(eff.year, 5, 10)
    return last_td(review_year(eff, True), 11)


def tip_00929(eff, ev):              # 6、12 月第 7 個交易日
    if eff.month in (6, 7):
        return nth_td(eff.year, 6, 7)
    return nth_td(review_year(eff, True), 12, 7)


def tip_00918(eff, ev):              # 6、12 月第 10 個交易日
    if eff.month in (6, 7):
        return nth_td(eff.year, 6, 10)
    return nth_td(review_year(eff, True), 12, 10)


def twse_00692(eff, ev):             # 7 月第 3 個交易日
    return nth_td(eff.year, 7, 3)


def ice_00891(eff, ev):              # 第三個星期五（公告日）之前第 3 個指數營業日
    i = next(k for k, d in enumerate(DAYS) if d >= ev["announce"])
    return DAYS[i - 3], "第三個星期五之前第 3 個營業日（公開說明書寫法有歧義）"


RULES = {
    "0050": ftse, "006208": ftse, "0052": ftse, "0056": ftse,
    "00713": tip_last_prev, "00940": tip_last_prev, "00881": tip_last_prev, "00935": tip_last_prev,
    "009816": tip_last_prev, "00919": tip_00919, "00927": tip_00919, "00929": tip_00929,
    "00918": tip_00918, "00692": twse_00692, "00891": ice_00891,
    # 00878、00922（MSCI）：手上的編製規則沒有寫單一的資料截止日，不計算
}
ALWAYS_EST = {"00891"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with open(REBAL, encoding="utf-8") as f:
        data = json.load(f)
    for item in data:
        rule = RULES.get(item["code"])
        out = []
        for ev in item["events"]:
            for k in ("cutoff", "cutoff_est", "cutoff_note"):
                ev.pop(k, None)
            if not rule or ev.get("weight_only") or not ev.get("effective"):
                continue
            try:
                cutoff, how = rule(dt.date.fromisoformat(ev["effective"]), ev)
            except (IndexError, StopIteration, TypeError):
                continue
            ev["cutoff"] = cutoff
            # 推算：未來的日子（交易日曆是估的）、規則有歧義的，或截止日是由「推算的生效日」回推的（FTSE）
            ev["cutoff_est"] = (cutoff > LAST_ACTUAL or item["code"] in ALWAYS_EST
                                or (rule is ftse and not ev.get("verified")))
            ev["cutoff_note"] = f"依編製規則計算：{how}"
            out.append(f"{cutoff}{'?' if ev['cutoff_est'] else ''}→{ev['effective']}")
        print(f"{item['code']:<7} {' '.join(out) or '（無）'}")
    with open(REBAL, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
