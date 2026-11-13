"""Inspection working files and a manually maintained appointment register.

Each of eight inspections has its own working file of one, two or three dated
entries, written by a technician or the supervisor. An entry exists because
something happened: a first visit, a re-inspection, a visit that failed for want of
access, a corrected date, a corrected result, a worksheet checked with nothing to
change, a visit struck out, a date that could not be proved. Every entry restates
the whole account (examinations completed, result, completion date of the latest
examination) in its own words. The register carries a one-line Status note for each
file with the date it was copied by hand. The reader believes_status_note takes
every fact restated in the register from that note, including its finite date
alternatives; everything else stays as filed. The note is convenient; the truth
takes opening the file and applying the rule about the latest entry.

A note can be wrong because it was copied before a later entry changed the account
and not brought up to date (stale), because it gives the booked date instead of the date of the
examination (planned), or because it repeats the line of another row (copy). The
number of entries of a file, who wrote them and the gaps between them are drawn
independently of whether the note is wrong (the dates of the examinations also
set the entry dates). A wrong note on a one-entry file is therefore a planned date
or a copied line; a stale note needs a later entry. Passed, re-inspected and on
time are drawn independently of each other.

Dates in the register. The copy date follows the date the note states, by one rule
for every row, right or wrong and whatever made it wrong: it is the latest date the
note states, plus the lag of the entry the note summarises (one to four days), plus
a lag drawn with the file (one to eight days). No note states a date on or after
the day it was copied (smallest gap two days). A planned or copied note is written
after the examination that really took place; the date it states is before the last
entry of its file and, in a file of one entry, within four days of it (as for a right
note); it is copied after that entry in a file of one entry and no later after the
last entry than the note of a right account is. The diary booking named in F-01 is
the first date the note states (the date of the visit if the note records no
examination) in every file, so the booking says nothing about the note; a planned
note is the booked date. A copied line is half of a pair of identical lines, and the
two rows of a pair are neighbours in the register. Right notes form such pairs too
(two assets examined on one visit day with one result: a twin takes the line, tag
and date of its nearest free row), at a rate tuned by topic (TWIN_PAIRS, one to
three pairs, none in the timing topic) so that the chance that a single-date note
shares its line, or only its date, with another row is the same for right and wrong
notes. A copied line may misstate the fact a claim is about, or another fact of the
account (COPY_WEIGHTS), so that a repeated line says little about whether a claim
on its row is a lure. Measured over seeds 1--1000, line shared by wrong against
right single-date notes: all 0.263 against 0.295, result topic 0.438 against 0.444,
re-inspection topic 0.411 against 0.452, timing topic 0.002 against 0.001 (date
shared: 0.277 / 0.305, 0.441 / 0.450, 0.434 / 0.462, 0.016 / 0.013); no line is shared
by three rows; the two rows of a repeated line are adjacent in 0.964 of wrong and
0.905 of right notes (302 and 1,414). The checkpoints of a copying record and of a
twin are placed so that the copied date lies among them as the date of an ordinary
account does; the opening date of a file follows its first checkpoint (60 to 90
days earlier), not its notes. A right note is copied before the first entry that
states its account in 27 per cent of right records (46 per cent of those with more
than one entry: the register copies the visit sheet and the file is brought up to
date later), and a wrong note that repeats an entry in 8 per cent of those records;
only a stale note is always copied before a later entry that changes the account.

Rule sentences, verbatim (procedure.md):
Only an inspection's own file establishes its state, result, completion date and whether a repeat examination was completed.
Within a file, the entry with the latest entry date replaces the earlier account in full, including corrections to the history of examinations.
An entry date is the date of writing and is not an examination date.
The register's Status note is a convenience maintained by hand and establishes none of those facts.
A completed examination has either passed or failed; an examination not completed has no result or completion date.
In this review, an inspection passed if and only if its current result is passed; did not pass includes failed and not completed.
Re-inspected means that at least two examinations of the same asset were completed, as established by the latest account; a second visit without a completed examination does not count.
For a completed inspection, completion by a checkpoint means that the completion date of its most recent completed examination is on or before that checkpoint, including the whole checkpoint day.
An inspection not completed was not completed by any checkpoint.
When the latest entry says that the completion date was not recorded, exactly the two dates it lists remain possible, and neither may be excluded.
Open dates in different files may take any combination of their listed values.

Eight records receive one claim each, with no aggregate claim. One topic is drawn
for the dossier before the note discrepancies, and every record gets that topic.
All eight records have mutations. Both reports must have at least three positive
and three negative claims: at most two notes differ, so every lure has a control
with exactly its wording shape. A private copy of the supplied RNG previews the
generator's report selection; a rejected draw consumes the supplied RNG normally.
The filter inspects polarity counts, never echo labels. It conditions both reports
symmetrically. Unlike protecting individual controls from mutation, it never makes
a minority-polarity claim necessarily correct. Tests compare the preview with the
actual generator and check conditional report-only verdict frequencies.

Mutations reverse a settled assertion, or assert one of the two checkpoint outcomes
of an open date. A lure mutation is called settles_status_note_conflict: it treats
agreement OR disagreement with a note as settled, including premature rejection of
a note whose date remains possible. It never invents an offset or a third date.
Each record draws its claim checkpoint from its three checkpoints, and a claim
that is wrong uses the same checkpoint as the right claim about that record, so the
checkpoint says nothing about correctness. For an open date the claims that are left
open use the checkpoint that falls between the two possible dates, and the right
claim uses one of the other two. Checkpoints lie between 2034-01-01 and 2034-12-31.

Executed measurements. Over seeds 1--300, 86 dossiers have a note that is right on
every fact of every record (28.7 per cent; 28.8 per cent over seeds 1--1000), 33
have one wrong note and 181 have two. Of the 214 with a wrong note, 24 have no echo
on the selected claims. Executed R9, seeds 1--100, twelve dossiers per seed: echo
rows per twelve dossiers have minimum 10, median 22.0, maximum 35. The 2,205 echo
rows have 2,140 definite actual keys (1,726 supported, 414 refuted) and 65
undetermined. Directions: supported->refuted 1,726; refuted->supported 414;
undetermined->refuted 34; undetermined->supported 31. All six polarity-preserving
wording shapes have supported and wrong/open examples. No tested calendar or
polarity-frequency bin with at least 20 observations has a decisive verdict. Seeds
1--300: the sources outside the register hold 2,148--3,046 words; each record file
199--386 words. Seeds 1--1000: record files of 1,350--2,530 bytes; seeds 1--100
with twelve dossiers each: 199--386 words, 1,350--2,531 bytes. Of 1,728 timing
claims, 86 name the exact completion date (78 positive, 8 negative statements).
The share of wrong timing claims is 0.194, 0.211 and 0.180 at the earliest, middle
and latest checkpoint (609, 564 and 555 claims).

Length. Every sentence that carries a fact is written to one fixed size: the
sentence naming the examinations completed (0, 1 or 2), the result (pass, fail,
none), the completion date (one date, two possible dates, none), the reason for an
entry (94 bytes and 18 words whatever happened) and the register note (65 bytes and
9 words for every state). This is length-matched wording, not filler; the sizes are
asserted when the module is imported and again, from the rendered text, in a test.
The rest of the length (people, wording frames, zero to two paperwork sentences per
entry, zero to three routing sentences, one to three entries) is drawn without the
facts. Offset-normalised length rules (the offset of each record file learned as its
shortest length in seeds 1--500, then a lookup of topic, polarity and residual,
scored on seeds 501--1000) stay at or below the base rate, apart from sampling noise
in the altered-report rule (-0.003 here, up to +0.013 for word counts): wrong claim
0.705 against 0.7945; ordinary wrong claims in altered reports 0.5731 against 0.6127; lure claim
0.8324 against 0.882; altered report 0.497 against 0.5000; a smoothed per-record
rule for "the dossier holds a wrong note" 0.71 against 0.71. The same method on
the previous version of this family gave 0.9461 (base 0.7975), 0.9733 (0.6211),
0.9404 (0.8848), 0.9610 (0.5000) and 0.7780 (0.6900). With the neutral structure
(record number, entries, routing and paperwork sentences, supervisor entries) taken
out, file length differs by any fact of the record with |z| at most 1.92 (about 12
bytes of spread left, which a leak of a few bytes would exceed).

Guesser measurements use seeds 1--150 for training and 151--300 for evaluation.
Features include the entire visible report (title, preamble, claims, citations),
wording, polarity, frequency, position, record identity, calendar day/month/rank,
report length, source filenames/lengths, and linked record lengths and word counts.
Highest accuracy / majority base rate by target, taking the feature that gains most:
altered report 0.5400 / 0.5000 (collapsed register bytes with polarity count; the
same rule trained on seeds 1--500 and scored on 501--1000 gives 0.5080, the other
way round 0.4990, and trained on 1--1000 and scored on 1001--2000 0.5125, so this
is mostly sampling noise on 300 reports); wrong claim 0.7975 / 0.7975; lure claim
0.8892 / 0.8892; dossier discrepancy 0.6867 / 0.6867. These probes cannot rule out other
cues; tests use a predeclared tolerance for sampling noise.

Remaining cues. Report-only: each report has one topic and three to five positive
statements; this reduces variety without disclosing which records are erroneous.
The polarity histogram (topic and count of positive statements) and the report
length name the altered report with accuracy 0.50--0.53 across the splits above
(histogram 0.497, 0.521, 0.519 and 0.521; length 0.530, 0.512, 0.516 and 0.500): a
point or two above one half at most (inference: a wrong claim moves the positive
share of a report towards a half). Open timing claims can
move checkpoint dates between reports. Source-level, measured over seeds 1--1000
(8,000 records, 16.6 per cent of them wrong): whether a file has a later entry does
not move the chance that its note is wrong (0.163, 0.164 and 0.175 for one, two and
three entries; chance of a lure claim on the record 0.114, 0.125 and 0.130).

What the register alone, and the register plus F-01, still give. Attacks of the
third fix round, trained on seeds 1--1000 and scored on 1001--2000 (a decision
tree of depth three to five over copy date minus note date, shared line, shared
date, note form (one date, two dates, none), result, repeat, tag and record number;
for F-01 also the note date against the three checkpoints, the booking and the
opening date). "This record's note is wrong": register alone 0.8411 against base
0.8411 (no rule beats the majority), register plus F-01 0.8429 against 0.8411 (48
notes flagged, precision 0.646, recall 0.024); the previous version gave 0.8880
against 0.8384 (precision 0.993, recall 0.309) and 0.8974 (0.994, 0.367). "This
claim is a lure": register alone 0.8877 against 0.8862 (92 claims, precision 0.630,
recall 0.032), plus F-01 0.8904 (605 claims, precision 0.555, recall 0.185);
previously 0.9288 against 0.8843 and 0.9443. "This claim is wrong": 0.8492 and
0.8927 against 0.7966, close to the note-trusting reader (the note is right on 83
per cent of records), not a leak: the rule "trust the note, but flip it when its
date is on or after its copy day" never fires and equals that reader (0.8792 with
the register alone, 0.8868 with F-01; previously 0.9116 against 0.8811 and 0.9120
against 0.8843). A naive-Bayes score over the same features separates wrong from
right notes with AUC 0.577 (register alone) and 0.608 (with F-01); previously 0.708
and 0.755. On the notes that state one date, the date features alone (copy date,
shared line, shared date) give AUC 0.491 and 0.557 with F-01 (previously 0.655 and
0.729). Adding every entry header date to the register and F-01 gives a tree at
0.8426 against 0.8411 (30 flagged, precision 0.700, recall 0.017; previously 0.9055
against 0.8384, precision 0.991, recall 0.419). What is left is, in order of size:
(1) the form of the note (register alone): a note that states two dates is wrong in
6.9 per cent of 764 rows against 17.6 per cent (covering 4.0 per cent of wrong
notes; the wrong ones are copied lines), so it rarely carries a lure; an "awaiting"
note is wrong in 9.5 per cent of 1,298 rows against 17.9 per cent (covering 9.3
per cent). The five form features together give AUC 0.578 (0.556 without the
two-date cue);
(2) a note date more than ten days from the middle checkpoint (register and F-01):
19.2 per cent wrong of 2,220 records, against 15.5 per cent, covering 32.2 per cent
(a stale note about a re-examination states a date up to 21 days before the later
one; AUC of the distance 0.52);
(3) a repeated line and the claim on its row (register and report): among claims
on topics other than timing in the altered report, a claim that the note
contradicts is a lure in 21.6 per cent of cases when the note's line is repeated in
the register and in 13.3 per cent when it is not (a claim the note agrees with: 11.2
and 7.0 per cent); rows with a repeated line are wrong on the claimed topic in
15.2 and 16.2 per cent (result, re-inspection) against 9.8 and 9.5 per cent. The
number of repeated dated lines also moves the chance that a dossier holds a wrong
note: 0.636, 0.683, 0.842, 0.863 and 1.000 for none to four pairs (19 dossiers
with four), and the share of wrong notes among the rows of a pair is 0.132, 0.209,
0.189 and 0.217 for one to four pairs; pairs of rows three or more apart are right
(3 of 202 rows wrong);
(4) a file that leaves its completion date open: 22.3 per cent wrong of 915, against
15.8 per cent, covering 15.4 per cent;
(5) a completed-inspection note identical to another row's note (register): 19.5
per cent wrong of 1,822 records, against 15.7 per cent, covering 26.8 per cent;
(6) the entries' own dates (source level, not the register): a copy date earlier
than the file's latest entry is 19.6 per cent wrong of 3,604 records, against 14.1
per cent, covering 53.3 per cent.
The booking in F-01 equals the note date in every one of the 6,702 rows that state
a date; the rows that do not (awaiting notes) are cue (1). Reason sentences, the
account sentences and the register note differ by facts by design. The paired
source and report filenames are generator conventions: blind staging renames the
selected report to report.md and withholds its twin and evaluator keys.

Limits: this fictional office uses eight Markdown files, fixed prose and a small
finite date space. Whole dossiers use one topic, and the symmetric polarity
filter reduces the distribution's variety. Passed, re-inspected and on time are
independent, so the family does not test a reader who expects them to go together;
a file has at most two examinations and at most three entries; every entry restates
the whole account. The report preview must change if the generator changes its
selection algorithm; an integration test checks agreement. It does not vary scans,
signatures, authenticity, legal standards or incomplete file delivery, nor how the
register is laid out. The twin rate (TWIN_PAIRS) is a constant tuned on seeds 1--2000
against the rate of copied lines, which follows from the mechanism weights; a change
to those weights needs it tuned again, and the tests compare the two rates. The procedure fixes the meaning of completion and
re-inspection even when a history is corrected; without that rule a careful reader
could reasonably count an earlier entry as independent evidence. Both derivations
share an interpretation of the English, not proof that no other interpretation
exists. The citations deliberately include both the register and the full working
file, departing from the research sketch's uncited-note proposal as required by the
brief. Diligent readers who compare the later entries with the notes may never
follow the lure. No auditor susceptibility has been measured.
"""
import datetime
import random

