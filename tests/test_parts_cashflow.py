"""parts_data.py 현금흐름(--cashflow) 가짜 응답 시험: 계정 찾기, 연결→별도 대체, 이어받기, 한도 멈춤."""
import csv
import json
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())
import parts_data as P  # noqa: E402


def cf_rows(ocf, icf, capex, by_name=False):
    def row(aid, nm, v):
        return {"sj_div": "CF", "account_id": "-표준계정코드 미사용-" if by_name else aid, "account_nm": nm, "thstrm_amount": v}
    return [
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "1"},
        row("ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동으로 인한 현금흐름", ocf),
        row("ifrs-full_CashFlowsFromUsedInInvestingActivities", "투자활동으로 인한 현금흐름", icf),
        row("ifrs-full_PurchaseOfPropertyPlantAndEquipment", "유형자산의 취득", capex),
    ]


# 1) 계정 찾기: ID로, 이름으로, 유형자산 취득 부호 통일
got = P.pick_cashflow(cf_rows("1,000", "(500)", "(300)"))
assert got == {"ocf": 1000, "icf": -500, "capex": 300}, got
got = P.pick_cashflow(cf_rows("2,000", "-700", "400", by_name=True))
assert got == {"ocf": 2000, "icf": -700, "capex": 400}, got
assert P.pick_cashflow([{"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "9"}]) == {}


class R:
    def __init__(self, jo): self.jo, self.content = jo, b""
    def json(self): return self.jo


class Fake:
    def __init__(self, quota_after=None):
        self.n, self.quota_after, self.calls = 0, quota_after, []

    def get(self, url, params=None, timeout=None):
        self.n += 1
        p = url.rsplit("/", 1)[1]
        self.calls.append((p, params.get("corp_code"), params.get("bsns_year"), params.get("fs_div")))
        if self.quota_after and self.n > self.quota_after:
            return R({"status": "020", "message": "한도"})
        assert p == "fnlttSinglAcntAll.json", p  # 직원·재무는 이미 받아 둔 상태라 다시 부르면 안 됨
        c, fs = params["corp_code"], params["fs_div"]
        if c == "B" and fs == "CFS":  # B사는 연결이 없음
            return R({"status": "013"})
        return R({"status": "000", "list": cf_rows("1,000", "(400)", "(250)")})


tmp = tempfile.mkdtemp()
corp_csv = os.path.join(tmp, "corp.csv")
with open(corp_csv, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["corp_code", "corp_name", "stock_code"])
    for i in range(1600):
        w.writerow([f"{i:08d}", f"회사{i}", f"{i:06d}"])
screen = {f"{i:08d}": {"induty": "58221", "cls": "Y", "est": ""} for i in range(1600)}
screen["A"] = screen["B"] = {"induty": "303", "cls": "Y", "est": ""}
with open(corp_csv, "a", encoding="utf-8", newline="") as f:
    csv.writer(f).writerows([["A", "가사", "900001"], ["B", "나사", "900002"]])
years = {str(y): {"done": True, "emp": 10, "rev": 100, "op": 5, "fs": "CFS", "rcept_no": "R"} for y in (2024, 2025)}
prev = {"screen": screen, "corps": [
    {"corp_code": "A", "name": "가사", "stock_code": "900001", "induty": "303", "market": "코스피", "picked_by": "업종코드",
     "years": json.loads(json.dumps(years)), "docs": {"2025": {"none": "x"}}},
    {"corp_code": "B", "name": "나사", "stock_code": "900002", "induty": "303", "market": "코스피", "picked_by": "업종코드",
     "years": json.loads(json.dumps(years)), "docs": {"2025": {"none": "x"}}},
]}
prev_path = os.path.join(tmp, "prev.json")
json.dump(prev, open(prev_path, "w", encoding="utf-8"), ensure_ascii=False)
os.environ["DART_API_KEY"] = "fake"
base = ["--corp-csv", corp_csv, "--years", "2024-2025", "--doc-years", "2025", "--sleep", "0", "--cashflow"]

# 2) 한도에 걸림: A사 2024만 받고 멈춤 → complete 아님
out1 = os.path.join(tmp, "a.json")
P.main(base + ["--out", out1, "--csv", os.path.join(tmp, "a.csv"), "--prev", prev_path], session=Fake(quota_after=1))
a = json.load(open(out1, encoding="utf-8"))
assert a["complete"] is False and a["cf_progress"] == "1/4" and "020" in a["stopped"], (a["cf_progress"], a["stopped"])

# 3) 이어받기: 남은 3건만 부르고, B사는 연결이 없어 별도로 다시 부름
f2 = Fake()
out2 = os.path.join(tmp, "b.json")
P.main(base + ["--out", out2, "--csv", os.path.join(tmp, "b.csv"), "--prev", out1], session=f2)
b = json.load(open(out2, encoding="utf-8"))
assert b["complete"] and b["cf_progress"] == "4/4", b["cf_progress"]
assert len(f2.calls) == 5, f2.calls  # A 2025(1) + B 2024·2025(연결 실패+별도 = 2×2)
cb = {c["name"]: c for c in b["corps"]}
assert cb["가사"]["years"]["2025"]["cf"] == {"ocf": 1000, "icf": -400, "capex": 250, "fs": "CFS"}
assert cb["나사"]["years"]["2024"]["cf"]["fs"] == "OFS"
rows = list(csv.DictReader(open(os.path.join(tmp, "b.csv"), encoding="utf-8-sig")))
assert rows[0]["영업활동현금흐름"] == "1000" and rows[0]["유형자산취득"] == "250", rows[0]

# 4) --cashflow 없이 돌리면 현금흐름을 부르지 않고 complete 판정에도 안 씀
f3 = Fake()
P.main([x for x in base if x != "--cashflow"] + ["--out", os.path.join(tmp, "c.json"), "--csv", os.path.join(tmp, "c.csv"),
                                                 "--prev", prev_path], session=f3)
assert f3.calls == [] and json.load(open(os.path.join(tmp, "c.json"), encoding="utf-8"))["complete"]
# 5) 지난번 '직접 추가' 회사는 --extra 없이 돌려도 빠지지 않음
with open(corp_csv, "a", encoding="utf-8", newline="") as f:
    csv.writer(f).writerow(["C", "다사", "900003"])
prev2 = json.loads(json.dumps(prev))
prev2["screen"]["C"] = {"induty": "289", "cls": "Y", "est": ""}
prev2["corps"].append({"corp_code": "C", "name": "다사", "stock_code": "900003", "induty": "289", "market": "코스피",
                       "picked_by": "직접 추가", "years": json.loads(json.dumps(years)), "docs": {"2025": {"none": "x"}}})
p2 = os.path.join(tmp, "prev2.json")
json.dump(prev2, open(p2, "w", encoding="utf-8"), ensure_ascii=False)
P.main([x for x in base if x != "--cashflow"] + ["--out", os.path.join(tmp, "d.json"), "--csv", os.path.join(tmp, "d.csv"),
                                                 "--prev", p2], session=Fake())
d = json.load(open(os.path.join(tmp, "d.json"), encoding="utf-8"))
names = {c["name"]: c for c in d["corps"]}
assert "다사" in names and names["다사"]["picked_by"] == "직접 추가" and names["다사"]["years"]["2025"]["emp"] == 10, names.keys()
print("현금흐름 시험 통과")
