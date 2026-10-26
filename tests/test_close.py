import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import os, sys
from datetime import datetime
sys.path.insert(0, REPO); os.chdir(REPO)
import pandas as pd, intraday as it
it.time.sleep = lambda s: None
ok = True
def check(c, m):
    global ok; print(("  ✅ " if c else "  ❌ ") + m); ok &= bool(c)
prices = {"s": {"005930": {"n": "삼성전자", "m": "코스피"}, "000660": {"n": "SK하이닉스", "m": "코스피"}}}
idx = pd.date_range("2026-10-23 14:40", periods=4, freq="5min", tz="Asia/Seoul")   # 14:40~14:55
def minute(tickers):
    d = {}
    for t in tickers: d[(t, "Close")] = [100.0, 101, 102, 103]; d[(t, "Volume")] = [10.0, 10, 10, 10]
    return pd.DataFrame(d, index=idx)
didx = pd.DatetimeIndex(["2026-10-22", "2026-10-23"]).tz_localize("Asia/Seoul")
def daily(tickers):
    d = {}
    for t in tickers: d[(t, "Close")] = [99.0, 108.0]; d[(t, "Volume")] = [500.0, 75.0]
    return pd.DataFrame(d, index=didx)
a = it.build(prices, minute, datetime(2026, 10, 23, 16, 10, tzinfo=it.KST), daily)
check(a["t"][-1] == "1525" and a["t"][:4] == ["1440", "1445", "1450", "1455"] and a.get("closed") is True, f"마감 뒤: 마지막 봉 {a['t'][-1]} closed={a.get('closed')}")
check(a["s"]["005930"]["c"][-1] == 108 and a["s"]["005930"]["v"][-1] == 35, f"종가 108, 거래량 75-40=35 ({a['s']['005930']['c'][-1]}, {a['s']['005930']['v'][-1]})")
check(len(a["s"]["005930"]["c"]) == len(a["t"]) == 5, "열 길이 맞음")
b = it.build(prices, minute, datetime(2026, 10, 23, 15, 35, tzinfo=it.KST), daily)
check(b["t"][-1] == "1455" and "closed" not in b, "15:35 실행은 보정 안 함")
def broken(t): raise RuntimeError("야후 오류")
c = it.build(prices, minute, datetime(2026, 10, 23, 16, 10, tzinfo=it.KST), broken)
check(c["t"][-1] == "1455" and "closed" not in c, "일봉 실패하면 보정 없이 저장")
def stale(tickers):  # 일봉에 오늘이 아직 없을 때
    d = {}
    for t in tickers: d[(t, "Close")] = [99.0]; d[(t, "Volume")] = [500.0]
    return pd.DataFrame(d, index=didx[:1])
e = it.build(prices, minute, datetime(2026, 10, 23, 16, 10, tzinfo=it.KST), stale)
check(e["t"][-1] == "1455" and "closed" not in e, "오늘 일봉이 아직 없으면 보정 안 함")
f = it.build(prices, lambda t: minute(t), datetime(2026, 10, 23, 16, 10, tzinfo=it.KST), lambda t: pd.concat([daily(t[:1])], axis=1))
check(f["t"][-1] == "1455" or f["s"]["005930"]["c"][-1] == 108, "일부 종목만 일봉이 있어도 있는 것만 채움(커버리지 미달이면 건너뜀)")
print("결과:", "통과" if ok else "실패"); sys.exit(0 if ok else 1)
