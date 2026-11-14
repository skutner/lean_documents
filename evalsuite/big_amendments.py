"""A long services framework agreement and the instruments made under it: amendments that must be tracked.

One dossier is three sources: `agreement.md` (126 to 158 clauses in parts, 57 of them holding one parameter: a period
in days, a percentage, a money cap, a threshold or a responsible officer; 11 to 15 of the 57 leave the value open
until an amending provision gives it one), `instruments.md` (26 to 32 later instruments in date order: deeds of
variation, side letters, notices, certificates and correction slips, each with its own filler of ordinary business)
and a short `procedure.md` that settles every rule of reading. About 380,000 characters in all.

What the instruments do, each in prose: substitute a value; insert an exception for one of four categories of work;
disapply a clause for a period; revoke an earlier amending provision; correct a slip in an earlier instrument (value,
commencement date, sunset date or last day of a disapplication); come into force on a fixed date or on an event that a
certificate or notice records later, or never; cease to have effect (in the provision, or in a later instrument);
renumber clauses, with references following the renumbering; give a parameter the value another parameter has on the
day the provision takes effect; define an expression for a parameter, or for another defined expression. Most amending
provisions name their parameter by an expression defined in an instrument, by a description ("the period that the
clause on late delivery allows to the Supplier"), by the heading of its clause or by its defined term; the rest by
clause number, as the clause was numbered on the date of the instrument. Proposals that were not accepted sit among
the paragraphs as decoys, with a rule in procedure.md. Every deciding fact is a sentence inside a numbered paragraph
with ordinary business around it; there is no anchor, table or list of them, and a statement cites the three
documents, never a passage.

The facts are a flat list of JSON-serialisable dicts (`case['events']`), one per parameter, defined term, amending
provision, commencement event, revocation, correction, later sunset, renumbering and decoy (a proposal that decides
nothing: `decides: False`), with ids "E001", "E002", ... and, for each, `requires`: the facts that must be present
for it to be read. `answer(query, events, reader=None)` reads any subset of that list as if it were the whole file,
under the procedure's own rules (`_Index.meaning`): a fact that cannot be read without something the subset lacks (a
provision made through an expression whose definition is absent, a revocation, correction or later sunset of an
absent provision, a clause number whose renumbering is absent) is left out; a provision that waits on an event the
subset lacks reads as if no instrument recorded the event, so it never has effect; a provision that takes over the
value of another parameter whose clause is absent takes no value. The answer is None only when what the statement is
about cannot be computed at all (the clause of the statement's own parameter is absent); counts over the whole file
are never None (an empty subset counts zero). NONE_VALUE ('none') means that it is established that there is no
value; otherwise the value. It never raises. `value_form(query)` and `relevant(query, events)` complete the interface;
`value_in_force`, `instruments_in_effect`, `provisions_in_effect` and `first_date` are the parts. The key is computed
from the full list, and every claim carries `claim['query']` (kind, parameter, date(s), category, value kind, stated
value) that `answer` evaluates.
`case['passages']` says which block of which source tells which facts (`{file, paragraph, event_ids, role}`, the
paragraph being the 0-based index among the blank-line-separated blocks of the rendered file).

Three named flawed readers, all functions of `answer`:
    reads_agreement_only         the original text; no instrument.
    applies_everything           every substitution and exception from the date of its own instrument, whatever its
                                 commencement date or event; no sunset, no revocation, no later sunset; a
                                 disapplication for the period it names; value corrections and corrections of the
                                 end of a disapplication; no other correction.
    searches_by_clause_number    only the amending provisions that name their parameter by the clause number it had in
                                 the original agreement. It reads those correctly (commencement, own sunset, event
                                 records) but does not see revocations, corrections or later sunsets, because none of
                                 them names a clause by number.

Statements (24 per report): the value on a date, generally or for a category, or that it had no value in force (13);
how many instruments held an amending provision for one parameter that had taken effect by a date (2); how many
amending provisions of all parameters together had (3, each resting on every counted provision); the first date on
which the general value was a given value (2); whether the value on one date differed from the value on another, or
was the same (4, two of each so that neither wording tells); at least four rest on a value taken over from another
parameter. At least 6 controls (every reader right), at least 9 statements on
which each reader is wrong, at least 14 whose deciding passages are two or more instruments or paragraphs. There are no
unknowns: procedure.md says that an event no instrument records never takes place.

Size. The environment variable BIG_AMENDMENTS_SIZE (1, 2, 3 or 4; unset means 1; anything else is an error) sets how
many instruments and amending provisions the file holds: about SIZE times as many, with the definitions, events,
corrections, revocations and sunsets that go with them, over a SIZE times longer period; the agreement keeps its size
and `case['size']` says which size was built. Size 1 is the dossier described above, byte for byte as it was before
sizes existed. From size 2 the 24 statements are 9 about the whole file (seven shapes, at least five of them in every
dossier, each defined in procedure.md by what it counts), 9 values on a date, 2 per-parameter counts, 2 first dates and
2 comparisons of two dates (at least 6 controls, as before), and procedure.md gains the rule that defines the
whole-file counts. The size changes the draw (a separate random stream is derived from the one given), so the same
seed, index and size always give the same bytes and another size gives another dossier.

Limits. A script that implements the sentence frames of the inventory (about 130 of them), resolves the references and
interprets the rules recovers everything; the test of this module is such a script. What the family takes away is the
cheap script (the full name plus a number) and the citation. The two reports must not be shown together.
"""
import contextlib
import datetime
import json
import os
import random

import generate
from generate import claim

NONE_VALUE = 'none'          # established: nothing is in force, there is no such date
NO_VALUE = NONE_VALUE
NOT_ESTABLISHED = None
READERS = ('reads_agreement_only', 'applies_everything', 'searches_by_clause_number')
DAY = datetime.timedelta(days=1)
SIZE_VARIABLE = 'BIG_AMENDMENTS_SIZE'
SIZES = (1, 2, 3, 4)
# Statements about the whole file (the value is a count over every amending provision): the kinds of their queries.
WHOLE_FILE = ('total', 'between', 'class', 'on', 'params', 'insts', 'never')


def current_size():
    """The size asked for by BIG_AMENDMENTS_SIZE: 1 when it is not set; an error when it is set to anything but 1, 2,
    3 or 4."""
    raw = os.environ.get(SIZE_VARIABLE)
    if raw is None:
        return 1
    if raw not in ('1', '2', '3', '4'):
        raise generate.GenerationError('%s must be 1, 2, 3 or 4, not %r' % (SIZE_VARIABLE, raw))
    return int(raw)

CATEGORIES = ('Hardware Services', 'Software Services', 'Maintenance Services', 'Training Services')
PARTS = ('Preliminary matters', 'The Services', 'Delivery and acceptance', 'People and sites', 'Charges and payment',
         'Performance and service credits', 'Risk, liability and insurance', 'Data, security and audit',
         'Governance and change', 'Term, termination and exit', 'General provisions')

# Parameters of the agreement. The tuple is (kind, part, topic, term, extras...): the extras are
# days: party, act, trigger; pct and money: subject; count: unit, consequence; officer: party, duty.
PARAM_ROWS = (
    ('days', 1, 'onboarding of new sites', 'Onboarding Completion Period', 'Supplier', 'complete onboarding of a new Customer Site', 'the signing of the relevant site schedule'),
    ('days', 1, 'approval of subcontractors', 'Subcontractor Approval Period', 'Customer', 'approve or refuse a subcontractor proposed by the Supplier', "receipt of the Supplier's written proposal"),
    ('days', 2, 'late delivery', 'Delivery Grace Period', 'Supplier', 'complete a delivery that is overdue', "the Customer's written notice of the delay"),
    ('days', 2, 'acceptance testing', 'Acceptance Test Period', 'Customer', 'complete the acceptance tests for a Deliverable', 'delivery of that Deliverable'),
    ('days', 2, 'correction of snagging items', 'Snagging Period', 'Supplier', 'correct the minor defects on a snagging list', 'the issue of the list'),
    ('days', 3, 'replacement of key personnel', 'Personnel Replacement Period', 'Supplier', 'propose a replacement for a member of key personnel who has left', 'the departure of that person'),
    ('days', 4, 'invoice queries', 'Invoice Query Window', 'Customer', 'raise a query on an invoice', 'receipt of the invoice'),
    ('days', 4, 'payment of undisputed invoices', 'Payment Period', 'Customer', 'pay each undisputed invoice', 'receipt of a valid invoice'),
    ('days', 5, 'claims for service credits', 'Credit Claim Period', 'Customer', 'claim a service credit', 'the end of the month to which it relates'),
    ('days', 6, 'remedy of material breach', 'Cure Period', 'Supplier', 'remedy a material breach', 'written notice of that breach'),
    ('days', 6, 'notice of force majeure', 'Force Majeure Notice Period', 'Supplier', 'notify the Customer of a force majeure event', 'the start of that event'),
    ('days', 6, 'evidence of insurance', 'Insurance Evidence Period', 'Supplier', 'supply renewed certificates of insurance', 'the renewal date of each policy'),
    ('days', 7, 'security incident notification', 'Incident Notification Period', 'Supplier', 'notify the Customer of a security incident', 'becoming aware of it'),
    ('days', 7, 'audit requests', 'Audit Response Period', 'Supplier', 'provide the documents requested for an audit', "the Customer's written request"),
    ('days', 7, 'return of Customer Data', 'Data Return Period', 'Supplier', 'return the Customer Data', 'the date of termination or expiry'),
    ('days', 8, 'change requests', 'Change Response Period', 'Supplier', 'respond to a change request', 'receipt of that request'),
    ('days', 8, 'escalation of disputes', 'Escalation Period', 'Supplier', 'escalate an unresolved dispute to the Governance Board', 'a written request for escalation'),
    ('days', 8, 'review of proposed rate changes', 'Rate Review Response Period', 'Customer', 'respond to a proposed rate change', 'receipt of the proposal'),
    ('days', 9, 'warranty claims', 'Warranty Claim Period', 'Customer', 'give notice of a warranty claim', 'discovering the defect'),
    ('days', 9, 'return of records', 'Records Return Period', 'Supplier', "return the Customer's records", 'the end of the relevant Statement of Work'),
    ('days', 9, 'withdrawal of a termination notice', 'Withdrawal Period', 'Customer', 'withdraw a notice of termination', 'giving that notice'),
    ('days', 9, 'renewal of software licences', 'Licence Renewal Notice Period', 'Customer', 'confirm the renewal of a software licence', 'receipt of the renewal offer'),
    ('pct', 4, 'interest on late payment', 'Late Payment Rate', 'overdue sums, for each month of delay'),
    ('pct', 5, 'service credits', 'Service Credit Rate', 'the monthly Charges for the affected Service'),
    ('pct', 4, 'retention from milestone payments', 'Retention Rate', 'each milestone payment'),
    ('pct', 4, 'early payment discount', 'Early Payment Discount', 'the amount of the invoice concerned'),
    ('pct', 4, 'indexation of the Charges', 'Indexation Cap', 'the annual increase in the Charges'),
    ('pct', 5, 'damages for delay', 'Delay Damages Rate', 'the Charges for the delayed Deliverable, for each week of delay'),
    ('pct', 8, 'handling of approved changes', 'Change Handling Rate', 'the value of each approved change'),
    ('pct', 4, 'volume rebate', 'Volume Rebate Rate', 'the Charges paid above the rebate threshold in a Contract Year'),
    ('pct', 9, 'assistance on exit', 'Exit Assistance Surcharge', 'the standard rates for exit work'),
    ('pct', 4, 'out of hours work', 'Out of Hours Surcharge', 'the standard rate for work done outside normal hours'),
    ('money', 6, 'overall liability', 'General Liability Cap', 'the total liability of either party in a Contract Year'),
    ('money', 6, 'liability for data protection breaches', 'Data Liability Cap', 'liability for a breach of the data protection clause'),
    ('money', 6, 'single claims', 'Single Claim Limit', 'any one claim under this Agreement'),
    ('money', 4, 'supplier expenses', 'Expense Approval Limit', "expenses the Supplier may incur without the Customer's approval"),
    ('money', 8, 'approval of changes by the Project Manager', 'Change Approval Threshold', 'the value of a change the Project Manager may approve alone'),
    ('money', 6, 'minimum insurance', 'Minimum Insurance Cover', 'the public liability cover that the Supplier must hold'),
    ('money', 4, 'advance payments', 'Advance Payment Ceiling', 'any advance payment'),
    ('money', 4, 'ordering limits', 'Monthly Order Ceiling', 'the orders placed in each month'),
    ('money', 1, 'low value purchases', 'Low Value Purchase Limit', 'a purchase made for the Customer without quotations'),
    ('money', 9, 'performance bond', 'Performance Bond Amount', 'the bond to be provided at mobilisation'),
    ('count', 5, 'repeated service failures', 'Failure Threshold', 'service failures in a rolling quarter', 'the Customer may serve a remedial plan notice'),
    ('count', 2, 'missed milestones', 'Milestone Default Threshold', 'missed milestones under one Statement of Work', 'the Customer may withdraw that Statement of Work'),
    ('count', 5, 'upheld complaints', 'Complaint Threshold', 'upheld complaints in a quarter', "the Governance Board must review the Supplier's complaints process"),
    ('count', 7, 'recurring security incidents', 'Incident Review Threshold', 'reportable security incidents in a year', 'the Customer may require an independent security review'),
    ('count', 3, 'staff turnover', 'Turnover Alert Threshold', 'departures of key personnel in a year', 'the Supplier must submit a retention plan'),
    ('count', 2, 'rejected deliverables', 'Rework Threshold', 'rejected deliverables in a quarter', 'the Customer may suspend further acceptance testing'),
    ('count', 1, 'active sites', 'Site Limit', 'active Customer Sites', 'a further site schedule needs the approval of the Governance Board'),
    ('officer', 8, 'service governance', 'Governance Lead', 'Customer', 'chair the Governance Board'),
    ('officer', 8, 'change control', 'Change Authority', 'Customer', 'approve changes to the Services'),
    ('officer', 4, 'invoice approval', 'Invoice Approver', 'Customer', 'approve invoices for payment'),
    ('officer', 7, 'security', 'Security Officer', 'Supplier', 'report on security matters to the Governance Board'),
    ('officer', 7, 'data protection contacts', 'Data Protection Contact', 'Supplier', 'act as the contact for data protection matters'),
    ('officer', 2, 'delivery management', 'Delivery Manager', 'Supplier', 'manage delivery under each Statement of Work'),
    ('officer', 5, 'complaints handling', 'Complaints Officer', 'Customer', 'handle complaints about the Services'),
    ('officer', 9, 'exit planning', 'Exit Manager', 'Supplier', 'manage the exit plan'),
)

# Clauses that hold no parameter, by part. "Definitions" is always clause 1.
BACKGROUND = (
    ('Interpretation', 'Commencement and duration', 'Order of precedence', 'Entire agreement', 'Authority and capacity',
     'Counterparts and electronic signature', 'Language of the Agreement'),
    ('Scope of the Services', 'Statements of Work', 'Standard of performance', 'Customer dependencies',
     'Supplier equipment', 'Service descriptions', 'Additional services', 'Reasonable assistance',
     'Policies and procedures', 'Use of Customer premises', 'Third party software', 'Service catalogue'),
    ('Delivery planning', 'Delivery locations', 'Delivery documentation', 'Risk and title in goods',
     'Inspection on delivery', 'Rejection of Deliverables', 'Installation', 'Commissioning',
     'Handover of documentation', 'Training Deliverables', 'Acceptance certificates'),
    ('Supplier Personnel', 'Vetting of personnel', 'Health and safety on site', 'Site access',
     'Conduct on Customer premises', 'Working hours', 'Training of Supplier Personnel', 'Key personnel',
     'Staff transfers'),
    ('Charges', 'Invoicing', 'Value added tax', 'Expenses generally', 'Set-off', 'Disputed amounts', 'Rate cards',
     'Price review', 'Currency', 'Records of time worked'),
    ('Service levels', 'Measurement of performance', 'Monthly service reports', 'Root cause analysis',
     'Continuous improvement', 'Customer satisfaction surveys', 'Remedial plans', 'Service credits generally'),
    ('Indemnities', 'Exclusions of liability', 'Conduct of claims', 'Insurance generally', 'Mitigation of loss',
     'Force majeure', 'Warranties', 'Limits on remedies'),
    ('Data protection', 'Security of Customer Data', 'Business continuity', 'Audit and inspection', 'Confidentiality',
     'Intellectual property', 'Licences to use materials', 'Publicity', 'Records and reporting',
     'Information security standards'),
    ('Governance Board', 'Contract managers', 'Change control procedure', 'Dispute resolution', 'Reporting lines',
     'Meetings and minutes', 'Risk register', 'Benchmarking'),
    ('Termination for breach', 'Termination for insolvency', 'Consequences of termination', 'Exit plan',
     'Transfer of knowledge', 'Survival of clauses', 'Step-in rights', 'Termination assistance generally'),
    ('Notices', 'Assignment', 'Waiver', 'Severance', 'Third party rights', 'Governing law', 'Jurisdiction',
     'Variation', 'Further assurance', 'Anti-bribery', 'Modern slavery', 'Costs of the Agreement'),
)

TITLES = ('Finance Director', 'Head of Procurement', 'Chief Operating Officer', 'Commercial Director',
          'Head of Operations', 'Programme Director', 'General Counsel', 'Chief Information Officer',
          'Head of Compliance', 'Service Director', 'Operations Manager', 'Head of Legal', 'Contracts Director',
          'Head of Delivery')
DAYS = (3, 5, 7, 10, 14, 15, 20, 21, 28, 30, 35, 45, 60, 75, 90)
PCTS = (0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10, 12.5, 15, 20)
MONEY = (25000, 40000, 50000, 75000, 100000, 120000, 150000, 200000, 250000, 300000, 400000, 500000, 750000,
         1000000, 1500000, 2500000)
COUNTS = (2, 3, 4, 5, 6, 7, 8, 9, 10, 12)
POOLS = {'days': DAYS, 'pct': PCTS, 'money': MONEY, 'count': COUNTS, 'officer': TITLES}
NOUNS = {'days': 'period', 'pct': 'percentage', 'money': 'amount', 'count': 'number', 'officer': 'officer'}

