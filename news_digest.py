"""산업 뉴스·공시 자동 발송: 정해진 시각에 섹터별 통신/단독 뉴스와 주요 공시를 찾아 텔레그램으로 보낸다.

- GitHub Actions(.github/workflows/news_digest.yml)가 정해진 시각(한국 시간 06~19시 매시 정각)에 실행한다.
- 섹터·기업·언론사·별칭 같은 데이터는 같은 폴더의 app.py에서 그대로 읽어 온다(앱을 고치면 여기도 따라 바뀜).
- 이미 보낸 기사는 digest_seen.json에 기록해 두고 다시 보내지 않는다.

필요한 환경변수(GitHub 저장소 Settings > Secrets):
  TELEGRAM_BOT_TOKEN                             텔레그램 봇 토큰 (없으면 화면에만 출력하는 시험 모드)
  DIGEST_SUBSCRIBERS                             받는 사람 목록(JSON). TELEGRAM_CHAT_ID 칸에 같은 JSON을 넣어도 됨. 예:
      [{"name":"나상현","chat_id":"111","sectors":["자동차","배터리"],"modes":["통신","단독"]},
       {"name":"김OO","chat_id":"222","sectors":["방산","조선·해운"]}]
      sectors·modes를 생략하면 전부 받음. (없으면 subscribers.json 파일, 그것도 없으면 TELEGRAM_CHAT_ID 한 명)
  DART_API_KEY                                   (공시 모드용) DART 오픈API 인증키
  ADMIN_CHAT_ID                                  (선택) 앱의 '알람 받기' 신청을 알려 받을 내 chat id
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET           (선택) 단독 모드에서 구글 결과가 적을 때 보충
사용: python news_digest.py [--mode 통신|단독|공시] [--sectors 자동차,배터리] [--dry-run]
      python news_digest.py --who      봇에게 말을 건 사람들의 chat id 확인
"""
import argparse
import ast
import difflib
import html
import io
import json
import os
import re
import sys
import threading
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
SEEN_FILE = os.path.join(HERE, "digest_seen.json")
PREFS_FILE = os.path.join(HERE, "digest_prefs.json")  # 받는 사람이 봇 명령으로 바꾼 설정(회차 사이에 캐시로 이어짐)

# ---------- 설정: 받고 싶은 섹터·조회 방식 ----------
SECTORS = ["자동차", "배터리", "중후장대", "정유·석화", "에너지·소재"]
MODES = ["통신", "단독", "공시"]
LOOKBACK_HOURS = 12  # 발송 시각 기준 이 시간 안에 올라온 기사만 (쉬는 요일·시간 뒤에 다시 받을 때도 같은 기준. 이미 보낸 기사는 기록으로 걸러냄)
MAX_PER_SECTOR = 100  # 섹터마다 메시지에 싣는 최대 묶음 수 (넘으면 '외 N건'). 길면 텔레그램 메시지가 여러 통으로 나뉨
USE_NAVER = os.environ.get("USE_NAVER", "1").strip().lower() in ("1", "true", "yes", "on")  # 기본은 켜짐(네이버 키가 있을 때만 실제로 사용). 끄려면 환경변수 USE_NAVER=0
WIRE_DOMAINS = ("yna.co.kr",)  # 통신 모드: 연합뉴스만 (늘리려면 "newsis.com", "news1.kr" 추가)
SITE_CHUNK = 8
TIMEOUT = 20
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
DISCLOSURE_DAYS = 2  # 공시 모드: 오늘과 어제 접수분을 확인 (이미 보낸 공시는 기록으로 걸러냄)
MAX_DART_PAGES = 30  # 시장마다 하루 최대 3,000건까지만 확인
DISCLOSURE_SKIP = ["소유상황보고서", "소유주식변동신고서"]  # 텔레그램 알림에서 뺄 보고서명 단어: 임원·주요주주 특정증권등 소유상황보고서, 최대주주등 소유주식변동신고서 (공백·가운뎃점 무시, 수집·타임라인에는 적용 안 함)
DART_DOC_URL = "https://opendart.fss.or.kr/api/document.xml"  # 공시 원문(zip). 잠정실적 수치를 읽을 때 쓴다
PROV_UNITS = {"원": 1, "천원": 10**3, "백만원": 10**6, "억원": 10**8, "십억원": 10**9, "조원": 10**12}  # 잠정실적 표의 단위 (app.py와 같게)
EARN_WAIT_MINUTES = 30  # 잠정실적 원문이 DART에 아직 안 올라왔으면 이 시간까지는 기다렸다가(다음 회차에) 수치와 함께 보낸다
# -----------------------------------------------------


def load_app_data():
    """app.py를 실행하지 않고(Streamlit 없이) 필요한 상수와 함수만 읽어 온다."""
    src = open(APP, encoding="utf-8").read()
    tree = ast.parse(src)
    consts, funcs = {}, {}
    want_consts = {"LETTER_NAMES", "WORD_READINGS", "PUBLISHERS", "SIMILAR_TITLE", "NEWS_ALIASES", "NAME_READINGS",
                   "NEWS_SECTORS", "NEWS_ALWAYS_SEARCH", "INDUSTRY_GROUPS", "MAJOR_DISCLOSURES", "PUBLISHER_BLOCK", "ALARM_SECTORS", "ALARM_MODES", "TEAM_PRESETS", "HEAVY_NAME", "HEAVY_PARTS",
                   "PROV_REV_LABELS", "PROV_OP_LABELS", "SPORTS_WORDS", "NEWS_BLOCK_TERMS"}
    want_funcs = {"name_key", "is_exclusive", "title_key", "publisher_of", "cluster_news", "disclosure_key",
                  "parse_amount_cell", "parse_provisional", "doc_text_from_zip", "is_sports", "is_blocked", "key_in_title"}  # 뒤의 셋: 잠정실적 원문 읽기 (앱 '재무 조회'와 같은 규칙)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in want_consts and name not in consts:  # NEWS_SECTORS는 처음 정의(리터럴)만
                try:
                    consts[name] = ast.literal_eval(node.value)
                except ValueError:
                    pass
        elif isinstance(node, ast.FunctionDef) and node.name in want_funcs:
            funcs[node.name] = ast.get_source_segment(src, node)
    missing = (want_consts - set(consts)) | (want_funcs - set(funcs))
    if missing:
        raise SystemExit(f"app.py에서 읽지 못한 항목: {sorted(missing)}")
    ns = {"re": re, "unicodedata": unicodedata, "difflib": difflib, "urlparse": urlparse, "io": io, "zipfile": zipfile,
          "html": html, "AiError": RuntimeError, "PROV_UNITS": PROV_UNITS, **consts}
    for code in funcs.values():
        exec(code, ns)
    # app.py와 같은 방식으로 별칭 표를 완성한다 (NAME_READINGS를 NEWS_ALIASES에 합침)
    al = ns["NEWS_ALIASES"]
    for name, readings in ns["NAME_READINGS"].items():
        cur = al.setdefault(name, [])
        cur.extend(r for r in readings if r not in cur)
    return ns


A = load_app_data()
name_key, is_exclusive, title_key, publisher_of, cluster_news = (
    A["name_key"], A["is_exclusive"], A["title_key"], A["publisher_of"], A["cluster_news"])


def aliases_of(term):
    """검색어 + 같은 회사를 부르는 다른 이름들 (제목에 이 중 하나만 있어도 통과)."""
    key = name_key(term)
    names = [term]
    for canon, al in A["NEWS_ALIASES"].items():
        group = [canon] + list(al)
        if any(name_key(g) == key for g in group):
            names += [g for g in group if g not in names]
    return names


def migrate_sectors(sectors):
    """옛 섹터 이름(조선·해운, 방산, 철강)을 '중후장대'로 바꾼다 (순서 유지, 중복 제거).
    -> (새 목록, 세 섹터 중 일부만 받던 사람인지). 일부만 받던 사람은 묶음으로 넓어지므로 첫 회차에 조용히 기록만 한다."""
    parts, name = set(A["HEAVY_PARTS"]), A["HEAVY_NAME"]
    old = list(sectors or [])
    out = []
    for x in old:
        y = name if x in parts else x
        if y not in out:
            out.append(y)
    return out, bool(parts & set(old)) and not parts <= set(old)


def sector_terms(sector):
    """섹터 -> (검색어 목록, {이름: 대표 검색어}). 앱의 섹터 선택과 같은 규칙: 줄임말은 따로 검색하지 않는다."""
    info = A["NEWS_SECTORS"][sector]
    kept, skip = [], set()
    for f in info["기업"]:
        if name_key(f) in skip:
            continue
        kept.append(f)
        skip.update(name_key(a) for a in A["NEWS_ALIASES"].get(f, []))
    terms = kept + [k for k in info["키워드"] if k not in kept]
    owner, search = {}, []
    for t in terms:
        for n in aliases_of(t):
            owner.setdefault(n, t)
        search.append(t)
        for n in A["NEWS_ALWAYS_SEARCH"].get(t, []):
            owner.setdefault(n, t)
            search.append(n)
    return search, owner


