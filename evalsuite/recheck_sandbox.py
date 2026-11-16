#!/usr/bin/env python3
"""Fail-closed Lean 4.24.0 executor; phase-2 interface (JSON-compatible values).

What a pass can mean: if an admitted file could run its own code while Lean
compiles it, that code could also change what the target text means, and no
check of the text would notice. So the MEANING of pass rests on the two
admission filters (the product's and strict_findings) refusing every such
file; CONTAINMENT of whatever does run rests on this sandbox. Nothing here
claims more than that split.

run_job(job: dict) -> dict accepts EXACTLY these required fields:
  schema_version: int, exactly 1.
  run_dir: str, absolute NEW directory beneath this checkout's tmp/recheck/.
  runtime: str, absolute runtime directory beneath this checkout's tmp/recheck/,
    built by build_runtime().
  runtime_hash: str, SHA256 of canonical runtime manifest (files AND directories).
  gate: str, absolute independent verifier receipt; absent/stale receipts fail closed.
  sources: list[{module: str, path: str, sha256: str}]. Paths are absolute regular
    single-link .lean files, opened descriptor-relative without following symlinks.
    Module names are conventional ASCII dotted identifiers; Init/Std/Lean reserved.
    Maximum 256 files, 1 MiB each, 16 MiB total. No retained objects are admitted.
  proofs: list[{module: str, theorem: str, target: str, relation: str, claim_id: str}].
    relation is supports|refutes|auxiliary. Existing _proofs validates each target.
    All declared theorems are checked, followed by sorted requested target checks.

Execution: one sandbox invocation per STEP, never a driver inside the sandbox.
Steps, in order: build each module (dependency order), replay each module
through the kernel (trusted REPLAY_PROGRAM), then one check per request. Each
step is its own systemd-run scope (same limits) -> _exec --step -> bwrap ->
/lean/bin/lean. The supervisor reads the step's stdout/stderr through its own
bounded pipes and takes status only from what it observes: exit status, the
accepted object, cgroup files and its own timers. Child bytes are never parsed
as events. Mounts per step: runtime /, staged sources /src, generated checks
/checks, object store /build and trusted program /control, all read-only; a
private 64 MiB tmpfs /scratch; build steps only additionally get /out, a fresh
directory on the host /dev/shm tmpfs whose pages are charged to the step's memory
cgroup (so bounded by MemoryMax), plus RLIMIT_FSIZE per file. After a build step
whose scope is empty, the supervisor accepts exactly one regular single-link
file named <module>.olean (<= 32 MiB), copies it from one descriptor into the
object store, records its hash and deletes the /out directory. Replay steps run
REPLAY_PROGRAM (importModules without extension loading, refusal of
reduceBool/reduceNat, Environment.replay, CollectAxioms) and report by exit code
only. Every step sets LEAN_ABORT_ON_PANIC=1. Before every step and after the last
one the supervisor re-hashes sources, checks, the replay program and every
accepted object; any difference is source_changed. Staged sources and checks are
read-only for the job. One total deadline (120 s) covers all steps; each scope
also has RuntimeMaxSec=120s as an independent backstop.

Result: every field below is always present (including early rejection):
  schema_version: int = 1; outcome: str; reason: str|null; detail: str;
  argv: list[str] and bwrap_argv: list[str] of the FIRST launched step (empty
    before launch); steps: list[{index, kind: build|replay|check, module,
    argv, bwrap_argv, unit, exit_code, signal, seconds, output_offset,
    output_length, output_sha256, object_sha256 (build only), evidence}];
  exit_code: int|null; signal: int|null (last launched step); seconds: float;
  axioms: list[str]; checks: list[{module:str, theorem:str, target:str|null,
    relation:str|null, claim_id:str|null, index:int, axioms:list[str]}];
    null fields identify declaration-only checks. log_paths:list[str] absolute;
    log_hashes:dict[str,str] maps those paths to SHA256 digests.
  truncated: bool; lean_subprocesses: int (sandbox steps started);
  runtime_hash: str|null;
  evidence: dict, empty before preparation; otherwise these optional observed
    fields only (absence means unobserved, NEVER a passing observation), taken
    from the LAST launched step (each step's own copy is in steps[i].evidence):
    bindings:dict = binding_manifest(runtime_hash); gate_hash:str|null;
    event:str="preflight"; cgroup:str (absolute cgroup v2 path);
    limits:dict[str,str], last_cgroup:dict[str,str] (literal kernel file values:
      memory.max/swap.max/current/peak/events, pids.max/current/events,
      cpu.max/stat, cgroup.events; disappearing fields preserve prior samples);
    systemd:dict[str,str] (ControlGroup, RuntimeMaxUSec, TimeoutStopUSec,
      MemoryMax, MemorySwapMax, TasksMax, CPUQuotaPerSecUSec);
    rlimits:{fsize:[int,int], core:[int,int]} (soft,hard); nice:int;
    namespaces:dict[str,str], sandbox_namespaces:dict[str,str] map
      user/ipc/pid/net/uts/cgroup to kernel namespace identity strings;
    cleanup_empty:bool (every step); cleanup_seconds:float (largest);
    cleanup_errors:list[str] on cleanup faults; steps_complete:bool.
  Result outcomes: pass|fail|filter_rejected|timeout|not_linked|not_run.
  Reason enum: input_limit|invalid_metadata|unsafe_path|source_changed|
    lexical_rejection|strict_rejection|dependency_missing|dependency_cycle|
    compile_error|replay_failed|target_mismatch|axioms_disallowed|axioms_missing|
    output_limit|memory_limit|process_limit|scratch_limit|timeout|signal|
    infrastructure_error|interrupted.
  lexical_rejection: the unchanged product filter (audit.inspect_model) refused.
  strict_rejection: the product filter admitted the file, but this tool's own
    stricter admission (strict_findings) refused it. Both are filter_rejected and
    no Lean process starts; count them separately by reason.
  replay_failed: the trusted kernel replay of a module failed (kernel rejection,
    native evaluation, missing/non-theorem name, module-system object, crash).
    axioms_disallowed also results when the replay finds a non-allowed axiom.
  pass has reason null. Infrastructure failures return not_run; callers MUST abort
  their batch on infrastructure_error or interrupted. not_linked is reserved for
  phase 2. scratch_limit covers ENOSPC and the 32 MiB per-file EFBIG limit.
  A pass needs every step to exit 0, every object accepted, every replay clean,
  one exact allowed axiom report per check and unchanged inputs at the end.

The supervisor writes job.json (same job plus private prepared metadata), sources/,
checks/, control/Replay.lean, objects/, steps/NNN/preflight.json, output.log,
wire.log and atomic result.json in run_dir. output.log retains at most 1 MiB
combined child stdout/stderr over all steps. wire.log holds supervisor-written
step records only. The prepared job object has job:dict (the public job),
fixture:str|null (null in production), order:list[str], requests:list[check
fields without index and axioms], plan:list[step dict], unit:str (UUID prefix;
step K uses unit-sKKK), gate_hash:str|null, probe:bool (true only for run_probe).
Call run_job; do not construct prepared jobs. _exec --job --step reads only that
prepared job, revalidates admission/gate/runtime/generated bytes, checks its
*actual* scope and limits, writes its preflight record, sets rlimits, then execs
Bubblewrap. It is not an alternative public bypass. Environment and descriptors
are scrubbed.

Receipt contract (external verifier writes it, never this module): exactly
{schema_version:1, approved:true, bindings:binding_manifest(runtime_hash),
 fixture_observations:{name: true for every FIXTURE_HASHES key}}. Bindings include
code, unchanged filters, fixed fixtures, runtime, kernel, bwrap/systemd and profile.
No receipt is generated here. Fixed-hash fixture testing has a separate private
entry; it cannot accept caller paths, arbitrary source, targets or limits.

Verifier probes (run_probe(job) or `recheck_sandbox.py probe --job FILE`) let an
independent verifier run its OWN hostile Lean through the identical per-step
launch (same systemd-run scopes/properties, Bubblewrap argv, replay and limits)
BEFORE any approving receipt exists. A probe job is a production job plus exactly one
field, probe_root: absolute verifier-owned directory (not a symlink, owned by
this user, not group/other writable, not an ancestor of this checkout). Every
source path must lie strictly inside probe_root and must not contain
"harness-matrix", a validation/runs segment pair or an agent-home segment; all
other staging rules apply unchanged. Modules build in the listed source order;
declaration checks cover exactly the theorems named in proofs. gate is the path
of a probe receipt, written by the verifier, never by this module, exactly:
{schema_version:1, kind:"verifier_probe", bindings:binding_manifest(runtime_hash),
 probe_root:<same string as the job>, probe_sources:[sorted unique SHA256 hex of
 every authorized probe file, 1..256]}.
A listed file skips the product and strict filters, exactly as fixed fixtures do;
an unlisted file is refused (infrastructure_error) before any Lean starts. A probe
receipt never contains `approved` and can never open the production gate
(check_gate refuses it); run_probe refuses any receipt that is not a probe
receipt, including an approving one. A probe result always has outcome
"verifier_probe" (PROBE_OUTCOME, deliberately absent from OUTCOMES) and carries the
underlying outcome in probe_outcome; it can never be counted as a recheck result.
Do not run the probe CLI under an outer `nice`: _exec requires niceness exactly 15.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import resource
import selectors
import shutil
import stat
import subprocess
import sys
import time
import uuid

# _exec is launched without -B by the settled argv contract. Never create
# repository bytecode caches before entering Bubblewrap.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/evidence-to-proof-review/scripts'
sys.path.insert(0, str(SCRIPTS))
audit = importlib.import_module('audit')
markdown_proofs = importlib.import_module('markdown_proofs')
FIXTURES = ROOT / 'tests/fixtures/recheck'
WORK = ROOT / 'tmp/recheck'
LIMIT = 1048576
PROFILE = {'memory': 2147483648, 'swap': 0, 'tasks': 64, 'cpu': '100000 100000',
           'seconds': 120, 'stop_seconds': 5, 'output': LIMIT, 'scratch': 67108864,
           'fsize': 33554432, 'core': 0, 'nice': 15}
REASONS = ('input_limit', 'invalid_metadata', 'unsafe_path', 'source_changed',
           'lexical_rejection', 'strict_rejection', 'dependency_missing', 'dependency_cycle',
           'compile_error', 'replay_failed', 'target_mismatch', 'axioms_disallowed', 'axioms_missing',
           'output_limit', 'memory_limit', 'process_limit', 'scratch_limit',
           'timeout', 'signal', 'infrastructure_error', 'interrupted')
OUTCOMES = ('pass', 'fail', 'filter_rejected', 'timeout', 'not_linked', 'not_run')
# Every run_probe result carries this outcome; it is never a recheck outcome.
PROBE_OUTCOME = 'verifier_probe'
PROBE_KIND = 'verifier_probe'
# Literal hashes, reviewed with the executor. Never load an allowlist from a job.
FIXTURE_HASHES = {
    'control': '55cafced6288c93114c59b951b5b9c3b5dcccd1381b51536caeaade7050d7b45',
    'descendant': '20984c1a2a2a7c47550d819da2fb67783bf9708282bfcad540bfa2aeb7e2de21',
    'false_target': '760f3c068ad080bef80d3acbf35b24b358e685449ec8b1db58e96e0bd2bae844',
    'filesystem': '1918152b3fd9d144c53a631db19e4b57e07f0ab2a87613c61df96b427d2f650b',
    'forbidden_axiom': '7f4b2c2830a3cbcfaba67620e0ccde1d08a2b53e5804333956d96bc30c1c0cf9',
    'forged': 'bdb6611203ad5f8aa77188c25ce04dc36515dd75edd795f0fedab53cace16189',
    'fsize': '6e073b4199c0e2691a0d889df67de8d02af547c8e1231fd19b4c46a490da8638',
    'import_escape': 'c113ffe22e80abe88db12fe85e3e46d52a79004b050f0cf733e8a713ef4b9409',
    'initialize': '7a06bc95ff106a25ec2c8fcdbfe5ef72f526a969dbb9b7aa11e2c20953b1129f',
    'macro': '56406831806f23ac5cc59819071689219c430972f77db0186e961996ac6870e2',
    'memory': '7fde1eb515a291b5ec7e8be04eedc0da0248891c7bd425d2a66e72c6a559ad04',
    'network': '6da6cd90345e6170e741375586ca2bb91b1ac4433c26fdd472965d8bebb3038d',
    'output': 'abf6811b00aeb179980fcc2b36856643bba02cb35acb1909aeef56991f7130fb',
    'passing': '760f3c068ad080bef80d3acbf35b24b358e685449ec8b1db58e96e0bd2bae844',
    'scratch': 'c4163f50d4842b738f34e7c01c700b9a2c18bbc86279941466563ec67e4d9f44',
    'sleep': '2ffcf87794533252ddc9727e7b59b2213572f06231e3d41454f155e45ddc29e6',
    'sorry': '123ca067bb7f2c843117216d2f65d92463bb2c8415ce33680d98d5f72758a443',
    'tasks': '17acd6b9e36ccd498b719812d7be2367cc99377264e50c808627ee972cf3cf3e',
}
MODULE = r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*'
MOUNT_POINTS = ('src', 'checks', 'control', 'build', 'out', 'proc', 'dev', 'scratch')
SHM = Path('/dev/shm')
OBJECT_LIMIT = 33554432
# Replay exit codes (trusted program; the supervisor reads ONLY the exit status).
REPLAY_EXIT = {3: 'kernel replay rejected', 4: 'non-allowed axiom', 5: 'native evaluation or module-system object',
               6: 'named theorem missing or not a theorem of this module', 7: 'object missing',
               8: 'Lean library version is not 4.24.0',
               9: 'duplicate, mismatched or missing constant names in the object'}
# Trusted kernel replay, written by the supervisor to control/Replay.lean and run
# with `lean --run`. It never loads environment extensions (no interpreter use for
# imported data), refuses kernel-evaluated native code before replaying, replays
# every constant of the module through the kernel over its imports, and checks
# the axioms of every named theorem in the REPLAYED environment.
REPLAY_PROGRAM = """import Lean.Replay
import Lean.Util.CollectAxioms
open Lean

