"""Independent rendered-document checks for the silent truncated-view family.

Reading imports no family rule, parser or constant. Its two readings differ only
by a locally implemented physical-line filter. The three prose documents are pinned
below sentence by sentence, including headings, so adding a sentence cannot silently
change their meaning. The implementation is imported separately for boundary and
parameter probes only.

Leak probes consume the ENTIRE rendered report before labels are attached, then the
source file names, byte lengths, line counts and the layout of the annex (line
lengths, paragraph sizes, entry counts). Staging renames either report report.md; the
original correct/mixed filename is never a feature. Seeds 1..300 train and 301..1300
test the guessers. A guesser may beat the majority class only by four standard
errors of an accuracy measured over independent units (dossiers, or reports for
claim targets, since the twelve claims of a report share their features). Ranking
measures (AUC) cover every claim feature inside strata of kind and polarity, and
every report feature for the dossier target, so that a partial cue (for example hidden
amounts that are all large) is seen even when no feature value is mostly lures.
Polarity is disclosed separately: hidden entries necessarily exist, so a positive
claim is the likelier lure, and its rates and AUC are measured and printed.
The layout checks compare line lengths and paragraph sizes of the annex between
dossiers with and without a lure, between paragraphs with and without a penalty
entry, and between paragraphs inside and beyond the view.
Only cases 1..300 are cached whole; the others are reduced to compact records.
"""
import ast
import collections
import functools
import math
from pathlib import Path
import random
import re
import statistics
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import generate
generate.HARD_FAMILIES.setdefault('truncated', 'lure_truncated')
import blindness
import run_blind
import scorecard

CASES = {}
TEST_SEEDS = 1300
CLAIM_SEEDS = 700
Z = 4
PROCEDURE = '''# Commercial review procedure

<a id="P-01"></a>
## P-01 — Authority and scope
Oriole Estates Cooperative, Contracts Office, 20 October 2034.
Procedure issued for review of the adopted estates annex.

<a id="P-02"></a>
## P-02 — Reading the schedule
Each document supplied for this review counts in full, from its first line to its last line.
The annex is the complete adopted schedule of termination penalties for the sites in this packet.
A site has a termination penalty if and only if the annex contains an entry setting a termination penalty for that site.
The amount of a site's termination penalty is the euro amount stated in that entry.
Each site has at most one such entry.
If the annex contains no such entry for a site, the annex sets no termination penalty for that site.
The phrase "sets the termination penalty at EUR X" asserts both that a penalty entry exists and that its amount is X euros.
The phrase "does not set the termination penalty at EUR X" is the negation of that assertion.
Line wrapping, entry order, and the date on which an entry was filed do not change its effect.
'''
PREFIX = '# Review of the estates annex\n\n'
# The same document sentence by sentence, written out separately for the checks
# that must name a missing, changed or added sentence. A new section, a new
# sentence in either section, and a changed heading all fail.
REVIEWED_PROCEDURE_TITLE = 'Commercial review procedure'
REVIEWED_PROCEDURE_SECTIONS = (
    ('P-01', 'Authority and scope', (
        'Oriole Estates Cooperative, Contracts Office, 20 October 2034.',
        'Procedure issued for review of the adopted estates annex.')),
    ('P-02', 'Reading the schedule', (
        'Each document supplied for this review counts in full, from its first line to its last line.',
        'The annex is the complete adopted schedule of termination penalties for the sites in this packet.',
        'A site has a termination penalty if and only if the annex contains an entry setting a termination penalty for that site.',
        "The amount of a site's termination penalty is the euro amount stated in that entry.",
        'Each site has at most one such entry.',
        'If the annex contains no such entry for a site, the annex sets no termination penalty for that site.',
        'The phrase "sets the termination penalty at EUR X" asserts both that a penalty entry exists and that its amount is X euros.',
        'The phrase "does not set the termination penalty at EUR X" is the negation of that assertion.',
        'Line wrapping, entry order, and the date on which an entry was filed do not change its effect.')),
)
# Every report is this frame; only the twelve claim sentences vary.
REVIEWED_REPORT_TITLE = '# Review of the estates annex'
REVIEWED_CITATIONS = 'Citations: filing-note F-01; annex A-01; procedure P-02.'
REVIEWED_CLAIM_FORMS = (
    r'The annex sets a termination penalty for site [A-Z][a-z]+\.',
    r'The annex contains no termination penalty entry for site [A-Z][a-z]+\.',
    r'The annex sets the termination penalty at EUR [0-9]+ for site [A-Z][a-z]+\.',
    r'The annex does not set the termination penalty at EUR [0-9]+ for site [A-Z][a-z]+\.',
)
READER = 'sees_truncated_view'
# The fixed text of the other two documents and the fixed frame of the annex: the
# paragraphs that are not numbered items.
REVIEWED_FILING_TEXT = """The estates team has brought together the adopted papers for the winter
handover programme. This copy accompanies the commercial review rather than the
site inspection visit. The office kept the original order of the returned
schedules so that a query can be traced back to the folder used by the contracts
assistant. The annex includes facilities instructions as well as individual
commercial entries. The facilities instructions explain how the handovers were
organised; each commercial entry names one site and records the termination
penalty agreed for it. The two types of material travelled together when the
schedule was circulated for signature.

The first circulation went to the regional managers while several property
teams were still arranging their return visits. Entries received after that
circulation were filed at the back of the schedule, in the last part headed as
returns. The contracts office did not renumber the earlier items when those
entries arrived, so the returns carry the next numbers in the same sequence. The
final adoption meeting considered the assembled copy, including the returns
filed at the back. This is why the sequence in the annex follows the work of
assembling the file rather than the order in which a reviewer might wish to
read the sites. No alphabetical index was prepared for the meeting pack.

The archive copy came from a printer export used by the commercial team. Most
items were wrapped for a narrow page. Some mail-merge items were retained as a
single line because the export treated their narrative field as one printing
instruction. A blank line separates the items, and a few blank lines separate
the parts of the schedule. These features reflect the office's transfer between
systems. They were present before this review was requested. The records
assistant has retained them to keep the copy comparable with the print spool
held in the administrative archive.

The review concerns what the adopted annex says, rather than whether any
contract has already ended. No termination notices are included in this
packet. The facilities team was asked for practical arrangements for an orderly
exit, including key collection, meter readings, and the return of equipment.
Those arrangements were useful even at sites where the commercial negotiation
did not result in a termination penalty. The site managers described the
physical handover arrangements in the same narrative field used by the
contracts team for commercial terms. The resulting items consequently have
similar lengths and similar opening language.

The office received questions about access to locked rooms during the final
week of occupation. The estates coordinator arranged for a member of the local
team to attend each visit. This reduced the need to exchange keys by post and
made it easier to identify equipment belonging to the outgoing operator.
Photographs were taken for the facilities archive. They have not been included
here because the commercial review does not concern the condition of the
fixtures. A later condition survey will use those images with its own visit
notes and the inventory prepared by the local team.

Several managers asked whether the handover pack would replace the ordinary
maintenance log. The estates coordinator explained that the log would continue
to accompany the building until the incoming operator had accepted custody.
The log contains visits by maintenance contractors, reports of minor defects,
and checks of emergency lighting. None of those records was copied into the
commercial narrative merely to make the packet longer. The operational
instructions in the annex are those circulated for this particular programme,
including arrangements for storing the log during the change of operator.

The finance liaison attended the adoption meeting to identify the account to
which a termination payment would be posted if a termination occurred. That
discussion was separate from the setting of the amount in an individual site
entry. The accounts staff had not raised invoices at the time this packet was
prepared. Their month-end worksheet therefore does not accompany the annex.
The review team requested the adopted wording because the existence of an
invoice would answer a different question. The commercial review records the
terms of the annex without forecasting whether any payment will become due.

The packet was prepared by Mira Fen, records officer at the fictional Oriole
Estates Cooperative, on 20 October 2034. It was sent to the internal commercial
review desk on the same day. The original printer output is retained by the
records office. This covering note describes the assembly history and the
purpose of the enclosed papers. Questions about the physical transfer of a
building remain with the estates team; questions about the wording of a site
entry can be returned to the contracts office with the site name and the
opening words of the relevant entry.
"""

REVIEWED_HANDOVER_TEXT = """The facilities desk prepared this account of the handover programme for the
commercial reviewers who did not attend the planning meetings. Each local team
was given a visit window and asked to identify a person who could accompany
the incoming operator through the building. The desk grouped visits by travel
area to avoid repeated journeys. Commercial negotiations took place separately
and did not determine the travel route. This account explains the operational
material that appears between the site entries in the annex and the records
that the desk expects to keep after the programme has finished.

At the first planning meeting the teams compared their existing inventories.
Some inventories used room numbers while others used the names painted on the
doors. The coordinator asked teams to bring a marked floor plan so that the
visiting staff could reconcile those descriptions without changing the labels
on equipment. The working inventory includes desks, portable heaters, storage
cabinets, and the items kept in shared service rooms. It also records where
an item has been taken out of use. These details are useful to the incoming
operator even when no transfer of ownership is involved.

The practical visits start at the reception point used during ordinary working
hours. A local contact brings the sign-in book and introduces the visiting
staff to the person holding the building keys. The group then checks the
rooms in the order shown on the floor plan. Areas that cannot be entered are
recorded in the visit note with the reason given at the time. The coordinator
has asked the teams to avoid relying on recollections at the end of the day;
the visit note is prepared while the people who saw each room are still
available to describe it.

Meter readings were a recurring source of delay in previous programmes. Some
meters are in shared cupboards, and a key held by the landlord is needed to
reach them. The facilities desk has therefore included access arrangements
among the operational instructions. A photograph of the display is kept with the
reading when possible. The reading is also written in the visit note so that
the image can be checked against a legible transcription. The accounts team
will later use the readings for utility reconciliation. That exercise is
separate from reviewing the commercial wording in the adopted annex.

For security equipment, the incoming operator receives a demonstration before
the local contact leaves. The demonstration covers the ordinary locking
sequence and the location of the contact list for equipment faults. Codes are
exchanged through the security desk rather than printed in the visit note.
The handover paperwork records that the exchange took place without retaining
the code itself. Where a demonstration has to be postponed, the coordinator
books a separate attendance. A postponed demonstration does not prevent the
team from recording the other parts of the visit that were completed that day.

The desk has also arranged a place for paper records during the transition.
Maintenance books and equipment instructions stay in the building in a labelled
cabinet. Records belonging to the outgoing operator are collected separately.
The local contact identifies the two groups during the visit, with particular
attention to folders that contain personal information. The commercial packet
contains no personnel records. The examples in the operational schedule refer
to buildings and equipment because those are the subjects the visiting teams
will encounter. A separate records officer handles any question about a
folder whose ownership is unclear.

At the end of each visit the coordinator compares the completed note with the
planned route. Items left for a return visit are collected in a short work
list. That list describes the outstanding activity and the person expected
to arrange it. It is not a revised commercial schedule. The facilities desk
expects some return visits because access and attendance cannot always be
arranged on one day. The team will keep the initial note alongside the later
record rather than replacing it, so that the sequence of practical work can
be reconstructed if a manager has a question after occupation changes.

Prepared by Len Arvo, facilities coordinator, Oriole Estates Cooperative,
20 October 2034, for the internal commercial review desk. The organisation and
the personnel in these papers are fictional. The desk's purpose in providing
this background is to explain why commercial entries sit among operational
instructions in one annex. The same assembled schedule was used by several
teams, each reading it for a different task. The review desk has received the
procedure, the filing note, this programme account, and the assembled annex
as a single packet for its review of the report statements.
"""

