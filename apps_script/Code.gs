/**
 * 산업 뉴스 알람 봇 - 즉시 응답 서버 (Google Apps Script)
 *
 * 텔레그램이 메시지를 이 웹앱으로 바로 보내주면(웹훅), 명령(/설정 /섹터 /종류 /끄기 /켜기 /도움)과
 * 알람 신청(/start s…m…)을 1~3초 안에 처리하고 답장한다. 설정은 이 프로젝트의 '스크립트 속성'에 저장된다.
 * 뉴스 발송(GitHub Actions)은 이 웹앱의 주소(PREFS_URL)에서 설정을 읽어 간다.
 *
 * 메시지를 받는 방법은 두 가지다.
 *  A) 1분마다 자동 확인(권장): startPolling()을 한 번 실행하면, 1분마다 pollUpdates()가 텔레그램에 새 메시지를 물어 처리한다. 답장은 최대 1분 안.
 *  B) 웹훅(setWebhook): 구글 웹앱은 요청에 302 응답을 돌려줘서 텔레그램이 실패로 보고 재전송을 미루는 일이 있다(메시지가 막힘). 그래서 A를 권장.
 *
 * 설정 방법은 같은 폴더의 '즉시응답_설정방법.md'를 보세요.
 * 스크립트 속성(프로젝트 설정 > 스크립트 속성)에 넣을 값:
 *   BOT_TOKEN      텔레그램 봇 토큰
 *   ADMIN_CHAT_ID  알람 신청 알림을 받을 내 chat id
 *   SUBSCRIBERS    (선택) 받는 사람 목록(JSON). 평소엔 GitHub가 동기화(sync)해서 채워 주므로 안 넣어도 된다
 *   SECRET         아무 긴 영문·숫자 문자열(웹훅 주소를 아무나 못 쓰게 하는 비밀번호)
 *   WEBAPP_URL     배포한 웹 앱 주소(…/exec 로 끝나는 것. 배포 → 배포 관리에서 복사)
 */

// ---- 기본값: 평소에는 GitHub가 동기화(sync)로 아래 값과 받는 사람 목록을 최신으로 덮어쓴다. 동기화가 한 번도 없었을 때만 쓰인다 ----
var ALARM_SECTORS = ["자동차", "배터리", "중후장대", "정유·석화", "에너지", "반도체", "IT·통신", "전자", "항공", "재계"];
var ALARM_MODES = ["통신", "단독", "공시"];
var TEAM_PRESETS = { "차팀": ["자동차", "배터리", "중후장대", "정유·석화", "에너지"], "재계팀": ["반도체", "전자", "항공", "재계"] };
var HEAVY_NAME = "중후장대", HEAVY_PARTS = ["조선·해운", "방산", "철강"];   // 옛 섹터 이름은 중후장대로 읽는다
var SECTOR_RENAMES = { "에너지·소재": "에너지" };   // 이름을 바꾼 섹터: 옛 이름으로 저장된 설정은 새 이름으로 읽는다 (app.py와 같은 표)
var DEFAULT_SECTORS = ["자동차", "배터리", "중후장대", "정유·석화", "에너지"]; // 목록에 섹터를 안 적은 사람의 기본값(news_digest.py의 SECTORS)

// 현재 설정: 동기화로 받은 것(CONFIG)이 있으면 그것, 없으면 위의 기본값
function cfg_() {
  var c = {};
  try { c = JSON.parse(PropertiesService.getScriptProperties().getProperty("CONFIG") || "{}"); } catch (e) { c = {}; }
  return {
    sectors: (c.sectors && c.sectors.length) ? c.sectors : ALARM_SECTORS,
    modes: (c.modes && c.modes.length) ? c.modes : ALARM_MODES,
    teams: c.teams || TEAM_PRESETS,
    defaults: (c.defaults && c.defaults.length) ? c.defaults : DEFAULT_SECTORS
  };
}

var HELP_TEXT =
  "알람 설정 명령어예요.\n" +
  "/설정 - 지금 설정 보기\n" +
  "/섹터 자동차 배터리 - 받을 섹터 바꾸기 (팀 이름 '차팀', '재계팀', '전체'도 돼요)\n" +
  "/종류 통신 공시 - 받을 종류 바꾸기 (통신·단독·공시)\n" +
  "/시간 8-18 - 받을 시간대 (기본 8~19시)\n" +
  "/요일 월-금 일 - 받을 요일 (기본 월~금)\n" +
  "/끄기 - 알람 잠시 멈추기\n" +
  "/켜기 - 다시 받기\n" +
  "/도움 - 이 안내 다시 보기\n" +
  "통신·공시는 매시 정각, 단독과 잠정실적 공시는 15분마다 확인해요.\n" +
  "새 기사나 공시가 없으면 메시지를 보내지 않아요.";

// ---------------------------------------------------------------- 진입점
function doPost(e) {
  var props = PropertiesService.getScriptProperties();
  if (!e || !e.parameter || e.parameter.key !== props.getProperty("SECRET")) {
    return ContentService.createTextOutput("no");
  }
  var update;
  try {
    update = JSON.parse(e.postData.contents);
  } catch (err) {
    return ContentService.createTextOutput("bad");
  }
  if (update.sync) return handleSync_(update);   // GitHub가 보내는 동기화(받는 사람 목록·섹터·팀)
  if (update.alert) return handleAlert_(update); // Worker·GitHub가 보내는 오류 알림
  if (update.run) return handleRun_(update);     // 뉴스 발송이 끝날 때마다 보내는 실행 결과 (/점검에서 보여줌)
  if (update.use) return handleUse_(update);     // 앱이 보내는 기능 사용 기록 (구글 시트 '사용기록'에 한 줄씩)
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var last = Number(props.getProperty("LAST_UPDATE_ID") || 0);
    if (update.update_id && update.update_id <= last) {   // 텔레그램이 같은 메시지를 다시 보내도 한 번만 처리
      return ContentService.createTextOutput("dup");
    }
    if (update.update_id) props.setProperty("LAST_UPDATE_ID", String(update.update_id));
    if (update.callback_query) handleCallback_(update.callback_query);   // 승인·거절 버튼
    else handleMessage_(update.message || {});
  } catch (err) {
    reportError_(err);
  } finally {
    lock.releaseLock();
  }
  return ContentService.createTextOutput("ok");
}

// GitHub가 보낸 최신 받는 사람 목록·섹터·팀을 저장한다 (원본은 GitHub의 DIGEST_SUBSCRIBERS와 app.py)
function handleSync_(u) {
  var props = PropertiesService.getScriptProperties();
  try {
    if (!Array.isArray(u.subscribers) || !u.subscribers.length) {
      return ContentService.createTextOutput(JSON.stringify({ ok: false, error: "subscribers 비어 있음" }));
    }
    var subs = u.subscribers.map(function (d) {
      return { name: String(d.name || d.chat_id), chat_id: String(d.chat_id), sectors: d.sectors || [], modes: d.modes || [] };
    });
    props.setProperty("SUBSCRIBERS", JSON.stringify(subs));
    props.setProperty("CONFIG", JSON.stringify({ sectors: u.sectors || [], modes: u.modes || [], teams: u.teams || {}, defaults: u.defaults || [] }));
    setCommandMenu_();  // 매 동기화마다 메뉴를 최신으로 (바뀐 게 없어도 무해)
    ensureButtonUpdates_();  // 승인 버튼 누름이 Worker로 오도록 웹훅 설정을 맞춘다
    props.setProperty("LAST_SYNC", String(nowMs_()));
    maybeBackup_();          // 하루에 한 번(아침 9시 이후 첫 동기화) 설정 백업을 관리자에게 보냄
    var guided = guideNew_(loadSubscribers_());
    return ContentService.createTextOutput(JSON.stringify({ ok: true, subscribers: subs.length, guided: guided })).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    reportError_(err);
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: String(err) }));
  }
}

