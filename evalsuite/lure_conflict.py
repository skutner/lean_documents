"""Conflict: choosing a disputed room hire amount by prestige or repetition.

The fictional Bellwether Civic Rooms Trust assembles a signed schedule (record A),
booking correspondence (record B), a programme desk digest (record C), the
governing agreement and a short review procedure. Six of the twelve venues have
two different room hire amounts in the records; the other six agree. For every
venue the agreement holds one clause. At a venue with two amounts it selects
record A, selects record B, or selects no entry (drawn 1 : 1 : 2, independently of
which record holds the lower amount, of the digest's copying route, of the
wording and of every amount). At a venue whose records agree it selects a record
and is irrelevant (rule 3); "no entry" is used only where the records differ.

Two flawed readers, both programs (`reader_world`) that ignore the agreement:
  higher_ranked_source_wins   takes the amount of record A, the signed schedule.
  most_copies_win             takes the amount that two of the three records
                              carry; the digest copies A or B, so this is the
                              digest's amount.
Which of the two candidates record A holds, and whether the digest copies A or B,
are drawn independently, so the readers agree with each other on about half of
the disputed venues. A reader is right where the records agree or where the
clause happens to select the amount it favours. Otherwise it is wrong: at an
unsettled venue (no entry) it gives a definite verdict where the key is
`undetermined`, and at a settled venue whose clause selects the other amount it
gives the opposite of the key (supported for refuted, refuted for supported).
The rank reader is also returned as `told`/`reader`; every dossier holds
disputed venues, so `told` is always present and `echo_state` appears on the rows
where the rank reader is wrong. The first version let both readers obey the
clause; it therefore measured unwarranted certainty and not ignoring a contract.

Rule sentences, verbatim (the whole procedure, P-01):
    The review concerns the daily room hire charge for each named venue, excluding equipment hire.
    The room hire amounts stated in records A, B and C are the exhaustive candidate values for each venue.
    If those records state the same amount for a venue, that amount is established.
    If those records state different amounts for a venue, its amount is established only when a clause in the governing agreement selects one of those records for that venue.
    Without such a clause, each stated candidate remains possible and the packet does not establish a single amount.
    The date of a record does not create precedence.
    A selected record determines the amount.
    The agreement's venue clauses are its complete list of record selections.
    Amounts for different venues may be resolved independently.
Changed in the fix round (sentences 6 and 7; the others are unchanged):
  6 was: Document titles, signatures, dates and the number of copies do not create precedence.
  6 is:  The date of a record does not create precedence.
  7 was: A selected record determines the amount even when another record looks more official or more records repeat another amount.
  7 is:  A selected record determines the amount.
The old wording named both shortcuts outright. Sentence 6 is kept for dates only,
because the five documents now carry different dates (sentence 4 already says a
differing amount needs a clause, so titles, signatures and copies need no
mention). Sentence 7 keeps the half that no other sentence gives: the established
amount is the selected record's own.

Report design: twelve claims, one per venue, in an order that build_case shuffles:
four "at most" and five "more than" comparisons, one "exactly" and two "not". Five
of the nine budget venues are disputed, and one of the two "not" venues; the other
"not" venue is the same-wording control in every report. The "exactly" claim never
concerns a disputed venue, because a correct "exactly" claim cannot hold on an
unsettled venue and would show it to be settled.
A budget slot has three kinds of statement. The open bound lies outside both
candidates and is true whatever the clause says ("at most" the higher
candidate; "more than" last season's charge). The separating bound lies between
them (the negotiation target, which is the lower candidate or a value halfway
between) and is true only if the clause selects the amount on the matching
side. The false bound lies outside both on the wrong side ("at most" last
season's charge, "more than" the equipment price) and is refuted whatever the
clause says. The correct report states the separating bound as its true claim in
13 cases in 100 where the clause makes it true (`SEPARATING_SHARE`); the rate
makes a separating bound as likely on an unsettled venue as on a settled one over
the correct and mixed reports together (tested). A disputed "not" slot has the
true claim "not" the equipment price and two mutations, "not" the lower and "not"
the higher candidate: at a settled venue the one that names the selected amount
is refuted, at an unsettled venue both are undetermined. It never states a "not
candidate" claim as true, for that would show the venue to be settled. A mutation
is a documentary substitution: the target, last season's charge or the
equipment price; for the exact claims the equipment price or an amount of the
records. A separating mutation on a disputed venue carries the error name
`settles_conflict_by_rank_or_count`, on an agreed venue `copies_negotiation_target`.
No `open_claims` reservation is used, so the plain draw of build_case alters each
slot with the same probability, whatever the venue's clause. Citations are the
same five passages in every claim.

Prose: the schedule, correspondence and digest each carry a note on every venue
that is specific to the venue; no paragraph repeats. Each document has an office,
an author and an issue date. The five dates differ and are drawn independently
of everything else: the order of the three records is any of six, equally often;
the agreement, which records a meeting about the papers, is dated after all
three. The digest says it copied from a final copy or a working draft, so that
its date may precede its source. Agreement selections use equal-length prose
('record A', 'record B', 'no entry'). File lengths differ only by one digit of a
date. Together the sources hold 2,959 prose words, 1,977 of them in distinct
sentences once venue names and amounts are masked.

Measured with generator version 4, seeds 1..100 and indexes 1..12 (tests):
  Rows with a non-empty lure_states per twelve dossiers (R9): minimum 7, median 15,
  maximum 24 (seeds 101..300: median 14.5). 1,483 rows: 144 in correct reports
  and 1,339 in mixed reports; 1,044 budget rows ("at most" 460, "more than" 584)
  and 439 "not" rows. By key and clause: 830 undetermined (all at unsettled
  venues), 227 supported and 426 refuted (all at settled venues). Rank reader:
  739 supported and 515 refuted verdicts against the key; count reader: 678
  supported and 576 refuted. Both readers are wrong on 1,025 rows, rank only on
  229, count only on 229. Per mixed report 0, 1, 2, 3, 4 lure rows in 317, 521,
  275, 80, 7 of 1,200 reports; per correct report 0, 1, 2 in 1,063, 130, 7.
  Rows without a lure (controls), by wording and report: "at most" 4,736 and
  4,404, "more than" 5,920 and 5,496, "exactly" 1,200 and 1,200, "not" 2,400 and
  1,961 (correct and mixed). 24.9% of the 1,200 dossiers hold no lure row (R3
  asked for about 30%; 22% of seeds 1..300 at index 1).
  Alternatives tried: with three disputed venues, as before, the median was 6 and
  59% of dossiers held no lure; with seven or eight (budget only) the median was
  16 and 24.5%; with seven or eight in all including a "not" slot, 18 and 19%.
Clause state against what a guesser can see (report plus the three records and
the correspondence figures, never the agreement; seeds 1..500 train, 501..1000
score):
  Settled or unsettled, for a claim on a disputed venue: the best lookup gains
  1.8 to 2.7 points over the majority guess (.4950): .5213 on claim and records,
  .5190 with the markers of an altered report, .5222 when the report variant is
  given as well (an oracle). Over both reports P(unsettled | disputed claim) is
  .498; P(unsettled | separating claim) .502 (.503 "at most", .499 "more than", .506
  "not"); P(unsettled | false bound) .39 for "at most" and .41 for "more than".
  The rule "treat every venue whose records differ as open" matches the key on
  11,659 of 12,000 rows (97.2%, because most claims do not separate the
  candidates) but on only 380 of the 721 separating claims in seeds 1..500
  (52.7%; 47.8% in seeds 501..1000): every claim it gets wrong is a settled
  separating claim.
  Source lengths, normalised per file name and fitted on seeds 1..500: at base rate
  for four or more unsettled venues (.6480), five or more (.8940) and a lure
  in the dossier (.7540 against .7560).
Report-only probes (train 1..300, hold out 301..600; Bayes and numeric stump
against majority): altered report .5100/.5067 against .5000; dossier has lure
.2000/.8000 against .8000; wrong claim .2019/.7982 against .7981; lure claim
.0546/.9454 against .9454; adding file names and lengths changes nothing.

Remaining cues and limits:
- A lure is a separating claim on a disputed venue, and a separating claim is
  readable from the claim and the records. Guessers that see the records score
  .9878 (lookup) and .9934 (rule "separating claim") for "lure claim" against
  .9471, and .9360 (mixed reports) against .7560 for "dossier holds a lure"
  (correct reports: .7560, the base rate). Both readers are functions of the
  records, so no design in which they ignore the clause can hide this; what is
  hidden is the clause. The review's wording "lure claim and dossier lure at
  base rate for such guessers" is therefore not met.
- In the mixed report P(unsettled | separating claim) is .57 (altered claims
  only), in the correct report 0 (166 such claims); pooled it is .50. The false
  bounds lean to settled (.39 and .41 unsettled) because a settled venue whose
  clause matches can only be altered into a false bound. These give the 2 to 3
  point gain above.
- A claim that copies a figure of the correspondence (target, last season's
  charge, equipment price) or denies a record amount is almost always altered (a
  lookup rule scores .9881 against .7987 for "wrong claim"; two such claims
  mark a mixed report in 500 of 500 seeds against 3 of 500 correct reports); this is
  the documentary substitution and is by design.
- A "not <candidate>" claim never appears in a correct report. Separating budget
  bounds equal the lower candidate or a value halfway (about half each); only
  "not <higher candidate>" asserts the higher one (60 of 377 lure rows in seeds
  1..300, against 178 that state the lower and 139 that lie between). The
  agreement is always dated last; the number of disputed venues is constant.
- The family does not vary missing documents, authenticity, time priority, legal
  presumptions, ties in copy counts or dependencies between venues. Whole-euro,
  single-currency, fixed template of twelve venues.
- A careful human could take "select no entry" as no selection (rule 5), and
  could wonder whether a stated negotiation target is a candidate; the
  correspondence says it is not, and rule 2 makes the candidates exhaustive.
- The procedure no longer names rank or count, but sentences 4, 5 and 7 tell a
  careful reader that only a clause establishes a differing amount, so a reader
  who follows the procedure has no use for either shortcut. The family measures
  whether auditors follow it; a ceiling result would mean it no longer separates
  auditors. No model campaign is claimed.
"""
import collections

