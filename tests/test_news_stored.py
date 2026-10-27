"""산업 뉴스: 섹터만 고르면 모아 둔 기사(recent.json·general/*.json)를 '뉴스 조회' 없이 바로 보여 주는지 (가짜 응답, 실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import os, sys, types, json, hashlib, requests, datetime as dtm
os.environ["DART_API_KEY"] = "x" * 40
os.environ["NAVER_CLIENT_ID"] = "nid"; os.environ["NAVER_CLIENT_SECRET"] = "nsec"
import pandas as pd
KST = dtm.timezone(dtm.timedelta(hours=9))
now = dtm.datetime.now(KST)
def d(h): return (now - dtm.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
REC = {"updated": d(0.5), "items": [
    {"t": "현대차 美 공장 증설", "u": "https://x/1", "p": "연합뉴스", "k": "통신", "d": d(1), "s": ["자동차"], "g": ["현대차"]},
    {"t": "기아 EV5 유럽 출시", "u": "https://x/2", "p": "연합뉴스", "k": "통신", "d": d(30), "s": ["자동차"], "g": ["기아"]},
    {"t": "[단독] 현대모비스 자율주행 센서 수주", "u": "https://x/3", "p": "한국경제", "k": "단독", "d": d(2), "s": ["자동차"], "g": ["현대모비스"]},
    {"t": "삼성전자 HBM4 양산", "u": "https://x/4", "p": "연합뉴스", "k": "통신", "d": d(1), "s": ["반도체"], "g": ["삼성전자"]}]}
GEN = {"updated": d(0.5), "sector": "자동차", "items": [
    {"t": "현대차 노조 임금협상 타결", "u": "https://y/1", "p": "중앙일보", "k": "일반", "d": d(3), "s": ["자동차"], "g": ["현대차"]}]}
gname = "general/" + hashlib.md5("자동차".encode()).hexdigest()[:10] + ".json"
GETS = []
# 기업 목록에 S-Oil·NAVER를 넣어 실제처럼 '에쓰오일'·'네이버'가 등록명으로 바뀌게 한다
CSV = "corp_code,corp_name,stock_code\n" + "\n".join([f"0{i:07d},비상장사{i}," for i in range(52000)] + ["00126380,삼성전자,005930", "00138279,S-Oil,010950", "00266961,NAVER,035420"]) + "\n"
class R:
    status_code = 200; text = "{}"; ok = True; content = b"{}"
    def __init__(self, p=None): self._p = p if p is not None else {}
    def json(self): return self._p
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    GETS.append(url)
    if url.endswith("/data/recent.json"): return R(REC)
    if url.endswith("/data/" + gname): return R(GEN)
    if "openapi.naver.com" in url:
        q = (k.get("params") or {}).get("query", "")
        if "현대차" in q:
            return R({"items": [{"title": "현대차 신형 아이오닉 공개", "originallink": "https://www.yna.co.kr/view/AKR9", "link": "https://n.news.naver.com/9",
                                 "pubDate": now.strftime("%a, %d %b %Y %H:%M:%S +0900")}], "total": 1})
        return R({"items": [], "total": 0})
    if "news.google.com" in url: return R()
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode("utf-8"); return r
    return R()
requests.get = fake_get
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url, *a, **k)
requests.Session = _S
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["1"], "corp_name": ["삼성전자"], "stock_code": ["005930"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def md(at): return " ".join(m.value for m in at.markdown)
def caps(at): return " ".join(c.value for c in at.caption)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
import time as _t; _t.sleep(1.5)  # 앱을 열면 뒤에서 모아 둔 기사 파일을 미리 받는다
check(any(u.endswith("/data/recent.json") for u in GETS) and any(gname in u for u in GETS), "앱을 열면 모아 둔 기사 파일(통신·단독·일반)을 미리 받아 둠")
at.radio(key="news_mode").set_value("통신").run()
GETS.clear()
at.button_group(key="nw_통신_groups").set_value(["자동차"]).run()
check("현대차" in at.session_state["nw_통신_terms"], f"섹터를 고르면 검색어가 채워짐 [{at.session_state['nw_통신_terms'][:40]}]")
check(not at.exception, f"오류 없음 {[str(e.value)[:120] for e in at.exception]}")
t = md(at)
check("현대차 美 공장 증설" in t and "삼성전자 HBM4" not in t, "통신: '뉴스 조회' 없이 모아 둔 자동차 기사가 바로 보임")
check("기아 EV5 유럽 출시" not in t, "기간 24시간: 30시간 전 기사는 빠짐")
check("모아 둔 기사" in caps(at), "출처에 '모아 둔 기사 (… 기준)' 표시")
check(not any("news.google.com" in u or "openapi.naver.com" in u for u in GETS), f"구글·네이버를 부르지 않음 {[u[:40] for u in GETS if 'google' in u or 'naver' in u][:3]}")
at.radio(key="nw_통신_period").set_value("3일").run()
check("기아 EV5 유럽 출시" in md(at), "기간을 3일로 바꾸면 바로 다시 걸러짐")
# 최신 기사 더 찾기: 네이버로만
GETS.clear()
[b for b in at.button if b.key == "nw_통신_topup_btn"][0].click().run()
check(not at.exception and "현대차 신형 아이오닉 공개" in md(at), f"최신 기사 더 찾기: 네이버 새 기사 더함 {[str(e.value)[:100] for e in at.exception]}")
check(not any("news.google.com" in u for u in GETS) and any("openapi.naver.com" in u for u in GETS), "더 찾기는 네이버만 부름")
check("최신 1건 더함" in caps(at), f"출처에 더한 건수 {caps(at)[:120]}")
# 섹터를 고른 그대로 '뉴스 조회'를 누르면: 전부 다시 검색하지 않고 모아 둔 기사 + 네이버 새 기사만
GETS.clear()
[b for b in at.button if "nw_통신_form" in str(b.key)][0].click().run()
check(not any("news.google.com" in u for u in GETS) and "현대차 美 공장 증설" in md(at) and "현대차 신형 아이오닉 공개" in md(at),  # 네이버는 바로 앞 '더 찾기' 저장분(5분)을 다시 씀
      f"섹터 그대로 '뉴스 조회' = 모아 둔 기사 + 새 기사만 (구글 {sum('google' in u for u in GETS)}번)")
# 연합만 끄면 안내
at.checkbox(key="nw_통신_yna").uncheck().run()
check("'뉴스 조회'를 누르면 새로 검색" in caps(at) and "현대차 美 공장 증설" not in md(at), "연합만을 끄면 새로 검색하라는 안내")
at.checkbox(key="nw_통신_yna").check().run()
# 검색어를 직접 고치면 안내
GETS.clear()
at.text_input(key="nw_통신_terms").set_value("현대차, 테슬라 오토파일럿")
[b for b in at.button if "nw_통신_form" in str(b.key)][0].click().run()
check(any("news.google.com" in u for u in GETS), "검색어를 고쳐 '뉴스 조회'를 누르면 새로 검색")
at.radio(key="nw_통신_period").set_value("일주일").run()
check("검색어를 바꿨어요" in caps(at), "고친 검색어로 조건을 바꾸면 '뉴스 조회' 안내(모아 둔 기사로 오해 없게)")
# 팀 버튼(차팀)만 눌러도 바로 보여야 함 — 여러 섹터 검색어에 같은 회사의 다른 이름(한화에어로·한화에어로스페이스 등)이 섞여도
at.button_group(key="nw_통신_groups").set_value([]).run()
[b for b in at.button if b.key == "nw_통신_team_차팀"][0].click().run()
check(set(at.session_state["nw_통신_groups"]) >= {"자동차", "배터리", "중후장대"} and "현대차 美 공장 증설" in md(at) and "검색어를 바꿨어요" not in caps(at),
      f"차팀 버튼만 눌러도 모아 둔 기사가 바로 [{caps(at)[:80]}]")
# 단독
at.radio(key="news_mode").set_value("단독").run()
at.button_group(key="nw_단독_groups").set_value(["자동차"]).run()
check("현대모비스 자율주행 센서 수주" in md(at) and "현대차 美 공장" not in md(at), "단독: 모아 둔 단독 기사만")
# 일반: 섹터별 파일
GETS.clear()
at.radio(key="news_mode").set_value("일반").run()
at.button_group(key="nw_일반_groups").set_value("자동차").run()
check(not at.exception and "현대차 노조 임금협상 타결" in md(at), f"일반: 섹터별 파일에서 바로 {[str(e.value)[:100] for e in at.exception]}")
at.radio(key="nw_일반_period").set_value("일주일").run()
check("최근 3일치만" in caps(at), "일반 3일 넘는 기간 안내")
print("실패", fails); sys.exit(1 if fails else 0)
