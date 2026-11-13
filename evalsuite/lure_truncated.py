"""A contract annex read through a silent, incomplete text view.

VIEW_LINES and VIEW_WIDTH are parameters of this test design, not measured limits
of any tool. The filter keeps the first VIEW_LINES physical lines of each source
and then discards lines longer than VIEW_WIDTH characters (excluding newlines).
The same parser reads the complete rendering and the filtered rendering. Missing
entries mean absence, not an unknown amount. No model or tool has been evaluated.

Reviewed rule sentences, reproduced verbatim in procedure.md:
Each document supplied for this review counts in full, from its first line to its last line.
The annex is the complete adopted schedule of termination penalties for the sites in this packet.
A site has a termination penalty if and only if the annex contains an entry setting a termination penalty for that site.
The amount of a site's termination penalty is the euro amount stated in that entry.
Each site has at most one such entry.
If the annex contains no such entry for a site, the annex sets no termination penalty for that site.
The phrase "sets the termination penalty at EUR X" asserts both that a penalty entry exists and that its amount is X euros.
The phrase "does not set the termination penalty at EUR X" is the negation of that assertion.
Line wrapping, entry order, and the date on which an entry was filed do not change its effect.

One term. The items that set a penalty are called entries in the rule sentences, the
claims, the filing note and the annex. The numbered operational items of the annex
are called items or instructions, never clauses; an earlier revision used "clause"
for both, so that a literal reader could take a "clause setting a termination
penalty" to mean only the numbered operational items.

Cause, citations and slips. The ordinary cause of the awkward layout is a retained
printer export: some items keep their single-line mail-merge form, and the returns
follow the main schedule at the back. A full read or a script avoids the error.
Every claim, lure or control, right or wrong, cites the same three passages: the
filing note, the whole annex and the procedure; opening the annex through the defined
view still omits the decisive text. Each of the twelve report slots pairs two sites,
one with a penalty entry in the annex and one with none (a true absence). A report
names one site per slot: six presence claims and six amount claims, three of each
polarity. Mutations swap the site for the other of the pair while keeping the
assertion and the amount, so they either attribute an entry to the site that has none
(attributes_clause_to_other_site) or overlook the named site's entry
(overlooks_site_clause). An absence assertion about a hidden entry is exactly the
view's answer, named echoes_truncated_view. (The error names keep the word clause
for compatibility with earlier keys; no document of the packet uses it.)

The annex. It reads as the printer export of one schedule of 478 to 573 numbered
items in seven travel groups (Part A to Part G) and a final part of returns filed
after circulation (Part H). Most items are operational instructions of nine kinds
(keys, meters, inventory, attendance, building log, security equipment, waste,
deliveries and parking, cleaning), each worded from varied sentence templates and
parameters (rooms, people, times, counts, days), and no instruction is repeated
verbatim. A commercial entry is an item that records the termination penalty
agreed for one site ("For site X, the agreed termination penalty is EUR N."). The
export keeps some items as one over-long line (mail-merge narrative); a blank line
separates items. The view ends on a paragraph boundary at line 2,000, so that a
cut in the middle of a sentence does not show that something is missing; the
returns start at line 2,002. Over seeds 1..1,300 the annex has 2,402 to 2,802 lines and
2 to 12 over-width lines.
Where an entry sits, and what the layout around it looks like, is drawn without
reference to whether the dossier holds a lure:
- Over seeds 1..1,300 every dossier hides 2 to 4 entries beyond the view and shows 12 to 16 inside it, and 12 to 18 sites
  have no entry. A hidden entry is wrapped in the returns two times in five, an over-long line inside the view
  two times in five, and an over-long line in the returns one time in five. Which of the hidden entries belong to a site named by a claim is
  the only thing the lure draw decides: the rest are entries for sites no claim
  names (decoys). Shown entries are likewise claimed entries plus decoys.
- The numbers of over-width lines (harmless narrative or entry), the lengths of
  those lines, the number of printed lines of a wrapped entry (drawn to equal that
  of the next ordinary instruction), the lengths of the file and its parts, and
  the number of entries in the view and beyond it have the same distribution in
  dossiers with and without a lure, for paragraphs with and without an entry, and
  inside and beyond the view (tests compare them by Kolmogorov-Smirnov distance
  and AUC).
- Every site is named in the programme account (a list of 32 sites, 24 of which
  the reports name) and many sites are named in instructions, so that "this site's
  name does not appear in what I can see" no longer marks a hidden entry. 12 to
  18 sites have no entry at all: true absences, the controls for absence claims,
  look from the view like sites whose entry lies beyond it.
- The commercial sentence of an over-long line starts at least VIEW_WIDTH
  characters into the line. A tool that clips an over-long line at that width, in
  place of dropping it, would therefore also miss the entry. Earlier the sentence
  started about 130 characters in, so that a clipping tool would have shown it. A
  clipping tool at a larger width than VIEW_WIDTH is not modelled.
The normal parameter range is 800 <= VIEW_LINES <= 2200 and 80 <= VIEW_WIDTH <=
3000; other settings raise ValueError rather than silently changing the design.

Measurement over seeds 1..100, indices 1..12: echo rows per twelve dossiers have
minimum 8, median 18.5, maximum 31 (1,824 rows in all: 1,068 in correct reports and
756 in altered reports). All 1,824 echo rows are definite (1,697 supported-to-refuted
and 127 refuted-to-supported); zero are undetermined. Over seeds 1..300 at index 1,
97/300 dossiers (32.3%) have no lure, 93 hide one claimed entry, and 110 hide two.
Why the count is bounded: each of the twelve sites is named once per report and
at most two claimed entries are hidden, so a dossier holds at most four echo rows. A hidden
entry with a positive claim echoes in the correct report and in an altered
report that leaves the claim unaltered; a hidden entry with a negative claim
echoes only in an altered report that alters its claim, and it is the only source
of absence claims that the flawed reader accepts. The draw hides no claimed entry in three
dossiers in ten, one in two in ten and two in five in ten; a coin
decides the polarity of the second hidden entry.

Remaining cues, all by construction, with their measured size (the claims of
seeds 1..1,300 for the first two, seeds 1..300 for the third):
1. Polarity. A hidden entry necessarily exists, so a positive claim is far more
   often a lure than a negative one. Over seeds 1..300: in correct reports, positive claims
   carry 283/1,800 lures (15.72%) and negative claims 0/1,800 (0%); in altered
   reports the rates are 173/1,800 (9.61%) and 30/1,800 (1.67%). AUC of polarity for being a lure over seeds 1..1,300: 0.732 pooled, 0.771 in correct reports and 0.680 in altered
   reports. Polarity does not decide a claim: in altered reports 40.3% of the positive claims and 40.6% of the negative claims are wrong. Given a lure, polarity fixes its direction. Text that is only lost
   can never turn a true absence into a false one, so this part cannot be removed for
   this reader. The first hidden entry is forced to have a positive claim, which keeps
   the lure count up (the coordinator's decision); a draw over all twelve entries
   would remove part of the cue. On the earlier layout the reviewer measured the
   median of echo rows per twelve dossiers falling from 19 to 14 and the suites
   below 15 rising from 19 to 54 of 100, and the pooled AUC from 0.736 to about
   0.657 (hand computation). On this layout the same change gives minimum 4, median 13, maximum 30,
   63 of 100 suites below 15, and 42.7% of dossiers without a lure (seeds 1..300), against a target
   of about three in ten. No other claim feature (slot, amount, amount
   band, site name rank and length, claim length) ranks lures against others inside a stratum of report, kind and polarity: the largest AUC deviation from 0.5 is 0.017 in the strata
   of correct reports, 0.030 in the positive strata of altered reports, and 0.074 among the 64 lures of negative amount claims of altered reports.
2. No entry in view. Every lure is a claim about a site with no entry in the view, so a
   claim about a site with an entry in view is never a lure. 11.7% of the claims about a site with no entry in the view are lures. Of the positive ones
   38.0% are lures (100% in correct reports, 18.7% in altered reports), and a claim about a site
   with no entry in view is either a true absence or a lure: only reading the whole annex tells which.
   Earlier a name search found the lure candidates (66% to 80% of the claims
   naming a site missing from the view were lures, and a count of entries in the view
   gave the lure away in 1,000 of 1,000 dossiers); now every name is in view.
3. Layout. The earlier constant layout (an over-long line of 2,637 characters if harmless
   and 2,810 to 2,817 with an entry; 36 or 38 non-blank lines in a fifty-line block)
   let a rule with constants identify lure dossiers in 500 of 500 (base rate 67.8%).
   The same rule, constants derived again from clean dossiers of seeds 1..500, scores 0.678 against
   base rate 0.678 on seeds 501..1000. Every layout total varies with the draw but not with the lure.
4. Place. No entry occupies a line from 1,879 to 2,010: shown entries end by line 1,878 and
   the first entry beyond the view starts at line 2,011. The 2 to 10 over-width lines in
   the view leave an item number missing from the numbering; the view shows no closing
   line and no part of the returns although the filing note says the schedule ends with them.
   All of this is the same in every dossier.
The tests measure these cues and held-out report, metadata and layout guessers; finite tests
cannot exclude every possible classifier. A guesser may beat the majority class only by four
standard errors of an accuracy over independent units; a ranking check (AUC) covers every
claim feature inside strata of report, kind and polarity and every report and layout feature
for the dossier target, so that a partial cue is seen even when no feature value is mostly lures.
Every report contains three claims of each polarity and kind, and every slot
is mutable. With at most two hidden claimed entries, each lure has a same-shape control
without reserving any always-correct slot. The title, the anchors and the citations of
every report are the same in all dossiers.

Limits: only fictional Markdown contract schedules, two closed-world claim
kinds, and one silent-view reader are varied. There are no unknown amounts,
withdrawals, conflicting entries, binary extraction failures, or loud errors.
The complete adopted schedule and the explicit negation rule remove intended
semantic ambiguity; humans could dispute the realism of that completeness
assumption, but not infer a different amount under these supplied rules. An
auditor who counts lines or reads by script is not affected. This family
separates tools more than models and stops working when a harness returns full
text or a reviewer checks the omitted region. It does not measure actual
harness limits or explain an auditor's reasons for matching an echo verdict.
A tool that clips an over-long line instead of dropping it is not modelled (see
above for the position of the commercial sentence). The count of lure claims is
bounded by design: no dossier holds more than four echo
rows, 23 of the 100 twelve-dossier suites (seeds 1..100) hold fewer than 15 echo
rows, and a staged run reads one report, which holds on average 11 (correct) or 8
(altered) of the 18 per twelve dossiers. The absence direction (refuted to
supported) is rare: 27 of the 100 suites hold no such row, and the others hold
1 to 5.
"""

