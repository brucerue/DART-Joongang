"""섹터_키워드.md가 app.py와 같은지 (섹터·키워드를 바꾸면 `python sector_keywords.py`로 다시 만들 것)"""
import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])
import os, sys
sys.path.insert(0, REPO)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake")
import sector_keywords as sk
want = sk.build()
have = open(os.path.join(REPO, "섹터_키워드.md"), encoding="utf-8").read()
if want != have:
    print("  ❌ 섹터_키워드.md가 app.py와 달라요. `python sector_keywords.py`로 다시 만드세요.")
    sys.exit(1)
print("  ✅ 섹터_키워드.md 최신")