# Events that a certificate or notice can record. Each phrase completes "the date on which ..." and
# "certifies that ...".
EVENT_PHRASES = (
    'the Northern depot has passed its readiness inspection',
    'the second phase of the data migration has been signed off',
    'the Customer has accepted the Riverside data centre',
    'the new service desk platform has gone live',
    'the second tranche of equipment has been delivered',
    'the security accreditation of the shared platform has been granted',
    'the Eastern region rollout has been completed',
    'the Supplier has opened its operations centre at Leeds',
    "the opening asset register has been reconciled with the Customer's records",
    "the interface with the Customer's finance system has been certified",
    'the first annual joint review has been concluded',
    'the transition of the Western helpdesk has been completed',
)
# More events, used only from size 2 (a larger file records more of them); the first twelve are all that size 1 draws.
EVENT_PHRASES_MORE = (
    'the Southern warehouse has passed its fire safety inspection',
    'the third phase of the network upgrade has been signed off',
    'the Customer has accepted the Harbourside server room',
    'the new payroll interface has gone live',
    'the third tranche of laptops has been delivered',
    'the cloud hosting accreditation of the reporting platform has been granted',
    'the Midlands region rollout has been completed',
    'the Supplier has opened its service centre at Ghent',
    "the opening licence register has been reconciled with the Customer's records",
    "the interface with the Customer's procurement system has been certified",
    'the second annual joint review has been concluded',
    'the transition of the Northern helpdesk has been completed',
    'the backup site at Rotterdam has passed its recovery test',
    'the fourth phase of the data migration has been signed off',
    'the Customer has accepted the Lakeside training suite',
    'the new asset tracking tool has gone live',
    'the first batch of replacement printers has been delivered',
    'the penetration test of the customer portal has been passed',
    'the Western region rollout has been completed',
    'the Supplier has opened its logistics hub at Antwerp',
    'the spares inventory has been reconciled with the Customer ledger',
    "the interface with the Customer's timetabling system has been certified",
    'the third annual joint review has been concluded',
    'the transition of the Eastern helpdesk has been completed',
    'the Customer has accepted the Millbrook control room',
    'the electronic signature platform has gone live',
    'the second consignment of network switches has been delivered',
    'the data centre energy audit has been completed',
    'the Central region rollout has been completed',
    'the Supplier has opened its repair workshop at Turin',
    'the fifth phase of the data migration has been signed off',
    'the identity management upgrade has been certified',
    'the first stage of the site consolidation has been completed',
    'the Customer has accepted the Quayside storage facility',
    'the annual disaster recovery exercise has been concluded',
    'the transition of the Southern helpdesk has been completed',
)

KIND_TITLE = {'deed': 'Deed of Variation', 'letter': 'Side Letter', 'notice': 'Notice', 'certificate': 'Certificate',
              'slip': 'Correction Slip'}
KIND_NOUN = {'deed': 'deed', 'letter': 'side letter', 'notice': 'notice', 'certificate': 'certificate',
             'slip': 'correction slip'}

MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December')
MONTH_ABBR = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
DATE_FORMATS = ('{y}-{m02}-{d02}', '{d} {mname} {y}', '{mname} {d}, {y}', '{dord} {mname} {y}', '{d} {mabbr} {y}',
                'the {dord} day of {mname} {y}')
ONES = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
        'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen')
