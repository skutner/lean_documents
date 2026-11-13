"""Report claims that invite a reader to repair them before giving a verdict.

The fictional Mereford Community Trust reviews paid invoices and programme funds. The named flawed reader,
repairs_the_claim, repairs the claim and not the sources: (a) it judges the main assertion of a statement and
overlooks a detail given in passing, and (b) it subtracts and divides as on natural numbers (a shortfall
becomes zero; a share is rounded down). Both habits apply to the whole dossier, controls included. Nothing in
the sources misleads it. The ordinary cause of a wrong detail lies in the report: the statement gives, in
passing, a value that really stands in the sources for another voucher, the companion it is mixed up with.
Requirement R13 asks for a misleading element that is the convenient route to an answer; that rule does not
apply to this family. A first version had meeting cards that showed stale or clamped values, and a reader who
trusted them could decide claims without the records and could not be told from the flawed reader; they are
gone, and so is every document that restates a fact a claim depends on. The dossier holds the procedure, the
settlement record and the programme accounts, and each claim is decided by one record, which it cites.

Settling sentences, reproduced verbatim from procedure.md:
A report statement is correct if and only if every assertion in it is correct, including amounts, dates, names and references given in parentheses.
For this review, the settlement record establishes invoice details and payment status at the checkpoint; the dispatch history does not replace that record.
An invoice was paid by the checkpoint if and only if the settlement record gives its checkpoint status as paid; a status of open means that it was unpaid at the checkpoint.
Differences retain their sign: the budget margin is the approved budget minus actual spending, including a negative margin when spending exceeds the budget.
Spending is within its budget tolerance if and only if the absolute value of the budget margin is at most the tolerance; otherwise it is outside its budget tolerance.
Equal allocations are exact rational shares unless the fund instruction explicitly requires each share to be rounded down to whole euros.
Allocations exhaust a fund if and only if their sum equals the fund total; they leave a balance if and only if their sum is less than that total.
Allocations in this review are internal accounting entitlements, not cash transfers.
No rounding to cents is applied.

Construction: 28 statements per report. Twelve are about vouchers ("Invoice 4180 (EUR 6925) was paid by the
checkpoint of 2034-10-31."), eight about budgets and eight about funds. Every statement of one kind has one
wording, so a slip never changes the wording and no count of wordings shows an altered report. There are 24
vouchers. Twelve are the ones a report speaks about, and each of those is paid. Each has a companion among the
other twelve, the voucher it is mixed up with in a slip; no document marks the relation, and the status of a
companion is an independent fair draw, so no other entry shows whether a reported voucher is paid. Four
reported vouchers (one of each detail kind: amount, date, name, reference) are open to a lure; eight (two of each
kind) are controls, which are never offered the lure. Every amount, date and purchase order stands on exactly
two vouchers that are not companions, and every name on three, so a detail is always found on at least one other
voucher, but see the entry-level cue below. Slips on a voucher: where the companion is open, the companion in
place of the voucher (main assertion wrong, detail right) and the companion with this voucher's detail (both
wrong); and, in a dossier with a lure only, the right voucher with the companion's detail (main right, detail wrong: key
refuted, flawed reader supported; this is the lure of habit (a), offered three times so that it is drawn about as
often as each other slip). The fourth combination, both right, is the unaltered claim; an altered claim is never
right. Of 4,205 altered voucher claims (seeds 1..100, indexes 1..12) 1,307 (31.1 percent) are main right and
detail wrong, 1,487 (35.4 percent) main wrong and detail right, 1,411 (33.6 percent) both wrong; 14,400 claims in
the correct reports and 10,195 in the altered reports are both right. The 1,307 lures are 328 amount, 332 date,
331 name and 316 reference.
Budgets and funds: ten of each. Items 0 to 5 are open to a lure, items 6 and 7 are controls (one for each
wording) and items 8 and 9 their companions, from which the slip about a control is made (the same sentence
about the neighbouring programme, wrong, no lure). The two arithmetics disagree on exactly two kinds of
item: an overspend that is outside tolerance (natural-number margin 0 reads as within) and an exact-shares
fund whose total does not divide evenly (floored shares leave a balance). They agree on every other item:
overspends within tolerance, underspends within and outside, exact funds that divide, round-down funds that
divide and that do not. Whether a programme is within or outside tolerance and whether a fund exhausts or leaves a
balance is an independent fair draw for each of the six open items of a kind, so the wording counts of a report
have the same distribution before and after claims are altered (tested over 1,000 dossiers). The lure is made by
pairing two ordinary properties on one item, and a dossier without a lure has the same draws with the pairing
broken: it has overspends, items outside tolerance and exact instructions, with the same numbers of items outside
tolerance, of exact instructions and of each wording (overspends and indivisible totals are one more for each
lure item, see the cues below). Sections and entries of both record files, and the order of vouchers, are
shuffled with the case's random source. Directions for habit (b): a true statement on a lure item ("outside",
"exhausted") is supported for the key and refuted for the reader; the opposite-wording statement that an altered
report may carry on the same item is refuted for the key and supported for the reader. A dossier is drawn to hold
a lure with probability 0.7; a lure dossier has one or two lure items among the budgets and funds. A dispatch
history differs from the checkpoint status on 1,887 of 7,200 vouchers (26.2 percent, seeds 1..300): a payment run
changes a minority.

R3, seeds 1..300, index 1: 80 dossiers (26.7 percent) hold no lure (seeds 1..1000: 308, 30.8 percent). In a
dossier with a lure the correct report holds 1 or 2 echo rows (mean 1.33) and the altered report 1 to 6 (mean
2.79): the lure items of habit (b) show in both reports, the compound lures only where claims are altered. This
exceeds "one or two items"; it is the price of drawing enough lures of habit (a) from the claims that the
generator alters.
R9, seeds 1..100, dossier indexes 1..12, echo rows per twelve dossiers: total minimum 18, median 36 and
maximum 62 (3,569 rows, all definite: 1,713 supported-to-refuted and 1,856 refuted-to-supported; none is
undetermined). Habit (a): minimum 5, median 13 and maximum 28 (1,307 rows, all refuted-to-supported). Habit (b):
minimum 10, median 22 and maximum 34 (2,262 rows; budgets 836 supported-to-refuted and 290 refuted-to-supported,
funds 877 and 259).

Remaining cues, measured as stated (trained on seeds 1..500, scored on 501..1000 unless noted; tests print each):
- The wording of a true statement fixes whether it can be a lure: in correct reports (seeds 1..1000) "within" and
  "left a balance" statements are never lures (0 of 4,009 and 0 of 4,002), "outside" and "exhausted" are 11.2 and
  11.8 percent. The wording alone never gives the verdict: every wording shape is both right and wrong.
- Reading the record is the deciding step, so some sentences carry the verdict: a statement about an open voucher is
  always wrong (2,491 of 2,491 in 1,000 dossiers) and an exact-shares fund never "left a balance" (1,080 of 1,080
  wrong). The status and instruction sentences with the wording give "wrong claim" at 86.3 percent against a base
  rate of 79.9. Two masked source sentences occur only on ordinary records: "Checkpoint status: open." and the
  round-down instruction. No masked sentence occurs only on lure records.
- Counting figures in both record files: overspends plus indivisible totals give "dossier holds a lure" at 69.4
  percent against 67.4 (one overspend or one indivisible total more per lure item). The count of exact instructions
  and of each wording does not.
- Entry level: a reader that skips the cited entry but looks at every other voucher and counts where the detail
  stands tells a wrong detail (one more voucher holds a wrong detail than a right one): "wrong claim" at 94.9
  percent against 85.2 and "lure claim" at 95.7 against 95.7 among voucher claims (the count finds wrong details, but
  about half of those are not lures). It says nothing about the main assertion. No reader that skips the settlement
  record and the programme accounts decides any claim: no identifier, name, date, reference or figure stands
  outside the file that decides it.
- Every other attack stayed at or below its base rate within its margin: eighteen length rules (per file, per
  record, per report, normalised per file name, with polarity for claims; the largest margin is 5.4 points), one
  rule per masked sentence (78 sentences for dossier targets and "lure claim", 72 for "wrong claim"), the feature
  guessers of the tests (none exceeds its base rate) and the whole-report guessers (none exceeds its base rate). The
  attack that looks up an exact normalised length meets an unseen length in 91 percent of cases and falls back to
  the majority, so the tests use thresholds. A reader of the documents that do not decide a claim (procedure and
  the other record) scores 79.9 percent against 79.9 with the best rule on wording shape.
- Findings of an independent review that no longer apply: there are no meeting cards, so no reader can decide a
  claim from them; the key of a claim does not depend on any display.

Departures from the sketch and from the first build: every statement about a voucher says "was paid by" and none
says "unpaid", so that a slip never changes the wording; the slip about a main assertion is the companion voucher
in its place, no longer the dispatch status; no document marks the companion (a marked companion's entry settled
the claimed voucher's status or detail without opening the claimed entry); the controls can themselves be wrong,
except where their companion is paid. Fund instructions sometimes require whole-euro rounding, so both fund
wordings are true and false across dossiers.

Limits: all records are closed, so there are no undetermined verdicts. Offices, workflow, document layout and claim
templates are fixed; figures, identities, statuses, tolerances and rounding vary. Equal allocations are internal
accounting entitlements, not cash transfers; reading them as cash payments would invite an unstated cent-rounding
rule, which the procedure excludes. The ordinary sense of "was paid by" could include a payment later reversed;
the checkpoint-status definition excludes that reading here. A statement whose main assertion is right and whose
detail is wrong is refuted, not undetermined, because the record settles the detail. No unresolved rule ambiguity
is intended, but code cannot establish that every human reads a sentence alike. The reader's answer is a
behavioural signature, not evidence of its reasoning. This family does not measure whether an agent's Lean
statement matches the claim; that is a separate plan. Checking every parenthesis and using signed integers and
rational numbers removes the lure; a careful tool-using auditor may saturate this family.
"""
from fractions import Fraction

