import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os; os.environ["DART_API_KEY"]="x"*40
import json, requests, sys
sys.path.insert(0,REPO)
from streamlit.testing.v1 import AppTest
hist=json.load(open(FIX + "/ch.json"))
real=requests.get
class R:
    def __init__(s,j): s._j=j; s.status_code=200; s.text=json.dumps(j)
    def json(s): return s._j
    def raise_for_status(s): pass
    def __getattr__(s,k): raise AttributeError(k)
def fake(url,*a,**k):
    if "chaebol_history.json" in url: return R(hist)
    r=requests.models.Response(); r.status_code=200; r._content=b"corp_code,corp_name,stock_code\n"; return r
requests.get=fake
at=AppTest.from_file(REPO + "/app.py",default_timeout=60); at.run()
assert not at.exception,at.exception
sel=at.selectbox(key="cb_group")
sel.select_index(0).run()
assert not at.exception,at.exception
ex=[e for e in at.expander if "연도별 이력" in e.label]
print([e.label for e in ex])
assert ex
sel.select_index(len(sel.options)-1).run()   # 마지막 그룹(이력 일부 없음 가능)
assert not at.exception,at.exception
for i in (5,20):
    sel.select_index(i).run(); assert not at.exception,at.exception
print("OK", [e.label for e in at.expander if "연도별" in e.label])
sel.select_index(0).run()
for df in at.dataframe[:0]: pass
ex=[e for e in at.expander if "연도별 이력" in e.label][0]
print(ex.dataframe[0].value)
sb=[x for x in ex.selectbox]; print([ (x.label,x.options[:3]) for x in sb])
if sb: print(ex.dataframe[1].value.head(3))
