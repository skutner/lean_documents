"""Harder case family: purchase requests reviewed against an approval policy.

A register of 67 to 75 purchase requests is checked against seven interacting rules: conversion to euro with the
rate of the request's month, requests of one requester and supplier in one calendar week assessed together, three
tiers with a Finance officer requirement, an amendment with an effective date, approving roles only, acting
appointments, and the requester's own signature never counting. The packet holds two issues of the policy under neutral
names, and the document index alone says which is in force. Four facts are left unreadable and become unknowns with
two or three alternatives each: a signature that settles its request whoever signed, a signature that matters, an
amount, and the last day of an appointment. Requests that depend on them, and that no claim names, carry the wrong
variants that the key judges open.

`build(rng)` returns the dict the families of generate.py return. The rules live in `assess`, one function with one
flag per rule, so a wrong variant is the result of the same engine with one rule switched off or one input swapped
(`SLIPS`), never an arbitrary offset. A slip that touches an unknown is evaluated in the first world, the reference
world; the key then decides the state of the wrong variant over every world.

Requests are planted so that several rules decide them (a foreign pair in one week signed by an acting Finance
officer, signatures on the first and last day of an appointment, amounts between the old and the new limit, an amount
exactly at a limit, a tier 3 request without a Finance officer, the requester signing, a request that complies only
because of conversion and an acting signer), and background requests fill the register. The effective date is any
weekday, so a week can straddle it. The packet holds two issues of the policy under neutral names; only the document
index says which is in force. Dossiers are drawn "mild" with probability 0.45 (34 of the first 60 had no request whose compliance depends on an open fact): their unreadable entries change counts and
amounts but not whether a request complies, so the register-wide counts are settled there and "exactly" is
sometimes right.

All arithmetic is integer arithmetic. Rates are integers in ten-thousandths of a euro, so a converted amount is
`amount * rate // 10000`; amounts are chosen so that a float computation gives the same figure.
"""
import datetime
import math
import re

import generate
from generate import DAY, GenerationError, claim, document, section, table

SCALE = 10000
APPROVING = ('Manager', 'Director', 'Finance officer')
DEPARTMENTS = ('Operations', 'Research', 'Sales', 'Facilities')
ROLE_POOL = ['Finance officer'] * 2 + ['Director'] * 2 + ['Manager'] * 4 + ['Analyst'] * 6 + ['Assistant'] * 4
NAMES = ('Alma Voss', 'Bram Keller', 'Cora Lindqvist', 'Dario Mancini', 'Elsa Novak', 'Felix Okoro', 'Greta Petrov',
         'Hugo Quint', 'Ines Rossi', 'Jonas Silva', 'Kira Tanaka', 'Leon Ulrich', 'Mila Varga', 'Nils Weber',
         'Olga Xenakis', 'Pablo Yilmaz', 'Rosa Zeman', 'Stefan Aaltonen', 'Tilda Bergman', 'Ugo Castellan',
         'Vera Dimitrov', 'Wim Eklund', 'Yara Fontaine', 'Zane Gallo')
SUPPLIERS = ('Harbor Stationery', 'Quill Office Goods', 'Tern Laboratory Supply', 'Moss Catering', 'Cedar Facilities Care',
             'Lumen Lighting', 'Ridge Printing', 'Orchard Software', 'Delta Courier', 'Summit Furniture',
             'Basalt Tools', 'Pine Cleaning', 'Nimbus Cloud Services', 'Cobalt Security', 'Ember Energy', 'Saffron Travel')
BACKGROUND, PLANTED = SUPPLIERS[:10], SUPPLIERS[10:]
BASE_RATES = {'USD': 9000, 'GBP': 11600, 'CHF': 10400}
CURRENCY_CHOICES = ('EUR', 'EUR', 'USD', 'GBP', 'CHF')
LIMITS = (2000, 2500, 3000, 3500, 4000)       # A of the policy in force, of the other issue and of the amendment; B is 4 A

# Each wrong variant is the result of one slip: the error name and the engine flag that produces it. When several
# slips give the same sentence, the key names the first of them in this list: the rarer slips come first, so that
# a common slip does not take their place.
SLIPS = (('counts_requester_signature', 'requester'), ('ignores_finance_requirement', 'finance'),
         ('ignores_delegation', 'delegation'), ('applies_delegation_outside_dates', 'dates'),
         ('ignores_amendment', 'amendment'), ('applies_amendment_too_early', 'early'),
         ('treats_limit_as_exclusive', 'exclusive'), ('uses_superseded_policy', 'previous'),
         ('uses_wrong_month_rate', 'month'), ('ignores_same_week_rule', 'week'), ('ignores_conversion', 'conversion'),
         ('counts_nonapproving_role', 'approving'))
NONE = frozenset()
# The flags that leave out one of the seven rules. The others (`month`, `early`, `previous`, `exclusive`) swap an input
# and are only used for wrong variants; a request is "decided by" a rule when leaving that rule out changes its figure.
RULE_FLAGS = ('conversion', 'week', 'finance', 'amendment', 'approving', 'delegation', 'requester')

AM, DI = 'amendment.md', 'document-index.md'
POLICIES = ('policy-a.md', 'policy-b.md')
DATA = [('staff.md', 'S-01'), ('delegations.md', 'D-01'), ('delegations.md', 'D-02'), ('rates.md', 'RT-01'),
        ('rates.md', 'RT-02'), ('requests.md', 'RQ-01'), ('requests.md', 'RQ-02')]


def policy_cites(numbers):
    """The same sections of both issues of the policy, always in the same order: the claim must not show which of the two
    settles the question. The key names only the passages of the one in force (see `claim` in `attempt_case`)."""
    return [(file, f'P-{n:02d}') for file in POLICIES for n in numbers]


INDEX = [(DI, 'DI-02'), (DI, 'DI-03')]
EVERYTHING = INDEX + policy_cites(range(1, 9)) + [(AM, 'AM-01')] + DATA
# The same citations for every claim of a kind, whatever the request and whether the claim is right.
CITES = {
    'request_compliance': EVERYTHING, 'failing_requests': EVERYTHING, 'failing_total': EVERYTHING,
    'tier_three_requests': EVERYTHING, 'department_failures': EVERYTHING, 'department_top': EVERYTHING,
    'failure_comparison': EVERYTHING,
    'required_tier': INDEX + policy_cites(range(1, 5)) + [(AM, 'AM-01')] + DATA[3:],
    'assessed_amount': INDEX + policy_cites(range(1, 4)) + DATA[3:],
    'counting_approvals': INDEX + policy_cites((1, 5, 6, 7)) + DATA[:3] + DATA[5:],
    'request_amount': [('requests.md', 'RQ-01')], 'request_signers': [('requests.md', 'RQ-01')]}


class Retry(Exception):
    """A random draw did not meet the requirements of the case; draw again."""


# ---------- The rules ----------

class Data(object):
    """Everything known about a dossier; unknown facts arrive in a world as 'amount:ID', 'signer:ID', 'last:ID'."""

    def __init__(self, **fields):
        self.__dict__.update(fields)
        self.cache = {}
        self.by_id = {r['id']: r for r in self.requests}
        self.department = {r['id']: self.staff[r['requester']]['dept'] for r in self.requests}


class Run(object):
    def __init__(self, rows):
        self.rows = rows
        self.by_id = {row['id']: row for row in rows}


def monday(day):
    return day - DAY * day.weekday()


def roles_on(data, world, person, day, flags):
    roles = {data.staff[person]['role']}
    if 'delegation' not in flags:
        for d in data.delegations:
            if d['person'] == person:
                last = d['last'] if d['last'] is not None else world['last:' + d['id']]
                if 'dates' in flags or d['first'] <= day <= last:
                    roles.add(d['role'])
    return roles


def limits_on(data, day, flags):
    if 'amendment' in flags:
        return data.limits
    if 'early' in flags:
        return data.amended if day >= data.issued else data.limits
    if day >= data.effective:
        return data.amended
    return data.previous if 'previous' in flags else data.limits


