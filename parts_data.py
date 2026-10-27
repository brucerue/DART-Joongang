"""상장 자동차부품사 전수 10년 자료 모으기 (손으로 실행, 기획 취재용).

- 대상: DART 상장사(corp-codes 브랜치 corp_codes.csv에서 종목코드 있는 곳) 중
  DART 기업개황 업종코드(KSIC)가 303(자동차 신품 부품)·304(재제조 부품)로 시작하는 곳 + --extra로 더한 곳.
- 연도별(사업보고서): 직원 수(정규직·계약직·합계)·평균 근속연수·1인 평균 급여(DART '직원 현황'),
  매출액·영업이익(DART '단일회사 주요계정', 연결 우선).
- --doc-years에 적은 해만: 사업보고서 원문 'II. 사업의 내용'에서 '주요 제품' 부분 글을 잘라 두고
  전동화·내연기관 낱말 수를 센다. 전동화 매출 비중은 API에 없어서, 이 글을 보고 사람이(또는 Claude가) 분류한다.
- 결과: parts.json(전체) + parts.csv(기업×연도 한 줄씩, 엑셀용). 워크플로가 'parts' 브랜치에 한 커밋으로 덮어 올린다.
- 이어받기: --prev에 지난 parts.json을 주면 이미 받은 (기업, 연도)와 업종 확인 결과는 다시 부르지 않는다.
  DART 하루 한도(020)에 걸리면 받은 데까지 저장하고 끝낸다(다음 날 다시 실행하면 이어받음).

주의(기사에 쓸 때): 지금 상장된 회사만 대상이라 그사이 상장폐지된 부품사는 빠진다(생존 편향).
1인 평균 급여는 회사마다 단위(원·천원·백만원)가 다르게 적힌 경우가 있어 원문 글자도 같이 남긴다.
필요한 환경변수: DART_API_KEY
사용: python parts_data.py --corp-csv corp_codes.csv --out parts.json [--prev old.json] [--years 2015-2025]
      [--doc-years 2016,2020,2025] [--extra 현대위아,한온시스템] [--max-calls 15000]
"""
import argparse
import csv
import html
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://opendart.fss.or.kr/api/"
REPORT = "11011"  # 사업보고서
KSIC_PREFIXES = ("303", "304")
KST = timezone(timedelta(hours=9))
TIMEOUT = (15, 90)
SLEEP = 0.08
ELEC_WORDS = ["전기차", "전동화", "친환경차", "EV", "하이브리드", "HEV", "PHEV", "수소", "연료전지",
              "배터리", "모터", "인버터", "감속기", "열관리", "히트펌프", "전장", "자율주행", "OBC", "BMS"]
ICE_WORDS = ["내연기관", "엔진", "변속기", "배기", "연료펌프", "실린더", "피스톤", "크랭크", "캠샤프트", "터보"]
PRODUCT_CAP = 6000


class QuotaError(Exception):
    pass


class Dart:
    def __init__(self, key, session=None, max_calls=15000, sleep=SLEEP, max_minutes=0):
        self.key, self.s = key, session or requests.Session()
        self.calls, self.max_calls, self.sleep = 0, max_calls, sleep
        self.deadline = time.monotonic() + max_minutes * 60 if max_minutes else None

    def _get(self, path, params):
        if self.calls >= self.max_calls:
            raise QuotaError(f"이번 실행 호출 한도 {self.max_calls}번")
        if self.deadline and time.monotonic() > self.deadline:
            raise QuotaError("이번 실행 시간 한도 — 받은 데까지 저장")
        self.calls += 1
        if self.sleep:
            time.sleep(self.sleep)
        last = None
        for attempt in range(3):
            try:
                return self.s.get(BASE + path, params={"crtfc_key": self.key, **params}, timeout=TIMEOUT)
            except requests.RequestException as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"DART 접속 실패: {path} {last}")

    def json(self, path, params):
        jo = self._get(path, params).json()
        st = jo.get("status")
        if st == "000":
            return jo
        if st == "013":  # 조회된 자료 없음
            return None
        if st == "020":
            raise QuotaError("DART 하루 호출 한도 초과(020)")
        raise RuntimeError(f"DART 오류 {st} {jo.get('message', '')} ({path} {params})")

    def document(self, rcept_no):
        res = self._get("document.xml", {"rcept_no": rcept_no})
        data = res.content or b""
        if not data.startswith(b"PK"):
            msg = data.decode("utf-8", "ignore")
            if "<status>020</status>" in msg:
                raise QuotaError("DART 하루 호출 한도 초과(020)")
            return None
        return data