def allowedAxioms : List Name := [``propext, ``Classical.choice, ``Quot.sound]
def nativeConstants : List Name := [``Lean.reduceBool, ``Lean.reduceNat, ``Lean.ofReduceBool, ``Lean.ofReduceNat]

def main (args : List String) : IO UInt32 := do
  if Lean.versionString != "4.24.0" then return 8
  let some modName := args.head? | return 2
  initSearchPath ⟨"/lean"⟩ [⟨"/build"⟩]
  let file ← findOLean modName.toName
  unless (← file.pathExists) do return 7
  let (data, _) ← readModuleData file
  if data.isModule then return 5
  for ci in data.constants do
    let used := ci.getUsedConstantsAsSet
    if nativeConstants.any used.contains then
      IO.eprintln s!"REPLAY_NATIVE {ci.name}"
      return 5
  let env ← importModules data.imports {}
  -- Every constant exactly once, under its own name; a duplicate would let a
  -- later entry shadow an earlier one in the map below.
  if data.constNames.size != data.constants.size then return 9
  let mut newConstants : Std.HashMap Name ConstantInfo := {}
  for name in data.constNames, ci in data.constants do
    if newConstants.contains name || ci.name != name then return 9
    newConstants := newConstants.insert name ci
  match ← (env.replay newConstants).toBaseIO with
  | .error e =>
    IO.eprintln s!"REPLAY_FAILED {e}"
    return 3
  | .ok replayed =>
    for thm in args.tail do
      let n := thm.toName
      unless newConstants.contains n do return 6
      match replayed.find? n with
      | some (.thmInfo _) =>
        let (_, s) := ((CollectAxioms.collect n).run replayed).run {}
        IO.println s!"REPLAY_AXIOMS {n} {s.axioms.toList}"
        if s.axioms.any (fun a => !allowedAxioms.contains a) then return 4
      | _ => return 6
    IO.println "REPLAY_OK"
    return 0
"""
THEOREM = r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*"
ALLOWED_AXIOMS = {'propext', 'Classical.choice', 'Quot.sound'}


class Rejection(Exception):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path, limit=LIMIT):
    """Absolute descriptor-relative traversal; rejects links, devices and races."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Rejection('unsafe_path', f'Not a confined absolute path: {path}')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    leaf = None
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        before = os.fstat(leaf)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Rejection('unsafe_path', f'Not a single-link regular file: {path}')
        if before.st_size > limit:
            raise Rejection('input_limit', f'Oversize file: {path}')
        parts, size = [], 0
        while True:
            block = os.read(leaf, min(65536, limit + 1 - size))
            if not block:
                break
            size += len(block)
            if size > limit:
                raise Rejection('input_limit', f'Growing/oversize file: {path}')
            parts.append(block)
        after = os.fstat(leaf)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after):
            raise Rejection('source_changed', f'Changed while reading: {path}')
        return b''.join(parts)
    except OSError as exc:
        raise Rejection('unsafe_path', str(exc)) from exc
    finally:
        os.close(fd)
        if leaf is not None:
            os.close(leaf)


def parse_json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise Rejection('invalid_metadata', 'Duplicate JSON field')
            value[key] = item
        return value
    try:
        value = json.loads(data, object_pairs_hook=pairs)
        def check(v, depth=0):
            if depth > 32:
                raise Rejection('input_limit', 'JSON nesting exceeds 32')
            if isinstance(v, dict):
                for x in v.values(): check(x, depth + 1)
            if isinstance(v, list):
                for x in v: check(x, depth + 1)
        check(value)
        return value
    except (ValueError, RecursionError) as exc:
        raise Rejection('invalid_metadata', str(exc)) from exc


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for data in iter(lambda: stream.read(1048576), b''):
            h.update(data)
    return h.hexdigest()


