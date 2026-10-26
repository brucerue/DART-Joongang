import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, json, requests, re
os.environ["DART_API_KEY"] = "x"*40
os.environ["CUSTOMS_API_KEY"] = "dummy%2Bkey" if (len(sys.argv) < 2 or sys.argv[1] != "nokey") else ""
if not os.environ["CUSTOMS_API_KEY"]: os.environ.pop("CUSTOMS_API_KEY")
MODE = "mti"
import pandas as pd
TRADE = json.load(open(FIX + "/trade_full.json", encoding="utf-8"))
TRADE["hs"] = {"8542": "전자집적회로", "8541": "다이오드ㆍ트랜지스터 및 반도체 디바이스", "8703": "승용차", "8507": "축전지", "7208": "철강 평판압연제품", "2710": "석유제품"}
CSV = "corp_code,corp_name,stock_code\n" + "\n".join([f"0{i:07d},비상장사{i}," for i in range(52000)] + ["00126380,삼성전자,005930"]) + "\n"
calls = []
class R:
    status_code = 200; text = "{}"; content = b"{}"; ok = True
    def __init__(self, p=None, text=None): self._p = p if p is not None else {}; 
    def json(self): return self._p
    def raise_for_status(self): pass
def month_iter(s, e):
    y, m = int(s[:4]), int(s[4:]); out = []
    while f"{y}{m:02d}" <= e:
        out.append(f"{y}.{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out
sys.path.insert(0, REPO)
import trade_calc as _tc
_MTI = _tc.load_mti(REPO + "/mti_map.json")
_ALL = {}
for _t in _MTI["tables"]:
    for _h, _m in _t["hsk_to_mti"].items(): _ALL.setdefault(_h[:6], set()).add(_h)
def item_xml_mti(params):
    hs6, cn = params["hsSgn"], params.get("cntyCd")
    if len(hs6) == 4:  # HS4 조회 -> HS6 행 (자식 HS10 합)
        items = ["<item><year>총계</year><hsCd>-</hsCd><expDlr>1</expDlr><impDlr>1</impDlr></item>"]
        for ym in month_iter(params["strtYymm"], params["endYymm"]):
            for h6 in sorted(k for k in _ALL if k[:4] == hs6):
                for c, cname in ((("US", "미국"), ("CN", "중국")) if not cn else ((cn, {"US": "미국", "CN": "중국"}.get(cn, cn)),)):
                    n = len(_ALL[h6])
                    items.append(f"<item><year>{ym}</year><hsCd>{h6}</hsCd><statKor>품목 {h6}</statKor><statCd>{c}</statCd><statCdCntnKor1>{cname}</statCdCntnKor1><expDlr>{1000*n}</expDlr><impDlr>{400*n}</impDlr><expWgt>10</expWgt><impWgt>5</impWgt></item>")
        calls.append(dict(params))
        return "<response><header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header><body><items>" + "".join(items) + "</items></body></response>"
    items = ["<item><year>총계</year><hsCd>-</hsCd><expDlr>1</expDlr><impDlr>1</impDlr></item>"]
    for ym in month_iter(params["strtYymm"], params["endYymm"]):
        for h10 in sorted(_ALL.get(hs6, [])):
            for c, cname in ((("US", "미국"), ("CN", "중국")) if not cn else ((cn, {"US": "미국", "CN": "중국"}.get(cn, cn)),)):
                items.append(f"<item><year>{ym}</year><hsCd>{h10}</hsCd><statKor>품목 {h10}</statKor><statCd>{c}</statCd><statCdCntnKor1>{cname}</statCdCntnKor1>"
                             f"<expDlr>1000</expDlr><impDlr>400</impDlr><expWgt>10</expWgt><impWgt>5</impWgt></item>")
        # MTI에 안 묶인 같은 HS6의 다른 10자리 행(걸러져야 함)
        items.append(f"<item><year>{ym}</year><hsCd>{hs6}9999</hsCd><statKor>다른 MTI</statKor><statCd>US</statCd><statCdCntnKor1>미국</statCdCntnKor1><expDlr>77777</expDlr><impDlr>1</impDlr><expWgt>1</expWgt><impWgt>1</impWgt></item>")
    calls.append(dict(params))
    return "<response><header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header><body><items>" + "".join(items) + "</items></body></response>"
def item_xml(params):
    if len(params.get("hsSgn") or "") in (4, 6) and MODE == "mti": return item_xml_mti(params)
    hs, cn = params.get("hsSgn"), params.get("cntyCd")
    if MODE == "apierr": return "<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
    base = 3e9 if hs == "8542" else 1e9
    items = ["<item><year>총계</year><hsCd>-</hsCd><expDlr>1</expDlr><impDlr>1</impDlr></item>"]
    for ym in month_iter(params["strtYymm"], params["endYymm"]):
        k = int(ym[:4]) * 12 + int(ym[5:])
        for sub, nm in ((f"{hs}31", "프로세서"), (f"{hs}39", "기타")):
            for c, cname in ((("US", "미국"), ("CN", "중국")) if not cn else ((cn, {"US": "미국", "CN": "중국", "VN": "베트남"}.get(cn, cn)),)):
                items.append(f"<item><year>{ym}</year><hsCd>{sub}</hsCd><statKor>{nm}</statKor><statCd>{c}</statCd><statCdCntnKor1>{cname}</statCdCntnKor1>"
                             f"<expDlr>{int(base*(1+0.01*k)/4)}</expDlr><impDlr>{int(base*0.3/4)}</impDlr><expWgt>5000000</expWgt><impWgt>2000000</impWgt></item>")
    calls.append(dict(params))
    return "<response><header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header><body><items>" + "".join(items) + "</items></body></response>"
sys.path.insert(0, REPO)
import motie_flash as _mf
_fm = _mf.parse_flash(open(FIX + "/motie/sep.txt", encoding="utf-8").read())
_fm["month"] = "2026-10"; _fm["released"] = "2026-11-01"
_fm["monthly"] = {"2026-10": _fm["monthly"]["2026-09"], "2025-10": _fm["monthly"]["2025-09"]}
FLASH = {"updated": "x", "months": {"2026-10": _fm}}
FLASH_ON = [True]
def fake_get(url, *a, **k):
    if "/flash/flash.json" in url:
        if not FLASH_ON[0]: raise requests.RequestException("no flash")
        return R(FLASH)
    if "/trade/trade.json" in url: return R(TRADE)
    if "nitemtrade" in url:
        r = R(); r.text = item_xml(k.get("params", {})); return r
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode("utf-8"); return r
    return R()
requests.get = fake_get
class FakeDart:
    def __init__(self, key): self.corp_codes = pd.DataFrame({"corp_code": ["00126380"], "corp_name": ["삼성전자"], "stock_code": ["005930"]})
    def __getattr__(self, n): return lambda *a, **k: pd.DataFrame()
sys.modules["OpenDartReader"] = types.SimpleNamespace(OpenDartReader=FakeDart)
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
def exc(at): return [str(e.value)[:160] for e in at.exception]
def frames(at):
    out = []
    for d in at.dataframe:
        try: out.append(d.value)
        except Exception: pass
    return out

def tfr_p(at):
    return [f for f in frames(at) if "기간" in list(f.columns) and any(str(x) == "합계" for x in f["기간"])]
def tfr(at):
    """월별 추이 표(첫 열 '월'): 항목 이름 목록 = 값 열들"""
    return [f for f in frames(at) if "월" in list(f.columns) and any(str(x) == "합계" for x in f["월"])]
def names(f): return [c for c in f.columns if c != "월" and not c.endswith(" 증감")]




import trade_calc as tc
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.radio(key="tr_view").set_value("일반 검색").run()
def go(at): [b for b in at.button if b.key == "tr_s_go"][0].click().run()
def setm(at, ms): at.multiselect(key="tr_s_metrics_stored").set_value(ms).run()
setm(at, ["수출금액"])
check(at.radio(key="tr_s_period").options == ["올해", "1년", "3년", "직접 선택"], "기간 빠른 선택 4가지")
check(at.radio(key="tr_s_period").value == "올해" and not [s for s in at.selectbox if s.key in ("tr_s_start", "tr_s_end")], "기본 올해(시작·종료 칸은 숨김)")
M = TRADE["months"]
# 올해(잠정 포함) 월별
go(at); f = tfr(at)[0]
check(len(f) == 11 and list(f["월"])[-2] == "2026.10 (잠정)", f"올해 월별 {len(f)}줄(1~10월 + 합계)")
# 최근 1년
at.radio(key="tr_s_period").set_value("1년").run(); go(at); f = tfr(at)[0]
check(list(f["월"])[0] == "2025.11" and len(f) == 13, f"최근 1년(잠정 2026.10 포함, 12개월) {list(f['월'])[0]}")
# 직접 선택 -> 2026-09까지(잠정 제외), 연도별
at.radio(key="tr_s_period").set_value("직접 선택").run()
at.selectbox(key="tr_s_start").select("2024-10").run(); at.selectbox(key="tr_s_end").select("2026-09").run(); go(at)
check(at.radio(key="tr_s_unit").options == ["월별", "분기별", "연도별"], "집계 선택지")
at.radio(key="tr_s_unit").set_value("연도별").run()
f = tfr_p(at)[0]
check(list(f["기간"]) == ["2024년 (10~12월)", "2025년", "2026년 (1~9월 누계)", "합계"], f"연도별 기간 {list(f['기간'])}")
check(f.iloc[2]["전체 수출입 증감"] == "+56.8%", f"2026년 1~9월 누계 증감 = 산업부 발표 +56.8% ({f.iloc[2]['전체 수출입 증감']})")
exp26 = sum(TRADE["total"]["exp"][i] for i, m in enumerate(M) if m.startswith("2026"))
check(f.iloc[2]["전체 수출입"] == f"{exp26 / 1e8:,.1f}억달러", f"2026 누계 값 {f.iloc[2]['전체 수출입']}")
exp_q4_24 = sum(TRADE["total"]["exp"][i] for i, m in enumerate(M) if "2024-10" <= m <= "2024-12"); exp_q4_23 = sum(TRADE["total"]["exp"][i] for i, m in enumerate(M) if "2023-10" <= m <= "2023-12")
check(f.iloc[0]["전체 수출입 증감"] == f"{(exp_q4_24 / exp_q4_23 - 1) * 100:+.1f}%", f"2024년 10~12월은 2023년 10~12월과 비교 {f.iloc[0]['전체 수출입 증감']}")
check(at.radio(key="tr_s_base_연도별").options == ["전년 동기", "전년"], "연도별 증감 기준 이름")
# 분기별
at.radio(key="tr_s_unit").set_value("분기별").run()
f = tfr_p(at)[0]
check(list(f["기간"])[:2] == ["2024 Q4", "2025 Q1"] and list(f["기간"])[-2] == "2026 Q3", f"분기 기간 {list(f['기간'])}")
q3_26 = sum(TRADE["total"]["exp"][i] for i, m in enumerate(M) if "2026-07" <= m <= "2026-09"); q3_25 = sum(TRADE["total"]["exp"][i] for i, m in enumerate(M) if "2025-07" <= m <= "2025-09")
check(f.iloc[-2]["전체 수출입"] == f"{q3_26 / 1e8:,.1f}억달러" and f.iloc[-2]["전체 수출입 증감"] == f"{(q3_26 / q3_25 - 1) * 100:+.1f}%", f"2026 Q3 증감 {f.iloc[-2]['전체 수출입 증감']}")
# 직전 기간 기준(분기)
at.radio(key="tr_s_base_분기별").set_value("전분기").run(); f = tfr_p(at)[0]
q2_26 = sum(TRADE["total"]["exp"][i] for i, m in enumerate(M) if "2026-04" <= m <= "2026-06")
check(f.iloc[-2]["전체 수출입 증감"] == f"{(q3_26 / q2_26 - 1) * 100:+.1f}%", f"전분기 대비 {f.iloc[-2]['전체 수출입 증감']}")
check(not exc(at), f"오류 없음 {exc(at)}")
# 지역 곱(국가) + 연도별
at.radio(key="tr_s_unit").set_value("월별").run()
at.radio(key="tr_s_reg").set_value("국가").run(); at.multiselect(key="tr_s_cnty").set_value(["미국"]).run(); go(at)
at.radio(key="tr_s_unit").set_value("연도별").run(); f = tfr_p(at)[0]
us26 = sum(TRADE["country"]["US"][1][i] for i, m in enumerate(M) if m.startswith("2026"))
check(not exc(at) and f.iloc[2]["미국"] == f"{us26 / 1e8:,.1f}억달러", f"미국 연도별 {f.iloc[2]['미국']}")
print("실패", fails); sys.exit(1 if fails else 0)
