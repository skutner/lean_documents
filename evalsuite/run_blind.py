#!/usr/bin/env python3
"""Run blind audits through ALA, one contestant ("arm") at a time, and keep what each one hands in.

Every review gets a new folder that holds one report, its sources and, for the arms that use it,
a copy of the skill. ALA mounts that folder, a private home and, for the Lean arm, the Lean
toolchain, beside the system folders and the coding agent's own program, which it mounts
read-only. The answer keys, the other report of the same dossier, the records and earlier
reviews are not mounted.

Every arm hands in the same answer sheet, `verdicts.json`. The runner turns it into the bundle
layout that scorecard.py reads. A review that has started is final: whatever sheet exists when
the agent stops or the time runs out is scored, and a review without a valid sheet counts as
failed. Nothing is run a second time.

    plan   show what would run; starts nothing
    probe  model-free check of the sandbox: a simulated agent looks for the answer keys
    run    probe, then launch the reviews (this is the only command that reaches a model)

This runner never opens an answer key. It reads the manifest that sealing wrote, for the canary
and the hashes of the sealed files, and needs the keys folder only to keep it out of reach.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blindness  # noqa: E402

RUNNER_VERSION = 1
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STATES = ('supported', 'refuted', 'both', 'undetermined', 'not_applicable')
FORMAL = ('proved', 'refuted', 'auxiliary', 'not_attempted', 'blocked')
FORMAL_CLAIMS = {'proved', 'refuted', 'auxiliary'}
SHEET = 'verdicts.json'
SHEET_LIMIT = 2_000_000
VARIANT_FILES = (('report-correct.md', 'correct'), ('report-mixed.md', 'mixed'), ('report.md', 'mixed'))
SAFE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}')
ARM_ID = re.compile(r'[a-z][a-z0-9-]{0,39}')
AGENT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
REPORT_ANCHOR = re.compile(r'<a\s+id=["\']([^"\']+)["\']\s*>\s*</a>')
CLAIM_HEADING = re.compile(r'\*\*([A-Za-z0-9-]+)\.\*\*')
SUSPECT_LIMIT = 3
HOME_LIMIT_FILES, HOME_LIMIT_BYTES = 200, 20_000_000
PROBE_SECONDS = 300
CASE_FILE_LIMIT = 200_000_000
KEPT_SUFFIXES, KEPT_FILE_LIMIT = ('.jsonl', '.json', '.md', '.txt', '.log', '.toml'), 20_000_000
ENDPOINT_VARIABLES = ('OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'OPENAI_ORGANIZATION', 'OPENAI_PROJECT')
SEATBELT = '/usr/bin/sandbox-exec'
PYTHON_SENTENCE = 'Use {python} wherever these instructions or the skill say python3.'
PYTHON_MINIMUM = (3, 12)


class RunError(Exception):
    """The campaign cannot run as requested."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    """Write through a temporary file, so a reader never sees half a record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def tree_hash(root, skip=()):
    """One hash for a folder: every regular file's relative path and bytes."""
    root, digest = Path(root), hashlib.sha256()
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or any(part in skip for part in relative.parts) or path.suffix == '.pyc':
            continue
        digest.update(relative.as_posix().encode() + b'\0' + sha_bytes(path.read_bytes()).encode() + b'\n')
    return digest.hexdigest()


# ---------- Arms and their instructions ----------

def load_arms(path=HERE / 'arms.json', python=None):
    """Read the arms file. An arm's fingerprint covers everything that shapes what its agent is told. With the
    installed toolchain interpreter (`toolchain_python`), every arm's task ends with one sentence that names it, so
    the sentence is part of every fingerprint; without it the task text is the file's, unchanged."""
    path = Path(path)
    data = read_json(path)
    task = (path.parent / data['task']).read_text(encoding='utf-8')
    if python:
        task = task.rstrip('\n') + '\n\n' + PYTHON_SENTENCE.format(python=python['path']) + '\n'
    arms = {}
    for spec in data['arms']:
        ident = spec.get('id', '')
        if not ARM_ID.fullmatch(ident) or ident in arms:
            raise RunError(f'Unusable or repeated arm id: {ident!r}')
        if type(spec.get('skill')) is not bool or type(spec.get('lean')) is not bool:
            raise RunError(f'Arm {ident} needs true or false for "skill" and "lean"')
        method = (path.parent / spec['prompt']).read_text(encoding='utf-8')
        fingerprint = sha_bytes(json.dumps([RUNNER_VERSION, ident, spec['skill'], spec['lean'], method, task]).encode())
        arms[ident] = {'id': ident, 'label': spec.get('label', ident), 'skill': spec['skill'], 'lean': spec['lean'],
                       'method': method, 'task': task, 'fingerprint': fingerprint}
    return arms


def instructions(arm, minutes, lean=None, brief=False):
    """The task text one agent receives: the shared task with this arm's way of working inserted."""
    values = {'method': arm['method'].strip(), 'minutes': str(minutes), 'sheet': SHEET,
              'lean': str(lean) if arm['lean'] and lean else '',
              'brief': 'A review brief is in `case/brief.md`. It is an instruction, not evidence.' if brief else ''}
    text = arm['task']
    for name in ('method', 'minutes', 'sheet', 'lean', 'brief'):   # the method first: it may use the others
        text = text.replace('{{' + name + '}}', values[name])
    if '{{' in text:
        raise RunError(f'Arm {arm["id"]} leaves a placeholder unfilled')
    return re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'


# ---------- Fixtures and the plan ----------

def discover(fixtures):
    """Dossiers under `fixtures`: a `sources` folder and a report file for each variant."""
    found = []
    for folder in sorted(Path(fixtures).iterdir()):
        if folder.is_symlink() or not folder.is_dir():
            continue
        variants = {}
        for name, variant in VARIANT_FILES:
            path = folder / name
            if path.is_file() and not path.is_symlink():
                variants.setdefault(variant, path)
        sources = folder / 'sources'
        if not variants or sources.is_symlink() or not sources.is_dir():
            continue
        if not SAFE_NAME.fullmatch(folder.name):
            raise RunError(f'Dossier folder name cannot be used in file names: {folder.name}')
        brief = folder / 'brief.md'
        found.append({'dossier': folder.name, 'variants': variants, 'sources': sources,
                      'brief': brief if brief.is_file() and not brief.is_symlink() else None})
    return found


def dossier_hashes(dossier):
    """What the agent will be given for each variant, as one hash per variant."""
    shared = tree_hash(dossier['sources']) + (sha_bytes(dossier['brief'].read_bytes()) if dossier['brief'] else '')
    return {variant: sha_bytes((sha_bytes(path.read_bytes()) + shared).encode())
            for variant, path in sorted(dossier['variants'].items())}


def unit_id(arm, dossier, variant, repeat):
    return f'{arm}__{dossier}__{variant}__r{repeat}'


def plan_units(dossiers, arm_ids, repeats, seed):
    """Every review of the campaign, in an order fixed by the seed, so the arms take turns."""
    units = [{'id': unit_id(arm, d['dossier'], variant, repeat), 'arm': arm, 'dossier': d['dossier'],
              'variant': variant, 'repeat': repeat}
             for d in dossiers for variant in sorted(d['variants']) for arm in arm_ids for repeat in range(1, repeats + 1)]
    return sorted(units, key=lambda unit: sha_bytes(f'{seed}:{unit["id"]}'.encode()))


# ---------- The answer sheet and the bundle the scorer reads ----------

def paragraphs(text):
    """Report passages by anchor, exactly as the audit helper's `prepare` records them."""
    explicit = list(REPORT_ANCHOR.finditer(text))
    if explicit:
        result = {}
        prefix = text[:explicit[0].start()].strip()
        if prefix:
            anchor = 'document-preamble'
            while anchor in {m.group(1) for m in explicit}:
                anchor += '-preamble'
            result[anchor] = prefix
        for index, match in enumerate(explicit):
            if match.group(1) in result:
                raise RunError(f'Duplicate report anchor: {match.group(1)}')
            end = explicit[index + 1].start() if index + 1 < len(explicit) else len(text)
            result[match.group(1)] = text[match.end():end].strip()
        return result
    parts = (part for part in re.split(r'\n\s*\n', text.replace('\r\n', '\n')) if part.strip())
    return {f'P{number:02d}': part.strip() for number, part in enumerate(parts, 1)}


def claim_anchors(ledger):
    """Claim identifier -> report anchor, for passages that start with a bold identifier such as **C01.**"""
    anchors = {}
    for anchor, paragraph in ledger.items():
        heading = CLAIM_HEADING.match(paragraph.strip())
        if heading:
            if heading[1] in anchors:
                raise RunError(f'Duplicate report claim identifier: {heading[1]}')
            anchors[heading[1]] = anchor
    return anchors


def read_plain(root, relative, limit):
    """Bytes of `root/relative`, or None unless it is a regular file of at most `limit` bytes that is reached
    through real folders only. An agent can leave links, devices or huge files in any folder it could write.
    Every step is opened without following links and the opened file itself is checked, so nothing outside
    `root` is ever read, whatever the agent left and even if something were still changing the folder."""
    parts = Path(relative).parts
    if not parts or any(part in ('', '.', '..') for part in parts):
        return None
    try:
        handle = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        for position, part in enumerate(parts):
            kind = os.O_NONBLOCK if position == len(parts) - 1 else os.O_DIRECTORY
            step = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | kind, dir_fd=handle)
            os.close(handle)
            handle = step
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        with os.fdopen(os.dup(handle), 'rb') as stream:
            data = stream.read(limit + 1)
        return data if len(data) <= limit else None
    except OSError:
        return None
    finally:
        os.close(handle)


def unlink_inside(root, relative):
    """Remove `root/relative` when its folders are real folders. A link in the last place is removed itself,
    never its target; a link earlier in the path leaves everything untouched."""
    parts = Path(relative).parts
    if not parts or any(part in ('', '.', '..') for part in parts):
        return False
    try:
        handle = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return False
    try:
        for part in parts[:-1]:
            step = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=handle)
            os.close(handle)
            handle = step
        os.unlink(parts[-1], dir_fd=handle)
        return True
    except OSError:
        return False
    finally:
        os.close(handle)


