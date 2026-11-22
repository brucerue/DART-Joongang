"""수집기: 구글 뉴스 주소 -> 언론사 원문 주소 (가짜 구글 응답, 실서버 접속 없음)"""
import pathlib, sys
from datetime import datetime
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import collect_data as cd  # noqa: E402

fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)

now = datetime(2026, 11, 21, 12, 0, tzinfo=cd.KST)
G = "https://news.google.com/rss/articles/CBMiAAA{}?oc=5"
check(cd.google_id(G.format(1)) == "CBMiAAA1" and cd.google_id("https://www.joongang.co.kr/article/1") is None, "구글 기사 번호 읽기")

# 1) 가짜 구글: 기사 페이지(서명·시각) -> batchexecute 답(두 겹 이스케이프)
class R:
    def __init__(self, text): self.text = text
class FakeSession:
    headers = {}
    def __init__(self): self.posts = 0
    def get(self, url, timeout=None):
        return R('<c-wiz><div jscontroller="x" data-n-a-sg="SIG" data-n-a-ts="1700000000"></div></c-wiz>')
    def post(self, url, data=None, headers=None, timeout=None):
        self.posts += 1
        assert "SIG" in data and "1700000000" in data
        return R(')]}\'\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://www.edaily.co.kr/News/Read?newsId\\\\u003d0203\\\\u0026mediaCodeNo\\\\u003d257\\",1]",null]]')
fs = FakeSession()
got = cd.decode_google(fs, "CBMiAAA1")
check(got == "https://www.edaily.co.kr/News/Read?newsId=0203&mediaCodeNo=257", f"구글 답에서 원문 주소 꺼내기 {got}")

# 2) 목록 바꾸기: 처음 보는 것만 묻고, 기억해 둔 건 다시 묻지 않음, 못 바꾼 건 그대로
asked = []
def dec(session, gid):
    asked.append(gid)
    return None if gid.endswith("9") else f"https://press.example/{gid}"
ex = [{"url": G.format(1), "title": "a"}, {"url": "https://www.yna.co.kr/view/1", "title": "b"}]
wire = [{"url": G.format(1), "title": "a"}, {"url": G.format(2), "title": "c"}, {"url": G.format(9), "title": "d"}]
gmap = {"CBMiAAA2": ["https://old.example/2", "2026-11-20"]}
ok, fail = cd.resolve_urls([ex, wire], gmap, now, session=fs, gap=0, decoder=dec)
check(asked == ["CBMiAAA1", "CBMiAAA9"], f"처음 보는 기사만 한 번씩 묻기(단독 먼저) {asked}")
check(ex[0]["url"] == "https://press.example/CBMiAAA1" and wire[0]["url"] == ex[0]["url"] and wire[1]["url"] == "https://old.example/2", "바꾼 주소·기억해 둔 주소로 바꿈")
check(wire[2]["url"] == G.format(9) and (ok, fail) == (1, 1), f"못 바꾼 건 구글 주소 그대로 ({ok}, {fail})")
check(gmap["CBMiAAA1"] == ["https://press.example/CBMiAAA1", "2026-11-21"], "새로 바꾼 짝을 기억")

# 3) 연달아 막히면 그 회차는 멈춤
asked.clear()
many = [{"url": G.format(f"{i}9")} for i in range(10)]
cd.resolve_urls([many], {}, now, session=fs, gap=0, decoder=dec)
check(len(asked) == 3, f"3번 연달아 실패하면 다음 회차로 미룸 ({len(asked)}번 물음)")

# 4) 모아 둔 줄 옮기기: 같은 기사가 원문 주소로 이미 있으면 섹터를 합침
store = {G.format(1): {"u": G.format(1), "s": ["재계"], "g": ["A"]},
         "https://press.example/CBMiAAA1": {"u": "https://press.example/CBMiAAA1", "s": ["유통"], "g": ["B"]},
         G.format(9): {"u": G.format(9), "s": ["항공"], "g": []}}
n = cd.rekey_store(store, gmap)
r = store["https://press.example/CBMiAAA1"]
check(n == 1 and G.format(1) not in store and set(r["s"]) == {"재계", "유통"} and G.format(9) in store, f"구글 주소 줄을 원문 주소로 옮기고 합침 {store.keys()}")
gst = {G.format(1): {"u": G.format(1), "t": "Samsung", "c": ["Samsung"]}, "https://press.example/CBMiAAA1": {"u": "https://press.example/CBMiAAA1", "t": "Samsung", "c": ["Apple"]}}
cd.rekey_store(gst, gmap)
check(list(gst) == ["https://press.example/CBMiAAA1"] and set(gst["https://press.example/CBMiAAA1"]["c"]) == {"Samsung", "Apple"}, "외신 줄(기업 c)도 옮기고 합침")
check(cd.clean_url("https://www.yna.co.kr/amp/view/AKR1") == "https://www.yna.co.kr/view/AKR1" and cd.clean_url("https://www.yna.co.kr/view/AKR1") == "https://www.yna.co.kr/view/AKR1", "연합 AMP 주소는 보통 주소로(같은 기사 두 줄 방지)")
# 구글 날짜가 하루 넘게 지난 '처음 보는' 기사는 처음 찾은 시각으로 (연합 미리 써 둔 기사)
from datetime import timedelta
its = [{"url": "https://yna/AKR20261120", "dt": now - timedelta(hours=41)},   # 처음 봄 + 41시간 전 -> 지금으로
       {"url": "https://yna/old-known", "dt": now - timedelta(hours=41)},     # 이미 모아 둔 기사 -> 그대로
       {"url": "https://yna/missed", "dt": now - timedelta(hours=7)}]         # 몇 시간 놓친 기사 -> 그대로
n = cd.fix_stale_dates(its, {"https://yna/old-known": {}}, now)
check(n == 1 and its[0]["dt"] == now and its[1]["dt"] < now - timedelta(hours=40) and its[2]["dt"] == now - timedelta(hours=7), "처음 보는데 하루 넘게 지난 날짜만 처음 찾은 시각으로")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