// 아직 명령어 안내를 못 받은 사람에게 한 번만 안내를 보낸다 (한국시간 08~20시에만. 보내기에 실패하면 다음 동기화 때 다시 시도)
function guideNew_(subs) {
  var hourKst = (new Date().getUTCHours() + 9) % 24;
  if (hourKst < 8 || hourKst >= 20) return 0;  // 밤·새벽에는 보내지 않고, 다음 동기화(최대 1시간 뒤) 낮 시간에 보낸다
  var prefs = loadPrefs_(), n = 0;
  subs.forEach(function (s) {
    var mine = prefs[s.chat_id] || (prefs[s.chat_id] = {});
    if (mine.pinned) return;  // 고정 안내까지 받은 사람은 건너뜀 (예전에 안내만 받은 사람은 한 번 더 받고 고정됨)
    if (sendGuide_(s.chat_id, mine)) n++;
    else if (!Object.keys(mine).length) delete prefs[s.chat_id];
  });
  savePrefs_(prefs);
  return n;
}

// ---------------------------------------------------------------- 점검(/점검)과 백업
function nowMs_() { return new Date().getTime(); }
function kstFmt_(ms) {
  var d = new Date(Number(ms) + 9 * 3600 * 1000), p = function (n) { return (n < 10 ? "0" : "") + n; };
  return (d.getUTCMonth() + 1) + "/" + d.getUTCDate() + " " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes());
}
function kstDate_(ms) {
  var d = new Date(Number(ms) + 9 * 3600 * 1000), p = function (n) { return (n < 10 ? "0" : "") + n; };
  return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate());
}
function jsonProp_(key) {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty(key) || "null"); } catch (e) { return null; }
}

// 뉴스 발송(GitHub)이 끝날 때마다 결과를 알려 온다: 전체(정각) / 단독(15분)
function handleRun_(u) {
  var props = PropertiesService.getScriptProperties();
  var kind = u.kind === "단독" ? "quick" : "full";
  props.setProperty("RUN_" + kind, JSON.stringify({ at: nowMs_(), people: Number(u.people) || 0, delivered: Number(u.delivered) || 0, errors: Number(u.errors) || 0, skipped: !!u.skipped }));
  return ContentService.createTextOutput(JSON.stringify({ ok: true })).setMimeType(ContentService.MimeType.JSON);
}

function runLine_(label, r) {
  if (!r) return label + ": 아직 기록 없음";
  var base = label + ": " + kstFmt_(r.at);
  if (r.skipped) return base + " (이 시간에 받을 사람이 없어 건너뜀)";
  return base + " (받을 사람 " + r.people + "명 중 " + r.delivered + "명에게 새 소식 발송" + (r.errors ? ", 실패 " + r.errors + "건" : "") + ")";
}

function statusText_() {
  var props = PropertiesService.getScriptProperties();
  var gh = []; try { gh = JSON.parse(props.getProperty("SUBSCRIBERS") || "[]"); } catch (e) {}
  var ap = loadApproved_(), pend = Object.keys(loadPending_()).length, prefs = loadPrefs_();
  var paused = loadSubscribers_().filter(function (s) { return (prefs[s.chat_id] || {}).paused; }).length;
  var fail = jsonProp_("LAST_FAIL"), sync = props.getProperty("LAST_SYNC"), bk = props.getProperty("LAST_BACKUP");
  return "📊 점검\n" +
    "받는 사람: " + (gh.length + ap.length) + "명 (GitHub " + gh.length + " · 앱 승인 " + ap.length + ")" + (paused ? ", 일시 멈춤 " + paused + "명" : "") +
    (pend ? "\n승인 대기·보류: " + pend + "건 (/대기)" : "") + "\n" +
    runLine_("마지막 정시 발송", jsonProp_("RUN_full")) + "\n" +
    runLine_("마지막 단독 확인", jsonProp_("RUN_quick")) + "\n" +
    "마지막 동기화: " + (sync ? kstFmt_(sync) : "기록 없음") + "\n" +
    "마지막 설정 백업: " + (bk || "아직 없음") + "\n" +
    "최근 오류: " + (fail ? kstFmt_(fail.at) + " " + fail.text : "없음") + "\n" +
    "메시지 수신 방식: " + (props.getProperty("MODE") === "worker" ? "Worker(즉시)" : "1분 확인");
}

// 설정 백업(1주일에 한 번): 사람들의 개인 설정(PREFS)·승인 명단·대기 명단을 파일로 만들어 관리자 텔레그램으로 보낸다 (토큰·비밀 값은 넣지 않음)
function sendBackup_(note) {
  var props = PropertiesService.getScriptProperties();
  var admin = props.getProperty("ADMIN_CHAT_ID");
  if (!admin) return false;
  var data = { created: kstFmt_(nowMs_()), prefs: loadPrefs_(), approved: loadApproved_(), pending: loadPending_() };
  var blob = Utilities.newBlob(JSON.stringify(data, null, 1), "application/json", "alarm_backup_" + kstDate_(nowMs_()) + ".json");
  try {
    var res = UrlFetchApp.fetch("https://api.telegram.org/bot" + props.getProperty("BOT_TOKEN") + "/sendDocument", {
      method: "post", muteHttpExceptions: true,
      payload: { chat_id: String(admin), caption: "🗄 알람 설정 백업 (" + kstDate_(nowMs_()) + ")" + (note ? "\n" + note : "") +
        "\n개인 설정 " + Object.keys(data.prefs).length + "명 · 승인 명단 " + data.approved.length + "명\n구글 설정이 사라졌을 때 복구에 쓰는 파일이에요. 보관만 하세요.", document: blob }
    });
    if (res.getResponseCode() !== 200) { console.error("백업 전송 실패: " + res.getContentText()); return false; }
    props.setProperty("LAST_BACKUP", kstDate_(nowMs_()));
    return true;
  } catch (e) { console.error("백업 오류: " + e); return false; }
}

// 마지막 백업 후 7일이 지나면, 한국시간 낮(09~20시)의 첫 동기화 때 자동으로 (밤에는 알림이 가지 않게)
function maybeBackup_() {
  try {
    var hour = (new Date().getUTCHours() + 9) % 24;
    if (hour < 9 || hour >= 20) return;
    var last = PropertiesService.getScriptProperties().getProperty("LAST_BACKUP");
    if (last && (Date.parse(kstDate_(nowMs_())) - Date.parse(last)) / 86400000 < 7) return;
    sendBackup_("");
  } catch (e) { console.error(e); }
}

