"""The approvals family at `--scale N`: a dossier of N departments, each a case of today's size.

A department is a block. Its seven people, their roles and its six or seven requests are drawn as
`generate.approvals` draws them (the body of that function is copied here, and changed only where
the notes below say). The approval limit and the policy text exist once per dossier. People are
dealt from a pool of given names with surnames, so a person belongs to one department; the
requests of all departments are interleaved into one register and numbered after the merge. The one
aggregate claim of a block names its department, and no other claim needs to. An illegible
signature ranges over the seven people of its own department, so each claim is judged over the
open facts of its own block only.
"""
import random

from generate import (GIVEN_NAMES, PLACE_STEMS, ROLES, SURNAMES, GenerationError, bound_slot, claim, document,
                      interleave, section, span, table)

MAX_SCALE = 30
PEOPLE_PER_BLOCK = 7


def draw_requests(rng, people, limit):
    """The roles and requests of one department, drawn as `generate.approvals` draws them. The requests
    have no identifier yet: it is given after the requests of all departments are merged."""
    roles = dict(zip(people, rng.sample(ROLES, PEOPLE_PER_BLOCK)))
    by_role = lambda *wanted: [p for p in people if roles[p] in wanted]  # noqa: E731
    finance, analysts = by_role('Finance officer'), by_role('Analyst')
    counting = by_role('Finance officer', 'Manager', 'Director')
    small = lambda: rng.randrange(2, limit // 100) * 100 - 50  # noqa: E731
    large = lambda: limit + rng.randrange(1, 60) * 100  # noqa: E731

    def build(kind):
        requester = rng.choice(people)
        others = [p for p in counting if p != requester]
        clerks = [p for p in others if roles[p] != 'Finance officer']
        if kind == 'small_ok':
            return requester, small(), [rng.choice(others), None]
        if kind == 'boundary_ok':
            return requester, limit, [rng.choice(others), None]
        if kind == 'small_analyst':
            return requester, small(), [rng.choice([p for p in analysts if p != requester]), None]
        if kind == 'large_ok':
            signer = rng.choice([p for p in finance if p != requester])
            return requester, large(), rng.sample([signer, rng.choice([p for p in others if p != signer])], 2)
        if kind == 'large_no_finance':
            return requester, large(), rng.sample(clerks, 2)
        if kind == 'large_self':
            requester = rng.choice(clerks)
            return requester, large(), [rng.choice([p for p in finance if p != requester]), requester]
        return requester, large(), [rng.choice(clerks), 'illegible']
    chosen = ['large_self', 'large_no_finance', 'large_ok', 'boundary_ok']
    chosen += ['large_illegible'] if rng.random() < 0.6 else []       # without it, every count below is settled
    chosen += rng.sample(['small_analyst', 'small_ok'], rng.randint(1, 2))
    rng.shuffle(chosen)
    requests = []
    for kind in chosen:
        requester, amount, signers = build(kind)
        requests.append({'kind': kind, 'requester': requester, 'amount': amount, 'signers': signers})
    return roles, requests


def claims_of_block(rng, department, people, roles, requests, limit):
    """The claim slots of one department, once its requests are numbered; the body of `generate.approvals`
    after the sources. `rng` continues the stream that drew the department's requests."""
    def find(w, ident):
        return next(r for r in w['requests'] if r['id'] == ident)

    def meets(w, r):
        signers = [w['signer:' + r['id']] if s == 'illegible' else s for s in r['signers'] if s]
        valid = {s for s in signers if s != r['requester'] and w['roles'][s] != 'Analyst'}
        if r['amount'] <= w['limit']:
            return len(valid) >= 1
        return len(valid) >= 2 and any(w['roles'][s] == 'Finance officer' for s in valid)

    def failing(w):
        return sum(not meets(w, r) for r in w['requests'])

    base = {'requests': requests, 'roles': roles, 'limit': limit}
    unclear = next((r for r in requests if r['kind'] == 'large_illegible'), None)
    unknowns = {'signer:' + unclear['id']: list(people)} if unclear else {}

    Q1, Q2, Q3, Q4 = (('policy.md', a) for a in ('Q-01', 'Q-02', 'Q-03', 'Q-04'))
    S1, S2, B1, B2 = ('staff.md', 'S-01'), ('staff.md', 'S-02'), ('requests.md', 'B-01'), ('requests.md', 'B-02')
    slots = []

    def policy_claim(r, met, error=None):
        text = f'Request {r["id"]} met the approval policy.' if met else f'Request {r["id"]} did not meet the approval policy.'
        return claim('policy_compliance', text, lambda w: meets(w, find(w, r['id'])) == met,
                     [Q1, Q2, Q3, Q4, S1, S2, B1, B2], error=error)
    for kind in ('large_self', 'large_no_finance', 'large_ok', 'boundary_ok'):
        r = next(r for r in requests if r['kind'] == kind)
        met = meets(base, r)
        errors = {'large_self': 'counts_requester_signature', 'large_no_finance': 'ignores_finance_requirement'}
        slots.append((policy_claim(r, met), [policy_claim(r, not met, errors.get(kind, 'flips_outcome'))]))

    def tier_claim(r, two, error=None):
        need = 'two counting approvals' if two else 'one counting approval'
        return claim('approval_tier', f'Request {r["id"]} needed {need}.',
                     lambda w: (find(w, r['id'])['amount'] > w['limit']) == two, [Q1, Q2, B1], error=error)
    edge = next(r for r in requests if r['kind'] == 'boundary_ok')
    tiered = edge if rng.random() < 0.4 else rng.choice(requests)
    slots.append((tier_claim(tiered, tiered['amount'] > limit), [tier_claim(tiered, tiered['amount'] <= limit, 'applies_wrong_tier')]))

    def failing_claim(value, exact, error=None):
        lead = 'Exactly' if exact else 'At least'
        return claim('policy_failures',
                     f'{lead} {value} of the {len(requests)} requests of the {department} did not meet the approval policy.',
                     lambda w: failing(w) == value if exact else failing(w) >= value,
                     [Q1, Q2, Q3, Q4, S1, S2, B1, B2], failing, value, error=error)
    slots.append(bound_slot(failing_claim, *span(failing, base, unknowns), rng))

    def role_claim(person, r, role, error=None):
        article = 'an' if role[0] in 'AEIOU' else 'a'
        return claim('signer_role', f'{person}, who signed request {r["id"]}, is {article} {role}.',
                     lambda w: w['roles'][person] == role, [S1, B1], formal=False, error=error)
    signed = rng.choice([r for r in requests if r['kind'] in ('large_ok', 'large_no_finance')])
    signer = signed['signers'][0]
    slots.append((role_claim(signer, signed, roles[signer]),
                  [role_claim(signer, signed, rng.choice([name for name in sorted(set(ROLES)) if name != roles[signer]]),
                              'wrong_role')]))

    if unclear:
        def illegible_claim(r, error=None):
            return claim('illegible_signature', f'The second signature on request {r["id"]} is illegible in the supplied scan.',
                         lambda w: find(w, r['id'])['signers'][1] == 'illegible', [B1, B2], formal=False, error=error)
        slots.append((illegible_claim(unclear),
                      [illegible_claim(rng.choice([r for r in requests if r is not unclear]), 'overlooks_recorded_value'),
                       policy_claim(unclear, True, 'asserts_unknown_as_fact'),
                       policy_claim(unclear, False, 'treats_illegible_as_missing')]))

    def request_claim(r, requester, amount, error=None):
        return claim('request_lookup', f'Request {r["id"]} was submitted by {requester} for {amount} euro.',
                     lambda w: (find(w, r['id'])['requester'], find(w, r['id'])['amount']) == (requester, amount),
                     [B1], formal=False, error=error)
    one = rng.choice(requests)
    slots.append((request_claim(one, one['requester'], one['amount']),
                  [request_claim(one, one['requester'], one['amount'] + rng.choice((-100, 100, 1000)), 'wrong_amount'),
                   request_claim(one, rng.choice([p for p in people if p != one['requester']]), one['amount'], 'wrong_attribution')]))
    return {'base': base, 'unknowns': unknowns, 'slots': slots, 'rng': rng}


def build_scaled(rng, scale):
    """The case of `scale` departments, as the contract of the scaled families describes it."""
    if not 1 <= scale <= MAX_SCALE:
        raise GenerationError(f'Family approvals does not support --scale {scale} (the limit is {MAX_SCALE})')
    # Dossier level, in this order: the departments, the limit, the people.
    departments = [f'{place} office' for place in rng.sample(PLACE_STEMS, scale)]
    limit = rng.choice((2000, 5000, 10000))
    pairs = [f'{given} {surname}' for given in GIVEN_NAMES for surname in SURNAMES]
    dealt = rng.sample(pairs, PEOPLE_PER_BLOCK * scale)
    teams = [dealt[PEOPLE_PER_BLOCK * i:PEOPLE_PER_BLOCK * (i + 1)] for i in range(scale)]
    streams = [random.Random(rng.getrandbits(64)) for _ in range(scale)]

    drawn = [draw_requests(stream, team, limit) for stream, team in zip(streams, teams)]
    for department, (_, requests) in zip(departments, drawn):
        for r in requests:
            r['department'] = department
    # Merge: the departments' requests are interleaved, then numbered in that order.
    merged = interleave(rng, [requests for _, requests in drawn])
    width = max(2, len(str(len(merged))))
    for number, r in enumerate(merged, 1):
        r['id'] = f'Q{number:0{width}d}'
    blocks = [claims_of_block(stream, department, team, roles, requests, limit)
              for stream, department, team, (roles, requests) in zip(streams, departments, teams, drawn)]

    department_of = {p: d for d, team in zip(departments, teams) for p in team}
    role_of = {p: roles[p] for _, (roles, _) in zip(departments, drawn) for p in roles}
    shown = lambda s: 'None' if s is None else 'Illegible in the supplied scan' if s == 'illegible' else s  # noqa: E731
    sources = {
        'policy.md': document('Synthetic approval policy', [
            section('Q-01', 'Lower tier', f'A request of at most {limit} euro needs one counting approval.'),
            section('Q-02', 'Upper tier', f'A request above {limit} euro needs two counting approvals from two different '
                    'people, and at least one of them must be a Finance officer.'),
            section('Q-03', 'Counting approvals', 'A signature is a counting approval when, and only when, the signer is '
                    'a Manager, a Finance officer or a Director and is not the requester of that request. A signature by '
                    'an Analyst never counts.'),
            section('Q-04', 'Meeting the policy', 'A request meets the approval policy when, and only when, it has the '
                    'counting approvals that Q-01 or Q-02 requires for its amount.')]),
        'staff.md': document('Synthetic staff list', [
            section('S-01', 'Roles', table(('Person', 'Department', 'Role'),
                                           [(p, department_of[p], role_of[p]) for p in sorted(department_of)])),
            section('S-02', 'Signatures', "Every signature on a request belongs to a person listed in S-01 for that "
                    "request's department.")]),
        'requests.md': document('Synthetic request register', [
            section('B-01', 'Requests and signatures', table(
                ('Request', 'Department', 'Requester', 'Amount, euro', 'First signature', 'Second signature'),
                [(r['id'], r['department'], r['requester'], r['amount'], shown(r['signers'][0]), shown(r['signers'][1]))
                 for r in merged])),
            section('B-02', 'Illegible signatures', 'Where a signature is illegible, the packet does not establish '
                    'who signed.')])}
    return {'title': 'Synthetic approval-policy review', 'sources': sources, 'blocks': blocks}
