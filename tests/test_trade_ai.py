import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import faulthandler; faulthandler.dump_traceback_later(600, exit=True)
import os, sys, types, json, requests, re
os.environ["DART_API_KEY"] = "x"*40
os.environ["ANTHROPIC_API_KEY"] = "sk-test" if "noai" not in sys.argv else ""
os.environ["NAVER_CLIENT_ID"] = "nid"
os.environ["NAVER_CLIENT_SECRET"] = "nsec"
if "noai" in sys.argv: os.environ.pop("ANTHROPIC_API_KEY")
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
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
check(not exc(at), f"기본 화면 오류 없음 {exc(at)}")
at.radio(key="tr_view").set_value("일반 검색").run()
check(at.radio(key="tr_s_kind").options == ["일반", "국가별", "품목별"], f"종류에서 시도·세관 빠짐 {at.radio(key='tr_s_kind').options}")
at.radio(key="tr_view").set_value("AI 검색").run()
if "noai" in sys.argv:
    check(any("AI 검색을 쓰려면" in i.value for i in at.info), "AI 키 없으면 안내")
    print("실패", fails); sys.exit(1 if fails else 0)
check(not exc(at), f"AI 검색 화면 오류 없음 {exc(at)}")
# 1) 품목 찾기: 전기차
def reply1(prompt):
    assert "전기차" in prompt and "산업부 20대" in prompt and "참고 기사" in prompt
    assert "전기차 수출 사상 최대" in prompt   # 참고 기사가 프롬프트에 들어감
    return json.dumps({"summary": "전기자동차 품목 코드를 찾음", "items": [
        {"kind": "hs", "code": "8703.80", "reason": "전기 모터만으로 추진하는 승용차(HS 8703.80)", "confidence": "높음", "refs": [1]},
        {"kind": "group", "code": "자동차", "reason": "산업부 20대 품목의 자동차(내연기관 포함, 전기차만이 아님)", "confidence": "낮음", "refs": []},
        {"kind": "hs", "code": "ABCD", "reason": "잘못된 코드", "confidence": "높음"}],
        "regions": [], "period": None, "metrics": None, "notes": "전기차 전용 코드는 없어요"}, ensure_ascii=False)
AI_REPLY[0] = reply1
go_ai(at, "전기차")
check(not exc(at), f"AI 질문 오류 없음 {exc(at)}")
check(any("AI가 제안한 코드" in w.value for w in at.warning), "확인하라는 경고")
labels = [c.label for c in at.checkbox if "확신" in c.label]
check(len(labels) == 2 and "HS 870380" in labels[0] and "🟢" in labels[0] and "자동차 (산업부 20대 품목)" in labels[1] and "🔴" in labels[1], f"제안 코드 2개(잘못된 코드는 뺌) {labels}")
check([c.value for c in at.checkbox if "확신" in c.label] == [True, False], "확신 낮음은 기본 체크 안 됨")
caps = " ".join(c.value for c in at.caption)
check("관세청 품목명: 품목 870380" in caps, "관세청 자료로 코드 확인")
check("근거 기사 [기사1](https://www.yna.co.kr/view/AKR1)" in caps, f"근거 기사 링크")
check("전기차 전용 코드는 없어요" in caps and "HS 코드 'ABCD'" in caps, "메모·경고 표시")
check(any("참고 기사" in e.label for e in at.expander), "참고 기사·사이트 칸")
md = " ".join(m.value for m in at.markdown)
check("unipass.customs.go.kr" in md and "stat.kita.net" in md and "전기차</b>" not in md and "전기차 수출 사상 최대" in md, "참고 링크·기사 표시(태그 제거)")
check(not tfr(at), "AI가 찾기만 하면 결과는 아직 안 나옴(조회 버튼 대기)")
run_q(at)
f = tfr(at)
check(not exc(at) and len(f) == 1, f"조회 누르면 결과 {exc(at)}")
cols = list(f[0].columns) if f else []
check(cols[:3] == ["월", "수출", "수출 증감"] and len(cols) == 7, f"체크한 HS 1개 × 지표 3개 {cols}")
# 체크를 바꾸면 결과가 바뀜: 낮음(자동차)도 체크 -> 두 항목
cb = [c for c in at.checkbox if "확신" in c.label]
cb[1].check().run(); run_q(at)
f = tfr(at)
check(not exc(at) and len(f) == 1 and sum("870380" in c or c.startswith("자동차") for c in f[0].columns) >= 2, f"체크 추가하면 결과에 반영 {list(f[0].columns)[:6] if f else None}")
cb = [c for c in at.checkbox if "확신" in c.label]
cb[0].uncheck(); cb[1].uncheck(); at.run(); run_q(at)
check(any("체크해 주세요" in w.value for w in at.warning) and not tfr(at), "모두 해제하고 조회하면 안내")
# 같은 입력은 다시 AI를 부르지 않음(저장된 답)
n1 = len(POSTS); go_ai(at, "전기차")
check(len(POSTS) == n1, f"같은 입력은 AI 재호출 없음 ({len(POSTS) - n1})")
# 2) 자연어 질의
def reply2(prompt):
    assert "2025년 미국으로 수출된 자동차 수출액" in prompt
    return "```json\n" + json.dumps({"summary": "2025년 미국 자동차 수출액", "items": [{"kind": "group", "code": "자동차", "reason": "산업부 20대 품목 자동차", "confidence": "높음", "refs": []}],
        "regions": [{"kind": "국가", "name": "미국"}], "period": {"start": "2025-01", "end": "2025-12"}, "metrics": ["수출금액"], "notes": ""}, ensure_ascii=False) + "\n```"
