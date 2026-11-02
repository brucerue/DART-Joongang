"""현대차·기아 월별 판매실적('영업(잠정)실적' 공시, 매출 칸은 비고 판매대수 표만 있음)을 읽어 알람에 붙이는지 (2026.11.02 실제 공시 원문, 실서버 접속 없음)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")
import os, sys, html
sys.path.insert(0, REPO)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake")
os.environ["DART_API_KEY"] = "fake"
from datetime import datetime
import news_digest as nd
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
kia = open(FIX + "/sales_kia.txt", encoding="utf-8").read()
hy = open(FIX + "/sales_hyundai.txt", encoding="utf-8").read()
check(nd.parse_earnings(kia) is None, "매출·영업이익 칸은 비어 있음 (예전엔 '수치를 읽지 못했어요')")
d = html.unescape(nd.sales_detail(nd.parse_sales(kia)))
print(d)
check("10월 판매 291,219대 (전년比 +10.3% · 전월比 +3.1%)" in d, "기아: 합계·전년比·전월比")
check("국내 51,007대(+27.5%) · 해외 239,308대(+7.2%) · 특수 904대(+12.9%)" in d, "기아: 내수·해외·특수")
check("1~10월 누적 2,769,954대 (전년比 +5.1%)" in d, "기아: 누적")
d = html.unescape(nd.sales_detail(nd.parse_sales(hy)))
print(d)
check("10월 판매 329,773대 (전년比 -6.3% · 전월比 +6.4%)" in d, "현대차: 부호 없는 원문 증감률에 + 붙임")
check("국내 49,042대(-8.9%) · 해외 280,731대(-5.9%)" in d and "특수" not in d, "현대차: 국내·해외")
check("1~10월 누적 3,218,586대 (전년比 -6.9%)" in d, "현대차: 누적")
# 알람 흐름: 원문을 받아 제목·수치 줄을 붙임
nd.fetch_doc_text = lambda key, no: {"1": kia, "2": hy}[no]
items = [{"key": "1", "title": "[기아] 영업(잠정)실적(공정공시)", "corp": "기아", "earn": "별도", "rcept_dt": "20261102", "url": "u1", "publisher": "DART"},
         {"key": "2", "title": "[현대자동차] 영업(잠정)실적(공정공시)", "corp": "현대자동차", "earn": "별도", "rcept_dt": "20261102", "url": "u2", "publisher": "DART"}]
nd.enrich_earnings(items, {}, datetime(2026, 11, 2, 15, 30, tzinfo=nd.KST))
check(items[0]["title"] == "[기아] 2026년 10월 판매실적", f"제목 {items[0]['title']}")
check("291,219대" in items[0]["detail"] and "329,773대" in items[1]["detail"], "알람 줄에 판매대수")
msg = nd.build_messages("공시", datetime(2026, 11, 2, 15, 30), [("자동차", [{"rep": i, "others": []} for i in items], 0)], earn_only=True)[0]
check("수치를 읽지 못했어요" not in msg and "2026년 10월 판매실적" in msg, "메시지에 '수치를 읽지 못했어요' 대신 판매실적")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
