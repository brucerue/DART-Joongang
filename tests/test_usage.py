import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, time, requests, threading
os.environ["DART_API_KEY"] = "x"*40
os.environ["APPS_SCRIPT_URL"] = "https://script.google.com/macros/s/FAKE/exec?key=K"
sys.path.insert(0, REPO)
POSTS = []
STATS = {"from": "2026-09-26", "to": "2026-10-26", "days": [{"d": "2026-10-26", "sessions": 3, "sessions_others": 2, "n": 9, "n_others": 6}],
         "features": [{"f": "뉴스 조회: 통신", "n": 5, "n_others": 4, "sessions": 2}, {"f": "계산기", "n": 4, "n_others": 2, "sessions": 2},
                      {"f": "AI·Gemini", "n": 8, "n_others": 5, "sessions": 2}, {"f": "AI·Claude(대체)", "n": 2, "n_others": 1, "sessions": 1}], "days_exclude_ai": True}
class R:
    status_code = 200; ok = True; text = "{}"
    def __init__(self, p=None): self._p = p if p is not None else {}
    def json(self): return self._p
    def raise_for_status(self): pass
PR = json.load(open(FIX + "/fx_prices.json")); IN = json.load(open(FIX + "/fx_intraday.json"))
def fake_get(url, *a, **k):
    if "script.google.com" in url: return R(STATS)
    if "prices.json" in url: return R(PR)
    if "intraday.json" in url: return R(IN)
    if "corp_codes" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = b"corp_code,corp_name,stock_code\n"; return r
    return R()
def fake_post(url, *a, **k):
    POSTS.append(k.get("json")); return R({"ok": True})
requests.get = fake_get; requests.post = fake_post
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url)
requests.Session = _S
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def feats(): time.sleep(0.5); return [p["items"][0]["f"] for p in POSTS if p]
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not at.exception, f"오류 없음 {at.exception}")
check(feats() == [], f"처음 열 때는 기록 없음 {feats()}")
at.radio(key="px_mode").set_value("시세판").run()
check("주가 분석: 시세판" in feats(), f"보기 바꾸면 기록 {feats()}")
n = len(POSTS); at.run()
check(len(POSTS) == n, "같은 값이면 다시 기록 안 함")
[b for b in at.button if "search" in str(b.key) and "조회" in str(b.label)][0].click().run()
check("재무 분석 조회" in feats(), f"조회 버튼 기록 {feats()}")
at.text_input(key="calc_a").input("100").run(); at.text_input(key="calc_b").input("120").run(); at.run()
check(feats().count("계산기") == 1, f"계산기는 세션당 10분에 한 번 {feats().count('계산기')}")
p = POSTS[0]; check(p["use"] is True and len(p["sid"]) == 8 and p["me"] == 0 and "T" in p["items"][0]["t"], f"보내는 내용 {p}")
# 관리자 통계 탭
at2 = AppTest.from_file(REPO + "/app.py", default_timeout=300); at2.query_params["lab"] = "1"; at2.query_params["me"] = "1"; at2.run()
check([t.label for t in at2.tabs][:10] == ["오늘의 현황", "기업 브리핑", "재무 분석", "주가 분석", "공시 분석", "산업 뉴스", "AI 무역 통계", "실적 캘린더", "정기보고서", "재계 순위"] and "사용 통계" in [t.label for t in at2.tabs] and not at2.exception, "위 탭 줄은 그대로, 아래에 관리자 탭")
dfs = [d.value for d in at2.dataframe if "기능" in d.value.columns]
check(dfs and list(dfs[0]["기능"])[0] == "뉴스 조회: 통신" and list(dfs[0]["횟수"])[0] == 4, f"기능별 표(내 사용 제외) {dfs[0].to_dict('records') if dfs else None}")
at2.radio(key="px_mode").set_value("업계 비교").run(); time.sleep(0.5)
check(any(p and p["me"] == 1 for p in POSTS), "?me=1이면 me=1로 보냄")
allmd = " ".join(m.value for m in at2.markdown)
check("AI·" not in " ".join(" ".join(map(str, d["기능"])) for d in dfs if "기능" in d), "기능별 표에 AI 기록 없음")
check("Gemini(무료)" in allmd and "8번" in allmd and "Claude로 넘어감(유료)" in allmd and "2번" in allmd and "20%" in allmd, "AI 호출 칸: Gemini 8 · Claude 2 (20%)")
at2.radio(key="use_days").set_value(0).run()
check(list(at2.radio(key="use_days").options) == ["오늘", "최근 7일", "최근 30일", "최근 90일"] and not at2.exception, f"기간에 '오늘' {list(at2.radio(key='use_days').options)}")
check(any("새 버전으로 배포" in w.value for w in at2.warning), "예전 Apps Script면 '오늘' 안내")
import datetime as _dt
STATS["from"] = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).strftime("%Y-%m-%d")
import streamlit as _st; _st.cache_data.clear()  # 사용 통계는 10분 저장되므로 비우고 다시 읽는다
at2.radio(key="use_days").set_value(7).run(); at2.radio(key="use_days").set_value(0).run()
check(not any("새 버전으로 배포" in w.value for w in at2.warning) and "오늘" in " ".join(m.value for m in at2.markdown), "새 Apps Script면 '오늘' 카드")
print("실패", fails)
