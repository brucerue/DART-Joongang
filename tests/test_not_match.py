"""검색어가 다른 낱말의 일부로 들어간 제목은 섹터 기사로 치지 않는다 (재계 ≠ 재계약 등, NEWS_NOT_MATCH)"""
import pathlib, sys
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import news_digest as nd  # 앱 규칙(key_in_title·name_key·is_blocked)을 알람과 같은 방식으로 읽는다
ns = nd.A
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
for term, title in [("가전", "[단독] 세무사 등 국가전문자격 37개, 결과 공개"), ("라면", "신라면세점, 위스키 단독 출시"), ("라면", "손정의 \"그때 투자했더라면\""),
                    ("르노", "아르노 디올 CEO 명예박사"), ("무협", "코트라, 에코비스와 업무협약"), ("효성", "수능 스티커 실효성 논란"),
                    ("항공사", "인천공항공사-이스타항공 멘토링"), ("항공사", "해군 항공사령부 부대 개방"), ("농심", "농협 '농심천심의 날'")]:
    check(not hit(term, title), f"{term} ≠ {title[:20]}")
check(hit("라면", "농심, 신라면 해물 출시") and hit("항공사", "인도 항공사들, 여객기 주문") and hit("가전", "LG전자 세탁가전 1천만대"), "진짜 라면·항공사·가전 기사는 그대로")
isb = ns["is_blocked"]
check(isb("[단독] 유아인, UAA 전속계약 종료..GD 소속사 갤럭시行") and isb("[방송소식] 정일우, 소속사 이적"), "연예 기사(소속사·전속계약·방송소식)는 뺌")
check(not isb("[단독] 정용진 회장 아내 콘서트, 글로벌 명사 총출동") and not isb("삼성전자 HBM4 양산"), "산업·재계 기사는 그대로")
sp = ns["is_sports"]
check(all(sp(t) for t in ["KT, 이강철 감독과 3년 재계약…총액 34억 원", "kt wiz 정규시즌 우승 시상식", "한화 이글스 지명된 민주홍", "젠지, 한화생명 꺾고 LCK 왕좌", "kt 고영표 10탈삼진 무실점"]), "프로야구·e스포츠 기사는 스포츠로 거름")
check(not any(sp(t) for t in ["블랙이글스 곡예비행에 방산 수출도", "포스코, 자동차용 도금강판 공장 신설", "노동부 근로감독 착수", "삼성·LG전자, IFA 혁신상"]), "산업 기사는 스포츠로 안 거름")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
