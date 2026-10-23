"""관세청 수출입 통계(공공데이터포털 API)를 모아 둔다 (앱 '수출' 탭용).

- GitHub Actions(.github/workflows/trade_data.yml)가 매일 실행한다.
- 월별 수출입(총괄·국가별·경제권별·시도별·세관별)과 10일 단위 잠정치를 받아 trade.json에 쓴다.
  이미 받아 둔 달은 다시 받지 않고(--prev), 최근 3개월은 수정될 수 있어 매번 다시 받는다.
- 결과는 워크플로가 저장소의 별도 'trade' 브랜치에 한 커밋으로 올린다 (main은 건드리지 않는다).
필요한 환경변수: CUSTOMS_API_KEY (공공데이터포털 일반 인증키, Encoding·Decoding 어느 쪽이든 됨)
사용: python trade_data.py [--prev prev.json] [--out trade.json] [--months 25]
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
BASE = "https://apis.data.go.kr/1220000/"
TOTAL_API = "Newtrade/getNewtradeList"  # 수출입총괄 (N이 대문자)
COUNTRY_API = "nationtrade/getNationtradeList"
REGION_API = "economytrade/getEconomytradeList"
SIDO_API = "sidotrade/getSidotradeList"
PORT_API = "porttrade/getPorttradeList"
TENDAY_APIS = {  # 10일 단위 잠정치: 수출·수입 × 주요 품목·국가
    "exp_item": "prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs",
    "exp_cnty": "cntyMmUtPrviExpAcrs/getCntyMmUtPrviExpAcrs",
    "imp_item": "prlstMmUtPrviImpAcrs/getPrlstMmUtPrviImpAcrs",
    "imp_cnty": "cntyMmUtPrviImpAcrs/getCntyMmUtPrviImpAcrs",
}
REFRESH_MONTHS = 3  # 최근 이 개월 수는 매번 다시 받는다 (수정될 수 있음)
TENDAY_MONTHS = 14  # 10일 단위 잠정치는 이 개월 수만 보관 (전년 같은 달과 비교하려면 13개월 이상)
PAUSE = 0.15  # 호출 사이 쉬는 시간(초)


def api_key():
    key = os.environ.get("CUSTOMS_API_KEY", "").strip()
    if not key:
        raise SystemExit("CUSTOMS_API_KEY가 없어요 (GitHub Secrets 확인).")
    return urllib.parse.unquote(key) if "%" in key else key


def get_items(key, path, **params):
    """API 호출 -> [{태그: 글자}]. 자료가 없으면 []. 오류 응답이면 RuntimeError."""
    last = None
    for attempt in range(4):
        try:
            res = requests.get(BASE + path, params={"serviceKey": key, **params}, timeout=90)
            root = ET.fromstring(res.text)
        except (requests.RequestException, ET.ParseError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
            continue
        code = root.findtext(".//resultCode")
        if code in ("00", None) and root.find(".//items") is not None:
            time.sleep(PAUSE)
            return [{c.tag: (c.text or "").strip() for c in it} for it in root.findall(".//item")]
        msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or ""
        if code in ("00", None) or "조회된" in msg or "없습니다" in msg:  # 자료 없음
            time.sleep(PAUSE)
            return []
        last = RuntimeError(f"{path} {params.get('strtYymm')}: {code} {msg}")
        if "서비스키" in msg or "인증" in msg or code in ("02", "03"):
            break  # 키 문제는 다시 시도해도 같다
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last))


def num(text):
    """'1,234'·'1234'·'' -> 정수 (비면 0)"""
    try:
        return int(float(str(text).replace(",", "") or 0))
    except ValueError:
        return 0


def month_list(now, count):
    """now가 속한 달부터 거꾸로 count개월 -> 오래된 순 ['202409', ..., '202610']"""
    y, m = now.year, now.month
    out = []
    for _ in range(count):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def month_values(key, ym):
    """한 달의 모든 월별 자료 -> dict 또는 None(총괄이 아직 없으면)"""
    total = get_items(key, TOTAL_API, strtYymm=ym, endYymm=ym)
    if not total:
        return None
    t = total[0]
    out = {"total": {"exp": num(t["expDlr"]), "imp": num(t["impDlr"]), "bal": num(t["balPayments"]), "expCnt": num(t["expCnt"]), "impCnt": num(t["impCnt"])}}
    out["country"] = {r["statCd"]: [r["statCdCntnKor1"], num(r["expDlr"]), num(r["impDlr"])] for r in get_items(key, COUNTRY_API, strtYymm=ym, endYymm=ym) if r.get("statCd")}
    out["region"] = {r["statCd"]: [r["statCdCntnKor1"], num(r["expDlr"]), num(r["impDlr"])] for r in get_items(key, REGION_API, strtYymm=ym, endYymm=ym) if r.get("statCd")}
    sido = {}
    for r in get_items(key, SIDO_API, strtYymm=ym, endYymm=ym):
        if r.get("sidoNm"):  # 천달러 -> 달러
            sido[r["sidoNm"]] = [num(r["expUsdAmt"]) * 1000, num(r["impUsdAmt"]) * 1000]
    out["sido"] = sido
    port = {}
    for r in get_items(key, PORT_API, strtYymm=ym, endYymm=ym):  # 항구·공항 행을 세관 단위로 합친다
        name = r.get("statKor") or r.get("cstmSgn") or "?"
        cur = port.setdefault(name, [0, 0])
        cur[0] += num(r["expDlr"])
        cur[1] += num(r["impDlr"])
    out["port"] = port
    return out


def tenday_values(key, ym):
    """한 달의 10일 단위 잠정치 -> {종류: {'01~10': [합계, 1위..10위(천달러)], ...}} 또는 {}"""
    out = {}
    for kind, path in TENDAY_APIS.items():
        rows = get_items(key, path, strtYymm=ym, endYymm=ym)
        got = {}
        for r in rows:
            if r.get("priodMon") == ym and r.get("priodDt"):
                got[r["priodDt"]] = [num(r.get(f"itemUsdAmt{i:02d}")) for i in range(11)]
        if got:
            out[kind] = got
    return out


def build(key, prev, now, months=25):
    """prev(이전 trade.json 또는 {}) + 새로 받은 자료 -> 새 trade.json 내용"""
    current = now.strftime("%Y%m")  # 이번 달은 월별 API가 중간 집계를 주므로 월별 자료에서 빼고, 10일 단위 잠정치만 쓴다
    want = [ym for ym in month_list(now, months) if ym != current]
    store = {}  # 달 -> 월별 자료
    for i, label in enumerate(prev.get("months", [])):
        store[label.replace("-", "")] = {
            "total": {k: v[i] for k, v in prev["total"].items()},
            "country": {c: [n, e[i], m[i]] for c, (n, e, m) in prev.get("country", {}).items() if e[i] is not None},
            "region": {c: [n, e[i], m[i]] for c, (n, e, m) in prev.get("region", {}).items() if e[i] is not None},
            "sido": {c: [e[i], m[i]] for c, (e, m) in prev.get("sido", {}).items() if e[i] is not None},
            "port": {c: [e[i], m[i]] for c, (e, m) in prev.get("port", {}).items() if e[i] is not None},
        }
    store.pop(current, None)  # 이전에 중간 집계가 저장돼 있었어도 버린다
    refresh = set(want[-REFRESH_MONTHS - 1:])
    errors = []
    for ym in want:
        if ym in store and ym not in refresh:
            continue
        try:
            got = month_values(key, ym)
        except RuntimeError as e:
            errors.append(str(e))
            continue
        if got:
            store[ym] = got
    done = sorted(ym for ym in store if ym in set(want))
    if not done:
        raise SystemExit("받아진 달이 없어요: " + "; ".join(errors[:3]))

    result = {"updated": now.strftime("%Y-%m-%dT%H:%M"), "months": [f"{ym[:4]}-{ym[4:]}" for ym in done]}
    result["total"] = {k: [store[ym]["total"][k] for ym in done] for k in ("exp", "imp", "bal", "expCnt", "impCnt")}
    for group in ("country", "region"):
        names = {}
        for ym in done:
            for c, (n, _, _) in store[ym][group].items():
                names[c] = n
        result[group] = {c: [names[c], [store[ym][group].get(c, [None, None, None])[1] for ym in done], [store[ym][group].get(c, [None, None, None])[2] for ym in done]] for c in names}
    for group in ("sido", "port"):
        names = sorted({c for ym in done for c in store[ym][group]})
        result[group] = {c: [[store[ym][group].get(c, [None, None])[0] for ym in done], [store[ym][group].get(c, [None, None])[1] for ym in done]] for c in names}

    # 10일 단위 잠정치: 최근 TENDAY_MONTHS개월
    ten = dict(prev.get("tenday", {}))
    for ym in month_list(now, TENDAY_MONTHS):
        if ym in ten and ym not in refresh:
            continue
        try:
            got = tenday_values(key, ym)
        except RuntimeError as e:
            errors.append(str(e))
            continue
        if got:
            ten[ym] = got
    keep = set(month_list(now, TENDAY_MONTHS))
    result["tenday"] = {ym: v for ym, v in sorted(ten.items()) if ym in keep}
    if errors:
        print(f"(일부 실패 {len(errors)}건) " + "; ".join(errors[:3]), file=sys.stderr)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", default="trade.json")
    ap.add_argument("--months", type=int, default=25)
    args = ap.parse_args()
    prev = {}
    if args.prev and os.path.exists(args.prev):
        try:
            with open(args.prev, encoding="utf-8") as f:
                prev = json.load(f)
        except ValueError:
            prev = {}
    out = build(api_key(), prev, datetime.now(KST), args.months)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"월 {len(out['months'])}개({out['months'][0]}~{out['months'][-1]}), 국가 {len(out['country'])}곳, 시도 {len(out['sido'])}곳, "
          f"세관 {len(out['port'])}곳, 10일 단위 {len(out['tenday'])}개월, {os.path.getsize(args.out) / 1e3:.0f}KB")


if __name__ == "__main__":
    sys.exit(main())
