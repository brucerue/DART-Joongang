"""Streamlit Community Cloud 앱이 잠들지 않게(접속이 없으면 약 12시간 뒤 잠듦) 실제 브라우저로 앱을 열어 둔다.
잠들어 있으면 'Yes, get this app back up!' 버튼을 누르고, 앱 제목이 보일 때까지 기다린다.
GitHub Actions(.github/workflows/keep_awake.yml)가 하루 네 번 실행한다. 앱 주소는 공개 주소라 비밀값이 아니다."""
import sys
import time

from playwright.sync_api import sync_playwright

APP_URL = "https://dartjoongang.streamlit.app/"
TITLE = "산업 취재 도우미"
WAIT_SEC = 240


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=90_000)
        start = time.time()
        woke = False
        while time.time() - start < WAIT_SEC:
            btn = page.get_by_role("button", name="Yes, get this app back up!")
            if not woke and btn.count():
                btn.first.click()
                woke = True
                print("잠들어 있어서 깨우기 버튼을 눌렀어요")
            for frame in page.frames:  # 앱 화면은 iframe 안에 있다
                try:
                    if frame.get_by_text(TITLE, exact=False).count():
                        print(f"앱이 열렸어요 ({time.time() - start:.0f}초)")
                        time.sleep(20)  # 잠깐 머물러 접속으로 잡히게
                        browser.close()
                        return 0
                except Exception:
                    pass
            time.sleep(5)
        print("앱 제목을 찾지 못했어요 (앱 오류이거나 깨우는 데 오래 걸림)", file=sys.stderr)
        page.screenshot(path="keep_awake.png", full_page=True)
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
