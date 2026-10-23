# 산업 취재 도우미 — 작업 규칙 (Claude Code용)

중앙일보 나상현 기자의 Streamlit 앱(`app.py`)과 텔레그램 뉴스·공시 알람 시스템. 사용자는 한국어 사용, 짧고 친절한 설명과 단계별 안내를 좋아함(캡처 화면으로 도움받음).

## 반드시 지킬 것
- 이 저장소는 **공개**. 토큰·SECRET·chat id·API 키·PREFS_URL을 파일에 절대 쓰지 않는다. 비밀값은 GitHub Secrets / Streamlit Secrets / Apps Script 속성 / Cloudflare Secrets에만 둔다. 사용자에게 채팅에 붙여 넣으라고 하지 않는다(캡처 시 가려 달라고 안내).
- `st.set_page_config(page_title="산업 취재 도우미")`, `st.title("산업 취재 도우미")`, `st.caption("credit.중앙일보 나상현")`는 사용자가 정한 것 — 바꾸지 않는다.
- 앱 기능을 바꾸면 `app.py`의 `LAST_UPDATED`("YYYY.MM.DD HH:MM", 한국시간)를 올린다. 문구만 바꾼 경우는 올리지 않는다.
- 요청하지 않은 변경은 하지 않는다. 추가 비용이 드는 서비스는 쓰지 않는다.
- 기능을 추가·수정·삭제하면 **같은 커밋에서** `업데이트_기록.md` 맨 위(해당 날짜 아래)에 `시각 [추가/수정/삭제] 내용 · 바뀐 파일 · ▶할 일`을 적는다. 문구만 바꾼 것은 적지 않는다. 시각은 한국시간.
- 발송은 한국시간 **8~19시 안에서만**, 예외 없음. 기본 월~금.
- 텔레그램 웹훅은 `setWebhook`이 아니라 `setWorkerWebhook`(Apps Script)으로 등록한다.
- 변경 후에는 관련 테스트를 가짜 응답으로 돌리고, 바뀐 파일과 올리는 순서를 짧은 한국어로 알린다. 실서버(DART·네이버·구글·텔레그램·Cloudflare·GitHub) 접속이 안 되는 환경이면 그 사실을 솔직히 말한다.

## 구조
- `app.py`: Streamlit 앱 (해외 기업은 재무 조회 안 '해외 섹터', 단일 선택·최대 20곳)(탭: 기업 브리핑, 재무 조회(기업/국내 섹터/해외 섹터), 주가(시세판/특징주/업계 비교/주가·이벤트), 산업 뉴스, 공시 분석, 실적 캘린더, 정기보고서, 재계 순위. '이슈 타임라인' 탭은 숨김 상태(주소 끝 `?lab=1`일 때만 보임)). 섹터·기업·언론사 데이터의 원본.
- `news_digest.py`: 텔레그램 발송. `app.py`의 상수·함수를 AST로 읽어 쓴다(앱을 고치면 알람도 따라 바뀜). `--sync`, `--auto`, `--alert 종류` 옵션.
- `collect_data.py` + `.github/workflows/collect_data.yml`: 매시간 40분, 기사·공시를 `data` 브랜치(`archive.json` 6개월, `recent.json` 7일)에 저장. 앱의 '이슈 타임라인'이 읽음(`archive.json`). `recent.json`은 지금 앱에서 쓰지 않음(빠르게 보기 버튼은 의논 끝에 뺌).
- `stock_calc.py`(주가 탭의 계산, 화면 없음) + `price_history.py` + `.github/workflows/price_history.yml`: 평일 09:40 KRX 일별 시세(섹터 기업 + 재계 상위 50개 그룹 상장 계열사)를 `prices` 브랜치(`prices.json`, 약 1년)에 한 커밋으로 덮어써 저장. GitHub Secrets에 `KRX_API_KEY` 필요. 앱 '주가' 탭이 읽음. KRX는 전일 종가 기준이라 장중 시세 없음.
- `intraday.py` + `.github/workflows/intraday.yml`: 평일 장중(09:05~15:35) 30분마다 추적 종목의 오늘 5분봉(야후 파이낸스 yfinance, 비공식)을 `intraday` 브랜치(`intraday.json`, 한 커밋으로 덮어씀)에 저장. 앱 시세판·특징주의 '장중' 기준이 읽음(전일 종가는 KRX 자료). 야후 자료는 지연·오차가 있을 수 있음.
- `.github/workflows/news_digest.yml`: Cloudflare Worker가 15분마다 호출(정각 전체, 15·30·45분 `단독`).
- `.github/workflows/sync_config.yml`: app.py/news_digest.py 푸시 + 매시 17분에 구글 스크립트로 동기화.
- `apps_script/Code.gs`(텔레그램 명령·승인·백업), `apps_script/worker.js`(Cloudflare Worker). 수정 후 Apps Script는 **새 버전으로 배포**해야 반영됨.
- 문서: `업데이트_기록.md`(기능 변경 로그), `뉴스_자동발송_사용법.md`, `관리자_명령어_정리.md`, `앱_데이터모으기_사용법.md`, `apps_script/*.md`.

## 주의할 함정
- 산업 뉴스·공시 분석·실적 캘린더·텔레그램 알람의 섹터는 조선·해운/방산/철강을 '중후장대'로 묶어 쓴다(`HEAVY_*`, `NEWS_SECTORS`·`ALARM_SECTORS`·`TEAM_PRESETS`, `CAL_GROUPS`). 재무 조회·주가(`INDUSTRY_GROUPS`)는 옛 섹터 그대로. 옛 이름으로 저장된 받는 사람 설정은 `news_digest.py`(`migrate_sectors`)와 `Code.gs`(`migrateSectors_`)가 읽을 때 중후장대로 바꾼다(알림 없음). `news_digest.py`는 `NEWS_SECTORS`의 첫 정의만 읽으니 그 리터럴을 지우지 말 것.
- Worker Cron은 Cron expression `*/15 * * * *` (UTC 기준 아님, 주기 형식).
- 텔레그램 `/` 메뉴 이름은 소문자 영어만 가능(status, sectors, modes, hours, days, off, on, help). 한글 명령은 직접 입력.
- JS 블록 주석 안에 `*/`를 쓰면 주석이 끝난다.
- Telegram 안내 고정(핀)은 08~20시에만 보낸다.
- `data` 브랜치는 orphan 한 커밋으로 덮어쓴다(기록을 쌓지 않음). main에 자료를 푸시하면 Streamlit이 재배포되므로 금지.
