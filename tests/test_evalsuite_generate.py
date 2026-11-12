"""Checks that generated cases carry answer keys that follow from their rendered documents.

The generator decides each verdict from its structured facts. The tests below decide the same
verdicts a second time with separately written code that reads only the rendered Markdown, as
an auditor would. Agreement shows that the documents say what the generator computed from and
that neither computation slipped. It cannot show that a rule sentence is free of a second
reading: both derivations read the rules the same way. That is a matter for human review of the
few rule sentences, which the wording test at the end keeps honest in one respect: no wording
may be only ever right or only ever wrong.
"""
import collections
import datetime
import itertools
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import blindness  # noqa: E402
import generate  # noqa: E402
import run_blind  # noqa: E402
import scorecard  # noqa: E402

TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-generate-tests-', dir=TEST_ROOT))
SEEDS = range(1, 21)


def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


def rows(body):
    lines = [line for line in body.splitlines() if line.startswith('|')]
    if not lines:
        return []
    cells = lambda line: [cell.strip() for cell in line.strip('|').split('|')]  # noqa: E731
    return [dict(zip(cells(lines[0]), cells(line))) for line in lines[2:]]


def day(text):
    return datetime.date.fromisoformat(text)


def at_least(value, low, high):
    return 'supported' if value <= low else 'refuted' if value > high else 'undetermined'


def exactly(value, low, high):
    return 'supported' if low == high == value else 'refuted' if value < low or value > high else 'undetermined'


def over_all(outcomes):
    """Verdict of a statement from its truth in each remaining possibility."""
    return 'supported' if all(outcomes) else 'undetermined' if any(outcomes) else 'refuted'


def ledger_verdict(kind, text, sources):
    book = sections(sources['ledger.md'])
    corrected = {r['Entry']: int(r['Corrected amount, euro cents'])
                 for r in rows(sections(sources['corrections.md'])['K-01'])}
    paid = [r for r in rows(book['L-02']) if r['Status'] == 'Paid']
    amount = lambda r: corrected.get(r['Entry'], int(r['Ledger amount, euro cents']))  # noqa: E731
    total = sum(amount(r) for r in paid)
    budget = int(re.search(r'exactly (\d+) euro cents', book['L-03'])[1])
    absent = [r for r in paid if r['Required approval at cutoff'] == 'Absent']
    unknown = [r for r in paid if r['Required approval at cutoff'] == 'Approval sheet not in packet']
    number = lambda pattern: int(re.search(pattern, text)[1])  # noqa: E731
    if kind == 'sample_total':
        return 'supported' if number(r'total exactly (\d+) euro cents') == total else 'refuted'
    if kind == 'budget_gap':
        gap = budget - total if text.startswith('The comparison budget exceeds') else total - budget
        return 'supported' if number(r'by exactly (\d+) euro cents') == gap else 'refuted'
    if kind == 'largest_payment':
        top = max(paid, key=amount)
        named = re.search(r'is entry (P\d+), at (\d+) euro cents', text)
        return 'supported' if (named[1], int(named[2])) == (top['Entry'], amount(top)) else 'refuted'
    if kind == 'recorded_approvals':
        present = sum(r['Required approval at cutoff'] == 'Present' for r in paid)
        return 'supported' if number(r'as “Present” for exactly (\d+) of') == present else 'refuted'
    if kind == 'sample_missing_approvals':
        lead, value = re.match(r'(Exactly|At least) (\d+) of', text).groups()
        return (exactly if lead == 'Exactly' else at_least)(int(value), len(absent), len(absent) + len(unknown))
    if kind == 'sample_missing_approval_amount':
        lead, value = re.search(r'total (exactly|at least) (\d+) euro cents', text).groups()
        sums = {sum(amount(r) for r in absent) + sum(amount(r) for r in extra)
                for size in range(len(unknown) + 1) for extra in itertools.combinations(unknown, size)}
        return over_all([s == int(value) if lead == 'exactly' else s >= int(value) for s in sums])
    if kind == 'population_missing_approvals':
        lead, value, population = re.match(r'(Exactly|At least) (\d+) of the (\d+) population', text).groups()
        high = len(absent) + len(unknown) + int(population) - len(paid)
        return (exactly if lead == 'Exactly' else at_least)(int(value), len(absent), high)
    if kind == 'payment_lookup':
        entry, payee, date = re.match(r'Entry (P\d+) was paid to (.+) on (\d{4}-\d\d-\d\d)\.', text).groups()
        row = next(r for r in paid if r['Entry'] == entry)
        return 'supported' if (row['Payee'], row['Payment date']) == (payee, date) else 'refuted'
    annex = sections(sources['annex.md'])['A-01']
    supplied = 'is included in the supplied packet' in annex
    if kind == 'annex_scope':
        return 'refuted' if supplied else 'supported'
    if kind == 'annex_content':
        if not supplied:
            return 'undetermined'      # nothing in the packet states what the annex contains
        says_obtained = 'had been obtained, on' in annex
        return 'supported' if ('confirms that' in text) == says_obtained else 'refuted'
    return None