REVIEWED_SITES_TEXT = """The programme covers 32 sites: Alder, Aspen, Birch, Bramble, Cedar, Clover, Damson, Dovetail, Elder, Elmstead, Fennel, Foxglove, Garnet, Hawthorn, Hazel, Iris, Ivy, Juniper, Kestrel, Larch, Maple, Nutmeg, Orchid, Plover, Quince, Rowan, Sorrel, Teasel, Umber, Violet, Willow, and Yarrow. The desk arranged a handover visit for every one of them. Some of these sites have a commercial entry in the annex and some do not."""
REVIEWED_ANNEX_FRAME = (
    ('# Adopted estates annex',),
    ('Oriole Estates Cooperative, Contracts Office, 20 October 2034.',
     'Schedule assembled for the winter handover programme.'),
    ('<a id="A-01"></a>', '## A-01 — Assembled schedule', 'Printer export retained by the records office.'),
    ('Part A — Travel group A',), ('Part B — Travel group B',), ('Part C — Travel group C',),
    ('Part D — Travel group D',), ('Part E — Travel group E',), ('Part F — Travel group F',),
    ('Part G — Travel group G',), ('Part H — Returns filed after circulation',),
    ('End of schedule.',),
)
PENALTY_ENTRY = r'For site ([A-Za-z]+), the agreed termination penalty is EUR ([0-9]+)\.'


def normalized(text):
    return ' '.join(text.split())


def case(seed, index=1):
    if index != 1 or seed > 300:
        return generate.build_case('truncated', seed, index)
    if seed not in CASES:
        CASES[seed] = generate.build_case('truncated', seed, index)
    return CASES[seed]


def sources(built):
    return {name[8:]: text for name, text in built['files'].items() if name.startswith('sources/')}


def filtered(source, lines=2000, width=2000):
    # Slice ORIGINAL lines before rejecting over-width lines, independently per file.
    return {name: '\n'.join(line for line in text.splitlines()[:lines] if len(line) <= width)
            for name, text in source.items()}


def clipped(source, lines=2000, width=2000):
    """The other kind of tool: it keeps every line of the view but clips a long one."""
    return {name: '\n'.join(line[:width] for line in text.splitlines()[:lines])
            for name, text in source.items()}


def texts(report):
    return re.findall(r'\*\*C\d+\.\*\* (.*?) Citations:', report)


def shape(text):
    return re.sub(r'\d+', 'N', re.sub(r'for site \w+', 'for site SITE', text))


def frame(report):
    """Everything a reader sees in a report except the claim sentences."""
    for text in texts(report):
        report = report.replace(text, '<CLAIM>', 1)
    return report


REVIEWED_REPORT_FRAME = REVIEWED_REPORT_TITLE + '\n\n' + '\n'.join(
    '<a id="C{0:02d}"></a>\n**C{0:02d}.** <CLAIM> {1}\n'.format(number, REVIEWED_CITATIONS)
    for number in range(1, 13))


def sentences(text):
    return tuple(re.split(r'(?<=[.])\s+(?=[A-Z"])', ' '.join(text.split())))


REVIEWED_FILING = ('Filing note for the estates packet',
                   (('F-01', 'Assembly and circulation', sentences(REVIEWED_FILING_TEXT)),))
REVIEWED_PROGRAMME = ('Facilities programme account',
                      (('H-01', 'Work arranged for handover', sentences(REVIEWED_HANDOVER_TEXT)),
                       ('H-02', 'Sites in the programme', sentences(REVIEWED_SITES_TEXT))))
REVIEWED_PROCEDURE = (REVIEWED_PROCEDURE_TITLE, REVIEWED_PROCEDURE_SECTIONS)


def document_structure(text):
    """Title, then (anchor, heading, sentences) per section, read from the Markdown."""
    title, *chunks = re.split(r'\n(?=<a id=)', text.strip())
    sections = []
    for chunk in chunks:
        match = re.fullmatch(r'<a id="([^"]+)"></a>\n## \1 — ([^\n]+)\n(.*)', chunk, re.S)
        assert match, 'Malformed section: ' + chunk[:60]
        anchor, heading, body = match.groups()
        sections.append((anchor, heading, sentences(body)))
    assert title.startswith('# ')
    return title[2:].strip(), tuple(sections)


def procedure_structure(text):
    title, sections = document_structure(text)
    return '# ' + title, sections


def check_document(test, text, reviewed):
    """Fail on any missing, changed or added sentence, section or heading."""
    test.assertEqual(document_structure(text), reviewed)


def check_procedure(test, text):
    check_document(test, text, REVIEWED_PROCEDURE)


def edit_sentence(text, sentence, replacement):
    """Replace one sentence of the rendered, line-wrapped Markdown."""
    pattern = r'\s+'.join(re.escape(word) for word in sentence.split())
    changed, count = re.subn(pattern, lambda _: replacement, text, count=1)
    assert count == 1, sentence
    return changed


def site_names(source):
    """The programme's list of sites, read from the rendered programme document."""
    match = re.search(r'The programme covers (\d+) sites: (.*?)\. The desk', normalized(source['programme.md']))
    names = re.split(r', (?:and )?', match[2])
    assert len(names) == int(match[1])
    return names


class Reading:
    """Read only rendered Markdown; absence is false before applying negation."""
    def __init__(self, source):
        self.penalties = {}
        for site, amount in re.findall(PENALTY_ENTRY, normalized(source['annex.md'])):
            if site in self.penalties:
                raise AssertionError('Duplicate entry for ' + site)
            self.penalties[site] = int(amount)

    @staticmethod
    def parse(text):
        match = re.fullmatch(r'The annex (sets a termination penalty|contains no termination penalty entry) '
                             r'for site ([A-Za-z]+)\.', text)
        if match:
            return 'penalty_presence', match[2], match[1].startswith('sets'), None
        match = re.fullmatch(r'The annex (sets|does not set) the termination penalty at EUR ([0-9]+) '
                             r'for site ([A-Za-z]+)\.', text)
        if not match:
            raise AssertionError('Unrecognised claim: ' + text)
        return 'penalty_amount', match[3], match[1] == 'sets', int(match[2])

    def verdict(self, text):
        kind, site, positive, amount = self.parse(text)
        truth = site in self.penalties
        if kind == 'penalty_amount':
            truth = truth and self.penalties[site] == amount
        return 'supported' if truth == positive else 'refuted'


def raw(seed):
    return generate.family_builder('truncated')(random.Random(
        'evalsuite:{}:{}:1:truncated'.format(generate.GENERATOR_VERSION, seed)))


# ---------- statistics ----------

def auc(positives, negatives):
    """Chance that a positive scores above a negative; ties count half."""
    a, b = collections.Counter(positives), collections.Counter(negatives)
    below, total = 0, 0.0
    for value in sorted(set(a) | set(b)):
        total += a[value] * (below + b[value] / 2)
        below += b[value]
    return total / (len(positives) * len(negatives))


def null_se(n1, n0):
    """Standard error of an AUC when the feature says nothing."""
    return math.sqrt((n1 + n0 + 1) / (12 * n1 * n0))


def ks(a, b):
    ca, cb = collections.Counter(a), collections.Counter(b)
    seen_a = seen_b = 0
    worst = 0.0
    for value in sorted(set(ca) | set(cb)):
        seen_a += ca[value]
        seen_b += cb[value]
        worst = max(worst, abs(seen_a / len(a) - seen_b / len(b)))
    return worst


def same_distribution(test, a, b, label):
    """Fail when two samples differ by more than chance: Kolmogorov-Smirnov distance beyond
    its 0.0002 critical value, or an AUC more than four null standard errors from one half."""
    test.assertGreater(min(len(a), len(b)), 30, label)
    distance = ks(a, b)
    critical = 2.2 * math.sqrt((len(a) + len(b)) / (len(a) * len(b)))
    test.assertLess(distance, critical, '{}: KS {:.4f} against {:.4f}'.format(label, distance, critical))
    value, bound = auc(a, b), Z * null_se(len(a), len(b))
    test.assertLess(abs(value - .5), bound, '{}: AUC {:.4f} against {:.4f}'.format(label, value, bound))
    return distance, value


def chi_square_z(a, b):
    """Homogeneity of two category counts as a normal score (Wilson-Hilferty), over categories seen often enough."""
    keys = [k for k in set(a) | set(b) if a[k] + b[k] >= 10]
    n, m = sum(a[k] for k in keys), sum(b[k] for k in keys)
    statistic = 0.0
    for k in keys:
        total = a[k] + b[k]
        for count, size in ((a[k], n), (b[k], m)):
            expected = total * size / (n + m)
            statistic += (count - expected) ** 2 / expected
    df = max(len(keys) - 1, 1)
    return ((statistic / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))


def margin(baseline, units):
    """Four standard errors of an accuracy near `baseline` measured over independent units."""
    return Z * math.sqrt(baseline * (1 - baseline) / units)


# ---------- reading the annex ----------

def annex_paragraphs(annex):
    """(first line index, lines) for each run of non-blank lines of the annex."""
    out, current, start = [], [], 0
    for number, line in enumerate(annex.splitlines()):
        if line.strip():
            if not current:
                start = number
            current.append(line)
        elif current:
            out.append((start, current))
            current = []
    if current:
        out.append((start, current))
    return out


def annex_items(annex):
    """The numbered items of the annex, each as read from the rendered lines."""
    out = []
    for start, group in annex_paragraphs(annex):
        if not group[0].startswith('Item '):
            continue
        text = normalized(' '.join(group))
        match = re.search(PENALTY_ENTRY, text)
        longest = max(map(len, group))
        out.append({'start': start, 'lines': len(group), 'longest': longest, 'entry': bool(match),
                    'site': match[1] if match else None, 'text': text if match else None,
                    'long': longest > 2000, 'beyond': start >= 2000, 'hidden': start >= 2000 or longest > 2000,
                    'hidden_by': lambda lines, width, start=start, longest=longest: start >= lines or longest > width})
    return out


