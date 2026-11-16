"""Generate blind, single-error Lean submissions; never invoke a model.

Run ``python -m evalsuite.fidelity_mutants generate --help`` for the CLI. All
output belongs under a fresh repository tmp/ directory. Only individual case
folders are checker inputs; key.json, evidence/ and excluded/ are evaluator-only.

The family graders test operational rules and supported claim predicates, not
submitted fact tables. A planted fact error (one transcribed cell changed, with no
rule or claim edit) is therefore measured the way the interface scorer measures it:
fidelity_score's comparison of the recorded cells with the generator's. It counts as
effective when fact accuracy drops below 100%. Any attempt that is not effective is
preserved under excluded/ with its executed grade and dossier visibility, but is
NEVER advertised as unseen-effective. Unsupported claim coverage is explicit.

Casework and procurement keep their rule engines behind string switches in the
authored fixtures, which name every slip. Their blind files are built from the
slip-free engine in fixtures/Neutral.lean plus one named edit (fidelity_plants.py),
and each planted rule is checked against the generator's own output for that slip.

Silence means agreement on every completed dossier world, for both the full rule
output on every row and every supported report claim. It does not mean the source
text cannot reveal the error. Own tests compare claim truth vectors, including
false/undetermined claims in the report; report truth is not formalization fidelity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil

from evalsuite import fidelity_plants, fidelity_score, generate
from evalsuite.fidelity import FidelityError, parse_vectors, run_checked, trusted_lean_runner

FAMILIES = ('approvals', 'ledger', 'deadlines', 'packet', 'casework', 'procurement')
HARD = ('casework', 'procurement')
REPORTS = ('correct', 'mixed')
ROOT = Path(__file__).resolve().parents[1]
LIMITS = [
    'Bounded generated inputs; no universal semantic equivalence claim.',
    'Faithful means 100% on rules and supported claims, not full report coverage.',
    'Fact errors are measured by fact accuracy (recorded cells, as the interface scorer counts them), not by the unseen-input graders.',
    'Filesystem staging is not a sandbox: mount only the selected case for a checker.',
]


def family_module(family):
    if family not in FAMILIES:
        raise FidelityError(f'Unknown family: {family}')
    return importlib.import_module(f'evalsuite.fidelity_{family}')


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def opaque(*values):
    return 'case-' + hashlib.sha256(json.dumps(values).encode()).hexdigest()[:20]


def clean(source):
    """Remove fixture provenance comments, not Lean code or document text."""
    return '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith('--')) + '\n'


def blindness_scan(folder):
    """Reject evaluator labels in file names/content and all symlinks.

    Policy/source words such as 'failed', 'incorrect' or a lowercase 'reference' are
    legitimate prose; this scan targets exact fixture identifiers, answer-key fields,
    and labels (the file names Reference and Fixture are matched with capitals).
    Semantic code differences (e.g. a constant claim) are deliberately observable.
    """
    folder = Path(folder)
    identifiers = [v for f in FAMILIES for v in family_module(f).VARIANTS]
    pattern = re.compile(r'(?i)\b(?:faithful|not_faithful|evaluator_only|expected_verdict|'
                         r'semantic_state|rule_fidelity|claim_fidelity|planted|mutant|'
                         r'report-correct|report-mixed|correct/|mixed/|(?-i:Reference|Fixture)|'
                         + '|'.join(map(re.escape, identifiers)) + r')\b')
    findings = []
    if folder.is_symlink():
        findings.append(str(folder))
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            findings.append(str(path.relative_to(folder)) + ': symlink')
        elif path.is_file():
            text = str(path.relative_to(folder)) + '\n' + path.read_text(encoding='utf-8')
            if pattern.search(text):
                findings.append(str(path.relative_to(folder)) + ': evaluator label')
    if findings:
        raise FidelityError('Blindness scan failed: ' + ', '.join(findings))
    return True


def _definitions(source):
    """Split authored fixture definitions, whose top-level layout is controlled."""
    return {m.group(1): m.group(0).rstrip() for m in re.finditer(
        r'^def (\w+)\b.*?(?=^def |^end |\Z)', clean(source), re.M | re.S)}


def _select_arms(source, variant):
    """Keep one report's dispatch arms, whole: an arm is its header and the indented lines after it."""
    out, keep = [], True
    for line in source.splitlines():
        if re.match(r'  \| "(?:correct|mixed)/', line):
            keep = line.startswith(f'  | "{variant}/')
        elif not line.startswith('    '):
            keep = True
        if keep:
            out.append(line)
    return '\n'.join(out).replace(f'"{variant}/', '"') + '\n'