// 오류 알림을 관리자(나) 텔레그램으로 전달한다. 같은 종류는 1시간에 한 번만 (15분마다 도는 실행이 계속 실패해도 알림이 쏟아지지 않게)
function handleAlert_(u) {
  var props = PropertiesService.getScriptProperties();
  try {
    var kind = String(u.kind || "etc").replace(/[^A-Za-z0-9_]/g, "").slice(0, 20) || "etc";
    props.setProperty("LAST_FAIL", JSON.stringify({ kind: kind, at: nowMs_(), text: String(u.text || "").slice(0, 120) }));   // /점검에서 보여줌 (알림은 1시간 1회만 가도 기록은 남김)
    var last = Number(props.getProperty("ALERT_" + kind) || 0), now = Date.now();
    if (now - last < 3600000) return ContentService.createTextOutput(JSON.stringify({ ok: true, skipped: true })).setMimeType(ContentService.MimeType.JSON);
    props.setProperty("ALERT_" + kind, String(now));
    var admin = props.getProperty("ADMIN_CHAT_ID");
    var sentOk = admin ? sendRaw_(admin, "⚠ " + esc_(String(u.text || "오류가 있었어요").slice(0, 600))) : false;
    return ContentService.createTextOutput(JSON.stringify({ ok: !!sentOk })).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    console.error(err);
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: String(err) }));
  }
}

// 오류는 로그(보이지 않을 수 있음)와 함께 관리자 텔레그램으로도 알린다
function reportError_(err) {
  var msg = err && err.stack ? err.stack : String(err);
  console.error(msg);
  try {
    var admin = PropertiesService.getScriptProperties().getProperty("ADMIN_CHAT_ID");
    if (admin) sendRaw_(admin, "⚠ 알람 봇 오류\n<code>" + esc_(msg.slice(0, 600)) + "</code>");
  } catch (e2) { /* 알림도 실패하면 어쩔 수 없다 */ }
}

// 1분마다 도는 함수: 텔레그램에 새 메시지가 있는지 묻고 처리한다 (startPolling이 이 함수를 1분 간격으로 예약)
function pollUpdates() {
  var props = PropertiesService.getScriptProperties();
  if (props.getProperty("MODE") === "worker") return;   // Cloudflare Worker로 받는 중이면 1분 확인은 쉰다
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  try {
    var last = Number(props.getProperty("LAST_UPDATE_ID") || 0);
    var url = "https://api.telegram.org/bot" + props.getProperty("BOT_TOKEN") + "/getUpdates?timeout=0&allowed_updates=" +
      encodeURIComponent('["message","callback_query"]') + (last ? "&offset=" + (last + 1) : "");
    var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    var js = JSON.parse(res.getContentText());
    if (!js.ok) {
      console.error("getUpdates 실패: " + res.getContentText());
      if (js.error_code === 409 && props.getProperty("MODE") !== "worker") {   // 웹훅이 켜져 있으면 getUpdates가 거부된다 -> 스스로 웹훅을 끈다(쌓인 메시지는 보존)
        UrlFetchApp.fetch("https://api.telegram.org/bot" + props.getProperty("BOT_TOKEN") + "/deleteWebhook", { muteHttpExceptions: true });
      }
      return;
    }
    js.result.forEach(function (u) {
      props.setProperty("LAST_UPDATE_ID", String(u.update_id));   // 먼저 기록해서 같은 메시지를 두 번 처리하지 않게
      try {
        if (u.callback_query) handleCallback_(u.callback_query); else handleMessage_(u.message || {});
      } catch (err) { reportError_(err); }
    });
  } finally {
    lock.releaseLock();
  }
}

// 뉴스 발송(GitHub)이 설정을 읽어 가는 주소: <웹앱 주소>?key=SECRET
function doGet(e) {
  var props = PropertiesService.getScriptProperties();
  if (!e || !e.parameter || e.parameter.key !== props.getProperty("SECRET")) {
    return ContentService.createTextOutput("no");
  }
  if (e.parameter.usage) return usageStats_(Number(e.parameter.days) || 30);   // 앱 관리자 화면(?lab=1)의 사용 통계
  var out = loadPrefs_();
  out._approved = loadApproved_();   // 앱 신청을 승인 버튼으로 받아들인 사람들 (뉴스 발송이 GitHub 명단에 합쳐서 씀)
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

// ---------------------------------------------------------------- 앱 사용 기록 (구글 시트)
// 앱이 기능을 쓸 때마다 {use:true, sid:세션번호, me:0|1, items:[{f:기능, t:시각}]}를 보낸다. 개인정보 없이 기능 이름·시각·무작위 세션 번호만.
// 시트는 처음 쓸 때 내 드라이브에 '산업 취재 도우미 사용기록'으로 만들고 속성 USAGE_SHEET_ID에 기억한다.
var USAGE_SHEET_NAME = "사용기록", USAGE_MAX_ROWS = 200000;

function usageSheet_() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty("USAGE_SHEET_ID"), ss = null;
  if (id) { try { ss = SpreadsheetApp.openById(id); } catch (err) { ss = null; } }
  if (!ss) {
    ss = SpreadsheetApp.create("산업 취재 도우미 사용기록");
    props.setProperty("USAGE_SHEET_ID", ss.getId());
  }
  var sh = ss.getSheetByName(USAGE_SHEET_NAME);
  if (!sh) {
    sh = ss.getSheets()[0]; sh.setName(USAGE_SHEET_NAME);
    sh.appendRow(["날짜", "시각", "세션", "기능", "나"]);
  }
  return sh;
}

function handleUse_(u) {
  try {
    var items = Array.isArray(u.items) ? u.items.slice(0, 50) : [];
    var sid = String(u.sid || "").replace(/[^A-Za-z0-9]/g, "").slice(0, 12) || "-";
    var me = u.me ? 1 : 0, rows = [];
    for (var i = 0; i < items.length; i++) {
      var t = String(items[i].t || ""), f = String(items[i].f || "").slice(0, 60);
      if (!f || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(t)) continue;
      rows.push([t.slice(0, 10), t.slice(11, 16), sid, f, me]);
    }
    if (!rows.length) return ContentService.createTextOutput(JSON.stringify({ ok: true, n: 0 })).setMimeType(ContentService.MimeType.JSON);
    var lock = LockService.getScriptLock(); lock.waitLock(10000);
    try {
      var sh = usageSheet_();
      sh.getRange(sh.getLastRow() + 1, 1, rows.length, 5).setValues(rows);
      if (sh.getLastRow() > USAGE_MAX_ROWS) sh.deleteRows(2, 20000);   // 너무 쌓이면 오래된 것부터 지운다
    } finally { lock.releaseLock(); }
    return ContentService.createTextOutput(JSON.stringify({ ok: true, n: rows.length })).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    console.error(err);
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: String(err) })).setMimeType(ContentService.MimeType.JSON);
  }
}