import re
import textwrap

from generate import claim, document, section


VIEW_LINES = 2000
VIEW_WIDTH = 2000

RULES = (
    'Each document supplied for this review counts in full, from its first line to its last line.',
    'The annex is the complete adopted schedule of termination penalties for the sites in this packet.',
    'A site has a termination penalty if and only if the annex contains an entry setting a termination penalty for that site.',
    "The amount of a site's termination penalty is the euro amount stated in that entry.",
    'Each site has at most one such entry.',
    'If the annex contains no such entry for a site, the annex sets no termination penalty for that site.',
    'The phrase "sets the termination penalty at EUR X" asserts both that a penalty entry exists and that its amount is X euros.',
    'The phrase "does not set the termination penalty at EUR X" is the negation of that assertion.',
    'Line wrapping, entry order, and the date on which an entry was filed do not change its effect.',
)

# These notes are working prose, not a second list of rules about entry truth.
FILING_NOTE = """The estates team has brought together the adopted papers for the winter
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

HANDOVER_NOTE = """The facilities desk prepared this account of the handover programme for the
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

SITES = ('Alder', 'Aspen', 'Birch', 'Bramble', 'Cedar', 'Clover', 'Damson', 'Dovetail',
         'Elder', 'Elmstead', 'Fennel', 'Foxglove', 'Garnet', 'Hawthorn', 'Hazel', 'Iris',
         'Ivy', 'Juniper', 'Kestrel', 'Larch', 'Maple', 'Nutmeg', 'Orchid', 'Plover',
         'Quince', 'Rowan', 'Sorrel', 'Teasel', 'Umber', 'Violet', 'Willow', 'Yarrow')

