"""Harder case family `casework`: a regulator's register of notices and the replies to them.

Every verdict needs several prose rules applied together to many rows: a reply period by class that an
amendment changes from a stated date, a shift past non-working days, extensions that may be void, refused,
pending or barred, and a postal rule. A second issue of the register is supplied that settles nothing (only the document index says which),
and a few facts are left open: the posting date of two replies and the decision date of one or two
extensions, each bounded by a note to a short range of days.

One rule engine (`evaluate`) decides every notice. A wrong variant is the value the same engine gives when one
rule is switched off (`RULE_OF`) or one input is swapped (the row of the register that settles nothing, the neighbouring
row), so each error
names the single slip that produces it. Where two slips reach the same value, the key names the narrower one.

The module is loaded by `generate.family_builder` and reuses the helpers of `generate`.
"""
import collections
import datetime
import itertools

import generate
from generate import DAY, GenerationError, bound_slot, claim, document, plural, section, span, table

CLASSES = ('standard', 'complex', 'urgent')
CHANNELS = ('Electronic', 'Hand', 'Post')
NEW_PERIODS = {'standard': (21, 25, 40, 45), 'complex': (30, 35, 60, 75), 'urgent': (5, 7, 14, 21)}
WRONG_CLASS = {'standard': 'complex', 'complex': 'standard', 'urgent': 'standard'}
NONE = '—'

ADDRESSEES = ('Alder Fabrication', 'Birch Marine', 'Cedar Logistics', 'Dunmore Energy', 'Elmwood Foods',
              'Fenwick Rail', 'Garrick Textiles', 'Halden Pharma', 'Ironbridge Steel', 'Juniper Telecom',
              'Kestrel Aviation', 'Linden Water', 'Marlow Chemicals', 'Northfield Mining', 'Oakham Printing',
              'Pennant Shipping')
HOLIDAY_NAMES = ('Founders Day', 'Harbour Day', 'Lantern Day', 'Harvest Day', 'River Day', 'Charter Day',
                 'Unity Day', 'Orchard Day', 'Beacon Day', 'Meadow Day', 'Summit Day', 'Mariners Day',
                 'Weavers Day', 'Quarry Day', 'Kite Day', 'Granary Day', 'Forge Day', 'Bell Day')

# Each slip switches off one rule (the number is the procedure rule it omits) or swaps one input.
RULE_OF = {
    'wrong_class_period': 1,
    'ignores_amendment': 2, 'applies_amendment_too_early': 2,
    'counts_issue_day': 3,
    'ignores_nonworking_shift': 4, 'ignores_holiday': 4, 'ignores_shift_after_extension': 4,
    'counts_refused_extension': 5, 'counts_pending_extension': 5, 'counts_void_extension': 5,
    'extends_urgent_notice': 5, 'ignores_extension': 5,
    'ignores_postal_rule': 6, 'counts_calendar_days_for_post': 6,
    'late_on_deadline_day': 7, 'counts_deadline_day_in_days_late': 7}
# What "rule N is decisive for a notice" means: switching the rule off, in one of these ways, changes the answer.
PRIMARY = {1: ('wrong_class_period',), 2: ('ignores_amendment',), 3: ('counts_issue_day',), 4: ('ignores_nonworking_shift',),
           5: ('ignores_extension', 'counts_refused_extension', 'counts_pending_extension', 'counts_void_extension',
               'extends_urgent_notice'),
           6: ('ignores_postal_rule',), 7: ('late_on_deadline_day',)}
# Narrow slips first: where two slips give one value, the earlier name labels it.
PRIORITY = ('extends_urgent_notice', 'counts_pending_extension', 'counts_refused_extension',
            'counts_void_extension', 'applies_amendment_too_early', 'ignores_holiday',
            'ignores_shift_after_extension', 'counts_calendar_days_for_post', 'uses_draft_register',
            'takes_neighbouring_row', 'wrong_class_period', 'counts_issue_day', 'ignores_postal_rule',
            'ignores_amendment', 'ignores_extension', 'late_on_deadline_day',
            'counts_deadline_day_in_days_late', 'ignores_nonworking_shift')
SPECIAL = {'extends_urgent_notice', 'counts_pending_extension', 'counts_refused_extension',
           'counts_void_extension', 'applies_amendment_too_early', 'ignores_holiday', 'uses_draft_register',
           'takes_neighbouring_row'}
RARE_FAMILIES = SPECIAL | {5, 6, 7, 'overstated'}
OPEN_FACT_ERRORS = {'asserts_unknown_as_fact', 'treats_unknown_as_settled'}

PROC = lambda *anchors: [('procedure.md', a) for a in anchors]  # noqa: E731


REGISTERS = ('register-a.md', 'register-b.md')


def cites_for():
    """The passages each kind of claim cites: the same list for a true claim and for every wrong variant of it. A claim
    that depends on the register cites both issues of it and the document index, always in the same order, so that
    the citations do not say which issue is in force."""
    both = lambda *anchors: [(name, a) for a in anchors for name in REGISTERS]  # noqa: E731
    deadline = (PROC('P-01', 'P-02', 'P-03', 'P-04', 'P-05') + [('amendment.md', 'A-01'), ('amendment.md', 'A-02'),
                ('calendar.md', 'C-02'), ('document-index.md', 'D-02')] + both('R-01')
                + [('extensions.md', 'X-01'), ('extensions.md', 'X-02')])
    timing = deadline + PROC('P-06', 'P-07') + both('R-02', 'R-03')
    return {'deadline': deadline, 'timing': timing, 'shifted': deadline + PROC('P-08'),
            'notice': [('document-index.md', 'D-02')] + both('R-01')}


Result = collections.namedtuple('Result', 'deadline basic took shifted received late days')


class Retry(Exception):
    """The drawn dossier lacks something the family needs; draw another."""


# ---------- The rule engine ----------