from generate import claim, document, section

FACTS = {
    'Alder': (
        'The hall is a ground-floor room with a sprung floor that dance classes use in the mornings and small exhibitions use later in the day.',
        'The steward asked that deliveries use the side door because the front lobby is shared with a nursery group until noon. Chairs are stacked behind the stage curtain and the steward keeps the key to that store.',
        'Staff who telephone about Alder are told that the hall closes for cleaning on the first Monday of each month.'),
    'Bramble': (
        'The room lies above a library branch and is reached by a staircase and a small lift, which limits the size of display stands.',
        'The steward explained that the lift carries four people or one trolley at a time. Large stands were therefore assembled upstairs in earlier years, and the library supervisor lets the steward use the reading room for that work.',
        'Callers asking about Bramble should be reminded that the lift is not available during library fire drills.'),
    'Cedar': (
        'The venue is a converted chapel hall with fixed benches along two walls and a clear floor in the middle.',
        'The caretaker for Cedar asked for notice of any event that needs the heating on before eight in the morning. The steward noted that the benches cannot be moved, so groups needing tables must plan around the central floor.',
        'The digest notes that Cedar has no kitchen, so catering enquiries are referred to the mobile caterers on the approved list.'),
    'Dunlin': (
        'The room belongs to a harbour office building and has wide windows over the quay, with blinds that darken it for presentations.',
        'The steward reported that the quay road is closed to vans on market mornings, so deliveries to Dunlin are taken on the previous afternoon. A note on the call sheet records that the harbour office locks its car park at six.',
        'Reception staff should tell callers that Dunlin has no cloakroom; coats are kept on racks beside the entrance.'),
    'Elm': (
        'The hall is part of a community centre and is let with its stage, which can be curtained off for smaller groups.',
        'The steward at Elm said that evening events must finish by half past ten because neighbours share the car park. The call also covered the stage lights, which the centre manager will switch on only if a named person is present.',
        'The digest records that Elm shares a booking diary with the community centre, so same-day changes are confirmed by telephone.'),
    'Fennel': (
        'The room is on the first floor of a former school and has a good acoustic for talks, though its floor is uneven near the windows.',
        'The steward asked visiting groups to keep to the main stairs because the back stairs are used by the cleaners at the end of the day. The call sheet notes that parking is limited to eight cars in the old playground.',
        'Callers about Fennel are told that the room has no projector and that groups bring their own.'),
    'Grove': (
        'The venue is a pavilion in a public park with a glazed front, used in summer for markets and in autumn for training days.',
        'The park manager told the steward that the pavilion gates are opened at seven and locked again at dusk, which the steward passed on to the coordinator. Wet weather mats are kept in the garden store and can be laid on request.',
        'The digest lists Grove as step-free, with a ramp at the garden entrance.'),
    'Hazel': (
        'The room is within a hotel annexe and is let with its own entrance, so that groups need not pass through the hotel.',
        'The steward confirmed that the annexe corridor is shared with a dining room on Friday evenings, which restricts setting up between six and nine. The hotel will supply water jugs on request, and this was noted for the operations team.',
        'Staff are reminded that Hazel is the only listed room whose entrance is on the street rather than on the square.'),
    'Iris': (
        'The hall is a long room in a parish building, with a serving hatch to a small kitchen and a stage at the far end.',
        'The steward at Iris described the kitchen as suitable for tea and sandwiches only. The parish office requires a week of notice for any event with more than eighty people, and the steward undertook to pass the request on.',
        'The digest notes that Iris has the earliest access of any venue on the list, at half past seven.'),
    'Juniper': (
        'The room is a studio theatre with raked seating for sixty and a black floor, used mostly for rehearsals and small performances.',
        'The theatre technician told the steward that the seating rake is fixed and that chairs cannot be added in front of it. A technician is on site for the first hour of any event, after which the lighting desk is locked.',
        'Callers asking about Juniper should be told that the studio is not suitable for meals.'),
    'Kestrel': (
        'The venue is a hall at the edge of the old market, with a high ceiling, a gallery and a loading door at the rear.',
        'The market inspector visits Kestrel on Thursdays, and the steward warned that stalls are set up in the yard from five in the morning. Deliveries should therefore use the loading door before half past four or after ten.',
        'The digest records that Kestrel has the largest floor area among the listed halls, although its heating is slow.'),
    'Linden': (
        'The room is on the ground floor of a residential trust building, with a garden door that can stay open in warm weather.',
        'The steward reported that residents use the garden at midday and asked that groups avoid loud music then. The trust warden keeps a spare key, and the coordinator asked that the warden be told of any late finish.',
        'Staff should tell callers that Linden has a hearing loop in the main room.'),
}


