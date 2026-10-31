"""외신 검색 계산 (화면 없음): 앱(app.py)의 외신 탭과 수집기(collect_data.py)가 같은 규칙을 쓰도록 모아 둔 것.
- 기업별: 영문 별칭(GNEWS_ALIASES)으로 넓게 찾고, 제목엔 별칭이 낱말 그대로 든 기사만
- 한국 관련: (KOREA_WORDS) AND (섹터 낱말 KOREA_TOPICS)로 본문까지 찾고, 북한 기사(KOREA_EXCLUDE)·기사 아닌 것(GN_JUNK_RE)은 뺀다
- 한글 제목: AI 번역 문구(GN_KO_SYSTEM, ko_prompt)와 결과 읽기(parse_ko), 수집기용 Gemini 번역(gemini_translate)
"""
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

KST = timezone(timedelta(hours=9))
GNEWS_URL = "https://news.google.com/rss/search"
GLOBAL_KEEP_DAYS = 7  # 모아 두는 외신 기간 (화면 기간 선택 최대 7일)

GNEWS_OUTLETS = {  # 화면 이름 -> 도메인
    "WSJ": "wsj.com", "Bloomberg": "bloomberg.com", "NYT": "nytimes.com", "Reuters": "reuters.com",
    "FT": "ft.com", "CNBC": "cnbc.com", "AP": "apnews.com",
    "Nikkei Asia": "asia.nikkei.com", "Economist": "economist.com", "SCMP": "scmp.com", "Politico": "politico.com", "Axios": "axios.com",
}
SITE_CHUNK = 6  # 검색어 하나에 넣는 언론사 수 (구글은 너무 긴 검색어를 잘라서, 언론사가 많으면 나눠 검색해 합친다)


def domain_chunks(domains):
    """언론사 도메인들 -> SITE_CHUNK개씩 묶음"""
    domains = list(domains)
    return [tuple(domains[i:i + SITE_CHUNK]) for i in range(0, len(domains), SITE_CHUNK)]


