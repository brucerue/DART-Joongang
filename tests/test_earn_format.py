"""잠정실적 원문 표 읽기: 예전 5칸 양식과 2026.10에 확인된 7칸 양식(흑자적자전환여부 칸 추가) 모두 전년비·전분기비를 읽는지 (가짜 응답, 실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import os, sys
sys.path.insert(0, REPO)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake")
import news_digest as nd
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
# 7칸 양식: 칸마다 줄이 나뉜 원문 (HD현대일렉트릭 2026.10.27 공시 모양, 단위 억원)
seven = "\n".join(x + " |" for x in """(단위 : 억원)
구분
당해실적
전기실적
전기대비증감율(%)
흑자적자전환여부
전년동기실적
전년동기대비증감율(%)
흑자적자전환여부
매출액
당해실적
11,799
11,418
3.3
-
9,954
18.5
-
누계실적
33,582
-
-
-
29,163
15.2
-
영업이익
당해실적
2,845
2,870
-0.9
-
2,471
15.1
-
당기순이익
당해실적
-120
300
-
적자전환
-50
-
적자지속""".split("\n"))
r = nd.parse_earnings(seven)
check(r and r["rev"]["prev_y"] == 9954e8 and r["rev"]["yoy"] == 18.5 and r["rev"]["qoq"] == 3.3, f"7칸 매출: 전년동기·전년비·전분기비 {r and r['rev']}")
check(r and r["op"]["prev_y"] == 2471e8 and r["op"]["yoy"] == 15.1, f"7칸 영업이익 {r and r['op']}")
check(r and r["net"]["qoq"] == "적자전환" and r["net"]["yoy"] == "적자지속", f"7칸 순이익: 전환여부 칸 {r and r['net']}")
d = nd.earnings_detail(r)
print(d)
check("매출 1조 1,799억 (전년比 +18.5% · 전분기比 +3.3%)" in d, "알림 줄에 전년비·전분기비 모두")
check("영업이익 2,845억 (전년比 +15.1% · 전분기比 -0.9%)" in d, "영업이익 줄")
# 예전 5칸 양식: 한 줄 표
five = "(단위 : 억원)\n구분 | 당해실적 | 전기실적 | 전기대비증감율 | 전년동기실적 | 전년동기대비증감율\n매출액 | 1,000 | 900 | 11.1 | 800 | 25.0\n영업이익 | 100 | 120 | -16.7 | 90 | 11.1"
r5 = nd.parse_earnings(five)
check(r5 and r5["rev"]["prev_y"] == 800e8 and r5["rev"]["yoy"] == 25.0 and r5["op"]["qoq"] == -16.7, f"5칸 양식도 그대로 {r5}")
print("실패", fails); sys.exit(1 if fails else 0)
