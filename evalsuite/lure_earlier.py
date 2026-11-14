"""An earlier accounts review with findings that must be checked against records.

The reader ``follows_earlier_review`` adopts every finding and every figure in
the review memo for the points the memo covers: its reconciliation finding, the
variance it computed and the schedule total it compared with (so the reader
also takes the memo's total as the authorised total of a programme). For other facts it uses the
records. A mistaken memo uses the first schedule issue, which the signed
authorisation later replaced, instead of the issue named as operative; its
working-paper sentence names the issue it used, which makes the cause
recoverable. The memo is dated later than every other document. All amounts
and findings are computed, including the ordinary error made by using the
replaced issue when there is no misleading memo.

Reviewed rule sentences, reproduced verbatim in procedure.md:
    A review memo records its author's conclusions.
    A review memo is not a record of the facts it discusses.
    Those facts are established only by the payment records and signed schedule authorisations.
    For each programme, the schedule issue named as operative in its signed authorisation is the authorised schedule for the quarter.
    Q3 disbursements are the sum of the three bank-settled payments recorded for that programme.
    Disbursements reconcile to the authorised schedule if and only if that sum equals the total in the operative schedule issue.
    The disbursement variance is the absolute difference between that sum and the operative schedule total.
    A replaced schedule issue has no effect on that comparison.
    No payment, reversal or schedule authorisation outside the supplied records forms part of this quarter's comparison.

One rule sentence changed in the review round (finding 4 of the independent
review). It read "An earlier schedule issue has no effect on that comparison."
and could be taken to mean the issue with the earlier letter, when the first
issue of a record is A in half the records and B in the other half. It now
reads "A replaced schedule issue has no effect on that comparison." The first
issue's letter stays random, because that is what keeps the memo's letter
uninformative on its own. The other eight sentences are unchanged.

Design, as decided by the coordinator after the review (this replaces the
earlier design of a memo in 70% of dossiers that is wrong in half of them).
A review memo is among the sources in about 85% of dossiers, and in about 80%
of those it is wrong on one or two of its eight points (two in 90% of such
dossiers, one in the rest), so that about three dossiers in ten hold no lure,
as R3 asks, and a campaign of twelve dossiers reliably holds lures. Inside a
dossier the memo judges most points rightly, and both its findings and its
figures can be right or wrong; it can be neither trusted nor dismissed unread.
Lures run in both directions: a true claim that the memo contradicts, and a
wrong claim (the figure the replaced issue gives) that the memo confirms.

Seeds 1 through 300, index 1 (test_matched_controls_and_frequencies): 254 of
300 dossiers hold a memo (84.7%); 213 of those 254 hold a wrong one (83.9%);
87 of 300 (29.0%) hold no lure; the memo is wrong on two points in 190
dossiers and on one point in 23.

R9, seeds 1 through 100 (the range of the common brief), indexes 1 through 12
per seed: minimum 16, median 32, maximum 48 lure rows per twelve dossiers, 3,178
in all.

R9, seeds 1 through 300, indexes 1 through 12 per seed (3,600 dossiers): lure
rows (key rows with an echo_state) per twelve dossiers have minimum 14, median
32, maximum 48. All 9,468 lure rows are definite, none undetermined:
7,473 supported-to-refuted and 1,995 refuted-to-supported; 4,672 are on
variance claims and 4,796 on authorised-total claims. Of the 3,600 dossiers,
3,080 hold a memo (85.6%), 2,505 hold a lure (69.6%) and 1,095 (30.4%) hold
none; 2,229 hold two wrong points, 276 one.

Claims. Each report holds eight claims, one per programme: four state the
Q3 disbursement variance of a programme ("is N euros") and four state its
authorised schedule total ("is N euros"), in a random order that is the same
in both reports of a dossier. A wrong claim states the value that the replaced
issue gives. Each kind has one wording shape, so a lure claim always has
controls of its own kind and shape in the same report: at most two of the four
claims of a kind can be lures. Every claim cites the same five passages (the
memo rule P-02, the comparison rule P-03 and the three sections of the
programme's record), never the review memo. A yes-or-no claim ("reconciles" and "does not reconcile") was
tried in simulation and not used: because its wording flips when it is
altered, 17.6% of its lure claims would have had no control of the same
wording in the same report.

The records vary independently of the memo and of the truth of every claim:
eight of twelve programmes in a random order, the settlement, transcription,
sending, signing and closing dates, two staff names, and one of two or three
wordings for each ordinary sentence. Both schedule copies, the first and the
signed one, are said in every record to have gone to the accounts review
office, which gives the memo author an ordinary route to the replaced issue
and to the operative one, and no record mentions a memo.

Remaining measured cues (all in tests of this module):
- A review memo among the file names is necessary for a lure: with the file
  list in view, "memo present" predicts "this dossier holds a lure" with
  0.8340 accuracy against a base rate of 0.6853 (held-out seeds 301 through
  1800, two reports each). This is the memo's own presence and cannot be
  removed. The test lets no guesser beat this rule by more than 0.03. Inside
  dossiers that hold a memo, the best guesser reaches 0.8054 against 0.8050.
  The procedure says the same in every dossier, memo or not, because it is a
  general rule; its section on review memoranda is therefore no cue.
- Report-only guessers (token, polarity, amount-pattern and length shortcuts,
  trained on seeds 1 through 300 with all of a dossier in one partition, scored
  on 301 through 1800), best accuracy against the base rate: altered report
  0.5013 / 0.5000, wrong claim 0.7968 / 0.7968, lure claim 0.8363 / 0.8363,
  dossier lure 0.6860 / 0.6853. Adding file names and per-file lengths changes
  none of them except the dossier-lure rule above.
- Per-file lengths normalised per file name (shortest length in seeds 1 through
  500 subtracted), with the claim's own record and its polarity, trained on
  1 through 500 and scored on 501 through 1000 (8,000 claims): wrong claim
  0.7975 / 0.7975, lure claim 0.8330 / 0.8330, altered report 0.5054 / 0.5000,
  dossier lure 0.8440 against the memo-presence rule's 0.8460 (base 0.7020).
  Record lengths now vary by a standard deviation of about 21 bytes (2,259 to
  2,398), so an accuracy rule cannot see a shift smaller than that spread; the
  masked-sentence test compares mean lengths between groups and flags a
  difference of 4.5 standard errors: 2.8 bytes between records the memo
  misjudges and records it judges rightly, 3.7 bytes against records of
  memo-less dossiers (seeds 1 through 1000).
- Masked sentences (names, numbers and dates masked), seeds 1 through 1000:
  each sentence occurs in records the memo misjudges (1,339), records it judges
  rightly (5,477) and records of memo-less dossiers (1,184) at the same rate,
  with identical line and sentence counts; largest standardised difference
  2.57. The same holds for memo entries on wrong points (1,339) and right
  points (5,477): 0.89.
- Rates of lure and of wrong across every visible claim feature (programme,
  record number cited, position, kind, stated value including zero), compared with the rate expected
  when the flagged claims are a random subset of the report, seeds 1 through
  1000: largest standardised difference 2.21 for lures (both reports), 2.37
  for wrong claims in mixed reports, and 2.21 for lures against wrongness.
- The records of one dossier still share sentences: the mean share of masked
  sentences common to two records (intersection over union, headings included)
  is 0.42, against 0.71 before this round, when eight records differed in
  about four of 29 sentences.

Limits: these are fictional office records, not a measurement of agent
behaviour. Two claim kinds are varied, both about one schedule comparison; there
are no missing records, open facts, tax rules, rounding, partial memo coverage
or forged records. Dates are fictional and not checked against a calendar: about
30% of the dates in the records fall on a Saturday or Sunday, including some
bank settlements and signatures. The quarter, the trust, its offices, the accounts manager and
the review author are fixed, and every dossier holds exactly eight programmes. The replaced issue always gives the opposite reconciliation
finding and a different total from the operative issue, which a reader can
exploit after reading the sources, and dismissing the lure takes one comparison
of the memo's issue letter with the operative letter. Memo presence is visible
in the file list (see above). The two reports must never be shown together:
their differences identify errors. A careful human could use a broader everyday
meaning of "reconcile", but the procedure expressly defines it as equality for
this comparison. The key relies on treating the supplied bank records and
signed authorisations as authentic and complete. An unrecorded reversal or an
overriding authorisation would break that assumption. A reviewer who recomputes
the totals and reads the operative issue selection defeats the lure; this
family may saturate under read-all prompts.
"""

