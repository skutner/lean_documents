"""Payment-register layout family, with the reader ``runs_naive_script``.

The settling sentences in conventions.md, verbatim:
Net disbursements for an account are the sum of its entry amounts dated within the reporting period, including both endpoints.
The reporting period is the interval stated in the covering note.
Rows whose Record cell is Subtotal or Carried forward are display totals and are not entries.
An amount enclosed in brackets is negative; a leading minus sign also denotes a negative amount.
Commas in an amount separate thousands, and every amount is in whole euros.
Dates containing slashes are written day/month/year; dates containing hyphens are written year-month-day.
A row whose Record cell says Reissue of followed by an entry reference replaces that entry in full, using the reissue row's own date, account and amount.
Remove replaced entries before selecting dates for the reporting period, and count each remaining entry once.

One or two distinct quirks affect distinct accounts in seven cases out of ten (of
ten dossiers, three use a plain register, four have one affected account and
three have two). The literal naive_figures program runs on the rendered table: it
sums all rows, strips brackets, tries month/day first, and retains replaced
entries. The true totals instead come from structured entries.

Claims. Eight account claims, one per account, each "totalled" or "did not total"
a figure, in both reports. A right "totalled" claim states the true figure. A
right "did not total" claim states a wrong figure: on an affected account the
figure the script prints, elsewhere the figure of an ordinary slip. Every claim
on an affected account is therefore a lure, in both reports. Four claims per
report are never altered: the two unaffected controls (one of each polarity) and
two more unaffected accounts. The other four, which include every affected
account, can be altered; the mixed report alters two to four of them and keeps
each claim's polarity. A wrong figure is the exact value of a named slip:
transposed_digits (two adjacent digits of the true total swapped),
boundary_day_included (the true total plus the entries dated 31 March, the day
before the period) and boundary_day_excluded (the true total less the entries
dated 30 June, the last day). Register rows dated on those two days are ordinary
for every account. On an affected account the altered "totalled" claim states the
script's figure and the altered "did not total" claim denies the true figure;
both are named runs_naive_script. Elsewhere an altered "did not total" claim
denies the true figure and is named denies_true_total. No arbitrary numeric
offsets are used.

Two figures are deliberately not used, because no lure claim states either, so a
claim that does is flagged as no lure by a reader who computes sums it already
has: the prior-quarter sum, and the true total of a neighbouring account (which
is that account's plain sum). In sweeps with the other settings close to those of
the time, the AUC of the cell rates of the reader of the two simple sums (below)
for "which claim is a lure" was 0.66 without them; the prior-quarter slip at
weight 1 in 7 of the slips raised it to 0.72 and the neighbour slip (2 in 6) to
0.75, with 32% of all claims then equal to another account's plain sum and none
of them a lure.

Layout. Every quirk row keeps its own date. The quirk row of the brackets,
subtotal and reissue layouts is chosen among the account's payments already
inside the period; a subtotal row or a reissue row replaces one row of the
account's drawn row count, so affected accounts hold as many rows as the others.
A reissue row sits anywhere after the entry it replaces, with an ordinary date
and an amount drawn from the account's own scale. Ordinary rows have days of
1 to 28; a day of 12 or less appears only where day/month and month/day fall on
the same side of the period, so only the dates quirk (a row written 02/05 or
05/02 that reads as another day on the other side of a boundary) changes sides.
Display rows are numbered in the register's own sequence. Credit notes are
drawn smaller than payments, and a draw in which an account totals zero or less
is drawn again from the same stream.

Measurement over seeds 1..100, twelve dossiers per seed: echo rows have minimum
12, median 24 and maximum 36. All are definite (supported/refuted); undetermined
echo rows have minimum, median and maximum zero. Over seeds 1..1000 (index 1)
58.6% of echo rows are right claims that the script refutes (supported to
refuted) and 41.4% wrong claims that it accepts (refuted to supported); all four
quirks occur in both directions. At index 1, 90 of seeds 1..300 have a plain
register and no echo row (30.0%); over seeds 1..1000, 30.1% do.
Wrong figures over seeds 1..1000, both reports (3,277 wrong claims):
denies_true_total 37.6%, runs_naive_script 25.1%, transposed_digits 22.6%,
boundary_day_excluded 7.7%, boundary_day_included 7.0%.

Cue measurements. A threshold guesser fitted on seeds 1..200 and tested on
201..300 scored 48.5% against a 50.0% majority baseline for the altered report,
67.0% against 70.0% for the dossier's quirk presence, 87.7% against 87.7% for
claim lure presence, and 79.7% against 79.8% for claim error; adding source file
names and lengths gave the same scores except 72.0% against 70.0% for the
dossier's quirk presence (the test prints the current values). The
test permits a twelve-percentage-point excess over the base rate for sampling
variation in its 100 heldout dossier clusters. Per-file lengths: only register.md
varies in length (844 distinct values in 1,000 dossiers). Fitted on seeds 1..500
and tested on 501..1000, a lookup from that length and a threshold on it scored
63.4% and 66.4% against 67.0% for whether the dossier holds a lure, 88.2% and
87.7% against 88.2% for lure claims, and 79.4% and 79.0% against 79.4% for wrong
claims; its AUC is 0.51 or 0.53 for the dossier, 0.50 for the altered report. Every
sentence of the conventions, the covering note and the narrative, and the
register's preamble, is the same in every dossier once dates are masked.

A reader that adds the register twice without any convention (the plain sum of
the period and the plain sum before it) and reads each claim's polarity and the
relation of its figure to those sums, fitted on seeds 1..500 and scored on
501..1000: the best rule over (polarity; equals the plain sum / the prior sum /
neither) scores 88.2% for "which claim is a lure" against 88.2% for the majority
guess; the rule "the figure equals neither sum" has precision 12.0% against a
base rate of 11.8% (recall 50.7%); P(neither | lure) is 0.52 and
P(neither | not a lure) 0.50. The old design scored 93.6% against 87.3%, with
precision 100%. A partial cue remains under the constraints held (a median of at
least 15 echo rows per twelve dossiers, alterations that keep each claim's
polarity, lures in both directions): the plain sum is the flawed reader's own
answer, a right claim that it refutes is a lure by definition, and the correct
report holds no wrong claim to dilute the claims it refutes. Over seeds 1..1000
the lure rate is 6.6% ("totalled", figure equals the plain sum) and 6.7% ("did
not total", figure equals neither) among claims the plain sum supports, and 33.6%
("totalled", neither) and 30.8% ("did not total", equals the plain sum) among
those it refutes, against 12.4% overall. The AUC of the cell rates is 0.70 (old
design 0.94); the best single cell has precision 29.9% and recall 29.2% on 501..1000.
The lure rates by polarity are alike (12.8% and 12.1%). The partial cue is not
removed, and the test bounds it (AUC of at most 0.76, no cell at a lure rate of
one half). For "which claim is wrong" this reader scores 88.2% against a majority
guess of 79.4% (AUC 0.83; old design 93.6% and 0.96): on an account no quirk
affects the plain sum is the true figure, so a reader that adds the rows is right
on every claim but the lures, which is what the controls require.

A reader that also knows the exact list of slips and applies them to the plain
sum finds 0 lures among the 44% of figures a slip explains, and what neither the
plain sum nor a slip of it explains (6.5% of claims, index 1, seeds 1..1000) is
a lure every time: it identifies half of the lures with certainty. This limit is
real and not closed: every ordinary wrong figure is the exact value of a named
slip, so a reader that knows the list can rule them out. A reader that does not
know the list cannot. The test pins the size of this cell.

Other remaining cues: a claim's wrong rate in the mixed report rises with the
number of claims in the report that share its polarity: 0.281 when two do, 0.349,
0.411, 0.447 and 0.448 when six do (seeds 1..1000, AUC 0.547; 0.535 over both
reports); the number of "did not total" claims in a report does not show a quirk
(AUC 0.53 for a quirk, 0.51 for two); the shared prose template, exactly eight
account claims, two fixed controls of opposite polarity and a visible quirk once
the register is read. Once the register is read, a claim on an affected account
is wrong more often than another (0.41 against 0.18 over both reports, 0.83
against 0.35 in the mixed report: every affected account is among the four slots
an alteration can reach). Comparing both reports would reveal altered slots;
staging must continue to withhold the other report. Counting only the rows of an
account that carry no quirk marker gives an affected account one row fewer (AUCs
of 0.44 to 0.45 for the three counts that drop it); counting all of its rows does
not (0.51). Dates, positions and counts of rows per account are otherwise alike
for affected and other accounts (every other AUC between 0.48 and 0.54, seeds
1..300), and restoring a forced 21 May date and a final reissue row dated 23
June raises two of them to 0.70 and 0.83.

Limits: all facts are closed, so no verdict is undetermined. Only whole-euro
account totals for one quarter are tested, not counts, currencies, fractions,
missing records, overlapping replacements or changing units. The optional unit
quirk is omitted. Positive/negative statements replace the sketch's exclusively
positive total claims. The prose is fictional office material, not a sample of
real financial records. No empirical claim about auditing agents follows from
these generator tests. A careful reader could dispute whether this internal
register measures actual bank cash flow; the conventions explicitly define the
narrower register figure. The family stops working when a reader applies the
conventions before summing, normalises the table, or independently checks its
script. A plain twin verifies that normalisation removes the lure. Statistical
cue checks cover the tested guessers, not every possible classifier or access to
both withheld reports.
"""
import datetime

