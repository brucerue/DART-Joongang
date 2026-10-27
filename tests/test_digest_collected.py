"""정시 회차 합치기: 수집기(--modes·--no-disc·--dump)가 모은 기사를 텔레그램 발송이 다시 검색하지 않고 쓰는지, 빠진 칸만 직접 검색하는지 (가짜 응답)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import os, sys, json, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, REPO)
os.environ.pop("DART_API_KEY", None)
import collect_data as cd, news_digest as nd
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
now = datetime.now(nd.KST)
real_collect = nd.collect
calls = []
def fake_collect(mode, sector, cutoff, google=None):
    calls.append((mode, sector))
    if sector == "배터리":
        raise RuntimeError("구글 오류")  # 이 칸은 모으지 못함 -> 발송이 직접 검색해야
    return [{"title": f"[단독] {sector} 기사", "url": f"https://x/{mode}/{sector}", "publisher": "중앙일보", "category": "종합일간지",
             "dt": now - timedelta(hours=1), "tags": [sector]}]
cd.nd.collect = fake_collect
out = tempfile.mkdtemp(); dump = os.path.join(out, "fresh.json")
sys.argv = ["collect_data.py", "--out", out, "--modes", "단독", "--no-disc", "--dump", dump]
cd.main()
check({m for m, _ in calls} == {"단독"}, f"15분 회차: 단독만 모음 {sorted({m for m, _ in calls})}")
d = json.load(open(dump, encoding="utf-8"))
check("단독|자동차" in d and "단독|배터리" not in d and "통신|자동차" not in d, f"모은 칸만 기록 {sorted(d)[:3]}")
check(not os.path.exists(os.path.join(out, cd.general_file("자동차"))), "단독만 모을 땐 일반 파일을 건드리지 않음")
# 발송: 모아 둔 칸은 그대로 쓰고, 빠진 칸(배터리)만 직접 검색
cd.nd.collect = real_collect
os.environ["COLLECTED_FILE"] = dump
nd._COLLECTED.clear()
searched = []
nd.google_search = lambda terms, domains, extra, cutoff: (searched.append(1) or [], None)
got = nd.collect("단독", "자동차", now - timedelta(hours=12))
check([i["title"] for i in got] == ["[단독] 자동차 기사"] and not searched and isinstance(got[0]["dt"], datetime), "모아 둔 칸은 다시 검색하지 않음")
check(nd.collect("단독", "자동차", now - timedelta(minutes=30)) == [], "발송 기준 시각보다 오래된 기사는 뺌")
nd.collect("단독", "배터리", now - timedelta(hours=12))
check(searched, "모으지 못한 칸은 직접 검색")
print("실패", fails); sys.exit(1 if fails else 0)
