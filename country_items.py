"""한 나라와의 품목별(HS 10자리) 월별 수출입을 한꺼번에 받는다 (기획 취재용 즉석 조회).

- 관세청 '품목별 국가별 수출입실적' API에 나라만 지정하면 HS 10자리 전체가 월별로 온다(12개월씩).
- 결과는 워크플로(.github/workflows/country_items.yml)가 비공개 저장소 paper-private의 query/ 폴더에 올린다.
  (취재 주제가 공개 저장소에 드러나지 않게 결과는 공개 저장소에 두지 않는다)
- 로그에는 건수만 남긴다.
필요한 환경변수: CUSTOMS_API_KEY
사용: python country_items.py --cnty CN --start 202301 --end 202610 --out out.json
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import trade_calc as tc
import trade_data as td

KST = timezone(timedelta(hours=9))


def collect(key, cnty, start, end, get=None):
    """[start, end] 월별 HS 10자리 행 -> {hs: {n: 이름, exp/imp/ew/iw: {'YYYY-MM': 값}}} (달러·kg, 0은 뺌)"""
    get = get or td.get_items
    items = {}
    for s, e in tc.chunk_months(start, end):
        for r in get(key, td.ITEM_API, strtYymm=s, endYymm=e, cntyCd=cnty):
            ym = r.get("year", "")
            if len(ym) != 7 or ym[4] != ".":  # '총계' 줄
                continue
            ym = ym.replace(".", "-")
            hs = r.get("hsCd", "")
            if not hs:
                continue
            it = items.setdefault(hs, {"n": r.get("statKor", "")})
            for out, src in (("exp", "expDlr"), ("imp", "impDlr"), ("ew", "expWgt"), ("iw", "impWgt")):
                v = td.num(r.get(src))
                if v:
                    d = it.setdefault(out, {})
                    d[ym] = d.get(ym, 0) + v
    return items


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cnty", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    cnty, start, end = a.cnty.strip().upper(), a.start.strip(), a.end.strip()
    if len(cnty) != 2 or not (start.isdigit() and end.isdigit() and len(start) == len(end) == 6 and start <= end):
        raise SystemExit("입력 확인: 나라 2글자(CN), 시작·끝 YYYYMM")
    items = collect(td.api_key(), cnty, start, end)
    data = {"cnty": cnty, "start": start, "end": end, "made": datetime.now(KST).strftime("%Y-%m-%dT%H:%M"), "items": items}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    months = sorted({m for it in items.values() for k in ("exp", "imp") for m in it.get(k, {})})
    print(f"품목 {len(items)}개, 월 {months[0] if months else '-'}~{months[-1] if months else '-'}")
    if not items:
        sys.exit(1)


if __name__ == "__main__":
    main()