# ---------- 구글 뉴스 ----------
def google_fetch(term, domains, extra, cutoff):
    sites = " OR ".join(f"site:{d}" for d in domains)
    query = f'"{term}" {extra} ({sites}) when:2d'.replace("  ", " ")
    res = requests.get("https://news.google.com/rss/search", timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"},
                       params={"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    res.raise_for_status()
    root = ET.fromstring(res.content)
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        domain = urlparse((src.get("url") if src is not None else "") or "").netloc.lower().removeprefix("www.")
        if not any(domain == d or domain.endswith("." + d) for d in domains):
            continue
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST)
        except Exception:
            continue
        if when < cutoff:
            continue
        publisher = (src.text or "").strip() if src is not None else domain
        title = (it.findtext("title") or "").strip()
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3].strip()
        name, category = publisher_of(f"https://{domain}")
        out.append({"title": title, "url": (it.findtext("link") or "").strip(), "publisher": name, "category": category, "dt": when})
    return out


def google_search(terms, domains, extra, cutoff):
    chunks = [tuple(domains[i:i + SITE_CHUNK]) for i in range(0, len(domains), SITE_CHUNK)]
    jobs = [(t, c) for t in terms for c in chunks]

    def one(job):
        try:
            return google_fetch(job[0], job[1], extra, cutoff), None
        except Exception as e:  # 일부 실패는 건너뜀
            return [], str(e)

    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(one, jobs))
    errs = [e for _, e in got if e]
    items, seen = [], set()
    for lst, _ in got:
        for it in lst:
            if it["url"] not in seen:
                seen.add(it["url"])
                items.append(it)
    return items, (errs[0] if errs and len(errs) == len(got) else None)


# ---------- 네이버 뉴스 (단독 보충용) ----------
def naver_search(terms, cutoff):
    cid, secret = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and secret):
        return []
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}

    gate = {"lock": threading.Lock(), "next": 0.0}

    def get_page(term, page):  # 호출 간격을 조절하고, 몰림(429)이면 쉬었다 다시 시도한다 (네이버는 초당 약 10건 제한)
        for attempt in range(5):
            with gate["lock"]:
                now = time.monotonic()
                at = max(now, gate["next"])
                gate["next"] = at + 0.12
            if at > now:
                time.sleep(at - now)
            res = requests.get("https://openapi.naver.com/v1/search/news.json", headers=headers, timeout=TIMEOUT,
                               params={"query": f"{term} 단독", "display": 100, "start": 1 + page * 100, "sort": "date"})
            if res.status_code == 429 and attempt < 4:
                time.sleep(0.6 * (attempt + 1))
                continue
            return res

    def one(term):
        out = []
        for page in range(3):
            res = get_page(term, page)
            res.raise_for_status()
            raw = res.json().get("items") or []
            old = False
            for it in raw:
                try:
                    when = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
                except Exception:
                    continue
                if when < cutoff:
                    old = True
                    continue
                url = it.get("originallink") or it.get("link") or ""
                name, category = publisher_of(url)
                title = html.unescape(re.sub(r"<[^>]+>", "", it.get("title") or "")).strip()
                out.append({"title": title, "url": url, "publisher": name, "category": category, "dt": when})
            if old or len(raw) < 100:
                break
        return out

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            return [i for lst in pool.map(one, terms) for i in lst]
    except Exception as e:
        print(f"  네이버 보충 실패: {e}", file=sys.stderr)
        return []


# ---------- 한 섹터 조회 ----------
def fetch_dart_day(api_key, ymd, cls, ty=""):
    """하루치 전체 공시(한 시장). DART 공시 목록 API를 페이지별로 받는다. 데이터 없음(013)은 빈 목록.
    ty: 공시 유형(예: "I" 거래소공시)만 받을 때. 비우면 전부"""
    def page(n):
        params = {"crtfc_key": api_key, "bgn_de": ymd, "end_de": ymd, "corp_cls": cls,
                  "last_reprt_at": "N", "page_no": n, "page_count": 100}
        if ty:
            params["pblntf_ty"] = ty
        res = requests.get(DART_LIST_URL, timeout=TIMEOUT, params=params)
        res.raise_for_status()
        jo = res.json()
        if jo.get("status") == "013":
            return {"list": [], "total_page": 0}
        if jo.get("status") != "000":
            raise RuntimeError(f"DART 오류 {jo.get('status')}: {jo.get('message')}")
        return jo
    first = page(1)
    rows = list(first.get("list", []))
    last = min(int(first.get("total_page", 1) or 1), MAX_DART_PAGES)
    if last > 1:
        with ThreadPoolExecutor(max_workers=5) as pool:
            for jo in pool.map(page, range(2, last + 1)):
                rows.extend(jo.get("list", []))
    return rows


def dart_key():
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DART_API_KEY가 없어요. GitHub Secrets에 DART API 키를 DART_API_KEY로 등록하세요.")
    return api_key