def runtime_manifest(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise Rejection('infrastructure_error', 'Invalid runtime root')
    result = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            result[rel] = {'directory': True}
        elif stat.S_ISREG(mode) and path.stat().st_nlink == 1:
            result[rel] = {'sha256': file_hash(path), 'mode': stat.S_IMODE(mode)}
        else:
            raise Rejection('infrastructure_error', f'Runtime link/special file: {rel}')
    return result


def build_runtime(lean, destination):
    """Copy pinned trusted toolchain, resolving links; include only ELF closure.

    This is a preparation utility, not permission to execute supplied Lean. The
    input must be a separately trusted Lean installation. No compiler/Lake/shell
    executables are copied. Manifest lives BESIDE the read-only runtime root.
    """
    lean, destination = Path(lean).resolve(strict=True), Path(destination)
    if destination.exists():
        raise Rejection('infrastructure_error', 'Runtime destination must be new')
    if lean.name != 'lean':
        raise Rejection('infrastructure_error', 'Expected trusted Lean executable')
    toolchain = lean.parent.parent
    destination.mkdir(parents=True)
    copied = set()
    def copy(src, target):
        resolved = src.resolve(strict=True)
        if target in copied: return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved, target)
        target.chmod(0o555 if os.access(resolved, os.X_OK) else 0o444)
        copied.add(target)
    copy(lean, destination / 'lean/bin/lean')
    lib = toolchain / 'lib/lean'
    for src in sorted(lib.rglob('*')):
        if src.is_file() and (src.suffix in {'.olean', '.ilean', '.ir', '.private', '.server', '.so'} or '.so.' in src.name):
            if not src.resolve().is_relative_to(toolchain):
                raise Rejection('infrastructure_error', 'Toolchain link escapes installation')
            copy(src, destination / 'lean/lib/lean' / src.relative_to(lib))
    # ldd is used only on the trusted installation's ELF binaries, never inputs.
    pending = [lean] + [p for p in lib.rglob('*.so') if p.is_file()]
    visited = set()
    while pending:
        src = pending.pop()
        if src.resolve() in visited: continue
        visited.add(src.resolve())
        out = subprocess.run(['/usr/bin/ldd', str(src)], capture_output=True, text=True,
                             check=True, env={'PATH': '/usr/bin:/bin'}).stdout
        if 'not found' in out:
            raise Rejection('infrastructure_error', f'Unresolved ELF dependency: {src}')
        for name in re.findall(r'(?:=>\s*)?(/[^\s()]+)', out):
            dep = Path(name)
            if dep.resolve().is_relative_to(toolchain):
                target = destination / 'lean' / dep.resolve().relative_to(toolchain)
            else:
                target = destination / dep.relative_to('/')
            copy(dep, target)
            pending.append(dep)
    # Empty mount points only; the runtime root itself stays read-only.
    for directory in MOUNT_POINTS:
        (destination / directory).mkdir(exist_ok=True)
    for directory in sorted(destination.rglob('*'), reverse=True):
        if directory.is_dir(): directory.chmod(0o555)
    destination.chmod(0o555)
    manifest = runtime_manifest(destination)
    digest = sha(canonical(manifest))
    destination.with_suffix('.manifest.json').write_bytes(canonical(manifest))
    return digest


def check_mount_points(runtime):
    """An older runtime without every mount point would make each step fail as a
    build error and let a batch continue; refuse it as infrastructure instead."""
    missing = [d for d in MOUNT_POINTS
               if not os.path.lexists(Path(runtime) / d) or not stat.S_ISDIR(os.lstat(Path(runtime) / d).st_mode)]
    if missing:
        raise Rejection('infrastructure_error', f'Runtime lacks mount points {missing}; rebuild it with build_runtime()')


def binding_manifest(runtime_hash):
    # REPLAY_PROGRAM is a constant of this module, so the module hash binds it.
    paths = [Path(__file__), SCRIPTS / 'audit.py', SCRIPTS / 'markdown_proofs.py',
             ROOT / 'tests/test_evalsuite_recheck_sandbox.py']
    return {'code': {str(p.relative_to(ROOT)): file_hash(p) for p in paths},
            'fixtures': FIXTURE_HASHES, 'runtime_hash': runtime_hash,
            'kernel': {'release': os.uname().release, 'version_hash': file_hash('/proc/version')},
            'profile': PROFILE, 'profile_hash': sha(canonical(PROFILE)),
            'bwrap_hash': file_hash('/usr/bin/bwrap'),
            'systemd_run_hash': file_hash('/usr/bin/systemd-run')}


def check_gate(path, runtime_hash):
    try:
        receipt = parse_json(read_regular(path))
    except Rejection as exc:
        raise Rejection('infrastructure_error', 'Missing/invalid verifier receipt: ' + str(exc)) from exc
    if not isinstance(receipt, dict) or 'kind' in receipt or 'probe_sources' in receipt:
        raise Rejection('infrastructure_error', 'A verifier probe receipt can never open the production gate')
    if (set(receipt) != {'schema_version', 'approved', 'bindings', 'fixture_observations'}
        or type(receipt['schema_version']) is not int or receipt['schema_version'] != 1 or receipt['approved'] is not True
        or receipt['bindings'] != binding_manifest(runtime_hash)
        or receipt['fixture_observations'] != {name: True for name in FIXTURE_HASHES}):
        raise Rejection('infrastructure_error', 'Missing, invalid or stale verifier receipt')
    return sha(canonical(receipt))


def check_probe_gate(path, runtime_hash, probe_root):
    """Probe-only authorization. Returns (receipt hash, authorized source hashes).

    Never accepts an approving receipt; never usable by check_gate/run_job.
    """
    try:
        receipt = parse_json(read_regular(path))
    except Rejection as exc:
        raise Rejection('infrastructure_error', 'Missing/invalid verifier probe receipt: ' + str(exc)) from exc
    if not isinstance(receipt, dict) or 'approved' in receipt or 'fixture_observations' in receipt:
        raise Rejection('infrastructure_error', 'An approving receipt can never authorize a verifier probe')
    sources = receipt.get('probe_sources')
    if (set(receipt) != {'schema_version', 'kind', 'bindings', 'probe_root', 'probe_sources'}
        or type(receipt['schema_version']) is not int or receipt['schema_version'] != 1
        or receipt['kind'] != PROBE_KIND or receipt['probe_root'] != probe_root
        or not isinstance(sources, list) or not 1 <= len(sources) <= 256
        or not all(isinstance(h, str) and re.fullmatch('[a-f0-9]{64}', h) for h in sources)
        or sources != sorted(set(sources))
        or receipt['bindings'] != binding_manifest(runtime_hash)):
        raise Rejection('infrastructure_error', 'Missing, invalid or stale verifier probe receipt')
    return sha(canonical(receipt)), frozenset(sources)


def probe_path_refusal(path):
    """Lexical refusal for probe paths: campaign trees, validation runs, agent homes."""
    parts = Path(path).parts
    if 'harness-matrix' in str(path):
        return 'campaign tree (harness-matrix)'
    if any(parts[i:i + 2] == ('validation', 'runs') for i in range(len(parts))):
        return 'validation/runs'
    if 'agent-home' in parts:
        return 'agent home'
    return None


def check_probe_root(root):
    """Filesystem checks for the verifier-owned probe folder (preparation time)."""
    try:
        info = os.lstat(root)
    except OSError as exc:
        raise Rejection('unsafe_path', 'Probe folder unavailable: ' + str(exc)) from exc
    if (not stat.S_ISDIR(info.st_mode) or os.path.realpath(root) != str(root)
        or info.st_uid != os.getuid() or info.st_mode & 0o022):
        raise Rejection('unsafe_path', 'Probe folder must be a real directory owned by this user, not group/other writable')


# Strict admission of this tool's own, applied in production AFTER the unchanged
# product filter and before any Lean process. Lexical, not a Lean parser: it
# fails closed on anything it cannot classify. Allow-lists, where possible:
# Attributes: EMPTY. Every attribute is an extension point (term_elab,
#   command_elab, init, tactic, app_unexpander, simp on simprocs, instance,
#   default_instance, csimp, export ...); none occurs in the retained models.
STRICT_ATTRIBUTES = frozenset()
# Built-in deriving handlers, and ONLY as a `deriving` clause of an inductive or
# structure declared in the same file (`deriving instance ... for` is refused):
# a derived instance is generated for the new type and cannot redefine one of
# an existing type.
STRICT_DERIVING = frozenset({'DecidableEq', 'Repr', 'BEq', 'Inhabited', 'Hashable', 'Ord'})
# Hand-written instances: proof-carrying classes only. A Decidable* instance
# carries a proof, Nonempty only asserts existence, so neither can change what a
# statement means. Value-carrying classes (Inhabited, Repr, ToString, BEq, OfNat,
# Coe*, Add ...) could change default, xs[i]!, toString, ==, numerals or
# operators in a target. Any instance option such as (priority := ...) is refused.
STRICT_INSTANCE_CLASSES = frozenset({'Decidable', 'DecidableEq', 'DecidablePred', 'DecidableRel', 'Nonempty'})
_SHADOWED = STRICT_INSTANCE_CLASSES | STRICT_DERIVING | {'ToString'}
# Identifier components refused anywhere (exact component match, so names that
# merely contain a word, such as macroeconomic or prefixLength, are not refused).
STRICT_WORDS = (
    ('notation', r'notation\d*|infix[lr]?|prefix|postfix|mixfix|binder_\w+'),
    ('metaprogramming', r'initialize|init_quot|prelude|unif_hint|run_(?:cmd|elab|meta|tac)'
                        r'|(?:builtin|register|declare)_\w+'
                        r'|(?:\w+_)?(?:macro|syntax|elab|simproc|dsimproc|unexpander|delab|delaborator)(?:_\w+)?'),
    ('unsafe_or_partial', r'unsafe|partial|implemented_by|extern'),
    # Kernel/elaborator evaluation of compiled code (decide +native, reduceBool).
    ('native_evaluation', r'native|reduceBool|reduceNat|ofReduceBool|ofReduceNat|trustCompiler'),
    ('class_or_alias', r'class|export|renaming'),
    ('numeral_or_coercion', r'Coe(?:[A-Z]\w*)?|OfNat|OfScientific|NatCast|IntCast'),
)
IDENT = r"[^\W\d][\w'!?]*(?:\.[^\W\d][\w'!?]*)*"
_OPENER_PREVIOUS = set(' \t\r\n([{,⟨')
# Lean starts a comment only between tokens. A `--` or `/-` glued to a preceding
# character may be part of a token (`//-`, `\/-`, `<--`), which both maskers would
# misread as a comment; such openers are refused.
_COMMENT_PREVIOUS = set(' \t\r\n')
_DECLARATION = r"(?<![\w'!?.])(?:def|abbrev|theorem|lemma|structure|inductive|instance|opaque|axiom|class)\s+(" + IDENT + ')'


