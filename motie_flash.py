"""산업통상부 '○월 수출입 동향' 보도자료(PDF)에서 잠정치 숫자를 읽는다 (앱 '수출입 분석'의 잠정치용, 화면 없음).

- 매달 1일에 나오는 보도자료의 '참고' 표(백만 달러)를 읽는다:
  월별 수출입·수지(참고 2), 20대 주력 수출품목(수출), 주요 지역별 수출(9대 지역+베트남·인도), 주요 지역별 수입.
- PDF 글자는 `pdftotext -layout`으로 뽑은 것을 쓴다(poppler). 표 모양이 바뀌면 검산에서 걸러 낸다.
- 검산: ① 품목·지역 이름이 예상과 같은지 ② 수출-수입=수지 ③ 마지막 칸의 증감률이 13칸 전(전년 같은 달) 금액과 맞는지 ④ 참고 표('20대 주요 수출 품목 규모')와 금액이 같은지.
  하나라도 어긋나면 ValueError를 내서, 틀린 숫자가 저장되지 않게 한다.
사용: python motie_flash.py 보도자료.pdf   (읽은 결과와 검산 결과를 보여 줌)
"""
import json
import re
import subprocess
import sys

ITEMS = ["반도체", "자동차", "일반기계", "석유제품", "석유화학", "선박", "철강", "자동차부품", "무선통신기기", "디스플레이",
         "전기기기", "바이오헬스", "비철금속", "컴퓨터", "농수산식품", "화장품", "섬유", "생활용품", "가전", "이차전지"]
ITEM_ALIAS = {"철강제품": "철강", "EU": "EU(27)"}  # 참고 표에서는 "철강제품", 괄호 안 (27)은 증감률 괄호로 잘못 읽혀 빠짐
EXPORT_REGIONS = ["중국", "미국", "일본", "아세안", "EU(27)", "중동", "중남미", "CIS", "베트남", "인도"]
COLS = 14  # 표 한 줄: 전년 전체 + 최근 13개월
NUM_RE = re.compile(r"^\d[\d,]*$")
PCT_RE = re.compile(r"\(([△\-]?[\d.,]+)\)")


def pdf_text(path):
    """PDF -> 글자(쪽 사이는 \\f). poppler의 pdftotext가 필요하다."""
    return subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True, text=True).stdout


def to_num(s):
    s = s.strip().replace(",", "")
    neg = s.startswith("△") or s.startswith("-")
    s = s.lstrip("△-")
    return -float(s) if neg else float(s)


def month_back(ym, n):
    y, m = int(ym[:4]), int(ym[5:])
    k = y * 12 + (m - 1) - n
    return f"{k // 12}-{k % 12 + 1:02d}"


def target_month(text):
    m = re.search(r"(20\d\d)년\s*(\d{1,2})월\s*수출입\s*동향", text)
    if not m:
        raise ValueError("제목('○월 수출입 동향')을 못 찾았어요")
    return f"{m.group(1)}-{int(m.group(2)):02d}"


def release_date(text):
    m = re.search(r"보도시점\s*(20\d\d)\.\s*(\d{1,2})\.\s*(\d{1,2})", text)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def is_value_line(line):
    toks = line.split()
    ints = [t for t in toks if NUM_RE.match(t)]
    return len(ints) >= COLS and not any("." in t for t in ints) and len(PCT_RE.findall(line)) == 0 and len(ints) >= len(toks) - 3


def is_share_line(line):
    toks = line.split()
    dec = [t for t in toks if re.match(r"^\d+\.\d+$", t)]
    return len(dec) >= COLS and "(" not in line


def is_pct_line(line):
    return len(PCT_RE.findall(line)) >= COLS


def name_bits(line):
    """한 줄에서 숫자·괄호 값을 빼고 남은 글자(이름 조각)"""
    s = PCT_RE.sub(" ", line)
    return "".join(t for t in s.split() if not re.match(r"^[\d,.△\-]+$", t))


def parse_blocks(lines, with_share):
    """표(품목·지역) -> {이름: {'v': [14칸 금액], 'g': [14칸 증감률]}}. 이름은 값 줄·증감률 줄·비중 줄 사이사이의 글자 조각을 이어 붙여 만든다."""
    out, i = {}, 0
    while i < len(lines):
        if not is_value_line(lines[i]):
            i += 1
            continue
        vals = [to_num(t) for t in lines[i].split() if NUM_RE.match(t)][-COLS:]
        bits, pct, j = [name_bits(lines[i])], None, i + 1
        while j < len(lines) and not is_value_line(lines[j]):
            ln = lines[j]
            if is_pct_line(ln) and pct is None:
                pct = [to_num(x) for x in PCT_RE.findall(ln)][-COLS:]
                bits.append(name_bits(ln))
                if not with_share:
                    j += 1
                    break
            elif is_share_line(ln):
                bits.append(name_bits(ln))
                j += 1
                break
            else:
                bits.append(name_bits(ln))
            j += 1
        name = "".join(bits).replace(" ", "")
        if pct is not None and name:
            out[ITEM_ALIAS.get(name, name)] = {"v": vals, "g": pct}
        i = max(j, i + 1)
    return out


