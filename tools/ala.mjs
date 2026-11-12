/** ALA (Advanced Language Agent) integration. Every audit and report draft reaches its coding agent (Codex, Claude Code, OpenCode, Pi, ...) through ALA. */
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {liesWithin} from './agent-home.mjs';

const ENTRY=path.join('bin','ala.mjs');
const fail=(message,status=400)=>Object.assign(Error(message),{status});

// ---------- Locating ALA ----------
// Accept an AdvancedLanguageAgent checkout or its bin/ala.mjs entrypoint.
function inspect(candidate){
 let entry=path.resolve(candidate);
 try{if(fs.statSync(entry).isDirectory())entry=path.join(entry,ENTRY);entry=fs.realpathSync(entry);}catch{return {valid:false,reason:'Not found.'};}
 const alaRoot=path.dirname(path.dirname(entry));
 let name,declared=0;try{const pkg=JSON.parse(fs.readFileSync(path.join(alaRoot,'package.json'),'utf8'));name=pkg.name;declared=Object.keys({...pkg.dependencies,...pkg.optionalDependencies}).length;}catch{}
 if(path.basename(entry)!=='ala.mjs'||name!=='advanced-language-agent'||!fs.existsSync(path.join(alaRoot,'src/coding-agents/discovery.mjs')))return {valid:false,reason:'This location is not an ALA installation.'};
 return {valid:true,entry,alaRoot,dependencies:declared===0||fs.existsSync(path.join(alaRoot,'node_modules'))};
}
function onPath(){
 for(const folder of (process.env.PATH||'').split(path.delimiter).filter(Boolean)){const file=path.join(folder,'ala');try{if(fs.statSync(file).isFile())return fs.realpathSync(file);}catch{}}
 return null;
}
// ALA_CLI is an operator override; automatic detection is the fallback: a checkout beside or inside the repository, then
// the copy `npm install` puts under node_modules (the commit pinned in package.json), then PATH. The API cannot set it.
export function alaCandidates(root){
 if(process.env.ALA_CLI)return [{source:'environment',location:process.env.ALA_CLI}];
 const candidates=[{source:'parent',location:path.resolve(root,'..','AdvancedLanguageAgent')},{source:'application',location:path.resolve(root,'AdvancedLanguageAgent')},{source:'dependency',location:path.resolve(root,'node_modules','advanced-language-agent')}];
 const installed=onPath();if(installed)candidates.push({source:'path',location:installed});
 return candidates;
}
// The server imports ALA's modules unconfined, so a location in a folder that runs can write is refused. Folders are
// compared by device and inode of every ancestor of the real entry, so neither a link nor letter case hides one.
export const locationRefused=folder=>`This ALA location is not accepted: it lies inside ${folder}, where runs can write. Use a checkout outside it.`;
const refusedRoot=(entry,refused)=>refused.find(folder=>folder&&liesWithin(entry,folder))||null;
// <root>/tmp, where every run and draft folder lies, is always refused; a caller adds folders (the server adds the folder
// set with npm run set-home) but can never remove this default.
const refusedRoots=(root,refused)=>[path.join(root,'tmp'),...(Array.isArray(refused)?refused:[])];
// ALA_CLI is authoritative: an invalid one is reported, never silently replaced. `refused` adds folders runs can write
// to the default <root>/tmp (the rule covers every source but ALA_CLI, which is the operator's own statement); a
// refused candidate's entry in `checked` names the folder as `refusedInside`.
export function resolveALA(root,{refused=[]}={}){
 const checked=[],roots=refusedRoots(root,refused);
 for(const candidate of alaCandidates(root)){
  const result=inspect(candidate.location);let reason=result.valid?null:result.reason,inside=null;
  if(!reason&&candidate.source!=='environment'){inside=refusedRoot(result.entry,roots);if(inside)reason=locationRefused(inside);}
  checked.push({...candidate,reason,...(inside?{refusedInside:inside}:{})});
  if(!reason)return {found:true,source:candidate.source,entry:result.entry,root:result.alaRoot,dependencies:result.dependencies,checked};
 }
 return {found:false,checked};
}
export const alaUnavailable='ALA was not found. Run "npm install" in this repository, or start the server with ALA_CLI pointing to the AdvancedLanguageAgent checkout.';

