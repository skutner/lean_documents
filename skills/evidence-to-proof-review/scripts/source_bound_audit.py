#!/usr/bin/env python3
"""Markdown-native source-bound-v1 adapter and agent command line.

Only generated units are executed. Authored definitions pass the existing
lexical filter and the profile grammar before they enter those units. The
caller supplies its reviewed sandbox prefix; this module never provisions one.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile

import audit
import source_bound_contract as c
import source_bound_facts as f
import source_bound_templates as t
import source_bound_guards as g
import source_bound_proofs as p


def text(path):
    return Path(path).read_bytes().decode('utf-8')


def path(root, relative, *, exists=True):
    """Reject symlinks even inside the workspace; writes must remain task-owned."""
    root = Path(root).resolve()
    value = Path(relative)
    if value.is_absolute() or '..' in value.parts:
        raise c.ContractError('Expected a workspace-relative path')
    target = root / value
    if any(part.is_symlink() for part in (target, *target.parents) if part != root and part.is_relative_to(root)):
        raise c.ContractError('Source-bound artifacts cannot be symlinks')
    if not target.resolve().is_relative_to(root) or (exists and not target.is_file()):
        raise c.ContractError(f'Missing or escaping artifact: {relative}')
    return target


def write(root, artifact):
    target = path(root, artifact.path, exists=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(artifact.content.encode('utf-8'))


def profile(policy):
    value = policy.get('Formalization profile')
    if value is not None and value != c.PROFILE_VERSION:
        raise c.ContractError(f'Unsupported formalization profile: {value}')
    return value


def initialize_intake(root, args):
    """Called BEFORE preparation seals the immutable manifest."""
    import markdown_audit as md
    locale = getattr(args, 'locale', 'en')
    if locale not in {'en', 'ro'}:
        raise c.ContractError('Source locale must be en or ro')
    overrides = {}
    for item in getattr(args, 'source_locale', ()) or ():
        name, sep, value = item.rpartition('=')
        if not sep or value not in {'en', 'ro'} or name in overrides:
            raise c.ContractError('Expected unique workspace source path=en|ro')
        overrides[name] = value
    extractions = f._rows(text(root / 'input/extraction-index.md')) if (root / 'input/extraction-index.md').exists() else []
    native_originals = {r['Original'] for r in extractions}
    documents = [root / 'input/report.md'] + sorted((root / 'input/sources').rglob('*'))
    bindings = []
    for document in documents:
        name = document.relative_to(root).as_posix()
        if not document.is_file() or name in native_originals:
            continue
        anchors = audit.paragraphs(text(document))
        bindings.append((name, 'report' if name == 'input/report.md' else 'source',
                         overrides.pop(name, locale), ', '.join(anchors)))
    if overrides:
        raise c.ContractError('Unknown source locale paths: ' + ', '.join(sorted(overrides)))
    write(root, c.GeneratedFile('input/source-bound-bindings.md',
        '# Manifest-bound source locales\n\n' + md.table(['Path', 'Origin', 'Locale', 'Anchors'], bindings)))
    initialize(root, require_profile=False)


def require_profile(root):
    import markdown_audit as md
    policy = md.fields(text(path(root, 'audit-policy.md')))
    if profile(policy) != c.PROFILE_VERSION:
        raise c.ContractError('Prepare with --formalization-profile source-bound-v1 first')
    manifest = f._rows(text(path(root, 'manifest.md')))
    required = {'audit-policy.md', 'input/report.md', 'input/source-bound-bindings.md'}
    if not required <= {r['Path'] for r in manifest}:
        raise c.ContractError('Manifest must bind policy, report and source locales')
    for row in manifest:
        if md.digest(path(root, row['Path'])) != row['SHA256']:
            raise c.ContractError('Input changed: ' + row['Path'])
    return policy


def initialize(root, *, require_profile=True):
    root = Path(root).resolve()
    if require_profile:
        globals()['require_profile'](root)
    for name, content in (
        ('source-bound/facts.md', '# Source-bound facts\n'),
        ('source-bound/claims.md', '# Source-bound claims\n'),
        ('source-bound/registrations.md', '# Source-bound rule, domain and scope registrations\n'),
        ('models/Rules.lean', 'import SourceBound.Facts\n')):
        target = path(root, name, exists=False)
        if not target.exists():
            write(root, c.GeneratedFile(name, content))
    return root / 'source-bound'


def bindings(root):
    require_profile(root)
    result = []
    for row in f._rows(text(path(root, 'input/source-bound-bindings.md'))):
        if set(row) != {'Path', 'Origin', 'Locale', 'Anchors'}:
            raise c.ContractError('Unexpected source binding fields')
        if row['Origin'] == 'report' and row['Path'] != 'input/report.md':
            raise c.ContractError('Only the registered report can have report origin')
        actual = frozenset(audit.paragraphs(text(path(root, row['Path']))))
        anchors = frozenset(a.strip() for a in row['Anchors'].split(',') if a.strip())
        if anchors != actual:
            raise c.ContractError('Source binding anchors differ from preserved passages')
        result.append(c.SourceBinding(row['Path'], c.Origin(row['Origin']), row['Locale'], anchors))
    return tuple(result)


def registrations(root):
    """Small strict Markdown adapter to the contract's in-memory registrations."""
    rules, domains, scopes = [], [], []
    for kind, ident, fields in c._records(text(path(root, 'source-bound/registrations.md')), {'Rule', 'Enum', 'Scope'}):
        if kind == 'Enum':
            c._fields(fields, {'Version', 'Labels'})
            domains.append(c.EnumDomain(ident, fields['Version'], tuple(v.strip() for v in fields['Labels'].split('|'))))
            continue
        evidence_keys = {'Source', 'Anchor', 'Quote-start', 'Quote'}
        if kind == 'Rule':
            c._fields(fields, evidence_keys | {'Parameters', 'Result', 'Dependencies', 'Correspondence'},
                      empty={'Parameters', 'Dependencies'})
        else:
            c._fields(fields, evidence_keys | {'Rationale'})
        evidence = (c.EvidenceReference(fields['Source'], fields['Anchor'], fields['Quote'], c._unsigned(fields['Quote-start'])),)
        if kind == 'Scope':
            scopes.append(c.ScopeRegistration(ident, evidence, fields['Rationale']))
            continue
        parameters = []
        for parameter in c._split(fields['Parameters']) if fields['Parameters'] else ():
            name, sep, typ = parameter.partition(':')
            if not sep:
                raise c.ContractError('Parameters require name:TYPE')
            parameters.append((c._identifier(name.strip()), c.parse_type(typ.strip(), generated=True)))
        rules.append(c.RuleRegistration(ident, tuple(parameters), c.parse_type(fields['Result'], generated=True),
            evidence, fields['Correspondence'], c._references(fields['Dependencies'])))
    return tuple(rules), tuple(domains), tuple(scopes)


