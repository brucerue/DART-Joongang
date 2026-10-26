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

def tfr(at):
    """월별 추이 표(첫 열 '월'): 항목 이름 목록 = 값 열들"""
    return [f for f in frames(at) if "월" in list(f.columns) and any(str(x) == "합계" for x in f["월"])]
def names(f): return [c for c in f.columns if c != "월" and not c.endswith(" 증감")]



import trade_calc as tc
def rfr(at): return [f for f in frames(at) if "순위" in list(f.columns) and "점유율" in list(f.columns)]
def go_r(at): [b for b in at.button if b.key == "tr_r_go"][0].click().run()
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not exc(at), f"기본 화면 오류 없음 {exc(at)}")
at.radio(key="tr_view").set_value("일반 검색").run()
check(at.radio(key="tr_s_kind").options == ["일반", "국가별", "품목별"], f"종류 {at.radio(key='tr_s_kind').options}")
at.radio(key="tr_s_kind").set_value("국가별").run()
check(not exc(at), f"순위 화면 오류 없음 {exc(at)}")
check(at.radio(key="tr_r_item").value == "전체", "품목 기본 전체")
# 지표 복수 선택: 기본 3개
check(at.multiselect(key="tr_r_metrics_stored").value == ["수출금액", "수입금액", "무역수지"], f"지표 기본 3개 {at.multiselect(key='tr_r_metrics_stored').value}")
go_r(at); tm = rfr(at)[0]
check(all(c in tm.columns for c in ("수출", "수출 증감", "수입", "수입 증감", "무역수지", "무역수지 증감", "점유율")) and tm.iloc[0]["국가"] == "중국", f"기본 3지표 열 {list(tm.columns)}")
check(tm.iloc[-1]["국가"] == "합계" and str(tm.iloc[-2]["국가"]).startswith("기타"), "다지표도 기타·합계 줄")
at.multiselect(key="tr_r_metrics_stored").set_value([]).run(); go_r(at)
check(any("지표를 하나 이상" in w.value for w in at.warning), "지표 없으면 경고")
at.multiselect(key="tr_r_metrics_stored").set_value(["수출금액"]).run()
n0 = len(calls); go_r(at)
f = rfr(at)
check(not exc(at) and len(f) == 1 and len(calls) == n0, f"전체 품목 국가별 순위(호출 없음) {exc(at)}")
t = f[0]
check(list(t.columns) == ["순위", "국가", "수출", "점유율", "전년 같은 기간", "증감"], f"열 {list(t.columns)}")
check(t.iloc[0]["국가"] == "중국" and str(t.iloc[0]["순위"]) == "1" and t.iloc[1]["국가"] == "미국", f"1위 중국 2위 미국 {list(t['국가'])[:3]}")
check(str(t.iloc[-2]["국가"]).startswith("기타") and t.iloc[-1]["국가"] == "합계", f"기타·합계 줄 {list(t['국가'])[-3:]}")
# 값 검산: 2026-01~09 중국 수출 합
M = TRADE["months"]; cn = TRADE["country"]["CN"][1]
exp_cn = sum(cn[i] for i, m in enumerate(M) if "2026-01" <= m <= "2026-09")
check(t.iloc[0]["수출"] == f"{exp_cn / 1e8:,.1f}억달러", f"중국 합계 {t.iloc[0]['수출']} vs {exp_cn/1e8:,.1f}")
shares = [float(x.rstrip("%")) for x in t.iloc[:-1]["점유율"]]
check(abs(sum(shares) - 100) < 0.2, f"점유율 합 100% ({sum(shares):.1f})")
check(t.iloc[0]["증감"].startswith("+"), f"전년 같은 기간 증감 {t.iloc[0]['증감']}")
# 상위 5, 전체
at.selectbox(key="tr_r_top").select("5").run(); go_r(at); t = rfr(at)[0]
check(len(t) == 7, f"상위 5 + 기타 + 합계 = 7줄 ({len(t)})")
at.selectbox(key="tr_r_top").select("전체").run(); go_r(at); t = rfr(at)[0]
check(len(t) > 100 and not any(str(x).startswith("기타 (") for x in t["국가"]), f"전체는 기타 없이 {len(t)}줄")
at.selectbox(key="tr_r_top").select("10").run()
# 수입 / 무역수지
at.multiselect(key="tr_r_metrics_stored").set_value(["무역수지"]).run(); go_r(at); t = rfr(at)[0]
check(t.iloc[0]["점유율"] == "-" and t.iloc[0]["증감"].endswith("억달러"), f"무역수지는 점유율 없음·증감액 {t.iloc[0].to_dict()}")
at.multiselect(key="tr_r_metrics_stored").set_value(["수입금액"]).run(); go_r(at); t = rfr(at)[0]
check(t.iloc[0]["국가"] == "중국" and "수입" in t.columns, "수입금액 순위")
at.multiselect(key="tr_r_metrics_stored").set_value(["수출금액"]).run()
# 경제권·대륙
at.radio(key="tr_r_level").set_value("경제권").run(); go_r(at); t = rfr(at)[0]
check("경제권" in t.columns and "APEC" in list(t["경제권"]) and not any(str(x).startswith("기타 (") for x in t["경제권"]), f"경제권 순위 {list(t['경제권'])[:4]}")
at.radio(key="tr_r_level").set_value("대륙").run(); go_r(at); t = rfr(at)[0]
check(t.iloc[0]["대륙"] == "아시아" and "북미" in list(t["대륙"]), f"대륙 순위 {list(t['대륙'])[:3]}")
at.radio(key="tr_r_level").set_value("국가").run()
# 전년 비교 끄기
at.toggle(key="tr_r_compare").set_value(False).run(); go_r(at); t = rfr(at)[0]
check(list(t.columns) == ["순위", "국가", "수출", "점유율"], f"비교 끄면 열 {list(t.columns)}")
at.toggle(key="tr_r_compare").set_value(True).run()
# 기간 거꾸로 경고
at.radio(key="tr_r_period").set_value("직접 선택").run(); at.selectbox(key="tr_r_start").select("2026-09").run(); at.selectbox(key="tr_r_end").select("2026-01").run(); go_r(at)
check(any("시작월이 종료월보다" in w.value for w in at.warning), "기간 거꾸로 경고")
at.selectbox(key="tr_r_start").select("2026-01").run(); at.selectbox(key="tr_r_end").select("2026-09").run()
# 20대 품목(반도체) 국가별 순위: 품목 조회 행에서
at.radio(key="tr_r_item").set_value("20대").run()
at.button_group(key="tr_r_groups").set_value(["반도체"]).run(); n1 = len(calls); go_r(at)
f = rfr(at)
check(not exc(at) and len(f) == 1 and len(calls) > n1 and all("cntyCd" not in c for c in calls[n1:]), f"20대 반도체 국가별 순위 {exc(at)} 호출 {len(calls)-n1}")
t = f[0]
check(set(t["국가"]) - {"합계"} <= {"미국", "중국"} and str(t.iloc[0]["순위"]) == "1", f"품목 순위는 받은 행에서 {list(t['국가'])}")
# 경제권 순위(반도체): 소속 국가 합산
at.radio(key="tr_r_level").set_value("경제권").run(); n2 = len(calls); go_r(at); t = rfr(at)[0]
check(len(calls) == n2 and "NAFTA" in list(t["경제권"]) and "APEC" in list(t["경제권"]), f"품목의 경제권 순위(추가 호출 없음) {list(t['경제권'])}")
at.radio(key="tr_r_level").set_value("국가").run()
# 품목(반도체) 다지표 + 중량 + 경제권
at.button_group(key="tr_r_groups").set_value(["반도체"]).run()
at.multiselect(key="tr_r_metrics_live").set_value(["수출금액", "수입금액", "무역수지", "수출 중량"]).run(); go_r(at); t = rfr(at)[0]
check(not exc(at) and all(c in t.columns for c in ("수출", "수입", "무역수지", "수출 중량", "수출 중량 증감")), f"품목 다지표(중량 포함) {exc(at)} {list(t.columns)}")
check(str(t.iloc[0]["수출 중량"]).endswith("톤"), f"중량 톤 {t.iloc[0]['수출 중량']}")
at.radio(key="tr_r_level").set_value("경제권").run(); go_r(at); t = rfr(at)[0]
check(not exc(at) and t.iloc[-1]["경제권"] == "(전체)" and "수출" in t.columns, f"경제권 다지표 합계 줄 {exc(at)} {t.iloc[-1].to_dict()}")
at.radio(key="tr_r_level").set_value("국가").run()
at.multiselect(key="tr_r_metrics_live").set_value(["수출금액"]).run()
# 품목 여러 개 -> 탭
at.button_group(key="tr_r_groups").set_value(["반도체", "일반기계"]).run(); go_r(at)
check(not exc(at) and len(rfr(at)) == 2 and len(at.tabs) >= 2, f"품목 2개는 탭 2개, 표 2개 ({len(rfr(at))}, {len(at.tabs)})")
# HS 코드: 품목 안 고르면 경고
at.radio(key="tr_r_item").set_value("HS코드").run(); go_r(at)
check(any("품목을 하나 이상" in w.value for w in at.warning), "HS 품목 안 고르면 경고")
at.multiselect(key="tr_r_hs").set_value(["8542 전자집적회로"]).run(); go_r(at)
check(not exc(at) and len(rfr(at)) == 1, f"HS 4자리 국가별 순위 {exc(at)}")
print("실패", fails); sys.exit(1 if fails else 0)
