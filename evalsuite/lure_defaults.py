"""Defaults: closing an open office record with a familiar assumption.

The fictional Merevale Rooms Trust reviews seven September bookings and seven response
notices. The short desk briefing retains August prices and an exclusive-counting
reminder convention. Booking files and response clauses, read with the procedure,
decide whether either shortcut is justified. In 70% of draws one booking rate and
one notice convention remain open; otherwise every item is stated. Each open item
has exactly two possibilities. `fills_in_default` carries August's rate forward
and excludes the receipt day only where the record is open. Stated values survive
unchanged, including non-default values. Its single completed world is allowed by
the documents, so its only differing verdicts are undetermined -> supported and
undetermined -> refuted. This measures unwarranted certainty in either direction,
not a claim that an auditor actually reasoned by default.

Rule sentences, verbatim (procedure.md P-01):
    A hire rate applies only to the period for which a signed rate instruction states it.
    Only a notice's response clause fixes whether its receipt day is counted.
    If the supplied documents do not state a rate for a period or a counting convention for a notice, the packet does not establish that item.
    The alternatives listed in each record are exhaustive, and each listed alternative remains possible unless that record selects one.
    Each booking covers one session, and its hire charge equals the rate for its booking period.
    The response deadline is the last counted day of the notice's response period.
    Under inclusive counting the receipt date is day one.
    Under exclusive counting the date following receipt is day one.
    Every day is counted, including weekends and holidays, and no deadline is extended.
    Desk estimates and reminder dates do not select a rate or a counting convention.

Why these defaults are tempting, but not implied: a prior month's price is an easy
budget estimate, but these individually priced bookings require a monthly signed
instruction and expressly allow either listed price. Excluding receipt is useful
for reminder diaries, but this office uses both clause forms. No assertion about
real jurisdictions or a universal business custom is needed. The desk note predates
the September instructions, and the reminder worksheet omits the clause field;
these ordinary workflow limitations explain the shortcuts. No summary falsely
claims that an open item is settled.

Claims are bounds: "is at most" or "exceeds" a rate, "on or before" or "after" a date.
Each report holds six claims of each kind, on six of the seven bookings and six of
the seven notices, no item twice. Where every record states its selection, a bound
inside the pair (the lower alternative, or the higher one less a penny or a day) is
right for exactly one selection, and a bound outside the pair is decided by the
listed alternatives alone, whatever is selected. So every threshold of every claim
lies inside the pair: for hire the lower rate or the higher rate less one penny; for
a deadline the date under inclusive counting (the other date is the next day). The
wording of a right claim then follows the selection: "at most" or "on or before" when
the lower alternative is selected, "exceeds" or "after" when the higher one is. An
altered claim on a stated item takes the other wording at one of the same thresholds,
which is what a named slip produces:
    excludes_boundary (hire only): the comparison drops the boundary unit, "exceeds"
        the selected rate itself, or "at most" one penny under the selected higher rate.
    other_alternative_taken: the claim states what the other alternative would have
        made true: "at most" the lower rate when the higher is selected, "exceeds" one
        penny under the higher rate when the lower is selected; for deadlines the
        other counting convention, which gives the same claim as the boundary slip,
        so there is one name.
    fills_in_default: on an open item, either wording at either threshold is
        undetermined, and the default decides what the flawed reader sees. The
        wording of the lure is drawn once per kind and dossier.
Over seeds 1 to 1,000 (index 1) the altered claims are 821 excludes_boundary and 876
other_alternative_taken on hire, 1,690 other_alternative_taken on deadlines, and 716
fills_in_default of each kind. The slip "the neighbouring booking's or notice's value"
is not used: independently drawn rates and dates almost never fall inside the pair.

Three stated items per kind are wording-stable: their claim is never altered, so each
lure keeps a same-wording, no-echo control in its own report whatever the generator
alters (the tests enumerate every draw). Their wordings are drawn like any other,
except that the third takes the lure's wording when neither earlier one has it. The
open item is not claimed in the correct report: its lure takes the place of a claim on
a stated item, which is why a claim about an open item exists only in an altered
report (see limits).

Revision note. An earlier construction drew thresholds from the selected value (the
rate itself, or one unit away) and let every wrong claim keep its wording. A reader
who never opened the selection decided every stated hire claim by the rule "at most
is wrong unless the value is a listed rate, exceeds is wrong if it is" (accuracy 1.000
against a base rate of .839, AUC 1.000, precision and recall 1.000, seeds 1..500 to
501..1000), and the stated deadline claims by the same rule with AUC .741 (precision
.362, recall .738) or, with wording and position, AUC .886 and accuracy .920 against
.836. The same position features scored AUC .805 (hire) and .785 (deadline) for
"which claim is a lure". Right claims, wrong claims, lures and the claims on open items
now stand in the same relation to the pair (all inside it; for hire, half at the lower
alternative and half one penny under the higher), and a threshold inside the pair is
decided only by the selection. Three things changed with it: an altered stated claim
takes the other wording (so the two reports no longer share each slot's wording), the
open item is claimed only in the altered report, and there are seven items of each
kind. No rule sentence changed. The variant that keeps a supported claim about the open
item in the correct report (the bound that holds for both alternatives, which must lie
outside the pair) was built and measured on seeds 1..1,000: stated claims stay at the
base rate, but the number of thresholds outside the pair then tells the altered report
from the correct one with accuracy .858 and AUC .858 (the earlier construction: .658
and .723), and lures score AUC .552 (hire) and .541 (deadline), so it was not kept.

Measured with generator version 4. R9, seeds 1..100, twelve dossiers per seed:
echo rows per campaign minimum 10, median 16, maximum 24. All 1,678 echoes are
definite (851 supported, 827 refuted); zero echoes are undetermined. Their true
keys are all undetermined. There are 361 no-lure dossiers out of 1,200 (30.08%).
Each active dossier contributes two rows, one per kind, both in its mixed report.

Position guessers, reading only the rendered bookings and notices (the two listed
alternatives, or the two deadlines from receipt date and period, never the selection
sentences), trained on seeds 1..500 and scored on 501..1000, n = 5,642 stated claims
and 6,000 claims per kind. AUC, with its z against chance (standard error about .015
for lures, .011 for stated claims); accuracy equals the majority baseline everywhere:
    which stated claim is wrong (base .8463 hire, .8527 deadline):
        rule "at most is wrong unless the value is listed, exceeds is wrong if it is":
            hire AUC .524 (z +2.2), deadline .495 (z -0.5)
        wording and position (coarse, or exact offsets): hire .485 (z -1.4), deadline .495
    which claim is a lure (base .9403):
        the same rule: hire .491 (z -0.6), deadline .511 (z +0.7)
        wording and position: hire .486 (z -0.9), deadline .511 (z +0.7)
    Trained on 1..2,000 and scored on 2,001..4,000 every one of these has |z| below 2
    (hire wrong: rule .500, wording and position .490; lure .498 and .493; deadline
    wrong .497; lure .490). Precision of the rule "cell rate above the training base
    rate" is at the base rate: .142 to .166 for stated claims against .150, .058 to
    .062 for lures against .060. Report level: no report holds a threshold outside
    the pair, and the number of "at most" claims per kind gives AUC .484 (z -0.9) for
    "which report is altered" and .485 (z -0.7) for "does the dossier hold a lure".

Leak probes train on seeds 1..300 and hold out 301..600, keeping each seed's two
reports together. Categorical naive Bayes and a training-selected numeric stump
use the whole visible report, including title, preamble and citations, then also
source names, byte/trimmed/normalized lengths and
word counts. Numeric report features expose bounds and penny/calendar summaries;
synthetic threshold controls check that these features can carry a learned signal.
A wording that a report lacks is summarised by zeros, since reports no longer hold
three claims of each wording per kind.
Accuracy (Bayes, stump; majority baseline), report only:
    altered report: .5150, .4750; .5000
    dossier has lure: .4033, .7317; .7467
    wrong claim: .3083, .7996; .7996
    lure claim: .2572, .9378; .9378
With source metadata the Bayes accuracies are .5150, .4050, .3089, .2597 in
that order; stump accuracies and baselines are unchanged. Sparse numeric
categories hurt Bayes accuracy; this is not evidence of strong classification.
No tested guesser exceeds its baseline by more than the predeclared tolerance (the
largest gain is the altered-report Bayes guesser, +.015). Fixed
report text is also pinned independently. These are
finite probes with a predeclared three-standard-error tolerance, not a universal
claim about classifiers. All four wording shapes have all three verdicts over
400 seeds. There are 2,986 prose words excluding headings/anchors over 600 seeds.
Source byte, trimmed and whitespace-normalized character lengths and word counts
are each constant over those 600 cases, and over seeds 1..1,000 no source feature
(bytes, words, lines, table rows, right-stripped length) varies in any file, so a
lookup, naive Bayes or threshold rule on per-file lengths has nothing to learn.
Open/stated selection paragraphs have
equal word and normalized character counts; padding alone would not suffice.
Comparing source sentences with the numbers, names and dates masked, between the 716
dossiers with a lure and the 284 without (seeds 1..1,000): the only sentences whose
rate differs are the seven selection sentences themselves (the fact) and the two
clause sentences naming a counting convention, which follow from a dossier with a lure
having six stated notices against seven (inclusive 2.99 against 3.57, exclusive 3.02
against 3.43 per dossier).

Source sections have fixed character widths, padded with trailing spaces as a
plain-text office export. All amounts have two decimal places and all dates use
ISO dates. Names, headings, source order and section widths do not depend on which
records are open. Report lengths now depend on the wordings an altered report holds.
The two report filenames are the generator's authoring names; blindness.stage_case
renames the chosen report to case/report.md. Comparing both authoring reports is
not blind.

Limits: two binary items, one currency, one session per booking, calendar days,
and fixed prose templates only; no rounding, business-day shifts, legal doctrines,
missing files, OCR, disputed signatures or interdependent unknowns. Every claim is a
bound at a threshold inside the pair, so no claim is true whatever is selected: the
family no longer holds a claim that is correct whether an item is open or stated.
Remaining cues, measured:
  * A claim about an open item exists only in an altered report, where it is always
    altered, undetermined and a lure: of the 1,432 claims about open items over seeds
    1..1,000, none is in a correct report and all are lures in the altered one (the
    share of such claims that are lures is 0 of 0 in correct reports and 1,432 of
    1,432 in altered ones). A reader who opens the selection sentences therefore
    learns that a claim about an open item marks an altered report; the claim itself
    is still decided only by the selection. Open items exist only in dossiers that
    hold a lure, and those dossiers use two altered claims on lures, so stated claims
    are altered at .281 in a dossier with a lure (7,160 claims) against .404 in a plain
    one (3,408 claims).
  * The wording of a claim is not independent of the report. The three wording-stable
    items and the lure's control make the number of claims that share a wording a
    weak cue: AUC .529 (hire) and .520 (deadline) for "which stated claim is wrong",
    .552 and .536 for "which claim is a lure", in altered reports on seeds 501..1000
    (a test bounds each below .58). The review's per-report feature "claims of the
    same kind and wording" gives AUC .534 and .545 on seeds 1..1000. A guaranteed
    same-wording control that survives every alteration cannot be fully independent
    of the other wordings; a simulation of the ideal case (all wordings drawn alike,
    the lure's wording conditioned on having a partner) still gives AUC .528.
There are no correct claims about open items. Only altered reports
have echo rows, as necessarily follows from a default world inside the allowed worlds.
Sources explicitly disclose the alternatives and the missing selection; a reader
who follows those statements should not err. No model campaign has been run.
The independent parser checks arithmetic and rendering, not whether English has
only one reading. The quoted "Please respond within N days of receipt." leaves
receipt-day counting open; the procedure expressly preserves both interpretations.
No rule sentence is intentionally ambiguous. A careful reader
could disagree only by importing an external carry-forward or counting rule that
the fictional procedure expressly excludes. Removing that procedure, introducing
an implicit legal convention, or supplying another binding instruction would make
the key contestable. Universal careful reading would make the family cease to
separate auditors. Statistical leak probes do not prove absence of every cue.
"""
import datetime