def render_claim(claim, facts, rules, relation):
    """Phase B plus phase A's dimensional metadata (days are not item counts)."""
    rendered = t.render_claim(claim, facts, rules, relation)
    if rendered.target is None:
        return rendered
    slots = {s.name: s.value for s in claim.slots}
    groups = {'equal': ('a', 'b'), 'compare': ('a', 'b'), 'difference': ('a', 'b', 'v'),
              'sum': ('xs', 'v'), 'ratio': ('a', 'b')}
    units = [f.value_unit(slots[k], facts, rules) for k in groups.get(claim.shape, ())]
    reason = None
    if units and any(u != units[0] for u in units):
        reason = 'Incompatible source dimensions'
    if claim.shape == 'date_add' and f.value_unit(slots['days'], facts, rules) not in {None, 'day'}:
        reason = 'Calendar days require a day count'
    if reason:
        return replace(rendered, proposition=None, target=None, english='', files=(),
                       not_formalized_reason='not formalized: ' + reason)
    return rendered


@dataclass(frozen=True)
class State:
    facts: c.ResolvedFacts
    rules: tuple[c.RuleRegistration, ...]
    claims: tuple[c.ClaimRecord, ...]
    rendered: tuple[c.RenderedClaim, ...]
    files: tuple[c.GeneratedFile, ...]
    authored: tuple[c.GeneratedFile, ...]


