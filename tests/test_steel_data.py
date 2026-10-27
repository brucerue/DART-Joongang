"""steel_data.py 가짜 응답 테스트 (DART 접속 없음)."""
import io, json, os, sys, tempfile, zipfile
sys.path.insert(0, os.getcwd())
import steel_data

DOC = """<DOCUMENT><TITLE>II. 사업의 내용</TITLE><P>당사는 전기로 2기를 보유</P>
<TITLE>3. 원재료 및 생산설비</TITLE><TABLE><TR><TD>사업부문</TD><TD>생산능력</TD><TD>가동률</TD></TR>
<TR><TD>철근</TD><TD>1,000</TD><TD>66.2%</TD></TR></TABLE>
<TITLE>4. 매출</TITLE><P>매출 내용</P><TITLE>III. 재무에 관한 사항</TITLE>
<P>제조원가 중 전력비는 2,961억원입니다</P></DOCUMENT>"""


def zbytes():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("main.xml", DOC.encode("utf-8"))
    return b.getvalue()


class Resp:
    def __init__(self, data=None, content=None):
        self._d, self.content = data, content
    def json(self):
        return self._d


class Sess:
    calls = []
    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url.endswith("document.xml"):
            return Resp(content=zbytes())
        return Resp({"status": "000", "list": [
            {"fs_div": "CFS", "account_nm": "매출액", "thstrm_amount": "32,034", "rcept_no": "R1"},
            {"fs_div": "CFS", "account_nm": "영업이익", "thstrm_amount": "594", "rcept_no": "R1"},
            {"fs_div": "OFS", "account_nm": "매출액", "thstrm_amount": "30,000", "rcept_no": "R1"}]})


def main():
    d = tempfile.mkdtemp()
    with open(f"{d}/c.csv", "w", encoding="utf-8") as f:
        f.write("corp_code,corp_name,stock_code\n001,가짜제강,000001\n002,비상장제강,\n003,다른회사,000003\n")
    with open(f"{d}/p.json", "w", encoding="utf-8") as f:
        json.dump({"screen": {"001": {"induty": "2411", "cls": "Y"}, "003": {"induty": "303", "cls": "Y"}}}, f)
    os.environ["DART_API_KEY"] = "x"
    steel_data.main(["--corp-csv", f"{d}/c.csv", "--screen", f"{d}/p.json", "--out", f"{d}/o.json",
                     "--years", "2024-2025", "--half", "2026", "--extra", "비상장제강,없는회사"], session=Sess())
    o = json.load(open(f"{d}/o.json", encoding="utf-8"))
    names = [c["name"] for c in o["corps"]]
    assert names == ["가짜제강", "비상장제강"], names
    assert o["extra_missing"] == ["없는회사"]
    c = o["corps"][0]
    assert c["eaf"] is True
    r = c["reports"]["2025"]
    assert r["fin"]["CFS"] == {"rev": 32034, "op": 594} and r["fin"]["OFS"] == {"rev": 30000}
    assert "66.2%" in r["prod_text"] and "| " in r["prod_text"] and "매출 내용" not in r["prod_text"]
    assert any("2,961억원" in s for s in r["power"])
    assert c["reports"]["2026H1"]["doc_done"] and c["reports"]["2024"]["doc_done"]
    # 이어받기: 같은 결과로 다시 돌리면 호출 0번
    Sess.calls.clear()
    steel_data.main(["--corp-csv", f"{d}/c.csv", "--screen", f"{d}/p.json", "--out", f"{d}/o2.json", "--prev", f"{d}/o.json",
                     "--years", "2024-2025", "--half", "2026", "--extra", "비상장제강"], session=Sess())
    assert Sess.calls == [], Sess.calls
    print("ok")


if __name__ == "__main__":
    main()
