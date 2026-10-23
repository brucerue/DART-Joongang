"""기사·공시를 미리 모아 두는 수집기: 앱이 빠르게 보여주고, 6개월치를 쌓아 '이슈 타임라인'에 쓴다.

- GitHub Actions(.github/workflows/collect_data.yml)가 매시간 실행한다.
- 모든 섹터의 통신·단독 기사와 주요 공시를 모아 data/archive.json(6개월치)과 data/recent.json(최근 7일)에 쓴다.
- 결과 파일은 워크플로가 저장소의 별도 'data' 브랜치에 올린다(앱이 올라간 main 브랜치는 건드리지 않아 앱이 다시 배포되지 않는다).
- 섹터·기업·언론사 같은 데이터는 news_digest.py(→ app.py)를 그대로 써서 알람과 같은 기준으로 모은다.
필요한 환경변수: DART_API_KEY (공시용, 없으면 기사만), NAVER_CLIENT_ID/NAVER_CLIENT_SECRET (선택)
사용: python collect_data.py [--out data] [--days 183]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import news_digest as nd

KST = nd.KST
KEEP_DAYS = 183  # 6개월
RECENT_DAYS = 7
LOOKBACK_HOURS = 48  # 매시간 돌지만, 실패한 회차가 있어도 메워지도록 이틀 전까지 훑는다
FMT = "%Y-%m-%dT%H:%M"


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def to_row(item, kind, sector):
    return {
        "t": item["title"], "u": item["url"], "p": item["publisher"], "k": kind,
        "d": item["dt"].astimezone(KST).strftime(FMT),
        "s": [sector], "g": list(item.get("tags") or []),
    }


def merge(store, rows):
    """store: {url: row}. 같은 기사가 여러 섹터에 걸리면 섹터·꼬리표를 합친다. 새로 들어온 건수를 돌려준다."""
    added = 0
    for r in rows:
        cur = store.get(r["u"])
        if cur is None:
            store[r["u"]] = r
            added += 1
        else:
            cur["s"] = list(dict.fromkeys(cur["s"] + r["s"]))
            cur["g"] = list(dict.fromkeys(cur["g"] + r["g"]))
    return added


def disclosure_rows(feed, sector):
    out = []
    for it in nd.disclosure_items(feed, sector, use_skip=False):
        no = it["key"]
        try:
            when = datetime.strptime(no[:8], "%Y%m%d").replace(tzinfo=KST)
        except ValueError:
            continue
        corp = it["title"].split("]")[0].lstrip("[")
        out.append({"t": it["title"], "u": it["url"], "p": "DART", "k": "공시", "d": when.strftime(FMT),
                    "s": [sector], "g": [corp] if corp else []})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--days", type=int, default=KEEP_DAYS)
    args = ap.parse_args()
    now = datetime.now(KST)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "archive.json")
    store = {r["u"]: r for r in load_json(path).get("items", [])}
    before = len(store)

    errors, added = [], 0
    for sector in nd.SECTORS:
        for mode in ("통신", "단독"):
            try:
                items = nd.collect(mode, sector, cutoff)
            except Exception as e:  # 한 곳이 실패해도 나머지는 계속
                errors.append(f"{sector}/{mode}: {e}")
                continue
            added += merge(store, [to_row(i, mode, sector) for i in items])
    if os.environ.get("DART_API_KEY", "").strip():
        try:
            feed = nd.fetch_dart_feed(now, wide=True)
            for sector in nd.SECTORS:
                added += merge(store, disclosure_rows(feed, sector))
        except Exception as e:
            errors.append(f"공시: {e}")
    else:
        print("DART_API_KEY가 없어 공시는 건너뜀", file=sys.stderr)

    keep_from = (now - timedelta(days=args.days)).strftime(FMT)
    rows = sorted((r for r in store.values() if r["d"] >= keep_from), key=lambda r: r["d"], reverse=True)
    stamp = now.strftime(FMT)
    recent_from = (now - timedelta(days=RECENT_DAYS)).strftime(FMT)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"updated": stamp, "items": rows}, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out, "recent.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": stamp, "items": [r for r in rows if r["d"] >= recent_from]}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"수집 완료: 새로 {added}건, 보관 {len(rows)}건(이전 {before}건), 실패 {len(errors)}곳")
    for e in errors:
        print("  실패:", e, file=sys.stderr)
    if errors and len(errors) >= len(nd.SECTORS) * 2:  # 전부 실패했으면 오류로 끝내 알림이 가게 한다
        sys.exit(1)


if __name__ == "__main__":
    main()
