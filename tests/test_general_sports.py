import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, tempfile, datetime as dt
sys.path.insert(0, REPO)
os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
import news_digest as nd
now = dt.datetime.now(nd.KST)
def it(title, pub, cat): return {"title": title, "url": "https://x/" + str(abs(hash(title))), "publisher": pub, "category": cat, "dt": now, "domain": "x"}
rows = [it("현대차 신형 전기차 공개", "중앙일보", "종합일간지"), it("현대모비스 농구단, 개막전 승리", "한국경제", "경제지"),
        it("기아, 올림픽 공식 후원 연장", "연합뉴스", "통신사"), it("인천시 현대차 공장 인근 도로 확장", "연합뉴스", "통신사"), it("현대차 노조 교섭", "연합뉴스", "통신사")]
nd.google_search = lambda terms, domains, extra, cutoff: (list(rows), None)
nd._SPORTS_KEYS.clear(); nd._SPORTS_KEYS.append((nd.title_key("인천시 현대차 공장 인근 도로 확장"),))
out = [i["title"] for i in nd.collect("일반", "자동차", now - dt.timedelta(hours=12))]
print(out)
assert out == ["현대차 신형 전기차 공개", "현대차 노조 교섭"], out
print("nd ok")