class Rules:
    def __init__(self, periods, amended, effective, holidays):
        self.periods, self.amended, self.effective = periods, amended, effective
        self.holidays = frozenset(holidays)

    def off(self, day, ignore_holidays=False):
        return day.weekday() >= 5 or (not ignore_holidays and day in self.holidays)

    def shifted_to(self, day, ignore_holidays=False):
        while self.off(day, ignore_holidays):
            day += DAY
        return day

    def working_after(self, day, count, ignore_holidays=False):
        found = 0
        while found < count:
            day += DAY
            found += not self.off(day, ignore_holidays)
        return day

    def working_before(self, day, ignore_holidays=False):
        day -= DAY
        while self.off(day, ignore_holidays):
            day -= DAY
        return day

    def period(self, n, slip=None):
        cls = WRONG_CLASS[n['cls']] if slip == 'wrong_class_period' else n['cls']
        period = self.periods[cls]
        if cls in self.amended and slip != 'ignores_amendment':
            if n['issued'] >= self.effective or (
                    slip == 'applies_amendment_too_early' and n['issued'] + DAY * period >= self.effective):
                period = self.amended[cls]
        return cls, period

    def chain(self, n, ext, slip=None, decided=None):
        """(basic deadline, deadline, whether an extension took effect, whether a shift changed the deadline)."""
        cls, period = self.period(n, slip)
        last = n['issued'] + DAY * (period - (1 if slip == 'counts_issue_day' else 0))
        ignore = slip == 'ignores_holiday'
        move = (lambda d: d) if slip == 'ignores_nonworking_shift' else (lambda d: self.shifted_to(d, ignore))
        basic = move(last)
        deadline, took = basic, False
        if ext is not None and slip != 'ignores_extension':
            status = ext['decision']
            counted = status == 'granted' or (slip == 'counts_refused_extension' and status == 'refused') or (
                slip == 'counts_pending_extension' and status == 'pending')
            date = decided if decided is not None else ext['date']
            in_time = status != 'granted' or slip == 'counts_void_extension' or date <= basic
            allowed = cls != 'urgent' or slip == 'extends_urgent_notice'
            if counted and in_time and allowed:
                raw = basic + DAY * ext['days']
                deadline = raw if slip in ('ignores_nonworking_shift', 'ignores_shift_after_extension') else move(raw)
                took = True
        # P-08: later than the last day, or later than the last day plus the days requested when an extension takes effect.
        shifted = deadline > last + DAY * (ext['days'] if took else 0)
        return basic, deadline, took, shifted

    def receipt(self, n, sent, slip=None):
        if n['channel'] != 'Post' or slip == 'ignores_postal_rule':
            return sent
        if slip == 'counts_calendar_days_for_post':
            return sent + 2 * DAY
        return self.working_after(sent, 2, slip == 'ignores_holiday')

    def basic(self, n):
        return self.chain(n, None)[0]


def evaluate(rules, n, ext, slip=None, sent=None, decided=None):
    basic, deadline, took, shifted = rules.chain(n, ext, slip, decided)
    received = rules.receipt(n, n['sent'] if sent is None else sent, slip)
    late = received >= deadline if slip == 'late_on_deadline_day' else received > deadline
    days = (received - deadline).days
    if late and slip == 'counts_deadline_day_in_days_late':
        days += 1
    return Result(deadline, basic, took, shifted, received, late, days if late else 0)


class Model:
    """The true results of every notice in every world the sources allow, plus the results under each slip."""

    def __init__(self, rules, notices, extensions, draft, neighbour):
        self.rules, self.notices, self.draft, self.neighbour = rules, notices, draft, neighbour
        self.ids = [n['id'] for n in notices]
        self.by_id = {n['id']: n for n in notices}
        self.ext_of = {e['nid']: e for e in extensions}
        self.table, self.fixed, self.variable, self.canon = {}, {}, [], {}
        for n in notices:
            ext = self.ext_of.get(n['id'])
            sent_values = n.get('sent_values')
            date_values = ext.get('date_values') if ext else None
            names = ([f'sent:{n["id"]}'] if sent_values else []) + ([f'decided:{ext["ref"]}'] if date_values else [])
            mapping = {}
            for s in sent_values or [None]:
                for d in date_values or [None]:
                    key = tuple(v for v, open_ in ((s, sent_values), (d, date_values)) if open_)
                    mapping[key] = evaluate(rules, n, ext, None, s, d)
            self.table[n['id']] = (names, mapping)
            self.canon[n['id']] = ((sent_values or [None])[0], (date_values or [None])[0])
            if names:
                self.variable.append(n['id'])
            else:
                self.fixed[n['id']] = mapping[()]
        self.cache = {}

    def get(self, w, ident):
        names, mapping = self.table[ident]
        return mapping[tuple(w[x] for x in names)]

    def tally(self, fn, ids=None):
        ids = self.ids if ids is None else ids
        fixed = sum(fn(self.fixed[i]) for i in ids if i in self.fixed)
        open_ids = [i for i in ids if i not in self.fixed]
        return lambda w: fixed + sum(fn(self.get(w, i)) for i in open_ids)

    def slip_results(self, slip):
        if slip not in self.cache:
            out = {}
            for n in self.notices:
                ext = self.ext_of.get(n['id'])
                sent, decided = self.canon[n['id']]
                if slip == 'uses_draft_register':
                    out[n['id']] = evaluate(self.rules, self.draft[n['id']], ext, None, sent, decided)
                elif slip == 'takes_neighbouring_row':
                    other = self.neighbour.get(n['id'])
                    out[n['id']] = self.fixed[other] if other else None
                else:
                    out[n['id']] = evaluate(self.rules, n, ext, slip, sent, decided)
            self.cache[slip] = out
        return self.cache[slip]


# ---------- Drawing one dossier ----------

def make_draft(rng, rules, notices, extensions, forced_issue, forced_sent, touch, protected):
    """The second issue of the register, in which several rows differ in class or in a date. A moved date of issue keeps
    every property the true register has: the rows stay in order of issue, the date is a working day that is not a
    holiday, and it lies before the reply and before the decision on any extension of the notice. So nothing in the
    file itself tells the two issues apart."""
    draft = {n['id']: dict(n) for n in notices}
    decided = {e['nid']: e['date'] for e in extensions if e['date'] is not None}
    free = [n['id'] for n in notices if n['id'] not in protected]
    ids = list(forced_issue) + list(forced_sent) + [i for i in touch if rng.random() < 0.7]
    ids += rng.sample([i for i in free if i not in ids], rng.randint(3, 5))
    plan = {}
    for ident in ids:
        if ident not in plan and ident not in protected:
            plan[ident] = 'issued' if ident in forced_issue else 'sent' if ident in forced_sent else rng.choice(
                ('issued', 'issued', 'class', 'sent'))
    done = 0
    for position, n in enumerate(notices):
        what, row = plan.get(n['id']), draft[n['id']]
        if what == 'class':
            row['cls'] = rng.choice([c for c in CLASSES if c != row['cls']])
        elif what == 'sent':
            while True:
                moved = row['sent'] + DAY * rng.choice((-5, -3, -2, -1, 1, 2, 4, 5))
                if moved != row['sent'] and moved > row['issued']:
                    break
            row['sent'] = moved
        elif what == 'issued':
            low = draft[notices[position - 1]['id']]['issued'] if position else row['issued'] - 9 * DAY
            high = min(notices[position + 1]['issued'] if position + 1 < len(notices) else row['issued'] + 8 * DAY,
                       row['sent'] - DAY, decided.get(n['id'], row['sent']) - DAY)
            options = [row['issued'] + DAY * k for k in range(-9, 9) if k and low <= row['issued'] + DAY * k <= high
                       and not rules.off(row['issued'] + DAY * k)]
            if not options:
                if n['id'] in forced_issue:
                    raise Retry
                continue
            row['issued'] = rng.choice(options)
        done += bool(what)
    if done < 6:
        raise Retry
    return draft


