#!/usr/bin/env python3
"""Freeze, plan and run retained proof rechecks.

inventory and plan never execute Lean. run executes the planned proof jobs one at
a time through recheck_sandbox.run_job, only after check_gate accepts the verifier
receipt for the runtime actually used; there is no option that skips the gate or
the executor's filters. A run directory is a journal: finished jobs are kept, an
interrupted or aborted run resumes only with identical code, runtime, gate and
input hashes, and export renders lean-recheck.json/.md per cell from the journal.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
sys.path.insert(0, str(ROOT / 'evalsuite'))
import audit
import markdown_proofs
import scorecard

FILE_LIMIT = 1024 * 1024
TOTAL_LIMIT = 16 * FILE_LIMIT
JSON_DEPTH = 32
CATEGORIES = ('workspace_record', 'explicit_reference', 'name_match',
              'ambiguous_or_stale', 'unlinked_lean', 'no_lean')
CELLS = ('X3', 'C4', 'X1')
PARTS = tuple(f'p2lean{i}' for i in range(1, 6))
BASELINES = {'X3': 668, 'C4': 653, 'X1': 122}
REVIEWS_PER_CELL = 120
WORK = ROOT / 'tmp/recheck'
# Mirrors recheck_sandbox.REASONS/OUTCOMES (a test compares them); the run refuses
# any executor result outside these sets instead of recording it.
REASONS = ('input_limit', 'invalid_metadata', 'unsafe_path', 'source_changed',
           'lexical_rejection', 'strict_rejection', 'dependency_missing', 'dependency_cycle',
           'compile_error', 'replay_failed', 'target_mismatch', 'axioms_disallowed', 'axioms_missing',
           'output_limit', 'memory_limit', 'process_limit', 'scratch_limit',
           'timeout', 'signal', 'infrastructure_error', 'interrupted')
OUTCOMES = ('pass', 'fail', 'filter_rejected', 'timeout', 'not_linked', 'not_run')
# What the executor may return for a production job; verifier_probe and
# not_linked are never accepted.
EXECUTOR_OUTCOMES = ('pass', 'fail', 'filter_rejected', 'timeout', 'not_run')
FILTER_REASONS = ('lexical_rejection', 'strict_rejection')
ABORT_REASONS = ('infrastructure_error', 'interrupted')
# Always shown in the Markdown table, even when zero.
REASON_ROWS = ('lexical_rejection', 'strict_rejection', 'replay_failed', 'target_mismatch', 'compile_error',
               'axioms_disallowed', 'timeout', 'source_changed', 'infrastructure_error')
RESULT_FIELDS = ('schema_version', 'outcome', 'reason', 'detail', 'argv', 'steps', 'exit_code', 'signal',
                 'seconds', 'axioms', 'checks', 'log_paths', 'log_hashes', 'truncated', 'lean_subprocesses',
                 'runtime_hash', 'evidence')
ALLOWED_AXIOMS = ('Classical.choice', 'Quot.sound', 'propext')
HISTORY_DIRS = {'proof-attempts', 'validation-history', 'phase-artifacts'}


class RecheckError(ValueError):
    def __init__(self, reason, detail):
        self.reason = reason
        super().__init__(f'{reason}: {detail}')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return (json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def safe_parts(path):
    if not isinstance(path, str) or not path or '\\' in path or '\0' in path:
        raise RecheckError('unsafe_path', repr(path))
    parts = path.split('/')
    if any(p in ('', '.', '..', 'agent-home') for p in parts) or len(parts) > 64:
        raise RecheckError('unsafe_path', path)
    return parts


class SafeTree:
    """All retained reads use pinned directory descriptors and O_NOFOLLOW.

    Root components are traversed from / as well. Nonblocking leaf opens avoid
    FIFO hangs; regular files must have one link and stable identity/stat/bytes.
    """
    def __init__(self, root):
        path = os.path.abspath(root)
        self.fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in safe_parts(path.lstrip('/')):
                new = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
                os.close(self.fd)
                self.fd = new
        except (OSError, RecheckError) as exc:
            os.close(self.fd)
            raise RecheckError('unsafe_path', str(exc)) from exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.fd)

    def _parent(self, path):
        parts = safe_parts(path)
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd, parts[-1]
        except OSError as exc:
            os.close(fd)
            raise RecheckError('unsafe_path', f'{path}: {exc}') from exc

    def names(self, path=None):
        if path is None:
            fd = os.dup(self.fd)
        else:
            parent, name = self._parent(path)
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except OSError as exc:
                raise RecheckError('unsafe_path', f'{path}: {exc}') from exc
            finally:
                os.close(parent)
        try:
            names = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > 16384:
                        raise RecheckError('input_limit', 'Directory entry budget exceeded')
            return sorted(names)
        finally:
            os.close(fd)

    def info(self, path):
        parent, name = self._parent(path)
        try:
            return os.stat(name, dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(parent)

    def read(self, path, limit=FILE_LIMIT):
        parent, name = self._parent(path)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError as exc:
            raise RecheckError('unsafe_path', f'{path}: {exc}') from exc
        finally:
            os.close(parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise RecheckError('unsafe_path', f'Not a singly linked regular file: {path}')
            if before.st_size > limit:
                raise RecheckError('input_limit', path)
            def bounded():
                chunks, total = [], 0
                while True:
                    chunk = os.read(fd, min(65536, limit + 1 - total))
                    if not chunk:
                        return b''.join(chunks)
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > limit:
                        raise RecheckError('input_limit', path)
            data = bounded()
            os.lseek(fd, 0, os.SEEK_SET)
            again = bounded()
            after = os.fstat(fd)
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
            if data != again or signature(before) != signature(after):
                raise RecheckError('source_changed', path)
            return data
        finally:
            os.close(fd)


def parse_json(data, label='JSON'):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise RecheckError('invalid_metadata', f'Duplicate field {k} in {label}')
            result[k] = v
        return result
    # Check depth before the JSON parser recurses, ignoring quoted delimiters.
    depth, quoted, escaped = 0, False, False
    try:
        text = data.decode('utf-8')
        for c in text:
            if quoted:
                if escaped:
                    escaped = False
                elif c == '\\':
                    escaped = True
                elif c == '"':
                    quoted = False
            elif c == '"':
                quoted = True
            elif c in '[{':
                depth += 1
                if depth > JSON_DEPTH:
                    raise RecheckError('input_limit', f'JSON depth in {label}')
            elif c in ']}':
                depth -= 1
        return json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, RecheckError):
            raise
        raise RecheckError('invalid_metadata', f'{label}: {exc}') from exc


def text_bytes(data, label):
    try:
        return data.decode('utf-8')
    except UnicodeError as exc:
        raise RecheckError('invalid_metadata', f'Non-UTF8 text: {label}') from exc


def object_fields(value, required, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise RecheckError('invalid_metadata', f'Unsupported or missing fields in {label}')
    return value


def capture_review(tree, prefix):
    """Read only selected metadata and Lean text; never inspect agent-home.

    Supplied skill/tool installations and old build products are not evidence.
    All agent work directories (including run/runs/audit/models) are walked.
    Keys/reports remain metadata in memory, never part of staged source trees.
    """
    result, counts, totals = {}, Counter(), Counter()
    entries = 0
    def read(relative):
        kind = 'source' if relative.endswith('.lean') else 'metadata'
        data = tree.read(prefix + '/' + relative)
        counts[kind] += 1
        totals[kind] += len(data)
        if counts[kind] > (256 if kind == 'source' else 4096) or totals[kind] > TOTAL_LIMIT:
            raise RecheckError('input_limit', f'{kind} budget in {prefix}')
        if relative.endswith('.json'):
            parse_json(data, relative)
        text_bytes(data, relative)
        result[relative] = data
    def walk(relative, depth=0):
        nonlocal entries
        if depth > 32:
            raise RecheckError('input_limit', f'Directory depth in {prefix}')
        for name in tree.names(prefix + '/' + relative):
            if name in {'agent-home', '.git', '.lake', '__pycache__', 'node_modules'}:
                continue
            path = relative + '/' + name
            entries += 1
            if entries > 16384:
                raise RecheckError('input_limit', f'Directory entries in {prefix}')
            info = tree.info(prefix + '/' + path)
            if stat.S_ISLNK(info.st_mode):
                raise RecheckError('unsafe_path', path)
            if stat.S_ISDIR(info.st_mode):
                walk(path, depth + 1)
            elif Path(name).suffix.lower() in {'.md', '.json', '.lean', '.log'}:
                read(path)
    for relative in ('run.json', 'findings.json', 'input/report-ledger.json'):
        read(relative)
    # The runner's supplied inputs are immutable; no need to copy source documents.
    for name in tree.names(prefix + '/agent'):
        if name in {'agent-home', '.ala', '.git', '.lake', 'skill', 'case', '__pycache__', 'node_modules'}:
            continue
        relative = 'agent/' + name
        info = tree.info(prefix + '/' + relative)
        if stat.S_ISLNK(info.st_mode):
            raise RecheckError('unsafe_path', relative)
        if stat.S_ISDIR(info.st_mode):
            walk(relative)
        elif Path(name).suffix.lower() in {'.md', '.json', '.lean', '.log'}:
            read(relative)
    return result


RUN_FIELDS = {'schema_version', 'unit', 'arm', 'arm_fingerprint', 'dossier', 'variant', 'repeat',
              'opaque_id', 'status', 'ended', 'exit_code', 'seconds', 'detail', 'sheet', 'sheet_rows',
              'sheet_rows_dropped', 'suspect', 'supplied_files_changed', 'login_files_changed',
              'finished_at', 'findings', 'formal_claims_stripped', 'claims_in_report'}
FINDING_FIELDS = {'claim_id', 'report_anchor', 'semantic_state', 'formal_status', 'argument_status',
                  'evidence', 'explanation', 'reasoning'}


def review_metadata(blobs):
    run = object_fields(parse_json(blobs['run.json']),
                        {'unit', 'dossier', 'variant', 'repeat', 'status', 'ended', 'finished_at'}, RUN_FIELDS, 'run')
    if any(not isinstance(run.get(k), str) for k in ('unit', 'dossier', 'variant', 'status', 'ended', 'finished_at', 'arm')):
        raise RecheckError('invalid_metadata', 'Review labels/status must be strings')
    if run['status'] not in {'scored', 'failed'} or run['ended'] not in {'exit_0', 'exit_nonzero', 'timeout'} or not run['finished_at']:
        raise RecheckError('invalid_metadata', 'Not a finished review')
    if run.get('arm') != 'skill-lean' or run['variant'] not in {'mixed', 'correct'} or type(run['repeat']) is not int or run['repeat'] < 1:
        raise RecheckError('invalid_metadata', 'Invalid review labels')
    ledger = parse_json(blobs['input/report-ledger.json'])
    if not isinstance(ledger, dict) or not all(isinstance(v, str) for v in ledger.values()):
        raise RecheckError('invalid_metadata', 'Invalid report ledger')
    anchors = {}
    for anchor, paragraph in ledger.items():
        match = scorecard.CLAIM_HEADING.match(paragraph.strip())
        if match:
            if match[1] in anchors:
                raise RecheckError('invalid_metadata', 'Duplicate report claim')
            anchors[match[1]] = anchor
    findings = parse_json(blobs['findings.json'])
    if not isinstance(findings, list):
        raise RecheckError('invalid_metadata', 'Findings must be an array')
    seen = set()
    for row in findings:
        object_fields(row, {'claim_id', 'report_anchor', 'semantic_state', 'formal_status'}, FINDING_FIELDS, 'finding')
        if any(not isinstance(row[k], str) for k in ('claim_id', 'report_anchor', 'semantic_state', 'formal_status')):
            raise RecheckError('invalid_metadata', 'Finding identifiers/verdicts must be strings')
        if row['claim_id'] in seen or row['report_anchor'] not in ledger:
            raise RecheckError('invalid_metadata', 'Duplicate claim or unknown report anchor')
        if row['semantic_state'] not in scorecard.STATES or row['formal_status'] not in audit.STATES['formal_status']:
            raise RecheckError('invalid_metadata', 'Unsupported verdict')
        if not isinstance(row.get('explanation', ''), str):
            raise RecheckError('invalid_metadata', 'Non-string explanation')
        if not isinstance(row.get('evidence', []), list):
            raise RecheckError('invalid_metadata', 'Evidence must be an array')
        for item in row.get('evidence', []):
            object_fields(item, {'path', 'anchor', 'quote'}, {'path', 'anchor', 'quote'}, 'evidence')
            if any(not isinstance(v, str) for v in item.values()):
                raise RecheckError('invalid_metadata', 'Evidence values must be strings')
        seen.add(row['claim_id'])
    return run, ledger, anchors, findings


def code_hashes():
    paths = ['evalsuite/recheck.py', 'evalsuite/recheck_sandbox.py', 'evalsuite/scorecard.py', 'evalsuite/blindness.py',
             'skills/evidence-to-proof-review/scripts/audit.py',
             'skills/evidence-to-proof-review/scripts/markdown_proofs.py']
    return {p: digest((ROOT / p).read_bytes()) for p in paths}


def new_output(path):
    """Only fresh directories under this worktree's tmp; no symlink parents."""
    absolute = Path(os.path.abspath(path))
    temporary = ROOT / 'tmp'
    try:
        relative = absolute.relative_to(temporary)
    except ValueError as exc:
        raise RecheckError('unsafe_path', 'Output must be under the copied worktree tmp/') from exc
    if not relative.parts:
        raise RecheckError('unsafe_path', 'Output must be a new run directory')
    current = ROOT
    for name in ('tmp', *relative.parts):
        safe_parts(name)
        current = current / name
        if current.is_symlink():
            raise RecheckError('unsafe_path', str(current))
        if current == absolute:
            current.mkdir(mode=0o700, exist_ok=False)
        else:
            current.mkdir(mode=0o700, exist_ok=True)
    return absolute


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def inventory(campaign_root, cells, parts, out):
    if not cells or len(set(cells)) != len(cells) or set(cells) - set(CELLS) or not parts or len(set(parts)) != len(parts) or set(parts) - set(PARTS):
        raise RecheckError('invalid_metadata', 'Only unique X3/C4/X1 and p2lean1–5 selections are permitted')
    destination = new_output(out)
    frozen = {'schema_version': 1, 'id': destination.name, 'created_utc': datetime.now(timezone.utc).isoformat(),
              'base_commit': '6c80ee3b71394d5e71f39519070931434373c84b', 'code_hashes': code_hashes(),
              'campaign_root': os.path.abspath(campaign_root), 'cells': list(cells), 'parts': list(parts),
              'reviews': [], 'control_hashes': {}, 'quarantined': [], 'excluded': []}
    with SafeTree(campaign_root) as tree:
        def control(path):
            data = tree.read(path)
            frozen['control_hashes'][path] = digest(data)
            return parse_json(data, path)
        for part in parts:
            key_root = f'lean/{part}/keys-sealed'
            manifest_path = key_root + '/KEYS-MANIFEST.json'
            manifest = control(manifest_path)
            object_fields(manifest, {'schema_version', 'files', 'canary'}, {'schema_version', 'files', 'canary'}, manifest_path)
            for name, entry in manifest['files'].items():
                if len(safe_parts(name)) != 1 or not name.endswith('.json'):
                    raise RecheckError('unsafe_path', 'Key name')
                path = key_root + '/' + name
                control(path)
                if frozen['control_hashes'][path] != entry.get('sealed_sha256'):
                    raise RecheckError('source_changed', f'Sealed key hash: {path}')
            for cell in cells:
                base = f'runs/{part}/{cell}/lean/out'
                campaign = control(base + '/campaign.json')
                if campaign.get('keys_manifest_sha256') != frozen['control_hashes'][manifest_path]:
                    raise RecheckError('invalid_metadata', f'Campaign/key manifest mismatch: {part}/{cell}')
                index = control(base + '/skill-lean/records/index.json')
                if not isinstance(index, dict):
                    raise RecheckError('invalid_metadata', 'Invalid roster')
                labels = set()
                for name, label in sorted(index.items()):
                    if len(safe_parts(name)) != 1:
                        raise RecheckError('unsafe_path', 'Roster name')
                    object_fields(label, {'dossier', 'variant', 'repeat'}, {'dossier', 'variant', 'repeat'}, 'roster label')
                    identity = (label['dossier'], label['variant'], label['repeat'])
                    if identity in labels:
                        raise RecheckError('invalid_metadata', 'Duplicate roster labels')
                    labels.add(identity)
                    prefix = base + '/skill-lean/runs/' + name
                    try:
                        final = parse_json(tree.read(prefix + '/run.json'))
                        object_fields(final, {'status', 'ended'}, RUN_FIELDS, 'run')
                        if any(not isinstance(final[k], str) for k in ('status', 'ended')):
                            raise RecheckError('invalid_metadata', 'Review status/ended must be strings')
                        if final.get('status') not in {'scored', 'failed'} or not final.get('finished_at') or final.get('ended') not in {'exit_0', 'exit_nonzero', 'timeout'}:
                            frozen['excluded'].append({'path': prefix, 'reason': 'unfinished'})
                            continue
                        blobs = capture_review(tree, prefix)
                        run, _, _, findings = review_metadata(blobs)
                        if any(run.get(k) != label[k] for k in label) or run.get('opaque_id') != name:
                            raise RecheckError('invalid_metadata', 'Roster and review labels disagree')
                        frozen['reviews'].append({'cell': cell, 'part': part, 'path': prefix,
                            'unit': part + '/' + cell + '/' + run['unit'], 'dossier': run['dossier'],
                            'variant': run['variant'], 'repeat': run['repeat'],
                            'input_hashes': {p: digest(b) for p, b in sorted(blobs.items())},
                            'reported': sum(f['formal_status'] in scorecard.FORMAL_CERTAIN for f in findings),
                            'registration_profile': 'legacy'})
                    except (RecheckError, OSError) as exc:
                        frozen['quarantined'].append({'path': prefix, 'cell': cell, 'part': part,
                            'reason': getattr(exc, 'reason', 'unsafe_path'), 'detail': str(exc)})
    frozen['complete'] = not frozen['quarantined'] and not frozen['excluded']
    atomic_write(destination / 'inventory.json', canonical(frozen))
    return frozen


