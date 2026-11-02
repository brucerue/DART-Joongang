"""현대차·기아 월별 판매실적(DART '영업(잠정)실적(공정공시)'의 판매대수 표)을 모아 'sales' 브랜치(sales.json)에 저장한다.

- GitHub Actions(.github/workflows/sales_data.yml)가 매월 1~3일에 실행한다(처음엔 수동 실행으로 과거 10년을 채움).
- 회사마다 DART 공시 목록(거래소공시)에서 '영업(잠정)실적' 보고서를 찾아 원문을 받아 판매대수 표를 읽는다(sales_calc.parse_sales).
  분기 잠정실적(매출·영업이익)처럼 판매대수 표가 없는 공시는 건너뛰고, 한 번 본 접수번호는 다시 받지 않는다.
- 같은 달에 정정 공시가 있으면 나중 것(접수번호가 큰 것)을 쓴다.
- 결과: {"updated", "corps": {회사: {"months": {"YYYY-MM": {"국내","해외",["특수"],"합계","rcept","released"}}}}, "seen": {접수번호: "ok"|"skip"|"fail"}}
사용: python sales_data.py --prev prev.json --out sales.json [--since 20160101] [--max-docs 400]
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import sales_calc as slc

KST = timezone(timedelta(hours=9))
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DOC_URL = "https://opendart.fss.or.kr/api/document.xml"


def dart_key():
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise SystemExit("DART_API_KEY가 없어요 (GitHub Secrets)")
    return key


def list_reports(key, corp_code, since, until, get=requests.get):
    """회사의 거래소공시 중 '영업(잠정)실적' 보고서 목록 [(접수번호, 접수일, 보고서명)] (오래된 것부터)"""
    out, page = [], 1
    while True:
        params = {"crtfc_key": key, "corp_code": corp_code, "bgn_de": since, "end_de": until, "pblntf_ty": "I",
                  "last_reprt_at": "N", "page_no": page, "page_count": 100}
        jo = None
        for attempt in range(3):
            try:
                res = get(LIST_URL, params=params, timeout=30)
                jo = res.json()
                break
            except Exception:
                time.sleep(3 * (attempt + 1))
        if jo is None:
            raise RuntimeError("DART 공시 목록을 받지 못했어요")
        if jo.get("status") == "013":
            break
        if jo.get("status") != "000":
            raise RuntimeError(f"DART 오류 {jo.get('status')}: {jo.get('message')}")
        for r in jo.get("list", []):
            nm = str(r.get("report_nm", "")).replace(" ", "")
            if "영업(잠정)실적" in nm:
                out.append((str(r["rcept_no"]), str(r.get("rcept_dt", "")), nm))
        if page >= int(jo.get("total_page", 1) or 1):
            break
        page += 1
    return sorted(out)


def doc_text(key, rcept_no, get=requests.get):
    """원문 zip -> 글자 (news_digest.fetch_doc_text와 같은 규칙: 앱의 doc_text_from_zip)"""
    import news_digest as nd
    return nd.fetch_doc_text(key, rcept_no)


def parser_sig():
    """sales_calc.py 내용 지문"""
    import hashlib
    with open(slc.__file__, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:10]


def collect(prev, key, since, max_docs, get=requests.get, text_of=None, now=None):
    now = now or datetime.now(KST)
    text_of = text_of or (lambda no: doc_text(key, no))
    sig = parser_sig()
    if prev.get("parser") != sig:  # 읽는 규칙(sales_calc.py)이 바뀌면 처음부터 다시 읽는다 (두 회사 10년이 원문 약 350건)
        prev = {}
    data = {"corps": {c: {"months": dict((prev.get("corps", {}).get(c) or {}).get("months", {}))} for c in slc.SALES_CORPS},
            "seen": dict(prev.get("seen", {})), "parser": sig}
    done, read, failed = 0, 0, 0
    for corp, code in slc.SALES_CORPS.items():
        months = data["corps"][corp]["months"]
        for no, day, nm in list_reports(key, code, since, now.strftime("%Y%m%d"), get):
            if data["seen"].get(no) in ("ok", "skip"):
                continue
            if done >= max_docs:
                break
            done += 1
            try:
                parsed = slc.parse_sales(text_of(no))
            except Exception as e:
                data["seen"][no] = "fail"
                failed += 1
                print(f"  {corp} {no} {nm}: 원문을 받지 못함 ({e})", file=sys.stderr)
                continue
            if not parsed:
                data["seen"][no] = "skip"  # 분기 잠정실적 등 판매대수 표가 없는 공시
                continue
            rec = slc.sales_record(parsed)
            ym = rec.pop("ym")
            old = months.get(ym)
            if old is None or old.get("rcept", "") <= no:  # 정정 공시(나중 접수)가 이긴다
                months[ym] = dict(rec, rcept=no, released=f"{day[:4]}-{day[4:6]}-{day[6:]}")
            data["seen"][no] = "ok"
            read += 1
        data["corps"][corp]["months"] = dict(sorted(months.items()))
    data["updated"] = now.strftime("%Y-%m-%dT%H:%M")
    print(f"원문 {done}건 확인: 판매대수 {read}건, 실패 {failed}건 / " +
          ", ".join(f"{c} {len(v['months'])}개월({min(v['months'], default='-')}~{max(v['months'], default='-')})" for c, v in data["corps"].items()))
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", default="sales.json")
    ap.add_argument("--since", default="20160101")
    ap.add_argument("--max-docs", type=int, default=400, help="한 번에 받을 최대 원문 수 (DART 하루 한도 보호)")
    args = ap.parse_args()
    prev = {}
    if args.prev and os.path.exists(args.prev):
        with open(args.prev, encoding="utf-8") as f:
            prev = json.load(f)
    data = collect(prev, dart_key(), args.since, args.max_docs)
    if not any(v["months"] for v in data["corps"].values()):
        raise SystemExit("판매대수를 하나도 읽지 못했어요 (저장하지 않음)")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
