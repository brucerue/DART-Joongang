import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import sys, types, time
sys.path.insert(0, REPO)
import news_digest as nd
calls = []
class R:
    def __init__(s, content=b"", js=None, ok=True): s.content=content; s._js=js; s.ok=ok
    def raise_for_status(s):
        if not s.ok: raise RuntimeError("500")
    def json(s): return s._js
rss = lambda *t: ("<rss><channel>" + "".join(f"<item><title>{x}</title></item>" for x in t) + "</channel></rss>").encode()
def fake_get(url, **kw):
    calls.append(url)
    if url.endswith("sports.xml"): return R(rss("프로야구 KS 1차전"))
    if url.endswith("local.xml"):
        return R(ok=False) if calls.count(url) < 2 else R(rss("인천시, 주안·부평산단에 10㎿ 태양광 발전시설 설치"))
    return R(js={"items": [{"k": "대전형필수의료체계만든다"}]})
nd.requests.get = fake_get; nd.time.sleep = lambda s: None
nd._SPORTS_KEYS.clear()  # 한 실행 안에서는 한 번만 읽어 두므로 시험마다 비운다
keys = nd.sports_feed_keys()
print(keys)
assert "인천시주안부평산단에10태양광발전시설설치" in keys and "대전형필수의료체계만든다" in keys and "프로야구ks1차전" in keys
assert calls.count("https://www.yna.co.kr/rss/local.xml") == 2, calls
assert nd.A["is_sports"]("인천시, 주안·부평산단에 10㎿ 태양광 발전시설 설치 - 연합뉴스", keys)
# 모두 실패해도 빈 목록
calls.clear(); nd.requests.get = lambda url, **kw: R(ok=False)
nd._SPORTS_KEYS.clear()
assert nd.sports_feed_keys() == ()
print("ok")