def sections(text):
    result = {}
    for match in re.finditer(r'^## ([^\n]+)\n(.*?)(?=^## |\Z)', text, re.M | re.S):
        if match[1].strip() in result:
            raise RecheckError('invalid_metadata', 'Duplicate Markdown section')
        result[match[1].strip()] = match[2].strip()
    return result


def fields(text):
    result = {}
    for match in re.finditer(r'^- ([^:\n]+): (.*)$', text, re.M):
        key = match[1].strip()
        if key in result:
            raise RecheckError('invalid_metadata', 'Duplicate Markdown field ' + key)
        result[key] = match[2].strip().strip('`')
    return result


def table_rows(text):
    lines = [line for line in text.splitlines() if line.startswith('|')]
    if not lines:
        return []
    split = lambda line: [v.strip().replace('&#124;', '|') for v in line.strip().strip('|').split('|')]
    header = split(lines[0])
    if len(set(header)) != len(header):
        raise RecheckError('invalid_metadata', 'Duplicate table fields')
    result = []
    for line in lines[1:]:
        values = split(line)
        if all(re.fullmatch(r':?-+:?', v) for v in values):
            continue
        if len(values) != len(header):
            raise RecheckError('invalid_metadata', 'Malformed Markdown table')
        result.append(dict(zip(header, values)))
    return result


NAME = r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*"


def validate_names(module, theorem):
    if not isinstance(module, str) or not re.fullmatch(audit.MODULE_NAME, module) or not isinstance(theorem, str) or not re.fullmatch(NAME, theorem):
        raise RecheckError('lexical_rejection', 'Unsupported module/theorem name')
    if module.split('.')[0] in {'Init', 'Std', 'Lean'}:
        raise RecheckError('lexical_rejection', 'Reserved module shadowing')