def aggregate(rendered):
    # Reuse the catalogue formatter after the adapter's dimensional gate.
    return t._claims_file(tuple(rendered)) or (c.GeneratedFile('models/SourceBound/Claims.lean',
        '-- No formalized claims.\nimport SourceBound.Derived\n'),)


def load(root, *, verify_generated=True):
    root = Path(root).resolve()
    bound = bindings(root)
    rules, domains, scopes = registrations(root)
    document = c.parse_facts(text(path(root, 'source-bound/facts.md')), workspace=root,
        bindings=bound, rules=rules, enum_domains=domains, scopes=scopes)
    facts = f.extract_facts(root, document, bound, rules)
    claims = c.parse_claims(text(path(root, 'source-bound/claims.md')), facts=document,
        report_anchors=frozenset(audit.paragraphs(text(path(root, 'input/report.md')))), rules=rules)
    authored = (c.GeneratedFile('models/Rules.lean', text(path(root, 'models/Rules.lean'))),)
    g.inspect_rules(root / 'models/Rules.lean', facts, rules)
    rendered = tuple(render_claim(claim, facts, rules, claim.proof_relation) for claim in claims)
    import markdown_audit as md
    summary = '# Generated source-bound claim renderings\n\n' + md.table(
        ['Claim ID', 'Report anchor', 'Shape', 'Polarity', 'Proof relation', 'English', 'Target', 'Limit'],
        [(r.claim.id, r.claim.report_anchor, r.claim.shape, r.claim.polarity, r.relation,
          r.english, r.target or '', r.not_formalized_reason or 'Semantic correspondence still requires review.') for r in rendered])
    files = f.generate_facts(facts, rules) + aggregate(rendered) + (c.GeneratedFile('source-bound/rendered.md', summary),)
    expected_models = {a.path for a in files + authored if a.path.endswith('.lean')}
    actual_models = {a.relative_to(root).as_posix() for a in (root / 'models').rglob('*') if a.is_file()}
    if actual_models - expected_models:
        raise c.ContractError('Only generated SourceBound modules and authored Rules.lean are allowed')
    if verify_generated:
        for artifact in files:
            if path(root, artifact.path).read_bytes() != artifact.content.encode('utf-8'):
                raise c.ContractError('Deterministic regeneration mismatch: ' + artifact.path)
    return State(facts, rules, claims, rendered, files, authored)


def regenerate(root):
    state = load(root, verify_generated=False)
    for artifact in state.files:
        write(root, artifact)
    return Path(root) / 'source-bound/rendered.md'


def add_fact(args):
    root = Path(args.workspace).resolve()
    bound = bindings(root)
    rules, domains, scopes = registrations(root)
    filename = path(root, 'source-bound/facts.md')
    original = text(filename)
    document = c.parse_facts(original, workspace=root, bindings=bound, rules=rules, enum_domains=domains, scopes=scopes)
    record = f.make_fact(root, id=args.id, type=c.parse_type(args.type), source_path=args.source,
        anchor=args.anchor, quote=args.quote, selected_token=args.token, bindings=bound,
        occurrence=getattr(args, 'occurrence', None), quote_occurrence=getattr(args, 'quote_occurrence', None),
        unit_source=c.Reference(args.unit_source) if getattr(args, 'unit_source', None) else None, context=document)
    # Summary is derived display, so remove it before appending another record.
    updated = original.split('## Summary', 1)[0].rstrip() + '\n\n' + f._fact_text(record)
    parsed = c.parse_facts(updated, workspace=root, bindings=bound, rules=rules, enum_domains=domains, scopes=scopes)
    f.extract_facts(root, parsed, bound, rules)
    filename.write_bytes(updated.encode('utf-8'))
    return filename


