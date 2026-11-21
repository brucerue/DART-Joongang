"""검색어가 다른 낱말의 일부로 들어간 제목은 섹터 기사로 치지 않는다 (재계 ≠ 재계약 등, NEWS_NOT_MATCH)"""
import ast, pathlib, re, sys
REPO = pathlib.Path(__file__).resolve().parents[1]
src = (REPO / "app.py").read_text(encoding="utf-8")
import unicodedata
ns = {"re": re, "unicodedata": unicodedata}
for node in ast.parse(src).body:  # 앱 전체를 실행하지 않고 필요한 정의만 읽는다
    names = [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)] or [getattr(node, "name", "")]
    if any(n in ("NEWS_NOT_MATCH", "key_in_title", "name_key") for n in names):
        exec(compile(ast.Module([node], []), "app.py", "exec"), ns)
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def hit(term, title):
    return ns["key_in_title"](ns["name_key"](term), ns["name_key"](title), title)
check(not hit("재계", "[단독] 차은우, 현 소속사 판타지오와 재계약"), "재계약은 재계 기사가 아님")
check(hit("재계", "재계 총수들, 연말 인사 앞두고 긴장"), "진짜 재계 기사는 그대로")
check(hit("재계", "재계약 앞둔 재계 단체 회장"), "재계약과 재계가 함께 있으면 재계로 인정")
check(not hit("인텔", "인텔리전스 기업 투자"), "인텔 ≠ 인텔리전스(기존 규칙)")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
