"""Uncited site sheets: a complete register mistaken for its cited sample.

The fictional Merehaven Estates maintenance office records ventilation pressure
at 40--120 sites over six monthly inspections. Each site sheet has one operating
ceiling (copied from the site's commissioning card and stated again in each
visit account) and three readings per visit. A finding requires comparing those
numbers; no exception label, filename, heading or length identifies the result.
Both reports cite the same four sheets, chosen uniformly before their contents
are drawn. The named reader reads their union and completes every other sheet
with no exceedance. This implements the report-union option in the brief.

Settling rule sentences, reproduced verbatim in procedure.md:
The inspection register consists of every numbered site sheet supplied in this packet.
Each sheet records all three inspection readings and the applicable operating ceiling for each month shown.
A site exceeded its operating ceiling in a month if and only if at least one of its three readings for that month is strictly greater than that month's ceiling on its own sheet.
A reading equal to the ceiling is not an exceedance.
A monthly count of sites exceeding their ceilings counts each site once, even when several readings on that site exceed its ceiling.
A statement about sites in a month ranges over every site in the register, not just the sites cited beside the statement.
A finding that no site exceeded its ceiling requires checking every site's sheet for that month.
Citations are navigation aids and do not restrict the register's scope.
Sentence 7 applies to every statement whose count is zero ("the number of sites
that exceeded their operating ceilings was 0" or "was at most 0").

The second section of procedure.md (circulation note) says why the cited sheets
are the ones they are: they are the returns for which the plant caretaker
telephoned the office, and a caretaker does not telephone for every reading
above a ceiling. That is why the cited sheets hold exceedances far more often
(25.3% of cited sheet-months, 6,070 of 24,000 over seeds 1--1000) than the
others (0.43%, 1,980 of 456,078) and why a decisive sheet can be missing from
the list. It states no rule beyond the eight sentences above.

What a statement says. Each month has one statement, of one of two forms: "the
number of sites that exceeded their operating ceilings was N" or "... was at
most N". The key is exact for both. A month's true count T is k + u, where k is
the number of exceeding sites among the four cited sheets (0, 1 or 2, drawn from
0,0,1,1,1,2,2) and u is the number among the others (0 in most months, 1 or 2 in
a "decisive" month). Two dossiers in three have two decisive months and one in
three has none (663 and 337 of seeds 1--1000). T never exceeds 4. The flawed
reader counts only the cited sheets, so its count is k.

How wrong counts are drawn. Every wrong count is the exact output of a named
slip and is never below k (a count below k would be refuted by the cited
sheets alone, so the family offers none). In a decisive month the only wrong
count offered is k itself, "ignores_uncited_sheets" (for "at most N" the count
is also k). In every other month a wrong exact count is one or two above the
true count: "counts_equality_as_exceedance" (one or two sites have a reading
exactly at the ceiling, all on uncited sheets) or "counts_readings_not_sites"
(an exceeding site with two high readings, drawn alike on every sheet). The
mixed report can alter exactly three months chosen without regard to
citations: every decisive month plus ordinary months with an exact statement.
Over seeds 1--1000, 66.7% of the 12,000 statements equal k and 33.3% exceed it
by one or two (a few "at most" bounds by more, all right). Of the 2,663 exact
statements above k, exactly half are wrong (51.0% of those one above, 49.0% of
those two above) and half are right statements of decisive months, which are
lures; of the 7,063 exact statements equal to k, 13.3% are wrong, and every
such wrong count is a lure. No statement is below k. The parity of N carries
nothing (lure share 19.5% for even N, 21.1% for odd N; wrong share 19.3% and
21.4%). The one place where a cited-sheets reader can beat chance is
structural (see the cues below).

Matched controls. Every wording that can carry a lure ("was N", "was at most
N") also occurs in the same reports on a month that cannot be altered and that
the reader and the key agree on; the generator redraws a dossier that lacks one.

The family also varies, independently of every label, the ceiling sentence (three
wordings), the order of the two paragraphs of a visit, the day of the visit
(14--24), the reading times (four sets) and one closing sentence (four
wordings, of different length). Exceeding readings are placed at any of the three
times (30% of exceeding sites have a normal first reading) and by 1--70 Pa; other
readings are 1--90 Pa below the ceiling.

R3 over seeds 1--300: 102/300 (34%) dossiers hold no lure. A report holds at
most two lure claims.

R9 over seeds 1--100, twelve dossiers per seed, counting both reports:
echo rows min/median/max = 18/28/46. All 2,957 echoes are definite: 1,673
supported-to-refuted and 1,284 refuted-to-supported; zero are undetermined.

Guessers (single-feature lookup and numeric threshold, fitted on seeds 1--500 and
scored on 501--1000, 1,000 reports and 6,000 claims; best accuracy against base
rate), with every feature a reviewer sees of a report, its title and citation
lines included, and then also source filenames and byte/word/normalised lengths:
altered report 52.9%/50.0%; dossier holds a lure 67.7%/67.6%; wrong claim
79.58%/79.58%; lure claim 79.35%/79.35%. Per-file length rule (length minus the
shortest length that file name shows in seeds 1--500, combined with the
statement form, scored on 501--1000): altered 50.0%/50.0%, dossier lure
67.6%/67.6%, wrong 79.58%/79.58%, lure 79.35%/79.35%.

Cited-sheets reader (reads only the four cited sheets and computes the flawed
reader's verdicts itself; same seeds). Per claim it is at base: "wrong claim" and
"lure claim" by N-k, by form and N-k, by form, N-k and k, by those and the
presence of equality readings, and by N minus the equality-reading and
high-reading counts give 79.35%--79.67% against 79.58% and 79.35%--79.67%
against 79.35%; no stated count is below k; the rule "refuted if N is below k,
otherwise supported" scores 79.77%, the same as the flawed reader (79.77%), and
avoids 54.8% of the lures (1,331 of 2,427). The old build gave 86.0% against
80.1% by the first method, and 86.3% against 81.0% and 81.7% of lures avoided
by the rule. Three limits are not at base, with their sizes (all structural;
see the cues below): "dossier holds a lure" 83.6% against 67.6% at best;
"altered report" 66.4% against 50.0% at best, none of it from counts below k;
and the per-claim rules gain 5.0 to 5.3 points (84.6%) when they also know how
many exact statements in the same report exceed k.

Cues that remain, with their measured size:
- A reader of the cited sheets can compute the flawed reader's verdicts itself.
  In a correct report of a dossier without a lure every exact count equals k,
  and in a correct report of a dossier with a lure at least one exceeds k (97.3%
  of such reports): a right claim that is a lure says something the cited
  sheets do not show, so this cannot be removed while a right claim can be a
  lure (R2). The rule "all exact counts equal k means no lure" scores 76.7%
  against 67.6% (83.8% against 69.8% on the old build), and counting the exact
  statements above k scores 83.6%. "Altered report" by the same counts scores
  60.7% (all-equal rule), 66.4% (count rule) and 52.9% (report-only guessers):
  the mixed report holds the wrong exact counts, all above k.
- Report context: a mixed report of a dossier without a lure has three altered
  months, all exact counts above k, while no other report has more than two.
  The 140 reports (7%) with three such counts have all three wrong (420 claims,
  3.5%), and a report with exactly one such count has it wrong in 67% (322 of
  477). Knowing the number of such counts lifts the per-claim rules from 79.6%
  to 84.6% (83.5% against 81.8% without the three-count reports).
- The mean of the six stated counts is 0.2 higher in a mixed report than in a
  correct one; the best report-only guesser reaches 52.9% against 50%.
- An exceeding site with two high readings is present on a cited sheet in 7.4%
  of months, and a "counts_readings_not_sites" count matches it (such a count
  is wrong in 35% of the 201 claims where it matches); the effect on the
  cited-sheets guessers above is under 0.4 points.
- "At most N" statements are wrong in 6.9% of cases against 23.3% for exact
  statements, and an "at most" bound above k is never wrong (1,331 of 1,331 are
  right); "at most 3" and "at most 4" (5.1% of claims) are right in every
  dossier. Accuracy against the majority rate cannot see this.
- "Was at most 0" and "was 0" are not how a reviewer writes; the sentences are
  uniform instead.

Departures from the design sketch: 40--120 files follows the task brief instead
of its 60--200; the reader uses exactly the cited union, without an extra
sample. The sketch's "at least N" and "highest" statements are not implemented;
"at most N" was added, and zero counts are written as numbers, not as a separate
"no site" sentence. A wrong count below the cited count was dropped, together
with the slip "reads_first_measurement_only", because the cited sheets alone
refute it.

Limits: all readings are complete, exact, integer pressures in pascals; there
are no missing sheets, uncertain readings, OCR errors or undetermined verdicts.
The six months, organization and inspection purpose do not vary. The sample is
four sheets for every claim and every report. Counts range only from 0 to 4.
A site's ceiling does not change within the six months. The settling rules
define exceedance during the recorded inspections, not between them; the claims
explicitly use that scope. No rule sentence is known to have a second reading,
but two parsers cannot establish that humans agree. Finite guesser checks do
not exclude stronger statistical cues. A script which parses every sheet finds
every exception; the family measures whether the auditor reads beyond what the
report points to. It stops separating readers when every reader actually checks
all files or always follows the scope rule.
"""

