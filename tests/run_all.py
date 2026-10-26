"""저장소 테스트를 한꺼번에 돌린다. 모두 가짜 응답으로 돌아가고 실서버(DART·구글·텔레그램 등)에는 접속하지 않는다.

저장소를 임시 폴더에 통째로 복사해 그 안에서 돌리므로, 테스트가 발송 기록(digest_seen.json) 같은 실제 파일을 건드리지 않는다.
사용:
  python tests/run_all.py            # 전부
  python tests/run_all.py alarm ai   # 파일 이름에 alarm·ai가 들어간 것만
.js 테스트(Apps Script Code.gs 검사)는 node가 있을 때만 돈다.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TIMEOUT = 900  # 테스트 하나당 최대 시간(초). 앱 전체를 띄우는 테스트는 1~3분 걸린다


def main():
    picks = [a.lower() for a in sys.argv[1:]]
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="dj_tests_"))
    work = tmp / "repo"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".streamlit"))
    tests = sorted(p for p in (work / "tests").iterdir() if p.name.startswith("test_") and p.suffix in (".py", ".js"))
    if picks:
        tests = [t for t in tests if any(k in t.name.lower() for k in picks)]
    node = shutil.which("node")
    results = []
    for t in tests:
        if t.suffix == ".js" and not node:
            print(f"⏭  {t.name} (node가 없어 건너뜀)")
            continue
        cmd = [node, str(t)] if t.suffix == ".js" else [sys.executable, str(t)]
        start = time.time()
        try:
            r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=TIMEOUT)
            out, rc = r.stdout + r.stderr, r.returncode
        except subprocess.TimeoutExpired:
            out, rc = "시간 초과", -1
        bad = [line for line in out.splitlines() if "❌" in line]
        ok = rc == 0 and not bad
        print(f"{'✅' if ok else '❌'} {t.name} ({time.time() - start:.0f}초)", flush=True)
        if not ok:
            for line in (bad or out.strip().splitlines()[-8:]):
                print("     " + line)
        results.append(ok)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n통과 {sum(results)}/{len(results)}")
    sys.exit(0 if results and all(results) else 1)


if __name__ == "__main__":
    main()
