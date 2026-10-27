"""철강 전기로 제강사 전수 자료 모으기 (손으로 실행, 기획 취재용).

- 대상: DART 기업개황 업종코드(KSIC)가 241(1차 철강 제조업)로 시작하는 상장사
  (parts 브랜치 parts.json의 업종 확인 결과 `screen`을 재사용) + --extra로 더한 곳(비상장도 가능: 포스코·세아베스틸 등).
- 보고서: --years의 사업보고서(11011) + --half의 반기보고서(11012).
- 보고서마다: 매출액·영업이익(연결·별도 둘 다, '단일회사 주요계정'),
  원문 'II. 사업의 내용' 중 '생산' 들어간 소제목(생산능력·생산실적·가동률·생산설비) 글을 표 모양(| 칸, 줄바꿈) 그대로,
  원문 전체에서 전력비·전기료 등 낱말 앞뒤 글 조각, '전기로' 낱말 수.
- 원문은 먼저 가장 최근 사업보고서 한 건만 받아 '전기로'가 나오는 곳(또는 --extra)만 나머지 보고서 원문을 받는다.
- 결과: steel.json → 워크플로가 'steel' 브랜치에 한 커밋으로 덮어 올린다. 지난 steel.json을 --prev로 주면 이어받는다.
필요한 환경변수: DART_API_KEY
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime

from parts_data import Dart, QuotaError, KST, num, _decode, parse_years

ANNUAL, HALF = "11011", "11012"
KSIC = ("241",)
POWER_WORDS = ["전력비", "전기료", "전력료", "전기요금", "전력요금", "동력비", "전력 사용", "전력사용", "수도광열비"]
SNIP = 220
MAX_SNIPS = 40
PROD_CAP = 30000


def accounts(rows):
    out = {}
    for fs in ("CFS", "OFS"):
        d = {}
        for r in rows or []:
            if r.get("fs_div") != fs:
                continue
            name = (r.get("account_nm") or "").replace(" ", "")
            v = num(r.get("thstrm_amount"))
            if "rev" not in d and ("매출액" in name or name in ("수익(매출액)", "영업수익", "매출")):
                d["rev"] = v
            elif "op" not in d and (name.startswith("영업이익") or name.startswith("영업손실")):
                d["op"] = v
        if d:
            out[fs] = d
    return out


def table_text(xml):
    """XML -> 글. 표는 칸을 ' | ', 줄을 줄바꿈으로 남긴다."""
    t = re.sub(r"</(TD|TH|TE|TU)>", " | ", xml, flags=re.I)
    t = re.sub(r"</TR>|<BR\s*/?>|</P>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    import html
    t = html.unescape(t)
    t = re.sub(r"[ \t　]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def read_doc(zip_bytes):
    """-> (사업의 내용 중 '생산' 소제목 글, 전체 글, 사업의 내용 글)"""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xmls = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        parts = [_decode(zf.read(n)) for n in sorted(xmls, key=lambda n: -zf.getinfo(n).file_size)]
    if not parts:
        return "", "", ""
    xml = parts[0]
    whole = "\n".join(table_text(p) for p in parts)
    titles = [(m.start(), table_text(m.group(1))) for m in re.finditer(r"<TITLE[^>]*>(.*?)</TITLE>", xml, re.S | re.I)]
    start = next((i for i, (_, t) in enumerate(titles) if "사업의 내용" in t), None)
    if start is None:
        return "", whole, ""
    end = next((p for p, t in titles[start + 1:] if re.match(r"^(III|Ⅲ)\s*\.", t)), len(xml))
    sec_xml = xml[titles[start][0]:end]
    sec = table_text(sec_xml)
    subs = [(m.start(), table_text(m.group(1))) for m in re.finditer(r"<TITLE[^>]*>(.*?)</TITLE>", sec_xml, re.S | re.I)]
    prod = []
    for i, (p, t) in enumerate(subs):
        if "생산" in t:
            q = subs[i + 1][0] if i + 1 < len(subs) else len(sec_xml)
            prod.append(table_text(sec_xml[p:q]))
    if not prod:  # 소제목이 TITLE이 아닌 옛 양식
        k = sec.find("생산능력")
        prod = [sec[max(0, k - 500):k + 15000]] if k >= 0 else []
    return "\n\n".join(prod)[:PROD_CAP], whole, sec


def power_snips(text):
    out, seen = [], set()
    for w in POWER_WORDS:
        for m in re.finditer(re.escape(w), text):
            a = max(0, m.start() - SNIP)
            key = a // 150
            if key in seen:
                continue
            seen.add(key)
            out.append(text[a:m.end() + SNIP].replace("\n", " / "))
            if len(out) >= MAX_SNIPS:
                return out
    return out


def periods(years, half):
    ps = [(str(y), ANNUAL) for y in years] + [(str(y), HALF) for y in half]
    return ps


def key_of(y, code):
    return f"{y}{'H1' if code == HALF else ''}"


def collect(dart, corps, years, half, prev, log):
    prevc = {c["corp_code"]: c for c in (prev or {}).get("corps", [])}
    ps = periods(years, half)
    latest = (str(max(years)), ANNUAL) if years else None
    out, stopped = [], None
    for ci, c in enumerate(corps, 1):
        rec = {**c, "reports": dict(prevc.get(c["corp_code"], {}).get("reports", {})),
               "eaf": prevc.get(c["corp_code"], {}).get("eaf")}
        out.append(rec)
        if stopped:
            continue
        try:
            for y, code in ps:
                k = key_of(y, code)
                if rec["reports"].get(k, {}).get("fin_done"):
                    continue
                fj = dart.json("fnlttSinglAcnt.json", {"corp_code": c["corp_code"], "bsns_year": y, "reprt_code": code})
                lst = (fj or {}).get("list") or []
                rec["reports"][k] = {"fin": accounts(lst), "rcept_no": lst[0].get("rcept_no") if lst else None,
                                     "fin_done": True}
            if rec["eaf"] is None and latest:  # 가장 최근 사업보고서로 전기로 여부 판단
                r = rec["reports"].get(key_of(*latest), {})
                if not r.get("rcept_no"):
                    r = next((rec["reports"][key_of(y, cd)] for y, cd in reversed(ps)
                              if rec["reports"].get(key_of(y, cd), {}).get("rcept_no")), {})
                if r.get("rcept_no"):
                    zb = dart.document(r["rcept_no"])
                    if zb:
                        prod, whole, sec = read_doc(zb)
                        r.update({"prod_text": prod, "power": power_snips(whole), "eaf_words": sec.count("전기로"),
                                  "doc_done": True})
                        rec["eaf"] = sec.count("전기로") > 0
            if rec["eaf"] or c.get("picked_by") == "직접 추가":
                for y, code in ps:
                    r = rec["reports"].get(key_of(y, code), {})
                    if r.get("doc_done") or not r.get("rcept_no"):
                        continue
                    zb = dart.document(r["rcept_no"])
                    if not zb:
                        r["doc_err"] = "원문 받기 실패"
                        continue
                    prod, whole, sec = read_doc(zb)
                    r.update({"prod_text": prod, "power": power_snips(whole), "eaf_words": sec.count("전기로"),
                              "doc_done": True})
        except QuotaError as e:
            stopped = str(e)
            log(f"멈춤: {e} — {c['name']}에서. 받은 데까지 저장합니다.")
        if ci % 10 == 0:
            log(f"  {ci}/{len(corps)}곳 (호출 {dart.calls}번)")
    return out, stopped


def pick(screen, corp_rows, extra):
    names = {r["corp_name"]: r for r in corp_rows}
    picked, seen = [], set()
    for code, meta in screen.items():
        if str(meta.get("induty", "")).startswith(KSIC) and meta.get("cls") in ("Y", "K"):
            r = next((x for x in corp_rows if x["corp_code"] == code), None)
            if r and (r.get("stock_code") or "").strip():
                picked.append({"corp_code": code, "name": r["corp_name"], "stock_code": r["stock_code"],
                               "induty": meta.get("induty"), "picked_by": "업종코드"})
                seen.add(code)
    missing = []
    for n in extra:
        r = names.get(n)
        if not r:
            missing.append(n)
        elif r["corp_code"] not in seen:
            picked.append({"corp_code": r["corp_code"], "name": n, "stock_code": (r.get("stock_code") or "").strip(),
                           "induty": "", "picked_by": "직접 추가"})
    return picked, missing


def main(argv=None, session=None):
    import csv
    ap = argparse.ArgumentParser()
    ap.add_argument("--corp-csv", default="corp_codes.csv")
    ap.add_argument("--screen", default="parts.json", help="업종 확인 결과(screen)가 든 parts.json")
    ap.add_argument("--out", default="steel.json")
    ap.add_argument("--prev", default="")
    ap.add_argument("--years", default="2021-2025")
    ap.add_argument("--half", default="2025,2026")
    ap.add_argument("--extra", default="")
    ap.add_argument("--max-calls", type=int, default=5000)
    ap.add_argument("--max-minutes", type=float, default=0)
    args = ap.parse_args(argv)
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise SystemExit("DART_API_KEY가 없어요.")
    with open(args.corp_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(args.screen, encoding="utf-8") as f:
        screen = json.load(f).get("screen", {})
    prev = {}
    if args.prev and os.path.exists(args.prev):
        with open(args.prev, encoding="utf-8") as f:
            prev = json.load(f)
    extra = [n.strip() for n in args.extra.split(",") if n.strip()]
    corps, missing = pick(screen, rows, extra)
    print(f"대상 {len(corps)}곳" + (f", 못 찾은 추가 기업: {', '.join(missing)}" if missing else ""))
    dart = Dart(key, session=session, max_calls=args.max_calls, max_minutes=args.max_minutes)
    years, half = parse_years(args.years), parse_years(args.half)
    data, stopped = collect(dart, corps, years, half, prev, print)
    result = {"built": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "complete": stopped is None, "stopped": stopped,
              "years": years, "half": half, "ksic": list(KSIC), "extra_missing": missing, "calls_this_run": dart.calls,
              "notes": ["업종코드 241(1차 철강) 상장사 + 직접 추가(비상장 포함)",
                        "eaf=가장 최근 사업보고서 '사업의 내용'에 '전기로' 낱말이 있는지(설비 보유 확정 아님, 원문 확인)",
                        "prod_text=사업의 내용 중 '생산' 소제목 글(표는 | 와 줄바꿈), power=원문 전체 전력비 낱말 앞뒤 조각",
                        "fin: CFS=연결, OFS=별도, 원 단위"],
              "corps": data}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    n_eaf = sum(1 for c in data if c.get("eaf"))
    print(f"저장: {args.out} — 대상 {len(data)}곳, 전기로 언급 {n_eaf}곳, 호출 {dart.calls}번"
          + (f", 멈춤: {stopped}" if stopped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
