"""이슈 타임라인 자료(archive.json)의 과거를 한 번 채운다 (collect_data.py는 최근 2일만 훑으므로 모으기 전 기간은 비어 있다).

- 공시: DART 공시 목록 API를 날짜별로 받아 섹터 기업의 주요 공시를 담는다 (평일만, 접수 날짜 기준).
- 기사: 구글 뉴스 검색의 after:/before: 로 기간을 쪼개 받는다(구글이 막으면 일부가 빠질 수 있음). 단독은 네이버(최신순 최대 300건)도 함께 쓴다.
  걸러내는 규칙은 알람·수집과 같다(news_digest.collect를 그대로 부름: 검색어·언론사·스포츠 제외·차단어).
- 이미 있는 자료와 합친다(같은 주소는 한 번만, 섹터는 합침). 결과는 워크플로(.github/workflows/backfill_data.yml)가 'data' 브랜치에 올린다.
사용: python backfill_data.py [--out data] [--disc-days 183] [--news-days 45] [--news-sectors 반도체,자동차] [--news-step 7]
필요한 환경변수: DART_API_KEY (공시용), NAVER_CLIENT_ID/NAVER_CLIENT_SECRET (선택)
"""
import argparse
import collections
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

import collect_data as cd
import news_digest as nd

KST = nd.KST
RETRIES = 3
GAPS = (5, 15, 30)  # 구글이 429·503을 주면 이만큼(초) 쉬었다 다시 시도
WORKERS = 4
PAUSE = 0.3  # 구글 요청 사이 쉬는 시간(초)
MAX_FAIL_RATE = 0.3  # 이 비율을 넘게 실패하면 그 섹터·모드는 믿을 수 없어 실패로 본다


def date_windows(start, end, step):
    """start(포함)~end(제외) 날짜를 step일 이하 구간으로 -> [(시작, 끝)] (구글 before:는 그 날짜를 포함하지 않는다)"""
    out, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=step), end)
        out.append((cur, nxt))
        cur = nxt
    return out


def google_fetch_range(term, domains, extra, a, b, session=requests):
    """구글 뉴스 RSS: term·언론사 도메인·a(포함)~b(제외) 날짜의 기사. 429·503이면 쉬었다 다시 시도한다."""
    sites = " OR ".join(f"site:{d}" for d in domains)
    query = f'"{term}" {extra} ({sites}) after:{a:%Y-%m-%d} before:{b:%Y-%m-%d}'.replace("  ", " ")
    res = None
    for attempt in range(RETRIES):
        res = session.get("https://news.google.com/rss/search", timeout=nd.TIMEOUT, headers={"User-Agent": "Mozilla/5.0"},
                          params={"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        if res.status_code in (429, 503) and attempt < RETRIES - 1:
            time.sleep(GAPS[attempt])
            continue
        break
    res.raise_for_status()
    lo = datetime(a.year, a.month, a.day, tzinfo=KST)
    hi = datetime(b.year, b.month, b.day, tzinfo=KST)
    out = []
    for it in ET.fromstring(res.content).iter("item"):
        src = it.find("source")
        domain = urlparse((src.get("url") if src is not None else "") or "").netloc.lower().removeprefix("www.")
        if not any(domain == d or domain.endswith("." + d) for d in domains):
            continue
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST)
        except Exception:
            continue
        if not (lo <= when < hi):
            continue
        publisher = (src.text or "").strip() if src is not None else domain
        title = (it.findtext("title") or "").strip()
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3].strip()
        name, category = nd.publisher_of(f"https://{domain}")
        out.append({"title": title, "url": (it.findtext("link") or "").strip(), "publisher": name, "category": category, "dt": when})
    time.sleep(PAUSE)
    return out


def make_range_search(start, end, step, stats):
    """nd.collect에 끼워 넣을 구글 검색 함수: [start, end) 기간을 step일씩 쪼개 검색한다. stats에 요청·실패 수를 센다."""
    windows = date_windows(start, end, step)

    def search(terms, domains, extra, cutoff):
        chunks = [tuple(domains[i:i + nd.SITE_CHUNK]) for i in range(0, len(domains), nd.SITE_CHUNK)]
        jobs = [(t, c, a, b) for t in terms for c in chunks for a, b in windows]

        def one(job):
            try:
                return google_fetch_range(job[0], job[1], extra, job[2], job[3]), None
            except Exception as e:
                return [], f"{type(e).__name__}: {str(e)[:80]}"

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            got = list(pool.map(one, jobs))
        stats["jobs"] += len(jobs)
        errs = [e for _, e in got if e]
        stats["fail"] += len(errs)
        stats["first_error"] = stats.get("first_error") or (errs[0] if errs else None)
        items, seen = [], set()
        for lst, _ in got:
            for it in lst:
                if it["url"] not in seen:
                    seen.add(it["url"])
                    items.append(it)
        too_many = len(errs) > len(jobs) * MAX_FAIL_RATE
        return items, (errs[0] if too_many else None)

    return search


