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

import base64
import difflib
import hashlib
import html
import io
import json
import os
import re
import threading
import time
import unicodedata
import zipfile
from collections import namedtuple
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
DART_DOC_URL = "https://opendart.fss.or.kr/api/document.xml"  # 공시서류 원본파일 (zip)
MAX_DOC_CHARS = 80_000  # AI에 보내는 원문 최대 글자 수 (사업보고서처럼 아주 긴 공시는 앞부분만)
AI_TIMEOUT = 90  # AI 응답을 기다리는 최대 시간(초)
AI_TTL = 7 * 24 * 3600  # 같은 공시의 요약은 일주일 동안 저장해 두고 다시 쓴다 (비용 절약)
# 임원 보수 (사업·반기보고서). 2026년 5월 이후 제출된 보고서는 V2 주소로만 나와서, 옛 주소에 없으면 V2로 다시 찾는다.
PAY_APIS = {
    "indiv": ["hmvAuditIndvdlBySttus", "hmvAuditIndvdlBySttusV2"],  # 이사·감사 개인별 보수 (5억원 이상)
    "top5": ["indvdlByPay", "indvdlByPayV2"],  # 개인별 보수 상위 5인 (5억원 이상, 미등기 포함)
    "board": ["hmvAuditAllSttus"],  # 이사·감사 전체 보수 (인원·총액·1인 평균)
    "emp": ["empSttus"],  # 직원 현황 (직원 수·급여 총액)
}
PAY_REPORTS = {"사업보고서 (연간)": "11011", "반기보고서 (상반기)": "11012"}
PAY_REPORTS_ALL = {**PAY_REPORTS, "1분기보고서": "11013", "3분기보고서": "11014"}  # 임직원 화면 (보수는 사업·반기보고서에만 실린다)
QUARTER_CODES = ("11013", "11014")
MAX_PAY_CORPS = 5

# 업종 합산: 업종을 고르면 아래 기업들이 입력칸에 채워진다 (DART에 등록된 이름 그대로). 자유롭게 추가·수정하세요.
INDUSTRY_GROUPS = {
    "반도체": ["삼성전자", "SK하이닉스", "한미반도체"],
    "배터리": ["LG에너지솔루션", "삼성SDI", "에스케이온", "포스코퓨처엠", "엘앤에프", "에코프로비엠"],
    "자동차": ["현대자동차", "기아", "현대모비스"],
    "조선": ["HD한국조선해양", "HD현대중공업", "한화오션", "삼성중공업"],
    "방산": ["한화에어로스페이스", "한국항공우주", "LIG디펜스앤에어로스페이스", "현대로템"],
    "철강": ["POSCO홀딩스", "현대제철", "동국제강"],
    "정유·석화": ["SK이노베이션", "S-Oil", "GS칼텍스", "에이치디현대오일뱅크", "롯데케미칼", "LG화학", "한화솔루션", "금호석유화학"],
    "전자부품·디스플레이": ["LG전자", "삼성전기", "LG이노텍", "LG디스플레이"],
    "통신": ["SK텔레콤", "KT", "LG유플러스"],
    "IT": ["NAVER", "카카오"],
}
INDUSTRY_PERIODS = ["연간", "1Q", "2Q", "3Q", "4Q"]

# 주요사항보고서: 보고서명(공백·괄호 무시)에 들어 있는 말 -> (DART 주요정보 API, 칸 목록).
# 칸: (항목 이름, 응답 키 또는 (시작, 끝) 키 묶음, 표시 방식 won=금액 / shares=주식수 / pct=% / price=원 / text=그대로).
# 앞에 있는 것부터 맞춰 보므로, 더 긴 이름(예: 신주인수권부사채)을 먼저 둔다.
FUND_PURPOSES = [("fdpp_fclt", "시설자금"), ("fdpp_bsninh", "영업양수자금"), ("fdpp_op", "운영자금"),
                 ("fdpp_dtrp", "채무상환자금"), ("fdpp_ocsa", "타법인 증권 취득자금"), ("fdpp_etc", "기타자금")]
_BOND_COMMON = [("사채 종류", "bd_knd", "text"), ("회차", "bd_tm", "text"), ("권면총액", "bd_fta", "won"),
                ("표면이자율", "bd_intr_ex", "pct"), ("만기이자율", "bd_intr_sf", "pct"), ("만기일", "bd_mtd", "text"),
                ("발행방법", "bdis_mthn", "text")]
MAJOR_REPORTS = [
    ("유상증자결정", "piicDecsn", "유상증자", [
        ("증자방식", "ic_mthn", "text"), ("신주(보통주)", "nstk_ostk_cnt", "shares"), ("신주(기타주)", "nstk_estk_cnt", "shares"),
        ("증자 전 발행주식(보통주)", "bfic_tisstk_ostk", "shares"), ("1주당 액면가", "fv_ps", "price")]),
    ("무상증자결정", "fricDecsn", "무상증자", [
        ("신주(보통주)", "nstk_ostk_cnt", "shares"), ("증자 전 발행주식(보통주)", "bfic_tisstk_ostk", "shares"),
        ("1주당 배정 주식수", "nstk_ascnt_ps_ostk", "text"), ("신주배정기준일", "nstk_asstd", "text"),
        ("신주 상장예정일", "nstk_lstprd", "text")]),
    ("감자결정", "crDecsn", "감자", [
        ("감자방법", "cr_mth", "text"), ("감자사유", "cr_rs", "text"), ("감자비율(보통주)", "cr_rt_ostk", "pct"),
        ("감자 전 자본금", "bfcr_cpt", "won"), ("감자 후 자본금", "atcr_cpt", "won"), ("감자기준일", "cr_std", "text")]),
    ("신주인수권부사채권발행결정", "bdwtIsDecsn", "신주인수권부사채(BW)", _BOND_COMMON + [
        ("행사가액", "ex_prc", "price"), ("행사 시 발행 주식수", "nstk_isstk_cnt", "shares"),
        ("주식총수 대비", "nstk_isstk_tisstk_vs", "pct"), ("행사기간", ("expd_bgd", "expd_edd"), "text"),
        ("최저 조정가액", "act_mktprcfl_cvprc_lwtrsprc", "price"), ("납입일", "pymd", "text")]),
    ("전환사채권발행결정", "cvbdIsDecsn", "전환사채(CB)", _BOND_COMMON + [
        ("전환가액", "cv_prc", "price"), ("전환 시 발행 주식수", "cvisstk_cnt", "shares"),
        ("주식총수 대비", "cvisstk_tisstk_vs", "pct"), ("전환청구기간", ("cvrqpd_bgd", "cvrqpd_edd"), "text"),
        ("최저 조정가액", "act_mktprcfl_cvprc_lwtrsprc", "price"), ("납입일", "pymd", "text")]),
    ("교환사채권발행결정", "exbdIsDecsn", "교환사채(EB)", _BOND_COMMON + [
        ("교환가액", "ex_prc", "price"), ("교환대상", "extg", "text"), ("교환대상 주식수", "extg_stkcnt", "shares"),
        ("주식총수 대비", "extg_tisstk_vs", "pct"), ("교환청구기간", ("exrqpd_bgd", "exrqpd_edd"), "text"),
        ("납입일", "pymd", "text")]),
    ("회사분할합병결정", None, None, []),  # 정리된 데이터 칸이 달라 AI 요약으로
    ("회사분할결정", "cmpDvDecsn", "회사분할", [
        ("분할방법", "dv_mth", "text"), ("분할비율", "dv_rt", "text"), ("이전할 사업", "dv_trfbsnprt_cn", "text"),
        ("존속회사", "atdv_excmp_cmpnm", "text"), ("신설회사", "dvfcmp_cmpnm", "text"), ("신설회사 주요사업", "dvfcmp_mbsn", "text"),
        ("신설부문 최근 매출", "dvfcmp_nbsn_rsl", "won"), ("신설회사 자산총계", "ffdtl_tast", "won"),
        ("주주총회 예정일", "gmtsck_prd", "text"), ("분할기일", "dvdt", "text")]),
    ("회사합병결정", "cmpMgDecsn", "회사합병", [
        ("합병방법", "mg_mth", "text"), ("합병형태", "mg_stn", "text"), ("합병목적", "mg_pp", "text"), ("합병비율", "mg_rt", "text"),
        ("합병상대회사", "mgptncmp_cmpnm", "text"), ("회사와의 관계", "mgptncmp_rl_cmpn", "text"),
        ("상대회사 매출액", "rbsnfdtl_sl", "won"), ("상대회사 당기순이익", "rbsnfdtl_nic", "won"),
        ("합병기일", "mgsc_mgdt", "text"), ("주식매수청구 가격", "aprskh_plnprc", "text")]),
    ("영업양수결정", "bsnInhDecsn", "영업양수", [
        ("양수영업", "inh_bsn", "text"), ("양수가액", "inh_prc", "won"), ("매출액 비중", "sl_rt", "pct"),
        ("양수목적", "inh_pp", "text"), ("거래상대방", "dlptn_cmpnm", "text"), ("회사와의 관계", "dlptn_rl_cmpn", "text"),
        ("양수기준일", "inh_prd_inh_std", "text")]),
    ("영업양도결정", "bsnTrfDecsn", "영업양도", [
        ("양도영업", "trf_bsn", "text"), ("양도가액", "trf_prc", "won"), ("매출액 비중", "sl_rt", "pct"),
        ("양도목적", "trf_pp", "text"), ("거래상대방", "dlptn_cmpnm", "text"), ("회사와의 관계", "dlptn_rl_cmpn", "text"),
        ("양도기준일", "trf_prd_trf_std", "text")]),
    ("유형자산양수결정", "tgastInhDecsn", "유형자산 양수", [
        ("자산구분", "ast_sen", "text"), ("자산명", "ast_nm", "text"), ("양수금액", "inhdtl_inhprc", "won"),
        ("자산총액 대비", "inhdtl_tast_vs", "pct"), ("양수목적", "inh_pp", "text"), ("거래상대방", "dlptn_cmpnm", "text"),
        ("양수기준일", "inh_prd_inh_std", "text")]),
    ("유형자산양도결정", "tgastTrfDecsn", "유형자산 양도", [
        ("자산구분", "ast_sen", "text"), ("자산명", "ast_nm", "text"), ("양도금액", "trfdtl_trfprc", "won"),
        ("자산총액 대비", "trfdtl_tast_vs", "pct"), ("양도목적", "trf_pp", "text"), ("거래상대방", "dlptn_cmpnm", "text"),
        ("양도기준일", "trf_prd_trf_std", "text")]),
    ("타법인주식및출자증권양수결정", "otcprStkInvscrInhDecsn", "타법인 주식 양수", [
        ("대상회사", "iscmp_cmpnm", "text"), ("대상회사 주요사업", "iscmp_mbsn", "text"), ("양수 주식수", "inhdtl_stkcnt", "shares"),
        ("양수금액", "inhdtl_inhprc", "won"), ("자기자본 대비", "inhdtl_ecpt_vs", "pct"), ("양수 후 지분율", "atinh_eqrt", "pct"),
        ("양수목적", "inh_pp", "text"), ("거래상대방", "dlptn_cmpnm", "text"), ("양수예정일", "inh_prd", "text")]),
    ("타법인주식및출자증권양도결정", "otcprStkInvscrTrfDecsn", "타법인 주식 양도", [
        ("대상회사", "iscmp_cmpnm", "text"), ("양도 주식수", "trfdtl_stkcnt", "shares"), ("양도금액", "trfdtl_trfprc", "won"),
        ("자기자본 대비", "trfdtl_ecpt_vs", "pct"), ("양도 후 지분율", "attrf_eqrt", "pct"), ("양도목적", "trf_pp", "text"),
        ("거래상대방", "dlptn_cmpnm", "text"), ("양도예정일", "trf_prd", "text")]),
    ("자기주식취득결정", "tsstkAqDecsn", "자기주식 취득", [
        ("취득예정 주식(보통주)", "aqpln_stk_ostk", "shares"), ("취득예정금액(보통주)", "aqpln_prc_ostk", "won"),
        ("취득기간", ("aqexpd_bgd", "aqexpd_edd"), "text"), ("취득목적", "aq_pp", "text"), ("취득방법", "aq_mth", "text"),
        ("위탁 증권사", "cs_iv_bk", "text")]),
    ("자기주식처분결정", "tsstkDpDecsn", "자기주식 처분", [
        ("처분예정 주식(보통주)", "dppln_stk_ostk", "shares"), ("처분예정금액(보통주)", "dppln_prc_ostk", "won"),
        ("처분기간", ("dpprpd_bgd", "dpprpd_edd"), "text"), ("처분목적", "dp_pp", "text"), ("위탁 증권사", "cs_iv_bk", "text")]),
]
MAX_DIV_CORPS = 5

MAX_INDUSTRY_CORPS = 10

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
MAX_NEWS_PAGES = 3  # 검색어 하나당 최신 300건까지만 확인 (네이버 하루 호출 한도 25,000회를 아끼기 위해. 제목에 [단독]이 붙은 기사는 대부분 앞쪽에 있음)
NEWS_TTL = 30 * 60  # 같은 검색은 30분 동안 저장해 둔 결과를 재사용 (호출 절약)
NEWS_TTL_TODAY = 10 * 60  # 기간이 '오늘'인 검색은 새 기사가 계속 올라오므로 더 짧게
SIMILAR_TITLE = 0.8  # 제목이 이 정도(0~1) 이상 비슷하면 같은 기사로 묶는다
NEWS_PERIODS = {"오늘": 1, "3일": 3, "일주일": 7}

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
    "한국항공우주산업": ["KAI", "한국항공우주"],
    "한국항공우주": ["KAI", "한국항공우주산업"],
    "KAI": ["한국항공우주산업", "한국항공우주"],
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

LAST_UPDATED = "2026.10.18"  # 화면 아래 크레딧의 updated 날짜. 앱을 고칠 때마다 직접 바꿔주세요

KST = timezone(timedelta(hours=9))  # 서버가 해외에 있어도 한국 날짜 기준으로 동작
TODAY = datetime.now(KST).date()
NEW_SINCE = (TODAY - timedelta(days=1)).strftime("%Y%m%d")  # NEW 표시 기준: 어제부터

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

# AI 기능 (선택). ANTHROPIC_API_KEY가 있으면 Claude(유료), 없고 GEMINI_API_KEY가 있으면 Gemini(무료 한도)를 쓴다.
# 둘 다 넣어 두었다면 Secrets의 AI_PROVIDER = "gemini" 또는 "claude"로 고를 수 있다.
# 모델 이름은 자주 바뀌므로 Secrets의 GEMINI_MODEL / ANTHROPIC_MODEL로 바꿀 수 있다.
GEMINI_KEY = load_secret("GEMINI_API_KEY")
GEMINI_MODEL = load_secret("GEMINI_MODEL") or "gemini-3.6-flash"
# 기본 모델이 붐벼서(503) 실패하면 자동으로 바꿔 쓰는 가벼운 모델
GEMINI_FALLBACK = load_secret("GEMINI_FALLBACK_MODEL") or "gemini-3.5-flash-lite"
ANTHROPIC_KEY = load_secret("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = load_secret("ANTHROPIC_MODEL") or "claude-haiku-5-5"
_want = (load_secret("AI_PROVIDER") or "").strip().lower()
if _want == "gemini" and GEMINI_KEY:
    AI_PROVIDER = "gemini"
elif _want == "claude" and ANTHROPIC_KEY:
    AI_PROVIDER = "claude"
else:
    AI_PROVIDER = "claude" if ANTHROPIC_KEY else ("gemini" if GEMINI_KEY else None)

# 계정명은 회사·연도마다 바뀔 수 있다 (예: LG에너지솔루션 2026년부터 '매출액 및 기타수익').
# 그래서 표준 계정 코드(_IDS)로 먼저 찾고, 없으면 이름으로 찾는다. 이름은 띄어쓰기를 지우고 비교.
REVENUE_NAMES = [
    "매출액", "수익(매출액)", "영업수익", "매출", "수익", "매출액및기타수익", "매출및기타수익", "매출액(영업수익)",
]
REVENUE_IDS = ["ifrs-full_Revenue", "ifrs_Revenue"]
OPERATING_NAMES = ["영업이익", "영업이익(손실)", "영업손익", "영업손실", "영업손실(이익)"]
OPERATING_IDS = ["dart_OperatingIncomeLoss"]
NET_NAMES = [
    "당기순이익", "당기순이익(손실)", "당기순손익", "당기순손실", "당기순손실(이익)", "연결당기순이익",
    "분기순이익", "분기순이익(손실)", "분기순손실", "반기순이익", "반기순이익(손실)", "반기순손실",
    "당기순이익(순손실)", "분기순이익(순손실)", "반기순이익(순손실)", "분기순손익", "반기순손익",
    "연결분기순이익", "연결반기순이익", "연결당기순이익(손실)",
]
NET_IDS = ["ifrs-full_ProfitLoss", "ifrs_ProfitLoss"]  # 당기순이익의 표준 계정 코드 (이름이 회사마다 달라도 잡힘)
# 재무상태 항목: (계정명들, 표준 계정 코드들, 찾을 재무제표). 계정명은 띄어쓰기를 지우고 비교한다.
BS_ITEMS = {
    "liab": (["부채총계"], ["ifrs-full_Liabilities", "ifrs_Liabilities"], ("BS",)),
    "equity": (["자본총계"], ["ifrs-full_Equity", "ifrs_Equity"], ("BS",)),
    "cash": (["현금및현금성자산"], ["ifrs-full_CashAndCashEquivalents", "ifrs_CashAndCashEquivalents"], ("BS",)),
    "inv": (["재고자산"], ["ifrs-full_Inventories", "ifrs_Inventories"], ("BS",)),
    "capex": (["유형자산의취득", "유형자산취득"],
              ["ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
               "ifrs-full_PurchaseOfPropertyPlantAndEquipment"], ("CF",)),
    # --- 더 보기 표용 ---
    "assets": (["자산총계"], ["ifrs-full_Assets", "ifrs_Assets"], ("BS",)),
    "cur_assets": (["유동자산"], ["ifrs-full_CurrentAssets", "ifrs_CurrentAssets"], ("BS",)),
    "cur_liab": (["유동부채"], ["ifrs-full_CurrentLiabilities", "ifrs_CurrentLiabilities"], ("BS",)),
    "opcf": (["영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동으로인한순현금흐름", "영업활동순현금흐름", "영업활동으로부터의현금흐름"],
             ["ifrs-full_CashFlowsFromUsedInOperatingActivities", "ifrs_CashFlowsFromUsedInOperatingActivities"], ("CF",)),
    "dep": (["감가상각비"], [], ("CF",)),
    "ppe_dep": (["유형자산감가상각비"], [], ("CF",)),
    "rou": (["사용권자산상각비", "사용권자산감가상각비"], [], ("CF",)),
    "amort": (["무형자산상각비", "무형자산상각"], [], ("CF",)),
    "interest": (["이자비용"], ["ifrs-full_InterestExpense", "ifrs_InterestExpense"], ("IS", "CIS")),
    "int_paid": (["이자의지급", "이자지급", "이자의지급(영업활동)", "이자지급(영업활동)", "이자의지급(재무활동)"], [], ("CF",)),
}
# 연간(사업보고서)에서만 믿을 수 있는 항목: 현금흐름표는 분기보고서가 누적 금액이고, 4분기 손익은 '연간 − 3분기 누적'이라 분기 값을 바로 쓸 수 없다
ANNUAL_ONLY = {"capex", "opcf", "dep", "ppe_dep", "rou", "amort", "int_paid", "interest"}
ABS_KEYS = {"capex", "dep", "ppe_dep", "rou", "amort", "int_paid", "interest"}  # 괄호 음수로 적혀도 크기로 쓴다
# 차입금: 재무상태표의 이 계정들을 더한다 (같은 이름은 한 번만). 리스부채는 뺀다.
BORROW_NAMES = [
    "단기차입금", "유동성장기부채", "유동성장기차입금", "유동성사채", "유동성전환사채", "단기사채", "차입금", "유동성차입금",
    "장기차입금", "비유동차입금", "사채", "장기사채", "전환사채", "신주인수권부사채", "교환사채",
]
BS_NAMES = [n for names, _, _ in BS_ITEMS.values() for n in names] + BORROW_NAMES
BS_IDS = [i for _, ids, _ in BS_ITEMS.values() for i in ids]
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
CACHE_VERSION = 5


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
    keep = account_names(df).isin(REVENUE_NAMES + OPERATING_NAMES + NET_NAMES + BS_NAMES)
    if "account_id" in df.columns:
        keep |= df["account_id"].astype(str).str.strip().isin(REVENUE_IDS + OPERATING_IDS + NET_IDS + BS_IDS)
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
    if "list" in jo or status == "000":
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


def pick(df, names, column="thstrm_amount", ids=None, sj=("IS", "CIS")):
    """재무제표에서 계정과목(names 중 하나)의 금액을 뽑아냄. ids(표준 계정 코드)가 있으면 그것부터 찾는다.
    sj: 찾을 재무제표 (기본 손익계산서. 재무상태표는 BS, 현금흐름표는 CF)"""
    if df is None or column not in df.columns:
        return None
    rows = df.iloc[0:0]
    if ids and "account_id" in df.columns:
        rows = df[df["account_id"].astype(str).str.strip().isin(ids)]
        if "sj_div" in rows.columns:
            rows = rows[rows["sj_div"].isin(sj)]  # 다른 재무제표의 같은 코드는 제외 (예: 현금흐름표의 당기순이익)
    if rows.empty:
        rows = df[account_names(df).isin(names)]
    if "sj_div" in rows.columns:
        is_rows = rows[rows["sj_div"].isin(sj)]
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


def borrow_total(df):
    """차입금 합계: 재무상태표의 BORROW_NAMES 계정을 더한다. 하나도 못 찾으면 None (0으로 보지 않음)."""
    if df is None or "account_nm" not in df.columns or "thstrm_amount" not in df.columns:
        return None
    rows = df[account_names(df).isin(BORROW_NAMES)]
    if "sj_div" in rows.columns:
        rows = rows[rows["sj_div"] == "BS"]
    seen, total = set(), None
    for _, row in rows.iterrows():
        name = re.sub(r"\s+", "", str(row["account_nm"]))
        v = to_int(row["thstrm_amount"])
        if name in seen or v is None:
            continue
        seen.add(name)
        total = (total or 0) + abs(v)
    return total


def bs_values(df, with_capex=False):
    """재무상태 항목 (기말 잔액). with_capex=True는 연간(사업보고서): 현금흐름·이자·감가상각 항목은 연간만 쓴다."""
    out = {}
    for key, (names, ids, sj) in BS_ITEMS.items():
        if key in ANNUAL_ONLY and not with_capex:
            out[key] = None
            continue
        v = pick(df, names, ids=ids, sj=sj)
        out[key] = abs(v) if key in ABS_KEYS and v is not None else v  # 취득액·이자 등은 (괄호) 음수로 적힌 경우가 많다
    out["borrow"] = borrow_total(df)
    return out


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
        "bs": cur.get("bs") or {},
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
            "rev": pick(df, REVENUE_NAMES, ids=REVENUE_IDS), "op": pick(df, OPERATING_NAMES, ids=OPERATING_IDS),
            "net": pick(df, NET_NAMES, ids=NET_IDS), "link": link_of(df),
            "bs": bs_values(df, with_capex=True),
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
        revs = quarter_values(*reports, REVENUE_NAMES, ids=REVENUE_IDS)
        ops = quarter_values(*reports, OPERATING_NAMES, ids=OPERATING_IDS)
        nets = quarter_values(*reports, NET_NAMES, ids=NET_IDS)
        for i in range(4):  # 1~3분기는 각 분기 보고서, 4분기는 사업보고서로 연결
            data[(y, i + 1)] = {"rev": revs[i], "op": ops[i], "net": nets[i], "link": link_of(reports[i]),
                                "bs": bs_values(reports[i])}  # 4분기 = 사업보고서의 기말 잔액

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


def debt_ratio(bs):
    """부채비율(%) = 부채총계 ÷ 자본총계 × 100. 자본이 0 이하면 '자본잠식'."""
    liab, equity = bs.get("liab"), bs.get("equity")
    if liab is None or equity is None:
        return None
    if equity <= 0:
        return "자본잠식"
    return liab / equity * 100


def bs_metrics(r, yearly):
    """기간 하나의 재무상태 지표(원 단위 숫자·비율). 계산할 수 없으면 None. 연간 전용 지표는 분기별에서 None."""
    bs = r.get("bs") or {}
    liab, equity, cash, borrow = bs.get("liab"), bs.get("equity"), bs.get("cash"), bs.get("borrow")
    m = {"debt_ratio": debt_ratio(bs), "borrow": borrow}
    nd = borrow - cash if borrow is not None and cash is not None else None  # 마이너스 = 순현금
    m["net_debt"] = nd
    m["nd_ratio"] = None if nd is None or equity is None else ("자본잠식" if equity <= 0 else nd / equity * 100)
    ca, cl = bs.get("cur_assets"), bs.get("cur_liab")
    m["cur_ratio"] = ca / cl * 100 if ca is not None and cl else None
    opcf, capex = bs.get("opcf"), bs.get("capex")
    m["opcf"] = opcf
    m["fcf"] = opcf - capex if yearly and opcf is not None and capex is not None else None
    interest = next((v for v in (bs.get("interest"), bs.get("int_paid")) if v), None)  # 이자비용, 없으면 현금흐름표의 이자 지급액
    op, net = r.get("op"), r.get("net")
    m["int_cov"] = op / interest if yearly and op is not None and interest else None
    dep = bs.get("dep")
    if dep is None and (bs.get("ppe_dep") is not None or bs.get("rou") is not None):
        dep = (bs.get("ppe_dep") or 0) + (bs.get("rou") or 0)
    m["ebitda"] = op + dep + (bs.get("amort") or 0) if yearly and op is not None and dep is not None else None
    m["roe"] = net / equity * 100 if yearly and net is not None and equity is not None and equity > 0 else None
    m["assets"], m["liab"], m["equity"] = bs.get("assets"), liab, equity
    return m


def fmt_pct(v):
    """비율(%) 표시. 문자(자본잠식)는 그대로, 없으면 '-'."""
    if isinstance(v, str):
        return v
    return "-" if v is None else f"{v:,.1f}%"


def fmt_times(v):
    return "-" if v is None else f"{v:,.1f}배"


def _num(v, nd=1):
    """다운로드용 숫자: 문자(자본잠식)는 그대로, 없으면 None."""
    return v if isinstance(v, str) or v is None else round(v, nd)


BS_KEYS = ("liab", "equity", "cash", "inv", "capex", "assets", "opcf", "borrow")


def build_bs_table(records, period, for_export=False, unit=DEFAULT_UNIT):
    """재무상태 기본 표: 기간, 부채비율, 현금성자산, 재고자산, (연도별) CAPEX, 순차입금, (연도별) 잉여현금흐름.
    for_export=True면 더 보기 표의 지표까지 모두 담은 다운로드용 표. 값이 하나도 없으면 빈 표."""
    yearly = period != "분기별"
    rows = []
    for r in sorted_records(records):
        bs = r.get("bs") or {}
        if not any(bs.get(k) is not None for k in BS_KEYS):
            continue
        m = bs_metrics(r, yearly)
        if for_export:
            row = {"기간": r["label"], "부채비율(%)": _num(m["debt_ratio"]),
                   f"현금성자산({unit})": num_amount(bs.get("cash"), unit), f"재고자산({unit})": num_amount(bs.get("inv"), unit)}
            if yearly:
                row[f"CAPEX({unit})"] = num_amount(bs.get("capex"), unit)
            row[f"순차입금({unit})"] = num_amount(m["net_debt"], unit)
            if yearly:
                row[f"잉여현금흐름({unit})"] = num_amount(m["fcf"], unit)
            row["순차입금비율(%)"] = _num(m["nd_ratio"])
            row["유동비율(%)"] = _num(m["cur_ratio"])
            if yearly:
                row["이자보상배율(배)"] = _num(m["int_cov"])
                row[f"영업활동현금흐름({unit})"] = num_amount(m["opcf"], unit)
                row[f"EBITDA({unit})"] = num_amount(m["ebitda"], unit)
                row["ROE(%)"] = _num(m["roe"])
            row[f"자산총계({unit})"] = num_amount(m["assets"], unit)
            row[f"부채총계({unit})"] = num_amount(m["liab"], unit)
            row[f"자본총계({unit})"] = num_amount(m["equity"], unit)
        else:
            row = {"기간": r["label"], "부채비율": fmt_pct(m["debt_ratio"]),
                   "현금성자산": fmt_amount(bs.get("cash"), unit), "재고자산": fmt_amount(bs.get("inv"), unit)}
            if yearly:
                row["CAPEX"] = fmt_amount(bs.get("capex"), unit)
            row["순차입금"] = fmt_amount(m["net_debt"], unit)
            if yearly:
                row["잉여현금흐름"] = fmt_amount(m["fcf"], unit)
        rows.append(row)
    return pd.DataFrame(rows)


def build_bs_more_table(records, period, unit=DEFAULT_UNIT):
    """재무상태 '더 보기' 표: 순차입금비율, 유동비율, (연도별) 이자보상배율·영업활동현금흐름·EBITDA·ROE, 자산·부채·자본 금액."""
    yearly = period != "분기별"
    rows = []
    for r in sorted_records(records):
        bs = r.get("bs") or {}
        if not any(bs.get(k) is not None for k in BS_KEYS):
            continue
        m = bs_metrics(r, yearly)
        row = {"기간": r["label"], "순차입금비율": fmt_pct(m["nd_ratio"]), "유동비율": fmt_pct(m["cur_ratio"])}
        if yearly:
            row["이자보상배율"] = fmt_times(m["int_cov"])
            row["영업활동현금흐름"] = fmt_amount(m["opcf"], unit)
            row["EBITDA"] = fmt_amount(m["ebitda"], unit)
            row["ROE"] = fmt_pct(m["roe"])
        row["자산총계"] = fmt_amount(m["assets"], unit)
        row["부채총계"] = fmt_amount(m["liab"], unit)
        row["자본총계"] = fmt_amount(m["equity"], unit)
        rows.append(row)
    return pd.DataFrame(rows)


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
# 업종 합산 (여러 회사 같은 기간 실적 합계)
# ------------------------------------------------------------
def latest_period(today=None):
    """지금 볼 수 있는 가장 최근 정기보고서 기간 (제출 기한 기준, 잠정실적 공시는 제외)."""
    t = today or TODAY
    md = (t.month, t.day)
    if md >= (11, 15):
        return t.year, "3Q"
    if md >= (8, 15):
        return t.year, "2Q"
    if md >= (5, 16):
        return t.year, "1Q"
    if md >= (3, 31):
        return t.year - 1, "4Q"
    return t.year - 1, "3Q"


def period_codes(period):
    """기간 -> 필요한 보고서 코드들. 4분기는 '연간 - 3분기 누적'이라 네 보고서가 모두 필요."""
    return {"연간": [ANNUAL], "1Q": [Q1], "2Q": [H1], "3Q": [Q3], "4Q": [Q1, H1, Q3, ANNUAL]}[period]


def period_values(reports, period):
    """보고서들 {코드: 데이터} -> 그 기간의 {rev, op, net}"""
    if period == "연간" or period in ("1Q", "2Q", "3Q"):
        df = reports.get(period_codes(period)[0])
        return {"rev": pick(df, REVENUE_NAMES, ids=REVENUE_IDS), "op": pick(df, OPERATING_NAMES, ids=OPERATING_IDS),
                "net": pick(df, NET_NAMES, ids=NET_IDS)}
    r = [reports.get(c) for c in (Q1, H1, Q3, ANNUAL)]
    return {
        "rev": quarter_values(*r, REVENUE_NAMES, ids=REVENUE_IDS)[3],
        "op": quarter_values(*r, OPERATING_NAMES, ids=OPERATING_IDS)[3],
        "net": quarter_values(*r, NET_NAMES, ids=NET_IDS)[3],
    }


def collect_industry(corps, year, period):
    """여러 회사의 올해·전년 같은 기간 실적. corps: [(회사명, 고유번호)] -> {회사명: {"now": {...}, "prev": {...}}}"""
    session, store = get_session(), get_cache_store()
    jobs = [(name, code, y, c) for name, code in corps for y in (year, year - 1) for c in period_codes(period)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda j: fetch(session, store, j[1], j[2], j[3]), jobs))
    got = {(j[0], j[2], j[3]): r for j, r in zip(jobs, results)}
    out = {}
    for name, _ in corps:
        out[name] = {
            y_key: period_values({c: got.get((name, y, c)) for c in period_codes(period)}, period)
            for y_key, y in (("now", year), ("prev", year - 1))
        }
    return out


def won_text(v):
    """원 -> 기사체 금액. 123조4567억원 / 4567억원 / -320억원"""
    if v is None:
        return "-"
    eok_total = int(round(abs(v) / 1e8))
    jo, rest = divmod(eok_total, 10000)
    if jo and rest:
        body = f"{jo:,}조{rest}억원"
    elif jo:
        body = f"{jo:,}조원"
    else:
        body = f"{rest}억원"  # 기사 표기: 7200억원 (쉼표 없음)
    return ("-" if v < 0 else "") + body


def change_text(c):
    """증감 -> 기사체. 8.8% 증가 / 3.1% 감소 / 흑자전환"""
    if c is None:
        return None
    if isinstance(c, str):
        return c
    return f"{abs(c):.1f}% {'증가' if c >= 0 else '감소'}"


def industry_result(data, unit):
    """조회 결과 -> (표, 합계 dict, 데이터 있는 회사 수)"""
    rows, tot_now, tot_prev = [], {"rev": 0, "op": 0, "net": 0}, {"rev": 0, "op": 0, "net": 0}
    has_now = {"rev": 0, "op": 0, "net": 0}
    both = {k: {"now": 0, "prev": 0, "n": 0} for k in ("rev", "op", "net")}
    used = 0
    for corp, d in data.items():
        now, prev = d["now"], d["prev"]
        if all(now[k] is None for k in ("rev", "op", "net")):
            continue
        used += 1
        for k in ("rev", "op", "net"):
            if now[k] is not None:
                tot_now[k] += now[k]
                has_now[k] += 1
                if prev[k] is not None:  # 전년 비교는 두 해 모두 값이 있는 회사끼리
                    both[k]["now"] += now[k]
                    both[k]["prev"] += prev[k]
                    both[k]["n"] += 1
        margin = now["op"] / now["rev"] * 100 if now["op"] is not None and now["rev"] else None
        rows.append({
            "회사": corp,
            "매출액": fmt_amount(now["rev"], unit), "매출 전년比": fmt_change(pct_change(now["rev"], prev["rev"])),
            "영업이익": fmt_amount(now["op"], unit), "영업이익 전년比": fmt_change(op_change(now["op"], prev["op"])),
            "영업이익률": f"{margin:.1f}%" if margin is not None else "-",
            "순이익": fmt_amount(now["net"], unit), "순이익 전년比": fmt_change(op_change(now["net"], prev["net"])),
        })
    total = {}
    for k in ("rev", "op", "net"):
        total[k] = tot_now[k] if has_now[k] else None
        b = both[k]
        total[k + "_chg"] = (pct_change if k == "rev" else op_change)(b["now"], b["prev"]) if b["n"] else None
    if rows:
        margin = total["op"] / total["rev"] * 100 if total["op"] is not None and total["rev"] else None
        rows.append({
            "회사": f"합계 ({used}개사)",
            "매출액": fmt_amount(total["rev"], unit), "매출 전년比": fmt_change(total["rev_chg"]),
            "영업이익": fmt_amount(total["op"], unit), "영업이익 전년比": fmt_change(total["op_chg"]),
            "영업이익률": f"{margin:.1f}%" if margin is not None else "-",
            "순이익": fmt_amount(total["net"], unit), "순이익 전년比": fmt_change(total["net_chg"]),
        })
    return pd.DataFrame(rows), total, used


def industry_sentence(label, used, year, period, total):
    """기사 첫 문장 초안."""
    when = f"{year}년" if period == "연간" else f"{year}년 {period[0]}분기"
    base = "전년 대비" if period == "연간" else "전년 동기 대비"
    parts = []
    for k, name in (("rev", "매출"), ("op", "영업이익"), ("net", "순이익")):
        v = total.get(k)
        if v is None:
            continue
        nm = "영업손실" if k == "op" and v < 0 else ("순손실" if k == "net" and v < 0 else name)
        chg = change_text(total.get(k + "_chg"))
        parts.append(f"{nm} {won_text(abs(v) if v < 0 else v)}" + (f"({base} {chg})" if chg else ""))
    if not parts:
        return ""
    return f"{label} {used}개사의 {when} 합산 실적은 " + ", ".join(parts) + "으로 집계됐다."


