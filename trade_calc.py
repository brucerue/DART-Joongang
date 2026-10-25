"""수출 탭의 계산 (화면 없음). trade.json(trade_data.py가 만든 자료)을 받아 증감률·누계·비중·10일 단위 비교를 계산한다.

trade.json: {"updated", "months": ["2025-01", ...],
  "total": {"exp": [...], "imp": [...], "bal": [...], "expCnt": [...], "impCnt": [...]},   (달러)
  "country"/"region": {코드: [이름, [수출...], [수입...]]},  "sido"/"port": {이름: [[수출...], [수입...]]},   (달러, 없는 달은 None)
  "tenday": {"202609": {"exp_item": {"01~10": [합계, 1위..10위], ...}, "exp_cnty": ..., "imp_item": ..., "imp_cnty": ...}}}   (천달러)
"""
import re


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


# 산업부 '20대 주력 수출품목'을 MTI 코드로 옮긴 것 (포함할 코드 앞자리들, 뺄 코드 앞자리들).
# 산업부 2026년 9월 수출입 동향 보도자료의 품목별 수출액과 맞춰 확인함(반올림 오차 이내). 일반기계·석유제품·농수산식품은 아래 설명대로 맞춘 구성.
MTI_GROUPS = {
    "반도체": (("831",), ()), "디스플레이": (("8371",), ()), "무선통신기기": (("812",), ()), "컴퓨터": (("813",), ()),
    "자동차": (("741",), ()), "자동차부품": (("742",), ()), "선박": (("746",), ()),
    "석유제품": (("133",), ()),  # 산업부 개편 보도자료(2026.5.6) 기준 MTI 133 전체(나프타 포함)
    "석유화학": (("21",), ()), "이차전지": (("8361",), ()),
    "일반기계": (("71", "72", "73", "75", "76"), ("732", "736")),  # 기계류에서 수송기계·반도체/디스플레이 제조용 장비 제외 (40.7억달러에 근접)
    "전기기기": (("84",), ()), "철강": (("61",), ()), "비철금속": (("62",), ()),
    "농수산식품": (("0",), ()), "화장품": (("2273",), ()), "생활용품": (("5",), ()), "섬유": (("4",), ()), "가전": (("82",), ()), "바이오헬스": (("93",), ()),
}
GROUP_PREFIX = "@"


def mti_spec(key):
    """검색 항목(코드 '831110' 또는 주력품목 '@반도체') -> (포함 앞자리들, 뺄 앞자리들)"""
    if key.startswith(GROUP_PREFIX):
        return MTI_GROUPS[key[len(GROUP_PREFIX):]]
    return (key,), ()


def mti_label(mti, key):
    """검색 항목 이름 ('반도체' 또는 '831110 D램')"""
    return key[len(GROUP_PREFIX):] if key.startswith(GROUP_PREFIX) else f"{key} {mti_name(mti, key)}"


def mti_group_level(key):
    """내역 표에서 묶을 MTI 자릿수: 코드 하나면 한 단계 아래, 주력품목이면 그 구성의 한 단계 아래(여러 개면 3자리)"""
    include, _ = mti_spec(key)
    if len(include) == 1:
        return next_level(include[0])
    return 3


def mti_hsk(table, include, exclude=()):
    """MTI 코드(앞자리 include 아래, exclude 아래는 뺌)에 묶인 HS 10자리 집합. include·exclude는 글자 하나 또는 글자 목록"""
    inc = (include,) if isinstance(include, str) else tuple(include)
    exc = (exclude,) if isinstance(exclude, str) else tuple(exclude)
    out = set()
    for m, hs in table["mti"].items():
        if m.startswith(inc) and not (exc and m.startswith(exc)):
            out.update(hs)
    return out


def next_level(code):
    """한 단계 아래 단위의 자릿수 (이름이 있는 단위: 1·2·3·4·6)"""
    return {1: 2, 2: 3, 3: 4, 4: 6, 5: 6}.get(len(code))


def mti_plan(table, hsk):
    """조회 계획 -> (hs4_jobs {HS4: {HS6: MTI}}, hs6_jobs {HS6: 묶인 HS10 집합}).
    한 HS6의 HS10이 모두 묶음 안에 있고 MTI가 하나뿐이면(단순) HS 4자리로 한꺼번에 조회해 HS6 행으로 받고(호출이 훨씬 적음),
    그렇지 않거나 한 HS4에 단순 HS6가 하나뿐이면 HS 6자리로 조회해 HS10 행을 받아 거른다."""
    by6 = table.get("by_hs6")
    if by6 is None:
        by6 = {}
        for h in table["hsk_to_mti"]:
            by6.setdefault(h[:6], []).append(h)
        table["by_hs6"] = by6
    simple, other = {}, {}
    for h6 in {h[:6] for h in hsk}:
        children = by6[h6]
        inside = {c for c in children if c in hsk}
        mtis = {table["hsk_to_mti"][c] for c in children}
        if len(inside) == len(children) and len(mtis) == 1:
            simple[h6] = next(iter(mtis))
        else:
            other[h6] = inside
    by4 = {}
    for h6, m in simple.items():
        by4.setdefault(h6[:4], {})[h6] = m
    hs4_jobs = {}
    for h4, d in by4.items():
        if len(d) >= 2:
            hs4_jobs[h4] = d
        else:
            for h6 in d:
                other[h6] = {c for c in by6[h6]}
    return hs4_jobs, other