def assess(data, world, flags=NONE):
    """Apply the rules to every request in a world; `flags` switches one rule off or swaps one input."""
    key = (flags, tuple(world[name] for name in data.unknown_keys))
    hit = data.cache.get(key)
    if hit is not None:
        return hit
    converted, totals = {}, {}
    for r in data.requests:
        amount = r['amount'] if r['amount'] is not None else world['amount:' + r['id']]
        if r['currency'] != 'EUR' and 'conversion' not in flags:
            day = r['date']
            year, month = day.year, day.month
            if 'month' in flags:
                year, month = (year, month - 1) if month > 1 else (year - 1, 12)
            amount = amount * data.rates[(r['currency'], year, month)] // SCALE
        converted[r['id']] = amount
        group = (r['requester'], r['supplier'], monday(r['date']))
        totals[group] = totals.get(group, 0) + amount
    rows = []
    for r in data.requests:
        assessed = converted[r['id']] if 'week' in flags else totals[(r['requester'], r['supplier'], monday(r['date']))]
        low, high = limits_on(data, r['date'], flags)
        if 'exclusive' in flags:
            tier = 1 if assessed < low else 2 if assessed < high else 3
        else:
            tier = 1 if assessed <= low else 2 if assessed <= high else 3
        needed = 1 if tier == 1 else 2
        exempt = data.exempt_out if 'previous' in flags else data.exempt_in
        if exempt is not None and assessed <= exempt:
            needed = 0
        counting, finance = [], False
        for person in (world['signer:' + r['id']] if s is None else s for s in r['signers']):
            roles = roles_on(data, world, person, r['date'], flags)
            if person == r['requester'] and 'requester' not in flags:
                continue
            if 'approving' not in flags and not roles.intersection(APPROVING):
                continue
            counting.append(person)
            finance = finance or 'Finance officer' in roles
        wants_finance = tier == 3 and 'finance' not in flags
        rows.append({'id': r['id'], 'converted': converted[r['id']], 'assessed': assessed, 'tier': tier,
                     'counting': len(set(counting)), 'finance': finance,
                     'complies': len(set(counting)) >= needed and (finance or not wants_finance)})
    run = data.cache[key] = Run(rows)
    return run


def row_of(world, ident, flags=NONE):
    return assess(world['data'], world, flags).by_id[ident]


# ---------- Rendering ----------

def rate_text(rate):
    return f'{rate // SCALE}.{rate % SCALE:04d}'


def name_list(names):
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' or ' + names[-1]


def and_list(names):
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def policy_document(data, file, limits, exempt):
    """One issue of the approval policy. The two issues have the same headings and section ids; they differ in the limits
    of P-04 and in one sentence there (the exemption), and say nothing about which of them is in force."""
    low, high = limits
    exemption = ('.' if exempt is None else
                 f', except that a request whose assessed amount is at most {exempt} euro needs no counting approval.')
    return document(f'Purchase approval policy, issue of {data.policy_issue[file].isoformat()}', [
        section('P-01', 'Definitions',
                'A date is written as year, month and day, for example 2034-03-07. The request date of a request '
                'is the date in the Date column of requests.md. A calendar month is a month of the Gregorian '
                'calendar. A calendar week runs from Monday to Sunday, both days included; the calendar week of a '
                'request is the one that contains its request date.\n\n'
                'The requester of a request is the person named in the Requester column of requests.md. The '
                'department of a request is the department that staff.md lists for its requester. A request is '
                'paid in the currency shown in its Currency column. A period written from one date to another '
                'includes both of those dates.\n\n'
                'A person holds a role on a date when the role is the person\'s role in staff.md or when P-06 '
                'gives the role to the person on that date. The approving roles are Manager, Director and Finance '
                'officer. Analyst and Assistant are not approving roles.'),
        section('P-02', 'Converted amount',
                'The converted amount of a request is its amount expressed in whole euro. If the currency of the '
                'request is EUR, the converted amount is the amount. Otherwise the amount is multiplied by the '
                'rate that rates.md gives for that currency and for the calendar month that contains the request '
                'date of that request, and the exact product is rounded down to the nearest whole euro. Every '
                'request is converted on its own, with the rate of the month of its own request date.'),
        section('P-03', 'Requests assessed together',
                'Two requests are assessed together when they have the same requester, the same supplier and the '
                'same calendar week. Supplier names are compared as they are written in requests.md. The assessed '
                'amount of a request is the sum of the converted amounts, as P-02 defines them, of all requests '
                'that are assessed together with it, the request itself included. A request that is assessed '
                'together with no other request has its own converted amount as its assessed amount. Converted '
                'amounts are rounded before they are added, and no other rounding takes place.'),
        section('P-04', 'Tiers',
                f'Two limits, A and B, apply to requests. A is {low} euro and B is {high} euro, unless an amendment to '
                'this policy sets other values for requests dated from a stated day. The limits that apply to a '
                'request are the limits that apply on its own request date, whatever the request dates of the '
                'requests that are assessed together with it.\n\n'
                'A request is in tier 1 when its assessed amount is at most A, in tier 2 when its assessed amount '
                'is more than A and at most B, and in tier 3 when its assessed amount is more than B. A request '
                'in tier 1 needs at least 1 counting approval' + exemption + ' A request in tier 2 needs at least 2 '
                'counting approvals. A request in tier 3 needs at least 2 counting approvals, and at least one of '
                'them must be the approval of a person who holds the role Finance officer on the request date.'),
        section('P-05', 'Counting approvals',
                'A request is approved by the people who signed it, as the Signed by column of requests.md lists '
                'them. No person signs a request twice. An illegible signature is the signature of the person who '
                'made it, and it counts or not according to who that person is. A signature counts when the signer '
                'holds at least one approving role on the request date, except as P-07 provides. A signature by a '
                'person who holds no approving role on the request date does not count. The number of counting '
                'approvals of a request is the number of different people whose signature on it counts.'),
        section('P-06', 'Acting appointments',
                'delegations.md lists the acting appointments. A person who is listed there holds the role acted '
                'in on every date from the first day to the last day of the appointment, both days included, in '
                'addition to the role the person has in staff.md. Outside those dates the appointment gives no '
                'role. A person who is not listed holds only the role in staff.md.'),
        section('P-07', 'The requester\'s signature',
                'The signature of the requester of a request on that request never counts, whatever role the '
                'requester holds.'),
        section('P-08', 'Compliance and counts',
                'A request complies with this policy when, and only when, its number of counting approvals is at '
                'least the number of counting approvals that P-04 says the request needs and, in tier 3, at least one '
                'of its counting approvals is '
                'the approval of a person who holds the role Finance officer on the request date. A count or a '
                'total over several requests takes each request once, with the converted amount of P-02.')])