import generate

from generate import DAY, claim, document, section


RULES = (
    "Only an inspection's own file establishes its state, result, completion date and whether a repeat examination was completed.",
    "Within a file, the entry with the latest entry date replaces the earlier account in full, including corrections to the history of examinations.",
    "An entry date is the date of writing and is not an examination date.",
    "The register's Status note is a convenience maintained by hand and establishes none of those facts.",
    "A completed examination has either passed or failed; an examination not completed has no result or completion date.",
    "In this review, an inspection passed if and only if its current result is passed; did not pass includes failed and not completed.",
    "Re-inspected means that at least two examinations of the same asset were completed, as established by the latest account; a second visit without a completed examination does not count.",
    "For a completed inspection, completion by a checkpoint means that the completion date of its most recent completed examination is on or before that checkpoint, including the whole checkpoint day.",
    "An inspection not completed was not completed by any checkpoint.",
    "When the latest entry says that the completion date was not recorded, exactly the two dates it lists remain possible, and neither may be excluded.",
    "Open dates in different files may take any combination of their listed values.",
)

ASSETS = (
    ('Alder depot', 'loading door', 'the lower guide and the emergency release'),
    ('Briar store', 'platform gate', 'the latch housing and the closing spring'),
    ('Cairn workshop', 'service shutter', 'the travel stops and the manual chain'),
    ('Dene warehouse', 'delivery barrier', 'the pivot pin and the locking handle'),
    ('Elver station', 'access hatch', 'the hinges and the retaining catch'),
    ('Fallow yard', 'inspection cover', 'the lifting handles and the edge seal'),
    ('Gorse hall', 'equipment cage', 'the mesh fixings and the access lock'),
    ('Hazel annex', 'stores door', 'the frame anchors and the door closer'),
)

