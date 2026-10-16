"""
DART 매출액 / 영업이익 조회 (Streamlit)

기능: 연도별·분기별 조회, 전년(동기)·전분기 대비 증감률,
      흑자전환/적자전환/적자지속 표시, 금액은 억원 단위,
      CSV·엑셀 다운로드, 공시 원문 링크,
      공시 목록(유형 필터·주요 공시 버튼·보고서명 검색), 오늘의 공시(전 상장사 주요 공시 모아보기),
      단독 뉴스(기업명·섹터 검색, 비슷한 기사 묶기)

[설치]   pip install -r requirements.txt
[실행]   streamlit run app.py
"""

import difflib
import html
import io
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

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
UNITS = {"억원": (1e8, 0), "조원": (1e12, 2)}  # 단위: (나눌 값, 소수점 자리). 첫 번째가 기본값
DEFAULT_UNIT = "억원"
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
MAX_FEED_PAGES = 30  # 오늘의 공시는 시장마다 최대 3,000건까지만 확인
FEED_MARKETS = {"코스피+코스닥": ["Y", "K"], "코스피": ["Y"], "코스닥": ["K"]}  # DART 법인구분: Y 코스피, K 코스닥
CLS_NAMES = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타"}

# 주요 공시 묶음. 보고서명에 아래 단어 중 하나가 들어 있으면 그 묶음에 속한다 (공백·괄호·가운뎃점은 무시하고 비교).
# '공시 목록' 탭의 주요 공시 버튼과 '오늘의 공시' 탭이 함께 쓴다. 자유롭게 추가·수정하세요.
MAJOR_DISCLOSURES = {
    "영업실적": ["영업(잠정)실적", "영업실적", "잠정실적", "손익구조"],
    "공급계약": ["공급계약", "판매계약"],
    "증자·감자": ["유상증자", "무상증자", "감자"],
    "합병·분할": ["합병", "분할", "주식교환", "주식이전"],
    "자기주식": ["자기주식"],
    "지분 변동": ["대량보유", "소유상황", "최대주주", "소유주식변동"],
}

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

# 뉴스 (네이버 뉴스 검색 API)
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
NEWS_PAGE_SIZE = 100  # 한 번에 받는 최대 기사 수
MAX_NEWS_PAGES = 10  # 최대 1,000건까지만 확인 (네이버가 한 검색에서 주는 최대치)
NEW_HOURS = 3  # 올라온 지 이 시간 이내인 단독 기사에 NEW 표시
NEWS_TTL = 5 * 60  # 같은 검색은 5분 동안 저장해 둔 결과를 재사용
SIMILAR_TITLE = 0.8  # 제목이 이 정도(0~1) 이상 비슷하면 같은 기사로 묶는다
NEWS_PERIODS = {"오늘": 1, "3일": 3, "일주일": 7, "한 달": 30}

# 뉴스에 보여줄 언론사. 기사 주소(도메인) -> (언론사 이름, 구분). 여기 없는 매체의 기사는 보이지 않는다.
NEWS_CATEGORIES = ["종합일간지", "경제지", "통신사"]
MAX_NEWS_CORPS = 5  # 기업명 칸에 한 번에 넣을 수 있는 기업 수 (많을수록 조회가 느려짐)
NEWS_SPLIT = r"[,，、/;\n]+"  # 여러 기업을 나누는 기호: 쉼표, 슬래시 등 (띄어쓰기는 'LG 에너지솔루션'처럼 이름 안에 있을 수 있어 제외)
NEWS_KEYWORD = "단독"  # 뉴스 탭은 항상 이 표기가 제목에 있는 기사만 보여준다 (화면에는 드러내지 않고 내부에서 적용)
# 이 기업명을 입력하면 아래 별칭도 함께 검색한다 (제목에 줄임말로 쓰이는 경우를 잡기 위함). 자유롭게 추가·수정하세요.
# 별칭 없이 기업명만 입력하면 그 이름만 검색합니다. 예: '삼성'을 입력하면 '삼성'만, '삼성전자'를 입력하면 삼성전자·삼성·삼전.
NEWS_ALIASES = {
    "삼성전자": ["삼성", "삼전"],
    "현대자동차": ["현대차", "현차"],
    "현대차": ["현차"],
    "SK하이닉스": ["하닉"],
    "LG에너지솔루션": ["LG엔솔", "엔솔"],
    "LG엔솔": ["LG에너지솔루션", "엔솔"],
    "삼성바이오로직스": ["삼바"],
    "삼성바이오": ["삼성바이오로직스", "삼바"],
    "SK텔레콤": ["SKT"],
    "SKT": ["SK텔레콤"],
    "SK이노베이션": ["SK이노"],
    "한국전력공사": ["한전"],
    "한국전력": ["한전"],
    "POSCO홀딩스": ["포스코홀딩스", "포스코"],
    "포스코홀딩스": ["POSCO홀딩스", "포스코"],
    "한화에어로스페이스": ["한화에어로"],
    "한화에어로": ["한화에어로스페이스"],
    "한국항공우주산업": ["KAI"],
    "KAI": ["한국항공우주산업"],
    "HD한국조선해양": ["한국조선해양", "한조"],
    "한국조선해양": ["HD한국조선해양", "한조"],
    "삼성화재해상보험": ["삼성화재"],
    "삼성화재": ["삼성화재해상보험"],
    "현대모비스": ["모비스"],
    "모비스": ["현대모비스"],
    "신한지주": ["신한금융", "신한금융지주"],
    "신한금융지주": ["신한지주", "신한금융"],
    "신한금융": ["신한지주", "신한금융지주"],
    "하나금융지주": ["하나금융"],
    "하나금융": ["하나금융지주"],
    "우리금융지주": ["우리금융"],
    "우리금융": ["우리금융지주"],
    "KB금융": ["KB금융지주", "KB금융그룹"],
    "KB금융지주": ["KB금융", "KB금융그룹"],
    "NAVER": ["네이버"],
    "네이버": ["NAVER"],
    "아모레퍼시픽": ["아모레"],
    "한국타이어앤테크놀로지": ["한국타이어"],
    "한국타이어": ["한국타이어앤테크놀로지"],
    "기업은행": ["IBK기업은행", "IBK"],
    "IBK기업은행": ["기업은행", "IBK"],
}
# 섹터 모드: 섹터를 고르면 아래 검색어를 하나씩 '검색어 단독'으로 검색해 합친다.
# '기업'은 대표 기업·줄임말, '키워드'는 기업명 없이 산업 이슈로 나오는 기사를 잡기 위한 단어.
# 제목에 이 검색어 중 하나가 들어간 기사만 보여줍니다. 너무 넓은 단어는 잡음이 늘어나니 꼭 필요한 것만 넣으세요.
NEWS_SECTORS = {
    "반도체": {
        "기업": ["삼성전자", "삼전", "SK하이닉스", "하닉", "한미반도체", "DB하이텍"],
        "키워드": ["HBM", "파운드리", "반도체"],
    },
    "자동차": {
        "기업": ["현대차", "기아", "현대모비스", "한국GM", "KG모빌리티"],
        "키워드": ["전기차", "완성차"],
    },
    "배터리": {
        "기업": ["LG에너지솔루션", "LG엔솔", "엔솔", "삼성SDI", "SK온", "에코프로비엠", "포스코퓨처엠"],
        "키워드": ["2차전지", "배터리"],
    },
    "철강": {
        "기업": ["포스코", "현대제철", "동국제강", "세아제강"],
        "키워드": ["철강", "후판", "열연"],
    },
    "방산·항공우주": {
        "기업": ["한화에어로스페이스", "KAI", "LIG넥스원", "현대로템"],
        "키워드": ["방산", "K방산", "수출 계약"],
    },
    "조선·해운": {
        "기업": ["HD한국조선해양", "한화오션", "삼성중공업", "HMM"],
        "키워드": ["조선", "LNG선", "수주"],
    },
    "석유화학·정유": {
        "기업": ["롯데케미칼", "LG화학", "S-Oil", "GS칼텍스", "SK이노베이션"],
        "키워드": ["석유화학", "나프타", "정유"],
    },
}
PUBLISHERS = {
    # 종합일간지
    "joongang.co.kr": ("중앙일보", "종합일간지"), "chosun.com": ("조선일보", "종합일간지"),
    "donga.com": ("동아일보", "종합일간지"), "hani.co.kr": ("한겨레", "종합일간지"),
    "khan.co.kr": ("경향신문", "종합일간지"), "hankookilbo.com": ("한국일보", "종합일간지"),
    "munhwa.com": ("문화일보", "종합일간지"), "segye.com": ("세계일보", "종합일간지"),
    "kmib.co.kr": ("국민일보", "종합일간지"), "seoul.co.kr": ("서울신문", "종합일간지"),
    # 경제지
    "hankyung.com": ("한국경제", "경제지"), "mk.co.kr": ("매일경제", "경제지"),
    "sedaily.com": ("서울경제", "경제지"), "edaily.co.kr": ("이데일리", "경제지"),
    "mt.co.kr": ("머니투데이", "경제지"), "fnnews.com": ("파이낸셜뉴스", "경제지"),
    "heraldcorp.com": ("헤럴드경제", "경제지"), "asiae.co.kr": ("아시아경제", "경제지"),
    "etnews.com": ("전자신문", "경제지"), "biz.chosun.com": ("조선비즈", "경제지"),  # biz.chosun.com이 chosun.com보다 우선
    # 통신사
    "yna.co.kr": ("연합뉴스", "통신사"), "newsis.com": ("뉴시스", "통신사"), "news1.kr": ("뉴스1", "통신사"),
}

