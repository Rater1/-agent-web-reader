import fs from 'node:fs/promises';
import {spawn} from 'node:child_process';
import crypto from 'node:crypto';
const base='/mnt/fixtures',job=JSON.parse(await fs.readFile(base+'/job.json','utf8'));
const need=(x,s)=>{if(!x)throw new Error(s);},delay=ms=>new Promise(r=>setTimeout(r,ms));
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const started=Date.now(),report={run_id:job.run_id,url:job.url,status:'RUNNING',screenshots:[],blocked_requests:[],blocked_count:0,fulfilled:0,script_errors:0};
need(process.getuid()!==0,'ROOT_BROWSER');report.network_interfaces=await fs.readdir('/sys/class/net');need(report.network_interfaces.join(',')==='lo','NETWORK');
for(const r of Object.values(job.maps))need(hash(await fs.readFile(base+'/'+r.body))===r.sha256,'INPUT_HASH');
let browser,ws,exit=null,sequence=0,closing=false,requestError,stderr='';const pending=new Map();
function send(method,params={},sessionId){const id=++sequence;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP_TIMEOUT'));},12000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));});}
async function ev(s,expression){need(!requestError,'REQUEST_HANDLER');const r=await send('Runtime.evaluate',{expression,returnByValue:true},s);need(!r.exceptionDetails,'EVALUATION');return r.result.value;}
async function shot(s,name,y,height){
 const r=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,fromSurface:true,clip:{x:0,y,width:1100,height,scale:1}},s);
 const b=Buffer.from(r.data,'base64');
 if(b.length>600000)return null;
 return {name,bytes:b.length,sha256:hash(b),width:1100,height,y,data:b.toString('base64')};
}
async function captureWithinBudget(s,height){
 let covered=Math.max(750,Math.min(6000,height));
 while(true){
   const full=await shot(s,'page_full.png',0,covered);
   if(full){
     const top=await shot(s,'page_top.png',0,750);
     const bottom=await shot(s,'page_bottom.png',covered-750,750);
     need(top&&bottom,'PNG_SIZE');
     if(full.bytes+top.bytes+bottom.bytes<=1200000){
       report.screenshots=[full,top,bottom];
       return covered;
     }
   }
   need(covered>750,'TOTAL_PNG_SIZE');
   covered=Math.max(750,Math.floor(covered/2));
 }
}
const watchdog=setTimeout(()=>{report.watchdog=true;browser?.kill('SIGKILL');},65000);
try{
 await fs.mkdir('/tmp/capture');
 browser=spawn('/usr/bin/chromium-headless-shell',['--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--user-data-dir=/tmp/capture/profile','--no-first-run','--disable-extensions','--disable-background-networking','--disable-sync','--disable-component-update','--proxy-server=http://127.0.0.1:9','--disable-dev-shm-usage','--disable-gpu','about:blank'],{env:{PATH:'/usr/bin:/bin',HOME:'/home/browser',LANG:'C.UTF-8',TMPDIR:'/tmp'},stdio:['ignore','ignore','pipe']});
 browser.on('exit',(code,signal)=>exit={code,signal});browser.on('error',e=>report.spawn_error=e.code);browser.stderr.on('data',b=>{if(stderr.length<6000)stderr+=b;});
 let portData;for(let i=0;i<150;i++){try{portData=await fs.readFile('/tmp/capture/profile/DevToolsActivePort','utf8');break;}catch{}if(exit||report.spawn_error)break;await delay(100);}need(portData,'DEVTOOLS_NOT_READY');const [port,route]=portData.trim().split(/\r?\n/);need(/^\d+$/.test(port)&&route.startsWith('/devtools/browser/'),'DEVTOOLS_SCOPE');ws=new WebSocket('ws://127.0.0.1:'+port+route);
 await new Promise((resolve,reject)=>{const fail=()=>reject(new Error('WEBSOCKET_OPEN'));ws.addEventListener('error',fail,{once:true});ws.addEventListener('open',()=>{ws.removeEventListener('error',fail);resolve();},{once:true});setTimeout(()=>reject(new Error('WEBSOCKET_TIMEOUT')),5000).unref();});
 ws.addEventListener('error',()=>{if(!closing)requestError='WEBSOCKET_ERROR';});
 ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.id&&pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(new Error('CDP_COMMAND')):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown'){report.script_errors++;}else if(m.method==='Fetch.requestPaused'){
   (async()=>{const {requestId,request}=m.params;const r=request.method==='GET'&&Object.hasOwn(job.maps,request.url)?job.maps[request.url]:null;if(!r){report.blocked_count++;if(report.blocked_requests.length<32){let u;try{const p=new URL(request.url);u=p.protocol==='https:'?request.url.slice(0,512):p.protocol;}catch{u='invalid-url';}report.blocked_requests.push({url:u,type:m.params.resourceType,method:request.method});}await send('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'},m.sessionId);return;}
    const b=await fs.readFile(base+'/'+r.body);need(hash(b)===r.sha256,'FULFILL_HASH');report.fulfilled++;await send('Fetch.fulfillRequest',{requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:r.content_type},{name:'Cache-Control',value:'no-store'}],body:b.toString('base64')},m.sessionId);
   })().catch(()=>{requestError='FETCH_HANDLER';});
 }});
 report.browser_version=await send('Browser.getVersion');await send('Browser.setDownloadBehavior',{behavior:'deny'});
 const {targetId}=await send('Target.createTarget',{url:'about:blank'});const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
 for(const name of ['Page.enable','Runtime.enable','Network.enable'])await send(name,{},s);
 await send('Network.setCacheDisabled',{cacheDisabled:true},s);await send('Network.setBypassServiceWorker',{bypass:true},s);await send('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]},s);await send('Emulation.setDeviceMetricsOverride',{width:1100,height:750,deviceScaleFactor:1,mobile:false},s);
 const nav=await send('Page.navigate',{url:job.url},s);need(!nav.errorText,'NAVIGATION');
 let ready=false;for(let i=0;i<100;i++){if(await ev(s,"document.readyState==='complete'")){ready=true;break;}await delay(100);}need(ready,'LOAD_TIMEOUT');await delay(700);
 report.initial_text=await ev(s,'document.body.innerText.slice(0,4000)');
 for(let i=0;i<3;i++){await ev(s,'window.scrollTo(0,Math.min(document.documentElement.scrollHeight,6000))');await delay(450);}
 const page=await ev(s,"({title:document.title,url:location.href,text:document.body.innerText.slice(0,4000),text_length:document.body.innerText.length,height:document.documentElement.scrollHeight})");need(page.url===job.url,'PAGE_URL_CHANGED');
 report.title=page.title;report.text=page.text;report.text_truncated=page.text_length>4000;report.page_height=page.height;
 const covered=await captureWithinBudget(s,page.height);
 report.vertical_truncated=page.height>covered;
 need(!requestError,'REQUEST_HANDLER');report.status='CAPTURED';
}catch(e){report.status='STOPPED';report.error=e.message;process.exitCode=1;}
finally{
 closing=true;if(ws?.readyState===WebSocket.OPEN)try{ws.send(JSON.stringify({id:++sequence,method:'Browser.close'}));}catch{}
 for(let i=0;i<50&&!exit;i++)await delay(100);if(browser&&!exit){browser.kill('SIGKILL');report.forced=true;for(let i=0;i<20&&!exit;i++)await delay(100);}ws?.close();for(const p of pending.values())clearTimeout(p.timer);pending.clear();clearTimeout(watchdog);
 report.browser_exit=exit;report.duration_ms=Date.now()-started;report.stderr=stderr;if(!exit||exit.code!==0){report.status='STOPPED';process.exitCode=1;}
 const result=JSON.stringify(report);need(Buffer.byteLength(result)<1900000,'FRAME_SIZE');console.log('SCREENSHOT_RESULT:'+result);
}
