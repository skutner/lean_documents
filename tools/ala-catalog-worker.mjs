// Catalog-only ALA adapter. It lists installed coding agents and their models, or reports the login of one agent; it never
// starts a prompt, task or model inference.
//   node tools/ala-catalog-worker.mjs <ala entry> <agent home | -> [agent]
//   node tools/ala-catalog-worker.mjs <ala entry> <agent home | -> --login <agent>
// The login form asks ALA's login module, which reads the agent's login files. A run can write those files, so the
// server never reads them itself: it runs this process and kills it when it does not answer in time.
// The home is the one a run would get, as an ALA execution with or without --home (ALA's listAgentModels): `-` is your
// own home, so the environment is the caller's without PLOINKY_* and the service gets no home; a folder must be an
// existing real folder, and the environment is homeEnvironment(<environment without PLOINKY_*>, folder).
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [entrypoint,home,agent,loginAgent]=process.argv.slice(2);
let workspace;
try{
 const required=()=>Error('An agent home folder or "-" is required.');
 let agentHome=null;
 if(home!=='-'){
  if(typeof home!=='string'||!path.isAbsolute(home))throw required();
  const metadata=await fs.lstat(home).catch(()=>null);
  if(!metadata?.isDirectory())throw required();
  agentHome=await fs.realpath(home);
 }
 const entry=await fs.realpath(entrypoint),alaRoot=path.dirname(path.dirname(entry));
 // Standalone coding-agent discovery uses the native agent login, never a host Ploinky router.
 const caller=Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('PLOINKY_')));
 if(agent==='--login'){
  let logins=null;try{logins=await import(pathToFileURL(path.join(alaRoot,'src/coding-agents/logins.mjs')));}catch{}
  if(!logins||['homeEnvironment','codingAgentLoginFiles','detectCodingAgentLogin'].some(name=>typeof logins[name]!=='function')){process.stdout.write(JSON.stringify({problem:'noModule'}));process.exit(0);}
  const env=agentHome?logins.homeEnvironment(caller,agentHome):caller,detection=await logins.detectCodingAgentLogin(loginAgent,{env});
  process.stdout.write(JSON.stringify({login:{loggedIn:!!detection?.loggedIn,file:!!detection?.file,env:Array.isArray(detection?.env)?detection.env:[],files:logins.codingAgentLoginFiles(loginAgent,{env})}}));
  process.exit(0);
 }
 const {homeEnvironment}=await import(pathToFileURL(path.join(alaRoot,'src/coding-agents/logins.mjs')));
 const {discoverCodingAgents}=await import(pathToFileURL(path.join(alaRoot,'src/coding-agents/discovery.mjs')));
 const env=agentHome?homeEnvironment(caller,agentHome):caller;
 const agents=await discoverCodingAgents({env});
 if(!agent)process.stdout.write(JSON.stringify({agents:agents.map(a=>({id:a.name,label:a.name,available:!!a.available}))}));
 else{
  if(!agents.some(a=>a.name===agent&&a.available))throw Error('The selected coding agent is not installed.');
  const {createCodingAgentService}=await import(pathToFileURL(path.join(alaRoot,'src/coding-agents/service.mjs')));
  workspace=await fs.mkdtemp(path.join(os.tmpdir(),'evidence-audit-catalog-'));
  const service=createCodingAgentService({agents,workspace,home:agentHome,cwd:workspace,env});
  const models=await service.listModels(agent,{details:true,signal:AbortSignal.timeout(20000)});
  if(!Array.isArray(models))throw Error('ALA returned an invalid model catalog.');
  process.stdout.write(JSON.stringify({models:models.map(m=>typeof m==='string'?{id:m,label:m}:{id:m.id,label:m.label||m.id,efforts:Array.isArray(m.efforts)?m.efforts:[]})}));
 }
}catch(error){process.stderr.write(error.message+'\n');process.exitCode=1;}
finally{if(workspace)await fs.rm(workspace,{recursive:true,force:true});}