RULES = (
    'The review concerns the daily room hire charge for each named venue, excluding equipment hire.',
    'The room hire amounts stated in records A, B and C are the exhaustive candidate values for each venue.',
    'If those records state the same amount for a venue, that amount is established.',
    'If those records state different amounts for a venue, its amount is established only when a clause in the governing agreement selects one of those records for that venue.',
    'Without such a clause, each stated candidate remains possible and the packet does not establish a single amount.',
    'The date of a record does not create precedence.',
    'A selected record determines the amount.',
    "The agreement's venue clauses are its complete list of record selections.",
    'Amounts for different venues may be resolved independently.',
)

VENUES = ('Alder', 'Bramble', 'Cedar', 'Dunlin', 'Elm', 'Fennel',
          'Grove', 'Hazel', 'Iris', 'Juniper', 'Kestrel', 'Linden')
RANK = 'higher_ranked_source_wins'
COPIES = 'most_copies_win'
LURE_ERROR = 'settles_conflict_by_rank_or_count'
DISPUTED_BUDGET = (5, 5)  # venues, of the nine with a budget claim, whose records differ; one 'not' venue also does
# Clause draw for every venue: record A, record B, no entry.
CLAUSES = (('A', 1), ('B', 1), (None, 2))
# Share of settled, separating-true cases in which the correct report states the
# separating bound; chosen so that a separating bound is as likely on an unsettled
# venue as on a settled one over the correct and mixed reports together.
SEPARATING_SHARE = 0.13


