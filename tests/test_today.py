import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, json, requests, datetime as dtm
os.environ["DART_API_KEY"] = "x"*40
import pandas as pd
sys.path.insert(0, REPO)
KST = dtm.timezone(dtm.timedelta(hours=9))
now = dtm.datetime.now(KST)
def d(h): return (now - dtm.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
ROWS = [
 {"t": "현대차 신형 전기차 공개", "u": "https://x/1", "p": "연합뉴스", "k": "통신", "d": d(1), "s": ["자동차"], "g": ["현대차"]},
 {"t": "현대차 신형 전기차 공개…가격은", "u": "https://x/2", "p": "연합뉴스", "k": "통신", "d": d(2), "s": ["자동차"], "g": ["현대차"]},
 {"t": "[단독] 기아 미국 공장 증설", "u": "https://x/3", "p": "한국경제", "k": "단독", "d": d(3), "s": ["자동차"], "g": ["기아"]},
 {"t": "기아 최대주주등소유주식변동신고서", "u": "https://x/4", "p": "DART", "k": "공시", "d": d(4), "s": ["자동차"], "g": ["기아"]},
 {"t": "너무 오래된 기사", "u": "https://x/5", "p": "연합뉴스", "k": "통신", "d": d(60), "s": ["자동차"], "g": []},
 {"t": "삼성전자 HBM4 양산", "u": "https://x/6", "p": "연합뉴스", "k": "통신", "d": d(1), "s": ["반도체"], "g": ["삼성전자"]},
 {"t": "LG전자 신형 냉장고", "u": "https://x/7", "p": "연합뉴스", "k": "통신", "d": d(2), "s": ["전자"], "g": ["LG전자"]},
]
REC = {"updated": now.strftime("%Y-%m-%dT%H:%M"), "items": ROWS}
class R:
    status_code = 200; text = "{}"; ok = True; content = b"{}"
    def __init__(self, p=None): self._p = p if p is not None else {}
    def json(self): return self._p
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "/data/recent.json" in url: return R(REC)
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = b"corp_code,corp_name,stock_code\n"; return r
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
import today_calc as tdc
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
# 순수 계산
since = tdc.window_start(now, "24시간")
check(len(tdc.sector_items(ROWS, "자동차", "통신", since)) == 2, "24시간: 자동차 통신 2건(오래된 기사 제외)")
check(tdc.counts(ROWS, "자동차", since) == {"통신": 2, "단독": 1, "공시": 1}, f"건수 {tdc.counts(ROWS, '자동차', since)}")
snap = pd.DataFrame([{"code": "1", "name": "A", "close": 100.0, "rate": 3.0}, {"code": "2", "name": "B", "close": 50.0, "rate": -2.0},
                     {"code": "3", "name": "C", "close": 70.0, "rate": 1.0}, {"code": "4", "name": "D", "close": 20.0, "rate": None}])
up, down = tdc.movers(snap, ["1", "2", "3", "4"], 1)
check([r["name"] for r in up] == ["A"] and [r["name"] for r in down] == ["B"], f"등락 상·하위 {up} {down}")
# 앱
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not at.exception, f"예외 없음 {[str(e.value)[:120] for e in at.exception]}")
labs = [t.label for t in at.tabs]
check(labs[0] == "오늘의 현황", f"탭 {labs}")
check(not at.session_state.get("td_groups") and any("섹터나 팀을 하나 이상" in i.value for i in at.info), f"처음엔 섹터 선택 없음 {list(at.session_state.get('td_groups') or [])}")
at.session_state["td_groups"] = ["자동차", "배터리", "중후장대", "정유·석화", "에너지"]; at.run()
md = " ".join(m.value for m in at.markdown)
check("신형 전기차 공개" in md and "기아 미국 공장 증설" in md and "너무 오래된" not in md, "자동차 기사·단독 표시, 오래된 기사 제외")
import re as _re
pills = _re.findall(r'td-pill k\d">(\S+) <b>(\d+)</b>', md)
check(pills[:3] == [("단독", "1"), ("통신", "2"), ("공시", "1")], f"섹터 머리의 건수 알약 {pills[:3]}")
cap = " ".join(c.value for c in at.caption)
check("이후 · 자료 갱신" in cap, "기간은 24시간 고정 안내")
# 묶기: 두 통신 기사는 한 묶음
check("묶음 1건" in md, "같은 사건 통신 기사는 한 묶음")
# 섹터 바꾸기
at.button_group(key="td_groups").set_value(["반도체", "전자"]).run()
md = " ".join(m.value for m in at.markdown)
check(not at.exception and "삼성전자 HBM4 양산" in md and "LG전자 신형 냉장고" in md and "신형 전기차 공개" not in md, "섹터를 바꾸면 그 섹터 기사만")
check(not [r for r in at.radio if r.key == "td_period"], "기간 선택지 없음(24시간 고정)")
at.button_group(key="td_groups").set_value([]).run()
check(any("섹터나 팀을 하나 이상" in i.value for i in at.info), "섹터 없으면 안내")
print("실패", fails); sys.exit(1 if fails else 0)