from generate import claim, document, section


RULES = (
    'A report statement is correct if and only if every assertion in it is correct, including amounts, dates, names and references given in parentheses.',
    'For this review, the settlement record establishes invoice details and payment status at the checkpoint; the dispatch history does not replace that record.',
    'An invoice was paid by the checkpoint if and only if the settlement record gives its checkpoint status as paid; a status of open means that it was unpaid at the checkpoint.',
    'Differences retain their sign: the budget margin is the approved budget minus actual spending, including a negative margin when spending exceeds the budget.',
    'Spending is within its budget tolerance if and only if the absolute value of the budget margin is at most the tolerance; otherwise it is outside its budget tolerance.',
    'Equal allocations are exact rational shares unless the fund instruction explicitly requires each share to be rounded down to whole euros.',
    'Allocations exhaust a fund if and only if their sum equals the fund total; they leave a balance if and only if their sum is less than that total.',
)
PEOPLE = ('Mira Dale', 'Orin Vale', 'Lena Reed', 'Tara Moss', 'Nell Hart', 'Iris Wood', 'Cora West', 'Vera Shaw')
PROGRAMMES = ('Alder', 'Birch', 'Cedar', 'Dover', 'Elton', 'Ferry', 'Grove', 'Heath',
              'Ivern', 'Junor', 'Keldy', 'Larch', 'Marsh', 'North', 'Oaken', 'Perry',
              'Quill', 'Rowan', 'Sedge', 'Thorn', 'Upton', 'Vance', 'Wells', 'Yewen')
