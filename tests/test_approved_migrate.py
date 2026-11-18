"""앱 신청·승인으로 들어온 받는 사람의 옛 섹터 이름('반도체'·'전자' 등)을 새 묶음 이름으로 바꿔 받게 (버려서 알림이 안 가던 문제)"""
import os
import sys

sys.path.insert(0, os.getcwd())
import news_digest as nd  # noqa: E402

fails = []


def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        fails.append(m)


out = {o["name"]: o for o in nd.merge_approved([], [
    {"chat_id": "1", "name": "가", "sectors": ["반도체", "IT·통신", "항공", "재계"]},
    {"chat_id": "2", "name": "나", "sectors": ["전자", "반도체", "재계"]},
    {"chat_id": "3", "name": "다", "sectors": ["조선·해운", "자동차"]},
    {"chat_id": "4", "name": "라", "sectors": ["반도체·전자"]},
])}
check(out["가"]["sectors"] == ["반도체·전자", "IT·통신", "항공", "재계"], f"'반도체' -> 반도체·전자 {out['가']['sectors']}")
check(out["가"]["widened"] == ["반도체·전자"], "일부만 받던 묶음은 첫 회차에 기존 기사를 기록만(한꺼번에 몰려오지 않게)")
check(out["나"]["sectors"] == ["반도체·전자", "재계"] and out["나"]["widened"] == [], "둘 다 받던 사람은 그대로 넓어짐 없음")
check(out["다"]["sectors"] == ["중후장대", "차·배터리"], f"다른 묶음도 {out['다']['sectors']}")
check(out["라"]["sectors"] == ["반도체·전자"], "새 이름은 그대로")
check(nd.merge_approved([{"chat_id": "1", "name": "가", "sectors": ["재계"], "modes": ["단독"]}], [{"chat_id": "1", "sectors": ["반도체"]}])[0]["sectors"] == ["재계"],
      "GitHub 명단에 이미 있으면 그쪽이 우선")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