TENS = ('', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety')
VALUE_FORMATS = {
    'days': ('{n} days', '{n} calendar days', '{w} days', '{w} ({n}) days', 'a period of {n} days'),
    'pct': ('{n} per cent', '{n}%', '{w} per cent', '{n} percent', '{w} per cent ({n}%)'),
    'money': ('EUR {n}', '€{n}', '{n} euros', '{w} euros', 'EUR {n} ({w} euros)'),
    'count': ('{n} {unit}', '{w} {unit}', '{w} ({n}) {unit}'),
    'officer': ("the {party}'s {title}", 'the {title} of the {party}'),
}

# ---- Wordings of the facts the agreement and the instruments state. Placeholders are named in
# INVENTORY['placeholders']. A sentence is matched after lower-casing its first letter, so the
# renderer capitalises the first letter of each sentence it writes.
ORIGINAL = {
    'days': (
        'The {party} shall {act} within {value} after {trigger}.',
        'The {term} is {value}, and runs from {trigger}.',
        'Within {value} after {trigger}, the {party} must {act}; that period is the {term}.',
        'The {party} has {value} from {trigger} in which to {act}, and the {term} is to be construed accordingly.',
        'Time for the {party} to {act} is limited to {value} after {trigger} (the {term}).',
        'If {trigger} has occurred, the {party} is to {act} no later than {value} afterwards.',
        'The period allowed under this clause is {value}, counted from {trigger}; within it the {party} shall {act}.',
        'For the purposes of this Agreement the {term} means {value} from {trigger}, within which the {party} will {act}.',
    ),
    'pct': (
        'The rate applying under this clause is {value} of {subject}.',
        'The {term} is {value}, applied to {subject}.',
        'An adjustment of {value} applies to {subject} (the {term}).',
        'Where this clause applies, the Parties shall apply {value} to {subject}, and that figure is the {term}.',
        'The percentage stated for the purposes of this clause is {value}, calculated on {subject}.',
        'For {subject} the {term} is fixed at {value}.',
        'A charge, deduction or surcharge of {value} is made on {subject}; the {term} is to be read accordingly.',
        'The {term} equals {value} of {subject}.',
    ),
    'money': (
        'The {term} is {value}.',
        'For {subject}, the Parties fix the {term} at {value}.',
        'A figure of {value} applies to {subject}, and that amount is the {term}.',
        'For the purposes of this clause, the amount that applies to {subject} is {value}.',
        'The {term} for {subject} is set at {value}.',
        '{value} is the figure the Parties have fixed for {subject}; it is called the {term}.',
        'The sum fixed by this clause is {value}, and it governs {subject}.',
        'As to {subject}, the {term} stands at {value}.',
    ),
    'count': (
        'If there are {value}, {conseq}.',
        'The {term} is {value}; once it is reached, {conseq}.',
        'Where there have been {value}, {conseq}; that is the {term}.',
        'Upon {value}, {conseq}, and that number is the {term}.',
        'The Parties fix the {term} at {value}, and when it is reached {conseq}.',
        'The threshold under this clause is {value}: {conseq}.',
        'Once the count reaches {value}, {conseq}, and the {term} is to be read accordingly.',
        'For the purposes of this Agreement the {term} means {value}, at which point {conseq}.',
    ),
    'officer': (
        'The {term} is {value}.',
        'The Parties designate {value} to {duty}, and that office is the {term}.',
        '{value} shall {duty}; for this purpose that officer is the {term}.',
        'The person responsible under this clause is {value}, who shall {duty}.',
        'Responsibility to {duty} rests with {value} as the {term}.',
        'The {term}, being {value}, shall {duty}.',
        'For the purposes of this clause the responsible officer is {value}, who is to {duty}.',
        'The Parties name {value} as the {term}, to {duty}.',
    ),
}
UNSET = (
    'No {noun} is fixed by this clause; the {term} applies only once an amending provision gives it a value.',
    'The {term} has no value at the date of this Agreement and takes one only when an amending provision supplies it.',
    'This clause leaves the {noun} open, so the {term} has no value until an amending provision fixes it.',
    'Until a variation to this Agreement provides one, there is no {term}.',
    'The Parties have not yet settled the {term}, which has no value until a variation fixes it.',
    'No value is stated for the {term}; it is to be supplied by an amending provision.',
    'The {noun} under this clause is left to be agreed, and the {term} has no value before then.',
    'The {term} is to be fixed by a later amending provision, and none applies until it is.',
)
DEFINITION = (
    '"{term}" means the {noun} stated in the clause headed "{heading}".',
    '"{term}" has the meaning given to the {noun} in the clause entitled "{heading}".',
    'The expression "{term}" refers to the {noun} dealt with in the clause headed "{heading}".',
    '"{term}" means the {noun} that the clause titled "{heading}" provides for.',
    '"{term}": the {noun} fixed (or left unfixed) by the clause headed "{heading}".',
    'For this Agreement, "{term}" is the {noun} under the clause "{heading}".',
    '"{term}" shall mean the {noun} governed by the clause headed "{heading}".',
    'References to "{term}" are to the {noun} in the clause with the heading "{heading}".',
)
SET = (
    '{ref} shall be {value}.',
    'For {ref}, {value} is substituted for any value stated or in force before.',
    '{ref} is amended so that it reads {value}.',
    'The Parties agree that {ref} will be {value}.',
    '{ref} is changed to {value}.',
    'With effect as stated below, {ref} becomes {value}.',
    'The Supplier and the Customer replace the existing value of {ref} with {value}.',
    'The value of {ref} is hereby fixed at {value}.',
    '{ref} is to stand at {value} in place of any earlier value.',
    'Substitute {value} for the existing value of {ref}.',
    'With effect from {cdate}, {ref} shall be {value}.',
    'From {cdate}, {ref} is {value}.',
)
EXCEPT = (
    'In the case of {cat}, {ref} shall be {value}.',
    '{ref} is {value} for {cat}; for all other work it is unchanged.',
    'An exception is inserted: for {cat}, {ref} is {value}.',
    'For {cat} alone, {ref} will stand at {value}.',
    '{ref}, as it applies to {cat}, is changed to {value}.',
    'The Parties agree that, for {cat}, {ref} is {value}.',
    '{ref} is amended by adding that for {cat} it is {value}.',
    'As regards {cat}, {ref} becomes {value}, and no other work is affected.',
    'A separate value applies to {cat}: {ref} is {value}.',
)
DISAPPLY = (
    '{ref} shall not apply for the period beginning on {start} and ending on {end}.',
    'The operation of {ref} is suspended from {start} to {end}, both dates included.',
    'From {start} until {end}, both dates included, {ref} is disapplied.',
    'No value of {ref} applies from {start} through {end}.',
    '{ref} is disapplied from {start} to {end}, both dates included.',
    'The Parties disapply {ref} for the period running from {start} to {end}, both dates included.',
    '{ref} is not to apply during the period from {start} to {end}, both dates included.',
    'For the period from {start} to {end}, both dates included, {ref} is set aside.',
    '{ref} is disapplied for {cat} from {start} to {end}, both dates included.',
    'For {cat}, {ref} shall not apply for the period beginning on {start} and ending on {end}.',
    'The operation of {ref} for {cat} is suspended from {start} to {end}, both dates included.',
    'No value of {ref} applies to {cat} from {start} through {end}.',
    'The Parties disapply {ref} for {cat} for the period running from {start} to {end}, both dates included.',
    '{ref} is not to apply to {cat} during the period from {start} to {end}, both dates included.',
    'For {cat}, and for the period from {start} to {end}, both dates included, {ref} is set aside.',
    'During the period from {start} to {end}, both dates included, {ref} is disapplied as it affects {cat}.',
)
REVOKE = (
    '{pref} is revoked.',
    'The Parties revoke {pref}.',
    '{pref} is hereby revoked in its entirety.',
    'With the effect stated below, {pref} is revoked.',
    'The provision made by {pref} is revoked.',
    'The Supplier and the Customer agree to revoke {pref}.',
    '{pref} is revoked, and the position that it altered is restored to that which would otherwise apply.',
    'Revocation of {pref} is agreed.',
)
LINK = (
    '{ref} shall be the same as {other} is on the day this paragraph takes effect.',
    'The value of {ref} is to equal the value that {other} has on the day this paragraph takes effect.',
    '{ref} is set to the value that {other} has on the day on which this paragraph takes effect.',
    'The Parties agree that {ref} will carry the value of {other} as it stands on the day this paragraph takes effect.',
    'The value of {ref} is fixed, from the day this paragraph takes effect, at whatever {other} is on that day.',
    '{ref} becomes equal to {other}, taken as it stands on the day this paragraph takes effect.',
    'On the day this paragraph takes effect, {ref} is to be given the value that {other} then has.',
    'The value that {other} has on the day this paragraph takes effect is substituted for the value of {ref}.',
)
COMMENCE_DATE = (
    'This paragraph comes into force on {cdate}.',
    'This paragraph takes effect on {cdate}.',
    'The change made by this paragraph applies from {cdate}.',
    'This provision has effect from {cdate}.',
    'It shall come into operation on {cdate}.',
    'The effective date of this paragraph is {cdate}.',
    'This paragraph is to operate from {cdate}.',
    'This paragraph applies on and from {cdate}.',
)
COMMENCE_EVENT = (
    'This paragraph comes into force on the date on which {event}.',
    'This paragraph takes effect on the date of the instrument recording that {event}.',
    'The change made by this paragraph applies from the date on which {event}.',
    'This provision has effect from the day on which {event}.',
    'It shall come into operation on the date when {event}.',
    'This paragraph is to operate from the date on which {event}, as recorded in an instrument.',
    'This paragraph takes effect only on the date on which {event}.',
    'The effective date of this paragraph is the date on which {event}.',
)
RECORD = (
    'The Customer certifies that {event}.',
    'It is hereby recorded that {event}.',
    'This instrument records that {event}.',
    'The Customer confirms that {event}.',
    'The Supplier confirms that {event}.',
    'Both Parties confirm that {event}.',
    'Notice is given that {event}.',
    'It is certified that {event}.',
)
SUNSET = (
    'This paragraph ceases to have effect on {sdate}.',
    'The change made by this paragraph does not apply on or after {sdate}.',
    'This provision lapses on {sdate} and has no effect on or after that date.',
    'This paragraph shall cease to operate on {sdate}.',
    'On {sdate} this paragraph ceases to have effect.',
    'The effect of this paragraph stops on {sdate}, which is the first day on which it no longer applies.',
    'This paragraph is of no effect from {sdate} onwards.',
    'A sunset applies: this paragraph has no effect on or after {sdate}.',
)
SUNSET_LATER = (
    '{pref} shall cease to have effect on {sdate}.',
    'The change made by {pref} does not apply on or after {sdate}.',
    '{pref} lapses on {sdate} and has no effect on or after that date.',
    '{pref} shall cease to operate on {sdate}.',
    'On {sdate}, {pref} ceases to have effect.',
    'The effect of {pref} stops on {sdate}, which is the first day on which it no longer applies.',
    '{pref} is of no effect from {sdate} onwards.',
    'A sunset is applied to {pref}, which has no effect on or after {sdate}.',
)
CORRECT = (
    'In {pref}, for {what} {old} read {new}.',
    '{pref} is corrected: {what} was printed as {old} in error and should have read {new}.',
    'A slip in {pref} is corrected by substituting {new} for {old} as {what}.',
    '{pref} shall be read as if {what} had been stated as {new} instead of {old}.',
    'Correction to {pref}: {what} is {old} in error, and {new} was intended.',
    'The Parties correct {pref} by replacing {old} with {new} as {what}.',
    'In {pref}, {what} appears as {old}; that is a clerical error for {new}.',
    'Where {pref} gives {what} as {old}, {new} is to be read instead.',
)
CORRECT_WHAT = {'value': 'the value', 'commencement': 'the date on which it comes into force',
                'sunset': 'the date on which it ceases to have effect',
                'end': 'the last day of the period of disapplication'}
RENUMBER = (
    'With effect from {cdate}, clauses {a} to {b} of the Agreement are renumbered as clauses {c} to {d}, and references to those clauses follow the renumbering.',
    'The Parties insert new clauses and, from {cdate}, renumber clauses {a} to {b} as clauses {c} to {d}; every reference to a renumbered clause follows it.',
    'From {cdate}, the clauses numbered {a} to {b} in the Agreement carry the numbers {c} to {d}, and references to them follow the renumbering.',
    'Clauses {a} to {b} of the Agreement become clauses {c} to {d} with effect from {cdate}, references to them following the renumbering.',
    'As from {cdate}, the Agreement is renumbered so that clauses {a} to {b} are clauses {c} to {d}, and references follow.',
    'To make room for new clauses, clauses {a} to {b} are renumbered {c} to {d} from {cdate}, and references to them follow the renumbering.',
    'The numbering of the Agreement changes on {cdate}: clauses {a} to {b} become {c} to {d}, and references follow the clauses they name.',
    'On and from {cdate}, former clauses {a} to {b} are numbered {c} to {d}, and each reference to a renumbered clause follows it.',
)
DEFINE = (
    'In this instrument, "{short}" means {ref}.',
    'The Parties use "{short}" below to mean {ref}.',
    'For the purposes of this instrument and every later instrument, "{short}" denotes {ref}.',
    '"{short}" is defined as {ref}.',
    'References in this instrument to "{short}" are references to {ref}.',
    'Where this instrument says "{short}", it means {ref}.',
    'The expression "{short}" stands for {ref}.',
    'In what follows "{short}" is shorthand for {ref}.',
)
DECOY = (
    'The Supplier asked whether {ref} could be {value}; the Customer has not agreed, and nothing in this instrument changes it.',
    'The Customer suggested that {ref} should be {value}, but that suggestion was not accepted.',
    'A proposal that {ref} be {value} was discussed and withdrawn.',
    'The Parties note a request to set {ref} at {value}; the request has not been agreed.',
    'It was proposed at the last meeting that {ref} become {value}, and the proposal remains under consideration.',
    "The Supplier's offer to accept {value} for {ref} was declined.",
    'Neither party has agreed that {ref} should be {value}; the possibility was raised for discussion only.',
    'The Customer expects to propose {value} for {ref} in due course, which would be a separate matter.',
)
DECOY_EVENT = (
    'The Customer expects to certify, in due course, that {event}.',
    'The Supplier has asked the Customer to certify that {event}; no certificate has yet been issued.',
    'The Parties hope that it will soon be possible to record that {event}.',
)
PREF_FRAMES = ('paragraph {n} of {title}', 'paragraph {n} of the {kind} dated {date}')
REF_FRAMES = {
    'term': 'the {term}',
    'short': 'the {short}',
    'number': ('the {noun} stated in clause {n}', 'the {noun} fixed by clause {n}', 'the {noun} in clause {n}'),
    'heading': 'the {noun} in the clause headed "{heading}"',
    'description': {
        'days': 'the period that the clause on {topic} allows to the {party}',
        'pct': 'the percentage that the clause on {topic} fixes',
        'money': 'the amount that the clause on {topic} fixes',
        'count': 'the number that the clause on {topic} fixes',
        'officer': 'the officer that the clause on {topic} names',
    },
}
# Statements. {term} is the defined term of the parameter; {cat} a category; value as in VALUE_FORMATS.
STATEMENTS = {
    'value': ('On {date}, the {term} was {value}.', 'The {term} stood at {value} on {date}.',
              'As at {date}, the value of the {term} was {value}.'),
    'value_cat': ('On {date}, the {term} for {cat} was {value}.', 'The {term} applicable to {cat} stood at {value} on {date}.',
                  'As at {date}, the value of the {term} for {cat} was {value}.'),
    'none': ('On {date}, the {term} had no value in force.', 'No value of the {term} was in force on {date}.'),
    'none_cat': ('On {date}, the {term} for {cat} had no value in force.',
                 'No value of the {term} for {cat} was in force on {date}.'),
    'count': ('The number of instruments in the file with an amending provision for the {term} that had taken effect on or before {date} was {n}.',
              'On {date}, the count of instruments in the file that had put into effect an amending provision for the {term} stood at {n}.',
              'As at {date}, {n} was the number of instruments in the file that had brought an amending provision for the {term} into effect.'),
    'total': ('The number of amending provisions in the file, for all parameters together, that had taken effect on or before {date} was {n}.',
              'Counting every parameter, {n} amending provisions in the file had taken effect on or before {date}.',
              'As at {date}, the number of amending provisions over all parameters taken together that had come into effect was {n}.'),
    'first': ('The first date on which the {term} was {value} was {date}.', 'The {term} first stood at {value} on {date}.',
              '{date} was the first date on which the {term} was {value}.'),
    'differs': ('The value of the {term}{catpart} on {date} differed from its value on {date2}.',
                'The {term}{catpart} had a different value on {date} from the value it had on {date2}.'),
    'same': ('The value of the {term}{catpart} on {date} was the same as its value on {date2}.',
             'The {term}{catpart} had the same value on {date} as on {date2}.'),
    # Whole-file statements of the other shapes (used from size 2).
    'between': ('The number of amending provisions in the file, for all parameters together, that took effect between {date} and {date2}, both dates included, was {n}.',
                'Counting every parameter, {n} amending provisions in the file took effect on or after {date} and on or before {date2}.',
                'From {date} to {date2}, both dates included, {n} was the number of amending provisions over all parameters taken together that came into effect.'),
    'class_dis': ('The number of disapplications in the file, for all parameters together, that had taken effect on or before {date} was {n}.',
                  'Counting every parameter, {n} disapplications in the file had taken effect on or before {date}.',
                  'As at {date}, the number of disapplications over all parameters taken together that had come into effect was {n}.'),
    'class_exc': ('The number of exceptions in the file, for all parameters together, that had taken effect on or before {date} was {n}.',
                  'Counting every parameter, {n} exceptions in the file had taken effect on or before {date}.',
                  'As at {date}, the number of exceptions over all parameters taken together that had come into effect was {n}.'),
    'on': ('The number of amending provisions in the file, for all parameters together, that had effect on {date}, whether or not another provision prevailed over them, was {n}.',
           'Counting every parameter, {n} amending provisions in the file were in effect on {date}, including any that another provision overrode.',
           'On {date}, {n} was the number of amending provisions over all parameters taken together that had effect, whether or not another provision prevailed over them.'),
    'params': ('The number of parameters for which at least one amending provision had taken effect on or before {date} was {n}.',
               'As at {date}, {n} parameters of the Agreement had at least one amending provision that had come into effect.',
               'On {date}, {n} was the number of parameters with an amending provision that had taken effect on or before that date.'),
    'insts': ('The number of instruments in the file that hold at least one amending provision that had taken effect on or before {date} was {n}.',
              'Counting each instrument once, {n} instruments in the file held an amending provision, of any parameter, that had taken effect on or before {date}.',
              'As at {date}, {n} was the number of instruments that hold an amending provision, for any parameter, that had come into effect.'),
    'never': ('The number of amending provisions in the file, for all parameters together, that never took effect on any date was {n}.',
              'Counting every parameter, {n} amending provisions in the file never came into effect on any date.',
              'Of the amending provisions in the file, {n} never had effect on any date, whatever the parameter.'),
}

ORIGIN_TERMS = ('Business Day', 'Affiliate', 'Contract Year', 'Statement of Work', 'Deliverable', 'Customer Data',
                'Customer Site', 'Governance Board', 'Good Industry Practice', 'Charges', 'Services',
                'Supplier Personnel', 'Policies', 'Project Manager', 'Service Level', 'Effective Date',
                'Confidential Information', 'Intellectual Property Rights', 'Key Personnel', 'Mobilisation',
                'Personal Data', 'Applicable Law', 'Change Request', 'Exit Plan', 'Force Majeure Event',
                'Insolvency Event', 'Material Breach', 'Service Credit', 'Term', 'Working Hours')
SHORT_ADJ = ('Interim', 'Standard', 'Revised', 'Operative', 'Working', 'Core', 'Agreed', 'Primary', 'Settled',
             'Current', 'Adjusted', 'Contract', 'Site', 'Joint', 'Reserve', 'Fixed', 'Baseline', 'Local')
SHORT_NOUN = ('Window', 'Allowance', 'Figure', 'Ceiling', 'Mark', 'Limit', 'Trigger', 'Allocation', 'Setting',
              'Measure', 'Reference', 'Entry', 'Lead', 'Level')


# ---------------------------------------------------------------------------------------------------------
# The facts and the one interpreter. The key, the flawed readers and every outside checker use these two.
#
# `case['events']` is a list of JSON-serialisable facts. Each has an `id` and a `kind`:
#   parameter   a clause of the agreement that holds a value (`value` None when the clause leaves it open)
#   definition  a defined term, in the agreement (`source` 'agreement') or in an instrument ('instrument')
#   amendment   substitute / exception / disapply for a parameter (+ commencement, own sunset, reference style)
#   event       a commencement event; `date` None when no instrument in the file records it
#   revocation, correction, sunset (a later instrument ending an earlier provision), renumbering; the first three
#   carry the `parameter` of the provision they act on
# `requires` lists the facts that must be in the list for a fact to be established.
# ---------------------------------------------------------------------------------------------------------

def _d(value):
    return value if isinstance(value, datetime.date) else datetime.date.fromisoformat(value)


def _order(fact):
    return (fact['date'], fact['paragraph'])


_CACHE = {}
_READING = {'legacy': False}


@contextlib.contextmanager
def legacy_reading():
    """For the length of the block, a subset is read as it was before the rule of `_Index.meaning` (a fact whose
    requirements are not all present, whatever they are, is not established, and a missing fact that could matter makes
    the answer not established). The generator keeps drawing the dossiers of size 1 under it, so that they stay the
    bytes they were; nothing that reads a key or checks an answer uses it."""
    saved = _READING['legacy']
    _READING['legacy'] = True
    try:
        yield
    finally:
        _READING['legacy'] = saved


def _index(events):
    legacy = _READING['legacy']
    hit = _CACHE.get((id(events), legacy))
    if hit is not None and hit[0] is events and hit[2] == len(events):
        return hit[1]
    index = _Index(events, legacy)
    if len(_CACHE) > 64:
        _CACHE.clear()
    _CACHE[(id(events), legacy)] = (events, index, len(events))
    return index


class _Index:
    def __init__(self, events, legacy=False):
        self.legacy = legacy
        self.by_id = {e['id']: e for e in events}
        self.params, self.amend, self.revokes, self.corrections, self.sunsets = {}, {}, {}, {}, {}
        self.side = {}
        self.memo, self.known, self.busy = {}, {}, set()
        for e in events:
            kind = e['kind']
            if kind == 'parameter':
                self.params[e['parameter']] = e
            elif kind == 'amendment':
                self.amend.setdefault(e['parameter'], []).append(e)
            elif kind in ('revocation', 'correction', 'sunset'):
                table = {'revocation': self.revokes, 'correction': self.corrections, 'sunset': self.sunsets}[kind]
                table.setdefault(e['target'], []).append(e)
                self.side.setdefault(e['parameter'], []).append(e)
        for facts in self.amend.values():
            facts.sort(key=_order)
        for table in (self.corrections, self.sunsets):
            for facts in table.values():
                facts.sort(key=_order)

    def meaning(self, fact):
        """The facts that must be present, and themselves readable, for a fact to mean anything. A subset is read as
        the whole file, under the procedure's own rules: a fact that cannot be read without something the subset lacks
        (a provision made through an expression whose definition is absent, a revocation, correction or later sunset of
        an absent provision, a clause number whose renumbering is absent) is left out. What a provision waits on is not
        part of its meaning: a provision whose commencement event the subset lacks reads as the procedure reads it
        when no instrument records the event, that is, it never has effect. A provision that takes over the value of
        another parameter does not need that parameter's clause: without it the other parameter has no value
        (procedure rule 3), and the provision gives none. Of its requirements, in the order `facts` writes them (its
        own parameter, the other parameter, the event), the other parameter's is the last that is not the event."""
        need = list(fact.get('requires', ()))
        if self.legacy:
            return need
        commence = fact.get('commencement')
        if commence and commence['type'] == 'event' and fact['kind'] in ('amendment', 'revocation'):
            need = [n for n in need if n != commence['event']]
        link = fact.get('link')
        if link and fact['kind'] == 'amendment' and link['parameter'] not in self.params and need:
            need = need[:-1]
        return need

    def established(self, fact):
        got = self.known.get(fact['id'])
        if got is None:
            got = True
            self.known[fact['id']] = True          # the lists of requirements are acyclic
            for need in self.meaning(fact):
                if need not in self.by_id or not self.established(self.by_id[need]):
                    got = False
                    break
            self.known[fact['id']] = got
        return got

    def correction(self, fact, field, reader):
        """The corrected value of a field of an amendment, or None. A reader that is not the true one sees the
        corrections that its shortcut lets it see: `applies_everything` sees corrections of values and of the
        end of a disapplication."""
        if reader is not None and not (reader == 'applies_everything' and field in ('value', 'end')):
            return None
        found = None
        for item in self.corrections.get(fact['id'], ()):
            if item['field'] == field and self.established(item):
                found = item['new']
        return found

    def commence(self, fact, reader):
        if reader == 'applies_everything':
            return _d(fact['date']) if fact['operation'] != 'disapply' else _d(fact['commencement']['date'])
        c = fact['commencement']
        if c['type'] == 'event':
            event = self.by_id.get(c['event'])
            return _d(event['date']) if event is not None and event['date'] else None
        new = self.correction(fact, 'commencement', reader)
        return _d(new or c['date'])

    def sunset(self, fact, reader):
        if fact['operation'] == 'disapply':
            return _d(self.correction(fact, 'end', reader) or fact['ends']) + DAY
        if reader == 'applies_everything':
            return None
        own = self.correction(fact, 'sunset', reader) or fact['sunset']
        found = [_d(own)] if own else []
        if reader is None:
            found += [_d(x['sunset']) for x in self.sunsets.get(fact['id'], ()) if self.established(x)]
        return min(found) if found else None

    def revocation_start(self, revocation):
        c = revocation['commencement']
        if c['type'] == 'event':
            event = self.by_id.get(c['event'])
            return _d(event['date']) if event is not None and event['date'] else None
        return _d(c['date'])

    def in_effect(self, fact, day, reader):
        start = self.commence(fact, reader)
        if start is None or day < start:
            return False
        end = self.sunset(fact, reader)
        if end is not None and day >= end:
            return False
        if reader is None:
            for item in self.revokes.get(fact['id'], ()):
                if self.established(item):
                    began = self.revocation_start(item)
                    if began is not None and began <= day:
                        return False
        return True

    def took(self, fact, reader):
        """The date on which an amending provision took effect (it had effect on its commencement date), or None
        when it never did: no commencement, or ended (or revoked) on or before it."""
        began = self.commence(fact, reader)
        if began is not None and self.in_effect(fact, began, reader):
            return began
        return None

    def visible(self, fact, reader):
        if reader == 'reads_agreement_only':
            return False
        if reader == 'searches_by_clause_number':
            ref, own = fact['reference'], self.params.get(fact['parameter'])
            return ref['style'] == 'number' and own is not None and ref['number'] == own['clause']
        return True

    def hazard(self, key, day, reader):
        """Only in the legacy reading (`legacy_reading`, which keeps the dossiers of size 1 as they were); the current
        reading never has a hazard. True when a fact that could matter for this parameter on this day is not established: an amendment that
        lacks something it requires, or (for the true reading) a revocation, correction or later sunset that lacks
        something it requires, such as the provision or the instrument it acts on."""
        if reader == 'reads_agreement_only' or not self.legacy:
            return False
        for fact in self.amend.get(key, ()):
            if reader == 'searches_by_clause_number' and fact['reference']['style'] != 'number':
                continue
            if not self.established(fact) and _d(fact['date']) <= day:
                return True
            if fact.get('link') and self.established(fact):
                began = self.commence(fact, reader)
                if began is not None and began <= day and self.hazard(fact['link']['parameter'], began, reader):
                    return True
        if reader is None:
            for fact in self.side.get(key, ()):
                if not self.established(fact) and (fact['kind'] == 'correction' or _d(fact['date']) <= day):
                    return True
        return False

    def original(self, key):
        value = self.params[key]['value']
        return NO_VALUE if value is None else value

    def value(self, key, day, category, reader):
        spot = (reader, key, category, day)
        if spot in self.memo:
            return self.memo[spot]
        if spot in self.busy:
            return NOT_ESTABLISHED                      # a value that rests on itself is not established
        self.busy.add(spot)
        out = NOT_ESTABLISHED
        param = self.params.get(key)
        if param is not None and self.established(param):
            if reader == 'reads_agreement_only':
                out = self.original(key)
            elif not self.hazard(key, day, reader):
                best = None
                for fact in self.amend.get(key, ()):
                    if fact['category'] not in (None, category) or not self.established(fact):
                        continue
                    if self.visible(fact, reader) and self.in_effect(fact, day, reader):
                        best = fact                      # the list is in order; the last one in effect prevails
                if best is None:
                    out = self.original(key)
                elif best['operation'] == 'disapply':
                    out = NO_VALUE
                elif best.get('link'):                  # the value another parameter has on the day this one takes effect
                    other = best['link']['parameter']
                    out = self.value(other, self.commence(best, reader), None, reader)
                    if out is NOT_ESTABLISHED and not self.legacy and other not in self.params:
                        out = NO_VALUE                  # a parameter whose clause is absent has no value
                else:
                    new = self.correction(best, 'value', reader)
                    out = best['value'] if new is None else new
        self.busy.discard(spot)
        self.memo[spot] = out
        return out


def value_in_force(events, parameter, date, category=None, reader=None):
    """The value of a parameter on a date for a category (None: the general value), computed from any subset of the
    facts. Returns the value, NO_VALUE when the clause is disapplied or has no value yet, or None (NOT_ESTABLISHED)
    when the clause of the parameter is missing from `events`. `reader` is None for the true reading or one of
    READERS."""
    return _index(events).value(parameter, _d(date), category, reader)


def instruments_in_effect(events, parameter, date, reader=None):
    """The number of instruments with an amending provision for the parameter that had taken effect on or before
    the date, or None when it is not established."""
    index, day = _index(events), _d(date)
    param = index.params.get(parameter)
    if param is None or not index.established(param):
        return None
    if reader == 'reads_agreement_only':
        return 0
    if index.hazard(parameter, day, reader):
        return None
    titles = set()
    for fact in index.amend.get(parameter, ()):
        if index.established(fact) and index.visible(fact, reader):
            began = index.commence(fact, reader)
            if began is not None and began <= day and index.in_effect(fact, began, reader):
                titles.add(fact['instrument'])
    return len(titles)


def provisions_in_effect(events, date, reader=None):
    """The number of amending provisions (substitutions, exceptions, disapplications) of every parameter together that
    had taken effect on or before the date, or None when a fact that could bear on it is not established."""
    index, day = _index(events), _d(date)
    if reader == 'reads_agreement_only':
        return 0
    keys = sorted(set(index.amend) | set(index.side))
    if any(index.hazard(key, day, reader) for key in keys):
        return None
    found = 0
    for key in sorted(index.amend):
        for fact in index.amend[key]:
            if index.established(fact) and index.visible(fact, reader):
                began = index.commence(fact, reader)
                if began is not None and began <= day and index.in_effect(fact, began, reader):
                    found += 1
    return found


def census(events, reader, day):
    """Every established amending provision the reader can see, as (fact, the date it took effect or None), for the
    whole-file statements; None when a fact that could bear on a provision up to `day` is not established. The true
    reading is `reader` None."""
    index = _index(events)
    keys = sorted(set(index.amend) | set(index.side))
    if any(index.hazard(key, day, reader) for key in keys):
        return None
    out = []
    for key in sorted(index.amend):
        for fact in index.amend[key]:
            if index.established(fact) and index.visible(fact, reader):
                out.append((fact, index.took(fact, reader)))
    return index, out


def whole_file_count(events, query, reader=None):
    """The value of a statement about the whole file, for the shapes other than 'total': a count over every amending
    provision of every parameter (see rule 10 of procedure.md). None when not established."""
    kind = query['kind']
    if reader == 'reads_agreement_only':
        return 0
    if kind == 'never':
        got = census(events, reader, datetime.date.max)
        if got is None:
            return None
        return sum(1 for _, began in got[1] if began is None)
    day = _d(query['date2'] if kind == 'between' else query['date'])
    got = census(events, reader, day)
    if got is None:
        return None
    index, rows = got
    if kind == 'on':
        return sum(1 for fact, _ in rows if index.in_effect(fact, day, reader))
    taken = [(fact, began) for fact, began in rows if began is not None and began <= day]
    if kind == 'between':
        low = _d(query['date'])
        return sum(1 for _, began in taken if began >= low)
    if kind == 'class':
        wanted = {'disapplication': 'disapply', 'exception': 'exception'}[query['class']]
        return sum(1 for fact, _ in taken if fact['operation'] == wanted)
    if kind == 'params':
        return len({fact['parameter'] for fact, _ in taken})
    return len({fact['instrument'] for fact, _ in taken})                 # 'insts'


def counted(events, query):
    """The ids of the amending provisions that a whole-file statement of a provision-counting shape counts (the true
    reading): 'total', 'between', 'class', 'on' and 'never'. Empty for the other shapes, which count parameters or
    instruments."""
    kind = query['kind']
    got = census(events, None, datetime.date.max if kind in ('never', 'on') else _d(query.get('date2') or query['date']))
    if got is None or kind not in ('total', 'between', 'class', 'on', 'never'):
        return set()
    index, rows = got
    if kind == 'never':
        return {fact['id'] for fact, began in rows if began is None}
    day = _d(query['date2'] if kind == 'between' else query['date'])
    if kind == 'on':
        return {fact['id'] for fact, _ in rows if index.in_effect(fact, day, None)}
    low = _d(query['date']) if kind == 'between' else None
    wanted = {'disapplication': 'disapply', 'exception': 'exception'}[query['class']] if kind == 'class' else None
    return {fact['id'] for fact, began in rows if began is not None and began <= day and (low is None or began >= low)
            and (wanted is None or fact['operation'] == wanted)}


def first_date(events, parameter, value, reader=None):
    """The first date on which the general value of the parameter was `value`: an ISO date, NONE_VALUE if the value never was `value`, or None."""
    index = _index(events)
    param = index.params.get(parameter)
    if param is None or not index.established(param):
        return None
    start = _d(param['from'])
    if index.hazard(parameter, datetime.date.max, reader):
        return None
    days = {start}
    for fact in index.amend.get(parameter, ()):
        if fact['category'] is None and index.established(fact) and index.visible(fact, reader):
            for day in (index.commence(fact, reader), index.sunset(fact, reader)):
                if day is not None and day > start:
                    days.add(day)
            if reader is None:
                for item in index.revokes.get(fact['id'], ()):
                    if index.established(item):
                        began = index.revocation_start(item)
                        if began is not None and began > start:
                            days.add(began)
    for day in sorted(days):
        if index.value(parameter, day, None, reader) == value:
            return day.isoformat()
    return NONE_VALUE


def answer_from(events, query, reader=None):
    """The answer to a query of a claim from the facts: a value (kind 'value'), a count ('count'), an ISO date or
    NONE_VALUE ('first'), or 'same' / 'different' ('differs'); None when not established."""
    kind = query['kind']
    if kind == 'value':
        return value_in_force(events, query['parameter'], query['date'], query.get('category'), reader)
    if kind == 'count':
        return instruments_in_effect(events, query['parameter'], query['date'], reader)
    if kind == 'total':
        return provisions_in_effect(events, query['date'], reader)
    if kind in WHOLE_FILE:
        return whole_file_count(events, query, reader)
    if kind == 'first':
        return first_date(events, query['parameter'], query['value'], reader)
    first = value_in_force(events, query['parameter'], query['date'], query.get('category'), reader)
    second = value_in_force(events, query['parameter'], query['date2'], query.get('category'), reader)
    if first is None or second is None:
        return None
    return 'different' if first != second else 'same'


def answer(query, events, reader=None):
    """The value of a statement from ANY SUBSET of the facts, for the true reading (`reader` None) or a named flawed
    reader, the subset read as if it were the whole file (see `_Index.meaning`). None: what the statement is about
    cannot be computed (the clause of its parameter is absent, or the query is not one); a count over the whole file
    is never None. NONE_VALUE: established that there is no value. Otherwise the value as a JSON type. Never raises."""
    try:
        return answer_from(events, query, reader)
    except Exception:       # noqa: BLE001 - a subset is never a reason to fail
        return None


def value_form(query):
    """How the value of a statement is written: integer, decimal, date, month, name or ('choice', (words...))."""
    kind = query.get('kind')
    if kind == 'first':
        return 'date'
    if kind == 'differs':
        return ('choice', ('same', 'different'))
    if kind == 'count' or kind in WHOLE_FILE:
        return 'integer'
    return {'days': 'integer', 'money': 'integer', 'count': 'integer', 'pct': 'decimal',
            'officer': 'name'}.get(query.get('value_kind'), 'integer')


def relevant(query, events):
    """The ids of the facts of the statement's parameter and of everything they refer to: its clause and defined
    terms, its amendments, what acts on them (revocations, corrections, later sunsets), the events that commence
    them, the definitions and the renumberings they are made through."""
    try:
        if query.get('kind') in WHOLE_FILE:                          # a count over every amending provision
            return sorted(f['id'] for f in whole_file_events(events))
        return sorted(f['id'] for f in local_events(events, query['parameter']))
    except Exception:       # noqa: BLE001
        return []


def depends(events):
    """For every event of the list, what it cannot be read without, kept apart by kind (direct ones only; a checker
    closes `name`):
        name    what fixes how the event's paragraph names its parameter INDIRECTLY: the definition behind a term or an
                expression it uses, the renumberings behind a clause number it uses, and, for a parameter named by a
                description of what its clause says ("the period that the clause on late delivery allows"), that clause;
        amount  what its value is stated by reference to: the definition through which a provision that takes over
                another parameter's value names that parameter (not that parameter's clause, which the reading does
                without);
        target  what the event is about: the provision a revocation, a correction or a later sunset acts on, and for a
                provision, a definition or a defined expression the clause of its parameter, which it names directly
                (by its number or heading) or is made through.
    Each is a list of ids of events of the list, and is a dependency of the reading: without the event named, the
    reading leaves the dependent event out (`_Index.meaning`; a term or an expression is read through its definition,
    which is itself made through the clause). The event a provision waits on is not among them."""
    index = _Index(events)
    ids = {e['id'] for e in events}
    clause = {key: fact['id'] for key, fact in index.params.items()}
    renumberings = {e['id'] for e in events if e['kind'] == 'renumbering'}
    out = {}
    for e in events:
        need = [n for n in index.meaning(e) if n in ids]
        name, amount, target = [], [], []
        if e['kind'] in ('revocation', 'correction', 'sunset'):
            target = [n for n in need if n == e['target']]
        elif e['kind'] in ('amendment', 'definition'):
            ref = e.get('reference') or {}
            style, own = ref.get('style'), clause.get(e['parameter'])
            if e['kind'] == 'definition' and e.get('source') == 'agreement':
                named = set()                                       # a definition of the agreement: about its clause
            elif style in ('term', 'short'):
                named = {ref.get('definition')}
            elif style == 'description':
                named = {own}
            else:                                                   # a number or a heading: named directly
                named = renumberings if style == 'number' else set()
            name = [n for n in need if n in named]
            target = [own] if own in ids and own != e['id'] and own not in name else []
            amount = [n for n in need if n not in named and n != own]
            link = e.get('link')
            if link and link['parameter'] in index.params:      # the other parameter's clause is read without
                amount = [n for n in amount if n != clause.get(link['parameter'])]
        out[e['id']] = {'name': name, 'amount': amount, 'target': target}
    return out


def needed(query, events):
    """The ids of the facts whose removal from `events` changes the answer or makes it not established."""
    base = answer(query, events)
    out = []
    for fid in relevant(query, events):
        if answer(query, [f for f in events if f['id'] != fid]) != base:
            out.append(fid)
    return out


# ---------------------------------------------------------------------------------------------------------
# Planning: the agreement, the instruments and what they do, as objects. `facts` turns them into events.
# ---------------------------------------------------------------------------------------------------------

class O:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def slug(term):
    return term.lower().replace(' ', '_')


def number_at(orig, day, renumbers):
    """The number the clause with original number `orig` bears on `day`."""
    n = orig
    for r in sorted(renumbers, key=lambda r: r.commence):
        if r.commence <= day and n >= r.from_n:
            n += r.k
    return n


def make_params(rng):
    unset = set(rng.sample([slug(r[3]) for r in PARAM_ROWS], rng.randint(11, 15)))
    params = {}
    for row in PARAM_ROWS:
        kind, part, topic, term = row[:4]
        extra = row[4:]
        p = O(key=slug(term), kind=kind, part=part, topic=topic, term=term, noun=NOUNS[kind],
              heading=topic[0].upper() + topic[1:])
        p.unset = p.key in unset
        if kind == 'days':
            p.party, p.act, p.trigger = extra
        elif kind in ('pct', 'money'):
            p.subject = extra[0]
        elif kind == 'count':
            p.unit, p.conseq = extra
        else:
            p.party, p.duty = extra
        p.orig = None if p.unset else rng.choice(POOLS[kind])
        params[p.key] = p
    return params


def make_clauses(rng, params):
    total = rng.randint(126, 158)
    background = [(part, name) for part, names in enumerate(BACKGROUND) for name in names]
    chosen = rng.sample(background, total - 1 - len(params))
    by_part = [[] for _ in PARTS]
    for p in params.values():
        by_part[p.part].append(O(kind='param', heading=p.heading, param=p))
    for part, name in chosen:
        by_part[part].append(O(kind='bg', heading=name, param=None))
    clauses, parts = [], []
    for part, items in enumerate(by_part):
        rng.shuffle(items)
        if part == 0:
            items.insert(0, O(kind='defs', heading='Definitions', param=None))
        if items:
            parts.append((part, len(clauses)))
        for c in items:
            c.number, c.part = len(clauses) + 1, part
            if c.param is not None:
                c.param.number = c.number
            clauses.append(c)
    return clauses, parts


POOL_SIZE = {1: 36, 2: 43, 3: 50, 4: 57}      # parameters that later instruments change, by size


def plan(rng, size=1):
    """The agreement and the instruments. `size` multiplies what the instruments hold and the period they cover; at
    size 1 every draw is the one the module made before sizes existed."""
    T = size
    P = O(e0=datetime.date(2031, rng.randint(1, 12), rng.randint(1, 28)))
    P.params = make_params(rng)
    keys = list(P.params)
    P.clauses, P.parts = make_clauses(rng, P.params)
    P.total = len(P.clauses)
    e0 = P.e0
    used, insts, provs, events, renumbers, corrections = set(), [], [], [], [], []
    seen = {k: {p.orig} for k, p in P.params.items()}
    shuffled = list(EVENT_PHRASES if T == 1 else EVENT_PHRASES + EVENT_PHRASES_MORE)
    rng.shuffle(shuffled)

    def fresh(day):
        while day in used:
            day += DAY
        used.add(day)
        return day

    def new_inst(kind, day):
        inst = O(kind=kind, date=fresh(day), items=[])
        insts.append(inst)
        return inst

    nd = rng.randint(12 * T, 15 * T)
    deeds = [new_inst('deed' if rng.random() < 0.65 else 'letter', e0 + DAY * o)
             for o in sorted(rng.sample(range(25, 1300 * T), nd))]
    pool = rng.sample(keys, POOL_SIZE[T])

    def choose():
        if T == 1:
            return P.params[rng.choice(pool)]
        # a larger file changes a parameter more often: keep to those whose pool of values has room left
        return P.params[rng.choice([k for k in pool if len(POOLS[P.params[k].kind]) - len(seen[k]) >= 6])]

    def new_value(p):
        options = [v for v in POOLS[p.kind] if v not in seen[p.key]]
        if not options:
            return None
        value = rng.choice(options)
        seen[p.key].add(value)
        return value

    def later_index(i):
        """The index of a later instrument, after the instrument at index i."""
        return rng.randrange(i + 1, nd if T == 1 else min(nd, i + 16))

    def has(inst, p):
        return any(x.type == 'value' and x.param is p for x in inst.items)

    def carrier(p, lo=0, hi=None):
        options = [d for d in deeds[lo:hi] if not has(d, p)]
        return rng.choice(options) if options else None

    def fixed(inst):
        return inst.date + DAY * rng.choice((0, 0, 3, 7, 14, 21, 30, 45, 60, 90, 120))

    def add(inst, op, p, cat, value, commence, sunset=None, ends=None, link=None):
        prov = O(type='value', op=op, param=p, cat=cat, value=value, commence=commence, sunset=sunset, ends=ends,
                 inst=inst, corrections=[], revokers=[], later=[], link=link,
                 other_style=rng.choice(('term', 'heading', 'description')) if link is not None else None)
        inst.items.append(prov)
        provs.append(prov)
        return prov

    def new_event():
        ev = O(phrase=shuffled.pop(), record=None)
        events.append(ev)
        return ev

    def record(ev, after, kind=None):
        cert = new_inst(kind or rng.choice(('certificate', 'notice')), after.date + DAY * rng.randint(12, 300))
        ev.record = O(type='record', event=ev, inst=cert)
        cert.items.append(ev.record)

    for _ in range(rng.randint(5 * T, 7 * T)):                         # successive substitutions
        p = choose()
        for idx in sorted(rng.sample(range(nd), rng.randint(2, 4))):
            if not has(deeds[idx], p):
                add(deeds[idx], 'substitute', p, None, new_value(p), ('date', fixed(deeds[idx])))
    for occurs in [True] * rng.randint(4 * T, 5 * T) + [False] * rng.randint(2 * T, 3 * T):   # an event, recorded later or never
        p = choose()
        inst = carrier(p, 0, nd - 2)
        if inst is None or not shuffled:
            continue
        ev = new_event()
        cat = rng.choice(CATEGORIES) if rng.random() < 0.2 else None
        prov = add(inst, 'exception' if cat else 'substitute', p, cat, new_value(p), ('event', ev))
        if rng.random() < 0.25:
            prov.sunset = inst.date + DAY * rng.randint(300, 700)
        if occurs:
            record(ev, inst)
    for _ in range(rng.randint(3 * T, 4 * T)):                         # a sunset in the provision itself
        p = choose()
        inst = carrier(p, 0, nd - 1)
        if inst is not None:
            start = fixed(inst)
            add(inst, 'substitute', p, None, new_value(p), ('date', start), sunset=start + DAY * rng.randint(40, 400))
    for _ in range(rng.randint(2 * T, 3 * T)):                         # a sunset in a later instrument
        p = choose()
        i = rng.randrange(nd - 2)
        inst = carrier(p, i, i + 1)
        if inst is None:
            continue
        start = fixed(inst)
        prov = add(inst, 'substitute', p, None, new_value(p), ('date', start))
        later = deeds[later_index(i)]
        sun = O(type='sunset', target=prov, inst=later,
                date=max(later.date, start) + DAY * rng.randint(20, 300))
        later.items.append(sun)
        prov.later.append(sun)
    for _ in range(rng.randint(4 * T, 5 * T)):                         # a revocation
        p = choose()
        i = rng.randrange(nd - 2)
        inst = carrier(p, i, i + 1)
        if inst is None:
            continue
        start = inst.date + DAY * rng.choice((0, 14, 30, 60, 120, 180, 240))
        prov = add(inst, 'substitute', p, None, new_value(p), ('date', start))
        later = deeds[later_index(i)]
        if rng.random() < 0.25 and shuffled:
            ev = new_event()
            record(ev, later)
            commence = ('event', ev)
        else:
            commence = ('date', later.date + DAY * rng.choice((0, 0, 10, 30, 60)))
        rev = O(type='revoke', target=prov, inst=later, commence=commence)
        later.items.append(rev)
        prov.revokers.append(rev)
    for _ in range(rng.randint(4 * T, 5 * T)):                         # an exception, sometimes displaced later
        p = choose()
        i = rng.randrange(nd - 2)
        inst = carrier(p, i, i + 1)
        if inst is None:
            continue
        add(inst, 'exception', p, rng.choice(CATEGORIES), new_value(p), ('date', fixed(inst)))
        if rng.random() < 0.5:
            later = carrier(p, i + 1)
            if later is not None:
                add(later, 'substitute', p, None, new_value(p), ('date', fixed(later)))
    for _ in range(rng.randint(3 * T, 4 * T)):                         # a disapplication for a period
        p = choose()
        notice = new_inst('notice', e0 + DAY * rng.randint(60, 1350 * T))
        start = notice.date + DAY * rng.choice((0, 5, 10, 25))
        add(notice, 'disapply', p, rng.choice(CATEGORIES) if rng.random() < 0.25 else None, None, ('date', start),
            ends=start + DAY * rng.randint(14, 160))
    linkers, targets = set(), set()
    for _ in range(rng.randint(6 * T, 9 * T)):                         # a value taken over from another parameter
        p = choose()
        options = [q for q in P.params.values() if q.kind == p.kind and q is not p and q.key not in linkers
                   and (q.key in pool or rng.random() < 0.3)]
        if p.key in targets or p.key in linkers or not options:
            continue
        inst = carrier(p, 0, nd - 1)
        if inst is None:
            continue
        other = rng.choice(options)
        add(inst, 'substitute', p, None, None, ('date', fixed(inst)), link=other)
        linkers.add(p.key)
        targets.add(other.key)
    first = rng.randrange(3, max(4, nd // 2))
    second = rng.randrange(first + 2, nd - 1)
    for idx, (low, high, shift) in ((first, (14, 40, rng.randint(2, 4))), (second, (60, 100, rng.randint(1, 3)))):
        inst = deeds[idx]
        renumbers.append(O(type='renumber', inst=inst, from_n=rng.randint(low, high), k=shift,
                           commence=inst.date + DAY * rng.choice((0, 0, 7, 14))))
        inst.items.append(renumbers[-1])
    if renumbers[1].commence <= renumbers[0].commence:
        renumbers[1].commence = renumbers[0].commence + DAY
    renumbers[0].last = P.total
    renumbers[1].last = P.total + renumbers[0].k
    for _ in range(rng.randint(4 * T, 5 * T)):                         # correction slips
        insts_slip = new_inst('slip', e0 + DAY * rng.randint(150, 1450 * T))
        for _ in range(rng.randint(1, 2)):
            targets = [t for t in provs if t.inst.date + DAY * 7 < insts_slip.date and not t.corrections and t.link is None]
            if not targets:
                break
            t = rng.choice(targets)
            fields = ['value'] * 3 if t.op != 'disapply' else []
            if t.op != 'disapply' and t.commence[0] == 'date':
                fields += ['commencement'] * 2
            if t.sunset is not None and t.op != 'disapply':
                fields.append('sunset')
            if t.op == 'disapply':
                fields += ['end'] * 3
            field = rng.choice(fields)
            if field == 'value':
                old, new = t.value, new_value(t.param)
                if new is None:
                    continue
            elif field == 'commencement':
                old = t.commence[1]
                new = t.inst.date + DAY * rng.choice((0, 3, 7, 14, 21, 30, 45, 60, 90, 120, 150, 180))
                limits = [x for x in [t.sunset] + [s.date for s in t.later] if x is not None]
                if new == old or (limits and new >= min(limits)):
                    continue
            elif field == 'sunset':
                old = t.sunset
                base = t.commence[1] if t.commence[0] == 'date' else t.inst.date + DAY * 400
                new = base + DAY * rng.randint(30, 420)
                if new == old:
                    continue
            else:
                old = t.ends
                new = t.commence[1] + DAY * rng.randint(10, 170)
                if new == old:
                    continue
            corr = O(type='correct', target=t, field=field, old=old, new=new, inst=insts_slip)
            t.corrections.append(corr)
            corrections.append(corr)
            insts_slip.items.append(corr)
    while len(insts) < rng.randint(26 * T, 32 * T):
        new_inst(rng.choice(('certificate', 'notice', 'notice')), e0 + DAY * rng.randint(30, 1450 * T))
    for _ in range(rng.randint(10 * T, 16 * T)):                       # proposals that were not accepted
        p = choose()
        inst = rng.choice([i for i in insts if i.kind in ('deed', 'letter', 'notice')])
        inst.items.append(O(type='decoy', param=p, value=rng.choice(POOLS[p.kind]), inst=inst))
    for ev in events:
        if ev.record is None and rng.random() < 0.6:
            inst = rng.choice([i for i in deeds if i.date > min(x.inst.date for x in provs if x.commence == ('event', ev))])
            inst.items.append(O(type='decoy_event', event=ev, inst=inst))
    insts.sort(key=lambda i: i.date)
    counters = {}
    for inst in insts:
        counters[inst.kind] = counters.get(inst.kind, 0) + 1
        inst.number = counters[inst.kind]
        inst.title = '%s (Number %d)' % (KIND_TITLE[inst.kind], inst.number)
    P.insts, P.provs, P.events, P.renumbers, P.corrections = insts, provs, events, renumbers, corrections
    assign_references(rng, P)
    place(rng, P)
    return P


def assign_references(rng, P):
    shorts = ['%s %s' % (a, n) for a in SHORT_ADJ for n in SHORT_NOUN]
    rng.shuffle(shorts)
    terms = {p.term for p in P.params.values()}
    shorts = [s for s in shorts if s not in terms]
    weights = (('term', 16), ('short', 40), ('description', 16), ('heading', 8), ('number', 20))
    defined = {}                      # parameter key -> the definitions of expressions for it, in order

    def draw(options):
        total = sum(w for _, w in options)
        pick = rng.randrange(total)
        for style, w in options:
            if pick < w:
                return style
            pick -= w

    for inst in P.insts:
        out = []
        for item in inst.items:
            if item.type in ('value', 'decoy'):
                options = weights if item.type == 'value' else [w for w in weights if w[0] not in ('short', 'number')]
                item.style = draw(options)
                if item.style == 'short':
                    made = defined.setdefault(item.param.key, [])
                    if not made:
                        first = O(type='define', short=shorts.pop(), param=item.param, inst=inst, base=None,
                                  style=draw([w for w in weights if w[0] != 'short']))
                        if first.style == 'number':
                            first.cited = number_at(item.param.number, inst.date, P.renumbers)
                        out.append(first)
                        made.append(first)
                    elif rng.random() < 0.4:        # an expression defined by another expression
                        chained = O(type='define', short=shorts.pop(), param=item.param, inst=inst, style='short',
                                    base=rng.choice(made))
                        out.append(chained)
                        made.append(chained)
                    item.define = made[-1] if rng.random() < 0.6 else rng.choice(made)
                if item.style == 'number':
                    item.cited = number_at(item.param.number, inst.date, P.renumbers)
            out.append(item)
        inst.items = out
    # Make sure that clause numbers are cited both before and after a renumbering.
    values = [it for inst in P.insts for it in inst.items if it.type == 'value']
    for after, want in ((False, 3), (True, 3)):
        def counts(it):
            return it.style == 'number' and (it.cited != it.param.number) == after
        have = sum(1 for it in values if counts(it))
        options = [it for it in values if it.style != 'number'
                   and (number_at(it.param.number, it.inst.date, P.renumbers) != it.param.number) == after]
        rng.shuffle(options)
        for it in options[:max(0, want - have)]:
            it.style, it.cited = 'number', number_at(it.param.number, it.inst.date, P.renumbers)
            it.define = None


def place(rng, P):
    sizes = {'deed': (24, 36), 'letter': (18, 28), 'notice': (14, 22), 'certificate': (12, 20), 'slip': (10, 16)}
    for inst in P.insts:
        low, high = sizes[inst.kind]
        inst.n = max(rng.randint(low, high), len(inst.items) + 6)
        spots = sorted(rng.sample(range(inst.n), len(inst.items)))
        for spot, item in zip(spots, inst.items):
            item.par = spot + 1
        inst.spots = {item.par: item for item in inst.items}


def facts(P):
    """The list of facts of a plan, with stable ids, and the id of every object that is a fact."""
    out, n = [], [0]

    def new(obj):
        n[0] += 1
        obj.id = 'E%03d' % n[0]
        return obj.id

    for c in P.clauses:
        if c.param is not None:
            new(c.param)
    for c in P.clauses:
        if c.param is not None:
            c.param.defn = O(id='')
            new(c.param.defn)
    order = []
    for inst in P.insts:
        for item in sorted(inst.items, key=lambda x: x.par):
            if item.type == 'record':
                item.event.id = new(item)
                item.id = item.event.id
            elif item.type in ('value', 'revoke', 'sunset', 'correct', 'renumber', 'define', 'decoy', 'decoy_event'):
                new(item)
            order.append(item)
    for ev in P.events:
        if ev.record is None:
            new(ev)
    P.fact_order = order

    def ref_requires(style, param, day, definition=None):
        if style == 'term':
            return [param.defn.id]
        if style == 'short':
            return [definition.id]
        need = [param.id]
        if style == 'number':
            need += [r.id for r in sorted(P.renumbers, key=lambda r: r.commence) if r.commence <= day]
        return need

    def commence_of(c):
        return {'type': 'event', 'event': c[1].id} if c[0] == 'event' else {'type': 'date', 'date': c[1].isoformat()}

    def where(item):
        return {'instrument': item.inst.title, 'date': item.inst.date.isoformat(), 'paragraph': item.par}

    events = []
    for c in P.clauses:
        p = c.param
        if p is not None:
            events.append({'id': p.id, 'kind': 'parameter', 'parameter': p.key, 'term': p.term, 'heading': p.heading,
                           'clause': p.number, 'value_kind': p.kind, 'value': p.orig, 'from': P.e0.isoformat(),
                           'party': getattr(p, 'party', None)})
    for c in P.clauses:
        p = c.param
        if p is not None:
            events.append({'id': p.defn.id, 'kind': 'definition', 'source': 'agreement', 'term': p.term,
                           'parameter': p.key, 'date': P.e0.isoformat(), 'paragraph': 0, 'requires': [p.id]})
    for item in P.fact_order:
        t = item.type
        if t == 'value':
            day = item.inst.date
            ref = {'style': item.style, 'definition': item.define.id if item.style == 'short' else (
                item.param.defn.id if item.style == 'term' else None),
                'number': item.cited if item.style == 'number' else None}
            need = ref_requires(item.style, item.param, day, getattr(item, 'define', None))
            link = None
            if item.link is not None:
                link = {'parameter': item.link.key, 'style': item.other_style}
                need = need + ref_requires(item.other_style, item.link, day)
            if item.commence[0] == 'event':
                need = need + [item.commence[1].id]
            events.append(dict(where(item), id=item.id, kind='amendment', parameter=item.param.key,
                               operation=item.op, category=item.cat, value=item.value,
                               commencement=commence_of(item.commence), link=link,
                               sunset=item.sunset.isoformat() if item.sunset else None,
                               ends=item.ends.isoformat() if item.ends else None, reference=ref, requires=need))
        elif t == 'define':
            if item.style == 'number':
                ref = {'style': 'number', 'definition': None, 'number': item.cited}
            else:
                ref = {'style': item.style, 'definition': item.param.defn.id if item.style == 'term' else (
                    item.base.id if item.style == 'short' else None), 'number': None}
            events.append(dict(where(item), id=item.id, kind='definition', source='instrument', term=item.short,
                               parameter=item.param.key, reference=ref,
                               requires=ref_requires(item.style, item.param, item.inst.date, item.base)))
        elif t == 'record':
            events.append(dict(where(item), id=item.id, kind='event', phrase=item.event.phrase,
                               recorded_by=item.inst.title, requires=[]))
        elif t == 'revoke':
            need = [item.target.id] + ([item.commence[1].id] if item.commence[0] == 'event' else [])
            events.append(dict(where(item), id=item.id, kind='revocation', target=item.target.id,
                               parameter=item.target.param.key,
                               commencement=commence_of(item.commence), requires=need))
        elif t == 'sunset':
            events.append(dict(where(item), id=item.id, kind='sunset', target=item.target.id,
                               parameter=item.target.param.key,
                               sunset=item.date.isoformat(), requires=[item.target.id]))
        elif t == 'correct':
            conv = (lambda v: v.isoformat()) if item.field != 'value' else (lambda v: v)
            events.append(dict(where(item), id=item.id, kind='correction', target=item.target.id,
                               parameter=item.target.param.key, field=item.field,
                               old=conv(item.old), new=conv(item.new), requires=[item.target.id]))
        elif t in ('decoy', 'decoy_event'):
            # a proposal, request or hope: it decides nothing (procedure.md, rule 1) but it is a fact of the file
            events.append(dict(where(item), id=item.id, kind='decoy', decides=False, requires=[],
                               parameter=item.param.key if t == 'decoy' else None,
                               value=item.value if t == 'decoy' else None,
                               phrase=item.event.phrase if t == 'decoy_event' else None))
        elif t == 'renumber':
            events.append(dict(where(item), id=item.id, kind='renumbering', first=item.from_n, last=item.last,
                               shift=item.k, effective=item.commence.isoformat(), requires=[]))
    for ev in P.events:
        if ev.record is None:
            events.append({'id': ev.id, 'kind': 'event', 'phrase': ev.phrase, 'recorded_by': None, 'date': None,
                           'paragraph': None, 'requires': []})
    for e in events:
        e.setdefault('decides', True)
    return events


# ---------------------------------------------------------------------------------------------------------
# Values, dates and statements
# ---------------------------------------------------------------------------------------------------------

def words(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ('-' + ONES[n % 10] if n % 10 else '')
    if n < 1000:
        rest = n % 100
        return ONES[n // 100] + ' hundred' + (' and ' + words(rest) if rest else '')
    for size, name in ((1000000, ' million'), (1000, ' thousand')):
        if n >= size:
            rest = n % size
            tail = ''
            if rest:
                tail = (' and ' if rest < 100 else ' ') + words(rest)
            return words(n // size) + name + tail
    raise ValueError(n)


def number_text(value):
    """Digits of a value: thousands separators from 1,000, a decimal part only for halves."""
    if isinstance(value, float) and value != int(value):
        return repr(value)
    return '{:,}'.format(int(value))


def fmt_value(rng, param, value):
    if value == NO_VALUE:
        raise ValueError('no value')
    pattern = rng.choice(VALUE_FORMATS[param.kind])
    if param.kind == 'officer':
        return pattern.format(party=param.party, title=value)
    whole = float(value) == int(value)
    if '{w}' in pattern and not whole:
        pattern = VALUE_FORMATS[param.kind][0] if param.kind != 'pct' else '{n} per cent'
    n = int(value) if whole and param.kind != 'pct' else value
    if param.kind == 'pct' and whole:
        n = int(value)
    return pattern.format(n=number_text(n), w=words(int(value)) if whole else '', unit=getattr(param, 'unit', ''))


def ordinal(n):
    suffix = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return '%d%s' % (n, suffix)


def fmt_date(rng, day, pattern=None):
    pattern = pattern or rng.choice(DATE_FORMATS)
    return pattern.format(y=day.year, m02='%02d' % day.month, d02='%02d' % day.day, d=day.day,
                          dord=ordinal(day.day), mname=MONTHS[day.month - 1], mabbr=MONTH_ABBR[day.month - 1])


def cap(text):
    return text[0].upper() + text[1:]


STATE_CITES = [('procedure.md', 'P-00'), ('agreement.md', 'A-00'), ('instruments.md', 'I-00')]


def render_statement(rng, params, q, stated):
    p = params[q['parameter']] if q.get('parameter') else None
    kind, cat = q['kind'], q.get('category')
    if kind == 'value':
        if stated == NO_VALUE:
            pattern = rng.choice(STATEMENTS['none_cat' if cat else 'none'])
            return pattern.format(date=fmt_date(rng, _d(q['date'])), term=p.term, cat=cat)
        pattern = rng.choice(STATEMENTS['value_cat' if cat else 'value'])
        return pattern.format(date=fmt_date(rng, _d(q['date'])), term=p.term, cat=cat, value=fmt_value(rng, p, stated))
    if kind == 'count':
        return rng.choice(STATEMENTS['count']).format(date=fmt_date(rng, _d(q['date'])), term=p.term, n=stated)
    if kind == 'total':
        return rng.choice(STATEMENTS['total']).format(date=fmt_date(rng, _d(q['date'])), n=stated)
    if kind == 'between':
        return rng.choice(STATEMENTS['between']).format(date=fmt_date(rng, _d(q['date'])),
                                                        date2=fmt_date(rng, _d(q['date2'])), n=stated)
    if kind == 'class':
        pattern = rng.choice(STATEMENTS['class_dis' if q['class'] == 'disapplication' else 'class_exc'])
        return pattern.format(date=fmt_date(rng, _d(q['date'])), n=stated)
    if kind in ('on', 'params', 'insts'):
        return rng.choice(STATEMENTS[kind]).format(date=fmt_date(rng, _d(q['date'])), n=stated)
    if kind == 'never':
        return rng.choice(STATEMENTS['never']).format(n=stated)
    if kind == 'first':
        return rng.choice(STATEMENTS['first']).format(date=fmt_date(rng, _d(stated)), term=p.term,
                                                      value=fmt_value(rng, p, q['value']))
    pattern = rng.choice(STATEMENTS['differs' if stated == 'different' else 'same'])
    return pattern.format(date=fmt_date(rng, _d(q['date'])), date2=fmt_date(rng, _d(q['date2'])), term=p.term,
                          catpart=' for ' + cat if cat else '')


def make_claim(rng, params, q, stated, error=None):
    """A claim for query `q` (without its 'stated') asserting `stated`; `error` names what a wrong one stands for."""
    q = dict(q, stated=stated)
    text = render_statement(rng, params, q, stated)
    expected = stated

    def pred(world, q=q, expected=expected):
        return answer_from(world['events'], q, world['reader']) == expected

    def quantity(world, q=q):
        return answer_from(world['events'], q, world['reader'])

    item = claim('amendment_' + q['kind'], text, pred, STATE_CITES, quantity=quantity, stated=stated, error=error)
    item['query'] = q
    return item


# ---------------------------------------------------------------------------------------------------------
# Choosing the 24 statements
# ---------------------------------------------------------------------------------------------------------

def local_events(events, key):
    """The facts that can bear on one parameter: its own facts, what acts on its amendments, what they require, and
    the facts of any parameter whose value one of its amendments takes over."""
    by_id = {f['id']: f for f in events}
    keys, own = {key}, set()
    while True:
        before = len(own)
        own |= {f['id'] for f in events if f.get('parameter') in keys}
        own |= {f['id'] for f in events if f.get('target') in own}
        todo = list(own)
        while todo:
            for need in by_id[todo.pop()].get('requires', ()):
                if need in by_id and need not in own:
                    own.add(need)
                    todo.append(need)
        keys |= {by_id[i]['link']['parameter'] for i in own if by_id[i].get('link')}
        if len(own) == before:
            break
    return [f for f in events if f['id'] in own]


def whole_file_events(events):
    """The facts that a count over the whole file can rest on: every amending provision, what acts on one (a
    revocation, a correction, a later sunset), and everything these refer to (clauses, definitions, renumberings and
    events)."""
    by_id = {f['id']: f for f in events}
    own = {f['id'] for f in events if f['kind'] == 'amendment'}
    while True:
        before = len(own)
        own |= {f['id'] for f in events if f.get('target') in own}
        todo = list(own)
        while todo:
            for need in by_id[todo.pop()].get('requires', ()):
                if need in by_id and need not in own:
                    own.add(need)
                    todo.append(need)
        if len(own) == before:
            return [f for f in events if f['id'] in own]


def strict_set(events, query):
    """The facts, other than parameters and agreement definitions, on which the true answer rests: those whose
    removal changes the answer, with the facts they act on or require."""
    if query['kind'] in WHOLE_FILE:
        return set(range(99))                      # rests on every amending provision of the file
    key = query['parameter']
    events = local_events(events, key)
    base = answer_from(events, query)
    index = _index(events)
    near = {f['id'] for f in index.amend.get(key, ())}
    for table in (index.revokes, index.corrections, index.sunsets):
        for fid in list(near):
            near.update(x['id'] for x in table.get(fid, ()))
    for f in events:
        if f['kind'] in ('renumbering', 'event') or (f['kind'] == 'definition' and f.get('source') == 'instrument'
                                                      and f['parameter'] == key):
            near.add(f['id'])
    needed = set()
    for fid in sorted(near):
        gone = {fid}                                   # the passage and whatever rests on it are taken away
        for f in events:
            if any(need in gone for need in f.get('requires', ())):
                gone.add(f['id'])
        rest = [f for f in events if f['id'] not in gone]
        if answer_from(rest, query) != base:
            needed.add(fid)
    by_id = index.by_id
    todo = list(needed)
    while todo:
        f = by_id[todo.pop()]
        links = list(f.get('requires', ())) + ([f['target']] if 'target' in f else [])
        for other in links:
            g = by_id.get(other)
            if g is not None and g['kind'] != 'parameter' and g.get('source') != 'agreement' and other not in needed:
                needed.add(other)
                todo.append(other)
    return {fid for fid in needed if by_id[fid]['kind'] != 'parameter' and by_id[fid].get('source') != 'agreement'}


def interesting_days(events, key):
    index = _index(events)
    days = set()
    for f in index.amend.get(key, ()):
        found = [_d(f['date']), index.commence(f, None), index.commence(f, 'applies_everything'),
                 index.sunset(f, None), _d(f['commencement']['date']) if f['commencement']['type'] == 'date' else None]
        found += [index.revocation_start(r) for r in index.revokes.get(f['id'], ())]
        found += [_d(x['sunset']) for x in index.sunsets.get(f['id'], ())]
        for x in index.corrections.get(f['id'], ()):
            if x['field'] != 'value':
                found.append(_d(x['new']))
                found.append(_d(x['old']))
        for day in found:
            if day is not None:
                days.update((day - DAY, day, day + DAY))
    start = _d(index.params[key]['from'])
    return sorted(d for d in days if d >= start)


class Cand:
    def __init__(self, kind, query, truth, readers, key):
        self.kind, self.query, self.truth, self.readers, self.key = kind, query, truth, readers, key
        self.wrong = frozenset(r for r, v in readers.items() if v != truth)
        self.strict = None
        self.cross = None
        self.sens = 0                   # in how many ways a count is one out when a date is read the other way (sizes 2 and up)
        self.alias = (0, 0)             # (amending provisions in the deciding set named by a defined short form, all of them)
        self.hidden = False             # a passage that decides it holds no word of the subject's name or of its aliases


def candidates(rng, events, params, size=1):
    index = _index(events)
    kinds = {k: v['value_kind'] for k, v in index.params.items()}
    out = []
    amended = [k for k in params if index.amend.get(k)]
    for key in amended:
        days = interesting_days(events, key)
        if not days:
            continue
        start = _d(index.params[key]['from'])
        cats = [None] + sorted({f['category'] for f in index.amend[key] if f['category']})
        sample = rng.sample(days, min(len(days), 9)) + [start + DAY * rng.randint(1, 20)]
        for cat in cats:
            for day in sample:
                q = {'kind': 'value', 'parameter': key, 'date': day.isoformat(), 'category': cat, 'value_kind': kinds[key]}
                vals = {r: answer_from(events, q, r) for r in READERS}
                out.append(Cand('value', q, answer_from(events, q), vals, key))
        for day in sample[:6]:
            q = {'kind': 'count', 'parameter': key, 'date': day.isoformat(), 'value_kind': kinds[key]}
            out.append(Cand('count', q, answer_from(events, q), {r: answer_from(events, q, r) for r in READERS}, key))
        general = [f['value'] for f in index.amend[key] if f['category'] is None and f['operation'] != 'disapply'
                   and f['value'] is not None]
        for value in sorted(set(general), key=str) + ([] if index.params[key]['value'] is None
                                                      else [index.params[key]['value']]):
            q = {'kind': 'first', 'parameter': key, 'value': value, 'value_kind': kinds[key]}
            truth = answer_from(events, q)
            if truth not in (None, NONE_VALUE):
                out.append(Cand('first', q, truth, {r: answer_from(events, q, r) for r in READERS}, key))
        for _ in range(6):
            cat = rng.choice(cats)
            d1, d2 = sorted(rng.sample(days + [start], 2))
            q = {'kind': 'differs', 'parameter': key, 'date': d1.isoformat(), 'date2': d2.isoformat(), 'category': cat,
                 'value_kind': kinds[key]}
            out.append(Cand('differs', q, answer_from(events, q), {r: answer_from(events, q, r) for r in READERS}, key))
    late = sorted({_d(f['date']) for f in events if f['kind'] == 'amendment'})
    if size == 1:
        for day in rng.sample(late[len(late) // 2:], min(8, len(late) - len(late) // 2)):
            for shift in (0, 40, 130):
                q = {'kind': 'total', 'date': (day + DAY * shift).isoformat(), 'value_kind': 'count'}
                truth = answer_from(events, q)
                if truth is not None and truth >= 4:
                    out.append(Cand('total', q, truth, {r: answer_from(events, q, r) for r in READERS}, ('total', q['date'])))
    # statements on parameters nobody amended, and on early dates: every reader is right
    for key in rng.sample([k for k in params if k not in amended], 8 if size == 1 else min(8, len(params) - len(amended))):
        day = _d(index.params[key]['from']) + DAY * rng.randint(30, 1200)
        q = {'kind': 'value', 'parameter': key, 'date': day.isoformat(), 'category': None, 'value_kind': kinds[key]}
        out.append(Cand('value', q, answer_from(events, q), {r: answer_from(events, q, r) for r in READERS}, key))
        q = {'kind': 'count', 'parameter': key, 'date': day.isoformat(), 'value_kind': kinds[key]}
        out.append(Cand('count', q, answer_from(events, q), {r: answer_from(events, q, r) for r in READERS}, key))
    if size > 1:
        out += whole_candidates(rng, events)
    return out


# The smallest true count that a whole-file statement may have: a count of nothing or of one has no enumeration.
MIN_TRUTH = {'total': 4, 'between': 2, 'class': 2, 'on': 3, 'params': 3, 'insts': 3, 'never': 2}


def whole_candidates(rng, events):
    """Candidates for the statements about the whole file (sizes 2 and up), every shape, on dates at and around the
    dates on which provisions took effect, so that a count that is one out (a provision missed or counted twice, an
    end date read the other way) is plausible."""
    index, rows = census(events, None, datetime.date.max)
    starts = sorted({began for _, began in rows if began is not None})
    anchors = starts[len(starts) // 5:]
    out = []

    def add(query, sens_query=()):
        truth = answer_from(events, query)
        if truth is None or truth < MIN_TRUTH[query['kind']]:
            return
        key = (query['kind'], query.get('class'), query.get('date'), query.get('date2'))
        c = Cand(query['kind'], query, truth, {r: answer_from(events, query, r) for r in READERS}, key)
        c.sens = sum(1 for other in sens_query if answer_from(events, other) != truth)
        out.append(c)

    def day():
        return rng.choice(anchors) + DAY * rng.choice((0, 0, 0, -1, 1, 20, 75))

    def before(kind, **extra):
        d = day()
        q = dict({'kind': kind, 'date': d.isoformat(), 'value_kind': 'count'}, **extra)
        add(q, [dict(q, date=(d - DAY).isoformat()), dict(q, date=(d + DAY).isoformat())])

    for _ in range(10):
        before('total')
    for _ in range(10):
        before('params')
    for _ in range(10):
        before('insts')
    for variant in ('disapplication', 'exception'):
        for _ in range(8):
            before('class', **{'class': variant})
    for _ in range(10):
        d = day()
        q = {'kind': 'on', 'date': d.isoformat(), 'value_kind': 'count'}
        add(q, [dict(q, date=(d - DAY).isoformat()), dict(q, date=(d + DAY).isoformat())])
    for _ in range(12):
        d1 = day()
        d2 = d1 + DAY * rng.choice((35, 60, 90, 150, 240, 400))
        if rng.random() < 0.5:
            d2 = rng.choice([x for x in anchors if x > d1 + DAY * 2] or [d2]) - DAY * rng.choice((0, 0, 1))
        q = {'kind': 'between', 'date': d1.isoformat(), 'date2': d2.isoformat(), 'value_kind': 'count'}
        add(q, [dict(q, date=(d1 + DAY).isoformat()), dict(q, date=(d1 - DAY).isoformat()),
                dict(q, date2=(d2 - DAY).isoformat()), dict(q, date2=(d2 + DAY).isoformat())])
    add({'kind': 'never', 'value_kind': 'count'})
    return out


QUOTA = {'value': 13, 'count': 2, 'total': 3, 'first': 2, 'differs': 4}
CONTROLS = {'value': 3, 'count': 1, 'total': 0, 'first': 1, 'differs': 1}
MIN_WRONG = 9
MIN_STRICT = 14
MIN_CROSS = 4


def depends_on_other_subject(events, cand):
    """True when the answer rests on an amendment that takes over the value of another parameter, and so on a fact
    that belongs to that parameter."""
    key = cand.query.get('parameter')
    if key is None:
        return False
    by_id = {f['id']: f for f in events}
    need = needed(cand.query, events)
    return any(by_id[i].get('link') for i in need) and any(by_id[i].get('parameter') not in (None, key) for i in need)


def decided(events, cand):
    """A statement needs a deciding fact: the set of facts whose removal changes its answer is not empty, and where
    the answer is not what the agreement alone gives, one of them is an instrument's fact (no two equal values leave
    the answer without a single deciding fact)."""
    need = needed(cand.query, events)
    if not need:
        return False
    if cand.truth == answer_from(events, cand.query, 'reads_agreement_only'):
        return True
    by_id = {f['id']: f for f in events}
    return any(by_id[i]['kind'] not in ('parameter',) and by_id[i].get('source') != 'agreement' for i in need)


def select(rng, events, cands):
    controls = [c for c in cands if not c.wrong]
    others = [c for c in cands if c.wrong]
    for _ in range(40):
        chosen, per_key, spots = [], {}, set()

        def ok(c):
            spot = (c.key, c.query.get('date'), c.query.get('value'), c.query['kind'])
            return per_key.get(c.key, 0) < 2 and spot not in spots

        def take(c):
            chosen.append(c)
            per_key[c.key] = per_key.get(c.key, 0) + 1
            spots.add((c.key, c.query.get('date'), c.query.get('value'), c.query['kind']))

        for kind, n in CONTROLS.items():
            pool = [c for c in controls if c.kind == kind and ok(c)]
            rng.shuffle(pool)
            for c in pool[:n]:
                if ok(c):
                    take(c)
        covered = {r: 0 for r in READERS}
        for kind, total in QUOTA.items():
            need = total - sum(1 for c in chosen if c.kind == kind)
            pool = [c for c in others if c.kind == kind]
            rng.shuffle(pool)
            truths = ['different', 'different', 'same'] if kind == 'differs' else [None] * need   # as many true "same" as "differed"
            for want in truths[:need]:
                options = [c for c in pool if ok(c) and (want is None or c.truth == want)]
                if not options:
                    break
                sample = rng.sample(options, min(12, len(options)))
                for c in sample:
                    if c.strict is None:
                        c.strict = len(strict_set(events, c.query))
                    if c.cross is None:
                        c.cross = depends_on_other_subject(events, c)
                wanting = sum(1 for c in chosen if c.cross) < MIN_CROSS
                lacking = sum(1 for c in chosen if c.query['kind'] == 'value' and c.truth == NO_VALUE) < 3
                best = max(sample, key=lambda c: (sum(1 for r in c.wrong if covered[r] < MIN_WRONG), c.cross and wanting, c.strict >= 2,
                                                  c.truth == NO_VALUE and lacking, rng.random()))
                take(best)
                for r in best.wrong:
                    covered[r] += 1
        if len(chosen) != 24 or sum(1 for c in chosen if not c.wrong) < sum(CONTROLS.values()) or any(sum(1 for c in chosen if r in c.wrong) < MIN_WRONG for r in READERS):
            continue
        for c in chosen:
            if c.strict is None:
                c.strict = len(strict_set(events, c.query))
        if sum(1 for c in chosen if c.strict >= 2) < MIN_STRICT:
            continue
        if sum(1 for c in chosen if c.query['kind'] == 'value' and c.query['category']) < 2:
            continue
        if not 3 <= sum(1 for c in chosen if c.query['kind'] == 'value' and c.truth == NO_VALUE) <= 6:
            continue
        for c in chosen:
            if c.cross is None:
                c.cross = depends_on_other_subject(events, c)
        if sum(1 for c in chosen if c.cross) < MIN_CROSS or any(not decided(events, c) for c in chosen):
            continue
        return chosen
    return None


# From size 2 the 24 statements are 9 about the whole file (at least 5 of the 7 shapes) and 15 of the older kinds.
QUOTA_BIG = {'value': 9, 'count': 2, 'first': 2, 'differs': 2}
CONTROLS_BIG = {'value': 3, 'count': 1, 'first': 1, 'differs': 1}
WHOLE_SLOTS = 9
MIN_SHAPES = 5


def decided_big(events, cand, memo):
    """`decided`, and: the facts whose removal changes the answer are a complete argument (`answer` on them alone is the
    true value), except for a count of one parameter's instruments, of parameters or of instruments; and for a
    statement that counts provisions, every provision it counts is needed (removing it changes the count)."""
    spot = json.dumps({k: v for k, v in cand.query.items() if k != 'stated'}, sort_keys=True)
    if spot not in memo:
        need = needed(cand.query, events)
        ok = bool(need)
        if ok and cand.kind not in ('count', 'params', 'insts'):
            # the needed facts alone are a complete argument; a count that one of several provisions of one parameter,
            # instrument or file supports is the exception, since each of them would do and so none is needed
            ok = answer(cand.query, [f for f in events if f['id'] in need]) == cand.truth
        if ok and cand.kind in WHOLE_FILE:
            ok = counted(events, cand.query) <= set(need)
        elif ok and cand.truth != answer_from(events, cand.query, 'reads_agreement_only'):
            by_id = {f['id']: f for f in events}
            ok = any(by_id[i]['kind'] != 'parameter' and by_id[i].get('source') != 'agreement' for i in need)
        memo[spot] = ok
    return memo[spot]


MIN_ALIAS_SHARE = 0.5


def hides_a_passage(events, ids):
    """True when one of the facts `ids` is told in a passage that names no subject: a renumbering, a revocation, a
    correction, a later sunset, an instrument recording an event, or a provision that cites its clause by number or
    takes over the value of another parameter."""
    by_id = {f['id']: f for f in events}
    for i in ids:
        f = by_id.get(i)
        if f is None:
            continue
        if f['kind'] in ('renumbering', 'revocation', 'correction', 'sunset', 'event'):
            return True
        if f['kind'] == 'amendment' and (f['reference']['style'] == 'number' or f.get('link')):
            return True
    return False


MIN_HIDDEN_SHARE = 0.7


def alias_counts(events, ids):
    """How many of the amending provisions among the facts `ids` name their parameter only through an expression
    that an instrument defines, and how many there are."""
    by_id = {f['id']: f for f in events}
    provs = [by_id[i] for i in ids if i in by_id and by_id[i]['kind'] == 'amendment']
    return sum(1 for f in provs if f['reference']['style'] == 'short'), len(provs)


def select_big(rng, events, cands):
    """The 24 statements of a dossier of size 2 and up: the 15 of the older kinds chosen as `select` chooses them, and
    9 about the whole file, in at least MIN_SHAPES of the seven shapes."""
    controls = [c for c in cands if not c.wrong and c.kind not in WHOLE_FILE]
    others = [c for c in cands if c.wrong and c.kind not in WHOLE_FILE]
    whole = [c for c in cands if c.kind in WHOLE_FILE and c.wrong]
    available = sorted({c.kind for c in whole})
    memo = {}
    if len(available) < MIN_SHAPES:
        return None
    for _ in range(40):
        chosen, per_key, spots = [], {}, set()

        def ok(c):
            spot = (c.key, c.query.get('date'), c.query.get('value'), c.query['kind'])
            return per_key.get(c.key, 0) < 2 and spot not in spots

        def take(c):
            chosen.append(c)
            per_key[c.key] = per_key.get(c.key, 0) + 1
            spots.add((c.key, c.query.get('date'), c.query.get('value'), c.query['kind']))

        for kind, n in CONTROLS_BIG.items():
            pool = [c for c in controls if c.kind == kind and ok(c)]
            rng.shuffle(pool)
            for c in pool[:n]:
                if ok(c):
                    take(c)
        covered = {r: 0 for r in READERS}
        for kind, total in QUOTA_BIG.items():
            need = total - sum(1 for c in chosen if c.kind == kind)
            pool = [c for c in others if c.kind == kind]
            rng.shuffle(pool)
            have = [c.truth for c in chosen if c.kind == 'differs']
            truths = [t for t in ('different', 'same') if t not in have][:need] if kind == 'differs' else [None] * need
            for want in truths:
                options = [c for c in pool if ok(c) and (want is None or c.truth == want)]
                if not options:
                    break
                sample = rng.sample(options, min(12, len(options)))
                for c in sample:
                    if c.strict is None:
                        ids = strict_set(events, c.query)
                        c.strict = len(ids)
                        c.alias = alias_counts(events, ids)
                        c.hidden = hides_a_passage(events, ids)
                    if c.cross is None:
                        c.cross = depends_on_other_subject(events, c)
                wanting = sum(1 for c in chosen if c.cross) < MIN_CROSS
                lacking = sum(1 for c in chosen if c.query['kind'] == 'value' and c.truth == NO_VALUE) < 2
                best = max(sample, key=lambda c: (sum(1 for r in c.wrong if covered[r] < MIN_WRONG), c.cross and wanting,
                                                  c.strict >= 2, c.alias[0] / max(1, c.alias[1]), c.hidden,
                                                  c.truth == NO_VALUE and lacking, rng.random()))
                take(best)
                for r in best.wrong:
                    covered[r] += 1
        shapes = rng.sample(available, rng.choice(range(MIN_SHAPES, len(available) + 1)))
        for shape in shapes + [rng.choice(shapes) for _ in range(WHOLE_SLOTS - len(shapes))]:
            def twice(c):               # a second statement of one shape uses the other variant of 'class'
                return sum(1 for x in chosen if x.kind == 'class' and c.kind == 'class' and x.query['class'] == c.query['class'])
            options = [c for c in whole if c.kind == shape and ok(c)]
            if not options:
                break
            sample = rng.sample(options, min(8, len(options)))
            best = max(sample, key=lambda c: (-twice(c), sum(1 for r in c.wrong if covered[r] < MIN_WRONG), c.sens,
                                              rng.random()))
            take(best)
            for r in best.wrong:
                covered[r] += 1
        if len(chosen) != 24 or sum(1 for c in chosen if not c.wrong) < sum(CONTROLS_BIG.values()):
            continue
        if any(sum(1 for c in chosen if r in c.wrong) < MIN_WRONG for r in READERS):
            continue
        if len({c.kind for c in chosen if c.kind in WHOLE_FILE}) < MIN_SHAPES:
            continue
        for c in chosen:
            if c.strict is None:
                ids = strict_set(events, c.query)
                c.strict = len(ids)
                c.alias = alias_counts(events, ids)
                c.hidden = hides_a_passage(events, ids)
        if sum(1 for c in chosen if c.strict >= 2) < MIN_STRICT:
            continue
        # a reader that keeps the paragraphs with any word of the subject's name still misses a passage that decides
        # most of the statements that are not controls
        subject = [c for c in chosen if c.kind not in WHOLE_FILE and c.wrong]
        if sum(1 for c in subject if c.hidden) < MIN_HIDDEN_SHARE * len(subject):
            continue
        # the alias share of the statements that rest on one parameter: as at size 1, most of what decides them is
        # named through an expression that an instrument defines
        named = sum(c.alias[0] for c in chosen if c.kind not in WHOLE_FILE)
        if named < MIN_ALIAS_SHARE * sum(c.alias[1] for c in chosen if c.kind not in WHOLE_FILE):
            continue
        if sum(1 for c in chosen if c.query['kind'] == 'value' and c.query['category']) < 2:
            continue
        if not 2 <= sum(1 for c in chosen if c.query['kind'] == 'value' and c.truth == NO_VALUE) <= 5:
            continue
        for c in chosen:
            if c.cross is None:
                c.cross = depends_on_other_subject(events, c)
        if sum(1 for c in chosen if c.cross) < MIN_CROSS or any(not decided_big(events, c, memo) for c in chosen):
            continue
        return chosen
    return None


def slot_for(rng, params, c, size=1):
    q = {k: v for k, v in c.query.items() if k != 'stated'}
    kind = c.kind
    wrong = {}
    for r, v in c.readers.items():
        if v != c.truth and v is not None:
            wrong.setdefault(v if not isinstance(v, bool) else v, []).append(r)
    p = params.get(c.key)
    if kind == 'differs':
        true = make_claim(rng, params, q, c.truth)
        other = 'same' if c.truth == 'different' else 'different'
        return true, [make_claim(rng, params, q, other, '+'.join(wrong.get(other, [])) or 'wrong_value')]
    if kind == 'first':
        true = make_claim(rng, params, q, c.truth)
        options = {v: names for v, names in wrong.items() if v != NONE_VALUE}
        extra = _d(c.truth) + DAY * rng.choice((-30, -7, -1, 1, 7, 30, 90))
        options.setdefault(extra.isoformat(), [])
        mutations = [make_claim(rng, params, q, v, '+'.join(names) or 'wrong_value') for v, names in sorted(options.items())]
        return true, mutations
    true = make_claim(rng, params, q, c.truth)
    if size > 1 and kind in WHOLE_FILE:
        # A wrong count: in about half the cases the true one out by one (a provision missed or counted twice),
        # otherwise the count of a named flawed reader. `choose` picks one of the two at random.
        step = rng.choice([v for v in (1, -1) if c.truth + v >= 1])
        options = {c.truth + step: wrong.get(c.truth + step, [])}
        others = sorted(v for v in wrong if v not in options and v >= 1)
        second = rng.choice(others) if others else c.truth - step if c.truth - step >= 1 else c.truth + 2
        options[second] = wrong.get(second, [])
        return true, [make_claim(rng, params, q, v, '+'.join(names) or 'wrong_value') for v, names in options.items()]
    options = dict(wrong)
    if kind in ('count', 'total'):
        for v in (c.truth + 1, c.truth - 1):
            if v >= (0 if kind == 'count' else 3):
                options.setdefault(v, [])
    else:
        if c.truth != NO_VALUE:
            options.setdefault(NO_VALUE, [])
        pool = [v for v in POOLS[p.kind] if v != c.truth and v not in options]
        for v in rng.sample(pool, 1 if len(options) >= 2 else 2):
            options.setdefault(v, [])
    mutations = [make_claim(rng, params, q, v, '+'.join(names) or 'wrong_value')
                 for v, names in sorted(options.items(), key=lambda kv: str(kv[0]))]
    return true, mutations


# ---------------------------------------------------------------------------------------------------------
# Prose. Everything that is not a deciding fact: clauses, definitions, recitals and the ordinary business of
# the instruments. No sentence below names a defined term of a parameter.
# ---------------------------------------------------------------------------------------------------------

CUSTOMERS = ('Meridian Holdings Limited', 'Northvale Services Group Limited', 'Halcyon Public Utilities Limited',
             'Brightwater Estates Limited', 'Carrick and Dunmore Limited')
SUPPLIERS = ('Corvane Systems Limited', 'Altair Managed Services Limited', 'Pellucid Technology Limited',
             'Strathmore Infrastructure Limited', 'Kestrel Operations Limited')
FILL = {
    'party': ('the Supplier', 'the Customer', 'each party', 'the Supplier and the Customer together'),
    'shall': ('shall', 'must', 'is to', 'will'),
    'action': ('keep complete and accurate records of', 'give the other party reasonable access to',
               'tell the other party promptly about any matter affecting', 'cooperate in good faith over',
               'maintain suitable arrangements for', 'review at regular intervals', 'document in writing',
               'take reasonable steps to limit the effect on', 'plan and schedule', 'report to the Governance Board on',
               'safeguard', 'make available the information needed for', 'coordinate its work on',
               'give proper priority to'),
    'neg': ('change', 'disclose to any third party', 'suspend', 'relocate', 'delegate responsibility for',
            'dispose of', 'alter the arrangements for', 'withdraw resources from'),
    'object': ('the Services provided at each Customer Site', 'the Deliverables under each Statement of Work',
               'the Supplier Personnel', 'the Customer Data', 'the equipment used in the Services',
               'the facilities made available by the Customer', 'the reports produced under this Agreement',
               'the Charges invoiced for the Services', 'the working arrangements between the parties',
               'the service desk and its records', 'the schedules agreed under each Statement of Work',
               'the materials supplied to the Supplier Personnel', 'the systems on which the Services run',
               'the Policies that apply to the Services'),
    'qual': ('in accordance with Good Industry Practice', 'so far as is reasonably practicable', 'at its own cost',
             'without unreasonable delay', 'subject to any contrary written direction from the Customer',
             'consistently with the Policies', 'using appropriately skilled staff', 'in a manner that the other party can verify'),
    'cond': ('Where the Customer so requests in writing', 'If the other party has given reasonable notice',
             'Unless the Governance Board agrees otherwise', 'To the extent that the law allows',
             'Where a Statement of Work so provides', 'In the event of a disagreement over scope',
             'Where the circumstances reasonably call for it', 'Once the relevant Statement of Work has been signed'),
    'recordq': ('complete and accurate', 'kept in a durable form', 'dated and, where practicable, signed',
                'retained for the length of the relevant Statement of Work'),
    'person': ('Anna Haldane', 'Marcus Okafor', 'Priya Lindqvist', 'Tomas Moreau', 'Helena Castellan', 'Kofi Brandt',
               'Ingrid Vasquez', 'Dmitri Ibsen', 'Leila Novak', 'Oskar Fairweather', 'Mira Adeyemi', 'Rafael Rinaldi',
               'Sana Kessler', 'Viktor Marlowe', 'Elena Thornton', 'Joris Duarte'),
    'site': ('Leeds', 'Rotterdam', 'Antwerp', 'Lyon', 'Turin', 'Gdansk', 'Porto', 'Bremen', 'Lille', 'Graz',
             'Utrecht', 'Aarhus', 'Bologna', 'Salzburg'),
    'dept': ('facilities', 'finance operations', 'procurement support', 'the service desk', 'estates',
             'human resources', 'logistics', 'information security', 'the programme office', 'customer accounts'),
    'meet': ('the migration timetable', 'staffing at the regional centres', 'the backlog of open service tickets',
             'the next round of equipment refresh', 'access arrangements for the data halls',
             'the template for monthly reports', 'handover notes for the training team',
             'changes to the escalation contact list', 'the schedule for quarterly reviews',
             'the condition of the loading bay', 'storage of spare parts', 'the layout of the shared dashboard'),
    'outcome': ('noted the update and asked for a short written summary', 'agreed that the item should return to the next meeting',
                'asked the contract managers to settle the details between themselves', 'recorded no objection',
                'deferred the matter pending further information from the Supplier', 'accepted the explanation offered',
                'asked for the figures to be rechecked before the next meeting'),
    'report': ('quarterly operations report', 'site visit note', 'equipment register', 'training log',
               'helpdesk summary', 'access audit sheet', 'delivery tracker', 'spares inventory'),
    'role': ('site coordinator', 'facilities lead', 'shift supervisor', 'training coordinator', 'logistics planner',
             'helpdesk team leader', 'records clerk', 'access controller'),
    'eq': ('rack-mounted servers', 'handheld scanners', 'network switches', 'uninterruptible power units',
           'label printers', 'replacement batteries', 'monitor arms', 'secure storage cabinets'),
    'train': ('the handling of shared credentials', 'the new ticket categories', 'safe use of the loading bay',
              'the revised escalation contacts', 'reading the monthly dashboard', 'fire marshal duties',
              'the handover checklist'),
    'system': ('badge reader', 'ticketing portal', 'backup scheduler', 'monitoring dashboard', 'visitor kiosk',
               'cooling plant controller'),
    'cause': ('a loose power connector', 'a faulty network card', 'an expired certificate on the portal',
              'an overnight software update', 'a blocked filter', 'a misconfigured schedule'),
    'tail': ('The Customer asked for a copy of the note.', 'No further action was requested.',
             'The Supplier will confirm the position in writing if it changes.',
             'Both parties regarded the matter as routine.', 'A short note of the discussion was placed on the file.',
             'It was agreed to keep the matter under review.', 'Neither party raised any difficulty.'),
    'words': (('reasonable endeavours', 'all reasonable endeavours'), ('promptly', 'without delay'),
              ('in writing', 'in writing or by email'), ('may', 'is entitled to'),
              ('the relevant manager', 'the Customer\'s contract manager'), ('quarterly', 'at least quarterly'),
              ('the Services', 'the Services and any related materials'), ('notified', 'notified in writing')),
}
CLAUSE_FRAMES = (
    '{party} {shall} {action} {object} {qual}.',
    '{cond}, {party} {shall} {action} {object}.',
    'Nothing in this clause limits the obligation of {party} to {action} {object}.',
    '{party} shall not, without the prior written consent of the other party, {neg} {object}.',
    'Any disagreement about {topic} is to be taken to the Governance Board before either party pursues any other step.',
    'The parties acknowledge that {topic} matters to the proper running of the Services, and each shall {action} {object} accordingly.',
    'Where {topic} touches more than one Statement of Work, {party} {shall} {action} {object} separately for each of them.',
    '{party} shall give the other party, on reasonable request, the information it needs to {action} {object}.',
    'A failure by {party} to {action} {object} does not by itself release the other party from its own obligations.',
    'Each party bears its own costs of complying with this clause {qual}.',
    'The Governance Board may, at any of its meetings, ask {party} to explain how it has dealt with {object} and record any recommendation in its minutes.',
    'In applying this clause the parties shall have regard to {object} and to any guidance issued by the Governance Board.',
    'If {party} learns of any difficulty in relation to {topic}, it shall tell the other party {qual}.',
    'This clause does not apply to the extent that a Statement of Work expressly provides for {topic} in a different way.',
    '{cond}, the parties may agree in writing a different arrangement for {topic} for a single Statement of Work.',
    'Records kept under this clause shall be {recordq} and shall be made available to the other party on request.',
    '{party} shall ensure that those of its staff who deal with {topic} understand what this Agreement requires of them.',
    'The rights given by this clause are in addition to any other right either party has under this Agreement.',
    'Where the parties disagree about how this clause applies to {object}, {party} shall continue to perform while the disagreement is resolved.',
    'References in this clause to {object} include anything done on behalf of {party} by a subcontractor.',
    '{party} {shall} keep the other party informed of progress on {topic} {qual}.',
    'No delay by either party in exercising a right in respect of {topic} operates as a waiver of that right.',
    'The parties shall review the operation of this clause at least once in each Contract Year.',
    '{party} {shall} nominate a person with authority to deal with {topic} on its behalf.',
)
INSTRUMENT_FRAMES = {
    'meeting': (
        'The Governance Board met at {site} on {date} and discussed {meet}; it {outcome}.',
        'At its meeting on {date}, the Governance Board considered {meet}, and {person} reported on the {report}.',
        '{person} of the Customer raised {meet} at the meeting on {date}, and the Supplier undertook to respond in writing.',
        'Both parties record that {meet} was reviewed on {date} without any change to the arrangements already in place.',
        'The minutes of the meeting held on {date} show that {person} and {person2} agreed an order of work for {meet}.',
        'The Governance Board asked {person} to bring the latest {report} to its next meeting and {outcome}.',
    ),
    'people': (
        '{person} will act as {role} for the {site} site, replacing {person2}, once the handover notes are complete.',
        '{person}, the {role}, has moved to the {site} team, and {person2} has taken over the day-to-day contact.',
        'The Supplier has confirmed {person} as the new {role} at {site}, and contact details have been circulated.',
        'The Customer has asked that {person} be copied on every {report} issued for {site}.',
        '{person2} has left the {site} programme, and the Supplier is arranging cover from its pool of {role}s.',
    ),
    'invoice': (
        'Invoices relating to {site} should be addressed to {dept}, to the attention of {person}.',
        'The Customer asked that every invoice quote the purchase order reference and the {site} cost centre.',
        '{dept} will reconcile the monthly statements with the Supplier\'s accounts team led by {person}.',
        'Remittance advice for the {site} account is to be sent by email to {dept}.',
        'The Supplier has opened a second billing contact at {site}, and {person} will deal with queries about layout.',
    ),
    'access': (
        'Access to the {site} data hall is limited to named staff, and {person} holds the visitor register.',
        'The loading bay at {site} will be closed on {date} for resurfacing, and deliveries will use the east entrance.',
        'A site induction for new Supplier staff was held at {site} on {date}, led by {person}.',
        'The Customer reminded the Supplier that badges must be worn at all times in {dept} areas.',
        'Parking passes for the {site} visit on {date} were arranged by {person}.',
    ),
    'equipment': (
        'The {site} store received {n} {eq} on {date}, which {person} checked against the delivery note.',
        '{n} {eq} were moved from {site} to the spares cage, and {person} updated the register.',
        'The Supplier replaced {n} {eq} at {site} without charge following the inspection on {date}.',
        'The surplus {eq} at {site} were counted by {person} and held for collection.',
    ),
    'training': (
        'A training session on {train} was given at {site} on {date} to {n} members of the Customer\'s staff.',
        '{person} has prepared a handbook on {train}, which both parties will review before it is issued.',
        'The Supplier offered a second session on {train}, which {person} will schedule with {dept}.',
    ),
    'incident': (
        'A short interruption of the {system} at {site} on {date} was traced to {cause} and put right by {person} within the hour.',
        'After the interruption of the {system} on {date}, {person} wrote a short account of {cause} for the file.',
        'The Customer asked for the {system} at {site} to be checked after {cause} was found on {date}.',
    ),
    'wording': (
        'The wording of the visitor notice at {site} was changed on {date} after {person} reviewed it.',
        'The badge design is changed each year, and {person} keeps the master copy at {site}.',
        'A proposal to substitute the supplier of the {eq} was raised by {person} and set aside.',
        'The door code at {site} is replaced every quarter, and {dept} keeps the record.',
        'The lease of the {site} store takes effect under a separate agreement between the Customer and its landlord.',
        'The rota for {dept} ceases to have effect when the shift pattern changes, which {person} will announce.',
        'The old visitor policy at {site} lapses once the new one comes into force, as {person} explained.',
        'A typing error in the {report} was corrected by {person} before it was filed.',
        'The price of the {eq} is fixed by their maker and not by anything in this Agreement.',
        'The permit for the {site} car park was revoked by the council, and {person} has applied for another.',
        'A copy of the {report} will stand at the front desk for the staff of {dept}.',
        'The cleaning contract at {site} was disapplied from the approved list by {person} after the audit.',
        'Nothing in the {report} is substituted for the minutes, which remain the record of {meet}.',
    ),
    'general': (
        'The parties thank {person} for the work done on {meet} during the past quarter.',
        'Nothing in this instrument affects any claim that either party may have in respect of {meet}.',
        'The parties confirm that the arrangements for {meet} continue as before.',
        'The Customer has noted the Supplier\'s offer to help with {meet}, and will respond after the next meeting.',
        '{person} will keep a record of anything arising from {meet}.',
    ),
}
INSTRUMENT_FAMILY_WEIGHTS = (('meeting', 4), ('people', 3), ('invoice', 2), ('access', 3), ('equipment', 3),
                             ('training', 2), ('incident', 2), ('general', 4), ('text', 3), ('wording', 16))
OTHER_DEFINITION = (
    '"{t}" means the matters so described in the relevant Statement of Work or, where none is so described, the meaning that the expression ordinarily has in the business of the parties.',
    'The expression "{t}" has the meaning that the Governance Board records for it from time to time, so far as that is consistent with this Agreement.',
    '"{t}" bears the meaning given to it in the Policies, and where the Policies are silent the more specific reading prevails.',
    '"{t}" includes anything which the parties agree in writing to treat as falling within it.',
    'In this Agreement "{t}" is used in its commercial sense, as between a customer and a supplier of managed services.',
    '"{t}" means what the parties intend by it in the clause in which it is used, read together with the relevant Statement of Work.',
)
RECITALS = (
    'The Customer carries on a business that depends on reliable information systems and on the facilities in which they are housed, and it wishes to buy managed services for them from a single supplier.',
    'The Supplier carries on the business of providing managed hardware, software, maintenance and training services, and has represented to the Customer that it has the skill and resources to provide the Services.',
    'The parties have agreed that the Services will be ordered through Statements of Work made under this Agreement, so that each order carries the terms set out below.',
    'The parties intend this Agreement to remain in place for a number of years and expect to adjust some of its figures as experience of the Services grows.',
)


def fill(rng, frame, base=None, number=None, **extra):
    """Replace each {name} of a frame by a draw from the pool of that name, or by the value given in `extra`."""
    out, i, people = [], 0, []
    while i < len(frame):
        ch = frame[i]
        if ch == '{':
            j = frame.index('}', i)
            name = frame[i + 1:j]
            if name in extra:
                out.append(str(extra[name]))
            elif name == 'date':
                out.append(fmt_date(rng, base + DAY * rng.randint(-120, 40)))
            elif name == 'n':
                out.append(str(rng.randint(2, 48)))
            elif name in ('person', 'person2'):
                person = rng.choice([x for x in FILL['person'] if x not in people])
                people.append(person)
                out.append(person)
            else:
                out.append(rng.choice(FILL[name]))
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return cap(''.join(out))


def topic_of(heading):
    return heading[0].lower() + heading[1:] if not heading.split()[0].isupper() and heading.split()[0] not in (
        'Customer', 'Supplier', 'Statements', 'Governance', 'Services') else heading


def clause_sentences(rng, topic, count):
    """`count` sentences of ordinary clause text, no frame twice."""
    return [fill(rng, frame, topic=topic) for frame in rng.sample(CLAUSE_FRAMES, count)]


def instrument_sentences(rng, P, inst, count):
    """Ordinary business: one family, `count` sentences."""
    total = sum(w for _, w in INSTRUMENT_FAMILY_WEIGHTS)
    pick = rng.randrange(total)
    for family, w in INSTRUMENT_FAMILY_WEIGHTS:
        if pick < w:
            break
        pick -= w
    out = []
    frames = list(INSTRUMENT_FRAMES.get(family, ()))
    rng.shuffle(frames)
    for number in range(count):
        if family == 'text':
            plain = [c for c in P.clauses if c.kind == 'bg']
            c = rng.choice(plain)
            old, new = rng.choice(FILL['words'])
            cl = number_at(c.number, inst.date, P.renumbers)
            out.append(rng.choice((
                'In clause %d, the word%s "%s" %s replaced by "%s".' % (cl, 's' if ' ' in old else '', old,
                                                                         'are' if ' ' in old else 'is', new),
                'The second sentence of clause %d is amended by inserting "%s" in place of "%s".' % (cl, new, old),
                'Clause %d is to be read with "%s" in place of "%s" wherever the latter appears.' % (cl, new, old))))
        else:
            out.append(fill(rng, frames[number % len(frames)], base=inst.date))
    if rng.random() < 0.35:
        out.append(fill(rng, rng.choice(FILL['tail'])))
    return out


# ---------------------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------------------

class Doc:
    def __init__(self, name):
        self.name, self.blocks, self.passages = name, [], []

    def add(self, text, tags=()):
        index = len(self.blocks)
        self.blocks.append(text)
        for ids, role in tags:
            self.passages.append({'file': self.name, 'paragraph': index, 'event_ids': list(ids), 'role': role})
        return index

    def text(self):
        return '\n\n'.join(self.blocks) + '\n'


def ref_text(rng, p, style, cited=None, short=None):
    if style == 'term':
        return 'the ' + p.term
    if style == 'short':
        return 'the ' + short
    if style == 'number':
        return rng.choice(REF_FRAMES['number']).format(noun=p.noun, n=cited)
    if style == 'heading':
        return REF_FRAMES['heading'].format(noun=p.noun, heading=p.heading)
    return REF_FRAMES['description'][p.kind].format(topic=p.topic, party=getattr(p, 'party', ''))


def pref_text(rng, target):
    if rng.random() < 0.5:
        return PREF_FRAMES[0].format(n=target.par, title=target.inst.title)
    return PREF_FRAMES[1].format(n=target.par, kind=KIND_NOUN[target.inst.kind], date=fmt_date(rng, target.inst.date))


def original_sentence(rng, p):
    if p.orig is None:
        return cap(rng.choice(UNSET).format(term=p.term, noun=p.noun))
    frame = rng.choice(ORIGINAL[p.kind])
    return cap(frame.format(term=p.term, party=getattr(p, 'party', ''), act=getattr(p, 'act', ''),
                            trigger=getattr(p, 'trigger', ''), subject=getattr(p, 'subject', ''),
                            conseq=getattr(p, 'conseq', ''), duty=getattr(p, 'duty', ''),
                            value=fmt_value(rng, p, p.orig)))


def render_agreement(rng, P, cust, supp):
    doc = Doc('agreement.md')
    doc.add('<a id="A-00"></a>\n# Framework Services Agreement')
    doc.add('This Framework Services Agreement is dated %s and is made between %s (the Customer) and %s (the Supplier).'
            % (fmt_date(rng, P.e0, DATE_FORMATS[1]), cust, supp))
    for text in rng.sample(RECITALS, 3):
        doc.add(text)
    starts = {first: part for part, first in P.parts}
    names = {part: n for n, (part, _) in enumerate(P.parts, 1)}
    others = list(ORIGIN_TERMS)
    for c in P.clauses:
        if c.number - 1 in starts:
            part = starts[c.number - 1]
            doc.add('## Part %d — %s' % (names[part], PARTS[part]))
        p = c.param
        doc.add('### %d. %s' % (c.number, c.heading), [([p.id], 'titles')] if p else ())
        if c.kind == 'defs':
            entries = [('param', q) for q in P.params.values()] + [('other', t) for t in others]
            rng.shuffle(entries)
            k, at = 1, 0
            while at < len(entries):
                size = rng.randint(3, 6)
                chunk = entries[at:at + size]
                at += size
                sentences, ids = [], []
                if k == 1:
                    sentences.append('In this Agreement, unless the context requires otherwise, the following expressions have the meanings given below.')
                for kind, item in chunk:
                    if kind == 'param':
                        sentences.append(rng.choice(DEFINITION).format(term=item.term, noun=item.noun, heading=item.heading))
                        ids.append(item.defn.id)
                    elif item == 'Effective Date':
                        sentences.append('"Effective Date" means %s, the date of this Agreement.' % fmt_date(rng, P.e0))
                    else:
                        sentences.append(rng.choice(OTHER_DEFINITION).format(t=item))
                doc.add('1.%d %s' % (k, ' '.join(sentences)), [(ids, 'defines')] if ids else ())
                k += 1
            continue
        topic = p.topic if p else topic_of(c.heading)
        sizes = [rng.randint(2, 3) for _ in range(rng.randint(2, 4))]
        text = clause_sentences(rng, topic, sum(sizes))
        paragraphs = [text[sum(sizes[:k]):sum(sizes[:k + 1])] for k in range(len(sizes))]
        tags = {}
        if p:
            at = rng.randrange(len(paragraphs))
            paragraphs[at].insert(rng.randint(0, len(paragraphs[at])), original_sentence(rng, p))
            tags[at] = [([p.id], 'states')]
        for k, sentences in enumerate(paragraphs):
            doc.add('%d.%d %s' % (c.number, k + 1, ' '.join(sentences)), tags.get(k, ()))
    return doc


def commence_ids(item):
    """What a commencement sentence tells: the provision's own commencement. It names an event but gives neither the
    date nor the instrument that records it, so it tells the event only where no instrument records it at all."""
    if item.commence[0] == 'event' and item.commence[1].record is None:
        return [item.id, item.commence[1].id]
    return [item.id]


def commence_sentence(rng, item):
    if item.commence[0] == 'date':
        return cap(rng.choice(COMMENCE_DATE).format(cdate=fmt_date(rng, item.commence[1])))
    return cap(rng.choice(COMMENCE_EVENT).format(event=item.commence[1].phrase))


def render_item(rng, P, inst, item):
    """The text of an operative paragraph and what it tells."""
    t = item.type
    if t == 'value':
        p = item.param
        ref = ref_text(rng, p, item.style, getattr(item, 'cited', None),
                       item.define.short if item.style == 'short' else None)
        tags = [([item.id], 'amends')]
        merged = False
        cdate = fmt_date(rng, item.commence[1]) if item.commence[0] == 'date' else None
        if item.op == 'disapply':
            options = [f for f in DISAPPLY if ('{cat}' in f) == (item.cat is not None)]
            sentence = rng.choice(options).format(ref=ref, cat=item.cat, start=fmt_date(rng, item.commence[1]),
                                                  end=fmt_date(rng, item.ends))
            sentences = [cap(sentence)]
        elif item.link is not None:
            other = ref_text(rng, item.link, item.other_style)
            sentences = [cap(rng.choice(LINK).format(ref=ref, other=other)), commence_sentence(rng, item)]
            tags.append((commence_ids(item), 'commences'))
            if item.sunset is not None:
                sentences.append(cap(rng.choice(SUNSET).format(sdate=fmt_date(rng, item.sunset))))
                tags.append(([item.id], 'sunsets'))
        else:
            value = fmt_value(rng, p, item.value)
            if item.op == 'exception':
                frame = rng.choice(EXCEPT)
            else:
                frame = rng.choice([f for f in SET if item.commence[0] == 'date' or '{cdate}' not in f])
            merged = '{cdate}' in frame
            sentences = [cap(frame.format(ref=ref, value=value, cat=item.cat, cdate=cdate))]
            if merged:
                tags.append(([item.id], 'commences'))
            else:
                sentences.append(commence_sentence(rng, item))
                tags.append((commence_ids(item), 'commences'))
            if item.sunset is not None:
                sentences.append(cap(rng.choice(SUNSET).format(sdate=fmt_date(rng, item.sunset))))
                tags.append(([item.id], 'sunsets'))
        return sentences, tags
    if t == 'define':
        ref = ref_text(rng, item.param, item.style, getattr(item, 'cited', None),
                       item.base.short if item.base is not None else None)
        return [cap(rng.choice(DEFINE).format(short=item.short, ref=ref))], [([item.id], 'defines')]
    if t == 'revoke':
        sentences = [cap(rng.choice(REVOKE).format(pref=pref_text(rng, item.target))), commence_sentence(rng, item)]
        return sentences, [([item.id], 'revokes'), (commence_ids(item), 'commences')]
    if t == 'sunset':
        sentence = rng.choice(SUNSET_LATER).format(pref=pref_text(rng, item.target), sdate=fmt_date(rng, item.date))
        return [cap(sentence)], [([item.id], 'sunsets')]
    if t == 'correct':
        p = item.target.param
        if item.field == 'value':
            old, new = fmt_value(rng, p, item.old), fmt_value(rng, p, item.new)
        else:
            old, new = fmt_date(rng, item.old), fmt_date(rng, item.new)
        sentence = rng.choice(CORRECT).format(pref=pref_text(rng, item.target), what=CORRECT_WHAT[item.field],
                                              old=old, new=new)
        return [cap(sentence)], [([item.id], 'corrects')]
    if t == 'renumber':
        sentence = rng.choice(RENUMBER).format(cdate=fmt_date(rng, item.commence), a=item.from_n, b=item.last,
                                               c=item.from_n + item.k, d=item.last + item.k)
        return [cap(sentence)], [([item.id], 'renumbers')]
    if t == 'record':
        return [cap(rng.choice(RECORD).format(event=item.event.phrase))], [([item.id], 'records')]
    if t == 'decoy':
        ref = ref_text(rng, item.param, item.style)
        return [cap(rng.choice(DECOY).format(ref=ref, value=fmt_value(rng, item.param, item.value)))], [
            ([item.id], 'proposes')]
    return [cap(rng.choice(DECOY_EVENT).format(event=item.event.phrase))], [([item.id], 'proposes')]


INTROS = {
    'deed': 'This deed is made on {date} between {cust} (the Customer) and {supp} (the Supplier). It is supplemental to the Framework Services Agreement between the same parties made on {e0} (the Agreement).',
    'letter': 'This side letter is written on {date} by {supp} (the Supplier) to {cust} (the Customer), and the Customer countersigns it. It relates to the Framework Services Agreement of {e0} (the Agreement).',
    'notice': 'This notice is given on {date} under the Framework Services Agreement of {e0} (the Agreement) by one party to the other, the parties being {cust} (the Customer) and {supp} (the Supplier).',
    'certificate': 'This certificate is issued on {date} under the Framework Services Agreement of {e0} (the Agreement) between {cust} (the Customer) and {supp} (the Supplier).',
    'slip': 'This correction slip is issued on {date} by the parties to the Framework Services Agreement of {e0} (the Agreement), {cust} (the Customer) and {supp} (the Supplier), to put right slips in earlier instruments.',
}


def render_instruments(rng, P, cust, supp):
    doc = Doc('instruments.md')
    doc.add('<a id="I-00"></a>\n# Instruments made under the Framework Services Agreement')
    doc.add('The instruments below were made after the Framework Services Agreement and are set out in the order of their dates. Each begins with its title and date and numbers its paragraphs from one.')
    for inst in P.insts:
        doc.add('## %s — dated %s' % (inst.title, fmt_date(rng, inst.date, DATE_FORMATS[1])))
        doc.add(INTROS[inst.kind].format(date=fmt_date(rng, inst.date), cust=cust, supp=supp,
                                         e0=fmt_date(rng, P.e0, DATE_FORMATS[1])))
        for _ in range(rng.randint(0, 2)):
            doc.add(' '.join(instrument_sentences(rng, P, inst, rng.randint(2, 3))))
        for n in range(1, inst.n + 1):
            item = inst.spots.get(n)
            if item is None:
                sentences = instrument_sentences(rng, P, inst, rng.randint(1, 3))
                tags = []
            else:
                sentences, tags = render_item(rng, P, inst, item)
                if rng.random() < 0.3:
                    sentences = instrument_sentences(rng, P, inst, 1)[:1] + sentences
                if rng.random() < 0.3:
                    sentences = sentences + instrument_sentences(rng, P, inst, 1)[:1]
            doc.add('%d. %s' % (n, ' '.join(sentences)), tags)
    return doc


PROCEDURE = """<a id="P-00"></a>
# Reading procedure for the Framework Services Agreement and its later instruments

**Documents.** The file holds the Framework Services Agreement (agreement.md) and, in the order of their dates, the instruments made under it (instruments.md). Every instrument has a title and a date, both in its heading, and no two instruments have the same date. A paragraph of an instrument is cited by its number in that instrument. The Effective Date is the date of the Agreement. A statement is judged on the whole file, including instruments dated after the date the statement is about; such an instrument can bear on that date only through a correction (rule 8) or through a commencement date it records (rule 3).

**1. What has effect.** A provision of an instrument has effect if the instrument makes it: if it fixes, changes, takes away, ends, corrects or records something under the Agreement. What a provision does is settled by what it does to a parameter, to an earlier provision or to the numbering, as rules 3 to 8 define each; the verb or phrase an instrument uses for it does not matter, and no list of verbs is complete. A proposal, request, offer, suggestion or statement of what a party expects or hopes makes nothing and changes nothing, even where it names a value, and neither does any paragraph about something other than a parameter, an earlier provision or the numbering. An instrument records an event only if it states that the event has happened.

**2. Values.** From the Effective Date the value of a parameter is the value stated in its clause in the Agreement. A clause that says the parameter has no value until an amending provision supplies one gives it no value until then. Periods are calendar days. Values are compared as numbers or officer titles; wording, digits against words and the form of a date do not matter, and five per cent, 5% and 5 per cent are the same value. The work under the Agreement falls in four categories: Hardware Services, Software Services, Maintenance Services and Training Services.

**3. Amending provisions and commencement.** A provision that gives a parameter a value for all work, in place of any value it had, is a substitution. A provision that gives it a value for one category of work only is an exception. A provision that, for a named period of dates, leaves a parameter without any value, for all work or for the category it names, is a disapplication, whatever words it uses for doing so; a period it names includes its first and its last date. A provision that gives a parameter the value that another parameter has on the day the provision takes effect is a substitution too: it fixes the value once, as these rules establish the other parameter's value on that day (no value if there is none), and a later change to the other parameter does not change it. Substitutions, exceptions and disapplications are the amending provisions. A provision that names no category applies to every category. An amending provision has effect on and from its commencement date and not before; the date named as the start of a provision is itself included, and the date named as its sunset is not. Its commencement date is the date it names or, if it names an event, the date of the instrument in the file that records that the event has happened. Until an instrument in the file records the event the provision has no effect, and if no instrument does it never has effect.

**4. Sunsets.** A provision that says that it, or another provision, has no effect from a date, or ceases to have effect on a date, has no effect on that date or after it. A sunset may be in the provision itself or in a later instrument that names the provision; if both apply, the earlier date governs.

**5. Revocation.** A provision that withdraws an earlier amending provision in full is a revocation. A revoked provision has no effect on or after the date on which the revocation commences, even if its own commencement date or event falls later. What it altered is then given by the other provisions that have effect, or by the Agreement if there are none. A revocation is not itself revoked and has no sunset.

**6. Which provision prevails.** Where several provisions have effect on a date for the same parameter and apply to the same category, the provision in the instrument with the latest date prevails. A provision that applies to every category therefore replaces an earlier exception for each category as well. A provision that has no effect on a date is ignored for that date. A provision that a later provision overrides still counts as having taken effect.

**7. Naming a parameter.** A provision may name a parameter (a) by its defined term, as defined in clause 1 of the Agreement; (b) by an expression that an instrument defines, as meaning the parameter or as meaning another such expression, which has that meaning from the paragraph that defines it onwards, in that instrument and in every later instrument, and not before; (c) by a description of the form "the period, percentage, amount, number or officer that the clause on T allows, fixes or names", where T is the heading of one clause; (d) by the heading of its clause; or (e) by clause number. A clause number cited in an instrument means the clause that bore that number on the date of that instrument. A provision that gives clauses new numbers is a renumbering: it takes effect on its commencement date, the clauses it names carry their new numbers from then, and every reference to a renumbered clause follows the clause, not the number.

**8. Corrections.** A provision that says that a figure or date in an earlier paragraph was printed wrongly, and what it should have said, is a correction. A correction applies as if the instrument it corrects had always read as the correction says. It corrects one field of the paragraph it names (the value, the commencement date, the date of sunset or the last day of a disapplication) and applies at every date, including dates before the correction.

**9. Statements.** "On D, the T was V" is about the value in force on D for work generally, which is given by the provisions that name no category, or, where it says "for C", for the category C, which is given by the provisions that apply to C. A parameter has no value in force before it takes one and while it is disapplied, and a statement then says that it had no value in force. "The number of instruments in the file with an amending provision for T that had taken effect on or before D" counts each instrument once if it holds an amending provision for T, of any category, that took effect on or before D under rules 3 to 8, whether or not it was later overridden, revoked or ended; a provision revoked or ended on or before its own commencement date never took effect. Revocations, corrections, sunsets, renumberings and definitions are not amending provisions for this count. "The number of amending provisions in the file, for all parameters together" counts in the same way each amending provision of any parameter (each provision, not each instrument) that took effect on or before D. "The first date on which the T was V" is the earliest date on which the general value was V. A statement that the value on one date differed from, or was the same as, the value on another date compares the values in force on the two dates, for the category if one is named and generally otherwise.

**10. Nothing else.** No outside knowledge is needed or allowed. Every statement in the reports can be settled from the file and these rules.
"""


COUNTS_RULE = """**10. Counts over the whole file.** In this rule an amending provision is any substitution, exception or disapplication of rule 3, of any parameter, and each provision is counted once; a parameter is a clause of the Agreement that holds a value or leaves one open, named by its defined term in clause 1; an instrument holds a provision if the provision stands in one of its paragraphs. A provision has effect on a date if that date is on or after its commencement date and before the date on which it ends, which is the earliest of its own sunset, a sunset in a later instrument and the commencement of a revocation of it, each date as corrected under rule 8; a disapplication therefore has effect on every date of its period. It does not matter whether another provision prevails over it on that date (rule 6), and a provision that has effect on a date need not have begun on that date. A provision took effect on its commencement date if it had effect on that date. It never took effect if there is no date on which it has effect: its commencement depends on an event that no instrument in the file records, or its commencement date is not before the date on which it ends, so that it is revoked or ended on or before it begins. However a statement words it, a count is taken as follows, D, D1 and D2 being dates and D2 not before D1. (a) Provisions that had taken effect on or before D: those that took effect on a date on or before D, whether or not they were later overridden, revoked or ended. (b) Provisions that took effect between D1 and D2, both dates included: those that took effect on D1, on D2 or on a date between them. (c) Disapplications that had taken effect on or before D, and likewise exceptions: the provisions in (a) that are disapplications, and likewise those that are exceptions. (d) Provisions that had effect on D: those that have effect on D, as defined above, so that a provision that began on or before D and had not ended on or before D is counted, and one that began after D, or ended on or before D, is not. (e) Parameters for which an amending provision had taken effect on or before D: each parameter that has at least one provision in (a), counted once however many it has; a provision that takes over the value of another parameter belongs to the parameter it gives a value to. (f) Instruments that hold such a provision: each instrument that holds at least one provision in (a), counted once however many it holds. (g) Provisions that never took effect: those that never took effect, as defined above. The number is the number of provisions, parameters or instruments counted; a provision that is not counted by a rule is not counted by it, and a provision that is not an amending provision (a revocation, a correction, a sunset, a renumbering or a definition) is never counted."""


def procedure_text(size=1):
    """procedure.md: the same rules at every size, and from size 2 the rule that defines the counts over the whole file
    (rule 10), which the statements of size 1 do not use."""
    if size == 1:
        return PROCEDURE
    last = PROCEDURE.index('**10. Nothing else.**')
    return PROCEDURE[:last] + COUNTS_RULE + '\n\n' + PROCEDURE[last:].replace('**10. Nothing else.**', '**11. Nothing else.**')


def render_procedure(size=1):
    doc = Doc('procedure.md')
    doc.blocks = procedure_text(size).rstrip('\n').split('\n\n')
    return doc


def inventory():
    rows = []
    for row in PARAM_ROWS:
        kind, topic, term = row[0], row[2], row[3]
        r = {'kind': kind, 'topic': topic, 'term': term, 'heading': topic[0].upper() + topic[1:],
             'noun': NOUNS[kind]}
        if kind == 'days':
            r.update(party=row[4], act=row[5], trigger=row[6])
        elif kind in ('pct', 'money'):
            r.update(subject=row[4])
        elif kind == 'count':
            r.update(unit=row[4], conseq=row[5])
        else:
            r.update(party=row[4], duty=row[5])
        rows.append(r)
    return {
        'placeholders': 'ref, pref, value, cat, cdate, sdate, start, end, event, old, new, what, a, b, c, d, short, '
                        'term, heading, noun, party, act, trigger, subject, unit, conseq, duty, date, date2, n',
        'files': {'procedure': 'procedure.md', 'agreement': 'agreement.md', 'instruments': 'instruments.md'},
        'formats': {'clause_heading': '### {n}. {heading}', 'part_heading': '## Part {k} — {title}',
                    'instrument_heading': '## {title} — dated {date}', 'paragraph': '{n}. {text}',
                    'heading_date': '{d} {mname} {y}', 'clause_paragraph': '{n}.{k} {text}'},
        'categories': CATEGORIES, 'months': MONTHS, 'month_abbr': MONTH_ABBR, 'date_formats': DATE_FORMATS,
        'ones': ONES, 'tens': TENS, 'value_formats': VALUE_FORMATS, 'nouns': NOUNS, 'parameters': rows,
        'original': ORIGINAL, 'unset': UNSET, 'definition': DEFINITION, 'substitute': SET, 'exception': EXCEPT,
        'disapply': DISAPPLY, 'revoke': REVOKE, 'commence_date': COMMENCE_DATE, 'commence_event': COMMENCE_EVENT,
        'record': RECORD, 'link': LINK, 'sunset_own': SUNSET, 'sunset_later': SUNSET_LATER, 'correct': CORRECT,
        'correct_what': CORRECT_WHAT, 'renumber': RENUMBER, 'define': DEFINE, 'decoy': DECOY,
        'decoy_event': DECOY_EVENT, 'pref_frames': PREF_FRAMES, 'ref_frames': REF_FRAMES, 'statements': STATEMENTS,
        'event_phrases': EVENT_PHRASES + EVENT_PHRASES_MORE, 'kind_title': KIND_TITLE, 'kind_noun': KIND_NOUN, 'readers': READERS,
    }


INVENTORY = inventory()


def names_of(events):
    """For every officer title that a statement can name, every name the file gives it."""
    party_of = {f['parameter']: f['party'] for f in events if f['kind'] == 'parameter' and f.get('party')}
    held = {}
    for f in events:
        if f['kind'] == 'parameter' and f['value_kind'] == 'officer' and f['value']:
            held.setdefault(f['value'], set()).add(f['party'])
        elif f['kind'] == 'amendment' and f['parameter'] in party_of and f['value'] in TITLES:
            held.setdefault(f['value'], set()).add(party_of[f['parameter']])
    return {title: [title] + [pattern.format(party=party, title=title) for party in sorted(held.get(title, ()))
                              for pattern in VALUE_FORMATS['officer']] for title in TITLES}


PREAMBLE = ('Each statement below is about the Framework Services Agreement and the instruments made under it, read '
            'under the reading procedure, and each is judged on the whole file. Where a value is asked for, write it '
            'as follows. A period in days, an amount of money and a number of instruments are whole numbers (digits; '
            'a currency sign is not needed). A percentage is a decimal number such as 7.5. A date is written '
            'YYYY-MM-DD. A responsible officer is written as its title. A comparison of the values on two dates is '
            'written same or different. The word none means that the sources establish that there is no value (or, '
            'for a date, that there is no such date).')
# From size 2 some statements count provisions or parameters rather than instruments; the note says so in the same
# plain words, and names no kind of event or trap.
PREAMBLE_COUNTS = (' A number of provisions, of parameters or of instruments is likewise a whole number, and a count '
                   'of nothing is written 0.')


def build(rng):
    size = current_size()
    if size > 1:
        # a stream of its own for each size: the same seed and index give another dossier at another size, and the
        # given `rng` is left untouched but for one draw, so that `generate.choose` still draws from it afterwards
        work = random.Random('amendments:size%d:%r' % (size, rng.random()))
    else:
        work = rng
    for _ in range(40):
        P = plan(work, size)
        events = facts(P)
        cands = candidates(work, events, P.params, size)
        if size == 1:
            with legacy_reading():              # the draws of size 1 stay the bytes they were
                chosen = select(work, events, cands)
        else:
            chosen = select_big(work, events, cands)
        if chosen:
            break
    else:
        raise generate.GenerationError('No dossier with the required statements was found')
    slots = [slot_for(work, P.params, c, size) for c in chosen]
    cust, supp = work.choice(CUSTOMERS), work.choice(SUPPLIERS)
    agreement = render_agreement(work, P, cust, supp)
    instruments = render_instruments(work, P, cust, supp)
    procedure = render_procedure(size)
    sources = {'agreement.md': agreement.text(), 'instruments.md': instruments.text(),
               'procedure.md': procedure.text()}
    passages = sorted(agreement.passages + instruments.passages,
                      key=lambda x: (x['file'], x['paragraph'], x['role']))
    readers = {name: {'base': {'events': events, 'reader': name}, 'unknowns': {}} for name in READERS}
    return {'title': 'Framework services agreement: values in force', 'sources': dict(sorted(sources.items())),
            'base': {'events': events, 'reader': None}, 'unknowns': {}, 'slots': slots, 'readers': readers,
            'preamble': PREAMBLE if size == 1 else PREAMBLE + PREAMBLE_COUNTS, 'events': events,
            'passages': passages, 'names': names_of(events), 'size': size}
