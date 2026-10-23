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


# ---- 검색 (품목별 국가별 수출입실적 API로 품목·국가 조합을 바로 조회) ----
ITEM_API_PATH = "nitemtrade/getNitemtradeList"
MAX_CHUNK_MONTHS = 12  # API는 한 번에 1년 이내만 조회된다


class TradeApiError(Exception):
    """관세청 API 오류 (사용자에게 알릴 글을 담는다)"""


def month_add(ym, n):
    """'202609' + n개월 -> 'YYYYMM'"""
    y, m = int(ym[:4]), int(ym[4:])
    t = y * 12 + (m - 1) + n
    return f"{t // 12}{t % 12 + 1:02d}"


def month_span(start, end):
    """시작~끝 월 수 (포함)"""
    return (int(end[:4]) - int(start[:4])) * 12 + int(end[4:]) - int(start[4:]) + 1


def chunk_months(start, end):
    """시작~끝 월을 12개월 이하 구간들로 나눈다 -> [(시작, 끝)]"""
    out, cur = [], start
    while cur <= end:
        last = min(month_add(cur, MAX_CHUNK_MONTHS - 1), end)
        out.append((cur, last))
        cur = month_add(last, 1)
    return out


def _to_int(text):
    try:
        return int(float(str(text).replace(",", "") or 0))
    except ValueError:
        return 0