def backfill_disclosures(store, api_key, days, sectors, now, fetch_day=nd.fetch_dart_day):
    """평일마다 그날 전체 공시를 받아 섹터 기업의 주요 공시를 store에 합친다 -> (새 건수, 실패한 날짜 목록)"""
    added, failed = 0, []
    for k in range(days, -1, -1):
        day = now - timedelta(days=k)
        if day.weekday() >= 5:
            continue
        ymd = day.strftime("%Y%m%d")
        feed, ok = [], True
        for cls in ("Y", "K"):
            try:
                feed.extend(fetch_day(api_key, ymd, cls, ""))
            except Exception as e:
                ok = False
                failed.append(f"{ymd}/{cls}: {str(e)[:60]}")
        if feed:
            for sector in sectors:
                added += cd.merge(store, cd.disclosure_rows(feed, sector))
    return added, failed


def month_counts(rows):
    return dict(sorted(collections.Counter(r["d"][:7] for r in rows).items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--disc-days", type=int, default=183, help="공시를 채울 기간(일). 0이면 건너뜀")
    ap.add_argument("--news-days", type=int, default=45, help="기사를 채울 기간(일). 0이면 건너뜀")
    ap.add_argument("--news-sectors", default="반도체,자동차", help="기사를 채울 섹터(쉼표)")
    ap.add_argument("--news-step", type=int, default=7, help="기사 검색을 쪼개는 기간(일)")
    ap.add_argument("--keep-days", type=int, default=cd.KEEP_DAYS)
    args = ap.parse_args()
    now = datetime.now(KST)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "archive.json")
    store = {r["u"]: r for r in cd.load_json(path).get("items", [])}
    before = len(store)
    news_sectors = [x.strip() for x in args.news_sectors.split(",") if x.strip()]
    for sec in news_sectors:
        if sec not in nd.A["NEWS_SECTORS"]:
            raise SystemExit(f"없는 섹터: {sec}")
    errors = []

    if args.disc_days > 0:
        if not os.environ.get("DART_API_KEY", "").strip():
            errors.append("공시: DART_API_KEY 없음")
        else:
            sectors = list(dict.fromkeys(list(nd.SECTORS) + news_sectors))
            added, failed = backfill_disclosures(store, nd.dart_key(), args.disc_days, sectors, now)
            print(f"공시: 최근 {args.disc_days}일, 섹터 {sectors}, 새로 {added}건, 실패한 날 {len(failed)}곳")
            errors += [f"공시 {f}" for f in failed[:10]]

    if args.news_days > 0:
        end = (now + timedelta(days=1)).date()
        start = (now - timedelta(days=args.news_days)).date()
        cutoff = datetime(start.year, start.month, start.day, tzinfo=KST)
        for sector in news_sectors:
            for mode in ("통신", "단독"):
                stats = {"jobs": 0, "fail": 0}
                try:
                    items = nd.collect(mode, sector, cutoff, google=make_range_search(start, end, args.news_step, stats))
                except Exception as e:
                    errors.append(f"기사 {sector}/{mode}: {e}")
                    print(f"기사 {sector}/{mode}: 실패 — {e} (요청 {stats['jobs']}, 실패 {stats['fail']})")
                    continue
                rows = [cd.to_row(i, mode, sector) for i in items]
                new = cd.merge(store, rows)
                print(f"기사 {sector}/{mode}: 찾은 {len(rows)}건, 새로 {new}건, 구글 요청 {stats['jobs']}번 중 실패 {stats['fail']}번"
                      + (f" ({stats['first_error']})" if stats.get("first_error") else "") + f" | 월별 {month_counts(rows)}")

    keep_from = (now - timedelta(days=args.keep_days)).strftime(cd.FMT)
    rows = sorted((r for r in store.values() if r["d"] >= keep_from), key=lambda r: r["d"], reverse=True)
    stamp = now.strftime(cd.FMT)
    recent_from = (now - timedelta(days=cd.RECENT_DAYS)).strftime(cd.FMT)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"updated": stamp, "items": rows}, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out, "recent.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": stamp, "items": [r for r in rows if r["d"] >= recent_from]}, f, ensure_ascii=False, separators=(",", ":"))
    by_kind = collections.Counter(r["k"] for r in rows)
    print(f"채우기 완료: 보관 {len(rows)}건(이전 {before}건), 종류별 {dict(by_kind)}, 월별 {month_counts(rows)}")
    for e in errors:
        print("  주의:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
