"""구글 뉴스 RSS 주소(news.google.com/rss/articles/...) -> 원문 주소 바꾸기 시험 (최근 단독 기사 30건). 결과: 성공률·걸린 시간"""
import json, re, time, urllib.parse, requests
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
rec = requests.get("https://raw.githubusercontent.com/brucerue/DART-Joongang/data/recent.json", timeout=30).json()
urls = [x["u"] for x in rec["items"] if "news.google.com" in x["u"] and x.get("k") == "단독"][:30]

def decode(u):
    gid = re.search(r"articles/([^?]+)", u).group(1)
    r = S.get(f"https://news.google.com/rss/articles/{gid}", timeout=15)
    sg = re.search(r'data-n-a-sg="([^"]+)"', r.text); ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
    if not (sg and ts):
        return None, f"서명 없음 {r.status_code}"
    inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
             f'"{gid}",{ts.group(1)},"{sg.group(1)}"]')
    body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inner, None, "generic"]]]))
    r2 = S.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=15)
    m = re.search(r'\[\\"garturlres\\",\\"(.*?)\\"', r2.text)
    return (m.group(1).encode().decode("unicode_escape") if m else None), (None if m else f"답 없음 {r2.status_code}")

ok, t0, errs = 0, time.time(), {}
for i, u in enumerate(urls):
    try:
        got, err = decode(u)
    except Exception as e:
        got, err = None, type(e).__name__
    if got:
        ok += 1
        if i < 5: print("예:", got[:90])
    else:
        errs[err] = errs.get(err, 0) + 1
    time.sleep(0.5)
print(f"성공 {ok}/{len(urls)} · 걸린 시간 {time.time()-t0:.1f}초(간격 0.5초 포함) · 실패 {errs}")
