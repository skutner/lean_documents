// Catalog-only ALA adapter. It lists installed coding agents and their models; it never starts a prompt, task or model inference.
//   node tools/ala-catalog-worker.mjs <ala entry> <agent home> [agent]
// Every call names an agent home, as an ALA execution with --home would (ALA's listAgentModels): the environment is
// homeEnvironment(<environment without PLOINKY_*>, home), so neither discovery nor the agent's own model listing ever
// looks at the user's real agent folders. The agent list may name a home that does not exist (only install paths are
// derived from it); a model listing needs an existing real folder, the home the server opened for it.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [entrypoint,home,agent]=process.argv.slice(2);
let workspace;
try{
 if(typeof home!=='string'||!path.isAbsolute(home))throw Error('An agent home is required.');
 let agentHome=path.resolve(home);
 if(agent){
  const metadata=await fs.lstat(agentHome).catch(()=>null);
  if(!metadata?.isDirectory())throw Error('An agent home is required.');
  agentHome=await fs.realpath(agentHome);
 }
 const entry=await fs.realpath(entrypoint),alaRoot=path.dirname(path.dirname(entry));
 const {homeEnvironment}=await import(pathToFileURL(path.join(alaRoot,'src/coding-agents/logins.mjs')));
 const {discoverCodingAgents}=await import(pathToFileURL(path.join(alaRoot,'src/coding-agents/discovery.mjs')));
 // Standalone coding-agent discovery uses the native agent login, never a host Ploinky router.
 const env=homeEnvironment(Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('PLOINKY_'))),agentHome);
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
