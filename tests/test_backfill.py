import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import sys, os, json, tempfile
sys.path.insert(0, REPO)
from datetime import datetime, timedelta
import backfill_data as bf, news_digest as nd, collect_data as cd

# windows
w = bf.date_windows(datetime(2026,9,1).date(), datetime(2026,9,25).date(), 7)
assert w[0] == (datetime(2026,9,1).date(), datetime(2026,9,8).date()) and w[-1][1] == datetime(2026,9,25).date() and len(w)==4, w

# google fake
class R:
    def __init__(s, st, content=b""): s.status_code=st; s.content=content
    def raise_for_status(s):
        if s.status_code>=400: raise RuntimeError(f"HTTP {s.status_code}")
def rss(items):
    x = "".join(f'<item><title>{t} - 연합뉴스</title><link>{u}</link><pubDate>{d}</pubDate><source url="https://www.yna.co.kr">연합뉴스</source></item>' for t,u,d in items)
    return f'<rss><channel>{x}</channel></rss>'.encode()
calls=[]
class S:
    def get(s, url, **kw):
        calls.append(kw["params"]["q"])
        if len(calls)==1: return R(429)
        return R(200, rss([("삼성전자 신제품","https://a/1","Tue, 15 Sep 2026 03:00:00 GMT"),("범위밖","https://a/2","Tue, 01 Sep 2026 03:00:00 GMT")]))
bf.GAPS=(0,0,0); bf.PAUSE=0
out = bf.google_fetch_range("삼성전자", ["yna.co.kr"], "", datetime(2026,9,10).date(), datetime(2026,9,17).date(), S())
assert len(out)==1 and out[0]["title"]=="삼성전자 신제품" and out[0]["dt"].day==15, out
assert "after:2026-09-10 before:2026-09-17" in calls[-1] and len(calls)==2

# disclosures
now = datetime(2026,10,24,12,tzinfo=nd.KST)
grp = nd.A["INDUSTRY_GROUPS"]; corp = grp["자동차"][0]
def fake(key, ymd, cls, ty):
    if ymd=="20261020" and cls=="Y": return [{"corp_name":corp,"report_nm":"주요사항보고서(자기주식취득결정)","rcept_no":"20261020000001","rcept_dt":ymd}]
    if ymd=="20261021" and cls=="K": raise RuntimeError("x")
    return []
store={}
added, failed = bf.backfill_disclosures(store,"k",10,["자동차","반도체"],now,fake)
print(added, failed, list(store.values())[:1])
assert added>=1 and len(failed)==1 and failed[0].startswith("20261021/K")
assert all(r["d"].startswith("2026-10-20") for r in store.values())

# collect with injected google
def gs(terms, domains, extra, cutoff): return [{"title":"반도체 뉴스","url":"https://x/1","publisher":"연합뉴스","category":"통신","dt":datetime(2026,9,20,tzinfo=nd.KST)}], None
print(len(nd.collect("통신","반도체",datetime(2026,9,1,tzinfo=nd.KST),google=gs)))
print("OK")
