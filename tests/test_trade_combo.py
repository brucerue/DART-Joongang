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
# 0) 경제권 구성국: 관세청 경제권 합계와 국가별 합계가 모든 달 정확히 같아야 함 (실제 trade.json)
REAL = json.load(open(FIX + "/trade_full.json", encoding="utf-8"))
byname = {v[0]: k for k, v in REAL["region"].items()}
for z in tc.ZONES:
    e, i = tc.sum_arrays(REAL["country"], tc.zone_codes(z), REAL["months"], tc.ZONE_WINDOW.get(z))
    r = REAL["region"][byname[z]]
    check(all((e[k] or 0) == (r[1][k] or 0) and (i[k] or 0) == (r[2][k] or 0) for k in range(len(e)) if r[1][k] is not None), f"경제권 {z} 구성국 합계 = 관세청 합계(10년)")
ALLM = [False]
# 대륙 구성국 = 관세청 대륙별 합계 (수출·수입, 모든 달)
bad = 0
for k, v in REAL["continent"].items():
    e, i = tc.sum_arrays(REAL["country"], tc.continent_codes(v[0]))
    bad += sum(1 for t in range(len(e)) if (e[t] or 0) != (v[1][t] or 0) or (i[t] or 0) != (v[2][t] or 0))
check(bad == 0 and len(tc.CONTINENTS) == 9, f"대륙 9개 구성국 합계 = 관세청 대륙별 합계 (불일치 {bad})")
allc = [c for v in tc.CONTINENTS.values() for c in v.split()]
check(len(allc) == len(set(allc)), "한 나라가 두 대륙에 들어가지 않음")
def groups(at, names):
    at.button_group(key="tr_s_groups").set_value(list(names)).run()
def go(at):
    if not ALLM[0]:
        for k in ("tr_s_metrics_stored", "tr_s_metrics_live"):
            try: at.multiselect(key=k).set_value(["수출금액"]).run()
            except Exception: pass
    [b for b in at.button if b.key == "tr_s_go"][0].click().run()
