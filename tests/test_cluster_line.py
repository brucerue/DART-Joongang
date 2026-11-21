"""텔레그램 알람: 같은 사건 묶음은 대표 기사 아래 다른 매체 제목을 2개까지 보여 주고 나머지는 (+N건) (가짜 자료)"""
import pathlib, sys
from datetime import datetime
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import news_digest as nd  # noqa: E402
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def it(t, p, u): return {"title": t, "publisher": p, "url": u}
c = {"rep": it("[단독] 삼성, 애플 D램 장기계약", "매일경제", "https://a/1"),
     "others": [it("삼성 애플 D램 계약 (사진)", "매일경제", "https://a/2"), it("[단독] 애플, 삼성 D램 장기 공급", "한국경제", "https://b/1"),
                it("애플-삼성 D램 계약", "서울경제", "https://c/1"), it("애플 D램 계약 삼성", "이데일리", "https://d/1")]}
t = nd.cluster_line(c, "단독")
lines = t.split("\n")
check(len(lines) == 3 and "(+2건)" in lines[0], f"대표 + 다른 매체 2줄, 나머지 (+2건) {lines[0][-12:]}")
check("한국경제" in lines[1] and "서울경제" in lines[2] and "└" in lines[1] and "https://b/1" in lines[1], "같은 매체(매일경제) 사진 기사는 건너뛰고 다른 매체 제목")
check(nd.cluster_line({"rep": c["rep"], "others": []}, "단독").count("\n") == 0 and "(+" not in nd.cluster_line({"rep": c["rep"], "others": []}, "단독"), "묶음이 없으면 한 줄")
check(nd.cluster_line({"rep": c["rep"], "others": c["others"][:1]}, "단독").endswith("(+1건)"), "같은 매체만 묶이면 예전처럼 (+1건)")
check("└" not in nd.cluster_line(c, "공시") and "(+4건)" in nd.cluster_line(c, "공시"), "공시는 예전처럼 (+N건)만")
msgs = nd.build_messages("단독", datetime(2026, 11, 21, 9, 0), [("반도체·전자", [c], 0)])
check(len(msgs) == 1 and "한국경제" in msgs[0], "메시지에 다른 매체 제목이 들어감")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
