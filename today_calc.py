"""'오늘의 현황' 탭의 계산(화면 없음): 모아 둔 기사·공시 줄(data 브랜치 recent.json)을 섹터·종류·기간으로 거르고, 시세표에서 등락 상·하위 종목을 고른다."""
import re
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


LEAD_SKIP = re.compile(r"^\s*\[(부고|인사|게시판|포토|사진|날씨|모집|알림)\]")


def _title_key(title):
    """같은 기사 판정용 제목: [단독]·공백·따옴표를 뺀다"""
    t = str(title or "").replace("[단독]", "").replace("[속보]", "")
    return "".join(ch for ch in t if ch.isalnum())


def overview(rows, sectors, since):
    """'한눈에 보기'(오늘의 현황 맨 위) 계산 -> {totals{단독,통신,공시}, cards[{sector,n,lead,lead_kind}], exclusives[...]}.
    totals·exclusives는 여러 섹터에 같이 걸린 기사(같은 주소, 또는 같은 매체·같은 제목)를 한 번만 센다.
    lead: 그 섹터의 가장 최근 단독, 없으면 가장 최근 통신."""
    totals, cards, excl = {}, [], {}
    for kind in ("단독", "통신", "공시"):
        seen, n = set(), 0
        for sec in sectors:
            for it in sector_items(rows, sec, kind, since):
                key = (it["publisher"], _title_key(it["title"]))
                if it["url"] in seen or key in seen:
                    if kind == "단독" and key in excl and sec not in excl[key]["sectors"]:
                        excl[key]["sectors"].append(sec)
                    continue
                seen.update((it["url"], key))
                n += 1
                if kind == "단독":
                    excl[key] = dict(it, sectors=[sec])
        totals[kind] = n
    for sec in sectors:
        ex, wire = sector_items(rows, sec, "단독", since), sector_items(rows, sec, "통신", since)
        plain = [w for w in wire if not LEAD_SKIP.match(w["title"])] or wire  # 부고·인사·게시판은 대표 기사로 쓰지 않음
        lead, lead_kind = (ex[0], "단독") if ex else ((plain[0], "통신") if plain else (None, ""))
        cards.append({"sector": sec, "n": len(ex) + len(wire), "ex": len(ex), "lead": lead, "lead_kind": lead_kind})
    exclusives = sorted(excl.values(), key=lambda x: x["dt"], reverse=True)
    return {"totals": totals, "cards": cards, "exclusives": exclusives}