def reclaim(root):
    """Give this user back access to every folder and file under `root`. An agent can take permissions away
    from what it wrote, and the runner could then neither read nor move nor remove it. Links are not followed."""
    pending = [str(root)]
    while pending:
        folder = pending.pop()
        try:
            info = os.lstat(folder)
            if not stat.S_ISDIR(info.st_mode):
                continue
            os.chmod(folder, stat.S_IMODE(info.st_mode) | 0o700)
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        mode = stat.S_IMODE(entry.stat(follow_symlinks=False).st_mode)
                        if mode & 0o600 != 0o600:
                            os.chmod(entry.path, mode | 0o600)
        except OSError:
            continue


def read_sheet(folder):
    """Read the answer sheet an agent left. Rows that cannot be used are dropped and counted."""
    if not os.path.lexists(Path(folder) / SHEET):
        return {'status': 'missing', 'rows': [], 'dropped': 0}
    data = read_plain(folder, SHEET, SHEET_LIMIT)
    try:
        data = json.loads(data.decode('utf-8-sig')) if data is not None else None
    except (ValueError, RecursionError):
        data = None
    if data is None:
        return {'status': 'unreadable', 'rows': [], 'dropped': 0}
    claims = data.get('claims') if isinstance(data, dict) else data
    if not isinstance(claims, list):
        return {'status': 'malformed', 'rows': [], 'dropped': 0}
    rows = [row for row in claims if isinstance(row, dict) and isinstance(row.get('claim_id'), str)
            and row['claim_id'].strip() and isinstance(row.get('semantic_state'), str) and row['semantic_state'] in STATES]
    return {'status': 'valid' if rows else 'malformed', 'rows': rows, 'dropped': len(claims) - len(rows)}


def source_path(value):
    """Name a cited source the way the bundle does, wherever the agent saw it."""
    parts = [part for part in value.replace('\\', '/').split('/') if part not in ('', '.')]
    if 'sources' in parts[:-1]:
        last = len(parts) - 2 - parts[-2::-1].index('sources')
        return 'input/sources/' + '/'.join(parts[last + 1:])
    return value


def findings_from(rows, anchors, lean):
    """Turn sheet rows into findings. An arm without Lean cannot hold a checked proof, so a formal
    result it reports is recorded as not attempted and counted."""
    findings, stripped = [], 0
    for row in rows:
        ident = row['claim_id'].strip()[:80]
        formal = row.get('formal_status') if row.get('formal_status') in FORMAL else 'not_attempted'
        if formal in FORMAL_CLAIMS and not lean:
            formal, stripped = 'not_attempted', stripped + 1
        evidence = [{'path': source_path(item['path'])[:400], 'anchor': str(item.get('anchor', ''))[:200],
                     'quote': str(item.get('quote', ''))[:2000]}
                    for item in (row.get('evidence') if isinstance(row.get('evidence'), list) else [])
                    if isinstance(item, dict) and isinstance(item.get('path'), str)]
        findings.append({'claim_id': ident, 'report_anchor': anchors.get(ident, 'unlisted:' + ident),
                         'semantic_state': row['semantic_state'], 'formal_status': formal,
                         'argument_status': 'not_reported', 'evidence': evidence,
                         'explanation': str(row.get('explanation', ''))[:4000]})
    return findings, stripped


def build_bundle(bundle, report, sources, sheet, lean):
    """Write what scorecard.py reads, from the original report and sources, never from the agent's copies.
    A review without a valid sheet gets no findings file, which the scorer lists as a missing run."""
    ledger = paragraphs(Path(report).read_text(encoding='utf-8'))
    write_json(bundle / 'input/report-ledger.json', ledger)
    shutil.copytree(sources, bundle / 'input/sources')
    if sheet['status'] != 'valid':
        return {'findings': 0, 'formal_claims_stripped': 0, 'claims_in_report': len(claim_anchors(ledger))}
    findings, stripped = findings_from(sheet['rows'], claim_anchors(ledger), lean)
    write_json(bundle / 'findings.json', findings)
    return {'findings': len(findings), 'formal_claims_stripped': stripped, 'claims_in_report': len(claim_anchors(ledger))}


# ---------- ALA ----------

def locate_ala(explicit=None):
    """An AdvancedLanguageAgent checkout: --ala, then ALA_CLI, then beside or inside this repository."""
    candidates = [explicit] if explicit else [os.environ['ALA_CLI']] if os.environ.get('ALA_CLI') else [
        REPO.parent / 'AdvancedLanguageAgent', REPO / 'AdvancedLanguageAgent']
    for candidate in candidates:
        root = Path(candidate)
        root = root.parent.parent if root.name == 'ala.mjs' else root
        entry = root / 'bin/ala.mjs'
        try:
            package = read_json(root / 'package.json')
        except (OSError, ValueError):
            continue
        if entry.is_file() and package.get('name') == 'advanced-language-agent':
            commit = None
            try:
                done = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=20)
                commit = done.stdout.strip() if done.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                pass
            return {'entry': str(entry.resolve()), 'version': package.get('version'), 'commit': commit}
    raise RunError('ALA was not found. Pass --ala with the AdvancedLanguageAgent folder, or set ALA_CLI.')


def ala_arguments(entry, selection, run, lean_root=None, home=None, session=None, python_root=None):
    """ALA mounts exactly `--cwd` and each `--folder`. The run folder is the only writable one, the Lean
    toolchain and the installed Python toolchain (for every arm, when it is the interpreter in use) are read-only
    at their own paths, and the repository is not mounted at all."""
    agent, model, effort = selection['agent'], selection.get('model') or '', selection.get('effort') or ''
    if not AGENT_ID.fullmatch(agent) or model.startswith('-') or effort.startswith('-'):
        raise RunError('Unusable agent, model or effort')
    run = Path(run)
    args = [entry, '--ca', agent] + (['--model', model] if model else []) + (['--effort', effort] if model and effort else [])
    args += ['--config', str(run / 'no-ala-config.json'), '--cwd', str(run)]
    if home:
        args += ['--home', str(home)]
    if lean_root:
        args += ['--folder', str(lean_root)]
    if python_root:
        args += ['--folder', str(python_root)]
    if session:
        args += ['--session-id', session]
    return args + ['--taskFile', str(run / 'instructions.md'), '--output', str(run / 'final-message.md')]


def ala_environment(env=None, credentials=True):
    """ALA clears the environment inside its sandbox. Host router variables are dropped here as the pilot
    server does, and the probe also drops model credentials."""
    env = dict(os.environ if env is None else env)
    blocked = ('PLOINKY_',) + (() if credentials else ('OPENAI_', 'ANTHROPIC_', 'CLAUDE_CODE_'))
    return {key: value for key, value in env.items() if not key.startswith(blocked)}


def stop_group(child):
    """End the agent and everything it started."""
    for signal_number, patience in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
        try:
            os.killpg(child.pid, signal_number)
        except (ProcessLookupError, PermissionError):
            break
        try:
            child.wait(timeout=patience)
            break
        except subprocess.TimeoutExpired:
            continue
    try:                                    # the leader is gone; nothing it started may outlive it
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def launch(command, cwd, env, seconds, log):
    """Run one agent to its end or to the time limit. Output goes to files outside the agent's folder."""
    started = time.monotonic()
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    with open(f'{log}.stdout.log', 'wb') as out, open(f'{log}.stderr.log', 'wb') as err:
        try:
            child = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                     start_new_session=True)
        except OSError as error:
            return {'ended': 'launch_error', 'exit_code': None, 'seconds': 0.0, 'detail': str(error)}
        timed_out = False
        try:
            code = child.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            stop_group(child)
        code = child.wait()
    return {'ended': 'timeout' if timed_out else 'exit_0' if code == 0 else 'exit_nonzero', 'exit_code': code,
            'seconds': round(time.monotonic() - started, 1)}


# ---------- A private agent home per review ----------

def check_home_template(template):
    """A login made for the campaign: small, with no links, so it can be copied for every review."""
    template = Path(template)
    if template.is_symlink() or not template.is_dir():
        raise RunError(f'--agent-home must be an existing folder: {template}')
    found, size = [], 0
    for path in sorted(template.rglob('*')):
        if path.is_symlink():
            raise RunError(f'--agent-home holds a symbolic link: {path}')
        if path.is_file():
            found.append(path)
            size += path.stat().st_size
    if not found:
        raise RunError(f'--agent-home is empty; log the coding agent in there first: {template}')
    if len(found) > HOME_LIMIT_FILES or size > HOME_LIMIT_BYTES:
        raise RunError(f'--agent-home holds {len(found)} files and {size} bytes. It should hold only a login, '
                       'not the history or caches of earlier sessions.')
    return {path.relative_to(template).as_posix(): sha_bytes(path.read_bytes()) for path in found}


def open_home(template, arena):
    """Copy the login into a new home for one review. Returns the home and the hashes of what was copied."""
    listed = check_home_template(template)
    home = Path(arena) / ('home-' + uuid.uuid4().hex[:16])
    try:
        shutil.copytree(template, home)
    except BaseException:
        # A copy that failed half way may hold part of the login: it goes, and only this one, never another home.
        if home.is_dir() and not home.is_symlink():
            reclaim(home)
            shutil.rmtree(home, ignore_errors=True)
        raise
    return home, listed


def close_home(home, listed, archive):
    """Take the copied login out of a used home and keep the rest with the review. Nothing goes back to the
    template: a file that returned there would carry whatever one review wrote into the next. Returns the
    login files the agent's client changed. If such a change was a real token refresh, later reviews may
    fail to sign in; the campaign then stops, and that is the price of keeping reviews apart."""
    reclaim(home)
    changed = []
    for relative, digest in sorted(listed.items()):
        data = read_plain(home, relative, HOME_LIMIT_BYTES)
        if data is None or sha_bytes(data) != digest:
            changed.append(relative)
        unlink_inside(home, relative)
    prune_home(home)
    if any(path.is_file() for path in Path(home).rglob('*')):
        shutil.move(str(home), str(archive))
    else:
        shutil.rmtree(home)
    return changed