def declarations(text):
    """Conservative name/type extraction, independent of whether source passes.

    Binder-free explicit types only. Ambient section variables or syntax that
    would require a Lean parser leave the type unknown and block fallback.
    """
    try:
        code = audit.lean_code(text)
        literal_code = audit.lean_code(text, mask_strings=False)
    except audit.AuditError:
        return {}
    result, namespaces, offset = {}, [], 0
    ambient = bool(re.search(r'^\s*(?:variable|variables|section|universe|universes)\b', code, re.M))
    for line in code.splitlines(keepends=True):
        ns = re.fullmatch(r'\s*namespace\s+(' + audit.MODULE_NAME + r')\s*', line)
        end = re.fullmatch(r'\s*end(?:\s+(' + audit.MODULE_NAME + r'))?\s*', line)
        dec = re.match(r'\s*(?:(?:private|protected|noncomputable)\s+)*(?:theorem|lemma)\s+(' + NAME + r')(?=\s|:)', line)
        if ns:
            namespaces.append(ns[1])
        elif end and namespaces and (end[1] is None or end[1] == namespaces[-1]):
            namespaces.pop()
        elif dec:
            name = '.'.join(namespaces + [dec[1]])
            tail = code[offset + dec.end():]
            target = None
            if not ambient and tail.lstrip().startswith(':'):
                # := inside a binder/type is unsupported, never guessed around.
                before, separator, _ = tail.lstrip()[1:].partition(':=')
                if separator and len(before) <= FILE_LIMIT and not re.search(r'\b(?:theorem|lemma|def|end)\b', before):
                    start = offset + dec.end() + len(tail) - len(tail.lstrip()) + 1
                    original_type = literal_code[start:start + len(before)].strip()
                    # Folding ordinary type layout is safe, but literal spaces
                    # belong to the proposition and must remain byte-exact.
                    if '"' in original_type or "'" in original_type:
                        target = original_type if '\n' not in original_type and '\r' not in original_type else None
                    else:
                        target = ' '.join(original_type.split())
            if name in result:
                result[name]['target'] = None
                result[name]['duplicate'] = True
            else:
                result[name] = {'target': target, 'namespace': '.'.join(namespaces), 'duplicate': False}
        offset += len(line)
    return result


def source_groups(blobs):
    groups = {}
    for path, data in sorted(blobs.items()):
        if not path.endswith('.lean'):
            continue
        parts = path.split('/')
        if parts[-1].lower() == 'lakefile.lean' or 'checks' in parts or 'build' in parts or re.fullmatch(r'Target\d+\.lean', parts[-1]):
            continue  # Retained Target files are evidence, never staged as models.
        if 'models' in parts:
            pos = len(parts) - 1 - parts[::-1].index('models')
            root, module = '/'.join(parts[:pos + 1]), '.'.join(parts[pos + 1:])[:-5]
        else:
            root, module = path.rsplit('/', 1)[0], Path(path).stem
        group = groups.setdefault(root, {'root': root, 'models': {}, 'historical': bool(HISTORY_DIRS & set(parts))})
        group['models'][module] = {'path': path, 'sha256': digest(data), 'data': data,
                                   'declarations': declarations(text_bytes(data, path))}
    return groups


def evidence(path, blobs, excerpt=''):
    return {'path': path, 'hash': digest(blobs[path]), 'excerpt': excerpt[:4096]}


def proof_candidate(group, module, theorem, target, relation, origin, namespace='', provenance=()):
    return {'group': group, 'module': module, 'theorem': theorem, 'target': target,
            'relation': relation, 'target_origin': origin,
            'relation_origin': 'workspace_record' if origin == 'workspace_record' else 'inferred_reported_status',
            'namespace': namespace, 'attempt_provenance': list(provenance)}


def candidate_key(candidate):
    return (candidate['module'], candidate['theorem'], candidate['target'], candidate['relation'],
            candidate['namespace'], tuple(sorted((m, s['sha256']) for m, s in candidate['group']['models'].items())))


def deduplicate(candidates):
    merged = {}
    for candidate in candidates:
        key = candidate_key(candidate)
        paths = set(candidate.get('source_paths', [])) | {s['path'] for s in candidate['group']['models'].values()}
        hashes = dict(candidate.get('source_hashes', {}))
        hashes.update({s['path']: s['sha256'] for s in candidate['group']['models'].values()})
        if key not in merged:
            merged[key] = dict(candidate, source_paths=sorted(paths), source_hashes=hashes)
        else:
            item = merged[key]
            item['source_paths'] = sorted(set(item['source_paths']) | paths)
            item['source_hashes'].update(hashes)
            item['attempt_provenance'] += [p for p in candidate['attempt_provenance'] if p not in item['attempt_provenance']]
    return list(merged.values())


def parse_retained_target(data):
    """Only the existing template is understood; nothing here executes it."""
    raw = text_bytes(data, 'retained target')
    match = re.fullmatch(r'import (' + audit.MODULE_NAME + r')\n#check @(' + NAME + r')\n#print axioms \2\n(?:example : \(([^\n]+)\) := \2\n)?', raw)
    return {'module': match[1], 'theorem': match[2], 'target': match[3]} if match else None


def linked_workspace(finding, ledger, blobs, groups):
    """Return None if no relevant workspace record, else a blocking tier result."""
    bundles, all_evidence, errors = [], [], []
    for registry_path, data in sorted(blobs.items()):
        if not registry_path.endswith('/claims.md'):
            continue
        if HISTORY_DIRS & set(registry_path.split('/')):
            # Artifact stores contain individual historical files, not another
            # active workspace. Their hashes remain in the frozen inventory.
            continue
        workspace = registry_path[:-len('/claims.md')]
        rows = table_rows(text_bytes(data, registry_path))
        matches = [r for r in rows if r.get('Report anchor') == finding['report_anchor'] and r.get('Disposition') in {'audited', 'deferred'}]
        if not matches:
            continue
        ids = [r.get('Claim ID') for r in rows if r.get('Disposition') in {'audited', 'deferred'}]
        if len(ids) != len(set(ids)):
            errors.append('Duplicate registry IDs')
        required = []
        for row in matches:
            ident = row.get('Claim ID', '')
            if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]*', ident):
                errors.append('Unsupported finding ID')
                continue
            path = workspace + '/findings/' + ident + '.md'
            all_evidence.append(evidence(registry_path, blobs, str(row)))
            if path not in blobs:
                errors.append('Registered finding is missing')
                continue
            content = text_bytes(blobs[path], path)
            sec = sections(content)
            all_evidence.append(evidence(path, blobs, sec.get('Proofs', '')))
            if sec.get('Report anchor') != finding['report_anchor']:
                errors.append('Finding anchor conflicts with registry')
            # Resolve registry anchors against the supplied report, never IDs by spelling.
            report_path = workspace + '/input/report.md'
            if report_path not in blobs or audit.paragraphs(text_bytes(blobs[report_path], report_path)).get(finding['report_anchor']) != ledger.get(finding['report_anchor']):
                errors.append('Workspace report does not match supplied report ledger')
            if sec.get('Formal status') != finding['formal_status'] or sec.get('Semantic state') != finding['semantic_state']:
                errors.append('Workspace status conflicts with reported status')
            blocks = re.split(r'^### ', sec.get('Proofs', ''), flags=re.M)[1:]
            if not blocks:
                errors.append('Registered finding has no attributable proof')
            expected_relation = {'proved': 'supports', 'refuted': 'refutes'}[finding['formal_status']]
            justifies_finding = False
            for block in blocks:
                record = fields(block)
                if not {'Module', 'Theorem', 'Target', 'Relation'} <= record.keys() or record.keys() - {'Module', 'Theorem', 'Target', 'Relation'}:
                    errors.append('Unsupported proof fields')
                    continue
                module, theorem, target, relation = (record[k] for k in ('Module', 'Theorem', 'Target', 'Relation'))
                if relation not in {expected_relation, 'auxiliary'}:
                    errors.append('Conflicting proof relation')
                    continue
                justifies_finding |= relation == expected_relation
                # A proof's own explicit attempt/log reference takes precedence.
                attempts = set(re.findall(r'proof-attempts/([A-Za-z0-9_-]+)/', block))
                if attempts:
                    roots = [workspace + '/proof-attempts/' + a + '/models' for a in sorted(attempts)]
                else:
                    roots = [workspace + '/models']
                candidates = []
                for root in roots:
                    group = groups.get(root)
                    if group is None or module not in group['models'] or theorem not in group['models'][module]['declarations']:
                        errors.append('Unresolved source/theorem for workspace proof')
                        continue
                    provenance = [{'kind': 'explicit_attempt' if attempts else 'current_models', 'path': root}]
                    for check_path, check_data in blobs.items():
                        if attempts and any(check_path.startswith(workspace + '/proof-attempts/' + a + '/checks/') for a in attempts):
                            parsed = parse_retained_target(check_data)
                            if parsed and parsed['module'] == module and parsed['theorem'] == theorem and parsed['target'] == target:
                                provenance.append({'kind': 'retained_target_evidence', 'path': check_path, 'sha256': digest(check_data)})
                    candidates.append(proof_candidate(group, module, theorem, target, relation, 'workspace_record', provenance=provenance))
                candidates = deduplicate(candidates)
                if len(candidates) != 1:
                    errors.append('Conflicting or missing attempt snapshots')
                else:
                    required.extend(candidates)
            if not justifies_finding:
                errors.append('Auxiliary references cannot justify the reported status')
        bundles.append(deduplicate(required))
    if not bundles:
        return None
    signatures = {tuple(sorted((candidate_key(c) for c in bundle), key=repr)) for bundle in bundles}
    if errors or len(signatures) != 1 or not bundles[0]:
        return {'category': 'ambiguous_or_stale', 'proofs': [], 'evidence': all_evidence,
                'detail': '; '.join(sorted(set(errors))) or 'Conflicting workspace records'}
    return {'category': 'workspace_record', 'proofs': deduplicate([c for b in bundles for c in b]),
            'evidence': all_evidence, 'detail': None}