def deadline_verdict(kind, text, sources):
    policy = sections(sources['policy.md'])
    change, first = re.search(r'issued before (\d{4}-\d\d-\d\d)\. Its reply period is (\d+) days', policy['R-01']).groups()
    second = re.search(r'Its reply period is (\d+) days', policy['R-02'])[1]
    register = {r['Notice']: r for r in rows(sections(sources['register.md'])['G-01'])}
    requests = {r['Notice']: r for r in rows(sections(sources['extensions.md'])['X-01'])}

    def deadlines(notice):
        issued = day(register[notice]['Notice date'])
        base = issued + datetime.timedelta(days=int(first) if issued < day(change) else int(second))
        request = requests.get(notice)
        longer = base + datetime.timedelta(days=int(request['Days requested'])) if request else base
        decision = request['Decision'] if request else 'Refused'
        return {'Granted': [longer], 'Refused': [base]}.get(decision, [base, longer])
    named = re.search(r'[Nn]otice (N\d+)', text)
    notice = named[1] if named else None
    if kind == 'reply_deadline':
        stated = day(re.search(r'is (\d{4}-\d\d-\d\d)\.', text)[1])
        return over_all([d == stated for d in deadlines(notice)])
    if kind == 'rule_version':
        version = 1 if day(register[notice]['Notice date']) < day(change) else 2
        return 'supported' if int(re.search(r'version (\d)', text)[1]) == version else 'refuted'
    if kind == 'notice_lookup':
        return 'supported' if register[notice]['Notice date'] == re.search(r'issued on (\S+)\.', text)[1] else 'refuted'
    if kind == 'reply_timing':
        if register[notice]['Reply received'] == 'Not recorded':
            return 'undetermined'      # a reply on any day, or none, is possible
        received = day(register[notice]['Reply received'])
        late = re.search(r'received (\d+) days? after its deadline', text)
        if late:
            return over_all([(received - d).days == int(late[1]) for d in deadlines(notice)])
        return over_all([received <= d for d in deadlines(notice)])
    if kind == 'late_replies':
        lead, value = re.match(r'(Exactly|At least) (\d+) of', text).groups()
        surely = maybe = 0
        for name, row in register.items():
            if row['Reply received'] == 'Not recorded':
                maybe += 1             # it may have come late, on time or not at all
                continue
            outcomes = [day(row['Reply received']) > d for d in deadlines(name)]
            surely, maybe = surely + all(outcomes), maybe + any(outcomes)
        return (exactly if lead == 'Exactly' else at_least)(int(value), surely, maybe)
    if kind == 'unrecorded_reply':
        return 'supported' if register[notice]['Reply received'] == 'Not recorded' else 'refuted'
    if kind == 'undecided_extension':
        return 'supported' if requests.get(notice, {}).get('Decision') == 'No decision recorded' else 'refuted'
    return None


