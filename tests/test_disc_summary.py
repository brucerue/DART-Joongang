"""주요 공시 AI 한 줄 요약: 숫자 대조, 원문 없음·AI 실패 시 요약 없이, 같은 공시는 다시 안 부름, 메시지에 줄로 붙음 (가짜 응답, 실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import os, sys
sys.path.insert(0, REPO)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake")
os.environ["DART_API_KEY"] = "fake"
os.environ["GEMINI_API_KEY"] = "fake"
from datetime import datetime
import news_digest as nd
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)

DOC = {"1": "단일판매ㆍ공급계약체결 | 계약금액(원) | 1,234,500,000,000 | 최근 매출액 대비(%) | 12.3 | 계약기간 2026-11-01 ~ 2028-12-31",
       "2": "유상증자결정 | 신주의 수 | 5,000,000 | 발행가액 | 32,000"}
def fake_doc(key, no):
    if no == "3":
        raise nd.DocNotReady(no)
    return DOC[no]
nd.fetch_doc_text = fake_doc
calls = []
class R:
    def __init__(s, code, text): s.status_code, s._t = code, text
    def json(s): return {"candidates": [{"content": {"parts": [{"text": s._t}]}}]}
def fake_post(url, json=None, headers=None, timeout=None):
    calls.append(url)
    txt = json["contents"][0]["parts"][0]["text"]
    if "계약금액" in txt:
        if "3.6" in url:
            return R(429, "")  # 첫 모델 한도 -> 다음 모델
        return R(200, "1조2,345억원 규모 공급계약(매출 대비 12.3%), 2028-12-31까지")
    return R(200, "신주 7,000,000주를 주당 32,000원에 발행")  # 원문에 없는 숫자 -> 버림
nd.requests.post = fake_post

check(nd.summary_ok("계약금액 1,234,500,000,000원, 매출 대비 12.3%", DOC["1"]), "원문에 있는 숫자면 통과")
check(not nd.summary_ok("계약금액 9,999원", DOC["1"]), "원문에 없는 숫자면 버림")

items = [{"key": n, "title": f"[가나] 공시{n}", "url": f"u{n}", "publisher": "DART"} for n in ("1", "2", "3")]
items.append(dict(items[0]))  # 같은 공시가 두 섹터에 걸린 경우
seen = {}
now = datetime(2026, 11, 2, 10, 0, tzinfo=nd.KST)
changed = nd.summarize_disclosures(items, seen, now)
check("1조2" not in (items[0].get("detail") or ""), "'1조2,345억원'처럼 원문 표기와 다른 숫자는 버림")
check(changed and seen.get("sum|1", "").endswith("|") and seen.get("sum|2", "").endswith("|"), "실패한 요약도 기록해 다시 안 부름")
check("sum|3" not in seen and not items[2].get("detail"), "원문이 아직 없으면 요약 없이, 기록도 안 남김")

# 원문 숫자 그대로 쓴 요약은 붙는다
def fake_post2(url, json=None, headers=None, timeout=None):
    calls.append(url)
    return R(200, "계약금액 1,234,500,000,000원(최근 매출액 대비 12.3%), 계약기간 2028-12-31까지")
nd.requests.post = fake_post2
seen2 = {}
it = [{"key": "1", "title": "[가나] 공급계약", "url": "u", "publisher": "DART"}, {"key": "1", "title": "[가나] 공급계약", "url": "u", "publisher": "DART"}]
nd.summarize_disclosures(it, seen2, now)
check(all("↳ 계약금액 1,234,500,000,000원" in x.get("detail", "") for x in it), "요약이 같은 공시 모든 항목에 붙음")
n = len(calls)
again = [{"key": "1", "title": "[가나] 공급계약", "url": "u", "publisher": "DART"}]
nd.summarize_disclosures(again, seen2, now)
check(len(calls) == n and "↳ 계약금액" in again[0].get("detail", ""), "다음 회차엔 기록된 요약을 다시 씀 (AI 안 부름)")
msg = nd.build_messages("공시", now, [("자동차", [{"rep": again[0], "others": []}], 0)])[0]
check("\n   ↳ 계약금액" in msg, "메시지에서 공시 줄 아래 요약 줄")

os.environ["GEMINI_API_KEY"] = ""
x = [{"key": "9", "title": "t", "url": "u", "publisher": "DART"}]
check(nd.summarize_disclosures(x, {}, now) is False and not x[0].get("detail"), "키가 없으면 요약 없이 그대로")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