def link_claim(finding, ledger, blobs):
    expected_semantic = {'proved': 'supported', 'refuted': 'refuted'}[finding['formal_status']]
    if finding['semantic_state'] != expected_semantic:
        return {'category': 'ambiguous_or_stale', 'proofs': [], 'evidence': [],
                'detail': 'Reported semantic state and formal status conflict'}
    groups = source_groups(blobs)
    workspace = linked_workspace(finding, ledger, blobs, groups)
    if workspace is not None:
        return workspace
    explanation = finding.get('explanation', '')
    relation = {'proved': 'supports', 'refuted': 'refutes'}[finding['formal_status']]
    explicit, named, evidence_rows = [], [], []
    file_mentions = re.findall(r'[A-Za-z0-9_./-]+\.lean\b', explanation)
    by_mention = defaultdict(list)
    for group in groups.values():
        for module, source in group['models'].items():
            for theorem, declaration in source['declarations'].items():
                candidate = proof_candidate(group, module, theorem, declaration['target'], relation,
                                             'declaration', declaration['namespace'],
                                             [{'kind': 'historical_unassociated' if group['historical'] else 'current_models', 'path': source['path']}])
                exact_theorem = re.search(r"(?<![\w.'])" + re.escape(theorem) + r"(?![\w']|\.[\w'])", explanation)
                mentioned = [f for f in file_mentions if source['path'] == f or source['path'].endswith('/' + f)]
                if exact_theorem and mentioned:
                    # A historical file needs a path into that exact attempt, not merely its basename.
                    associated = not group['historical'] or any(
                        'proof-attempts/' in f or ('phase-artifacts/' in f and source['sha256'] in f.split('/'))
                        for f in mentioned)
                    candidate['historical_associated'] = associated
                    for mention in mentioned:
                        by_mention[mention].append(candidate)
                final = theorem.rsplit('.', 1)[-1]
                claim = finding['claim_id']
                if re.fullmatch(r'C\d+', claim, re.I) and re.fullmatch(re.escape(claim) + r'(?:_.*)?', final, re.I):
                    named.append(candidate)
    # Resolve each named file independently: unresolved required references and
    # conflicting exact snapshots cannot disappear behind a successful link.
    for mention in set(file_mentions):
        choices = by_mention[mention]
        if not HISTORY_DIRS & set(mention.split('/')):
            current = [c for c in choices if not c['group']['historical']]
            choices = current or choices
        if not choices:
            return {'category': 'ambiguous_or_stale', 'proofs': [], 'evidence': [], 'detail': 'Unresolved explicit Lean reference'}
        explicit.extend(choices)
    if file_mentions and not explicit:
        return {'category': 'ambiguous_or_stale', 'proofs': [], 'evidence': [], 'detail': 'Unresolved explicit Lean reference'}
    category = 'explicit_reference' if explicit else 'name_match'
    candidates = explicit if explicit else named
    if not explicit:
        current = [c for c in candidates if not c['group']['historical']]
        candidates = current or candidates
    candidates = deduplicate(candidates)
    if candidates:
        for c in candidates:
            source = c['group']['models'][c['module']]
            evidence_rows.append(evidence(source['path'], blobs, c['theorem'] + ' : ' + str(c['target'])))
        if len(candidates) != 1 or candidates[0]['target'] is None or (candidates[0]['group']['historical'] and not candidates[0].get('historical_associated')):
            return {'category': 'ambiguous_or_stale', 'proofs': [], 'evidence': evidence_rows,
                    'detail': 'Conflicting candidates, unsupported declaration type, or unassociated historical source'}
        return {'category': category, 'proofs': candidates, 'evidence': evidence_rows, 'detail': None}
    # An explicit file reference that cannot be resolved is stronger than absence.
    if file_mentions:
        return {'category': 'ambiguous_or_stale', 'proofs': [], 'evidence': [], 'detail': 'Unresolved explicit Lean reference'}
    return {'category': 'unlinked_lean' if any(p.endswith('.lean') for p in blobs) else 'no_lean',
            'proofs': [], 'evidence': [], 'detail': 'No attributable theorem'}


def stage_proof(candidate, claim_id, destination, proof_id):
    """Stage bytes, inspect that exact snapshot, then describe the executor job.

    The return value is (schema proof record, executor job description or None).
    The job carries exactly what recheck_sandbox.run_job needs from the plan (the
    sources with their plan hashes and the one proof request); the executor writes
    its own checks, so nothing generated here is ever executed.
    """
    module, theorem = candidate['module'], candidate['theorem']
    proof = {k: candidate[k] for k in ('module', 'theorem', 'target', 'target_origin', 'relation', 'relation_origin', 'attempt_provenance')}
    proof.update(id=proof_id, outcome='not_run', reason=None, detail=None, namespace=candidate.get('namespace', ''),
                 source_paths=candidate.get('source_paths', sorted(s['path'] for s in candidate['group']['models'].values())),
                 source_hashes=candidate.get('source_hashes', {s['path']: s['sha256'] for s in candidate['group']['models'].values()}),
                 filter_result={'passed': False, 'detail': None},
                 executor_admission={'passed': None, 'reason': None, 'detail': None},
                 argv=[], steps=[], checks=[], exit_code=None, signal=None, seconds=0, axioms=[], log_paths=[],
                 log_hashes={}, truncated=False, lean_subprocesses=0, run_dir=None)
    try:
        validate_names(module, theorem)
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', proof_id):
            raise RecheckError('unsafe_path', 'Invalid proof id')
        job_root = new_output(Path(destination) / proof_id)
        sources = job_root / 'sources'
        sources.mkdir()
        models, source_manifest, source_list = {}, {}, []
        for name, source in sorted(candidate['group']['models'].items()):
            validate_names(name, theorem)
            relative = name.replace('.', '/') + '.lean'
            path = sources / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = source['data']
            if len(data) > FILE_LIMIT or digest(data) != source['sha256']:
                raise RecheckError('source_changed', source['path'])
            path.write_bytes(data)
            path.chmod(0o400)
            models[name] = path
            source_manifest[relative] = source['sha256']
            source_list.append({'module': name, 'path': str(path.relative_to(ROOT)), 'sha256': source['sha256']})
        proof['staged_sources'] = str(sources.relative_to(ROOT))
        proof['staged_source_manifest'] = source_manifest
        # No retained object, script, Lake file, report or key enters this tree.
        declared = {m: audit.inspect_model(path, models) for m, path in models.items()}
        request = {'module': module, 'theorem': theorem, 'target': candidate['target'],
                   'relation': candidate['relation'], 'claim_id': claim_id}
        markdown_proofs._proofs([request], declared)
        for names in declared.values():
            for name in names:
                validate_names(module, name)
        order = markdown_proofs._order(models)
        proof.update(filter_result={'passed': True, 'detail': None})
        job = {'schema_version': 1, 'id': proof_id, 'proof_ids': [proof_id], 'claim_id': claim_id,
               'lean_version': '4.24.0', 'sources': source_list, 'proofs': [request],
               'namespace': candidate.get('namespace', ''), 'build_order': order,
               'target_origin': candidate['target_origin'], 'relation_origin': candidate['relation_origin'],
               'requires_gate': True, 'execution_status': 'not_run'}
        job_bytes = canonical(job)
        atomic_write(job_root / 'job.json', job_bytes)
        proof['job_path'] = str((job_root / 'job.json').relative_to(ROOT))
        proof['job_sha256'] = digest(job_bytes)
        return proof, job
    except (audit.AuditError, RecheckError, ValueError) as exc:
        reason = getattr(exc, 'reason', 'lexical_rejection')
        if 'cycle' in str(exc).lower():
            reason = 'dependency_cycle'
        elif 'reviewed local model imports' in str(exc):
            reason = 'dependency_missing'
        proof.update(outcome='filter_rejected', reason=reason, detail=str(exc),
                     filter_result={'passed': False, 'detail': str(exc)})
        return proof, None


def join_correctness(run, findings, anchors, key_rows):
    """Use the scorer's anchor grouping and observation, with unknown joins kept null."""
    rows_by_id = defaultdict(list)
    if key_rows is None:
        return {}
    for row in key_rows:
        variant = row.get('variant') or scorecard.VARIANT_BY_REPORT.get(row.get('report_path'))
        if variant == run['variant'] and isinstance(row.get('claim_id'), str):
            rows_by_id[row['claim_id']].append(row)
    usable = [items[0] for items in rows_by_id.values() if len(items) == 1 and items[0].get('semantic_state') in scorecard.STATES]
    grouped = defaultdict(list)
    for row in findings:
        grouped[row['report_anchor']].append(dict(row, argument_status=row.get('argument_status', 'not_reported')))
    bundle = {'claim_anchors': anchors, 'grouped': grouped, 'labels': {},
              'findings_sha256': digest(canonical(findings))}
    observed = scorecard.observe(dict(run, run_id=run['unit']), bundle, usable)
    return {row['claim_id']: row for row in observed}


def claim_outcome(proof_ids, proofs):
    """Mandatory references all pass; a definitive failure outranks pending ones.

    Preflight rejections win (nothing may launch for the claim), then the first
    failed or timed-out proof in sorted order. Only without any failure do pending
    proofs keep the claim not_run. A proof that compiled without a linked request
    (compiled_unlinked) never passes a claim.
    """
    if not proof_ids:
        return 'not_linked', None
    by_id = {p['id']: p for p in proofs}
    selected = [by_id[p] for p in sorted(proof_ids)]
    for p in selected:
        if p['outcome'] == 'filter_rejected':
            return p['outcome'], p['reason']
    for p in selected:
        if p['outcome'] in ('fail', 'timeout'):
            return p['outcome'], p['reason']
    if any(p['outcome'] == 'compiled_unlinked' for p in selected):
        return 'not_linked', None
    for p in selected:
        if p['outcome'] != 'pass':
            return p['outcome'], p['reason']
    return 'pass', None


def outcome_for_reason(reason):
    """The executor's own mapping (recheck_sandbox._run) from a reason to an outcome."""
    return ('filter_rejected' if reason in FILTER_REASONS else 'timeout' if reason == 'timeout'
            else 'not_run' if reason in ABORT_REASONS else 'fail')


def validate_executor_result(result, requests, context):
    """Contract check of one run_job result; any contradiction is infrastructure_error.

    requests is the list of proof requests the job carried. Returns the proof-level
    outcome: the executor's outcome, or compiled_unlinked for a pass of a job
    that carried no proof request. Nothing is recorded for a rejected result.
    """
    def refuse(detail):
        raise RecheckError('infrastructure_error', 'Executor contract violation: ' + detail)
    if not isinstance(result, dict) or set(RESULT_FIELDS) - set(result):
        refuse('result is not an object with every documented field')
    outcome, reason = result['outcome'], result['reason']
    if outcome not in EXECUTOR_OUTCOMES:
        refuse(f'outcome {outcome!r} is not a recheck outcome')
    if reason is not None and reason not in REASONS:
        refuse(f'unknown reason {reason!r}')
    if (outcome == 'pass') != (reason is None) or (reason is not None and outcome != outcome_for_reason(reason)):
        refuse(f'outcome {outcome!r} contradicts reason {reason!r}')
    if result['schema_version'] != 1 or not isinstance(result['detail'], str) or not isinstance(result['seconds'], (int, float)):
        refuse('schema_version, detail or seconds malformed')
    if result['runtime_hash'] != context['runtime_hash']:
        refuse('result is bound to a different runtime')
    if not isinstance(result['steps'], list) or not isinstance(result['checks'], list) or not isinstance(result['evidence'], dict):
        refuse('steps, checks or evidence malformed')
    if outcome != 'pass':
        return outcome
    if (type(result['lean_subprocesses']) is not int or result['lean_subprocesses'] < 1
            or result['evidence'].get('gate_hash') != context['gate_receipt_hash']
            or result['evidence'].get('steps_complete') is not True or result['truncated'] is not False
            or set(result['axioms']) - set(ALLOWED_AXIOMS)):
        refuse('a pass without gate attestation, complete steps, allowed axioms or any Lean step')
    fields = ('module', 'theorem', 'target', 'relation', 'claim_id')
    reported = [tuple(c.get(k) for k in fields) for c in result['checks']
                if isinstance(c, dict) and c.get('target') is not None]
    for request in requests:
        if tuple(request[k] for k in fields) not in reported:
            refuse(f'pass without a check of the requested {request["module"]}.{request["theorem"]}')
    return 'pass' if requests else 'compiled_unlinked'


