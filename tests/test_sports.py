import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import sys, json, os, tempfile, datetime as dt
sys.path.insert(0,REPO)
import requests, sports_titles as st
st.fetch_rss.__defaults__=(lambda s:None, st.RSS_URLS[0])
RSS=b'<rss><channel>'+b''.join(f'<item><title>[프로농구] 경기 {i}</title><pubDate>Fri, 23 Oct 2026 10:00:00 +0900</pubDate></item>'.encode() for i in range(5))+b'</channel></rss>'
calls={"n":0}
class R:
    content=RSS
    def raise_for_status(self): pass
def make(fail_n):
    calls["n"]=0
    def get(*a,**k):
        calls["n"]+=1
        if calls["n"]<=fail_n: raise requests.ConnectTimeout("timeout")
        return R()
    return get
d=tempfile.mkdtemp(); out=d+"/o.json"
def run(prev_obj):
    prev=d+"/p.json"
    if prev_obj is None: 
        if os.path.exists(prev): os.remove(prev)
    else: json.dump(prev_obj,open(prev,"w"))
    sys.argv=["x","--prev",prev,"--out",out]
    if os.path.exists(out): os.remove(out)
    try: st.main(); return "ok"
    except Exception as e: return "예외 "+type(e).__name__
now=dt.datetime.now(dt.timezone.utc)
fresh_prev={"updated":(now-dt.timedelta(hours=1)).isoformat(),"items":[{"k":"a","t":"옛","d":"2026-10-22"}]}
stale_prev={"updated":(now-dt.timedelta(hours=20)).isoformat(),"items":[{"k":"a","t":"옛","d":"2026-10-22"}]}
st.requests.get=make(2); print("2번 실패 뒤 성공:",run(None),calls["n"],len(json.load(open(out))["items"]))
st.requests.get=make(99); print("계속 실패+최근 목록:",run(fresh_prev),calls["n"],len(json.load(open(out))["items"]))
st.requests.get=make(99); print("계속 실패+오래된 목록:",run(stale_prev),os.path.exists(out))
st.requests.get=make(99); print("계속 실패+목록 없음:",run(None))

# 전국(local) RSS도 함께 모은다
LOCAL=b'<rss><channel>'+b''.join(f'<item><title>[전국 사건] 지방 소식 {i}</title><pubDate>Fri, 23 Oct 2026 11:00:00 +0900</pubDate></item>'.encode() for i in range(3))+b'</channel></rss>'
urls=[]
class R2:
    def __init__(self,c): self.content=c
    def raise_for_status(self): pass
def get2(url,*a,**k):
    urls.append(url); return R2(LOCAL if "local" in url else RSS)
st.requests.get=get2; print("두 RSS:",run(None),urls)
titles=[i["t"] for i in json.load(open(out))["items"]]
print("스포츠 5 + 전국 3 =", len(titles), any("전국 사건" in t for t in titles) and any("프로농구" in t for t in titles))
# 출처 표시: 스포츠 RSS 제목은 s='스포츠', 전국은 s='전국' (앱·알람은 스포츠 제목만 비슷한 제목까지 거름)
items=json.load(open(out))["items"]
src={i["t"][:6]: i.get("s") for i in items}
assert all(i.get("s")==("전국" if "전국 사건" in i["t"] else "스포츠") for i in items), items
# 예전에 출처 없이 모은 제목이 스포츠 RSS에 다시 나오면 '스포츠'로 표시가 붙는다
k=st.title_key("[프로농구] 경기 0")
m=st.merge({"items":[{"k":k,"t":"[프로농구] 경기 0","d":"2026-10-23"}]},[("[프로농구] 경기 0","2026-10-23","스포츠")],"2026-10-23")
assert m["items"][0].get("s")=="스포츠", m
print("출처 표시 ok")
