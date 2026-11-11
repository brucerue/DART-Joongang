"""아침 브리핑용 자료 모으기 (화면 없음) -> brief/brief.json

다른 프로그램(브라우저 없이 서버에서 도는 아침 브리핑)이 읽어 가도록, 앱 화면에서만 계산되던 것을 파일로 남긴다.
- 섹터별(산업 뉴스 섹터와 같은 묶음): 단독·통신 기사 묶음(같은 기사 묶기), 섹터 기업 주요 공시, 특징주(오른·내린 2종목),
  앞으로 7일 IR 일정(공시로 확정된 것, NDR 제외), 앞으로 14일 법정 제출기한
- 전 상장사 오늘의 주요 공시(공시 분석 '오늘의 공시'와 같은 묶음)
기간: 평소 24시간, 월요일은 금요일 15:00부터.
계산 규칙은 app.py의 것을 그대로 읽어 쓴다(news_digest.py처럼 AST로) — 앱을 고치면 이 자료도 따라 바뀐다.

입력 파일: --data(data 브랜치: recent.json), --prices(prices.json), --intraday(intraday.json), --corp-csv(corp_codes.csv)
환경변수: DART_API_KEY (IR 일정·제출기한·오늘의 공시. 없으면 그 칸은 비우고 notes에 적음)
"""
import argparse
import ast
import html
import io
import json
import os
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

import news_digest as nd
import stock_calc as sc
import today_calc as tdc

KST = timezone(timedelta(hours=9))
FMT = "%Y-%m-%dT%H:%M"
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
IR_DAYS = 7  # 앞으로 며칠 IR 일정 (오늘의 현황과 같게)
DEADLINE_DAYS = 14  # 앞으로 며칠 제출기한
IR_LOOKBACK = 30  # IR 개최 공시를 찾는 기간(일) — 공시는 보통 행사 1~3주 전에 나옴
MOVERS_N = 2
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
DART_DOC_URL = "https://opendart.fss.or.kr/api/document.xml"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
CLS_NAMES = {"Y": "코스피", "K": "코스닥"}


def load_extra():
    """app.py에서 news_digest가 읽지 않는 것(공시 묶음·IR 규칙·제출기한·시세 섹터)을 더 읽어 nd.A에 합친다."""
    src = open(APP, encoding="utf-8").read()
    tree = ast.parse(src)
    want_lit = {"EXTRA_DISCLOSURES", "DISC_VIEW_GROUPS", "IR_NDR_WORDS", "IR_NDR_SURE", "IR_CALL_WORDS", "WEEKDAYS"}
    want_src = {"IR_DATE_RE", "IR_PURPOSE_RE"}  # re.compile(...) 줄
    want_fn = {"cal_deadlines", "ir_is_ndr", "doc_text_from_zip", "px_names", "px_find", "px_sector_codes", "today_price_codes"}
    ns = dict(nd.A)
    ns.update({"re": re, "io": io, "zipfile": zipfile, "html": html, "date": date, "timedelta": timedelta, "AiError": RuntimeError})
    got = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in got:
                continue
            if name in want_lit:
                try:
                    ns[name] = ast.literal_eval(node.value)
                    got.add(name)
                except ValueError:
                    pass
            elif name in want_src:
                exec(ast.get_source_segment(src, node), ns)
                got.add(name)
        elif isinstance(node, ast.FunctionDef) and node.name in want_fn and node.name not in got:
            exec(ast.get_source_segment(src, node), ns)
            got.add(node.name)
    missing = (want_lit | want_src | want_fn) - got
    if missing:
        raise SystemExit(f"app.py에서 읽지 못한 항목: {sorted(missing)}")
    return ns


A = load_extra()


def window_since(now):
    """평소 24시간 전, 월요일은 금요일 15:00부터(주말 포함)"""
    if now.weekday() == 0:
        fri = (now - timedelta(days=3)).date()
        return datetime(fri.year, fri.month, fri.day, 15, 0, tzinfo=KST)
    return now - timedelta(hours=24)