def _hard_candidate(module, material, variant, slip=None):
    """Slip-free engine with at most one named edit, followed by the fixture's claim dispatch."""
    family = material.regenerated.key['generator']['family']
    fixture = clean(module.fixture(material).read_text())
    claims = fixture[fixture.index(f'\nnamespace Candidate\nopen {family.title()}\ndef claims'):]
    claims = re.sub(r'\bapplyRule\b', 'rule', claims)
    return _select_arms(clean(fidelity_plants.plant(family, slip)) + claims, variant)


def _candidate(module, material, variant, slip=None):
    """Retain only the chosen rule implementation, with neutral identifiers."""
    if material.regenerated.key['generator']['family'] in HARD:
        return _hard_candidate(module, material, variant, slip)
    source = module.fixture(material).read_text()
    base_rule = {'approvals': 'meets', 'ledger': 'rules', 'deadlines': 'applyRule', 'packet': 'meets'}[
        material.regenerated.key['generator']['family']]
    source = clean(source)
    if slip:
        defs = _definitions((module.LEAN_DIR / 'fixtures/Slips.lean').read_text())
        # Approvals variants use one common helper. No other slip is included.
        extra = (defs['distinctCounting'] + '\n') if 'distinctCounting' in defs[slip] else ''
        extra += defs[slip]
        extra = re.sub(r'\b' + re.escape(slip) + r'\b', 'rule', extra)
        boundary = source.index('\nend Candidate')
        source = source[:boundary] + '\n' + extra + '\n' + source[boundary:]
        pos = source.index('def claims')
        source = source[:pos] + re.sub(r'\b' + base_rule + r'\b', 'rule', source[pos:])
    else:
        # Keep the same helper layout in every case. 'rule' is the public location.
        pos = source.index('def claims')
        source = source[:pos] + re.sub(r'\b' + base_rule + r'\b', 'rule', source[pos:])
        boundary = source.index('\nend Candidate')
        source = source[:boundary] + f'\ndef rule : Rule := {base_rule}\n' + source[boundary:]
    return _select_arms(source, variant)


def _facts(module, material, family):
    lines = (material.directory / 'Facts.lean').read_text().splitlines()
    recorded = next(x for x in lines if x.startswith('def recorded '))
    dossier = next(x for x in lines if x.startswith('def dossier '))
    worlds = material.truth['sealed_worlds']
    if family == 'approvals':
        people = sorted(material.regenerated.case['base']['roles'])
        entries = []
        for w in worlds:
            resolutions = [f'({json.dumps(k[7:])}, {people.index(w[k])})'
                           for k in sorted(w) if k.startswith('signer:')]
            entries.append('{ recorded with resolutions := [' + ', '.join(resolutions) + '] }')
        expression = '[' + ', '.join(entries) + ']'
    elif family == 'ledger':
        entries = []
        for w in worlds:
            approvals = [f'({json.dumps(k[9:])}, {str(w[k]).lower()})'
                         for k in sorted(w) if k.startswith('approval:')]
            entries.append('{ recorded with approvals := [' + ', '.join(approvals)
                           + f'], unsampledMissing := {w["unsampled_missing"]} }}')
        expression = '[' + ', '.join(entries) + ']'
    elif family in ('deadlines', 'casework'):
        expression = next(x for x in lines if x.startswith('def sealedWorlds ')).split(':=', 1)[1].strip()
    elif family == 'procurement':
        # Facts.lean derives recorded from a completed world. Submit recorded as its own literal,
        # so a planted transcription error is one cell on that line, and complete it from there.
        completed = next(x for x in lines if x.startswith('def first '))
        head, sep, tail = completed.partition(', amounts := ')
        if not sep:
            raise FidelityError('Unexpected procurement facts layout')
        recorded = ('def recorded : World := ' + head[len('def first : World := '):]
                    + ', amounts := [], signers := [], lastDays := [] }')
        dossier += '\ndef first : World := { recorded with amounts := ' + tail
        expression = next(x for x in lines if x.startswith('def sealedWorlds ')).split(':=', 1)[1].strip()
    else:
        expression = 'possibleWorlds dossier'
    return (f'namespace Submitted\nopen {family.title()}\nnamespace Facts\n{recorded}\n{dossier}\n'
            f'def worlds : List World := {expression}\nend Facts\nend Submitted\n')


