import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import sys, json, os, tempfile, random
from datetime import datetime
sys.path.insert(0,REPO)
import trade_data as td
td.PAUSE=0
random.seed(1)
calls=[]
def fake(key, path, **p):
    ym=p["strtYymm"]; calls.append((path.split("/")[0],ym))
    if ym>"202610": return []
    if path==td.TOTAL_API:
        if ym=="202610": return []                    # 이번 달 총괄은 아직 없음
        return [{"expDlr":str(60_000_000_000+int(ym)%100*1_000_000),"impDlr":"55000000000","balPayments":"5000000000","expCnt":"1000000","impCnt":"4000000"}]
    if path==td.COUNTRY_API: return [{"statCd":c,"statCdCntnKor1":n,"expDlr":"1000000000","impDlr":"500000000"} for c,n in (("CN","중국"),("US","미국"))]
    if path==td.REGION_API: return [{"statCd":"10","statCdCntnKor1":"EU","expDlr":"800000000","impDlr":"700000000"}]
    if path==td.CONTINENT_API: return [{"statCd":"10","statCdCntnKor1":"아시아","expDlr":"3000000000","impDlr":"2000000000"}]
    if path==td.SIDO_API: return [{"priodTitle":"총계","expUsdAmt":"1,000","impUsdAmt":"900"},{"sidoNm":"서울특별시","expUsdAmt":"7,053,873","impUsdAmt":"21,500,429","priodTitle":"2026"}]
    if path==td.PORT_API: return [{"statKor":"서울세관","expDlr":"10","impDlr":"5","cstmSgn":"010"},{"statKor":"서울세관","expDlr":"20","impDlr":"1","cstmSgn":"011"},{"statKor":"부산세관","expDlr":"7","impDlr":"2"}]
    if path in td.TENDAY_APIS.values():
        return [{"priodMon":ym,"priodDt":d,"priodYear":ym[:4],**{f"itemUsdAmt{i:02d}":f"{(10-i)*1000:,}" for i in range(11)}} for d in ("01~10","01~20","01~30")]
    if path==td.ITEM_API:
        ch=int(p["hsSgn"]); return [{"year":"총계","hsCd":"-","statKor":"-"}]+[{"year":"2026.09","hsCd":f"{ch:02d}{k:02d}","statKor":f"품목 {ch:02d}{k:02d}","statCd":"US"} for k in range(1,12)]
    return []
td.get_items=fake
ok=True
def check(c,m):
    global ok; print(("  ✅ " if c else "  ❌ ")+m); ok&=bool(c)
now=datetime(2026,10,23,tzinfo=td.KST)
out=td.build("k",{},now,25)
check(out["months"][0]=="2024-10" and out["months"][-1]=="2026-09" and len(out["months"])==24, f"월 범위(이번 달 제외) {out['months'][0]}~{out['months'][-1]} {len(out['months'])}개")
# 이번 달에 중간 집계가 있어도 월별에서 뺀다
def fake_cur(key,path,**p):
    if path==td.TOTAL_API and p["strtYymm"]=="202610": return [{"expDlr":"1","impDlr":"1","balPayments":"0","expCnt":"1","impCnt":"1"}]
    return fake(key,path,**p)
keep=td.get_items; td.get_items=fake_cur
o_cur=td.build("k",{},now,25); td.get_items=keep
check("2026-10" not in o_cur["months"], "이번 달(중간 집계)은 월별 자료에서 제외")
check(len(out["total"]["exp"])==24 and out["country"]["CN"][0]=="중국" and len(out["country"]["CN"][1])==24, "국가 배열 길이")
check(out["sido"]["서울특별시"][0][-1]==7053873000, "시도 천달러→달러")
check(out["port"]["서울세관"][0][-1]==30 and out["port"]["서울세관"][1][-1]==6, "세관 합산")
check(sorted(out["tenday"])[-1]=="202610" and out["tenday"]["202609"]["exp_cnty"]["01~20"][0]==10000, f"10일 단위 {sorted(out['tenday'])[:1]}~{sorted(out['tenday'])[-1]} ({len(out['tenday'])}개월)")
check(len(out["hs"])>=800 and out["hs"]["0101"]=="품목 0101", f"품목 이름 {len(out['hs'])}개")
# 증분: prev를 주면 최근 몇 달만 다시 받음
calls.clear()
out2=td.build("k",out,now,25)
total_calls=[c for c in calls if c[0]=="Newtrade"]
check(len(total_calls)<=td.REFRESH_MONTHS+2, f"증분: 총괄 호출 {len(total_calls)}번 {[c[1] for c in total_calls]}")
check(out2["months"]==out["months"] and out2["total"]==out["total"], "증분 결과 동일")
# 새 달이 생기면 추가
now2=datetime(2026,11,16,tzinfo=td.KST)
def fake2(key,path,**p):
    if path==td.TOTAL_API and p["strtYymm"]=="202610": return [{"expDlr":"61000000000","impDlr":"50000000000","balPayments":"11000000000","expCnt":"1","impCnt":"1"}]
    return fake(key,path,**{**p,"strtYymm":p["strtYymm"]}) if p["strtYymm"]<="202610" else []
td.get_items=lambda key,path,**p: fake2(key,path,**p) if not (path!=td.TOTAL_API and p["strtYymm"]=="202610") else fake(key,path,**{**p,"strtYymm":"202609"})
out3=td.build("k",out,now2,25)
check(out3["months"][-1]=="2026-10" and out3["total"]["exp"][-1]==61000000000, "새 달 추가")
print("결과:", "통과" if ok else "실패"); sys.exit(0 if ok else 1)
