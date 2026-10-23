"""KRX 일별 시세를 추적 종목만 골라 매일 쌓는다 (앱 '주가' 탭의 라이벌 비교·이벤트·그룹 시총용).

- GitHub Actions(.github/workflows/price_history.yml)가 평일 아침에 실행한다.
- 추적 종목: 섹터 기업(app.py의 INDUSTRY_GROUPS) + 재계 순위 상위 그룹의 상장 계열사(chaebol_2026.json).
- 이전 결과(--prev)에 새 거래일을 이어 붙이고, 오래된 날은 뺀다. 결과 prices.json은 'prices' 브랜치에 올라간다.
- 처음에는 --since 날짜부터 한 번에 --max-days일씩 채운다 (여러 번 실행하면 따라잡는다).
- KRX가 하루 호출 한도(429)를 알리면 그때까지 받은 것만 저장하고 끝낸다.
필요한 환경변수: KRX_API_KEY
사용: python price_history.py [--prev prices.json] [--out prices.json] [--since 20250101] [--max-days 60] [--keep 260]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import news_digest as nd

KST = timezone(timedelta(hours=9))
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"
KRX_MARKETS = {"코스피": "sto/stk_bydd_trd", "코스닥": "sto/ksq_bydd_trd"}  # app.py의 KRX_MARKETS와 같게
KRX_PREF = re.compile(r"^(?P<base>.+?)(?P<s>\d?우[A-C]?(?:\(전환\))?)$")  # app.py와 같은 우선주 이름 규칙
MAX_GROUP_RANK = 50  # 재계 순위 이 위까지의 그룹만 따라간다
TIMEOUT = (10, 60)
HERE = os.path.dirname(os.path.abspath(__file__))


class RateLimited(Exception):
    """KRX 하루 호출 한도"""


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def code6(row):
    for key in ("ISU_SRT_CD", "ISU_CD"):
        s = re.sub(r"\s+", "", str(row.get(key) or ""))
        if len(s) == 12 and s[:2].isalpha():
            return s[3:9]
        if len(s) == 6:
            return s
    return None


def request_market(session, key, path, ymd):
    """KRX 한 시장 하루치 -> 줄 목록. 이용 승인이 없으면 None (그 시장은 건너뜀)."""
    last = None
    for attempt in range(3):
        try:
            res = session.get(f"{KRX_BASE}/{path}", params={"basDd": ymd}, headers={"AUTH_KEY": key}, timeout=TIMEOUT)
            break
        except Exception as e:  # 일시적인 네트워크 오류는 두 번까지 다시 시도
            last, res = e, None
            time.sleep(1 + attempt)
    if res is None:
        raise RuntimeError(f"KRX 서버에 연결하지 못했어요: {type(last).__name__}")
    if res.status_code == 429:
        raise RateLimited()
    if res.status_code in (401, 403):
        return None
    if res.status_code != 200:
        raise RuntimeError(f"KRX 응답 오류 {res.status_code} ({path})")
    data = res.json()
    rows = data.get("OutBlock_1") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def fetch_day(session, key, ymd):
    """하루치 전 종목 -> {종목코드: {n 이름, m 시장, c 종가, r 등락률, v 거래대금(원), k 시총(원)}}. 휴장·아직 안 올라온 날은 빈 {}."""
    out, approved = {}, 0
    for market, path in KRX_MARKETS.items():
        rows = request_market(session, key, path, ymd)
        if rows is None:
            print(f"  {market}: 이용 승인이 안 돼 건너뜀", file=sys.stderr)
            continue
        approved += 1
        for r in rows:
            code = code6(r)
            if not code or not isinstance(r, dict):
                continue
            out[code] = {"n": str(r.get("ISU_NM") or r.get("ISU_ABBRV") or "").strip(), "m": market, "c": num(r.get("TDD_CLSPRC")),
                         "r": num(r.get("FLUC_RT")), "v": num(r.get("ACC_TRDVAL")), "k": num(r.get("MKTCAP"))}
    if not approved:
        raise RuntimeError("KRX 인증키가 올바르지 않거나 시세 API 이용 승인이 안 됐어요.")
    return out


def caps_with_pref(rows):
    """{보통주 종목코드: 시총(보통주+우선주, 원)} - 앱의 krx_caps와 같은 규칙"""
    by_name = {}
    for code, r in rows.items():
        if r["n"] and not KRX_PREF.match(r["n"]):
            by_name.setdefault(r["n"], code)
    caps = {c: r["k"] for c, r in rows.items() if r["k"] is not None and not KRX_PREF.match(r["n"])}
    for code, r in rows.items():
        m = KRX_PREF.match(r["n"] or "")
        if m and r["k"] is not None and m.group("base") in by_name:
            base = by_name[m.group("base")]
            caps[base] = caps.get(base, 0) + r["k"]
    return caps


def load_chaebol():
    try:
        with open(os.path.join(HERE, "chaebol_2026.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"groups": []}


def pick_universe(rows):
    """그날 시세의 이름으로 추적 종목을 찾는다 -> (섹터 기업 코드 집합, {그룹: {rank, codes}})"""
    nk = nd.A["name_key"]
    by_key = {}
    for code, r in rows.items():
        if r["n"] and not KRX_PREF.match(r["n"]):
            by_key.setdefault(nk(r["n"]), code)
    sector_codes = set()
    for names in nd.A["INDUSTRY_GROUPS"].values():
        for n in names:
            code = by_key.get(nk(n))
            if code:
                sector_codes.add(code)
    groups = {}
    for g in load_chaebol().get("groups", []):
        rank = int(g.get("rank") or 0)
        if not rank or rank > MAX_GROUP_RANK:
            continue
        codes = []
        for m in g.get("members") or []:
            code = by_key.get(nk(m["name"]))
            if code and code not in codes:
                codes.append(code)
        if codes:
            groups[g["name"]] = {"rank": rank, "codes": codes}
    return sector_codes, groups


def weekdays(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def build(prev, session, key, since, max_days, keep, today):
    data = prev if isinstance(prev, dict) and prev.get("dates") and prev.get("s") is not None else {"dates": [], "s": {}, "g": {}}
    dates, series = list(data["dates"]), data["s"]
    if dates:
        start = datetime.strptime(dates[-1], "%Y%m%d").date() + timedelta(days=1)
    else:
        start = datetime.strptime(since, "%Y%m%d").date()
    tracked = set(series)
    groups = data.get("g") or {}
    tried, added, limited = 0, 0, False
    for day in weekdays(start, today):
        if tried >= max_days:
            break
        tried += 1
        ymd = day.strftime("%Y%m%d")
        try:
            rows = fetch_day(session, key, ymd)
        except RateLimited:
            print("KRX 하루 호출 한도에 걸려 여기까지만 저장해요.", file=sys.stderr)
            limited = True
            break
        if not rows:  # 휴장일이거나 아직 안 올라온 날
            continue
        sector_codes, new_groups = pick_universe(rows)
        group_codes = {c for g in new_groups.values() for c in g["codes"]}
        for code in sector_codes | group_codes:
            if code not in tracked:  # 새로 따라가는 종목: 앞쪽 날짜는 빈칸
                series[code] = {"n": rows[code]["n"], "m": rows[code]["m"], "c": [None] * len(dates), "r": [None] * len(dates),
                                "v": [None] * len(dates), "k": [None] * len(dates)}
                tracked.add(code)
        if new_groups:
            groups = new_groups
        caps = caps_with_pref(rows)
        dates.append(ymd)
        for code, s in series.items():
            r = rows.get(code)
            if r is None:
                for f in "crvk":
                    s[f].append(None)
                continue
            s["n"], s["m"] = r["n"] or s["n"], r["m"]
            s["c"].append(None if r["c"] is None else int(r["c"]))
            s["r"].append(None if r["r"] is None else round(r["r"], 2))
            s["v"].append(None if r["v"] is None else int(round(r["v"] / 1e8)))
            cap = caps.get(code, r["k"])
            s["k"].append(None if cap is None else int(round(cap / 1e8)))
        added += 1
        time.sleep(0.2)
    if len(dates) > keep:
        cut = len(dates) - keep
        dates = dates[cut:]
        for s in series.values():
            for f in "crvk":
                s[f] = s[f][cut:]
    for code in [c for c, s in series.items() if all(v is None for v in s["c"])]:  # 보관 구간에 값이 하나도 없는 종목은 뺀다
        del series[code]
    groups = {g: {"rank": v["rank"], "codes": [c for c in v["codes"] if c in series]} for g, v in groups.items()}
    groups = {g: v for g, v in groups.items() if v["codes"]}
    out = {"updated": datetime.now(KST).strftime("%Y-%m-%dT%H:%M"), "dates": dates, "s": series, "g": groups}
    return out, added, tried, limited


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", default="prices.json")
    ap.add_argument("--since", default="20250101")
    ap.add_argument("--max-days", type=int, default=60)
    ap.add_argument("--keep", type=int, default=260)
    args = ap.parse_args()
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        raise SystemExit("KRX_API_KEY가 없어요. GitHub Secrets에 KRX_API_KEY를 등록하세요.")
    prev = {}
    if args.prev and os.path.exists(args.prev):
        try:
            with open(args.prev, encoding="utf-8") as f:
                prev = json.load(f)
        except ValueError:
            prev = {}
    today = datetime.now(KST).date()
    out, added, tried, limited = build(prev, requests.Session(), key, args.since, args.max_days, args.keep, today)
    if not out["dates"]:
        raise SystemExit("받은 시세가 하나도 없어서 파일을 만들지 않았어요 (인증키·이용 승인·날짜를 확인하세요).")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(args.out) / 1e6
    print(f"{tried}일 확인, {added}일 추가 → 보관 {len(out['dates'])}일 ({out['dates'][0]}~{out['dates'][-1]}), 종목 {len(out['s'])}개, 그룹 {len(out['g'])}개, {size:.1f}MB"
          + (" (호출 한도로 중단)" if limited else ""))


if __name__ == "__main__":
    sys.exit(main())
