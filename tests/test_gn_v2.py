"""외신 개선: 별칭 검색·정확한 제목 판정(SK만 맞는 기사 뺌)·같은 기사 묶기·한글 제목·'한국 관련' 보기(제목/본문 나눔) (가짜 응답, 실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import os, sys, types, json, re, requests
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
os.environ["DART_API_KEY"] = "x" * 40
os.environ["ANTHROPIC_API_KEY"] = "sk-test"
os.environ.pop("GEMINI_API_KEY", None)
import pandas as pd
CSV = "corp_code,corp_name,stock_code\n" + "\n".join([f"0{i:07d},비상장사{i}," for i in range(52000)] + ["00126380,삼성전자,005930"]) + "\n"
def ago(h): return format_datetime(datetime.now(timezone.utc) - timedelta(hours=h))
def item(title, host, pub, h, path):
    return f'<item><title>{title} - {pub}</title><link>https://{host}/{path}</link><pubDate>{ago(h)}</pubDate><source url="https://www.{host}">{pub}</source></item>'
queries = []
class R:
    status_code = 200; text = "{}"; ok = True
    def __init__(self, content=b"{}", js=None): self.content = content; self._js = js or {}
    def json(self): return self._js
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "news.google.com" in url:
        q = k.get("params", {}).get("q", ""); queries.append(q)
        if '"Hynix"' in q:
            body = (item("SK Hynix profit jumps on HBM demand", "reuters.com", "Reuters", 2, "a1")
                    + item("SK Hynix profit jumps on HBM demand, update", "cnbc.com", "CNBC", 1, "a2")
                    + item("SK Group chairman meets Trump", "bloomberg.com", "Bloomberg", 3, "a3"))
        elif '"South Korea"' in q and "chip" in q:
            body = (item("South Korea's chip exports hit record", "reuters.com", "Reuters", 2, "k1")
                    + item("Nvidia supply deal reshapes memory market", "wsj.com", "WSJ", 4, "k2")
                    + item("Tech, Media &amp; Telecom Roundup: Market Talk", "wsj.com", "WSJ", 5, "k3"))
        else:
            body = ""
        return R(f"<rss><channel>{body}</channel></rss>".encode())
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode("utf-8"); return r
    return R()
AI_CALLS = []
def fake_post(self, url, **k):
    if "anthropic.com" in url:
        prompt = k["json"]["messages"][0]["content"]
        titles = re.findall(r"^\d+\. (.*)$", prompt, re.M)
        AI_CALLS.append(len(titles))
        return R(js={"content": [{"type": "text", "text": json.dumps([f"[한글] {t}" for t in titles], ensure_ascii=False)}]})
    return R()
requests.get = fake_get
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url, *a, **k)
    def post(self, url, *a, **k): return fake_post(self, url, **k)
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
def md(at): return " ".join(m.value for m in at.markdown)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.radio(key="news_mode").set_value("외신").run()
check(list(at.radio(key="gn_view").options) == ["기업별", "한국 관련"], "외신 보기: 기업별 / 한국 관련")
# 1) 기업별: SK hynix
at.text_input(key="gn_corp").set_value("SK hynix")
[b for b in at.button if "해외 뉴스 조회" in str(b.label)][0].click().run()
check(not at.exception, f"예외 없음 {[str(e.value)[:120] for e in at.exception]}")
q = queries[-1] if queries else ""
hq = [x for x in queries if '"Hynix"' in x]
check('"SK hynix"' in hq[0] and " OR " in hq[0] and len(hq) == 2 and any("site:economist.com" in x for x in hq), f"별칭으로 넓게, 언론사는 두 묶음으로 [{len(hq)}번]")
t = md(at)
check(t.count("- [SK Hynix profit jumps") == 1 and "같은 기사 1건 더(Reuters)" in t, "같은 기사(로이터·CNBC)는 한 묶음, 최신 기사가 대표")
check("SK Group chairman" not in t, "'SK'만 맞는 다른 회사 기사는 뺌")
check("[한글] SK Hynix profit jumps on HBM demand" in t, "한글 제목 함께")
# 2) 한국 관련
at.radio(key="gn_view").set_value("한국 관련").run()
at.button_group(key="gnk_groups").set_value(["반도체"]).run()
[b for b in at.button if b.key == "gnk_live"][0].click().run()
check(not at.exception, f"예외 없음 {[str(e.value)[:120] for e in at.exception]}")
q = queries[-1] if queries else ""
check('"South Korea"' in q and "chip" in q, f"한국 낱말 + 섹터 낱말로 검색 [{q[:90]}]")
heads = [m.value for m in at.markdown]
i_t = next((i for i, h in enumerate(heads) if "제목에 한국이 나온 기사" in h), -1)
i_b = next((i for i, h in enumerate(heads) if "본문에 한국이 나온 기사" in h), -1)
check(i_t >= 0 and i_b > i_t and "South Korea's chip exports hit record" in heads[i_t + 1] and "Nvidia supply deal" in heads[i_b + 1],
      "제목에 한국 / 본문 언급을 나눠 보여 줌")
check("[한글] Nvidia supply deal reshapes memory market" in md(at), "한국 관련도 한글 제목")
check("Market Talk" not in md(at), "기사가 아닌 모음(Market Talk 등)은 뺌")
check('-"North Korea"' in q, "북한 기사는 검색에서 뺌")
print("실패", fails); sys.exit(1 if fails else 0)