from generate import GenerationError, claim, document, section, table


RULES = (
    'Net disbursements for an account are the sum of its entry amounts dated within the reporting period, including both endpoints.',
    'The reporting period is the interval stated in the covering note.',
    'Rows whose Record cell is Subtotal or Carried forward are display totals and are not entries.',
    'An amount enclosed in brackets is negative; a leading minus sign also denotes a negative amount.',
    'Commas in an amount separate thousands, and every amount is in whole euros.',
    'Dates containing slashes are written day/month/year; dates containing hyphens are written year-month-day.',
    "A row whose Record cell says Reissue of followed by an entry reference replaces that entry in full, using the reissue row's own date, account and amount.",
    'Remove replaced entries before selecting dates for the reporting period, and count each remaining entry once.',
)

ACCOUNTS = ('Buildings', 'Transport', 'Catering', 'Grounds', 'Equipment', 'Training', 'Libraries', 'Housing')
PAYEES = ('Alder Works', 'Bramble Services', 'Cedar Stores', 'Dovetail Supply', 'Elm Workshops',
          'Fallow Trading', 'Gorse Maintenance', 'Hazel Distribution', 'Ilex Partners', 'Juniper Depot')
HEADERS = ('Entry', 'Date', 'Account', 'Payee', 'Amount EUR', 'Record')