TECHNICIANS = ('Mara Senn', 'Joss Whitby', 'Dev Okoro', 'Ines Calloway')
AUTHORS = tuple((name, 'examining technician') for name in TECHNICIANS) + (('Hollis Brandt', 'depot supervisor'),)
CLERKS = ('Oren Pell', 'Tamsin Rudd')
DIARY_TAGS = ('(diary entry)', '(as diarised)')

# Every sentence that carries a fact of the account is written in a fixed number of bytes and words, so that the
# length of a file says nothing about the facts in it; the assertions below check each set at import.
COUNT_FRAMES = ('Completed examinations of this asset, counting from the first: {n}.',
                'Examinations of this asset completed to date, repeats included: {n}.')
RESULT_FRAMES = ('Result of the latest examination: {r}.', 'Operating checks, final result: {r}.')
DATE_ONE = 'Completion date of the latest examination: {d}, as transcribed on the visit sheet.'
DATE_OPEN = 'Completion date of the latest examination: not recorded; one of {a} or {b}.'
DATE_NONE = 'Completion date of the latest examination: none, since no examination was completed here.'

# One sentence says why the entry was written. All of them have 94 bytes and 18 words, whatever happened.
REASONS = {
    'first_two': (
        'I examined the asset twice at the depot and set down both results here, as the diary required.',
        'I looked at the asset twice at the depot and entered both results here, as the diary required.',
        'I inspected the asset twice on the site visit and noted both sets here, as the diary required.'),
    'first_open': (
        'I examined the asset at the depot but the visit sheet was illegible, so the date is uncertain.',
        'I inspected the asset at the depot but the supervisor holds the sheet, so the date stays open.',
        'I examined the asset during the visit but the visit sheet was smudged, so the date stays open.'),
    'first_two_open': (
        'I examined the asset twice at the depot but the sheet was illegible, so the date is uncertain.',
        'I examined the asset twice on site but the supervisor holds the sheet, so the date stays open.',
        'I examined the asset twice during the visit but the sheet was smudged, so the date stays open.'),
    'reexam_open': (
        'I inspected the asset again after the repairs, but the sheet was smudged, so the date is open.',
        'I tested the asset again after the repairs, but the sheet was smudged, so the date is unclear.',
        'I tested the asset again after the repairs, but the sheet was smudged, so the date stays open.'),
    'completed_open': (
        'After access was agreed I tested the asset, but the sheet was smudged, so the date stays open.',
        'Once access was agreed I checked the asset, but the sheet was smudged, so the date is unclear.',
        'I came back and examined the asset, but the visit sheet was illegible, so the date stays open.'),
    'first_done': (
        'I examined the asset at the depot and entered the results in this file, as the diary required.',
        'I inspected the asset on the site visit and set down the findings here, as the diary required.',
        'I looked at the asset on the site visit and entered the results here, as the supervisor asked.'),
    'first_not': (
        'I called at the depot gate but did not manage to examine the asset because access was refused.',
        'I called at the depot gate but did not manage to inspect the asset because access was refused.'),
    'reexam': (
        'I examined the asset a second time after the repair crew left and set down the findings below.',
        'I inspected the asset once more after the repair visit and set down the findings in this file.',
        'I tested the asset a second time after the repair crew left and noted the results that follow.'),
    'completed': (
        'I came back once access was agreed and tested the asset, setting down the checks in this file.',
        'I came back once entry was given and examined the asset, setting down the checks in this file.'),
    'again_not': (
        'I called back but did not manage to test the asset because access had still not been arranged.',
        'I came out again but could not examine the asset as the foreman had still not arranged access.'),
    'redate': (
        'I checked the worksheet in full against the log and amended the date given in the first entry.',
        'I compared the worksheet line by line against the diary and fixed the date in the older entry.',
        'I checked the worksheet line by line against the file and changed the date in the prior entry.'),
    'rescore': (
        'I checked the worksheet in full against the log and fixed the result given in the older entry.',
        'I compared the worksheet line by line against the log and fixed the scores in the first entry.'),
    'confirm': (
        'I checked the worksheet in full against the log and found nothing to alter in the first entry.',
        'I checked the worksheet in full against the log and found nothing to amend in the older entry.'),
    'uncount': (
        'I went through the worksheet in full against the diary and struck out a visit listed in error.',
        'I went through the worksheet in full against the file and struck out a visit entered in error.'),
    'undated': (
        'I checked the worksheet again against the log but could not prove the date in the first entry.',
        'I checked the worksheet twice against the log but could not prove the date in the older entry.'),
}