SUPPLIED = (
    'The order covered folding tables for the neighbourhood hall.',
    'The work replaced the door closers in the library.',
    'The purchase supplied printed guides for the autumn advice sessions.',
    'The service was transport for the volunteer training day.',
    'The order supplied shelving for the community archive.',
    'The engagement covered interpretation at an outreach meeting.',
    'The supplier repaired the kitchen water heater.',
    'The booking covered equipment hire for a local history exhibition.',
    'The order supplied stacking chairs for the youth club.',
    'The work covered repainting of the main corridor at the day centre.',
    'The purchase supplied first-aid kits for the community minibuses.',
    'The service was catering for the volunteers\' thank-you evening.',
    'The order supplied laptops for the digital skills course.',
    'The work was the annual servicing of the heating controls in the community hall.',
    'The purchase supplied notice boards for the entrance lobby.',
    'The booking covered a sign-language interpreter for the winter open day.',
    'The order supplied crockery for the lunch club.',
    'The work covered the replacement of two fire extinguishers at the day centre.',
    'The purchase supplied large-print leaflets for the welfare rights desk.',
    'The service was a minibus hire for the allotment project\'s open afternoon.',
    'The order supplied a projector screen for the training room.',
    'The engagement covered a first-aid refresher course for the volunteer stewards.',
    'The work was the clearing of the car park gutters before the winter.',
    'The purchase supplied seed trays and compost for the community garden.',
)
HANDLING = (
    'The receiving officer kept the signed delivery note with the purchase order.',
    'The coordinator checked the booked quantities against the order before passing the voucher to accounts.',
    'The service team confirmed completion by email, and the message was filed with the order.',
    'Staff noted the matter in the weekly operations log, which is held outside the finance file.',
    'The caretaker was present when the work was handed over and signed the completion sheet.',
    'Accounts received the supplier\'s invoice by post and entered it in the same week.',
    'The office manager approved the order in advance under the delegated spending limit.',
    'The supplier asked to be paid by bank transfer and not by cheque.',
    'The volunteer lead confirmed on the day that everything ordered had arrived.',
    'The supplier\'s covering letter was kept with the order until the voucher was signed.',
    'The finance assistant matched the order number on the invoice to the approved requisition.',
    'The secretary recorded the approval in the minutes of the previous month.',
)
ACTIVITIES = ('community support sessions', 'a weekly advice clinic', 'holiday activity days', 'a volunteer training scheme')
PERIODS = ('for the next quarter', 'for the winter term', 'for the coming year', 'for the spring programme')
INSTRUCTIONS = {True: 'Round shares down, with fractional euros withheld for every team.',
                False: 'Keep shares exact, with fractional euros retained for every team.'}