def query_mti(fetch, mti, key, cnty, start, end, workers=8):
    """MTI 품목(코드 key 이하, 또는 '@주력품목')의 월별 행 -> (행들, [건너뛴 기간]). 각 행에 'mti'(6자리)를 붙인다.
    HS 4자리·6자리로 나눠 조회하고(mti_plan) 묶음에 든 행만 남긴다. 연계표에 이 코드가 없는 기간은 건너뛴 기간으로 알린다. fetch는 여러 스레드에서 불린다."""
    from concurrent.futures import ThreadPoolExecutor

    jobs, skipped = [], []
    include, exclude = mti_spec(key)
    for table, s, e in mti_segments(mti, start, end):
        hsk = mti_hsk(table, include, exclude)
        if not hsk:
            skipped.append(f"{s[:4]}.{s[4:]}~{e[:4]}.{e[4:]}")
            continue
        hs4_jobs, hs6_jobs = mti_plan(table, hsk)
        for h4, d in hs4_jobs.items():
            jobs.append(("4", table, h4, d, s, e))
        for h6, inside in hs6_jobs.items():
            jobs.append(("6", table, h6, inside, s, e))

    def one(job):
        kind, table, code, info, s, e = job
        out = []
        for r in query_items(fetch, code, cnty, s, e):
            if kind == "4":
                m = info.get(r["hs"])  # HS4 조회 행은 HS6 단위
                if m:
                    out.append(dict(r, mti=m))
            elif r["hs"] in info:
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


# ---- 산업부 보도자료 잠정치(flash.json)와 월별 추이 표 ----
FLASH_REGION_ALIAS = {"EU": "EU(27)", "ASEAN": "아세안", "CIS": "CIS"}  # 관세청 경제권 이름 -> 보도자료 지역 이름


def flash_new(flash, months):
    """flash.json 중 가장 최근 달이 확정 자료(months)보다 새로우면 그 달의 자료, 아니면 None"""
    if not isinstance(flash, dict) or not flash.get("months") or not months:
        return None
    ym = max(flash["months"])
    fm = flash["months"][ym]
    return fm if ym > months[-1] and isinstance(fm, dict) and fm.get("monthly") else None


def flash_metrics(fm, kind, name):
    """잠정 자료 fm에서 항목의 {'ym', 'cur', 'prev'}(달러; 보도자료는 백만 달러 단위) 또는 None.
    kind: '총괄' / '국가'(보도자료 지역 중 개별 국가) / '경제권' / '품목'(산업부 20대 주력 수출품목, 수출만)"""
    ym = fm["month"]

    def usd(v):
        return None if v is None else v * 1e6

    def pack(cur_e, prev_e, cur_i, prev_i):
        out = {}
        for tag, (e, i) in (("cur", (cur_e, cur_i)), ("prev", (prev_e, prev_i))):
            out[tag] = {"exp": usd(e), "imp": usd(i), "bal": None if e is None or i is None else usd(e - i), "exp_wgt": None, "imp_wgt": None}
        return {"ym": ym, "cur": out["cur"], "prev": out["prev"]}

    if kind == "총괄":
        cur = fm["monthly"].get(ym)
        prev = fm["monthly"].get(f"{int(ym[:4]) - 1}-{ym[5:]}")
        return pack(cur["exp"], prev["exp"], cur["imp"], prev["imp"]) if cur and prev else None
    if kind == "품목":
        v = fm.get("items", {}).get(name)
        return pack(v[-1], v[0], None, None) if v else None
    name = FLASH_REGION_ALIAS.get(name, name) if kind == "경제권" else name
    e, i = fm.get("exp_region", {}).get(name), fm.get("imp_region", {}).get(name)
    return pack(e[-1], e[0], i[-1], i[0]) if e and i else None


def _dash_add(ym, n):
    """'2026-09' + n개월 -> '2026-08' 꼴"""
    t = month_add(ym.replace("-", ""), n)
    return f"{t[:4]}-{t[4:]}"


def _cell(sr, ym, key, base, prov, prev_key=None):
    """한 항목의 한 기간 -> (값, 비교 대상 값). prev_key: 바로 앞 기간 이름을 돌려주는 함수(기본: 앞 달)"""
    before = (prev_key or (lambda k: _dash_add(k, -1)))(ym) if base != "yoy" else None  # 앞 기간은 '직전 기간' 기준일 때만 필요
    if prov:
        fl = sr.get("flash")
        if not fl:
            return None, None
        cur = fl["cur"].get(key)
        if base == "yoy":
            return cur, fl["prev"].get(key)
        return cur, sr["month"].get(before, {}).get(key)
    cur = sr["month"].get(ym, {}).get(key)
    if base == "yoy":
        return cur, ((sr.get("prev") or {}).get(ym) or {}).get(key)
    return cur, sr["month"].get(before, {}).get(key)


def trend_rows(series, months_all, key, base="yoy", flash_ym=None, prev_key=None):
    """월별 추이 표(오래된 달부터) -> (rows, total).
    rows: [{'ym', 'prov'(잠정 여부), 'cells': [(값, 증감) ...]}], total: {'cells': [(합계, 증감) ...]}
    증감: 무역수지(bal)는 증감액(달러), 나머지는 증감률(%). base='yoy' 전년 동월 / 'mom' 전월.
    합계의 증감은 전년 동월 기준일 때만(모든 달에 전년 값이 있어야) 계산한다."""
    rows = []
    sums = [{"cur": 0, "prev": 0, "n": 0, "np": 0} for _ in series]
    for ym in months_all:
        prov = ym == flash_ym
        cells = []
        for k, sr in enumerate(series):
            cur, prev = _cell(sr, ym, key, base, prov, prev_key)
            if cur is None:
                chg = None
            elif key == "bal":
                chg = None if prev is None else cur - prev
            else:
                chg = pct(cur, prev)
            cells.append((cur, chg))
            if cur is not None:
                sums[k]["cur"] += cur
                sums[k]["n"] += 1
                if prev is not None:
                    sums[k]["prev"] += prev
                    sums[k]["np"] += 1
        rows.append({"ym": ym, "prov": prov, "cells": cells})
    total = []
    for s in sums:
        v = s["cur"] if s["n"] else None
        chg = None
        if base == "yoy" and s["n"] and s["n"] == s["np"]:
            chg = s["cur"] - s["prev"] if key == "bal" else pct(s["cur"], s["prev"])
        total.append((v, chg))
    return rows, {"cells": total}