def _fact_change(facts, material, family):
    """One source cell only; no compensating rule or claim edits."""
    base = material.regenerated.case['base']
    if family == 'approvals':
        ident = base['requests'][0]['id']
        pattern = rf'(⟨"{ident}", \d+, )(\d+)'
        field = 'amount'
    elif family == 'ledger':
        ident = next(e['id'] for e in base['entries'] if not e['voided'])
        pattern = rf'(⟨"{ident}", "[^"]+", \d+, \d+, )(\d+)'
        field = 'amount'
    elif family == 'deadlines':
        ident = base['notices'][0]['id']
        pattern = rf'(⟨"{ident}", )(\d+)'
        field = 'issued'
    elif family == 'casework':
        recorded = material.truth['recorded']
        ident = recorded['registers'][recorded['in_force']][0]['id']
        pattern = rf'(⟨"{ident}", )(\d+)'
        field = 'issued'
    elif family == 'procurement':
        ident = next(r['id'] for r in material.truth['recorded']['data']['requests'] if r['amount'] is not None)
        pattern = rf'(⟨"{ident}", ⟨\d+, \d+⟩, "[^"]*", "[^"]*", \(some )(\d+)'
        field = 'amount'
    else:
        ident = next(u['id'] for u in material.truth['recorded']['units'] if u['certificate'])
        pattern = rf'(⟨"{ident}", \(some ⟨\.\w+, )(\d+)'
        field = 'testedDay'
    found = list(re.finditer(pattern, facts))
    # A casework notice is listed once in each of the two registers: change the one in force.
    position = material.truth['recorded']['in_force'] if family == 'casework' else 0
    if len(found) != (2 if family == 'casework' else 1):
        raise FidelityError('Fact cell must occur exactly once in the submitted table')
    old = int(found[position].group(2))
    start, end = found[position].span(2)
    return (facts[:start] + str(old + 1) + facts[end:],
            {'kind': 'fact', 'id': f'{ident}.{field}'}, {'before': old, 'after': old + 1})


def _arm_blocks(lines):
    """Map each dispatch arm's ID to its (start, end) lines: the header and its indented continuation."""
    blocks, ident, start = {}, None, 0
    for i, line in enumerate(lines + ['end']):
        if ident is not None and not line.startswith('    '):
            blocks[ident] = (start, i)
            ident = None
        header = re.match(r'  \| "([^"]+)" =>', line)
        if header:
            ident, start = header.group(1), i
    return blocks


def _item_ids(material, family):
    """Source order of the request or notice IDs a claim can name, for the harder families."""
    recorded = material.truth['recorded']
    if family == 'casework':
        return [n['id'] for n in recorded['registers'][recorded['in_force']]]
    return [r['id'] for r in recorded['data']['requests']]


