"""현대차·기아 판매실적: 수집기(가짜 DART)와 앱 화면(재무 분석 '차 판매', 기업 브리핑 계산, 오늘의 현황 한 줄) (실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")
import os, sys, types, json, requests
sys.path.insert(0, REPO)
os.environ.setdefault("DART_API_KEY", "x" * 40)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake")
from datetime import datetime, timedelta, timezone
import sales_calc as slc, sales_data as sd
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
# 1) 수집기: 목록에서 '영업(잠정)실적'만, 판매대수 없는 공시는 skip, 정정은 나중 것이 이김
docs = {"1": open(FIX + "/sales_kia_2025.txt", encoding="utf-8").read(), "2": "분기 잠정실적 매출액 | 1,000 |", "3": open(FIX + "/sales_kia.txt", encoding="utf-8").read()}
class R:
    def __init__(s, p): s._p = p
    def json(s): return s._p
def fake_get(url, params=None, timeout=None):
    if params["corp_code"] != slc.SALES_CORPS["기아"]:
        return R({"status": "013"})
    return R({"status": "000", "total_page": 1, "list": [
        {"rcept_no": "1", "rcept_dt": "20250901", "report_nm": "영업(잠정)실적(공정공시)"},
        {"rcept_no": "2", "rcept_dt": "20251030", "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)"},
        {"rcept_no": "3", "rcept_dt": "20261102", "report_nm": "영업(잠정)실적(공정공시)"},
        {"rcept_no": "9", "rcept_dt": "20261102", "report_nm": "자기주식처분결정"}]})
calls = []
def text_of(no):
    calls.append(no); return docs[no]
now = datetime(2026, 11, 3, tzinfo=timezone(timedelta(hours=9)))
d = sd.collect({}, "k", "20160101", 100, get=fake_get, text_of=text_of, now=now)
m = d["corps"]["기아"]["months"]
check(sorted(m) == ["2025-08", "2026-10"] and m["2026-10"]["합계"] == 291219 and m["2026-10"]["released"] == "2026-11-02", f"모은 달 {sorted(m)}")
check(d["seen"] == {"1": "ok", "2": "skip", "3": "ok"} and "9" not in calls, "판매대수 없는 공시는 skip, 다른 보고서는 안 받음")
calls.clear(); d2 = sd.collect(d, "k", "20160101", 100, get=fake_get, text_of=text_of, now=now)
check(calls == [], "다음 실행: 이미 본 공시는 다시 안 받음")
d["parser"] = "old"; calls.clear(); sd.collect(d, "k", "20160101", 100, get=fake_get, text_of=text_of, now=now)
check(calls == ["1", "2", "3"], "읽는 규칙이 바뀌면 처음부터 다시 읽음")
# 2) 계산
months = {f"2025-{k:02d}": {"합계": 100} for k in range(1, 13)}
months.update({f"2026-{k:02d}": {"합계": 110} for k in range(1, 11)})
check(slc.ytd(months, "2026-10", "합계") == 1100 and slc.ytd(months, "2025-10", "합계") == 1000, "1~10월 누계")
yr = {r["year"]: r for r in slc.year_rows(months, "합계", "2026-10")}
check(yr["2025"]["full"] == 1200 and yr["2026"]["full"] is None and round(yr["2026"]["same_yoy"], 1) == 10.0, "연도별: 연간·같은 기간 비교")
# 3) 앱 화면 (모아 둔 sales.json을 가짜로)
today = datetime.now(timezone(timedelta(hours=9))).date()
SALES = {"corps": {"현대자동차": {"months": {**{f"2025-{k:02d}": {"국내": 50000, "해외": 280000, "합계": 330000, "released": "2025-01-01"} for k in range(1, 13)},
                                         **{f"2026-{k:02d}": {"국내": 49000, "해외": 280000, "합계": 329000, "released": today.isoformat() if k == 10 else "2026-01-01"} for k in range(1, 11)}}},
                   "기아": {"months": {f"2026-{k:02d}": {"국내": 51000, "해외": 239000, "특수": 900, "합계": 290900, "released": today.isoformat() if k == 10 else "2026-01-01"} for k in range(1, 11)}}}}
import pandas as pd
CSV = "corp_code,corp_name,stock_code\n00126380,삼성전자,005930\n00164742,현대자동차,005380\n00106641,기아,000270\n"
class RR:
    status_code = 200; text = "{}"; content = b"{}"; ok = True
    def __init__(s, p=None): s._p = p if p is not None else {}
    def json(s): return s._p
    def raise_for_status(s): pass
def app_get(url, *a, **k):
    if "/sales/sales.json" in url: return RR(SALES)
    if url.endswith("/recent.json"): return RR({"updated": "2026-11-02T10:00", "items": []})
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode(); return r
    return RR()
requests.get = app_get
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["00126380", "00164742", "00106641"], "corp_name": ["삼성전자", "현대자동차", "기아"], "stock_code": ["005930", "005380", "000270"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check("차 판매" not in list(at.radio(key="fin_mode").options), "재무 분석에 '차 판매' 메뉴는 없음")
recs = [{"label": f"{y}", "sort": f"{y}", "rev": 1e14, "op": 1e13, "net": 1e13, "rev_yoy": 1.0, "op_yoy": 1.0, "net_yoy": 1.0, "bs": {}, "link": "x"} for y in (2024, 2025)]
at.session_state["result"] = {"corp": "현대자동차", "records": recs, "period": "연도별"}; at.run()
check(not at.exception, f"현대차 조회: 오류 없음 {[str(e.value)[:150] for e in at.exception]}")
md = "".join(m.value for m in at.markdown)
check("현대차 판매 실적" in md and "현대차 10월 판매" in md and "329,000대" in md and "1~10월 누적" in md, "현대차를 조회하면 판매 실적이 함께 (카드)")
mon = [d.value for d in at.dataframe if "월" in d.value.columns]
check(mon and list(mon[0].columns) == ["월", "판매(합계)", "전년比", "전월比"] and mon[0].iloc[0]["월"] == "2026.10" and mon[0].iloc[0]["판매(합계)"] == "329,000", "월별 표")
check(mon and mon[0].iloc[0]["전년比"] == "-0.3%", "전년比")
at.radio(key="sales_part").set_value("국내").run()
mon = [d.value for d in at.dataframe if "월" in d.value.columns]
check(mon and mon[0].iloc[0]["판매(국내)"] == "49,000", "구분: 국내")
at.radio(key="sales_view").set_value("연도별").run()
yr = [d.value for d in at.dataframe if "연도" in d.value.columns]
check(not at.exception and yr and list(yr[0].columns) == ["연도", "연간", "1~10월", "1~10월 전년比"] and yr[0].iloc[1]["연간"] == "600,000", f"연도별 표 {yr and list(yr[0].columns)}")
at.session_state["result"] = {"corp": "삼성전자", "records": recs, "period": "연도별"}; at.run()
check("판매 실적" not in "".join(m.value for m in at.markdown), "다른 기업은 판매 실적 없음")
# 오늘의 현황 한 줄 (공시 난 지 3일 안)
at2 = AppTest.from_file(REPO + "/app.py", default_timeout=300); at2.run()
from importlib import import_module
check(not at2.exception, "기본 화면 오류 없음")
at2.session_state["td_groups"] = ["차·배터리"]; at2.run()
md2 = "".join(m.value for m in at2.markdown)
check(not at2.exception and "10월 판매" in md2 and "현대차 <b>329,000대</b> (-0.3%)" in md2 and "기아 <b>290,900대</b> (-)" in md2, "오늘의 현황 차·배터리: 공시 난 날 '10월 판매' 한 줄")
at2.session_state["td_groups"] = ["반도체"]; at2.run()
check("10월 판매" not in "".join(m.value for m in at2.markdown), "다른 섹터엔 없음")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
