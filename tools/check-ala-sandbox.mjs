/** Model-free check of the ALA sandbox layout the audit server uses. ALA runs a simulated Codex inside its real
 * sandbox (Bubblewrap on Linux, Seatbelt through sandbox-exec on macOS) with the server's own arguments; the simulation
 * probes what the coding agent can read, write and execute. No model, prompt or network request is involved. The check
 * tests one of two modes, the configured one unless --mode says otherwise: `own` (ALA without --home, a fixture home
 * standing in for yours) or `folder` (ALA with --home on an empty temporary folder). Neither mode reads or writes the
 * owner's real agent state or login. Results
 * stay under tmp/ala-sandbox-check/. Two host fixtures live outside it and are removed on normal exit, on an error and on
 * SIGINT, SIGTERM or SIGHUP: a sentinel file in the host temporary folder and a helper process that carries a marker
 * variable (a SIGKILL ends the helper through its standard input but leaves the sentinel folder). */
import fs from 'node:fs/promises';
import {rmSync,existsSync,statSync,accessSync,constants as fsConstants} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn,execFile,spawnSync} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolveALA,alaArguments,alaEnvironment,ALA_EVENT_PREFIX,ABSENT_CONFIG} from './ala.mjs';
import {leanExecutable,resolvePython} from './toolchain.mjs';
import {readAgentHome,loadALALogins} from './agent-home.mjs';

// Keep in step with the Lean pin in toolchain.json and the Lean version named in the audit prompt in audit-ui.mjs.
export const LEAN_VERSION='4.24.0';
export const PROBE_MESSAGE='Sandbox probe finished.';
export const CHECKS=[
 ['cwd','The coding agent starts in the run folder',true],
 ['run_write','It can write inside the run folder, and the file reaches the host',true],
 ['repo_read','It can read the repository (the audit skill)',true],
 ['repo_write_denied','It cannot write at the repository root',true],
 ['tmp_write_denied','It cannot write in the repository tmp folder',true],
 ['sibling_write_denied','It cannot write next to its run folder, where other runs live',true],
 ['outside_read_denied','A sentinel file in the host temporary folder is neither readable nor listed',true],
 ['home_private','HOME is not the real home, and an entry of the real home is neither readable nor listed',true],
 ['other_process_environment','The environment of an outside process is unreadable, while a reader sees its own',true],
 ['real_agent_state_denied','The real coding-agent state folders are not the sandbox home\'s, and neither listable nor their login files readable',true],
 ['agent_state_shared','Without a separate agent home the agent\'s state folder inside is the real one: the agent uses your login, and a run can read and change that folder (measured on a fixture home)',true],
 ['python','The resolved Python is version 3.12 or later',true],
 ['helper_import','The audit helper loads: <python> tools/document_audit.py --help',true],
 ['lean_version',`The repository Lean toolchain reports version ${LEAN_VERSION}`,true],
 ['lean_check','Lean checks a small theorem written in the run folder',true],
 ['extractors','Optional PDF and OCR extractors on the sandbox PATH',false],
 ['home','Sandbox home, temporary folder and PATH',false],
 ['launchd_residual','Known macOS residual: existing launchd jobs (stated, not measured)',false],
];
// CHECKS is the union of both modes' rows (label lookups); a run uses only checksFor(its mode). real_agent_state_denied
// exists only with a separate agent home (folder), agent_state_shared only without one (own): in own the first cannot
// pass by design and is not listed, in folder the second cannot hold.
export const MODES=['own','folder'];
const ROW_OF_OTHER_MODE={own:'real_agent_state_denied',folder:'agent_state_shared'};
export function checksFor(mode){
 if(!MODES.includes(mode))throw Error('Unknown mode: '+mode);
 return CHECKS.filter(([key])=>key!==ROW_OF_OTHER_MODE[mode]);
}
// The checks that must fail when nothing confines the probe. cwd, run_write and repo_read pass unconfined, and so does
// agent_state_shared, which measures an exposure instead of a refusal.
const CONFINEMENT_ROWS=['repo_write_denied','tmp_write_denied','sibling_write_denied','outside_read_denied','home_private','other_process_environment','real_agent_state_denied'];
export const confinementFor=mode=>checksFor(mode).map(([key])=>key).filter(key=>CONFINEMENT_ROWS.includes(key));
const MODE_NAMES={own:'own home',folder:'folder'};
const MODE_TEXT={
 own:'ALA was started without `--home`; a fixture home stood in for yours. In this mode a run can read and change the agent\'s state folder, including its login, and no row says otherwise.',
 folder:'ALA was started with `--home`; an empty temporary folder stood in for the set folder. All runs share that folder, including the login kept there, and no row says otherwise.'
};
/** The two lines every report opens with: the configured mode against the mode checked (`configured` is a mode, or null
 * when the setting could not be read), then the fixed statement of what the checked mode does and does not give. */
export function modeStatement(mode,configured){
 if(!MODES.includes(mode))throw Error('Unknown mode: '+mode);
 return `Configured mode: ${configured?MODE_NAMES[configured]:'unknown (the setting could not be read)'}. Mode checked: ${MODE_NAMES[mode]}.\n\n${MODE_TEXT[mode]}`;
}
export const deniedProbes=({root,check,id})=>({repo_write_denied:path.join(root,`ala-sandbox-write-probe-${id}.txt`),tmp_write_denied:path.join(root,'tmp',`ala-sandbox-write-probe-${id}.txt`),sibling_write_denied:path.join(check,'outside-run-probe.txt')});
// A fixed statement, not a measurement: the check creates no launchd job (ALA DS006, decision Q5).
export const launchdResidual=(platform=process.platform)=>platform==='darwin'?'Known residual on macOS: a confined process can start or unload existing launchd jobs in your login session; see ALA DS006.':'not applicable';