# Sentences about the paperwork that say nothing about the examination; zero to two are added to an entry.
TAILS = (
    'The worksheet is kept in this file.',
    'The asset label was legible and matched the worksheet.',
    'The photographs taken are held by the records desk.',
    'The foreman signed the visit sheet.',
    'Copies went to the supervisor and the records desk.',
    'Nothing else was raised at the visit.',
)

ROUTING = (
    'The records clerk checked the folder reference against the shelf list.',
    'The office copy was returned to the records desk after circulation.',
    'The review secretary requested a spare copy for the meeting folder.',
    'The clerk replaced the worn cover and retained the original reference label.',
)
NO_ROUTING = 'No movement of the file has been recorded.'

# Days before the claim checkpoint (on time) and after it (late) on which an ordinary examination is dated.
ON_TIME = (0, 1, 2, 3, 4, 5, 7, 9, 12)
LATE = (1, 2, 3, 4, 5, 7, 9)
# Pairs of right notes that read alike (two assets examined on one visit day with one result), per dossier topic.
TWIN_PAIRS = {'timing': (0,), 'result': (0, 1, 1, 1, 1, 2, 3), 'reinspection': (0, 1, 1, 1, 1, 2, 3)}
TOPICS = ('timing', 'result', 'reinspection')
MODES = ('up', 'up', 'down', 'down', 'open', 'same_side')
LAST_DATE = datetime.date(2035, 3, 1)
MAX_ENTRY_LAG = 4  # the most days between an examination and the entry that records it
COPY_WEIGHTS = (1, 3)  # draws of a copied line among the ways to misstate a record in up/down and in open/same_side modes
MAX_NOTE_LAG = 8  # the most days between the last entry of a file and the copying of its note


def size(text):
    return len(text.encode('utf-8')), len(text.split())


def _check_lengths():
    """The fixed-size sets of sentences; a wrong edit fails when the module is imported."""
    for frame in COUNT_FRAMES:
        assert len({size(frame.format(n=n)) for n in (0, 1, 2)}) == 1, frame
    for frame in RESULT_FRAMES:
        assert len({size(frame.format(r=r)) for r in ('pass', 'fail', 'none')}) == 1, frame
    first, second, third = '2034-05-03', '2034-05-09', None
    assert len({size(DATE_ONE.format(d=first)), size(DATE_OPEN.format(a=first, b=second)), size(DATE_NONE)}) == 1
    assert {size(text) for texts in REASONS.values() for text in texts} == {(94, 18)}, sorted(
        {size(text) for texts in REASONS.values() for text in texts})
    assert len({size(note_text({'count': c, 'passed': p, 'dates': d}, tag))
                for tag in DIARY_TAGS for c, p, d in
                ((0, False, ()), (1, True, (datetime.date(2034, 5, 3),)), (2, False, (datetime.date(2034, 5, 3),)),
                 (1, False, (datetime.date(2034, 5, 3), datetime.date(2034, 5, 9))))}) == 1


class Retry(Exception):
    """A draw that cannot be completed; the caller draws again."""


