"""재무 분석(개별) 분기별: 억원/조원 아래 '전체/1Q~4Q' — 고른 분기끼리만 보여 주고 '전분기比'는 뺀다 (가짜 응답)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")
import sys
exec(open(REPO + "/tests/test_tabs.py").read().split("at = AppTest")[0].split("from streamlit.testing.v1 import AppTest")[0])
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
recs = []
for y in (2024, 2025, 2026):
    for q in (1, 2, 3, 4):
        if (y, q) > (2026, 3):
            break
        lab = f"{y} {q}Q" + ("*" if (y, q) == (2026, 3) else "")
        recs.append({"label": lab, "sort": f"{y}{q}", "rev": (y - 2000) * 1e12 + q * 1e11, "op": 1e11, "net": 1e11, "rev_yoy": 5.0, "op_yoy": 1.0,
                     "net_yoy": 1.0, "rev_qoq": 2.0, "op_qoq": 1.0, "net_qoq": 1.0, "bs": {}, "link": "x", "prov": (y, q) == (2026, 3)})
at = AppTest.from_file(REPO + "/app.py", default_timeout=120)
at.session_state["result"] = {"corp": "LG에너지솔루션", "records": recs, "period": "분기별"}
at.run()
check(not at.exception, f"예외 없음 {[str(e.value)[:150] for e in at.exception]}")
r = at.radio(key="fin_q_pick")
check(list(r.options) == ["전체", "1Q", "2Q", "3Q", "4Q"] and r.value == "전체", f"분기 선택지 {list(r.options)}")
def table(): return [d.value for d in at.dataframe if "기간" in d.value.columns][0]
t = table()
check(len(t) == 11 and any("전분기" in c for c in t.columns), f"전체: 11분기, 전분기比 있음 ({len(t)})")
r.set_value("3Q").run()
t = table()
check(list(t["기간"]) == ["2024 3Q", "2025 3Q", "2026 3Q*"] or sorted(t["기간"]) == ["2024 3Q", "2025 3Q", "2026 3Q*"], f"3Q: 3분기끼리만 {list(t['기간'])}")
check(not any("전분기" in c for c in t.columns) and any("전년동기" in c for c in t.columns), f"3Q: 전분기比 빼고 전년동기比만 {list(t.columns)}")
at.radio(key="fin_q_pick").set_value("4Q").run()
check(len(table()) == 2, f"4Q: 2024·2025 두 줄 ({len(table())})")
at2 = AppTest.from_file(REPO + "/app.py", default_timeout=120)
at2.session_state["result"] = {"corp": "삼성전자", "records": recs[:2], "period": "연도별"}
at2.run()
check(not [x for x in at2.radio if x.key == "fin_q_pick"], "연도별 조회에는 분기 선택지 없음")
print("실패", fails); sys.exit(1 if fails else 0)