def merge_items(lists):
    """여러 묶음 검색 결과 -> 주소로 중복을 뺀 최신순 목록"""
    out, seen = [], set()
    for lst in lists:
        for it in lst or []:
            if it["url"] not in seen:
                seen.add(it["url"])
                out.append(it)
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out
# 외신 기업 별칭: 검색은 별칭 모두로 넓게, 제목 판정은 별칭이 낱말 그대로 들어간 것만 (없는 기업은 영문 이름 하나)
GNEWS_ALIASES = {
    "Samsung Electronics": ["Samsung Electronics", "Samsung"], "SK hynix": ["SK hynix", "SK Hynix", "Hynix"],
    "Hyundai Motor": ["Hyundai Motor", "Hyundai Motors"], "Kia": ["Kia"], "LG Energy Solution": ["LG Energy Solution", "LGES"],
    "LG Electronics": ["LG Electronics"], "Samsung SDI": ["Samsung SDI"], "POSCO Holdings": ["POSCO"], "Hanwha Aerospace": ["Hanwha Aerospace"],
    "KEPCO": ["KEPCO", "Korea Electric Power"], "KT Corp": ["KT Corp"], "Samsung C&T": ["Samsung C&T"],
    "TSMC": ["TSMC", "Taiwan Semiconductor"], "Alphabet": ["Alphabet", "Google"], "Meta": ["Meta Platforms", "Meta", "Facebook"],
    "General Motors": ["General Motors", "GM"], "Exxon Mobil": ["Exxon Mobil", "ExxonMobil", "Exxon"], "RTX": ["RTX", "Raytheon"],
    "Lockheed Martin": ["Lockheed Martin", "Lockheed"], "Northrop Grumman": ["Northrop Grumman", "Northrop"],
    "Johnson & Johnson": ["Johnson & Johnson", "J&J"], "Eli Lilly": ["Eli Lilly", "Lilly"], "SAIC Motor": ["SAIC Motor", "SAIC"],
    "Hua Hong Semiconductor": ["Hua Hong"], "ArcelorMittal": ["ArcelorMittal", "Arcelor"], "Great Wall Motor": ["Great Wall Motor"],
    "Mitsubishi Motors": ["Mitsubishi Motors"], "Micron": ["Micron"], "Intel": ["Intel"], "Nvidia": ["Nvidia"],
}
# '한국 관련' 외신: 한국 낱말 + 섹터 낱말이 함께 든 기사를 본문까지 찾는다 (구글은 본문도 색인)
KOREA_WORDS = ["South Korea", "South Korean", "Korean", "Seoul"]
GN_JUNK_RE = re.compile(r"Market Talk|Roundup|News Quiz|Breaking News|Live Updates?|Newsletter|Podcast|Morning Bid|Stocks making the biggest moves|^Video|Crossword|World in Brief|The Morning|Evening Briefing|Daily Briefing", re.I)
KOREA_EXCLUDE = '-"North Korea" -"North Korean" -Pyongyang -"Kim Jong"'
KOREA_TOPICS = {
    "자동차": ["automaker", "carmaker", "auto", "cars", "EV"], "배터리": ["battery", "batteries", "cathode"],
    "중후장대": ["shipbuilder", "shipbuilding", "shipyard", "defense", "arms", "steel"], "정유·석화": ["refiner", "refinery", "petrochemical", "oil"],
    "에너지": ["nuclear", "reactor", "power grid", "energy", "LNG"], "반도체": ["chip", "chips", "chipmaker", "semiconductor", "memory"],
    "IT·통신": ["telecom", "internet", "AI", "data center"], "전자": ["electronics", "smartphone", "display", "appliance"],
    "항공": ["airline", "aviation", "aircraft"], "재계": ["chaebol", "conglomerate", "tariff", "trade deal", "investment"],
}
KOREA_TITLE_RE = re.compile(r"(?<![A-Za-z])(South\s+Korea|Korea|Korean|Koreans|Seoul|Samsung|Hyundai|SK\s+hynix|Hynix|LG|Hanwha|POSCO|Kia|KEPCO|Naver|Kakao)(?![A-Za-z])", re.I)
GN_KO_SYSTEM = "너는 중앙일보 산업부 기자를 돕는 번역가다. 영문 외신 기사 제목을 한국 신문 제목처럼 짧고 정확하게 옮긴다. 회사·인명은 한국 언론 표기(엔비디아, 마이크론, TSMC 등)를 쓰고, 없는 내용을 더하지 않는다."
GN_KO_CHUNK = 40


GN_JUNK_PUBLISHER_RE = re.compile(r"Cooking|Wirecutter|Games|Crossword", re.I)  # 같은 도메인의 기사 아닌 곳 (NYT Cooking 등)
GN_JUNK_DOMAINS = ("cooking.nytimes.com",)


def is_junk(title, publisher="", domain=""):
    """외신 중 기사가 아닌 것(모음·요약 코너·요리 등)인지 — 기업별·한국 관련 모두 뺀다"""
    return bool(GN_JUNK_RE.search(title or "") or GN_JUNK_PUBLISHER_RE.search(publisher or "") or (domain or "") in GN_JUNK_DOMAINS)


def aliases(term):
    """외신 검색어 -> 별칭 목록 (검색·제목 판정에 쓴다)"""
    return GNEWS_ALIASES.get(term) or [term]


def title_has(title, names):
    """제목에 별칭 중 하나가 낱말 그대로 들어 있는지 (대소문자 무시, 'Meta'는 'metaverse'에 안 걸림)"""
    return any(re.search(r"(?<![A-Za-z0-9])" + re.escape(a) + r"(?![A-Za-z0-9])", title, re.I) for a in names)


def company_core(term):
    """기업 검색식 앞부분: ("별칭1" OR "별칭2")"""
    return "(" + " OR ".join(f'"{a}"' for a in aliases(term)) + ")"