// ---------- Saved agent settings ----------
// A readable Markdown record, so the legacy Markdown-only profile never gains a data document. An "ALA location" row left
// by an earlier version is ignored and disappears when the record is next written.
export const DEFAULT_SETTINGS=Object.freeze({agent:'codex',model:'',effort:''});
const settingRows=[['agent','Coding agent'],['model','Model'],['effort','Effort']];
// Identifiers become ALA argument values, so none may start with '-' and be read as an option.
const AGENT_ID=/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/,EFFORT_ID=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const validModel=v=>typeof v==='string'&&v.length<=256&&!/[\r\n\0|]/.test(v)&&v===v.trim()&&!v.startsWith('-');
function cleanSettings(value){
 const out={...DEFAULT_SETTINGS};
 if(AGENT_ID.test(value?.agent||''))out.agent=value.agent;
 if(validModel(value?.model))out.model=value.model;
 if(value?.effort===''||EFFORT_ID.test(value?.effort||''))out.effort=value?.effort||'';
 return out;
}
export function parseSettings(markdown){
 const found={};
 for(const m of markdown.matchAll(/^\| ([^|\n]+?) \| ([^|\n]*?) \|$/gm)){
  const row=settingRows.find(([,label])=>label===m[1]);if(!row)continue;
  const raw=m[2].replace(/^`|`$/g,'');
  found[row[0]]=raw==='Agent default'?'':raw;
 }
 return cleanSettings(found);
}
export function formatSettings(settings){
 const s=cleanSettings(settings);
 const shown={agent:s.agent,model:s.model?'`'+s.model+'`':'Agent default',effort:s.effort||'Agent default'};
 return '# Coding agent settings\n\nNew verifications and report drafts run through ALA (Advanced Language Agent) with the coding agent below. Work that has already been queued keeps the settings it was queued with. Agent default leaves the model or effort choice to the coding agent itself.\n\n| Setting | Value |\n| --- | --- |\n'+settingRows.map(([key,label])=>'| '+label+' | '+shown[key]+' |').join('\n')+'\n';
}
export function createSettingsStore(file){
 return {
  file,
  read(){try{return parseSettings(fs.readFileSync(file,'utf8'));}catch(e){if(e.code==='ENOENT')return {...DEFAULT_SETTINGS};throw e;}},
  async write(settings){await fsp.mkdir(path.dirname(file),{recursive:true});const temporary=file+'.'+crypto.randomBytes(4).toString('hex')+'.tmp';await fsp.writeFile(temporary,formatSettings(settings));await fsp.rename(temporary,file);return this.read();}
 };
}

