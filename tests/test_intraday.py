import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import json, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, REPO); os.chdir(REPO)
import pandas as pd, intraday as it
it.time.sleep = lambda s: None
it.build.__defaults__ = (it.default_download, None, lambda t: (_ for _ in ()).throw(RuntimeError("no net")))
ok = True
def check(c, m):
    global ok; print(("  ✅ " if c else "  ❌ ") + m); ok &= bool(c)
prices = {"s": {"005930": {"n": "삼성전자", "m": "코스피"}, "247540": {"n": "에코프로비엠", "m": "코스닥"}, "000660": {"n": "SK하이닉스", "m": "코스피"}, "999999": {"n": "빈종목", "m": "코스피"}}}
check(it.yahoo_ticker("005930", "코스피") == "005930.KS" and it.yahoo_ticker("247540", "코스닥") == "247540.KQ", "야후 종목 표기(.KS/.KQ)")
idx = pd.date_range("2026-10-23 09:00", periods=6, freq="5min", tz="Asia/Seoul")
def frame(tickers, drop=()):
    cols, data = [], {}
    for t in tickers:
        if t in drop: continue
        data[(t, "Close")] = [100.0 + i for i in range(6)]; data[(t, "Volume")] = [10.0 * (i + 1) for i in range(6)]
    return pd.DataFrame(data, index=idx)
calls = []
def fake(tickers):
    calls.append(list(tickers)); return frame(tickers, drop=("999999.KS",))
out = it.build(prices, fake, datetime(2026, 10, 23, 10, 5, tzinfo=it.KST))
check(out["date"] == "20261023" and out["t"][0] == "0900" and out["t"][-1] == "0925" and len(out["t"]) == 6, f"날짜·봉 시각 {out['date']} {out['t']}")
check(set(out["s"]) == {"005930", "247540", "000660"}, f"받은 종목만 저장 {sorted(out['s'])}")
check(out["s"]["005930"]["c"] == [100, 101, 102, 103, 104, 105] and out["s"]["005930"]["v"][-1] == 60, "종가·거래량 정수로 저장")
check(out["fetched"] == "2026-10-23T10:05" and out["interval"] == 5, "갱신 시각·간격")
# 2) 묶음(80개) 나눠 받기 + 일부 묶음 실패
big = {"s": {f"{i:06d}": {"n": f"x{i}", "m": "코스피"} for i in range(170)}}
n = {"k": 0}
def flaky(tickers):
    n["k"] += 1
    if n["k"] == 2: raise RuntimeError("야후 오류")
    return frame(tickers)
out2 = it.build(big, flaky, None)
check(n["k"] == 3 and len(out2["s"]) == 90, f"3묶음 중 1묶음 실패해도 나머지 저장: {len(out2['s'])}곳")
# 3) 거의 못 받으면 파일을 만들지 않음
try:
    it.build(big, lambda t: frame(t[:3]), datetime(2026, 10, 23, 11, 0, tzinfo=it.KST)); check(False, "너무 적게 받으면 중단해야 함")
except SystemExit as e:
    check("만들지 않았어요" in str(e), "너무 적게 받으면 중단: " + str(e)[:28])
# 3-1) 장 초반(09:15)에 하나도 못 받으면 실패가 아니라 '아직 없음' (야후 지연)
try:
    it.build(big, lambda t: frame([]), datetime(2026, 10, 27, 9, 15, tzinfo=it.KST)); check(False, "장 초반은 NotYet이어야 함")
except it.NotYet as e:
    check("다음 회차" in str(e), "09:15에 시세 없음 -> 아직 없음(실패 아님): " + str(e)[:30])
except SystemExit:
    check(False, "09:15에 시세 없음이 실패로 처리됨")
# 4) 날짜가 섞이면 가장 최근 날만
idx2 = list(pd.date_range("2026-10-22 15:20", periods=2, freq="5min", tz="Asia/Seoul")) + list(idx[:2])
df = pd.DataFrame({("005930.KS", "Close"): [1.0, 2.0, 3.0, 4.0], ("005930.KS", "Volume"): [1.0, 1.0, 1.0, 1.0]}, index=pd.DatetimeIndex(idx2))
o3 = it.build({"s": {"005930": {"n": "삼", "m": "코스피"}}}, lambda t: df, None)
check(o3["date"] == "20261023" and o3["t"] == ["0900", "0905"] and o3["s"]["005930"]["c"] == [3, 4], f"가장 최근 거래일 봉만: {o3['t']} {o3['s']['005930']['c']}")
# 5) 종목 하나만 받을 때(열이 한 단계)
df1 = pd.DataFrame({"Close": [10.0, 11.0], "Volume": [5.0, float('nan')]}, index=idx[:2])
b = it.bars_of(df1, "005930.KS")
check(len(b) == 2 and b[1][3] == 0.0, "열이 한 단계여도 읽음, 거래량 NaN은 0")
json.dump(out, open(FIX + "/intraday_sample.json", "w"))
print("결과:", "통과" if ok else "실패"); sys.exit(0 if ok else 1)