def execute_jobs(jobs, run_job, record=None):
    """Run jobs one at a time and return one result per job.

    record(job, result) is called for every FINISHED result (pass, fail,
    filter_rejected, timeout). The first infrastructure_error or interrupted result,
    or an interrupt, stops the batch: that job and every later one come back
    not_run with the stopping reason and are not recorded, so a resume runs them
    again. A result outside EXECUTOR_OUTCOMES (a verifier_probe result, not_linked,
    a non-object) is never recorded: it raises infrastructure_error.
    """
    results, stopped = [], None
    for job in jobs:
        if stopped:
            results.append({'id': job['id'], 'outcome': 'not_run', 'reason': stopped, 'detail': 'Batch stopped before this job'})
            continue
        try:
            result = run_job(job)
        except (KeyboardInterrupt, SystemExit):
            result = {'outcome': 'not_run', 'reason': 'interrupted', 'detail': 'Interrupted while running this job'}
        except Exception as exc:
            result = {'outcome': 'not_run', 'reason': 'infrastructure_error', 'detail': f'{type(exc).__name__}: {exc}'}
        if not isinstance(result, dict) or result.get('outcome') not in EXECUTOR_OUTCOMES:
            raise RecheckError('infrastructure_error', 'Invalid injected job outcome')
        if result.get('reason') in ABORT_REASONS:
            stopped = result['reason']
        elif record is not None:
            record(job, result)
        results.append(dict(result, id=job['id']))
    return results


def summarize(claims, proofs):
    counts = Counter(c['outcome'] for c in claims)
    certain = [c for c in claims if c['false_formal_certainty'] is True]
    by_id = {p['id']: p for p in proofs}
    result = {'reported': len(claims), 'linked': sum(bool(c['proofs']) for c in claims),
              'pass': counts['pass'], 'fail': counts['fail'],
              'fail_by_reason': dict(sorted(Counter(c['reason'] for c in claims if c['outcome'] == 'fail').items(), key=lambda row: str(row[0]))),
              'filter_rejected': counts['filter_rejected'], 'timeout': counts['timeout'],
              'not_linked': counts['not_linked'], 'not_run': counts['not_run'],
              'reason_counts': dict(sorted(Counter(c['reason'] for c in claims if c['reason']).items())),
              'filter_rejected_by_reason': dict(sorted(Counter(c['reason'] for c in claims if c['outcome'] == 'filter_rejected').items(), key=lambda row: str(row[0]))),
              'correctness_unknown': sum(c['matches'] is None for c in claims),
              'false_formal_certainty': len(certain),
              'distinct_claim_false_formal_certainty': len({(c['dossier'], c['variant'], c.get('report_claim_id', c['claim_id'])) for c in certain}),
              # Proof level only: a pass of a job without a linked proof request is never a claim pass.
              'compiled_unlinked': sum(p['outcome'] == 'compiled_unlinked' for p in proofs),
              'link_categories': {k: sum(c['link_category'] == k for c in claims) for k in CATEGORIES},
              'lexical_filter_pass_claims': sum(bool(c['proofs']) and all(by_id[pid]['filter_result']['passed'] for pid in c['proofs']) for c in claims),
              'lexical_filter_pass_proofs': sum(p['filter_result']['passed'] for p in proofs),
              'proofs_executed': sum(p.get('lean_subprocesses', 0) > 0 for p in proofs),
              'proofs_passed': sum(p['outcome'] == 'pass' for p in proofs),
              'namespaced_proofs': sum(bool(p.get('namespace')) and p['filter_result']['passed'] for p in proofs),
              'staged_source_files': sum(len(p.get('staged_source_manifest', {})) for p in proofs),
              'distinct_staged_source_hashes': len({h for p in proofs for h in p.get('staged_source_manifest', {}).values()})}
    if result['reported'] != sum(counts[k] for k in ('pass', 'fail', 'filter_rejected', 'timeout', 'not_linked', 'not_run')):
        raise RecheckError('invalid_metadata', 'Reported/outcome reconciliation failed')
    if sum(result['fail_by_reason'].values()) != result['fail'] or None in result['fail_by_reason']:
        raise RecheckError('invalid_metadata', 'A failed claim without a reason')
    if any(c['outcome'] == 'pass' and c['reason'] for c in claims):
        raise RecheckError('invalid_metadata', 'A passing claim with a reason')
    return result


def markdown_table(cell, reviews, summary):
    rows = [('Finished reviews', len(reviews)), ('Reported proved + refuted', summary['reported'])]
    rows += [(name, summary['link_categories'][name]) for name in CATEGORIES]
    rows += [('Linked claims', summary['linked']), ('Lexical filter passed (claims)', summary['lexical_filter_pass_claims']),
             ('Lexical filter passed (proof references)', summary['lexical_filter_pass_proofs']),
             ('Staged source files (per proof job)', summary['staged_source_files']),
             ('Distinct staged source hashes', summary['distinct_staged_source_hashes']),
             ('Proof references with a namespace (checked without `open`)', summary['namespaced_proofs']),
             ('Proof references executed in the sandbox', summary['proofs_executed']),
             ('Proof references passed', summary['proofs_passed']),
             ('Compiled without a linked proof request (never a claim pass)', summary['compiled_unlinked']),
             ('False formal certainty (repeat-level)', summary['false_formal_certainty']),
             ('False formal certainty (distinct claims)', summary['distinct_claim_false_formal_certainty']),
             ('Correctness unknown', summary['correctness_unknown']), ('pass', summary['pass']),
             ('fail', summary['fail']), ('filter_rejected', summary['filter_rejected']),
             ('timeout', summary['timeout']), ('not_linked', summary['not_linked']), ('not_run', summary['not_run'])]
    reasons = summary['reason_counts']
    rows += [('reason: ' + name, reasons.get(name, 0)) for name in REASON_ROWS]
    rows += [('reason: ' + name, count) for name, count in reasons.items() if name not in REASON_ROWS]
    return '\n'.join([f'| {cell} | Count |', '| --- | ---: |'] + [f'| {key} | {value} |' for key, value in rows]) + '\n'


def render_result(result):
    summary = result['summary']
    rechecked = result['run'].get('phase') == 'recheck'
    if rechecked:
        run = result['run']
        lines = [('Retained proofs were rechecked in the verified sandbox (Lean 4.24.0, runtime '
                  f'`{run["runtime_hash"]}`, gate receipt `{run["gate_receipt_hash"]}`). ') +
                 ('This result is COMPLETE.' if run['complete'] else
                  'This result is PARTIAL: ' + (run.get('incomplete_reason') or 'not every planned job finished') + '.'), '']
    else:
        lines = ['No proof was rerun. These are inventory, linking, lexical-filter and sealed-key join results.', '']
    lines += [markdown_table(result['cell'], result['reviews'], summary),
              'Reconciliation: reported = pass + fail + filter_rejected + timeout + not_linked + not_run.', '',
              ('lexical_rejection is the product filter, strict_rejection is the executor\'s stricter admission; both are '
               'filter_rejected and no Lean process started. replay_failed, compile_error, target_mismatch, '
               'axioms_disallowed, source_changed and the other executor reasons are reported exactly as the executor '
               'gave them. A claim passes only if every required proof reference passed.' if rechecked else
               'Linking and lexical acceptance do not establish a proof or documentary fidelity.'), '',
              'A pass establishes the encoded proposition under the recorded axioms in the sandboxed runtime, '
              'not documentary fidelity, full-claim coverage or authentic evidence. Name-based links are weaker. '
              'False formal certainty compares the reported verdict with the sealed key, independently of recheck outcome. '
              'Distinct claims collapse repeats by dossier, variant and report claim ID, as in scorecard.collapse. '
              'Staged-file counts include separate per-proof snapshots; distinct hashes count content once.', '',
              'Registration: retained bundles are legacy; this planner does not invent v2 registration.', '',
              '| Review | Claim | Reported | Link category | Outcome | Reason / linking limits |',
              '| --- | --- | --- | --- | --- | --- |']
    for c in result['claims']:
        cells = [c['review_unit'], c['claim_id'], c['reported_status'], c['link_category'], c['outcome'],
                 c['reason'] or c.get('link_detail') or '; '.join(c.get('caveats', [])) or
                 ('Pending gated execution' if c['outcome'] == 'not_run' else '')]
        lines.append('| ' + ' | '.join(str(v).replace('|', '&#124;').replace('\n', ' ') for v in cells) + ' |')
    if result.get('discrepancies'):
        lines += ['', 'Inventory discrepancies: ' + '; '.join(result['discrepancies'])]
    return '\n'.join(lines) + '\n'


