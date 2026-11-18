"""산업부 '20대 주력 수출품목'의 품목 × 국가 × 월별 수출입을 모아 둔다 (앱 '수출입 분석'에서 20대 품목을 호출 없이 바로 보여주기 위한 자료).

- 관세청 품목별 국가별 API는 국가만 지정하면 그 나라의 HS 10자리 전체 품목 행을 한 번에 준다(12개월 단위).
  그 행을 무역협회 HSK-MTI 연계표(mti_map.json, 기준년도별)로 20대 품목에 묶어 월별로 더한다.
- 한 나라씩 받아 나라마다 '어느 달까지 받았는지(done_to)'를 기록하므로, 시간이 모자라 중간에 끝나도 다음 실행이 이어서 한다.
  모든 나라가 최신 달까지 끝나면 complete=true (앱은 complete일 때만 쓴다). 최근 3개월은 수정될 수 있어 매번 다시 받는다.
- 결과: groups.json  {"updated","months":[...],"unit":"천달러","groups":[...],"defs":정의 지문,"done_to":{국가:달},"complete":bool,
                      "data":{품목:{국가코드:[[수출...],[수입...]]}}}  (값은 천달러로 반올림해 용량을 줄임, 0만 있는 국가는 뺌)
- 국가 목록·월 목록은 trade.json(trade_data.py가 만든 것)에서 읽는다.
- 결과는 워크플로(.github/workflows/trade_groups.yml)가 'groups' 브랜치에 한 커밋으로 올린다.
- HS 6자리 자료(--hs-out을 줄 때만): 같은 호출로 받은 HS 10자리 행을 HS 6자리로도 더해 'hs' 브랜치에 HS 2자리 장별 파일로 둔다(수출 계기판 AI 검색이 읽음).
  세계 합계는 10년 전체, 나라별은 교역 상위 N개국(기본 20)만. 최근 3개월은 수정될 수 있어 나라별 값을 recent.json에 들고 있다가 매번 다시 더하고,
  그보다 오래된 달은 나라를 처음 받을 때 한 번만 세계 합계에 더해 둔다(전체 나라 나라별 10년치를 들고 있지 않으려고). 나라별 진행은 meta.json의 done_to.
  HS 쪽에서 오류가 나도 groups.json 저장에는 영향이 없다.
  hs/ 폴더: meta.json {"updated","months","unit":"천달러","countries":[상위 N개국],"done_to":{국가:달},"window_from","complete","chapters"},
            recent.json {국가:{HS6:{달:[수출,수입]}}}, ch/<HS2>.json {"months","codes":{HS6:{"W":[[수출..],[수입..]], 국가코드:[[..],[..]]}}}
필요한 환경변수: CUSTOMS_API_KEY
사용: python trade_groups.py --trade trade.json [--prev prev_groups.json] [--out groups.json] [--max-minutes 150] [--limit-countries N]
      [--hs-prev 이전hs폴더 --hs-out 새hs폴더 --hs-countries 20]
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


def fetch_country(key, mti, lookups, cnty, start, end, get=None, active=None, hs_acc=None):
    """한 나라의 [start, end](YYYYMM) 20대 품목별 월 수출입 -> {품목 번호: {'YYYY-MM': [수출, 수입]}} (달러). 12개월 단위로 받는다.
    active: 그 나라가 거래한 달('YYYY-MM') 집합(trade.json 기준) — 구간 안에 거래한 달이 없으면 그 구간은 받지 않는다
    hs_acc: 주면 HS 6자리별 {HS6: {'YYYY-MM': [수출, 수입]}}(달러)도 여기에 더한다(같은 호출로)"""
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
            hs10 = r.get("hsCd", "")
            ex, im = td.num(r.get("expDlr")), td.num(r.get("impDlr"))
            if hs_acc is not None and len(hs10) >= 6 and hs10[:6].isdigit() and (ex or im):
                h = hs_acc.setdefault(hs10[:6], {}).setdefault(ym, [0, 0])
                h[0] += ex
                h[1] += im
            hit = lookups[table_index(mti, ym)].get(hs10)
            if not hit:
                continue
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


# ---- HS 6자리 저장소 ----
HS_DEFAULT_COUNTRIES = 20


class HsStore:
    """hs/ 폴더 자료를 읽고 고치고 쓴다. 값은 천달러 정수.
    world[HS6][달] = [수출, 수입]: window_from 이전 달(확정된 세계 합계). window_from 이후 달은 recent(나라별)의 합으로 그때그때 만든다.
    det[HS6][국가][달]: 상위 국가의 나라별 값(전 기간)."""

    def __init__(self, prev_dir, months, trade, n_countries):
        self.months, self.latest = months, months[-1]
        meta = load_json(os.path.join(prev_dir, "meta.json")) if prev_dir else {}
        self.done_to = dict(meta.get("done_to", {}))
        rank = sorted(trade["country"], key=lambda c: -(sum(x or 0 for x in trade["country"][c][1]) + sum(x or 0 for x in trade["country"][c][2])))
        self.countries = meta.get("countries") or rank[:n_countries]
        self.recent = {c: {h: dict(v) for h, v in per.items()} for c, per in load_json(os.path.join(prev_dir, "recent.json")).items()} if prev_dir else {}
        prev_window = meta.get("window_from")
        refresh_from = tc._dash_add(self.latest, -(REFRESH_MONTHS - 1))
        # 지난번에 모든 나라가 끝났을 때만 창을 앞으로 민다(덜 끝났으면 창을 그대로 둬서, 덜 받은 나라의 달을 나중에 채울 수 있게)
        self.window_from = refresh_from if (meta.get("complete") or not prev_window) else min(prev_window, refresh_from)
        self.world, self.det = {}, {}
        for ch in meta.get("chapters", []) if prev_dir else []:
            f = load_json(os.path.join(prev_dir, "ch", f"{ch}.json"))
            fm = f.get("months", [])
            for h, per in f.get("codes", {}).items():
                for c, (e, i) in per.items():
                    cells = {m: [e[k], i[k]] for k, m in enumerate(fm) if (e[k] or i[k]) and m >= months[0]}
                    if c == "W":  # 창 안의 달은 recent로 다시 만든다
                        cells = {m: v for m, v in cells.items() if not prev_window or m < prev_window}
                        if cells:
                            self.world[h] = cells
                    elif cells and c in self.countries:
                        self.det.setdefault(h, {})[c] = cells
        # 창 밖으로 나간 달은 recent의 합을 세계 합계로 굳히고 recent에서 뺀다
        for c, per in self.recent.items():
            for h, cells in per.items():
                for m in [m for m in cells if m < self.window_from]:
                    if m >= months[0]:
                        w = self.world.setdefault(h, {}).setdefault(m, [0, 0])
                        w[0] += cells[m][0]
                        w[1] += cells[m][1]
                    del cells[m]

    def start_for(self, cnty, active):
        """이 나라를 HS 쪽에서 어디부터 받아야 하나 ('YYYY-MM') 또는 None(받을 것 없음)"""
        dt = self.done_to.get(cnty)
        if dt is None:
            return self.months[0] if active else None
        return self.window_from if dt == self.latest else max(self.window_from, min(self.window_from, tc._dash_add(dt, 1)))

    def apply(self, cnty, acc, start):
        """한 나라의 받은 값(acc: {HS6: {달: [수출, 수입]}} 달러)을 넣는다. start 이후 달은 이 나라 값을 새로 바꾼다"""
        first = self.done_to.get(cnty) is None  # 처음 받는 나라만 창 밖(오래된) 달을 세계 합계에 더한다
        rec = self.recent.setdefault(cnty, {})
        for h in list(rec):
            for m in [m for m in rec[h] if m >= start]:
                del rec[h][m]
        if cnty in self.countries:
            for per in self.det.values():
                if cnty in per:
                    for m in [m for m in per[cnty] if m >= start]:
                        del per[cnty][m]
        for h, cells in acc.items():
            for m, (ex, im) in cells.items():
                if m < start or m < self.months[0]:
                    continue
                v = [round(ex / UNIT), round(im / UNIT)]
                if not (v[0] or v[1]):
                    continue
                if m >= self.window_from:
                    rec.setdefault(h, {})[m] = v
                elif first:
                    w = self.world.setdefault(h, {}).setdefault(m, [0, 0])
                    w[0] += v[0]
                    w[1] += v[1]
                if cnty in self.countries:
                    self.det.setdefault(h, {}).setdefault(cnty, {})[m] = v
        self.recent[cnty] = {h: c for h, c in rec.items() if c}
        self.done_to[cnty] = self.latest

    def save(self, out_dir, all_countries):
        months = self.months
        world = {h: dict(c) for h, c in self.world.items()}
        for per in self.recent.values():
            for h, cells in per.items():
                for m, (ex, im) in cells.items():
                    w = world.setdefault(h, {}).setdefault(m, [0, 0])
                    w[0] += ex
                    w[1] += im
        chapters = {}
        for h, cells in world.items():
            if not any(e or i for e, i in cells.values()):
                continue
            row = {"W": [[cells.get(m, [0, 0])[0] for m in months], [cells.get(m, [0, 0])[1] for m in months]]}
            for c, dc in sorted((self.det.get(h) or {}).items()):
                if dc:
                    row[c] = [[dc.get(m, [0, 0])[0] for m in months], [dc.get(m, [0, 0])[1] for m in months]]
            chapters.setdefault(h[:2], {})[h] = row
        os.makedirs(os.path.join(out_dir, "ch"), exist_ok=True)
        for ch, codes in chapters.items():
            with open(os.path.join(out_dir, "ch", f"{ch}.json"), "w", encoding="utf-8") as f:
                json.dump({"months": months, "unit": "천달러", "codes": dict(sorted(codes.items()))}, f, ensure_ascii=False, separators=(",", ":"))
        complete = all(self.done_to.get(c) == self.latest for c in all_countries)
        meta = {"updated": datetime.now().strftime("%Y-%m-%dT%H:%M"), "months": months, "unit": "천달러", "countries": self.countries,
                "done_to": self.done_to, "window_from": self.window_from, "complete": complete, "chapters": sorted(chapters)}
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(out_dir, "recent.json"), "w", encoding="utf-8") as f:
            json.dump(self.recent, f, ensure_ascii=False, separators=(",", ":"))
        n_done = sum(1 for c in all_countries if self.done_to.get(c) == self.latest)
        size = sum(os.path.getsize(os.path.join(out_dir, "ch", f"{ch}.json")) for ch in chapters) / 1e6
        return f"HS 6자리: 완료 {n_done}/{len(all_countries)}곳, complete={complete}, 품목 {sum(len(v) for v in chapters.values())}개, 장 파일 {len(chapters)}개 {size:.1f}MB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade", default="trade.json")
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", default="groups.json")
    ap.add_argument("--mti", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "mti_map.json"))
    ap.add_argument("--max-minutes", type=float, default=150)
    ap.add_argument("--limit-countries", type=int, default=0)
    ap.add_argument("--hs-prev", default="", help="이전 hs 폴더(meta.json·recent.json·ch/)")
    ap.add_argument("--hs-out", default="", help="HS 6자리 자료를 쓸 폴더 (안 주면 HS 자료는 만들지 않음)")
    ap.add_argument("--hs-countries", type=int, default=HS_DEFAULT_COUNTRIES)
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
    hs = None
    if args.hs_out:
        try:
            hs = HsStore(args.hs_prev if args.hs_prev and os.path.isdir(args.hs_prev) else "", months, trade, args.hs_countries)
        except Exception as e:  # HS 쪽 문제로 20대 품목 수집이 멈추지 않게
            print(f"HS 6자리 자료를 읽지 못해 이번엔 건너뛰어요: {str(e)[:120]}")
            hs = None
    hs_start = {}
    todo = []
    for cnty in countries:
        dt = done_to.get(cnty)
        hst = hs.start_for(cnty, active[cnty]) if hs else None
        if hs and hst is None:
            hs.done_to[cnty] = latest  # 거래가 없는 나라
        if dt is None:
            if not active[cnty]:  # 10년 내내 거래가 없는 나라는 받을 것이 없다
                done_to[cnty] = latest
                continue
            st = months[0]
        else:  # 최근 3개월은 수정될 수 있어 항상 다시, 새 달이 생겼으면 거기부터
            st = refresh_from if dt == latest else min(refresh_from, tc._dash_add(dt, 1))
        if hst is not None:
            hs_start[cnty] = hst
            st = min(st, hst)  # HS 자료를 처음 받는 나라는 10년치를 받는다(20대 품목도 같은 값으로 다시 채워짐)
        todo.append((cnty, st))
    if args.limit_countries:
        todo = todo[:args.limit_countries]
    print(f"받을 나라 {len(todo)}곳 (전체 {len(countries)}곳), 월 {months[0]}~{latest}, 품목 {len(names)}개")
    errors, done_now, calls_est = [], 0, 0

    def work(job):
        cnty, start = job
        start = max(start, months[0])
        hacc = {} if hs and cnty in hs_start else None
        acc = fetch_country(key, mti, lookups, cnty, start.replace("-", ""), latest.replace("-", ""), active=active[cnty], hs_acc=hacc)
        return cnty, (acc, hacc), start

    for i in range(0, len(todo), BATCH):
        if time.time() - started > args.max_minutes * 60:
            print(f"시간 한도({args.max_minutes:g}분)에 도달해 여기서 저장하고 끝내요. 이어서는 다음 실행에서.")
            break
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = [(job, pool.submit(work, job)) for job in todo[i:i + BATCH]]
            for job, fut in futs:
                try:
                    cnty, (acc, hacc), start = fut.result()
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
                if hs and hacc is not None:
                    try:
                        hs.apply(cnty, hacc, max(hs_start[cnty], months[0]))
                    except Exception as e:
                        errors.append(f"{cnty} HS: {str(e)[:80]}")
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
    if hs:
        try:
            print(hs.save(args.hs_out, countries))
        except Exception as e:  # groups.json은 이미 저장됨
            print(f"HS 6자리 자료 저장 실패: {str(e)[:160]}")
    for e in errors[:10]:
        print("  오류:", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