from generate import claim, document, section


MONTHS = ('April', 'May', 'June', 'July', 'August', 'September')
RULES = (
    'The inspection register consists of every numbered site sheet supplied in this packet.',
    'Each sheet records all three inspection readings and the applicable operating ceiling for each month shown.',
    "A site exceeded its operating ceiling in a month if and only if at least one of its three readings for that month is strictly greater than that month's ceiling on its own sheet.",
    'A reading equal to the ceiling is not an exceedance.',
    'A monthly count of sites exceeding their ceilings counts each site once, even when several readings on that site exceed its ceiling.',
    'A statement about sites in a month ranges over every site in the register, not just the sites cited beside the statement.',
    "A finding that no site exceeded its ceiling requires checking every site's sheet for that month.",
    "Citations are navigation aids and do not restrict the register's scope.",
)

# Drawing weights. Each tuple is sampled uniformly, so a repeated entry is a weight.
HIDDEN_MONTHS = (0, 0, 0, 2, 2, 2, 2, 2, 2, 2)      # months with a decisive uncited sheet
KINDS = ('exact', 'exact', 'exact', 'exact', 'bound')   # how a month's statement counts
CITED_EXCEPTIONS = (0, 0, 1, 1, 1, 2, 2)   # exceeding sites among the four cited sheets
UNCITED_EXCEPTIONS = (1, 2)                           # exceeding sites among the uncited sheets of a hidden month
EQUALITY_SITES = (1, 2)                               # sites with a reading exactly at the ceiling
MULTI_READING = 0.08                                  # chance that an exceeding site has a second high reading
LATER_ONLY = 0.3                                      # chance that an exceeding site has a normal first reading
BOUND_SLACK = (0, 0, 1, 1, 2)                         # how far above the true count an "at most" statement sits
ALTERABLE = 3                                         # months in which a mixed report can carry a slip
VISIT_TIMES = (('08:00', '12:00', '16:00'), ('07:30', '11:30', '15:30'),
               ('09:00', '13:00', '17:00'), ('08:30', '12:30', '16:30'))
