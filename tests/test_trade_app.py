import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, json, requests
os.environ.setdefault("DART_API_KEY", "x"*40)
import pandas as pd
MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
TRADE = json.load(open(FIX + "/trade_sample.json", encoding="utf-8"))
CSV = "corp_code,corp_name,stock_code\n" + "\n".join([f"0{i:07d},비상장사{i}," for i in range(52000)] + ["00126380,삼성전자,005930"]) + "\n"
class R:
    status_code = 200; text = "{}"; content = b"{}"; ok = True
    def __init__(self, p=None): self._p = p if p is not None else {}
    def json(self): return self._p
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "/trade/trade.json" in url:
        if MODE == "nodata": raise requests.ConnectionError("no branch")
        return R(TRADE)
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode("utf-8"); return r
    return R()
requests.get = fake_get
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["00126380"], "corp_name": ["삼성전자"], "stock_code": ["005930"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def exc(at): return [str(e.value)[:140] for e in at.exception]
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not exc(at), f"기본 화면 오류 없음 {exc(at)}")
radios = [r for r in at.radio if r.key == "tr_view"]
if MODE == "nodata":
    check(not radios and any("수출입 자료를 아직" in i.value for i in at.info), "자료 없으면 안내 문구")
else:
    check(len(radios) == 1, "수출 탭 보기 선택칸")
    def frames(at):
        out = []
        for d in at.dataframe:
            try: out.append(d.value)
            except Exception: pass
        return out
    for view in ("월간", "10일", "국가"):
        at.radio(key="tr_view").set_value(view).run()
        check(not exc(at), f"{view}: 오류 없음 {exc(at)}")
        fr = [f for f in frames(at) if any(c in list(f.columns) for c in ("수출", "누계"))]
        check(len(fr) >= 1, f"{view}: 표 {len(fr)}개")
        if fr: print("      ", fr[0].head(3).to_string().replace("\n", "\n       ")[:500])
    at.radio(key="tr_view").set_value("10일").run()
    at.radio(key="tr_t_kind").set_value("수입").run()
    check(not exc(at), "10일 단위: 수입 보기")
    at.selectbox(key="tr_t_month").select("202609").run()
    check(not exc(at), "10일 단위: 지난달 선택")
    at.radio(key="tr_view").set_value("월간").run()
    at.selectbox(key="tr_m_month").select("2025-03").run()
    check(not exc(at), "월간: 전년 자료 없는 달 선택(2025-03)")
    at.selectbox(key="tr_m_month").select("2024-10").run()
    check(not exc(at), "월간: 첫 달(2024-10, 전년 없음)")
print("실패", fails); sys.exit(1 if fails else 0)