# ---------- 숫자 읽기 ----------
def num(s):
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if t in ("", "-", "－"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    v = -v if neg else v
    return int(v) if v.is_integer() else v


def tenure_years(s):
    """'12.3' / '12년 3개월' / '12년' / '3개월' -> 연 단위 소수"""
    if s is None:
        return None
    t = str(s).replace(" ", "")
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)년)?(?:(\d+(?:\.\d+)?)개?월)?", t)
    if m and (m.group(1) or m.group(2)):
        return round(float(m.group(1) or 0) + float(m.group(2) or 0) / 12, 2)
    return num(t)


TOTAL_WORDS = ("합계", "총계", "전체", "총합")


def _is_total(v):
    v = (v or "").replace(" ", "")
    return v in ("계", "총") or any(w in v for w in TOTAL_WORDS)


def employee_summary(rows):
    """DART 직원 현황 여러 줄(사업부문×성별) -> 합계 한 개. 합계 줄이 있으면 그걸 쓰고, 없으면 더한다."""
    if not rows:
        return None
    by_div = [r for r in rows if _is_total(r.get("fo_bbm"))]
    if by_div:
        both = [r for r in by_div if _is_total(r.get("sexdstn"))]
        pick, method = (both, "부문합계·성별합계 줄") if both else (by_div, "부문합계 줄(성별 더함)")
    else:
        sex_tot = [r for r in rows if _is_total(r.get("sexdstn"))]
        pick, method = (sex_tot, "부문별 성별합계 더함") if sex_tot else (rows, "모든 줄 더함")

    def total(field):
        vals = [num(r.get(field)) for r in pick]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    emp = total("sm")
    reg = total("rgllbr_co")
    con = total("cnttk_co")
    pay_total = total("fyer_salary_totamt")
    if len(pick) == 1:
        ten = tenure_years(pick[0].get("avrg_cnwk_sdytrn"))
        pay_avg_raw = pick[0].get("jan_salary_am")
    else:  # 인원으로 가중평균
        pairs = [(tenure_years(r.get("avrg_cnwk_sdytrn")), num(r.get("sm"))) for r in pick]
        pairs = [(t, w) for t, w in pairs if t is not None and w]
        ten = round(sum(t * w for t, w in pairs) / sum(w for _, w in pairs), 2) if pairs else None
        pay_avg_raw = None
    return {"emp": emp, "regular": reg, "contract": con, "tenure": ten,
            "pay_total_raw": pay_total, "pay_avg_raw": pay_avg_raw, "emp_method": method,
            "rcept_no": rows[0].get("rcept_no")}


def pick_accounts(rows):
    """단일회사 주요계정 -> 매출액·영업이익 (연결 CFS 우선, 없으면 별도 OFS)"""
    out = {}
    for fs in ("CFS", "OFS"):
        part = [r for r in rows or [] if r.get("fs_div") == fs]
        if not part:
            continue
        for r in part:
            name = (r.get("account_nm") or "").replace(" ", "")
            v = num(r.get("thstrm_amount"))
            if "rev" not in out and ("매출액" in name or name in ("수익(매출액)", "영업수익", "매출")):
                out["rev"] = v
            elif "op" not in out and (name.startswith("영업이익") or name.startswith("영업손실")):
                out["op"] = v
        if out:
            out["fs"] = fs
            return out
    return out


# ---------- 사업보고서 원문 ----------
def _decode(b):
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "ignore")


