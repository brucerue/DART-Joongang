"""20대 품목 증감 순위: 관세청 품목별 자료가 아직 없는 달(모두 0)은 '없는 달'로 보고, 산업부 보도자료 잠정치(수출)로 보여 준다 (가짜 자료, 실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import sys
sys.path.insert(0, REPO)
import trade_calc as tc
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
months = [f"2025-{m:02d}" for m in range(9, 13)] + [f"2026-{m:02d}" for m in range(1, 11)]  # 2025-09 ~ 2026-10 (14개월)
n = len(months)
e = [100 + k for k in range(n)]; e[-1] = 0
im = [50] * n; im[-1] = 0
g = {"months": months, "data": {"반도체": {"CN": [e, im], "US": [[0] * n, [0] * n]}}}
check(tc.groups_empty_months(g) == {"2026-10"}, "모두 0인 달(2026-10)을 찾음")
gc = tc.groups_country(g, months)
check(tc.group_month_rows(gc, months, n - 1, "exp") == [], "그 달은 순위 표에서 빠짐 (0억달러·-100%로 보이지 않게)")
r = tc.group_month_rows(gc, months, n - 2, "exp")[0]
check(r["cur"] == (100 + n - 2) * 1000 and r["prev"] == (100 + n - 14) * 1000, "다른 달은 그대로")
fm = {"month": "2026-10", "items": {"반도체": [15701.0] + [0.0] * 11 + [56438.0], "컴퓨터": [979.0] + [0.0] * 11 + [5845.0]}}
rows = {x["name"]: x for x in tc.flash_item_rows(fm)}
check(abs(rows["반도체"]["cur"] - 56438e6) < 1 and abs(rows["반도체"]["prev"] - 15701e6) < 1, "보도자료 품목(백만 달러) -> 달러, 전년 같은 달과 비교")
check(round(rows["컴퓨터"]["chg"], 1) == 497.0, "증감률 계산")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