# ---- 경제권 구성국 (품목×경제권 조합용) ----
# 관세청 월별 경제권 합계와 '구성국 국가별 합계'가 최근 24개월 모두 정확히 같아지는 것만 적었다(trade.json으로 검산).
# 구성국이 바뀐 경우는 ZONE_WINDOW로 달을 나눈다: ASEAN의 티모르(TL)는 2026-01부터, EU의 영국(GB)은 2020-12까지(브렉시트 전환기 끝).
# (관세청 월별 경제권 합계와 국가별 합계가 최근 10년 모든 달 정확히 같다. GCC 합계는 2022-01부터만 있어 그 전은 검산하지 못했다.)
ZONES = {
    "EU": "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE GB",
    "EU28": "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE GB",
    "ASEAN": "BN KH ID LA MY MM PH SG TH VN TL",
    "CIS": "RU BY KZ KG TJ UZ AZ AM MD TM UA GE",
    "NAFTA": "US CA MX",
    "EFTA": "CH NO IS LI",
    "MERCOSUR": "AR BR PY UY",
    "GCC": "SA AE KW QA BH OM",
    "APEC": "AU BN CA CL CN HK ID JP MY MX NZ PG PE PH RU SG TW TH US VN",
    "동남아": "BN KH ID LA MY MM PH SG TH VN TW HK",
}


ZONE_WINDOW = {"ASEAN": {"TL": ("2026-01", None)}, "EU": {"GB": (None, "2020-12")}}  # 경제권 -> {국가: (이 달부터, 이 달까지) 구성국}


def in_window(ym, rng):
    """달 ym이 구성국 기간 rng=(시작 또는 None, 끝 또는 None) 안인가"""
    return not ((rng[0] and ym < rng[0]) or (rng[1] and ym > rng[1]))


def zone_codes(name):
    """경제권 이름 -> 구성국 코드 목록 (없으면 [])"""
    return ZONES.get(name, "").split()


def sum_arrays(country, codes, months=None, window=None):
    """trade.json의 국가별 {코드: [이름, 수출[], 수입[]]} 중 codes를 달별로 더한 (수출[], 수입[]) (모두 비어 있는 달은 None).
    window: {코드: (시작월 또는 None, 끝월 또는 None)} 그 기간에만 구성국인 국가 (months: trade.json의 월 목록과 함께 줘야 함)"""
    hit = [(c, country[c]) for c in codes if c in country]
    if not hit:
        return [], []
    n = len(hit[0][1][1])
    exp, imp = [], []
    for i in range(n):
        live = [h for c, h in hit if not (window and months and c in window and not in_window(months[i], window[c]))]
        e = [h[1][i] for h in live if h[1][i] is not None]
        m = [h[2][i] for h in live if h[2][i] is not None]
        exp.append(sum(e) if e else None)
        imp.append(sum(m) if m else None)
    return exp, imp


def filter_rows(rows, codes, window=None):
    """품목 조회 행 중 codes(국가 코드)에 속한 것만 (window: {코드: (시작월, 끝월)} 그 기간에만 구성국)"""
    keep = set(codes)
    return [r for r in rows if r["cnty"] in keep and not (window and r["cnty"] in window and not in_window(r["ym"], window[r["cnty"]]))]


# 대륙 구성국: 관세청 대륙별 월별 합계와 국가별 합계가 최근 10년(119개월) 수출·수입 모두 정확히 같아지도록 맞춘 목록이다(trade.json으로 검산).
# 관세청 분류는 지리와 다른 곳이 있다(예: 북아프리카는 '중동', 튀르키예·조지아는 '유럽', 괌은 '오세아니아'). 국제통화기금(Z1)·해외교포(RK)는 어느 대륙 합계에도 안 들어간다.
CONTINENTS = {
    "아시아": "AF AM AZ BD BN BT CN HK ID IN JP KG KH KZ LA LK MM MN MO MV MY NP PH PK SG TH TJ TL TM TW UZ VN",
    "중동": "AE BH DZ EG IL IQ IR JO KW LB LY MA MR OM QA SA SD SY TN YE",
    "유럽": "AD AL AT AX BA BE BG BY CH CY CZ DE DK EE ER ES FI FO FR GB GE GG GI GR GS HR HU IE IM IS IT JE LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS RU SE SI SJ SK SM TR UA VA",
    "북미": "CA US",
    "중남미": "AG AN AQ AR AW BB BL BM BO BQ BR BS BZ CL CO CR CU CW DM DO EC FK GD GF GL GP GT GY HN HT JM KN KY LC MF MQ MS MX NI PA PE PM PR PY SR SV SX TC TT UY VC VE VG VI",
    "아프리카": "AI AO BF BI BJ BV BW CD CF CG CI CM CV DJ EH ET GA GH GM GN GQ GW IO KE KM LR LS MG ML MU MW MZ NA NE NG PS RE RW SC SH SL SN SO SS ST SZ TD TF TG TZ UG YT ZA ZM ZR ZW",
    "오세아니아": "AU CK FJ GU NC NR NU NZ PF PG SB TK TO WS",
    "대양주": "AS CC CX FM HM KI MH MP NF PN PW TV UM VU WF",
    "기타": "ZZ",
}


def continent_codes(name):
    """대륙 이름 -> 구성국 코드 목록 (없으면 [])"""
    return CONTINENTS.get(name, "").split()


