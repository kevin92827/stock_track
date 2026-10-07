"""把各投信查到的指數審核時程 JSON 合併成 data/rebalance.json。

    python merge_rebalance.py <來源資料夾>          從零建立（會覆蓋 data/rebalance.json）
    python merge_rebalance.py <來源資料夾> --add    只新增或取代來源裡有的 ETF，其餘保留

來源資料夾裡每個 .json 都是 [{code, index, provider, frequency, review_months, rule, events, ...}]。
之後要手動修改日期，直接改 data/rebalance.json 再執行 compute_cutoff.py、build_site.py 即可。
"""

import datetime as dt
import glob
import json
import os
import sys

from etfs import ETFS

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "rebalance.json")
PASSIVE = {e[0] for e in ETFS if e[3] == "被動"}


def check_date(s, where):
    if s is None:
        return None
    dt.date.fromisoformat(s)
    return s


def main(src_dir, add=False):
    merged = {}
    if add and os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            merged = {x["code"]: x for x in json.load(f)}
    for path in sorted(glob.glob(os.path.join(src_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8-sig") as f:
                items = json.load(f)
        except ValueError:
            print(f"略過 {os.path.basename(path)}（不是有效的 JSON）")
            continue
        if not isinstance(items, list) or not all(isinstance(x, dict) and "code" in x for x in items):
            print(f"略過 {os.path.basename(path)}（不是審核時程格式）")
            continue
        for it in items:
            code = it["code"]
            if code not in PASSIVE:
                print(f"略過 {code}（不在被動式清單）")
                continue
            events = []
            for ev in it.get("events") or []:
                events.append({
                    "announce": check_date(ev.get("announce"), code),
                    "effective": check_date(ev.get("effective"), code),
                    "verified": bool(ev.get("verified")),
                    "note": ev.get("note") or "",
                    "source": ev.get("source") or "",
                    # 有些指數在非審核月只調整權重上限，不換成分股
                    "weight_only": bool(ev.get("weight_only")) or (ev.get("note") or "").startswith("僅"),
                })
            events.sort(key=lambda e: e["effective"] or e["announce"] or "")
            # 頻率只留「每季／每半年／每年」，括號裡的補充併進規則說明
            freq, _, extra = (it.get("frequency") or "").partition("（")
            rule = it.get("rule", "")
            if extra:
                rule = f"{rule}（{extra}" if rule else extra
            merged[code] = {
                "code": code, "index": it.get("index", ""), "provider": it.get("provider", ""),
                "frequency": freq.strip(), "review_months": it.get("review_months") or [],
                "rule": rule, "events": events,
                "prospectus_url": it.get("prospectus_url"), "prospectus_file": it.get("prospectus_file"),
                "prospectus_date": it.get("prospectus_date"), "sources": it.get("sources") or [],
            }
    order = [e[0] for e in ETFS]
    out = [merged[c] for c in order if c in merged]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    missing = PASSIVE - set(merged)
    print(f"已寫入 {OUT}：{len(out)} 檔，{sum(len(x['events']) for x in out)} 個事件"
          + (f"；缺少 {sorted(missing)}" if missing else ""))
    for x in out:
        evs = " ".join(f"{e['announce']}→{e['effective']}{'' if e['verified'] else '?'}" for e in x["events"])
        print(f"  {x['code']:<7}{x['frequency']:<5} {evs}")


if __name__ == "__main__":
    main(sys.argv[1], add="--add" in sys.argv)
