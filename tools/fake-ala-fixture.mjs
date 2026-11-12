/** Explicitly simulated ALA checkout for isolated server tests. It never runs a coding agent, an audit, a proof or a model. */
import fs from 'node:fs/promises';
import path from 'node:path';

// Shared by the prelude and the fake service: names (never contents) under a folder, and SHA-256 hashes (never values)
// of an environment, so a test can show that a secret is absent without the record ever holding it.
const recording = `const listEntries=folder=>{const out=[];const walk=(dir,prefix)=>{let entries;try{entries=fs.readdirSync(dir,{withFileTypes:true});}catch{return;}for(const e of entries.sort((a,b)=>a.name.localeCompare(b.name))){out.push(prefix+e.name+(e.isDirectory()?'/':''));if(e.isDirectory())walk(path.join(dir,e.name),prefix+e.name+'/');}};walk(folder,'');return out;};
const isFolder=folder=>{try{return fs.statSync(folder).isDirectory();}catch{return false;}};
const valueHashes=env=>Object.keys(env||{}).sort().map(key=>key+' '+crypto.createHash('sha256').update(String(env[key])).digest('hex'));
const fence=lines=>'\\n\`\`\`text\\n'+lines.join('\\n')+'\\n\`\`\`\\n';
const realOrNearest=file=>{const rest=[];let current=path.resolve(file);for(;;){try{return path.join(fs.realpathSync(current),...rest);}catch{const parent=path.dirname(current);if(parent===current)return path.resolve(file);rest.unshift(path.basename(current));current=parent;}}};
const insideTemporary=file=>{const temporary=fs.realpathSync(os.tmpdir()),real=realOrNearest(file);return real===temporary||real.startsWith(temporary+path.sep);};`;

// The prelude gives each simulated executor the parsed ALA arguments and an event writer, and records the actual
// invocation in Markdown so tests can check the arguments, environment, agent home and working folder the server
// supplied. Like ALA it refuses a --home that is not an existing folder. `agentEnv` is the environment ALA gives the
// agent: with --home the one it derives (homeEnvironment of the fixture's login module), without --home the caller's
// own, as ALA's default. A test must never hand a fixture the developer's real home, so a --home outside the host
// temporary folder is refused before anything else happens.
const prelude = `#!${process.execPath}
// Explicit simulation: this fake ALA never runs a coding agent, an audit, a proof or a model.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
${recording}
const argv=process.argv.slice(2),value=flag=>{const i=argv.indexOf(flag);return i<0?undefined:argv[i+1];};
const cwd=value('--cwd'),taskFile=value('--taskFile'),output=value('--output'),agent=value('--ca'),home=value('--home');
if(argv.includes('--home')&&!insideTemporary(home||'/')){process.stderr.write('ala: the test fixture refuses a --home outside the temporary folder\\n');process.exit(1);}
const prompt=fs.readFileSync(taskFile,'utf8');
process.chdir(cwd);
const event=e=>{if(process.env.ALA_EVENT_STREAM==='1')process.stderr.write('@@ALA_EVENT@@'+JSON.stringify(e)+'\\n');};
const homeExists=!!home&&isFolder(home);
fs.writeFileSync(path.join(cwd,'fake-ala-invocation.md'),'# Simulated ALA invocation\\n\\nThis record comes from an explicit test executor. No coding agent ran.\\n\\n'+
 '| Check | Value |\\n| --- | --- |\\n| Process folder | '+process.cwd()+' |\\n| Event stream | '+(process.env.ALA_EVENT_STREAM||'off')+' |\\n| Ploinky variables | '+(Object.keys(process.env).some(k=>k.startsWith('PLOINKY_'))?'present':'absent')+' |\\n| Configuration file exists | '+(fs.existsSync(value('--config')||'')?'yes':'no')+' |\\n| Agent home | '+(home||'none')+' |\\n| Agent home exists | '+(homeExists?'yes':'no')+' |\\n\\n## Arguments\\n\\n\`\`\`text\\n'+argv.join('\\n')+'\\n\`\`\`\\n\\n## Agent home entries\\n'+fence(homeExists?listEntries(home):[])+'\\n## Environment value hashes\\n'+fence(valueHashes(process.env)));
if(home&&!homeExists){process.stderr.write('ala: Coding-agent home is unavailable.\\n');process.exit(1);}
const agentEnv=home?(await import(new URL('../src/coding-agents/logins.mjs',import.meta.url))).homeEnvironment(process.env,fs.realpathSync(home)):process.env;
event({type:'coding-agent-selected',agent,permissionMode:'full-access',...(value('--model')?{model:value('--model')}:{})});
`;