def naive_figures(markdown, start, end):
    """The flawed reader's complete program; neither conventions nor row notes are read.

    Inputs start/end are the reporting dates from the covering note. This reader
    understands minus signs and ISO dates, but uses the four specified shortcuts.
    """
    totals = {}
    for line in markdown.splitlines():
        if not line.startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if len(cells) != 6 or cells[0] == 'Entry' or cells[0].startswith('---'):
            continue
        _, date, account, _, amount, _ = cells
        if '/' in date:
            a, b, year = map(int, date.split('/'))
            try:
                date = datetime.date(year, a, b)
            except ValueError:
                date = datetime.date(year, b, a)
        else:
            date = datetime.date.fromisoformat(date)
        value = int(amount.replace(',', '').replace('(', '').replace(')', ''))
        totals.setdefault(account, 0)
        if start <= date <= end:
            totals[account] += value
    return totals


def office_documents(year, start, end):
    """The same procedure and ordinary filing context accompany every layout."""
    conventions = document('Marshwick Community Trust — Register conventions', [
        section('C-01', 'Accounting basis',
                f'Finance Office. Issued 15 January {year}. Owner: Register Services Manager.\n\n'
                + '\n\n'.join(RULES)),
        section('C-02', 'Purpose and custody', """
This instruction governs the quarterly disbursement schedule prepared for the trustees by the
Finance Office. It defines the figure reported from the register, rather than a bank balance or an
expense recognition policy. A payment may relate to work completed in another quarter. That does
not move its register date, and the schedule does not attempt to allocate the cost over the useful
life of the item purchased. The trust keeps its management accounts separately. Reviewers should
use those accounts for questions about depreciation, commitments or the value of goods held in
store. None of those balances forms part of this schedule.

The office retains the register in the order in which the clerks posted the documents. Posting
order can differ from the dates shown in the rows because a site may send its paperwork after
another site has already submitted a later transaction. Entry references are permanent filing
references. They do not encode the month, the account or the value of a payment. Payee names identify
the recipient recorded by the clerk; they are not account names. An account can therefore contain
payments to several suppliers, and a supplier can appear under more than one account. The report
uses the account classification printed on each entry without reallocating expenses by payee.

The paper register was designed for checking vouchers at a desk. Its export preserves features of
that register so that staff can compare the export with a retained printout. Display totals helped
clerks move between pages. Credit notes used the notation selected by the originating office.
Returned payment instructions remained visible after a replacement was issued, allowing a clerk
to find the original voucher when answering a supplier enquiry. These practices explain why a
display line and a payment instruction can occupy similar amounts of space in the exported table.
They do not change the accounting basis above.

Each entry belongs to the account stated on that row. A reissue reference identifies one earlier
entry; it is not a separate credit or an additional settlement of the same instruction. The
retained originals help reconcile correspondence about returned bank mandates. The register
describes the completed internal processing of those instructions. It does not certify the date
on which a recipient's bank made funds available. Requests for bank confirmation go to Treasury
with the entry reference and payee name, rather than with a total copied from this schedule.

The manager keeps the signed instruction with the quarterly working papers. Staff who find a
disagreement between a voucher and the register should record both references in the query book.
They should not erase an old entry or silently change a supplier's name in a circulated copy.
Corrections are posted through the register so that the next person can follow the document trail.
This copy is for the trustee review file and includes the complete register needed for the stated
period. The schedule is confined to the entries supplied here, with no estimated payments or
unposted instructions to be added by the reviewer.
""")])
    covering = document('Marshwick Community Trust — Quarterly working papers', [
        section('N-01', 'Scope of the schedule',
                f'Finance Office. Prepared by Mira Fen, Accounts Supervisor, on 18 July {year}.\n\n'
                f'The reporting period runs from {start.isoformat()} through {end.isoformat()}, inclusive.\n\n'
                """The attached register and the standing conventions form the working papers for the
trustee review. The register includes dates outside the reporting period because the office exports
the surrounding filing run when preparing a quarter. This allows a reviewer to follow a returned
instruction or a late posting without requesting another volume. The scope of the reported figure
remains the reporting period above. The register is complete for that purpose, and the covering
note supplies no separate total to substitute for the entries.

Please retain this note with the exported register when circulating the papers to the committee.
The period narrative explains the work that generated the paperwork and the roles of the offices
that supplied it. It is not a second ledger. The conventions give the basis on which the schedule
is prepared each quarter. The report's account statements concern that schedule alone; they do not
express a view about the quality of a supplier's work, whether a purchase was economical, or whether
an individual voucher satisfied a delegated spending limit. Those questions belong in the service
manager's review and require the purchase order and acceptance record.

The register has been exported as a table to permit routine searches by entry reference and payee.
Finance staff often use the export when a supplier asks which instruction covered an invoice. The
same export supports the quarterly schedule, but the two tasks use different selections of rows.
For an invoice enquiry the clerk follows the individual reference through the file. For a quarterly
account statement the reviewer applies the conventions to the full register and the stated period.
The office has retained the familiar printing layout rather than producing a new spreadsheet for
each recipient of the papers.

The committee secretary requested one observation for each account so that the report can be filed
beside the corresponding service budget. An observation may confirm a proposed figure or record
that the account did not total that figure. Neither wording directs the recipient to approve a
payment. The committee will consider any resulting query at its next ordinary meeting. Suppliers
should continue to send questions through the accounts mailbox, where the correspondence can be
linked to the voucher file before a reply is issued.

Copies for the service managers contain the same source documents as the committee copy. There is
no separate annex of withheld transactions and no outstanding decision about which register issue
applies. The source files preserve account names and entry references so that queries can be
discussed without redistributing bank details. The working papers contain no bank account numbers
or personal reimbursement claims. The records concern the trust's operating purchases and the
related adjustments processed by its offices.
""")])
    narrative = document('Marshwick Community Trust — Period narrative', [
        section('P-01', 'Services and document flow', f'Operations Office. Lena Vale, Business Manager. 19 July {year}.\n\n'
                """The trust runs community rooms, a small library service and temporary accommodation
across its district. During the spring quarter the service teams continued their usual purchasing
rounds while preparing rooms and shared equipment for the summer programme. The work included
building maintenance, transport arrangements, catering supplies and replacement tools. The teams
submitted their documents through the office serving each location, with account names chosen by
the responsible budget holder before the documents reached Finance.

Buildings staff combined small repairs with scheduled inspections. A single supplier could provide
materials for several locations, so its name can appear repeatedly in the register without any
duplication of the instruction. Transport staff arranged deliveries as well as passenger journeys.
Catering documents included supplies for meetings held in community rooms and deliveries to the
accommodation service. Grounds staff scheduled seasonal maintenance around access to the shared
sites. The account headings describe the budget charged, rather than separate suppliers or separate
bank accounts.

Equipment purchases covered replacement items and workshop supplies. The training programme used
both external providers and materials ordered directly by the trust. Libraries continued their
regular stock and maintenance work. Housing staff dealt with routine operating purchases for the
temporary accommodation buildings. Some suppliers served several of these teams. Finance retained
the account selected on the processed instruction so that the quarterly report could be compared
with each manager's own record of activity.

The local offices use the same filing procedure but have retained different printing settings.
Some prepare credit notes on stationery that encloses the amount in brackets. Other offices print
a minus sign. Page totals were introduced when vouchers were checked against bound paper registers;
the export can retain them between batches of instructions. Dates are transcribed from the local
forms using the trust's date convention. These are established office practices, described in the
standing instruction, rather than changes to the way the underlying purchases were authorised.

When a bank mandate is returned, Treasury checks the supplier's details with the requesting office
before issuing another instruction. Finance keeps the first reference so that correspondence about
the return can still be located. The replacement carries its own posting details and names the
reference it replaces. The presence of both references in a filing run therefore preserves the
history of the enquiry. Staff do not remove the original paper voucher from its position merely
because the payment instruction has been replaced.

The business manager asked teams to send their quarterly explanations before the review papers
were assembled. Those explanations describe workloads and timing, not account totals, and are
summarised here to provide context for the committee. A delayed delivery can explain why an invoice
arrived after a job finished, but it does not establish a different date for a register entry. A
supplier enquiry can explain why a reference appears later in the filing run, but the query itself
does not add a disbursement. Finance remains responsible for the recorded amounts and the schedule's
accounting basis.

For the next quarter the offices will continue to file vouchers under their existing references.
The committee secretary will keep questions arising from this review with the circulated working
papers, and the relevant service manager will respond through Finance. This approach allows a
future reader to distinguish an explanation of operational activity from a change posted to the
register. The narrative closes the description of the period; it introduces no additional payment
records and no alternative totals for the accounts under review.
""")])
    return {'conventions.md': conventions, 'covering.md': covering, 'period.md': narrative}


