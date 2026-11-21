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
# 한눈에 보기 계산: 두 섹터에 걸린 같은 단독은 한 번만, 대표 기사는 단독 우선·부고 건너뜀
OV = ROWS + [{"t": "[단독] 기아 미국 공장 증설", "u": "https://x/3b", "p": "한국경제", "k": "단독", "d": d(3), "s": ["반도체"], "g": []},
             {"t": "[부고] 홍길동씨 별세", "u": "https://x/8", "p": "연합뉴스", "k": "통신", "d": d(0.5), "s": ["반도체"], "g": []}]
ov = tdc.overview(OV, ["자동차", "반도체"], since)
check(ov["totals"] == {"단독": 1, "통신": 4, "공시": 1}, f"전체 건수(겹침 한 번) {ov['totals']}")
check(ov["exclusives"][0]["sectors"] == ["자동차", "반도체"], f"단독의 섹터 모음 {ov['exclusives'][0]['sectors']}")
cards = {c["sector"]: c for c in ov["cards"]}
check(cards["자동차"]["lead_kind"] == "단독" and cards["반도체"]["lead"]["title"] == "[단독] 기아 미국 공장 증설", "대표 기사는 단독 우선")
ov2 = tdc.overview([r for r in OV if r["k"] != "단독"], ["반도체"], since)
check(ov2["cards"][0]["lead"]["title"] == "삼성전자 HBM4 양산", f"부고는 대표 기사로 안 씀 {ov2['cards'][0]['lead']['title']}")
# 앱
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not at.exception, f"예외 없음 {[str(e.value)[:120] for e in at.exception]}")
labs = [t.label for t in at.tabs]
check(labs[0] == "오늘의 현황", f"탭 {labs}")
md0 = " ".join(m.value for m in at.markdown)
check("오늘 산업면, 한눈에." in md0 and 'class="ov-card"' in md0 and "기아 미국 공장 증설" in md0, "첫 화면: 섹터 카드·오늘의 단독")
check(not [p for p in at.button_group if p.key == "td_groups"] and "ov-stats" not in md0, "섹터 고르기·숫자판은 없음(2026.11.21 단순화)")
btns = {b.label: b for b in at.button if str(b.key).startswith("ovb_")}
check(len(btns) >= 10 and "차·배터리 자세히 보기" in btns, f"섹터 카드마다 '자세히 보기' 버튼 {list(btns)[:3]}")
def open_card(name):
    b = [x for x in at.button if x.label == f"{name} 자세히 보기"][0]
    b.click().run()
    return " ".join(m.value for m in at.markdown)
md = open_card("차·배터리")  # 모아 둔 줄은 옛 이름 '자동차' -> 읽을 때 차·배터리로
check(not at.exception, f"카드를 눌러 창을 열어도 오류 없음 {[str(e.value)[:120] for e in at.exception]}")
check("신형 전기차 공개" in md and "너무 오래된" not in md, "차·배터리 창: 통신 기사 표시, 오래된 기사 제외")
import re as _re
pills = _re.findall(r'td-pill k\d">(\S+) <b>(\d+)</b>', md)
check(pills[:3] == [("단독", "1"), ("통신", "2"), ("공시", "1")], f"창 머리의 건수 알약 {pills[:3]}")
check("묶음 1건" in md, "같은 사건 통신 기사는 한 묶음")
md = open_card("반도체·전자")
check(not at.exception and "LG전자 신형 냉장고" in md, "다른 카드를 누르면 그 섹터 창")
print("실패", fails); sys.exit(1 if fails else 0)
