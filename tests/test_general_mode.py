import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, tempfile, datetime as dt
sys.path.insert(0, REPO)
os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
os.environ["DIGEST_SUBSCRIBERS"] = json.dumps([{"name": "둘다", "chat_id": "111", "sectors": ["자동차"], "modes": ["통신", "일반"]},
                                               {"name": "일반만", "chat_id": "222", "sectors": ["자동차"], "modes": ["일반"]},
                                               {"name": "기본", "chat_id": "333", "sectors": ["자동차"]}])
os.environ.pop("PREFS_URL", None)
import news_digest as nd
class _R:
    status_code = 200; text = "{}"; content = b"{}"
    def json(self): return {"ok": True, "result": []}
    def raise_for_status(self): pass
nd.requests.get = lambda *a, **k: _R(); nd.requests.post = lambda *a, **k: _R()
d = tempfile.mkdtemp(); nd.SEEN_FILE = d + "/seen.json"
sent = []
nd.send_safely = lambda text, token, chat: sent.append((chat, text))
now = dt.datetime.now(nd.KST)
def item(title, url, pub="연합뉴스", cat="통신사"):
    return {"title": title, "url": url, "publisher": pub, "category": cat, "dt": now - dt.timedelta(minutes=5), "tags": []}
wire = [item("현대차 신형 전기차 공개", "https://a/1")]
general = [item("현대차 신형 전기차 공개", "https://a/1"), item("현대차 노조 파업 예고", "https://b/2", "중앙일보", "종합일간지")]
calls = []
def fake_collect(mode, sector, cutoff, google=None):
    calls.append(mode); return list(wire if mode == "통신" else general if mode == "일반" else [])
nd.collect = fake_collect
nd.fetch_dart_feed = lambda now, earn_only: []
nd.disclosure_items = lambda feed, sector, earn_only: []
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
subs = nd.load_subscribers() if hasattr(nd, "load_subscribers") else None
sys.argv = ["x", "--sectors", "자동차"]
try: nd.main()
except SystemExit as e: print("exit", e)
by = {}
for chat, t in sent: by.setdefault(chat, []).append(t)
print(json.dumps({k: [t[:200] for t in v] for k, v in by.items()}, ensure_ascii=False, indent=1))
t1 = "\n".join(by.get("111", []))
check(t1.count("현대차 신형 전기차 공개") == 1 and "노조 파업" in t1, "통신+일반: 같은 기사는 통신에만, 일반엔 나머지만")
t2 = "\n".join(by.get("222", []))
check(t2.count("현대차 신형 전기차 공개") == 1 and "노조 파업" in t2 and "일반" in t2, "일반만: 둘 다 받음")
t3 = "\n".join(by.get("333", []))
check("노조 파업" not in t3 and "현대차 신형" in t3, "종류 미지정(기본): 통신만, 일반 없음")
check(sorted(set(calls)) == ["단독", "일반", "통신"], f"조회 종류 {sorted(set(calls))}")
# 15분 회차(--mode 단독)에는 일반을 조회하지 않는다
calls.clear(); sent.clear(); sys.argv = ["x", "--mode", "단독", "--sectors", "자동차"]
try: nd.main()
except SystemExit: pass
check("일반" not in calls, f"단독 회차엔 일반 조회 없음 {calls}")
# /종류 전체 = 통신·단독·공시
r = nd.run_command("/종류 전체", {"sectors": ["자동차"], "modes": ["통신"], "chat_id": "111", "name": "둘다"}, {"111": {}})
print("  /종류 전체 ->", r)
check(r and "통신, 단독, 일반, 공시" in r, "/종류 전체 = 넷 다")
r = nd.run_command("/종류 통신 일반", {"sectors": ["자동차"], "modes": ["통신"], "chat_id": "111", "name": "둘다"}, {"111": {}})
check(r and "통신, 일반" in r, f"/종류 통신 일반 -> {r}")
print("실패", fails); sys.exit(1 if fails else 0)
