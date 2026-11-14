"""A grants committee's minute book: a long, irregular dossier in which the facts sit inside prose.

The dossier is the minute book of a grants committee over about thirty monthly meetings (three volumes) and a
short `procedure.md`. Eighteen to twenty-four funded projects run through it. What happens to a project is told
inside paragraphs, in the order it happened: an award, payments (absolute, or relative to something said
elsewhere), variations of the award, corrections of earlier minutes, a rescission before any payment, and the
return of a payment. Beside these stand things that decide nothing and are there to be mistaken for decisions:
motions that fell, items deferred, payments authorised but not yet made, and the treasurer’s periodic summaries
that restate payments already reported. The bulk of every meeting is ordinary committee business.

There is no identifier per fact, no table and no list of the deciding facts. Every project has a full name
(`Corbin Lane Hostel Refurbishment`), four short forms or descriptions derived from its parts (`the Corbin Lane
hostel`, `the hostel refurbishment`, `the refurbishment at Corbin Lane`, `the project to refurbish the hostel in
Corbin Lane`) and, once a passage has said who sponsors it, a fifth (`the Marlow Trust scheme`). A phrase is used
only if it fits exactly one project of the dossier; the sponsor form only after the passage that establishes it.

The facts are one event log, drawn from `rng` (a list of JSON-serialisable dicts, `case['events']`). One
interpreter, `state(events, reader=None, meetings=None)`, turns any subset of the log into the quantities the
statements are about, as a flat dict keyed `kind|subject|date`; `base` is what it returns for the whole log, and
each flawed reader's `base` is what it returns with that reader's shortcut switched on. A subset is read as if it
were the whole minute book (see `answer`): an event whose own meaning depends on an event the subset lacks is left
out of the reading, and the subset is otherwise the whole record. Three kinds of dependency are recorded for each
event: `refs` (what its meaning is read through), `named_by` (what fixes the way its paragraph names its project: the
application that ties a sponsor to a project, every award up to and including the project's own for "the Nth
undertaking to receive the committee's backing", or the event that stands for the lead paragraph a paragraph that
points back depends on) and `amount_by` (what fixes the words that give its sum: the instalments before the k-th, and
what names the other project of "the first instalment paid to" it). The generator itself still draws its dossiers
under the older reading (`legacy`, used only by `Ablation`), so that they stay the bytes they were.

What a checker may call: `answer(query, events, reader=None)` (the value of a statement from any subset of the events;
`None` when not established, `NONE_VALUE` when it is established that there is no value), `value_form(query)`,
`depends(events)` (what each event cannot be read without: its name, its amount, its target), `relevant(query, events)`, `case['events']`, `case['passages']`, `case['names']` and `claim['query']`. A subset is read as
the whole record: an event that refers to an event the subset lacks, or whose project is named through one it lacks,
is left out of the reading. The number of payments to a project counts a payment that can be attributed to it (named,
and reported made) even where its amount cannot be read from the subset; every other quantity leaves such a payment out.

The same facts are told in many ways, so that no word list isolates them: with the verbs of awarding and paying and
without them (a proposal "stood", a sum "went out with the month's cheques"), by the project's full name, by short
forms, by its sponsor, by its place in the order of backing, or by pointing back to the paragraph above. Sums given by
other bodies and the committee's own fair are told with the same verbs and decide nothing.

Reading rules (all of them are restated in `procedure.md`, which is the only authority for a statement):
    - A meeting is identified by its date; there is one meeting per calendar month, so `May 2036` names one.
    - An award counts from the meeting that made it. A correction replaces the figure it corrects from the start
      (from the meeting of the minute it corrects, not from the meeting that minutes the correction), and every
      figure defined by reference to the corrected one is read from the corrected figure.
    - A variation takes effect at the meeting that minutes it, from the award in force immediately before it.
    - A payment counts from the meeting at which the treasurer reports it made. A payment authorised, a motion
      that fell, an item deferred and a restatement in a summary decide nothing. A payment returned or recalled
      stops counting from the meeting that reports the return; the numbering of the instalments does not change.
    - A rescission ends the award from the meeting that minutes it, and is only ever made before any payment.
    - Within a meeting the business is taken before the treasurer’s report.

Flawed readers (each a specific shortcut; the interpreter switches):
    counts_every_mention         Every entry of a treasurer’s summary is one more payment to that project,
                                 reported at the summary's meeting, for the sum the summary states. It counts for
                                 the totals, the number of payments, the largest payment, the balance and for
                                 "the balance of the award" in later payments; it is not a numbered instalment.
    ignores_corrections          Keeps every figure as first minuted (corrections are skipped), keeps a rescinded
                                 award in force and keeps a returned payment counting. Relative amounts follow
                                 the uncorrected figures.
    counts_motions_as_decisions  A fallen or deferred variation is applied as if carried, at its own meeting; a
                                 deferred award recommendation sets the award at its meeting (the later real award
                                 replaces it); an authorisation is a payment (a numbered instalment) at the
                                 authorisation's meeting, and the later report that it was made is the same
                                 payment, not a second one (a return of such a payment, which the reader does
                                 not see as a payment of its own, changes nothing).
    full_name_only               Sees only the events told in a paragraph that holds the project's full name and
                                 reads them as the procedure says, from what it has: "the first instalment" is
                                 the first one it sees, "the award then in force" the one it has, a return or
                                 correction that points at something it does not see changes nothing, and a
                                 payment whose amount it cannot work out at all is skipped.

Edition. The environment variable BIG_MINUTES_EDITION (1 or 2; unset means 1; anything else is an error) says which
edition of the generator draws the dossier. Edition 1 is the dossier described above, byte for byte as it was before
editions existed. Edition 2 is about one and a half times as large (30 to 36 projects over 44 to 48 meetings, four or
five minute books, 570,000 to 630,000 characters), its 24 statements per report are 7 about the number of projects holding
an award, 6 totals paid, 6 numbers of payments, 2 unpaid balances, 2 largest payments and 1 first full payment (none
about the award in force; 18 statements on which a flawed reader is wrong and 6 controls on which every reader is right),
and every lead paragraph (the paragraph that "the undertaking named in the paragraph above" points to) occurs once in the
dossier and is at least 40 characters. The reading rules, `procedure.md`, the interpreter (`answer`, `reading`, `depends`,
`relevant`) and the rule for what a paragraph earns are the same in both editions. `case['size']` is 2 for edition 2 and
absent for edition 1 (the launcher records it in the key's facts as `size`).
"""
import datetime
import os
import random
from fractions import Fraction
import generate
from generate import claim

READERS = ('counts_every_mention', 'ignores_corrections', 'counts_motions_as_decisions', 'full_name_only')
# A reader that bundles several shortcuts is made of these devices; `state(events, 'only:<device>')` switches on one.
DEVICES = {'ignores_corrections': ('corrections', 'rescissions', 'returns'),
           'counts_motions_as_decisions': ('fell_vary', 'defer_vary', 'defer_award', 'authorise')}

MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December')

PLACES = ('Corbin Lane', 'Tanner Row', 'Fenwick Street', 'Quay Road', 'Ashgrove Close', 'Mill Lane', 'Harrow Gate',
          'Sedge Walk', 'Pilcher Street', 'Dunmore Road', 'Kiln Yard', 'Orchard Way', 'Saltings Road', 'Wren Court',
          'Bellwether Lane', 'Gorse Hill')
BUILDINGS = ('hall', 'hostel', 'boathouse', 'pavilion', 'library', 'schoolroom', 'clinic', 'nursery', 'workshop',
             'bandstand', 'lido', 'chapel', 'almshouse', 'cafe')
# (title-case noun used in the full name, verb phrase of the description; the building follows the verb phrase)
WORKS = (('Refurbishment', 'refurbish'), ('Roof Repair', 'repair the roof of'), ('Extension', 'extend'),
         ('Rewiring', 'rewire'), ('Solar Installation', 'fit solar panels to'),
         ('Accessible Entrance', 'fit an accessible entrance to'), ('Heating Upgrade', 'upgrade the heating of'),
         ('Kitchen Refit', 'refit the kitchen of'), ('Insulation', 'insulate'),
         ('Flood Defences', 'build flood defences for'), ('Window Replacement', 'replace the windows of'),
         ('Drainage Works', 'improve the drainage of'), ('Car Park', 'add a car park to'),
         ('Garden Project', 'lay out a garden beside'))
SPONSORS = ('Marlow Trust', 'Aldric Parish Council', 'Harbour Lights Society', 'Fernhill Residents Association',
            'Calder Valley Scouts', 'Brook Street Guild', 'Thistle Housing Cooperative', 'Penrose Rowing Club',
            'Willowbank Friends', 'Granary Arts Collective', 'Lantern Youth Project', 'Oakham Mutual Aid',
            'Saint Brendan Mission', 'Rookery Tenants Union', 'Silverdale Bowls Club', 'Tidewater Cooperative',
            'Elmstead Sports Forum', 'Northcote Allotment Society', 'Hartley Memorial Trust', 'Ravenscar Community Hub',
            'Stonebridge Players', 'Meadowvale Neighbours', 'Cobbler Row Association', 'Kestrel Guides')

# Edition 2 draws from longer pools: the tables above are their first entries, so that edition 1 draws what it always drew.
PLACES2 = PLACES + ('Beacon Hill', 'Cooper Street', 'Lantern Walk', 'Rushey Green', 'Thorn Lane', 'Weaver Close',
                    'Abbey Fields', 'Ferry Lane')
BUILDINGS2 = BUILDINGS + ('museum', 'theatre', 'gymnasium', 'stables', 'smithy', 'orangery')
WORKS2 = WORKS + (('Lift Installation', 'install a lift in'), ('Fire Doors', 'fit fire doors to'),
                  ('Lighting Upgrade', 'upgrade the lighting of'), ('Boundary Wall', 'rebuild the boundary wall of'),
                  ('Floor Restoration', 'restore the floor of'), ('Toilet Block', 'build a toilet block beside'))
SPONSORS2 = SPONSORS + ('Beacon Allotment Holders', 'Riverside Reading Room Trust', 'Hilltop Cricket Club',
                        'Millpond Conservation Group', 'Greenfield Playgroup Committee', 'Stoneleigh Heritage Society',
                        'Fairway Angling Club', 'Cedar Court Residents', 'Ashby Folk Society', 'Larkspur Gardeners',
                        'Tannery Row Traders', 'Willow Pond Trust', 'Harvest Home Society', 'Kingsway Youth Trust',
                        'Ivy Lane Chapel Trust', 'Barley Mow Friends')
# What an edition changes: meetings, projects, minute books and size of the text, and the mix of statements per report.
EDITION_VARIABLE = 'BIG_MINUTES_EDITION'
EDITIONS = (1, 2)
SETTINGS = {1: {'meetings': (29, 32), 'projects': (18, 24), 'books': 3, 'size': (350000, 420000), 'bounds': (300000, 450000),
                'places': PLACES, 'buildings': BUILDINGS, 'works': WORKS, 'sponsors': SPONSORS},
            2: {'meetings': (44, 48), 'projects': (30, 36), 'books': None, 'size': (570000, 630000), 'bounds': (550000, 650000),
                'places': PLACES2, 'buildings': BUILDINGS2, 'works': WORKS2, 'sponsors': SPONSORS2}}
# Edition 2: statements per report by kind (the unpaid award in force is not asked), and of them the controls (every
# flawed reader right); the other 18 are statements on which a flawed reader is wrong.
MIX2 = {'projects': 7, 'total': 6, 'count': 6, 'balance': 2, 'largest': 2, 'first': 1, 'award': 0}
CONTROLS2 = {'projects': 2, 'total': 2, 'count': 1, 'balance': 1}


def current_edition():
    """The edition asked for by BIG_MINUTES_EDITION: 1 when it is not set; an error when it is set to anything but 1 or 2."""
    raw = os.environ.get(EDITION_VARIABLE)
    if raw is None:
        return 1
    if raw not in ('1', '2'):
        raise generate.GenerationError('%s must be 1 or 2, not %r' % (EDITION_VARIABLE, raw))
    return int(raw)


# Aliases derived from the parts of the full name; `{work}` is the lower-case noun of the work.
ALIAS_FORMS = ('the {place} {building}', 'the {building} {work}', 'the {work} at {place}',
               'the project to {verb} the {building} in {place}')
SPONSOR_ALIAS = 'the {sponsor} scheme'

DATE_FORMS = ('{d} {Month} {Y}', '{Month} {d}, {Y}', '{od} {Month} {Y}', '{Y}-{mm}-{dd}')
MONTH_YEAR = '{Month} {Y}'
# A reference to a meeting: whole noun phrases; the frames put "at", "in the minutes of" and the like before them.
WHEN_FORMS = (('date', 'the meeting of {date}'), ('date', 'the meeting held on {date}'), ('date', 'the {date} meeting'),
              ('month', 'the {monthyear} meeting'), ('month', 'the meeting in {monthyear}'),
              ('month', 'the meeting of {monthyear}'), ('date', 'the meeting on {date}'))
HEADINGS = ('Minutes of the meeting held on {date}', 'Meeting of {date}', '{date}: minutes of the meeting',
            'Minutes, {date}', 'Grants Committee, {date}', 'Record of the meeting on {date}')

MONEY_FORMS = ('£{digits}', '{digits} pounds', '{words} pounds')
FRACTIONS = (('half', 1, 2), ('50 per cent', 1, 2), ('a quarter', 1, 4), ('25 per cent', 1, 4), ('a fifth', 1, 5),
             ('20 per cent', 1, 5), ('a tenth', 1, 10), ('10 per cent', 1, 10))
ORDINALS = (('second', 2), ('third', 3), ('fourth', 4), ('fifth', 5))
RAISE = ('increased', 'raised', 'enlarged', 'topped up')
LOWER = ('reduced', 'cut', 'lowered', 'scaled back')
RAISE_INF = ('increase', 'raise', 'enlarge')
LOWER_INF = ('reduce', 'cut', 'lower')

# Sentence frames. Placeholders: {proj} a reference to a project, {proj2} a second one, {full} a full name,
# {sponsor}, {money} an absolute sum, {amt} an amount phrase (see AMOUNT_PHRASES), {delta} a variation phrase,
# {when} a reference to a meeting, {verb} a verb (RAISE / LOWER, or the infinitives).
FRAMES = {
    'award': (
        'The committee resolved to award {money} to {proj}.',
        'On a motion from the chair, seconded and carried, {proj} was awarded {money}.',
        'Members voted to fund {proj} with a grant of {money}.',
        'It was resolved that a grant of {money} be made to {proj}.',
        'The committee agreed an award of {money} for {proj}.',
        'After a short discussion the committee carried a motion awarding {money} to {proj}.',
        'A grant of {money} was approved for {proj}.',
        'The committee made {proj} an award of {money}.',
        'Carried: an award of {money} to {proj}.',
        'The committee decided, without dissent, to support {proj} with {money}.'),
    'pay': (
        'The treasurer reported that {proj} had been paid {amt}.',
        'The treasurer reported payment of {amt} to {proj}.',
        'Treasurer: {amt} was paid to {proj} during the month.',
        'The treasurer told the meeting that {amt} had gone to {proj}.',
        'The treasurer advised that {amt} had been transferred to {proj}.',
        'The treasurer reported the release of {amt} to {proj}.',
        'According to the treasurer, {proj} was sent {amt} this month.',
        'The treasurer confirmed that {proj} had received {amt}.',
        'Among the payments made since the last meeting, the treasurer reported {amt} to {proj}.',
        'The treasurer noted a transfer to {proj} of {amt}.'),
    'vary': (
        'The award for {proj} was {verb} by {delta}.',
        'The committee agreed that the award for {proj} be {verb} by {delta}.',
        'Members voted to have the award to {proj} {verb} by {delta}.',
        'On a show of hands the award for {proj} was {verb} by {delta}.',
        'A variation was carried under which the award to {proj} was {verb} by {delta}.',
        'The grant to {proj} was {verb} by {delta}, with no member dissenting.',
        'The committee had the award for {proj} {verb} by {delta}, agreeing unanimously.',
        'Carried: the award for {proj} to be {verb} by {delta}.'),
    'fell_vary': (
        'A motion to {verb} the award for {proj} by {delta} was lost.',
        'The proposal to {verb} the award to {proj} by {delta} fell for want of a seconder.',
        'Members rejected a motion to {verb} the grant to {proj} by {delta}.',
        'A motion to {verb} the award for {proj} by {delta} failed on a show of hands.',
        'The committee voted down a proposal to {verb} the award for {proj} by {delta}.',
        'A proposal to {verb} the award to {proj} by {delta} did not carry.',
        'The motion to {verb} the grant for {proj} by {delta} was defeated.',
        'No change was made: the motion to {verb} the award for {proj} by {delta} fell.'),
    'defer_vary': (
        'A proposal to {verb} the award for {proj} by {delta} was deferred.',
        'Consideration of a motion to {verb} the award to {proj} by {delta} was deferred to a later meeting.',
        'The committee deferred a proposal to {verb} the award for {proj} by {delta}.',
        'Members put off the question of whether to {verb} the award to {proj} by {delta}.',
        'A request to {verb} the grant to {proj} by {delta} was held over without a decision.',
        'The proposal to {verb} the award for {proj} by {delta} was held over to the next meeting.',
        'The chair deferred the item on whether to {verb} the award for {proj} by {delta}.',
        'Discussion of a proposal to {verb} the grant for {proj} by {delta} was adjourned undecided.'),
    'defer_award': (
        'A recommendation to award {money} to {proj} was deferred pending further information.',
        'The committee deferred a proposed award of {money} to {proj}.',
        'Consideration of a grant of {money} for {proj} was deferred to a later meeting.',
        'The proposal that {proj} be awarded {money} was held over without a decision.',
        'Members put off a decision on awarding {money} to {proj}.',
        'The recommended award of {money} for {proj} was deferred while the sponsor supplied its accounts.',
        'A motion to award {money} to {proj} was adjourned.',
        'The panel recommended {money} for {proj}, but the item was deferred and nothing was decided.'),
    'authorise': (
        'The committee authorised the treasurer to pay {money} to {proj} once the contractor’s certificate arrived.',
        'Authority was given to pay {money} to {proj} as soon as the sponsor returned the signed agreement.',
        'The treasurer was authorised to release {money} to {proj} on receipt of the architect’s certificate.',
        'A payment of {money} to {proj} was authorised, to be made when the invoices had been checked.',
        'The committee approved a payment of {money} for {proj}, to be released once the site visit was complete.',
        'Members authorised a payment to {proj} of {money}, subject to receipt of the insurance certificate.',
        'The treasurer received authority to pay {proj} {money} after the grant conditions had been met.',
        'Payment of {money} to {proj} was authorised; it was not yet due.'),
    'correct_award': (
        'The award for {proj} minuted at {when} should have read {money}.',
        'It was noted that the award to {proj} minuted at {when} should have read {money}.',
        'Correction: the sum awarded to {proj} at {when} should be read as {money}.',
        'The secretary reported an error in the minutes of {when}, where the award for {proj} should have read {money}.',
        'The award made to {proj} at {when} had been minuted wrongly and should have read {money}.',
        'Members approved a correction to the minutes of {when}: the award to {proj} should have read {money}.',
        'The record of the award to {proj} at {when} was corrected to read {money}.',
        'The figure minuted for the award to {proj} at {when} was wrong and is corrected to {money}.'),
    'correct_pay': (
        'The payment to {proj} reported at {when} should have read {money}.',
        'It was noted that the payment to {proj} reported at {when} should have read {money}.',
        'Correction: the sum paid to {proj} as reported at {when} should be read as {money}.',
        'The treasurer reported an error in the minutes of {when}, where the payment to {proj} should have read {money}.',
        'The payment made to {proj} and reported at {when} had been minuted wrongly and should have read {money}.',
        'Members approved a correction to the minutes of {when}: the payment to {proj} should have read {money}.',
        'The record of the payment to {proj} reported at {when} was corrected to read {money}.',
        'The figure minuted for the payment to {proj} at {when} was wrong and is corrected to {money}.'),
    'rescind': (
        'The committee rescinded the award to {proj}, no payment having been made.',
        'Members voted to withdraw the award for {proj}; nothing had been paid.',
        'The award to {proj} was rescinded before any instalment was paid.',
        'At the request of its sponsor the committee rescinded the grant to {proj}, which had not been drawn.',
        'The grant to {proj} was withdrawn, no money having left the fund.',
        'Following a review the committee cancelled the award for {proj}; no payment had been made.',
        'It was resolved to rescind the award made to {proj}, as the project could not go ahead.',
        'The award for {proj} was cancelled at the sponsor’s own wish, with no payment made.'),
    'return': (
        'The treasurer reported that the payment to {proj} reported at {when} had been returned.',
        'The treasurer told the meeting that the sum paid to {proj} and reported at {when} had been recalled.',
        'The payment to {proj} reported at {when} was returned by the sponsor’s bank, as the treasurer explained.',
        'The treasurer reported the repayment by {proj} of the instalment reported at {when}.',
        'The instalment paid to {proj} and reported at {when} was sent back, the treasurer reported.',
        'The treasurer reported that the instalment to {proj} reported at {when} had been clawed back.',
        'According to the treasurer, the transfer to {proj} reported at {when} bounced and the money came back.',
        'The treasurer recalled the payment to {proj} reported at {when}, the sponsor having asked for it to be reversed.'),
    'outside': (
        'The sponsor of {proj} said that the county council had awarded it {money} towards the building work.',
        'A donor had paid {money} to the sponsor of {proj} for its roof fund, which members noted with pleasure.',
        'The sponsor of {proj} reported that a regional trust had granted it {money}, a matter for the sponsor alone.',
        'The parish had sent {money} to {proj} from its collection, the sponsor told members.',
        '{proj} had been awarded {money} by a lottery fund, the sponsor reported; this was no concern of the committee.',
        'The sponsor of {proj} had received {money} from the diocese, which it paid into its own account.',
        'A national charity transferred {money} to the sponsor of {proj}, according to a letter read to members.',
        'A brewery had agreed to give the sponsor of {proj} {money}, the sponsor wrote.'),
    'intro': (
        '{sponsor} had applied for funding for {full}; the application was noted.',
        'An application from {sponsor} for {full} was received and noted.',
        'The secretary reported an application by {sponsor} concerning {full}, to be considered in due course.',
        'The committee noted that {sponsor} was the applicant for {full}.',
        'Papers for {full} arrived from {sponsor}, the applicant, and were circulated.',
        'The sponsor of {full}, {sponsor}, sent in its application form.'),
}
# What may stand for {amt} in a payment frame, and for {delta} in a variation frame.
AMOUNT_PHRASES = {
    'first': ('a sum equal to the first instalment', 'an amount equal to the first instalment',
              'the same sum as the first instalment'),
    'ordinal': ('a sum equal to the {ord} instalment', 'an amount equal to the {ord} instalment',
                'the same sum as the {ord} instalment'),
    'frac_award': ('{frac} of the sum awarded at {when}', '{frac} of the amount the committee awarded at {when}',
                   '{frac} of the award made at {when}'),
    'frac_current': ('{frac} of the award then in force', '{frac} of the award as it then stood',
                     '{frac} of the award in force at that meeting'),
    'balance': ('the balance of the award', 'the whole unpaid balance of the award', 'what remained unpaid of the award'),
    'first_of': ('a sum equal to the first instalment paid to {proj2}',
                 'an amount equal to the first instalment that went to {proj2}'),
    'authorised': ('the sum authorised at {when}', 'the amount authorised at {when}',
                   'the sum the committee had authorised at {when}'),
}
DELTA_PHRASES = ('{frac} of the award as it then stood', '{frac} of the award in force beforehand',
                 '{frac} of the award immediately before')