def _claim_changes(source, claims, material, family):
    lines = source.splitlines()
    blocks = _arm_blocks(lines)

    def swap(ident, new):
        start, end = blocks[ident]
        return '\n'.join(lines[:start] + new + lines[end:]) + '\n'
    # Each alternative changes exactly ONE dispatch arm, never the whole report.
    for c in claims:
        if any(v != c['sealed'][0] for v in c['unseen']):
            ident = c['id'].split('/')[1]
            result = swap(ident, [f'  | "{ident}" => {str(c["sealed"][0]).lower()}'])
            prefix, body = result.split('def claims', 1)
            if len(re.findall(r'\bw\b', body)) == 1:
                # A singleton supported report loses its only world use. Mark
                # that binder unused; do not disable the Lean warning gate or
                # introduce a fake fact dependency into the constant test.
                body = body.replace('(w : World)', '(_w : World)', 1)
            yield 'constant', ident, prefix + 'def claims' + body
            break
    if family in HARD:
        ids = _item_ids(material, family)
        quoted = re.compile('"(' + '|'.join(map(re.escape, ids)) + ')"')
    else:
        prefix = {'approvals': 'Q', 'ledger': 'P', 'deadlines': 'N', 'packet': 'U'}[family]
    for c in claims:
        ident = c['id'].split('/')[1]
        start, end = blocks[ident]
        if family in HARD:
            match = quoted.search('\n'.join(lines[start:end]))
            if match:
                old = match.group(1)
                other = next(i for i in ids if i != old)
                # Every mention inside the arm moves together, so the claim stays about one other row.
                block = [line.replace(f'"{old}"', f'"{other}"') for line in lines[start:end]]
                yield 'wrong_item', ident, swap(ident, block)
                break
            continue
        match = re.search(rf'"({prefix}\d+)"', lines[start])
        if match:
            old = match.group(1)
            other = prefix + ('02' if old == prefix + '01' else '01')
            yield 'wrong_item', ident, swap(ident, [lines[start].replace(f'"{old}"', f'"{other}"', 1)])
            break
    for c in claims:
        ident = c['id'].split('/')[1]
        start, end = blocks[ident]
        # Numeric equality to lower bound; do not change Boolean/role equality. A multi-line arm keeps its test.
        if end - start == 1 and re.search(r' == \d+\)*$', lines[start]):
            yield 'weaker', ident, swap(ident, [re.sub(r' == (\d+\)*)$', r' >= \1', lines[start])])
            break


def _checks(claims):
    tests = []
    for c in claims:
        ident = json.dumps(c['id'].split('/')[1])
        expected = '[' + ', '.join(str(v).lower() for v in c['sealed']) + ']'
        tests.append(f'(Submitted.Facts.worlds.map (Candidate.model.claim {ident}) == {expected})')
    return ('\ndef dossierChecks : List Bool := [' + ',\n  '.join(tests) + ']\n'
            '#eval "CHECKS|" ++ String.mk (dossierChecks.map (fun b => if b then \'1\' else \'0\'))\n')


def _bits(output, marker):
    lines = [json.loads(line) for line in output.splitlines() if line.startswith('"' + marker + '|')]
    if len(lines) != 1 or set(lines[0].split('|', 1)[1]) - {'0', '1'}:
        raise FidelityError(f'Missing or malformed {marker} output')
    return [v == '1' for v in lines[0].split('|', 1)[1]]