LAST_UPDATED = "2026.10.16"  # 화면 아래 크레딧의 updated 날짜. 앱을 고칠 때마다 직접 바꿔주세요

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


def load_secret(name):
    """선택 사항인 키(네이버 API 등). 없으면 None."""
    try:
        value = st.secrets[name]
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(name)


NAVER_ID = load_secret("NAVER_CLIENT_ID")
NAVER_SECRET = load_secret("NAVER_CLIENT_SECRET")

REVENUE_NAMES = ["매출액", "수익(매출액)", "영업수익"]
OPERATING_NAMES = ["영업이익", "영업이익(손실)", "영업손익", "영업손실", "영업손실(이익)"]
NET_NAMES = [
    "당기순이익", "당기순이익(손실)", "당기순손익", "당기순손실", "당기순손실(이익)", "연결당기순이익",
    "분기순이익", "분기순이익(손실)", "분기순손실", "반기순이익", "반기순이익(손실)", "반기순손실",
    "당기순이익(순손실)", "분기순이익(순손실)", "반기순이익(순손실)", "분기순손익", "반기순손익",
    "연결분기순이익", "연결반기순이익", "연결당기순이익(손실)",
]
NET_IDS = ["ifrs-full_ProfitLoss", "ifrs_ProfitLoss"]  # 당기순이익의 표준 계정 코드 (이름이 회사마다 달라도 잡힘)
LOSS_ONLY_NAMES = ["영업손실", "당기순손실", "분기순손실", "반기순손실"]  # 이 이름이면 값이 양수여도 손실로 처리

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


# 저장해 두는 데이터의 모양이 바뀌면(예: 순이익 추가) 이 숫자를 올린다.
# 그래야 앱을 고친 직후에도 예전 형식으로 저장된 결과(순이익 없음)를 다시 쓰지 않는다.
CACHE_VERSION = 2


@st.cache_resource
def _cache_store_for(version):
    return {}


def get_cache_store():
    """{(고유번호, 연도, 보고서코드): (저장시각, 데이터)} - 서버가 켜져 있는 동안 유지."""
    return _cache_store_for(CACHE_VERSION)


KEEP_COLUMNS = ["rcept_no", "sj_div", "account_id", "account_nm", "thstrm_amount", "thstrm_add_amount"]


def account_names(df):
    """계정명 비교용: 띄어쓰기를 모두 지운다. ('당기순이익 (손실)' -> '당기순이익(손실)')"""
    return df["account_nm"].astype(str).str.replace(r"\s+", "", regex=True)


def slim(df):
    """필요한 계정(매출·영업이익·순이익)과 열만 남겨 저장 용량을 줄인다. 없으면 None."""
    if df is None or df.empty or "account_nm" not in df.columns:
        return None
    keep = account_names(df).isin(REVENUE_NAMES + OPERATING_NAMES + NET_NAMES)
    if "account_id" in df.columns:
        keep |= df["account_id"].astype(str).str.strip().isin(NET_IDS)
    rows = df[keep]
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


def fetch_feed_page(session, store, ymd, cls, page):
    """하루치 전체 공시 한 페이지(최대 100건). 기업을 지정하지 않고 시장(corp_cls)별로 조회한다."""
    key = ("feed", ymd, cls, page)
    cached = cache_lookup(store, key, LIST_TTL)
    if cached is not _MISSING:
        return cached
    params = {
        "crtfc_key": API_KEY,
        "bgn_de": ymd,
        "end_de": ymd,
        "corp_cls": cls,
        "last_reprt_at": "N",  # 정정 공시까지 모두
        "page_no": page,
        "page_count": LIST_PAGE_SIZE,
    }
    jo = call_dart(session, DART_LIST_URL, params)
    cache_save(store, key, jo)
    return jo


