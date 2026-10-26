const REPO = require('path').resolve(__dirname, '..');  // 저장소 폴더
const fs=require('fs');let src=fs.readFileSync(REPO + '/apps_script/Code.gs','utf8');
const props={SUBSCRIBERS:"[]",PREFS:JSON.stringify({"555":{hours:[9,18]}}),ADMIN_CHAT_ID:"999",SECRET:"x",APPROVED:"[]",
 PENDING:JSON.stringify({"555":{name:"신청",chat_id:"555",sectors:["자동차"],modes:["통신"]}})};
global.PropertiesService={getScriptProperties:()=>({getProperty:k=>props[k]??null,setProperty:(k,v)=>{props[k]=v},deleteProperty:k=>{delete props[k]}})};
global.UrlFetchApp={fetch:()=>({getContentText:()=>'{"ok":true,"result":{}}',getResponseCode:()=>200})};
src+="\nglobalThis.X={handleCallback_,setSend:f=>{send_=f;sendRaw_=f;tg_=f;guideNew_=()=>{}}};";
new Function(src)(); X.setSend(()=>({ok:true}));
const t0=Date.now();
X.handleCallback_({id:"c",from:{id:999},data:"ok:555",message:{chat:{id:999},message_id:1}});
const p=JSON.parse(props.PREFS)["555"]; console.log(p);
const ok=p.resumed_at && Math.abs((t0-3600000)-p.resumed_at)<5000 && p.hours[0]===9 && JSON.parse(props.APPROVED).length===1;
console.log(ok?"  ✅ 승인 시 1시간 전부터 + 기존 설정 유지":"  ❌ 실패"); process.exit(ok?0:1);
