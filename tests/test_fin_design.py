import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import sys
exec(open(REPO + "/tests/test_tabs.py").read().split("at = AppTest")[0].split("from streamlit.testing.v1 import AppTest")[0])
from streamlit.testing.v1 import AppTest
fails=[]
def check(c,m):
    print(("  ✅ " if c else "  ❌ ")+m)
    if not c: fails.append(m)
at = AppTest.from_file(REPO + "/app.py", default_timeout=120)
recs=[{"label":"2024","sort":"2024","rev":3.0e14,"op":3.3e13,"net":3.4e13,"rev_yoy":None,"op_yoy":None,"net_yoy":None,"rev_qoq":None,"op_qoq":None,"net_qoq":None,"bs":{},"link":"x"},
      {"label":"2025","sort":"2025","rev":3.3e14,"op":6.5e12,"net":3.4e13,"rev_yoy":10.0,"op_yoy":-80.5,"net_yoy":"흑자전환","rev_qoq":None,"op_qoq":None,"net_qoq":None,"bs":{},"link":"x"}]
at.session_state["result"]={"corp":"삼성전자","records":recs,"period":"연도별"}
at.run()
check(not at.exception, f"예외 없음 {[str(e.value)[:150] for e in at.exception]}")
md=" ".join(m.value for m in at.markdown)
check("2025 매출액" not in md, "요약 카드 없음")
check("재무 조회</div>" not in md, "제목 카드 없음")
print("실패", fails)