from generate import claim, document, section


RULES = (
    'A hire rate applies only to the period for which a signed rate instruction states it.',
    "Only a notice's response clause fixes whether its receipt day is counted.",
    'If the supplied documents do not state a rate for a period or a counting convention for a notice, '
    'the packet does not establish that item.',
    'The alternatives listed in each record are exhaustive, and each listed alternative remains possible '
    'unless that record selects one.',
    'Each booking covers one session, and its hire charge equals the rate for its booking period.',
    "The response deadline is the last counted day of the notice's response period.",
    'Under inclusive counting the receipt date is day one.',
    'Under exclusive counting the date following receipt is day one.',
    'Every day is counted, including weekends and holidays, and no deadline is extended.',
    'Desk estimates and reminder dates do not select a rate or a counting convention.',
)
ROOMS = ('Alder', 'Beech', 'Cedar', 'Hazel', 'Maple', 'Rowan', 'Willow')

BOOKING_NOTES = (
    'The booking is for a neighbourhood reading group. The organiser asked for chairs in a circle and '
    'a clear route to the entrance. Facilities staff have recorded the arrangement separately from the '
    'price instruction so that an alteration to the furniture does not overwrite the account history. '
    'The caretaker will retain the attendance sheet with the room diary.',
    'The booking is for a sewing workshop. The organiser will bring portable machines and asked the '
    'caretaker to check the sockets before admission. The booking team has kept the equipment request '
    'with the access correspondence. It is not a purchase order and creates no additional charge in '
    'the hire account considered here. The access plan remains with the duty supervisor.',
    'The booking is for a local history discussion. The group will display copies of maps on removable '
    'stands. The original maps will not be stored on site. Facilities staff asked for the stands to be '
    'collected at the end of the session so that the cleaning team can prepare the room for its next '
    'use without moving privately owned material.',
    'The booking is for a volunteer induction. The organiser requested a quiet space for introductions '
    'and a separate sign-in desk. The manager agreed the room arrangement in the access correspondence. '
    'Names of attendees are held by the organiser and are not part of the finance packet. The account '
    'relates to the room session regardless of how many volunteers attend.',
    'The booking is for a community repair meeting. Participants may bring small household items but '
    'will not leave them overnight. The caretaker has asked the organiser to remove packaging before '
    'returning the keys. The room diary records the practical arrangements separately from the pricing '
    'record. No sale of replacement parts or other activity is charged through this hire account.',
    'The booking is for a neighbourhood choir rehearsal. The organiser requested the usual seating '
    'layout and agreed to leave the corridor clear. The caretaker will check the room when the keys '
    'are returned. The access correspondence records these arrangements for staff on the evening shift; '
    'it does not serve as the monthly price instruction or add another session to this booking.',
    'The booking is for a youth band practice. The organiser asked for the stage blocks to be set at the '
    'far end of the room and for the garden doors to stay closed during the session. The caretaker will '
    'agree the volume limit with the duty supervisor before the first session begins. The room diary '
    'records these arrangements separately from the pricing record, and the practice involves no sale of '
    'tickets or refreshments through this hire account.',
)
NOTICE_NOTES = (
    'The notice asks the organiser to confirm who will collect the keys. The office needs a named '
    'contact for the handover, but the receptionist must not choose that person on the organiser\'s '
    'behalf. The correspondence file will retain the reply and the delivery acknowledgement together '
    'so that the next shift can reconstruct the sequence without consulting a private diary.',
    'The notice requests a revised seating plan after the organiser asked to move the demonstration '
    'table. The facilities team needs the plan to arrange access for cleaning. The clerk has retained '
    'the delivery acknowledgement with the notice because the date on the outgoing letter alone does '
    'not show when the response period began. No decision about the plan is recorded here.',
    'The notice requests confirmation of the responsible adult for a group visit. It concerns the '
    'administration of the visit rather than the room price. The response will be filed by the reception '
    'team and passed to the duty manager. Personal contact details are held separately and have been '
    'excluded from this review copy because they are unnecessary for checking the deadline.',
    'The notice asks for a list of equipment that will be brought into the building. Facilities staff '
    'need that information to allocate storage during the session. The office retained the acknowledgement '
    'as evidence of delivery, while the original message remains in the correspondence system. Any later '
    'discussion about storage space will be entered as a separate piece of correspondence.',
    'The notice concerns arrangements for returning a borrowed access card. Reception staff asked the '
    'organiser to identify a person who can bring it to the desk. The card number is held in the access '
    'system and is not needed in this review. The response deadline concerns the written confirmation '
    'and is separate from the practical date proposed for the card handover.',
    'The notice requests confirmation that temporary display materials will be collected. The caretaker '
    'needs a contact for arranging collection and has passed the correspondence to the reception office. '
    'The file keeps the notice and acknowledgement together for continuity between shifts. The question '
    'of where material can be stored has not been delegated to the person maintaining the reminder diary.',
    'The notice asks the organiser to confirm that the garden doors will stay locked during an evening '
    'session. The caretaker needs the confirmation before the keys are issued and has asked reception to '
    'keep the reply with the delivery acknowledgement. The response concerns safety arrangements rather '
    'than the room price, and any later change to the evening rota will be entered as separate '
    'correspondence, so the diary entry remains a plain record of the reply.',
)


