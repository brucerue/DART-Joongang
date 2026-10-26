import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
"""잠정실적 알림 가짜 응답 테스트 (실서버 접속 없음)"""
import io, json, os, sys, zipfile, importlib.util
from datetime import datetime, timedelta, timezone
import requests
T = REPO
KST = timezone(timedelta(hours=9))
os.environ.update(DART_API_KEY="x"*40, TELEGRAM_BOT_TOKEN="fake", DIGEST_SUBSCRIBERS=json.dumps(
    [{"name": "기자", "chat_id": "111", "sectors": ["자동차"], "modes": ["단독", "공시"]},
     {"name": "통신만", "chat_id": "222", "sectors": ["자동차"], "modes": ["통신"]}]))
for k in ("PREFS_URL", "ADMIN_CHAT_ID", "TELEGRAM_CHAT_ID"): os.environ.pop(k, None)

def doc_zip(body):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z: z.writestr("20261023000001.xml", body.encode("utf-8"))
    return b.getvalue()
GOOD = """<DOCUMENT><TITLE>연결재무제표기준영업(잠정)실적(공정공시)</TITLE>
<TABLE><TR><TD>1. 연결실적내용</TD><TD>단위 : 억원, %</TD></TR>
<TR><TD>구분</TD><TD></TD><TD>당기실적<BR>('26.3Q)</TD><TD>전기실적<BR>('26.2Q)</TD><TD>전기대비증감율(%)</TD><TD>전년동기실적<BR>('25.3Q)</TD><TD>전년동기대비증감율(%)</TD></TR>
<TR><TD>매출액</TD><TD>당해실적</TD><TD>467,214</TD><TD>452,000</TD><TD>3.4</TD><TD>430,000</TD><TD>8.7</TD></TR>
<TR><TD></TD><TD>누계실적</TD><TD>1,300,000</TD><TD>-</TD><TD>-</TD><TD>1,200,000</TD><TD>8.3</TD></TR>
<TR><TD>영업이익</TD><TD>당해실적</TD><TD>35,120</TD><TD>36,000</TD><TD></TD><TD>30,000</TD><TD></TD></TR>
<TR><TD>당기순이익</TD><TD>당해실적</TD><TD>(1,204)</TD><TD>2,000</TD><TD>적자전환</TD><TD>1,500</TD><TD>적자전환</TD></TR>
</TABLE></DOCUMENT>"""
BAD = "<DOCUMENT><P>표가 없는 이상한 원문</P></DOCUMENT>"
state = {"docs": {}, "sent": [], "list_calls": []}

class R:
    def __init__(self, status=200, content=b"", js=None):
        self.status_code, self.content = status, content if js is None else json.dumps(js).encode()
        self.text = self.content.decode("utf-8", "ignore"); self.ok = status < 400
    def json(self): return json.loads(self.content)
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(str(self.status_code))
def row(no, corp, nm, dt="20261023"):
    return {"rcept_no": no, "corp_name": corp, "corp_code": corp, "report_nm": nm, "rcept_dt": dt, "stock_code": "0"}
FEED = [row("20261023000001", "현대자동차", "연결재무제표기준영업(잠정)실적(공정공시)"),
        row("20261023000002", "현대자동차", "영업(잠정)실적(공정공시)"),
        row("20261023000003", "기아", "연결재무제표기준영업(잠정)실적(공정공시)"),
        row("20261023000004", "현대모비스", "영업(잠정)실적(공정공시)"),
        row("20261023000005", "현대자동차", "주요사항보고서(자기주식취득결정)")]
TY = {"20261023000005": "B"}
def route(method, url, params=None, data=None, json_=None):
    params = params or {}
    if "list.json" in url:
        state["list_calls"].append(dict(params))
        if params.get("corp_cls") != "Y": return R(js={"status": "013", "message": "없음"})
        rows = [r for r in FEED if not params.get("pblntf_ty") or TY.get(r["rcept_no"], "I") == params["pblntf_ty"]]
        return R(js={"status": "000", "list": rows, "total_page": 1})
    if "document.xml" in url:
        d = state["docs"].get(params.get("rcept_no"))
        return R(content=doc_zip(d)) if d else R(content=b'{"status":"014","message":"no file"}')
    if "api.telegram.org" in url:
        if url.endswith("/sendMessage"):
            body = json_ or data or {}
            state["sent"].append((str(body.get("chat_id")), body.get("text")))
            return R(js={"ok": True, "result": {"message_id": 1}})
        return R(js={"ok": True, "result": []})
    if "news.google.com" in url or "naver" in url:
        return R(content=b'<?xml version="1.0"?><rss><channel></channel></rss>', js=None) if "google" in url else R(js={"items": []})
    raise AssertionError("예상 못한 주소: " + url)
