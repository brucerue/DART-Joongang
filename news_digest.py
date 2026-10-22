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
import json
import os
import re
import sys
import unicodedata
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

# ---------- 설정: 받고 싶은 섹터·조회 방식 ----------
SECTORS = ["자동차", "배터리", "조선·해운", "방산", "철강", "정유·석화", "에너지·소재"]
MODES = ["통신", "단독", "공시"]
LOOKBACK_HOURS = 36  # 이 시간 안에 올라온 기사만 (이미 보낸 기사는 기록으로 걸러냄)
MAX_PER_SECTOR = 100  # 섹터마다 메시지에 싣는 최대 묶음 수 (넘으면 '외 N건'). 길면 텔레그램 메시지가 여러 통으로 나뉨
USE_NAVER = os.environ.get("USE_NAVER", "").strip().lower() in ("1", "true", "yes", "on")  # 기본은 꺼짐(구글 뉴스만). 네이버 보충을 쓰려면 환경변수 USE_NAVER=1 (워크플로 env에 추가)
NAVER_MIN = 5  # 단독 모드: 구글에서 찾은 단독 기사가 이보다 적으면 네이버로 보충
WIRE_DOMAINS = ("yna.co.kr",)  # 통신 모드: 연합뉴스만 (늘리려면 "newsis.com", "news1.kr" 추가)
SITE_CHUNK = 8
TIMEOUT = 20
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
DISCLOSURE_DAYS = 2  # 공시 모드: 오늘과 어제 접수분을 확인 (이미 보낸 공시는 기록으로 걸러냄)
MAX_DART_PAGES = 30  # 시장마다 하루 최대 3,000건까지만 확인
DISCLOSURE_SKIP = []  # 알림에서 뺄 보고서명 단어 (예: ["소유상황"] 이면 임원·주요주주 지분 보고는 안 보냄)
# -----------------------------------------------------


def load_app_data():
    """app.py를 실행하지 않고(Streamlit 없이) 필요한 상수와 함수만 읽어 온다."""
    src = open(APP, encoding="utf-8").read()
    tree = ast.parse(src)
    consts, funcs = {}, {}
    want_consts = {"LETTER_NAMES", "WORD_READINGS", "PUBLISHERS", "SIMILAR_TITLE", "NEWS_ALIASES", "NAME_READINGS",
                   "NEWS_SECTORS", "NEWS_ALWAYS_SEARCH", "INDUSTRY_GROUPS", "MAJOR_DISCLOSURES", "PUBLISHER_BLOCK", "ALARM_SECTORS", "ALARM_MODES"}
    want_funcs = {"name_key", "is_exclusive", "title_key", "publisher_of", "cluster_news", "disclosure_key"}
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
    ns = {"re": re, "unicodedata": unicodedata, "difflib": difflib, "urlparse": urlparse, **consts}
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

    def one(term):
        out = []
        for page in range(3):
            res = requests.get("https://openapi.naver.com/v1/search/news.json", headers=headers, timeout=TIMEOUT,
                               params={"query": f"{term} 단독", "display": 100, "start": 1 + page * 100, "sort": "date"})
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
def fetch_dart_day(api_key, ymd, cls):
    """하루치 전체 공시(한 시장). DART 공시 목록 API를 페이지별로 받는다. 데이터 없음(013)은 빈 목록."""
    def page(n):
        res = requests.get(DART_LIST_URL, timeout=TIMEOUT, params={
            "crtfc_key": api_key, "bgn_de": ymd, "end_de": ymd, "corp_cls": cls,
            "last_reprt_at": "N", "page_no": n, "page_count": 100})
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


def fetch_dart_feed(now):
    """오늘·어제(한국 날짜)에 접수된 전 상장사(코스피·코스닥) 공시. 한 번 받아 모든 섹터가 같이 쓴다."""
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DART_API_KEY가 없어요. GitHub Secrets에 DART API 키를 DART_API_KEY로 등록하세요.")
    days = [(now - timedelta(days=d)).strftime("%Y%m%d") for d in range(DISCLOSURE_DAYS)]
    rows = []
    for ymd in days:
        for cls in ("Y", "K"):
            rows.extend(fetch_dart_day(api_key, ymd, cls))
    return rows


