"""장중 시세(5분봉)를 모아 둔다 (앱 '주가' 탭 시세판·특징주의 '장중' 기준용).

- GitHub Actions(.github/workflows/intraday.yml)가 평일 장중에 30분마다 실행한다.
- 추적 종목(prices.json의 종목)의 오늘 5분봉을 야후 파이낸스(yfinance)에서 통째로 받아 intraday.json에 쓴다.
  봉을 통째로 받으므로 실행 시각이 조금 밀려도 상관없고, 앱이 원하는 시각(10시·11시…)의 시세를 뽑아 쓴다.
- 결과는 워크플로가 저장소의 'intraday' 브랜치에 한 커밋으로 덮어써 올린다 (main은 건드리지 않는다).
- 야후 자료는 비공식이고 지연될 수 있다. 전일 종가는 KRX 자료(prices.json)에서 앱이 가져온다.
필요한 것: prices.json (--prices)
사용: python intraday.py --prices prices.json [--out intraday.json]
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
INTERVAL_MIN = 5
CHUNK = 80  # 한 번에 요청할 종목 수
CLOSE_FIX_AFTER = (15, 40)  # 이 시각(한국시간) 이후 실행에서는 일봉 종가로 마지막 구간(15:00~15:30)을 채운다
CLOSE_BAR = "1525"  # 채워 넣는 마지막 봉의 시작 시각 (5분봉이라 끝은 15:30)
MIN_COVERAGE = 0.3  # 이 비율보다 적은 종목만 받아지면 이상한 응답으로 보고 파일을 만들지 않는다


def yahoo_ticker(code, market):
    return f"{code}{'.KQ' if market == '코스닥' else '.KS'}"


def default_download(tickers):
    """야후에서 5분봉 하루치를 받는다 -> DataFrame (열은 (종목, 항목))"""
    import warnings

    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return yf.download(tickers, period="1d", interval=f"{INTERVAL_MIN}m", group_by="ticker", progress=False, auto_adjust=False, threads=True)


def default_daily_download(tickers):
    """야후에서 일봉 며칠치를 받는다 (마감 뒤 오늘 종가 확인용)"""
    import warnings

    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return yf.download(tickers, period="5d", interval="1d", group_by="ticker", progress=False, auto_adjust=False, threads=True)


def bars_of(df, ticker):
    """다운로드 결과에서 종목 하나의 봉 -> [(날짜 'YYYYMMDD', 시작시각 'HHMM', 종가, 거래량)]. 없으면 []"""
    try:
        sub = df[ticker] if ticker in set(df.columns.get_level_values(0)) else df
    except Exception:
        return []
    try:
        sub = sub[["Close", "Volume"]].dropna(subset=["Close"])
    except Exception:
        return []
    if sub.empty:
        return []
    idx = sub.index
    idx = idx.tz_convert("Asia/Seoul") if getattr(idx, "tz", None) is not None else idx
    out = []
    for ts, close, vol in zip(idx, sub["Close"], sub["Volume"]):
        out.append((ts.strftime("%Y%m%d"), ts.strftime("%H%M"), float(close), 0.0 if vol != vol else float(vol)))
    return out


def build(prices, download=default_download, now=None, daily_download=default_daily_download):
    """prices.json -> intraday.json 내용. 마감 뒤(15:40 이후) 실행이면 일봉 종가를 마지막 봉(15:25~15:30)으로 덧붙인다.
    야후 분봉은 15:00 이후가 비어 있어서, 그냥 두면 마지막 값이 실제 종가와 달라진다."""
    now = now or datetime.now(KST)
    tickers = {code: yahoo_ticker(code, s.get("m", "")) for code, s in prices["s"].items()}
    codes = list(tickers)
    got = {}
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        try:
            df = download([tickers[c] for c in chunk])
        except Exception as e:
            print(f"  {i // CHUNK + 1}번째 묶음 실패: {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            continue
        for c in chunk:
            bars = bars_of(df, tickers[c])
            if bars:
                got[c] = bars
        time.sleep(1)
    if len(got) < max(1, int(len(codes) * MIN_COVERAGE)):
        raise SystemExit(f"받아진 종목이 {len(got)}/{len(codes)}곳뿐이라 파일을 만들지 않았어요 (야후 응답을 확인하세요).")
    day = max(b[0] for bars in got.values() for b in bars)  # 가장 최근 거래일
    times = sorted({b[1] for bars in got.values() for b in bars if b[0] == day})
    pos = {t: i for i, t in enumerate(times)}
    series = {}
    for code, bars in got.items():
        c, v = [None] * len(times), [None] * len(times)
        for d, t, close, vol in bars:
            if d == day:
                c[pos[t]], v[pos[t]] = int(round(close)), int(vol)
        if any(x is not None for x in c):
            series[code] = {"c": c, "v": v}
    closed = False
    if (now.hour, now.minute) >= CLOSE_FIX_AFTER and CLOSE_BAR not in pos:
        closed = add_close_bar(times, series, day, tickers, daily_download)
    return {"date": day, "interval": INTERVAL_MIN, "fetched": now.strftime("%Y-%m-%dT%H:%M"), "t": times, "s": series, **({"closed": True} if closed else {})}


def add_close_bar(times, series, day, tickers, daily_download):
    """일봉의 오늘 종가·거래량으로 마지막 봉을 덧붙인다 (times·series를 그 자리에서 고침). 일봉을 못 받으면 False.
    거래량은 일봉 거래량에서 5분봉 거래량 합을 뺀 값(15:00 이후 분량, 음수면 0)."""
    codes = list(series)
    daily = {}
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        try:
            df = daily_download([tickers[c] for c in chunk])
        except Exception as e:
            print(f"  일봉 {i // CHUNK + 1}번째 묶음 실패: {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            continue
        for c in chunk:
            hit = [b for b in bars_of(df, tickers[c]) if b[0] == day]
            if hit:
                daily[c] = hit[-1]
        time.sleep(1)
    if len(daily) < max(1, int(len(codes) * MIN_COVERAGE)):
        print(f"  오늘 일봉이 {len(daily)}/{len(codes)}곳뿐이라 종가 보정을 건너뜀", file=sys.stderr)
        return False
    for t in series.values():
        t["c"].append(None)
        t["v"].append(None)
    times.append(CLOSE_BAR)
    for code, (_, _, close, vol) in daily.items():
        s = series[code]
        s["c"][-1] = int(round(close))
        s["v"][-1] = int(max(vol - sum(x for x in s["v"][:-1] if x), 0))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", required=True)
    ap.add_argument("--out", default="intraday.json")
    args = ap.parse_args()
    with open(args.prices, encoding="utf-8") as f:
        prices = json.load(f)
    out = build(prices)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{out['date']} 봉 {len(out['t'])}개({out['t'][0]}~{out['t'][-1]}), 종목 {len(out['s'])}/{len(prices['s'])}곳, {os.path.getsize(args.out) / 1e3:.0f}KB")


if __name__ == "__main__":
    sys.exit(main())