def fetch_dart_feed(now, earn_only=False, wide=False):
    """접수된 전 상장사(코스피·코스닥) 공시. 한 번 받아 모든 섹터가 같이 쓴다.
    공시 목록에는 날짜만 있고 시각이 없어서, 알림은 'LOOKBACK_HOURS 전이 속한 날짜 ~ 오늘'을 받는다 (오전 8시 회차는 어제+오늘, 낮에는 오늘만).
    earn_only(15분마다 도는 단독 확인 때): 잠정실적만 보면 되므로 오늘 접수된 거래소공시(I)만 받아 가볍게
    wide(기사·공시 수집용): 오늘·어제를 모두 받는다"""
    api_key = dart_key()
    if earn_only:
        days = [now.strftime("%Y%m%d")]
    elif wide:
        days = [(now - timedelta(days=d)).strftime("%Y%m%d") for d in range(DISCLOSURE_DAYS)]
    else:
        days = sorted({(now - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y%m%d"), now.strftime("%Y%m%d")})
    rows = []
    for ymd in days:
        for cls in ("Y", "K"):
            rows.extend(fetch_dart_day(api_key, ymd, cls, "I" if earn_only else ""))
    return rows


def earn_kind(report_nm):
    """보고서명이 영업(잠정)실적 공시면 '연결' 또는 '별도', 아니면 None"""
    rk = A["disclosure_key"](report_nm)
    if "영업잠정실적" not in rk:
        return None
    return "연결" if "연결" in rk else "별도"


def disclosure_items(feed, sector, earn_only=False, use_skip=True):
    """시장 전체 공시 중 섹터 기업의 '주요 공시'만 골라, 뉴스 항목과 같은 모양으로 돌려준다 (최신순).
    잠정실적은 같은 회사가 연결 기준을 냈으면 별도 기준은 빼고(중복), 수치를 붙일 수 있게 표시해 둔다.
    earn_only면 잠정실적만. use_skip: 알림에서 뺄 보고서(DISCLOSURE_SKIP)를 뺄지 (수집은 끔)."""
    companies = [n for part in A["HEAVY_PARTS"] for n in A["INDUSTRY_GROUPS"].get(part, [])] if sector == A["HEAVY_NAME"] else A["INDUSTRY_GROUPS"].get(sector, [])
    keys = {name_key(n) for n in companies}
    major = [A["disclosure_key"](w) for ws in A["MAJOR_DISCLOSURES"].values() for w in ws]
    skip = [A["disclosure_key"](w) for w in DISCLOSURE_SKIP] if use_skip else []
    has_cons = {r.get("corp_code") or r.get("corp_name") for r in feed if earn_kind(r.get("report_nm", "")) == "연결"}
    out = []
    for r in feed:
        if name_key(r.get("corp_name", "")) not in keys:
            continue
        rk = A["disclosure_key"](r.get("report_nm", ""))
        kind = earn_kind(r.get("report_nm", ""))
        if earn_only and not kind:
            continue
        if not kind and (not any(m in rk for m in major) or any(x in rk for x in skip)):
            continue
        if kind and any(x in rk for x in skip):
            continue
        if kind == "별도" and (r.get("corp_code") or r.get("corp_name")) in has_cons:
            continue
        no = str(r.get("rcept_no", ""))
        item = {"title": f"[{r.get('corp_name', '')}] {str(r.get('report_nm', '')).strip()}", "url": DART_VIEW_URL.format(no),
                "publisher": "DART", "key": no, "day": str(r.get("rcept_dt", ""))}
        if kind:
            item.update(earn=kind, corp=str(r.get("corp_name", "")), rcept_dt=str(r.get("rcept_dt", "")),
                        fix="정정" in str(r.get("report_nm", "")))
        out.append(item)
    out.sort(key=lambda x: x["key"], reverse=True)  # 접수번호가 클수록 나중에 접수
    return out


# ---------- 잠정실적 수치 ----------
class DocNotReady(Exception):
    """DART에 공시 원문 파일이 아직 없다 (방금 올라온 공시)"""


def fetch_doc_text(api_key, rcept_no):
    """접수번호 -> 공시 원문 글자 (표는 칸 ' | ', 줄 바꿈으로)"""
    res = requests.get(DART_DOC_URL, params={"crtfc_key": api_key, "rcept_no": rcept_no}, timeout=TIMEOUT)
    data = res.content or b""
    if not data.startswith(b"PK"):  # zip이 아니면 오류 응답
        msg = data.decode("utf-8", "ignore")
        code = re.search(r"<status>(\d+)</status>", msg) or re.search(r'"status"\s*:\s*"(\d+)"', msg)
        code = code.group(1) if code else "?"
        if code in ("013", "014"):
            raise DocNotReady(rcept_no)
        raise RuntimeError(f"DART 원문 오류 [{code}]")
    return A["doc_text_from_zip"](data)


EARN_ROWS = [  # (키, 메시지에 쓸 이름, 표의 줄 이름들)
    ("rev", "매출", ("매출액", "영업수익", "수익(매출액)")),
    ("op", "영업이익", ("영업이익", "영업손익", "영업손실")),
    ("net", "순이익", ("당기순이익", "당기순손익", "당기순손실", "분기순이익", "반기순이익")),
]
EARN_WORDS = ("흑자전환", "적자전환", "적자지속", "흑자지속", "적자확대", "적자축소")


def parse_earnings(text):
    """잠정실적 원문 표 -> {"rev": {"cur", "prev", "qoq", "prev_y", "yoy"}, ...} (금액은 원). 못 읽으면 None.
    표 모양(거래소 양식): 구분 | 당해실적 | 전기실적 | 전기대비증감율 | 전년동기실적 | 전년동기대비증감율"""
    mult = None
    for m in re.finditer(r"단위\s*[:：]?[\s|(]*([가-힣]+)", text):
        if m.group(1) in A["PROV_UNITS"]:
            mult = A["PROV_UNITS"][m.group(1)]
            break
    if not mult:
        return None
    stop = {"누계실적", "누적실적"}

    def row_from(cells):
        """라벨 다음 칸들 -> 한 줄 수치 (당해·전기·전기대비·전년동기·전년동기대비). 당해 금액이 없으면 None"""
        cells = [c for c in cells if c.replace(" ", "") not in ("당해실적", "당기실적")][:5]
        if any(c.replace(" ", "") in stop for c in cells):
            cells = cells[:[c.replace(" ", "") in stop for c in cells].index(True)]
        cur = A["parse_amount_cell"](cells[0]) if cells else None
        if cur is None:
            return None
        num = lambda k: A["parse_amount_cell"](cells[k]) if len(cells) > k else None
        row = {"cur": cur * mult, "prev": None, "qoq": None, "prev_y": None, "yoy": None}
        if len(cells) >= 5:  # 전기·전년동기 칸까지 있는 표
            row["prev"] = num(1) * mult if num(1) is not None else None
            row["prev_y"] = num(3) * mult if num(3) is not None else None
            for k, col in (("qoq", 2), ("yoy", 4)):  # 회사가 적은 증감율을 우선 쓰고, 비어 있으면 금액으로 계산
                w = cells[col].replace(" ", "")
                row[k] = w if w in EARN_WORDS else num(col)
        return row

    def which(cell):
        label = re.sub(r"[\s\d.\)]", "", cell) if len(cell) < 16 else ""
        if not label or any(x in label for x in ("대비", "비율", "률", "율")):  # 영업이익률 같은 줄은 금액이 아님
            return None
        return next((k for k, _, names in EARN_ROWS if label.startswith(names)), None)

    out = {}
    # 1) 표의 한 줄(칸 ' | ')씩: 빈 칸도 자리를 지켜서 읽는다
    for line in text.split("\n"):
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if cells and not cells[-1]:
            cells.pop()
        for i, cell in enumerate(cells):
            key = which(cell)
            if key and key not in out:
                row = row_from(cells[i + 1:])
                if row:
                    out[key] = row
                break
    # 2) 줄이 나뉜 표: 칸을 모두 펼쳐 라벨 뒤 칸들을 읽는다 (빈 칸은 건너뜀)
    flat = [c.strip() for c in re.split(r"[|\n]", text) if c.strip()]
    for i, cell in enumerate(flat):
        key = which(cell)
        if key and key not in out:
            row = row_from(flat[i + 1:i + 9])
            if row:
                out[key] = row
    return out or None


def change_text(cur, base, rev=False):
    """증감 표시: +7.2% / -15.1% / 흑자전환 / 적자전환 / 적자지속. 계산할 수 없으면 None"""
    if cur is None or base is None:
        return None
    if base > 0:
        return f"{(cur - base) / base * 100:+.1f}%"
    if rev:
        return None
    if base <= 0 < cur:
        return "흑자전환"
    if base >= 0 > cur:
        return "적자전환"
    if base < 0 and cur < 0:
        return "적자지속"
    return None


def won_text(v):
    """원 -> '46조 7,214억' / '3,512억' / '-1,204억'"""
    sign, v = ("-" if v < 0 else ""), abs(v)
    eok_total = round(v / 1e8)
    if eok_total >= 10000:
        jo, eok = divmod(eok_total, 10000)
        return f"{sign}{jo:,}조" + (f" {eok:,}억" if eok else "")
    if eok_total >= 1:
        return f"{sign}{eok_total:,}억"
    return f"{sign}{v / 1e8:.1f}억"


def earnings_detail(rows):
    """읽은 수치 -> 메시지에 붙일 줄들 (HTML)"""
    lines = []
    for key, name, _ in EARN_ROWS:
        r = rows.get(key)
        if not r:
            continue
        rev = key == "rev"
        yoy = r["yoy"] if isinstance(r["yoy"], str) else (f"{r['yoy']:+.1f}%" if r["yoy"] is not None and r["prev_y"] and r["prev_y"] > 0 else None)
        qoq = r["qoq"] if isinstance(r["qoq"], str) else (f"{r['qoq']:+.1f}%" if r["qoq"] is not None and r["prev"] and r["prev"] > 0 else None)
        yoy = yoy or change_text(r["cur"], r["prev_y"], rev)
        qoq = qoq or change_text(r["cur"], r["prev"], rev)
        if rev and yoy in EARN_WORDS:
            yoy = None
        if rev and qoq in EARN_WORDS:
            qoq = None
        cmp_ = " · ".join(x for x in (f"전년比 {yoy}" if yoy else "", f"전분기比 {qoq}" if qoq else "") if x)
        lines.append(f"   {name} {won_text(r['cur'])}" + (f" ({cmp_})" if cmp_ else ""))
    return html.escape("\n".join(lines), quote=False)


def earnings_title(item, text):
    """'[현대차] 2026년 3분기 잠정실적 (연결)' 처럼 짧게. 분기를 못 읽으면 원래 보고서명 그대로"""
    try:
        got = A["parse_provisional"](text, item.get("rcept_dt", "")) or {}
    except Exception:
        got = {}
    if not got.get("quarter"):
        return item["title"]
    q = "4분기(연간)" if got["quarter"] == 4 else f"{got['quarter']}분기"
    return f"[{item['corp']}] {got['year']}년 {q} 잠정실적 ({item['earn']}{', 정정' if item.get('fix') else ''})"


def enrich_earnings(items, seen, now, dry=False):
    """잠정실적 항목에 수치를 붙인다(같은 접수번호는 한 번만 읽음). 원문이 아직 없으면 EARN_WAIT_MINUTES까지는
    이번 회차에서 빼 두었다가(hold) 다음 회차에 수치와 함께 보내고, 그 뒤에도 없으면 수치 없이 보낸다.
    seen에 '기다리기 시작한 시각'을 남긴다 -> seen이 바뀌었으면 True"""
    by_no = {}
    for it in items:
        by_no.setdefault(it["key"], []).append(it)
    if not by_no:
        return False
    api_key = dart_key()

    def one(no):
        try:
            text = fetch_doc_text(api_key, no)
        except DocNotReady:
            return no, "wait", None
        except Exception as e:
            return no, "error", str(e)
        return no, "ok", text

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, list(by_no)))
    changed = False
    for no, status, text in results:
        group = by_no[no]
        if status == "ok":
            rows = parse_earnings(text)
            title = earnings_title(group[0], text)
            detail = earnings_detail(rows) if rows else html.escape("   (수치를 읽지 못했어요 · 원문에서 확인해 주세요)", quote=False)
            for it in group:
                it["title"], it["detail"] = title, detail
            print(f"  잠정실적 {no}: {title} / {'수치 읽음' if rows else '수치 못 읽음'}")
            continue
        if status == "wait":
            wkey = f"wait|{no}"
            first = seen.get(wkey)
            if first is None:
                seen[wkey] = now.isoformat()
                changed = True
                first = seen[wkey]
            if now - datetime.fromisoformat(first) < timedelta(minutes=EARN_WAIT_MINUTES):
                for it in group:
                    it["hold"] = True
                print(f"  잠정실적 {no}: 원문이 아직 없어 다음 회차에 다시 확인")
                continue
            note = "   (원문이 아직 DART에 없어 수치는 원문 링크에서 확인해 주세요)"
        else:
            print(f"  잠정실적 {no}: 원문을 받지 못함 ({text})", file=sys.stderr)
            note = "   (원문을 받지 못해 수치는 원문 링크에서 확인해 주세요)"
        for it in group:
            it["detail"] = html.escape(note, quote=False)
    return changed


SPORTS_RSS_URLS = ("https://www.yna.co.kr/rss/sports.xml", "https://www.yna.co.kr/rss/local.xml")  # 스포츠, 전국(지방 소식): 둘 다 통신 기사에서 뺀다


def sports_feed_keys():
    """연합뉴스 스포츠·전국 RSS의 제목 -> 비교용 글자 목록. 못 읽은 RSS는 건너뛴다(둘 다 못 읽으면 빈 목록: 낱말 목록만 쓴다)."""
    keys = []
    for url in SPORTS_RSS_URLS:
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (news-digest)"}, timeout=(10, 30))
            res.raise_for_status()
            root = ET.fromstring(res.content)
            keys += [A["title_key"](t) for t in (it.findtext("title") or "" for it in root.iter("item")) if t.strip()]
        except Exception as e:
            print(f"RSS({url.rsplit('/', 1)[-1]})를 읽지 못해 그 부분은 건너뜁니다: {e}", file=sys.stderr)
    return tuple(keys)


def collect(mode, sector, cutoff, google=None):
    """(모드, 섹터)의 cutoff 이후 기사. google: 구글 뉴스 검색 함수를 바꿔 끼울 때(과거 채우기용, 기본은 최근 2일을 찾는 google_search)"""
    google = google or google_search
    search, owner = sector_terms(sector)
    if mode == "통신":
        items, err = google(search, list(WIRE_DOMAINS), "", cutoff)
        if err:
            raise RuntimeError(f"구글 뉴스 오류: {err}")
        sports_keys = sports_feed_keys()  # 연합뉴스 스포츠 RSS의 제목 (낱말 목록과 함께 스포츠 기사를 뺀다)
        items = [i for i in items if i["category"] == "통신사" and i["publisher"] == "연합뉴스" and not A["is_sports"](i["title"], sports_keys)]
    else:
        items, err = google(search, list(A["PUBLISHERS"]), "단독", cutoff)
        if USE_NAVER and os.environ.get("NAVER_CLIENT_ID"):  # 구글 뉴스에 네이버 뉴스를 항상 합친다(구글이 늦게 반영하는 기사를 네이버가 메움)
            have = {title_key(i["title"]) for i in items}
            extra = [n for n in naver_search(search, cutoff) if title_key(n["title"]) not in have]
            if err and not extra:  # 구글이 막혔는데 네이버도 못 쓰면 실패로 기록
                raise RuntimeError(f"구글 뉴스 오류: {err}")
            items += extra
        elif err:
            raise RuntimeError(f"구글 뉴스 오류: {err}")
        items = [i for i in items if i["category"] and is_exclusive(i["title"])]
    items = [i for i in items if not A["is_blocked"](i["title"])]  # 이름이 같은 다른 회사·단체 기사(NEWS_BLOCK_TERMS)는 뺀다
    keys = {n: name_key(n) for n in owner}
    out = []
    for i in items:
        tk = name_key(i["title"])
        hits = [n for n, k in keys.items() if A["key_in_title"](k, tk, i["title"])]  # 제목에 검색어(줄임말 포함)가 있는 기사만 (짧은 한글 낱말은 띄어쓰기도 확인)
        if hits:
            out.append(dict(i, tags=list(dict.fromkeys(owner[h] for h in hits))))
    return out


# ---------- 메시지 ----------
RAW_LIMIT = 6000  # 링크 주소까지 포함한 메시지 길이 상한 (구글 뉴스 링크가 길어 텔레그램의 ENTITIES_TOO_LONG을 막기 위함)


def vlen(text):
    """텔레그램 글자 수 한도(4,096자)는 화면에 보이는 글자만 센다(링크 주소·HTML 태그는 제외)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def item_line(it, more=""):
    """기사·공시 한 건 -> 메시지 줄. 잠정실적은 아래에 수치 줄(detail)이 붙는다."""
    line = f'• <a href="{html.escape(it["url"], quote=True)}">{html.escape(it["title"])}</a> · {html.escape(it["publisher"])}{more}'
    return line + ("\n" + it["detail"] if it.get("detail") else "")


def build_messages(mode, now, sections, earn_only=False):
    """sections: [(섹터, [묶음...], 숨겨진 묶음 수)] -> 텔레그램 메시지 목록(보이는 글자 기준 4,000자 이하, 넘으면 여러 통)
    간략하게 보이도록 섹터 제목 없이 기사만 줄로 나열한다(같은 기사가 여러 섹터에 걸리면 한 번만)."""
    limit = 4000
    label = ("📊 잠정실적" if earn_only else "📋 주요 공시") if mode == "공시" else f"📰 산업 뉴스 · {mode}"
    head = f"<b>{label} · {now:%m/%d %H:%M}</b>"
    cont = head + " (이어서)"
    lines, seen_urls, hidden = [], set(), 0
    for _, clusters, h in sections:
        hidden += h
        for c in clusters:
            it = c["rep"]
            if it["url"] in seen_urls:
                continue
            seen_urls.add(it["url"])
            more = f" (+{len(c['others'])}건)" if c["others"] else ""
            lines.append(item_line(it, more))
    if hidden:
        lines.append(f"  … 외 {hidden}건")
    msgs, cur = [], head
    for ln in lines:  # 줄 단위로 채우고, 넘치면 새 메시지에서 이어간다
        if cur not in (head, cont) and (vlen(cur) + 1 + vlen(ln) > limit or len(cur) + 1 + len(ln) > RAW_LIMIT):
            msgs.append(cur)
            cur = cont
        cur += "\n" + ln
    if cur not in (head, cont):
        msgs.append(cur)
    return msgs


def combine_messages(now, parts, earn_only=False):
    """모드가 둘 이상이고 전부 합쳐도 한 메시지에 들어가면 하나로 합친 글을, 아니면 None(모드별로 따로 보냄).
    합친 글은 간략하게 보이도록 섹터 제목 없이 기사만 모아 보여 준다(같은 기사가 여러 섹터에 걸리면 한 번만)."""
    if len(parts) < 2:
        return None
    names = {"통신": "통신", "단독": "단독", "공시": "📊 잠정실적" if earn_only else "📋 주요 공시"}
    bodies = []
    for mode, sections, _ in parts:
        lines, seen_urls, hidden = [], set(), 0
        for _, clusters, h in sections:
            hidden += h
            for c in clusters:
                it = c["rep"]
                if it["url"] in seen_urls:
                    continue
                seen_urls.add(it["url"])
                more = f" (+{len(c['others'])}건)" if c["others"] else ""
                lines.append(item_line(it, more))
        if hidden:
            lines.append(f"  … 외 {hidden}건")
        bodies.append(f"<b>━ {names.get(mode, mode)} ━</b>\n" + "\n".join(lines))
    text = f"<b>📰 산업 뉴스·공시 · {now:%m/%d %H:%M}</b>\n" + "\n\n".join(bodies)
    if vlen(text) > 4000 or len(text) > RAW_LIMIT:
        return None
    return text


def send_telegram(text, token, chat_id):
    res = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=TIMEOUT,
                        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    if res.status_code != 200:
        raise RuntimeError(f"텔레그램 발송 실패 [{res.status_code}] {res.text[:200]}")


def send_safely(text, token, chat_id):
    """보내다가 '너무 길다'(ENTITIES_TOO_LONG 등)고 거절되면 줄 단위로 반씩 나눠 다시 보낸다."""
    try:
        send_telegram(text, token, chat_id)
    except RuntimeError as e:
        if not any(k in str(e) for k in ("ENTITIES_TOO_LONG", "MESSAGE_TOO_LONG")):
            raise
        lines = text.split("\n")
        if len(lines) < 4:
            raise
        mid = len(lines) // 2
        first = "\n".join(lines[:mid])
        second = lines[0] + " (이어서)\n" + "\n".join(lines[mid:])
        send_safely(first, token, chat_id)
        send_safely(second, token, chat_id)


def send_failure_alert(kind, token):
    """발송 실행이 실패했을 때 관리자(나)에게 텔레그램으로 알린다. 구글 스크립트(PREFS_URL)가 있으면 거기로 보내(같은 종류 1시간 1회 제한),
    없으면 봇 토큰으로 직접 보낸다. 알림이 안 가도 오류로 끝내지 않는다."""
    run_url = "/".join(filter(None, [os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY"), "actions/runs", os.environ.get("GITHUB_RUN_ID")]))
    what = {"digest": "뉴스 발송", "sync": "동기화", "collect": "기사·공시 모으기", "corp": "기업 목록 만들기", "sports": "스포츠 제목 모으기", "prices": "시세 모으기", "intraday": "장중 시세 모으기", "trade": "수출입 통계 모으기", "flash": "산업부 수출입 잠정치 모으기", "groups": "20대 품목 자료 모으기"}.get(kind, kind)
    mode = os.environ.get("IN_MODE", "").strip()
    text = f"{what} 실행이 실패했어요{' (' + mode + ')' if mode and mode != '전체' else ''}.\n아래 링크에서 원인을 볼 수 있어요.\n{run_url}"
    url = os.environ.get("PREFS_URL", "").strip()
    try:
        if url:
            requests.post(url, json={"alert": True, "kind": kind, "text": text}, timeout=TIMEOUT)
            print("실패 알림을 보냈어요(구글 스크립트 경유)")
            return
        admin = os.environ.get("ADMIN_CHAT_ID", "").strip()
        if token and admin:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": admin, "text": "⚠ " + text, "disable_web_page_preview": True}, timeout=TIMEOUT)
            print("실패 알림을 보냈어요")
        else:
            print("실패 알림을 보낼 곳이 없어요(PREFS_URL 또는 봇 토큰+ADMIN_CHAT_ID 필요)")
    except Exception as e:
        print(f"(실패 알림 전송 오류: {type(e).__name__})", file=sys.stderr)


def merge_approved(subs, approved):
    """앱 '알람 받기' 신청을 관리자가 승인 버튼으로 받아들인 사람들(구글 스크립트가 보관)을 GitHub 명단에 합친다. 이미 명단에 있으면 GitHub 쪽이 우선."""
    if not isinstance(approved, list):
        return subs
    have = {x["chat_id"] for x in subs}
    out = list(subs)
    for d in approved:
        try:
            cid = str(d["chat_id"]).strip()
        except (KeyError, TypeError):
            continue
        if not cid or cid in have:
            continue
        sectors = [x for x in (d.get("sectors") or []) if x in A["NEWS_SECTORS"]] or list(SECTORS)
        modes = [m for m in (d.get("modes") or []) if m in MODES] or list(MODES)
        out.append({"name": str(d.get("name") or cid), "chat_id": cid, "sectors": sectors, "modes": modes})
        have.add(cid)
    return out


def report_run(kind, people, delivered, errors, skipped=False):
    """이번 실행 결과를 구글 스크립트에 알려 둔다(관리자 /상태 명령이 보여줌). 실패해도 무시."""
    url = os.environ.get("PREFS_URL", "").strip()
    if not url:
        return
    try:
        requests.post(url, json={"run": True, "kind": kind, "people": people, "delivered": delivered, "errors": errors, "skipped": skipped}, timeout=TIMEOUT)
    except Exception as e:
        print(f"(실행 결과 보고 실패: {type(e).__name__})", file=sys.stderr)


def sync_to_script(subs):
    """PREFS_URL(구글 Apps Script)이 있으면, GitHub의 받는 사람 목록(Secret)과 app.py의 섹터·팀 목록을 거기로 보내
    구글 쪽을 따로 고치지 않아도 되게 한다. 실패해도 발송은 계속한다. -> 성공 여부"""
    url = os.environ.get("PREFS_URL", "").strip()
    if not url or not subs:
        return False
    payload = {
        "sync": True,
        "subscribers": [{"name": x["name"], "chat_id": x["chat_id"], "sectors": x["sectors"], "modes": x["modes"]} for x in subs],
        "sectors": list(A.get("ALARM_SECTORS") or []), "modes": list(A.get("ALARM_MODES") or MODES),
        "teams": {k: list(v) for k, v in (A.get("TEAM_PRESETS") or {}).items()}, "defaults": list(SECTORS),
    }
    for attempt in range(3):  # 구글 스크립트가 가끔 늦게 응답해서 몇 번 다시 시도한다
        try:
            res = requests.post(url, json=payload, timeout=(10, 40))
            res.raise_for_status()
            try:
                ok = bool(res.json().get("ok"))
            except ValueError:
                ok = False
            print(f"구글 스크립트와 동기화: {'완료' if ok else '응답을 확인하지 못함'} (받는 사람 {len(subs)}명)")
            return ok
        except Exception as e:
            print(f"(구글 스크립트 동기화 실패: {type(e).__name__}{' - 다시 시도' if attempt < 2 else ''})", file=sys.stderr)
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    return False


def fetch_remote_prefs():
    """PREFS_URL(구글 Apps Script가 즉시 응답하는 방식)이 있으면 거기서 받는 사람 설정을 읽어 PREFS_FILE에 저장한다.
    못 읽으면 마지막으로 저장해 둔 것을 그대로 쓴다. -> 사용 여부(True/False)"""
    url = os.environ.get("PREFS_URL", "").strip()
    if not url:
        return False
    try:
        res = requests.get(url, timeout=TIMEOUT)
        res.raise_for_status()
        data = res.json()
        if not isinstance(data, dict):
            raise ValueError("설정이 목록 모양이 아님")
        save_prefs(data)
        print(f"받는 사람 설정을 Apps Script에서 읽음 ({len(data)}명)")
    except Exception as e:
        print(f"(받는 사람 설정을 읽지 못해 마지막 설정을 씀: {type(e).__name__})", file=sys.stderr)
    return True


def load_prefs():
    """{chat_id: {"sectors": [...], "modes": [...], "paused": bool}} - 받는 사람이 봇 명령으로 바꾼 설정."""
    try:
        with open(PREFS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(prefs):
    with open(PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False)


HELP_TEXT = (
    "알람 설정 명령어예요.\n"
    "/설정 - 지금 설정 보기\n"
    "/섹터 자동차 배터리 - 받을 섹터 바꾸기 (팀 이름 '차팀', '전체'도 돼요)\n"
    "/종류 통신 공시 - 받을 종류 바꾸기 (통신·단독·공시)\n"
    "/시간 8-18 - 받을 시간대 (기본 8~19시)\n"
    "/요일 월-금 일 - 받을 요일 (기본 월~금)\n"
    "/끄기 - 알람 잠시 멈추기\n"
    "/켜기 - 다시 받기\n"
    "/도움 - 이 안내 다시 보기\n"
    "통신·공시는 매시 정각, 단독과 잠정실적 공시는 15분마다 확인해요.\n"
    "새 기사나 공시가 없으면 메시지를 보내지 않아요."
)


HOURS_MIN, HOURS_MAX = 8, 19  # 발송 가능한 전체 시간대(한국시간). 사람마다 이 안에서만 좁힐 수 있다


DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]  # 번호는 파이썬 weekday()와 같음: 월=0 ... 일=6
DAYS_OPEN = [0, 1, 2, 3, 4, 5, 6]  # 받을 수 있는 요일: 월~일 모두 (기본은 월~금)
DAYS_DEFAULT = [0, 1, 2, 3, 4]


def days_text(days):
    """[0,1,2,3,4] -> '월~금', [0,2,4] -> '월·수·금', [0,1,2,3,4,6] -> '월~금·일'"""
    days = sorted(days)
    runs, cur = [], [days[0]]
    for d in days[1:]:
        (cur.append(d) if d == cur[-1] + 1 else (runs.append(cur), cur := [d]))
    runs.append(cur)
    return "·".join(DAY_NAMES[r[0]] if len(r) == 1 else (DAY_NAMES[r[0]] + ("·" if len(r) == 2 else "~") + DAY_NAMES[r[-1]]) for r in runs)


def parse_days(words):
    """'월수금', '월-목', '월-금 일', '평일', '매일' -> [요일 번호]. '기본' -> None. 못 알아들으면 ValueError(안내 글)."""
    text = "".join(words).replace(" ", "").replace("요일", "")
    if text in ("기본", "reset", "default"):
        return None
    text = text.replace("평일", "월-금").replace("매일", "월-일")
    order = [0, 1, 2, 3, 4, 5, 6]
    out = set()
    i = 0
    while i < len(text):
        c = text[i]
        if c not in DAY_NAMES:
            if c in "-~,":
                i += 1
                continue
            raise ValueError("알아듣지 못했어요. 예: /요일 월수금  /요일 월-금 일 (되돌리기: /요일 기본)")
        if i + 2 < len(text) + 0 and text[i + 1] in "-~" and text[i + 2] in DAY_NAMES:
            a, b = DAY_NAMES.index(c), DAY_NAMES.index(text[i + 2])
            ia, ib = order.index(a), order.index(b)
            if ia > ib:
                raise ValueError("범위는 월→일 순서로 써주세요. 예: 월-목")
            out.update(order[ia:ib + 1])
            i += 3
            continue
        out.add(DAY_NAMES.index(c))
        i += 1
    if not out:
        raise ValueError("요일을 알려주세요. 예: /요일 월-금 일 (되돌리기: /요일 기본)")
    return sorted(out)


def parse_hours(words):
    """'8-18', '8~18', '8시부터 18시' -> (8, 18). '기본'/'전체' -> None(되돌리기). 못 알아들으면 ValueError(안내 글)."""
    text = " ".join(words)
    if text.strip() in ("기본", "전체", "reset", "default"):
        return None
    nums = [int(x) for x in re.findall(r"\d{1,2}", text)]
    if len(nums) != 2:
        raise ValueError("시간 범위를 알려주세요. 예: /시간 8-18 (되돌리기: /시간 기본)")
    a, b = nums
    if not (HOURS_MIN <= a <= b <= HOURS_MAX):
        raise ValueError(f"{HOURS_MIN}~{HOURS_MAX}시 안에서만 고를 수 있고, 시작은 끝보다 늦을 수 없어요. 예: /시간 8-18")
    return (a, b)


def plain_name(text):
    """섹터 이름 비교·안내용: 중간점(·ㆍ・•)과 공백을 없앤 글자 (모바일에서 중간점을 치기 어려워 '조선해운'으로도 받는다)"""
    return re.sub(r"[·ㆍ・•∙\s]", "", str(text))


def parse_choice(words, options, aliases=None):
    """명령 뒤 단어들 -> (고른 항목들, 못 알아본 단어들). 이름이 정확히 같거나(중간점 없이 써도 됨: 조선해운), 한 항목에만 들어 있는 글자(예: 조선 -> 조선·해운)면 인정."""
    chosen, bad = [], []
    for w in words:
        w = w.strip(",;")
        if not w:
            continue
        if aliases and w in aliases:
            hits = list(aliases[w])
        elif w in options:
            hits = [w]
        else:
            pw = plain_name(w).lower()
            hits = [o for o in options if pw and plain_name(o).lower() == pw]
            if not hits:
                hits = [o for o in options if len(pw) >= 2 and pw in plain_name(o).lower()]
            hits = hits if len(hits) == 1 else []
        if not hits:
            bad.append(w)
        for h in hits:
            if h not in chosen:
                chosen.append(h)
    return chosen, bad


def describe_prefs(sub, prefs):
    mine = prefs.get(sub["chat_id"]) or {}
    state = "멈춤 (/켜기 로 다시 받아요)" if mine.get("paused") else "받는 중"
    h = mine.get("hours")
    hours = f"{h[0]}~{h[1]}시" if h else f"{HOURS_MIN}~{HOURS_MAX}시 (기본)"
    dd = mine.get("days")
    dtxt = days_text(dd) if dd else days_text(DAYS_DEFAULT) + " (기본)"
    return (f"지금 설정이에요.\n상태: {state}\n섹터: {', '.join(sub['sectors'])}\n종류: {', '.join(sub['modes'])}\n받는 시간: {hours}\n받는 요일: {dtxt}")


def run_command(text, sub, prefs):
    """받는 사람의 명령 한 줄 -> 답장 글(명령이 아니면 None). prefs를 그 자리에서 고친다."""
    m = re.match(r"^/(\S+?)(?:@\w+)?(?:\s+(.*))?$", text.strip(), re.S)
    if not m:
        return None
    cmd, rest = m.group(1).lower(), (m.group(2) or "").replace(",", " ").split()
    mine = prefs.setdefault(sub["chat_id"], {})
    if cmd in ("start", "help", "도움", "도움말"):
        return HELP_TEXT
    if cmd in ("설정", "status", "상태"):
        return describe_prefs(sub, prefs)
    if cmd in ("끄기", "off", "stop", "중지"):
        mine["paused"] = True
        return "알람을 멈췄어요. 다시 받으려면 /켜기 를 보내주세요."
    if cmd in ("켜기", "on", "resume", "재개"):
        mine.pop("paused", None)
        return "알람을 다시 켰어요."
    if cmd in ("섹터", "sectors", "sector"):
        presets = A.get("TEAM_PRESETS") or {}
        options = list(A.get("ALARM_SECTORS") or SECTORS)
        aliases = {t: v for t, v in presets.items()}
        aliases["전체"] = options
        for old in ("조선·해운", "조선해운", "조선", "해운", "방산", "철강"):  # 옛 섹터 이름은 중후장대로 받는다
            aliases.setdefault(old, [A["HEAVY_NAME"]])
        chosen, bad = parse_choice(rest, options, aliases)
        if bad or not chosen:
            head = f"알아듣지 못한 말: {', '.join(bad)}" if bad else "받을 섹터를 같이 보내주세요. 예: /섹터 자동차 배터리"
            return f"{head}\n\n가능한 섹터: {', '.join(plain_name(o) for o in options)}\n팀: {', '.join(presets) or '-'} / 전체"
        mine["sectors"] = chosen
        sub["sectors"] = chosen
        return "받을 섹터를 바꿨어요: " + ", ".join(chosen)
    if cmd in ("요일", "days", "day"):
        try:
            d = parse_days(rest)
        except ValueError as e:
            return str(e)
        if d is None:
            mine.pop("days", None)
            return f"받는 요일을 기본({days_text(DAYS_DEFAULT)})으로 되돌렸어요."
        mine["days"] = d
        return f"받는 요일을 {days_text(d)}로 바꿨어요. 쉬는 날 나온 기사는 다음 받는 날 첫 시간에 한꺼번에 와요."
    if cmd in ("시간", "hours", "hour", "time"):
        try:
            h = parse_hours(rest)
        except ValueError as e:
            return str(e)
        if h is None:
            mine.pop("hours", None)
            return f"받는 시간을 기본({HOURS_MIN}~{HOURS_MAX}시, 평일)으로 되돌렸어요."
        mine["hours"] = list(h)
        return f"받는 시간을 {h[0]}~{h[1]}시로 바꿨어요. 이 시간 밖에 나온 기사는 시작 시각에 한꺼번에 와요."
    if cmd in ("종류", "modes", "mode"):
        chosen, bad = parse_choice(rest, list(MODES), {"전체": list(MODES)})
        if bad or not chosen:
            head = f"알아듣지 못한 말: {', '.join(bad)}" if bad else "받을 종류를 같이 보내주세요. 예: /종류 통신 공시"
            return f"{head}\n\n가능한 종류: {', '.join(MODES)} / 전체"
        mine["modes"] = chosen
        sub["modes"] = chosen
        return "받을 종류를 바꿨어요: " + ", ".join(chosen)
    return "모르는 명령이에요.\n" + HELP_TEXT


def handle_updates(token, admin_chat, base_subs, dry):
    """봇에게 온 메시지를 읽는다. 1) 알람 신청(/start s…m…)은 관리자(나)에게 알리고 신청자에게 접수 안내
    2) 받는 사람 목록에 있는 사람의 명령(/섹터 /종류 /끄기 /켜기 /설정)은 설정을 바꾸고 바로 답장.
    신청 내용은 앱의 '알람 받기'가 t.me 링크의 start 값에 담아 둔 것. 받는 사람 목록(Secrets)에 넣는 일은 직접 한다.
    -> 설정이 바뀌었으면 True"""
    try:
        res = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=TIMEOUT,
                           params={"timeout": 0, "allowed_updates": json.dumps(["message"])})
        updates = res.json().get("result", []) if res.status_code == 200 else []
    except Exception as e:  # 확인이 안 돼도 뉴스 발송은 계속한다
        print(f"(메시지 확인 실패: {type(e).__name__})", file=sys.stderr)
        return False
    sectors, modes = A.get("ALARM_SECTORS") or [], A.get("ALARM_MODES") or []
    prefs = load_prefs()
    before = json.dumps(prefs, sort_keys=True, ensure_ascii=False)
    effective = {s["chat_id"]: s for s in apply_prefs(base_subs, prefs, keep_paused=True)}
    last_id = None
    for u in updates:
        last_id = u["update_id"]
        msg = u.get("message") or {}
        body = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        cid = str(chat.get("id", ""))
        m = re.match(r"^/start(?:@\w+)?\s+s([01]+)m([01]+)\s*$", body)
        if m:
            if not admin_chat or len(m.group(1)) != len(sectors) or len(m.group(2)) != len(modes):
                continue
            name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("title") or cid
            sec = [x for x, b in zip(sectors, m.group(1)) if b == "1"]
            mod = [x for x, b in zip(modes, m.group(2)) if b == "1"]
            entry = json.dumps({"name": name, "chat_id": cid, "sectors": sec, "modes": mod}, ensure_ascii=False, separators=(",", ":"))
            user_tag = f" (@{chat['username']})" if chat.get("username") else ""
            text = (f"🔔 알람 신청\n이름: {html.escape(name)}{html.escape(user_tag)}\nchat id: {cid}\n"
                    f"섹터: {html.escape(', '.join(sec) or '-')}\n종류: {html.escape(', '.join(mod) or '-')}\n\n"
                    f"받는 사람 목록에 넣을 항목:\n<code>{html.escape(entry, quote=False)}</code>")
            if dry:
                print("-" * 40 + "\n[신청 알림 → 관리자]\n" + text)
                continue
            try:
                send_telegram(text, token, admin_chat)
                send_telegram("신청이 접수됐어요. 확인되면 알람이 시작돼요.", token, cid)
            except Exception as e:
                print(f"(신청 알림 발송 실패: {e})", file=sys.stderr)
            continue
        if not body.startswith("/") or not cid:
            continue  # 잡담은 무시
        sub = effective.get(cid)
        if sub is None:
            reply = "아직 알람 받는 사람 목록에 없어요. 앱의 '🔔 알람 받기'로 신청해 주세요." if not body.startswith("/start") else None
            if reply is None:
                continue
        else:
            reply = run_command(body, sub, prefs)
        if reply is None:
            continue
        if dry:
            print("-" * 40 + f"\n[명령 답장 → {cid}]\n" + reply)
            continue
        try:
            send_telegram(html.escape(reply, quote=False), token, cid)
        except Exception as e:
            print(f"(명령 답장 실패: {e})", file=sys.stderr)
    if last_id is not None and not dry:  # 처리한 알림은 확인 처리해서 다음에 다시 읽지 않게 한다
        try:
            requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=TIMEOUT, params={"offset": last_id + 1, "timeout": 0})
        except Exception:
            pass
    changed = json.dumps(prefs, sort_keys=True, ensure_ascii=False) != before
    if changed and not dry:
        save_prefs(prefs)
    return changed


def send_guides(token, subs, dry, force=False):
    """받는 사람에게 명령어 안내를 보낸다. 이미 받은 사람은 건너뛴다(받았다는 표시는 설정 저장소에 남음).
    force(--only와 함께 시험할 때)면 받은 적 있어도 보내고 표시는 남기지 않는다."""
    prefs = load_prefs()
    todo = [s for s in subs if force or not (prefs.get(s["chat_id"]) or {}).get("guided")]
    if not todo:
        return
    text = html.escape("📌 알람 설정을 직접 바꿀 수 있어요.\n\n" + HELP_TEXT, quote=False)
    done = False
    for sub in todo:
        if dry:
            print(f"[명령어 안내 → {sub['name']}] (시험 모드라 보내지 않음)")
            continue
        try:
            send_telegram(text, token, sub["chat_id"])
        except Exception as e:
            print(f"(명령어 안내 실패 {sub['name']}: {e})", file=sys.stderr)
            continue
        if not force:
            prefs.setdefault(sub["chat_id"], {})["guided"] = True
            done = True
    if done:
        save_prefs(prefs)


def apply_prefs(subs, prefs, keep_paused=False):
    """받는 사람 목록에 봇 명령으로 바꾼 설정을 덧씌운다. 멈춘 사람은 뺀다(keep_paused면 둔다)."""
    out = []
    for sub in subs:
        mine = prefs.get(sub["chat_id"]) or {}
        if mine.get("paused") and not keep_paused:
            continue
        new = dict(sub)
        if mine.get("paused"):
            new["paused"] = True  # 멈춘 사람: 보내지는 않지만 '받은 것으로' 기록해 둔다 (켠 이후 것만 받게)
        ra = mine.get("resumed_at")  # 마지막으로 /켜기 한 시각(밀리초). 이전에 나온 기사·공시는 보내지 않는다
        if isinstance(ra, (int, float)) and ra > 0:
            new["since"] = datetime.fromtimestamp(ra / 1000, KST)
        if mine.get("sectors"):
            moved, partial = migrate_sectors(mine["sectors"])
            new["sectors"] = [x for x in moved if x in A["NEWS_SECTORS"]] or sub["sectors"]
            new["heavy_partial"] = partial
        if mine.get("modes"):
            new["modes"] = [x for x in mine["modes"] if x in MODES] or sub["modes"]
        h = mine.get("hours")
        if isinstance(h, list) and len(h) == 2 and all(isinstance(x, int) for x in h) and HOURS_MIN <= h[0] <= h[1] <= HOURS_MAX:
            new["hours"] = (h[0], h[1])
        dd = mine.get("days")
        if isinstance(dd, list) and dd and all(isinstance(x, int) and x in DAYS_OPEN for x in dd):
            new["days"] = sorted(set(dd))
        out.append(new)
    return out


def load_seen():
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_subscribers():
    """받는 사람 목록. 우선순위: 환경변수 DIGEST_SUBSCRIBERS(JSON) > subscribers.json 파일 > TELEGRAM_CHAT_ID 한 명.
    각 항목: {"name": "이름", "chat_id": "123", "sectors": ["자동차", ...], "teams": ["차팀"], "modes": ["통신", "단독"]}
    teams는 앱의 팀 프리셋(TEAM_PRESETS)에 든 섹터를 한꺼번에 더한다(sectors와 합쳐짐). sectors·teams·modes를 생략하면 SECTORS·MODES 전부를 받는다."""
    raw = os.environ.get("DIGEST_SUBSCRIBERS", "").strip()
    path = os.path.join(HERE, "subscribers.json")
    if not raw and os.path.exists(path):
        raw = open(path, encoding="utf-8").read()
    chat_env = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not raw and chat_env.startswith("["):  # TELEGRAM_CHAT_ID 칸에 받는 사람 목록(JSON)을 넣어도 된다
        raw = chat_env
    if raw:
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise SystemExit(f"받는 사람 목록(JSON)을 읽지 못했어요: {e}\n따옴표는 반드시 곧은 따옴표(\"), 맨 앞은 [ , 맨 끝은 ] 이어야 해요.")
    elif chat_env:  # 숫자 하나, 또는 쉼표로 여러 개: 111,222 (모두 같은 설정으로 전부 받음)
        data = [{"name": f"기본{i + 1}" if "," in chat_env else "기본", "chat_id": c.strip()} for i, c in enumerate(chat_env.split(",")) if c.strip()]
    else:
        data = []
    subs = []
    for d in data:
        teams = d.get("teams") or []
        presets = A.get("TEAM_PRESETS") or {}
        bad_teams = [t for t in teams if t not in presets]
        if bad_teams:
            raise SystemExit(f"{d.get('name', d.get('chat_id'))}: 알 수 없는 팀 {bad_teams} (가능: {', '.join(presets) or '없음'})")
        team_sectors = [x for t in teams for x in presets[t]]
        explicit, partial = migrate_sectors(d.get("sectors") or [])  # 옛 이름(조선·해운·방산·철강)은 중후장대로
        partial = partial and A["HEAVY_NAME"] not in team_sectors
        sectors = list(dict.fromkeys(explicit + team_sectors)) or SECTORS  # 팀 + 개별 섹터를 합쳐서, 둘 다 없으면 전부
        modes = d.get("modes") or MODES
        bad = [x for x in sectors if x not in A["NEWS_SECTORS"]] + [m for m in modes if m not in MODES]
        if bad:
            raise SystemExit(f"{d.get('name', d.get('chat_id'))}: 알 수 없는 섹터/모드 {bad}")
        subs.append({"name": d.get("name") or str(d["chat_id"]), "chat_id": str(d["chat_id"]), "sectors": list(sectors), "modes": list(modes), "heavy_partial": partial})
    return subs


def show_chats(token):
    """봇에게 말을 건 사람들의 chat id를 보여준다(받는 사람 목록을 만들 때 사용)."""
    res = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=TIMEOUT)
    res.raise_for_status()
    found = {}
    for u in res.json().get("result", []):
        m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        c = m.get("chat") or {}
        if c.get("id") is not None:
            who = c.get("title") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or c.get("username") or ""
            found[c["id"]] = f'{who} (@{c["username"]})' if c.get("username") else who
    if not found:
        print("봇에게 말을 건 사람이 없어요. 받을 사람들이 봇에게 /start 를 보낸 뒤 다시 실행하세요.")
    for cid, who in found.items():
        print(f"chat_id={cid}  {who}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=MODES, help="이 모드만 (시험용)")
    ap.add_argument("--sectors", help="이 섹터만, 쉼표로 (시험용)")
    ap.add_argument("--dry-run", action="store_true", help="보내지 않고 화면에만 출력")
    ap.add_argument("--only", help="이 이름(또는 chat id)의 받는 사람에게만 보냄 (시험용)")
    ap.add_argument("--force", action="store_true", help="이미 보낸 기사도 다시 보내고, 보낸 기록은 남기지 않음 (시험용)")
    ap.add_argument("--who", action="store_true", help="봇에게 말을 건 사람의 chat id 목록을 보여주고 끝냄")
    ap.add_argument("--guide", action="store_true", help="--only와 같이 써서 명령어 안내를 그 사람에게만 시험으로 보냄 (안내는 평소에도 못 받은 사람에게 한 번 자동으로 감)")
    ap.add_argument("--alert", metavar="종류", help="발송이 실패했을 때 관리자에게 알림을 보내고 끝냄 (GitHub Actions의 '실패 시' 단계에서 사용)")
    ap.add_argument("--auto", action="store_true", help="정시 자동 실행: 받는 사람마다 정한 받는 시간(/시간) 밖이면 건너뜀 (수동 실행·시험에는 쓰지 않음)")
    ap.add_argument("--sync", action="store_true", help="받는 사람 목록·섹터·팀을 구글 스크립트(PREFS_URL)로 동기화하고 끝냄")
    ap.add_argument("--commands-only", action="store_true", help="뉴스는 보내지 않고, 알람 신청·받는 사람 명령만 처리하고 끝냄 (30분마다 도는 확인용)")
    args = ap.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if args.alert:
        return send_failure_alert(args.alert, token)
    if args.who:
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN이 필요해요.")
        return show_chats(token)
    base_subs = load_subscribers()
    if args.sync:
        if not os.environ.get("PREFS_URL", "").strip():
            raise SystemExit("PREFS_URL이 없어요. GitHub Secrets에 구글 스크립트 주소(PREFS_URL)를 등록하세요.")
        if not sync_to_script(base_subs):
            raise SystemExit("동기화에 실패했어요(위 메시지 참고).")
        return
    if not args.dry_run and not args.only and not args.mode:  # 15분마다 도는 '단독만' 실행에서는 동기화하지 않음
        sync_to_script(base_subs)  # 발송 때마다 구글 쪽도 최신으로 맞춘다 (실패해도 계속)
    remote = fetch_remote_prefs()
    if remote:  # 승인 버튼으로 들어온 사람을 합친다 (구글 연결이 잠깐 끊겨도 마지막으로 받은 명단을 씀)
        base_subs = merge_approved(base_subs, load_prefs().get("_approved"))
    # PREFS_URL이 있으면: 텔레그램이 Apps Script로 바로 보내므로 여기서는 메시지를 읽지 않는다
    if token and not remote:  # 알람 신청·받는 사람 명령(/섹터 등)을 먼저 처리해서, 이번 발송부터 바뀐 설정을 쓴다
        handle_updates(token, os.environ.get("ADMIN_CHAT_ID", "").strip(), base_subs, args.dry_run)
        if args.only and args.guide:  # 시험: 그 사람에게만 안내를 (받은 적 있어도) 보낸다
            pick = [x for x in base_subs if args.only in (x["name"], x["chat_id"])]
            if not pick:
                raise SystemExit(f"받는 사람 목록에 '{args.only}'이(가) 없어요.")
            send_guides(token, pick, args.dry_run, force=True)
        elif not args.only:  # 평소: 아직 안내를 못 받은 사람에게 한 번 (새로 추가된 사람 포함)
            send_guides(token, base_subs, args.dry_run)
    if args.commands_only:
        print("명령 확인 끝")
        return
    subs = apply_prefs(base_subs, load_prefs(), keep_paused=True)  # 멈춘 사람도 둔다(보내지 않고 기록만)
    print(f"설정 확인: 봇 토큰 {'있음' if token else '없음'} / DIGEST_SUBSCRIBERS {'있음' if os.environ.get('DIGEST_SUBSCRIBERS', '').strip() else '없음'} "
          f"/ TELEGRAM_CHAT_ID {'있음' if os.environ.get('TELEGRAM_CHAT_ID') else '없음'} / 받는 사람 {sum(1 for x in subs if not x.get('paused'))}명 (멈춤 {sum(1 for x in subs if x.get('paused'))}명)")
    if not subs:
        print("※ 받는 사람 목록이 비어 있어요. GitHub Secrets에 DIGEST_SUBSCRIBERS(또는 TELEGRAM_CHAT_ID)가 등록돼 있는지, "
              "이름 철자가 같은지, 'Repository secrets'에 넣었는지 확인하세요.", file=sys.stderr)
    dry = args.dry_run or not (token and subs)
    if dry:
        print("(시험 모드: 텔레그램으로 보내지 않고 출력만 합니다)")
        subs = subs or [{"name": "시험", "chat_id": "0", "sectors": SECTORS, "modes": MODES}]
    if args.only:
        subs = [x for x in subs if args.only in (x["name"], x["chat_id"])]
        if not subs:
            raise SystemExit(f"받는 사람 목록에 '{args.only}'이(가) 없어요. (위 '설정 확인' 줄에서 받는 사람이 0명이면 Secrets 문제예요)")
    for s in (args.sectors.split(",") if args.sectors else []):
        if s not in A["NEWS_SECTORS"]:
            raise SystemExit(f"없는 섹터: {s}")
    # 15분마다 도는 '단독' 확인 때는 공시를 켠 사람에게 잠정실적 공시도 함께 확인해 보낸다 (나머지 공시는 정각에)
    earn_only = args.mode == "단독"
    for sub in subs:  # 시험용 옵션은 모든 받는 사람에게 같이 적용
        if args.sectors:
            sub["sectors"] = [s for s in sub["sectors"] if s in args.sectors.split(",")]
        if args.mode:
            sub["modes"] = [m for m in sub["modes"] if m == args.mode or (earn_only and m == "공시")]
    now = datetime.now(KST)
    if args.auto:  # 사람마다 받는 시간대(기본 8~19시)가 있고, 그 밖의 회차는 건너뛴다. 못 받은 기사는 기록하지 않으니 시작 시각에 모아서 간다
        subs = [x for x in subs if x.get("paused") or (x.get("hours", (HOURS_MIN, HOURS_MAX))[0] <= now.hour <= x.get("hours", (HOURS_MIN, HOURS_MAX))[1]
                and now.weekday() in x.get("days", DAYS_DEFAULT))]
        if not any(not x.get("paused") for x in subs):
            print(f"{now.hour}시({DAY_NAMES[now.weekday()]}): 이 시간에 받을 사람이 없어 건너뜀")
            report_run("단독" if args.mode == "단독" else "전체", 0, 0, 0, skipped=True)
            return
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    seen = {} if args.force else load_seen()
    seen = {k: v for k, v in seen.items() if v >= (now - timedelta(days=7)).isoformat() or k.startswith("migrate|")}  # 7일 지난 기록은 정리 (이전 표시는 남김)

    # 1) 필요한 (모드, 섹터) 조합을 한 번씩만 조회한다 (받는 사람이 겹쳐도 검색은 한 번)
    need = sorted({(m, s) for sub in subs for m in sub["modes"] for s in sub["sectors"]},
                  key=lambda x: (MODES.index(x[0]), SECTORS.index(x[1]) if x[1] in SECTORS else 99))
    found, failed = {}, set()
    feed = None
    for mode, sector in need:
        try:
            if mode == "공시":
                if feed is None:
                    feed = fetch_dart_feed(now, earn_only)
                if feed is False:
                    raise RuntimeError("DART 공시를 받지 못해 건너뜀 (위 오류 참고)")
                found[(mode, sector)] = disclosure_items(feed, sector, earn_only)
            else:
                found[(mode, sector)] = collect(mode, sector, cutoff)
        except Exception as e:
            failed.add((mode, sector))
            if mode == "공시":
                feed = False if feed is None else feed  # 같은 회차에 DART를 되풀이해 부르지 않는다
            print(f"[{mode}/{sector}] 조회 실패: {e}", file=sys.stderr)
    if need and len(failed) == len(need):
        raise SystemExit("모든 조회가 실패했어요.")

    # 1-2) 이번에 누군가에게 새로 갈 잠정실적 공시는 원문에서 매출·영업이익·순이익을 읽어 붙인다
    earn_new = [i for sub in subs if "공시" in sub["modes"] and not sub.get("paused") for s in sub["sectors"]
                for i in found.get(("공시", s), []) if i.get("earn") and f"{sub['chat_id']}|공시|{i['key']}" not in seen]
    seen_dirty = False
    if earn_new:
        try:
            seen_dirty = enrich_earnings(earn_new, seen, now)
        except Exception as e:  # 수치를 못 붙여도 공시 알림은 그대로 보낸다
            print(f"잠정실적 수치 읽기 실패: {e}", file=sys.stderr)

    # 2) 받는 사람마다 자기 섹터·모드에서 아직 안 보낸 기사만 골라 보낸다
    #    모드별 결과가 모두 한 통에 들어갈 만큼 적으면 한 메시지로 합치고, 많으면 모드별로 따로 보낸다
    send_errors = 0
    delivered = 0  # 이번에 새 소식을 받은 사람 수
    for sub in subs:
        chat = sub["chat_id"]
        parts = []  # [(모드, 섹션들, 새 기록 키들)]
        base_key = f"migrate|heavy|{chat}"
        baseline = bool(sub.get("heavy_partial")) and base_key not in seen and not dry and not args.force and not args.only
        quiet_keys = []  # 중후장대로 묶이기 전 일부만 받던 사람: 넓어진 범위의 기존 기사는 보내지 않고 '받은 것으로만' 기록
        for mode in sub["modes"]:
            sections, placed, new_keys = [], set(), []
            for sector in sub["sectors"]:
                if (mode, sector) not in found:
                    continue
                fresh = []
                since = sub.get("since")
                for i in found[(mode, sector)]:
                    if i.get("hold"):  # 원문이 올라오면 수치와 함께 보낸다 (다음 회차)
                        continue
                    if since and ((i.get("dt") and i["dt"] < since) or (i.get("day") and i["day"] < since.strftime("%Y%m%d"))):
                        continue  # /켜기 하기 전에 나온 기사·공시(공시는 날짜 기준)는 보내지 않는다
                    k = f"{chat}|{mode}|{i['key'] if mode == '공시' else title_key(i['title'])}"
                    if k in seen or k in placed:
                        continue
                    placed.add(k)
                    fresh.append(i)
                clusters = [{"rep": i, "others": []} for i in fresh] if mode == "공시" else cluster_news(fresh)
                if baseline and sector == A["HEAVY_NAME"]:
                    for c in clusters:
                        quiet_keys += [f"{chat}|{mode}|{it['key'] if mode == '공시' else title_key(it['title'])}" for it in [c["rep"]] + c["others"]]
                    continue
                for c in clusters:
                    new_keys += [f"{chat}|{mode}|{it['key'] if mode == '공시' else title_key(it['title'])}" for it in [c["rep"]] + c["others"]]
                if clusters:
                    sections.append((sector, clusters[:MAX_PER_SECTOR], max(0, len(clusters) - MAX_PER_SECTOR)))
            print(f"[{sub['name']}/{mode}] 새 묶음 {sum(len(c) + h for _, c, h in sections)}건 ({len(sections)}개 섹터)")
            if sections:
                parts.append((mode, sections, new_keys))
        if baseline:
            for k in quiet_keys:
                seen[k] = now.isoformat()
            seen[base_key] = now.isoformat()
            seen_dirty = True
            print(f"[{sub['name']}] 섹터가 중후장대로 묶여 기존 기사 {len(quiet_keys)}건은 알림 없이 기록만 했어요")
        if not parts:
            continue
        if sub.get("paused"):  # 멈춘 사람: 보내지 않고 '받은 것으로만' 기록한다 -> 켜면 그 뒤에 나온 것만 받는다
            if not dry and not args.force:
                for _, _, new_keys in parts:
                    for k in new_keys:
                        seen[k] = now.isoformat()
                seen_dirty = True
            print(f"[{sub['name']}] 멈춤 상태라 보내지 않고 기록만 했어요")
            continue
        combined = combine_messages(now, parts, earn_only)
        try:
            for text in ([combined] if combined else [t for mode, sections, _ in parts for t in build_messages(mode, now, sections, earn_only)]):
                if dry:
                    print("-" * 40 + f"\n[{sub['name']}]\n" + text)
                else:
                    send_safely(text, token, chat)
            delivered += 1
            if not dry and not args.force:  # 보낸 뒤에만 기록해야 실패한 회차의 기사가 다음에 다시 시도됨
                for _, _, new_keys in parts:
                    for k in new_keys:
                        seen[k] = now.isoformat()
                with open(SEEN_FILE, "w", encoding="utf-8") as f:
                    json.dump(seen, f, ensure_ascii=False)
        except Exception as e:  # 한 명이 실패해도(봇 차단 등) 다른 사람에게는 계속 보낸다
            send_errors += 1
            print(f"[{sub['name']}] 발송 실패: {e}", file=sys.stderr)
    if seen_dirty and not dry and not args.force:  # 원문을 기다리기 시작한 시각을 남긴다 (보낸 사람이 없었어도)
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False)
    if not dry and not args.only and not args.force:
        report_run("단독" if args.mode == "단독" else "전체", sum(1 for x in subs if not x.get("paused")), delivered, send_errors)
    if send_errors:
        raise SystemExit(f"{send_errors}건의 발송이 실패했어요. 위 로그를 확인하세요.")


if __name__ == "__main__":
    main()
