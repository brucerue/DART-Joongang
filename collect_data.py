"""기사·공시를 미리 모아 두는 수집기: 앱이 빠르게 보여주고, 1년치를 쌓아 '타임라인'에 쓴다.

- GitHub Actions(.github/workflows/collect_data.yml)가 매시간 실행한다.
- 모든 섹터의 통신·단독 기사와 주요 공시를 모아 data/archive.json(1년치)과 data/recent.json(최근 7일)에 쓴다.
- '일반'(단독 표기 없는 기사 전부)은 양이 많아 1년치에는 넣지 않고, 섹터별 파일 data/general/<이름>.json에 최근 3일만 둔다(앱 산업 뉴스 '일반'이 바로 보여 줌).
- 결과 파일은 워크플로가 저장소의 별도 'data' 브랜치에 올린다(앱이 올라간 main 브랜치는 건드리지 않아 앱이 다시 배포되지 않는다).
- 섹터·기업·언론사 같은 데이터는 news_digest.py(→ app.py)를 그대로 써서 알람과 같은 기준으로 모은다.
필요한 환경변수: DART_API_KEY (공시용, 없으면 기사만), NAVER_CLIENT_ID/NAVER_CLIENT_SECRET (선택)
사용: python collect_data.py [--out data] [--days 365]
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta

import news_digest as nd

KST = nd.KST
KEEP_DAYS = 365  # 1년
RECENT_DAYS = 7
SECTORS = list(nd.A["NEWS_SECTORS"])  # 산업 뉴스의 모든 섹터 (앱이 저장분을 바로 보여 주므로 빠짐없이)
GENERAL_DAYS = 3  # '일반' 기사 보관 일수 (양이 많아 섹터별 파일에 최근 며칠만)
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


def general_file(sector):
    """섹터의 '일반' 기사 파일 이름 (한글·중간점 없는 이름: 앱의 general_file과 같은 규칙)"""
    return "general/" + hashlib.md5(sector.encode("utf-8")).hexdigest()[:10] + ".json"


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
    for sector in SECTORS:
        for mode in ("통신", "단독"):
            try:
                items = nd.collect(mode, sector, cutoff)
            except Exception as e:  # 한 곳이 실패해도 나머지는 계속
                errors.append(f"{sector}/{mode}: {e}")
                continue
            added += merge(store, [to_row(i, mode, sector) for i in items])
    general_from = (now - timedelta(days=GENERAL_DAYS)).strftime(FMT)
    general_n = 0
    for sector in SECTORS:  # '일반'은 섹터별 파일에 최근 GENERAL_DAYS일만
        gpath = os.path.join(args.out, general_file(sector))
        gstore = {r["u"]: r for r in load_json(gpath).get("items", []) if r.get("d", "") >= general_from}
        try:
            items = nd.collect("일반", sector, cutoff)
        except Exception as e:
            errors.append(f"{sector}/일반: {e}")
        else:
            general_n += merge(gstore, [to_row(i, "일반", sector) for i in items])
        os.makedirs(os.path.dirname(gpath), exist_ok=True)
        grows = sorted((r for r in gstore.values() if r["d"] >= general_from), key=lambda r: r["d"], reverse=True)
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump({"updated": now.strftime(FMT), "sector": sector, "items": grows}, f, ensure_ascii=False, separators=(",", ":"))
    if os.environ.get("DART_API_KEY", "").strip():
        try:
            feed = nd.fetch_dart_feed(now, wide=True)
            for sector in SECTORS:
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
    print(f"수집 완료: 새로 {added}건, 보관 {len(rows)}건(이전 {before}건), 일반 새로 {general_n}건, 실패 {len(errors)}곳")
    for e in errors:
        print("  실패:", e, file=sys.stderr)
    if errors and len(errors) >= len(SECTORS) * 3:  # 전부 실패했으면 오류로 끝내 알림이 가게 한다
        sys.exit(1)


if __name__ == "__main__":
    main()