// 최근 days일 통계: {days:[{d, sessions, sessions_others, n, n_others}], features:[{f, n, n_others, sessions}], from, to}
function usageStats_(days) {
  try {
    var sh = usageSheet_(), last = sh.getLastRow();
    var from = new Date(Date.now() + 9 * 3600000 - days * 86400000).toISOString().slice(0, 10);   // 한국시간 기준 날짜
    var byDay = {}, byFeat = {}, out = { days: [], features: [], from: from, to: "" };
    if (last >= 2) {
      var vals = sh.getRange(2, 1, last - 1, 5).getValues();
      for (var i = 0; i < vals.length; i++) {
        var d = vals[i][0] instanceof Date ? Utilities.formatDate(vals[i][0], "Asia/Seoul", "yyyy-MM-dd") : String(vals[i][0]);
        if (d < from) continue;
        var sid = String(vals[i][2]), f = String(vals[i][3]), me = Number(vals[i][4]) === 1;
        if (!byDay[d]) byDay[d] = { s: {}, so: {}, n: 0, no: 0 };
        byDay[d].s[sid] = 1; byDay[d].n++;
        if (!me) { byDay[d].so[sid] = 1; byDay[d].no++; }
        if (!byFeat[f]) byFeat[f] = { n: 0, no: 0, s: {} };
        byFeat[f].n++; byFeat[f].s[sid] = 1;
        if (!me) byFeat[f].no++;
        if (d > out.to) out.to = d;
      }
    }
    Object.keys(byDay).sort().forEach(function (d) {
      out.days.push({ d: d, sessions: Object.keys(byDay[d].s).length, sessions_others: Object.keys(byDay[d].so).length, n: byDay[d].n, n_others: byDay[d].no });
    });
    Object.keys(byFeat).forEach(function (f) { out.features.push({ f: f, n: byFeat[f].n, n_others: byFeat[f].no, sessions: Object.keys(byFeat[f].s).length }); });
    out.features.sort(function (a, b) { return b.n - a.n; });
    return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    console.error(err);
    return ContentService.createTextOutput(JSON.stringify({ error: String(err) })).setMimeType(ContentService.MimeType.JSON);
  }
}

// ---------------------------------------------------------------- 메시지 처리
function handleMessage_(msg) {
  var body = String(msg.text || "").trim();
  var chat = msg.chat || {};
  var cid = String(chat.id === undefined ? "" : chat.id);
  if (!cid || body.charAt(0) !== "/") return;   // 잡담은 무시

  var m = body.match(/^\/start(?:@\w+)?\s+s([01]+)m([01]+)\s*$/);
  if (m) {   // 알람 신청 (앱의 '알람 받기' 링크)
    if (m[1].length !== cfg_().sectors.length || m[2].length !== cfg_().modes.length) return;
    handleSignup_(chat, cid, m[1], m[2]);
    return;
  }
  var adminId = String(PropertiesService.getScriptProperties().getProperty("ADMIN_CHAT_ID") || "");
  if (adminId && cid === adminId && handleAdminCommand_(body, cid)) return;   // 관리자 전용 명령
  var subs = loadSubscribers_();
  var sub = null;
  for (var i = 0; i < subs.length; i++) if (subs[i].chat_id === cid) sub = subs[i];
  if (!sub) {
    if (!/^\/start/.test(body)) send_(cid, "아직 알람 받는 사람 목록에 없어요. 앱의 '🔔 알람 받기'로 신청해 주세요.");
    return;
  }
  var prefs = loadPrefs_();
  var effective = applyPrefs_(sub, prefs);
  var reply = runCommand_(body, effective, prefs, cid);
  if (reply === null) return;
  savePrefs_(prefs);
  send_(cid, reply);
}

// ---- 승인 명단 (앱 '알람 받기' 신청을 관리자가 승인 버튼으로 받아들인 사람들) ----
function loadApproved_() {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty("APPROVED") || "[]"); } catch (e) { return []; }
}
function saveApproved_(list) { PropertiesService.getScriptProperties().setProperty("APPROVED", JSON.stringify(list)); }
function loadPending_() {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty("PENDING") || "{}"); } catch (e) { return {}; }
}
function savePending_(p) { PropertiesService.getScriptProperties().setProperty("PENDING", JSON.stringify(p)); }

// 승인·보류·거절 버튼 (보류한 뒤에는 보류 버튼을 뺀다)
function approvalButtons_(cid, withHold) {
  var row = [{ text: "✅ 승인", callback_data: "ok:" + cid }];
  if (withHold) row.push({ text: "⏸ 보류", callback_data: "hold:" + cid });
  row.push({ text: "❌ 거절", callback_data: "no:" + cid });
  return { inline_keyboard: [row] };
}

// 관리자가 승인·보류·거절 버튼을 눌렀을 때
function handleCallback_(cb) {
  var props = PropertiesService.getScriptProperties();
  var admin = String(props.getProperty("ADMIN_CHAT_ID") || "");
  var fromId = String((cb.from || {}).id === undefined ? "" : cb.from.id);
  var m = String(cb.data || "").match(/^(ok|no|hold):(-?\d+)$/);
  if (!m) { tg_("answerCallbackQuery", { callback_query_id: cb.id }); return; }
  if (!admin || fromId !== admin) { tg_("answerCallbackQuery", { callback_query_id: cb.id, text: "관리자만 누를 수 있어요." }); return; }
  var cid = m[2], pending = loadPending_(), entry = pending[cid];
  var msg = cb.message || {}, mchat = (msg.chat || {}).id, mid = msg.message_id;
  if (!entry) {
    tg_("answerCallbackQuery", { callback_query_id: cb.id, text: "이미 처리된 신청이에요." });
    if (mchat !== undefined && mid) tg_("editMessageText", { chat_id: mchat, message_id: mid, text: "이미 처리된 신청이에요.", parse_mode: "HTML" });
    return;
  }
  var who = esc_(entry.name) + " (" + cid + ")";
  if (m[1] === "hold") {   // 보류: 신청은 그대로 남기고, 나중에 /대기 로 모아서 다시 본다 (버튼은 승인·거절만 남김)
    entry.held = true; pending[cid] = entry; savePending_(pending);
    tg_("answerCallbackQuery", { callback_query_id: cb.id, text: "보류했어요. /대기 로 다시 볼 수 있어요." });
    if (mchat !== undefined && mid) tg_("editMessageText", { chat_id: mchat, message_id: mid, parse_mode: "HTML", reply_markup: approvalButtons_(cid, false),
      text: "⏸ 보류함: " + who + "\n섹터: " + esc_(entry.sectors.join(", ") || "-") + "\n종류: " + esc_(entry.modes.join(", ") || "-") + "\n\n나중에 /대기 로 모아서 볼 수 있어요." });
    return;
  }
  delete pending[cid]; savePending_(pending);
  if (m[1] === "ok") {
    var list = loadApproved_().filter(function (a) { return String(a.chat_id) !== cid; });
    list.push(entry); saveApproved_(list);
    tg_("answerCallbackQuery", { callback_query_id: cb.id, text: "승인했어요." });
    if (mchat !== undefined && mid) tg_("editMessageText", { chat_id: mchat, message_id: mid, parse_mode: "HTML",
      text: "✅ 승인함: " + who + "\n섹터: " + esc_(entry.sectors.join(", ") || "-") + "\n종류: " + esc_(entry.modes.join(", ") || "-") +
        "\n\n이름을 바꾸려면 아래를 복사해서 새 이름만 고쳐 보내세요:\n<code>/이름 " + cid + " 새이름</code>" });
    send_(cid, "✅ 승인됐어요! 이제 알람이 시작돼요.\n/도움 으로 섹터·시간·요일을 바꿀 수 있어요.");
    guideNew_(loadSubscribers_().filter(function (s) { return s.chat_id === cid; }));   // 낮(08~20시)이면 고정 안내까지, 밤이면 아침에
  } else {
    tg_("answerCallbackQuery", { callback_query_id: cb.id, text: "거절했어요." });
    if (mchat !== undefined && mid) tg_("editMessageText", { chat_id: mchat, message_id: mid, text: "❌ 거절함: " + who, parse_mode: "HTML" });
  }
}