def prune_home(home):
    """Reduce a used home to its readable record: session history, notes and logs. With a home of its own, the
    agent's client also unpacks its runtime and caches there, hundreds of megabytes per review that say nothing
    about the audit. Links are removed themselves and never followed."""
    for folder, folders, files in os.walk(home, topdown=False):
        for name in files + folders:
            path = os.path.join(folder, name)
            try:
                info = os.lstat(path)
                if stat.S_ISDIR(info.st_mode):
                    if not os.listdir(path):
                        os.rmdir(path)
                elif not (stat.S_ISREG(info.st_mode) and name.endswith(KEPT_SUFFIXES) and info.st_size <= KEPT_FILE_LIMIT):
                    os.unlink(path)
            except OSError:
                continue


def remove_stale_homes(arena):
    """Homes left in the arena by a runner that was stopped. Each may hold a copy of the login, so none is kept."""
    removed = []
    for entry in sorted(Path(arena).glob('home-*')):
        if entry.is_dir() and not entry.is_symlink():
            reclaim(entry)
            shutil.rmtree(entry)
            removed.append(entry.name)
    return removed


# ---------- Campaign: what is frozen, the lock, the records ----------

def code_hash():
    names = ['run_blind.py', 'blindness.py', 'arms.json'] + sorted(p.relative_to(HERE).as_posix()
                                                                   for p in (HERE / 'prompts').glob('*.md'))
    return sha_bytes(''.join(name + '\0' + sha_bytes((HERE / name).read_bytes()) + '\n' for name in names).encode())


def lean_identity(lean):
    """The toolchain folder that is mounted, and the version it reports when this machine can run it."""
    lean = Path(lean)
    if not lean.is_absolute() or lean.name != 'lean' or lean.parent.name != 'bin' or not lean.is_file():
        raise RunError('--lean must be the absolute path of a toolchain executable: /…/bin/lean')
    version = None
    try:
        done = subprocess.run([str(lean), '--version'], capture_output=True, text=True, timeout=30)
        version = done.stdout.strip() if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {'path': str(lean), 'root': str(lean.parent.parent.resolve()), 'version': version}


