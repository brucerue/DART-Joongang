import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, requests
os.environ.setdefault("DART_API_KEY", "x"*40)
import pandas as pd
CSV = "corp_code,corp_name,stock_code\n" + "\n".join([f"0{i:07d},비상장사{i}," for i in range(52000)] + ["00126380,삼성전자,005930"]) + "\n"
class R:
    status_code = 200; text = "{}"; content = b"{}"; ok = True
    def json(self): return {}
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode("utf-8"); return r
    return R()
requests.get = fake_get
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["00126380"], "corp_name": ["삼성전자"], "stock_code": ["005930"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest

"""재무 분석 '국내': 첫 줄은 묶음(차·배터리 …), 묶음이면 둘째 줄에서 낱개 섹터를 골라 합산 / 주가는 묶음 섹터 (가짜 응답)"""
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.radio(key="fin_mode").set_value("국내").run()
top = at.radio(key="ind_top")
check(list(top.options)[:4] == ["차·배터리", "중후장대", "에너지·화학", "반도체·전자"] and "자동차" not in list(top.options), f"첫 줄은 묶음 {list(top.options)}")
check(not [r for r in at.radio if r.key == "ind_group"], "처음엔 둘째 줄 없음")
top.set_value("차·배터리").run()
sub = [r for r in at.radio if r.key == "ind_group"]
check(sub and list(sub[0].options) == ["자동차", "배터리", "타이어"], "차·배터리 -> 둘째 줄 자동차·배터리·타이어")
check(at.session_state["ind_corps"] == "", "묶음만 고르면 기업 칸은 아직 비어 있음")
sub[0].set_value("배터리").run()
check(at.session_state["ind_corps"].startswith("LG에너지솔루션"), f"배터리 -> 배터리 기업 채움 {at.session_state['ind_corps'][:30]}")
at.radio(key="ind_group").set_value("타이어").run()
check(at.session_state["ind_corps"] == "한국앤컴퍼니, 한국타이어앤테크놀로지, 금호타이어, 넥센타이어", f"타이어 -> 타이어 4곳 {at.session_state['ind_corps']}")
at.radio(key="ind_group").set_value("자동차").run()
check(at.session_state["ind_corps"] == "현대자동차, 기아, 현대모비스", "자동차 -> 완성차·모비스 3곳")
at.radio(key="ind_top").set_value("항공").run()
check(not [r for r in at.radio if r.key == "ind_group"] and at.session_state["ind_corps"].startswith("대한항공"), "묶지 않은 섹터(항공)는 바로 채움")
at.radio(key="ind_top").set_value("중후장대").run()
sub = [r for r in at.radio if r.key == "ind_group"]
check(sub and list(sub[0].options) == ["조선·해운", "방산", "철강"] and sub[0].value is None, "중후장대 -> 조선·해운·방산·철강 (새로 고르기)")
check(not at.exception, "오류 없음")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