def assemble(rng):
    # ---- the rules and the calendar ----
    year = rng.randint(2031, 2034)
    while True:
        effective = datetime.date(year, rng.randint(4, 8), rng.randint(3, 26))
        if effective.weekday() < 5:
            break
    periods = {'standard': rng.choice((28, 30, 35)), 'complex': rng.choice((45, 50, 60)), 'urgent': rng.choice((7, 10, 14))}
    changed = sorted(rng.sample(CLASSES, rng.choice((1, 1, 2))), key=CLASSES.index)
    amended = {}
    for cls in changed:
        amended[cls] = rng.choice([v for v in NEW_PERIODS[cls] if v != periods[cls]])

    # ---- the notices ----
    start = effective - DAY * 84
    window = [start + DAY * i for i in range(155) if (start + DAY * i).weekday() < 5]
    before = effective - DAY
    while before.weekday() >= 5:
        before -= DAY
    count = rng.randint(46, 68)
    dates = sorted(rng.choices(window, k=count - 2) + [before, effective])
    notices = []
    for number, day in enumerate(dates, 1):
        cls = rng.choice(changed) if day in (before, effective) else rng.choices(CLASSES, weights=(5, 3, 2))[0]
        notices.append({'id': f'N{number:03d}', 'issued': day, 'cls': cls, 'addressee': rng.choice(ADDRESSEES),
                        'channel': rng.choices(CHANNELS, weights=(4, 2, 4))[0], 'sent': None, 'ext': None})
    gaps = [i for i in range(count - 1) if 1 <= (dates[i + 1] - dates[i]).days <= 6]
    rng.shuffle(gaps)
    pair_rows, taken = [], []
    for i in gaps:
        if i in taken or i + 1 in taken:
            continue
        pair_rows.append(i)
        taken += [i, i + 1]
        if len(pair_rows) == 3:
            break
    if len(pair_rows) < 3:
        raise Retry
    neighbour = {}
    for i in pair_rows:
        first, second = notices[i], notices[i + 1]
        second['addressee'], second['cls'] = first['addressee'], first['cls']
        neighbour[first['id']], neighbour[second['id']] = second['id'], first['id']

    def last_day(n):
        return n['issued'] + DAY * (amended[n['cls']] if n['cls'] in amended and n['issued'] >= effective
                                    else periods[n['cls']])
    issue_days = {n['issued'] for n in notices}
    lasts = sorted({last_day(n) for n in notices if last_day(n).weekday() < 5} - issue_days)
    if len(lasts) < 7:
        raise Retry
    planted = rng.sample(lasts, 7)
    pool = [d for d in (effective - DAY * 30 + DAY * i for i in range(231))
            if d.weekday() < 5 and d not in issue_days and d not in planted]
    holidays = sorted(planted + rng.sample(pool, 6))
    holiday_names = dict(zip(holidays, rng.sample(HOLIDAY_NAMES, len(holidays))))
    calendar_from, calendar_to = start - DAY * 28, effective + DAY * 270
    rules = Rules(periods, amended, effective, holidays)

    # ---- extensions and the facts left open ----
    paired = set(neighbour)
    free = [n for n in notices if n['id'] not in paired]
    urgent_free = [n for n in free if n['cls'] == 'urgent']
    other_free = [n for n in free if n['cls'] != 'urgent']
    kinds = {'valid': rng.randint(3, 4), 'void': rng.randint(1, 2), 'refused': rng.randint(1, 2),
             'pending': rng.randint(1, 2), 'e1': 1, 'e2': rng.choice((0, 1))}
    urgent_count = rng.randint(1, 2)
    needed = sum(kinds.values()) + 2
    if len(urgent_free) < urgent_count or len(other_free) < needed:
        raise Retry
    chosen = rng.sample(other_free, needed)
    roles, cursor = {}, 0
    for kind in ('valid', 'void', 'refused', 'pending', 'e1', 'e2'):
        for _ in range(kinds[kind]):
            roles[chosen[cursor]['id']] = kind
            cursor += 1
    p1, p2 = chosen[cursor], chosen[cursor + 1]
    for n in rng.sample(urgent_free, urgent_count):
        roles[n['id']] = 'urgent'
    p1['channel'] = p2['channel'] = 'Post'
    extensions = []
    for number, n in enumerate([n for n in notices if n['id'] in roles], 1):
        kind = roles[n['id']]
        basic = rules.basic(n)
        room = (basic - n['issued']).days
        ext = {'ref': f'X{number:02d}', 'nid': n['id'], 'days': rng.choice((5, 7, 10, 14, 21)), 'kind': kind,
               'decision': 'granted', 'date': None}
        if kind in ('valid', 'urgent'):
            ext['date'] = basic if rng.random() < 0.25 else n['issued'] + DAY * rng.randint(1, room)
        elif kind == 'void':
            ext['date'] = basic + DAY * rng.randint(1, 9)
        elif kind == 'refused':
            ext.update(decision='refused', date=n['issued'] + DAY * rng.randint(1, room + 3))
        elif kind == 'pending':
            ext.update(decision='pending')
        elif kind == 'e1':
            # Mostly a range that straddles the basic deadline, so the extension is in time on some days and void on
            # others; otherwise a range on one side of it, which leaves the effect of the extension settled.
            mode = rng.choices(('straddle', 'in_time', 'void'), weights=(16, 2, 2))[0]
            e1_straddles = mode == 'straddle'
            length = rng.choice((3, 4))
            if mode == 'straddle':
                offset = rng.choice((-1, 0)) if length == 3 else rng.choice((-2, -1, 0))
            elif mode == 'in_time':
                offset = -rng.randint(length + 1, length + 3)
            else:
                offset = rng.randint(1, 4)
            ext['date_values'] = [basic + DAY * (offset + i) for i in range(length)]
            n['channel'] = rng.choice(('Electronic', 'Hand'))
        else:
            first = basic - DAY * rng.randint(3, 5)
            ext['date_values'] = [first + DAY * i for i in range(3)]
        n['ext'] = ext['ref']
        extensions.append(ext)
    ext_of = {e['nid']: e for e in extensions}

    # ---- the replies ----
    def sent_for(n, target):
        if n['channel'] != 'Post':
            # A reply by hand or electronically may be dated on a non-working day; it is received on that date (P-06).
            return rules.shifted_to(target) if rules.off(target) and rng.random() < 0.5 else target
        received = rules.shifted_to(target)
        for _ in range(8):
            w1 = rules.working_before(received)
            w2 = rules.working_before(w1)
            low, high = max(w2, n['issued'] + DAY), w1 - DAY
            if low <= high:
                posted = low + DAY * rng.randint(0, (high - low).days)
                if rules.receipt(n, posted) != received:
                    raise Retry
                return posted
            received = rules.shifted_to(received + DAY)
        raise Retry

    for n in notices:
        if n is p1 or n is p2:
            continue
        ext = ext_of.get(n['id'])
        decided = ext['date_values'][0] if ext and ext.get('date_values') else None
        basic, deadline, _, _ = rules.chain(n, ext, None, decided)
        role = roles.get(n['id'])
        if ext is not None and (rng.random() < 0.3 if role == 'e1' else role != 'e2' and rng.random() < 0.65):
            reach = rules.shifted_to(basic + DAY * ext['days'])
            target = basic + DAY * rng.randint(1, (reach - basic).days)
        else:
            if n['channel'] == 'Post' and rng.random() < 0.5:
                offset = rng.choice((-3, -2, -1, 0, 1, 2, 3, 4))
            else:
                chance = rng.random()
                offset = 0 if chance < 0.12 else -rng.randint(1, 12) if chance < 0.62 else rng.randint(1, 14)
            target = deadline + DAY * offset
        n['sent'] = sent_for(n, max(target, n['issued'] + DAY * 2))

    def range_for(n, mode):
        """Consecutive posting days. 'straddle': the reply is on time for some and late for others; 'late' or 'ontime':
        the same outcome on every day, with receipt dates that differ; 'constant': every day gives one receipt date
        (a Friday to Sunday range, received on the Tuesday)."""
        deadline = rules.chain(n, None)[1]
        for _ in range(120):
            length = rng.choice((3, 4))
            if mode == 'constant':
                target = deadline + DAY * rng.randint(-10, 12)
                received = target + DAY * ((1 - target.weekday()) % 7)
                if rules.off(received) or rules.off(received - DAY) or rules.off(received - DAY * 4):
                    continue
                days = [received - DAY * 4 + DAY * i for i in range(3)]
                if any(rules.receipt(n, x) != received for x in days) or min(days) <= n['issued']:
                    continue
                return days
            if mode == 'straddle':
                first = deadline - DAY * rng.randint(1, 5)
            else:
                early = mode == 'ontime'
                received = rules.shifted_to(deadline - DAY * rng.randint(2, 12) if early else deadline + DAY * rng.randint(2, 12))
                first = sent_for(n, received) - DAY * rng.randint(0, length - 1)
            days = [first + DAY * i for i in range(length)]
            outcomes = [rules.receipt(n, x) > deadline for x in days]
            if min(days) <= n['issued']:
                continue
            if (mode == 'straddle' and len(set(outcomes)) == 2) or (mode != 'straddle' and set(outcomes) == {mode == 'late'}):
                return days
        raise Retry
    p1['sent_values'] = range_for(p1, rng.choices(('late', 'ontime', 'constant'), weights=(9, 5, 6))[0])
    # At least one open fact must leave a claim undetermined, so the second reply straddles whenever the extension does not.
    p2_mode = rng.choices(('straddle', 'late', 'constant'), weights=(11, 5, 4))[0]
    p2['sent_values'] = range_for(p2, p2_mode if e1_straddles else 'straddle')
    deadline_p1 = rules.chain(p1, None)[1]
    settled_late = rules.receipt(p1, p1['sent_values'][0]) > deadline_p1

    # ---- the model, before the draft ----
    protected = {p1['id'], p2['id']} | {e['nid'] for e in extensions if e.get('date_values')}
    draft_probe = Model(rules, notices, extensions, {n['id']: n for n in notices}, neighbour)

    def decisive(ident, field):
        base, hit = draft_probe.fixed[ident], set()
        for rule, slip in ((rule, slip) for rule, slips in PRIMARY.items() for slip in slips):
            r = draft_probe.slip_results(slip)[ident]
            if field == 'deadline':
                differs = r.deadline != base.deadline
            elif field == 'timing':
                differs = r.late != base.late
            else:
                differs = (r.late, r.days) != (base.late, base.days)
            if differs:
                hit.add(rule)
        return hit
    used = []

    def choose(candidates, field, need, among, must=frozenset(), prefer=None):
        scored = [(len(decisive(n['id'], field) & among), n) for n in candidates
                  if n['id'] in draft_probe.fixed and n['id'] not in used and must <= decisive(n['id'], field)]
        scored = [(s, n) for s, n in scored if s >= need]
        if not scored:
            raise Retry
        if prefer and any(prefer(n) for _, n in scored) and rng.random() < 0.7:
            scored = [(s, n) for s, n in scored if prefer(n)]
        best = max(s for s, _ in scored)
        n = rng.choice([n for s, n in scored if s >= best - (1 if best > need else 0)])
        used.append(n['id'])
        return n
    plain_rows = [n for n in notices if n['ext'] is None]
    one = choose(plain_rows, 'deadline', 2, {2, 4})
    ext_kind = rng.choice(('valid', 'void', 'refused', 'pending', 'urgent'))
    with_ext = [n for n in notices if n['ext'] and roles[n['id']] == ext_kind]
    two = choose(with_ext, 'deadline', 2, {2, 4, 5}, {5})
    timing_pool = [n for n in notices if n['channel'] == 'Post'] if rng.random() < 0.7 else notices
    try:
        # Often a reply received on the deadline itself, where only the rule on time and late decides.
        if rng.random() < 0.9:
            three = choose(timing_pool, 'timing', 2, {2, 4, 5, 6, 7}, {7})
        else:
            raise Retry
    except Retry:
        three = choose(timing_pool, 'timing', 2, {2, 4, 5, 6, 7})
    late_rows = [n for n in notices if n['id'] in draft_probe.fixed and draft_probe.fixed[n['id']].late]
    four = choose(late_rows, 'days', 2, {2, 4, 5, 6},
                  prefer=lambda n: n['channel'] != 'Post' and rules.off(n['sent']))   # a reply dated on a non-working day
    pairs_ids = [n for n in notices if n['id'] in neighbour and n['id'] not in used]
    look_a = rng.choice(pairs_ids)
    draft = make_draft(rng, rules, notices, extensions, [look_a['id']], [],
                       [one['id'], two['id'], three['id'], four['id']], protected)
    model = Model(rules, notices, extensions, draft, neighbour)
    # Two issues of the register. Which one is in force is decided by the document index alone, and it is not
    # always the later one.
    last_reply = max(max(n['sent_values']) if n.get('sent_values') else n['sent'] for n in notices)
    earlier = last_reply + DAY * rng.randint(5, 25)
    later = earlier + DAY * rng.randint(12, 45)
    names = ('register-a.md', 'register-b.md')
    in_force = rng.choice(names)
    in_force_later = rng.random() < 0.5
    register = (in_force, names[1] if in_force == names[0] else names[0],
                later if in_force_later else earlier, earlier if in_force_later else later)
    return Dossier(rng=rng, rules=rules, model=model, notices=notices, extensions=extensions, draft=draft,
                   neighbour=neighbour, holiday_names=holiday_names, calendar=(calendar_from, calendar_to),
                   p1=p1, p2=p2, settled_late=settled_late, roles=roles, picks=(one, two, three, four, look_a),
                   register=register)