AI_REPLY[0] = reply2
go_ai(at, "2025년 미국으로 수출된 자동차 수출액")
check(not exc(at) and any("2025.01 ~ 2025.12" in c.value for c in at.caption), "기간 해석 표시")
run_q(at)
f = tfr(at)
check(not exc(at) and len(f) == 1 and names(f[0]) == ["자동차 × 미국"], f"자연어 → 일반 검색 결과 {names(f[0]) if f else None} {exc(at)}")
months = [str(x) for x in f[0]["월"]]
check(months[0] == "2025.01" and months[-2] == "2025.12" and months[-1] == "합계" and len(months) == 13, f"기간 2025.01~12 + 합계 {months[:2]}…{months[-2:]}")
check(list(f[0].columns) == ["월", "자동차 × 미국", "자동차 × 미국 증감"], f"지표 수출만 {list(f[0].columns)}")
# 3) AI가 지어낸 지역·품목은 걸러짐
def reply3(prompt):
    return json.dumps({"summary": "x", "items": [{"kind": "mti", "code": "0000000", "reason": "x", "confidence": "높음"}], "regions": [{"kind": "국가", "name": "아틀란티스"}], "period": {"start": "1999-01", "end": "2099-12"}}, ensure_ascii=False)
AI_REPLY[0] = reply3
go_ai(at, "이상한 질문")
caps = " ".join(c.value for c in at.caption)
check(not exc(at) and "MTI 코드 0000000은(는) 연계표에 없어서" in caps and "아틀란티스" in caps and "종료월을" in caps, f"없는 코드·지역·기간 보정 {exc(at)}")
check("품목은 지정하지 않았어요" in caps, "남은 품목 없으면 전체로")
# 4) AI 답이 JSON이 아님
AI_REPLY[0] = lambda p: "죄송해요 모르겠어요"
go_ai(at, "아무 말")
check(not exc(at) and any("AI 답을" in e.value for e in at.error), "JSON 아니면 오류 안내")
# 5) 빈 입력
at.text_area(key="tr_ai_text").set_value("  ").run(); [b for b in at.button if b.key == "tr_ai_go"][0].click().run()
check(any("입력해 주세요" in w.value for w in at.warning), "빈 입력 경고")

