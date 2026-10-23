"""주가 탭의 계산 (화면 없이 숫자만). prices.json -> 기간 수익률·시총 비교·그룹 시총·이벤트 반응.

prices.json 모양 (price_history.py가 매일 만든다):
  {"updated": "2026-10-23T09:40", "dates": ["20251023", ...],
   "s": {"005930": {"n": "삼성전자", "m": "코스피", "c": [종가 원...], "r": [등락률 %...], "v": [거래대금 억원...], "k": [시총 억원(보통주+우선주)...]}},
   "g": {"삼성": {"rank": 1, "codes": ["005930", ...]}}}
  배열은 dates와 같은 길이이고, 그 날 값이 없으면 null.
"""
from itertools import combinations

import numpy as np
import pandas as pd

PERIODS = ["1개월", "3개월", "6개월", "연초 이후", "1년", "직접"]


def dates_of(data):
    return pd.to_datetime(data["dates"], format="%Y%m%d")


def frame(data, code):
    """종목 하나 -> 날짜별 표 (close 종가 원, rate 등락률 %, val 거래대금 억원, cap 시총 억원)"""
    s = data["s"][code]
    idx = dates_of(data)
    return pd.DataFrame({"close": s["c"], "rate": s["r"], "val": s["v"], "cap": s["k"]}, index=idx, dtype="float64")


def period_start(period, first, last):
    """기간 이름 -> 시작일 (자료 첫날보다 이르면 첫날)"""
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    if period == "1개월":
        start = last - pd.DateOffset(months=1)
    elif period == "3개월":
        start = last - pd.DateOffset(months=3)
    elif period == "6개월":
        start = last - pd.DateOffset(months=6)
    elif period == "1년":
        start = last - pd.DateOffset(years=1)
    elif period == "연초 이후":
        start = pd.Timestamp(year=last.year, month=1, day=1)
    else:
        start = first
    return max(start, first)


def cut(df, start, end=None):
    """시작일~끝일 구간 (끝일이 없으면 마지막 날까지)"""
    end = pd.Timestamp(end) if end is not None else df.index.max()
    return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= end)]


def chain_index(df):
    """구간 시작일 종가를 100으로 한 지수. KRX 등락률을 이어 붙여서, 액면분할 같은 종가 단절에 흔들리지 않는다 (배당은 미포함).
    구간 앞쪽에 값이 없는 날(상장 전)은 건너뛴다."""
    d = df.dropna(subset=["close"])
    if d.empty:
        return pd.Series(dtype="float64")
    r = (d["rate"].fillna(0) / 100).copy()
    r.iloc[0] = 0.0
    return (1 + r).cumprod() * 100


def period_return(df):
    """구간 수익률 (%) 또는 None"""
    idx = chain_index(df)
    if len(idx) < 2:
        return None
    return float(idx.iloc[-1] / 100 - 1) * 100


def crossings(a, b):
    """두 시계열(시총 등)의 순서가 바뀐 날 -> [(날짜, 'a' 또는 'b': 그날부터 앞선 쪽)]"""
    both = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
    if len(both) < 2:
        return []
    sign = np.sign((both["a"] - both["b"]).to_numpy())
    out, prev = [], 0.0
    for day, sg in zip(both.index, sign):
        if sg == 0:
            continue
        if prev != 0 and sg != prev:
            out.append((day, "a" if sg > 0 else "b"))
        prev = sg
    return out


def group_caps(data, codes):
    """그룹의 상장 계열사 시총 합계 (억원). 거래정지 등으로 하루 비는 값은 직전 값으로 메운다."""
    cols = {}
    for code in codes:
        if code in data["s"]:
            cols[code] = frame(data, code)["cap"].ffill()
    if not cols:
        return pd.Series(dtype="float64")
    return pd.DataFrame(cols).sum(axis=1, min_count=1)


def all_group_caps(data):
    """{그룹 이름: 시총 합계 Series} (상장 계열사가 있는 그룹만)"""
    out = {}
    for name, g in (data.get("g") or {}).items():
        s = group_caps(data, g.get("codes") or [])
        if not s.empty and s.notna().any():
            out[name] = s
    return out


def rank_flips(caps, names, start, end, limit=15):
    """names(상위 그룹들) 사이에서 시총 순서가 바뀐 날 -> [(날짜, 앞선 그룹, 밀린 그룹)] 최신순"""
    events = []
    for a, b in combinations(names, 2):
        sa, sb = caps[a].loc[start:end], caps[b].loc[start:end]
        for day, who in crossings(sa, sb):
            events.append((day, a, b) if who == "a" else (day, b, a))
    events.sort(key=lambda e: e[0], reverse=True)
    return events[:limit]


