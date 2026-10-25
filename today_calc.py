"""'오늘의 현황' 탭의 계산(화면 없음): 모아 둔 기사·공시 줄(data 브랜치 recent.json)을 섹터·종류·기간으로 거르고, 시세표에서 등락 상·하위 종목을 고른다."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
FMT = "%Y-%m-%dT%H:%M"
PERIODS = ["24시간", "오늘"]
HEAVY_PARTS_KEY = "중후장대"


def window_start(now, period):
    """기간 이름 -> 시작 시각(한국시간). '오늘'은 오늘 0시, '24시간'은 지금부터 24시간 전"""
    now = now.astimezone(KST)
    if period == "오늘":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(hours=24)


def sector_items(rows, sector, kind, since):
    """저장된 줄들 -> 그 섹터·종류(통신/단독/공시)의 since 이후 항목 [{title,url,publisher,dt,tags,kind}] (최신순, 같은 주소는 한 번)"""
    out, seen = [], set()
    for r in rows:
        try:
            if r.get("k") != kind or sector not in (r.get("s") or []):
                continue
            dt = datetime.strptime(r["d"], FMT).replace(tzinfo=KST)
            if dt < since or r["u"] in seen:
                continue
            seen.add(r["u"])
            out.append({"title": r["t"], "url": r["u"], "publisher": r.get("p") or "", "category": "통신사" if kind == "통신" else "언론",
                        "dt": dt, "tags": list(r.get("g") or []), "kind": kind})
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out


def movers(snap, codes, n=2):
    """시세표(snapshot)에서 codes 종목의 등락률 상위·하위 n개 -> (오른 종목들, 내린 종목들) [{name, close, rate}]. 등락률이 없는 종목은 뺀다"""
    sub = snap[snap["code"].isin(list(codes))].dropna(subset=["rate"])
    rows = [{"name": r["name"], "close": r["close"], "rate": float(r["rate"])} for _, r in sub.iterrows()]
    up = sorted([r for r in rows if r["rate"] > 0], key=lambda r: -r["rate"])[:n]
    down = sorted([r for r in rows if r["rate"] < 0], key=lambda r: r["rate"])[:n]
    return up, down


def counts(rows, sector, since):
    """섹터의 since 이후 통신·단독·공시 건수 {종류: 건수}"""
    return {k: len(sector_items(rows, sector, k, since)) for k in ("통신", "단독", "공시")}