# ---- AI 검색: AI가 돌려준 조건을 검증해 검색 조건으로 바꾼다 ----
AI_METRICS = ["수출금액", "수입금액", "무역수지", "수출 중량", "수입 중량"]
AI_VIEWS = ["월별 추이", "국가별 순위", "품목별 구성"]
AI_VIEW_NAMES = {"trend": "월별 추이", "rank": "국가별 순위", "composition": "품목별 구성", "월별 추이": "월별 추이", "국가별 순위": "국가별 순위", "품목별 구성": "품목별 구성"}
AI_MAX_ITEMS = 6


def ai_local_candidates(text, mti_names, hs_names, limit=20):
    """질문에 들어 있는 낱말(과 3글자 이상 낱말의 두 글자 조각)이 이름에 들어간 MTI·HS 후보 -> ([(코드, 이름)], [(코드, 이름)]).
    AI에게 '실제로 있는 코드' 후보를 보여 주려는 용도라 정확하지 않아도 된다."""
    words = [w for w in re.split(r"[\s,·/()]+", text or "") if len(w) >= 2]
    grams = set(words)
    for w in words:
        if len(w) >= 3:
            grams.update(w[i:i + 2] for i in range(len(w) - 1))

    def rank(names):
        scored = []
        for code, name in names.items():
            score = sum(1 for g in grams if g in name)
            if score:
                scored.append((-score, len(code), code, name))
        scored.sort()
        return [(c, n) for _, _, c, n in scored[:limit]]

    return rank(mti_names), rank(hs_names)


def _ym_ok(v):
    return isinstance(v, str) and re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", v) is not None


def ai_validate(spec, ctx):
    """AI가 돌려준 조건(dict)을 확인해 쓸 수 있는 것만 남긴다. ctx: groups, mti_names, hs_names, countries(이름->코드), zones, continents, months, flash_ym.
    -> {'summary','notes','items':[{kind,code,key,label,reason,confidence,refs}],'regions':[(종류,이름,코드들)],'start','end','metrics','warnings'}"""
    warns = []
    spec = spec if isinstance(spec, dict) else {}
    items, seen = [], set()
    for it in (spec.get("items") or []):
        if not isinstance(it, dict):
            continue
        kind, code = str(it.get("kind", "")).strip().lower(), str(it.get("code", "")).strip()
        if kind in ("group", "20대", "주력품목"):
            if code not in ctx["groups"]:
                warns.append(f"'{code}'은(는) 산업부 20대 품목에 없어서 뺐어요.")
                continue
            key, label = ("mti", GROUP_PREFIX + code), f"{code} (산업부 20대 품목)"
            kind = "group"
        elif kind == "mti":
            if code not in ctx["mti_names"]:
                warns.append(f"MTI 코드 {code}은(는) 연계표에 없어서 뺐어요.")
                continue
            key, label = ("mti", code), f"MTI {code} {ctx['mti_names'][code]}"
        elif kind == "hs":
            code = code.replace(".", "").replace("-", "")
            if not (code.isdigit() and len(code) in (2, 4, 6, 8, 10)):
                warns.append(f"HS 코드 '{code}'은(는) 2·4·6·8·10자리 숫자가 아니어서 뺐어요.")
                continue
            name = ctx["hs_names"].get(code[:4], "")
            key, label = ("hs", code), f"HS {code}" + (f" {name}" if name and len(code) == 4 else "")
        else:
            continue
        if key in seen:
            continue
        seen.add(key)
        refs = [r for r in (it.get("refs") or []) if isinstance(r, int)]
        conf = str(it.get("confidence", "")).strip()
        items.append({"kind": kind, "code": code, "key": key, "label": label, "reason": str(it.get("reason", "")).strip(),
                      "confidence": conf if conf in ("높음", "중간", "낮음") else "중간", "refs": refs})
    if len(items) > AI_MAX_ITEMS:
        warns.append(f"품목은 {AI_MAX_ITEMS}개까지만 남겼어요.")
        items = items[:AI_MAX_ITEMS]

    regions = []
    for rg in (spec.get("regions") or []):
        if not isinstance(rg, dict):
            continue
        kind, name = str(rg.get("kind", "")).strip(), str(rg.get("name", "")).strip()
        if kind == "국가" and name in ctx["countries"]:
            regions.append(("국가", name, (ctx["countries"][name],)))
        elif kind == "경제권" and name in ctx["zones"]:
            regions.append(("경제권", name, tuple(zone_codes(name))))
        elif kind == "대륙" and name in ctx["continents"]:
            regions.append(("대륙", name, tuple(continent_codes(name))))
        else:
            warns.append(f"지역 '{name}'({kind})을(를) 찾지 못해 뺐어요(국가는 관세청 국가명, 경제권·대륙은 목록에 있는 이름만 가능).")
    months = ctx["months"]
    last_ok = max(months[-1], ctx.get("flash_ym") or "")
    default_start = next((m for m in months if m.startswith(months[-1][:4])), months[0])
    per = spec.get("period") if isinstance(spec.get("period"), dict) else {}
    start, end = per.get("start"), per.get("end")
    start = start if _ym_ok(start) else default_start
    end = end if _ym_ok(end) else last_ok
    if start < months[0]:
        warns.append(f"모아 둔 자료는 {months[0]}부터라 시작월을 {months[0]}로 맞췄어요.")
        start = months[0]
    if end > last_ok:
        warns.append(f"종료월을 가장 최근 자료인 {last_ok}로 맞췄어요.")
        end = last_ok
    if start > end:
        warns.append("기간이 거꾸로라서 기본 기간(올해 1월~최근)으로 바꿨어요.")
        start, end = default_start, last_ok
    metrics = [m for m in (spec.get("metrics") or []) if m in AI_METRICS] or ["수출금액", "수입금액", "무역수지"]
    if not items:
        metrics = [m for m in metrics if "중량" not in m] or ["수출금액", "수입금액", "무역수지"]  # 품목이 없으면 중량 자료가 없다
    n = max(1, len(items)) * max(1, len(regions))
    if n > 12:
        warns.append(f"항목이 {n}개라 많아서 앞쪽 일부만 쓰도록 줄였어요(최대 12개).")
        while max(1, len(items)) * max(1, len(regions)) > 12:
            if len(regions) >= len(items) and len(regions) > 1:
                regions = regions[:-1]
            else:
                items = items[:-1]
    view = AI_VIEW_NAMES.get(str(spec.get("view", "")).strip().lower(), "월별 추이")
    level = str(spec.get("rank_level", "")).strip()
    try:
        top = int(spec.get("top"))
    except (TypeError, ValueError):
        top = 10
    top = min((5, 10, 20, 50), key=lambda x: abs(x - top))
    return {"summary": str(spec.get("summary", "")).strip(), "notes": str(spec.get("notes", "")).strip(), "items": items, "regions": regions,
            "start": start, "end": end, "metrics": metrics, "warnings": warns, "view": view, "rank_level": level if level in ("국가", "경제권", "대륙") else "국가", "top": top}