def validate_findings(root, findings):
    state = load(root)
    by_id = {f['claim_id']: f for f in findings}
    if by_id.keys() != {claim.id for claim in state.claims}:
        raise c.ContractError('Every audited finding requires exactly one source-bound claim registration')
    for rendered in state.rendered:
        finding = by_id[rendered.claim.id]
        finding['source_bound'] = dict(english=rendered.english, target=rendered.target,
            reason=rendered.not_formalized_reason or '', relation=str(rendered.relation))
        if finding['sections']['Report anchor'] != rendered.claim.report_anchor:
            raise c.ContractError('Source-bound claim and exact report registration disagree')
        proofs = finding['proofs']
        if proofs:
            expected = dict(claim_id=rendered.claim.id, module='SourceBound.Claims',
                theorem=c.lean_claim_name(rendered.claim.id), target=rendered.target,
                relation='auxiliary' if finding['sections']['Formal status'] == 'auxiliary' else str(rendered.relation))
            if proofs != [expected] or rendered.target is None:
                raise c.ContractError('Authored proof must name the exact generated claim target and relation')
        if rendered.target is None:
            c.validate_status(c.FormalStatus(finding['sections']['Formal status']),
                c.resolve_status(supported=False, baseline_checked=False, flags=frozenset(), relation=rendered.relation), None if not proofs else 'proof')
    return state


def implementation_hashes():
    base = Path(__file__).resolve().parent
    names = ('source_bound_audit.py', 'source_bound_contract.py', 'source_bound_facts.py',
             'source_bound_templates.py', 'source_bound_guards.py', 'source_bound_proofs.py',
             'markdown_audit.py', 'markdown_proofs.py', 'audit.py')
    files = [(name, base / name) for name in names]
    files.append(('Types.lean', base.parent / 'assets/source_bound/Types.lean'))
    return [('@implementation/' + name, hashlib.sha256(file.read_bytes()).hexdigest()) for name, file in files] + [
        ('@version/' + name, hashlib.sha256(value.encode()).hexdigest()) for name, value in (
            ('profile', c.PROFILE_VERSION), ('parser', c.PARSER_VERSION), ('catalogue', c.CATALOGUE_VERSION))]


def fingerprints(root):
    import markdown_audit as md
    extra = []
    for folder in ('source-bound', 'tmp/source-bound-checks'):
        directory = path(root, folder, exists=False)
        for file in sorted(directory.rglob('*')):
            if file.is_file():
                relative = file.relative_to(root).as_posix()
                extra.append((relative, md.digest(path(root, relative))))
    return extra + implementation_hashes()


def command(args):
    executable = shutil.which(str(args.lean)) or str(Path(args.lean).resolve())
    prefix = getattr(args, 'command_prefix', ()) or ()
    if isinstance(prefix, str):
        prefix = tuple(shlex.split(prefix))
    return (*prefix, executable) if prefix else ()


