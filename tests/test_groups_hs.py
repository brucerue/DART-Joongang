import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
import sys, os, json, tempfile, shutil
sys.path.insert(0, REPO)
os.environ["CUSTOMS_API_KEY"] = "dummy"
import trade_calc as tc, trade_data as td, trade_groups as tg
# trade_groups.py의 HS 6자리 저장(--hs-out): 같은 호출로 HS 10자리 행을 6자리로 더해 hs/ 폴더에 쌓는지 가짜 응답으로 확인
mti = tc.load_mti(REPO + "/mti_map.json")
names, lookups = tg.group_lookup(mti)
t25 = mti["tables"].index(tc.mti_table_for_year(mti, 2025))
car = next(h for h, g in lookups[t25].items() if names.index("자동차") in g and h.startswith("870380"))  # 전기 승용차
months = [f"2025-{m:02d}" for m in range(8, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]  # 14개월
trade = {"months": months, "country": {
    "US": ["미국", [10] * 14, [5] * 14], "CN": ["중국", [9] * 14, [3] * 14], "DE": ["독일", [1] * 14, [1] * 14],
    "VN": ["베트남", [0] * 9 + [2] * 5, [0] * 14], "ZZ": ["없음", [0] * 14, [0] * 14]}}
BASE = {"US": 3000000, "CN": 2000000, "DE": 1000000, "VN": 500000}
DATA = {"bump": {}}  # (국가, 'YYYYMM') -> 배수 (최근 달 수정 흉내)
calls = []


def fake_get(key, path, **p):
    calls.append(dict(p))
    cn = p["cntyCd"]
    rows = [{"year": "총계", "hsCd": "-", "expDlr": "1", "impDlr": "1"}]
    cur = p["strtYymm"]
    while cur <= p["endYymm"]:
        m = f"{cur[:4]}-{cur[4:]}"
        if cn in BASE and m in trade["months"]:
            k = trade["months"].index(m)
            if trade["country"][cn][1][k] or trade["country"][cn][2][k]:
                b = BASE[cn] * DATA["bump"].get((cn, cur), 1)
                ym = f"{cur[:4]}.{cur[4:]}"
                rows.append({"year": ym, "hsCd": car, "expDlr": str(b), "impDlr": "0"})
                rows.append({"year": ym, "hsCd": "8703802000", "expDlr": str(b // 10), "impDlr": "2000"})  # 같은 6자리(870380)의 다른 10자리
                rows.append({"year": ym, "hsCd": "3102109000", "expDlr": "0", "impDlr": str(b // 2)})     # 요소(수입만)
        cur = tc.month_add(cur, 1)
    return rows


td.get_items = fake_get
d = tempfile.mkdtemp()
tj = d + "/trade.json"; json.dump(trade, open(tj, "w"))
out, hs_prev, hs_out = d + "/groups.json", d + "/hs_prev", d + "/hs_out"
fails = []


def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        fails.append(m)


def run(*a, hs=True):
    """한 번 실행. 워크플로처럼 지난번 hs_out을 hs_prev로 옮겨 넘긴다"""
    if os.path.isdir(hs_out):
        shutil.rmtree(hs_prev, ignore_errors=True)
        shutil.copytree(hs_out, hs_prev)
        shutil.rmtree(hs_out)
    extra = ["--hs-prev", hs_prev, "--hs-out", hs_out, "--hs-countries", "2"] if hs else []
    sys.argv = ["x", "--trade", tj, "--out", out, "--prev", out] + extra + list(a)
    return tg.main()


def ch(c="87"):
    return json.load(open(f"{hs_out}/ch/{c}.json"))


def meta():
    return json.load(open(f"{hs_out}/meta.json"))


# 1) HS 없이 돌린 groups.json과, HS를 켜고 돌린 groups.json이 같아야 한다
sys.argv = ["x", "--trade", tj, "--out", d + "/plain.json"]
tg.main()
plain = json.load(open(d + "/plain.json"))
assert run() == 0
g = json.load(open(out))
check(g["data"] == plain["data"] and g["complete"] and g["done_to"] == plain["done_to"], "HS를 켜도 groups.json 내용은 그대로")
m = meta()
check(m["complete"] and m["countries"] == ["US", "CN"] and set(m["done_to"]) == {"US", "CN", "DE", "VN", "ZZ"}, f"meta: 상위 2개국·완료 {m['countries']}")
c87 = ch()["codes"]["870380"]
# 870380 = 8703801000(b) + 8703802000(b/10), 천달러
exp_w = [(3300 + 2200 + 1100) if k < 9 else (3300 + 2200 + 1100 + 550) for k in range(14)]
check(c87["W"][0] == exp_w, f"세계 합계 = 모든 나라 합(베트남은 거래 있는 달만) {c87['W'][0][:2]}…{c87['W'][0][-2:]}")
check(c87["W"][1] == [6] * 9 + [8] * 5, f"수입 2,000달러×거래한 나라 수(베트남은 5월부터) {c87['W'][1][8:11]}")
check(c87["US"][0] == [3300] * 14 and c87["CN"][0] == [2200] * 14 and "DE" not in c87 and "VN" not in c87, "나라별은 상위 2개국만")
c31 = ch("31")["codes"]["310210"]
check(c31["W"][0] == [0] * 14 and c31["W"][1][0] == 1500 + 1000 + 500, f"요소 310210 수입 {c31['W'][1][:2]}")
check(json.load(open(f"{hs_out}/recent.json"))["US"]["870380"]["2026-09"] == [3300, 2], "최근 3개월은 나라별로 recent.json에")

# 2) 다시 돌리면 최근 3개월만 다시 받고, 수정된 값으로 바뀐다(세계 합계도)
n0 = len(calls); DATA["bump"][("DE", "202609")] = 3; run()
re = calls[n0:]
check(all(c["strtYymm"] >= "202607" for c in re), f"두 번째부터는 최근 3개월만 ({sorted({c['strtYymm'] for c in re})})")
c87 = ch()["codes"]["870380"]
check(c87["W"][0][-1] == 3300 + 2200 + 3300 + 550 and c87["W"][0][-2] == exp_w[-2], f"독일 9월 수정이 세계 합계에 반영 {c87['W'][0][-2:]}")

# 3) 새 달이 생기면 창이 한 칸 밀리고, 나간 달(7월)은 세계 합계로 굳는다
trade["months"] = months + ["2026-10"]
for v in trade["country"].values():
    v[1].append(v[1][-1]); v[2].append(v[2][-1])
json.dump(trade, open(tj, "w")); run()
m, c87 = meta(), ch()["codes"]["870380"]
check(m["window_from"] == "2026-08" and len(c87["W"][0]) == 15, f"창 {m['window_from']}, 15개월")
check(c87["W"][0][-6] == exp_w[-5] and c87["W"][0][-1] == 3300 + 2200 + 1100 + 550, f"굳은 달·새 달 값 {c87['W'][0][-6]} {c87['W'][0][-1]}")
check("2026-07" not in json.load(open(f"{hs_out}/recent.json"))["US"]["870380"], "창 밖 달은 recent에서 빠짐")

# 4) 처음부터, 중간에 끊겼다가 이어받기 (나라 2곳만 → 미완 → 나머지)
shutil.rmtree(hs_out); shutil.rmtree(hs_prev, ignore_errors=True)
run("--limit-countries", "2")
m = meta()
check(not m["complete"] and len([c for c, t in m["done_to"].items() if t == "2026-10" and c != "ZZ"]) == 2, f"2곳만 받으면 미완 {m['done_to']}")
run()
m, c87 = meta(), ch()["codes"]["870380"]
check(m["complete"] and c87["W"][0][0] == 3300 + 2200 + 1100 and c87["W"][0][-1] == 3300 + 2200 + 1100 + 550, f"이어받아 완료, 세계 합계 맞음 {c87['W'][0][:1]} {c87['W'][0][-1:]}")
g2 = json.load(open(out))
check(g2["data"]["자동차"]["US"][0][-1] == g["data"]["자동차"]["US"][0][-1], "groups 값은 그대로")

# 5) HS 폴더가 깨져 있어도 groups.json은 저장
shutil.rmtree(hs_out); os.makedirs(hs_out + "/ch"); open(hs_out + "/meta.json", "w").write("{깨짐"); open(hs_out + "/recent.json", "w").write("[]")
rc = run()
check(rc == 0 and json.load(open(out))["complete"], "hs 자료가 이상해도 groups는 정상 저장")
print("실패", fails); sys.exit(1 if fails else 0)