def event_reaction(df, day, n=3):
    """이벤트일의 주가 반응: (당일 등락률 %, 당일 포함 n거래일 누적 %). 휴장일이면 다음 거래일 기준. 계산할 수 없으면 None."""
    pos = df.index.searchsorted(pd.Timestamp(day).normalize())
    if pos >= len(df):
        return None, None
    day_rate = df["rate"].iloc[pos]
    r0 = None if pd.isna(day_rate) else float(day_rate)
    window = df["rate"].iloc[pos:pos + n]
    r3 = None
    if len(window) == n and window.notna().all():
        r3 = float((1 + window / 100).prod() - 1) * 100
    return r0, r3


# ---- 시세판 · 특징주 ----
SNAP_COLS = ["code", "name", "close", "rate", "val", "cap", "ratio"]
LIMIT_RATE = 29.0  # 상한가·하한가로 볼 등락률(%) 기준 (가격제한폭 ±30%)


def snapshot(data, ymd):
    """기준일(YYYYMMDD) 하루의 종목별 표: code, name, close(원), rate(%), val(거래대금 억원), cap(시총 억원),
    ratio(그날 거래대금 ÷ 직전 20거래일 평균, 직전 자료가 10일 미만이면 None). 그날 종가가 없는 종목은 뺀다."""
    dates = data["dates"]
    if ymd not in dates:
        return pd.DataFrame(columns=SNAP_COLS)
    pos = dates.index(ymd)
    rows = []
    for code, s in data["s"].items():
        close = s["c"][pos]
        if close is None:
            continue
        val = s["v"][pos]
        prev = [x for x in s["v"][max(0, pos - 20):pos] if x is not None]
        ratio = None
        if val is not None and len(prev) >= 10 and sum(prev) > 0:
            ratio = val / (sum(prev) / len(prev))
        rows.append({"code": code, "name": s["n"], "close": close, "rate": s["r"][pos], "val": val, "cap": s["k"][pos], "ratio": ratio})
    return pd.DataFrame(rows, columns=SNAP_COLS)


def sector_summary(snap, sector_codes):
    """{섹터: [종목코드]} -> 섹터별 요약 [{sector, n, avg(단순 평균 등락률), up, down, val(거래대금 합계 억원), best, worst}] (종목이 없는 섹터는 뺌)"""
    out = []
    for sector, codes in sector_codes.items():
        sub = snap[snap["code"].isin(codes)].dropna(subset=["rate"])
        if sub.empty:
            continue
        best, worst = sub.loc[sub["rate"].idxmax()], sub.loc[sub["rate"].idxmin()]
        out.append({"sector": sector, "n": int(len(sub)), "avg": float(sub["rate"].mean()), "up": int((sub["rate"] > 0).sum()),
                    "down": int((sub["rate"] < 0).sum()), "val": float(sub["val"].fillna(0).sum()),
                    "best": (best["name"], float(best["rate"])), "worst": (worst["name"], float(worst["rate"]))})
    return out


def pick_features(snap, rate_thr, ratio_thr):
    """특징주: 등락률 절댓값이 rate_thr(%) 이상이거나 거래대금이 직전 평균의 ratio_thr배 이상. 등락률 절댓값이 큰 순."""
    d = snap.dropna(subset=["rate"]).copy()
    mask = (d["rate"].abs() >= rate_thr) | (d["ratio"].fillna(0) >= ratio_thr)
    out = d[mask].copy()
    out["_abs"] = out["rate"].abs()
    return out.sort_values(["_abs", "ratio"], ascending=False).drop(columns="_abs").reset_index(drop=True)


def feature_flags(row, ratio_thr):
    """특징주 한 줄의 표시: 상한가·하한가·거래대금 급증"""
    flags = []
    if row["rate"] >= LIMIT_RATE:
        flags.append("상한가")
    elif row["rate"] <= -LIMIT_RATE:
        flags.append("하한가")
    if row["ratio"] is not None and not pd.isna(row["ratio"]) and row["ratio"] >= ratio_thr:
        flags.append("거래대금 급증")
    return " · ".join(flags)


# ---- 장중 (intraday.json: 오늘 5분봉) ----
# intraday.json: {"date": "YYYYMMDD", "interval": 5, "fetched": "2026-10-26T10:35", "t": ["0900", "0905", ...(봉 시작 시각)],
#                 "s": {"005930": {"c": [봉 종가...], "v": [봉 거래량(주)...]}}}
# 전일 종가·시총·평균 거래대금은 KRX 자료(data)에서 가져온다.
INTRADAY_MARKS = ["0930", "1000", "1100", "1200", "1300", "1400", "1500", "1530"]