def disclosure_items(feed, sector):
    """시장 전체 공시 중 섹터 기업의 '주요 공시'만 골라, 뉴스 항목과 같은 모양으로 돌려준다 (최신순)."""
    keys = {name_key(n) for n in A["INDUSTRY_GROUPS"].get(sector, [])}
    major = [A["disclosure_key"](w) for ws in A["MAJOR_DISCLOSURES"].values() for w in ws]
    skip = [A["disclosure_key"](w) for w in DISCLOSURE_SKIP]
    out = []
    for r in feed:
        if name_key(r.get("corp_name", "")) not in keys:
            continue
        rk = A["disclosure_key"](r.get("report_nm", ""))
        if not any(m in rk for m in major) or any(x in rk for x in skip):
            continue
        no = str(r.get("rcept_no", ""))
        out.append({"title": f"[{r.get('corp_name', '')}] {str(r.get('report_nm', '')).strip()}", "url": DART_VIEW_URL.format(no),
                    "publisher": "DART", "key": no})
    out.sort(key=lambda x: x["key"], reverse=True)  # 접수번호가 클수록 나중에 접수
    return out


def collect(mode, sector, cutoff):
    search, owner = sector_terms(sector)
    if mode == "통신":
        items, err = google_search(search, list(WIRE_DOMAINS), "", cutoff)
        if err:
            raise RuntimeError(f"구글 뉴스 오류: {err}")
        items = [i for i in items if i["category"] == "통신사" and i["publisher"] == "연합뉴스"]
    else:
        items, err = google_search(search, list(A["PUBLISHERS"]), "단독", cutoff)
        few = sum(is_exclusive(i["title"]) for i in items) < NAVER_MIN
        if USE_NAVER and (err or few):
            have = {title_key(i["title"]) for i in items}
            extra = [n for n in naver_search(search, cutoff) if title_key(n["title"]) not in have]
            if err and not extra:  # 구글이 막혔는데 네이버도 못 쓰면 실패로 기록
                raise RuntimeError(f"구글 뉴스 오류: {err}")
            items += extra
        elif err:
            raise RuntimeError(f"구글 뉴스 오류: {err}")
        items = [i for i in items if i["category"] and is_exclusive(i["title"])]
    keys = {n: name_key(n) for n in owner}
    out = []
    for i in items:
        tk = name_key(i["title"])
        hits = [n for n, k in keys.items() if k in tk]  # 제목에 검색어(줄임말 포함)가 있는 기사만
        if hits:
            out.append(dict(i, tags=list(dict.fromkeys(owner[h] for h in hits))))
    return out


# ---------- 메시지 ----------
RAW_LIMIT = 6000  # 링크 주소까지 포함한 메시지 길이 상한 (구글 뉴스 링크가 길어 텔레그램의 ENTITIES_TOO_LONG을 막기 위함)


