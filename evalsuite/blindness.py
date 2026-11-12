#!/usr/bin/env python3
"""Keep answer keys away from an auditing agent, and look for leaks afterwards.

A blind evaluation is only as blind as the filesystem the audited agent can read.
This module states that boundary as data (the agent-readable roots and the
withheld answer material), refuses layouts where the two overlap, stages a
neutral per-run input folder, and scans finished runs for key material.

It cannot observe what a sandbox really mounted: the caller attests the roots.
A clean scan means no copied key material was detected, not that nothing was read.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil

CANARY_PREFIX = 'EVALSUITE-KEY-CANARY-'
MANIFEST_NAME = 'KEYS-MANIFEST.json'
OVERLAP_WORDS = 8
# Without a stage record, supplied material is recognized by these names inside a run.
SUPPLIED_DIRS = {'input', 'case'}
SUPPLIED_FILES = {'source-ledger.jsonl'}


class BlindnessError(Exception):
    """The requested layout would let the audited agent read withheld material."""


def real(path):
    return Path(os.path.realpath(os.fspath(path)))


def _same_file(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def within(child, parent):
    """True when child is parent or lies under it, by resolved path or by file identity.

    Identity catches aliases that string comparison misses, such as a different letter
    case on a case-insensitive volume.
    """
    child, parent = real(child), real(parent)
    if child == parent or parent in child.parents:
        return True
    return parent.exists() and any(_same_file(ancestor, parent)
                                   for ancestor in (child, *child.parents) if ancestor.exists())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exposure(agent_roots, protected):
    """Report, for each protected path, which agent-readable roots overlap it."""
    rows = []
    for item in protected:
        target = real(item)
        inside = [str(real(root)) for root in agent_roots if within(target, root)]
        around = [str(real(root)) for root in agent_roots if not within(target, root) and within(root, target)]
        rows.append({'path': str(target), 'exists': target.exists(),
                     'readable_from': inside, 'contains_agent_root': around})
    return rows


def attest(agent_roots, protected):
    """Summarize isolation from caller-declared roots; never claims observed enforcement.

    A declared root that is not an existing directory cannot be compared with anything,
    so it leaves the status at not_attested instead of counting as isolated.
    """
    basis = 'Roots are declared by the caller; this tool did not observe the sandbox mounts.'
    missing = [str(root) for root in agent_roots if not Path(root).is_dir()]
    rows = exposure([root for root in agent_roots if Path(root).is_dir()], protected)
    if not agent_roots or missing:
        status = 'not_attested'
    elif any(row['readable_from'] or row['contains_agent_root'] for row in rows):
        status = 'exposed'
    else:
        status = 'isolated_as_attested'
    return {'status': status, 'agent_roots': [str(real(root)) for root in agent_roots],
            'missing_agent_roots': missing, 'protected': rows, 'basis': basis}


def assert_isolated(agent_roots, protected, roots_must_exist=True):
    for root in agent_roots if roots_must_exist else []:
        if not Path(root).is_dir():
            raise BlindnessError(f'Agent root is not an existing directory: {root}')
    for row in exposure(agent_roots, protected):
        if row['readable_from']:
            raise BlindnessError(f"{row['path']} is readable from agent root {row['readable_from'][0]}")
        if row['contains_agent_root']:
            raise BlindnessError(f"{row['path']} contains agent root {row['contains_agent_root'][0]}")


def seal_keys(source, dest, agent_roots):
    """Copy answer keys to a new directory outside every agent root and embed one canary."""
    source, dest = Path(source), Path(dest)
    if not agent_roots:
        raise BlindnessError('Sealing needs at least one declared agent root')
    if dest.exists() and any(dest.iterdir()):
        raise BlindnessError(f'Sealed key directory must be new or empty: {dest}')
    assert_isolated(agent_roots, [dest])
    canary = CANARY_PREFIX + secrets.token_hex(16)
    sealed = {}
    for path in sorted(source.glob('*.json')):
        if path.name == MANIFEST_NAME:
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('expectations'), list):
            raise BlindnessError(f'Not an answer key: {path}')
        data['canary'] = canary
        sealed[path.name] = (sha(path), json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    if not sealed:
        raise BlindnessError(f'No answer keys found in {source}')
    dest.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, (source_sha, text) in sealed.items():
        (dest / name).write_text(text, encoding='utf-8')
        files[name] = {'source_sha256': source_sha, 'sealed_sha256': sha(dest / name)}
    manifest = {'schema_version': 1, 'canary': canary, 'files': files}
    (dest / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def _withheld_hashes(withheld):
    found = {}
    for item in withheld:
        item = Path(item)
        paths = [item] if item.is_file() else sorted(p for p in item.rglob('*') if p.is_file()) if item.is_dir() else []
        for path in paths:
            if path.stat().st_size:
                found[sha(path)] = str(path)
    return found


def _refuse_withheld(data, origin, forbidden):
    digest = hashlib.sha256(data).hexdigest()
    if data and digest in forbidden:
        raise BlindnessError(f'{origin} has the same bytes as withheld {forbidden[digest]}')
    if CANARY_PREFIX.encode() in data:
        raise BlindnessError(f'{origin} contains an answer-key canary')
    return digest


def stage_case(report, sources, arena_root, records_dir, label, withheld, protected=(), brief=None, skill=None,
               forbidden_hashes=None):
    """Copy one report and its sources into a new opaque run folder the agent may read.

    The folder name and the report file name reveal neither dossier nor variant; file
    names under `sources` are copied unchanged. `withheld` paths (answer keys, the
    alternate report, authoring masters) must lie outside the arena and none of their
    bytes may be staged. `protected` paths are checked for location only. The label is
    written to `records_dir`, never to the arena. `forbidden_hashes` maps SHA-256 digests
    to names: it lets a caller that must not open the answer keys still refuse their bytes,
    using the digests the sealing manifest recorded.
    """
    report, sources, arena_root, records_dir = Path(report), Path(sources), Path(arena_root), Path(records_dir)
    if not {'dossier', 'variant'} <= set(label):
        raise BlindnessError('A stage label needs dossier and variant')
    if not withheld and not forbidden_hashes:
        raise BlindnessError('Staging needs the withheld answer material to keep it out of the arena')
    for item in withheld:
        # A mistyped path would otherwise skip the byte comparison without any sign.
        if not Path(item).exists():
            raise BlindnessError(f'Withheld path does not exist: {item}')
    if report.is_symlink() or not report.is_file():
        raise BlindnessError(f'Report must be a regular file: {report}')
    if sources.is_symlink() or not sources.is_dir():
        raise BlindnessError(f'Sources must be a directory: {sources}')
    assert_isolated([arena_root], [records_dir, *withheld, *protected], roots_must_exist=False)
    # Number the audits of one report here, so a campaign cannot reach scoring with two runs under one label.
    index_path, label = records_dir / 'index.json', dict(label)
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    used = {entry.get('repeat', 1) for entry in index.values()
            if (entry.get('dossier'), entry.get('variant')) == (label['dossier'], label['variant'])}
    if label.get('repeat') is None:
        label['repeat'] = max(used, default=0) + 1
    elif label['repeat'] in used:
        raise BlindnessError(f"Repeat {label['repeat']} of this report is already staged")
    forbidden = _withheld_hashes(withheld)
    forbidden.update(forbidden_hashes or {})
    plan = [(report, Path('case/report.md'))]
    if brief:
        plan.append((Path(brief), Path('case/brief.md')))
    for path in sorted(sources.rglob('*')):
        if path.is_symlink():
            raise BlindnessError(f'Symbolic link in sources: {path}')
        if path.is_file():
            plan.append((path, Path('case/sources') / path.relative_to(sources)))
    if len(plan) < 2 + bool(brief):
        raise BlindnessError(f'No source files in {sources}')
    staged = []
    for origin, relative in plan:
        data = origin.read_bytes()
        staged.append((relative, data, _refuse_withheld(data, origin, forbidden)))
    opaque = 'run-' + secrets.token_hex(8)
    run = arena_root / opaque
    arena_root.mkdir(parents=True, exist_ok=True)
    run.mkdir()  # Fails if the folder exists, so the cleanup below removes only what this call made.
    try:
        for relative, data, _ in staged:
            target = run / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if skill:
            shutil.copytree(skill, run / 'skill', symlinks=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.lake'))
        for path in run.rglob('*'):
            if path.is_symlink():
                raise BlindnessError(f'Staged path is a symbolic link: {path.relative_to(run)}')
            if path.is_file():
                _refuse_withheld(path.read_bytes(), f'Staged file {path.relative_to(run)}', forbidden)
    except Exception:
        shutil.rmtree(run)
        raise
    record = {'schema_version': 1, 'opaque_id': opaque, 'arena_run': str(real(run)), 'label': label,
              'agent_roots': [str(real(run))],
              'case_files': {str(relative): digest for relative, _, digest in staged},
              'skill_staged': bool(skill)}
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / f'{opaque}.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    index[opaque] = label
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return record


def _words(text):
    return re.findall(r'[a-z0-9]+', text.lower())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _normalized(path, text):
    if path.suffix.lower() in {'.json', '.jsonl'}:
        try:
            lines = text.splitlines() if path.suffix.lower() == '.jsonl' else [text]
            text = '\n'.join(s for line in lines if line.strip() for s in _strings(json.loads(line)))
        except ValueError:
            pass
    return ' ' + ' '.join(_words(text)) + ' '


def scan_run(run_dir, key_rows, min_words=OVERLAP_WORDS, supplied_digests=None):
    """Look for a canary and for key rationale wording copied into agent-authored files.

    A phrase of `min_words` consecutive rationale words counts only when it appears in
    an authored file and nowhere in the supplied report, brief or sources. With
    `supplied_digests` (the stage record's file hashes) a file is supplied only when its
    bytes match one of them, so a file the agent wrote cannot pose as an input. Without
    them, supplied files are recognized by folder name, which the agent could imitate.
    Hits need human reading: independent authors can coincide, and reviewers may hold the keys.
    """
    run_dir = Path(run_dir)
    canary_hits, authored, supplied = [], {}, []
    for path in sorted(run_dir.rglob('*')):
        relative = path.relative_to(run_dir)
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if CANARY_PREFIX.encode() in data:
            canary_hits.append(str(relative))
        if '.lake' in relative.parts:
            continue
        try:
            text = _normalized(path, data.decode('utf-8'))
        except UnicodeDecodeError:
            continue
        if supplied_digests is not None:
            is_supplied = hashlib.sha256(data).hexdigest() in supplied_digests
        else:
            is_supplied = relative.parts[0] in SUPPLIED_DIRS or path.name in SUPPLIED_FILES
        if is_supplied:
            supplied.append(text)
        else:
            authored[str(relative)] = text
    overlaps = []
    for row in key_rows:
        tokens = _words(row.get('rationale', ''))
        hits = {}
        for start in range(len(tokens) - min_words + 1):
            phrase = ' ' + ' '.join(tokens[start:start + min_words]) + ' '
            if any(phrase in text for text in supplied):
                continue
            for name, text in authored.items():
                if phrase in text:
                    hits.setdefault(name, []).append(start)
        for name, starts in sorted(hits.items()):
            span_start = previous = starts[0]
            for start in starts[1:] + [None]:
                if start is not None and start == previous + 1:
                    previous = start
                    continue
                overlaps.append({'claim_id': row.get('claim_id'), 'variant': row.get('variant'), 'file': name,
                                 'words': previous + min_words - span_start,
                                 'text': ' '.join(tokens[span_start:previous + min_words])})
                span_start = previous = start
    return {'canary_hits': canary_hits, 'verbatim_overlaps': overlaps,
            'authored_files_scanned': len(authored), 'min_words': min_words,
            'supplied_basis': 'stage_record' if supplied_digests is not None else 'folder_names'}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('exposure', help='Report which protected paths the declared agent roots can read')
    check.add_argument('--agent-root', action='append', default=[], type=Path)
    check.add_argument('--protected', action='append', default=[], type=Path, required=True)
    seal = commands.add_parser('seal', help='Copy answer keys outside the agent roots and embed a canary')
    seal.add_argument('--source', type=Path, required=True)
    seal.add_argument('--dest', type=Path, required=True)
    seal.add_argument('--agent-root', action='append', default=[], type=Path, required=True)
    stage = commands.add_parser('stage', help='Stage one report and its sources in a new opaque run folder')
    stage.add_argument('--report', type=Path, required=True)
    stage.add_argument('--sources', type=Path, required=True)
    stage.add_argument('--arena', type=Path, required=True)
    stage.add_argument('--records', type=Path, required=True)
    stage.add_argument('--dossier', required=True)
    stage.add_argument('--variant', required=True)
    stage.add_argument('--repeat', type=int, help='Audit number of this report; the next free number when omitted')
    stage.add_argument('--withheld', action='append', default=[], type=Path, required=True)
    stage.add_argument('--protected', action='append', default=[], type=Path)
    stage.add_argument('--brief', type=Path)
    stage.add_argument('--skill', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'exposure':
            result = attest(args.agent_root, args.protected)
        elif args.command == 'seal':
            manifest = seal_keys(args.source, args.dest, args.agent_root)
            result = {'sealed': str(real(args.dest)), 'files': sorted(manifest['files'])}
        else:
            record = stage_case(args.report, args.sources, args.arena, args.records,
                                {'dossier': args.dossier, 'variant': args.variant, 'repeat': args.repeat},
                                withheld=args.withheld, protected=args.protected, brief=args.brief, skill=args.skill)
            # The label stays in the records directory; print only what the agent side may know.
            result = {'arena_run': record['arena_run'], 'case_files': len(record['case_files'])}
    except BlindnessError as error:
        raise SystemExit(f'Blindness check failed: {error}')
    print(json.dumps(result, indent=2))
    if args.command == 'exposure' and result['status'] != 'isolated_as_attested':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