// 관리자 전용 명령: /명단, /삭제 이름
function handleAdminCommand_(body, cid) {
  var m = body.match(/^\/(명단|list|삭제|remove|이름|rename|대기|pending|점검|health|백업|backup)(?:@\w+)?(?:\s+(.*))?$/i);
  if (!m) return false;
  var cmd = m[1].toLowerCase(), arg = (m[2] || "").trim();
  var gh = []; try { gh = JSON.parse(PropertiesService.getScriptProperties().getProperty("SUBSCRIBERS") || "[]"); } catch (e) {}
  var ap = loadApproved_();
  if (cmd === "명단" || cmd === "list") {
    send_(cid, "받는 사람 " + (gh.length + ap.length) + "명\n\n[GitHub 명단 " + gh.length + "]\n" +
      (gh.map(function (d) { return "· " + (d.name || d.chat_id) + " (" + d.chat_id + ")"; }).join("\n") || "-") + "\n\n[앱 신청·승인 " + ap.length + "]\n" +
      (ap.map(function (d) { return "· " + d.name + " (" + d.chat_id + ")"; }).join("\n") || "-") + (Object.keys(loadPending_()).length ? "\n\n[승인 대기·보류 " + Object.keys(loadPending_()).length + "] /대기 로 확인" : "") +
      "\n\n이름 바꾸기: /이름 chat_id 새이름\n지우기: /삭제 이름");
    return true;
  }
  if (cmd === "점검" || cmd === "health") { send_(cid, statusText_()); return true; }
  if (cmd === "백업" || cmd === "backup") {
    if (!sendBackup_("(요청해서 보낸 백업)")) send_(cid, "백업을 보내지 못했어요. 구글 실행 기록을 확인해 주세요.");
    return true;
  }
  if (cmd === "대기" || cmd === "pending") {   // 승인 대기·보류 중인 신청을 버튼과 함께 다시 보낸다
    var pend = loadPending_(), ids = Object.keys(pend);
    if (!ids.length) { send_(cid, "대기 중인 신청이 없어요."); return true; }
    send_(cid, "대기 중인 신청 " + ids.length + "건이에요.");
    ids.forEach(function (id) {
      var e = pend[id];
      sendRaw_(cid, (e.held ? "⏸ 보류 중\n" : "🔔 대기 중\n") + "이름: " + esc_(e.name) + "\nchat id: " + id + "\n섹터: " + esc_((e.sectors || []).join(", ") || "-") +
        "\n종류: " + esc_((e.modes || []).join(", ") || "-"), approvalButtons_(id, !e.held));
    });
    return true;
  }
  if (cmd === "이름" || cmd === "rename") {
    var parts = arg.split(/\s+/), key = parts.shift(), newName = parts.join(" ").trim();
    if (!key || !newName) { send_(cid, "이렇게 보내주세요: /이름 chat_id 새이름\n예: /이름 123456789 홍길동\n(chat_id는 /명단 에서 확인)"); return true; }
    var found = ap.filter(function (d) { return String(d.chat_id) === key || d.name === key; });
    if (!found.length) {
      var inGh2 = gh.some(function (d) { return d.name === key || String(d.chat_id) === key; });
      send_(cid, inGh2 ? "그분은 GitHub 명단에 있어요. 이름은 GitHub의 DIGEST_SUBSCRIBERS에서 바꿔 주세요." : "승인 명단에 '" + key + "'이(가) 없어요. /명단 으로 chat_id를 확인해 보세요.");
      return true;
    }
    if (found.length > 1) { send_(cid, "같은 이름이 여러 명이에요. chat_id로 지정해 주세요. (/명단)"); return true; }
    var oldName = found[0].name; found[0].name = newName; saveApproved_(ap);
    send_(cid, "이름을 바꿨어요: " + oldName + " → " + newName + " (" + found[0].chat_id + ")");
    return true;
  }
  if (!arg) { send_(cid, "지울 사람의 이름이나 chat id를 같이 보내주세요. 예: /삭제 홍길동"); return true; }
  var hit = ap.filter(function (d) { return d.name === arg || String(d.chat_id) === arg; });
  if (!hit.length) {
    var inGh = gh.some(function (d) { return d.name === arg || String(d.chat_id) === arg; });
    send_(cid, inGh ? "그분은 GitHub 명단(DIGEST_SUBSCRIBERS)에 있어요. GitHub에서 지워 주세요." : "승인 명단에 '" + arg + "'이(가) 없어요. /명단 으로 확인해 보세요.");
    return true;
  }
  saveApproved_(ap.filter(function (d) { return hit.indexOf(d) < 0; }));
  var prefs = loadPrefs_(); hit.forEach(function (d) { delete prefs[String(d.chat_id)]; }); savePrefs_(prefs);
  send_(cid, "지웠어요: " + hit.map(function (d) { return d.name; }).join(", "));
  return true;
}

function handleSignup_(chat, cid, secBits, modeBits) {
  var admin = PropertiesService.getScriptProperties().getProperty("ADMIN_CHAT_ID");
  if (!admin) return;
  var name = [chat.first_name, chat.last_name].filter(Boolean).join(" ") || chat.title || cid;
  var sec = cfg_().sectors.filter(function (x, i) { return secBits.charAt(i) === "1"; });
  var mod = cfg_().modes.filter(function (x, i) { return modeBits.charAt(i) === "1"; });
  var already = loadSubscribers_().some(function (s) { return s.chat_id === cid; });
  if (already) { send_(cid, "이미 알람을 받고 있어요. /설정 으로 확인하고, /도움 으로 바꿀 수 있어요."); return; }
  var pending = loadPending_();
  pending[cid] = { name: name, chat_id: cid, sectors: sec, modes: mod };
  savePending_(pending);
  var tag = chat.username ? " (@" + chat.username + ")" : "";
  var text = "🔔 알람 신청\n이름: " + esc_(name) + esc_(tag) + "\nchat id: " + cid + "\n" +
    "섹터: " + esc_(sec.join(", ") || "-") + "\n종류: " + esc_(mod.join(", ") || "-");
  sendRaw_(admin, text, approvalButtons_(cid, true));
  send_(cid, "신청이 접수됐어요. 확인되면 알람이 시작돼요.");
}

// ---------------------------------------------------------------- 명령
// 저장된 옛 섹터 이름(조선·해운·방산·철강)을 중후장대로 바꾼다 (순서 유지, 중복 제거). 알림은 보내지 않는다.
function migrateSectors_(list) {
  var out = [];
  (list || []).forEach(function (x) { var y = HEAVY_PARTS.indexOf(x) >= 0 ? HEAVY_NAME : (SECTOR_RENAMES[x] || x); if (out.indexOf(y) < 0) out.push(y); });
  return out;
}

function plainName_(t) { return String(t).replace(/[·ㆍ・•∙\s]/g, ""); }  // 모바일에서 중간점을 치기 어려워 '조선해운'으로도 받는다