def render_register(rows, year):
    return document('Marshwick Community Trust — Disbursement register', [
        section('R-01', 'Filing copy', f'Finance Office. Export prepared on 17 July {year} by Register Services. '
                'This filing copy records the instructions and adjustments processed for the operating accounts. '
                'Entry references link the exported lines to vouchers held by the office.'),
        section('R-02', 'Register', table(HEADERS, [
            (r['id'], r['date'].strftime('%d/%m/%Y'), r['account'], r['payee'],
             f'({-r["amount"]:,})' if r.get('brackets') else f'{r["amount"]:,}', r['record'])
            for r in rows]))])


# Weights are repeat counts in a draw among the slips that can be named for an
# account. MUTABLE, the number of claims an alteration can reach, was chosen by
# measuring the reader of the two simple sums, see the docstring.
SLIP_WEIGHTS = {'transposed_digits': 2, 'boundary_day_included': 1, 'boundary_day_excluded': 1}
BOUNDARY_SHARE = 0.10
QUIRK_COUNTS = (0, 0, 0, 1, 1, 1, 1, 2, 2, 2)
MUTABLE = 4


def ordinary_date(rng, year):
    """A date in January to June whose day/month swap stays on the same side of the period.

    Days of 12 or less are valid in both orders; only the dates written for a
    layout quirk may change sides when the order is swapped.
    """
    while True:
        month, day = rng.randint(1, 6), rng.randint(1, 28)
        if day > 12 or (month <= 3 and day <= 3) or (month >= 4 and 4 <= day <= 6):
            return datetime.date(year, month, day)


