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
- 변경 후에는 관련 테스트를 가짜 응답으로 돌리고(`python tests/run_all.py [이름 일부]`: 저장소를 임시 폴더에 복사해 돌리므로 digest_seen.json 등 실제 파일을 건드리지 않음. 새 기능엔 `tests/test_*.py`를 더하고, 화면을 바꾸면 해당 테스트도 함께 고친다), 바뀐 파일과 올리는 순서를 짧은 한국어로 알린다. 실서버(DART·네이버·구글·텔레그램·Cloudflare·GitHub) 접속이 안 되는 환경이면 그 사실을 솔직히 말한다.

## 구조
- `app.py`: Streamlit 앱 (해외 기업은 재무 조회 안 '해외 섹터', 단일 선택·최대 20곳)(탭: 기업 브리핑, 재무 분석(개별/국내/해외/AI 검색), AI 무역 통계(AI 검색/일반 검색[종류: 일반(품목×지역 월별·분기·연도 추이+증감, 산업부 잠정치 행)/국가별 순위/품목별 구성(HS 2→10자리 내려가기)]/월간/10일/국가), 산업 뉴스(통신/단독/일반/외신; 일반=단독 표기 없는 기사 전부, 통신처럼 스포츠 기사 거름, 알람은 정각에만·원하는 사람만), 공시 분석(오늘의 공시/기업/섹터/AI 검색), 실적 캘린더, 정기보고서(AI 분석/주주·지분/임직원/배당·자사주), 재계 순위, 주가 분석(장중 현황/시세판/업계 비교/이벤트). **관리자용 주소** 끝에 `?lab=1`을 붙이면 맨 아래(알람 버튼 밑)에 관리자 탭 '사용 통계'·'타임라인'이 보임. 위 탭 줄은 일반 사용자와 같음). 섹터·기업·언론사 데이터의 원본.
- AI 호출: `app.py`의 `ai_generate`가 Gemini(`GEMINI_API_KEY`)를 먼저 쓰고 실패하면 Claude(`ANTHROPIC_API_KEY`)로 넘어감(429면 `GEMINI_REST_SEC` 동안 Gemini 건너뜀). 화면에는 어느 모델을 썼는지 표시하지 않는다. 둘 다 실패하면 한 가지 문구만(관리자 주소에선 원인 덧붙임). 실제 호출은 `log_ai`가 'AI·Gemini'/'AI·Claude(대체)'로 사용 기록에 남기고 관리자 '사용 통계'가 따로 보여 줌.
- `calc_tools.py`: 제목 옆 '🧮 계산기'의 계산(화면 없음): 숫자 읽기(`parse_num`: 쉼표·조·억·△·괄호), 증감 판정, 단위·기사체 금액, 한글↔영어 단위, 연평균 성장률, 비중, 글자 수, 영업일(토·일만 제외).
- `ai_search.py`: 재무·공시 AI 검색의 계산(화면 없음). AI가 준 조건을 허용 값으로 거르고(연도·날짜·지표·공시 묶음), AI가 원문에서 옮겼다는 값이 원문에 정말 있는지 대조(`value_in`), 금액 글자를 원 단위로(`parse_won`). AI는 조건만 정하고 숫자는 DART 자료·원문에서 나오게 하는 것이 원칙(무역 통계 AI 검색과 같은 방식).
- `news_digest.py`: 텔레그램 발송. `app.py`의 상수·함수를 AST로 읽어 쓴다(앱을 고치면 알람도 따라 바뀜). `--sync`, `--auto`, `--alert 종류` 옵션.
- `collect_data.py` + `.github/workflows/collect_data.yml`: 매시간 40분, 기사·공시를 `data` 브랜치(`archive.json` 1년, `recent.json` 7일)에 저장. 앱의 '타임라인'(맨 아래 관리자 탭)이 읽음(`archive.json`). `recent.json`은 지금 앱에서 쓰지 않음(빠르게 보기 버튼은 의논 끝에 뺌).
- `backfill_data.py` + `.github/workflows/backfill_data.yml`: 손으로 한 번 돌리는 '타임라인' 과거 채우기(공시=DART 날짜별, 기사=구글 뉴스 `after:/before:` 기간 검색; 단독(`news_*`)과 통신(`wire_*`) 기간·간격 따로, 섹터 '전체' 가능). 같은 `data` 브랜치 archive.json에 합침. 구글이 막으면 일부 빠질 수 있음.
- `stock_calc.py`(주가 탭의 계산, 화면 없음) + `price_history.py` + `.github/workflows/price_history.yml`: 평일 09:40 KRX 일별 시세(섹터 기업 + 재계 상위 50개 그룹 상장 계열사)를 `prices` 브랜치(`prices.json`, 약 1년)에 한 커밋으로 덮어써 저장. GitHub Secrets에 `KRX_API_KEY` 필요. 앱 '주가' 탭이 읽음. KRX는 전일 종가 기준이라 장중 시세 없음.
- `intraday.py` + `.github/workflows/intraday.yml`: 평일 장중 Cloudflare Worker(`worker.js`)가 09:15~16:15 KST에 15분마다 실행(GitHub cron은 16:10 예비만). 추적 종목의 오늘 5분봉(야후 파이낸스 yfinance, 비공식)을 `intraday` 브랜치(`intraday.json`, 한 커밋으로 덮어씀)에 저장. 앱 장중 현황·시세판의 '장중' 기준이 읽음(전일 종가는 KRX 자료). 야후 자료는 지연·오차가 있을 수 있음.
- `trade_calc.py`(수출 탭의 계산, 화면 없음) + `trade_data.py` + `.github/workflows/trade_data.yml`: 매일 08:30·18:00 관세청 수출입 통계(공공데이터포털 API: 총괄·국가별·경제권별·대륙별(`continenttradet/getContinenttradeList`, 끝 t 주의)·시도별·세관별 월별 최근 10년, 10일 단위 잠정치)를 `trade` 브랜치(`trade.json`, 한 커밋 덮어쓰기)에 저장. GitHub Secrets에 `CUSTOMS_API_KEY` 필요(Encoding·Decoding 어느 쪽이든). 앱 '수출입 분석' 탭이 읽음. API 주소는 `trade_data.py` 상단. 10일 단위 잠정치는 항목 이름 없이 순번(1~10위)만 줘서 앱의 `TRADE_RANK_NAMES`에 확인된 이름만 적어 둠(기술문서로 확인되면 채움).
- `trade_groups.py` + `.github/workflows/trade_groups.yml`: 산업부 20대 주력 수출품목 × 국가 × 월별 수출입(최근 10년, 천달러)을 `groups` 브랜치(`groups.json`)에 저장. 관세청 품목별 국가별 API가 국가만 지정하면 HS 10자리 전체를 주는 점을 이용해 나라마다 받아 HSK-MTI 연계표(기준년도별)로 20대 품목에 묶음. 나라별 `done_to`로 이어받기, 모든 나라가 끝나면 `complete=true`(앱은 complete일 때만 사용; 일반 검색·국가별 순위의 '20대' 품목이 이 자료를 씀. 중량 지표는 관세청 직접 조회), 매일 최근 3개월 재수집. 품목 정의(`MTI_GROUPS`)가 바뀌면 정의 지문(`defs`)이 달라져 처음부터 다시 받음. 호출 첫 채우기 약 2,500번.
- `motie_flash.py`: 산업통상부 매월 1일 '○월 수출입 동향' 보도자료(PDF, `pdftotext -layout`)의 '참고' 표(월별 총괄·20대 품목·주요 지역 수출입, 백만 달러)를 읽고 검산하는 파서(화면 없음). 검산이 틀리면 ValueError로 저장을 막음. 목록은 motie.go.kr 보도자료 게시판(`/kor/article/ATCL3f49a5a8c`, 글마다 pdf·hwp 첨부)에서 GitHub 서버로 받아짐(연결이 가끔 끊겨 재시도 필요).
- `motie_data.py` + `.github/workflows/motie_flash.yml`: 매월 1일 12:10·15:10·18:10(2일 09:10 마지막) KST에 산업부 게시판에서 '○월 수출입 동향' PDF를 받아 `motie_flash.py`로 읽고 `flash` 브랜치(`flash.json`, 한 커밋 덮어쓰기, 최근 14개월)에 저장. 이미 받은 달은 건너뜀. 수동 실행 입력: month, force. 워크플로에서 `poppler-utils`를 설치함.
- `chaebol_history.py` + `.github/workflows/chaebol_history.yml`: 손으로 한 번 돌리는 공정위 2019~2025년 공시대상기업집단 지정 현황·계열회사 변동 내역(엑셀 zip, `BOARD`에 연도별 글 번호)을 읽어 `chaebol` 브랜치(`chaebol_history.json`, 한 커밋 덮어쓰기)에 저장. 앱 '재계 순위'의 그룹 자세히 보기 '연도별 이력'이 읽음(올해 2026 자료는 `chaebol_2026.json`). 새 연도가 나오면 BOARD에 번호를 더하고 다시 실행.
- `parts_data.py` + `.github/workflows/parts_data.yml`: 손으로 실행하는 기획 취재용 수집기. 상장 자동차부품사(DART 기업개황 업종코드 303·304로 시작 + 입력 `extra`) 전수의 연도별 직원 현황(empSttus)·주요계정(fnlttSinglAcnt, 연결 우선)과 고른 해 사업보고서 원문 '주요 제품' 글을 `parts` 브랜치(parts.json·parts.csv, 한 커밋 덮어쓰기)에 저장. 지난 parts.json을 읽어 이어받음(업종 확인 결과 `screen`도 재사용, `fresh=yes`면 처음부터). 지금 상장사만 대상이라 상장폐지 기업은 빠짐. 앱은 아직 안 읽음.
- `steel_data.py` + `.github/workflows/steel_data.yml`: 손으로 실행하는 기획 취재용 수집기. 업종코드 241 상장사(parts.json의 `screen` 재사용) + 입력 `extra`(비상장 가능)의 사업보고서·반기보고서 매출·영업이익(연결·별도), 사업의 내용 중 '생산' 소제목 글(표는 `|`·줄바꿈), 전력비 낱말 앞뒤 글을 `steel` 브랜치(steel.json, 한 커밋 덮어쓰기)에 저장. 원문은 '전기로' 언급 회사만 전부 받음. 지난 steel.json으로 이어받음. 앱은 안 읽음.
- `mti_map.json` + `mti_build.py`: 무역협회 K-stat HSK-MTI 연계표(엑셀)를 앱이 읽는 파일로 바꾼 것(기준년도별 표 3개 + MTI 이름). '수출입 분석' 탭 검색의 '산업부 품목(MTI)'이 HS 10자리를 MTI로 묶는 데 씀. 연계표가 갱신되면 `mti_build.py`로 다시 만든다.
- `.github/workflows/news_digest.yml`: Cloudflare Worker가 15분마다 호출(정각 전체, 15·30·45분 `단독`). 알람 종류는 `ALARM_MODES`(통신·단독·일반·공시), 기본 신청값은 `ALARM_DEFAULT_MODES`(통신·단독·공시). 텔레그램 `/종류 전체`는 네 가지 모두. 일반은 통신·단독으로 이미 간 기사를 다시 보내지 않음.
- `.github/workflows/sync_config.yml`: app.py/news_digest.py 푸시 + 매시 17분에 구글 스크립트로 동기화.
- 기능 사용 통계: `app.py`의 `log_use`/`log_if`/`track_choice`가 Streamlit Secrets `APPS_SCRIPT_URL`(Apps Script 웹앱 주소+?key=…)로 기능 이름·시각·세션 번호만 POST(`{use:true,…}`) → `Code.gs`의 `handleUse_`가 구글 시트 '사용기록'(속성 USAGE_SHEET_ID)에 추가, `usageStats_`(GET `?usage=1&days=N`)가 집계. 앱 `?lab=1` 맨 아래 관리자 탭 '사용 통계'가 읽음. `?lab=1`·`?me=1`로 연 세션은 본인(me=1)으로 보내 집계에서 뺄 수 있음. 새 기능을 만들면 조회 버튼에 `log_if(...)`, 보기 라디오에 `track_choice(...)`를 붙일 것.
- `apps_script/Code.gs`(텔레그램 명령·승인·백업), `apps_script/worker.js`(Cloudflare Worker). 수정 후 Apps Script는 **새 버전으로 배포**해야 반영됨.
- 문서: `업데이트_기록.md`(기능 변경 로그), `뉴스_자동발송_사용법.md`, `관리자_명령어_정리.md`, `앱_데이터모으기_사용법.md`, `apps_script/*.md`.

