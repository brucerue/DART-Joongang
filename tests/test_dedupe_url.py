import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, tempfile, datetime as dt
sys.path.insert(0, REPO)
os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
os.environ["DIGEST_SUBSCRIBERS"] = json.dumps([{"name": "시험", "chat_id": "111", "sectors": ["자동차"], "modes": ["통신"]}])
os.environ.pop("PREFS_URL", None)
import news_digest as nd
import requests
class _R:
    status_code = 200; text = "{}"; content = b"{}"
    def json(self): return {"ok": True, "result": []}
    def raise_for_status(self): pass
nd.requests.get = lambda *a, **k: _R()
nd.requests.post = lambda *a, **k: _R()
KST = nd.KST
d = tempfile.mkdtemp(); nd.SEEN_FILE = d + "/seen.json"
sent = []
nd.send_safely = lambda text, token, chat: sent.append(text)
now = dt.datetime.now(KST)
def item(title, url, mins=5):
    return {"title": title, "url": url, "publisher": "연합뉴스", "category": "통신사", "dt": now - dt.timedelta(minutes=mins)}
ROUND = [[]]
nd.collect = lambda mode, sector, cutoff, google=None: list(ROUND[0])
nd.is_sports = nd.A.get("is_sports")
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def run():
    sys.argv = ["x", "--mode", "통신", "--sectors", "자동차"]
    n = len(sent)
    try: nd.main()
    except SystemExit: pass
    return len(sent) - n
u1 = "https://news.google.com/rss/articles/CBMiAAA?oc=5"
u2 = "https://news.google.com/rss/articles/CBMiBBB?oc=5"
ROUND[0] = [item("현대차 신형 전기차 공개", u1)]
check(run() == 1, "처음엔 보낸다")
ROUND[0] = [item("현대차 신형 전기차 공개", u1)]
check(run() == 0, "같은 제목·같은 주소는 안 보냄")
ROUND[0] = [item("[업데이트] 현대차 신형 전기차 공개…가격은", u1)]
check(run() == 0, "제목이 고쳐져도 같은 주소면 안 보냄")
ROUND[0] = [item("기아 노조, 임금협상 결렬 선언", u2)]
check(run() == 1, "다른 기사(다른 주소·다른 제목)는 보낸다")
seen = json.load(open(nd.SEEN_FILE))
check(any("|u|" in k for k in seen) and all(len(k) < 120 for k in seen), f"주소는 줄여서 기록 ({len(seen)}개)")
print("실패", fails); sys.exit(1 if fails else 0)