def _grade(module, base, directory, fragment, canonical, variant, runner, claims, slip=None):
    """Use the family grader, adapting neutral IDs without altering predicates."""
    directory.mkdir(parents=True)
    material = copy(base)
    material.directory = directory
    # Reuse only the reference validation performed on this exact object in this
    # process. No disk success marker is trusted; candidate runs remain distinct.
    if not getattr(base, '_validated', False):
        raise FidelityError('Reference validation has not executed')
    for name in ('Facts.lean', 'Inputs.lean'):
        shutil.copyfile(base.directory / name, directory / name)
    original = canonical.replace('namespace Candidate', 'namespace Original').replace('end Candidate', 'end Original')
    adapted = fragment.replace('def model : Model', 'def submittedModel : Model').replace('Candidate.model', 'Candidate.submittedModel')
    dispatch = []
    for c in base.truth['claims']:
        cid = c['id']
        body = f'Candidate.submittedModel.claim {json.dumps(cid.split("/")[1])}' if cid.startswith(variant + '/') else f'Original.model.claim {json.dumps(cid)}'
        dispatch.append(f'  | {json.dumps(cid)} => {body}')
    source = original + '\n' + adapted + '\nnamespace Candidate\n'
    source += f'open {base.regenerated.key["generator"]["family"].title()}\n'
    source += 'def model : Model := ⟨submittedModel.rule, fun id => match id with\n'
    source += '\n'.join(dispatch) + '\n  | _ => fun _ => false⟩\nend Candidate\n'
    family = base.regenerated.key['generator']['family']
    if family in ('ledger', 'procurement'):
        compare = 'Original.model.rule pair.1 == Candidate.model.rule pair.2'
    elif family == 'casework':
        compare = ('((pair.1.registers[pair.1.inForce]!).zip (pair.2.registers[pair.2.inForce]!)).all (fun rows => '
                   'Original.model.rule pair.1 rows.1.id == Candidate.model.rule pair.2 rows.2.id)')
    else:
        field = {'approvals': 'requests', 'deadlines': 'notices', 'packet': 'units'}[family]
        compare = (f'(pair.1.{field}.zip pair.2.{field}).all (fun rows => '
                   'Original.model.rule pair.1 rows.1 == Candidate.model.rule pair.2 rows.2)')
    source += (f'\n#eval "OWNRULE|" ++ (if (sealedWorlds.zip Submitted.Facts.worlds).all '
               f'(fun pair => {compare}) then "1" else "0")\n')
    path = directory / 'Submission.lean'
    path.write_text(source, encoding='utf-8')
    grade = module.grade(material, path, runner)
    output = (directory / 'CandidateCheck.output.txt').read_text()
    lengths = {f'claim{i}': c['input_count'] for i, c in enumerate(base.truth['claims']) if c.get('supported', True)}
    if family in ('approvals', 'packet'):
        lengths['rule0'] = base.truth['unseen_count']
    if family == 'packet':
        lengths['worlds'] = 1
    vectors = parse_vectors(output, lengths)
    named = None
    if family in HARD:
        # The harder graders print whole results, not bits. A planted rule must equal the generator's
        # output for its named slip on every unseen input; every other file must equal its rule.
        if family == 'casework':
            rows = module.parse_results(output, {'rule0': base.truth['unseen_count']})['rule0']
        else:
            rows = module.parse_rows(output, ['rule0'], [len(x) for x in base.truth['rule_expected']])['rule0']
        named = rows == (base.truth['variant_expected'][slip] if slip else base.truth['rule_expected'])
    unseen = []
    for i, c in enumerate(base.truth['claims']):
        if c in claims:
            values = vectors[f'claim{i}'][len(c['sealed']):]
            unseen.extend(a == b for a, b in zip(values, c['unseen']))
    chosen = [row for row in grade['claims'] if row['id'] in {c['id'] for c in claims}]
    return {'rule_rate': grade['rule_fidelity']['rate'],
            'supported_claim_rate': sum(c['agrees_everywhere'] for c in chosen) / len(chosen),
            'unseen_claim_rate': sum(unseen) / len(unseen),
            'own_rule_agrees': all(_bits(output, 'OWNRULE')),
            'own_claim_tests_pass': all(_bits(output, 'CHECKS')), 'rule_equals_generator': named,
            'grader_candidate_sha256': grade['candidate_sha256']}


def _fact_report(module, material, fact_source, directory, runner, expected):
    """Compare the cells a submission records with the generator's, as fidelity_score counts them."""
    family = material.regenerated.key['generator']['family']
    source = ((module.LEAN_DIR / 'Interface.lean').read_text() + '\n' + fidelity_score.fact_serializer(family)
              + '\n' + fact_source + '\n#eval FidelityFacts.printFacts Submitted.Facts.recorded\n')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'SubmittedFacts.lean'
    path.write_text(source, encoding='utf-8')
    cells = fidelity_score.parse_cells(run_checked(path, runner).stdout)
    return fidelity_score.compare_facts(cells, expected)