VISIT_NOTES = (
    'The access visit also included a visual inspection of the cabinet seal; '
    'the pressure record was transcribed before the enclosure was closed.',
    'The filter cabinet was opened only after the readings had been taken, '
    'and the rest of the access visit was routine.',
    'The caretaker found the seal and the filter housing as at the previous visit.',
    'The visit was combined with the monthly check of the fan belts, which was done after the readings.',
)
MAX_COUNT = 4     # no month has more exceeding sites than this
MAX_EXCESS = 2    # a wrong count in an ordinary month exceeds the true count by one or two


def exceeds(world, month, sites=None):
    """Count sites, rather than individual readings, using a strict comparison."""
    return sum(max(readings) > ceiling
               for site, (readings, ceiling) in enumerate(world['months'][month])
               if sites is None or site in sites)


def statement(month, kind, asserted):
    bound = 'at most ' if kind == 'bound' else ''
    return (f'During the recorded {MONTHS[month]} 2034 inspections, the number of sites '
            f'that exceeded their operating ceilings was {bound}{asserted}.')


def true_count(base, month, plan):
    """The count a month's true statement asserts."""
    true = exceeds(base, month)
    return min(true + plan['slack'], MAX_COUNT) if plan['kind'] == 'bound' else true


def draw_register(rng):
    """The sites, the readings and, for each month, the facts the claims are drawn from."""
    size = rng.randint(40, 120)
    cited = sorted(rng.sample(range(size), 4))
    uncited = [site for site in range(size) if site not in cited]
    ceilings = [rng.randint(300, 700) for _ in range(size)]
    hidden = set(rng.sample(range(len(MONTHS)), rng.choice(HIDDEN_MONTHS)))
    months, plans = [], []
    for month in range(len(MONTHS)):
        kind = rng.choice(KINDS)
        cited_exceptions = rng.choice(CITED_EXCEPTIONS)
        exceptional = rng.sample(cited, cited_exceptions)
        if month in hidden:
            exceptional += rng.sample(uncited, rng.choice(UNCITED_EXCEPTIONS))
        equal = rng.sample([site for site in uncited if site not in exceptional], rng.choice(EQUALITY_SITES))
        # Each exceeding site is drawn alike, whether or not its sheet is cited.
        multi = {site for site in exceptional if rng.random() < MULTI_READING}
        later = {site for site in exceptional if rng.random() < LATER_ONLY}
        measurements = []
        for site in range(size):
            ceiling = ceilings[site]
            readings = [ceiling - rng.randint(1, 90) for _ in range(3)]
            if site in exceptional:
                if site in later:
                    high = {1, 2} if site in multi else {rng.choice((1, 2))}
                else:
                    high = {0, rng.choice((1, 2))} if site in multi else {0}
                for position in high:
                    readings[position] = ceiling + rng.randint(1, 70)
            elif site in equal:
                readings[rng.randrange(3)] = ceiling
            measurements.append((tuple(readings), ceiling))
        months.append(measurements)
        plans.append({'kind': kind, 'slack': rng.choice(BOUND_SLACK)})
    return size, cited, hidden, {'months': months}, plans