// Reads kern.procargs2 (sysctl through syscall 202) of the process in argument 1 and of itself, and looks for the text
// NAME=VALUE for the variable named in argument 2, with the value taken from its own environment: the arguments of a
// process share kern.procargs2 with its environment, so the arguments alone must never satisfy it. `ps` cannot serve:
// it is set-id and does not run inside the macOS sandbox. No module is loaded.
export const READER_PERL=`my ($pid,$name)=@ARGV; my $needle=$name.'='.($ENV{$name}//'');
sub state { my $p=shift; my $buf="\\0" x 262144; my $len=pack('Q',262144);
 return 'denied errno='.($!+0) if syscall(202,pack('i3',1,49,$p),3,$buf,$len,0,0)!=0;
 return index(substr($buf,0,unpack('Q',$len)),$needle)>=0?'found':'absent'; }
print 'other='.state($pid).' own='.state($$)."\\n";
`;
const READER_OUTPUT=/^other=(found|absent|denied errno=\d+) own=(found|absent|denied errno=\d+)$/;
const SENTINEL_NAME='sentinel.txt',SENTINEL_PREFIX='ala-sandbox-sentinel-';
const HELPER_ARGS=['-e','process.stdin.on("end",()=>process.exit(0)).resume();setInterval(()=>{},1e6)'];
const SAFE_NAME=/^[\w .#:@+=,~-]+$/;
// Entries the host never picks: agent, runtime and package-manager state may be mounted, and names that suggest secrets.
const STATE_NAMES=/^\.(claude|codex|ala|opencode|pi|omp|lean-documents|config|local|cache|npm|nvm|volta|fnm|asdf|cargo|rustup|pyenv|bun|deno|node|homebrew)/;
const SECRET_NAMES=/key|token|secret|passw|credential|auth|private|\.pem$|\.env$|^\.?env|^\.(ssh|aws|gnupg|netrc|git-credentials|pgpass|pypirc|docker|kube)$/i;
const BENIGN_FILES=['.CFUserTextEncoding','.DS_Store','.hushlogin','.zshrc','.zprofile','.bashrc','.bash_profile','.profile','.lesshst','.viminfo','.gitconfig'];

// real_agent_state_denied, inside: for each state folder of the real home, (a) the same relative folder under $HOME must
// be absent or have another identity, device and inode as the host recorded them (stat follows a link, as a linked state
// folder would be), and (b) the real folder must not be listable and none of its login files readable, under the read
// control CTL. Nothing is ever written there, and
// nothing read is printed. The marker goes into $HOME only when HOME is neither the real home nor the state home.
const identFunction=platform=>`ident() { ${platform==='darwin'?`stat -L -f '%d:%i'`:`stat -L -c '%d:%i'`} "$1" 2>/dev/null; }`;

// agent_state_shared, inside (own mode, where `state` describes the fixture home that stands in for yours): $HOME/.codex
// must have the device and inode of the fixture's .codex (the identity comparison of check_state, stat following a link),
// and only then is a marker written into it, so a marker can only ever reach the fixture, never another .codex. The host
// then looks for the marker in the fixture folder (see combineShared).
function sharedSection({state,marker,platform}){
 const folder=state?.folders.find(f=>f.relative==='.codex');
 if(!folder||!marker)return `record agent_state_shared fail 'not shared: the host found no .codex in the fixture home'`;
 return `SREAL='${state.home}'
${identFunction(platform)}
IN="$HOME/.codex"; ID='${folder.dev}:${folder.ino}'
if [ ! -e "$IN" ]; then record agent_state_shared fail "not shared: \\$HOME/.codex does not exist inside (HOME=$HOME)"
else
 I=$(ident "$IN")
 if [ -z "$I" ]; then record agent_state_shared fail "not shared: cannot tell the identity of \\$HOME/.codex (HOME=$HOME)"
 elif [ "$I" != "$ID" ]; then record agent_state_shared fail "not shared: \\$HOME/.codex (HOME=$HOME) is not the folder $SREAL/.codex"
 elif printf '%s\\n' '${marker.value}' > "$IN/${marker.name}" 2>/dev/null; then record agent_state_shared pass "shared: \\$HOME/.codex (HOME=$HOME) is the folder $SREAL/.codex, and a marker written into it"
 else record agent_state_shared fail "not shared: \\$HOME/.codex is the folder $SREAL/.codex, but a marker cannot be written into it"; fi
fi`;
}

function stateSection({state,marker,platform}){
 const writeMarker=marker?`SP=; [ -n "$SREAL" ] && SP=$(cd "$SREAL" 2>/dev/null && pwd -P)
if [ -n "$HP" ] && [ "$HP" != "$HREAL" ] && [ "$HP" != "$SP" ]; then printf '%s\\n' '${marker.value}' > "$HOME/${marker.name}" 2>/dev/null; fi`:'';
 if(!state)return `SREAL=
${writeMarker}
record real_agent_state_denied fail 'The host named no real home whose state folders the probe should check'`;
 return `SREAL='${state.home}'
${writeMarker}
${identFunction(platform)}
SFAIL=; SOK=
check_state() {
 R="$SREAL/$1"; IN="$HOME/$1"; ID="$2"; shift 2
 if [ -e "$IN" ]; then
  I=$(ident "$IN")
  if [ -z "$I" ]; then SFAIL="$SFAIL; cannot tell the identity of \\$HOME/$(basename "$R")"; return; fi
  if [ "$I" = "$ID" ]; then SFAIL="$SFAIL; \\$HOME/\${R#"$SREAL"/} is your real folder $R"; return; fi
 fi
 if ls -A "$R" >/dev/null 2>&1; then SFAIL="$SFAIL; $R is listable inside"; return; fi
 for F in "$@"; do if head -c 1 -- "$R/$F" >/dev/null 2>&1; then SFAIL="$SFAIL; $R/$F is readable inside"; return; fi; done
 SOK="$SOK, $R"
}
if [ "$CTL" != ok ]; then record real_agent_state_denied fail "Control read or listing in the run folder failed, so a refusal proves nothing"
${state.folders.length?`else
${state.folders.map(f=>` check_state '${f.relative}' '${f.dev}:${f.ino}'${f.files.map(file=>` '${file}'`).join('')}`).join('\n')}
 if [ -n "$SFAIL" ]; then record real_agent_state_denied fail "HOME=$HOME: \${SFAIL#; }"
 else record real_agent_state_denied pass "HOME=$HOME is none of them, and none is listable or has a readable login file inside: \${SOK#, }"; fi`:`else record real_agent_state_denied na 'not applicable: no coding-agent state folder exists in the real home'`}
fi`;
}

/** Completes agent_state_shared (own mode) on the host. `marker`: the probe's marker was found in the fixture's .codex
 * folder. The row passes only with it; any other outcome is `not shared: …`. */
export function combineShared(row,{marker=false}={}){
 if(row.status==='pass'&&marker)return Object.assign(row,{detail:row.detail+'; its marker reached the fixture folder'});
 if(row.status==='pass')return failCheck(row,'not shared: the probe\'s marker is missing from the fixture\'s .codex folder');
 return failCheck(row,/^not shared:/.test(row.detail||'')?row.detail:'not shared: '+(row.detail||'the probe did not record this check'));
}

/** Completes real_agent_state_denied on the host. `hostProblem`: a host control failed; `marker`: the probe's marker was
 * found in the --home folder; `control`: the positive control's row and marker. The row passes only with the control. */
export function combineState(row,{hostProblem=null,marker=false,control=null}={}){
 if(hostProblem)return failCheck(row,hostProblem);
 if(!marker)return failCheck(row,(row.status==='fail'?row.detail+'; ':'')+'the probe\'s marker is missing from the --home folder, so HOME inside was not the dedicated home');
 const controlOk=control?.status==='pass'&&control.marker===true;
 const controlText=!control?'the positive control did not run':controlOk?`positive control passed: ${control.detail}; its marker reached its --home folder`:`positive control failed: ${control.status==='pass'?'its marker is missing from its --home folder':control.detail}`;
 if(!controlOk)return failCheck(row,(row.status==='fail'?row.detail+'; ':'')+controlText);
 if(row.status==='na')return Object.assign(row,{status:'pass',ok:true,detail:row.detail+'; '+controlText});
 return Object.assign(row,{detail:row.detail+'; '+controlText});
}

// A POSIX shell stands in for Codex: ALA starts it as `codex ... exec --json ... <prompt>` and needs one agent_message
// on stdout. The sandbox clears the environment, so every path is written into the script. `python` and `lean` are
// absolute paths (or null when the host could not resolve them; the host then reports the two checks itself).
// `sentinel`, `home` and `helper` describe the host fixtures: see makeSentinel, pickHomeEntry and startHelper.
// `state` ({home, folders: [{relative, path, ino, files}]}, see agentStateFolders) names the coding-agent state folders of
// a real home (folder mode) or of the fixture home (own mode), and `marker` ({name, value}) is written into $HOME (folder)
// or into $HOME/.codex when that is the fixture's (own) so the host can tell which folder HOME was. `mode` picks the row:
// real_agent_state_denied in folder, agent_state_shared in own.
export function probeScript({run,root,check,id,python=null,lean=null,platform=process.platform,sentinel,home,helper,reader,state=null,marker=null,mode='folder'}){
 if(!MODES.includes(mode))throw Error('Unknown mode: '+mode);
 const values=[run,root,check,id,python||'',lean||'',sentinel.file,sentinel.dir,sentinel.marker,sentinel.base,home.real,home.entry||'',helper.name,helper.value,String(helper.pid),reader,
  ...(state?[state.home,...state.folders.flatMap(f=>[f.relative,String(f.dev),String(f.ino),...f.files])]:[]),...(marker?[marker.name,marker.value]:[])];
 for(const value of values)if(/['\r\n\\]/.test(value))throw Error('This check does not support quotes, backslashes or line breaks in paths: '+value);
 if(marker&&!/^[\w.-]+$/.test(marker.name+marker.value))throw Error('Unsupported marker');
 if(state&&state.folders.some(f=>!/^\d+$/.test(String(f.dev))||!/^\d+$/.test(String(f.ino))))throw Error('Unsupported folder identity');
 if(home.entry&&!SAFE_NAME.test(home.entry))throw Error('Unsupported characters in the real home entry name');
 const helperFile=path.join(root,'tools/document_audit.py'),skill=path.join(root,'skills/evidence-to-proof-review/SKILL.md'),denied=deniedProbes({root,check,id}),needle=`${helper.name}=${helper.value}`;
 const readEntry=home.kind==='file'?'head -c 1 -- "$HREAL/$HENTRY" >/dev/null 2>&1':'ls -A "$HREAL/$HENTRY" >/dev/null 2>&1';
 const reading=platform==='darwin'?`OE=$(${helper.name}='${helper.value}' /usr/bin/perl '${reader}' ${helper.pid} '${helper.name}' 2>&1); OR=$?`
  :`envstate() { grep -zqxF '${needle}' "$1" 2>/dev/null; case $? in 0) echo found;; 1) echo absent;; *) echo denied;; esac; }
envown() { ${helper.name}='${helper.value}' grep -zqxF '${needle}' /proc/self/environ 2>/dev/null; case $? in 0) echo found;; 1) echo absent;; *) echo denied;; esac; }
OE="other=$(envstate /proc/${helper.pid}/environ) own=$(envown)"; OR='n/a, grep reader'`;
 return `#!/bin/sh
# Simulated Codex for the model-free ALA sandbox check. It never contacts a model or the network.
RUN='${run}'
OUT="$RUN/probe-results.tsv"
record() { printf '%s\\t%s\\t%s\\n' "$1" "$2" "$(printf '%s' "$3" | tr '\\t\\r\\n' '   ' | cut -c1-500)" >> "$OUT"; }
denied() { if ( : > "$1" ) 2>/dev/null; then record "$2" fail "Write succeeded: $1"; else record "$2" pass "Write refused: $1"; fi; }
: > "$OUT" || exit 3
if [ "$(pwd -P)" = "$RUN" ]; then record cwd pass "$(pwd -P)"; else record cwd fail "Started in $(pwd -P)"; fi
if printf 'Written inside the sandbox.\\n' > "$RUN/write-probe.txt"; then record run_write pass "$RUN/write-probe.txt"; else record run_write fail "Cannot write $RUN/write-probe.txt"; fi
if head -n 1 '${skill}' >/dev/null 2>&1; then record repo_read pass '${skill}'; else record repo_read fail 'Cannot read ${skill}'; fi
denied '${denied.repo_write_denied}' repo_write_denied
denied '${denied.tmp_write_denied}' tmp_write_denied
denied '${denied.sibling_write_denied}' sibling_write_denied
# Control for the read checks below: ls, cat and head must work where they are allowed, or a refusal proves nothing.
CTL=fail
case "
$(ls -A "$RUN" 2>/dev/null)
" in *"
probe-results.tsv
"*) if [ -n "$(cat "$OUT" 2>/dev/null)" ] && [ -n "$(head -c 1 -- "$OUT" 2>/dev/null)" ]; then CTL=ok; fi;; esac
SFILE='${sentinel.file}'; SDIR='${sentinel.dir}'
if [ "$CTL" != ok ]; then record outside_read_denied fail "Control read or listing in the run folder failed, so a refusal proves nothing"
else
 SR=$(cat "$SFILE" 2>&1); SL=$(ls -A "$SDIR" 2>&1)
 case "$SR" in *'${sentinel.marker}'*) record outside_read_denied fail "Read the sentinel outside the mounts: $SFILE";;
 *) case "
$SL
" in *"
${SENTINEL_NAME}
"*) record outside_read_denied fail "Listed the sentinel folder outside the mounts: $SDIR";; *) record outside_read_denied pass "Neither read nor listed: $SFILE";; esac;; esac
fi
HREAL='${home.real}'; HENTRY='${home.entry||''}'
HP=; [ -n "$HOME" ] && HP=$(cd "$HOME" 2>/dev/null && pwd -P)
if [ "$CTL" != ok ]; then record home_private fail "Control read or listing in the run folder failed, so a refusal proves nothing"
elif [ -z "$HENTRY" ]; then record home_private fail "The host found no entry of $HREAL that is off every mount path"
elif [ -z "$HP" ]; then record home_private fail "HOME is unset or not a folder: $HOME"
elif [ "$HP" = "$HREAL" ]; then record home_private fail "HOME is the real home: $HOME"
elif ${readEntry}; then record home_private fail "An entry of the real home is readable inside: $HREAL/$HENTRY"
else
 LH=$(ls -A "$HREAL" 2>/dev/null); LS=$?
 if [ "$LS" -ne 0 ]; then LK="listing of $HREAL refused"; else LK="listing of $HREAL shows $(printf '%s\n' "$LH" | wc -l | tr -d ' ') other entries"; fi
 case "
$LH
" in *"
$HENTRY
"*) record home_private fail "The listing of the real home shows $HENTRY";; *) record home_private pass "HOME=$HOME is not the real home; $HENTRY (${home.kind}) is not readable; $LK, without it";; esac
fi
${reading}
case "$OE" in
 "other=found own=found") record other_process_environment fail "visible: the environment of outside process ${helper.pid} is readable inside ($OE)";;
 "other=denied"*" own=found") record other_process_environment pass "hidden: the environment of outside process ${helper.pid} is refused, while the reader sees its own ($OE)";;
 *) record other_process_environment fail "The reader did not give a usable answer (exit $OR): $OE";;
esac
${mode==='own'?sharedSection({state,marker,platform}):stateSection({state,marker,platform})}
${python?`PY='${python}'
V=$("$PY" -c 'import sys; print(sys.version.split()[0])' 2>&1)
if "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then record python pass "$PY $V"; else record python fail "$PY $V; 3.12 or later is required"; fi
HF='${helperFile}'
H=$("$PY" -B "$HF" --help 2>&1)
if [ $? -eq 0 ]; then record helper_import pass "$PY -B $HF --help"; else record helper_import fail "$H"; fi`:'# No Python 3.12 or later was resolved on the host; the host reports python and helper_import.'}
${lean?`LEAN='${lean}'
if [ -x "$LEAN" ]; then
 LV=$("$LEAN" --version 2>&1)
 case "$LV" in *"version ${LEAN_VERSION},"*|*"version ${LEAN_VERSION})"*|*"version ${LEAN_VERSION} "*) record lean_version pass "$LV";; *) record lean_version fail "$LV";; esac
 printf 'theorem alaSandboxProbe : 2 + 2 = 4 := rfl\\n' > "$RUN/SandboxProbe.lean"
 LC=$("$LEAN" "$RUN/SandboxProbe.lean" 2>&1)
 if [ $? -eq 0 ]; then record lean_check pass "lean $RUN/SandboxProbe.lean"; else record lean_check fail "$LC"; fi
else record lean_version fail "Not executable inside the sandbox: $LEAN"; record lean_check fail "Lean is unavailable"; fi`:'# The Lean toolchain has no location on this platform; the host reports lean_version and lean_check.'}
X=""
for T in pdftotext pdftoppm tesseract; do if command -v "$T" >/dev/null 2>&1; then X="$X $T=$(command -v "$T")"; else X="$X $T=missing"; fi; done
record extractors info "$X"
HK='private, not the real home'; [ "$HP" = "$HREAL" ] && HK='the real home'; [ -z "$HP" ] && HK='unavailable'
TK=unset; if [ -n "$TMPDIR" ]; then case "
$(ls -A "$TMPDIR" 2>/dev/null)
" in *"
${path.basename(sentinel.dir)}
"*) TK='the host temporary folder: it lists the sentinel folder';; *) TK='private, not the host temporary folder';; esac; fi
record home info "HOME=$HOME ($HK) TMPDIR=\${TMPDIR:-unset} ($TK) PATH=$PATH"
printf '%s\\n' '{"type":"item.completed","item":{"type":"agent_message","text":"${PROBE_MESSAGE}"}}'
exit 0
`;
}

// `overrides` replaces rows the host knows better than the probe: a Python or Lean location that did not resolve, and
// the launchd line, which is stated rather than measured. `pythonWarning` is the resolver's note on a home-folder interpreter.
export function evaluate(tsv,{mode='folder',platform=process.platform,pythonError=null,pythonWarning=null,leanError=null}={}){
 const rows=new Map();
 for(const line of String(tsv||'').split('\n')){if(!line)continue;const [key,status,...detail]=line.split('\t');rows.set(key,{status,detail:detail.join(' ')});}
 for(const key of pythonError?['python','helper_import']:[])rows.set(key,{status:'fail',detail:pythonError});
 for(const key of leanError?['lean_version','lean_check']:[])rows.set(key,{status:'fail',detail:leanError});
 rows.set('launchd_residual',{status:'info',detail:launchdResidual(platform)});
 if(pythonWarning&&rows.get('python')&&!pythonError)rows.get('python').detail+=' Warning: '+pythonWarning;
 return checksFor(mode).map(([key,label,required])=>{const row=rows.get(key);return {key,label,required,status:row?.status||'missing',detail:row?.detail||'The probe did not record this check.',ok:!required||row?.status==='pass'};});
}
// A host-side finding overrides whatever the probe reported for the check.
export const failCheck=(check,detail)=>Object.assign(check,{status:'fail',ok:false,detail});

const cell=value=>String(value??'').replaceAll('|','&#124;').replaceAll('\n',' ');
// The simulation never uses them, and a missing API key keeps a mistaken real Codex from reaching a model.
const isCredential=key=>key.startsWith('OPENAI_')||key.startsWith('ANTHROPIC_')||key==='CLAUDE_CODE_OAUTH_TOKEN';
const withoutCredentials=env=>Object.fromEntries(Object.entries(env).filter(([key])=>!isCredential(key)));
// The agent state variables (CODEX_HOME, CLAUDE_CONFIG_DIR, XDG_*, ...) that ALA's login module derives from a home:
// none of the caller's may reach ALA, which with --home derives them again from that home.
const withoutAgentState=(env,logins)=>{const names=new Set(Object.keys(logins.homeEnvironment({},'/')).filter(key=>key!=='HOME'));return Object.fromEntries(Object.entries(env).filter(([key])=>!names.has(key)));};
// ALA's own discovery, imported from the ALA folder that will run, answers which binaries it would start.
export const alaDiscovery=entry=>async env=>(await import(pathToFileURL(path.join(path.dirname(path.dirname(entry)),'src/coding-agents/discovery.mjs')).href)).discoverCodingAgents({env});
// Last look before ALA starts: no model credential is in the environment, the agent home is an empty folder (or, in own
// mode, holds exactly the fixture `homeFiles`), and ALA will really run this check's probe script. ALA
// skips a CODEX_BIN that is not executable and then falls back to <HOME>/.local/bin/codex and to `codex` on PATH, so
// the value of the variable proves nothing: discovery, with the environment ALA derives (`homeEnvironment(env, home)`
// for a --home run), must return a Codex record whose binary is the real path of the probe. Only names are reported.
export async function assertModelFree({env,codex,home,homeFiles=null,homeEnvironment=null,discover}){
 const credentials=Object.keys(env).filter(isCredential);
 if(credentials.length)throw Error('Refusing to start ALA: the environment holds model credentials: '+credentials.join(', '));
 if(env.CODEX_BIN!==codex)throw Error('Refusing to start ALA: CODEX_BIN is not the probe script ('+env.CODEX_BIN+')');
 if(!(await fs.readFile(codex,'utf8')).includes('Simulated Codex for the model-free ALA sandbox check'))throw Error('Refusing to start ALA: CODEX_BIN is not the simulated Codex: '+codex);
 if(typeof home!=='string'||!path.isAbsolute(home)||!(await stat(home))?.isDirectory())throw Error('Refusing to start ALA: the agent home is not an existing folder ('+home+')');
 const expected=homeFiles?[...new Set(Object.keys(homeFiles).flatMap(file=>file.split('/').map((_,i,parts)=>parts.slice(0,i+1).join('/'))))].sort():[];
 const entries=(await fs.readdir(home,{recursive:true})).map(String).sort();
 if(JSON.stringify(entries)!==JSON.stringify(expected))throw Error(homeFiles?'Refusing to start ALA: the fixture home holds something other than '+Object.keys(homeFiles).join(', ')+' ('+home+')':'Refusing to start ALA: the agent home is not an empty folder ('+home+')');
 for(const [file,content] of Object.entries(homeFiles||{}))if(await fs.readFile(path.join(home,file),'utf8')!==content)throw Error('Refusing to start ALA: the fixture file '+file+' does not hold the fixture content');
 const info=await stat(codex);
 if(!info?.isFile()||!(info.mode&0o100))throw Error('Refusing to start ALA: the probe script is not a regular file that its owner can execute: '+codex);
 await fs.access(codex,fs.constants.X_OK).catch(()=>{throw Error('Refusing to start ALA: the probe script cannot be executed (a noexec mount?): '+codex);});
 if(typeof discover!=='function')throw Error('Refusing to start ALA: nothing confirms which Codex ALA will run');
 const runEnv=homeEnvironment?homeEnvironment(env,await fs.realpath(home)):env;
 const records=await discover(runEnv).catch(error=>{throw Error('Refusing to start ALA: ALA discovery failed: '+error.message);});
 const record=records?.find?.(item=>item.name==='codex'),real=await fs.realpath(codex);
 if(!record?.available||record.binary!==real)throw Error(`Refusing to start ALA: ALA would run ${record?.binary??'no Codex'} instead of the probe script ${real}`);
}

// ---------- Host fixtures ----------
const stat=async file=>{try{return await fs.lstat(file);}catch{return null;}};

/** A file in the host temporary folder, which no sandbox mount covers. It must lie outside the repository, because the
 * repository is mounted read-only and the sandbox would read it, and outside the real home, which the check must not
 * write to: a temporary folder inside the home is replaced by /tmp, or refused. */
export async function makeSentinel(root,{home=null,tmpdir=os.tmpdir()}={}){
 const homeReal=home?await fs.realpath(home).catch(()=>null):null,inHome=folder=>Boolean(homeReal)&&(folder===homeReal||folder.startsWith(homeReal.endsWith(path.sep)?homeReal:homeReal+path.sep));
 let base=await fs.realpath(tmpdir);
 if(inHome(base)){const fallback=await fs.realpath('/tmp').catch(()=>null);if(!fallback||inHome(fallback))throw Error('The host temporary folder lies inside the home folder: '+base);base=fallback;}
 const dir=await fs.realpath(await fs.mkdtemp(path.join(base,SENTINEL_PREFIX))),marker=crypto.randomBytes(16).toString('hex'),file=path.join(dir,SENTINEL_NAME);
 const sentinel={base,dir,file,marker};
 try{
  if(dir===root||dir.startsWith(root+path.sep))throw Error('The host temporary folder lies inside the repository: '+dir);
  await fs.writeFile(file,marker);
  if(await fs.readFile(file,'utf8')!==marker)throw Error('The host cannot read back the sentinel '+file);
 }catch(error){removeSentinel(sentinel);throw error;}
 return sentinel;
}
/** Removes only the folder makeSentinel created. Returns true when it is gone. */
export function removeSentinel({base,dir}){
 if(path.dirname(dir)===base&&path.basename(dir).startsWith(SENTINEL_PREFIX))rmSync(dir,{recursive:true,force:true});
 return !existsSync(dir);
}

const BASE_PREFIX='ala-sandbox-base-';
/** The check's own agent-home base, beside the sentinel in the host temporary folder (outside the repository and the
 * real home), laid out as the server's: homes/check-home is the --home of the run in folder mode. Also the positive control
 * homes, and ownHome, the fixture that stands in for your home in own mode. */
export async function makeTemporaryBase(sentinel){
 const dir=await fs.realpath(await fs.mkdtemp(path.join(sentinel.base,BASE_PREFIX)));
 const base={base:sentinel.base,dir,homes:path.join(dir,'homes'),checkHome:path.join(dir,'homes','check-home'),controlHome:path.join(dir,'homes','control-home'),controlReal:path.join(dir,'control-real-home'),ownHome:path.join(dir,'own-home'),neverCreated:path.join(dir,'never-created-home')};
 try{for(const folder of [base.homes,base.checkHome,base.controlHome])await fs.mkdir(folder,{mode:0o700});}catch(error){removeTemporaryBase(base);throw error;}
 return base;
}
/** Removes only the folder makeTemporaryBase created. Returns true when it is gone. */
export function removeTemporaryBase({base,dir}){
 if(path.dirname(dir)!==base||!path.basename(dir).startsWith(BASE_PREFIX))return !existsSync(dir);
 try{rmSync(dir,{recursive:true,force:true});}catch{spawnSync('/bin/chmod',['-R','u+rwx',dir]);try{rmSync(dir,{recursive:true,force:true});}catch{}}
 return !existsSync(dir);
}
/** A fixture home holding `.codex/auth.json` with `{}`: no login, only the folder whose identity the controls check. */
export async function makeCodexFixture(home){
 await fs.mkdir(path.join(home,'.codex'),{recursive:true,mode:0o700});
 await fs.writeFile(path.join(home,'.codex/auth.json'),'{}',{mode:0o600});
 return {'.codex/auth.json':'{}'};
}

/** The coding-agent state folders of `home` that exist, derived from ALA's login module (the folders of each agent's
 * login files). Only metadata of the folders is read (stat), never a listing or a file inside them. `probeHome` is a
 * path that never exists: detection there only yields ALA's agent names. */
export async function agentStateFolders({logins,home,probeHome}){
 const names=Object.keys(await logins.detectCodingAgentLogins({env:logins.homeEnvironment({},probeHome)}));
 const folders=new Map();
 for(const name of names)for(const file of logins.codingAgentLoginFiles(name,{env:logins.homeEnvironment({},home)})){
  const folder=path.dirname(file),relative=path.relative(home,folder);
  if(!relative||relative.startsWith('..')||path.isAbsolute(relative))continue;
  const entry=folders.get(relative)||{relative,path:folder,agents:[],files:[]};
  if(!entry.agents.includes(name))entry.agents.push(name);if(!entry.files.includes(path.basename(file)))entry.files.push(path.basename(file));
  folders.set(relative,entry);
 }
 // Only a folder that does not exist is left out (ENOTDIR: an ancestor is not a folder, so it cannot exist either). Any
 // other error leaves the folder's state unknown, which stateHostProblem turns into a failure of the row.
 const found=[],problems=[];
 for(const entry of folders.values()){let info;try{info=statSync(entry.path);}catch(error){if(error.code!=='ENOENT'&&error.code!=='ENOTDIR')problems.push(`The host cannot examine ${entry.path} (${error.code||error.message}), so the row cannot tell whether it is protected.`);continue;}if(info.isDirectory())found.push({...entry,dev:info.dev,ino:info.ino});}
 return {home,folders:found,problems};
}
/** A refusal inside means something only if the host itself can examine and list each folder (stat and access(2) only;
 * nothing is read). */
export function stateHostProblem(state){
 if(state.problems?.length)return state.problems[0];
 for(const folder of state.folders){try{accessSync(folder.path,fsConstants.R_OK|fsConstants.X_OK);}catch{return `The host cannot list ${folder.path}, so a refusal inside proves nothing.`;}}
 return null;
}

/** An existing entry of the real home for the home_private check. It is never read: only its type is looked at, and
 * names that suggest secrets or agent and runtime state are skipped. `avoid` lists the paths of everything mounted
 * (repository, ALA, Node, interpreter): an entry that equals, contains or lies inside one of them is skipped, since the
 * sandbox legitimately shows the path to a mount. `above` lists paths (the temporary folder) of which only the
 * entries that equal or contain them are skipped. Ranking: benign dotfiles, then folders, then other files. */
export async function pickHomeEntry({home,avoid=[],above=[]}){
 const real=await fs.realpath(home),known=[],over=[];
 for(const item of avoid.filter(Boolean))known.push(await fs.realpath(item).catch(()=>path.resolve(item)));
 for(const item of above.filter(Boolean))over.push(await fs.realpath(item).catch(()=>path.resolve(item)));
 const related=(a,b)=>a===b||a.startsWith(b+path.sep)||b.startsWith(a+path.sep),found=[];
 for(const name of (await fs.readdir(real)).sort()){
  if(!SAFE_NAME.test(name)||STATE_NAMES.test(name)||SECRET_NAMES.test(name))continue;
  const file=path.join(real,name),info=await stat(file);
  if(!info||info.isSymbolicLink()||(!info.isFile()&&!info.isDirectory())||known.some(item=>related(file,item))||over.some(item=>item===file||item.startsWith(file+path.sep)))continue;
  if(await fs.access(file,fs.constants.R_OK).then(()=>false,()=>true))continue;
  found.push({entry:name,kind:info.isFile()?'file':'dir',rank:info.isFile()?(BENIGN_FILES.includes(name)?0:2):1});
 }
 found.sort((a,b)=>a.rank-b.rank);
 return {real,entry:found[0]?.entry??null,kind:found[0]?.kind??null};
}

/** A Node process (not an Apple program, whose environment macOS never shows to another process) with a random marker
 * variable and nothing else in its environment. It exits when its standard input closes, so it ends even when this
 * check is killed. `stop()` kills it, waits and returns true once the process is gone. */
export async function startHelper({command=process.execPath,args=HELPER_ARGS}={}){
 const name='ALA_CHECK_MARKER_'+crypto.randomBytes(4).toString('hex').toUpperCase(),value=crypto.randomBytes(16).toString('hex');
 const child=spawn(command,args,{env:{[name]:value},stdio:['pipe','ignore','ignore']});
 child.stdin.on('error',()=>{});
 await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject);});
 const finished=()=>child.exitCode!==null||child.signalCode!==null,pid=child.pid;
 return {pid,name,value,child,needle:`${name}=${value}`,async stop(){
  if(!finished()){try{child.stdin.end();}catch{}child.kill('SIGKILL');await new Promise(resolve=>{if(finished())return resolve();child.once('exit',resolve);setTimeout(resolve,5000).unref();});}
  try{process.kill(pid,0);return false;}catch(error){return error.code==='ESRCH';}
 }};
}

