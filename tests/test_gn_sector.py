import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, json, requests
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
RECENT = format_datetime(datetime.now(timezone.utc) - timedelta(hours=2))  # 늘 '최근' 기사가 되게 (날짜를 고정하면 며칠 뒤 기간 밖으로 빠짐)
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
        return ('<rss><channel><item><title>%s surges on AI demand - Reuters</title><link>https://reuters.com/%s</link><pubDate>%s</pubDate>'
                '<source url="https://www.reuters.com">Reuters</source></item></channel></rss>' % (term, term.replace(' ', '-'), RECENT)).encode()
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
check(not [r for r in at.radio if r.key == "gn_view"], "기업별·한국 관련 나누던 선택 없음(한 화면)")
bg = at.button_group(key="gn_groups")
check(list(bg.options) == ["차·배터리", "중후장대", "반도체·전자", "IT·통신", "에너지·화학", "항공", "재계"], f"섹터 = 산업 뉴스 묶음 (준비 중 제약·바이오는 숨김) {list(bg.options)}")
bg.set_value(["반도체·전자"]).run()
check(not at.exception, f"섹터 고름 예외 없음 {[str(e.value)[:100] for e in at.exception]}")
queries.clear()
[b for b in at.button if b.key == "gn_live"][0].click().run()
check(not at.exception and not at.error, f"새로 검색 예외·오류 없음 {[str(e.value)[:100] for e in at.exception]} {[e.value for e in at.error]}")
kq = [q for q in queries if '"South Korea"' in q]
fq = [q for q in queries if '"South Korea"' not in q]
check(len(kq) == 6, f"한국 관련: 섹터 낱말 3묶음 × 언론사 2묶음 = 6번 ({len(kq)})")
check(len(fq) == 24 * 2 and any('"Apple"' in q for q in fq) and any('"Samsung Electronics"' in q for q in fq) and any('"SK hynix"' in q for q in fq),
      f"기업: 해외 반도체·빅테크 + 삼성전자·SK하이닉스·LG전자 24곳 × 2 ({len(fq)})")
heads = [m.value for m in at.markdown]
i_k = next((i for i, h in enumerate(heads) if "제목에 한국·한국 기업" in h), -1)
i_f = next((i for i, h in enumerate(heads) if "해외 기업 기사" in h), -1)
i_b = next((i for i, h in enumerate(heads) if "본문에 한국" in h), -1)
check(0 <= i_k < i_f < i_b, f"세 칸 순서: 한국 → 해외 기업 → 본문 ({i_k},{i_f},{i_b})")
check(i_k >= 0 and "Samsung surges on AI demand" in heads[i_k + 1], "삼성 기사는 '제목에 한국' 칸")
check(i_f >= 0 and "Nvidia surges on AI demand" in heads[i_f + 1], "엔비디아 기사는 '해외 기업' 칸")
# 기업 직접 검색 (아래 펼치기)
queries.clear()
at.text_input(key="gn_corp").set_value("Nvidia, Intel")
[b for b in at.button if "해외 뉴스 조회" in str(b.label)][0].click().run()
check(not at.exception and not at.error, f"직접 검색 예외 없음 {[str(e.value)[:100] for e in at.exception]}")
md = " ".join(m.value for m in at.markdown)
check("##### Nvidia" in md and "##### Intel" in md, "기업별로 나눠 보여 줌 (모아 둔 외신을 못 읽어도 직접 검색은 됨)")
print("실패", fails); sys.exit(1 if fails else 0)
