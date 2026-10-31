"""기사·공시를 미리 모아 두는 수집기: 앱이 빠르게 보여주고, 1년치를 쌓아 '타임라인'에 쓴다.

- GitHub Actions(.github/workflows/collect_data.yml)가 매시간 실행한다.
- 모든 섹터의 통신·단독 기사와 주요 공시를 모아 data/archive.json(1년치)과 data/recent.json(최근 7일)에 쓴다.
- '일반'(단독 표기 없는 기사 전부)은 양이 많아 1년치에는 넣지 않고, 섹터별 파일 data/general/<이름>.json에 최근 3일만 둔다(앱 산업 뉴스 '일반'이 바로 보여 줌).
- 결과 파일은 워크플로가 저장소의 별도 'data' 브랜치에 올린다(앱이 올라간 main 브랜치는 건드리지 않아 앱이 다시 배포되지 않는다).
- 섹터·기업·언론사 같은 데이터는 news_digest.py(→ app.py)를 그대로 써서 알람과 같은 기준으로 모은다.
필요한 환경변수: DART_API_KEY (공시용, 없으면 기사만), NAVER_CLIENT_ID/NAVER_CLIENT_SECRET (선택)
사용: python collect_data.py [--out data] [--days 365] [--modes 통신,단독,일반] [--no-disc] [--dump fresh.json]
  텔레그램 발송(news_digest.yml)이 정해진 회차마다 먼저 이걸 돌려 저장하고(--dump), 같은 결과로 알람을 보낸다(검색을 두 번 하지 않게).
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta

import requests

import gnews_calc as gc
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


GEMINI_DEFAULT_MODEL = "gemini-3.6-flash"  # app.py의 GEMINI_MODEL 기본값과 같게 (GitHub Secrets/환경변수 GEMINI_MODEL로 바꿀 수 있음)
GEMINI_LITE_MODEL = "gemini-3.5-flash-lite"


def collect_global(out_dir, now, errors):
    """외신을 모아 섹터별 파일(global/·korea/)에 최근 7일 쌓는다. 기업별 = 해외 섹터 기업의 별칭이 제목에 든 기사,
    한국 관련 = 한국 낱말 + 섹터 낱말로 본문까지 찾은 기사. 새 제목은 Gemini로 한글 제목을 붙인다(GEMINI_API_KEY 없으면 건너뜀 — 앱이 볼 때 옮김).
    -> (새로 들어온 건수, 새로 옮긴 제목 수)"""
    from concurrent.futures import ThreadPoolExecutor
    groups = gc.global_sectors(nd.A["OVERSEAS_GROUPS"], nd.A["FOREIGN_ENGLISH"], nd.A["OVERSEAS_NAMES"])
    doms = list(gc.GNEWS_OUTLETS.values())
    jobs = [("global", g, t, c) for g, terms in groups.items() for t in terms for c in gc.domain_chunks(doms)]
    jobs += [("korea", sec, None, c) for sec in gc.KOREA_TOPICS for c in gc.domain_chunks(doms)]

    def one(job):  # (종류, 섹터, 기업, 언론사 묶음) 하나 — 언론사가 많아 나눠 검색
        kind, sec, term, chunk = job
        core = gc.company_core(term) if kind == "global" else gc.korea_core(sec)
        try:
            r = requests.get(gc.GNEWS_URL, params={"q": gc.query(core, 2, chunk), "hl": "en-US", "gl": "US", "ceid": "US:en"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            items = gc.parse_rss(r.content, chunk, 2, now, gc.aliases(term) if kind == "global" else None)
        except Exception as e:
            errors.append(f"외신 {sec}/{term or '한국'}: {type(e).__name__}")
            return job, None
        if kind == "korea":
            items = [i for i in items if not gc.GN_JUNK_RE.search(i["title"])]
        return job, items

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, jobs))
    keep_from = (now - timedelta(days=gc.GLOBAL_KEEP_DAYS)).strftime(FMT)
    files = {}  # 파일 경로 -> (kind, 섹터, {주소: 줄})
    for kind, sec in [("global", g) for g in groups] + [("korea", s2) for s2 in gc.KOREA_TOPICS]:
        path = os.path.join(out_dir, gc.file_name(kind, sec))
        files[path] = (kind, sec, {r["u"]: r for r in load_json(path).get("items", [])
                                   if r.get("d", "") >= keep_from and not gc.is_junk(r.get("t", ""), r.get("p", ""))})
    added = 0
    for (kind, sec, term, _), items in results:
        store = files[os.path.join(out_dir, gc.file_name(kind, sec))][2]
        for it in items or []:
            row = store.get(it["url"])
            if row is None:
                row = {"t": it["title"], "u": it["url"], "p": it["publisher"], "d": it["dt"].astimezone(KST).strftime(FMT), "ko": ""}
                store[it["url"]] = row
                added += 1
            if term:
                row["c"] = list(dict.fromkeys((row.get("c") or []) + [term]))  # 어느 기업 기사인지 (기업별)
    need = list(dict.fromkeys(r["t"] for _, _, st in files.values() for r in st.values() if not r.get("ko")))
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    ko = gc.gemini_translate(need, key, os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_DEFAULT_MODEL, requests.post) if need and key else {}
    rest = [t for t in need if t not in ko]
    if rest and key:  # 기본 모델이 붐비거나 없으면 가벼운 모델로 한 번 더
        ko.update(gc.gemini_translate(rest, key, GEMINI_LITE_MODEL, requests.post))
    for path, (kind, sec, store) in files.items():
        for r in store.values():
            if not r.get("ko") and ko.get(r["t"]):
                r["ko"] = ko[r["t"]]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = sorted(store.values(), key=lambda r: r["d"], reverse=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"updated": now.strftime(FMT), "kind": kind, "sector": sec, "items": rows}, f, ensure_ascii=False, separators=(",", ":"))
    return added, len(ko)


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
    ap.add_argument("--modes", default="통신,단독,일반,외신", help="모을 기사 종류(쉼표). 15분 회차는 단독만")
    ap.add_argument("--no-disc", action="store_true", help="공시는 건너뜀 (15분 회차)")
    ap.add_argument("--dump", default="", help="이번에 찾은 기사를 (종류|섹터)별로 이 파일에 써 둔다 — 텔레그램 발송이 다시 검색하지 않고 쓴다")
    args = ap.parse_args()
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    dump = {}
    now = datetime.now(KST)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "archive.json")
    store = {r["u"]: r for r in load_json(path).get("items", [])}
    before = len(store)

    errors, added = [], 0
    for sector in SECTORS:
        for mode in [m for m in ("통신", "단독") if m in modes]:
            try:
                items = nd.collect(mode, sector, cutoff)
            except Exception as e:  # 한 곳이 실패해도 나머지는 계속 (발송은 이 칸만 직접 검색한다)
                errors.append(f"{sector}/{mode}: {e}")
                continue
            dump[f"{mode}|{sector}"] = items
            added += merge(store, [to_row(i, mode, sector) for i in items])
    general_from = (now - timedelta(days=GENERAL_DAYS)).strftime(FMT)
    general_n = 0
    for sector in (SECTORS if "일반" in modes else []):  # '일반'은 섹터별 파일에 최근 GENERAL_DAYS일만
        gpath = os.path.join(args.out, general_file(sector))
        gstore = {r["u"]: r for r in load_json(gpath).get("items", []) if r.get("d", "") >= general_from}
        try:
            items = nd.collect("일반", sector, cutoff)
        except Exception as e:
            errors.append(f"{sector}/일반: {e}")
        else:
            dump[f"일반|{sector}"] = items
            general_n += merge(gstore, [to_row(i, "일반", sector) for i in items])
        os.makedirs(os.path.dirname(gpath), exist_ok=True)
        grows = sorted((r for r in gstore.values() if r["d"] >= general_from), key=lambda r: r["d"], reverse=True)
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump({"updated": now.strftime(FMT), "sector": sector, "items": grows}, f, ensure_ascii=False, separators=(",", ":"))
    if "외신" in modes:
        g_added, g_ko = collect_global(args.out, now, errors)
        print(f"외신: 새로 {g_added}건, 한글 제목 {g_ko}건")
    if args.no_disc:
        pass
    elif os.environ.get("DART_API_KEY", "").strip():
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
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump({k: [dict(i, dt=i["dt"].isoformat()) for i in v] for k, v in dump.items()}, f, ensure_ascii=False)
    print(f"수집 완료: 새로 {added}건, 보관 {len(rows)}건(이전 {before}건), 일반 새로 {general_n}건, 실패 {len(errors)}곳")
    for e in errors:
        print("  실패:", e, file=sys.stderr)
    if errors and len(errors) >= len(SECTORS) * len([m for m in modes if m != "외신"]) + (150 if "외신" in modes else 0):  # 거의 전부 실패했으면 오류로 끝내 알림이 가게 한다
        sys.exit(1)


if __name__ == "__main__":
    main()
