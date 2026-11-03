"""오늘의 현황 특징주: 묶은 섹터(차·배터리 등)는 낱개 섹터(자동차·배터리)별로 상승·하락 2개씩 (가짜 시세)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys, json, requests
os.environ["DART_API_KEY"] = "x"*40
sys.path.insert(0, REPO)
D = FIX
PR = json.load(open(f"{D}/fx_prices.json")); IN = json.load(open(f"{D}/fx_intraday.json"))
class R:
    status_code = 200; ok = True; text = "{}"
    def __init__(self, p=None): self._p = p if p is not None else {}
    def json(self): return self._p
    def raise_for_status(self): pass
def fake_get(url, *a, **k):
    if "prices.json" in url: return R(PR)
    if "intraday.json" in url: return R(IN)
    if url.endswith("/recent.json"): return R({"updated": "2026-11-03T10:00", "items": []})
    if "corp_codes" in url:
        r = requests.models.Response(); r.status_code = 200; r._content = b"corp_code,corp_name,stock_code\n"; return r
    return R()
requests.get = fake_get
class _S(requests.Session):
    def get(self, url, *a, **k): return fake_get(url)
requests.Session = _S
from streamlit.testing.v1 import AppTest
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
at = AppTest.from_file(REPO + "/app.py", default_timeout=300); at.run()
at.session_state["td_groups"] = ["차·배터리", "항공"]; at.run()
check(not at.exception, f"오류 없음 {[str(e.value)[:150] for e in at.exception]}")
md = "".join(m.value for m in at.markdown)
check("특징주" in md, "특징주 칸이 나옴")
check('<span class="mv-part">자동차</span>' in md and '<span class="mv-part">배터리</span>' in md, "차·배터리: 자동차·배터리 줄이 따로")
import re
blocks = re.findall(r'<div class="td-movers">(.*?)</div>', md)
car = [b for b in blocks if 'mv-part">자동차<' in b]
check(car and len(re.findall(r'class="mv (?:up|down)"', car[0])) <= 4, "자동차 줄: 상승·하락 최대 2개씩")
check(any('mv-part' not in b for b in blocks), "묶지 않은 섹터(항공)는 이름 없이 한 줄")
print("실패:", fails) if fails else print("모두 통과")
sys.exit(1 if fails else 0)
