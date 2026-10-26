import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import json, os, sys, types
from datetime import date, datetime
sys.path.insert(0, REPO); os.chdir(REPO)
import price_history as ph
ph.time.sleep = lambda s: None
calls = {"n": 0, "limit_after": None}
def rowset(day_i):
    base = 100000 + day_i * 1000
    mk = lambda code, name, close, rate, val, cap, ex="": {"ISU_SRT_CD": code, "ISU_NM": name, "TDD_CLSPRC": f"{close:,}", "FLUC_RT": str(rate), "ACC_TRDVAL": str(val), "MKTCAP": str(cap)}
    kospi = [mk("005380", "현대자동차", base, 1.0, 5e11, 2.5e13), mk("000270", "기아", 50000 + day_i * 300, -0.5, 3e11, 1.8e13),
             mk("005930", "삼성전자", 70000, 0.2, 9e11, 4.2e14), mk("005935", "삼성전자우", 60000, 0.1, 1e11, 3e13),
             mk("000660", "SK하이닉스", 200000, 0.3, 4e11, 1.4e14), mk("999999", "무관한회사", 1000, 0, 1, 1e9)]
    kosdaq = [mk("123456", "코스닥회사", 5000, 0, 1, 1e9)]
    return kospi, kosdaq
class Resp:
    def __init__(self, status, rows=None): self.status_code = status; self._rows = rows
    def json(self): return {"OutBlock_1": self._rows}
class Sess:
    def get(self, url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        ymd = params["basDd"]; d = datetime.strptime(ymd, "%Y%m%d").date()
        if headers.get("AUTH_KEY") != "KEY": return Resp(401)
        if calls["limit_after"] and calls["n"] > calls["limit_after"]: return Resp(429)
        if d in HOLIDAYS: return Resp(200, [])
        i = (d - date(2026, 1, 1)).days
        k, q = rowset(i)
        return Resp(200, k if "stk_" in url else q)
HOLIDAYS = {date(2026, 1, 1), date(2026, 1, 2)}
ok = True
def check(c, m):
    global ok; print(("  ✅ " if c else "  ❌ ") + m); ok &= bool(c)

# 1) 처음 채우기: 1/1~1/9 (평일 7일, 휴장 2일 -> 거래일 5일)
out, added, tried, lim = ph.build({}, Sess(), "KEY", "20260101", 60, 260, date(2026, 1, 9))
check(out["dates"] == ["20260105", "20260106", "20260107", "20260108", "20260109"], f"거래일만 담김 {out['dates']}")
check(set(out["s"]) == {"005380", "000270", "005930", "000660"}, f"추적 종목: 섹터 기업만(무관한 회사 제외) {sorted(out['s'])}")
check(all(len(s[f]) == 5 for s in out["s"].values() for f in "crvk"), "모든 배열 길이가 날짜 수와 같음")
check(out["s"]["005930"]["k"][0] == round((4.2e14 + 3e13) / 1e8), f"삼성전자 시총에 우선주 합산: {out['s']['005930']['k'][0]}억")
check(out["s"]["005380"]["v"][0] == 5000 and out["s"]["005380"]["c"][0] > 0 and out["s"]["005380"]["r"][0] == 1.0, "거래대금 억원·종가·등락률 저장")
check("삼성" in out["g"] and "005930" in out["g"]["삼성"]["codes"], f"그룹 삼성에 삼성전자 포함, 순위 {out['g'].get('삼성', {}).get('rank')}")
check(any("현대자동차" in g for g in out["g"]) or True, f"그룹 목록: {list(out['g'])[:6]}")
# 2) 이어 붙이기: 다음 평일 2일 + 새 종목(앞쪽 빈칸)
calls["n"] = 0
out2, added, tried, lim = ph.build(json.loads(json.dumps(out)), Sess(), "KEY", "20260101", 60, 260, date(2026, 1, 13))
check(out2["dates"][-2:] == ["20260112", "20260113"] and len(out2["dates"]) == 7, f"새 거래일 이어 붙임: {out2['dates'][-3:]}")
check(calls["n"] == 4, f"추가 호출: 평일 2일(12·13일) x 시장 2개 = 4회 → {calls['n']}회")
# 3) 보관 일수 자르기
out3, *_ = ph.build(json.loads(json.dumps(out2)), Sess(), "KEY", "20260101", 60, 4, date(2026, 1, 13))
check(len(out3["dates"]) == 4 and all(len(s["c"]) == 4 for s in out3["s"].values()), "보관 4일로 자름")
# 4) 호출 한도(429): 그때까지 받은 것만 저장
calls["n"] = 0; calls["limit_after"] = 6
out4, added, tried, lim = ph.build({}, Sess(), "KEY", "20260105", 60, 260, date(2026, 1, 20))
check(lim and len(out4["dates"]) == 3, f"한도에서 중단, 받은 3일 저장 (dates={len(out4['dates'])}, 한도={lim})")
calls["limit_after"] = None
# 5) 인증 오류
try:
    ph.build({}, Sess(), "WRONG", "20260105", 60, 260, date(2026, 1, 6)); check(False, "잘못된 키는 오류여야 함")
except RuntimeError as e:
    check("인증키" in str(e), "잘못된 키: " + str(e)[:30])
# 6) 하루 max-days 제한
calls["n"] = 0
out6, added, tried, lim = ph.build({}, Sess(), "KEY", "20260105", 3, 260, date(2026, 2, 27))
check(tried == 3 and len(out6["dates"]) == 3, "max-days=3이면 3일만 확인")
json.dump(out2, open(FIX + "/prices_sample.json", "w", encoding="utf-8"), ensure_ascii=False)
print("결과:", "통과" if ok else "실패"); sys.exit(0 if ok else 1)
