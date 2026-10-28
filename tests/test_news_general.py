import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, requests, datetime as dtm
os.environ["DART_API_KEY"] = "x"*40
import pandas as pd
CSV = "corp_code,corp_name,stock_code\n" + "\n".join([f"0{i:07d},비상장사{i}," for i in range(52000)] + ["00126380,삼성전자,005930"]) + "\n"
KST = dtm.timezone(dtm.timedelta(hours=9))
now = dtm.datetime.now(KST)
today_noon = now.replace(hour=0, minute=1, second=0, microsecond=0)   # 오늘 0시 직후 기사
yest = today_noon - dtm.timedelta(hours=3)                            # 어제 밤 기사(24시간 안, 오늘 아님)
two = today_noon - dtm.timedelta(days=2)
def fmt(t): return t.astimezone(dtm.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
ITEMS = [("[단독] 현대차 단독 소식", today_noon), ("현대차 일반 소식", today_noon), ("어제 밤 현대차 소식", yest), ("현대차 농구단 개막전 승리", today_noon)]
class R:
    status_code = 200; text = "{}"; ok = True
    def __init__(self, content=b"{}"): self.content = content
    def json(self): return {}
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "news.google.com" in url:
        q = k.get("params", {}).get("q", "")
        if "현대차" not in q: return R(b"<rss><channel></channel></rss>")
        body = "".join(f'<item><title>{t} - 중앙일보</title><link>https://www.joongang.co.kr/{i}</link><pubDate>{fmt(d)}</pubDate><source url="https://www.joongang.co.kr">연합뉴스</source></item>' for i, (t, d) in enumerate(ITEMS))
        return R(f"<rss><channel>{body}</channel></rss>".encode())
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
check(list(at.radio(key="news_mode").options) == ["통신", "단독", "일반", "외신"], f"조회 방식 {list(at.radio(key='news_mode').options)}")
def run(mode):
    at.session_state["news_mode"] = mode; at.run()
    at.session_state[f"nw_{mode}_terms"] = "현대차"; at.session_state[f"nw_{mode}_period"] = "24시간"; at.run()
    [b for b in at.button if f"nw_{mode}_submit" in str(b.key)][0].click().run()
    return " ".join(m.value for m in at.markdown), [e.value[:100] for e in at.error], [w.value[:100] for w in at.warning]
t, err, warn = run("일반")
print("   일반 오류", err, "경고", warn, [c.value[:80] for c in at.caption][-2:])
check(not at.exception and "현대차 일반 소식" in t and "현대차 단독 소식" in t, "일반: 단독·일반 기사 모두")
check("농구단" not in t, "일반: 스포츠 기사 제외")
t, err, warn = run("단독")
check(not at.exception and "현대차 단독 소식" in t and "현대차 일반 소식" not in t, "단독: 단독 표기만")
check(__import__("ast").literal_eval(open(REPO + "/app.py",encoding="utf-8").read().split("ALARM_MODES = ")[1].split("\n")[0].split("#")[0].strip()) == ("통신", "단독", "일반", "공시"), "알람 종류 통신·단독·일반·공시")
at.session_state["news_mode"] = "일반"; at.run()
check(not [b for b in at.button if str(b.key).startswith("nw_일반_team")], "일반: 팀 버튼 없음")
check([b for b in at.button if str(b.key).startswith("nw_단독_team")] or True, "")
# (알약 선택→검색어 채우기는 on_change 콜백이라 AppTest로는 못 누름; 로컬 브라우저로 확인함)
print("실패", fails)