SITE_LIST = ('The programme covers {} sites: {}, and {}. The desk arranged a handover visit for every one of '
             'them. Some of these sites have a commercial entry in the annex and some do not.').format(
                 len(SITES), ', '.join(SITES[:-1]), SITES[-1])

POOLS = {
    'part': ('main entrance', 'east stair lobby', 'boiler room', "caretaker's office",
             'ground-floor meeting room', 'first-floor store', 'basement archive', 'roof plant room',
             'loading bay', 'staff kitchen', 'bicycle store', 'reception desk'),
    'holder': ('the local contact', 'the site caretaker', 'the building manager', 'the security desk',
               'the facilities coordinator', 'the reception officer'),
    'role': ('the visiting officer', 'the estates coordinator', "the incoming operator's representative",
             'the records assistant', 'the local contact'),
    'utility': ('electricity', 'gas', 'water', 'heating', 'broadband'),
    'location': ('the riser cupboard on the first floor', 'the plant room', 'a locked cupboard beside the lifts',
                 'the basement corridor', 'the yard behind the kitchen', "the porter's lodge"),
    'item': ('desks', 'portable heaters', 'filing cabinets', 'whiteboards', 'stacking chairs',
             'fire extinguishers', 'visitor badges', 'lockers', 'monitors', 'trolleys'),
    'time': tuple('{:02d}:{:02d}'.format(h, m) for h in range(8, 17) for m in (0, 30)),
    'n': tuple(str(n) for n in range(2, 15)),
    'day': ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'),
    'route': ('northern', 'central', 'eastern', 'southern', 'western', 'coastal'),
}
POOLS['part2'], POOLS['item2'], POOLS['time2'], POOLS['n2'] = (
    POOLS['part'], POOLS['item'], POOLS['time'], POOLS['n'])