def approval_verdict(kind, text, sources):
    limit = int(re.search(r'at most (\d+) euro', sections(sources['policy.md'])['Q-01'])[1])
    roles = {r['Person']: r['Role'] for r in rows(sections(sources['staff.md'])['S-01'])}
    requests = {r['Request']: r for r in rows(sections(sources['requests.md'])['B-01'])}

    def outcomes(request):
        """Whether the request meets the policy, for every person an illegible signature could belong to."""
        row = requests[request]
        signers = [row['First signature'], row['Second signature']]
        results = []
        for who in roles if 'Illegible in the supplied scan' in signers else [None]:
            names = [who if s == 'Illegible in the supplied scan' else s for s in signers if s != 'None']
            counting = {n for n in names if n != row['Requester'] and roles[n] != 'Analyst'}
            if int(row['Amount, euro']) <= limit:
                results.append(len(counting) >= 1)
            else:
                results.append(len(counting) >= 2 and any(roles[n] == 'Finance officer' for n in counting))
        return results
    named = re.search(r'[Rr]equest (Q\d+)', text)
    request = named[1] if named else None
    if kind == 'policy_compliance':
        met = 'did not meet' not in text
        return over_all([result == met for result in outcomes(request)])
    if kind == 'approval_tier':
        return 'supported' if ('two counting approvals' in text) == (int(requests[request]['Amount, euro']) > limit) else 'refuted'
    if kind == 'policy_failures':
        lead, value = re.match(r'(Exactly|At least) (\d+) of', text).groups()
        fails = [sum(not result for result in outcomes(r)) for r in requests]      # per request: failing possibilities
        low = sum(len(outcomes(r)) == f for r, f in zip(requests, fails))
        high = sum(f > 0 for f in fails)
        return (exactly if lead == 'Exactly' else at_least)(int(value), low, high)
    if kind == 'signer_role':
        person, role = re.match(r'(.+), who signed request Q\d+, is an? (.+)\.', text).groups()
        return 'supported' if roles[person] == role else 'refuted'
    if kind == 'request_lookup':
        who, amount = re.search(r'was submitted by (.+) for (\d+) euro', text).groups()
        return 'supported' if (requests[request]['Requester'], requests[request]['Amount, euro']) == (who, amount) else 'refuted'
    if kind == 'illegible_signature':
        return 'supported' if requests[request]['Second signature'] == 'Illegible in the supplied scan' else 'refuted'
    return None


def packet_verdict(kind, text, sources, believe_sheet=False):
    """With `believe_sheet`, the verdict of a reader who takes the transmittal sheet at its word: an annex is
    included when the sheet says so, and a certificate is read only if the sheet lists it as included."""
    register = sections(sources['register.md'])
    annex_of = {r['Unit']: r['Calibration certificate'].removeprefix('Annex ') for r in rows(register['E-01'])}
    place_of = {r['Unit']: r['Location'] for r in rows(register['E-01'])}
    review = day(re.search(r'review date is (\d{4}-\d\d-\d\d)', register['E-02'])[1])
    limit = int(re.search(r'at most (\d+) days before', sections(sources['method.md'])['M-02'])[1])
    listed = {r['Annex']: r['Status in this packet'] == 'Included'
              for r in rows(sections(sources['transmittal.md'])['T-02'])}
    on_file = {letter: f'annex-{letter.lower()}.md' in sources for letter in listed}
    included = listed if believe_sheet else on_file

    def certificate(unit):
        """(result, may stay in service), or None when the reader has no certificate to go by."""
        letter = annex_of[unit]
        if not (on_file[letter] and included[letter]):
            return None
        body = sections(sources[f'annex-{letter.lower()}.md'])[f'C{letter}-01']
        tested, result = re.search(rf'Unit {unit} was tested on (\d{{4}}-\d\d-\d\d) by .+\. Result: (Pass|Fail)\.', body).groups()
        return result, result == 'Pass' and (review - day(tested)).days <= limit
    if kind == 'packet_contents':
        letter, negated = re.match(r'Annex ([A-G]) is (not )?included in the supplied packet\.', text).groups()
        return 'supported' if included[letter] != bool(negated) else 'refuted'
    if kind == 'units_in_service':
        lead, value, count = re.match(r'(Exactly|At least) (\d+) of the (\d+) units may stay', text).groups()
        read = [certificate(unit) for unit in annex_of]
        low = sum(found is not None and found[1] for found in read)
        high = low + sum(found is None for found in read)
        return (exactly if lead == 'Exactly' else at_least)(int(value), low, high) if int(count) == len(read) else None
    unit = re.search(r'[Uu]nit (U\d+)', text)[1]
    if kind == 'unit_location':
        return 'supported' if text == f'Unit {unit} is installed in {place_of[unit]}.' else 'refuted'
    found = certificate(unit)
    if kind == 'certificate_result':
        stated = re.search(r'records the result “(Pass|Fail)”', text)[1]
        return 'undetermined' if found is None else 'supported' if found[0] == stated else 'refuted'
    if kind == 'service_status':
        stays = 'may not stay' not in text
        return 'undetermined' if found is None else 'supported' if found[1] == stays else 'refuted'
    return None


