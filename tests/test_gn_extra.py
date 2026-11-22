"""외신 수집 기업 추가(2026.11.22): 한국 기업 10곳 + 외신 전용 해외 기업, 짧은 별칭 문맥(SK On·KAI·Mercedes), F1 거르기, 재계 낱말 (가짜 자료)"""
import pathlib, sys
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import gnews_calc as gc  # noqa: E402
import news_digest as nd  # noqa: E402
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
g = gc.global_sectors(nd.A["OVERSEAS_GROUPS"], nd.A["FOREIGN_ENGLISH"], nd.A["OVERSEAS_NAMES"])
check(all(f in g["자동차"] for f in ["Volkswagen", "Mercedes-Benz", "BMW", "Stellantis", "Waymo", "Rivian", "Gotion", "Tesla"]), "자동차: 외신 전용 7곳 더함(기존 기업 그대로)")
check(all(f in g["방산·항공우주"] for f in ["CSSC", "Huntington Ingalls", "Rheinmetall", "L3Harris", "Boeing"]), "방산·항공우주: 조선·방산 4곳")
check(all(f in g["철강"] for f in ["Nippon Steel", "U.S. Steel", "Cleveland-Cliffs", "Nucor"]) and all(f in g["에너지·정유"] for f in ["Westinghouse", "Cheniere"]), "철강·에너지")
new_kr = ["Hyundai Mobis", "SK On", "Hyundai Rotem", "LIG Nex1", "Korea Aerospace", "Hyundai Steel", "SK Innovation", "S-Oil", "KEPCO", "Doosan Enerbility"]
check(all(f in g[gc.KOREA_GROUP] for f in new_kr + ["Samsung Electronics", "Airbus"]), "한국 기업 10곳 더함(기존·에어버스 그대로)")
check(sum(len(v) for v in g.values()) <= 110, f"검색 기업 수가 크게 늘지 않음 ({sum(len(v) for v in g.values())})")
# 화면 섹터 짝
check(all(f in gc.view_companies("차·배터리", g) for f in ["Hyundai Mobis", "SK On", "BMW"]), "차·배터리 화면에 현대모비스·SK온·BMW")
check(all(f in gc.view_companies("중후장대", g) for f in ["Hyundai Rotem", "LIG Nex1", "Hyundai Steel", "Rheinmetall", "CSSC"]), "중후장대 화면에 현대로템·LIG·현대제철·라인메탈·CSSC")
check(all(f in gc.view_companies("에너지·화학", g) for f in ["SK Innovation", "S-Oil", "KEPCO", "Doosan Enerbility", "Westinghouse"]), "에너지·화학 화면에 SK이노·에쓰오일·한전·두산에너빌리티")
# 별칭·문맥
th = lambda t, firm: gc.title_has(t, gc.aliases(firm))
check(th("LIG D&A wins Saudi missile deal", "LIG Nex1") and th("LIG Nex1 shares jump", "LIG Nex1"), "LIG는 새 이름(LIG D&A)·옛 이름 모두")
check(th("SK On to supply batteries to Ford", "SK On") and not th("SK on track to beat estimates", "SK On"), "SK On은 배터리 문맥일 때만")
check(th("KAI wins Polish FA-50 deal", "Korea Aerospace") and not th("Kai Havertz scores twice", "Korea Aerospace"), "KAI는 항공 문맥일 때만")
check(not th("Hamilton leaves Mercedes for Ferrari", "Mercedes-Benz") and th("Mercedes cuts China sales outlook", "Mercedes-Benz") and th("Mercedes-Benz to buy LG batteries", "Mercedes-Benz"), "Mercedes만 걸리면 자동차 문맥일 때만(F1 팀 기사 방지)")
check(gc.is_junk("Mercedes F1 driver Russell wins Grand Prix", "Reuters") and not gc.is_junk("BMW to buy batteries from Samsung SDI", "Reuters"), "F1·Grand Prix 기사는 잡음으로 뺌")
check(any("Section 232" in c for c in gc.korea_cores("재계")) and any("anti-dumping" in c for c in gc.korea_cores("재계")), "한국 관련 재계 낱말: Section 232·anti-dumping")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