# ---------- Facts of one account ----------
# A fact set is {'count': examinations completed (0, 1 or 2), 'passed': bool, 'dates': () | (date,) | (date, date),
# 'visit': the date of a visit without an examination, or None}.

def facts(state):
    return state['count'], state['passed'], state['dates']


def anchor(state):
    return max(state['dates']) if state['count'] else state['visit']


def outcomes(state, topic, checkpoint):
    """The values the topic can take for this account; two values mean the account leaves it open."""
    if topic == 'timing':
        return {date <= checkpoint for date in state['dates']} if state['count'] else {False}
    return {state['passed']} if topic == 'result' else {state['count'] == 2}


def sides(date, checkpoints):
    return tuple(date <= checkpoint for checkpoint in checkpoints)


def note_text(state, tag):
    """The clerk's one-line status note."""
    if not state['count']:
        return f'Awaiting inspection {tag}, no result filed; repeat: none.'
    dates = state['dates']
    when = dates[0].isoformat() + ' ' + tag if len(dates) == 1 else dates[0].isoformat() + ' or ' + dates[1].isoformat()
    return (f"Last inspected on {when}, {'passed' if state['passed'] else 'failed'}; "
            f"repeat: {'done' if state['count'] == 2 else 'none'}.")


def draw_state(rng, cp, want_topic=None, want=None, open_dates=False, single=False):
    """An account whose fields are drawn independently: only the topic outcome may be asked for."""
    for _ in range(400):
        count = rng.choice((1, 2)) if open_dates or single else rng.choice((0, 1, 1, 2, 2))
        passed = bool(count) and rng.random() < 0.55
        state = {'count': count, 'passed': passed, 'dates': (), 'visit': None}
        if not count:
            state['visit'] = cp + DAY * rng.choice((-6, -3, -1, 0, 2, 4, 7))
        elif open_dates:
            state['dates'] = (cp - DAY * rng.choice((0, 1, 2, 3, 5)), cp + DAY * rng.choice((1, 2, 3, 5, 7)))
        elif rng.random() < 0.6:
            state['dates'] = (cp - DAY * rng.choice(ON_TIME),)
        else:
            state['dates'] = (cp + DAY * rng.choice(LATE),)
        if want is None or outcomes(state, want_topic, cp) == {want}:
            return state
    raise Retry('no account with the wanted outcome')


def applicable(state):
    kinds = [('rescore', 2), ('confirm', 2)] if state['count'] else [('again_not', 3), ('uncount', 1), ('confirm', 1)]
    if state['count']:
        kinds.append(('redate', 2) if len(state['dates']) == 1 else ('undated', 2))
        kinds += [('completed', 3), ('uncount', 1)] if state['count'] == 1 else [('reexam', 3)]
    return [kind for kind, weight in kinds for _ in range(weight)]


def predecessor(rng, kind, cur, same_side_as=None):
    """The account that an event of this kind turned into `cur` (the entry before it)."""
    prev = dict(cur)
    if kind == 'redate':
        deltas = [-9, -6, -4, -2, -1, 1, 2, 4, 6, 9]
        rng.shuffle(deltas)
        for delta in deltas:
            date = cur['dates'][0] + DAY * delta
            if same_side_as is None or sides(date, same_side_as) == sides(cur['dates'][0], same_side_as):
                prev['dates'] = (date,)
                return prev
        raise Retry('no date on the same side')
    if kind == 'undated':
        prev['dates'] = (rng.choice(cur['dates']),)
    elif kind == 'rescore':
        prev['passed'] = not cur['passed']
    elif kind == 'reexam':
        prev.update(count=1, passed=rng.random() < 0.5, dates=(min(cur['dates']) - DAY * rng.choice((7, 10, 14, 18, 21)),))
    elif kind == 'completed':
        prev.update(count=0, passed=False, dates=(), visit=min(cur['dates']) - DAY * rng.choice((3, 5, 8, 12)))
    elif kind == 'again_not':
        prev['visit'] = cur['visit'] - DAY * rng.choice((3, 5, 8, 12))
    elif kind == 'uncount':
        if cur['count']:
            prev.update(count=2, passed=rng.random() < 0.5, dates=(max(cur['dates']) + DAY * rng.choice((2, 4, 6)),))
        else:
            prev.update(count=1, passed=rng.random() < 0.5, dates=(cur['visit'] - DAY * rng.choice((2, 4, 6)),), visit=None)
    return prev


def history(rng, own, entries, force=None, same_side_as=None):
    """Chronological accounts and the events between them, built backwards from the latest account."""
    states, kinds, cur = [own], [], own
    for step in range(entries - 1):
        kind = force if step == 0 and force else rng.choice(applicable(cur))
        prev = predecessor(rng, kind, cur, same_side_as if step == 0 else None)
        states.append(prev)
        kinds.append(kind)
        cur = prev
    return states[::-1], kinds[::-1]


def filing_log(rng):
    """Routine routing entries drawn without consulting examination facts."""
    return '\n\n'.join(rng.sample(ROUTING, rng.randrange(4)))


def draw_shape(rng):
    """Everything about a file that has nothing to do with its facts or with its note."""
    middle = datetime.date(2034, 1, 11) + DAY * rng.randrange(345)
    entries = rng.choice((1, 1, 1, 1, 2, 2, 2, 2, 3, 3))
    return {'middle': middle, 'checkpoints': tuple(middle + offset * DAY for offset in (-10, 0, 10)),
            'cp': rng.randrange(3), 'entries': entries, 'opener': rng.choice(TECHNICIANS),
            'authors': [rng.choice(AUTHORS) for _ in range(entries - 1)], 'gaps': [rng.choice((7, 8, 9, 10, 12, 14)) for _ in range(entries - 1)],
            'lags': [rng.choice((1, 2, 3, 4)) for _ in range(entries)], 'note_lag': rng.randrange(1, MAX_NOTE_LAG + 1),
            'lead': rng.choice((60, 70, 80, 90)), 'tag': rng.choice(DIARY_TAGS),
            'tails': [rng.sample(TAILS, rng.choice((0, 1, 1, 2))) for _ in range(entries)],
            'orders': [rng.choice(((0, 1, 2), (1, 0, 2), (0, 2, 1))) for _ in range(entries)],
            'frames': [(rng.randrange(2), rng.randrange(2)) for _ in range(entries)],
            'wording': [rng.random() for _ in range(entries)], 'routing': filing_log(rng)}


