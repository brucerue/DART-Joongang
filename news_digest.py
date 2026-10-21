"""산업 뉴스 자동 발송: 정해진 시각에 섹터별 통신/단독 뉴스를 찾아 텔레그램으로 보낸다.

- GitHub Actions(.github/workflows/news_digest.yml)가 하루 세 번(07:00, 13:00, 17:00 한국 시간) 실행한다.
- 섹터·기업·언론사·별칭 같은 데이터는 같은 폴더의 app.py에서 그대로 읽어 온다(앱을 고치면 여기도 따라 바뀜).
- 이미 보낸 기사는 digest_seen.json에 기록해 두고 다시 보내지 않는다.

필요한 환경변수(GitHub 저장소 Settings > Secrets):
  TELEGRAM_BOT_TOKEN                             텔레그램 봇 토큰 (없으면 화면에만 출력하는 시험 모드)
  DIGEST_SUBSCRIBERS                             받는 사람 목록(JSON). 예:
      [{"name":"나상현","chat_id":"111","sectors":["자동차","배터리"],"modes":["통신","단독"]},
       {"name":"김OO","chat_id":"222","sectors":["방산","조선·해운"]}]
      sectors·modes를 생략하면 전부 받음. (없으면 subscribers.json 파일, 그것도 없으면 TELEGRAM_CHAT_ID 한 명)
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET           (선택) 단독 모드에서 구글 결과가 적을 때 보충
  DIGEST_SEND_EMPTY=1                            (선택) 새 기사가 없어도 '없음' 알림을 보냄
사용: python news_digest.py [--mode 통신|단독] [--sectors 자동차,배터리] [--dry-run]
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
MODES = ["통신", "단독"]
LOOKBACK_HOURS = 36  # 이 시간 안에 올라온 기사만 (이미 보낸 기사는 기록으로 걸러냄)
MAX_PER_SECTOR = 12  # 섹터마다 메시지에 싣는 최대 묶음 수 (넘으면 '외 N건')
NAVER_MIN = 5  # 단독 모드: 구글에서 찾은 단독 기사가 이보다 적으면 네이버로 보충
WIRE_DOMAINS = ("yna.co.kr", "newsis.com", "news1.kr")
SITE_CHUNK = 8
TIMEOUT = 20
# -----------------------------------------------------


def load_app_data():
    """app.py를 실행하지 않고(Streamlit 없이) 필요한 상수와 함수만 읽어 온다."""
    src = open(APP, encoding="utf-8").read()
    tree = ast.parse(src)
    consts, funcs = {}, {}
    want_consts = {"LETTER_NAMES", "WORD_READINGS", "PUBLISHERS", "SIMILAR_TITLE", "NEWS_ALIASES", "NAME_READINGS",
                   "NEWS_SECTORS", "NEWS_ALWAYS_SEARCH"}
    want_funcs = {"name_key", "is_exclusive", "title_key", "publisher_of", "cluster_news"}
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
def collect(mode, sector, cutoff):
    search, owner = sector_terms(sector)
    if mode == "통신":
        items, err = google_search(search, list(WIRE_DOMAINS), "", cutoff)
        if err:
            raise RuntimeError(f"구글 뉴스 오류: {err}")
        items = [i for i in items if i["category"] == "통신사"]
    else:
        items, err = google_search(search, list(A["PUBLISHERS"]), "단독", cutoff)
        few = sum(is_exclusive(i["title"]) for i in items) < NAVER_MIN
        if err or few:
            have = {title_key(i["title"]) for i in items}
            extra = [n for n in naver_search(search, cutoff) if title_key(n["title"]) not in have]
            if err and not extra:  # 구글이 막혔는데 네이버도 못 쓰면 실패로 기록
                raise RuntimeError(f"구글 뉴스 오류: {err}")
            items += extra
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
def build_messages(mode, now, sections):
    """sections: [(섹터, [묶음...], 숨겨진 묶음 수)] -> 텔레그램 메시지 목록(각 4,000자 이하)"""
    head = f"<b>📰 산업 뉴스 · {mode} · {now:%m/%d %H:%M}</b>"
    blocks = []
    for sector, clusters, hidden in sections:
        lines = [f"\n<b>[{html.escape(sector)}]</b>"]
        for c in clusters:
            it = c["rep"]
            more = f" (+{len(c['others'])}건)" if c["others"] else ""
            lines.append(f'• <a href="{html.escape(it["url"], quote=True)}">{html.escape(it["title"])}</a> · {html.escape(it["publisher"])}{more}')
        if hidden:
            lines.append(f"  … 외 {hidden}건")
        blocks.append("\n".join(lines))
    msgs, cur = [], head
    for b in blocks:
        if len(cur) + len(b) > 3800 and cur != head:
            msgs.append(cur)
            cur = head + " (이어서)"
        while len(cur) + len(b) > 3800:  # 한 섹터만으로 넘치면 줄 단위로 자름
            lines = b.split("\n")
            keep = [lines[0]]
            for ln in lines[1:]:
                if len(cur) + len("\n".join(keep + [ln])) > 3800:
                    break
                keep.append(ln)
            msgs.append(cur + "\n".join(keep))
            b = "\n".join([lines[0] + " (이어서)"] + lines[len(keep):])
            cur = head + " (이어서)"
        cur += b
    if cur != head:
        msgs.append(cur)
    return msgs


def send_telegram(text, token, chat_id):
    res = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=TIMEOUT,
                        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    if res.status_code != 200:
        raise RuntimeError(f"텔레그램 발송 실패 [{res.status_code}] {res.text[:200]}")


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
    if raw:
        data = json.loads(raw)
    elif os.environ.get("TELEGRAM_CHAT_ID"):
        data = [{"name": "기본", "chat_id": os.environ["TELEGRAM_CHAT_ID"]}]
    else:
        data = []
    subs = []
    for d in data:
        sectors = d.get("sectors") or SECTORS
        modes = d.get("modes") or MODES
        bad = [x for x in sectors if x not in A["NEWS_SECTORS"]] + [m for m in modes if m not in ("통신", "단독")]
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
    dry = args.dry_run or not (token and subs)
    if dry:
        print("(시험 모드: 텔레그램으로 보내지 않고 출력만 합니다)")
        subs = subs or [{"name": "시험", "chat_id": "0", "sectors": SECTORS, "modes": MODES}]
    if args.only:
        subs = [x for x in subs if args.only in (x["name"], x["chat_id"])]
        if not subs:
            raise SystemExit(f"받는 사람 목록에 '{args.only}'이(가) 없어요.")
    for s in (args.sectors.split(",") if args.sectors else []):
        if s not in A["NEWS_SECTORS"]:
            raise SystemExit(f"없는 섹터: {s}")
    for sub in subs:  # 시험용 옵션은 모든 받는 사람에게 같이 적용
        if args.sectors:
            sub["sectors"] = [s for s in sub["sectors"] if s in args.sectors.split(",")]
        if args.mode:
            sub["modes"] = [m for m in sub["modes"] if m == args.mode]
    now = datetime.now(KST)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    seen = {} if args.force else load_seen()
    seen = {k: v for k, v in seen.items() if v >= (now - timedelta(days=7)).isoformat()}  # 7일 지난 기록은 정리

    # 1) 필요한 (모드, 섹터) 조합을 한 번씩만 조회한다 (받는 사람이 겹쳐도 검색은 한 번)
    need = sorted({(m, s) for sub in subs for m in sub["modes"] for s in sub["sectors"]},
                  key=lambda x: (MODES.index(x[0]), SECTORS.index(x[1]) if x[1] in SECTORS else 99))
    found, failed = {}, set()
    for mode, sector in need:
        try:
            found[(mode, sector)] = collect(mode, sector, cutoff)
        except Exception as e:
            failed.add((mode, sector))
            print(f"[{mode}/{sector}] 조회 실패: {e}", file=sys.stderr)
    if need and len(failed) == len(need):
        raise SystemExit("모든 조회가 실패했어요.")

    # 2) 받는 사람마다 자기 섹터·모드에서 아직 안 보낸 기사만 골라 보낸다
    send_errors = 0
    for sub in subs:
        chat = sub["chat_id"]
        for mode in sub["modes"]:
            sections, placed, new_keys = [], set(), []
            for sector in sub["sectors"]:
                if (mode, sector) not in found:
                    continue
                fresh = []
                for i in found[(mode, sector)]:
                    k = f"{chat}|{mode}|{title_key(i['title'])}"
                    if k in seen or k in placed:
                        continue
                    placed.add(k)
                    fresh.append(i)
                clusters = cluster_news(fresh)
                for c in clusters:
                    new_keys += [f"{chat}|{mode}|{title_key(it['title'])}" for it in [c["rep"]] + c["others"]]
                if clusters:
                    sections.append((sector, clusters[:MAX_PER_SECTOR], max(0, len(clusters) - MAX_PER_SECTOR)))
            print(f"[{sub['name']}/{mode}] 새 묶음 {sum(len(c) + h for _, c, h in sections)}건 ({len(sections)}개 섹터)")
            try:
                if not sections:
                    if os.environ.get("DIGEST_SEND_EMPTY") == "1" and not dry:
                        send_telegram(f"📰 산업 뉴스 · {mode} · {now:%m/%d %H:%M}\n새 기사가 없어요.", token, chat)
                    continue
                for text in build_messages(mode, now, sections):
                    if dry:
                        print("-" * 40 + f"\n[{sub['name']}]\n" + text)
                    else:
                        send_telegram(text, token, chat)
                if not dry and not args.force:  # 보낸 뒤에만 기록해야 실패한 회차의 기사가 다음에 다시 시도됨
                    for k in new_keys:
                        seen[k] = now.isoformat()
                    with open(SEEN_FILE, "w", encoding="utf-8") as f:
                        json.dump(seen, f, ensure_ascii=False)
            except Exception as e:  # 한 명이 실패해도(봇 차단 등) 다른 사람에게는 계속 보낸다
                send_errors += 1
                print(f"[{sub['name']}/{mode}] 발송 실패: {e}", file=sys.stderr)
    if send_errors:
        raise SystemExit(f"{send_errors}건의 발송이 실패했어요. 위 로그를 확인하세요.")


if __name__ == "__main__":
    main()