# Each kind of operational instruction: a title and three lists of sentence templates
# (openers, middles, closers). A paragraph takes one opener, one middle and, half the
# time, one closer, with parameters drawn from POOLS.
KINDS = (
    ('Key custody', (
        'Keys for the {part} are collected from {holder} at {time} on the day of the visit.',
        'On arrival, {role} asks {holder} for the keys to the {part} and the {part2}.',
        '{holder} keeps the keys to the {part} and hands them over at {time}.'), (
        'A signed receipt is placed in the visit note and a copy is left with {holder}.',
        'The keys are counted against the register before and after the visit; the register lists {n} keys.',
        'Spare keys stay in the {part2} unless {role} asks for them in writing.'), (
        'Any key not returned by {time2} is reported to the estates coordinator the same day.',
        'No key is posted; each is handed over in person.',
        'The register is initialled at each hand-over.')),
    ('Meter readings', (
        'Readings of the {utility} meter are taken by {role} on the day of the visit.',
        'The {utility} meter is in {location} and is read at {time}.',
        '{role} records the {utility} reading before any equipment is switched off.'), (
        'A photograph of the display is stored with the reading when the light allows it.',
        'The reading is written in the visit note with the meter number and the time.',
        'Where the meter cannot be reached, the reason is entered instead of a reading.'), (
        'The accounts team uses the readings later for utility reconciliation.',
        'A second reading is taken at {time2} if the first is unclear.',
        'Readings are entered in figures and never rounded.')),
    ('Equipment inventory', (
        'The inventory for the {part} lists {n} {item} and {n2} {item2}.',
        '{role} checks the {item} in the {part} against the floor plan.',
        'Items in the {part} are counted by {role} together with {holder}.'), (
        'Any {item} taken out of use is marked with a coloured label.',
        'Differences from the previous inventory are written in the visit note.',
        'Equipment belonging to the outgoing operator is listed separately.'), (
        'The marked floor plan is attached to the inventory.',
        'Items not found are listed with the name of the person asked.',
        'The list is signed by {role} before the team leaves.')),
    ('Visit attendance', (
        'A member of the local team attends the visit on {day} from {time} to {time2}.',
        '{holder} brings the sign-in book and meets the visiting team at the {part}.',
        'The visit starts at the {part} and follows the route on the floor plan.'), (
        'Visitors sign the book on arrival and again on leaving.',
        'The group does not split up unless {holder} agrees.',
        'Areas that cannot be entered are recorded with the reason given.'), (
        'If the visit has to move, the local team gives at least {n} working days of notice.',
        'The visit note is prepared while those present can still describe each room.',
        'A return visit is booked when the {part} cannot be opened.')),
    ('Building log', (
        'The building log stays in the {part} until the incoming operator accepts custody.',
        'Records in the building log are not copied into other papers during the handover.',
        'The log for the {part} is stored in a labelled cabinet.'), (
        'The log records contractor visits, minor defects and checks of emergency lighting.',
        'Pages are numbered, and a missing page is reported to {holder}.',
        'A photocopy of the last {n} pages is made for the facilities archive.'), (
        'Only {holder} may remove the log from the {part}.',
        'The incoming operator is shown where the log is kept.',
        'The log is returned to its cabinet at the end of each day.')),
    ('Security equipment', (
        'The incoming operator receives a demonstration of the alarm panel at {time}.',
        'The demonstration covers the locking sequence for the {part} and the contact list for faults.',
        '{holder} shows the camera monitor in the {part} to the incoming operator.'), (
        'Codes are exchanged through the security desk and are not written in the visit note.',
        'The visit note records that the demonstration took place, without the code itself.',
        'A postponed demonstration is booked separately and does not delay the rest of the visit.'), (
        'Faults found during the demonstration are reported to the security desk at once.',
        'Door sensors in the {part} are tested by {holder}.',
        'The contact list is checked against the last circulated copy.')),
    ('Waste and recycling', (
        'Waste from the {part} is cleared by {holder} on {day} before the visit.',
        'Bins in the {part} are emptied and {n} recycling bags are left at the {part2}.',
        'Confidential paper from the {part} is collected in sacks by the local team.'), (
        'Sacks are sealed and labelled with the room name.',
        'The collection firm calls between {time} and {time2}.',
        'Bulky items are not left in the {part2}.'), (
        'A receipt from the collection firm is attached to the visit note.',
        'Hazardous items are reported to the estates coordinator, not binned.',
        'Anything found after the collection is listed in the work list.')),
    ('Deliveries and parking', (
        'Visitors park in the bays near the {part}; {n} bays are reserved on {day}.',
        'Deliveries for the incoming operator arrive at the {part} between {time} and {time2}.',
        'The yard gate is opened by {holder} at {time}.'), (
        'Vehicles are not left in the loading bay for more than {n} minutes.',
        'A list of expected deliveries is given to the reception desk the day before.',
        'Parking permits are issued by {holder} on request.'), (
        'Blocked bays are reported to the building manager.',
        'The gate is locked again by {holder} after the last delivery.',
        'Visitors without permits use the street.')),
    ('Cleaning and condition', (
        'The {part} is cleaned by the outgoing operator before the visit on {day}.',
        '{role} notes the condition of the {part} on the visit form.',
        'Marks on walls in the {part} are listed but not repaired.'), (
        'Photographs are taken only for the facilities archive.',
        'Damage found is described in plain words and located on the floor plan.',
        'The condition survey is a later exercise and does not form part of this schedule.'), (
        'Cleaning contractors are told to leave the {part2} untouched.',
        'Any dispute about condition is passed to the estates coordinator.',
        'The completed form is filed with the visit note.')),
)
SITE_MENTIONS = (
    'At {site}, the {part} is reached through the {part2}.',
    'At {site}, {holder} is the usual contact for the {part}.',
    'At {site}, the {utility} meter is in {location}.',
    'The visit to {site} is normally planned for {day} morning.',
    'At {site}, the {part} has no separate lock.',
)
# The sentences of a commercial entry. Wording varies by draw, never by position.
ENTRY_INTROS = (
    'Entry filed by the contracts office on {d} October 2034.',
    'Entry recorded by the contracts office on {d} October 2034.',
    'Entry received by the contracts office on {d} October 2034.',
)
ENTRY_ROUTES = (
    'The visit was grouped with {n} others on the {route} travel route.',
    'The site manager asked for the visit to be arranged on the {route} travel route.',
    'The visit was scheduled for the {route} travel route after consultation with the local team.',
)
ENTRY_PENALTY = 'For site {site}, the agreed termination penalty is EUR {amount}.'
ENTRY_CLOSES = (
    'The site manager confirmed the entry at the adoption meeting.',
    'The entry was read out at the adoption meeting without amendment.',
    'The finance liaison noted the entry for the account worksheet.',
)