def sector_companies(sector):
    """뉴스 섹터 -> 실적 캘린더 기업 이름들 (묶은 섹터는 낱개 섹터를 합침, 재계처럼 기업 목록이 없으면 빈 목록)"""
    groups = A["INDUSTRY_GROUPS"]
    parts = A["MERGED_SECTORS"].get(sector) or [sector]
    return list(dict.fromkeys(n for p in parts for n in groups.get(p, [])))


def item_out(it):
    return {"t": it["title"], "u": it["url"], "p": it["publisher"], "d": it["dt"].strftime(FMT), "g": it.get("tags") or []}


def news_block(rows, sector, since):
    """섹터의 단독·통신 기사 -> {종류: [{rep, others}]} (같은 기사 묶음, 대표 기사 최신순)"""
    out = {}
    for kind in ("단독", "통신"):
        items = tdc.sector_items(rows, sector, kind, since)
        out[kind] = [{"rep": item_out(c["rep"]), "others": [item_out(o) for o in c["others"]]} for c in A["cluster_news"](items)]
    return out


def movers_block(prices, intra, sector, codes_by):
    """섹터 특징주 -> {"when": '11.10 종가' 등, "groups": [{"part": 낱개 섹터 또는 None, "up": [...], "down": [...]}]} 또는 None"""
    if not prices or not codes_by.get(sector):
        return None
    if sc.intraday_ready(intra, prices):
        snap, last_end = sc.intraday_snapshot(intra, prices)
        when = f"{intra['date'][4:6]}.{intra['date'][6:]} {last_end or '장중'}"
        basis = "intraday"
    else:
        d = prices["dates"][-1]
        snap, when, basis = sc.snapshot(prices, d), f"{d[4:6]}.{d[6:]} 종가", "krx"
    groups = []
    for part, codes in codes_by[sector]:
        up, down = tdc.movers(snap, codes, MOVERS_N)
        clean = lambda rs: [{"name": r["name"], "rate": round(r["rate"], 2), "close": None if r["close"] != r["close"] else float(r["close"])} for r in rs]
        groups.append({"part": part, "up": clean(up), "down": clean(down)})
    return {"when": when, "basis": basis, "groups": groups}


class Dart:
    def __init__(self, key):
        self.key = key
        self.s = requests.Session()

    def get(self, url, **params):
        for i in range(3):
            try:
                r = self.s.get(url, params={"crtfc_key": self.key, **params}, timeout=30)
                return r
            except requests.RequestException:
                if i == 2:
                    raise

    def fiscal_month(self, corp_code):
        try:
            m = int((self.get(DART_COMPANY_URL, corp_code=corp_code).json() or {}).get("acc_mt"))
        except Exception:
            return 12
        return m if 1 <= m <= 12 else 12

    def list_rows(self, corp_code, start, end, ty="I"):
        rows = []
        for page in range(1, 6):
            jo = self.get(DART_LIST_URL, corp_code=corp_code, bgn_de=start.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                          pblntf_ty=ty, page_no=page, page_count=100).json()
            if jo.get("status") == "013":
                break
            if jo.get("status") != "000":
                raise RuntimeError(f"DART {jo.get('status')}")
            rows.extend(jo.get("list", []))
            if page >= int(jo.get("total_page", 1) or 1):
                break
        return rows

    def doc_text(self, rcept_no):
        data = self.get(DART_DOC_URL, rcept_no=rcept_no).content or b""
        if not data.startswith(b"PK"):
            raise RuntimeError("원문 없음")
        return A["doc_text_from_zip"](data)


def ir_info(text):
    """IR 개최 공시 원문 -> (개최일, 목적, NDR인지) — app.py의 cal_ir_info와 같은 규칙"""
    held = None
    pos = text.find("일시")
    for chunk in ([text[pos:pos + 200]] if pos >= 0 else []) + [text[:1500]]:
        m = A["IR_DATE_RE"].search(chunk)
        if m:
            try:
                held = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                break
            except ValueError:
                continue
    m = A["IR_PURPOSE_RE"].search(text)
    purpose = re.sub(r"\s+", " ", m.group(1)).strip(" ()|") if m else None
    return held, purpose or "", A["ir_is_ndr"](text, purpose)