def ai_infer_metrics(text):
    """질문 글에서 원하는 지표를 읽는다(AI가 지표를 비워 뒀을 때 보완). 수출·수입 중 한쪽만 말했으면 그쪽만, 물량·중량·톤이면 중량, 수지면 무역수지. 모르면 None"""
    t = text or ""
    ex, im = "수출" in t, "수입" in t
    wgt = any(w in t for w in ("물량", "중량", "톤"))
    amt = "액" in t or not wgt  # '수출액'처럼 금액을 말했거나, 물량 얘기가 없으면 금액
    out = []
    for flag, name in ((ex and not im, "수출"), (im and not ex, "수입")):
        if flag:
            if amt:
                out.append(f"{name}금액")
            if wgt:
                out.append(f"{name} 중량")
    if "수지" in t:
        out.append("무역수지")
    return out or None


AI_FILLER = ["수출입", "수출액", "수입액", "수출량", "수입량", "무역수지", "수출", "수입", "금액", "중량", "물량", "통계", "실적", "현황", "찾아줘", "찾아", "알려줘", "보여줘", "조회",
             "올해", "작년", "지난해", "지난달", "최근", "으로", "에서", "에게", "한테", "수지", "년", "월", "된", "한", "의", "을", "를", "이", "가", "은", "는", "와", "과", "로", "에", "도", "만"]


def ai_clear_groups(text, groups, other_names=()):
    """질문이 '산업부 20대 품목 이름 + 지역·기간·지표 말'뿐이면 그 20대 품목들을, 아니면 [] (예: '자동차' -> ['자동차'], '반도체 장비'·'전기차' -> []).
    '자동차부품'처럼 긴 이름이 먼저 맞고 짧은 이름은 그 안에서 다시 세지 않는다."""
    rest, found = text or "", []
    for g in sorted(groups, key=len, reverse=True):
        if g in rest:
            found.append(g)
            rest = rest.replace(g, " ")
    if not found:
        return []
    rest = re.sub(r"20\d\d|\d{1,2}\s*(?:~|-)\s*\d{1,2}\s*월|\d{1,2}\s*월|\d\s*분기|상반기|하반기", " ", rest)  # 연·월·분기 말은 기간
    for w in sorted(set(AI_FILLER) | set(other_names), key=len, reverse=True):
        rest = rest.replace(w, " ")
    if re.search(r"\d", rest):  # 남은 숫자는 코드로 본다(예: '자동차 8703')
        return []
    left = re.sub(r"[^가-힣A-Za-z]", "", rest)
    return found if len(left) == 0 else []


# ---- 국가별 순위 ----
RANK_METRICS = {"수출금액": "exp", "수입금액": "imp", "무역수지": "bal", "수출 중량": "exp_wgt", "수입 중량": "imp_wgt"}


def _zero_metrics():
    return {"exp": 0, "imp": 0, "bal": 0, "exp_wgt": 0, "imp_wgt": 0}


def stored_period(exp, imp, months, start, end):
    """trade.json 월별 배열의 [start, end](포함) 합 -> 지표 dict, 그 기간에 값이 하나도 없으면 None"""
    e = m = 0
    seen = False
    for i, ym in enumerate(months):
        if start <= ym <= end and (exp[i] is not None or imp[i] is not None):
            seen = True
            e += exp[i] or 0
            m += imp[i] or 0
    return {"exp": e, "imp": m, "bal": e - m, "exp_wgt": 0, "imp_wgt": 0} if seen else None


def prev_period_bounds(start, end):
    """[start, end]의 1년 전 같은 기간 -> ('YYYY-MM', 'YYYY-MM')"""
    s, e = month_add(start.replace("-", ""), -12), month_add(end.replace("-", ""), -12)
    return f"{s[:4]}-{s[4:]}", f"{e[:4]}-{e[4:]}"


def rank_entries_stored(data, level, start, end, compare=True):
    """모아 둔 월별 자료로 만든 순위 후보 -> [{code, name, cur, prev}]. level: '국가' / '경제권' / '대륙' (경제권·대륙은 관세청이 준 합계를 그대로 쓴다)"""
    months = data["months"]
    pstart, pend = prev_period_bounds(start, end)
    full_prev = compare and pstart >= months[0]
    out = []
    if level == "국가":
        src = {c: (v[0], v[1], v[2]) for c, v in data["country"].items()}
    else:
        src = {c: (v[0], v[1], v[2]) for c, v in (data.get("region" if level == "경제권" else "continent") or {}).items()}
    for code, (name, exp, imp) in src.items():
        cur = stored_period(exp, imp, months, start, end)
        if cur is None:
            continue
        prev = stored_period(exp, imp, months, pstart, pend) if full_prev else None
        out.append({"code": code, "name": name, "cur": cur, "prev": prev})
    return out