def anchor_shape(rng, shape, date):
    """The same file with its checkpoints moved so that `date` lies among them as the date of an ordinary account does."""
    for _ in range(20):
        offset = -rng.choice(ON_TIME) if rng.random() < 0.6 else rng.choice(LATE)
        middle = date - DAY * (offset + 10 * (shape['cp'] - 1))
        if datetime.date(2034, 1, 11) <= middle <= datetime.date(2034, 12, 21):
            return dict(shape, middle=middle, checkpoints=tuple(middle + offset_ * DAY for offset_ in (-10, 0, 10)))
    raise Retry('no checkpoints in the year around this date')


def choose_mechanism(rng, topic, mode, entries, neighbours):
    options = ['stale'] * 3 if entries >= 2 else []
    if mode in ('open', 'same_side') or topic == 'timing':
        options += ['planned'] * 2
    if topic != 'timing' and neighbours:
        # A copied line may misstate the fact a claim is about (up, down) or another fact of the account (open, same_side),
        # so that a repeated line says nothing about whether a claim on its row is a lure.
        options += ['copy'] * (COPY_WEIGHTS[0] if mode in ('up', 'down') else COPY_WEIGHTS[1])
    if not options:
        raise Retry('no way to misstate this record')
    return rng.choice(options)


def build_record(rng, ident, shape, topic, mode, mechanism, neighbour, twin=None):
    """Draw the accounts of one file and the note about it; the shape is already fixed."""
    cps, cp = shape['checkpoints'], shape['checkpoints'][shape['cp']]
    for _ in range(400):
        try:
            want = {'up': False, 'down': True}.get(mode)
            if mechanism == 'copy':
                held = neighbour['state']['passed'] if topic == 'result' else neighbour['state']['count'] == 2
                want = held if mode in ('open', 'same_side') else not held
            own = dict(twin) if twin else draw_state(
                rng, cp, topic, want, open_dates=mode == 'open' or (mode is None and rng.random() < 0.12),
                single=mode == 'same_side' or (mechanism == 'planned' and mode != 'open'))
            force = {'open': 'undated', 'same_side': 'redate'}.get(mode) if mechanism == 'stale' else None
            states, kinds = history(rng, own, shape['entries'], force, cps if mode == 'same_side' else None)
            if mechanism == 'stale' and mode in ('up', 'down'):
                differing = [k for k, s in enumerate(states[:-1]) if outcomes(s, topic, cp) == {not want}]
                if not differing:
                    continue
                index = rng.choice(differing)
            elif mechanism == 'stale':
                index = shape['entries'] - 2
            elif mode is None:
                index = rng.choice([k for k, s in enumerate(states) if facts(s) == facts(own)])
            else:
                index = shape['entries'] - 1
            note = states[index]
            if mechanism == 'planned':
                note = dict(own)
                if mode == 'open':
                    note['dates'] = (cp + DAY * rng.choice((-7, -5, -3, -1, 0, 1, 3, 5, 7)),)
                elif mode == 'same_side':
                    options = [own['dates'][0] + DAY * j for j in (-3, -2, -1, 1, 2, 3)]
                    options = [d for d in options if sides(d, cps) == sides(own['dates'][0], cps)]
                    if not options:
                        continue
                    note['dates'] = (rng.choice(options),)
                else:
                    later = own['dates'][0] > cp
                    note['dates'] = (cp - DAY * rng.choice(ON_TIME) if later else cp + DAY * rng.choice(LATE),)
            elif mechanism == 'copy':
                note = neighbour['state']
            if mode in ('up', 'down') and outcomes(note, topic, cp) != {not want}:
                continue
            if mechanism in ('planned', 'copy'):
                # A planned or copied note is written after the examination that really took place, after the entry
                # of a file of one entry, and no later after the last entry than the note of a right account is; the
                # date it states is before the last entry and, in a file of one entry, within the days that an entry
                # of a right account follows its examination (as an examination is dated before the entry recording it).
                dates = entry_dates(shape, states)[1]
                copied_on = anchor(note) + DAY * (shape['lags'][index] + shape['note_lag'])
                if (anchor(own) >= copied_on or (shape['entries'] == 1 and copied_on <= dates[-1])
                        or (copied_on - dates[-1]).days > MAX_NOTE_LAG or anchor(note) >= dates[-1]
                        or (shape['entries'] == 1 and (dates[-1] - anchor(note)).days > MAX_ENTRY_LAG)):
                    continue
            text = neighbour['text'] if mechanism == 'copy' else note_text(note, shape['tag'])
            return finish(rng, ident, shape, topic, mode, mechanism, own, states, kinds, index, note, text)
        except Retry:
            continue
    raise Retry('could not build a record')


def stated(state):
    """The first date a note about this account states: the examination, or the visit if none was held."""
    return state['dates'][0] if state['dates'] else state['visit']


def entry_dates(shape, states):
    """The anchor date of each account and the date of writing of each entry."""
    anchors = [anchor(state) for state in states]
    dates = []
    for position, state in enumerate(states):
        earliest = anchors[position] + DAY * shape['lags'][position]
        dates.append(earliest if position == 0 else max(dates[-1] + DAY * shape['gaps'][position - 1], earliest))
    return anchors, dates


def finish(rng, ident, shape, topic, mode, mechanism, own, states, kinds, index, note, text):
    anchors, dates = entry_dates(shape, states)
    # The clerk copies the note a drawn number of days after the date it states, whatever the note says about the file;
    # the lag is the entry's own lag and the note's lag, both drawn with the shape.
    transcribed = anchor(note) + DAY * (shape['lags'][index] + shape['note_lag'])
    # The diary booking named in the file is the date the note states, so the booking says nothing about the note.
    booking = stated(note)
    if transcribed >= LAST_DATE or dates[-1] >= LAST_DATE:
        raise Retry('dates run past the register')
    return {'id': ident, 'topic': topic, 'mode': mode, 'mechanism': mechanism, 'shape': shape, 'own': own,
            'note': note, 'note_text': text, 'states': states, 'kinds': kinds, 'entry_dates': dates,
            'transcribed': transcribed, 'booking': booking,
            'opened': min(shape['checkpoints'][0] - DAY * shape['lead'], min(anchors + [booking]) - 7 * DAY), 'note_index': index}