def total_row_style(row):
    """합계 줄은 굵게 + 옅은 배경."""
    bold = str(row.get("회사", "")).startswith("합계")
    return ["font-weight: 800; background-color: #eef4fb" if bold else "" for _ in row]



# ------------------------------------------------------------
# 임원 보수 (사업보고서·반기보고서)
# ------------------------------------------------------------
def fetch_pay(session, store, kind, corp_code, year, reprt_code):
    """보수 관련 API 한 종류 조회 -> DART 응답(dict) 또는 None(데이터 없음).
    옛 주소에서 데이터가 없으면 V2 주소로 다시 찾는다. ※ 작업 스레드에서 실행 (st.* 사용 금지)"""
    key = ("pay", kind, corp_code, year, reprt_code)
    cached = cache_lookup(store, key, CACHE_TTL)
    if cached is not _MISSING:
        return cached
    jo = None
    for api in PAY_APIS[kind]:
        params = {"crtfc_key": API_KEY, "corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code}
        jo = call_dart(session, f"https://opendart.fss.or.kr/api/{api}.json", params)
        if jo is not None:
            break
    cache_save(store, key, jo)
    return jo


def dart_rows(jo, need):
    """응답 안에서 need 항목들을 가진 행을 모두 찾는다 (옛 'list' 모양·V2 'group' 모양 모두).
    상위 항목의 접수번호·회사명은 아래 행에 물려준다."""
    out = []

    def walk(node, inherited):
        if isinstance(node, dict):
            here = dict(inherited)
            for k in ("rcept_no", "corp_name"):
                if isinstance(node.get(k), str) and node.get(k):
                    here[k] = node[k]
            if all(k in node for k in need):
                out.append({**here, **{k: v for k, v in node.items() if not isinstance(v, (list, dict))}})
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v, here)
        elif isinstance(node, list):
            for v in node:
                walk(v, inherited)

    walk(jo or {}, {})
    # 전기·전전기 금액이 함께 오는 경우(V2)에는 당기만
    return [r for r in out if str(r.get("fscl_year", "") or "").strip() in ("", "당기", "-")]


def person_key(name):
    return re.sub(r"\s+", "", str(name))


def pay_people(indiv, top5):
    """개인별 보수(5억 이상) 두 목록을 합쳐 사람별 한 줄로. -> {이름키: {nm, ofcps, amt, rcept_no}}"""
    people = {}
    for jo in (indiv, top5):
        for r in dart_rows(jo, ["nm", "mendng_totamt"]):
            amt = to_int(r.get("mendng_totamt"))
            k = person_key(r.get("nm", ""))
            if not k or amt is None:
                continue
            if k not in people or amt > people[k]["amt"]:  # 두 목록에 다 있으면 큰 금액(같은 금액이 보통)
                people[k] = {"nm": str(r["nm"]).strip(), "ofcps": str(r.get("ofcps", "") or "").strip(),
                             "amt": amt, "rcept_no": r.get("rcept_no", "")}
    return people


def board_summary(jo):
    """이사·감사 전체: (인원, 1인 평균 보수[원]). 없으면 (None, None)."""
    rows = dart_rows(jo, ["nmpr", "mendng_totamt"])
    total = sum(to_int(r.get("mendng_totamt")) or 0 for r in rows)
    count = sum(to_int(r.get("nmpr")) or 0 for r in rows)
    if not count:
        return None, None
    return count, total / count


def is_total(text):
    return str(text or "").replace(" ", "") in ("합계", "총계", "계", "전체", "소계", "합 계")


def parse_years(text):
    """평균 근속연수 '12.3' / '12년 3개월' / '12년' -> 12.3 / 12.25 / 12.0. 모르면 None."""
    t = str(text or "").replace(",", "").strip()
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*년?\s*(?:(\d+)\s*개월)?\s*$", t)
    if not m:
        return None
    return float(m.group(1)) + (int(m.group(2)) / 12 if m.group(2) else 0)


def employee_stats(jo):
    """직원 현황 -> {count, avg(1인 평균 급여, 원), tenure(평균 근속, 년), contract(계약직 비중 %), female(여성 비중 %)}.
    사업부문·성별로 나뉜 표에서 합계 줄은 빼고 더한다."""
    rows = dart_rows(jo, ["sm", "fyer_salary_totamt"])
    detail = [r for r in rows if not is_total(r.get("fo_bbm")) and not is_total(r.get("sexdstn"))]
    if not detail:  # 성별 합계만 있는 경우 -> 부문별 합계 줄을 더함
        detail = [r for r in rows if not is_total(r.get("fo_bbm"))]
    if not detail:  # 전체 합계 한 줄만 있는 경우
        detail = rows
    count = sum(to_int(r.get("sm")) or 0 for r in detail)
    total = sum(to_int(r.get("fyer_salary_totamt")) or 0 for r in detail)
    out = {"count": count or None, "avg": total / count if count and total else None,
           "tenure": None, "contract": None, "female": None}
    if not count:
        return out
    weighted = [(parse_years(r.get("avrg_cnwk_sdytrn")), to_int(r.get("sm")) or 0) for r in detail]
    weighted = [(y, n) for y, n in weighted if y is not None and n]
    if weighted:
        out["tenure"] = sum(y * n for y, n in weighted) / sum(n for _, n in weighted)
    if any(r.get("cnttk_co") not in (None, "", "-") for r in detail):
        out["contract"] = sum(to_int(r.get("cnttk_co")) or 0 for r in detail) / count * 100
    sexes = [str(r.get("sexdstn") or "").strip() for r in detail]
    if any(x.startswith("여") for x in sexes) and any(x.startswith("남") for x in sexes):
        out["female"] = sum(to_int(r.get("sm")) or 0 for r, x in zip(detail, sexes) if x.startswith("여")) / count * 100
    return out


def employee_summary(jo):
    """직원 현황: (직원 수, 1인 평균 급여[원])."""
    st_ = employee_stats(jo)
    return st_["count"], st_["avg"]


def collect_pay(corps, year, reprt_code):
    """여러 회사의 올해·전년 보수 자료를 동시에 조회. corps: [(회사명, 고유번호)]
    -> {회사명: {"now": {...}, "prev": {...}}}  (조회 실패는 DartApiError)"""
    session, store = get_session(), get_cache_store()
    kinds = ["emp"] if reprt_code in QUARTER_CODES else list(PAY_APIS)  # 분기보고서에는 임원 보수가 없다
    jobs = [(name, code, y, kind) for name, code in corps for y in (year, year - 1) for kind in kinds]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda j: fetch_pay(session, store, j[3], j[1], j[2], reprt_code), jobs))
    got = {(j[0], j[2], j[3]): r for j, r in zip(jobs, results)}
    out = {}
    for name, _ in corps:
        def part(y):
            return {
                "people": pay_people(got.get((name, y, "indiv")), got.get((name, y, "top5"))),
                "board": board_summary(got.get((name, y, "board"))),
            }
        now, prev = part(year), part(year - 1)
        now["emp"] = employee_summary(got.get((name, year, "emp")))
        now["emp_stats"] = employee_stats(got.get((name, year, "emp")))
        prev["emp_stats"] = employee_stats(got.get((name, year - 1, "emp")))
        out[name] = {"now": now, "prev": prev}
    return out


def eok(v, digits=1):
    """원 -> 억원 글자. 없으면 '-'."""
    return "-" if v is None else f"{v / 1e8:,.{digits}f}"


def pay_tables(data):
    """조회 결과 -> (개인별 표, 회사별 요약 표)"""
    people_rows, summary_rows = [], []
    for corp, d in data.items():
        prev_people = d["prev"]["people"]
        for k, p in d["now"]["people"].items():
            before = prev_people.get(k, {}).get("amt")
            people_rows.append({
                "회사": corp, "이름": p["nm"], "직위": p["ofcps"] or "-",
                "보수": eok(p["amt"]), "전년": eok(before), "증감률": fmt_change(pct_change(p["amt"], before)),
                "원문": DART_VIEW_URL.format(p["rcept_no"]) if p["rcept_no"] else None, "_amt": p["amt"],
            })
        (n_now, avg_now), (_, avg_prev) = d["now"]["board"], d["prev"]["board"]
        emp_n, emp_avg = d["now"]["emp"]
        summary_rows.append({
            "회사": corp,
            "이사·감사 인원": f"{n_now:,}" if n_now else "-",
            "임원 1인 평균": eok(avg_now, 2),
            "전년比": fmt_change(pct_change(avg_now, avg_prev)),
            "직원 수": f"{emp_n:,}" if emp_n else "-",
            "직원 1인 평균": eok(emp_avg, 2),
            "임원/직원": f"{avg_now / emp_avg:,.1f}배" if avg_now and emp_avg else "-",
        })
    people = pd.DataFrame(people_rows, columns=["회사", "이름", "직위", "보수", "전년", "증감률", "원문", "_amt"])
    people = people.sort_values("_amt", ascending=False, kind="stable").drop(columns="_amt").reset_index(drop=True)
    return people, pd.DataFrame(summary_rows)


def employee_table(data):
    """직원 현황 비교 표 (평균 연봉 높은 순)."""
    rows = []
    for corp, d in data.items():
        now, prev = d["now"].get("emp_stats") or {}, d["prev"].get("emp_stats") or {}
        if not now.get("count"):
            continue
        diff = now["count"] - prev["count"] if prev.get("count") else None
        rows.append({
            "회사": corp,
            "직원 수": f"{now['count']:,}",
            "증감(명)": (f"{diff:+,}" if diff else "0") if diff is not None else "-",
            "평균 연봉(만원)": f"{now['avg'] / 1e4:,.0f}" if now.get("avg") else "-",
            "연봉 전년比": fmt_change(pct_change(now.get("avg"), prev.get("avg"))),
            "평균 근속(년)": f"{now['tenure']:.1f}" if now.get("tenure") is not None else "-",
            "계약직 비중": f"{now['contract']:.1f}%" if now.get("contract") is not None else "-",
            "여성 비중": f"{now['female']:.1f}%" if now.get("female") is not None else "-",
            "_avg": now.get("avg") or 0,
        })
    df = pd.DataFrame(rows, columns=["회사", "직원 수", "증감(명)", "평균 연봉(만원)", "연봉 전년比", "평균 근속(년)",
                                     "계약직 비중", "여성 비중", "_avg"])
    return df.sort_values("_avg", ascending=False, kind="stable").drop(columns="_avg").reset_index(drop=True)


def change_style(cell):
    """증감 칸: 늘면 파랑, 줄면 빨강 (둘 다 굵게)."""
    t = str(cell)
    if t.startswith("+"):
        return f"font-weight: bold; color: {POSITIVE_COLOR}"
    if t.startswith("-") and t != "-":
        return f"font-weight: bold; color: {NEGATIVE_COLOR}"
    return ""



# ------------------------------------------------------------
# AI 공시 요약 (DART 원문 -> Gemini/Claude)
# ------------------------------------------------------------
class AiError(Exception):
    """AI 요약 실패 (키 오류·사용량 초과·원문 없음 등). 화면에 그대로 보여준다."""


def doc_text_from_zip(data):
    """DART 원본파일 zip -> 읽을 수 있는 글자. 표는 칸을 ' | ', 줄을 줄바꿈으로 바꿔 구조를 살린다."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise AiError("DART 원문 파일을 열 수 없어요. 잠시 후 다시 시도해주세요.") from e
    names = sorted(zf.namelist(), key=lambda n: (not n.endswith(".xml"), len(n), n))  # 본문(접수번호.xml)이 보통 가장 짧은 이름
    parts = []
    for name in names:
        raw = zf.read(name)
        for enc in ("utf-8", "cp949"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                text = None
        if text is None:
            continue
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?i)</(TD|TH|TE|TU)>", " | ", text)
        text = re.sub(r"(?i)</(TR|P|TITLE|TABLE|LIBRARY|SECTION-\d+|BR)\s*>|<BR\s*/?>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        text = re.sub(r"[ \t ]+", " ", text)
        text = re.sub(r"\s*\n\s*", "\n", text).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def download_document(session, rcept_no):
    """접수번호 -> DART 원본파일 zip 바이트. 원문이 없거나 DART 오류면 AiError."""
    try:
        res = session.get(DART_DOC_URL, params={"crtfc_key": API_KEY, "rcept_no": rcept_no}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise AiError("DART 서버 응답이 없거나 늦어지고 있습니다. 잠시 후 다시 시도해주세요.") from e
    data = res.content or b""
    if not data.startswith(b"PK"):  # zip이 아니면 오류 메시지 (XML 또는 JSON)
        msg = data.decode("utf-8", "ignore")
        code = (re.search(r"<status>(\d+)</status>", msg) or re.search(r'"status"\s*:\s*"(\d+)"', msg))
        code = code.group(1) if code else "?"
        if code in ("013", "014"):
            raise AiError("DART에 이 공시의 원문 파일이 아직 없어요. 잠시 후 다시 시도하거나 원문 링크로 확인해주세요.")
        if code == "020":
            raise AiError("DART API 하루 호출 한도를 초과했습니다. 잠시 후 또는 내일 다시 시도해주세요.")
        raise AiError(f"DART 원문을 받지 못했어요. [{code}]")
    return data


def fetch_document_text(session, store, rcept_no):
    """접수번호 -> 공시 원문 글자 (하루 저장). 원문이 없거나 DART 오류면 AiError."""
    key = ("doc", rcept_no)
    cached = cache_lookup(store, key, 24 * 3600)
    if cached is not _MISSING and cached:
        return cached
    text = doc_text_from_zip(download_document(session, rcept_no))
    if not text:
        raise AiError("원문에서 글자를 찾지 못했어요. 원문 링크로 확인해주세요.")
    cache_save(store, key, text)
    return text


AI_SYSTEM = (
    "너는 한국 경제신문 산업부 기자를 돕는 공시 분석 보조자다. "
    "반드시 사용자가 준 DART 공시 원문에 적힌 내용만 근거로 쓴다. "
    "원문에 없는 숫자·사실·전망·평가·시장 반응을 절대 만들지 않는다. "
    "숫자는 원문 표기 그대로(단위 포함) 옮기고, 원문에서 확인되지 않는 항목은 '원문에 없음'이라고 쓴다. "
    "한국어로, 짧고 명확한 기사체로 쓴다."
)


def ai_prompt(title, text):
    truncated = len(text) > MAX_DOC_CHARS
    body = text[:MAX_DOC_CHARS]
    note = f"(원문이 길어 앞부분 {MAX_DOC_CHARS:,}자만 제공됨)\n" if truncated else ""
    return (
        f"공시 제목: {title}\n{note}\n"
        "아래 원문을 읽고 다음 형식 그대로 요약해줘.\n\n"
        "**핵심 요약**\n- (무슨 공시이고 무엇을 결정·보고했는지 3줄 이내)\n\n"
        "**주요 숫자**\n- 항목: 값 (공시 종류에 맞는 핵심 항목만. 예: 계약금액, 최근 매출액 대비 비율, 계약기간, 계약상대방, "
        "증자 규모·발행가·방식·자금 용도, 실적 증감, 취득·처분 금액과 목적 등. 최대 8개)\n\n"
        "**눈여겨볼 점**\n- (원문에 적힌 조건·해지 사유·정정 내용·유의사항 등 최대 3개. 없으면 '특이사항 없음')\n\n"
        f"[공시 원문]\n{body}"
    )


def call_gemini(session, prompt, system=None, json_mode=False):
    """Gemini 호출. 서버가 붐비면(500·502·503·504) 잠깐 쉬었다 다시 시도하고, 그래도 안 되면 가벼운 모델로 바꿔 시도.
    -> (요약 글, 실제로 쓴 모델)"""
    body = {
        "systemInstruction": {"parts": [{"text": system or AI_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }
    if json_mode:  # 표로 쓸 결과는 JSON으로만 받는다
        body["generationConfig"]["responseMimeType"] = "application/json"
    models = [GEMINI_MODEL] + ([GEMINI_FALLBACK] if GEMINI_FALLBACK and GEMINI_FALLBACK != GEMINI_MODEL else [])
    res, model = None, GEMINI_MODEL
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(3):  # 같은 모델로 최대 3번 (2초, 4초 쉬고)
            try:
                res = session.post(url, json=body, headers={"x-goog-api-key": GEMINI_KEY}, timeout=AI_TIMEOUT)
            except Exception:
                res = None
            if res is not None and res.status_code not in (500, 502, 503, 504):
                break
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
        if res is not None and res.status_code not in (500, 502, 503, 504):
            break
    if res is None:
        raise AiError("Gemini 서버 응답이 없거나 늦어지고 있어요. 잠시 후 다시 시도해주세요.")
    if res.status_code in (500, 502, 503, 504):
        raise AiError("Gemini 서버가 지금 붐벼서 응답하지 못했어요 (API 키 문제는 아니에요). 1~2분 뒤 다시 눌러주세요.")
    if res.status_code == 429:
        raise AiError("Gemini 사용량 한도를 넘었어요 (무료 등급은 분·일 한도가 있어요). 잠시 후 다시 시도해주세요.")
    if res.status_code == 404:
        raise AiError(f"Gemini 모델 '{model}'을 찾지 못했어요. Secrets의 GEMINI_MODEL 이름을 확인해주세요.")
    if res.status_code in (400, 401, 403):
        try:
            msg = res.json().get("error", {}).get("message", "")
        except Exception:
            msg = ""
        if "key" in msg.lower() or res.status_code in (401, 403):
            raise AiError("Gemini API 키가 올바르지 않아요. Secrets의 GEMINI_API_KEY를 확인해주세요.")
        raise AiError(f"Gemini 요청 오류: {msg or res.status_code}")
    if res.status_code != 200:
        raise AiError(f"Gemini 응답 오류 [{res.status_code}]")
    data = res.json()
    cands = data.get("candidates") or []
    text = "".join(p.get("text", "") for c in cands[:1] for p in (c.get("content") or {}).get("parts", []) if not p.get("thought"))
    if not text.strip():
        reason = (cands[0].get("finishReason") if cands else None) or (data.get("promptFeedback") or {}).get("blockReason")
        raise AiError(f"Gemini가 요약을 돌려주지 않았어요{f' ({reason})' if reason else ''}. 다시 시도하거나 원문 링크로 확인해주세요.")
    return text.strip(), model


def call_claude(session, prompt, system=None, json_mode=False):
    """Claude 호출. 서버가 붐비면(429·500·502·503·529) 잠깐 쉬었다 두 번 더 시도한다."""
    if json_mode:  # Claude는 JSON 모드가 따로 없으니 말로 요청한다 (앞뒤에 붙은 ```json 표시는 parse_json이 걷어냄)
        prompt += "\n\n설명 없이 JSON만 출력해라."
    body = {"model": ANTHROPIC_MODEL, "max_tokens": 4000, "system": system or AI_SYSTEM,
            "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    res = None
    for attempt in range(3):
        try:
            res = session.post("https://api.anthropic.com/v1/messages", json=body, headers=headers, timeout=AI_TIMEOUT)
        except Exception:
            res = None
        if res is not None and res.status_code not in (429, 500, 502, 503, 529):
            break
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    if res is None:
        raise AiError("Claude 서버 응답이 없거나 늦어지고 있어요. 잠시 후 다시 시도해주세요.")
    if res.status_code in (401, 403):
        raise AiError("Claude API 키가 올바르지 않아요. Secrets의 ANTHROPIC_API_KEY를 확인해주세요.")
    if res.status_code == 429:
        raise AiError("Claude 사용량 한도를 넘었어요. 잠시 후 다시 시도해주세요.")
    if res.status_code in (500, 502, 503, 529):
        raise AiError("Claude 서버가 지금 붐벼서 응답하지 못했어요 (API 키 문제는 아니에요). 1~2분 뒤 다시 눌러주세요.")
    if res.status_code == 404:
        raise AiError(f"Claude 모델 '{ANTHROPIC_MODEL}'을 찾지 못했어요. Secrets의 ANTHROPIC_MODEL 이름을 확인해주세요.")
    if res.status_code != 200:
        try:
            msg = res.json().get("error", {}).get("message", "")
        except Exception:
            msg = ""
        if "credit balance" in msg.lower():
            raise AiError("Anthropic 크레딧 잔액이 부족해요. console.anthropic.com의 Plans & Billing에서 충전해주세요.")
        raise AiError(f"Claude 응답 오류 [{res.status_code}] {msg}".strip())
    text = "".join(b.get("text", "") for b in res.json().get("content", []) if b.get("type") == "text")
    if not text.strip():
        raise AiError("Claude가 요약을 돌려주지 않았어요. 다시 시도해주세요.")
    return text.strip()


def ai_summary(rcept_no, title):
    """공시 하나 요약 (같은 공시는 일주일 저장). -> (요약 글, 원문이 잘렸는지, 쓴 모델)"""
    session, store = get_session(), get_cache_store()
    model = GEMINI_MODEL if AI_PROVIDER == "gemini" else ANTHROPIC_MODEL
    key = ("ai", AI_PROVIDER, model, rcept_no)
    cached = cache_lookup(store, key, AI_TTL)
    if cached is not _MISSING and cached:
        return cached
    text = fetch_document_text(session, store, rcept_no)
    prompt = ai_prompt(title, text)
    summary, used = ai_generate(prompt)
    value = (summary, len(text) > MAX_DOC_CHARS, used)
    cache_save(store, key, value)
    return value



def ai_generate(prompt, system=None, json_mode=False):
    """설정된 AI(Gemini 또는 Claude)로 글을 받는다. -> (글, 쓴 모델)"""
    session = get_session()
    if AI_PROVIDER == "gemini":
        return call_gemini(session, prompt, system=system, json_mode=json_mode)
    return call_claude(session, prompt, system=system, json_mode=json_mode), ANTHROPIC_MODEL


def ai_model_name():
    return GEMINI_MODEL if AI_PROVIDER == "gemini" else ANTHROPIC_MODEL


def ai_cached(key, make):
    """AI 결과 저장소: 같은 질문은 일주일 동안 다시 부르지 않는다."""
    store = get_cache_store()
    full = ("ai2", AI_PROVIDER, ai_model_name()) + tuple(key)
    hit = cache_lookup(store, full, AI_TTL)
    if hit is not _MISSING and hit:
        return hit
    value = make()
    cache_save(store, full, value)
    return value


# ------------------------------------------------------------
# 정기보고서(사업·반기·분기) AI 분석
#  - 보고서 전체를 보내지 않고, 분석 항목마다 필요한 목차(II. 사업의 내용 등)만 잘라서 보낸다.
# ------------------------------------------------------------
REPORT_UNI_ROMAN = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V", "Ⅵ": "VI", "Ⅶ": "VII", "Ⅷ": "VIII", "Ⅸ": "IX", "Ⅹ": "X", "Ⅺ": "XI", "Ⅻ": "XII"}
# 정기보고서 대분류 (I~XII). 로마자 번호가 아니라 제목 글자로 찾는다 (재무제표 항목 'I. 매출액'과 헷갈리지 않게)
REPORT_MAIN_SECTIONS = [
    ("co", r"회사의\s*개요"), ("biz", r"사업의\s*내용"), ("fin", r"재무에\s*관한\s*사항"), ("mdna", r"이사의\s*경영진단"),
    ("audit", r"(?:회계)?감사인의\s*감사의견"), ("board", r"이사회\s*등\s*회사의\s*기관"), ("holder", r"주주에\s*관한\s*사항"),
    ("staff", r"임원\s*및\s*직원"), ("affil", r"계열회사\s*등에\s*관한"), ("related", r"대주주\s*등과의\s*거래"),
    ("etc", r"그\s*밖에\s*투자자\s*보호"), ("detail", r"상세표"),
]
REPORT_SUB_SECTIONS = {
    "biz": [("overview", r"사업의\s*개요"), ("products", r"주요\s*제품\s*및\s*서비스"), ("plant", r"원재료\s*및\s*생산설비"),
            ("sales", r"매출\s*및\s*수주상황"), ("risk", r"위험관리\s*및\s*파생거래"), ("rnd", r"주요계약\s*및\s*연구개발활동"),
            ("etc", r"기타\s*참고사항"), ("fin_ops", r"영업의\s*현황"), ("fin_deriv", r"파생상품거래\s*현황"),
            ("fin_equip", r"영업설비"), ("fin_health", r"재무건전성")],
    "audit": [("ext", r"외부감사에\s*관한\s*사항"), ("internal", r"내부통제에\s*관한\s*사항")],
    "etc": [("progress", r"공시내용\s*진행\s*및\s*변경사항"), ("contingent", r"우발부채\s*등에\s*관한\s*사항"),
            ("sanction", r"제재\s*등과\s*관련된\s*사항"), ("after", r"작성기준일\s*이후\s*발생한\s*주요사항")],
    "detail": [("consol", r"연결대상\s*종속회사\s*현황"), ("affil", r"계열회사\s*현황"), ("invest", r"타법인출자\s*현황"), ("rnd", r"연구개발실적")],
}


def _is_heading_line(s):
    """제목 줄 후보: 짧고, 목차의 점선(…..)이 없는 줄."""
    return 0 < len(s) <= 80 and "…" not in s and "....." not in s


def split_main_sections(text):
    """정기보고서 글 -> {대분류 키: 그 부분 글}. 같은 제목이 목차에도 나오면, 뒤에 글이 가장 길게 이어지는 쪽(본문)을 쓴다."""
    for k, v in REPORT_UNI_ROMAN.items():
        text = text.replace(k, v)
    lines = text.replace("\x0c", "\n").split("\n")
    cands = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not _is_heading_line(s):
            continue
        m = re.match(r"^[IVX]+\s*\.\s*(.+)$", s)
        if not m:
            continue
        for key, pat in REPORT_MAIN_SECTIONS:
            if re.match(pat, m.group(1)):
                cands.append((i, key))
                break
    if not cands:
        return {}
    nxt = {i: (cands[j + 1][0] if j + 1 < len(cands) else len(lines)) for j, (i, _) in enumerate(cands)}
    best = {}
    for i, key in cands:
        span = nxt[i] - i
        if key not in best or span > best[key][1]:
            best[key] = (i, span)
    starts = sorted((i, key) for key, (i, _) in best.items())
    out = {}
    for j, (i, key) in enumerate(starts):
        end = starts[j + 1][0] if j + 1 < len(starts) else len(lines)
        out[key] = "\n".join(lines[i + 1:end]).strip()
    return out


def report_subsections(main_text, main_key):
    """대분류 안의 '1. 제목' 소제목별 글. 같은 제목이 여러 개(예: 제조업·금융업)면 이어 붙인다."""
    specs = REPORT_SUB_SECTIONS.get(main_key, [])
    lines = main_text.split("\n")
    heads = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not _is_heading_line(s):
            continue
        m = re.match(r"^\d{1,2}\s*\.\s*(?:\([^)]{1,12}\))?\s*(.+)$", s)
        if not m:
            continue
        for key, pat in specs:
            if re.match(pat, m.group(1)):
                heads.append((i, key))
                break
    out = {}
    for j, (i, key) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        out[key] = (out.get(key, "") + "\n" + "\n".join(lines[i + 1:end])).strip()
    return out


def report_fin_note(fin_text, name_pat):
    """재무 주석 한 항목 ('16. 우발부채와 약정사항 (연결)'). 연결재무제표 주석을 먼저 찾는다."""
    lines = fin_text.split("\n")
    found = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not _is_heading_line(s):
            continue
        m = re.match(r"^(\d{1,2})\s*\.\s*(?:" + name_pat + r").*$", s)
        if m:
            found.append((i, int(m.group(1)), "(연결)" in s))
    if not found:
        return ""
    found.sort(key=lambda x: (not x[2], x[0]))
    i, n, _ = found[0]
    end = len(lines)
    for k in range(i + 1, len(lines)):
        s = lines[k].strip()
        if _is_heading_line(s) and re.match(rf"^{n + 1}\s*\.\s*\S", s):
            end = k
            break
    return "\n".join(lines[i + 1:end]).strip()


# (블록 이름: 글 자르는 최대 글자 수) - 한 블록이 아주 길어도 이만큼만 저장한다
REPORT_BLOCK_CAPS = {
    "mdna": 45000, "holder": 30000, "related": 20000, "affil": 45000, "detail_invest": 20000, "detail_rnd": 20000,
    "note_contingent": 12000, "note_related": 25000, "note_segment": 15000,
}
REPORT_NOTES = {  # 재무 주석에서 따로 뽑는 항목: 블록 이름 -> 주석 제목 정규식
    "note_contingent": r"우발부채", "note_related": r"특수관계자", "note_segment": r"(?:영업)?부문(?:별)?\s*(?:보고|정보)|영업부문",
}


def extract_report_blocks(text):
    """정기보고서 글 -> {블록 이름: 글}. 분석에 쓰는 부분만 골라 둔다 (저장 용량을 줄이려고 블록마다 길이 제한)."""
    main = text.split("\n\n")[0]  # doc_text_from_zip: 본문 파일이 맨 앞 (뒤는 첨부 문서)
    sec = split_main_sections(main) or split_main_sections(text)
    if not sec:
        raise AiError("이 보고서에서 '사업의 내용', '재무에 관한 사항' 같은 목차를 찾지 못했어요. 정기보고서(사업·반기·분기)가 맞는지 확인하거나 원문으로 보세요.")
    blocks = {}

    def put(key, value, cap=30000):
        if value and value.strip():
            blocks[key] = value.strip()[:cap]

    for main_key, prefix in (("biz", "biz"), ("audit", "audit"), ("etc", "etc"), ("detail", "detail")):
        for sub_key, value in report_subsections(sec.get(main_key, ""), main_key).items():
            if f"{prefix}_{sub_key}" not in REPORT_BLOCK_LABELS:  # 분석에 안 쓰는 부분은 저장하지 않는다
                continue
            put(f"{prefix}_{sub_key}", value, REPORT_BLOCK_CAPS.get(f"{prefix}_{sub_key}", 30000))
    for key in ("mdna", "holder", "related", "affil"):
        put(key, sec.get(key), REPORT_BLOCK_CAPS[key])
    for key, pat in REPORT_NOTES.items():
        put(key, report_fin_note(sec.get("fin", ""), pat), REPORT_BLOCK_CAPS[key])
    return blocks


def periodic_blocks(rcept_no):
    """접수번호 -> 분석용 블록 (일주일 저장). 원문을 받아 자르는 데 몇 초 걸린다."""
    session, store = get_session(), get_cache_store()
    key = ("rblocks", rcept_no)
    cached = cache_lookup(store, key, AI_TTL)
    if cached is not _MISSING and cached:
        return cached
    text = doc_text_from_zip(download_document(session, rcept_no))
    if not text:
        raise AiError("원문에서 글자를 찾지 못했어요. 원문 링크로 확인해주세요.")
    blocks = extract_report_blocks(text)
    cache_save(store, key, blocks)
    return blocks


# 블록 이름 -> 화면·프롬프트에 쓰는 이름 (AI가 근거 위치를 적을 때도 이 이름을 쓴다)
REPORT_BLOCK_LABELS = {
    "mdna": "IV. 이사의 경영진단 및 분석의견", "biz_overview": "II-1. 사업의 개요", "biz_products": "II-2. 주요 제품 및 서비스",
    "biz_plant": "II-3. 원재료 및 생산설비", "biz_sales": "II-4. 매출 및 수주상황", "biz_risk": "II-5. 위험관리 및 파생거래",
    "biz_rnd": "II-6. 주요계약 및 연구개발활동", "biz_fin_ops": "II(금융업). 영업의 현황", "biz_fin_equip": "II(금융업). 영업설비",
    "audit_ext": "V-1. 외부감사에 관한 사항", "audit_internal": "V-2. 내부통제에 관한 사항",
    "etc_contingent": "XI-2. 우발부채 등에 관한 사항", "etc_sanction": "XI-3. 제재 등과 관련된 사항", "etc_after": "XI-4. 작성기준일 이후 발생한 주요사항",
    "holder": "VII. 주주에 관한 사항", "related": "X. 대주주 등과의 거래내용", "affil": "IX. 계열회사 등에 관한 사항",
    "detail_invest": "XII-3. 타법인출자 현황(상세)", "detail_rnd": "XII-4. 연구개발실적(상세)",
    "note_contingent": "재무 주석 > 우발부채와 약정사항", "note_related": "재무 주석 > 특수관계자와의 거래", "note_segment": "재무 주석 > 부문(영업부문) 정보",
}
MAX_REPORT_CHARS = 75_000  # 한 번에 AI에 보내는 발췌 최대 글자 수 (분석 항목의 블록을 합친 길이)

REPORT_COMMON_FORMAT = (
    "아래 형식으로 써라. 발췌에 없는 항목은 건너뛰고, 있는데 '해당사항 없음'이면 그대로 적는다.\n"
    "**한 줄 요약**: 이 항목에서 기사로 가장 중요한 한 문장\n"
    "**핵심 포인트**: 3~6개 항목(- 로 시작). 숫자는 단위·기간과 함께 원문 그대로. 각 항목 끝에 근거 위치를 [ ] 안에 적는다 (예: [II-4. 매출 및 수주상황])\n"
    "**기사로 쓴다면 확인할 것**: 2~3개 항목(- 로 시작). 발췌만으로는 알 수 없는 배경·원인 중 취재로 확인할 질문"
)

# 분석 항목: key, 이름, 설명, 읽을 블록 [(블록 이름)], 지시문
PERIODIC_ANALYSES = [
    dict(key="diff", label="직전 보고서 대비 달라진 점", blocks=["biz_overview", "biz_products", "biz_plant", "biz_sales", "biz_risk", "audit_ext", "etc_contingent", "etc_sanction"],
         needs_prev=True, help="같은 기간 직전 보고서와 비교해 새로 생기거나 사라진 문장 (사업·위험·소송·제재 항목 중심)",
         task="아래는 직전 보고서와 이번 보고서를 항목별로 비교해 기계적으로 뽑은 '새로 생긴 문장'과 '없어진 문장'이다 (숫자만 바뀐 줄은 제외됨). "
              "이 중 의미 있는 변화를 골라라: 신규 사업·제품·투자 계획, 중단·철수, 위험 요인 문구의 변화, 소송·제재의 신규 발생이나 종결, 감사 관련 변화. "
              "단순 표현 다듬기와 날짜·숫자 갱신은 무시한다. 변화마다 '직전 → 이번' 형태로 쓴다."),
    dict(key="mdna", label="경영진단 요약 (실적 변동 원인·전망)", blocks=["mdna"], needs_prev=False,
         help="회사가 직접 설명하는 실적 변동 원인, 사업부문별 설명, 자금조달, 향후 전망·위험 요인",
         task="이사의 경영진단 및 분석의견에서 ① 이번 기간 실적(매출·영업이익 등)이 변한 이유로 회사가 든 요인 ② 사업부문별 설명 "
              "③ 유동성·자금조달(차입·사채·투자 재원) ④ 향후 전망·계획·위험 요인을 정리해라. 회사의 설명은 '회사는 ~라고 설명' 식으로 회사의 주장임을 드러낸다."),
    dict(key="sales", label="매출 구성·주요 고객", blocks=["biz_products", "biz_sales", "note_segment", "biz_fin_ops"], needs_prev=False,
         help="제품·지역별 매출과 비중, 판매경로, 주요 매출처, 수주상황, 사업부문별 매출·이익",
         task="매출 구성을 정리해라: 제품·서비스별/지역별 매출과 비중, 매출 유형, 판매경로(직판·대리점·수출), 주요 매출처(특정 고객 매출 비중), 수주 잔고·수주상황, "
              "사업부문별 매출·이익 비중. 비중이 큰 항목과 한쪽(고객·지역·제품)으로 쏠린 정도를 짚고, 전기·전년 대비 숫자가 표에 있으면 함께 적는다."),
    dict(key="risk", label="위험 신호 점검 (감사의견·소송·제재)", blocks=["audit_ext", "audit_internal", "etc_contingent", "etc_sanction", "etc_after", "note_contingent"], needs_prev=False,
         help="감사의견, 강조사항·핵심감사사항, 내부회계, 소송·우발채무·채무보증, 제재, 보고기간 이후 중요사항",
         task="위험 신호를 점검해라. 항목마다 '있음/없음/확인 불가'와 내용을 적는다: 감사의견 종류(적정·한정·부적정·의견거절), 강조사항·기타사항, 핵심감사사항(KAM) 제목, "
              "계속기업 불확실성 언급, 내부회계관리제도 검토·감사 의견, 감사 보수·감사시간 변화, 소송(원고·피고·금액), 채무보증·담보·우발부채(금액), 제재 현황(행정·사법·과태료), "
              "작성기준일 이후 중요사항. 위험하다는 평가는 하지 말고 발췌에 적힌 사실만 쓴다."),
    dict(key="plant", label="생산능력·가동률·설비투자", blocks=["biz_plant", "biz_fin_equip"], needs_prev=False,
         help="제품별 생산능력·생산실적·가동률, 주요 생산설비, 진행·계획 중인 시설투자, 원재료 조달",
         task="생산능력·생산실적·가동률(제품별·사업장별, 기간별 숫자), 주요 생산설비(사업장·면적), 시설투자(진행 중·계획 중 금액, 목적, 예상 완료 시기), "
              "주요 원재료 매입 구조와 가격 변동을 정리해라. 기간별 표가 있으면 숫자를 시간순으로 적는다."),
    dict(key="rnd", label="연구개발 (비용·조직·과제)", blocks=["biz_rnd", "detail_rnd"], needs_prev=False,
         help="연구개발비 규모와 매출 대비 비율, 연구 조직·인력, 주요 연구 과제·실적, 경영상 주요 계약",
         task="연구개발비용(금액, 매출 대비 비율, 비용·자산 처리), 연구개발 조직·인력, 주요 연구개발 실적·과제(제품·기술명, 시기), 경영상 주요 계약(상대방·기간·내용)을 정리해라. "
              "연구개발비 추이 표가 있으면 기간별 숫자를 적는다."),
    dict(key="affil", label="계열회사·타법인 출자 변동", blocks=["affil", "detail_invest"], needs_prev=False,
         help="계열회사 수(상장·비상장) 증감, 신규 편입·제외, 타법인 출자 취득·처분, 지분율·장부가",
         task="계열회사 수(상장·비상장) 증감, 신규 편입·제외된 회사(이름), 주요 타법인 출자의 변동(취득·처분·평가, 지분율, 장부가액), 임원 겸직 현황 중 눈에 띄는 것을 정리해라. "
              "M&A·지분 거래의 단서가 되는 것을 우선한다."),
    dict(key="related", label="특수관계자·대주주 거래", blocks=["related", "note_related"], needs_prev=False,
         help="특수관계자·대주주와의 거래 상대방·유형·금액, 채권·채무 잔액, 계열회사와의 내부거래",
         task="특수관계자·대주주와의 거래를 거래액이 큰 순으로 정리해라: 상대방, 거래 유형(매출·매입·자금대여·지급보증 등), 금액, 채권·채무 잔액, 계열회사와의 내부거래 규모. "
              "지원성 거래나 일감 몰아주기를 의심할 만한 단서는 '확인 필요'로만 표시하고 단정하지 않는다."),
    dict(key="holder", label="최대주주·지분 변동", blocks=["holder"], needs_prev=False,
         help="최대주주·특수관계인 지분율, 5% 이상 주주, 소액주주, 최대주주 변경, 주가·거래실적",
         task="최대주주 및 특수관계인 지분(주식 수·지분율, 기초 대비 변동), 5% 이상 주주(국민연금 등), 소액주주 현황(주주 수·지분율), 최대주주 변경 이력, "
              "주가·거래량 요약, 자기주식 취득·처분이 있으면 함께 정리해라."),
]
PERIODIC_ANALYSIS_BY_KEY = {a["key"]: a for a in PERIODIC_ANALYSES}

AI_PERIODIC_SYSTEM = (
    "너는 중앙일보 산업부 기자를 돕는 정기보고서(사업·반기·분기보고서) 분석 보조자다. 사용자가 준 [발췌]에 적힌 내용만 근거로 쓴다. "
    "발췌에 없는 숫자·사실·원인·평가·전망을 절대 만들지 않는다. 숫자는 단위와 기간까지 원문 표기 그대로 옮기고 새로 계산하지 않는다. "
    "표는 칸을 ' | '로 구분해 줄로 나열한 것이다. 발췌에서 확인되지 않으면 '보고서에 없음'이라고 쓴다. "
    "회사가 쓴 설명은 회사의 주장으로 구분해서 쓴다. 한국어, 짧고 명확한 기사체로 쓴다."
)


def report_period_of(name):
    """보고서명 '사업보고서 (2025.12)' -> (연, 월) 또는 None."""
    m = re.search(r"\((\d{4})\.(\d{2})\)", str(name or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def find_prev_periodic(rows, cur):
    """같은 종류 보고서(사업·반기·분기)의 1년 전 같은 기간 보고서. 없으면 None. 정정본이 여럿이면 가장 늦게 낸 것."""
    kind = re.sub(r"\s*\(\d{4}\.\d{2}\)\s*$", "", strip_correction(cur.get("report_nm"))).strip()
    per = report_period_of(cur.get("report_nm"))
    if not per:
        return None
    want = (per[0] - 1, per[1])
    cands = [r for r in rows if report_period_of(r.get("report_nm")) == want
             and re.sub(r"\s*\(\d{4}\.\d{2}\)\s*$", "", strip_correction(r.get("report_nm"))).strip() == kind]
    return max(cands, key=lambda r: str(r.get("rcept_no", ""))) if cands else None


def _prose_lines(text):
    """글에서 문장 줄만 (한글이 6자 이상). 숫자는 #으로 바꿔 비교용 키를 함께 돌려준다."""
    out = []
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) >= 15 and len(re.findall(r"[가-힣]", line)) >= 6:
            out.append((line, re.sub(r"[\d,.]+", "#", line)))
    return out


