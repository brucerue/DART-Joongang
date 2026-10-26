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
CODES10 = sorted(_ALL_H10 := [h for hs6 in sorted(_ALL) for h in sorted(_ALL[hs6])])[:300]
def item_xml_country(params):
    cn = params["cntyCd"]
    items = ["<item><year>총계</year><hsCd>-</hsCd><expDlr>1</expDlr><impDlr>1</impDlr></item>"]
    for ym in month_iter(params["strtYymm"], params["endYymm"]):
        for k, h in enumerate(CODES10):
            items.append(f"<item><year>{ym}</year><hsCd>{h}</hsCd><statKor>품목 {h}</statKor><statCd>{cn}</statCd><statCdCntnKor1>{cn}</statCdCntnKor1>"
                         f"<expDlr>{1000 * (k % 7 + 1)}</expDlr><impDlr>{500 * (k % 3 + 1)}</impDlr><expWgt>{10 * (k % 5 + 1)}</expWgt><impWgt>5</impWgt></item>")
    calls.append(dict(params))
    return "<response><header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header><body><items>" + "".join(items) + "</items></body></response>"
def item_xml(params):
    if not params.get("hsSgn") and params.get("cntyCd"): return item_xml_country(params)
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
def trade_usd_t(v):
    a = abs(v)
    return f"{v / 1e8:,.1f}억달러" if a >= 1e8 else (f"{v / 1e4:,.0f}만달러" if a >= 1e4 else f"{v:,.0f}달러")
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
def cfr(at): return [f for f in frames(at) if "순위" in list(f.columns) and "점유율" in list(f.columns) and "품목" in list(f.columns)]
def go_c(at): [b for b in at.button if b.key == "tr_c_go"][0].click().run()
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.radio(key="tr_view").set_value("일반 검색").run()
check(at.radio(key="tr_s_kind").options == ["일반", "국가별", "품목별"], f"종류 {at.radio(key='tr_s_kind').options}")
at.radio(key="tr_s_kind").set_value("품목별").run()
check(not exc(at), f"화면 오류 없음 {exc(at)}")
go_c(at)
check(any("하나 이상 골라" in w.value for w in at.warning), "지역 안 고르면 경고")
at.multiselect(key="tr_c_cnty").set_value(["미국"]).run()
n0 = len(calls); go_c(at)
t = cfr(at)
check(not exc(at) and len(t) == 1, f"미국 품목별 구성 {exc(at)}")
check(len(calls) - n0 == 2 and all(c.get("cntyCd") == "US" and "hsSgn" not in c for c in calls[n0:]), f"국가만 지정해 올해+전년 2번 호출 ({len(calls) - n0})")
t = t[0]
# 기대값: 올해(2026-01~09, 9개월) HS2 합
def exp_for(prefix_len, months_n, metric="exp"):
    acc = {}
    for k, h in enumerate(CODES10):
        v = {"exp": 1000 * (k % 7 + 1), "imp": 500 * (k % 3 + 1), "exp_wgt": 10 * (k % 5 + 1)}[metric] * months_n
        acc[h[:prefix_len]] = acc.get(h[:prefix_len], 0) + v
    return acc
