import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import vm from 'node:vm';

// Read-only browser regression against the existing local audit server. The API needs the server's key, read from its
// key file (AUDIT_AGENT_HOME or the default base). The browser profile stores the key, so it lives in the host temporary
// folder, which no run can read, and is removed afterwards; results stay under tmp/ and never hold the key.
// Chrome speaks its debugging protocol over a pipe (--remote-debugging-pipe), never over a TCP port: a debugging port
// listens on loopback without credentials, where a confined run could attach and read the key or drive the browser.
//
//   node tools/test-audit-workspace.mjs [<example id> [<report path>]]
//   node tools/test-audit-workspace.mjs --smoke
//
// --smoke needs no pilot server: it starts Chrome the same way with a throwaway profile, evaluates one expression on
// about:blank, checks that no TCP debugging endpoint exists, removes the profile and exits 0.
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const PROFILE_PREFIX='workspace-browser-profile-';

/** A fresh profile folder in the host temporary folder, and its guarded removal (only a folder of that name there). */
const makeProfileRoot=async()=>fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(),PROFILE_PREFIX)));
async function removeProfileRoot(profileRoot){
 if(profileRoot&&path.dirname(profileRoot)===await fs.realpath(os.tmpdir())&&path.basename(profileRoot).startsWith(PROFILE_PREFIX))await fs.rm(profileRoot,{recursive:true,force:true,maxRetries:3});
}
async function stopChrome(chrome){
 if(chrome.pid===undefined||chrome.exitCode!==null||chrome.signalCode!==null)return;
 chrome.kill('SIGTERM');
 await new Promise(resolve=>{if(chrome.exitCode!==null||chrome.signalCode!==null)return resolve();chrome.once('exit',resolve);setTimeout(resolve,5000);});
 if(chrome.exitCode===null&&chrome.signalCode===null)chrome.kill('SIGKILL');
}

/** Starts Chrome with the debugging protocol on descriptors 3 (commands to Chrome) and 4 (answers and events from it),
 * each message JSON followed by a NUL byte. Over the pipe the connection is attached to the browser target, so the page
 * target is attached with flatten:true and its sessionId goes with every page command. Fails, after stopping Chrome,
 * when the profile holds a DevToolsActivePort file, which Chrome writes only for a TCP debugging endpoint. */
async function startChrome(profileRoot){
 const profile=path.join(profileRoot,'profile');
 const chrome=spawn('google-chrome',['--headless','--no-sandbox','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-dev-shm-usage','--remote-debugging-pipe','--user-data-dir='+profile,'--disk-cache-dir='+path.join(profileRoot,'cache'),'about:blank'],{cwd:root,stdio:['ignore','ignore','pipe','pipe','pipe']});
 let log='',buffer='',seq=0,ended=null;const pending=new Map(),listeners=new Set();
 const end=error=>{ended??=error;for(const p of pending.values()){clearTimeout(p.timer);p.reject(ended);}pending.clear();};
 chrome.stderr.on('data',c=>{log+=c;});
 chrome.once('error',error=>end(Error('Chrome could not start: '+error.message)));
 chrome.once('exit',(code,signal)=>end(Error('Chrome exited ('+(signal||code)+'): '+log.slice(-4000))));
 const commands=chrome.stdio[3],answers=chrome.stdio[4];
 commands.on('error',error=>end(Error('The debugging pipe to Chrome failed: '+error.message)));answers.on('error',()=>{});
 answers.setEncoding('utf8');
 answers.on('data',chunk=>{
  buffer+=chunk;let at;
  while((at=buffer.indexOf('\0'))>=0){
   const text=buffer.slice(0,at);buffer=buffer.slice(at+1);let m;try{m=JSON.parse(text);}catch{end(Error('Chrome sent an unreadable protocol message'));return;}
   if(m.id){const p=pending.get(m.id);if(!p)continue;pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}
   else for(const listener of listeners)listener(m);
  }
 });
 function send(method,params={},sessionId){
  if(ended)return Promise.reject(ended);
  return new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout: '+method));},20000);pending.set(id,{resolve,reject,timer});commands.write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0');});
 }
 const noPort=()=>assert.equal(existsSync(path.join(profile,'DevToolsActivePort')),false,'Chrome must not open a TCP debugging endpoint: the profile holds DevToolsActivePort');
 try{
  let target;
  for(let i=0;i<100&&!target;i++){target=(await send('Target.getTargets')).targetInfos.find(t=>t.type==='page');if(!target)await sleep(100);}
  assert(target,'Chrome opened no page: '+log);
  const {sessionId}=await send('Target.attachToTarget',{targetId:target.targetId,flatten:true});
  noPort();
  return {chrome,profile,noPort,log:()=>log,
   page:(method,params={})=>send(method,params,sessionId),
   on(listener){listeners.add(m=>{if(m.sessionId===sessionId)listener(m);});},
   async close(){if(!ended)await Promise.race([send('Browser.close').catch(()=>{}),sleep(3000)]);await stopChrome(chrome);}};
 }catch(error){await stopChrome(chrome);throw error;}
}

