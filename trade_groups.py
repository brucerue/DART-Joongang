"""산업부 '20대 주력 수출품목'의 품목 × 국가 × 월별 수출입을 모아 둔다 (앱 '수출입 분석'에서 20대 품목을 호출 없이 바로 보여주기 위한 자료).

- 관세청 품목별 국가별 API는 국가만 지정하면 그 나라의 HS 10자리 전체 품목 행을 한 번에 준다(12개월 단위).
  그 행을 무역협회 HSK-MTI 연계표(mti_map.json, 기준년도별)로 20대 품목에 묶어 월별로 더한다.
- 한 나라씩 받아 나라마다 '어느 달까지 받았는지(done_to)'를 기록하므로, 시간이 모자라 중간에 끝나도 다음 실행이 이어서 한다.
  모든 나라가 최신 달까지 끝나면 complete=true (앱은 complete일 때만 쓴다). 최근 3개월은 수정될 수 있어 매번 다시 받는다.
- 결과: groups.json  {"updated","months":[...],"unit":"천달러","groups":[...],"defs":정의 지문,"done_to":{국가:달},"complete":bool,
                      "data":{품목:{국가코드:[[수출...],[수입...]]}}}  (값은 천달러로 반올림해 용량을 줄임, 0만 있는 국가는 뺌)
- 국가 목록·월 목록은 trade.json(trade_data.py가 만든 것)에서 읽는다.
- 결과는 워크플로(.github/workflows/trade_groups.yml)가 'groups' 브랜치에 한 커밋으로 올린다.
필요한 환경변수: CUSTOMS_API_KEY
사용: python trade_groups.py --trade trade.json [--prev prev_groups.json] [--out groups.json] [--max-minutes 150] [--limit-countries N]
"""
import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import trade_calc as tc
import trade_data as td

REFRESH_MONTHS = 3  # 최근 이 개월 수는 매번 다시 받는다
WORKERS = 4
BATCH = 8  # 한 번에 병렬로 받는 나라 수 (사이사이에 시간 한도를 확인)
UNIT = 1000  # 저장 단위(달러 -> 천달러)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def group_lookup(mti):
    """연계표(기준년도별)마다 {HS10: [20대 품목 번호들]} -> 표 번호 순서의 목록"""
    names = list(tc.MTI_GROUPS)
    out = []
    for t in mti["tables"]:
        look = {}
        for gi, g in enumerate(names):
            include, exclude = tc.MTI_GROUPS[g]
            for hs in tc.mti_hsk(t, include, exclude):
                look.setdefault(hs, []).append(gi)
        out.append(look)
    return names, out


