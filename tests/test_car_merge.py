"""자동차·배터리 -> '차·배터리' 묶음 (뉴스·알람·캘린더), 재무·주가는 따로, 옛 설정·자료 이름 바꾸기, 넓어진 사람은 첫 회차 조용히 (가짜 자료)"""
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
A = nd.A
check("차·배터리" in A["NEWS_SECTORS"] and "자동차" not in A["NEWS_SECTORS"] and "배터리" not in A["NEWS_SECTORS"], "산업 뉴스 섹터: 차·배터리 하나")
terms = A["NEWS_SECTORS"]["차·배터리"]
check("현대차" in terms["기업"] and "LG에너지솔루션" in terms["기업"] and "전기차" in terms["키워드"] and "양극재" in terms["키워드"], "검색어: 두 섹터를 합침")
check(list(A["ALARM_SECTORS"]) == ["차·배터리", "중후장대", "에너지·화학", "IT·통신", "반도체·전자", "항공", "재계", "유통", "식품", "뷰티·패션"], "알람 섹터")
check(A["TEAM_PRESETS"]["차팀"] == ["차·배터리", "중후장대", "에너지·화학"] and nd.SECTORS == A["TEAM_PRESETS"]["차팀"], "차팀·기본 섹터")
check(A["TEAM_PRESETS"]["재계팀"] == ["반도체·전자", "항공", "재계"], "재계팀")
check("자동차" in A["INDUSTRY_GROUPS"] and "배터리" in A["INDUSTRY_GROUPS"], "재무·주가는 자동차·배터리 따로")
check(nd.migrate_sectors(["자동차", "항공"]) == (["차·배터리", "항공"], ["차·배터리"]), "옛 설정 '자동차'만 -> 차·배터리 (넓어짐 표시)")
check(nd.migrate_sectors(["자동차", "배터리"]) == (["차·배터리"], []), "둘 다 받던 사람 -> 넓어짐 아님")
check(nd.migrate_sectors(["방산"])[1] == ["중후장대"], "중후장대 옛 규칙 그대로")
# 에너지·화학 = 정유·석화+에너지, 반도체·전자 = 반도체+전자
for gone in ("정유·석화", "에너지", "반도체", "전자"):
    check(gone not in A["NEWS_SECTORS"], f"산업 뉴스: '{gone}' 섹터 없음")
check({"LG화학", "고려아연"} <= set(A["NEWS_SECTORS"]["에너지·화학"]["기업"]) and {"SK하이닉스", "LG전자"} <= set(A["NEWS_SECTORS"]["반도체·전자"]["기업"])
      and A["NEWS_SECTORS"]["반도체·전자"]["기업"].count("삼성전자") == 1, "검색어 합침 (삼성전자 한 번)")
check(all(x in A["INDUSTRY_GROUPS"] for x in ("정유·석화", "에너지", "반도체", "전자")), "재무·주가는 낱개 그대로")
check(nd.migrate_sectors(["정유·석화", "반도체", "항공"]) == (["에너지·화학", "반도체·전자", "항공"], ["에너지·화학", "반도체·전자"]), "옛 설정 -> 새 이름 (넓어짐)")
check(nd.migrate_sectors(["정유·석화", "에너지"]) == (["에너지·화학"], []) and nd.migrate_sectors(["에너지·소재"]) == (["에너지·화학"], []), "둘 다 받던 사람·에너지·소재")
check(nd.migrate_sectors(["에너지·화학"]) == (["에너지·화학"], []), "새 이름을 고른 사람은 그대로")
feed2 = [{"corp_name": n, "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": str(i)} for i, n in enumerate(["LG화학", "고려아연", "SK하이닉스", "LG전자"])]
check({i["key"] for i in nd.disclosure_items(feed2, "에너지·화학")} == {"0", "1"} and {i["key"] for i in nd.disclosure_items(feed2, "반도체·전자")} == {"2", "3"}, "공시 알람: 묶음의 기업 모두")
r = nd.run_command("/섹터 반도체전자 에너지화학 정유", {"chat_id": "1", "name": "x", "sectors": ["항공"], "modes": ["통신"]}, {})
check("반도체·전자" in r and "에너지·화학" in r and "알아듣지" not in r, f"/섹터: 점 없이·옛 이름도 {r[:60]}")
feed = [{"corp_name": "삼성SDI", "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": "1"}, {"corp_name": "현대자동차", "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": "2"},
        {"corp_name": "삼성전자", "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": "3"}]
got = {i["key"] for i in nd.disclosure_items(feed, "차·배터리")}
check(got == {"1", "2"}, f"공시 알람: 자동차·배터리 기업 모두 {got}")
check("차·배터리" in __import__("gnews_calc").KOREA_TOPICS, "외신 한국 관련 낱말")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