import random

import generate
from generate import claim, document, section


# Twelve programmes; each dossier draws eight of them in a random order.
PROGRAMMES = (
    ('Riverside Meals', 'meal preparation and delivery to the district day centres',
     'Kitchen coordinators submitted delivery sheets with the monthly invoices. '
     'The July consignment included insulated carriers as well as ingredients.'),
    ('Eastgate Transport', 'accessible transport between neighbourhood centres',
     'Route supervisors attached mileage books to the monthly service certificates. '
     'The August journeys included additional trips during the station closure.'),
    ('Weavers Lane Workshops', 'practical classes at the community learning rooms',
     'Tutors attached attendance sheets to the monthly materials requests. '
     'The September delivery included replacement benches for the evening classes.'),
    ('Harbour Housing Repairs', 'repairs to the trust housing stock before winter',
     'Property officers attached completion photographs to the contractors invoices. '
     'The July work included access ramps at the courtyard entrances.'),
    ('Millpond Gardens', 'maintenance of shared gardens beside the neighbourhood halls',
     'Grounds staff attached delivery tickets to the monthly nursery invoices. '
     'The August order included irrigation fittings for the raised beds.'),
    ('Northfield Reading Rooms', 'stock renewal and outreach at the branch reading rooms',
     'Branch supervisors attached accession lists to the monthly supplier invoices. '
     'The September order included large print editions for the mobile collection.'),
    ('St Aldric Clinics', 'equipment servicing at the community treatment rooms',
     'Clinic administrators attached service sheets to the monthly maintenance invoices. '
     'The July visit included replacement seals for the sterilisation equipment.'),
    ('Kingsway Training', 'short employment courses in the neighbourhood learning centres',
     'Course administrators attached session registers to the monthly trainer invoices. '
     'The August timetable included extra evening sessions for shift workers.'),
    ('Copperfield Childcare', 'holiday play sessions at the neighbourhood halls',
     'Play leaders attached sign-in sheets to the monthly staffing invoices. '
     'The August programme included minibus hire for the harbour trip.'),
    ('Larkhill Advice', 'drop-in debt and benefits advice at the library annexes',
     'Advisers attached caseload summaries to the monthly sessional fee claims. '
     'The July claims included interpreter fees for the evening sessions.'),
    ('Tanner Street Pantry', 'weekly food parcels for registered households',
     'Pantry volunteers attached stock sheets to the monthly wholesaler invoices. '
     'The September order included chilled storage for the new unit.'),
    ('Ashgrove Day Centre', 'daytime care and activities for older residents',
     'Centre staff attached attendance registers to the monthly catering invoices. '
     'The August invoices included the cost of the summer outing.'),
)

