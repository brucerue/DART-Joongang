import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, datetime as dt
os.environ["DART_API_KEY"] = "x"*40
sys.path.insert(0, REPO)
import requests
def fake_get(url, *a, **k):
    r = requests.models.Response(); r.status_code = 200
    r._content = b"corp_code,corp_name,stock_code\n" if "corp_codes" in url else b"{}"
    return r
requests.get = fake_get
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
at = AppTest.from_file(REPO + "/app.py", default_timeout=120); at.run()
check(not at.exception, "첫 화면")
def out():
    return " ".join(m.value for m in at.markdown if "calc-out" in m.value)
at.text_input(key="calc_a").input("1조2,345억").run(); at.text_input(key="calc_b").input("1조5,000억").run()
o = out(); check("+21.5%" in o and "증가" in o and "2655억" in o, "증감: " + o[-200:])
at.text_input(key="calc_a").input("-500").run(); at.text_input(key="calc_b").input("300").run()
check("흑자전환" in out(), "흑자전환")
at.text_input(key="calc_a").input("8.1%").run(); at.text_input(key="calc_b").input("9.4%").run()
check("+1.3%p" in out(), "%p")
at.text_area(key="calc_tc").input("반도체 1200 1500\n자동차 600 620\n석유제품 400 350").run()
check(any("기여도" in d.value.columns and list(d.value["기여도"])[0]=="111.1%" and list(d.value["판정"])[-1]=="증가" for d in at.dataframe), "표 증감")
at.text_input(key="calc_unit_v").input("1,234,567").run()
check("1조2346억원" in out() and "1234567000000" in out() and "," not in out().split("calc-out")[-1], "단위 기사체")
at.text_input(key="calc_en_ko").input("3조5000억").run()
check("3.5 trillion" in out(), "한글→영어")
at.text_input(key="calc_en_en").input("$3.5bn").run()
check("35억" in out(), "영어→한글")
at.text_input(key="calc_g_a").input("100").run(); at.text_input(key="calc_g_b").input("200").run()
check("+14.9%" in out(), "연평균")
at.text_area(key="calc_share").input("반도체 1,200\n자동차 600\n기타 200").run()
check(any("비중" in d.value.columns and "60.0%" in list(d.value["비중"]) for d in at.dataframe), "비중")
at.text_area(key="calc_text").input("가나다라마. 바사아자차카다.\n\n둘째 문단입니다.").run()
check("공백 포함" in out(), "글자 수")
at.number_input(key="calc_dn").set_value(3).run()
check("영업일 뒤" in out(), "날짜")
check(not at.exception, f"오류 없음 {at.exception}")
print("실패", fails)
# 지우기
at.text_input(key="calc_a").input("100").run(); at.text_input(key="calc_b").input("200").run()
at.button(key="calc_clr0").click().run()
check(at.text_input(key="calc_a").value == "" and at.text_input(key="calc_b").value == "" and not at.exception, "증감 지우기")
check(at.text_area(key="calc_share").value != "", "다른 칸은 그대로")
at.button(key="calc_clr5").click().run()
check(at.text_area(key="calc_share").value == "" and not at.exception, "비중 지우기")
at.button(key="calc_clr7").click().run()
check(not at.exception and at.number_input(key="calc_dn").value == 0, f"날짜 지우기 {at.exception}")
print("실패", fails)