# The same facts told without the verbs a filter would look for (award, grant, agreed, paid, instalment, ...): a
# decision is read by what the minute says happened, as `procedure.md` explains.
PLAIN_FRAMES = {
    'award': (
        'The chair’s proposal to back {proj} with {money} stood.',
        '{proj} was backed to the extent of {money}, nobody speaking against.',
        'Members settled on {money} for {proj} and moved on.',
        'The committee’s backing for {proj} was fixed at {money} on a show of hands.',
        'On the voices the proposal of {money} for {proj} went through.',
        '{money} was fixed as the committee’s help for {proj}.',
        'The proposal to back {proj} with {money} was adopted without a vote against.',
        'Members gave {proj} their backing, to the sum of {money}.'),
    'pay': (
        'The month’s cheques included {amt} made out to {proj}.',
        '{amt} left the fund’s account for {proj} this month.',
        '{proj} had {amt} credited to its account from the fund.',
        'Out of the bank this month went {amt} for {proj}.',
        'The cheque run covered {amt} for {proj}.',
        'The sum went out to {proj} with the month’s cheques: {amt}.',
        'For {proj} the fund’s account was debited {amt}.',
        'A cheque for {amt} was drawn in favour of {proj}.'),
    'vary': (
        'The sum backing {proj} was {verb} by {delta}.',
        'The backing for {proj} was {verb} by {delta}, nobody objecting.',
        '{proj} had its backing {verb} by {delta}.',
        'On the voices the sum fixed for {proj} was {verb} by {delta}.',
        'The committee’s help for {proj} was {verb} by {delta} on a show of hands.',
        'It stood that the backing for {proj} be {verb} by {delta}.'),
    'correct_award': (
        'The figure minuted at {when} for {proj} misstated the sum; it stood at {money}.',
        'At {when} the sum fixed for {proj} was minuted wrongly; the true figure was {money}.',
        'The record of {when} was amended for {proj}: the backing was {money} all along.',
        'Members noted that the sum minuted at {when} for {proj} was wrong and ought to read {money}.',
        'A slip in the minute of {when} was put right: {proj} was backed to the sum of {money}.',
        'The minutes of {when} gave a wrong sum for {proj}; the right one is {money}.'),
    'correct_pay': (
        'The figure minuted at {when} for the cheque to {proj} misstated the sum; it stood at {money}.',
        'At {when} the cheque to {proj} was minuted wrongly; the true sum was {money}.',
        'The record of {when} was amended for {proj}: the cheque was for {money} all along.',
        'Members noted that the sum minuted at {when} as going to {proj} was wrong and ought to read {money}.',
        'A slip in the minute of {when} was put right: the cheque to {proj} was for {money}.',
        'The minutes of {when} gave a wrong sum for the cheque to {proj}; the right one is {money}.'),
    'rescind': (
        'The backing for {proj} was withdrawn before any money had left the fund.',
        'The sum fixed for {proj} was dropped, nothing having left the fund.',
        '{proj} lost its backing at the sponsor’s own request; no money had gone out.',
        'The committee withdrew its help from {proj}, with no cheque ever drawn.',
        'Backing for {proj} was struck out before a single cheque went out.',
        'The committee’s support for {proj} was cancelled while the fund still held every penny of it.'),
    'return': (
        'The money that went out to {proj} as reported at {when} was repaid to the fund.',
        'The cheque to {proj} reported at {when} bounced and the money came back to the fund.',
        '{proj} repaid the sum reported at {when}.',
        'The bank reversed the cheque to {proj} reported at {when}.',
        'The sum that left the fund for {proj} at {when} came back to the account.',
        'The cheque to {proj} that was reported at {when} was cancelled and the money restored to the fund.'),
}
PLAIN_AMOUNT_PHRASES = {
    'first': ('a sum matching the opening payment', 'the same figure as the opening cheque'),
    'ordinal': ('a sum matching the {ord} payment', 'the same figure as the {ord} cheque'),
    'frac_award': ('{frac} of the backing fixed at {when}', '{frac} of the sum the committee fixed for the project at {when}'),
    'frac_current': ('{frac} of the backing then standing', '{frac} of the sum then fixed for the project'),
    'balance': ('the sum still owing on the backing', 'whatever remained owing to the project'),
    'first_of': ('a sum matching the opening payment made to {proj2}', 'the same figure as the opening cheque that went to {proj2}'),
    'authorised': ('the sum approved for payment at {when}', 'the amount approved at {when}'),
}
PLAIN_DELTA_PHRASES = ('{frac} of the backing as it then stood', '{frac} of the sum then fixed')
# Ways to point at a project with no word of its name: back to the paragraph above, or by its place in the order in
# which the committee backed projects.
ANAPHORS = ('the undertaking named in the paragraph above', 'the applicant considered under the previous item',
            'the venture discussed in the preceding paragraph', 'the matter dealt with in the paragraph before this one',
            'the concern mentioned just above', 'the enterprise referred to in the paragraph above')
ORDINAL_REF = 'the {rank} undertaking to receive the committee’s backing'
RANK_WORDS = ('first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth', 'eleventh',
              'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth', 'eighteenth', 'nineteenth',
              'twentieth', 'twenty-first', 'twenty-second', 'twenty-third', 'twenty-fourth',
              # edition 2 has up to 36 awards in the order of backing
              'twenty-fifth', 'twenty-sixth', 'twenty-seventh', 'twenty-eighth', 'twenty-ninth', 'thirtieth', 'thirty-first',
              'thirty-second', 'thirty-third', 'thirty-fourth', 'thirty-fifth', 'thirty-sixth', 'thirty-seventh',
              'thirty-eighth', 'thirty-ninth', 'fortieth')
LEADS = (
    '{member} had visited {ref} that week and brought back photographs for the file.',
    'A letter had come from the sponsor of {ref} asking when the committee would next meet.',
    'The secretary reminded members that {ref} had been on the agenda before.',
    'A local resident had written to the committee about the noise from {ref}.',
    'The chair mentioned that {ref} was due to be inspected by the county.',
    '{member} reported that the volunteers at {ref} had held a tea afternoon on the Saturday.',
    'The clerk of works at {ref} had telephoned the secretary about the delivery of materials.',
    'There was some talk of how {ref} would look from the road once it was finished.')
# Edition 2: a lead paragraph gets one more sentence, drawn from these frames and pools, so that no two lead paragraphs of a
# dossier are alike. None of them holds a verb of deciding or paying, a form of a project's name or a place in the order.
WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
LEAD_DETAILS = (
    'It was the {nth} matter of the kind raised since the spring.',
    'By the end of the week some {n} members had asked about it.',
    'The note was dated the {day} of the month.',
    '{member2} had made a note of it on the {weekday}.',
    'It had been mentioned at {time} on the {weekday} before the meeting.',
    'The same point had come up {n} times in the last {m} months.',
    'Members were told that {n} people had seen it for themselves.',
    'It was written up in the visitors’ book on the {weekday} of the {season} term.',
    '{member2} said it had been in the local paper on the {weekday}.',
    'A photograph taken on the {weekday} was passed round, one of {n}.',
    'The clerk had logged it as item {n} on the {nth} page of the file.',
    'Someone had left a message about it at {time} on the {weekday}.')
RESTATE_INTROS = (
    'The treasurer’s summary of payments already reported, repeated here for the record only:',
    'For information, the treasurer restated the sums paid since the last summary, none of them new:',
    'The quarterly summary recapitulated payments reported earlier and made no new payment:',
    'The treasurer read out a summary that restates earlier payments, for the record and not as fresh payments:',
    'As a periodic reminder the treasurer repeated these earlier payments, nothing further having been paid:',
    'The treasurer’s periodic summary repeated the following payments, all reported at earlier meetings:',
    'Summary of payments already minuted (a restatement, not a new report):',
    'The treasurer recapped the recent payments, which had all been reported before:')
RESTATE_ENTRIES = ('{money} to {proj}', '{proj}, {money}', '{money} to {proj} (reported at {when})', '{proj} at {money}',
                   'the {money} sent to {proj}', '{proj} ({money})', '{money} for {proj}', '{proj}: {money}')
RESTATE_CLOSINGS = ('No new payment was reported in the summary.', 'Members noted the summary without comment.',
                    'The summary was received and filed.', 'The chair thanked the treasurer for the recapitulation.',
                    'Nothing in the summary called for a decision.', 'The summary was noted for the record.')

STATEMENTS = {
    'total': ('By the end of {when}, the total paid to {proj} was {value}.',
              'The sums paid to {proj} up to and including {when} came to {value} in all.',
              'As at the close of {when}, {value} had been paid to {proj} in total.'),
    'award': ('After {when}, the award in force for {proj} was {value}.',
              'At the close of {when}, {proj} held an award of {value}.',
              'Once {when} had ended, the award standing for {proj} was {value}.'),
    'balance': ('After {when}, the unpaid balance of the award to {proj} was {value}.',
                'Once {when} was over, {value} of the award to {proj} remained unpaid.',
                'At the close of {when}, the award to {proj} stood {value} short of being fully paid.'),
    'count': ('By the end of {when}, {value} payments had been made to {proj}.',
              'The number of payments made to {proj} up to and including {when} was {value}.',
              'As at the close of {when}, {proj} had received {value} payments.'),
    'projects': ('After {when}, {value} projects held an award in force.',
                 'At the close of {when}, the number of projects holding an award was {value}.',
                 'Once {when} had ended, {value} projects stood awarded.'),
    'first': ('The award to {proj} was first paid in full at {value}.',
              'The first time the award to {proj} was fully paid was at {value}.',
              '{proj} had its award fully paid for the first time at {value}.'),
    'largest': ('The largest single payment made to {proj} by the end of {when} was {value}.',
                'Up to and including {when}, no single payment to {proj} exceeded {value}, and one reached it.',
                'As at the close of {when}, the biggest payment made to {proj} was {value}.'),
}
# For each statement kind: the key of the flat dict `state` returns, and the parameters of a query.
QUERIES = {
    'total': {'quantity': 'sum of the payments counting after the meeting', 'subject': 'project', 'until': 'meeting date',
              'key': 'total|<subject>|<until>'},
    'award': {'quantity': 'award in force after the meeting', 'subject': 'project', 'until': 'meeting date',
              'key': 'award|<subject>|<until>'},
    'balance': {'quantity': 'award in force minus the payments counting after the meeting', 'subject': 'project',
                'until': 'meeting date', 'key': 'balance|<subject>|<until>'},
    'count': {'quantity': 'number of payments counting after the meeting', 'subject': 'project',
              'until': 'meeting date', 'key': 'count|<subject>|<until>'},
    'projects': {'quantity': 'number of projects holding an award after the meeting', 'subject': None,
                 'until': 'meeting date', 'key': 'projects||<until>'},
    'first': {'quantity': 'date of the first meeting after which the award was fully paid', 'subject': 'project',
              'until': None, 'key': 'first|<subject>|'},
    'largest': {'quantity': 'largest payment counting after the meeting', 'subject': 'project', 'until': 'meeting date',
                'key': 'largest|<subject>|<until>'},
}

INVENTORY = {      # the tables of the larger edition 2 (the first entries of each are those of edition 1)
    'months': MONTHS, 'places': PLACES2, 'buildings': BUILDINGS2, 'works': WORKS2, 'sponsors': SPONSORS2,
    'alias_forms': ALIAS_FORMS, 'sponsor_alias': SPONSOR_ALIAS, 'date_forms': DATE_FORMS, 'month_year': MONTH_YEAR,
    'when_forms': WHEN_FORMS, 'headings': HEADINGS, 'money_forms': MONEY_FORMS, 'fractions': FRACTIONS,
    'ordinals': ORDINALS, 'raise': RAISE, 'lower': LOWER, 'raise_inf': RAISE_INF, 'lower_inf': LOWER_INF,
    'frames': {k: FRAMES[k] + PLAIN_FRAMES.get(k, ()) for k in FRAMES},
    'amount_phrases': {k: AMOUNT_PHRASES[k] + PLAIN_AMOUNT_PHRASES[k] for k in AMOUNT_PHRASES},
    'delta_phrases': DELTA_PHRASES + PLAIN_DELTA_PHRASES, 'anaphors': ANAPHORS, 'ordinal_ref': ORDINAL_REF,
    'rank_words': RANK_WORDS,
    'restate_intros': RESTATE_INTROS, 'restate_entries': RESTATE_ENTRIES, 'restate_closings': RESTATE_CLOSINGS,
    'statements': STATEMENTS, 'readers': READERS, 'queries': QUERIES}


# ---------- The facts: projects, calendar, event log ----------

BUSINESS = ('intro', 'award', 'defer_award', 'vary', 'fell_vary', 'defer_vary', 'correct', 'rescind', 'authorise', 'outside')
TREASURER = ('pay', 'return', 'restate')
DECIDES = {'intro': False, 'award': True, 'defer_award': False, 'vary': True, 'fell_vary': False, 'defer_vary': False,
           'correct': True, 'rescind': True, 'authorise': False, 'pay': True, 'return': True, 'restate': False,
           'outside': False}
REAL_FRACTIONS = ((1, 2), (1, 4), (1, 5), (1, 10))
UNKNOWN = object()
UNREAD = object()       # the sum of a payment that is attributed but whose amount the subset cannot read: counted, not summed


def draw_calendar(rng, edition=1):
    """One meeting a month for 29 to 32 months (edition 2: 44 to 48), each on a day of its own."""
    count, year, month = rng.randint(*SETTINGS[edition]['meetings']), 2036, rng.randint(1, 12)
    dates = []
    for _ in range(count):
        dates.append(datetime.date(year, month, rng.randint(3, 27)))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return dates


def alias_forms(project):
    parts = {'place': project['place'], 'building': project['building'], 'work': project['work_noun'].lower(),
             'verb': project['work_verb']}
    return [form.format(**parts) for form in ALIAS_FORMS]