def vlen(text):
    """텔레그램 글자 수 한도(4,096자)는 화면에 보이는 글자만 센다(링크 주소·HTML 태그는 제외)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def build_messages(mode, now, sections):
    """sections: [(섹터, [묶음...], 숨겨진 묶음 수)] -> 텔레그램 메시지 목록(보이는 글자 기준 4,000자 이하, 넘으면 여러 통)"""
    limit = 4000
    label = "📋 주요 공시" if mode == "공시" else f"📰 산업 뉴스 · {mode}"
    head = f"<b>{label} · {now:%m/%d %H:%M}</b>"
    cont = head + " (이어서)"
    msgs, cur = [], head

    def fresh():
        return cur in (head, cont)

    def flush():
        nonlocal cur
        msgs.append(cur)
        cur = cont

    for sector, clusters, hidden in sections:
        title = f"<b>[{html.escape(sector)}]</b>"
        lines = []
        for c in clusters:
            it = c["rep"]
            more = f" (+{len(c['others'])}건)" if c["others"] else ""
            lines.append(f'• <a href="{html.escape(it["url"], quote=True)}">{html.escape(it["title"])}</a> · {html.escape(it["publisher"])}{more}')
        if hidden:
            lines.append(f"  … 외 {hidden}건")
        head_line = title
        for ln in lines:  # 줄 단위로 채우고, 넘치면 새 메시지에서 섹터 제목을 다시 붙여 이어간다
            if head_line is not None:
                if not fresh() and (vlen(cur) + 2 + vlen(head_line) + 1 + vlen(ln) > limit or len(cur) + 2 + len(head_line) + 1 + len(ln) > RAW_LIMIT):
                    flush()
                cur += ("\n" if fresh() else "\n\n") + head_line
                head_line = None
            if vlen(cur) + 1 + vlen(ln) > limit or len(cur) + 1 + len(ln) > RAW_LIMIT:
                flush()
                cur += "\n" + title + " (이어서)"
            cur += "\n" + ln
    if not fresh():
        msgs.append(cur)
    return msgs


def combine_messages(now, parts):
    """모드가 둘 이상이고 전부 합쳐도 한 메시지에 들어가면 하나로 합친 글을, 아니면 None(모드별로 따로 보냄).
    합친 글은 간략하게 보이도록 섹터 제목 없이 기사만 모아 보여 준다(같은 기사가 여러 섹터에 걸리면 한 번만)."""
    if len(parts) < 2:
        return None
    names = {"통신": "통신", "단독": "단독", "공시": "📋 주요 공시"}
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
                lines.append(f'• <a href="{html.escape(it["url"], quote=True)}">{html.escape(it["title"])}</a> · {html.escape(it["publisher"])}{more}')
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


def handle_signups(token, admin_chat, dry):
    """봇에게 온 알람 신청(/start s…m…)을 읽어 관리자(나)에게 알려 주고, 신청자에게는 접수 안내를 보낸다.
    신청 내용은 앱의 '알람 받기'가 t.me 링크의 start 값에 담아 둔 것. 받는 사람 목록(Secrets)에 넣는 일은 직접 한다."""
    if not admin_chat:
        print("(신청 알림: ADMIN_CHAT_ID가 없어 건너뜀)")
        return
    try:
        res = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=TIMEOUT,
                           params={"timeout": 0, "allowed_updates": json.dumps(["message"])})
        updates = res.json().get("result", []) if res.status_code == 200 else []
    except Exception as e:  # 신청 확인이 안 돼도 뉴스 발송은 계속한다
        print(f"(신청 확인 실패: {type(e).__name__})", file=sys.stderr)
        return
    sectors, modes = A.get("ALARM_SECTORS") or [], A.get("ALARM_MODES") or []
    last_id = None
    for u in updates:
        last_id = u["update_id"]
        msg = u.get("message") or {}
        m = re.match(r"^/start(?:@\w+)?\s+s([01]+)m([01]+)\s*$", (msg.get("text") or "").strip())
        if not m or len(m.group(1)) != len(sectors) or len(m.group(2)) != len(modes):
            continue
        chat = msg.get("chat") or {}
        cid = str(chat.get("id", ""))
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
    if last_id is not None and not dry:  # 처리한 알림은 확인 처리해서 다음에 다시 읽지 않게 한다
        try:
            requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=TIMEOUT, params={"offset": last_id + 1, "timeout": 0})
        except Exception:
            pass


def load_seen():
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_subscribers():
    """받는 사람 목록. 우선순위: 환경변수 DIGEST_SUBSCRIBERS(JSON) > subscribers.json 파일 > TELEGRAM_CHAT_ID 한 명.
    각 항목: {"name": "이름", "chat_id": "123", "sectors": ["자동차", ...], "modes": ["통신", "단독"]}
    sectors·modes를 생략하면 SECTORS·MODES 전부를 받는다."""
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
        sectors = d.get("sectors") or SECTORS
        modes = d.get("modes") or MODES
        bad = [x for x in sectors if x not in A["NEWS_SECTORS"]] + [m for m in modes if m not in MODES]
        if bad:
            raise SystemExit(f"{d.get('name', d.get('chat_id'))}: 알 수 없는 섹터/모드 {bad}")
        subs.append({"name": d.get("name") or str(d["chat_id"]), "chat_id": str(d["chat_id"]), "sectors": list(sectors), "modes": list(modes)})
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
    args = ap.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if args.who:
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN이 필요해요.")
        return show_chats(token)
    subs = load_subscribers()
    print(f"설정 확인: 봇 토큰 {'있음' if token else '없음'} / DIGEST_SUBSCRIBERS {'있음' if os.environ.get('DIGEST_SUBSCRIBERS', '').strip() else '없음'} "
          f"/ TELEGRAM_CHAT_ID {'있음' if os.environ.get('TELEGRAM_CHAT_ID') else '없음'} / 받는 사람 {len(subs)}명")
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
    for sub in subs:  # 시험용 옵션은 모든 받는 사람에게 같이 적용
        if args.sectors:
            sub["sectors"] = [s for s in sub["sectors"] if s in args.sectors.split(",")]
        if args.mode:
            sub["modes"] = [m for m in sub["modes"] if m == args.mode]
    if token:
        handle_signups(token, os.environ.get("ADMIN_CHAT_ID", "").strip(), dry)
    now = datetime.now(KST)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    seen = {} if args.force else load_seen()
    seen = {k: v for k, v in seen.items() if v >= (now - timedelta(days=7)).isoformat()}  # 7일 지난 기록은 정리

    # 1) 필요한 (모드, 섹터) 조합을 한 번씩만 조회한다 (받는 사람이 겹쳐도 검색은 한 번)
    need = sorted({(m, s) for sub in subs for m in sub["modes"] for s in sub["sectors"]},
                  key=lambda x: (MODES.index(x[0]), SECTORS.index(x[1]) if x[1] in SECTORS else 99))
    found, failed = {}, set()
    feed = None
    for mode, sector in need:
        try:
            if mode == "공시":
                if feed is None:
                    feed = fetch_dart_feed(now)
                if feed is False:
                    raise RuntimeError("DART 공시를 받지 못해 건너뜀 (위 오류 참고)")
                found[(mode, sector)] = disclosure_items(feed, sector)
            else:
                found[(mode, sector)] = collect(mode, sector, cutoff)
        except Exception as e:
            failed.add((mode, sector))
            if mode == "공시":
                feed = False if feed is None else feed  # 같은 회차에 DART를 되풀이해 부르지 않는다
            print(f"[{mode}/{sector}] 조회 실패: {e}", file=sys.stderr)
    if need and len(failed) == len(need):
        raise SystemExit("모든 조회가 실패했어요.")

    # 2) 받는 사람마다 자기 섹터·모드에서 아직 안 보낸 기사만 골라 보낸다
    #    모드별 결과가 모두 한 통에 들어갈 만큼 적으면 한 메시지로 합치고, 많으면 모드별로 따로 보낸다
    send_errors = 0
    for sub in subs:
        chat = sub["chat_id"]
        parts = []  # [(모드, 섹션들, 새 기록 키들)]
        for mode in sub["modes"]:
            sections, placed, new_keys = [], set(), []
            for sector in sub["sectors"]:
                if (mode, sector) not in found:
                    continue
                fresh = []
                for i in found[(mode, sector)]:
                    k = f"{chat}|{mode}|{i['key'] if mode == '공시' else title_key(i['title'])}"
                    if k in seen or k in placed:
                        continue
                    placed.add(k)
                    fresh.append(i)
                clusters = [{"rep": i, "others": []} for i in fresh] if mode == "공시" else cluster_news(fresh)
                for c in clusters:
                    new_keys += [f"{chat}|{mode}|{it['key'] if mode == '공시' else title_key(it['title'])}" for it in [c["rep"]] + c["others"]]
                if clusters:
                    sections.append((sector, clusters[:MAX_PER_SECTOR], max(0, len(clusters) - MAX_PER_SECTOR)))
            print(f"[{sub['name']}/{mode}] 새 묶음 {sum(len(c) + h for _, c, h in sections)}건 ({len(sections)}개 섹터)")
            if sections:
                parts.append((mode, sections, new_keys))
        if not parts:
            continue
        combined = combine_messages(now, parts)
        try:
            for text in ([combined] if combined else [t for mode, sections, _ in parts for t in build_messages(mode, now, sections)]):
                if dry:
                    print("-" * 40 + f"\n[{sub['name']}]\n" + text)
                else:
                    send_safely(text, token, chat)
            if not dry and not args.force:  # 보낸 뒤에만 기록해야 실패한 회차의 기사가 다음에 다시 시도됨
                for _, _, new_keys in parts:
                    for k in new_keys:
                        seen[k] = now.isoformat()
                with open(SEEN_FILE, "w", encoding="utf-8") as f:
                    json.dump(seen, f, ensure_ascii=False)
        except Exception as e:  # 한 명이 실패해도(봇 차단 등) 다른 사람에게는 계속 보낸다
            send_errors += 1
            print(f"[{sub['name']}] 발송 실패: {e}", file=sys.stderr)
    if send_errors:
        raise SystemExit(f"{send_errors}건의 발송이 실패했어요. 위 로그를 확인하세요.")


if __name__ == "__main__":
    main()
