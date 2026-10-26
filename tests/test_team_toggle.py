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

at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
fails=[]
def check(c,m):
    print(("  ✅ " if c else "  ❌ ")+m)
    if not c: fails.append(m)
for tab,key,grp in (("뉴스","nw_통신_team_차팀","nw_통신_groups"),("캘린더","cal_team_차팀","cal_groups")):
    btn=lambda: [b for b in at.button if b.key==key][0]
    btn().click(); at.run()
    sel1=list(at.session_state[grp]); check(len(sel1)>=4, f"{tab}: 1번 누르면 팀 섹터 선택 {sel1}")
    btn().click(); at.run()
    check(list(at.session_state[grp])==[], f"{tab}: 다시 누르면 해제 {list(at.session_state[grp])}")
    check(not at.exception, f"{tab}: 예외 없음")
    # 팀 선택 + 다른 섹터 따로 -> 다시 누르면 다른 섹터만 남음
    btn().click(); at.run()
    extra=["반도체"]
    if extra:
        at.session_state[grp]=sel1+extra; at.run()
        btn().click(); at.run()
        check(list(at.session_state[grp])==extra, f"{tab}: 추가 섹터는 유지 {list(at.session_state[grp])}")
for tab,key,grp,want in (("뉴스","nw_통신_team_재계팀","nw_통신_groups",["반도체","전자","항공","재계"]),("캘린더","cal_team_재계팀","cal_groups",["반도체","전자","항공"])):
    b=[x for x in at.button if x.key==key]
    check(len(b)==1, f"{tab}: 재계팀 칩 있음")
    if b:
        b[0].click(); at.run()
        check(sorted(at.session_state[grp])==sorted(want) and not at.exception, f"{tab}: 재계팀 선택 {list(at.session_state[grp])}")
print("실패",fails)
