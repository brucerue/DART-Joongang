import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, json, requests
os.environ["DART_API_KEY"] = "x"*40
import pandas as pd
CSV = "corp_code,corp_name,stock_code\n" + "\n".join([f"0{i:07d},비상장사{i}," for i in range(52000)] + ["00126380,삼성전자,005930"]) + "\n"
queries = []
class R:
    status_code = 200; text = "{}"; ok = True
    def __init__(self, content=b"{}"): self.content = content
    def json(self): return {}
    def raise_for_status(self): pass
def rss(term):
    if term in ("Nvidia", "Samsung"):
        return ('<rss><channel><item><title>%s surges on AI demand - Reuters</title><link>https://reuters.com/a</link><pubDate>Sun, 25 Oct 2026 01:00:00 GMT</pubDate>'
                '<source url="https://www.reuters.com">Reuters</source></item></channel></rss>' % term).encode()
    return b"<rss><channel></channel></rss>"
def fake_get(url, *a, **k):
    if "news.google.com" in url:
        q = k.get("params", {}).get("q", ""); queries.append(q)
        term = q.split('"')[1] if '"' in q else q
        return R(rss(term))
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode("utf-8"); return r
    return R()
requests.get = fake_get
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url, *a, **k)
requests.Session = _S
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["00126380"], "corp_name": ["삼성전자"], "stock_code": ["005930"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.radio(key="news_mode").set_value("외신").run()
check(not at.exception, f"외신 화면 예외 없음 {[str(e.value)[:100] for e in at.exception]}")
bg = at.button_group(key="gn_groups")
check(list(bg.options)[:3] == ["반도체", "빅테크", "자동차"] and len(bg.options) == 8, f"섹터 선택지 {list(bg.options)}")
bg.set_value(["반도체"]).run()
txt = at.text_input(key="gn_corp").value
check(txt.startswith("Nvidia, AMD, Micron") and txt.endswith("Samsung") and "SK hynix" not in txt, f"기업명 칸 채움: {txt[:80]}…")
bg = at.button_group(key="gn_groups"); bg.set_value(["반도체", "빅테크"]).run()
txt = at.text_input(key="gn_corp").value
check(txt.count("Nvidia") == 1 and "Apple" in txt, "두 섹터를 고르면 중복 없이 합침")
at.button_group(key="gn_groups").set_value(["반도체"]).run()
queries.clear()
[b for b in at.button if "해외 뉴스 조회" in str(b.label)][0].click().run()
check(not at.exception and not at.error, f"조회 예외·오류 없음 {[str(e.value)[:100] for e in at.exception]} {[e.value for e in at.error]}")
md = " ".join(m.value for m in at.markdown)
check("Nvidia surges on AI demand" in md and "Samsung surges on AI demand" in md, "기사가 있는 기업은 목록으로")
cap = [c.value for c in at.caption if "기사가 없는 기업" in c.value]
check(len(cap) == 1 and "Intel" in cap[0] and "Nvidia" not in cap[0] and "Samsung" not in cap[0], f"기사 없는 기업은 한 줄로: {cap}")
check(len(queries) == 15, f"반도체 15곳 → 구글 검색 15번 ({len(queries)})")
at.button_group(key="gn_groups").set_value(list(at.button_group(key="gn_groups").options)).run()
names = at.text_input(key="gn_corp").value.split(", ")
check(len(names) == len(set(names)) and 50 < len(names) <= 100, f"전체 섹터 {len(names)}곳(중복 없음, 한도 100 이내)")
queries.clear(); [b for b in at.button if "해외 뉴스 조회" in str(b.label)][0].click().run()
check(not at.exception and not at.error and len(queries) == len(names) - 15, f"전체 섹터 조회(앞서 받은 반도체 15곳은 저장분 재사용) {len(queries)}번 {[e.value for e in at.error]}")
print("실패", fails); sys.exit(1 if fails else 0)