const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const runFile=(file,args,env)=>new Promise(resolve=>execFile(file,args,{env,timeout:15000},(error,stdout,stderr)=>resolve({error,stdout:String(stdout).trim(),stderr:String(stderr).trim()})));

/** On the host, with no sandbox, the same reader must see the helper's marker. If it cannot (a protected helper, a
 * reader that does not run), a "hidden" answer inside the sandbox would say nothing, so the check fails. */
export async function confirmOnHost({helper,reader,platform=process.platform,attempts=40,delay=50}){
 let detail='';
 for(let attempt=0;attempt<attempts;attempt++){
  if(platform==='darwin'){
   const {error,stdout,stderr}=await runFile('/usr/bin/perl',[reader,String(helper.pid),helper.name],{[helper.name]:helper.value}),match=READER_OUTPUT.exec(stdout);
   if(!match)detail=`The reader did not run on the host: ${error?.message||stderr||stdout||'no output'}`;
   else if(match[1]==='found'&&match[2]==='found')return {ok:true,detail:`The host reader sees the marker in the environment of helper process ${helper.pid}`};
   else detail=`The host reader cannot see the marker in the environment of helper process ${helper.pid} (${stdout}); a "hidden" answer inside the sandbox would prove nothing`;
  }else{
   const text=await fs.readFile(`/proc/${helper.pid}/environ`,'utf8').catch(error=>{detail=`The host cannot read /proc/${helper.pid}/environ: ${error.code||error.message}`;return null;});
   if(text!==null){if(text.split('\0').includes(helper.needle))return {ok:true,detail:`The host sees the marker in /proc/${helper.pid}/environ`};detail=`/proc/${helper.pid}/environ has no marker`;}
  }
  await sleep(delay);
 }
 return {ok:false,detail};
}