def plan(inventory_path, out):
    inventory_path = Path(os.path.abspath(inventory_path))
    with SafeTree(inventory_path.parent) as tree:
        frozen_bytes = tree.read(inventory_path.name, limit=64 * FILE_LIMIT)
    frozen = parse_json(frozen_bytes, 'inventory')
    if frozen['code_hashes'] != code_hashes():
        raise RecheckError('source_changed', 'Planner/filter changed since inventory; create a new inventory')
    destination = new_output(out)
    results = {}
    with SafeTree(frozen['campaign_root']) as tree:
        # Freeze control and sealed-key bytes, verifying the complete manifest.
        controls = {}
        for path, expected_hash in frozen['control_hashes'].items():
            data = tree.read(path)
            if digest(data) != expected_hash:
                raise RecheckError('source_changed', path)
            controls[path] = parse_json(data, path)
        for cell in frozen['cells']:
            reviews = [r for r in frozen['reviews'] if r['cell'] == cell]
            result = {'schema_version': 1, 'cell': cell,
                'run': {k: frozen[k] for k in ('id', 'created_utc', 'base_commit', 'code_hashes')},
                'reviews': reviews, 'claims': [], 'proofs': [], 'jobs': [], 'discrepancies': []}
            result['run'].update(input_manifest_hash=digest(frozen_bytes), runtime_hash=None,
                                 lean_version='4.24.0', gate_receipt_hash=None, complete=False,
                                 phase='plan', planning_complete=False)
            for review in reviews:
                blobs = capture_review(tree, review['path'])
                if {p: digest(b) for p, b in sorted(blobs.items())} != review['input_hashes']:
                    raise RecheckError('source_changed', review['path'])
                run, ledger, anchors, findings = review_metadata(blobs)
                key_path = f'lean/{review["part"]}/keys-sealed/{review["dossier"]}.json'
                key = controls.get(key_path)
                observed = join_correctness(run, findings, anchors, key.get('expectations') if key else None)
                report_ids = {anchor: claim for claim, anchor in anchors.items()}
                for finding in findings:
                    if finding['formal_status'] not in scorecard.FORMAL_CERTAIN:
                        continue
                    link = link_claim(finding, ledger, blobs)
                    report_id = report_ids.get(finding['report_anchor'])
                    obs = observed.get(report_id)
                    claim = {k: review[k] for k in ('dossier', 'variant', 'repeat')}
                    claim.update(review_unit=review['unit'], claim_id=finding['claim_id'], part=review['part'],
                        review_path=review['path'], report_claim_id=report_id,
                        reported_status=finding['formal_status'], semantic_state=finding['semantic_state'],
                        expected_state=obs['expected'] if obs else None, matches=obs['matches'] if obs else None,
                        false_formal_certainty=not obs['matches'] if obs else None,
                        link_category=link['category'], link_evidence=link['evidence'], proofs=[],
                        link_detail=link['detail'], outcome='not_linked', reason=None)
                    for candidate in link['proofs']:
                        proof_id = f'{cell}-proof-{len(result["proofs"]) + 1:05d}'
                        proof, job = stage_proof(candidate, finding['claim_id'], destination / cell / 'jobs', proof_id)
                        result['proofs'].append(proof)
                        claim['proofs'].append(proof_id)
                        if job:
                            result['jobs'].append(job)
                    claim['outcome'], claim['reason'] = claim_outcome(claim['proofs'], result['proofs'])
                    result['claims'].append(claim)
            result['summary'] = summarize(result['claims'], result['proofs'])
            if len(reviews) != REVIEWS_PER_CELL:
                result['discrepancies'].append(f'{len(reviews)} reviews; full five-part baseline is {REVIEWS_PER_CELL}')
            if result['summary']['reported'] != BASELINES[cell]:
                result['discrepancies'].append(f'{result["summary"]["reported"]} reported; full five-part baseline is {BASELINES[cell]}')
            result['quarantined'] = [q for q in frozen['quarantined'] if q['cell'] == cell]
            result['excluded'] = frozen['excluded']
            result['run']['planning_complete'] = not result['discrepancies'] and not result['quarantined'] and not result['excluded']
            # complete remains false: no recheck has run. Atomic files precede a
            # publication marker binding the JSON and Markdown pair.
            json_data, md_data = canonical(result), render_result(result).encode()
            atomic_write(destination / cell / 'lean-recheck.json', json_data)
            atomic_write(destination / cell / 'lean-recheck.md', md_data)
            atomic_write(destination / cell / 'plan-summary.md', ('No proof was rerun.\n\n' + markdown_table(cell, reviews, result['summary'])).encode())
            atomic_write(destination / cell / 'publication.json', canonical({'json_sha256': digest(json_data), 'markdown_sha256': digest(md_data), 'phase': 'plan'}))
            results[cell] = result
    atomic_write(destination / 'inventory.json', frozen_bytes)
    return results


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_executor():
    """The verified executor, imported only when a command needs it."""
    return importlib.import_module('recheck_sandbox')


def under_work(path, label):
    """A path beneath this worktree's tmp/recheck/ (the executor's own confinement), no symlink parents."""
    absolute = Path(os.path.abspath(path))
    try:
        relative = absolute.relative_to(WORK)
    except ValueError as exc:
        raise RecheckError('unsafe_path', f'{label} must be beneath the copied worktree tmp/recheck/') from exc
    if not relative.parts:
        raise RecheckError('unsafe_path', f'{label} must be a directory beneath tmp/recheck/')
    current = ROOT
    for name in ('tmp', 'recheck', *relative.parts):
        safe_parts(name)
        current = current / name
        if current.is_symlink():
            raise RecheckError('unsafe_path', str(current))
    return absolute


def prerequisites(executor, gate, runtime):
    """Hash the runtime actually used and require check_gate to accept the receipt for it.

    Nothing here is optional: run, check-gate and every job go through it, and the
    executor repeats both checks before each job.
    """
    runtime, gate = under_work(runtime, 'runtime'), Path(os.path.abspath(gate))
    try:
        runtime_hash = executor.sha(executor.canonical(executor.runtime_manifest(runtime)))
        receipt_hash = executor.check_gate(str(gate), runtime_hash)
        with SafeTree(gate.parent) as tree:
            gate_file = digest(tree.read(gate.name))
    except Exception as exc:
        raise RecheckError('infrastructure_error', f'Verifier receipt or runtime refused '
                           f'({getattr(exc, "reason", type(exc).__name__)}): {exc}') from exc
    return {'runtime': str(runtime), 'runtime_hash': runtime_hash, 'gate': str(gate),
            'gate_receipt_hash': receipt_hash, 'gate_file_sha256': gate_file}


def load_plan(plan_dir, cells):
    """Published plan pairs for the cells, verified against their publication markers.

    The plan must have been made by the code that is running now, from an inventory
    whose bytes are the ones the plan recorded; otherwise the run refuses.
    """
    plan_dir = Path(os.path.abspath(plan_dir))
    loaded = {}
    with SafeTree(plan_dir) as tree:
        inventory_bytes = tree.read('inventory.json', limit=64 * FILE_LIMIT)
        for cell in cells:
            json_bytes = tree.read(f'{cell}/lean-recheck.json', limit=256 * FILE_LIMIT)
            markdown = tree.read(f'{cell}/lean-recheck.md', limit=64 * FILE_LIMIT)
            publication = parse_json(tree.read(f'{cell}/publication.json'), 'publication')
            if publication != {'json_sha256': digest(json_bytes), 'markdown_sha256': digest(markdown), 'phase': 'plan'}:
                raise RecheckError('source_changed', f'Plan publication marker does not match {cell}')
            result = parse_json(json_bytes, f'{cell} plan')
            if result.get('cell') != cell or result['run'].get('phase') != 'plan':
                raise RecheckError('invalid_metadata', f'{cell}: not a plan result')
            if result['run']['code_hashes'] != code_hashes():
                raise RecheckError('source_changed', 'Code changed since the plan; make a new inventory and plan')
            if result['run']['input_manifest_hash'] != digest(inventory_bytes):
                raise RecheckError('source_changed', f'{cell}: inventory differs from the plan input')
            loaded[cell] = {'result': result, 'sha256': digest(json_bytes)}
    return loaded, digest(inventory_bytes)


def claim_key(claim):
    return f'{claim["review_unit"]}#{claim["claim_id"]}'


def planned_jobs(loaded, cells, claims=None, limit=None):
    """Jobs to run, in plan order. A claim that already has a preflight rejection
    launches nothing (its other proof references stay not_run); a selection keeps
    only the named claims; unselected claims are never run."""
    jobs = []
    for cell in cells:
        result = loaded[cell]['result']
        proofs = {p['id']: p for p in result['proofs']}
        for claim in result['claims']:
            if claims is not None and claim_key(claim) not in claims or claim['outcome'] == 'filter_rejected':
                continue
            for proof_id in claim['proofs']:
                proof = proofs[proof_id]
                if proof.get('job_path') and proof['filter_result']['passed']:
                    jobs.append({'id': proof_id, 'cell': cell, 'claim': claim_key(claim),
                                 'job_path': proof['job_path'], 'job_sha256': proof['job_sha256']})
    if claims is not None and set(claims) - {j['claim'] for j in jobs}:
        # A selected claim that has nothing to run (not linked, rejected) is a selection mistake.
        raise RecheckError('invalid_metadata', 'Selected claims without a runnable job: '
                           + ', '.join(sorted(set(claims) - {j['claim'] for j in jobs})))
    jobs = jobs[:limit] if limit else jobs
    with SafeTree(ROOT) as tree:
        for job in jobs:
            data = tree.read(job['job_path'])
            if digest(data) != job['job_sha256']:
                raise RecheckError('source_changed', job['job_path'])
            job['description'] = parse_json(data, job['job_path'])
    return jobs


def executor_job(description, run_dir, runtime, runtime_hash, gate):
    """The planned job in the executor's exact job format; only these seven fields plus the
    schema version. Source hashes come from the plan, never from a rehash here, so the
    executor's own source_changed check stays meaningful."""
    return {'schema_version': 1, 'run_dir': str(run_dir), 'runtime': str(runtime), 'runtime_hash': runtime_hash,
            'gate': str(gate),
            'sources': [{'module': s['module'], 'path': str(ROOT / s['path']), 'sha256': s['sha256']}
                        for s in description['sources']],
            'proofs': [dict(p) for p in description['proofs']]}


def binding_for(context, inventory_sha, loaded, cells, jobs, claims, limit):
    return {'schema_version': 1, 'code_hashes': code_hashes(), 'runtime_hash': context['runtime_hash'],
            'gate_receipt_hash': context['gate_receipt_hash'], 'gate_file_sha256': context['gate_file_sha256'],
            'lean_version': '4.24.0', 'inventory_sha256': inventory_sha, 'cells': list(cells),
            'plan': {cell: loaded[cell]['sha256'] for cell in cells},
            'claims': sorted(claims) if claims is not None else None, 'limit': limit,
            'jobs': [[j['cell'], j['id'], j['job_sha256']] for j in jobs]}


def init_run_dir(out, binding, plan_dir):
    """Create the run directory, or accept an existing one only for the identical binding."""
    manifest_path = out / 'manifest.json'
    if not out.exists():
        out.mkdir(parents=True, mode=0o700)
        for name in ('journal', 'exec', 'history'):
            (out / name).mkdir(mode=0o700)
        atomic_write(manifest_path, canonical({'schema_version': 1, 'created_utc': utc_now(), 'plan_dir': str(plan_dir),
                                               'binding': binding, 'binding_sha256': digest(canonical(binding))}))
        return True
    with SafeTree(out) as tree:
        try:
            manifest = parse_json(tree.read('manifest.json'), 'run manifest')
        except RecheckError as exc:
            raise RecheckError('invalid_metadata', 'Existing directory is not a recheck run') from exc
    if manifest.get('binding') != binding:
        changed = sorted(k for k in set(binding) | set(manifest.get('binding', {})) if binding.get(k) != manifest.get('binding', {}).get(k))
        raise RecheckError('source_changed', 'Resume refused: the run was created with different ' + ', '.join(changed))
    return False


