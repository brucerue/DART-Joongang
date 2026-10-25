"""
DART 매출액 / 영업이익 조회 (Streamlit)

기능: 연도별·분기별 조회, 전년(동기)·전분기 대비 증감률,
      흑자전환/적자전환/적자지속 표시, 금액은 억원 단위,
      CSV·엑셀 다운로드, 공시 원문 링크,
      공시 목록(유형 필터·주요 공시 버튼·보고서명 검색), 오늘의 공시(전 상장사 주요 공시 모아보기),
      산업 뉴스(통신·단독·외신, 섹터·검색어 검색, 비슷한 기사 묶기)

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
import xml.etree.ElementTree as ET
import zipfile
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote, urlparse

import importlib

import pandas as pd
import stock_calc as sc

sc = importlib.reload(sc)  # 배포로 stock_calc.py가 바뀌었는데 서버 메모리에 옛 모듈이 남아 있으면 새 함수를 못 찾으므로, 실행할 때마다 다시 읽는다
import importlib
import today_calc as tdc
if not hasattr(tdc, "PERIODS"):  # 앱이 켜진 채로 배포되면 서버가 옛 모듈을 기억할 수 있어, 새 항목이 없으면 다시 읽는다
    tdc = importlib.reload(tdc)
import trade_calc as tc
tc = importlib.reload(tc)  # (위와 같은 이유)
import ai_search as ais
ais = importlib.reload(ais)  # (위와 같은 이유)
import calc_tools as ct
ct = importlib.reload(ct)  # (위와 같은 이유)
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
    "자동차": ["현대자동차", "기아", "현대모비스"],
    "배터리": ["LG에너지솔루션", "삼성SDI", "에스케이온", "포스코퓨처엠", "엘앤에프", "에코프로비엠"],
    "조선·해운": ["HD현대", "HD한국조선해양", "HD현대중공업", "한화오션", "삼성중공업", "HMM", "팬오션"],
    "방산": ["한화에어로스페이스", "한국항공우주", "LIG디펜스앤에어로스페이스", "현대로템"],
    "철강": ["POSCO홀딩스", "현대제철", "동국제강"],
    "정유·석화": ["SK이노베이션", "S-Oil", "GS칼텍스", "에이치디현대오일뱅크", "롯데케미칼", "LG화학", "한화솔루션", "금호석유화학"],
    "에너지·소재": ["효성", "HS효성", "대한전선", "엘에스일렉트릭", "LS", "LS전선", "고려아연", "OCI", "OCI홀딩스", "HD현대일렉트릭"],
    "반도체": ["삼성전자", "SK하이닉스", "한미반도체"],
    "IT·통신": ["SK텔레콤", "KT", "LG유플러스", "NAVER", "카카오"],
    "전자": ["LG전자", "삼성전기", "LG이노텍", "삼성에스디에스", "LG디스플레이"],  # 삼성전자는 반도체에 있어 뺌(뉴스 섹터 '전자'에는 들어 있음)
    "항공": ["대한항공", "한진칼", "아시아나항공", "진에어", "제주항공", "에어부산", "트리니티항공"],  # 트리니티항공 = 옛 티웨이항공
}
# 실적 캘린더의 섹터: 자동차와 배터리는 한 섹터로 묶는다
# 산업 뉴스·공시 분석·실적 캘린더·텔레그램 알람의 섹터에서는 조선·해운 / 방산 / 철강을 '중후장대' 하나로 묶는다 (재무 조회·주가는 위 INDUSTRY_GROUPS 그대로)
HEAVY_NAME = "중후장대"
HEAVY_PARTS = ("조선·해운", "방산", "철강")
CAL_GROUPS = {name: list(INDUSTRY_GROUPS[name]) for name in ("정유·석화", "에너지·소재", "반도체", "IT·통신", "전자", "항공")}
CAL_GROUPS["자동차·배터리"] = INDUSTRY_GROUPS["자동차"] + INDUSTRY_GROUPS["배터리"]
CAL_GROUPS[HEAVY_NAME] = [n for part in HEAVY_PARTS for n in INDUSTRY_GROUPS[part]]
CAL_GROUPS = {k: CAL_GROUPS[k] for k in ("자동차·배터리", HEAVY_NAME, "정유·석화", "에너지·소재", "반도체", "전자", "항공", "IT·통신")}
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
    "거래 중단·해지": ["거래중단", "계약해지", "계약해제", "계약종료", "영업정지", "생산중단"],
    "증자·감자": ["유상증자", "무상증자", "감자"],
    "합병·분할": ["합병", "분할", "주식교환", "주식이전"],
    "기업설명회": ["기업설명회"],
    "자기주식": ["자기주식"],
    "지분 변동": ["대량보유", "소유상황", "최대주주", "소유주식변동"],
    "정기보고서": ["사업보고서", "반기보고서", "분기보고서"],
}

# 앱의 공시 화면에서만 쓰는 추가 묶음(보기 버튼). 위 MAJOR_DISCLOSURES와 달리 '주요 전체'와 텔레그램 공시 알람에는 들어가지 않는다
EXTRA_DISCLOSURES = {
    "투자·출자": ["타법인주식및출자증권취득", "타법인주식및출자증권처분", "유형자산취득", "유형자산처분", "신규시설투자"],
    "사채 발행": ["전환사채권발행", "신주인수권부사채권발행", "교환사채권발행", "사채권발행"],
    "최대주주·대표 변경": ["최대주주변경", "대표이사변경", "대표이사(대표집행임원)변경", "최대주주등소유주식변동"],
    "배당": ["배당결정", "현금배당", "현물배당"],
    "소송·제재": ["소송등의제기", "소송등의판결", "횡령", "배임", "과징금", "불성실공시", "조회공시"],
    "상장·거래 위험": ["상장폐지", "관리종목", "거래정지", "회생절차", "파산", "감사의견"],
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
NEWS_TTL = 10 * 60  # 같은 검색은 10분 동안 저장해 둔 결과를 재사용 (호출 절약). 길게 두면 방금 나온 기사가 저장분 때문에 안 보임
NEWS_TTL_TODAY = 5 * 60  # 기간이 '오늘'인 검색은 새 기사가 계속 올라오므로 더 짧게
SIMILAR_TITLE = 0.8  # 제목이 이 정도(0~1) 이상 비슷하면 같은 기사로 묶는다
NEWS_PERIODS = {"오늘": 1, "24시간": 1, "3일": 3, "일주일": 7}  # 오늘=오늘 0시부터(한국시간), 24시간=지금부터 24시간 전까지

# 뉴스에 보여줄 언론사. 기사 주소(도메인) -> (언론사 이름, 구분). 여기 없는 매체의 기사는 보이지 않는다.
NEWS_CATEGORIES = ["종합일간지", "경제지", "방송사", "통신사"]
NEWS_SPLIT = r"[,，、/;\n]+"  # 여러 기업을 나누는 기호: 쉼표, 슬래시 등 (띄어쓰기는 'LG 에너지솔루션'처럼 이름 안에 있을 수 있어 제외)
NEWS_KEYWORD = "단독"  # 뉴스 탭은 항상 이 표기가 제목에 있는 기사만 보여준다 (화면에는 드러내지 않고 내부에서 적용)
# 이 기업명을 입력하면 아래 별칭도 함께 검색한다 (제목에 줄임말로 쓰이는 경우를 잡기 위함). 자유롭게 추가·수정하세요.
# 별칭 없이 기업명만 입력하면 그 이름만 검색합니다. 예: '삼성'을 입력하면 '삼성'만, '삼성전자'를 입력하면 삼성전자·삼성·삼전.
NEWS_ALIASES = {
    "케이티": ["KT"],  # 기사 제목에는 KT로 나오므로 함께 검색
    "LIG디펜스앤에어로스페이스": ["LIG넥스원"],
    "LIG넥스원": ["LIG디펜스앤에어로스페이스"],
    "KT": ["케이티"],
    "삼성전자": ["삼성", "삼전"],
    "삼전": ["삼성전자"],
    "하닉": ["SK하이닉스"],
    "엔솔": ["LG에너지솔루션", "LG엔솔"],
    "현차": ["현대자동차", "현대차"],
    "삼전": ["삼성전자"],
    "하닉": ["SK하이닉스"],
    "엔솔": ["LG에너지솔루션", "LG엔솔"],
    "현차": ["현대자동차", "현대차"],
    "현대자동차": ["현대차", "현차"],
    "현대차": ["현차", "현대자동차"],
    "SK하이닉스": ["하닉"],
    "LG에너지솔루션": ["LG엔솔", "엔솔"],
    "LG엔솔": ["LG에너지솔루션", "엔솔"],
    "삼성바이오로직스": ["삼바"],
    "삼성바이오": ["삼성바이오로직스", "삼바"],
    "삼바": ["삼성바이오로직스", "삼성바이오"],
    "SK텔레콤": ["SKT"],
    "SKT": ["SK텔레콤"],
    "SK이노베이션": ["SK이노"],
    "SK이노": ["SK이노베이션"],
    "S-Oil": ["에쓰오일"],
    "에쓰오일": ["S-Oil"],
    "LG유플러스": ["LG유플"],
    "LG유플": ["LG유플러스"],
    "HD현대오일뱅크": ["오일뱅크", "에이치디현대오일뱅크"],
    "에이치디현대오일뱅크": ["오일뱅크", "HD현대오일뱅크"],
    "오일뱅크": ["HD현대오일뱅크", "에이치디현대오일뱅크"],
    "한국전력공사": ["한전"],
    "한국전력": ["한전"],
    "한전": ["한국전력공사", "한국전력"],
    "POSCO홀딩스": ["포스코홀딩스", "포스코"],
    "포스코홀딩스": ["POSCO홀딩스", "포스코"],
    "한화에어로스페이스": ["한화에어로"],
    "한화에어로": ["한화에어로스페이스"],
    "한국항공우주산업": ["KAI", "한국항공우주"],
    "한국항공우주": ["KAI", "한국항공우주산업"],
    "KAI": ["한국항공우주산업", "한국항공우주"],
    "HD한국조선해양": ["한국조선해양", "한조"],
    "한국조선해양": ["HD한국조선해양", "한조"],
    "한조": ["HD한국조선해양", "한국조선해양"],
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
    "KB금융그룹": ["KB금융", "KB금융지주"],
    "NAVER": ["네이버"],
    "네이버": ["NAVER"],
    "아모레퍼시픽": ["아모레"],
    "한국타이어앤테크놀로지": ["한국타이어"],
    "한국타이어": ["한국타이어앤테크놀로지"],
    "기업은행": ["IBK기업은행", "IBK"],
    "IBK기업은행": ["기업은행", "IBK"],
    # 재계·경제단체·항공·전자 (줄임말과 정식 이름을 서로 같은 것으로 본다)
    "대한상의": ["대한상공회의소"],
    "대한상공회의소": ["대한상의"],
    "한경협": ["한국경제인협회"],
    "한국경제인협회": ["한경협"],
    "중기중앙회": ["중소기업중앙회"],
    "중소기업중앙회": ["중기중앙회"],
    "무협": ["무역협회"],
    "무역협회": ["무협"],
    "코트라": ["KOTRA"],
    "KOTRA": ["코트라"],
    "암참": ["주한미국상공회의소"],
    "주한미국상공회의소": ["암참"],
    "상장협": ["상장회사협의회"],
    "상장회사협의회": ["상장협"],
    "아시아나": ["아시아나항공"],
    "아시아나항공": ["아시아나"],
    "인천공항": ["인천공항공사"],
    "인천공항공사": ["인천공항"],
    "LG디플": ["LG디스플레이"],
    "LG디스플레이": ["LG디플"],
    "삼성에스디에스": ["삼성SDS"],
    "삼성SDS": ["삼성에스디에스"],
}
# 섹터 모드: 섹터를 고르면 아래 검색어를 하나씩 '검색어 단독'으로 검색해 합친다.
# '기업'은 대표 기업·줄임말, '키워드'는 기업명 없이 산업 이슈로 나오는 기사를 잡기 위한 단어.
# 제목에 이 검색어 중 하나가 들어간 기사만 보여줍니다. 너무 넓은 단어는 잡음이 늘어나니 꼭 필요한 것만 넣으세요.
NEWS_SECTORS = {
    "반도체": {
        "기업": ["삼성전자", "삼전", "SK하이닉스", "하닉", "엔비디아", "마이크론", "TSMC"],
        "키워드": ["반도체", "HBM", "파운드리"],
    },
    "자동차": {
        "기업": ["현대차", "기아", "현대모비스", "테슬라", "KG모빌리티", "르노", "르노코리아", "GM", "한국GM", "벤츠", "BMW"],
        "키워드": ["전기차", "완성차", "수입차", "정의선"],
    },
    "배터리": {
        "기업": ["LG에너지솔루션", "엔솔", "삼성SDI", "SK온", "에코프로비엠", "포스코퓨처엠", "엘앤에프", "CATL"],
        "키워드": ["2차전지", "배터리", "전고체", "양극재", "음극재"],
    },
    "중후장대": {  # 조선·해운 + 방산 + 철강 (텔레그램 알람도 같은 묶음)
        "기업": ["HD현대", "HD한국조선해양", "HD현대중공업", "한화오션", "삼성중공업", "HMM",
               "한화에어로", "한화에어로스페이스", "KAI", "LIG넥스원", "LIG", "현대로템",
               "포스코", "현대제철", "동국제강"],
        "키워드": ["조선업", "LNG선", "방산", "철강", "후판", "열연", "김승연", "김동관", "정기선", "한화"],
    },
    "정유·석화": {
        "기업": ["SK이노베이션", "SK이노", "에쓰오일", "GS칼텍스", "오일뱅크", "롯데케미칼", "LG화학", "한화솔루션", "금호석유화학", "여천NCC"],
        "키워드": ["석유화학", "석화", "정유", "석유", "WTI", "브렌트유", "OPEC"],
    },
    "IT·통신": {
        "기업": ["네이버", "카카오", "SKT", "KT", "LG유플"],
        "키워드": ["통신"],
    },
    "전자": {  # 반도체 밖의 가전·모바일·디스플레이·부품
        "기업": ["LG전자", "삼성전자", "삼성디스플레이", "LG디스플레이", "삼성전기", "LG이노텍", "삼성SDS", "애플", "샤오미", "하이센스", "TCL"],
        "키워드": ["가전", "스마트폰", "갤럭시", "아이폰", "폴더블", "디스플레이", "OLED", "냉장고", "에어컨", "로봇청소기", "웨어러블", "스마트홈", "가전 관세"],
    },
    "항공": {
        "기업": ["대한항공", "아시아나항공", "진에어", "제주항공", "티웨이항공", "에어부산", "에어서울", "에어프레미아", "이스타항공", "한진칼",
               "인천공항공사", "한국공항공사", "보잉", "에어버스"],
        "키워드": ["항공사", "항공기", "항공권", "항공유", "항공화물", "저비용항공사", "LCC", "유류할증료", "조종사", "김포공항"],
    },
    "재계": {  # 경제단체 + 주요 총수 + 재계 이슈(다른 섹터의 총수 키워드와 일부러 겹침)
        "기업": ["대한상의", "대한상공회의소", "한경협", "한국경제인협회", "경총", "중기중앙회", "중소기업중앙회", "무역협회", "무협", "코트라", "KOTRA", "암참", "상장협",
               "이재용", "최태원", "정의선", "구광모", "신동빈", "김승연", "김동관", "조원태", "박정원", "구자은", "장인화", "정용진"],
        "키워드": ["재계", "총수", "오너", "대기업집단", "동일인 지정", "상법 개정", "노란봉투법", "법인세"],
    },
    "에너지·소재": {
        "기업": ["효성", "HS효성", "대한전선", "LS일렉트릭", "LS", "LS전선", "고려아연", "OCI", "OCI홀딩스", "HD현대일렉트릭", "두산에너빌"],
        "키워드": ["원전", "전력망", "핵심광물", "태양광", "최윤범"],
    },
}
NEWS_SECTORS = {k: NEWS_SECTORS[k] for k in ("자동차", "배터리", HEAVY_NAME, "정유·석화", "에너지·소재", "반도체", "전자", "항공", "재계", "IT·통신")}  # 화면에 나오는 순서
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
    "thebell.co.kr": ("더벨", "경제지"), "newspim.com": ("뉴스핌", "경제지"),
    "bloter.net": ("블로터", "경제지"), "etoday.co.kr": ("이투데이", "경제지"),
    "zdnet.co.kr": ("지디넷코리아", "경제지"),
    # 방송사 (도메인은 '뒤쪽 일치'라 news.kbs.co.kr 같은 하위 주소도 잡힌다)
    "kbs.co.kr": ("KBS", "방송사"), "sbs.co.kr": ("SBS", "방송사"),
    "imbc.com": ("MBC", "방송사"), "mbc.co.kr": ("MBC", "방송사"),
    "tvchosun.com": ("TV조선", "방송사"), "jtbc.co.kr": ("JTBC", "방송사"),
    "ichannela.com": ("채널A", "방송사"), "mbn.co.kr": ("MBN", "방송사"),
    "ytn.co.kr": ("YTN", "방송사"), "yonhapnewstv.co.kr": ("연합뉴스TV", "방송사"),
    "nocutnews.co.kr": ("노컷뉴스", "방송사"),
    # 통신사
    "yna.co.kr": ("연합뉴스", "통신사"), "newsis.com": ("뉴시스", "통신사"), "news1.kr": ("뉴스1", "통신사"),
}

# 언론사 도메인 아래에 있어도 기사가 아닌 곳(커뮤니티·게시판 등)은 뺀다. 필요하면 여기에 추가하세요.
# 텔레그램 알람 신청: 앱 맨 아래 '뉴스·공시 알람 받기'. 고른 섹터·종류를 t.me 링크의 start 값에 0/1로 담는다(순서를 바꾸면 news_digest.py의 해석도 달라지므로 끝에만 추가할 것)
ALARM_BOT = "joongangbiz_bot"
ALARM_SECTORS = ("자동차", "배터리", "중후장대", "정유·석화", "에너지·소재", "반도체", "IT·통신", "전자", "항공", "재계")
ALARM_MODES = ("통신", "단독", "공시")
# 팀 프리셋: 팀 버튼을 누르면 이 섹터들이 한꺼번에 선택된다(텔레그램 받는 사람 설정의 "teams"도 같은 목록을 쓴다). 팀을 늘리려면 여기에 한 줄 추가
TEAM_PRESETS = {
    "차팀": ["자동차", "배터리", "중후장대", "정유·석화", "에너지·소재"],
    "재계팀": ["반도체", "전자", "항공", "재계"],
}
PUBLISHER_BLOCK = ("mlbpark.donga.com", "view.mk.co.kr")  # 뷰타임스: 더구루 등 외부 매체 제휴 기사가 매일경제로 잡히는 것 방지

LAST_UPDATED = "2026.10.26 22:30"  # 화면 아래 크레딧의 updated 날짜·시간(한국 시간). 앱을 고칠 때마다 직접 바꿔주세요

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
USE_NAVER = (load_secret("USE_NAVER") or "1").strip().lower() in ("1", "true", "yes", "on")  # 기본은 켜짐(네이버 키가 있을 때): 단독 검색은 구글 뉴스에 네이버 뉴스를 항상 합친다. 끄려면 Streamlit Secrets에 USE_NAVER = "0"

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
    """기업명 → 고유번호 변환용. (처음 한 번 기업 목록을 내려받느라 몇 초 걸릴 수 있음)
    DART 서버가 응답하지 않으면 라이브러리는 몇 분씩 기다리므로, 접속 시간을 제한하고 몇 번 다시 시도한다."""
    original_get = requests.get

    def get_with_timeout(*args, **kwargs):
        kwargs.setdefault("timeout", (10, 90))  # (연결 10초, 응답 90초)
        return original_get(*args, **kwargs)

    last_error = None
    requests.get = get_with_timeout
    try:
        for attempt in range(3):
            try:
                return OpenDartReader(API_KEY)
            except Exception as e:  # 연결 시간 초과 등
                last_error = e
                time.sleep(2 * (attempt + 1))
    finally:
        requests.get = original_get
    raise RuntimeError("DART 서버에 연결하지 못했어요 (기업 목록을 받지 못함). 잠시 후 다시 시도하거나 앱을 새로고침해 주세요.") from None  # 원래 오류에는 인증키가 든 주소가 있어, 화면에 보이지 않게 이어 붙이지 않는다


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


def fetch(session, store, corp_code, year, reprt_code, fs_div=None):
    """보고서 한 건 조회. 없으면 None, DART 오류·응답 없음이면 DartApiError.
    fs_div: 연결(CFS)·별도(OFS). 비우면 앱 기본(FS_DIV).

    ※ 작업 스레드에서 실행되므로 st.* 함수를 쓰지 않고, 세션·저장소는 인자로 받는다.
    """
    fs_div = fs_div or FS_DIV
    key = (corp_code, year, reprt_code) if fs_div == FS_DIV else (corp_code, year, reprt_code, fs_div)
    cached = cache_lookup(store, key, CACHE_TTL)
    if cached is not _MISSING:
        return cached

    params = {
        "crtfc_key": API_KEY,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": reprt_code,
        "fs_div": fs_div,
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


# --- DART '공시 보기' 링크를 보고서의 연결재무제표 쪽으로 (실패하면 보고서 첫 화면 링크 그대로) ---
DART_VIEWER_URL = "https://dart.fss.or.kr/report/viewer.do"
RCP_IN_LINK = re.compile(r"rcpNo=(\d{14})")


def parse_dart_consol_node(page):
    """DART 보고서 첫 화면(main.do)의 문서목차 -> 연결재무제표 항목의 {rcpNo, dcmNo, eleId, offset, length, dtd} 또는 None.
    '주석' 항목(연결재무제표 주석)은 건너뛴다."""
    marks = list(re.finditer(r"\['text'\]\s*=\s*\"([^\"]*)\"", page))
    for n, m in enumerate(marks):
        text = m.group(1)
        if "연결재무제표" not in text.replace(" ", "") or "주석" in text:
            continue
        block = page[m.end(): marks[n + 1].start() if n + 1 < len(marks) else m.end() + 1500]
        vals = dict(re.findall(r"\['(\w+)'\]\s*=\s*\"([^\"]*)\"", block))
        if all(vals.get(k) for k in ("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd")):
            return {k: vals[k] for k in ("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd")}
    return None


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False, max_entries=800)
def _dart_consol_url(rcp):
    res = requests.get(DART_VIEW_URL.format(rcp), headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 15))
    res.raise_for_status()
    node = parse_dart_consol_node(res.text)
    if not node:
        return ""  # 연결재무제표가 없는 문서(잠정실적 등): 빈 값을 저장해 다시 묻지 않는다
    return f"{DART_VIEWER_URL}?" + "&".join(f"{k}={v}" for k, v in node.items())


def consol_link(link):
    """보고서 링크 -> 그 보고서의 연결재무제표가 바로 열리는 링크. 못 찾거나 접속이 안 되면 받은 링크 그대로."""
    m = RCP_IN_LINK.search(link or "") if isinstance(link, str) and "dart.fss.or.kr/dsaf001" in link else None
    if not m:
        return link
    try:
        return _dart_consol_url(m.group(1)) or link
    except Exception:
        return link


def prefetch_consol(links):
    """표에 넣을 링크들의 연결재무제표 위치를 한꺼번에(병렬로) 미리 찾아 둔다 (같은 보고서는 일주일 동안 저장)"""
    todo = list(dict.fromkeys(m.group(1) for l in links if isinstance(l, str) and "dart.fss.or.kr/dsaf001" in l for m in [RCP_IN_LINK.search(l)] if m))
    if len(todo) > 1:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda r: consol_link(DART_VIEW_URL.format(r)), todo))


# ------------------------------------------------------------
# 연도별 / 분기별 수집
# ------------------------------------------------------------
ALT_REV_LABELS = ("매출액", "영업수익", "수익(매출액)")
ALT_OP_LABELS = ("영업이익", "영업손익", "영업손실")
ALT_NET_LABELS = ("당기순이익", "당기순손익", "당기순손실", "분기순이익", "분기순손익", "반기순이익", "반기순손익", "연결당기순이익")
ALT_HEAD = re.compile(r"^(?:[가-하]\.)?(?:연결)?(?:포괄)?손익계산서\.{0,}$")
SUMMARY_PERIOD = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*~\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})")
ALT_STOP = re.compile(r"현금흐름표|자본변동표|이익잉여금처분계산서|결손금처리계산서|재무상태표")


def parse_income_table(text):
    """보고서 원문 글자 -> {"rev": [...], "op": [...], "net": [...]} (원 단위, 표의 열 순서대로. '-'는 None) 또는 None.
    DART 재무 API에 자료가 없는 기업(일반기업회계기준 등)용. '손익계산서' 제목 아래 표에서 매출액·영업이익·당기순이익 줄을 찾는다.
    요약재무정보 표는 제목이 달라 건너뛰고, 번호(Ⅰ. 1.)는 떼고 이름을 비교한다. 단위는 줄 앞쪽의 가장 가까운 '(단위 : ...)'."""
    lines = text.split("\n")
    unit_at, unit = [], 1
    for ln in lines:
        m = re.search(r"단위\s*[:：]?[\s|(]*([가-힣]+)", ln)
        if m and m.group(1) in PROV_UNITS:
            unit = PROV_UNITS[m.group(1)]
        unit_at.append(unit)
    for i, ln in enumerate(lines):
        head = re.sub(r"\s", "", ln.split("|")[0])
        if not ALT_HEAD.match(head):
            continue
        found = {}
        for j in range(i + 1, min(i + 120, len(lines))):
            cells = [c.strip() for c in lines[j].split("|") if c.strip()]
            if not cells:
                continue
            compact = re.sub(r"\s", "", cells[0])
            if j > i + 3 and found and ALT_STOP.search(compact) and len(cells) <= 2:
                break
            if len(cells) < 3:
                continue
            label = re.sub(r"^[\dIVXⅠ-Ⅻ.()\-]+", "", compact)
            vals = []
            for c in cells[1:]:
                v = parse_amount_cell(c)
                if v is not None:
                    vals.append(v * unit_at[j])
                elif c in ("-", "－", "—"):
                    vals.append(None)
            if sum(v is not None for v in vals) < 1:
                continue
            for key, names in (("rev", ALT_REV_LABELS), ("op", ALT_OP_LABELS), ("net", ALT_NET_LABELS)):
                if key not in found and label.startswith(names) and "대비" not in label and "률" not in label:
                    found[key] = vals
        if found.get("rev") or found.get("op"):
            return found
    return parse_summary_flat(lines, unit_at)


def parse_summary_flat(lines, unit_at):
    """칸마다 줄이 나뉜 원문의 '요약 손익계산서'(예: 중앙일보) -> parse_income_table과 같은 모양 또는 None.
    제목 줄 뒤 칸들을 이어서, 항목 이름 칸 바로 뒤에 나오는 숫자 칸들(당기·전기·전전기)을 읽는다."""
    for i, ln in enumerate(lines):
        if "요약" not in ln or "손익계산서" not in ln or len(ln.split("|")[0]) > 40:
            continue
        cells, owner = [], []
        for j in range(i + 1, min(i + 80, len(lines))):
            for c in lines[j].split("|"):
                if c.strip():
                    cells.append(c.strip())
                    owner.append(j)
        found, k = {}, 0
        hdr = []  # 이름 칸이 나오기 전의 열 제목들에서 기간 (시작연, 시작월, 끝연, 끝월)
        while k < len(cells) and not re.sub(r"\s", "", cells[k]).startswith(ALT_REV_LABELS + ALT_OP_LABELS + ALT_NET_LABELS):
            m = SUMMARY_PERIOD.search(cells[k])
            if m:
                hdr.append(tuple(int(m.group(g)) for g in (1, 2, 4, 5)))
            k += 1
        k = 0
        while k < len(cells):
            label = re.sub(r"^[\dIVXⅠ-Ⅻ.()\-]+", "", re.sub(r"\s", "", cells[k]))
            key = next((key for key, names in (("rev", ALT_REV_LABELS), ("op", ALT_OP_LABELS), ("net", ALT_NET_LABELS))
                        if key not in found and label.startswith(names) and "대비" not in label and "률" not in label), None)
            k += 1
            if not key:
                continue
            vals = []
            while k < len(cells) and len(vals) < 6:
                v = parse_amount_cell(cells[k])
                if v is not None:
                    vals.append(v * unit_at[owner[k]])
                elif cells[k] in ("-", "－", "—"):
                    vals.append(None)
                else:
                    break
                k += 1
            if any(v is not None for v in vals):
                found[key] = vals
        if found.get("rev") or found.get("op"):
            if hdr:
                found["hdr"] = hdr
            return found
    return None


def summary_columns(got):
    """parse_income_table 결과에 열 제목 기간이 있으면 -> [(끝 연도, 끝 월, 개월 수, {rev, op, net})] (없으면 None).
    3개월짜리 열이 없는 회사(반기·3분기 보고서가 누적만 싣는 곳)도 열 기간으로 구분할 수 있다."""
    hdr = got.get("hdr")
    if not hdr:
        return None
    return [(ey, em, (ey - sy) * 12 + em - sm + 1, {k: alt_col(got, k, i) for k in ("rev", "op", "net")}) for i, (sy, sm, ey, em) in enumerate(hdr)]


def alt_col(got, key, i):
    v = got.get(key) or []
    return v[i] if len(v) > i else None


def alt_report_doc(corp_code, year, reprt, session, store):
    """정기보고서 원문의 손익계산서 표(parse_income_table) + 원문 링크. 없으면 (None, None)."""
    try:
        rno = find_periodic_rcept(corp_code, year, reprt)
        if not rno:
            return None, None
        return parse_income_table(fetch_document_text(session, store, rno)), DART_VIEW_URL.format(rno)
    except (DartApiError, AiError):
        return None, None


def is_empty(cur):
    return all(cur is None or cur.get(k) is None for k in ("rev", "op", "net"))


def collect_alt_yearly(corp_code, years, data):
    """연결 재무제표 API에 자료가 없는 연도를 채운다. 1) 별도(OFS) API 2) 사업보고서 원문 표.
    -> {연도: 'ofs'|'doc'} (어떤 방법으로 채웠는지). data를 그 자리에서 고친다."""
    session, store = get_session(), get_cache_store()
    how = {}
    missing = [y for y in years if is_empty(data[y])]
    for y in missing:  # 1) 연결재무제표가 없는 기업은 별도 재무제표로 조회
        try:
            df = fetch(session, store, corp_code, y, ANNUAL, fs_div="OFS")
        except DartApiError:
            continue
        cur = {"rev": pick(df, REVENUE_NAMES, ids=REVENUE_IDS), "op": pick(df, OPERATING_NAMES, ids=OPERATING_IDS),
               "net": pick(df, NET_NAMES, ids=NET_IDS), "link": link_of(df), "bs": bs_values(df, with_capex=True)}
        if not is_empty(cur):
            data[y] = cur
            how[y] = "ofs"
    left = sorted((y for y in missing if y not in how), reverse=True)
    docs = 0
    for y in left:  # 2) 원문: 한 보고서에 당기·전기·전전기가 함께 있으니 최신 연도부터 읽어 같이 채운다
        if not is_empty(data[y]) or docs >= 4:
            continue
        docs += 1
        got, link = alt_report_doc(corp_code, y, ANNUAL, session, store)
        if not got:
            continue
        for i in range(3):
            yy = y - i
            if yy not in data or not is_empty(data[yy]):
                continue
            vals = {k: alt_col(got, k, i) for k in ("rev", "op", "net")}
            if all(v is None for v in vals.values()):
                continue
            data[yy] = {**vals, "link": link, "bs": None}
            how[yy] = "doc"
    return how


def collect_alt_quarterly(corp_code, data, start, end):
    """분기 자료가 비는 곳을 정기보고서 원문 표로 채운다. data[(연,분기)]를 그 자리에서 고치고 {(연,분기): 'doc'}을 돌려준다.
    열 구성: 1분기 [당분기, 전년 1분기, 전기 연간, ...] / 반기·3분기 [3개월, 누적, 전년 3개월, 전년 누적, 전기 연간, ...] / 사업 [당기, 전기, 전전기].
    4분기 = 사업보고서 연간 - 3분기 누적. 한 보고서에 전년도 같은 분기도 있으니 두 해가 함께 채워진다."""
    session, store = get_session(), get_cache_store()
    how, docs = {}, 0
    cum_all = {}  # 열 제목 기간으로 읽은 값: {(끝 연도, 끝 월, 개월 수): (값, 원문 링크)}

    def take_columns(got, link):
        cols = summary_columns(got)
        for yy, em, months, vals in cols or []:
            cum_all.setdefault((yy, em, months), (vals, link))
        return bool(cols)

    def put(y, q, vals, link):
        key = (y, q)
        if key in data and is_empty(data[key]) and any(v is not None for v in vals.values()):
            data[key] = {**vals, "link": link, "bs": None}
            how[key] = "doc"

    def vals_at(got, i):
        return {k: alt_col(got, k, i) for k in ("rev", "op", "net")}

    for y in range(start, end + 1):
        if y < TODAY.year - 4:
            continue
        need = {q: [yy for yy in (y, y - 1) if (yy, q) in data and is_empty(data[(yy, q)])] for q in (1, 2, 3, 4)}
        cum9 = {}
        for q, reprt in ((1, Q1), (2, H1), (3, Q3)):
            if not need[q] and not (q == 3 and need[4]):
                continue
            if docs >= 12:
                break
            docs += 1
            got, link = alt_report_doc(corp_code, y, reprt, session, store)
            if not got:
                continue
            if take_columns(got, link):  # 열 제목에 기간이 있으면 아래 합산·차감으로 한꺼번에 채운다
                continue
            n = max(len(got.get("rev") or []), len(got.get("op") or []), len(got.get("net") or []))
            if q == 1:
                put(y, 1, vals_at(got, 0), link)
                put(y - 1, 1, vals_at(got, 1), link)
            elif n >= 4:
                put(y, q, vals_at(got, 0), link)
                put(y - 1, q, vals_at(got, 2), link)
                if q == 3:
                    cum9 = {y: vals_at(got, 1), y - 1: vals_at(got, 3)}
        if need[4] and docs < 12:  # 4분기 = 연간 - 3분기 누적
            docs += 1
            got, link = alt_report_doc(corp_code, y, ANNUAL, session, store)
            if got and take_columns(got, link):
                pass
            elif got:
                for i, yy in ((0, y), (1, y - 1)):
                    if yy in cum9:
                        ann, cum = vals_at(got, i), cum9[yy]
                        put(yy, 4, {k: (ann[k] - cum[k] if ann[k] is not None and cum[k] is not None else None) for k in ("rev", "op", "net")}, link)

    def minus(a, b):
        return {k: (a[k] - b[k] if a.get(k) is not None and b.get(k) is not None else None) for k in ("rev", "op", "net")}

    for yy in sorted({k[0] for k in cum_all}):  # 열 기간으로 읽은 값에서 분기 만들기: 3개월 열이 있으면 그대로, 없으면 누적에서 앞 분기를 뺀다
        def col(em, months):
            hit = cum_all.get((yy, em, months))
            return hit if hit else (None, None)

        def have(q):
            cur = data.get((yy, q))
            return cur if cur and not is_empty(cur) else None

        q1, l1 = col(3, 3)
        q1 = q1 or have(1)
        h1, l2 = col(6, 6)
        q2, l2b = col(6, 3)
        q3, l3b = col(9, 3)
        n9, l3 = col(9, 9)
        ann, l4 = col(12, 12)
        if q1:
            put(yy, 1, q1, l1 or "")
        if q2 or (h1 and q1):
            put(yy, 2, q2 or minus(h1, q1), l2b or l2)
        cum6 = h1 or (minus(q2, {k: (-q1[k] if q1[k] is not None else None) for k in q1}) if q1 and q2 else None)  # 2분기 3개월 열만 있으면 1분기와 더해 상반기
        if q3 or (n9 and cum6):
            put(yy, 3, q3 or minus(n9, cum6), l3b or l3)
        if ann and n9:
            put(yy, 4, minus(ann, n9), l4)
    return how


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
    how = {}
    try:  # 연결 자료가 비는 연도(연결재무제표가 없거나 API에 자료가 없는 기업)는 별도 API·원문 표로 채운다
        how = collect_alt_yearly(corp_code, list(years), data)
    except Exception:
        how = {}

    records = []
    for y in range(start, end + 1):
        cur = data[y]
        if cur["rev"] is None and cur["op"] is None and cur["net"] is None:
            continue
        rec = make_record(f"{y}†" if y in how else str(y), (y, 0), cur, yoy=data[y - 1])
        if y in how:
            rec["alt"] = how[y]
        records.append(rec)
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


# ------------------------------------------------------------
# 잠정실적: 분기보고서가 나오기 전, '영업(잠정)실적(공정공시)'에서 매출액·영업이익만 읽어 분기별 표에 보태 보여준다.
# ------------------------------------------------------------
PROV_UNITS = {"원": 1, "천원": 10**3, "백만원": 10**6, "억원": 10**8, "십억원": 10**9, "조원": 10**12}
PROV_REV_LABELS = ("매출액", "영업수익", "수익(매출액)")
PROV_OP_LABELS = ("영업이익", "영업손익", "영업손실")


def parse_amount_cell(cell):
    """표 칸 -> 숫자. '1,234', '(1,234)', '△1,234', '-1,234'는 마이너스 처리. 숫자가 아니면 None."""
    t = str(cell).strip().replace(" ", "")
    m = re.fullmatch(r"([\(\-△▲]?)([\d,]+(?:\.\d+)?)\)?", t)
    if not m or not re.search(r"\d", m.group(2)):
        return None
    v = float(m.group(2).replace(",", ""))
    return -v if m.group(1) else v


def parse_provisional(text, rcept_dt):
    """영업(잠정)실적 공시 원문 글자 -> {"rev": 원, "op": 원, "year", "quarter"} 또는 None.
    표의 '매출액'·'영업이익' 줄에서 맨 앞 숫자(당해 분기 실적)를 읽는다. 단위·분기를 확인하지 못하면 None."""
    mult = None
    for m in re.finditer(r"단위\s*[:：]?[\s|(]*([가-힣]+)", text):  # '단위 : 백만원', '(단위: 억원)', '단위 | 백만원' 모두
        if m.group(1) in PROV_UNITS:
            mult = PROV_UNITS[m.group(1)]
            break
    if not mult:
        return None
    rev = op = None
    # 칸 단위로 펼쳐서 읽는다: 줄 나뉨·병합 칸이 달라도 '라벨 칸 뒤 첫 숫자'를 당해 분기 실적으로 본다
    flat = [c.strip() for c in re.split(r"[|\n]", text) if c.strip()]
    for i, cell in enumerate(flat):
        label = re.sub(r"[\s\d.\)]", "", cell) if len(cell) < 14 else ""
        if not label or "대비" in label or "비율" in label:
            continue
        kind = "rev" if label.startswith(PROV_REV_LABELS) else "op" if label.startswith(PROV_OP_LABELS) else None
        if kind is None or (kind == "rev" and rev is not None) or (kind == "op" and op is not None):
            continue
        for nxt in flat[i + 1:i + 7]:
            if nxt.replace(" ", "") in ("누계실적", "누적실적"):
                break  # 당해 줄이 없고 누계만 있으면 쓰지 않는다
            v = parse_amount_cell(nxt)
            if v is not None:
                if kind == "rev":
                    rev = v * mult
                else:
                    op = v * mult
                break
        if rev is not None and op is not None:
            break
    if rev is None and op is None:
        return None
    month = int(str(rcept_dt)[4:6])
    year = int(str(rcept_dt)[:4])
    head = text[:4000]
    tag = re.search(r"당기실적\s*\(\s*'?(\d{2})\.\s*([1-4])\s*Q", head) or re.search(r"'(\d{2})\.\s*([1-4])\s*Q", head)
    period = re.search(r"(20\d\d)-(\d\d)-\d\d\s*\|?\s*~\s*\|?\s*(20\d\d)-(\d\d)-\d\d", head)
    if tag:  # 열 제목 "당기실적 ('26.3Q)"
        return {"rev": rev, "op": op, "year": 2000 + int(tag.group(1)), "quarter": int(tag.group(2))}
    if period and period.group(1) == period.group(3) and period.group(2) in ("01", "04", "07", "10"):
        # 실적기간 "2026-07-01 ~ 2026-09-30" (당기 3개월 구간일 때만)
        if int(period.group(4)) - int(period.group(2)) == 2:
            return {"rev": rev, "op": op, "year": int(period.group(1)), "quarter": int(period.group(4)) // 3}
    found = re.search(r"([1-4])\s*분기", head)
    if found:
        quarter = int(found.group(1))
    else:  # 원문에 분기 글자가 없으면 접수 시기로 짐작 (1분기 4~5월, 반기 7~8월, 3분기 10~11월, 연간 1~3월)
        quarter = {1: 4, 2: 4, 3: 4, 4: 1, 5: 1, 7: 2, 8: 2, 10: 3, 11: 3}.get(month)
        if quarter is None:
            return None
    if quarter == 4 and month <= 6:
        year -= 1  # 연간(4분기) 잠정실적은 이듬해 1~3월에 나온다
    return {"rev": rev, "op": op, "year": year, "quarter": quarter}


def quarter_end(year, quarter):
    return date(year, quarter * 3, 30 if quarter in (2, 3) else 31)


def collect_provisional(corp_code, data, start, end):
    """분기보고서가 아직 없는 분기 중 영업(잠정)실적 공시가 있는 분기를 {(연, 분기): {rev, op, link}}로 돌려준다.
    연결재무제표기준 공시만 쓰고(표는 연결 기준), 같은 분기 공시가 여럿이면 가장 최근 것을 쓴다."""
    missing = [(y, q) for y in range(start, end + 1) for q in range(1, 5)
               if y >= TODAY.year - 1 and quarter_end(y, q) <= TODAY
               and all(data.get((y, q), {}).get(k) is None for k in ("rev", "op", "net"))]
    if not missing:
        return {}, ""
    since = max(date(min(y for y, _ in missing), 1, 1), TODAY - timedelta(days=400))
    rows, _, _ = collect_disclosures(corp_code, since, TODAY, "I")  # 거래소공시(공정공시 포함), 최신순
    session, store = get_session(), get_cache_store()
    out, cands, errors = {}, 0, []
    for r in rows:
        name = disclosure_key(r.get("report_nm", ""))
        if "영업잠정실적" not in name or "연결" not in name:
            continue
        cands += 1
        try:
            doc = fetch_document_text(session, store, r["rcept_no"])
            got = parse_provisional(doc, r.get("rcept_dt", ""))
        except (AiError, KeyError, ValueError) as e:
            errors.append(str(e))
            continue
        if got is None:
            at = doc.find("매출액")
            errors.append("원문 표에서 매출액·영업이익 또는 단위를 찾지 못함 "
                          f"[원문 {len(doc)}자, 단위 {'있음' if '단위' in doc else '없음'}, "
                          f"매출액 주변: {doc[max(at - 20, 0):at + 80].replace(chr(10), ' / ') if at >= 0 else '없음'}]")
            continue
        key = (got["year"], got["quarter"])
        if key in missing and key not in out:  # 최신 공시가 먼저 오므로 처음 읽은 것이 가장 최근
            out[key] = {"rev": got["rev"], "op": got["op"], "link": DART_VIEW_URL.format(r["rcept_no"])}
    if out:
        return out, ""
    if not cands:
        return out, "연결 영업(잠정)실적 공시를 찾지 못했어요"
    return out, f"연결 영업(잠정)실적 공시 {cands}건을 찾았지만 읽지 못했어요 ({errors[0] if errors else '이미 확정 보고서가 있는 분기예요'})"


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

    how = {}
    try:  # API에 자료가 없는 분기(연결재무제표가 없거나 일반기업회계기준 회사)는 정기보고서 원문 표로 채운다
        how = collect_alt_quarterly(corp_code, data, start, end)
    except Exception:
        how = {}

    records = []
    for y in range(start, end + 1):
        for q in range(1, 5):
            cur = data[(y, q)]
            if cur["rev"] is None and cur["op"] is None and cur["net"] is None:
                continue
            qoq = data[(y, q - 1)] if q > 1 else data[(y - 1, 4)]
            rec = make_record(f"{y} {q}Q†" if (y, q) in how else f"{y} {q}Q", (y, q), cur, yoy=data[(y - 1, q)], qoq=qoq)
            if (y, q) in how:
                rec["alt"] = "doc"
            records.append(rec)
    st.session_state["fin_prov_note"] = ""
    try:  # 분기보고서 전 단계: 영업(잠정)실적 공시가 있으면 매출액·영업이익만 보태고 '*'로 표시
        found, why = collect_provisional(corp_code, data, start, end)
        if why:
            st.session_state["fin_prov_note"] = "잠정실적: " + why
        for (y, q), v in sorted(found.items()):
            cur = {"rev": v["rev"], "op": v["op"], "net": None, "link": v["link"], "bs": None}
            qoq = data.get((y, q - 1)) if q > 1 else data.get((y - 1, 4))
            rec = make_record(f"{y} {q}Q*", (y, q), cur, yoy=data.get((y - 1, q)), qoq=qoq)
            rec["prov"] = True
            records.append(rec)
    except Exception as e:  # 잠정실적을 못 읽어도 확정 실적 표는 그대로 보여준다
        st.session_state["fin_prov_note"] = "잠정실적 공시를 확인하지 못했어요 (확정 실적만 표시)" + (f": {e}" if isinstance(e, (DartApiError, AiError)) else "")
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
    prefetch_consol([r.get("link") for r in records])
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
        row["원문"] = consol_link(r["link"])
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
    elif preset in EXTRA_DISCLOSURES:
        words = EXTRA_DISCLOSURES[preset]
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
                "net": pick(df, NET_NAMES, ids=NET_IDS), "link": link_of(df)}
    r = [reports.get(c) for c in (Q1, H1, Q3, ANNUAL)]
    return {
        "rev": quarter_values(*r, REVENUE_NAMES, ids=REVENUE_IDS)[3],
        "op": quarter_values(*r, OPERATING_NAMES, ids=OPERATING_IDS)[3],
        "net": quarter_values(*r, NET_NAMES, ids=NET_IDS)[3],
        "link": link_of(reports.get(ANNUAL)),  # 4분기는 연간(사업)보고서로 연결
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
    if period in ("1Q", "2Q", "3Q", "4Q"):  # 분기보고서 전 단계: 영업(잠정)실적 공시가 있으면 매출액·영업이익만 보태고 '*'로 표시
        q = int(period[0])
        empty = {"rev": None, "op": None, "net": None}
        for name, code in corps:
            now = out[name]["now"]
            if any(now[k] is not None for k in ("rev", "op", "net")):
                continue
            try:
                filled = {(year, i): {"rev": 0, "op": 0, "net": 0} for i in range(1, 5) if i != q}  # 다른 분기는 조회 대상이 아님
                found, _ = collect_provisional(code, {**filled, (year, q): empty}, year, year)
            except Exception:  # 잠정실적을 못 읽어도 나머지 결과는 그대로
                continue
            if (year, q) in found:
                v = found[(year, q)]
                out[name]["now"] = {"rev": v["rev"], "op": v["op"], "net": None, "link": v["link"], "prov": True}
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
            "회사": corp + ("*" if now.get("prov") else ""),
            "매출액": fmt_amount(now["rev"], unit), "매출 전년比": fmt_change(pct_change(now["rev"], prev["rev"])),
            "영업이익": fmt_amount(now["op"], unit), "영업이익 전년比": fmt_change(op_change(now["op"], prev["op"])),
            "영업이익률": f"{margin:.1f}%" if margin is not None else "-",
            "순이익": fmt_amount(now["net"], unit), "순이익 전년比": fmt_change(op_change(now["net"], prev["net"])),
            "원문": now.get("link"),
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
            "원문": None,
        })
    return pd.DataFrame(rows), total, used


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


NAVER_MIN_GAP = 0.12  # 네이버 호출 사이 최소 간격(초): 초당 약 10건 제한에 걸리지 않게 동시 호출을 한 줄로 세운다
_naver_gate = {"lock": threading.Lock(), "next": 0.0}


def naver_wait():
    """모든 스레드가 같이 쓰는 호출 간격 조절: 다음 호출 가능 시각까지 기다린다."""
    with _naver_gate["lock"]:
        now = time.monotonic()
        at = max(now, _naver_gate["next"])
        _naver_gate["next"] = at + NAVER_MIN_GAP
    if at > now:
        time.sleep(at - now)


def call_naver(session, params):
    """네이버 뉴스 검색 API 호출 공통 처리. 정상이면 응답(dict), 문제가 있으면 NewsApiError."""
    headers = {"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET}
    res, last_error = None, None
    for attempt in range(5):  # 네트워크 오류·호출 몰림(429)이면 쉬었다 다시 시도 (최대 5번)
        try:
            naver_wait()
            res = session.get(NAVER_NEWS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            if res.status_code == 429 and attempt < 4:
                time.sleep(0.6 * (attempt + 1))
                continue
            break
        except Exception as e:
            last_error = e
            res = None
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
    if any(host == b or host.endswith("." + b) for b in PUBLISHER_BLOCK):
        return host, None  # 목록에 없는 곳과 같게 취급 -> 뉴스 결과에서 빠진다
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


def search_news(terms, days, keyword=None, suffixes=("",)):
    """검색어마다 '검색어 단독'으로 동시에 검색해 합친다.

    -> (기사 목록, 한도에 걸렸는지, 한도에 걸린 검색에서 빠짐없이 본 가장 이른 시각)
    같은 기사가 여러 검색에 걸려도 한 번만 담는다. 같은 검색은 30분('오늘'은 10분) 동안 저장해 둔 결과를 쓴다.
    """
    session, store, today = get_session(), get_cache_store(), TODAY.isoformat()  # 화면 스레드에서 미리 준비
    keyword = NEWS_KEYWORD if keyword is None else keyword  # 빈 문자열이면 단독 표기 없이 일반 기사까지

    def one(job):
        term, suffix = job
        query = " ".join(x for x in (term, keyword, suffix) if x)
        key = ("news", query, days, today)
        hit = cache_lookup(store, key, NEWS_TTL_TODAY if days <= 1 else NEWS_TTL)
        if hit is not _MISSING:
            return hit
        value = fetch_news(session, query, days, today)
        cache_save(store, key, value)
        return value

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(one, [(t, x) for t in terms for x in suffixes]))  # 하나라도 실패하면 그 오류(NewsApiError)를 그대로 올림
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


WIRE_DOMAINS = ("yna.co.kr", "newsis.com", "news1.kr")  # 통신 모드의 언론사: 연합뉴스(첫째)·뉴시스·뉴스1. 기본은 연합만, 체크를 풀면 전부
GNEWS_SITE_CHUNK = 8  # 구글 검색어에 한 번에 넣는 site: 개수 (검색어가 너무 길면 구글이 잘라내므로 언론사를 나눠 검색)
NEWS_GOOGLE_MIN = 5  # 단독 모드: 구글 뉴스에서 찾은 단독 기사가 이보다 적으면 네이버로 보충


def _chunks(seq, n):
    seq = list(seq)
    return [tuple(seq[i:i + n]) for i in range(0, len(seq), n)]


def google_news(terms, days, domains, keyword=""):
    """구글 뉴스(한국어)로 지정 언론사 도메인의 기사를 찾는다. -> (기사 목록, 오류 메시지 또는 None)

    언론사가 많으면 GNEWS_SITE_CHUNK개씩 나눠 검색한다. 일부만 실패하면 받은 것만 쓰고, 전부 실패했을 때만 오류.
    """
    session, store, today = get_session(), get_cache_store(), TODAY.isoformat()
    groups = _chunks(domains, GNEWS_SITE_CHUNK)

    def one(job):
        term, group = job
        key = ("gnk", term, keyword, days, group, today)
        hit = cache_lookup(store, key, NEWS_TTL_TODAY if days <= 1 else NEWS_TTL)
        if hit is not _MISSING:
            return hit, None
        try:
            value = gnews_fetch(session, term, days, group, in_title=False, ko=True, extra=keyword)
        except Exception as e:
            return [], (str(e) if isinstance(e, NewsApiError) else "구글 뉴스에서 기사를 받지 못했어요. 잠시 후 다시 시도해주세요.")
        cache_save(store, key, value)
        return value, None

    jobs = [(t, g) for t in terms for g in groups]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        got = list(pool.map(one, jobs))
    errs = [e for _, e in got if e]
    if errs and len(errs) == len(got):
        return [], errs[0]
    out, seen = [], set()
    for lst, _ in got:
        for it in lst:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            name, category = publisher_of(f"https://{it['domain']}")
            out.append({"title": it["title"], "url": it["url"], "publisher": name, "category": category, "dt": it["dt"]})
    return out, None


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


# 통신 기사에서 뺄 스포츠 낱말. 기업명(예: 현대모비스 농구단, LG 농구팀)이 들어간 스포츠 기사가 기업 기사로 잡히는 것을 막는다.
SPORTS_WORDS = ("농구", "야구", "축구", "배구", "프로골프", "골프대회", "K리그", "KBO", "레이싱", "연패", "연승", "e스포츠")


NEWS_BLOCK_TERMS = ("현대차증권", "기아대책")  # 검색어 '현대차'·'기아'에 걸리지만 다른 회사·단체의 기사라서 아예 뺀다


def is_blocked(title):
    """제목에 차단 낱말이 있는지 (뉴스 전체에 적용: 검색·브리핑·알람·타임라인)."""
    return any(t in str(title) for t in NEWS_BLOCK_TERMS)


def drop_blocked(items):
    return [i for i in items if not is_blocked(i["title"])]


SPORTS_TITLES_URL = "https://raw.githubusercontent.com/brucerue/DART-Joongang/sports-titles/sports_titles.json"  # 연합뉴스 스포츠·전국 RSS 제목 (매시간, 30일)


def is_sports(title, feed_keys=()):
    """스포츠·전국(지방 소식) 기사인지 (통신 기사 제외용). 낱말 목록에 걸리거나, 연합뉴스 스포츠·전국 RSS에 실린 제목이면 True.
    feed_keys: 스포츠 RSS 제목의 비교용 글자(title_key). 구글 뉴스 제목 끝에 '- 연합뉴스'가 붙어도 앞부분으로 맞춘다."""
    t = str(title).lower()
    if any(w.lower() in t for w in SPORTS_WORDS):
        return True
    k = title_key(title)
    return any(len(fk) >= 8 and k.startswith(fk) for fk in feed_keys)


@st.cache_data(ttl=3600, show_spinner=False)
def load_sports_keys():
    """GitHub에 모아 둔 스포츠 RSS 제목 목록 -> 비교용 글자 목록. 못 읽으면 빈 목록(낱말 목록만 쓴다)."""
    try:
        res = requests.get(SPORTS_TITLES_URL, timeout=(10, 30))
        res.raise_for_status()
        return tuple(it["k"] for it in res.json().get("items", []) if it.get("k"))
    except Exception:
        return ()


def title_key(title):
    """제목 비교용: 기호·공백을 지우고 소문자로."""
    return re.sub(r"[^0-9a-z가-힣]", "", title.lower())


def news_tokens(title):
    """제목 -> 핵심 낱말 목록 ([단독]·[속보]·(종합) 같은 꼬리표와 흔한 말은 뺌, 조사는 떼어냄)"""
    stop = {"종합", "속보", "영상", "단독", "포토", "사진", "오늘", "올해", "관련", "대한", "위해", "통해", "이후", "지난", "최근", "전망", "예정", "발표", "확대", "추진", "개최", "진행"}
    t = re.sub(r"\[[^\]]*\]|\([^)]*종합[^)]*\)|\(종합\d*보?\)|\d보", " ", str(title))
    out = []
    for w in re.findall(r"[A-Za-z0-9가-힣＆&\.]+", t):
        w = re.sub(r"(에서|으로|에게|까지|부터|이다|했다|하는|한다|에는|에도|보다|처럼|으로는|이며|이고)$", "", w)
        if len(w) >= 3:
            w = re.sub(r"(은|는|이|가|을|를|의|에|도|로|과|와|서)$", "", w)
        if len(w) >= 2 and w not in stop:
            out.append(w)
    return out


def same_news_event(a, b):
    """두 기사(제목·tags)가 같은 사건인지: 핵심 낱말이 충분히 겹치면 True.
    - 앞머리 낱말(주로 회사)이 같고 3개 이상·절반 가까이 겹치거나, 4개 이상·절반 이상 겹침
    - 검색어 꼬리표(tags)가 둘 다 있는데 하나도 안 겹치면 다른 회사 기사라 False(실적 기사처럼 틀이 같은 제목이 섞이는 것 방지)"""
    ta, tb = news_tokens(a["title"]), news_tokens(b["title"])
    sa, sb = set(ta), set(tb)
    shared = sa & sb
    if not shared:
        return False
    if a.get("tags") and b.get("tags") and not set(a["tags"]) & set(b["tags"]):
        return False
    jac = len(shared) / len(sa | sb)
    first = bool(ta and tb and ta[0] == tb[0])
    return (len(shared) >= 3 and jac >= 0.4 and first) or (len(shared) >= 4 and jac >= 0.5)


def cluster_news(items):
    """제목이 비슷한 기사를 한 묶음으로. -> [{"rep": 대표 기사, "others": 나머지 기사들}, ...] (최신순)

    대표 기사는 묶음 안에서 가장 최근 기사.
    """
    def is_share(it):  # 동아일보 '퍼가기' 사본 등: 한 번 더 눌러야 본문이 나오는 공유용 페이지
        return "shareiframe" in (it.get("url") or "") or bool(re.search(r"[|ㅣ｜]\s*퍼가기\s*$", it.get("title") or ""))

    clean = []
    for it in items:
        if is_share(it):  # 제목 끝 'ㅣ퍼가기' 표시는 떼어 내고, 바로 열리는 기사를 대표로 앞세우기 위해 표시해 둔다
            it = {**it, "title": re.sub(r"\s*[|ㅣ｜]\s*퍼가기\s*$", "", it["title"]), "_share": True}
        clean.append(it)
    clusters = []
    # 바로 열리는 기사 먼저(최신순), 그다음 공유용 사본(최신순) -> 같은 묶음이면 바로 열리는 기사가 대표가 된다
    for item in sorted(clean, key=lambda x: (bool(x.get("_share")), -x["dt"].timestamp())):
        key = title_key(item["title"])
        for c in clusters:
            m = c["matcher"]
            m.set_seq1(key)
            if (m.real_quick_ratio() >= SIMILAR_TITLE and m.quick_ratio() >= SIMILAR_TITLE and m.ratio() >= SIMILAR_TITLE) or same_news_event(item, c["items"][0]):
                c["items"].append(item)
                break
        else:
            matcher = difflib.SequenceMatcher(None, autojunk=False)
            matcher.set_seq2(key)  # 묶음의 첫 제목을 기준으로 삼는다 (한 번만 분석해 두면 빠르다)
            clusters.append({"matcher": matcher, "items": [item]})
    out = []
    for c in clusters:
        out.append({"rep": c["items"][0], "others": sorted(c["items"][1:], key=lambda x: x["dt"], reverse=True)})
    out.sort(key=lambda c: c["rep"]["dt"], reverse=True)  # 묶음 순서는 대표 기사 기준 최신순
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
    lines = [f"[{res.get('kind', '단독')} 뉴스] {res['corp']} · 최근 {res['period']} ({now:%m.%d %H:%M} 기준)", ""]
    for c in res["clusters"]:
        it = c["rep"]
        tags = f" ({', '.join(it['tags'])})" if it.get("tags") else ""
        lines.append(f"{it['title']} · {it['publisher']}{tags}")
        lines.append(it["url"])
        lines.append("")
    return "\n".join(lines).strip()


def apply_watchlist():
    """주소 뒤 관심 검색어(?news=삼성전자,현대차&nmode=통신)를 처음 한 번만 입력칸에 채우고 자동 조회.
    산업 뉴스 탭을 먼저 열어야 하면 True."""
    qp = st.query_params
    terms, kind = qp.get("news", "").strip(), qp.get("nmode", "").strip()
    if st.session_state.get("watch_applied"):
        return False
    st.session_state["watch_applied"] = True
    if not terms:
        return False
    kind = kind if kind in NEWS_KINDS else "단독"
    st.session_state["news_mode"] = kind
    st.session_state[f"nw_{kind}_terms"] = terms
    st.session_state["news_run"] = True
    return True


def save_watchlist(res):
    """조회한 검색어를 주소 뒤에 붙여 둔다 -> 그 주소를 북마크하면 다음에 바로 조회."""
    try:
        st.query_params["news"] = res["corp"]
        st.query_params["nmode"] = res["kind"]
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
    "케이티": ["KT"],  # DART 등록명은 '케이티'. KT로 쳐도 바로 인식
    "삼성전자": ["삼전"],
    "LG에너지솔루션": ["LG엔솔", "엔솔"],
    "SK하이닉스": ["SK하닉", "하닉", "하이닉스"],
    "삼성바이오로직스": ["삼바", "삼성바이오"],
    "한국전력공사": ["한전", "한국전력"],
    "현대모비스": ["모비스"],
    "SK이노베이션": ["SK이노"],
    "LG유플러스": ["LG유플"],
    "에이치디현대오일뱅크": ["오일뱅크", "HD현대오일뱅크"],
    "HD한국조선해양": ["한국조선해양", "한조"],
    "삼성화재해상보험": ["삼성화재"],
    "신한지주": ["신한금융", "신한금융지주"],
    "하나금융지주": ["하나금융"],
    "우리금융지주": ["우리금융"],
    "KB금융": ["KB금융지주", "KB금융그룹"],
    "아모레퍼시픽": ["아모레"],
    "한국타이어앤테크놀로지": ["한국타이어"],
    "기업은행": ["IBK기업은행", "IBK"],
    "SK텔레콤": ["SKT"],
    "에스케이온": ["SK온"],
    "한화에어로스페이스": ["한화에어로"],
    "한국항공우주산업": ["한국항공우주", "KAI"],
    "LIG디펜스앤에어로스페이스": ["LIG넥스원"],
    "S-Oil": ["에쓰오일", "에스오일", "에쓰-오일"],
    "NAVER": ["네이버"],
    "엘에스일렉트릭": ["LS ELECTRIC", "LS일렉트릭", "LS산전"],  # DART 등록명은 '엘에스일렉트릭'
    "POSCO홀딩스": ["포스코홀딩스", "포스코"],
    "KT&G": ["케이티앤지", "KT앤G"],
    "F&F": ["에프앤에프"],
    "SOOP": ["숲", "아프리카TV"],
    "JYP Ent.": ["JYP엔터", "제이와이피", "JYP엔터테인먼트"],
}
# 위 별명은 단독 뉴스 검색에도 그대로 쓰인다 (기사 제목에 줄임말이 나오므로 함께 검색). 별명은 NAME_READINGS 한 곳에만 추가하면 된다.
for _name, _readings in NAME_READINGS.items():
    _al = NEWS_ALIASES.setdefault(_name, [])
    _al.extend(r for r in _readings if r not in _al)
SUGGEST_LIMIT = 12

# 추천 순서에서 먼저 보여줄 이름난 기업 (대략 유명한 순서). 목록에 없는 이름은 뒤로 밀릴 뿐 추천에서 빠지지는 않는다.
# DART에 등록된 이름과 달라도 오류는 나지 않고, 그 기업만 우선 순위를 못 받는다. 필요하면 자유롭게 추가·수정하세요.
FAMOUS = [
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스", "현대자동차", "기아", "셀트리온", "삼성물산",
    "KB금융", "한화에어로스페이스", "HD현대중공업", "NAVER", "두산에너빌리티", "신한지주", "현대모비스", "한화오션",
    "삼성생명", "POSCO홀딩스", "SK스퀘어", "하나금융지주", "LG화학", "삼성SDI", "HD한국조선해양", "카카오",
    "삼성화재해상보험", "현대로템", "우리금융지주", "한국전력공사", "SK텔레콤", "KT", "LG전자", "삼성전기",
    "삼성중공업", "HD현대일렉트릭", "크래프톤", "SK이노베이션", "포스코퓨처엠", "현대글로비스", "삼성에스디에스",
    "삼성증권", "삼성E&A", "두산로보틱스", "HMM", "한미반도체", "한화시스템", "엘에스일렉트릭", "에코프로비엠",
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


def name_key(name, words=False, keep_space=False):
    """기업명을 비교용으로 정리: 소문자, (주)·공백·기호 제거, 영문은 한글 발음으로.
    기본은 한 글자씩(SK -> 에스케이). words=True면 단어로 읽는 영문은 단어 발음으로(NAVER -> 네이버).
    keep_space=True면 띄어쓰기는 한 칸으로 남긴다 (key_in_title에서 단어 경계를 확인할 때)."""
    text = unicodedata.normalize("NFKC", str(name)).lower()  # 전각 영문(ＳＫ)도 일반 영문으로
    text = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사", "", text)

    def latin(m):
        w = m.group()
        if words and w in WORD_READINGS:
            return WORD_READINGS[w]
        return "".join(LETTER_NAMES.get(ch, ch) for ch in w)

    text = re.sub(r"[a-z]+", latin, text)  # 띄어쓰기·기호를 지우기 전에 영문 단어 단위로 바꾼다
    if keep_space:
        return re.sub(r"\s+", " ", re.sub(r"[\-.,&·()/]", "", text)).strip()
    return re.sub(r"[\s\-.,&·()/]", "", text)


def key_in_title(k, tk, title):
    """검색어 키 k(name_key 결과)가 제목에 들어 있는지. tk는 name_key(title).
    띄어쓰기를 지우고 비교하다 보니 '개원 전'이 '원전'과 일치하는 식으로 두 단어가 우연히 이어 붙을 수 있어서,
    짧은 한글 낱말(3글자 이하)은 띄어쓰기를 지킨 제목에서도 한 번 더 확인한다."""
    if k not in tk:
        return False
    if len(k) <= 3 and re.fullmatch(r"[가-힣]+", k):
        return k in name_key(title, keep_space=True)
    return True


CORP_LIST_URL = "https://raw.githubusercontent.com/brucerue/DART-Joongang/corp-codes/corp_codes.csv"  # GitHub가 매일 만드는 DART 기업 전체 목록(비상장 포함)


def load_corp_list_file():
    """GitHub에 올려 둔 DART 기업 전체 목록(CSV, 비상장 포함) -> 표. 못 읽거나 이상하면 None (그러면 DART에서 직접 받는다)."""
    try:
        res = requests.get(CORP_LIST_URL, timeout=(10, 30))
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.content.decode("utf-8")), dtype=str, keep_default_na=False)
    except Exception:
        return None
    if not {"corp_code", "corp_name", "stock_code"} <= set(df.columns) or len(df) < 50000:  # 비상장이 빠진 작은 목록이면 쓰지 않는다
        return None
    return df[["corp_code", "corp_name", "stock_code"]].copy()


@st.cache_resource
def get_name_index():
    """추천용 기업명 목록 (상장사 우선). 서버가 켜진 뒤 처음 한 번만 만든다.
    상장사 목록 파일을 먼저 읽고, 못 읽으면 DART에서 받는다 (DART가 느리거나 막혀도 기업명 검색이 열리게)."""
    df = load_corp_list_file()
    if df is None:
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
    """기업명/종목코드 -> 고유번호. 같은 이름이 여럿이면 상장사를 우선한다.
    상장사는 기업 목록에서 바로 찾고, 목록에 없을 때만 DART에 물어본다."""
    idx = get_name_index()
    if str(corp).isdigit():
        hit = idx[idx["stock_code"] == str(corp).zfill(6)]  # 종목코드로 찾기
    else:
        corp = canonical_corp(corp)  # 어디서 불러도 별칭(현대차 -> 현대자동차)이 먼저 적용되도록
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

    def set_default_years():
        """조회 단위를 바꾸면 기간 기본값도 바꾼다: 분기별은 올해 한 해(분기는 보통 당해 분기를 보므로), 연도별은 2021~작년."""
        if st.session_state.get("fin_period") == "분기별":
            st.session_state["fin_start"], st.session_state["fin_end"] = this_year, this_year
        else:
            st.session_state["fin_start"], st.session_state["fin_end"] = 2021, this_year - 1

    st.session_state.setdefault("fin_start", 2021)  # 기간 기본값 (조회 단위를 바꾸면 위 함수가 다시 채운다)
    st.session_state.setdefault("fin_end", this_year - 1)
    # 조회 단위는 폼 밖에 둔다(폼 안에서는 '조회'를 눌러야 반영돼 기간 기본값이 바로 바뀌지 않음)
    with st.container(key="fin_unit_bar"):  # '조회 단위' 글자와 선택지를 '조회 방식'과 같은 모양으로 한 줄에
        u1, u2 = st.columns([1, 4], vertical_alignment="center")
        u1.markdown('<span class="mode-lbl">조회 단위</span>', unsafe_allow_html=True)
        period = u2.radio("조회 단위", ["연도별", "분기별"], horizontal=True, key="fin_period", on_change=set_default_years, label_visibility="collapsed")
    with st.form("search"):
        corp_text = st.text_input("기업명 또는 종목코드", key="fin_corp", placeholder="예: 삼성전자 또는 005930")
        c1, c2 = st.columns(2)
        start = c1.number_input("시작연도", min_value=2015, max_value=this_year, step=1, key="fin_start")
        end = c2.number_input("종료연도", min_value=2015, max_value=this_year, step=1, key="fin_end")
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

    with st.container(border=True):
        st.markdown(f"##### {result['corp']} 실적")  # 아래 '재무상태'와 같은 크기의 소제목
        unit = st.radio("단위", list(UNITS), horizontal=True, key="fin_unit", label_visibility="collapsed")  # 기본 억원, 조원으로 바꿀 수 있음
        table = build_table(result["records"], result["period"], unit=unit)
        basis = "연결 기준" if FS_DIV == "CFS" else "별도 기준"  # 지금은 항상 연결재무제표(CFS)로 조회
        st.caption(f"단위: {unit} · {basis}")
        alts = {r["alt"] for r in result["records"] if r.get("alt")}
        if "ofs" in alts:
            st.caption("†연결재무제표가 없어 별도 재무제표 기준으로 보여줘요.")
        if "doc" in alts:
            st.caption("†DART 재무 자료에 없어 정기보고서 원문 표에서 읽은 매출액·영업이익·순이익이에요(4분기는 연간 − 3분기 누적. 기준·단위는 원문 확인).")
        if any(r.get("prov") for r in result["records"]):
            st.caption("\\*잠정실적: 분기보고서 제출 전, '영업(잠정)실적(공정공시)'에서 읽은 매출액·영업이익만 보여줘요(연결 기준). 확정치와 다를 수 있어요")
        elif st.session_state.get("fin_prov_note") and result["period"] == "분기별":
            st.caption(st.session_state["fin_prov_note"])
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
        with st.container(border=True):
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
            only_year = "" if yearly else " 현금흐름·이자·EBITDA·ROE는 연도별 조회에서만 나와요."
            more_help = (
                "**순차입금비율** = 순차입금 ÷ 자본총계  \n**유동비율** = 유동자산 ÷ 유동부채  \n"
                "**이자보상배율** = 영업이익 ÷ 이자비용(없으면 현금흐름표의 이자 지급액)  \n"
                "**EBITDA** = 영업이익 + 감가상각비 + 무형자산상각비  \n**ROE** = 순이익 ÷ 기말 자본총계  \n"
                "차입금은 재무상태표의 단기차입금·유동성장기부채·사채·장기차입금 등을 더한 값이에요. "
                "은행·보험·증권사는 계정 구조가 달라 '-'로 나올 수 있어요." + only_year
            )
            if st.toggle("더 보기", key="fin_bs_more", help=more_help):
                more = build_bs_more_table(result["records"], result["period"], unit=unit)
                st.dataframe(
                    more, hide_index=True,
                    column_config={"기간": st.column_config.TextColumn("기간", width=95)},
                    height=(len(more) + 1) * 35 + 3,
                )

    with st.container(border=True):
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
    """탭: 재무 조회 - 기업 한 곳의 기간별 추이 / 국내 섹터 합산 / 해외 섹터(미국 SEC 등)를 고른다."""
    with st.container(key="fin_wrap"):
        with st.container(key="fin_mode_bar"):  # '조회 방식' 글자와 선택지를 한 줄에
            c1, c2 = st.columns([1, 4], vertical_alignment="center")
            c1.markdown('<span class="mode-lbl">조회 방식</span>', unsafe_allow_html=True)
            mode = c2.radio("조회 방식", ["개별", "국내", "해외", "AI 검색"], horizontal=True, key="fin_mode", label_visibility="collapsed")
        if mode == "AI 검색":
            render_fin_ai_search()
        elif mode == "개별":
            render_financials()
        elif mode == "국내":
            render_industry()
        else:
            render_overseas()


def render_industry():
    """탭: 업종 합산 - 여러 회사의 같은 기간 실적을 모으고 합계를 낸다."""
    groups = list(INDUSTRY_GROUPS)
    st.session_state.setdefault("ind_corps", "")  # 처음엔 섹터도 기업도 비워 둔다
    st.radio("섹터", groups, index=None, horizontal=True, key="ind_group", on_change=fill_industry)  # 폼 밖: 고르면 아래 기업 칸이 바뀜
    ly, lp = latest_period()
    years = list(range(TODAY.year, 2014, -1))
    with st.form("ind_search"):
        corp_text = st.text_input(
            "기업 (쉼표로 구분)", key="ind_corps", placeholder="예: 삼성전자, SK하이닉스",
            help=f"섹터를 고르면 대표 기업이 채워져요. 빼거나 더해서 최대 {MAX_INDUSTRY_CORPS}곳까지 합산할 수 있어요.",
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
    if any(d["now"].get("prov") for d in res["data"].values()):
        st.caption("\\*잠정실적: 분기보고서 제출 전, '영업(잠정)실적(공정공시)'에서 읽은 매출액·영업이익만 보여줘요(연결 기준). 합계에도 포함돼 있고 확정치와 다를 수 있어요")
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
            "원문": LINK_COLUMN,
        },
        height=(len(table) + 1) * 35 + 3,
    )
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
        ["전체"] + list(MAJOR_DISCLOSURES) + list(EXTRA_DISCLOSURES),
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


def render_feed(by_sector=False):
    """탭 3: 오늘의 공시 - 하루 동안 전 상장사가 낸 공시 중 취재거리가 될 만한 것만 모아 보기.
    by_sector=True면 고른 섹터(실적 캘린더와 같은 섹터, 여러 개 가능)의 기업이 낸 공시만 보여준다."""
    p = "fsec" if by_sector else "feed"  # 두 화면의 입력칸·결과가 섞이지 않도록 이름을 나눈다
    if by_sector:
        team_buttons("fsec", CAL_GROUPS, "fsec_groups")
        st.pills("섹터", list(CAL_GROUPS), selection_mode="multi", key="fsec_groups", label_visibility="collapsed")
    with st.form(f"{p}_search"):
        c1, c2 = st.columns(2)
        day = c1.date_input(
            "날짜", value=last_weekday(TODAY), min_value=date(2015, 1, 1), max_value=TODAY, key=f"{p}_date"
        )
        market = c2.radio("시장", list(FEED_MARKETS), index=0 if by_sector else list(FEED_MARKETS).index("코스피"),
                          horizontal=True, key=f"{p}_market")
        submitted = st.form_submit_button("공시 불러오기")

    if submitted:
        groups = list(st.session_state.get("fsec_groups") or []) if by_sector else []
        st.session_state.pop(p, None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움
        if by_sector and not groups:
            st.error("섹터를 하나 이상 골라주세요.")
        else:
            with st.spinner("공시를 불러오는 중입니다... (공시가 많은 날은 몇 초 걸려요)"):
                try:
                    rows, total, truncated = collect_feed(day, FEED_MARKETS[market])
                except DartApiError as e:
                    st.error(str(e))
                else:
                    if by_sector:  # 시장 전체 공시 중 고른 섹터 기업의 것만 남긴다
                        keys = {name_key(n) for g in groups for n in CAL_GROUPS.get(g, [])}
                        rows = [r for r in rows if name_key(r.get("corp_name", "")) in keys]
                    st.session_state[p] = {
                        "day": day, "market": market, "rows": rows, "total": total, "truncated": truncated,
                        "groups": groups, "id": time.time_ns(),  # 조회할 때마다 달라지는 번호 (아래 라디오 키에 사용)
                    }

    res = st.session_state.get(p)
    if not res:
        if by_sector:
            st.caption("섹터를 고르고 날짜·시장을 정한 뒤 '공시 불러오기'를 누르세요. 고른 섹터의 기업이 그날 낸 공시를 골라 보여줍니다.")
        else:
            st.caption("날짜와 시장을 고르고 '공시 불러오기'를 누르세요. 영업실적·공급계약·증자·합병·자기주식·지분 변동 공시를 골라 보여줍니다.")
        return
    if not res["rows"]:
        if by_sector:
            st.warning("이 날짜에 고른 섹터 기업의 공시가 없습니다. 시장을 바꾸거나 다른 날짜를 골라 보세요.")
        else:
            st.warning("이 날짜에 조회된 공시가 없습니다. 휴장일이거나 아직 공시가 올라오기 전일 수 있어요.")
        return

    views = ["주요 전체"] + list(MAJOR_DISCLOSURES) + list(EXTRA_DISCLOSURES) + ["모든 공시"]
    if by_sector:
        notes = [f"{res['day']}", res["market"], ", ".join(res["groups"]), f"전체 공시 {res['total']:,}건 중 섹터 기업 {len(res['rows']):,}건",
                 "최신순", "정정 공시 포함"]
    else:
        notes = [f"{res['day']}", res["market"], f"전체 공시 {res['total']:,}건", "최신순", "정정 공시 포함"]
    st.caption(" · ".join(notes))
    if res["truncated"]:
        st.warning(f"공시가 매우 많아 시장마다 최신 {MAX_FEED_PAGES * LIST_PAGE_SIZE:,}건까지만 확인했어요.")
    view = st.radio(
        "보기",
        views,
        horizontal=True,
        # 선택지 이름의 건수가 조회마다 바뀌므로, 조회마다 키를 새로 만들어 항상 첫 번째가 선택된 채로 시작하게 한다
        key=f"{p}_view_{res['id']}",
        format_func=lambda v: f"{v} ({len(match_preset(res['rows'], v)):,})",
    )
    keyword = st.text_input(
        "회사·보고서명 검색", key=f"{p}_keyword", placeholder="예: 삼성, 하이닉스, 유상증자 (공백으로 여러 단어를 넣으면 모두 포함된 공시만)"
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
    saved = render_reaction(shown, p)
    if saved:
        render_reaction_table(feed_table(shown), shown, saved)
    else:
        st.dataframe(
            feed_table(shown),
            hide_index=True,
            column_config={"보고서명": st.column_config.TextColumn("보고서명", width="large"), "원문": LINK_COLUMN},
        )
    render_ai_summary(shown, p, all_rows=res["rows"])
    render_news_picks(shown, res["id"])


# ------------------------------------------------------------
# 외신 뉴스 (Google News RSS) - 시험용. 문제가 있으면 이 블록과 render_news 첫머리의 '외신' 분기를 지우면 된다.
#  - 공식 API가 아니라 비공식 RSS라 문법이 바뀌거나 막힐 수 있다. 제목·링크·매체·시각만 가져온다 (본문은 가져오지 않는다).
# ------------------------------------------------------------
GNEWS_URL = "https://news.google.com/rss/search"
GNEWS_OUTLETS = {  # 화면 이름 -> 도메인
    "WSJ": "wsj.com", "Bloomberg": "bloomberg.com", "NYT": "nytimes.com", "Reuters": "reuters.com",
    "FT": "ft.com", "CNBC": "cnbc.com", "AP": "apnews.com",
}
GNEWS_PERIODS = {"1일": 1, "3일": 3, "7일": 7}
MAX_GNEWS_CORPS = 100  # 섹터를 고르면 기업이 많아지므로 넉넉하게 (기업 하나당 구글 검색 한 번)
# 한글 기업명 -> 영문 검색어 (없으면 입력한 그대로 검색). 필요하면 자유롭게 추가하세요.
GNEWS_NAMES = {
    "삼성전자": "Samsung Electronics", "SK하이닉스": "SK hynix", "현대자동차": "Hyundai Motor", "기아": "Kia",
    "LG에너지솔루션": "LG Energy Solution", "NAVER": "Naver", "카카오": "Kakao", "POSCO홀딩스": "POSCO Holdings",
    "LG전자": "LG Electronics", "삼성SDI": "Samsung SDI", "삼성바이오로직스": "Samsung Biologics", "셀트리온": "Celltrion",
    "한화에어로스페이스": "Hanwha Aerospace", "에스케이온": "SK On", "현대모비스": "Hyundai Mobis", "케이티": "KT Corp",
    "SK텔레콤": "SK Telecom", "하이브": "HYBE", "삼성물산": "Samsung C&T", "한국전력공사": "KEPCO",
    "두산에너빌리티": "Doosan Enerbility", "HD현대중공업": "HD Hyundai Heavy Industries", "한화오션": "Hanwha Ocean",
    "LG디스플레이": "LG Display", "삼성전기": "Samsung Electro-Mechanics", "SK이노베이션": "SK Innovation",
    "한국항공우주산업": "Korea Aerospace Industries", "LIG디펜스앤에어로스페이스": "LIG Nex1", "현대로템": "Hyundai Rotem",
}


def gnews_term(corp):
    """입력한 기업명 -> 영문 검색어 (모르는 이름은 그대로)."""
    name = canonical_corp(corp)
    if GNEWS_NAMES.get(name) or GNEWS_NAMES.get(corp):
        return GNEWS_NAMES.get(name) or GNEWS_NAMES.get(corp)
    key = re.sub(r"\s+", "", corp).lower()
    ticker = KOREAN_TO_TICKER.get(key) or next((c for c, v in ASIA_COMPANIES.items() if key in v[2] or key == v[0].lower()), None)
    if ticker is None and corp.upper() in FOREIGN_ENGLISH:  # NVDA, MU처럼 티커로 쳤을 때
        ticker = corp.upper()
    return FOREIGN_ENGLISH.get(ticker) or corp


def gnews_fetch(session, term, days, domains, in_title=True, ko=False, extra=""):
    """검색어 하나 -> [{"title","url","publisher","domain","dt"}] 최신순. 선택한 언론사 도메인만 남긴다."""
    sites = " OR ".join(f"site:{d}" for d in domains)
    query = f'"{term}" {extra} ({sites}) when:{days}d'.replace("  ", " ")
    locale = {"hl": "ko", "gl": "KR", "ceid": "KR:ko"} if ko else {"hl": "en-US", "gl": "US", "ceid": "US:en"}
    res = session.get(GNEWS_URL, params={"q": query, "hl": locale["hl"], "gl": locale["gl"], "ceid": locale["ceid"]}, timeout=REQUEST_TIMEOUT,
                      headers={"User-Agent": "Mozilla/5.0"})
    if getattr(res, "status_code", 200) >= 400:
        raise NewsApiError(f"Google News에서 기사를 받지 못했어요 (오류 {res.status_code}). 잠시 후 다시 시도해주세요.")
    try:
        root = ET.fromstring(res.content)
    except ET.ParseError as e:
        raise NewsApiError("Google News 응답을 읽지 못했어요. 잠시 후 다시 시도해주세요.") from e
    cutoff = datetime.now(KST) - timedelta(days=days)
    first = term.split()[0] if term.split() else term
    keys = {term.casefold()} | ({first.casefold()} if len(first) >= 2 else set())  # 'Samsung Electronics'는 'Samsung'만 있는 제목도 통과
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        domain = urlparse((src.get("url") if src is not None else "") or "").netloc.lower().removeprefix("www.")
        if not any(domain == d or domain.endswith("." + d) for d in domains):
            continue
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST)
        except Exception:
            continue
        if when < cutoff:
            continue
        publisher = (src.text or "").strip() if src is not None else domain
        title = (it.findtext("title") or "").strip()
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3].strip()
        if in_title and not any(k in title.casefold() for k in keys):  # 제목에 기업명이 없는 기사는 뺀다
            continue
        out.append({"title": title, "url": (it.findtext("link") or "").strip(), "publisher": publisher, "domain": domain, "dt": when})
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out


def gnews_search(terms, days, domains, in_title=True):
    """-> {검색어: 기사 목록}. 같은 검색은 30분 저장."""
    session, store = get_session(), get_cache_store()
    doms = tuple(sorted(domains))

    def one(term):
        key = ("gnews", term, days, doms, in_title, TODAY.isoformat())
        hit = cache_lookup(store, key, NEWS_TTL)
        if hit is not _MISSING:
            return hit
        value = gnews_fetch(session, term, days, doms, in_title)
        cache_save(store, key, value)
        return value

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return dict(zip(terms, pool.map(one, terms)))


def global_sector_names(groups):
    """외신 섹터 이름들 -> 영문 검색어 목록(중복 제외). 재무 조회 '해외 섹터'와 같은 기업 구성, 티커 대신 영문 기업명"""
    out = []
    for g in groups:
        for n in GLOBAL_SECTORS.get(g, []):
            if n not in out:
                out.append(n)
    return out


def fill_gn_terms():
    st.session_state["gn_corp"] = ", ".join(global_sector_names(st.session_state.get("gn_groups") or []))


def render_global_news():
    """뉴스 탭 > 외신: 영문 기업명으로 WSJ·Bloomberg 등의 최근 기사 제목과 링크를 보여준다."""
    st.markdown('<span class="sec-lbl">섹터 (여러 개 선택 가능)</span>', unsafe_allow_html=True)
    st.pills("섹터", list(GLOBAL_SECTORS), selection_mode="multi", key="gn_groups", on_change=fill_gn_terms, label_visibility="collapsed",
             help="재무 분석 '해외'와 같은 기업이에요. 고르면 아래 기업명 칸에 영문 기업명이 채워져요.")
    with st.form("gn_search"):
        corp_text = st.text_input("기업명 (여러 곳은 쉼표로)", key="gn_corp", placeholder="예: Samsung, TSMC, Tesla",
                                  help=f"한글 이름은 알려진 기업이면 영문으로 바꿔 검색해요. 최대 {MAX_GNEWS_CORPS}곳까지 한 번에 검색해요(곳 수만큼 시간이 걸려요).")
        period = st.radio("기간", list(GNEWS_PERIODS), index=1, horizontal=True, key="gn_period")
        in_title = st.checkbox("제목에 검색어(기업명)가 들어간 기사만", value=True, key="gn_title")
        submitted = st.form_submit_button("해외 뉴스 조회")
    if submitted:
        st.session_state.pop("gn_res", None)
        parts = []
        for part in re.split(NEWS_SPLIT, corp_text):
            part = part.strip()
            if part and part.casefold() not in {x.casefold() for x in parts}:
                parts.append(part)
        if not parts:
            st.error("기업명을 입력해주세요.")
        elif len(parts) > MAX_GNEWS_CORPS:
            st.error(f"기업은 한 번에 {MAX_GNEWS_CORPS}곳까지 넣을 수 있어요. (지금 {len(parts)}곳)")
        else:
            terms = {gnews_term(p): p for p in parts}  # 영문 검색어 -> 입력한 이름
            with st.spinner("해외 뉴스를 찾는 중입니다..."):
                try:
                    got = gnews_search(list(terms), GNEWS_PERIODS[period], list(GNEWS_OUTLETS.values()), in_title)
                except NewsApiError as e:
                    st.error(str(e))
                except requests.exceptions.RequestException:
                    st.error("Google News에 연결하지 못했어요. 잠시 후 다시 시도해주세요.")
                else:
                    st.session_state["gn_res"] = {"got": got, "labels": terms, "period": period}
    res = st.session_state.get("gn_res")
    if not res:
        return
    st.caption(f"최근 {res['period']} · 최신순")
    many = len(res["got"]) > 5  # 섹터처럼 기업이 많으면 기사 없는 기업은 한 줄로 모은다
    empty = []
    for term, items in res["got"].items():
        label = res["labels"].get(term, term)
        if not items and many:
            empty.append(label)
            continue
        st.markdown(f"##### {label}" + (f" ({term})" if label.casefold() != term.casefold() else ""))
        if not items:
            st.info("이 기간에 해당 언론사 기사를 찾지 못했어요. 기간을 늘리거나 영문 이름으로 검색해 보세요.")
            continue
        lines = []
        for i in items:
            title = re.sub(r"([\[\]])", r"\\\1", i["title"])
            lines.append(f"- [{title}]({i['url'].replace(')', '%29')})  \n  <small>{i['publisher']} · {i['dt']:%m/%d %H:%M}</small>")
        st.markdown("\n".join(lines), unsafe_allow_html=True)
    if empty:
        st.caption("이 기간에 기사가 없는 기업: " + ", ".join(empty))


NEWS_ALWAYS_SEARCH = {"엘에스일렉트릭": ["LS일렉트릭"]}
MAX_NEWS_TERMS = {"통신": 100, "단독": 100}  # 검색어 칸에 넣을 수 있는 검색어 수 (섹터를 여러 개 고르면 많아짐). 통신은 구글 뉴스라 한도가 넉넉함
NEWS_ALIAS_LIMIT = 5  # 검색어가 이 수 이하일 때만 줄임말(별칭)도 따로 검색 (그 이상이면 호출이 너무 많아져 제목 확인에만 씀)
NEWS_KINDS = {  # 조회 방식 -> 검색 때 붙이는 말(None=단독), 보여줄 언론사 구분
    "통신": {"keyword": "", "need_exclusive": False},
    "단독": {"keyword": None, "need_exclusive": True},
}


def fill_news_terms(kind):
    """고른 섹터들의 기업·키워드를 중복 없이 검색어 칸에 채운다 (그 뒤에 자유롭게 고칠 수 있다)."""
    terms, skip = [], set()
    for g in st.session_state.get(f"nw_{kind}_groups") or []:
        for f in NEWS_SECTORS[g]["기업"]:
            if name_key(f) in skip or f in terms:  # 앞에서 검색하는 기업의 줄임말이면 건너뜀
                continue
            terms.append(f)
            skip.update(name_key(a) for a in NEWS_ALIASES.get(f, []))
        terms += [k for k in NEWS_SECTORS[g]["키워드"] if k not in terms]
    st.session_state[f"nw_{kind}_terms"] = ", ".join(terms)


def news_groups_changed(kind):
    """섹터를 고르면 검색어를 채우고, 선택을 기억해 둔다 (통신↔단독으로 옮겨도 같은 섹터가 선택되도록)."""
    st.session_state["nw_sel"] = list(st.session_state.get(f"nw_{kind}_groups") or [])
    fill_news_terms(kind)


def parse_news_terms(text):
    """검색어 칸 글 -> 검색어 목록 (대소문자 통일, 종목코드는 기업명으로, 중복 제외)."""
    out = []
    for part in re.split(NEWS_SPLIT, text or ""):
        part = canonical_corp(part)
        if part.isdigit() and len(part) == 6:
            idx = get_name_index()
            hit = idx[idx["stock_code"] == part]
            if not hit.empty:
                part = hit.iloc[0]["corp_name"]
        if part and name_key(part) not in {name_key(c) for c in out}:
            out.append(part)
    return out


def team_sector_options(team, options):
    """팀 프리셋의 섹터 -> 이 화면의 선택지 이름들. 실적 캘린더·공시 섹터는 '자동차·배터리'처럼 묶은 이름이라 그쪽으로 바꿔 준다."""
    out = []
    for sec in TEAM_PRESETS[team]:
        name = sec if sec in options else next((o for o in options if sec in o.split("·") and "·" in o and sec in ("자동차", "배터리")), None)
        if name is None and sec in HEAVY_PARTS and HEAVY_NAME in options:
            name = HEAVY_NAME
        if name and name not in out:
            out.append(name)
    return out


def apply_team(team, options, target, after=None):
    """팀 칩을 누르면 그 팀의 섹터를 선택하고, 이미 다 선택돼 있으면 (다시 누르면) 그 팀의 섹터를 해제한다."""
    team_secs = team_sector_options(team, options)
    current = list(st.session_state.get(target) or [])
    if team_secs and all(x in current for x in team_secs):
        st.session_state[target] = [x for x in current if x not in team_secs]
    else:
        st.session_state[target] = team_secs
    if after:
        after()


def team_buttons(prefix, options, target, after=None, label="섹터 (여러 개 선택 가능)"):
    """섹터 선택칸의 제목 줄: 왼쪽에 제목, 오른쪽에 팀 칩. 누르면 그 팀의 섹터가 한꺼번에 선택되고, 이후에는 하나씩 더하거나 뺄 수 있다."""
    with st.container(key=f"{prefix}_secbar"):  # 모바일에서도 한 줄 (CSS: secbar)
        cols = st.columns([1] + [1] * len(TEAM_PRESETS), vertical_alignment="center")
        cols[0].markdown(f'<span class="sec-lbl">{label}</span>', unsafe_allow_html=True)
        for col, team in zip(cols[1:], TEAM_PRESETS):
            col.button(team, key=f"{prefix}_team_{team}", on_click=apply_team, args=(team, list(options), target, after),
                       help="이 팀의 섹터를 한꺼번에 선택해요(다시 누르면 해제): " + ", ".join(team_sector_options(team, list(options))))


# ------------------------------------------------------------
# 미리 모아 둔 기사·공시 (GitHub 'data' 브랜치: collect_data.py가 매시간 쌓음) -> 이슈 타임라인
# ------------------------------------------------------------
DATA_REPO = (load_secret("DATA_REPO") or "brucerue/DART-Joongang").strip()  # 'data' 브랜치가 있는 저장소(계정/저장소). 다르면 Streamlit Secrets에 DATA_REPO로 지정
DATA_BRANCH = "data"
STORED_FMT = "%Y-%m-%dT%H:%M"
STORED_KINDS = ["통신", "단독", "공시"]


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_stored(name):
    res = requests.get(f"https://raw.githubusercontent.com/{DATA_REPO}/{DATA_BRANCH}/{name}", timeout=20)
    res.raise_for_status()
    return res.json()  # 실패(예외)는 캐시에 남지 않아 다음 조회 때 다시 시도한다


def get_stored(name):
    """모아 둔 자료(recent.json: 최근 7일 / archive.json: 6개월). 못 읽으면 None."""
    try:
        data = _fetch_stored(name)
        return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None
    except Exception:
        return None


def stored_item(row):
    """저장된 한 줄 -> 화면용 기사 항목 (읽을 수 없는 줄은 None)."""
    try:
        dt = datetime.strptime(row["d"], STORED_FMT).replace(tzinfo=KST)
        return {"title": row["t"], "url": row["u"], "publisher": row.get("p") or "", "category": "통신사" if row.get("k") == "통신" else "언론",
                "dt": dt, "kind": row.get("k", ""), "g": row.get("g") or []}
    except (KeyError, ValueError, TypeError):
        return None


def render_timeline():
    """탭: 이슈 타임라인 - 모아 둔 기사·공시(최대 6개월)를 기업별 시간순으로."""
    st.caption("매시간 자동으로 모아 둔 기사·공시를 기업별로 시간순으로 보여줘요. 모으기 시작한 날부터의 자료만 있어요.")
    data = get_stored("archive.json")
    if data is None:
        st.info("모아 둔 자료를 아직 불러오지 못했어요. 처음이라면 GitHub Actions의 '기사·공시 모아두기'를 한 번 실행해 주세요.")
        return
    rows = [it for it in map(stored_item, data["items"]) if it and not is_blocked(it["title"])]
    if not rows:
        st.info("모아 둔 기사가 아직 없어요.")
        return
    with st.form("tl_form"):
        name = st.text_input("기업·키워드", key="tl_name", placeholder="예: 삼성전자")
        c1, c2 = st.columns(2)
        span = c1.radio("기간", ["1개월", "3개월", "6개월"], index=2, horizontal=True, key="tl_span")
        kinds = c2.multiselect("종류", STORED_KINDS, default=STORED_KINDS, key="tl_kinds")
        st.form_submit_button("타임라인 보기")
    first = min(r["dt"] for r in rows)
    st.caption(f"자료 {data.get('updated', '?').replace('T', ' ')} 기준 · {first:%Y.%m.%d}부터 {len(rows):,}건 보관 중")
    terms = parse_news_terms(name)
    if not terms:
        return
    now = datetime.now(KST)
    since = now - timedelta(days={"1개월": 30, "3개월": 91, "6개월": 183}[span])
    keys = {name_key(n) for t in terms for n in news_names(t)}
    picked = []
    for it in rows:
        if it["kind"] not in kinds or it["dt"] < since:
            continue
        tk = name_key(it["title"])
        if any(key_in_title(k, tk, it["title"]) for k in keys) or any(name_key(g) in keys for g in it["g"]):
            picked.append(dict(it, tags=[it["kind"]]))
    if not picked:
        st.warning("조건에 맞는 기사·공시가 없어요. 기간을 늘리거나 이름을 바꿔 보세요. (섹터 대표 기업·키워드만 모아 두고 있어요)")
        return
    counts = {k: sum(1 for p in picked if p["kind"] == k) for k in STORED_KINDS}
    st.markdown(f"**{', '.join(terms)}** · 최근 {span} · 총 {len(picked):,}건 (" + " · ".join(f"{k} {v}" for k, v in counts.items() if v) + ")")
    weeks = pd.date_range((since - timedelta(days=since.weekday())).date(), now.date(), freq="W-MON")
    if len(weeks) == 0:
        weeks = pd.DatetimeIndex([(now - timedelta(days=now.weekday())).date()])
    chart = pd.DataFrame(0, index=[w.strftime("%m.%d") for w in weeks], columns=[k for k in STORED_KINDS if counts[k]])
    for p in picked:
        monday = (p["dt"] - timedelta(days=p["dt"].weekday())).strftime("%m.%d")
        if monday in chart.index:
            chart.loc[monday, p["kind"]] += 1
    st.caption("주별 건수 (월요일 시작)")
    st.bar_chart(chart)
    clusters = cluster_news(picked)
    with st.expander("목록 복사하기 (메모·메신저용)"):
        lines = [f"[이슈 타임라인] {', '.join(terms)} · 최근 {span} ({now:%m.%d %H:%M} 기준)", ""]
        for c in clusters:
            it = c["rep"]
            lines += [f"{it['dt']:%Y.%m.%d} [{it['kind']}] {it['title']} · {it['publisher']}", it["url"], ""]
        st.code("\n".join(lines).strip(), language=None, wrap_lines=True, height=320)
    shown = clusters[:300]
    months = list(dict.fromkeys(f"{c['rep']['dt']:%Y.%m}" for c in shown))
    for m in months:
        part = [c for c in shown if f"{c['rep']['dt']:%Y.%m}" == m]
        st.markdown(f"##### {m} · {len(part)}건")
        st.markdown(news_html(part, now), unsafe_allow_html=True)
    if len(clusters) > len(shown):
        st.caption(f"묶음 {len(clusters):,}건 중 최근 {len(shown)}건만 보여줘요. 기간을 줄이면 더 자세히 볼 수 있어요.")


def render_news_search(kind):
    """통신/단독 공통 화면: 섹터 고르기 + 검색어 + 기간 -> 기사 묶음 목록."""
    cfg = NEWS_KINDS[kind]
    k = f"nw_{kind}"
    st.session_state.setdefault(f"{k}_terms", "")  # 처음엔 섹터도 검색어도 비워 둔다
    if f"{k}_groups" not in st.session_state:  # 다른 모드(통신↔단독)에서 옮겨 왔으면 거기서 고른 섹터를 이어받는다
        carried = [g for g in st.session_state.get("nw_sel") or [] if g in NEWS_SECTORS]
        st.session_state[f"{k}_groups"] = carried
        if carried:
            fill_news_terms(kind)
    team_buttons(k, NEWS_SECTORS, f"{k}_groups", after=lambda: news_groups_changed(kind))
    st.pills("섹터", list(NEWS_SECTORS), selection_mode="multi", key=f"{k}_groups", label_visibility="collapsed",
             on_change=news_groups_changed, args=(kind,))
    with st.form(f"{k}_form"):
        text = st.text_input(
            "검색어 (쉼표로 구분)", key=f"{k}_terms", placeholder="예: 삼성전자, SK하이닉스, HBM",
            help=f"고른 섹터의 대표 기업·키워드가 채워져요. 직접 빼거나 더해서 최대 {MAX_NEWS_TERMS[kind]}개까지 검색할 수 있어요. 많으면 조회가 오래 걸려요.",
        )
        period = st.radio("기간", list(NEWS_PERIODS), index=0, horizontal=True, key=f"{k}_period")
        if kind == "통신":
            with st.container(key="news_opts_bar"):  # 모바일에서도 두 체크박스를 한 줄에
                cc1, cc2 = st.columns(2)
                in_title = cc1.checkbox("제목만 검색", value=True, key=f"{k}_title", help="제목에 검색어(기업명)가 들어간 기사만 보여줘요.")
                yna_only = cc2.checkbox("연합만 검색", value=True, key=f"{k}_yna", help="끄면 뉴시스·뉴스1 기사도 함께 검색해요.")
        else:
            in_title = st.checkbox("제목만 검색", value=True, key=f"{k}_title", help="제목에 검색어(기업명)가 들어간 기사만 보여줘요.")
            yna_only = False
        submitted = st.form_submit_button("뉴스 조회") or st.session_state.pop("news_run", False)

    if submitted:
        terms = parse_news_terms(text)
        if not terms:
            st.error("섹터를 고르거나 검색어를 입력해주세요.")
        elif len(terms) > MAX_NEWS_TERMS[kind]:
            st.error(f"검색어는 한 번에 {MAX_NEWS_TERMS[kind]}개까지 넣을 수 있어요. (지금 {len(terms)}개)")
        else:
            st.session_state.pop(f"news_res_{kind}", None)  # 이전 결과가 새 조회 결과로 오해되지 않도록 비움
            owner = {}  # 검색어·줄임말 -> 어느 검색어 것인지 (제목 확인과 꼬리표용)
            search = []
            for t in terms:
                for n in news_names(t):
                    if n not in owner:
                        owner[n] = t
                        if n == t or len(terms) <= NEWS_ALIAS_LIMIT:
                            search.append(n)  # 검색어가 적을 때만 줄임말도 따로 검색
            for t in terms:  # 기사에서 주로 쓰는 이름이 등록명과 다른 기업은 항상 함께 검색 (예: 엘에스일렉트릭 -> LS일렉트릭)
                for n in NEWS_ALWAYS_SEARCH.get(t, []):
                    if n not in owner:
                        owner[n] = t
                        search.append(n)
            with st.spinner("뉴스를 불러오는 중입니다..."):
                try:
                    g_err = naver_note = None
                    capped, since = False, None
                    if kind == "통신":  # 통신: 구글 뉴스(연합뉴스·뉴시스·뉴스1 도메인만)로만 검색 -> 네이버 키·한도와 무관
                        items, g_err = google_news(search, NEWS_PERIODS[period], WIRE_DOMAINS[:1] if yna_only else WIRE_DOMAINS)
                        items = drop_blocked(items)
                        if g_err:
                            raise NewsApiError(g_err)
                        source = "구글 뉴스"
                    else:  # 단독: 구글 뉴스에 네이버 뉴스를 항상 합친다(구글이 늦게 반영하는 기사를 네이버가 메움). 키가 없거나 USE_NAVER=0이면 구글만
                        items, g_err = google_news(search, NEWS_PERIODS[period], list(PUBLISHERS), "단독")
                        items = drop_blocked(items)
                        source = "구글 뉴스"
                        n_excl = sum(is_exclusive(i["title"]) for i in items)
                        few = n_excl < NEWS_GOOGLE_MIN
                        if USE_NAVER and NAVER_ID and NAVER_SECRET:
                            try:
                                n_items, capped, since = search_news(search, NEWS_PERIODS[period], keyword=cfg["keyword"])
                                n_items = drop_blocked(n_items)
                            except NewsApiError as e:
                                if g_err or not n_excl:  # 보여줄 단독 기사가 하나도 없으면 네이버 오류를 그대로 알림
                                    raise
                                naver_note = str(e)
                            else:
                                have = {title_key(i["title"]) for i in items}
                                items = items + [n for n in n_items if title_key(n["title"]) not in have]
                                source = "네이버 뉴스(구글 뉴스 실패)" if g_err else "구글 뉴스 + 네이버 뉴스"
                        elif g_err:
                            raise NewsApiError(g_err)
                except NewsApiError as e:
                    st.error(str(e))
                else:
                    total = len(items)
                    want = "통신사" if kind == "통신" else None  # 통신: 연합뉴스·뉴시스·뉴스1만 / 단독: 목록에 있는 언론사 모두
                    items = [i for i in items if (i["category"] == want if want else i["category"])]
                    if period == "오늘":  # 구글·네이버는 '24시간' 단위로 찾으니, 오늘 0시(한국시간) 이후 기사만 남긴다
                        today0 = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
                        items = [i for i in items if i["dt"] >= today0]
                    if kind == "통신":  # 통신: 스포츠 기사는 기업명이 들어 있어도 뺀다 (낱말 + 연합뉴스 스포츠 제목 목록)
                        sports_keys = load_sports_keys()
                        items = [i for i in items if not is_sports(i["title"], sports_keys)]
                    if yna_only:  # 연합만: 연합뉴스 아닌 기사(뉴시스·뉴스1 등)는 뺀다
                        items = [i for i in items if i["publisher"] == "연합뉴스"]
                    keys = {t: name_key(t) for t in owner}
                    tagged = []
                    for i in items:
                        tk = name_key(i["title"])
                        hits = [t for t, nk in keys.items() if key_in_title(nk, tk, i["title"])]  # 제목에 들어 있는 검색어(줄임말 포함)
                        if in_title and not hits:
                            continue
                        tags = list(dict.fromkeys(owner[t] for t in hits)) if len(terms) > 1 else []
                        tagged.append(dict(i, tags=tags))  # 저장된 원본은 건드리지 않음
                    if cfg["need_exclusive"]:  # 검색은 제목·요약 어디든 '단독'이 있으면 잡으므로, 제목에 단독 표기가 있는 것만 남긴다
                        tagged = [i for i in tagged if is_exclusive(i["title"])]
                    clusters = cluster_news(tagged)  # 비슷한 기사끼리 묶어 최신순으로
                    st.session_state[f"news_res_{kind}"] = {
                        "corp": ", ".join(terms), "kind": kind, "terms": terms, "names": search, "period": period,
                        "total": total, "kept": len(tagged), "clusters": clusters, "capped": capped, "oldest": since, "source": source, "naver_note": naver_note,
                    }
                    save_watchlist(st.session_state[f"news_res_{kind}"])

    res = st.session_state.get(f"news_res_{kind}")
    if not res:
        return
    clusters = res["clusters"]
    if not clusters:
        st.warning(
            "조건에 맞는 기사가 없습니다. '제목만 검색'을 끄거나 기간을 늘려보세요."
            if res["total"]
            else "조회된 기사가 없습니다. 검색어를 확인하거나 기간을 늘려보세요."
        )
        return

    now = datetime.now(KST)
    notes = [f"최근 {res['period']}", f"기사 {res['kept']:,}건 → 묶음 {len(clusters):,}건"]
    fresh = sum(is_new(c["rep"], now) for c in clusters)
    if fresh:
        notes.append(f"NEW {fresh}건 (어제·오늘)")
    notes.append(f"검색어 {len(res['terms'])}개")
    notes.append(res.get("source", ""))
    st.caption(" · ".join(notes))
    if res.get("naver_note"):
        st.caption(f"네이버 뉴스를 함께 불러오지 못해 구글 뉴스 결과만 보여줘요: {res['naver_note']}")
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
    st.caption("지금 주소를 북마크(또는 홈 화면에 추가)하면 다음에 열 때 같은 검색어로 바로 조회돼요.")


def render_news():
    """탭: 산업 뉴스 - 통신(연합뉴스·뉴시스·뉴스1) / 단독 / 외신"""
    with st.container(key="news_mode_bar"):  # '조회 방식' 글자와 선택지를 한 줄에 (재무 조회와 같은 모양)
        c1, c2 = st.columns([1, 4], vertical_alignment="center")
        c1.markdown('<span class="mode-lbl">조회 방식</span>', unsafe_allow_html=True)
        mode = c2.radio("조회 방식", ["통신", "단독", "외신"], horizontal=True, key="news_mode", label_visibility="collapsed")  # 폼 밖: 바꾸면 아래 입력칸이 바뀜
    if mode == "외신":
        render_global_news()
        return
    render_news_search(mode)


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


CHAEBOL_HIST_BRANCH = "chaebol"  # chaebol_history.py가 공정위 2019~2025년 지정 자료를 모아 저장하는 브랜치
CHAEBOL_HIST_ALIAS = {"호반": "호반건설", "BS": "BS(舊 보성)"}  # 공정위가 해마다 달리 적은 이름


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_chaebol_history():
    """연도별 지정 자료(chaebol 브랜치). 못 읽으면 None."""
    try:
        res = requests.get(f"https://raw.githubusercontent.com/{DATA_REPO}/{CHAEBOL_HIST_BRANCH}/chaebol_history.json", timeout=(10, 30))
        res.raise_for_status()
        return res.json()
    except Exception:
        return None


def cb_history_key(name, hist):
    """올해 이름 -> 이력 자료의 이름 (표기 차이 보정). 없으면 None."""
    groups = (hist or {}).get("groups", {})
    for cand in (name, CHAEBOL_HIST_ALIAS.get(name), re.sub(r"\(.*", "", name).strip()):
        if cand and cand in groups:
            return cand
    return None


def cb_history_rows(g, hist, year):
    """연도 오름차순 [{'연도','순위','동일인','계열회사','자산'(십억)}]. 올해(g)까지 이어 붙인다."""
    key = cb_history_key(g["name"], hist)
    rows = []
    for y in sorted((hist or {}).get("groups", {}).get(key, {}) if key else {}):
        v = hist["groups"][key][y]
        if int(y) < year:
            rows.append({"연도": int(y), "순위": v.get("rank"), "동일인": v.get("owner"), "계열회사": v.get("n"), "자산": v.get("asset")})
    rows.append({"연도": year, "순위": int(g["rank"]), "동일인": g["owner"], "계열회사": g["n"], "자산": g["asset"]})
    return rows


def render_chaebol_history(g, unit, year):
    hist = get_chaebol_history()
    rows = cb_history_rows(g, hist, year)
    if len(rows) < 2:
        return
    key = cb_history_key(g["name"], hist)
    with st.expander(f"연도별 이력 · {rows[0]['연도']}~{year}년 지정 기준"):
        df = pd.DataFrame(rows)
        table = pd.DataFrame({"연도": df["연도"].astype(str), "순위": df["순위"].map(lambda v: "-" if v is None else f"{int(v)}위"),
                              "동일인": df["동일인"], "계열회사": df["계열회사"].map(lambda v: "-" if v is None else f"{int(v)}곳"),
                              f"공정자산({unit})": df["자산"].map(lambda v: cb_money(v, unit))})
        st.dataframe(table, hide_index=True, height=(len(table) + 1) * 35 + 3)
        chart = pd.DataFrame({"공정자산": [None if v is None else v * 1e9 / UNITS[unit][0] for v in df["자산"]]}, index=df["연도"].astype(str))
        st.line_chart(chart, height=200)
        st.caption(f"공정자산 추이({unit}) · 매년 5월 1일 지정 기준 · 공정위 각 연도 지정 자료")
        changes = (hist or {}).get("changes", {}).get(key or "", {})
        years = [y for y in sorted(changes, reverse=True)]
        if years:
            pick = st.selectbox("계열회사 변동 연도", years, key=f"cb_hist_year_{g['rank']}", format_func=lambda y: f"{y}년 지정 (전년 5월 → 그해 5월)")
            items = changes[pick]
            n_in = sum(1 for c in items if c["t"] == "편입")
            st.caption(f"편입 {n_in}곳 · 제외 {len(items) - n_in}곳")
            st.dataframe(pd.DataFrame(items).rename(columns={"t": "구분", "r": "사유", "c": "회사명"}), hide_index=True,
                         height=min(len(items) + 1, 12) * 35 + 3)


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
    render_chaebol_history(g, unit, CHAEBOL_YEAR)

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
    q = {"code": code, "day": day, "row": row, "market": row.market, "caps": krx_caps(rows).get(code, row.cap), "ret": {}}
    targets = [(label, day - timedelta(days=n)) for label, n in BRIEF_RETURNS] + [("연초 이후", date(day.year - 1, 12, 31))]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        got = list(pool.map(lambda t: krx_on_or_before(t[1], market), targets))
    for (label, _), (d, past) in zip(targets, got):
        p = past.get(code)
        q["ret"][label] = (row.close / p.close - 1) * 100 if p and p.close else None
    return q


def quote_range(data, code, close, days=250):
    """시세 자료(prices.json)의 최근 약 1년 종가로 52주(자료 기간) 최고·최저와 현재가의 거리.
    -> {"hi", "lo", "from_hi"(%), "from_lo"(%), "n"(일수), "ratio"(직전 20거래일 평균 대비 마지막 날 거래대금 배수 또는 None)} 또는 None"""
    s = (data or {}).get("s", {}).get(code)
    if not s or not close:
        return None
    closes = [c for c in s["c"][-days:] if c]
    if len(closes) < 20:
        return None
    hi, lo = max(closes + [close]), min(closes + [close])
    ratio = None
    snap = sc.snapshot(data, data["dates"][-1])
    row = snap[snap["code"] == code]
    if not row.empty and not pd.isna(row.iloc[0]["ratio"]):
        ratio = float(row.iloc[0]["ratio"])
    return {"hi": hi, "lo": lo, "from_hi": (close / hi - 1) * 100, "from_lo": (close / lo - 1) * 100, "n": len(closes), "ratio": ratio}


def render_quote(q):
    st.markdown("##### 주가 지표")
    if q is None:
        st.info("KRX에서 이 종목의 시세를 찾지 못했어요. 비상장사이거나 코넥스 종목일 수 있어요.")
        return
    r = q["row"]
    try:
        rng = quote_range(get_prices(), q.get("code"), r.close)
    except Exception:
        rng = None
    cards = [("거래대금", won_text(r.val) if r.val else "-", f"직전 20일 평균의 {rng['ratio']:.1f}배" if rng and rng["ratio"] else (f"{r.vol:,.0f}주" if r.vol else None), "flat")]
    cards.append(("상장주식수", f"{r.shares:,.0f}주" if r.shares else "-", q["market"], "flat"))
    if rng:
        cards.append(("52주 최고 대비", pct_text(rng["from_hi"], 1), f"최고가 {rng['hi']:,.0f}원", None))
        cards.append(("52주 최저 대비", pct_text(rng["from_lo"], 1), f"최저가 {rng['lo']:,.0f}원", None))
    st.markdown(cb_cards(cards), unsafe_allow_html=True)
    ret = pd.DataFrame([{"기간": k, "수익률": pct_text(v)} for k, v in q["ret"].items()]).set_index("기간").T
    st.dataframe(ret.style.map(price_style), height=(len(ret) + 1) * 35 + 3)
    st.caption(f"KRX {q['day']:%Y.%m.%d} 종가 기준" + (f" · 52주는 종가 기준 최근 {rng['n']}거래일" if rng else ""))


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
MAX_OVERSEAS = 20
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
# 외신 검색용 영문 이름 (한글 이름 -> 영문 검색어). 해외 기업 탭에 있는 회사는 여기서 영문으로 바꿔 검색한다.
FOREIGN_ENGLISH = {
    "NVDA": "Nvidia", "AMD": "AMD", "MU": "Micron", "INTC": "Intel", "QCOM": "Qualcomm", "AVGO": "Broadcom", "TSM": "TSMC", "ASML": "ASML",
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet", "AMZN": "Amazon", "META": "Meta", "TSLA": "Tesla", "GM": "General Motors",
    "F": "Ford", "TM": "Toyota", "LMT": "Lockheed Martin", "RTX": "RTX", "NOC": "Northrop Grumman", "GD": "General Dynamics", "BA": "Boeing",
    "XOM": "Exxon Mobil", "CVX": "Chevron", "COP": "ConocoPhillips", "MPC": "Marathon Petroleum", "VLO": "Valero", "NUE": "Nucor",
    "STLD": "Steel Dynamics", "MT": "ArcelorMittal", "VZ": "Verizon", "T": "AT&T", "TMUS": "T-Mobile", "LLY": "Eli Lilly",
    "JNJ": "Johnson & Johnson", "PFE": "Pfizer", "MRK": "Merck", "ABBV": "AbbVie",
    "2330.TW": "TSMC", "2454.TW": "MediaTek", "2303.TW": "UMC", "285A.T": "Kioxia", "7203.T": "Toyota", "7267.T": "Honda", "7201.T": "Nissan",
    "7269.T": "Suzuki", "7270.T": "Subaru", "7261.T": "Mazda", "7211.T": "Mitsubishi Motors", "688825.SS": "CXMT", "688981.SS": "SMIC",
    "688347.SS": "Hua Hong Semiconductor", "002594.SZ": "BYD", "0175.HK": "Geely", "601633.SS": "Great Wall Motor", "600104.SS": "SAIC Motor",
    "300750.SZ": "CATL",
}
# 대만·일본 상장사는 야후 파이낸스로 조회한다 (코드 -> (이름, 보고 통화, 영문·한글 별칭))
ASIA_COMPANIES = {
    "2330.TW": ("TSMC", "TWD", ("tsmc", "taiwansemiconductor", "대만tsmc", "티에스엠씨")),
    "2454.TW": ("미디어텍", "TWD", ("mediatek", "미디어텍")),
    "2303.TW": ("UMC", "TWD", ("umc", "unitedmicroelectronics", "유나이티드마이크로")),
    "285A.T": ("키옥시아", "JPY", ("kioxia", "키옥시아")),
    "7203.T": ("도요타", "JPY", ("toyota", "도요타도요다", "토요타", "도요다")),
    "7267.T": ("혼다", "JPY", ("honda", "혼다")),
    "7201.T": ("닛산", "JPY", ("nissan", "닛산")),
    "7269.T": ("스즈키", "JPY", ("suzuki", "스즈키")),
    "7270.T": ("스바루", "JPY", ("subaru", "스바루")),
    "7261.T": ("마쓰다", "JPY", ("mazda", "마쓰다", "마즈다")),
    "7211.T": ("미쓰비시자동차", "JPY", ("mitsubishimotors", "미쓰비시자동차", "미쓰비시모터스")),
    "688825.SS": ("창신메모리", "CNY", ("cxmt", "changxinmemory", "창신메모리", "창신메모리테크놀로지", "长鑫科技")),
    "688981.SS": ("SMIC", "CNY", ("smic", "중신궈지", "중芯국제", "중신국제")),
    "688347.SS": ("화홍반도체", "CNY", ("huahong", "huahongsemiconductor", "화홍반도체", "화홍")),
    "002594.SZ": ("BYD", "CNY", ("byd", "비야디")),
    "0175.HK": ("지리자동차", "CNY", ("geely", "지리", "지리자동차", "길리")),
    "601633.SS": ("창청자동차", "CNY", ("greatwall", "greatwallmotor", "창청자동차", "장성자동차")),
    "600104.SS": ("상하이자동차", "CNY", ("saic", "saicmotor", "상하이자동차", "상기집단")),
    "300750.SZ": ("CATL", "CNY", ("catl", "닝더스다이", "寧德時代", "영덕시대")),
}
OVERSEAS_NAMES.update({k: v[0] for k, v in ASIA_COMPANIES.items()})
OVERSEAS_GROUPS = {
    "반도체": ["NVDA", "AMD", "MU", "INTC", "QCOM", "AVGO", "ASML", "2330.TW", "2454.TW", "2303.TW", "285A.T", "688825.SS", "688981.SS", "688347.SS"],
    "빅테크": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
    "자동차": ["TSLA", "GM", "F", "7203.T", "7267.T", "7201.T", "7269.T", "7270.T", "7261.T", "7211.T", "002594.SZ", "0175.HK", "601633.SS", "600104.SS", "300750.SZ"],
    "방산·항공우주": ["LMT", "RTX", "NOC", "GD", "BA"],
    "에너지·정유": ["XOM", "CVX", "COP", "MPC", "VLO"],
    "철강": ["NUE", "STLD", "MT"],
    "통신": ["VZ", "T", "TMUS"],
    "제약·바이오": ["LLY", "JNJ", "PFE", "MRK", "ABBV"],
}
KOREAN_TO_TICKER = {re.sub(r"\s+", "", v).lower(): k for k, v in OVERSEAS_NAMES.items()}
# 산업 뉴스 > 외신의 섹터: 재무 조회 '해외 섹터'(OVERSEAS_GROUPS)와 같은 기업을 티커 대신 영문 기업명으로. 반도체에는 Samsung을 더함
GLOBAL_SECTORS = {g: list(dict.fromkeys(FOREIGN_ENGLISH.get(t) or OVERSEAS_NAMES.get(t) or t for t in tickers)) for g, tickers in OVERSEAS_GROUPS.items()}
GLOBAL_SECTORS["반도체"].append("Samsung")
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


def asia_resolve(text):
    """대만·일본 상장사 입력(코드 2330·285A·2330.TW, 한글·영문 이름) -> (코드, "YF", 이름) 또는 None"""
    t = re.sub(r"[\s\-_.()]", "", text).lower()
    if not t:
        return None
    for code, (name, _, aliases) in ASIA_COMPANIES.items():
        base = code.split(".")[0].lower()
        suffix_ok = t.startswith(base) and t[len(base):] in ("", "ss", "sh", "sz", "hk", "tw", "t")  # 688825, 688825.SH 등
        if suffix_ok or t == name.lower().replace(" ", "") or t in aliases:
            return code, "YF", name
    return None


YF_TTL = 6 * 3600
YF_PARSE_VER = 1
YF_ROWS = {
    "rev": ("Total Revenue", "Operating Revenue"),
    "op": ("Operating Income",),
    "net": ("Net Income Common Stockholders", "Net Income"),
    "gp": ("Gross Profit",),
    "eps": ("Diluted EPS",),
    "ocf": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
    "capex": ("Capital Expenditure",),
    "da": ("Depreciation And Amortization", "Depreciation Amortization Depletion", "Reconciled Depreciation"),
}


def yf_frames(code):
    """야후 파이낸스에서 (분기 손익, 연간 손익, 분기 현금흐름, 연간 현금흐름) DataFrame을 받는다. 실패하면 SecError."""
    try:
        import yfinance as yf
    except ImportError:
        raise SecError("yfinance 패키지가 설치되어 있지 않아요 (requirements.txt에 yfinance 추가 필요)")
    try:
        tk = yf.Ticker(code)
        return tk.quarterly_income_stmt, tk.income_stmt, tk.quarterly_cashflow, tk.cashflow
    except Exception as e:
        msg = str(e)
        if "Too Many" in msg or "RateLimit" in type(e).__name__ or "429" in msg:
            raise SecError("야후 파이낸스가 접속을 일시 제한했어요 - 잠시 뒤 다시 시도해주세요")
        raise SecError(f"야후 파이낸스 조회 실패: {msg[:80]}")


def _yf_cell(df, names, col):
    if df is None or getattr(df, "empty", True) or col not in df.columns:
        return None
    for n in names:
        if n in df.index:
            v = df.loc[n, col]
            if isinstance(v, pd.Series):
                v = v.iloc[0]
            if pd.notna(v):
                return float(v)
    return None


def yf_parse(code, frames):
    """야후 표 -> sec_parse와 같은 모양 {"name", "cur", "annual", "quarter"}"""
    qi, ai, qc, ac = frames
    name, cur = ASIA_COMPANIES[code][0], ASIA_COMPANIES[code][1]

    def build(inc, cf, days):
        out = []
        if inc is None or getattr(inc, "empty", True):
            return out
        for col in inc.columns:
            try:
                end = pd.Timestamp(col).date()
            except Exception:
                continue
            rec = {m: _yf_cell(inc, YF_ROWS[m], col) for m in ("rev", "op", "net", "gp", "eps")}
            if all(rec[m] is None for m in ("rev", "op", "net")):
                continue
            cfc = None
            if cf is not None and not getattr(cf, "empty", True):
                cfc = next((c for c in cf.columns if abs((pd.Timestamp(c).date() - end).days) <= 5), None)
            for m in ("ocf", "capex", "da"):
                rec[m] = _yf_cell(cf, YF_ROWS[m], cfc) if cfc is not None else None
            if rec["capex"] is not None:
                rec["capex"] = abs(rec["capex"])
            out.append({"start": end - timedelta(days=days), "end": end, "derived": False, **rec, "accn": ""})
        return sorted(out, key=lambda p: p["end"])

    return {"name": name, "cur": cur, "annual": build(ai, ac, 364), "quarter": build(qi, qc, 91)}


def yf_company(code):
    """대만·일본 회사 하나의 재무 (6시간 저장). 자료가 없으면 SecError."""
    s = _sec_store()
    ck = ("yf", code, YF_PARSE_VER)
    with s["lock"]:
        hit = s["facts"].get(ck)
    if hit and time.time() - hit[0] < YF_TTL:
        return hit[1]
    co = yf_parse(code, yf_frames(code))
    if not co["annual"] and not co["quarter"]:
        raise SecError("야후 파이낸스에 매출·이익 자료 없음")
    with s["lock"]:
        s["facts"][ck] = (time.time(), co)
    return co


def yf_fx(cur, start, end):
    """야후 파이낸스 환율(통화->원)로 기간 평균·기간 말을 구한다. 못 받으면 None."""
    try:
        import yfinance as yf
        h = yf.Ticker(f"{cur}KRW=X").history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat())["Close"].dropna()
    except Exception:
        return None
    if len(h) == 0:
        return None
    return {"avg": float(h.mean()), "last": float(h.iloc[-1]), "last_date": pd.Timestamp(h.index[-1]).date()}


def yf_link(code):
    return f"https://finance.yahoo.com/quote/{code}/financials/"


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
            out = yf_fx(cur, start, end)  # 지원하지 않는 통화 (예: 대만달러)는 야후 환율로
            break
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
            co = yf_company(ticker) if cik == "YF" else sec_company(cik)
        except SecError as e:
            return {"error": str(e)}
        now, prev = sec_pick(co, year, period)
        r = {"co": co, "now": now, "prev": prev, "fx": None, "error": None}
        if now is None and cik == "YF":
            last = max((p["end"] for p in co["quarter"] + co["annual"]), default=None)
            return {"error": "해당 기간 자료 없음 - 야후 파이낸스는 최근 4~5개 분기, 연간 4년까지만 제공해요" + (f" (가장 최근 {last:%Y-%m-%d} 종료분)" if last else "")}
        if now is None:
            have = sorted({approx_end(p["end"]).year for p in co["annual"]})
            last_q = max((p["end"] for p in co["quarter"]), default=None)
            found = (f" (EDGAR에 있는 자료: 연간 {have[0]}~{have[-1]}년" + (f", 분기는 {last_q:%Y-%m-%d} 종료분까지" if last_q else "") + ")") if have else ""
            r["error"] = ("분기 자료 없음 - 연 1회만 공시하는 기업" + found if period != "연간" and not co["quarter"]
                          else "해당 기간 자료 없음 - 아직 공시 전이거나 EDGAR에 반영 전일 수 있어요" + found)
        else:
            r["fx"] = fx_range(co["cur"], now["start"], now["end"])
            r["fx_usd"] = r["fx"] if co["cur"] == "USD" else fx_range("USD", now["start"], now["end"])
            recent = TODAY - timedelta(days=10)  # 현재(오늘) 환율: 최근 10일 중 가장 늦은 날 값 (주말·휴일이면 직전 영업일)
            r["fx_now"] = fx_range(co["cur"], recent, TODAY)
            r["fx_usd_now"] = r["fx_now"] if co["cur"] == "USD" else fx_range("USD", recent, TODAY)
            if cik == "YF":
                r["link"] = yf_link(ticker)
                return r
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
    if mode == "현재":
        fx = entry.get("fx_now")
        return (fx["last"], "자동") if fx else (None, "")
    fx = entry.get("fx")
    if fx:
        return fx["avg" if mode == "기간 평균" else "last"], "자동"
    return None, ""


def ovs_factor(entry, view, mode, overrides):
    """표시 통화로 바꾸는 곱셈 값 -> (값 또는 None, 구분). 현지 통화는 1, 원화 환산은 원/통화, 달러 환산은 달러/통화."""
    cur = entry["co"]["cur"]
    if view == "현지 통화":
        return 1.0, ""
    rate, how = ovs_rate(entry, mode, overrides.get(cur))
    if view == "원화 환산":
        return rate, how
    if cur == "USD":
        return 1.0, "자동"
    if rate is None:
        return None, ""
    usd, how_usd = ovs_rate({"fx": entry.get("fx_usd"), "fx_now": entry.get("fx_usd_now")}, mode, overrides.get("USD"))
    if not usd:
        return None, ""
    return rate / usd, ("직접 입력" if "직접 입력" in (how, how_usd) else "자동")


def ovs_unit_label(cur, view, unit):
    """표의 '단위' 칸에 적을 글자"""
    if view == "원화 환산":
        return unit  # 억원, 조원
    return "억" + ("USD" if view == "달러 환산" else ("원" if cur == "KRW" else cur))


def ovs_table(res, view, unit, mode, overrides):
    """조회 결과 -> (표, 환율 표, 환율이 없어 원화로 못 바꾼 회사)"""
    rows, rate_rows, no_rate = [], [], []
    krw = view == "원화 환산"
    for ticker, entry in res["data"].items():
        label = res['names'][ticker]
        now, prev, co = entry.get("now"), entry.get("prev"), entry.get("co")
        if not now:
            continue
        cur = co["cur"]
        mult, how = ovs_factor(entry, view, mode, overrides)
        if view != "현지 통화" and mult is None:
            no_rate.append(label)
        if mult is not None and view != "현지 통화" and cur != ("KRW" if krw else "USD"):
            fx_ref = (entry.get("fx_now") if mode == "현재" else entry.get("fx")) or {}
            rate_rows.append({"통화": f"{cur} ({CURRENCY_NAMES.get(cur, cur)})",
                              f"적용 환율({'원' if krw else '달러'})": f"{mult:,.2f}" if krw else f"{mult:,.4f}", "구분": how,
                              "기준": (f"{now['start']:%Y.%m.%d}~{now['end']:%Y.%m.%d} 평균" if mode == "기간 평균" and how == "자동"
                                     else (f"{fx_ref['last_date']:%Y.%m.%d} 종가" + (" (현재)" if mode == "현재" else "") if how == "자동" and fx_ref else "직접 입력")),
                              "회사": label})

        def show(v):
            if v is None:
                return "-"
            if view == "현지 통화":
                return f"{round(v / 1e8) + 0.0:,.0f}"
            if mult is None:
                return "-"
            return fmt_amount(v * mult, unit) if krw else f"{round(v * mult / 1e8) + 0.0:,.0f}"

        pv = prev or {}
        margin = now["op"] / now["rev"] * 100 if now["op"] is not None and now["rev"] else None
        period_note = "4분기 계산" if now.get("derived") else ""
        rows.append({
            "회사": label, "단위": ovs_unit_label(cur, view, unit), "결산 종료일": f"{now['end']:%Y-%m-%d}" + (f" ({period_note})" if period_note else ""),
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
        mult, _ = ovs_factor(entry, view, mode, overrides)

        def amt(v):
            if v is None:
                return "-"
            if view == "현지 통화":
                return f"{round(v / 1e8) + 0.0:,.0f}"
            if mult is None:
                return "-"
            return fmt_amount(v * mult, unit) if krw else f"{round(v * mult / 1e8) + 0.0:,.0f}"

        def eps_text(v):
            if v is None:
                return "-"
            if view == "현지 통화":
                return f"{v:,.2f}"
            if mult is None:
                return "-"
            return f"{v * mult:,.0f}원" if krw else f"{v * mult:,.2f}"

        pv = prev or {}
        gm = now["gp"] / now["rev"] * 100 if now.get("gp") is not None and now.get("rev") else None
        rows.append({
            "회사": res['names'][ticker], "단위": ovs_unit_label(co["cur"], view, unit),
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
    st.session_state.setdefault("ovs_corps", "")  # 처음엔 섹터도 기업도 비워 둔다
    st.radio("섹터", list(OVERSEAS_GROUPS), index=None, horizontal=True, key="ovs_group", on_change=fill_overseas)
    ly, lp = latest_overseas_period()
    years = list(range(TODAY.year, 2014, -1))
    with st.form("ovs_search"):
        corp_text = st.text_input(
            "티커 또는 회사 이름 (쉼표로 구분)", key="ovs_corps", placeholder="예: NVDA, 마이크론, Intel",
            help=(f"티커(NVDA), 영문 이름, 주요 기업은 한글 이름으로도 찾아요. 대만·일본·중국은 2330, 7203, 688825처럼 종목 코드도 돼요. 최대 {MAX_OVERSEAS}곳.\n\n"
                  "미국 SEC에 공시하는 기업의 실적이에요 (연결 기준, 각사 결산월 기준). 연 1회만 공시하는 외국 기업은 연간만 나와요.\n\n"
                  "대만·일본·중국 상장사(TSMC·도요타·BYD 등)는 야후 파이낸스 자료라 최근 4~5개 분기, 연간 4년까지만 조회돼요."),
        )
        c1, c2 = st.columns(2)
        year = c1.selectbox("연도 (달력 기준)", years, index=years.index(ly), key="ovs_year")
        period = c2.radio("기간", OVS_PERIODS, index=OVS_PERIODS.index(lp), horizontal=True, key="ovs_period",
                          help="분기는 달력 분기에 가장 가까운 회사별 결산 분기를 골라요. 연간은 회계연도 종료일이 그 해에 있는 해예요.")
        submitted = st.form_submit_button("해외 기업 조회")

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
                        asia = asia_resolve(n)
                        hit, alts, by_name = (asia, [], False) if asia else sec_resolve(n)
                        if not hit:
                            missing.append((n, "SEC 목록에 없음 - 티커(예: NVDA)로 입력해보세요. 대만·일본·중국은 2330, 7203, 688825처럼 종목 코드도 돼요"))
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

    view = st.radio("표시 통화", ["현지 통화", "달러 환산", "원화 환산"], horizontal=True, key="ovs_view",
                    help="현지 통화는 각사가 보고하는 통화(달러·엔·대만달러·위안 등) 그대로, 환산은 환율로 한 통화로 맞춰 보여줘요. 단위는 표의 '단위' 칸에 적혀 있어요.")
    krw_on = view == "원화 환산"
    fx_on = view != "현지 통화"
    unit, mode = list(UNITS)[1], "기간 평균"
    if fx_on:
        with st.container(key="ovs_fx_bar"):  # 모바일에서도 단위·환율 기준을 한 줄에 나란히
            c1, c2 = st.columns([2, 3]) if krw_on else (st.container(), st.container())
            if krw_on:
                unit = c1.radio("단위", list(UNITS), index=list(UNITS).index("조원"), horizontal=True, key="ovs_unit")
            mode = c2.radio("환율 기준", ["기간 평균", "기간 말", "현재"], horizontal=True, key="ovs_fxmode",
                            help="손익은 한 기간 동안 번 돈이라 보통 기간 평균 환율로 바꿔요. 기간 말은 결산일 환율, 현재는 오늘(가장 최근 영업일) 환율이에요.")
    cur_set = {e["co"]["cur"] for e in res["data"].values() if e.get("now")}
    currencies = sorted(cur_set - {"KRW"}) if krw_on else (sorted(cur_set | {"USD"}) if fx_on and cur_set - {"USD"} else [])
    overrides = {}
    body = st.container()  # 표 영역. 적용 환율 칸이 표·안내 줄 바로 밑에 오도록 자리를 먼저 잡는다
    fx_box = st.expander("적용 환율 보기·직접 입력") if fx_on else None
    if fx_box is not None:
        with fx_box:
            st.caption("자동 환율은 유럽중앙은행 기준환율(Frankfurter, 없는 통화는 야후 파이낸스)로 계산한 원화 환율이에요. 한국은행 매매기준율과 조금 다를 수 있어요. "
                       "기사에 쓸 환율이 따로 있으면 아래에 입력하면 그 값으로 바꿔요 (0이면 자동)."
                       + (" 달러 환산은 '통화→원 ÷ 달러→원'으로 계산해요." if not krw_on else ""))
            cols = st.columns(max(len(currencies), 1))
            for col, cur in zip(cols, currencies):
                overrides[cur] = col.number_input(f"{cur} 1{CURRENCY_NAMES.get(cur, '')} = ? 원", min_value=0.0, value=0.0, step=1.0,
                                                  key=f"ovs_fx_{cur}")
    table, rates, no_rate = ovs_table(res, view, unit, mode, overrides)
    when = f"{res['year']}년 " + ("연간" if res["period"] == "연간" else f"{res['period'][0]}분기")
    with body:
        st.caption(f"{when} (달력 기준에 가장 가까운 결산 기간) · 연결 기준")
        if no_rate:
            st.warning("환율을 자동으로 구하지 못해 원화로 바꾸지 못한 회사: " + ", ".join(no_rate) + ". 아래 '적용 환율 보기·직접 입력'을 열어 환율을 넣어주세요.")
        st.dataframe(
            table.style.map(amount_style, subset=["매출액", "영업이익", "순이익"]).apply(total_row_style, axis=1),
            hide_index=True, height=(len(table) + 1) * 35 + 3,
            column_config={"회사": st.column_config.TextColumn("회사", width=105), "단위": st.column_config.TextColumn("단위", width=60), "매출액": st.column_config.TextColumn("매출액", width=80), "결산 종료일": st.column_config.TextColumn("결산 종료일", width=130),
                           "원문": st.column_config.LinkColumn("원문", display_text="보기")},
        )
        miss = ovs_missing_line(res)
        if miss:
            st.caption(miss)
        more_on = st.toggle("지표 더 보기", key="ovs_more", help="주당순이익, 매출총이익률, EBITDA, 영업현금흐름, 잉여현금흐름을 표 아래에 더 보여줘요.")
    if fx_box is not None and not rates.empty:
        with fx_box:
            st.markdown("**적용 환율**")
            st.dataframe(rates, hide_index=True, height=(len(rates) + 1) * 35 + 3)
    if more_on:
        extra = ovs_extra_table(res, view, unit, mode, overrides)
        st.markdown("##### 추가 지표", help=(
            "EBITDA = 영업이익 + 감가상각비 (회사가 발표하는 조정 EBITDA와 다를 수 있어요)\n\n"
            "잉여현금흐름 = 영업현금흐름 - 설비투자\n\n"
            "주당순이익은 1주당 금액(" + ("원화 환산 시 원" if krw_on else ("달러 환산 시 달러" if fx_on else "현지 통화")) + ")이고, 4분기 계산 기간은 비워요\n\n"
            "조정(Non-GAAP) 실적과 가이던스는 SEC 데이터에 없어요. 첫 표의 '원문'(실적 발표 8-K)에서 확인하세요"))
        st.dataframe(extra.style.map(amount_style, subset=["EBITDA", "영업현금흐름", "잉여현금흐름"]), hide_index=True,
                     height=(len(extra) + 1) * 35 + 3, column_config={"회사": st.column_config.TextColumn("회사", width=105), "단위": st.column_config.TextColumn("단위", width=60)})
    st.download_button("CSV 다운로드", table.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"해외기업_{res['year']}_{res['period']}.csv", mime="text/csv", key="ovs_csv")
    st.caption("자료: 미국 SEC EDGAR(XBRL), 환율: 유럽중앙은행 기준환율(Frankfurter) · 4분기는 '연간 - 3분기 누적'으로 계산한 값이에요 · "
               "'원문'은 실적 발표 8-K 공시로 연결하고, 찾지 못하면 분기·연간 보고서 공시로 연결해요 · "
               "대만·일본·중국 기업은 야후 파이낸스(비공식 집계) 재무 페이지로 연결되며, 기사에 쓰기 전 각사 실적 발표로 확인하세요")


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
[class*="secbar"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem !important; align-items: center; }
[class*="secbar"] [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 0 0 auto !important; }
[class*="secbar"] [data-testid="stColumn"]:first-child { flex: 1 1 auto !important; }
[class*="secbar"] button { min-height: 0 !important; padding: 0.1rem 0.7rem !important; border-radius: 999px !important; font-size: 0.8rem !important; font-weight: 600 !important; color: #1c7ed6 !important; border-color: #1c7ed6 !important; background: #fff !important; }
[class*="secbar"] button p { font-size: 0.8rem !important; }
.sec-lbl { font-size: 0.9rem; color: #31333f; white-space: nowrap; }
.mode-lbl { font-size: 0.9rem; font-weight: 600; color: #334155; white-space: nowrap; }
.st-key-fin_mode_bar [data-testid="stHorizontalBlock"], .st-key-fin_unit_bar [data-testid="stHorizontalBlock"], .st-key-disc_mode_bar [data-testid="stHorizontalBlock"], .st-key-news_mode_bar [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem !important; }  /* 모바일에서도 한 줄 */
.st-key-fin_mode_bar [data-testid="stColumn"], .st-key-fin_unit_bar [data-testid="stColumn"], .st-key-disc_mode_bar [data-testid="stColumn"], .st-key-news_mode_bar [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; }
.st-key-fin_mode_bar [data-testid="stColumn"]:first-child, .st-key-fin_unit_bar [data-testid="stColumn"]:first-child, .st-key-disc_mode_bar [data-testid="stColumn"]:first-child, .st-key-news_mode_bar [data-testid="stColumn"]:first-child { flex: 0 0 auto !important; }
.st-key-news_opts_bar [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem !important; }  /* 모바일에서도 한 줄 */
.st-key-news_opts_bar [data-testid="stColumn"] { min-width: 0 !important; }
.st-key-ovs_fx_bar [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.75rem !important; }  /* 모바일에서도 한 줄 */
.st-key-ovs_fx_bar [data-testid="stColumn"] { min-width: 0 !important; }

.block-container { max-width: 1080px; padding-top: 2.5rem; padding-bottom: 4rem; }

h1 { font-size: 2.15rem !important; font-weight: 800 !important; letter-spacing: -0.045em; line-height: 1.2 !important; color: #0b2545 !important; padding: 0.2rem 0 0.1rem !important;
     background: linear-gradient(100deg, #0b2545 0%, #1c4f9c 55%, #1c7ed6 100%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
[data-testid="stMainBlockContainer"] { position: relative; }
.st-key-alarm_top { position: absolute; top: 3.9rem; right: 1rem; width: auto !important; z-index: 5; }
.st-key-calc_box { width: min(26rem, calc(100vw - 3rem)) !important; }  /* 계산기 창: 칸마다 같은 크기 (높이는 st.container(height)로 고정, 넘치면 안에서 스크롤) */
.calc-out { display: grid; grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr)); gap: 0.4rem; margin: 0.4rem 0; }
.calc-out .cc { background: #f4f7fb; border-radius: 10px; padding: 0.35rem 0.6rem; }
.calc-out .l { display: block; font-size: 0.72rem; color: #7a8699; }
.calc-out .v { display: block; font-size: 0.98rem; font-weight: 700; color: #0b2545; word-break: break-all; }
.st-key-alarm_top > div { width: auto !important; flex: 0 0 auto !important; }
.st-key-alarm_top button { min-height: 0 !important; width: auto !important; justify-content: center !important; white-space: nowrap !important; padding: 0.2rem 0.8rem !important; font-size: 0.78rem !important; border-radius: 999px !important; border: 1px solid #d5deea !important; background: #ffffff !important; color: #33415c !important; box-shadow: 0 1px 4px rgba(16, 42, 84, 0.06); }
.st-key-alarm_top button [aria-hidden="true"]:has([data-testid="stIconMaterial"]) { display: none !important; }
.st-key-alarm_top button > div, .st-key-alarm_top button > div > div { margin: 0 !important; padding: 0 !important; gap: 0 !important; width: auto !important; justify-content: center !important; }  /* 펼침 화살표 숨김 (자리까지 없애 글자가 가운데 오게) */
.st-key-alarm_top button * { overflow: visible !important; text-overflow: clip !important; max-width: none !important; }
.st-key-alarm_top > div, .st-key-alarm_top [data-testid="stPopover"] { width: auto !important; min-width: 0 !important; }
.st-key-alarm_top button:hover { border-color: #1c7ed6 !important; color: #1c7ed6 !important; }
h1 a { display: none !important; }  /* 제목 옆 링크 아이콘 숨김 */
[data-testid="stElementContainer"]:has(h1) + [data-testid="stElementContainer"] [data-testid="stCaptionContainer"] { font-size: 0.78rem; letter-spacing: 0.06em; color: #7a8699; margin-top: 0.1rem; }
[data-testid="stElementContainer"]:has(h1) + [data-testid="stElementContainer"] [data-testid="stCaptionContainer"] p::before { content: ""; display: inline-block; width: 1.6rem; height: 3px; border-radius: 2px; background: #1c7ed6; vertical-align: middle; margin-right: 0.5rem; }
[data-testid="stCaptionContainer"] { color: #7a8699; }

/* 탭: 모바일은 한 줄에 5개씩 2줄 바둑판으로 (옆으로 밀지 않아도 모두 보이게) */
[data-testid="stTabs"] [role="tablist"] {
    display: grid !important; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 0.3rem;
    overflow: visible !important; width: 100% !important; border-bottom: 1px solid #dde3ec !important; padding-bottom: 0.75rem; margin-bottom: 1rem;  /* 탭과 내용 사이 구분선 */
}
[data-testid="stTab"] {
    display: flex; align-items: center; justify-content: center; min-width: 0; margin: 0 !important;
    padding: 0.55rem 0.2rem !important; border: 1px solid #dde3ec; border-radius: 10px; background: #ffffff; cursor: pointer;
}
[data-testid="stTab"] p { font-size: 0.8rem; font-weight: 600; white-space: nowrap; margin: 0; color: #334155; letter-spacing: -0.02em; }
[data-testid="stTab"]:hover { border-color: #1c7ed6; }
[data-testid="stTab"][aria-selected="true"] { background: #1c7ed6; border-color: #1c7ed6; }
[data-testid="stTab"][aria-selected="true"] p { color: #ffffff !important; }
[data-testid="stTab"] > div:not([data-testid]) { display: none; }  /* 기본 밑줄 표시는 숨김 */
[data-testid="stTabs"] [role="tablist"]::before, [data-testid="stTabs"] [role="tablist"]::after { display: none !important; }
[data-testid="stTabs"] [role="tablist"] { box-shadow: none !important; background: none !important; }
@media (min-width: 900px) { [data-testid="stTabs"] [role="tablist"] { display: flex !important; flex-wrap: nowrap; } [data-testid="stTab"] { flex: 1 1 0; } [data-testid="stTab"] p { font-size: 0.84rem; } }  /* PC: 탭이 몇 개든 한 줄에 (모바일은 5개씩 바둑판) */
@media (max-width: 400px) { [data-testid="stTab"] p { font-size: 0.72rem; } [data-testid="stTab"] { padding: 0.5rem 0.1rem !important; } }  /* 작은 휴대폰에서도 글자가 칸 안에 */
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
/* 기업 브리핑·수출입 분석·재무 조회: 카드형 화면 (다른 탭에는 영향 없음) */
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) h5 { display: flex; align-items: center; gap: 0.5rem; font-size: 1.05rem !important; font-weight: 800 !important; color: #0b2545 !important; margin: 0.9rem 0 0.5rem !important; padding: 0 !important; }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) h5::before { content: ""; width: 4px; height: 1.05em; border-radius: 2px; background: #1c7ed6; flex: 0 0 auto; }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) [data-testid="stVerticalBlockBorderWrapper"] { background: #ffffff; border: 1px solid #e3e8ef !important; border-radius: 16px !important; box-shadow: 0 2px 12px rgba(16, 42, 84, 0.06); padding: 0.4rem 0.6rem 0.6rem; }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) [data-testid="stVerticalBlockBorderWrapper"] h5:first-child { margin-top: 0.3rem !important; }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) .cb-stats { gap: 0.6rem; }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) .cb-card { border-radius: 14px; padding: 0.8rem 0.95rem; background: linear-gradient(160deg, #ffffff 0%, #f1f6fd 100%); box-shadow: 0 2px 10px rgba(16, 42, 84, 0.05); }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) .cb-card .l { font-size: 0.8rem; }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) .cb-card .v { font-size: 1.5rem; margin: 0.1rem 0; }
@media (max-width: 500px) { :is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) .cb-card .v { font-size: 1.25rem; } }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) .cb-card .d { display: inline-block; font-size: 0.76rem; border-radius: 999px; padding: 0.05rem 0.5rem; background: #eef2f7; }
:is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) .cb-card .d.up { background: #e7f1fd; } :is(.st-key-brief_wrap, .st-key-trade_wrap, .st-key-fin_wrap) .cb-card .d.down { background: #fdecec; }
/* 수출입 검색 조건: PC에서는 종류·품목, 지역·기간을 한 줄에 둘씩 (모바일은 위아래) */
@media (min-width: 900px) {
  .st-key-trade_wrap { flex-direction: row !important; flex-wrap: wrap; column-gap: 1.35rem; }
  .st-key-trade_wrap > * { flex: 0 0 100%; min-width: 0; }
  .st-key-trade_wrap > .st-key-tr_s_kind, .st-key-trade_wrap > .st-key-tr_s_item, .st-key-trade_wrap > .st-key-tr_r_item, .st-key-trade_wrap > .st-key-tr_c_basis, .st-key-trade_wrap > .st-key-tr_s_reg, .st-key-trade_wrap > .st-key-tr_s_period, .st-key-trade_wrap > .st-key-tr_r_level, .st-key-trade_wrap > .st-key-tr_r_period, .st-key-trade_wrap > .st-key-tr_c_reg, .st-key-trade_wrap > .st-key-tr_c_period { flex: 0 0 calc(50% - 0.675rem); }
}
/* 수출입 검색 조건(종류·품목·지역·기간): 이름 바로 옆에 선택지 */
:is(.st-key-tr_s_kind,.st-key-tr_s_item,.st-key-tr_r_item,.st-key-tr_s_reg,.st-key-tr_c_reg,.st-key-tr_s_period,.st-key-tr_c_period,.st-key-tr_r_period,.st-key-tr_r_level,.st-key-tr_c_basis) [data-testid="stRadio"] { display: flex; flex-direction: row; align-items: flex-start; gap: 0.7rem; }
:is(.st-key-tr_s_kind,.st-key-tr_s_item,.st-key-tr_r_item,.st-key-tr_s_reg,.st-key-tr_c_reg,.st-key-tr_s_period,.st-key-tr_c_period,.st-key-tr_r_period,.st-key-tr_r_level,.st-key-tr_c_basis) [data-testid="stRadio"] > label { flex: 0 0 auto; width: 4.1rem; min-width: 4.1rem; margin: 0.15rem 0 0 0; white-space: nowrap; }
:is(.st-key-tr_s_kind,.st-key-tr_s_item,.st-key-tr_r_item,.st-key-tr_s_reg,.st-key-tr_c_reg,.st-key-tr_s_period,.st-key-tr_c_period,.st-key-tr_r_period,.st-key-tr_r_level,.st-key-tr_c_basis) [data-testid="stRadio"] > label * { white-space: nowrap; flex-wrap: nowrap !important; }
:is(.st-key-tr_s_kind,.st-key-tr_s_item,.st-key-tr_r_item,.st-key-tr_s_reg,.st-key-tr_c_reg,.st-key-tr_s_period,.st-key-tr_c_period,.st-key-tr_r_period,.st-key-tr_r_level,.st-key-tr_c_basis) [data-testid="stRadio"] > div[role="radiogroup"] { flex: 1 1 auto; min-width: 0; }
.st-key-tr_r_level [data-testid="stRadio"] > label { width: 5.2rem; min-width: 5.2rem; }
.cb-hero { position: relative; overflow: hidden; background: linear-gradient(135deg, #3d86dc 0%, #5ea6ee 100%); border: 0; border-radius: 18px; padding: 1.2rem 1.3rem; margin: 0.2rem 0 0.8rem; box-shadow: 0 6px 16px rgba(61, 134, 220, 0.25); }
.cb-hero::after { content: ""; position: absolute; right: -2.2rem; top: -2.2rem; width: 9rem; height: 9rem; border-radius: 50%; background: radial-gradient(circle, rgba(255, 255, 255, 0.18) 0%, rgba(255, 255, 255, 0) 70%); pointer-events: none; }
.cb-hero .t { position: relative; font-size: 1.7rem; font-weight: 800; color: #ffffff; letter-spacing: -0.03em; line-height: 1.25; }
.cb-hero .s { position: relative; color: #e6f1ff; font-size: 0.85rem; margin-top: 0.3rem; }
.st-key-trade_wrap { gap: 1.35rem !important; }  /* 수출입 분석: 칸 사이를 넓게 */
.st-key-trade_wrap [data-testid="stRadio"], .st-key-trade_wrap [data-testid="stMultiSelect"] { margin-bottom: 0.2rem; }
/* 섹터 알약(st.pills): 폰에서는 5칸 격자로 줄 끝을 맞춘다 (PC는 한 줄에 다 들어가므로 그대로) */
@media (max-width: 640px) {
  [data-testid="stButtonGroup"] [role="toolbar"]:has(button[data-variant="pills"]) { display: grid !important; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 0.35rem 0.3rem; }
  [data-testid="stButtonGroup"] button[data-variant="pills"] { width: 100%; min-width: 0; margin: 0 !important; padding-left: 0.15rem !important; padding-right: 0.15rem !important; justify-content: center; }
  [data-testid="stButtonGroup"] button[data-variant="pills"] * { max-width: none !important; overflow: visible !important; text-overflow: clip !important; }  /* '에너지·소…'처럼 줄임표로 잘리지 않게 */
  [data-testid="stButtonGroup"] button[data-variant="pills"] p { font-size: 0.74rem !important; white-space: nowrap !important; letter-spacing: -0.03em; }
}
@media (max-width: 400px) { [data-testid="stButtonGroup"] button[data-variant="pills"] p { font-size: 0.68rem !important; } }
/* 오늘의 현황: 섹터 카드 */
.st-key-today_wrap [data-testid="stVerticalBlockBorderWrapper"] { background: #ffffff; border: 1px solid #e3e8ef !important; border-radius: 16px !important; box-shadow: 0 2px 12px rgba(16, 42, 84, 0.06); }
.td-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.4rem 0.8rem; padding: 0.2rem 0 0.5rem; border-bottom: 1px solid #eef2f7; margin-bottom: 0.4rem; }
.td-head .n { display: flex; align-items: center; gap: 0.5rem; font-size: 1.25rem; font-weight: 800; color: #0b2545; letter-spacing: -0.03em; }
.td-head .n::before { content: ""; width: 4px; height: 1.1em; border-radius: 2px; background: #1c7ed6; }
.td-pill { display: inline-block; font-size: 0.76rem; border-radius: 999px; padding: 0.1rem 0.6rem; margin-left: 0.3rem; background: #eef2f7; color: #51607a; }
.td-pill.k1 { background: #fff1e0; color: #b45f06; } .td-pill.k2 { background: #e7f1fd; color: #1864ab; } .td-pill.k3 { background: #e6f6ec; color: #2b8a3e; }
.td-lbl { display: flex; align-items: baseline; gap: 0.45rem; font-size: 0.86rem; font-weight: 800; color: #0b2545; margin: 0.85rem 0 0.4rem; }
.td-lbl small { font-size: 0.76rem; font-weight: 500; color: #7a8699; }
.td-lbl.k1::before, .td-lbl.k2::before { content: ""; align-self: center; width: 0.5rem; height: 0.5rem; border-radius: 50%; background: #f08c00; } .td-lbl.k2::before { background: #1c7ed6; }
.td-movers { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.td-movers .mv { display: inline-flex; align-items: baseline; gap: 0.3rem; font-size: 0.84rem; border-radius: 10px; padding: 0.25rem 0.6rem; }
.td-movers .mv small { color: #7a8699; font-size: 0.72rem; }
.td-movers .mv.up { background: #fdecec; color: #c92a2a; } .td-movers .mv.down { background: #e7f1fd; color: #1864ab; }
.td-ev { display: flex; gap: 0.6rem; align-items: baseline; padding: 0.3rem 0; font-size: 0.88rem; color: #0b2545; }
.td-ev .dt { flex: 0 0 auto; font-size: 0.76rem; font-weight: 700; color: #1864ab; background: #e7f1fd; border-radius: 8px; padding: 0.1rem 0.5rem; }
.td-ev .why, .td-ev .src { color: #7a8699; font-size: 0.8rem; }
.cb-top { display: block; }
@media (min-width: 900px) {
  .cb-top { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2.1fr); gap: 0.8rem; align-items: stretch; margin: 0.2rem 0 0.8rem; }
  .cb-top .cb-hero { margin: 0; display: flex; flex-direction: column; justify-content: center; }
  .cb-top .cb-stats, .cb-top .cb-stats.n4, .cb-top .cb-stats.n3 { grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; }
}
.cb-latest { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0.5rem; margin: 0 0 0.8rem; }
@media (min-width: 700px) { .cb-latest { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.cb-latest .it { background: #ffffff; border: 1px solid #e3e8ef; border-left: 4px solid #1c7ed6; border-radius: 12px; padding: 0.6rem 0.85rem; }
.cb-latest .k { color: #1c7ed6; font-size: 0.75rem; font-weight: 700; }
.cb-latest .m { color: #7a8699; font-size: 0.78rem; margin-left: 0.4rem; }
.cb-latest a, .cb-latest .x { display: block; color: #0b2545; font-weight: 600; text-decoration: none; line-height: 1.45; margin-top: 0.15rem; word-break: keep-all; }
.cb-latest a:hover { color: #1c7ed6; text-decoration: underline; }
</style>
"""

st.set_page_config(page_title="산업 취재 도우미", layout="wide")
st.markdown(STYLE, unsafe_allow_html=True)
st.title("산업 취재 도우미")
st.caption("credit.중앙일보 나상현")

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
# 기업 브리핑 (회사 하나의 실적·공시·뉴스를 한 화면에)
# ------------------------------------------------------------
BRIEF_YEARS = 5  # 실적 표에 보여줄 연수
BRIEF_DISC_DAYS = 92  # 최근 공시: 약 3개월
BRIEF_NEWS_DAYS = 30  # 최근 단독 뉴스: 한 달
BRIEF_NEWS_MAX = 8  # 보여줄 뉴스 묶음 수
BRIEF_TREND_DAYS = 7  # 동향 요약에 보내는 단독·통신 기사: 최근 1주일
BRIEF_WIRE_MAX = 15  # 동향 요약에 보내는 통신 기사 묶음 수
BRIEF_DISC_MAX = 12  # 보여줄 주요 공시 수

AI_BRIEF_SYSTEM = (
    "너는 중앙일보 산업부 기자의 취재 준비를 돕는 보조자다. 사용자가 준 자료(실적·재무, 최근 주요 공시 제목)만 근거로 쓴다. "
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
    return (
        "\n\n".join(parts)
        + "\n\n위 자료만 근거로, 이 기업을 취재하는 산업부 기자를 위해 아래 형식으로 써라. 자료가 부족한 항목은 건너뛴다.\n"
        "**한 줄 요약**: 지금 이 기업을 어떻게 봐야 하는지 한 문장\n"
        "**취재 포인트 3가지**: 각 항목은 '- 포인트: 한 문장 (근거: 자료의 숫자·공시명)' 형식. 근거가 어느 자료(실적·공시)인지 밝혀라\n"
        "**물어볼 것**: 자료만으로 알 수 없어 회사나 취재원에게 확인해야 할 질문 2~3개 (- 로 시작)"
    )


BRIEF_TREND_NEWS_MAX = 12  # 동향 요약에 보내는 일반 기사 묶음 수
BRIEF_CLS = {"Y": "유가증권시장", "K": "코스닥", "N": "코넥스", "E": "기타"}
# 회사 개요 요약에 보내는 보고서 부분과 부분별 최대 글자 수
BRIEF_BIZ_BLOCKS = [("biz_overview", 6000), ("biz_products", 12000), ("biz_sales", 12000), ("note_segment", 8000), ("biz_fin_ops", 8000)]

AI_TREND_SYSTEM = (
    "너는 중앙일보 산업부 기자의 취재 준비를 돕는 보조자다. 사용자가 준 공시 제목과 기사 제목만 근거로 쓴다. "
    "제목에 적힌 내용 밖의 숫자·원인·배경·전망·시장 반응을 절대 만들지 않는다. 기사 본문과 공시 내용은 보지 못했으니 제목에서 읽히는 것까지만 말하되 \"확인이 필요하다\" 같은 단서 문구는 쓰지 않는다. "
    "같은 사안의 공시·기사는 하나의 이슈로 묶는다. 한국어, 짧고 명확한 기사체로 쓴다."
)


def brief_profile(code):
    """기업 개황(company.json) 중 브리핑에 쓰는 항목 (하루 저장). 못 받으면 None."""
    session, store = get_session(), get_cache_store()
    key = ("company_profile", code)
    cached = cache_lookup(store, key, 24 * 3600)
    if cached is not _MISSING and cached:
        return cached
    jo = call_dart(session, DART_COMPANY_URL, {"crtfc_key": API_KEY, "corp_code": code})
    prof = {k: str((jo or {}).get(k) or "").strip() for k in ("stock_name", "ceo_nm", "est_dt", "acc_mt", "corp_cls", "hm_url", "adres", "stock_code")}
    if not any(prof.values()):
        return None
    cache_save(store, key, prof)
    return prof


def brief_holder(code, end_year):
    """최대주주 현황 (사업보고서 기준, 가장 최근 연도). -> dict(year, name, relate, rate, total_rate) 또는 None"""
    for y in (end_year, end_year - 1):
        got = rt_collect([("h", code, y, "hyslrSttus", "11011")])
        rows = [r for r in (((got.get(("h", y, "hyslrSttus"))) or {}).get("list") or []) if isinstance(r, dict)]
        if not rows:
            continue
        common = [r for r in rows if "보통" in str(r.get("stock_knd", ""))] or rows
        main = next((r for r in common if "최대주주" in str(r.get("relate", ""))), None) or common[0]
        total = next((r for r in common if str(r.get("nm", "")).strip() == "계"), None)
        return {"year": y, "name": str(main.get("nm", "")).strip(), "relate": str(main.get("relate", "")).strip(),
                "rate": str(main.get("trmend_posesn_stock_qota_rt", "")).strip(),
                "total_rate": str(total.get("trmend_posesn_stock_qota_rt", "")).strip() if total else ""}
    return None


def brief_report_rows(code, limit=5):
    """사업 설명을 읽을 보고서 후보 (먼저 읽어 볼 순서): 정정 아닌 사업보고서 최신순 -> 정정 사업보고서 -> 반기·분기보고서.
    앞의 것이 원문을 못 받으면 다음 것으로 넘어가려고 여러 개를 돌려준다."""
    rows = [r for r in periodic_reports(code) if report_period_of(r.get("report_nm"))]  # 이름에 (2025.12) 같은 기간이 있는 것만

    def rank(r):
        name = str(r.get("report_nm") or "")
        annual = strip_correction(name).startswith("사업보고서")
        return (0 if annual and strip_correction(name) == name.strip() else 1 if annual else 2)

    rows = sorted(rows, key=lambda r: (rank(r), "".join(chr(255 - ord(c)) for c in str(r.get("rcept_dt", "")))))  # 같은 순위 안에서는 접수일 최신순
    return rows[:limit]


def brief_overview_excerpt(blocks):
    """보고서 블록에서 회사 개요 요약에 쓸 부분만 이어 붙인다. -> (글, 읽은 부분 이름들)"""
    parts, read = [], []
    for key, cap in BRIEF_BIZ_BLOCKS:
        value = (blocks.get(key) or "").strip()
        if value:
            parts.append(f"[{REPORT_BLOCK_LABELS[key]}]\n{value[:cap]}")
            read.append(REPORT_BLOCK_LABELS[key])
    return "\n\n".join(parts), read


def brief_overview_prompt(corp, row, excerpt):
    return (
        f"기업: {corp}\n보고서: {strip_correction(row.get('report_nm'))}\n\n[발췌]\n{excerpt}\n\n"
        "위 발췌만 근거로, 이 기업을 처음 취재하는 산업부 기자가 읽을 '회사 개요'를 아래 형식으로 써라. 발췌에 없는 항목은 건너뛴다.\n"
        "**회사 소개**: 이 회사가 무엇으로 돈을 버는 곳인지 3~4문장 (주력 사업·제품·서비스, 사업 구조. 회사의 자기 설명은 '회사는 ~라고 설명'으로)\n"
        "**주력 제품·서비스**: 3~5개 항목(- 로 시작). 각 항목은 '제품·서비스 이름: 한 줄 설명'\n"
        "**매출 구성**: 사업 부문별 또는 제품별 매출과 비중을 마크다운 표로 (열: 구분 | 매출액 | 비중). "
        "금액·비중은 발췌 표기 그대로(단위 포함) 옮기고, 비중이 발췌에 없으면 '-'로 둔다. 새로 계산하지 않는다. "
        "표 아래에 기준 기간과 근거 위치를 [ ] 안에 적는다 (예: [II-4. 매출 및 수주상황]). 매출 구성을 찾지 못하면 '보고서 발췌에서 확인되지 않음'이라고 쓴다."
    )


def brief_trend_sources(b):
    """동향 요약에 보내는 공시·기사 -> [(구역, 줄 글자, 제목, 링크)]. 번호는 이 목록의 순서(1부터)."""
    out = []
    if b.get("disc") is not None and not b["disc"].empty:
        for r in b["disc"].to_dict("records"):
            out.append(("[최근 3개월 주요 공시 제목]", f"{r.get('접수일', '')} | {r.get('보고서명', '')}", str(r.get("보고서명", "")), str(r.get("원문") or "")))
    cutoff = b["asof"] - timedelta(days=BRIEF_TREND_DAYS)
    seen = {url for *_, url in out if url}  # 같은 기사가 단독·통신·일반 목록에 겹쳐 나오면 한 번만
    for head, key in (("[최근 1주일 단독 기사 제목]", "news"), ("[최근 1주일 통신 기사 제목 (연합뉴스·뉴시스·뉴스1)]", "wire"),
                      ("[최근 일반 기사 제목 (최신순)]", "news_all")):
        for c in b.get(key) or []:
            rep = c["rep"]
            if key == "news" and rep["dt"] < cutoff:  # 단독은 화면에는 한 달 치를 보여주지만 동향 요약에는 1주일 치만
                continue
            url = rep.get("url", "")
            if url and url in seen:
                continue
            seen.add(url)
            out.append((head, f"{rep['dt']:%m/%d} {rep.get('publisher', '')} · {rep['title']}", rep["title"], url))
    return out


def brief_trend_prompt(b):
    parts, last, n = [], None, 0
    for head, line, _, _ in brief_trend_sources(b):
        n += 1
        if head != last:
            parts.append(("\n\n" if parts else "") + head)
            last = head
        parts.append(f"\n[{n}] {line}")
    return (
        f"기업: {b['corp']}\n\n" + "".join(parts)
        + "\n\n위 제목들만 근거로 아래 형식으로 써라.\n"
        "**이번 분기 이슈 3가지**: 각 항목은 '- 이슈 이름: 한두 문장 (근거: [번호], [번호])' 형식. 근거는 위 제목 앞의 번호만 대괄호로 적고 제목이나 주소는 쓰지 않는다. "
        "공시와 기사가 같은 사안이면 한 이슈로 묶고, 중요도 순으로 쓴다.\n"
        "'제목만으로는 알 수 없다', '확인이 필요하다' 같은 단서·면책 문구는 쓰지 않는다. 제목에서 읽히는 사실만 담백하게 쓴다."
    )


def brief_trend_linkify(text, sources):
    """AI 답의 [3], [3, 7] 같은 번호를 제목 링크로 바꾼다 (번호가 목록에 없으면 지움)."""
    def label(t):
        t = re.sub(r"[\[\]]", "", t).strip()
        return t if len(t) <= 28 else t[:27] + "…"

    def one(m):
        links = []
        for num in re.findall(r"\d+", m.group(1)):
            i = int(num) - 1
            if 0 <= i < len(sources):
                _, _, title, url = sources[i]
                links.append(f"[{label(title)}]({url.replace(')', '%29')})" if url else label(title))
        return ", ".join(links)

    return re.sub(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]", one, text)


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

    b["profile"] = step("기업 개황을 불러오는 중...", "profile", lambda: brief_profile(code))
    b["holder"] = step("최대주주를 확인하는 중...", "holder", lambda: brief_holder(code, end))
    b["biz_rows"] = step("사업보고서를 찾는 중...", "biz_row", lambda: brief_report_rows(code))
    if KRX_KEY:
        stock = stock_code_of(code)
        b["quote"] = step("시세를 불러오는 중...", "quote", lambda: collect_quote(stock) if stock else None)
    else:
        b["quote"] = False  # 키 없음
    b["records"] = step("실적을 불러오는 중...", "fin", lambda: collect_yearly(code, end - BRIEF_YEARS + 1, end, lambda: None))

    def disclosures():
        rows, total, _ = collect_disclosures(code, TODAY - timedelta(days=BRIEF_DISC_DAYS), TODAY, "")
        b["disc_total"] = total
        major = match_preset(rows, "주요 전체")
        major.sort(key=lambda r: str(r.get("rcept_no", "")), reverse=True)
        return disclosure_table(major[:BRIEF_DISC_MAX]) if major else pd.DataFrame()

    b["disc"] = step("최근 공시를 불러오는 중...", "disc", disclosures)

    # 뉴스는 모두 구글 뉴스로 찾는다 (네이버 키가 없어도 됨)
    def google(terms, days, domains, keyword=""):
        items, err = google_news(terms, days, domains, keyword)
        items = drop_blocked(items)
        if err:
            raise NewsApiError(err)
        return items

    def news():
        names = news_names(name)
        items = google(names, BRIEF_NEWS_DAYS, list(PUBLISHERS), "단독")
        keys = [name_key(t) for t in names]
        items = [i for i in items if i["category"] and is_exclusive(i["title"]) and any(key_in_title(k, name_key(i["title"]), i["title"]) for k in keys)]
        return cluster_news(items)[:BRIEF_NEWS_MAX]

    b["news"] = step("최근 단독 뉴스를 찾는 중...", "news", news)

    def wire():  # 연합뉴스·뉴시스·뉴스1 기사 (브리핑 화면에는 보이지 않고 동향 요약에만 쓴다)
        names = news_names(name)
        items = google(names, BRIEF_TREND_DAYS, list(WIRE_DOMAINS))
        keys = [name_key(t) for t in names]
        sports_keys = load_sports_keys()
        items = [i for i in items if i["category"] == "통신사" and not is_sports(i["title"], sports_keys) and any(key_in_title(k, name_key(i["title"]), i["title"]) for k in keys)]
        return cluster_news(items)[:BRIEF_WIRE_MAX]

    b["wire"] = step("최근 통신 기사를 찾는 중...", "wire", wire)

    def news_all():  # 단독 표기와 상관없는 일반 기사 (동향 요약용)
        items = google([name], BRIEF_NEWS_DAYS, list(PUBLISHERS), "")
        key = name_key(name)
        items = [i for i in items if i["category"] and key in name_key(i["title"]) and not is_exclusive(i["title"])]  # 단독은 위 목록에 있음
        return cluster_news(items)[:BRIEF_TREND_NEWS_MAX]

    b["news_all"] = step("최근 기사를 찾는 중...", "news_all", news_all)
    return b


def render_brief_overview(b):
    """브리핑 맨 위: 기업 개황(대표이사·설립일·결산월·최대주주) + 사업 개요·매출 구성 AI 요약."""
    errs = b["errors"]
    st.markdown("##### 회사 개요")
    p = b.get("profile")
    if errs.get("profile"):
        st.warning(errs["profile"])
    elif p:
        bits = []
        if p.get("ceo_nm"):
            bits.append(f"**대표이사** {p['ceo_nm']}")
        d = p.get("est_dt", "")
        if len(d) == 8 and d.isdigit():
            bits.append(f"**설립일** {d[:4]}.{d[4:6]}.{d[6:]}")
        if p.get("acc_mt", "").isdigit():
            bits.append(f"**결산월** {int(p['acc_mt'])}월")
        if BRIEF_CLS.get(p.get("corp_cls")):
            bits.append(f"**시장** {BRIEF_CLS[p['corp_cls']]}")
        h = b.get("holder")
        if h and h.get("name"):
            rate = f"{h['rate']}%" if h.get("rate") not in ("", "-") else "-"
            extra = f", 특수관계인 합계 {h['total_rate']}%" if h.get("total_rate") not in ("", "-") else ""
            bits.append(f"**최대주주** {h['name']} ({rate}{extra} · {h['year']}년 말)")
        elif errs.get("holder"):
            bits.append("**최대주주** 불러오지 못함")
        if bits:
            st.markdown("  ·  ".join(bits))
        if p.get("hm_url"):
            url = p["hm_url"] if p["hm_url"].startswith("http") else "http://" + p["hm_url"]
            st.caption(f"홈페이지 {url}")
    else:
        st.info("기업 개황을 찾지 못했어요.")

    rows = b.get("biz_rows") or []
    row = rows[0] if rows else None
    if not AI_PROVIDER:
        st.caption("사업 개요·매출 구성 요약을 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY(Claude) 또는 GEMINI_API_KEY(Gemini)를 추가해주세요.")
        return
    if errs.get("biz_row"):
        st.warning(errs["biz_row"])
        return
    if not row:
        st.caption("최근 4년 안에 정기보고서(사업·반기·분기)가 없어 사업 개요를 만들 수 없어요.")
        return
    rno = str(row["rcept_no"]).strip()
    if st.button("사업 개요·매출 구성 보기 (AI)", key="brief_ov_go"):
        with st.spinner("사업보고서를 읽는 중입니다... (처음엔 30초 안팎)"):
            last_err, done = None, None
            for cand in rows:  # 앞의 보고서 원문을 못 받으면(정정본 등) 다음 후보로
                crno = str(cand["rcept_no"]).strip()
                try:  # 1) 보고서 원문 받기: 실패하면 다음 후보로
                    blocks = periodic_blocks(crno)
                    excerpt, read = brief_overview_excerpt(blocks)
                    if not excerpt:
                        raise AiError("보고서에서 사업의 내용 부분을 찾지 못했어요. 원문 링크로 확인해주세요.")
                except (AiError, DartApiError) as e:
                    last_err = e
                    continue
                try:  # 2) AI 요약: 실패 원인(키·한도)은 보고서를 바꿔도 같으니 멈춘다
                    (text, used) = ai_cached(("briefov", crno), lambda: ai_generate(brief_overview_prompt(b["corp"], cand, excerpt), system=AI_PERIODIC_SYSTEM))
                except AiError as e:
                    last_err = e
                    break
                done = {"rno": rno, "used_rno": crno, "name": strip_correction(cand.get("report_nm")), "text": text, "model": used, "read": read}
                break
            if done:
                st.session_state["brief_ov"] = done
            else:
                st.error(str(last_err))
    saved = st.session_state.get("brief_ov")
    if saved and saved["rno"] == rno:
        st.markdown(saved["text"])
        st.caption(f"AI 요약({saved['model']}) · [{saved['name']}]({DART_VIEW_URL.format(saved['used_rno'])}) 발췌만 근거 · 읽은 부분: {', '.join(saved['read'])}")
    else:
        st.caption(f"누르면 {strip_correction(row.get('report_nm'))}의 '사업의 내용'을 읽어 회사 소개·주력 제품·부문별 매출 비중을 정리해 줘요.")


def render_brief_trend(b):
    """최근 3개월 공시 + 최근 기사 제목으로 '이번 분기 이슈'를 AI가 정리."""
    st.markdown("##### 최근 동향 요약 (AI)")
    if not AI_PROVIDER:
        st.caption("동향 요약을 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY(Claude) 또는 GEMINI_API_KEY(Gemini)를 추가해주세요.")
        return
    has_disc = b.get("disc") is not None and not b["disc"].empty
    if not (has_disc or b.get("news") or b.get("wire") or b.get("news_all")):
        st.caption("요약할 최근 공시·기사가 없어요.")
        return
    prompt = brief_trend_prompt(b)
    sig = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    if st.button("공시·기사 제목으로 이슈 정리", key="brief_trend_go"):
        with st.spinner("최근 공시와 기사를 종합하는 중입니다... (10~30초)"):
            try:
                (text, used) = ai_cached(("brieftrend", sig), lambda: ai_generate(prompt, system=AI_TREND_SYSTEM))
            except AiError as e:
                st.error(str(e))
            else:
                st.session_state["brief_trend"] = {"sig": sig, "text": text, "model": used}
    saved = st.session_state.get("brief_trend")
    n_news = len(b.get("news") or []) + len(b.get("wire") or []) + len(b.get("news_all") or [])
    for key, label in (("wire", "통신 기사"), ("news_all", "일반 기사")):
        if b["errors"].get(key):
            st.caption(f"{label}를 불러오지 못해 빼고 정리해요: {b['errors'][key]}")
    if saved and saved["sig"] == sig:
        st.markdown(brief_trend_linkify(saved["text"], brief_trend_sources(b)))
        st.caption(f"AI 분석({saved['model']}) · 공시 제목과 기사 제목만 보고 정리한 것이에요. 본문은 읽지 않았으니 원문 확인이 필요해요")
    else:
        st.caption(f"누르면 최근 주요 공시 {0 if not has_disc else len(b['disc'])}건과 기사 {n_news}묶음의 제목을 종합해 이슈를 정리해 줘요. (기사는 최근 1주일 단독·통신 기사와 일반 기사 중 주요 언론사 것만 봐요. 통신 기사는 브리핑 화면에는 보이지 않아요)")


def brief_snap(data, intra):
    """브리핑용 종목 표: 장중 자료가 새 거래일이면 장중 최신, 아니면 마지막 종가. -> (표, '15:00 현재' 또는 '10.22 종가')"""
    if sc.intraday_ready(intra, data):
        snap, last_end = sc.intraday_snapshot(intra, data)
        return snap, f"{last_end} 현재" if last_end else "장중"
    return sc.snapshot(data, data["dates"][-1]), f"{data['dates'][-1][4:6]}.{data['dates'][-1][6:]} 종가"


def brief_peer_rows(data, snap, code):
    """같은 섹터 기업 비교표 -> (섹터 이름, [{code, name, cap, rate, m1, ytd}] 시총 큰 순) 또는 (None, [])
    m1: 최근 1개월 수익률, ytd: 연초 이후 수익률 (KRX 등락률을 이어 붙인 값)"""
    for sector, codes in px_sector_codes(data).items():
        if code not in codes:
            continue
        last, first = sc.dates_of(data).max(), sc.dates_of(data).min()
        rows = []
        for c in codes:
            r = snap[snap["code"] == c]
            if r.empty:
                continue
            r = r.iloc[0]
            full = sc.frame(data, c)
            rows.append({"code": c, "name": r["name"], "cap": None if pd.isna(r["cap"]) else float(r["cap"]),
                         "rate": None if pd.isna(r["rate"]) else float(r["rate"]),
                         "m1": sc.period_return(sc.cut(full, sc.period_start("1개월", first, last))),
                         "ytd": sc.period_return(sc.cut(full, sc.period_start("연초 이후", first, last)))})
        rows.sort(key=lambda x: -(x["cap"] or 0))
        return (sector, rows) if len(rows) >= 2 else (None, [])
    return None, []


BRIEF_TL_DAYS = 90
BRIEF_TL_MOVE = 5.0  # 타임라인에 올릴 주가 급등락 기준(±%)
BRIEF_TL_MAX = 40
BRIEF_TL_MOVES_MAX = 8  # 타임라인에 올리는 주가 급등락일 수 (큰 순)


def brief_timeline(b, archive, data, code, now=None):
    """최근 3개월 이슈 타임라인 -> [{dt, kind, title, url, r0, r3}] 최신순.
    모아 둔 기사·공시(archive) + 브리핑의 주요 공시·단독 기사 + 주가 급등락일(±BRIEF_TL_MOVE%)을 하루 단위로 합친다.
    r0·r3: 그날 등락률과 3거래일 누적 (시세 자료가 있을 때만)."""
    now = (now or datetime.now(KST)).replace(tzinfo=None)
    start = now - timedelta(days=BRIEF_TL_DAYS)
    name = ((data or {}).get("s", {}).get(code) or {}).get("n") or b["corp"]
    items, seen = [], set()

    def add(dt, kind, title, url):
        sig = (dt.date() if hasattr(dt, "date") else dt, title_key(title))
        if sig in seen:
            return
        seen.add(sig)
        items.append({"dt": dt, "kind": kind, "title": title, "url": url or "", "r0": None, "r3": None})

    d = b.get("disc")
    if d is not None and not d.empty:
        for r in d.to_dict("records"):
            try:
                add(datetime.strptime(str(r["접수일"]), "%Y-%m-%d"), "공시", str(r["보고서명"]), str(r.get("원문") or ""))
            except ValueError:
                pass
    for c in b.get("news") or []:
        rep = c["rep"]
        if rep["dt"].replace(tzinfo=None) >= start:
            add(rep["dt"].replace(tzinfo=None), "단독", rep["title"], rep.get("url"))
    if archive:
        for e in px_events(archive, name, start, now):
            add(e["dt"], e["kind"], e["title"], e["url"])
    full = sc.frame(data, code) if data and code in (data.get("s") or {}) else None
    if full is not None:
        moves = [(day, rate) for day, rate in full["rate"].dropna().items() if day >= pd.Timestamp(start) and abs(rate) >= BRIEF_TL_MOVE]
        for day, rate in sorted(moves, key=lambda m: -abs(m[1]))[:BRIEF_TL_MOVES_MAX]:  # 급등락이 잦은 종목은 큰 것만
            close = full["close"].get(day)
            add(day.to_pydatetime(), "주가", f"주가 {rate:+.1f}%" + (f" (종가 {close:,.0f}원)" if pd.notna(close) else ""), "")
        for it in items:
            if it["kind"] != "주가":
                it["r0"], it["r3"] = sc.event_reaction(full, it["dt"])
    items.sort(key=lambda x: (x["dt"].date(), x["kind"] == "주가", x["dt"]), reverse=True)
    return items[:BRIEF_TL_MAX]


def render_brief_peers(b):
    """브리핑: 같은 섹터 기업과 시총·등락률·수익률 비교 (접이식). 상장사이고 시세 자료에 섹터가 있을 때만."""
    code = stock_code_of(b["code"])
    data = get_prices() if code else None
    if not data or code not in (data.get("s") or {}):
        return
    snap, when = brief_snap(data, get_intraday())
    sector, rows = brief_peer_rows(data, snap, code)
    if not rows:
        return
    rank = next((i for i, r in enumerate(rows, 1) if r["code"] == code), None)
    with st.expander(f"업계 비교 ({sector} 시총 {rank}위 / {len(rows)}곳)"):
        table = pd.DataFrame([{"순위": i, "기업": ("★ " if r["code"] == code else "") + r["name"], "시총": px_cap_text(r["cap"]),
                               f"등락률 ({when})": px_pct(r["rate"], 2), "1개월": px_pct(r["m1"]), "연초 이후": px_pct(r["ytd"])}
                              for i, r in enumerate(rows, 1)])
        st.dataframe(table.style.map(price_style, subset=[f"등락률 ({when})", "1개월", "연초 이후"]), hide_index=True)
        st.caption("★ 이 기업 · 섹터 기업 목록은 앱의 섹터 구성이에요(비상장사 제외). 수익률은 KRX 등락률을 이어 붙인 값(배당 제외), 시총은 보통주+우선주예요.")


def render_brief_timeline(b):
    """브리핑: 최근 3개월 공시·기사·주가 급등락을 날짜순 한 표로 (접이식)."""
    code = stock_code_of(b["code"])
    data = get_prices() if code else None
    archive = get_stored("archive.json")
    items = brief_timeline(b, archive, data, code)
    if not items:
        return
    with st.expander(f"이슈 타임라인 (최근 3개월 {len(items)}건)"):
        table = pd.DataFrame([{"날짜": it["dt"].strftime("%m.%d"), "종류": it["kind"], "내용": it["title"], "당일 등락": px_pct(it["r0"]), "3거래일 누적": px_pct(it["r3"]),
                               "원문": it["url"] or None} for it in items])
        show = [c for c in ("날짜", "종류", "내용", "당일 등락", "3거래일 누적", "원문") if c in table and not (c in ("당일 등락", "3거래일 누적") and (table[c] == "-").all())]
        st.dataframe(table[show].style.map(price_style, subset=[c for c in ("당일 등락", "3거래일 누적") if c in show]), hide_index=True,
                     height=min(len(table) + 1, 15) * 35 + 3, column_config={"내용": st.column_config.TextColumn("내용", width="large"), "원문": st.column_config.LinkColumn("원문", display_text="열기")})
        st.caption(f"공시(주요 공시)·단독 기사·모아 둔 기사에 주가 ±{BRIEF_TL_MOVE:g}% 이상 움직인 날을 합친 것이에요. 공시는 접수 날짜만 알 수 있어서 등락은 그날(휴장이면 다음 거래일) 기준이고, "
                   "원인을 단정할 수 없어요. 모아 둔 기사·공시는 섹터 기업의 통신·단독 기사와 주요 공시만 있어요.")


def brief_glance_price(data, intra, code):
    """한눈에 카드의 시세: 시세 자료(prices.json)에 있는 종목이면 장중 최신(없으면 마지막 종가) 등락률·시총과 소속 섹터 평균 등락률.
    -> {"rate", "cap"(억원), "when", "sector", "sector_avg", "n"} 또는 None"""
    if not data or code not in (data.get("s") or {}):
        return None
    snap, when = brief_snap(data, intra)
    row = snap[snap["code"] == code]
    if row.empty:
        return None
    r = row.iloc[0]
    out = {"close": float(r["close"]), "rate": None if pd.isna(r["rate"]) else float(r["rate"]), "cap": None if pd.isna(r["cap"]) else float(r["cap"]),
           "when": when, "sector": None, "sector_avg": None, "n": 0}
    for sector, codes in px_sector_codes(data).items():
        if code in codes:
            sub = snap[snap["code"].isin(codes)].dropna(subset=["rate"])
            if len(sub) >= 2:
                out.update(sector=sector, sector_avg=float(sub["rate"].mean()), n=int(len(sub)))
            break
    return out


def brief_glance_cards(b, pr):
    """한눈에 카드 목록 [(이름, 값, 증감, 색)]. 시세가 없는 비상장사는 실적 카드만."""
    cards = []
    q = b.get("quote") if isinstance(b.get("quote"), dict) else None
    if pr and pr["rate"] is not None:  # 변동 글자 색: 오르면(+) 파랑, 내리면(-) 빨강
        cards.append((f"{'오늘 주가' if pr['when'].endswith('현재') else '주가'} ({pr['when']})", f"{pr['close']:,.0f}원", pct_text(pr["rate"], 2), None))
    elif q and q["row"].rate is not None:
        r = q["row"]
        cards.append((f"주가 ({q['day']:%m.%d} 종가)", f"{r.close:,.0f}원", pct_text(r.rate, 2), None))
    cap = pr["cap"] * 1e8 if pr and pr["cap"] else (q["caps"] if q else None)
    if cap:
        label = f"시가총액 ({pr['when']}{' 추정' if pr['when'].endswith('현재') else ''})" if pr and pr["cap"] else "시가총액"
        cards.insert(0, (label, won_text(cap), None, "flat"))
    recs = sorted_records(b.get("records") or [])
    if recs:
        r = recs[-1]
        ryoy = fmt_change(r["rev_yoy"]) if r.get("rev_yoy") is not None else None
        cards.append((f"{r['label']} 매출액", won_text(r["rev"]) if r.get("rev") is not None else "-", f"전년比 {ryoy}" if ryoy else None, "flat"))
        yoy = fmt_change(r["op_yoy"]) if r.get("op_yoy") is not None else None
        cards.append((f"{r['label']} 영업이익", won_text(r["op"]) if r.get("op") is not None else "-", f"전년比 {yoy}" if yoy else None, "flat"))
    return cards


def render_brief_glance(b, hero=""):
    """브리핑 맨 위 '한눈에': 시총·오늘 등락·섹터 대비·최근 영업이익, 가장 최근 공시·기사 한 건씩."""
    pr = None
    if stock_code_of(b["code"]):
        try:
            pr = brief_glance_price(get_prices(), get_intraday(), stock_code_of(b["code"]))
        except Exception:
            pr = None
    cards = brief_glance_cards(b, pr)
    if cards:  # 제목 카드와 지표 카드를 한 묶음으로(컴퓨터 화면은 제목 왼쪽·지표 오른쪽 2x2, 모바일은 위아래)
        st.markdown(f'<div class="cb-top">{hero}{cb_cards(cards)}</div>', unsafe_allow_html=True)
    elif hero:
        st.markdown(hero, unsafe_allow_html=True)
    latest = []
    d = b.get("disc")
    if d is not None and not d.empty:
        r = d.iloc[0]
        latest.append(("가장 최근 주요 공시", str(r["접수일"])[5:].replace("-", "."), r["보고서명"], r.get("원문")))
    news = [c["rep"] for key in ("news", "wire", "news_all") for c in (b.get(key) or [])]
    if news:
        n = max(news, key=lambda i: i["dt"])
        latest.append(("가장 최근 기사", f"{n['dt']:%m.%d}", n["title"], n.get("url")))
    if latest:
        items = []
        for k, day, title, url in latest:
            t = html.escape(str(title))
            body = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{t}</a>' if url and str(url).startswith("http") else f'<span class="x">{t}</span>'
            items.append(f'<div class="it"><span class="k">{k}</span><span class="m">{day}</span>{body}</div>')
        st.markdown(f'<div class="cb-latest">{"".join(items)}</div>', unsafe_allow_html=True)


def render_brief_result(b):
    hero = f'<div class="cb-hero"><div class="t">{html.escape(b["corp"])} 브리핑</div><div class="s">{b["asof"]:%Y.%m.%d %H:%M} 기준 · 연결 기준</div></div>'
    errs = b["errors"]

    render_brief_glance(b, hero)
    with st.container(border=True):
        render_brief_overview(b)

    with st.container(border=True):
        if b.get("quote") is False:
            krx_hint()
        elif errs.get("quote"):
            krx_error_box(errs["quote"])
        else:
            render_quote(b.get("quote"))
    render_brief_peers(b)

    with st.container(border=True):
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
            if not bs.empty and st.toggle("재무상태 보기 (부채비율·현금성자산·재고자산·CAPEX·순차입금·잉여현금흐름)", key="brief_bs"):
                st.dataframe(bs, hide_index=True, height=(len(bs) + 1) * 35 + 3,
                             column_config={"기간": st.column_config.TextColumn("기간", width=95)})

    with st.container(border=True):
        st.markdown("##### 최근 3개월 주요 공시")
        if errs.get("disc"):
            st.warning(errs["disc"])
        elif b["disc"] is None or b["disc"].empty:
            st.info("최근 3개월 동안 실적·공급계약·증자·자기주식·지분 변동 등 주요 공시가 없어요.")
        else:
            st.caption(f"최근 3개월 공시 {b.get('disc_total', 0):,}건 중 주요 공시 · 최신순")
            st.dataframe(b["disc"], hide_index=True, column_config={"원문": LINK_COLUMN, "보고서명": st.column_config.TextColumn("보고서명", width="large")},
                         height=min(len(b["disc"]) + 1, 14) * 35 + 3)

    with st.container(border=True):
        st.markdown("##### 최근 한 달 단독 뉴스")
        if errs.get("news"):
            st.warning(errs["news"])
        elif not b["news"]:
            st.info("최근 한 달 동안 제목에 [단독]이 붙은 기사가 없어요.")
        else:
            st.markdown(news_html(b["news"]), unsafe_allow_html=True)

    render_brief_timeline(b)
    render_brief_trend(b)

    with st.container(border=True):
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
            st.caption(f"AI 분석({saved['model']}) · 위 실적·공시 자료만 근거로 한 해석이에요. 공시는 제목만 보고 판단한 것이라 원문 확인이 필요해요")
        else:
            st.caption("누르면 위 실적·공시 자료를 종합해 취재 포인트와 확인할 질문을 정리해 줘요. (뉴스는 포함하지 않아요)")

def render_brief():
    """탭: 기업 브리핑 - 회사 하나의 실적·공시·뉴스를 한 화면에."""
    with st.form("brief_search"):
        corp_text = st.text_input("기업명 또는 종목코드", key="brief_corp", placeholder="예: 삼성전자 또는 005930")
        submitted = st.form_submit_button("브리핑 만들기", type="primary") or st.session_state.pop("brief_run", False)

    if submitted:
        corp = canonical_corp(corp_text.strip())
        if not corp:
            st.error("기업명을 입력해주세요.")
        else:
            st.session_state.pop("brief", None)
            st.session_state.pop("brief_ai", None)
            st.session_state.pop("brief_ov", None)
            st.session_state.pop("brief_trend", None)
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
                    bar.progress(min(steps[0] / 8, 1.0), text=label)

                try:
                    st.session_state["brief"] = collect_brief(corp, code, progress)
                finally:
                    bar.empty()

    b = st.session_state.get("brief")
    if b:
        with st.container(key="brief_wrap"):
            render_brief_result(b)



# ------------------------------------------------------------
# 실적 캘린더 (국내 기업)
# ------------------------------------------------------------
DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"  # 기업 개황 (결산월)
CAL_RANGES = {"앞으로 2주": (0, 14), "앞으로 1개월": (0, 31), "지난 2주": (-14, 0)}
MAX_CAL_CORPS = 100
WEEKDAYS = "월화수목금토일"
CAL_KINDS_HELP = (
    "**IR 개최**: 기업이 공시한 IR(기업설명회) 개최 예정 일정. 원문에서 날짜를 읽어 와요. 이미 발표한 잠정실적 공시는 넣지 않아요\n\n"
    "**전년 기준 예상**: 작년 같은 시기 잠정실적 공시일을 올해로 옮긴 추정이에요. 확정이 아니에요\n\n"
    "**법정 제출기한**: 분기·반기보고서는 결산 후 45일, 사업보고서는 90일이에요. 잠정실적은 보통 이보다 먼저 나와요"
)
IR_DATE_RE = re.compile(r"(20\d\d)\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def cal_day(d):
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def cal_fiscal_month(session, store, corp_code):
    """결산월 (못 받으면 12월로 본다)"""
    key = ("company", corp_code)
    cached = cache_lookup(store, key, 24 * 3600)
    if cached is not _MISSING and cached:
        return cached
    try:
        jo = call_dart(session, DART_COMPANY_URL, {"crtfc_key": API_KEY, "corp_code": corp_code})
    except DartApiError:
        return 12
    try:
        month = int((jo or {}).get("acc_mt"))
    except (TypeError, ValueError):
        month = 12
    month = month if 1 <= month <= 12 else 12
    cache_save(store, key, month)
    return month


def cal_deadlines(fy_month, lo, hi):
    """lo~hi 사이에 있는 정기보고서 제출기한 -> [(기한, 보고서 이름)]. 분기·반기는 결산 후 45일, 사업보고서는 90일."""
    out = []
    for fy in range(lo.year - 1, hi.year + 2):  # fy = 회계연도가 끝나는 해
        for i, label in enumerate(("1분기보고서", "반기보고서", "3분기보고서", "사업보고서")):
            m = fy_month - 9 + 3 * i
            yy, mm = fy + (m - 1) // 12, (m - 1) % 12 + 1
            end = date(yy + (mm == 12), mm % 12 + 1, 1) - timedelta(days=1)  # 그 달 말일
            deadline = end + timedelta(days=90 if i == 3 else 45)
            if lo <= deadline <= hi:
                out.append((deadline, label))
    return out


def cal_list_rows(session, store, code, start, end):
    """기간 내 거래소공시(공정공시 포함) 목록. DART 오류는 DartApiError."""
    rows, bgn, fin = [], start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    for page in range(1, 6):
        jo = fetch_disclosure_page(session, store, code, bgn, fin, page, "I")
        if not jo:
            break
        rows.extend(jo.get("list", []))
        if page >= int(jo.get("total_page", 1)):
            break
    return rows


IR_PURPOSE_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:\d+\s*\.\s*)?(?:개최|실시)?[ \t]*목적[ \t]*[|:：]*\s*([^|\n]{2,80})")


def cal_ir_info(session, store, rcept_no):
    """IR 개최 공시 원문에서 (개최일, 개최 목적)을 읽는다. 못 읽은 것은 None."""
    try:
        text = fetch_document_text(session, store, rcept_no)
    except Exception:
        return None, None
    held = None
    pos = text.find("일시")
    for chunk in ([text[pos:pos + 200]] if pos >= 0 else []) + [text[:1500]]:
        m = IR_DATE_RE.search(chunk)
        if m:
            try:
                held = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                break
            except ValueError:
                continue
    m = IR_PURPOSE_RE.search(text)
    purpose = re.sub(r"\s+", " ", m.group(1)).strip(" ()|") if m else None
    return held, (purpose or None)


def cal_move_weekday(d):
    return d + timedelta(days=(7 - d.weekday()) if d.weekday() >= 5 else 0)  # 주말이면 다음 월요일


def cal_company(session, store, name, code, ws, we, today):
    """회사 하나의 일정 -> {"events": [...], "deadlines": [...], "unread": [...]}"""
    fy = cal_fiscal_month(session, store, code)
    events, unread = [], []
    recent = cal_list_rows(session, store, code, min(ws, today) - timedelta(days=30), today)
    actual, irs = [], []
    for r in recent:
        key = disclosure_key(r.get("report_nm", ""))
        if "정정" in key:
            continue
        try:
            d = datetime.strptime(str(r.get("rcept_dt", "")), "%Y%m%d").date()
        except ValueError:
            continue
        if "잠정" in key and "실적" in key:
            actual.append(d)
        elif "기업설명회" in key:
            irs.append((d, r))
    ir_days = []
    for filed, r in sorted(irs, key=lambda x: x[0], reverse=True)[:3]:
        held, purpose = cal_ir_info(session, store, r["rcept_no"])
        if held is None:
            unread.append((name, filed, r.get("rcept_no")))
        elif ws <= held <= we:
            ir_days.append(held)
            events.append({"date": held, "corp": name, "kind": "IR 개최", "purpose": purpose or "", "text": f"기업설명회(IR) 개최 · {filed.month}/{filed.day} 공시", "no": r.get("rcept_no")})
    if we >= today:  # 앞으로의 날짜: 작년 같은 시기 발표일을 올해로 옮겨 예상
        for r in cal_list_rows(session, store, code, ws - timedelta(days=372), we - timedelta(days=357)):
            key = disclosure_key(r.get("report_nm", ""))
            if "정정" in key or not ("잠정" in key and "실적" in key):
                continue
            try:
                last = datetime.strptime(str(r.get("rcept_dt", "")), "%Y%m%d").date()
            except ValueError:
                continue
            est = cal_move_weekday(last + timedelta(days=364))
            if not (max(ws, today) <= est <= we):
                continue
            if any(abs((est - a).days) <= 45 for a in actual) or any(abs((est - h).days) <= 5 for h in ir_days):
                continue  # 올해 이미 발표했거나 IR 일정이 잡힌 회차
            if any(e["corp"] == name and e["kind"] == "전년 기준 예상" and e["date"] == est for e in events):
                continue
            events.append({"date": est, "corp": name, "kind": "전년 기준 예상", "text": f"전년 {last.month}/{last.day} 발표", "no": None})
    return {"events": events, "deadlines": cal_deadlines(fy, ws, we), "unread": unread}


def collect_calendar(corps, ws, we):
    """corps: [(회사명, 고유번호)] -> {"events": [...], "deadlines": {(기한, 보고서): [회사]}, "unread": [...]}"""
    session, store = get_session(), get_cache_store()
    today = TODAY
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda c: cal_company(session, store, c[0], c[1], ws, we, today), corps))
    out = {"events": [], "deadlines": {}, "unread": []}
    for (name, _), res in zip(corps, results):
        out["events"].extend(res["events"])
        out["unread"].extend(res["unread"])
        for key in res["deadlines"]:
            out["deadlines"].setdefault(key, []).append(name)
    kind_order = {"IR 개최": 1, "전년 기준 예상": 2}
    out["events"].sort(key=lambda e: (e["date"], kind_order[e["kind"]], e["corp"]))
    return out


def fill_calendar():
    """고른 섹터들의 기업을 중복 없이 입력칸에 채운다 (입력칸은 그 뒤에 자유롭게 고칠 수 있다)."""
    names = []
    for g in st.session_state.get("cal_groups") or []:
        names += [n for n in CAL_GROUPS.get(g, []) if n not in names]
    st.session_state["cal_corps"] = ", ".join(names)


def tsv_text(df, drop=()):
    """표 -> 엑셀에 붙여넣으면 칸이 나뉘는 글(탭으로 구분, 첫 줄은 제목). 칸 안의 줄바꿈·탭은 공백으로"""
    d = df.drop(columns=[c for c in drop if c in df.columns]).fillna("")
    clean = d.astype(str).replace(r"[\t\r\n]+", " ", regex=True)
    return clean.to_csv(sep="\t", index=False, lineterminator="\n")


def copy_table_box(df, key, label="엑셀에 붙여넣기용 복사", drop=()):
    """표 아래 접이식 상자: 오른쪽 위 복사 아이콘을 누르고 엑셀 셀에 붙여넣으면 표 모양 그대로 들어간다(파일을 받을 필요 없음)"""
    with st.expander(label):
        st.caption("오른쪽 위 복사 아이콘을 누르고, 엑셀·구글시트의 셀 하나를 눌러 붙여넣기(Ctrl+V) 하세요.")
        st.code(tsv_text(df, drop), language=None, wrap_lines=False, height=260)


def render_calendar():
    """탭: 실적 캘린더 - 업종별 국내 기업의 잠정실적·IR 일정과 정기보고서 제출기한."""
    st.session_state.setdefault("cal_corps", "")  # 처음엔 섹터도 기업도 비워 둔다
    team_buttons("cal", CAL_GROUPS, "cal_groups", after=fill_calendar)
    st.pills("섹터", list(CAL_GROUPS), selection_mode="multi", key="cal_groups", on_change=fill_calendar, label_visibility="collapsed")
    with st.form("cal_search"):
        corp_text = st.text_input("기업 (쉼표로 구분)", key="cal_corps", placeholder="예: 삼성전자, SK하이닉스",
                                  help=f"고른 섹터의 대표 기업이 채워져요. 기업을 직접 빼거나 더해서 최대 {MAX_CAL_CORPS}곳까지 볼 수 있어요. 많으면 조회가 오래 걸려요.")
        span = st.radio("기간", list(CAL_RANGES), horizontal=True, key="cal_span", help=CAL_KINDS_HELP)
        submitted = st.form_submit_button("일정 조회")

    if submitted:
        names = []
        for part in re.split(NEWS_SPLIT, corp_text):
            part = canonical_corp(part)
            if part and name_key(part) not in {name_key(n) for n in names}:
                names.append(part)
        if not names:
            st.error("기업명을 입력해주세요.")
        elif len(names) > MAX_CAL_CORPS:
            st.error(f"기업은 한 번에 {MAX_CAL_CORPS}곳까지 넣을 수 있어요. (지금 {len(names)}곳)")
        else:
            st.session_state.pop("calendar", None)
            corps, missing = [], []
            with st.spinner("기업 정보를 확인하는 중입니다..."):
                for n in names:
                    code = resolve_corp_code(n)
                    (corps if code else missing).append((n, code) if code else n)
            for n in missing:
                hint = [label for label, _ in suggest_corps(n, limit=3)]
                st.warning(f'"{n}" 기업을 찾지 못해 뺐어요.' + (f" 혹시: {', '.join(hint)}" if hint else ""))
            if corps:
                lo, hi = CAL_RANGES[span]
                ws, we = TODAY + timedelta(days=lo), TODAY + timedelta(days=hi)
                with st.spinner(f"DART 공시를 확인하는 중입니다... ({len(corps)}곳, 기업이 많으면 몇 분 걸릴 수 있어요)"):
                    try:
                        data = collect_calendar(corps, ws, we)
                    except DartApiError as e:
                        st.error(str(e))
                    else:
                        st.session_state["calendar"] = {"data": data, "ws": ws, "we": we, "span": span, "n": len(corps)}

    res = st.session_state.get("calendar")
    if not res:
        return
    data, ws, we = res["data"], res["ws"], res["we"]
    st.caption(f"{ws.month}/{ws.day}~{we.month}/{we.day} · {res['n']}개 기업 · DART 공시 기준")
    est_n = sum(e["kind"] == "전년 기준 예상" for e in data["events"])
    show_est = False
    if est_n:  # 확정이 아닌 예상은 눌러야 보인다 (기사에 확정 일정처럼 쓰이는 것을 막기 위해)
        show_est = st.toggle(f"전년 기준 예상 일정도 보기 ({est_n}건)", key="cal_est",
                             help="작년 같은 시기 잠정실적 공시일을 올해로 옮긴 추정이에요. 확정된 일정이 아니고, 표에서 기업명 뒤에 *가 붙어요.")
    events = [e for e in data["events"] if show_est or e["kind"] != "전년 기준 예상"]
    if events:
        table = pd.DataFrame([{
            "날짜": cal_day(e["date"]), "기업": e["corp"] + ("*" if e["kind"] == "전년 기준 예상" else ""), "개최 목적": e.get("purpose") or "-", "내용": e["text"],
            "원문": DART_VIEW_URL.format(e["no"]) if e.get("no") else None,
        } for e in events])
        st.dataframe(table, hide_index=True, height=(len(table) + 1) * 35 + 3,
                     column_config={"날짜": st.column_config.TextColumn("날짜", width=80), "기업": st.column_config.TextColumn("기업", width=110),
                                    "개최 목적": st.column_config.TextColumn("개최 목적", width=260),
                                    "원문": LINK_COLUMN})
        copy_table_box(table, "cal_copy")
    else:
        st.info("이 기간에 확인된 실적 일정이 없어요. 기업이 IR 일정을 공시하면 나타나요." + (" 위 스위치를 켜면 전년 기준 예상을 볼 수 있어요." if est_n else ""))
    for (deadline, label), who in sorted(data["deadlines"].items()):
        names = who[0] if len(who) == 1 else f"{who[0]} 외 {len(who) - 1}곳"
        st.caption(f"법정 제출기한 · {cal_day(deadline)} {label} — {names}")
    if data["unread"]:
        st.caption("IR 개최 공시는 있으나 날짜를 읽지 못한 기업: " + ", ".join(
            f"[{n}]({DART_VIEW_URL.format(no)})" if no else n for n, _, no in data["unread"]))


def render_disclosure_hub():
    """탭: 공시 - 오늘의 공시(시장 전체) / 기업 / 섹터 중 방식을 고른다."""
    with st.container(key="disc_mode_bar"):  # '조회 방식' 글자와 선택지를 한 줄에
        c1, c2 = st.columns([1, 4], vertical_alignment="center")
        c1.markdown('<span class="mode-lbl">조회 방식</span>', unsafe_allow_html=True)
        mode = c2.radio("조회 방식", ["오늘의 공시", "기업", "섹터", "AI 검색"], horizontal=True, key="disc_mode", label_visibility="collapsed")
    if mode == "AI 검색":
        render_disc_ai_search()
    elif mode == "오늘의 공시":
        render_feed()
    elif mode == "섹터":
        render_feed(by_sector=True)
    else:
        render_disclosures()


# ------------------------------------------------------------
# 주가 탭: 시세판 · 특징주 · 업계 비교 · 주가+이벤트
# ------------------------------------------------------------
# 'prices' 브랜치의 prices.json을 읽는다 (.github/workflows/price_history.yml이 평일 아침 KRX 일별 시세를 쌓음).
# 계산은 stock_calc.py. KRX Open API는 전일 종가 기준이라 장중 시세는 없다.
PRICES_BRANCH = "prices"
RIVAL_PRESETS = {  # 이름은 KRX 종목명(비교할 때 표기 차이는 무시). 시세 자료에 없는 묶음은 화면에서 빠진다
    "현대차 vs 기아": ["현대자동차", "기아"],
    "삼성전자 vs SK하이닉스": ["삼성전자", "SK하이닉스"],
    "LG에너지솔루션 vs 삼성SDI": ["LG에너지솔루션", "삼성SDI"],
    "배터리·소재 4사": ["LG에너지솔루션", "삼성SDI", "에코프로비엠", "포스코퓨처엠"],
    "조선 3사": ["HD한국조선해양", "한화오션", "삼성중공업"],
    "방산 4사": ["한화에어로스페이스", "한국항공우주", "LIG디펜스앤에어로스페이스", "현대로템"],
    "통신 3사": ["SK텔레콤", "KT", "LG유플러스"],
    "철강": ["POSCO홀딩스", "현대제철", "동국제강"],
    "정유": ["SK이노베이션", "S-Oil"],
}
PX_GENERIC_ALIASES = {"삼성", "엘지", "에스케이", "현대", "한화", "롯데"}  # 기사 제목 찾기에서 너무 흔해서 뺀 줄임말
PX_KIND_COLORS = {"통신": "#1c7ed6", "단독": "#f08c00", "공시": "#2f9e44"}


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_prices():
    res = requests.get(f"https://raw.githubusercontent.com/{DATA_REPO}/{PRICES_BRANCH}/prices.json", timeout=(10, 60))
    res.raise_for_status()
    return res.json()  # 실패는 캐시에 남지 않아 다음에 다시 시도


def get_prices():
    """쌓아 둔 시세 자료. 없거나 이상하면 None."""
    try:
        data = _fetch_prices()
    except Exception:
        return None
    if isinstance(data, dict) and data.get("dates") and isinstance(data.get("s"), dict) and data["s"]:
        return data
    return None


def px_names(data):
    """{종목명: 종목코드}"""
    return {s["n"]: code for code, s in data["s"].items() if s.get("n")}


def px_find(names, wanted):
    """종목명 목록에서 wanted를 찾는다 (표기 차이·'현대차' 같은 줄임말도 인식)"""
    keys = {name_key(wanted)} | {name_key(a) for a in NEWS_ALIASES.get(wanted, [])}
    return next((n for n in names if name_key(n) in keys), None)


def px_pct(v, digits=1):
    return "-" if v is None or pd.isna(v) else f"{v:+.{digits}f}%"


def px_cap_text(eok):
    """시총(억원) -> 기사체 금액"""
    return "-" if eok is None or pd.isna(eok) else won_text(float(eok) * 1e8)


def px_period_controls(key, data):
    """기간 고르기 -> (시작일, 끝일)"""
    dates = sc.dates_of(data)
    first, last = dates.min(), dates.max()
    period = st.radio("기간", sc.PERIODS, index=1, horizontal=True, key=f"{key}_period")
    if period == "직접":
        c1, c2 = st.columns(2)
        default_from = max(first, last - pd.DateOffset(months=3)).date()
        s = c1.date_input("시작일", value=default_from, min_value=first.date(), max_value=last.date(), key=f"{key}_from")
        e = c2.date_input("끝일", value=last.date(), min_value=first.date(), max_value=last.date(), key=f"{key}_to")
        start, end = sorted([pd.Timestamp(s), pd.Timestamp(e)])  # 거꾸로 골라도 맞바꿔 쓴다
        return start, end
    return sc.period_start(period, first, last), last


def px_line_chart(long_df, y_title, zero=False, fmt="%Y-%m-%d", axis_fmt=None):
    """long_df: 날짜·이름·값 열의 긴 표 -> 선 그래프 (fmt: 마우스를 올릴 때 날짜·시각 표기, axis_fmt: 가로축 표기)"""
    import altair as alt

    chart = alt.Chart(long_df).mark_line().encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format=axis_fmt) if axis_fmt else alt.Axis()),
        y=alt.Y("값:Q", title=y_title, scale=alt.Scale(zero=zero)),
        color=alt.Color("이름:N", title=None, legend=alt.Legend(orient="bottom")),
        tooltip=[alt.Tooltip("날짜:T", format=fmt), "이름:N", alt.Tooltip("값:Q", format=",.2f" if axis_fmt else ",.1f")],
    ).properties(height=300)
    st.altair_chart(chart)


def px_long(series_by_name):
    """{이름: Series} -> 날짜·이름·값 긴 표"""
    parts = [pd.DataFrame({"날짜": s.index, "이름": n, "값": s.values}) for n, s in series_by_name.items() if len(s)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["날짜", "이름", "값"])


# ---- 업계 비교 ----
def render_px_rivals(data):
    names = px_names(data)
    presets = {k: [px_find(names, n) for n in v] for k, v in RIVAL_PRESETS.items()}
    presets = {k: v for k, v in presets.items() if all(v)}
    st.session_state["px_presets"] = presets

    def pick_preset():  # 묶음을 고르면 아래 기업 선택을 그 묶음으로 바꾼다
        p = st.session_state.get("px_rv_preset")
        if p in st.session_state.get("px_presets", {}):
            st.session_state["px_rv_names"] = list(st.session_state["px_presets"][p])

    if presets:
        first_preset = next(iter(presets))
        st.session_state.setdefault("px_rv_preset", first_preset)
        st.session_state.setdefault("px_rv_names", list(presets[first_preset]))
    st.session_state["px_rv_names"] = [n for n in st.session_state.get("px_rv_names", []) if n in names]
    if presets:
        st.selectbox("비교 묶음", ["직접 고르기"] + list(presets), key="px_rv_preset", on_change=pick_preset)
    chosen = st.multiselect("비교할 기업 (2~5곳)", sorted(names), key="px_rv_names", max_selections=5)
    if len(chosen) < 2:
        st.info("기업을 2곳 이상 골라주세요.")
        return
    start, end = px_period_controls("px_rv", data)

    rows, idx_by, cap_by = [], {}, {}
    for n in chosen:
        code = names[n]
        df = sc.cut(sc.frame(data, code), start, end)
        idx = sc.chain_index(df)
        if idx.empty:
            continue
        cap = df["cap"].dropna()
        ret = sc.period_return(df)
        rows.append({"기업": n, "시장": data["s"][code].get("m", ""), "기간 수익률": px_pct(ret), "_ret": ret if ret is not None else -1e9,
                     "시총(기준일)": px_cap_text(cap.iloc[-1] if len(cap) else None),
                     "시총 증감": px_pct((cap.iloc[-1] / cap.iloc[0] - 1) * 100) if len(cap) > 1 and cap.iloc[0] else "-",
                     "평균 거래대금": f"{df['val'].mean():,.0f}억원" if df["val"].notna().any() else "-"})
        idx_by[n] = idx
        cap_by[n] = cap
    if not rows:
        st.info("이 기간에 시세 자료가 없어요.")
        return
    rows.sort(key=lambda r: -r["_ret"])
    table = pd.DataFrame(rows).drop(columns=["_ret"])
    st.dataframe(table.style.map(price_style, subset=["기간 수익률", "시총 증감"]), hide_index=True)
    st.caption(f"{start:%Y.%m.%d} ~ {end:%Y.%m.%d} · 수익률은 KRX 등락률을 이어 붙인 값(시작일 종가=100, 배당 제외) · 시총은 보통주+우선주")

    st.markdown("##### 수익률 비교 (시작일=100)")
    px_line_chart(px_long(idx_by), "지수")
    st.markdown("##### 시가총액 (조원)")
    px_line_chart(px_long({n: s / 1e4 for n, s in cap_by.items()}), "조원")

    # 시총 순서가 바뀐 날
    flips = []
    for a, b in combinations(list(cap_by), 2):
        for day, who in sc.crossings(cap_by[a], cap_by[b]):
            flips.append((day, a if who == "a" else b, b if who == "a" else a))
    if flips:
        st.markdown("##### 시총 순위가 바뀐 날")
        for day, up, down in sorted(flips, key=lambda f: f[0], reverse=True)[:10]:
            st.write(f"- {day:%m.%d} {up}이(가) {down}의 시총을 앞섰어요")

    wide = pd.concat({**{f"{n} 지수": s for n, s in idx_by.items()}, **{f"{n} 시총(억원)": s for n, s in cap_by.items()}}, axis=1).round(2)
    st.download_button("표 내려받기 (CSV)", wide.to_csv().encode("utf-8-sig"), file_name="업계비교.csv", mime="text/csv", key="px_rv_dl")


# ---- 2) 주가 + 이벤트 ----
def px_events(archive, name, start, end):
    """모아 둔 기사·공시 중 이 기업 제목이 든 것 -> [{dt, kind, title, url}] 최신순 (시작일~끝일)"""
    keys = {name_key(name)} | {name_key(a) for a in NEWS_ALIASES.get(name, []) if a not in PX_GENERIC_ALIASES}
    keys.discard("")
    out, seen = [], set()
    for row in archive.get("items", []):
        it = stored_item(row)
        if not it or it["dt"].replace(tzinfo=None) < start or it["dt"].replace(tzinfo=None) >= end + pd.Timedelta(days=1):
            continue
        tk = name_key(it["title"])
        if not any(key_in_title(k, tk, it["title"]) for k in keys) or is_blocked(it["title"]) or (it["kind"] == "통신" and is_sports(it["title"])):
            continue
        sig = title_key(it["title"])
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"dt": it["dt"].replace(tzinfo=None), "kind": it["kind"] or "기사", "title": it["title"], "url": it["url"]})
    return sorted(out, key=lambda e: e["dt"], reverse=True)


def render_px_events(data):
    names = px_names(data)
    ordered = sorted(names)
    name = st.selectbox("기업", ordered, index=ordered.index("현대자동차") if "현대자동차" in ordered else 0, key="px_ev_name")
    start, end = px_period_controls("px_ev", data)
    full = sc.frame(data, names[name])
    df = sc.cut(full, start, end).dropna(subset=["close"])
    if df.empty:
        st.info("이 기간에 시세 자료가 없어요.")
        return
    archive = get_stored("archive.json")
    events = px_events(archive, name, start, end) if archive else []
    price = df["close"].reset_index()
    price.columns = ["날짜", "종가"]
    ev_rows = []
    for e in events:
        pos = df.index.searchsorted(pd.Timestamp(e["dt"]).normalize())
        if pos >= len(df):
            continue
        r0, r3 = sc.event_reaction(full, e["dt"])
        ev_rows.append({"날짜": e["dt"].date(), "종류": e["kind"], "제목": e["title"], "원문": e["url"], "당일 등락": px_pct(r0), "3거래일 누적": px_pct(r3),
                        "차트날짜": df.index[pos], "종가": float(df["close"].iloc[pos])})
    import altair as alt

    line = alt.Chart(price).mark_line(color="#495057").encode(
        x=alt.X("날짜:T", title=None), y=alt.Y("종가:Q", title="종가(원)", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), alt.Tooltip("종가:Q", format=",.0f")])
    picked = []
    if ev_rows:
        ev_df = pd.DataFrame(ev_rows)
        ev_df["번호"] = range(len(ev_df))
        ev_df["날짜"] = ev_df["날짜"].map(lambda d: d.strftime("%Y-%m-%d"))  # 글자로 넘겨야 툴팁에 숫자(타임스탬프)가 아니라 날짜가 보인다
        # 폰에서는 마우스 올리기(툴팁)가 어렵고 긴 제목은 화면 밖으로 잘리므로, 점을 누르면 아래 카드에 내용을 보여준다 (툴팁은 짧게)
        pick = alt.selection_point(name="ev_pick", fields=["번호"], nearest=True, on="click", clear="dblclick")
        pts = alt.Chart(ev_df).mark_point(filled=True, opacity=0.9).encode(
            x=alt.X("차트날짜:T"), y=alt.Y("종가:Q", scale=alt.Scale(zero=False)),
            color=alt.Color("종류:N", title=None, scale=alt.Scale(domain=list(PX_KIND_COLORS), range=list(PX_KIND_COLORS.values())), legend=alt.Legend(orient="bottom")),
            size=alt.condition(pick, alt.value(320), alt.value(140)),
            tooltip=["날짜:N", "종류:N", "당일 등락:N"]).add_params(pick)
        state = st.altair_chart((line + pts).properties(height=320), on_select="rerun", key="px_ev_chart")
        try:
            picked = [int(p["번호"]) for p in (state.selection.get("ev_pick") or []) if "번호" in p]
        except (AttributeError, TypeError, ValueError):
            picked = []
        if picked:
            for i in picked[:3]:
                e = ev_rows[i]
                with st.container(border=True):
                    st.markdown(f"**{e['날짜']:%m.%d}** · {e['종류']} · 당일 {e['당일 등락']} · 3거래일 {e['3거래일 누적']}  \n"
                                f"[{html.escape(str(e['제목'])).replace('[', '(').replace(']', ')')}]({e['원문']})")
        else:
            st.caption("그래프의 점을 누르면 그 기사·공시가 여기에 나와요. (점이 겹쳐 있으면 가까운 것 하나)")
    else:
        st.altair_chart(line.properties(height=320))
    last = df.iloc[-1]
    ret = sc.period_return(df)
    st.markdown(cb_cards([
        ("종가", f"{last['close']:,.0f}원", px_pct(last["rate"], 2) + " (전일比)" if pd.notna(last["rate"]) else None,
         None if pd.isna(last["rate"]) else ("down" if last["rate"] > 0 else "up" if last["rate"] < 0 else "flat")),  # 카드 색은 증시 관행(상승=빨강)에 맞춰 뒤집는다
        ("기간 수익률", px_pct(ret), None, None if ret is None else ("down" if ret > 0 else "up" if ret < 0 else "flat")),
        ("시가총액", px_cap_text(last["cap"]), "보통주+우선주", "flat"),
        ("거래대금", f"{last['val']:,.0f}억원" if pd.notna(last["val"]) else "-", None, "flat"),
    ]), unsafe_allow_html=True)
    if not archive:
        st.caption("모아 둔 기사·공시 자료를 불러오지 못해 이벤트는 표시하지 못했어요.")
    elif not ev_rows:
        first_day = min((stored_item(r)["dt"] for r in archive["items"] if stored_item(r)), default=None)
        st.caption("이 기간에 이 기업의 기사·공시가 모아져 있지 않아요." + (f" (모으기 시작: {first_day:%Y.%m.%d})" if first_day else "")
                   + " 섹터 기업의 통신·단독 기사와 주요 공시만 모아요.")
    else:
        st.markdown("##### 이벤트와 주가 반응")
        st.caption("공시는 접수 날짜만 알 수 있어서 당일 등락은 그날(휴장이면 다음 거래일) 기준이에요. 원인을 단정할 수 없고, 같은 날 다른 이벤트가 겹쳤을 수 있어요.")
        table = pd.DataFrame(ev_rows)[["날짜", "종류", "제목", "당일 등락", "3거래일 누적", "원문"]]
        st.dataframe(table.style.map(price_style, subset=["당일 등락", "3거래일 누적"]), hide_index=True,
                     column_config={"원문": st.column_config.LinkColumn("원문", display_text="열기")})


# ---- 시세판 · 특징주 ----
def px_sector_codes(data):
    """{섹터: [종목코드]} - 앱의 섹터 기업 목록(INDUSTRY_GROUPS) 중 시세 자료에 있는 종목"""
    names = px_names(data)
    out = {}
    for sector, companies in INDUSTRY_GROUPS.items():
        codes = []
        for n in companies:
            disp = px_find(names, n)
            if disp and names[disp] not in codes:
                codes.append(names[disp])
        if codes:
            out[sector] = codes
    return out


def px_labels(data, sectors):
    """{종목코드: 분류 이름} - 섹터 기업은 섹터, 그 밖의 추적 종목은 재계 그룹"""
    label = {}
    for g, v in (data.get("g") or {}).items():
        for code in v.get("codes") or []:
            label.setdefault(code, g)
    for sector, codes in sectors.items():
        for code in codes:
            label[code] = sector
    return label


def px_day_picker(data):
    """기준일 고르기 (최근 60거래일) -> 'YYYYMMDD'"""
    options = list(reversed(data["dates"][-60:]))
    return st.selectbox("기준일", options, format_func=lambda d: f"{d[:4]}.{d[4:6]}.{d[6:]}", key="px_day")


INTRADAY_BRANCH = "intraday"


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_intraday():
    res = requests.get(f"https://raw.githubusercontent.com/{DATA_REPO}/{INTRADAY_BRANCH}/intraday.json", timeout=(10, 30))
    res.raise_for_status()
    return res.json()


def get_intraday():
    """오늘 장중 5분봉 자료 (intraday 브랜치). 없거나 이상하면 None."""
    try:
        data = _fetch_intraday()
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("t") and isinstance(data.get("s"), dict) and data["s"] else None


def px_basis(data):
    """시세 기준 고르기. 장중 자료가 KRX 자료보다 새 날이면 '장중/종가'를 고르게 하고, 아니면 종가(기준일 선택)만.
    -> {"snap": 종목 표, "day": 'YYYYMMDD', "intraday": bool, "label": 설명, "intra": 장중 자료, "upto": 시각 또는 None}"""
    intra = get_intraday()
    if sc.intraday_ready(intra, data):
        d_new, d_krx = intra["date"][4:6] + "." + intra["date"][6:], data["dates"][-1][4:6] + "." + data["dates"][-1][6:]
        names = ({"장중": f"{d_new} 마감 (야후·잠정)", "종가": f"{d_krx} 종가 (KRX 확정)"} if intra.get("closed")
                 else {"장중": f"{d_new} 장중 (야후)", "종가": f"{d_krx} 종가 (KRX)"})  # KRX가 새 날짜를 올리면 이 선택지는 저절로 사라지고 KRX 종가만 보인다
        basis = st.radio("기준", ["장중", "종가"], horizontal=True, key="px_basis", format_func=names.get)
        if basis == "장중":
            marks = sc.intraday_marks(intra)
            date_text = f"{intra['date'][:4]}.{intra['date'][4:6]}.{intra['date'][6:]}"
            pick = st.selectbox("시각", ["최신"] + marks, format_func=lambda m: f"{date_text} 최신" if m == "최신" else f"{m[:2]}:{m[2:]} 현재", key="px_time")
            upto = None if pick == "최신" else pick
            snap, last_end = sc.intraday_snapshot(intra, data, upto)
            day = intra["date"]
            fetched = str(intra.get("fetched", ""))[11:16]
            label = (f"{day[:4]}.{day[4:6]}.{day[6:]} 장중 · 마지막 봉 {last_end or '-'} · 자료 갱신 {fetched or '-'} · "
                     "야후 파이낸스(비공식) 5분봉이라 지연·오차가 있을 수 있어요 · "
                     + ("마감 뒤 일봉 종가를 반영했어요 (15:00~15:30 중간 흐름은 없어요) · 확정 종가는 다음 영업일 아침 KRX 자료예요" if intra.get("closed") else "마감 가격은 다음 영업일 아침 종가 자료가 정확해요"))
            return {"snap": snap, "day": day, "intraday": True, "label": label, "intra": intra, "upto": upto}
    day = px_day_picker(data)
    return {"snap": sc.snapshot(data, day), "day": day, "intraday": False, "label": f"{day[:4]}.{day[4:6]}.{day[6:]} 종가 기준", "intra": None, "upto": None}


def px_ratio_text(v):
    return "-" if v is None or pd.isna(v) else f"{v:.1f}배"


def px_val_text(eok):
    return "-" if eok is None or pd.isna(eok) else px_cap_text(eok)


def px_stock_table(snap, labels, ratio_thr=None):
    """종목 표 (종목·분류·종가·등락률·거래대금·거래대금 배수·시총[·표시])"""
    rows = []
    for _, r in snap.iterrows():
        row = {"종목": r["name"], "분류": labels.get(r["code"], ""), "종가": f"{r['close']:,.0f}원", "등락률": px_pct(r["rate"], 2),
               "거래대금": px_val_text(r["val"]), "거래대금 배수": px_ratio_text(r["ratio"]), "시총": px_cap_text(r["cap"])}
        if ratio_thr is not None:
            row["표시"] = sc.feature_flags(r, ratio_thr)
        rows.append(row)
    return pd.DataFrame(rows)


def render_px_intraday_flow(data, b, sectors):
    """장중 시세판: 시간대별 섹터 평균 등락률 표와 추이 그래프"""
    intra = b["intra"]
    curve = sc.intraday_sector_curve(intra, data, sectors)
    if b["upto"]:
        curve = curve[curve.index.strftime("%H%M") <= b["upto"]]
    if curve.empty:
        return
    hourly = sc.intraday_hourly(curve, sc.INTRADAY_MARKS)
    latest = curve.iloc[[-1]].copy()
    latest.index = [f"{latest.index[0]:%H:%M} (최신)"]
    table = pd.concat([hourly, latest]) if latest.index[0][:5] not in set(hourly.index) else hourly
    show = table.map(lambda v: px_pct(v, 2)).reset_index().rename(columns={"index": "시각"})
    st.markdown("##### 시간대별 섹터 평균 등락률 (전일 종가 대비)")
    st.dataframe(show.style.map(price_style, subset=[c for c in show.columns if c != "시각"]), hide_index=True)
    px_line_chart(px_long({name: curve[name].dropna() for name in curve.columns}), "등락률(%)", zero=True, fmt="%H:%M", axis_fmt="%H:%M")


def render_px_board(data):
    """시세판: 섹터별 평균 등락률·오른/내린 종목 수·최고/최저, 섹터 기업 시세"""
    b = px_basis(data)
    snap = b["snap"]
    sectors = px_sector_codes(data)
    summary = sc.sector_summary(snap, sectors)
    if not summary:
        st.info("이 기준에 보여줄 섹터 시세가 없어요.")
        return
    rows = [{"섹터": s["sector"], "종목 수": f"{s['n']}곳", "평균 등락률": px_pct(s["avg"], 2), "오른/내린": f"▲{s['up']} ▼{s['down']}",
             "거래대금 합계": px_val_text(s["val"]), "최고": f"{s['best'][0]} {px_pct(s['best'][1])}", "최저": f"{s['worst'][0]} {px_pct(s['worst'][1])}"} for s in summary]
    st.dataframe(pd.DataFrame(rows).style.map(price_style, subset=["평균 등락률"]), hide_index=True)
    st.caption("기준 안내", help=b["label"].replace(" · ", "  \n") + "  \n평균은 섹터 기업 시세의 단순 평균이에요 (비상장사 제외, 시총 가중 아님)")
    if b["intraday"]:
        render_px_intraday_flow(data, b, sectors)

    labels = px_labels(data, sectors)
    pick = st.selectbox("섹터별 종목 보기", ["전체"] + [s["sector"] for s in summary], key="px_board_sector")
    codes = {c for cs in sectors.values() for c in cs} if pick == "전체" else set(sectors[pick])
    detail = snap[snap["code"].isin(codes)].sort_values("rate", ascending=False, na_position="last")
    st.dataframe(px_stock_table(detail, labels).style.map(price_style, subset=["등락률"]), hide_index=True)
    st.caption("표 설명", help="거래대금 배수는 직전 20거래일 평균 거래대금 대비예요.  \n시총은 보통주+우선주예요."
               + ("  \n장중에는 5분봉으로 추정한 누적 거래대금 기준이고, 시총은 전일 시총을 가격 변화율로 늘린 근사값이에요." if b["intraday"] else ""))


def render_px_features(data):
    """특징주: 등락률이 크거나 거래대금이 갑자기 늘어난 종목과 그 무렵의 기사·공시"""
    b = px_basis(data)
    day = b["day"]
    sectors = px_sector_codes(data)
    labels = px_labels(data, sectors)
    scope = st.radio("대상", ["섹터 기업", "추적 종목 전체"], horizontal=True, key="px_ft_scope")
    c1, c2 = st.columns(2)
    rate_thr = c1.slider("등락률 기준 (±%)", 3.0, 15.0, 5.0, 0.5, key="px_ft_rate")
    ratio_thr = c2.slider("거래대금 배수 (직전 20거래일 평균 대비)", 2.0, 10.0, 3.0, 0.5, key="px_ft_ratio")
    snap = b["snap"]
    if scope == "섹터 기업":
        snap = snap[snap["code"].isin({c for cs in sectors.values() for c in cs})]
    feats = sc.pick_features(snap, rate_thr, ratio_thr)
    st.caption(f"{b['label']} · {scope} {len(snap)}곳 중 등락률 ±{rate_thr:g}% 이상이거나 거래대금이 평균의 {ratio_thr:g}배 이상인 종목"
               + (" (장중에는 누적 거래대금 기준)" if b["intraday"] else ""))
    if feats.empty:
        st.info("기준에 맞는 종목이 없어요. 기준을 낮춰 보세요.")
        return
    st.dataframe(px_stock_table(feats, labels, ratio_thr).style.map(price_style, subset=["등락률"]), hide_index=True)

    archive = get_stored("archive.json")
    names = list(feats["name"])
    st.markdown("##### 관련 기사·공시")
    name = st.selectbox("종목", names, key="px_ft_pick")
    if not archive:
        st.caption("모아 둔 기사·공시 자료를 불러오지 못했어요.")
        return
    d0 = pd.Timestamp(day)
    events = px_events(archive, name, d0 - pd.Timedelta(days=1), d0)
    if not events:
        st.caption("기준일과 그 전날에 모아 둔 기사·공시가 없어요. 섹터 기업의 통신·단독 기사와 주요 공시만 모아요.")
    for e in events[:10]:
        title = e["title"].replace("[", "(").replace("]", ")")
        st.markdown(f"- [{title}]({e['url']}) · {e['kind']} · {e['dt']:%m.%d %H:%M}")
    st.caption("기사·공시는 원인이 아니라 같은 시기에 나온 것이에요. 공시는 접수 날짜만 알 수 있어요.")


def render_stock():
    """탭: 주가 - 시세판 / 특징주 / 업계 비교 / 주가·이벤트 (KRX 일별 종가를 매일 쌓은 자료)"""
    data = get_prices()
    if data is None:
        st.info("시세 자료가 아직 없어요. 매 평일 아침 GitHub 작업이 KRX 시세를 모아 두는데, 처음에는 과거 시세를 채우느라 며칠 걸릴 수 있어요.")
        return
    dates = sc.dates_of(data)
    st.caption("자료 기준", help=f"종가는 KRX 일별 자료 {dates.min():%Y.%m.%d} ~ {dates.max():%Y.%m.%d} ({len(dates)}거래일, 평일 아침 갱신)  \n장중은 야후 파이낸스 5분봉(비공식)을 장중 30분마다 갱신")
    mode = st.radio("보기", ["시세판", "특징주", "업계 비교", "주가·이벤트"], horizontal=True, key="px_mode", label_visibility="collapsed")
    if mode == "시세판":
        render_px_board(data)
    elif mode == "특징주":
        render_px_features(data)
    elif mode == "업계 비교":
        render_px_rivals(data)
    else:
        render_px_events(data)


def run_js(code):
    """높이 없는 iframe으로 짧은 스크립트를 실행한다. 새 Streamlit은 st.iframe, 옛 버전은 components.html. 실패해도 앱은 계속."""
    try:
        if hasattr(st, "iframe"):
            st.iframe(code, height=1)
        else:
            st.components.v1.html(code, height=0)
    except Exception:
        pass


# ------------------------------------------------------------
# 수출 (관세청 수출입 통계: GitHub 'trade' 브랜치의 trade.json, trade_data.py가 매일 모아 둠)
# ------------------------------------------------------------
TRADE_BRANCH = "trade"
TRADE_VIEWS = ["검색", "월간 현황", "10일 단위 속보", "국가·경제권"]
CUSTOMS_KEY = load_secret("CUSTOMS_API_KEY")  # 공공데이터포털 일반 인증키 (검색 화면이 품목별 국가별 수출입실적 API를 직접 부른다)
TRADE_ITEM_URL = "https://apis.data.go.kr/1220000/" + tc.ITEM_API_PATH
TRADE_KINDS = ["일반", "국가별", "품목별", "AI검색"]
TRADE_ITEM_MODES = ["전체", "20대", "HS코드", "MTI코드"]
TRADE_REGION_MODES = ["전체", "국가", "경제권", "대륙"]
MTI_FILE = "mti_map.json"  # mti_build.py가 무역협회 HSK-MTI 연계표(엑셀)로 만든 파일 (앱과 같은 폴더)
TRADE_MTI_WARN_CALLS = 300  # 한 조회가 이만큼 넘는 관세청 호출이 필요하면 오래 걸린다고 알린다
TRADE_MAX_SERIES = 12  # 한 번에 보는 항목 수(품목×국가 조합 포함). 조합마다 API를 부르므로 제한한다
# 10일 단위 잠정치(수출·수입 × 주요 품목·국가)는 항목 이름 없이 1~10번째 금액만 오고, 순번이 고정돼 있다. 순번별 이름은 월별 정식 통계의 금액과 맞춰 확인한 것.
TRADE_RANK_NAMES = {
    "exp_cnty": {1: "중국", 2: "미국", 3: "EU", 4: "베트남", 5: "홍콩", 6: "일본", 7: "대만", 8: "인도", 9: "싱가포르", 10: "말레이시아"},
    "imp_cnty": {1: "중국", 2: "미국", 3: "EU", 4: "일본", 5: "베트남", 6: "호주", 7: "대만", 8: "사우디아라비아", 9: "러시아 연방", 10: "말레이시아"},
    "exp_item": {1: "반도체", 2: "철강제품", 3: "승용차", 4: "석유제품", 5: "무선통신기기", 6: "선박", 7: "자동차부품", 8: "컴퓨터주변기기", 9: "정밀기기", 10: "가전제품"},
    "imp_item": {1: "반도체", 2: "원유", 3: "기계류", 4: "가스", 5: "반도체제조용장비", 6: "정밀기기", 7: "석유제품", 8: "무선통신기기", 9: "승용차", 10: "석탄"},
}  # 수출 품목은 관세청 기술문서(수출 주요품목별 10일 단위)의 순번 설명, 국가는 월별 정식 통계 금액과 맞춰 확인한 것. 수입 품목도 기술문서(수입 주요품목별 10일 단위)의 순번 설명


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_trade():
    res = requests.get(f"https://raw.githubusercontent.com/{DATA_REPO}/{TRADE_BRANCH}/trade.json", timeout=(10, 60))
    res.raise_for_status()
    return res.json()  # 실패는 캐시에 남지 않아 다음에 다시 시도


def get_trade():
    """모아 둔 수출입 통계. 없거나 이상하면 None."""
    try:
        data = _fetch_trade()
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("months") and isinstance(data.get("total"), dict) else None


GROUPS_BRANCH = "groups"


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_groups():
    res = requests.get(f"https://raw.githubusercontent.com/{DATA_REPO}/{GROUPS_BRANCH}/groups.json", timeout=(10, 60))
    res.raise_for_status()
    return res.json()


@st.cache_data(ttl=3600, show_spinner=False)
def _groups_country(updated, months):
    g = _fetch_groups()
    data = get_trade()
    names = {c: v[0] for c, v in (data or {}).get("country", {}).items()}
    return tc.groups_country(g, list(months), names)


def get_groups_country(data, metrics=()):
    """20대 품목 × 국가 × 월 자료(groups 브랜치)를 {품목: {국가코드: [이름, 수출[], 수입[]]}}로. 못 읽거나 덜 받았거나, 중량 지표가 필요하면 None(관세청에서 직접 조회)"""
    if any("중량" in m for m in metrics):
        return None
    try:
        g = _fetch_groups()
        if not tc.groups_usable(g, data["months"]):
            return None
        return _groups_country(str(g.get("updated", "")), tuple(data["months"]))
    except Exception:
        return None


FLASH_BRANCH = "flash"


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_flash():
    res = requests.get(f"https://raw.githubusercontent.com/{DATA_REPO}/{FLASH_BRANCH}/flash.json", timeout=(10, 30))
    res.raise_for_status()
    return res.json()


def trade_flash(data):
    """산업부 보도자료 잠정치 중 관세청 확정 자료(data)보다 새로운 달 -> 그 달의 자료 또는 None (없거나 읽지 못하면 None)"""
    try:
        return tc.flash_new(_fetch_flash(), data["months"])
    except Exception:
        return None


def trade_eok(v):
    """달러 -> '659.0억달러'"""
    return "-" if v is None else f"{v / 1e8:,.1f}억달러"


def trade_cheon(v):
    """천달러 -> '659.0억달러'"""
    return "-" if v is None else f"{v / 1e5:,.1f}억달러"


def trade_month_pick(data, key):
    """월 고르기 (최신이 먼저) -> 그 달의 위치"""
    months = data["months"]
    pick = st.selectbox("월", list(reversed(months)), format_func=lambda m: m.replace("-", "."), key=key)
    return months.index(pick)


def trade_styled(table, cols):
    return table.style.map(cb_style, subset=cols)


HS_NOTES = {  # 코드에 대해 확인된 설명(관세청 확인 내용) -> 결과 아래에 보여준다
    "3102109000": "관세청 확인(2026.10): '질소비료의 기타'가 이 세번이고, 산업용·차량용 요소가 여기에 들어가요. 일부 목재용도 포함되지만 양이 적어 '산업용·차량용 요소'로 표현해도 큰 무리가 없다고 해요.",
}


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=60)
def trade_query(hs, cnty, start, end):
    """품목(hs)·국가(cnty) 조건의 월별 행 (관세청 품목별 국가별 수출입실적 API). 같은 조회는 6시간 동안 저장한 결과를 쓴다. 실패는 TradeApiError."""
    return tc.query_items(trade_fetch_text, hs, cnty, start, end)


@st.cache_resource(show_spinner=False)
def get_mti():
    """MTI 연계표(mti_map.json). 없으면 None."""
    return tc.load_mti(os.path.join(os.path.dirname(os.path.abspath(__file__)), MTI_FILE))


def trade_fetch_text(params):
    """관세청 품목별 국가별 API 한 번 호출 (접속이 끊기면 두 번 더 시도). 실패는 TradeApiError."""
    key = unquote(CUSTOMS_KEY) if "%" in CUSTOMS_KEY else CUSTOMS_KEY
    last = None
    for attempt in range(3):
        try:
            res = requests.get(TRADE_ITEM_URL, params={"serviceKey": key, **params}, timeout=(10, 90))
            res.raise_for_status()
            return res.text
        except requests.RequestException as e:
            last = e
            time.sleep(1 + attempt)
    raise tc.TradeApiError("관세청 서버에 연결하지 못했어요. 잠시 후 다시 시도해주세요.") from last


def trade_hs_rows(code, start, end, cnty=None):
    """HS 코드(2·4·6·8·10자리)의 월별 행. 관세청 API는 2·4·6자리만 받아 한 단계 아래 행을 주므로, 8·10자리는 6자리로 조회해 그 코드에 속한 행만 남긴다."""
    if len(code) in (2, 4, 6):
        return trade_query(code, cnty, start, end)
    return [r for r in trade_query(code[:6], cnty, start, end) if r["hs"].startswith(code)]


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=40)
def trade_mti_query(key, cnty, start, end):
    """산업부 품목(MTI 코드 또는 '@주력품목')의 월별 행 -> (행들, 건너뛴 기간). 묶인 HS 4·6자리마다 관세청 API를 불러 합치므로 몇 초~수십 초 걸린다. 같은 조회는 6시간 저장."""
    return tc.query_mti(trade_fetch_text, get_mti(), key, cnty, start, end)


def trade_usd(v):
    """달러 -> 크기에 맞춘 글자 (12.3억달러 / 3,250만달러 / 8,000달러)"""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:,.1f}억달러"
    if a >= 1e4:
        return f"{v / 1e4:,.0f}만달러"
    return f"{v:,.0f}달러"


def trade_ton(kg):
    return "-" if kg is None else f"{kg / 1000:,.0f}톤"


def trade_hs_options(data):
    """품목 고르기 선택지 {표시 글: 코드}: 4자리 품목 이름 목록(수집돼 있을 때)"""
    return {f"{c} {n}": c for c, n in sorted((data.get("hs") or {}).items())}


TRADE_MTI_MAX_CALLS = 1500


def trade_mti_calls(mti, key, start, end):
    """산업부 품목 조회에 필요한 관세청 호출 수 (HS 4·6자리 조회 수 × 12개월 구간 수)"""
    s_ym, e_ym = start.replace("-", ""), end.replace("-", "")
    include, exclude = tc.mti_spec(key)
    total = 0
    for table, s, e in tc.mti_segments(mti, s_ym, e_ym):
        hsk = tc.mti_hsk(table, include, exclude)
        if hsk:
            j4, j6 = tc.mti_plan(table, hsk)
            total += (len(j4) + len(j6)) * len(tc.chunk_months(s, e))
    return total


def trade_shift_year(by_month):
    """전년 같은 기간의 월별 지표를 올해 달로 옮긴다 ({'2025-03': ..} -> {'2026-03': ..})"""
    out = {}
    for k, v in by_month.items():
        n = tc.month_add(k.replace("-", ""), 12)
        out[f"{n[:4]}-{n[4:]}"] = v
    return out


def trade_series(data, q):
    """검색 조건 q -> [{name, month(월별 지표), prev(전년 동기 월별 또는 None), rows(품목 행 또는 None)}]. API 오류는 TradeApiError.
    q: kind('품목·지역'), items([('hs'|'mti', 코드)], 비면 전체), regions([(종류, 이름, 국가코드들)], 비면 전체)"""
    months, kind = data["months"], q["kind"]
    q = dict(q, end=min(q["end"], months[-1]), end_sel=q["end"])  # 잠정 달은 관세청 자료에 없으니 확정된 마지막 달까지만 조회하고, 잠정치는 보도자료에서 붙인다
    s_ym, e_ym = q["start"].replace("-", ""), q["end"].replace("-", "")
    p_s, p_e = tc.month_add(s_ym, -12), tc.month_add(e_ym, -12)
    expected = tc.month_span(s_ym, e_ym)
    out = []

    def stored(name, arrays, flash_key):
        cur = tc.stored_metrics(arrays, months, q["start"], q["end"])
        prev = tc.stored_metrics(arrays, months, f"{p_s[:4]}-{p_s[4:]}", f"{p_e[:4]}-{p_e[4:]}")
        out.append({"name": name, "month": cur, "prev": trade_shift_year(prev) if prev else None, "rows": None, "flash_key": flash_key})  # 전년 자료가 일부만 있어도 있는 달끼리는 비교

    regions, items = q["regions"], q["items"]
    if not items:  # 품목 전체: 모아 둔 월별 자료(국가별 합산)로 보여준다
        if not regions:
            stored("전체 수출입", (data["total"]["exp"], data["total"]["imp"]), ("총괄", ""))
        for rkind, name, codes in regions:
            since = tc.ZONE_WINDOW.get(name) if rkind == "경제권" else None
            stored(name, tc.sum_arrays(data["country"], codes, months, since), (rkind, name))
        return trade_attach_flash(out, q)
    hs_names = data.get("hs") or {}
    gcountry = get_groups_country(data, q.get("metrics") or ()) if any(k == "mti" and c.startswith(tc.GROUP_PREFIX) for k, c in items) else None
    mti = get_mti() if any(k == "mti" and not (gcountry and c.startswith(tc.GROUP_PREFIX) and c[len(tc.GROUP_PREFIX):] in gcountry) for k, c in items) else None
    for ikind, code in items:  # 품목은 국가를 정하지 않고 한 번 받아 두고, 지역은 그 행에서 거른다(호출 수가 지역 수와 무관)
        skipped = []
        gname = code[len(tc.GROUP_PREFIX):] if ikind == "mti" and code.startswith(tc.GROUP_PREFIX) else None
        if gname and gcountry and gname in gcountry:  # 20대 품목: 미리 모아 둔 품목×국가×월 자료로 바로 (관세청 호출 없음, 중량·HS 내역은 없음)
            for reg in (regions or [None]):
                if reg:
                    rkind, rname, codes = reg
                    arrays = tc.sum_arrays(gcountry[gname], codes, months, tc.ZONE_WINDOW.get(rname) if rkind == "경제권" else None)
                else:
                    arrays = tc.sum_arrays(gcountry[gname], list(gcountry[gname]), months)
                stored(gname + (f" × {reg[1]}" if reg else ""), arrays if arrays[0] else ([None] * len(months), [None] * len(months)), ("품목", gname) if not reg else None)
            continue
        if ikind == "mti":
            rows, skipped = trade_mti_query(code, None, s_ym, e_ym)
            prev_rows = trade_mti_query(code, None, p_s, p_e)[0] if q["compare"] else None
            label = tc.mti_label(mti, code)
            group = code[len(tc.GROUP_PREFIX):] if code.startswith(tc.GROUP_PREFIX) else None
        else:
            rows = trade_hs_rows(code, s_ym, e_ym)
            prev_rows = trade_hs_rows(code, p_s, p_e) if q["compare"] else None
            if len(code) > 6:  # 8·10자리는 관세청 품목명(행에 있는 이름)을 붙인다
                label = f"{code} {next((r['name'] for r in rows if r['hs'] == code), '')}".strip()
            else:
                label = f"{code} {hs_names.get(code[:4], '') if len(code) == 4 else ''}".strip()
            group = None
        for reg in (regions or [None]):
            if reg:
                rkind, rname, codes = reg
                since = tc.ZONE_WINDOW.get(rname) if rkind == "경제권" else None
                cur_rows = tc.filter_rows(rows, codes, since)
                p_rows = tc.filter_rows(prev_rows, codes, since) if prev_rows is not None else None
            else:
                cur_rows, p_rows = rows, prev_rows
            out.append({"name": label + (f" × {reg[1]}" if reg else ""), "month": tc.metrics_by_month(cur_rows),
                        "prev": trade_shift_year(tc.metrics_by_month(p_rows)) if p_rows is not None else None, "rows": cur_rows,
                        "skipped": skipped, "mti_code": code if ikind == "mti" else None, "hs_code": code if ikind == "hs" else None,
                        "flash_key": ("품목", group) if group and not reg else None})
    return trade_attach_flash(out, q)


def trade_attach_flash(out, q):
    """잠정 달을 고른 조회면, 보도자료에 있는 항목(20대 주력품목·주요 국가·경제권)에 잠정치를 붙인다"""
    fm = q.get("flash")
    if fm and q["end_sel"] >= fm["month"]:
        for sr in out:
            key = sr.get("flash_key")
            sr["flash"] = tc.flash_metrics(fm, *key) if key else None
    return out


def render_trade_search_result(data, q, series):
    METRIC_KEYS = {"수출금액": "exp", "수입금액": "imp", "무역수지": "bal", "수출 중량": "exp_wgt", "수입 중량": "imp_wgt"}
    wanted = [m for m in q["metrics"] if m in METRIC_KEYS]
    if not wanted:
        wanted = ["수출금액"]
    flash_ym = q["flash"]["month"] if q.get("flash") and q["end"] >= q["flash"]["month"] and any(sr.get("flash") for sr in series) else None
    months_all = sorted({ym for sr in series for ym in sr["month"]} | ({flash_ym} if flash_ym else set()))
    if not months_all:
        st.info("이 조건으로 조회된 자료가 없어요. 기간이나 품목을 바꿔 보세요.")
        return
    uc, bc = st.columns(2)
    agg = uc.radio("집계", tc.AGG_UNITS, horizontal=True, key="tr_s_unit")
    base_opts = {"월별": ["전년 동월", "전월"], "분기별": ["전년 동분기", "전분기"], "연도별": ["전년 동기", "전년"]}[agg]
    base_label = bc.radio("증감 기준", base_opts, horizontal=True, key=f"tr_s_base_{agg}")
    base = "yoy" if base_label == base_opts[0] else "mom"
    st.markdown(f"##### {agg} 추이 · {q['start'].replace('-', '.')} ~ {q['end'].replace('-', '.')} · {' · '.join(wanted)}")
    short = {"수출금액": "수출", "수입금액": "수입", "무역수지": "무역수지", "수출 중량": "수출 중량", "수입 중량": "수입 중량"}
    if agg == "월별":
        view_series, keys, labels, prov_keys, trend_flash, prev_fn = series, months_all, None, set(), flash_ym, None
    else:
        view_series, keys, labels, prov_keys = tc.aggregate_series(series, months_all, agg, flash_ym)
        trend_flash, prev_fn = None, (lambda k, _u=agg: tc.prev_period_key(k, _u))
    per_metric = {}  # 지표 -> (rows, total)
    for m in wanted:
        per_metric[m] = tc.trend_rows(view_series, keys, METRIC_KEYS[m], base, trend_flash, prev_fn)
    series = view_series
    first_col = "월" if agg == "월별" else "기간"

    def fmt_val(m, v):
        if v is None:
            return "-"
        return trade_ton(v) if METRIC_KEYS[m].endswith("wgt") else trade_usd(v)

    def fmt_chg(m, v):
        if v is None:
            return "-"
        return f"{v / 1e8:+,.1f}억달러" if METRIC_KEYS[m] == "bal" else px_pct(v)

    def col_name(sr, m):
        if len(wanted) == 1:
            return sr["name"]
        return short[m] if len(series) == 1 else f"{sr['name']} · {short[m]}"

    table_rows = []
    first_rows = per_metric[wanted[0]][0]
    for r_i, r0 in enumerate(first_rows):
        row = {first_col: (r0["ym"].replace("-", ".") + (" (잠정)" if r0["prov"] else "")) if labels is None else labels[r0["ym"]] + (" (잠정 포함)" if r0["ym"] in prov_keys else "")}
        for k, sr in enumerate(series):
            for m in wanted:
                v, chg = per_metric[m][0][r_i]["cells"][k]
                row[col_name(sr, m)] = fmt_val(m, v)
                row[col_name(sr, m) + " 증감"] = fmt_chg(m, chg)
        table_rows.append(row)
    row = {first_col: "합계"}
    for k, sr in enumerate(series):
        for m in wanted:
            v, chg = per_metric[m][1]["cells"][k]
            row[col_name(sr, m)] = fmt_val(m, v)
            row[col_name(sr, m) + " 증감"] = fmt_chg(m, chg)
    table_rows.append(row)
    table = pd.DataFrame(table_rows)
    chg_cols = [c for c in table.columns if c.endswith(" 증감")]
    st.dataframe(trade_styled(table, chg_cols), hide_index=True, height=min(len(table) + 1, 16) * 35 + 3)
    unit_word = {"월별": "달", "분기별": "분기", "연도별": "해"}[agg]
    notes = [f"증감은 {('전년 같은 ' + unit_word + ' 대비') if base == 'yoy' else ('바로 앞 ' + unit_word + ' 대비')}예요" + ("(합계는 전년 같은 기간 대비)." if base == "yoy" else "(합계 증감은 전년 기준에서만).") + (" 무역수지의 증감은 증감액이에요." if "무역수지" in wanted else "")
             + " 보고 싶지 않은 지표는 위 '지표' 칸에서 빼면 돼요."]
    if labels is not None and any("(" in v for v in labels.values()):
        notes.append("일부 달만 지난 기간은 '(1~9월 누계)'처럼 범위를 적고, 전년 같은 달들끼리 비교해요.")
    if flash_ym:
        notes.append(f"'잠정'은 산업부 {flash_ym.replace('-', '.')} 수출입 동향 보도자료(백만 달러 단위) 숫자예요. 관세청 확정치가 올라오면(보통 15일 전후) 자동으로 확정치로 바뀌어요. 보도자료에 없는 항목은 '-'로 나와요.")
    if q["compare"] is False and base == "yoy":
        notes.append("'전년 같은 기간과 비교'를 꺼 두면 전년 동월 증감이 나오지 않아요.")
    st.caption(" ".join(notes))
    pick = st.radio("그래프 지표", wanted, horizontal=True, key="tr_s_metric") if len(wanted) > 1 else wanted[0]
    key = METRIC_KEYS[pick]
    is_wgt = key.endswith("wgt")
    rows = per_metric[pick][0]
    idx = pd.to_datetime([tc.period_start(k, agg) for k in keys], format="%Y-%m-%d")
    scale, unit = (1e3, "톤") if is_wgt else (1e6, "백만달러")
    px_line_chart(px_long({sr["name"]: pd.Series([c[0] / scale if c[0] is not None else None for c in [r["cells"][k] for r in rows]], index=idx).dropna() for k, sr in enumerate(series)}),
                  unit, fmt="%Y-%m", axis_fmt="%Y" if agg == "연도별" else "%y.%m")
    if key != "bal" and any(c[1] is not None for r in rows for c in r["cells"]):
        st.markdown(f"**{short[pick]} 증감률 추이 (%, {base_label} 대비)**")
        px_line_chart(px_long({sr["name"]: pd.Series([c[1] if c[1] is not None else None for c in [r["cells"][k] for r in rows]], index=idx).dropna() for k, sr in enumerate(series)}),
                      "%", fmt="%Y-%m", axis_fmt="%Y" if agg == "연도별" else "%y.%m")
    # 산업부 품목(MTI): 하위 MTI별·국가별·하위 HS 품목별 내역
    for sr in series:
        if sr.get("skipped"):
            st.caption(f"{sr['name']}: 연계표에 이 코드가 없는 기간({', '.join(sr['skipped'])})은 빠졌어요.")
        if sr["rows"] and sr.get("mti_code"):
            mti, code = get_mti(), sr["mti_code"]
            hs4 = sorted({r["hs"][:4] for r in sr["rows"]})
            with st.expander(f"{sr['name']} · 하위 MTI·국가·HS 품목별 내역"):
                st.caption("묶인 HS 4자리: " + ", ".join(hs4[:20]) + (" …" if len(hs4) > 20 else "") + " (HS 10자리 중 이 MTI에 연결된 것만 합산)")
                lvl = tc.mti_group_level(code)
                c1, c2, c3 = st.columns(3)
                parts = []
                if lvl:
                    parts.append((c1, "하위 MTI별 (상위 15)", "MTI", [{"MTI": f"{r['code']} {r['name']}", "수출": trade_usd(r["exp"]), "수입": trade_usd(r["imp"])} for r in tc.group_mti(sr["rows"], mti, lvl)[:15]]))
                parts.append((c2, "국가별 (상위 15)", "국가", [{"국가": r["name"], "수출": trade_usd(r["exp"]), "수입": trade_usd(r["imp"])} for r in tc.group_rows(sr["rows"], "cnty")[:15]]))
                parts.append((c3, "HS 품목별 (상위 15)", "품목", [{"품목": f"{r['code']} {r['name'][:22]}", "수출": trade_usd(r["exp"]), "수입": trade_usd(r["imp"])} for r in tc.group_rows(sr["rows"], "hs")[:15]]))
                for col, title, _label, rr in parts:
                    col.markdown(f"**{title}**")
                    col.dataframe(pd.DataFrame(rr), hide_index=True, height=min(len(rr) + 1, 12) * 35 + 3)
    # 품목 조회: 국가별·하위 품목별 내역
    for sr in series:
        if sr["rows"] and not sr.get("mti_code"):
            with st.expander(f"{sr['name']} · 국가별·하위 품목별 내역"):
                c1, c2 = st.columns(2)
                for col, key2, title, label in ((c1, "cnty", "국가별 (상위 15)", "국가"), (c2, "hs", "하위 품목별 (상위 15)", "품목")):
                    rr = tc.group_rows(sr["rows"], key2)[:15]
                    col.markdown(f"**{title}**")
                    col.dataframe(pd.DataFrame([{label: (r["name"] if key2 == "cnty" else f"{r['code']} {r['name'][:24]}"), "수출": trade_usd(r["exp"]), "수입": trade_usd(r["imp"])} for r in rr]),
                                  hide_index=True, height=min(len(rr) + 1, 12) * 35 + 3)
    for sr in series:
        if sr.get("hs_code") in HS_NOTES:
            st.info(f"{sr['name']} — {HS_NOTES[sr['hs_code']]}")
    st.download_button("월별 표 내려받기 (CSV)", table.to_csv(index=False).encode("utf-8-sig"), file_name="수출입검색_월별.csv", mime="text/csv", key="tr_s_dl_m")


TRADE_AI_SYSTEM = (
    "너는 한국 수출입 무역통계(관세청·산업통상부)에서 품목 코드와 검색 조건을 찾아 주는 도우미다. "
    "코드는 사용자가 준 후보 목록과 네가 확실히 아는 것만 쓰고, 모르면 만들지 말고 confidence를 '낮음'으로 한다. "
    "지역(국가·경제권·대륙)과 산업부 20대 품목은 반드시 주어진 목록의 이름을 그대로 쓴다. "
    "참고 기사는 제목만 있으니 코드의 근거로 인용할 때는 번호(refs)만 적고, 기사에 없는 내용을 지어내지 않는다. "
    "설명 없이 JSON 하나만 출력한다."
)
TRADE_AI_LINKS = [
    ("관세법령정보포털 (HS 품목분류·관세율표 검색)", "https://unipass.customs.go.kr/clip/index.do"),
    ("관세청 무역통계", "https://tradedata.go.kr"),
    ("한국무역협회 무역통계 K-stat", "https://stat.kita.net"),
    ("산업통상부 보도자료 (월간 수출입 동향)", "https://www.motie.go.kr/kor/article/ATCL3f49a5a8c"),
]


def trade_ai_context(data, mti):
    """AI 답을 검증할 때 쓰는 목록 (국가 이름 -> 코드, 경제권, 대륙, 20대 품목, MTI·HS 이름)"""
    return {"groups": list(tc.MTI_GROUPS), "mti_names": mti["names"], "hs_names": data.get("hs") or {},
            "countries": {v[0]: k for k, v in data["country"].items()}, "zones": list(tc.ZONES), "continents": list(tc.CONTINENTS),
            "months": data["months"], "flash_ym": (trade_flash(data) or {}).get("month")}


def trade_ai_prompt(text, ctx, cands, articles):
    mti_c, hs_c = cands
    lines = [
        f"오늘: {TODAY.isoformat()} / 모아 둔 월별 자료: {ctx['months'][0]} ~ {ctx['months'][-1]}" + (f" (잠정 {ctx['flash_ym']} 포함 가능)" if ctx["flash_ym"] else ""),
        f"사용자 입력: {text}",
        "",
        "[산업부 20대 주력 수출품목 이름 — kind=group에 이 이름 그대로] " + ", ".join(ctx["groups"]),
        "[경제권 이름] " + ", ".join(ctx["zones"]),
        "[대륙 이름] " + ", ".join(ctx["continents"]),
        "[국가 이름(관세청 표기) — kind=국가에 이 이름 그대로] " + ", ".join(sorted(ctx["countries"])),
        "[입력과 이름이 겹치는 MTI(산업부 품목분류) 후보 — 코드: 이름] " + ("; ".join(f"{c}: {n}" for c, n in mti_c) or "없음"),
        "[입력과 이름이 겹치는 HS 4자리 후보] " + ("; ".join(f"{c}: {n}" for c, n in hs_c) or "없음"),
        "[관세청 등으로 확인된 코드 설명] " + (" / ".join(f"{c}: {t}" for c, t in HS_NOTES.items()) or "없음"),
        "[참고 기사(번호. 제목 - 언론사)] " + (" | ".join(f"{i}. {a['title']} - {a['publisher']}" for i, a in enumerate(articles, 1)) or "없음"),
        "",
        "아래 JSON 형식으로만 답해라.",
        '{"summary": "입력을 한 문장으로 해석", ',
        ' "items": [{"kind": "group|mti|hs", "code": "group은 20대 품목 이름, mti는 MTI 코드(1~6자리 숫자), hs는 HS 코드(2·4·6자리 숫자)", '
        '"reason": "이 코드를 고른 이유와 한계를 한두 문장으로", "confidence": "높음|중간|낮음", "refs": [참고 기사 번호들, 없으면 빈 배열]}], ',
        ' "regions": [{"kind": "국가|경제권|대륙", "name": "목록의 이름 그대로"}], ',
        ' "period": {"start": "YYYY-MM", "end": "YYYY-MM"} 또는 입력에 기간이 없으면 null, ',
        ' "metrics": ["수출금액", "수입금액", "무역수지", "수출 중량", "수입 중량" 중 입력이 원하는 것] 또는 입력에 없으면 null, ',
        ' "view": "trend|rank|composition", "rank_level": "국가|경제권|대륙", "top": 5|10|20|50, ',
        ' "notes": "코드가 정확하지 않은 이유나 확인할 점(예: 전용 코드가 없어 여러 코드의 합으로 근사)"}',
        "",
        "규칙: ① 입력이 20대 품목 중 하나와 같은 뜻이면 kind=group을 쓴다. ② 그 밖의 품목은 HS를 가능한 한 구체적으로(6자리, 용도로 가려지면 한국 HSK 8·10자리까지) 제안하고, 4자리 호만 쓰는 것은 6자리를 모를 때뿐이다. "
        "후보 목록에 있는 것을 우선하고, 한 품목에 코드가 여러 개 필요하면 모두 items에 넣는다(최대 6개). '관세청 등으로 확인된 코드 설명'에 있는 코드가 입력에 맞으면 notes에만 적지 말고 items에 그 코드(10자리)를 넣는다. "
        "③ '2025년'처럼 연도만 있으면 그 해 1월~12월. ④ metrics는 입력이 말한 것만: '수출된·수출액'이면 [수출금액]만, '수입된·수입액'이면 [수입금액]만, 수출량·물량이면 [수출 중량], 수입량이면 [수입 중량], 수지면 [무역수지], 수출입 모두나 언급이 없으면 null. ⑤ 입력에 나온 지역만 regions에 넣고, 없으면 빈 배열. "
        "⑥ 입력이 수출입 통계와 무관하면 items·regions를 비우고 summary에 이유를 쓴다. "
        "⑦ view: 시간 흐름·추이·금액을 물으면 trend, '품목이 어느 나라로 가장 많이 / 순위 / 상위 국가'처럼 나라별 비교를 물으면 rank(rank_level은 국가·경제권·대륙 중 묻는 단위, top은 '상위 N'의 N), "
        "'지역이 어떤 품목으로 / 품목별 / 구성 / 비중 / 주력 품목'처럼 한 지역의 품목 구성을 물으면 composition(regions 필수). 확실하지 않으면 trend.",
    ]
    return "\n".join(lines)


def trade_ai_news(text):
    """입력 낱말로 최근 기사를 찾는다(근거 참고용). 네이버 키가 없거나 실패하면 []"""
    if not (NAVER_ID and NAVER_SECRET):
        return []
    try:
        items, _, _ = search_news([text.strip()[:60]], 60, keyword="")
    except Exception:
        return []
    return sorted(items, key=lambda i: i["dt"], reverse=True)[:6]


def trade_hs_check(code, ym, hs_names):
    """AI가 낸 HS 코드를 관세청 자료에서 확인 -> 관세청 품목명(찾음) / ''(그 코드는 안 보임) / None(확인 못 함)"""
    if len(code) < 4 or not CUSTOMS_KEY:
        return None
    ym = ym.replace("-", "")
    try:
        if len(code) == 4:
            return hs_names.get(code) or ("확인됨" if trade_query(code, None, ym, ym) else "")
        rows = trade_query(code[:4] if len(code) == 6 else code[:6], None, ym, ym)
    except Exception:
        return None
    return next((r["name"] for r in rows if r["hs"].startswith(code)), "")


def trade_hs_children(code, ym, limit=6):
    """코드 바로 아래 단계(4자리 -> 6자리, 6자리 -> 10자리)의 [(코드, 이름, 최근 12개월 수출액)]를 수출액 큰 순으로"""
    if len(code) not in (4, 6) or not CUSTOMS_KEY:
        return []
    end = ym.replace("-", "")
    try:
        rows = trade_query(code, None, tc.month_add(end, -11), end)
    except Exception:
        return []
    acc = {}
    for r in rows:
        a = acc.setdefault(r["hs"], [r["name"], 0])
        a[1] += r["exp"]
    return sorted(((c, n, e) for c, (n, e) in acc.items()), key=lambda x: -x[2])[:limit]


def trade_ai_expand(code, ym):
    """HS 제안 하나의 하위 코드: 4자리면 6자리 몇 개와 그 아래 10자리, 6자리면 10자리 -> [{code, name, exp, sub: [...]}]"""
    out = []
    for c, n, e in trade_hs_children(code, ym):
        sub = [{"code": c2, "name": n2, "exp": e2, "sub": []} for c2, n2, e2 in trade_hs_children(c, ym)] if len(code) == 4 and len(out) < 3 else []
        out.append({"code": c, "name": n, "exp": e, "sub": sub})
    return out


def trade_ai_ask(text, data):
    """입력 한 줄 -> 검증된 AI 검색 조건 (같은 입력은 일주일 동안 다시 묻지 않는다)"""
    mti = get_mti()
    if mti is None:
        raise AiError("산업부 품목(MTI) 연계표를 불러오지 못했어요.")
    ctx = trade_ai_context(data, mti)

    def make():
        cands = tc.ai_local_candidates(text, mti["names"], ctx["hs_names"])
        articles = trade_ai_news(text)
        raw, _ = ai_generate(trade_ai_prompt(text, ctx, cands, articles), system=TRADE_AI_SYSTEM, json_mode=True)
        spec = parse_json(raw)
        if isinstance(spec, dict) and not spec.get("view"):
            spec["view"] = tc.ai_infer_view(text)  # AI가 보기를 안 골랐으면 질문 글에서 읽는다
        if isinstance(spec, dict) and not spec.get("metrics"):
            spec["metrics"] = tc.ai_infer_metrics(text)  # AI가 지표를 비웠으면 질문 글에서 읽는다('수입된' -> 수입금액만)
        out = tc.ai_validate(spec, ctx)
        clear = tc.ai_clear_groups(text, ctx["groups"], list(ctx["countries"]) + ctx["zones"] + ctx["continents"])
        if clear:  # 질문이 20대 품목 이름 그대로면 그것만 보여준다(AI가 덧붙인 MTI·HS 코드는 과잉 정보)
            have = {it["code"]: it for it in out["items"] if it["kind"] == "group"}
            out["items"] = [have.get(g) or {"kind": "group", "code": g, "key": ("mti", tc.GROUP_PREFIX + g), "label": f"{g} (산업부 20대 품목)",
                                            "reason": "입력한 말이 산업부 20대 주력 수출품목 이름과 같아요.", "confidence": "높음", "refs": []} for g in clear]
            out["clear_group"] = True
        out["articles"] = [{"title": a["title"], "url": a["url"], "publisher": a["publisher"], "date": a["dt"].strftime("%Y.%m.%d")} for a in articles]
        out["refs"] = [r for it in out["items"] for r in it["refs"]]
        return out

    out = ai_cached(("trade_ai", text.strip(), ctx["months"][-1]), make)
    for it in out["items"]:  # 코드 확인은 저장하지 않고 그때그때(관세청 자료로)
        it["checked"] = trade_hs_check(it["code"], ctx["months"][-1], ctx["hs_names"]) if it["kind"] == "hs" else None
        it["children"] = trade_ai_expand(it["code"], ctx["months"][-1]) if it["kind"] == "hs" and len(it["code"]) in (4, 6) else []
    return out


def render_trade_ai(data):
    """AI 검색: 품목 설명이나 질문을 입력하면 코드 조합·근거·링크를 보여주고, 골라서 일반 검색과 같은 결과를 낸다"""
    if not AI_PROVIDER:
        st.info("AI 검색을 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY(Claude) 또는 GEMINI_API_KEY(Gemini)를 추가해주세요.")
        return
    st.caption("품목을 말로 설명하거나(예: 전기차) 질문하듯 쓰면(예: 2025년 미국으로 수출된 자동차 수출액) AI가 코드와 검색 조건을 제안해요. 확인하고 '조회'를 누르면 일반 검색과 같은 결과가 나와요.")
    text = st.text_area("찾고 싶은 것", key="tr_ai_text", height=80, placeholder="예: 전기차  /  2025년 미국으로 수출된 자동차 수출액  /  올해 EU 반도체 수입")
    if st.button("AI에게 물어보기", type="primary", key="tr_ai_go"):
        st.session_state.pop("tr_ai_query", None)
        st.session_state["tr_ai_n"] = st.session_state.get("tr_ai_n", 0) + 1  # 새로 물을 때마다 체크 상태를 새로 시작
        if not text.strip():
            st.warning("찾고 싶은 것을 입력해 주세요.")
            st.session_state.pop("tr_ai_spec", None)
        else:
            try:
                with st.spinner("AI가 코드와 조건을 찾는 중입니다... (몇 초~수십 초)"):
                    st.session_state["tr_ai_spec"] = trade_ai_ask(text, data)
            except AiError as e:
                st.session_state.pop("tr_ai_spec", None)
                st.error(str(e))
                return
    spec = st.session_state.get("tr_ai_spec")
    if not spec:
        return
    st.warning("AI가 제안한 코드와 조건이에요. 틀릴 수 있으니 아래 근거·관세청 품목명을 확인하고, 중요한 숫자는 관세청·산업부 자료로 다시 확인하세요.", icon="⚠️")
    if spec["summary"]:
        st.markdown(f"**AI가 이해한 내용** · {spec['summary']}")
    arts = spec.get("articles") or []
    picked = []
    if spec["items"]:
        st.markdown("**품목 코드 제안** (체크한 코드로 조회해요)")
        for i, it in enumerate(spec["items"]):
            checked = it["checked"]
            tag = {"높음": "🟢 높음", "중간": "🟡 중간", "낮음": "🔴 낮음"}[it["confidence"]]
            noted = [n["code"] for ch in it.get("children", []) for n in [ch] + ch["sub"] if n["code"] in HS_NOTES]  # 관세청이 확인해 준 하위 코드가 있으면 그걸 기본으로 고른다
            if st.checkbox(f"{it['label']}  ·  확신 {tag}", value=it["confidence"] != "낮음" and not noted, key=f"tr_ai_item_{st.session_state.get('tr_ai_n', 0)}_{i}"):
                picked.append(it["key"])
            bits = [it["reason"]] if it["reason"] else []
            if checked:
                bits.append(f"관세청 품목명: {checked}")
            elif checked == "":
                bits.append("⚠️ 관세청 자료에서 이 코드가 확인되지 않았어요")
            ref_links = [f"[기사{r}]({arts[r - 1]['url']})" for r in it["refs"] if 1 <= r <= len(arts)]
            if ref_links:
                bits.append("근거 기사 " + " ".join(ref_links))
            if it["code"] in HS_NOTES:
                bits.append(HS_NOTES[it["code"]])
            if bits:
                st.caption(" · ".join(bits))
            if it.get("children"):
                with st.expander(f"하위 코드 보기 ({len(it['children'])}개 · 최근 12개월 수출액, 더 세부로 보려면 체크)", expanded=bool(noted)):
                    for ch in it["children"]:
                        for node, depth in [(ch, 1)] + [(sb, 2) for sb in ch["sub"]]:
                            mark = "↳ " if depth == 1 else "　　↳ "
                            if st.checkbox(f"{mark}{node['code']} {node['name'][:26]} · {trade_usd(node['exp'])}", value=node["code"] in HS_NOTES,
                                           key=f"tr_ai_ch_{st.session_state.get('tr_ai_n', 0)}_{i}_{node['code']}"):
                                picked.append(("hs", node["code"]))
        if spec.get("clear_group"):
            st.caption("20대 품목 이름과 같은 말이라 그 품목만 보여드려요. 더 세부 코드(HS·MTI)가 필요하면 구체적으로 적어 주세요(예: 전기차, 승용차 8703, 3102109000).")
    else:
        st.caption("품목은 지정하지 않았어요 (전체).")
    reg = " · ".join(f"{n}" for _, n, _ in spec["regions"]) or "전체"
    st.caption(f"지역: {reg}  |  기간: {spec['start'].replace('-', '.')} ~ {spec['end'].replace('-', '.')}  |  지표: {', '.join(spec['metrics'])}")
    if spec["notes"]:
        st.caption("메모: " + spec["notes"])
    for w in spec["warnings"]:
        st.caption("• " + w)
    with st.expander("참고 기사·사이트"):
        if arts:
            st.markdown("\n".join(f"{i}. [{a['title']}]({a['url']}) · {a['publisher']} · {a['date']}" for i, a in enumerate(arts, 1)))
        else:
            st.caption("관련 기사를 찾지 못했어요(네이버 뉴스 키가 없거나 결과가 없음).")
        st.markdown("\n".join(f"- [{t}]({u})" for t, u in TRADE_AI_LINKS))
    view = st.radio("보기", tc.AI_VIEWS, horizontal=True, index=tc.AI_VIEWS.index(spec.get("view", "월별 추이")), key=f"tr_ai_view_{st.session_state.get('tr_ai_n', 0)}",
                    help="AI가 질문에 맞게 골랐어요. 바꾸면 같은 조건으로 다른 보기가 나와요. 월별 추이=시간 흐름, 국가별 순위=품목이 어느 나라로, 품목별 구성=지역이 어떤 품목으로.")
    if st.button("조회", type="primary", key="tr_ai_run"):
        picked = list(dict.fromkeys(picked))
        metric = spec["metrics"][0]
        problems = []
        if spec["items"] and not picked:
            problems.append("조회할 품목 코드를 하나 이상 체크해 주세요.")
        if view == "월별 추이" and len(picked) * max(1, len(spec["regions"])) > TRADE_MAX_SERIES:
            problems.append(f"한 번에 {TRADE_MAX_SERIES}개 항목까지만 볼 수 있어요 (지금 {len(picked) * max(1, len(spec['regions']))}개). 체크를 줄여 주세요.")
        if view == "국가별 순위" and len(picked) > 6:
            problems.append("국가별 순위는 품목을 6개까지 볼 수 있어요.")
        if view == "품목별 구성" and not spec["regions"]:
            problems.append("품목별 구성은 지역(국가·경제권·대륙)이 필요해요. 질문에 지역을 넣어 다시 물어봐 주세요(예: 미국으로 수출된 품목).")
        group_items = [it for it in picked if it[0] == "mti" and str(it[1]).startswith(tc.GROUP_PREFIX)]
        # 20대 품목만 고르고 금액 지표면 미리 모아 둔 자료로 바로 보여줘서(관세청 호출 없음) 인증키가 없어도 된다
        from_groups = bool(picked) and len(group_items) == len(picked) and get_groups_country(data, spec["metrics"] if view != "국가별 순위" else [metric]) is not None
        comp_20 = view == "품목별 구성" and (bool(group_items) or any(w in text for w in ("20대", "주력")))
        if comp_20 and any("중량" in m for m in spec["metrics"]):
            comp_20 = False  # 중량은 모아 둔 자료에 없어 HS 기준으로
        comp_20 = comp_20 and get_groups_country(data) is not None
        if (picked or any("중량" in m for m in spec["metrics"])) and view != "품목별 구성" and not CUSTOMS_KEY and not from_groups:
            problems.append("품목 조회에는 Streamlit Secrets에 CUSTOMS_API_KEY가 필요해요.")
        if view == "품목별 구성" and not CUSTOMS_KEY and not comp_20:
            problems.append("품목별 구성에는 Streamlit Secrets에 CUSTOMS_API_KEY가 필요해요.")
        if problems:
            st.warning(" ".join(problems))
            st.session_state.pop("tr_ai_query", None)
        elif view == "월별 추이":
            st.session_state["tr_ai_query"] = {"view": view, "kind": "일반 검색", "items": picked, "regions": spec["regions"], "metrics": spec["metrics"], "compare": True,
                                               "start": spec["start"], "end": spec["end"], "flash": trade_flash(data)}
        elif view == "국가별 순위":
            if not picked and "중량" in metric:
                metric = "수출금액"
            st.session_state["tr_ai_query"] = {"view": view, "items": picked, "level": spec.get("rank_level", "국가"), "start": spec["start"], "end": min(spec["end"], data["months"][-1]),
                                               "metric": metric, "top": str(spec.get("top", 10)), "compare": True}
        else:
            start, end = spec["start"], min(spec["end"], data["months"][-1])
            if not comp_20 and tc.month_span(start.replace("-", ""), end.replace("-", "")) > 36:
                t = tc.month_add(end.replace("-", ""), -35)
                start = f"{t[:4]}-{t[4:]}"  # 품목별 구성은 최대 3년
                st.info("품목별 구성은 최대 3년까지라 기간을 최근 3년으로 줄였어요.")
            codes = []
            for _kind, name, cc in spec["regions"]:
                win = tc.ZONE_WINDOW.get(name) if _kind == "경제권" else None
                codes += [(c, (win or {}).get(c, (None, None))[0] or "", (win or {}).get(c, (None, None))[1] or "") for c in cc]
            codes = tuple(dict.fromkeys(codes))
            calls = 0 if comp_20 else trade_hs_calls(codes, start, end) * 2
            if calls > 150:
                st.warning(f"조회할 범위가 너무 커요(관세청 호출 약 {calls}번). 지역을 줄여 다시 물어봐 주세요.")
                st.session_state.pop("tr_ai_query", None)
            else:
                st.session_state["tr_ai_query"] = {"view": view, "parts": codes, "label": " + ".join(n for _, n, _ in spec["regions"]), "start": start, "end": end,
                                                   "metric": metric, "top": str(spec.get("top", 10)), "compare": True, "basis": "20대" if comp_20 else "HS"}
                st.session_state["tr_ai_path"] = ""
    sq = st.session_state.get("tr_ai_query")
    if not sq:
        st.caption("코드를 확인하고 '조회'를 누르면 선택한 보기로 결과가 나와요.")
        return
    if sq.get("view") == "국가별 순위":
        with st.container(border=True):
            render_trade_rank_result(data, sq)
        return
    if sq.get("view") == "품목별 구성":
        with st.container(border=True):
            render_trade_composition_result(data, sq, "tr_ai_")
        return
    try:
        with st.spinner("관세청 자료를 불러오는 중입니다..."):
            series = trade_series(data, sq)
    except tc.TradeApiError as e:
        st.error(str(e))
        return
    with st.container(border=True):
        render_trade_search_result(data, sq, series)


TRADE_PERIOD_PRESETS = ["올해", "1년", "3년", "직접 선택"]


def trade_period_pick(data, pre, fm=None):
    """기간 고르기(빠른 선택 또는 직접). fm: 산업부 잠정 달 자료(있으면 마지막 달 선택지에 '잠정'이 더해짐) -> (시작월, 종료월)"""
    months = data["months"]
    end_opts = months + ([fm["month"]] if fm else [])
    last = end_opts[-1]
    ly = int(last[:4])
    mode = st.radio("기간", TRADE_PERIOD_PRESETS, horizontal=True, key=f"{pre}period",
                    help="'올해'는 올해 1월부터 가장 최근 달까지, '1년'은 최근 12개월이에요. '3년'은 2년 전 1월부터 시작해요. 그 밖의 기간은 '직접 선택'에서 고르세요(모아 둔 자료는 최근 10년이에요).")
    fixed = {"올해": (f"{ly}-01", last), "1년": (tc._dash_add(last, -11), last), "3년": (f"{ly - 2}-01", last)}
    if mode != "직접 선택":
        start, end = fixed[mode]
        start, end = max(start, months[0]), min(end, last)
        if start > end:
            st.warning("모아 둔 자료 기간 밖이에요. 다른 기간을 골라 주세요.")
        return start, end
    default_start = next((m for m in months if m.startswith(months[-1][:4])), months[0])
    c1, c2 = st.columns(2)
    start = c1.selectbox("시작월", months, index=months.index(default_start), key=f"{pre}start", format_func=lambda m: m.replace("-", "."))
    end = c2.selectbox("종료월", end_opts, index=len(end_opts) - 1, key=f"{pre}end",
                       format_func=lambda m: m.replace("-", ".") + (" (잠정)" if fm and m == fm["month"] else ""))
    return start, end


def trade_item_picker(data, pre):
    """품목 고르기(전체·20대 품목·HS 코드·MTI 코드). 위젯 키는 pre(접두어)로 구분한다. -> (품목 목록, 품목을 골랐는지, MTI 연계표) 또는 쓸 수 없으면 None"""
    items, mti = [], None
    item_mode = st.radio("품목", TRADE_ITEM_MODES, horizontal=True, key=f"{pre}item",
                         help="'전체'는 품목을 가리지 않아요. '20대 품목'은 산업부 월간 수출입 동향 보도자료의 20대 주력 수출품목(산업부 MTI 기준)이에요.")
    live = item_mode != "전체"
    if live and not CUSTOMS_KEY:
        st.info("품목 조회에는 Streamlit Secrets에 CUSTOMS_API_KEY(공공데이터포털 인증키)가 필요해요. 추가하면 바로 쓸 수 있어요. (품목을 '전체'로 두면 지금도 지역별로 볼 수 있어요.)")
        return None
    if live and item_mode != "HS코드":
        mti = get_mti()
        if mti is None:
            st.info("산업부 품목(MTI) 연계표(mti_map.json)를 불러오지 못했어요.")
            return None
    if item_mode == "20대":
        st.caption("보고 싶은 품목을 눌러 선택하세요 (여러 개 가능).")
        picked = st.pills("20대 품목", list(tc.MTI_GROUPS), selection_mode="multi", key=f"{pre}groups", label_visibility="collapsed")
        items += [("mti", tc.GROUP_PREFIX + g) for g in (picked or [])]
    if item_mode == "MTI코드":
        opts = {f"{c} {n}": c for c, n in sorted(mti["names"].items())}
        picked = st.multiselect("MTI 품목 — 이름이나 코드(1~6자리)를 입력해 검색", list(opts), key=f"{pre}mti", max_selections=6,
                                placeholder="예: 반도체, 8312, 831110")
        items += [("mti", opts[p]) for p in picked]
        st.caption("산업부(MOTIE)가 발표에 쓰는 품목 분류예요. 같은 이름이라도 HS 코드 하나가 아니라 여러 HS 코드를 묶은 것이에요(예: 반도체 = 집적회로·반도체 소자·웨이퍼 등). 자릿수가 짧을수록 넓은 묶음이에요.")
    if item_mode == "HS코드":
        opts = trade_hs_options(data)
        picked = st.multiselect("HS 품목 — 이름이나 코드로 검색 (목록에 없는 코드는 입력하고 엔터, 2·4·6·8·10자리)", list(opts), key=f"{pre}hs", max_selections=6,
                                accept_new_options=True, placeholder="예: 전자집적회로, 8542, 854231, 3102109000")
        codes = [opts.get(p) or (p.strip().replace(".", "").replace("-", "") if p.strip().replace(".", "").replace("-", "").isdigit()
                                 and len(p.strip().replace(".", "").replace("-", "")) in (2, 4, 6, 8, 10) else None) for p in picked]
        if any(c is None for c in codes):
            st.caption("HS 코드는 숫자 2·4·6·8·10자리로 입력해 주세요. 맞지 않는 입력은 빼고 조회해요.")
        items += [("hs", c) for c in dict.fromkeys(c for c in codes if c)]
        st.caption("HS 코드는 관세청 분류예요. 산업부 발표 기준(반도체·자동차 등)으로 보려면 '20대'나 'MTI코드'를 쓰세요.")
    return items, live, mti


def trade_rank_metrics(live):
    return ["수출금액", "수입금액", "무역수지"] + (["수출 중량", "수입 중량"] if live else [])


def render_trade_rank_table(title, entries, metric, top_n, denom, level, compare, key, with_other=None):
    """순위 표·막대 그래프 하나 (entries: tc.rank_entries_* 결과). metric: 지표 이름 하나 또는 목록(여러 개면 한 표에 지표별 칸, denom은 {지표키: 값})"""
    if isinstance(metric, (list, tuple)):
        if len(metric) > 1:
            return render_trade_multi_table(title, entries, list(metric), top_n, compare, key, level=level, denom=denom,
                                            with_other=(level == "국가") if with_other is None else with_other)
        metric = metric[0]
        denom = (denom or {}).get(tc.RANK_METRICS[metric]) if isinstance(denom, dict) else denom
    mk = tc.RANK_METRICS[metric]
    rows, tot = tc.rank_table(entries, mk, top_n, denom=denom, with_other=(level == "국가") if with_other is None else with_other)
    if not rows:
        st.info("이 조건으로 조회된 자료가 없어요. 기간이나 품목을 바꿔 보세요.")
        return
    is_wgt = mk.endswith("wgt")
    fmt = trade_ton if is_wgt else trade_usd
    short = {"수출금액": "수출", "수입금액": "수입", "무역수지": "무역수지", "수출 중량": "수출 중량", "수입 중량": "수입 중량"}[metric]
    has_prev = any(r["prev"] is not None for r in rows)
    table_rows = []
    for r in rows:
        row = {"순위": "" if r["rank"] is None else r["rank"], level: r["name"], short: fmt(r["value"]),
               "점유율": "-" if r["share"] is None else f"{r['share']:.1f}%"}
        if compare:
            row["전년 같은 기간"] = fmt(r["prev"]) if r["prev"] is not None else "-"
            row["증감"] = px_pct(r["chg"]) if mk != "bal" else ("-" if r["prev"] is None or r["value"] is None else f"{(r['value'] - r['prev']) / 1e8:+,.1f}억달러")
        table_rows.append(row)
    total_row = {"순위": "", level: "합계" if level in ("국가", "품목") else "(전체)", short: fmt(denom if denom and level not in ("국가", "품목") else tot["value"]), "점유율": "100.0%" if mk != "bal" else "-"}
    if compare:
        total_row["전년 같은 기간"] = fmt(tot["prev"]) if tot["prev"] is not None else "-"
        total_row["증감"] = px_pct(tot["chg"]) if tot["chg"] is not None else "-"
    table_rows.append(total_row)
    table = pd.DataFrame(table_rows)
    st.markdown(f"**{title}**")
    st.dataframe(trade_styled(table, ["증감"]) if compare else table, hide_index=True, height=min(len(table) + 1, 16) * 35 + 3)
    if compare and not has_prev:
        st.caption("전년 같은 기간 자료가 모아 둔 기간 밖이라 비교는 비어 있어요.")
    if level in ("경제권", "대륙"):
        st.caption("경제권은 서로 겹칠 수 있어요(예: ASEAN은 APEC에도 들어가요)." if level == "경제권" else "대륙은 관세청 분류예요(예: 북아프리카는 '중동').")
    import altair as alt

    top = [r for r in rows if r["rank"] is not None]
    scale, unit = (1e6, "천톤") if is_wgt else (1e8, "억달러")
    df = pd.DataFrame({"이름": [r["name"] for r in top], "값": [(r["value"] or 0) / scale for r in top]})
    chart = alt.Chart(df).mark_bar().encode(y=alt.Y("이름:N", sort=[r["name"] for r in top], title=None), x=alt.X("값:Q", title=unit),
                                           tooltip=["이름", alt.Tooltip("값:Q", format=",.1f")]).properties(height=min(len(top) * 26 + 30, 420))
    st.altair_chart(chart, use_container_width=True)
    st.download_button("순위 표 내려받기 (CSV)", table.to_csv(index=False).encode("utf-8-sig"), file_name="수출입_순위.csv", mime="text/csv", key=f"{key}_dl")


def render_trade_multi_table(title, entries, metrics, top_n, compare, key, level="품목", denom=None, with_other=True):
    """지표 여러 개를 한 표에: 첫 지표 큰 순으로 줄을 세우고 지표마다 값(·증감) 칸. 그래프는 첫 지표. entries: tc.hs_entries·tc.rank_entries_* 결과.
    denom: {지표키: 점유율·합계 줄의 기준값}(국가·품목 단위가 아닌 경제권·대륙은 겹치는 묶음이라 전체 값을 합계로 씀). with_other: 상위 밖을 '기타'로 묶을지"""
    mks = [tc.RANK_METRICS[m] for m in metrics]
    short = {"수출금액": "수출", "수입금액": "수입", "무역수지": "무역수지", "수출 중량": "수출 중량", "수입 중량": "수입 중량"}
    first = mks[0]
    ents = sorted(entries, key=lambda e: -(e["cur"][first] or 0))
    if not ents:
        st.info("이 조건으로 조회된 자료가 없어요. 기간이나 품목을 바꿔 보세요.")
        return
    top = ents if top_n is None else ents[:top_n]
    rest = ents[len(top):]
    has_prev = all(e["prev"] is not None for e in ents)

    def cell(mk, group, which):
        vals = [e[which][mk] if e[which] is not None else None for e in group]
        return None if any(v is None for v in vals) and which == "prev" else sum(v or 0 for v in vals)

    def fmt_val(mk, v):
        return trade_ton(v) if mk.endswith("wgt") else trade_usd(v)

    def fill(row, group):
        for m, mk in zip(metrics, mks):
            cur_v, prev_v = cell(mk, group, "cur"), (cell(mk, group, "prev") if has_prev else None)
            row[short[m]] = fmt_val(mk, cur_v)
            if compare:
                if prev_v is None:
                    row[f"{short[m]} 증감"] = "-"
                elif mk == "bal":
                    row[f"{short[m]} 증감"] = f"{(cur_v - prev_v) / 1e8:+,.1f}억달러"
                else:
                    row[f"{short[m]} 증감"] = px_pct(tc.pct(cur_v, prev_v))
        return row

    denom = denom or {}
    total_first = denom.get(first) or sum(e["cur"][first] or 0 for e in ents)
    rows = []
    for i, e in enumerate(top, 1):
        row = fill({"순위": i, level: e["name"]}, [e])
        if first != "bal" and total_first:
            row["점유율"] = f"{(e['cur'][first] or 0) / total_first * 100:.1f}%"
        rows.append(row)
    if rest and with_other:
        row = fill({"순위": "", level: f"기타 ({len(rest)}곳)"}, rest)
        if first != "bal" and total_first:
            row["점유율"] = f"{sum(e['cur'][first] or 0 for e in rest) / total_first * 100:.1f}%"
        rows.append(row)
    total = fill({"순위": "", level: "합계" if level in ("국가", "품목") else "(전체)"}, ents)
    if level not in ("국가", "품목"):
        for m, mk in zip(metrics, mks):
            if denom.get(mk) is not None:
                total[short[m]] = fmt_val(mk, denom[mk])
    if first != "bal" and total_first:
        total["점유율"] = "100.0%"
    rows.append(total)
    table = pd.DataFrame(rows)
    st.markdown(f"**{title}** (순위는 {metrics[0]} 기준)")
    st.dataframe(trade_styled(table, [c for c in table.columns if c.endswith("증감")]) if compare else table, hide_index=True, height=min(len(table) + 1, 16) * 35 + 3)
    if compare and not has_prev:
        st.caption("전년 같은 기간 자료가 모아 둔 기간 밖이라 비교는 비어 있어요.")
    import altair as alt

    is_wgt = first.endswith("wgt")
    scale, unit = (1e6, "천톤") if is_wgt else (1e8, "억달러")
    df = pd.DataFrame({"이름": [e["name"] for e in top], "값": [(e["cur"][first] or 0) / scale for e in top]})
    chart = alt.Chart(df).mark_bar().encode(y=alt.Y("이름:N", sort=[e["name"] for e in top], title=None), x=alt.X("값:Q", title=f"{metrics[0]} ({unit})"),
                                           tooltip=["이름", alt.Tooltip("값:Q", format=",.1f")]).properties(height=min(len(top) * 26 + 30, 420))
    st.altair_chart(chart, use_container_width=True)
    st.download_button("표 내려받기 (CSV)", table.to_csv(index=False).encode("utf-8-sig"), file_name="품목별_구성.csv", mime="text/csv", key=f"{key}_dl")


def trade_country_hs(cnty, s_ym, e_ym):
    """한 나라의 HS 10자리 품목별 기간 합계 -> {hs10: [이름, 수출, 수입, 수출중량, 수입중량]}. 관세청은 국가만 지정하면 10자리 행을 주므로 12개월 단위로 받아 바로 합친다(캐시 없음, 스레드에서 호출)."""
    out = {}
    for s, e in tc.chunk_months(s_ym, e_ym):
        for r in tc.parse_items(trade_fetch_text({"strtYymm": s, "endYymm": e, "cntyCd": cnty})):
            a = out.setdefault(r["hs"], [r["name"], 0, 0, 0, 0])
            a[1] += r["exp"]
            a[2] += r["imp"]
            a[3] += r["exp_wgt"]
            a[4] += r["imp_wgt"]
    return out


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=8)
def trade_region_hs(parts, s_ym, e_ym):
    """지역(국가 코드들)의 HS 10자리 품목별 기간 합계. parts: ((국가코드, 시작월 또는 '', 끝월 또는 ''), ...) — 구성국 기간이 있는 나라는 그 기간만 받는다(예: 영국은 2020-12까지)."""
    jobs = []
    for code, w_from, w_to in parts:
        s = max(s_ym, w_from.replace("-", "")) if w_from else s_ym
        e = min(e_ym, w_to.replace("-", "")) if w_to else e_ym
        if s <= e:
            jobs.append((code, s, e))
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(lambda j: trade_country_hs(*j), jobs))
    return tc.merge_hs_totals(got)


def trade_hs_calls(parts, start, end):
    """품목별 구성에 필요한 관세청 호출 수(나라 수 × 12개월 구간 수)"""
    s_ym, e_ym = start.replace("-", ""), end.replace("-", "")
    n = 0
    for code, w_from, w_to in parts:
        s = max(s_ym, w_from.replace("-", "")) if w_from else s_ym
        e = min(e_ym, w_to.replace("-", "")) if w_to else e_ym
        if s <= e:
            n += len(tc.chunk_months(s, e))
    return n


def render_trade_composition(data):
    """품목별 구성: 지역(국가·경제권·대륙)이 주고받은 품목의 구성을 HS 2자리(류)부터 보여주고, 눌러서 4·6·10자리로 내려간다"""
    months = data["months"]
    st.caption("지역을 고르면 그 지역과 주고받은 품목의 구성을 큰 단위(HS 2자리 류)부터 보여주고, 아래 칸에서 골라 4·6·10자리 세부로 내려가요.")
    basis = st.radio("기준", ["HS", "20대"], horizontal=True, key="tr_c_basis",
                     help="'HS'는 HS 2자리(류)부터 4·6·10자리로 내려가며 봐요. '20대'는 산업부 20대 주력 품목별 구성이에요(미리 모아 둔 자료로 바로 나오고, 금액만 있어요).")
    reg_mode = st.radio("지역", ["국가", "경제권", "대륙"], horizontal=True, key="tr_c_reg")
    regions = []
    if reg_mode == "국가":
        order = sorted(data["country"].items(), key=lambda kv: -(sum(x or 0 for x in kv[1][1][-12:])))
        cmap = {v[0]: k for k, v in order}
        sel = st.multiselect("국가", list(cmap), key="tr_c_cnty", max_selections=5)
        regions = [("국가", n, (cmap[n],)) for n in sel]
    elif reg_mode == "경제권":
        sel = st.multiselect("경제권", [z for z in tc.ZONES if tc.zone_codes(z)], key="tr_c_zone", max_selections=3)
        regions = [("경제권", z, tuple(tc.zone_codes(z))) for z in sel]
    else:
        sel = st.multiselect("대륙", list(tc.CONTINENTS), key="tr_c_cont", max_selections=3)
        regions = [("대륙", c, tuple(tc.continent_codes(c))) for c in sel]
    start, end = trade_period_pick(data, "tr_c_")
    c3, c4 = st.columns([3, 2])
    metrics = c3.multiselect("지표", ["수출금액", "수입금액", "무역수지", "수출 중량", "수입 중량"], default=["수출금액", "수입금액", "무역수지"], key="tr_c_metrics")
    top_label = c4.selectbox("상위", ["5", "10", "20", "50", "전체"], index=1, key="tr_c_top")
    compare = st.toggle("전년 같은 기간과 비교", value=True, key="tr_c_compare")
    if st.button("조회", type="primary", key="tr_c_go"):
        problems = []
        if start > end:
            problems.append("시작월이 종료월보다 늦어요.")
        if not regions:
            problems.append(f"{reg_mode}을(를) 하나 이상 골라 주세요.")
        if not metrics:
            problems.append("지표를 하나 이상 골라 주세요.")
        if basis == "20대" and any("중량" in m for m in metrics):
            problems.append("20대 기준은 금액 지표만 볼 수 있어요(중량은 HS 기준에서).")
        if basis == "HS" and tc.month_span(start.replace("-", ""), end.replace("-", "")) > 36:
            problems.append("기간은 최대 3년(36개월)까지 볼 수 있어요.")
        codes = []
        for _kind, name, cc in regions:
            win = tc.ZONE_WINDOW.get(name) if _kind == "경제권" else None
            codes += [(c, (win or {}).get(c, (None, None))[0] or "", (win or {}).get(c, (None, None))[1] or "") for c in cc]
        codes = list(dict.fromkeys(codes))
        calls = trade_hs_calls(tuple(codes), start, end) * (2 if compare else 1) if not problems and basis == "HS" else 0
        if calls > 150:
            problems.append(f"조회할 범위가 너무 커요(관세청 호출 약 {calls}번). 지역 수나 기간을 줄여 주세요.")
        elif calls > 25:
            st.info(f"국가가 많아서 시간이 걸려요(관세청 호출 약 {calls}번, 1~3분). 같은 조회를 6시간 안에 다시 하면 바로 나와요.")
        if problems:
            st.warning(" ".join(problems))
            st.session_state.pop("tr_c_query", None)
        else:
            st.session_state["tr_c_query"] = {"parts": tuple(codes), "label": " + ".join(n for _, n, _ in regions), "start": start, "end": end, "metrics": metrics, "basis": basis,
                                              "top": top_label, "compare": compare}
            st.session_state["tr_c_path"] = ""
    cq = st.session_state.get("tr_c_query")
    if not cq:
        st.caption("지역과 기간을 고르고 '조회'를 누르면 품목 구성이 나와요.")
        return
    with st.container(border=True):
        render_trade_composition_result(data, cq, "tr_c_")


def render_trade_composition_groups(data, cq, pre):
    """품목별 구성 · 20대 기준: 지역이 주고받은 금액을 산업부 20대 주력 품목별로 (미리 모아 둔 groups 자료, 관세청 호출 없음, 금액만)"""
    months = data["months"]
    metrics = cq.get("metrics") or [cq["metric"]]
    gcountry = get_groups_country(data)
    st.markdown(f"##### {cq['label']} · 20대 품목별 구성 · {cq['start'].replace('-', '.')} ~ {cq['end'].replace('-', '.')} · {' · '.join(metrics)}")
    if not gcountry:
        st.info("20대 품목 자료(미리 모아 둔 자료)를 아직 불러오지 못했어요. 잠시 뒤 다시 시도하거나 기준을 'HS'로 바꿔 보세요.")
        return
    window = {c: (wf or None, wt or None) for c, wf, wt in cq["parts"] if wf or wt}
    codes = list(dict.fromkeys(c for c, _, _ in cq["parts"]))
    pstart, pend = tc.prev_period_bounds(cq["start"], cq["end"])
    full_prev = cq["compare"] and pstart >= months[0]
    entries = []
    for name, per in gcountry.items():
        exp, imp = tc.sum_arrays(per, codes, months, window or None)
        if not exp:
            continue
        cur = tc.stored_period(exp, imp, months, cq["start"], cq["end"])
        if not cur or not (cur["exp"] or cur["imp"]):
            continue
        entries.append({"code": name, "name": name, "cur": cur, "prev": tc.stored_period(exp, imp, months, pstart, pend) if full_prev else None})
    if not entries:
        st.info("이 조건으로 조회된 자료가 없어요. 기간이나 지역을 바꿔 보세요.")
        return
    top_n = None if cq["top"] == "전체" else int(cq["top"])
    st.caption("산업부 20대 주력 품목 기준이에요(미리 모아 둔 자료, 금액만). 점유율은 20대 품목 합계 기준이고, 20대 품목 합은 지역 전체 수출입과 같지 않아요.")
    render_trade_multi_table("20대 품목별 구성", entries, metrics, top_n, cq["compare"], f"{pre}tab", denom=None, with_other=True)


def render_trade_composition_result(data, cq, pre):
    """품목별 구성 결과(표·내려가기). cq: {parts, label, start, end, metric, top, compare}. pre: 위젯·상태 키 접두어"""
    if cq.get("basis") == "20대":
        render_trade_composition_groups(data, cq, pre)
        return
    s_ym, e_ym = cq["start"].replace("-", ""), cq["end"].replace("-", "")
    p_s, p_e = tc.month_add(s_ym, -12), tc.month_add(e_ym, -12)
    try:
        with st.spinner("관세청 자료를 불러오는 중입니다... (나라마다 몇 초씩, 큰 나라는 12개월에 10초 정도 걸려요)"):
            cur = trade_region_hs(cq["parts"], s_ym, e_ym)
            prev = trade_region_hs(cq["parts"], p_s, p_e) if cq["compare"] else None
    except tc.TradeApiError as e:
        st.error(str(e))
        return
    if not cur:
        st.info("이 조건으로 조회된 자료가 없어요. 기간이나 지역을 바꿔 보세요.")
        return
    path = st.session_state.get(f"{pre}path", "")
    entries, length = tc.hs_entries(cur, prev, path, data.get("hs") or {})
    crumbs = " › ".join(["전체"] + [f"{path[:n]}" for n in (2, 4, 6, 10) if len(path) >= n])
    metrics = cq.get("metrics") or [cq["metric"]]
    st.markdown(f"##### {cq['label']} · 품목별 구성 · {cq['start'].replace('-', '.')} ~ {cq['end'].replace('-', '.')} · {' · '.join(metrics)}")
    st.caption(f"HS {length}자리 단위 · 위치: {crumbs}")
    top_n = None if cq["top"] == "전체" else int(cq["top"])
    if len(metrics) == 1:
        render_trade_rank_table("품목별 구성" + (f" ({path} 안)" if path else ""), entries, metrics[0], top_n, None, "품목", cq["compare"], f"{pre}tab", with_other=True)
    else:
        render_trade_multi_table("품목별 구성" + (f" ({path} 안)" if path else ""), entries, metrics, top_n, cq["compare"], f"{pre}tab")
    if length == 6:
        st.caption("HS 6자리의 이름은 그 안에서 가장 큰 세부 품목의 이름으로 적었어요.")
    mk = tc.RANK_METRICS[metrics[0]]
    top_rows, _ = tc.rank_table(entries, mk, top_n or 15, None, with_other=False)
    opts = [""] + [r["name"] for r in top_rows if r["rank"] is not None]
    bc1, bc2, bc3 = st.columns([3, 1, 1])
    if length < 10 and len(opts) > 1:
        pick = bc1.selectbox("더 세부로 내려가기 (위 표의 품목을 고르세요)", opts, key=f"{pre}drill_{path}", format_func=lambda x: x or "(선택)")
        if pick:
            st.session_state[f"{pre}path"] = pick.split()[0]
            st.rerun()
    if path:
        if bc2.button("한 단계 위로", key=f"{pre}up"):
            st.session_state[f"{pre}path"] = path[:{2: 0, 4: 2, 6: 4, 10: 6}[len(path)]]
            st.rerun()
        if bc3.button("처음으로", key=f"{pre}top_btn"):
            st.session_state[f"{pre}path"] = ""
            st.rerun()


def render_trade_rank_country(data):
    """국가별 순위: 품목(전체·20대·HS·MTI)을 고르면 어느 국가(또는 경제권·대륙)로 가장 많이 나갔는지(들어왔는지) 순위·점유율·증감을 보여준다"""
    months = data["months"]
    picked = trade_item_picker(data, "tr_r_")
    if picked is None:
        return
    items, live, mti = picked
    level = st.radio("순위 단위", ["국가", "경제권", "대륙"], horizontal=True, key="tr_r_level")
    start, end = trade_period_pick(data, "tr_r_")
    c3, c4 = st.columns([3, 2])
    metrics = c3.multiselect("지표", trade_rank_metrics(live), default=["수출금액", "수입금액", "무역수지"], key=f"tr_r_metrics_{'live' if live else 'stored'}")
    top_label = c4.selectbox("상위", ["5", "10", "20", "50", "전체"], index=1, key="tr_r_top")
    compare = st.toggle("전년 같은 기간과 비교", value=True, key="tr_r_compare")
    if st.button("조회", type="primary", key="tr_r_go"):
        problems = []
        if start > end:
            problems.append("시작월이 종료월보다 늦어요.")
        if live and not items:
            problems.append("품목을 하나 이상 골라 주세요.")
        if len(items) > 6:
            problems.append("품목은 6개까지 볼 수 있어요.")
        if not metrics:
            problems.append("지표를 하나 이상 골라 주세요.")
        mti_items = [c for k, c in items if k == "mti"]
        if mti_items and mti is not None and not problems:
            calls = sum(trade_mti_calls(mti, c, start, end) for c in mti_items) * (2 if compare else 1)
            if calls > TRADE_MTI_MAX_CALLS:
                problems.append(f"조회할 범위가 너무 커요(관세청 호출 약 {calls:,}번). 품목 수나 기간을 줄여 주세요.")
            elif calls > TRADE_MTI_WARN_CALLS:
                st.info(f"범위가 커서 시간이 걸려요(관세청 호출 약 {calls:,}번, 1분 안팎). 같은 조회를 6시간 안에 다시 하면 바로 나와요.")
        if problems:
            st.warning(" ".join(problems))
            st.session_state.pop("tr_r_query", None)
        else:
            st.session_state["tr_r_query"] = {"items": items, "level": level, "start": start, "end": end, "metrics": metrics, "top": top_label, "compare": compare}
    rq = st.session_state.get("tr_r_query")
    if not rq:
        st.caption("조건을 고르고 '조회'를 누르면 순위가 나와요.")
        return
    with st.container(border=True):
        render_trade_rank_result(data, rq)


def render_trade_rank_result(data, rq):
    """국가별 순위 결과(표·그래프). rq: {items, level, start, end, metric, top, compare}"""
    months = data["months"]
    top_n = None if rq["top"] == "전체" else int(rq["top"])
    period = f"{rq['start'].replace('-', '.')} ~ {rq['end'].replace('-', '.')}"
    metrics = rq.get("metrics") or [rq["metric"]]
    mks = [tc.RANK_METRICS[m] for m in metrics]
    st.markdown(f"##### {rq['level']}별 순위 · {period} · {' · '.join(metrics)}")
    try:
        if not rq["items"]:  # 품목 전체: 모아 둔 월별 자료
            entries = tc.rank_entries_stored(data, rq["level"], rq["start"], rq["end"], rq["compare"])
            tot = tc.stored_period(data["total"]["exp"], data["total"]["imp"], months, rq["start"], rq["end"])
            ptot = tc.stored_period(data["total"]["exp"], data["total"]["imp"], months, *tc.prev_period_bounds(rq["start"], rq["end"]))
            denoms = {mk: (tot or {}).get(mk) for mk in mks}
            render_trade_rank_table("전체 품목", entries, metrics, top_n, denoms, rq["level"], rq["compare"], "tr_r_all")
            return
        s_ym, e_ym = rq["start"].replace("-", ""), rq["end"].replace("-", "")
        p_s, p_e = tc.month_add(s_ym, -12), tc.month_add(e_ym, -12)
        mti_tab = get_mti() if any(k == "mti" for k, _ in rq["items"]) else None
        tabs = st.tabs([trade_item_label(data, k, c, mti_tab) for k, c in rq["items"]]) if len(rq["items"]) > 1 else [st.container()]
        gcountry = get_groups_country(data, metrics) if any(k == "mti" and c.startswith(tc.GROUP_PREFIX) for k, c in rq["items"]) else None
        with st.spinner("관세청 자료를 불러오는 중입니다... (산업부 품목은 묶인 HS 코드마다 조회해서 몇 초~1분 걸려요)"):
            for (ikind, code), tab in zip(rq["items"], tabs):
                gname = code[len(tc.GROUP_PREFIX):] if ikind == "mti" and code.startswith(tc.GROUP_PREFIX) else None
                if gname and gcountry and gname in gcountry:  # 20대 품목: 미리 모아 둔 자료로 바로
                    entries = tc.rank_entries_arrays(gcountry[gname], months, rq["level"], rq["start"], rq["end"], rq["compare"])
                    st_tot = tc.stored_period(*tc.sum_arrays(gcountry[gname], list(gcountry[gname]), months), months, rq["start"], rq["end"]) or {}
                    denoms = {mk: st_tot.get(mk) for mk in mks}
                    with tab:
                        render_trade_rank_table(gname, entries, metrics, top_n, denoms, rq["level"], rq["compare"], f"tr_r_{ikind}_{code}")
                    continue
                if ikind == "mti":
                    rows = trade_mti_query(code, None, s_ym, e_ym)[0]
                    prev_rows = trade_mti_query(code, None, p_s, p_e)[0] if rq["compare"] else None
                else:
                    rows = trade_hs_rows(code, s_ym, e_ym)
                    prev_rows = trade_hs_rows(code, p_s, p_e) if rq["compare"] else None
                entries = tc.rank_entries_rows(rows, prev_rows, rq["level"])
                total_all = {mk: (sum(r[mk] for r in rows) if mk in ("exp", "imp", "exp_wgt", "imp_wgt") else sum(r["exp"] - r["imp"] for r in rows)) for mk in mks}
                with tab:
                    render_trade_rank_table(trade_item_label(data, ikind, code, mti_tab), entries, metrics, top_n, total_all, rq["level"], rq["compare"], f"tr_r_{ikind}_{code}")
    except tc.TradeApiError as e:
        st.error(str(e))


def trade_item_label(data, ikind, code, mti):
    """품목 이름표: 20대 품목은 이름, MTI는 '코드 이름', HS는 '코드 (4자리 이름)'"""
    if ikind == "mti":
        return tc.mti_label(mti, code) if mti else code
    return f"{code} {(data.get('hs') or {}).get(code[:4], '') if len(code) == 4 else ''}".strip()


def render_trade_search(data):
    """검색: 무엇을(품목: 전체·HS·산업부 MTI) × 어디서(지역: 전체·국가·경제권) -> 기간 -> 지표를 고르면 바로 보여준다. 시도·세관은 따로."""
    months = data["months"]
    kind = st.radio("종류", TRADE_KINDS, horizontal=True, key="tr_s_kind")
    if kind == "AI검색":
        render_trade_ai(data)
        return
    if kind == "국가별":
        render_trade_rank_country(data)
        return
    if kind == "품목별":
        render_trade_composition(data)
        return
    q = {"kind": "일반 검색", "items": [], "regions": []}
    live = False
    mti = None
    if kind == "일반":
        picked = trade_item_picker(data, "tr_s_")
        if picked is None:
            return
        q["items"], live, mti = picked
        item_mode = st.session_state.get("tr_s_item", "전체")
        reg_mode = st.radio("지역", TRADE_REGION_MODES, horizontal=True, key="tr_s_reg", help="'전체'면 모든 국가를 합쳐요. 경제권은 구성국을 합산해요(관세청 경제권 합계와 같은 값).")
        if reg_mode == "국가":
            order = sorted(data["country"].items(), key=lambda kv: -(sum(x or 0 for x in kv[1][1][-12:])))
            cmap = {v[0]: k for k, v in order}
            sel = st.multiselect("국가", list(cmap), key="tr_s_cnty", max_selections=8)
            q["regions"] = [("국가", n, (cmap[n],)) for n in sel]
        elif reg_mode == "경제권":
            zones = [z for z in tc.ZONES if tc.zone_codes(z)]
            sel = st.multiselect("경제권", zones, key="tr_s_zone", max_selections=8,
                                 help="관세청 월별 경제권 합계와 구성국 합계가 최근 24개월 모두 같은 것만 넣었어요. ASEAN은 티모르가 2026년 1월부터 포함돼요.")
            q["regions"] = [("경제권", z, tuple(tc.zone_codes(z))) for z in sel]
        elif reg_mode == "대륙":
            sel = st.multiselect("대륙", list(tc.CONTINENTS), key="tr_s_cont", max_selections=8,
                                 help="관세청 대륙별 월별 합계와 구성국 합계가 최근 24개월 수출·수입 모두 같도록 맞춘 분류예요. 관세청 분류는 지리와 달라서, 북아프리카(이집트·알제리 등)는 '중동', 튀르키예·조지아는 '유럽', 괌은 '오세아니아'에 들어가요.")
            q["regions"] = [("대륙", c, tuple(tc.continent_codes(c))) for c in sel]
    fm = trade_flash(data)  # 산업부 보도자료 잠정치가 관세청 확정 자료보다 새로우면 종료월에 '잠정' 달이 더 생긴다
    start, end = trade_period_pick(data, "tr_s_", fm)
    metric_opts = ["수출금액", "수입금액", "무역수지"] + (["수출 중량", "수입 중량"] if live else [])
    q["metrics"] = st.multiselect("지표", metric_opts, default=["수출금액", "수입금액", "무역수지"], key=f"tr_s_metrics_{'live' if live else 'stored'}")
    q["compare"] = st.toggle("전년 같은 기간과 비교", value=True, key="tr_s_compare")
    q["start"], q["end"], q["flash"] = start, end, fm
    if st.button("조회", type="primary", key="tr_s_go"):
        problems = []
        if start > end:
            problems.append("시작월이 종료월보다 늦어요.")
        if live and not q["items"]:
            problems.append("품목을 하나 이상 골라 주세요.")
        if reg_mode != "전체" and not q["regions"]:
            problems.append(f"{reg_mode}을(를) 하나 이상 골라 주세요.")
        n = max(1, len(q["items"])) * max(1, len(q["regions"]))
        if n > TRADE_MAX_SERIES:
            problems.append(f"한 번에 {TRADE_MAX_SERIES}개 항목까지만 볼 수 있어요 (지금 {n}개).")
        mti_items = [c for k, c in q["items"] if k == "mti"]
        if mti_items and mti is not None and not problems:
            calls = sum(trade_mti_calls(mti, c, start, end) for c in mti_items) * (2 if q["compare"] else 1)
            if calls > TRADE_MTI_MAX_CALLS:
                problems.append(f"조회할 범위가 너무 커요(관세청 호출 약 {calls:,}번). 품목 수나 기간을 줄여 주세요.")
            elif calls > TRADE_MTI_WARN_CALLS:
                st.info(f"범위가 커서 시간이 걸려요(관세청 호출 약 {calls:,}번, 1분 안팎). 같은 조회를 6시간 안에 다시 하면 바로 나와요.")
        if problems:
            st.warning(" ".join(problems))
            st.session_state.pop("tr_s_query", None)
        else:
            st.session_state["tr_s_query"] = q
    sq = st.session_state.get("tr_s_query")
    if not sq:
        st.caption("조건을 고르고 '조회'를 누르면 결과가 나와요.")
        return
    try:
        with st.spinner("관세청 자료를 불러오는 중입니다... (산업부 품목은 묶인 HS 코드마다 조회해서 몇 초~1분 걸려요)" if any(k == "mti" for k, _ in sq["items"]) else "관세청 자료를 불러오는 중입니다..."):
            series = trade_series(data, sq)
    except tc.TradeApiError as e:
        st.error(str(e))
        return
    with st.container(border=True):
        render_trade_search_result(data, sq, series)
    st.caption("검색 안내", help="품목은 관세청 '품목별 국가별 수출입실적'(통관 기준, 달러)을 바로 불러와요. HS 코드 2·4·6자리로 조회하고, 하위 품목·국가별 내역이 함께 나와요. 품목에 지역(국가·경제권·대륙)을 고르면 받은 자료에서 그 국가들만 합산해요.  \n"
               "20대 품목과 MTI 코드는 무역협회 HSK-MTI 연계표로 HS 10자리를 MTI에 묶어 합친 값이에요. 연계표는 기준년도(2021년 이전·2022~2024·2025~2026)별로 달라서 조회월의 해에 맞는 표를 써요. 산업부·관세청 발표와 1~5% 정도 차이가 날 수 있어요(잠정치·분류 시점 차이).  \n"
               "품목을 '전체'로 두면 모아 둔 월별 자료(최근 2년)를 써서 호출 없이 바로 나와요.  \n"
               "증감은 전년 같은 기간 대비이고, 같은 조건을 6시간 안에 다시 보면 저장한 결과를 써요.")


def render_trade_flash_cards(data):
    """관세청 확정 자료보다 새로운 달의 산업부 잠정치(보도자료 총괄)를 카드로 보여준다"""
    fm = trade_flash(data)
    f = tc.flash_metrics(fm, "총괄", "") if fm else None
    if not f:
        return
    c, p = f["cur"], f["prev"]
    st.markdown(f"##### 산업부 잠정치 · {fm['month'].replace('-', '.')}")
    cards = [("수출", trade_eok(c["exp"]), f"{px_pct(tc.pct(c['exp'], p['exp']))} (전년동월比)", None),
             ("수입", trade_eok(c["imp"]), f"{px_pct(tc.pct(c['imp'], p['imp']))} (전년동월比)", None),
             ("무역수지", trade_eok(c["bal"]), f"{(c['bal'] - p['bal']) / 1e8:+,.1f}억달러 (전년동월比)", None)]
    st.markdown(cb_cards(cards), unsafe_allow_html=True)
    st.caption(f"산업부 보도자료({fm.get('released', '')} 발표) 잠정치예요. 관세청 확정치가 올라오면 아래 표·그래프에 확정치로 들어가요.")


def render_trade_group_rank(data, i):
    """월간 현황: 고른 달의 20대 주력 품목 증감 순위 (미리 모아 둔 groups 자료, 관세청 호출 없음)"""
    gc = get_groups_country(data)
    if not gc:
        return
    months = data["months"]
    with st.container(border=True):
        st.markdown(f"##### 20대 품목 증감 순위 · {months[i].replace('-', '.')}")
        c1, c2 = st.columns(2)
        kind_label = c1.radio("구분", ["수출", "수입"], horizontal=True, key="tr_m_gkind")
        order = c2.radio("정렬", ["증감률", "증감액"], horizontal=True, key="tr_m_gorder")
        rows = tc.group_month_rows(gc, months, i, "exp" if kind_label == "수출" else "imp")
        if not rows:
            st.info("이 달의 20대 품목 자료가 없어요.")
            return
        key = "chg" if order == "증감률" else "diff"
        rows.sort(key=lambda r: (r[key] is None, -(r[key] or 0)))
        table = pd.DataFrame([{"순위": n if r[key] is not None else "", "품목": r["name"], kind_label: trade_eok(r["cur"]),
                               "전년 동월": trade_eok(r["prev"]) if r["prev"] is not None else "-", "증감률": px_pct(r["chg"]),
                               "증감액": "-" if r["diff"] is None else f"{r['diff'] / 1e8:+,.1f}억달러"} for n, r in enumerate(rows, 1)])
        st.dataframe(trade_styled(table, ["증감률", "증감액"]), hide_index=True, height=min(len(table) + 1, 21) * 35 + 3)
        st.caption("산업부 20대 주력 품목 기준이에요(미리 모아 둔 자료). 증감은 전년 같은 달 대비예요.")


def render_trade_monthly(data):
    months, tot = data["months"], data["total"]
    render_trade_flash_cards(data)
    i = trade_month_pick(data, "tr_m_month")
    exp, imp, bal = tot["exp"], tot["imp"], tot["bal"]
    ey, iy = tc.yoy(exp, i), tc.yoy(imp, i)
    ytd_e, ytd_e_prev = tc.ytd_pair(months, exp, i)
    ytd_y = tc.pct(ytd_e, ytd_e_prev)
    cards = [("수출", trade_eok(exp[i]), None if ey is None else f"{px_pct(ey)} (전년동월比)", None),
             ("수입", trade_eok(imp[i]), None if iy is None else f"{px_pct(iy)} (전년동월比)", None),
             ("무역수지", trade_eok(bal[i]), None if i < 12 else f"{(bal[i] - bal[i - 12]) / 1e8:+,.1f}억달러 (전년동월比)", None),
             ("연초 이후 수출", trade_eok(ytd_e), None if ytd_y is None else f"{px_pct(ytd_y)} (전년 누계比)", None)]
    st.markdown(cb_cards(cards), unsafe_allow_html=True)
    render_trade_group_rank(data, i)
    lo = max(0, i - 12)
    rows = [{"월": months[k].replace("-", "."), "수출": trade_eok(exp[k]), "수출 증감": px_pct(tc.yoy(exp, k)), "수입": trade_eok(imp[k]),
             "수입 증감": px_pct(tc.yoy(imp, k)), "무역수지": trade_eok(bal[k])} for k in range(i, lo - 1, -1)]
    table = pd.DataFrame(rows)
    with st.container(border=True):
        st.markdown("##### 최근 13개월")
        st.dataframe(trade_styled(table, ["수출 증감", "수입 증감"]), hide_index=True, height=(len(table) + 1) * 35 + 3)
    first = max(0, i - 23)
    idx = pd.to_datetime(months[first:i + 1], format="%Y-%m")
    with st.container(border=True):
        st.markdown("##### 수출·수입 추이 (억달러)")
        px_line_chart(px_long({"수출": pd.Series([v / 1e8 for v in exp[first:i + 1]], index=idx), "수입": pd.Series([v / 1e8 for v in imp[first:i + 1]], index=idx)}),
                      "억달러", fmt="%Y-%m", axis_fmt="%y.%m")
        st.download_button("표 내려받기 (CSV)", table.to_csv(index=False).encode("utf-8-sig"), file_name="월간수출입.csv", mime="text/csv", key="tr_m_dl")


def render_trade_tenday(data):
    ten = data.get("tenday") or {}
    if not ten:
        st.info("10일 단위 잠정치 자료가 아직 없어요.")
        return
    c1, c2 = st.columns(2)
    ym = c1.selectbox("월", list(reversed(sorted(ten))), format_func=lambda m: f"{m[:4]}.{m[4:]}", key="tr_t_month")
    kind = c2.radio("구분", ["수출", "수입"], horizontal=True, key="tr_t_kind")
    k = "exp" if kind == "수출" else "imp"
    rows = tc.tenday_rows(data, ym, f"{k}_cnty") or tc.tenday_rows(data, ym, f"{k}_item")
    if not rows:
        st.info("이 달의 10일 단위 자료가 아직 없어요.")
        return
    cards = []
    for r in rows:
        end = r["period"][3:]
        label = f"{int(ym[4:])}월 1~{int(end)}일 누계"
        cards.append((label, trade_cheon(r["cur"]), None if r["yoy"] is None else f"{px_pct(r['yoy'])} (전년 동기比)", None))
    st.markdown(cb_cards(cards), unsafe_allow_html=True)
    period = st.radio("누계 기간", [r["period"] for r in rows], index=len(rows) - 1, horizontal=True, key="tr_t_period",
                      format_func=lambda p: f"1~{int(p[3:])}일")
    for part, title in (("cnty", "주요 국가"), ("item", "주요 품목")):
        items = tc.tenday_items(data, ym, f"{k}_{part}", period)
        if not items:
            continue
        total = sum(r["cur"] for r in rows if r["period"] == period) or 1
        names = TRADE_RANK_NAMES.get(f"{k}_{part}", {})
        if part == "item" and len(names) < 5:  # 순번별 품목 이름을 거의 모르면 숫자만으로는 쓸모가 없어 보이지 않는다
            st.caption(f"주요 품목({kind})은 순번별 품목 이름을 확인하는 중이라 아직 보여주지 않아요.")
            continue
        with st.container(border=True):
            st.markdown(f"##### {title} ({kind})")
            table = pd.DataFrame([{"항목": names.get(r["rank"], f"{r['rank']}번째 항목"), "누계": trade_cheon(r["cur"]), "전년 같은 기간": trade_cheon(r["prev"]),
                                   "증감": px_pct(r["yoy"]), "비중": f"{r['cur'] / total * 100:.1f}%"} for r in items])
            st.dataframe(trade_styled(table, ["증감"]), hide_index=True, height=(len(table) + 1) * 35 + 3)
    st.caption("10일 단위 안내", help="관세청 잠정치예요(1일부터 10일·20일·말일까지의 누계). 확정치는 다음 달 중순에 나와 조금 달라질 수 있어요.  \n"
               "전년 같은 기간과 조업일수(주말·휴일)가 다르면 증감률이 왜곡돼요.  \n"
               "항목 이름은 관세청이 순번으로만 줘서, 월별 정식 통계의 금액과 맞춰 확인한 이름이에요. 확인하지 못한 항목은 '○번째 항목'으로 보여요.")


def render_trade_country(data):
    months, tot = data["months"], data["total"]
    i = trade_month_pick(data, "tr_c_month")
    rows = tc.group_table(data["country"], months, i)
    total_exp = tot["exp"][i] or 1
    top = rows[:30]
    table = pd.DataFrame([{"순위": n, "국가": r["name"], "수출": trade_eok(r["exp"]), "증감": px_pct(r["exp_yoy"]), "비중": f"{r['exp'] / total_exp * 100:.1f}%",
                           "수입": trade_eok(r["imp"]), "수입 증감": px_pct(r["imp_yoy"]), "무역수지": trade_eok(r["bal"])} for n, r in enumerate(top, 1)])
    with st.container(border=True):
        st.markdown("##### 국가별 수출입 (수출 상위 30곳)")
        st.dataframe(trade_styled(table, ["증감", "수입 증감"]), hide_index=True, height=min(len(table) + 1, 16) * 35 + 3)
    reg = tc.group_table(data.get("region") or {}, months, i)
    if reg:
        with st.container(border=True):
            st.markdown("##### 경제권별")
            rt = pd.DataFrame([{"경제권": r["name"], "수출": trade_eok(r["exp"]), "증감": px_pct(r["exp_yoy"]), "수입": trade_eok(r["imp"]), "수입 증감": px_pct(r["imp_yoy"]),
                                "무역수지": trade_eok(r["bal"])} for r in reg])
            st.dataframe(trade_styled(rt, ["증감", "수입 증감"]), hide_index=True, height=(len(rt) + 1) * 35 + 3)
            st.caption("경제권은 서로 겹치는 묶음이에요(예: EU와 EU28, OECD). 합산하지 마세요.")
    with st.container(border=True):
        st.markdown("##### 월별 추이 (수출, 억달러)")
        names = [r["name"] for r in rows]
        pick = st.multiselect("국가", names, default=names[:4], key="tr_c_pick", max_selections=8)
        by_name = {v[0]: v for v in data["country"].values()}
        idx = pd.to_datetime(months, format="%Y-%m")
        series = {n: pd.Series([None if x is None else x / 1e8 for x in by_name[n][1]], index=idx).dropna() for n in pick if n in by_name}
        if series:
            px_line_chart(px_long(series), "억달러", fmt="%Y-%m", axis_fmt="%y.%m")
        st.download_button("국가별 표 내려받기 (CSV)", table.to_csv(index=False).encode("utf-8-sig"), file_name="국가별수출입.csv", mime="text/csv", key="tr_c_dl")


def render_trade():
    """탭: 수출입 분석 - 관세청 수출입 통계(검색·월간 현황·10일 단위 속보·국가·경제권)"""
    data = get_trade()
    if data is None:
        st.info("수출입 자료를 아직 불러오지 못했어요. 처음이라면 GitHub Actions의 '수출입 통계 모으기'를 한 번 실행해 주세요.")
        return
    months = data["months"]
    with st.container(key="trade_wrap"):
        st.caption(f"관세청 통관 기준 · 갱신 {str(data.get('updated', ''))[:16].replace('T', ' ')}",
                   help=f"관세청 수출입 통계(통관 기준, 달러)예요. 월별 확정치는 보통 다음 달 중순에 나와요.  \n"
                   f"월별 자료 {months[0].replace('-', '.')} ~ {months[-1].replace('-', '.')} · 자료 갱신 {str(data.get('updated', ''))[:16].replace('T', ' ')}  \n"
                   "증감은 전년 같은 달 대비이고, 파랑은 늘어난 것·빨강은 줄어든 것이에요.")
        view = st.radio("보기", TRADE_VIEWS, horizontal=True, key="tr_view", label_visibility="collapsed")
        {"검색": render_trade_search, "월간 현황": render_trade_monthly, "10일 단위 속보": render_trade_tenday, "국가·경제권": render_trade_country}[view](data)


# ------------------------------------------------------------
# 오늘의 현황 (담당 섹터의 통신·단독·공시·특징주를 한 화면에)
# ------------------------------------------------------------
TODAY_TOP = {"단독": 5, "통신": 3}  # 섹터마다 먼저 보여주는 묶음 수 (나머지는 '더 보기'에)


def today_price_codes(data):
    """뉴스 섹터 이름 -> 시세 자료의 종목코드 목록 (중후장대는 조선·해운+방산+철강)"""
    by = px_sector_codes(data)
    out = {}
    for sec in NEWS_SECTORS:
        names = list(HEAVY_PARTS) if sec == HEAVY_NAME else [sec]
        codes = []
        for n in names:
            codes += [c for c in by.get(n, []) if c not in codes]
        out[sec] = codes
    return out


TODAY_EARN_DAYS = 7  # 오늘의 현황: 실적·IR 일정을 미리 보여 주는 기간(일)


def earnings_names(sector):
    """오늘의 현황 섹터 -> 실적 캘린더에서 쓰는 기업 이름들 (재계처럼 상장사 목록이 없는 섹터는 빈 목록)"""
    return list(CAL_GROUPS.get(sector) or INDUSTRY_GROUPS.get(sector) or [])


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def today_earnings(sector, day_str):
    """섹터 기업의 오늘부터 7일 안 실적·IR 일정 [{'date','corp','kind','text','purpose','no'}]. DART를 못 쓰면 None"""
    names = earnings_names(sector)
    if not names:
        return []
    try:
        corps = [(n, resolve_corp_code(n)) for n in names]
        corps = [(n, c) for n, c in corps if c]
        day = date.fromisoformat(day_str)
        data = collect_calendar(corps, day, day + timedelta(days=TODAY_EARN_DAYS))
    except Exception:
        return None
    return [e for e in data["events"] if e["kind"] != "전년 기준 예상"]  # 확정된 IR 일정만 (전년 기준 예상은 뺌)


def earnings_html(events):
    """일정 -> 날짜 배지가 붙은 한 줄씩의 HTML"""
    rows = []
    for e in events:
        why = f' <span class="why">{html.escape(e["purpose"])}</span>' if e.get("purpose") else ""
        link = (f' <a class="src" href="{html.escape(DART_VIEW_URL.format(e["no"]), quote=True)}" target="_blank" rel="noopener noreferrer">원문</a>'
                if e.get("no") else "")
        rows.append(f'<div class="td-ev"><span class="dt">{cal_day(e["date"])}</span><span class="tx"><b>{html.escape(e["corp"])}</b> {html.escape(e["text"])}{why}{link}</span></div>')
    return "".join(rows)


def movers_html(up, down, when):
    chips = "".join(
        f'<span class="mv {"up" if r["rate"] > 0 else "down"}"><b>{html.escape(str(r["name"]))}</b> {"▲" if r["rate"] > 0 else "▼"}{abs(r["rate"]):.2f}%'
        f'<small>{r["close"]:,.0f}원</small></span>' for r in up + down)
    return f'<div class="td-lbl">특징주 <small>{html.escape(when)}</small></div><div class="td-movers">{chips}</div>'


def render_today():
    """탭: 오늘의 현황 - 고른 섹터마다 단독·통신 기사, 공시, 특징주를 모아 보여준다(매시간 모아 둔 자료라 새로 검색하지 않아요)."""
    rec = get_stored("recent.json")
    if rec is None:
        st.info("모아 둔 기사 자료를 아직 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.")
        return
    with st.container(key="today_wrap"):
        _render_today_body(rec)


def _render_today_body(rec):
    team_buttons("td", NEWS_SECTORS, "td_groups")
    st.pills("섹터", list(NEWS_SECTORS), selection_mode="multi", key="td_groups", label_visibility="collapsed")
    groups = [g for g in (st.session_state.get("td_groups") or []) if g in NEWS_SECTORS]
    now = datetime.now(KST)
    since = tdc.window_start(now, "24시간")  # 기간은 24시간으로 고정 (선택지 없음)
    updated = str(rec.get("updated", ""))[:16].replace("T", " ")
    st.caption(f"{since:%m.%d %H:%M} 이후 · 자료 갱신 {updated}" if updated else f"{since:%m.%d %H:%M} 이후")
    if not groups:
        st.info("섹터를 하나 이상 골라 주세요.")
        return
    rows = rec["items"]
    prices = get_prices()
    snap, when, codes_by = None, "", {}
    if prices:
        try:
            intra = get_intraday()
            snap, when = brief_snap(prices, intra)
            if when.endswith("현재") and sc.intraday_ready(intra, prices):  # 장중이면 날짜도 붙인다 (예: 10.23 15:30 현재)
                when = f"{intra['date'][4:6]}.{intra['date'][6:]} {when}"
            codes_by = today_price_codes(prices)
        except Exception:
            snap = None
    for sec in groups:
        with st.container(border=True):
            cnt = tdc.counts(rows, sec, since)
            pills = "".join(f'<span class="td-pill {cls}">{k} <b>{cnt[k]}</b></span>' for k, cls in (("단독", "k1"), ("통신", "k2"), ("공시", "k3")))
            st.markdown(f'<div class="td-head"><span class="n">{html.escape(sec)}</span><span class="pills">{pills}</span></div>', unsafe_allow_html=True)
            if snap is not None and codes_by.get(sec):
                up, down = tdc.movers(snap, codes_by[sec], 2)
                if up or down:
                    st.markdown(movers_html(up, down, when), unsafe_allow_html=True)
            if earnings_names(sec):
                events = today_earnings(sec, str(now.date()))
                if events:
                    st.markdown(f'<div class="td-lbl">실적·IR 일정 <small>앞으로 {TODAY_EARN_DAYS}일</small></div>{earnings_html(events)}', unsafe_allow_html=True)
            for kind in ("단독", "통신"):
                items = tdc.sector_items(rows, sec, kind, since)
                if not items:
                    continue
                clusters = cluster_news(items)
                st.markdown(f'<div class="td-lbl {"k1" if kind == "단독" else "k2"}">{kind} <small>{len(items)}건 → 묶음 {len(clusters)}건</small></div>', unsafe_allow_html=True)
                top = TODAY_TOP[kind]
                st.markdown(news_html(clusters[:top], now), unsafe_allow_html=True)
                if len(clusters) > top:
                    with st.expander(f"{kind} 더 보기 ({len(clusters) - top}건)"):
                        st.markdown(news_html(clusters[top:], now), unsafe_allow_html=True)
            disc = tdc.sector_items(rows, sec, "공시", since)
            if disc:
                with st.expander(f"공시 {len(disc)}건"):
                    st.markdown("\n".join(f"- [{d['title'].replace('[', '(').replace(']', ')')}]({d['url']}) · {d['publisher']} · {d['dt']:%m.%d %H:%M}" for d in disc[:30]))
            if not any(cnt.values()) and not (snap is not None and codes_by.get(sec)):
                st.caption("최근 24시간에 모아 둔 기사·공시가 없어요.")


# ------------------------------------------------------------
# AI 검색 (재무 분석 · 공시 분석) — 무역 통계 AI 검색과 같은 방식:
#  AI는 질문을 '조회 조건'으로 바꾸거나 원문에서 그대로 옮긴 글을 고르기만 하고, 숫자는 DART 자료·원문에서 나온다.
#  AI가 준 조건은 허용된 값으로 거르고(ai_search.py), 옮긴 글·숫자는 원문에 정말 있는지 확인해 표시한다.
# ------------------------------------------------------------
AI_SEARCH_SYSTEM = (
    "너는 중앙일보 산업부 기자의 질문을 앱의 조회 조건(JSON)으로 바꾸는 도우미다. 질문에 직접 답하거나 숫자를 만들지 않는다. "
    "정해진 목록에 있는 값만 고르고, 모르면 비워 둔다. 회사명은 DART에 등록된 정식 이름(예: 현대차→현대자동차, LG엔솔→LG에너지솔루션, 포스코홀딩스→POSCO홀딩스)으로 쓴다."
)
AI_NOKEY = "AI 검색을 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY(Claude) 또는 GEMINI_API_KEY(Gemini)를 추가해주세요."


def ai_ask_json(tag, prompt, system=AI_SEARCH_SYSTEM):
    """AI에 JSON으로 묻고(같은 질문은 일주일 저장) -> (dict, 쓴 모델)"""
    sig = hashlib.sha1((system + prompt).encode("utf-8")).hexdigest()
    text, model = ai_cached((tag, sig), lambda: ai_generate(prompt, system=system, json_mode=True))
    data = parse_json(text)
    if not isinstance(data, dict):
        raise AiError("AI 답을 조건으로 바꾸지 못했어요. 질문을 조금 바꿔 다시 시도해주세요.")
    return data, model


def ai_resolve_corps(names):
    """회사명 목록 -> ([(이름, 고유번호)], [못 찾은 이름])"""
    found, missing = [], []
    for n in names:
        corp = canonical_corp(n)
        code = resolve_corp_code(corp) if corp else None
        if code and code not in [c for _, c in found]:
            found.append((corp, code))
        elif not code:
            missing.append(n)
    return found, missing


def ai_missing_note(missing):
    for n in missing:
        hint = [label for label, _ in suggest_corps(n, limit=3)]
        st.warning(f'"{n}" 기업을 DART에서 찾지 못해 뺐어요.' + (f" 혹시: {', '.join(hint)}" if hint else ""))


def ai_understood(spec):
    st.warning("AI가 질문을 조건으로 바꾼 결과예요. 아래 조건이 맞는지 확인하고 조회하세요. 숫자는 AI가 아니라 DART 자료·원문에서 나와요.")
    if spec.get("summary"):
        st.markdown(f"**AI가 이해한 내용** · {spec['summary']}")
    if spec.get("note"):
        st.caption(f"조건으로 바꾸지 못한 부분: {spec['note']}")


# --- 1) 재무 분석 AI 검색 ---
FIN_AI_MAX_CORPS = 5
FIN_AI_METRICS = {  # 지표 이름 -> (종류, 값 꺼내기(r, m)). 종류: amt=금액(원), pct=%, times=배
    "매출액": ("amt", lambda r, m: r.get("rev")),
    "영업이익": ("amt", lambda r, m: r.get("op")),
    "순이익": ("amt", lambda r, m: r.get("net")),
    "영업이익률": ("pct", lambda r, m: r["op"] / r["rev"] * 100 if r.get("op") is not None and r.get("rev") else None),
    "순이익률": ("pct", lambda r, m: r["net"] / r["rev"] * 100 if r.get("net") is not None and r.get("rev") else None),
    "자산총계": ("amt", lambda r, m: m["assets"]),
    "부채총계": ("amt", lambda r, m: m["liab"]),
    "자본총계": ("amt", lambda r, m: m["equity"]),
    "현금성자산": ("amt", lambda r, m: (r.get("bs") or {}).get("cash")),
    "재고자산": ("amt", lambda r, m: (r.get("bs") or {}).get("inv")),
    "부채비율": ("pct", lambda r, m: m["debt_ratio"]),
    "순차입금": ("amt", lambda r, m: m["net_debt"]),
    "순차입금비율": ("pct", lambda r, m: m["nd_ratio"]),
    "유동비율": ("pct", lambda r, m: m["cur_ratio"]),
    "영업활동현금흐름": ("amt", lambda r, m: m["opcf"]),
    "CAPEX": ("amt", lambda r, m: (r.get("bs") or {}).get("capex")),
    "잉여현금흐름": ("amt", lambda r, m: m["fcf"]),
    "EBITDA": ("amt", lambda r, m: m["ebitda"]),
    "이자보상배율": ("times", lambda r, m: m["int_cov"]),
    "ROE": ("pct", lambda r, m: m["roe"]),
}
FIN_AI_YEARLY_ONLY = {"CAPEX", "잉여현금흐름", "EBITDA", "이자보상배율", "ROE"}
FIN_AI_DEFAULT = ["매출액", "영업이익", "영업이익률"]
FIN_AI_MAX_QUARTER_YEARS = 3  # 분기별은 호출이 많아 최대 3년


def fin_ai_prompt_q(text):
    ly, lp = latest_period()
    sectors = "\n".join(f"- {k}: {', '.join(v)}" for k, v in INDUSTRY_GROUPS.items())
    return (
        f"오늘: {TODAY}. 지금 볼 수 있는 가장 최근 정기보고서: {ly}년 {lp} (연간 확정 실적은 {ly if lp == '4Q' else ly - 1}년까지).\n"
        f"질문: {text}\n\n"
        "아래 JSON 하나로만 답해라.\n"
        '{"summary": "질문을 어떻게 이해했는지 한 문장", "corps": ["회사 정식 이름", ...], "period": "연도별" 또는 "분기별", '
        '"start": 시작연도, "end": 끝연도, "metrics": ["지표", ...], "note": "조건으로 표현하지 못한 부분(없으면 빈 문자열)"}\n'
        f"- corps: 최대 {FIN_AI_MAX_CORPS}곳. 섹터·업계를 말하면 아래 섹터 기업 목록에서 대표 기업을 골라라.\n"
        f"- metrics: 다음 중에서만 고른다: {', '.join(FIN_AI_METRICS)}. 말하지 않았으면 {', '.join(FIN_AI_DEFAULT)}.\n"
        "- 기간을 말하지 않으면 연도별 최근 5년(연간 확정 실적 기준). '최근 N년'은 끝연도에서 N년. "
        "'분기', '3분기', '이번 분기'처럼 분기를 말하면 분기별로, 그 연도를 시작·끝으로(추이를 원하면 최대 3년).\n"
        f"[섹터 기업 목록]\n{sectors}"
    )


def fin_ai_spec(text):
    raw, model = ai_ask_json("finq", fin_ai_prompt_q(text))
    period = "분기별" if str(raw.get("period", "")).strip() == "분기별" else "연도별"
    ly, lp = latest_period()
    last_full = ly if lp == "4Q" else ly - 1
    if period == "분기별":
        start, end = ais.year_range(raw.get("start"), raw.get("end"), 2015, TODAY.year, ly, 1, FIN_AI_MAX_QUARTER_YEARS)
    else:
        start, end = ais.year_range(raw.get("start"), raw.get("end"), 2015, TODAY.year, last_full, 5, MAX_YEAR_SPAN)
    metrics = ais.pick_known(raw.get("metrics"), FIN_AI_METRICS) or list(FIN_AI_DEFAULT)
    dropped = [m for m in metrics if period == "분기별" and m in FIN_AI_YEARLY_ONLY]
    metrics = [m for m in metrics if m not in dropped] or list(FIN_AI_DEFAULT)
    note = str(raw.get("note") or "").strip()
    if dropped:
        note = (note + " · " if note else "") + f"{', '.join(dropped)}은(는) 연도별에서만 볼 수 있어 뺐어요"
    return {"summary": str(raw.get("summary") or "").strip(), "note": note, "model": model,
            "corps": ais.clean_names(raw.get("corps"), FIN_AI_MAX_CORPS), "period": period, "start": start, "end": end, "metrics": metrics}


def fin_ai_cell(kind, v, unit):
    if isinstance(v, str):
        return v
    if v is None:
        return "-"
    return fmt_amount(v, unit) if kind == "amt" else (f"{v:,.1f}배" if kind == "times" else f"{v:,.1f}%")


def fin_ai_tables(results, metrics, period, unit):
    """[(회사, 기록)] -> (보여줄 표, 그래프용 {지표: 표})"""
    yearly = period != "분기별"
    labels, values = {}, {}
    for corp, records in results:
        for r in sorted_records(records):
            lab = r["label"].replace("†", "")
            labels.setdefault(lab, r["sort"])
            m = bs_metrics(r, yearly)
            for name in metrics:
                values[(lab, corp, name)] = FIN_AI_METRICS[name][1](r, m)
    order = sorted(labels, key=lambda l: labels[l])
    corps = [c for c, _ in results]
    rows = []
    for lab in order:
        row = {"기간": lab}
        for name in metrics:
            for corp in corps:
                col = name if len(corps) == 1 else (corp if len(metrics) == 1 else f"{corp} {name}")
                row[col] = fin_ai_cell(FIN_AI_METRICS[name][0], values.get((lab, corp, name)), unit)
        rows.append(row)
    charts = {}
    div = UNITS[unit][0]
    for name in metrics:
        kind = FIN_AI_METRICS[name][0]
        data = {}
        for corp in corps:
            col = []
            for lab in order:
                v = values.get((lab, corp, name))
                col.append(None if v is None or isinstance(v, str) else (v / div if kind == "amt" else v))
            data[corp] = col
        charts[name] = pd.DataFrame(data, index=order)
    return pd.DataFrame(rows), charts


def render_fin_ai_search():
    if not AI_PROVIDER:
        st.info(AI_NOKEY)
        return
    st.caption("궁금한 것을 말로 쓰면 AI가 회사·기간·지표를 정하고, 숫자는 재무 조회와 같은 DART 자료로 계산해요.")
    text = st.text_area("찾고 싶은 것", key="fai_text", height=80,
                        placeholder="예: 현대차·기아 최근 5년 영업이익률 비교  /  배터리 3사 올해 분기별 영업이익  /  조선 3사 부채비율 추이")
    if st.button("AI에게 물어보기", type="primary", key="fai_go"):
        st.session_state.pop("fai_result", None)
        if not text.strip():
            st.warning("찾고 싶은 것을 입력해 주세요.")
            st.session_state.pop("fai_spec", None)
        else:
            try:
                with st.spinner("AI가 회사·기간·지표를 정하는 중입니다... (몇 초)"):
                    st.session_state["fai_spec"] = fin_ai_spec(text)
            except AiError as e:
                st.session_state.pop("fai_spec", None)
                st.error(str(e))
                return
    spec = st.session_state.get("fai_spec")
    if not spec:
        return
    ai_understood(spec)
    if not spec["corps"]:
        st.info("질문에서 회사를 찾지 못했어요. 회사 이름을 넣어 다시 물어봐 주세요.")
        return
    st.markdown(f"**회사** {', '.join(spec['corps'])}  \n**기간** {spec['start']}~{spec['end']}년 {spec['period']}  \n**지표** {', '.join(spec['metrics'])}")
    if st.button("이 조건으로 조회", key="fai_run"):
        found, missing = ai_resolve_corps(spec["corps"])
        ai_missing_note(missing)
        collect = collect_quarterly if spec["period"] == "분기별" else collect_yearly
        results, bar = [], st.progress(0.0, text="DART에서 조회 중입니다...")
        try:
            for i, (corp, code) in enumerate(found):
                bar.progress(i / max(len(found), 1), text=f"DART에서 조회 중입니다... {corp} ({i + 1}/{len(found)})")
                results.append((corp, collect(code, spec["start"], spec["end"], lambda: None)))
        except DartApiError as e:
            st.error(str(e))
            results = []
        bar.empty()
        st.session_state["fai_result"] = {"spec": spec, "results": [(c, r) for c, r in results if r],
                                          "empty": [c for c, r in results if not r]}
    res = st.session_state.get("fai_result")
    if not res or res["spec"] != spec:
        return
    if res["empty"]:
        st.caption("자료가 없는 회사: " + ", ".join(res["empty"]))
    if not res["results"]:
        st.warning("조회된 자료가 없어요. 회사·기간을 바꿔 다시 물어봐 주세요.")
        return
    with st.container(border=True):
        st.markdown("##### 조회 결과")
        unit = st.radio("단위", list(UNITS), horizontal=True, key="fai_unit", label_visibility="collapsed")
        table, charts = fin_ai_tables(res["results"], spec["metrics"], spec["period"], unit)
        st.caption(f"금액 단위: {unit} · 연결 기준(없으면 별도) · DART 재무 자료로 계산한 값이에요")
        st.dataframe(table, hide_index=True, height=(len(table) + 1) * 35 + 3)
        copy_table_box(table, "fai_copy")
        for name, chart in charts.items():
            if chart.notna().any().any():
                kind = FIN_AI_METRICS[name][0]
                st.markdown(f"**{name}** ({unit if kind == 'amt' else ('배' if kind == 'times' else '%')})")
                st.line_chart(chart, height=220)


# --- 2) 공시 분석 AI 검색 ---
DISC_AI_KINDS = {**MAJOR_DISCLOSURES, **EXTRA_DISCLOSURES}
DISC_AI_SECTORS = {**INDUSTRY_GROUPS, **CAL_GROUPS}
DISC_AI_MAX_CORPS = 30
DISC_AI_MAX_DOCS = 15  # 원문에서 숫자를 뽑는 공시 수 (AI 호출 수)
DISC_AI_FIELDS = 4


def disc_ai_prompt_q(text):
    kinds = "\n".join(f"- {k}: {', '.join(v)}" for k, v in DISC_AI_KINDS.items())
    sectors = "\n".join(f"- {k}: {', '.join(v)}" for k, v in DISC_AI_SECTORS.items())
    return (
        f"오늘: {TODAY}.\n질문: {text}\n\n"
        "아래 JSON 하나로만 답해라.\n"
        '{"summary": "질문을 어떻게 이해했는지 한 문장", "corps": ["회사 정식 이름", ...], "sectors": ["섹터", ...], '
        '"kinds": ["공시 묶음", ...], "words": ["보고서명에 들어갈 낱말", ...], "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", '
        '"fields": ["원문에서 뽑을 항목", ...], "sum": "합계를 낼 금액 항목(없으면 빈 문자열)", "note": "조건으로 표현하지 못한 부분"}\n'
        "- 회사를 말하면 corps, 업계·섹터를 말하면 sectors(아래 목록 이름 그대로). 둘 다 써도 된다.\n"
        "- kinds는 아래 공시 묶음 이름에서만 고른다. 묶음에 없는 공시는 words에 보고서명 낱말로 적는다(예: 수주→공급계약, 자사주 소각→자기주식, 주식소각).\n"
        "- 기간을 말하지 않으면 최근 30일. '올해'는 올해 1월 1일부터 오늘까지. 최대 1년.\n"
        f"- fields: 표로 보고 싶은 원문 항목 최대 {DISC_AI_FIELDS}개(예: 계약금액, 계약상대방, 계약기간, 매출액 대비 비율, 소각 주식수, 증자 금액). "
        "숫자를 묻지 않으면 빈 목록. '합계', '모두 얼마' 같은 질문이면 sum에 그 금액 항목 이름.\n"
        f"[공시 묶음]\n{kinds}\n[섹터 기업 목록]\n{sectors}"
    )


def disc_ai_spec(text):
    raw, model = ai_ask_json("discq", disc_ai_prompt_q(text))
    sectors = ais.pick_known(raw.get("sectors"), DISC_AI_SECTORS)
    names = ais.clean_names(raw.get("corps"), DISC_AI_MAX_CORPS)
    for s in sectors:
        names += [n for n in DISC_AI_SECTORS[s] if n not in names]
    note = str(raw.get("note") or "").strip()
    if len(names) > DISC_AI_MAX_CORPS:
        note = (note + " · " if note else "") + f"기업이 많아 앞의 {DISC_AI_MAX_CORPS}곳만 봐요"
    kinds = ais.pick_known(raw.get("kinds"), DISC_AI_KINDS)
    words = [w for w in ais.clean_names(raw.get("words"), 5) if len(w) <= 20]
    start, end = ais.date_range(raw.get("start"), raw.get("end"), TODAY)
    fields = [f for f in ais.clean_names(raw.get("fields"), DISC_AI_FIELDS) if len(f) <= 20]
    total = str(raw.get("sum") or "").strip()
    if total and total not in fields:
        fields = (fields + [total])[-DISC_AI_FIELDS:] if len(fields) >= DISC_AI_FIELDS else fields + [total]
    return {"summary": str(raw.get("summary") or "").strip(), "note": note, "model": model, "corps": names[:DISC_AI_MAX_CORPS],
            "sectors": sectors, "kinds": kinds, "words": words, "start": start, "end": end, "fields": fields,
            "sum": total if total in fields else ""}


def disc_ai_rows(spec, found):
    """조건에 맞는 공시 (최신순)"""
    keys = [k for kind in spec["kinds"] for k in DISC_AI_KINDS[kind]] + spec["words"]
    out = []
    for i, (corp, code) in enumerate(found):
        rows, _, _ = collect_disclosures(code, spec["start"], spec["end"])
        for r in rows:
            name = str(r.get("report_nm", "")).strip()
            if not keys or ais.match_report(strip_correction(name), keys):
                out.append(dict(r, corp_name=r.get("corp_name") or corp))
    return sorted(out, key=lambda r: (str(r.get("rcept_dt", "")), str(r.get("rcept_no", ""))), reverse=True)


def disc_ai_extract(rno, title, fields):
    """공시 원문에서 항목 값을 '원문 표기 그대로' 뽑고, 원문에 정말 있는지 확인 -> {항목: (값, 확인됨)}"""
    session, store = get_session(), get_cache_store()
    text = fetch_document_text(session, store, rno)
    body = text[:20000]
    prompt = (
        f"공시 제목: {title}\n아래 공시 원문에서 다음 항목의 값을 찾아라: {', '.join(fields)}\n"
        '{"항목 이름": "원문에 적힌 표기 그대로의 값(숫자·단위·쉼표 그대로, 못 찾으면 빈 문자열)", ...} 형식의 JSON 하나로만 답해라. '
        "계산하거나 단위를 바꾸지 마라. 정정 공시면 정정 후 값을 쓴다.\n\n[공시 원문]\n" + body
    )
    raw, _ = ai_ask_json("discx", prompt, system=AI_SYSTEM)
    return {f: (str(raw.get(f) or "").strip(), ais.value_in(raw.get(f), text)) for f in fields}


def render_disc_ai_search():
    if not AI_PROVIDER:
        st.info(AI_NOKEY)
        return
    st.caption("찾고 싶은 공시를 말로 쓰면 AI가 회사·공시 종류·기간을 정하고, 목록은 DART에서 그대로 가져와요. 금액 같은 숫자는 원문에서 옮긴 뒤 원문에 있는지 확인해요.")
    text = st.text_area("찾고 싶은 공시", key="dai_text", height=80,
                        placeholder="예: 최근 한 달 조선사 수주 공시 금액 합계  /  올해 자사주 소각한 반도체 기업  /  이번 주 유상증자 결정한 배터리 회사")
    if st.button("AI에게 물어보기", type="primary", key="dai_go"):
        for k in ("dai_rows", "dai_nums"):
            st.session_state.pop(k, None)
        if not text.strip():
            st.warning("찾고 싶은 공시를 입력해 주세요.")
            st.session_state.pop("dai_spec", None)
        else:
            try:
                with st.spinner("AI가 회사·공시 종류·기간을 정하는 중입니다... (몇 초)"):
                    st.session_state["dai_spec"] = disc_ai_spec(text)
            except AiError as e:
                st.session_state.pop("dai_spec", None)
                st.error(str(e))
                return
    spec = st.session_state.get("dai_spec")
    if not spec:
        return
    ai_understood(spec)
    if not spec["corps"]:
        st.info("질문에서 회사나 섹터를 찾지 못했어요. 회사·업계 이름을 넣어 다시 물어봐 주세요.")
        return
    what = ", ".join(spec["kinds"] + [f"'{w}'" for w in spec["words"]]) or "모든 공시"
    lines = [f"**기업** {', '.join(spec['corps'][:8])}" + (f" 외 {len(spec['corps']) - 8}곳" if len(spec["corps"]) > 8 else ""),
             f"**공시** {what}", f"**기간** {spec['start']:%Y.%m.%d}~{spec['end']:%Y.%m.%d}"]
    if spec["fields"]:
        lines.append(f"**원문에서 뽑을 항목** {', '.join(spec['fields'])}" + (f" (합계: {spec['sum']})" if spec["sum"] else ""))
    st.markdown("  \n".join(lines))
    if st.button("이 조건으로 공시 찾기", key="dai_run"):
        st.session_state.pop("dai_nums", None)
        with st.spinner(f"DART에서 {len(spec['corps'])}곳의 공시를 찾는 중입니다..."):
            found, missing = ai_resolve_corps(spec["corps"])
            try:
                st.session_state["dai_rows"] = {"spec": spec, "rows": disc_ai_rows(spec, found), "missing": missing}
            except DartApiError as e:
                st.error(str(e))
    res = st.session_state.get("dai_rows")
    if not res or res["spec"] != spec:
        return
    ai_missing_note(res["missing"])
    rows = res["rows"]
    if not rows:
        st.info("조건에 맞는 공시가 없어요. 기간을 늘리거나 질문을 바꿔 보세요.")
        return
    st.markdown(f"##### 찾은 공시 {len(rows)}건")
    table = pd.DataFrame([{"접수일": f"{str(r.get('rcept_dt', ''))[:4]}.{str(r.get('rcept_dt', ''))[4:6]}.{str(r.get('rcept_dt', ''))[6:8]}",
                           "기업": r.get("corp_name", ""), "보고서명": str(r.get("report_nm", "")).strip(),
                           "원문": DART_VIEW_URL.format(str(r["rcept_no"]).strip())} for r in rows])
    st.dataframe(table, hide_index=True, column_config={"원문": LINK_COLUMN}, height=min(len(table) + 1, 15) * 35 + 3)
    copy_table_box(table, "dai_copy", drop=("원문",))
    if not spec["fields"]:
        return
    targets = [r for r in rows if not is_correction(r.get("report_nm"))][:DISC_AI_MAX_DOCS] or rows[:DISC_AI_MAX_DOCS]
    if st.button(f"원문에서 {', '.join(spec['fields'])} 뽑기 ({len(targets)}건)", key="dai_nums_go"):
        out, bar = [], st.progress(0.0, text="공시 원문을 읽는 중입니다...")
        for i, r in enumerate(targets):
            bar.progress(i / len(targets), text=f"공시 원문을 읽는 중입니다... ({i + 1}/{len(targets)})")
            rno = str(r["rcept_no"]).strip()
            try:
                vals = disc_ai_extract(rno, str(r.get("report_nm", "")), spec["fields"])
            except (AiError, DartApiError) as e:
                vals = {f: ("", False) for f in spec["fields"]}
                vals["_err"] = str(e)
            out.append((r, vals))
        bar.empty()
        st.session_state["dai_nums"] = {"spec": spec, "out": out}
    nums = st.session_state.get("dai_nums")
    if not nums or nums["spec"] != spec:
        if len(rows) > len(targets):
            st.caption(f"숫자는 정정 공시를 뺀 최신 {len(targets)}건까지만 뽑아요.")
        return
    recs, total, n_total = [], 0.0, 0
    for r, vals in nums["out"]:
        rec = {"기업": r.get("corp_name", ""), "보고서명": strip_correction(r.get("report_nm"))}
        unsure = []
        for f in spec["fields"]:
            v, ok = vals.get(f, ("", False))
            rec[f] = v or "-"
            if v and not ok:
                unsure.append(f)
            if f == spec["sum"] and v and ok:
                won = ais.parse_won(v)
                if won is not None:
                    total += won
                    n_total += 1
        rec["확인"] = "원문 확인" if not unsure else "확인 필요: " + ", ".join(unsure)
        if vals.get("_err"):
            rec["확인"] = "원문을 못 읽음"
        rec["원문"] = DART_VIEW_URL.format(str(r["rcept_no"]).strip())
        recs.append(rec)
    st.markdown("##### 원문에서 뽑은 숫자")
    if spec["sum"]:
        st.markdown(f"**{spec['sum']} 합계: {won_text(total)}** (원문에서 확인된 원화 금액 {n_total}건)")
    ntab = pd.DataFrame(recs)
    st.dataframe(ntab, hide_index=True, column_config={"원문": LINK_COLUMN}, height=min(len(ntab) + 1, 15) * 35 + 3)
    copy_table_box(ntab, "dai_nums_copy", drop=("원문",))
    st.caption("값은 AI가 원문 표기 그대로 옮긴 것이고, '원문 확인'은 그 값이 원문에 실제로 있는 것을 앱이 확인했다는 뜻이에요. "
               "'확인 필요'는 원문에서 같은 표기를 찾지 못한 것이니 원문으로 확인하세요. 합계는 원문 확인된 원화 금액만 더했어요.")


TAB_NAMES = ["오늘의 현황", "기업 브리핑", "재무 분석", "주가 분석", "공시 분석", "산업 뉴스", "무역 통계", "실적 캘린더", "정기보고서", "재계 순위"]
SHOW_LAB = st.query_params.get("lab", "") == "1"  # 관리자용 주소: 주소 끝에 ?lab=1을 붙이면 숨겨 둔 탭(이슈 타임라인)이 보임
if SHOW_LAB:
    TAB_NAMES += ["이슈 타임라인"]  # 관리자용 탭은 맨 끝
# 관심 기업 주소(?news=... 또는 ?sector=...)로 들어오면 산업 뉴스 탭을 먼저 연다
first_tab = "산업 뉴스" if apply_watchlist() else None
try:
    tabs = st.tabs(TAB_NAMES, default=first_tab)
except TypeError:  # 처음 열 탭 지정을 지원하지 않는 옛 Streamlit
    tabs = st.tabs(TAB_NAMES)
by_name = dict(zip(TAB_NAMES, tabs))
tab_today, tab_brief, tab_fin, tab_trade, tab_news, tab_disc, tab_cal, tab_periodic, tab_chaebol = (by_name[n] for n in ("오늘의 현황", "기업 브리핑", "재무 분석", "무역 통계", "산업 뉴스", "공시 분석", "실적 캘린더", "정기보고서", "재계 순위"))
with tab_today:
    render_today()
with tab_brief:
    render_brief()
with tab_fin:
    render_fin_hub()
with by_name["주가 분석"]:
    render_stock()
with tab_trade:
    render_trade()
with tab_news:
    render_news()
if SHOW_LAB:
    with by_name["이슈 타임라인"]:
        render_timeline()
with tab_disc:
    render_disclosure_hub()
with tab_periodic:
    render_periodic()
with tab_cal:
    render_calendar()
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

def alarm_link(sectors, modes):
    """고른 섹터·종류 -> 봇을 열면서 신청 내용을 넘기는 t.me 링크 (start 값: s + 섹터 0/1 아홉 칸 + m + 종류 0/1 세 칸)"""
    sb = "".join("1" if x in sectors else "0" for x in ALARM_SECTORS)
    mb = "".join("1" if x in modes else "0" for x in ALARM_MODES)
    return f"https://t.me/{ALARM_BOT}?start=s{sb}m{mb}"


def render_alarm_signup():
    with st.popover("🔔 뉴스·공시 알람 받기"):
        st.caption("원하는 섹터를 고르고 신청하면 텔레그램이 열려요. 아래 '시작(Start)'을 누르면 신청이 접수되고, 확인 후 알람이 시작돼요. 알람 받을 섹터, 종류는 추후 변경 가능해요.")
        team_buttons("alarm", ALARM_SECTORS, "alarm_sectors", label="받을 섹터")
        secs = st.multiselect("받을 섹터", list(ALARM_SECTORS), key="alarm_sectors", label_visibility="collapsed")
        modes = st.multiselect("받을 종류", list(ALARM_MODES), default=list(ALARM_MODES), key="alarm_modes")
        if secs and modes:
            st.link_button("신청하기 (텔레그램 열기)", alarm_link(secs, modes), type="primary")
        else:
            st.button("신청하기 (텔레그램 열기)", disabled=True, help="섹터와 종류를 하나 이상 골라주세요.")


CALC_TABS = ["증감", "표 증감", "단위", "영어", "연평균", "비중", "글자 수", "날짜"]


def calc_result(rows):
    """계산 결과 (이름, 값) 목록 -> 작은 카드 줄"""
    cells = "".join(f'<div class="cc"><span class="l">{html.escape(k)}</span><span class="v">{html.escape(v)}</span></div>' for k, v in rows)
    st.markdown(f'<div class="calc-out">{cells}</div>', unsafe_allow_html=True)


def calc_num(label, key, placeholder):
    raw = st.text_input(label, key=key, placeholder=placeholder)
    v, pct = ct.parse_num(raw)
    if raw.strip() and v is None:
        st.caption(f"'{raw}'을(를) 숫자로 읽지 못했어요.")
    return v, pct


def render_calculator():
    """제목 옆 '계산기': 숫자를 보면서 바로 쓰는 계산 도구 (앱 안에서 계산만, 외부 조회 없음)"""
    with st.popover("🧮 계산기"), st.container(key="calc_box", height=460, border=False):  # 창 크기는 CSS로 고정 (칸마다 같은 크기, 넘치면 안에서 스크롤)
        tabs = st.tabs(CALC_TABS)
        with tabs[0]:
            st.caption("이전 값과 지금 값을 넣으면 증감률·증감액·배수가 나와요. 쉼표·조·억·△(마이너스)·%를 그대로 붙여넣어도 돼요.")
            a, ap = calc_num("이전 값 (A)", "calc_a", "예: 1조2,345억 / 8.1%")
            b, bp = calc_num("지금 값 (B)", "calc_b", "예: 1조5,000억 / 9.4%")
            if a is not None and b is not None:
                r = ct.change(a, b)
                rows = [("판정", r["status"]),
                        ("증감률", f"{r['rate']:+.1f}%" if r["rate"] is not None else "계산 안 함 (이전 값이 0 이하)"),
                        ("증감액", ("+" if r["diff"] > 0 else "") + (ct.kor_number(r["diff"]) if abs(r["diff"]) >= 1e4 else ct.num_text(r["diff"], 2)))]
                if r["times"] is not None:
                    rows.append(("배수 (B÷A)", f"{r['times']:.2f}배"))
                if ap and bp:
                    rows.append(("%p 차이", f"{b - a:+.1f}%p"))
                calc_result(rows)
                st.caption("증감률 = (B − A) ÷ A × 100. 이전 값이 적자(0 이하)면 증감률 대신 흑자전환·적자확대·적자축소로 판정해요.")
        with tabs[1]:
            st.caption("한 줄에 '항목 작년값 올해값'. 엑셀에서 이름·작년·올해 세 열을 복사해 붙여넣어도 돼요.")
            text = st.text_area("항목 작년값 올해값", key="calc_tc", height=120, placeholder="반도체 1200 1500\n자동차 600 620\n석유제품 400 350", label_visibility="collapsed")
            st.button("표 증감 계산", key="calc_tc_go", use_container_width=True)  # 폰은 Ctrl+Enter가 없어, 누르면 입력이 반영돼 계산된다
            rows, total = ct.table_change(text)
            if rows:
                def pct(v, sign=False):
                    return "-" if v is None else (f"{v:+.1f}%" if sign else f"{v:.1f}%")
                df = pd.DataFrame([{"항목": r["name"], "작년": ct.num_text(r["a"], 2), "올해": ct.num_text(r["b"], 2), "증감률": pct(r["rate"], True),
                                    "증감액": ct.num_text(r["diff"], 2), "비중": f"{pct(r['share_a'])}→{pct(r['share_b'])}",
                                    "비중 변화": "-" if r["share_a"] is None or r["share_b"] is None else f"{r['share_b'] - r['share_a']:+.1f}%p",
                                    "기여도": pct(r["contrib"]), "판정": r["status"]} for r in rows + [total]])
                st.dataframe(df, hide_index=True, height=(len(df) + 1) * 35 + 3)
                copy_table_box(df, "calc_tc_copy")
                st.caption("기여도 = 그 항목 증감액 ÷ 전체 증감액 × 100 (전체가 늘 때 줄어든 항목은 마이너스). 비중은 합계 대비. 증감률은 작년값이 0 이하면 '-'.")
        with tabs[2]:
            c1, c2 = st.columns([3, 2])
            with c1:
                v, _ = calc_num("숫자", "calc_unit_v", "예: 1,234,567")
            unit = c2.selectbox("단위", list(ct.UNIT_MULT), index=list(ct.UNIT_MULT).index("백만원"), key="calc_unit_u")
            if v is not None:
                won, rows, words = ct.convert(v, unit)
                calc_result([("기사체", words)] + rows)
                usd = st.text_input("환율 (원/달러, 넣으면 달러로도)", key="calc_fx", placeholder="예: 1,380")
                fx, _ = ct.parse_num(usd)
                if fx:
                    calc_result([("달러", "$" + ct.en_number(won / fx)[0]), ("달러(숫자)", f"${won / fx:.0f}")])
        with tabs[3]:
            st.caption("한글 단위(조·억·만) ↔ 영어 단위(trillion·billion·million) 바꾸기")
            v, _ = calc_num("한글 → 영어", "calc_en_ko", "예: 3조5000억 / 1,234억")
            if v is not None:
                calc_result([("영어", t) for t in ct.en_number(v)])
            raw = st.text_input("영어 → 한글", key="calc_en_en", placeholder="예: $3.5 billion / 350 million / 1.2tn")
            if raw.strip():
                n = ct.parse_en(raw)
                if n is None:
                    st.caption(f"'{raw}'을(를) 읽지 못했어요. 예: 3.5 billion, 350m, 1.2tn")
                else:
                    calc_result([("한글", ct.kor_number(n)), ("숫자", ct.num_text(n))])
        with tabs[4]:
            a, _ = calc_num("시작 값", "calc_g_a", "예: 100")
            b, _ = calc_num("끝 값", "calc_g_b", "예: 200")
            years = st.number_input("기간 (년)", min_value=0.0, value=5.0, step=1.0, key="calc_g_n",
                                    help="2020년→2025년이면 5년 (끝 연도 − 시작 연도)")
            g = ct.cagr(a, b, years)
            if a is not None and b is not None:
                calc_result([("연평균 성장률", f"{g:+.1f}%" if g is not None else "계산 안 함 (시작·끝은 0보다 커야 해요)"),
                             ("전체 증감률", f"{(b - a) / a * 100:+.1f}%" if a > 0 else "-")])
        with tabs[5]:
            text = st.text_area("한 줄에 하나씩 (이름 값 또는 값만)", key="calc_share", height=120,
                                placeholder="반도체 1,200\n자동차 600\n기타 200")
            st.button("비중 계산", key="calc_share_go", use_container_width=True)  # 폰은 Ctrl+Enter가 없어, 누르면 입력이 반영돼 계산된다
            items, total = ct.shares(text)
            if items:
                df = pd.DataFrame([{"항목": n, "값": ct.num_text(v, 2), "비중": f"{p:.1f}%" if p is not None else "-"} for n, v, p in items]
                                  + [{"항목": "합계", "값": ct.num_text(total, 2), "비중": "100%"}])
                st.dataframe(df, hide_index=True, height=(len(df) + 1) * 35 + 3)
        with tabs[6]:
            text = st.text_area("원고 붙여넣기", key="calc_text", height=160)
            st.button("글자 수 세기", key="calc_text_go", use_container_width=True)  # 폰은 Ctrl+Enter가 없어, 누르면 입력이 반영돼 계산된다
            if text.strip():
                t = ct.text_stats(text)
                calc_result([("공백 포함", f"{t['with_space']:}자"), ("공백 제외", f"{t['no_space']:}자"),
                             ("200자 원고지", f"{t['sheets']}매"), ("문장", f"{t['sentences']}개"), ("문단", f"{t['paras']}개")])
        with tabs[7]:
            today = datetime.now(KST).date()
            d1 = st.date_input("기준 날짜", value=today, key="calc_d1", format="YYYY.MM.DD")
            n = st.number_input("며칠 뒤 (음수면 앞)", value=0, step=1, key="calc_dn")
            if n:
                after, biz = d1 + timedelta(days=int(n)), ct.add_business_days(d1, int(n))
                calc_result([(f"{int(n)}일 뒤", f"{after:%Y.%m.%d}({WEEKDAYS[after.weekday()]})"),
                             (f"{int(n)}영업일 뒤", f"{biz:%Y.%m.%d}({WEEKDAYS[biz.weekday()]})")])
            d2 = st.date_input("다른 날짜 (사이 일수·D-day)", value=None, key="calc_d2", format="YYYY.MM.DD")
            if d2:
                gap = (d2 - d1).days
                calc_result([("사이 일수", f"{gap:+}일"), ("영업일", f"{ct.business_days_between(d1, d2):+}일"),
                             ("D-day", "D-day" if gap == 0 else (f"D-{gap}" if gap > 0 else f"D+{-gap}"))])
            st.caption("영업일은 토·일만 빼요 (공휴일은 빼지 않아요).")


with st.container(key="alarm_top"):  # 코드는 맨 아래지만 CSS로 제목 오른쪽 위에 작게 띄움
    render_calculator()
render_alarm_signup()  # 맨 아래
st.caption(f"updated {LAST_UPDATED}")  # 맨 아래
