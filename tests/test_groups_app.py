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
GROUPS = None
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
    if "/groups/groups.json" in url: return R(GROUPS)
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
M = TRADE["months"]
def mk(rows):
    return {"CN": [[1000 * (k + 1) for k in range(len(M))], [400 * (k + 1) for k in range(len(M))]],
            "US": [[500 * (k + 1) for k in range(len(M))], [100 * (k + 1) for k in range(len(M))]]}
GROUPS = {"updated": "2026-10-24T15:44", "months": M, "unit": "천달러", "groups": ["반도체", "자동차"], "defs": "x", "done_to": {}, "complete": True,
          "data": {"반도체": mk(0), "자동차": {"CN": [[7] * len(M), [3] * len(M)]}}}
def go(at): [b for b in at.button if b.key == "tr_s_go"][0].click().run()
def go_r(at): [b for b in at.button if b.key == "tr_r_go"][0].click().run()
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.radio(key="tr_view").set_value("일반 검색").run()
at.radio(key="tr_s_item").set_value("20대").run()
at.button_group(key="tr_s_groups").set_value(["반도체"]).run()
n0 = len(calls); go(at)
print("   쿼리", {k: v for k, v in at.session_state["tr_s_query"].items() if k in ("items","regions","metrics")} if "tr_s_query" in at.session_state else None, [w.value for w in at.warning][:2])
check(not exc(at), f"예외 없음 {exc(at)}")
check(len(calls) == n0, f"20대 품목 일반 검색은 관세청 호출 없음 ({len(calls) - n0}번)")
fr = [f for f in frames(at) if "월" in list(f.columns) and any(str(x) == "합계" for x in f["월"])]
check(len(fr) == 1, f"월별 표 {len(fr)}개")
f = fr[0]
cols = [c for c in f.columns if c != "월" and not str(c).endswith("증감")]
print("   열", list(f.columns))
# 올해 1~9월 반도체 수출 = (CN 1000 + US 500) * sum(k+1) 천달러 -> 달러
idx = [i for i, m in enumerate(M) if "2026-01" <= m <= "2026-09"]
exp_total = sum((1000 + 500) * (i + 1) * 1000 for i in idx)
last = f.iloc[-1]
want = exp_total / 1e8 + 603.0  # 확정(모아 둔 자료) + 10월 산업부 잠정치(반도체 603.0억달러)
check(abs(float(str(last["수출"]).replace("억달러", "").replace(",", "")) - want) < 0.2, f"합계 수출 {want:.1f}억달러(자료 {exp_total / 1e8:.2f} + 잠정 603.0) vs {last['수출']}")
# 지역 국가 CN
at.radio(key="tr_s_reg").set_value("국가").run()
at.multiselect(key="tr_s_cnty").set_value(["중국"]).run(); n1 = len(calls); go(at)
check(not exc(at) and len(calls) == n1, f"반도체 × 중국도 호출 없음 {exc(at)} ({len(calls) - n1})")
f = [f for f in frames(at) if "월" in list(f.columns) and any(str(x) == "합계" for x in f["월"])][0]
check(abs(float(str(f.iloc[-1]["수출"]).replace("억달러", "").replace(",", "")) - (sum(1000 * (i + 1) * 1000 for i in idx) / 1e8)) < 0.2, f"중국만: {f.iloc[-1]['수출']}")
# 중량 지표를 고르면 관세청 호출로
at.radio(key="tr_s_reg").set_value("전체").run()
ms = [w for w in at.multiselect if str(w.key).startswith("tr_s_metrics")][0]
ms.set_value(["수출금액", "수출 중량"]).run(); n2 = len(calls); go(at)
check(not exc(at) and len(calls) > n2, f"중량 지표는 관세청 조회로 ({len(calls) - n2}번)")
# 국가별 순위
at.radio(key="tr_s_kind").set_value("국가별").run()
at.radio(key="tr_r_item").set_value("20대").run()
at.button_group(key="tr_r_groups").set_value(["반도체"]).run(); n3 = len(calls); go_r(at)
check(not exc(at) and len(calls) == n3, f"20대 국가별 순위 호출 없음 {exc(at)} ({len(calls) - n3})")
t = [f for f in frames(at) if "순위" in list(f.columns) and "점유율" in list(f.columns)]
check(len(t) == 1 and t[0].iloc[0]["국가"] == "중국" and t[0].iloc[1]["국가"] == "미국", f"1위 중국 2위 미국 {list(t[0]['국가']) if t else None}")
at.radio(key="tr_r_level").set_value("대륙").run(); go_r(at)
t = [f for f in frames(at) if "순위" in list(f.columns) and "점유율" in list(f.columns)]
check(not exc(at) and len(t) == 1 and "대륙" in t[0].columns, f"대륙 순위 {exc(at)}")
# 덜 받은 자료(complete False)면 예전 방식(관세청 호출)
GROUPS["complete"] = False
import streamlit as _st; _st.cache_data.clear()
at2 = AppTest.from_file(REPO + "/app.py", default_timeout=300); at2.run()
at2.radio(key="tr_view").set_value("일반 검색").run(); at2.radio(key="tr_s_item").set_value("20대").run()
at2.button_group(key="tr_s_groups").set_value(["반도체"]).run(); n4 = len(calls); go(at2)
check(not exc(at2) and len(calls) > n4, f"complete=false면 관세청 조회 ({len(calls) - n4}번)")
# 품목별 구성 · 20대 기준
import streamlit as _st; _st.cache_data.clear(); GROUPS["complete"] = True
at3 = AppTest.from_file(REPO + "/app.py", default_timeout=300); at3.run()
at3.radio(key="tr_view").set_value("일반 검색").run(); at3.radio(key="tr_s_kind").set_value("품목별").run()
check(at3.radio(key="tr_c_basis").options == ["HS", "20대"] and at3.radio(key="tr_c_basis").value == "HS", "기준 선택지 HS/20대, 기본 HS")
at3.radio(key="tr_c_basis").set_value("20대").run()
at3.multiselect(key="tr_c_cnty").set_value(["중국"]).run()
n5 = len(calls); [b for b in at3.button if b.key == "tr_c_go"][0].click().run()
tc3 = [f for f in frames(at3) if "순위" in list(f.columns) and "품목" in list(f.columns)]
check(not exc(at3) and len(calls) == n5 and len(tc3) == 1, f"20대 구성 호출 없음 {exc(at3)} ({len(calls) - n5}) 표 {len(tc3)}")
if tc3:
    names3 = list(tc3[0]["품목"])
    check("반도체" in names3 and "자동차" in names3 and names3[-1] == "합계", f"20대 품목 줄 {names3}")
    row = tc3[0][tc3[0]["품목"] == "반도체"].iloc[0]
    want = sum(1000 * (i + 1) * 1000 for i in idx) / 1e8
    check(abs(float(str(row["수출"]).replace("억달러", "").replace(",", "")) - want) < 0.2, f"중국 반도체 수출 {row['수출']} vs {want:.2f}")