def parse_items(xml_text):
    """API 응답(XML 글) -> [{ym, hs, name, cnty, cnty_name, exp, imp, exp_wgt, imp_wgt}] (기간 합계 '총계' 행은 뺀다).
    오류 응답이면 TradeApiError, 자료가 없으면 []."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise TradeApiError("관세청 응답을 읽지 못했어요.") from e
    code = root.findtext(".//resultCode")
    msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or ""
    if code not in (None, "00"):
        raise TradeApiError(f"관세청 API: {msg}".strip())
    if code is None and msg:  # 포털 쪽 오류(서비스키 등)
        raise TradeApiError(f"관세청 API: {msg}")
    out = []
    for it in root.findall(".//item"):
        row = {c.tag: (c.text or "").strip() for c in it}
        ym = row.get("year", "")
        if len(ym) != 7 or ym[4] != ".":  # '총계' 등
            continue
        out.append({"ym": ym.replace(".", "-"), "hs": row.get("hsCd", ""), "name": row.get("statKor", ""), "cnty": row.get("statCd", ""),
                    "cnty_name": row.get("statCdCntnKor1", ""), "exp": _to_int(row.get("expDlr")), "imp": _to_int(row.get("impDlr")),
                    "exp_wgt": _to_int(row.get("expWgt")), "imp_wgt": _to_int(row.get("impWgt"))})
    return out


def query_items(fetch, hs, cnty, start, end):
    """품목·국가 조건으로 월별 행을 모두 받는다. fetch(params dict) -> 응답 글. 1년 넘는 기간은 나눠서 받는다.
    hs: 2·4·6자리 코드 또는 None, cnty: 2글자 국가코드 또는 None (둘 중 하나는 있어야 함)"""
    if not hs and not cnty:
        raise TradeApiError("품목이나 국가 중 하나는 골라야 해요.")
    rows = []
    for s, e in chunk_months(start, end):
        params = {"strtYymm": s, "endYymm": e}
        if hs:
            params["hsSgn"] = hs
        if cnty:
            params["cntyCd"] = cnty
        rows += parse_items(fetch(params))
    return rows


def metrics_by_month(rows):
    """행들 -> {ym: {exp, imp, bal, exp_wgt, imp_wgt}} (같은 달 행을 모두 더함)"""
    out = {}
    for r in rows:
        m = out.setdefault(r["ym"], {"exp": 0, "imp": 0, "exp_wgt": 0, "imp_wgt": 0})
        for k in ("exp", "imp", "exp_wgt", "imp_wgt"):
            m[k] += r[k]
    for m in out.values():
        m["bal"] = m["exp"] - m["imp"]
    return out


def totals(by_month):
    """월별 -> 기간 합계"""
    t = {"exp": 0, "imp": 0, "exp_wgt": 0, "imp_wgt": 0}
    for m in by_month.values():
        for k in t:
            t[k] += m[k]
    t["bal"] = t["exp"] - t["imp"]
    return t


def group_rows(rows, key):
    """기간 전체를 key('hs' 하위 품목 / 'cnty' 국가)별로 더해 수출이 큰 순 -> [{code, name, exp, imp, bal}]"""
    acc = {}
    for r in rows:
        code = r[key]
        a = acc.setdefault(code, {"code": code, "name": r["name"] if key == "hs" else r["cnty_name"], "exp": 0, "imp": 0})
        a["exp"] += r["exp"]
        a["imp"] += r["imp"]
    out = list(acc.values())
    for a in out:
        a["bal"] = a["exp"] - a["imp"]
    out.sort(key=lambda a: (-a["exp"], -a["imp"]))
    return out


def stored_metrics(arrays, months, start, end):
    """trade.json의 월별 배열 -> {ym: {exp, imp, bal}} (시작~끝 'YYYY-MM' 포함). arrays: (수출 배열, 수입 배열), 없는 달은 건너뜀"""
    exp, imp = arrays
    out = {}
    for i, ym in enumerate(months):
        if start <= ym <= end and (exp[i] is not None or imp[i] is not None):
            e, m = exp[i] or 0, imp[i] or 0
            out[ym] = {"exp": e, "imp": m, "bal": e - m, "exp_wgt": 0, "imp_wgt": 0}
    return out


def sum_metrics(parts):
    """여러 {ym: 지표} 를 달별로 더한다"""
    out = {}
    for p in parts:
        for ym, m in p.items():
            a = out.setdefault(ym, {"exp": 0, "imp": 0, "bal": 0, "exp_wgt": 0, "imp_wgt": 0})
            for k in a:
                a[k] += m.get(k, 0)
    return out


# ---- 산업부 품목(MTI) 검색: HSK-MTI 연계표(mti_map.json)로 HS 10자리를 MTI로 묶어 합친다 ----
def load_mti(path):
    """mti_map.json -> {'tables': [{label, from, to, mti: {MTI6: [HSK]}, names, hsk_to_mti}], 'names': {코드: 이름}} (없으면 None)"""
    import json

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    for t in data.get("tables", []):
        t["hsk_to_mti"] = {h: m for m, hs in t["mti"].items() for h in hs}
    return data if data.get("tables") and data.get("names") else None


def mti_name(mti, code):
    """MTI 코드(1~6자리) -> 이름. 모르면 코드 그대로"""
    if code in mti["names"]:
        return mti["names"][code]
    for t in reversed(mti["tables"]):
        if code in t["names"]:
            return t["names"][code]
    return code


def mti_table_for_year(mti, year):
    """그 해의 연계표 (범위 밖이면 가장 가까운 표)"""
    tables = mti["tables"]
    for t in tables:
        if t["from"] <= year <= t["to"]:
            return t
    return tables[0] if year < tables[0]["from"] else tables[-1]


def mti_segments(mti, start, end):
    """시작~끝 'YYYYMM'을 연계표 기준년도 경계에서 나눈다 -> [(표, 시작, 끝)]"""
    out, cur = [], start
    while cur <= end:
        t = mti_table_for_year(mti, int(cur[:4]))
        last_year = t["to"] if t is not mti["tables"][-1] else 9999
        seg_end = min(end, f"{last_year}12")
        out.append((t, cur, seg_end))
        cur = month_add(seg_end, 1)
    return out


def mti_hsk(table, prefix):
    """MTI 코드(앞자리 prefix) 아래 묶인 HS 10자리 집합"""
    out = set()
    for m, hs in table["mti"].items():
        if m.startswith(prefix):
            out.update(hs)
    return out


def next_level(code):
    """한 단계 아래 단위의 자릿수 (이름이 있는 단위: 1·2·3·4·6)"""
    return {1: 2, 2: 3, 3: 4, 4: 6, 5: 6}.get(len(code))


def query_mti(fetch, mti, prefix, cnty, start, end, workers=6):
    """MTI 품목(prefix 이하)의 월별 행. 묶인 HS 10자리의 HS 6자리마다 조회해서 그 10자리 행만 남기고, 각 행에 'mti'(6자리)를 붙인다.
    fetch는 여러 스레드에서 불린다. 묶인 코드가 없는 기간이 있으면 skipped에 그 기간을 모아 함께 돌려준다 -> (행들, [건너뛴 기간])"""
    from concurrent.futures import ThreadPoolExecutor

    jobs, skipped = [], []
    for table, s, e in mti_segments(mti, start, end):
        hsk = mti_hsk(table, prefix)
        if not hsk:
            skipped.append(f"{s[:4]}.{s[4:]}~{e[:4]}.{e[4:]}")
            continue
        for h6 in sorted({h[:6] for h in hsk}):
            jobs.append((table, hsk, h6, s, e))

    def one(job):
        table, hsk, h6, s, e = job
        out = []
        for r in query_items(fetch, h6, cnty, s, e):
            if r["hs"] in hsk:
                out.append(dict(r, mti=table["hsk_to_mti"][r["hs"]]))
        return out

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(one, jobs):
            rows += part
    return rows, skipped


def group_mti(rows, mti, level):
    """행들을 MTI level자리로 묶어 수출이 큰 순 -> [{code, name, exp, imp, bal}]"""
    acc = {}
    for r in rows:
        code = r["mti"][:level]
        a = acc.setdefault(code, {"code": code, "name": mti_name(mti, code), "exp": 0, "imp": 0})
        a["exp"] += r["exp"]
        a["imp"] += r["imp"]
    out = list(acc.values())
    for a in out:
        a["bal"] = a["exp"] - a["imp"]
    out.sort(key=lambda a: (-a["exp"], -a["imp"]))
    return out