e2 = exp_for(2, 9)
top2 = sorted(e2.items(), key=lambda kv: -kv[1])[0]
check(t.iloc[0]["품목"].startswith(top2[0]) and t.iloc[0]["수출"] == trade_usd_t(top2[1]), f"HS2 1위 {t.iloc[0]['품목']} {t.iloc[0]['수출']} vs {top2}")
check(str(t.iloc[-1]["품목"]) == "합계" and abs(sum(float(x.rstrip('%')) for x in t.iloc[:-1]["점유율"]) - 100) < 0.2, "점유율 합 100")
check("수출 증감" in t.columns and t.iloc[0]["수출 증감"] != "-", f"전년 비교 열 {list(t.columns)}")
caps = " ".join(c.value for c in at.caption)
check("HS 2자리 단위" in caps and "위치: 전체" in caps, "단계·위치 안내")
# 내려가기
first = t.iloc[0]["품목"].split()[0]
sel = at.selectbox(key="tr_c_drill_")
opts = [o for o in sel.options if o != "(선택)"]
sel.select(opts[0]).run()
t2 = cfr(at)[0]
check(not exc(at) and all(str(x).split()[0].startswith(first) for x in t2["품목"] if str(x) not in ("합계",) and not str(x).startswith("기타")), f"{first}류 안 HS4 {list(t2['품목'])[:3]}")
caps = " ".join(c.value for c in at.caption)
check("HS 4자리 단위" in caps and f"위치: 전체 › {first}" in caps, "4자리 단계 안내")
n1 = len(calls)
sel = at.selectbox(key=f"tr_c_drill_{first}"); sel.select([o for o in sel.options if o != "(선택)"][0]).run()
caps = " ".join(c.value for c in at.caption)
check("HS 6자리 단위" in caps and len(calls) == n1 and "대표" not in caps and "가장 큰 세부 품목" in caps, "6자리로 내려가도 추가 호출 없음")
p6 = [c for c in at.caption if "위치:" in c.value][0].value.split("› ")[-1]
sel = at.selectbox(key=f"tr_c_drill_{p6}"); sel.select([o for o in sel.options if o != "(선택)"][0]).run()
caps = " ".join(c.value for c in at.caption)
check("HS 10자리 단위" in caps and not [s for s in at.selectbox if str(s.key).startswith("tr_c_drill_") and len(str(s.key)) > 14], "10자리에서는 더 내려가기 없음")
t10 = cfr(at)[0]
check(all(len(str(x).split()[0]) == 10 for x in t10["품목"] if not str(x).startswith(("기타", "합계"))), "10자리 코드 표시")
up = [b for b in at.button if b.key == "tr_c_up"][0]; up.click().run()
check("HS 6자리 단위" in " ".join(c.value for c in at.caption), "한 단계 위로")
[b for b in at.button if b.key == "tr_c_top_btn"][0].click().run()
check("HS 2자리 단위" in " ".join(c.value for c in at.caption), "처음으로")
# 지표: 기본 3개(수출금액·수입금액·무역수지), 중량 추가
check(at.multiselect(key="tr_c_metrics").value == ["수출금액", "수입금액", "무역수지"], f"지표 기본 3개 {at.multiselect(key='tr_c_metrics').value}")
go_c(at); t = cfr(at)[0]
check(all(c in t.columns for c in ("수출", "수입", "무역수지", "수출 증감", "수입 증감", "무역수지 증감")), f"기본 3지표 열 {list(t.columns)}")
at.multiselect(key="tr_c_metrics").set_value(["수출금액", "수출 중량", "수입 중량"]).run(); go_c(at); t = cfr(at)[0]
check("수출 중량" in t.columns and "수입 중량" in t.columns and "무역수지" not in t.columns, f"중량 지표 열 {list(t.columns)}")
check(str(t.iloc[0]["수출 중량"]).endswith("톤"), f"중량은 톤 {t.iloc[0]['수출 중량']}")
at.multiselect(key="tr_c_metrics").set_value(["무역수지"]).run(); go_c(at); t = cfr(at)[0]
check(t.iloc[0]["점유율"] == "-", "무역수지만 고르면 점유율 없음")
at.multiselect(key="tr_c_metrics").set_value([]).run(); go_c(at)
check(any("지표를 하나 이상" in w.value for w in at.warning), "지표 없으면 경고")
at.multiselect(key="tr_c_metrics").set_value(["수출금액"]).run(); go_c(at)
# 비교 끄기
at.toggle(key="tr_c_compare").set_value(False).run(); n2 = len(calls); go_c(at); t = cfr(at)[0]
check(len(calls) - n2 == 0 and "수출 증감" not in t.columns, f"비교 끄면 전년 호출 없음(올해 자료는 저장된 것 사용), 열 {list(t.columns)}")
at.toggle(key="tr_c_compare").set_value(True).run()
# 경제권 EU: 영국은 2020-12까지라 올해 기간에는 호출하지 않음
at.radio(key="tr_c_reg").set_value("경제권").run(); at.multiselect(key="tr_c_zone").set_value(["EU"]).run(); n3 = len(calls); go_c(at)
cs = {c["cntyCd"] for c in calls[n3:]}
check(not exc(at) and "GB" not in cs and len(cs) == 27, f"EU 27개국(영국 제외) 호출 {len(cs)} {'GB' in cs}")
# 경제권 EU 2020 이전 기간에는 영국 포함
at.radio(key="tr_c_period").set_value("직접 선택").run(); at.selectbox(key="tr_c_start").select("2019-01").run(); at.selectbox(key="tr_c_end").select("2019-12").run()
at.toggle(key="tr_c_compare").set_value(False).run(); n4 = len(calls); go_c(at)
check("GB" in {c["cntyCd"] for c in calls[n4:]}, "2019년 EU에는 영국 포함")
# 기간 너무 김
at.selectbox(key="tr_c_start").select("2016-11").run(); at.selectbox(key="tr_c_end").select("2026-09").run(); go_c(at)
check(any("최대 3년" in w.value for w in at.warning), "기간 36개월 초과 경고")
# 대륙
at.radio(key="tr_c_period").set_value("1년").run(); at.radio(key="tr_c_reg").set_value("대륙").run(); at.multiselect(key="tr_c_cont").set_value(["북미"]).run(); n5 = len(calls); go_c(at)
check({c["cntyCd"] for c in calls[n5:]} == {"US", "CA"} and not exc(at), "대륙(북미 = 미국·캐나다)")
print("실패", fails); sys.exit(1 if fails else 0)
