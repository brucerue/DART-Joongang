import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import ast, re
src=open(REPO + "/news_digest.py",encoding="utf-8").read()
ns={"re":re}
for n in ast.parse(src).body:
    if isinstance(n,ast.FunctionDef) and n.name in ("plain_name","parse_choice"): exec(compile(ast.Module([n],[]),"a","exec"),ns)
opts=["자동차","배터리","조선·해운","방산","철강","정유·석화","에너지·소재","반도체","IT·통신"]
al={"차팀":["자동차","배터리"],"전체":opts}
for words in (["조선해운"],["정유석화","반도체"],["조선"],["조선·해운"],["에너지소재"],["it통신"],["IT통신"],["통신"],["해운"],["차팀"],["없는말"],["조선","해운"],["에너지"]):
    print(words, ns["parse_choice"](words,opts,al))
print(", ".join(ns["plain_name"](o) for o in opts))