def _text(xml):
    t = re.sub(r"<[^>]+>", " ", xml)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def business_section(zip_bytes):
    """zip 안의 본문 XML -> ('II. 사업의 내용' 전체 글, '주요 제품' 부분 글)"""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            return "", ""
        main = max(names, key=lambda n: zf.getinfo(n).file_size)
        xml = _decode(zf.read(main))
    titles = [(m.start(), _text(m.group(1))) for m in re.finditer(r"<TITLE[^>]*>(.*?)</TITLE>", xml, re.S | re.I)]
    start = next((i for i, (_, t) in enumerate(titles) if "사업의 내용" in t), None)
    if start is None:
        return "", ""
    end_pos = next((p for p, t in titles[start + 1:] if re.match(r"^(III|Ⅲ)\s*\.", t)), len(xml))
    sec_xml = xml[titles[start][0]:end_pos]
    sec = _text(sec_xml)
    sub = [(m.start(), _text(m.group(1))) for m in re.finditer(r"<TITLE[^>]*>(.*?)</TITLE>", sec_xml, re.S | re.I)]
    prod = ""
    for i, (p, t) in enumerate(sub):
        if "주요 제품" in t or "주요제품" in t:
            q = sub[i + 1][0] if i + 1 < len(sub) else len(sec_xml)
            prod = _text(sec_xml[p:q])
            break
    if not prod:  # 옛 양식: 소제목이 TITLE이 아님 -> 글에서 '주요 제품'부터 자름
        k = sec.find("주요 제품")
        prod = sec[k:] if k >= 0 else sec
    return sec, prod[:PRODUCT_CAP]


def word_counts(text, words):
    out = {}
    for w in words:
        if w.isascii():
            c = len(re.findall(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", text))
        else:
            c = text.count(w)
        if c:
            out[w] = c
    return out


# ---------- 대상 고르기 ----------
def load_listed(path):
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("stock_code") or "").strip()]


def screen(dart, listed, prev_screen, prefixes, extra_names, log):
    """업종코드 확인. 지난 결과가 있으면 그대로 쓴다."""
    info = dict(prev_screen or {})
    todo = [r for r in listed if r["corp_code"] not in info]
    log(f"업종 확인: 지난 결과 {len(info)}곳, 새로 확인 {len(todo)}곳")
    partial = None
    t0 = time.monotonic()
    for i, r in enumerate(todo, 1):
        try:
            jo = dart.json("company.json", {"corp_code": r["corp_code"]})
        except QuotaError as e:  # 확인한 데까지 돌려주고, 대상 고르기는 다음 실행으로 미룬다
            partial = str(e)
            log(f"업종 확인 중 멈춤: {e} ({i - 1}/{len(todo)}곳 확인)")
            break
        info[r["corp_code"]] = {"induty": (jo or {}).get("induty_code", ""), "cls": (jo or {}).get("corp_cls", ""),
                                "est": (jo or {}).get("est_dt", "")}
        if i % 100 == 0:
            el = time.monotonic() - t0
            log(f"  {i}/{len(todo)} ({el:.0f}초, 한 번에 {el / i:.2f}초)")
    if partial:
        return info, [], [], partial
    extra = set(n.strip() for n in extra_names if n.strip())
    picked = []
    for r in listed:
        meta = info.get(r["corp_code"], {})
        by_code = str(meta.get("induty", "")).startswith(prefixes)
        by_name = r["corp_name"] in extra
        if (by_code or by_name) and meta.get("cls") in ("Y", "K", ""):
            picked.append({"corp_code": r["corp_code"], "name": r["corp_name"], "stock_code": r["stock_code"],
                           "induty": meta.get("induty", ""), "market": {"Y": "코스피", "K": "코스닥"}.get(meta.get("cls"), ""),
                           "picked_by": "업종코드" if by_code else "직접 추가"})
    missing = sorted(extra - {p["name"] for p in picked})
    return info, picked, missing, None


# ---------- 현금흐름 (전체 재무제표) ----------
CF_ITEMS = {  # 키: (IFRS 계정ID들, 계정이름에 들어가는 글자들)
    "ocf": (("ifrs-full_CashFlowsFromUsedInOperatingActivities",), ("영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동으로부터의현금흐름", "영업활동순현금흐름")),
    "icf": (("ifrs-full_CashFlowsFromUsedInInvestingActivities",), ("투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동순현금흐름")),
    "capex": (("ifrs-full_PurchaseOfPropertyPlantAndEquipment",), ("유형자산의취득", "유형자산취득")),
}


def pick_cashflow(rows):
    """전체 재무제표(fnlttSinglAcntAll) 중 현금흐름표(sj_div=CF)에서 영업·투자 현금흐름과 유형자산 취득."""
    cf = [r for r in rows or [] if r.get("sj_div") == "CF"]
    out = {}
    for key, (ids, names) in CF_ITEMS.items():
        hit = next((r for r in cf if r.get("account_id") in ids), None)
        if hit is None:
            hit = next((r for r in cf if any(n in (r.get("account_nm") or "").replace(" ", "") for n in names)), None)
        if hit is not None:
            v = num(hit.get("thstrm_amount"))
            if key == "capex" and v is not None:
                v = abs(v)  # 회사마다 음수(유출)·양수로 적는 방식이 달라 크기로 통일
            out[key] = v
    return out


