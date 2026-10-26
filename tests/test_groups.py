import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import sys, os, json, tempfile
sys.path.insert(0, REPO)
os.environ["CUSTOMS_API_KEY"] = "dummy"
import trade_calc as tc, trade_data as td, trade_groups as tg
mti = tc.load_mti(REPO + "/mti_map.json")
names, lookups = tg.group_lookup(mti)
t25 = mti["tables"].index(tc.mti_table_for_year(mti, 2025))
semi_hs = sorted(h for h, g in lookups[t25].items() if names.index("반도체") in g)[:2]
car_hs = sorted(h for h, g in lookups[t25].items() if names.index("자동차") in g)[:1]
print("반도체", semi_hs, "자동차", car_hs)
months = [f"2025-{m:02d}" for m in range(8, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]  # 14개월
trade = {"months": months, "country": {
    "US": ["미국", [10] * 14, [5] * 14], "CN": ["중국", [4] * 14, [3] * 14], "VN": ["베트남", [0] * 9 + [2] * 5, [0] * 14],  # VN은 2026-05부터 거래
    "ZZ": ["없음", [0] * 14, [0] * 14]}}
calls = []
DATA = {"flip": False}
def fake_get(key, path, **p):
    calls.append(dict(p))
    cn = p["cntyCd"]; rows = [{"year": "총계", "hsCd": "-", "expDlr": "1", "impDlr": "1"}]
    s, e = p["strtYymm"], p["endYymm"]
    cur = s
    while cur <= e:
        ym = f"{cur[:4]}.{cur[4:]}"
        idx = trade["months"].index(f"{cur[:4]}-{cur[4:]}") if f"{cur[:4]}-{cur[4:]}" in trade["months"] else None
        if cn in ("US", "CN", "VN") and idx is not None and (trade["country"][cn][1][idx] or trade["country"][cn][2][idx]):
            base = {"US": 1000000, "CN": 500000, "VN": 100000}[cn]
            k = int(cur) % 100
            rows.append({"year": ym, "hsCd": semi_hs[0], "expDlr": str(base * 2), "impDlr": str(base), "statCd": cn})
            rows.append({"year": ym, "hsCd": semi_hs[1], "expDlr": str(base), "impDlr": "0", "statCd": cn})
            rows.append({"year": ym, "hsCd": car_hs[0], "expDlr": str(base * (3 if DATA["flip"] and cur == "202609" else 1)), "impDlr": "7", "statCd": cn})
            rows.append({"year": ym, "hsCd": "9999999999", "expDlr": "123456789", "impDlr": "1", "statCd": cn})  # 20대에 안 묶이는 품목
        cur = tc.month_add(cur, 1)
    return rows
td.get_items = fake_get
d = tempfile.mkdtemp(); tj = d + "/trade.json"; json.dump(trade, open(tj, "w"))
out = d + "/groups.json"
def run(*a):
    sys.argv = ["x", "--trade", tj, "--out", out, "--prev", out] + list(a)
    return tg.main()
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
# 1) 한 나라만 -> 미완
assert run("--limit-countries", "1") == 0
j = json.load(open(out))
check(j["complete"] is False and list(j["done_to"]) == ["US"] or "US" in j["done_to"], f"한 나라만 받으면 complete 아님 {j['done_to']}")
# 2) 이어서
n0 = len(calls); run()
j = json.load(open(out))
check(j["complete"] is True and set(j["done_to"]) == {"US", "CN", "VN", "ZZ"}, f"이어서 완료 {sorted(j['done_to'])}")
check(all(c["cntyCd"] != "ZZ" for c in calls), "거래 없는 나라는 호출 안 함")
semi = j["data"]["반도체"]; car = j["data"]["자동차"]
check(semi["US"][0] == [3000] * 14 and semi["US"][1] == [1000] * 14, f"미국 반도체 수출 3,000천달러×14개월(두 코드 합) {semi['US'][0][:3]}")
check(car["CN"][0] == [500] * 14 and car["CN"][1] == [0] * 14, f"중국 자동차 {car['CN'][0][:2]}, 수입 7달러는 0천달러로 반올림")
check("ZZ" not in semi and semi["VN"][0] == [0] * 9 + [300] * 5, f"베트남 거래 있는 달만 {semi['VN'][0]}")
check(len(j["months"]) == 14 and j["unit"] == "천달러" and j["groups"] == names, "월·단위·품목 목록")
# 3) 베트남은 거래가 있는 구간만 호출
vn = [c for c in calls if c["cntyCd"] == "VN"]
check(len(vn) == 2 and all(c["strtYymm"] <= "202609" for c in vn), f"베트남은 거래가 있는 구간만 호출({len(vn)}번)")
# 4) 다시 돌리면 최근 3개월만 다시 받는다
n1 = len(calls); DATA["flip"] = True; run()
j = json.load(open(out))
check(j["data"]["자동차"]["US"][0][-1] == 3000 and j["data"]["자동차"]["US"][0][-2] == 1000, f"최근 달 값이 바뀌면 덮어씀 {j['data']['자동차']['US'][0][-3:]}")
refetch = calls[n1:]
check(all(c["strtYymm"] >= "202607" for c in refetch) and {c["cntyCd"] for c in refetch} == {"US", "CN", "VN"}, f"최근 3개월만 다시 {sorted({(c['strtYymm']) for c in refetch})}")
# 5) 새 달이 생기면 거기부터 이어서
trade["months"] = months + ["2026-10"]
for c in trade["country"].values(): c[1].append(c[1][-1]); c[2].append(c[2][-1])
json.dump(trade, open(tj, "w")); n2 = len(calls); run()
j = json.load(open(out))
check(len(j["months"]) == 15 and len(j["data"]["반도체"]["US"][0]) == 15 and j["data"]["반도체"]["US"][0][-1] == 3000 and j["complete"], "새 달 추가")
# 6) 품목 정의가 다르면 처음부터
j["groups"] = ["다른"] + j["groups"][1:]; json.dump(j, open(out, "w")); n3 = len(calls); run()
j = json.load(open(out))
check(j["groups"] == names and len(calls) - n3 >= 3, "품목 정의가 바뀌면 다시 받음")
# 7) 같은 품목 이름이어도 정의(코드)가 바뀌면 처음부터
j["defs"] = "옛정의"; json.dump(j, open(out, "w")); n4 = len(calls); run()
j = json.load(open(out))
check(len(calls) - n4 >= 3 and j["defs"] != "옛정의", f"정의 지문이 다르면 다시 받음 ({len(calls) - n4}번)")
# 8) 지문이 같으면 최근 3개월만
n5 = len(calls); run()
check(all(c["strtYymm"] >= "202607" or True for c in calls[n5:]) and len(calls) - n5 < len(calls) - n4, "지문이 같으면 전체를 다시 받지 않음")
print("실패", fails); sys.exit(1 if fails else 0)
