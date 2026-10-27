/**
 * Cloudflare Worker: 텔레그램 -> (즉시 200 OK) -> 구글 Apps Script
 *
 * 구글 웹앱은 요청에 302를 돌려줘서 텔레그램이 "실패"로 보고 재전송을 미룬다.
 * 이 Worker가 텔레그램에게는 바로 200을 돌려주고, 같은 내용을 뒤에서 Apps Script로 넘긴다.
 *
 * Worker의 '변수 및 보안 암호(Variables and Secrets)'에 넣을 값 (유형: Secret):
 *   TG_SECRET        텔레그램이 보내는 비밀 헤더 값 (영문·숫자·_·- 만, 예: wH9kQ2LmZx7vB4nR). Apps Script 속성 WORKER_SECRET과 같은 값
 *   APPS_SCRIPT_URL  Apps Script 웹앱 주소 + ?key=SECRET  (…/exec?key=…)
 *   GH_TOKEN         (정시 발송용) GitHub 토큰: 이 저장소의 Actions 쓰기 권한만 준 Fine-grained token. 유형: Secret
 *   GH_REPO          (정시 발송용) 저장소 이름 "계정/저장소" (예: brucerue/DART-Joongang). 유형: 텍스트
 *   그리고 Settings -> Triggers -> Cron Triggers 에 Cron expression으로 별-슬래시-15 형태(아래 CRON 상수 참고)를 한 줄 추가. 정각만 쓰던 기존 줄은 지우세요.
 *
 * 정시 발송: 한국시간 평일 8~19시에
 *   - 매시 정각(:00)      -> 통신·단독·공시 전체 발송
 *   - :15, :30, :45       -> '단독'만 확인해서, 단독을 켜 둔 사람에게 새 단독 기사가 있으면 바로 발송
 * GitHub Actions의 '산업 뉴스 텔레그램 발송'을 바로 실행시킨다.
 * (GitHub 자체 예약은 20~30분씩 밀려서, 시각 지키는 일을 여기서 맡는다.)
 * 장중 시세: 같은 15분 틱에서 평일 09:15~16:15(한국시간)에는 '장중 시세 모으기'(intraday.yml)도 실행시킨다.
 */
const SEND_HOURS = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]; // 한국시간
// Cron Triggers에 넣을 값(UTC 기준, 15분마다):  */15 * * * *
const WORKFLOW_FILE = "news_digest.yml";
// 장중 시세 모으기(intraday.yml)도 여기서 15분마다 실행시킨다: 한국시간 평일 09:15 ~ 16:15 (GitHub 예약은 늦거나 빠져서)
// 야후 5분봉이 15~20분 늦게 나오므로 09:00 회차는 건너뛰고, 16:00·16:15 회차는 마감 뒤 일봉 종가 보정용
const INTRADAY_FILE = "intraday.yml";
const INTRADAY_FROM = 9 * 60 + 15, INTRADAY_TO = 16 * 60 + 15; // 분 단위(한국시간)
// 기사·공시 모아두기(collect_data.yml)도 매시 정각에 실행시킨다 (하루 종일, 주말 포함 — 발송이 아니라 앱 산업 뉴스·오늘의 현황이 바로 보여 줄 자료라서)
const COLLECT_FILE = "collect_data.yml";

function intradayDue(kst) {
  const day = kst.getUTCDay(); // 0=일 … 6=토 (한국시간으로 옮긴 Date라 UTC getter가 한국 요일)
  if (day === 0 || day === 6) return false;
  const m = kst.getUTCHours() * 60 + kst.getUTCMinutes();
  return m >= INTRADAY_FROM && m <= INTRADAY_TO;
}

async function dispatch(env, inputs, file = WORKFLOW_FILE) {
  const url = `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${file}/dispatches`;
  let last = "";
  for (let i = 0; i < 3; i++) {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "news-digest-scheduler", "Content-Type": "application/json",
        },
        body: JSON.stringify(inputs ? { ref: "main", inputs } : { ref: "main" }),
      });
      if (r.status === 204) return true;
      last = `${r.status} ${(await r.text()).slice(0, 200)}`;
    } catch (e) { last = String(e); }
    await new Promise((res) => setTimeout(res, 2000));
  }
  console.error("GitHub 실행 요청 실패: " + last);
  const code = Number(last.slice(0, 3));
  const hint = code === 401 || code === 403 ? "GitHub 토큰(GH_TOKEN)이 만료됐거나 권한이 없어요. 새 토큰을 만들어 GH_TOKEN을 바꿔 주세요."
    : code === 404 ? "저장소 이름(GH_REPO)이나 워크플로 파일 이름을 확인하세요."
    : code === 422 ? "워크플로 설정(입력값)이 맞지 않아요. GitHub의 news_digest.yml이 최신인지 확인하세요."
    : "GitHub에 연결하지 못했어요.";
  await alertAdmin(env, "dispatch", `정시 발송을 시작하지 못했어요 (${last.slice(0, 60)}).\n${hint}`);
  return false;
}

// 관리자(나) 텔레그램으로 오류 알림: 이미 연결된 Apps Script가 대신 보내 준다 (같은 종류는 1시간에 한 번만)
async function alertAdmin(env, kind, text) {
  try {
    await fetch(env.APPS_SCRIPT_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alert: true, kind, text }), redirect: "manual",
    });
  } catch (e) { console.error("오류 알림 전달 실패: " + e); }
}

export default {
  async scheduled(event, env, ctx) {
    const kst = new Date(event.scheduledTime + 9 * 3600 * 1000); // UTC getter로 한국시간을 읽는다
    const hour = kst.getUTCHours();
    if (intradayDue(kst)) ctx.waitUntil(dispatch(env, null, INTRADAY_FILE)); // 장중 시세 모으기 (평일 09:15~16:15)
    if (kst.getUTCMinutes() < 5) ctx.waitUntil(dispatch(env, null, COLLECT_FILE)); // 기사·공시 모아두기 (매시 정각)
    if (!SEND_HOURS.includes(hour)) return;  // 시간 밖은 쉼 (요일은 받는 사람마다 정해요: 기본 월~금, '/요일'로 토·일도 선택)
    const quick = kst.getUTCMinutes() >= 5; // 정각이 아니면 단독만
    ctx.waitUntil(dispatch(env, quick ? { mode: "단독", auto: "true" } : { auto: "true" }));
  },
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TG_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const body = await request.text();
    // 텔레그램에는 기다리게 하지 않고, 뒤에서 Apps Script로 넘긴다
    ctx.waitUntil(
      fetch(env.APPS_SCRIPT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        redirect: "manual",   // Apps Script의 302를 따라가지 않는다(처리는 이미 끝난 뒤)
      }).catch(() => {})
    );
    return new Response("ok");
  },
};
