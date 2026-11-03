"""공시 알람: 같은 날 같은 회사·같은 제목 공시는 한 줄로 묶고 (+N건) (가짜 자료)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import os, sys
sys.path.insert(0, REPO)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake")
from datetime import datetime
import news_digest as nd
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
it = lambda no, t, day="20261103": {"title": t, "url": f"u{no}", "publisher": "DART", "key": no, "day": day}
items = [it("1", "[샘표식품] 기업설명회(IR)개최(안내공시)"), it("2", "[샘표식품] 기업설명회(IR)개최(안내공시)"),
         it("3", "[대상] 기업설명회(IR)개최(안내공시)"), it("4", "[샘표식품] 기업설명회(IR)개최(안내공시)", "20261102")]
cl = nd.disclosure_clusters(items)
check(len(cl) == 3 and cl[0]["rep"]["key"] == "1" and [o["key"] for o in cl[0]["others"]] == ["2"], "같은 날 같은 제목은 한 묶음, 다른 회사·다른 날은 따로")
msg = nd.build_messages("공시", datetime(2026, 11, 3, 17, 0), [("식품", cl, 0)])[0]
check(msg.count("샘표식품") == 2 and "(+1건)" in msg, "메시지: 샘표 두 줄(오늘 묶음 1 + 어제 1), 묶음엔 (+1건)")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
