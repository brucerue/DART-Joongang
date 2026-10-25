"""AI 검색(재무·공시·정기보고서) 공통 계산 (화면 없음).

AI는 질문을 '조회 조건'으로 바꾸거나 원문에서 '그대로 옮긴 글'을 고르기만 한다.
여기 함수들은 AI가 준 조건을 허용된 값으로 걸러내고, AI가 옮겼다는 글·숫자가 원문에 정말 있는지 확인한다.
"""
import re
from datetime import date, datetime, timedelta


def pick_known(values, allowed, limit=None):
    """AI가 준 목록 중 허용된 값만, 순서 유지·중복 제거"""
    out = []
    for v in values or []:
        v = str(v).strip()
        if v in allowed and v not in out:
            out.append(v)
    return out[:limit] if limit else out


def clean_names(values, limit):
    """회사명 목록: 공백 정리, 빈 값·중복 제거, 최대 limit개"""
    out = []
    for v in values or []:
        v = re.sub(r"\s+", " ", str(v)).strip()
        if v and v not in out:
            out.append(v)
    return out[:limit]


def year_range(start, end, lo, hi, default_end, default_span, max_span):
    """AI가 준 연도 -> (시작, 끝). 이상하면 기본값(default_end에서 default_span년), 너무 길면 끝에서 max_span년만"""
    def as_year(v):
        try:
            y = int(str(v).strip()[:4])
        except (TypeError, ValueError):
            return None
        return y if lo <= y <= hi else None

    s, e = as_year(start), as_year(end)
    if e is None:
        e = default_end
    if s is None:
        s = e - default_span + 1
    if s > e:
        s, e = e, s
    s = max(s, lo, e - max_span + 1)
    return s, e


def as_date(v):
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def date_range(start, end, today, default_days=30, max_days=366):
    """AI가 준 날짜 -> (시작, 끝). 끝은 오늘을 넘지 않고, 기간은 max_days까지"""
    e = as_date(end) or today
    e = min(e, today)
    s = as_date(start) or (e - timedelta(days=default_days - 1))
    if s > e:
        s, e = e, s
    s = max(s, e - timedelta(days=max_days - 1))
    return s, e


def squash(text):
    """비교용: 공백·표 구분자를 모두 없앤 글"""
    return re.sub(r"[\s|]+", "", str(text or ""))


def quote_in(quote, text):
    """AI가 '원문 그대로' 옮겼다는 글이 원문에 있는지 (공백·줄바꿈·표 구분자 차이는 무시).
    여러 줄이면 줄마다 확인해 모두 있어야 True"""
    body = squash(text)
    lines = [squash(l) for l in str(quote or "").split("\n")]
    lines = [l for l in lines if l]
    return bool(lines) and all(l in body for l in lines)


def value_in(value, text):
    """뽑은 값이 원문에 있는지. 숫자가 있으면 숫자 덩어리(쉼표 포함)가 원문에 그대로 있어야 한다"""
    v = str(value or "").strip()
    if not v:
        return False
    body = squash(text)
    if squash(v) in body:
        return True
    nums = re.findall(r"\d[\d,.]*\d|\d", v)
    return bool(nums) and all(n in body for n in nums) and len("".join(nums)) >= 3


UNIT_WORDS = (("조원", 1e12), ("억원", 1e8), ("백만원", 1e6), ("천원", 1e3), ("만원", 1e4), ("원", 1))


def parse_won(value):
    """'123,456,789,000' / '1조2,345억원' / '3,000억원' / '1,234백만원' -> 원 단위 숫자. 못 읽으면 None.
    달러 등 원화가 아닌 금액은 None (합계에 섞지 않는다)"""
    v = str(value or "").replace(" ", "")
    if not v or re.search(r"USD|US\$|\$|달러|EUR|유로|엔|JPY|위안|CNY", v, re.I):
        return None
    total, found = 0.0, False
    for word, mult in (("조", 1e12), ("억", 1e8)):
        m = re.search(r"([\d,.]+)" + word, v)
        if m:
            try:
                total += float(m.group(1).replace(",", "")) * mult
                found = True
            except ValueError:
                return None
            v = v.replace(m.group(0), "", 1)
    if found:
        rest = re.match(r"([\d,]+)만", v)
        if rest:
            total += float(rest.group(1).replace(",", "")) * 1e4
        return total
    m = re.match(r"^([\d,]+(?:\.\d+)?)(백만원|천원|만원|원)?$", v)
    if not m:
        return None
    mult = dict(UNIT_WORDS).get(m.group(2) or "원", 1)
    try:
        return float(m.group(1).replace(",", "")) * mult
    except ValueError:
        return None


def match_report(name, keywords):
    """보고서명(공백·괄호 무시)에 낱말 중 하나라도 들어 있는지"""
    key = re.sub(r"[\s·ㆍ()\[\]]", "", str(name).lower())
    return any(re.sub(r"[\s·ㆍ()\[\]]", "", str(k).lower()) in key for k in keywords if str(k).strip())


def pick_report(rows, kind, year, period_of):
    """정기보고서 목록(최신순)에서 종류·연도로 하나 고르기. 조건에 맞는 게 없으면 맨 앞(최신)"""
    def ok(r):
        name = str(r.get("report_nm", ""))
        if kind and kind != "최신" and kind not in name:
            return False
        if year:
            per = period_of(name)
            if not per or per[0] != int(year):
                return False
        return True

    for r in rows:
        if ok(r):
            return r, True
    return (rows[0], False) if rows else (None, False)


def quote_context(quote, text, before=12, after=4, cap=40):
    """AI가 고른 발췌의 앞뒤 원문 줄 -> [(원문 줄, AI가 고른 줄인지)]. 표가 칸마다 한 줄로 풀려 있어도 머리글·행 이름이 함께 보이게.
    원문에서 위치를 못 찾으면 빈 목록"""
    lines = str(text or "").split("\n")
    keys = [squash(l) for l in lines]
    want = [squash(q) for q in str(quote or "").split("\n")]
    want = [w for w in want if w]
    hit, pos = [], 0
    for w in want:
        for i in range(pos, len(lines)):
            if keys[i] and (w in keys[i] or (len(keys[i]) >= 2 and keys[i] in w)):
                hit.append(i)
                pos = i + 1
                break
    if not hit:
        return []
    lo, hi = max(0, hit[0] - before), min(len(lines), hit[-1] + after + 1)
    if hi - lo > cap:  # 너무 길면 고른 줄 주변만
        lo, hi = max(0, hit[0] - before), min(len(lines), max(hit[0] - before, 0) + cap)
    marked = set(hit)
    return [(lines[i].rstrip(), i in marked) for i in range(lo, hi) if lines[i].strip()]


def context_parts(ctx):
    """quote_context 결과 -> 화면용 덩어리 [("text", 글, 표시) | ("table", [(칸 목록, 표시)])].
    ' | '로 칸이 나뉜 줄이 이어지면 표 하나로 묶는다"""
    parts = []
    for line, marked in ctx:
        if "|" in line:
            cells = [c.strip() for c in line.split("|")]
            if cells and cells[-1] == "":
                cells = cells[:-1]
            if parts and parts[-1][0] == "table":
                parts[-1][1].append((cells, marked))
            else:
                parts.append(("table", [(cells, marked)]))
        else:
            parts.append(("text", line.strip(), marked))
    return parts
