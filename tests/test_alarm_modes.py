import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, requests
os.environ["DART_API_KEY"] = "x"*40
import pandas as pd
class R:
    status_code = 200; text = "{}"; ok = True; content = b"{}"
    def json(self): return {}
    def raise_for_status(self): pass
requests.get = lambda *a, **k: R()
class _S(requests.Session):
    def get(self, *a, **k): return R()
requests.Session = _S
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["00126380"], "corp_name": ["삼성전자"], "stock_code": ["005930"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
ms = at.multiselect(key="alarm_modes")
print("옵션", ms.options, "기본", ms.value)
assert list(ms.options) == ["통신", "단독", "일반", "공시"] and list(ms.value) == ["통신", "단독", "공시"]
at.session_state["alarm_sectors"] = ["자동차"]; at.run()
links = [l for l in at.get("link_button") if "t.me" in str(l.url)] if hasattr(at, "get") else []
import re
src = open(REPO + "/app.py", encoding="utf-8").read()
import importlib.util
print("alarm_link 확인은 함수 직접 호출로:")
ns = {}
exec("ALARM_SECTORS=" + src.split("ALARM_SECTORS = ")[1].split("\n")[0].split("#")[0] + "\nALARM_MODES=" + src.split("ALARM_MODES = ")[1].split("\n")[0].split("#")[0] + "\nALARM_BOT='bot'\n" + src[src.index("def alarm_link"):src.index("def render_alarm_signup")], ns)
u = ns["alarm_link"](["자동차"], ["통신", "일반"]); print(u)
assert u.endswith("m1010"), u
print("ok", at.exception)
