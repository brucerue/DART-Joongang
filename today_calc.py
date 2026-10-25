"""'오늘의 현황' 탭의 계산(화면 없음): 모아 둔 기사·공시 줄(data 브랜치 recent.json)을 섹터·종류·기간으로 거르고, 시세표에서 등락 상·하위 종목을 고른다."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
FMT = "%Y-%m-%dT%H:%M"
PERIODS = ["24시간", "오늘"]
MISS_PERIODS = ["24시간", "오늘", "3일", "7일"]  # 물먹음 점검 기간
HEAVY_PARTS_KEY = "중후장대"


def window_start(now, period):
    """기간 이름 -> 시작 시각(한국시간). '오늘'은 오늘 0시, '24시간'은 지금부터 24시간 전"""
    now = now.astimezone(KST)
    if period == "오늘":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period in ("3일", "7일"):
        return now - timedelta(days=int(period[0]))
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


OWN_PUBLISHER = "중앙일보"


def split_miss(clusters, own=OWN_PUBLISHER):
    """묶음(cluster_news 결과) -> (우리도 쓴 묶음, 타사 단독만 있는 묶음). 묶음 안에 중앙일보 기사가 하나라도 있으면 '우리도 씀'.
    (모아 둔 자료에는 제목에 [단독]이 붙은 기사만 있어서, 중앙일보가 단독 표기 없이 쓴 기사는 알 수 없다 -> 두 번째 묶음은 '물먹음 후보')"""
    ours, theirs = [], []
    for c in clusters:
        pubs = [c["rep"]["publisher"]] + [o["publisher"] for o in c["others"]]
        (ours if own in pubs else theirs).append(c)
    return ours, theirs


def outlet_counts(items):
    """기사 항목들 -> [(매체, 건수)] 건수 많은 순"""
    cnt = {}
    for it in items:
        cnt[it["publisher"]] = cnt.get(it["publisher"], 0) + 1
    return sorted(cnt.items(), key=lambda x: (-x[1], x[0]))