DETAIL_KINDS = ('amount', 'date', 'name', 'reference')


def detail_text(kind, value):
    return {'amount': 'EUR {}', 'date': 'issued {}', 'name': 'supplier {}',
            'reference': 'order {}'}[kind].format(value)


def within(world, ident):
    item = world['budgets'][ident]
    margin = item['budget'] - item['actual']
    if world['natural_arithmetic']:
        margin = max(0, margin)
    return abs(margin) <= item['tolerance']


def exhausts(world, ident):
    item = world['funds'][ident]
    share = Fraction(item['total'], item['recipients'])
    if world['natural_arithmetic'] or item['round_down']:
        share = item['total'] // item['recipients']
    return share * item['recipients'] == item['total']


def build(rng):
    active = rng.random() < 0.7
    checkpoint = '2034-10-31'
    # Twenty-four vouchers. The first twelve are the ones the report speaks about, and each is paid at the
    # checkpoint. Each of them has a companion among the other twelve, the voucher it is mixed up with in a
    # slip. No document marks the relation, and the status of a companion is an independent fair draw, so no
    # entry other than the voucher's own shows whether it is paid. Four reported vouchers (one for each kind
    # of detail) can carry a lure; eight (two for each kind) cannot, and are the controls of the same
    # wording. Every report statement about a voucher has the same wording ("was paid by"), so a slip never
    # changes the wording and no wording count shows an altered report.
    # Every amount, issue date and purchase order is shared by exactly two vouchers that are not companions of
    # each other, and every name by three, so a value found on a second voucher says nothing about a claim.
    identifiers = rng.sample(range(1000, 9999), 24)
    shared = {}
    for kind, values in (('amount', rng.sample(range(1100, 9900), 12)),
                         ('date', ['2034-{:02d}-{:02d}'.format(8 + d // 28, 1 + d % 28) for d in rng.sample(range(56), 12)]),
                         ('reference', ['PO-{}'.format(n) for n in rng.sample(range(1000, 9999), 12)])):
        while True:
            order = rng.sample(range(24), 24)
            twins = [(order[k], order[k + 1]) for k in range(0, 24, 2)]
            if all(abs(a - b) != 12 for a, b in twins):
                break
        shared[kind] = {voucher: values[k] for k, pair in enumerate(twins) for voucher in pair}
    while True:
        names = list(PEOPLE) * 3
        rng.shuffle(names)
        if all(names[i] != names[12 + i] for i in range(12)):
            break
    kinds = list(DETAIL_KINDS)
    rng.shuffle(kinds)
    control_kinds = list(DETAIL_KINDS) * 2
    rng.shuffle(control_kinds)
    invoices, claimed, companion = {}, [], {}
    for i, ident in enumerate(identifiers):
        paid = i < 12 or rng.random() < 0.5
        # A payment run changes the status of a minority of vouchers after dispatch.
        invoices[ident] = dict(amount=shared['amount'][i], date=shared['date'][i], name=names[i],
                               reference=shared['reference'][i], paid=paid, dispatch=paid != (rng.random() < 0.25))
    for i in range(12):
        companion[identifiers[i]] = identifiers[12 + i]
        claimed.append((identifiers[i], kinds[i] if i < 4 else control_kinds[i - 4], i < 4))

    # Programme budgets and fund allocations: ten of each. Items 0 to 5 are open to a lure. Items 6 and 7 are the
    # controls (one for each wording); items 8 and 9 are their companions, of the opposite wording, from which
    # a slip about a control is made. Whether a programme is within or outside tolerance, and whether a fund
    # exhausts or leaves a balance, is an independent fair draw for items 0 to 5, so the wording counts of a
    # report have the same distribution before and after any claim is altered. A lure is made by pairing two
    # ordinary properties on one programme: an overspend that is also outside tolerance, or an exact-shares
    # instruction on a total that does not divide evenly. A dossier without a lure has the same draws with the
    # pairing broken, so every property occurs in both kinds of dossier.
    programmes = rng.sample(PROGRAMMES, 20)
    while True:
        outside = [rng.random() < 0.5 for _ in range(6)] + [False, True, True, False]
        exhausting = [rng.random() < 0.5 for _ in range(6)] + [True, False, False, True]
        exact = [e and rng.random() < 0.5 for e in exhausting]
        # Some exhausting fund among the first six, and one more beside it, has the exact instruction whatever the
        # draw, so that a lure never stands alone; the instruction does not change the verdict of such a fund.
        lead = [i for i in range(6) if exhausting[i]]
        if lead and not any(exact[:6]):
            exact[rng.choice(lead)] = True
        spare = [i for i in range(10) if exhausting[i] and not exact[i]]
        if sum(exact) < 2 and spare:
            exact[rng.choice(spare)] = True
        budget_candidates = [i for i in range(6) if outside[i]]
        fund_candidates = [i for i in range(6) if exact[i]]
        if budget_candidates or fund_candidates:
            break
    options = [(1, 0)] * bool(budget_candidates) + [(0, 1)] * bool(fund_candidates) + [(1, 1)] * bool(
        budget_candidates and fund_candidates)
    lure_budget, lure_fund = rng.choice(options) if active else (0, 0)
    lure_over = set(rng.sample(budget_candidates, lure_budget))
    lure_exact = set(rng.sample(fund_candidates, lure_fund))
    ordinary_over = {i for i in range(10) if not outside[i] and rng.random() < 0.5}
    if not ordinary_over:
        ordinary_over = {rng.choice([i for i in range(10) if not outside[i]])}
    budgets, budget_names = {}, programmes[:10]
    for i, name in enumerate(budget_names):
        budget, tolerance = rng.randrange(12000, 85000), rng.randrange(100, 900)
        distance = rng.randrange(tolerance + 1, 2 * tolerance + 1) if outside[i] else rng.randrange(1, tolerance + 1)
        over = i in lure_over if outside[i] else i in ordinary_over
        budgets[name] = dict(budget=budget, actual=budget + distance if over else budget - distance,
                             tolerance=tolerance)
    funds, fund_names = {}, programmes[10:]
    for i, name in enumerate(fund_names):
        recipients, quotient = rng.choice((3, 4, 6)), rng.randrange(4000, 9000)
        divisible = exhausting[i] and i not in lure_exact
        funds[name] = dict(total=quotient * recipients + (0 if divisible else rng.randrange(1, recipients)),
                           recipients=recipients, round_down=not exact[i])
    base = dict(invoices=invoices, budgets=budgets, funds=funds, companion=companion,
                ignore_detail=False, natural_arithmetic=False)
    told = dict(base, ignore_detail=True, natural_arithmetic=True)

    def lures(name, kind):
        return (within(base, name) != within(told, name)) if kind == 'budget' else (
            exhausts(base, name) != exhausts(told, name))

    sources = {}
    sources['procedure.md'] = document('Mereford Community Trust — finance review procedure', [
        section('P-01', 'Authority and purpose', 'Finance Office, issued 1 October 2034. This procedure governs '
                'the monthly review sent to the trustees. It keeps the assessment of a written statement separate '
                'from the decision to release money. A reviewer records what the retained documents establish '
                'at the checkpoint, rather than predicting the next payment run. ' + ' '.join(RULES[:3])),
        section('P-02', 'Budget comparisons', ' '.join(RULES[3:5])),
        section('P-03', 'Allocation accounting', ' '.join(RULES[5:]) + ' Allocations in this review are internal '
                'accounting entitlements, not cash transfers. No rounding to cents is applied. The fund '
                'instruction in the programme accounts states whether whole-euro rounding is required. A later '
                'cash payment is a separate transaction and is outside the present review.')])
    supplied = rng.sample(SUPPLIED, 24)
    settlement_entries = []
    for i, ident in enumerate(identifiers):
        inv = invoices[ident]
        settlement_entries.append(section('S-{}'.format(ident), 'Invoice {}'.format(ident),
            supplied[i] + ' ' + rng.choice(HANDLING) + '\n\n' +
            'The signed voucher for invoice {} names supplier {}; its issue date is {}, '
            'its amount is EUR {}, and its purchase order is {}. '
            'Dispatch history: {}. Checkpoint status: {}. '
            'The checkpoint entry includes the bank response and is the record used for the trustees\' review.'.format(
                ident, inv['name'], inv['date'], inv['amount'], inv['reference'],
                'paid' if inv['dispatch'] else 'open', 'paid' if inv['paid'] else 'open')))
    rng.shuffle(settlement_entries)
    settlement = [section('S-00', 'Monthly close', 'Accounts Office, 31 October 2034. The entries below are the '
                          'settlement record at the checkpoint of 2034-10-31. The invoice dates are issue dates, not '
                          'dates of payment. Supplier names are the trading names on the signed vouchers. The dispatch '
                          'history records the '
                          'status when the voucher was sent to the bank: paid meant that a provisional payment had '
                          'been entered, while open meant that it awaited release. Where the dispatch history and '
                          'the checkpoint status differ, a released payment was posted, or a provisional entry '
                          'rejected by the bank was reversed, between dispatch and the checkpoint. Each entry states '
                          'the resulting checkpoint status explicitly.')]
    settlement.extend(settlement_entries)
    financial = [section('F-00', 'Programme accounts', 'Management Accounts Office, 31 October 2034. These notes '
                         'document the budget reviews and allocation instructions approved at the close. The '
                         'programme amounts are independent of the invoices in the settlement record, which belong '
                         'to the central office cost centre; none of the programme totals can be recovered by '
                         'adding those invoices.')]
    records = []
    for name, b in budgets.items():
        records.append(section('F-' + name, name + ' programme',
                               'The {} programme operated {} during October. The manager agreed the review '
                               'tolerance before receiving the close figures so that underspending and overspending '
                               'would be assessed on the same basis. Approved budget: EUR {}. Actual spending: '
                               'EUR {}. Budget tolerance: EUR {}. Actual spending includes every posted charge for '
                               'the programme at the checkpoint and excludes commitments for November. There are no '
                               'later adjustments to these figures. The accounts office keeps the approved budget '
                               'and the actual spending as two separate figures and does not record their '
                               'difference.'.format(name, rng.choice(ACTIVITIES), b['budget'], b['actual'],
                                                    b['tolerance'])))
    for name, f in funds.items():
        records.append(section('F-' + name, name + ' fund',
                               'The {} fund finances the service teams\' internal entitlements {}. Fund total: '
                               'EUR {}. Recipient teams: {}. Fund instruction: {} Each team receives an equal share '
                               'under that instruction. This is a ledger allocation; no bank payment is being '
                               'authorised by this entry. The treasurer will arrange cash transfers separately '
                               'after the teams submit their spending schedules. Any amount left after the '
                               'instructed allocation stays in the fund, and no team receives that amount as an '
                               'extra payment.'.format(name, rng.choice(PERIODS), f['total'], f['recipients'],
                                                       INSTRUCTIONS[f['round_down']])))
    rng.shuffle(records)
    financial.extend(records)
    sources['settlement.md'] = document('Mereford Community Trust — settlement record', settlement)
    sources['funds.md'] = document('Mereford Community Trust — programme accounts', financial)
    slots = []

    def invoice_claim(ident, kind, detail, error=None):
        text = 'Invoice {} ({}) was paid by the checkpoint of {}.'.format(ident, detail_text(kind, detail), checkpoint)

        def pred(w):
            row = w['invoices'][ident]
            return row['paid'] and (w['ignore_detail'] or row[kind] == detail)
        return claim('compound_' + kind, text, pred, [('settlement.md', 'S-' + str(ident)), ('procedure.md', 'P-01')],
                     error=error)

    for ident, kind, open_to_lure in claimed:
        inv, other = invoices[ident], companion[ident]
        peer = invoices[other]
        # Slips that keep the wording: the companion in place of this voucher, with its own detail or with this
        # voucher's detail. Both are wrong in the main assertion, but only where the companion is open.
        wrong = []
        if not peer['paid']:
            wrong = [invoice_claim(other, kind, peer[kind], 'copies_companion_voucher'),
                     invoice_claim(other, kind, inv[kind], 'copies_companion_number')]
        if open_to_lure and active:
            # The right voucher with the companion's detail, offered three times so that it is drawn about as often
            # as each other slip once the dossiers without a lure, the controls and the vouchers with a paid
            # companion (which have no other slip) are counted.
            wrong += [invoice_claim(ident, kind, peer[kind], 'repairs_parenthetical_detail')] * 3
        slots.append((invoice_claim(ident, kind, inv[kind]), wrong))

    def budget_claim(name, flag, error=None):
        return claim('budget_tolerance', 'Spending on the {} programme was {} its budget tolerance.'.format(
            name, 'within' if flag else 'outside'), lambda w: within(w, name) == flag,
            [('funds.md', 'F-' + name), ('procedure.md', 'P-02')], error=error)

    def fund_claim(name, flag, error=None):
        action = 'exhausted the fund' if flag else 'left a balance in the fund'
        return claim('allocation_balance', 'The equal allocations from the {} fund {}.'.format(name, action),
                     lambda w: exhausts(w, name) == flag, [('funds.md', 'F-' + name), ('procedure.md', 'P-03')],
                     error=error)

    for i, name in enumerate(budget_names[:8]):
        actual = within(base, name)
        if i < 6:
            error = 'repairs_signed_arithmetic' if lures(name, 'budget') else 'reverses_budget_comparison'
            wrong = [budget_claim(name, not actual, error)]
        else:
            wrong = [budget_claim(budget_names[i + 2], actual, 'copies_companion_programme')]
        slots.append((budget_claim(name, actual), wrong))
    for i, name in enumerate(fund_names[:8]):
        actual = exhausts(base, name)
        if i < 6:
            error = 'repairs_share_arithmetic' if lures(name, 'fund') else 'reverses_allocation_comparison'
            wrong = [fund_claim(name, not actual, error)]
        else:
            wrong = [fund_claim(fund_names[i + 2], actual, 'copies_companion_programme')]
        slots.append((fund_claim(name, actual), wrong))
    return dict(title='Mereford Community Trust — October finance review', sources=dict(sorted(sources.items())),
                base=base, unknowns={}, slots=slots,
                told={'base': told, 'unknowns': {}} if active else None, reader='repairs_the_claim')
