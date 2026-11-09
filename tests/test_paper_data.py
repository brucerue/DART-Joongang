"""지면 목록 수집기: 면·제목·주소 읽기, [사진] 빼기, 언론사 이름 대조, 다른 날짜 시각 표시(posted), 이미 ok인 날 건너뛰기, 실패는 status (가짜 페이지)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import json, os, sys, tempfile
sys.path.insert(0, REPO)
from datetime import date, datetime, timedelta, timezone
import paper_data as pd_
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def li(oid, aid, title, when=""):
    stamp = f'<span class="date">{when}</span>' if when else ""
    return f'<li><a href="https://n.news.naver.com/article/newspaper/{oid}/{aid}?date=20261109" class="x"><div><strong>{title}</strong><p></p>{stamp}</div></a></li>'
def page(oid, name, blocks):
    body = "".join(f'<h3><span class="page_notation"><em>{pg}</em>면</span></h3><ul class="newspaper_article_lst">' + "".join(li(oid, *x) for x in arts) + "</ul>" for pg, arts in blocks)
    return f'<div class="press_name">\n\t {name} \n</div>{body}'
PAGES = {
    "025": page("025", "중앙일보", [("A1", [("0001", "트럼프 마이웨이"), ("0002", "[사진] 우승 한 풀었다")]), ("E4", [("0003", "금호석화마저 적자 &amp; 혹한기", "2026.11.07. 오후 6:00"), ("0004", "같은 날 기사", "2026.11.09. 오전 12:03")])]),
    "023": page("023", "조선일보", []),
    "020": page("020", "한국일보", [("A1", [("0009", "무언가")])]),
}
calls = []
def fetch(url):
    calls.append(url)
    if "media.naver.com" in url:
        oid = url.split("/press/")[1][:3]
        if oid == "015":
            raise RuntimeError("HTTP 503")
        return PAGES.get(oid, page(oid, pd_.PAPERS.get(oid, ""), []))
    return ""
now = datetime(2026, 11, 9, 7, 40, tzinfo=timezone(timedelta(hours=9)))
r = pd_.collect_one("025", date(2026, 11, 9), fetch, now, wait=lambda: None)
check(r["status"] == "ok" and r["paper"] == "중앙일보" and r["pages_found"] == 2, f"중앙일보: ok, 면 2개 {r['status']}")
check([i["title"] for i in r["items"]] == ["트럼프 마이웨이", "금호석화마저 적자 & 혹한기", "같은 날 기사"], "[사진] 빼고 제목(HTML 기호 풀기)")
check(r["items"][0] == {"pg": "A1", "title": "트럼프 마이웨이", "url": "https://n.news.naver.com/article/025/0001"}, "항목: pg·title·url (목록에 시각이 없으면 posted 없음)")
check(r["items"][1].get("posted") == "2026-11-07 18:00", "목록에 다른 날짜 시각이 보이면 posted 표시 (오후 6시 -> 18:00)")
check("posted" not in r["items"][2] and "time" not in r["items"][2], "같은 날 시각이면 posted 없음")
check(not any("n.news.naver.com" in c for c in calls), "기사 페이지는 열지 않음 (목록 페이지만 요청)")
check(pd_.collect_one("020", date(2026, 11, 9), fetch, now, wait=lambda: None)["status"].startswith("name_mismatch"), "언론사 번호와 신문명이 다르면 name_mismatch")
check(pd_.collect_one("023", date(2026, 11, 9), fetch, now, wait=lambda: None)["status"] == "empty", "목록이 비면 empty")
check(pd_.collect_one("015", date(2026, 11, 9), fetch, now, wait=lambda: None)["status"].startswith("error"), "받기 실패는 error")
root = tempfile.mkdtemp()
days = [date(2026, 11, 8), date(2026, 11, 9)]  # 11/8 일요일
done, bad = pd_.run(root, days, ["025", "015"], fetch, now, wait=lambda: None)
idx = json.load(open(os.path.join(root, "paper", "index.json"), encoding="utf-8"))
check(done == 2 and list(idx["dates"]) == ["2026-11-09"] and idx["dates"]["2026-11-09"]["025"]["n"] == 3, f"일요일은 건너뜀, 인덱스 {idx['dates']}")
f = json.load(open(os.path.join(root, "paper", "2026-11-09", "025.json"), encoding="utf-8"))
check(set(f) >= {"date", "oid", "paper", "collected_at", "pages_found", "items", "status"}, "파일 항목")
calls.clear(); pd_.run(root, days, ["025", "015"], fetch, now, wait=lambda: None)
check(any("/press/025/" in c for c in calls), "07:40에 받은 ok는 덜 올라왔을 수 있어 다시 받음 (10시 전)")
later = now.replace(hour=10, minute=40)
pd_.run(root, days, ["025", "015"], fetch, later, wait=lambda: None)
calls.clear(); pd_.run(root, days, ["025", "015"], fetch, later.replace(hour=11), wait=lambda: None)
check(not any("/press/025/" in c for c in calls) and any("/press/015/" in c for c in calls), "10시 이후 받은 ok는 건너뛰고 실패한 지면만 다시")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
