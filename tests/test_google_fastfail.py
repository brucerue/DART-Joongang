"""구글 뉴스가 막히면(503·429·시간 초과) 그 회차의 남은 구글 검색을 바로 건너뛴다 (가짜 응답)"""
import pathlib, sys, time
from datetime import datetime
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import requests  # noqa: E402
import news_digest as nd  # noqa: E402
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
calls = []
def fake_fetch(term, domains, extra, cutoff):
    calls.append(term)
    if term == "A":
        r = requests.models.Response(); r.status_code = 503
        raise requests.HTTPError("503 Service Unavailable", response=r)
    time.sleep(0.05)
    return [{"title": term, "url": f"https://x/{term}", "publisher": "p", "category": "언론", "dt": datetime.now(nd.KST)}]
nd.google_fetch = fake_fetch
nd.GOOGLE_STATE["down_until"] = 0.0
terms = ["A"] + [f"T{i}" for i in range(40)]
items, err = nd.google_search(terms, ["a.com"], "단독", datetime.now(nd.KST))
check(len(calls) < 20, f"503 뒤 남은 검색은 묻지 않고 건너뜀 (물은 수 {len(calls)}/{len(terms)})")
check(nd.GOOGLE_STATE["down_until"] > time.monotonic(), "막힘 기록(쉬는 시간)")
calls.clear()
items2, err2 = nd.google_search(["B", "C"], ["a.com"], "", datetime.now(nd.KST))
check(calls == [] and err2 and "건너뜀" in err2, f"쉬는 동안 다른 섹터 검색도 바로 건너뜀 {err2}")
nd.GOOGLE_STATE["down_until"] = 0.0
items3, err3 = nd.google_search(["B"], ["a.com"], "", datetime.now(nd.KST))
check(items3 and err3 is None, "쉬는 시간이 지나면 다시 검색")
r = requests.models.Response(); r.status_code = 404
check(not nd.google_blocked(requests.HTTPError("404", response=r)) and nd.google_blocked(requests.Timeout()), "404는 막힘이 아니고, 시간 초과는 막힘")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
