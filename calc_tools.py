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


KOR_UNIT_MULT = (("조", 1e12), ("억", 1e8), ("만", 1e4))
_SUM_UNIT_RE = re.compile(r"[^\d\s.,%조억만천)]+$")  # 숫자 뒤 단위 글자(달러·원·대·명 …)


def _sum_item(raw):
    """한 칸 -> (값, 단위 글자, 쓴 한글 단위(조·억·만 중 가장 큰 것 또는 ''), 소수 자릿수(그 한글 단위 기준)) 또는 None"""
    s = str(raw).strip().replace(" ", "")
    if not s:
        return None
    prefix = ""
    if s[0] in "$₩€¥£":
        prefix, s = s[0], s[1:]
    m = _SUM_UNIT_RE.search(s)
    unit = m.group(0) if m else ""
    if unit:
        s = s[: m.start()]
    if unit.startswith("원") or unit == "원":
        s += "원"  # parse_num이 '원'을 알아서 지운다
    s = re.sub(r"(\d+(?:\.\d+)?)천(?=[조억만]|$)", lambda x: str(float(x.group(1)) * 1000).rstrip("0").rstrip("."), s)  # 5천억 -> 5000억
    v, pct = parse_num(s)
    if v is None:
        return None
    big = next((w for w, _ in KOR_UNIT_MULT if w in s), "")
    dec = 0
    if big and re.fullmatch(r"[△▲\-−(]*[\d,]*\.?\d+" + big + r"\)?", s.replace("원", "")):  # '-0.8억'처럼 한 단위로만 쓴 값은 그 자릿수 그대로
        mm = re.search(r"\.(\d+)", s)
        dec = len(mm.group(1)) if mm else 0
    elif not big:
        mm = re.search(r"\.(\d+)", s)
        dec = len(mm.group(1)) if mm else 0
    if pct:
        unit = "%" + unit
    return v, prefix + "|" + unit, big, dec


def _one_korean_number(parts):
    """['1조', '2,345억', '6789만']처럼 큰 단위부터 이어 쓴 조각이면 한 숫자"""
    order = [next((i for i, (w, _) in enumerate(KOR_UNIT_MULT) if re.search(r"\d" + w + r"[^\d]*$", p)), 3) for p in parts]
    return all(a < b for a, b in zip(order, order[1:])) and order[0] < 3


def sum_numbers(text):
    """여러 줄(또는 탭·쌍점 구분)로 붙여 넣은 숫자를 더한다. '20억', '△3,000', '(1,234)', '-0.8억달러'처럼 한글 단위·단위 글자가 붙어도 된다.
    -> {'total', 'count', 'text'(입력과 같은 꼴: 예 -4.2억달러), 'kor'(조·억·만 풀어 쓰기), 'plain'(숫자만), 'units'(쓴 단위 글자들), 'bad'(못 읽은 칸)}"""
    items, bad = [], []
    for line in re.split(r"[\n\r;]+", str(text or "")):
        line = line.strip()
        if not line:
            continue
        parts = [p for p in re.split(r"[\t ]+", line) if p]  # 한 줄에 여러 숫자(엑셀 가로 복사 등). '1조 2,345억'처럼 큰 단위부터 띄어 쓴 건 한 숫자
        got = [_sum_item(p) for p in parts]
        if len(parts) > 1 and all(g is not None for g in got) and not _one_korean_number(parts):
            items += got
            continue
        one = _sum_item(line)
        if one is not None:
            items.append(one)
        else:
            bad.append(line)
    if not items:
        return None
    total = sum(v for v, _, _, _ in items)
    units = list(dict.fromkeys(u for _, u, _, _ in items))
    prefix, unit = (units[0].split("|", 1) if len(units) == 1 else ("", ""))
    bigs = [b for _, _, b, _ in items if b]
    big = max(bigs, key=lambda w: dict(KOR_UNIT_MULT)[w]) if bigs else ""
    dec = max(d for _, _, b, d in items if b == big) if bigs else max(d for _, _, _, d in items)
    shown = total / dict(KOR_UNIT_MULT)[big] if big else total
    if big and abs(round(shown, dec) - shown) > 1e-9 * max(1, abs(shown)):  # 그 단위로 딱 떨어지지 않으면(예 25억1234만) 풀어 쓴다
        prefix_k = "-" if total < 0 else ""
        text_out = f"{prefix_k}{prefix}{kor_number(abs(total))}{unit.lstrip('%')}"
        return {"total": total, "count": len(items), "text": text_out, "kor": None, "plain": num_text(total),
                "units": [u.replace("|", "") for u in units if u != "|"] if len(units) > 1 else [], "bad": bad}
    num = f"{shown:.{dec}f}" if dec else str(int(round(shown)))
    if num.startswith("-") and float(num) == 0:
        num = num[1:]
    sign = "-" if num.startswith("-") else ""
    pct = unit.startswith("%")
    tail = unit[1:] if pct else unit
    text_out = f"{sign}{prefix}{num.lstrip('-')}{big}{'%' if pct else ''}{tail}"
    return {"total": total, "count": len(items), "text": text_out, "kor": kor_number(total) + tail if big else None,
            "plain": num_text(total, max(dec, 0) or 1) if not big else num_text(total), "units": [u.replace("|", "") for u in units if u != "|"] if len(units) > 1 else [],
            "bad": bad}