def reason_kind(record, position):
    """Why the entry was written; an entry that records an examination but no date says why the date is missing."""
    state = record['states'][position]
    open_date = len(state['dates']) == 2
    if position == 0:
        if not state['count']:
            return 'first_not'
        return 'first_' + ('two' if state['count'] == 2 else 'open' if open_date else 'done') + (
            '_open' if open_date and state['count'] == 2 else '')
    kind = record['kinds'][position - 1]
    return kind + '_open' if open_date and kind in ('reexam', 'completed') else kind


def entry_text(rng, record, position):
    """One dated entry: why it was written, the facts of the account in sentences of fixed size, then paperwork."""
    shape, state = record['shape'], record['states'][position]
    reason = reason_kind(record, position)
    count_frame, result_frame = (COUNT_FRAMES[shape['frames'][position][0]], RESULT_FRAMES[shape['frames'][position][1]])
    dates = state['dates']
    sentences = [count_frame.format(n=state['count']),
                 result_frame.format(r='pass' if state['passed'] else 'fail' if state['count'] else 'none'),
                 DATE_NONE if not state['count'] else DATE_ONE.format(d=dates[0].isoformat()) if len(dates) == 1 else
                 DATE_OPEN.format(a=dates[0].isoformat(), b=dates[1].isoformat())]
    sentences = [sentences[i] for i in shape['orders'][position]]
    variants = REASONS[reason]
    name, role = (shape['opener'], 'examining technician') if position == 0 else shape['authors'][position - 1]
    return (f"Entry dated {record['entry_dates'][position].isoformat()} — {name}, {role}. "
            + variants[int(shape['wording'][position] * len(variants))] + ' ' + ' '.join(sentences + shape['tails'][position]))