def slips(base, month, cited):
    """What each named slip would count in a month, in the order they are offered."""
    measurements = base['months'][month]
    return (('ignores_uncited_sheets', exceeds(base, month, cited)),
            ('counts_equality_as_exceedance', sum(max(readings) >= ceiling for readings, ceiling in measurements)),
            ('counts_readings_not_sites', sum(sum(value > ceiling for value in readings)
                                              for readings, ceiling in measurements)))


def offered(base, month, kind, cited):
    """The wrong counts a slip yields in a month, never below what the cited sheets show.

    A count below the cited count would be refuted by the cited sheets alone, so it is never offered.
    A month with a decisive uncited sheet offers only the count of the cited sheets (the lure); any
    other month offers a count one or two above the true one.
    """
    true, known = exceeds(base, month), exceeds(base, month, cited)
    options, seen = [], set()
    for error, value in slips(base, month, cited):
        if true > known:
            wrong = value == known
        else:
            wrong = kind == 'exact' and known < value <= min(known + MAX_EXCESS, MAX_COUNT)
        if wrong and value not in seen:
            seen.add(value)
            options.append((error, value))
    return options


def build(rng):
    while True:
        size, cited, hidden, base, plans = draw_register(rng)
        choices = [offered(base, month, plan['kind'], cited) for month, plan in enumerate(plans)]
        # Exactly this many months can be altered in the mixed report: every month with a decisive
        # uncited sheet, and otherwise months with a plain count claim, drawn without regard to citations.
        plain = [month for month, options in enumerate(choices)
                 if options and month not in hidden and plans[month]['kind'] == 'exact']
        needed = ALTERABLE - len(hidden)
        if len(plain) < needed:
            continue
        keep = set(hidden) | set(rng.sample(plain, needed))
        # Every wording that can carry a lure must also occur on a month that cannot be altered,
        # where the reader and the key agree: that month's statement is the matched control.
        if {plans[month]['kind'] for month in hidden} <= {plans[month]['kind'] for month in range(len(MONTHS))
                                                          if month not in keep}:
            break
    choices = [options if month in keep else [] for month, options in enumerate(choices)]

    # Complete an unread sheet with readings at its ceiling. Only the resulting
    # no-exceedance fact matters to any predicate; no unknown is asserted open.
    told_base = {'months': [
        [(readings, ceiling) if site in cited else ((ceiling,) * 3, ceiling)
         for site, (readings, ceiling) in enumerate(measurements)]
        for measurements in base['months']]}
    sources = {'procedure.md': document('Inspection register procedure', [
        section('P-01', 'Scope and comparison',
                'Merehaven Estates, Maintenance Assurance Office. Issued 02 April 2034.\n\n'
                + ' '.join(RULES)),
        section('P-02', 'Circulation and use',
                'Added by the records coordinator on 02 October 2034 for the autumn maintenance meeting. '
                'The sheet references listed beside each statement in the review are the returns for which '
                'the plant caretaker telephoned the office during the six months. A caretaker does not '
                'telephone for every reading above a ceiling, so the list shows where the office was '
                'already alerted; it is not an index of findings. The office keeps the individual site '
                'returns as separate files because each caretaker receives only the return for the plant '
                'under their care. For the meeting, the records team brought all of those returns together in '
                'this packet. The telephone list was useful when preparing the review, but the meeting '
                'needs a finding about the full register.\n\n'
                'The measurements concern ventilation plant, not the quality of occupied-room air. '
                'Care staff use the record to plan filter purchases and access visits. Different installations '
                'have different operating ceilings; a high absolute pressure at one installation does not '
                'by itself establish a maintenance exception at that installation. The site return keeps '
                'the ceiling beside the visit account so that equipment with a different setting can be '
                'reviewed without relying on a remembered estate-wide figure. The monthly review covers '
                'the three recorded inspection readings only. It makes no statement about pressure between '
                'visits. Follow-up work, if needed, is arranged separately and is not recorded as another '
                'inspection reading in this register.')])}

    for site in range(size):
        parts = [section('S-01', 'Custody and purpose',
                         f'Merehaven Estates, Site Services Office. Site {site + 1:03d}. '
                         'Return closed 30 September 2034 by the duty plant caretaker. '
                         'This sheet accompanies the maintenance access request for the next quarter. '
                         'The inspection port is inside the plant enclosure; staff read the gauge after '
                         'the fan has settled and before opening the filter cabinet. The readings below '
                         'are retained for the assurance meeting as well as for the caretaker taking '
                         'over the next shift. Ceiling settings belong to this installation and are '
                         'copied from its commissioning card into each visit account. The card has not '
                         'been reissued since commissioning, so the same ceiling applies in every month.')]
        for month, name in enumerate(MONTHS):
            readings, ceiling = base['months'][month][site]
            a, b, c = readings
            ceiling_text = rng.choice((
                f'The operating ceiling for {name} was {ceiling} Pa, as recorded on the commissioning card.',
                f'On the commissioning card, the operating ceiling for {name} was recorded as {ceiling} Pa.',
                f'The commissioning card recorded {ceiling} Pa as the operating ceiling in force in {name}.',
            ))
            first, second, third = rng.choice(VISIT_TIMES)
            record = (f'On {rng.randint(14, 24)} {name} 2034, the pressure readings at {first}, {second} and {third} '
                      f'were {a} Pa, {b} Pa and {c} Pa respectively. '
                      'The caretaker took these readings at the same port with the plant running. '
                      + rng.choice(VISIT_NOTES))
            body = ceiling_text + '\n\n' + record if rng.choice((False, True)) else record + '\n\n' + ceiling_text
            parts.append(section(f'M-{month + 4:02d}', name + ' visit', body))
        sources[f'sheet-{site + 1:03d}.md'] = document(f'Site {site + 1:03d} inspection return', parts)

    def finding(month, kind, asserted, error=None):
        text = statement(month, kind, asserted)
        anchor = f'M-{month + 4:02d}'
        cites = [('procedure.md', 'P-01')] + [(f'sheet-{site + 1:03d}.md', anchor) for site in cited]
        if kind == 'bound':
            holds = lambda world: exceeds(world, month) <= asserted
        else:
            holds = lambda world: exceeds(world, month) == asserted
        item = claim('register_exceedance_count', text, holds, cites,
                     quantity=lambda world: int(exceeds(world, month)), stated=int(asserted), error=error)
        # Evidence is the full register, even where the report's sample omits it.
        item['anchors'] = [('procedure.md', 'P-01')] + [
            (f'sheet-{site + 1:03d}.md', anchor) for site in range(size)]
        return item

    slots = []
    for month, plan in enumerate(plans):
        kind, asserted = plan['kind'], true_count(base, month, plan)
        slots.append((finding(month, kind, asserted),
                      [finding(month, kind, value, error) for error, value in choices[month]]))
    return {'title': 'Monthly ventilation inspection review', 'sources': sources,
            'base': base, 'unknowns': {}, 'slots': slots,
            'told': {'base': told_base, 'unknowns': {}} if hidden else None,
            'reader': 'reads_cited_files_only'}