def generate_dossier(family, seed, index, report_variant, directory, runner=None):
    """Emit one report's candidates. Without a runner all labels stay pending.

    Excluded attempts are retained, never silently dropped. Only a validated,
    effective candidate is copied to cases/. The evaluator-only key also records
    hashes and unsupported claims. A fresh directory is required for every run.
    """
    if report_variant not in REPORTS:
        raise FidelityError('Report variant must be correct or mixed')
    directory = Path(directory).resolve()
    if not directory.is_relative_to((ROOT / 'tmp').resolve()):
        raise FidelityError('Run directories must be under repository tmp/')
    if directory.exists():
        raise FidelityError('Use a distinct, new run directory')
    directory.mkdir(parents=True)
    module = family_module(family)
    built = generate.build_case(family, seed, index)
    material = module.emit(built['key'], directory / 'evidence/reference')
    canonical = clean(module.fixture(material).read_text())
    # fixture() forbids overwriting in some families; construct each requested
    # rule in a fresh shallow material with the same regenerated inputs.
    def candidate(slip=None):
        work = directory / 'evidence/assembly' / (slip or 'base')
        work.mkdir(parents=True)
        view = copy(material)
        view.directory = work
        return _candidate(module, view, report_variant, slip)

    claims = [c for c in material.truth['claims'] if c['id'].startswith(report_variant + '/') and c.get('supported', True)]
    unsupported = [c['id'].split('/')[1] for c in material.truth['claims']
                   if c['id'].startswith(report_variant + '/') and not c.get('supported', True)]
    if not claims:
        raise FidelityError('No supported claims in the chosen report')
    facts = _facts(module, material, family)
    faithful = candidate()
    attempts = [('faithful', 'none', None, faithful, facts, {})]
    attempts += [('rule', slip, {'kind': 'rule', 'id': 'Candidate.rule'}, candidate(slip), facts, {})
                 for slip in module.VARIANTS]
    altered_facts, location, change = _fact_change(facts, material, family)
    attempts.append(('fact', 'transcription', location, faithful, altered_facts, change))
    attempts += [('claim', kind, {'kind': 'claim', 'id': ident}, source, facts, {})
                 for kind, ident, source in _claim_changes(faithful, claims, material, family)]
    if runner is not None:
        module.validate(material, runner)
        expected_cells = fidelity_score.expected_facts(module, family, material, runner)
    fact_reports = {}
    rows, excluded = [], []
    interface = clean((module.LEAN_DIR / 'Interface.lean').read_text())
    for error_class, mutation, location, source, fact_source, change in attempts:
        cid = opaque(family, seed, index, report_variant, error_class, mutation)
        folder = directory / 'attempts' / cid
        folder.mkdir(parents=True)
        for name, content in built['files'].items():
            if name.startswith('sources/'):
                dest = folder / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding='utf-8')
        (folder / 'report.md').write_text(built['files'][f'report-{report_variant}.md'], encoding='utf-8')
        fragment = fact_source + '\n' + source + _checks(claims)
        standalone = interface + '\nset_option maxRecDepth 1000000\nset_option maxHeartbeats 0\n' + fragment
        (folder / 'Formalization.lean').write_text(standalone, encoding='utf-8')
        blindness_scan(folder)
        row = {'case_id': cid, 'family': family, 'seed': seed, 'index': index,
               'report_variant': report_variant, 'error_class': error_class, 'mutation': mutation,
               'location': location, 'claims': [c['id'].split('/')[1] for c in claims],
               'unsupported_claims': unsupported, 'validated': runner is not None,
               'visibility': 'pending', 'effective': None, 'change': change,
               'formalization_sha256': digest(folder / 'Formalization.lean')}
        keep = runner is None
        if runner is not None:
            # Compile the exact blind file separately, retaining logs outside it.
            evidence = directory / 'evidence' / cid
            evidence.mkdir()
            compiled = evidence / 'Formalization.lean'
            shutil.copyfile(folder / 'Formalization.lean', compiled)
            own = run_checked(compiled, runner)
            measured = _grade(module, material, evidence / 'grade', fragment, canonical, report_variant, runner, claims,
                              slip=mutation if error_class == 'rule' else None)
            # Facts differ only in the fact plant, so each distinct table is compared once per dossier.
            table = hashlib.sha256(fact_source.encode()).hexdigest()
            if table not in fact_reports:
                fact_reports[table] = _fact_report(module, material, fact_source, evidence / 'facts' / table[:12],
                                                   runner, expected_cells)
            compared = fact_reports[table]
            measured.update(fact_rate=compared['rate'], fact_cells_wrong=len(compared['mismatches']),
                            fact_first_mismatch=compared['mismatches'][0]['field'] if compared['mismatches'] else None)
            if measured['rule_equals_generator'] is False:
                raise FidelityError(f'{family} {error_class}/{mutation} does not reproduce the generator rule output')
            if error_class != 'fact' and measured['fact_rate'] != 1:
                raise FidelityError(f'A non-fact case changed the recorded facts: {error_class}/{mutation}')
            if measured['own_claim_tests_pass'] != all(_bits(own.stdout, 'CHECKS')):
                raise FidelityError('Standalone and grader dossier checks disagree')
            silent = measured['own_claim_tests_pass'] and measured['own_rule_agrees']
            effective = measured['rule_rate'] < 1 or measured['unseen_claim_rate'] < 1 or measured['fact_rate'] < 1
            row.update(measured, effective=effective, visibility='silent' if silent else 'visible')
            if error_class == 'faithful':
                if effective or not silent or measured['supported_claim_rate'] != 1:
                    raise FidelityError(f'Faithful submission failed validation: {row}')
                row['visibility'] = 'faithful'
                keep = True
            else:
                keep = effective
                if not keep:
                    row['exclusion_reason'] = ('The planted cell did not change the compared recorded facts' if error_class == 'fact'
                                               else 'No unseen counterexample in the bounded family inputs')
            row['evidence'] = str(evidence.relative_to(directory))
        target = directory / ('cases' if keep else 'excluded') / cid
        target.parent.mkdir(exist_ok=True)
        folder.rename(target)
        row['path'] = str(target.relative_to(directory))
        (rows if keep else excluded).append(row)
        write_json(directory / 'key.json', {'schema_version': 1, 'cases': rows,
                   'excluded': excluded, 'limits': LIMITS, 'complete': False})
    key = {'schema_version': 1, 'cases': rows, 'excluded': excluded, 'limits': LIMITS, 'complete': True}
    write_json(directory / 'key.json', key)
    return key


