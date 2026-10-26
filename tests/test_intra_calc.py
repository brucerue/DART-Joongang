import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import sys; sys.path.insert(0, REPO)
import pandas as pd, stock_calc as sc
ok = True
def check(c, m):
    global ok; print(("  ✅ " if c else "  ❌ ") + m); ok &= bool(c)
dates = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-09-01", periods=30)]   # 마지막 날 = 20261012? 계산: 30영업일
last = dates[-1]
def base(name, prev_close, val=100, cap=1000):
    n = len(dates); return {"n": name, "m": "코스피", "c": [prev_close] * n, "r": [0.0] * n, "v": [val] * n, "k": [cap] * n}
data = {"dates": dates, "s": {"A": base("에이", 1000), "B": base("비", 2000), "C": base("씨", 500)}}
today = (pd.Timestamp(last) + pd.offsets.BDay(1)).strftime("%Y%m%d")
t = ["0900", "0905", "0910", "0915", "0920", "0925", "0930", "0935", "0940", "0945", "0950", "0955", "1000", "1005"]
n = len(t)
intra = {"date": today, "interval": 5, "fetched": "x", "t": t, "s": {
    "A": {"c": [1000 + 10 * i for i in range(n)], "v": [100000] * n},          # 1000 -> 1130
    "B": {"c": [None, None] + [1900] * (n - 2), "v": [None, None] + [10] * (n - 2)},  # 2봉 늦게 시작, -5%
    "C": {"c": [600] * n, "v": [1_000_000] * n},                                # +20%, 거래대금 큼
    "Z": {"c": [1] * n, "v": [1] * n}}}                                       # 자료에 없는 종목
check(sc.intraday_ready(intra, data), "장중 날짜가 KRX 마지막 날보다 새로우면 사용")
check(not sc.intraday_ready({**intra, "date": last}, data) and not sc.intraday_ready(None, data) and not sc.intraday_ready({**intra, "t": []}, data), "KRX에 이미 있는 날·빈 자료는 사용 안 함")
check(sc.bar_end_text("0955") == "10:00" and sc.bar_end_text("1525") == "15:30" and sc.bar_end_text("0900") == "09:05", "봉 끝 시각")
check(sc.intraday_marks(intra) == ["0930", "1000"], f"고를 수 있는 시각(마지막 봉 10:10까지): {sc.intraday_marks(intra)}")
snap, last_end = sc.intraday_snapshot(intra, data)
r = snap.set_index("code")
check(last_end == "10:10" and set(r.index) == {"A", "B", "C"}, f"최신: 마지막 봉 {last_end}, 종목 {sorted(r.index)} (자료 없는 Z 제외)")
check(abs(r.loc["A", "rate"] - 13.0) < 1e-9 and abs(r.loc["B", "rate"] - (-5.0)) < 1e-9 and abs(r.loc["C", "rate"] - 20.0) < 1e-9, f"전일 종가 대비 등락률: {r['rate'].round(2).to_dict()}")
val_c = 600 * 1_000_000 * n / 1e8
check(abs(r.loc["C", "val"] - val_c) < 1e-6, f"누적 거래대금(추정) = 종가×거래량 합 = {r.loc['C','val']:.0f}억")
check(abs(r.loc["C", "ratio"] - val_c / 100) < 1e-6, f"거래대금 배수 = 누적 ÷ 20일 평균(100억) = {r.loc['C','ratio']:.1f}")
check(abs(r.loc["A", "cap"] - 1000 * 1130 / 1000) < 1e-9, "시총 = 전일 시총 × 가격 변화율")
s10, e10 = sc.intraday_snapshot(intra, data, "1000")
check(e10 == "10:00" and abs(s10.set_index("code").loc["A", "rate"] - 11.0) < 1e-9, f"10:00 기준: 마지막 봉 {e10}, A {s10.set_index('code').loc['A','rate']:.1f}%")
s0, e0 = sc.intraday_snapshot(intra, data, "0900")
check(s0.empty and e0 is None, "첫 봉 이전 시각이면 빈 표")
# 섹터 추이
curve = sc.intraday_sector_curve(intra, data, {"가": ["A", "B"], "나": ["C"], "다": ["Q"]})
check(list(curve.columns) == ["가", "나"] and len(curve) == n, f"섹터 추이 열 {list(curve.columns)}")
check(abs(curve["나"].iloc[-1] - 20.0) < 1e-9 and abs(curve["가"].iloc[-1] - (13.0 - 5.0) / 2) < 1e-9, f"섹터 평균: 가 {curve['가'].iloc[-1]:.1f}%, 나 {curve['나'].iloc[-1]:.1f}%")
check(abs(curve["가"].iloc[0] - 0.0) < 1e-9 and curve.index[0].strftime("%H:%M") == "09:05", "첫 봉(자료 있는 종목만) 평균과 시각(봉 끝)")
hr = sc.intraday_hourly(curve, ["0930", "1000"])
check(list(hr.index) == ["09:30", "10:00"] and abs(hr["나"].iloc[0] - 20.0) < 1e-9, f"시각별 표: {list(hr.index)}")
print("결과:", "통과" if ok else "실패"); sys.exit(0 if ok else 1)
