import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, tempfile, datetime as dt
sys.path.insert(0, REPO)
os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
os.environ["DIGEST_SUBSCRIBERS"] = json.dumps([{"name": "가", "chat_id": "111", "sectors": ["자동차", "배터리"], "modes": ["통신", "일반", "공시"]}])
os.environ.pop("PREFS_URL", None)
import news_digest as nd
class _R:
    status_code = 200; text = "{}"; content = b"{}"
    def json(self): return {"ok": True, "result": []}
    def raise_for_status(self): pass
nd.requests.get = lambda *a, **k: _R(); nd.requests.post = lambda *a, **k: _R()
d = tempfile.mkdtemp(); nd.SEEN_FILE = d + "/seen.json"
sent = []; nd.send_safely = lambda text, token, chat: sent.append(text)
now = dt.datetime.now(nd.KST)
ms = lambda t: int(t.timestamp() * 1000)
def it(title, mins): return {"title": title, "url": "https://x/" + title, "publisher": "연합뉴스", "category": "통신사", "dt": now - dt.timedelta(minutes=mins), "tags": []}
data = {("통신", "자동차"): [it("현대차 美 관세 협상 타결", 180), it("기아 EV5 유럽 출시", 30)],
        ("통신", "배터리"): [it("LG엔솔 북미 공장 가동 중단", 180), it("삼성SDI 전고체 시제품 공개", 30)],
        ("일반", "자동차"): [it("현대모비스 자율주행 센서 개발", 180), it("KG모빌리티 신차 사전계약", 30)]}
nd.collect = lambda mode, sector, cutoff, google=None: list(data.get((mode, sector), []))
today = now.strftime("%Y%m%d"); yday = (now - dt.timedelta(days=1)).strftime("%Y%m%d")
nd.fetch_dart_feed = lambda now, earn_only: []
nd.disclosure_items = lambda feed, sector, earn_only: ([{"title": "[배터리사] 어제 공시", "url": "u1", "publisher": "DART", "key": "1", "day": yday},
                                                       {"title": "[배터리사] 오늘 공시", "url": "u2", "publisher": "DART", "key": "2", "day": today}] if sector == "배터리" else [])
# 배터리 섹터를 20분 전에, 일반 종류를 20분 전에 더했다고 가정
prefs = {"111": {"added": {"s:배터리": ms(now - dt.timedelta(minutes=20)), "m:일반": ms(now - dt.timedelta(minutes=20))}}}
nd.load_prefs = lambda: prefs
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
sys.argv = ["x"]
try: nd.main()
except SystemExit as e: print("exit", e)
t = "\n".join(sent); print(t)
check("현대차 美 관세 협상 타결" in t and "기아 EV5 유럽 출시" in t, "기존 섹터·종류(자동차 통신)는 12시간 그대로")
check("삼성SDI 전고체 시제품 공개" in t and "LG엔솔 북미 공장 가동 중단" not in t, "새 섹터(배터리): 1시간 전부터만")
check("KG모빌리티 신차 사전계약" in t and "현대모비스 자율주행 센서 개발" not in t, "새 종류(일반): 1시간 전부터만")
check("오늘 공시" in t and "어제 공시" not in t, "새 섹터 공시: 더한 날 것만")
# mark_added 규칙
m = {}
nd.mark_added(m, "s", ["자동차"], ["자동차", "배터리"], now_ms=1000)
check(m == {"added": {"s:배터리": 1000}}, f"새로 더한 것만 기록 {m}")
nd.mark_added(m, "s", ["자동차", "배터리"], ["자동차"], now_ms=2000)
check("added" not in m, f"뺀 섹터 기록은 지움 {m}")
r = nd.run_command("/종류 통신 일반", {"chat_id": "9", "name": "x", "sectors": ["자동차"], "modes": ["통신"]}, pp := {})
check(list(pp["9"]["added"]) == ["m:일반"], f"/종류로 일반 추가 기록 {pp}")
print("실패", fails); sys.exit(1 if fails else 0)