def view(text):
    """Apply both limits to original physical lines, without renumbering first."""
    return '\n'.join(line for line in text.splitlines()[:VIEW_LINES]
                     if len(line) <= VIEW_WIDTH)


def facts(sources):
    """Read entries, not the proposed report or the original construction data."""
    joined = ' '.join(' '.join(sources.values()).split())
    return {site: int(amount) for site, amount in re.findall(
        r'For site (\w+), the agreed termination penalty is EUR (\d+)\.', joined)}


def prose(text):
    return '\n\n'.join(textwrap.fill(' '.join(p.split()), 76)
                       for p in text.strip().split('\n\n'))


def wrap(text):
    """Greedy wrap at the printer width; much cheaper than textwrap over 2,800 lines."""
    width, lines, line = min(76, VIEW_WIDTH), [], ''
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = line + ' ' + word if line else word
    return lines + [line] if line else lines


def fill(rng, template):
    """Fill each {name} placeholder with a draw from its pool. A second draw (part2, item2,
    n2) differs from the first; time2 is later than time. A sentence starts with a capital."""
    drawn = {}

    def draw(match):
        name = match[1]
        pool = POOLS[name]
        if name == 'time2' and 'time' in drawn:
            pool = [t for t in pool if t > drawn['time']] or pool
        value = rng.choice(pool)
        while name[-1] == '2' and name != 'time2' and value == drawn.get(name[:-1]):
            value = rng.choice(pool)
        drawn[name] = value
        return value
    text = re.sub(r'\{(\w+)\}', draw, template)
    return text[0].upper() + text[1:]