ms3 = at3.multiselect(key="tr_c_metrics"); ms3.set_value(["수출금액", "수출 중량"]).run()
[b for b in at3.button if b.key == "tr_c_go"][0].click().run()
check(any("금액 지표만" in w.value for w in at3.warning), "20대 기준에서 중량 고르면 경고")
# 월간 현황: 20대 품목 증감 순위
GROUPS["data"]["자동차"] = {"CN": [[7 * (k + 1) * 1000 for k in range(len(M))], [3] * len(M)]}  # 자동차는 가파르게 증가(전년 대비 증감률이 더 큼)
_st.cache_data.clear()
at5 = AppTest.from_file(REPO + "/app.py", default_timeout=300); at5.run()
at5.radio(key="tr_view").set_value("월간").run()
check(not exc(at5), f"월간 현황 예외 없음 {exc(at5)}")
gr = [f for f in frames(at5) if "품목" in list(f.columns) and "증감률" in list(f.columns) and "증감액" in list(f.columns)]
check(len(gr) == 1, f"20대 증감 순위 표 {len(gr)}개")
if gr:
    g = gr[0]
    check(list(g["품목"]) == ["자동차", "반도체"] or set(g["품목"]) == {"자동차", "반도체"}, f"품목 {list(g['품목'])}")
    r1 = g.iloc[0]
    check(str(r1["증감률"]).startswith("+") and str(r1["증감액"]).endswith("억달러"), f"1위 {r1.to_dict()}")
    at5.radio(key="tr_m_gorder").set_value("증감액").run()
    g2 = [f for f in frames(at5) if "증감액" in list(f.columns) and "품목" in list(f.columns)][0]
    check(list(g2["품목"])[0] in ("반도체", "자동차"), f"증감액 정렬도 동작 {list(g2['품목'])}")
    at5.radio(key="tr_m_gkind").set_value("수입").run()
    check(not exc(at5), "수입 보기 예외 없음")
print("실패", fails); sys.exit(1 if fails else 0)