def korea_core(sector):
    """'한국 관련' 검색식 앞부분: (한국 낱말) (섹터 낱말) -북한"""
    korea = "(" + " OR ".join(f'"{w}"' for w in KOREA_WORDS) + ")"
    words = KOREA_TOPICS.get(sector) or []
    return korea + (" (" + " OR ".join(f'"{w}"' if " " in w else w for w in words) + ")" if words else "") + " " + KOREA_EXCLUDE


def query(core, days, domains):
    """구글 뉴스 검색식 (언론사 도메인만, 최근 days일)"""
    return f"{core} (" + " OR ".join(f"site:{d}" for d in domains) + f") when:{days}d"


def parse_rss(content, domains, days, now=None, title_names=None):
    """구글 뉴스 RSS -> [{title, url, publisher, domain, dt}] 최신순. 고른 언론사·기간만, title_names가 있으면 제목에 별칭이 든 것만."""
    now = now or datetime.now(KST)
    root = ET.fromstring(content)
    cutoff = now - timedelta(days=days)
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
        if title_names is not None and not title_has(title, title_names):
            continue
        if is_junk(title, publisher, domain):
            continue
        out.append({"title": title, "url": (it.findtext("link") or "").strip(), "publisher": publisher, "domain": domain, "dt": when})
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out


def file_name(kind, sector):
    """모아 둔 외신 파일 이름: kind = 'global'(기업별 해외 섹터) / 'korea'(한국 관련 섹터)"""
    return f"{kind}/" + hashlib.md5(sector.encode("utf-8")).hexdigest()[:10] + ".json"


def global_sectors(groups, english, names):
    """해외 섹터 -> 영문 기업명 목록 (app.py의 GLOBAL_SECTORS와 같은 규칙: 재무 분석 '해외 섹터' 구성, 반도체엔 Samsung)"""
    out = {g: list(dict.fromkeys(english.get(t) or names.get(t) or t for t in tickers)) for g, tickers in groups.items()}
    if "반도체" in out and "Samsung" not in out["반도체"]:
        out["반도체"].append("Samsung")
    return out


def ko_prompt(chunk):
    return ("다음 영문 기사 제목들을 순서대로 한국어 제목으로 옮겨 JSON 배열(문자열 목록)로만 답해라. 개수는 정확히 " + str(len(chunk)) + "개.\n"
            + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk)))


def parse_ko(text, n):
    """AI 답(JSON 배열, 또는 {"titles": [...]}) -> 한글 제목 n개 목록. 개수가 다르면 None"""
    try:
        got = json.loads(re.sub(r"^```(?:json)?|```$", "", str(text).strip(), flags=re.M).strip())
    except (ValueError, TypeError):
        return None
    if isinstance(got, dict):
        got = next((v for v in got.values() if isinstance(v, list)), None)
    if not isinstance(got, list) or len(got) != n:
        return None
    return [str(x).strip() for x in got]


def gemini_translate(titles, key, model, post, chunk_size=None):
    """수집기용: 영문 제목들 -> {영문: 한글} (Gemini). post(url, json, headers, timeout) -> 응답. 실패한 묶음은 건너뛴다(앱이 볼 때 다시 옮김)."""
    chunk_size = chunk_size or GN_KO_CHUNK
    out = {}
    if not key:
        return out
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for i in range(0, len(titles), chunk_size):
        chunk = titles[i:i + chunk_size]
        body = {"systemInstruction": {"parts": [{"text": GN_KO_SYSTEM}]}, "contents": [{"role": "user", "parts": [{"text": ko_prompt(chunk)}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192, "responseMimeType": "application/json"}}
        for attempt in range(3):
            try:
                res = post(url, json=body, headers={"x-goog-api-key": key}, timeout=90)
            except Exception:
                res = None
            if res is not None and res.status_code == 200:
                parts = ((res.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                got = parse_ko("".join(p.get("text", "") for p in parts if not p.get("thought")), len(chunk))
                if got:
                    out.update({en: ko for en, ko in zip(chunk, got) if ko})
                break
            if res is not None and res.status_code in (400, 401, 403, 404, 429):
                return out  # 키·모델 문제나 한도 초과면 그만 (앱이 볼 때 옮김)
            time.sleep(2 * (attempt + 1))
    return out
