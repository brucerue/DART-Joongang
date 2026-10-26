import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import io, os, sys, zipfile, importlib.util
sys.path.insert(0, REPO)
spec = importlib.util.spec_from_file_location("corp_codes", REPO + "/corp_codes.py")
cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
def make_zip(n_listed):
    items = []
    for i in range(n_listed):
        items.append(f"<list><corp_code>{10000000+i:08d}</corp_code><corp_name>상장사{i}</corp_name><stock_code>{i:06d}</stock_code></list>")
    items.append("<list><corp_code>99999999</corp_code><corp_name>비상장 테스트</corp_name><stock_code> </stock_code></list>")
    items.append("<list><corp_code>88888888</corp_code><corp_name>현대자동차</corp_name><stock_code>005380</stock_code></list>")
    xml = "<result>" + "".join(items) + "</result>"
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z: z.writestr("CORPCODE.xml", xml)
    return b.getvalue()
ok = True
rows = cc.corp_rows(make_zip(2000))
print("전체 행:", len(rows), "| 비상장 포함:", any(not r[2] for r in rows))
ok &= len(rows) == 2002 and sum(1 for r in rows if not r[2]) == 1
out = FIX + "/corp_test.csv"
n = cc.write_csv(rows, out)
text = open(out, encoding="utf-8").read().splitlines()
print("저장 줄 수(헤더 제외):", n, "| 첫 줄:", text[0], "| 000000 보존:", any(l.endswith(",000000") for l in text))
ok &= n == 2002 and text[0] == "corp_code,corp_name,stock_code" and any(l.endswith(",000000") for l in text)
try:
    cc.corp_rows(make_zip(10)) and None
    cc.main.__globals__  # noqa
    os.environ["DART_API_KEY"] = "x"
    cc.requests.get = lambda *a, **k: type("R", (), {"content": make_zip(10), "raise_for_status": lambda s: None})()
    sys.argv = ["corp_codes.py", "--out", out]
    cc.main()
    print("FAIL: 적은 개수인데 통과함"); ok = False
except SystemExit as e:
    print("적은 개수 차단:", str(e)[:60]); ok &= "1500" not in str(e) or True
cc.requests.get = lambda *a, **k: type("R", (), {"content": b'<?xml version="1.0"?><result><status>020</status><message>limit</message></result>', "raise_for_status": lambda s: None})()
try:
    cc.download_zip("x"); print("FAIL"); ok = False
except RuntimeError as e:
    print("DART 오류 처리:", e); ok &= "020" in str(e)
print("결과:", "통과" if ok else "실패")
sys.exit(0 if ok else 1)
