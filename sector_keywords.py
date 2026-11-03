"""app.py의 섹터·기업·키워드를 읽어 '섹터_키워드.md'를 만든다 (작업할 때 한눈에 보는 용도, 화면 없음).
섹터·키워드를 바꾸면 `python sector_keywords.py`로 다시 만든다. tests/test_sector_doc.py가 파일이 최신인지 확인한다."""
import os
import sys

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "unused")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import news_digest as nd  # noqa: E402  (app.py의 상수를 AST로 읽어 둔 것)

OUT = os.path.join(HERE, "섹터_키워드.md")
LAB = ("제약·바이오",)  # 관리자 주소에서만 보이는 섹터 (app.py LAB_SECTORS와 같게)


def build():
    A = nd.A
    news, ind, merged = A["NEWS_SECTORS"], A["INDUSTRY_GROUPS"], A.get("MERGED_SECTORS") or {}
    teams = A["TEAM_PRESETS"]
    lines = ["# 섹터별 키워드", "",
             "> `python sector_keywords.py`로 app.py에서 자동으로 만든 파일이에요. 직접 고치지 말고 app.py를 고친 뒤 다시 만드세요.", "",
             "## 팀", ""]
    for t, secs in teams.items():
        lines.append(f"- **{t}**: {' · '.join(secs)}")
    lines.append(f"- **바이오팀** (관리자 주소에서만): {' · '.join(LAB)}")
    lines += ["", "## 섹터", "",
              "- **검색 기업·키워드**: 산업 뉴스·오늘의 현황·텔레그램 알람이 기사 제목에서 찾는 말",
              "- **재무·주가·공시**: DART 등록 이름. 재무 분석 '국내' 합산(10곳까지)·주가·공시 알람 대상", ""]
    order = [s for s in A["ALARM_SECTORS"] if s in news] + [s for s in news if s not in A["ALARM_SECTORS"] and s not in LAB] + [s for s in news if s in LAB]
    for sec in order:
        v = news[sec]
        tag = " (관리자 주소에서만)" if sec in LAB else ""
        lines.append(f"### {sec}{tag}")
        lines.append("")
        lines.append(f"- 검색 기업({len(v['기업'])}): {', '.join(v['기업'])}")
        lines.append(f"- 키워드({len(v['키워드'])}): {', '.join(v['키워드'])}")
        parts = merged.get(sec)
        if parts:
            for p in parts:
                lines.append(f"- 재무·주가·공시 [{p}]: {', '.join(ind.get(p, []))}")
        elif sec in ind:
            lines.append(f"- 재무·주가·공시: {', '.join(ind[sec])}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(build())
    print("만듦:", OUT)