ACCOUNTANTS = ('Ivo Pell', 'Tamsin Hurley', 'Dale Orrin')
CLERKS = ('Nessa Reed', 'Colm Ardley', 'Priya Vasey')

# Ordinary sentences of a record. Each record draws one variant of each,
# independently of everything else in the dossier.
COVERING = (
    'The covering letter listed the completed service bundles and the allocation headings '
    'against which invoices had been filed.',
    'The covering letter set out the service bundles completed in the quarter and the '
    'allocation headings used for filing the invoices.',
    'The letter that came with it listed the service bundles delivered and the headings '
    'under which the invoices were filed.')
FIRST_COPY = (
    'A copy of that schedule went to the accounts review office when it was issued.',
    'The accounts review office received a copy of that issue on the day it was circulated.',
    'A further copy was sent to the accounts review office for its working files.')
FOLIO_NOTE = (
    'The desk copy remains in this folio with the correspondence that accompanied it.',
    'The desk copy has stayed in this folio with its covering letter.',
    'This folio retains the desk copy together with the letter that accompanied it.')
CONFIRMATIONS = (
    'The payee confirmations carry the same settlement dates.',
    'The payee confirmations on file show the same settlement dates.')
BUNDLES = (
    'Each payment covered the monthly bundle of invoices filed behind its service certificate; '
    'the bundles were kept separate during the transfer run.',
    'Each payment settled one monthly bundle of invoices, filed behind its service certificate, '
    'and the transfer run kept the bundles apart.')