def money(pence):
    return f'{pence // 100}.{pence % 100:02d}'


def office_section(anchor, title, body, width):
    """Retain fixed-width export fields without truncating any source text."""
    text = section(anchor, title, body)
    if len(text) > width:
        raise ValueError('Office export field is too short')
    return text.rstrip('\n').ljust(width - 1) + '\n'


def roles_for(rng, open_index):
    """Give each of the seven items of one kind its part in the reports.

    At most one item is open; it is not claimed in the correct report, and in the altered report its
    lure takes the place of the claim on `replaced`. Three stated items are wording-stable: the claim
    on each is never altered, so a lure always keeps a same-wording control. Two or three stated items
    can be altered (`flip`). With nothing open, one stated item is left unclaimed instead."""
    others = [i for i in range(7) if i != open_index]
    rng.shuffle(others)
    roles = {others[0]: 'replaced' if open_index is not None else 'unclaimed',
             others[1]: 'stable', others[2]: 'stable', others[3]: 'stable'}
    for i in others[4:]:
        roles[i] = 'flip'
    if open_index is not None:
        roles[open_index] = 'open'
    return roles


def build(rng):
    has_open = rng.random() < 0.7
    open_room = rng.randrange(7) if has_open else None
    open_notice = rng.randrange(7) if has_open else None
    base, unknowns, defaults, slots, pending = {}, {}, {}, [], []
    bookings, notices, desk_rates = [], [], []
    room_roles, notice_roles = roles_for(rng, open_room), roles_for(rng, open_notice)
    # Per kind, the wording of the lure (True: "is at most" / "on or before"), drawn whether or not
    # anything is open so that dossiers look alike; and the wordings the stable items have drawn so far.
    lure_wording = {'hire': rng.random() < 0.5, 'date': rng.random() < 0.5}
    stable_wordings = {'hire': [], 'date': []}

    def selects_lower(kind, role):
        """Whether a stated item selects its lower alternative; the right claim then says "at most"."""
        if role == 'stable':
            drawn = stable_wordings[kind]
            # Wordings are drawn alike for every stable item, except that the third takes the lure's
            # wording where neither of the first two has it, so that the lure has a control.
            lower = lure_wording[kind] if len(drawn) == 2 and lure_wording[kind] not in drawn \
                else rng.random() < 0.5
            drawn.append(lower)
            return lower
        return rng.random() < 0.5

    def record(key, possibilities, selected, default):
        if selected is None:
            unknowns[key] = list(possibilities)
            defaults[key] = default
        else:
            base[key] = selected

    def fee_claim(room, at_most, value, error=None):
        key = 'rate:' + room
        wording = 'is at most' if at_most else 'exceeds'
        return claim('hire_charge', f'The September hire charge for the {room} room {wording} {money(value)} pounds.',
                     lambda w: w[key] <= value if at_most else w[key] > value,
                     [('briefing.md', 'B-02'), ('bookings.md', 'F-01'), ('procedure.md', 'P-01')],
                     lambda w: w[key], value, error=error)

    def date_claim(ident, before, value, error=None):
        key = 'deadline:' + ident
        wording = 'on or before' if before else 'after'
        date = datetime.date.fromordinal(value).isoformat()
        return claim('response_deadline', f'The response deadline for notice {ident} falls {wording} {date}.',
                     lambda w: w[key] <= value if before else w[key] > value,
                     [('briefing.md', 'B-03'), ('notices.md', 'N-01'), ('procedure.md', 'P-01')],
                     lambda w: w[key], value, error=error)

    def pair_slot(make, low, high, selected, role, lure=None):
        """Slot for one stated item. Every threshold lies inside the pair: the lower alternative, or
        the highest value below the higher one. At such a threshold "at most" is right exactly when the
        lower alternative is selected, and "exceeds" exactly when the higher one is, so the wording of a
        right claim follows the selection and a wrong claim takes the other wording at one of the same
        thresholds. `lure` is (make, low, high, wording) of the open item that takes this item's place
        in the altered report: the same thresholds, in the lure's wording."""
        thresholds = sorted({low, high - 1})
        at_most = selected == low
        right = make(at_most, rng.choice(thresholds))
        if role == 'stable':
            return right, []
        if lure is not None:
            open_make, open_low, open_high, wording = lure
            return right, [open_make(wording, value, 'fills_in_default') for value in sorted({open_low, open_high - 1})]
        wrong = []
        for value in thresholds:
            # Hire: at the selected alternative's own boundary the comparison drops the boundary unit;
            # at the other threshold it states what the other alternative would have made true. The
            # two deadlines are one day apart, so there both come to the same claim: one name.
            boundary = high - low > 1 and value == (low if selected == low else high - 1)
            wrong.append(make(not at_most, value, 'excludes_boundary' if boundary else 'other_alternative_taken'))
        return right, wrong

    for i, room in enumerate(ROOMS):
        low = rng.randrange(20000, 79000)
        high = low + rng.randrange(101, 1000)
        previous = rng.choice((low, high))
        role = room_roles[i]
        selected = None if role == 'open' else low if selects_lower('hire', role) else high
        record('rate:' + room, (low, high), selected, previous)
        desk_rates.append(f'{room} room: August rate {money(previous)} pounds per session.')
        selection = (
            'The September instruction is now held in the office signature folder, which was not included in '
            'this transfer. No supplied document selects either September rate.' if selected is None else
            f'The signed September instruction is copied here: "For September the rate is {money(selected)} '
            'pounds per session." The clerk checked the text against the signed instruction.')
        bookings.append(
            f'### {room} room\n\n{BOOKING_NOTES[i]}\n\n'
            f'The booking period is September 2034 and the booking covers one session. The pricing panel '
            f'approved exactly two possible September rates for this room: {money(low)} pounds or {money(high)} '
            f'pounds per session. A separate monthly instruction selects one of these rates. {selection} '
            'The hire charge contains no deposit, tax, discount or other component. The monthly instructions '
            'are issued independently for each room; an instruction for another room does not decide this account.')
        pending.append(('hire', room, low, high, selected, role))

    for i in range(7):
        ident = f'N{i + 1:02d}'
        received = datetime.date(2034, 1, 1) + datetime.timedelta(days=rng.randrange(280))
        period = rng.randrange(10, 41)
        exclusive = received.toordinal() + period
        role = notice_roles[i]
        convention = None if role == 'open' else 'inclusive' if selects_lower('date', role) else 'exclusive'
        selected = None if convention is None else exclusive - (convention == 'inclusive')
        record('deadline:' + ident, (exclusive - 1, exclusive), selected, exclusive)
        clause = (f'"Please respond within {period} days of receipt." The response clause does not select '
                  'inclusive or exclusive counting.' if convention is None else
                  f'"Please respond within {period} days of receipt using {convention} counting." '
                  'The notice clause fixes this counting rule.')
        notices.append(
            f'### Notice {ident}\n\n{NOTICE_NOTES[i]}\n\n'
            f'The delivery acknowledgement for notice {ident} records receipt on {received.isoformat()}. '
            f'The response period is {period} days. The complete response clause reads: {clause} '
            'The permitted conventions are inclusive and exclusive counting. No other counting convention '
            'is permitted. No other supplied document selects a counting convention for this notice. '
            'The acknowledgement records delivery only; it adds no terms to the response clause. '
            'Reception has kept the clause text with this entry so that the diary can be checked without '
            'treating a staff reminder as part of the notice.')
        pending.append(('date', ident, exclusive - 1, exclusive, selected, role))

    for kind in ('hire', 'date'):
        entries = [entry for entry in pending if entry[0] == kind]
        open_entry = next((entry for entry in entries if entry[5] == 'open'), None)
        for _, name, low, high, selected, role in entries:
            if role in ('open', 'unclaimed'):
                continue
            make = fee_claim if kind == 'hire' else date_claim
            lure = None
            if role == 'replaced':
                _, open_name, open_low, open_high, _, _ = open_entry
                lure = (lambda at_most, value, error=None, open_name=open_name, make=make:
                        make(open_name, at_most, value, error), open_low, open_high, lure_wording[kind])
            slots.append(pair_slot(lambda at_most, value, error=None, name=name, make=make:
                                   make(name, at_most, value, error), low, high, selected, role, lure))

    sources = {
        'briefing.md': document('Merevale Rooms Trust | desk briefing', [
            office_section('B-01', 'Handover',
                'Reception and Accounts Office. Prepared by Mira Vale, office manager, 28 August 2034. '
                'This handover supports staffing, provisional booking estimates and the shared reminder diary. '
                'It is kept at the front of the review packet because reception staff use it while answering '
                'telephone enquiries. The detailed booking records and notice clauses accompany it for the '
                'subsequent account review. Staff should keep correspondence about furniture, access and '
                'equipment with the relevant booking rather than entering it in a price field.\n\n'
                'The accounts team assembles monthly instructions after the pricing panel has approved the '
                'available levels. The reception sheet is prepared before those instructions arrive. During '
                'the handover the team uses the preceding month for estimates so that organisers can discuss '
                'a provisional budget. An estimate can therefore remain in the diary after a new instruction '
                'has been filed elsewhere. The review packet retains that sheet to explain the figures staff '
                'had available at the time, together with the records transferred at the December close.', 1600),
            office_section('B-02', 'Booking estimates',
                'The August schedule records the following rates. Reception carries these amounts into the '
                'September estimate worksheet pending the monthly instructions.\n\n' + '\n\n'.join(desk_rates), 800),
            office_section('B-03', 'Response diary',
                'The shared reminder worksheet starts counting on the day after receipt. Staff call this '
                'exclusive counting. The worksheet was designed to reserve staff time and has no field for '
                'the counting words in an individual response clause. The office also issues clauses using '
                'inclusive counting. When the record is transferred, the clause remains with the notice '
                'rather than being copied into the diary. A reminder is a staff planning entry, not an '
                'instruction to the recipient. Reception passes queries about a particular deadline to '
                'the records officer with the acknowledgement and the complete clause attached.', 800),
        ]),
        'procedure.md': document('Merevale Rooms Trust | account review procedure', [
            office_section('P-00', 'Issue and purpose',
                'Records Office. Issued by Iona Marsh, records officer, 2 January 2034. This procedure '
                'governs review of room charges and notice response deadlines within the Trust. The review '
                'uses the supplied monthly instructions, booking records, delivery acknowledgements and '
                'complete response clauses. Its purpose is to distinguish what those records establish '
                'from what a member of staff may have used for planning. All prices are in pounds and '
                'pence; a penny is the smallest monetary unit in these accounts. The procedure remains '
                'in force for the records in this packet.', 800),
            office_section('P-01', 'Rates and response periods', '\n\n'.join(RULES), 1400),
        ]),
        'bookings.md': document('Merevale Rooms Trust | booking account records', [
            office_section('F-00', 'Records transfer',
                'Accounts Office. Prepared by Tessa Reed, accounts officer, 2 December 2034. These entries '
                'bring together the September bookings, the panel\'s permitted prices and the monthly '
                'instruction text available in the transferred records. Their purpose is to support the '
                'account review without treating the reception estimate as an invoice. The narrative '
                'describes the use of the room because the account record also serves the facilities '
                'handover. It does not introduce separate billable services. The price selection is '
                'recorded after the booking details in each entry.', 800),
            office_section('F-01', 'September bookings', '\n\n'.join(bookings), 10500),
        ]),
        'notices.md': document('Merevale Rooms Trust | correspondence records', [
            office_section('N-00', 'Records transfer',
                'Correspondence Office. Prepared by Corin Bell, correspondence clerk, 2 December 2034. '
                'These entries retain the acknowledgement and response clause for each administrative '
                'notice in the review. The purpose of the transfer is to preserve the recipient\'s '
                'response terms alongside the evidence of delivery. Notices are independent of the '
                'room price instructions, and their numbering is a filing reference rather than a '
                'chronological sequence. Staff reminders are retained in the desk briefing; the clause '
                'text below is the complete response wording supplied for each notice.', 800),
            office_section('N-01', 'Notice files', '\n\n'.join(notices), 11500),
        ]),
    }
    told = {'base': dict(base, **defaults), 'unknowns': {}} if unknowns else None
    return {'title': 'Merevale Rooms Trust | account and correspondence review',
            'sources': dict(sorted(sources.items())), 'base': base, 'unknowns': unknowns,
            'slots': slots, 'told': told, 'reader': 'fills_in_default', 'open_claims': 2}