Dossier = collections.namedtuple('Dossier', 'rng rules model notices extensions draft neighbour holiday_names '
                                 'calendar p1 p2 settled_late roles picks register')


# ---------- Rendering ----------

def render(d):
    rules, notices, extensions = d.rules, d.notices, d.extensions
    effective, total = rules.effective, len(notices)
    day = lambda value: value.isoformat()  # noqa: E731
    open_sent = [n for n in notices if n.get('sent_values')]
    open_dates = [e for e in extensions if e.get('date_values')]

    def register_rows(rows):
        return [(r['id'], day(r['issued']), r['cls'], r['addressee'], r['channel'],
                 day(r['sent']) if r['sent'] else 'Not recorded', r['ext'] or NONE) for r in rows]
    headers = ('Notice', 'Date issued', 'Class', 'Addressee', 'Reply channel', 'Reply date', 'Extension request')
    first = rules.periods
    amended_text = ' '.join(f'The reply period of {"an" if cls == "urgent" else "a"} {cls} notice is {days} days.'
                            for cls, days in sorted(rules.amended.items(), key=lambda item: CLASSES.index(item[0])))
    listed = lambda values: ', '.join(day(v) for v in values)  # noqa: E731
    cover = ' '.join(
        f'The register records no reply date for notice {n["id"]}. The cover note filed with that reply states that it was '
        f'posted on one of these days: {listed(n["sent_values"])}. Each of these days is possible, whether or not it is a '
        f'working day, and nothing else about the posting date is known.' for n in open_sent)
    minutes = ' '.join(
        f'The register records no date of decision for request {e["ref"]}. The minute of the extension panel for that '
        f'request states that the decision was taken on one of these days: {listed(e["date_values"])}. Each of these days '
        f'is possible, whether or not it is a working day, and nothing else about the date of the decision is known.' for e in open_dates)
    in_force, other, issue_in_force, issue_other = d.register

    def register_file(issue, rows):
        return document(f'Notice register, issue of {day(issue)}', [
            section('R-01', 'Notices', f'This register lists every notice with which this review is concerned: {total} notices. '
                    'Each notice received exactly one reply, whether or not the register shows its date, and has at most one '
                    'extension request.\n\n' + table(headers, register_rows(rows))),
            section('R-02', 'Reply dates', 'The column Reply date gives the date on which the reply was sent, when the '
                    'channel is Electronic, the date on which it was delivered, when the channel is Hand, and the date on '
                    'which it was posted, when the channel is Post. Not recorded means that the register gives no date for '
                    'that reply; R-03 then states the days on which it may have been posted.'),
            section('R-03', 'Cover notes', cover)])
    decision_text = {'granted': 'Granted', 'refused': 'Refused', 'pending': 'Pending'}
    sources = {
        'document-index.md': document('Document index', [
            section('D-01', 'Purpose', 'This index lists the documents supplied for the review of the notices recorded in '
                    'the register that D-02 lists as in force. It states which documents are in force and which settle '
                    'nothing.'),
            section('D-02', 'Status of the documents', table(('Document', 'Status', 'Contents and reason'), [
                ('procedure.md', 'In force', 'Rules for reply periods, deadlines, extensions and the receipt of replies'),
                ('amendment.md', 'In force', 'Change to the reply period of one or more classes of notice'),
                ('calendar.md', 'In force', 'Public holidays')] + [
                (name, 'In force' if name == in_force else 'Settles nothing',
                 f'Notices, reply channels and reply dates, issue of {day(issue)}' + (
                     '' if name == in_force else '; replaced by a later issue' if issue < issue_in_force
                     else '; a proposed revision that was not adopted'))
                for name, issue in sorted([(in_force, issue_in_force), (other, issue_other)])] + [
                ('extensions.md', 'In force', 'Extension requests and their decisions')])),
            section('D-03', 'Precedence', 'Where amendment.md and procedure.md give different reply periods for the same '
                    'notice, amendment.md prevails for the notices that A-01 names, and procedure.md prevails for every '
                    'other notice.'),
            section('D-04', 'Documents that settle nothing', 'A document that D-02 lists as settling nothing contributes no '
                    'class, date, channel or reference. Where the two registers differ, the register that D-02 lists as in '
                    'force is right.')]),
        'procedure.md': document('Reply procedure', [
            section('P-01', 'Definitions', 'A period of days is counted in calendar days unless a rule says otherwise. A '
                    'non-working day is a Saturday, a Sunday or a date listed in the holiday table in calendar.md (C-02). A '
                    'working day is a day that is not a non-working day. To shift a date means to replace it with the first '
                    'working day after it when it is a non-working day; a date that is a working day is not shifted. The '
                    'class, the date issued, the reply channel and the reply date of a notice are the values recorded for it '
                    'in the register that D-02 lists as in force.'),
            section('P-02', 'Reply period', 'Every notice belongs to one class: standard, complex or urgent. The reply '
                    f'period of a notice is {first["standard"]} days for a standard notice, {first["complex"]} days for a '
                    f'complex notice and {first["urgent"]} days for an urgent notice. A figure is replaced only where '
                    'amendment.md replaces it (D-03).'),
            section('P-03', 'Last day', 'The day on which a notice was issued is not counted. Day 1 of the reply period is '
                    'the day after the day of issue, and the last day of the reply period is the day that is as many '
                    'calendar days after the day of issue as the reply period has days. A notice issued on the 3rd of a '
                    'month with a reply period of 10 days thus has the 13th of that month as its last day. The reply '
                    'period ends at the end of its last day.'),
            section('P-04', 'Basic deadline', 'The basic deadline of a notice is its last day when that is a working day. '
                    'When the last day is a non-working day, it is shifted, and the basic deadline is the first working '
                    'day after it.'),
            section('P-05', 'Extensions and the deadline', 'An extension request is recorded in extensions.md with its '
                    'notice, the number of days requested, its decision (Granted, Refused or Pending) and the date of the '
                    'decision, where the register gives one. A request shown as Pending had not been decided when the '
                    'register was closed; it is not granted and does not take effect. Each notice '
                    'has at most one request. An extension takes effect when all three of these hold: the request is '
                    'granted; the notice is not urgent; the date of the decision is not later than the basic deadline (a '
                    'decision on the basic deadline itself is in time). A granted request whose decision date is later than '
                    'the basic deadline is void. When an extension takes effect, the extended deadline is the date that is '
                    'as many calendar days after the basic deadline as the days requested; when that date is a non-working '
                    'day, it is shifted and the extended deadline is the first working day after it. A request '
                    'that does not take effect adds nothing and gives no extended deadline. The deadline of a notice is its extended deadline when it has one, and otherwise its basic '
                    'deadline.'),
            section('P-06', 'Receipt of a reply', 'A reply with the channel Electronic or Hand is received on its reply '
                    'date, whether or not that date is a working day. A reply with the channel Post is received on the second working day after its reply date, which '
                    'is its posting date: counting begins with the day after the posting date, the posting date itself is '
                    'never counted even when it is a working day, and only working days are counted.'),
            section('P-07', 'On time and late', 'A reply is on time when it is received on or before the deadline. A reply '
                    'that is received after the deadline is late. A late reply is late by the number of calendar days from '
                    'the deadline to the day on which it was received; a reply received on the day after the deadline is '
                    'late by 1 day.'),
            section('P-08', 'Shifted deadline', 'Whether a deadline is a shifted deadline is decided in one of two ways. '
                    'For a notice on which no extension takes effect, the deadline is a shifted deadline when it is later '
                    'than the last day of the reply period. For a notice on which an extension takes effect, the deadline '
                    'is a shifted deadline when it is later than the date that is as many calendar days after the last '
                    'day of the reply period as the days requested. In every other case the deadline is not a shifted '
                    'deadline.')]),
        'amendment.md': document('Amendment 1 to the reply procedure', [
            section('A-01', 'Changed reply periods', f'For every notice issued on or after {day(effective)}, the reply '
                    f'period in P-02 of procedure.md is replaced as follows. {amended_text}'),
            section('A-02', 'Notices issued earlier', f'A notice issued before {day(effective)} keeps the reply period in '
                    f'P-02, including a notice whose reply period had begun but had not ended on {day(effective)}. This '
                    'amendment changes no other rule of procedure.md.')]),
        'calendar.md': document('Public holidays', [
            section('C-01', 'Scope', f'This calendar covers every date from {day(d.calendar[0])} to {day(d.calendar[1])}. '
                    'Every public holiday in that period is listed in C-02, and no other date in that period is a public '
                    'holiday.'),
            section('C-02', 'Holiday table', table(('Date', 'Holiday'), [(day(h), d.holiday_names[h]) for h in sorted(rules.holidays)]))]),
        'extensions.md': document('Extension register', [
            section('X-01', 'Requests', 'This register lists every extension request made for the notices in the register that D-02 lists as in force.'
                    '\n\n' + table(('Request', 'Notice', 'Days requested', 'Decision', 'Date of the decision'), [
                        (e['ref'], e['nid'], e['days'], decision_text[e['decision']],
                         day(e['date']) if e['date'] else 'Not recorded' if e.get('date_values') else NONE)
                        for e in extensions])),
            section('X-02', 'Undated decisions', minutes)])}
    for name, issue, rows in ((in_force, issue_in_force, notices), (other, issue_other, [d.draft[n['id']] for n in notices])):
        sources[name] = register_file(issue, rows)
    return dict(sorted(sources.items()))