def defs_signature():
    """품목 정의(MTI_GROUPS)의 지문 — 정의가 바뀌면 이미 받은 자료가 낡은 것이라 처음부터 다시 받는다"""
    return hashlib.sha1(json.dumps(tc.MTI_GROUPS, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def table_index(mti, ym):
    t = tc.mti_table_for_year(mti, int(ym[:4]))
    return mti["tables"].index(t)


def fetch_country(key, mti, lookups, cnty, start, end, get=None, active=None):
    """한 나라의 [start, end](YYYYMM) 20대 품목별 월 수출입 -> {품목 번호: {'YYYY-MM': [수출, 수입]}} (달러). 12개월 단위로 받는다.
    active: 그 나라가 거래한 달('YYYY-MM') 집합(trade.json 기준) — 구간 안에 거래한 달이 없으면 그 구간은 받지 않는다"""
    get = get or td.get_items
    acc = {}
    for s, e in tc.chunk_months(start, end):
        if active is not None and not any(f"{m[:4]}-{m[4:]}" in active for m in _months_between(s, e)):
            continue
        for r in get(key, td.ITEM_API, strtYymm=s, endYymm=e, cntyCd=cnty):
            ym = r.get("year", "")
            if len(ym) != 7 or ym[4] != ".":  # '총계' 줄
                continue
            ym = ym.replace(".", "-")
            hit = lookups[table_index(mti, ym)].get(r.get("hsCd", ""))
            if not hit:
                continue
            ex, im = td.num(r.get("expDlr")), td.num(r.get("impDlr"))
            for gi in hit:
                a = acc.setdefault(gi, {}).setdefault(ym, [0, 0])
                a[0] += ex
                a[1] += im
    return acc


def _months_between(s, e):
    out, cur = [], s
    while cur <= e:
        out.append(cur)
        cur = tc.month_add(cur, 1)
    return out


def to_arrays(cells, months):
    """{'YYYY-MM': [수출, 수입]}(천달러) -> ([수출...], [수입...]) (months 순서, 없는 달은 0)"""
    return [cells.get(m, [0, 0])[0] for m in months], [cells.get(m, [0, 0])[1] for m in months]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade", default="trade.json")
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", default="groups.json")
    ap.add_argument("--mti", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "mti_map.json"))
    ap.add_argument("--max-minutes", type=float, default=150)
    ap.add_argument("--limit-countries", type=int, default=0)
    args = ap.parse_args()
    started = time.time()
    key = td.api_key()
    trade = load_json(args.trade)
    if not trade.get("months") or not trade.get("country"):
        raise SystemExit("trade.json에서 월·국가 목록을 못 읽었어요.")
    mti = tc.load_mti(args.mti)
    if mti is None:
        raise SystemExit("mti_map.json을 못 읽었어요.")
    names, lookups = group_lookup(mti)
    months = trade["months"]
    latest = months[-1]
    # 큰 나라부터(10년 수출입 합계가 큰 순)
    countries = sorted(trade["country"], key=lambda c: -(sum(x or 0 for x in trade["country"][c][1]) + sum(x or 0 for x in trade["country"][c][2])))

    prev = load_json(args.prev) if args.prev else {}
    prev_months = prev.get("months", [])
    cells = {gi: {} for gi in range(len(names))}  # 품목 번호 -> 국가 -> {월: [수출, 수입](천달러)}
    done_to = dict(prev.get("done_to", {}))
    sig = defs_signature()
    if prev.get("groups") == names and prev.get("defs") == sig:
        for gi, g in enumerate(names):
            for cnty, (e, i) in (prev.get("data", {}).get(g, {})).items():
                cells[gi][cnty] = {m: [e[k], i[k]] for k, m in enumerate(prev_months) if e[k] or i[k]}
    else:
        done_to = {}  # 품목 정의가 바뀌었으면 처음부터
    refresh_from = tc._dash_add(latest, -(REFRESH_MONTHS - 1))

    active = {c: {m for k, m in enumerate(months) if (trade["country"][c][1][k] or 0) or (trade["country"][c][2][k] or 0)} for c in countries}
    todo = []
    for cnty in countries:
        dt = done_to.get(cnty)
        if dt is None:
            if not active[cnty]:  # 10년 내내 거래가 없는 나라는 받을 것이 없다
                done_to[cnty] = latest
                continue
            todo.append((cnty, months[0]))
        else:  # 최근 3개월은 수정될 수 있어 항상 다시, 새 달이 생겼으면 거기부터
            todo.append((cnty, refresh_from if dt == latest else min(refresh_from, tc._dash_add(dt, 1))))
    if args.limit_countries:
        todo = todo[:args.limit_countries]
    print(f"받을 나라 {len(todo)}곳 (전체 {len(countries)}곳), 월 {months[0]}~{latest}, 품목 {len(names)}개")
    errors, done_now, calls_est = [], 0, 0

    def work(job):
        cnty, start = job
        start = max(start, months[0])
        return cnty, fetch_country(key, mti, lookups, cnty, start.replace("-", ""), latest.replace("-", ""), active=active[cnty]), start

    for i in range(0, len(todo), BATCH):
        if time.time() - started > args.max_minutes * 60:
            print(f"시간 한도({args.max_minutes:g}분)에 도달해 여기서 저장하고 끝내요. 이어서는 다음 실행에서.")
            break
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = [(job, pool.submit(work, job)) for job in todo[i:i + BATCH]]
            for job, fut in futs:
                try:
                    cnty, acc, start = fut.result()
                except Exception as e:  # 한 나라가 실패해도 나머지는 계속
                    errors.append(f"{job[0]}: {str(e)[:80]}")
                    continue
                s_key = start
                for gi in range(len(names)):
                    row = cells[gi].setdefault(cnty, {})
                    for m in [m for m in months if m >= s_key]:  # 받은 구간은 새 값으로 덮어쓴다(없던 달은 0)
                        row.pop(m, None)
                    for m, (ex, im) in (acc.get(gi) or {}).items():
                        if ex or im:
                            row[m] = [round(ex / UNIT), round(im / UNIT)]
                done_to[cnty] = latest
                done_now += 1
        print(f"  진행 {min(i + BATCH, len(todo))}/{len(todo)} (오류 {len(errors)}곳, {(time.time() - started) / 60:.1f}분)")

    complete = all(done_to.get(c) == latest for c in countries)
    data = {}
    for gi, g in enumerate(names):
        per = {}
        for cnty, row in cells[gi].items():
            if row:
                e, im = to_arrays(row, months)
                per[cnty] = [e, im]
        data[g] = per
    out = {"updated": datetime.now().strftime("%Y-%m-%dT%H:%M"), "months": months, "unit": "천달러", "groups": names, "defs": sig, "done_to": done_to, "complete": complete, "data": data}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    n_done = sum(1 for c in countries if done_to.get(c) == latest)
    print(f"저장: 완료 {n_done}/{len(countries)}곳, complete={complete}, 이번에 받은 나라 {done_now}곳, 오류 {len(errors)}곳, 파일 {os.path.getsize(args.out) / 1e6:.1f}MB")
    for e in errors[:10]:
        print("  오류:", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