def collect_feed(day, classes):
    """하루치 전체 공시(최신순). (행 목록, 전체 건수, 잘림 여부)를 돌려준다."""
    session, store = get_session(), get_cache_store()
    ymd = day.strftime("%Y%m%d")
    rows, total, truncated, pages = [], 0, False, {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        firsts = list(pool.map(lambda c: fetch_feed_page(session, store, ymd, c, 1), classes))
        futures = {}
        for cls, first in zip(classes, firsts):
            if first is None:
                continue
            total += int(first.get("total_count", len(first["list"])))
            total_page = int(first.get("total_page", 1))
            truncated = truncated or total_page > MAX_FEED_PAGES
            rows.extend(first["list"])
            for page in range(2, min(total_page, MAX_FEED_PAGES) + 1):  # 나머지 페이지는 동시에 조회
                futures[pool.submit(fetch_feed_page, session, store, ymd, cls, page)] = (cls, page)
        try:
            for future in as_completed(futures):
                pages[futures[future]] = future.result()
        except Exception:
            for f in futures:
                f.cancel()
            raise
    for key in sorted(pages):
        if pages[key]:
            rows.extend(pages[key]["list"])
    rows.sort(key=lambda r: str(r.get("rcept_no", "")), reverse=True)  # 접수번호가 클수록 나중에 접수
    return rows, total, truncated


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


def pick(df, names, column="thstrm_amount", ids=None):
    """손익계산서에서 계정과목(names 중 하나)의 금액을 뽑아냄. ids(표준 계정 코드)가 있으면 그것부터 찾는다."""
    if df is None or column not in df.columns:
        return None
    rows = df.iloc[0:0]
    if ids and "account_id" in df.columns:
        rows = df[df["account_id"].astype(str).str.strip().isin(ids)]
        if "sj_div" in rows.columns:
            rows = rows[rows["sj_div"].isin(["IS", "CIS"])]  # 현금흐름표·자본변동표의 같은 코드는 제외
    if rows.empty:
        rows = df[account_names(df).isin(names)]
    if "sj_div" in rows.columns:
        is_rows = rows[rows["sj_div"].isin(["IS", "CIS"])]
        if not is_rows.empty:
            rows = is_rows
    if rows.empty:
        return None
    row = rows.iloc[0]
    value = to_int(row[column])
    # 계정명이 '영업손실'인데 양수로 적혀 있으면 손실(마이너스)로 본다
    if value is not None and value > 0 and re.sub(r"\s+", "", str(row["account_nm"])) in LOSS_ONLY_NAMES:
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
    rev, op, net = cur["rev"], cur["op"], cur.get("net")
    return {
        "label": label,
        "sort": sort_key,
        "rev": rev,
        "op": op,
        "net": net,
        "rev_yoy": pct_change(rev, yoy["rev"]) if yoy else None,
        "op_yoy": op_change(op, yoy["op"]) if yoy else None,
        "net_yoy": op_change(net, yoy.get("net")) if yoy else None,  # 흑자전환·적자전환 표시는 영업이익과 같은 방식
        "rev_qoq": pct_change(rev, qoq["rev"]) if qoq else None,
        "op_qoq": op_change(op, qoq["op"]) if qoq else None,
        "net_qoq": op_change(net, qoq.get("net")) if qoq else None,
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
        data[y] = {
            "rev": pick(df, REVENUE_NAMES), "op": pick(df, OPERATING_NAMES),
            "net": pick(df, NET_NAMES, ids=NET_IDS), "link": link_of(df),
        }

    records = []
    for y in range(start, end + 1):
        cur = data[y]
        if cur["rev"] is None and cur["op"] is None and cur["net"] is None:
            continue
        records.append(make_record(str(y), (y, 0), cur, yoy=data[y - 1]))
    return records


def quarter_values(r1, r2, r3, r4, names, ids=None):
    """1~4분기 값을 계산. 4분기 = 연간 - 3분기 누적."""
    q1 = pick(r1, names, ids=ids)
    q2 = pick(r2, names, ids=ids)
    q3 = pick(r3, names, ids=ids)
    annual = pick(r4, names, ids=ids)

    cum9 = pick(r3, names, "thstrm_add_amount", ids=ids)  # 3분기 누적(9개월)
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
        nets = quarter_values(*reports, NET_NAMES, ids=NET_IDS)
        for i in range(4):  # 1~3분기는 각 분기 보고서, 4분기는 사업보고서로 연결
            data[(y, i + 1)] = {"rev": revs[i], "op": ops[i], "net": nets[i], "link": link_of(reports[i])}

    records = []
    for y in range(start, end + 1):
        for q in range(1, 5):
            cur = data[(y, q)]
            if cur["rev"] is None and cur["op"] is None and cur["net"] is None:
                continue
            qoq = data[(y, q - 1)] if q > 1 else data[(y - 1, 4)]
            records.append(make_record(f"{y} {q}Q", (y, q), cur, yoy=data[(y - 1, q)], qoq=qoq))
    return records


# ------------------------------------------------------------
# 표 만들기 (화면용 / 다운로드용)
# ------------------------------------------------------------
def fmt_amount(v, unit=DEFAULT_UNIT):
    """원 -> 억원/조원 (천 단위 쉼표). 값이 없으면 '-'."""
    if v is None:
        return "-"
    div, digits = UNITS[unit]
    return f"{round(v / div, digits) + 0.0:,.{digits}f}"  # + 0.0: '-0.00'처럼 보이지 않게


def fmt_change(c):
    if c is None:
        return "-"
    if isinstance(c, str):
        return c
    return f"{c:+,.1f}%"


def num_amount(v, unit=DEFAULT_UNIT):
    """다운로드 파일용: 원 -> 억원/조원(숫자). 값이 없으면 None."""
    if v is None:
        return None
    div, digits = UNITS[unit]
    return int(round(v / div)) if digits == 0 else round(v / div, digits)


def amount_style(cell):
    """금액 칸 스타일: 기본 파랑 + 굵게, 마이너스(앞에 '-')면 빨강 + 굵게."""
    color = NEGATIVE_COLOR if str(cell).startswith("-") and str(cell) != "-" else POSITIVE_COLOR
    return f"font-weight: bold; color: {color}"


def style_table(table):
    """매출액·영업이익·순이익 열에만 색과 굵기를 입힌다."""
    return table.style.map(amount_style, subset=["매출액", "영업이익", "순이익"])


def sorted_records(records):
    # 오래된 기간이 위로 (2023 → 2024 → 2025). 최신이 위로 오게 하려면 reverse=True를 추가
    return sorted(records, key=lambda r: r["sort"])


def build_table(records, period, for_export=False, unit=DEFAULT_UNIT):
    quarterly = period == "분기별"
    yoy = "전년동기比" if quarterly else "전년比"

    def amt(v):
        return num_amount(v, unit) if for_export else fmt_amount(v, unit)

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
        row["순이익"] = amt(r.get("net"))
        row[f"순이익 {yoy}"] = fmt_change(r.get("net_yoy"))
        if quarterly:
            row["순이익 전분기比"] = fmt_change(r.get("net_qoq"))
        row["원문"] = r["link"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if for_export:
        df = df.rename(
            columns={"매출액": f"매출액({unit})", "영업이익": f"영업이익({unit})", "순이익": f"순이익({unit})", "원문": "공시원문링크"}
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


def disclosure_key(text):
    """보고서명 비교용: 소문자로 바꾸고 공백·괄호·가운뎃점을 없앤다. 예: 영업(잠정)실적 -> 영업잠정실적"""
    return re.sub(r"[\s·ㆍ()\[\]]", "", str(text).lower())


REGULAR_REPORTS = ["사업보고서", "반기보고서", "분기보고서"]  # 공시 목록 결과의 '정기공시' 버튼


def match_preset(rows, preset):
    """묶음 이름으로 공시를 걸러낸다. '전체'·'모든 공시'는 그대로, '주요 전체'는 모든 묶음의 합."""
    if preset == "정기공시":
        words = REGULAR_REPORTS
    elif preset in MAJOR_DISCLOSURES:
        words = MAJOR_DISCLOSURES[preset]
    elif preset == "주요 전체":
        words = [w for ws in MAJOR_DISCLOSURES.values() for w in ws]
    else:
        return rows
    keys = [disclosure_key(w) for w in words]
    return [r for r in rows if any(k in disclosure_key(r.get("report_nm", "")) for k in keys)]


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


def feed_table(rows):
    """오늘의 공시 목록 -> 화면용 표 (시장·회사 포함)."""
    df = pd.DataFrame(rows).reindex(columns=["rcept_dt", "corp_cls", "corp_name", "report_nm", "rcept_no"]).fillna("")
    dates = pd.to_datetime(df["rcept_dt"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    return pd.DataFrame(
        {
            "접수일": dates,
            "시장": df["corp_cls"].map(lambda c: CLS_NAMES.get(c, c)),
            "회사": df["corp_name"],
            "보고서명": df["report_nm"].astype(str).str.strip(),
            "원문": df["rcept_no"].astype(str).map(lambda n: DART_VIEW_URL.format(n) if n else None),
        }
    )


# ------------------------------------------------------------
# 뉴스 조회 (네이버 뉴스 검색 API)
# ------------------------------------------------------------
class NewsApiError(Exception):
    """네이버 API 오류·호출 한도 초과 등 사용자에게 알려야 하는 뉴스 조회 실패."""


def call_naver(session, params):
    """네이버 뉴스 검색 API 호출 공통 처리. 정상이면 응답(dict), 문제가 있으면 NewsApiError."""
    headers = {"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET}
    res, last_error = None, None
    for attempt in range(3):  # 일시적인 네트워크 오류는 두 번까지 다시 시도
        try:
            res = session.get(NAVER_NEWS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            break
        except Exception as e:
            last_error = e
            time.sleep(0.5 * (attempt + 1))
    if res is None:
        raise NewsApiError("네이버 뉴스 서버 응답이 없거나 늦어지고 있습니다. 잠시 후 다시 시도해주세요.") from last_error
    if res.status_code == 200:
        return res.json()
    if res.status_code in (401, 403):
        raise NewsApiError("네이버 API 키가 올바르지 않거나 '검색' API 권한이 없습니다. Secrets의 키를 확인해주세요.")
    if res.status_code == 429:
        raise NewsApiError("네이버 API 호출 한도를 초과했습니다. 잠시 후 다시 시도해주세요.")
    raise NewsApiError(f"네이버 응답 오류 [{res.status_code}]")


def clean_title(raw):
    """제목의 <b> 태그와 &quot; 같은 HTML 기호를 정리한다."""
    return html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()


def publisher_of(url):
    """기사 주소 -> (언론사 이름, 구분). 목록에 없는 매체는 (도메인, None)."""
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    best = None
    for domain, (name, category) in PUBLISHERS.items():
        if (host == domain or host.endswith("." + domain)) and (best is None or len(domain) > len(best[0])):
            best = (domain, name, category)
    if best:
        return best[1], best[2]
    return host or "알 수 없음", None


def fetch_news(session, query, days, today_str):
    """검색어로 최신순 기사를 받아 기간 안의 것만 돌려준다. -> (기사 목록, 한도(1,000건)에 걸렸는지)

    ※ 작업 스레드에서 실행되므로 st.* 함수를 쓰지 않고, 세션은 인자로 받는다.
    오류는 저장하지 않고 그대로 올려 보낸다(다음에 다시 시도할 수 있게).
    """
    first_day = date.fromisoformat(today_str) - timedelta(days=days - 1)
    cutoff = datetime.combine(first_day, datetime.min.time(), KST)  # 기간 시작: 그날 0시(한국 시간)
    items, reached_cutoff, got_full_page = [], False, False
    for page in range(MAX_NEWS_PAGES):
        data = call_naver(
            session,
            {"query": query, "display": NEWS_PAGE_SIZE, "start": 1 + page * NEWS_PAGE_SIZE, "sort": "date"},
        )
        raw = data.get("items") or []
        for it in raw:
            try:
                when = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
            except Exception:
                continue
            if when < cutoff:
                reached_cutoff = True
                continue
            url = it.get("originallink") or it.get("link") or ""
            name, category = publisher_of(url)
            items.append(
                {"title": clean_title(it.get("title")), "url": url, "publisher": name, "category": category, "dt": when}
            )
        got_full_page = len(raw) >= NEWS_PAGE_SIZE
        if reached_cutoff or not got_full_page:
            break
    return items, (got_full_page and not reached_cutoff)


def search_news(terms, days):
    """검색어마다 '검색어 단독'으로 동시에 검색해 합친다.

    -> (기사 목록, 한도에 걸렸는지, 한도에 걸린 검색에서 빠짐없이 본 가장 이른 시각)
    같은 기사가 여러 검색에 걸려도 한 번만 담는다. 같은 검색은 5분 동안 저장해 둔 결과를 쓴다.
    """
    session, store, today = get_session(), get_cache_store(), TODAY.isoformat()  # 화면 스레드에서 미리 준비

    def one(term):
        query = f"{term} {NEWS_KEYWORD}"
        key = ("news", query, days, today)
        hit = cache_lookup(store, key, NEWS_TTL)
        if hit is not _MISSING:
            return hit
        value = fetch_news(session, query, days, today)
        cache_save(store, key, value)
        return value

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(one, terms))  # 하나라도 실패하면 그 오류(NewsApiError)를 그대로 올림
    items, seen, capped, since = [], set(), False, None
    for got, cap in results:
        if cap and got:  # 한도에 걸렸다면, 그 검색은 가장 오래된 기사 시각 이후만 빠짐없이 본 것
            oldest = min(i["dt"] for i in got)
            since = oldest if since is None else max(since, oldest)
        capped = capped or cap
        for it in got:
            if it["url"] not in seen:
                seen.add(it["url"])
                items.append(it)
    return items, capped, since


def news_names(corp):
    """입력한 기업명 + 등록된 별칭. 예: 삼성전자 -> [삼성전자, 삼성, 삼전]"""
    key = name_key(corp)
    for name, aliases in NEWS_ALIASES.items():
        if name_key(name) == key:  # 대소문자·띄어쓰기는 무시
            return [corp] + [a for a in aliases if name_key(a) != key]
    return [corp]


def is_exclusive(title):
    """제목에 [단독] 같은 단독 표기가 있는지. '단독주택', '단독 출시'처럼 다른 뜻의 단어는 제외."""
    return bool(re.search(r"[\[\(<【〈]\s*단독\s*[\]\)>】〉]|^\s*단독\s*[:,\-…]|^\s*단독\s", title))


def title_key(title):
    """제목 비교용: 기호·공백을 지우고 소문자로."""
    return re.sub(r"[^0-9a-z가-힣]", "", title.lower())


def cluster_news(items):
    """제목이 비슷한 기사를 한 묶음으로. -> [{"rep": 대표 기사, "others": 나머지 기사들}, ...] (최신순)

    대표 기사는 묶음 안에서 가장 최근 기사.
    """
    clusters = []
    for item in sorted(items, key=lambda x: x["dt"], reverse=True):
        key = title_key(item["title"])
        for c in clusters:
            m = c["matcher"]
            m.set_seq1(key)
            if m.real_quick_ratio() >= SIMILAR_TITLE and m.quick_ratio() >= SIMILAR_TITLE and m.ratio() >= SIMILAR_TITLE:
                c["items"].append(item)
                break
        else:
            matcher = difflib.SequenceMatcher(None, autojunk=False)
            matcher.set_seq2(key)  # 묶음의 첫 제목을 기준으로 삼는다 (한 번만 분석해 두면 빠르다)
            clusters.append({"matcher": matcher, "items": [item]})
    out = []
    for c in clusters:
        out.append({"rep": c["items"][0], "others": c["items"][1:]})
    return out


def is_new(item, now):
    """올라온 지 NEW_HOURS 시간 이내인 기사인지."""
    return now - item["dt"] < timedelta(hours=NEW_HOURS)


def news_html(clusters, now=None):
    """묶음 목록 -> 화면에 보여줄 HTML. 제목·주소는 반드시 escape해서 넣는다."""
    now = now or datetime.now(KST)

    def link(item):
        url = item["url"] if item["url"].startswith(("http://", "https://")) else "#"
        badge = '<span class="news-new">NEW</span>' if is_new(item, now) else ""
        return badge + f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(item["title"])}</a>'

    def meta(item):
        tags = f' · <span class="news-tags">{html.escape(", ".join(item["tags"]))}</span>' if item.get("tags") else ""
        return f'{html.escape(item["publisher"])} · {item["dt"]:%m.%d %H:%M}{tags}'

    parts = []
    for c in clusters:
        rep, others = c["rep"], c["others"]
        extra = f" · 외 {len(others)}건" if others else ""
        block = f'<div class="news-item">{link(rep)}<div class="news-meta">{meta(rep)}{extra}</div>'
        if others:
            rows = "".join(f"<li>{link(o)} <span>{meta(o)}</span></li>" for o in others)
            block += f"<details><summary>관련 기사 {len(others)}건 보기</summary><ul>{rows}</ul></details>"
        parts.append(block + "</div>")
    return "\n".join(parts)


def news_copy_text(res, now=None):
    """결과 목록을 메모·메신저에 붙여 넣기 좋은 글로. 묶음마다 대표 기사 한 건."""
    now = now or datetime.now(KST)
    lines = [f"[단독 뉴스] {res['corp']} · 최근 {res['period']} ({now:%m.%d %H:%M} 기준)", ""]
    for c in res["clusters"]:
        it = c["rep"]
        tags = f" ({', '.join(it['tags'])})" if it.get("tags") else ""
        lines.append(f"{it['title']} · {it['publisher']}{tags}")
        lines.append(it["url"])
        lines.append("")
    return "\n".join(lines).strip()


def apply_watchlist():
    """주소 뒤 관심 목록(?news=삼성전자,현대차 / ?sector=반도체)을 처음 한 번만 입력칸에 채우고 자동 조회.
    단독 뉴스 탭을 먼저 열어야 하면 True."""
    qp = st.query_params
    corps, sector = qp.get("news", "").strip(), qp.get("sector", "").strip()
    if st.session_state.get("watch_applied"):
        return False
    st.session_state["watch_applied"] = True
    if corps:
        st.session_state["news_mode"] = "기업명"
        st.session_state["news_corp"] = corps
    elif sector in NEWS_SECTORS:
        st.session_state["news_mode"] = "섹터"
        st.session_state["news_sector"] = sector
    else:
        return False
    st.session_state["news_run"] = True
    return True


def save_watchlist(res):
    """조회한 기업(섹터)을 주소 뒤에 붙여 둔다 -> 그 주소를 북마크하면 다음에 바로 조회."""
    try:
        if res["mode"] == "섹터":
            st.query_params.pop("news", None)
            st.query_params["sector"] = res["corp"]
        else:
            st.query_params.pop("sector", None)
            st.query_params["news"] = res["corp"]
    except Exception:  # 주소를 못 바꾸는 환경이어도 조회 결과는 그대로 보여준다
        pass


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
# 글자 하나씩이 아니라 단어로 읽는 영문 (NAVER는 '엔에이브이이알'이 아니라 '네이버')
# 추천에서 '네이버' -> NAVER, '포스코' -> POSCO홀딩스처럼 찾게 해준다. 필요하면 여기에 추가.
WORD_READINGS = {
    "naver": "네이버", "posco": "포스코", "soop": "숲", "oil": "오일", "nice": "나이스", "kisco": "키스코",
    "simpac": "심팩", "plus": "플러스", "ent": "엔터", "new": "뉴", "holdings": "홀딩스", "bio": "바이오",
    "tech": "테크", "korea": "코리아", "global": "글로벌", "energy": "에너지", "coway": "코웨이",
    "hanwha": "한화", "lotte": "롯데", "samsung": "삼성", "hyundai": "현대", "kakao": "카카오",
    "doosan": "두산", "kolon": "코오롱", "krafton": "크래프톤", "hybe": "하이브", "ecopro": "에코프로",
}
SUGGEST_LIMIT = 12

# 추천 순서에서 먼저 보여줄 이름난 기업 (대략 유명한 순서). 목록에 없는 이름은 뒤로 밀릴 뿐 추천에서 빠지지는 않는다.
# DART에 등록된 이름과 달라도 오류는 나지 않고, 그 기업만 우선 순위를 못 받는다. 필요하면 자유롭게 추가·수정하세요.
FAMOUS = [
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스", "현대자동차", "기아", "셀트리온", "삼성물산",
    "KB금융", "한화에어로스페이스", "HD현대중공업", "NAVER", "두산에너빌리티", "신한지주", "현대모비스", "한화오션",
    "삼성생명", "POSCO홀딩스", "SK스퀘어", "하나금융지주", "LG화학", "삼성SDI", "HD한국조선해양", "카카오",
    "삼성화재해상보험", "현대로템", "우리금융지주", "한국전력공사", "SK텔레콤", "KT", "LG전자", "삼성전기",
    "삼성중공업", "HD현대일렉트릭", "크래프톤", "SK이노베이션", "포스코퓨처엠", "현대글로비스", "삼성에스디에스",
    "삼성증권", "삼성E&A", "두산로보틱스", "HMM", "한미반도체", "한화시스템", "LS ELECTRIC", "에코프로비엠",
    "에코프로", "알테오젠", "한국항공우주산업", "현대건설", "고려아연", "대한항공", "아모레퍼시픽", "LG생활건강",
    "롯데케미칼", "롯데쇼핑", "롯데지주", "신세계", "이마트", "CJ제일제당", "CJ", "하이브", "엔씨소프트", "넷마블",
    "카카오뱅크", "카카오페이", "카카오게임즈", "한화", "한화솔루션", "GS", "GS건설", "GS리테일", "SK",
    "SK바이오팜", "SK바이오사이언스", "SK네트웍스", "SK가스", "SK케미칼", "현대제철", "현대위아", "현대오토에버",
    "현대백화점", "두산", "두산밥캣", "두산퓨얼셀", "KT&G", "대한전선", "한진칼", "LG디스플레이", "LG이노텍",
    "LG유플러스", "LG씨엔에스", "LG", "포스코인터내셔널", "금호석유화학", "S-Oil", "HD현대", "HD현대건설기계",
    "삼성카드", "DB손해보험", "메리츠금융지주", "미래에셋증권", "한국금융지주", "NH투자증권", "기업은행",
    "BNK금융지주", "JB금융지주", "오리온", "농심", "삼양식품", "CJ대한통운", "한국타이어앤테크놀로지", "만도",
    "효성", "효성중공업", "코웨이", "한온시스템", "에스엠", "와이지엔터테인먼트", "펄어비스", "컴투스", "위메이드",
]


def name_key(name, words=False):
    """기업명을 비교용으로 정리: 소문자, (주)·공백·기호 제거, 영문은 한글 발음으로.
    기본은 한 글자씩(SK -> 에스케이). words=True면 단어로 읽는 영문은 단어 발음으로(NAVER -> 네이버)."""
    text = unicodedata.normalize("NFKC", str(name)).lower()  # 전각 영문(ＳＫ)도 일반 영문으로
    text = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사", "", text)

    def latin(m):
        w = m.group()
        if words and w in WORD_READINGS:
            return WORD_READINGS[w]
        return "".join(LETTER_NAMES.get(ch, ch) for ch in w)

    text = re.sub(r"[a-z]+", latin, text)  # 띄어쓰기·기호를 지우기 전에 영문 단어 단위로 바꾼다
    return re.sub(r"[\s\-.,&·()/]", "", text)


@st.cache_resource
def get_name_index():
    """추천용 기업명 목록 (상장사 우선). 서버가 켜진 뒤 처음 한 번만 만든다."""
    df = get_dart().corp_codes[["corp_code", "corp_name", "stock_code"]].copy()
    df["corp_name"] = df["corp_name"].astype(str)
    df["stock_code"] = df["stock_code"].fillna("").astype(str).str.strip()
    df["listed"] = df["stock_code"] != ""
    df["dup"] = df["corp_name"].duplicated(keep=False)  # 같은 이름이 여럿이면 종목코드로 구분
    df = df.sort_values("listed", ascending=False, kind="stable").drop_duplicates("corp_name")
    df["key"] = [name_key(n) for n in df["corp_name"]]
    df["key2"] = [name_key(n, words=True) for n in df["corp_name"]]  # 단어 발음 (NAVER -> 네이버)
    rank = {}
    for i, n in enumerate(FAMOUS):
        rank.setdefault(name_key(n), i)
    df["fame"] = df["key"].map(rank).fillna(len(FAMOUS))  # 작을수록 유명
    return df.reset_index(drop=True)


def suggest_corps(query, limit=SUGGEST_LIMIT, listed_only=False, exclude=None):
    """입력한 이름과 비슷한 기업 추천. [(버튼에 보일 이름, 조회에 쓸 값), ...]
    listed_only: 상장사만 / exclude: 이 이름의 기업은 제외(이미 조회한 기업)"""
    q = name_key(query)
    if not q:
        return []
    idx = get_name_index()
    if listed_only:
        idx = idx[idx["listed"]]
    if exclude:
        idx = idx[idx["corp_name"] != exclude]
    def tiers(keys):
        t = pd.Series(3, index=idx.index)
        t[keys.str.contains(q, regex=False)] = 2  # 이름 안에 포함 (SK -> SK하이닉스)
        t[keys.str.startswith(q)] = 1
        t[keys == q] = 0  # 영문/한글 표기만 다른 경우 (에스케이하이닉스 -> SK하이닉스)
        return t

    # 글자 읽기(에스케이)와 단어 읽기(네이버) 중 더 잘 맞는 쪽으로
    tier = pd.concat([tiers(idx["key"]), tiers(idx["key2"])], axis=1).min(axis=1)
    keys = idx["key"]
    found = idx[tier < 3].assign(tier=tier[tier < 3], n=keys[tier < 3].str.len())
    found = found.sort_values(
        ["tier", "fame", "listed", "n"], ascending=[True, True, False, True], kind="stable"
    ).head(limit)

    if found.empty and not exclude:  # 오타 등: 상장사 중에서 글자가 비슷한 이름
        listed = idx[idx["listed"]]
        pool = list(dict.fromkeys(list(listed["key"]) + list(listed["key2"])))
        close = difflib.get_close_matches(q, pool, n=limit, cutoff=0.6)
        rank = {k: i for i, k in enumerate(close)}
        r = pd.concat([listed["key"].map(rank), listed["key2"].map(rank)], axis=1).min(axis=1)
        found = listed[r.notna()].assign(r=r[r.notna()]).sort_values("r").head(limit)

    out = []
    for _, r in found.iterrows():
        label = f"{r['corp_name']} ({r['stock_code']})" if r["listed"] else r["corp_name"]
        out.append((label, r["stock_code"] if r["dup"] and r["listed"] else r["corp_name"]))
    return out


def canonical_corp(corp):
    """입력한 기업명을 DART에 등록된 정확한 표기로 바꾼다. 영문 대소문자는 구분하지 않는다.
    예: sk하이닉스 -> SK하이닉스, lg엔솔은 기업명이 아니므로 그대로. 같은 이름이 여럿이면 상장사 우선."""
    text = unicodedata.normalize("NFKC", str(corp)).strip()
    if not text or text.isdigit():
        return text
    idx = get_name_index()  # 상장사가 먼저 오도록 정렬된 목록
    hit = idx[idx["corp_name"].str.casefold() == text.casefold()]
    return hit.iloc[0]["corp_name"] if not hit.empty else text


def resolve_corp_code(corp):
    """기업명/종목코드 -> 고유번호. 같은 이름이 여럿이면 상장사를 우선한다."""
    if not str(corp).isdigit():
        idx = get_name_index()
        hit = idx[idx["corp_name"] == str(corp)]  # 상장사가 먼저 오도록 정렬된 목록
        if not hit.empty:
            return hit.iloc[0]["corp_code"]
    return get_dart().find_corp_code(corp)


def suggestion_buttons(suggestions, prefix, tag):
    """추천 기업을 4개씩 한 줄로 버튼으로 보여준다."""
    for row in range(0, len(suggestions), 4):
        cols = st.columns(4)
        for col, (i, (label, value)) in zip(cols, list(enumerate(suggestions))[row : row + 4]):
            col.button(label, key=f"{prefix}_{tag}_{i}", on_click=pick_suggestion, args=(prefix, value))


def pick_suggestion(prefix, value):
    """추천 기업 버튼 클릭 -> 입력칸을 그 기업으로 바꾸고 바로 조회."""
    st.session_state[f"{prefix}_corp"] = value
    st.session_state[f"{prefix}_run"] = True


def lookup_corp_code(corp, prefix):
    """기업명(또는 종목코드) -> DART 고유번호. 못 찾으면 비슷한 기업을 추천하고 None."""
    with st.spinner("기업 정보를 확인하는 중입니다... (서버를 처음 켠 뒤 첫 조회는 몇 초 더 걸릴 수 있어요)"):
        corp_code = resolve_corp_code(corp)
        if corp_code:  # 찾았어도, 이름이 들어간 다른 상장사가 있으면 함께 안내 (SK -> SK하이닉스 ...)
            related = [] if str(corp).isdigit() else suggest_corps(corp, listed_only=True, exclude=corp)
            suggestions = []
        else:
            related, suggestions = [], suggest_corps(corp)
    if corp_code:
        if related:
            st.caption(f'"{corp}"이(가) 들어간 다른 상장사 — 누르면 바로 조회합니다.')
            suggestion_buttons(related, prefix, "rel")
    elif suggestions:
        st.warning(f'"{corp}"와 정확히 일치하는 기업이 없습니다. 혹시 이 기업인가요? 누르면 바로 조회합니다.')
        suggestion_buttons(suggestions, prefix, "sugg")
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
        corp_text = st.text_input("기업명 또는 종목코드", key="fin_corp", placeholder="예: 삼성전자 또는 005930")
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

            corp = canonical_corp(corp)  # 대소문자 무시 (sk하이닉스 -> SK하이닉스)
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

    unit = st.radio("단위", list(UNITS), horizontal=True, key="fin_unit", label_visibility="collapsed")  # 기본 억원, 조원으로 바꿀 수 있음
    table = build_table(result["records"], result["period"], unit=unit)
    basis = "연결 기준" if FS_DIV == "CFS" else "별도 기준"  # 지금은 항상 연결재무제표(CFS)로 조회
    st.caption(f"{result['corp']} · {basis} · 단위: {unit}")
    # 스크롤 없이 전체가 보이도록 높이를 행 수에 맞춘다 (머리글 1줄 + 데이터 행, 한 줄당 약 35px)
    st.dataframe(
        style_table(table),
        hide_index=True,
        # 기간 칸: "2025 4Q"가 모바일에서도 잘리지 않게 넓힌다
        column_config={
            "기간": st.column_config.TextColumn("기간", width=95),
            # 금액 칸: 삼성전자 매출(3,336,059억원)도 굵은 글씨로 잘리지 않는 너비
            "매출액": st.column_config.TextColumn("매출액", width=108),
            "영업이익": st.column_config.TextColumn("영업이익", width=95),
            "순이익": st.column_config.TextColumn("순이익", width=95),
            "원문": LINK_COLUMN,
        },
        height=(len(table) + 1) * 35 + 3,
    )

    export = build_table(result["records"], result["period"], for_export=True, unit=unit)
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
        corp_text = st.text_input("기업명 또는 종목코드", key="list_corp", placeholder="예: 삼성전자 또는 005930")
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
            corp = canonical_corp(corp)  # 대소문자 무시
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
                            "id": time.time_ns(),  # 조회할 때마다 달라지는 번호 (아래 라디오 키에 사용)
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

    # 주요 공시 버튼과 검색은 이미 가져온 목록 안에서 걸러내는 것이라 DART를 다시 부르지 않는다
    preset = st.radio(
        "주요 공시",
        ["전체", "정기공시"] + list(MAJOR_DISCLOSURES),
        horizontal=True,
        key=f"list_preset_{res['id']}",  # 건수가 조회마다 바뀌므로 키도 새로 만든다 (feed_view와 같은 이유)
        format_func=lambda p: f"{p} ({len(match_preset(res['rows'], p)):,})",
    )
    base_rows = match_preset(res["rows"], preset)
    keyword = st.text_input(
        "보고서명 검색",
        key="list_keyword",
        placeholder="예: 잠정실적, 유상증자, 사업보고서 (공백으로 여러 단어를 넣으면 모두 포함된 공시만)",
    )
    shown = filter_by_keyword(base_rows, keyword)
    if keyword.strip() or preset != "전체":
        st.caption(f"검색 결과 {len(shown):,}건 (불러온 {len(res['rows']):,}건 중)")
    if not shown:
        st.info("검색어와 일치하는 공시가 없습니다.")
        return

    st.dataframe(
        disclosure_table(shown),
        hide_index=True,
        column_config={"보고서명": st.column_config.TextColumn("보고서명", width="large"), "원문": LINK_COLUMN},
    )


def last_weekday(day):
    """주말이면 직전 금요일로 (공시는 평일에만 나온다. 공휴일은 따로 계산하지 않는다)."""
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def render_feed():
    """탭 3: 오늘의 공시 - 하루 동안 전 상장사가 낸 공시 중 취재거리가 될 만한 것만 모아 보기"""
    with st.form("feed_search"):
        c1, c2 = st.columns(2)
        day = c1.date_input(
            "날짜", value=last_weekday(TODAY), min_value=date(2015, 1, 1), max_value=TODAY, key="feed_date"
        )
        market = c2.radio("시장", list(FEED_MARKETS), horizontal=True, key="feed_market")
        submitted = st.form_submit_button("공시 불러오기")

    if submitted:
        st.session_state.pop("feed", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움
        with st.spinner("공시를 불러오는 중입니다... (공시가 많은 날은 몇 초 걸려요)"):
            try:
                rows, total, truncated = collect_feed(day, FEED_MARKETS[market])
            except DartApiError as e:
                st.error(str(e))
            else:
                st.session_state["feed"] = {
                    "day": day, "market": market, "rows": rows, "total": total, "truncated": truncated,
                    "id": time.time_ns(),  # 조회할 때마다 달라지는 번호 (아래 라디오 키에 사용)
                }

    res = st.session_state.get("feed")
    if not res:
        st.caption("날짜와 시장을 고르고 '공시 불러오기'를 누르세요. 영업실적·공급계약·증자·합병·자기주식·지분 변동 공시를 골라 보여줍니다.")
        return
    if not res["rows"]:
        st.warning("이 날짜에 조회된 공시가 없습니다. 휴장일이거나 아직 공시가 올라오기 전일 수 있어요.")
        return

    views = ["주요 전체"] + list(MAJOR_DISCLOSURES) + ["모든 공시"]
    notes = [f"{res['day']}", res["market"], f"전체 공시 {res['total']:,}건", "최신순", "정정 공시 포함"]
    st.caption(" · ".join(notes))
    if res["truncated"]:
        st.warning(f"공시가 매우 많아 시장마다 최신 {MAX_FEED_PAGES * LIST_PAGE_SIZE:,}건까지만 확인했어요.")
    view = st.radio(
        "보기",
        views,
        horizontal=True,
        # 선택지 이름의 건수가 조회마다 바뀌므로, 조회마다 키를 새로 만들어 항상 첫 번째가 선택된 채로 시작하게 한다
        key=f"feed_view_{res['id']}",
        format_func=lambda v: f"{v} ({len(match_preset(res['rows'], v)):,})",
    )
    keyword = st.text_input(
        "회사·보고서명 검색", key="feed_keyword", placeholder="예: 삼성, 하이닉스, 유상증자 (공백으로 여러 단어를 넣으면 모두 포함된 공시만)"
    )
    shown = match_preset(res["rows"], view)
    terms = keyword.lower().split()
    if terms:
        shown = [
            r for r in shown
            if all(t in f"{r.get('corp_name', '')} {r.get('report_nm', '')}".lower() for t in terms)
        ]
        st.caption(f"검색 결과 {len(shown):,}건")
    if not shown:
        st.info("조건에 맞는 공시가 없습니다.")
        return
    st.dataframe(
        feed_table(shown),
        hide_index=True,
        column_config={"보고서명": st.column_config.TextColumn("보고서명", width="large"), "원문": LINK_COLUMN},
    )


def render_news():
    """탭 3: 기업 관련 단독 뉴스 (비슷한 기사는 한 묶음으로)"""
    if not (NAVER_ID and NAVER_SECRET):
        st.info(
            "뉴스 기능을 쓰려면 네이버 검색 API 키를 등록해야 합니다. "
            "Streamlit Secrets에 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 두 줄을 추가해주세요."
        )
        return

    st.session_state.setdefault("news_corp", "")  # 처음엔 빈칸 (관심 목록 주소로 들어오면 그 기업들)
    mode = st.radio("검색 방식", ["기업명", "섹터"], horizontal=True, key="news_mode")  # 폼 밖: 바꾸면 아래 입력칸이 바뀜
    with st.form("news_search"):
        if mode == "기업명":
            corp_text = st.text_input(
                "기업명 (여러 곳은 쉼표로)", key="news_corp",
                placeholder="예: 삼성전자, SK하이닉스, 현대차",
                help=f"쉼표(,)나 슬래시(/)로 나눠 최대 {MAX_NEWS_CORPS}곳까지 한 번에 검색해요.",
            )
        else:
            sector = st.radio("섹터", list(NEWS_SECTORS), horizontal=True, key="news_sector")
        period = st.radio("기간", list(NEWS_PERIODS), index=1, horizontal=True, key="news_period")
        in_title = st.checkbox("제목에 검색어(기업명)가 들어간 기사만", value=True, key="news_title")
        submitted = st.form_submit_button("뉴스 조회") or st.session_state.pop("news_run", False)

    if submitted:
        corps = []
        if mode == "기업명":
            for part in re.split(NEWS_SPLIT, corp_text):
                part = canonical_corp(part)  # 대소문자 무시 (sk하이닉스 -> SK하이닉스)
                if part.isdigit() and len(part) == 6:  # 종목코드를 넣었다면 기업명으로 바꿔서 검색
                    idx = get_name_index()
                    hit = idx[idx["stock_code"] == part]
                    if not hit.empty:
                        part = hit.iloc[0]["corp_name"]
                if part and name_key(part) not in {name_key(c) for c in corps}:  # 빈칸·중복 제외
                    corps.append(part)
            label = ", ".join(corps)
        else:
            label = sector
        if not label:
            st.error("기업명을 입력해주세요.")
        elif len(corps) > MAX_NEWS_CORPS:
            st.error(f"기업은 한 번에 {MAX_NEWS_CORPS}곳까지 넣을 수 있어요. (지금 {len(corps)}곳)")
        else:
            st.session_state.pop("news", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움
            owner = {}  # 검색어 -> 어느 기업 것인지 (여러 기업 검색 때 기사에 기업명 꼬리표를 달기 위해)
            if mode == "기업명":
                terms = []
                for c in corps:
                    for t in news_names(c):  # 입력한 이름 + 별칭
                        if t not in owner:
                            owner[t] = c
                            terms.append(t)
            else:
                terms = NEWS_SECTORS[sector]["기업"] + NEWS_SECTORS[sector]["키워드"]
            multi = len(corps) > 1
            with st.spinner("뉴스를 불러오는 중입니다..."):
                try:
                    items, capped, since = search_news(terms, NEWS_PERIODS[period])
                except NewsApiError as e:
                    st.error(str(e))
                else:
                    total = len(items)
                    items = [i for i in items if i["category"]]  # 목록에 있는 언론사만
                    keys = {t: name_key(t) for t in terms}
                    tagged = []
                    for i in items:
                        tk = name_key(i["title"])
                        hits = [t for t, k in keys.items() if k in tk]  # 제목에 들어 있는 검색어
                        if in_title and not hits:
                            continue
                        if mode == "섹터":
                            tags = hits
                        elif multi:  # 여러 기업 검색: 별칭 대신 기업명으로 꼬리표
                            tags = list(dict.fromkeys(owner[t] for t in hits))
                        else:
                            tags = []
                        tagged.append(dict(i, tags=tags))  # 저장된 원본은 건드리지 않음
                    # 검색은 제목·요약 어디든 '단독'이 있으면 잡으므로, 제목에 단독 표기가 있는 것만 남긴다
                    items = [i for i in tagged if is_exclusive(i["title"])]
                    clusters = cluster_news(items)  # 비슷한 기사끼리 묶어 최신순으로
                    st.session_state["news"] = {
                        "corp": label, "mode": mode, "names": terms, "corps": corps, "period": period, "total": total,
                        "kept": len(items), "clusters": clusters, "capped": capped, "oldest": since,
                    }
                    save_watchlist(st.session_state["news"])

    res = st.session_state.get("news")
    if not res:
        return

    clusters = res["clusters"]
    if not clusters:
        st.warning(
            "조건에 맞는 기사가 없습니다. '제목에 검색어' 조건을 끄거나 기간을 늘려보세요."
            if res["total"]
            else "조회된 기사가 없습니다. 기업명을 확인하거나 기간을 늘려보세요."
        )
        return

    now = datetime.now(KST)
    notes = [res["corp"], f"최근 {res['period']}", f"기사 {res['kept']:,}건 → 묶음 {len(clusters):,}건"]
    fresh = sum(is_new(c["rep"], now) for c in clusters)
    if fresh:
        notes.append(f"NEW {fresh}건 (최근 {NEW_HOURS}시간)")
    if res.get("mode") == "섹터":
        notes.append(f"검색어 {len(res['names'])}개: {', '.join(res['names'])}")
    else:
        extra = [n for n in res["names"] if n not in res.get("corps", [res["corp"]])]  # 별칭만
        if extra:
            notes.append(f"함께 검색: {', '.join(extra)}")
    st.caption(" · ".join(notes))
    if res["capped"]:
        since = f"{res['oldest']:%m.%d %H:%M}" if res.get("oldest") else "?"
        st.warning(
            f"기사가 매우 많아 최신 {MAX_NEWS_PAGES * NEWS_PAGE_SIZE:,}건까지만 확인했어요. "
            f"{since} 이후 기사만 보이고 그 이전은 빠져 있습니다. "
            "기간을 줄이면 그 기간은 빠짐없이 볼 수 있어요."
        )
    with st.expander("목록 복사하기 (메모·메신저용)"):
        st.caption("오른쪽 위 복사 아이콘을 누르면 전체가 복사돼요.")
        st.code(news_copy_text(res, now), language=None, wrap_lines=True, height=320)  # 길면 상자 안에서 스크롤
    st.markdown(news_html(clusters, now), unsafe_allow_html=True)
    st.caption("지금 주소를 북마크(또는 홈 화면에 추가)하면 다음에 열 때 같은 기업으로 바로 조회돼요.")


STYLE = """
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css");

html, body, [class*="st-"], button, input, textarea {
    font-family: "Pretendard", -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
}
/* 아이콘(화살표 등)은 아이콘 글꼴 그대로 둔다. 안 그러면 'arrow_right' 같은 글자가 보임 */
[data-testid="stIconMaterial"], [data-testid="stExpanderIcon"], [data-testid="stExpanderIcon"] span {
    font-family: "Material Symbols Rounded" !important;
}
/* 복사용 글상자: 고정폭 글꼴 대신 본문 글꼴로 읽기 쉽게 */
[data-testid="stCode"] code { font-family: "Pretendard", -apple-system, sans-serif !important; font-size: 0.85rem; }
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

/* 뉴스 목록 */
.news-item { background: #ffffff; border: 1px solid #e3e8ef; border-radius: 12px; padding: 0.8rem 1rem; margin-bottom: 0.55rem; }
.news-item > a { font-weight: 600; color: #0b2545; text-decoration: none; line-height: 1.5; }
.news-item > a:hover { color: #1c7ed6; text-decoration: underline; }
.news-meta { color: #7a8699; font-size: 0.85rem; margin-top: 0.2rem; }
.news-new {
    display: inline-block; background: #e03131; color: #fff; font-size: 0.66rem; font-weight: 700;
    border-radius: 4px; padding: 0.05rem 0.35rem; margin-right: 0.35rem; vertical-align: 0.12em; letter-spacing: 0.03em;
}
.news-item details { margin-top: 0.45rem; font-size: 0.9rem; }
.news-item summary { color: #1c7ed6; cursor: pointer; }
.news-item ul { margin: 0.4rem 0 0 0; padding-left: 1.1rem; }
.news-item li { margin-bottom: 0.25rem; }
.news-item li a { color: #1f2937; text-decoration: none; }
.news-item li a:hover { color: #1c7ed6; text-decoration: underline; }
.news-tags { color: #4c6ef5; font-weight: 600; }
.news-item li span { color: #7a8699; font-size: 0.8rem; margin-left: 0.3rem; }
</style>
"""

st.set_page_config(page_title="산업부 취재 도우미", layout="wide")
st.markdown(STYLE, unsafe_allow_html=True)
st.title("산업부 취재 도우미")
st.caption(f"credit/중앙일보 나상현  \nupdated/{LAST_UPDATED}")  # 크레딧 두 줄 (줄바꿈 = 공백 2칸 + \n). 수정할 때 빠뜨리지 말 것

if not API_KEY:
    st.error(
        "DART API 키가 설정되지 않았습니다. "
        "Streamlit secrets(DART_API_KEY) 또는 환경변수 DART_API_KEY에 키를 등록해주세요."
    )
    st.stop()

TAB_NAMES = ["재무 조회", "공시 목록", "오늘의 공시", "단독 뉴스"]
# 관심 기업 주소(?news=... 또는 ?sector=...)로 들어오면 단독 뉴스 탭을 먼저 연다
first_tab = "단독 뉴스" if apply_watchlist() else None
try:
    tabs = st.tabs(TAB_NAMES, default=first_tab)
except TypeError:  # 처음 열 탭 지정을 지원하지 않는 옛 Streamlit
    tabs = st.tabs(TAB_NAMES)
tab_fin, tab_list, tab_feed, tab_news = tabs
with tab_fin:
    render_financials()
with tab_list:
    render_disclosures()
with tab_feed:
    render_feed()
with tab_news:
    render_news()