def fake_req(method, url, **kw): return route(method, url, kw.get("params"), kw.get("data"), kw.get("json"))
requests.get = lambda url, **kw: fake_req("GET", url, **kw)
requests.post = lambda url, **kw: fake_req("POST", url, **kw)
requests.request = fake_req
requests.Session.request = lambda self, method, url, **kw: fake_req(method, url, **kw)

spec = importlib.util.spec_from_file_location("nd", os.path.join(T, "news_digest.py"))
nd = importlib.util.module_from_spec(spec); spec.loader.exec_module(nd)
nd._SPORTS_KEYS.append(())  # 연합 스포츠·전국 RSS는 시험에서 읽지 않는다 (빈 목록)
clock = {"now": datetime(2026, 10, 23, 15, 15, tzinfo=KST)}
class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None): return clock["now"]
nd.datetime = FakeDT
seen_path = nd.SEEN_FILE
if os.path.exists(seen_path): os.remove(seen_path)
nd.send_guides = lambda *a, **k: None  # 안내 글은 이번 시험 대상 아님

def run(argv, label):
    state["sent"].clear(); state["list_calls"].clear()
    sys.argv = ["news_digest.py"] + argv
    print(f"\n===== {label} ({clock['now']:%H:%M}) =====")
    try: nd.main()
    except SystemExit as e: print("SystemExit:", e)
    return [t for c, t in state["sent"] if c == "111"], [t for c, t in state["sent"] if c == "222"]

fails = []
def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond: fails.append(msg)

# 현대차 연결 원문 있음, 현대차 별도는 연결이 있으니 빠져야 함, 기아는 원문 아직 없음, 모비스(별도만)는 표를 못 읽는 원문
state["docs"] = {"20261023000001": GOOD, "20261023000004": BAD}
a, b = run(["--mode", "단독"], "1) 15분 단독 확인")
txt = "\n".join(a)
print(txt)
check(all(c.get("pblntf_ty") == "I" and c.get("bgn_de") == "20261023" for c in state["list_calls"]), "15분 확인은 오늘·거래소공시(I)만 조회")
check("[현대자동차] 2026년 3분기 잠정실적 (연결)" in txt, "제목이 짧게 바뀜")
check("매출 46조 7,214억 (전년比 +8.7% · 전분기比 +3.4%)" in txt, "매출 + 회사가 적은 증감율")
check("영업이익 3조 5,120억 (전년比 +17.1% · 전분기比 -2.4%)" in txt, "증감율 빈칸이면 금액으로 계산")
check("순이익 -1,204억 (전년比 적자전환 · 전분기比 적자전환)" in txt, "적자전환 표시")
check("000002" not in txt, "연결을 낸 회사의 별도 공시는 뺌")
check("000003" not in txt, "원문 없는 기아는 이번엔 보류")
check("000004" in txt and "수치를 읽지 못했어요" in txt, "별도만 낸 모비스는 포함 + 못 읽음 안내")
check("자기주식" not in txt, "15분 확인에는 다른 공시 없음")
check(not b, "공시 안 켠 사람에게는 안 감")
seen = json.load(open(seen_path)); check("wait|20261023000003" in seen, "기아 대기 시작 시각 기록")

clock["now"] = datetime(2026, 10, 23, 16, 0, tzinfo=KST)
a, b = run([], "2) 정각 전체 발송 (기아 대기 45분째 → 30분 지나 링크만)")
txt = "\n".join(a); print(txt)
check("000001" not in txt and "000004" not in txt, "15분 때 보낸 잠정실적은 정각에 다시 안 보냄")
check("자기주식" in txt, "정각에는 다른 주요 공시도 감")
check("000003" in txt and "원문이 아직 DART에 없어" in txt, "30분 지나도 원문 없으면 링크만")

# 대기 30분 안에 원문이 올라오는 경우
state["docs"]["20261023000006"] = GOOD
FEED.append(row("20261023000006", "기아", "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"))
clock["now"] = datetime(2026, 10, 23, 16, 15, tzinfo=KST)
del state["docs"]["20261023000006"]
a, _ = run(["--mode", "단독"], "3) 새 정정공시, 원문 아직 없음")
check(not any("000006" in t for t in a), "원문 없으면 보류")
state["docs"]["20261023000006"] = GOOD
clock["now"] = datetime(2026, 10, 23, 16, 30, tzinfo=KST)
a, _ = run(["--mode", "단독"], "4) 15분 뒤 원문 올라옴")
txt = "\n".join(a); print(txt)
check("[기아] 2026년 3분기 잠정실적 (연결, 정정)" in txt and "매출 46조" in txt, "다음 회차에 수치와 함께 + 정정 표시")
a, _ = run(["--mode", "단독"], "5) 같은 회차 다시")
check(not a, "이미 보낸 건 다시 안 보냄")
print("\n실패:", fails if fails else "없음")
sys.exit(1 if fails else 0)