// Hand-authored stand-ins for ALA's discovery and service modules. When FAKE_ALA_RECORDS names a folder, each records
// the home and environment it was given (names and hashes only) there, so tests can show which home the server passed
// (`none` for your own home). The service refuses a home outside the host temporary folder, as the prelude does.
// Catalog metadata is hand-authored, never an AI agent or model response.
const discoveryModule = `import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
export async function discoverCodingAgents({env=process.env}={}){
 if(process.env.FAKE_ALA_RECORDS)fs.writeFileSync(path.join(process.env.FAKE_ALA_RECORDS,'discovery-'+process.pid+'-'+crypto.randomBytes(4).toString('hex')+'.md'),'# Simulated discovery\\n\\n| Check | Value |\\n| --- | --- |\\n| Environment HOME | '+(env.HOME||'unset')+' |\\n| Home exists | '+(env.HOME&&fs.existsSync(env.HOME)?'yes':'no')+' |\\n');
 return [{name:'codex',available:true,binary:null},{name:'claude',available:true,binary:null},{name:'pi',available:false,binary:null}];
}
`;
const serviceModule = `import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
${recording}
export function createCodingAgentService(options={}){
 if(options.home&&!insideTemporary(options.home))throw new Error('the test fixture refuses a home outside the temporary folder');
 if(process.env.FAKE_ALA_RECORDS){
  const home=options.home||null,exists=!!home&&isFolder(home);
  fs.writeFileSync(path.join(process.env.FAKE_ALA_RECORDS,'service-'+process.pid+'-'+crypto.randomBytes(4).toString('hex')+'.md'),'# Simulated coding-agent service\\n\\n| Check | Value |\\n| --- | --- |\\n| Agent home | '+(home||'none')+' |\\n| Agent home exists | '+(exists?'yes':'no')+' |\\n| Environment HOME | '+(options.env?.HOME||'unset')+' |\\n| Working folder | '+(options.cwd||'unset')+' |\\n\\n## Agent home entries\\n'+fence(exists?listEntries(home):[])+'\\n## Environment value hashes\\n'+fence(valueHashes(options.env)));
 }
 return {async listModels(agent){return agent==='codex'?[{id:'test-codex-model',label:'Test Codex Model',efforts:['low','high']}]:[{id:'test-claude-model',label:'Test Claude Model',efforts:[]}];}};
}
`;