# ---------- Claims ----------

def working_dates_only(variants, rules):
    """A deadline or a receipt by post is always a working day, so a stated date on a non-working day would be refutable
    by its weekday alone; only a variant that comes from a slip may state one."""
    return [c for c in variants if c['error'] != 'wrong_value' or not rules.off(c['stated'])]


def one_exact_open_variant(wrong, rng):
    """Of the claims that state a feasible value of an open fact exactly, keep one, so that the claims about open facts
    are as often lower bounds as exact values; a lower bound that only some completions meet is kept as well."""
    exact = [c for c in wrong if c['error'] in OPEN_FACT_ERRORS and not any(
        w in c['text'] for w in ('At least', 'not earlier than', 'or after'))]
    drop = rng.sample(exact, len(exact) - 1) if len(exact) > 1 else []
    return [c for c in wrong if not any(c is x for x in drop)]


def family(error):
    """Slips of one rule share a family, except the special ones, so that common slips do not crowd out rare ones."""
    return error if error in SPECIAL or error not in RULE_OF else RULE_OF[error]


def finish(true, variants, rng, limit, always=()):
    """Drop variants that repeat the true claim or each other (the narrowest slip names a repeated value), then keep
    the claims about open facts and one variant from each of `limit` minus those families, drawn at random."""
    ordered = sorted(variants, key=lambda v: PRIORITY.index(v['error']) if v['error'] in PRIORITY else len(PRIORITY))
    seen, kept = {true['text']}, []
    for item in ordered:
        if item['text'] not in seen:
            seen.add(item['text'])
            kept.append(item)
    pinned = [c for c in kept if c['error'] in always]
    families = {}
    for item in kept:
        if item['error'] not in always:
            families.setdefault(family(item['error']), []).append(item)
    keys = list(families)
    chosen = []
    for _ in range(min(len(keys), max(0, limit - len(pinned)))):
        # Rare slips are three times as likely to be drawn as the slips every dossier offers.
        pick = rng.choices(keys, weights=[3 if k in RARE_FAMILIES else 1 for k in keys])[0]
        keys.remove(pick)
        chosen.append(pick)
    return true, pinned + [rng.choice(families[k]) for k in chosen]