function parseChoice_(words, options, aliases) {
  var chosen = [], bad = [];
  words.forEach(function (w0) {
    var w = w0.replace(/^[,;]+|[,;]+$/g, "");
    if (!w) return;
    var hits;
    if (aliases && Object.prototype.hasOwnProperty.call(aliases, w)) hits = aliases[w].slice();
    else if (options.indexOf(w) >= 0) hits = [w];
    else {
      var pw = plainName_(w).toLowerCase();
      hits = options.filter(function (o) { return pw && plainName_(o).toLowerCase() === pw; });
      if (!hits.length) hits = options.filter(function (o) { return pw.length >= 2 && plainName_(o).toLowerCase().indexOf(pw) >= 0; });
      if (hits.length !== 1) hits = [];
    }
    if (!hits.length) bad.push(w);
    hits.forEach(function (h) { if (chosen.indexOf(h) < 0) chosen.push(h); });
  });
  return { chosen: chosen, bad: bad };
}

function runCommand_(text, sub, prefs, cid) {
  var m = text.trim().match(/^\/(\S+?)(?:@\w+)?(?:\s+([\s\S]*))?$/);
  if (!m) return null;
  var cmd = m[1].toLowerCase();
  var rest = (m[2] || "").replace(/,/g, " ").split(/\s+/).filter(Boolean);
  var mine = prefs[cid] || (prefs[cid] = {});
  if (["start", "help", "도움", "도움말"].indexOf(cmd) >= 0) return HELP_TEXT;
  if (["설정", "status", "상태"].indexOf(cmd) >= 0) {
    return "지금 설정이에요.\n" + describe_(sub, mine);
  }
  if (["끄기", "off", "stop", "중지"].indexOf(cmd) >= 0) {
    mine.paused = true;
    return "알람을 멈췄어요. 다시 받으려면 /켜기 를 보내주세요.\n\n" + describe_(sub, mine);
  }
  if (["켜기", "on", "resume", "재개"].indexOf(cmd) >= 0) {
    delete mine.paused;
    mine.resumed_at = nowMs_();   // 켠 시각: 이보다 먼저 나온 기사·공시는 보내지 않는다 (뉴스 발송이 읽어 감)
    return "알람을 다시 켰어요. 지금부터 나오는 소식만 받아요.\n\n" + describe_(sub, mine);
  }
  if (["섹터", "sectors", "sector"].indexOf(cmd) >= 0) {
    var C = cfg_(), aliases = {};
    Object.keys(C.teams).forEach(function (t) { aliases[t] = C.teams[t]; });
    aliases["전체"] = C.sectors;
    ["조선·해운", "조선해운", "조선", "해운", "방산", "철강"].forEach(function (o) { if (!(o in aliases)) aliases[o] = [HEAVY_NAME]; });   // 옛 이름은 중후장대로
    ["에너지·소재", "에너지소재", "소재"].forEach(function (o) { if (!(o in aliases)) aliases[o] = ["에너지"]; });   // 옛 이름은 에너지로
    var r = parseChoice_(rest, C.sectors, aliases);
    if (r.bad.length || !r.chosen.length) {
      return (r.bad.length ? "알아듣지 못한 말: " + r.bad.join(", ") : "받을 섹터를 같이 보내주세요. 예: /섹터 자동차 배터리") +
        "\n\n가능한 섹터: " + C.sectors.map(plainName_).join(", ") +
        "\n팀: " + (Object.keys(C.teams).join(", ") || "-") + " / 전체";
    }
    mine.sectors = r.chosen;
    sub.sectors = r.chosen;
    return "✅ 받을 섹터를 바꿨어요. 다음 뉴스 발송부터 적용돼요.\n\n" + describe_(sub, mine);
  }
  if (["요일", "days", "day"].indexOf(cmd) >= 0) {
    var pd = parseDays_(rest);
    if (pd.error) return pd.error;
    if (pd.reset) {
      delete mine.days;
      return "✅ 받는 요일을 기본(월~금)으로 되돌렸어요.\n\n" + describe_(sub, mine);
    }
    mine.days = pd.days;
    return "✅ 받는 요일을 " + daysText_(pd.days) + "로 바꿨어요. 쉬는 날 나온 기사는 다음 받는 날 첫 시간 기준 12시간 안에 나온 것만 와요.\n\n" + describe_(sub, mine);
  }
  if (["시간", "hours", "hour", "time"].indexOf(cmd) >= 0) {
    var txt = rest.join(" ");
    if (["기본", "전체", "reset", "default"].indexOf(txt.trim()) >= 0) {
      delete mine.hours;
      return "✅ 받는 시간을 기본(8~19시, 평일)으로 되돌렸어요.\n\n" + describe_(sub, mine);
    }
    var nums = (txt.match(/\d{1,2}/g) || []).map(Number);
    if (nums.length !== 2) return "시간 범위를 알려주세요. 예: /시간 8-18 (되돌리기: /시간 기본)";
    if (!(nums[0] >= 8 && nums[0] <= nums[1] && nums[1] <= 19)) return "8~19시 안에서만 고를 수 있고, 시작은 끝보다 늦을 수 없어요. 예: /시간 8-18";
    mine.hours = nums;
    return "✅ 받는 시간을 " + nums[0] + "~" + nums[1] + "시로 바꿨어요. 이 시간 밖에 나온 기사는 시작 시각 기준 12시간 안에 나온 것만 와요.\n\n" + describe_(sub, mine);
  }
  if (["종류", "modes", "mode"].indexOf(cmd) >= 0) {
    var r2 = parseChoice_(rest, cfg_().modes, { "전체": cfg_().modes });
    if (r2.bad.length || !r2.chosen.length) {
      return (r2.bad.length ? "알아듣지 못한 말: " + r2.bad.join(", ") : "받을 종류를 같이 보내주세요. 예: /종류 통신 공시") +
        "\n\n가능한 종류: " + cfg_().modes.join(", ") + " / 전체";
    }
    mine.modes = r2.chosen;
    sub.modes = r2.chosen;
    return "✅ 받을 종류를 바꿨어요. 다음 뉴스 발송부터 적용돼요.\n\n" + describe_(sub, mine);
  }
  return "모르는 명령이에요.\n" + HELP_TEXT;
}

var DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"];  // 번호: 월=0 ... 일=6 (파이썬 weekday()와 같음)

function daysText_(days) {
  var d = days.slice().sort(function (a, b) { return a - b; }), runs = [[d[0]]];
  for (var i = 1; i < d.length; i++) {
    var cur = runs[runs.length - 1];
    if (d[i] === cur[cur.length - 1] + 1) cur.push(d[i]); else runs.push([d[i]]);
  }
  return runs.map(function (r) {
    return r.length === 1 ? DAY_NAMES[r[0]] : DAY_NAMES[r[0]] + (r.length === 2 ? "·" : "~") + DAY_NAMES[r[r.length - 1]];
  }).join("·");
}

