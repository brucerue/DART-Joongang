import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import json, os, sys
from datetime import date, datetime
sys.path.insert(0, REPO); os.chdir(REPO)
import price_history as ph, news_digest as nd
ph.time.sleep = lambda s: None
ok = True
def check(c, m):
    global ok; print(("  ✅ " if c else "  ❌ ") + m); ok &= bool(c)
class Resp:
    def __init__(self, rows): self.status_code = 200; self._rows = rows
    def json(self): return {"OutBlock_1": self._rows}
class Sess:
    def get(self, url, params=None, headers=None, timeout=None):
        if "stk_" not in url: return Resp([])
        mk = lambda code, name, k: {"ISU_SRT_CD": code, "ISU_NM": name, "TDD_CLSPRC": "100000", "FLUC_RT": "1.0", "ACC_TRDVAL": "5e11", "MKTCAP": str(k)}
        return Resp([mk("005380", "현대차", 5e13), mk("012330", "현대모비스", 2e13), mk("267260", "HD현대일렉트릭", 1e13), mk("066570", "LG전자", 9e12), mk("999990", "무관", 1e9)])
corp = ph.load_corp_map(FIX + "/corp_sample.csv") if os.path.exists(FIX + "/corp_sample.csv") else {}
open(FIX + "/corp_sample.csv", "w", encoding="utf-8").write("corp_code,corp_name,stock_code\n00164742,현대자동차,005380\n00164788,현대모비스,012330\n01205709,HD현대일렉트릭,267260\n00401731,엘지전자,066570\n")
corp = ph.load_corp_map(FIX + "/corp_sample.csv")
check(len(corp) == 4, f"DART 목록 읽기 {len(corp)}곳")
out_a, *_ = ph.build({}, Sess(), "K", "20260105", 1, 260, date(2026, 1, 5))
check("005380" not in out_a["s"], "DART 목록 없이 KRX 종목명('현대차')만으로는 현대자동차를 못 찾음 (이전 문제 재현)")
out_b, *_ = ph.build({}, Sess(), "K", "20260105", 1, 260, date(2026, 1, 5), corp)
check("005380" in out_b["s"] and out_b["s"]["005380"]["n"] == "현대자동차", f"DART 목록을 쓰면 찾고, 화면 이름은 섹터 목록의 '현대자동차': {out_b['s'].get('005380', {}).get('n')}")
check("012330" in out_b["s"], "현대모비스(섹터 기업) 포함")
check("999990" not in out_b["s"], "무관한 종목 제외")
g = out_b["g"]; print("  그룹:", {k: v["codes"] for k, v in g.items()})
check(any("005380" in v["codes"] for v in g.values()), "현대자동차가 그룹 상장 계열사에도 들어감")
# 이어 붙일 때 화면 이름이 KRX 이름으로 덮어쓰이지 않음
out_c, *_ = ph.build(json.loads(json.dumps(out_b)), Sess(), "K", "20260105", 3, 260, date(2026, 1, 8), corp)
check(out_c["s"]["005380"]["n"] == "현대자동차" and len(out_c["dates"]) == 4, f"이어 붙여도 이름 유지, 일수 {len(out_c['dates'])}")
# 앱의 이름 찾기 (줄임말 허용)
import re
src = open("app.py", encoding="utf-8").read(); ns = {"re": re, "NEWS_ALIASES": nd.A["NEWS_ALIASES"], "name_key": nd.A["name_key"]}
exec(re.search(r"def px_find\(names, wanted\):.*?\n\n\n", src, re.S).group(0), ns)
f = ns["px_find"]
check(f({"현대차": "x", "기아": "y"}, "현대자동차") == "현대차", "px_find: '현대자동차'가 '현대차'를 찾음")
check(f({"SK하이닉스": "x"}, "에스케이하이닉스") == "SK하이닉스", "px_find: 표기 차이(SK/에스케이)")
check(f({"기아": "x"}, "삼성전자") is None, "없는 회사는 None")
print("결과:", "통과" if ok else "실패"); sys.exit(0 if ok else 1)