def assemble(rng):
    """Build from the supplied random source; no external state is consulted."""
    topic = rng.choice(TOPICS)
    conflict_count = rng.choice((0, 0, 0, 1, 2, 2, 2, 2, 2, 2))
    modes = {ident: rng.choice(MODES) for ident in rng.sample(range(8), conflict_count)}
    clerk = rng.choice(CLERKS)
    shapes = [draw_shape(rng) for _ in range(8)]
    mechanisms = {ident: choose_mechanism(rng, topic, mode, shapes[ident]['entries'],
                                          [other for other in range(8) if other not in modes])
                  for ident, mode in modes.items()}
    # A line repeated in the register is half of a pair of neighbours: a copy repeats the nearest right row's line, and
    # a twin (two assets examined on one visit day with one result) repeats the line of its nearest free row.
    free, originals, twins = [i for i in range(8) if i not in modes], {}, {}
    for ident in sorted(i for i in modes if mechanisms.get(i) == 'copy'):
        if not free:
            raise Retry('no neighbour to copy')
        originals[ident] = min(free, key=lambda other: (abs(other - ident), other))
        free.remove(originals[ident])
    for _ in range(rng.choice(TWIN_PAIRS[topic])):
        if len(free) >= 2:
            target = rng.choice(free)
            source = min((other for other in free if other != target), key=lambda other: (abs(other - target), other))
            free.remove(source)
            free.remove(target)
            twins[target] = source
    built = {}
    for ident in sorted(range(8), key=lambda i: (mechanisms.get(i) == 'copy', i in twins)):
        neighbour, twin, shape = None, None, shapes[ident]
        if mechanisms.get(ident) == 'copy':
            original = built[originals[ident]]
            neighbour = {'state': original['note'], 'text': original['note_text']}
            shape = anchor_shape(rng, shape, anchor(neighbour['state']))
        elif ident in twins and len(built[twins[ident]]['own']['dates']) < 2:
            # Two assets examined on one visit day with one result: the line is the same and so is its date.
            twin = built[twins[ident]]['own']
            shape = anchor_shape(rng, dict(shape, tag=built[twins[ident]]['shape']['tag']), anchor(twin))
        built[ident] = build_record(rng, 'R%02d' % (ident + 1), shape, topic, modes.get(ident),
                                    mechanisms.get(ident), neighbour, twin)
    records = [built[ident] for ident in range(8)]

    def world(field):
        base, unknowns = {}, {}
        for record in records:
            value, ident = record[field], record['id']
            base['done:' + ident] = bool(value['count'])
            base['passed:' + ident] = value['passed']
            base['repeat:' + ident] = value['count'] == 2
            dates = value['dates']
            if len(dates) == 2:
                unknowns['date:' + ident] = dates
            else:
                base['date:' + ident] = dates[0] if dates else None
        return {'base': base, 'unknowns': unknowns}

    actual, told = world('own'), world('note')
    sources = {'procedure.md': document('Merewick Facilities Office — Examination procedure', [
        section('P-00', 'Issue and purpose',
                'Issued by operations manager Lena Venn on 2033-01-08. This procedure governs the annual '
                'review of depot access equipment. It distinguishes the date an examination took place from '
                'the date its paperwork reached the office. Inspectors retain the asset label with their '
                'worksheet so that a copied appointment cannot substitute for an examination of that asset.'),
        section('P-01', 'Reading the working files', '\n\n'.join(RULES)),
        section('P-02', 'Review administration',
                'The review concerns the recorded examination history, not permission to put equipment back '
                'into service. Repair orders and local isolation instructions remain with the depot supervisor. '
                'For each inspection, reviewers may compare completion with the three checkpoints listed in '
                'its working file. Those dates belong to the review timetable; none is an examination booking. '
                'The archive copy of a working file retains each of its dated entries so that staff can trace '
                'how a worksheet reached the file.')])}
    register_sections = [section('G-00', 'Circulation',
        f'Merewick Facilities Office. Prepared by appointments clerk {clerk} on 2035-03-01 for the annual '
        'equipment review. This index helps the review secretary locate the eight working files. Each Status '
        'note was copied into the index by hand on the transcription date given beside it; the index was '
        'assembled for circulation without another pass over those entries. Supervisors use the location and '
        'asset descriptions to route questions to the right technician. Booking changes are handled by the '
        'appointments desk, while examination worksheets are retained by the records desk. The two desks use '
        'the same inspection reference on correspondence.')]
    for record in records:
        ident, shape = record['id'], record['shape']
        location, asset, parts = ASSETS[int(ident[1:]) - 1]
        filename = 'record-%02d.md' % int(ident[1:])
        register_sections.append(section('G-' + ident, location,
            f"Inspection {ident} — {asset}; working file {filename}. "
            f"Transcribed {record['transcribed'].isoformat()}. Status note: {record['note_text']}"))
        texts = [entry_text(rng, record, position) for position in range(shape['entries'])]
        if len({text.split('. ', 1)[1] for text in texts}) < len(texts):
            raise Retry('two entries of one file say the same words')
        entries = '\n\n'.join(texts)
        sources[filename] = document('Merewick Facilities Office — Inspection ' + ident, [
            section('F-01', 'Assignment and review checkpoints',
                f"Opened by technician {shape['opener']} on {record['opened'].isoformat()} for {location}. "
                f"Inspection {ident} concerns the {asset}, specifically {parts}. The supervisor requested "
                'an operational examination after routine maintenance, and the appointments diary shows a '
                f"booking for {record['booking'].isoformat()}. The technician was to check the asset label "
                'before recording travel, closure and manual release. Access arrangements and repair work are '
                'separate from the examination itself. The dated entries below record whether an examination '
                'finished and whether the operating checks met the standard. The working file stays with the '
                'records desk until the annual review.\n\n'
                'Review checkpoints: ' + ', '.join(c.isoformat() for c in shape['checkpoints']) + '.'),
            section('F-02', 'Dated accounts', entries),
            section('F-03', 'File circulation', shape['routing'] or NO_ROUTING)])
    register_sections.append(section('G-09', 'Office routing', filing_log(rng) or NO_ROUTING))
    sources['register.md'] = document('Merewick Facilities Office — Examination register', register_sections)

    def make(record, positive, checkpoint, error=None):
        ident, topic = record['id'], record['topic']
        if topic == 'timing':
            text = f"Inspection {ident} was {'completed' if positive else 'not completed'} by the checkpoint of {checkpoint.isoformat()}."
            quantity = lambda w: w['done:' + ident] and w['date:' + ident] <= checkpoint
        elif topic == 'result':
            text = f"Inspection {ident} {'passed' if positive else 'did not pass'}."
            quantity = lambda w: w['passed:' + ident]
        else:
            text = f"Inspection {ident} was {'re-inspected' if positive else 'not re-inspected'}."
            quantity = lambda w: w['repeat:' + ident]
        filename = 'record-%02d.md' % int(ident[1:])
        cites = [('register.md', 'G-' + ident), (filename, 'F-01'), (filename, 'F-02'), ('procedure.md', 'P-01')]
        return claim('check_' + topic, text, lambda w: quantity(w) == positive, cites,
                     quantity, positive, error=error)

    slots = []
    for record in records:
        own, topic, shape = record['own'], record['topic'], record['shape']
        cp = shape['checkpoints'][shape['cp']]
        checkpoint = cp
        possible = outcomes(own, topic, checkpoint)
        if len(possible) > 1:
            checkpoint = rng.choice([c for c in shape['checkpoints'] if c != cp])
            possible = outcomes(own, topic, checkpoint)
        true = make(record, next(iter(possible)), checkpoint)
        mutations = []
        actual_values = outcomes(own, topic, cp)
        noted_values = outcomes(record['note'], topic, cp)
        for positive in (False, True):
            if actual_values == {positive}:
                continue
            error = ('settles_status_note_conflict' if actual_values != noted_values else
                     'settles_unrecorded_date' if len(actual_values) > 1 else 'reverses_' + topic)
            mutations.append(make(record, positive, cp, error))
        slots.append((true, mutations))
    return {'title': 'Merewick Facilities Office — Examination review',
            'sources': {name: text + '\nFiled by the Merewick Facilities Office.\n'
                        for name, text in sorted(sources.items())},
            'base': actual['base'], 'unknowns': actual['unknowns'], 'slots': slots,
            'told': told if actual != told else None, 'reader': 'believes_status_note', 'open_claims': 1}


def preview_reports(case, rng):
    """Mirror build_case's documented draw, on a private copy of its RNG state.

    This is a rejection check, not another source of key verdicts. Its agreement
    with the actual reports is tested. Keep in step with generate.build_case if
    that function changes its shuffle, mutation count or open-claim reservation.
    """
    preview = random.Random(0)
    preview.setstate(rng.getstate())
    slots = case['slots'][:]
    preview.shuffle(slots)
    correct = [true for true, _ in slots]
    usable, left_open = {}, {}
    for position, (_, alternatives) in enumerate(slots):
        states = [(item, generate.judge(item, case['base'], case['unknowns'])[0]) for item in alternatives]
        usable[position] = [item for item, state in states if state != 'supported']
        left_open[position] = [item for item, state in states if state == 'undetermined']
    wanted = max(2, round(len(slots) * preview.uniform(0.3, 0.5)))
    mixed = correct[:]
    openable = sorted(position for position in usable if left_open[position])
    reserved = preview.sample(openable, min(case['open_claims'], len(openable), wanted))
    for position in reserved:
        mixed[position] = preview.choice(left_open[position])
    for position in preview.sample(sorted(set(usable) - set(reserved)), wanted - len(reserved)):
        mixed[position] = preview.choice(usable[position])
    return correct, mixed


def build(rng):
    """Keep three of each polarity, without making any record immune to errors."""
    for _ in range(200):
        try:
            case = assemble(rng)
        except Retry:
            continue
        # With at most two conflicting records, three claims of either wording
        # ensure a matching control even when both conflicting records share it.
        # Apply the same condition to both reports, without examining echo labels.
        if all(3 <= sum(item['stated'] for item in report) <= 5
               for report in preview_reports(case, rng)):
            return case
    raise generate.GenerationError('attested: no balanced pair of reports was drawn')


_check_lengths()