def summarize(keys):
    counts = Counter()
    for key in keys:
        for row in key['cases'] + key['excluded']:
            if row['error_class'] == 'faithful':
                continue
            counts[(row['family'], row['error_class'], row['visibility'], row['effective'])] += 1
    return [{'family': f, 'error_class': c,
             'silent': counts[f, c, 'silent', True], 'visible': counts[f, c, 'visible', True],
             'excluded_silent': counts[f, c, 'silent', False],
             'excluded_visible': counts[f, c, 'visible', False]}
            for f in FAMILIES for c in ('rule', 'fact', 'claim')]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('generate', help='Generate trusted cases; never run a model')
    make.add_argument('--output', type=Path, required=True)
    make.add_argument('--families', default=','.join(FAMILIES))
    make.add_argument('--seed', type=int, default=511)
    make.add_argument('--indices', default='1,2,3,4,5')
    make.add_argument('--reports', default=','.join(REPORTS))
    make.add_argument('--lean', default=os.environ.get('AUDIT_TEST_LEAN'))
    make.add_argument('--prepare-only', action='store_true', help='Unvalidated artifacts, never detector-ready')
    args = parser.parse_args(argv)
    if not args.prepare_only and not args.lean:
        parser.error('--lean is required unless --prepare-only is explicit')
    root = args.output.resolve()
    if root.exists():
        parser.error('output must be a new directory')
    runner = None if args.prepare_only else trusted_lean_runner(args.lean, timeout=1500)
    keys, rows, excluded = [], [], []
    for family in args.families.split(','):
        for index in map(int, args.indices.split(',')):
            for variant in args.reports.split(','):
                relative = Path(f'{family}-{index}-{variant}')
                key = generate_dossier(family, args.seed, index, variant, root / relative, runner)
                keys.append(key)
                for kind, destination in (('cases', rows), ('excluded', excluded)):
                    for row in key[kind]:
                        row = dict(row)
                        row['path'] = str(relative / row['path'])
                        if 'evidence' in row:
                            row['evidence'] = str(relative / row['evidence'])
                        destination.append(row)
                print(f'{family} index={index} report={variant}: {len(key["cases"])} retained, {len(key["excluded"])} excluded', flush=True)
    result = {'schema_version': 1, 'cases': rows, 'excluded': excluded, 'limits': LIMITS, 'complete': True}
    write_json(root / 'key.json', result)
    write_json(root / 'summary.json', summarize(keys))
    print(json.dumps(summarize(keys), indent=2))


if __name__ == '__main__':
    main()
