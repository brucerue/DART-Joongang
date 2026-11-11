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
for t in ["Giants focused on pitching and defense as GM meetings begin", "Former Angels GM Perry Minasian to rejoin Braves front office: Source",
          "Eight changes Blackhawks GM Kyle Davidson should consider to salvage this season", "Check out General Motors's stock price (GM) in real time"]:
    check(not keep(t, gm), f"실제로 섞였던 것 뺌: {t[:40]}")
check(keep("New York sues GM in case related to deceptive trade practices", gm), "GM 소송 기사는 남김")
check(gc.is_junk("ProShares Ultra SK hynix (SKHU.N) | Stock Price & Latest News"), "ETF 주가 페이지 뺌")
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
# 한국 관련: 섹터 낱말을 3개씩 나눠 검색, 북한 제외는 검색식 대신 제목으로 (검색식이 길면 구글이 언론사 제한을 무시)
cores = gc.korea_cores("반도체·전자")
check(len(cores) == 3 and all("North Korea" not in c for c in cores), f"반도체·전자 검색식 3개, 북한 제외 없음: {cores}")
check(all(c.count(" OR ") <= 3 + 2 for c in cores) and "chip" in cores[0] and "appliance" in cores[-1], "낱말이 나뉘어 모두 들어감")
check(gc.korea_noise("North Korea fires missile as Seoul steps up defense") and gc.korea_noise("Pyongyang warns Seoul"), "제목에 북한이면 뺌")
check(not gc.korea_noise("South Korea's Hanwha wins Polish defense deal"), "한국 방산 기사는 남김")
check(gc.korea_core("항공") == gc.korea_cores("항공")[0], "예전 이름 호환")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