def collect_cashflow(dart, data, years, log):
    """각 기업·연도에 cf가 없으면 받는다. 매출·영업이익과 같은 기준(fs: 연결/별도)으로 부른다."""
    stopped = None
    for ci, c in enumerate(data, 1):
        try:
            for y in years:
                row = c["years"].get(str(y))
                if not row or not row.get("done") or "cf" in row:
                    continue
                fs = row.get("fs") or "CFS"
                jo = dart.json("fnlttSinglAcntAll.json", {"corp_code": c["corp_code"], "bsns_year": str(y),
                                                          "reprt_code": REPORT, "fs_div": fs})
                got = pick_cashflow((jo or {}).get("list"))
                if not got and fs == "CFS":  # 연결이 없으면 별도로 한 번 더
                    jo = dart.json("fnlttSinglAcntAll.json", {"corp_code": c["corp_code"], "bsns_year": str(y),
                                                              "reprt_code": REPORT, "fs_div": "OFS"})
                    got = pick_cashflow((jo or {}).get("list"))
                    fs = "OFS" if got else fs
                row["cf"] = {**got, "fs": fs}
        except QuotaError as e:
            stopped = str(e)
            log(f"현금흐름 멈춤: {e} — {c['name']}에서. 받은 데까지 저장합니다.")
            break
        if ci % 20 == 0:
            log(f"  현금흐름 {ci}/{len(data)}곳 (호출 {dart.calls}번)")
    return stopped


# ---------- 모으기 ----------
def collect(dart, corps, years, doc_years, prev_corps, log):
    prev = {c["corp_code"]: c for c in prev_corps or []}
    out, stopped = [], None
    for ci, c in enumerate(corps, 1):
        old = prev.get(c["corp_code"], {})
        rec = {**c, "years": dict(old.get("years", {})), "docs": dict(old.get("docs", {}))}
        out.append(rec)
        if stopped:
            continue
        try:
            for y in years:
                ys = str(y)
                if ys in rec["years"] and rec["years"][ys].get("done"):
                    continue
                jo = dart.json("empSttus.json", {"corp_code": c["corp_code"], "bsns_year": ys, "reprt_code": REPORT})
                row = employee_summary((jo or {}).get("list")) or {}
                fj = dart.json("fnlttSinglAcnt.json", {"corp_code": c["corp_code"], "bsns_year": ys, "reprt_code": REPORT})
                row.update(pick_accounts((fj or {}).get("list")))
                if not row.get("rcept_no") and fj and fj.get("list"):
                    row["rcept_no"] = fj["list"][0].get("rcept_no")
                row["done"] = True
                rec["years"][ys] = row
            for y in doc_years:
                ys = str(y)
                if ys in rec["docs"]:
                    continue
                rno = (rec["years"].get(ys) or {}).get("rcept_no")
                if not rno:
                    rec["docs"][ys] = {"none": "해당 연도 사업보고서 없음"}
                    continue
                zb = dart.document(rno)
                if not zb:
                    rec["docs"][ys] = {"none": "원문 받기 실패", "rcept_no": rno}
                    continue
                sec, prod = business_section(zb)
                rec["docs"][ys] = {"rcept_no": rno, "elec": word_counts(sec, ELEC_WORDS),
                                   "ice": word_counts(sec, ICE_WORDS), "product_text": prod,
                                   "section_chars": len(sec)}
        except QuotaError as e:
            stopped = str(e)
            log(f"멈춤: {e} — {c['name']}에서. 받은 데까지 저장합니다.")
        if ci % 20 == 0:
            log(f"  {ci}/{len(corps)}곳 (호출 {dart.calls}번)")
    return out, stopped