# ---- 20대 품목 자료(groups.json: 품목 × 국가 × 월, 천달러)를 trade.json 국가 배열 모양으로 ----
def groups_usable(g, months):
    """groups.json이 앱에서 쓸 수 있는 상태인가: 다 받았고(complete), trade.json의 마지막 달까지 있고, 단위가 천달러"""
    return bool(isinstance(g, dict) and g.get("complete") and g.get("unit") == "천달러" and g.get("months") and g.get("data")
                and months and g["months"][-1] >= months[-1])


def groups_country(g, months, names=None):
    """groups.json -> {품목: {국가코드: [이름, 수출[], 수입[]]}} (달러, months 순서. groups에 없는 달은 None). sum_arrays·rank_entries_arrays에 그대로 쓴다"""
    gi = {m: i for i, m in enumerate(g["months"])}
    pos = [gi.get(m) for m in months]
    out = {}
    for name, per in g["data"].items():
        d = {}
        for code, (e, im) in per.items():
            d[code] = [(names or {}).get(code, code),
                       [None if k is None else e[k] * 1000 for k in pos],
                       [None if k is None else im[k] * 1000 for k in pos]]
        out[name] = d
    return out


def group_month_rows(gcountry, months, i, kind):
    """20대 품목별 한 달 수출(또는 수입): groups 자료({품목: {국가: [이름, 수출[], 수입[]]}})를 전 세계로 합쳐
    -> [{name, cur, prev(전년 같은 달), chg(%), diff(증감액)}] (i는 months의 위치, kind는 'exp' 또는 'imp')"""
    out = []
    for name, per in gcountry.items():
        exp, imp = sum_arrays(per, list(per), months)
        arr = exp if kind == "exp" else imp
        if not arr or arr[i] is None:
            continue
        cur = arr[i]
        prev = arr[i - 12] if i >= 12 else None
        out.append({"name": name, "cur": cur, "prev": prev, "chg": pct(cur, prev) if prev else None, "diff": None if prev is None else cur - prev})
    return out


def rank_entries_arrays(country, months, level, start, end, compare=True):
    """국가별 월 배열({코드: [이름, 수출[], 수입[]]})로 만든 순위 후보 -> [{code, name, cur, prev}]. level: '국가' / '경제권' / '대륙' (경제권·대륙은 소속 국가를 합산, 경제권끼리는 겹칠 수 있다)"""
    pstart, pend = prev_period_bounds(start, end)
    full_prev = compare and pstart >= months[0]
    if level == "국가":
        src = {c: (v[0], v[1], v[2]) for c, v in country.items()}
    else:
        table = ZONES if level == "경제권" else CONTINENTS
        src = {}
        for name, codes in table.items():
            members = codes.split() if isinstance(codes, str) else codes
            e, i = sum_arrays(country, members, months, ZONE_WINDOW.get(name) if level == "경제권" else None)
            if e:
                src[name] = (name, e, i)
    out = []
    for code, (name, exp, imp) in src.items():
        cur = stored_period(exp, imp, months, start, end)
        if cur is None or not (cur["exp"] or cur["imp"]):
            continue
        out.append({"code": code, "name": name, "cur": cur, "prev": stored_period(exp, imp, months, pstart, pend) if full_prev else None})
    return out


def rank_entries_rows(rows, prev_rows, level):
    """품목 조회 행(국가 코드 포함)으로 만든 순위 후보 -> [{code, name, cur, prev}] (prev_rows가 None이면 prev는 None).
    level: '국가' / '경제권' / '대륙' (경제권·대륙은 소속 국가 행을 합산, 경제권끼리는 겹칠 수 있다)"""
    def group(rs):
        acc = {}
        for r in rs:
            a = acc.setdefault(r["cnty"], {"name": r["cnty_name"], "m": _zero_metrics()})
            for k in ("exp", "imp", "exp_wgt", "imp_wgt"):
                a["m"][k] += r[k]
        for a in acc.values():
            a["m"]["bal"] = a["m"]["exp"] - a["m"]["imp"]
        return acc

    cur_c = group(rows)
    prev_c = group(prev_rows) if prev_rows is not None else None
    if level == "국가":
        return [{"code": c, "name": a["name"], "cur": a["m"], "prev": (prev_c.get(c, {"m": _zero_metrics()})["m"] if prev_c is not None else None)} for c, a in cur_c.items()]
    table = ZONES if level == "경제권" else {n: v for n, v in CONTINENTS.items()}
    out = []
    for name, codes in table.items():
        members = codes.split() if isinstance(codes, str) else codes
        window = ZONE_WINDOW.get(name) if level == "경제권" else None

        def total(rs):
            m = _zero_metrics()
            for r in rs:
                if r["cnty"] in members and not (window and r["cnty"] in window and not in_window(r["ym"], window[r["cnty"]])):
                    for k in ("exp", "imp", "exp_wgt", "imp_wgt"):
                        m[k] += r[k]
            m["bal"] = m["exp"] - m["imp"]
            return m

        cur = total(rows)
        if cur["exp"] or cur["imp"]:
            out.append({"code": name, "name": name, "cur": cur, "prev": total(prev_rows) if prev_rows is not None else None})
    return out


