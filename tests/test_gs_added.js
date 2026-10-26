const REPO = require('path').resolve(__dirname, '..');  // 저장소 폴더
const fs=require('fs');let src=fs.readFileSync(REPO + '/apps_script/Code.gs','utf8');
const props={SUBSCRIBERS:JSON.stringify([{name:"홍",chat_id:"111",sectors:["자동차"],modes:["통신"]}]),PREFS:"{}",ADMIN_CHAT_ID:"999",SECRET:"x",APPROVED:"[]",PENDING:"{}",
 CONFIG: JSON.stringify({sectors:["자동차","배터리","중후장대","정유·석화","에너지","반도체","IT·통신","전자","항공","재계"],modes:["통신","단독","일반","공시"],teams:{"차팀":["자동차","배터리","중후장대","정유·석화","에너지"]},defaults:["자동차"],default_modes:["통신","단독","공시"]})};
global.PropertiesService={getScriptProperties:()=>({getProperty:k=>props[k]??null,setProperty:(k,v)=>{props[k]=v}})};
global.UrlFetchApp={fetch:()=>({getContentText:()=>"{}"})};
src+="\nglobalThis.X={runCommand_,loadSubscribers_,applyPrefs_};";
new Function(src)();
let fails=[]; const check=(c,m)=>{console.log((c?"  ✅ ":"  ❌ ")+m); if(!c) fails.push(m)};
const prefs={}; let sub=X.applyPrefs_(X.loadSubscribers_()[0],prefs);
let r=X.runCommand_("/섹터 차팀", sub, prefs, "111");
const a=prefs["111"].added||{}; console.log(a);
check(!("s:자동차" in a) && ["s:배터리","s:중후장대","s:정유·석화","s:에너지"].every(k=>k in a), "기존 자동차 빼고 새 섹터 4개 기록");
check(/1시간 전 기사부터/.test(r), "안내 문구");
sub=X.applyPrefs_(X.loadSubscribers_()[0],prefs);
X.runCommand_("/종류 전체", sub, prefs, "111");
const b=prefs["111"].added; check(["m:단독","m:일반","m:공시"].every(k=>k in b) && !("m:통신" in b) && "s:배터리" in b, "종류 추가 기록 + 섹터 기록 유지");
sub=X.applyPrefs_(X.loadSubscribers_()[0],prefs);
X.runCommand_("/섹터 자동차", sub, prefs, "111");
const c=prefs["111"].added; check(!Object.keys(c).some(k=>k.startsWith("s:")) && "m:일반" in c, "섹터 뺀 기록은 지움, 종류 기록은 유지");
console.log("실패", fails); process.exit(fails.length?1:0);