def reader_world(items, reader):
    """Ignore every agreement clause: rank takes record A, count the majority amount."""
    result = {}
    for item in items:
        entries = item['entries']
        if reader == RANK:
            value = entries['A']
        else:
            value = collections.Counter(entries.values()).most_common(1)[0][0]
        result[item['venue']] = value
    return {'base': result, 'unknowns': {}}


def draw_clause(rng):
    point = rng.randrange(sum(weight for _, weight in CLAUSES))
    for value, weight in CLAUSES:
        if point < weight:
            return value
        point -= weight


def build(rng):
    """Draw everything from the supplied Random; no retries or outside state."""
    shuffled = list(VENUES)
    rng.shuffle(shuffled)
    kinds = {name: ('budget', 'at most' if i < 4 else 'more than')
             for i, name in enumerate(shuffled[:9])}
    kinds.update({shuffled[9]: ('exact', 'exactly'), shuffled[10]: ('exact', 'not'), shuffled[11]: ('exact', 'not')})
    # One of the two "not" venues is disputed, so the other is a same-wording control in every report.
    disputed = set(rng.sample(shuffled[:9], rng.choice(DISPUTED_BUDGET))) | {rng.choice(shuffled[10:])}
    items, slots, base, unknowns = [], [], {}, {}
    for venue in VENUES:
        kind, wording = kinds[venue]
        low = rng.randrange(1800, 8600)
        gap = rng.choice((10, 20, 30))
        high, middle = low + gap, low + gap // 2
        target = rng.choice((low, middle))
        previous = low - gap * rng.choice((1, 2))
        equipment = high + gap * rng.choice((1, 2, 3))
        selection = draw_clause(rng)
        copied = rng.choice(('A', 'B'))
        conflict = venue in disputed
        if conflict:
            official, working = (low, high) if rng.random() < 0.5 else (high, low)
            entries = {'A': official, 'B': working}
            entries['C'] = entries[copied]
            value = entries[selection] if selection else None
        else:
            value = high if wording == 'at most' else low if wording == 'more than' else rng.choice((low, high))
            entries = {'A': value, 'B': value, 'C': value}
            # Where the records agree the clause is irrelevant (rule 3); it still selects a record.
            selection = selection or copied
        if value is None:
            unknowns[venue] = sorted(set(entries.values()))
        else:
            base[venue] = value
        items.append(dict(venue=venue, entries=entries, selection=selection, copied=copied,
                          target=target, previous=previous, equipment=equipment, conflict=conflict))
        cites = [('schedule.md', 'V-' + venue), ('correspondence.md', 'V-' + venue),
                 ('circulation.md', 'V-' + venue), ('agreement.md', 'V-' + venue), ('procedure.md', 'P-01')]

        def make(amount, error=None, venue=venue, kind=kind, wording=wording, cites=cites):
            text = f'The daily room hire charge for {venue} is {wording} EUR {amount}.'
            if wording == 'at most':
                predicate = lambda w: w[venue] <= amount
            elif wording == 'more than':
                predicate = lambda w: w[venue] > amount
            elif wording == 'exactly':
                predicate = lambda w: w[venue] == amount
            else:
                predicate = lambda w: w[venue] != amount
            return claim('room_hire_' + kind, text, predicate, cites,
                         quantity=lambda w: w[venue], stated=amount, error=error)

        if kind == 'budget':
            separating_error = LURE_ERROR if conflict else 'copies_negotiation_target'
            if wording == 'at most':
                open_bound, false_bound, false_error = high, previous, 'copies_last_season_charge'
                matches = value == low
            else:
                open_bound, false_bound, false_error = previous, equipment, 'copies_equipment_price'
                matches = value == high
            # The separating bound is true only where the clause selects the matching amount.
            if conflict and value is not None and matches and rng.random() < SEPARATING_SHARE:
                true, mutations = make(target), [make(false_bound, false_error)]
            else:
                true = make(open_bound)
                mutations = [make(target, separating_error), make(false_bound, false_error)]
        elif wording == 'exactly':
            true, mutations = make(value), [make(equipment, 'copies_equipment_price')]
        elif conflict:
            # "Not" a candidate: refuted where the clause selects that candidate, undetermined where it
            # selects nothing; the true claim denies the equipment price, a third value.
            true, mutations = make(equipment), [make(amount, LURE_ERROR) for amount in sorted(set(entries.values()))]
        else:
            true, mutations = make(equipment), [make(value, 'denies_selected_room_price')]
        slots.append((true, mutations))

    days = rng.sample(range(1, 24), 4)
    dates = {name: f'{day} September 2034' for name, day in zip(('A', 'B', 'C', 'P'), days)}
    dates['G'] = f'{max(days) + rng.randrange(1, 5)} September 2034'
    readers = {name: reader_world(items, name) for name in (RANK, COPIES)}
    return {'title': 'Room hire review', 'sources': render(items, dates), 'base': base, 'unknowns': unknowns,
            'slots': slots, 'readers': readers, 'reader': RANK, 'told': readers[RANK]}


