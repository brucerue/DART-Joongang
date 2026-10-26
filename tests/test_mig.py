import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
"""섹터 중후장대 묶음 이전 테스트 (가짜 데이터)"""
import importlib.util, json, os, sys
from datetime import datetime, timedelta, timezone
T = REPO
KST = timezone(timedelta(hours=9))
os.environ.update(DART_API_KEY="x"*40, TELEGRAM_BOT_TOKEN="fake", DIGEST_SUBSCRIBERS=json.dumps([
    {"name": "일부", "chat_id": "1", "sectors": ["철강"], "modes": ["통신"]},
    {"name": "전체", "chat_id": "2", "sectors": ["조선·해운", "방산", "철강", "자동차"], "modes": ["통신"]},
    {"name": "자동차만", "chat_id": "3", "sectors": ["자동차"], "modes": ["통신"]},
    {"name": "차팀", "chat_id": "4", "teams": ["차팀"], "modes": ["통신"]}]))
for k in ("PREFS_URL", "ADMIN_CHAT_ID", "TELEGRAM_CHAT_ID"): os.environ.pop(k, None)
spec = importlib.util.spec_from_file_location("nd", os.path.join(T, "news_digest.py"))
nd = importlib.util.module_from_spec(spec); spec.loader.exec_module(nd)
nd._SPORTS_KEYS.append(())  # 연합 스포츠·전국 RSS는 시험에서 읽지 않는다 (빈 목록)
clock = {"now": datetime(2026, 10, 26, 10, tzinfo=KST)}
class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None): return clock["now"]
nd.datetime = FakeDT
sent, prefs, news = [], {}, []
nd.send_safely = lambda text, token, chat: sent.append((str(chat), text))
nd.handle_updates = lambda *a, **k: None
nd.send_guides = lambda *a, **k: None
nd.load_prefs = lambda: prefs
nd.fetch_remote_prefs = lambda: None
nd.google_search = lambda terms, domains, extra, cutoff: ([dict(i) for i in news if i["dt"] >= cutoff], None)
nd.fetch_dart_feed = lambda now, earn_only=False, wide=False: []
def n(title, dt): return {"title": title, "url": "https://x/" + title, "publisher": "연합뉴스", "category": "통신사", "dt": dt}
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def run(label):
    sent.clear(); sys.argv = ["news_digest.py", "--auto"]
    print(f"\n== {label} ==")
    try: nd.main()
    except SystemExit as e: print("SystemExit:", e)
    got = {c: [] for c in "1234"}
    for c, t in sent: got[c].append(t)
    return got
subs = nd.load_subscribers()
print({s["name"]: (s["sectors"], s["heavy_partial"]) for s in subs})
check(subs[0]["sectors"] == ["중후장대"] and subs[0]["heavy_partial"], "일부만 받던 사람 -> 중후장대, 일부 표시")
check(subs[1]["sectors"] == ["중후장대", "자동차"] and not subs[1]["heavy_partial"], "세 섹터 모두 받던 사람 -> 표시 없음")
check(subs[2]["sectors"] == ["자동차"] and not subs[2]["heavy_partial"], "자동차만")
check(subs[3]["sectors"] == ["자동차", "배터리", "중후장대", "정유·석화", "에너지"] and not subs[3]["heavy_partial"], "차팀")
# 앱에서 /섹터로 저장해 둔 옛 이름
prefs.clear(); prefs["9"] = {"sectors": ["방산", "자동차"]}
ap = nd.apply_prefs([{"name": "x", "chat_id": "9", "sectors": ["자동차"], "modes": ["통신"]}], prefs)
check(ap[0]["sectors"] == ["중후장대", "자동차"] and ap[0]["heavy_partial"], f"저장된 옛 설정 이전 {ap[0]['sectors']}")
prefs.clear()
if os.path.exists(nd.SEEN_FILE): os.remove(nd.SEEN_FILE)
old = datetime(2026, 10, 26, 8, tzinfo=KST)
t_ship, t_steel = "HD현대중공업 대형 LNG선 수주", "포스코 철강 감산 검토"
news[:] = [n(t_ship, old), n(t_steel, old), n("현대자동차 신형 전기차 공개", old)]
# 세 섹터를 다 받던 사람(2)과 차팀(4)은 이미 받은 기사로 기록해 둔다
seen = {f"{c}|통신|{nd.title_key(t)}": old.isoformat() for c in ("2", "4") for t in (t_ship, t_steel)}
json.dump(seen, open(nd.SEEN_FILE, "w"))
g = run("이전 직후 첫 회차")
txt = lambda c: "\n".join(g[c])
check(not g["1"], "일부만 받던 사람(1): 기존 중후장대 기사는 알림 없음")
check(not any(t_ship in x or t_steel in x for x in g["2"]), "세 섹터 받던 사람(2): 이미 받은 기사는 다시 안 옴")
check(not any("LNG선" in x for x in g["3"]) and any("전기차" in x for x in g["3"]), "자동차만(3): 자동차 기사만 옴")
check(not any("LNG선" in x for x in g["4"]), "차팀(4): 이미 받은 중후장대 기사는 안 옴")
saved = json.load(open(nd.SEEN_FILE))
check("migrate|heavy|1" in saved and any(k.startswith("1|통신|") for k in saved), "1번은 기록만 남김(이전 표시 포함)")
news.append(n("한화오션 해양플랜트 계약 체결", datetime(2026, 10, 26, 10, tzinfo=KST)))
clock["now"] = datetime(2026, 10, 26, 11, tzinfo=KST)
g = run("다음 회차(새 중후장대 기사)")
for c in "124":
    check(any("해양플랜트" in x for x in g[c]), f"{c}번: 새 중후장대 기사가 옴")
check(not any("해양플랜트" in x for x in g["3"]), "자동차만(3)은 안 옴")
print("\n실패:", fails); sys.exit(1 if fails else 0)