def run(root, args, *, findings=None):
    root = Path(root).resolve()
    state = validate_findings(root, findings) if findings is not None else load(root)
    import markdown_audit as md
    before = dict(md.fingerprints(root))
    history = path(root, 'tmp/source-bound-checks', exists=False)
    history.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix='run-', dir=history))
    sandbox = command(args)
    # Same launcher convention as markdown_proofs: executable (or prefix plus
    # executable) followed by --version/unit. No shell, no ambient Lean imports.
    if any(r.target for r in state.rendered) and sandbox:
        if Path(sandbox[0]).name != 'bwrap' or not ({'--unshare-net', '--unshare-all'} & set(sandbox)) or '--share-net' in sandbox:
            raise c.ContractError('A reviewed no-network Bubblewrap prefix is required')
        from markdown_proofs import _execute
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(attempt), 'LEAN_PATH': '', 'LANG': 'C.UTF-8'}
        output = _execute([*sandbox, '--version'], attempt, attempt / 'version.md', env, 10)
        match = re.search(r'\bversion ([^\s,;()]+)', output)
        version = require_profile(root)['Lean version']
        if not match or match[1] != audit.validate_lean_version(version):
            raise c.ContractError(f'Lean {version} required; observed {output.strip()}')
    def batch(facts, rules):
        return aggregate(render_claim(claim, facts, rules, claim.proof_relation) for claim in state.claims)
    engine = p.ProofEngine(f.generate_facts, render_claim, f.recompute, aggregate_claims=batch)
    results, proof_results = [], []
    with p.using_engine(engine):
        for rendered in state.rendered:
            result = g.check_guards(root, rendered, state.facts, state.rules, state.authored,
                budget=c.GuardBudget(), attempt_directory=attempt / rendered.claim.id, sandbox_command=sandbox)
            results.append((rendered, result))
            if result.proof_reference:
                reference = Path(result.proof_reference).relative_to(root).as_posix()
                log = next(str(Path(v).relative_to(root)) for v in result.logs if v.endswith('baseline.log.md'))
                output = text(root / log)
                groups = re.findall(r'depends on axioms:\s*\[([^\]]*)\]', output, re.S)
                axioms = sorted({a.strip() for group in groups for a in group.split(',') if a.strip()})
                proof_results.append(dict(claim_id=rendered.claim.id, module='SourceBound.Claims',
                    theorem=c.lean_claim_name(rendered.claim.id), target=rendered.target,
                    relation='auxiliary' if result.decision.reference_kind == 'auxiliary' else str(rendered.relation),
                    axioms=axioms, check_file=reference, log=log))
    after = dict(md.fingerprints(root))
    # New retained attempts are the only dependencies allowed to appear during a run.
    stable = lambda pairs: {k: v for k, v in pairs.items() if not k.startswith('tmp/source-bound-checks/')}
    if stable(before) != stable(after):
        raise c.ContractError('Audit dependencies changed during verification')
    summary = '# Source-bound verification\n\n' + md.table(
        ['Claim ID', 'Status', 'Flags', 'Reason', 'English', 'Reference kind', 'Proof reference', 'Logs'],
        [(r.claim.id, v.decision.status, ', '.join(sorted(v.flags)), r.not_formalized_reason or v.decision.reason,
          r.english, v.decision.reference_kind or '',
          str(Path(v.proof_reference).relative_to(root)) if v.proof_reference else '',
          ', '.join(str(Path(log).relative_to(root)) for log in v.logs)) for r, v in results])
    summary += '\nNo tested source sensitivity means “not depending on sources under these perturbations”; it does not prove mathematical independence.\n'
    write(root, c.GeneratedFile('source-bound/results.md', summary))
    write(root, c.GeneratedFile(str(attempt.relative_to(root) / 'results.md'), summary))
    if findings is not None:
        by_id = {v['claim_id']: v for v in findings}
        for rendered, result in results:
            finding = by_id[rendered.claim.id]
            status = c.FormalStatus(finding['sections']['Formal status'])
            kind = ('auxiliary' if status == c.FormalStatus.AUXILIARY else 'proof') if finding['proofs'] else None
            c.validate_status(status, result.decision, kind)
    return proof_results, results


def cli_check(args):
    _, results = run(Path(args.workspace), args)
    print(Path(args.workspace) / 'source-bound/results.md')
    # An honest unsupported claim is successful processing, not a successful proof.
    return 1 if any(v.decision.status in {c.FormalStatus.BLOCKED, c.FormalStatus.REJECTED} for _, v in results) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    commands.add_parser('shapes')
    for name in ('init', 'add-fact', 'regenerate', 'check'):
        sub = commands.add_parser(name)
        sub.add_argument('--workspace', required=True)
        if name == 'add-fact':
            for key in ('id', 'type', 'source', 'anchor', 'quote', 'token'):
                sub.add_argument('--' + key, required=True)
            sub.add_argument('--occurrence', type=int)
            sub.add_argument('--quote-occurrence', type=int)
            sub.add_argument('--unit-source')
        if name == 'check':
            sub.add_argument('--lean', default='lean')
            sub.add_argument('--command-prefix', default='', help='Reviewed no-network sandbox prefix, shell-quoted as one argument; never executed by a shell')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'shapes':
            for shape in c.SHAPES.values():
                print(shape.name + '/' + shape.version + ': ' + ', '.join(
                    slot.name + ':' + slot.type + (' [' + ', '.join(slot.choices) + ']' if slot.choices else '') for slot in shape.slots))
        elif args.operation == 'init':
            print(initialize(Path(args.workspace)))
        elif args.operation == 'add-fact':
            print(add_fact(args))
        elif args.operation == 'regenerate':
            print(regenerate(Path(args.workspace)))
        else:
            return cli_check(args)
        return 0
    except (ValueError, OSError, audit.AuditError) as exc:
        print('Source-bound operation failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