def draw_projects(rng, edition=1):
    """18 to 24 projects (edition 2: 30 to 36) with similar names (a hall and a hostel in one lane), each of whose short
    forms fits one project only and is no part of another project's names."""
    pools = SETTINGS[edition]
    count = rng.randint(*pools['projects'])
    for _ in range(2000):
        places = rng.sample(pools['places'], count // 2 + 1)
        projects, seen = [], set()
        for number in range(count):
            place = places[number % len(places)]
            building = rng.choice([b for b in pools['buildings'] if (place, b) not in seen])
            seen.add((place, building))
            noun, verb = rng.choice(pools['works'])
            projects.append({'place': place, 'building': building, 'work_noun': noun, 'work_verb': verb,
                             'full': f'{place} {building.title()} {noun}'})
        pairs = [(p['building'], p['work_noun']) for p in projects] + [(p['work_noun'], p['place']) for p in projects]
        if len(set(pairs)) != len(pairs):
            continue
        sponsors = rng.sample(pools['sponsors'], count)
        for project, sponsor in zip(projects, sponsors):
            project['sponsor'] = sponsor
            project['refs'] = alias_forms(project)
            project['sponsor_ref'] = SPONSOR_ALIAS.format(sponsor=sponsor)
        names = [(i, name.lower()) for i, p in enumerate(projects) for name in [p['full']] + p['refs'] + [p['sponsor_ref']]]
        if any(a != b and x in y for a, x in names for b, y in names):
            continue
        return projects
    raise generate.GenerationError('No set of project names found')


def new_event(events, kind, m, p, projects, dates, **fields):
    event = {'id': f'T{len(events) + 1:04d}', 'kind': kind, 'm': m, 'date': dates[m].isoformat(), 'p': p,
             'subject': projects[p]['full'], 'decides': DECIDES[kind]}
    event.update(fields)
    events.append(event)
    return event


def link(events):
    """Fill in what each event refers to: `refs` (the events it is read through: the award it is a share of, the
    minute it corrects, the instalment it equals), `since` (the earliest meeting at which a missing referent leaves
    its project not established: used only by the legacy reading, which the generator keeps for its own draws) and,
    for payments, `seq` and the events they point at. An event whose referent a subset lacks is left out of the
    reading of that subset. Later corrections, returns and variations are not referents: a subset that lacks one is
    read as a record without it. Beside `refs`, two lists that only the non-legacy reading uses: `named_by` (empty here;
    the text writer fills it in with what fixes how the paragraph names the project) and `amount_by` (for "the k-th
    instalment": the instalments before the k-th, which fix the numbering; the text writer adds what names the other
    project of "the first instalment paid to" it)."""
    by_id = {e['id']: e for e in events}
    mine = {}
    for e in events:
        mine.setdefault(e['p'], []).append(e)
    for items in mine.values():
        items.sort(key=lambda e: (e['m'], e['id']))
        award = next((e for e in items if e['kind'] == 'award'), None)
        pays = [e for e in items if e['kind'] == 'pay']
        for number, pay in enumerate(pays, 1):
            pay['seq'] = number
        for e in items:
            refs, since = [], None
            if e['kind'] in ('vary', 'rescind'):
                refs, since = [award['id']], award['m']
            elif e['kind'] in ('correct', 'return'):
                refs, since = [e['target']], by_id[e['target']]['m']
            elif e['kind'] == 'pay':
                spec = e['spec']
                kind = spec['type']
                if kind == 'first':
                    spec['of'] = pays[0]['id']
                    refs, since = [spec['of']], e['m']
                elif kind == 'ordinal':
                    spec['of'] = pays[spec['k'] - 1]['id']
                    refs, since = [spec['of']], e['m']
                elif kind == 'frac_award':
                    spec['award'] = award['id']
                    refs, since = [award['id']], award['m']
                elif kind in ('frac_current', 'balance'):
                    refs, since = [award['id']], award['m']
                elif kind == 'first_of':
                    other = sorted((x for x in events if x['kind'] == 'pay' and x['subject'] == spec['other']),
                                   key=lambda x: (x['m'], x['id']))[0]
                    spec['of'] = other['id']
                    refs, since = [other['id']], e['m']
                elif kind == 'authorised':
                    refs, since = [spec['auth']], e['m']
            e['refs'] = refs
            e['since'] = since
            e['named_by'] = list(e.get('named_by', ()))
            numbering = [x['id'] for x in pays[:e['spec']['k'] - 1]] if e['kind'] == 'pay' and e['spec']['type'] == 'ordinal' else []
            e['amount_by'] = list(dict.fromkeys(list(e.get('amount_by', ())) + numbering))
    return events


def fraction_of(pair):
    return Fraction(pair[0], pair[1])


def whole(value):
    if isinstance(value, Fraction) and value.denominator == 1:
        return int(value)
    return value


def plus(a, b):
    return UNKNOWN if a is UNKNOWN or b is UNKNOWN else a + b


# ---------- The interpreter ----------

def poisoned(e):
    """Legacy reading only: what an event whose referent is missing leaves not established: the project's award, its
    payments, or both."""
    if e['kind'] in ('vary', 'rescind'):
        return ('award',)
    if e['kind'] == 'correct':
        return ('award',) if e['target_kind'] == 'award' else ('pay',)
    if e['kind'] == 'pay' and e['spec']['type'] in ('frac_award', 'frac_current', 'balance'):
        return ('award', 'pay')
    return ('pay',)


def reading(events):
    """How a subset is read, as the whole minute book: (the events that can be attributed, in their order; the ids of
    the payments among them whose amount cannot be read).

    An event is attributed when what fixes the way its paragraph names its project (`named_by`: the application of a
    sponsor, the awards that fix a place in the order of backing, the lead paragraph) is in the subset and attributed
    itself, and, unless it is a payment, what it refers to (`refs`: the minute it corrects, the payment it returns, the
    award it varies or rescinds) is attributed too: to a fixed point. A payment is attributed without its amount; its
    amount can be read when what it is stated by (`refs`: the award, authorisation or instalment, the first instalment
    of another project) and what fixes its words (`amount_by`: the instalments before the k-th, the naming of another
    project) can be read: for a payment, with an amount that can be read. A payment that is attributed and whose amount cannot be read
    is a payment (the number of payments counts it) with no sum (every other quantity leaves it out)."""
    kept = list(events)
    while True:
        ids = {e['id'] for e in kept}
        now = [e for e in kept if all(n in ids for n in e.get('named_by', ()))
               and (e['kind'] == 'pay' or all(r in ids for r in e.get('refs', ())))]
        if len(now) == len(kept):
            break
        kept = now
    ids = {e['id'] for e in kept}
    pays = [e for e in kept if e['kind'] == 'pay']
    unread = set()
    while True:
        known = {e['id'] for e in kept if e['kind'] != 'pay' or e['id'] not in unread}
        more = {e['id'] for e in pays if e['id'] not in unread
                and not (all(r in known for r in e.get('refs', ())) and all(a in known for a in e.get('amount_by', ())))}
        if not more:
            return kept, frozenset(unread)
        unread |= more


def readable(events):
    """The events of a subset that can be read in full, in their order: those `reading` attributes, without the
    payments whose amount it cannot read."""
    kept, unread = reading(events)
    return [e for e in kept if e['id'] not in unread]


def depends(events):
    """For every event of the list, what it cannot be read without, in three lists of ids of events of the list (direct
    dependencies only; a checker follows them): `name`, what fixes how its paragraph names its project (`named_by`: the
    application that ties a sponsor to a project, every award up to and including the project's own for "the Nth
    undertaking to receive the committee's backing", the event of the lead paragraph a paragraph that points back
    depends on); `amount`, what its sum is stated by reference to (a payment's `refs`: the award, the authorisation, the
    other instalment, and `amount_by`: the instalments that fix the k-th, what names the other project of "the first
    instalment paid to"); `target`, the minute or payment it corrects, returns, varies or rescinds (the `refs` of every
    event but a payment). These are the dependencies `reading` uses when it leaves an event out of a subset. A number of
    payments does not turn on `amount`, and a return and the payment it reverses cancel by design, so a checker that
    closes what a statement turns on closes it under `name` only. Never raises: an event that cannot be read gets
    no dependencies."""
    try:
        ids = {e['id'] for e in events}
    except Exception:  # noqa: BLE001
        return {}
    found = {}
    for e in events:
        try:
            pay = e['kind'] == 'pay'
            lists = {'name': e.get('named_by', ()), 'amount': list(e.get('refs', ()) if pay else ()) + list(e.get('amount_by', ())),
                     'target': () if pay else e.get('refs', ())}
            found[e['id']] = {kind: [r for r in dict.fromkeys(rs) if r in ids and r != e['id']] for kind, rs in lists.items()}
        except Exception:  # noqa: BLE001
            found[e.get('id')] = {'name': [], 'amount': [], 'target': []}
    return found


def _run(events, reader=None, meetings=None, legacy=False, subjects=()):
    """The state after every meeting, from any subset of the event log: (flat dict, problems, resolved amounts).
    A subset is read as if it were the whole minute book: with no reader every event is read as the procedure says
    after the events that refer to something the subset lacks have been left out (`readable` keeps the rest), and what is then
    about a project whose award the subset lacks (the award in force, the balance, the first full payment) is `None`.
    A reader resolves references against what it has (see the readers above) and leaves out what it cannot work out.
    `meetings` is a list of dates or a dict of meeting number to date; `subjects` are projects to give a state to
    even when no event of the subset names them.

    `legacy` is the reading that the generator draws its dossiers under (`Ablation`): a reference to an event the
    subset lacks makes everything of that project from the meeting it touches onward `None`. It is used nowhere else."""
    names = sorted({e['subject'] for e in events if e['kind'] != 'names'} | set(subjects))
    devices = frozenset(DEVICES.get(reader, ())) or (frozenset([reader[5:]]) if reader and reader.startswith('only:') else frozenset())
    # The projects whose award the subset holds (for a reader, whatever it takes for an award): of the others the award
    # in force, the balance and the first full payment are not established.
    awarded = {e['subject'] for e in events if e['kind'] == 'award' or (e['kind'] == 'defer_award' and 'defer_award' in devices)}
    events = [e for e in events if reader != 'full_name_only' or e['kind'] in ('intro', 'restate') or e.get('full', True)]
    dates = {e['m']: e['date'] for e in events}
    last = max(dates) if dates else -1
    if meetings is not None:
        if isinstance(meetings, dict):
            dates = {m: d for m, d in meetings.items() if d is not None}
            last = max(meetings, default=-1)
        else:
            dates = {m: d for m, d in enumerate(meetings) if d is not None}
            last = len(meetings) - 1
    first_meeting = min([0, *dates, *(e['m'] for e in events)])
    unread = frozenset()
    if not legacy and reader is None:
        events, unread = reading(events)
        awarded = {e['subject'] for e in events if e['kind'] == 'award'}
    events = [e for e in events if e['kind'] != 'names']
    present = {e['id']: e for e in events}
    no_corrections, no_rescissions, no_returns = 'corrections' in devices, 'rescissions' in devices, 'returns' in devices
    problems, resolved = [], {}
    bad_award, bad_pay = {}, {}
    corr = {}
    usable = []
    for e in events:
        broken = [r for r in e.get('refs', ()) if r not in present] if reader is None and e['id'] not in unread else []
        if broken:
            if reader is None:
                for table in poisoned(e):
                    (bad_award if table == 'award' else bad_pay)[e['subject']] = min(
                        (bad_award if table == 'award' else bad_pay).get(e['subject'], 10 ** 6), e['since'])
            continue
        usable.append(e)
        if e['kind'] == 'correct' and not no_corrections:
            corr[e['target']] = e['new']
    state = {name: {'award': None, 'at': {}, 'pays': [], 'numbered': [], 'by_id': {}, 'counted': set(),
                    'first': None, 'dead': False, 'dead_award': False, 'dead_pay': False} for name in names}
    flat = {}

    def pay_amount(e, st):
        spec = e['spec']
        kind = spec['type']
        if kind == 'abs':
            return corr.get(e['id'], spec['amount'])
        if kind == 'authorised':
            return present[spec['auth']]['amount'] if spec['auth'] in present else None
        if kind in ('first', 'ordinal'):
            number = 1 if kind == 'first' else spec['k']
            if reader is None:
                found = st['by_id'].get(spec['of'])
            else:
                found = st['numbered'][number - 1] if len(st['numbered']) >= number else None
            return None if found is None else found['amount']
        if kind == 'first_of':
            other = state.get(spec['other'])
            if other is None:
                found = None
            elif reader is None:
                found = other['by_id'].get(spec['of'])
            else:
                found = other['numbered'][0] if other['numbered'] else None
            return None if found is None else found['amount']
        if kind == 'frac_award':
            figure = st['at'].get(spec['award_m'])
            return None if figure is None else figure if figure is UNKNOWN else figure * fraction_of(spec['frac'])
        if st['award'] is None:
            return None
        if kind == 'frac_current':
            return UNKNOWN if st['award'] is UNKNOWN else st['award'] * fraction_of(spec['frac'])
        paid = 0
        for pay in st['pays']:
            if pay['ret'] is None and pay['amount'] is not UNREAD:
                paid = plus(paid, pay['amount'])
        return UNKNOWN if st['award'] is UNKNOWN or paid is UNKNOWN else st['award'] - paid

    def add_pay(st, e, amount, numbered, m):
        pay = {'id': e['id'], 'm': m, 'amount': amount, 'ret': None}
        st['pays'].append(pay)
        st['by_id'][e['id']] = pay
        if numbered:
            st['numbered'].append(pay)
        return pay

    for m in range(first_meeting, last + 1):
        todays = [e for e in usable if e['m'] == m]
        todays.sort(key=lambda e: (e['kind'] in TREASURER, e['id']))
        for e in todays:
            st = state[e['subject']]
            kind = e['kind']
            if kind == 'award':
                figure = corr.get(e['id'], e['amount'])
                st['award'], st['at'][m] = figure, figure
            elif kind == 'defer_award' and 'defer_award' in devices:
                st['award'], st['at'][m] = e['amount'], e['amount']
            elif kind == 'vary' or (kind in ('fell_vary', 'defer_vary') and kind in devices):
                if st['award'] is None:
                    problems.append(f'{e["id"]}: variation without an award')
                elif st['award'] is not UNKNOWN:
                    delta = e['delta']
                    step = delta['money'] if 'money' in delta else st['award'] * fraction_of(delta['frac'])
                    st['award'] = st['award'] + e['sign'] * step
            elif kind == 'rescind' and not no_rescissions:
                if st['pays']:
                    problems.append(f'{e["id"]}: rescission after a payment')
                st['award'] = None
            elif kind == 'authorise' and 'authorise' in devices:
                add_pay(st, e, e['amount'], True, m)
                st['counted'].add(e['id'])
            elif kind == 'pay':
                spec = e['spec']
                if spec['type'] == 'authorised' and spec['auth'] in st['counted']:
                    continue
                if e['id'] in unread:
                    add_pay(st, e, UNREAD, True, m)
                    continue
                amount = pay_amount(e, st)
                if amount is None and reader is not None:
                    continue
                if amount is None:
                    if e['subject'] not in bad_pay and e['subject'] not in bad_award:
                        problems.append(f'{e["id"]}: payment of no amount')
                    if not legacy:
                        continue
                    amount = UNKNOWN
                if st['award'] is None and reader is None:
                    problems.append(f'{e["id"]}: payment without an award')
                if amount is not UNKNOWN:
                    amount = whole(amount)
                    if reader is None and (not isinstance(amount, int) or amount <= 0):
                        problems.append(f'{e["id"]}: payment of {amount}')
                add_pay(st, e, amount, True, m)
                resolved[e['id']] = amount
            elif kind == 'return' and not no_returns:
                found = st['by_id'].get(e['target'])
                if found is not None:
                    found['ret'] = m
            elif kind == 'restate' and reader == 'counts_every_mention':
                for entry in e['entries']:
                    add_pay(st, {'id': f'{e["id"]}/{entry["pay"]}'}, entry['amount'], False, m)
        for name in names:
            st = state[name]
            if reader is None and name in bad_award and m >= bad_award[name]:
                st['dead_award'] = True
            if reader is None and name in bad_pay and m >= bad_pay[name]:
                st['dead_pay'] = True
            st['dead'] = st['dead_award'] or st['dead_pay']
            paid, count, biggest = 0, 0, None
            for pay in st['pays']:
                if pay['ret'] is None:
                    count += 1
                    if pay['amount'] is UNREAD:
                        continue
                    paid = plus(paid, pay['amount'])
                    biggest = UNKNOWN if pay['amount'] is UNKNOWN or biggest is UNKNOWN else (
                        pay['amount'] if biggest is None else max(biggest, pay['amount']))
            award = st['award']
            if award is not UNKNOWN and award is not None:
                award = whole(award)
                if reader is None and (not isinstance(award, int) or award <= 0):
                    problems.append(f'{name}: award of {award} after {dates.get(m)}')
            if paid is not UNKNOWN:
                paid = whole(paid)
                if reader is None and award not in (None, UNKNOWN) and paid > award:
                    problems.append(f'{name}: paid {paid} against an award of {award} after {dates.get(m)}')
            if st['first'] is None and not st['dead'] and award not in (None, UNKNOWN) and paid is not UNKNOWN and paid >= award:
                st['first'] = m
            if m not in dates:
                continue
            date = dates[m]
            no_award, no_pay = st['dead_award'] or (not legacy and name not in awarded), st['dead_pay']
            hide = lambda v, *dead: None if any(dead) or v is UNKNOWN else v  # noqa: E731
            flat[f'total|{name}|{date}'] = hide(paid, no_pay)
            flat[f'award|{name}|{date}'] = hide('none' if award is None else award, no_award)
            flat[f'balance|{name}|{date}'] = hide('none' if award is None else UNKNOWN if paid is UNKNOWN else whole(award - paid),
                                                  no_award, no_pay)
            flat[f'count|{name}|{date}'] = None if no_pay else count
            flat[f'largest|{name}|{date}'] = hide('none' if biggest is None else whole(biggest) if biggest is not UNKNOWN else biggest,
                                                  no_pay)
        if m in dates:
            unknown = any(state[n]['dead_award'] or state[n]['award'] is UNKNOWN for n in names)
            flat[f'projects||{dates[m]}'] = None if unknown else sum(state[n]['award'] is not None for n in names)
    for name in names:
        st = state[name]
        found = dates.get(st['first']) if st['first'] is not None else None
        flat[f'first|{name}|'] = found if st['first'] is not None else None if (
            st['dead'] or (not legacy and name not in awarded)) else 'never'
    return flat, problems, resolved


def state(events, reader=None, meetings=None):
    """The quantities the statements are about, from any subset of the event log, as a flat dict keyed
    `total|<project>|<meeting date>` (also `award`, `balance`, `count`, `largest`), `projects||<date>` and
    `first|<project>|`. `None` means not established (the award, balance or first full payment of a project whose
    award the subset lacks); `'none'` means established that there is no award or no payment, `'never'` that the
    award was never fully paid. `meetings` is the list of meeting dates (ISO), which a subset may not carry. A subset
    is read as the whole minute book, events that cannot be read without an absent one being left out (see
    `answer`). Never raises on a subset."""
    return _run(events, reader, meetings)[0]


def legacy_state(events, meetings=None):
    """`state` under the reading the generator draws its dossiers under (see `_run`, `legacy`): a reference to an event
    the subset lacks makes what depends on it not established. Only `Ablation` uses it, to choose statements exactly
    as before the subset rule of `answer` was written, and the test that measures how much wider it made the sets of
    events a statement turns on. Nothing that reads a key or checks an answer may use it."""
    return _run(events, None, meetings, True)[0]


NONE_VALUE = 'none'
VALUE_FORMS = {'total': 'integer', 'award': 'integer', 'balance': 'integer', 'count': 'integer', 'projects': 'integer',
               'largest': 'integer', 'first': 'date'}


def value_form(query):
    """How the value of a statement is written: `integer` (pounds or a number, digits) or `date` (YYYY-MM-DD)."""
    return VALUE_FORMS[query['kind']]


def calendar_for(query, events):
    """The meeting dates a subset can vouch for, as a dict of meeting number to date, with the date a query names
    added: one meeting a month, so its number follows from the number and date of any event (and is 0 where the
    subset has no event; a date before every meeting of the subset gets a number below the first)."""
    known = {e['m']: e['date'] for e in events}
    until = query.get('until')
    if until is not None:
        if known:
            number, date = next(iter(sorted(known.items())))
            known[number + (int(until[:4]) * 12 + int(until[5:7])) - (int(date[:4]) * 12 + int(date[5:7]))] = until
        else:
            known[0] = until
    return known


def answer(query, events, reader=None):
    """The value a statement is about, from any subset of the events, read as if it were the whole minute book under
    the procedure's own rules: `None` when it is not established, `NONE_VALUE` when it is established that there is
    no value (no award in force, no payment, never fully paid), otherwise a JSON number or an ISO date.

    An event whose own meaning depends on something the subset lacks is left out of the reading: a payment whose
    amount is stated by reference to an absent instalment, award or authorisation (including "the sum authorised at"
    a meeting whose authorisation is absent), a correction or return of an absent payment, a correction, variation or
    rescission of an absent award, and whatever refers to an event so left out. A subset is otherwise the whole
    record: a later correction, return, variation or rescission that is missing is simply not applied, and a payment
    or authorisation that is missing is not there. The value is `None` only where what the statement is about cannot
    be computed from the subset at all: the award in force, the unpaid balance or the first full payment of a
    project whose award the subset lacks. A total paid, a number of payments or a largest payment with no payment
    in the subset is the established "nothing" (0, or `NONE_VALUE`), and the number of projects holding an award is
    never `None`. The flawed readers follow the same rule in their own facts: what they cannot work out they leave
    out, and their award in force, balance or first full payment is not established for a project whose award
    the subset lacks. Never raises."""
    try:
        meetings = calendar_for(query, events)
        subjects = (query['subject'],) if query.get('subject') else ()
        value = _run(events, reader, meetings, False, subjects)[0].get(query['key'])
    except Exception:  # noqa: BLE001 - the checker calls this on arbitrary subsets
        return None
    if value in ('none', 'never'):
        return NONE_VALUE
    if isinstance(value, Fraction):
        return int(value) if value.denominator == 1 else float(value)
    return value


def relevant(query, events, naming=True):
    """Ids of the events a statement can turn on: those of its own subject, everything they refer to (`refs`) and
    everything their naming and their amounts depend on (`named_by`, `amount_by`: the application of a sponsor, the awards
    that fix a place in the order of backing, the lead paragraph, the instalments that fix a number), and what corrects
    or returns any of these. The number of projects holding an award turns on every award and rescission.
    `naming=False` follows `refs` only (what the generator's own `Ablation` uses)."""
    by_id = {e['id']: e for e in events}
    links = ('refs', 'named_by', 'amount_by') if naming else ('refs',)
    if query['kind'] == 'projects':
        ids = {e['id'] for e in events if e['kind'] in ('award', 'rescind')}
    else:
        ids = {e['id'] for e in events if e['subject'] == query['subject']}
    grown = True
    while grown:
        grown = False
        for e in events:
            if e['id'] in ids:
                new = {r for key in links for r in e.get(key, ()) if r in by_id} - ids
            else:
                new = {e['id']} if e.get('target') in ids else set()
            if new:
                ids |= new
                grown = True
    return sorted(ids)


def needed(query, events, within=None):
    """Ids of the events whose removal from `events` changes the answer or makes it not established."""
    full = answer(query, events)
    return [e['id'] for e in events if (within is None or e['id'] in within)
            and answer(query, [x for x in events if x['id'] != e['id']]) != full]


# ---------- Drawing the log ----------

def free_meeting(rng, p, low, high, busy):
    options = [m for m in range(low, high + 1) if (p, m) not in busy]
    return rng.choice(options) if options else None


def draw_delta(rng, small=False):
    if rng.random() < 0.4:
        return {'frac': list(rng.choice(REAL_FRACTIONS[1:] if small else REAL_FRACTIONS))}
    return {'money': 500 * rng.randint(2, 14)}


def add_outside(rng, add, free, award_m, last):
    """Sums given to a project or its sponsor by somebody else, told in words that look like awards and payments."""
    for _ in range(rng.choice((0, 1, 1, 2))):
        at = free(award_m + 1, last) if last > award_m else None
        if at is not None:
            add('outside', at, amount=50 * rng.randint(24, 1200))


def plan_project(rng, p, projects, dates, events, busy):
    """The events of one project, or None when the draw does not fit. `events` holds the projects drawn so far."""
    count = len(dates)
    mine, taken = [], set()

    def add(kind, m, **fields):
        event = new_event(events + mine, kind, m, p, projects, dates, **fields)
        mine.append(event)
        taken.add((p, m))
        return event

    def free(low, high):
        return free_meeting(rng, p, low, high, busy | taken)
    award_m = rng.randint(2, count - 9)
    intro_m = rng.randint(max(0, award_m - 5), award_m - 1)
    amount = 500 * rng.randint(24, 180)
    add('intro', intro_m, sponsor=projects[p]['sponsor'])
    award = add('award', award_m, amount=amount)
    if intro_m <= award_m - 2 and rng.random() < 0.25:
        add('defer_award', rng.randint(intro_m + 1, award_m - 1), amount=amount if rng.random() < 0.5 else amount + 500 * rng.randint(2, 10))
    if rng.random() < 0.12:
        rescind_m = free(award_m + 1, min(count - 1, award_m + 6))
        if rescind_m is None:
            return None
        if rescind_m - 1 > award_m and rng.random() < 0.4:
            fix_m = free(award_m + 1, rescind_m - 1)
            if fix_m is not None:
                add('correct', fix_m, target=award['id'], target_kind='award', target_m=award_m,
                    new=amount + rng.choice((-1, 1)) * 500 * rng.randint(2, 16))
        if rng.random() < 0.3 and rescind_m - 1 > award_m:
            vary_m = free(award_m + 1, rescind_m - 1)
            if vary_m is not None:
                add('vary', vary_m, sign=rng.choice((-1, 1)), delta=draw_delta(rng))
        if rng.random() < 0.3 and rescind_m - 1 > award_m:
            fell_m = free(award_m + 1, rescind_m - 1)
            if fell_m is not None:
                add('fell_vary', fell_m, sign=rng.choice((-1, 1)), delta=draw_delta(rng))
        add('rescind', rescind_m)
        add_outside(rng, add, free, award_m, rescind_m - 1)
        return mine
    pay_months, month = [], award_m
    for _ in range(rng.choice((2, 3, 3, 4, 4, 5))):
        month += rng.randint(1, 4)
        while month < count and (p, month) in busy | taken:
            month += 1
        if month >= count:
            break
        pay_months.append(month)
        taken.add((p, month))
    if len(pay_months) < 2:
        return None
    others = [q for q in range(p) if any(e['kind'] == 'pay' and e['p'] == q for e in events)]
    pays = []
    for number, month in enumerate(pay_months):
        last = number == len(pay_months) - 1
        options = [('abs', 4)]
        if number >= 1:
            options += [('first', 3)]
        if number >= 2:
            options += [('ordinal', 2)]
        options += [('frac_award', 2), ('auth', 2)]
        if number >= 1:
            options += [('frac_current', 1)]
        if last:
            options += [('balance', 14)]
        reachable = [q for q in others if min(x['m'] for x in events if x['kind'] == 'pay' and x['p'] == q) < month]
        if reachable:
            options += [('first_of', 2)]
        kind = rng.choices([o[0] for o in options], [o[1] for o in options])[0]
        spec = {'type': kind}
        if kind == 'abs':
            spec['amount'] = max(1000, 500 * round(amount * rng.uniform(0.12, 0.4) / 500))
        elif kind == 'ordinal':
            spec['k'] = rng.randint(2, number)
        elif kind == 'frac_award':
            spec.update(award_m=award_m, frac=list(rng.choice(REAL_FRACTIONS)))
        elif kind == 'frac_current':
            spec['frac'] = list(rng.choice(REAL_FRACTIONS))
        elif kind == 'first_of':
            spec['other'] = projects[rng.choice(reachable)]['full']
        elif kind == 'auth':
            authorise_m = free(award_m + 1, month - 1)
            if authorise_m is None:
                spec = {'type': 'abs', 'amount': max(1000, 500 * round(amount * rng.uniform(0.12, 0.4) / 500))}
            else:
                note = add('authorise', authorise_m, amount=max(1000, 500 * round(amount * rng.uniform(0.12, 0.4) / 500)))
                spec = {'type': 'authorised', 'auth': note['id']}
        taken.discard((p, month))
        pays.append(add('pay', month, spec=spec))
    if rng.random() < 0.3:
        victim = rng.choice(pays)
        return_m = free(victim['m'] + 1, count - 1)
        if return_m is not None:
            add('return', return_m, target=victim['id'], target_m=victim['m'])
            victim['returned'] = True
    if rng.random() < 0.3:
        fix_m = free(award_m + 1, count - 1)
        if fix_m is not None:
            add('correct', fix_m, target=award['id'], target_kind='award', target_m=award_m,
                new=amount + rng.choice((-1, 1)) * 500 * rng.randint(2, 16))
    if rng.random() < 0.22:
        options = [x for x in pays if x['spec']['type'] == 'abs' and not x.get('returned')]
        if options:
            victim = rng.choice(options)
            fix_m = free(victim['m'] + 1, count - 1)
            if fix_m is not None:
                add('correct', fix_m, target=victim['id'], target_kind='pay', target_m=victim['m'],
                    new=victim['spec']['amount'] + rng.choice((-1, 1)) * 500 * rng.randint(1, 6))
    if rng.random() < 0.4:
        vary_m = free(award_m + 1, count - 1)
        if vary_m is not None:
            add('vary', vary_m, sign=rng.choice((-1, 1)), delta=draw_delta(rng))
    for kind, chance in (('fell_vary', 0.3), ('defer_vary', 0.2)):
        if rng.random() < chance:
            decoy_m = free(award_m + 1, count - 1)
            if decoy_m is not None:
                add(kind, decoy_m, sign=rng.choice((-1, 1)), delta=draw_delta(rng))
    if rng.random() < 0.22:
        decoy_m = free(award_m + 1, count - 2)
        if decoy_m is not None:
            add('authorise', decoy_m, amount=max(1000, 500 * round(amount * rng.uniform(0.1, 0.35) / 500)))
    add_outside(rng, add, free, award_m, count - 1)
    for event in mine:
        event.pop('returned', None)
    return mine


def draw_log(rng, projects, dates):
    """Every project's events, each project drawn so that the whole log is consistent under the procedure."""
    events, busy = [], set()
    for p in range(len(projects)):
        for _ in range(80):
            planned = plan_project(rng, p, projects, dates, events, busy)
            if planned is None:
                continue
            candidate = events + planned
            link(candidate)
            if _run(candidate)[1]:
                continue
            events = candidate
            busy |= {(e['p'], e['m']) for e in planned}
            break
        else:
            return None
    return events


# ---------- Ordinary business: the bulk of every meeting ----------

MEMBERS = ('Mrs Hargreaves', 'Mr Okonkwo', 'Dr Pellew', 'Ms Varga', 'Mr Ainsley', 'Mrs Doherty', 'Cllr Whitcombe',
           'Ms Abara', 'Mr Lindqvist', 'Mrs Tregear', 'Dr Mahmood', 'Mr Fairbairn', 'Ms Quayle', 'Rev Ashdown',
           'Mr Petrakis', 'Mrs Lowther')
OFFICERS = ('Mr Bellamy', 'Ms Rutherford', 'Mrs Castellan', 'Mr Ngata', 'Ms Holloway', 'Dr Vance')
SAID = ('pointed out that', 'observed that', 'reminded members that', 'said that', 'explained that', 'noted that',
        'suggested that', 'was concerned that', 'argued that', 'recalled that', 'wondered whether', 'confirmed that')
OTHER_APPLICANTS = (
    'the Westbrook Allotment Society', 'the Greenway Youth Club', 'the Harrow Lane Mothers and Toddlers Group',
    'the Dunmore Chess Circle', 'the Pennine Walking Network', 'the Linnet Singers', 'the Old Fishermen Reading Group',
    'the Meridian Dance School', 'the Southgate Cycling Cooperative', 'the Bracken Repair Cafe',
    'the Ironmonger Court Tenants', 'the Wavertree Parents Forum', 'the Chapel Row Beekeepers',
    'the Alder Street Men Shed', 'the Saltmarsh Birdwatchers', 'the Fairfield Pensioners Lunch Club')
OTHER_PURPOSES = ('a second-hand minibus', 'a new community oven', 'a programme of evening classes',
                  'a travelling exhibition', 'equipment for a youth orchestra', 'a mobile library trolley',
                  'a summer play scheme', 'repairs to a shared shed', 'a series of cookery workshops',
                  'a loan collection of tools', 'a pilot walking-bus for schools', 'a stage for outdoor plays')
DECLINE_REASONS = ('the application fell outside the fund purposes', 'the sponsor had not supplied its accounts',
                   'the committee felt the work could be done by volunteers', 'a similar scheme already existed nearby',
                   'the form had not been countersigned', 'the need had not been shown to the satisfaction of members',
                   'the project could not start within the period the fund covers')
BODIES = ('the county council community team', 'the Regional Heritage Trust', 'the diocesan property office',
          'the district planning office', 'the Fire and Rescue Service', 'the National Lottery regional office',
          'the university extension unit', 'the housing association forum', 'the local newspaper', 'the health authority',
          'the Chamber of Trade', 'the Rural Community Council', 'the clerk of the parish', 'the charities regulator',
          'the water company', 'the library service')
MATTERS = (
    ('a consultation on community buildings', 'the response period closed at the end of the following month',
     'members agreed to send a short joint reply'),
    ('an invitation to a regional funders meeting', 'it would be held in the county town on a weekday afternoon',
     'the secretary was asked to attend on the committee behalf'),
    ('a change to the guidance on safeguarding', 'every funded body would have to confirm that its volunteers were checked',
     'it was agreed to add a line about this to the monitoring form'),
    ('a request for the committee annual report', 'the report would be quoted in a regional review',
     'the chair undertook to send the latest edition'),
    ('the closure of a road near one of the funded sites', 'diversions would run for about six weeks',
     'the secretary was asked to warn the sponsors concerned'),
    ('an offer of a talk on accessible design', 'the speaker was available on two dates in the spring',
     'the secretary would reply and suggest the later date'),
    ('a questionnaire about volunteer numbers', 'responses were wanted within a fortnight',
     'members thought the questions too detailed and agreed to answer only the first half'),
    ('an enquiry about insurance for grant-funded works', 'its policy now required notice of any building work',
     'the treasurer would check the committee own cover'),
    ('a notice about changes to data protection rules', 'committee records might need a short retention policy',
     'the secretary promised a draft policy for the next meeting'),
    ('a thank-you letter from a sponsor', 'it described the first events held in the restored rooms',
     'the chair would acknowledge it and pass a copy to members'),
    ('a complaint from a neighbour about noise from a funded site', 'it concerned early starts by contractors',
     'the secretary would write to the sponsor and ask for working hours to be respected'),
    ('a proposal for a joint open day', 'several funders would share a stand at the town show',
     'members were willing to take part if no cost fell on the committee'))
TOPICS = (
    (('the monitoring forms sent to sponsors', 'the way sponsors report back on progress'),
     ('the forms were too long for the smaller sponsors to complete', 'two sponsors had returned theirs without a signature',
      'the section on volunteers was rarely filled in', 'a shorter form would make late returns less likely',
      'the forms asked twice for the same site address', 'photographs would help more than a written summary',
      'a reminder by post worked better than one by email', 'the committee should say how the forms were used',
      'one sponsor had written to ask what happened to the forms it sent'),
     ('It was agreed to shorten the form and test it with two sponsors.', 'The secretary would redraft the form for the next meeting.',
      'Members agreed to leave the form as it was for one more round.', 'No change was made, but the matter would be kept under review.')),
    (('publicity for the fund', 'the notice board and newsletter'),
     ('the newsletter reached fewer than half of the households it was meant for', 'the board outside the library was out of date',
      'a short item in the parish magazine had brought two enquiries', 'sponsors might be asked to mention the fund on their own signs',
      'the leaflet still showed the previous chair', 'photographs from the last open day could be reused',
      'the local radio station had offered a slot in the autumn', 'the website page about the fund was hard to find',
      'a stand at the market would cost little and reach a wide audience'),
     ('The secretary was asked to update the board before the next meeting.', 'It was agreed to try the parish magazine for three issues.',
      'A small working party of two members was formed to look at the leaflet.', 'Members asked for a plain list of options at the next meeting.')),
    (('safeguarding and volunteer checks', 'the checks made on volunteers at funded sites'),
     ('a sponsor had asked who was responsible for checking helpers', 'the guidance had changed twice in the last year',
      'smaller sponsors relied on one person who held all the papers', 'the committee needed only a confirmation, not copies of certificates',
      'a training evening had been offered by the county', 'one site ran children sessions in the same rooms as the works',
      'no problems had been reported at any site', 'the wording of the grant conditions on this was out of date'),
     ('The secretary would update the conditions and send them to every sponsor.', 'Members agreed to ask sponsors for a yearly confirmation.',
      'It was agreed to take advice before changing the wording.', 'The item was noted and left open.')),
    (('the schedule of site visits', 'visits to funded sites'),
     ('visits were easier to arrange on weekday mornings', 'two members had never seen the sites they voted on',
      'sponsors liked to know a week ahead', 'a short written note after each visit was enough',
      'the visit to the riverside site had been cut short by rain', 'visits in pairs gave better notes than visits alone',
      'a sponsor had offered tea for any visitors', 'the rota had favoured members who lived nearby',
      'contractors preferred not to be disturbed on delivery days'),
     ('A rota of pairs was drawn up for the coming quarter.', 'It was agreed that each visit would be followed by a one-page note.',
      'The secretary would ask sponsors for suitable dates.', 'Members agreed to review the arrangements in the spring.')),
    (('the committee terms of reference', 'a review of the terms of reference'),
     ('the quorum had not been changed for many years', 'the term of office of members was unclear in the text',
      'the document still referred to a post that no longer existed', 'a member who missed three meetings was not addressed',
      'the rule on declaring interests could be stated more plainly', 'it was unclear who could call an extra meeting',
      'the order of business had drifted from the written one', 'the text spoke of annual meetings, but the committee met monthly'),
     ('A short list of amendments would be prepared by the secretary.', 'Members agreed to read the text again before the next meeting.',
      'It was agreed that no change was urgent.', 'The chair would take advice from the parent charity.')),
    (('training for sponsors', 'a workshop for the sponsors of funded projects'),
     ('sponsors had asked for help with basic bookkeeping', 'an evening session would suit volunteers best',
      'the library annexe could be hired for the occasion', 'a speaker from the county had offered to come',
      'earlier workshops had been well attended by the same few bodies', 'a short guide to contractors contracts was wanted',
      'sponsors from the north of the area found the venue too far', 'food would have to be provided if it ran into the evening'),
     ('It was agreed to hold one session in the spring and judge the response.', 'The secretary would draft an invitation for sponsors.',
      'Members agreed in principle, subject to a suitable date.', 'The item was left to the working party.')),
    (('the annual open day', 'planning for the open day'),
     ('the date clashed with the town regatta', 'stewards were needed for the main hall',
      'last year the displays had been put up too late', 'some sponsors wanted a table of their own',
      'a map showing every funded site would be popular', 'the weather plan needed more thought',
      'the caterers had asked for numbers earlier than before', 'children from a local school might sing'),
     ('The date was fixed provisionally, subject to the hall being free.', 'A planning group of three members was appointed.',
      'It was agreed to ask each sponsor for a single display board.', 'The secretary would write to the sponsors in the autumn.')),
    (('the committee records and filing', 'how the committee papers are kept'),
     ('older papers were stored in a damp cupboard', 'the minute books of earlier years had never been indexed',
      'members received papers late when they were posted', 'electronic copies would save the secretary time',
      'a locked cabinet would be needed for letters from sponsors', 'some sponsor files were thin and others bulky',
      'the retention period for unsuccessful applications was unclear'),
     ('The secretary would report on the cost of a cabinet.', 'It was agreed to scan the older minute books over the winter.',
      'Members asked for a simple list of what was kept where.', 'The matter was deferred to the next meeting.')),
    (('contractors and procurement by sponsors', 'how sponsors choose their contractors'),
     ('sponsors were asked to obtain three quotations where they could', 'one sponsor had used a relative as its builder',
      'the committee should not recommend any firm', 'a list of questions to ask a contractor might help',
      'delays often followed a change of contractor part-way through', 'smaller sponsors were afraid of fixed-price contracts',
      'quotations older than a year were often out of date', 'a sponsor had asked about retention periods in contracts'),
     ('The secretary would prepare a short note for sponsors.', 'Members agreed that the conditions need not change.',
      'It was agreed to raise the matter at the sponsors workshop.', 'No action was thought necessary.')),
    (('accessibility at funded buildings', 'access for disabled visitors'),
     ('several of the older buildings still had steps at the main door', 'a hearing loop had been fitted at only one site',
      'the committee should ask every sponsor what it planned', 'a local access group had offered to inspect',
      'toilets were more often the problem than doors', 'ramps needed a firm handrail to be useful',
      'the guidance on signs had been updated this year', 'a visit by a wheelchair user had been very instructive'),
     ('Members agreed to ask the local access group for an informal survey.', 'It was agreed to add a question on access to the monitoring form.',
      'The secretary would collect leaflets for sponsors.', 'The chair asked members to bear the point in mind at site visits.')),
    (('energy use and heating at funded sites', 'the running costs of the restored buildings'),
     ('several sponsors expected their bills to rise after the works', 'a thermal survey had been offered free by the county',
      'solar panels would help only if the roof faced south', 'a boiler left unserviced was the usual cause of trouble',
      'insulation was cheaper than new heating in most buildings', 'sponsors should be asked for a plan to pay the bills',
      'a community energy group had written to members', 'the committee could share some examples of good practice'),
     ('It was agreed to circulate the county survey offer to every sponsor.', 'Members asked the secretary to find two short case studies.',
      'The item was noted for discussion at the sponsors workshop.', 'No decision was needed, and the item was closed.')),
    (('the fund bank arrangements', 'dealings with the bank'),
     ('the bank had asked for new signature cards', 'online transfers needed two signatories', 'statements arrived late each month',
      'the committee could change to a different account type', 'the treasurer had waited a week for a reply to a letter',
      'the branch in the town was due to close', 'cheques were hardly ever used now'),
     ('The treasurer would ask the bank for a meeting.', 'Members agreed that the present account should be kept for now.',
      'It was agreed to complete the new signature cards.', 'The matter was left with the treasurer.')),
    (('the schedule of meetings', 'meeting dates and venues'),
     ('the room at the library was often double-booked', 'evening meetings suited working members better',
      'a member had asked for papers to be sent a week in advance', 'the winter dates might need to move for the weather',
      'a longer meeting each quarter would let members deal with larger items', 'the secretary had difficulty getting the room key',
      'the school hall had been offered at no charge'),
     ('The present arrangement would continue for the time being.', 'The secretary would book the library room for the coming year.',
      'It was agreed to try the school hall once and see.', 'Members agreed to circulate papers earlier.')),
    (('thanks and recognition of sponsors', 'a small ceremony for sponsors who had finished their works'),
     ('a plaque on each restored building was an old suggestion', 'sponsors liked a certificate more than a plaque',
      'a short ceremony on site would suit the volunteers', 'the local press would probably attend',
      'some sponsors would prefer no fuss at all', 'the committee name should appear on any plaque'),
     ('Members agreed to ask sponsors what they would prefer.', 'The secretary would price a standard plaque.',
      'It was agreed to hold the question over until a project had finished.', 'The chair would draft a form of words.')),
    (('the committee reading list and guidance notes', 'the guidance notes issued to applicants'),
     ('applicants often asked the same three questions', 'the notes did not say how long a decision would take',
      'an example of a good application would help', 'the language of the notes was too formal',
      'the notes should explain what happens to an unsuccessful application', 'a one-page summary could go on the website'),
     ('The secretary would produce a plainer draft.', 'Members agreed to test the new notes on two applicants.',
      'It was agreed to add the expected timetable to the notes.', 'The item was noted.')),
    (('parking and transport at funded sites', 'how visitors reach the funded buildings'),
     ('the bus service to the quay was cut last year', 'there was no parking at one of the older sites', 'a shared minibus might help several sites',
      'cycle racks cost little and were popular', 'neighbours had objected to cars in the lane', 'a map with bus stops would help visitors'),
     ('Members agreed to raise the matter with the county transport officer.', 'The secretary would ask sponsors what they knew about visitor numbers.',
      'It was agreed to take no action at present.', 'The item was passed to the working party.')),
    (('local history and the record of the fund', 'a short history of the fund'),
     ('the fund had been started with a legacy and a public appeal', 'a retired member had offered to write a few pages',
      'old photographs were held by the library', 'the first sponsors had mostly been churches and clubs',
      'a history could be printed for the open day', 'the minute books of the early years were incomplete'),
     ('Members welcomed the offer and asked for an outline.', 'The secretary would find the old photographs.',
      'It was agreed to decide on printing once the text was ready.', 'The chair thanked the member concerned.')))
TOPIC_OPENINGS = ('{who} raised the matter of {title}.', 'Members returned to {title}.', 'The committee considered {title}.',
                  'Under this heading the committee took up {title}.', '{who} introduced the item on {title}.',
                  'The secretary had circulated a note on {title}, which members discussed.',
                  'There was a general discussion of {title}.', 'The next item was {title}.')
GENERIC_STAGES = (
    'the contractor had started on site', 'the scaffolding was in place', 'the planning officer had asked for a revised drawing',
    'an inspection date had been fixed', 'the volunteers had cleared the site over a weekend',
    'the first deliveries of materials had arrived', 'a delay of about three weeks was expected because of the weather',
    'the sponsor newsletter had announced a completion date in the spring', 'a neighbour had asked about parking during the works',
    'the building inspector had signed off the first stage', 'the sponsor had recruited two more volunteers for the works',
    'the sponsor had held a public meeting about the works which twenty people attended')
WORK_STAGES = {
    'Refurbishment': ('the old plaster had been stripped from the walls', 'the decorators were due the following week'),
    'Roof Repair': ('the slates on the north side had been replaced', 'the lead flashing was being renewed'),
    'Extension': ('the foundations for the new wing had been poured', 'the brickwork had reached the first floor'),
    'Rewiring': ('the electrician had found old cabling behind the stage', 'the new consumer unit was fitted'),
    'Solar Installation': ('the panels had been ordered for delivery in the spring', 'the roof was being checked for strength'),
    'Accessible Entrance': ('the ramp had been marked out at the front door', 'the new automatic door was awaiting delivery'),
    'Heating Upgrade': ('the old boiler had been removed', 'pipework was being run under the floor'),
    'Kitchen Refit': ('the new units were stacked in the vestibule', 'the plumber had capped the old supply'),
    'Insulation': ('the loft space had been cleared for the work', 'cavity insulation was done on the east wall'),
    'Flood Defences': ('the flood gates had been measured and ordered', 'a survey of the culvert was complete'),
    'Window Replacement': ('the first six windows had been fitted', 'the joiner was making the oak frames'),
    'Drainage Works': ('a trench had been dug across the yard', 'the soakaway had been inspected'),
    'Car Park': ('the surface had been marked out with pegs', 'kerbs had been laid along the lane'),
    'Garden Project': ('the beds had been dug over by volunteers', 'a path of reclaimed brick had been laid'),
    'Lift Installation': ('the lift shaft had been cut through the back wall', 'the lift car was due from the maker'),
    'Fire Doors': ('the old doors had been taken off their hinges', 'the new door sets were stacked in the porch'),
    'Lighting Upgrade': ('the old fittings had been taken down', 'the new lamps were being tested one room at a time'),
    'Boundary Wall': ('the old coping had been lifted and stacked', 'the new footings had been inspected'),
    'Floor Restoration': ('the boards had been lifted in the main room', 'the sanding was due to start on Monday'),
    'Toilet Block': ('the trenches for the drains had been dug', 'the block walls had reached window height')}
CONTEXT_BEFORE = (
    'The matter had been left open at the previous meeting.', 'Members had before them the papers circulated by the secretary.',
    '{member} spoke first and asked that the sponsor reply be read out.', 'After some discussion of the sponsor plans the chair put the question.',
    'The secretary read the sponsor letter, which said that work was going to plan.', 'Members had visited the site the week before.',
    '{member} declared no interest in the item.', 'The chair reminded members of the conditions attached to the fund.',
    'There was a brief exchange about the timing of the work.', 'The item had been placed first on the agenda at the chair request.',
    '{member} asked whether the sponsor had answered the committee earlier questions, and was told it had.',
    'Members noted the covering note from the sponsor, which was brief.')
CONTEXT_AFTER = (
    'The chair thanked the secretary for the preparation of the papers.', '{member} asked that the sponsor be told promptly.',
    'The secretary would write to the sponsor after the meeting.', 'There was no further discussion.',
    'The sponsor representative, who was in the public seats, was thanked for attending.',
    '{member} asked for the decision to be recorded in the usual form.', 'The item took about ten minutes.',
    'The chair asked members to remember the sponsor in their site visits.', 'Members moved on to the next item.',
    'The item was to be mentioned in the next newsletter.', '{member} voted against, as minuted, and asked that the reason be noted.',
    'The sponsor would be sent an extract of the minute.')
ADMIN = (
    'Bank charges for the month were {n}.', 'The treasurer reported that the printing of the newsletter had cost {n}.',
    'The room hire for the previous meeting came to {n}.', 'The treasurer noted that the committee stationery account stood at {n}.',
    'Postage for the sponsors letters had cost {n}.', 'The cost of the annual audit fee was put at {n}.',
    'The treasurer said that the insurance premium for the committee papers was {n}.',
    'Tea and coffee for the open day were expected to cost {n}.',
    'The treasurer noted that the committee had received {n} in bank interest.',
    'The fee for the committee entry in the charities directory was {n}.')
ADMIN_PLAIN = (
    'The treasurer reported that the bank statements had been reconciled with the ledger.',
    'The treasurer confirmed that the fund accounts were in order and ready for inspection.',
    'The treasurer asked members to return their expenses claims by the end of the month.',
    'The treasurer said that the auditor had asked for the sponsor receipts in a single bundle.',
    'The treasurer reported no change to the committee investment arrangements.',
    'The treasurer noted that two cheques from earlier years remained uncashed and would be cancelled.',
    'The treasurer circulated the quarterly bank statements for members to read.',
    'The treasurer reported that the accounts software had been updated without difficulty.')
AOB = (
    '{member} asked whether the committee could be given a copy of the sponsor list; the secretary said it could.',
    'The chair announced that the committee photograph for the annual report would be taken before the next meeting.',
    '{member} reported that the notice board outside the library had been repaired.',
    'The secretary noted a change of address for the committee, to be shown on the next letters.',
    '{member} thanked the committee for its good wishes during a recent illness.',
    'It was noted that the visit of the county officer had been put back by a fortnight.',
    '{member} asked that the minutes record the committee thanks to the caretaker of the library annexe.',
    'The chair reminded members to return the signed declarations of interest by post.')
TIMES = ('7.55 pm', '8.10 pm', '8.25 pm', '8.40 pm', '8.55 pm', '9.05 pm', '9.20 pm')
SUBHEADS = {
    'attendance': ('Attendance', 'Present and apologies', 'Members present'),
    'arising': ('Matters arising', 'Matters arising from the last meeting', 'Arising'),
    'business': ('Business', 'Items of business', 'Business of the meeting'),
    'treasurer': ("Treasurer's report", 'Finance', 'Report from the treasurer', 'Accounts'),
    'aob': ('Any other business', 'Other business', 'Closing business')}

NUMBER_WORDS = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
                'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen')
