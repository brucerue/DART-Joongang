"""DART 기업 고유번호 전체 목록(상장·비상장)을 CSV로 만든다 (앱의 기업명 검색용). 비상장사는 종목코드 칸이 비어 있다.

- GitHub Actions(.github/workflows/corp_codes.yml)가 매일 실행한다.
- 결과 파일 corp_codes.csv는 워크플로가 저장소의 별도 'corp-codes' 브랜치에 올린다
  (main은 건드리지 않아 앱이 다시 배포되지 않고, data 브랜치와도 섞이지 않는다).
- 앱은 이 파일을 먼저 읽고, 못 읽으면 DART에서 직접 받는다.
필요한 환경변수: DART_API_KEY
사용: python corp_codes.py [--out corp_codes.csv]
"""
import argparse
import csv
import io
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

import requests

DART_CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
MIN_LISTED = 1500  # 상장사가 이보다 적게 나오면 DART 응답이 이상한 것이므로 파일을 만들지 않는다
MIN_ALL = 50000  # 전체(비상장 포함)가 이보다 적어도 이상한 응답으로 본다 (DART 전체 목록은 10만 곳 안팎)
TIMEOUT = (15, 120)


def download_zip(api_key):
    res = requests.get(DART_CORP_URL, params={"crtfc_key": api_key}, timeout=TIMEOUT)
    data = res.content or b""
    if not data.startswith(b"PK"):  # zip이 아니면 오류 응답(XML)이다
        msg = data.decode("utf-8", "ignore")
        status = ET.fromstring(msg).findtext("status") if msg.lstrip().startswith("<") else None
        raise RuntimeError(f"DART 기업 목록을 받지 못했어요 [{status or '?'}]")
    return data


def corp_rows(zip_bytes):
    """zip 안의 CORPCODE.xml -> 전체 줄들 [(고유번호, 회사명, 종목코드)] (비상장사는 종목코드가 빈 글자)"""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        root = ET.fromstring(zf.read("CORPCODE.xml"))
    rows = []
    for node in root.iter("list"):
        code = (node.findtext("stock_code") or "").strip()
        rows.append(((node.findtext("corp_code") or "").strip(),
                     (node.findtext("corp_name") or "").strip(), code))
    return rows


def write_csv(rows, path):
    rows = sorted(set(r for r in rows if r[0] and r[1]), key=lambda r: r[1])
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["corp_code", "corp_name", "stock_code"])
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="corp_codes.csv")
    args = ap.parse_args()
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DART_API_KEY가 없어요.")
    rows = corp_rows(download_zip(api_key))
    listed = sum(1 for r in rows if r[2])
    if listed < MIN_LISTED or len(rows) < MIN_ALL:
        raise SystemExit(f"상장사 {listed}곳·전체 {len(rows)}곳뿐이라 파일을 만들지 않았어요 (정상은 상장사 2,500곳 이상, 전체 5만 곳 이상).")
    n = write_csv(rows, args.out)
    print(f"전체 {n}곳(상장사 {listed}곳)을 {args.out}에 저장했어요.")


if __name__ == "__main__":
    sys.exit(main())