RETURNS = (
    'Treasury returned the statement copies to the programme accountant after matching the '
    'transfer references to the invoice folders.',
    'The statement copies went back to the programme accountant once Treasury had matched the '
    'transfer references to the invoice folders.',
    'Treasury matched the transfer references to the invoice folders and then handed the '
    'statement copies back to the programme accountant.')
LETTERS = (
    'The signed letter records the committee allocation following the completion certificates '
    'received from the service teams.',
    'The signed letter records the allocation agreed by the committee, with reference to the '
    'completion certificates from the service teams.',
    'In the signed letter the committee allocation follows the completion certificates sent in '
    'by the service teams.')
SIGNED_COPIES = (
    'A copy of the signed letter went to the accounts review office on the same afternoon, and '
    'a duplicate was placed in the accounts office correspondence folder.',
    'The accounts review office received a copy of the signed letter that afternoon; a duplicate '
    'went into the accounts office correspondence folder.',
    'A duplicate of the signed letter was placed in the accounts office correspondence folder on '
    'the same afternoon, and the accounts review office was sent a copy.')
FILED = (
    'The programme accountant filed the signed copy behind the settlement evidence so the payment '
    'trail and the committee allocation could be retrieved together for the quarterly meeting.',
    'The signed copy was filed by the programme accountant behind the settlement evidence, so that '
    'the payments and the committee allocation can be retrieved together for the quarterly meeting.')
MEMO_TAILS = (
    'My working paper uses the issue {issue} attachment from the programme correspondence and the '
    'three bank settlements listed in the payment folio.',
    'The working paper cites the issue {issue} attachment found in the programme correspondence and '
    'the three bank settlements listed in the payment folio.',
    'I took the issue {issue} attachment from the programme correspondence and the three bank '
    'settlements listed in the payment folio.')


def procedure():
    return document('Quarterly accounts procedure', [
        section('P-01', 'Administration',
                'Merehaven Community Trust, Finance Office. Issued 2034-10-01 by '
                'Mara Venn, accounts manager. This procedure defines the quarterly '
                'comparison used in the programme accounts pack. It accompanies '
                'the payment folios and schedule authorisations for the accounts '
                'committee. Amounts in this pack are expressed in whole euros.'),
        section('P-02', 'Review memoranda',
                "A review memo records its author's conclusions. "
                'A review memo is not a record of the facts it discusses. '
                'Those facts are established only by the payment records and signed schedule authorisations.'),
        section('P-03', 'Quarterly comparison',
                'For each programme, the schedule issue named as operative in its signed authorisation is '
                'the authorised schedule for the quarter. '
                'Q3 disbursements are the sum of the three bank-settled payments recorded for that programme. '
                'Disbursements reconcile to the authorised schedule if and only if that sum equals the total '
                'in the operative schedule issue. '
                'The disbursement variance is the absolute difference between that sum and the operative schedule total. '
                'A replaced schedule issue has no effect on that comparison. '
                "No payment, reversal or schedule authorisation outside the supplied records forms part of this quarter's comparison.")])


