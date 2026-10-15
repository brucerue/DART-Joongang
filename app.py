"""
DART 매출액 / 영업이익 조회 (Streamlit, 단순 버전)

[설치]   pip install -r requirements.txt
[실행]   streamlit run app.py
"""

import os
import time
from datetime import date

import pandas as pd
import streamlit as st
import OpenDartReader

# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------
FS_DIV = "CFS"  # CFS = 연결재무제표, OFS = 별도(개별)재무제표
MAX_YEAR_SPAN = 10  # 한 번에 조회 가능한 최대 연도 수 (API 사용량 보호)


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
OPERATING_NAMES = ["영업이익", "영업이익(손실)"]

# 보고서 코드: 1분기 / 반기 / 3분기 / 사업보고서(연간)
Q1, H1, Q3, ANNUAL = "11013", "11012", "11014", "11011"


# ------------------------------------------------------------
# 데이터 조회 함수
# ------------------------------------------------------------
@st.cache_resource
def get_dart():
    return OpenDartReader(API_KEY)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _fetch_cached(corp, year, reprt_code):
    """같은 조회는 6시간 동안 저장해두고 재사용 (오류는 저장되지 않음)."""
    df = get_dart().finstate_all(corp, year, reprt_code=reprt_code, fs_div=FS_DIV)
    time.sleep(0.2)  # API 호출 제한 보호
    return df if df is not None else pd.DataFrame()


def fetch(corp, year, reprt_code):
    """전체 재무제표 조회. 데이터가 없거나 오류면 None."""
    try:
        df = _fetch_cached(corp, year, reprt_code)
        return df if not df.empty else None
    except Exception:
        return None


def to_int(x):
    try:
        return int(str(x).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


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
    return to_int(rows.iloc[0][column])


def fmt(v):
    """원 -> 억원 (천 단위 쉼표). 값이 없으면 '-'."""
    return "-" if v is None else f"{v / 1e8:,.0f}"


# ------------------------------------------------------------
# 연도별 / 분기별 계산
# ------------------------------------------------------------
def yearly_rows(corp, start, end):
    rows = []
    for year in range(start, end + 1):
        df = fetch(corp, year, ANNUAL)
        rev = pick(df, REVENUE_NAMES)
        op = pick(df, OPERATING_NAMES)
        if rev is not None or op is not None:
            rows.append({"기간": f"{year}", "매출액": fmt(rev), "영업이익": fmt(op)})
    return rows


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


def quarterly_rows(corp, start, end):
    rows = []
    for year in range(start, end + 1):
        r1, r2, r3, r4 = (fetch(corp, year, c) for c in (Q1, H1, Q3, ANNUAL))
        revs = quarter_values(r1, r2, r3, r4, REVENUE_NAMES)
        ops = quarter_values(r1, r2, r3, r4, OPERATING_NAMES)
        for i in range(4):
            if revs[i] is not None or ops[i] is not None:
                rows.append({"기간": f"{year} {i + 1}Q", "매출액": fmt(revs[i]), "영업이익": fmt(ops[i])})
    return rows


# ------------------------------------------------------------
# 화면
# ------------------------------------------------------------
st.set_page_config(page_title="DART 매출·영업이익 조회")
st.title("매출액 · 영업이익 조회")

if not API_KEY:
    st.error(
        "DART API 키가 설정되지 않았습니다. "
        "Streamlit secrets(DART_API_KEY) 또는 환경변수 DART_API_KEY에 키를 등록해주세요."
    )
    st.stop()

this_year = date.today().year

with st.form("search"):
    corp = st.text_input("기업명 또는 종목코드", value="삼성전자")
    period = st.radio("조회 단위", ["연도별", "분기별"], horizontal=True)
    c1, c2 = st.columns(2)
    start = c1.number_input("시작연도", min_value=2015, max_value=this_year, value=2021, step=1)
    end = c2.number_input("종료연도", min_value=2015, max_value=this_year, value=this_year - 1, step=1)
    submitted = st.form_submit_button("조회")

if submitted:
    if start > end:
        st.error("시작연도가 종료연도보다 늦을 수 없습니다.")
    elif end - start + 1 > MAX_YEAR_SPAN:
        st.error(f"한 번에 최대 {MAX_YEAR_SPAN}년까지만 조회할 수 있습니다.")
    else:
        with st.spinner("조회 중입니다..."):
            if period == "연도별":
                rows = yearly_rows(corp.strip(), int(start), int(end))
            else:
                rows = quarterly_rows(corp.strip(), int(start), int(end))

        if not rows:
            st.warning("조회된 데이터가 없습니다. 기업명(종목코드)과 연도를 확인해주세요.")
        else:
            st.caption("단위: 억원 · 연결재무제표 기준" + (" · 4분기 = 연간 − 3분기 누적(계산값)" if period == "분기별" else ""))
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