SECOND_OPINION = {'ledger': ledger_verdict, 'deadlines': deadline_verdict, 'approvals': approval_verdict,
                  'packet': packet_verdict}

# What a unit's certificate may be for a claim about it to be established: not supplied, or supplied and
# passing in time, passing too long ago, or failing.
PACKET_STATES = {'absent', 'fit', 'late', 'failed'}
PACKET_NEEDS = {'annex not included': {'absent'}, 'annex included': {'fit', 'late', 'failed'},
                'records pass': {'fit', 'late'}, 'records fail': {'failed'},
                'may stay': {'fit'}, 'may not stay': {'late', 'failed'}}


def packet_needs(row_list, unit_of):
    """Read one packet report as its claims alone: what each needs of its unit, and the claim that counts."""
    needs, count = [], None
    for row in row_list:
        text = row['claim_text']
        annex = re.match(r'Annex ([A-G]) is (not )?included', text)
        counted = re.match(r'(Exactly|At least) (\d+) of the (\d+) units may stay', text)
        unit = re.search(r'[Uu]nit (U\d\d)', text)
        if annex:
            needs.append((row, unit_of[annex[1]], 'annex not included' if annex[2] else 'annex included'))
        elif counted:
            count = (row, counted[1], int(counted[2]), int(counted[3]))
        elif row['claim_kind'] == 'certificate_result':
            needs.append((row, unit[1], 'records pass' if '“Pass”' in text else 'records fail'))
        elif row['claim_kind'] == 'service_status':
            needs.append((row, unit[1], 'may not stay' if 'may not stay' in text else 'may stay'))
    return needs, count


def packet_claims_can_all_be_established(needs, count):
    """Whether some packet exists in which every one of these claims would be established. An exact number of
    units that may stay needs every certificate; a lower bound needs that many units that can pass in time."""
    domain = collections.defaultdict(lambda: set(PACKET_STATES))
    for _, unit, need in needs:
        domain[unit] &= PACKET_NEEDS[need]
    if not all(domain.values()):
        return False
    if count is None:
        return True
    _, lead, value, units = count
    can_fit = sum('fit' in states for states in domain.values()) + units - len(domain)
    if lead == 'At least':
        return can_fit >= value
    must_fit = sum(states == {'fit'} for states in domain.values())
    return all(states != {'absent'} for states in domain.values()) and must_fit <= value <= can_fit


def cases():
    for family in generate.FAMILIES:
        for seed in SEEDS:
            yield generate.build_case(family, seed, 1)