def record(number, name, purpose, detail, payments, original, operative, first, draw, width=2):
    """A payment folio retains the first issue and the signed replacement.

    `draw` holds the dates, the staff and the ordinary-sentence variants of this record.
    The record number is written with `width` digits."""
    second = 'B' if first == 'A' else 'A'
    d = draw
    return document(name + ' programme account', [
        section(f'R{number:0{width}d}-S', 'Schedule correspondence',
                'Merehaven Community Trust, Programme Accounts Office. File closed '
                f'{d["closed"]} by {d["accountant"]}, programme accountant. This folio brings '
                f'together the Q3 payment evidence for the {name} programme, which '
                f'funds {purpose}. {detail}\n\n'
                f'The schedule sent to the accounts desk on {d["sent"]} was issue {first}, '
                f'with a quarter total of {original} euros. {d["covering"]} {d["first_copy"]} '
                f'{d["folio_note"]}'),
        section(f'R{number:0{width}d}-P', 'Bank settlement evidence',
                f'The bank settled the July payment of {payments[0]} euros on {d["july"]}. '
                f'The bank settled the August payment of {payments[1]} euros on {d["august"]}. '
                f'The bank settled the September payment of {payments[2]} euros on {d["september"]}. '
                f'These entries were transcribed from the trust account statements '
                f'by {d["clerk"]} in Treasury on {d["transcribed"]}. {d["confirmation"]} '
                f'{d["bundle"]} {d["returned"]}'),
        section(f'R{number:0{width}d}-A', 'Signed schedule authorisation',
                f'On {d["signed"]} Mara Venn signed schedule issue {second} for the {name} programme. '
                f'The authorised quarter total in issue {second} is {operative} euros. '
                f'Issue {second} is operative for Q3 and replaces issue {first}. '
                f'{d["letter"]} {d["signed_copy"]} {d["filed"]}')])


FINDINGS = "The findings below state whether each programme's Q3 disbursements reconcile to its authorised schedule. "
# Where the memo covers only some programmes of a larger dossier, it says which.
SCALED_FINDINGS = ('The findings below state, for each programme I reviewed, whether its Q3 disbursements '
                   'reconcile to its authorised schedule. ')


def review(entries, date, width=2, findings=FINDINGS):
    """Both conclusion phrases have equal length; no padding or hidden markers."""
    sections = [section(f'V-{1:0{width}d}', 'Committee preparation',
                        'Merehaven Community Trust, Accounts Review Office. '
                        f'{date}. Prepared by Soren Vale for the quarterly '
                        'accounts committee. I compared the Q3 disbursements '
                        'with the schedule copies in my working folder. '
                        + findings +
                        'The following findings give the amounts compared and the '
                        'schedule issue used, so the committee secretary can '
                        'retrieve the corresponding working paper. The folder '
                        'contains copies from the programme accounts circulation; '
                        'it is filed alongside this note for the meeting.')]
    for number, name, paid, scheduled, issue, tail in entries:
        finding = 'No variance found.' if paid == scheduled else 'One variance seen.'
        sections.append(section(f'V-{number + 1:0{width}d}', name,
                                f'{finding} Q3 disbursements for the {name} programme: '
                                f'{paid} euros against {scheduled} euros in schedule issue {issue}. '
                                + tail.format(issue=issue)))
    return document('Accounts review', sections)


def day(rng, month, low, high):
    return f'2034-{month:02d}-{rng.randint(low, high):02d}'