def _strict_mask(text):
    """Mask comments and literals, refusing every literal opener the product
    lexer and Lean might read differently (escaped identifiers, a quote or raw
    string after an identifier/dot/bang, an apostrophe that is neither an
    identifier prime nor a plain character literal)."""
    if '«' in text or '»' in text:
        raise Rejection('strict_rejection', 'escaped_identifier: guillemets are not admitted')
    out, i, depth, n = [], 0, 0, len(text)
    def masked(value):
        return ''.join('\n' if c == '\n' else ' ' for c in value)
    def opener_ok(at):
        return at == 0 or text[at - 1] in _OPENER_PREVIOUS
    while i < n:
        c = text[i]
        if depth:
            if text.startswith('/-', i):
                depth += 1; out.append('  '); i += 2
            elif text.startswith('-/', i):
                depth -= 1; out.append('  '); i += 2
            else:
                out.append('\n' if c == '\n' else ' '); i += 1
            continue
        if text.startswith('/-', i) or text.startswith('--', i):
            if i and text[i - 1] not in _COMMENT_PREVIOUS:
                raise Rejection('strict_rejection', f'comment_opener: comment opener glued to a token at offset {i}')
        if text.startswith('/-', i):
            # /-- and /-! are three-character openers: /--/ is still an open comment.
            width = 3 if text[i + 2:i + 3] in ('-', '!') else 2
            depth = 1; out.append(' ' * width); i += width; continue
        if text.startswith('--', i):
            end = text.find('\n', i)
            end = n if end < 0 else end
            out.append(masked(text[i:end])); i = end; continue
        raw = re.match(r'r(#*)"', text[i:]) if c == 'r' and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_'!?.")) else None
        if c == '"' or raw:
            if not opener_ok(i):
                raise Rejection('strict_rejection', f'literal_opener: ambiguous string opener at offset {i}')
            start = i
            if raw:
                closing = '"' + raw.group(1)
                end = text.find(closing, i + len(raw.group()))
                if end < 0: raise Rejection('strict_rejection', 'literal_opener: unclosed raw string')
                i = end + len(closing)
            else:
                i += 1
                while i < n and text[i] != '"':
                    i += 2 if text[i] == '\\' else 1
                if i >= n: raise Rejection('strict_rejection', 'literal_opener: unclosed string')
                i += 1
            # Interpolating macros (s!, m!, f!, dbg_trace, throwError, panic!,
            # trace[..] ...) elaborate code inside {...} while the maskers see a
            # plain string; refused for every string literal without enumerating
            # the macros. Raw strings too: whether no macro accepts one is not
            # verified here, so a brace in a raw string also fails closed.
            if '{' in text[start:i]:
                raise Rejection('strict_rejection', f'interpolation: brace inside a string literal at offset {start}')
            out.append(masked(text[start:i])); continue
        if c == "'":
            if i and (text[i - 1].isalnum() or text[i - 1] in "_'!?"):
                out.append(c); i += 1; continue   # identifier prime such as h'
            char = re.match(r"'(?:\\(?:u[0-9A-Fa-f]{4}|x[0-9A-Fa-f]{2}|.)|[^'\\\n])'", text[i:])
            if not opener_ok(i) or not char:
                raise Rejection('strict_rejection', f'literal_opener: ambiguous apostrophe at offset {i}')
            out.append(masked(char.group())); i += len(char.group()); continue
        out.append(c); i += 1
    if depth:
        raise Rejection('strict_rejection', 'literal_opener: unclosed comment')
    code = ''.join(out)
    try:
        product = audit.lean_code(text)
    except (audit.AuditError, UnicodeError) as exc:
        raise Rejection('strict_rejection', 'mask_divergence: product lexer refused: ' + str(exc)) from exc
    if product != code:
        raise Rejection('strict_rejection', 'mask_divergence: product lexer masks this file differently')
    return code


def _instance_head(code, start):
    """Class head after the first top-level ':' of an instance header, else None."""
    depth, i = 0, start
    while i < len(code) and i - start < 4096:
        c = code[i]
        if c in '([{⦃⟨':
            depth += 1
        elif c in ')]}⦄⟩':
            depth -= 1
            if depth < 0: return None
        elif depth == 0:
            if code.startswith(':=', i) or c == '|' or re.match(r"where(?![\w'!?])", code[i:]) and not (code[i - 1].isalnum() or code[i - 1] in "_'!?."):
                return None
            if c == ':':
                head = re.match(r'\s*(' + IDENT + ')', code[i + 1:])
                return head.group(1) if head else None
        i += 1
    return None


def strict_findings(text):
    """Every strict-admission violation as (category, token, line), in order."""
    try:
        code = _strict_mask(text)
    except Rejection as exc:
        category, _, detail = str(exc).partition(': ')
        return [(category, detail, 0)]
    line = lambda at: code.count('\n', 0, at) + 1
    found = []
    for attr in re.finditer(r'@\s*\[([^\]]*)\]?', code):
        items = [re.sub(r'^(?:local|scoped)\s+|^-\s*', '', item.strip()) for item in attr.group(1).split(',')]
        names = [re.match(IDENT, item) for item in items]
        if not attr.group(0).endswith(']') or '[' in attr.group(1) or any(not m or m.group() not in STRICT_ATTRIBUTES for m in names):
            found.append(('attribute', re.sub(r'\s+', ' ', attr.group(0))[:60], line(attr.start())))
    for decl in re.finditer(_DECLARATION, code):
        if re.search(r"(?<![\w'!?.])deriving\s*$", code[max(0, decl.start() - 64):decl.start()]):
            continue   # deriving instance H for T names a handler, not a declaration
        if decl.group(1).split('.')[-1] in _SHADOWED:
            found.append(('shadowing', decl.group(1), line(decl.start())))
    previous = None
    for token in re.finditer(IDENT, code):
        name, parts = token.group(), token.group().split('.')
        for part in parts:
            for category, pattern in STRICT_WORDS:
                if re.fullmatch(pattern, part):
                    found.append((category, name, line(token.start())))
            if part == 'attribute':
                found.append(('attribute', name, line(token.start())))
        if len(parts) > 1 and {'instance', 'deriving'} & set(parts):
            found.append(('unparsed_declaration', name, line(token.start())))
        if parts[-1] in _SHADOWED:
            before = code[max(0, token.start() - 256):token.start()].rstrip()
            after = code[token.end():token.end() + 16]
            if before.endswith('|') or (re.match(r'\s*:(?![=:])', after) and not re.search(r"(?<![\w'!?.])(?:instance|deriving)\s*$", before)):
                found.append(('shadowing', name, line(token.start())))
        if name == 'deriving':
            if re.match(r'\s+instance(?![\w\'!?.])', code[token.end():]):
                found.append(('deriving', 'deriving instance', line(token.start())))
            handlers = re.match(r'\s+(?:instance\s+)?(' + IDENT + r'(?:\s*,\s*' + IDENT + r')*)', code[token.end():])
            listed = re.split(r'\s*,\s*', handlers.group(1)) if handlers else []
            if not listed or any(h not in STRICT_DERIVING for h in listed):
                found.append(('deriving', handlers.group(1) if handlers else name, line(token.start())))
        if name == 'instance' and not (previous is not None and previous.group() == 'deriving'
                                       and not code[previous.end():token.start()].strip()):
            if re.match(r'\s*\(\s*' + IDENT + r'\s*:=', code[token.end():]):
                found.append(('instance', 'instance option', line(token.start())))
            head = _instance_head(code, token.end())
            if head not in STRICT_INSTANCE_CLASSES:
                found.append(('instance', head or 'unparsed header', line(token.start())))
        previous = token
    return sorted(set(found), key=lambda f: (f[2], f[0], f[1]))


