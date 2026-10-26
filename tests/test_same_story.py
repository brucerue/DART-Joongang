import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, tempfile, datetime as dt
sys.path.insert(0, REPO)
os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
os.environ["DIGEST_SUBSCRIBERS"] = json.dumps([{"name": "가", "chat_id": "111", "sectors": ["중후장대"], "modes": ["통신", "일반"]}])
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
def it(t, u, mins=5, pub="연합뉴스"): return {"title": t, "url": u, "publisher": pub, "category": "통신사", "dt": now - dt.timedelta(minutes=mins), "tags": ["HD현대"]}
ROUND = {}
nd.collect = lambda mode, sector, cutoff, google=None: list(ROUND.get(mode, []))
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def run():
    n = len(sent); sys.argv = ["x"]
    try: nd.main()
    except SystemExit: pass
    return "\n".join(sent[n:])
ROUND = {"통신": [it("HD현대마린솔루션 3분기 영업이익 957억원…2.2% 증가(종합)", "https://a/1")]}
t = run(); check("957억원" in t, "1회차: 보냄")
ROUND = {"통신": [it("HD현대마린솔루션 3분기 영업이익 957억원…작년 대비 2.2%↑", "https://a/2"), it("한화오션 LNG선 2척 수주", "https://a/3")]}
t = run(); print(t)
check("957억원" not in t and "한화오션" in t, "2회차: 제목 고쳐 다시 올린 같은 기사는 거르고, 새 기사는 보냄")
ROUND = {"통신": [it("HD현대마린솔루션 4분기 신규 수주 목표 상향", "https://a/4")]}
t = run(); check("4분기 신규 수주" in t, "같은 회사 다른 기사는 보냄")
ROUND = {"통신": [], "일반": [it("한화오션, LNG선 2척 수주…1조원 규모", "https://b/9", pub="한국경제")]}
t = run(); check("1조원" not in t, f"다른 종류(일반)에서도 이미 보낸 사건은 거름 {t[:80]}")
seen = json.load(open(nd.SEEN_FILE)); check(any("|title|" in k for k in seen), "보낸 제목 기록")
print("실패", fails); sys.exit(1 if fails else 0)
