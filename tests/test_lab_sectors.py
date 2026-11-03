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

"""준비 중인 섹터(유통·식품·뷰티·패션·제약·바이오)와 팀(유통팀·바이오팀)은 관리자 주소(?lab=1)에서만 보이고, 텔레그램 알람·팀 명령에는 없다"""
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
LAB = ["유통", "식품", "뷰티·패션", "제약·바이오"]
def pills(at, key):
    return [p for p in at.get("button_group") if getattr(p, "key", None) == key]
def options(at, key):
    got = pills(at, key)
    return [str(o.content) if hasattr(o, "content") else str(o) for o in got[0].options] if got else []
def team_keys(at, prefix):
    return [b.key for b in at.button if (b.key or "").startswith(prefix + "_team_")]

at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not at.exception, "일반 주소: 오류 없음")
td = options(at, "nw_통신_groups")
check(td and not any(x in td for x in LAB), f"일반 주소: 산업 뉴스 섹터에 준비 중 섹터 없음 {td}")
check(not any(k.endswith(("유통팀", "바이오팀")) for k in team_keys(at, "nw_통신")), "일반 주소: 유통팀·바이오팀 버튼 없음")
cal = options(at, "cal_groups")
check(cal and not any(x in cal for x in LAB), "일반 주소: 실적 캘린더 섹터에 없음")
alarm = [m for m in at.multiselect if m.key == "alarm_sectors"]
check(alarm and not any(x in list(alarm[0].options) for x in LAB), "일반 주소: 알람 받을 섹터에 없음")

at2 = AppTest.from_file(REPO + "/app.py", default_timeout=300); at2.query_params["lab"] = "1"; at2.run()
check(not at2.exception, "관리자 주소: 오류 없음")
td2 = options(at2, "nw_통신_groups")
check(all(x in td2 for x in LAB), f"관리자 주소: 산업 뉴스 섹터에 보임 {td2}")
check({"nw_통신_team_유통팀", "nw_통신_team_바이오팀"} <= set(team_keys(at2, "nw_통신")), "관리자 주소: 유통팀·바이오팀 버튼")
[b for b in at2.button if b.key == "nw_통신_team_유통팀"][0].click(); at2.run()
check(list(at2.session_state["nw_통신_groups"]) == ["유통", "식품", "뷰티·패션"], f"관리자 주소: 유통팀 누르면 세 섹터 {list(at2.session_state['nw_통신_groups'])}")
check(not at2.exception, "관리자 주소: 유통팀 선택 후 오류 없음")
cal2 = options(at2, "cal_groups")
check(all(x in cal2 for x in LAB), "관리자 주소: 실적 캘린더에 보임")
alarm2 = [m for m in at2.multiselect if m.key == "alarm_sectors"]
check(alarm2 and not any(x in list(alarm2[0].options) for x in LAB), "관리자 주소: 알람 받을 섹터에는 여전히 없음")

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake")
import importlib; sys.path.insert(0, REPO); nd = importlib.import_module("news_digest")
check(all(x in nd.A["NEWS_SECTORS"] for x in LAB), "수집기: 준비 중 섹터도 모아 둠")
check(set(nd.A["TEAM_PRESETS"]) == {"차팀", "재계팀"} and not any(x in nd.A["ALARM_SECTORS"] for x in LAB), "텔레그램: 팀·섹터 목록은 그대로")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
