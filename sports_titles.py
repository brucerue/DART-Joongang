"""연합뉴스 스포츠·전국(지방 소식) RSS의 기사 제목을 모아 둔다 (통신 기사에서 스포츠·전국 기사를 빼는 데 쓴다).

- GitHub Actions(.github/workflows/sports_titles.yml)가 매시간 실행한다.
- 이전에 모아 둔 목록(--prev)에 이번 RSS 제목을 더하고, 30일이 지난 것은 뺀다.
- 결과 sports_titles.json은 워크플로가 저장소의 별도 'sports-titles' 브랜치에 올린다 (main은 건드리지 않는다).
- 앱은 이 파일을 읽고, 알람(news_digest.py)은 RSS를 직접 읽는다.
사용: python sports_titles.py [--prev prev.json] [--out sports_titles.json]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests

RSS_URLS = ("https://www.yna.co.kr/rss/sports.xml", "https://www.yna.co.kr/rss/local.xml")  # 스포츠, 전국(지방 소식)
KEEP_DAYS = 30
MIN_ITEMS = 3  # RSS에 기사가 이보다 적으면 이상한 응답이므로 파일을 바꾸지 않는다
UA = {"User-Agent": "Mozilla/5.0 (news-digest sports titles)"}
TRIES = 4  # 연합뉴스 서버가 가끔 늦어서 몇 번 다시 시도한다 (쉬는 시간 10·30·60초)
STALE_HOURS = 6  # 이전 목록이 이보다 오래됐으면 접속 실패를 그냥 넘기지 않고 실패로 알린다


def fetch_rss(sleep=time.sleep, url=RSS_URLS[0]):
    """RSS 본문. 접속이 안 되면 몇 번 쉬었다 다시 시도하고, 끝내 안 되면 마지막 오류를 낸다."""
    last = None
    for i in range(TRIES):
        try:
            res = requests.get(url, headers=UA, timeout=(15, 60))
            res.raise_for_status()
            return res.content
        except requests.RequestException as e:
            last = e
            if i < TRIES - 1:
                sleep((10, 30, 60)[i])
    raise last


def is_fresh(prev, now=None):
    """이전 목록이 STALE_HOURS 안에 만들어진 것이면 True"""
    try:
        made = dt.datetime.fromisoformat(prev["updated"])
        if made.tzinfo is None:
            made = made.replace(tzinfo=dt.timezone.utc)
        return (now or dt.datetime.now(dt.timezone.utc)) - made < dt.timedelta(hours=STALE_HOURS)
    except (KeyError, ValueError, TypeError):
        return False


def title_key(title):
    """제목 비교용: 기호·공백을 지우고 소문자로 (앱의 title_key와 같은 규칙)"""
    return re.sub(r"[^0-9a-z가-힣]", "", str(title).lower())


def feed_items(xml_bytes):
    """RSS -> [(제목, 날짜 YYYY-MM-DD)]"""
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        day = dt.datetime.now(dt.timezone.utc).date().isoformat()
        try:
            day = dt.datetime.strptime((item.findtext("pubDate") or "").strip()[:25], "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
        except ValueError:
            pass
        out.append((title, day))
    return out


def merge(prev, fresh, today):
    """이전 목록 + 이번 제목 -> 30일 안의 목록 (같은 제목은 한 번만)"""
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    by_key = {}
    for it in prev.get("items", []):
        if it.get("d", "") >= cutoff and it.get("k"):
            by_key[it["k"]] = it
    for title, day in fresh:
        k = title_key(title)
        if not k:
            continue
        old = by_key.get(k)
        if old is None or day > old["d"]:
            by_key[k] = {"k": k, "t": title, "d": day}
    items = sorted(by_key.values(), key=lambda x: (x["d"], x["k"]), reverse=True)
    return {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "items": items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", default="sports_titles.json")
    args = ap.parse_args()
    prev = {}
    if args.prev and os.path.exists(args.prev):
        try:
            with open(args.prev, encoding="utf-8") as f:
                prev = json.load(f)
        except ValueError:
            prev = {}
    try:
        fresh = [it for url in RSS_URLS for it in feed_items(fetch_rss(url=url))]  # 둘 중 하나라도 못 읽으면 이번엔 목록을 그대로 둔다
    except requests.RequestException as e:
        if prev.get("items") and is_fresh(prev):  # 한두 번 놓쳐도 목록은 그대로 두고 넘어간다 (오래 안 되면 실패로 알림)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(prev, f, ensure_ascii=False, indent=1)
            print(f"RSS 접속 실패({type(e).__name__})라 이번엔 목록을 그대로 두었어요. 보관 {len(prev['items'])}건")
            return
        raise
    if len(fresh) < MIN_ITEMS:
        raise SystemExit(f"RSS 기사가 {len(fresh)}건뿐이라 목록을 바꾸지 않았어요.")
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    data = merge(prev, fresh, today)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"RSS(스포츠·전국) {len(fresh)}건 확인, 보관 {len(data['items'])}건")


if __name__ == "__main__":
    sys.exit(main())