def render(data):
    a2, b2 = data.amended
    staff_rows = [(n, data.staff[n]['role'], data.staff[n]['dept']) for n in sorted(data.staff)]
    delegation_rows = [(d['person'], d['role'], d['first'].isoformat(),
                        d['last'].isoformat() if d['last'] is not None else 'unreadable') for d in data.delegations]
    rate_rows = [(cur, f'{year}-{month:02d}', rate_text(rate))
                 for (cur, year, month), rate in sorted(data.rates.items())]
    request_rows = []
    for r in data.requests:
        signed = '; '.join('illegible' if s is None else s for s in r['signers']) or 'none'
        request_rows.append((r['id'], r['date'].isoformat(), r['requester'], r['supplier'],
                             'unreadable' if r['amount'] is None else r['amount'], r['currency'], signed))
    notes = []
    for r in data.requests:
        if r['signer_choices']:
            notes.append(f'On request {r["id"]} one signature is illegible. The signer is one of {name_list(r["signer_choices"])}.')
        if r['amount_choices']:
            notes.append(f'On request {r["id"]} the amount is unreadable. It is one of '
                         f'{name_list([str(v) for v in r["amount_choices"]])} {r["currency"]}.')
    delegation_notes = [
        f'The last day of the appointment of {d["person"]} as {d["role"]}, which starts on {d["first"].isoformat()}, '
        f'is unreadable. It is either {d["choices"][0].isoformat()} or {d["choices"][1].isoformat()}.'
        for d in data.delegations if d['last'] is None]
    open_text = ('An entry that is marked unreadable or illegible is one of the alternatives listed in the note that names '
                 'it. Which of them it is, is not recorded anywhere in the packet.')
    sources = {
        'document-index.md': document('Document index: purchase request review', [
            section('DI-01', 'Purpose', 'This index controls the documents supplied for the review of purchase requests '
                    'against the approval policy. The review covers every request listed in requests.md and no other. '
                    'A fact that no supplied document establishes is not to be assumed.'),
            section('DI-02', 'Documents and their status', table(
                ('Document', 'Status', 'Content'),
                [(data.policy_file, 'In force', f'Approval policy, issue of {data.policy_issue[data.policy_file].isoformat()}'),
                 (data.other_file, 'Not in force', f'Approval policy, issue of {data.policy_issue[data.other_file].isoformat()}'),
                 ('amendment.md', 'In force from its effective date', 'Amendment to the approval policy that is in force'),
                 ('staff.md', 'In force', 'Staff list with roles and departments'),
                 ('delegations.md', 'In force', 'Acting appointments'),
                 ('rates.md', 'In force', 'Conversion rates to euro'),
                 ('requests.md', 'In force', 'Register of purchase requests and their signatures')])),
            section('DI-03', 'Precedence and documents that are not in force',
                    f'{data.policy_file} is the approval policy for this review. ' + (
                        f'{data.other_file} is an earlier issue; {data.policy_file} replaced it.' if data.in_force_later else
                        f'{data.other_file} is a later issue, proposed as a revision of {data.policy_file}; it was not adopted.') +
                    ' In this review "the policy" means the policy that this index lists as in force. The amendment '
                    'prevails over the policy from the effective date stated in the amendment. Before that date the '
                    'policy applies as written. A document that is not in force settles no question about any request in '
                    'this review. Where it differs from a document in force, the document in force decides; where the '
                    'documents in force say nothing, it does not fill the gap.')]),
        data.policy_file: policy_document(data, data.policy_file, data.limits, data.exempt_in),
        data.other_file: policy_document(data, data.other_file, data.previous, data.exempt_out),
        'amendment.md': document('Amendment 1 to the approval policy', [
            section('AM-01', 'New limits',
                    f'This amendment to the policy that the document index lists as in force was issued on '
                    f'{data.issued.isoformat()} and takes effect on {data.effective.isoformat()}. For every request '
                    f'whose request date is on or after {data.effective.isoformat()}, the limit A is {a2} euro and the '
                    f'limit B is {b2} euro. For every request dated before {data.effective.isoformat()}, the limits '
                    f'stated in P-04 of that policy apply, including a request dated between {data.issued.isoformat()} '
                    'and the day before the effective date.'),
            section('AM-02', 'Other provisions', 'No other provision of the approval policy is changed.')]),
        'staff.md': document('Staff list', [
            section('S-01', 'People, roles and departments', table(('Person', 'Role', 'Department'), staff_rows)),
            section('S-02', 'Validity', 'Each person has exactly one role in this list, and the role and the department '
                    'are valid on every date of the review period. Every person named in any other supplied document '
                    'is listed here.')]),
        'delegations.md': document('Acting appointments', [
            section('D-01', 'Appointments', table(('Person', 'Role acted in', 'First day', 'Last day'), delegation_rows)),
            section('D-02', 'Unreadable entries', open_text + (' ' + ' '.join(delegation_notes) if delegation_notes else
                                                              ' No entry of this list is unreadable.'))]),
        'rates.md': document('Conversion rates to euro', [
            section('RT-01', 'Rates', table(('Currency', 'Month', 'Euro per 1 unit of the currency'), rate_rows)),
            section('RT-02', 'Use of the rates', 'Each row gives the number of euro that one unit of the currency is '
                    'worth on every day of the calendar month shown. There is one rate for each currency and each '
                    'calendar month. Amounts in EUR are already in euro and have no row.')]),
        'requests.md': document('Purchase request register', [
            section('RQ-01', 'Requests', table(('Request', 'Date', 'Requester', 'Supplier', 'Amount', 'Currency',
                                                'Signed by'), request_rows)),
            section('RQ-02', 'Reading the register',
                    'Amounts are whole units of the currency shown, written without separators. The Signed by column '
                    'lists every person who signed the request, separated by semicolons; none means that nobody '
                    'signed. ' + open_text + (' ' + ' '.join(notes) if notes else ''))])}
    return dict(sorted(sources.items()))


# ---------- Building a dossier ----------