// Hand-authored stand-in for ALA's src/coding-agents/logins.mjs, for the agents the fake discovery names. Like the real
// module it only looks at files and variable names; it never runs a CLI, contacts a provider or returns a value. The
// file lists and home variables follow ALA's descriptors; tools/test-agent-home.mjs compares them with a real ALA.
const loginsModule = `// Hand-authored simulation of ALA's login module. No coding agent or model is involved.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
const isRecord = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const filled = value => (typeof value === 'string' && value.trim() !== '') || (isRecord(value) && Object.keys(value).length > 0);
async function readJson(file) {
  try {
    const metadata = await fs.lstat(file);
    if (!metadata.isFile() || metadata.size > 4 * 1024 * 1024) return null;
    return JSON.parse(await fs.readFile(file, 'utf8'));
  } catch {
    return null;
  }
}
const AGENTS = [
  {name: 'codex', directory: (env, home) => path.resolve(env.CODEX_HOME || path.join(home, '.codex')),
   environment: root => ({CODEX_HOME: path.join(root, '.codex')}), files: ['auth.json'], variables: ['OPENAI_API_KEY'],
   async detect(directory) {
     const auth = await readJson(path.join(directory, 'auth.json'));
     return isRecord(auth) && Object.entries(auth).some(([key, value]) => !['last_refresh', 'auth_mode'].includes(key) && filled(value));
   }},
  {name: 'pi', directory: (env, home) => env.PI_CODING_AGENT_DIR ? path.resolve(env.PI_CODING_AGENT_DIR) : path.join(home, '.pi', 'agent'),
   environment: root => ({PI_CODING_AGENT_DIR: path.join(root, '.pi', 'agent')}), files: ['auth.json'], variables: ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY'],
   async detect(directory) {
     const auth = await readJson(path.join(directory, 'auth.json'));
     return isRecord(auth) && Object.values(auth).some(entry => isRecord(entry) && entry.type === 'api_key' && filled(entry.key));
   }},
  {name: 'claude', directory: (env, home) => path.resolve(env.CLAUDE_CONFIG_DIR || path.join(home, '.claude')),
   environment: root => ({CLAUDE_CONFIG_DIR: path.join(root, '.claude')}), files: ['.credentials.json', '.claude.json'],
   variables: ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN'],
   async detect(directory) {
     const oauth = (await readJson(path.join(directory, '.credentials.json')))?.claudeAiOauth;
     if (isRecord(oauth) && (filled(oauth.accessToken) || filled(oauth.refreshToken))) return true;
     const config = await readJson(path.join(directory, '.claude.json'));
     return isRecord(config) && filled(config.primaryApiKey);
   }}
];
function requireAgent(name) {
  const agent = AGENTS.find(candidate => candidate.name === name);
  if (!agent) throw new TypeError('Unknown coding agent: ' + name);
  return agent;
}
const stateDirectory = (agent, env) => agent.directory(env, path.resolve(env.HOME || os.homedir()));
export function homeEnvironment(env, home) {
  const root = path.resolve(home);
  return {...env, HOME: root, XDG_CONFIG_HOME: path.join(root, '.config'), XDG_DATA_HOME: path.join(root, '.local', 'share'),
    XDG_CACHE_HOME: path.join(root, '.cache'), XDG_STATE_HOME: path.join(root, '.local', 'state'),
    ...Object.assign({}, ...AGENTS.map(agent => agent.environment(root)))};
}
export function codingAgentLoginFiles(name, {env = process.env} = {}) {
  const agent = requireAgent(name);
  return agent.files.map(file => path.join(stateDirectory(agent, env), file));
}
export async function detectCodingAgentLogin(name, {env = process.env} = {}) {
  const agent = requireAgent(name);
  const file = await agent.detect(stateDirectory(agent, env));
  const variables = agent.variables.filter(variable => typeof env[variable] === 'string' && env[variable].trim() !== '');
  return {loggedIn: file || variables.length > 0, file, env: variables};
}
export async function detectCodingAgentLogins({env = process.env} = {}) {
  return Object.fromEntries(await Promise.all(AGENTS.map(async agent => [agent.name, await detectCodingAgentLogin(agent.name, {env})])));
}
`;

export async function createFakeALA(directory, body) {
  await fs.mkdir(path.join(directory, 'bin'), { recursive: true });
  await fs.mkdir(path.join(directory, 'src/coding-agents'), { recursive: true });
  await fs.writeFile(path.join(directory, 'package.json'), JSON.stringify({ name: 'advanced-language-agent', version: '0.0.0-simulated', private: true }) + '\n');
  // Hand-authored catalog metadata; never an AI agent or model response.
  await fs.writeFile(path.join(directory, 'src/coding-agents/discovery.mjs'), discoveryModule);
  await fs.writeFile(path.join(directory, 'src/coding-agents/logins.mjs'), loginsModule);
  await fs.writeFile(path.join(directory, 'src/coding-agents/service.mjs'), serviceModule);
  await fs.writeFile(path.join(directory, 'bin/ala.mjs'), prelude + body, { mode: 0o700 });
  return directory;
}
