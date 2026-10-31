"""수집기: '일반'은 섹터별 파일(general/…json)에 최근 3일만, 통신·단독은 archive/recent에. 모든 산업 뉴스 섹터(IT·통신 포함)를 모은다 (가짜 응답)"""
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
now = datetime.now(cd.KST)
check("IT·통신" in cd.SECTORS and set(cd.SECTORS) == set(nd.A["NEWS_SECTORS"]), f"모든 섹터 수집 {cd.SECTORS}")
calls = []
def fake_collect(mode, sector, cutoff, google=None):
    calls.append((mode, sector))
    if sector != "자동차":
        return []
    t = {"통신": "현대차 통신 기사", "단독": "[단독] 현대차 단독 기사", "일반": "현대차 일반 기사"}[mode]
    return [{"title": t, "url": f"https://x/{mode}", "publisher": "중앙일보", "dt": now - timedelta(hours=1), "tags": ["현대차"]}]
cd.nd.collect = fake_collect
out = tempfile.mkdtemp()
g = os.path.join(out, cd.general_file("자동차"))
os.makedirs(os.path.dirname(g), exist_ok=True)
old = (now - timedelta(days=5)).strftime(cd.FMT); keep = (now - timedelta(days=2)).strftime(cd.FMT)
json.dump({"items": [{"t": "오래된 일반", "u": "https://old", "p": "a", "k": "일반", "d": old, "s": ["자동차"], "g": []},
                     {"t": "이틀 전 일반", "u": "https://keep", "p": "a", "k": "일반", "d": keep, "s": ["자동차"], "g": []}]}, open(g, "w"))
sys.argv = ["collect_data.py", "--out", out, "--modes", "통신,단독,일반"]  # 외신은 test_gn_collect.py에서
cd.main()
gen = json.load(open(g, encoding="utf-8"))
titles = [r["t"] for r in gen["items"]]
check(titles == ["현대차 일반 기사", "이틀 전 일반"], f"일반 파일: 새 기사 + 3일 안 기사만 {titles}")
check(gen.get("sector") == "자동차" and gen.get("updated"), "일반 파일에 섹터·갱신 시각")
rec = json.load(open(os.path.join(out, "recent.json"), encoding="utf-8")); arc = json.load(open(os.path.join(out, "archive.json"), encoding="utf-8"))
check(sorted(r["k"] for r in rec["items"]) == ["단독", "통신"] and all(r["k"] != "일반" for r in arc["items"]), "일반은 recent·archive에 안 들어감")
check(all(os.path.exists(os.path.join(out, cd.general_file(s))) for s in cd.SECTORS), "섹터마다 일반 파일")
check({m for m, _ in calls} == {"통신", "단독", "일반"} and len(calls) == 3 * len(cd.SECTORS), f"섹터마다 통신·단독·일반 조회 {len(calls)}번")
# 앱과 파일 이름 규칙이 같은지
import ast, hashlib
src = open(REPO + "/app.py", encoding="utf-8").read()
fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "general_file")
ns = {"hashlib": hashlib}; exec(compile(ast.Module([fn], []), "x", "exec"), ns)
check(all(ns["general_file"](s) == cd.general_file(s) for s in cd.SECTORS), "앱과 수집기의 일반 파일 이름이 같음")
print("실패", fails); sys.exit(1 if fails else 0)
