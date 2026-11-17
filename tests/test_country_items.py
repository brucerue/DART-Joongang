"""country_items.collect: 가짜 응답으로 12개월 나눠 받기·총계 줄 빼기·합치기 확인"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import country_items as ci

calls = []


def fake_get(key, path, **p):
    calls.append((p["strtYymm"], p["endYymm"], p["cntyCd"]))
    rows = [{"year": "총계", "hsCd": "", "impDlr": "999"}]
    if p["strtYymm"] == "202501":
        rows += [
            {"year": "2025.01", "hsCd": "8703801010", "statKor": "전기 승용차", "impDlr": "1,000", "impWgt": "50", "expDlr": "0"},
            {"year": "2025.01", "hsCd": "8703801010", "statKor": "전기 승용차", "impDlr": "500", "impWgt": "", "expDlr": "7"},
        ]
    else:
        rows += [{"year": "2026.02", "hsCd": "8507600000", "statKor": "리튬이온 축전지", "impDlr": "300", "expDlr": "0"}]
    return rows


items = ci.collect("k", "CN", "202501", "202602", get=fake_get)
assert calls == [("202501", "202512", "CN"), ("202601", "202602", "CN")], calls
ev = items["8703801010"]
assert ev["n"] == "전기 승용차" and ev["imp"] == {"2025-01": 1500} and ev["iw"] == {"2025-01": 50} and ev["exp"] == {"2025-01": 7}, ev
assert items["8507600000"]["imp"] == {"2026-02": 300} and "exp" not in items["8507600000"]
assert len(items) == 2
print("ok test_country_items")
