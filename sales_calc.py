"""현대차·기아 월별 판매실적(DART '영업(잠정)실적(공정공시)'의 판매대수 표) 읽기·계산 (화면 없음).
알람(news_digest.py), 수집기(sales_data.py), 앱(app.py '판매 실적'·기업 브리핑·오늘의 현황)이 같이 쓴다."""
import re

SALES_CORPS = {"현대자동차": "00164742", "기아": "00106641"}  # 매월 판매대수를 공시하는 회사: DART 이름 -> 고유번호
SALES_PARTS = ("국내", "해외", "특수", "합계")  # 표의 구분 (특수 = 기아 군수·상용특장, 2023년부터)
SALES_LABELS = {"국내": "국내", "내수": "국내", "판매대수(내수)": "국내", "해외": "해외", "판매대수(해외)": "해외",
                "특수": "특수", "판매대수(특수)": "특수", "계": "합계", "합계": "합계", "판매대수(계)": "합계"}  # 판매대수 표의 줄 이름 -> 우리 이름


def pct_text(v):
    """'7.5' -> '+7.5%', '-8.9' -> '-8.9%' (원문 숫자 그대로, 부호만 붙임). 숫자가 아니면 None"""
    v = str(v).strip().replace(" ", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", v):
        return None
    return (v if v[0] in "+-" or float(v) == 0 else "+" + v) + "%"


def _month_of(header):
    """표 머리글 '당기실적 (2019년12월)' / "당기실적 ('25.8월)" -> (연도, 월) 또는 None"""
    h = header.replace(" ", "")
    m = re.search(r"(\d{4})년(\d{1,2})월", h) or re.search(r"'(\d{2})\.(\d{1,2})월", h)
    if not m:
        return None
    y = m.group(1)
    return (y if len(y) == 4 else "20" + y), str(int(m.group(2)))


def parse_sales(text):
    """공시 원문 글(표 칸은 ' | ') -> {year, month, monthly: [(이름, 대수글자, 전년比, 전월比)], cum: [(이름, 대수글자, 전년比)], cum_label}
    또는 None(판매대수 표가 없는 공시). 표 양식이 해마다 달라(칸 5개 또는 흑자적자전환여부까지 7개) 머리글로 칸 위치를 찾는다."""
    cells = [" ".join(c.split()) for c in text.split("|")]
    period = re.search(r"당기실적\s*\|?\s*\n\s*(\d{4})-(\d{2})-\d{2}", text)
    ym = (period.group(1), str(int(period.group(2)))) if period else None
    monthly, cum = [], []
    k = 0
    while k < len(cells):
        head = cells[k].replace(" ", "")
        if not (head.startswith("구분") and "단위:대" in head):
            k += 1
            continue
        k += 1
        hdr = []
        while k < len(cells) and cells[k].replace(" ", "") not in SALES_LABELS and len(hdr) < 9:
            hdr.append(cells[k].replace(" ", ""))
            k += 1
        width = len(hdr)
        is_cum = any("누적" in h for h in hdr)
        yoy_i = next((i for i, h in enumerate(hdr) if "전년동기" in h and ("증감" in h or "대비" in h) and "실적" not in h), None)
        mom_i = next((i for i, h in enumerate(hdr) if h.startswith("전기대비")), None)
        if not is_cum and ym is None and hdr:
            ym = _month_of(hdr[0])
        while k + width < len(cells) + 1:
            name = SALES_LABELS.get(cells[k].replace(" ", ""))
            row = cells[k + 1:k + 1 + width]
            if not name or len(row) < width or not re.fullmatch(r"\d[\d,]*", row[0]):
                break
            yoy = pct_text(row[yoy_i]) if yoy_i is not None else None
            if is_cum:
                cum.append((name, row[0], yoy))
            else:
                monthly.append((name, row[0], yoy, pct_text(row[mom_i]) if mom_i is not None else None))
            k += 1 + width
    if not monthly or not ym:
        return None
    return {"year": ym[0], "month": ym[1], "monthly": monthly, "cum": cum, "cum_label": f"1~{ym[1]}월"}


def to_int(s):
    return int(str(s).replace(",", ""))


def sales_record(parsed):
    """parse_sales 결과 -> 저장용 {'ym': 'YYYY-MM', '국내': n, '해외': n, ['특수': n,] '합계': n}"""
    rec = {"ym": f"{parsed['year']}-{int(parsed['month']):02d}"}
    for name, num, _, _ in parsed["monthly"]:
        rec[name] = to_int(num)
    if "합계" not in rec:
        rec["합계"] = sum(rec.get(p, 0) for p in SALES_PARTS[:3])
    return rec


def ym_add(ym, n):
    y, m = int(ym[:4]), int(ym[5:])
    t = y * 12 + (m - 1) + n
    return f"{t // 12}-{t % 12 + 1:02d}"


def pct(cur, prev):
    return None if cur is None or not prev else (cur / prev - 1) * 100


def value(months, ym, part):
    """{ym: {구분: 대수}}에서 한 달 값 (없으면 None)"""
    return (months.get(ym) or {}).get(part)


def ytd(months, ym, part):
    """ym 해의 1월~ym 누계. 그 사이에 빠진 달이 있으면 None"""
    y, m = ym[:4], int(ym[5:])
    vals = [value(months, f"{y}-{k:02d}", part) for k in range(1, m + 1)]
    return None if any(v is None for v in vals) else sum(vals)


def month_rows(months, part, last, n=13):
    """최근 n개월(last부터 거꾸로) -> [{ym, cur, yoy, mom}]"""
    out = []
    for k in range(n):
        ym = ym_add(last, -k)
        cur = value(months, ym, part)
        if cur is None:
            continue
        out.append({"ym": ym, "cur": cur, "yoy": pct(cur, value(months, ym_add(ym, -12), part)), "mom": pct(cur, value(months, ym_add(ym, -1), part))})
    return out


def year_rows(months, part, last):
    """연도별 -> [{year, full(연간, 12달 다 있을 때), same(1~last월 누계), same_yoy}] (최신 해 먼저). last: 가장 최근 달(같은 기간 비교 기준)"""
    years = sorted({ym[:4] for ym in months}, reverse=True)
    mm = last[5:]
    out = []
    for y in years:
        full = ytd(months, f"{y}-12", part)
        same = ytd(months, f"{y}-{mm}", part)
        prev_same = ytd(months, f"{int(y) - 1}-{mm}", part)
        out.append({"year": y, "full": full, "same": same, "same_yoy": pct(same, prev_same)})
    return out
