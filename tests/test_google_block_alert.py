"""수집 실패가 전부 '구글이 막음'이면 3회 연속일 때만 실패 알림, 다른 실패는 바로 알림. 밤엔 외신을 2시간마다만, 외신도 구글이 막히면 건너뜀 (가짜 응답)"""
import pathlib, sys, tempfile, json, os
from datetime import datetime
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import requests  # noqa: E402
import collect_data as cd, news_digest as nd  # noqa: E402
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
blk = ["차·배터리/통신: 구글 뉴스 오류: 503 Server Error: Service Unavailable for url: https://news.google.com/rss",
       "중후장대/단독: 구글 뉴스 오류: 구글 뉴스가 잠시 막혀 이번 회차는 건너뜀",
       "외신 반도체/Nvidia: HTTPError 503", "외신 자동차/한국: " + cd.GOOGLE_SKIP, "외신 철강/Nucor: ReadTimeout"]
check(all(cd.google_block_error(e) for e in blk), "503·막혀 건너뜀·시간 초과는 '구글 막힘'")
check(not any(cd.google_block_error(e) for e in ["공시: DART 오류", "외신 반도체/Nvidia: HTTPError 404", "외신 반도체/Nvidia: HTTPError", "반도체/통신: KeyError 'x'"]),
      "공시·404·코드 오류는 '구글 막힘'이 아님")
out = tempfile.mkdtemp()
r1 = cd.block_verdict(blk, 5, out); r2 = cd.block_verdict(blk, 5, out); r3 = cd.block_verdict(blk, 5, out)
check(r1[0] is False and r2[0] is False and r3[0] is True, f"구글 막힘만이면 1·2회는 알림 없이, 3회 연속이면 알림 {r1} {r3}")
check(cd.block_verdict(blk[:2], 5, out) == (False, "") and json.load(open(os.path.join(out, cd.BLOCK_FILE)))["streak"] == 0, "정상(일부 실패) 회차가 오면 연속 횟수 0으로")
check(cd.block_verdict(blk + ["공시: 오류"], 5, out)[0] is True, "다른 원인이 섞여 거의 전부 실패하면 바로 알림")
check(cd.block_verdict(blk, 5, out)[0] is False, "그 뒤 구글 막힘 1회는 다시 알림 없음")
K = cd.KST
check(cd.global_due(datetime(2026, 11, 28, 9, tzinfo=K)) and cd.global_due(datetime(2026, 11, 28, 22, tzinfo=K))
      and not cd.global_due(datetime(2026, 11, 28, 23, tzinfo=K)) and not cd.global_due(datetime(2026, 11, 28, 3, tzinfo=K)), "외신: 낮엔 매시, 밤엔 짝수 시에만")
# 외신도 구글 막힘을 세어 8번 연달아 막히면 나머지는 묻지 않음
asked = []
def fake_get(url, params=None, **k):
    asked.append(1)
    r = requests.models.Response(); r.status_code = 503
    raise requests.HTTPError("503", response=r)
cd.requests.get = fake_get
nd.GOOGLE_STATE.update(down_until=0.0, streak=0)
errors = []
cd.collect_global(tempfile.mkdtemp(), datetime.now(K), errors)
check(len(asked) < 30 and len(errors) > 100, f"외신: 구글이 막히면 나머지는 묻지 않고 건너뜀 (물은 수 {len(asked)}, 건너뜀 포함 실패 {len(errors)})")
check(all(cd.google_block_error(e) for e in errors), "외신 실패가 모두 '구글 막힘'으로 분류됨")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
