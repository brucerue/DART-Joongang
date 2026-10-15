"""
DART 매출액 / 영업이익 조회 (Streamlit)

기능: 연도별·분기별 조회, 전년(동기)·전분기 대비 증감률,
      흑자전환/적자전환/적자지속 표시, 금액은 억원 단위,
      CSV·엑셀 다운로드, 공시 원문 링크, 공시 목록(유형 필터·보고서명 검색)

[설치]   pip install -r requirements.txt
[실행]   streamlit run app.py
"""

import io
import os
import difflib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
import OpenDartReader

try:
    import openpyxl  # noqa: F401  (엑셀 다운로드에 필요)

    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False

# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------
FS_DIV = "CFS"  # CFS = 연결재무제표, OFS = 별도(개별)재무제표
UNIT = "억원"  # 금액 단위는 억원으로 고정
MAX_YEAR_SPAN = 10  # 한 번에 조회 가능한 최대 연도 수 (API 사용량 보호)
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
DART_API_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"  # 재무제표
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"  # 공시 목록

MAX_WORKERS = 6  # 동시에 보낼 DART 요청 수 (한 번 조회에 최대 44번이라 분당 한도에는 여유)
REQUEST_TIMEOUT = 20  # DART 응답을 기다리는 최대 시간(초)
CACHE_TTL = 6 * 3600  # 재무 조회 결과를 저장해두는 시간
EMPTY_TTL = 30 * 60  # '아직 공시 없음' 결과는 더 짧게 저장 (곧 공시될 수 있으므로)
LIST_TTL = 10 * 60  # 공시 목록은 새 공시가 계속 올라오므로 짧게 저장
LIST_PAGE_SIZE = 100  # DART 공시 목록 API가 한 번에 주는 최대 건수
MAX_LIST_PAGES = 10  # 공시 목록은 최대 1,000건까지만 표시

# 공시 유형 (DART 공시 목록 API의 pblntf_ty 값). 빈 문자열은 '전체'
DISCLOSURE_TYPES = {
    "전체": "",
    "정기공시": "A",
    "주요사항보고": "B",
    "발행공시": "C",
    "지분공시": "D",
    "기타공시": "E",
    "외부감사관련": "F",
    "펀드공시": "G",
    "자산유동화": "H",
    "거래소공시": "I",
    "공정위공시": "J",
}

KST = timezone(timedelta(hours=9))  # 서버가 해외에 있어도 한국 날짜 기준으로 동작
TODAY = datetime.now(KST).date()

# 표에서 눈에 띄게 보여줄 금액 열의 글자색 (밝은 테마·어두운 테마 모두에서 읽히는 중간 톤)
POSITIVE_COLOR = "#1c7ed6"  # 매출액·영업이익: 파랑
NEGATIVE_COLOR = "#e03131"  # 마이너스(적자 등): 빨강


def load_api_key():
    """API 키는 코드에 적지 않고 Streamlit secrets 또는 환경변수에서 읽습니다."""
    try:
        key = st.secrets["DART_API_KEY"]
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("DART_API_KEY")


API_KEY = load_api_key()

REVENUE_NAMES = ["매출액", "수익(매출액)", "영업수익"]
OPERATING_NAMES = ["영업이익", "영업이익(손실)", "영업손익", "영업손실", "영업손실(이익)"]
LOSS_ONLY_NAMES = ["영업손실"]  # 이 이름이면 값이 양수여도 손실로 처리

# 보고서 코드: 1분기 / 반기 / 3분기 / 사업보고서(연간)
Q1, H1, Q3, ANNUAL = "11013", "11012", "11014", "11011"


# ------------------------------------------------------------
# 데이터 조회 함수
# ------------------------------------------------------------
class DartApiError(Exception):
    """DART 서버 오류·호출 한도 초과 등 사용자에게 알려야 하는 조회 실패."""


@st.cache_resource
def get_dart():
    """기업명 → 고유번호 변환용. (처음 한 번 기업 목록을 내려받느라 몇 초 걸릴 수 있음)"""
    return OpenDartReader(API_KEY)


@st.cache_resource
def get_session():
    """연결을 재사용하는 HTTP 세션 (요청마다 새로 접속하는 시간을 줄임)."""
    session = requests.Session()
    session.mount("https://", HTTPAdapter(pool_maxsize=MAX_WORKERS))
    return session


@st.cache_resource
def get_cache_store():
    """{(고유번호, 연도, 보고서코드): (저장시각, 데이터)} - 서버가 켜져 있는 동안 유지."""
    return {}


KEEP_COLUMNS = ["rcept_no", "sj_div", "account_nm", "thstrm_amount", "thstrm_add_amount"]


def slim(df):
    """필요한 계정(매출·영업이익)과 열만 남겨 저장 용량을 줄인다. 없으면 None."""
    if df is None or df.empty or "account_nm" not in df.columns:
        return None
    rows = df[df["account_nm"].str.strip().isin(REVENUE_NAMES + OPERATING_NAMES)]
    if rows.empty:
        return None
    return rows[[c for c in KEEP_COLUMNS if c in rows.columns]].reset_index(drop=True)


_MISSING = object()  # '저장된 값 없음' 표시 (None은 '조회했더니 데이터 없음'이라는 뜻이라 구분 필요)


def cache_lookup(store, key, ttl):
    """저장된 값이 유효하면 그 값을, 아니면 _MISSING을 돌려준다. 데이터 없음(None)은 짧게만 저장."""
    hit = store.get(key)
    if hit is None:
        return _MISSING
    saved_at, value = hit
    if time.time() - saved_at < (ttl if value is not None else EMPTY_TTL):
        return value
    return _MISSING


def cache_save(store, key, value):
    if len(store) > 2000:  # 메모리 보호
        store.clear()
    store[key] = (time.time(), value)


def call_dart(session, url, params):
    """DART API 호출 공통 처리.

    정상이면 응답(dict), 조회된 데이터가 없으면(013) None,
    응답 없음·호출 한도 초과·키 오류 등은 DartApiError.
    """
    jo, last_error = None, None
    for attempt in range(3):  # 일시적인 네트워크 오류는 두 번까지 다시 시도
        try:
            jo = session.get(url, params=params, timeout=REQUEST_TIMEOUT).json()
            break
        except Exception as e:  # 시간 초과, 연결 끊김, JSON 오류
            last_error = e
            time.sleep(0.5 * (attempt + 1))
    if jo is None:
        raise DartApiError("DART 서버 응답이 없거나 늦어지고 있습니다. 잠시 후 다시 시도해주세요.") from last_error

    status = str(jo.get("status", ""))
    if "list" in jo:
        return jo
    if status == "013":  # 조회된 데이터 없음 (아직 공시 전이거나 해당 보고서 없음)
        return None
    if status == "020":
        raise DartApiError("DART API 하루 호출 한도를 초과했습니다. 잠시 후 또는 내일 다시 시도해주세요.")
    raise DartApiError(f"DART 응답 오류 [{status}] {jo.get('message', '')}".strip())


def fetch(session, store, corp_code, year, reprt_code):
    """보고서 한 건 조회. 없으면 None, DART 오류·응답 없음이면 DartApiError.

    ※ 작업 스레드에서 실행되므로 st.* 함수를 쓰지 않고, 세션·저장소는 인자로 받는다.
    """
    key = (corp_code, year, reprt_code)
    cached = cache_lookup(store, key, CACHE_TTL)
    if cached is not _MISSING:
        return cached

    params = {
        "crtfc_key": API_KEY,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": reprt_code,
        "fs_div": FS_DIV,
    }
    jo = call_dart(session, DART_API_URL, params)
    df = slim(pd.DataFrame(jo["list"])) if jo else None
    cache_save(store, key, df)
    return df


