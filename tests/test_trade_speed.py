import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import faulthandler; faulthandler.dump_traceback_later(600, exit=True)
import time
DELAY = 0.5
import os, sys, types, json, requests, re
os.environ["DART_API_KEY"] = "x"*40
os.environ["ANTHROPIC_API_KEY"] = "sk-test" if "noai" not in sys.argv else ""
os.environ["NAVER_CLIENT_ID"] = "nid"
os.environ["NAVER_CLIENT_SECRET"] = "nsec"
if "noai" in sys.argv: os.environ.pop("ANTHROPIC_API_KEY")
os.environ["CUSTOMS_API_KEY"] = "dummy%2Bkey" if (len(sys.argv) < 2 or sys.argv[1] != "nokey") else ""
if not os.environ["CUSTOMS_API_KEY"]: os.environ.pop("CUSTOMS_API_KEY")
MODE = "plain"  # 품목 행은 간단한 가짜 자료로
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
AI_REPLY = [None]
POSTS = []
def fake_post(self, url, **k):
    POSTS.append(k.get("json"))
    prompt = k["json"]["messages"][0]["content"]
    r = R({"content": [{"type": "text", "text": AI_REPLY[0](prompt)}]}); return r
requests.Session.post = fake_post
def fake_get(url, *a, **k):
    if "openapi.naver.com" in url:
        return R({"items": [{"title": "<b>전기차</b> 수출 사상 최대", "originallink": "https://www.yna.co.kr/view/AKR1", "link": "https://n.news.naver.com/1", "pubDate": "Fri, 23 Oct 2026 09:00:00 +0900"},
                            {"title": "미국 전기차 관세 영향", "originallink": "https://www.hankyung.com/a/2", "link": "https://n.news.naver.com/2", "pubDate": "Thu, 22 Oct 2026 09:00:00 +0900"}], "total": 2})
    if "/flash/flash.json" in url:
        if not FLASH_ON[0]: raise requests.RequestException("no flash")
        return R(FLASH)
    if "/trade/trade.json" in url: return R(TRADE)
    if "nitemtrade" in url:
        time.sleep(DELAY)  # 실측 관세청 응답(1~4초)을 줄여 흉내: 한 번에 DELAY초
        r = R(); r.text = item_xml(k.get("params", {})); return r
    if "corp_codes.csv" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = CSV.encode("utf-8"); return r
    return R()
requests.get = fake_get
requests.Session.get = lambda self, url, **k: fake_get(url, **k)
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
def run_q(at): [b for b in at.button if b.key == "tr_ai_run"][0].click().run()
def go_ai(at, text):
    at.text_area(key="tr_ai_text").set_value(text).run()
    [b for b in at.button if b.key == "tr_ai_go"][0].click().run()

# 관세청이 한 번에 DELAY초 걸릴 때, HS 4자리 코드 3개를 제안받아 월별 추이(올해·전년 비교)를 조회하는 시간을 잰다.
# 먼저 관세청 0초로 같은 흐름을 돌려 '화면 그리기 시간'을 재고, 그만큼을 빼고 비교한다.
# 예전처럼 하나씩 부르면 AI 답 뒤 확인·펼치기 9번(4.5초) + 조회 6번(3초)을 그대로 기다렸다.
CODES = [None]
def reply(prompt):
    return json.dumps({"summary": "미국 수출", "items": [
        {"kind": "hs", "code": c, "reason": c, "confidence": "높음", "refs": []} for c in CODES[0]],
        "regions": [{"kind": "국가", "name": "미국"}], "period": {"start": "2025-01", "end": "2025-12"}, "metrics": ["수출금액"], "view": "trend"}, ensure_ascii=False)
AI_REPLY[0] = reply
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.radio(key="tr_view").set_value("AI 검색").run()
def flow(codes, text, delay):
    global DELAY
    DELAY, CODES[0] = delay, codes
    calls.clear(); t0 = time.time(); go_ai(at, text); t_ask = time.time() - t0; n_ask = len(calls)
    time.sleep(0.5 * 3)  # 사용자가 코드를 확인하는 시간 (그동안 뒤에서 조회 자료를 미리 받는다)
    calls.clear(); t0 = time.time(); run_q(at); t_q = time.time() - t0
    return t_ask, n_ask, t_q, len(calls)
b_ask, _, b_q, _ = flow(["2710", "7208", "8901"], "작년 미국으로 수출된 석유·철강·선박", 0.0)  # 화면 그리기 시간
t_ask, n_ask, t_q, n_q = flow(["8703", "8542", "8507"], "작년 미국으로 수출된 자동차·반도체·배터리", 0.5)
check(not exc(at), f"오류 없음 {exc(at)}")
t = tfr(at)
check(t and any("8703" in c for c in t[0].columns) and any("8507" in c for c in t[0].columns), f"세 품목 결과 표 {[list(f.columns)[:4] for f in t]}")
w_ask, w_q = t_ask - b_ask, t_q - b_q
print(f"   AI 답 뒤 확인·펼치기: 관세청 기다림 {w_ask:.1f}초 (관세청 {n_ask}번, 하나씩이면 약 {n_ask * 0.5:.1f}초)")
print(f"   조회: 관세청 기다림 {w_q:.1f}초 (이때 새로 부른 관세청 {n_q}번, 하나씩이면 약 3.0초)")
check(w_ask < n_ask * 0.5 * 0.45, f"AI 답 뒤 확인·펼치기를 동시에 ({w_ask:.1f}초 < {n_ask * 0.5 * 0.45:.1f}초)")
check(w_q < 1.0, f"조회는 미리 받아 둔 자료로 ({w_q:.1f}초 < 1.0초)")
print("실패", fails); sys.exit(1 if fails else 0)