def strict_admission(path):
    """Refuse (strict_rejection) a staged model the product filter admitted."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        raise Rejection('strict_rejection', f'{Path(path).name}: unreadable: {exc}') from exc
    found = strict_findings(text)
    if found:
        category, token, line = found[0]
        raise Rejection('strict_rejection', f'{Path(path).name}:{line}: {category} {token!r} '
                        f'({len(found)} strict finding(s))')


def systemd_argv(unit, job_path, step):
    return ['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '--expand-environment=no',
            '--unit=' + unit, '--property=MemoryMax=2147483648', '--property=MemorySwapMax=0',
            '--property=TasksMax=64', '--property=CPUQuota=100%', '--property=RuntimeMaxSec=120s',
            '--property=TimeoutStopSec=5s', '/usr/bin/nice', '-n', '15', '/usr/bin/python3',
            str(Path(__file__).resolve()), '_exec', '--job', str(job_path), '--step', str(step)]


def step_unit(unit, index):
    return f'{unit}-s{index:03d}'


def shm_parent():
    """Per-checkout parent on the host /dev/shm tmpfs; swept under the executor lock."""
    return SHM / ('lean-recheck-' + sha(str(WORK.resolve()).encode())[:16])


def out_dir(unit, index):
    return shm_parent() / unit / f's{index:03d}'


def bwrap_argv(runtime, run_dir, step, out=None):
    run_dir = Path(run_dir)
    argv = ['/usr/bin/bwrap', '--unshare-user', '--unshare-ipc', '--unshare-pid', '--unshare-net',
            '--unshare-uts', '--unshare-cgroup', '--disable-userns', '--assert-userns-disabled',
            '--cap-drop', 'ALL', '--die-with-parent', '--new-session', '--clearenv',
            '--ro-bind', str(runtime), '/', '--ro-bind', str(run_dir / 'sources'), '/src',
            '--ro-bind', str(run_dir / 'checks'), '/checks', '--ro-bind', str(run_dir / 'objects'), '/build',
            '--ro-bind', str(run_dir / 'control'), '/control']
    if step['kind'] == 'build':
        # The only writable host location: this step's fresh /dev/shm directory.
        argv += ['--bind', str(out), '/out']
    return argv + ['--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev', '--remount-ro', '/dev',
                   '--size', '67108864', '--tmpfs', '/scratch', '--chdir', '/scratch',
                   '--setenv', 'PWD', '/scratch', '--setenv', 'TMPDIR', '/scratch',
                   '--setenv', 'LEAN_PATH', '/build', '--setenv', 'LANG', 'C.UTF-8',
                   '--setenv', 'LEAN_ABORT_ON_PANIC', '1', '/lean/bin/lean'] + list(step['args'])


def validate_job(job, probe=False):
    expected = {'schema_version', 'run_dir', 'runtime', 'runtime_hash', 'gate', 'sources', 'proofs'}
    if probe:
        expected = expected | {'probe_root'}
    if not isinstance(job, dict) or set(job) != expected or type(job['schema_version']) is not int or job['schema_version'] != 1:
        raise Rejection('invalid_metadata', 'Job fields/schema do not match contract')
    if len(canonical(job)) > LIMIT:
        raise Rejection('input_limit', 'Job metadata exceeds 1 MiB')
    for key in ('run_dir', 'runtime', 'runtime_hash', 'gate') + (('probe_root',) if probe else ()):
        if not isinstance(job[key], str):
            raise Rejection('invalid_metadata', f'{key} must be a string')
    for key in ('runtime', 'run_dir', 'gate') + (('probe_root',) if probe else ()):
        if not Path(job[key]).is_absolute() or '..' in Path(job[key]).parts:
            raise Rejection('unsafe_path', f'Invalid {key}')
    if probe:
        root = Path(job['probe_root'])
        if str(root) != job['probe_root'] or probe_path_refusal(root) or ROOT.is_relative_to(root):
            raise Rejection('unsafe_path', 'Probe folder must be a normalized verifier folder outside campaign/validation trees and not an ancestor of this checkout')
    if not Path(job['runtime']).resolve().is_relative_to(WORK.resolve()):
        raise Rejection('unsafe_path', 'Runtime must be beneath checkout tmp/recheck')
    if not re.fullmatch('[a-f0-9]{64}', job['runtime_hash']):
        raise Rejection('invalid_metadata', 'Invalid runtime_hash')
    if not isinstance(job['sources'], list) or not 1 <= len(job['sources']) <= 256:
        raise Rejection('input_limit', 'Expected 1..256 sources')
    if not isinstance(job['proofs'], list) or len(job['proofs']) > 4096:
        raise Rejection('input_limit', 'Invalid proof count')
    seen = set()
    for source in job['sources']:
        if not isinstance(source, dict) or set(source) != {'module', 'path', 'sha256'} or not all(isinstance(v, str) for v in source.values()):
            raise Rejection('invalid_metadata', 'Invalid source record')
        module = source['module']
        if not re.fullmatch(MODULE, module) or module.split('.')[0] in {'Init', 'Std', 'Lean'}:
            raise Rejection('unsafe_path', 'Reserved/invalid source module')
        if module in seen or Path(source['path']).suffix != '.lean' or 'agent-home' in Path(source['path']).parts:
            raise Rejection('unsafe_path', 'Duplicate module or forbidden source path')
        if probe:
            path = Path(source['path'])
            if (not path.is_absolute() or '..' in path.parts or str(path) != source['path']
                or not path.is_relative_to(root) or path == root or probe_path_refusal(path)):
                raise Rejection('unsafe_path', 'Probe sources must lie inside the probe folder, outside campaign/validation trees')
        seen.add(module)
    for proof in job['proofs']:
        if (not isinstance(proof, dict) or set(proof) != {'module', 'theorem', 'target', 'relation', 'claim_id'}
            or not all(isinstance(v, str) for v in proof.values())
            or not re.fullmatch(MODULE, proof['module']) or not re.fullmatch(THEOREM, proof['theorem'])):
            raise Rejection('lexical_rejection', 'Invalid proof identifiers/fields')


def probe_declarations(sources, proofs):
    """Probe declarations come only from the verifier's proof list, never from
    parsing hostile probe text."""
    declared = {source['module']: set() for source in sources}
    for proof in proofs:
        if proof['module'] not in declared:
            raise Rejection('invalid_metadata', 'Probe proof names a module that is not a probe source')
        declared[proof['module']].add(proof['theorem'])
    return {module: sorted(names) for module, names in declared.items()}


def admission(directory, sources, proofs, fixture=None, probe_allowed=None):
    models, total = {}, 0
    for source in sources:
        data = read_regular(source['path'])
        total += len(data)
        if total > 16 * LIMIT: raise Rejection('input_limit', 'Source total exceeds 16 MiB')
        if sha(data) != source['sha256']: raise Rejection('source_changed', 'Source hash mismatch')
        if probe_allowed is not None and sha(data) not in probe_allowed:
            raise Rejection('infrastructure_error', 'Probe source is not listed in the verifier probe receipt')
        path = directory / (source['module'].replace('.', '/') + '.lean')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o444)
        models[source['module']] = path
    if fixture:
        if len(models) != 1 or set(models) != {'Fixture'} or file_hash(models['Fixture']) != FIXTURE_HASHES.get(fixture):
            raise Rejection('infrastructure_error', 'Fixture not in fixed hash allowlist')
        # Fixture mode NEVER consumes retained sources or caller proof plans.
        declarations = {'Fixture': ['safe']} if fixture in {'passing', 'false_target', 'forbidden_axiom', 'forged'} else {'Fixture': []}
    elif probe_allowed is not None:
        # Listed probe sources skip both filters, exactly as fixed fixtures do.
        declarations = probe_declarations(sources, proofs)
    else:
        try:
            declarations = {m: audit.inspect_model(p, models) for m, p in models.items()}
        except (audit.AuditError, UnicodeError) as exc:
            raise Rejection('lexical_rejection', str(exc)) from exc
        # Only after the unchanged product filter admitted every module.
        for path in models.values():
            strict_admission(path)
    for module, names in declarations.items():
        if any(not re.fullmatch(THEOREM, n) for n in names):
            raise Rejection('lexical_rejection', 'Nonconventional theorem identifier')
    try:
        markdown_proofs._proofs(proofs, declarations)
        order = (['Fixture'] if fixture else [s['module'] for s in sources] if probe_allowed is not None
                 else markdown_proofs._order(models))
    except (ValueError, audit.AuditError) as exc:
        raise Rejection('dependency_cycle' if 'cycle' in str(exc) else 'lexical_rejection', str(exc)) from exc
    requests = [dict(module=m, theorem=t, target=None, relation=None, claim_id=None)
                for m in sorted(declarations) for t in sorted(declarations[m])]
    requests += sorted(proofs, key=lambda p: tuple(p[k] for k in ('module', 'theorem', 'claim_id', 'target', 'relation')))
    return order, requests


def make_plan(order, requests, fixture=None):
    """Builds in dependency order, then a kernel replay per module, then checks."""
    plan = []
    for module in order:
        plan.append({'kind': 'build', 'module': module, 'object': module + '.olean',
                     'args': ['--threads=1', '--root=/src', '-o', '/out/' + module + '.olean',
                              '/src/' + module.replace('.', '/') + '.lean']})
    for module in order:
        theorems = sorted({r['theorem'] for r in requests if r['module'] == module})
        plan.append({'kind': 'replay', 'module': module,
                     'args': ['--threads=1', '--run', '/control/Replay.lean', module] + theorems})
    for i, request in enumerate(requests, 1):
        plan.append({'kind': 'check', 'module': request['module'], 'request': i - 1,
                     'args': ['--threads=1', f'/checks/Target{i:03d}.lean']})
    return plan


def tree_hashes(root):
    """Every entry beneath root without following links: dirs, regular-file hashes,
    and a marker for anything else (which never equals a recorded baseline)."""
    root, found = Path(root), {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            rel = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode): found[rel] = 'directory'
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1: found[rel] = file_hash(path)
            else: found[rel] = 'special:' + str(info.st_mode)
    return found


def input_hashes(run_dir):
    return {part: tree_hashes(Path(run_dir) / part) for part in ('sources', 'checks', 'control')}


def seal(path):
    """Read-only snapshot for the job: files 0444, directories 0555."""
    for directory, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in files: (Path(directory) / name).chmod(0o444)
        Path(directory).chmod(0o555)


def check_text(proof):
    value = f'import {proof["module"]}\n#check @{proof["theorem"]}\n#print axioms {proof["theorem"]}\n'
    if proof['target'] is not None:
        value += f'example : ({proof["target"]}) := {proof["theorem"]}\n'
    return value.encode()


def _fixture_job(name, runtime, runtime_hash, run_dir):
    if name not in FIXTURE_HASHES: raise Rejection('infrastructure_error', 'Unknown fixture')
    path = FIXTURES / (name + '.lean')
    proofs = []
    if name in {'passing', 'false_target', 'forbidden_axiom', 'forged'}:
        proofs = [dict(module='Fixture', theorem='safe', target='False' if name == 'false_target' else 'True',
                       relation='supports', claim_id='control')]
    return dict(schema_version=1, runtime=str(runtime), runtime_hash=runtime_hash, gate='/fixture-only',
                run_dir=str(run_dir), sources=[dict(module='Fixture', path=str(path), sha256=FIXTURE_HASHES[name])], proofs=proofs)


def _prepare(job, fixture, created=None, probe=False):
    if probe and fixture:
        raise Rejection('infrastructure_error', 'A probe is never a fixture')
    validate_job(job, probe)
    run_dir = Path(job['run_dir'])
    WORK.mkdir(parents=True, exist_ok=True)
    if not run_dir.parent.resolve().is_relative_to(WORK.resolve()) or run_dir.exists() or run_dir.is_symlink():
        raise Rejection('unsafe_path', 'run_dir must be new and beneath checkout tmp/recheck')
    run_dir.mkdir(parents=True, mode=0o700)
    if created is not None:
        created.append(run_dir)
    (run_dir / 'sources').mkdir()
    (run_dir / 'checks').mkdir()
    if fixture:
        (run_dir / 'checks/host-path').write_text(str(run_dir / 'host-sibling'))
        (run_dir / 'checks/Sleeper.lean').write_text('def main : IO Unit := do\n  IO.FS.writeFile "/scratch/child-ready" "ready"\n  IO.sleep 10000\n')
    if fixture and job != _fixture_job(fixture, job['runtime'], job['runtime_hash'], run_dir):
        raise Rejection('infrastructure_error', 'Modified fixed fixture job')
    allowed = None
    if probe:
        # Authorization first: nothing is staged for a probe without its receipt.
        check_probe_root(job['probe_root'])
        probe_hash, allowed = check_probe_gate(job['gate'], job['runtime_hash'], job['probe_root'])
    order, requests = admission(run_dir / 'sources', job['sources'], job['proofs'], fixture, allowed)
    for i, proof in enumerate(requests, 1):
        (run_dir / 'checks' / f'Target{i:03d}.lean').write_bytes(check_text(proof))
    plan = make_plan(order, requests, fixture)
    (run_dir / 'control').mkdir()
    (run_dir / 'control/Replay.lean').write_bytes(REPLAY_PROGRAM.encode())
    (run_dir / 'objects').mkdir()
    (run_dir / 'steps').mkdir()
    if sha(canonical(runtime_manifest(job['runtime']))) != job['runtime_hash']:
        raise Rejection('infrastructure_error', 'Runtime hash mismatch')
    check_mount_points(job['runtime'])
    gate_hash = None if fixture else probe_hash if probe else check_gate(job['gate'], job['runtime_hash'])
    prepared = {'job': job, 'fixture': fixture, 'order': order, 'requests': requests, 'plan': plan,
                'unit': 'lean-recheck-' + str(uuid.uuid4()), 'gate_hash': gate_hash, 'probe': probe}
    for part in ('sources', 'checks', 'control'):
        seal(run_dir / part)
    (run_dir / 'job.json').write_bytes(canonical(prepared))
    (run_dir / 'job.json').chmod(0o444)
    return prepared


def cgroup_values(path):
    values = {}
    for name in ('memory.max', 'memory.swap.max', 'memory.current', 'memory.peak', 'memory.events',
                 'pids.max', 'pids.current', 'pids.events', 'cpu.max', 'cpu.stat', 'cgroup.events'):
        try: values[name] = (path / name).read_text().strip()
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ENODEV): raise
    return values


def scope_properties(unit):
    response = subprocess.run(['/usr/bin/systemctl', '--user', 'show', unit + '.scope',
        '--property=ControlGroup,RuntimeMaxUSec,TimeoutStopUSec,MemoryMax,MemorySwapMax,TasksMax,CPUQuotaPerSecUSec'],
        capture_output=True, text=True, timeout=5, env=host_env())
    if response.returncode:
        raise Rejection('infrastructure_error', response.stderr.strip())
    return dict(line.split('=', 1) for line in response.stdout.splitlines() if '=' in line)


def host_env():
    return {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}'}


def _exec(job_path, step_index):
    prepared = parse_json(read_regular(job_path))
    job, fixture, probe = prepared['job'], prepared['fixture'], prepared['probe']
    # The mode is fixed by the job's shape: probe exactly when probe_root exists.
    if type(probe) is not bool or (probe and fixture) or probe != (isinstance(job, dict) and 'probe_root' in job):
        raise Rejection('infrastructure_error', 'Inconsistent prepared job mode')
    validate_job(job, probe)
    run_dir = Path(job['run_dir'])
    if Path(job_path) != run_dir / 'job.json' or not run_dir.resolve().is_relative_to(WORK.resolve()):
        raise Rejection('infrastructure_error', 'Invalid prepared job location')
    allowed = None
    if fixture:
        if job != _fixture_job(fixture, job['runtime'], job['runtime_hash'], run_dir):
            raise Rejection('infrastructure_error', 'Altered fixture job')
    elif probe:
        _, allowed = check_probe_gate(job['gate'], job['runtime_hash'], job['probe_root'])
    else:
        check_gate(job['gate'], job['runtime_hash'])
    if sha(canonical(runtime_manifest(job['runtime']))) != job['runtime_hash']:
        raise Rejection('infrastructure_error', 'Runtime changed before exec')
    check_mount_points(job['runtime'])
    models = {s['module']: run_dir / 'sources' / (s['module'].replace('.', '/') + '.lean') for s in job['sources']}
    for source in job['sources']:
        data = read_regular(models[source['module']])
        if sha(data) != source['sha256']:
            raise Rejection('source_changed', 'Snapshot changed before exec')
        if probe:
            if sha(data) not in allowed:
                raise Rejection('infrastructure_error', 'Probe snapshot not listed in the verifier probe receipt')
        elif not fixture: audit.inspect_model(models[source['module']], models)
    if not fixture and not probe:
        for path in models.values():
            strict_admission(path)
    declarations = ({'Fixture': ['safe'] if fixture in {'passing', 'false_target', 'forbidden_axiom', 'forged'} else []} if fixture else
                    probe_declarations(job['sources'], job['proofs']) if probe else
                    {m: audit.inspect_model(p, models) for m, p in models.items()})
    markdown_proofs._proofs(job['proofs'], declarations)
    order = ['Fixture'] if fixture else [s['module'] for s in job['sources']] if probe else markdown_proofs._order(models)
    requests = [dict(module=m, theorem=t, target=None, relation=None, claim_id=None)
                for m in sorted(declarations) for t in sorted(declarations[m])]
    requests += sorted(job['proofs'], key=lambda p: tuple(p[k] for k in ('module', 'theorem', 'claim_id', 'target', 'relation')))
    plan = make_plan(order, requests, fixture)
    if prepared['order'] != order or prepared['requests'] != requests or prepared['plan'] != json.loads(canonical(plan)):
        raise Rejection('infrastructure_error', 'Altered prepared plan')
    if read_regular(run_dir / 'control/Replay.lean') != REPLAY_PROGRAM.encode() or os.listdir(run_dir / 'control') != ['Replay.lean']:
        raise Rejection('infrastructure_error', 'Altered trusted replay program')
    if type(step_index) is not int or not 0 <= step_index < len(plan):
        raise Rejection('infrastructure_error', 'Invalid step')
    step = plan[step_index]
    for i, proof in enumerate(requests, 1):
        if read_regular(run_dir / 'checks' / f'Target{i:03d}.lean') != check_text(proof):
            raise Rejection('infrastructure_error', 'Altered generated check')
    expected_checks = {f'Target{i:03d}.lean' for i in range(1, len(requests) + 1)}
    if fixture:
        expected_checks |= {'host-path', 'Sleeper.lean'}
        if read_regular(run_dir / 'checks/host-path') != str(run_dir / 'host-sibling').encode() or read_regular(run_dir / 'checks/Sleeper.lean') != b'def main : IO Unit := do\n  IO.FS.writeFile "/scratch/child-ready" "ready"\n  IO.sleep 10000\n':
            raise Rejection('infrastructure_error', 'Altered fixture helper')
    if {p.relative_to(run_dir / 'checks').as_posix() for p in (run_dir / 'checks').rglob('*') if not p.is_dir()} != expected_checks:
        raise Rejection('infrastructure_error', 'Unexpected check/helper file')
    expected_sources = {m.replace('.', '/') + '.lean' for m in models}
    if {p.relative_to(run_dir / 'sources').as_posix() for p in (run_dir / 'sources').rglob('*') if not p.is_dir()} != expected_sources:
        raise Rejection('infrastructure_error', 'Unexpected snapshot file')
    if not re.fullmatch(r'lean-recheck-[a-f0-9-]{36}', prepared['unit']):
        raise Rejection('infrastructure_error', 'Invalid unit')
    unit = step_unit(prepared['unit'], step_index)
    out = None
    if step['kind'] == 'build':
        # Must be the supervisor's fresh, private, empty directory on the host tmpfs.
        out = out_dir(prepared['unit'], step_index)
        for path in (out.parent.parent, out.parent, out):
            info = os.lstat(path)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise Rejection('infrastructure_error', 'Invalid build output directory')
        if os.path.realpath(out) != str(out) or os.listdir(out):
            raise Rejection('infrastructure_error', 'Build output directory not fresh')
    relative = Path('/proc/self/cgroup').read_text().strip().split('0::')[-1]
    cgroup = Path('/sys/fs/cgroup' + relative)
    values = cgroup_values(cgroup)
    required = {'memory.max': '2147483648', 'memory.swap.max': '0', 'pids.max': '64', 'cpu.max': '100000 100000'}
    props = scope_properties(unit)
    if (cgroup.name != unit + '.scope' or any(values.get(k) != v for k, v in required.items())
        or props.get('ControlGroup') != relative or props.get('RuntimeMaxUSec') != '2min'
        or props.get('TimeoutStopUSec') != '5s' or os.getpriority(os.PRIO_PROCESS, 0) != 15):
        raise Rejection('infrastructure_error', f'Effective scope mismatch: {values}, {props}')
    resource.setrlimit(resource.RLIMIT_FSIZE, (33554432, 33554432))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    evidence = {'event': 'preflight', 'cgroup': str(cgroup), 'limits': values, 'systemd': props,
                'rlimits': {'fsize': list(resource.getrlimit(resource.RLIMIT_FSIZE)), 'core': list(resource.getrlimit(resource.RLIMIT_CORE))},
                'nice': os.getpriority(os.PRIO_PROCESS, 0),
                'namespaces': {n: os.readlink('/proc/self/ns/' + n) for n in ('user', 'ipc', 'pid', 'net', 'uts', 'cgroup')}}
    # Preflight evidence goes to a supervisor-read file the sandbox cannot reach,
    # never to the step's output pipes.
    record = run_dir / 'steps' / f'{step_index:03d}'
    record.mkdir(mode=0o700)
    descriptor = os.open(record / 'preflight.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(canonical(evidence))
    os.execve('/usr/bin/bwrap', bwrap_argv(job['runtime'], run_dir, step, out), {})


def empty_result(job):
    return dict(schema_version=1, outcome='not_run', reason=None, detail='', argv=[], bwrap_argv=[], steps=[],
                exit_code=None, signal=None, seconds=0.0, axioms=[], checks=[], log_paths=[], log_hashes={},
                truncated=False, lean_subprocesses=0, runtime_hash=job.get('runtime_hash') if isinstance(job, dict) and isinstance(job.get('runtime_hash'), str) else None,
                evidence={})


def kill_scope(unit, cgroup):
    started = time.monotonic()
    # Exact UUID unit only. cgroup.kill also covers descendants if systemd is delayed.
    if cgroup is not None and cgroup.name == unit + '.scope' and cgroup.exists():
        try: (cgroup / 'cgroup.kill').write_text('1')
        except OSError:
            subprocess.run(['/usr/bin/systemctl', '--user', 'kill', '--signal=KILL', unit + '.scope'],
                           capture_output=True, timeout=5, env=host_env())
    while cgroup is not None and cgroup.exists():
        try:
            if 'populated 0' in (cgroup / 'cgroup.events').read_text(): break
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENODEV): break
            raise
        if time.monotonic() - started >= 5: return False, time.monotonic() - started
        time.sleep(.02)
    return True, time.monotonic() - started


def axiom_report(text, theorem):
    escaped = re.escape(theorem)
    reports = re.findall(r"(?m)^'" + escaped + r"' (does not depend on any axioms|depends on axioms:\s*\[[^\]]*\])[ \t]*$", text)
    if len(reports) != 1: raise Rejection('axioms_missing', f'Expected one exact axiom report for {theorem}')
    if reports[0] == 'does not depend on any axioms': return []
    group = reports[0].split('[', 1)[1][:-1]
    axioms = [s.strip() for s in group.split(',')] if group.strip() else []
    if any(not a for a in axioms):
        raise Rejection('axioms_missing', f'Malformed axiom report for {theorem}')
    if set(axioms) - ALLOWED_AXIOMS:
        raise Rejection('axioms_disallowed', f'Forbidden axioms for {theorem}: {axioms}')
    return sorted(set(axioms))


def _launch_step(prepared, index, result, started, output):
    """Run ONE plan step in its own scope and sandbox; status only from observation."""
    job, step = prepared['job'], prepared['plan'][index]
    run_dir = Path(job['run_dir'])
    unit = step_unit(prepared['unit'], index)
    out = out_dir(prepared['unit'], index) if step['kind'] == 'build' else None
    argv = systemd_argv(unit, run_dir / 'job.json', index)
    record = {'index': index, 'kind': step['kind'], 'module': step['module'], 'unit': unit, 'argv': argv,
              'bwrap_argv': bwrap_argv(job['runtime'], run_dir, step, out), 'exit_code': None, 'signal': None,
              'seconds': 0.0, 'output_offset': len(output), 'output_length': 0, 'output_sha256': None, 'evidence': {}}
    result['steps'].append(record)
    if not result['argv']:
        result['argv'], result['bwrap_argv'] = argv, record['bwrap_argv']
    evidence = record['evidence']
    # Establish cleanup ownership before spawning or allocating any pipe watcher.
    proc, selector = None, None
    cgroup = Path(f'/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/{unit}.scope')
    forced, detail = None, ''
    step_started = time.monotonic()
    last_sample = 0.0
    # The scope's own cgroup can vanish before a final sample; the parent's
    # memory.events counter is hierarchical and survives it.
    parent_before = oom_kills(cgroup.parent)
    try:
        if time.monotonic() - started >= 120:
            raise Rejection('timeout', f'Job deadline reached before step {index}')
        if out is not None:
            out.parent.parent.mkdir(mode=0o700, exist_ok=True)
            out.parent.mkdir(mode=0o700, exist_ok=True)
            out.mkdir(mode=0o700)
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                close_fds=True, env=host_env(), start_new_session=True)
        result['lean_subprocesses'] += 1
        selector = selectors.DefaultSelector()
        for pipe in (proc.stdout, proc.stderr):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ)
        while selector.get_map():
            now = time.monotonic()
            if now - started >= 120:
                forced = 'timeout'; break
            if now - last_sample >= .05:
                sample = cgroup_values(cgroup)
                if sample: evidence.setdefault('last_cgroup', {}).update(sample)
                last_sample = now
                try:
                    for pid in (cgroup / 'cgroup.procs').read_text().split():
                        ns = {n: os.readlink(f'/proc/{pid}/ns/{n}') for n in ('user', 'ipc', 'pid', 'net', 'uts', 'cgroup')}
                        if ns['net'] != os.readlink('/proc/self/ns/net'):
                            evidence['sandbox_namespaces'] = ns
                except (OSError, ProcessLookupError):
                    pass
            for key, _ in selector.select(.05):
                block = os.read(key.fileobj.fileno(), 65536)
                if not block:
                    selector.unregister(key.fileobj); continue
                # Bytes only: nothing a child prints is ever interpreted.
                room = max(0, LIMIT - len(output))
                output.extend(block[:room])
                if len(block) > room:
                    forced = 'output_limit'; break
            if forced: break
    except (KeyboardInterrupt, SystemExit):
        forced = 'interrupted'
    except Rejection as exc:
        forced, detail = exc.reason, str(exc)
    except Exception as exc:
        forced, detail = 'infrastructure_error', f'{type(exc).__name__}: {exc}'
    finally:
        cleanup_started, errors, empty = time.monotonic(), [], False
        # A failing observation must never prevent subsequent termination attempts.
        try:
            sample = cgroup_values(cgroup)
            if sample: evidence.setdefault('last_cgroup', {}).update(sample)
        except Exception as exc:
            errors.append('cgroup observation: ' + str(exc))
        # Cancel a pending launch first, closing the pre-attestation startup race.
        if forced and proc is not None:
            try:
                if proc.poll() is None: proc.kill()
            except ProcessLookupError:
                pass
            except Exception as exc:
                errors.append('launcher kill: ' + str(exc))
        try:
            empty, _ = kill_scope(unit, cgroup)
        except Exception as exc:
            errors.append('scope kill: ' + str(exc))
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception as exc:
                    errors.append('launcher reap: ' + str(exc))
            except Exception as exc:
                errors.append('launcher wait: ' + str(exc))
        # Verify again after the launcher has been reaped: the scope may have been
        # created between the first missing-directory observation and cancellation.
        try:
            empty_after, _ = kill_scope(unit, cgroup)
            empty = empty and empty_after
        except Exception as exc:
            empty = False
            errors.append('final scope verification: ' + str(exc))
        if selector is not None:
            try: selector.close()
            except Exception as exc: errors.append('selector close: ' + str(exc))
        if proc is not None:
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    try: pipe.close()
                    except Exception as exc: errors.append('pipe close: ' + str(exc))
        evidence.update(cleanup_empty=empty, cleanup_seconds=time.monotonic() - cleanup_started)
        if errors or not empty:
            forced = 'infrastructure_error'
            evidence['cleanup_errors'] = errors
        if proc is None and forced is None:
            forced = 'infrastructure_error'
    code = proc.returncode if proc is not None else None
    record.update(exit_code=code if code is not None and code >= 0 else None,
                  signal=-code if code is not None and code < 0 else None,
                  seconds=time.monotonic() - step_started,
                  output_length=len(output) - record['output_offset'],
                  output_sha256=sha(bytes(output[record['output_offset']:])))
    preflight = run_dir / 'steps' / f'{index:03d}' / 'preflight.json'
    try:
        if proc is not None and os.path.lexists(preflight):
            evidence.update(parse_json(read_regular(preflight)))
    except Rejection as exc:
        forced = forced or 'infrastructure_error'
        detail = detail or 'Unreadable preflight record: ' + str(exc)
    summary = result['evidence']
    summary.update({k: v for k, v in evidence.items() if k not in ('cleanup_empty', 'cleanup_seconds', 'cleanup_errors')})
    summary['cleanup_empty'] = summary.get('cleanup_empty', True) and evidence['cleanup_empty']
    summary['cleanup_seconds'] = max(summary.get('cleanup_seconds', 0.0), evidence['cleanup_seconds'])
    if 'cleanup_errors' in evidence:
        summary.setdefault('cleanup_errors', []).extend(evidence['cleanup_errors'])
    result['exit_code'], result['signal'] = record['exit_code'], record['signal']
    result['truncated'] = result['truncated'] or forced == 'output_limit'
    if forced:
        raise Rejection(forced, detail or f'Step {index} ({step["kind"]}) stopped: {forced}')
    if not evidence.get('limits'):
        raise Rejection('infrastructure_error', f'No effective limit attestation for step {index}: '
                        + bytes(output[record['output_offset']:]).decode(errors='replace')[-2000:])
    samples = evidence.get('last_cgroup', {})
    parent_after = oom_kills(cgroup.parent)
    evidence['parent_oom_kill_delta'] = (parent_after - parent_before
                                         if parent_before is not None and parent_after is not None else None)
    # Reason only: a kill (137) during which the parent slice counted an OOM kill.
    if (re.search(r'oom_kill [1-9]', samples.get('memory.events', ''))
            or (code == 137 and (evidence['parent_oom_kill_delta'] or 0) > 0)):
        raise Rejection('memory_limit', f'Step {index} ({step["kind"]}): memory limit')
    if re.search(r'max [1-9]', samples.get('pids.events', '')):
        raise Rejection('process_limit', f'Step {index} ({step["kind"]}): task limit')
    text = bytes(output[record['output_offset']:]).decode('utf-8', errors='replace')
    if code != 0:
        lowered = text.lower()
        if 'no space left on device' in lowered or 'file too large (error code: 27)' in lowered: reason = 'scratch_limit'
        elif code in (137, 139, 143, 153) or code is None or code < 0: reason = 'signal'
        elif step['kind'] == 'build': reason = 'compile_error'
        elif step['kind'] == 'replay':
            reason = 'axioms_disallowed' if code == 4 else 'infrastructure_error' if code == 8 else 'replay_failed'
        else: reason = 'target_mismatch'
        if step['kind'] == 'replay':
            text = f'replay exit {code}: {REPLAY_EXIT.get(code, "replay crashed")}\n' + text
        raise Rejection(reason, text[-2000:])
    if out is not None:
        record['object_sha256'] = accept_object(out, step, run_dir)
    return record, text


def accept_object(out, step, run_dir):
    """Outside the sandbox, after the scope is empty: exactly one regular file.
    Anything the step left behind is a build failure, never an infrastructure error."""
    try:
        unlock_tree(out)
        names = sorted(os.listdir(out))
        if names != [step['object']]:
            raise Rejection('compile_error', f'Build output must be exactly {step["object"]}, found {names[:8]}')
        directory = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            leaf = os.open(step['object'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        try:
            info = os.fstat(leaf)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > OBJECT_LIMIT:
                raise Rejection('compile_error', 'Build output is not a single-link regular file within 32 MiB')
            parts, size = [], 0
            while True:
                block = os.read(leaf, 1048576)
                if not block: break
                size += len(block)
                if size > OBJECT_LIMIT: raise Rejection('compile_error', 'Build output grew beyond 32 MiB')
                parts.append(block)
        finally:
            os.close(leaf)
    except OSError as exc:
        raise Rejection('compile_error', f'Unusable build output: {exc}') from exc
    data = b''.join(parts)
    target = run_dir / 'objects' / (step['module'].replace('.', '/') + '.olean')
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
    return sha(data)


def unlock_tree(path):
    """Give the owner rwx back on every real directory beneath path (never through
    a link), so a step that locked its own output cannot wedge cleanup."""
    pending = [Path(path)]
    while pending:
        directory = pending.pop()
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            continue
        os.chmod(directory, 0o700)
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))


def oom_kills(cgroup):
    """Hierarchical oom_kill counter of a cgroup (None when unreadable)."""
    try:
        match = re.search(r'^oom_kill (\d+)$', (cgroup / 'memory.events').read_text(), re.M)
        return int(match.group(1)) if match else None
    except OSError:
        return None


def remove_tree(path):
    if os.path.lexists(path):
        if stat.S_ISDIR(os.lstat(path).st_mode):
            unlock_tree(path)
        shutil.rmtree(path)


def sweep_stale_outputs():
    """Under the executor lock: remove build outputs a dead supervisor left behind."""
    parent = shm_parent()
    if not os.path.lexists(parent):
        return 0
    info = os.lstat(parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Rejection('infrastructure_error', 'Unexpected /dev/shm output parent')
    stale = os.listdir(parent)
    for name in stale:
        remove_tree(parent / name)
    return len(stale)


def shm_is_tmpfs():
    try:
        mounts = Path('/proc/mounts').read_text().splitlines()
    except OSError:
        return False
    return any(line.split()[1:3] == [str(SHM), 'tmpfs'] for line in mounts if len(line.split()) > 2)


def _launch(prepared, result, started):
    job = prepared['job']
    run_dir = Path(job['run_dir'])
    plan = prepared['plan']
    result['evidence']['gate_hash'] = prepared['gate_hash']
    output, objects = bytearray(), {}
    complete = False
    try:
        baseline = input_hashes(run_dir)
        for index, step in enumerate(plan):
            # Same-user host writes to staged inputs or accepted objects are caught here.
            if input_hashes(run_dir) != baseline:
                raise Rejection('source_changed', f'Staged sources, checks or replay program changed before step {index}')
            if tree_hashes(run_dir / 'objects') != objects:
                raise Rejection('source_changed', f'Accepted objects changed before step {index}')
            try:
                record, text = _launch_step(prepared, index, result, started, output)
            finally:
                if step['kind'] == 'build':
                    remove_tree(out_dir(prepared['unit'], index))
            if step['kind'] == 'build':
                path = step['module'].replace('.', '/') + '.olean'
                for prefix in Path(path).parents:
                    if str(prefix) != '.': objects[prefix.as_posix()] = 'directory'
                objects[path] = record['object_sha256']
            if step['kind'] == 'check':
                proof = prepared['requests'][step['request']]
                axioms = axiom_report(text, proof['theorem'])
                result['checks'].append(dict(proof, index=index, axioms=axioms))
                result['axioms'] = sorted(set(result['axioms']) | set(axioms))
        if input_hashes(run_dir) != baseline or tree_hashes(run_dir / 'objects') != objects:
            raise Rejection('source_changed', 'Staged inputs or accepted objects changed during the job')
        complete = True
    finally:
        remove_tree(shm_parent() / prepared['unit'])
        result['evidence']['steps_complete'] = complete
        (run_dir / 'output.log').write_bytes(bytes(output[:LIMIT]))
        wire = bytearray()
        for record in result['steps']:
            row = {k: v for k, v in record.items() if k not in ('evidence', 'bwrap_argv')}
            wire.extend((canonical(row) + b'\n')[:max(0, LIMIT - len(wire))])
        (run_dir / 'wire.log').write_bytes(bytes(wire))
        result['log_paths'] = [str(run_dir / n) for n in ('output.log', 'wire.log')]
        result['log_hashes'] = {p: file_hash(p) for p in result['log_paths']}
    result.update(outcome='pass', reason=None)


def _run(job, fixture=None, probe=False):
    started, result = time.monotonic(), empty_result(job)
    lock, created = None, []
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        lock = open(WORK / 'executor.lock', 'a')
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Rejection('infrastructure_error', 'Another sandbox invocation is active')
        prepared = _prepare(job, fixture, created, probe)
        if sys.platform != 'linux' or not Path('/usr/bin/bwrap').is_file():
            raise Rejection('infrastructure_error', 'Linux, cgroup v2, systemd user manager and Bubblewrap required')
        if not shm_is_tmpfs():
            raise Rejection('infrastructure_error', '/dev/shm must be a tmpfs (build output bound by the memory cgroup)')
        result['evidence']['stale_outputs_removed'] = sweep_stale_outputs()
        result['evidence']['bindings'] = binding_manifest(job['runtime_hash'])
        _launch(prepared, result, started)
    except Rejection as exc:
        outcome = ('filter_rejected' if exc.reason in {'lexical_rejection', 'strict_rejection'} else 'timeout' if exc.reason == 'timeout'
                   else 'not_run' if exc.reason in {'infrastructure_error', 'interrupted'} else 'fail')
        result.update(outcome=outcome, reason=exc.reason, detail=str(exc))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result.update(outcome='not_run', reason='infrastructure_error', detail=f'{type(exc).__name__}: {exc}')
    finally:
        if lock: lock.close()
    if probe:
        # Every probe result, including early refusals, is marked and uncountable.
        result.update(probe_outcome=result['outcome'], outcome=PROBE_OUTCOME)
    result['seconds'] = time.monotonic() - started
    if created:
        path = created[0]
        temporary = path / 'result.pending'
        with temporary.open('xb') as stream:
            stream.write(canonical(result))
        temporary.replace(path / 'result.json')
    return result


def run_job(job):
    """Production entry: unchanged lexical admission, strict admission and
    mandatory verifier gate. Refuses probe jobs (probe_root) and probe receipts."""
    return _run(job)


def run_probe(job):
    """Verifier-only entry: identical launch path, probe receipt admission.

    The result's outcome is always PROBE_OUTCOME; it can never approve anything
    or count as a recheck result.
    """
    return _run(job, probe=True)


def _run_fixture(name, runtime, runtime_hash, run_dir):
    """Test-only fixed content entry. No arbitrary-path/filter/gate switch."""
    return _run(_fixture_job(name, runtime, runtime_hash, run_dir), fixture=name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['_exec', 'probe'])
    parser.add_argument('--job', required=True)
    parser.add_argument('--step', type=int)
    args = parser.parse_args()
    if args.command == 'probe':
        try:
            job = parse_json(read_regular(os.path.abspath(args.job)))
        except Rejection as exc:
            job = None
            print(f'Unreadable probe job: {exc}', file=sys.stderr)
        result = run_probe(job) if job is not None else None
        if result is None:
            return 2
        sys.stdout.write(canonical(result).decode() + '\n')
        return 0
    try: _exec(args.job, args.step)
    except Exception as exc:
        print(f'Infrastructure failure before exec: {exc}', file=sys.stderr)
        return 125
    return 0


if __name__ == '__main__':
    sys.exit(main())