def fetch_disclosure_page(session, store, corp_code, bgn, end, page, kind=""):
    """공시 목록 한 페이지(최대 100건). 없으면 None. kind는 공시 유형 코드(A~J), 빈 값이면 전체."""
    key = ("list", corp_code, bgn, end, page, kind)
    cached = cache_lookup(store, key, LIST_TTL)
    if cached is not _MISSING:
        return cached

    params = {
        "crtfc_key": API_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn,  # 시작일 YYYYMMDD
        "end_de": end,  # 종료일 YYYYMMDD
        "last_reprt_at": "N",  # N = 정정 보고서까지 모두 (DART 사이트 목록과 같은 방식)
        "page_no": page,
        "page_count": LIST_PAGE_SIZE,
    }
    if kind:
        params["pblntf_ty"] = kind
    jo = call_dart(session, DART_LIST_URL, params)
    cache_save(store, key, jo)
    return jo


def collect_disclosures(corp_code, start, end, kind=""):
    """기간 내 공시 목록(최신순). (행 목록, 전체 건수, 잘림 여부)를 돌려준다."""
    session, store = get_session(), get_cache_store()
    bgn, fin = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    first = fetch_disclosure_page(session, store, corp_code, bgn, fin, 1, kind)
    if first is None:
        return [], 0, False

    total_count = int(first.get("total_count", len(first["list"])))
    total_page = int(first.get("total_page", 1))
    last_page = min(total_page, MAX_LIST_PAGES)

    pages = {1: first}
    if last_page > 1:  # 나머지 페이지는 동시에 조회
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(fetch_disclosure_page, session, store, corp_code, bgn, fin, p, kind): p
                for p in range(2, last_page + 1)
            }
            try:
                for future in as_completed(futures):
                    pages[futures[future]] = future.result()
            except Exception:
                for f in futures:
                    f.cancel()
                raise

    rows = []
    for p in range(1, last_page + 1):
        if pages.get(p):
            rows.extend(pages[p]["list"])
    return rows, total_count, total_page > MAX_LIST_PAGES


def fetch_many(corp_code, tasks, tick):
    """(연도, 보고서코드) 목록을 동시에 조회해 {(연도, 보고서코드): 데이터}로 돌려준다."""
    session, store = get_session(), get_cache_store()  # 화면 스레드에서 미리 준비
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch, session, store, corp_code, y, c): (y, c) for (y, c) in tasks}
        try:
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                tick()
        except Exception:
            for f in futures:  # 아직 시작 안 한 요청은 취소
                f.cancel()
            raise
    return results


def to_int(x):
    """문자열 금액을 정수로 변환. 음수 표기(괄호, -, △, ▲)를 모두 마이너스로 처리."""
    s = str(x).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "nan", "None"):
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):  # (1234) 형태
        negative = True
        s = s[1:-1]
    if s and s[0] in ("△", "▲", "-", "−"):  # △1234, ▲1234, -1234
        negative = True
        s = s[1:]
    try:
        value = int(s)
    except ValueError:
        return None
    return -value if negative else value


def pick(df, names, column="thstrm_amount"):
    """손익계산서에서 계정과목(names 중 하나)의 금액을 뽑아냄."""
    if df is None or column not in df.columns:
        return None
    rows = df[df["account_nm"].str.strip().isin(names)]
    if "sj_div" in rows.columns:
        is_rows = rows[rows["sj_div"].isin(["IS", "CIS"])]
        if not is_rows.empty:
            rows = is_rows
    if rows.empty:
        return None
    row = rows.iloc[0]
    value = to_int(row[column])
    # 계정명이 '영업손실'인데 양수로 적혀 있으면 손실(마이너스)로 본다
    if value is not None and value > 0 and str(row["account_nm"]).strip() in LOSS_ONLY_NAMES:
        value = -value
    return value


def link_of(df):
    """해당 보고서의 DART 원문 링크. 접수번호가 없으면 None."""
    if df is None or "rcept_no" not in df.columns or df.empty:
        return None
    rcept_no = str(df["rcept_no"].iloc[0]).strip()
    return DART_VIEW_URL.format(rcept_no) if rcept_no else None


# ------------------------------------------------------------
# 증감률 계산
# ------------------------------------------------------------
def pct_change(cur, prev):
    """증감률(%). 비교 값이 없거나 0이면 None."""
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100


def op_change(cur, prev):
    """영업이익 증감: 퍼센트(float) 또는 '흑자전환/적자전환/적자지속' 문구(str)."""
    if cur is None or prev is None:
        return None
    if prev <= 0 and cur > 0:
        return "흑자전환"
    if prev >= 0 and cur < 0:
        return "적자전환"
    if prev < 0 and cur < 0:
        return "적자지속"
    if prev > 0:
        return pct_change(cur, prev)
    return None


def make_record(label, sort_key, cur, yoy=None, qoq=None):
    rev, op = cur["rev"], cur["op"]
    return {
        "label": label,
        "sort": sort_key,
        "rev": rev,
        "op": op,
        "rev_yoy": pct_change(rev, yoy["rev"]) if yoy else None,
        "op_yoy": op_change(op, yoy["op"]) if yoy else None,
        "rev_qoq": pct_change(rev, qoq["rev"]) if qoq else None,
        "op_qoq": op_change(op, qoq["op"]) if qoq else None,
        "link": cur["link"],
    }


# ------------------------------------------------------------
# 연도별 / 분기별 수집
# ------------------------------------------------------------
def collect_yearly(corp_code, start, end, tick):
    """증감률 계산을 위해 시작연도 전년도까지 함께 조회한다."""
    years = range(start - 1, end + 1)
    reports = fetch_many(corp_code, [(y, ANNUAL) for y in years], tick)

    data = {}
    for y in years:
        df = reports[(y, ANNUAL)]
        data[y] = {"rev": pick(df, REVENUE_NAMES), "op": pick(df, OPERATING_NAMES), "link": link_of(df)}

    records = []
    for y in range(start, end + 1):
        cur = data[y]
        if cur["rev"] is None and cur["op"] is None:
            continue
        records.append(make_record(str(y), (y, 0), cur, yoy=data[y - 1]))
    return records


def quarter_values(r1, r2, r3, r4, names):
    """1~4분기 값을 계산. 4분기 = 연간 - 3분기 누적."""
    q1 = pick(r1, names)
    q2 = pick(r2, names)
    q3 = pick(r3, names)
    annual = pick(r4, names)

    cum9 = pick(r3, names, "thstrm_add_amount")  # 3분기 누적(9개월)
    if cum9 is None and None not in (q1, q2, q3):
        cum9 = q1 + q2 + q3
    q4 = annual - cum9 if annual is not None and cum9 is not None else None
    return [q1, q2, q3, q4]


def collect_quarterly(corp_code, start, end, tick):
    years = range(start - 1, end + 1)
    codes = (Q1, H1, Q3, ANNUAL)
    fetched = fetch_many(corp_code, [(y, c) for y in years for c in codes], tick)

    data = {}
    for y in years:
        reports = [fetched[(y, c)] for c in codes]
        revs = quarter_values(*reports, REVENUE_NAMES)
        ops = quarter_values(*reports, OPERATING_NAMES)
        for i in range(4):  # 1~3분기는 각 분기 보고서, 4분기는 사업보고서로 연결
            data[(y, i + 1)] = {"rev": revs[i], "op": ops[i], "link": link_of(reports[i])}

    records = []
    for y in range(start, end + 1):
        for q in range(1, 5):
            cur = data[(y, q)]
            if cur["rev"] is None and cur["op"] is None:
                continue
            qoq = data[(y, q - 1)] if q > 1 else data[(y - 1, 4)]
            records.append(make_record(f"{y} {q}Q", (y, q), cur, yoy=data[(y - 1, q)], qoq=qoq))
    return records


# ------------------------------------------------------------
# 표 만들기 (화면용 / 다운로드용)
# ------------------------------------------------------------
def fmt_amount(v):
    """원 -> 억원 (천 단위 쉼표). 값이 없으면 '-'."""
    if v is None:
        return "-"
    return f"{v / 1e8:,.0f}"