def rank_table(entries, metric_key, top_n, denom=None, with_other=True):
    """순위 후보 -> (표 줄들, 합계 줄). 무역수지는 큰 순(흑자 큰 나라 먼저), 나머지는 값이 큰 순.
    줄: {rank, name, value, share(%, 합계/denom 대비·무역수지는 None), prev, chg(%, 전년 같은 기간 대비)}
    top_n이 None이면 전부. with_other면 상위 밖을 '기타' 한 줄로 합친다. denom: 점유율의 분모(없으면 후보 값의 합)"""
    ents = sorted(entries, key=lambda e: -(e["cur"][metric_key] or 0))
    total = sum(e["cur"][metric_key] or 0 for e in ents)
    prev_total = sum((e["prev"][metric_key] or 0) for e in ents) if ents and all(e["prev"] is not None for e in ents) else None
    base = denom if denom else total
    share_ok = metric_key != "bal" and base

    def line(rank, name, value, prev):
        return {"rank": rank, "name": name, "value": value, "prev": prev,
                "share": (value / base * 100) if share_ok and value is not None else None,
                "chg": (pct(value, prev) if metric_key != "bal" and prev is not None else None)}

    top = ents if top_n is None else ents[:top_n]
    rows = [line(i, e["name"], e["cur"][metric_key], (e["prev"][metric_key] if e["prev"] is not None else None)) for i, e in enumerate(top, 1)]
    rest = ents[len(top):]
    if rest and with_other:
        v = sum(e["cur"][metric_key] or 0 for e in rest)
        pv = sum((e["prev"][metric_key] or 0) for e in rest) if all(e["prev"] is not None for e in rest) else None
        rows.append(line(None, f"기타 ({len(rest)}곳)", v, pv))
    totrow = {"value": total, "prev": prev_total, "chg": (pct(total, prev_total) if metric_key != "bal" else None)}
    return rows, totrow


# ---- 집계 단위(월·분기·연) ----
AGG_UNITS = ["월별", "분기별", "연도별"]


def period_key(ym, unit):
    """'2026-09' -> 월별 '2026-09' / 분기별 '2026-Q3' / 연도별 '2026'"""
    if unit == "분기별":
        return f"{ym[:4]}-Q{(int(ym[5:]) - 1) // 3 + 1}"
    if unit == "연도별":
        return ym[:4]
    return ym


def period_start(key, unit):
    """기간 키 -> 그 기간 첫날 'YYYY-MM-DD' (그래프의 가로 위치용)"""
    if unit == "분기별":
        return f"{key[:4]}-{(int(key[6:]) - 1) * 3 + 1:02d}-01"
    if unit == "연도별":
        return f"{key}-01-01"
    return f"{key}-01"


def prev_period_key(key, unit):
    """바로 앞 기간 이름"""
    if unit == "분기별":
        y, q = int(key[:4]), int(key[6:])
        return f"{y - 1}-Q4" if q == 1 else f"{y}-Q{q - 1}"
    if unit == "연도별":
        return str(int(key) - 1)
    return _dash_add(key, -1)


def period_label(key, unit, months_in):
    """기간 이름표. 일부 달만 있는 기간은 '(7~9월)'·'(1~9월 누계)'처럼 범위를 적는다"""
    first, last = int(months_in[0][5:]), int(months_in[-1][5:])
    full = 12 if unit == "연도별" else 3
    if unit == "월별":
        return key.replace("-", ".")
    base = key.replace("-", " ") if unit == "분기별" else f"{key}년"
    if len(months_in) >= full:
        return base
    rng = f"{first}월" if first == last else f"{first}~{last}월"
    return f"{base} ({rng}{' 누계' if unit == '연도별' and first == 1 else ''})"


def _add_metrics(a, b):
    return {k: (a.get(k) or 0) + (b.get(k) or 0) for k in ("exp", "imp", "exp_wgt", "imp_wgt")}


def aggregate_series(series, months_all, unit, flash_ym=None):
    """월별 계열들을 분기·연 단위로 합친다 -> (새 계열들, 기간 키 목록(오래된 순), {키: 이름표}, 잠정이 들어간 키 집합).
    기간마다 '값이 있는 달'만 더하고, 비교 대상(전년 같은 달들)도 그 달들만 더해서 일부만 지난 기간도 같은 기간끼리 비교한다."""
    keys, groups = [], {}
    for ym in months_all:
        k = period_key(ym, unit)
        if k not in groups:
            keys.append(k)
            groups[k] = []
        groups[k].append(ym)
    labels = {k: period_label(k, unit, groups[k]) for k in keys}
    prov = {period_key(flash_ym, unit)} if flash_ym else set()
    out = []
    for sr in series:
        month, prev, fl = {}, {}, sr.get("flash")
        for k in keys:
            cur_m, prev_m, have, prev_ok = None, None, 0, True
            for ym in groups[k]:
                if ym == flash_ym and fl:
                    c, pv = fl["cur"], fl["prev"]
                elif ym in sr["month"]:
                    c, pv = sr["month"][ym], ((sr.get("prev") or {}).get(ym))
                else:
                    continue
                if any(c.get(x) is None for x in ("exp", "imp")):  # 잠정치는 일부 지표가 없을 수 있다
                    c = {x: c.get(x) for x in c}
                have += 1
                cur_m = _add_metrics(cur_m or {}, {x: (c.get(x) or 0) for x in ("exp", "imp", "exp_wgt", "imp_wgt")})
                if pv is None or any(pv.get(x) is None for x in ("exp", "imp")):
                    prev_ok = False
                else:
                    prev_m = _add_metrics(prev_m or {}, pv)
            if have:
                cur_m["bal"] = cur_m["exp"] - cur_m["imp"]
                month[k] = cur_m
                if prev_ok and prev_m is not None and (sr.get("prev") is not None or (ym == flash_ym and fl)):
                    prev_m["bal"] = prev_m["exp"] - prev_m["imp"]
                    prev[k] = prev_m
        new = dict(sr, month=month, prev=prev if sr.get("prev") is not None or any(prev) else None, flash=None)
        out.append(new)
    return out, keys, labels, prov