def python_version(interpreter):
    """The version an interpreter reports as X.Y.Z, or None when it cannot be run or answers unexpectedly."""
    try:
        done = subprocess.run([str(interpreter), '-I', '-c', 'import sys; print(".".join(str(n) for n in sys.version_info[:3]))'],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.strip()
    return out if done.returncode == 0 and re.fullmatch(r'\d+\.\d+\.\d+', out) else None


def toolchain_module():
    """tools/toolchain.py, which reads the same manifest as tools/toolchain.mjs."""
    spec = importlib.util.spec_from_file_location('evalsuite_toolchain', REPO / 'tools/toolchain.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def toolchain_python(env=None, repo=REPO, manifest=None, key=None, version_of=python_version):
    """The interpreter installed by tools/setup-toolchain.mjs, when it is the one `resolvePython` in tools/toolchain.mjs
    picks: AUDIT_PYTHON decides when it is set, and it is the toolchain one only when it lies in the toolchain folder;
    otherwise the toolchain interpreter counts when its install marker matches the pinned asset and it runs at the
    minimum version. Returns its absolute path, its install root and its version. None means a system interpreter,
    or none at all: the suite then mounts, tells and records nothing about Python, exactly as before."""
    env = os.environ if env is None else env
    if manifest is None or key is None:
        try:
            tools = toolchain_module()
        except Exception as error:      # missing or broken: a clear refusal, not a traceback
            raise RunError(f'tools/toolchain.py cannot be loaded, so the Python toolchain cannot be resolved '
                           f'({type(error).__name__}: {error})')
        try:
            key = key or tools.platform_key()
        except tools.UnsupportedPlatformError:
            return None
        try:
            manifest = manifest or tools.load_manifest()
        except (OSError, ValueError) as error:
            raise RunError(f'tools/toolchain.json cannot be read, so the Python toolchain cannot be resolved '
                           f'({type(error).__name__}: {error})')
    # No python section, or no entry for this platform: no toolchain interpreter, so a system one. Only a manifest
    # of the wrong shape is an error.
    invalid = RunError('tools/toolchain.json is malformed: the manifest, its "python" section, "python.platforms" and '
                       'each platform entry must be objects, and "python.minimum" a list of whole numbers')
    if not isinstance(manifest, dict):
        raise invalid
    section = manifest.get('python')
    if section is None:
        return None
    platforms = section.get('platforms', {}) if isinstance(section, dict) else None
    minimum = section.get('minimum') if isinstance(section, dict) else None
    if not isinstance(platforms, dict) or not (
            minimum is None or (isinstance(minimum, list) and all(type(part) is int for part in minimum))):
        raise invalid
    entry = platforms.get(key)
    if entry is not None and not isinstance(entry, dict):
        raise invalid
    if not entry or not SAFE_NAME.fullmatch(str(entry.get('folder', ''))):
        return None
    root = Path(repo) / 'tmp' / 'toolchains' / entry['folder']
    if env.get('AUDIT_PYTHON'):
        candidate = Path(env['AUDIT_PYTHON'])
        if not candidate.is_absolute() or not root.is_dir() or not blindness.within(candidate, root):
            return None
    else:
        try:
            marker = read_json(root.with_name(entry['folder'] + '.installed.json'))
        except (OSError, ValueError):
            return None
        pin = entry.get('sha256')
        if not isinstance(marker, dict) or not isinstance(pin, str) or marker.get('sha256') != pin \
                or marker.get('asset') != entry.get('asset'):
            return None
        candidate = root / 'bin/python3'
    if not candidate.is_file() or not os.access(str(candidate), os.X_OK):
        return None
    version = version_of(candidate)
    minimum = tuple(section.get('minimum') or PYTHON_MINIMUM)
    if not version or tuple(int(part) for part in version.split('.')) < minimum:
        return None
    return {'path': str(blindness.real(candidate.parent) / candidate.name), 'root': str(blindness.real(root)),
            'version': version}


def sealed_keys(keys):
    """The sealed keys folder: its manifest names the dossiers and carries the canary. No key is opened."""
    manifest_path = Path(keys) / blindness.MANIFEST_NAME
    if not manifest_path.is_file():
        raise RunError(f'{keys} is not a sealed keys folder. Seal the keys first: python3 evalsuite/blindness.py seal …')
    manifest = read_json(manifest_path)
    canary = manifest.get('canary', '')
    if not isinstance(canary, str) or not canary.startswith(blindness.CANARY_PREFIX):
        raise RunError('The keys manifest carries no canary')
    files = manifest.get('files', {})
    hashes = {entry.get('sealed_sha256'): f'sealed key {name}' for name, entry in files.items() if isinstance(entry, dict)}
    if not files or None in hashes or any(not re.fullmatch(r'[0-9a-f]{64}', digest) for digest in hashes):
        raise RunError('The keys manifest does not list the hashes of the sealed files')
    return {'canary': canary, 'dossiers': {Path(name).stem for name in files}, 'hashes': hashes,
            'sha256': sha_bytes(manifest_path.read_bytes())}


def check_layout(context):
    """Refuse a layout in which a folder the agent can read overlaps answer material."""
    roots = [context['arena']] + ([context['lean']['root']] if context.get('lean') else []) + (
        [context['home_template']] if context.get('home_template') else []) + (
        [context['python']['root']] if context.get('python') else [])
    # The repository's own answer material is protected too, wherever the campaign folders are.
    protected = [context['keys'], context['fixtures'], context['out']] + [
        path for path in (REPO / 'examples', REPO / 'validation') if path.is_dir()]
    Path(context['arena']).mkdir(parents=True, exist_ok=True)
    try:
        blindness.assert_isolated(roots, protected)
        if context.get('lean'):      # the read-only toolchain and the writable run folders must not overlap
            blindness.assert_isolated([context['lean']['root']], [context['arena']])
        if context.get('python'):    # the same for the Python toolchain
            blindness.assert_isolated([context['python']['root']], [context['arena']])
    except blindness.BlindnessError as error:
        raise RunError(f'Blindness check failed: {error}')
    if context.get('lean'):
        # `--lean ~/bin/lean` would mount the whole home folder for the Lean arm.
        for place in (REPO, Path.home(), Path(context['lean']['root']).anchor):
            if blindness.within(place, context['lean']['root']):
                raise RunError(f'--lean must sit in a toolchain folder of its own; {context["lean"]["root"]} also holds {place}')
    if context.get('python'):
        for place in (REPO, Path.home(), Path(context['python']['root']).anchor):
            if blindness.within(place, context['python']['root']):
                raise RunError(f'The Python toolchain must sit in a folder of its own; {context["python"]["root"]} also holds {place}')
    return [str(blindness.real(root)) for root in roots]


def acquire_lock(out):
    """One runner per campaign folder. A lock whose process is gone is taken over; a live one is refused.
    The lock appears with its content already in it, so no reader ever finds it empty."""
    path = Path(out) / 'runner.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = path.with_name(f'runner.lock.{os.getpid()}.{uuid.uuid4().hex[:8]}')
    draft.write_text(f'{os.getpid()} {now()}\n', encoding='utf-8')
    try:
        for _ in range(2):
            try:
                os.link(draft, path)
                return path
            except FileExistsError:
                pass
            try:
                holder = int(path.read_text(encoding='utf-8').split()[0])
            except (OSError, ValueError, IndexError):
                raise RunError(f'The campaign lock cannot be read. If no runner is using this campaign, remove {path}.')
            try:
                os.kill(holder, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except OSError:           # for example a process of another user: it exists
                alive = True
            if alive:
                raise RunError(f'Another runner holds this campaign (process {holder}). If that process is not a '
                               f'runner of this campaign, remove {path}.')
            path.unlink()
        raise RunError(f'Could not take the campaign lock: {path}')
    finally:
        draft.unlink()


def freeze(context):
    """Everything that must not change between the first review of a campaign and the last. The Python toolchain
    is recorded only when it is the interpreter in use; with a system interpreter the record is as it always was."""
    frozen = {'schema_version': 1, 'runner_version': RUNNER_VERSION,
            'agent': context['selection']['agent'], 'model': context['selection'].get('model') or '',
            'effort': context['selection'].get('effort') or '', 'minutes': context['minutes'],
            'repeats': context['repeats'], 'seed': context['seed'],
            'arms': {ident: context['arms'][ident]['fingerprint'] for ident in context['arm_ids']},
            'dossiers': {d['dossier']: dossier_hashes(d) for d in context['dossiers']},
            'keys_manifest_sha256': context['sealed']['sha256'],
            'skill_sha256': tree_hash(context['skill'], skip=('__pycache__', '.lake')) if context.get('skill') else None,
            'lean': {k: context['lean'][k] for k in ('path', 'version')} if context.get('lean') else None,
            'ala': context.get('ala'), 'code_sha256': code_hash(),
            'agent_state': 'private_home_per_review' if context.get('home_template') else 'shared_with_other_sessions',
            'agent_home_sha256': tree_hash(context['home_template']) if context.get('home_template') else None,
            # Where the model is reached must not change either. Only a hash is kept: an address can hold a secret.
            'endpoint_sha256': sha_bytes(json.dumps([os.environ.get(name, '') for name in ENDPOINT_VARIABLES]).encode()),
            'arena': str(blindness.real(context['arena']))}
    if context.get('python'):
        frozen['python'] = {key: context['python'][key] for key in ('path', 'root', 'version')}
    return frozen


def open_campaign(context, persist):
    """Create the campaign record, or check that this invocation matches the one that created it."""
    path, frozen = Path(context['out']) / 'campaign.json', freeze(context)
    if path.is_file():
        stored = read_json(path)
        changed = sorted(key for key in frozen if stored.get(key) != frozen[key])
        if changed:
            raise RunError('This campaign was started with other settings. Changed: ' + ', '.join(changed)
                           + '. Use a new --out folder for a new campaign.')
        return stored
    if persist:
        write_json(path, dict(frozen, created_at=now()))
    return frozen


def unit_record(context, unit):
    path = Path(context['out']) / 'units' / (unit['id'] + '.json')
    return read_json(path) if path.is_file() else None


def staged_runs(context, arm):
    """Label -> opaque run id, from the stage records of one arm."""
    index = Path(context['out']) / arm / 'records/index.json'
    if not index.is_file():
        return {}
    return {(label['dossier'], label['variant'], label.get('repeat', 1)): opaque
            for opaque, label in read_json(index).items()}


# ---------- One review ----------

def archive_review(context, unit, record, result, home=None, listed=None):
    """Take a finished or abandoned review out of the arena, build its bundle and write its record."""
    arm, opaque = context['arms'][unit['arm']], record['opaque_id']
    dossier = next(d for d in context['dossiers'] if d['dossier'] == unit['dossier'])
    bundle = Path(context['out']) / arm['id'] / 'runs' / opaque
    bundle.mkdir(parents=True)
    login_changed = close_home(home, listed, bundle / 'agent-home') if home else []
    run = Path(record['arena_run'])
    if run.is_dir() and not run.is_symlink():
        reclaim(run)
        shutil.move(str(run), str(bundle / 'agent'))
    scored = result['ended'] in ('exit_0', 'timeout', 'exit_nonzero')
    sheet = read_sheet(bundle / 'agent') if scored else {'status': 'not_read', 'rows': [], 'dropped': 0}
    built = build_bundle(bundle, dossier['variants'][unit['variant']], dossier['sources'], sheet, arm['lean'])
    changed = []
    for relative, digest in sorted(record['case_files'].items()):
        data = read_plain(bundle / 'agent', relative, CASE_FILE_LIMIT)
        if data is None or sha_bytes(data) != digest:
            changed.append(relative)
    valid = sheet['status'] == 'valid'
    final = {'schema_version': 1, 'unit': unit['id'], 'arm': arm['id'], 'arm_fingerprint': arm['fingerprint'],
             'dossier': unit['dossier'], 'variant': unit['variant'], 'repeat': unit['repeat'], 'opaque_id': opaque,
             'status': 'scored' if valid else 'failed', 'ended': result['ended'], 'exit_code': result.get('exit_code'),
             'seconds': result.get('seconds'), 'detail': result.get('detail'), 'sheet': sheet['status'],
             'sheet_rows': len(sheet['rows']), 'sheet_rows_dropped': sheet['dropped'],
             'suspect': not valid and result['ended'] in ('exit_nonzero', 'launch_error'),
             'supplied_files_changed': changed, 'login_files_changed': login_changed, 'finished_at': now(), **built}
    write_json(bundle / 'run.json', final)
    write_json(Path(context['out']) / 'units' / (unit['id'] + '.json'), final)
    return final


def run_unit(context, unit):
    """Stage one review, let the agent work, then keep what it handed in."""
    arm = context['arms'][unit['arm']]
    dossier = next(d for d in context['dossiers'] if d['dossier'] == unit['dossier'])
    report = dossier['variants'][unit['variant']]
    others = [path for variant, path in dossier['variants'].items() if variant != unit['variant']]
    lean = context.get('lean') if arm['lean'] else None
    text = instructions(arm, context['minutes'], lean['path'] if lean else None, bool(dossier['brief']))
    # Everything that can fail before the agent exists happens before staging: once a review is staged it is final.
    home, listed = open_home(context['home_template'], context['arena']) if context.get('home_template') else (None, None)
    try:
        record = blindness.stage_case(
            report, dossier['sources'], context['arena'], Path(context['out']) / arm['id'] / 'records',
            {'dossier': unit['dossier'], 'variant': unit['variant'], 'repeat': unit['repeat']},
            withheld=others, forbidden_hashes=context['sealed']['hashes'],
            protected=[context['keys'], context['fixtures'], context['out']],
            brief=dossier['brief'], skill=context['skill'] if arm['skill'] else None)
    except blindness.BlindnessError as error:
        if home:
            shutil.rmtree(home)
        raise RunError(f'{unit["id"]}: {error}')
    run = Path(record['arena_run'])
    (run / 'instructions.md').write_text(text, encoding='utf-8')
    # `ala_arguments` refuses only values that `build_context` has already refused, so it cannot fail here.
    command = [context['node']] + ala_arguments(context['ala']['entry'], context['selection'], run,
                                                lean['root'] if lean else None, home, str(uuid.uuid4()),
                                                context['python']['root'] if context.get('python') else None)
    result = context['launch'](command, run, ala_environment(), context['minutes'] * 60,
                               Path(context['out']) / 'logs' / unit['id'])
    return archive_review(context, unit, record, result, home, listed)


def settle_interrupted(context):
    """A review that was staged but has no final record was cut short. It is closed as failed, not run again."""
    settled = []
    for unit in context['units']:
        if unit_record(context, unit):
            continue
        opaque = staged_runs(context, unit['arm']).get((unit['dossier'], unit['variant'], unit['repeat']))
        if not opaque:
            continue
        record = read_json(Path(context['out']) / unit['arm'] / 'records' / (opaque + '.json'))
        bundle = Path(context['out']) / unit['arm'] / 'runs' / opaque
        if bundle.exists():       # the earlier runner stopped while archiving; keep what it left, under a new name
            os.replace(bundle, bundle.with_name(f'{opaque}.partial-{int(time.time())}'))
        settled.append(archive_review(context, unit, record, {'ended': 'interrupted', 'exit_code': None, 'seconds': None}))
    return settled


def arena_entries(context):
    """What is left in the arena. Between reviews it must be empty: every review is moved out when it ends."""
    return sorted(entry.name for entry in Path(context['arena']).iterdir())


def note_probe(context, status, verdicts=()):
    """Keep, with the campaign, whether each invocation checked the sandbox before starting reviews."""
    path = Path(context['out']) / 'probes.json'
    history = read_json(path) if path.is_file() else []
    history.append({'at': now(), 'status': status,
                    'arms': {verdict['arm']: verdict['passed'] for verdict in verdicts}})
    write_json(path, history)


# ---------- The model-free sandbox probe ----------

def shell_quote_safe(*values):
    for value in values:
        if re.search(r"['\r\n\\]", str(value)):
            raise RunError(f'The probe does not support quotes, backslashes or line breaks in paths: {value}')


def probe_script(run, arm, hidden, lean, canary_prefix, python=None, seatbelt=None):
    """A shell script that stands in for the coding agent. Inside the sandbox it records what it can see.
    It contacts no model and no network. `python` is the installed toolchain interpreter (`toolchain_python`), or
    None for a system one. `seatbelt` (macOS only, see `seatbelt_fixtures`) adds the raw observations of the macOS
    witness; the runner, not the script, decides those rows (`seatbelt_verdicts`)."""
    shell_quote_safe(run, canary_prefix, *(path for _, path in hidden), *( [lean['path'], lean['root']] if lean else []),
                     *([python['path'], python['root']] if python else []),
                     *([seatbelt[name] for name in ('sentinel', 'sentinel_marker', 'reader', 'helper_name', 'helper_value')]
                       if seatbelt else []))
    if seatbelt and not all(re.fullmatch(r'[A-Za-z0-9_]+', str(seatbelt[name])) for name in
                            ('sentinel_marker', 'helper_name', 'helper_value', 'runner_pid', 'helper_pid')):
        raise RunError('The macOS probe needs plain markers, names and process numbers')
    lines = [
        '#!/bin/sh',
        '# Simulated coding agent for the model-free blindness probe. It contacts no model and no network.',
        'case " $* " in *" --version "*|*" -V "*) echo "codex-cli 0.0.0-probe"; exit 0;; esac',
        f"RUN='{run}'",
        'OUT="$RUN/probe-results.tsv"',
        '[ -e "$RUN/probe-finished" ] && exit 0',
        'record() { printf \'%s\\t%s\\t%s\\n\' "$1" "$2" "$(printf \'%s\' "$3" | tr \'\\t\\r\\n\' \'   \' | cut -c1-300)" >> "$OUT"; }',
        # Hidden means not visible, not listed and not read: on macOS a hidden path can exist for `test -e`'s purposes
        # or not, but its listing and content must be refused. CTL proves that ls and head work where they may.
        'hidden() { if [ "$CTL" != ok ]; then record "$1" fail "Control listing or read in the run folder failed, so a refusal proves nothing: $2"; '
        'elif [ -e "$2" ] || [ -L "$2" ]; then record "$1" fail "Visible inside the sandbox: $2"; '
        'elif ls -A -- "$2" >/dev/null 2>&1; then record "$1" fail "Listed inside the sandbox: $2"; '
        'elif head -c 1 -- "$2" >/dev/null 2>&1; then record "$1" fail "Read inside the sandbox: $2"; '
        'else record "$1" pass "Not visible, not listed, not read: $2"; fi; }',
        ': > "$OUT" || exit 3',
        'record namespace info "$(readlink /proc/self/ns/mnt 2>/dev/null)"',
        'CTL=fail',
        'case "\n$(ls -A "$RUN" 2>/dev/null)\n" in *"\nprobe-results.tsv\n"*) '
        'if [ -n "$(head -c 1 -- "$OUT" 2>/dev/null)" ]; then CTL=ok; fi;; esac',
        'if [ "$(pwd -P)" = "$RUN" ]; then record cwd pass "$(pwd -P)"; else record cwd fail "Started in $(pwd -P)"; fi',
        'if [ -r "$RUN/case/report.md" ] && [ -d "$RUN/case/sources" ]; then record case_readable pass "case/report.md"; '
        'else record case_readable fail "The staged report or sources cannot be read"; fi',
        'if printf "probe\\n" > "$RUN/probe-write.txt"; then record run_writable pass "$RUN/probe-write.txt"; '
        'else record run_writable fail "Cannot write in the run folder"; fi',
        'if ( : > "$RUN/../probe-outside-$$" ) 2>/dev/null; then record outside_write_refused fail "Wrote beside the run folder"; '
        'else record outside_write_refused pass "Write beside the run folder refused"; fi']
    lines += [f"hidden {name} '{path}'" for name, path in hidden]
    if seatbelt:
        lines += seatbelt_lines(seatbelt)
    # The folders this runner asks ALA to mount are searched for the canary: the run folder, the home, the temporary
    # folders and, for the Lean arm, the toolchain, and the Python toolchain when it is mounted. The search covers
    # binary files, and a missing grep is a failure, not a pass.
    searched = '"$RUN" "$HOME" /tmp ${TMPDIR:+"$TMPDIR"}' + (f" '{lean['root']}'" if arm['lean'] else '') + (
        f" '{python['root']}'" if python else '')
    lines += [
        'if ! command -v grep >/dev/null 2>&1; then record no_canary fail "grep is not available inside the sandbox"; else',
        f" HITS=$(grep -rla -- '{canary_prefix}' {searched} 2>/dev/null | head -n 5)",
        ' if [ -z "$HITS" ]; then record no_canary pass "No answer-key canary in the run folder, the home, /tmp, $TMPDIR or the toolchains"; '
        'else record no_canary fail "$HITS"; fi',
        'fi']
    if python:
        # The runner decides python_available from this raw line; bare_python3 shows what an agent that ignores the
        # appended sentence would run.
        lines += [f"PY='{python['path']}'",
                  'PV=$("$PY" -I -c \'import sys; print(".".join(str(n) for n in sys.version_info[:3]))\' 2>&1); PX=$?',
                  'record python_available raw "exit=$PX version=$PV"',
                  'BP=$(command -v python3 2>/dev/null)',
                  'if [ -n "$BP" ]; then record bare_python3 info "$BP $("$BP" -c \'import sys; print(sys.version.split()[0])\' 2>&1)"; '
                  'else record bare_python3 info "no python3 on PATH"; fi']
    if arm['lean']:
        lines += [f"LEAN='{lean['path']}'",
                  'if [ -x "$LEAN" ] && V=$("$LEAN" --version 2>&1); then record lean_available pass "$V"; '
                  'else record lean_available fail "Lean does not run inside the sandbox: $LEAN"; fi']
    else:
        if lean:
            lines.append(f"hidden lean_hidden '{lean['root']}'")
        lines.append('if command -v lean >/dev/null 2>&1; then record no_lean_on_path fail "$(command -v lean)"; '
                     'else record no_lean_on_path pass "No lean on PATH"; fi')
    if arm['skill']:
        interpreter = '"$PY"' if python else 'python3'    # the interpreter the instructions name
        lines.append(f'if [ -f "$RUN/skill/SKILL.md" ] && {interpreter} -B "$RUN/skill/scripts/markdown_audit.py" --help >/dev/null 2>&1; '
                     'then record skill_usable pass "skill/SKILL.md and its helper"; '
                     f'else record skill_usable fail "The staged skill or {interpreter} is unusable"; fi')
    else:
        lines.append('hidden skill_absent "$RUN/skill"')
    lines += ['record home info "HOME=$HOME files=$(find "$HOME" -type f 2>/dev/null | wc -l)"',
              'record finished pass complete', ': > "$RUN/probe-finished"',
              'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"agent_message","text":"Probe finished."}}\'',
              'exit 0']
    return '\n'.join(lines) + '\n'


# Reads kern.procargs2 (sysctl through syscall 202) of the process in argument 1 and of itself, and looks for NAME=VALUE
# of the variable named in argument 2, with the value taken from its own environment, so the arguments alone never
# satisfy it. The same reader as tools/check-ala-sandbox.mjs. `ps` cannot serve: it is set-id and does not run inside
# the macOS sandbox. No module is loaded.
READER_PERL = r"""my ($pid,$name)=@ARGV; my $needle=$name.'='.($ENV{$name}//'');
sub state { my $p=shift; my $buf="\0" x 262144; my $len=pack('Q',262144);
 return 'denied errno='.($!+0) if syscall(202,pack('i3',1,49,$p),3,$buf,$len,0,0)!=0;
 return index(substr($buf,0,unpack('Q',$len)),$needle)>=0?'found':'absent'; }
print 'other='.state($pid).' own='.state($$)."\n";
"""
# A Node process with a marker variable: not an Apple program, whose environment macOS never shows to another process.
HELPER_SCRIPT = 'process.stdin.on("end",()=>process.exit(0)).resume();setInterval(()=>{},1e6)'
SEATBELT_ROWS = ('confinement', 'other_process_environment_hidden')
EXISTENCE_ORACLE = ('Known residual on macOS: a path outside the mounts can be neither read nor listed, but whether it '
                    'exists is observable; see ALA DS006.')


def seatbelt_lines(seatbelt):
    """The macOS part of the probe script. Each line records what it observed as `raw`: the exit status, and the
    refusal text where the verdict depends on it. Nothing here decides a row."""
    sentinel = Path(seatbelt['sentinel'])
    return [
        f"if [ -x '{SEATBELT}' ]; then NO=$('{SEATBELT}' -p '(version 1)(allow default)' /usr/bin/true 2>&1); NX=$?; "
        "else NO='sandbox-exec is not executable inside'; NX=126; fi",
        'record nested_sandbox raw "exit=$NX | $NO"',
        'KO=$(kill -0 $$ 2>&1); KX=$?',
        f"RO=$(kill -0 {seatbelt['runner_pid']} 2>&1); RX=$?",
        'record runner_signal raw "own=$KX other=$RX | $RO"',
        f"SR=$(cat '{sentinel}' 2>&1); SX=$?",
        f"case \"$SR\" in *'{seatbelt['sentinel_marker']}'*) SM=yes;; *) SM=no;; esac",
        f"SL=$(ls -A '{sentinel.parent}' 2>&1); LX=$?",
        f'case "\n$SL\n" in *"\n{sentinel.name}\n"*) SN=yes;; *) SN=no;; esac',
        'record sentinel_read raw "control=$CTL read=$SX marker=$SM list=$LX listed=$SN"',
        f"OE=$({seatbelt['helper_name']}='{seatbelt['helper_value']}' /usr/bin/perl '{seatbelt['reader']}' "
        f"{seatbelt['helper_pid']} '{seatbelt['helper_name']}' 2>&1); OX=$?",
        'record environment_read raw "exit=$OX | $OE"']


def probe_checks(arm, hidden, lean, python=None, platform='linux'):
    """The rows a probe must record as passed for this arm. On macOS the runner adds the rows it decides itself
    from the probe's raw observations; with the installed Python toolchain, python_available as well."""
    names = ['cwd', 'case_readable', 'run_writable', 'outside_write_refused', 'no_canary', 'finished']
    names += [name for name, _ in hidden]
    names += ['lean_available'] if arm['lean'] else (['lean_hidden'] if lean else []) + ['no_lean_on_path']
    names.append('skill_usable' if arm['skill'] else 'skill_absent')
    if python:
        names.append('python_available')
    if platform == 'darwin':
        names += list(SEATBELT_ROWS)
    return names


def raw_observation(rows, name):
    """The probe's raw line `name` as (values, text), or None. A status other than `raw`, a bare "pass" included,
    counts as no observation: the runner decides these rows."""
    row = rows.get(name)
    if not row or row['status'] != 'raw':
        return None
    head, _, text = row['detail'].partition(' | ')
    return dict(re.findall(r'(\w+)=(\S*)', head)), text


def exit_code(values, name):
    value = values.get(name, '')
    return int(value) if re.fullmatch(r'\d+', value) else None


def seatbelt_verdicts(rows, host):
    """The macOS witness, decided here from the probe's raw lines and from the host controls in `host` (see
    `seatbelt_host`). Each differential holds only when the act is refused inside and works on the host:
    a refusal without its host control proves nothing, and a probe that did not record the act fails it.
    Returns the rows confinement (all three differentials) and other_process_environment_hidden."""
    def control(name):
        given = host.get(name) or {}
        return given.get('ok') is True, given.get('detail') or 'the host control did not run'

    parts, held = [], []
    # 1. A nested sandbox-exec is refused (sandbox_apply: Operation not permitted) and works on the host.
    raw = raw_observation(rows, 'nested_sandbox')
    code = exit_code(raw[0], 'exit') if raw else None
    inside = raw is not None and code not in (None, 0, 126, 127) and 'not permitted' in raw[1]
    works, detail = control('nested_sandbox')
    held.append(inside and works)
    parts.append(f'nested sandbox-exec {"refused" if inside else "NOT refused"} inside '
                 f'({raw[1][:120] if raw else "not attempted"}), host: {detail}')
    # 2. A sentinel in the campaign's probe folder is neither read nor listed inside, and is readable on the host.
    raw = raw_observation(rows, 'sentinel_read')
    values = raw[0] if raw else {}
    inside = (raw is not None and values.get('control') == 'ok' and exit_code(values, 'read') not in (None, 0)
              and values.get('marker') == 'no' and exit_code(values, 'list') not in (None, 0) and values.get('listed') == 'no')
    works, detail = control('sentinel')
    held.append(inside and works)
    parts.append(f'sentinel {"unreadable and unlisted" if inside else "NOT shown hidden"} inside '
                 f'({rows["sentinel_read"]["detail"] if raw else "not attempted"}), host: {detail}')
    # 3. kill -0 <runner pid> is refused (EPERM, not ESRCH) while kill -0 of the probe's own shell works, and it works
    #    on the host.
    raw = raw_observation(rows, 'runner_signal')
    values = raw[0] if raw else {}
    inside = (raw is not None and exit_code(values, 'own') == 0 and exit_code(values, 'other') not in (None, 0)
              and 'not permitted' in raw[1])
    works, detail = control('runner_signal')
    held.append(inside and works)
    parts.append(f'kill -0 of the runner {"refused" if inside else "NOT refused"} inside '
                 f'({raw[1][:120] if raw else "not attempted"}), host: {detail}')
    confinement = {'status': 'pass' if all(held) else 'fail', 'detail': '; '.join(parts)}
    # 4. The environment of an outside helper is refused to the reader, which sees its own; the host reader sees it.
    #    Only the permission refusal counts (errno 1, EPERM). Outside a sandbox a helper that has died answers with
    #    another errno; inside, under ALA's rules, it may answer errno 1 as well, so the host reads after the run
    #    (`seatbelt_host`) are what show the helper was alive.
    raw = raw_observation(rows, 'environment_read')
    inside = (raw is not None and exit_code(raw[0], 'exit') == 0
              and re.fullmatch(r'other=denied errno=1 own=found', raw[1].strip()) is not None)
    works, detail = control('environment')
    environment = {'status': 'pass' if inside and works else 'fail',
                   'detail': f'reader inside: {raw[1].strip()[:120] if raw else "did not run"}'
                             f'{"" if inside else " (needs other=denied errno=1 and own=found)"}; host: {detail}'}
    return {'confinement': confinement, 'other_process_environment_hidden': environment}


def python_verdict(rows, python):
    """python_available from the probe's raw line: the absolute interpreter ran inside and reported the version
    the runner found for the same interpreter on the host."""
    row = rows.get('python_available')
    seen = re.fullmatch(r'exit=0 version=(\d+\.\d+\.\d+)', row['detail'].strip()) if row and row['status'] == 'raw' else None
    ok = seen is not None and seen.group(1) == python['version']
    return {'status': 'pass' if ok else 'fail',
            'detail': f'{python["path"]} inside: {row["detail"] if row and row["status"] == "raw" else "did not run"}; '
                      f'expected {python["version"]}'}


def evaluate_probe(tsv, required, host_namespace, seatbelt=None, python=None):
    """Read the probe's results. Every required row must be present and passed, and the probe must have run
    in another mount namespace than this process: otherwise it looked at the host, not at the sandbox.
    On macOS (`seatbelt`: the host controls, see `seatbelt_host`) there is no mount namespace; the witness is
    instead the confinement row, three differentials that each hold only when an act is refused inside and works
    on the host. Rows the runner decides replace whatever the probe recorded under the same name."""
    rows = {}
    for line in str(tsv or '').splitlines():
        parts = line.split('\t')
        if len(parts) >= 2:
            rows[parts[0]] = {'status': parts[1], 'detail': ' '.join(parts[2:])}
    required, info = list(required), {}
    decided = {}
    if seatbelt is not None:
        decided.update(seatbelt_verdicts(rows, seatbelt))
        required += [name for name in SEATBELT_ROWS if name not in required]
        info['existence_oracle'] = EXISTENCE_ORACLE
    if python:
        decided['python_available'] = python_verdict(rows, python)
        if 'python_available' not in required:
            required.append('python_available')
        if 'bare_python3' in rows:
            info['bare_python3'] = rows['bare_python3']['detail']
    rows.update(decided)
    checks = [{'check': name, 'status': rows.get(name, {}).get('status', 'missing'),
               'detail': rows.get(name, {}).get('detail', 'The probe did not record this check.')} for name in required]
    if seatbelt is not None:
        sandboxed = rows['confinement']['status'] == 'pass'
        checks.append({'check': 'ran_inside_sandbox', 'status': 'pass' if sandboxed else 'fail',
                       'detail': 'macOS: ' + ('all three differentials of confinement hold' if sandboxed
                                              else 'confinement failed, so the probe may have looked at the host')})
    else:
        inside = rows.get('namespace', {}).get('detail', '')
        sandboxed = bool(inside) and bool(host_namespace) and inside != host_namespace
        checks.append({'check': 'ran_inside_sandbox', 'status': 'pass' if sandboxed else 'fail',
                       'detail': f'probe {inside or "unknown"}, runner {host_namespace or "unknown"}'})
    verdict = {'passed': all(check['status'] == 'pass' for check in checks), 'checks': checks,
               'home': rows.get('home', {}).get('detail')}
    if info:
        verdict['info'] = info
    return verdict


def host_namespace():
    try:
        return os.readlink('/proc/self/ns/mnt')
    except OSError:
        return None


def host_run(command, env=None):
    """Run a host control outside any sandbox. Returns (exit status or None, output)."""
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                              env=env if env is not None else {'PATH': '/usr/bin:/bin'})
    except (OSError, subprocess.SubprocessError) as error:
        return None, str(error)
    return done.returncode, (done.stdout + done.stderr).strip()


def seatbelt_fixtures(work, run, node):
    """The host side of the macOS witness, set up before ALA starts: a sentinel in the campaign's probe folder, the
    environment reader in the run folder (the probe can read only what is mounted), and a Node helper process
    that carries a marker variable and ends when its standard input closes. `stop_helper` ends it."""
    marker, value = uuid.uuid4().hex, uuid.uuid4().hex
    sentinel = Path(work) / f'sentinel-{uuid.uuid4().hex[:8]}.txt'
    sentinel.write_text(marker, encoding='utf-8')
    reader = Path(run) / 'probe-environment-reader.pl'
    reader.write_text(READER_PERL, encoding='utf-8')
    name = 'EVALSUITE_PROBE_MARKER_' + uuid.uuid4().hex[:8].upper()
    try:
        helper = subprocess.Popen([str(node), '-e', HELPER_SCRIPT], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', name: value})
    except OSError as error:
        raise RunError(f'The macOS probe cannot start its helper process with {node}: {error}')
    return {'runner_pid': os.getpid(), 'sentinel': str(blindness.real(sentinel)), 'sentinel_marker': marker,
            'reader': str(blindness.real(reader)), 'helper': helper, 'helper_pid': helper.pid,
            'helper_name': name, 'helper_value': value}


def stop_helper(fixtures):
    helper = fixtures['helper']
    try:
        helper.stdin.close()
    except OSError:
        pass
    try:
        helper.wait(timeout=5)
    except subprocess.TimeoutExpired:
        helper.kill()
        helper.wait()


def environment_on_host(fixtures, attempts=40, delay=0.05):
    """The same reader, on the host and with no sandbox, must see the helper's marker. If it cannot, a refusal inside
    would prove nothing."""
    command = ['/usr/bin/perl', fixtures['reader'], str(fixtures['helper_pid']), fixtures['helper_name']]
    for attempt in range(attempts):
        code, out = host_run(command, {'PATH': '/usr/bin:/bin', fixtures['helper_name']: fixtures['helper_value']})
        if code == 0 and out == 'other=found own=found':
            return {'ok': True, 'detail': f'the host reader sees the marker of helper process {fixtures["helper_pid"]}'}
        if attempt + 1 < attempts:
            time.sleep(delay)
    return {'ok': False, 'detail': f'the host reader cannot see the marker of helper process {fixtures["helper_pid"]} '
                                   f'(exit {code}: {out[:200]}), so a refusal inside proves nothing'}


def seatbelt_host(fixtures, before):
    """The "works on the host" half of every macOS differential, run after the probe by this runner, outside any
    sandbox: the same nested sandbox-exec, the same kill -0, the sentinel read back, and the environment reader
    again (`before` is its result from before ALA started)."""
    code, out = host_run([SEATBELT, '-p', '(version 1)(allow default)', '/usr/bin/true'])
    nested = {'ok': code == 0, 'detail': f'{SEATBELT} works on the host' if code == 0
              else f'{SEATBELT} fails on the host too (exit {code}: {out[:200]}), so a refusal inside proves nothing'}
    code, out = host_run(['/bin/sh', '-c', f'kill -0 {int(fixtures["runner_pid"])}'])
    signal_zero = {'ok': code == 0, 'detail': f'kill -0 {fixtures["runner_pid"]} works on the host' if code == 0
                   else f'kill -0 {fixtures["runner_pid"]} fails on the host too (exit {code}: {out[:200]})'}
    try:
        readable = Path(fixtures['sentinel']).read_text(encoding='utf-8') == fixtures['sentinel_marker']
    except OSError:
        readable = False
    sentinel = {'ok': readable, 'detail': f'the host reads {fixtures["sentinel"]}' if readable
                else f'the host cannot read {fixtures["sentinel"]} back, so a refusal inside proves nothing'}
    after = environment_on_host(fixtures, attempts=1)
    environment = {'ok': before['ok'] and after['ok'],
                   'detail': f'before ALA: {before["detail"]}; after: {after["detail"]}'}
    return {'nested_sandbox': nested, 'runner_signal': signal_zero, 'sentinel': sentinel, 'environment': environment}


# Asks ALA's own discovery which program it would start, exactly as `ala` does for a run: with the environment the
# process gets, and with --home, with the environment ALA derives from the real path of that home (src/cli.mjs).
# It reads its inputs from standard input and prints one JSON line. It starts nothing.
DISCOVERY_SCRIPT = r"""import path from 'node:path';
import {realpath} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
let text = '';
for await (const chunk of process.stdin) text += chunk;
const {root, home, agent} = JSON.parse(text);
const load = name => import(pathToFileURL(path.join(root, 'src', 'coding-agents', name)).href);
const {discoverCodingAgents} = await load('discovery.mjs');
const {homeEnvironment} = await load('logins.mjs');
const env = home ? homeEnvironment(process.env, await realpath(home)) : process.env;
const records = await discoverCodingAgents({env});
const record = Array.isArray(records) ? records.find(item => item && item.name === agent) || null : null;
process.stdout.write(JSON.stringify({answer: 'ala-discovery', record}) + '\n');
"""
PROBE_REFUSAL = ('The probe was not run, because ALA would start a real coding agent instead of the probe\'s simulated '
                 'one: {}')
PROBE_FAILED = 'The probe was not run, because the checks before ALA starts failed ({}); nothing was started.'


def ala_discovery(node, entry, agent, env, home, cwd, timeout=60):
    """The program ALA's discovery picks for `agent` when ALA is started with `env` in `cwd` (and --home `home`),
    as a path, or None when it finds none. Discovery that cannot be asked refuses the probe."""
    if not node:
        raise RunError(PROBE_REFUSAL.format("ALA's discovery cannot be asked: node was not found"))
    request = json.dumps({'root': str(Path(entry).parent.parent), 'home': str(home) if home else None, 'agent': agent})
    try:
        done = subprocess.run([str(node), '--input-type=module', '-e', DISCOVERY_SCRIPT], input=request.encode('utf-8'),
                              cwd=str(cwd), env=env, capture_output=True, timeout=timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise RunError(PROBE_REFUSAL.format(f"ALA's discovery cannot be asked: {error}"))
    # Bytes, decoded leniently: whatever discovery prints must end in an answer or a refusal, never in an error.
    stdout, stderr = done.stdout.decode('utf-8', errors='replace'), done.stderr.decode('utf-8', errors='replace')
    lines = stdout.splitlines()
    try:
        answer = json.loads(lines[0]) if done.returncode == 0 and len(lines) == 1 else None
    except (ValueError, RecursionError):
        answer = None
    if not isinstance(answer, dict) or answer.get('answer') != 'ala-discovery':
        output = ' '.join((stderr or stdout).split())[-300:]
        raise RunError(PROBE_REFUSAL.format(f"ALA's discovery gave no usable answer (exit {done.returncode}: {output})"))
    record = answer.get('record')
    if not isinstance(record, dict) or record.get('available') is not True or not isinstance(record.get('binary'), str):
        return None
    return record['binary']


def assert_simulated_agent(node, entry, shim, env, home, cwd):
    """Before ALA starts: the probe script must be an executable regular file and ALA's own discovery, with the
    environment the probe passes, must name it (by real path). ALA skips a CODEX_BIN it cannot execute and falls
    back to <HOME>/.local/bin/codex and to codex on PATH, so the variable alone proves nothing. Returns the real path
    of the program discovery named; raises RunError otherwise, so the probe is not run."""
    shim = Path(shim)
    try:
        info = os.lstat(shim)
        usable = stat.S_ISREG(info.st_mode) and bool(info.st_mode & stat.S_IXUSR) and os.access(str(shim), os.X_OK)
    except OSError:
        usable = False
    binary = ala_discovery(node, entry, 'codex', env, home, cwd)
    real = os.path.realpath(shim)
    if binary is None or os.path.realpath(binary) != real or not usable:
        problem = '' if usable else f'the probe script {shim} is not an executable regular file; '
        raise RunError(PROBE_REFUSAL.format(f'{problem}ALA would run {binary or "no codex at all"} instead of {real}'))
    return real


def discard_before_launch(home, listed, work, run, decoy, tools):
    """Clear the arena of a probe that was refused before ALA started. Every step is tried whatever the others do,
    and the copied login goes first. Only what this probe created is touched: its own home (a copy that open_home
    left half made, open_home removes itself), its run folder, decoy and probe-bin folder; another runner's homes
    in the same arena stay."""
    if home:
        try:
            close_home(home, listed, Path(work) / 'agent-home')
        except Exception:
            pass
        if Path(home).is_dir() and not Path(home).is_symlink():     # close_home failed: the login copy goes anyway
            reclaim(home)
            shutil.rmtree(home, ignore_errors=True)
    try:
        if Path(run).is_dir() and not Path(run).is_symlink():
            reclaim(run)
            shutil.move(str(run), str(Path(work) / 'run'))
    except (OSError, shutil.Error):
        pass
    for folder in (decoy, tools):
        shutil.rmtree(folder, ignore_errors=True)


def probe_arm(context, arm):
    """Stage a small made-up case exactly as a review is staged, start ALA with a simulated agent, and see
    whether anything that must stay hidden is visible from inside."""
    out, arena = Path(context['out']), Path(context['arena'])
    stamp = f'{int(time.time())}-{uuid.uuid4().hex[:6]}'
    work = out / 'probe' / f'{arm["id"]}-{stamp}'
    fixture = work / 'fixture'
    (fixture / 'sources').mkdir(parents=True)
    (fixture / 'report.md').write_text('<a id="C01"></a>\n**C01.** The probe note holds one line.\n', encoding='utf-8')
    (fixture / 'sources/note.md').write_text('<a id="N-01"></a>\n## N-01 — Note\nOne line.\n', encoding='utf-8')
    record = blindness.stage_case(fixture / 'report.md', fixture / 'sources', arena, work / 'records',
                                  {'dossier': 'probe', 'variant': arm['id']}, withheld=[],
                                  forbidden_hashes=context['sealed']['hashes'],
                                  protected=[context['keys'], context['fixtures'], out],
                                  skill=context['skill'] if arm['skill'] else None)
    run, tag = Path(record['arena_run']), record['opaque_id'].split('-', 1)[1]
    decoy, tools = arena / f'decoy-{tag}', arena / f'probe-bin-{tag}'
    home = listed = seatbelt = None
    # Everything up to the launch, the guard included: whatever fails here, nothing was started, and the copied login
    # and the staged folders leave the arena before the probe is refused.
    try:
        decoy.mkdir()
        (decoy / 'other-review.txt').write_text('Another review would be here.\n', encoding='utf-8')
        lean = context.get('lean')
        hidden = [('keys_hidden', blindness.real(context['keys'])), ('records_hidden', blindness.real(out)),
                  ('fixtures_hidden', blindness.real(context['fixtures'])), ('other_reviews_hidden', blindness.real(decoy))]
        if (REPO / 'examples/evaluator').is_dir():
            hidden.append(('repository_keys_hidden', blindness.real(REPO / 'examples/evaluator')))
        tools.mkdir()
        shim = tools / 'codex'
        python = context.get('python')
        # On macOS there is no mount namespace to compare; the witness needs host fixtures (see seatbelt_verdicts).
        seatbelt = seatbelt_fixtures(work, run, context['node']) if sys.platform == 'darwin' else None
        before = environment_on_host(seatbelt) if seatbelt else None
        shim.write_text(probe_script(blindness.real(run), arm, hidden, lean, blindness.CANARY_PREFIX, python, seatbelt),
                        encoding='utf-8')
        os.chmod(shim, 0o755)
        (run / 'instructions.md').write_text('Model-free sandbox probe. The simulated agent ignores this text.\n', encoding='utf-8')
        home, listed = open_home(context['home_template'], arena) if context.get('home_template') else (None, None)
        command = [context['node']] + ala_arguments(context['ala']['entry'], {'agent': 'codex'}, run,
                                                    lean['root'] if lean and arm['lean'] else None, home, None,
                                                    python['root'] if python else None)
        env = dict(ala_environment(credentials=False), CODEX_BIN=str(shim))
        agent_program = assert_simulated_agent(context['node'], context['ala']['entry'], shim, env, home, run)
    except BaseException as error:
        if seatbelt:
            stop_helper(seatbelt)
        discard_before_launch(home, listed, work, run, decoy, tools)
        if isinstance(error, RunError) or not isinstance(error, Exception):
            raise
        raise RunError(PROBE_FAILED.format(f'{type(error).__name__}: {error}')) from error
    try:
        result = context['launch'](command, run, env, PROBE_SECONDS, work / 'ala')
        host = seatbelt_host(seatbelt, before) if seatbelt else None
    finally:
        if seatbelt:
            stop_helper(seatbelt)
    results = run / 'probe-results.tsv'
    tsv = results.read_text(encoding='utf-8', errors='replace') if results.is_file() and not results.is_symlink() else ''
    verdict = evaluate_probe(tsv, probe_checks(arm, hidden, lean, python, sys.platform), host_namespace(), host, python)
    if home:
        close_home(home, listed, work / 'agent-home')      # the copied login is not evidence and is not kept
    reclaim(run)
    shutil.move(str(run), str(work / 'run'))
    shutil.rmtree(decoy)
    shutil.rmtree(tools)
    verdict.update(arm=arm['id'], ala_ended=result['ended'], ala_exit_code=result.get('exit_code'), evidence=str(work),
                   agent_program=agent_program)
    write_json(work / 'probe.json', verdict)
    return verdict


def probe_platform_ready(platform=None, which=shutil.which, seatbelt=SEATBELT):
    """Linux with Bubblewrap, or macOS with an executable sandbox-exec: the two sandboxes ALA runs agents in."""
    platform = sys.platform if platform is None else platform
    if platform.startswith('linux'):
        return bool(which('bwrap'))
    if platform == 'darwin':
        return os.path.isfile(seatbelt) and os.access(seatbelt, os.X_OK)
    return False


def run_probe(context):
    if not probe_platform_ready():
        raise RunError('The probe needs Linux with Bubblewrap or macOS with sandbox-exec, because ALA runs agents '
                       'only inside a sandbox.')
    return [probe_arm(context, context['arms'][ident]) for ident in context['arm_ids']]


# ---------- Command line ----------

def build_context(args, need_ala):
    python = toolchain_python()       # None with a system interpreter: then nothing about Python changes
    arms = load_arms(args.arms_file, python)
    arm_ids = [name for name in args.arms.split(',') if name]
    for name in arm_ids:
        if name not in arms:
            raise RunError(f'Unknown arm "{name}". Available: {", ".join(arms)}')
    if not arm_ids or len(set(arm_ids)) != len(arm_ids):
        raise RunError('Name each arm once in --arms')
    if args.minutes < 1 or args.repeats < 1:
        raise RunError('--minutes and --repeats need positive numbers')
    for folder, name in ((args.fixtures, '--fixtures'), (args.keys, '--keys')):
        if not folder.is_dir():
            raise RunError(f'{name} is not an existing folder: {folder}')
    dossiers = discover(args.fixtures)
    if args.dossiers:
        wanted = [name for name in args.dossiers.split(',') if name]
        unknown = sorted(set(wanted) - {d['dossier'] for d in dossiers})
        if unknown:
            raise RunError('No such dossier in --fixtures: ' + ', '.join(unknown))
        dossiers = [d for d in dossiers if d['dossier'] in wanted]
    if not dossiers:
        raise RunError(f'No dossier with a report and a sources folder in {args.fixtures}')
    sealed = sealed_keys(args.keys)
    unkeyed = sorted(d['dossier'] for d in dossiers if d['dossier'] not in sealed['dossiers'])
    if unkeyed:
        raise RunError('The sealed keys name no key for: ' + ', '.join(unkeyed))
    if not AGENT_ID.fullmatch(args.agent or ''):
        raise RunError('--agent names the coding agent ALA should use, for example codex')
    # Checked here, before anything is staged: a value ALA would read as an option must not cost a review.
    if any(value.startswith('-') or re.search(r'[\r\n\0]', value) for value in (args.model or '', args.effort or '')):
        raise RunError('--model and --effort must not start with "-" or hold a line break')
    context = {'arms': arms, 'arm_ids': arm_ids, 'dossiers': dossiers, 'sealed': sealed,
               'fixtures': args.fixtures.resolve(), 'keys': args.keys.resolve(), 'arena': args.arena.resolve(),
               'out': args.out.resolve(), 'minutes': args.minutes, 'repeats': args.repeats, 'seed': args.seed,
               'selection': {'agent': args.agent, 'model': args.model or '', 'effort': args.effort or ''},
               'skill': (args.skill.resolve() if any(arms[i]['skill'] for i in arm_ids) else None),
               'lean': None, 'home_template': args.agent_home.resolve() if args.agent_home else None,
               'launch': launch, 'probe': run_probe, 'node': shutil.which('node'), 'ala': None, 'python': python}
    if context['skill'] and not (context['skill'] / 'SKILL.md').is_file():
        raise RunError(f'--skill is not a skill folder: {context["skill"]}')
    if any(arms[i]['lean'] for i in arm_ids):
        if not args.lean:
            raise RunError('An arm that uses Lean needs --lean with the absolute path of the toolchain executable')
        context['lean'] = lean_identity(args.lean)
    if context['home_template']:
        check_home_template(context['home_template'])
    context['agent_roots'] = check_layout(context)
    context['units'] = plan_units(dossiers, arm_ids, args.repeats, args.seed)
    if need_ala:
        if not context['node']:
            raise RunError('node was not found on PATH; ALA needs it')
        context['ala'] = locate_ala(args.ala)
    return context


def describe(context):
    units, reports = context['units'], sum(len(d['variants']) for d in context['dossiers'])
    hours = len(units) * context['minutes'] / 60
    lines = [f'Campaign {context["out"]}: {len(context["dossiers"])} dossier(s), {reports} report(s) × '
             f'{len(context["arm_ids"])} arm(s) × {context["repeats"]} repeat(s) = {len(units)} review(s).',
             f'Each review: up to {context["minutes"]} minutes with {context["selection"]["agent"]}'
             + (f' / {context["selection"]["model"]}' if context['selection']['model'] else ' / agent default model')
             + f'. At most {hours:.1f} hours of model usage.',
             'Agent-readable folders: ' + ', '.join(context['agent_roots']) + '.',
             'Agent state: ' + ('a private home per review, copied from --agent-home.' if context['home_template']
                                else 'SHARED with other sessions of this user (no --agent-home).')]
    return '\n'.join(lines)


def next_steps(context):
    roots = ' '.join(f'--agent-root {root}' for root in context['agent_roots'])
    lines = ['Score each arm, then compare them:']
    for ident in context['arm_ids']:
        base = Path(context['out']) / ident
        lines.append(f'  python3 evalsuite/scorecard.py --runs {base / "runs"} --keys {context["keys"]} --records '
                     f'{base / "records"} {roots} --gate evalsuite/thresholds.json --output {base / "scorecard.json"}')
    lines.append(f'  python3 evalsuite/compare.py --campaign {context["out"]} --keys {context["keys"]} '
                 f'--ladder {",".join(context["arm_ids"])}')
    return '\n'.join(lines)


def command_run(context, args):
    if not context['home_template'] and not args.shared_agent_state:
        raise RunError('Pass --agent-home with a login made for this campaign, so that no review can read the '
                       'session history of another. To accept shared agent state instead, pass --shared-agent-state; '
                       'the campaign is then not blind against earlier sessions.')
    lock = acquire_lock(context['out'])
    try:
        open_campaign(context, persist=True)
        stop = Path(context['out']) / 'stopped.json'
        if stop.is_file():
            raise RunError(f'This campaign was stopped ({read_json(stop).get("reason")}). Its reviews stay as they are. '
                           'Start a new campaign in a new --out folder.')
        for final in settle_interrupted(context):
            print(f'{final["unit"]}: closed as failed (interrupted in an earlier invocation)')
        remove_stale_homes(context['arena'])
        left = arena_entries(context)
        if left:
            raise RunError(f'The arena holds entries no pending review explains: {", ".join(left[:5])}. '
                           'Use an empty arena for this campaign.')
        pending = [unit for unit in context['units'] if not unit_record(context, unit)]
        if pending and args.skip_probe:
            note_probe(context, 'skipped')
            print('The sandbox probe was skipped. The comparison will say so.')
        elif pending:
            verdicts = context['probe'](context)
            failed = [verdict for verdict in verdicts if not verdict['passed']]
            note_probe(context, 'failed' if failed else 'passed', verdicts)
            for verdict in failed:
                bad = ', '.join(c['check'] for c in verdict['checks'] if c['status'] != 'pass')
                print(f'Probe failed for arm {verdict["arm"]}: {bad}. Evidence: {verdict["evidence"]}')
            if failed:
                raise RunError('The sandbox probe failed, so no review was started.')
            print('Sandbox probe passed for every arm.')
        print(f'{len(context["units"]) - len(pending)} review(s) already final, {len(pending)} to run.')
        streak = {}
        for number, unit in enumerate(pending, 1):
            final = run_unit(context, unit)
            print(f'[{number}/{len(pending)}] {unit["id"]}: {final["status"]} ({final["ended"]}, sheet {final["sheet"]}, '
                  f'{final["sheet_rows"]} row(s))', flush=True)
            streak[unit['arm']] = streak.get(unit['arm'], 0) + 1 if final['suspect'] else 0
            if final['ended'] == 'launch_error' or streak[unit['arm']] >= SUSPECT_LIMIT:
                reason = f'arm {unit["arm"]} kept ending without an answer sheet: {final.get("detail") or final["ended"]}'
                # The stop is kept with the campaign: carrying on after an outage would charge it to one arm only.
                write_json(Path(context['out']) / 'stopped.json', {'at': now(), 'unit': unit['id'], 'reason': reason})
                print(f'Stopped: {reason}. These reviews stay failed. Fix the cause and start a new campaign in a new folder.')
                return 2
        print(next_steps(context))
        return 0
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def main(argv=None, overrides=None):
    """`overrides` replaces parts of the context (the launcher, the probe, ALA); the tests use it to run
    campaigns with a stand-in agent."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('plan', 'probe', 'run'))
    parser.add_argument('--fixtures', type=Path, required=True, help='Folder of dossiers (reports and sources)')
    parser.add_argument('--keys', type=Path, required=True, help='Sealed answer keys; never opened, only kept away')
    parser.add_argument('--arena', type=Path, required=True, help='Empty folder where reviews are staged')
    parser.add_argument('--out', type=Path, required=True, help='Campaign folder for records and finished reviews')
    parser.add_argument('--arms', default='plain,skill-no-lean,skill-lean')
    parser.add_argument('--arms-file', type=Path, default=HERE / 'arms.json')
    parser.add_argument('--agent', default='codex', help='Coding agent ALA should use')
    parser.add_argument('--model', help='Model, the same for every arm')
    parser.add_argument('--effort')
    parser.add_argument('--minutes', type=int, required=True, help='Time limit of one review, the same for every arm')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--dossiers', help='Comma-separated dossier names; all when omitted')
    parser.add_argument('--skill', type=Path, default=REPO / 'skills/evidence-to-proof-review')
    parser.add_argument('--lean', type=Path, help='Absolute path of the Lean executable for the arm that uses Lean')
    parser.add_argument('--ala', type=Path, help='AdvancedLanguageAgent folder')
    parser.add_argument('--agent-home', type=Path, help='A home folder that holds only a login made for this campaign')
    parser.add_argument('--shared-agent-state', action='store_true',
                        help='Run without --agent-home; reviews can then read earlier session history')
    parser.add_argument('--skip-probe', action='store_true', help='Start reviews without the sandbox probe')
    args = parser.parse_args(argv)
    try:
        context = build_context(args, need_ala=args.command != 'plan' and 'ala' not in (overrides or {}))
        context.update(overrides or {})
        if args.command == 'run' and not args.model:
            raise RunError('--model is required for a campaign, so every arm uses the same named model')
        print(describe(context))
        if args.command == 'plan':
            open_campaign(context, persist=False)
            print('Plan only. Nothing was staged and no agent was started.')
            return 0
        if args.command == 'probe':
            verdicts = context['probe'](context)
            for verdict in verdicts:
                print(f'Arm {verdict["arm"]}: {"passed" if verdict["passed"] else "FAILED"} (evidence: {verdict["evidence"]})')
                for check in verdict['checks']:
                    print(f'  {check["status"]:7} {check["check"]}: {check["detail"]}')
            return 0 if all(verdict['passed'] for verdict in verdicts) else 1
        return command_run(context, args)
    except RunError as error:
        print(f'Cannot run: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