// '월수금', '월-목', '월-금 일', '평일', '매일' -> {days:[...]} / {reset:true} / {error:"안내"}
function parseDays_(words) {
  var text = words.join("").replace(/\s/g, "").replace(/요일/g, "");
  if (["기본", "reset", "default"].indexOf(text) >= 0) return { reset: true };
  text = text.replace(/평일/g, "월-금").replace(/매일/g, "월-일");
  var order = [0, 1, 2, 3, 4, 5, 6], out = {}, i = 0, any = false;
  while (i < text.length) {
    var c = text.charAt(i);
    if (DAY_NAMES.indexOf(c) < 0) {
      if ("-~,".indexOf(c) >= 0) { i++; continue; }
      return { error: "알아듣지 못했어요. 예: /요일 월수금  /요일 월-금 일 (되돌리기: /요일 기본)" };
    }
    if (i + 2 < text.length && "-~".indexOf(text.charAt(i + 1)) >= 0 && DAY_NAMES.indexOf(text.charAt(i + 2)) >= 0) {
      var ia = order.indexOf(DAY_NAMES.indexOf(c)), ib = order.indexOf(DAY_NAMES.indexOf(text.charAt(i + 2)));
      if (ia > ib) return { error: "범위는 월→일 순서로 써주세요. 예: 월-목" };
      for (var k = ia; k <= ib; k++) out[order[k]] = true;
      i += 3; any = true; continue;
    }
    out[DAY_NAMES.indexOf(c)] = true; any = true; i++;
  }
  if (!any) return { error: "요일을 알려주세요. 예: /요일 월-금 일 (되돌리기: /요일 기본)" };
  return { days: Object.keys(out).map(Number).sort(function (a, b) { return a - b; }) };
}

function describe_(sub, mine) {
  return "상태: " + (mine.paused ? "멈춤 (/켜기 로 다시 받아요)" : "받는 중") +
    "\n섹터: " + sub.sectors.join(", ") + "\n종류: " + sub.modes.join(", ") +
    "\n받는 시간: " + (mine.hours ? mine.hours[0] + "~" + mine.hours[1] + "시" : "8~19시 (기본)") +
    "\n받는 요일: " + (mine.days && mine.days.length ? daysText_(mine.days) : "월~금 (기본)");
}

// ---------------------------------------------------------------- 받는 사람·설정 저장
function loadSubscribers_() {
  var raw = PropertiesService.getScriptProperties().getProperty("SUBSCRIBERS") || "[]";
  var data = JSON.parse(raw), C = cfg_();
  loadApproved_().forEach(function (a) {   // 승인 버튼으로 들어온 사람 (GitHub 명단에 이미 있으면 GitHub 쪽이 우선)
    if (!data.some(function (d) { return String(d.chat_id) === String(a.chat_id); })) data.push(a);
  });
  return data.map(function (d) {
    var teamSectors = [];
    (d.teams || []).forEach(function (t) { teamSectors = teamSectors.concat(C.teams[t] || []); });
    var sectors = [];
    migrateSectors_(d.sectors).concat(teamSectors).forEach(function (s) { if (sectors.indexOf(s) < 0) sectors.push(s); });
    return {
      name: d.name || String(d.chat_id), chat_id: String(d.chat_id),
      sectors: sectors.length ? sectors : C.defaults.slice(),
      modes: (d.modes && d.modes.length) ? d.modes : C.modes.slice()
    };
  });
}

function applyPrefs_(sub, prefs) {
  var mine = prefs[sub.chat_id] || {};
  return {
    name: sub.name, chat_id: sub.chat_id,
    sectors: (mine.sectors && mine.sectors.length) ? migrateSectors_(mine.sectors) : sub.sectors,
    modes: (mine.modes && mine.modes.length) ? mine.modes : sub.modes
  };
}

function loadPrefs_() {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty("PREFS") || "{}"); }
  catch (err) { return {}; }
}

function savePrefs_(prefs) {
  PropertiesService.getScriptProperties().setProperty("PREFS", JSON.stringify(prefs));
}

// ---------------------------------------------------------------- 텔레그램
function esc_(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

function send_(chatId, text) { return sendRaw_(chatId, esc_(text)); }

function sendRaw_(chatId, htmlText, markup) {
  var token = PropertiesService.getScriptProperties().getProperty("BOT_TOKEN");
  var body = { chat_id: chatId, text: htmlText, parse_mode: "HTML", disable_web_page_preview: true };
  if (markup) body.reply_markup = markup;   // 버튼(승인·거절)
  var res = UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/sendMessage", {
    method: "post", contentType: "application/json", muteHttpExceptions: true,
    payload: JSON.stringify(body)
  });
  if (res.getResponseCode() !== 200) { console.error("sendMessage 실패 " + chatId + ": " + res.getContentText()); return false; }
  try { return JSON.parse(res.getContentText()).result.message_id || true; } catch (e) { return true; }  // 보낸 글 번호(고정할 때 씀)
}

// 텔레그램 기능 호출 (실패해도 멈추지 않고 true/false만 돌려준다)
function tg_(method, payload) {
  try {
    var token = PropertiesService.getScriptProperties().getProperty("BOT_TOKEN");
    var res = UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/" + method, {
      method: "post", contentType: "application/json", muteHttpExceptions: true, payload: JSON.stringify(payload)
    });
    if (res.getResponseCode() !== 200) { console.error(method + " 실패: " + res.getContentText()); return false; }
    return true;
  } catch (e) { console.error(method + " 오류: " + e); return false; }
}

// Worker 방식일 때, 텔레그램 웹훅이 버튼 누름(callback_query)도 보내도록 맞춘다 (이미 맞으면 아무것도 안 함)
function ensureButtonUpdates_() {
  try {
    var props = PropertiesService.getScriptProperties();
    if (props.getProperty("MODE") !== "worker") return;
    var token = props.getProperty("BOT_TOKEN");
    var info = JSON.parse(UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/getWebhookInfo", { muteHttpExceptions: true }).getContentText());
    var allowed = (info.result && info.result.allowed_updates) || [];
    if (allowed.length === 0 || allowed.indexOf("callback_query") >= 0) return;   // 비어 있으면 '전부 허용'이라 이미 괜찮음
    var url = props.getProperty("WORKER_URL"), secret = props.getProperty("WORKER_SECRET");
    if (!url || !secret) return;
    tg_("setWebhook", { url: url, secret_token: secret, allowed_updates: ["message", "callback_query"], drop_pending_updates: false });
  } catch (e) { console.error("웹훅 확인 오류: " + e); }
}

// 안내 글을 보내고 채팅 맨 위에 고정한다. 이전에 고정해 둔 안내는 내린다. 보냈으면 true
function sendGuide_(chatId, mine) {
  var id = send_(chatId, "📌 알람 설정을 직접 바꿀 수 있어요.\n\n" + HELP_TEXT);
  if (!id) return false;
  if (mine.guide_msg) tg_("unpinChatMessage", { chat_id: chatId, message_id: mine.guide_msg });
  if (typeof id === "number") {
    tg_("pinChatMessage", { chat_id: chatId, message_id: id, disable_notification: true });
    mine.guide_msg = id;
  }
  mine.guided = true;
  mine.pinned = true;  // 고정 안내를 받았다는 표시 (고정이 안 돼도 다시 보내지는 않는다)
  return true;
}

// 입력창 옆 '/' 메뉴에 명령어 목록을 등록한다 (텔레그램 메뉴 이름은 영문 소문자만 가능해서, 한글 명령의 영문 이름을 쓴다)
function setCommandMenu_() {
  return tg_("setMyCommands", { commands: [
    { command: "status", description: "지금 설정 보기 (/설정)" },
    { command: "sectors", description: "받을 섹터 바꾸기 (/섹터)" },
    { command: "modes", description: "받을 종류 바꾸기: 통신·단독·공시 (/종류)" },
    { command: "hours", description: "받을 시간대 (/시간, 기본 8~19시)" },
    { command: "days", description: "받을 요일 (/요일, 기본 월~금)" },
    { command: "off", description: "알람 잠시 멈추기 (/끄기)" },
    { command: "on", description: "알람 다시 받기 (/켜기)" },
    { command: "help", description: "명령어 안내 다시 보기 (/도움)" }
  ] });
}

// ---------------------------------------------------------------- 직접 실행하는 함수 (편집기 위쪽에서 고르고 ▶ 실행)
// 0) 권장: 1분마다 자동 확인 시작. 웹훅을 끄고(쌓인 메시지는 버림) 1분 간격 확인을 예약한다. 한 번만 실행
function startPolling() {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty("BOT_TOKEN");
  console.log(UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/deleteWebhook?drop_pending_updates=true").getContentText());
  stopPolling();
  props.setProperty("MODE", "polling");
  ScriptApp.newTrigger("pollUpdates").timeBased().everyMinutes(1).create();
  console.log("1분마다 새 메시지를 확인하도록 예약했어요. 이제 봇에게 /설정 을 보내보세요(최대 1분).");
}

// 자동 확인 멈추기
function stopPolling() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "pollUpdates") ScriptApp.deleteTrigger(t);
  });
}