# ---- 품목별 구성 (HS 단계별) ----
HS2_NAMES = {
    "01": "산 동물", "02": "육과 식용 설육", "03": "어류·갑각류·연체동물", "04": "낙농품·조란·꿀", "05": "기타 동물성 생산품", "06": "산 수목·꽃", "07": "식용 채소",
    "08": "식용 과실·견과", "09": "커피·차·향신료", "10": "곡물", "11": "제분공업 생산품", "12": "채유 종자·과실", "13": "락·검·수지", "14": "식물성 편조물",
    "15": "동식물성 유지", "16": "육류·어류 조제품", "17": "당류·설탕과자", "18": "코코아·초콜릿", "19": "곡물·밀가루 조제품", "20": "채소·과실 조제품",
    "21": "기타 조제 식료품", "22": "음료·주류·식초", "23": "식품공업 잔재물·사료", "24": "담배", "25": "소금·황·토석류·시멘트", "26": "광·슬래그·회",
    "27": "광물성 연료·에너지", "28": "무기화학품", "29": "유기화학품", "30": "의료용품", "31": "비료", "32": "염료·안료·도료", "33": "정유·향료·화장품",
    "34": "비누·세제·왁스", "35": "단백질계 물질·풀", "36": "화약류·성냥", "37": "사진용 재료", "38": "각종 화학공업 생산품", "39": "플라스틱", "40": "고무",
    "41": "원피·가죽", "42": "가죽제품·여행용구", "43": "모피·인조모피", "44": "목재", "45": "코르크", "46": "짚·초 제품", "47": "펄프", "48": "지·판지",
    "49": "서적·인쇄물", "50": "견", "51": "양모·동물의 털", "52": "면", "53": "기타 식물성 방직섬유", "54": "인조필라멘트", "55": "인조스테이플섬유",
    "56": "부직포·특수사", "57": "양탄자", "58": "특수직물", "59": "침투·도포 직물", "60": "편물", "61": "의류(편물제)", "62": "의류(편물제 제외)",
    "63": "기타 방직용 섬유제품", "64": "신발", "65": "모자", "66": "우산·지팡이", "67": "조제 깃털·조화", "68": "석·플라스터·시멘트 제품", "69": "도자제품",
    "70": "유리·유리제품", "71": "귀금속·보석", "72": "철강", "73": "철강제품", "74": "구리", "75": "니켈", "76": "알루미늄", "78": "납", "79": "아연", "80": "주석",
    "81": "기타 비금속", "82": "공구·칼붙이", "83": "각종 비금속제품", "84": "기계류", "85": "전기기기", "86": "철도차량", "87": "자동차·부품", "88": "항공기",
    "89": "선박", "90": "광학·정밀·의료기기", "91": "시계", "92": "악기", "93": "무기", "94": "가구·침구·조명", "95": "완구·운동용구", "96": "잡품", "97": "예술품·골동품",
}
HS_LEVELS = {0: 2, 2: 4, 4: 6, 6: 10}  # 지금 보고 있는 경로(코드) 길이 -> 다음에 보여줄 HS 자릿수


def merge_hs_totals(parts):
    """여러 {hs10: [이름, 수출, 수입, 수출중량, 수입중량]}를 합친다"""
    out = {}
    for part in parts:
        for hs, v in part.items():
            a = out.setdefault(hs, [v[0], 0, 0, 0, 0])
            for k in range(1, 5):
                a[k] += v[k]
    return out


def hs_metrics(v):
    return {"exp": v[1], "imp": v[2], "bal": v[1] - v[2], "exp_wgt": v[3], "imp_wgt": v[4]}


def hs_entries(cur, prev, prefix, hs_names):
    """HS 10자리 합계(cur·prev: {hs10: [이름, 수출, 수입, 수출중량, 수입중량]})를 prefix 아래에서 다음 단계(2·4·6·10자리)로 묶어 순위 후보로 -> (후보들, 단계 자릿수)"""
    length = HS_LEVELS.get(len(prefix), 10)
    groups = {}
    for src, tag in ((cur, "cur"), (prev, "prev")):
        if src is None:
            continue
        for hs, v in src.items():
            if not hs.startswith(prefix):
                continue
            g = groups.setdefault(hs[:length], {"cur": [None, 0, 0, 0, 0], "prev": [None, 0, 0, 0, 0], "big": ("", -1)})
            for k in range(1, 5):
                g[tag][k] += v[k]
            if tag == "cur" and v[1] + v[2] > g["big"][1]:
                g["big"] = (v[0], v[1] + v[2])
    out = []
    for code, g in groups.items():
        if length == 2:
            name = HS2_NAMES.get(code, f"HS {code}류")
        elif length == 4:
            name = hs_names.get(code) or (g["big"][0][:20] + "…" if g["big"][0] else "")
        elif length == 6:
            name = (g["big"][0][:20] + " 등") if g["big"][0] else ""
        else:
            name = g["big"][0]
        out.append({"code": code, "name": f"{code} {name}".strip(), "cur": hs_metrics(g["cur"]),
                    "prev": hs_metrics(g["prev"]) if prev is not None else None})
    return out, length


def ai_infer_view(text):
    """질문 글에서 보기 종류를 읽는다(AI가 안 골랐을 때 보완): '가장 많이·순위·어느 나라·상위' + 나라 말 -> 국가별 순위, '어떤 품목·품목별·구성·비중·주력 품목' -> 품목별 구성. 모르면 None"""
    t = text or ""
    comp = any(w in t for w in ("어떤 품목", "무슨 품목", "품목별", "품목 구성", "구성", "비중", "주력 품목", "많이 나간 품목", "많이 수출한 품목", "많이 수입한 품목", "품목 순위", "품목 상위"))
    rank = any(w in t for w in ("어느 나라", "어떤 나라", "어디로", "어디에서", "국가별", "나라별", "순위", "상위", "가장 많이", "1위", "톱"))
    if comp:
        return "품목별 구성"
    if rank:
        return "국가별 순위"
    return None