if(process.argv[2]==='--smoke'){
 if(process.argv.length>3){console.error('Usage: node tools/test-audit-workspace.mjs --smoke');process.exit(2);}
 let profileRoot=null,browser=null;
 try{
  profileRoot=await makeProfileRoot();
  browser=await startChrome(profileRoot);
  await browser.page('Page.enable');await browser.page('Page.navigate',{url:'about:blank'});
  const evaluated=await browser.page('Runtime.evaluate',{expression:'[location.href,6*7,typeof document.createElement]',returnByValue:true});
  assert.deepEqual(evaluated.result.value,['about:blank',42,'function'],'the page evaluated the expression');
  browser.noPort();
  console.log('PASS browser smoke: Chrome '+(await browser.page('Runtime.evaluate',{expression:'navigator.userAgent',returnByValue:true})).result.value.replace(/^.*(HeadlessChrome\/\S+).*$/,'$1')+' answered over --remote-debugging-pipe; no DevToolsActivePort in the profile.');
 }finally{await browser?.close();await removeProfileRoot(profileRoot);}
 assert.equal(existsSync(profileRoot),false,'the throwaway profile was removed');
 console.log('The throwaway profile '+profileRoot+' was removed.');
 process.exit(0);
}

// The pilot modules are loaded only here: --smoke reads no key file and no agent-home base.
const {readApiKey}=await import('./api-auth.mjs');
const {agentHomeLocation}=await import('./agent-home.mjs');
const origin='http://127.0.0.1:8787';
const dir=await fs.mkdtemp(path.join(root,'tmp/workspace-browser-'));
const key=await readApiKey({base:agentHomeLocation().base,port:8787});
const authorized={headers:{Authorization:'Bearer '+key}};
const sourceHTML=await fs.readFile(path.join(root,'tools/audit-ui.html'),'utf8');
const renderer=vm.createContext({});
vm.runInContext(sourceHTML.split('<script>')[1].split('async function catalog()')[0],renderer);
const rendered=vm.runInContext("markdown('# Heading\\n\\n- One\\n- Two\\n\\n> Quote\\n\\n###### Small\\n\\n| Column | Other |\\n| --- | --- |\\n| Value | Entry |\\n\\n[Evidence](../input/source.md)\\n\\n```lean\\ntheorem demo : True := by trivial\\n```')",renderer);
for(const tag of ['<h1','<ul>','<blockquote>','<h6','<thead>','<th>','<code>','data-md-link='])assert(rendered.includes(tag),'Markdown element missing: '+tag);
assert(!vm.runInContext("inline('[Bad](javascript:alert)')",renderer).includes('<a'),'Executable links are not rendered');
const anchored=vm.runInContext("markdown('## S01 — Chapter 1\\n\\nSource text\\n\\n## R01 — Conclusion')",renderer);
assert(anchored.includes('id="md-s01"')&&anchored.includes('id="md-r01"'),'Short source and report citations have stable section anchors');
assert.equal(vm.runInContext("resolveDocumentLink('work/a/traces/step.md','../input/source.md').path",renderer),'work/a/input/source.md');
assert.throws(()=>vm.runInContext("resolveDocumentLink('one.md','../../outside.md')",renderer));
const catalogResponse=await fetch(origin+'/api/examples',authorized);
const catalog=await catalogResponse.json();
assert(catalogResponse.ok&&Array.isArray(catalog),'Catalog unavailable: '+JSON.stringify(catalog));
const requested=process.argv[2];
const example=requested?catalog.find(x=>x.id===requested):catalog.find(x=>(x.language||'en')==='en'&&x.runs.some(r=>r.status==='finished'))||catalog.find(x=>x.runs.some(r=>r.status==='finished'));
assert(example,'A real completed example is required (optional argument: example ID)');
const requestedReport=process.argv[3];
const report=requestedReport?example.reports.find(r=>r.path===requestedReport):example.reports.find(r=>example.runs.some(x=>x.reportPath===r.path&&x.status==='finished'))||example.reports[0];
assert(report,'The selected example must have an input report');
const source=example.files.find(f=>f.role==='source');
const runs=example.runs.filter(r=>r.reportPath===report.path).sort((a,b)=>b.created.localeCompare(a.created));
const finished=runs.find(r=>r.status==='finished');
assert(source&&finished,'A source and a real finished verification are required');
const runData=await (await fetch(origin+'/api/runs/'+finished.id,authorized)).json();
assert(runData.findings?.length,'The real run must contain findings');
const claim=runData.findings.find(f=>f.proofs?.length)||runData.findings[0];
const errors=[],mutations=[];let profileRoot=null,browser=null;
try{
 // The profile is created inside the try, so an early failure leaves nothing behind.
 profileRoot=await makeProfileRoot();
 browser=await startChrome(profileRoot);
 const cdp=browser.page;
 browser.on(m=>{if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);else if(m.method==='Network.requestWillBeSent'&&m.params.request.url.startsWith(origin)&&!['GET','HEAD'].includes(m.params.request.method)&&!(m.params.request.method==='POST'&&new URL(m.params.request.url).pathname==='/api/session'))mutations.push({method:m.params.request.method,url:m.params.request.url});});
 async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
 async function until(expression){for(let i=0;i<150;i++){if(await js(expression))return;await sleep(100);}throw Error('Timed out: '+expression);}
 async function clickNode(expression){
  const point=await js(`(()=>{const e=${expression};e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return {x:r.x+Math.min(30,r.width/2),y:r.y+r.height/2};})()`);
  await cdp('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});
  await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
 }
 const quote=JSON.stringify;
 const node=`document.querySelector('#tree details[data-example-id="'+${quote(example.id)}+'"]')`;
 const reportButton=`${node}.querySelector('.report-file[data-report-path="'+${quote(report.path)}+'"]')`;
 const sourceButton=`${node}.querySelector('[data-input-files] .input-file[data-path="'+${quote(source.path)}+'"]')`;
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Network.enable');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 // The sign-in link goes through the debugging channel, never into the browser's arguments.
 await cdp('Page.navigate',{url:origin+'/#key='+key});await until(`${node}!==null`);
 assert.equal(await js('location.hash'),'','the page removed the key from the address');
 assert.equal(await js("document.querySelectorAll('dialog,[role=dialog],#refresh,#run-history,#expand-verification').length"),0,'No popups, history, refresh or fullscreen navigation');
 await js("document.getElementById('new-example').click()");
 assert.deepEqual(await js("[...document.querySelectorAll('#example-language option')].map(o=>o.value).sort()"),['en','ro']);
 await clickNode(`${node}.querySelector('summary')`);await until(`${sourceButton}!==null`);
 await until(`${node}.open`);
 await clickNode(`${node}.querySelector('summary')`);await until(`!${node}.open`);
 await sleep(3300);assert(await js(`!${node}.open`),'Root remains collapsed after polling');
 await clickNode(`${node}.querySelector('summary')`);await until(`${node}.open`);
 assert(await js("!!document.getElementById('add-files') && !!document.getElementById('stop-example') && !!document.getElementById('generate-report')"),'Example-level actions exist');
 await js("document.getElementById('add-files').click()");assert(await js("!!document.querySelector('#upload-form select[name=role]')"),'Inline upload role selector');
 await js("document.getElementById('cancel-form').click();document.getElementById('generate-report').click()");
 assert.deepEqual(await js("[...document.querySelectorAll('#generate-form select[name=mode] option')].map(o=>o.value)"),['grounded','with_errors']);
 await js("document.getElementById('cancel-form').click()");
 const inputs=`${node}.querySelector('[data-input-files]')`;
 await clickNode(`${inputs}.querySelector('summary')`);
 await until(`${inputs}.open && ${sourceButton}.getBoundingClientRect().height>0`);
 await sleep(3300);
 assert(await js(`${inputs}.open`),'Input files stays expanded after polling');
 await clickNode(`${inputs}.querySelector('summary')`);
 await until(`!${inputs}.open`);
 await sleep(3300);
 assert(await js(`!${inputs}.open`),'Input files stays collapsed after polling');
 await js(`${inputs}.querySelector('summary').focus()`);
 await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});
 await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
 await until(`${inputs}.open && ${sourceButton}.getBoundingClientRect().height>0`);
 await clickNode(sourceButton);await until(`document.getElementById('verification-document').dataset.artifactPath===${quote(source.path)}`);
 await clickNode(reportButton);await until(`document.getElementById('content').dataset.runId===${quote(finished.id)}`);
 assert.equal(await js("document.querySelectorAll('#add-files,#stop-example,#generate-report').length"),0,'Example-only actions do not leak into report');
 assert(await js("!!document.getElementById('start')"),'Report verification action');
 const reportNode=`${node}.querySelector('details[data-report-path="'+${quote(report.path)}+'"]')`;
 await clickNode(reportButton);
 await until(`!${reportNode}.open`);
 await sleep(3300);
 assert(await js(`!${reportNode}.open`),'Selected report stays collapsed after polling');
 await clickNode(reportButton);
 await until(`${reportNode}.open`);

 const finding=`${node}.querySelector('details[data-report-path="'+${quote(report.path)}+'"] .finding-file[data-claim-id="'+${quote(claim.claim_id)}+'"]')`;
 await until(`${finding}!==null`);await clickNode(finding);
 await until(`document.getElementById('content')?.dataset.claimId===${quote(claim.claim_id)}`);
 await until(`document.getElementById('verification-document')?.dataset.artifactPath===${quote(claim.path)}`);
 assert.equal(await js("document.querySelectorAll('#content details,#content [role=tab],#content nav,#verification-documents,dialog').length"),0,'All navigation is in the tree; right pane is a plain document');
 const statusCases={supported:'Supported',refuted:'Refuted',undetermined:'Undetermined',both:'Conflicting',pending:'Pending',not_applicable:'Not applicable'};
 for(const [state,label] of Object.entries(statusCases)){const result=await js(`findingStatus(${quote(state)})`);assert.equal(result.label,label);assert(result.icon&&result.description,'Every state has an icon and explanation');}
 assert.equal((await js('findingStatus(undefined)')).state,'pending','Missing verdict cannot appear supported');
 for(const phase of ['queued','running','finalizing','failed','interrupted','incomplete','stopped']){const draft=await js(`findingStatus('supported',${quote(phase)})`);assert.equal(draft.state,'pending','Nonfinished drafts cannot appear supported');assert(draft.description.includes('provisional'));}
 assert.equal(await js(`${reportButton}.querySelector('.report-status').dataset.status`),finished.status,'Report exposes execution status');
 assert(await js("[...document.querySelectorAll('#tree details[data-report-path]')].every(d=>d.querySelector(':scope > summary .report-status')?.dataset.status==='finished'||[...d.querySelectorAll('.finding-file')].every(f=>f.dataset.semanticState==='pending'))"),'Every unfinished report shows provisional finding badges');
 for(const f of runData.findings){const state=Object.hasOwn(statusCases,f.semantic_state)?f.semantic_state:'pending';const summary=`${reportNode}.querySelector('.finding-file[data-claim-id="'+${quote(f.claim_id)}+'"]')`;assert.equal(await js(`${summary}.dataset.semanticState`),state);assert.equal(await js(`${summary}.querySelector('.status-label').textContent`),statusCases[state]);assert(await js(`${summary}.querySelector('.finding-status').title.length>0 && ${summary}.querySelector('.status-icon').getAttribute('aria-hidden')==='true'`),'Visible text conveys the decorative icon status accessibly');}
 await clickNode(finding);await until(`!${finding}.parentElement.open`);await sleep(3300);
 assert(await js(`!${finding}.parentElement.open`),'Finding remains collapsed after polling');
 await clickNode(finding);await until(`${finding}.parentElement.open`);
 await until(`document.getElementById('verification-document')?.dataset.artifactPath===${quote(claim.path)}`);

 const narrative=await js("document.getElementById('verification-document').innerText");
 assert(!/&(?:amp|lt|gt|quot|#\d+);/.test(narrative),'No escaped entities');
 assert(!/"(?:claim_id|executive_summary|semantic_state)"\s*:/.test(narrative),'No internal JSON');
 if(example.language==='ro')assert(/[ăâîșțĂÂÎȘȚ]/.test(narrative),'Romanian diacritics');
 const steps=`${finding}.parentElement.querySelector('details')`;
 if(await js(`!!${steps}`)){await clickNode(`${steps}.querySelector('summary')`);await until(`${steps}.open`);}
 await js(`${finding}.scrollIntoView({block:'nearest'})`);
 await fs.writeFile(path.join(dir,'finding-narrative.png'),Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
 const docs=await js("[...document.querySelectorAll('#tree .document-link')].map(a=>a.dataset.path)");
 for(const e of runData.phases.filter(e=>e.claim_ids?.length===1&&e.claim_ids[0]===claim.claim_id&&e.claim_step))assert(docs.includes(e.path),'Individual step linked');
 const tracePath=runData.phases.find(e=>e.claim_ids?.length===1&&e.claim_ids[0]===claim.claim_id&&e.claim_step==='evidence').path;
 await js(`[...document.querySelectorAll('.document-link')].find(a=>a.dataset.path===${quote(tracePath)}).click()`);
 await until(`document.getElementById('verification-document').dataset.artifactPath===${quote(tracePath)}`);
 assert(await js("!!document.querySelector('#verification-document thead th')"),'Readable source table');
 const reference=await js("(()=>{const a=document.querySelector('#verification-document [data-md-link]');a.dataset.testLink='yes';return a.dataset.mdLink;})()");
 const linkedPath=path.posix.normalize(path.posix.join(path.posix.dirname(tracePath),decodeURIComponent(reference.split('#')[0])));
 await js("document.querySelector('[data-test-link=yes]').click()");await until(`document.getElementById('verification-document').dataset.artifactPath===${quote(linkedPath)}`);
 assert.equal(await js("document.getElementById('verification-document').dataset.sourceId"),finished.id);
 const fragment=decodeURIComponent(reference.split('#')[1]||'');
 if(fragment)assert(await js(`[...document.querySelectorAll('#verification-document [id]')].some(e=>e.id===${quote(fragment)}||e.id===headingId(${quote(fragment)}))`),'Evidence citation resolves to its exact section');

 await js("[...document.querySelectorAll('.document-link')].find(a=>a.dataset.path.endsWith('.lean')).click()");
 await until("document.getElementById('verification-document').dataset.artifactPath?.endsWith('.lean')");
 assert(await js("/theorem|namespace/.test(document.getElementById('verification-document').textContent)"),'Actual Lean code shown');
 const selectedArtifact=await js("document.getElementById('verification-document').dataset.artifactPath");
 const openedNodes=await js("[...document.querySelectorAll('#tree details[open]')].map(d=>d.dataset.key)");
 await sleep(3300);
 assert.equal(await js("document.getElementById('verification-document').dataset.artifactPath"),selectedArtifact,'Polling keeps selected document');
 const stillOpen=await js("[...document.querySelectorAll('#tree details[open]')].map(d=>d.dataset.key)");
 for(const key of openedNodes)assert(stillOpen.includes(key),'Polling preserves expanded tree nodes');
 assert(await js(`[...document.querySelectorAll('#tree .document-link.selected')].some(b=>b.dataset.path===${quote(selectedArtifact)})`),'Selected document stays highlighted after polling');
 const bounds=[];
 for(const [width,height] of [[1440,1000],[390,844]]){
  await cdp('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});await sleep(150);
  assert(await js("document.documentElement.scrollWidth<=innerWidth+2"),'Page has no horizontal overflow');
  bounds.push({viewportWidth:width,viewportHeight:height,width,height});
  await js("document.querySelector('#tree .document-link.selected')?.scrollIntoView({block:'nearest'})");
  await fs.writeFile(path.join(dir,`verification-${width}x${height}.png`),Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
 }
 // Exercise real pointer toggles at every tree depth, including a redraw.
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 async function checkToggle(summary,details,label){
  if(!await js(`${details}.open`))await clickNode(summary);
  await until(`${details}.open`);
  await clickNode(summary);
  await until(`!${details}.open`);
  await js('drawTree()');
  assert(await js(`!${details}.open`),label+' remains collapsed after tree redraw');
  await clickNode(summary);
  await until(`${details}.open`);
 }
 const findingNode=`${finding}.parentElement`;
 const stepsNode=`${findingNode}.querySelector('details')`;
 await checkToggle(`${stepsNode}.querySelector('summary')`,stepsNode,'Verification steps');
 await checkToggle(finding,findingNode,'Finding');
 await checkToggle(reportButton,reportNode,'Report');
 await checkToggle(`${node}.querySelector('summary')`,node,'Example');
 assert.deepEqual(errors,[],'No browser exceptions');assert.deepEqual(mutations,[],'This test must remain read-only');
 assert.equal(await js("document.getElementById('error')?.textContent.trim()||''"),'','No user-visible errors');
 browser.noPort();
 await fs.writeFile(path.join(dir,'result.md'),'# Browser verification\n\nPassed: read-only real-server navigation, source preview, finding summary, Lean preview, Markdown links and responsive document bounds.\n\n- Example: '+example.id+'\n- Report: '+report.path+'\n- Run: '+finished.id+'\n- Claim: '+claim.claim_id+'\n\n## Viewports\n\n'+bounds.map(b=>'- '+b.viewportWidth+' × '+b.viewportHeight+': viewport '+b.width+' × '+b.height).join('\n')+'\n');
 console.log('PASS read-only audit workspace. Artifacts: '+dir);
}catch(e){await fs.writeFile(path.join(dir,'failure.md'),'# Browser verification failed\n\n```text\n'+String(e.stack).split(key).join('<key>')+'\n```\n\nBrowser exceptions: '+errors.length+'\n\nUnexpected writes: '+mutations.length+'\n');throw e;}
finally{await browser?.close();await fs.writeFile(path.join(dir,'chrome.md'),'# Browser diagnostics\n\n```text\n'+(browser?browser.log():'Chrome did not start; see failure.md.').split(key).join('<key>')+'\n```\n');await removeProfileRoot(profileRoot);}