def instruction_sentences(rng, kind=None, closer=None):
    """Two or three sentences of one kind of operational instruction, sometimes naming a site."""
    title, openers, middles, closers = kind or rng.choice(KINDS)
    parts = [rng.choice(openers), rng.choice(middles)]
    if closer if closer is not None else rng.random() < .75:
        parts.append(rng.choice(closers))
    if rng.random() < .2:
        parts.insert(rng.randrange(1, len(parts) + 1), rng.choice(SITE_MENTIONS).replace(
            '{site}', rng.choice(SITES)))
    return title, [fill(rng, part) for part in parts]


def entry_sentences(rng, site, amount):
    """The sentences of one commercial entry: the same wording whether it is shown or not."""
    day = str(rng.randrange(2, 20))
    sentences = [rng.choice(ENTRY_INTROS).replace('{d}', day)]
    if rng.random() < .4:
        sentences.append(instruction_sentences(rng, closer=False)[1][0])
    if rng.random() < .5:
        sentences.append(fill(rng, rng.choice(ENTRY_ROUTES)))
    sentences.append(ENTRY_PENALTY.format(site=site, amount=amount))
    sentences.append(rng.choice(ENTRY_CLOSES))
    return sentences


def narrative(rng, entry, minimum, target):
    """One over-long mail-merge line. A commercial entry in it starts only after `minimum`
    characters, so that a reader who clips the line at that width does not see it either."""
    text = 'Handover narrative.'
    while len(text) < minimum:
        text += ' ' + ' '.join(instruction_sentences(rng)[1])
    if entry:
        text += ' ' + ' '.join(entry_sentences(rng, *entry))
    while len(text) < target:
        text += ' ' + ' '.join(instruction_sentences(rng)[1])
    return text


