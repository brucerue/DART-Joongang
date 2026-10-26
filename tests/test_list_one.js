const REPO = require('path').resolve(__dirname, '..');  // 저장소 폴더
const fs=require('fs');let src=fs.readFileSync('apps_script/Code.gs','utf8');
const props={SUBSCRIBERS:JSON.stringify([{name:"홍길동",chat_id:"111",sectors:["자동차"],modes:["통신"]},{name:"김철수",chat_id:"222"}]),
 PREFS:JSON.stringify({"222":{paused:true,hours:[9,18],sectors:["반도체","IT·통신"]}}), ADMIN_CHAT_ID:"999", SECRET:"x", APPROVED:"[]", PENDING:"{}"};
global.PropertiesService={getScriptProperties:()=>({getProperty:k=>props[k]??null,setProperty:(k,v)=>{props[k]=v}})};
global.console=console; const out=[];
global.UrlFetchApp={fetch:(u,o)=>({getContentText:()=>"{}"})};
src+="\nglobalThis.H=handleAdminCommand_;globalThis.setSend=f=>{send_=f};";
new Function(src)();
// cfg_ 가짜가 필요할 수 있음
try{ setSend((c,t)=>out.push(t)); }catch(e){}
for (const q of ["/명단", "/명단 김철수", "/명단 홍", "/명단 222", "/명단 없는사람"]) { out.length=0; try{H(q,"999")}catch(e){out.push("ERR "+e.message)}; console.log("=== "+q+"\n"+out.join("\n")); }