// 뉴스 발송(GitHub)이 설정을 읽어 갈 주소를 보여준다 (GitHub Secret PREFS_URL에 넣을 값). 웹훅은 건드리지 않는다 ← 주소만 필요할 때는 이걸 실행
function showPrefsUrl() {
  var props = PropertiesService.getScriptProperties();
  console.log("PREFS_URL: " + props.getProperty("WEBAPP_URL") + "?key=" + props.getProperty("SECRET"));
}

// 0-2) 즉시 응답: Cloudflare Worker를 웹훅으로 연결. 속성 WORKER_URL(…workers.dev 주소)과 WORKER_SECRET(Worker의 TG_SECRET과 같은 값)을 넣은 뒤 한 번 실행.
//      1분 확인은 쉬게 되고, 되돌리려면 startPolling을 실행하면 된다.
function setWorkerWebhook() {
  var props = PropertiesService.getScriptProperties();
  var url = props.getProperty("WORKER_URL"), secret = props.getProperty("WORKER_SECRET");
  if (!url || !secret) { console.log("속성 WORKER_URL, WORKER_SECRET을 먼저 넣어 주세요."); return; }
  var res = UrlFetchApp.fetch("https://api.telegram.org/bot" + props.getProperty("BOT_TOKEN") + "/setWebhook", {
    method: "post", muteHttpExceptions: true,
    payload: { url: url, secret_token: secret, allowed_updates: JSON.stringify(["message", "callback_query"]), drop_pending_updates: "true" }
  });
  console.log(res.getContentText());
  if (JSON.parse(res.getContentText()).ok) {
    stopPolling();
    props.setProperty("MODE", "worker");
    console.log("Worker로 즉시 받도록 설정했어요. 이제 봇에게 /설정 을 보내 보세요.");
  }
}

// 1) (웹훅 방식, 권장하지 않음 - 1분 확인 방식을 쓸 때는 실행하지 마세요. 웹훅이 켜져서 1분 확인이 막혀요)
function setWebhook() {
  var props = PropertiesService.getScriptProperties();
  // 편집기에서 실행하면 getUrl()이 시험용 주소(…/dev)를 돌려줘서 텔레그램이 못 쓴다. 배포 관리에서 복사한 …/exec 주소를 WEBAPP_URL 속성에 넣어 두고 쓴다.
  var base = props.getProperty("WEBAPP_URL") || ScriptApp.getService().getUrl();
  if (!/\/exec$/.test(base)) {
    console.log("⚠ 웹 앱 주소가 …/exec 로 끝나지 않아요(지금: " + base + "). 배포 → 배포 관리에서 '웹 앱 URL'을 복사해 스크립트 속성 WEBAPP_URL에 넣고 다시 실행하세요.");
    return;
  }
  var url = base + "?key=" + props.getProperty("SECRET");
  var res = UrlFetchApp.fetch("https://api.telegram.org/bot" + props.getProperty("BOT_TOKEN") + "/setWebhook", {
    method: "post", muteHttpExceptions: true,
    payload: { url: url, allowed_updates: JSON.stringify(["message"]), drop_pending_updates: "false" }
  });
  console.log(res.getContentText());
  console.log("뉴스 발송이 설정을 읽어 갈 주소(GitHub Secret PREFS_URL에 넣을 값): " + url);
}

// 2) 연결 상태 확인: 'last_error_message'가 비어 있으면 정상
function checkWebhook() {
  var token = PropertiesService.getScriptProperties().getProperty("BOT_TOKEN");
  console.log(UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/getWebhookInfo").getContentText());
}

// 3) 웹훅 끄기 (GitHub 방식으로 되돌릴 때)
function deleteWebhook() {
  var token = PropertiesService.getScriptProperties().getProperty("BOT_TOKEN");
  console.log(UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/deleteWebhook").getContentText());
}

// 4) 명령어 안내 보내기: 나에게만(시험) / 아직 못 받은 사람 전부
function sendGuideToAdmin() {
  send_(PropertiesService.getScriptProperties().getProperty("ADMIN_CHAT_ID"), "📌 알람 설정을 직접 바꿀 수 있어요.\n\n" + HELP_TEXT);
}

function sendGuideToAll() {
  var prefs = loadPrefs_();
  loadSubscribers_().forEach(function (s) {
    var mine = prefs[s.chat_id] || (prefs[s.chat_id] = {});
    if (mine.pinned) return;
    sendGuide_(s.chat_id, mine);
  });
  savePrefs_(prefs);
}

// 5) 입력창 옆 '/' 메뉴 등록 (동기화 때 자동으로도 하므로, 바로 보고 싶을 때만 실행)
function setCommandMenu() {
  console.log(setCommandMenu_() ? "메뉴를 등록했어요. 텔레그램에서 봇 채팅 입력창 옆 / 버튼(또는 메뉴)을 확인하세요." : "메뉴 등록에 실패했어요(BOT_TOKEN 확인).");
}

// 6) 백업에서 복구: 백업 파일(.json)의 내용을 통째로 복사해 스크립트 속성 RESTORE에 붙여넣은 뒤 이 함수를 실행
function restoreBackup() {
  var props = PropertiesService.getScriptProperties();
  var raw = props.getProperty("RESTORE");
  if (!raw) { console.log("스크립트 속성 RESTORE에 백업 파일 내용을 붙여넣은 뒤 다시 실행하세요."); return; }
  var data;
  try { data = JSON.parse(raw); } catch (e) { console.log("RESTORE 내용이 올바른 JSON이 아니에요: " + e); return; }
  if (!data || typeof data.prefs !== "object" || !Array.isArray(data.approved)) { console.log("백업 파일 형식이 아니에요."); return; }
  props.setProperty("PREFS", JSON.stringify(data.prefs));
  props.setProperty("APPROVED", JSON.stringify(data.approved));
  props.setProperty("PENDING", JSON.stringify(data.pending || {}));
  props.deleteProperty("RESTORE");
  console.log("복구했어요: 개인 설정 " + Object.keys(data.prefs).length + "명, 승인 명단 " + data.approved.length + "명. (받는 사람 목록·섹터는 GitHub에서 자동으로 다시 와요)");
}