def setq(at, item=None, reg=None):
    if item: at.radio(key="tr_s_item").set_value(item).run()
    if reg: at.radio(key="tr_s_reg").set_value(reg).run()
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not exc(at), f"기본 화면 오류 없음 {exc(at)}")
at.radio(key="tr_view").set_value("일반 검색").run()
check(at.radio(key="tr_s_kind").options == ["일반", "국가별", "품목별"], "종류: 품목·지역/AI 검색")
check(at.radio(key="tr_s_item").value == "전체" and at.radio(key="tr_s_reg").value == "전체", "품목·지역 기본은 전체")
n0 = len(calls)
ALLM[0] = True; go(at); ALLM[0] = False
f = tfr(at)
cols = list(f[0].columns)
check(cols == ["월", "수출", "수출 증감", "수입", "수입 증감", "무역수지", "무역수지 증감"], f"기본은 수출·수입·무역수지 + 각 증감 {cols}")
last = f[0].iloc[-2]
check(last["수출"] == "1,209.4억달러" and last["수입"] == "710.9억달러" and last["무역수지"] == "498.5억달러" and last["수출 증감"] == "+83.5%" and last["수입 증감"] == "+26.0%" and last["무역수지 증감"].endswith("억달러"), f"총괄 잠정 3지표 {last.to_dict()}")
at.multiselect(key="tr_s_metrics_stored").set_value(["수출금액", "무역수지"]).run(); [b for b in at.button if b.key == "tr_s_go"][0].click().run()
check(list(tfr(at)[0].columns) == ["월", "수출", "수출 증감", "무역수지", "무역수지 증감"], "지표를 빼면 그 열이 사라짐")
ALLM[0] = False; go(at)
f = tfr(at)
check(not exc(at) and len(f) == 1 and names(f[0]) == ["전체 수출입"] and len(calls) == n0, f"전체×전체 = 총괄, 호출 없음 {names(f[0]) if f else None}")
check(list(f[0]["월"])[-2] == "2026.10 (잠정)" and f[0].iloc[-2]["전체 수출입"] == "1,209.4억달러", f"총괄 잠정 {f[0].iloc[-2].to_dict()}")
# 여러 항목 + 지표 3개: 열 이름 "항목 · 지표"
setq(at, reg="국가"); at.multiselect(key="tr_s_cnty").set_value(["미국", "중국"]).run()
at.multiselect(key="tr_s_metrics_stored").set_value(["수출금액", "수입금액", "무역수지"]).run()
ALLM[0] = True; go(at); ALLM[0] = False
cols = list(tfr(at)[0].columns)
check("중국 · 수출" in cols and "중국 · 무역수지 증감" in cols and len(cols) == 1 + 2 * 3 * 2, f"여러 항목×3지표 열 {cols}")
setq(at, reg="전체")
# 1) 국가
setq(at, reg="국가"); at.multiselect(key="tr_s_cnty").set_value(["미국", "중국"]).run(); go(at)
f = tfr(at)[0]
check(names(f) == ["미국", "중국"] and f.iloc[-2]["중국"] == "260.1억달러" and len(calls) == n0, f"국가 2개 {names(f)}")
# 2) 경제권 (EU, ASEAN) = 관세청 경제권 월별 합계
setq(at, reg="경제권"); at.multiselect(key="tr_s_zone").set_value(["EU", "ASEAN"]).run(); go(at)
f = tfr(at)[0]
check(names(f) == ["EU", "ASEAN"] and f.iloc[-2]["ASEAN"] == "212.9억달러", f"경제권 {names(f)} {f.iloc[-2].to_dict()}")
eu = TRADE["region"]["10"][1][-1]
check(f.iloc[-3]["EU"] == f"{eu / 1e8:,.1f}억달러", f"EU 확정 달 값 = 경제권 합계 {f.iloc[-3]['EU']} vs {eu/1e8:,.1f}")
# 3) 산업부 20대(국가 전체): 호출 발생, 국가코드 없이
setq(at, item="20대", reg="전체")
groups(at, ["반도체", "일반기계"]); n0 = len(calls); go(at)
f = tfr(at)[0]
check(not exc(at) and names(f) == ["반도체", "일반기계"] and len(calls) > n0, f"MTI 20대 {names(f)} 호출 {len(calls) - n0}")
check(all("cntyCd" not in c for c in calls[n0:]), "품목 조회에는 국가코드를 안 보냄(국가는 받은 행에서 거름)")
check(f.iloc[-2]["반도체"] == "603.0억달러" and list(f["월"])[-2] == "2026.10 (잠정)", f"반도체 잠정 {f.iloc[-2]['반도체']}")
tot_all = f.iloc[-3]["반도체"]
# 4) 같은 품목 × 지역: 새 API 호출 없이 캐시에서 거름
n1 = len(calls)
setq(at, reg="국가"); at.multiselect(key="tr_s_cnty").set_value(["미국", "중국"]).run()
groups(at, ["반도체"]); go(at)
f = tfr(at)[0]
check(names(f) == ["반도체 × 미국", "반도체 × 중국"] and len(calls) == n1, f"품목×국가 4→2행, 추가 호출 {len(calls) - n1}: {names(f)}")
setq(at, reg="경제권"); at.multiselect(key="tr_s_zone").set_value(["NAFTA"]).run(); go(at)
f = tfr(at)[0]
check(names(f) == ["반도체 × NAFTA"] and len(calls) == n1 and not exc(at), f"품목×경제권 {names(f)} 추가 호출 {len(calls) - n1}")
# 5) 국가 합 = 지역 전체 (미국+중국 ≤ 전체) : 같은 달 값 비교
setq(at, reg="국가"); at.multiselect(key="tr_s_cnty").set_value(["미국", "중국"]).run(); go(at)
f = tfr(at)[0]
def eok(t): return float(t.replace(",", "").replace("억달러", "").replace("만달러", "e-4"))
check(eok(f.iloc[-3]["반도체 × 미국"]) + eok(f.iloc[-3]["반도체 × 중국"]) <= eok(tot_all) + 0.2, "미국+중국 ≤ 전체")
# 6) HS 품목 × 국가 (HS 4자리 조회, 국가는 거름)
setq(at, item="HS코드", reg="국가")
at.multiselect(key="tr_s_hs").set_value(["8542 전자집적회로"]).run(); at.multiselect(key="tr_s_cnty").set_value(["미국"]).run()
n2 = len(calls); go(at)
f = tfr(at)[0]
check(not exc(at) and names(f) == ["8542 전자집적회로 × 미국"] and len(calls) > n2 and all("cntyCd" not in c for c in calls[n2:]), f"HS×국가 {names(f)} 호출 {len(calls) - n2}")
check(any("국가별·하위 품목별" in e.label for e in at.expander), "HS 조회: 국가별·하위 품목별 칸")
# 7a) MTI 코드 모드: 빠른 선택 없음, 코드 직접 입력
at.radio(key="tr_s_kind").set_value("일반").run()
setq(at, item="MTI코드", reg="전체")
check(len([b for b in at.button_group if b.key == "tr_s_groups"]) == 0, "MTI 코드 모드에는 20대 선택 칩 없음")
check(not at.text_input(key="tr_s_mti_extra") if False else len([ti for ti in at.text_input if ti.key in ("tr_s_mti_extra", "tr_s_hs_extra")]) == 0, "직접 입력 칸 없음(검색창 하나)")
at.multiselect(key="tr_s_mti").set_value([[o for o in at.multiselect(key="tr_s_mti").options if o.startswith("831110 ")][0]]).run(); n4 = len(calls); go(at)
f = tfr(at)[0]
check(not exc(at) and len(names(f)) == 1 and len(calls) > n4, f"MTI 코드 직접 입력 {names(f)}")
setq(at, item="20대")
check(len(at.button_group(key="tr_s_groups").options) == 20, "20대 품목 선택 칩 20개")
check(set(at.radio(key="tr_s_item").options) == {"전체", "20대", "HS코드", "MTI코드"}, "품목 선택지 4개")
# 7b) 대륙: 품목 전체 / 품목 × 대륙
at.radio(key="tr_s_kind").set_value("일반").run()
setq(at, item="전체", reg="대륙"); at.multiselect(key="tr_s_cont").set_value(["아시아", "북미"]).run(); go(at)
f = tfr(at)[0]
check(not exc(at) and names(f) == ["아시아", "북미"], f"대륙(품목 전체) {names(f)} {exc(at)}")
setq(at, item="20대", reg="대륙"); groups(at, ["반도체"])
at.multiselect(key="tr_s_cont").set_value(["북미"]).run(); n3 = len(calls); go(at)
f = tfr(at)[0]
check(not exc(at) and names(f) == ["반도체 × 북미"] and len(calls) == n3, f"품목×대륙 {names(f)} 추가 호출 {len(calls) - n3}")
# 8) 입력 문제
at.radio(key="tr_s_kind").set_value("일반").run()
setq(at, item="HS코드", reg="전체"); at.multiselect(key="tr_s_hs").set_value([]).run(); go(at)
check(any("품목을 하나 이상" in w.value for w in at.warning), "품목 안 고르면 경고")
setq(at, item="전체", reg="경제권"); at.multiselect(key="tr_s_zone").set_value([]).run(); go(at)
check(any("경제권을(를) 하나 이상" in w.value for w in at.warning), "경제권 안 고르면 경고")
setq(at, reg="전체")
setq(at, item="HS코드", reg="국가")
at.multiselect(key="tr_s_hs").set_value(["8542 전자집적회로", "8541 다이오드ㆍ트랜지스터 및 반도체 디바이스", "8703 승용차", "8507 축전지", "72", "2710"]).run()
pass
at.multiselect(key="tr_s_cnty").set_value(["미국", "중국", "홍콩"]).run(); go(at)
check(any("한 번에 12개" in w.value for w in at.warning), "12개 초과 경고")
# 9) 잠정치 없으면 확정 달까지
FLASH_ON[0] = False
import streamlit as _st; _st.cache_data.clear()
at2 = AppTest.from_file(REPO + "/app.py", default_timeout=300); at2.run()
at2.radio(key="tr_view").set_value("일반 검색").run()
at2.radio(key="tr_s_period").set_value("직접 선택").run()
check(at2.selectbox(key="tr_s_end").options[-1] == "2026.09" and not exc(at2), "flash 없으면 확정 달까지만")
print("실패", fails); sys.exit(1 if fails else 0)
