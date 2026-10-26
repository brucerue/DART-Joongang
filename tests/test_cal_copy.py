import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, requests, datetime as dtm
os.environ["DART_API_KEY"] = "x"*40
import pandas as pd
class R:
    status_code = 200; text = "{}"; ok = True; content = b"{}"
    def json(self): return {}
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = b"corp_code,corp_name,stock_code\n"; return r
    return R()
requests.get = fake_get
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url, *a, **k)
requests.Session = _S
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["1"], "corp_name": ["삼성전자"], "stock_code": ["005930"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest
today = dtm.date.today()
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300)
ev = [{"date": today + dtm.timedelta(days=2), "kind": "확정", "corp": "삼성전자", "purpose": "IR 설명회", "text": "3분기 실적 발표", "no": "20261023000001"}]
at.session_state["calendar"] = {"data": {"events": ev, "deadlines": {}, "unread": []}, "ws": today, "we": today + dtm.timedelta(days=7), "span": list(at.session_state["cal_span"] if "cal_span" in at.session_state else ["이번 주"])[0], "n": 2}
at.run()
check(not at.exception, f"예외 없음 {[str(e.value)[:120] for e in at.exception]}")
codes = [c.value for c in at.code]
tsv = [c for c in codes if c.startswith("날짜\t기업")]
check(len(tsv) == 1, f"복사용 글 {len(tsv)}개")
if tsv:
    lines = tsv[0].strip("\n").split("\n")
    check(lines[0] == "날짜\t기업\t개최 목적\t내용\t원문" and lines[1].split("\t")[1] == "삼성전자" and lines[1].split("\t")[4].startswith("https://dart.fss.or.kr/"), f"첫 줄 제목·값 {lines[:2]}")
    check(len(lines) == 2, "확정 일정 1건만")
check(not [t for t in at.toggle if t.key == "cal_est"], "예상 일정 스위치 없음")
src = open(REPO + "/app.py", encoding="utf-8").read()
check("전년 기준 예상" not in src, "앱에 전년 기준 예상 없음")
print("실패", fails); sys.exit(1 if fails else 0)