class GeneratedCaseTests(unittest.TestCase):
    def test_keys_agree_with_a_second_derivation_from_the_rendered_documents(self):
        checked = total = 0
        for case in cases():
            sources = {name[len('sources/'):]: text for name, text in case['files'].items() if name.startswith('sources/')}
            for row in case['key']['expectations']:
                total += 1
                verdict = SECOND_OPINION[case['family']](row['claim_kind'], row['claim_text'], sources)
                if verdict is None:
                    continue
                checked += 1
                self.assertEqual(verdict, row['semantic_state'],
                                 f'{case["dossier"]} {row["variant"]} {row["claim_id"]}: {row["claim_text"]} ({row["rationale"]})')
        self.assertEqual(checked, total, f'only {checked} of {total} claims were derived a second time')

    def test_correct_reports_hold_only_supported_claims_and_mixed_reports_hold_typed_errors(self):
        states = set()
        for case in cases():
            by_variant = {variant: [r for r in case['key']['expectations'] if r['variant'] == variant]
                          for variant in ('correct', 'mixed')}
            self.assertEqual([r['claim_id'] for r in by_variant['correct']], [r['claim_id'] for r in by_variant['mixed']])
            self.assertEqual(len({r['claim_id'] for r in by_variant['correct']}), len(by_variant['correct']))
            for row in by_variant['correct']:
                self.assertEqual((row['semantic_state'], row['error_type']), ('supported', None))
            wrong = [r for r in by_variant['mixed'] if r['semantic_state'] != 'supported']
            self.assertGreaterEqual(len(wrong), 2, case['dossier'])
            self.assertLess(len(wrong), len(by_variant['mixed']), case['dossier'])
            for row in by_variant['mixed']:
                self.assertEqual(row['error_type'] is not None, row['semantic_state'] != 'supported', row)
                self.assertEqual(row['derivation']['worlds_where_true'] == row['derivation']['worlds'],
                                 row['semantic_state'] == 'supported')
                states.add(row['semantic_state'])
            self.assertNotEqual(case['files']['report-correct.md'], case['files']['report-mixed.md'])
        self.assertEqual(states, {'supported', 'refuted', 'undetermined'})

    def test_reports_and_keys_name_the_same_claims_and_cite_passages_that_exist(self):
        for case in cases():
            for variant in ('correct', 'mixed'):
                ledger = run_blind.paragraphs(case['files'][f'report-{variant}.md'])
                anchors = run_blind.claim_anchors(ledger)
                keyed = [r for r in case['key']['expectations'] if r['variant'] == variant]
                self.assertEqual(sorted(anchors), sorted(r['claim_id'] for r in keyed))
                for row in keyed:
                    self.assertIn(row['claim_text'], ledger[anchors[row['claim_id']]])
                    for anchor in row['source_anchors']:
                        name, _, mark = anchor.partition('#')
                        self.assertIn(f'<a id="{mark}"></a>', case['files'][name], anchor)

    def test_the_same_seed_gives_the_same_case_and_another_seed_another(self):
        for family in generate.FAMILIES:
            first, again, other = (generate.build_case(family, seed, 3) for seed in (5, 5, 6))
            self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(again, sort_keys=True))
            self.assertNotEqual(first['files'], other['files'])

    def test_neither_report_nor_sources_reveal_the_variant_or_the_errors(self):
        for case in cases():
            for name, text in case['files'].items():
                for word in ('mixed', 'correct report', 'error_type', 'refuted', 'undetermined', 'supported'):
                    self.assertNotIn(word, text.lower() if word != 'error_type' else text, f'{case["dossier"]}/{name}')
            self.assertEqual(case['files']['report-correct.md'].count('**C'), case['files']['report-mixed.md'].count('**C'))

    def test_no_wording_is_only_ever_right_or_only_ever_wrong(self):
        """A wording, or a wording with its citations, that occurs only in altered claims would give the
        verdict away without reading a source, and one that is always right would do the same."""
        names = sorted(set(generate.PAYEES) | set(generate.STAFF) | set(generate.SITES), key=len, reverse=True)

        def shape(text):
            for name in names:
                text = text.replace(name, 'NAME')
            for role in sorted(set(generate.ROLES)):
                text = text.replace('an ' + role, 'ROLE').replace('a ' + role, 'ROLE')
            text = re.sub(r'version \d', 'version V', text)
            text = re.sub(r'needed (two counting approvals|one counting approval)', 'needed TIER', text)
            text = re.sub(r'\bAnnex [A-G]\b', 'Annex X', text)
            text = re.sub(r'\b[PNQU]\d\d\b', 'ID', re.sub(r'\d{4}-\d\d-\d\d', 'DATE', text))
            return re.sub(r'\d+', 'N', re.sub(r'\b1 day\b', 'N days', text))
        seen = collections.defaultdict(set)
        for family in generate.FAMILIES:
            for seed in range(1, 121):
                case = generate.build_case(family, seed, 1)
                ledgers = {variant: run_blind.paragraphs(case['files'][f'report-{variant}.md'])
                           for variant in ('correct', 'mixed')}
                anchors = {variant: run_blind.claim_anchors(ledger) for variant, ledger in ledgers.items()}
                for row in case['key']['expectations']:
                    self.assertNotRegex(row['claim_text'], r'(^| )-\d', 'a negative amount or count marks a slip')
                    # The citations as the auditor reads them in the report. The key lists only passages that
                    # exist, so its anchors would differ with what was supplied, which no auditor sees in a claim.
                    passage = ledgers[row['variant']][anchors[row['variant']][row['claim_id']]]
                    cited = passage.split(row['claim_text'], 1)[1].replace('R-01', 'R-0V').replace('R-02', 'R-0V')
                    cited = re.sub(r'annex-[a-g] C[A-G]-01', 'annex-x CX-01', cited)
                    self.assertRegex(cited, r'^ Citations?: .+\.$')
                    seen[(family, row['claim_kind'], shape(row['claim_text']), cited)].add(row['semantic_state'] == 'supported')
        one_sided = sorted(key for key, sides in seen.items() if sides != {True, False})
        self.assertEqual(one_sided, [])
        self.assertGreater(len(seen), 30)
        self.assertGreater(sum(key[0] == 'packet' for key in seen), 8)

    def test_echo_verdicts_are_those_of_a_reader_who_believes_the_transmittal_sheet(self):
        """The echo verdict is derived a second time from the rendered files: the sheet decides what is included,
        and a certificate the sheet lists as not included stays unread. It is kept only where it differs."""
        pairs, conflicts, quiet, silent = collections.Counter(), collections.Counter(), 0, 0
        for seed in range(1, 81):
            case = generate.build_case('packet', seed, 1)
            sources = {name[len('sources/'):]: text for name, text in case['files'].items() if name.startswith('sources/')}
            listed = {r['Annex']: r['Status in this packet'] == 'Included'
                      for r in rows(sections(sources['transmittal.md'])['T-02'])}
            wrong = {letter: says for letter, says in listed.items() if says != (f'annex-{letter.lower()}.md' in sources)}
            conflicts.update('listed as included, not supplied' if says else 'supplied, listed as not included'
                             for says in wrong.values())
            quiet += not wrong
            echoes = 0
            for row in case['key']['expectations']:
                actual = packet_verdict(row['claim_kind'], row['claim_text'], sources)
                believed = packet_verdict(row['claim_kind'], row['claim_text'], sources, believe_sheet=True)
                where = f'{case["dossier"]} {row["variant"]} {row["claim_id"]}: {row["claim_text"]}'
                self.assertEqual(row['semantic_state'], actual, where)
                self.assertEqual(row['echo_state'], believed if believed != actual else None, where)
                self.assertEqual(row['echo_derivation'] is None, row['echo_state'] is None, where)
                if row['echo_state']:
                    echoes += 1
                    pairs[(row['variant'], row['semantic_state'], row['echo_state'])] += 1
            # A sheet that is right about every annex leaves nothing to echo. A wrong one usually does, but not
            # always: the one statement drawn for that unit may be its location, on which the sheet has no bearing.
            if not wrong:
                self.assertEqual(echoes, 0, case['dossier'])
            silent += bool(wrong) and not echoes
        self.assertGreater(quiet, 12, 'the sheet must often be right, or it could be dismissed unread')
        self.assertGreater(80 - quiet, 40)
        self.assertLess(silent, (80 - quiet) / 3, 'too many conflicts that no claim touches')
        self.assertEqual(set(conflicts), {'listed as included, not supplied', 'supplied, listed as not included'})
        # Believing the sheet withholds true claims, rejects true claims, accepts false ones and shrugs at false ones.
        for pair in (('correct', 'supported', 'undetermined'), ('correct', 'supported', 'refuted'),
                     ('mixed', 'refuted', 'supported'), ('mixed', 'refuted', 'undetermined')):
            self.assertGreater(pairs[pair], 5, pair)

    def test_no_claim_is_given_away_by_the_other_claims_about_the_same_unit(self):
        """A report must not show which certificates were supplied. If a unit had a statement about its
        certificate only when the file was there, "Annex B is included" could be judged from the report alone,
        and so could the conflict with the sheet. Every claim, taken together with what else its report says
        about the same unit, must be right in some reports and wrong in others. The generator now makes one
        statement per unit, so there is nothing else to take a claim together with; this test keeps it so."""
        about_certificate = {'certificate_result', 'service_status'}
        seen, contradicted, lengths = collections.defaultdict(set), 0, collections.defaultdict(set)
        drawn = {True: [], False: []}
        for seed in range(1, 401):
            case = generate.build_case('packet', seed, 1)
            register = rows(sections(case['files']['sources/register.md'])['E-01'])
            unit_of = {r['Calibration certificate'].removeprefix('Annex '): r['Unit'] for r in register}
            supplied = sum(name.startswith('sources/annex-') for name in case['files'])
            on_file = {unit: f'sources/annex-{letter.lower()}.md' in case['files'] for letter, unit in unit_of.items()}
            for variant in ('correct', 'mixed'):
                claims = []
                for row in case['key']['expectations']:
                    if row['variant'] != variant:
                        continue
                    annex = re.match(r'Annex ([A-G]) is (not )?included', row['claim_text'])
                    unit = re.search(r'[Uu]nit (U\d\d)', row['claim_text'])
                    if annex:
                        claims.append((row, unit_of[annex[1]], 'annex not included' if annex[2] else 'annex included'))
                    elif unit:
                        claims.append((row, unit[1], row['claim_kind']))
                lengths[(len(register), len(claims))].add(supplied)
                self.assertEqual(sorted(unit for _, unit, _ in claims), sorted(unit_of.values()), 'one statement per unit')
                if variant == 'correct':
                    for _, unit, said in claims:
                        drawn[on_file[unit]].append(said.startswith('annex'))
                flagged = False
                for row, unit, said in claims:
                    others = tuple(sorted(o for r, u, o in claims if u == unit and r is not row))
                    seen[(said, others)].add(row['semantic_state'] == 'supported')
                    flagged |= said == 'annex not included' and bool(about_certificate & set(others))
                # Calling an annex missing while stating what its certificate records is a contradiction, and
                # only an altered report holds one. It must not tell which of the two statements is the wrong one.
                self.assertFalse(flagged and variant == 'correct', case['dossier'])
                contradicted += flagged
        one_sided = sorted(key for key, sides in seen.items() if sides != {True, False})
        self.assertEqual(one_sided, [])
        self.assertEqual({said for said, _ in seen}, {'annex included', 'annex not included', 'certificate_result',
                                                      'service_status', 'unit_location'})
        self.assertEqual(contradicted, 0, 'an altered report can be told apart without a source')
        # Whether a unit gets a statement about its annex or about itself does not go with what was supplied.
        share = {supplied: sum(flags) / len(flags) for supplied, flags in drawn.items()}
        self.assertLess(abs(share[True] - share[False]), 0.06, share)
        # Nor does the number of claims about units give away how many certificates were supplied.
        self.assertTrue(all(len(counts) > 1 for counts in lengths.values()), lengths)

    def test_what_a_packet_report_shows_without_its_sources_is_known_and_bounded(self):
        """A known limit, kept from growing. The claim that counts units can disagree with the claims about
        single units: "Exactly 4 of the 6 units may stay in service" needs every certificate, so beside "Annex D
        is not included" one of the two is not established. A correct report never holds such a pair. About a
        third of the altered ones do, which marks them as altered; in about one in nine the counting claim is
        the only one whose removal settles it. No claim about a single unit is ever singled out that way, so
        no statement about what is included, the claims an echo is asserted on, is given away."""
        altered = singled_out = 0
        for seed in range(1, 401):
            case = generate.build_case('packet', seed, 1)
            register = rows(sections(case['files']['sources/register.md'])['E-01'])
            unit_of = {r['Calibration certificate'].removeprefix('Annex '): r['Unit'] for r in register}
            for variant in ('correct', 'mixed'):
                needs, count = packet_needs([r for r in case['key']['expectations'] if r['variant'] == variant], unit_of)
                self.assertIsNotNone(count, case['dossier'])
                self.assertEqual(len(needs) + sum(r['claim_kind'] == 'unit_location' for r in case['key']['expectations']
                                                  if r['variant'] == variant), len(register), case['dossier'])
                if packet_claims_can_all_be_established(needs, count):
                    continue
                self.assertEqual(variant, 'mixed', f'{case["dossier"]}: a correct report disagrees with itself')
                altered += 1
                settles = [packet_claims_can_all_be_established(needs, None)]
                settles += [packet_claims_can_all_be_established(needs[:i] + needs[i + 1:], count) for i in range(len(needs))]
                self.assertTrue(any(settles), case['dossier'])
                if sum(settles) == 1:
                    self.assertTrue(settles[0], f'{case["dossier"]}: a claim about one unit is singled out')
                    singled_out += 1
        self.assertGreater(altered, 0, 'the limit is gone: say so in the README and tighten this test')
        self.assertLess(altered, 0.45 * 400)
        self.assertLess(singled_out, 0.15 * 400)

    def test_families_without_a_told_world_carry_no_echo_verdict(self):
        for case in cases():
            if case['family'] != 'packet':
                self.assertEqual({row['echo_state'] for row in case['key']['expectations']}, {None}, case['dossier'])

    def test_a_report_may_cite_a_certificate_that_was_not_supplied_but_the_key_never_does(self):
        cited_missing = 0
        for seed in SEEDS:
            case = generate.build_case('packet', seed, 1)
            for variant in ('correct', 'mixed'):
                for name in set(re.findall(r'annex-[a-g]', case['files'][f'report-{variant}.md'])):
                    cited_missing += f'sources/{name}.md' not in case['files']
            for row in case['key']['expectations']:
                for anchor in row['source_anchors']:
                    self.assertIn(anchor.partition('#')[0], case['files'], row)
        self.assertGreater(cited_missing, 0)

    def test_report_passages_are_split_as_the_audit_helper_splits_them(self):
        import audit  # the skill's own helper
        for case in itertools.islice(cases(), 0, None, 7):
            for variant in ('correct', 'mixed'):
                text = case['files'][f'report-{variant}.md']
                self.assertEqual(run_blind.paragraphs(text), audit.paragraphs(text))
        plain = 'First passage.\n\n**D-01.** Second passage.\n'
        self.assertEqual(run_blind.paragraphs(plain), audit.paragraphs(plain))


class GeneratedSuiteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=TEST_RUN))

    def test_a_written_suite_loads_in_the_scorer_seals_and_stages(self):
        fixtures, keys = self.root / 'fixtures', self.root / 'keys'
        summary = generate.write_suite(fixtures, keys, 9, 6, list(generate.FAMILIES))
        self.assertEqual(len(summary['dossiers']), 6)
        self.assertEqual(sum(summary['expected_states'].values()), summary['claims'])
        loaded = scorecard.load_keys(keys)
        self.assertEqual(sorted(loaded), sorted(summary['dossiers']))
        self.assertEqual({key['status'] for key in loaded.values()}, {'computed_by_generator'})
        arena, sealed = self.root / 'arena', self.root / 'sealed'
        arena.mkdir()
        blindness.seal_keys(keys, sealed, [arena])
        for dossier in summary['dossiers']:
            for variant, other in (('correct', 'mixed'), ('mixed', 'correct')):
                record = blindness.stage_case(fixtures / dossier / f'report-{variant}.md', fixtures / dossier / 'sources',
                                              arena, self.root / 'records', {'dossier': dossier, 'variant': variant},
                                              withheld=[sealed, fixtures / dossier / f'report-{other}.md'])
                staged = Path(record['arena_run'])
                self.assertEqual((staged / 'case/report.md').read_bytes(),
                                 (fixtures / dossier / f'report-{variant}.md').read_bytes())
                self.assertNotIn(dossier, record['arena_run'])
        for folder in fixtures.rglob('*'):
            self.assertFalse(folder.name.endswith('.json'), 'no answer material belongs in the fixtures')

    def test_key_wording_is_not_flagged_when_an_auditor_restates_the_claims(self):
        """The leak scan must stay quiet about an honest audit, or every campaign would fail its gate."""
        for case in itertools.islice(cases(), 0, None, 5):
            run = self.root / case['dossier']
            (run / 'case').mkdir(parents=True)
            (run / 'case/report.md').write_text(case['files']['report-mixed.md'], encoding='utf-8')
            notes = '\n'.join(f'{r["claim_id"]}: {r["claim_text"]} I recomputed this from the cited table and it is '
                              f'{r["semantic_state"]}. Stated {r["derivation"].get("stated")}, found '
                              f'{r["derivation"].get("feasible_min")} to {r["derivation"].get("feasible_max")}.'
                              for r in case['key']['expectations'] if r['variant'] == 'mixed')
            (run / 'notes.md').write_text(notes, encoding='utf-8')
            scan = blindness.scan_run(run, case['key']['expectations'])
            self.assertEqual(scan['verbatim_overlaps'], [], case['dossier'])
            (run / 'copied.md').write_text(case['key']['expectations'][-1]['rationale'], encoding='utf-8')
            self.assertTrue(blindness.scan_run(run, case['key']['expectations'])['verbatim_overlaps'])

    def test_folders_that_overlap_or_already_hold_files_are_refused(self):
        with self.assertRaises(generate.GenerationError):
            generate.write_suite(self.root / 'a', self.root / 'a/keys', 1, 1, ['ledger'])
        with self.assertRaises(generate.GenerationError):
            generate.write_suite(self.root / 'b/fixtures', self.root / 'b', 1, 1, ['ledger'])
        (self.root / 'used').mkdir()
        (self.root / 'used/old.txt').write_text('x', encoding='utf-8')
        with self.assertRaises(generate.GenerationError):
            generate.write_suite(self.root / 'used', self.root / 'keys', 1, 1, ['ledger'])
        with self.assertRaises(generate.GenerationError):
            generate.build_case('poems', 1, 1)


if __name__ == '__main__':
    unittest.main()