def render(items, dates):
    """Office prose, with concurrent source histories and separate selection clauses."""
    schedule = [section('S-01', 'Issue and distribution',
        'Bellwether Civic Rooms Trust. Estates Office. Signed by Mara Vale, Estates Director. '
        f'Issue date: {dates["A"]}. Record A: signed room hire schedule. '
        'This schedule accompanies the autumn venue booking papers circulated to finance and programme teams. '
        'It provides a short price reference for staff preparing purchase requests and for reception staff '
        'answering enquiries about single-day events. The amounts concern room hire for one day, with ordinary '
        'furniture already in place. The schedule was assembled from venue estimates during the joint booking '
        'exercise. The director signed the compiled sheet for distribution to the participating offices. '
        'A signature records responsibility for that distribution; the agreement and review procedure travel '
        'with the sheet in the same packet. Copies were sent to the programme secretary and to the accounts '
        'desk so that telephone enquiries could be answered without fetching the booking correspondence. '
        'Reception staff also received the venue access notes, which describe entrances and furniture storage '
        'without setting prices. The finance desk retained this issue with the meeting papers for the autumn '
        'booking review. No event attendance figures or catering invoices form part of this schedule.')]
    correspondence = [section('B-01', 'Booking file note',
        'Bellwether Civic Rooms Trust. Booking Office. Prepared by Iona Reed, bookings coordinator. '
        f'Issue date: {dates["B"]}. Record B: booking correspondence. '
        'These notes preserve the price entries in the booking team\'s correspondence with the venue stewards. '
        'The booking office and estates office prepared their papers concurrently from separate worksheets. '
        'Estates used venue estimates; bookings used the amounts discussed during the negotiation calls. '
        'Each office issued its own set when its worksheet was ready, and the circulation deadline arrived '
        'before the two worksheets had been reconciled throughout. '
        'Both sets of papers were therefore retained for the agreement meeting, rather than overwriting one '
        'office\'s entries with the other\'s. '
        'The negotiation target in each note was a budget planning figure used during the call, not another '
        'quotation of the daily room hire charge. Last season\'s charge is quoted for comparison only. '
        'Equipment packages were discussed separately because some '
        'visiting groups bring their own audio and display equipment. The file note retains those amounts '
        'to explain the price fields on the booking worksheet. The coordinator asked each steward to keep '
        'the access arrangements available for the operations meeting, where delivery routes and staffing '
        'were discussed alongside the budget. No catering or transport prices appear in these notes.')]
    circulation = [section('C-01', 'Programme desk digest',
        'Bellwether Civic Rooms Trust. Programme Office. Prepared by Rowan Field, programme secretary. '
        f'Issue date: {dates["C"]}. Record C: circulation digest. '
        'This digest is the short list used at the programme desk while staff arrange the autumn meeting '
        'calendar. The secretary copied each price entry from the paper already open for that venue, whether '
        'a final copy or a working draft, and recorded the source letter beside it. The digest is a separate '
        'circulated document, but its room hire entries are transcriptions of the other papers rather than '
        'new conversations with stewards. '
        'It was assembled for colleagues who needed a compact reference during telephone calls. The '
        'source letters make the copying route visible when the booking file is revisited. A repeated '
        'amount can consequently appear in two circulated documents because one office reused another '
        'office\'s entry. The room list was checked against the calendar to avoid omitting a venue from '
        'the meeting agenda. That check covered the list of names, not reconciliation of the two price '
        'worksheets. The secretary filed the digest with the agreement, the signed schedule and the booking '
        'correspondence so that the finance reviewer would have the papers used by each office.')]
    agreement = [section('G-01', 'Governing agreement',
        'Bellwether Civic Rooms Trust. Contracts Office. Recorded by Ellis Moss, contracts secretary. '
        f'Issue date: {dates["G"]}. Venue booking agreement for the autumn programme. '
        'The parties reviewed the room hire papers at the agreement meeting and recorded document selections '
        'venue by venue below. Record A is the signed room hire schedule in schedule.md. '
        'Record B is the booking correspondence in correspondence.md. '
        'Record C is the programme desk digest in circulation.md. '
        'Each venue clause records the meeting\'s selection without transcribing its amount again. '
        'The secretary used this format so that later readers could trace the selected entry to the '
        'paper actually discussed at the meeting. The original worksheets were kept with the agreement '
        'because they also contain access and equipment information used by the operations team. '
        'The review procedure in procedure.md accompanies this agreement and sets out how the packet '
        'establishes room hire amounts. The contracts office retained the complete packet for the finance '
        'review, together with the source letters used in the programme digest. The list below covers '
        'all venues on the autumn agenda. It records the room hire selection for each venue; it does '
        'not record a decision about optional equipment packages or about attendance at any event.')]
    for item in items:
        venue, entries = item['venue'], item['entries']
        anchor = 'V-' + venue
        hall, steward, desk = FACTS[venue]
        schedule.append(section(anchor, venue,
            f'For {venue}, this schedule states a daily room hire charge of EUR {entries["A"]}. {hall}'))
        correspondence.append(section(anchor, venue,
            f'The steward\'s booking correspondence for {venue} gives a daily room hire charge of EUR {entries["B"]}. '
            f'The negotiation target on the call worksheet was EUR {item["target"]}. '
            f'Last season\'s daily room hire charge was EUR {item["previous"]}. '
            f'The separate daily equipment package price was EUR {item["equipment"]}. {steward}'))
        circulation.append(section(anchor, venue,
            f'The programme desk lists the daily room hire charge for {venue} as EUR {entries["C"]}, '
            f'copied from record {item["copied"]}. {desk}'))
        chosen = 'no entry' if item['selection'] is None else 'record ' + item['selection']
        agreement.append(section(anchor, venue,
            f'At {venue}, the parties select {chosen} as the governing room hire entry.'))
    sources = {
        'schedule.md': document('Room hire schedule', schedule),
        'correspondence.md': document('Booking correspondence', correspondence),
        'circulation.md': document('Programme desk digest', circulation),
        'agreement.md': document('Venue booking agreement', agreement),
        'procedure.md': document('Room hire review procedure', [
            section('P-00', 'Office record',
                'Bellwether Civic Rooms Trust. Contracts Office. Prepared by Ellis Moss, contracts secretary. '
                f'Issue date: {dates["P"]}. This procedure accompanies the autumn venue booking agreement '
                'and is retained with the papers used for the finance review.'),
            section('P-01', 'Establishing the room hire charge', '\n'.join(RULES))])}
    return dict(sorted(sources.items()))
