import pathlib as _pl
REPO = str(_pl.Path(__file__).resolve().parents[1])  # 저장소 폴더 (tests/run_all.py는 임시 복사본에서 돌린다)
FIX = str(_pl.Path(__file__).resolve().parent / "fixtures")  # 시험용 자료
import ast, time, sys
src = open(REPO + "/app.py", encoding="utf-8").read()
tree = ast.parse(src)
want = {"AiError", "call_gemini", "call_claude", "ai_generate"}
code = "\n".join(ast.get_source_segment(src, n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in want)
calls = []
class Res:
    def __init__(self, code, js): self.status_code = code; self._js = js
    def json(self): return self._js
LOG = []
class _QP(dict):
    pass
class Sess:
    def __init__(self, gem_code, claude_code=200): self.gem_code = gem_code; self.claude_code = claude_code
    def post(self, url, json=None, headers=None, timeout=None):
        if "googleapis" in url:
            calls.append("gemini")
            if self.gem_code == 200:
                return Res(200, {"candidates": [{"content": {"parts": [{"text": "제미나이 답"}]}}]})
            return Res(self.gem_code, {"error": {"message": "x"}})
        calls.append("claude")
        if self.claude_code != 200: return Res(self.claude_code, {"error": {"message": "credit balance is too low"}})
        return Res(200, {"content": [{"type": "text", "text": "클로드 답"}]})
import types
def make(gem_code, gem_key="g", ant_key="a", claude_code=200, lab=False):
    ns = {"time": time, "sys": sys, "AI_SYSTEM": "s", "AI_TIMEOUT": 5, "GEMINI_KEY": gem_key, "ANTHROPIC_KEY": ant_key,
          "GEMINI_MODEL": "gm", "GEMINI_FALLBACK": "gm-lite", "ANTHROPIC_MODEL": "cm", "GEMINI_REST_SEC": 600, "_GEMINI_REST": {"until": 0.0}}
    ns["get_session"] = lambda: Sess(gem_code, claude_code)
    ns["log_ai"] = lambda k: LOG.append(k)
    ns["st"] = types.SimpleNamespace(query_params={"lab": "1"} if lab else {})
    exec(code, ns); ns["time"].sleep = lambda s: None
    return ns
fails = []
def check(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: fails.append(m)
ns = make(200); calls.clear()
check(ns["ai_generate"]("q") == ("제미나이 답", "gm") and calls == ["gemini"], "평소: 제미나이")
ns = make(429); calls.clear()
check(ns["ai_generate"]("q")[0] == "클로드 답" and calls == ["gemini", "claude"], f"한도 소진: 클로드로 {calls}")
calls.clear(); ns["ai_generate"]("q")
check(calls == ["claude"], f"한도 뒤 10분은 제미나이 건너뜀 {calls}")
ns["_GEMINI_REST"]["until"] = 0; ns["get_session"] = lambda: Sess(200); calls.clear()
check(ns["ai_generate"]("q")[0] == "제미나이 답", "10분 지나면 다시 제미나이")
ns = make(503); calls.clear()
check(ns["ai_generate"]("q")[0] == "클로드 답" and calls[-1] == "claude" and ns["_GEMINI_REST"]["until"] == 0, f"서버 혼잡: 클로드로, 쉬지는 않음 ({calls.count('gemini')}번 시도)")
ns = make(429, ant_key=None); calls.clear()
try: ns["ai_generate"]("q"); check(False, "클로드 키 없음")
except Exception as e: check("한도" in str(e), "클로드 키 없으면 제미나이 오류 그대로")
ns = make(200, gem_key=None); calls.clear()
check(ns["ai_generate"]("q")[0] == "클로드 답" and calls == ["claude"], "제미나이 키 없으면 클로드만")
ns = make(429); calls.clear(); ns["ai_generate"]("q", json_mode=True)
check(calls == ["gemini", "claude"], "JSON 모드도 넘어감")
LOG.clear(); ns = make(200); ns["ai_generate"]("q"); ns = make(429); ns["ai_generate"]("q"); ns["ai_generate"]("q")
check(LOG == ["Gemini", "Claude(대체)", "Claude(대체)"], f"호출 기록 {LOG}")
LOG.clear(); ns = make(200, gem_key=None); ns["ai_generate"]("q"); check(LOG == ["Claude"], f"클로드만 있을 때 기록 {LOG}")
ns = make(503, claude_code=400)
try: ns["ai_generate"]("q"); check(False, "둘 다 실패")
except Exception as e:
    check(str(e).startswith("AI가 지금 응답하지 않아요") and "Claude" not in str(e) and "Gemini" not in str(e), f"둘 다 실패: 일반 문구 [{e}]")
ns = make(503, claude_code=400, lab=True)
try: ns["ai_generate"]("q")
except Exception as e: check("관리자용" in str(e) and "크레딧" in str(e), f"관리자 주소는 원인도 [{e}]")
print("실패", fails); sys.exit(1 if fails else 0)
