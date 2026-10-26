import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
"""query_mti(HS4·HS6 나눠 조회)와 단순 HS6 조회 결과가 같은지 (가짜 API: HSK마다 값이 다름)"""
import sys, time
sys.path.insert(0, REPO)
import trade_calc as tc
mti = tc.load_mti(REPO + "/mti_map.json")
ok = True
def check(c, m):
    global ok; print(("  ✅ " if c else "  ❌ ") + m); ok &= bool(c)
def val(h): return (int(h) % 997) + 1          # HSK마다 다른 값
def months(s, e):
    out, cur = [], s
    while cur <= e:
        out.append(f"{cur[:4]}.{cur[4:]}"); cur = tc.month_add(cur, 1)
    return out
calls = []
def make_fetch(naive):
    def fetch(params):
        calls.append((len(params["hsSgn"]), params["strtYymm"]))
        hs = params["hsSgn"]; yr = int(params["strtYymm"][:4])
        table = tc.mti_table_for_year(mti, yr)
        by6 = {}
        for h in table["hsk_to_mti"]: by6.setdefault(h[:6], []).append(h)
        items = ["<item><year>총계</year><hsCd>-</hsCd></item>"]
        for ym in months(params["strtYymm"], params["endYymm"]):
            if len(hs) == 6:
                for h in by6.get(hs, []):
                    items.append(f"<item><year>{ym}</year><hsCd>{h}</hsCd><statKor>x</statKor><statCd>US</statCd><statCdCntnKor1>미국</statCdCntnKor1><expDlr>{val(h)}</expDlr><impDlr>1</impDlr><expWgt>1</expWgt><impWgt>1</impWgt></item>")
            else:  # HS4 -> HS6 행(자식 HS10 합)
                for h6, ch in sorted(by6.items()):
                    if h6[:4] == hs:
                        items.append(f"<item><year>{ym}</year><hsCd>{h6}</hsCd><statKor>x</statKor><statCd>US</statCd><statCdCntnKor1>미국</statCdCntnKor1><expDlr>{sum(val(c) for c in ch)}</expDlr><impDlr>{len(ch)}</impDlr><expWgt>1</expWgt><impWgt>1</impWgt></item>")
        return "<response><header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header><body><items>" + "".join(items) + "</items></body></response>"
    return fetch
def expected(key, start, end):
    """연계표만으로 계산한 정답: MTI6별 합"""
    out = {}
    for table, s, e in tc.mti_segments(mti, start, end):
        include, exclude = tc.mti_spec(key)
        n = tc.month_span(s, e)
        for h in tc.mti_hsk(table, include, exclude):
            m = table["hsk_to_mti"][h]
            out[m] = out.get(m, 0) + val(h) * n
    return out
for key, start, end in (("@반도체", "202509", "202511"), ("@일반기계", "202509", "202509"), ("741", "202412", "202501"), ("@농수산식품", "202511", "202511"), ("@석유제품", "202509", "202509"), ("831110", "202509", "202509"), ("@섬유", "202509", "202509")):
    calls.clear(); t0 = time.time()
    rows, skipped = tc.query_mti(make_fetch(False), mti, key, None, start, end, workers=16)
    got = {}
    for r in rows: got[r["mti"]] = got.get(r["mti"], 0) + r["exp"]
    exp = expected(key, start, end)
    n4 = sum(1 for c in calls if c[0] == 4)
    check(got == exp and not skipped, f"{key} {start}~{end}: MTI {len(exp)}개 일치, 호출 {len(calls)}번(HS4 {n4}) {time.time()-t0:.1f}초")
rows, skipped = tc.query_mti(make_fetch(False), mti, "99999", None, "202509", "202509")
check(rows == [] and skipped, f"없는 코드는 건너뜀 {skipped}")
# 그룹 정의 검사
check(all(g in tc.MTI_GROUPS for g in ("반도체", "일반기계", "석유제품")) and len(tc.MTI_GROUPS) == 20, f"주력품목 {len(tc.MTI_GROUPS)}개")
for g, (inc, exc) in tc.MTI_GROUPS.items():
    t = mti["tables"][-1]; check(len(tc.mti_hsk(t, inc, exc)) > 0, f"  {g} HSK {len(tc.mti_hsk(t, inc, exc))}개")
print("결과:", "통과" if ok else "실패"); sys.exit(0 if ok else 1)