def diff_report_text(prev_text, cur_text, cap=12000):
    """두 글의 문장 줄을 비교해 (새로 생긴 줄, 없어진 줄). 숫자만 다른 줄은 같은 줄로 본다."""
    a, b = _prose_lines(prev_text), _prose_lines(cur_text)
    sm = difflib.SequenceMatcher(None, [k for _, k in a], [k for _, k in b], autojunk=False)
    added, removed = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed += [line for line, _ in a[i1:i2]]
        if tag in ("replace", "insert"):
            added += [line for line, _ in b[j1:j2]]
    def clip(lines):
        out, n = [], 0
        for line in lines:
            n += len(line) + 1
            if n > cap:
                out.append("(이하 생략)")
                break
            out.append(line)
        return out
    return clip(added), clip(removed)


def periodic_input(blocks, spec, prev_blocks=None):
    """분석 항목에 보낼 발췌. -> (발췌 글, 읽은 블록 이름들, 못 찾은 블록 이름들, 길어서 잘렸는지)"""
    read, missing, parts, truncated, total = [], [], [], False, 0
    if spec["key"] == "diff":
        for key in spec["blocks"]:
            cur, prev = blocks.get(key), (prev_blocks or {}).get(key)
            if not cur or not prev:
                missing.append(REPORT_BLOCK_LABELS[key])
                continue
            added, removed = diff_report_text(prev, cur)
            read.append(REPORT_BLOCK_LABELS[key])
            if not added and not removed:
                continue
            parts.append(f"[{REPORT_BLOCK_LABELS[key]}]\n<새로 생긴 문장>\n" + ("\n".join(added) or "(없음)")
                         + "\n<없어진 문장>\n" + ("\n".join(removed) or "(없음)"))
        text = "\n\n".join(parts)
        return text[:MAX_REPORT_CHARS], read, missing, len(text) > MAX_REPORT_CHARS
    for key in spec["blocks"]:
        value = blocks.get(key)
        if not value:
            missing.append(REPORT_BLOCK_LABELS[key])
            continue
        room = MAX_REPORT_CHARS - total
        if room <= 0:
            truncated = True
            continue
        if len(value) > room:
            value, truncated = value[:room], True
        total += len(value)
        read.append(REPORT_BLOCK_LABELS[key])
        parts.append(f"[{REPORT_BLOCK_LABELS[key]}]\n{value}")
    return "\n\n".join(parts), read, missing, truncated


def periodic_analysis(corp, row, item_key, prev_row=None):
    """정기보고서 하나에 분석 항목 하나. -> dict(text, model, read, missing, chars, truncated, empty)
    발췌가 거의 없으면(해당사항 없음 등) AI를 부르지 않고 empty=True와 발췌 글을 돌려준다."""
    spec = PERIODIC_ANALYSIS_BY_KEY[item_key]
    rno = str(row["rcept_no"]).strip()
    blocks = periodic_blocks(rno)
    prev_blocks = periodic_blocks(str(prev_row["rcept_no"]).strip()) if spec["needs_prev"] and prev_row else None
    body, read, missing, truncated = periodic_input(blocks, spec, prev_blocks)
    info = dict(read=read, missing=missing, chars=len(body), truncated=truncated, text="", model=None, empty=False, excerpt=body)
    useful = re.sub(r"\[[^\]]*\]|\s+", "", body)
    if spec["needs_prev"] and read and not body.strip():
        info.update(empty=True, text="직전 보고서와 비교했을 때 문장이 바뀐 곳이 없어요. (숫자만 달라진 줄은 비교에서 뺐어요.)")
        return info
    if len(useful) < (20 if spec["needs_prev"] else 60):  # 비교는 바뀐 문장 하나만 있어도 의미가 있다
        info["empty"] = True
        return info
    title = f"{corp} {strip_correction(row.get('report_nm'))}"
    head = f"기업: {corp}\n보고서: {str(row.get('report_nm', '')).strip()} (접수번호 {rno})\n분석 항목: {spec['label']}\n"
    if prev_row:
        head += f"비교 대상(직전 보고서): {str(prev_row.get('report_nm', '')).strip()}\n"
    prompt = f"{head}\n{spec['task']}\n\n{REPORT_COMMON_FORMAT}\n\n[발췌]\n{body}"
    prev_no = str(prev_row["rcept_no"]).strip() if prev_row else ""
    text, used = ai_cached(("rep", rno, item_key, prev_no), lambda: ai_generate(prompt, system=AI_PERIODIC_SYSTEM))
    info.update(text=text, model=used, title=title)
    return info



def parse_json(text):
    """AI 답에서 JSON 부분만 꺼낸다 (```json 같은 표시가 붙어 와도). 실패하면 AiError."""
    t = re.sub(r"^```(?:json)?|```$", "", str(text).strip(), flags=re.M).strip()
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise AiError("AI 답을 표로 바꾸지 못했어요. 다시 시도해주세요.")
    i = min(starts)
    j = max(t.rfind("}"), t.rfind("]"))
    try:
        return json.loads(t[i:j + 1])
    except ValueError as e:
        raise AiError("AI 답을 표로 바꾸지 못했어요. 다시 시도해주세요.") from e


def strip_correction(name):
    """[기재정정]·[첨부정정] 같은 앞머리를 뗀 보고서명."""
    return re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", str(name or "")).strip()


def is_correction(name):
    return bool(re.match(r"^\s*\[[^\]]*정정[^\]]*\]", str(name or "")))


def find_previous_version(r, rows):
    """정정 공시의 바로 앞 버전(같은 보고서명, 더 이른 접수번호). 불러온 목록에 없으면 1년 전까지 DART에서 찾는다."""
    rno, core = str(r.get("rcept_no", "")), disclosure_key(strip_correction(r.get("report_nm")))
    corp = str(r.get("corp_code", "") or "")

    def same(x):
        return (disclosure_key(strip_correction(x.get("report_nm"))) == core and str(x.get("rcept_no", "")) < rno
                and (not corp or str(x.get("corp_code", corp) or corp) == corp))

    cands = [x for x in rows if same(x)]
    if not cands and corp:
        try:
            end = datetime.strptime(str(r.get("rcept_dt") or rno[:8])[:8], "%Y%m%d").date()
        except ValueError:
            end = TODAY
        found, _, _ = collect_disclosures(corp, end - timedelta(days=365), end)
        cands = [x for x in found if same(x)]
    return max(cands, key=lambda x: str(x.get("rcept_no", ""))) if cands else None


def correction_diff(cur_rno, prev_rno, title):
    """정정 전·후 원문을 비교해 바뀐 부분만."""
    def make():
        session, store = get_session(), get_cache_store()
        before = fetch_document_text(session, store, prev_rno)[:35_000]
        after = fetch_document_text(session, store, cur_rno)[:35_000]
        prompt = (
            f"공시 제목: {title}\n아래는 같은 공시의 정정 전 원문과 정정 후 원문이다. 바뀐 내용만 정리해줘. "
            "정정 후 원문에 '정정사항'·'정정 전/정정 후' 표가 있으면 그것을 우선 근거로 쓴다.\n\n"
            "**정정 사유**\n- (원문에 적힌 사유. 없으면 '원문에 없음')\n\n"
            "**바뀐 내용**\n- 항목: 정정 전 → 정정 후 (숫자는 원문 그대로. 금액·기간·상대방·조건 변화를 우선)\n\n"
            "**기사 관점에서 눈여겨볼 변화**\n- (계약 축소·해지, 일정 연기 등 원문에서 확인되는 것만. 없으면 '특이사항 없음')\n\n"
            f"[정정 전 원문]\n{before}\n\n[정정 후 원문]\n{after}"
        )
        return ai_generate(prompt)
    return ai_cached(("fix", cur_rno, prev_rno), make)


def ask_disclosure(rno, title, question):
    """고른 공시에 질문 -> 원문 근거와 함께 답."""
    def make():
        text = fetch_document_text(get_session(), get_cache_store(), rno)[:MAX_DOC_CHARS]
        prompt = (
            f"공시 제목: {title}\n질문: {question}\n\n아래 원문만 보고 답해줘. 형식:\n"
            "**답**\n(2~4문장. 원문에서 확인되지 않으면 '원문에서 확인되지 않음'이라고만)\n\n"
            "**근거**\n- \"원문 문장을 그대로 인용\" (최대 3개)\n\n"
            f"[공시 원문]\n{text}"
        )
        return ai_generate(prompt)
    return ai_cached(("ask", rno, question.strip()), make)


NEWS_PICK_SYSTEM = (
    "너는 한국 경제신문 산업부 데스크다. 공시 제목 목록만 보고 기사 가치가 높은 공시를 고른다. "
    "제목에서 알 수 있는 사실만 근거로 삼고, 제목에 없는 숫자나 내용을 지어내지 않는다."
)