## 주의할 함정
- 산업 뉴스·공시 분석·실적 캘린더·텔레그램 알람의 섹터는 조선·해운/방산/철강을 '중후장대'로 묶어 쓴다(`HEAVY_*`, `NEWS_SECTORS`·`ALARM_SECTORS`·`TEAM_PRESETS`, `CAL_GROUPS`). 재무 조회·주가(`INDUSTRY_GROUPS`)는 옛 섹터 그대로. 옛 이름으로 저장된 받는 사람 설정은 `news_digest.py`(`migrate_sectors`)와 `Code.gs`(`migrateSectors_`)가 읽을 때 중후장대로 바꾼다(알림 없음). `news_digest.py`는 `NEWS_SECTORS`의 첫 정의만 읽으니 그 리터럴을 지우지 말 것.
- 공시 묶음은 세 층이다: `MAJOR_DISCLOSURES`(텔레그램 공시 알람 범위·기업 브리핑 '알람 주요'), `EXTRA_DISCLOSURES`(앱 전용 추가 묶음), `DISC_VIEW_GROUPS`(공시 화면 보기 버튼 6개 = 위 둘을 합친 표시용). 알람 범위를 바꾸려면 MAJOR만, 화면 버튼을 바꾸려면 DISC_VIEW_GROUPS를 고친다. AI 검색(`DISC_AI_KINDS`)은 세부 묶음 이름을 쓴다.
- 섹터 이름을 바꾸면(예: 에너지·소재→에너지) `app.py`의 `SECTOR_RENAMES`(옛 이름→새 이름, 리터럴)와 `Code.gs`의 `SECTOR_RENAMES`에 함께 적는다. 앱은 모아 둔 자료(`_fetch_stored`)를, `news_digest.py`(`migrate_sectors`)와 `Code.gs`(`migrateSectors_`)는 받는 사람 설정을 읽을 때 새 이름으로 바꾼다.
- Worker Cron은 Cron expression `*/15 * * * *` (UTC 기준 아님, 주기 형식).
- 텔레그램 `/` 메뉴 이름은 소문자 영어만 가능(status, sectors, modes, hours, days, off, on, help). 한글 명령은 직접 입력.
- JS 블록 주석 안에 `*/`를 쓰면 주석이 끝난다.
- Telegram 안내 고정(핀)은 08~20시에만 보낸다.
- `data` 브랜치는 orphan 한 커밋으로 덮어쓴다(기록을 쌓지 않음). main에 자료를 푸시하면 Streamlit이 재배포되므로 금지.
- 수출입 검색의 지역(경제권·대륙) 구성국은 `trade_calc.py`의 `ZONES`·`ZONE_WINDOW`(기간별 구성국: ASEAN 티모르 2026-01~, EU 영국 ~2020-12)·`CONTINENTS`에 있고, 관세청 경제권·대륙 월별 합계(GCC는 2022-01부터만 있음)와 국가별 합계가 최근 10년 모든 달 정확히 같도록 trade.json으로 검산해 맞춘 것이다(새 국가가 생기거나 합계가 어긋나면 다시 검산할 것).