def company_calendar(dart, name, corp_code, today):
    """회사 하나 -> {"ir": [...], "deadlines": [(기한, 보고서)], "unread": n}"""
    fy = dart.fiscal_month(corp_code)
    deadlines = A["cal_deadlines"](fy, today, today + timedelta(days=DEADLINE_DAYS))
    irs = []
    for r in dart.list_rows(corp_code, today - timedelta(days=IR_LOOKBACK), today):
        key = A["disclosure_key"](r.get("report_nm", ""))
        if "정정" in key or "기업설명회" not in key:
            continue
        irs.append(r)
    irs.sort(key=lambda r: r.get("rcept_dt", ""), reverse=True)
    events, unread = [], 0
    for r in irs[:3]:
        try:
            held, purpose, is_ndr = ir_info(dart.doc_text(r["rcept_no"]))
        except Exception:
            held, purpose, is_ndr = None, "", False
        if held is None:
            unread += 1
        elif not is_ndr and today <= held <= today + timedelta(days=IR_DAYS):
            filed = r.get("rcept_dt", "")
            events.append({"date": held.isoformat(), "corp": name, "kind": "IR 개최", "purpose": purpose,
                           "filed": f"{filed[:4]}-{filed[4:6]}-{filed[6:]}", "url": DART_VIEW_URL.format(r["rcept_no"])})
    return {"ir": events, "deadlines": deadlines, "unread": unread}


def name_to_corp(prices, corp_rows):
    """기업 이름 -> (종목코드, 고유번호). 시세 자료의 종목 이름(앱과 같은 줄임말 인식 px_find)으로 종목코드를 찾고 기업 목록으로 고유번호."""
    by_stock = {r["stock_code"].strip(): r["corp_code"] for r in corp_rows if (r.get("stock_code") or "").strip()}
    names = A["px_names"](prices) if prices else {}

    def find(name):
        disp = A["px_find"](names, name)
        code = names.get(disp) if disp else None
        return (code, by_stock.get(code)) if code else (None, None)
    return find


def market_disclosures(dart_key, now, since):
    """전 상장사(코스피·코스닥) 주요 공시 — 공시 분석 '오늘의 공시'와 같은 묶음(MAJOR + EXTRA). since가 속한 날부터 오늘까지."""
    words = {g: [A["disclosure_key"](w) for w in ws] for g, ws in {**A["MAJOR_DISCLOSURES"], **A["EXTRA_DISCLOSURES"]}.items()}
    view_of = {g: v for v, gs in A["DISC_VIEW_GROUPS"].items() for g in gs}
    skip = [A["disclosure_key"](w) for w in nd.DISCLOSURE_SKIP]
    days, d = [], since.date()
    while d <= now.date():
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    out, seen = [], set()
    for ymd in days:
        for cls in ("Y", "K"):
            for r in nd.fetch_dart_day(dart_key, ymd, cls):
                no = r.get("rcept_no")
                if not no or no in seen:
                    continue
                key = A["disclosure_key"](r.get("report_nm", ""))
                if any(s in key for s in skip):  # 임원·주요주주 소유상황보고서 등 (텔레그램 알람과 같이 뺌, 하루 수백 건)
                    continue
                group = next((g for g, ks in words.items() if any(k in key for k in ks)), None)
                if not group:
                    continue
                seen.add(no)
                out.append({"d": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", "corp": r.get("corp_name", ""), "stock_code": r.get("stock_code", ""),
                            "market": CLS_NAMES.get(cls, cls), "title": r.get("report_nm", "").strip(), "group": group,
                            "view": view_of.get(group, ""), "alarm": group in A["MAJOR_DISCLOSURES"], "url": DART_VIEW_URL.format(no)})
    out.sort(key=lambda x: (x["d"], x["url"]), reverse=True)
    return out