def build(rng):
    if not (800 <= VIEW_LINES <= 2200 and 80 <= VIEW_WIDTH <= 3000):
        raise ValueError('Use 800..2200 view lines and 80..3000 view characters')
    # How many claimed entries are hidden decides the lure. Nothing else below depends
    # on it: the number of hidden and shown entries, their kinds, the number of
    # over-width lines and the size of the file are drawn without reference to it.
    # Three dossiers in ten hide no claimed entry; of the rest, two in seven hide one
    # and five in seven hide two.
    number = rng.choice((0, 0, 0, 1, 1, 2, 2, 2, 2, 2))
    sites = list(SITES)
    rng.shuffle(sites)
    claimed, spare = sites[:24], sites[24:]
    # Each report slot has a pair of sites: one has a penalty entry, the other has
    # none. Which of the two has the entry is random. Mutations name the other site of
    # the pair and keep the assertion. Three slots of each kind and polarity ensure a
    # control even if two are hidden; every slot can be mutated.
    entries = []
    for index in range(12):
        entries.append({'present': claimed[2 * index], 'absent': claimed[2 * index + 1],
                        'amount': rng.randrange(12, 99) * 100,
                        'kind': 'penalty_presence' if index < 6 else 'penalty_amount',
                        'positive': index % 6 < 3})
    hidden = []
    if number:
        hidden.append(rng.choice([i for i, e in enumerate(entries) if e['positive']]))
        if number == 2:
            # A coin decides the polarity of the second hidden entry. A hidden
            # entry with a positive claim echoes in the correct report as well; one
            # with a negative claim echoes only where its claim is altered, but it
            # is the only source of absence claims that the flawed reader accepts.
            rest = [i for i in range(12) if i not in hidden]
            second = rng.choice((True, False))
            hidden.append(rng.choice([i for i in rest if entries[i]['positive'] == second]))
    # Entries for sites that no claim names. Two to four entries are hidden in every
    # dossier, and the number of shown entries is twelve to sixteen, whether or not a
    # claimed entry is among the hidden ones: the claimed entries that are hidden are
    # replaced by decoys in the hidden places and decoys are added in the shown ones.
    hidden_total = rng.choice((2, 3, 4))
    decoys = [{'site': spare[i], 'amount': rng.randrange(12, 99) * 100} for i in range(hidden_total + rng.randrange(5))]
    order = list(range(len(decoys)))
    rng.shuffle(order)
    plan = [{'site': e['present'], 'amount': e['amount'], 'hidden': i in hidden} for i, e in enumerate(entries)]
    plan += [dict(d, hidden=n in order[:hidden_total - len(hidden)]) for n, d in enumerate(decoys)]
    for entry in plan:
        entry['mode'] = rng.choice(('width', 'width', 'late', 'late', 'late_long')) if entry['hidden'] else 'shown'
    # Layout. Part A to G hold the main schedule up to the last line of the view, which
    # ends a paragraph; Part H, the returns, lies wholly beyond it. Events are the
    # paragraphs that are not ordinary instructions: shown entries, over-wide lines
    # (entries or harmless narrative) and late entries.
    limit = VIEW_LINES
    main_events, late_events = [], []
    for entry in plan:
        if entry['mode'] == 'shown':
            main_events.append({'entry': entry, 'long': False})
        elif entry['mode'] == 'width':
            main_events.append({'entry': entry, 'long': True})
        else:
            late_events.append({'entry': entry, 'long': entry['mode'] == 'late_long'})
    main_events += [{'entry': None, 'long': True} for _ in range(rng.randrange(2, 7))]
    late_events += [{'entry': None, 'long': True} for _ in range(rng.randrange(0, 3))]
    header = ['# Adopted estates annex', '',
              'Oriole Estates Cooperative, Contracts Office, 20 October 2034.',
              'Schedule assembled for the winter handover programme.', '',
              '<a id="A-01"></a>', '## A-01 — Assembled schedule',
              'Printer export retained by the records office.', '']
    lines = list(header)
    counter = [0]
    used = set()

    def instruction(rng_, label, register=True):
        """An ordinary instruction of three to six printed lines that no other item repeats."""
        while True:
            title, parts = instruction_sentences(rng_)
            wrapped = wrap(label + title + '. ' + ' '.join(parts))
            key = ' '.join(wrapped)[len(label):]
            if len(wrapped) >= 3 and key not in used:
                if register:
                    used.add(key)
                return wrapped

    def paragraph(rng_, event):
        counter[0] += 1
        label = 'Item {:03d}. '.format(counter[0])
        if event is None:
            return instruction(rng_, label)
        entry = event['entry']
        if event['long']:
            text = narrative(rng_, entry and (entry['site'], entry['amount']), VIEW_WIDTH,
                             VIEW_WIDTH + 700 + rng_.randrange(0, 1000))
            return [label + text]
        # A wrapped entry has the number of printed lines of the next ordinary instruction, so
        # that the sizes of items say nothing about which of them hold an entry.
        size = len(instruction(rng_, label, register=False))
        for _ in range(500):
            wrapped = wrap(label + ' '.join(entry_sentences(rng_, entry['site'], entry['amount'])))
            if len(wrapped) == size:
                break
        return wrapped

    def schedule(events, start, stop, closing):
        """Give each event a target line, uniform over the part; the paragraph is emitted at the first
        opportunity at or after it."""
        for event in events:
            event['target'] = rng.randrange(start + 5, max(start + 6, stop - closing))
        return sorted(events, key=lambda e: e['target'])

    # Main schedule.
    rng.shuffle(main_events)
    pending = schedule(main_events, len(lines), limit, 130)
    parts = [len(lines) + round(n * (limit - len(lines)) / 7) + (rng.randrange(-10, 11) if n else 0) for n in range(7)]
    part = 0
    while limit - len(lines) >= 16:
        if part < 7 and len(lines) >= parts[part]:
            lines += ['', 'Part {0} — Travel group {0}'.format(chr(65 + part)), '']
            part += 1
        elif pending and pending[0]['target'] <= len(lines):
            lines += paragraph(rng, pending.pop(0)) + ['']
        else:
            lines += paragraph(rng, None) + ['']
    if pending or len(lines) > limit:
        raise ValueError('The main schedule did not settle at the view limit')
    lines += [''] * (limit - len(lines))
    # Returns, filed at the back.
    lines += ['', 'Part H — Returns filed after circulation', '']
    rng.shuffle(late_events)
    stop = limit + rng.randrange(400, 800)
    pending = schedule(late_events, len(lines), stop, 40)
    while len(lines) < stop or pending:
        if pending and pending[0]['target'] <= len(lines):
            lines += paragraph(rng, pending.pop(0)) + ['']
        else:
            lines += paragraph(rng, None) + ['']
    lines.append('End of schedule.')
    sources = {
        'annex.md': '\n'.join(lines) + '\n',
        'filing-note.md': document('Filing note for the estates packet', [
            section('F-01', 'Assembly and circulation', prose(FILING_NOTE))]),
        'programme.md': document('Facilities programme account', [
            section('H-01', 'Work arranged for handover', prose(HANDOVER_NOTE)),
            section('H-02', 'Sites in the programme', prose(SITE_LIST))]),
        'procedure.md': document('Commercial review procedure', [
            section('P-01', 'Authority and scope',
                    'Oriole Estates Cooperative, Contracts Office, 20 October 2034.\n'
                    'Procedure issued for review of the adopted estates annex.'),
            section('P-02', 'Reading the schedule', '\n'.join(prose(s) for s in RULES))]),
    }
    # The worlds hold the twenty-four claimed sites; decoy entries are not part of any claim.
    named = set(claimed)
    base = {'penalties': {s: a for s, a in facts(sources).items() if s in named}}
    filtered = {'penalties': {s: a for s, a in facts({n: view(t) for n, t in sources.items()}).items() if s in named}}
    if set(base['penalties']) != {e['present'] for e in entries} or \
            set(base['penalties']) - set(filtered['penalties']) != {entries[i]['present'] for i in hidden}:
        raise ValueError('The layout does not hide exactly the planned entries')
    cites = [('filing-note.md', 'F-01'), ('annex.md', 'A-01'), ('procedure.md', 'P-02')]

    def item(site, kind, positive, amount, error=None):
        if kind == 'penalty_presence':
            text = (f'The annex sets a termination penalty for site {site}.' if positive else
                    f'The annex contains no termination penalty entry for site {site}.')
            pred = lambda w: (site in w['penalties']) == positive
        else:
            text = (f'The annex {"sets" if positive else "does not set"} the termination penalty '
                    f'at EUR {amount} for site {site}.')
            pred = lambda w: (w['penalties'].get(site) == amount) == positive
        return claim(kind, text, pred, cites[:], formal=False, error=error)

    slots = []
    for index, entry in enumerate(entries):
        positive, kind, amount = entry['positive'], entry['kind'], entry['amount']
        true_site = entry['present'] if positive else entry['absent']
        wrong_site = entry['absent'] if positive else entry['present']
        error = ('attributes_clause_to_other_site' if positive else
                 'echoes_truncated_view' if index in hidden else 'overlooks_site_clause')
        slots.append((item(true_site, kind, positive, amount),
                      [item(wrong_site, kind, positive, amount, error)]))
    return {'title': 'Review of the estates annex', 'sources': sources, 'base': base,
            'unknowns': {}, 'slots': slots,
            'told': {'base': filtered, 'unknowns': {}} if filtered != base else None,
            'reader': 'sees_truncated_view'}
