"""실적 캘린더: 같은 회사의 실적 발표 IR과 NDR(증권사 주관 1대1·그룹 미팅)을 구별 — 2026.10 한화에어로스페이스·현대제철 공시 원문 모양 (실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import ast, re, sys
src = open(REPO + "/app.py", encoding="utf-8").read()
ns = {"re": re}
for n in ast.parse(src).body:
    if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "IR_NDR_WORDS") or (isinstance(n, ast.FunctionDef) and n.name == "ir_is_ndr"):
        exec(compile(ast.Module([n], []), "x", "exec"), ns)
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
DOCS = {  # 이름: (원문 일부, 개최목적, NDR인지)
    "한화에어로 10/29 실적발표": ("3. 개최목적 | 2026년 3분기 기업설명회 | 4. 개최방법 | Conference Call, Web Casting | 5. 후원기관 | 자사주관 |", "2026년 3분기 기업설명회", False),
    "한화에어로 10/30 NDR": ("3. 개최목적 | 2026년 3분기 경영실적 설명을 통한 투자자 이해도 제고 | 4. 개최방법 | 1대1 미팅 및 그룹 미팅 | 5. 후원기관 | DS투자증권 |",
                         "2026년 3분기 경영실적 설명을 통한 투자자 이해도 제고", True),
    "현대제철 10/30 실적발표": ("3. 개최목적 | 2026년 3분기 경영실적 발표 | 4. 개최방법 | 컨퍼런스콜 | 5. 후원기관 | - |", "2026년 3분기 경영실적 발표", False),
    "현대제철 11/3 NDR": ("3. 개최목적 | 국내 NDR 실시 | 4. 개최방법 | One-on-One 및 소규모 그룹 미팅(컨퍼런스콜) | 5. 후원기관 | 하나증권 |", "국내 NDR 실시", True),
}
for name, (text, purpose, exp) in DOCS.items():
    check(ns["ir_is_ndr"](text, purpose) == exp, f"{name}: {'NDR(뺌)' if exp else '실적 발표(보임)'}")
print("실패", fails); sys.exit(1 if fails else 0)