// ---------- Running a task through ALA ----------
// ALA reads --config, else ALA_CONFIG_PATH or ~/.ala/config.json, and treats a missing file as an empty configuration.
// Pointing --config at a file that is never created keeps personal ALA defaults from silently changing the recorded
// model or effort; the selection is always passed explicitly instead.
export const ABSENT_CONFIG='no-ala-config.json';
// The run folder is writable, and so is the agent home: by default ALA mounts the agent's own state folder, with a set
// folder (tools/agent-home.mjs) that folder. The repository is mounted read-only at its own path, so prompts can name
// skills, helpers and the Lean toolchain by absolute path while originals and other runs stay unmodifiable.
// `home` is null for your own home (no --home) or the absolute folder set with npm run set-home; it is never left out,
// so a forgotten argument cannot silently choose your own home. A home ALA cannot find stops ALA itself.
export const homeRequired='The agent home must be given: null for your own home, or an absolute folder';
const within=(inner,outer)=>{const relative=path.relative(path.resolve(outer),path.resolve(inner));return relative===''||(!relative.startsWith('..')&&!path.isAbsolute(relative))||liesWithin(inner,outer);};
export function alaArguments({entry,selection,cwd,home,root,taskFile,output}){
 const {agent,model,effort}=selection;
 if(!AGENT_ID.test(agent||''))throw Error('Invalid coding agent');
 if(home===undefined)throw Error(homeRequired);
 if(home!==null&&(typeof home!=='string'||!path.isAbsolute(home)))throw Error('The agent home must be an absolute path');
 if(home!==null&&[root,cwd].some(folder=>typeof folder==='string'&&folder&&within(home,folder)))throw Error('The agent home must lie outside the repository and the run folder');
 return [entry,'--ca',agent,...(model?['--model',model]:[]),...(model&&effort?['--effort',effort]:[]),'--config',path.join(cwd,ABSENT_CONFIG),'--cwd',cwd,...(home===null?[]:['--home',home]),'--folder',root,'--taskFile',taskFile,'--output',output];
}
// Standalone coding-agent execution uses the native agent login, never a host Ploinky router. The event stream exposes
// coding-agent messages and tool output on stderr for the execution journal.
export function alaEnvironment(env=process.env,options){
 return {...Object.fromEntries(Object.entries(certificateEnvironment(env,options)).filter(([key])=>!key.startsWith('PLOINKY_'))),ALA_EVENT_STREAM:'1'};
}
// macOS only. ALA's Seatbelt sandbox cannot reach the system certificate service, so a coding agent that verifies HTTPS
// through it (Codex) fails every request with "error sending request". ALA passes SSL_CERT_FILE into the sandbox and
// such agents read it, so the system's own CA bundle is named, unless the caller already set SSL_CERT_FILE or
// SSL_CERT_DIR. Measured with a real Codex request: exit 5 without the variable, an answer with it.
export const MACOS_CA_BUNDLE='/etc/ssl/cert.pem';
const isFile=file=>{try{return fs.statSync(file).isFile();}catch{return false;}};
export function certificateEnvironment(env=process.env,{platform=process.platform,exists=isFile}={}){
 if(platform!=='darwin'||env.SSL_CERT_FILE||env.SSL_CERT_DIR||!exists(MACOS_CA_BUNDLE))return env;
 return {...env,SSL_CERT_FILE:MACOS_CA_BUNDLE};
}
export const ALA_EVENT_PREFIX='@@ALA_EVENT@@';
// Converts ALA stderr lines into journal records. Streamed fragments that share an output id are joined into one record,
// as ALA's own transcript does; complete fragments without an id (for example one command's output) stay separate.
export function createALAJournal(record){
 let pending=null;const diagnostics=[];
 const flush=()=>{if(pending?.text)record(pending.kind==='assistant'?'agent message':'agent output',pending.kind==='assistant'?{type:'agent_message',agent:pending.agent,text:pending.text}:{type:'agent_output',agent:pending.agent,aggregated_output:pending.text});pending=null;};
 function message(event){
  const kind=event.outputKind==='assistant'?'assistant':'output',id=typeof event.outputId==='string'?event.outputId:null,text=typeof event.message==='string'?event.message:'';
  if(pending&&(pending.kind!==kind||pending.id!==id))flush();
  if(kind==='output'&&id===null){if(text)record('agent output',{type:'agent_output',agent:event.agent,aggregated_output:text});return;}
  pending??={kind,id,agent:event.agent,text:''};pending.text+=text;
  if(event.outputComplete||pending.text.length>1000000)flush();
 }
 return {
  line(line){
   if(!line.startsWith(ALA_EVENT_PREFIX)){if(line.trim()){diagnostics.push(line);if(diagnostics.length>40)diagnostics.shift();record('process diagnostic',{text:line});}return;}
   let event;try{event=JSON.parse(line.slice(ALA_EVENT_PREFIX.length));}catch{record('process diagnostic',{text:line});return;}
   if(event?.type==='coding-agent-message')return message(event);
   flush();
   if(event?.type==='coding-agent-selected')record('coding agent selected',{agent:event.agent,model:event.model||'Agent default',permission_mode:event.permissionMode});
   else if(event?.type==='coding-agent-final')record('final agent message',{type:'agent_message',agent:event.agent,text:String(event.message||'')});
   else if(event?.type==='coding-agent-request')record('coding agent request',{agent:event.agent,title:event.title,text:event.message,output:event.detail});
   else record(String(event?.type||'ALA event'),{output:JSON.stringify(event)});
  },
  flush,
  // The last ALA error line explains a failed run, for example a missing coding agent or sandbox.
  failure:()=>[...diagnostics].reverse().find(l=>l.startsWith('ala:'))||diagnostics.at(-1)||''
 };
}

