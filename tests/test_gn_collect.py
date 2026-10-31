"""외신 미리 모으기: 수집기가 기업별(별칭 제목)·한국 관련(본문) 외신을 섹터별 파일에 7일 쌓고 새 제목만 Gemini로 한글 제목을 붙이는지,
앱은 섹터만 고르면 모아 둔 외신을 바로 보여 주는지 (가짜 응답, 실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import os, sys, json, re, types, tempfile, requests
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
sys.path.insert(0, REPO)
os.environ["GEMINI_API_KEY"] = "g-test"
os.environ.pop("DART_API_KEY", None)
import collect_data as cd, gnews_calc as gc
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def ago(h): return format_datetime(datetime.now(timezone.utc) - timedelta(hours=h))
def item(title, host, pub, h, path):
    return f'<item><title>{title} - {pub}</title><link>https://{host}/{path}</link><pubDate>{ago(h)}</pubDate><source url="https://www.{host}">{pub}</source></item>'
class R:
    def __init__(self, content=b"", js=None, code=200): self.content = content; self._js = js or {}; self.status_code = code
    def json(self): return self._js
    def raise_for_status(self): pass
GQ = []
def fake_get(url, params=None, **k):
    q = (params or {}).get("q", ""); GQ.append(q)
    if '"Nvidia"' in q:
        body = item("Nvidia beats estimates", "reuters.com", "Reuters", 1, "n1") + item("Gaming stocks rally", "cnbc.com", "CNBC", 2, "n2")
    elif '"South Korea"' in q and "chip" in q:
        body = item("South Korea chip exports jump", "wsj.com", "WSJ", 1, "k1") + item("Tech, Media &amp; Telecom Roundup: Market Talk", "wsj.com", "WSJ", 1, "k9")
    else:
        body = ""
    return R(f"<rss><channel>{body}</channel></rss>".encode())
TRANSLATED = []
def fake_post(url, json=None, headers=None, timeout=None):
    titles = re.findall(r"^\d+\. (.*)$", json["contents"][0]["parts"][0]["text"], re.M)
    TRANSLATED.extend(titles)
    return R(js={"candidates": [{"content": {"parts": [{"text": __import__("json").dumps([f"[한글] {t}" for t in titles], ensure_ascii=False)}]}}]})
cd.requests.get = fake_get; cd.requests.post = fake_post
out = tempfile.mkdtemp()
now = datetime.now(cd.KST)
old = (now - timedelta(days=9)).strftime(cd.FMT)
g = os.path.join(out, gc.file_name("global", "반도체")); os.makedirs(os.path.dirname(g), exist_ok=True)
json.dump({"items": [{"t": "Old Nvidia story", "u": "https://old", "p": "AP", "d": old, "ko": "옛", "c": ["Nvidia"]},
                     {"t": "Nvidia kept", "u": "https://keep", "p": "AP", "d": (now - timedelta(days=2)).strftime(cd.FMT), "ko": "이미 옮김", "c": ["Nvidia"]}]}, open(g, "w"))
errors = []
added, nko = cd.collect_global(out, now, errors)
gj = json.load(open(g, encoding="utf-8"))
titles = [r["t"] for r in gj["items"]]
check("Nvidia beats estimates" in titles and "Gaming stocks rally" not in titles, f"기업별: 제목에 별칭이 든 기사만 {titles}")
check("Old Nvidia story" not in titles and "Nvidia kept" in titles, "7일 지난 기사는 지우고 그 안은 유지")
row = next(r for r in gj["items"] if r["t"] == "Nvidia beats estimates")
check(row["c"] == ["Nvidia"] and row["ko"] == "[한글] Nvidia beats estimates", f"기업 표시·한글 제목 {row}")
check("Nvidia kept" not in TRANSLATED, "이미 옮긴 제목은 다시 안 옮김")
kj = json.load(open(os.path.join(out, gc.file_name("korea", "반도체")), encoding="utf-8"))
check([r["t"] for r in kj["items"]] == ["South Korea chip exports jump"], f"한국 관련: 기사 아닌 모음은 빼고 저장 {[r['t'] for r in kj['items']]}")
check(all(os.path.exists(os.path.join(out, gc.file_name("korea", s2))) for s2 in gc.KOREA_TOPICS), "한국 관련 섹터마다 파일")
check(any('-"North Korea"' in q for q in GQ), "북한 기사 빼는 검색식")
# 앱: 섹터만 고르면 모아 둔 외신이 바로
os.environ["DART_API_KEY"] = "x" * 40
os.environ.pop("ANTHROPIC_API_KEY", None); os.environ.pop("GEMINI_API_KEY", None)
import pandas as pd
CSV = "corp_code,corp_name,stock_code\n" + "\n".join([f"0{i:07d},비상장사{i}," for i in range(52000)] + ["00126380,삼성전자,005930"]) + "\n"
FILES = {gc.file_name("global", "반도체"): gj, gc.file_name("korea", "반도체"): kj}
APP_G = []
class AR:
    status_code = 200; text = "{}"; ok = True; content = b"{}"
    def __init__(self, p=None): self._p = p if p is not None else {}
    def json(self): return self._p
    def raise_for_status(self): pass
def app_get(url, *a, **k):
    if "raw.githubusercontent" in url and "/data/" in url:
        name = url.split("/data/", 1)[1]
        if name in FILES: return AR(FILES[name])
        if name.startswith(("global/", "korea/")): return AR({"updated": "x", "items": []})
        return AR({})
    if "news.google.com" in url: APP_G.append(url); return AR()
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode("utf-8"); return r
    return AR()
requests.get = app_get
class _S(requests.Session):
    def get(self, url, *a, **k): return app_get(url, *a, **k)
requests.Session = _S
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["1"], "corp_name": ["삼성전자"], "stock_code": ["005930"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.radio(key="news_mode").set_value("외신").run()
APP_G.clear()
at.button_group(key="gn_groups").set_value(["반도체"]).run()
md = " ".join(m.value for m in at.markdown)
check(not at.exception and "Nvidia beats estimates" in md and "[한글] Nvidia beats estimates" in md, f"기업별: 섹터만 골라도 모아 둔 외신 + 한글 제목 {[str(e.value)[:100] for e in at.exception]}")
check(not APP_G and any("모아 둔 기사" in c.value for c in at.caption), "구글을 부르지 않고 '모아 둔 기사' 표시")
at.radio(key="gn_view").set_value("한국 관련").run()
at.button_group(key="gnk_groups").set_value(["반도체"]).run()
md = " ".join(m.value for m in at.markdown)
check(not at.exception and "South Korea chip exports jump" in md and not APP_G, "한국 관련: 섹터만 골라도 모아 둔 기사")
print("실패", fails); sys.exit(1 if fails else 0)
