"""제목 옆 '계산기'의 계산 (화면 없음): 증감률, 금액 단위, 연평균 성장률, 비중, 글자 수, 날짜.
결과는 기사에 바로 옮겨 쓰므로 중앙일보 표기대로 숫자에 쉼표를 넣지 않는다."""
import math
import re
from datetime import date, timedelta

UNIT_MULT = {"원": 1, "천원": 1e3, "만원": 1e4, "백만원": 1e6, "억원": 1e8, "조원": 1e12}


def parse_num(text):
    """'1,234' / '-3.5' / '△1,234' / '(1,234)' / '12.3%' / '1조2,345억' / '3,000억원' -> (숫자, %인지). 못 읽으면 (None, False).
    조·억·만이 붙으면 그 자릿수를 곱한 값(원 단위 숫자)으로 돌려준다"""
    s = str(text or "").strip().replace(" ", "").replace("원", "")
    if not s:
        return None, False
    neg = False
    if s[0] in "△▲-−":  # 공시는 마이너스를 △로 쓰기도 한다 (▲는 보통 증가지만 붙여넣기 실수 방지로 부호만 본다)
        neg = s[0] != "▲"
        s = s[1:]
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    pct = s.endswith("%") or s.endswith("%p")
    s = re.sub(r"%p?$", "", s)
    total, found = 0.0, False
    for word, mult in (("조", 1e12), ("억", 1e8), ("만", 1e4)):
        m = re.match(r"^([\d,]*\.?\d+)" + word, s)
        if m:
            total += float(m.group(1).replace(",", "")) * mult
            s = s[m.end():]
            found = True
    if found:
        if s:
            try:
                total += float(s.replace(",", ""))
            except ValueError:
                return None, False
        return (-total if neg else total), False
    try:
        v = float(s.replace(",", ""))
    except ValueError:
        return None, False
    return (-v if neg else v), pct


def change(a, b):
    """이전 값 a -> 지금 값 b. {'diff', 'rate'(%, 의미 없으면 None), 'times'(배, 둘 다 양수일 때), 'status'}"""
    diff = b - a
    rate = diff / a * 100 if a > 0 else None  # 이전 값이 0 이하(적자)면 증감률은 뜻이 거꾸로라 내지 않는다
    times = b / a if a > 0 and b > 0 else None
    if a < 0 and b > 0:
        status = "흑자전환"
    elif a > 0 and b < 0:
        status = "적자전환"
    elif a < 0 and b < 0:
        status = "적자축소" if b > a else ("적자확대" if b < a else "적자 지속(변동 없음)")
    else:
        status = "증가" if diff > 0 else ("감소" if diff < 0 else "변동 없음")
    return {"diff": diff, "rate": rate, "times": times, "status": status}


def num_text(v, digits=1):
    """숫자 -> 쉼표 붙인 글 (정수면 소수점 없이)"""
    if v is None:
        return "-"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.{digits}f}"


def won_words(won):
    """원 -> 기사체 금액: 1조2345억원 / 4567억원 / 3200만원 / 5000원"""
    if won is None:
        return "-"
    sign = "-" if won < 0 else ""
    w = abs(won)
    if w >= 1e8:
        eok = int(round(w / 1e8))
        jo, rest = divmod(eok, 10000)
        body = f"{jo}조{rest}억원" if jo and rest else (f"{jo}조원" if jo else f"{rest}억원")
    elif w >= 1e4:
        body = f"{int(round(w / 1e4))}만원"
    else:
        body = f"{int(round(w))}원"
    return sign + body


def convert(value, unit):
    """숫자 + 단위 -> [(단위, 그 단위로 나타낸 값 글)] + 기사체"""
    won = value * UNIT_MULT[unit]
    rows = []
    for u, mult in UNIT_MULT.items():
        v = won / mult
        rows.append((u, f"{v:.4f}".rstrip("0").rstrip(".")))  # 중앙일보 표기: 숫자에 쉼표를 쓰지 않는다
    return won, rows, won_words(won)


def cagr(a, b, years):
    """연평균 성장률(%). 시작·끝이 양수이고 기간이 0보다 클 때만"""
    if a is None or b is None or not years or years <= 0 or a <= 0 or b <= 0:
        return None
    return ((b / a) ** (1 / years) - 1) * 100


