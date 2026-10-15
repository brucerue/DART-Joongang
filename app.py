"""
DART 매출액 / 영업이익 조회 (Streamlit)

기능: 연도별·분기별 조회, 전년(동기)·전분기 대비 증감률,
      흑자전환/적자전환/적자지속 표시, 억원/조원 단위,
      CSV·엑셀 다운로드, 공시 원문 링크

[설치]   pip install -r requirements.txt
[실행]   streamlit run app.py
"""

import io
import os
import re
import time
from datetime import date

import pandas as pd
import streamlit as st
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
MAX_YEAR_SPAN = 10  # 한 번에 조회 가능한 최대 연도 수 (API 사용량 보호)
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

# 표에서 눈에 띄게 보여줄 금액 열의 글자색 (밝은 테마·어두운 테마 모두에서 읽히는 중간 톤)
REVENUE_COLOR = "#1c7ed6"  # 매출액: 파랑
OPERATING_COLOR = "#e8590c"  # 영업이익: 주황


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
def collect_yearly(corp, start, end, tick):
    """증감률 계산을 위해 시작연도 전년도까지 함께 조회한다."""
    data = {}
    for y in range(start - 1, end + 1):
        df = fetch(corp, y, ANNUAL)
        data[y] = {"rev": pick(df, REVENUE_NAMES), "op": pick(df, OPERATING_NAMES), "link": link_of(df)}
        tick()

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


def collect_quarterly(corp, start, end, tick):
    data = {}
    for y in range(start - 1, end + 1):
        reports = []
        for code in (Q1, H1, Q3, ANNUAL):
            reports.append(fetch(corp, y, code))
            tick()
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
def fmt_amount(v, unit):
    if v is None:
        return "-"
    return f"{v / 1e12:,.2f}" if unit == "조원" else f"{v / 1e8:,.0f}"


def fmt_change(c):
    if c is None:
        return "-"
    if isinstance(c, str):
        return c
    return f"{c:+,.1f}%"


def num_amount(v, unit):
    if v is None:
        return None
    return round(v / 1e12, 2) if unit == "조원" else int(round(v / 1e8))


def sorted_records(records):
    # 최신 기간이 위로
    return sorted(records, key=lambda r: r["sort"], reverse=True)


def build_table(records, period, unit, for_export=False):
    quarterly = period == "분기별"
    yoy = "전년동기比" if quarterly else "전년比"
    amt = num_amount if for_export else fmt_amount

    rows = []
    for r in sorted_records(records):
        row = {}
        row["기간"] = r["label"]
        row["매출액"] = amt(r["rev"], unit)
        row[f"매출 {yoy}"] = fmt_change(r["rev_yoy"])
        if quarterly:
            row["매출 전분기比"] = fmt_change(r["rev_qoq"])
        row["영업이익"] = amt(r["op"], unit)
        row[f"영업이익 {yoy}"] = fmt_change(r["op_yoy"])
        if quarterly:
            row["영업이익 전분기比"] = fmt_change(r["op_qoq"])
        row["원문"] = r["link"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if for_export:
        df = df.rename(
            columns={"매출액": f"매출액({unit})", "영업이익": f"영업이익({unit})", "원문": "공시원문링크"}
        )
    return df


def safe_filename(text):
    """파일 이름에 쓸 수 없는 문자(공백, 슬래시 등)를 밑줄로 바꾼다."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "기업"


# ------------------------------------------------------------
# 화면
# ------------------------------------------------------------
st.set_page_config(page_title="DART 매출·영업이익 조회", layout="wide")
st.title("매출액 · 영업이익 조회")

if not API_KEY:
    st.error(
        "DART API 키가 설정되지 않았습니다. "
        "Streamlit secrets(DART_API_KEY) 또는 환경변수 DART_API_KEY에 키를 등록해주세요."
    )
    st.stop()

this_year = date.today().year

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
        total = (end - start + 2) * (4 if quarterly else 1)  # 증감률 계산용 전년도 포함
        done = [0]
        progress = st.progress(0.0, text="조회 중입니다...")

        def tick():
            done[0] += 1
            progress.progress(min(done[0] / total, 1.0), text=f"조회 중입니다... ({done[0]}/{total})")

        collect = collect_quarterly if quarterly else collect_yearly
        records = collect(corp, start, end, tick)
        progress.empty()

        st.session_state["result"] = {"corp": corp, "records": records, "period": period}

result = st.session_state.get("result")

if result:
    if not result["records"]:
        st.warning("조회된 데이터가 없습니다. 기업명(종목코드)과 연도를 확인해주세요.")
    else:
        unit = st.radio("금액 단위", ["억원", "조원"], horizontal=True, key="unit")
        is_q = result["period"] == "분기별"

        notes = [result["corp"], f"단위: {unit}", "연결재무제표 기준", "증감률은 각 기간 보고서 수치로 계산한 값"]
        if is_q:
            notes.append("4분기 = 연간 − 3분기 누적(계산값)")
        st.caption(" · ".join(notes))
        st.caption("영업이익이 적자였던 구간은 증감률 대신 흑자전환 · 적자전환 · 적자지속으로 표시합니다. 원문 링크는 해당 기간 보고서(4분기는 사업보고서)로 연결됩니다.")

        table = build_table(result["records"], result["period"], unit)
        styled = table.style.set_properties(
            subset=["매출액"], **{"font-weight": "bold", "color": REVENUE_COLOR}
        ).set_properties(subset=["영업이익"], **{"font-weight": "bold", "color": OPERATING_COLOR})
        st.dataframe(
            styled,
            hide_index=True,
            column_config={"원문": st.column_config.LinkColumn("원문", display_text="공시 보기")},
        )

        export = build_table(result["records"], result["period"], unit, for_export=True)
        base_name = f"DART_{safe_filename(result['corp'])}_{result['period']}_{date.today():%Y%m%d}"
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
