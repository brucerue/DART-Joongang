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
print("예외:", [str(e.value)[:100] for e in at.exception])
for key in ("nw_통신_team_차팀", "cal_team_차팀"):
    btn = [b for b in at.button if b.key == key]
    print(key, "버튼", len(btn))
    if btn:
        btn[0].click(); at.run()
        print("  예외:", [str(e.value)[:100] for e in at.exception])
        print("  선택:", at.session_state["nw_통신_groups"] if key.startswith("nw") else at.session_state["cal_groups"])
        if key.startswith("nw"): print("  검색어:", at.session_state["nw_통신_terms"][:260])
        else:
            print("  캘린더 기업칸:", [w.value[:200] for w in at.text_area][:1] or [w.value[:200] for w in at.text_input][:3])
# 직접 중후장대만 골라 검색어 확인
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.session_state["nw_통신_groups"] = ["중후장대"]; at.run()