def month_start(first, n):
    index = first.year * 12 + first.month - 1 + n
    return datetime.date(index // 12, index % 12 + 1, 1)


def float_safe(amount, rate):
    exact = amount * rate // SCALE
    return math.floor(amount * float(rate_text(rate))) == exact and math.floor(amount * rate / SCALE) == exact


def attempt(rng):
    first = datetime.date(2034, rng.randint(2, 8), 1)
    end = month_start(first, 4) - DAY
    span_days = (end - first).days + 1
    effective = month_start(first, 2) + DAY * rng.randint(0, 12)
    issued = effective - DAY * rng.randint(11, 24)
    a, a2, a0 = rng.sample(LIMITS, 3)           # the same kind of relation for the policy in force, the other issue and the amendment
    b, b2, b0 = 4 * a, 4 * a2, 4 * a0
    exempt = rng.choice((200, 250, 300))        # the size of a small request, whatever the policies say about it
    exempt_in, exempt_out = rng.choice((None, 200, 250, 300)), rng.choice((None, 200, 250, 300))
    files = ['policy-a.md', 'policy-b.md']
    rng.shuffle(files)
    policy_file, other_file = files
    in_force_later = rng.random() < 0.5
    early = first - DAY * rng.randint(120, 500)
    late = early + DAY * rng.randint(30, 100)
    policy_issue = {policy_file: late if in_force_later else early, other_file: early if in_force_later else late}
    edges = (a, b, a2, b2, a0, b0)

    names = rng.sample(NAMES, 18)
    roles = ROLE_POOL[:]
    rng.shuffle(roles)
    departments = list(DEPARTMENTS * 5)[:18]
    rng.shuffle(departments)
    staff = {n: {'role': r, 'dept': d} for n, r, d in zip(names, roles, departments)}
    by_role = lambda *wanted: [n for n in names if staff[n]['role'] in wanted]  # noqa: E731
    nonapp, managers = by_role('Analyst', 'Assistant'), by_role('Manager')

    rates = {}
    for cur, base in BASE_RATES.items():
        rate = base + rng.randint(-300, 300)
        for n in range(-1, 4):
            month = month_start(first, n)
            rates[(cur, month.year, month.month)] = rate
            rate += rng.choice((-1, 1)) * rng.randint(60, 260)

    # Acting appointments: four by Analysts or Assistants, two by Managers. The first two act as Finance officer.
    mild = rng.random() < 0.45        # an unreadable entry that leaves every request's compliance as it is
    open_plan = ['signature', 'amount', 'delegation']
    people = rng.sample(nonapp, 4) + rng.sample(managers, 2)
    delegations = []
    for i, person in enumerate(people):
        role = 'Finance officer' if i < 2 else rng.choice(APPROVING if i < 4 else ('Director', 'Finance officer'))
        begin = first + DAY * rng.randint(3, span_days - 46)
        last = begin + DAY * rng.randint(12, 25 if i == 0 else 35)
        delegations.append({'id': f'D{i + 1}', 'person': person, 'role': role, 'first': begin, 'last': last, 'choices': None})
    if 'delegation' in open_plan:
        d = delegations[0]
        d['choices'] = [d['last'], d['last'] + DAY * rng.randint(4, 12)]
        d['last'] = None
    latest = lambda d: d['last'] if d['last'] is not None else d['choices'][-1]  # noqa: E731
    delegates = [d['person'] for d in delegations]
    reserved = [d['person'] for d in delegations if d['last'] is None]   # signs only the requests planted for it
    plain_nonapp = [n for n in nonapp if n not in delegates]
    plain_fin = by_role('Finance officer')
    plain_nonfin = [n for n in names if staff[n]['role'] in ('Manager', 'Director') and n not in delegates]
    approvers = by_role(*APPROVING)

    def limits_at(day):
        return (a, b) if day < effective else (a2, b2)

    def rand_day():
        return first + DAY * rng.randrange(span_days)

    def rate_of(cur, day):
        return rates[(cur, day.year, day.month)]

    def money(target, cur, day):
        if cur == 'EUR':
            return max(1, int(target))
        rate = rate_of(cur, day)
        amount = max(1, (int(target) * SCALE + rate // 2) // rate)
        for shift in (0, 1, -1, 2, -2, 3, -3, 4, -4, 5):
            if amount + shift >= 1 and float_safe(amount + shift, rate):
                return amount + shift
        raise Retry()

    def draw_target():
        roll = rng.random()
        if roll < 0.08:
            return rng.randint(60, exempt)
        if roll < 0.45:
            return int(rng.choice(edges) * rng.uniform(0.9, 1.1))
        if roll < 0.6:
            return rng.randint(exempt, a)
        if roll < 0.85:
            return rng.randint(a, b)
        return rng.randint(b, 2 * b)

    requests, registry = [], {}

    def make(requester, supplier, day, amount, cur, signers, **extra):
        r = {'requester': requester, 'supplier': supplier, 'date': day, 'amount': amount, 'currency': cur,
             'signers': signers, 'rank': 0, 'signer_choices': None, 'amount_choices': None, 'settled': False}
        r.update(extra)
        return r

    def place(r, shared=False):
        key = (r['requester'], r['supplier'], monday(r['date']))
        if key in registry and not shared:
            raise Retry()
        registry[key] = True
        requests.append(r)
        return r

    def planted():
        return rng.choice(PLANTED)

    def foreign():
        return rng.choice(tuple(BASE_RATES))

    def tier_two_target():
        return rng.randint(max(a, a2) + 200, min(b, b2) - 200)

    def other(pool, *excluded):
        fresh = [p for p in pool if p not in excluded]
        if not fresh:
            raise Retry()
        return rng.choice(fresh)

    def background_signers(requester, day):
        count = rng.choices((0, 1, 2, 3), (1, 5, 12, 3))[0]
        out = []
        near = [d['person'] for d in delegations if d['first'] - DAY * 4 <= day <= latest(d) + DAY * 4 and d['person'] not in reserved]
        if near and rng.random() < 0.5:
            out.append(rng.choice(near))
        while len(out) < count:
            roll = rng.random()
            pick = requester if roll < 0.05 else rng.choice([n for n in nonapp if n not in reserved]) if roll < 0.17 else rng.choice(approvers)
            if pick not in out:
                out.append(pick)
        rng.shuffle(out)
        return out

    # --- Planted requests: each is built so that several rules decide its outcome. ---
    # A pair in one week, paid in a foreign currency, signed by an acting Finance officer.
    d = delegations[1]
    for _ in range(40):
        d1 = d['first'] + DAY * rng.randint(0, (d['last'] - d['first']).days - 3)
        d2 = d1 + DAY * rng.randint(1, 2)
        if monday(d1) == monday(d2):
            break
    else:
        raise Retry()
    requester, cur = other(names, d['person']), foreign()
    supplier = planted()
    total = int(limits_at(d1)[1] * rng.uniform(1.06, 1.2))
    for day, share in ((d1, rng.uniform(0.45, 0.55)), (d2, None)):
        part = int(total * share) if share else total - int(total * 0.5)
        place(make(requester, supplier, day, money(part, cur, day), cur, [d['person'], other(plain_nonfin, requester)]),
              shared=day != d1)

    # Boundaries of acting appointments: the last day, the day after it and the day before the first day.
    for k, (d, shift) in enumerate(((delegations[2], 0), (delegations[2], 1), (delegations[3], -1), (delegations[3], 0))):
        day = (d['last'] if shift >= 0 else d['first']) + DAY * shift
        if not first <= day <= end:
            continue
        requester, cur = other(names, d['person']), rng.choice(CURRENCY_CHOICES)
        place(make(requester, planted(), day, money(tier_two_target(), cur, day), cur,
                   [d['person'], other(plain_nonfin, requester)]))

    # The requester signs his or her own request: Manager or Director, and Finance officer.
    for pool in (plain_nonfin, plain_fin):
        requester = rng.choice(pool)
        day = rand_day()
        cur = rng.choice(CURRENCY_CHOICES)
        target = tier_two_target() if pool is plain_nonfin else int(max(b, b2) * rng.uniform(1.1, 1.5))
        pair = [requester, other(plain_nonfin, requester)]
        if pool is plain_fin:       # two counting approvals, neither a Finance officer, besides the requester who is one
            pair.append(other(plain_nonfin, requester, pair[1]))
        place(make(requester, planted(), day, money(target, cur, day), cur, rng.sample(pair, len(pair))))

    # Signed by the requester, by an Analyst and by an acting approver, besides a plain approver: three rules move the count.
    for d in (delegations[2], delegations[3]):
        requester = rng.choice(plain_nonfin)
        day = d['first'] + DAY * rng.randint(0, (d['last'] - d['first']).days)
        cur = rng.choice(CURRENCY_CHOICES)
        place(make(requester, planted(), day, money(tier_two_target(), cur, day), cur,
                   rng.sample([requester, rng.choice(plain_nonapp), d['person'], other(plain_nonfin, requester)], 4)))

    # Complies, and only because of two rules: the amount in dollars is above the limit as a number but below it in euro,
    # and one of the two signers is acting in a role that makes the signature count.
    for d in (delegations[2], delegations[3], delegations[2]):
        requester = other(plain_nonfin, d['person'])
        day = d['first'] + DAY * rng.randint(0, (d['last'] - d['first']).days)
        edge = limits_at(day)[rng.randrange(2)]
        rate = rate_of('USD', day)
        euro = int(edge * rng.uniform(0.95, 0.99))
        if euro * SCALE // rate <= edge:
            raise Retry()
        signers = [d['person']] if edge == limits_at(day)[0] else [d['person'], other(plain_nonfin, requester)]
        place(make(requester, planted(), day, money(euro, 'USD', day), 'USD', signers))

    # Tier 3 without and with a Finance officer.
    for with_finance in (False, True):
        requester, day, cur = rng.choice(names), rand_day(), rng.choice(CURRENCY_CHOICES)
        pair = [other(plain_fin, requester), other(plain_nonfin, requester)] if with_finance else \
            rng.sample([p for p in plain_nonfin if p != requester], 2)
        rng.shuffle(pair)
        place(make(requester, planted(), day, money(int(max(b, b2) * rng.uniform(1.08, 1.4)), cur, day), cur, pair))

    # Between the old and the new limit, around the issue date and the effective date.
    for day in (effective - DAY, effective, issued, issued + DAY * rng.randint(1, 3), effective - DAY * rng.randint(2, 9),
                issued - DAY * rng.randint(1, 8)):
        low, high = sorted((a, a2) if rng.random() < 0.6 else (b, b2))
        requester, cur = rng.choice(names), rng.choice(CURRENCY_CHOICES)
        pick = other(plain_nonfin + plain_fin, requester)
        place(make(requester, planted(), day, money(rng.randint(low + 100, high - 100), cur, day), cur, [pick]))

    # An amount exactly at a limit.
    for day, edge in ((effective - DAY * rng.randint(1, 20), rng.choice((a, b))), (effective + DAY * rng.randint(0, 20), rng.choice((a2, b2)))):
        requester = rng.choice(names)
        count = 1 if edge in (a, a2) else 2
        place(make(requester, planted(), day, edge, 'EUR', rng.sample([p for p in plain_nonfin if p != requester], count)))

    # The same for a limit of the other issue, so that the register does not show which issue is in force.
    edge = rng.choice((a0, b0))
    requester = rng.choice(names)
    place(make(requester, planted(), effective - DAY * rng.randint(1, 20), edge, 'EUR',
               rng.sample([p for p in plain_nonfin if p != requester], 1 if edge == a0 else 2)))

    # Small requests, where the superseded policy asked for no approval at all.
    for signers in ([], [rng.choice(plain_nonapp)], []):
        requester, day, cur = rng.choice(names), rand_day(), rng.choice(CURRENCY_CHOICES)
        place(make(requester, planted(), day, money(rng.randint(60, exempt), cur, day), cur, signers))
    for limit in (exempt_in, exempt_out):        # exactly at the size an exemption covers, unsigned
        if limit:
            place(make(rng.choice(names), planted(), rand_day(), limit, 'EUR', []))
    for limit in (exempt_in, exempt_out):        # two requests of one week, each within the exemption and together above it
        if limit:
            requester, supplier, day = rng.choice(names), planted(), rand_day()
            mates = [k for k in range(-day.weekday(), 7 - day.weekday()) if k != 0 and first <= day + DAY * k <= end]
            if not mates:
                raise Retry()
            place(make(requester, supplier, day, rng.randint(limit * 55 // 100, limit * 9 // 10), 'EUR', []))
            place(make(requester, supplier, day + DAY * rng.choice(mates), rng.randint(limit * 55 // 100, limit * 9 // 10),
                       'EUR', []), shared=True)

    # A foreign amount that is just below or above a limit, so that conversion and the month's rate decide the tier.
    for kind in ('in', 'in', 'in', 'out', 'out'):
        requester, day, cur = rng.choice(names), rand_day(), foreign()
        edge = limits_at(day)[rng.randrange(2)] if kind == 'in' else (a0, b0)[rng.randrange(2)]
        place(make(requester, planted(), day, money(int(edge * rng.uniform(0.96, 1.04)), cur, day), cur,
                   rng.sample([p for p in approvers if p != requester], rng.choice((1, 2)))))

    # Two requests next to each other in the register: same requester, supplier and day, amounts a few units apart.
    requester, supplier, day = rng.choice(names), planted(), rand_day()
    cur = rng.choice(CURRENCY_CHOICES)
    amount = money(draw_target(), cur, day)
    twin = [make(requester, supplier, day, amount, cur, [p for p in rng.sample(approvers, 2) if p != requester][:2] or [rng.choice(approvers)]),
            make(requester, supplier, day, amount + rng.choice((-1, 1)) * rng.randint(2, 9), cur,
                 rng.sample([p for p in approvers if p != requester], rng.choice((1, 2))), rank=1)]
    twin[0]['rank'] = 1
    if twin[0]['amount'] == twin[1]['amount'] or sorted(twin[0]['signers']) == sorted(twin[1]['signers']):
        raise Retry()
    place(twin[0])
    place(twin[1], shared=True)

    # Groups assessed together: a few in the foreign currencies, some across a month boundary or around the effective date.
    kinds = ['month', 'effective', 'effective', 'plain', 'plain', 'plain', 'plain'] + rng.sample(['plain', 'plain', 'month'], 2)
    for kind in kinds:
        for _ in range(60):
            week = monday(rand_day())
            if kind == 'effective':
                week = monday(effective)
            if kind == 'month' and week.month == (week + DAY * 6).month:
                continue
            if week + DAY * 6 <= end and week >= first:
                break
        else:
            continue
        requester, supplier, cur = rng.choice(names), rng.choice(BACKGROUND), rng.choice(CURRENCY_CHOICES[2:] + CURRENCY_CHOICES[:3])
        count = rng.choice((2, 2, 3))
        edge = rng.choice(edges)
        total = int(edge * (rng.uniform(1.03, 1.25) if rng.random() < 0.65 else rng.uniform(0.8, 0.97)))
        if kind == 'month':
            days = [0, 6] + (rng.sample(range(1, 6), 1) if count == 3 else [])
        elif kind == 'effective' and effective.weekday() > 0:
            # some of the group before the effective date and some on or after it, with an assessed amount between
            # the old and the new limit: P-04 says each member keeps the limits of its own date
            lo, hi = sorted(rng.choice(((a, a2), (b, b2))))
            total = rng.randint(lo + 50, hi - 50)
            days = [rng.randrange(0, effective.weekday()), rng.randrange(effective.weekday(), 7)]
            if count == 3:
                days.append(rng.choice([k for k in range(7) if k not in days]))
        else:
            days = rng.sample(range(7), count)
        days.sort()
        weights = [rng.uniform(0.6, 1.4) for _ in days]
        for k, weight in zip(days, weights):
            day = week + DAY * k
            if not first <= day <= end:
                continue
            place(make(requester, supplier, day, money(int(total * weight / sum(weights)), cur, day), cur,
                       background_signers(requester, day)), shared=True)

    # --- The unreadable facts. ---
    def inert(day, requester):
        pool = list(plain_nonapp) + [requester] + [dd['person'] for dd in delegations
                                                  if dd['person'] in nonapp
                                                  and (day < dd['first'] - DAY or day > latest(dd) + DAY)]
        return pool

    day = rand_day()
    requester, cur = rng.choice(plain_nonfin), rng.choice(CURRENCY_CHOICES)
    known = other(plain_nonfin, requester)
    if rng.random() < 0.5:         # fails whoever signed: every alternative is a person whose signature cannot count
        pool = [p for p in dict.fromkeys(inert(day, requester)) if p != known]
        candidates = rng.sample(pool, rng.choice((2, 3)))
        signers = [known, None]
    else:                           # complies whoever signed: two counting signatures besides the illegible one
        second = other(plain_nonfin, requester, known)
        candidates = rng.sample([p for p in names if p not in (known, second)], rng.choice((2, 3)))
        signers = [known, second, None]
    rng.shuffle(signers)
    place(make(requester, planted(), day, money(tier_two_target(), cur, day), cur, signers,
               signer_choices=candidates, settled=True))

    if 'signature' in open_plan:
        day = rand_day()
        requester, cur = rng.choice(plain_nonfin), rng.choice(CURRENCY_CHOICES)
        known = other(plain_nonfin, requester)
        counting = other(plain_nonfin + plain_fin, requester, known)
        both = [known] if not mild else [known, other(plain_nonfin, requester, known, counting)]
        pool = [p for p in dict.fromkeys(inert(day, requester)) if p not in both + [counting]]
        candidates = rng.sample(pool, rng.choice((1, 2))) + [counting]
        rng.shuffle(candidates)
        place(make(requester, planted(), day, money(tier_two_target(), cur, day), cur,
                   rng.sample(both + [None], len(both) + 1), signer_choices=candidates))

    if 'amount' in open_plan:
        day = rand_day()
        requester, cur = rng.choice(plain_nonfin), foreign()
        upper = rng.random() < 0.5
        edge = limits_at(day)[1 if upper else 0]
        targets = sorted({int(edge * f) for f in (rng.uniform(0.88, 0.96), rng.uniform(0.99, 1.0), rng.uniform(1.04, 1.12))})
        if mild:        # three amounts inside tier 2, signed by two approvers: the request complies whichever it is
            lowest = max(a, a2) + 400
            targets = sorted({rng.randint(lowest, min(b, b2) - 1600) for _ in range(3)})
        choices = sorted({money(t, cur, day) for t in targets})
        if len(choices) < 3:
            raise Retry()
        plain_pair = rng.sample([p for p in plain_nonfin if p != requester], 2)
        signers = plain_pair if upper or mild else plain_pair[:1]
        supplier = planted()
        place(make(requester, supplier, day, None, cur, signers, amount_choices=choices))
        # Requests of the same requester and supplier in the same week: their assessed amount moves with the unreadable one
        mates = [k for k in range(-day.weekday(), 7 - day.weekday()) if k != 0 and first <= day + DAY * k <= end]
        for k in rng.sample(mates, min(len(mates), rng.choice((1, 2)))):
            partner = day + DAY * k
            place(make(requester, supplier, partner, money(rng.randint(100, 500) if mild else rng.randint(300, 1200), cur, partner), cur,
                       rng.sample([p for p in plain_nonfin if p != requester], len(signers))), shared=True)

    if 'delegation' in open_plan:
        d = delegations[0]
        low, high = d['choices']
        inside = [low + DAY * k for k in rng.sample(range(1, (high - low).days + 1), 3)]
        for day in [high] + inside + [low - DAY * rng.randint(0, 3), high + DAY]:
            signer = d['person']
            requester = other(names, d['person'])
            cur = rng.choice(CURRENCY_CHOICES)
            if mild:    # tier 2 with two approvers besides the acting one: the request complies, the count does not settle
                one = other(plain_nonfin, requester)
                place(make(requester, planted(), day, money(tier_two_target(), cur, day), cur,
                           rng.sample([signer, one, other(plain_nonfin, requester, one)], 3)))
                continue
            place(make(requester, planted(), day, money(int(max(b, b2) * rng.uniform(1.08, 1.4)), cur, day), cur,
                       rng.sample([signer, other(plain_nonfin, requester)], 2)))

    # --- Background requests up to the size of the register. ---
    wanted = rng.randint(54, 74)
    guard = 0
    while len(requests) < wanted and guard < 4000:
        guard += 1
        requester, supplier, day = rng.choice(names), rng.choice(BACKGROUND), rand_day()
        cur = rng.choice(CURRENCY_CHOICES)
        r = make(requester, supplier, day, money(draw_target(), cur, day), cur, background_signers(requester, day))
        key = (requester, supplier, monday(day))
        if key not in registry:
            place(r)
    if len(requests) < 40:
        raise Retry()

    rng.shuffle(requests)
    requests.sort(key=lambda r: (r['date'], r['rank']))
    for number, r in enumerate(requests, 1):
        r['id'] = f'PR-{number:03d}'
        # One rule for every list of alternatives, in the note and in the claims: names in alphabetical order, amounts in
        # increasing order, so that the order of a list says nothing about it.
        if r['signer_choices']:
            r['signer_choices'] = sorted(r['signer_choices'])
        if r['amount_choices']:
            r['amount_choices'] = sorted(r['amount_choices'])
    unknowns = {}
    for r in requests:
        if r['signer_choices']:
            unknowns['signer:' + r['id']] = list(r['signer_choices'])
        if r['amount_choices']:
            unknowns['amount:' + r['id']] = list(r['amount_choices'])
    for d in delegations:
        if d['last'] is None:
            unknowns['last:' + d['id']] = list(d['choices'])
    data = Data(staff=staff, delegations=delegations, rates=rates, requests=requests, limits=(a, b), amended=(a2, b2),
                previous=(a0, b0), exempt_in=exempt_in, exempt_out=exempt_out, policy_file=policy_file,
                other_file=other_file, policy_issue=policy_issue, in_force_later=in_force_later, effective=effective, issued=issued, unknown_keys=tuple(sorted(unknowns)),
                first=first, end=end)
    return data, unknowns


# ---------- Claims ----------

def choose(rng, pool, taken):
    fresh = [p for p in pool if p not in taken]
    pick = rng.choice(fresh or pool)
    taken.append(pick)
    return pick


def bounded(make, low, high, rng, slips, steps=(1,), floor=1, ceiling=None, cap=None):
    """`bound_slot`, with the wrong variants that are mere offsets replaced by the ones a slip produces."""
    if low != high and low < floor:         # "at least" a value that is always reached says nothing
        raise Retry()
    true, wrong = generate.bound_slot(make, low, high, rng, steps, floor)
    wrong = [w for w in wrong if ceiling is None or w['stated'] <= ceiling]
    extras = [make(value, True, error) for error, value in slips if value is not None and value != low]
    # A reader who made the slip may as well state the figure as a lower bound.
    extras += [make(value, False, error) for error, value in slips
               if value is not None and value > high and (ceiling is None or value <= ceiling)]
    if cap and len(extras) > cap:
        extras = [extras[i] for i in sorted(rng.sample(range(len(extras)), cap))]
    if len(extras) >= 2:
        wrong = [w for w in wrong if w['error'] != 'wrong_value']
    return true, extras + wrong


def attempt_case(rng):
    data, unknowns = attempt(rng)

    def claim(kind, text, pred, cites, quantity=None, stated=None, formal=True, error=None):
        """A claim cites both issues of the policy; the key names only the passages of the one in force."""
        item = generate.claim(kind, text, pred, cites, quantity, stated, formal, error)
        item['anchors'] = [c for c in cites if c[0] != data.other_file]
        return item
    base = {'data': data}
    everyone = list(generate.worlds(base, unknowns))
    ref = everyone[0]
    size = len(data.requests)
    if len(everyone) > 500:
        raise Retry()
    rows = assess(data, ref).rows
    if not 0.3 <= sum(not x['complies'] for x in rows) / size <= 0.6:
        raise Retry()

    sources = render(data)
    words = sum(len(text.split()) for text in sources.values())
    if not 2600 <= words <= 6800:
        raise Retry()

    # Requests that an unknown touches, directly or because another request is assessed with them.
    involved = set()
    for r in data.requests:
        if r['signer_choices'] or r['amount_choices']:
            involved.add(r['id'])
    for world in everyone[1:]:
        for x, y in zip(assess(data, world).rows, rows):
            if x != y:
                involved.add(x['id'])
    involved_groups = {(r['requester'], r['supplier'], monday(r['date'])) for r in data.requests if r['id'] in involved}
    free = [r['id'] for r in data.requests if (r['requester'], r['supplier'], monday(r['date'])) not in involved_groups
            and r['id'] not in involved]

    def slipped(fn, world=ref):
        """What a reader gets with one slip: the value of `fn` on the register assessed with one rule off."""
        out = []
        for error, flag in SLIPS:
            value = fn(assess(data, world, frozenset([flag])))
            out.append((error, value))
        return out

    def differing(fn, ident):
        """The slips that change a figure, one per distinct figure: the first slip in `SLIPS` that gives it."""
        true = fn(assess(data, ref))
        seen, out = set(), []
        for error, value in slipped(fn):
            if value != true and value not in seen:
                seen.add(value)
                out.append((error, value))
        return out

    def rules_deciding(fn, ident):
        true = fn(assess(data, ref))
        return sum(fn(assess(data, ref, frozenset([flag]))) != true for flag in RULE_FLAGS)

    pools = {}
    getters = {'compliance': lambda ident: (lambda run: run.by_id[ident]['complies']),
               'tier': lambda ident: (lambda run: run.by_id[ident]['tier']),
               'assessed': lambda ident: (lambda run: run.by_id[ident]['assessed']),
               'counting': lambda ident: (lambda run: run.by_id[ident]['counting'])}
    for kind, getter in getters.items():
        pools[kind] = [i for i in free if rules_deciding(getter(i), i) >= 2]
        if not pools[kind]:
            raise Retry()

    cite = CITES
    taken = []
    slots = []

    # Per-request claims.
    def compliance_claim(ident, complies, error=None):
        verb = 'complies' if complies else 'does not comply'
        return claim('request_compliance', f'Request {ident} {verb} with the approval policy.',
                     lambda w: row_of(w, ident)['complies'] == complies, cite['request_compliance'], error=error)

    def tier_claim(ident, value, exact, error=None):
        tail = '' if exact else ' or higher'
        return claim('required_tier', f'Request {ident} is in tier {value}{tail}.',
                     lambda w: row_of(w, ident)['tier'] == value if exact else row_of(w, ident)['tier'] >= value,
                     cite['required_tier'], lambda w: row_of(w, ident)['tier'], value, error=error)

    def assessed_claim(ident, value, exact, error=None):
        lead = 'exactly' if exact else 'at least'
        return claim('assessed_amount', f'The assessed amount of request {ident} is {lead} {value} euro.',
                     lambda w: row_of(w, ident)['assessed'] == value if exact else row_of(w, ident)['assessed'] >= value,
                     cite['assessed_amount'], lambda w: row_of(w, ident)['assessed'], value, error=error)

    def counting_claim(ident, value, exact, error=None):
        lead = 'exactly' if exact else 'at least'
        return claim('counting_approvals', f'Request {ident} has {lead} {generate.plural(value, "counting approval")}.',
                     lambda w: row_of(w, ident)['counting'] == value if exact else row_of(w, ident)['counting'] >= value,
                     cite['counting_approvals'], lambda w: row_of(w, ident)['counting'], value, error=error)

    def range_of(fn):
        values = [fn(assess(data, world)) for world in everyone]
        return min(values), max(values)

    def per_request(kind, ident):
        fn = getters[kind](ident)
        low, high = range_of(fn)
        if kind == 'compliance':
            true = fn(assess(data, ref))
            return compliance_claim(ident, true), [compliance_claim(ident, not true, error)
                                                   for error, _ in differing(fn, ident)] + open_variants(kind)
        make = {'tier': lambda v, e, err=None: tier_claim(ident, v, e, err),
                'assessed': lambda v, e, err=None: assessed_claim(ident, v, e, err),
                'counting': lambda v, e, err=None: counting_claim(ident, v, e, err)}[kind]
        steps, floor, ceiling = {'tier': ((1,), 2, 3), 'assessed': ((1, 10, 100), 1, None),
                                 'counting': ((1,), 1, None)}[kind]
        true, wrong = bounded(make, low, high, rng, differing(fn, ident), steps, floor, ceiling)
        return true, wrong + open_variants(kind)

    def open_variants(kind):
        """Wrong variants of the same kind about a request that no other claim names and whose outcome depends on an
        open fact, worded as if that fact were settled. Each has one reading: the figure or the outcome it states holds
        in some worlds and not in others."""
        ident = allocated.get(kind)
        if ident is None:
            return []
        low, high = range_of(getters[kind](ident))
        if kind == 'compliance':
            return [compliance_claim(ident, True, 'asserts_unknown_as_fact'), compliance_claim(ident, False, 'asserts_unknown_as_fact')]
        make = {'tier': tier_claim, 'assessed': assessed_claim, 'counting': counting_claim}[kind]
        return [make(ident, low, True, 'asserts_unknown_as_fact'), make(ident, high, True, 'treats_unknown_as_settled')]

    settled = next(r for r in data.requests if r['settled'])
    open_requests = [r for r in data.requests if r['signer_choices'] and not r['settled']]
    amount_requests = [r for r in data.requests if r['amount_choices']]
    delegation_open = [d for d in data.delegations if d['last'] is None]
    # The request whose unreadable entry gets a statement about the entry itself instead of a bounded figure.
    allocated = {}
    target = rng.choice([(r, 'signature') for r in open_requests] + [(r, 'amount') for r in amount_requests])

    # Aggregates over the whole register.
    def fail_count(run):
        return sum(not x['complies'] for x in run.rows)

    def fail_total(run):
        return sum(x['converted'] for x in run.rows if not x['complies'])

    def tier_three(run):
        return sum(x['tier'] == 3 for x in run.rows)

    def count_claim(value, exact, error=None):
        lead = 'exactly' if exact else 'at least'
        return claim('failing_requests', f'The number of requests that do not comply with the approval policy is {lead} {value}.',
                     lambda w: fail_count(assess(w['data'], w)) == value if exact else fail_count(assess(w['data'], w)) >= value,
                     cite['failing_requests'], lambda w: fail_count(assess(w['data'], w)), value, error=error)

    def total_claim(value, exact, error=None):
        lead = 'exactly' if exact else 'at least'
        return claim('failing_total', 'The converted amounts of the requests that do not comply with the approval policy '
                     f'add up to {lead} {value} euro.',
                     lambda w: fail_total(assess(w['data'], w)) == value if exact else fail_total(assess(w['data'], w)) >= value,
                     cite['failing_total'], lambda w: fail_total(assess(w['data'], w)), value, error=error)

    def tier_three_claim(value, exact, error=None):
        lead = 'exactly' if exact else 'at least'
        return claim('tier_three_requests', f'The number of requests in tier 3 is {lead} {value}.',
                     lambda w: tier_three(assess(w['data'], w)) == value if exact else tier_three(assess(w['data'], w)) >= value,
                     cite['tier_three_requests'], lambda w: tier_three(assess(w['data'], w)), value, error=error)

    for make, fn, steps in ((count_claim, fail_count, (1, 2)), (total_claim, fail_total, (10, 50, 100, 500)),
                            (tier_three_claim, tier_three, (1, 2))):
        low, high = range_of(fn)
        slots.append(bounded(make, low, high, rng, differing(fn, None), steps, 1, cap=2))

    # The same count for one department: the department of an unreadable request when there is one that matters.
    def department_claim(department, value, exact, error=None):
        lead = 'exactly' if exact else 'at least'
        count = lambda w: sum(not x['complies'] for x in assess(w['data'], w).rows if data.department[x['id']] == department)  # noqa: E731
        return claim('department_failures', f'The number of requests of the {department} department that do not comply '
                     f'with the approval policy is {lead} {value}.',
                     lambda w: count(w) == value if exact else count(w) >= value, cite['department_failures'], count, value,
                     error=error)
    open_departments = [data.department[i] for i in sorted(involved) if len(everyone) > 1
                        and len({assess(data, w).by_id[i]['complies'] for w in everyone}) > 1]
    department = rng.choice(open_departments or list(DEPARTMENTS))
    fn = lambda run: sum(not x['complies'] for x in run.rows if data.department[x['id']] == department)  # noqa: E731
    low, high = range_of(fn)
    slots.append(bounded(lambda v, e, err=None: department_claim(department, v, e, err), low, high, rng,
                         differing(fn, None), (1,), 1, cap=2))

    # Filtered superlative and group comparisons; a tie never decides.
    def top_of(run, department):
        failing = [x for x in run.rows if not x['complies'] and data.department[x['id']] == department]
        if len(failing) < 3:
            return None
        best = max(x['assessed'] for x in failing)
        tops = [x for x in failing if x['assessed'] == best]
        return (tops[0]['id'], best) if len(tops) == 1 else None

    def top_claim(department, ident, amount, error=None):
        return claim('department_top', f'Among the requests of the {department} department that do not comply with the '
                     f'approval policy, the largest assessed amount is {amount} euro, on request {ident}.',
                     lambda w: top_of(assess(w['data'], w), department) == (ident, amount), cite['department_top'],
                     error=error)

    top = None
    for department in rng.sample(DEPARTMENTS, len(DEPARTMENTS)):
        values = {top_of(assess(data, world), department) for world in everyone}
        if len(values) == 1 and None not in values:
            found = values.pop()
            wrong = [top_claim(department, v[0], v[1], error) for error, v in
                     slipped(lambda run, d=department: top_of(run, d)) if v is not None and v != found]
            if wrong:
                top = (top_claim(department, found[0], found[1]), wrong)
                break
    if top:
        slots.append(top)

    def count_where(run, test):
        return sum(not x['complies'] and test(x['id']) for x in run.rows)

    def comparison_claim(first_name, second_name, test_first, test_second, relation, error=None):
        word = 'greater' if relation > 0 else 'smaller'
        return claim('failure_comparison', f'The number of requests {first_name} that do not comply with the approval '
                     f'policy is {word} than the number of requests {second_name} that do not comply with the approval '
                     'policy.',
                     lambda w: (count_where(assess(w['data'], w), test_first) - count_where(assess(w['data'], w), test_second) > 0)
                     == (relation > 0) and count_where(assess(w['data'], w), test_first) != count_where(assess(w['data'], w), test_second),
                     cite['failure_comparison'], error=error)

    # Groups of requests of about the same size, so that no direction is the likely one: two windows of four weeks
    # on either side of the effective date, two departments, two currencies.
    before_from, after_to = data.effective - DAY * 28, data.effective + DAY * 27
    options = [(f'dated from {before_from.isoformat()} to {(data.effective - DAY).isoformat()}',
                f'dated from {data.effective.isoformat()} to {after_to.isoformat()}',
                lambda i: before_from <= data.by_id[i]['date'] < data.effective,
                lambda i: data.effective <= data.by_id[i]['date'] <= after_to)]
    for one, two in (rng.sample(DEPARTMENTS, 2), rng.sample(DEPARTMENTS, 2)):
        options.append((f'of the {one} department', f'of the {two} department',
                        lambda i, x=one: data.department[i] == x, lambda i, x=two: data.department[i] == x))
    one, two = rng.sample(tuple(BASE_RATES), 2)
    options.append((f'paid in {one}', f'paid in {two}',
                    lambda i, x=one: data.by_id[i]['currency'] == x, lambda i, x=two: data.by_id[i]['currency'] == x))
    rng.shuffle(options)
    comparisons = 0
    for name1, name2, t1, t2 in options:
        if comparisons >= (1 if top else 2):
            break
        if rng.random() < 0.5:
            name1, name2, t1, t2 = name2, name1, t2, t1
        gaps = {count_where(assess(data, world), t1) - count_where(assess(data, world), t2) for world in everyone}
        if 0 in gaps or len({g > 0 for g in gaps}) != 1:
            continue
        true = 1 if gaps.pop() > 0 else -1
        wrong = []
        for error, flag in SLIPS:
            run = assess(data, ref, frozenset([flag]))
            gap = count_where(run, t1) - count_where(run, t2)
            if gap and (gap > 0) != (true > 0):
                wrong.append(comparison_claim(name1, name2, t1, t2, -true, error))
        wrong.append(comparison_claim(name1, name2, t1, t2, -true, 'reversed_direction'))
        slots.append((comparison_claim(name1, name2, t1, t2, true), wrong))
        comparisons += 1
    if not top and not comparisons:
        raise Retry()

    # Two lookups where a neighbouring row can be mistaken for the one asked about.
    index = {r['id']: n for n, r in enumerate(data.requests)}
    pair = [r for r in data.requests if r['rank'] == 1]
    subject, partner = rng.sample(pair, 2)
    far = index[subject['id']] + (-1 if index[subject['id']] < index[partner['id']] else 1)
    neighbours = [n for n in [partner] + ([data.requests[far]] if 0 <= far < size else []) if n['amount'] is not None]

    def amount_claim(ident, amount, currency, error=None):
        return claim('request_amount', f'Request {ident} is for {amount} {currency}.',
                     lambda w: (data.by_id[ident]['amount'], data.by_id[ident]['currency']) == (amount, currency),
                     cite['request_amount'], formal=False, error=error)

    def signers_claim(ident, signers, error=None):
        return claim('request_signers', f'Request {ident} was signed by {and_list(signers)}.',
                     lambda w: sorted(data.by_id[ident]['signers']) == sorted(signers), cite['request_signers'],
                     formal=False, error=error)
    same_size = [n for n in neighbours if None not in n['signers'] and len(n['signers']) == len(subject['signers'])
                 and sorted(n['signers']) != sorted(subject['signers'])]
    if rng.random() < 0.5 or not same_size:
        slots.append((amount_claim(subject['id'], subject['amount'], subject['currency']),
                      [amount_claim(subject['id'], n['amount'], n['currency'], 'takes_neighbouring_row') for n in neighbours]))
    else:
        slots.append((signers_claim(subject['id'], subject['signers']),
                      [signers_claim(subject['id'], n['signers'], 'takes_neighbouring_row') for n in same_size]))

    # Claims that involve an unknown. The first illegible signature is one that settles the request whoever signed.
    outcome = assess(data, ref).by_id[settled['id']]['complies']
    wrong = [compliance_claim(settled['id'], not outcome, 'assumes_unknown_signature_counts' if not outcome
                              else 'treats_illegible_as_missing')]
    wrong += [compliance_claim(settled['id'], not outcome, error) for error, _ in differing(
        lambda run: run.by_id[settled['id']]['complies'], settled['id'])]
    slots.append((compliance_claim(settled['id'], outcome), wrong))

    def unreadable_claim(ident, what, shown, error=None):
        """The alternatives of an unreadable entry, in the words of the note that lists them."""
        if what == 'signature':
            text = f'The illegible signature on request {ident} is by one of {name_list(shown)}.'
            return claim('unreadable_signature', text, lambda w: w['signer:' + ident] in shown,
                         [('requests.md', 'RQ-01'), ('requests.md', 'RQ-02')], formal=False, error=error)
        shown = sorted(shown)
        text = f'The unreadable amount of request {ident} is one of {name_list([str(v) for v in shown])} {data.by_id[ident]["currency"]}.'
        return claim('unreadable_amount', text, lambda w: w['amount:' + ident] in shown,
                     [('requests.md', 'RQ-01'), ('requests.md', 'RQ-02')], formal=False, error=error)

    def invented_alternatives(r, what):
        """Wrong variants of the same sentence in which every alternative is swapped for an outsider: no world agrees.
        The list keeps its length."""
        true = list(r['signer_choices'] if what == 'signature' else r['amount_choices'])
        if what == 'signature':
            pool = [p for p in data.staff if p not in true and p not in r['signers']]
        else:
            pool = sorted({v + rng.choice((-1, 1)) * rng.randint(20, 400) for v in true for _ in range(2)} - set(true))
        if len(pool) < len(true) + 1:
            raise Retry()
        lists, wrong = [], []
        while len(lists) < 2:
            shown = sorted(rng.sample(pool, len(true)))
            if shown not in lists:
                lists.append(shown)
                wrong.append(unreadable_claim(r['id'], what, shown, 'invents_alternatives'))
        return wrong, true

    for r in open_requests:
        if r is target[0]:
            continue
        fn = getters['counting'](r['id'])
        low, high = range_of(fn)
        if low == high or low < 1:
            raise Retry()
        true, wrong = bounded(lambda v, e, err=None, i=r['id']: counting_claim(i, v, e, err), low, high, rng,
                              differing(fn, r['id']), (1,), 1)
        slots.append((true, wrong))
    for r in amount_requests:
        if r is target[0]:
            continue
        fn = getters['assessed'](r['id'])
        low, high = range_of(fn)
        true, wrong = bounded(lambda v, e, err=None, i=r['id']: assessed_claim(i, v, e, err), low, high, rng,
                              differing(fn, r['id']), (1, 10, 100), 1)
        slots.append((true, wrong))
    for d in delegation_open:
        low_day, high_day = d['choices']
        for ident in [r['id'] for r in data.requests if low_day < r['date'] <= high_day and r['id'] in involved]:
            fn = getters['counting'](ident)
            low, high = range_of(fn)
            if 1 <= low < high:
                break
        else:
            raise Retry()
        true, wrong = bounded(lambda v, e, err=None, i=ident: counting_claim(i, v, e, err), low, high, rng,
                              differing(fn, ident), (1,), 1)
        slots.append((true, wrong))

    r, what = target
    wrong, alternatives = invented_alternatives(r, what)
    slots.append((unreadable_claim(r['id'], what, alternatives), wrong))

    kinds = ['compliance', 'tier', 'assessed', 'counting']
    while len(slots) + len(kinds) > 14 and len(kinds) > 3:
        kinds.remove(rng.choice(kinds[1:]))

    # No request is the subject of more than one claim of a report. Each slot names its own requests, in its true claim
    # and in every wrong variant; the hero slots below take requests that no other slot names, and the wrong variants
    # of a hero slot about an open fact take a request that depends on one and that nothing else names.
    ids_of = lambda slot: {i for item in [slot[0]] + slot[1] for i in re.findall(r'PR-\d+', item['text'])}  # noqa: E731
    named = set()
    for slot in slots:
        named |= ids_of(slot)
    carriers = [i for i in sorted(involved) if i not in named]
    for kind in kinds:
        options = [i for i in carriers if i not in allocated.values() and range_of(getters[kind](i))[0] < range_of(getters[kind](i))[1]]
        if options:
            allocated[kind] = rng.choice(options)
    for kind in kinds:
        pool = [i for i in pools[kind] if i not in named and i not in allocated.values()]
        if not pool:
            raise Retry()
        if kind == 'compliance':        # either outcome is drawn as often as the pool allows
            wanted = rng.random() < 0.5
            pool = [i for i in pool if assess(data, ref).by_id[i]['complies'] == wanted] or pool
        slots.append(per_request(kind, choose(rng, pool, taken)))
    seen_ids = set()
    for slot in slots:
        if ids_of(slot) & seen_ids:
            raise Retry()
        seen_ids |= ids_of(slot)
    # No two wrong variants of a slot share a sentence; the first, which is the rarest slip, names the error.
    unique = []
    for true, wrong in slots:
        texts, kept = {true['text']}, []
        for item in wrong:
            if item['text'] not in texts:
                texts.add(item['text'])
                kept.append(item)
        unique.append((true, kept))
    slots = unique
    if not 10 <= len(slots) <= 14:
        raise Retry()
    return {'title': 'Purchase approval review', 'sources': sources, 'base': base, 'unknowns': unknowns, 'slots': slots,
            'open_claims': 2}


def build(rng):
    for _ in range(80):
        try:
            return attempt_case(rng)
        except Retry:
            continue
    raise GenerationError('Could not draw a procurement dossier that meets its requirements')
