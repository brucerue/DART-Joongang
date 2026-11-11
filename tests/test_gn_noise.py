"""외신 잡음 거르기: GM 야구 단장 기사·주가/프로필 페이지·스포츠 빼기, 한국 기업 섹터"""
import os
import sys

sys.path.insert(0, os.getcwd())
import gnews_calc as gc  # noqa: E402

fails = []


def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        fails.append(m)


gm = gc.aliases("General Motors")
keep = lambda t, names: gc.title_has(t, names) and not gc.is_junk(t)
check(not keep("Detroit Tigers GM says trade talks stalled", gm), "GM 야구 단장(관련 낱말 없음) 뺌")
check(not keep("Yankees GM Cashman on free agency", gm), "야구 기사 뺌")
check(keep("GM to cut EV output at Michigan plant", gm), "GM 자동차 기사는 남김")
check(keep("General Motors names new CFO", gm), "General Motors 전체 이름은 그대로")
for t in ["General Motors Co (GM) Stock Price & News - WSJ", "Mary Barra | Executive Profile", "Tesla Inc (TSLA) Stock Quote", "Hyundai Motor Co Company Profile"]:
    check(gc.is_junk(t), f"주가·프로필 페이지 뺌: {t}")
for t in ["Nvidia stock price falls after earnings", "Tech giants pour money into AI", "Samsung profile rises in Washington"]:
    check(not gc.is_junk(t), f"기사는 남김: {t}")
check(not keep("Meta unveils metaverse headset", ["Hyundai Motor"]), "다른 기업 제목은 안 걸림")
g = gc.global_sectors({"반도체": ["NVDA"]}, {"NVDA": "Nvidia"}, {})
check(g.get(gc.KOREA_GROUP) == gc.KOREAN_COMPANIES and "HD Hyundai" in gc.KOREAN_COMPANIES and "Hanwha Ocean" in gc.KOREAN_COMPANIES, "한국 기업 섹터")
check(keep("HD Korea Shipbuilding wins LNG carrier order", gc.aliases("HD Hyundai")), "HD현대 별칭")
check(gc.file_name("global", gc.KOREA_GROUP).startswith("global/"), "한국 기업 파일 이름")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