function runALA(args,env,cwd,timeout){
 return new Promise(resolve=>{
  const child=spawn(process.execPath,args,{cwd,env,stdio:['ignore','pipe','pipe'],detached:true});
  let stderr='',stdout='',timedOut=false;
  const timer=setTimeout(()=>{timedOut=true;try{process.kill(-child.pid,'SIGKILL');}catch{}},timeout);
  child.stdout.on('data',b=>{stdout=(stdout+b).slice(-20000);});
  child.stderr.on('data',b=>{stderr=(stderr+b).slice(-60000);});
  child.on('error',error=>{clearTimeout(timer);resolve({code:null,signal:null,stderr:error.message,stdout,timedOut});});
  child.on('close',(code,signal)=>{clearTimeout(timer);resolve({code,signal,stderr,stdout,timedOut});});
 });
}

async function exists(file){try{await fs.lstat(file);return true;}catch{return false;}}


const markerFound=async(home,marker)=>(await fs.readFile(path.join(home,marker.name),'utf8').catch(()=>null))===marker.value+'\n';
const INSTRUCTIONS='# Sandbox check\n\nModel-free ALA sandbox check. The simulated Codex ignores this text and never contacts a model.\n';
const USAGE='Usage: node tools/check-ala-sandbox.mjs [--mode own|folder]';

