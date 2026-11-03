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

"""앱 다이어트(2026.11): 기업 브리핑·정기보고서·재계 순위 탭, 주가 업계 비교·이벤트, 재무·공시 AI 검색은 관리자 주소에서만.
기업 브리핑은 재무 분석 '개별' 조회 결과의 '회사 브리핑' 펼치기로 (가짜 응답)"""
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
os.environ.pop("ALL_TABS", None)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
labels = [t.label for t in at.tabs][:12]
check(not at.exception and "기업 브리핑" not in labels and "정기보고서" not in labels and "재계 순위" not in labels and "재무 분석" in labels, f"공용 탭 {labels}")
at.session_state["main_tab"] = "재무 분석"; at.run()
check(list(at.radio(key="fin_mode").options) == ["개별", "국내", "해외"], "재무 분석: AI 검색 숨김")
at.session_state["main_tab"] = "공시 분석"; at.run()
check(list(at.radio(key="disc_mode").options) == ["오늘의 공시", "기업", "섹터"], "공시 분석: AI 검색 숨김")
# 재무 분석 개별: 회사 브리핑 펼치기
at.session_state["main_tab"] = "재무 분석"; at.run()
at.radio(key="fin_mode").set_value("개별").run()
recs = [{"label": f"{y}", "sort": f"{y}", "rev": 1e14, "op": 1e13, "net": 1e13, "rev_yoy": 1.0, "op_yoy": 1.0, "net_yoy": 1.0, "bs": {}, "link": "x"} for y in (2024, 2025)]
at.session_state["result"] = {"corp": "삼성전자", "records": recs, "period": "연도별"}; at.run()
exps = [e for e in at.expander if "회사 브리핑" in str(e.label)]
md = "".join(m.value for m in at.markdown)
check(not at.exception and exps and "최근 3개월 주요 공시" not in md, "조회 결과 위에 '회사 브리핑' (접힌 채, 아직 안 불러옴)")
at.session_state["fin_brief_open"] = True; at.run()
md = "".join(m.value for m in at.markdown)
check(not at.exception and "최근 3개월 주요 공시" in md and "최근 한 달 단독 뉴스" in md and "실적 (최근" not in md, f"펼치면 공시·뉴스 (실적표는 위에 이미 있어 뺌) {[str(e.value)[:120] for e in at.exception]}")
check("최근 동향 요약" in md, "AI 동향 요약 칸 (AI 키가 없는 시험에선 버튼 대신 안내)")
# 관리자 주소에선 그대로
at2 = AppTest.from_file(REPO + "/app.py", default_timeout=300); at2.query_params["lab"] = "1"; at2.run()
labels2 = [t.label for t in at2.tabs][:12]
check({"기업 브리핑", "정기보고서", "재계 순위"} <= set(labels2), "관리자 주소: 숨긴 탭도 보임")
at2.session_state["main_tab"] = "주가 분석"; at2.run()
px = [r for r in at2.radio if r.key == "px_mode"]
check(not px or list(px[0].options) == ["장중 현황", "시세판", "업계 비교", "이벤트"], "관리자 주소: 주가 네 보기")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
