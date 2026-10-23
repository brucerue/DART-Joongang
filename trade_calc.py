"""수출 탭의 계산 (화면 없음). trade.json(trade_data.py가 만든 자료)을 받아 증감률·누계·비중·10일 단위 비교를 계산한다.

trade.json: {"updated", "months": ["2025-01", ...],
  "total": {"exp": [...], "imp": [...], "bal": [...], "expCnt": [...], "impCnt": [...]},   (달러)
  "country"/"region": {코드: [이름, [수출...], [수입...]]},  "sido"/"port": {이름: [[수출...], [수입...]]},   (달러, 없는 달은 None)
  "tenday": {"202609": {"exp_item": {"01~10": [합계, 1위..10위], ...}, "exp_cnty": ..., "imp_item": ..., "imp_cnty": ...}}}   (천달러)
"""


def pct(cur, prev):
    """증감률(%) 또는 None (이전 값이 없거나 0이면)"""
    if cur is None or prev in (None, 0):
        return None
    return (cur / prev - 1) * 100


def yoy(series, i):
    """달 i의 전년 같은 달 대비 증감률(%)"""
    return pct(series[i], series[i - 12]) if i >= 12 else None


def year_of(months, i):
    return months[i][:4]


def ytd_pair(months, series, i):
    """달 i까지의 연초 이후 누계와 전년 같은 기간 누계 -> (올해, 전년 또는 None)"""
    year = year_of(months, i)
    cur_idx = [k for k in range(i + 1) if year_of(months, k) == year]
    if not cur_idx:
        return None, None
    cur_vals = [series[k] for k in cur_idx]
    if any(v is None for v in cur_vals):
        return None, None
    prev_idx = [k - 12 for k in cur_idx]
    prev = None
    if all(k >= 0 for k in prev_idx):
        pv = [series[k] for k in prev_idx]
        prev = None if any(v is None for v in pv) else sum(pv)
    return sum(cur_vals), prev


def group_table(group, months, i, kind="exp"):
    """국가·경제권 표 -> [{code, name, exp, imp, bal, exp_yoy, imp_yoy, share}] 수출이 큰 순.
    group: {코드: [이름, 수출들, 수입들]}. share는 그 달 목록 전체 수출 합 대비 비율(%) — 경제권은 서로 겹치므로 쓰지 않는다."""
    rows = []
    for code, (name, exp, imp) in group.items():
        e, m = exp[i], imp[i]
        if e is None and m is None:
            continue
        e0, m0 = e or 0, m or 0
        rows.append({"code": code, "name": name, "exp": e0, "imp": m0, "bal": e0 - m0, "exp_yoy": yoy(exp, i) if e else None, "imp_yoy": yoy(imp, i) if m else None})
    rows.sort(key=lambda r: -r["exp"] if kind == "exp" else -r["imp"])
    return rows


def region_table(group, months, i):
    """시도·세관 표 -> [{name, exp, imp, bal, exp_yoy, imp_yoy}] 수출이 큰 순. group: {이름: [[수출들], [수입들]]}"""
    rows = []
    for name, (exp, imp) in group.items():
        e, m = exp[i], imp[i]
        if not e and not m:
            continue
        e0, m0 = e or 0, m or 0
        rows.append({"name": name, "exp": e0, "imp": m0, "bal": e0 - m0, "exp_yoy": yoy(exp, i) if e else None, "imp_yoy": yoy(imp, i) if m else None})
    rows.sort(key=lambda r: -r["exp"])
    return rows


def prev_year_month(ym):
    """'202609' -> '202509'"""
    return f"{int(ym[:4]) - 1}{ym[4:]}"


def is_full(period):
    """'01~30'·'01~31'·'01~28' 같은 월말까지의 누계인지 ('01~10', '01~20'은 아님)"""
    return period[3:] >= "28"


def match_prev(prev_all, period):
    """전년 같은 달의 같은 기간 값(목록). 말일이 다른 해(01~30 ↔ 01~31)는 월말 누계끼리 짝짓는다. 없으면 None"""
    if period in prev_all:
        return prev_all[period]
    if is_full(period):
        fulls = [k for k in sorted(prev_all) if is_full(k)]
        if fulls:
            return prev_all[fulls[-1]]
    return None


def tenday_rows(data, ym, kind):
    """10일 단위 누계 비교 -> [{period, cur, prev, yoy}] (천달러, 합계 기준). 전년 자료가 없으면 prev·yoy가 None"""
    ten = data.get("tenday", {})
    cur = (ten.get(ym) or {}).get(kind) or {}
    prev_all = (ten.get(prev_year_month(ym)) or {}).get(kind) or {}
    out = []
    for period in sorted(cur):
        c = cur[period][0]
        prev = match_prev(prev_all, period)
        p = prev[0] if prev else None
        out.append({"period": period, "cur": c, "prev": p, "yoy": pct(c, p)})
    return out


def tenday_items(data, ym, kind, period):
    """한 누계 기간의 1~10번째 항목 금액과 전년 같은 기간 비교 -> [{rank, cur, prev, yoy}] (천달러). 항목 이름은 순번 고정(TRADE_RANK_NAMES)"""
    ten = data.get("tenday", {})
    cur = ((ten.get(ym) or {}).get(kind) or {}).get(period)
    if not cur:
        return []
    prev = match_prev((ten.get(prev_year_month(ym)) or {}).get(kind) or {}, period)
    out = []
    for rank in range(1, 11):
        c = cur[rank]
        p = prev[rank] if prev else None
        out.append({"rank": rank, "cur": c, "prev": p, "yoy": pct(c, p)})
    return out
