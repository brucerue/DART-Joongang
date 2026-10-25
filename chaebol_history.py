"""공정위 '공시대상기업집단 지정결과' 보도자료 별첨(엑셀)에서 연도별 기업집단 순위·자산·계열회사 수와 계열회사 변동 내역을 모은다 (재계 순위 탭의 '연도별 이력'용, 화면 없음).

- 보도자료 첨부 묶음(ftc.go.kr 보도자료 글 번호 nttSn)을 받아 안쪽 zip까지 풀어 엑셀을 찾는다. 2019~2025년 자료가 엑셀로 있다(2016~2018년은 한글(hwp)뿐이라 제외).
- 엑셀 파일 이름은 해마다 달라서 이름이 아니라 첫 줄들의 열 제목('기업집단명'·'동일인'·'계열회사수' / '구분'·'사유'·'회사명')으로 어떤 표인지 알아낸다.
- 결과: chaebol_history.json {"updated","years":[...],"groups":{집단명:{연도:{"rank","owner","n","asset"}}},"changes":{집단명:{연도:[{"t":편입|제외,"r":사유,"c":회사}]}}}  (자산은 십억원, 순위는 그해 공정자산 순)
  집단 이름은 공정위가 쓴 그대로이고(해마다 표기가 바뀌는 집단은 앱에서 이름을 맞춘다), 변동 내역은 '그 연도 지정 직전 1년'의 변동이다.
- 워크플로(.github/workflows/chaebol_history.yml)가 손으로 실행해 'chaebol' 브랜치에 한 커밋으로 올린다.
사용: python chaebol_history.py [--out chaebol_history.json]
"""
import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime

import requests

BOARD = {2019: 41119, 2020: 41476, 2021: 41915, 2022: 42416, 2023: 42836, 2024: 43406, 2025: 46053}  # 연도 -> 공정위 보도자료 글 번호
UA = {"User-Agent": "Mozilla/5.0"}


def fname(n):
    """zip 안 파일 이름(한글이 깨져 있음)을 바로잡는다"""
    for enc in ("euc-kr", "utf-8"):
        try:
            return n.encode("cp437").decode(enc)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return n


def walk_xlsx(z):
    """zip(안쪽 zip 포함) 안의 엑셀 파일 -> (이름, 바이트)"""
    for info in z.infolist():
        name = fname(info.filename)
        low = name.lower()
        if low.endswith(".zip"):
            try:
                yield from walk_xlsx(zipfile.ZipFile(io.BytesIO(z.read(info))))
            except zipfile.BadZipFile:
                continue
        elif low.endswith(".xlsx"):
            yield name, z.read(info)


def sheet_rows(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = []
    for ws in wb.worksheets[:1]:
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row))
    return rows


def txt(v):
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def header_index(rows, *words, limit=12):
    """앞쪽 limit줄 중 words가 모두 들어 있는 줄의 위치 (없으면 None)"""
    for i, row in enumerate(rows[:limit]):
        cells = " ".join(txt(c) for c in row)
        if all(w in cells for w in words):
            return i
    return None


def parse_designation(rows):
    """지정 현황 표 -> [{"name","owner","rank","n","asset"}] (그해 값만)"""
    h = header_index(rows, "기업집단명", "동일인")
    if h is None:
        return None
    head = [txt(c) for c in rows[h]]
    c_name = head.index("기업집단명")
    c_owner = head.index("동일인")
    c_n = next(i for i, c in enumerate(head) if "계열회사" in c)
    c_asset = next(i for i, c in enumerate(head) if "자산총액" in c or "공정자산" in c)
    out = []
    for row in rows[h + 1:]:
        name = txt(row[c_name]) if c_name < len(row) else ""
        rank, n, asset = num(row[0]), num(row[c_n]) if c_n < len(row) else None, num(row[c_asset]) if c_asset < len(row) else None
        if not name or rank is None or n is None or asset is None:
            continue
        out.append({"name": name, "owner": txt(row[c_owner]), "rank": int(rank), "n": int(n), "asset": asset})
    return out


def parse_changes(rows):
    """계열회사 변동 내역 표 -> [{"group","t","r","c"}] (구분: 편입/제외). 집단 이름이 비어 있는 줄은 위 줄의 집단"""
    h = header_index(rows, "구분", "사유", "회사명")
    if h is None:
        return None
    out, group = [], ""
    for row in rows[h + 1:]:
        cells = [txt(c) for c in row if c is not None and txt(c) != ""]
        if len(cells) < 3 or cells[-3] not in ("편입", "제외"):
            continue
        # 집단 이름: 줄의 앞쪽 문자 칸(숫자가 아닌 첫 칸). 없으면 위 줄 것을 이어 쓴다
        for c in cells[:-3]:
            if num(c) is None and not re.fullmatch(r"[`'’]?\d+\.\d+\.\d+\.?", c):
                group = c
                break
        out.append({"group": group, "t": cells[-3], "r": cells[-2], "c": cells[-1]})
    return out


def collect(year, ntt):
    res = requests.get("https://www.ftc.go.kr/www/downloadBbsFileAll.do", params={"atchmnflNo": str(ntt)}, headers=UA, timeout=120)
    res.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(res.content))
    des, chg, seen = None, None, []
    for name, data in walk_xlsx(z):
        try:
            rows = sheet_rows(data)
        except Exception as e:  # 열 수 없는 파일은 건너뛴다
            seen.append(f"{name[:20]}(열기 실패 {type(e).__name__})")
            continue
        if des is None:
            got = parse_designation(rows)
            if got:
                des = got
                seen.append(f"지정현황:{name[-22:]}")
                continue
        if chg is None:
            got = parse_changes(rows)
            if got is not None:
                chg = got
                seen.append(f"변동:{name[-22:]}")
    return des, chg, seen


def build():
    groups, changes, years, notes = {}, {}, [], []
    for year, ntt in sorted(BOARD.items()):
        try:
            des, chg, seen = collect(year, ntt)
        except Exception as e:
            notes.append(f"{year}: 실패 {type(e).__name__} {str(e)[:80]}")
            continue
        if not des:
            notes.append(f"{year}: 지정 현황 표를 못 찾음 {seen}")
            continue
        years.append(year)
        for g in des:
            groups.setdefault(g["name"], {})[str(year)] = {k: g[k] for k in ("rank", "owner", "n", "asset")}
        for c in chg or []:
            changes.setdefault(c["group"], {}).setdefault(str(year), []).append({"t": c["t"], "r": c["r"], "c": c["c"]})
        notes.append(f"{year}: 집단 {len(des)}곳(1위 {des[0]['name']} {des[0]['asset']:,.0f}십억), 변동 {len(chg or [])}건, {seen}")
    return {"updated": datetime.now().strftime("%Y-%m-%dT%H:%M"), "years": years, "groups": groups, "changes": changes}, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="chaebol_history.json")
    args = ap.parse_args()
    data, notes = build()
    for n in notes:
        print("::notice::" + n)
    if len(data["years"]) < 5:
        raise SystemExit(f"읽은 연도가 {len(data['years'])}개뿐이라 저장하지 않았어요.")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"저장: {len(data['groups'])}개 집단, {len(data['years'])}개 연도")
    return 0


if __name__ == "__main__":
    sys.exit(main())
