"""신문 지면 기사 목록 수집기 (네이버 뉴스 '신문 보기', 화면 없음). 결과는 'paper' 브랜치에 쌓는다 — 기획 도우미가 읽어 감.

- 파일: paper/YYYY-MM-DD/{oid}.json  {date, oid, paper, collected_at, pages_found, status, items:[{pg, title, url[, posted]}]}
  · 제목이 [사진]으로 시작하는 기사는 뺀다.
  · 기사 페이지에 찍힌 시각이 그 지면 날짜와 다르면(네이버 재게시·오분류) 기사는 그대로 두고 "posted"에 그 시각을 적는다.
  · status: ok / empty(그날 지면 목록이 비어 있음) / name_mismatch(언론사 번호와 신문명이 다름) / error: …  → ok가 아니면 다음 실행 때 다시 받는다.
- 인덱스: paper/index.json {updated, dates: {날짜: {oid: {status, n}}}}
- 요청 간격 0.5~1초.
사용: python paper_data.py --root out [--start 2026-11-01 --end 2026-11-09] [--oids 023,025] [--retry-days 3]
"""
import argparse
import html
import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
PAPERS = {"023": "조선일보", "025": "중앙일보", "020": "동아일보", "015": "한국경제", "009": "매일경제", "011": "서울경제"}
LIST_URL = "https://media.naver.com/press/{oid}/newspaper?date={ymd}"
ARTICLE_URL = "https://n.news.naver.com/article/{oid}/{aid}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}


def pause():
    time.sleep(random.uniform(0.5, 1.0))


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            res = requests.get(url, headers=UA, timeout=30)
            if res.status_code == 200:
                return res.text
            last = f"HTTP {res.status_code}"
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(3 * (i + 1))
    raise RuntimeError(last or "실패")


def press_name(page_html):
    """'신문 보기' 페이지의 언론사 이름 (없으면 '')"""
    for m in re.finditer(r'press_name[^>]*>\s*([^<]*?)\s*<', page_html):
        if m.group(1).strip():
            return html.unescape(m.group(1).strip())
    return ""


def parse_list(page_html, oid):
    """'신문 보기' 페이지 -> [{pg, title, url, aid}] (면 순서대로, [사진] 제외)"""
    out = []
    for blk in re.split(r'<span class="page_notation">', page_html)[1:]:
        m = re.match(r'\s*<em>([^<]+)</em>', blk)
        if not m:
            continue
        pg = m.group(1).strip()
        for aid, title in re.findall(rf'article/newspaper/{oid}/(\d+)\?date=\d+".*?<strong>(.*?)</strong>', blk, re.S):
            title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            title = re.sub(r"\s+", " ", title)
            if not title or title.startswith("[사진]"):
                continue
            out.append({"pg": pg, "title": title, "url": ARTICLE_URL.format(oid=oid, aid=aid), "aid": aid})
    return out


def posted_time(article_html):
    """기사 페이지에 찍힌 입력 시각 'YYYY-MM-DD HH:MM' (못 읽으면 None)"""
    m = re.search(r'data-date-time="(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', article_html)
    return m.group(1) if m else None


def collect_one(oid, d, fetch=get, now=None, wait=pause):
    """언론사 하나·날짜 하루 -> 저장할 dict"""
    now = now or datetime.now(KST)
    rec = {"date": d.isoformat(), "oid": oid, "paper": PAPERS.get(oid, ""), "collected_at": now.strftime("%Y-%m-%dT%H:%M"),
           "pages_found": 0, "status": "ok", "items": []}
    try:
        page = fetch(LIST_URL.format(oid=oid, ymd=d.strftime("%Y%m%d")))
    except Exception as e:
        rec["status"] = f"error: 목록 {e}"
        return rec
    name = press_name(page)
    if PAPERS.get(oid) and name and name != PAPERS[oid]:
        rec["status"] = f"name_mismatch: {name}"
        return rec
    items = parse_list(page, oid)
    rec["pages_found"] = len({i["pg"] for i in items})
    if not items:
        rec["status"] = "empty"
        return rec
    for it in items:  # 기사 페이지의 입력 시각이 지면 날짜와 다르면 표시만 한다
        wait()
        try:
            t = posted_time(fetch(it["url"]))
        except Exception:
            t = None
        if t and t[:10] != d.isoformat():
            it["posted"] = t
        it.pop("aid", None)
    rec["items"] = items
    return rec


def load_index(root):
    path = os.path.join(root, "paper", "index.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"dates": {}}


def run(root, days, oids, fetch=get, now=None, wait=pause):
    """days: 받을 날짜들(일요일은 뺌). 이미 ok인 날은 건너뛴다 -> (새로 받은 수, 실패 수)"""
    index = load_index(root)
    done, bad = 0, 0
    for d in days:
        if d.weekday() == 6:  # 일요일은 신문이 없다
            continue
        for oid in oids:
            prev = (index["dates"].get(d.isoformat()) or {}).get(oid)
            if prev and prev.get("status") == "ok":
                continue
            wait()
            rec = collect_one(oid, d, fetch, now, wait)
            folder = os.path.join(root, "paper", d.isoformat())
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{oid}.json"), "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=1)
            index["dates"].setdefault(d.isoformat(), {})[oid] = {"status": rec["status"], "n": len(rec["items"]), "paper": rec["paper"]}
            done += 1
            bad += rec["status"] != "ok"
            print(f"{d} {oid} {rec['paper']}: {rec['status']} · 기사 {len(rec['items'])}건 · 면 {rec['pages_found']}개")
    index["dates"] = dict(sorted(index["dates"].items()))
    index["updated"] = (now or datetime.now(KST)).strftime("%Y-%m-%dT%H:%M")
    index["papers"] = PAPERS
    os.makedirs(os.path.join(root, "paper"), exist_ok=True)
    with open(os.path.join(root, "paper", "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    return done, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="out")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--oids", default="")
    ap.add_argument("--retry-days", type=int, default=3, help="날짜를 안 주면: 오늘 + 이 기간 안에서 ok가 아닌 날을 다시 받음")
    args = ap.parse_args()
    today = datetime.now(KST).date()
    oids = [o.strip() for o in args.oids.split(",") if o.strip()] or list(PAPERS)
    if args.start:
        s = date.fromisoformat(args.start)
        e = date.fromisoformat(args.end) if args.end else s
        days = [s + timedelta(days=i) for i in range((e - s).days + 1)]
    else:
        days = [today - timedelta(days=i) for i in range(args.retry_days, -1, -1)]
    done, bad = run(args.root, days, oids)
    print(f"끝: 받은 지면 {done}개, ok 아닌 것 {bad}개")


if __name__ == "__main__":
    sys.exit(main())
