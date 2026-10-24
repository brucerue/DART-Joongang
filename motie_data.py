"""산업통상부 '○월 수출입 동향' 보도자료(매월 1일 11시)의 잠정치를 받아 flash.json에 모은다 (앱 '수출입 분석'의 잠정치용).

- 산업부 보도자료 게시판에서 해당 달 글을 찾아 PDF를 받고, motie_flash.parse_flash로 읽어 검산한 것만 저장한다.
- 이미 저장된 달은 다시 받지 않는다(--force로 다시). 글이 아직 없으면 조용히 끝내고(다음 회차에 다시 시도), --final이면 실패로 끝낸다.
- 결과는 워크플로(.github/workflows/motie_flash.yml)가 'flash' 브랜치에 한 커밋으로 올린다.
사용: python motie_data.py [--prev prev_flash.json] [--out flash.json] [--month 2026-09] [--final] [--force]
종료 코드: 0 = 저장함/이미 있음/글이 아직 없음(final 아님), 1 = 실패(글 없음+final, 읽기·검산 실패 등)
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import requests

import motie_flash as mf

KST = timezone(timedelta(hours=9))
BASE = "https://www.motie.go.kr"
LIST_URL = BASE + "/kor/article/ATCL3f49a5a8c"  # 보도자료 게시판
HEADERS = {"User-Agent": "Mozilla/5.0"}
KEEP_MONTHS = 14
MAX_PAGES = 15  # 최신 글이 앞쪽이라 1일 당일에는 1~2쪽이면 되지만, 늦게 받거나 지난 달을 받을 때를 위해 넉넉히
PAUSE = 1.0


def get(url, tries=5):
    """산업부 사이트는 연결을 가끔 끊어서, 쉬었다 다시 시도한다"""
    last = None
    for k in range(tries):
        try:
            res = requests.get(url, headers=HEADERS, timeout=60)
            res.raise_for_status()
            return res
        except requests.RequestException as e:
            last = e
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"{url[:80]}: {type(last).__name__}: {str(last)[:80]}")


def clean(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def title_regex(month):
    y, m = int(month[:4]), int(month[5:])
    return re.compile(rf"{y}년\s*{m}월(?:\s*및\s*\S+)?\s*수출입\s*동향")


def find_post(month):
    """게시판에서 해당 달 '수출입 동향' 글 -> (제목, 상세 주소). 없으면 None (ICT 수출입 동향은 제외)"""
    want = title_regex(month)
    for page in range(1, MAX_PAGES + 1):
        html = get(f"{LIST_URL}?pageIndex={page}").text
        time.sleep(PAUSE)
        for tr in re.findall(r"<tr>.*?</tr>", html, re.S):
            cell = re.search(r'<td[^>]*class="ta-l"[^>]*>.*?</td>', tr, re.S)
            title = clean(cell.group(0)) if cell else ""
            if not want.search(title) or "ICT" in title or "정보통신" in title:
                continue
            link = re.search(r'href="(/kor/article/ATCL3f49a5a8c/\d+/view[^"]*)"', tr)
            if link:
                return title, BASE + link.group(1).replace("&amp;", "&")
    return None


def pdf_link(detail_url):
    html = get(detail_url).text
    time.sleep(PAUSE)
    for href, name in re.findall(r'href="(/attach/down/[^"]+)"[^>]*>(.*?)</a>', html, re.S):
        if ".pdf" in clean(name).lower():
            return BASE + href
    raise RuntimeError("글에 PDF 첨부가 없어요 (hwp만 있을 수 있음)")


def fetch_month(month):
    """해당 달 보도자료를 받아 읽고 검산한 결과 dict. 글이 아직 없으면 None. 읽기·검산 실패는 예외."""
    post = find_post(month)
    if not post:
        return None
    title, url = post
    pdf = get(pdf_link(url)).content
    if pdf[:4] != b"%PDF":
        raise RuntimeError("받은 파일이 PDF가 아니에요")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf)
        path = f.name
    try:
        data = mf.parse_flash(mf.pdf_text(path))
    finally:
        os.unlink(path)
    if data["month"] != month:
        raise ValueError(f"찾은 글의 달({data['month']})이 달라요: {title}")
    data["title"] = title
    return data


def prev_month(now):
    y, m = now.year, now.month
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", default="flash.json")
    ap.add_argument("--month", default="")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    now = datetime.now(KST)
    month = args.month or prev_month(now)
    prev = {}
    if args.prev and os.path.exists(args.prev):
        try:
            with open(args.prev, encoding="utf-8") as f:
                prev = json.load(f)
        except ValueError:
            prev = {}
    months = dict(prev.get("months", {}))
    if month in months and not args.force:
        print(f"{month}은 이미 받아 뒀어요 (발표 {months[month].get('released')}).")
        return 0
    try:
        data = fetch_month(month)
    except Exception as e:
        print(f"{month} 보도자료 처리 실패: {e}", file=sys.stderr)
        return 1
    if data is None:
        msg = f"{month} '수출입 동향' 글이 아직 없어요."
        print(msg + (" (마지막 시도)" if args.final else " 다음 회차에 다시 확인해요."), file=sys.stderr if args.final else sys.stdout)
        return 1 if args.final else 0
    months[month] = data
    keep = sorted(months)[-KEEP_MONTHS:]
    out = {"updated": now.strftime("%Y-%m-%dT%H:%M"), "months": {k: months[k] for k in keep}}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    last = data["monthly"][month]
    print(f"{month} 잠정치 저장: 수출 {last['exp']:,.0f} 수입 {last['imp']:,.0f} 수지 {last['bal']:,.0f} (백만 달러), 발표 {data['released']}, "
          f"품목 {len(data['items'])}개, 지역 {len(data['exp_region'])}곳, 파일 {os.path.getsize(args.out) / 1e3:.0f}KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
