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

"""보고 있는 탭만 그리기: 다른 탭의 화면은 그리지 않고, 탭을 옮겼다 돌아와도 고른 값은 남는다 (가짜 응답)"""
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
os.environ.pop("ALL_TABS", None)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not at.exception, f"오류 없음 {[str(e.value)[:150] for e in at.exception]}")
keys = {getattr(w, "key", None) for w in at.radio} | {getattr(w, "key", None) for w in at.get("button_group")}
check(at.session_state["main_tab"] == "오늘의 현황" and "fin_mode" not in keys and "news_mode" not in keys and "px_mode" not in keys, "처음엔 첫 탭(오늘의 현황)만 그림")
at.session_state["main_tab"] = "재무 분석"; at.run()
check(not at.exception and [r for r in at.radio if r.key == "fin_mode"] and not [r for r in at.radio if r.key == "news_mode"], "재무 분석으로 옮기면 그 탭만")
at.radio(key="fin_mode").set_value("국내").run()
at.radio(key="ind_top").set_value("차·배터리").run()
at.radio(key="ind_group").set_value("배터리").run()
corps = at.session_state["ind_corps"]
at.session_state["main_tab"] = "산업 뉴스"; at.run()
check(not at.exception and [r for r in at.radio if r.key == "news_mode"], "산업 뉴스 탭")
at.button_group(key="nw_통신_groups").set_value(["항공"]).run()
at.session_state["main_tab"] = "재무 분석"; at.run()
check(at.radio(key="fin_mode").value == "국내" and at.radio(key="ind_top").value == "차·배터리" and at.radio(key="ind_group").value == "배터리" and at.session_state["ind_corps"] == corps,
      "재무 분석으로 돌아오면 고른 값(국내·차·배터리·배터리·기업 칸) 그대로")
at.session_state["main_tab"] = "산업 뉴스"; at.run()
check(list(at.session_state["nw_통신_groups"]) == ["항공"], "산업 뉴스로 돌아와도 고른 섹터 그대로")
check(not at.exception and not [w for w in at.warning if "Session State" in str(w.value)], "경고 없음")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
