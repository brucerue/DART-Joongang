"""AI 무역 통계: 품목 제안 중 가장 유력한 하나만 기본 체크"""
import os
import sys

sys.path.insert(0, os.getcwd())
import trade_calc as tc  # noqa: E402

fails = []


def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        fails.append(m)


ev = [{"code": "870380", "confidence": "중간"}, {"code": "자동차", "kind": "group", "confidence": "중간"}]
check(tc.ai_default_pick(ev) == 0, "전기차: 확신이 같으면 맨 앞(870380)만, '자동차'는 체크 안 함")
check(tc.ai_default_pick([{"confidence": "중간"}, {"confidence": "높음"}]) == 1, "확신 높은 것이 뒤에 있으면 그것")
check(tc.ai_default_pick([{"confidence": "낮음"}]) is None and tc.ai_default_pick([]) is None, "모두 낮음이면 체크 없음")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
