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
assert "인천시주안부평산단에10태양광발전시설설치" in keys and "대전형필수의료체계만든다" in keys and "^프로야구ks1차전" in keys  # '^' = 스포츠 RSS
assert calls.count("https://www.yna.co.kr/rss/local.xml") == 2, calls
assert nd.A["is_sports"]("인천시, 주안·부평산단에 10㎿ 태양광 발전시설 설치 - 연합뉴스", keys)
# 비슷한 제목: 스포츠 RSS 제목('^')만 비슷한 것까지, 전국 제목은 똑같은 것만
fk = ("^삼성화재kb손보31제압돌아온틸리카이넨감독첫승신고", "^삼성kia꺾고플레이오프통과2년만에한국시리즈진출종합", "hd현대중공업노조임단협2차잠정합의안찬반투표돌입")
is_sp = nd.A["is_sports"]
assert is_sp("삼성화재, KB손보 상대로 틸리카이넨 감독 첫 승 신고 - 연합뉴스", fk)  # 제목이 조금 바뀜
assert not is_sp("현대차 노조, 임금협상 잠정합의안 찬반투표 돌입", fk)  # 전국 제목과 비슷하지만 다른 기사
assert not is_sp("삼성전자, 3분기 영업이익 12조…반도체 회복", fk)
assert is_sp("대한항공, 현대캐피탈 3-0 제압…V리그 개막전 승리")  # 경기 점수·낱말
assert is_sp("우리카드, 한국전력에 3대1 역전승")
assert not is_sp("현대차, 美 관세 부담에도 3분기 매출 사상 최대")
assert is_sp("삼성, KIA 꺾고 2년 만에 한국시리즈 진출…KT와 우승 다툼")
# 모두 실패해도 빈 목록
calls.clear(); nd.requests.get = lambda url, **kw: R(ok=False)
nd._SPORTS_KEYS.clear()
assert nd.sports_feed_keys() == ()
print("ok")
