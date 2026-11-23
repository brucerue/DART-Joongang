"""직원 현황 계산(정기보고서 > 임직원): '성별합계' 줄을 두 번 세지 않고, 1인 평균 급여는 보고서 값의 직원 수 가중 평균
(2026.11.23, 사업보고서 분석기에서 실제 DART empSttus 응답으로 찾은 버그. 아래 자료는 그 응답 그대로)"""
import ast, pathlib, re, sys
REPO = pathlib.Path(__file__).resolve().parents[1]
src = (REPO / "app.py").read_text(encoding="utf-8")
ns = {"re": re}
for node in ast.parse(src).body:  # 앱 전체를 실행하지 않고 필요한 함수만
    if isinstance(node, ast.FunctionDef) and node.name in ("is_total", "parse_years", "to_int", "dart_rows", "_weighted_by_staff", "employee_stats", "employee_summary"):
        exec(compile(ast.Module([node], []), "app.py", "exec"), ns)
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
es = ns["employee_stats"]
sam = {"list": [  # 삼성전자 2025 사업보고서 ('성별합계' 줄이 섞임)
    {"fo_bbm": "DX", "sexdstn": "남", "sm": "38,119", "avrg_cnwk_sdytrn": "      17.4", "fyer_salary_totamt": "-", "jan_salary_am": "-"},
    {"fo_bbm": "DX", "sexdstn": "여", "sm": "12,698", "avrg_cnwk_sdytrn": "      14.4", "fyer_salary_totamt": "-", "jan_salary_am": "-"},
    {"fo_bbm": "DS", "sexdstn": "남", "sm": "56,154", "avrg_cnwk_sdytrn": "      11.7", "fyer_salary_totamt": "-", "jan_salary_am": "-"},
    {"fo_bbm": "DS", "sexdstn": "여", "sm": "21,910", "avrg_cnwk_sdytrn": "      12.1", "fyer_salary_totamt": "-", "jan_salary_am": "-"},
    {"fo_bbm": "성별합계", "sexdstn": "남", "sm": "94,273", "avrg_cnwk_sdytrn": "      14.0", "fyer_salary_totamt": "15,562,454,000,000", "jan_salary_am": "167,000,000"},
    {"fo_bbm": "성별합계", "sexdstn": "여", "sm": "34,608", "avrg_cnwk_sdytrn": "      13.0", "fyer_salary_totamt": "4,237,308,000,000", "jan_salary_am": "130,000,000"}]}
e = es(sam)
check(e["count"] == 128881, f"삼성전자 직원 수 12만8881명(두 배 아님) {e['count']}")
check(abs(e["avg"] - (167e6 * 94273 + 130e6 * 34608) / 128881) < 1, f"삼성전자 1인 평균 급여 = 성별합계 줄 1인 평균 급여액 가중 평균 {e['avg']:.0f}")
check(round(e["female"], 1) == round(34608 / 128881 * 100, 1), "여성 비중은 세부 줄 기준")
hy = {"list": [  # SK하이닉스 2025
    {"fo_bbm": "반도체", "sexdstn": "남", "sm": "23,037", "avrg_cnwk_sdytrn": "12.3", "fyer_salary_totamt": "4,376,624,000,000", "jan_salary_am": "198,000,000"},
    {"fo_bbm": "반도체", "sexdstn": "여", "sm": "11,512", "avrg_cnwk_sdytrn": "15.7", "fyer_salary_totamt": "1,771,407,000,000", "jan_salary_am": "159,000,000"}]}
h = es(hy)
check(h["count"] == 34549 and round(h["avg"] / 1e6) == 185, f"SK하이닉스 1억8500만원(기사와 같음) {h['avg']:.0f}")
old = {"list": [{"fo_bbm": "DX", "sexdstn": "남", "sm": "1,000", "fyer_salary_totamt": "158,000,000,000"},
                {"fo_bbm": "DX", "sexdstn": "여", "sm": "1,000", "fyer_salary_totamt": "158,000,000,000"},
                {"fo_bbm": "합계", "sexdstn": "", "sm": "2,000", "fyer_salary_totamt": "316,000,000,000"}]}
o = es(old)
check(o["count"] == 2000 and o["avg"] == 158_000_000 and o["avg_how"] == "급여 총액 ÷ 직원 수", "1인 평균 급여액이 없으면 예전처럼 급여 총액 ÷ 직원 수")
check(es({"list": [{"fo_bbm": "합 계", "sexdstn": "", "sm": "500", "jan_salary_am": "90,000,000"}]})["avg"] == 90_000_000, "전체 합계 한 줄만 있으면 그 줄의 값")
check([ns["is_total"](x) for x in ("성별합계", "합 계", "총계", "계", "전체", "DX", "남")] == [True] * 5 + [False] * 2, "합계 칸 판정")
check(ns["employee_summary"](sam) == (128881, e["avg"]), "employee_summary도 같은 값")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