// `homeDir` (the real home), `helperOptions` and `log` are inputs so that tests never read the real home or start a real
// helper. `mode` is `own` or `folder`; without it the configured mode is checked, read with readAgentHome at `homeDir`
// (AUDIT_AGENT_HOME is ignored). The setting is also read when `mode` is given, for the report's "Configured mode" line
// and the set folder that ALA must not be inside; an unreadable setting then only leaves the configured mode unknown.
// own: ALA runs without --home (the server's arguments with home null), HOME and every agent state variable pointing
// to a fixture home that holds only .codex/auth.json with {}, and the row agent_state_shared measures that the agent's
// state folder inside is the fixture's. folder: ALA runs with --home on an empty temporary folder, plus the positive
// control of real_agent_state_denied. Neither mode reads or writes the real agent state or login.
export async function main({root,timeout=120000,helperOptions={},homeDir=os.homedir(),log=console.log,mode}={}){
 root??=await fs.realpath(path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'));
 if(mode!==undefined&&!MODES.includes(mode)){console.error(USAGE);return 2;}
 let setting=null,settingError=null;
 try{setting=readAgentHome({root,home:homeDir});}catch(error){settingError=error;}
 if(mode===undefined){if(settingError){console.error(settingError.message);return 2;}mode=setting.mode;}
 const own=mode==='own',configured=setting?.mode??null;
 // ALA is located as the server does, and never inside tmp/ or the set agent-home folder, because this check imports
 // ALA's modules unconfined. ALA_CLI is the operator's own statement.
 const ala=resolveALA(root,{refused:[path.join(root,'tmp'),setting?.folder].filter(Boolean)});
 if(!ala.found){console.error('ALA was not found. Checked: '+ala.checked.map(c=>c.location+' ('+c.reason+')').join(', ')+'. Run "npm install" in this repository, or set ALA_CLI to the AdvancedLanguageAgent checkout.');return 2;}
 let logins;try{logins=await loadALALogins(ala.entry);}catch(error){console.error(error.message);return 2;}
 let python=null,pythonError=null,lean=null,leanError=null;
 try{python=resolvePython();}catch(error){pythonError=error.message;}
 try{lean=leanExecutable(root);}catch(error){leanError=error.message;}
 const id=new Date().toISOString().replace(/[:.]/g,'-')+'-'+crypto.randomBytes(3).toString('hex');
 const check=path.join(root,'tmp/ala-sandbox-check',id),run=path.join(check,'run'),bin=path.join(check,'bin'),reader=path.join(bin,'read-environment.pl');
 await fs.mkdir(run,{recursive:true});await fs.mkdir(bin);
 await fs.writeFile(reader,READER_PERL);
 const codex=path.join(bin,'codex');
 const sentinel=await makeSentinel(root,{home:homeDir}),homeNames=async()=>(await fs.readdir(homeDir).catch(()=>[])).sort(),homeBefore=await homeNames();
 let temporary=null;try{temporary=await makeTemporaryBase(sentinel);}catch(error){removeSentinel(sentinel);throw error;}
 let helper=null,cleaned=null;
 // The helper must not outlive this process, whatever the exit path; the sentinel and the temporary base go with it.
 const stopNow=()=>{try{helper?.child.kill('SIGKILL');}catch{}removeSentinel(sentinel);removeTemporaryBase(temporary);};
 const signals={SIGINT:130,SIGTERM:143,SIGHUP:129},handlers=Object.entries(signals).map(([signal,code])=>[signal,()=>{stopNow();process.exit(code);}]);
 process.once('exit',stopNow);for(const [signal,handler] of handlers)process.once(signal,handler);
 const cleanup=async()=>cleaned??={helperStopped:helper?await helper.stop():null,sentinelRemoved:removeSentinel(sentinel),baseRemoved:removeTemporaryBase(temporary)};
 const clean=withoutAgentState(withoutCredentials(process.env),logins);
 const marker={name:'.ala-sandbox-check-marker-'+crypto.randomBytes(4).toString('hex'),value:crypto.randomBytes(12).toString('hex')};
 // One probe run through ALA. `home` is the --home folder (null in own mode, whose fixture home `homeFiles` is
 // {home, files}). Returns the evaluated rows of this mode and ALA's result.
 async function probeRun({name,runDir,codexFile,state,home,homeFiles=null,env,args}){
  await fs.mkdir(runDir,{recursive:true});
  await fs.writeFile(codexFile,probeScript({run:runDir,root,check,id:name,python:python?.path,lean,sentinel,home:homeEntry,helper,reader,state,marker,mode}),{mode:0o755});
  await fs.chmod(codexFile,0o755); // a restrictive umask must not clear the execute bit
  await fs.writeFile(path.join(runDir,'instructions.md'),INSTRUCTIONS);
  await assertModelFree({env,codex:codexFile,home:home||homeFiles.home,homeFiles:homeFiles?.files||null,homeEnvironment:home?logins.homeEnvironment:null,discover:alaDiscovery(ala.entry)});
  const result=await runALA(args,env,runDir,timeout);
  const checks=evaluate(await fs.readFile(path.join(runDir,'probe-results.tsv'),'utf8').catch(()=>''),{mode,pythonError,pythonWarning:python?.warning,leanError});
  const finalMessage=await fs.readFile(path.join(runDir,'final-message.md'),'utf8').catch(()=>'');
  const diagnostics=result.stderr.split('\n').filter(line=>line.trim()&&!line.startsWith(ALA_EVENT_PREFIX));
  return {result,checks,byKey:Object.fromEntries(checks.map(c=>[c.key,c])),finalMessage,diagnostics,args};
 }
 const alaLineOf=({result,finalMessage,diagnostics})=>result.timedOut?`ALA did not finish within ${timeout/1000} seconds and was stopped.`:result.code===0?(finalMessage.includes(PROBE_MESSAGE)?'ALA exited with code 0 and returned the simulated final message.':'ALA exited with code 0 but did not return the simulated final message.'):`ALA exited with code ${result.code}${result.signal?' (signal '+result.signal+')':''}: ${[...diagnostics].reverse().find(l=>l.startsWith('ala:'))||diagnostics.at(-1)||'no diagnostic'}`;
 let homeEntry;
 try{
  helper=await startHelper(helperOptions);
  // home_private is measured against the real home in both modes (the fixture home has no eligible entry).
  homeEntry=await pickHomeEntry({home:homeDir,avoid:[root,check,ala.entry,path.dirname(path.dirname(ala.entry)),process.execPath,python?.path,lean],above:[os.tmpdir()]});
  let state,args,env,description,homeFiles=null;
  if(own){
   homeFiles={home:temporary.ownHome,files:await makeCodexFixture(temporary.ownHome)};
   state=await agentStateFolders({logins,home:temporary.ownHome,probeHome:temporary.neverCreated});
   // The server's own arguments with home null (no --home); every agent state variable points into the fixture home.
   args=alaArguments({entry:ala.entry,selection:{agent:'codex'},cwd:run,home:null,root,taskFile:path.join(run,'instructions.md'),output:path.join(run,'final-message.md')});
   env=alaEnvironment({...logins.homeEnvironment(clean,temporary.ownHome),CODEX_BIN:codex});
   description=`CODEX_BIN=${codex} (the simulated Codex)  no --home, HOME=${temporary.ownHome} (the fixture home: only .codex/auth.json with {})`;
  }else{
   state=await agentStateFolders({logins,home:homeDir,probeHome:temporary.neverCreated});
   args=alaArguments({entry:ala.entry,selection:{agent:'codex'},cwd:run,home:temporary.checkHome,root,taskFile:path.join(run,'instructions.md'),output:path.join(run,'final-message.md')});
   // The simulated Codex replaces the real binary. ALA derives every agent state variable from the empty --home.
   env=alaEnvironment({...clean,CODEX_BIN:codex});
   description=`CODEX_BIN=${codex} (the simulated Codex)  --home ${temporary.checkHome} (empty)`;
  }
  log(`ALA: ${ala.entry}\nCheck folder: ${check}\nMode checked: ${MODE_NAMES[mode]}\n${description}\nHost helper: process ${helper.pid}, environment variable ${helper.name}\n${own?`Fixture agent state folders: ${state.folders.map(f=>f.path).join(', ')||'none'}`:`Real agent state folders checked: ${state.folders.map(f=>f.path).join(', ')||'none'}`}\nRunning the simulated Codex through ALA…`);
  const before=await confirmOnHost({helper,reader});
  const main=await probeRun({name:id,runDir:run,codexFile:codex,state,home:own?null:temporary.checkHome,homeFiles,env,args});
  const after=await confirmOnHost({helper,reader,attempts:1});
  const {checks,byKey}=main;
  // Host-side confirmations, in both modes: the run-folder file must reach the host and no refused write may exist.
  if(byKey.run_write.status==='pass'&&!await exists(path.join(run,'write-probe.txt')))failCheck(byKey.run_write,'The write succeeded inside the sandbox but the file is missing on the host.');
  for(const [key,file] of Object.entries(deniedProbes({root,check,id})))if(await exists(file)){failCheck(byKey[key],'The probe file exists on the host: '+file);await fs.rm(file,{force:true});}
  // The refusals above only mean something if the host can read the sentinel, the home entry and the helper's marker.
  if(await fs.readFile(sentinel.file,'utf8').catch(()=>null)!==sentinel.marker)failCheck(byKey.outside_read_denied,'The host can no longer read the sentinel '+sentinel.file+', so a refusal inside proves nothing.');
  if(homeEntry.entry&&await fs.access(path.join(homeEntry.real,homeEntry.entry),fs.constants.R_OK).then(()=>false,()=>true))failCheck(byKey.home_private,'The host cannot read '+path.join(homeEntry.real,homeEntry.entry)+', so a refusal inside proves nothing.');
  for(const confirmation of [before,after])if(!confirmation.ok)failCheck(byKey.other_process_environment,confirmation.detail);
  let control=null,controlArgs=null,controlSection='';
  if(own){
   // The marker the probe wrote into $HOME/.codex must be in the fixture's folder.
   combineShared(byKey.agent_state_shared,{marker:await markerFound(path.join(temporary.ownHome,'.codex'),marker)});
  }else{
   const mainMarker=await markerFound(temporary.checkHome,marker);
   // Positive control [review 4]: the same probe through ALA with --home and a fixture real home that holds .codex/.
   // It must pass naming the fixture folder, and its marker must reach its own --home folder.
   await makeCodexFixture(temporary.controlReal);
   const controlState=await agentStateFolders({logins,home:temporary.controlReal,probeHome:temporary.neverCreated});
   const controlRun=path.join(check,'control-run'),controlCodex=path.join(bin,'codex-control');
   controlArgs=alaArguments({entry:ala.entry,selection:{agent:'codex'},cwd:controlRun,home:temporary.controlHome,root,taskFile:path.join(controlRun,'instructions.md'),output:path.join(controlRun,'final-message.md')});
   log('Running the positive control of real_agent_state_denied through ALA…');
   control=await probeRun({name:id+'-control',runDir:controlRun,codexFile:controlCodex,state:controlState,home:temporary.controlHome,env:alaEnvironment({...clean,CODEX_BIN:controlCodex}),args:controlArgs});
   for(const file of Object.values(deniedProbes({root,check,id:id+'-control'})))await fs.rm(file,{force:true});
   const controlRow=control.byKey.real_agent_state_denied;
   combineState(byKey.real_agent_state_denied,{hostProblem:stateHostProblem(state),marker:mainMarker,control:{status:controlRow.status,detail:controlRow.detail,marker:await markerFound(temporary.controlHome,marker)}});
   controlSection='\n## Positive control of real_agent_state_denied\n\n'+alaLineOf(control)+' Row: '+controlRow.status+' — '+cell(controlRow.detail)+'\n\n```sh\n'+[process.execPath,...controlArgs].join(' ')+'\n```\n';
  }
  const alaOk=main.result.code===0&&!main.result.timedOut&&main.finalMessage.includes(PROBE_MESSAGE);
  const passed=alaOk&&checks.every(c=>c.ok);
  const done=await cleanup(),homeAfter=await homeNames(),homeChanged=homeAfter.filter(name=>!homeBefore.includes(name)).length+homeBefore.filter(name=>!homeAfter.includes(name)).length;
  const report='# ALA sandbox check\n\n'+modeStatement(mode,configured)+'\n\n'+
   (own?'This model-free check ran a simulated Codex through ALA with the same arguments the audit server uses without a separate agent home, so without `--home`. No model, prompt or network request was involved.':'This model-free check ran a simulated Codex through ALA with the same arguments the audit server uses, including a dedicated `--home`. No model, prompt or network request was involved.')+'\n\n'+
   `**Result: ${passed?'passed':'failed'}.** ${alaLineOf(main)}\n\n| Check | Required | Result | Detail |\n| --- | --- | --- | --- |\n`+
   checks.map(c=>`| ${cell(c.label)} | ${c.required?'Yes':'No'} | ${c.status} | ${cell(c.detail)} |`).join('\n')+
   '\n\n## ALA invocation\n\n```sh\n'+[process.execPath,...args].join(' ')+'\n```\n\nThe environment set `CODEX_BIN` to the simulated Codex (`'+codex+'`) and `ALA_EVENT_STREAM=1`; '+(own?'`--home` was not passed, and `HOME` and the agent state variables pointed to the fixture home (`'+temporary.ownHome+'`, holding only `.codex/auth.json` with `{}`)':'`--home` was an empty folder (`'+temporary.checkHome+'`)')+'; `PLOINKY_*`, `OPENAI_*`, `ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN` and the agent state variables of the caller were removed.\n'+
   controlSection+
   `\n## Host fixtures\n\nA sentinel file in \`${sentinel.dir}\` (removed: ${done.sentinelRemoved?'yes':'NO'}), a helper process ${helper.pid} with the marker variable \`${helper.name}\` (stopped: ${done.helperStopped?'yes':'NO'}), and the temporary base \`${temporary.dir}\` with the fixture homes (removed: ${done.baseRemoved?'yes':'NO'}). The check writes nothing in the real home (${homeDir}); its list of entries was ${homeChanged?`seen to change by ${homeChanged} entries while it ran, which another program must have done`:'unchanged while it ran'}.\n`+
   (main.diagnostics.length?'\n## ALA diagnostics\n\n```text\n'+main.diagnostics.slice(-40).join('\n')+'\n```\n':'');
  await fs.writeFile(path.join(check,'report.md'),report);
  log('\n'+report+`\nReport: ${path.join(check,'report.md')}`);
  return passed&&done.helperStopped&&done.sentinelRemoved&&done.baseRemoved?0:1;
 }finally{
  await cleanup();
  process.removeListener('exit',stopNow);for(const [signal,handler] of handlers)process.removeListener(signal,handler);
 }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(await fs.realpath(process.argv[1])).href){
 const argv=process.argv.slice(2),chosen=argv.length===2&&argv[0]==='--mode'?argv[1]:undefined;
 if(argv.length!==0&&!MODES.includes(chosen)){console.error(USAGE);process.exitCode=2;}
 else process.exitCode=await main({mode:chosen});
}