def pick_newsworthy(rows):
    """오늘의 공시 제목 목록 -> 기사거리 최대 10건 [(행, 이유)]. 제목만 보낸다."""
    items = rows[:400]
    lines = [f"{i}. [{CLS_NAMES.get(r.get('corp_cls'), '')}] {r.get('corp_name', '')} | {str(r.get('report_nm', '')).strip()}"
             for i, r in enumerate(items)]
    digest = hashlib.md5("\n".join(lines).encode("utf-8")).hexdigest()

    def make():
        prompt = (
            "아래 공시 중 산업부 기사로 쓸 만한 것을 최대 10개 골라줘. 대기업·업종 대표 기업, 큰 규모의 계약·투자·M&A·증자, "
            "최대주주 변경, 실적 급변, 사업 철수·진출 같은 공시를 우선한다. 같은 회사의 비슷한 공시는 하나만. "
            "정정 공시는 내용이 중요해 보일 때만.\n"
            '반드시 JSON 배열로만 답해: [{"번호": 3, "이유": "한 문장"}, ...] (중요한 순서)\n\n' + "\n".join(lines)
        )
        return ai_generate(prompt, system=NEWS_PICK_SYSTEM, json_mode=True)

    text, used = ai_cached(("pick", digest), make)
    data = parse_json(text)
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    out, seen = [], set()
    for d in data if isinstance(data, list) else []:
        try:
            i = int(d.get("번호"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= i < len(items) and i not in seen:
            seen.add(i)
            out.append((items[i], str(d.get("이유", "")).strip()))
    return out[:10], used


# ------------------------------------------------------------
# 주요사항보고서 숫자 (DART가 정리해 주는 칸)
# ------------------------------------------------------------
def major_report_spec(report_nm):
    """보고서명 -> MAJOR_REPORTS의 (키워드, API, 이름, 칸) 또는 None. 주요사항보고서만."""
    key = disclosure_key(report_nm)
    if "주요사항보고서" not in key:
        return None
    for spec in MAJOR_REPORTS:
        if disclosure_key(spec[0]) in key:
            return spec if spec[1] else None
    return None


def fmt_field(value, kind):
    """칸 값 -> 화면 글자. 비었거나 '-'면 None."""
    t = str(value if value is not None else "").strip()
    if t in ("", "-", "해당사항없음", "해당사항 없음"):
        return None
    if kind in ("won", "shares", "price"):
        v = to_int(t)
        if v is None:
            return t
        if kind == "won":
            return won_text(v) if abs(v) >= 1e8 else f"{v:,}원"
        return f"{v:,}주" if kind == "shares" else f"{v:,}원"
    if kind == "pct":
        return t if t.endswith("%") else f"{t}%"
    return t


def major_report_rows(row, spec):
    """DART 응답 한 줄 -> [(항목, 값)]. 유상증자·사채는 자금 용도 합계와 비중도 붙인다."""
    out = []
    for label, keys, kind in spec[3]:
        if isinstance(keys, tuple):
            a, b = (fmt_field(row.get(k), "text") for k in keys)
            val = f"{a} ~ {b}" if a and b else (a or b)
        else:
            val = fmt_field(row.get(keys), kind)
        if val:
            out.append((label, val))
    if any(k in row for k, _ in FUND_PURPOSES):
        uses = [(name, to_int(row.get(k)) or 0) for k, name in FUND_PURPOSES]
        total = sum(v for _, v in uses)
        if total > 0:
            out.insert(1 if spec[1] == "piicDecsn" else 3, ("조달 총액", won_text(total)))
            for name, v in uses:
                if v > 0:
                    out.append((f"자금 용도: {name}", f"{won_text(v) if v >= 1e8 else f'{v:,}원'} ({v / total * 100:.0f}%)"))
        if spec[1] == "piicDecsn":
            new, before = to_int(row.get("nstk_ostk_cnt")), to_int(row.get("bfic_tisstk_ostk"))
            if new and before:
                out.append(("증자 비율(보통주)", f"{new / before * 100:.1f}%"))
    return out


def fetch_major_report(session, store, api, corp_code, rcept_no, rcept_dt):
    """주요사항 API는 접수번호가 아니라 기간으로 조회한다 -> 접수일 앞뒤로 찾아 같은 접수번호를 고른다.
    정정 공시면 최초 접수일이 더 이르므로 1년 전부터 찾는다. -> (행, 정확히 일치했는지) 또는 (None, False)"""
    key = ("major", api, corp_code, rcept_no)
    cached = cache_lookup(store, key, CACHE_TTL)
    if cached is not _MISSING:
        return cached
    end = str(rcept_dt or rcept_no[:8])[:8]
    try:
        bgn = (datetime.strptime(end, "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d")
    except ValueError:
        return None, False
    jo = call_dart(session, f"https://opendart.fss.or.kr/api/{api}.json",
                   {"crtfc_key": API_KEY, "corp_code": corp_code, "bgn_de": bgn, "end_de": end})
    rows = (jo or {}).get("list") or []
    exact = [r for r in rows if str(r.get("rcept_no", "")).strip() == rcept_no]
    if exact:
        value = (exact[0], True)
    elif rows:  # 정정 공시: 같은 결정의 최신 내용(접수번호가 가장 큰 것)
        value = (max(rows, key=lambda r: str(r.get("rcept_no", ""))), False)
    else:
        value = (None, False)
    cache_save(store, key, value)
    return value


# ------------------------------------------------------------
# 배당 · 자사주 (사업·반기보고서)
# ------------------------------------------------------------
def div_key(text):
    return re.sub(r"[\s()（）]", "", str(text or ""))


def dividend_summary(jo):
    """배당에 관한 사항 -> {"now": {...}, "prev": {...}}. total(원), payout(%), dps(원), yield(%)"""
    rows = dart_rows(jo, ["se", "thstrm"])

    def find(word, common=False, prefer=""):
        cands = [r for r in rows if word in div_key(r.get("se"))]
        if common:
            c2 = [r for r in cands if "보통" in str(r.get("stock_knd", ""))]
            cands = c2 or cands
        if prefer:
            c3 = [r for r in cands if prefer in div_key(r.get("se"))]
            cands = c3 or cands
        return cands[0] if cands else None

    def num(r, col):
        if not r:
            return None
        t = str(r.get(col, "")).replace(",", "").strip()
        try:
            return float(t)
        except ValueError:
            return None

    total, payout = find("현금배당금총액"), find("현금배당성향", prefer="연결")
    dps, yld = find("주당현금배당금", common=True), find("현금배당수익률", common=True)
    out = {}
    for k, col in (("now", "thstrm"), ("prev", "frmtrm")):
        t = num(total, col)
        out[k] = {"total": t * 1e6 if t is not None else None,  # 공시 단위: 백만원
                  "payout": num(payout, col), "dps": num(dps, col), "yield": num(yld, col)}
    return out


def treasury_summary(jo):
    """자기주식 취득·처분 현황 -> 보통주 {start, acq, disp, burn, end} (주). 총계 줄이 있으면 그것을 쓴다."""
    rows = dart_rows(jo, ["stock_knd", "trmend_qy"])
    common = [r for r in rows if "보통" in str(r.get("stock_knd", ""))] or rows
    total = [r for r in common if "총계" in div_key(r.get("acqs_mth1")) or "합계" in div_key(r.get("acqs_mth1"))]
    pick_rows = total[:1] or [r for r in common
                              if not any(w in div_key(r.get(f"acqs_mth{i}")) for i in (1, 2, 3) for w in ("소계", "총계", "합계"))]
    if not pick_rows:
        return None
    keys = {"start": "bsis_qy", "acq": "change_qy_acqs", "disp": "change_qy_dsps", "burn": "change_qy_incnr", "end": "trmend_qy"}
    return {k: sum(to_int(r.get(c)) or 0 for r in pick_rows) for k, c in keys.items()}


def collect_div_years(corps, years, reprt_code):
    """여러 회사·여러 해의 배당·자사주 현황 (동시에 조회). -> {(회사, 연도): {"div", "tsk", "rcept_no"}}"""
    session, store = get_session(), get_cache_store()
    apis = {"div": "alotMatter", "tsk": "tesstkAcqsDspsSttus"}

    def one(job):
        name, code, year, kind = job
        key = ("divtsk", kind, code, year, reprt_code)
        cached = cache_lookup(store, key, CACHE_TTL)
        if cached is not _MISSING:
            return cached
        jo = call_dart(session, f"https://opendart.fss.or.kr/api/{apis[kind]}.json",
                       {"crtfc_key": API_KEY, "corp_code": code, "bsns_year": str(year), "reprt_code": reprt_code})
        cache_save(store, key, jo)
        return jo

    jobs = [(name, code, y, kind) for name, code in corps for y in years for kind in apis]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(one, jobs))
    got = {(j[0], j[2], j[3]): r for j, r in zip(jobs, results)}
    return {(name, y): {"div": dividend_summary(got.get((name, y, "div"))), "tsk": treasury_summary(got.get((name, y, "tsk"))),
                        "rcept_no": next((str(r.get("rcept_no")) for r in dart_rows(got.get((name, y, "div")), ["rcept_no"])), None)}
            for name, _ in corps for y in years}


def collect_div(corps, year, reprt_code):
    """여러 회사의 배당·자사주 현황 (동시에 조회)."""
    got = collect_div_years(corps, [year], reprt_code)
    return {name: got[(name, year)] for name, _ in corps}


DIV_MODES = ["기업 비교", "연도별 추이 (5년)"]
DIV_TREND_YEARS = 5


KRX_DIV_COLS = ["기말 종가", "배당수익률(기말)", "배당수익률(현재가)"]  # KRX 시세로 직접 계산한 칸


def div_trend_table(got, corps, years, px=None):
    """연도별 추이 표 (오래된 해가 위). px = dividend_prices 결과(없으면 수익률 칸 생략).
    -> (표, {회사: 주당 배당금 [(연도, 원)]})"""
    rows, dps = [], {}
    ends = px[0] if px else {}
    for name, _ in corps:
        for y in sorted(years):
            d = got.get((name, y))
            if not d:
                continue
            now, prev, t = d["div"]["now"], d["div"]["prev"], d["tsk"]
            if not any(v is not None for v in now.values()) and not t:
                continue
            dps.setdefault(name, []).append((y, now["dps"]))
            rows.append({
                "회사": name, "연도": str(y),
                "배당금 총액": won_text(now["total"]) if now["total"] else ("0원" if now["total"] == 0 else "-"),
                "총액 전년比": fmt_change(pct_change(now["total"], prev["total"])) if now["total"] and prev["total"] else "-",
                "배당성향": f"{now['payout']:.1f}%" if now["payout"] is not None else "-",
                "주당 배당금": f"{now['dps']:,.0f}원" if now["dps"] is not None else "-",
                "시가배당률": f"{now['yield']:.2f}%" if now["yield"] is not None else "-",
                **({"기말 종가": f"{ends[(name, y)]:,.0f}원" if (name, y) in ends else "-",
                    "배당수익률(기말)": yield_text(now["dps"], ends.get((name, y)))} if px else {}),
                "자사주 취득": (f"{t['acq']:+,}" if t["acq"] else "0") if t else "-",
                "자사주 소각": (f"{-t['burn']:+,}" if t["burn"] else "0") if t else "-",
                "기말 보유": f"{t['end']:,}" if t else "-",
                "원문": DART_VIEW_URL.format(d["rcept_no"]) if d.get("rcept_no") else None,
            })
    return pd.DataFrame(rows), dps


def dps_streak(series):
    """주당 배당금 흐름 -> (연속 햇수, '증가'|'동결'|'감소'). 비교할 해가 둘 미만이면 None."""
    vals = [v for _, v in sorted(series) if v is not None]
    if len(vals) < 2:
        return None
    sign = lambda a, b: (a > b) - (a < b)
    last = sign(vals[-1], vals[-2])
    n = 1
    for i in range(len(vals) - 2, 0, -1):
        if sign(vals[i], vals[i - 1]) != last:
            break
        n += 1
    return n, {1: "증가", 0: "동결", -1: "감소"}[last]


# --- 자사주 결정 공시 (취득·처분·소각·신탁계약) ---
TSK_PERIODS = {"최근 1년": 1, "최근 2년": 2, "최근 3년": 3}
TSK_WORDS = ("자기주식", "자사주", "주식소각")  # 보고서명(공백 제거)에 하나라도 있으면 자사주 공시
TSK_DECISION_APIS = ("tsstkAqDecsn", "tsstkDpDecsn", "tsstkAqTrctrCnsDecsn", "tsstkAqTrctrCcDecsn")  # 주요사항보고서 상세
TSK_KINDS = ["취득 결정", "소각 결정", "처분 결정", "신탁계약 체결", "신탁계약 해지", "취득 결과", "처분 결과", "기타"]


def tsk_title(name):
    """보고서명에서 [기재정정] 같은 표시와 공백·괄호를 지운 비교용 글자."""
    return re.sub(r"\[[^\]]*\]|[\s()（）]", "", str(name or ""))


def tsk_kind(name):
    t = tsk_title(name)
    if "소각" in t:
        return "소각 결정"
    if "신탁" in t:
        return "신탁계약 해지" if "해지" in t else "신탁계약 체결"
    if "취득결과" in t:
        return "취득 결과"
    if "처분결과" in t:
        return "처분 결과"
    if "처분" in t:
        return "처분 결정"
    if "취득" in t:
        return "취득 결정"
    return "기타"


def tsk_extras(row):
    """결정 공시 상세 응답 한 줄 -> {"shares", "amount", "period"}. 필드 이름을 느슨하게 찾아서 못 찾으면 비워 둔다."""
    shares = amount = start = end = None
    for k, v in row.items():
        k = str(k).lower()
        if k.endswith("pln_stk_ostk"):
            shares = to_int(v)
        elif k.endswith("prc_ostk") or k.endswith("ctr_prc"):
            amount = to_int(v)
        elif k.endswith("_bgd") and "hd" not in k:
            start = str(v).strip()
        elif k.endswith("_edd") and "hd" not in k:
            end = str(v).strip()
    period = " ~ ".join(x for x in (start, end) if x and x != "-")
    return {"shares": shares, "amount": amount, "period": period}


def collect_tsk_decisions(corps, years):
    """최근 N년 자사주 관련 결정 공시. -> (행 목록(최신순), 1,000건에 걸렸는지)"""
    session, store = get_session(), get_cache_store()
    end = TODAY
    start = end - timedelta(days=365 * years)
    bgn, fin = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    def detail(job):
        code, api = job
        key = ("tskdec", api, code, bgn, fin)
        cached = cache_lookup(store, key, LIST_TTL)
        if cached is not _MISSING:
            return cached
        try:
            jo = call_dart(session, f"https://opendart.fss.or.kr/api/{api}.json",
                           {"crtfc_key": API_KEY, "corp_code": code, "bgn_de": bgn, "end_de": fin})
        except DartApiError:
            return None  # 금액·기간은 덤이라 실패해도 공시 목록은 보여준다
        cache_save(store, key, jo)
        return jo

    jobs = [(code, api) for _, code in corps for api in TSK_DECISION_APIS]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        details = list(pool.map(detail, jobs))
    extras = {}
    for jo in details:
        for r in (jo or {}).get("list") or []:
            extras[str(r.get("rcept_no"))] = tsk_extras(r)

    rows, truncated = [], False
    for name, code in corps:
        picked = {}
        for ty in ("B", "I"):  # 주요사항보고, 거래소공시
            found, _, cut = collect_disclosures(code, start, end, ty)
            truncated = truncated or cut
            for r in found:
                title = str(r.get("report_nm", "")).strip()
                if not any(w in tsk_title(title) for w in TSK_WORDS):
                    continue
                kind = tsk_kind(title)
                key = (r.get("rcept_dt"), kind, "정정" in title)  # 같은 날 같은 결정이 두 곳에 올라오면 하나만
                old = picked.get(key)
                if old is None or ("주요사항보고서" in title and "주요사항보고서" not in old["보고서명"]):
                    ex = extras.get(str(r.get("rcept_no")), {})
                    dt = str(r.get("rcept_dt", ""))
                    picked[key] = {
                        "접수일": f"{dt[:4]}-{dt[4:6]}-{dt[6:]}", "회사": name, "구분": kind,
                        "주식 수": f"{ex['shares']:,}주" if ex.get("shares") else "-",
                        "금액": won_text(ex["amount"]) if ex.get("amount") else "-",
                        "예정 기간": ex.get("period") or "-", "보고서명": title,
                        "원문": DART_VIEW_URL.format(r.get("rcept_no")),
                    }
        rows.extend(picked.values())
    rows.sort(key=lambda x: (x["접수일"], x["회사"]), reverse=True)
    return rows, truncated


def div_tables(data, px=None, year=None):
    """-> (배당 표, 자사주 표). px = dividend_prices 결과(없으면 수익률 칸 생략)"""
    drows, trows = [], []
    ends, cur = (px[0], px[1]) if px else ({}, {})
    for corp, d in data.items():
        now, prev = d["div"]["now"], d["div"]["prev"]
        if any(v is not None for v in now.values()):
            drows.append({
                "회사": corp,
                "배당금 총액": won_text(now["total"]) if now["total"] else ("0원" if now["total"] == 0 else "-"),
                "총액 전년比": fmt_change(pct_change(now["total"], prev["total"])) if now["total"] and prev["total"] else "-",
                "배당성향": f"{now['payout']:.1f}%" if now["payout"] is not None else "-",
                "주당 배당금": f"{now['dps']:,.0f}원" if now["dps"] is not None else "-",
                "전년 주당": f"{prev['dps']:,.0f}원" if prev["dps"] is not None else "-",
                "시가배당률": f"{now['yield']:.2f}%" if now["yield"] is not None else "-",
                **({"기말 종가": f"{ends[(corp, year)]:,.0f}원" if (corp, year) in ends else "-",
                    "배당수익률(기말)": yield_text(now["dps"], ends.get((corp, year))),
                    "배당수익률(현재가)": yield_text(now["dps"], cur.get(corp))} if px else {}),
            })
        t = d["tsk"]
        if t:
            trows.append({
                "회사": corp, "기초 보유": f"{t['start']:,}", "취득": f"{t['acq']:+,}" if t["acq"] else "0",
                "처분": f"{-t['disp']:+,}" if t["disp"] else "0", "소각": f"{-t['burn']:+,}" if t["burn"] else "0",
                "기말 보유": f"{t['end']:,}",
            })
    return pd.DataFrame(drows), pd.DataFrame(trows)



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
    같은 기사가 여러 검색에 걸려도 한 번만 담는다. 같은 검색은 30분('오늘'은 10분) 동안 저장해 둔 결과를 쓴다.
    """
    session, store, today = get_session(), get_cache_store(), TODAY.isoformat()  # 화면 스레드에서 미리 준비

    def one(term):
        query = f"{term} {NEWS_KEYWORD}"
        key = ("news", query, days, today)
        hit = cache_lookup(store, key, NEWS_TTL_TODAY if days <= 1 else NEWS_TTL)
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
    """어제·오늘(한국 시간) 올라온 기사인지."""
    return item["dt"].astimezone(KST).date() >= now.astimezone(KST).date() - timedelta(days=1)


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
# 회사 이름 전체를 부르는 한글 이름 (단어 읽기로도 안 맞는 경우). 이 이름을 치면 바로 그 회사로 조회한다.
# 예: '에쓰오일'은 S-Oil의 글자 읽기(에스오아이엘)·단어 읽기(에스오일) 어느 쪽과도 다르다. 필요하면 여기에 추가.
NAME_READINGS = {
    "현대자동차": ["현대차", "현차"],  # 중요 기업: 현대차는 추천 없이 바로 현대자동차로 인식
    "S-Oil": ["에쓰오일", "에스오일", "에쓰-오일"],
    "NAVER": ["네이버"],
    "POSCO홀딩스": ["포스코홀딩스", "포스코"],
    "KT&G": ["케이티앤지", "KT앤G"],
    "F&F": ["에프앤에프"],
    "SOOP": ["숲", "아프리카TV"],
    "JYP Ent.": ["JYP엔터", "제이와이피", "JYP엔터테인먼트"],
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
    for name, t in reading_targets(query).items():  # 한글로 부르는 이름 (에쓰오일 -> S-Oil)
        mask = idx["corp_name"].str.casefold() == name.casefold()
        tier[mask] = tier[mask].clip(upper=t)
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


def reading_targets(query):
    """NAME_READINGS로 부르는 회사들. -> {회사 이름: 0(정확히 일치) / 1(앞부분 일치)}"""
    q = name_key(query)
    out = {}
    for name, readings in NAME_READINGS.items():
        for r in readings:
            k = name_key(r)
            if q and k == q:
                out[name] = 0
            elif q and len(q) >= 2 and k.startswith(q):
                out.setdefault(name, 1)
    return out


def canonical_corp(corp):
    """입력한 기업명을 DART에 등록된 정확한 표기로 바꾼다. 영문 대소문자는 구분하지 않는다.
    예: sk하이닉스 -> SK하이닉스, lg엔솔은 기업명이 아니므로 그대로. 같은 이름이 여럿이면 상장사 우선."""
    text = unicodedata.normalize("NFKC", str(corp)).strip()
    if not text or text.isdigit():
        return text
    idx = get_name_index()  # 상장사가 먼저 오도록 정렬된 목록
    hit = idx[idx["corp_name"].str.casefold() == text.casefold()]
    if not hit.empty:
        return hit.iloc[0]["corp_name"]
    for name, tier in reading_targets(text).items():  # 에쓰오일 -> S-Oil, 네이버 -> NAVER
        hit = idx[idx["corp_name"].str.casefold() == name.casefold()]
        if tier == 0 and not hit.empty:
            return hit.iloc[0]["corp_name"]
    return text


def resolve_corp_code(corp):
    """기업명/종목코드 -> 고유번호. 같은 이름이 여럿이면 상장사를 우선한다."""
    if not str(corp).isdigit():
        corp = canonical_corp(corp)  # 어디서 불러도 별칭(현대차 -> 현대자동차)이 먼저 적용되도록
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


# ------------------------------------------------------------
# 막대 그래프 · 재무 AI 분석 (재무 조회 탭)
# ------------------------------------------------------------
CHART_ITEMS = [("rev", "매출액"), ("op", "영업이익"), ("net", "순이익")]


CHART_COLORS = {"매출액": "#1c7ed6", "영업이익": "#f08c00", "순이익": "#2f9e44"}  # 파랑 · 주황 · 초록


CHART_COLORS = {"매출액": "#1c7ed6", "영업이익": "#f08c00", "순이익": "#2f9e44"}  # 파랑 · 주황 · 초록


def chart_frames(records, unit=DEFAULT_UNIT):
    """실적 표를 막대 그래프용 '길게 늘어선' 표로. -> 열 [기간, 항목, 금액], 기간은 표와 같은 순서. 값이 없는 칸은 뺀다."""
    rows = []
    for r in sorted_records(records):
        for key, name in CHART_ITEMS:
            v = num_amount(r.get(key), unit)
            if v is not None:
                rows.append({"기간": r["label"], "항목": name, "금액": v})
    return pd.DataFrame(rows, columns=["기간", "항목", "금액"])


def chart_spec(periods, unit):
    """한 그래프에 기간별로 매출액·영업이익·순이익 막대를 나란히 (Vega-Lite). 마이너스는 0선 아래로 내려간다.
    확대·이동 같은 조작(params)은 넣지 않아, 화면을 위아래로 밀 때 그래프가 걸리지 않는다."""
    names = list(CHART_COLORS)
    return {
        "height": 320,
        "layer": [
            {
                "mark": {"type": "bar", "tooltip": True},
                "encoding": {
                    "x": {"field": "기간", "type": "ordinal", "sort": list(periods),
                          "axis": {"title": None, "labelAngle": 0 if len(periods) <= 8 else -45, "labelOverlap": "parent"}},
                    "xOffset": {"field": "항목", "type": "nominal", "sort": names},
                    "y": {"field": "금액", "type": "quantitative", "stack": None, "axis": {"title": unit, "format": ",.2~f"}},
                    "color": {
                        "field": "항목", "type": "nominal",
                        "scale": {"domain": names, "range": list(CHART_COLORS.values())},
                        "legend": {"title": None, "orient": "top"},
                    },
                    "tooltip": [{"field": "기간"}, {"field": "항목"}, {"field": "금액", "format": ",.2~f", "title": f"금액({unit})"}],
                },
            },
            {"data": {"values": [{"zero": 0}]}, "mark": {"type": "rule", "color": "#94a3b8"},
             "encoding": {"y": {"field": "zero", "type": "quantitative"}}},
        ],
    }


def render_fin_charts(records, unit):
    """실적 표 아래 막대 그래프: 금액만 (전년비·전분기비는 뺌). 한 그래프에 세 항목을 나란히."""
    df = chart_frames(records, unit)
    if df.empty:
        st.caption("그릴 수 있는 금액이 없어요.")
        return
    periods = [r["label"] for r in sorted_records(records)]
    st.caption(f"단위: {unit}")
    st.vega_lite_chart(df, chart_spec(periods, unit), use_container_width=True)


def sign_streak(records, key):
    """맨 끝(가장 최근)부터 같은 부호가 몇 번 이어졌는지. -> (흑자/적자, 연속 횟수, 직전과 다른 부호가 처음 나온 기간 or None)."""
    seq = [(r["label"], r.get(key)) for r in sorted_records(records) if r.get(key) is not None]
    if not seq:
        return None
    sign = seq[-1][1] >= 0
    n = 0
    for label, v in reversed(seq):
        if (v >= 0) != sign:
            break
        n += 1
    prev_label = seq[-1 - n][0] if n < len(seq) else None
    return ("흑자" if sign else "적자", n, prev_label)


def fin_facts(records, period):
    """AI가 계산을 틀리지 않도록 미리 계산해 두는 사실 목록."""
    unit_word = "분기" if period == "분기별" else "년"
    recs = sorted_records(records)
    facts = []
    for key, name in (("op", "영업이익"), ("net", "순이익")):
        st_ = sign_streak(records, key)
        if st_:
            sign, n, prev = st_
            line = f"{name}: 가장 최근 기간({recs[-1]['label']}) 기준 {n}{unit_word} 연속 {sign}"
            if prev is not None:
                line += f" (직전 {prev}은(는) {'적자' if sign == '흑자' else '흑자'})"
            else:
                line += " (조회 기간 내내 같은 부호)"
            facts.append(line)
    for key, name in CHART_ITEMS:
        vals = [(r["label"], r.get(key)) for r in recs if r.get(key) is not None]
        if len(vals) >= 2:
            hi, lo = max(vals, key=lambda x: x[1]), min(vals, key=lambda x: x[1])
            facts.append(f"{name}: 최고 {hi[0]}({fmt_amount(hi[1], '억원')}억원), 최저 {lo[0]}({fmt_amount(lo[1], '억원')}억원), "
                         f"처음 {vals[0][0]} → 마지막 {vals[-1][0]}: {fmt_amount(vals[0][1], '억원')} → {fmt_amount(vals[-1][1], '억원')}억원")
    ratios = [(r["label"], debt_ratio(r.get("bs") or {})) for r in recs]
    nums = [(l, v) for l, v in ratios if isinstance(v, float)]
    if len(nums) >= 2:
        hi = max(nums, key=lambda x: x[1])
        facts.append(f"부채비율: 처음 {nums[0][0]} {nums[0][1]:.1f}% → 마지막 {nums[-1][0]} {nums[-1][1]:.1f}%, 최고 {hi[0]} {hi[1]:.1f}%")
    erosion = [l for l, v in ratios if v == "자본잠식"]
    if erosion:
        facts.append(f"자본잠식(자본총계 0 이하) 기간: {', '.join(erosion)}")
    yearly = period != "분기별"
    ms = [(r["label"], bs_metrics(r, yearly)) for r in recs]
    nd = [(l, m["net_debt"]) for l, m in ms if m["net_debt"] is not None]
    if len(nd) >= 2:
        facts.append(f"순차입금(마이너스는 순현금): {nd[0][0]} {fmt_amount(nd[0][1], '억원')}억원 → {nd[-1][0]} {fmt_amount(nd[-1][1], '억원')}억원")
    if yearly:
        neg_fcf = [l for l, m in ms if m["fcf"] is not None and m["fcf"] < 0]
        if neg_fcf:
            facts.append(f"잉여현금흐름(영업활동현금흐름−CAPEX)이 마이너스인 해: {', '.join(neg_fcf)}")
        low_cov = [f"{l}({m['int_cov']:.1f}배)" for l, m in ms if m["int_cov"] is not None and m["int_cov"] < 1]
        if low_cov:
            facts.append(f"이자보상배율 1배 미만(영업이익이 이자비용보다 적음): {', '.join(low_cov)}")
    low_cur = [f"{l}({m['cur_ratio']:.0f}%)" for l, m in ms if m["cur_ratio"] is not None and m["cur_ratio"] < 100]
    if low_cur:
        facts.append(f"유동비율 100% 미만: {', '.join(low_cur)}")
    for key, name in (("cash", "현금성자산"), ("inv", "재고자산")):
        vals = [(r["label"], (r.get("bs") or {}).get(key)) for r in recs]
        vals = [(l, v) for l, v in vals if v is not None]
        if len(vals) >= 2:
            facts.append(f"{name}: {vals[0][0]} {fmt_amount(vals[0][1], '억원')}억원 → {vals[-1][0]} {fmt_amount(vals[-1][1], '억원')}억원")
    return facts


def fin_ai_text(corp, records, period):
    """AI에 보낼 자료: 기간별 표(억원) + 미리 계산한 사실."""
    head = ["기간", "매출액", "영업이익", "영업이익률", "순이익", "자산총계", "부채총계", "자본총계", "부채비율", "현금성자산", "재고자산", "순차입금", "순차입금비율", "유동비율"]
    yearly = period != "분기별"
    if yearly:
        head += ["CAPEX", "영업활동현금흐름", "잉여현금흐름", "EBITDA", "이자보상배율", "ROE"]
    lines = [" | ".join(head)]
    for r in sorted_records(records):
        bs = r.get("bs") or {}
        ratio = debt_ratio(bs)
        margin = f"{r['op'] / r['rev'] * 100:.1f}%" if r.get("op") is not None and r.get("rev") else "-"
        m = bs_metrics(r, yearly)
        row = [r["label"], fmt_amount(r.get("rev"), "억원"), fmt_amount(r.get("op"), "억원"), margin, fmt_amount(r.get("net"), "억원"),
               fmt_amount(bs.get("assets"), "억원"), fmt_amount(bs.get("liab"), "억원"), fmt_amount(bs.get("equity"), "억원"),
               f"{ratio:.1f}%" if isinstance(ratio, float) else (ratio or "-"),
               fmt_amount(bs.get("cash"), "억원"), fmt_amount(bs.get("inv"), "억원"),
               fmt_amount(m["net_debt"], "억원"), fmt_pct(m["nd_ratio"]), fmt_pct(m["cur_ratio"])]
        if yearly:
            row += [fmt_amount(bs.get("capex"), "억원"), fmt_amount(m["opcf"], "억원"), fmt_amount(m["fcf"], "억원"),
                    fmt_amount(m["ebitda"], "억원"), fmt_times(m["int_cov"]), fmt_pct(m["roe"])]
        lines.append(" | ".join(row))
    notes = ["연결재무제표 기준. 금액 단위는 억원(1조원 = 10,000억원). '-'는 자료 없음(은행·보험·증권사는 일부 지표가 해당 없음).",
             "순차입금 = 차입금 − 현금성자산 (마이너스면 순현금)."
             + (" 잉여현금흐름 = 영업활동현금흐름 − CAPEX. EBITDA = 영업이익 + 감가상각비 + 무형자산상각비. 이자보상배율 = 영업이익 ÷ 이자비용." if yearly else "")]
    if not yearly:
        notes.append("분기별 자료: 4분기 값은 사업보고서 연간 값에서 3분기 누적을 뺀 계산값. 재무상태는 각 분기 말 잔액. 계절성이 있으니 직전 분기보다 전년 같은 분기와 비교할 것.")
    return (f"기업: {corp}\n조회 단위: {period}\n" + "\n".join(notes) + "\n\n[기간별 자료]\n" + "\n".join(lines)
            + "\n\n[미리 계산한 사실 — 이 숫자를 그대로 쓸 것]\n" + "\n".join(f"- {f}" for f in fin_facts(records, period)))


AI_FIN_SYSTEM = (
    "너는 중앙일보 산업부 기자를 돕는 기업 실적 분석 보조자다. 사용자가 준 [기간별 자료]와 [미리 계산한 사실]만 근거로 쓴다. "
    "자료에 없는 숫자·사실·원인·전망·시장 반응을 절대 만들지 않는다. 숫자는 자료 표기 그대로(단위 포함) 옮기고 새로 계산하지 않는다. "
    "실적이 왜 변했는지 원인은 자료에 없으므로 단정하지 말고, 추정이면 '~했을 가능성', '확인 필요'처럼 표시한다. "
    "산업부 기자의 시각으로 쓴다: 흑자·적자 흐름과 전환 시점, 이익률(수익성) 변화, 부채비율·순차입금·유동비율·이자보상배율 등 재무 건전성, 영업현금흐름과 잉여현금흐름(이익은 나는데 현금이 도는지), 투자(CAPEX) 부담을 짚고 "
    "무엇이 기사 포인트인지 가려낸다. 한국어, 짧고 명확한 기사체로 쓴다."
)


def fin_ai_prompt(corp, records, period):
    return (
        fin_ai_text(corp, records, period)
        + "\n\n위 자료를 종합해 아래 형식으로 써라. 자료가 부족한 항목은 건너뛴다.\n"
        "**한 줄 요약**: 이 기업 실적·재무의 가장 큰 특징 한 문장\n"
        "**핵심 포인트**: 3~5개 항목(- 로 시작). 흑자/적자 지속·전환 시점, 매출·이익 추세, 이익률, 부채비율·현금·재고·CAPEX 변화 중 의미 있는 것\n"
        "**기사로 쓴다면 확인할 것**: 2~3개 항목(- 로 시작). 자료만으로 알 수 없는 원인·배경 중 취재로 확인할 질문"
    )


def fin_ai_analysis(corp, records, period):
    """실적·재무상태 AI 분석 (같은 자료는 일주일 저장). -> (글, 쓴 모델)"""
    prompt = fin_ai_prompt(corp, records, period)
    sig = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    return ai_cached(("fin", sig), lambda: ai_generate(prompt, system=AI_FIN_SYSTEM)), sig


def render_fin_ai(result):
    """재무 조회 결과 아래 'AI 분석' 상자."""
    st.markdown("##### AI 분석")
    if not AI_PROVIDER:
        st.caption("AI 분석을 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY(Claude)를 추가해주세요. (무료 Gemini를 쓰려면 GEMINI_API_KEY를 추가해주세요.)")
        return
    prompt_sig = hashlib.sha1(fin_ai_prompt(result["corp"], result["records"], result["period"]).encode("utf-8")).hexdigest()
    go = st.button("조회한 실적·재무상태 AI 분석", key="fin_ai_go", type="primary")
    if go:
        with st.spinner("실적과 재무상태를 종합하는 중입니다... (10~30초)"):
            try:
                (text, used), _ = fin_ai_analysis(result["corp"], result["records"], result["period"])
            except AiError as e:
                st.error(str(e))
            else:
                st.session_state["fin_ai"] = {"sig": prompt_sig, "text": text, "model": used}
    saved = st.session_state.get("fin_ai")
    if saved and saved["sig"] == prompt_sig:
        st.markdown(saved["text"])
        st.caption(f"AI 분석({saved['model']}) · 위 표의 숫자만 근거로 한 해석이에요. 원인·배경은 취재로 확인하세요")
    else:
        st.caption("누르면 위 표(실적·재무상태)를 종합해 흑자·적자 흐름, 수익성, 재무 건전성의 핵심 포인트를 정리해 줘요.")


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
            st.session_state.pop("fin_ai", None)

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

    st.markdown(f"##### {result['corp']} 실적")  # 아래 '재무상태'와 같은 크기의 소제목
    unit = st.radio("단위", list(UNITS), horizontal=True, key="fin_unit", label_visibility="collapsed")  # 기본 억원, 조원으로 바꿀 수 있음
    table = build_table(result["records"], result["period"], unit=unit)
    basis = "연결 기준" if FS_DIV == "CFS" else "별도 기준"  # 지금은 항상 연결재무제표(CFS)로 조회
    st.caption(f"단위: {unit} · {basis}")
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
    if st.toggle("그래프", key="fin_chart_on"):
        render_fin_charts(result["records"], unit)

    bs_table = build_bs_table(result["records"], result["period"], unit=unit)
    if not bs_table.empty:
        st.markdown("##### 재무상태")
        yearly = result["period"] != "분기별"
        capex_note = "  \n**CAPEX** = 유형자산 취득액(연간, 현금흐름표)  \n**잉여현금흐름** = 영업활동현금흐름 − CAPEX" if yearly else ""
        st.caption(
            f"단위: {unit} · 기말 기준",
            help="**부채비율** = 부채총계 ÷ 자본총계  \n**순차입금** = 차입금 − 현금성자산 (마이너스는 현금이 빚보다 많은 순현금)" + capex_note,
        )
        st.dataframe(
            bs_table, hide_index=True,
            column_config={"기간": st.column_config.TextColumn("기간", width=95)},
            height=(len(bs_table) + 1) * 35 + 3,
        )
        if st.toggle("더 보기", key="fin_bs_more"):
            more = build_bs_more_table(result["records"], result["period"], unit=unit)
            st.dataframe(
                more, hide_index=True,
                column_config={"기간": st.column_config.TextColumn("기간", width=95)},
                height=(len(more) + 1) * 35 + 3,
            )
            only_year = "" if yearly else " 현금흐름·이자·EBITDA·ROE는 연도별 조회에서만 나와요."
            st.caption(
                "순차입금비율 = 순차입금 ÷ 자본총계 · 유동비율 = 유동자산 ÷ 유동부채 · 이자보상배율 = 영업이익 ÷ 이자비용(없으면 현금흐름표의 이자 지급액) · "
                "EBITDA = 영업이익 + 감가상각비 + 무형자산상각비 · ROE = 순이익 ÷ 기말 자본총계. "
                "차입금은 재무상태표의 단기차입금·유동성장기부채·사채·장기차입금 등을 더한 값이에요. "
                "은행·보험·증권사는 계정 구조가 달라 '-'로 나올 수 있어요." + only_year
            )

    render_fin_ai(result)

    export = build_table(result["records"], result["period"], for_export=True, unit=unit)
    bs_export = build_bs_table(result["records"], result["period"], for_export=True, unit=unit)
    if not bs_export.empty:  # 다운로드 파일에는 재무상태 열도 함께
        export = export.merge(bs_export, on="기간", how="left")
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


def fill_industry():
    """업종을 고르면 그 업종 기업들을 입력칸에 채운다."""
    group = st.session_state.get("ind_group")
    if group in INDUSTRY_GROUPS:
        st.session_state["ind_corps"] = ", ".join(INDUSTRY_GROUPS[group])


def render_fin_hub():
    """탭: 재무 조회 - 기업 한 곳의 기간별 추이 / 여러 기업 합산을 고른다."""
    mode = st.radio("조회 방식", ["기업 한 곳 (기간별 추이)", "여러 기업 (업종 합산)"], horizontal=True, key="fin_mode")
    if mode.startswith("기업 한 곳"):
        render_financials()
    else:
        render_industry()


def render_industry():
    """탭: 업종 합산 - 여러 회사의 같은 기간 실적을 모으고 합계를 낸다."""
    groups = list(INDUSTRY_GROUPS) + ["직접 입력"]
    st.session_state.setdefault("ind_corps", ", ".join(INDUSTRY_GROUPS[groups[0]]))
    st.selectbox("업종", groups, key="ind_group", on_change=fill_industry)  # 폼 밖: 고르면 아래 기업 칸이 바뀜
    ly, lp = latest_period()
    years = list(range(TODAY.year, 2014, -1))
    with st.form("ind_search"):
        corp_text = st.text_input(
            "기업 (쉼표로 구분)", key="ind_corps", placeholder="예: 삼성전자, SK하이닉스",
            help=f"업종을 고르면 대표 기업이 채워져요. 빼거나 더해서 최대 {MAX_INDUSTRY_CORPS}곳까지 합산할 수 있어요.",
        )
        c1, c2 = st.columns(2)
        year = c1.selectbox("연도", years, index=years.index(ly), key="ind_year")
        period = c2.radio("기간", INDUSTRY_PERIODS, index=INDUSTRY_PERIODS.index(lp), horizontal=True, key="ind_period")
        submitted = st.form_submit_button("합산 조회")

    if submitted:
        names = []
        for part in re.split(NEWS_SPLIT, corp_text):
            part = canonical_corp(part)
            if part and name_key(part) not in {name_key(n) for n in names}:
                names.append(part)
        if not names:
            st.error("기업명을 입력해주세요.")
        elif len(names) > MAX_INDUSTRY_CORPS:
            st.error(f"기업은 한 번에 {MAX_INDUSTRY_CORPS}곳까지 넣을 수 있어요. (지금 {len(names)}곳)")
        else:
            st.session_state.pop("industry", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움
            corps, missing = [], []
            with st.spinner("기업 정보를 확인하는 중입니다..."):
                for n in names:
                    code = resolve_corp_code(n)
                    if code:
                        corps.append((n, code))
                    else:
                        missing.append(n)
            for n in missing:
                hint = [label for label, _ in suggest_corps(n, limit=3)]
                st.warning(f'"{n}" 기업을 찾지 못해 빼고 합산했어요.' + (f" 혹시: {', '.join(hint)}" if hint else ""))
            if corps:
                with st.spinner("실적을 불러오는 중입니다..."):
                    try:
                        data = collect_industry(corps, int(year), period)
                    except DartApiError as e:
                        st.error(str(e))
                    else:
                        group = st.session_state.get("ind_group")
                        # 업종 목록을 그대로 썼으면 문장에 업종 이름을, 고쳐 썼으면 '조회한'을 쓴다
                        label = group if group in INDUSTRY_GROUPS and names == INDUSTRY_GROUPS[group] else "조회한"
                        st.session_state["industry"] = {"data": data, "year": int(year), "period": period, "label": label, "corps": corps}

    res = st.session_state.get("industry")
    if not res:
        return
    unit = st.radio("단위", list(UNITS), horizontal=True, key="ind_unit", label_visibility="collapsed")
    table, total, used = industry_result(res["data"], unit)
    when = f"{res['year']}년 연간" if res["period"] == "연간" else f"{res['year']}년 {res['period']}"
    if table.empty:
        st.warning(f"{when} 실적이 공시된 회사가 없어요. 아직 보고서가 나오기 전일 수 있어요. 기간을 바꿔보세요.")
        render_industry_caps(res)  # 시가총액은 실적과 상관없이 볼 수 있다
        return
    empty = [c for c, d in res["data"].items() if all(d["now"][k] is None for k in ("rev", "op", "net"))]
    st.caption(f"{when} · 연결 기준 · 단위: {unit} · 전년比는 두 해 모두 공시된 회사끼리 비교")
    if empty:
        st.caption(f"실적이 없어 합계에서 빠진 회사: {', '.join(empty)}")
    st.dataframe(
        table.style.map(amount_style, subset=["매출액", "영업이익", "순이익"]).apply(total_row_style, axis=1),
        hide_index=True,
        column_config={
            "회사": st.column_config.TextColumn("회사", width=120),
            "매출액": st.column_config.TextColumn("매출액", width=108),
            "영업이익": st.column_config.TextColumn("영업이익", width=95),
            "순이익": st.column_config.TextColumn("순이익", width=95),
        },
        height=(len(table) + 1) * 35 + 3,
    )
    sentence = industry_sentence(res["label"], used, res["year"], res["period"], total)
    if sentence:
        st.caption("기사 첫 문장 초안 — 오른쪽 위 아이콘을 누르면 복사돼요.")
        st.code(sentence, language=None, wrap_lines=True)
    st.download_button(
        "CSV 다운로드",
        table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"업종합산_{safe_filename(res['label'])}_{res['year']}_{res['period']}.csv",
        mime="text/csv",
    )
    render_industry_caps(res)


def render_industry_caps(res):
    """업종 합산 아래: 같은 회사들의 시가총액 (KRX)."""
    if not KRX_KEY:
        krx_hint()
        return
    try:
        items, day = industry_caps(res["corps"])
    except KrxError as e:
        krx_error_box(e)
        return
    st.markdown("##### 시가총액")
    listed = [i for i in items if i["cap"]]
    if not listed:
        st.info("KRX에서 시가총액을 찾은 회사가 없어요.")
        return
    total = sum(i["cap"] for i in listed)
    rows = [{"회사": i["회사"], "시장": i["market"] or "-", "종가": f"{i['close']:,.0f}원" if i["close"] else "-",
             "전일比": pct_text(i["rate"], 2), "시가총액": won_text(i["cap"]) if i["cap"] else "-",
             "비중": f"{i['cap'] / total * 100:.1f}%" if i["cap"] else "-"}
            for i in sorted(items, key=lambda i: -(i["cap"] or 0))]
    rows.append({"회사": f"합계 ({len(listed)}개사)", "시장": "", "종가": "", "전일比": "", "시가총액": won_text(total), "비중": "100%"})
    table = pd.DataFrame(rows)
    st.caption(f"KRX {day:%Y.%m.%d} 종가 기준 · 보통주+우선주 · 비상장사는 빠져요" if day else "KRX 시세 기준")
    st.dataframe(table.style.map(price_style, subset=["전일比"]).apply(total_row_style, axis=1), hide_index=True,
                 height=(len(table) + 1) * 35 + 3)
    sentence = (f"{res['label']} {len(listed)}개사의 시가총액 합계는 {won_text(total)}으로, 가장 큰 {listed_top(items)[0]}이(가) "
                f"{listed_top(items)[1]:.0f}%를 차지한다.")
    st.caption("기사 문장 초안")
    st.code(sentence, language=None, wrap_lines=True)


def listed_top(items):
    """-> (가장 큰 회사 이름, 비중 %)"""
    listed = [i for i in items if i["cap"]]
    total = sum(i["cap"] for i in listed)
    top = max(listed, key=lambda i: i["cap"])
    return top["회사"], top["cap"] / total * 100


def render_major_numbers(r):
    """고른 공시가 주요사항보고서(유상증자·CB·분할·양수도 등)면 DART 정리 칸을 표로."""
    spec = major_report_spec(r.get("report_nm", ""))
    if not spec:
        return
    code = str(r.get("corp_code", "")).strip()
    rno = str(r["rcept_no"]).strip()
    if not code:
        return
    try:
        row, exact = fetch_major_report(get_session(), get_cache_store(), spec[1], code, rno, r.get("rcept_dt"))
    except DartApiError as e:
        st.error(str(e))
        return
    st.markdown(f"**핵심 숫자 · {spec[2]}**")
    items = major_report_rows(row, spec) if row else []
    if not items:
        st.caption("DART 정리 데이터가 아직 없어요. 아래 AI 요약이나 원문으로 확인해주세요.")
        return
    st.dataframe(pd.DataFrame(items, columns=["항목", "내용"]), hide_index=True,
                 column_config={"내용": st.column_config.TextColumn("내용", width="large")},
                 height=(len(items) + 1) * 35 + 3)
    note = "DART 정리 데이터 (원문 그대로의 숫자)"
    if not exact:
        note += " · 정정 공시라 같은 결정의 최신 내용을 보여줘요"
    st.caption(note)


def render_ai_summary(rows, prefix, all_rows=None):
    """표 아래 '공시 자세히 보기' 상자: 공시 하나를 골라 핵심 숫자(주요사항보고서)·AI 요약·질문·정정 비교."""
    with st.expander("공시 자세히 보기 — 핵심 숫자 · AI 요약 · 질문"):
        items = [r for r in rows if str(r.get("rcept_no", "")).strip()][:500]
        if not items:
            st.caption("고를 공시가 없어요.")
            return

        def label(r):
            d = str(r.get("rcept_dt", ""))
            d = f"{d[4:6]}.{d[6:8]}" if len(d) == 8 else d
            corp = f"{r.get('corp_name', '')} · " if r.get("corp_name") else ""
            mark = "[숫자표] " if major_report_spec(r.get("report_nm", "")) else ""
            new = "[NEW] " if str(r.get("rcept_dt", "")) >= NEW_SINCE else ""
            return f"{new}{mark}{d} {corp}{str(r.get('report_nm', '')).strip()}"

        idx = st.selectbox(
            "볼 공시", range(len(items)), index=None, format_func=lambda i: label(items[i]),
            placeholder="공시를 고르세요 ([숫자표]는 핵심 숫자가 바로 나와요)", key=f"{prefix}_ai_pick",
        )
        if idx is None:
            return
        r = items[idx]
        rno = str(r["rcept_no"]).strip()
        title = label(r).replace("[NEW] ", "").replace("[숫자표] ", "")
        render_major_numbers(r)
        if not AI_PROVIDER:
            st.caption("AI 요약을 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY(Claude)를 추가해주세요. (무료 Gemini를 쓰려면 GEMINI_API_KEY를 추가해주세요.)")
            st.markdown(f"[DART 원문 보기]({DART_VIEW_URL.format(rno)})")
            return

        fix = is_correction(r.get("report_nm"))
        cols = st.columns(2 if fix else 1)
        go_sum = cols[0].button("AI 요약", key=f"{prefix}_ai_go", type="primary")
        go_fix = cols[1].button("정정 전후 비교", key=f"{prefix}_fix_go") if fix else False

        saved = st.session_state.get(f"{prefix}_ai")
        if go_sum:
            with st.spinner("원문을 읽고 요약하는 중입니다... (긴 공시는 10~30초 걸려요)"):
                try:
                    summary, truncated, used = ai_summary(rno, title)
                except (AiError, DartApiError) as e:
                    st.error(str(e))
                else:
                    saved = {"rcept_no": rno, "summary": summary, "truncated": truncated, "model": used}
                    st.session_state[f"{prefix}_ai"] = saved
        if saved and saved["rcept_no"] == rno:
            st.markdown(saved["summary"])
            notes = [f"AI 요약({saved.get('model') or AI_PROVIDER}) · 숫자는 반드시 원문으로 확인하세요"]
            if saved["truncated"]:
                notes.append(f"원문이 길어 앞부분 {MAX_DOC_CHARS:,}자만 요약했어요")
            st.caption(" · ".join(notes))

        if fix:
            fsaved = st.session_state.get(f"{prefix}_fix")
            if go_fix:
                with st.spinner("정정 전 공시를 찾아 비교하는 중입니다..."):
                    try:
                        prev = find_previous_version(r, all_rows or rows)
                        if not prev:
                            raise AiError("정정 전 공시를 찾지 못했어요. (1년 안에 같은 이름의 공시가 없어요)")
                        text, used = correction_diff(rno, str(prev["rcept_no"]), title)
                    except (AiError, DartApiError) as e:
                        st.error(str(e))
                    else:
                        fsaved = {"rcept_no": rno, "text": text, "model": used, "prev": str(prev["rcept_no"]),
                                  "prev_title": str(prev.get("report_nm", "")).strip()}
                        st.session_state[f"{prefix}_fix"] = fsaved
            if fsaved and fsaved["rcept_no"] == rno:
                st.markdown("**정정 전후 비교**")
                st.markdown(fsaved["text"])
                st.caption(f"AI 비교({fsaved['model']}) · 정정 전: {fsaved['prev_title']} · 숫자는 반드시 원문으로 확인하세요")
                st.markdown(f"[정정 전 원문]({DART_VIEW_URL.format(fsaved['prev'])})")

        q = st.text_input("이 공시에 질문하기", key=f"{prefix}_q", placeholder="예: 계약 상대방이 누구야? 자금은 어디에 써?")
        qsaved = st.session_state.get(f"{prefix}_qa")
        if st.button("질문", key=f"{prefix}_q_go"):
            if not q.strip():
                st.error("질문을 입력해주세요.")
            else:
                with st.spinner("원문에서 찾는 중입니다..."):
                    try:
                        text, used = ask_disclosure(rno, title, q)
                    except (AiError, DartApiError) as e:
                        st.error(str(e))
                    else:
                        qsaved = {"rcept_no": rno, "q": q.strip(), "text": text, "model": used}
                        st.session_state[f"{prefix}_qa"] = qsaved
        if qsaved and qsaved["rcept_no"] == rno and qsaved["q"] == q.strip():
            st.markdown(qsaved["text"])
            st.caption(f"AI 답변({qsaved['model']}) · 근거 문장을 원문에서 확인하세요")
        st.markdown(f"[DART 원문 보기]({DART_VIEW_URL.format(rno)})")


def render_news_picks(rows, res_id):
    """오늘의 공시: 제목만 보고 AI가 기사거리 10건을 고른다 (참고용)."""
    if not AI_PROVIDER:
        return
    with st.expander("AI 기사거리 추리기 — 제목만 보고 고른 참고용 10건"):
        st.caption(f"지금 표에 보이는 공시 {min(len(rows), 400):,}건의 제목만 AI에 보내요. 내용은 원문이나 AI 요약으로 꼭 확인하세요.")
        saved = st.session_state.get("feed_picks")
        if st.button("기사거리 추리기", key="feed_pick_go", type="primary"):
            with st.spinner("제목을 훑어보는 중입니다..."):
                try:
                    picks, used = pick_newsworthy(rows)
                except AiError as e:
                    st.error(str(e))
                else:
                    saved = {"id": res_id, "picks": picks, "model": used, "n": len(rows)}
                    st.session_state["feed_picks"] = saved
        if saved and saved["id"] == res_id:
            if not saved["picks"]:
                st.info("AI가 고른 공시가 없어요. 다시 시도해주세요.")
                return
            df = pd.DataFrame([{
                "회사": r.get("corp_name", ""), "보고서명": str(r.get("report_nm", "")).strip(), "이유": why,
                "원문": DART_VIEW_URL.format(r["rcept_no"]) if r.get("rcept_no") else None,
            } for r, why in saved["picks"]])
            st.dataframe(df, hide_index=True, column_config={
                "이유": st.column_config.TextColumn("이유", width="large"), "원문": LINK_COLUMN})
            st.caption(f"AI 추천({saved['model']}) · 제목만 보고 고른 것이라 참고용이에요")


def render_disclosures():
    """탭 2: 공시 목록 (DART에서 기업명을 눌렀을 때 나오는 목록)"""
    first_day = date(1999, 1, 1)

    with st.form("list_search"):
        corp_text = st.text_input("기업명 또는 종목코드", key="list_corp", placeholder="예: 삼성전자 또는 005930")
        c0, c1, c2 = st.columns(3)
        kind_label = c0.selectbox("공시 유형", list(DISCLOSURE_TYPES), key="list_kind")
        start = c1.date_input(
            "시작일", value=TODAY - timedelta(days=92), min_value=first_day, max_value=TODAY, key="list_start"
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

    table = disclosure_table(shown)
    saved = render_reaction(shown, "list")
    if saved:
        render_reaction_table(table, shown, saved)
        render_ai_summary(shown, "list", all_rows=res["rows"])
        return
    table.insert(0, "new", ["NEW" if str(r.get("rcept_dt", "")) >= NEW_SINCE else "" for r in shown])  # 어제·오늘 올라온 공시
    st.dataframe(
        table.style.map(lambda v: f"color: {NEGATIVE_COLOR}; font-weight: 800" if v == "NEW" else "", subset=["new"]),
        hide_index=True,
        column_config={"new": st.column_config.TextColumn(" ", width=50),  # 머리글은 비워 둔다
                       "보고서명": st.column_config.TextColumn("보고서명", width="large"), "원문": LINK_COLUMN},
    )
    render_ai_summary(shown, "list", all_rows=res["rows"])


def last_weekday(day):
    """주말이면 직전 금요일로 (공시는 평일에만 나온다. 공휴일은 따로 계산하지 않는다)."""
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def default_pay_year():
    """사업보고서는 3월 말에 나오므로 4월부터 작년, 그 전엔 재작년을 기본으로."""
    return TODAY.year - 1 if TODAY.month >= 4 else TODAY.year - 2


def render_pay():
    """탭: 임직원 (임원 보수 + 직원 현황 + 임원 현황, 여러 회사 비교). 보수는 사업·반기보고서에만 실린다."""
    years = list(range(TODAY.year, 2014, -1))
    with st.form("pay_search"):
        corp_text = st.text_input(
            "기업명 (여러 곳은 쉼표로)", key="pay_corp", placeholder="예: 삼성전자, SK하이닉스, LG전자",
            help=f"쉼표(,)나 슬래시(/)로 나눠 최대 {MAX_PAY_CORPS}곳까지 한 번에 비교해요.",
        )
        year = st.selectbox("사업연도", years, index=years.index(default_pay_year()), key="pay_year")
        report = st.selectbox("보고서", list(PAY_REPORTS_ALL), key="pay_report")
        submitted = st.form_submit_button("임직원 조회") or st.session_state.pop("pay_run", False)

    if submitted:
        names = []
        for part in re.split(NEWS_SPLIT, corp_text):
            part = canonical_corp(part)
            if part and name_key(part) not in {name_key(n) for n in names}:
                names.append(part)
        if not names:
            st.error("기업명을 입력해주세요.")
        elif len(names) > MAX_PAY_CORPS:
            st.error(f"기업은 한 번에 {MAX_PAY_CORPS}곳까지 넣을 수 있어요. (지금 {len(names)}곳)")
        else:
            st.session_state.pop("pay", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움
            corps, missing = [], []
            if len(names) == 1:  # 한 곳이면 다른 탭처럼 비슷한 기업 추천 버튼
                code = lookup_corp_code(names[0], "pay")
                if code:
                    corps.append((names[0], code))
            else:
                with st.spinner("기업 정보를 확인하는 중입니다..."):
                    for n in names:
                        code = resolve_corp_code(n)
                        if code:
                            corps.append((n, code))
                        else:
                            missing.append(n)
                for n in missing:
                    hint = [label for label, _ in suggest_corps(n, limit=3)]
                    st.warning(f'"{n}" 기업을 찾지 못해 빼고 조회했어요.' + (f" 혹시: {', '.join(hint)}" if hint else ""))
            if corps:
                with st.spinner("임직원 공시를 불러오는 중입니다..."):
                    try:
                        data = collect_pay(corps, int(year), PAY_REPORTS_ALL[report])
                        exctv = rt_exctv(corps, int(year), PAY_REPORTS_ALL[report])
                    except DartApiError as e:
                        st.error(str(e))
                    else:
                        st.session_state["pay"] = {"data": data, "year": int(year), "report": report, "exctv": exctv, "corps": corps}
                        st.session_state.pop("pay_exctv_diff", None)

    res = st.session_state.get("pay")
    if not res:
        return
    people, summary = pay_tables(res["data"])
    label = f"{res['year']}년 {res['report'].split(' ')[0]}"
    corps_txt = ", ".join(res["data"])
    quarter = PAY_REPORTS_ALL[res["report"]] in QUARTER_CODES
    NO_PAY = "분기보고서에 기재되지 않는 항목이에요. (임원 보수는 사업·반기보고서에만 실려요)"

    st.markdown("##### 회사별 요약")
    if quarter:
        st.caption(NO_PAY)
    else:
        st.caption(f"{corps_txt} · {label} · 단위: 억원 · 임원 = 이사·감사 전체(사외이사 포함)")
        st.dataframe(
            summary.style.map(change_style, subset=["전년比"]), hide_index=True,
            height=(len(summary) + 1) * 35 + 3,
        )

    emp = employee_table(res["data"])
    st.markdown("##### 직원 현황")
    if emp.empty:
        st.info("분기보고서에 기재되지 않았거나 직원 현황 공시를 찾지 못했어요." if quarter else "직원 현황 공시를 찾지 못했어요.")
    else:
        half = " · 반기보고서는 상반기 급여 기준" if res["report"].startswith("반기") else ""
        st.caption(f"평균 연봉 높은 순 · 평균 연봉 = 급여 총액 ÷ 직원 수 · 증감·전년比는 전년 같은 보고서 기준{half}")
        st.dataframe(
            emp.style.map(change_style, subset=["증감(명)", "연봉 전년比"]), hide_index=True,
            height=(len(emp) + 1) * 35 + 3,
        )

    st.markdown("##### 개인별 보수 (5억원 이상)")
    if quarter:
        st.caption(NO_PAY)
    elif people.empty:
        st.info(f"{label}에 공시된 5억원 이상 개인별 보수가 없어요. 연도나 보고서를 바꿔보세요.")
    else:
        who = st.text_input("이름으로 거르기", key="pay_who", placeholder="예: 이재용 (일부만 써도 돼요)")
        shown = people
        if who.strip():
            shown = people[people["이름"].map(person_key).str.contains(person_key(who), regex=False)]
        st.caption(f"{len(shown):,}명 · 보수 많은 순 · 단위: 억원 · 전년은 같은 이름 기준")
        if shown.empty:
            st.info("이름이 일치하는 사람이 없어요.")
        else:
            st.dataframe(
                shown.style.map(change_style, subset=["증감률"]), hide_index=True,
                column_config={"원문": LINK_COLUMN},
                height=min(len(shown) + 1, 16) * 35 + 3,
            )
            st.download_button(
                "CSV 다운로드",
                shown.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"임원보수_{safe_filename(corps_txt)}_{res['year']}.csv",
                mime="text/csv",
            )

    render_exctv(res, label)


def _xml_parts(data):
    """DART 원본파일 zip -> 읽을 수 있는 xml 글들 (본문이 먼저)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise AiError("DART 원문 파일을 열 수 없어요. 잠시 후 다시 시도해주세요.") from e
    out = []
    for name in sorted(zf.namelist(), key=lambda n: (not n.endswith(".xml"), len(n), n)):
        raw = zf.read(name)
        for enc in ("utf-8", "cp949"):
            try:
                out.append(raw.decode(enc))
                break
            except UnicodeDecodeError:
                continue
    return out


def _cell_text(html_cell):
    t = html.unescape(re.sub(r"<[^>]+>", " ", html_cell))
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()


def parse_unregistered_execs(xml):
    """보고서 원문(xml)의 '미등기임원 현황' 표 -> 행들 (API 임원 현황과 같은 필드 이름).
    API(exctvSttus)에는 미등기임원이 안 담겨서 원문 표를 직접 읽는다. 표의 칸(TD) 구조로 읽기 때문에
    칸 안에 줄바꿈이 있어도 된다."""
    trs = [(m.start(), [_cell_text(c) for c in re.findall(r"(?is)<T[DHEU][^>]*>(.*?)</T[DHEU]>", m.group(1))])
           for m in re.finditer(r"(?is)<TR[^>]*>(.*?)</TR>", xml)]
    norm = lambda c: re.sub(r"\s+", "", c)

    def is_data(cells):
        cells = [c for c in cells]
        return len(cells) >= 6 and cells[1] in ("남", "여")

    def is_header(cells):
        j = "".join(norm(c) for c in cells)
        return "성명" in j and "담당업무" in j

    picked = []  # [(헤더 칸들 또는 None, 데이터 칸들)]
    anchors = [m.start() for m in re.finditer(r"미등기\s*임원\s*현황", xml)]
    ends = [m.start() for m in re.finditer(r"미등기\s*임원의\s*변동", xml)]
    for h in anchors:  # 1) '미등기임원 현황' 제목 바로 다음의 표
        stop = next((e for e in ends if e > h), len(xml))
        header, rows = None, []
        for pos, cells in trs:
            if pos <= h or pos >= stop:
                continue
            if is_data(cells):
                rows.append(cells)
            elif header is None and not rows and is_header(cells):
                header = cells
        if len(rows) > len(picked):
            picked = [(header, r) for r in rows]
    if not picked:  # 2) 제목을 못 찾으면: '성명·담당업무·재직기간' 머리글(임기 없음)을 가진 표
        header, rows, active = None, [], False
        for pos, cells in trs:
            if is_header(cells):
                j = "".join(norm(c) for c in cells)
                active = "재직" in j and "임기" not in j
                header = cells if active else None
                continue
            if active and is_data(cells):
                rows.append(cells)
        picked = [(header, r) for r in rows]

    cell_keys = [
        (r"성명", "nm"), (r"성별", "sexdstn"), (r"출생", "birth_ym"), (r"직위", "ofcps"), (r"상근", "fte_at"),
        (r"담당업무", "chrg_job"), (r"주요경력", "main_career"), (r"학력", "_edu"),
        (r"최대주주", "mxmm_shrholdr_relate"), (r"재직", "hffc_pd"),
    ]
    default10 = ["nm", "sexdstn", "birth_ym", "ofcps", "fte_at", "chrg_job", "main_career", "_edu", "mxmm_shrholdr_relate", "hffc_pd"]
    out = []
    for header, cells in picked:
        keys = None
        if header and len(header) == len(cells):
            keys = [next((k for pat, k in cell_keys if re.search(pat, norm(h))), "_x") for h in header]
        if not keys or keys.count("nm") != 1:
            keys = default10[:len(cells)] if len(cells) >= 10 else (default10[:7] + default10[8:])[:len(cells)]
        row = {k: v for k, v in zip(keys, cells) if not k.startswith("_")}
        row["rgist_exctv_at"] = "미등기임원"
        out.append(row)
    return out


def unregistered_execs(rcept_no):
    """접수번호 -> (미등기임원 행들, 안내 문구 또는 None). 원문을 받아 읽는 데 몇 초 걸려서, 찾은 결과만 일주일 저장한다."""
    session, store = get_session(), get_cache_store()
    key = ("unreg2", rcept_no)  # 못 찾은 결과는 저장하지 않는다 (앞 버전의 빈 결과가 남아 계속 '없음'으로 보이는 일 방지)
    cached = cache_lookup(store, key, AI_TTL)
    if cached is not _MISSING and cached:
        return cached, None
    try:
        parts = _xml_parts(download_document(session, rcept_no))
    except AiError as e:
        return [], f"원문을 받지 못했어요 ({e})"
    rows = []
    for xml in parts:
        rows = parse_unregistered_execs(xml)
        if rows:
            break
    if rows:
        cache_save(store, key, rows)
        return rows, None
    diag = f"원문 파일 {len(parts)}개 · '미등기' {sum(x.count('미등기') for x in parts)}회 · 표 행 {sum(len(re.findall(r'(?i)<TR[ >]', x)) for x in parts):,}개"
    return [], f"원문에서 미등기임원 현황 표를 찾지 못했어요 (보고서에 없거나 표 모양이 달라요) · {diag}"


def find_periodic_rcept(corp_code, year, reprt):
    """API가 접수번호를 안 줄 때: 정기보고서 목록에서 해당 연도·종류의 보고서를 찾는다 (정정본이 있으면 가장 늦게 낸 것)."""
    month, kind = {"11011": (12, "사업보고서"), "11012": (6, "반기보고서"), "11013": (3, "분기보고서"), "11014": (9, "분기보고서")}[reprt]
    best = None
    for r in periodic_reports(corp_code):
        per = report_period_of(r.get("report_nm"))
        if per and per[0] == year and (reprt == "11011" or per[1] == month) and strip_correction(r.get("report_nm")).startswith(kind):
            if best is None or str(r["rcept_no"]) > str(best["rcept_no"]):
                best = r
    return str(best["rcept_no"]).strip() if best else None


def rt_exctv(corps, year, reprt, with_unreg=True):
    """회사별 임원 현황 = API(등기임원 등) + 원문의 미등기임원 현황 표. -> (표, 데이터 없는 회사 목록, 안내 문구들)"""
    api = RT_SECTIONS["exctv"]["api"]
    got = rt_collect([(n, c, year, api, reprt) for n, c in corps])
    notes, part, rcepts = [], {}, {}
    for n, c in corps:
        jo = got.get((n, year, api))
        lst = [dict(r) for r in ((jo or {}).get("list") or []) if isinstance(r, dict)]
        rno = next((str(r["rcept_no"]).strip() for r in lst if r.get("rcept_no")), None)
        if not rno and with_unreg:
            try:
                rno = find_periodic_rcept(c, year, reprt)
            except DartApiError:
                rno = None
        rcepts[n] = rno
        part[(n, year)] = lst
    if with_unreg:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            extra = dict(zip(rcepts, pool.map(lambda n: unregistered_execs(rcepts[n]) if rcepts[n] else ([], None), rcepts)))
    else:  # 미등기임원은 원문을 읽어야 해서 느리다 - 브리핑처럼 빨리 봐야 할 때는 API(등기임원)만
        extra = {n: ([], None) for n in rcepts}
    for n, _ in corps:
        rows, err = extra[n]
        if err:
            notes.append(f"{n}: 미등기임원 — {err}")
        have = {person_key(r.get("nm", "")) for r in part[(n, year)]}  # API에 이미 있는 사람은 다시 넣지 않는다
        for r in rows:
            if person_key(r.get("nm", "")) not in have:
                part[(n, year)].append(dict(r, rcept_no=rcepts[n] or ""))
    part = {k: ({"list": v} if v else None) for k, v in part.items()}
    df, empty = rt_frame(RT_SECTIONS["exctv"], part, False)
    return df, empty, notes


def render_exctv(res, label):
    """임직원 화면 맨 아래: 임원 현황 (등기임원 + 미등기임원)."""
    df, empty, notes = res["exctv"]
    st.markdown("##### 임원 현황 (등기·미등기)")
    for note in notes:
        st.caption(note)
    if empty:
        st.caption("데이터 없음: " + ", ".join(empty))
    if df.empty:
        st.info(f"{label}에서 임원 현황을 찾지 못했어요.")
        return
    kinds = ["등기임원", "미등기임원", "전체"]  # 처음엔 등기임원만 (미등기·전체는 목록이 길다)
    kind = st.radio("구분", kinds, horizontal=True, key="pay_exctv", label_visibility="collapsed")
    col = "등기 여부"
    flag = df[col].astype(str) if col in df.columns else pd.Series([""] * len(df))
    unreg = flag.str.contains("미등기")  # API는 '사내이사'·'사외이사'처럼 적기도 해서, 미등기가 아니면 등기임원으로 본다
    registered = ~unreg
    shown = df if kind == "전체" else df[registered if kind == "등기임원" else unreg]
    st.caption(f"{label} · 전체 {len(df):,}명 (등기 {int(registered.sum()):,}명 · 미등기 {int(unreg.sum()):,}명) · 보고서에 적힌 현재 임원")
    if shown.empty:
        st.info("해당하는 임원이 없어요.")
        return
    st.dataframe(shown, hide_index=True, column_config={"원문": LINK_COLUMN}, height=min(len(shown) + 1, 16) * 35 + 3)
    st.download_button(
        "CSV 다운로드", shown.drop(columns=["원문"]).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"임원현황_{safe_filename(', '.join(res['data']))}_{res['year']}.csv", mime="text/csv", key=f"pay_exctv_dl_{kind}",
    )
    render_exctv_diff(res, label)


PREV_REPORT = {"11011": ("11014", 0), "11014": ("11012", 0), "11012": ("11013", 0), "11013": ("11011", -1)}  # 직전 정기보고서: (보고서 코드, 연도 보정)
REPORT_NAME = {"11011": "사업보고서", "11012": "반기보고서", "11013": "1분기보고서", "11014": "3분기보고서"}
DIFF_BASIS = ["1년 전 같은 보고서", "직전 정기보고서"]


def exctv_diff(cur, prev):
    """두 시점의 임원 현황 표 -> 변동 표 (새로 들어온 사람·나간 사람·직위/구분이 바뀐 사람). 회사별로 비교한다."""
    def kind(v):
        return "미등기" if "미등기" in str(v) else "등기"

    def keyed(df):
        use_birth = "출생" in df.columns and (df["출생"].astype(str).str.strip().replace("-", "") != "").any()
        out = {}
        for _, r in df.iterrows():
            k = (r["회사"], person_key(r["성명"]), str(r.get("출생", "")).strip() if use_birth else "")
            out.setdefault(k, r)
        return out, use_birth

    a, ub1 = keyed(cur)
    b, ub2 = keyed(prev)
    if not (ub1 and ub2):  # 한쪽에 출생 정보가 없으면 이름만으로 비교
        a = {(k[0], k[1], ""): v for k, v in a.items()}
        b = {(k[0], k[1], ""): v for k, v in b.items()}
    rows = []

    def line(change, r, pos_text=None):
        return {"회사": r["회사"], "변동": change, "성명": r["성명"], "직위": pos_text if pos_text is not None else str(r.get("직위", "-")),
                "구분": kind(r.get("등기 여부")), "담당 업무": str(r.get("담당 업무", "-"))}

    for k, r in a.items():
        if k not in b:
            rows.append(line("새로 포함", r))
            continue
        old = b[k]
        pos_old, pos_new = str(old.get("직위", "-")), str(r.get("직위", "-"))
        k_old, k_new = kind(old.get("등기 여부")), kind(r.get("등기 여부"))
        if pos_old == pos_new and k_old == k_new:
            continue
        text = f"{pos_old} → {pos_new}" if pos_old != pos_new else pos_new
        if k_old != k_new:
            text += f" ({k_old}→{k_new})"
        rows.append(line("직위·구분 변경", r, text))
    for k, r in b.items():
        if k not in a:
            rows.append(line("목록에서 빠짐", r))
    order = {"새로 포함": 0, "목록에서 빠짐": 1, "직위·구분 변경": 2}
    rows.sort(key=lambda x: (x["회사"], order[x["변동"]], x["구분"] != "등기"))
    return pd.DataFrame(rows, columns=["회사", "변동", "성명", "직위", "구분", "담당 업무"])


def render_exctv_diff(res, label):
    """임원 현황 아래: 이전 정기보고서와 비교해 새로 들어오거나 빠진 임원 찾기."""
    st.markdown("##### 이전 보고서와 비교")
    basis = st.radio("비교 기준", DIFF_BASIS, horizontal=True, key="pay_exctv_basis",
                     help="1년 전 같은 보고서: 한 해 동안의 인사 변동 / 직전 정기보고서: 바로 앞 분기·반기·사업보고서와의 변동")
    reprt = PAY_REPORTS_ALL[res["report"]]
    if basis == DIFF_BASIS[0]:
        prev_reprt, prev_year = reprt, res["year"] - 1
    else:
        prev_reprt, delta = PREV_REPORT[reprt]
        prev_year = res["year"] + delta
    prev_label = f"{prev_year}년 {REPORT_NAME[prev_reprt]}"
    st.caption(f"{label} ↔ {prev_label} · 이름(출생연월 있으면 함께)이 같은 사람을 같은 사람으로 봐요")
    if st.button("비교하기", key="pay_exctv_diff_go"):
        if prev_year < 2015:
            st.error("2015년 이전 보고서는 비교할 수 없어요.")
        else:
            with st.spinner(f"{prev_label}의 임원 현황을 불러오는 중입니다... (미등기임원은 원문을 읽어서 몇 초 걸려요)"):
                try:
                    prev_df, prev_empty, prev_notes = rt_exctv(res["corps"], prev_year, prev_reprt)
                except DartApiError as e:
                    st.error(str(e))
                else:
                    cur_df = res["exctv"][0]
                    skip = set(prev_empty) | {c for c in res["data"] if c not in set(cur_df["회사"])}
                    both = [c for c in res["data"] if c not in skip]
                    st.session_state["pay_exctv_diff"] = {
                        "key": (res["year"], res["report"], basis, tuple(res["data"])), "prev_label": prev_label,
                        "table": exctv_diff(cur_df[cur_df["회사"].isin(both)], prev_df[prev_df["회사"].isin(both)]) if both else pd.DataFrame(),
                        "skipped": sorted(skip), "notes": prev_notes, "compared": both, "label": label, "id": time.time_ns(),
                    }
    out = st.session_state.get("pay_exctv_diff")
    if not out or out["key"] != (res["year"], res["report"], basis, tuple(res["data"])):
        return
    for note in out["notes"]:
        st.caption("이전 보고서 · " + note)
    if out["skipped"]:
        st.warning("임원 현황을 찾지 못해 비교하지 못한 회사: " + ", ".join(out["skipped"]))
    table = out["table"]
    if not out["compared"]:
        return
    counts = table["변동"].value_counts() if not table.empty else {}
    st.markdown(f"**{out['label']} ↔ {out['prev_label']}** · 새로 포함 {int(counts.get('새로 포함', 0)):,}명 · "
                f"목록에서 빠짐 {int(counts.get('목록에서 빠짐', 0)):,}명 · 직위·구분 변경 {int(counts.get('직위·구분 변경', 0)):,}명")
    if table.empty:
        st.info("두 보고서의 임원 명단이 같아요. 변동이 없습니다.")
        return
    st.caption("'목록에서 빠짐'은 퇴임·퇴직 외에 보고서 작성 방식 차이로 빠졌을 수도 있어요 (미등기임원은 5년 이상 재직하면 재직기간만 생략돼요). 확정 전에 원문으로 확인하세요.")
    st.dataframe(table, hide_index=True, height=min(len(table) + 1, 16) * 35 + 3)
    st.download_button("CSV 다운로드", table.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"임원변동_{safe_filename(', '.join(out['compared']))}_{res['year']}.csv", mime="text/csv",
                       key=f"pay_exctv_diff_dl_{out['id']}")


def render_dividend():
    """탭: 배당·자사주 - 회사별 배당금 총액·배당성향·주당 배당금과 자사주 취득·처분·소각."""
    years = list(range(TODAY.year, 2014, -1))
    with st.form("div_search"):
        corp_text = st.text_input(
            "기업명 (여러 곳은 쉼표로)", key="div_corp", placeholder="예: 삼성전자, 현대차, KB금융",
            help=f"쉼표(,)나 슬래시(/)로 나눠 최대 {MAX_DIV_CORPS}곳까지 비교해요.",
        )
        c1, c2 = st.columns(2)
        year = c1.selectbox("사업연도", years, index=years.index(default_pay_year()), key="div_year")
        report = c2.radio("보고서", list(PAY_REPORTS), horizontal=True, key="div_report")
        c3, c4 = st.columns(2)
        mode = c3.radio("보기", DIV_MODES, horizontal=True, key="div_mode",
                        help="연도별 추이는 고른 사업연도를 끝으로 최근 5년을 한 표에 보여줘요.")
        tsk_label = c4.selectbox("자사주 결정 공시 기간", list(TSK_PERIODS), key="div_tsk_period",
                                 help="자기주식 취득·처분·소각·신탁계약 결정 공시를 이 기간만큼 찾아요.")
        submitted = st.form_submit_button("배당·자사주 조회") or st.session_state.pop("div_run", False)

    if submitted:
        names = []
        for part in re.split(NEWS_SPLIT, corp_text):
            part = canonical_corp(part)
            if part and name_key(part) not in {name_key(n) for n in names}:
                names.append(part)
        if not names:
            st.error("기업명을 입력해주세요.")
        elif len(names) > MAX_DIV_CORPS:
            st.error(f"기업은 한 번에 {MAX_DIV_CORPS}곳까지 넣을 수 있어요. (지금 {len(names)}곳)")
        else:
            st.session_state.pop("dividend", None)
            corps = []
            if len(names) == 1:
                code = lookup_corp_code(names[0], "div")
                if code:
                    corps.append((names[0], code))
            else:
                with st.spinner("기업 정보를 확인하는 중입니다..."):
                    for n in names:
                        code = resolve_corp_code(n)
                        if code:
                            corps.append((n, code))
                        else:
                            hint = [label for label, _ in suggest_corps(n, limit=3)]
                            st.warning(f'"{n}" 기업을 찾지 못해 빼고 조회했어요.' + (f" 혹시: {', '.join(hint)}" if hint else ""))
            if corps:
                with st.spinner("배당·자사주 공시를 불러오는 중입니다..."):
                    try:
                        trend = mode == DIV_MODES[1]
                        if trend:
                            years_used = [y for y in range(int(year) - DIV_TREND_YEARS + 1, int(year) + 1) if y >= 2015]
                            data = collect_div_years(corps, years_used, PAY_REPORTS[report])
                        else:
                            data = collect_div(corps, int(year), PAY_REPORTS[report])
                        decisions = collect_tsk_decisions(corps, TSK_PERIODS[tsk_label])
                        px, px_err = None, None
                        if KRX_KEY:
                            try:
                                px = dividend_prices(corps, years_used if trend else [int(year)], PAY_REPORTS[report])
                            except KrxError as e:
                                px_err = str(e)
                    except DartApiError as e:
                        st.error(str(e))
                    else:
                        st.session_state["dividend"] = {
                            "data": data, "year": int(year), "report": report, "trend": trend, "corps": corps,
                            "years": years_used if trend else [int(year)], "decisions": decisions, "tsk_label": tsk_label,
                            "px": px, "px_err": px_err}

    res = st.session_state.get("dividend")
    if not res:
        return
    label = f"{res['year']}년 {res['report'].split(' ')[0]}"
    half = " · 반기보고서는 중간배당 기준" if res["report"].startswith("반기") else ""
    if res.get("trend"):
        render_div_trend(res, half)
    else:
        div, tsk = div_tables(res["data"], res.get("px"), res["year"])
        st.markdown("##### 배당")
        if div.empty:
            st.info(f"{label}에 배당 공시가 없어요. 연도나 보고서를 바꿔보세요.")
        else:
            st.caption(f"{label} · 배당성향은 연결 기준 · 주당 배당금·시가배당률은 보통주{half}")
            render_yield_note(res)
            st.dataframe(div.style.map(change_style, subset=["총액 전년比"]), hide_index=True,
                         height=(len(div) + 1) * 35 + 3)
        st.markdown("##### 자사주 (보통주, 주)")
        if tsk.empty:
            st.info(f"{label}에 자기주식 현황 공시가 없어요.")
        else:
            st.caption("기초 보유 → 취득 · 처분 · 소각 → 기말 보유 · 해당 보고서 기간 동안의 변동")
            st.dataframe(tsk.style.map(change_style, subset=["취득", "처분", "소각"]), hide_index=True,
                         height=(len(tsk) + 1) * 35 + 3)
        links = [f"[{c} 원문]({DART_VIEW_URL.format(d['rcept_no'])})" for c, d in res["data"].items() if d.get("rcept_no")]
        if links:
            st.markdown(" · ".join(links))
    render_tsk_decisions(res)


def render_yield_note(res):
    """배당수익률 칸 설명 (KRX 시세로 직접 계산한 값)."""
    if not KRX_KEY:
        krx_hint()
    elif res.get("px_err"):
        krx_error_box(res["px_err"])
    elif res.get("px"):
        day = res["px"][2]
        st.caption("배당수익률은 KRX 종가로 직접 계산한 값이에요 (주당 배당금 ÷ 기준일 종가). 기말 = 보고서 기준일(사업보고서 12/31, 반기 6/30)의 마지막 거래일"
                   + (f" · 현재가는 {day:%Y.%m.%d} 종가" if day else "") + " · 회사가 공시한 '시가배당률'은 계산 기준이 달라 차이가 날 수 있어요")


def render_div_trend(res, half):
    """배당·자사주 최근 5년 추이."""
    table, dps = div_trend_table(res["data"], res["corps"], res["years"], res.get("px"))
    st.markdown("##### 배당·자사주 5년 추이")
    if table.empty:
        st.info(f"{res['years'][0]}~{res['years'][-1]}년 {res['report'].split(' ')[0]}에 배당·자사주 공시가 없어요. 연도나 보고서를 바꿔보세요.")
        return
    st.caption(f"{res['years'][0]}~{res['years'][-1]}년 {res['report'].split(' ')[0]} · 오래된 해가 위 · 배당성향은 연결 기준 · "
               f"주당 배당금·시가배당률은 보통주{half} · 자사주는 보통주(주)")
    render_yield_note(res)
    st.dataframe(table.style.map(change_style, subset=["총액 전년比", "자사주 취득", "자사주 소각"]), hide_index=True,
                 column_config={"원문": LINK_COLUMN}, height=min(len(table) + 1, 21) * 35 + 3)
    lines = []
    for name, series in dps.items():
        s = dps_streak(series)
        if s:
            n, word = s
            lines.append(f"{name}: 주당 배당금 " + (f"직전 해보다 {word}" if n == 1 else f"{n}년 연속 {word}"))
    if lines:
        st.caption(" · ".join(lines))
    st.download_button("추이 CSV 다운로드", table.drop(columns=["원문"]).to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"배당자사주추이_{'_'.join(n for n, _ in res['corps'])}_{res['years'][-1]}.csv",
                       mime="text/csv", key="div_trend_csv")


def render_tsk_decisions(res):
    """자기주식 취득·처분·소각·신탁계약 결정 공시 목록."""
    rows, truncated = res["decisions"]
    st.markdown(f"##### 자사주 결정 공시 ({res['tsk_label']})")
    if not rows:
        st.info(f"{res['tsk_label']} 동안 자기주식 취득·처분·소각 결정 공시가 없어요.")
        return
    counts = []
    for name, _ in res["corps"]:
        mine = [r for r in rows if r["회사"] == name]
        parts = [f"{k} {sum(1 for r in mine if r['구분'] == k)}건" for k in TSK_KINDS if any(r["구분"] == k for r in mine)]
        counts.append(f"{name}: " + (" · ".join(parts) if parts else "없음"))
    st.caption("  \n".join(counts))
    if truncated:
        st.warning("공시가 매우 많아 최신 1,000건까지만 확인했어요. 기간을 줄여보세요.")
    kinds = [k for k in TSK_KINDS if any(r["구분"] == k for r in rows)]
    pick = st.multiselect("구분", kinds, default=kinds)
    shown = pd.DataFrame([r for r in rows if r["구분"] in pick])
    if shown.empty:
        st.info("고른 구분에 해당하는 공시가 없어요.")
        return
    st.caption("주식 수·금액·예정 기간은 결정 공시에 적힌 계획치예요. 실제 취득·처분 결과는 '결과' 공시나 정기보고서로 확인하세요. "
               "표시되지 않은 칸은 원문을 보세요.")
    st.dataframe(shown, hide_index=True, column_config={"원문": LINK_COLUMN}, height=min(len(shown) + 1, 16) * 35 + 3)
    st.download_button("결정 공시 CSV 다운로드", shown.drop(columns=["원문"]).to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"자사주결정공시_{'_'.join(n for n, _ in res['corps'])}.csv",
                       mime="text/csv", key="div_tsk_csv")


def render_feed():
    """탭 3: 오늘의 공시 - 하루 동안 전 상장사가 낸 공시 중 취재거리가 될 만한 것만 모아 보기"""
    with st.form("feed_search"):
        c1, c2 = st.columns(2)
        day = c1.date_input(
            "날짜", value=last_weekday(TODAY), min_value=date(2015, 1, 1), max_value=TODAY, key="feed_date"
        )
        market = c2.radio("시장", list(FEED_MARKETS), index=list(FEED_MARKETS).index("코스피"), horizontal=True, key="feed_market")
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
    saved = render_reaction(shown, "feed")
    if saved:
        render_reaction_table(feed_table(shown), shown, saved)
    else:
        st.dataframe(
            feed_table(shown),
            hide_index=True,
            column_config={"보고서명": st.column_config.TextColumn("보고서명", width="large"), "원문": LINK_COLUMN},
        )
    render_ai_summary(shown, "feed", all_rows=res["rows"])
    render_news_picks(shown, res["id"])


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
        period = st.radio("기간", list(NEWS_PERIODS), index=list(NEWS_PERIODS).index("3일"), horizontal=True, key="news_period")
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
            match_extra = []  # 섹터 모드에서 검색하지 않는 줄임말 (제목 확인용)
            owner = {}  # 검색어 -> 어느 기업 것인지 (여러 기업 검색 때 기사에 기업명 꼬리표를 달기 위해)
            if mode == "기업명":
                terms = []
                for c in corps:
                    for t in news_names(c):  # 입력한 이름 + 별칭
                        if t not in owner:
                            owner[t] = c
                            terms.append(t)
            else:
                # 섹터: 줄임말(별칭)은 따로 검색하지 않는다 (호출 수 절약). 예: 삼성전자·삼전 -> 삼성전자만 검색
                kept, skip = [], set()
                for f in NEWS_SECTORS[sector]["기업"]:
                    if name_key(f) in skip:  # 앞에서 검색하는 기업의 줄임말이면 건너뜀
                        continue
                    kept.append(f)
                    skip.update(name_key(a) for a in NEWS_ALIASES.get(f, []))
                terms = kept + NEWS_SECTORS[sector]["키워드"]
                for t in NEWS_SECTORS[sector]["기업"] + NEWS_SECTORS[sector]["키워드"]:
                    match_extra.append(t)  # 검색은 안 해도, 제목에 줄임말이 있으면 걸러내지 않고 꼬리표도 단다
            multi = len(corps) > 1
            with st.spinner("뉴스를 불러오는 중입니다..."):
                try:
                    items, capped, since = search_news(terms, NEWS_PERIODS[period])
                except NewsApiError as e:
                    st.error(str(e))
                else:
                    total = len(items)
                    items = [i for i in items if i["category"]]  # 목록에 있는 언론사만
                    keys = {t: name_key(t) for t in list(terms) + match_extra}
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
        notes.append(f"NEW {fresh}건 (어제·오늘)")
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


# ------------------------------------------------------------
# 재계 순위 (공정위 공시대상기업집단 지정 자료. 엑셀을 chaebol_2026.json으로 바꿔 둔 정적 자료)
# ------------------------------------------------------------
CHAEBOL_FILE = "chaebol_2026.json"  # make_chaebol_data.py로 공정위 별첨 엑셀 5개를 합쳐 만든 파일 (앱과 같은 폴더)
CHAEBOL_OWN_DPI = 170  # 지분구조도 그림 선명도 (A4 가로 한 쪽 = 약 1,990px)
CHAEBOL_SOURCE = "공정거래위원회 2026.4.29. 보도자료(5.1. 지정 기준)"
# 보도자료 본문에서 가져온 설명 (숫자는 표에서 계산하고, 여기에는 사유 같은 글만 둔다). 새 자료로 바꿀 때 함께 고쳐주세요.
CHAEBOL_NOTES = [
    "신규 지정: 한국콜마·오리온(K-뷰티·K-푸드 해외 매출 증가), 토스(증권업 활황), 웅진(상조회사 프리드라이프 인수), "
    "희성(귀금속 재고 증가), 일진글로벌(환율 상승에 따른 해외 매출 확대) 등. 지난해 지정이던 영원은 자산 5조 원 미만이 돼 제외.",
    "상호출자제한 상향: 교보생명보험(에스비아이저축은행 인수), 다우키움(증권업 활황). 이랜드는 공시대상으로 하향.",
    "순위 상승: 한화 7→5위, 한국항공우주산업 62→53위, 엘아이지 69→63위(방위산업 수요), 태광 59→48위(애경산업 인수), "
    "소노인터내셔널 64→52위(티웨이항공 인수), DB 40→37위, 대신 76→69위(증권업).",
    "동일인: 중흥건설은 故 정창선 사망으로 장남 정원주로 변경, 쿠팡은 법인에서 김범석으로 변경 지정, 두나무는 법인 동일인 유지.",
]
CHAEBOL_VIEWS = ["전체", "상호출자제한", "신규 지정"]


@st.cache_resource
def get_chaebol():
    """chaebol_2026.json을 읽어 돌려준다. 파일이 없으면 None."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CHAEBOL_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def cb_money(v, unit):
    """십억 원 -> 억원/조원 글자. 값이 없으면 '-'."""
    return "-" if v is None else fmt_amount(v * 1e9, unit)


def cb_move(change):
    """'2↑' -> '▲2', '1↓' -> '▼1', '0' -> '-', '신규' -> 'NEW'."""
    c = str(change or "").strip()
    if c.endswith("↑"):
        return f"▲{c[:-1]}"
    if c.endswith("↓"):
        return f"▼{c[:-1]}"
    return "NEW" if c == "신규" else "-"


def cb_pct(cur, prev):
    return pct_change(cur, prev)


def cb_cards(items):
    """작은 숫자 카드 줄. items = [(이름, 값, 증감 글자 또는 None, 'up'|'down'|'flat'|None), ...] (모바일에서도 2칸씩 나란히)"""
    cards = []
    for label, value, delta, tone in items:
        if delta and not tone:
            tone = "up" if delta.startswith("+") else ("down" if delta.startswith("-") else "flat")
        d = f'<div class="d {tone or "flat"}">{html.escape(delta)}</div>' if delta else ""
        cards.append(f'<div class="cb-card"><div class="l">{html.escape(label)}</div><div class="v">{html.escape(value)}</div>{d}</div>')
    return f'<div class="cb-stats n{len(items)}">{"".join(cards)}</div>'


def cb_kind(g):
    return "상호출자제한" if g.get("limited") else "공시대상"


def cb_style(cell):
    t = str(cell)
    if t.startswith("▲") or t.startswith("+"):
        return f"font-weight: bold; color: {POSITIVE_COLOR}"
    if t.startswith("▼") or (t.startswith("-") and t != "-"):
        return f"font-weight: bold; color: {NEGATIVE_COLOR}"
    if t == "NEW":
        return "font-weight: bold; color: #e03131"
    return ""


def cb_filter(groups, view, query):
    """보기(전체·상호출자제한·신규 지정)와 검색어(그룹명·동일인)로 걸러낸다."""
    out = [g for g in groups
           if view == "전체" or (view == "상호출자제한" and g["limited"]) or (view == "신규 지정" and g["is_new"])]
    q = name_key(query or "")
    if q:
        out = [g for g in out if q in name_key(g["name"]) or q in name_key(g["owner"])]
    return out


def cb_rank_table(groups, unit, caps=None):
    """caps = chaebol_caps 결과(없으면 시총 칸 생략)"""
    rows = []
    order = {}
    if caps:
        ranked = sorted((r for r, v in caps["groups"].items() if v["cap"]), key=lambda r: -caps["groups"][r]["cap"])
        order = {r: i + 1 for i, r in enumerate(ranked)}
    for g in groups:
        grow = cb_pct(g["asset"], g["asset_prev"])
        dn = g["n"] - g["n_prev"] if g.get("n_prev") is not None else None
        rows.append({
            "순위": g["rank"], "변동": cb_move(g["change"]), "기업집단": g["name"],
            "공정자산": cb_money(g["asset"], unit),
            "계열회사": f"{g['n']:,}" + (f" ({dn:+d})" if dn else ""),
            "자산 증감": "-" if grow is None else f"{grow:+.1f}%",
            **({"상장사 시총": fmt_amount(caps["groups"][int(g["rank"])]["cap"], unit) if caps["groups"].get(int(g["rank"]), {}).get("cap") else "-",
                "시총 순위": str(order.get(int(g["rank"]), "-"))} if caps else {}),
            "동일인": g["owner"], "구분": cb_kind(g),
        })
    return pd.DataFrame(rows)


def cb_fin_table(g, unit):
    """재무·경영성과 표: 비금융·보험회사 / 전체회사."""
    def debt_ratio_text(f):
        if not f or not f.get("equity") or f.get("debt") is None:
            return "-"
        return f"{f['debt'] / f['equity'] * 100:,.1f}%"

    fin, perf = g.get("fin") or {}, g.get("perf") or {}
    rows = []
    for label, key in (("자산총액", "asset"), ("자본총액", "equity"), ("부채총액", "debt")):
        rows.append({"항목": label, "비금융·보험회사": cb_money((fin.get("nf") or {}).get(key), unit),
                     "전체회사": cb_money((fin.get("all") or {}).get(key), unit)})
    rows.append({"항목": "부채비율", "비금융·보험회사": debt_ratio_text(fin.get("nf")), "전체회사": debt_ratio_text(fin.get("all"))})
    rows.append({"항목": "매출액", "비금융·보험회사": cb_money((perf.get("nf") or {}).get("rev"), unit),
                 "전체회사": cb_money((perf.get("all") or {}).get("rev"), unit)})
    rows.append({"항목": "당기순이익", "비금융·보험회사": cb_money((perf.get("nf") or {}).get("ni"), unit),
                 "전체회사": cb_money((perf.get("all") or {}).get("ni"), unit)})
    return pd.DataFrame(rows)


def cb_pdf_path(data):
    """소유지분도 PDF 경로 (app.py와 같은 폴더). 자료에 이름이 없거나 파일이 없으면 None."""
    name = data.get("ownership_pdf")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name) if name else None
    return path if path and os.path.exists(path) else None


def _pdf_lib():
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        import fitz  # 옛 이름
        return fitz


@st.cache_data(show_spinner=False, max_entries=30)
def cb_render_page(pdf_path, page_no, dpi=CHAEBOL_OWN_DPI):
    """PDF 한 쪽을 그림(PNG)으로. -> PNG 바이트. 한 번 그린 쪽은 저장해 둔다."""
    doc = _pdf_lib().open(pdf_path)
    try:
        return doc[page_no - 1].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


@st.cache_data(show_spinner=False, max_entries=30)
def cb_page_pdf(pdf_path, page_no):
    """원본 PDF에서 한 쪽만 떼어낸 PDF (휴대폰 PDF 뷰어에서 확대해 보기용)."""
    lib = _pdf_lib()
    src, out = lib.open(pdf_path), lib.open()
    try:
        out.insert_pdf(src, from_page=page_no - 1, to_page=page_no - 1)
        return out.tobytes(deflate=True, garbage=3)
    finally:
        out.close()
        src.close()


def render_ownership(g, data):
    """그룹 상세의 '지분구조도': 소유지분도 PDF에서 그 그룹의 쪽을 그려 보여준다."""
    st.markdown("##### 지분구조도")
    path, page = cb_pdf_path(data), g.get("own_page")
    if not path or not page:
        st.info(f"지분구조도 PDF({data.get('ownership_pdf') or 'chaebol_ownership_2026.pdf'})를 app.py와 같은 폴더에 올리면 "
                "여기에 그룹별 지분구조도가 나타나요.")
        return
    try:
        png = cb_render_page(path, int(page))
        one_page = cb_page_pdf(path, int(page))
    except Exception as e:  # PDF 도구(pymupdf)가 없거나 PDF가 깨진 경우에도 앱 전체는 그대로
        st.warning(f"지분구조도를 그리지 못했어요. requirements.txt에 pymupdf가 들어 있는지 확인해주세요. ({type(e).__name__})")
        return
    big = st.checkbox("크게 보기 (옆으로 밀어서 보기)", key=f"cb_big_{g['rank']}",
                      help="휴대폰에서 글씨가 작을 때 켜세요. 그림을 크게 펼치고 옆으로 밀어서 볼 수 있어요.")
    if big:
        b64 = base64.b64encode(png).decode()
        st.markdown(f'<div class="cb-own-scroll"><img src="data:image/png;base64,{b64}" alt="{html.escape(g["name"])} 지분구조도"></div>',
                    unsafe_allow_html=True)
    else:
        st.image(png, caption=f"{g['name']} 소유지분도 · {CHAEBOL_SOURCE.split('(')[0].strip()} · 그림을 누르면 전체 화면")
    st.download_button("이 그룹 지분구조도 PDF 받기", one_page, file_name=f"{g['name']}_소유지분도_{data['year']}.pdf",
                       mime="application/pdf", key=f"cb_pdf_{g['rank']}")
    st.caption("2026.5.1. 지정일 기준 · 발행주식총수 기준 · 단위 %. 음영은 지주회사 등, ★은 상장회사, 마름모(◇)는 총수일가 지분 20% 이상 회사.")


def render_chaebol_caps(g, unit, caps):
    """그룹 상세: 상장 계열사 시가총액."""
    info = (caps or {}).get("groups", {}).get(int(g["rank"]))
    st.markdown("##### 상장 계열사 시가총액")
    if not info or not info["firms"]:
        st.info("KRX에서 시세를 찾은 상장 계열사가 없어요.")
        return
    total = info["cap"]
    rows = [{"회사": n, "종목코드": c, "종가": f"{px:,.0f}원" if px else "-", "전일比": pct_text(rt, 2),
             "시가총액": fmt_amount(cap, unit) if cap else "-", "비중": f"{cap / total * 100:.1f}%" if cap and total else "-"}
            for n, c, px, rt, cap in info["firms"]]
    rows.append({"회사": f"합계 ({len(info['firms'])}개사)", "종목코드": "", "종가": "", "전일比": "",
                 "시가총액": fmt_amount(total, unit), "비중": "100%"})
    table = pd.DataFrame(rows)
    asset = g.get("asset")
    ratio = f" · 공정자산총액({cb_money(asset, unit)}{unit})의 {total / (asset * 1e9) * 100:.0f}%" if asset else ""
    st.caption(f"KRX {caps['day']:%Y.%m.%d} 종가 기준 · 단위: {unit} · 보통주+우선주 · 계열회사 명단의 이름을 DART 상장사 이름과 맞춰 찾은 것이라 "
               f"이름이 다른 회사는 빠질 수 있어요{ratio}")
    st.dataframe(table.style.map(price_style, subset=["전일比"]).apply(total_row_style, axis=1), hide_index=True,
                 height=min(len(table) + 1, 14) * 35 + 3)


def render_chaebol_detail(g, unit, data, caps=None):
    st.markdown(f"#### {g['name']} · 동일인 {g['owner']}")
    prev = f"전년 {int(g['rank_prev'])}위" if g.get("rank_prev") else "올해 신규 지정"
    move = {"▲": "상승", "▼": "하락"}.get(cb_move(g["change"])[:1], "")
    move_text = f" ({cb_move(g['change'])[1:]}계단 {move})" if move else ""
    tags = [f"공정자산 {int(g['rank'])}위 · {prev}{move_text}", f"{cb_kind(g)}기업집단"]
    if g["is_new"]:
        tags.append("2026년 신규 지정")
    st.caption(" · ".join(tags))

    grow = cb_pct(g["asset"], g["asset_prev"])
    dn = g["n"] - g["n_prev"] if g.get("n_prev") is not None else None
    perf = (g.get("perf") or {}).get("all") or {}
    st.markdown(cb_cards([
        ("공정자산총액", f"{cb_money(g['asset'], unit)}{unit}",
         None if grow is None else f"{grow:+.1f}% (전년 {cb_money(g['asset_prev'], unit)}{unit})", None),
        ("계열회사", f"{g['n']:,}곳", None if dn is None else f"{dn:+d}곳 (전년 {g['n_prev']:,})", None),
        ("매출액(전체회사)", f"{cb_money(perf.get('rev'), unit)}{unit}", None, None),
        ("당기순이익(전체회사)", f"{cb_money(perf.get('ni'), unit)}{unit}", None, None),
    ]), unsafe_allow_html=True)

    st.markdown("##### 재무·경영성과")
    st.caption(f"단위: {unit} · 비금융·보험회사 = 금융·보험 계열사를 뺀 합계, 전체회사 = 모든 계열사 합계 · "
               "공정자산총액은 지정 기준으로 따로 산정한 값이라 여기 자산총액과 다를 수 있어요")
    fin_table = cb_fin_table(g, unit)
    st.dataframe(fin_table, hide_index=True, height=(len(fin_table) + 1) * 35 + 3)
    if caps:
        render_chaebol_caps(g, unit, caps)

    render_ownership(g, data)

    moves = g.get("moves") or []
    n_inc = sum(1 for m in moves if m["kind"] == "증가")
    n_dec = sum(1 for m in moves if m["kind"] == "감소")
    with st.expander(f"계열회사 변동 · 2025년 {g['n_prev'] if g.get('n_prev') is not None else '-'}곳 → 2026년 {g['n']}곳 (증가 {n_inc}, 감소 {n_dec})"):
        if not moves:
            st.write("지난 1년간 계열회사 변동이 없어요." if g.get("n_prev") is not None else "올해 처음 지정돼 비교할 전년 자료가 없어요.")
        else:
            reasons = {}
            for m in moves:
                reasons[(m["kind"], m["reason"])] = reasons.get((m["kind"], m["reason"]), 0) + 1
            st.caption(" · ".join(f"{k} {r} {n}곳" for (k, r), n in reasons.items()))
            st.dataframe(pd.DataFrame(moves).rename(columns={"kind": "구분", "reason": "사유", "name": "회사명"}),
                         hide_index=True, height=min(len(moves) + 1, 12) * 35 + 3)

    members = g.get("members") or []
    n_nf = sum(1 for m in members if m["type"] == "비금융")
    with st.expander(f"계열회사 명단 · {len(members):,}곳 (비금융 {n_nf}, 금융·보험 {len(members) - n_nf})"):
        pick_type = st.radio("구분", ["전체", "비금융", "금융·보험"], horizontal=True, key=f"cb_type_{g['rank']}")
        shown_members = [m for m in members if pick_type == "전체" or m["type"] == pick_type]
        table = pd.DataFrame(shown_members).rename(columns={"name": "회사명", "type": "구분", "no": "법인등록번호"})
        st.dataframe(table, hide_index=True, height=min(len(table) + 1, 12) * 35 + 3)
        st.download_button("명단 CSV 다운로드", pd.DataFrame(members).rename(columns={"name": "회사명", "type": "구분", "no": "법인등록번호"})
                           .to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{g['name']}_계열회사_{CHAEBOL_YEAR}.csv", mime="text/csv", key=f"cb_csv_{g['rank']}")


CHAEBOL_YEAR = 2026


def render_chaebol():
    """탭: 재계 순위 - 공정위가 지정한 공시대상기업집단의 순위·동일인·재무·계열회사·지분구조도를 한곳에 정리 (정적 자료)."""
    data = get_chaebol()
    if not data:
        st.info(f"{CHAEBOL_FILE} 파일이 없어요. make_chaebol_data.py로 공정위 별첨 엑셀을 합쳐 만든 파일을 app.py와 같은 폴더에 올려주세요.")
        return
    groups, excluded = data["groups"], data.get("excluded", [])
    n_groups, n_firms = len(groups), sum(g["n"] for g in groups)
    prev_groups = sum(1 for g in groups if g.get("n_prev") is not None) + len(excluded)
    prev_firms = sum(g.get("n_prev") or 0 for g in groups) + sum(e.get("n_prev") or 0 for e in excluded)
    limited = [g for g in groups if g["limited"]]
    st.caption(f"{data['year']}년 공시대상기업집단 · 출처: {CHAEBOL_SOURCE}")

    st.markdown(cb_cards([
        ("공시대상기업집단", f"{n_groups}개", f"{n_groups - prev_groups:+d}개", None),
        ("소속회사", f"{n_firms:,}곳", f"{n_firms - prev_firms:+,}곳", None),
        ("상호출자제한", f"{len(limited)}개", None, None),
        ("신규 지정", f"{sum(1 for g in groups if g['is_new'])}개", f"제외 {len(excluded)}개" if excluded else None, "flat"),
    ]), unsafe_allow_html=True)
    with st.expander("올해 지정 요약 (보도자료)"):
        for note in CHAEBOL_NOTES:
            st.markdown(f"- {note}")
        st.caption("상호출자제한 = 공정자산 12조 원 이상(음영 표시 집단), 공시대상 = 5조 원 이상. 설명은 보도자료 본문을 옮긴 것이에요.")

    st.markdown("##### 순위표")
    c1, c2 = st.columns([3, 2])
    view = c1.radio("보기", CHAEBOL_VIEWS, horizontal=True, key="cb_view")
    unit = c2.radio("단위", list(UNITS), index=list(UNITS).index("조원"), horizontal=True, key="cb_unit")  # 재계는 금액이 커서 조원이 기본
    query = st.text_input("그룹명·동일인 검색", key="cb_query", placeholder="예: 삼성, SK, 이재용")
    caps = None
    if KRX_KEY:
        try:
            info = chaebol_caps(str(TODAY))
            caps = {"groups": info["groups"], "day": info["day"]} if info else None
        except KrxError as e:
            krx_error_box(e)
    else:
        krx_hint()
    shown = cb_filter(groups, view, query)
    if not shown:
        st.info("조건에 맞는 기업집단이 없어요. 검색어나 보기를 바꿔보세요.")
    else:
        table = cb_rank_table(shown, unit, caps)
        st.caption(f"{len(shown)}개 집단 · 공정자산 단위: {unit} · 변동은 전년 대비 순위 · 계열회사 괄호는 전년 대비 증감")
        st.dataframe(
            table.style.map(cb_style, subset=["변동", "자산 증감"]), hide_index=True,
            column_config={"순위": st.column_config.NumberColumn("순위", width=52),
                           "변동": st.column_config.TextColumn("변동", width=55),
                           "기업집단": st.column_config.TextColumn("기업집단", width=110),
                           "공정자산": st.column_config.TextColumn("공정자산", width=85),
                           "계열회사": st.column_config.TextColumn("계열회사", width=85),
                           "자산 증감": st.column_config.TextColumn("자산 증감", width=85)},
            height=min(len(table) + 1, 16) * 35 + 3,
        )
        st.download_button("순위표 CSV 다운로드", table.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"재계순위_{data['year']}.csv", mime="text/csv", key="cb_csv_all")

    st.markdown("##### 그룹 자세히 보기")
    labels = {f"{int(g['rank'])}위 {g['name']} ({g['owner']})": g for g in groups}
    choice = st.selectbox("기업집단 선택", list(labels), index=None, key="cb_group",
                          placeholder="그룹을 고르면 재무·지분구조도·계열회사를 모아 보여줘요")
    if choice:
        render_chaebol_detail(labels[choice], unit, data, caps)
    st.caption("자료: 공정거래위원회 · 계열회사 수·재무·경영성과는 보도자료 별첨2~6 기준")


# ------------------------------------------------------------
# KRX 시세 (KRX Open API) - 공시 반응·시가총액·배당수익률
# ------------------------------------------------------------
# KRX는 '하루치 전 종목'을 한 번에 돌려주는 방식이라, 회사가 몇 곳이든 날짜 수만큼만 호출한다.
# 시장(코스피·코스닥)마다 이용 신청이 따로라서, 승인되지 않은 시장은 건너뛰고 되는 시장만 쓴다.
KRX_KEY = load_secret("KRX_API_KEY")
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"
KRX_MARKETS = {"코스피": "sto/stk_bydd_trd", "코스닥": "sto/ksq_bydd_trd"}  # 코넥스(sto/knx_bydd_trd)는 호출을 아끼려고 뺐다
KRX_PAST_TTL = 30 * 24 * 3600  # 이미 지난 날의 시세는 바뀌지 않는다
KRX_RECENT_TTL = 3600  # 어제·오늘 시세는 아직 올라오기 전일 수 있어 짧게
KRX_MAX_DAYS = 160  # 저장해 두는 날짜 수 (메모리 보호)
KRX_REACT_SPAN = 100  # 공시 반응을 한 번에 보는 기간(일)
KRX_LOOKBACK = 12  # 휴장일이면 이만큼(일) 앞까지 거슬러 올라가 가장 가까운 거래일을 찾는다


class KrxError(Exception):
    """KRX 키 문제·승인 안 됨·서버 오류 등 사용자에게 알려야 하는 조회 실패."""


KrxRow = namedtuple("KrxRow", "name market close chg rate vol val cap shares")


@st.cache_resource
def _krx_store():
    return {"days": {}, "lock": threading.Lock(), "auth": {"mode": "header"}, "status": {}}


def krx_num(v):
    """'1,234' / '-1.5' / '-' -> 숫자. 못 읽으면 None."""
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def krx_code6(row):
    """KRX 줄 -> 6자리 종목코드. ISU_CD가 12자리 표준코드(KR7005930003)면 가운데 6자리를 쓴다."""
    for key in ("ISU_SRT_CD", "ISU_CD"):
        s = re.sub(r"\s+", "", str(row.get(key) or ""))
        if len(s) == 12 and s[:2].isalpha():
            return s[3:9]
        if len(s) == 6:
            return s
    return None


def krx_parse(rows, market):
    """KRX 응답 줄들 -> {종목코드: KrxRow}. 필드가 비어 있는 줄은 그대로 None으로 둔다."""
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = krx_code6(r)
        if not code:
            continue
        out[code] = KrxRow(
            str(r.get("ISU_NM") or r.get("ISU_ABBRV") or "").strip(), market,
            krx_num(r.get("TDD_CLSPRC")), krx_num(r.get("CMPPREVDD_PRC")), krx_num(r.get("FLUC_RT")),
            krx_num(r.get("ACC_TRDVOL")), krx_num(r.get("ACC_TRDVAL")), krx_num(r.get("MKTCAP")),
            krx_num(r.get("LIST_SHRS")),
        )
    return out


def krx_body(res):
    """오류 응답 본문을 짧게 (원인 파악용)."""
    try:
        return re.sub(r"\s+", " ", str(res.text or ""))[:120] or "본문 없음"
    except Exception:
        return "본문 없음"


def krx_request(session, path, day_str):
    """KRX 한 번 호출. 인증키는 헤더로 먼저 보내고, 거절되면 주소 파라미터로 한 번 더 시도한다."""
    store = _krx_store()
    url = f"{KRX_BASE}/{path}"
    modes = [store["auth"]["mode"], "query" if store["auth"]["mode"] == "header" else "header"]
    res, last = None, None
    for i, mode in enumerate(modes):
        params = {"basDd": day_str}
        headers = {"AUTH_KEY": KRX_KEY}
        if mode == "query":
            params["AUTH_KEY"], headers = KRX_KEY, {}
        for attempt in range(3):  # 일시적인 네트워크 오류는 두 번까지 다시 시도
            try:
                res = session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
                break
            except Exception as e:
                last = e
                res = None
                time.sleep(0.5 * (attempt + 1))
        if res is None:
            raise KrxError("KRX 서버 응답이 없거나 늦어지고 있어요. 잠시 후 다시 시도해주세요.") from last
        if res.status_code in (401, 403) and i == 0:
            continue  # 다른 방식으로 한 번 더
        if res.status_code == 200:
            store["auth"]["mode"] = mode
        break
    if res.status_code in (401, 403):
        raise KrxError("KRX 인증키가 올바르지 않거나, 이 시세 API의 이용 승인이 아직 안 됐어요. "
                       "KRX Open API에서 '유가증권/코스닥 일별매매정보' 이용 신청 상태를 확인해주세요. "
                       f"(서버 응답 {res.status_code}: {krx_body(res)} · {path})")
    if res.status_code == 429:
        raise KrxError("KRX API 하루 호출 한도를 넘었어요. 내일 다시 시도해주세요.")
    if res.status_code != 200:
        raise KrxError(f"KRX 응답 오류 [{res.status_code}: {krx_body(res)}] {path}")
    try:
        data = res.json()
    except Exception as e:
        raise KrxError("KRX 응답을 읽지 못했어요.") from e
    if isinstance(data, dict) and str(data.get("respCode", "200")) not in ("200", "") and "OutBlock_1" not in data:
        raise KrxError(f"KRX 오류: {data.get('respMsg') or data.get('respCode')}")
    rows = data.get("OutBlock_1") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def krx_market_day(day, market, session=None):
    """한 시장의 하루치 시세 {종목코드: KrxRow}. 휴장일이면 빈 dict. 오류는 올려 보내고 저장하지 않는다."""
    store = _krx_store()
    key = (day.strftime("%Y%m%d"), market)
    now = time.time()
    with store["lock"]:
        hit = store["days"].get(key)
    if hit and now - hit[0] < (KRX_PAST_TTL if day < TODAY - timedelta(days=2) else KRX_RECENT_TTL):
        return hit[1]
    got = krx_parse(krx_request(session or get_session(), KRX_MARKETS[market], key[0]), market)
    with store["lock"]:
        store["days"][key] = (now, got)
        if len(store["days"]) > KRX_MAX_DAYS * len(KRX_MARKETS):  # 가장 오래 전에 저장한 것부터 비운다
            for k in sorted(store["days"], key=lambda k: store["days"][k][0])[:len(KRX_MARKETS)]:
                store["days"].pop(k, None)
    return got


def krx_day(day, markets=None):
    """하루치 시세 (여러 시장 합침) -> (dict, 실패한 시장 {시장: 오류글}). 모든 시장이 실패하면 KrxError."""
    markets = markets or list(KRX_MARKETS)
    merged, failed = {}, {}
    for m in markets:
        try:
            merged.update(krx_market_day(day, m))
        except KrxError as e:
            failed[m] = str(e)
    if failed and len(failed) == len(markets):
        raise KrxError(next(iter(failed.values())))
    return merged, failed


def krx_days(days, markets=None):
    """여러 날짜를 동시에 -> ({날짜: dict}, {시장: 오류글}). 실패한 날은 빠진다. 전부 실패하면 KrxError."""
    days = list(days)
    out, errors, failed = {}, [], {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(krx_day, d, markets): d for d in days}
        for f in as_completed(futs):
            try:
                rows, bad = f.result()
                out[futs[f]] = rows
                failed.update(bad)
            except KrxError as e:
                errors.append(str(e))
    if days and not out and errors:
        raise KrxError(errors[0])
    return out, failed


def krx_on_or_before(target, markets=None):
    """target 날짜 또는 그 이전 가장 가까운 거래일 -> (날짜, dict). 못 찾으면 (None, {})."""
    day = min(target, TODAY)
    for _ in range(KRX_LOOKBACK):
        if day.weekday() < 5:
            rows, _ = krx_day(day, markets)
            if rows:
                return day, rows
        day -= timedelta(days=1)
    return None, {}


def krx_latest(markets=None):
    """가장 최근 거래일 시세. 시세는 다음 날 아침에 올라오므로 보통 어제나 그 전 거래일."""
    return krx_on_or_before(TODAY, markets)


# 우선주 이름: 삼성전자우 / 현대차2우B / 대한항공우(전환)
KRX_PREF = re.compile(r"^(?P<base>.+?)(?P<s>\d?우[A-C]?(?:\(전환\))?)$")


def krx_caps(rows):
    """{보통주 종목코드: 시가총액(보통주+우선주, 원)}. 우선주는 '보통주 이름 + 우' 꼴인 것만 더한다."""
    by_name = {}
    for code, r in rows.items():
        if r.name and not KRX_PREF.match(r.name):
            by_name.setdefault(r.name, code)
    caps = {c: r.cap for c, r in rows.items() if r.cap is not None and not KRX_PREF.match(r.name)}
    for code, r in rows.items():
        m = KRX_PREF.match(r.name or "")
        if m and r.cap is not None and m.group("base") in by_name:
            base = by_name[m.group("base")]
            caps[base] = caps.get(base, 0) + r.cap
    return caps


def stock_code_of(corp_code):
    """DART 고유번호 -> 6자리 종목코드 (비상장이면 None)."""
    idx = get_name_index()
    hit = idx[idx["corp_code"] == corp_code]
    if hit.empty:
        return None
    code = str(hit.iloc[0]["stock_code"]).strip()
    return code if len(code) == 6 else None


def price_style(cell):
    """주가 등락 칸: 한국 증시 관행대로 오르면 빨강, 내리면 파랑."""
    t = str(cell)
    if t.startswith("+"):
        return f"font-weight: bold; color: {NEGATIVE_COLOR}"
    if t.startswith("-") and t != "-":
        return f"font-weight: bold; color: {POSITIVE_COLOR}"
    return ""


def pct_text(v, digits=1):
    return "-" if v is None else f"{v:+.{digits}f}%"


def krx_hint():
    """KRX 키가 없을 때 각 화면에 붙이는 안내."""
    st.caption("주가·시가총액을 함께 보려면 Streamlit Secrets에 KRX_API_KEY를 추가해주세요.")


def krx_error_box(e):
    st.warning(f"주가 정보를 가져오지 못했어요. {e}")


# --- 1) 공시 전후 주가 반응 ---
def weekdays_between(first, last):
    d, out = first, []
    while d <= last:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def price_reaction(rows):
    """공시 줄들(rcept_dt, stock_code 포함) -> ({rcept_no: 반응 dict}, 안내글 목록).

    반응: 직전 5일(공시 전날까지 5거래일 등락), 공시일 등락, 다음날 등락, 5거래일 누적(공시 전날 종가 대비).
    DART는 접수 시각을 주지 않아서 장 마감 뒤 공시는 '다음날' 칸에 반응이 나타난다.
    """
    notes = []
    dated = [r for r in rows if str(r.get("rcept_dt", "")).isdigit() and len(str(r.get("rcept_dt"))) == 8]
    if not dated:
        return {}, notes
    newest = max(date(int(r["rcept_dt"][:4]), int(r["rcept_dt"][4:6]), int(r["rcept_dt"][6:])) for r in dated)
    limit = newest - timedelta(days=KRX_REACT_SPAN)
    use = [r for r in dated if r["rcept_dt"] >= limit.strftime("%Y%m%d") and r["rcept_dt"] >= "20100104"]
    if len(use) < len(rows):
        notes.append(f"주가 반응은 가장 최근 공시일부터 {KRX_REACT_SPAN}일 이내(2010년 이후) 공시만 계산했어요.")
    if not use:
        return {}, notes
    first = min(date(int(r["rcept_dt"][:4]), int(r["rcept_dt"][4:6]), int(r["rcept_dt"][6:])) for r in use)
    span = weekdays_between(first - timedelta(days=12), min(newest + timedelta(days=10), TODAY))
    by_day, failed = krx_days(span)
    for m in failed:
        notes.append(f"{m} 시세는 가져오지 못해 그 시장 종목은 비워뒀어요. ({failed[m]})")
    trading = sorted(d for d, rows_ in by_day.items() if rows_)  # 시세가 있는 날 = 거래일
    out = {}
    for r in use:
        code = str(r.get("stock_code") or "").strip()
        if len(code) != 6:
            continue
        d = date(int(r["rcept_dt"][:4]), int(r["rcept_dt"][4:6]), int(r["rcept_dt"][6:]))
        after = [t for t in trading if t >= d]
        if not after:
            continue
        i0 = trading.index(after[0])
        row0 = by_day[trading[i0]].get(code)
        if row0 is None:
            continue
        react = {"same_day": trading[i0] == d}
        react["day0"] = row0.rate if trading[i0] == d else None
        if i0 + 1 < len(trading):
            nxt = by_day[trading[i0 + 1]].get(code)
            react["day1"] = nxt.rate if nxt else None
        prev_close = row0.close - row0.chg if row0.close is not None and row0.chg is not None else None
        if i0 + 5 < len(trading) and prev_close:
            end = by_day[trading[i0 + 5]].get(code)
            react["day5"] = (end.close / prev_close - 1) * 100 if end and end.close else None
        if i0 >= 6 and prev_close:  # 공시 전날 종가 vs 그 5거래일 전 종가
            back = by_day[trading[i0 - 6]].get(code)
            before = by_day[trading[i0 - 1]].get(code)
            if back and back.close and before and before.close:
                react["pre5"] = (before.close / back.close - 1) * 100
        out[r["rcept_no"]] = react
    return out, notes


def reaction_columns(table, rows, react):
    """공시 표에 주가 반응 칸을 붙인다 (table과 rows는 같은 순서)."""
    cols = {"공시 전 5일": "pre5", "공시일": "day0", "다음 거래일": "day1", "5거래일 누적": "day5"}
    t = table.copy()
    for label, key in cols.items():
        t[label] = [pct_text((react.get(r.get("rcept_no")) or {}).get(key)) for r in rows]
    # 원문 링크 칸은 맨 오른쪽에 두는 것이 읽기 편하다
    if "원문" in t.columns:
        t = t[[c for c in t.columns if c != "원문"] + ["원문"]]
    return t


def render_reaction(shown, key):
    """공시 표 아래의 '주가 반응 보기' 버튼. 누르면 표에 주가 칸을 붙여 다시 보여준다. -> 반응 표 또는 None"""
    if not KRX_KEY:
        return None
    if st.button("공시 전후 주가 반응 보기", key=f"react_btn_{key}",
                 help="공시일 전후 등락률을 KRX 시세로 계산해 표에 붙여요. 처음엔 몇 초 걸려요."):
        with st.spinner("KRX 시세를 불러오는 중입니다..."):
            try:
                react, notes = price_reaction(shown)
            except KrxError as e:
                st.session_state[f"react_{key}"] = {"error": str(e)}
            else:
                st.session_state[f"react_{key}"] = {"react": react, "notes": notes, "n": len(shown),
                                                    "ids": [r.get("rcept_no") for r in shown]}
    saved = st.session_state.get(f"react_{key}")
    if not saved:
        return None
    if saved.get("error"):
        krx_error_box(saved["error"])
        return None
    if saved["ids"] != [r.get("rcept_no") for r in shown]:
        st.caption("검색·보기를 바꿔서 위 표와 달라졌어요. '공시 전후 주가 반응 보기'를 다시 눌러주세요.")
        return None
    return saved


def render_reaction_table(table, shown, saved):
    t = reaction_columns(table, shown, saved["react"])
    got = sum(1 for r in shown if r.get("rcept_no") in saved["react"])
    st.markdown("##### 공시 전후 주가 반응")
    st.caption(f"KRX 종가 기준 등락률 · 계산된 공시 {got:,}/{len(shown):,}건 · 공시 전 5일 = 공시 전날까지 5거래일 등락 · "
               "5거래일 누적 = 공시 전날 종가 대비 · 접수 시각은 알 수 없어, 장 마감 뒤 나온 공시의 반응은 '다음 거래일'에 나타나요 · "
               "시세는 다음 날 아침에 올라와 최근 공시는 비어 있을 수 있어요")
    for n in saved["notes"]:
        st.caption(n)
    st.dataframe(
        t.style.map(price_style, subset=["공시 전 5일", "공시일", "다음 거래일", "5거래일 누적"]),
        hide_index=True,
        column_config={"보고서명": st.column_config.TextColumn("보고서명", width="large"), "원문": LINK_COLUMN},
        height=min(len(t) + 1, 16) * 35 + 3,
    )


# --- 2) 기업 브리핑 시세 ---
BRIEF_RETURNS = [("1주", 7), ("1개월", 30), ("3개월", 91), ("6개월", 182), ("1년", 365)]


def collect_quote(code):
    """종목 하나의 시세 요약 -> dict. 상장사가 아니거나 시세가 없으면 None."""
    day, rows = krx_latest()
    row = rows.get(code)
    if not row or row.close is None:
        return None
    market = [row.market]
    q = {"day": day, "row": row, "market": row.market, "caps": krx_caps(rows).get(code, row.cap), "ret": {}}
    targets = [(label, day - timedelta(days=n)) for label, n in BRIEF_RETURNS] + [("연초 이후", date(day.year - 1, 12, 31))]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        got = list(pool.map(lambda t: krx_on_or_before(t[1], market), targets))
    for (label, _), (d, past) in zip(targets, got):
        p = past.get(code)
        q["ret"][label] = (row.close / p.close - 1) * 100 if p and p.close else None
    return q


def render_quote(q):
    st.markdown("##### 주가·시가총액")
    if q is None:
        st.info("KRX에서 이 종목의 시세를 찾지 못했어요. 비상장사이거나 코넥스 종목일 수 있어요.")
        return
    r = q["row"]
    cards = [
        ("종가", f"{r.close:,.0f}원", pct_text(r.rate, 2) + " (전일比)" if r.rate is not None else None,
         None if r.rate is None else ("down" if r.rate > 0 else "up" if r.rate < 0 else "flat")),  # 카드 색은 증시 관행(상승=빨강)에 맞춰 뒤집는다
        ("시가총액", won_text(q["caps"]) if q["caps"] else "-", "보통주+우선주", "flat"),
        ("거래대금", won_text(r.val) if r.val else "-", f"{r.vol:,.0f}주" if r.vol else None, "flat"),
        ("상장주식수", f"{r.shares:,.0f}주" if r.shares else "-", q["market"], "flat"),
    ]
    st.markdown(cb_cards(cards), unsafe_allow_html=True)
    ret = pd.DataFrame([{"기간": k, "수익률": pct_text(v)} for k, v in q["ret"].items()]).set_index("기간").T
    st.dataframe(ret.style.map(price_style), height=(len(ret) + 1) * 35 + 3)
    st.caption(f"KRX {q['day']:%Y.%m.%d} 종가 기준 · 기간 수익률은 그 날짜(휴장이면 직전 거래일) 종가와 비교한 값이며 배당·증자는 반영하지 않았어요")


# --- 3) 배당수익률 직접 계산 ---
def base_date(year, reprt_code):
    """사업연도·보고서 -> 배당 기준일 (사업보고서 12/31, 반기 6/30, 1분기 3/31, 3분기 9/30)."""
    md = {"11011": (12, 31), "11012": (6, 30), "11013": (3, 31), "11014": (9, 30)}.get(reprt_code, (12, 31))
    return date(year, *md)


def dividend_prices(corps, years, reprt_code):
    """-> ({(회사, 연도): 기말 종가}, {회사: 최근 종가}, 최근 거래일). 시세를 못 구하면 KrxError."""
    codes = {name: stock_code_of(cc) for name, cc in corps}
    wanted = {c for c in codes.values() if c}
    if not wanted:
        return {}, {}, None
    day, latest_rows = krx_latest()
    cur = {n: latest_rows[c].close for n, c in codes.items() if c in latest_rows and latest_rows[c].close}
    ends = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for y, (d, rows) in zip(years, pool.map(lambda y: krx_on_or_before(base_date(y, reprt_code)), years)):
            for n, c in codes.items():
                if c and c in rows and rows[c].close:
                    ends[(n, y)] = rows[c].close
    return ends, cur, day


def yield_text(dps, price):
    if dps is None or not price:
        return "-"
    return f"{dps / price * 100:.2f}%"


# --- 4) 시가총액 연결 (업종 합산·재계 순위) ---
def listed_codes_by_name():
    """{name_key(DART 회사명): 종목코드} (상장사만)"""
    idx = get_name_index()
    listed = idx[idx["stock_code"].str.len() == 6]
    return dict(zip(listed["key"], listed["stock_code"]))


def industry_caps(corps):
    """업종 합산 회사들 -> (표, 합계 시총, 기준일)."""
    day, rows = krx_latest()
    caps = krx_caps(rows)
    out = []
    for name, cc in corps:
        code = stock_code_of(cc)
        row = rows.get(code) if code else None
        out.append({"회사": name, "market": row.market if row else None, "close": row.close if row else None,
                    "rate": row.rate if row else None, "cap": caps.get(code) if row else None})
    return out, day


@st.cache_data(ttl=3600, show_spinner=False)
def chaebol_caps(day_str):
    """기업집단별 상장 계열사 시가총액 -> {"day": 기준일, "groups": {순위: {"cap": 합계(원), "firms": [(회사명, 종목코드, 종가, 등락률, 시총)]}}}"""
    data = get_chaebol()
    if not data:
        return None
    day, rows = krx_latest()
    caps = krx_caps(rows)
    by_name = listed_codes_by_name()
    out = {}
    for g in data["groups"]:
        firms, seen = [], set()
        for m in g.get("members") or []:
            code = by_name.get(name_key(m["name"]))
            if not code or code in seen or code not in rows:
                continue
            seen.add(code)
            r = rows[code]
            firms.append((rows[code].name or m["name"], code, r.close, r.rate, caps.get(code)))
        firms.sort(key=lambda f: -(f[4] or 0))
        out[int(g["rank"])] = {"cap": sum(f[4] or 0 for f in firms), "firms": firms}
    return {"day": day, "groups": out}


# --- 연결 점검 ---
def render_krx_check():
    """KRX 연결 상태를 직접 확인하는 작은 도구."""
    with st.expander("KRX 연결 점검"):
        if not KRX_KEY:
            st.write("KRX_API_KEY가 등록되지 않았어요. Streamlit Secrets에 `KRX_API_KEY = \"...\"` 한 줄을 추가해주세요.")
            return
        st.caption("시장마다 최근 거래일 시세를 한 번씩 불러와 보고 결과를 보여줘요. 승인된 API만 '정상'으로 나와요.")
        if st.button("연결 점검하기", key="krx_check_btn"):
            lines, day = [], last_weekday(TODAY - timedelta(days=1))
            for m in KRX_MARKETS:
                for back in range(6):
                    d = day - timedelta(days=back)
                    if d.weekday() >= 5:
                        continue
                    try:
                        rows = krx_market_day(d, m)
                    except KrxError as e:
                        lines.append({"시장": m, "기준일": f"{d}", "결과": "실패", "내용": str(e)})
                        break
                    if rows:
                        sample = next(iter(rows.items()))
                        lines.append({"시장": m, "기준일": f"{d}", "결과": "정상", "내용": f"{len(rows):,}종목 · 예: {sample[0]} {sample[1].name} 종가 {sample[1].close}"})
                        break
                else:
                    lines.append({"시장": m, "기준일": "-", "결과": "자료 없음", "내용": "최근 6일 동안 시세가 비어 있어요"})
            st.dataframe(pd.DataFrame(lines), hide_index=True)
            st.caption(f"인증키 전달 방식: {_krx_store()['auth']['mode']} · 저장된 시세 {len(_krx_store()['days'])}건")


# ------------------------------------------------------------
# 해외 기업 실적 (미국 SEC EDGAR 재무 데이터 + 환율 환산)
# ------------------------------------------------------------
# 실적: SEC EDGAR 'companyfacts' (회사별 XBRL 재무 항목, 무료·키 없음). 연간(10-K·20-F)과 분기(10-Q)를 읽고,
#       4분기는 한국 기업과 같은 방식으로 '연간 - 3분기 누적'으로 계산한다. 연 1회만 공시하는 외국 기업(20-F)은 연간만 나온다.
# 환율: Frankfurter(유럽중앙은행 기준환율) 일별 환율의 기간 평균·기간 말. 대만달러처럼 지원하지 않는 통화는 직접 입력.
SEC_UA = load_secret("SEC_USER_AGENT") or "JoongangIndustryDesk research app (set the SEC_USER_AGENT secret with a contact)"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{:010d}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{:010d}.json"
SEC_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{accn}-index.htm"
SEC_TIMEOUT = 45  # 회사 하나의 재무 파일이 수 MB라 DART보다 길게
SEC_TTL = 6 * 3600
SEC_FORMS = ("10-K", "10-Q", "20-F", "40-F")
MAX_OVERSEAS = 10
FX_HOSTS = (("https://api.frankfurter.dev/v1", "base", "symbols"), ("https://api.frankfurter.app", "from", "to"))

# 회사 이름 (입력칸에 한글로 써도 찾는다). 업종 후보는 아래 OVERSEAS_GROUPS.
OVERSEAS_NAMES = {
    "NVDA": "엔비디아", "AMD": "AMD", "MU": "마이크론", "INTC": "인텔", "QCOM": "퀄컴", "AVGO": "브로드컴", "TSM": "TSMC", "ASML": "ASML",
    "AAPL": "애플", "MSFT": "마이크로소프트", "GOOGL": "알파벳", "AMZN": "아마존", "META": "메타", "TSLA": "테슬라",
    "GM": "GM", "F": "포드", "TM": "도요타",
    "LMT": "록히드마틴", "RTX": "RTX", "NOC": "노스럽그러먼", "GD": "제너럴다이내믹스", "BA": "보잉",
    "XOM": "엑슨모빌", "CVX": "셰브런", "COP": "코노코필립스", "MPC": "마라톤페트롤리엄", "VLO": "발레로에너지",
    "NUE": "뉴코", "STLD": "스틸다이내믹스", "MT": "아르셀로미탈",
    "VZ": "버라이즌", "T": "AT&T", "TMUS": "T모바일",
    "LLY": "일라이릴리", "JNJ": "존슨앤드존슨", "PFE": "화이자", "MRK": "머크", "ABBV": "애브비",
}
OVERSEAS_GROUPS = {
    "반도체": ["NVDA", "AMD", "MU", "INTC", "QCOM", "AVGO", "TSM", "ASML"],
    "빅테크": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
    "자동차": ["TSLA", "GM", "F", "TM"],
    "방산·항공우주": ["LMT", "RTX", "NOC", "GD", "BA"],
    "에너지·정유": ["XOM", "CVX", "COP", "MPC", "VLO"],
    "철강": ["NUE", "STLD", "MT"],
    "통신": ["VZ", "T", "TMUS"],
    "제약·바이오": ["LLY", "JNJ", "PFE", "MRK", "ABBV"],
}
KOREAN_TO_TICKER = {re.sub(r"\s+", "", v).lower(): k for k, v in OVERSEAS_NAMES.items()}
CURRENCY_NAMES = {"USD": "달러", "EUR": "유로", "JPY": "엔", "GBP": "파운드", "CNY": "위안", "TWD": "대만달러", "CHF": "프랑",
                  "CAD": "캐나다달러", "HKD": "홍콩달러", "AUD": "호주달러", "KRW": "원"}
SEC_CONCEPTS = {
    "us-gaap": {
        "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueNet", "SalesRevenueGoodsNet"],
        "op": ["OperatingIncomeLoss"],
        "net": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
        "gp": ["GrossProfit"],
        "eps": ["EarningsPerShareDiluted"],
        "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
        "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
        "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"],
    },
    "ifrs-full": {
        "rev": ["Revenue", "RevenueFromContractsWithCustomers"],
        "op": ["ProfitLossFromOperatingActivities"],
        "net": ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
        "gp": ["GrossProfit"],
        "eps": ["DilutedEarningsLossPerShare"],
        "ocf": ["CashFlowsFromUsedInOperatingActivities"],
        "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "PurchaseOfPropertyPlantAndEquipment"],
        "da": ["DepreciationAndAmortisationExpense", "AdjustmentsForDepreciationAndAmortisationExpense"],
    },
}
OVS_PERIODS = ["연간", "1Q", "2Q", "3Q", "4Q"]


class SecError(Exception):
    """SEC 접속 차단·서버 오류 등 사용자에게 알려야 하는 조회 실패."""


@st.cache_resource
def _sec_store():
    return {"lock": threading.Lock(), "tickers": None, "facts": {}, "fx": {}}


def sec_get(url):
    """SEC 한 번 호출 -> JSON(dict). 없으면 None. SEC는 연락처가 담긴 User-Agent를 요구한다."""
    res, last = None, None
    for attempt in range(3):
        try:
            res = get_session().get(url, headers={"User-Agent": SEC_UA, "Accept": "application/json"}, timeout=SEC_TIMEOUT)
            break
        except Exception as e:
            last, res = e, None
            time.sleep(0.7 * (attempt + 1))
    if res is None:
        raise SecError("SEC 서버 응답이 없거나 늦어요. 잠시 후 다시 시도해주세요.") from last
    if res.status_code == 404:
        return None
    if res.status_code in (403, 429):
        raise SecError("SEC가 요청을 막았어요. Streamlit Secrets에 SEC_USER_AGENT = \"회사명 담당자이메일\" 형식으로 연락처를 등록한 뒤 "
                       f"다시 시도해주세요. (SEC는 연락처 없는 요청을 막아요 · 응답 {res.status_code})")
    if res.status_code != 200:
        raise SecError(f"SEC 응답 오류 [{res.status_code}]")
    try:
        return res.json()
    except Exception as e:
        raise SecError("SEC 응답을 읽지 못했어요.") from e


def sec_tickers():
    """[(티커, CIK, 회사명)] 전체 목록 (하루 한 번만 받는다)."""
    s = _sec_store()
    with s["lock"]:
        hit = s["tickers"]
    if hit and time.time() - hit[0] < 24 * 3600:
        return hit[1]
    data = sec_get(SEC_TICKERS_URL)
    rows = []
    if isinstance(data, dict):
        for v in data.values():
            try:
                rows.append((str(v["ticker"]).upper(), int(v["cik_str"]), str(v["title"])))
            except Exception:
                continue
    if not rows:
        raise SecError("SEC 회사 목록을 읽지 못했어요.")
    with s["lock"]:
        s["tickers"] = (time.time(), rows)
    return rows


def sec_resolve(text):
    """입력(티커·영문 회사명·한글 이름) -> ((티커, CIK, 회사명) 또는 None, 비슷한 후보 목록, 이름으로 찾았는지)"""
    t = text.strip()
    if not t:
        return None, [], False
    rows = sec_tickers()
    by = {r[0]: r for r in rows}
    cand = KOREAN_TO_TICKER.get(re.sub(r"\s+", "", t).lower()) or t.upper().replace(".", "-")
    if cand in by:
        return by[cand], [], False
    words = t.lower().split()
    hits = sorted((r for r in rows if all(w in r[2].lower() for w in words)), key=lambda r: len(r[2]))
    if hits:
        return hits[0], hits[1:4], True
    return None, [], False


def sec_unit(units, cur):
    if cur and cur in units:
        return cur
    if "USD" in units:
        return "USD"
    keys = [k for k in units if "/" not in k and k.lower() not in ("shares", "pure")]
    return max(keys, key=lambda k: len(units[k])) if keys else None


def sec_pershare_unit(units, cur):
    keys = [k for k in units if "/" in k]
    for k in keys:
        if cur and k.startswith(cur + "/"):
            return k
    return keys[0] if keys else None


def sec_parse(data):
    """companyfacts -> {"name", "cur", "annual": [...], "quarter": [...]}.
    기간마다 start·end·rev(매출)·op(영업이익)·net(순이익)·gp(매출총이익)·eps(희석 주당순이익)·ocf(영업현금흐름)·capex(설비투자)·da(감가상각)·accn(공시 번호)."""
    facts = (data or {}).get("facts") or {}
    tax = next((t for t in ("us-gaap", "ifrs-full")
                if any(c in (facts.get(t) or {}) for c in SEC_CONCEPTS[t]["rev"] + SEC_CONCEPTS[t]["net"])), None)
    if not tax:
        return None
    f, cur, series, accn_of = facts[tax], None, {}, {}
    for metric in ("rev", "op", "net", "gp", "eps", "ocf", "capex", "da"):
        got = {}
        for concept in SEC_CONCEPTS[tax].get(metric, []):  # 앞에 있는 항목이 우선. 회사가 항목 이름을 바꿨으면 빈 기간만 뒤 항목으로 채움
            units = (f.get(concept) or {}).get("units") or {}
            u = sec_pershare_unit(units, cur) if metric == "eps" else sec_unit(units, cur)
            if not u:
                continue
            if metric != "eps":
                cur = cur or u
            best = {}
            for e in units[u]:
                if "start" not in e or not str(e.get("form", "")).startswith(SEC_FORMS):
                    continue
                try:
                    k = (date.fromisoformat(e["start"]), date.fromisoformat(e["end"]))
                    v = float(e["val"])
                except (KeyError, ValueError, TypeError):
                    continue
                filed = str(e.get("filed", ""))
                if k not in best or filed >= best[k][1]:  # 같은 기간이 여러 번 나오면 가장 나중(정정) 값
                    best[k] = (v, filed, str(e.get("accn", "")))
            for k, (v, _, ac) in best.items():
                got.setdefault(k, v)
                if metric in ("rev", "net") and ac:
                    accn_of.setdefault(k, ac)
        series[metric] = got
    inc = ("rev", "op", "net", "gp", "eps")  # 손익계산서 항목 (분기 값이 따로 공시됨)
    ann, qtr, ytd9 = {}, {}, {}
    for k in set().union(*(series[m].keys() for m in inc)):
        days = (k[1] - k[0]).days
        rec = {m: series[m].get(k) for m in inc}
        if 350 <= days <= 380:
            ann[k] = rec
        elif 80 <= days <= 100:
            qtr[k] = rec
        elif 255 <= days <= 290:
            ytd9[k] = rec
    derived, derived_accn = set(), {}
    for (s0, e0), rec in ann.items():  # 4분기 = 연간 - 3분기 누적 (직접 공시한 4분기가 없을 때만)
        if any(abs((qe - e0).days) <= 10 for _, qe in qtr):
            continue
        for (ys, ye), y in ytd9.items():
            if abs((ys - s0).days) <= 5 and 70 <= (e0 - ye).days <= 110:
                # 주당순이익은 주식 수가 분기마다 달라 빼서 구할 수 없으니 비운다
                q4 = {m: (None if m == "eps" else ((rec[m] - y[m]) if rec[m] is not None and y[m] is not None else None)) for m in rec}
                if any(v is not None for v in q4.values()):
                    key = (ye + timedelta(days=1), e0)
                    qtr[key] = q4
                    derived.add(key)
                    derived_accn[key] = accn_of.get((s0, e0), "")
                break
    cum = {m: series[m] for m in ("ocf", "capex", "da")}

    def flow(m, ps, pe, annual):
        """현금흐름 항목은 누적(3·6·9·12개월)으로만 공시돼서, 분기 값은 누적에서 앞 누적을 빼서 구한다."""
        c = cum[m]
        if annual:
            return next((v for (s0, e0), v in c.items() if abs((s0 - ps).days) <= 5 and abs((e0 - pe).days) <= 10), None)
        for (s0, e0), v in c.items():
            dur = (e0 - s0).days
            if abs((e0 - pe).days) > 10 or dur < 80 or dur > 380:
                continue
            n = round(dur / 91.3)
            if abs((ps - s0).days - (n - 1) * 91.3) > 20:
                continue
            if n == 1:
                return v
            for (s1, e1), v1 in c.items():
                if abs((s1 - s0).days) <= 3 and abs((e1 - ps).days) <= 12 and (e1 - s1).days < dur:
                    return v - v1
        return None

    def mk(d, annual):
        out = []
        for k, v in d.items():
            rec = {"start": k[0], "end": k[1], "derived": k in derived, **v, "accn": accn_of.get(k) or derived_accn.get(k, "")}
            for m in cum:
                rec[m] = flow(m, k[0], k[1], annual)
            out.append(rec)
        return sorted(out, key=lambda p: p["end"])

    return {"name": data.get("entityName"), "cur": cur, "annual": mk(ann, True), "quarter": mk(qtr, False)}


def sec_release_link(cik, end):
    """결산 종료일 뒤 90일 안에 낸 실적 발표 8-K(항목 2.02)의 공시 목록 주소. 못 찾으면 None. (실적 발표 자료는 이 공시에 첨부돼요)"""
    s = _sec_store()
    with s["lock"]:
        hit = s["facts"].get(("filings", cik))
    if not hit or time.time() - hit[0] > SEC_TTL:
        data = sec_get(SEC_SUBMISSIONS_URL.format(cik))
        rec = ((data or {}).get("filings") or {}).get("recent") or {}
        rows = list(zip(rec.get("form", []), rec.get("filingDate", []), rec.get("items", []), rec.get("accessionNumber", [])))
        with s["lock"]:
            s["facts"][("filings", cik)] = (time.time(), rows)
        hit = (0, rows)
    cands = []
    for form, filed, items, accn in hit[1]:
        try:
            d = date.fromisoformat(filed)
        except ValueError:
            continue
        if form == "8-K" and "2.02" in str(items).split(",") and end < d <= end + timedelta(days=90):
            cands.append((d, accn))
    if not cands:
        return None
    return sec_index_url(cik, min(cands)[1])


def sec_index_url(cik, accn):
    return SEC_INDEX_URL.format(cik=cik, nodash=accn.replace("-", ""), accn=accn)


SEC_PARSE_VER = 3  # 파싱 항목이 바뀌면 올린다 (서버에 남은 옛 저장분을 쓰지 않도록)


def sec_company(cik):
    """회사 하나의 파싱된 재무 (6시간 저장). 자료가 없으면 SecError."""
    s = _sec_store()
    ck = (cik, SEC_PARSE_VER)
    with s["lock"]:
        hit = s["facts"].get(ck)
    if hit and time.time() - hit[0] < SEC_TTL:
        return hit[1]
    co = sec_parse(sec_get(SEC_FACTS_URL.format(cik)))
    if not co or not co["cur"]:
        raise SecError("EDGAR에 매출·이익 자료 없음 (미국에 재무 보고서를 내지 않는 회사일 수 있어요)")
    with s["lock"]:
        s["facts"][ck] = (time.time(), co)
    return co


def approx_end(d):
    """52·53주 결산처럼 월 초에 끝나는 날은 전달 말로 본다 (1월 2일 종료 -> 12월 31일)."""
    return d if d.day > 15 else d - timedelta(days=d.day)


def sec_pick(co, year, period):
    """달력 기준 연도·기간에 가장 가까운 결산 기간 -> (현재, 전년 같은 기간). 없으면 (None, None).
    연간 = 회계연도 종료일이 그 해에 있는 것. 분기 = 종료일이 달력 분기 말(3/6/9/12월 말)에 46일 이내로 가까운 것."""
    if period == "연간":
        cands = [p for p in co["annual"] if approx_end(p["end"]).year == year]
        cur = max(cands, key=lambda p: p["end"]) if cands else None
        pool = co["annual"]
    else:
        q = int(period[0])
        target = date(year, q * 3, 31 if q in (1, 4) else 30)
        cands = [(abs((approx_end(p["end"]) - target).days), p) for p in co["quarter"]]
        cands = [c for c in cands if c[0] <= 46]
        cur = min(cands, key=lambda c: c[0])[1] if cands else None
        pool = co["quarter"]
    if not cur:
        return None, None
    want = approx_end(cur["end"]) - timedelta(days=365)
    prevs = [(abs((approx_end(p["end"]) - want).days), p) for p in pool if p is not cur]
    prevs = [c for c in prevs if c[0] <= 25]
    return cur, (min(prevs, key=lambda c: c[0])[1] if prevs else None)


def fx_range(cur, start, end):
    """통화 1단위의 원화 환율 {"avg": 기간 평균, "last": 기간 말, "last_date"}. 지원하지 않거나 못 받으면 None."""
    if cur == "KRW":
        return {"avg": 1.0, "last": 1.0, "last_date": end}
    s = _sec_store()
    key = (cur, start, end)
    with s["lock"]:
        if key in s["fx"]:
            return s["fx"][key]
    out = None
    for host, p_from, p_to in FX_HOSTS:
        try:
            res = get_session().get(f"{host}/{start:%Y-%m-%d}..{end:%Y-%m-%d}", params={p_from: cur, p_to: "KRW"}, timeout=REQUEST_TIMEOUT)
        except Exception:
            continue
        if res.status_code in (404, 422):
            break  # 지원하지 않는 통화 (예: 대만달러)
        if res.status_code != 200:
            continue
        try:
            rates = sorted((d, v["KRW"]) for d, v in (res.json().get("rates") or {}).items() if "KRW" in v)
        except Exception:
            continue
        if rates:
            out = {"avg": sum(v for _, v in rates) / len(rates), "last": rates[-1][1], "last_date": date.fromisoformat(rates[-1][0])}
            break
    else:
        return out  # 서버에 닿지 않은 경우는 저장하지 않는다 (다음에 다시 시도)
    with s["lock"]:
        s["fx"][key] = out
    return out


def collect_overseas(items, year, period):
    """items: [(티커, CIK, 회사명)] -> {티커: {"co", "now", "prev", "fx", "error"}}. 한 회사가 실패해도 나머지는 담는다."""
    def one(item):
        ticker, cik, title = item
        try:
            co = sec_company(cik)
        except SecError as e:
            return {"error": str(e)}
        now, prev = sec_pick(co, year, period)
        r = {"co": co, "now": now, "prev": prev, "fx": None, "error": None}
        if now is None:
            have = sorted({approx_end(p["end"]).year for p in co["annual"]})
            last_q = max((p["end"] for p in co["quarter"]), default=None)
            found = (f" (EDGAR에 있는 자료: 연간 {have[0]}~{have[-1]}년" + (f", 분기는 {last_q:%Y-%m-%d} 종료분까지" if last_q else "") + ")") if have else ""
            r["error"] = ("분기 자료 없음 - 연 1회만 공시하는 기업" + found if period != "연간" and not co["quarter"]
                          else "해당 기간 자료 없음 - 아직 공시 전이거나 EDGAR에 반영 전일 수 있어요" + found)
        else:
            r["fx"] = fx_range(co["cur"], now["start"], now["end"])
            try:
                r["link"] = sec_release_link(cik, now["end"])
            except SecError:
                r["link"] = None
            if not r["link"] and now.get("accn"):  # 실적 발표 8-K를 못 찾으면 분기·연간 보고서 공시로
                r["link"] = sec_index_url(cik, now["accn"])
        return r

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return {it[0]: r for it, r in zip(items, pool.map(one, items))}


def ovs_rate(entry, mode, override):
    """표시에 쓸 환율 (원/통화 1단위). 직접 입력이 있으면 그것, 없으면 자동값, 둘 다 없으면 None."""
    if override and override > 0:
        return override, "직접 입력"
    fx = entry.get("fx")
    if fx:
        return fx["avg" if mode == "기간 평균" else "last"], "자동"
    return None, ""


def ovs_table(res, view, unit, mode, overrides):
    """조회 결과 -> (표, 환율 표, 환율이 없어 원화로 못 바꾼 회사)"""
    rows, rate_rows, no_rate = [], [], []
    krw = view == "원화 환산"
    for ticker, entry in res["data"].items():
        label = f"{res['names'][ticker]} ({ticker})"
        now, prev, co = entry.get("now"), entry.get("prev"), entry.get("co")
        if not now:
            continue
        cur = co["cur"]
        rate, how = ovs_rate(entry, mode, overrides.get(cur))
        if krw and rate is None:
            no_rate.append(label)
        if rate is not None and cur != "KRW":
            rate_rows.append({"통화": f"{cur} ({CURRENCY_NAMES.get(cur, cur)})", "적용 환율(원)": f"{rate:,.2f}", "구분": how,
                              "기준": (f"{now['start']:%Y.%m.%d}~{now['end']:%Y.%m.%d} 평균" if mode == "기간 평균" and how == "자동"
                                     else (f"{entry['fx']['last_date']:%Y.%m.%d} 종가" if how == "자동" else "직접 입력")),
                              "회사": label})

        def show(v):
            if v is None:
                return "-"
            if krw:
                return "-" if rate is None else fmt_amount(v * rate, unit)
            return f"{round(v / 1e8) + 0.0:,.0f}"

        pv = prev or {}
        margin = now["op"] / now["rev"] * 100 if now["op"] is not None and now["rev"] else None
        period_note = "4분기 계산" if now.get("derived") else ""
        rows.append({
            "회사": label, "결산 종료일": f"{now['end']:%Y-%m-%d}" + (f" ({period_note})" if period_note else ""),
            "매출액": show(now["rev"]), "매출 전년比": fmt_change(pct_change(now["rev"], pv.get("rev"))),
            "영업이익": show(now["op"]), "영업이익 전년比": fmt_change(op_change(now["op"], pv.get("op"))),
            "영업이익률": f"{margin:.1f}%" if margin is not None else "-",
            "순이익": show(now["net"]), "순이익 전년比": fmt_change(op_change(now["net"], pv.get("net"))),
            "원문": entry.get("link"),
        })
    table = pd.DataFrame(rows)
    if not table.empty:  # 결산 종료일, 원문은 맨 뒤로
        table = table[[c for c in table.columns if c not in ("결산 종료일", "원문")] + ["결산 종료일", "원문"]]
    return table, pd.DataFrame(rate_rows), no_rate


def ovs_ebitda(p):
    """영업이익 + 감가상각비 (회사가 발표하는 조정 EBITDA와 다를 수 있다)"""
    return p["op"] + p["da"] if p and p.get("op") is not None and p.get("da") is not None else None


def ovs_fcf(p):
    """잉여현금흐름 = 영업현금흐름 - 설비투자"""
    return p["ocf"] - p["capex"] if p and p.get("ocf") is not None and p.get("capex") is not None else None


def ovs_extra_table(res, view, unit, mode, overrides):
    """'지표 더 보기' 표: 주당순이익(희석), 매출총이익률, EBITDA, 영업현금흐름, 잉여현금흐름."""
    rows = []
    krw = view == "원화 환산"
    for ticker, entry in res["data"].items():
        now, prev, co = entry.get("now"), entry.get("prev"), entry.get("co")
        if not now:
            continue
        rate, _ = ovs_rate(entry, mode, overrides.get(co["cur"]))

        def amt(v):
            if v is None:
                return "-"
            if krw:
                return "-" if rate is None else fmt_amount(v * rate, unit)
            return f"{round(v / 1e8) + 0.0:,.0f}"

        def eps_text(v):
            if v is None:
                return "-"
            if krw:
                return "-" if rate is None else f"{v * rate:,.0f}원"
            return f"{v:,.2f}"

        pv = prev or {}
        gm = now["gp"] / now["rev"] * 100 if now.get("gp") is not None and now.get("rev") else None
        rows.append({
            "회사": f"{res['names'][ticker]} ({ticker})",
            "주당순이익(희석)": eps_text(now.get("eps")), "주당순이익 전년比": fmt_change(op_change(now.get("eps"), pv.get("eps"))),
            "매출총이익률": f"{gm:.1f}%" if gm is not None else "-",
            "EBITDA": amt(ovs_ebitda(now)), "EBITDA 전년比": fmt_change(op_change(ovs_ebitda(now), ovs_ebitda(pv))),
            "영업현금흐름": amt(now.get("ocf")), "잉여현금흐름": amt(ovs_fcf(now)),
        })
    return pd.DataFrame(rows)


def ovs_missing_line(res):
    """조회되지 않은 기업 이름만 나열한 한 줄. 없으면 ''."""
    names = [n for n, _ in res.get("missing", [])]
    names += [f"{res['names'].get(t, t)}({t})" for t, e in res["data"].items() if e.get("error")]
    return "조회되지 않은 기업 — " + ", ".join(names) if names else ""


def fill_overseas():
    group = st.session_state.get("ovs_group")
    if group in OVERSEAS_GROUPS:
        st.session_state["ovs_corps"] = ", ".join(OVERSEAS_GROUPS[group])


def latest_overseas_period(today=None):
    """지금 공시가 나와 있을 가능성이 큰 가장 최근 달력 분기 (분기 말 + 40일 이후) -> (연도, '1Q'~'4Q')"""
    t = today or TODAY
    for y in (t.year, t.year - 1):
        for q in (4, 3, 2, 1):
            end = date(y, q * 3, 31 if q in (1, 4) else 30)
            if end + timedelta(days=40) <= t:
                return y, f"{q}Q"
    return t.year - 1, "4Q"


def render_overseas():
    """탭: 해외 기업 - 미국 SEC 공시로 해외 기업 실적을 보고, 환율로 원화 환산."""
    groups = list(OVERSEAS_GROUPS) + ["직접 입력"]
    st.session_state.setdefault("ovs_corps", ", ".join(OVERSEAS_GROUPS[groups[0]]))
    st.selectbox("업종", groups, key="ovs_group", on_change=fill_overseas)
    ly, lp = latest_overseas_period()
    years = list(range(TODAY.year, 2014, -1))
    with st.form("ovs_search"):
        corp_text = st.text_input(
            "티커 또는 회사 이름 (쉼표로 구분)", key="ovs_corps", placeholder="예: NVDA, 마이크론, Intel",
            help=f"티커(NVDA), 영문 이름, 주요 기업은 한글 이름으로도 찾아요. 최대 {MAX_OVERSEAS}곳.",
        )
        c1, c2 = st.columns(2)
        year = c1.selectbox("연도 (달력 기준)", years, index=years.index(ly), key="ovs_year")
        period = c2.radio("기간", OVS_PERIODS, index=OVS_PERIODS.index(lp), horizontal=True, key="ovs_period",
                          help="분기는 달력 분기에 가장 가까운 회사별 결산 분기를 골라요. 연간은 회계연도 종료일이 그 해에 있는 해예요.")
        submitted = st.form_submit_button("해외 기업 조회")
    st.caption("미국 SEC에 공시하는 기업의 실적이에요 (연결 기준, 각사 결산월 기준). 연 1회만 공시하는 외국 기업(TSMC·도요타 등)은 연간만 나와요.")

    if submitted:
        names, items, notes, missing = [], [], [], []
        for part in re.split(NEWS_SPLIT, corp_text):
            if part.strip() and part.strip().lower() not in [n.lower() for n in names]:
                names.append(part.strip())
        if not names:
            st.error("티커나 회사 이름을 입력해주세요.")
        elif len(names) > MAX_OVERSEAS:
            st.error(f"기업은 한 번에 {MAX_OVERSEAS}곳까지 넣을 수 있어요. (지금 {len(names)}곳)")
        else:
            st.session_state.pop("overseas", None)
            try:
                with st.spinner("회사를 확인하는 중입니다..."):
                    for n in names:
                        hit, alts, by_name = sec_resolve(n)
                        if not hit:
                            missing.append((n, "SEC 목록에 없음 - 티커(예: NVDA)로 입력해보세요"))
                        elif hit[0] not in [i[0] for i in items]:
                            items.append(hit)
                            if by_name:
                                alt = ", ".join(f"{a[2]}({a[0]})" for a in alts)
                                notes.append(f'"{n}" → {hit[2]}({hit[0]})로 찾았어요.' + (f" 다른 후보: {alt}" if alt else ""))
                data = {}
                if items:
                    with st.spinner("SEC 공시와 환율을 불러오는 중입니다... (회사당 몇 초 걸려요)"):
                        data = collect_overseas(items, int(year), period)
                if items or missing:
                    st.session_state["overseas"] = {
                        "data": data, "year": int(year), "period": period, "notes": notes, "missing": missing,
                        "names": {t: OVERSEAS_NAMES.get(t) or ((data[t]["co"]["name"] if data[t].get("co") else None) or title).title()
                                  for t, _, title in items},
                    }
            except SecError as e:
                st.error(str(e))

    res = st.session_state.get("overseas")
    if not res:
        return
    for n in res["notes"]:
        st.caption(n)
    if not any(e.get("now") for e in res["data"].values()):
        st.caption(ovs_missing_line(res) or "표시할 실적이 없어요.")
        return

    t1, t2 = st.columns(2)
    krw_on = t1.toggle("원화로 환산", key="ovs_krw", help="켜면 환율로 원화 환산해 보여주고, 단위와 환율 기준을 고를 수 있어요.")
    more_on = t2.toggle("지표 더 보기", key="ovs_more", help="주당순이익, 매출총이익률, EBITDA, 영업현금흐름, 잉여현금흐름을 표 아래에 더 보여줘요.")
    view = "원화 환산" if krw_on else "현지 통화"
    unit, mode = list(UNITS)[1], "기간 평균"
    if krw_on:
        c1, c2 = st.columns(2)
        unit = c1.radio("단위", list(UNITS), index=list(UNITS).index("조원"), horizontal=True, key="ovs_unit")
        mode = c2.radio("환율 기준", ["기간 평균", "기간 말"], horizontal=True, key="ovs_fxmode",
                        help="손익은 한 기간 동안 번 돈이라 보통 기간 평균 환율로 바꿔요. 기간 말은 결산일 환율이에요.")
    currencies = sorted({e["co"]["cur"] for e in res["data"].values() if e.get("now")} - {"KRW"})
    overrides = {}
    body = st.container()  # 표 영역. 적용 환율 칸이 표·안내 줄 바로 밑에 오도록 자리를 먼저 잡는다
    fx_box = st.expander("적용 환율 보기·직접 입력") if krw_on else None
    if fx_box is not None:
        with fx_box:
            st.caption("자동 환율은 유럽중앙은행 기준환율(Frankfurter)로 계산한 원화 환율이에요. 한국은행 매매기준율과 조금 다를 수 있어요. "
                       "기사에 쓸 환율이 따로 있으면 아래에 입력하면 그 값으로 바꿔요 (0이면 자동).")
            cols = st.columns(max(len(currencies), 1))
            for col, cur in zip(cols, currencies):
                overrides[cur] = col.number_input(f"{cur} 1{CURRENCY_NAMES.get(cur, '')} = ? 원", min_value=0.0, value=0.0, step=1.0,
                                                  key=f"ovs_fx_{cur}")
    table, rates, no_rate = ovs_table(res, view, unit, mode, overrides)
    when = f"{res['year']}년 " + ("연간" if res["period"] == "연간" else f"{res['period'][0]}분기")
    used = sorted({e["co"]["cur"] for e in res["data"].values() if e.get("now")})
    if krw_on:
        unit_label = f"{unit} · 환율 {mode}"
    else:
        # 외국 기업은 USD가 아니라 보고 통화(대만달러·엔·유로 등) 그대로 나오므로, 다른 통화인 회사는 따로 적는다
        cur_of = {t: e["co"]["cur"] for t, e in res["data"].items() if e.get("now")}
        main = max(set(cur_of.values()), key=lambda c: (list(cur_of.values()).count(c), c == "USD"))
        unit_label = f"억 {CURRENCY_NAMES.get(main, main)}"
        other = [f"{res['names'][t]}는 억 {CURRENCY_NAMES.get(c, c)}" for t, c in cur_of.items() if c != main]
        if other:
            unit_label += " (" + ", ".join(other) + ")"
    with body:
        st.markdown(f'<div style="font-weight:800; font-size:0.875rem; color:#1c7ed6; margin:0.3rem 0 0.1rem">단위: {html.escape(unit_label)}</div>',
                    unsafe_allow_html=True)
        st.caption(f"{when} (달력 기준에 가장 가까운 결산 기간) · 연결 기준 · 증감률은 현지 통화 기준이라 환율 영향이 없어요")
        if no_rate:
            st.warning("환율을 자동으로 구하지 못해 원화로 바꾸지 못한 회사: " + ", ".join(no_rate) + ". 아래 '적용 환율 보기·직접 입력'을 열어 환율을 넣어주세요.")
        st.dataframe(
            table.style.map(amount_style, subset=["매출액", "영업이익", "순이익"]).apply(total_row_style, axis=1),
            hide_index=True, height=(len(table) + 1) * 35 + 3,
            column_config={"회사": st.column_config.TextColumn("회사", width=150), "결산 종료일": st.column_config.TextColumn("결산 종료일", width=130),
                           "원문": st.column_config.LinkColumn("원문", display_text="실적 발표")},
        )
        miss = ovs_missing_line(res)
        if miss:
            st.caption(miss)
    if fx_box is not None and not rates.empty:
        with fx_box:
            st.markdown("**적용 환율**")
            st.dataframe(rates, hide_index=True, height=(len(rates) + 1) * 35 + 3)
    if more_on:
        extra = ovs_extra_table(res, view, unit, mode, overrides)
        st.markdown("##### 추가 지표", help=(
            "EBITDA = 영업이익 + 감가상각비 (회사가 발표하는 조정 EBITDA와 다를 수 있어요)\n\n"
            "잉여현금흐름 = 영업현금흐름 - 설비투자\n\n"
            "주당순이익은 1주당 금액(" + ("원화 환산 시 원" if krw_on else "현지 통화") + ")이고, 4분기 계산 기간은 비워요\n\n"
            "조정(Non-GAAP) 실적과 가이던스는 SEC 데이터에 없어요. 첫 표의 '원문'(실적 발표 8-K)에서 확인하세요"))
        st.dataframe(extra.style.map(amount_style, subset=["EBITDA", "영업현금흐름", "잉여현금흐름"]), hide_index=True,
                     height=(len(extra) + 1) * 35 + 3, column_config={"회사": st.column_config.TextColumn("회사", width=150)})
    st.download_button("CSV 다운로드", table.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"해외기업_{res['year']}_{res['period']}.csv", mime="text/csv", key="ovs_csv")
    st.caption("자료: 미국 SEC EDGAR(XBRL), 환율: 유럽중앙은행 기준환율(Frankfurter) · 4분기는 '연간 - 3분기 누적'으로 계산한 값이에요 · "
               "'원문'은 실적 발표 8-K 공시로 연결하고, 찾지 못하면 분기·연간 보고서 공시로 연결해요")


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

/* 탭: 모바일은 한 줄에 4개씩 2줄 바둑판으로 (옆으로 밀지 않아도 모두 보이게) */
[data-testid="stTabs"] [role="tablist"] {
    display: grid !important; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.35rem;
    overflow: visible !important; width: 100% !important; border-bottom: none !important; margin-bottom: 0.6rem;
}
[data-testid="stTab"] {
    display: flex; align-items: center; justify-content: center; min-width: 0; margin: 0 !important;
    padding: 0.55rem 0.2rem !important; border: 1px solid #dde3ec; border-radius: 10px; background: #ffffff; cursor: pointer;
}
[data-testid="stTab"] p { font-size: 0.88rem; font-weight: 600; white-space: nowrap; margin: 0; color: #334155; }
[data-testid="stTab"]:hover { border-color: #1c7ed6; }
[data-testid="stTab"][aria-selected="true"] { background: #1c7ed6; border-color: #1c7ed6; }
[data-testid="stTab"][aria-selected="true"] p { color: #ffffff !important; }
[data-testid="stTab"] > div:not([data-testid]) { display: none; }  /* 기본 밑줄 표시는 숨김 */
[data-testid="stTabs"] [role="tablist"]::before, [data-testid="stTabs"] [role="tablist"]::after { display: none !important; }
[data-testid="stTabs"] [role="tablist"] { box-shadow: none !important; background: none !important; }
@media (min-width: 900px) { [data-testid="stTabs"] [role="tablist"] { grid-template-columns: repeat(8, minmax(0, 1fr)); } }  /* PC: 8개 한 줄 (모바일은 4x2) */
@media (max-width: 400px) { [data-testid="stTab"] p { font-size: 0.8rem; } }  /* 작은 휴대폰에서도 글자가 칸 안에 */
[data-testid="stTabs"] [role="tablist"] ~ button, [data-testid="stTabs"] button[aria-label*="croll"] { display: none !important; }

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

/* 재계 순위: 숫자 카드 (모바일은 2칸씩, 컴퓨터는 한 줄) */
.cb-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.5rem; margin: 0.4rem 0 0.9rem; }
@media (min-width: 700px) { .cb-stats.n4 { grid-template-columns: repeat(4, minmax(0, 1fr)); } .cb-stats.n3 { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.cb-card { background: #ffffff; border: 1px solid #e3e8ef; border-radius: 12px; padding: 0.6rem 0.8rem; }
.cb-card .l { color: #7a8699; font-size: 0.78rem; }
.cb-card .v { color: #0b2545; font-weight: 800; font-size: 1.25rem; line-height: 1.35; letter-spacing: -0.02em; word-break: keep-all; }
.cb-card .d { font-size: 0.78rem; font-weight: 600; }
.cb-card .d.up { color: #1c7ed6; } .cb-card .d.down { color: #e03131; } .cb-card .d.flat { color: #7a8699; }

.cb-own-scroll { overflow-x: auto; border: 1px solid #e3e8ef; border-radius: 12px; background: #ffffff; }
.cb-own-scroll img { width: 1400px; max-width: none; display: block; }

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
st.caption("credit/중앙일보 나상현")

if not API_KEY:
    st.error(
        "DART API 키가 설정되지 않았습니다. "
        "Streamlit secrets(DART_API_KEY) 또는 환경변수 DART_API_KEY에 키를 등록해주세요."
    )
    st.stop()

# ------------------------------------------------------------
# 정기보고서 표 조회: 주주·지분 / 임직원의 임원 현황 (AI 없이 DART 표를 그대로 정리)
# ------------------------------------------------------------
RT_REPORTS = {"사업보고서": "11011", "반기보고서": "11012", "1분기보고서": "11013", "3분기보고서": "11014"}
MAX_RT_CORPS = 5
RT_TREND_YEARS = 5  # 연도별 추이: 고른 연도까지 최근 5년
RT_META = {"rcept_no", "corp_cls", "corp_code", "corp_name", "stlm_dt"}

# 항목: key -> dict(title, api, desc, cols[(필드, 열 이름, 종류 t=글 i=숫자)])
RT_SECTIONS = {
    "hyslr": dict(title="최대주주 현황", api="hyslrSttus", desc="최대주주와 특수관계인의 주식 수·지분율 (기초·기말)",
                  cols=[("nm", "성명", "t"), ("relate", "관계", "t"), ("stock_knd", "주식 종류", "t"),
                        ("bsis_posesn_stock_co", "기초 주식 수", "i"), ("bsis_posesn_stock_qota_rt", "기초 지분율(%)", "t"),
                        ("trmend_posesn_stock_co", "기말 주식 수", "i"), ("trmend_posesn_stock_qota_rt", "기말 지분율(%)", "t"), ("rm", "비고", "t")]),
    "mrhl": dict(title="소액주주 현황", api="mrhlSttus", desc="소액주주 수·지분율 (1% 미만 주주)",
                 cols=[("se", "구분", "t"), ("shrholdr_co", "소액주주 수", "i"), ("shrholdr_tot_co", "전체 주주 수", "i"),
                       ("shrholdr_rate", "주주 비율(%)", "t"), ("hold_stock_co", "소액주주 보유 주식", "i"), ("stock_tot_co", "총 발행 주식", "i"),
                       ("hold_stock_rate", "보유 비율(%)", "t")]),
    "stock": dict(title="주식 총수 현황", api="stockTotqySttus", desc="발행할 주식 총수, 발행주식 수, 자기주식, 유통주식 수",
                  cols=[("se", "구분", "t"), ("isu_stock_totqy", "발행할 주식 총수", "i"), ("now_to_isu_stock_totqy", "현재까지 발행", "i"),
                        ("now_to_dcrs_stock_totqy", "현재까지 감소", "i"), ("redc", "감자", "i"), ("profit_incnr", "이익소각", "i"),
                        ("rdmstk_repy", "상환주식 상환", "i"), ("etc", "기타", "i"), ("istc_totqy", "발행주식 총수", "i"),
                        ("tesstk_co", "자기주식", "i"), ("distb_stock_co", "유통주식 수", "i")]),
    "exctv": dict(title="임원 현황", api="exctvSttus", desc="등기임원과 미등기임원 명단·직위·담당 업무·주요 경력·재직기간",
                  cols=[("nm", "성명", "t"), ("sexdstn", "성별", "t"), ("birth_ym", "출생", "t"), ("ofcps", "직위", "t"),
                        ("rgist_exctv_at", "등기 여부", "t"), ("fte_at", "상근 여부", "t"), ("chrg_job", "담당 업무", "t"),
                        ("main_career", "주요 경력", "t"), ("mxmm_shrholdr_relate", "최대주주와 관계", "t"),
                        ("hffc_pd", "재직기간", "t"), ("tenure_end_on", "임기 만료", "t")]),
}
SHAREHOLDER_KEYS = ["hyslr", "mrhl", "stock"]  # 주주·지분 화면에 차례로 나열


def rt_collect(jobs):
    """[(이름, 기업코드, 연도, API, 보고서코드)] -> {(이름, 연도): 응답 또는 None} (동시 조회, 6시간 저장)"""
    session, store = get_session(), get_cache_store()

    def one(job):
        name, code, year, api, reprt = job
        key = ("rt", api, code, year, reprt)
        cached = cache_lookup(store, key, CACHE_TTL)
        if cached is not _MISSING:
            return cached
        jo = call_dart(session, f"https://opendart.fss.or.kr/api/{api}.json",
                       {"crtfc_key": API_KEY, "corp_code": code, "bsns_year": str(year), "reprt_code": reprt})
        cache_save(store, key, jo)
        return jo

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(one, jobs))
    return {(j[0], j[2], j[3]): r for j, r in zip(jobs, results)}


def rt_cell(value, kind):
    text = str(value).strip() if value is not None else ""
    if text in ("", "-"):
        return "-"
    if kind == "i":
        n = to_int(text)
        return f"{n:,}" if n is not None else text
    return text


def rt_frame(spec, results, trend):
    """조회 결과 -> (표, 데이터 없는 곳 목록). 표는 앞에 회사(비교) 또는 연도(추이) 열, 맨 뒤에 원문 열."""
    rows, empty = [], []
    for (name, year), jo in results.items():
        got = [r for r in ((jo or {}).get("list") or []) if isinstance(r, dict)]
        if not got:
            empty.append(f"{year}년" if trend else name)
            continue
        for r in got:
            rows.append(dict(r, __corp=name, __year=year))
    if not rows:
        return pd.DataFrame(), empty
    cols = [c for c in spec["cols"] if any(str(r.get(c[0], "")).strip() not in ("", "-") for r in rows)]
    if not cols:  # 필드 이름이 달라졌으면 받은 그대로 보여준다
        keys = [k for k in rows[0] if k not in RT_META and not k.startswith("__")]
        cols = [(k, k, "t") for k in keys]
    head = ("연도", "__year") if trend else ("회사", "__corp")
    out = []
    for r in rows:
        line = {head[0]: f"{r['__year']}" if trend else r["__corp"]}
        for field, label, kind in cols:
            line[label] = rt_cell(r.get(field), kind)
        line["원문"] = DART_VIEW_URL.format(r["rcept_no"]) if r.get("rcept_no") else ""
        out.append(line)
    return pd.DataFrame(out), empty


def render_shareholders():
    """정기보고서 > 주주·지분: 최대주주·소액주주·주식 총수 현황을 차례로 나열."""
    prefix = "rts"
    years = list(range(TODAY.year, 2014, -1))
    with st.form(f"{prefix}_search"):
        corp_text = st.text_input(
            "기업명 (여러 곳은 쉼표로)", key=f"{prefix}_corp", placeholder="예: 삼성전자, SK하이닉스",
            help=f"쉼표(,)나 슬래시(/)로 나눠 최대 {MAX_RT_CORPS}곳까지 비교해요. 연도별 추이는 첫 번째 기업만 봅니다.",
        )
        c1, c2 = st.columns(2)
        year = c1.selectbox("사업연도", years, index=years.index(default_pay_year()), key=f"{prefix}_year")
        report = c2.selectbox("보고서", list(RT_REPORTS), key=f"{prefix}_report")
        mode = st.radio("보기", ["기업 비교 (한 해)", f"연도별 추이 (최근 {RT_TREND_YEARS}년, 첫 기업)"], horizontal=True, key=f"{prefix}_mode")
        submitted = st.form_submit_button("주주·지분 조회") or st.session_state.pop(f"{prefix}_run", False)

    stored = f"{prefix}_res"
    if submitted:
        st.session_state.pop(stored, None)
        trend = mode.startswith("연도별")
        names = []
        for part in re.split(NEWS_SPLIT, corp_text):
            part = canonical_corp(part)
            if part and name_key(part) not in {name_key(n) for n in names}:
                names.append(part)
        if not names:
            st.error("기업명을 입력해주세요.")
        elif len(names) > MAX_RT_CORPS:
            st.error(f"기업은 한 번에 {MAX_RT_CORPS}곳까지 넣을 수 있어요. (지금 {len(names)}곳)")
        else:
            corps = []
            if len(names) == 1 or trend:
                code = lookup_corp_code(names[0], prefix)
                if code:
                    corps.append((names[0], code))
            else:
                with st.spinner("기업 정보를 확인하는 중입니다..."):
                    for n in names:
                        code = resolve_corp_code(n)
                        if code:
                            corps.append((n, code))
                        else:
                            hint = [label for label, _ in suggest_corps(n, limit=3)]
                            st.warning(f'"{n}" 기업을 찾지 못해 빼고 조회했어요.' + (f" 혹시: {', '.join(hint)}" if hint else ""))
            if corps:
                reprt = RT_REPORTS[report]
                yrs = list(range(int(year) - RT_TREND_YEARS + 1, int(year) + 1)) if trend else [int(year)]
                who = corps[:1] if trend else corps
                jobs = [(n, c, y, RT_SECTIONS[k]["api"], reprt) for k in SHAREHOLDER_KEYS for n, c in who for y in yrs]
                with st.spinner("주주 공시를 불러오는 중입니다..."):
                    try:
                        results = rt_collect(jobs)  # {(이름, 연도, API): 응답}
                    except DartApiError as e:
                        st.error(str(e))
                    else:
                        out = []
                        for k in SHAREHOLDER_KEYS:
                            part = {(n, y): rt_lookup(results, n, y, RT_SECTIONS[k]["api"]) for n, _ in who for y in yrs}
                            df, empty = rt_frame(RT_SECTIONS[k], part, trend)
                            out.append((k, df, empty))
                        st.session_state[stored] = {"sections": out, "trend": trend, "year": int(year), "report": report,
                                                    "corp": who[0][0], "id": time.time_ns()}

    res = st.session_state.get(stored)
    if not res:
        st.caption("기업·연도를 고르고 '주주·지분 조회'를 누르세요. DART 정기보고서의 표를 그대로 정리해 보여줍니다.")
        return
    scope = f"{res['corp']} · 최근 {RT_TREND_YEARS}년 추이" if res["trend"] else f"{res['year']}년"
    for k, df, empty in res["sections"]:
        spec = RT_SECTIONS[k]
        st.markdown(f"##### {spec['title']}")
        st.caption(f"{scope} · {res['report']} · {spec['desc']}")
        if empty:
            st.caption("데이터 없음: " + ", ".join(empty) + " (해당 보고서에 이 항목이 없거나 아직 공시 전이에요)")
        if df.empty:
            st.info("조회된 데이터가 없어요. 연도나 보고서를 바꿔보세요.")
            continue
        st.dataframe(df, hide_index=True, column_config={"원문": LINK_COLUMN}, height=min(len(df) + 1, 16) * 35 + 3)
        st.download_button(
            "CSV 다운로드", df.drop(columns=["원문"]).to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{safe_filename(spec['title'])}_{safe_filename(res['corp'] if res['trend'] else str(res['year']))}.csv",
            mime="text/csv", key=f"{prefix}_dl_{k}_{res['id']}",
        )


def rt_lookup(results, name, year, api):
    """rt_collect 결과에서 (이름, 연도, API) 한 건."""
    return results.get((name, year, api))


REPORT_KINDS = ("사업보고서", "반기보고서", "분기보고서")


def periodic_reports(corp_code):
    """기업의 최근 약 4년 사업·반기·분기보고서 (최신순)."""
    start = TODAY.replace(year=TODAY.year - 4)
    rows, _, _ = collect_disclosures(corp_code, start, TODAY, "A")
    out = [r for r in rows if any(k in strip_correction(r.get("report_nm")) for k in REPORT_KINDS) and str(r.get("rcept_no", "")).strip()]
    return sorted(out, key=lambda r: (str(r.get("rcept_dt", "")), str(r.get("rcept_no", ""))), reverse=True)


def render_report_ai():
    """정기보고서 > AI 분석: 보고서 하나와 분석 항목 하나를 골라, 그 부분만 읽고 분석."""
    if not AI_PROVIDER:
        st.info("AI 분석을 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY(Claude)를 추가해주세요. (무료 Gemini를 쓰려면 GEMINI_API_KEY를 추가해주세요.)")
        return
    with st.form("rai_search"):
        corp_text = st.text_input("기업명", key="rai_corp", placeholder="예: 삼성전자")
        found = st.form_submit_button("보고서 찾기")
    if found:
        st.session_state.pop("rai_rows", None)
        st.session_state.pop("rai_pick", None)
        corp = canonical_corp(corp_text)
        if not corp:
            st.error("기업명을 입력해주세요.")
        else:
            code = lookup_corp_code(corp, "rai")
            if code:
                with st.spinner("정기보고서 목록을 불러오는 중입니다..."):
                    try:
                        rows = periodic_reports(code)
                    except DartApiError as e:
                        st.error(str(e))
                    else:
                        if rows:
                            st.session_state["rai_rows"] = {"corp": corp, "rows": rows}
                        else:
                            st.warning("최근 4년 안의 사업·반기·분기보고서를 찾지 못했어요.")
    data = st.session_state.get("rai_rows")
    if not data:
        return
    corp, rows = data["corp"], data["rows"]

    def label(r):
        d = str(r.get("rcept_dt", ""))
        d = f"{d[:4]}.{d[4:6]}.{d[6:8]}" if len(d) == 8 else d
        return f"{d} {str(r.get('report_nm', '')).strip()}"

    ridx = st.selectbox("보고서", range(len(rows)), format_func=lambda i: label(rows[i]), key="rai_pick")
    aidx = st.selectbox("분석 항목", range(len(PERIODIC_ANALYSES)), format_func=lambda i: PERIODIC_ANALYSES[i]["label"], key="rai_item")
    spec = PERIODIC_ANALYSES[aidx]
    st.caption(spec["help"])
    row = rows[ridx]
    prev = find_prev_periodic(rows, row) if spec["needs_prev"] else None
    if spec["needs_prev"]:
        st.caption(f"비교 대상: {label(prev)}" if prev else "비교할 직전(1년 전 같은 기간) 보고서가 목록에 없어요.")
    rno = str(row["rcept_no"]).strip()
    state_key = (rno, spec["key"])
    if st.button("분석하기", key="rai_go", type="primary"):
        if spec["needs_prev"] and not prev:
            st.error("직전 보고서를 찾지 못해 비교할 수 없어요.")
        else:
            with st.spinner("보고서에서 해당 부분을 찾아 분석하는 중입니다... (처음 받는 보고서는 20~40초 걸려요)"):
                try:
                    res = periodic_analysis(corp, row, spec["key"], prev)
                except (AiError, DartApiError) as e:
                    st.error(str(e))
                else:
                    st.session_state["rai_result"] = {"key": state_key, "res": res}
    saved = st.session_state.get("rai_result")
    if not saved or saved["key"] != state_key:
        return
    res = saved["res"]
    if res["empty"] and not res["text"]:
        st.warning("이 보고서에서는 해당 항목의 내용이 거의 없어요. (분기·반기보고서는 '해당사항 없음'으로 생략되는 항목이 많아요.)")
        if res["excerpt"].strip():
            st.text(res["excerpt"][:1500])
    else:
        st.markdown(res["text"])
    notes = []
    if res["model"]:
        notes.append(f"AI 분석({res['model']}) · 숫자는 반드시 원문으로 확인하세요")
    if res["read"]:
        notes.append("읽은 부분: " + ", ".join(res["read"]))
    if res["missing"]:
        notes.append("보고서에서 못 찾은 부분: " + ", ".join(res["missing"]))
    if res["truncated"]:
        notes.append(f"길어서 앞부분 {MAX_REPORT_CHARS:,}자까지만 읽었어요")
    if notes:
        st.caption(" · ".join(notes))
    st.markdown(f"[DART 원문 보기]({DART_VIEW_URL.format(rno)})")


PERIODIC_VIEWS = ["AI 분석", "주주·지분", "임직원", "배당·자사주"]
# 정기보고서 탭은 고른 화면의 입력칸만 그린다. 안 그린 화면의 입력값은 Streamlit이 지우므로, 화면을 오가도 남도록 미리 일반 값으로 옮겨 둔다.
PERIODIC_KEEP = [f"rts_{k}" for k in ("corp", "year", "report", "mode")] + [
    "rai_corp", "rai_pick", "rai_item", "pay_exctv", "pay_exctv_basis", "pay_corp", "pay_year", "pay_report", "pay_who", "div_corp", "div_year", "div_report", "div_mode", "div_tsk_period"]


def render_periodic():
    """탭: 정기보고서 - 사업·반기·분기보고서에 실린 데이터 (AI 분석 / 배당·자사주 / 임직원 / 주주·지분)."""
    for key in PERIODIC_KEEP:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    view = st.radio("보기", PERIODIC_VIEWS, horizontal=True, key="periodic_view", label_visibility="collapsed")
    if view == "배당·자사주":
        render_dividend()
    elif view == "임직원":
        render_pay()
    elif view == "AI 분석":
        render_report_ai()
    else:
        render_shareholders()


# ------------------------------------------------------------
# 기업 브리핑 (회사 하나의 실적·공시·배당·임원·뉴스를 한 화면에)
# ------------------------------------------------------------
BRIEF_YEARS = 5  # 실적 표에 보여줄 연수
BRIEF_DISC_DAYS = 92  # 최근 공시: 약 3개월
BRIEF_NEWS_DAYS = 30  # 최근 단독 뉴스: 한 달
BRIEF_NEWS_MAX = 8  # 보여줄 뉴스 묶음 수
BRIEF_DISC_MAX = 12  # 보여줄 주요 공시 수

AI_BRIEF_SYSTEM = (
    "너는 중앙일보 산업부 기자의 취재 준비를 돕는 보조자다. 사용자가 준 자료(실적·재무, 최근 주요 공시 제목, 배당, 자사주 결정 공시, 임원 변동)만 근거로 쓴다. "
    "자료에 없는 숫자·사실·원인·전망·시장 반응·뉴스 내용은 절대 만들지 않는다. 숫자는 자료 표기 그대로 옮기고 새로 계산하지 않는다. "
    "공시 제목만으로는 내용을 알 수 없으니 제목에서 읽히는 것까지만 말하고, 내용은 원문 확인이 필요하다고 적는다. "
    "원인·배경은 단정하지 말고 '확인 필요'로 표시한다. 한국어, 짧고 명확한 기사체로 쓴다."
)


def df_lines(df, cols=None):
    """표 -> 'a | b | c' 줄 목록 (AI에 보낼 글)."""
    if df is None or df.empty:
        return []
    cols = [c for c in (cols or list(df.columns)) if c in df.columns]
    return [" | ".join(str(v) for v in row) for row in df[cols].itertuples(index=False)]


def brief_ai_prompt(b):
    parts = [fin_ai_text(b["corp"], b["records"], "연도별")] if b.get("records") else [f"기업: {b['corp']}\n(실적 자료 없음)"]
    if b.get("disc") is not None and not b["disc"].empty:
        parts.append("[최근 3개월 주요 공시 제목]\n" + "\n".join(df_lines(b["disc"], ["접수일", "보고서명"])))
    if b.get("div") is not None and not b["div"].empty:
        parts.append("[배당·자사주 (최근 3개년 사업보고서)]\n" + "\n".join(df_lines(b["div"].drop(columns=["원문"] + KRX_DIV_COLS, errors="ignore"))))
    if b.get("tsk"):
        parts.append("[최근 1년 자사주 결정 공시]\n" + "\n".join(f"{r['접수일']} | {r['구분']} | {r['주식 수']} | {r['금액']} | {r['보고서명']}" for r in b["tsk"][:10]))
    if b.get("exctv") and b["exctv"].get("diff") is not None:
        d = b["exctv"]
        lines = df_lines(d["diff"], ["변동", "성명", "직위", "구분"]) if not d["diff"].empty else ["변동 없음"]
        parts.append(f"[등기임원 변동: {d['year'] - 1}년 → {d['year']}년 사업보고서]\n" + "\n".join(lines[:20]))
    return (
        "\n\n".join(parts)
        + "\n\n위 자료만 근거로, 이 기업을 취재하는 산업부 기자를 위해 아래 형식으로 써라. 자료가 부족한 항목은 건너뛴다.\n"
        "**한 줄 요약**: 지금 이 기업을 어떻게 봐야 하는지 한 문장\n"
        "**취재 포인트 3가지**: 각 항목은 '- 포인트: 한 문장 (근거: 자료의 숫자·공시명)' 형식. 근거가 어느 자료(실적·공시·배당·자사주·임원)인지 밝혀라\n"
        "**물어볼 것**: 자료만으로 알 수 없어 회사나 취재원에게 확인해야 할 질문 2~3개 (- 로 시작)"
    )


def collect_brief(name, code, progress):
    """회사 하나의 브리핑 재료. 항목 하나가 실패해도 나머지는 보여주도록 항목별로 오류를 담는다."""
    b = {"corp": name, "code": code, "errors": {}, "asof": datetime.now(KST)}
    corps = [(name, code)]
    end = TODAY.year - 1

    def step(label, key, fn):
        progress(label)
        try:
            return fn()
        except Exception as e:  # 한 항목 때문에 전체를 버리지 않는다
            b["errors"][key] = str(e) if isinstance(e, (DartApiError, AiError, NewsApiError, KrxError)) else f"불러오지 못했어요 ({type(e).__name__})"
            return None

    if KRX_KEY:
        stock = stock_code_of(code)
        b["quote"] = step("시세를 불러오는 중...", "quote", lambda: collect_quote(stock) if stock else None)
    else:
        b["quote"] = False  # 키 없음
    b["records"] = step("실적을 불러오는 중...", "fin", lambda: collect_yearly(code, end - BRIEF_YEARS + 1, end, lambda: None))

    def disclosures():
        rows, total, _ = collect_disclosures(code, TODAY - timedelta(days=BRIEF_DISC_DAYS), TODAY, "")
        b["disc_total"] = total
        major = match_preset(rows, "주요 전체") + [r for r in match_preset(rows, "정기공시") if r not in match_preset(rows, "주요 전체")]
        major.sort(key=lambda r: str(r.get("rcept_no", "")), reverse=True)
        return disclosure_table(major[:BRIEF_DISC_MAX]) if major else pd.DataFrame()

    b["disc"] = step("최근 공시를 불러오는 중...", "disc", disclosures)

    def dividend():
        years = [y for y in range(end - 2, end + 1) if y >= 2015]
        got = collect_div_years(corps, years, "11011")
        px = None
        if KRX_KEY:
            try:
                px = dividend_prices(corps, years, "11011")
            except KrxError as e:
                b["errors"]["quote_div"] = str(e)
        table, dps = div_trend_table(got, corps, years, px)
        b["dps"] = dps.get(name, [])
        return table

    b["div"] = step("배당·자사주를 불러오는 중...", "div", dividend)
    tsk = step("자사주 결정 공시를 찾는 중...", "tsk", lambda: collect_tsk_decisions(corps, 1))
    b["tsk"], b["tsk_truncated"] = (tsk if tsk else ([], False))

    def execs():
        for y in (end, end - 1):
            cur, _, _ = rt_exctv(corps, y, "11011", with_unreg=False)
            if not cur.empty:
                prev, _, _ = rt_exctv(corps, y - 1, "11011", with_unreg=False)
                return {"year": y, "count": len(cur), "diff": exctv_diff(cur, prev) if not prev.empty else None}
        return None

    b["exctv"] = step("임원 변동을 확인하는 중...", "exctv", execs)

    if NAVER_ID and NAVER_SECRET:
        def news():
            names = news_names(name)
            items, _, _ = search_news(names, BRIEF_NEWS_DAYS)
            keys = [name_key(t) for t in names]
            items = [i for i in items if i["category"] and is_exclusive(i["title"]) and any(k in name_key(i["title"]) for k in keys)]
            return cluster_news(items)[:BRIEF_NEWS_MAX]

        b["news"] = step("최근 단독 뉴스를 찾는 중...", "news", news)
    else:
        b["news"] = None
    return b


def render_brief_result(b):
    st.markdown(f"#### {b['corp']} 브리핑")
    st.caption(f"{b['asof']:%Y.%m.%d %H:%M} 기준 · 연결 기준 · 항목마다 따로 불러와서 일부가 비어 있을 수 있어요")
    errs = b["errors"]

    if b.get("quote") is False:
        krx_hint()
    elif errs.get("quote"):
        krx_error_box(errs["quote"])
    else:
        render_quote(b.get("quote"))

    st.markdown(f"##### 실적 (최근 {BRIEF_YEARS}년, 억원)")
    if errs.get("fin"):
        st.warning(errs["fin"])
    elif not b["records"]:
        st.info("조회된 실적이 없어요.")
    else:
        table = build_table(b["records"], "연도별", unit="억원")
        st.dataframe(style_table(table), hide_index=True, height=(len(table) + 1) * 35 + 3,
                     column_config={"기간": st.column_config.TextColumn("기간", width=95), "원문": LINK_COLUMN})
        bs = build_bs_table(b["records"], "연도별", unit="억원")
        if not bs.empty:
            st.dataframe(bs, hide_index=True, height=(len(bs) + 1) * 35 + 3,
                         column_config={"기간": st.column_config.TextColumn("기간", width=95)})
        if st.toggle("그래프", key="brief_chart"):
            render_fin_charts(b["records"], "억원")

    st.markdown("##### 최근 3개월 주요 공시")
    if errs.get("disc"):
        st.warning(errs["disc"])
    elif b["disc"] is None or b["disc"].empty:
        st.info("최근 3개월 동안 실적·공급계약·증자·자기주식·지분 변동 등 주요 공시가 없어요.")
    else:
        st.caption(f"최근 3개월 공시 {b.get('disc_total', 0):,}건 중 주요 공시 · 최신순")
        st.dataframe(b["disc"], hide_index=True, column_config={"원문": LINK_COLUMN, "보고서명": st.column_config.TextColumn("보고서명", width="large")},
                     height=min(len(b["disc"]) + 1, 14) * 35 + 3)

    st.markdown("##### 배당·자사주")
    if errs.get("div"):
        st.warning(errs["div"])
    elif b["div"] is None or b["div"].empty:
        st.info("최근 3개년 사업보고서에 배당·자사주 공시가 없어요.")
    else:
        st.caption("사업보고서 기준 · 오래된 해가 위 · 배당성향은 연결 기준 · 주당 배당금·시가배당률은 보통주 · 자사주는 보통주(주)")
        st.dataframe(b["div"].style.map(change_style, subset=["총액 전년比", "자사주 취득", "자사주 소각"]), hide_index=True,
                     column_config={"원문": LINK_COLUMN}, height=(len(b["div"]) + 1) * 35 + 3)
        streak = dps_streak(b.get("dps", []))
        if streak:
            n, word = streak
            st.caption(f"주당 배당금 " + (f"직전 해보다 {word}" if n == 1 else f"{n}년 연속 {word}"))
    if errs.get("tsk"):
        st.warning(errs["tsk"])
    elif b["tsk"]:
        st.markdown("**최근 1년 자사주 결정 공시**")
        shown = pd.DataFrame(b["tsk"][:8])[["접수일", "구분", "주식 수", "금액", "예정 기간", "보고서명", "원문"]]
        st.dataframe(shown, hide_index=True, column_config={"원문": LINK_COLUMN}, height=(len(shown) + 1) * 35 + 3)
        if len(b["tsk"]) > 8:
            st.caption(f"전체 {len(b['tsk'])}건 중 최신 8건 · 나머지는 정기보고서 탭의 배당·자사주에서 볼 수 있어요")

    st.markdown("##### 임원 변동 (등기임원)")
    ex = b.get("exctv")
    if errs.get("exctv"):
        st.warning(errs["exctv"])
    elif not ex:
        st.info("임원 현황을 찾지 못했어요.")
    elif ex["diff"] is None:
        st.info(f"{ex['year']}년 사업보고서 등기임원은 {ex['count']}명이에요. 전년 보고서가 없어 비교하지 못했어요.")
    else:
        st.caption(f"{ex['year'] - 1}년 → {ex['year']}년 사업보고서 · 등기임원 {ex['count']}명 · 미등기임원까지 보려면 정기보고서 탭의 임직원을 이용하세요")
        if ex["diff"].empty:
            st.info("두 보고서의 등기임원 명단이 같아요.")
        else:
            st.dataframe(ex["diff"], hide_index=True, height=min(len(ex["diff"]) + 1, 14) * 35 + 3)

    st.markdown("##### 최근 한 달 단독 뉴스")
    if b.get("news") is None and "news" not in errs:
        st.caption("뉴스를 함께 보려면 Streamlit Secrets에 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET을 추가해주세요.")
    elif errs.get("news"):
        st.warning(errs["news"])
    elif not b["news"]:
        st.info("최근 한 달 동안 제목에 [단독]이 붙은 기사가 없어요.")
    else:
        st.markdown(news_html(b["news"]), unsafe_allow_html=True)
        st.caption("뉴스는 네이버 약관상 AI 분석에 쓰지 않고 제목만 보여드려요.")

    st.markdown("##### AI 취재 포인트")
    if not AI_PROVIDER:
        st.caption("AI 취재 포인트를 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY(Claude) 또는 GEMINI_API_KEY(Gemini)를 추가해주세요.")
        return
    prompt = brief_ai_prompt(b)
    sig = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    if st.button("위 자료로 취재 포인트 뽑기", key="brief_ai_go", type="primary"):
        with st.spinner("자료를 종합하는 중입니다... (10~30초)"):
            try:
                (text, used) = ai_cached(("brief", sig), lambda: ai_generate(prompt, system=AI_BRIEF_SYSTEM))
            except AiError as e:
                st.error(str(e))
            else:
                st.session_state["brief_ai"] = {"sig": sig, "text": text, "model": used}
    saved = st.session_state.get("brief_ai")
    if saved and saved["sig"] == sig:
        st.markdown(saved["text"])
        st.caption(f"AI 분석({saved['model']}) · 위 실적·공시·배당·임원 자료만 근거로 한 해석이에요. 공시는 제목만 보고 판단한 것이라 원문 확인이 필요해요")
    else:
        st.caption("누르면 위 실적·공시·배당·자사주·임원 자료를 종합해 취재 포인트와 확인할 질문을 정리해 줘요. (뉴스는 포함하지 않아요)")


def render_brief():
    """탭: 기업 브리핑 - 회사 하나의 실적·공시·배당·임원·뉴스를 한 화면에."""
    with st.form("brief_search"):
        corp_text = st.text_input("기업명 또는 종목코드", key="brief_corp", placeholder="예: 삼성전자 또는 005930")
        submitted = st.form_submit_button("브리핑 만들기", type="primary") or st.session_state.pop("brief_run", False)
    st.caption("취재 전에 훑어보는 한 장짜리 자료예요. 최근 5년 실적, 최근 3개월 주요 공시, 배당·자사주, 등기임원 변동, 최근 한 달 단독 뉴스를 모아 보여줍니다.")

    if submitted:
        corp = canonical_corp(corp_text.strip())
        if not corp:
            st.error("기업명을 입력해주세요.")
        else:
            st.session_state.pop("brief", None)
            st.session_state.pop("brief_ai", None)
            code = lookup_corp_code(corp, "brief")
            if code:
                if corp.isdigit():  # 종목코드로 넣었으면 회사 이름으로
                    idx = get_name_index()
                    hit = idx[idx["corp_code"] == code]
                    corp = hit.iloc[0]["corp_name"] if not hit.empty else corp
                bar = st.progress(0.0, text="브리핑을 만드는 중입니다...")
                steps = [0]

                def progress(label):
                    steps[0] += 1
                    bar.progress(min(steps[0] / 7, 1.0), text=label)

                try:
                    st.session_state["brief"] = collect_brief(corp, code, progress)
                finally:
                    bar.empty()

    b = st.session_state.get("brief")
    if b:
        render_brief_result(b)



def run_js(code):
    """높이 없는 iframe으로 짧은 스크립트를 실행한다. 새 Streamlit은 st.iframe, 옛 버전은 components.html. 실패해도 앱은 계속."""
    try:
        if hasattr(st, "iframe"):
            st.iframe(code, height=1)
        else:
            st.components.v1.html(code, height=0)
    except Exception:
        pass


TAB_NAMES = ["기업 브리핑", "재무 조회", "해외 기업", "단독 뉴스", "오늘의 공시", "공시 목록", "정기보고서", "재계 순위"]
# 관심 기업 주소(?news=... 또는 ?sector=...)로 들어오면 단독 뉴스 탭을 먼저 연다
first_tab = "단독 뉴스" if apply_watchlist() else None
try:
    tabs = st.tabs(TAB_NAMES, default=first_tab)
except TypeError:  # 처음 열 탭 지정을 지원하지 않는 옛 Streamlit
    tabs = st.tabs(TAB_NAMES)
tab_brief, tab_fin, tab_overseas, tab_news, tab_feed, tab_list, tab_periodic, tab_chaebol = tabs
with tab_brief:
    render_brief()
with tab_fin:
    render_fin_hub()
with tab_overseas:
    render_overseas()
with tab_news:
    render_news()
with tab_feed:
    render_feed()
with tab_list:
    render_disclosures()
with tab_periodic:
    render_periodic()
with tab_chaebol:
    render_chaebol()
# 모바일에서 표를 옆으로 밀 때 열 순서가 바뀌는 것을 막는다(Streamlit에 끄는 옵션이 없어 JS로 처리).
# 머리글에서 시작한 드래그의 move 이벤트만 차단하고, 탭(정렬)·기본 스크롤은 그대로 둔다.
_COL_LOCK_JS = """
<script>
(function(){
  try{
    var w = window.parent, d = w.document;
    if (w.__colLock) return;
    w.__colLock = true;
    var armed = false;
    function inHeader(e){
      var t = e.target && e.target.closest ? e.target.closest('[data-testid="stDataFrame"]') : null;
      if(!t) return false;
      var p = e.touches && e.touches[0] ? e.touches[0] : e;
      var r = t.getBoundingClientRect();
      return (p.clientY - r.top) < 40;
    }
    ['pointerdown','mousedown','touchstart'].forEach(function(n){
      d.addEventListener(n, function(e){ armed = inHeader(e); }, true);
    });
    ['pointerup','mouseup','touchend','touchcancel','pointercancel'].forEach(function(n){
      w.addEventListener(n, function(){ armed = false; }, true);
    });
    ['pointermove','mousemove','touchmove','dragstart'].forEach(function(n){
      w.addEventListener(n, function(e){
        if(armed){ e.stopImmediatePropagation(); }
      }, true);
    });
  }catch(err){}
})();
</script>
"""
run_js(_COL_LOCK_JS)

render_krx_check()
st.caption(f"updated/{LAST_UPDATED}")  # 맨 아래
