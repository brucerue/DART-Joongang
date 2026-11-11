"""brief_data.py 가짜 자료 시험 (DART·GitHub 접속 없음): 기간(월요일 금 15:00~), 기사 묶음, 섹터 공시, 특징주, IR 일정(NDR 제외), 제출기한, 오늘의 공시."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.getcwd())
import brief_data as B  # noqa: E402
import news_digest as nd  # noqa: E402

KST = timezone(timedelta(hours=9))
fails = []


def check(c, m):
    if not c:
        fails.append(m)


# 기간: 화요일 06:30이면 24시간 전, 월요일이면 금요일 15:00부터
check(B.window_since(datetime(2026, 11, 10, 6, 30, tzinfo=KST)) == datetime(2026, 11, 9, 6, 30, tzinfo=KST), "화요일 24시간")
check(B.window_since(datetime(2026, 11, 9, 6, 30, tzinfo=KST)) == datetime(2026, 11, 6, 15, 0, tzinfo=KST), "월요일 금 15시부터")

now = datetime(2026, 11, 11, 6, 30, 17, 5000, tzinfo=KST)  # 초가 붙은 실제 시각처럼
recent = {"updated": "2026-11-11T06:01", "items": [
    {"t": "[단독] 삼성전자, HBM4 공급 확대", "u": "u1", "p": "한국경제", "k": "단독", "d": "2026-11-10T21:00", "s": ["반도체·전자"], "g": ["삼성전자"]},
    {"t": "[단독] 삼성전자, HBM4 공급 확대한다", "u": "u2", "p": "매일경제", "k": "단독", "d": "2026-11-10T22:00", "s": ["반도체·전자"], "g": ["삼성전자"]},
    {"t": "SK하이닉스 신고가", "u": "u3", "p": "연합뉴스", "k": "통신", "d": "2026-11-11T05:00", "s": ["반도체·전자"], "g": ["SK하이닉스"]},
    {"t": "옛 기사", "u": "u4", "p": "연합뉴스", "k": "통신", "d": "2026-11-09T05:00", "s": ["반도체·전자"], "g": []},
    {"t": "[삼성전자] 기업설명회(IR)개최(안내공시)", "u": "u5", "p": "DART", "k": "공시", "d": "2026-11-10T00:00", "s": ["반도체·전자"], "g": ["삼성전자"]},
]}
prices = {"updated": "2026-11-10T09:40", "dates": ["20261107", "20261110"], "s": {
    "005930": {"n": "삼성전자", "m": "코스피", "c": [100, 110], "r": [0, 10.0], "v": [1, 1], "k": [1, 1]},
    "000660": {"n": "SK하이닉스", "m": "코스피", "c": [100, 95], "r": [0, -5.0], "v": [1, 1], "k": [1, 1]}}, "g": {}}
corp_rows = [{"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930"},
             {"corp_code": "00164779", "corp_name": "SK하이닉스", "stock_code": "000660"}]


class FakeDart:
    def fiscal_month(self, corp):
        return 12

    def list_rows(self, corp, start, end, ty="I"):
        if corp == "00126380":
            return [{"rcept_no": "20261106000001", "rcept_dt": "20261106", "report_nm": "기업설명회(IR)개최(안내공시)"},
                    {"rcept_no": "20261105000001", "rcept_dt": "20261105", "report_nm": "기업설명회(IR)개최(안내공시)"},
                    {"rcept_no": "20261104000001", "rcept_dt": "20261104", "report_nm": "기업설명회(IR)개최(안내공시)"}]
        return []

    def doc_text(self, no):
        if no in ("20261105000001", "20261106000001"):
            return "1. 일시 2026-11-13\n2. 목적 | 2026년 3분기 실적 발표\n개최방법 | 컨퍼런스콜"
        return "1. 일시 2026-11-12\n2. 목적 | NDR\n개최방법 | 1:1 미팅"


feed = {"20261110": [{"rcept_no": "20261110000010", "corp_name": "현대차", "stock_code": "005380", "report_nm": "단일판매ㆍ공급계약체결"},
                     {"rcept_no": "20261110000011", "corp_name": "현대차", "stock_code": "005380", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서"},
                     {"rcept_no": "20261110000012", "corp_name": "기아", "stock_code": "000270", "report_nm": "현금ㆍ현물배당결정"}],
        "20261111": [{"rcept_no": "20261111000001", "corp_name": "LG전자", "stock_code": "066570", "report_nm": "회사합병결정"},
                     {"rcept_no": "20261111000002", "corp_name": "LG전자", "stock_code": "066570", "report_nm": "기타 안내"}]}
nd.fetch_dart_day = lambda key, ymd, cls, ty="": feed.get(ymd, []) if cls == "Y" else []

res = B.build(recent, prices, None, corp_rows, "KEY", now, dart=FakeDart())
sec = res["sectors"]["반도체·전자"]
check(res["since"] == "2026-11-10T06:30", f"기간 {res['since']}")
check(len(sec["news"]["단독"]) == 1 and len(sec["news"]["단독"][0]["others"]) == 1, f"단독 묶음 {sec['news']['단독']}")
check([c["rep"]["u"] for c in sec["news"]["통신"]] == ["u3"], f"통신(옛 기사 제외) {sec['news']['통신']}")
check([d["u"] for d in sec["disclosures"]] == ["u5"], f"섹터 공시(어제 날짜도) {sec['disclosures']}")
mv = sec["movers"]
check(mv and mv["when"] == "11.10 종가", f"특징주 기준 {mv}")
flat = {r["name"]: r["rate"] for g in mv["groups"] for r in g["up"] + g["down"]}
check(flat == {"삼성전자": 10.0, "SK하이닉스": -5.0}, f"특징주 {flat}")
check([(e["corp"], e["date"]) for e in sec["ir"]] == [("삼성전자", "2026-11-13")], f"IR(NDR 제외) {sec['ir']}")
check(sec["ir"] and "3분기" in sec["ir"][0]["purpose"], f"IR 목적 {sec['ir']}")
check(all(d["date"] >= "2026-11-11" for d in sec["deadlines"]) and any(d["report"] == "3분기보고서" and d["date"] == "2026-11-14" for d in sec["deadlines"]),
      f"제출기한(9월 결산 후 45일) {sec['deadlines']}")
m = {(x["corp"], x["group"]) for x in res["market_disclosures"]}
check(m == {("현대차", "공급계약"), ("기아", "배당"), ("LG전자", "합병·분할")}, f"오늘의 공시(소유상황보고서·기타 제외) {m}")
check(next(x for x in res["market_disclosures"] if x["corp"] == "기아")["view"] == "지배구조", "보기 묶음")
check(next(x for x in res["market_disclosures"] if x["corp"] == "현대차")["alarm"] is True, "알람 범위 표시")

# DART 키가 없으면 그 칸은 비우고 notes에 적음
res2 = B.build(recent, prices, None, corp_rows, "", now)
check(res2["market_disclosures"] == [] and res2["notes"], "키 없음")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