def claims_of(d, rng):
    rules, model, notices = d.rules, d.model, d.notices
    total = len(notices)
    C = cites_for()
    ids = model.ids
    unknowns = {}
    for n in notices:
        if n.get('sent_values'):
            unknowns[f'sent:{n["id"]}'] = n['sent_values']
    for e in d.extensions:
        if e.get('date_values'):
            unknowns[f'decided:{e["ref"]}'] = e['date_values']
    base = {'notices': total}
    if len(list(itertools.islice(generate.worlds(base, unknowns), 600))) > 500:
        raise Retry
    one, two, three, four, look_a = d.picks
    e1_notice = next(e['nid'] for e in d.extensions if e['kind'] == 'e1')
    # Every notice that is the subject of a claim; no notice is the subject of two, so that an altered report
    # cannot contradict itself about one notice.
    claimed = {one['id'], two['id'], three['id'], four['id'], look_a['id'], d.p1['id'], d.p2['id'], e1_notice}
    slip_names = [s for s in PRIORITY if s in RULE_OF]
    rule_and_draft = slip_names + ['uses_draft_register']

    def true_of(ident):
        return model.fixed[ident]

    # --- claim builders ---
    def deadline_claim(ident, value, exact=True, error=None):
        text = f'The deadline of notice {ident} is {"" if exact else "not earlier than "}{value.isoformat()}.'
        pred = (lambda w: model.get(w, ident).deadline == value) if exact else (lambda w: model.get(w, ident).deadline >= value)
        return claim('deadline_date', text, pred, C['deadline'], lambda w: model.get(w, ident).deadline, value, error=error)

    def timing_claim(ident, late, error=None):
        text = f'The reply to notice {ident} was received {"after" if late else "on or before"} its deadline.'
        return claim('reply_timing', text, lambda w: model.get(w, ident).late == late, C['timing'],
                     lambda w: model.get(w, ident).received, error=error)

    def days_claim(ident, days, error=None):
        text = f'The reply to notice {ident} was received {plural(days, "day")} after its deadline.'
        return claim('days_late', text, lambda w: model.get(w, ident).late and model.get(w, ident).days == days,
                     C['timing'], lambda w: model.get(w, ident).days, days, error=error)

    def from_result(ident, r, error, kind):
        """The claim a reader who reached result `r` would state, or None when the slip gives nothing to state."""
        if r is None:
            return None
        if kind == 'deadline':
            return deadline_claim(ident, r.deadline, True, error) if r.deadline > by_id[ident]['issued'] else None
        if kind == 'timing':
            return timing_claim(ident, r.late, error)
        # A wrong variant keeps the kind of the true claim, so a reader who is on time has no day count to state.
        return days_claim(ident, r.days, error) if r.late and r.days >= 1 else None
    by_id = model.by_id
    slips_here = rule_and_draft + ['takes_neighbouring_row']

    def per_notice(ident, truth, kind, limit=4):
        variants = []
        for slip in slips_here:
            made = from_result(ident, model.slip_results(slip)[ident], slip, kind)
            if made:
                variants.append(made)
        return finish(truth, variants, rng, limit)
    slots = []
    # 1-4: single notices where several rules decide
    for ident, kind, truth in ((one['id'], 'deadline', deadline_claim(one['id'], true_of(one['id']).deadline)),
                               (two['id'], 'deadline', deadline_claim(two['id'], true_of(two['id']).deadline)),
                               (three['id'], 'timing', timing_claim(three['id'], true_of(three['id']).late)),
                               (four['id'], 'days', days_claim(four['id'], true_of(four['id']).days))):
        slots.append(per_notice(ident, truth, kind))

    # 5-7: counts over the whole register
    def count_slot(kind, wording, fn, cites, members=None):
        counter = model.tally(fn, members)
        low, high = span(counter, base, unknowns)

        def make(value, exact, error=None):
            lead = 'Exactly' if exact else 'At least'
            pred = (lambda w: counter(w) == value) if exact else (lambda w: counter(w) >= value)
            return claim(kind, wording.format(lead=lead, value=value, total=total, requests=len(d.extensions)),
                         pred, cites, counter, value, error=error)
        true, wrong = bound_slot(make, low, high, rng, (1, 2, 3))
        if low != high:
            wrong.append(make(high, False, 'asserts_unknown_as_fact'))     # a bound that only some completions meet
        for slip in rule_and_draft:
            results = model.slip_results(slip)
            value = sum(fn(results[i]) for i in (ids if members is None else members))
            if value < low or value > high:
                # A count above the range may also be stated as a lower bound, which is then too high.
                wrong.append(make(value, value < low or rng.random() < 0.5, slip))
        # "Exactly 0" is never true, so a variant that states it would be wrong by its wording alone.
        wrong = [c for c in wrong if not (c['stated'] == 0 and c['text'].startswith('Exactly'))]
        return finish(true, one_exact_open_variant(wrong, rng), rng, 4, OPEN_FACT_ERRORS)
    slots.append(count_slot('late_replies', '{lead} {value} of the {total} notices in the register had a reply received '
                            'after the deadline.', lambda r: r.late, C['timing']))
    slots.append(count_slot('shifted_deadlines', '{lead} {value} of the {total} notices in the register have a shifted '
                            'deadline.', lambda r: r.shifted, C['shifted']))
    slots.append(count_slot('extensions_in_effect', '{lead} {value} of the {requests} extension requests in the extension '
                            'register took effect.', lambda r: r.took, C['deadline']))

    # 8: whether one request took effect; each kind of request has its own slip
    free_requests = [e for e in d.extensions if not e.get('date_values') and e['nid'] not in claimed]
    kinds_here = [k for k in ('valid', 'void', 'refused', 'pending', 'urgent') if any(e['kind'] == k for e in free_requests)]
    if not kinds_here:
        raise Retry
    kind = rng.choice(kinds_here + [k for k in kinds_here if k == 'valid'])
    ext = rng.choice([e for e in free_requests if e['kind'] == kind])
    slip = {'valid': 'ignores_extension', 'void': 'counts_void_extension', 'refused': 'counts_refused_extension',
            'pending': 'counts_pending_extension', 'urgent': 'extends_urgent_notice'}[kind]
    asked, took = ext['nid'], model.fixed[ext['nid']].took

    def effect_claim(ident, flag, error=None):
        return claim('extension_effect', f'The extension requested for notice {ident} {"took" if flag else "did not take"} '
                     'effect.', lambda w: model.get(w, ident).took == flag, C['deadline'], formal=True, error=error)
    variants = [effect_claim(asked, not took, slip)]
    if model.slip_results('uses_draft_register')[asked].took != took:
        variants.append(effect_claim(asked, not took, 'uses_draft_register'))
    slots.append(finish(effect_claim(asked, took), variants, rng, 4))
    claimed.add(asked)

    # 9: the latest or earliest deadline in a class
    options = []
    for cls in CLASSES:
        group = [i for i in ids if by_id[i]['cls'] == cls]
        if len(group) < 4:
            continue
        for side in ('latest', 'earliest'):
            pick = max if side == 'latest' else min
            found = set()
            for w in generate.worlds(base, unknowns):
                dated = [(model.get(w, i).deadline, i) for i in group]
                best = pick(date for date, _ in dated)
                holders = [i for date, i in dated if date == best]
                found.add((holders[0], best) if len(holders) == 1 else None)
            if len(found) == 1 and None not in found and next(iter(found))[0] not in claimed:
                options.append((cls, side, group, pick, next(iter(found))))
    if options:
        cls, side, group, pick, (holder, best) = rng.choice(options)
        claimed.add(holder)

        def extreme_claim(ident, date, error=None):
            def pred(w):
                dated = [(model.get(w, i).deadline, i) for i in group]
                top = pick(x for x, _ in dated)
                return [i for x, i in dated if x == top] == [ident] and top == date
            return claim('deadline_extreme', f'Among the {cls} notices, notice {ident} has the {side} deadline, '
                         f'{date.isoformat()}.', pred, C['deadline'], lambda w: pick(model.get(w, i).deadline for i in group),
                         date, error=error)
        variants = []
        for slip in rule_and_draft:
            table_ = model.slip_results(slip)
            dated = [(table_[i].deadline, i) for i in group]
            top = pick(x for x, _ in dated)
            holders = [i for x, i in dated if x == top]
            if len(holders) == 1 and (holders[0], top) != (holder, best) and (holders[0] == holder or holders[0] not in claimed):
                variants.append(extreme_claim(holders[0], top, slip))
        runners = sorted(((model.fixed[i].deadline, i) for i in group if i in model.fixed and i != holder
                          and i not in claimed),
                         reverse=(side == 'latest'))
        if runners:
            variants.append(extreme_claim(runners[0][1], runners[0][0], 'names_runner_up'))
        slots.append(finish(extreme_claim(holder, best), variants, rng, 4))

    # 10: a lookup where a neighbouring row and the draft say something else
    def issued_claim(ident, date, error=None):
        return claim('notice_lookup', f'Notice {ident} was issued on {date.isoformat()}.',
                     lambda w: by_id[ident]['issued'] == date, C['notice'], formal=False, error=error)
    row = look_a
    variants = [issued_claim(row['id'], by_id[d.neighbour[row['id']]]['issued'], 'takes_neighbouring_row'),
                issued_claim(row['id'], d.draft[row['id']]['issued'], 'uses_draft_register')]
    slots.append(finish(issued_claim(row['id'], row['issued']), variants, rng, 4))

    # 11: a reply whose posting date is open but whose timing is settled
    p1 = d.p1['id']
    truth = timing_claim(p1, d.settled_late)
    variants = []
    for slip in rule_and_draft:
        r = model.slip_results(slip)[p1]
        if r.late != d.settled_late:
            variants.append(timing_claim(p1, r.late, slip))
    slots.append(finish(truth, variants, rng, 4))

    # 12: a deadline that depends on an extension whose decision date is open
    e1 = next(e for e in d.extensions if e['kind'] == 'e1')
    ident = e1['nid']
    low, high = span(lambda w: model.get(w, ident).deadline, base, unknowns)

    def make(value, exact, error=None):
        return deadline_claim(ident, datetime.date.fromordinal(value), exact, error)
    true, wrong = bound_slot(make, low.toordinal(), high.toordinal(), rng, (7, 14))
    if low != high:
        wrong.append(make(high.toordinal(), False, 'asserts_unknown_as_fact'))
    for slip in rule_and_draft:
        r = model.slip_results(slip)[ident]
        if (r.deadline < low or r.deadline > high) and r.deadline > by_id[ident]['issued']:
            wrong.append(deadline_claim(ident, r.deadline, r.deadline < low or rng.random() < 0.5, slip))
    slots.append(finish(true, working_dates_only(one_exact_open_variant(wrong, rng), rules), rng, 4, OPEN_FACT_ERRORS))

    # 13: the receipt date of the reply whose posting date is open and whose lateness depends on it
    p2 = d.p2['id']
    low, high = span(lambda w: model.get(w, p2).received, base, unknowns)

    def receipt_claim(value, exact, error=None):
        date = datetime.date.fromordinal(value)
        pred = (lambda w: model.get(w, p2).received == date) if exact else (lambda w: model.get(w, p2).received >= date)
        return claim('receipt_date', f'The reply to notice {p2} was received on {"" if exact else "or after "}{date.isoformat()}.',
                     pred, C['timing'], lambda w: model.get(w, p2).received, date, error=error)
    true, wrong = bound_slot(receipt_claim, low.toordinal(), high.toordinal(), rng, (7, 14))
    if low != high:
        wrong.append(receipt_claim(high.toordinal(), False, 'asserts_unknown_as_fact'))
    for slip in rule_and_draft:
        r = model.slip_results(slip)[p2]
        if r.received < low or r.received > high:
            wrong.append(receipt_claim(r.received.toordinal(), r.received < low or rng.random() < 0.5, slip))
    slots.append(finish(true, working_dates_only(one_exact_open_variant(wrong, rng), rules), rng, 4, OPEN_FACT_ERRORS))

    # 14: late replies among the notices answered by post
    post_ids = [i for i in ids if by_id[i]['channel'] == 'Post']
    slots.append(count_slot('late_replies_by_post', '{lead} {value} of the ' + str(len(post_ids)) + ' notices with the reply '
                            'channel Post had a reply received after the deadline.', lambda r: r.late, C['timing'], post_ids))
    if not 10 <= len(slots) <= 14:
        raise Retry
    # The key names the passages that decide a claim: its citations without the issue of the register that settles nothing.
    for true, wrong in slots:
        for item in [true] + list(wrong):
            item['anchors'] = [cite for cite in item['cites'] if cite[0] != d.register[1]]
    return base, unknowns, slots


def build(rng):
    for _ in range(60):
        try:
            d = assemble(rng)
            base, unknowns, slots = claims_of(d, rng)
            return {'title': 'Synthetic notice-register review', 'sources': render(d), 'base': base,
                    'unknowns': unknowns, 'slots': slots, 'open_claims': 2}
        except Retry:
            continue
    raise GenerationError('casework: no usable dossier was drawn')