def analyse(built):
    """A compact record of one dossier: its layout, its entries and what it holds."""
    source = sources(built)
    annex = source['annex.md']
    lines = annex.splitlines()
    rows = built['key']['expectations']
    claimed = {Reading.parse(r['claim_text'])[1] for r in rows}
    full, short = Reading(source).penalties, Reading(filtered(source)).penalties
    items = annex_items(annex)
    frame_ = [tuple(group) for _, group in annex_paragraphs(annex) if not group[0].startswith('Item ')]
    for item in items:
        item['claimed'] = item['site'] in claimed
    entries = [item for item in items if item['entry']]
    items = [{k: v for k, v in item.items() if k not in ('text', 'hidden_by')} for item in items]
    for entry in entries:
        entry.pop('hidden_by')
    over = tuple(sorted(len(line) for line in lines if len(line) > 2000))
    blocks = [lines[12 + 50 * i:12 + 50 * (i + 1)] for i in range(56)]
    nonblank = lambda block: sum(bool(line.strip()) for line in block)
    names = site_names(source)
    meta = {'annex lines': len(lines), 'annex bytes': len(annex.encode()),
            'annex blank lines': sum(not line.strip() for line in lines), 'annex items': len(items),
            'annex over-width lines': len(over), 'annex shortest over-width line': min(over, default=0),
            'annex longest over-width line': max(over, default=0),
            'annex mean over-width line': sum(over) / len(over) if over else 0,
            'annex mean item lines': sum(i['lines'] for i in items if not i['long']) / sum(not i['long'] for i in items),
            'entries in view': len(short), 'entries beyond view': len(full) - len(short),
            'entries in all': len(full), 'sites without entry': len(names) - len(full),
            'hidden claimed entries': len((set(full) - set(short)) & claimed),
            'lines beyond view': len(lines) - 2000, 'beyond-view over-width lines': sum(
                1 for n, line in enumerate(lines) if len(line) > 2000 and n >= 2000)}
    for name, text in sorted(source.items()):
        meta['bytes of ' + name] = len(text.encode())
        meta['lines of ' + name] = len(text.splitlines())
    return {'items': items, 'entries': entries, 'meta': meta, 'over': over, 'frame': tuple(frame_),
            'blocks50': (nonblank(blocks[50]), nonblank(blocks[53])), 'names': names,
            'lure': any(r['echo_state'] is not None for r in rows), 'full': full, 'short': short}


def report_features(report):
    """All rendered text, including title, preamble, citations and every claim."""
    tokens = re.findall(r'[a-z]+|[0-9]+', report.lower())
    bag = collections.Counter(tokens)
    claims = texts(report)
    parsed = [Reading.parse(text) for text in claims]
    features = {'whole report': report, 'title': report.splitlines()[0],
                'preamble': report.split('<a id=', 1)[0], 'report bytes': len(report.encode()),
                'citations': tuple(re.findall(r'Citations: [^\n]+', report)), 'claim-free frame': frame(report),
                'report words': len(tokens), 'positive claims': sum(p[2] for p in parsed),
                'site order': tuple(p[1] for p in parsed),
                'polarity order': tuple(p[2] for p in parsed),
                'amount total': sum(p[3] or 0 for p in parsed)}
    # All words are also consumed by a multivariate Bernoulli bag-of-words model.
    for word in ('no', 'not', 'sets', 'contains', 'penalty', 'entry', 'eur'):
        features['word/' + word] = bag[word]
    return features, frozenset(tokens)


def report_numbers(report, names):
    """Numeric features of a whole report that do not depend on the polarity of its claims
    (each report holds three claims of each polarity and kind)."""
    parsed = [Reading.parse(text) for text in texts(report)]
    rank = {name: i for i, name in enumerate(sorted(names))}
    amounts = [p[3] for p in parsed if p[3]]
    return {'amount total': sum(amounts), 'amount maximum': max(amounts), 'amount minimum': min(amounts),
            'amount repeats': len(amounts) - len(set(amounts)),
            'mean site rank': sum(rank[p[1]] for p in parsed) / 12,
            'first site rank': rank[parsed[0][1]], 'last site rank': rank[parsed[-1][1]],
            'sum of site name lengths': sum(len(p[1]) for p in parsed),
            'alphabetical neighbours': sum(a[1] < b[1] for a, b in zip(parsed, parsed[1:])),
            'first positive claim': next(i for i, p in enumerate(parsed) if p[2]),
            'report bytes': len(report.encode()), 'report words': len(report.split())}


@functools.lru_cache(maxsize=None)
def table():
    """Seeds 1..TEST_SEEDS reduced to compact records; the annex of each is read once."""
    out = {}
    for seed in range(1, TEST_SEEDS + 1):
        built = case(seed)
        record = analyse(built)
        names = record['names']
        rank = {name: i for i, name in enumerate(sorted(names))}
        metadata = {k: v for k, v in record['meta'].items() if k != 'hidden claimed entries'}
        reports, claims = {}, {}
        for variant in ('correct', 'mixed'):
            report = built['files']['report-' + variant + '.md']
            reports[variant] = report_features(report) + (report_numbers(report, names),)
            rows = {r['claim_text']: r for r in built['key']['expectations'] if r['variant'] == variant}
            claims[variant] = []
            for position, text in enumerate(texts(report)):
                kind, site, positive, amount = Reading.parse(text)
                row = rows[text]
                claims[variant].append({
                    'kind': kind, 'site': site, 'positive': positive, 'amount': amount or 0, 'position': position,
                    'shape': shape(text), 'claim_bytes': len(text.encode()), 'site rank': rank[site],
                    'site length': len(site), 'entry in view': site in record['short'],
                    'wrong': row['semantic_state'] == 'refuted', 'lure': row['echo_state'] is not None,
                    'direction': (row['semantic_state'], row['echo_state'])})
        record.update(metadata=metadata, reports=reports, claims=claims)
        out[seed] = record
    return out


def polarity_auc():
    """AUC of the polarity of a claim (positive or not) for being a lure, pooled and by report."""
    counts = collections.defaultdict(collections.Counter)
    for record in table().values():
        for variant, claims in record['claims'].items():
            for claim in claims:
                counts[variant][claim['positive'], claim['lure']] += 1
    def one(c):
        a = [True] * c[True, True] + [False] * c[False, True]
        b = [True] * c[True, False] + [False] * c[False, False]
        return auc(a, b)
    pooled = collections.Counter()
    for variant in counts:
        pooled.update(counts[variant])
    return one(pooled), {variant: one(counts[variant]) for variant in counts}


@functools.lru_cache(maxsize=None)
def reviewer_rule():
    """The reviewer's rule: constants (commonest over-width line length of clean dossiers, commonest
    non-blank counts of two fifty-line blocks) from seeds 1..500, scored on seeds 501..1000."""
    t = table()
    clean = [t[s] for s in range(1, 501) if not t[s]['lure']]
    modal_long = collections.Counter(n for r in clean for n in r['over']).most_common(1)[0][0]
    modal_blocks = collections.Counter(r['blocks50'] for r in clean).most_common(1)[0][0]
    correct = sum((any(n != modal_long for n in t[s]['over']) or t[s]['blocks50'] != modal_blocks) == t[s]['lure']
                  for s in range(501, 1001))
    share = sum(t[s]['lure'] for s in range(501, 1001)) / 500
    return modal_long, modal_blocks, correct / 500, max(share, 1 - share)


CLAIM_FEATURES = ('position', 'amount', 'site rank', 'site length', 'claim_bytes', 'thousands', 'high', 'slot')


@functools.lru_cache(maxsize=None)
def claim_strata():
    """Claims of seeds 1..TEST_SEEDS grouped by report, kind and polarity, with derived amount features."""
    strata = collections.defaultdict(list)
    for record in table().values():
        for variant, claims in record['claims'].items():
            for claim in claims:
                strata[variant, claim['kind'], claim['positive']].append(dict(
                    claim, thousands=claim['amount'] // 1000, hundreds=claim['amount'] // 100 % 10,
                    high=claim['amount'] >= 6000, slot=claim['position'] % 6))
    return strata


def stratum_deviations():
    """Largest |AUC - 1/2| over the claim features, per stratum with at least 30 lures: (deviation, lures)."""
    out = {}
    for key, rows in sorted(claim_strata().items()):
        lures = [c for c in rows if c['lure']]
        others = [c for c in rows if not c['lure']]
        if len(lures) >= 30:
            out[key] = (max(abs(auc([c[f] for c in lures], [c[f] for c in others]) - .5) for f in CLAIM_FEATURES
                            if len({c[f] for c in rows}) > 1), len(lures))
    return out


@functools.lru_cache(maxsize=None)
def r9_measurement():
    """Echo rows per twelve dossiers over seeds 1..100, streamed once for several tests."""
    counts, directions, variants = [], collections.Counter(), collections.Counter()
    per_dossier, absence = collections.Counter(), []
    for seed in range(1, 101):
        count = absent = 0
        for index in range(1, 13):
            rows = [r for r in case(seed, index)['key']['expectations'] if r['echo_state'] is not None]
            per_dossier[len(rows)] += 1
            for row in rows:
                count += 1
                directions[row['semantic_state'], row['echo_state']] += 1
                variants[row['variant']] += 1
                absent += row['semantic_state'] == 'refuted'
        counts.append(count)
        absence.append(absent)
    return (tuple(counts), tuple(sorted(directions.items())), tuple(sorted(variants.items())),
            tuple(sorted(per_dossier.items())), tuple(absence))


def guessers(train, test):
    """Train-only majority categorical, numeric decision stump and bag-of-words."""
    default = sum(y for _, _, y in train) > len(train) / 2
    scores = {}
    for key in train[0][0]:
        groups = collections.defaultdict(collections.Counter)
        for x, _, y in train:
            groups[x[key]][y] += 1
        pred = {value: count[True] > count[False] for value, count in groups.items()}
        scores[key + '/categorical'] = sum(pred.get(x[key], default) == y for x, _, y in test) / len(test)
        if not isinstance(train[0][0][key], (int, float)):
            continue
        total = sum(y for _, _, y in train)
        best = (max(total, len(train) - total), None, default, default)
        left_n = left_y = 0
        for value in sorted(groups):
            left_y += groups[value][True]
            left_n += sum(groups[value].values())
            right_y, right_n = total - left_y, len(train) - left_n
            fit = max(left_y, left_n - left_y) + max(right_y, right_n - right_y)
            if fit > best[0]:
                best = fit, value, left_y > left_n / 2, right_y > right_n / 2
        _, boundary, below, above = best
        scores[key + '/threshold'] = sum(
            (below if boundary is None or x[key] <= boundary else above) == y
            for x, _, y in test) / len(test)
    counts = [collections.Counter(), collections.Counter()]
    sizes = [0, 0]
    for _, bag, label in train:
        counts[label].update(bag)
        sizes[label] += 1
    vocab = set(counts[0]) | set(counts[1])
    bias = math.log((sizes[1] + 1) / (sizes[0] + 1))
    weights = {}
    for word in vocab:
        p0, p1 = [(counts[i][word] + 1) / (sizes[i] + 2) for i in (0, 1)]
        bias += math.log((1 - p1) / (1 - p0))
        weights[word] = math.log(p1 / p0) - math.log((1 - p1) / (1 - p0))
    scores['all report words/BernoulliNB'] = sum(
        (bias + sum(weights.get(word, 0) for word in bag) > 0) == y for _, bag, y in test) / len(test)
    return scores


