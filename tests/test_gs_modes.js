const REPO = require('path').resolve(__dirname, '..');  // 저장소 폴더
const fs=require('fs');let src=fs.readFileSync(REPO + '/apps_script/Code.gs','utf8');
const props={SUBSCRIBERS:JSON.stringify([{name:"홍길동",chat_id:"111",sectors:["자동차"],modes:["통신"]},{name:"무지정",chat_id:"222"}]),
 PREFS:"{}", ADMIN_CHAT_ID:"999", SECRET:"x", APPROVED:"[]", PENDING:"{}",
 CONFIG: JSON.stringify({sectors:["자동차","배터리","중후장대","정유·석화","에너지","반도체","IT·통신","전자","항공","재계"],modes:["통신","단독","일반","공시"],teams:{},defaults:["자동차"],default_modes:["통신","단독","공시"]})};
global.PropertiesService={getScriptProperties:()=>({getProperty:k=>props[k]??null,setProperty:(k,v)=>{props[k]=v}})};
global.UrlFetchApp={fetch:()=>({getContentText:()=>"{}"})}; global.LockService={getScriptLock:()=>({waitLock(){},releaseLock(){}})};
global.ContentService={createTextOutput:t=>({setMimeType(){return this}}),MimeType:{JSON:1}};
const out=[]; src+="\nglobalThis.X={runCommand_,loadSubscribers_,applyPrefs_,handleSignup_,cfg_,setSend:f=>{send_=f;sendRaw_=f}};";
new Function(src)(); X.setSend((c,t)=>out.push(c+": "+t));
let fails=[]; const check=(c,m)=>{console.log((c?"  ✅ ":"  ❌ ")+m); if(!c) fails.push(m)};
const subs=X.loadSubscribers_(); const prefs={};
const s1=X.applyPrefs_(subs[0],prefs), s2=X.applyPrefs_(subs[1],prefs);
check(s2.modes.join()==="통신,단독,공시", "종류 미지정 기본 = 통신·단독·공시 ("+s2.modes.join()+")");
let r=X.runCommand_("/종류 전체", s1, prefs, "111"); check(/종류: 통신, 단독, 일반, 공시/.test(r), "/종류 전체 -> 넷 다");
r=X.runCommand_("/종류 통신 일반", s1, prefs, "111"); check(/종류: 통신, 일반/.test(r), "/종류 통신 일반");
r=X.runCommand_("/종류 엉뚱", s1, prefs, "111"); check(/통신, 단독, 일반, 공시 \/ 전체/.test(r), "안내 문구");
out.length=0; X.handleSignup_({first_name:"신청"}, "555", "1000000000", "1010");
const pend=JSON.parse(props.PENDING); check(pend["555"] && pend["555"].modes.join()==="통신,일반", "앱 신청 4칸 비트 -> 통신·일반 ("+(pend["555"]||{}).modes+")");
console.log("실패", fails); process.exit(fails.length?1:0);
