import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import ast, re, html
from datetime import date
src=open(REPO + "/app.py",encoding="utf-8").read()
want={"parse_income_table","parse_amount_cell","parse_summary_flat","summary_columns","alt_col","is_empty","collect_alt_quarterly"}
consts={"ALT_HEAD","ALT_STOP","ALT_REV_LABELS","ALT_OP_LABELS","ALT_NET_LABELS","PROV_UNITS","SUMMARY_PERIOD"}
class D(dict): pass
ns={"re":re,"html":html,"TODAY":date(2026,10,23),"Q1":"11013","H1":"11012","Q3":"11014","ANNUAL":"11011","get_session":lambda:None,"get_cache_store":lambda:None}
for n in ast.parse(src).body:
    if isinstance(n,ast.FunctionDef) and n.name in want: exec(compile(ast.Module([n],[]),"a","exec"),ns)
    elif isinstance(n,ast.Assign) and getattr(n.targets[0],"id",None) in consts: exec(compile(ast.Module([n],[]),"a","exec"),ns)
def doc(cols,rows):
    t="(2) 개별요약 손익계산서\n(단위 : 백만원) |\n구 분 |\n"+"".join(c+" |\n" for c in cols)
    for name,vals in rows: t+=name+" |\n"+"".join(v+" |\n" for v in vals)
    return t+"2. 연결재무제표\n"
DOCS={
 (2026,"11013"):doc(["제62기 1분기(2026.1.1~2026.03.31)","제61기(2025.1.1~2025.12.31)","제60기(2024.1.1~2024.12.31)"],[("매출액",["66,207","321,039","282,165"]),("영업이익",["(12,367)","17,501","8,869"]),("당기순이익(손실)",["(18,587)","5,861","(1,024)"])]),
 (2026,"11012"):doc(["제62기 반기(2026.1.1~2026.06.30)","제61기 반기(2025.1.1~2025.06.30)","제61기(2025.1.1~2025.12.31)","제60기(2024.1.1~2024.12.31)"],[("매출액",["151,845","118,347","321,039","282,165"]),("영업이익",["(9,219)","(11,084)","17,501","8,869"]),("당기순이익(손실)",["(91,898)","(14,180)","5,861","(1,024)"])]),
 (2025,"11013"):doc(["제61기 1분기(2025.1.1~2025.03.31)","제60기(2024.1.1~2024.12.31)","제59기(2023.1.1~2023.12.31)"],[("매출액",["60,000","282,165","273,560"]),("영업이익",["(5,000)","8,869","5,621"]),("당기순이익(손실)",["(7,000)","(1,024)","(5,948)"])]),
 (2025,"11012"):doc(["제61기 반기(2025.1.1~2025.06.30)","제60기 반기(2024.1.1~2024.06.30)","제60기(2024.1.1~2024.12.31)"],[("매출액",["118,347","110,000","282,165"]),("영업이익",["(11,084)","(3,000)","8,869"]),("당기순이익(손실)",["(14,180)","(2,000)","(1,024)"])]),
 (2025,"11014"):doc(["제61기 3분기(2025.1.1~2025.09.30)","제60기 3분기(2024.1.1~2024.09.30)","제60기(2024.1.1~2024.12.31)"],[("매출액",["200,393","188,565","282,165"]),("영업이익",["(5,427)","(4,454)","8,869"]),("당기순이익(손실)",["(12,905)","(13,157)","(1,024)"])]),
 (2025,"11011"):doc(["제61기(2025.1.1~2025.12.31)","제60기(2024.1.1~2024.12.31)","제59기(2023.1.1~2023.12.31)"],[("매출액",["321,039","282,165","273,560"]),("영업이익",["17,501","8,869","5,621"]),("당기순이익(손실)",["5,861","(1,024)","(5,948)"])]),
}
ns["alt_report_doc"]=lambda cc,y,r,s,st:((ns["parse_income_table"](DOCS[(y,r)]),f"link{y}{r}") if (y,r) in DOCS else (None,None))
data={(y,q):{"rev":None,"op":None,"net":None,"link":None,"bs":None} for y in (2024,2025,2026) for q in (1,2,3,4)}
how=ns["collect_alt_quarterly"]("x",data,2025,2026)
for k in sorted(data):
    d=data[k]
    if d["rev"] is not None or d["op"] is not None: print(k, {x:(None if d[x] is None else round(d[x]/1e6)) for x in("rev","op","net")}, how.get(k))
exp={(2026,1):66207,(2026,2):151845-66207,(2025,1):60000,(2025,2):118347-60000,(2025,3):200393-118347,(2025,4):321039-200393}
bad=[k for k,v in exp.items() if data[k]["rev"] is None or round(data[k]["rev"]/1e6)!=v]
print("틀린 칸:",bad)