// ---------- Agent and model catalog ----------
const catalogUnavailable=()=>fail('Could not load the ALA catalog. Check the ALA installation and coding-agent login, then retry.',503);
// `home` is the home a run would get, as in alaArguments: null for your own home, else the set folder. The worker runs
// detached, so a timeout kills its process group. The promise settles only after the worker has exited.
function runWorker(args,timeout,unavailable){
 return new Promise((resolve,reject)=>{
  const worker=fileURLToPath(new URL('./ala-catalog-worker.mjs',import.meta.url));
  // The worker starts the agent's catalog query in the sandbox, so it gets the same certificate file as a run.
  const child=spawn(process.execPath,[worker,...args],{stdio:['ignore','pipe','pipe'],detached:true,env:certificateEnvironment()});
  let output='',settled=false,failure=null;
  const stop=()=>{try{process.kill(-child.pid,'SIGKILL');}catch{}};
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);clearTimeout(grace);error?reject(error):resolve(value);};
  // A killed worker settles on its exit; the grace timer only covers a worker that never reports one.
  let grace=null;const abort=error=>{if(failure)return;failure=error;stop();grace=setTimeout(()=>finish(failure),5000);};
  const timer=setTimeout(()=>abort(unavailable()),timeout);
  child.stdout.on('data',chunk=>{output+=chunk;if(output.length>2000000)abort(unavailable());});
  // Native stderr can contain installation paths or account diagnostics; do not expose it through the API.
  child.stderr.resume();
  child.on('error',()=>finish(failure||unavailable()));
  child.on('exit',()=>{if(failure)finish(failure);});
  child.on('close',code=>{if(failure)return finish(failure);if(code!==0)return finish(unavailable());try{finish(null,JSON.parse(output));}catch{finish(unavailable());}});
 });
}
const workerHome=home=>{if(home===undefined)throw fail(homeRequired);if(home!==null&&(typeof home!=='string'||!path.isAbsolute(home)))throw fail('The agent home must be an absolute path');return home===null?'-':home;};
export function readALACatalog(entry,home,agent){
 if(!entry)return Promise.reject(fail(alaUnavailable,503));
 let folder;try{folder=workerHome(home);}catch(error){return Promise.reject(error);}
 return runWorker([entry,folder,...(agent?[agent]:[])],25000,catalogUnavailable);
}
// The login of one agent in the home a run would get: {login: {loggedIn, file, env, files}} or {problem: 'noModule'}.
// ALA's login module reads files a run can write (a login file swapped for a FIFO blocks the read), so the check runs in
// the worker process and is killed when it does not answer in time; the caller's process never opens those files.
export const loginCheckUnavailable=agent=>`The login check for ${agent} failed or did not finish, so no coding agent was started. A file in the agent home may be blocking it; see tools/README.md, "Limits".`;
export function readALALogin(entry,home,agent,{timeout=10000}={}){
 if(!entry)return Promise.reject(fail(alaUnavailable,503));
 if(typeof agent!=='string'||!AGENT_ID.test(agent))return Promise.reject(fail('Choose an installed coding agent.'));
 let folder;try{folder=workerHome(home);}catch(error){return Promise.reject(error);}
 return runWorker([entry,folder,'--login',agent],timeout,()=>fail(loginCheckUnavailable(agent),503));
}
const cleanEfforts=list=>Array.isArray(list)?[...new Set(list.filter(e=>typeof e==='string'&&EFFORT_ID.test(e)))]:[];
// The caller supplies `read(agent)`, which gives the worker the agent home a run would get; there is no default that
// reaches ALA.
export function createALACatalog(locate,{read,ttl=60000,scope=()=>''}={}){
 if(typeof read!=='function')throw new TypeError('createALACatalog needs a read(agent) function that supplies an agent home');
 // Every key carries `scope()` (the server passes the agent home in force), so an answer for one home is never served
 // from the cache for another, and a request never joins a read that started for another home.
 const cached=new Map(),pending=new Map();
 async function fetchCatalog(name,agent){
  const key=JSON.stringify([scope(),name]);
  const previous=cached.get(key);if(previous&&previous.until>Date.now())return previous.value;
  if(pending.has(key))return pending.get(key);
  if(pending.size>=4)throw fail('Catalog requests are busy. Please retry shortly.',429);
  const job=(async()=>{
   const result=await read(agent),field=agent?'models':'agents';
   if(!Array.isArray(result?.[field]))throw catalogUnavailable();
   const seen=new Set(),values=result[field].filter(item=>typeof item?.id==='string'&&(agent?validModel(item.id)&&item.id.length>0:AGENT_ID.test(item.id))).filter(item=>{if(seen.has(item.id))return false;seen.add(item.id);return true;}).map(item=>({id:item.id,label:typeof item.label==='string'?item.label.slice(0,256):item.id,...(!agent?{available:item.available===true}:{}),...(agent&&cleanEfforts(item.efforts).length?{efforts:cleanEfforts(item.efforts)}:{})}));
   const value={[field]:values};cached.set(key,{value,until:Date.now()+ttl});return value;
  })();pending.set(key,job);try{return await job;}finally{pending.delete(key);}
 }
 return {
  clear:()=>cached.clear(),
  agents:({refresh=false}={})=>{if(refresh)cached.delete(JSON.stringify([scope(),'agents']));return fetchCatalog('agents');},
  async models(agent,{refresh=false}={}){if(typeof agent!=='string'||!AGENT_ID.test(agent))throw fail('Choose an installed coding agent.');if(refresh)cached.delete(JSON.stringify([scope(),'models:'+agent]));const {agents}=await fetchCatalog('agents');if(!agents.some(a=>a.id===agent&&a.available))throw fail('Choose an installed coding agent.');return fetchCatalog('models:'+agent,agent);},
  // Only an installed agent and a model that ALA reports for it can be saved; an empty model or effort means Agent default.
  async validate(body){
   const model=body?.model??'',effort=body?.effort??'';
   if(typeof body?.agent!=='string'||typeof model!=='string'||typeof effort!=='string')throw fail('Choose a coding agent and model.');
   const {agents}=await fetchCatalog('agents');if(!agents.some(a=>a.id===body.agent&&a.available))throw fail('Choose an installed coding agent.');
   if(!model){if(effort)throw fail('Choose a model before choosing an effort.');return {agent:body.agent,model:'',effort:''};}
   const {models}=await this.models(body.agent),entry=models.find(m=>m.id===model);if(!entry)throw fail('Choose a model supported by the selected coding agent.');
   if(effort&&!entry.efforts?.includes(effort))throw fail('Choose an effort supported by the selected model.');
   return {agent:body.agent,model,effort};
  }
 };
}
