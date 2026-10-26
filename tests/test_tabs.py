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
def labels(at): return [t.label for t in at.tabs]
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not exc(at), f"기본 화면 오류 없음 {exc(at)}")
lab = labels(at); print("   탭(일반):", lab)
TOP = ["오늘의 현황", "기업 브리핑", "재무 분석", "주가 분석", "공시 분석", "산업 뉴스", "AI 무역 통계", "실적 캘린더", "정기보고서", "재계 순위"]
check(lab[:10] == TOP, f"탭 순서 {lab[:10]}")
check("사용 통계" not in lab and "타임라인" not in lab, "일반 주소: 관리자 탭 없음")
check(len([r for r in at.radio if r.key == "tr_view"]) == 1, "AI 무역 통계 탭 안의 보기 선택칸")
at2 = AppTest.from_file(REPO + "/app.py", default_timeout=300)
at2.query_params["lab"] = "1"; at2.run()
lab2 = labels(at2); print("   탭(관리자 ?lab=1):", lab2)
check(not exc(at2), f"관리자 주소 오류 없음 {exc(at2)}")
check(lab2[:10] == TOP and lab2[-2:] == ["사용 통계", "타임라인"], f"관리자 주소: 위 탭은 그대로, 맨 아래 사용 통계·타임라인 {lab2[-2:]}")
print("실패", fails); sys.exit(1 if fails else 0)
