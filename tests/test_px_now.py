import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, requests
os.environ["DART_API_KEY"] = "x"*40
sys.path.insert(0, REPO)
D = FIX
PR = json.load(open(f"{D}/fx_prices.json")); IN = json.load(open(f"{D}/fx_intraday.json"))
class R:
    status_code = 200; ok = True; text = "{}"
    def __init__(self, p=None): self._p = p if p is not None else {}
    def json(self): return self._p
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "prices.json" in url: return R(PR)
    if "intraday.json" in url: return R(IN)
    if "corp_codes" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = b"corp_code,corp_name,stock_code\n"; return r
    return R()
requests.get = fake_get
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url)
requests.Session = _S
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(list(at.radio(key="px_mode").options) == ["장중 현황", "시세판", "업계 비교", "이벤트"], "보기에 장중 현황")
at.radio(key="px_mode").set_value("장중 현황").run()
check(not at.exception, f"장중 현황 오류 없음 {at.exception}")
dfs = [d.value for d in at.dataframe if "오늘 흐름" in d.value.columns]
check(len(dfs) == 1 and len(dfs[0]) > 50 and "구간 변동(30분)" in dfs[0].columns, f"종목 표 {len(dfs[0]) if dfs else 0}곳")
print(dfs[0].head(3).drop(columns=["오늘 흐름"]).to_string() if dfs else "")
md = " ".join(m.value for m in at.markdown)
check("기준 시각" in md and "10.26 10:15" in md, "기준 시각 카드")
at.session_state["px_now_groups"] = ["반도체"]; at.run()
dfs = [d.value for d in at.dataframe if "오늘 흐름" in d.value.columns]
check(not at.exception and 2 <= len(dfs[0]) <= 4 and "삼성전자" in list(dfs[0]["종목"]), f"반도체만 {list(dfs[0]['종목']) if dfs else None}")
at.radio(key="px_now_window").set_value("1시간").run()
check(not at.exception and "구간 변동(1시간)" in [c for d in at.dataframe for c in d.value.columns], "구간 1시간")
at.session_state["px_now_groups"] = []; at.run()
[b for b in at.button if str(b.key) == "pxnow_team_차팀"][0].click().run()
check(not at.exception and sorted(at.session_state["px_now_groups"]) == sorted(["자동차", "배터리", "조선·해운", "방산", "철강", "정유·석화", "에너지"]), f"차팀 → 중후장대 풀림 {at.session_state.get('px_now_groups')}")
print("실패", fails)