TENS = ('', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety')


def below_thousand(n):
    parts = []
    if n >= 100:
        parts.append(NUMBER_WORDS[n // 100] + ' hundred')
        n %= 100
        if n:
            parts.append('and')
    if n >= 20:
        parts.append(TENS[n // 10] + ('-' + NUMBER_WORDS[n % 10] if n % 10 else ''))
    elif n or not parts:
        parts.append(NUMBER_WORDS[n])
    return ' '.join(parts)


def words(n):
    """Whole pounds in English, `twelve thousand five hundred` (`and` before a last part under a hundred)."""
    if n < 1000:
        return below_thousand(n)
    head, tail = divmod(n, 1000)
    text = below_thousand(head) + ' thousand'
    if tail:
        text += (' and ' if tail < 100 else ' ') + below_thousand(tail)
    return text


def money_text(rng, n):
    form = rng.choices(MONEY_FORMS, (5, 2, 3))[0]
    return form.format(digits=f'{n:,}', words=words(n))


def fraction_text(rng, pair):
    return rng.choice([t for t, a, b in FRACTIONS if (a, b) == tuple(pair)])


def ordinal(day):
    suffix = 'th' if 11 <= day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    return f'{day}{suffix}'


def date_text(rng, date):
    form = rng.choice(DATE_FORMS)
    return form.format(d=date.day, od=ordinal(date.day), Month=MONTHS[date.month - 1], Y=date.year,
                       mm=f'{date.month:02d}', dd=f'{date.day:02d}')


def when_text(rng, date):
    kind, form = rng.choice(WHEN_FORMS)
    return form.format(date=date_text(rng, date), monthyear=MONTH_YEAR.format(Month=MONTHS[date.month - 1], Y=date.year))


def cap(text):
    return text[0].upper() + text[1:]


ACTORS = ('The secretary', 'The chair', 'The treasurer', '{member}', 'The vice-chair', '{member}')
WOULD = ('would', 'agreed to', 'offered to', 'was asked to', 'undertook to', 'promised to')
ACTIONS = ('write to the sponsors concerned', 'draft a short note for members', 'find out what the county had done in a similar case',
           'ask the library for the room key', 'collect two comparable examples', 'check the position with the parent charity',
           'bring the file to a later meeting', 'speak to the caretaker about the notice board', 'telephone the county officer',
           'look through the committee papers of earlier years', 'prepare a list of the sponsors affected',
           'send a reminder to every sponsor', 'ask two sponsors for their views', 'report back by email',
           'make enquiries at the town hall', 'find a volunteer to help with the filing', 'check the dates with the sponsors',
           'gather the opinions of the members who were absent')
WHEN_PHRASES = ('before the next meeting', 'within a fortnight', 'by the end of the month', 'once the holiday period was over',
                'after the next site visit', 'as soon as the papers arrived', 'in good time for the following meeting',
                'when the weather improved', 'before the autumn', 'in time for the newsletter', 'during the coming week',
                'at the first convenient opportunity')


def follow_up(rng, ctx):
    actor = rng.choice(ACTORS).format(member=rng.choice(ctx['members']))
    return f'{actor} {rng.choice(WOULD)} {rng.choice(ACTIONS)} {rng.choice(WHEN_PHRASES)}.'


VISIT_REMARKS = ('They were shown round by the sponsor and reported no concerns about safety or tidiness.',
                 'The sponsor provided a short written account of progress, which the visitors took away.',
                 'They noted that the volunteers on site were well organised and that notices were up in the porch.',
                 'The visitors left a note for the secretary listing two small points to raise with the sponsor.',
                 'A neighbour stopped to say that the work was a great improvement on what had been there before.',
                 'They found the site office tidy and the sponsor records easy to follow.',
                 'The visitors were glad to see that the sponsor had kept the committee name on the site board.',
                 'There was a short delay at the gate because the key holder was away, but the visit went ahead.',
                 'They took a few photographs for the committee file with the sponsor permission.',
                 'The visit lasted about an hour and ended with tea in the sponsor meeting room.',
                 'The sponsor asked that members should telephone before a future visit so that a guide could be found.',
                 'They saw nothing that called for any action by the committee.')
DECLINE_FOLLOWUPS = ('The secretary would write to explain and to invite a fresh application.',
                     'The secretary was asked to suggest two other funders who might help.',
                     'Members hoped that the applicant would try again once the position had changed.',
                     'The decision was to be conveyed in a friendly letter from the chair.',
                     'It was agreed that the applicant should be told which parts of the form had caused difficulty.',
                     'The chair said that the committee should explain its reasons in full.',
                     'The applicant would be offered a short meeting with the secretary.',
                     'No further action was taken.')
TAILS = (', and {other} agreed', ', as {sponsor} had also found', ', which had been the experience at {site} too',
         ', though {other} thought the effect would be small', ', a point first raised some {weeks} weeks earlier',
         '; {other} added that {sponsor} had said much the same', ', and the secretary made a note of it',
         ', although {other} doubted that {num} members would agree', ', as a recent visit to {site} had shown',
         '; this was not a new difficulty, {other} said', ', particularly in the {season} months',
         ', and {other} promised to bring an example next time', ', which {sponsor} had mentioned in its last letter',
         '; {other} wanted the point recorded before the item closed', '')
SEASONS = ('winter', 'spring', 'summer', 'autumn')


def fill(rng, ctx, text, speaker=None):
    others = [x for x in ctx['members'] if x != speaker]
    return text.format(other=rng.choice(others), sponsor=rng.choice(ctx['sponsors']), site=rng.choice(ctx['places']),
                       weeks=NUMBER_WORDS[rng.randint(2, 12)], num=NUMBER_WORDS[rng.randint(3, 9)],
                       season=rng.choice(SEASONS))


def topic_paragraph(rng, ctx, m):
    titles, points, outcomes = rng.choice(TOPICS)
    opening = rng.choice(TOPIC_OPENINGS).format(who=rng.choice(ctx['members']), title=rng.choice(titles))
    speakers = rng.sample(ctx['members'], 5)
    chosen = rng.sample(points, rng.randint(2, min(5, len(points))))
    sentences = [opening]
    for speaker, point in zip(speakers, chosen):
        sentences.append(fill(rng, ctx, f'{speaker} {rng.choice(SAID)} {point}{rng.choice(TAILS)}.', speaker))
    if rng.random() < 0.4:
        sentences.append(follow_up(rng, ctx))
    sentences.append(rng.choice(outcomes) if rng.random() < 0.4 else follow_up(rng, ctx))
    return ' '.join(sentences)


REPORT_EVENTS = ('a regional conference on community buildings', 'a training day run by the county', 'a meeting of the parish forum',
                 'a talk by an architect on historic roofs', 'a visit to a comparable fund in the next county',
                 'a workshop on running volunteer teams', 'a seminar on heating old buildings', 'a session on charity governance')
REPORT_LESSONS = ('smaller grants were often better monitored than large ones', 'sponsors valued a single named contact at the committee',
                  'committees elsewhere published their decisions in plain language', 'most delays came from planning permission, not building',
                  'a short progress note each quarter was thought enough', 'site visits were the most useful check of all',
                  'volunteers needed thanks more often than money', 'sponsors who had a clear timetable finished sooner')
REPORT_FOLLOWUPS = ('The chair thanked {member} and asked for a written note.', 'Members agreed to consider the point further.',
                    '{member} offered to share the handouts with anyone interested.', 'No action was proposed.',
                    'The secretary would file the papers with the committee records.')


def report_paragraph(rng, ctx, m):
    member = rng.choice(ctx['members'])
    other = rng.choice([x for x in ctx['members'] if x != member])
    lessons = rng.sample(REPORT_LESSONS, 2)
    return ' '.join((rng.choice((f'{member} reported on {rng.choice(REPORT_EVENTS)}, which {member} had attended on behalf of the committee.',
                                 f'{member} gave a short account of {rng.choice(REPORT_EVENTS)}.')),
                     f'The main lessons were that {lessons[0]} and that {lessons[1]}.',
                     rng.choice(REPORT_FOLLOWUPS).format(member=other)))


def correspondence_paragraph(rng, ctx, m):
    count = rng.randint(1, 3)
    sentences = []
    for body, (matter, detail, response) in zip(rng.sample(BODIES, count), rng.sample(MATTERS, count)):
        sentences.append(rng.choice((
            f'A letter was received from {body} about {matter}; it said that {detail}, and {response}.',
            f'The secretary reported correspondence from {body} concerning {matter}. It explained that {detail}. After a short discussion {response}.',
            f'{cap(body)} had written about {matter}, noting that {detail}; {response}.')))
    return ' '.join(sentences)


def progress_paragraph(rng, ctx, m):
    active = ctx['active'](m)
    if not active:
        return None
    sentences = []
    for p in rng.sample(active, min(len(active), rng.randint(2, 5))):
        stages = [rng.choice(GENERIC_STAGES), rng.choice(WORK_STAGES[ctx['projects'][p]['work_noun']])]
        who = rng.choice(('the site manager', 'the sponsor secretary', 'the volunteer coordinator', 'the clerk of works', 'the sponsor chair'))
        ref = ctx['ref'](rng, p, m)
        sentences.append(rng.choice((
            f'A progress note from {who} said that, at {ref}, {stages[0]} and {stages[1]}.',
            f'{cap(who)} told the secretary that {stages[1]} at {ref}; in addition, {stages[0]}.',
            f'Members read a note on {ref}: {stages[0]}, and {stages[1]}.',
            f'The secretary summarised the latest news from {ref}, where {stages[1]}.')))
    return ' '.join(sentences)


def visit_paragraph(rng, ctx, m):
    active = ctx['active'](m)
    if not active:
        return None
    p = rng.choice(active)
    ref = ctx['ref'](rng, p, m)
    a, b = rng.sample(ctx['members'], 2)
    stage = rng.choice(WORK_STAGES[ctx['projects'][p]['work_noun']])
    return rng.choice((
        f'{a} and {b} visited {ref} on a weekday morning and found that {stage}. {rng.choice(VISIT_REMARKS)}',
        f'{a} reported on a visit to {ref}, which had been made with {b}. The visitors saw that {stage}. {rng.choice(VISIT_REMARKS)}',
        f'A site visit to {ref} was reported by {b}, who said that {stage}. {a}, who had also been present, added that the neighbours seemed well disposed. {rng.choice(VISIT_REMARKS)}'))


def declined_paragraph(rng, ctx, m):
    applicant = rng.choice(OTHER_APPLICANTS)
    purpose = rng.choice(OTHER_PURPOSES)
    reason = rng.choice(DECLINE_REASONS)
    first = rng.choice((
        f'The application from {applicant} for {purpose} was considered and declined, because {reason}.',
        f'{cap(applicant)} had asked the committee to support {purpose}, but members declined the request, since {reason}.',
        f'The committee considered an enquiry from {applicant} about {purpose}; no application had been submitted, and in any case {reason}.',
        f'Members discussed the request of {applicant} for {purpose} and decided not to proceed, because {reason}.'))
    return f'{first} {rng.choice(DECLINE_FOLLOWUPS)}'


def declaration_paragraph(rng, ctx, m):
    active = ctx['active'](m)
    if not active:
        return None
    ref = ctx['ref'](rng, rng.choice(active), m)
    who = rng.choice(ctx['members'])
    return rng.choice((
        f'{who} declared an interest in {ref}, having helped its sponsor with a leaflet, and took no part in any discussion of it.',
        f'{who} reminded the meeting of a standing interest in {ref}; the chair noted it and asked that it be recorded.',
        f'Before the business began {who} declared that a relative served on the sponsor board of {ref}.'))


def admin_paragraph(rng, ctx, m):
    sentences = []
    for _ in range(rng.randint(1, 3)):
        if rng.random() < 0.5:
            sentences.append(rng.choice(ADMIN).format(n=money_text(rng, rng.randrange(12, 480, 2))))
        else:
            sentences.append(rng.choice(ADMIN_PLAIN))
    return vary(rng, ' '.join(rng.sample(sentences, len(sentences))), 0.5)


def aob_paragraph(rng, ctx, m):
    return ' '.join(rng.choice(AOB).format(member=rng.choice(ctx['members'])) for _ in range(rng.randint(1, 2)))


FIGURE_FRAMES = (
    '{who} told members that its fundraising for {ref} had brought in {money}, mostly from a sale of work and a sponsored walk.',
    'The contractor working at {ref} had quoted {money} for the next stage; members noted the quotation and made no comment on it.',
    'An estimate of {money} for the cost of the planned work at {ref} was circulated for information only.',
    'The insurers valued {ref} at {money} for the purposes of the new policy, which was noted.',
    'A neighbouring business had offered {money} in sponsorship towards {ref}; this was a matter for the sponsor and was noted.',
    'The architect fee for {ref} was put at {money}, according to a letter from the sponsor.',
    'The sponsor of {ref} reported takings of {money} at its summer fair, which members were glad to hear.',
    'A surveyor valued the building at {ref} at {money} before the works began, and the figure was filed.',
    'The sponsor of {ref} reported that its volunteers had saved {money} by doing the clearance work themselves.',
    'A local firm priced the scaffolding for {ref} at {money}, a figure the sponsor passed on for information.',
    'The clerk of works put the cost of an unforeseen drainage repair at {ref} at {money}; no request was made of the committee.',
    'Members noted that the sponsor of {ref} held {money} in its own bank account, raised for the building work.',
    'The sponsor of {ref} said that a legacy of {money} had been left to its building fund.',
    'The auditor note on {ref} gave the cost of the works as {money}, taken from the sponsor own books.',
    'A quotation of {money} for new signage at {ref} was shown to members.',
    'The sponsor reported {money} raised at a charity auction for {ref}.')


def figure_paragraph(rng, ctx, m):
    active = ctx['active'](m)
    if not active:
        return None
    sentences = []
    for p in rng.sample(active, min(len(active), rng.randint(1, 2))):
        sentences.append(rng.choice(FIGURE_FRAMES).format(who=ctx['projects'][p]['sponsor'], ref=ctx['ref'](rng, p, m),
                                                          money=money_text(rng, rng.randrange(1200, 60000, 50))))
    return ' '.join(sentences + [follow_up(rng, ctx)])


FAIR_LINES = (
    'A prize was awarded at the autumn fair to the best loaf, and the judges were thanked.',
    'The stallholders paid the secretary their pitch fees, which were counted and entered in the book.',
    'Members agreed to ask the school choir to sing at the fair, and the secretary would write.',
    'The raffle sent in by the bakery was drawn and the winner was granted a hamper.',
    'A grant of garden tokens was made to the best-kept allotment by the horticultural society, as members heard.',
    'Prizes awarded at the dog show were announced by the vice-chair, who read out the list.',
    'The committee resolved to thank the volunteers who had paid for their own refreshments.',
    'Money sent by the stallholders for the tombola was counted twice by two members, as agreed.',
    'The regional paper awarded its rosette to the stall run by the library friends.',
    'The tea urn was paid for by a member who asked for no thanks, and the chair recorded the gift.',
    'The treasurer reported that the float for the cake stall had been returned in full.',
    'A small grant from the parish towards the marquee was acknowledged with thanks.')


def fair_paragraph(rng, ctx, m):
    lines = rng.sample(FAIR_LINES, rng.randint(2, 4))
    return ' '.join(lines + [follow_up(rng, ctx)])


NOISE_KINDS = ((topic_paragraph, 8), (correspondence_paragraph, 3), (progress_paragraph, 5), (visit_paragraph, 3),
               (declined_paragraph, 2), (report_paragraph, 2), (figure_paragraph, 9), (fair_paragraph, 5))


MARKERS = ('In addition, ', 'On that point, ', 'For the record, ', 'Separately, ', 'At this stage, ', 'As before, ',
           'In general, ', 'Overall, ', 'By way of background, ', 'It was also recorded that ', 'It was noted that ',
           'Members were told that ', 'Briefly, ', 'On reflection, ', 'In the same spirit, ', 'To be clear, ')
STARTERS = ('The ', 'Members ', 'It ', 'They ', 'A ', 'No ', 'There ', 'Several ')


def vary(rng, text, chance=0.4):
    """Prefix some sentences (never the first) with a neutral marker, so that a small pool of stock sentences
    does not repeat word for word."""
    sentences = text.split('. ')
    for number in range(1, len(sentences)):
        if sentences[number].startswith(STARTERS) and rng.random() < chance:
            marker = rng.choice(MARKERS)
            sentences[number] = marker + sentences[number][0].lower() + sentences[number][1:]
    return '. '.join(sentences)


CLOSERS = ('Members agreed that the item should stay on the list.', 'It was resolved that no further action was needed.',
           'Carried: that the secretary note the points raised.', 'The chair said that the committee had agreed on the way forward.',
           'It was agreed that nobody need be paid for the extra work.', 'The committee resolved to take the matter up again in the spring.',
           'The secretary sent a note of the discussion to the members who were away.', 'It was agreed to grant the working party more time.',
           'Members agreed that the notes should be returned to the secretary for filing.', 'It was resolved to leave the matter there.',
           'The vice-chair agreed to chase the reply that had been paid too little attention.',
           'Carried: that the thanks of the committee go to those who had helped.')


# Closing lines for paragraphs that name a project: they decide nothing and use no verb of awarding or paying.
SAFE_CLOSERS = ('Members agreed that the sponsor should be told.', 'The secretary sent the sponsor a note of the discussion.',
                'It was agreed that the report be sent to members who were away.', 'The chair agreed to send a word of thanks to the volunteers.',
                'The secretary agreed to send the photographs on to members.', 'It was agreed that nothing more need be said.')


def noise_paragraph(rng, ctx, m):
    while True:
        make = rng.choices([k for k, _ in NOISE_KINDS], [w for _, w in NOISE_KINDS])[0]
        text = make(rng, ctx, m)
        if text:
            if make in (progress_paragraph, visit_paragraph, figure_paragraph):
                if rng.random() < 0.8:
                    text += ' ' + rng.choice(SAFE_CLOSERS)
            elif rng.random() < 0.9:
                text += ' ' + rng.choice(CLOSERS)
            return vary(rng, text)


# ---------- Rendering ----------

PROCEDURE_BLOCKS = (
    '<a id="PR"></a>\n# Procedure for reading the minute book',
    'This note says how the minutes of the grants committee are read. It is the only authority for how a statement '
    'about the minutes is decided; nothing outside the minute book and this note is to be used. All sums are in pounds. '
    'A sum may be written in figures or in words (£10,000, 10,000 pounds and ten thousand pounds are one sum), and a '
    'fraction may be written in words or as a percentage (a quarter is 25 per cent).',
    'Meetings and dates. The committee meets once in each calendar month, and the minutes of a meeting stand under the '
    'date of that meeting. A meeting may be named by its full date in any usual form (12 May 2036, May 12, 2036, 12th '
    'May 2036 or 2036-05-12) or by its month and year (the May 2036 meeting); both name the same single meeting. '
    '"After a meeting" and "by the end of a meeting" mean once all the business and the treasurer report of that '
    'meeting have been taken. Within a meeting the business is taken first and the treasurer report afterwards. The '
    'minutes record at most one deciding matter for a project at any one meeting.',
    'Projects. A project is named in the minutes by its full name, which is a lane or street, a kind of building and a '
    'kind of work (Corbin Lane Hostel Refurbishment), or by a short form or description that fits that one project and '
    'no other, such as "the hostel refurbishment" or "the Marlow Trust scheme". A phrase of the form "the X scheme" '
    'names the project of the applicant X, from the passage that records the application of X. A phrase that points '
    'back, such as "the undertaking named in the paragraph above" or "the applicant considered under the previous '
    'item", names the one project that the paragraph immediately before the paragraph it stands in names, and is only '
    'used where that paragraph names exactly one project. "The third undertaking to receive the committee\'s backing" '
    'names the project whose award was made third in the order of the minute book (the awards of the minute book are '
    'counted in the order of their meetings, and no award made at the same meeting as another is named this way). Every '
    'award counts in that order, including one that was later rescinded, corrected or varied. The '
    'committee funds '
    'only the projects to which the minutes record an award; an applicant whose application was declined, or who never '
    'applied, holds no award and is not a project in the sense of this note.',
    'Awards. An award is a sum the committee resolves to award to a project. It counts from the meeting that made it. '
    'A recommendation, an application, a request, a letter or a report is not an award, and neither is a proposal that '
    'was deferred or a motion that fell: these decide nothing. A project has at most one award. A project holds an '
    'award in force from the meeting that made it until the meeting that minutes its rescission, if there is one.',
    'Variations. When the committee varies an award, raising or reducing it by a sum or by a stated fraction of the '
    'award, the award in force changes from the meeting that minutes the variation. A fraction is taken of the award '
    'in force immediately before the variation. A variation that was proposed but deferred, or that fell, changes '
    'nothing.',
    'Corrections. When a later minute says that an earlier minute should have read a different figure, the corrected '
    'figure replaces the figure in the earlier minute from the start: the project is treated as if the earlier minute '
    'had always carried the corrected figure, from the meeting of that earlier minute onward, and not only from the '
    'meeting that minutes the correction. Whatever is defined by reference to a corrected figure is read from the '
    'corrected figure, even where that figure was used, in a payment or a variation, at a meeting earlier than the '
    'correction: the correction is read back into it.',
    'Reading by what happened. The minutes say what happened in many ways, and a matter counts by what the minute '
    'records, not by the verb it uses. A proposal minuted as having stood, been adopted, gone through or been settled '
    'on, with or without a vote, is a decision; one minuted as lost, fallen, defeated, put off, held over or '
    'adjourned is not. A sum the committee fixes for a project, whether the minute calls it an award, a grant, backing, '
    'help or support, is the award. A sum recorded in the treasurer\'s part of the minutes (after the business of the '
    'meeting) as having left the fund\'s account for a project, whether by cheque, transfer, credit or otherwise, is a '
    'payment made; a sum authorised or approved for payment later is not a payment. A reference to an earlier sum is to '
    'the sum itself, whatever noun is used: "the first instalment", "the opening payment" and "the opening cheque" are '
    'one thing, as are "the award", "the backing" and "the sum fixed for the project".',
    'Rescissions. When the committee rescinds, withdraws or cancels an award, the project holds no award in force from '
    'the meeting that minutes it. A rescission is only made before any payment to the project has been reported.',
    'Payments. A payment counts from the meeting at which the treasurer reports it made, and not from any earlier '
    'meeting at which it was authorised. A payment authorised but not yet reported made has not been made and counts '
    'for nothing; when a later report says that the payment authorised at a named meeting was made, that report is the '
    'one payment, counted at the meeting of the report, for the sum authorised. A restatement in a treasurer summary '
    'repeats payments already reported at earlier meetings; it is not a payment and is not counted again. A payment '
    'returned or recalled stops counting from the meeting at which the treasurer reports the return; until then it '
    'counts. A return or recall does not change the numbering of the instalments.',
    'Amounts defined by reference. The instalments paid to a project are numbered first, second and so on in the order '
    'in which the treasurer reported them made, whether or not one was later returned. "A sum equal to the first '
    'instalment" (or the second, third and so on) is the sum of that numbered instalment, as corrected if it was '
    'corrected; "the first instalment paid to" another project is the sum of that other project first instalment. '
    '"The sum awarded at" a meeting is the figure of the award made at that meeting to the project the payment goes '
    'to, as corrected, and a later '
    'variation does not change it; a stated fraction of it is taken of that figure. "The award then in force" (or "as '
    'it then stood") is the award in force at the meeting that reports the payment, once the business of that meeting '
    'has been taken. "The balance of the award" is the award in force at the meeting that reports the payment, less '
    'the instalments counting at that meeting before this one (an instalment already returned does not count). "The '
    'sum authorised at" a meeting is the sum of the authorisation made at that meeting for the same project. Wordings that differ '
    'but say the same thing ("a sum equal to" and "the same sum as", "the sum awarded at" and "the award made at") '
    'are read as the same. Every sum is a whole number of pounds.',
    'Statements. A statement in a report about the minute book is decided as follows. "The total paid to a project by '
    'the end of a meeting" is the sum of the payments to it that count at that point. "The number of payments made to '
    'a project" is the number of payments that count at that point, and "the largest single payment" is the greatest '
    'sum among them. "The award in force" for a project after a meeting is the award then in force, with every '
    'correction applied from the start. "The unpaid balance of the award" is the award in force after the meeting less '
    'the total paid to the project by the end of that meeting. "The number of projects holding an award in force" counts '
    'the projects with an award in force after the meeting. An award is "fully paid" at the first meeting by the end of '
    'which the total paid to the project is not less than the award then in force; a later return, rescission or '
    'variation does not undo the fact that it was first fully paid at that meeting. However a statement is worded, "paid", '
    '"received" and "made" refer to the payments that count at that point (a payment already returned is not counted, '
    'whatever the wording), "no single payment exceeded" and "the biggest payment" refer to the same payments, and '
    '"awarded", "stood awarded" and "held an award" mean holding an award in force.',
    'Other figures. The minutes also give figures for the projects that are not awards, variations, corrections or '
    'payments: a quotation from a contractor, an estimate, an insurance valuation, a sum a sponsor has raised itself or '
    'been offered by others. These are neither awards nor payments and change nothing. A payment authorised by the '
    'committee is not the payment reported by the treasurer, and a project is never said to be funded by anything other '
    'than an award. Sums given to a project or to its sponsor by anyone but the committee (a council, a trust, a '
    'lottery, a donor, a church or a business) are neither awards nor payments of the committee, however the minute '
    'words them; the same holds for prizes, raffles and fees at the committee\'s own events.')


PLAIN_KINDS = ('award', 'pay', 'vary', 'correct_award', 'correct_pay', 'rescind', 'return')


def merge(old, new):
    """`old` followed by what `new` adds, each once."""
    return list(dict.fromkeys(list(old) + list(new)))


def sentence(rng, e, ctx, anaphor=None):
    """One event as one sentence of the minutes. `anaphor` points back to the paragraph above instead of naming the
    project; some events are told without the verbs of deciding and paying."""
    kind = e['kind']
    projects, dates = ctx['projects'], ctx['dates']
    proj = anaphor or ctx['ref'](rng, e['p'], e['m'])
    own = None if anaphor else ctx['last_form']
    frames_key = 'pay' if kind == 'pay' else 'correct_award' if kind == 'correct' and e['target_kind'] == 'award' \
        else 'correct_pay' if kind == 'correct' else kind
    plain = frames_key in PLAIN_KINDS and rng.random() < 0.45
    frame = rng.choice(PLAIN_FRAMES[frames_key] if plain else FRAMES[frames_key])
    if own is not None and '{proj}' in frame:
        # How the sentence names its project, and what that depends on (an introduction's frames never use the reference).
        e['named_by'] = merge(e.get('named_by', ()), ctx['depends'](*own))
    fields = {'proj': proj}
    if kind in ('award', 'defer_award', 'authorise', 'outside'):
        fields['money'] = money_text(rng, e['amount'])
    elif kind == 'correct':
        fields.update(when=when_text(rng, dates[e['target_m']]), money=money_text(rng, e['new']))
    elif kind == 'return':
        fields['when'] = when_text(rng, dates[e['target_m']])
    elif kind in ('vary', 'fell_vary', 'defer_vary'):
        delta = e['delta']
        fields['delta'] = money_text(rng, delta['money']) if 'money' in delta else rng.choice(
            PLAIN_DELTA_PHRASES if plain else DELTA_PHRASES).format(frac=fraction_text(rng, delta['frac']))
        if kind == 'vary':
            fields['verb'] = rng.choice(RAISE if e['sign'] > 0 else LOWER)
        else:
            fields['verb'] = rng.choice(RAISE_INF if e['sign'] > 0 else LOWER_INF)
    elif kind == 'intro':
        fields.update(sponsor=e['sponsor'], full=e['subject'])
    elif kind == 'pay':
        spec = e['spec']
        kind_of = spec['type']
        if kind_of == 'abs':
            fields['amt'] = money_text(rng, spec['amount'])
        else:
            phrase = rng.choice((PLAIN_AMOUNT_PHRASES if plain else AMOUNT_PHRASES)[kind_of])
            parts = {}
            if kind_of == 'ordinal':
                parts['ord'] = dict((n, w) for w, n in ORDINALS)[spec['k']]
            if kind_of in ('frac_award', 'frac_current'):
                parts['frac'] = fraction_text(rng, spec['frac'])
            if kind_of == 'frac_award':
                parts['when'] = when_text(rng, dates[spec['award_m']])
            if kind_of == 'authorised':
                parts['when'] = when_text(rng, dates[ctx['events'][spec['auth']]['m']])
            if kind_of == 'first_of':
                other = next(i for i, p in enumerate(projects) if p['full'] == spec['other'])
                parts['proj2'] = ctx['ref'](rng, other, e['m'])
                e['amount_by'] = merge(e.get('amount_by', ()), ctx['depends'](*ctx['last_form']))
            fields['amt'] = phrase.format(**parts)
    return cap(frame.format(**fields))


def restatement(rng, es, ctx):
    entries = []
    for e in es:
        for entry in e['entries']:
            proj = ctx['ref'](rng, e['p'], e['m'])
            e['named_by'] = merge(e.get('named_by', ()), ctx['depends'](*ctx['last_form']))
            fields = {'proj': proj, 'money': money_text(rng, entry['amount']),
                      'when': when_text(rng, ctx['dates'][ctx['events'][entry['pay']]['m']])}
            entries.append(rng.choice(RESTATE_ENTRIES).format(**fields))
    rng.shuffle(entries)
    text = rng.choice(RESTATE_INTROS) + ' ' + '; '.join(entries) + '.'
    if rng.random() < 0.6:
        text += ' ' + rng.choice(RESTATE_CLOSINGS)
    return text


def decorate(rng, text, ctx):
    before = rng.choice(CONTEXT_BEFORE).format(member=rng.choice(ctx['members'])) if rng.random() < 0.55 else ''
    after = rng.choice(CONTEXT_AFTER).format(member=rng.choice(ctx['members'])) if rng.random() < 0.45 else ''
    return ' '.join(part for part in (before, text, after) if part)


def attendance(rng, ctx):
    present = rng.sample(ctx['members'], rng.randint(7, 11))
    away = [x for x in ctx['members'] if x not in present]
    away = rng.sample(away, min(len(away), rng.randint(1, 3)))
    names = ', '.join(present)
    text = rng.choice((
        f'Present: {ctx["chair"]} (in the chair), {ctx["treasurer"]} (treasurer), {ctx["secretary"]} (secretary), {names}.',
        f'{ctx["chair"]} took the chair. Also present were {ctx["treasurer"]}, the treasurer, {ctx["secretary"]}, who took the minutes, and {names}.',
        f'In the chair: {ctx["chair"]}. Members present: {names}. Officers: {ctx["treasurer"]} and {ctx["secretary"]}.'))
    text += ' ' + rng.choice((f'Apologies were received from {", ".join(away)}.', f'{", ".join(away)} sent their apologies.'))
    text += ' ' + rng.choice(('The chair noted that the meeting was quorate.', 'A quorum was present throughout.',
                              'The meeting opened with a quorum, which was maintained to the close.',
                              'The chair opened the meeting at the usual hour and confirmed the quorum.',
                              'With a quorum in the room the chair called the meeting to order.',
                              'The chair welcomed members and noted that there was a quorum.',
                              'The secretary confirmed that the quorum had been met.',
                              'Business began once the quorum had been counted.'))
    return text


def previous_minutes(rng, ctx, first):
    if first:
        return rng.choice(('This is the first meeting recorded in this volume; the minutes of the last meeting of the previous volume were taken as read.',
                           'The volume opens with this meeting. The earlier minutes were confirmed in the previous volume.'))
    fix = rng.random() < 0.15
    base = rng.choice(('The minutes of the previous meeting were approved as a correct record and signed by the chair.',
                       'The minutes of the last meeting, which had been circulated, were taken as read and approved.',
                       'Members approved the minutes of the previous meeting.',
                       'The chair signed the minutes of the last meeting as a true record.',
                       'No one wished to amend the minutes of the previous meeting, which were approved.',
                       'The secretary had circulated the minutes of the last meeting; they were agreed without change.',
                       'The minutes of the meeting before were read, approved and signed.'))
    if fix:
        base = base.rstrip('.') + f', subject to one correction to the spelling of the name of {rng.choice(ctx["members"])}.'
    return base


def closing(rng, ctx, m):
    text = f'The meeting closed at {rng.choice(TIMES)}.'
    if m + 1 < len(ctx['dates']):
        text += f' The next meeting was fixed for {date_text(rng, ctx["dates"][m + 1])}.'
    return text


def lead_paragraph(rng, ctx, p, m):
    """A paragraph that names exactly one project (p), in ordinary business, for a decision below to point back to.
    Edition 2: with one more sentence, and different from every other lead paragraph of the dossier, at least 40
    characters long."""
    if ctx['edition'] == 2:
        for _ in range(12):
            text = rng.choice(LEADS).format(member=rng.choice(ctx['members']), ref=ctx['ref'](rng, p, m))
            text += ' ' + rng.choice(LEAD_DETAILS).format(
                nth=RANK_WORDS[rng.randrange(20)], n=NUMBER_WORDS[rng.randint(2, 19)], m=NUMBER_WORDS[rng.randint(2, 12)],
                day=ordinal(rng.randint(1, 28)), member2=rng.choice(ctx['members']), weekday=rng.choice(WEEKDAYS),
                time=rng.choice(TIMES), season=rng.choice(SEASONS))
            named = {q for q in range(len(ctx['projects'])) if any(f in text for f in ctx['forms'](q))}
            if named == {p} and len(text) >= 40 and text not in ctx['leads']:
                ctx['leads'].add(text)
                return text
        return None
    for _ in range(6):
        text = rng.choice(LEADS).format(member=rng.choice(ctx['members']), ref=ctx['ref'](rng, p, m))
        named = {q for q in range(len(ctx['projects'])) if any(f in text for f in ctx['forms'](q))}
        if named == {p}:
            return text
    return None


ANAPHORIC = ('award', 'defer_award', 'vary', 'fell_vary', 'defer_vary', 'correct', 'rescind', 'authorise', 'outside',
             'pay', 'return')


def told(rng, ctx, e, m):
    """The paragraphs that tell one event: a lead paragraph and a paragraph that points back to it (for about two
    events in five), or one paragraph. Each paragraph comes with the roles it plays. An event is stated by one
    paragraph (role `states`), and its `named_by` records how that paragraph names the project. A paragraph that points
    back depends on the lead paragraph, which gets an event of its own (`names_event`, kind `names`, role `names`)
    whose `named_by` records how the lead paragraph names the project. A paragraph earns the events it states and no
    other: a return paragraph earns the return only (no wording of a return states a sum), and a correction paragraph
    earns the correction only, not the minute it corrects, which keeps the way its own paragraph names its project; an
    answer that rests on a correction quotes the corrected entry too."""
    roles = [(e['id'], 'states')]
    if e['kind'] in ANAPHORIC and not (e['kind'] == 'pay' and e['spec']['type'] == 'first_of') and rng.random() < 0.4:
        lead = lead_paragraph(rng, ctx, e['p'], m)
        if lead:
            naming = names_event(ctx, e, m)
            e['named_by'] = merge(e.get('named_by', ()), [naming['id']])
            return [(lead, [(naming['id'], 'names')]), (decorate(rng, sentence(rng, e, ctx, rng.choice(ANAPHORS)), ctx), roles)], None
    return None, (sentence(rng, e, ctx), roles)


def names_event(ctx, e, m):
    """The event that stands for the lead paragraph just written for `e` (it names project `e['p']` in the form that
    `ctx['last_form']` holds, the last reference the lead paragraph asked for): it decides nothing, and depends on what
    fixes that form. Appended to the log by `attempt`, after the text is written."""
    naming = {'id': f'N{len(ctx["names_events"]) + 1:03d}', 'kind': 'names', 'm': m, 'date': ctx['dates'][m].isoformat(),
              'p': e['p'], 'subject': ctx['projects'][e['p']]['full'], 'decides': False, 'refs': [], 'since': None,
              'named_by': ctx['depends'](*ctx['last_form']), 'amount_by': []}
    ctx['names_events'].append(naming)
    return naming


def build_meeting(rng, ctx, m, events_m, target, first, subheads):
    """The paragraphs of one meeting as a list of (text, [(event id, role), ...]). Deciding paragraphs are told in the
    order of business; ordinary business is added until the meeting is as long as asked. A unit is a run of
    paragraphs that must stay together."""
    head = [('## ' + rng.choice(HEADINGS).format(date=date_text(rng, ctx['dates'][m])), [])]

    def section(name):
        return [(f'### {rng.choice(SUBHEADS[name])}', [])] if subheads else []
    out = head + section('attendance') + [(attendance(rng, ctx), []), (previous_minutes(rng, ctx, first), [])]
    if rng.random() < 0.25:
        text = declaration_paragraph(rng, ctx, m)
        if text:
            out.append((text, []))
    arising = [(noise_paragraph(rng, ctx, m), []) for _ in range(rng.randint(1, 2))]
    business = []
    deciding = [e for e in events_m if e['kind'] in BUSINESS and e['kind'] != 'intro']
    rng.shuffle(deciding)
    singles = []
    for e in deciding:
        unit, single = told(rng, ctx, e, m)
        if unit:
            business.append(unit)
        else:
            singles.append(single)
    index = 0
    while index < len(singles):
        group = singles[index:index + (2 if rng.random() < 0.25 and index + 1 < len(singles) else 1)]
        index += len(group)
        business.append([(decorate(rng, ' '.join(t for t, _ in group), ctx), [r for _, rs in group for r in rs])])
    treasurer = []
    for _ in range(rng.randint(0, 1)):
        treasurer.append([(admin_paragraph(rng, ctx, m), [])])
    pays = [e for e in events_m if e['kind'] in ('pay', 'return')]
    rng.shuffle(pays)
    singles = []
    for e in pays:
        unit, single = told(rng, ctx, e, m)
        if unit:
            treasurer.append(unit)
        else:
            singles.append(single)
    index = 0
    while index < len(singles):
        group = singles[index:index + (2 if rng.random() < 0.3 and index + 1 < len(singles) else 1)]
        index += len(group)
        pieces = [t for t, _ in group]
        if rng.random() < 0.35:
            pieces.insert(rng.randint(0, len(pieces)), admin_paragraph(rng, ctx, m))
        treasurer.append([(' '.join(pieces), [r for _, rs in group for r in rs])])
    summary = [e for e in events_m if e['kind'] == 'restate']
    if summary:
        roles = [(e['id'], 'states') for e in summary]
        treasurer.insert(rng.randint(0, len(treasurer)), [(restatement(rng, summary, ctx), roles)])
    treasurer.append([(admin_paragraph(rng, ctx, m), [])])
    business += [[(decorate(rng, sentence(rng, e, ctx), ctx), [(e['id'], 'states')])] for e in events_m if e['kind'] == 'intro']
    tail = [(aob_paragraph(rng, ctx, m), [])] if rng.random() < 0.6 else []
    flat = lambda units: [para for unit in units for para in unit]  # noqa: E731
    fixed = sum(len(t) for t, _ in out) + sum(len(t) for t, _ in arising + flat(business) + flat(treasurer) + tail) + 600
    while fixed < target:
        text = noise_paragraph(rng, ctx, m)
        fixed += len(text)
        if rng.random() < 0.3:
            arising.append((text, []))
        else:
            business.insert(rng.randint(0, len(business)), [(text, [])])
    rng.shuffle(business)
    out += section('arising') + arising + section('business') + flat(business) + section('treasurer') + flat(treasurer)
    out += section('aob') + tail + [(closing(rng, ctx, m), [])]
    return out


# ---------- Restatements, ids, statements ----------

PREAMBLE = ('Each statement below concerns the minute book of the grants committee (volumes 1 to 3), read as procedure.md '
            'directs. Where a statement is about a sum of money, its value is a whole number of pounds, written in digits. '
            'Where it is about a number of payments or of projects, its value is a whole number, written in digits. Where it '
            'is about a meeting, its value is the date of that meeting written YYYY-MM-DD. The word none is the value when '
            'the sources establish that there is no such value.')

KINDS = ('total', 'award', 'balance', 'count', 'projects', 'first', 'largest')
MONEY_KINDS = ('total', 'award', 'balance', 'largest')
LINKING_SPECS = ('first', 'ordinal', 'frac_award', 'frac_current', 'balance', 'first_of', 'authorised')
REFERENCE_KEYS = ('target', 'of', 'auth', 'award')


def add_restatements(rng, events, projects, dates):
    """The treasurer's periodic summary, every third meeting: it restates payments reported in the three meetings
    before, at the sum the payment has under the procedure. Not the payments that a correction touches or that
    were returned by then, and not a project that has another deciding matter at that meeting."""
    flat, problems, resolved = _run(events)
    busy = {(e['p'], e['m']) for e in events}
    corrected = {e['target'] for e in events if e['kind'] == 'correct'}
    phase = rng.randint(0, 2)
    for meeting in range(4, len(dates)):
        if (meeting - phase) % 3:
            continue
        window = [e for e in events if e['kind'] == 'pay' and meeting - 3 <= e['m'] < meeting and e['id'] not in corrected
                  and not any(r['kind'] == 'return' and r['target'] == e['id'] and r['m'] <= meeting for r in events)]
        chosen = {}
        for pay in window:
            if (pay['p'], meeting) not in busy and rng.random() < 0.8:
                chosen.setdefault(pay['p'], []).append({'pay': pay['id'], 'amount': resolved[pay['id']]})
        for p in sorted(chosen):
            new_event(events, 'restate', meeting, p, projects, dates, entries=chosen[p])
            busy.add((p, meeting))
    return events


def remap(events):
    """Chronological ids E001, E002, ...: business before the treasurer's report within a meeting."""
    ordered = sorted(events, key=lambda e: (e['m'], e['kind'] in TREASURER, e['id']))
    names = {e['id']: f'E{number:03d}' for number, e in enumerate(ordered, 1)}
    for e in ordered:
        e['id'] = names[e['id']]
        for key in ('target',):
            if key in e:
                e[key] = names[e[key]]
        e['refs'] = [names[r] for r in e['refs']]
        for key in ('named_by', 'amount_by'):
            e[key] = [names[r] for r in e.get(key, ())]
        for entry in e.get('entries', ()):
            entry['pay'] = names[entry['pay']]
        for key in ('of', 'auth', 'award'):
            if key in e.get('spec', {}):
                e['spec'][key] = names[e['spec'][key]]
    return ordered


def claim_key(kind, name, date):
    return {'projects': f'projects||{date}', 'first': f'first|{name}|'}.get(kind, f'{kind}|{name}|{date}')


def good_value(kind, value):
    if kind == 'first':
        return isinstance(value, str) and value != 'never'
    if kind in ('count', 'projects'):
        return isinstance(value, int) and value >= 2
    return isinstance(value, int) and value > 0


def value_text(rng, kind, value):
    if kind in MONEY_KINDS:
        return money_text(rng, value)
    if kind == 'first':
        return when_text(rng, datetime.date.fromisoformat(value))
    return str(value) if value >= 20 or rng.random() < 0.5 else NUMBER_WORDS[value]


def plain_wrong(rng, kind, truth, dates):
    for _ in range(50):
        if kind in MONEY_KINDS:
            value = truth + rng.choice((-1, 1)) * 500 * rng.randint(2, 9)
        elif kind == 'first':
            at = dates.index(truth) + rng.choice((-1, 1)) * rng.randint(2, 6)
            value = dates[at] if 0 <= at < len(dates) else truth
        else:
            value = truth + rng.choice((-1, 1)) * rng.randint(1, 3)
        if value != truth and good_value(kind, value):
            return value
    return None


def make_claim(kind, name, date, value, shape, rng, cites, linked, error=None):
    key = claim_key(kind, name, date)
    text = STATEMENTS[kind][shape['frame']].format(proj=name, when=shape['when'], value=value_text(rng, kind, value))
    return claim(f'minutes_{kind}', text, lambda world, key=key, value=value: world[key] == value, cites,
                 quantity=lambda world, key=key: world[key], stated=value, error=error) | {
        'query': {'kind': kind, 'subject': name if kind != 'projects' else None, 'until': date if kind != 'first' else None,
                  'key': key, 'value': value},
        'second_passage': linked}


def link_events(events):
    return [e for e in events if e['kind'] in ('correct', 'return', 'rescind', 'vary')
            or (e['kind'] == 'pay' and e['spec']['type'] in LINKING_SPECS)]


class Ablation:
    """What a statement turns on, found by cutting each event in turn from the whole log."""

    def __init__(self, events, meetings, resolved):
        self.events, self.meetings, self.resolved = events, meetings, resolved
        self.cache, self.found = {}, {}
        self.linking = {e['id'] for e in link_events(events)}
        self.by_id = {e['id']: e for e in events}

    def without(self, event_id):
        if event_id not in self.cache:
            # The generator chooses its statements under the legacy reading (see `_run`), so that every seed gives the
            # bytes it always gave; nothing that reads a key or checks an answer uses it.
            self.cache[event_id] = legacy_state([e for e in self.events if e['id'] != event_id], self.meetings)
        return self.cache[event_id]

    def needed(self, kind, name, date, value):
        key = claim_key(kind, name, date)
        if key not in self.found:
            query = {'kind': kind, 'subject': name, 'until': date, 'key': key}
            self.found[key] = [i for i in relevant(query, self.events, False) if self.without(i).get(key) != value]
        return self.found[key]

    def second_passage(self, kind, name, date, value):
        """Needs a passage besides the one that states its own subject's figure: a linking event, or any event of
        another subject, is among those it turns on."""
        for i in self.needed(kind, name, date, value):
            if i in self.linking or (name is not None and self.by_id[i]['subject'] != name):
                return True
        return False

    def other_subject(self, kind, name, date, value):
        return name is not None and any(self.by_id[i]['subject'] != name for i in self.needed(kind, name, date, value))

    def unique_largest(self, name, date):
        """No tie for the largest payment that counts at the date: one payment alone decides the value."""
        paid = [e for e in self.events if e['kind'] == 'pay' and e['subject'] == name and e['date'] <= date]
        returned = {e['target'] for e in self.events if e['kind'] == 'return' and e['subject'] == name and e['date'] <= date}
        sums = [self.resolved[e['id']] for e in paid if e['id'] not in returned]
        return bool(sums) and sums.count(max(sums)) == 1


WHY = {}


def kind_counts():
    return {k: 0 for k in KINDS}



def pick_slots(rng, events, projects, dates, truth, readers, cites, resolved):
    meetings = [d.isoformat() for d in dates]
    names = [p['full'] for p in projects]
    events = [e for e in events if e['kind'] != 'names']    # the generator chooses its statements by the facts that decide
    cut = Ablation(events, meetings, resolved)
    devices = {d: state(events, 'only:' + d, meetings) for ds in DEVICES.values() for d in ds}
    first_of = {e['subject']: e['m'] for e in events if e['kind'] == 'pay' and e['spec']['type'] == 'first_of'}
    candidates = []
    for kind in KINDS:
        for name in ([None] if kind == 'projects' else names):
            for date in ([None] if kind == 'first' else meetings):
                key = claim_key(kind, name, date)
                value = truth.get(key)
                if not good_value(kind, value):
                    continue
                theirs = {r: base.get(key) for r, base in readers.items()}
                clean = True
                for reader, parts in DEVICES.items():
                    if theirs[reader] != value:
                        # A reader made of several devices must differ from the truth through exactly one of them.
                        moved = [d for d in parts if devices[d].get(key) != value]
                        clean = clean and len(moved) == 1 and devices[moved[0]].get(key) == theirs[reader]
                if not clean:
                    continue
                candidates.append({'kind': kind, 'name': name, 'date': date, 'value': value, 'key': key,
                                   'wrong': {r for r, v in theirs.items() if v != value},
                                   'valid': {r: v for r, v in theirs.items() if v != value and good_value(kind, v)}})
    rng.shuffle(candidates)
    lures = [c for c in candidates if c['valid']]
    controls = [c for c in candidates if not c['wrong']]
    required = set(READERS[:3])
    controls_required = [c for c in candidates if not (c['wrong'] & required)]
    chosen, pairs, per_name, per_kind, verdicts = [], set(), {}, {}, {}
    caps = {'projects': 4, 'first': 3, 'largest': 4, 'count': 4}

    def pair(c):
        return (c['kind'], c['name'], c['date'] if c['kind'] == 'projects' else None)

    def sound(c):
        """The value turns on at least one event, and on exactly one when it is a largest payment."""
        if c['key'] not in verdicts:
            ok = bool(cut.needed(c['kind'], c['name'], c['date'], c['value']))
            if c['kind'] == 'largest':
                ok = ok and cut.unique_largest(c['name'], c['date'])
            verdicts[c['key']] = ok
        return verdicts[c['key']]

    def allowed(c):
        return (pair(c) not in pairs and (c['name'] is None or per_name.get(c['name'], 0) < 2) and
                per_kind.get(c['kind'], 0) < caps.get(c['kind'], 6) and sound(c))

    def take(c):
        chosen.append(c)
        pairs.add(pair(c))
        per_name[c['name']] = per_name.get(c['name'], 0) + 1
        per_kind[c['kind']] = per_kind.get(c['kind'], 0) + 1
    coverage = {r: 0 for r in readers}

    def count(c):
        for r in c['valid']:
            coverage[r] += 1
    # Statements that turn on another subject's figure come first (at least four), then every kind of statement
    # (three counts over every project among them), then the readers' quotas, then the rest.
    across = 0
    for c in lures:
        if across >= 4 or len(chosen) >= 18:
            break
        if c['kind'] not in ('projects',) and c['name'] in first_of and c['name'] is not None \
                and (c['date'] is None or c['date'] >= dates[first_of[c['name']]].isoformat()) \
                and allowed(c) and c not in chosen and cut.other_subject(c['kind'], c['name'], c['date'], c['value']):
            take(c)
            count(c)
            across += 1
    for kind, least in (('first', 2), ('projects', 3), ('largest', 2), ('count', 2), ('total', 3), ('award', 3), ('balance', 3)):
        for c in lures:
            if least <= 0 or len(chosen) >= 18:
                break
            if c['kind'] == kind and allowed(c) and c not in chosen and per_kind.get(kind, 0) < least + across:
                take(c)
                count(c)
                least -= 1
    for reader in sorted(readers, key=lambda r: r == 'full_name_only'):
        while coverage[reader] < 7 and len(chosen) < 18:
            best, score = None, 0
            for c in lures:
                if reader in c['valid'] and allowed(c) and c not in chosen:
                    gain = sum(1 for r in c['valid'] if coverage[r] < 7)
                    if gain > score:
                        best, score = c, gain
                        if gain >= 2:
                            break
            if best is None:
                break
            take(best)
            count(best)
    for c in lures:
        if len(chosen) >= 18:
            break
        if allowed(c) and c not in chosen:
            take(c)
            count(c)
    if len(chosen) < 18:
        WHY['why'] = 'lures'
        return None
    for c in controls:
        if len(chosen) >= 24:
            break
        if allowed(c) and c not in chosen:
            take(c)
    # `full_name_only` is optional: where too few statements are right for it as well, controls are those that are right
    # for the three required readers, and the dossier carries no `full_name_only` reader.
    with_full = len(chosen) >= 24
    for c in controls_required:
        if len(chosen) >= 24:
            break
        if allowed(c) and c not in chosen:
            take(c)
    if len(chosen) < 24:
        WHY['why'] = 'controls'
        return None
    needed_by = {r: coverage[r] for r in readers}
    slots, linked_count, crossing = [], 0, 0
    for c in chosen:
        kind, name, date, value = c['kind'], c['name'], c['date'], c['value']
        linked = cut.second_passage(kind, name, date, value)
        linked_count += linked
        crossing += cut.other_subject(kind, name, date, value)
        shape = {'frame': rng.randrange(len(STATEMENTS[kind])),
                 'when': when_text(rng, datetime.date.fromisoformat(date)) if date else None}
        true = make_claim(kind, name, date, value, shape, rng, cites, linked)
        if c['valid']:
            options = {}
            for r in sorted(c['valid']):
                options.setdefault(c['valid'][r], r)
            wrong = [make_claim(kind, name, date, v, shape, rng, cites, linked, error=r) for v, r in list(options.items())[:3]]
        else:
            v = plain_wrong(rng, kind, value, meetings)
            if v is None:
                WHY['why'] = 'plain'
                return None
            wrong = [make_claim(kind, name, date, v, shape, rng, cites, linked, error='plain_wrong_value')]
        slots.append((true, wrong))
    if linked_count < 14 or crossing < 4 or sum(c['kind'] == 'projects' for c in chosen) < 3 \
            or not any(c['kind'] == 'first' for c in chosen):
        WHY['why'] = f'linked {linked_count} crossing {crossing} projects {sum(c["kind"] == "projects" for c in chosen)} first {any(c["kind"] == "first" for c in chosen)}'
        return None
    return slots, needed_by, with_full


def pick_slots2(rng, events, projects, dates, truth, readers, cites, resolved):
    """Edition 2: the 24 statements of a report are those of `MIX2` (7 projects, 6 totals, 6 numbers of payments, 2 balances, 2
    largest payments, 1 first full payment): 18 on which a flawed reader is wrong (`MIX2` less `CONTROLS2` of each kind) and 6
    controls on which every reader is right (`CONTROLS2`: 2 projects, 2 totals, 1 number of payments, 1 balance; where too few
    are right for the optional `full_name_only` as well, the controls of the kinds that lack them are right for the three
    required readers, and the dossier carries no `full_name_only` reader). The 7 statements about the number of projects
    are about 7 different meetings, one in each seventh of the period. No two statements are the same statement, and no
    project has more than two."""
    meetings = [d.isoformat() for d in dates]
    names = [p['full'] for p in projects]
    events = [e for e in events if e['kind'] != 'names']    # the generator chooses its statements by the facts that decide
    cut = Ablation(events, meetings, resolved)
    devices = {d: state(events, 'only:' + d, meetings) for ds in DEVICES.values() for d in ds}
    first_of = {e['subject']: e['m'] for e in events if e['kind'] == 'pay' and e['spec']['type'] == 'first_of'}
    candidates = []
    for kind in KINDS:
        for name in ([None] if kind == 'projects' else names):
            for date in ([None] if kind == 'first' else meetings):
                key = claim_key(kind, name, date)
                value = truth.get(key)
                if not good_value(kind, value):
                    continue
                theirs = {r: base.get(key) for r, base in readers.items()}
                clean = True
                for reader, parts in DEVICES.items():
                    if theirs[reader] != value:
                        # A reader made of several devices must differ from the truth through exactly one of them.
                        moved = [d for d in parts if devices[d].get(key) != value]
                        clean = clean and len(moved) == 1 and devices[moved[0]].get(key) == theirs[reader]
                if not clean:
                    continue
                candidates.append({'kind': kind, 'name': name, 'date': date, 'value': value, 'key': key,
                                   'wrong': {r for r, v in theirs.items() if v != value},
                                   'valid': {r: v for r, v in theirs.items() if v != value and good_value(kind, v)}})
    rng.shuffle(candidates)
    lures = [c for c in candidates if c['valid']]
    controls = [c for c in candidates if not c['wrong']]
    required = set(READERS[:3])
    controls_required = [c for c in candidates if not (c['wrong'] & required)]
    lure_quota = {k: MIX2[k] - CONTROLS2.get(k, 0) for k in MIX2}
    chosen, pairs, per_name, verdicts = [], set(), {}, {}
    taken = {'lure': kind_counts(), 'control': kind_counts()}
    strata = set()

    def pair(c):
        return (c['kind'], c['name'], c['date'] if c['kind'] == 'projects' else None)

    def stratum(c):
        return meetings.index(c['date']) * 7 // len(meetings)

    def sound(c):
        """The value turns on at least one event, and on exactly one when it is a largest payment."""
        if c['key'] not in verdicts:
            ok = bool(cut.needed(c['kind'], c['name'], c['date'], c['value']))
            if c['kind'] == 'largest':
                ok = ok and cut.unique_largest(c['name'], c['date'])
            verdicts[c['key']] = ok
        return verdicts[c['key']]

    def allowed(c, role):
        quota = lure_quota if role == 'lure' else CONTROLS2
        return (pair(c) not in pairs and (c['name'] is None or per_name.get(c['name'], 0) < 2)
                and taken[role][c['kind']] < quota.get(c['kind'], 0)
                and (c['kind'] != 'projects' or stratum(c) not in strata) and sound(c))

    def take(c, role):
        chosen.append(c)
        pairs.add(pair(c))
        per_name[c['name']] = per_name.get(c['name'], 0) + 1
        taken[role][c['kind']] += 1
        if c['kind'] == 'projects':
            strata.add(stratum(c))
    coverage = {r: 0 for r in readers}

    def count(c):
        for r in c['valid']:
            coverage[r] += 1

    def best_of(kind=None, reader=None):
        """The lure of `kind` (or any kind) that a flawed reader gets wrong and that adds most to the readers' quotas."""
        best, score = None, 0
        for c in lures:
            if (kind is None or c['kind'] == kind) and (reader is None or reader in c['valid']) and c not in chosen \
                    and allowed(c, 'lure'):
                gain = sum(1 for r in c['valid'] if coverage[r] < 7)
                if gain > score or best is None:
                    best, score = c, gain
        return best
    # The rarer kinds first, then the statements that turn on another subject's figure (at least four), then the readers'
    # quotas, then the rest of each kind's lures.
    for kind in ('first', 'largest', 'balance'):
        while taken['lure'][kind] < lure_quota[kind]:
            c = best_of(kind)
            if c is None:
                break
            take(c, 'lure')
            count(c)
    across = 0
    for c in lures:
        if across >= 4:
            break
        if c['kind'] != 'projects' and c['name'] in first_of and c['name'] is not None \
                and (c['date'] is None or c['date'] >= dates[first_of[c['name']]].isoformat()) \
                and allowed(c, 'lure') and c not in chosen and cut.other_subject(c['kind'], c['name'], c['date'], c['value']):
            take(c, 'lure')
            count(c)
            across += 1
    for reader in sorted(readers, key=lambda r: r == 'full_name_only'):
        while coverage[reader] < 7 and len(chosen) < 18:
            c = best_of(None, reader)
            if c is None:
                break
            take(c, 'lure')
            count(c)
    for kind in ('projects', 'count', 'total', 'first', 'largest', 'balance'):
        while taken['lure'][kind] < lure_quota[kind]:
            c = best_of(kind)
            if c is None:
                break
            take(c, 'lure')
            count(c)
    if len(chosen) < 18:
        WHY['why'] = 'lures'
        return None
    # `full_name_only` is optional: a control of a kind that has none right for it as well is one right for the three
    # required readers, and then the dossier carries no `full_name_only` reader.
    with_full = True
    for kind, wanted in CONTROLS2.items():
        for pool, full in ((controls, True), (controls_required, False)):
            for c in pool:
                if taken['control'][kind] >= wanted:
                    break
                if c['kind'] == kind and c not in chosen and allowed(c, 'control'):
                    take(c, 'control')
                    with_full = with_full and full
    if len(chosen) < 24:
        WHY['why'] = 'controls'
        return None
    needed_by = {r: coverage[r] for r in readers}
    slots, linked_count, crossing = [], 0, 0
    for c in chosen:
        kind, name, date, value = c['kind'], c['name'], c['date'], c['value']
        linked = cut.second_passage(kind, name, date, value)
        linked_count += linked
        crossing += cut.other_subject(kind, name, date, value)
        shape = {'frame': rng.randrange(len(STATEMENTS[kind])),
                 'when': when_text(rng, datetime.date.fromisoformat(date)) if date else None}
        true = make_claim(kind, name, date, value, shape, rng, cites, linked)
        if c['valid']:
            options = {}
            for r in sorted(c['valid']):
                options.setdefault(c['valid'][r], r)
            wrong = [make_claim(kind, name, date, v, shape, rng, cites, linked, error=r) for v, r in list(options.items())[:3]]
        else:
            v = plain_wrong(rng, kind, value, meetings)
            if v is None:
                WHY['why'] = 'plain'
                return None
            wrong = [make_claim(kind, name, date, v, shape, rng, cites, linked, error='plain_wrong_value')]
        slots.append((true, wrong))
    if linked_count < 14 or crossing < 4:
        WHY['why'] = f'linked {linked_count} crossing {crossing}'
        return None
    return slots, needed_by, with_full




def preflight(case, rng):
    """Dry-run the generator's own choice of altered statements on a copy of the random source, and advance the real
    one until the choice has what the family promises: 8 to 12 altered statements of 24, and at least four rows that
    each flawed reader gets wrong in each report. The real source is left at the state that was checked."""
    for _ in range(600):
        clone = random.Random()
        clone.setstate(rng.getstate())
        try:
            correct, mixed = generate.choose(case, clone)
        except generate.GenerationError:
            correct = None
        if correct is not None and 8 <= sum(a is not b for a, b in zip(correct, mixed)) <= 12:
            fine = True
            for reader in case['readers'].values():
                for items in (correct, mixed):
                    rows = sum(generate.judge(i, reader['base'], {})[0] != generate.judge(i, case['base'], {})[0] for i in items)
                    fine = fine and rows >= 4
            if fine:
                return True
        rng.random()
    return False


def build_context(rng, projects, dates, events, edition=1):
    by_id = {e['id']: e for e in events}
    intro = {e['p']: e['m'] for e in events if e['kind'] == 'intro'}
    award = {e['p']: e['m'] for e in events if e['kind'] == 'award'}
    ctx = {'projects': projects, 'dates': dates, 'events': by_id, 'members': rng.sample(MEMBERS, len(MEMBERS)),
           'sponsors': list(SETTINGS[edition]['sponsors']), 'places': list(SETTINGS[edition]['places']),
           'edition': edition, 'leads': set()}
    officers = rng.sample(OFFICERS, 3)
    ctx.update(chair=officers[0], treasurer=officers[1], secretary=officers[2])

    ranked = sorted((e for e in events if e['kind'] == 'award'), key=lambda e: (e['m'], e['id']))
    rank_of = {e['p']: number for number, e in enumerate(ranked) if sum(x['m'] == e['m'] for x in ranked) == 1}
    ctx['rank_of'] = rank_of
    introduced = {e['p']: e['id'] for e in events if e['kind'] == 'intro'}
    ctx.update(names_events=[], last_form=None)

    def depends(p, form):
        """What fixes a form of reference to project p: nothing for the full name and the short forms (the procedure
        lets a reader resolve them alone: "a short form or description that fits that one project and no other"), the
        application that records its sponsor for `the {sponsor} scheme`, every award up to and including the project's
        own for `the Nth undertaking to receive the committee's backing` (every award counts in that order)."""
        if form == 'sponsor':
            return [introduced[p]]
        if form == 'ordinal':
            return [a['id'] for a in ranked[:rank_of[p] + 1]]
        return []
    ctx['depends'] = depends

    def ordinal_ref(p):
        return ORDINAL_REF.format(rank=RANK_WORDS[rank_of[p]])

    def ref(rng, p, m):
        draw = rng.random()
        sponsor = intro[p] < m
        ranked_ok = p in rank_of and award[p] < m

        def form(kind, text):
            ctx['last_form'] = (p, kind)
            return text
        if draw < 0.25:
            return form('full', projects[p]['full'])
        if draw < 0.5 or not (sponsor or ranked_ok):
            return form('short', rng.choice(projects[p]['refs']))
        if draw < 0.75 and sponsor:
            return form('sponsor', projects[p]['sponsor_ref'])
        if ranked_ok:
            return form('ordinal', ordinal_ref(p))
        return form('sponsor', projects[p]['sponsor_ref']) if sponsor else form('short', rng.choice(projects[p]['refs']))

    def forms(p):
        return [projects[p]['full']] + projects[p]['refs'] + [projects[p]['sponsor_ref']] + (
            [ordinal_ref(p)] if p in rank_of else [])
    ctx['forms'] = forms
    ctx['ref'] = ref
    ctx['active'] = lambda m: [p for p in range(len(projects)) if award[p] < m]
    return ctx


def books_of(edition, count):
    """How many minute books hold `count` meetings: three in edition 1; four, or five from 47 meetings, in edition 2."""
    return SETTINGS[edition]['books'] or 4 + (count >= 47)


def render_book(rng, projects, dates, events, ctx):
    """The volumes (three in edition 1; four or five in edition 2) as lists of blocks (blank-line separated), and the map of
    which block tells which event."""
    count = len(dates)
    total = rng.uniform(*SETTINGS[ctx['edition']]['size'])
    per_meeting = total / count
    books = books_of(ctx['edition'], count)
    size = -(-count // books)
    files, passages, blocks_of = {}, [], {}
    for volume in range(books):
        name = f'minute-book-{volume + 1}.md'
        blocks = [f'<a id="MB{volume + 1}"></a>\n# Grants Committee minute book, volume {volume + 1}']
        for m in range(volume * size, min(count, (volume + 1) * size)):
            subheads = False
            paragraphs = build_meeting(rng, ctx, m, [e for e in events if e['m'] == m], per_meeting * rng.uniform(0.8, 1.2),
                                       m == volume * size, subheads)
            for text, roles in paragraphs:
                grouped = {}
                for event_id, role in roles:
                    grouped.setdefault(role, []).append(event_id)
                for role, ids in grouped.items():
                    passages.append({'file': name, 'paragraph': len(blocks), 'event_ids': ids, 'role': role})
                    if role == 'states':
                        for event_id in ids:
                            blocks_of[event_id] = text
                blocks.append(text)
        files[name] = '\n\n'.join(blocks) + '\n'
    return files, passages, blocks_of


def attempt(rng, edition=1):
    dates = draw_calendar(rng, edition)
    projects = draw_projects(rng, edition)
    events = draw_log(rng, projects, dates)
    if events is None:
        return None
    events = add_restatements(rng, events, projects, dates)
    link(events)
    flat, problems, resolved = _run(events)
    if problems:
        return None
    events = remap(events)
    resolved = _run(events)[2]
    ctx = build_context(rng, projects, dates, events, edition)
    files, passages, blocks_of = render_book(rng, projects, dates, events, ctx)
    for e in events:
        e['full'] = e['kind'] == 'intro' or e['subject'] in blocks_of[e['id']]
    events = events + ctx['names_events']
    sources = {'procedure.md': '\n\n'.join(PROCEDURE_BLOCKS) + '\n'}
    sources.update(files)
    low, high = SETTINGS[edition]['bounds']
    if not low <= sum(len(t) for t in sources.values()) <= high:
        return None
    if edition == 2:
        # every lead paragraph can be quoted: its text occurs once in the whole dossier
        if any(sum(body.count(text) for body in files.values()) != 1 or len(text) < 40 for text in ctx['leads']):
            return None
    meetings = [d.isoformat() for d in dates]
    truth = state(events, None, meetings)
    readers = {r: state(events, r, meetings) for r in READERS}
    books = books_of(edition, len(dates))
    cites = [('procedure.md', 'PR')] + [(f'minute-book-{v}.md', f'MB{v}') for v in range(1, books + 1)]
    picked = (pick_slots if edition == 1 else pick_slots2)(rng, events, projects, dates, truth, readers, cites, resolved)
    if picked is None:
        return None
    slots, coverage, with_full = picked
    if any(coverage[r] < 5 for r in READERS[:3]):
        return None
    chosen = {r: {'base': readers[r], 'unknowns': {}} for r in READERS
              if coverage[r] >= 5 and (with_full or r != 'full_name_only')}
    case = {'title': 'Grants committee minute book review', 'sources': dict(sorted(sources.items())), 'base': truth,
            'unknowns': {}, 'slots': slots, 'readers': chosen,
            'preamble': PREAMBLE if edition == 1 else PREAMBLE.replace('volumes 1 to 3', f'volumes 1 to {books}'),
            'names': {p['full']: [p['full']] for p in projects},
            'events': events, 'passages': passages, 'meetings': meetings,
            'projects': [{'name': p['full'], 'short_forms': p['refs'], 'sponsor': p['sponsor'],
                          'sponsor_form': p['sponsor_ref']} for p in projects]}
    if edition == 2:
        case['size'] = 2
    return case if preflight(case, rng) else None


def build(rng):
    """Use only the caller's random source; return the normal generator contract (plus `events`, `passages`,
    `meetings` and `projects` for a checker). A draw that does not satisfy the family's requirements is redrawn
    from the same source, so the same seed always gives the same bytes."""
    edition = current_edition()
    for _ in range(40 if edition == 1 else 80):
        case = attempt(rng, edition)
        if case is not None:
            return case
    raise generate.GenerationError('The minutes family found no consistent dossier')
