import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, pathlib
os.environ["DART_API_KEY"] = "x"*40
os.environ["ANTHROPIC_API_KEY"] = "fake"
sys.path.insert(0, REPO)
import requests
def fake_get(url, *a, **k):
    r = requests.models.Response(); r.status_code = 200
    r._content = b"corp_code,corp_name,stock_code\n" if "corp_codes" in url else b"{}"
    return r
requests.get = fake_get
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url)
requests.Session = _S
from streamlit.testing.v1 import AppTest
FAKE = r'''
import json as _j
CALLS = []
def ai_generate(prompt, system=None, json_mode=False):
    CALLS.append(prompt[:40])
    if "회사·기간" in prompt or ("metrics" in prompt and "질문:" in prompt):
        return _j.dumps({"summary":"현대차·기아 영업이익률 비교","corps":["현대차","기아","없는회사"],"period":"연도별","start":2021,"end":2025,"metrics":["영업이익","영업이익률","ROE","가짜지표"],"note":""}), "fake"
    if '"kinds"' in prompt and "질문:" in prompt:
        return _j.dumps({"summary":"조선사 수주","corps":[],"sectors":["조선·해운"],"kinds":["공급계약"],"words":[],"start":"2026-09-26","end":"2026-10-25","fields":["계약금액","계약상대방"],"sum":"계약금액"}), "fake"
    if "원문에서 다음 항목" in prompt:
        if "버뮤다" in prompt: return _j.dumps({"계약금액":"1,000,000,000,000","계약상대방":"버뮤다 선사"}), "fake"
        return _j.dumps({"계약금액":"9,999억원","계약상대방":"아시아 선사"}), "fake"
    if '"blocks"' in prompt and "질문:" in prompt:
        return _j.dumps({"summary":"LG엔솔 북미 생산능력","corp":"LG에너지솔루션","kind":"최신","year":None,"blocks":["biz_plant","nope"],"query":"북미 공장 생산능력"}), "fake"
    if "[발췌]" in prompt:
        return _j.dumps({"answer":"미국 공장 생산능력 표가 있다","found":[{"block":"biz_plant","quote":"미국 미시간 | 40GWh","why":"북미 생산능력"},{"block":"biz_plant","quote":"지어낸 문장 100GWh","why":"x"}]}), "fake"
    return "{}", "fake"
def resolve_corp_code(corp):
    return None if "없는" in corp else "C" + str(abs(hash(corp)) % 1000)
def suggest_corps(q, **k): return []
def canonical_corp(c):
    c = str(c or '').strip()
    return {'현대차': '현대자동차', 'LG엔솔': 'LG에너지솔루션'}.get(c, c)
def collect_yearly(code, s, e, tick):
    out = []
    for i, y in enumerate(range(s, e + 1)):
        out.append({"label": str(y), "sort": (y, 0), "rev": 1e12 * (i + 1), "op": 1e11 * (i + 1), "net": 5e10,
                    "bs": {"assets": 5e12, "liab": 2e12, "equity": 3e12, "cash": 1e11}})
    return out
collect_quarterly = collect_yearly
def collect_disclosures(code, s, e, kind=""):
    return [{"rcept_no": "A1" + code, "rcept_dt": "20261020", "report_nm": "단일판매ㆍ공급계약체결", "corp_code": code},
            {"rcept_no": "B2" + code, "rcept_dt": "20261021", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서", "corp_code": code}], 2, False
def fetch_document_text(session, store, rno):
    return "계약금액 1,000,000,000,000 원 계약상대방 버뮤다 선사" if rno.startswith("A1") else "계약금액 9,999억원 상대방 아시아 선사"
def periodic_reports(code):
    return [{"rcept_no": "R1", "rcept_dt": "20260814", "report_nm": "반기보고서 (2026.06)"}]
def periodic_blocks(rno):
    return {"biz_plant": "생산능력\n공장 | 능력\n미국 미시간 | 40GWh\n폴란드 | 70GWh"}
'''
src = open(REPO + "/app.py", encoding="utf-8").read().replace("\nTAB_NAMES = [", FAKE + "\nTAB_NAMES = [", 1)
p = pathlib.Path(REPO + "/_t_ai.py"); p.write_text(src, encoding="utf-8")
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
try:
    at = AppTest.from_file(str(p), default_timeout=120); at.run()
    check(not at.exception, f"첫 화면 {at.exception}")
    # 재무
    at.radio(key="fin_mode").set_value("AI 검색").run()
    at.text_area(key="fai_text").input("현대차 기아 영업이익률 비교").run()
    at.button(key="fai_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("현대차" in md and "기아" in md and "ROE" in md and "가짜지표" not in md, "재무: 조건 표시, 허용 지표만")
    at.button(key="fai_run").click().run()
    check(not at.exception, f"재무 조회 {at.exception}")
    dfs = [d.value for d in at.dataframe]
    t = next((d for d in dfs if "기아 영업이익률" in d.columns), None)
    print(t.head(3) if t is not None else None)
    check(t is not None and list(t["기간"]) == ["2021","2022","2023","2024","2025"] and "기아 영업이익률" in t.columns, "재무: 회사×지표 표")
    check(any("없는회사" in w.value for w in at.warning), "재무: 못 찾은 회사 안내")
    # 공시
    at.radio(key="disc_mode").set_value("AI 검색").run()
    at.text_area(key="dai_text").input("조선사 수주 합계").run()
    at.button(key="dai_go").click().run()
    at.button(key="dai_run").click().run()
    check(not at.exception, f"공시 찾기 {at.exception}")
    t = next(d.value for d in at.dataframe if "보고서명" in d.value.columns and "접수일" in d.value.columns and "기업" in d.value.columns)
    check(len(t) > 0 and all("공급계약" in x for x in t["보고서명"]), f"공시: 공급계약만 {len(t)}건")
    at.button(key="dai_nums_go").click().run()
    check(not at.exception, f"공시 숫자 {at.exception}")
    nt = next(d.value for d in at.dataframe if "보고서명" in d.value.columns and "접수일" in d.value.columns and "기업" in d.value.columns)
    print([list(d.value.columns) for d in at.dataframe][:8]); print([b.label for b in at.button][:30]); print([m.value[:60] for m in at.markdown if "합계" in m.value or "숫자" in m.value])
    md = " ".join(m.value for m in at.markdown)
    check("합계" in md and "7조원" in md, "공시: 원문 확인 금액 합계")
    at.radio(key="periodic_view").run()
    check("AI 찾기" not in at.radio(key="periodic_view").options and not at.exception, "정기보고서: AI 찾기 없음")
finally:
    p.unlink()
print("실패", fails)