def read_journal(out, jobs, context):
    finished = {}
    with SafeTree(out) as tree:
        present = set(tree.names('journal'))
        for job in jobs:
            path = f'journal/{job["id"]}.json'
            if job['id'] + '.json' not in present:
                continue
            entry = parse_json(tree.read(path, limit=8 * FILE_LIMIT), path)
            if entry.get('proof_id') != job['id'] or entry.get('job_sha256') != job['job_sha256']:
                raise RecheckError('source_changed', f'Journal entry does not belong to {job["id"]}')
            outcome = validate_executor_result(entry['result'], job['description']['proofs'], context)
            if outcome != entry.get('proof_outcome') or entry['result']['reason'] in ABORT_REASONS:
                raise RecheckError('source_changed', f'Journal entry is inconsistent: {job["id"]}')
            finished[job['id']] = entry
    return finished


class Progress:
    """status.json, rewritten atomically after every job."""
    def __init__(self, out, total, finished):
        self.out, self.total, self.state = out, total, 'running'
        self.finished = dict(finished)
        self.started = time.monotonic()
        self.started_utc = utc_now()
        self.this_run = 0
        self.seconds = 0.0
        self.last = []
        self.current = None
        self.stopped = None

    def write(self):
        outcomes = Counter(e['proof_outcome'] for e in self.finished.values())
        reasons = Counter(e['result']['reason'] for e in self.finished.values() if e['result']['reason'])
        remaining = self.total - len(self.finished)
        mean = self.seconds / self.this_run if self.this_run else None
        try:
            load = os.getloadavg()
        except OSError:
            load = None
        atomic_write(self.out / 'status.json', canonical({
            'state': self.state, 'pid': os.getpid(), 'started_utc': self.started_utc, 'updated_utc': utc_now(),
            'elapsed_seconds': round(time.monotonic() - self.started, 1), 'jobs_total': self.total,
            'jobs_finished': len(self.finished), 'jobs_remaining': remaining, 'finished_this_process': self.this_run,
            'mean_job_seconds': round(mean, 3) if mean else None,
            'eta_seconds': round(mean * remaining, 1) if mean else None, 'current': self.current,
            'stopped': self.stopped, 'outcomes': dict(sorted(outcomes.items())), 'reasons': dict(sorted(reasons.items())),
            'last': self.last[-5:], 'loadavg': load}))


