import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, types, json, requests
os.environ["DART_API_KEY"] = "x"*40
import pandas as pd
sys.path.insert(0, REPO)
ROWS = [{"corp_code": "1", "corp_name": "SK하이닉스", "corp_cls": "Y", "report_nm": "풍문또는보도에대한해명(미확정)", "rcept_no": "20261026000001", "rcept_dt": "20261026", "flr_nm": "SK하이닉스"},
        {"corp_code": "2", "corp_name": "LS에코", "corp_cls": "Y", "report_nm": "유상증자결정(자회사의 주요경영사항)", "rcept_no": "20261026000002", "rcept_dt": "20261026", "flr_nm": "LS에코"},
        {"corp_code": "3", "corp_name": "우진", "corp_cls": "Y", "report_nm": "최대주주등소유주식변동신고서", "rcept_no": "20261026000003", "rcept_dt": "20261026", "flr_nm": "우진"},
        {"corp_code": "4", "corp_name": "한화", "corp_cls": "Y", "report_nm": "기업설명회(IR)개최(안내공시)", "rcept_no": "20261026000004", "rcept_dt": "20261026", "flr_nm": "한화"},
        {"corp_code": "5", "corp_name": "기타", "corp_cls": "Y", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서", "rcept_no": "20261026000005", "rcept_dt": "20261026", "flr_nm": "기타"}]
def fake_get(url, *a, **k):
    r = requests.models.Response(); r.status_code = 200
    if "corp_codes" in url: r._content = b"corp_code,corp_name,stock_code\n"
    elif "list.json" in url: r._content = json.dumps({"status": "000", "total_count": len(ROWS), "total_page": 1, "list": ROWS}).encode()
    else: r._content = b"{}"
    return r
requests.get = fake_get
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url, *a, **k)
requests.Session = _S
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.session_state["disc_mode"] = "오늘의 공시"; at.run()
bs = [b for b in at.button if "공시 불러오기" in str(b.label)]
bs[0].click().run()
check(not at.exception, f"예외 없음 {at.exception}")
r = [x for x in at.radio if str(x.key).startswith("feed_view_") or "_view_" in str(x.key)]
r = r[0] if r else None
labels = list(r.options) if r else []
check(len(labels) == 8 and labels[0].startswith("주요 전체") and labels[-1].startswith("모든 공시"), f"보기 버튼 8개 {labels}")
print("  ", labels)
check("풍문·리스크 (1)" in labels and "자본·M&A (1)" in labels and "지배구조 (2)" in labels and "실적·IR (1)" in labels, "건수 계산")
check(":gray[수주·계약 (0)]" in labels and ":gray[정기보고서 (0)]" in labels, "0건은 회색")
check("주요 전체 (5)" in labels and "모든 공시 (5)" in labels, f"주요 전체 = 6묶음 합")
print("실패", fails)