class TruncatedTests(unittest.TestCase):
    def test_independent_keys_and_echo_including_world_counts(self):
        for seed in range(1, 61):
            built = case(seed)
            full, short = Reading(sources(built)), Reading(filtered(sources(built)))
            for variant in ('correct', 'mixed'):
                self.assertEqual(texts(built['files']['report-' + variant + '.md']),
                                 [r['claim_text'] for r in built['key']['expectations'] if r['variant'] == variant])
            for row in built['key']['expectations']:
                expected, echo = full.verdict(row['claim_text']), short.verdict(row['claim_text'])
                self.assertEqual(row['semantic_state'], expected)
                self.assertEqual(row['echo_state'], echo if echo != expected else None)
                self.assertEqual(row['derivation']['worlds'], 1)
                self.assertEqual(row['derivation']['worlds_where_true'], int(expected == 'supported'))
                if echo != expected:
                    self.assertEqual(row['echo_derivation'], {'worlds': 1, 'worlds_where_true': int(echo == 'supported')})

    def test_independent_reading_moves_when_rendered_penalty_changes(self):
        source = sources(case(1))
        reading = Reading(source)
        site, amount = next((s, a) for s, a in reading.penalties.items())
        claim = 'The annex sets the termination penalty at EUR {} for site {}.'.format(amount, site)
        changed = dict(source)
        # Match wrapped printer text, preserving every other source byte.
        pattern = r'(For\s+site\s+' + site + r',\s+the\s+agreed\s+termination\s+penalty\s+is\s+EUR\s+)' + str(amount) + r'\.'
        changed['annex.md'], n = re.subn(pattern, lambda m: m[1] + str(amount + 100) + '.', source['annex.md'])
        self.assertEqual(n, 1)
        self.assertEqual(reading.verdict(claim), 'supported')
        self.assertEqual(Reading(changed).verdict(claim), 'refuted')

    def test_all_slot_variants_are_mutable_subject_swaps_with_balanced_shapes(self):
        for seed in range(1, 61):
            built = raw(seed)
            full, short = Reading(built['sources']), Reading(filtered(built['sources']))
            shapes = collections.Counter()
            self.assertEqual(len(built['slots']), 12)
            self.assertEqual(len({Reading.parse(t['text'])[1] for t, _ in built['slots']}), 12)
            self.assertEqual(collections.Counter(t['kind'] for t, _ in built['slots']),
                             {'penalty_presence': 6, 'penalty_amount': 6})
            for true, wrongs in built['slots']:
                for item in [true] + wrongs:
                    self.assertEqual(generate.judge(item, built['base'], built['unknowns'])[0],
                                     full.verdict(item['text']))
                    told = built['told'] or {'base': built['base'], 'unknowns': built['unknowns']}
                    self.assertEqual(generate.judge(item, told['base'], told['unknowns'])[0],
                                     short.verdict(item['text']))
                self.assertEqual(full.verdict(true['text']), 'supported')
                self.assertEqual(len(wrongs), 1, 'Every slot must be mutable; no identifying immutable controls')
                wrong = wrongs[0]
                a, b = Reading.parse(true['text']), Reading.parse(wrong['text'])
                shapes[a[0], a[2]] += 1
                self.assertEqual(a[0:1] + a[2:], b[0:1] + b[2:])
                self.assertNotEqual(a[1], b[1])
                self.assertEqual(full.verdict(wrong['text']), 'refuted')
                expected_error = ('echoes_truncated_view' if short.verdict(wrong['text']) == 'supported' else
                                  'attributes_clause_to_other_site' if b[2] else 'overlooks_site_clause')
                self.assertEqual(wrong['error'], expected_error)
            self.assertEqual(shapes, {(kind, polarity): 3 for kind in ('penalty_presence', 'penalty_amount')
                                      for polarity in (False, True)})

    def test_told_is_none_exactly_when_no_claimed_entry_is_hidden(self):
        """Decoy entries (for sites no claim names) are hidden in every dossier and must not make `told` non-empty."""
        for seed in range(1, 101):
            built = raw(seed)
            names = {Reading.parse(t['text'])[1] for t, w in built['slots']} | \
                {Reading.parse(w[0]['text'])[1] for t, w in built['slots']}
            full, short = Reading(built['sources']).penalties, Reading(filtered(built['sources'])).penalties
            hidden_claimed = (set(full) - set(short)) & names
            self.assertGreaterEqual(len(set(full) - set(short)), 2)
            self.assertEqual(built['told'] is None, not hidden_claimed)
            self.assertEqual(set(built['base']['penalties']), set(full) & names)

    def test_correct_supported_and_mixed_errors_are_exact_subject_swaps(self):
        for seed in range(1, 61):
            rows = case(seed)['key']['expectations']
            correct = {r['claim_id']: r for r in rows if r['variant'] == 'correct'}
            changed = 0
            for row in rows:
                if row['variant'] == 'correct':
                    self.assertEqual(row['semantic_state'], 'supported')
                    self.assertIsNone(row['error_type'])
                elif row['claim_text'] != correct[row['claim_id']]['claim_text']:
                    changed += 1
                    a, b = Reading.parse(row['claim_text']), Reading.parse(correct[row['claim_id']]['claim_text'])
                    self.assertEqual(a[0:1] + a[2:], b[0:1] + b[2:])
                    self.assertNotEqual(a[1], b[1])
                    self.assertEqual(row['semantic_state'], 'refuted')
                    self.assertIn(row['error_type'], ('echoes_truncated_view', 'attributes_clause_to_other_site',
                                                      'overlooks_site_clause'))
                else:
                    self.assertEqual(row['semantic_state'], 'supported')
            self.assertGreaterEqual(changed, 4)
            self.assertLessEqual(changed, 6)

    def test_determinism_and_global_rng_untouched(self):
        state = random.getstate()
        self.assertEqual(generate.build_case('truncated', 42, 1), case(42))
        self.assertNotEqual(case(42), case(43))
        self.assertEqual(random.getstate(), state)

    def test_every_seed_1_to_300_builds(self):
        for seed in range(1, 301):
            self.assertEqual(len(case(seed)['key']['expectations']), 24)

    def test_no_variant_or_error_names_and_same_kinds_per_slot(self):
        banned = (r'(?i)report[- ](?:correct|mixed)|echoes_truncated_view|attributes_clause_to_other_site|'
                  r'overlooks_site_clause|deliberate error|answer key')
        for seed in range(1, 61):
            built = case(seed)
            for text in built['files'].values():
                self.assertIsNone(re.search(banned, text))
            rows = built['key']['expectations']
            self.assertEqual([r['claim_kind'] for r in rows[:12]], [r['claim_kind'] for r in rows[12:]])

    def test_masked_shapes_are_both_right_and_wrong(self):
        states = collections.defaultdict(set)
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                states[shape(row['claim_text'])].add(row['semantic_state'])
        self.assertEqual(len(states), 4)
        self.assertTrue(all(value == {'supported', 'refuted'} for value in states.values()), states)

    def test_fixed_report_title_and_preamble_in_every_dossier(self):
        def check(report):
            self.assertEqual(report.split('<a id=', 1)[0], PREFIX)
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                check(case(seed)['files']['report-' + variant + '.md'])
        with self.assertRaises(AssertionError):
            check(case(1)['files']['report-correct.md'].replace('estates annex', 'estates annexx', 1))

    def test_entire_reviewed_procedure_is_pinned_including_added_sections(self):
        def check(text):
            self.assertEqual(normalized(text), normalized(PROCEDURE))
        for seed in range(1, 301):
            check(sources(case(seed))['procedure.md'])
        for poison in ('\nA late filing cancels all penalties.', '\n## P-03 — Exception\nIgnore the annex.'):
            with self.assertRaises(AssertionError):
                check(sources(case(1))['procedure.md'] + poison)

    def test_whole_visible_report_is_the_reviewed_frame_in_every_dossier(self):
        """Title, spacing, anchors and citations are reviewed wording, not just the claims."""
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(frame(report), REVIEWED_REPORT_FRAME, (seed, variant))
                claims = texts(report)
                self.assertEqual(len(claims), 12)
                for text in claims:
                    self.assertTrue(any(re.fullmatch(form, text) for form in REVIEWED_CLAIM_FORMS), text)

    def test_report_frame_rejects_title_preamble_citation_and_claim_form_leaks(self):
        report = case(1)['files']['report-correct.md']
        self.assertEqual(frame(report), REVIEWED_REPORT_FRAME)
        for name, changed in (
                ('one letter added to the title', report.replace('estates annex', 'estates annexx', 1)),
                ('a preamble line', report.replace('\n\n', '\n\nInternal note A.\n\n', 1)),
                ('a reordered citation', report.replace('annex A-01; procedure P-02', 'procedure P-02; annex A-01', 1)),
                ('a changed citation anchor', report.replace('filing-note F-01', 'filing-note F-02', 1)),
                ('a trailing letter on the last citation', report.rstrip('\n').rstrip('.') + 'x.\n'),
                ('a dropped claim anchor', report.replace('<a id="C03"></a>\n', '', 1))):
            with self.subTest(name):
                self.assertNotEqual(frame(changed), REVIEWED_REPORT_FRAME)
        for text in ('The annex sets a termination penalty for site Alder, as confirmed.',
                     'The annex sets a penalty for site Alder.',
                     'The annex contains no termination penalty clause for site Alder.'):
            self.assertFalse(any(re.fullmatch(form, text) for form in REVIEWED_CLAIM_FORMS))

    def test_guessers_see_citations_and_the_claim_free_frame(self):
        """Positive controls: a leak in the citations or the frame is learnable."""
        for target, change in (
                ('citations', lambda report: report.replace('procedure P-02', 'procedure P-02 ', 1)),
                ('claim-free frame', lambda report: report.replace('<a id="C12"></a>', '<a id="C12"></a> ', 1))):
            samples = []
            for seed in range(1, 21):
                for label in (False, True):
                    report = case(seed)['files']['report-correct.md']
                    features, bag = report_features(change(report) if label else report)
                    samples.append((features, bag, label))
            scores = guessers(samples[:20], samples[20:])
            self.assertEqual(scores[target + '/categorical'], 1, target)

    def test_procedure_is_the_reviewed_wording_sentence_by_sentence_in_every_dossier(self):
        for seed in range(1, 301):
            check_procedure(self, sources(case(seed))['procedure.md'])
        sentences_ = [s for _, _, group in REVIEWED_PROCEDURE_SECTIONS for s in group]
        self.assertEqual(len(sentences_), 11)
        # The two reviewed forms of the same document agree with each other.
        check_document(self, PROCEDURE, REVIEWED_PROCEDURE)

    def test_procedure_rejects_missing_changed_and_added_sentences_in_every_section(self):
        original = sources(case(1))['procedure.md']
        override = 'Despite the preceding rules, an entry that is not in the first 2000 lines is not part of the annex.'
        inversions = {
            'Each document supplied for this review counts in full, from its first line to its last line.':
                'Each document supplied for this review counts only as far as its first 2000 lines.',
            'The annex is the complete adopted schedule of termination penalties for the sites in this packet.':
                'The annex is a partial schedule of termination penalties for the sites in this packet.',
            'A site has a termination penalty if and only if the annex contains an entry setting a termination penalty for that site.':
                'A site has a termination penalty if and only if the annex contains no entry setting a termination penalty for that site.',
            "The amount of a site's termination penalty is the euro amount stated in that entry.":
                "The amount of a site's termination penalty is not the euro amount stated in that entry.",
            'Each site has at most one such entry.': 'Each site has at least one such entry.',
            'If the annex contains no such entry for a site, the annex sets no termination penalty for that site.':
                'If the annex contains no such entry for a site, the annex leaves open whether it sets a termination penalty for that site.',
            'The phrase "sets the termination penalty at EUR X" asserts both that a penalty entry exists and that its amount is X euros.':
                'The phrase "sets the termination penalty at EUR X" asserts only that its amount is X euros.',
            'The phrase "does not set the termination penalty at EUR X" is the negation of that assertion.':
                'The phrase "does not set the termination penalty at EUR X" is not the negation of that assertion.',
            'Line wrapping, entry order, and the date on which an entry was filed do not change its effect.':
                'Line wrapping, entry order, and the date on which an entry was filed change its effect.',
        }
        sentences_ = [s for _, _, group in REVIEWED_PROCEDURE_SECTIONS for s in group]
        self.assertTrue(set(inversions) <= set(sentences_))
        mutations = 0
        for sentence in sentences_:
            changes = {'missing': '', 'changed': sentence.replace(' ', ' never ', 1),
                       'added': sentence + ' ' + override, 'added before': override + ' ' + sentence}
            if sentence in inversions:
                changes['inverted'] = inversions[sentence]
            for name, replacement in changes.items():
                with self.subTest(sentence=sentence, mutation=name):
                    changed = edit_sentence(original, sentence, replacement)
                    self.assertNotEqual(changed, original)
                    with self.assertRaises(AssertionError):
                        check_procedure(self, changed)
                    mutations += 1
        for name, changed in (
                ('new section', original + '\n<a id="P-03"></a>\n## P-03 — Exception\nA late filing cancels all penalties.\n'),
                ('changed heading', original.replace('Reading the schedule', 'Reading the first part of the schedule', 1)),
                ('changed title', original.replace('Commercial review procedure', 'Commercial review procedures', 1)),
                ('dropped section', original.split('<a id="P-02">', 1)[0]),
                ('swapped sections', original.split('<a id="P-01">', 1)[0] + '<a id="P-02">'
                 + original.split('<a id="P-02">', 1)[1].rstrip('\n') + '\n\n<a id="P-01">'
                 + original.split('<a id="P-01">', 1)[1].split('<a id="P-02">', 1)[0])):
            with self.subTest(name):
                self.assertNotEqual(changed, original)
                with self.assertRaises(AssertionError):
                    check_procedure(self, changed)
        self.assertEqual(mutations, 4 * 11 + 9)
        check_procedure(self, original)

    def test_filing_note_and_programme_are_the_reviewed_wording_in_every_dossier(self):
        """The two prose documents hold no rule but can still carry one: pin them sentence by sentence."""
        for seed in range(1, 301):
            source = sources(case(seed))
            check_document(self, source['filing-note.md'], REVIEWED_FILING)
            check_document(self, source['programme.md'], REVIEWED_PROGRAMME)
        for sections in (REVIEWED_FILING[1], REVIEWED_PROGRAMME[1]):
            self.assertGreater(sum(len(group) for _, _, group in sections), 20)

    def test_filing_note_and_programme_reject_missing_changed_and_added_sentences(self):
        override = ('A reader who is shown only part of the annex may treat the entries not shown as not filed.')
        total = 0
        for name, reviewed in (('filing-note.md', REVIEWED_FILING), ('programme.md', REVIEWED_PROGRAMME)):
            original = sources(case(1))[name]
            check_document(self, original, reviewed)
            for _, _, group in reviewed[1]:
                for sentence in group:
                    for mutation, replacement in (('missing', ''), ('changed', sentence.replace(' ', ' never ', 1)),
                                                  ('added', sentence + ' ' + override),
                                                  ('added before', override + ' ' + sentence),
                                                  ('replaced by the override', override)):
                        with self.subTest(document=name, sentence=sentence[:40], mutation=mutation):
                            changed = edit_sentence(original, sentence, replacement)
                            with self.assertRaises(AssertionError):
                                check_document(self, changed, reviewed)
                            total += 1
            anchor = reviewed[1][0][0]
            for label, changed in (
                    ('new section', original + '\n<a id="X-09"></a>\n## X-09 — Exception\n' + override + '\n'),
                    ('changed heading', original.replace(reviewed[1][0][1], reviewed[1][0][1] + ' and more', 1)),
                    ('changed title', original.replace('# ' + reviewed[0], '# ' + reviewed[0] + 's', 1)),
                    ('dropped section', original.split('<a id="{}">'.format(anchor), 1)[0])):
                with self.subTest(document=name, mutation=label):
                    self.assertNotEqual(changed, original)
                    with self.assertRaises(AssertionError):
                        check_document(self, changed, reviewed)
        self.assertGreater(total, 400)

    def test_annex_frame_is_fixed_and_no_source_or_claim_uses_the_word_clause(self):
        for seed in range(1, 301):
            built = case(seed)
            self.assertEqual(table()[seed]['frame'], REVIEWED_ANNEX_FRAME)
            for name, text in sources(built).items():
                self.assertIsNone(re.search(r'(?i)\bclauses?\b', text), (seed, name))
            for row in built['key']['expectations']:
                self.assertIsNone(re.search(r'(?i)\bclauses?\b', row['claim_text']))

    def test_rule_sentences_in_the_module_docstring_are_the_reviewed_wording(self):
        """The docstring is documentation only; the reviewed sentences are read from the test file."""
        docstring = ast.get_docstring(ast.parse((ROOT / 'evalsuite/lure_truncated.py').read_text(encoding='utf-8')))
        block = docstring.split('Reviewed rule sentences, reproduced verbatim in procedure.md:\n', 1)[1].split('\n\n', 1)[0]
        self.assertEqual(block.splitlines(), list(REVIEWED_PROCEDURE_SECTIONS[1][2]))

    def test_every_echo_row_names_the_flawed_reader(self):
        echoes = 0
        for seed in range(1, 301):
            built = case(seed)
            for row in built['key']['expectations']:
                if row['echo_state'] is not None:
                    echoes += 1
                    self.assertEqual(row.get('echo_reader'), READER, (seed, row['claim_id']))
                else:
                    self.assertNotIn('echo_reader', row)
        self.assertGreater(echoes, 300)
        self.assertEqual(generate.family_builder('truncated')(random.Random(5))['reader'], READER)

    def test_controls_match_kind_and_shape_and_both_echo_directions(self):
        directions = set()
        for seed in range(1, 301):
            rows = case(seed)['key']['expectations']
            for variant in ('correct', 'mixed'):
                chosen = [r for r in rows if r['variant'] == variant]
                for row in chosen:
                    if row['echo_state'] is not None:
                        directions.add((row['semantic_state'], row['echo_state']))
                        self.assertTrue(any(other['echo_state'] is None and other['claim_kind'] == row['claim_kind']
                                            and shape(other['claim_text']) == shape(row['claim_text']) for other in chosen))
        self.assertEqual(directions, {('supported', 'refuted'), ('refuted', 'supported')})

    def test_clean_fraction_and_one_or_two_distinct_hidden_claimed_sites(self):
        counts, hidden_all = collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            counts[table()[seed]['meta']['hidden claimed entries']] += 1
            hidden_all[table()[seed]['meta']['entries beyond view']] += 1
        self.assertEqual(set(counts), {0, 1, 2})
        self.assertGreaterEqual(counts[0] / 300, .25)
        self.assertLessEqual(counts[0] / 300, .40)
        # Entries for sites no claim names are hidden too, so that something is hidden in every dossier.
        self.assertEqual(set(hidden_all), {2, 3, 4})
        print('Truncated hidden-site counts:', dict(counts), 'hidden entries of any site:', dict(hidden_all))

    def test_layout_bands_and_minimum_substantive_words(self):
        """The annex is long enough for the line limit to bite, and its size is a band, not a constant:
        sizes vary with the draw but not with the lure (see the layout comparison tests)."""
        for seed in range(1, 301):
            source = sources(case(seed))
            lines = len(source['annex.md'].splitlines())
            self.assertGreaterEqual(lines, 2300)
            self.assertLessEqual(lines, 3200)
            self.assertGreaterEqual(lines, 2000 + 300)
            self.assertGreaterEqual(sum(len(t.split()) for n, t in source.items() if n != 'annex.md'), 1500)
            self.assertEqual(sorted(source), ['annex.md', 'filing-note.md', 'procedure.md', 'programme.md'])
        sizes = {table()[s]['meta']['annex bytes'] for s in range(1, 301)}
        self.assertGreater(len(sizes), 250)

    def test_both_physical_omission_modes_wholly_inside_or_beyond_the_view(self):
        modes, kinds = collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            source = sources(case(seed))
            full = Reading(source).penalties
            prefix = Reading(filtered(source, 2000, 10 ** 9)).penalties
            short = Reading(filtered(source)).penalties
            for site in set(full) - set(short):
                modes['overwidth within prefix' if site in prefix else 'beyond line cap'] += 1
            self.assertGreaterEqual(len(short), 12, 'Every dossier needs shown wrapped entries')
            for entry in table()[seed]['entries']:
                if entry['entry'] and not entry['hidden']:
                    # A shown entry lies wholly inside the first 2000 lines and on short lines.
                    self.assertLessEqual(entry['start'] + entry['lines'], 2000)
                    self.assertLessEqual(entry['longest'], 76)
                    self.assertIn(entry['site'], short)
                else:
                    kinds['beyond the cap, wrapped' if entry['beyond'] and not entry['long'] else
                          'beyond the cap, one long line' if entry['beyond'] else 'inside the cap, one long line'] += 1
                    self.assertGreaterEqual(entry['start'], 0)
                    self.assertTrue(entry['beyond'] or entry['long'])
                    self.assertNotIn(entry['site'], short)
        self.assertEqual(set(modes), {'overwidth within prefix', 'beyond line cap'})
        self.assertEqual(len(kinds), 3)
        print('Truncated physical omission modes:', dict(modes), dict(kinds))

    def test_view_ends_on_a_paragraph_boundary_and_the_returns_start_beyond_it(self):
        """A view that stops in the middle of a sentence would show that something was cut."""
        for seed in range(1, 301):
            lines = sources(case(seed))['annex.md'].splitlines()
            self.assertEqual(lines[1999], '')
            self.assertTrue(lines[1998] == '' or len(lines[1998]) <= 76)
            self.assertFalse(lines[2000].strip())
            self.assertEqual(lines[2001], 'Part H — Returns filed after circulation')

    def test_boundary_order_and_alternate_design_parameters(self):
        import lure_truncated as implementation
        old = implementation.VIEW_LINES, implementation.VIEW_WIDTH
        try:
            for (lines, width), seeds in (((2000, 2000), 60), ((1200, 1000), 60), ((800, 80), 40), ((2200, 3000), 40)):
                implementation.VIEW_LINES, implementation.VIEW_WIDTH = lines, width
                # The rejected first line must still consume its original position.
                physical = ['x' * (width + 1)] + ['x'] * (lines - 2) + ['y' * width, 'AFTER']
                text = '\n'.join(physical)
                self.assertEqual(implementation.view(text), filtered({'a': text}, lines, width)['a'])
                self.assertIn('y' * width, implementation.view(text))
                self.assertNotIn('AFTER', implementation.view(text))
                for seed in range(1, seeds + 1):
                    built = raw(seed)
                    full = Reading(built['sources'])
                    short = Reading(filtered(built['sources'], lines, width))
                    self.assertEqual(implementation.facts(built['sources']), full.penalties)
                    self.assertEqual(implementation.facts({n: implementation.view(t) for n, t in built['sources'].items()}),
                                     short.penalties)
                    # A reader that clips an over-long line sees none of an entry either.
                    self.assertEqual(Reading(clipped(built['sources'], lines, width)).penalties, short.penalties,
                                     (lines, width, seed))
                    annex = built['sources']['annex.md'].splitlines()
                    self.assertGreaterEqual(len(annex), lines + 400)
                    self.assertLessEqual(len(annex), lines + 1000)
                    self.assertEqual(annex[lines - 1], '')
                    self.assertTrue(any(len(line) > width for line in annex[:lines]))
                    for item in annex_items(built['sources']['annex.md']):
                        if item['entry'] and not item['hidden_by'](lines, width):
                            self.assertLessEqual(item['start'] + item['lines'], lines)
                    for true, wrongs in built['slots']:
                        self.assertEqual(full.verdict(true['text']), 'supported')
                        for wrong in wrongs:
                            self.assertEqual(full.verdict(wrong['text']), 'refuted')
                            self.assertEqual(wrong['error'] == 'echoes_truncated_view', short.verdict(wrong['text']) == 'supported')
        finally:
            implementation.VIEW_LINES, implementation.VIEW_WIDTH = old

    # ---------- the layout of the annex does not say whether a lure exists, or where the entries are ----------

    def test_line_lengths_and_paragraph_sizes_do_not_depend_on_the_lure_or_on_where_the_entries_are(self):
        """Per-line lengths and per-paragraph line counts, split by whether the dossier holds a lure, by whether
        a paragraph holds an entry and by whether it lies beyond the view."""
        long_lines = collections.defaultdict(list)
        wrapped = collections.defaultdict(list)
        for record in table().values():
            for item in record['items']:
                if item['long']:
                    long_lines['entry' if item['entry'] else 'filler'].append(item['longest'])
                    long_lines['beyond' if item['beyond'] else 'inside'].append(item['longest'])
                    long_lines['lure' if record['lure'] else 'clean'].append(item['longest'])
                    long_lines['lure entry' if record['lure'] and item['entry'] else 'clean entry' if item['entry']
                               else 'other'].append(item['longest'])
                else:
                    kind = 'entry' if item['entry'] else 'instruction'
                    wrapped[kind].append(item['lines'])
                    wrapped[kind + (' beyond' if item['beyond'] else ' inside')].append(item['lines'])
                    wrapped[kind + (' in lure' if record['lure'] else ' in clean')].append(item['lines'])
        results = {}
        for label, a, b in (
                ('over-width lines holding an entry against harmless ones', long_lines['entry'], long_lines['filler']),
                ('over-width lines beyond the view against inside it', long_lines['beyond'], long_lines['inside']),
                ('over-width lines of lure dossiers against clean ones', long_lines['lure'], long_lines['clean']),
                ('over-width entry lines of lure dossiers against clean ones', long_lines['lure entry'], long_lines['clean entry']),
                ('wrapped entries against wrapped instructions', wrapped['entry'], wrapped['instruction']),
                ('wrapped instructions beyond the view against inside it', wrapped['instruction beyond'], wrapped['instruction inside']),
                ('wrapped entries beyond the view against inside it', wrapped['entry beyond'], wrapped['entry inside']),
                ('wrapped instructions of lure dossiers against clean ones', wrapped['instruction in lure'], wrapped['instruction in clean']),
                ('wrapped entries of lure dossiers against clean ones', wrapped['entry in lure'], wrapped['entry in clean'])):
            results[label] = same_distribution(self, a, b, label)
        print('Truncated layout comparisons (KS distance, AUC):')
        for label, (distance, value) in results.items():
            print('  {}: {:.4f}, {:.4f}'.format(label, distance, value))
        # The detector sees a layout like the earlier one: entry lines of a fixed length, other long
        # lines of another, entry blocks with more lines than the others.
        with self.assertRaises(AssertionError):
            same_distribution(self, [2810 + n % 8 for n in range(300)], [2637] * 300, 'constant layout')
        with self.assertRaises(AssertionError):
            same_distribution(self, [4 + n % 3 for n in range(300)], [3 + n % 3 for n in range(300)], 'one more line')

    def test_layout_totals_do_not_depend_on_the_lure(self):
        names = ('annex lines', 'annex bytes', 'annex blank lines', 'annex items', 'annex over-width lines',
                 'annex shortest over-width line', 'annex longest over-width line', 'annex mean over-width line',
                 'annex mean item lines', 'lines beyond view', 'beyond-view over-width lines')
        for name in names:
            lure = [r['meta'][name] for r in table().values() if r['lure']]
            clean = [r['meta'][name] for r in table().values() if not r['lure']]
            same_distribution(self, lure, clean, name)
            self.assertGreater(len(set(lure + clean)), 5, name)

    def test_the_reviewers_constant_layout_rule_is_at_the_base_rate(self):
        """A rule built from constants alone (the commonest over-width line length of clean dossiers, and the
        commonest non-blank counts of two fifty-line blocks) identified lure dossiers in 500 of 500 before."""
        modal_long, modal_blocks, accuracy, baseline = reviewer_rule()
        print('Truncated reviewer rule: modal long line {}, modal blocks {}, accuracy {:.3f} against base rate {:.3f}'.format(
            modal_long, modal_blocks, accuracy, baseline))
        self.assertLessEqual(accuracy, baseline + margin(baseline, 500))
        # Positive control: the same rule on the layout it was found on.
        earlier = [{'over': (2637, 2637) if not lure else (2637, 2810 + n % 8), 'blocks50': (36, 36) if not lure else (38, 36),
                    'lure': lure} for n, lure in enumerate([False, True, True] * 200)]
        control = [r for r in earlier]
        modal_long = collections.Counter(n for r in control if not r['lure'] for n in r['over']).most_common(1)[0][0]
        found = sum((any(n != modal_long for n in r['over']) or r['blocks50'] != (36, 36)) == r['lure'] for r in control)
        self.assertEqual(found, len(control))

    def test_shown_and_hidden_entry_counts_do_not_depend_on_the_lure(self):
        """Counting entries in the view gave the lure away (twelve in every clean dossier, ten or eleven in the others)."""
        t = table()
        for name in ('entries in view', 'entries beyond view', 'entries in all', 'sites without entry'):
            lure = [r['meta'][name] for r in t.values() if r['lure']]
            clean = [r['meta'][name] for r in t.values() if not r['lure']]
            same_distribution(self, lure, clean, name)
        self.assertEqual({r['meta']['entries beyond view'] for r in t.values()}, {2, 3, 4})
        self.assertGreaterEqual(min(r['meta']['entries in view'] for r in t.values()), 12)
        self.assertGreaterEqual(len({r['meta']['entries in view'] for r in t.values()}), 5)
        self.assertGreaterEqual(min(r['meta']['sites without entry'] for r in t.values()), 8)
        self.assertGreaterEqual(len({r['meta']['sites without entry'] for r in t.values()}), 5)
        # Sites without any entry exist in every dossier: true absences are not only the sites whose
        # entry is hidden, and the sites of the claims are of both kinds.
        for seed in range(1, 301):
            record = t[seed]
            self.assertGreater(len(record['names']) - len(record['full']), 0)

    def test_every_site_is_named_in_a_document_that_is_always_in_view(self):
        """Each site was named once, in its annex entry, so that a name missing from the view marked a hidden entry."""
        for seed in range(1, 301):
            built = case(seed)
            source = sources(built)
            names = site_names(source)
            self.assertEqual(len(names), 32)
            self.assertEqual(names, sorted(names))
            visible = normalized(' '.join(filtered(source).values()))
            for row in built['key']['expectations']:
                site = Reading.parse(row['claim_text'])[1]
                self.assertIn(site, names)
                self.assertIsNotNone(re.search(r'\b' + site + r'\b', normalized(filtered(source)['programme.md'])))
            for name in names:
                self.assertIsNotNone(re.search(r'\b' + name + r'\b', visible))

    def test_what_remains_of_the_name_search_cue_is_the_stated_one(self):
        """Every lure is a claim about a site with no entry in the view; that is by construction. The share of such
        claims that are lures is measured and printed, for names missing from the annex view as well."""
        counts = collections.Counter()
        for record in table().values():
            for variant, claims in record['claims'].items():
                for claim in claims:
                    counts[claim['entry in view'], claim['positive'], variant, claim['lure']] += 1
        self.assertEqual(sum(n for (seen, _, _, lure), n in counts.items() if seen and lure), 0)
        for variant in ('correct', 'mixed'):
            for positive in (True, False):
                without = [counts[False, positive, variant, lure] for lure in (False, True)]
                print('Truncated claims with no entry in view, {} reports, {} claims: {} of {} are lures ({:.1f}%)'.format(
                    variant, 'positive' if positive else 'negative', without[1], sum(without),
                    100 * without[1] / sum(without)))
        total_without = sum(n for (seen, _, _, _), n in counts.items() if not seen)
        lures = sum(n for (_, _, _, lure), n in counts.items() if lure)
        share = lures / total_without
        print('Truncated lure share among claims with no entry in view: {:.1f}%; all claims {:.1f}%'.format(
            100 * share, 100 * lures / sum(counts.values())))
        self.assertLess(share, .35)
        self.assertGreater(total_without - lures, lures)

    def test_hidden_and_shown_entries_read_alike_after_masking_names_numbers_and_dates(self):
        """Compare entries beyond the view with entries inside it, sentence by sentence and by length."""
        names = re.compile(r'\b(?:' + '|'.join(table()[1]['names']) + r')\b')

        def mask(sentence):
            return re.sub(r'\d+', 'N', re.sub(r'\d+:\d+', 'TIME', names.sub('SITE', sentence)))
        # The sentences that make an entry: who filed it, the route, the penalty, its confirmation.
        core_words = ('contracts office', 'travel route', 'termination penalty', 'the entry', 'entry was')
        is_core = lambda sentence: any(word in sentence.lower() for word in core_words)
        classes = collections.defaultdict(list)
        for seed in range(1, 301):
            for entry in table()[seed]['entries']:
                core = [s for s in sentences(entry['text']) if is_core(s)]
                self.assertGreaterEqual(len(core), 3, entry['text'][:200])
                kind = ('shown' if not entry['hidden'] else 'beyond, wrapped' if entry['beyond'] and not entry['long']
                        else 'beyond, long line' if entry['beyond'] else 'inside, long line')
                record = {'sentences': [mask(s) for s in core], 'length': sum(len(s) for s in core), 'count': len(core)}
                labels = {kind, 'hidden' if entry['hidden'] else 'shown'}
                if entry['hidden']:
                    labels.add('claimed hidden' if entry['claimed'] else 'decoy hidden')
                for label in labels:
                    classes[label].append(record)
        self.assertEqual(set(classes), {'shown', 'hidden', 'beyond, wrapped', 'beyond, long line',
                                        'inside, long line', 'claimed hidden', 'decoy hidden'})
        shown = classes['shown']
        shown_set = {s for r in shown for s in r['sentences']}
        for label in ('hidden', 'beyond, wrapped', 'beyond, long line', 'inside, long line', 'claimed hidden', 'decoy hidden'):
            other = classes[label]
            seen = {s for r in other for s in r['sentences']}
            # No sentence of a hidden entry is missing from the shown ones (a different wording would show here);
            # the small classes need not use every variant, the pooled class does.
            self.assertLessEqual(seen, shown_set, label)
            if label == 'hidden':
                self.assertEqual(seen, shown_set)
            same_distribution(self, [r['length'] for r in other], [r['length'] for r in shown], label + ' length')
            same_distribution(self, [r['count'] for r in other], [r['count'] for r in shown], label + ' sentences')
            # Each sentence is used as often by hidden entries as by shown ones.
            for sentence in shown_set:
                p = sum(r['sentences'].count(sentence) for r in other) / len(other)
                q = sum(r['sentences'].count(sentence) for r in shown) / len(shown)
                pooled = (p * len(other) + q * len(shown)) / (len(other) + len(shown))
                spread = Z * math.sqrt(max(pooled * (1 - pooled), .01) * (1 / len(other) + 1 / len(shown)))
                self.assertLess(abs(p - q), spread, (label, sentence, p, q))
        print('Truncated entry sentences after masking: {} distinct, {} entries shown, {} hidden'.format(
            len(shown_set), len(shown), len(classes['hidden'])))
        # The check sees an entry that is worded differently.
        odd = dict(shown[0], sentences=shown[0]['sentences'][:-1] + ['Late return filed on N October N.'])
        self.assertNotEqual({s for r in classes['hidden'] + [odd] for s in r['sentences']}, shown_set)

    def test_no_paragraph_is_repeated_verbatim_more_than_a_few_times(self):
        worst = 0
        for seed in range(1, 61):
            counts = collections.Counter()
            for _, group in annex_paragraphs(sources(case(seed))['annex.md']):
                text = normalized(' '.join(group))
                if text.startswith('Item '):
                    counts[re.sub(r'^Item \d+\. ', '', text)] += 1
            worst = max(worst, max(counts.values()))
            self.assertGreater(len(counts), 400)
        self.assertLessEqual(worst, 3)
        print('Truncated most repeated paragraph in 60 annexes:', worst)

    def test_clipping_reader_would_miss_the_entries_too(self):
        """A tool that clips an over-long line instead of dropping it sees the same world: the entry in a long line
        starts beyond the width."""
        for seed in range(1, 301):
            source = sources(case(seed))
            self.assertEqual(Reading(clipped(source)).penalties, Reading(filtered(source)).penalties, seed)
        # Positive control: an entry at the start of a long line would be seen by that tool.
        text = sources(case(1))['annex.md']
        line = next(l for l in text.splitlines() if len(l) > 2000)
        moved = 'For site Zed, the agreed termination penalty is EUR 1200. ' + line
        self.assertIn('Zed', Reading({'annex.md': moved}).penalties)
        self.assertIn('Zed', Reading({'annex.md': '\n'.join(l[:2000] for l in moved.splitlines())}).penalties)
        tail = line + ' For site Zed, the agreed termination penalty is EUR 1200.'
        self.assertNotIn('Zed', Reading({'annex.md': '\n'.join(l[:2000] for l in tail.splitlines())}).penalties)

    def test_lures_occur_in_both_claim_kinds_and_in_both_directions(self):
        counts = collections.Counter()
        for record in table().values():
            for variant, claims in record['claims'].items():
                for claim in claims:
                    if claim['lure']:
                        counts[claim['kind'], claim['direction'], variant] += 1
        by_kind = {kind: sum(n for (k, _, _), n in counts.items() if k == kind) for kind in ('penalty_presence', 'penalty_amount')}
        total = sum(by_kind.values())
        print('Truncated lure rows by kind:', by_kind, 'by kind, direction and report:', dict(sorted(counts.items())))
        for kind, n in by_kind.items():
            self.assertGreater(n / total, .3, kind)
        for kind in by_kind:
            for direction in (('supported', 'refuted'), ('refuted', 'supported')):
                self.assertGreater(sum(n for (k, d, _), n in counts.items() if k == kind and d == direction), 20, (kind, direction))
        for kind in by_kind:
            for variant in ('correct', 'mixed'):
                self.assertGreater(sum(n for (k, _, v), n in counts.items() if k == kind and v == variant), 100, (kind, variant))

    # ---------- ranking measures: a partial cue is seen even when no value is mostly lures ----------

    def test_ranking_and_chi_square_find_no_partial_cue_in_claim_features(self):
        """Within strata of report, kind and polarity (so that polarity is not a feature), every claim feature
        ranks lures and other claims alike. Accuracy cannot see a cue like "hidden amounts are all 6,000 or more",
        because no feature value is then more than half lures."""
        strata = claim_strata()
        ranked = categorical = 0
        worst = 0
        for key, rows in sorted(strata.items()):
            lures = [c for c in rows if c['lure']]
            others = [c for c in rows if not c['lure']]
            if len(lures) < 30:
                self.assertEqual(key[:3:2], ('correct', False), key)  # only negative claims of correct reports hold none
                continue
            for feature in CLAIM_FEATURES:
                a, b = [c[feature] for c in lures], [c[feature] for c in others]
                if len(set(a + b)) == 1:
                    continue
                value, bound = auc(a, b), Z * null_se(len(a), len(b))
                worst = max(worst, abs(value - .5) / bound)
                self.assertLess(abs(value - .5), bound, (key, feature, value, bound))
                ranked += 1
            for feature in ('site', 'position', 'hundreds', 'thousands'):
                a, b = collections.Counter(c[feature] for c in lures), collections.Counter(c[feature] for c in others)
                if len(set(a) | set(b)) < 2:
                    continue
                z = chi_square_z(a, b)
                self.assertLess(z, Z, (key, feature, z))
                categorical += 1
        print('Truncated claim ranking: {} AUC checks and {} chi-square checks; largest AUC deviation {:.2f} of its bound'.format(
            ranked, categorical, worst))
        self.assertGreaterEqual(ranked, 12)
        self.assertGreaterEqual(categorical, 12)

    def test_ranking_finds_no_partial_cue_in_reports_the_annex_or_the_file_set(self):
        t = table()
        checked = 0
        for variant in ('correct', 'mixed'):
            for name in t[1]['reports'][variant][2]:
                a = [r['reports'][variant][2][name] for r in t.values() if r['lure']]
                b = [r['reports'][variant][2][name] for r in t.values() if not r['lure']]
                if len(set(a + b)) == 1:
                    continue
                value, bound = auc(a, b), Z * null_se(len(a), len(b))
                self.assertLess(abs(value - .5), bound, (variant, name, value, bound))
                checked += 1
        for name in t[1]['metadata']:
            a = [r['metadata'][name] for r in t.values() if r['lure']]
            b = [r['metadata'][name] for r in t.values() if not r['lure']]
            if len(set(a + b)) == 1:
                continue
            value, bound = auc(a, b), Z * null_se(len(a), len(b))
            self.assertLess(abs(value - .5), bound, (name, value, bound))
            checked += 1
        self.assertGreater(checked, 25)

    def test_the_ranking_check_sees_the_reviewers_partial_cue(self):
        """Positive control: a feature whose large values are 25% lures against none below is far outside the bound."""
        rng = random.Random(3)
        rows = [(rng.randrange(12, 99) * 100, False) for _ in range(3000)]
        rows += [(rng.randrange(60, 99) * 100, True) for _ in range(300)]
        a, b = [v for v, lure in rows if lure], [v for v, lure in rows if not lure]
        self.assertGreater(abs(auc(a, b) - .5), 3 * Z * null_se(len(a), len(b)))
        high = [lure for v, lure in rows if v >= 6000]
        self.assertLess(sum(high) / len(high), .5)   # accuracy cannot see it
        self.assertGreater(chi_square_z(collections.Counter(v // 1000 for v, lure in rows if lure),
                                        collections.Counter(v // 1000 for v, lure in rows if not lure)), Z)

    def test_r9_streaming_100_seeds_times_12_indices(self):
        counts, pairs, _, _, _ = r9_measurement()
        directions = dict(pairs)
        print('Truncated R9 min/median/max={} total={} directions={} unknown={}'.format(
            (min(counts), statistics.median(counts), max(counts)), sum(counts), directions,
            sum(n for (_, echo), n in directions.items() if echo == 'undetermined')))
        self.assertGreaterEqual(statistics.median(counts), 15)
        self.assertEqual(set(directions), {('supported', 'refuted'), ('refuted', 'supported')})

    def test_module_docstring_states_the_numbers_measured_on_this_code(self):
        """R3, R9, the polarity cue and the remaining cues in the docstring are the numbers this run measures."""
        docstring = ast.get_docstring(ast.parse((ROOT / 'evalsuite/lure_truncated.py').read_text(encoding='utf-8')))
        text = ' '.join(docstring.split())
        counts, pairs, variants, per_dossier, absence = r9_measurement()
        directions, variants = dict(pairs), dict(variants)
        total = sum(counts)
        self.assertEqual(total, sum(directions.values()))
        median = statistics.median(counts)
        self.assertIn('minimum {}, median {:g}, maximum {} ({:,} rows in all: {:,} in correct reports and {:,} in altered '
                      'reports)'.format(min(counts), median, max(counts), total, variants['correct'], variants['mixed']), text)
        self.assertIn('All {:,} echo rows are definite ({:,} supported-to-refuted and {:,} refuted-to-supported); '
                      'zero are undetermined'.format(total, directions['supported', 'refuted'],
                                                    directions['refuted', 'supported']), text)
        self.assertEqual(set(directions), {('supported', 'refuted'), ('refuted', 'supported')})
        self.assertEqual(max(n for n, _ in per_dossier), 4)
        self.assertIn('no dossier holds more than four echo rows, {} of the 100 twelve-dossier suites (seeds 1..100) '
                      'hold fewer than 15 echo rows'.format(sum(n < 15 for n in counts)), text)
        self.assertIn('which holds on average {:.0f} (correct) or {:.0f} (altered) of the {:.0f} per twelve dossiers'.format(
            variants['correct'] / 100, variants['mixed'] / 100, total / 100), text)
        self.assertIn('{} of the 100 suites hold no such row, and the others hold {} to {}'.format(
            sum(n == 0 for n in absence), min(n for n in absence if n), max(absence)), text)
        t = table()
        hidden = collections.Counter(t[s]['meta']['hidden claimed entries'] for s in range(1, 301))
        polarity = collections.defaultdict(collections.Counter)
        for seed in range(1, 301):
            for variant, claims in t[seed]['claims'].items():
                for claim in claims:
                    polarity[variant, claim['positive']]['total'] += 1
                    polarity[variant, claim['positive']]['lure'] += claim['lure']
        self.assertIn('{}/300 dossiers ({:.1f}%) have no lure, {} hide one claimed entry, and {} hide two'.format(
            hidden[0], 100 * hidden[0] / 300, hidden[1], hidden[2]), text)
        self.assertEqual(set(hidden), {0, 1, 2})

        def rate(variant, positive):
            counted = polarity[variant, positive]
            percent = '0' if not counted['lure'] else '{:.2f}'.format(100 * counted['lure'] / counted['total'])
            return '{}/{:,}'.format(counted['lure'], counted['total']), '({}%)'.format(percent)

        (a1, a2), (b1, b2) = rate('correct', True), rate('correct', False)
        (c1, c2), (d1, d2) = rate('mixed', True), rate('mixed', False)
        self.assertIn('in correct reports, positive claims carry {} lures {} and negative claims {} {}; in altered '
                      'reports the rates are {} {} and {} {}'.format(a1, a2, b1, b2, c1, c2, d1, d2), text)
        pooled, by_variant = polarity_auc()
        self.assertIn('AUC of polarity for being a lure over seeds 1..{:,}: {:.3f} pooled, {:.3f} in correct reports and '
                      '{:.3f} in altered reports'.format(TEST_SEEDS, pooled, by_variant['correct'], by_variant['mixed']), text)
        self.assertLess(pooled, .80)
        meta = [r['meta'] for r in t.values()]
        self.assertIn('every dossier hides {} to {} entries beyond the view and shows {} to {} inside it, and {} to {} sites '
                      'have no entry'.format(min(m['entries beyond view'] for m in meta), max(m['entries beyond view'] for m in meta),
                                             min(m['entries in view'] for m in meta), max(m['entries in view'] for m in meta),
                                             min(m['sites without entry'] for m in meta), max(m['sites without entry'] for m in meta)), text)
        self.assertIn('the annex has {:,} to {:,} lines and {} to {} over-width lines'.format(
            min(m['annex lines'] for m in meta), max(m['annex lines'] for m in meta),
            min(m['annex over-width lines'] for m in meta), max(m['annex over-width lines'] for m in meta)), text)
        without = lures_without = 0
        for record in t.values():
            for claims in record['claims'].values():
                for claim in claims:
                    without += not claim['entry in view']
                    lures_without += claim['lure']
        self.assertIn('{:.1f}% of the claims about a site with no entry in the view are lures'.format(
            100 * lures_without / without), text)
        positive = collections.Counter()
        wrong = collections.Counter()
        for record in t.values():
            for variant, claims in record['claims'].items():
                for claim in claims:
                    if claim['positive'] and not claim['entry in view']:
                        positive[variant, claim['lure']] += 1
                    if variant == 'mixed':
                        wrong[claim['positive'], claim['wrong']] += 1
        self.assertEqual(positive['correct', False], 0)
        self.assertIn('Of the positive ones {:.1f}% are lures (100% in correct reports, {:.1f}% in altered reports)'.format(
            100 * sum(positive[v, True] for v in ('correct', 'mixed')) / sum(positive.values()),
            100 * positive['mixed', True] / (positive['mixed', True] + positive['mixed', False])), text)
        self.assertIn('in altered reports {:.1f}% of the positive claims and {:.1f}% of the negative claims are wrong'.format(
            *(100 * wrong[p, True] / (wrong[p, True] + wrong[p, False]) for p in (True, False))), text)
        modal_long, modal_blocks, accuracy, baseline = reviewer_rule()
        self.assertIn('scores {:.3f} against base rate {:.3f} on seeds 501..1000'.format(accuracy, baseline), text)
        shown_end = max(e['start'] + e['lines'] for r in t.values() for e in r['entries'] if not e['beyond'])
        late_start = min(e['start'] + 1 for r in t.values() for e in r['entries'] if e['beyond'])
        self.assertIn('No entry occupies a line from {:,} to {:,}'.format(shown_end + 1, late_start - 1), text)
        inside = [m['annex over-width lines'] - m['beyond-view over-width lines'] for m in meta]
        self.assertIn('The {} to {} over-width lines in the view'.format(min(inside), max(inside)), text)
        self.assertIn('{} to {} numbered items'.format(min(m['annex items'] for m in meta), max(m['annex items'] for m in meta)), text)
        deviations = stratum_deviations()
        self.assertIn('the largest AUC deviation from 0.5 is {:.3f} in the strata of correct reports, {:.3f} in the positive strata '
                      'of altered reports, and {:.3f} among the {} lures of negative amount claims of altered reports'.format(
                          max(d for k, (d, n) in deviations.items() if k[0] == 'correct'),
                          max(d for k, (d, n) in deviations.items() if k[0] == 'mixed' and k[2]),
                          deviations['mixed', 'penalty_amount', False][0], deviations['mixed', 'penalty_amount', False][1]), text)

    def test_no_none_claim_text(self):
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                self.assertNotIn('None', row['claim_text'])

    def test_paragraphs_match_both_blind_runner_and_actual_audit_helper(self):
        import audit
        for seed in range(1, 61):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(run_blind.paragraphs(report), audit.paragraphs(report))
                parts = run_blind.paragraphs(report)
                self.assertEqual(set(parts) - {'document-preamble'}, {'C{:02d}'.format(i) for i in range(1, 13)})
                self.assertEqual(len(texts(report)), 12)

    def test_report_and_metadata_guessers_with_disclosed_polarity_cue(self):
        samples = collections.defaultdict(list)
        t = table()
        for seed in range(1, TEST_SEEDS + 1):
            record = t[seed]
            metadata = dict(record['metadata'])
            metadata['source names'] = tuple(sorted(n[9:] for n in metadata if n.startswith('bytes of ')))
            metadata['source lengths'] = tuple((n[9:], v) for n, v in sorted(metadata.items()) if n.startswith('bytes of '))
            for variant in ('correct', 'mixed'):
                features, bag, _ = record['reports'][variant]
                samples['altered report'].append((seed, features, bag, metadata, variant == 'mixed'))
                samples['dossier has lure'].append((seed, features, bag, metadata, record['lure']))
                if seed <= CLAIM_SEEDS:
                    for claim in record['claims'][variant]:
                        item = dict(features, kind=claim['kind'], site=claim['site'], positive=claim['positive'],
                                    amount=claim['amount'], position=claim['position'], shape=claim['shape'],
                                    claim_bytes=claim['claim_bytes'])
                        samples['wrong claim'].append((seed, item, bag, metadata, claim['wrong']))
                        samples['lure claim'].append((seed, item, bag, metadata, claim['lure']))
        for target, data in samples.items():
            for include_metadata in (False, True):
                train, test = [], []
                for seed, features, bag, metadata, label in data:
                    sample = (dict(features, **metadata) if include_metadata else features, bag, bool(label))
                    (train if seed <= 300 else test).append(sample)
                baseline = max(sum(y for _, _, y in test), sum(not y for _, _, y in test)) / len(test)
                # Independent units: reports for "altered report" (the two reports of a dossier carry opposite
                # labels), dossiers for "dossier has lure" (both reports carry the same label), and reports for
                # the claim targets (the twelve claims of a report share the features of the whole report).
                units = len(test) / (12 if 'claim' in target else 1 if target == 'altered report' else 2)
                scores = guessers(train, test)
                best_name = max(scores, key=scores.get)
                allowed = margin(baseline, units)
                print('Truncated guessers {} metadata={} best={} {:.4f} majority={:.4f} allowed +{:.4f} n={}; NB={:.4f}'.format(
                    target, include_metadata, best_name, scores[best_name], baseline, allowed, len(test),
                    scores['all report words/BernoulliNB']))
                print('  ' + '; '.join('{}={:.4f}/{:.4f}'.format(name, value, baseline)
                                      for name, value in scores.items()))
                with self.subTest(target=target, metadata=include_metadata):
                    self.assertLessEqual(max(scores.values()), baseline + allowed, (target, include_metadata, scores, baseline))
        pooled, by_variant = polarity_auc()
        print('Truncated logical polarity cue: AUC pooled {:.4f}, correct {:.4f}, altered {:.4f}'.format(
            pooled, by_variant['correct'], by_variant['mixed']))

    def test_guesser_margin_is_four_standard_errors_of_independent_units(self):
        self.assertAlmostEqual(margin(.5, 1000), 4 * math.sqrt(.25 / 1000))
        self.assertLess(margin(.5, 2000), .05)
        self.assertGreater(margin(.5, 500), margin(.5, 1000))
        self.assertGreater(margin(.5, 1000), .05)  # the earlier fixed margin of three points was inside the noise

    def test_title_sentinel_positive_control_is_detected(self):
        samples = []
        for seed in range(1, 21):
            for label in (False, True):
                report = case(seed)['files']['report-correct.md']
                if label:
                    report = report.replace('estates annex', 'estates annex SENTINEL', 1)
                features, bag = report_features(report)
                samples.append((features, bag, label))
        scores = guessers(samples[:20], samples[20:])
        self.assertEqual(scores['title/categorical'], 1)
        self.assertEqual(scores['all report words/BernoulliNB'], 1)

    def test_write_load_seal_and_stage(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='truncated-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            fixtures, keys = root / 'fixtures', root / 'keys'
            summary = generate.write_suite(fixtures, keys, 9, 3, ['truncated'])
            self.assertEqual(len(summary['dossiers']), 3)
            self.assertEqual(sorted(scorecard.load_keys(keys)), sorted(summary['dossiers']))
            arena, sealed = root / 'arena', root / 'sealed'
            arena.mkdir()
            blindness.seal_keys(keys, sealed, [arena])
            for dossier in summary['dossiers']:
                for variant, other in (('correct', 'mixed'), ('mixed', 'correct')):
                    original = fixtures / dossier / ('report-' + variant + '.md')
                    record = blindness.stage_case(original, fixtures / dossier / 'sources', arena, root / 'records',
                                                  {'dossier': dossier, 'variant': variant},
                                                  withheld=[sealed, fixtures / dossier / ('report-' + other + '.md')])
                    staged = Path(record['arena_run'])
                    self.assertEqual((staged / 'case/report.md').read_bytes(), original.read_bytes())
                    self.assertNotIn(dossier, str(staged))
                    self.assertEqual(sorted(p.name for p in (staged / 'case/sources').iterdir()),
                                     sorted(p.name for p in (fixtures / dossier / 'sources').iterdir()))

    def test_restatement_contamination_clean_and_copied_rationale_detected(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='truncated-restatement-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            for seed in (1, 2, 3):
                built = case(seed)
                run = root / ('restatement-' + str(seed))
                (run / 'case').mkdir(parents=True)
                (run / 'case/report.md').write_text(built['files']['report-mixed.md'])
                rows = [r for r in built['key']['expectations'] if r['variant'] == 'mixed']
                (run / 'notes.md').write_text('\n'.join(r['claim_text'] + ' My verdict is ' + r['semantic_state'] for r in rows))
                self.assertEqual(blindness.scan_run(run, built['key']['expectations'])['verbatim_overlaps'], [])
                (run / 'copied.md').write_text(rows[-1]['rationale'])
                self.assertTrue(blindness.scan_run(run, built['key']['expectations'])['verbatim_overlaps'])


if __name__ == '__main__':
    unittest.main()