# 6) HS 10자리: 일반 검색에서 직접 입력 -> 6자리로 조회해 그 10자리 행만
_h10 = sorted(_ALL["854231"])[0]
at.radio(key="tr_view").set_value("일반 검색").run(); at.radio(key="tr_s_kind").set_value("일반").run()
at.radio(key="tr_s_item").set_value("HS코드").run()
at.multiselect(key="tr_s_hs").set_value([_h10]).run()
n5 = len(calls); [b for b in at.button if b.key == "tr_s_go"][0].click().run()
f = tfr(at)
check(not exc(at) and len(f) == 1 and len(calls) > n5 and all(c.get("hsSgn") == "854231" for c in calls[n5:]), f"10자리는 6자리로 조회 {[c.get('hsSgn') for c in calls[n5:]][:3]} {exc(at)}")
check(list(f[0].columns)[:2] == ["월", "수출"], f"열 {list(f[0].columns)[:3]}")
# 7) AI: 4자리 제안 -> 하위 코드(6자리·10자리) 체크박스
at.radio(key="tr_view").set_value("AI 검색").run()
def reply7(prompt):
    assert "관세청 등으로 확인된 코드 설명" in prompt and "3102109000" in prompt
    return json.dumps({"summary": "집적회로", "items": [{"kind": "hs", "code": "8542", "reason": "집적회로", "confidence": "낮음", "refs": []}], "regions": []}, ensure_ascii=False)
AI_REPLY[0] = reply7
go_ai(at, "집적회로")
chs = [c.label for c in at.checkbox if str(c.label).startswith("↳") or str(c.label).startswith("　")]
check(len(chs) >= 2 and any("854231" in c for c in chs), f"하위 코드 체크박스 {chs[:4]}")
check(any(c.startswith("　　↳") for c in chs), "6자리 아래 10자리도 보임")
sub = [c for c in at.checkbox if str(c.label).startswith("　　↳")][0]
sub.check().run(); run_q(at)
f = tfr(at)
check(not exc(at) and len(f) == 1, f"하위 10자리 체크해서 조회 {exc(at)}")

# 8) AI가 지표를 비워도 질문의 '수입된'으로 수입금액만
def reply8(prompt):
    return json.dumps({"summary": "수입", "items": [{"kind": "group", "code": "자동차", "reason": "x", "confidence": "높음", "refs": []}],
                       "regions": [{"kind": "국가", "name": "미국"}], "period": {"start": "2025-01", "end": "2025-12"}, "metrics": None}, ensure_ascii=False)
AI_REPLY[0] = reply8
go_ai(at, "2025년 미국에서 수입된 자동차")
check(any("지표: 수입금액" in c.value for c in at.caption) and not any("수출금액" in c.value for c in at.caption if c.value.startswith("지역:")), "지표를 비워도 '수입된'이면 수입금액만")
run_q(at)
check(any("월별 추이" in m.value and m.value.rstrip().endswith("수입금액") for m in at.markdown), "표 제목에 지표 표시")
f = tfr(at)
check(len(f) == 1 and list(f[0].columns) == ["월", "자동차 × 미국", "자동차 × 미국 증감"], f"수입 하나만 {list(f[0].columns)}")

# 9) 20대 품목 이름 그대로면 그것만 (AI가 코드를 많이 붙여도)
def reply9(prompt):
    return json.dumps({"summary": "자동차 수입", "items": [
        {"kind": "group", "code": "자동차", "reason": "x", "confidence": "높음"},
        {"kind": "mti", "code": "741600", "reason": "x", "confidence": "중간"},
        {"kind": "hs", "code": "8703", "reason": "x", "confidence": "높음"},
        {"kind": "hs", "code": "8704", "reason": "x", "confidence": "중간"}],
        "regions": [{"kind": "국가", "name": "중국"}], "period": {"start": "2025-01", "end": "2025-12"}, "metrics": None}, ensure_ascii=False)
AI_REPLY[0] = reply9
go_ai(at, "2025년 중국에서 수입된 자동차")
labs = [c.label for c in at.checkbox if "확신" in c.label]
check(len(labs) == 1 and "자동차 (산업부 20대 품목)" in labs[0], f"20대 이름 그대로면 1개만 {labs}")
check(not at.expander or not any("하위 코드" in e.label for e in at.expander), "하위 코드 칸 없음")
check(any("20대 품목 이름과 같은 말" in c.value for c in at.caption), "안내 문구")
# 같은 AI 답이라도 구체적이면(전기차 8703) 그대로 여러 코드
go_ai(at, "자동차 8703")
labs = [c.label for c in at.checkbox if "확신" in c.label]
check(len(labs) == 4, f"코드가 섞인 입력은 AI 제안 그대로 {len(labs)}개")
check(any("하위 코드 보기" in e.label for e in at.expander), "하위 코드는 접힌 칸으로")
print("실패", fails); sys.exit(1 if fails else 0)