def transpositions(value):
    digits = str(value)
    return sorted({int(digits[:i] + digits[i + 1] + digits[i] + digits[i + 2:])
                   for i in range(len(digits) - 1)
                   if digits[i] != digits[i + 1] and not (i == 0 and digits[1] == '0')})


def draw(rng):
    year = rng.randint(2031, 2039)
    start, end = datetime.date(year, 4, 1), datetime.date(year, 6, 30)
    accounts = list(ACCOUNTS)
    rng.shuffle(accounts)
    count = rng.choice(QUIRK_COUNTS)
    quirk_of = dict(zip(rng.sample(accounts, count), rng.sample(('subtotal', 'brackets', 'dates', 'reissue'), count)))
    boundary = (datetime.date(year, 3, 31), datetime.date(year, 6, 30))
    # Row counts, scales and payees vary before the layout draw, so file size is
    # not a direct quirk flag. A quirk's extra row comes out of the account's
    # drawn row count, so affected accounts are not longer than the others.
    rows, scales = [], {}
    for account in accounts:
        scale = scales[account] = rng.randint(100, 3000)
        extra = quirk_of.get(account) in ('subtotal', 'reissue')
        for _ in range(rng.randint(16, 29) - extra):
            negative = rng.random() < 0.12
            # A credit note is smaller than a typical payment, so account totals stay positive.
            amount = (rng.randint(5, 60) if negative else rng.randint(10, 150)) * scale + rng.randrange(100)
            when = rng.choice(boundary) if rng.random() < BOUNDARY_SHARE else ordinary_date(rng, year)
            rows.append({'id': '', 'date': when, 'account': account, 'payee': rng.choice(PAYEES),
                         'amount': -amount if negative else amount,
                         'record': 'Credit note' if negative else 'Payment'})
    rng.shuffle(rows)

    def position(row):
        return next(i for i, r in enumerate(rows) if r is row)

    for account, quirk in quirk_of.items():
        # The row a layout quirk is placed on keeps its own date, so it is chosen
        # among the account's payments already inside the period, except for the
        # dates quirk, whose marker is the date itself.
        candidates = [r for r in rows if r['account'] == account and r['record'] == 'Payment'
                      and r['date'] not in boundary and (quirk == 'dates' or start <= r['date'] <= end)]
        if not candidates:
            return None
        row = rng.choice(candidates)
        if quirk == 'brackets':
            row.update(amount=-row['amount'], record='Credit note', brackets=True)
        elif quirk == 'dates':
            # Written day/month, the date reads as another day on the other side
            # of a period boundary when month and day are swapped.
            month, day = rng.choice([(m, d) for m in (4, 5, 6) for d in (1, 2, 3)]
                                    + [(m, d) for m in (1, 2, 3) for d in (4, 5, 6)])
            row['date'] = datetime.date(year, month, day)
        elif quirk == 'reissue':
            replacement = {'id': '', 'date': ordinary_date(rng, year), 'account': account, 'payee': row['payee'],
                           'amount': rng.randint(10, 150) * scales[account] + rng.randrange(100),
                           'record': 'Reissue', 'replaces': row}
            rows.insert(rng.randint(position(row) + 1, len(rows)), replacement)
        else:
            # A page carry repeats this account's running figure for the month so
            # far. The copy is a plausible printing artefact, not a result code.
            index = position(row)
            amount = sum(r['amount'] for r in rows[:index + 1]
                         if r['account'] == account and r['date'].month == row['date'].month)
            rows.insert(index + 1, dict(row, id='', amount=amount or row['amount'],
                                        record=rng.choice(('Subtotal', 'Carried forward'))))
    for index, row in enumerate(rows, 1):
        row['id'] = f'P{index:04d}'
    for row in rows:
        if 'replaces' in row:
            row['record'] = 'Reissue of ' + row['replaces']['id']
    # Truth is calculated from the entries, without reparsing the table or
    # delegating its interpretation to the flawed program.
    replaced = {r['replaces']['id'] for r in rows if 'replaces' in r}
    entries = [r for r in rows if r['record'] not in ('Subtotal', 'Carried forward') and r['id'] not in replaced]
    base = {account: sum(r['amount'] for r in entries if r['account'] == account and start <= r['date'] <= end)
            for account in accounts}
    sources = office_documents(year, start, end)
    sources['register.md'] = render_register(rows, year)
    naive = naive_figures(sources['register.md'], start, end)
    if any((naive[a] != base[a]) != (a in quirk_of) for a in accounts) or min(base.values()) <= 0:
        return None
    cites = [('register.md', 'R-02'), ('conventions.md', 'C-01'), ('covering.md', 'N-01')]
    controls = rng.sample([account for account in accounts if account not in quirk_of], 2)
    # Keep both wording shapes non-singleton in either report. Mutations below
    # preserve polarity, so the constraint survives every selection of errors.
    while True:
        polarities = {account: controls.index(account) == 0 if account in controls
                      else rng.choice((True, False)) for account in accounts}
        if 2 <= sum(polarities.values()) <= len(accounts) - 2:
            break

    def slips_of(account):
        true = base[account]
        mine = [r for r in entries if r['account'] == account]
        found = {'transposed_digits': transpositions(true),
                 'boundary_day_included': [true + sum(r['amount'] for r in mine if r['date'] == boundary[0])],
                 'boundary_day_excluded': [true - sum(r['amount'] for r in mine if r['date'] == boundary[1])]}
        # Each slip is the exact figure it names; one that lands on the true or the
        # naive figure (or has nothing to act on) is not a wrong figure and is skipped.
        options = []
        for name, values in found.items():
            values = [v for v in values if v not in (true, naive[account])]
            if values:
                options += [(name, rng.choice(values))] * SLIP_WEIGHTS[name]
        return options

    others = [a for a in accounts if a not in controls and a not in quirk_of]
    mutable = set(quirk_of) | set(rng.sample(others, max(0, min(len(others), MUTABLE - len(quirk_of)))))
    slots = []
    for account in accounts:
        affected = account in quirk_of
        positive = polarities[account]
        slips = slips_of(account)
        if not slips:
            return None

        def make(equal, value, error=None, account=account):
            verb = 'totalled' if equal else 'did not total'
            return claim('account_disbursements',
                         f'Net disbursements for the {account} account during the reporting period {verb} EUR {value:,}.',
                         lambda w: (w[account] == value) == equal, cites,
                         quantity=lambda w: w[account], stated=value, error=error)

        if positive:
            mutations = ([make(True, naive[account], 'runs_naive_script')] if affected
                         else [make(True, value, name) for name, value in slips])
            right = make(True, base[account])
        else:
            mutations = [make(False, base[account], 'runs_naive_script' if affected else 'denies_true_total')]
            right = make(False, naive[account] if affected else rng.choice(slips)[1])
        slots.append((right, [] if account in controls or account not in mutable else mutations))
    return {'title': 'Marshwick Community Trust — Disbursement review',
            'sources': dict(sorted(sources.items())), 'base': base, 'unknowns': {}, 'slots': slots,
            'told': {'base': naive, 'unknowns': {}} if naive != base else None,
            'reader': 'runs_naive_script'}


def build(rng):
    # A draw whose register leaves an account with a total of zero or less, or in
    # which a quirk does not move exactly the figure of the account it affects, is
    # drawn again from the same stream, so the same seed still gives the same case.
    for _ in range(50):
        case = draw(rng)
        if case:
            return case
    raise GenerationError('No usable payment register in 50 draws')
