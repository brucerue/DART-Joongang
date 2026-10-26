import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, re
os.environ["DART_API_KEY"] = "x"*40
import requests, pandas as pd
PAGE = """node1['text'] = "III. 재무에 관한 사항";
 var node2 = {};
 node2['text'] = "1. 요약재무정보";
 node2['id'] = "20"; node2['rcpNo'] = "20221114000599"; node2['dcmNo'] = "8868112"; node2['eleId'] = "20"; node2['offset'] = "563793"; node2['length'] = "26995"; node2['dtd'] = "dart3.xsd";
 var node2 = {};
 node2['text'] = "2. 연결재무제표";
 node2['id'] = "21";
 node2['rcpNo'] = "20221114000599";
 node2['dcmNo'] = "8868112";
 node2['eleId'] = "21";
 node2['offset'] = "591034";
 node2['length'] = "132863";
 node2['dtd'] = "dart3.xsd";
 var node2 = {};
 node2['text'] = "3. 연결재무제표 주석";
 node2['rcpNo'] = "20221114000599"; node2['dcmNo'] = "8868112"; node2['eleId'] = "22"; node2['offset'] = "1"; node2['length'] = "2"; node2['dtd'] = "dart3.xsd";
"""
calls = []
class R:
    status_code = 200; ok = True; content = b"{}"
    def __init__(self, text="{}"): self.text = text
    def json(self): return {}
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "dsaf001/main.do" in url:
        calls.append(k.get("params") or url)
        return R(PAGE if "20221114000599" in url else "<html>목차 없음</html>")
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
import ast
src = open(REPO + "/app.py", encoding="utf-8").read()
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
# 순수 함수만 뽑아서 시험
ns = {"re": re}
for name in ("parse_dart_consol_node",):
    m = re.search(rf"^def {name}\(.*?(?=^\S)", src, re.S | re.M)
    exec(m.group(0), ns)
node = ns["parse_dart_consol_node"](PAGE)
check(node and node["eleId"] == "21" and node["offset"] == "591034" and node["dtd"] == "dart3.xsd", f"연결재무제표 항목을 찾음(주석 아님) {node}")
check(ns["parse_dart_consol_node"]("<html>아무것도 없음</html>") is None, "목차 없으면 None")
# 앱 전체: 재무 조회 표의 원문 링크
from streamlit.testing.v1 import AppTest
L1 = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20221114000599"
L2 = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20230315000001"
recs = [{"label": "2021", "sort": "2021", "rev": 7.6e14, "op": 9.2e12, "net": 7.2e12, "rev_yoy": 32.1, "op_yoy": 284.4, "net_yoy": 302.4, "rev_qoq": None, "op_qoq": None, "net_qoq": None, "bs": {}, "link": L1},
        {"label": "2022", "sort": "2022", "rev": 8.4e14, "op": 4.8e12, "net": 3.5e12, "rev_yoy": 11.0, "op_yoy": -47.5, "net_yoy": -50.5, "rev_qoq": None, "op_qoq": None, "net_qoq": None, "bs": {}, "link": L2}]
at = AppTest.from_file(REPO + "/app.py", default_timeout=120)
at.session_state["result"] = {"corp": "POSCO홀딩스", "records": recs, "period": "연도별"}
at.run()
check(not at.exception, f"예외 없음 {[str(e.value)[:120] for e in at.exception]}")
df = [d.value for d in at.dataframe if "원문" in list(d.value.columns)]
links = list(df[0]["원문"]) if df else []
check(len(links) == 2 and "report/viewer.do" in links[0] and "eleId=21" in links[0] and "rcpNo=20221114000599" in links[0], f"2021 링크는 연결재무제표 {links[:1]}")
check(len(links) == 2 and links[1] == L2, f"목차를 못 찾은 보고서는 원래 링크 그대로 {links[1:]}")
n = len(calls); at.run()
check(len(calls) == n, "같은 보고서는 다시 묻지 않음(저장)")
print("실패", fails); sys.exit(1 if fails else 0)