def run(args, executor=None):
    """Run the planned jobs one at a time through the verified executor."""
    executor = executor or load_executor()
    cells = list(args.cells)
    context = prerequisites(executor, args.gate, args.runtime)
    out = under_work(args.out, 'run output')
    claims = set(args.claims) if args.claims else None
    if args.limit is not None and args.limit < 1:
        raise RecheckError('invalid_metadata', '--limit must be positive')
    loaded, inventory_sha = load_plan(args.plan, cells)
    jobs = planned_jobs(loaded, cells, claims, args.limit)
    if not jobs:
        raise RecheckError('invalid_metadata', 'No runnable job in the selection')
    binding = binding_for(context, inventory_sha, loaded, cells, jobs, claims, args.limit)
    created = init_run_dir(out, binding, Path(os.path.abspath(args.plan)))
    lock = open(out / 'run.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise RecheckError('infrastructure_error', 'Another run is active in this directory') from exc
    previous_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        finished = read_journal(out, jobs, context)
        if (out / 'abort.json').exists():
            os.replace(out / 'abort.json', out / 'history' / f'abort-{int(time.time())}.json')
        pending = [j for j in jobs if j['id'] not in finished]
        progress = Progress(out, len(jobs), finished)
        atomic_write(out / 'run.pid', f'{os.getpid()}\n'.encode())
        print(f'recheck run {"started" if created else "resumed"}: jobs={len(jobs)} finished={len(finished)} '
              f'pending={len(pending)} out={out}', flush=True)
        progress.write()

        def run_one(job):
            attempt = sum(p.name.startswith(job['id'] + '-a') for p in (out / 'exec').iterdir()) + 1
            run_dir = out / 'exec' / f'{job["id"]}-a{attempt:02d}'
            job['run_dir'] = str(run_dir)
            progress.current = job['id']
            started = time.monotonic()
            result = executor.run_job(executor_job(job['description'], run_dir, context['runtime'],
                                                   context['runtime_hash'], context['gate']))
            job['wall_seconds'] = round(time.monotonic() - started, 3)
            try:
                job['proof_outcome'] = validate_executor_result(result, job['description']['proofs'], context)
            except RecheckError:
                atomic_write(out / 'history' / f'violation-{job["id"]}-a{attempt:02d}.json', canonical(result) if isinstance(result, dict) else b'null\n')
                raise
            if result['reason'] in ABORT_REASONS:
                atomic_write(out / 'abort.json', canonical({'proof_id': job['id'], 'recorded_utc': utc_now(), 'result': result}))
            return result

        def record(job, result):
            entry = {'schema_version': 1, 'proof_id': job['id'], 'cell': job['cell'], 'claim': job['claim'],
                     'job_sha256': job['job_sha256'], 'recorded_utc': utc_now(), 'run_dir': job['run_dir'],
                     'wall_seconds': job['wall_seconds'], 'proof_outcome': job['proof_outcome'], 'result': result}
            atomic_write(out / 'journal' / f'{job["id"]}.json', canonical(entry))
            progress.finished[job['id']] = entry
            progress.this_run += 1
            progress.seconds += job['wall_seconds']
            progress.last.append({'id': job['id'], 'outcome': job['proof_outcome'], 'reason': result['reason'],
                                  'seconds': job['wall_seconds']})
            progress.current = None
            progress.write()
            print(f'[{len(progress.finished)}/{progress.total}] {job["id"]} {job["proof_outcome"]} '
                  f'{result["reason"] or "-"} {job["wall_seconds"]}s', flush=True)

        try:
            results = execute_jobs(pending, run_one, record)
        except RecheckError as exc:
            results = [{'id': progress.current, 'outcome': 'not_run', 'reason': 'infrastructure_error', 'detail': str(exc)}]
        stopped = next((r for r in results if r.get('reason') in ABORT_REASONS), None)
        if stopped:
            if not (out / 'abort.json').exists():
                atomic_write(out / 'abort.json', canonical({'proof_id': stopped['id'], 'recorded_utc': utc_now(), 'result': stopped}))
            progress.state, progress.stopped = ('interrupted' if stopped['reason'] == 'interrupted' else 'aborted'), {
                'proof_id': stopped['id'], 'reason': stopped['reason'], 'detail': stopped.get('detail')}
            print(f'recheck run {progress.state} at {stopped["id"]}: {stopped["reason"]}; '
                  f'{sum(r["outcome"] == "not_run" for r in results)} job(s) not run. Resume with the same command.', flush=True)
        else:
            progress.state = 'finished'
        progress.current = None
        progress.write()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        try:
            os.unlink(out / 'run.pid')
        except OSError:
            pass
        lock.close()
    try:
        export(out)
        exported = 'ok'
    except (RecheckError, OSError) as exc:
        exported = 'failed'
        print(f'export failed (run the export command): {exc}', file=sys.stderr, flush=True)
    if progress.state == 'finished':
        # Last line of a complete run: every job journaled and the per-cell pairs exported.
        print(f'RECHECK_FINISHED jobs={len(jobs)} export={exported} dir={out / "export"}', flush=True)
    return {'finished': 0, 'aborted': 3, 'interrupted': 130}[progress.state]


def terminate(signum, frame):
    """SIGTERM becomes SystemExit so the executor's own step cleanup runs and the batch stops as interrupted."""
    raise SystemExit(128 + signum)


def apply_result(proof, entry):
    result = entry['result']
    evidence = result['evidence']
    steps = [{k: s.get(k) for k in ('index', 'kind', 'module', 'exit_code', 'signal', 'seconds', 'output_offset',
                                    'output_length', 'output_sha256', 'object_sha256') if k in s}
             for s in result['steps']]
    proof.update(outcome=entry['proof_outcome'], reason=result['reason'], detail=result['detail'][:4096],
                 argv=result['argv'], steps=steps, checks=result['checks'], exit_code=result['exit_code'],
                 signal=result['signal'], seconds=result['seconds'], axioms=result['axioms'],
                 log_paths=result['log_paths'], log_hashes=result['log_hashes'], truncated=result['truncated'],
                 lean_subprocesses=result['lean_subprocesses'], run_dir=entry['run_dir'],
                 wall_seconds=entry['wall_seconds'],
                 executor_admission={'passed': True if result['lean_subprocesses'] > 0 else False if result['outcome'] == 'filter_rejected' else None,
                                     'reason': result['reason'] if result['outcome'] == 'filter_rejected' else None,
                                     'detail': result['detail'][:4096] if result['outcome'] == 'filter_rejected' else None},
                 executor_evidence={k: evidence.get(k) for k in ('gate_hash', 'steps_complete', 'cleanup_empty',
                                                                 'cleanup_seconds', 'nice', 'rlimits') if k in evidence})


def assemble(loaded, binding, manifest, journal, abort_reason):
    """One cell's final result from its plan and the journal. No outcome is invented:
    a job without a journal entry is not_run, with the batch's stopping reason if there was one."""
    results = {}
    partial = binding['claims'] is not None or binding['limit'] is not None
    for cell in binding['cells']:
        result = json.loads(json.dumps(loaded[cell]['result']))
        planned = {j[1] for j in binding['jobs'] if j[0] == cell}
        for proof in result['proofs']:
            entry = journal.get(proof['id'])
            if entry:
                apply_result(proof, entry)
            elif proof['id'] in planned:
                proof.update(outcome='not_run', reason=abort_reason, detail='Not finished' + (f': run stopped ({abort_reason})' if abort_reason else ''))
            elif proof.get('job_path'):
                proof.update(outcome='not_run', reason=None, detail='Not run: claim not selected or already filter_rejected at preflight')
        by_id = {p['id']: p for p in result['proofs']}
        for claim in result['claims']:
            claim['outcome'], claim['reason'] = claim_outcome(claim['proofs'], result['proofs'])
            # The executor checks `example : (target) := theorem` without the planner's `open NAMESPACE in`.
            claim['caveats'] = sorted({f'target checked without `open {by_id[pid]["namespace"]}`'
                                       for pid in claim['proofs'] if by_id[pid].get('namespace')})
        for job in result['jobs']:
            job['execution_status'] = by_id[job['id']]['outcome']
        result['summary'] = summarize(result['claims'], result['proofs'])
        unfinished = sorted(p for p in planned if p not in journal)
        reasons = []
        if partial:
            reasons.append('only the selected claims were run')
        if unfinished:
            reasons.append(f'{len(unfinished)} planned job(s) not finished')
        if not result['run']['planning_complete']:
            reasons.append('planning discrepancies remain')
        result['run'].update(phase='recheck', runtime_hash=binding['runtime_hash'], gate_receipt_hash=binding['gate_receipt_hash'],
                             gate_file_sha256=binding['gate_file_sha256'], complete=not reasons,
                             incomplete_reason='; '.join(reasons) or None, recheck_run_id=manifest['binding_sha256'],
                             jobs_planned=len(planned), jobs_finished=len(planned) - len(unfinished),
                             selection={'claims': binding['claims'], 'limit': binding['limit']},
                             stopped=abort_reason, exported_utc=utc_now())
        results[cell] = result
    return results


def export(run_dir, dest=None, evidence=True):
    """Render lean-recheck.json/.md (and a publication marker) per cell from a run's journal.

    Safe at any time, including while the run is going: it reads only finished journal entries.
    """
    out = under_work(run_dir, 'run directory')
    dest = Path(os.path.abspath(dest)) if dest else out / 'export'
    with SafeTree(out) as tree:
        manifest = parse_json(tree.read('manifest.json'), 'run manifest')
        names = tree.names('journal')
        abort = parse_json(tree.read('abort.json'), 'abort') if 'abort.json' in tree.names() else None
        entries = {n[:-5]: parse_json(tree.read('journal/' + n, limit=8 * FILE_LIMIT), n) for n in names if n.endswith('.json')}
    binding = manifest['binding']
    loaded, _ = load_plan(manifest['plan_dir'], binding['cells'])
    if {c: loaded[c]['sha256'] for c in binding['cells']} != binding['plan']:
        raise RecheckError('source_changed', 'Plan changed since the run started')
    job_hashes = {j[1]: j[2] for j in binding['jobs']}
    for proof_id, entry in entries.items():
        if job_hashes.get(proof_id) != entry.get('job_sha256'):
            raise RecheckError('source_changed', f'Journal entry {proof_id} is not part of this run')
    abort_reason = abort['result']['reason'] if abort and abort['result'].get('reason') in ABORT_REASONS else None
    # An abort marker from a stopped run is cleared when it resumes; a leftover one still states why jobs are missing.
    assembled = assemble(loaded, binding, manifest, entries, abort_reason)
    for cell, result in assembled.items():
        directory = dest / cell
        manifest_rows = {}
        if evidence:
            for proof in result['proofs']:
                run_path = proof.get('run_dir')
                if not run_path or proof['id'] not in entries:
                    continue
                for name in ('result.json', 'output.log', 'wire.log', 'job.json'):
                    source = Path(run_path) / name
                    if source.is_file() and not source.is_symlink():
                        target = directory / 'evidence' / proof['id'] / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        data = source.read_bytes()
                        atomic_write(target, data)
                        manifest_rows[f'evidence/{proof["id"]}/{name}'] = digest(data)
        evidence_manifest = canonical(manifest_rows)
        json_data, md_data = canonical(result), render_result(result).encode()
        atomic_write(directory / 'evidence-manifest.json', evidence_manifest)
        atomic_write(directory / 'lean-recheck.json', json_data)
        atomic_write(directory / 'lean-recheck.md', md_data)
        atomic_write(directory / 'publication.json', canonical({
            'json_sha256': digest(json_data), 'markdown_sha256': digest(md_data), 'phase': 'recheck',
            'complete': result['run']['complete'], 'evidence_manifest_sha256': digest(evidence_manifest),
            'run_id': manifest['binding_sha256']}))
    return assembled


def show_status(run_dir, verbose=False):
    """Read-only progress report. It never starts, resumes or calls the executor."""
    out = under_work(run_dir, 'run directory')
    with SafeTree(out) as tree:
        status = parse_json(tree.read('status.json'), 'status')
        entries = [parse_json(tree.read('journal/' + n, limit=8 * FILE_LIMIT), n)
                   for n in tree.names('journal') if n.endswith('.json')] if verbose else []
    alive = False
    try:
        os.kill(status['pid'], 0)
        alive = True
    except (OSError, TypeError):
        pass
    state = status['state'] + (' (process gone: stale status)' if status['state'] == 'running' and not alive else '')
    print(f'state={state} pid={status["pid"]} alive={alive}')
    print(f'jobs: finished {status["jobs_finished"]}/{status["jobs_total"]}, remaining {status["jobs_remaining"]}, '
          f'elapsed {status["elapsed_seconds"]}s, mean {status["mean_job_seconds"]}s/job, eta {status["eta_seconds"]}s')
    print(f'outcomes={status["outcomes"]} reasons={status["reasons"]} loadavg={status["loadavg"]}')
    if status['stopped']:
        print(f'stopped={status["stopped"]}')
    for row in status['last']:
        print('last:', row)
    for entry in sorted(entries, key=lambda e: e['proof_id']):
        result = entry['result']
        print(f'{entry["proof_id"]} {entry["claim"]} outcome={entry["proof_outcome"]} reason={result["reason"]} '
              f'seconds={result["seconds"]:.2f} wall={entry["wall_seconds"]}s lean_steps={result["lean_subprocesses"]}')
        for step in result['steps']:
            print(f'    step {step["index"]} {step["kind"]} {step["module"]} exit={step["exit_code"]} '
                  f'signal={step["signal"]} {step["seconds"]:.2f}s')
        for check in result['checks']:
            print(f'    check {check["module"]}.{check["theorem"]} target={check["target"]!r} relation={check["relation"]} '
                  f'claim={check["claim_id"]} axioms={check["axioms"]}')
        if result['detail']:
            print('    detail:', result['detail'][:300].replace('\n', ' '))
    if status['state'] == 'finished':
        return 0
    if status['state'] == 'running':
        return 1 if alive else 4
    return 3


def check_gate_command(executor, args):
    context = prerequisites(executor, args.gate, args.runtime)
    if args.expect_runtime and args.expect_runtime != context['runtime_hash']:
        raise RecheckError('infrastructure_error', f'Runtime hash {context["runtime_hash"]} differs from expected {args.expect_runtime}')
    print(f'runtime_hash={context["runtime_hash"]}')
    print(f'gate_receipt_hash={context["gate_receipt_hash"]}')
    print(f'gate_file_sha256={context["gate_file_sha256"]}')
    for path, value in sorted(executor.binding_manifest(context['runtime_hash'])['code'].items()):
        print(f'bound {path}={value}')
    print('gate accepted for this runtime')


def build_runtime_command(executor, args):
    destination = under_work(args.out, 'runtime')
    print(f'runtime_hash={executor.build_runtime(args.lean, destination)}')


def main(argv=None, executor=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    inv = commands.add_parser('inventory', help='Freeze finished reviews and input hashes; no Lean')
    inv.add_argument('--campaign-root', required=True)
    inv.add_argument('--cells', nargs='+', choices=CELLS, default=list(CELLS))
    inv.add_argument('--parts', nargs='+', choices=PARTS, default=list(PARTS))
    inv.add_argument('--out', required=True)
    planning = commands.add_parser('plan', help='Link and stage a frozen inventory; no Lean')
    planning.add_argument('--inventory', required=True)
    planning.add_argument('--out', required=True)
    build = commands.add_parser('build-runtime', help='Build the hash-manifested runtime with the executor (needs a trusted Lean)')
    build.add_argument('--lean', required=True)
    build.add_argument('--out', required=True)
    gate = commands.add_parser('check-gate', help='Hash the runtime and ask the executor whether the receipt is accepted for it')
    gate.add_argument('--gate', required=True)
    gate.add_argument('--runtime', required=True)
    gate.add_argument('--expect-runtime')
    execution = commands.add_parser('run', help='Run the planned jobs through the verified executor (gate mandatory; resumable)')
    execution.add_argument('--plan', required=True, help='plan output directory (inventory.json and CELL/ pairs)')
    execution.add_argument('--gate', required=True, help='approving verifier receipt')
    execution.add_argument('--runtime', required=True, help='runtime directory built by build-runtime')
    execution.add_argument('--out', required=True, help='run directory beneath tmp/recheck/; an existing one resumes')
    execution.add_argument('--cells', nargs='+', choices=CELLS, default=list(CELLS))
    execution.add_argument('--claims', nargs='+', help='run only these claims, as REVIEW_UNIT#CLAIM_ID (sample runs)')
    execution.add_argument('--limit', type=int, help='run only the first N jobs of the selection')
    state = commands.add_parser('status', help='Read-only progress of a run; never calls the executor')
    state.add_argument('--run', required=True)
    state.add_argument('--verbose', action='store_true', help='per finished proof: steps, checks, detail')
    exporting = commands.add_parser('export', help='Render lean-recheck.json/.md per cell from a run journal')
    exporting.add_argument('--run', required=True)
    exporting.add_argument('--dest', help='default: RUN/export')
    exporting.add_argument('--no-evidence', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'inventory':
            frozen = inventory(args.campaign_root, args.cells, args.parts, args.out)
            for cell in args.cells:
                rows = [r for r in frozen['reviews'] if r['cell'] == cell]
                print(f'{cell}: reviews={len(rows)} reported={sum(r["reported"] for r in rows)}')
            print(f'quarantined={len(frozen["quarantined"])} excluded={len(frozen["excluded"])} inventory={args.out}/inventory.json')
            return 0 if frozen['complete'] else 1
        if args.command == 'plan':
            results = plan(args.inventory, args.out)
            print('No proof was rerun.')
            for cell, result in results.items():
                print(markdown_table(cell, result['reviews'], result['summary']))
            return 0 if all(r['run']['planning_complete'] for r in results.values()) else 1
        if args.command == 'build-runtime':
            build_runtime_command(executor or load_executor(), args)
            return 0
        if args.command == 'check-gate':
            check_gate_command(executor or load_executor(), args)
            return 0
        if args.command == 'run':
            return run(args, executor)
        if args.command == 'status':
            return show_status(args.run, args.verbose)
        results = export(args.run, args.dest, not args.no_evidence)
        for cell, result in results.items():
            print(f'{cell}: complete={result["run"]["complete"]} {result["summary"]["reported"]} claims -> '
                  f'{(Path(args.dest) if args.dest else Path(args.run) / "export") / cell}')
    except (RecheckError, OSError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