def draw_programme(rng, number, name, purpose, detail, is_changed, kind, width=2):
    """Every draw of one programme, in the order of the scale-1 recipe: its record, the facts of its
    world, the facts the memo would state and its claim slot."""
    payments = [rng.randrange(100000, 190000) for _ in range(3)]
    paid = sum(payments)
    reconciles = rng.random() < 0.5
    difference = rng.choice((-7500, -3600, -1200, 1200, 3600, 7500))
    operative = paid if reconciles else paid + difference
    original = paid + difference if reconciles else paid
    first = rng.choice(('A', 'B'))
    second = 'B' if first == 'A' else 'A'
    draw = {'july': day(rng, 7, 22, 31), 'august': day(rng, 8, 22, 31), 'september': day(rng, 9, 22, 30),
            'transcribed': day(rng, 10, 2, 8), 'sent': day(rng, 10, 5, 12),
            'signed': day(rng, 10, 14, 20), 'closed': day(rng, 10, 21, 25),
            'accountant': rng.choice(ACCOUNTANTS), 'clerk': rng.choice(CLERKS),
            'covering': rng.choice(COVERING), 'first_copy': rng.choice(FIRST_COPY),
            'folio_note': rng.choice(FOLIO_NOTE), 'confirmation': rng.choice(CONFIRMATIONS),
            'bundle': rng.choice(BUNDLES), 'returned': rng.choice(RETURNS),
            'letter': rng.choice(LETTERS), 'signed_copy': rng.choice(SIGNED_COPIES),
            'filed': rng.choice(FILED)}
    tail = rng.choice(MEMO_TAILS)
    filename = f'record-{number:0{width}d}.md'
    text = record(number, name, purpose, detail, payments, original, operative, first, draw, width)
    variance, total = f'{name}|variance', f'{name}|total'
    base = {variance: abs(paid - operative), total: operative}
    memo_amount = original if is_changed else operative
    believed = {variance: abs(paid - memo_amount), total: memo_amount}
    memo_entry = (number, name, paid, memo_amount, first if is_changed else second, tail)
    cites = [('procedure.md', 'P-02'), ('procedure.md', 'P-03')]
    cites += [(filename, f'R{number:0{width}d}-{part}') for part in ('S', 'P', 'A')]

    def about_variance(value, error=None, name=name, key=variance, cites=cites):
        return claim('programme_variance',
                     f'The Q3 disbursement variance for the {name} programme is {value} euros.',
                     lambda world, key=key, value=value: world[key] == value,
                     cites, quantity=lambda world, key=key: world[key], stated=value, error=error)

    def about_total(value, error=None, name=name, key=total, cites=cites):
        return claim('programme_total',
                     f'The authorised schedule total for the {name} programme is {value} euros.',
                     lambda world, key=key, value=value: world[key] == value,
                     cites, quantity=lambda world, key=key: world[key], stated=value, error=error)

    # The alternative is exactly the comparison using the other schedule.
    # When that is also the memo finding, name the narrower measured slip.
    error = 'follows_earlier_review' if is_changed else 'uses_superseded_schedule'
    if kind == 'variance':
        slot = (about_variance(base[variance]), [about_variance(abs(paid - original), error)])
    else:
        slot = (about_total(operative), [about_total(original, error)])
    return filename, text, base, believed, slot, memo_entry


def build(rng):
    """Use only the caller's random source; return the normal generator contract."""
    has_memo = rng.random() < 0.85
    misleading = has_memo and rng.random() < 0.8
    changed = set(rng.sample(range(8), 2 if rng.random() < 0.9 else 1)) if misleading else set()
    chosen = rng.sample(PROGRAMMES, 8)
    kinds = ['variance'] * 4 + ['total'] * 4
    rng.shuffle(kinds)
    memo_date = day(rng, 10, 26, 31)
    sources = {'procedure.md': procedure()}
    base, believed, slots, memo_entries = {}, {}, [], []
    for offset, (name, purpose, detail) in enumerate(chosen):
        filename, text, facts, memo_facts, slot, memo_entry = draw_programme(
            rng, offset + 1, name, purpose, detail, offset in changed, kinds[offset])
        sources[filename] = text
        base.update(facts)
        believed.update(memo_facts)
        slots.append(slot)
        memo_entries.append(memo_entry)
    if has_memo:
        sources['review.md'] = review(memo_entries, memo_date)
    return {'title': 'Programme disbursement review', 'sources': dict(sorted(sources.items())),
            'base': base, 'unknowns': {}, 'slots': slots,
            'told': {'base': believed, 'unknowns': {}} if changed else None,
            'reader': 'follows_earlier_review'}