def fmt_change(c):
    if c is None:
        return "-"
    if isinstance(c, str):
        return c
    return f"{c:+,.1f}%"


def num_amount(v):
    """다운로드 파일용: 원 -> 억원(숫자). 값이 없으면 None."""
    if v is None:
        return None
    return int(round(v / 1e8))


def amount_style(cell):
    """금액 칸 스타일: 기본 파랑 + 굵게, 마이너스(앞에 '-')면 빨강 + 굵게."""
    color = NEGATIVE_COLOR if str(cell).startswith("-") and str(cell) != "-" else POSITIVE_COLOR
    return f"font-weight: bold; color: {color}"


def style_table(table):
    """매출액·영업이익 열에만 색과 굵기를 입힌다."""
    return table.style.map(amount_style, subset=["매출액", "영업이익"])


def sorted_records(records):
    # 오래된 기간이 위로 (2023 → 2024 → 2025). 최신이 위로 오게 하려면 reverse=True를 추가
    return sorted(records, key=lambda r: r["sort"])


def build_table(records, period, for_export=False):
    quarterly = period == "분기별"
    yoy = "전년동기比" if quarterly else "전년比"
    amt = num_amount if for_export else fmt_amount

    rows = []
    for r in sorted_records(records):
        row = {}
        row["기간"] = r["label"]
        row["매출액"] = amt(r["rev"])
        row[f"매출 {yoy}"] = fmt_change(r["rev_yoy"])
        if quarterly:
            row["매출 전분기比"] = fmt_change(r["rev_qoq"])
        row["영업이익"] = amt(r["op"])
        row[f"영업이익 {yoy}"] = fmt_change(r["op_yoy"])
        if quarterly:
            row["영업이익 전분기比"] = fmt_change(r["op_qoq"])
        row["원문"] = r["link"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if for_export:
        df = df.rename(
            columns={"매출액": f"매출액({UNIT})", "영업이익": f"영업이익({UNIT})", "원문": "공시원문링크"}
        )
    return df


def safe_filename(text):
    """파일 이름에 쓸 수 없는 문자(공백, 슬래시 등)를 밑줄로 바꾼다."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "기업"


def filter_by_keyword(rows, keyword):
    """보고서명에 검색어가 모두 들어 있는 공시만 남긴다. 공백으로 여러 단어를 넣으면 모두 포함된 것만(대소문자 무시)."""
    terms = keyword.lower().split()
    if not terms:
        return rows
    return [r for r in rows if all(t in str(r.get("report_nm", "")).lower() for t in terms)]


def disclosure_table(rows):
    """공시 목록 API 응답(rows)을 화면용 표로 바꾼다."""
    df = pd.DataFrame(rows).reindex(columns=["rcept_dt", "report_nm", "flr_nm", "rcept_no"]).fillna("")
    dates = pd.to_datetime(df["rcept_dt"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    return pd.DataFrame(
        {
            "접수일": dates,
            "보고서명": df["report_nm"].astype(str).str.strip(),
            "제출인": df["flr_nm"],
            "원문": df["rcept_no"].astype(str).map(lambda n: DART_VIEW_URL.format(n) if n else None),
        }
    )


# ------------------------------------------------------------
# 화면
# ------------------------------------------------------------
LINK_COLUMN = st.column_config.LinkColumn("원문", display_text="공시 보기")


# 영문 알파벳 -> 한글 이름 (SK -> 에스케이). 'SK하이닉스'와 '에스케이하이닉스'를 같은 이름으로 보기 위함
LETTER_NAMES = {
    "a": "에이", "b": "비", "c": "씨", "d": "디", "e": "이", "f": "에프", "g": "지", "h": "에이치", "i": "아이",
    "j": "제이", "k": "케이", "l": "엘", "m": "엠", "n": "엔", "o": "오", "p": "피", "q": "큐", "r": "알",
    "s": "에스", "t": "티", "u": "유", "v": "브이", "w": "더블유", "x": "엑스", "y": "와이", "z": "지",
}
SUGGEST_LIMIT = 8


def name_key(name):
    """기업명을 비교용으로 정리: 소문자, (주)·공백·기호 제거, 영문은 한글 발음으로."""
    text = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|[\s\-.,&·()/]", "", str(name).lower())
    return "".join(LETTER_NAMES.get(ch, ch) for ch in text)


@st.cache_resource
def get_name_index():
    """추천용 기업명 목록 (상장사 우선). 서버가 켜진 뒤 처음 한 번만 만든다."""
    df = get_dart().corp_codes[["corp_name", "stock_code"]].copy()
    df["corp_name"] = df["corp_name"].astype(str)
    df["stock_code"] = df["stock_code"].fillna("").astype(str).str.strip()
    df["listed"] = df["stock_code"] != ""
    df["dup"] = df["corp_name"].duplicated(keep=False)  # 같은 이름이 여럿이면 종목코드로 구분
    df = df.sort_values("listed", ascending=False, kind="stable").drop_duplicates("corp_name")
    df["key"] = [name_key(n) for n in df["corp_name"]]
    return df.reset_index(drop=True)


def suggest_corps(query, limit=SUGGEST_LIMIT):
    """못 찾은 기업명과 비슷한 기업 추천. [(버튼에 보일 이름, 조회에 쓸 값), ...]"""
    q = name_key(query)
    if not q:
        return []
    idx = get_name_index()
    keys = idx["key"]
    tier = pd.Series(3, index=idx.index)
    tier[keys.str.contains(q, regex=False)] = 2  # 이름 안에 포함 (SK -> SK하이닉스)
    tier[keys.str.startswith(q)] = 1
    tier[keys == q] = 0  # 영문/한글 표기만 다른 경우 (에스케이하이닉스 -> SK하이닉스)
    found = idx[tier < 3].assign(tier=tier[tier < 3], n=keys[tier < 3].str.len())
    found = found.sort_values(["tier", "listed", "n"], ascending=[True, False, True], kind="stable").head(limit)

    if found.empty:  # 오타 등: 상장사 중에서 글자가 비슷한 이름
        listed = idx[idx["listed"]]
        close = difflib.get_close_matches(q, list(listed["key"]), n=limit, cutoff=0.6)
        found = listed[listed["key"].isin(close)].assign(r=lambda d: d["key"].map(close.index))
        found = found.sort_values("r")

    out = []
    for _, r in found.iterrows():
        label = f"{r['corp_name']} ({r['stock_code']})" if r["listed"] else r["corp_name"]
        out.append((label, r["stock_code"] if r["dup"] and r["listed"] else r["corp_name"]))
    return out


def pick_suggestion(prefix, value):
    """추천 기업 버튼 클릭 -> 입력칸을 그 기업으로 바꾸고 바로 조회."""
    st.session_state[f"{prefix}_corp"] = value
    st.session_state[f"{prefix}_run"] = True


def lookup_corp_code(corp, prefix):
    """기업명(또는 종목코드) -> DART 고유번호. 못 찾으면 비슷한 기업을 추천하고 None."""
    with st.spinner("기업 정보를 확인하는 중입니다... (서버를 처음 켠 뒤 첫 조회는 몇 초 더 걸릴 수 있어요)"):
        corp_code = get_dart().find_corp_code(corp)
        suggestions = [] if corp_code else suggest_corps(corp)
    if not corp_code:
        if suggestions:
            st.warning(f'"{corp}"와 정확히 일치하는 기업이 없습니다. 혹시 이 기업인가요? 누르면 바로 조회합니다.')
            for row in range(0, len(suggestions), 4):
                cols = st.columns(4)
                for col, (i, (label, value)) in zip(cols, list(enumerate(suggestions))[row : row + 4]):
                    col.button(label, key=f"{prefix}_sugg_{i}", on_click=pick_suggestion, args=(prefix, value))
        else:
            st.error(
                f'"{corp}" 기업을 찾을 수 없습니다. '
                "기업명은 공시된 이름과 비슷하게 입력해 주세요(예: 삼성전자). 종목코드 6자리로도 조회할 수 있어요."
            )
    return corp_code


def render_financials():
    """탭 1: 매출액·영업이익 조회"""
    this_year = TODAY.year

    with st.form("search"):
        corp_text = st.text_input("기업명 또는 종목코드", value="삼성전자", key="fin_corp")
        period = st.radio("조회 단위", ["연도별", "분기별"], horizontal=True, key="fin_period")
        c1, c2 = st.columns(2)
        start = c1.number_input("시작연도", min_value=2015, max_value=this_year, value=2021, step=1, key="fin_start")
        end = c2.number_input("종료연도", min_value=2015, max_value=this_year, value=this_year - 1, step=1, key="fin_end")
        submitted = st.form_submit_button("조회") or st.session_state.pop("fin_run", False)

    if submitted:
        corp = corp_text.strip()
        if not corp:
            st.error("기업명을 입력해주세요.")
        elif start > end:
            st.error("시작연도가 종료연도보다 늦을 수 없습니다.")
        elif end - start + 1 > MAX_YEAR_SPAN:
            st.error(f"한 번에 최대 {MAX_YEAR_SPAN}년까지만 조회할 수 있습니다.")
        else:
            start, end = int(start), int(end)
            quarterly = period == "분기별"
            st.session_state.pop("result", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움

            corp_code = lookup_corp_code(corp, "fin")
            if corp_code:
                total = (end - start + 2) * (4 if quarterly else 1)  # 증감률 계산용 전년도 포함
                done = [0]
                progress = st.progress(0.0, text="조회 중입니다...")

                def tick():
                    done[0] += 1
                    progress.progress(min(done[0] / total, 1.0), text=f"조회 중입니다... ({done[0]}/{total})")

                collect = collect_quarterly if quarterly else collect_yearly
                try:
                    records = collect(corp_code, start, end, tick)
                except DartApiError as e:
                    progress.empty()
                    st.error(str(e))
                else:
                    progress.empty()
                    st.session_state["result"] = {"corp": corp, "records": records, "period": period}

    result = st.session_state.get("result")
    if not result:
        return

    if not result["records"]:
        st.warning("조회된 데이터가 없습니다. 기업명(종목코드)과 연도를 확인해주세요.")
        return

    table = build_table(result["records"], result["period"])
    st.caption(f"{result['corp']} · 단위: {UNIT}")
    # 스크롤 없이 전체가 보이도록 높이를 행 수에 맞춘다 (머리글 1줄 + 데이터 행, 한 줄당 약 35px)
    st.dataframe(
        style_table(table),
        hide_index=True,
        column_config={"원문": LINK_COLUMN},
        height=(len(table) + 1) * 35 + 3,
    )

    export = build_table(result["records"], result["period"], for_export=True)
    base_name = f"DART_{safe_filename(result['corp'])}_{result['period']}_{TODAY:%Y%m%d}"
    d1, d2, _ = st.columns([1, 1, 4])
    d1.download_button(
        "CSV 다운로드",
        export.to_csv(index=False).encode("utf-8-sig"),  # 엑셀에서 한글이 깨지지 않도록 utf-8-sig
        file_name=f"{base_name}.csv",
        mime="text/csv",
    )
    if HAS_XLSX:
        buffer = io.BytesIO()
        export.to_excel(buffer, index=False, engine="openpyxl")
        d2.download_button(
            "엑셀 다운로드",
            buffer.getvalue(),
            file_name=f"{base_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def render_disclosures():
    """탭 2: 공시 목록 (DART에서 기업명을 눌렀을 때 나오는 목록)"""
    first_day = date(1999, 1, 1)

    with st.form("list_search"):
        corp_text = st.text_input("기업명 또는 종목코드", value="삼성전자", key="list_corp")
        c0, c1, c2 = st.columns(3)
        kind_label = c0.selectbox("공시 유형", list(DISCLOSURE_TYPES), key="list_kind")
        start = c1.date_input(
            "시작일", value=TODAY - timedelta(days=183), min_value=first_day, max_value=TODAY, key="list_start"
        )
        end = c2.date_input("종료일", value=TODAY, min_value=first_day, max_value=TODAY, key="list_end")
        submitted = st.form_submit_button("공시 조회") or st.session_state.pop("list_run", False)

    if submitted:
        corp = corp_text.strip()
        if not corp:
            st.error("기업명을 입력해주세요.")
        elif start > end:
            st.error("시작일이 종료일보다 늦을 수 없습니다.")
        else:
            st.session_state.pop("disclosures", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움
            corp_code = lookup_corp_code(corp, "list")
            if corp_code:
                with st.spinner("공시 목록을 불러오는 중입니다..."):
                    try:
                        rows, total, truncated = collect_disclosures(
                            corp_code, start, end, DISCLOSURE_TYPES[kind_label]
                        )
                    except DartApiError as e:
                        st.error(str(e))
                    else:
                        st.session_state["disclosures"] = {
                            "corp": corp,
                            "kind": kind_label,
                            "rows": rows,
                            "total": total,
                            "truncated": truncated,
                            "start": start,
                            "end": end,
                        }

    res = st.session_state.get("disclosures")
    if not res:
        return

    if not res["rows"]:
        hint = " 공시 유형을 '전체'로 바꿔 보세요." if res["kind"] != "전체" else ""
        st.warning(f"조회된 공시가 없습니다. 기업명(종목코드)과 기간을 확인해주세요.{hint}")
        return

    name = res["rows"][0].get("corp_name") or res["corp"]
    notes = [name, f"{res['start']} ~ {res['end']}", f"총 {res['total']:,}건"]
    if res["kind"] != "전체":
        notes.append(f"유형: {res['kind']}")
    notes += ["최신순", "정정 공시 포함"]
    st.caption(" · ".join(notes))
    if res["truncated"]:
        st.warning(
            f"공시가 {res['total']:,}건이라 최신 {len(res['rows']):,}건만 보여드려요. "
            "기간을 줄이거나 공시 유형을 골라 다시 조회해보세요."
        )

    # 검색은 이미 가져온 목록 안에서 걸러내는 것이라 DART를 다시 부르지 않는다
    keyword = st.text_input(
        "보고서명 검색",
        key="list_keyword",
        placeholder="예: 잠정실적, 유상증자, 사업보고서 (공백으로 여러 단어를 넣으면 모두 포함된 공시만)",
    )
    shown = filter_by_keyword(res["rows"], keyword)
    if keyword.strip():
        st.caption(f"검색 결과 {len(shown):,}건 (불러온 {len(res['rows']):,}건 중)")
    if not shown:
        st.info("검색어와 일치하는 공시가 없습니다.")
        return

    st.dataframe(
        disclosure_table(shown),
        hide_index=True,
        column_config={"보고서명": st.column_config.TextColumn("보고서명", width="large"), "원문": LINK_COLUMN},
    )


STYLE = """
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css");

html, body, [class*="st-"], button, input, textarea {
    font-family: "Pretendard", -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
}
[data-testid="stAppViewContainer"] { background: #f6f8fb; }
[data-testid="stHeader"] { background: transparent; }
footer { visibility: hidden; }

.block-container { max-width: 1080px; padding-top: 2.5rem; padding-bottom: 4rem; }

h1 { font-weight: 800 !important; letter-spacing: -0.03em; color: #0b2545 !important; padding-bottom: 0.2rem; }
[data-testid="stCaptionContainer"] { color: #7a8699; }

/* 탭 */
[data-baseweb="tab-list"] { gap: 0.4rem; border-bottom: 1px solid #dde3ec; }
[data-baseweb="tab"] { font-weight: 600; padding: 0.6rem 1.1rem; }
[data-baseweb="tab-highlight"] { background-color: #1c7ed6 !important; height: 3px; }
[data-baseweb="tab"][aria-selected="true"] { color: #1c7ed6 !important; }

/* 입력 카드 */
[data-testid="stForm"] {
    background: #ffffff;
    border: 1px solid #e3e8ef;
    border-radius: 14px;
    padding: 1.4rem 1.6rem;
    box-shadow: 0 2px 10px rgba(16, 42, 84, 0.05);
}

/* 조회 버튼 */
[data-testid="stFormSubmitButton"] button {
    background: #1c7ed6;
    color: #ffffff;
    border: 0;
    border-radius: 10px;
    padding: 0.5rem 1.8rem;
    font-weight: 700;
}
[data-testid="stFormSubmitButton"] button:hover { background: #1864ab; color: #ffffff; }

/* 다운로드 버튼 */
[data-testid="stDownloadButton"] button {
    border-radius: 10px;
    border: 1px solid #ced7e3;
    background: #ffffff;
    font-weight: 600;
}
[data-testid="stDownloadButton"] button:hover { border-color: #1c7ed6; color: #1c7ed6; }

/* 표 */
[data-testid="stDataFrame"] {
    border: 1px solid #e3e8ef;
    border-radius: 12px;
    overflow: hidden;
    background: #ffffff;
}
</style>
"""

st.set_page_config(page_title="DART 재무·공시 조회", layout="wide")
st.markdown(STYLE, unsafe_allow_html=True)
st.title("DART 재무 · 공시 조회")
st.caption("제작: 중앙일보 산업부 나상현  \n출처: 금융감독원 전자공시시스템(DART)")  # 두 줄(줄바꿈 = 공백 2칸 + \n)

if not API_KEY:
    st.error(
        "DART API 키가 설정되지 않았습니다. "
        "Streamlit secrets(DART_API_KEY) 또는 환경변수 DART_API_KEY에 키를 등록해주세요."
    )
    st.stop()

tab_fin, tab_list = st.tabs(["재무 조회", "공시 목록"])
with tab_fin:
    render_financials()
with tab_list:
    render_disclosures()"""
DART 매출액 / 영업이익 조회 (Streamlit)

기능: 연도별·분기별 조회, 전년(동기)·전분기 대비 증감률,
      흑자전환/적자전환/적자지속 표시, 금액은 억원 단위,
      CSV·엑셀 다운로드, 공시 원문 링크, 공시 목록(유형 필터·보고서명 검색)

[설치]   pip install -r requirements.txt
[실행]   streamlit run app.py
"""

import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
import OpenDartReader

try:
    import openpyxl  # noqa: F401  (엑셀 다운로드에 필요)

    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False

# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------
FS_DIV = "CFS"  # CFS = 연결재무제표, OFS = 별도(개별)재무제표
UNIT = "억원"  # 금액 단위는 억원으로 고정
MAX_YEAR_SPAN = 10  # 한 번에 조회 가능한 최대 연도 수 (API 사용량 보호)
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
DART_API_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"  # 재무제표
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"  # 공시 목록

MAX_WORKERS = 6  # 동시에 보낼 DART 요청 수 (한 번 조회에 최대 44번이라 분당 한도에는 여유)
REQUEST_TIMEOUT = 20  # DART 응답을 기다리는 최대 시간(초)
CACHE_TTL = 6 * 3600  # 재무 조회 결과를 저장해두는 시간
EMPTY_TTL = 30 * 60  # '아직 공시 없음' 결과는 더 짧게 저장 (곧 공시될 수 있으므로)
LIST_TTL = 10 * 60  # 공시 목록은 새 공시가 계속 올라오므로 짧게 저장
LIST_PAGE_SIZE = 100  # DART 공시 목록 API가 한 번에 주는 최대 건수
MAX_LIST_PAGES = 10  # 공시 목록은 최대 1,000건까지만 표시

# 공시 유형 (DART 공시 목록 API의 pblntf_ty 값). 빈 문자열은 '전체'
DISCLOSURE_TYPES = {
    "전체": "",
    "정기공시": "A",
    "주요사항보고": "B",
    "발행공시": "C",
    "지분공시": "D",
    "기타공시": "E",
    "외부감사관련": "F",
    "펀드공시": "G",
    "자산유동화": "H",
    "거래소공시": "I",
    "공정위공시": "J",
}

KST = timezone(timedelta(hours=9))  # 서버가 해외에 있어도 한국 날짜 기준으로 동작
TODAY = datetime.now(KST).date()

# 표에서 눈에 띄게 보여줄 금액 열의 글자색 (밝은 테마·어두운 테마 모두에서 읽히는 중간 톤)
POSITIVE_COLOR = "#1c7ed6"  # 매출액·영업이익: 파랑
NEGATIVE_COLOR = "#e03131"  # 마이너스(적자 등): 빨강


def load_api_key():
    """API 키는 코드에 적지 않고 Streamlit secrets 또는 환경변수에서 읽습니다."""
    try:
        key = st.secrets["DART_API_KEY"]
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("DART_API_KEY")


API_KEY = load_api_key()

REVENUE_NAMES = ["매출액", "수익(매출액)", "영업수익"]
OPERATING_NAMES = ["영업이익", "영업이익(손실)", "영업손익", "영업손실", "영업손실(이익)"]
LOSS_ONLY_NAMES = ["영업손실"]  # 이 이름이면 값이 양수여도 손실로 처리

# 보고서 코드: 1분기 / 반기 / 3분기 / 사업보고서(연간)
Q1, H1, Q3, ANNUAL = "11013", "11012", "11014", "11011"


# ------------------------------------------------------------
# 데이터 조회 함수
# ------------------------------------------------------------
class DartApiError(Exception):
    """DART 서버 오류·호출 한도 초과 등 사용자에게 알려야 하는 조회 실패."""


@st.cache_resource
def get_dart():
    """기업명 → 고유번호 변환용. (처음 한 번 기업 목록을 내려받느라 몇 초 걸릴 수 있음)"""
    return OpenDartReader(API_KEY)


@st.cache_resource
def get_session():
    """연결을 재사용하는 HTTP 세션 (요청마다 새로 접속하는 시간을 줄임)."""
    session = requests.Session()
    session.mount("https://", HTTPAdapter(pool_maxsize=MAX_WORKERS))
    return session


@st.cache_resource
def get_cache_store():
    """{(고유번호, 연도, 보고서코드): (저장시각, 데이터)} - 서버가 켜져 있는 동안 유지."""
    return {}


KEEP_COLUMNS = ["rcept_no", "sj_div", "account_nm", "thstrm_amount", "thstrm_add_amount"]


def slim(df):
    """필요한 계정(매출·영업이익)과 열만 남겨 저장 용량을 줄인다. 없으면 None."""
    if df is None or df.empty or "account_nm" not in df.columns:
        return None
    rows = df[df["account_nm"].str.strip().isin(REVENUE_NAMES + OPERATING_NAMES)]
    if rows.empty:
        return None
    return rows[[c for c in KEEP_COLUMNS if c in rows.columns]].reset_index(drop=True)


_MISSING = object()  # '저장된 값 없음' 표시 (None은 '조회했더니 데이터 없음'이라는 뜻이라 구분 필요)


def cache_lookup(store, key, ttl):
    """저장된 값이 유효하면 그 값을, 아니면 _MISSING을 돌려준다. 데이터 없음(None)은 짧게만 저장."""
    hit = store.get(key)
    if hit is None:
        return _MISSING
    saved_at, value = hit
    if time.time() - saved_at < (ttl if value is not None else EMPTY_TTL):
        return value
    return _MISSING


def cache_save(store, key, value):
    if len(store) > 2000:  # 메모리 보호
        store.clear()
    store[key] = (time.time(), value)


def call_dart(session, url, params):
    """DART API 호출 공통 처리.

    정상이면 응답(dict), 조회된 데이터가 없으면(013) None,
    응답 없음·호출 한도 초과·키 오류 등은 DartApiError.
    """
    jo, last_error = None, None
    for attempt in range(3):  # 일시적인 네트워크 오류는 두 번까지 다시 시도
        try:
            jo = session.get(url, params=params, timeout=REQUEST_TIMEOUT).json()
            break
        except Exception as e:  # 시간 초과, 연결 끊김, JSON 오류
            last_error = e
            time.sleep(0.5 * (attempt + 1))
    if jo is None:
        raise DartApiError("DART 서버 응답이 없거나 늦어지고 있습니다. 잠시 후 다시 시도해주세요.") from last_error

    status = str(jo.get("status", ""))
    if "list" in jo:
        return jo
    if status == "013":  # 조회된 데이터 없음 (아직 공시 전이거나 해당 보고서 없음)
        return None
    if status == "020":
        raise DartApiError("DART API 하루 호출 한도를 초과했습니다. 잠시 후 또는 내일 다시 시도해주세요.")
    raise DartApiError(f"DART 응답 오류 [{status}] {jo.get('message', '')}".strip())


def fetch(session, store, corp_code, year, reprt_code):
    """보고서 한 건 조회. 없으면 None, DART 오류·응답 없음이면 DartApiError.

    ※ 작업 스레드에서 실행되므로 st.* 함수를 쓰지 않고, 세션·저장소는 인자로 받는다.
    """
    key = (corp_code, year, reprt_code)
    cached = cache_lookup(store, key, CACHE_TTL)
    if cached is not _MISSING:
        return cached

    params = {
        "crtfc_key": API_KEY,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": reprt_code,
        "fs_div": FS_DIV,
    }
    jo = call_dart(session, DART_API_URL, params)
    df = slim(pd.DataFrame(jo["list"])) if jo else None
    cache_save(store, key, df)
    return df


def fetch_disclosure_page(session, store, corp_code, bgn, end, page, kind=""):
    """공시 목록 한 페이지(최대 100건). 없으면 None. kind는 공시 유형 코드(A~J), 빈 값이면 전체."""
    key = ("list", corp_code, bgn, end, page, kind)
    cached = cache_lookup(store, key, LIST_TTL)
    if cached is not _MISSING:
        return cached

    params = {
        "crtfc_key": API_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn,  # 시작일 YYYYMMDD
        "end_de": end,  # 종료일 YYYYMMDD
        "last_reprt_at": "N",  # N = 정정 보고서까지 모두 (DART 사이트 목록과 같은 방식)
        "page_no": page,
        "page_count": LIST_PAGE_SIZE,
    }
    if kind:
        params["pblntf_ty"] = kind
    jo = call_dart(session, DART_LIST_URL, params)
    cache_save(store, key, jo)
    return jo


def collect_disclosures(corp_code, start, end, kind=""):
    """기간 내 공시 목록(최신순). (행 목록, 전체 건수, 잘림 여부)를 돌려준다."""
    session, store = get_session(), get_cache_store()
    bgn, fin = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    first = fetch_disclosure_page(session, store, corp_code, bgn, fin, 1, kind)
    if first is None:
        return [], 0, False

    total_count = int(first.get("total_count", len(first["list"])))
    total_page = int(first.get("total_page", 1))
    last_page = min(total_page, MAX_LIST_PAGES)

    pages = {1: first}
    if last_page > 1:  # 나머지 페이지는 동시에 조회
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(fetch_disclosure_page, session, store, corp_code, bgn, fin, p, kind): p
                for p in range(2, last_page + 1)
            }
            try:
                for future in as_completed(futures):
                    pages[futures[future]] = future.result()
            except Exception:
                for f in futures:
                    f.cancel()
                raise

    rows = []
    for p in range(1, last_page + 1):
        if pages.get(p):
            rows.extend(pages[p]["list"])
    return rows, total_count, total_page > MAX_LIST_PAGES


def fetch_many(corp_code, tasks, tick):
    """(연도, 보고서코드) 목록을 동시에 조회해 {(연도, 보고서코드): 데이터}로 돌려준다."""
    session, store = get_session(), get_cache_store()  # 화면 스레드에서 미리 준비
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch, session, store, corp_code, y, c): (y, c) for (y, c) in tasks}
        try:
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                tick()
        except Exception:
            for f in futures:  # 아직 시작 안 한 요청은 취소
                f.cancel()
            raise
    return results


def to_int(x):
    """문자열 금액을 정수로 변환. 음수 표기(괄호, -, △, ▲)를 모두 마이너스로 처리."""
    s = str(x).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "nan", "None"):
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):  # (1234) 형태
        negative = True
        s = s[1:-1]
    if s and s[0] in ("△", "▲", "-", "−"):  # △1234, ▲1234, -1234
        negative = True
        s = s[1:]
    try:
        value = int(s)
    except ValueError:
        return None
    return -value if negative else value


def pick(df, names, column="thstrm_amount"):
    """손익계산서에서 계정과목(names 중 하나)의 금액을 뽑아냄."""
    if df is None or column not in df.columns:
        return None
    rows = df[df["account_nm"].str.strip().isin(names)]
    if "sj_div" in rows.columns:
        is_rows = rows[rows["sj_div"].isin(["IS", "CIS"])]
        if not is_rows.empty:
            rows = is_rows
    if rows.empty:
        return None
    row = rows.iloc[0]
    value = to_int(row[column])
    # 계정명이 '영업손실'인데 양수로 적혀 있으면 손실(마이너스)로 본다
    if value is not None and value > 0 and str(row["account_nm"]).strip() in LOSS_ONLY_NAMES:
        value = -value
    return value


def link_of(df):
    """해당 보고서의 DART 원문 링크. 접수번호가 없으면 None."""
    if df is None or "rcept_no" not in df.columns or df.empty:
        return None
    rcept_no = str(df["rcept_no"].iloc[0]).strip()
    return DART_VIEW_URL.format(rcept_no) if rcept_no else None


# ------------------------------------------------------------
# 증감률 계산
# ------------------------------------------------------------
def pct_change(cur, prev):
    """증감률(%). 비교 값이 없거나 0이면 None."""
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100


def op_change(cur, prev):
    """영업이익 증감: 퍼센트(float) 또는 '흑자전환/적자전환/적자지속' 문구(str)."""
    if cur is None or prev is None:
        return None
    if prev <= 0 and cur > 0:
        return "흑자전환"
    if prev >= 0 and cur < 0:
        return "적자전환"
    if prev < 0 and cur < 0:
        return "적자지속"
    if prev > 0:
        return pct_change(cur, prev)
    return None


def make_record(label, sort_key, cur, yoy=None, qoq=None):
    rev, op = cur["rev"], cur["op"]
    return {
        "label": label,
        "sort": sort_key,
        "rev": rev,
        "op": op,
        "rev_yoy": pct_change(rev, yoy["rev"]) if yoy else None,
        "op_yoy": op_change(op, yoy["op"]) if yoy else None,
        "rev_qoq": pct_change(rev, qoq["rev"]) if qoq else None,
        "op_qoq": op_change(op, qoq["op"]) if qoq else None,
        "link": cur["link"],
    }


# ------------------------------------------------------------
# 연도별 / 분기별 수집
# ------------------------------------------------------------
def collect_yearly(corp_code, start, end, tick):
    """증감률 계산을 위해 시작연도 전년도까지 함께 조회한다."""
    years = range(start - 1, end + 1)
    reports = fetch_many(corp_code, [(y, ANNUAL) for y in years], tick)

    data = {}
    for y in years:
        df = reports[(y, ANNUAL)]
        data[y] = {"rev": pick(df, REVENUE_NAMES), "op": pick(df, OPERATING_NAMES), "link": link_of(df)}

    records = []
    for y in range(start, end + 1):
        cur = data[y]
        if cur["rev"] is None and cur["op"] is None:
            continue
        records.append(make_record(str(y), (y, 0), cur, yoy=data[y - 1]))
    return records


def quarter_values(r1, r2, r3, r4, names):
    """1~4분기 값을 계산. 4분기 = 연간 - 3분기 누적."""
    q1 = pick(r1, names)
    q2 = pick(r2, names)
    q3 = pick(r3, names)
    annual = pick(r4, names)

    cum9 = pick(r3, names, "thstrm_add_amount")  # 3분기 누적(9개월)
    if cum9 is None and None not in (q1, q2, q3):
        cum9 = q1 + q2 + q3
    q4 = annual - cum9 if annual is not None and cum9 is not None else None
    return [q1, q2, q3, q4]


def collect_quarterly(corp_code, start, end, tick):
    years = range(start - 1, end + 1)
    codes = (Q1, H1, Q3, ANNUAL)
    fetched = fetch_many(corp_code, [(y, c) for y in years for c in codes], tick)

    data = {}
    for y in years:
        reports = [fetched[(y, c)] for c in codes]
        revs = quarter_values(*reports, REVENUE_NAMES)
        ops = quarter_values(*reports, OPERATING_NAMES)
        for i in range(4):  # 1~3분기는 각 분기 보고서, 4분기는 사업보고서로 연결
            data[(y, i + 1)] = {"rev": revs[i], "op": ops[i], "link": link_of(reports[i])}

    records = []
    for y in range(start, end + 1):
        for q in range(1, 5):
            cur = data[(y, q)]
            if cur["rev"] is None and cur["op"] is None:
                continue
            qoq = data[(y, q - 1)] if q > 1 else data[(y - 1, 4)]
            records.append(make_record(f"{y} {q}Q", (y, q), cur, yoy=data[(y - 1, q)], qoq=qoq))
    return records


# ------------------------------------------------------------
# 표 만들기 (화면용 / 다운로드용)
# ------------------------------------------------------------
def fmt_amount(v):
    """원 -> 억원 (천 단위 쉼표). 값이 없으면 '-'."""
    if v is None:
        return "-"
    return f"{v / 1e8:,.0f}"


def fmt_change(c):
    if c is None:
        return "-"
    if isinstance(c, str):
        return c
    return f"{c:+,.1f}%"


def num_amount(v):
    """다운로드 파일용: 원 -> 억원(숫자). 값이 없으면 None."""
    if v is None:
        return None
    return int(round(v / 1e8))


def amount_style(cell):
    """금액 칸 스타일: 기본 파랑 + 굵게, 마이너스(앞에 '-')면 빨강 + 굵게."""
    color = NEGATIVE_COLOR if str(cell).startswith("-") and str(cell) != "-" else POSITIVE_COLOR
    return f"font-weight: bold; color: {color}"


def style_table(table):
    """매출액·영업이익 열에만 색과 굵기를 입힌다."""
    return table.style.map(amount_style, subset=["매출액", "영업이익"])


def sorted_records(records):
    # 오래된 기간이 위로 (2023 → 2024 → 2025). 최신이 위로 오게 하려면 reverse=True를 추가
    return sorted(records, key=lambda r: r["sort"])


def build_table(records, period, for_export=False):
    quarterly = period == "분기별"
    yoy = "전년동기比" if quarterly else "전년比"
    amt = num_amount if for_export else fmt_amount

    rows = []
    for r in sorted_records(records):
        row = {}
        row["기간"] = r["label"]
        row["매출액"] = amt(r["rev"])
        row[f"매출 {yoy}"] = fmt_change(r["rev_yoy"])
        if quarterly:
            row["매출 전분기比"] = fmt_change(r["rev_qoq"])
        row["영업이익"] = amt(r["op"])
        row[f"영업이익 {yoy}"] = fmt_change(r["op_yoy"])
        if quarterly:
            row["영업이익 전분기比"] = fmt_change(r["op_qoq"])
        row["원문"] = r["link"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if for_export:
        df = df.rename(
            columns={"매출액": f"매출액({UNIT})", "영업이익": f"영업이익({UNIT})", "원문": "공시원문링크"}
        )
    return df


def safe_filename(text):
    """파일 이름에 쓸 수 없는 문자(공백, 슬래시 등)를 밑줄로 바꾼다."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "기업"


def filter_by_keyword(rows, keyword):
    """보고서명에 검색어가 모두 들어 있는 공시만 남긴다. 공백으로 여러 단어를 넣으면 모두 포함된 것만(대소문자 무시)."""
    terms = keyword.lower().split()
    if not terms:
        return rows
    return [r for r in rows if all(t in str(r.get("report_nm", "")).lower() for t in terms)]


def disclosure_table(rows):
    """공시 목록 API 응답(rows)을 화면용 표로 바꾼다."""
    df = pd.DataFrame(rows).reindex(columns=["rcept_dt", "report_nm", "flr_nm", "rcept_no"]).fillna("")
    dates = pd.to_datetime(df["rcept_dt"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    return pd.DataFrame(
        {
            "접수일": dates,
            "보고서명": df["report_nm"].astype(str).str.strip(),
            "제출인": df["flr_nm"],
            "원문": df["rcept_no"].astype(str).map(lambda n: DART_VIEW_URL.format(n) if n else None),
        }
    )


# ------------------------------------------------------------
# 화면
# ------------------------------------------------------------
LINK_COLUMN = st.column_config.LinkColumn("원문", display_text="공시 보기")


def lookup_corp_code(corp):
    """기업명(또는 종목코드) -> DART 고유번호. 못 찾으면 안내 문구를 띄우고 None."""
    with st.spinner("기업 정보를 확인하는 중입니다... (서버를 처음 켠 뒤 첫 조회는 몇 초 더 걸릴 수 있어요)"):
        corp_code = get_dart().find_corp_code(corp)
    if not corp_code:
        st.error(
            f'"{corp}" 기업을 찾을 수 없습니다. '
            "기업명은 공시된 이름과 정확히 같아야 합니다(예: 삼성전자). 종목코드 6자리로도 조회할 수 있어요."
        )
    return corp_code


def render_financials():
    """탭 1: 매출액·영업이익 조회"""
    this_year = TODAY.year

    with st.form("search"):
        corp_text = st.text_input("기업명 또는 종목코드", value="삼성전자")
        period = st.radio("조회 단위", ["연도별", "분기별"], horizontal=True)
        c1, c2 = st.columns(2)
        start = c1.number_input("시작연도", min_value=2015, max_value=this_year, value=2021, step=1)
        end = c2.number_input("종료연도", min_value=2015, max_value=this_year, value=this_year - 1, step=1)
        submitted = st.form_submit_button("조회")

    if submitted:
        corp = corp_text.strip()
        if not corp:
            st.error("기업명을 입력해주세요.")
        elif start > end:
            st.error("시작연도가 종료연도보다 늦을 수 없습니다.")
        elif end - start + 1 > MAX_YEAR_SPAN:
            st.error(f"한 번에 최대 {MAX_YEAR_SPAN}년까지만 조회할 수 있습니다.")
        else:
            start, end = int(start), int(end)
            quarterly = period == "분기별"
            st.session_state.pop("result", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움

            corp_code = lookup_corp_code(corp)
            if corp_code:
                total = (end - start + 2) * (4 if quarterly else 1)  # 증감률 계산용 전년도 포함
                done = [0]
                progress = st.progress(0.0, text="조회 중입니다...")

                def tick():
                    done[0] += 1
                    progress.progress(min(done[0] / total, 1.0), text=f"조회 중입니다... ({done[0]}/{total})")

                collect = collect_quarterly if quarterly else collect_yearly
                try:
                    records = collect(corp_code, start, end, tick)
                except DartApiError as e:
                    progress.empty()
                    st.error(str(e))
                else:
                    progress.empty()
                    st.session_state["result"] = {"corp": corp, "records": records, "period": period}

    result = st.session_state.get("result")
    if not result:
        return

    if not result["records"]:
        st.warning("조회된 데이터가 없습니다. 기업명(종목코드)과 연도를 확인해주세요.")
        return

    table = build_table(result["records"], result["period"])
    st.caption(f"{result['corp']} · 단위: {UNIT}")
    # 스크롤 없이 전체가 보이도록 높이를 행 수에 맞춘다 (머리글 1줄 + 데이터 행, 한 줄당 약 35px)
    st.dataframe(
        style_table(table),
        hide_index=True,
        column_config={"원문": LINK_COLUMN},
        height=(len(table) + 1) * 35 + 3,
    )

    export = build_table(result["records"], result["period"], for_export=True)
    base_name = f"DART_{safe_filename(result['corp'])}_{result['period']}_{TODAY:%Y%m%d}"
    d1, d2, _ = st.columns([1, 1, 4])
    d1.download_button(
        "CSV 다운로드",
        export.to_csv(index=False).encode("utf-8-sig"),  # 엑셀에서 한글이 깨지지 않도록 utf-8-sig
        file_name=f"{base_name}.csv",
        mime="text/csv",
    )
    if HAS_XLSX:
        buffer = io.BytesIO()
        export.to_excel(buffer, index=False, engine="openpyxl")
        d2.download_button(
            "엑셀 다운로드",
            buffer.getvalue(),
            file_name=f"{base_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def render_disclosures():
    """탭 2: 공시 목록 (DART에서 기업명을 눌렀을 때 나오는 목록)"""
    first_day = date(1999, 1, 1)

    with st.form("list_search"):
        corp_text = st.text_input("기업명 또는 종목코드", value="삼성전자", key="list_corp")
        c0, c1, c2 = st.columns(3)
        kind_label = c0.selectbox("공시 유형", list(DISCLOSURE_TYPES), key="list_kind")
        start = c1.date_input(
            "시작일", value=TODAY - timedelta(days=183), min_value=first_day, max_value=TODAY, key="list_start"
        )
        end = c2.date_input("종료일", value=TODAY, min_value=first_day, max_value=TODAY, key="list_end")
        submitted = st.form_submit_button("공시 조회")

    if submitted:
        corp = corp_text.strip()
        if not corp:
            st.error("기업명을 입력해주세요.")
        elif start > end:
            st.error("시작일이 종료일보다 늦을 수 없습니다.")
        else:
            st.session_state.pop("disclosures", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움
            corp_code = lookup_corp_code(corp)
            if corp_code:
                with st.spinner("공시 목록을 불러오는 중입니다..."):
                    try:
                        rows, total, truncated = collect_disclosures(
                            corp_code, start, end, DISCLOSURE_TYPES[kind_label]
                        )
                    except DartApiError as e:
                        st.error(str(e))
                    else:
                        st.session_state["disclosures"] = {
                            "corp": corp,
                            "kind": kind_label,
                            "rows": rows,
                            "total": total,
                            "truncated": truncated,
                            "start": start,
                            "end": end,
                        }

    res = st.session_state.get("disclosures")
    if not res:
        return

    if not res["rows"]:
        hint = " 공시 유형을 '전체'로 바꿔 보세요." if res["kind"] != "전체" else ""
        st.warning(f"조회된 공시가 없습니다. 기업명(종목코드)과 기간을 확인해주세요.{hint}")
        return

    name = res["rows"][0].get("corp_name") or res["corp"]
    notes = [name, f"{res['start']} ~ {res['end']}", f"총 {res['total']:,}건"]
    if res["kind"] != "전체":
        notes.append(f"유형: {res['kind']}")
    notes += ["최신순", "정정 공시 포함"]
    st.caption(" · ".join(notes))
    if res["truncated"]:
        st.warning(
            f"공시가 {res['total']:,}건이라 최신 {len(res['rows']):,}건만 보여드려요. "
            "기간을 줄이거나 공시 유형을 골라 다시 조회해보세요."
        )

    # 검색은 이미 가져온 목록 안에서 걸러내는 것이라 DART를 다시 부르지 않는다
    keyword = st.text_input(
        "보고서명 검색",
        key="list_keyword",
        placeholder="예: 잠정실적, 유상증자, 사업보고서 (공백으로 여러 단어를 넣으면 모두 포함된 공시만)",
    )
    shown = filter_by_keyword(res["rows"], keyword)
    if keyword.strip():
        st.caption(f"검색 결과 {len(shown):,}건 (불러온 {len(res['rows']):,}건 중)")
    if not shown:
        st.info("검색어와 일치하는 공시가 없습니다.")
        return

    st.dataframe(
        disclosure_table(shown),
        hide_index=True,
        column_config={"보고서명": st.column_config.TextColumn("보고서명", width="large"), "원문": LINK_COLUMN},
    )


STYLE = """
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css");

html, body, [class*="st-"], button, input, textarea {
    font-family: "Pretendard", -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
}
[data-testid="stAppViewContainer"] { background: #f6f8fb; }
[data-testid="stHeader"] { background: transparent; }
footer { visibility: hidden; }

.block-container { max-width: 1080px; padding-top: 2.5rem; padding-bottom: 4rem; }

h1 { font-weight: 800 !important; letter-spacing: -0.03em; color: #0b2545 !important; padding-bottom: 0.2rem; }
[data-testid="stCaptionContainer"] { color: #7a8699; }

/* 탭 */
[data-baseweb="tab-list"] { gap: 0.4rem; border-bottom: 1px solid #dde3ec; }
[data-baseweb="tab"] { font-weight: 600; padding: 0.6rem 1.1rem; }
[data-baseweb="tab-highlight"] { background-color: #1c7ed6 !important; height: 3px; }
[data-baseweb="tab"][aria-selected="true"] { color: #1c7ed6 !important; }

/* 입력 카드 */
[data-testid="stForm"] {
    background: #ffffff;
    border: 1px solid #e3e8ef;
    border-radius: 14px;
    padding: 1.4rem 1.6rem;
    box-shadow: 0 2px 10px rgba(16, 42, 84, 0.05);
}

/* 조회 버튼 */
[data-testid="stFormSubmitButton"] button {
    background: #1c7ed6;
    color: #ffffff;
    border: 0;
    border-radius: 10px;
    padding: 0.5rem 1.8rem;
    font-weight: 700;
}
[data-testid="stFormSubmitButton"] button:hover { background: #1864ab; color: #ffffff; }

/* 다운로드 버튼 */
[data-testid="stDownloadButton"] button {
    border-radius: 10px;
    border: 1px solid #ced7e3;
    background: #ffffff;
    font-weight: 600;
}
[data-testid="stDownloadButton"] button:hover { border-color: #1c7ed6; color: #1c7ed6; }

/* 표 */
[data-testid="stDataFrame"] {
    border: 1px solid #e3e8ef;
    border-radius: 12px;
    overflow: hidden;
    background: #ffffff;
}
</style>
"""

st.set_page_config(page_title="DART 재무·공시 조회", layout="wide")
st.markdown(STYLE, unsafe_allow_html=True)
st.title("DART 재무 · 공시 조회")
st.caption("제작: 중앙일보 산업부 나상현  \n출처: 금융감독원 전자공시시스템(DART)")  # 두 줄(줄바꿈 = 공백 2칸 + \n)

if not API_KEY:
    st.error(
        "DART API 키가 설정되지 않았습니다. "
        "Streamlit secrets(DART_API_KEY) 또는 환경변수 DART_API_KEY에 키를 등록해주세요."
    )
    st.stop()

tab_fin, tab_list = st.tabs(["재무 조회", "공시 목록"])
with tab_fin:
    render_financials()
with tab_list:
    render_disclosures()