def shares(text):
    """여러 줄 '이름 값' (또는 값만) -> ([(이름, 값, 비중%)], 합계). 읽을 수 없는 줄은 건너뛴다"""
    items = []
    for i, line in enumerate(str(text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(.*?)[\s:=\t]+([△▲\-−(]?[\d,.]+[조억만]?[\d,.]*[조억만]?[\d,.]*\)?%?)$", line)
        name, raw = (m.group(1).strip(), m.group(2)) if m else ("", line)
        v, _ = parse_num(raw)
        if v is None:
            continue
        items.append((name or f"{len(items) + 1}번", v))
    total = sum(v for _, v in items)
    return [(n, v, v / total * 100 if total else None) for n, v in items], total


def text_stats(text):
    """원고 글자 수: 공백 포함·제외, 200자 원고지 매수, 문장 수, 문단 수"""
    t = str(text or "")
    body = t.replace("\r", "")
    with_space = len(body.replace("\n", ""))
    no_space = len(re.sub(r"\s", "", body))
    sentences = len([s for s in re.split(r"(?<=[.?!。])\s+|(?<=다\.)", body) if s.strip()])
    paras = len([p for p in re.split(r"\n\s*\n|\n", body) if p.strip()])
    return {"with_space": with_space, "no_space": no_space, "sheets": math.ceil(with_space / 200) if with_space else 0,
            "sentences": sentences, "paras": paras}


def add_business_days(start, n):
    """start에서 영업일(토·일 제외, 공휴일은 빼지 않음) n일 뒤(음수면 앞)"""
    d, step, left = start, (1 if n >= 0 else -1), abs(n)
    while left:
        d += timedelta(days=step)
        if d.weekday() < 5:
            left -= 1
    return d


def business_days_between(a, b):
    """a 다음 날부터 b까지의 평일 수 (a > b면 음수)"""
    if a == b:
        return 0
    lo, hi, sign = (a, b, 1) if a < b else (b, a, -1)
    n, d = 0, lo
    while d < hi:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n * sign


EN_UNITS = (("trillion", 1e12), ("billion", 1e9), ("million", 1e6), ("thousand", 1e3))
EN_ALIASES = {"tn": "trillion", "trn": "trillion", "t": "trillion", "bn": "billion", "b": "billion", "bil": "billion",
              "mn": "million", "m": "million", "mil": "million", "k": "thousand"}


def kor_number(n):
    """숫자 -> 한글 단위 표기: 1조2345억 / 35억 / 3500만 / 1,234 (원 없이)"""
    if n is None:
        return "-"
    sign = "-" if n < 0 else ""
    w = abs(n)
    parts = []
    for word, mult in (("조", 1e12), ("억", 1e8), ("만", 1e4)):
        q = int(w // mult)
        if q:
            parts.append(f"{q}{word}")
            w -= q * mult
    rest = round(w, 2)
    if rest:
        parts.append(f"{rest:.2f}".rstrip("0").rstrip(".") if not parts else str(int(round(rest))))
    return sign + ("".join(parts) if parts else "0")


def en_number(n):
    """숫자 -> 영어 단위 표기 [(표기)]: 큰 단위부터 1 이상 되는 것 두 가지 (예: 1.2345 trillion / 1,234.5 billion)"""
    if n is None:
        return []
    out = []
    for word, mult in EN_UNITS:
        v = n / mult
        if abs(v) >= 1:
            out.append(f"{v:.4f}".rstrip("0").rstrip(".") + f" {word}")
            if len(out) == 2:
                break
    return out or [f"{n:.2f}".rstrip("0").rstrip(".")]


def parse_en(text):
    """'3.5 billion' / '$3.5bn' / '350 million' / '1.2 trillion won' / '12k' -> 숫자. 못 읽으면 None"""
    s = str(text or "").strip().lower().replace("$", "").replace("us", "").replace("won", "").replace("dollars", "").replace("dollar", "")
    m = re.match(r"^\s*(-?[\d,]*\.?\d+)\s*([a-z]*)\s*$", s)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    word = EN_ALIASES.get(m.group(2), m.group(2))
    if not word:
        return v
    mult = dict(EN_UNITS).get(word)
    return v * mult if mult else None


def table_change(text):
    """여러 줄 '항목 작년값 올해값' (탭·공백·쉼표 구분, 엑셀 두 열 붙여넣기도 됨) -> [{name, a, b, diff, rate, status, share_a, share_b, contrib}], 합계 dict.
    contrib = 전체 증감액 가운데 그 항목 증감액의 몫(%). 전체 증감이 0이면 None"""
    rows = []
    for line in str(text or "").splitlines():
        parts = [p for p in re.split(r"[\t,]+|\s{1,}", line.strip()) if p]
        if len(parts) < 2:
            continue
        b, bp = parse_num(parts[-1])
        a, ap = parse_num(parts[-2])
        if a is None or b is None:
            continue
        name = " ".join(parts[:-2]) or f"{len(rows) + 1}번"
        rows.append({"name": name, "a": a, "b": b})
    if not rows:
        return [], None
    ta, tb = sum(r["a"] for r in rows), sum(r["b"] for r in rows)
    tdiff = tb - ta
    for r in rows:
        c = change(r["a"], r["b"])
        r.update(c)
        r["share_a"] = r["a"] / ta * 100 if ta else None
        r["share_b"] = r["b"] / tb * 100 if tb else None
        r["contrib"] = r["diff"] / tdiff * 100 if tdiff else None
    total = {"name": "합계", "a": ta, "b": tb, **change(ta, tb), "share_a": 100.0 if ta else None, "share_b": 100.0 if tb else None,
             "contrib": 100.0 if tdiff else None}
    return rows, total