def to_csv(corps, path):
    cols = ["기업", "종목코드", "시장", "업종코드", "연도", "직원수", "정규직", "계약직", "평균근속(년)",
            "1인평균급여(원문)", "연간급여총액(원문)", "매출액", "영업이익", "재무기준", "직원집계방식", "접수번호",
            "영업활동현금흐름", "투자활동현금흐름", "유형자산취득", "현금흐름기준"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in corps:
            for y in sorted(c["years"]):
                r = c["years"][y]
                w.writerow([c["name"], c["stock_code"], c["market"], c["induty"], y, r.get("emp"), r.get("regular"),
                            r.get("contract"), r.get("tenure"), r.get("pay_avg_raw"), r.get("pay_total_raw"),
                            r.get("rev"), r.get("op"), r.get("fs"), r.get("emp_method"), r.get("rcept_no"),
                            (r.get("cf") or {}).get("ocf"), (r.get("cf") or {}).get("icf"),
                            (r.get("cf") or {}).get("capex"), (r.get("cf") or {}).get("fs")])


def parse_years(s):
    out = []
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main(argv=None, session=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--corp-csv", default="corp_codes.csv")
    ap.add_argument("--out", default="parts.json")
    ap.add_argument("--csv", default="parts.csv")
    ap.add_argument("--prev", default="")
    ap.add_argument("--years", default="2015-2025")
    ap.add_argument("--doc-years", default="2016,2020,2025")
    ap.add_argument("--extra", default="")
    ap.add_argument("--max-calls", type=int, default=15000)
    ap.add_argument("--sleep", type=float, default=SLEEP)
    ap.add_argument("--max-minutes", type=float, default=0, help="이 시간이 지나면 받은 데까지 저장하고 끝냄(0=제한 없음)")
    ap.add_argument("--cashflow", action="store_true", help="현금흐름표(영업·투자 현금흐름, 유형자산 취득)도 받음")
    args = ap.parse_args(argv)
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise SystemExit("DART_API_KEY가 없어요.")
    prev = {}
    if args.prev and os.path.exists(args.prev):
        with open(args.prev, encoding="utf-8") as f:
            prev = json.load(f)
    dart = Dart(key, session=session, max_calls=args.max_calls, sleep=args.sleep, max_minutes=args.max_minutes)
    years, doc_years = parse_years(args.years), parse_years(args.doc_years)
    listed = load_listed(args.corp_csv)
    if len(listed) < 1500:
        raise SystemExit(f"상장사 목록이 {len(listed)}곳뿐이라 멈춥니다(정상 2,500곳 이상).")
    info, corps, missing, stopped = screen(dart, listed, prev.get("screen"), KSIC_PREFIXES, args.extra.split(","), print)
    if stopped:  # 업종 확인이 덜 끝남 -> 확인 결과만 저장하고, 기업 자료는 지난 것을 그대로 둔다
        data = prev.get("corps", [])
    else:
        print(f"대상 {len(corps)}곳 (업종코드 {sum(c['picked_by'] == '업종코드' for c in corps)}곳)"
              + (f", 못 찾은 추가 기업: {', '.join(missing)}" if missing else ""))
        data, stopped = collect(dart, corps, years, doc_years, prev.get("corps"), print)
        if args.cashflow and not stopped:
            stopped = collect_cashflow(dart, data, years, print)
    done = sum(1 for c in data for y in years if c["years"].get(str(y), {}).get("done"))
    need = len(data) * len(years)
    cf_need = sum(1 for c in data for y in years if c["years"].get(str(y), {}).get("done"))
    cf_done = sum(1 for c in data for y in years if "cf" in c["years"].get(str(y), {}))
    cf_ok = (not args.cashflow) or cf_done == cf_need
    result = {
        "built": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "complete": stopped is None and done == need and cf_ok,
        "stopped": stopped, "progress": f"{done}/{need}", "cf_progress": f"{cf_done}/{cf_need}",
        "years": years, "doc_years": doc_years, "ksic_prefixes": list(KSIC_PREFIXES),
        "extra_missing": missing, "calls_this_run": dart.calls,
        "notes": ["지금 상장된 회사만 대상(그사이 상장폐지 부품사 빠짐, 생존 편향)",
                  "직원 수는 사업보고서 기준(연말), 미등기임원 제외",
                  "1인 평균 급여는 회사마다 단위가 다를 수 있어 원문 글자 그대로",
                  "전동화·내연기관 낱말 수는 'II. 사업의 내용' 전체 글 기준 참고값(매출 비중 아님)"],
        "screen": info, "corps": data,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    to_csv(data, args.csv)
    print(f"저장: {args.out}, {args.csv} — 진행 {done}/{need}, 호출 {dart.calls}번" + (f", 멈춤: {stopped}" if stopped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