# ---------- Scale: the same dossier, many blocks of eight programmes ----------

MAX_SCALE = 30

# The twelve services of PROGRAMMES (its names without the place word), in the same order.
SERVICES = ('Meals', 'Transport', 'Workshops', 'Housing Repairs', 'Gardens', 'Reading Rooms',
            'Clinics', 'Training', 'Childcare', 'Advice', 'Pantry', 'Day Centre')


def build_scaled(rng, scale):
    """`scale` blocks of eight programmes, drawn as the scale-1 recipe draws a dossier.

    A block keeps today's draws: whether a memo exists, whether it misleads, which points it changes,
    the claim kinds and every per-programme value. The names are a place and a service, dealt to the
    blocks by the dossier's random source, which also numbers all records in one shuffle, so no block
    is contiguous in the sources. One review memo covers the programmes of the blocks that drew a memo.
    A block's `told` holds what the memo says about its own programmes, so a block without a memo, or
    with a right one, leaves a claim's `echo_state` empty exactly as at scale 1."""
    if not 1 <= scale <= MAX_SCALE:
        raise generate.GenerationError(f'The earlier family needs a scale from 1 to {MAX_SCALE}')
    pool = [(f'{place} {service}', purpose, detail)
            for place in generate.PLACE_STEMS
            for service, (_, purpose, detail) in zip(SERVICES, PROGRAMMES)]
    if len(pool) < 8 * scale or len({name for name, _, _ in pool}) != len(pool):
        raise generate.GenerationError('The earlier family has too few programme names for this scale')
    memo_date = day(rng, 10, 26, 31)
    rng.shuffle(pool)
    rngs = [random.Random(rng.getrandbits(64)) for _ in range(scale)]
    places = [(block, offset) for block in range(scale) for offset in range(8)]
    rng.shuffle(places)
    number_of = {place: position + 1 for position, place in enumerate(places)}
    width = max(2, len(str(8 * scale)))
    sources = {'procedure.md': procedure()}
    blocks, memo_entries = [], []
    for block, block_rng in enumerate(rngs):
        has_memo = block_rng.random() < 0.85
        misleading = has_memo and block_rng.random() < 0.8
        changed = set(block_rng.sample(range(8), 2 if block_rng.random() < 0.9 else 1)) if misleading else set()
        kinds = ['variance'] * 4 + ['total'] * 4
        block_rng.shuffle(kinds)
        base, believed, slots = {}, {}, []
        for offset in range(8):
            name, purpose, detail = pool[8 * block + offset]
            number = number_of[(block, offset)]
            filename, text, facts, memo_facts, slot, memo_entry = draw_programme(
                block_rng, number, name, purpose, detail, offset in changed, kinds[offset], width)
            sources[filename] = text
            base.update(facts)
            believed.update(memo_facts)
            slots.append(slot)
            if has_memo:
                memo_entries.append(memo_entry)
        blocks.append({'base': base, 'unknowns': {}, 'slots': slots, 'rng': block_rng,
                       'told': {'base': believed, 'unknowns': {}} if changed else None})
    if memo_entries:
        sources['review.md'] = review(sorted(memo_entries), memo_date, width, SCALED_FINDINGS)
    return {'title': 'Programme disbursement review', 'sources': dict(sorted(sources.items())),
            'reader': 'follows_earlier_review', 'blocks': blocks}