def bar_end_text(t, interval=5):
    """봉 시작 시각 'HHMM' -> 끝 시각 'HH:MM' (0955 -> 10:00)"""
    total = int(t[:2]) * 60 + int(t[2:]) + int(interval)
    return f"{total // 60:02d}:{total % 60:02d}"


def intraday_ready(intra, data):
    """장중 자료가 KRX 시세 자료의 마지막 날보다 새 거래일 것이면 True (KRX에 그날이 들어오면 장중은 쓰지 않는다)"""
    try:
        return bool(intra and intra.get("t") and intra.get("s") and intra["date"] > data["dates"][-1])
    except (KeyError, TypeError, IndexError):
        return False


def intraday_marks(intra):
    """고를 수 있는 시각('HHMM') 목록: 마지막 봉이 끝난 시각 이하의 정시·반 (09:30, 10:00 … 15:30)"""
    last_end = bar_end_text(intra["t"][-1], intra.get("interval", 5))
    return [m for m in INTRADAY_MARKS if f"{m[:2]}:{m[2:]}" <= last_end]


def _prev_pos(intra, data):
    return max(i for i, d in enumerate(data["dates"]) if d < intra["date"])


def intraday_snapshot(intra, data, upto=None):
    """장중 시세를 시세판 표(snapshot과 같은 열)로: 전일 종가 대비 등락률, 거래대금은 5분봉 종가×거래량의 합(추정, 억원),
    시총은 전일 시총을 가격 변화율로 늘린 근사, ratio는 누적 거래대금 ÷ 직전 20거래일 평균.
    upto: None이면 최신 봉까지, 'HHMM'이면 그 시각 전에 시작한 봉까지. -> (표, 마지막 봉 끝 시각 'HH:MM' 또는 None)"""
    k = len(intra["t"]) if upto is None else sum(1 for t in intra["t"] if t < upto)
    if k == 0:
        return pd.DataFrame(columns=SNAP_COLS), None
    pos = _prev_pos(intra, data)
    rows = []
    for code, s in intra["s"].items():
        base = data["s"].get(code)
        if base is None:
            continue
        prev_close = base["c"][pos]
        closes, vols = s["c"][:k], s["v"][:k]
        price = next((c for c in reversed(closes) if c is not None), None)
        if not prev_close or price is None:
            continue
        val = sum(c * v for c, v in zip(closes, vols) if c is not None and v is not None) / 1e8
        prev_vals = [x for x in base["v"][max(0, pos - 19):pos + 1] if x is not None]
        ratio = val / (sum(prev_vals) / len(prev_vals)) if len(prev_vals) >= 10 and sum(prev_vals) > 0 else None
        prev_cap = base["k"][pos]
        rows.append({"code": code, "name": base["n"], "close": price, "rate": (price / prev_close - 1) * 100, "val": val,
                     "cap": None if prev_cap is None else prev_cap * price / prev_close, "ratio": ratio})
    return pd.DataFrame(rows, columns=SNAP_COLS), bar_end_text(intra["t"][k - 1], intra.get("interval", 5))


def intraday_sector_curve(intra, data, sector_codes):
    """섹터별 평균 등락률(전일 종가 대비 %)의 봉별 추이 -> 표 (행: 봉 끝 시각 Timestamp, 열: 섹터)"""
    pos = _prev_pos(intra, data)
    interval = intra.get("interval", 5)
    index = pd.to_datetime([intra["date"] + bar_end_text(t, interval).replace(":", "") for t in intra["t"]], format="%Y%m%d%H%M")
    rates = {}
    for code, s in intra["s"].items():
        base = data["s"].get(code)
        prev_close = base["c"][pos] if base else None
        if not prev_close:
            continue
        price = pd.Series(s["c"], index=index, dtype="float64").ffill()
        rates[code] = (price / prev_close - 1) * 100
    cols = {}
    for sector, codes in sector_codes.items():
        part = [rates[c] for c in codes if c in rates]
        if part:
            cols[sector] = pd.concat(part, axis=1).mean(axis=1)
    return pd.DataFrame(cols, index=index)


def intraday_hourly(curve, marks):
    """봉별 추이에서 고른 시각(marks, 'HHMM')만 뽑은 표 (행 이름 'HH:MM')"""
    stamp = curve.index.strftime("%H%M")
    pick = curve[[s in set(marks) for s in stamp]].copy()
    pick.index = pick.index.strftime("%H:%M")
    return pick
