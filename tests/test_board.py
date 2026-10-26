import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import sys; sys.path.insert(0, REPO)
import pandas as pd, stock_calc as sc
ok = True
def check(c, m):
    global ok; print(("  ✅ " if c else "  ❌ ") + m); ok &= bool(c)
dates = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-03-02", periods=30)]
n = len(dates)
def mk(name, rate_last, val_hist, val_last, close=1000, cap=100, short=False):
    v = ([None] * 3 if short else []) + [val_hist] * (n - 1 - (3 if short else 0))
    v = v[-(n - 1):] if not short else [None] * (n - 1 - len(v)) + v
    return {"n": name, "m": "코스피", "c": [close] * n, "r": [0.0] * (n - 1) + [rate_last], "v": [val_hist] * (n - 1) + [val_last], "k": [cap] * n}
data = {"dates": dates, "s": {"A": mk("에이", 6.0, 100, 150), "B": mk("비", -2.0, 100, 400), "C": mk("씨", 29.9, 100, 100), "D": mk("디", -30.0, 100, 100),
        "E": mk("이", 1.0, 100, 100), "F": mk("에프", 0.5, 0, 50), "G": mk("지", None, 100, 100)}}
data["s"]["G"]["r"][-1] = None
data["s"]["H"] = {"n": "상장직후", "m": "코스피", "c": [None] * (n - 5) + [1000] * 5, "r": [None] * (n - 5) + [0.0] * 5, "v": [None] * (n - 5) + [10] * 5, "k": [None] * (n - 5) + [50] * 5}
last = dates[-1]
snap = sc.snapshot(data, last)
check(set(snap["code"]) == set("ABCDEFGH"), f"그날 종가 있는 종목 모두 포함 {sorted(snap['code'])}")
r = snap.set_index("code")
check(abs(r.loc["B", "ratio"] - 4.0) < 1e-9, f"거래대금 배수: 400 ÷ 평균 100 = {r.loc['B','ratio']}")
check(pd.isna(r.loc["F", "ratio"]) or r.loc["F", "ratio"] is None, "평균이 0이면 배수 없음")
check(pd.isna(r.loc["H", "ratio"]), "직전 자료가 10일 미만이면 배수 없음")
check(sc.snapshot(data, "20990101").empty, "없는 날짜는 빈 표")
d_old = sc.snapshot(data, dates[5]); check(len(d_old) == 7, "과거 날짜: 상장 전 종목(상장직후) 제외")
# 특징주
ft = sc.pick_features(snap, 5.0, 3.0)
print("  특징주:", list(zip(ft["code"], ft["rate"])))
check(list(ft["code"]) == ["D", "C", "A", "B"], "등락률 ±5%↑ 또는 거래대금 3배↑, 등락률 절댓값 큰 순")
check(sc.pick_features(snap, 40.0, 99.0).empty, "기준이 높으면 없음")
fl = {row["code"]: sc.feature_flags(row, 3.0) for _, row in ft.iterrows()}
check(fl["C"] == "상한가" and fl["D"] == "하한가" and fl["B"] == "거래대금 급증" and fl["A"] == "", f"표시: {fl}")
# 섹터 요약
ss = sc.sector_summary(snap, {"가": ["A", "B", "E"], "나": ["C", "D"], "다": ["ZZ"]})
check([s["sector"] for s in ss] == ["가", "나"], "종목이 없는 섹터는 뺌")
g = ss[0]; check(abs(g["avg"] - (6 - 2 + 1) / 3) < 1e-9 and g["up"] == 2 and g["down"] == 1 and g["best"][0] == "에이" and g["worst"][0] == "비", f"섹터 요약: 평균 {g['avg']:.2f} 오름 {g['up']} 내림 {g['down']} 최고 {g['best']} 최저 {g['worst']}")
check(g["val"] == 150 + 400 + 100, f"거래대금 합계 {g['val']}")
print("결과:", "통과" if ok else "실패"); sys.exit(0 if ok else 1)