def build(recent, prices, intra, corp_rows, dart_key, now, dart=None):
    since = window_since(now)
    rows = recent.get("items", []) if recent else []
    notes = []
    codes_by = {}
    if prices:
        try:
            codes_by = A["today_price_codes"](prices)
        except Exception as e:
            notes.append(f"특징주: {type(e).__name__}")
    sectors = {}
    for sec in A["NEWS_SECTORS"]:
        disc = tdc.sector_items(rows, sec, "공시", since.replace(hour=0, minute=0))  # 공시는 날짜만 있어서(00:00) 시작일 0시부터
        try:
            mv = movers_block(prices, intra, sec, codes_by)
        except Exception as e:
            mv = None
            notes.append(f"특징주 {sec}: {type(e).__name__}")
        sectors[sec] = {"news": news_block(rows, sec, since),
                        "disclosures": [{"t": d["title"], "u": d["url"], "d": d["dt"].strftime("%Y-%m-%d"), "g": d["tags"]} for d in disc],
                        "movers": mv, "ir": [], "deadlines": []}
    market = []
    if dart_key:
        dart = dart or Dart(dart_key)
        find = name_to_corp(prices, corp_rows)
        jobs = {}
        for sec in A["NEWS_SECTORS"]:
            for n in sector_companies(sec):
                _, corp = find(n)
                if corp:
                    jobs.setdefault((n, corp), []).append(sec)
        today = now.date()

        def one(k):
            try:
                return k, company_calendar(dart, k[0], k[1], today)
            except Exception as e:
                return k, {"error": type(e).__name__}
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = dict(pool.map(one, list(jobs)))
        unread = 0
        for (name, corp), res in results.items():
            if "error" in res:
                notes.append(f"캘린더 {name}: {res['error']}")
                continue
            unread += res["unread"]
            for sec in jobs[(name, corp)]:
                sectors[sec]["ir"].extend(res["ir"])
                for dl, label in res["deadlines"]:
                    cur = next((x for x in sectors[sec]["deadlines"] if x["date"] == dl.isoformat() and x["report"] == label), None)
                    if cur is None:
                        sectors[sec]["deadlines"].append({"date": dl.isoformat(), "report": label, "corps": [name]})
                    elif name not in cur["corps"]:
                        cur["corps"].append(name)
        for s in sectors.values():
            s["ir"].sort(key=lambda e: (e["date"], e["corp"]))
            s["deadlines"].sort(key=lambda e: (e["date"], e["report"]))
        if unread:
            notes.append(f"IR 공시 중 날짜를 못 읽은 것 {unread}건")
        try:
            market = market_disclosures(dart_key, now, since)
        except Exception as e:
            notes.append(f"오늘의 공시: {type(e).__name__}")
    else:
        notes.append("DART_API_KEY가 없어 IR 일정·제출기한·오늘의 공시는 비움")
    return {"updated": now.strftime(FMT), "since": since.strftime(FMT),
            "news_updated": (recent or {}).get("updated", ""), "prices_updated": (prices or {}).get("updated", ""),
            "sectors": sectors, "market_disclosures": market, "notes": notes}


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="data 브랜치를 받아 둔 폴더 (recent.json)")
    ap.add_argument("--prices", default="prices.json")
    ap.add_argument("--intraday", default="intraday.json")
    ap.add_argument("--corp-csv", default="corp_codes.csv")
    ap.add_argument("--out", default="brief/brief.json")
    a = ap.parse_args()
    import csv
    corp_rows = []
    try:
        with open(a.corp_csv, encoding="utf-8") as f:
            corp_rows = list(csv.DictReader(f))
    except OSError:
        pass
    now = datetime.now(KST)
    res = build(load(os.path.join(a.data, "recent.json")), load(a.prices), load(a.intraday), corp_rows,
                os.environ.get("DART_API_KEY", "").strip(), now)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    n_news = sum(len(c) for s in res["sectors"].values() for c in s["news"].values())
    n_ir = sum(len(s["ir"]) for s in res["sectors"].values())
    print(f"기사 묶음 {n_news} · IR {n_ir} · 오늘의 공시 {len(res['market_disclosures'])} · 참고 {len(res['notes'])}")
    for n in res["notes"][:20]:
        print(" -", n, file=sys.stderr)


if __name__ == "__main__":
    main()