def section(text, start, end):
    """text에서 start가 처음 나오는 곳부터 end가 나오기 전까지의 줄들"""
    a = text.find(start)
    if a < 0:
        raise ValueError(f"'{start}' 부분을 못 찾았어요")
    b = text.find(end, a + len(start)) if end else -1
    return text[a:b if b >= 0 else None].split("\n")


def parse_monthly(text):
    """참고 2 '연도별 수출입 실적 통계'의 월별 줄 -> {'2026-09': {'exp','imp','bal'}} (백만 달러)"""
    block = section(text, "연도별 수출입 실적 통계", "참고 3")
    out, year = {}, None
    row = re.compile(r"^\s*(\d{1,2})\s+([\d,]+)\s+(△?[\d.]+)\s+([\d,]+)\s+(△?[\d.]+)\s+(△?[\d,]+)\s*$")
    for ln in block:
        y = re.match(r"^\s*(20\d\d)년", ln)
        if y:
            year = int(y.group(1))
        m = row.match(ln)
        if m and year:
            out[f"{year}-{int(m.group(1)):02d}"] = {"exp": to_num(m.group(2)), "imp": to_num(m.group(4)), "bal": to_num(m.group(6))}
    return out


def parse_reference(text):
    """참고 '20대 주요 수출 품목 규모 및 증감률' -> {품목: [25.9금액, 26.7, 26.8, 26.9]} (교차 검산용)"""
    block = section(text, "20대 주요 수출 품목 규모", "품목·지역별 상세")
    out = {}
    row = re.compile(r"^\s*\d{1,2}\s+(\S+)\s+((?:[\d,]+\s+\([△\-\d.]+\)\s*){4})$")
    for ln in block:
        m = row.match(ln)
        if m:
            out[ITEM_ALIAS.get(m.group(1), m.group(1))] = [to_num(x) for x in re.findall(r"([\d,]+)\s+\(", m.group(2))]
    return out


def parse_flash(text):
    """보도자료 글자 -> 잠정치 dict. 검산이 틀리면 ValueError.
    {'month','released','monthly':{월:{exp,imp,bal}},'months':[13개월],'items':{품목:[13칸]},'exp_region':{..},'imp_region':{..}} (백만 달러)"""
    month = target_month(text)
    months = [month_back(month, k) for k in range(12, -1, -1)]  # 13개월(전년 같은 달 ~ 이번 달)
    lines = text.split("\n")
    problems = []

    items = parse_blocks(section(text, "20대 품목별 수출 추이", "유망 품목별 수출 추이"), with_share=True)
    exp_reg = parse_blocks(section(text, "주요 지역별 수출 추이", "수입 동향"), with_share=False)
    imp_reg = parse_blocks(section(text, f"지역별 {int(month[5:])}월 수입 추이", "무역수지 동향"), with_share=False)

    for label, got, want in (("20대 품목", items, ITEMS), ("수출 지역", exp_reg, EXPORT_REGIONS), ("수입 지역", imp_reg, EXPORT_REGIONS)):
        miss = [n for n in want if n not in got]
        if miss:
            problems.append(f"{label}에서 못 읽은 이름: {miss} (읽은 것: {sorted(got)})")

    # ③ 마지막 칸 증감률 = (마지막/13칸 전 - 1). 전년 같은 달과 비교되는 칸이라 반올림 오차만 허용
    for label, got in (("품목", items), ("수출지역", exp_reg), ("수입지역", imp_reg)):
        for name, d in got.items():
            v, g = d["v"], d["g"]
            if len(v) == COLS and len(g) == COLS and v[1] > 0:
                calc = (v[-1] / v[1] - 1) * 100
                if abs(calc - g[-1]) > 0.2:
                    problems.append(f"{label} {name}: 증감률 {g[-1]} ≠ 계산 {calc:.1f}")

    monthly = parse_monthly(text)
    if month not in monthly:
        problems.append(f"월별 수출입에 {month}이 없어요")
    for ym, d in monthly.items():
        if abs(d["exp"] - d["imp"] - d["bal"]) > 2:
            problems.append(f"{ym} 수지가 안 맞아요: {d}")

    ref = parse_reference(text)
    for name, vals in ref.items():
        if name in items:
            a = items[name]["v"]
            mine = [a[1], a[-3], a[-2], a[-1]]
            if mine != vals:
                problems.append(f"참고 표와 다름 {name}: {mine} ≠ {vals}")
    if len(ref) < 20:
        problems.append(f"참고 표에서 {len(ref)}개 품목만 읽음")

    if problems:
        raise ValueError("; ".join(problems[:8]))
    return {
        "month": month, "released": release_date(text), "months": months,
        "monthly": {k: v for k, v in monthly.items() if k >= months[0]},
        "items": {n: items[n]["v"][1:] for n in ITEMS},
        "exp_region": {n: exp_reg[n]["v"][1:] for n in EXPORT_REGIONS},
        "imp_region": {n: imp_reg[n]["v"][1:] for n in EXPORT_REGIONS},
    }


def main():
    text = pdf_text(sys.argv[1])
    out = parse_flash(text)
    print(json.dumps(out, ensure_ascii=False)[:1500])
    print(f"검산 통과: {out['month']} (품목 {len(out['items'])}, 수출지역 {len(out['exp_region'])}, 수입지역 {len(out['imp_region'])})")


if __name__ == "__main__":
    main()
