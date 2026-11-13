"""Review assignments that require a short-name or staff-role lookup.

The named reader, needs_it_stated, retains assignments naming a person outright.
For a settled assignment reached through a short name or a role, it removes the
assignment from base and allows every person on the staff list instead. Genuine
open allocations retain both possibilities. One or two assignments need a bridge
in seven cases out of ten; the other cases have no such assignments. Depth is
drawn before claim polarity and independently of it. Both reports have four
release-review and four visit-review claims, with distinct orders as subjects.

Settling rule sentences, verbatim (procedure.md P-02):
An assignment established by following the definitions, staff lists and short
names in this packet is established by the packet.
No document needs to repeat that assignment in a single sentence.
Each order entry appoints exactly one reviewer for the activity named in that entry.
A person's name in a reviewer entry appoints that person.
A short name in a reviewer entry appoints whoever its definition identifies.
A role in a reviewer entry appoints the person holding that role on the staff list.
These substitutions apply successively until a person's name is reached.
The staff list contains every person eligible for these assignments.
An open desk entry appoints one of the two people named in the handover note;
either appointment remains possible independently for each such entry.
No other appointment, substitution or delegation applies to these orders.
A statement that a person is not the reviewer concerns that person alone;
it does not assert that the order has no reviewer.

The convenient document is the booking export. It preserves the booking officer's
route term, rather than expanding shared-desk names after the roster was filed.
The route is not a false assertion: the temptation is to stop at that export and
withhold a conclusion. The extra work is one lookup (alias or role), or two
(alias to role to person). This implements block 5 without arithmetic, class or
group-membership comparisons. Open allocations lack the allocation decision,
not a supplied but overlooked definition. Correct reports use a settled order;
an open mutation copies its statement to another order with the same activity.

Mutations swap the actual reviewer with the adjacent person in paired alphabetical
staff order, retaining the sentence's positive or negative form. On derived assignments
this is named misstates_derived_assignment; the reader fails to refute it. The
same reader also withholds correct claims, which have no mutation error name.

Measurements (generator version 4): seeds 1 through 100, indexes 1 through 12,
give 12 / 28 / 38 echo rows per twelve dossiers (minimum / median / maximum).
All 2,700 echo verdicts are undetermined; zero are definite. Their true verdicts
are 2,223 supported and 477 refuted. Seeds 1 through 300 at index 1 have 80
no-lure dossiers (26.67%). Every source filename and source byte length is fixed; both
reports have equal byte lengths within a dossier. Names, roles and short names
use two words and nine characters. That artificial regularity is visible but
does not identify a verdict. No order is claimed twice within a report. The
generator's usual minority of altered claims and all-supported correct report
remain base-rate cues. Report-only guessing is a finite empirical check, not a
proof against every classifier or against comparison with a separately supplied
answer report. Both variants must be audited separately.
Held-out guessers trained on seeds 1-150 and tested on 151-300 reached the following
accuracies and majority baselines, both using reports alone and adding filenames
and source byte/word lengths: altered report .503/.500, wrong claim .794/.794,
lure claim .860/.860, dossier with a lure .700/.700. These are rounded to three
places. The altered-report guesser gains one answer in 300 over its baseline;
thus the literal requirement of no observed advantage is missed by 0.33 percentage
points. Tests allow a prespecified three-percentage-point sampling tolerance.
The two reports each contain exactly two positive and two negative statements
of each kind; this balance is visible in all cases and reveals neither variant.

Limits: only reviewer assignment is varied, with two activities and four people,
fixed dates, no changing appointments, and no conflicts between documents.
Aliases and roles, including their composition, are the only bridge mechanisms.
All source documents are clean Markdown; retrieval, OCR and tool truncation are
not tested. The synthetic office uses standardized prose and equal-length names.
A careful human could question whether the stipulated office rules resemble real
practice, but no discretion about substitutions or delegation is left in these
rules. The independent parser checks the same intended reading, not all possible
human interpretations. Echo agreement does not prove a reader used this strategy.
The family stops separating auditors once they consistently follow the lookup
chain; no empirical model error rate is claimed.

Remaining cue, measured on seeds 1 to 1,000 (index 1): a claim about an order
whose reviewer is genuinely open appears only in altered reports, in 475 of
1,000 altered reports and in 0 of 1,000 correct ones, so its presence shows
that the report was altered. That claim is always altered and undetermined, and
its same-wording partner is never a lure (0 of 475). The other claims of such a
report are wrong at 0.321, against 0.410 in altered reports without one. No
claim is decided by this: the open claim itself is decided by reading its entry.
"""

from generate import claim, document, section


PEOPLE = ('Iona Vale', 'Mara Reed', 'Nora West', 'Theo Lake')
ROLES = ('duty lead', 'site lead', 'case lead', 'team lead')
DESKS = ('Fern desk', 'Lake desk', 'Pine desk', 'Reed desk')

RULES = (
    'An assignment established by following the definitions, staff lists and short '
    'names in this packet is established by the packet.',
    'No document needs to repeat that assignment in a single sentence.',
    'Each order entry appoints exactly one reviewer for the activity named in that entry.',
    "A person's name in a reviewer entry appoints that person.",
    'A short name in a reviewer entry appoints whoever its definition identifies.',
    'A role in a reviewer entry appoints the person holding that role on the staff list.',
    "These substitutions apply successively until a person's name is reached.",
    'The staff list contains every person eligible for these assignments.',
    'An open desk entry appoints one of the two people named in the handover note; '
    'either appointment remains possible independently for each such entry.',
    'No other appointment, substitution or delegation applies to these orders.',
    'A statement that a person is not the reviewer concerns that person alone; '
    'it does not assert that the order has no reviewer.',
)

CITES = [('routing.md', 'R-02'), ('contacts.md', 'C-02'), ('staff.md', 'S-02'),
         ('handover.md', 'H-02'), ('procedure.md', 'P-02')]


def render(entries, appointments, aliases, candidates):
    """Office documents have separate purposes; only routing names individual orders."""
    sources = {
        'routing.md': document('Bellweather Estates Cooperative — Review bookings', [
            section('R-01', 'Booking office, 20 October 2034',
                    'Prepared by the booking office for the monthly allocation check. The cooperative maintains '
                    'workshops and storage rooms for its members. Orders in this extract concern either the release '
                    'of a completed work package or the review of a site visit. A release reviewer checks the '
                    'handover material before the operations office sends it to the member. A visit reviewer checks '
                    'the survey notes before a separate team prepares a quotation. These are assignments to review '
                    'documents, not records that a review has already been performed. The extract was closed at noon '
                    'on the date above, after the booking office had entered the allocations for this batch.\n\n'
                    'The export retains the route term entered at booking. An officer who booked a person directly '
                    'entered the full name; an officer who booked a shared desk entered the desk name or the roster '
                    'role. The export does not expand those terms when the staff office files its roster. That '
                    'separation keeps the booking history legible when somebody later searches the desk mailbox. '
                    'It also explains why the same extract contains personal names and office route terms. The '
                    'contact sheet and staff list belong to this batch and were filed with the extract.\n\n'
                    'Order numbers identify separate work packages. Similar numbers do not identify the same '
                    'member, building or job. The booking office has arranged the entries by order number for '
                    'filing, not by the identity of the reviewer. Each entry below is the complete reviewer entry '
                    'for its named activity. The subsequent sentence records the administrative destination of '
                    'the working copy. It does not appoint a second reviewer or record completion of the work.'),
            section('R-02', 'Booking entries', '\n\n'.join(
                f'Order {ident}: {activity} reviewer entry: {token}. '
                'The working copy remains with the booking office until the review notes are returned; '
                'the member copy is held for dispatch with the completed work package.'
                for ident, activity, token in sorted(entries))),
            section('R-03', 'Filing note',
                    'The booking office keeps this extract with the contact sheet, staff list, handover note and '
                    'review procedure. A reviewer may return comments on paper or through the office mailbox. '
                    'The choice of return channel does not change the assignment. No return receipts are included '
                    'here, because this check concerns the allocations at booking rather than the progress of '
                    'individual reviews. Requests for a further copy should quote the order number and the '
                    'activity so that the records clerk can retrieve the correct work package.')]),
        'contacts.md': document('Bellweather Estates Cooperative — Desk contact sheet', [
            section('C-01', 'Records office, 20 October 2034',
                    'This sheet accompanies the October review bookings. The records office maintains short '
                    'names for shared mailboxes because members often address correspondence to a desk rather '
                    'than to an individual. A short name is an office routing name, not another employee and not '
                    'a committee. A desk entry may identify a person or a role whose holder '
                    'appears on the staff list. The form of an entry reflects how the mailbox was registered, '
                    'not the importance or urgency of the orders routed through it.\n\n'
                    'The definitions below apply to this batch. Capitalization and spacing are retained from '
                    'the booking system so that the clerk can search the mailbox history without changing '
                    'the recorded route. The office filed the sheet with the batch rather than printing the '
                    'full directory on every order. This copy contains all four short names used for named '
                    'desks in the booking system. The separate open desk allocation is described in the '
                    'handover note. It is an allocation filing label rather than one of these four short names.'),
            section('C-02', 'Desk definitions', '\n\n'.join(
                f'The short name {desk} means {target}.' for desk, target in sorted(aliases.items()))),
            section('C-03', 'Correspondence handling',
                    'Members may continue to use the desk address after receiving a named contact. The records '
                    'office files incoming correspondence against the order number and retains the route as '
                    'written on the envelope. This prevents a change in an address label from splitting a work '
                    'package across two folders. The address history is retained for filing purposes; it is '
                    'not a separate appointment record. Questions about spelling are handled by the records '
                    'office before a package is dispatched, and questions about the work itself go through '
                    'the booking office with the activity named in the subject line.')]),
        'staff.md': document('Bellweather Estates Cooperative — Review staff list', [
            section('S-01', 'Staff office, 20 October 2034',
                    'This is the staff office list for the October review batch. It covers the entire period '
                    'in which the entries in the booking extract were made. Each role below has one holder '
                    'throughout that period. There were no acting appointments, absences requiring a substitute '
                    'or changes of holder during the period. The role names identify duties in the review '
                    'office; they do not describe seniority in the cooperative or signing authority for '
                    'purchases. Release and visit work can be assigned directly to any person on this list.\n\n'
                    'The staff office sends the roster to the booking and records offices together so that '
                    'a desk definition using a role can be followed without a separate telephone inquiry. '
                    'The roster does not repeat the orders booked to each holder. Keeping the appointments '
                    'here avoids reproducing the same appointment on every working copy. The order entry '
                    'identifies which route was selected for the particular piece of work.'),
            section('S-02', 'Appointments', '\n\n'.join(
                f'The {role} is {person}.' for role, person in sorted(appointments.items()))),
            section('S-03', 'Personnel records',
                    'The four people named above form the complete eligible review staff for this batch. '
                    'The staff office holds contact details and training records separately; those records '
                    'are not needed to identify an appointment. This list makes no claim that an individual '
                    'has completed an assigned review. Work completion is recorded when review notes return '
                    'to the booking office. An order can therefore have a reviewer even though no review '
                    'notes or completion receipt are included in the allocation packet.')]),
        'handover.md': document('Bellweather Estates Cooperative — Allocation handover', [
            section('H-01', 'Operations office, 20 October 2034',
                    'This handover note accompanies the booking extract and records the arrangements for '
                    'allocations kept under the open desk label. The label is available in every booking '
                    'batch, including batches in which no order uses it. It holds a work package while '
                    'the operations office obtains the individual allocation decision. The note describes '
                    'the two eligible choices for that label and the state of the decision record at the '
                    'time this packet was assembled. It does not change allocations booked to a named '
                    'person or to a defined desk or role.\n\n'
                    'The operations office keeps a separate allocation decision for each order using the '
                    'label. A package is not allocated to both candidates jointly. The copy of the decision '
                    'that would identify the individual was not transferred with this packet. Staff may '
                    'request that copy later, but a request for a copy is not evidence of which person '
                    'was selected. This note is retained even when the batch has no open desk entries so '
                    'that the records office has a complete account of the available routing labels.'),
            section('H-02', 'Allocation record',
                    f'The open desk allocation is either {candidates[0]} or {candidates[1]}. '
                    'The individual allocation decision is not in the packet. Both choices remain possible '
                    'for each open desk entry, and no other supplied document narrows that choice.'),
            section('H-03', 'Transfer arrangements',
                    'The operations office transfers working copies in order-number sequence. A transfer '
                    'receipt records custody of the paper, not the reviewer appointed to read it. The '
                    'records clerk may receive several packages in one envelope without becoming their '
                    'reviewer. Correspondence about a missing decision should identify the order and '
                    'the activity. Until the decision copy is obtained, the office can describe the two '
                    'possible appointments but cannot choose one merely from the position of an entry '
                    'in the extract or from the order in which the names appear in this note.')]),
        'procedure.md': document('Bellweather Estates Cooperative — Allocation review procedure', [
            section('P-01', 'Review office, 20 October 2034',
                    'This procedure governs the review of the October allocation packet. The purpose of '
                    'the check is to identify who was appointed for each named activity, using the booking '
                    'extract and its accompanying office records. The check does not decide whether the '
                    'review was performed well, whether the underlying work was satisfactory, or whether '
                    'a member has accepted delivery. Those questions require other records and are outside '
                    'this allocation check. The procedure uses the recorded appointments throughout the '
                    'batch and does not infer new appointments from job titles or correspondence habits.'),
            section('P-02', 'Reading an assignment', ' '.join(RULES)),
            section('P-03', 'Review record',
                    'The reviewing officer records the order number, activity and person concerned in the '
                    'review report. Citations should include '
                    'the booking entries and the accompanying contact, staff and handover records, so '
                    'that a colleague can trace the assignment without requesting another copy of the '
                    'packet. The office retains these documents together with the report for the next '
                    'allocation check. An unresolved individual allocation should remain unresolved '
                    'in that record until the missing decision is supplied.')]),
    }
    return dict(sorted(sources.items()))


def build(rng):
    """Build from the supplied random source only; no files or external state are read."""
    count = rng.choice((0, 0, 0, 1, 1, 1, 2, 2, 2, 2))
    bridged = {4 * kind + rng.randrange(4) for kind in rng.sample(range(2), count)}
    depths = {i: rng.choice((1, 2)) for i in sorted(bridged)}
    polarities = []
    for _ in range(2):
        group = [False, False, True, True]
        rng.shuffle(group)
        polarities.extend(group)
    # The source vocabulary has fixed widths without padding or hidden formatting.
    appointments = dict(zip(ROLES, rng.sample(PEOPLE, len(PEOPLE))))
    roles_for = {person: role for role, person in appointments.items()}
    desk_people = dict(zip(DESKS, rng.sample(PEOPLE, len(PEOPLE))))
    # Both definitions are useful in the office directory, even when no order uses
    # a particular desk. Depth-two entries use only role-valued definitions.
    aliases = {desk: rng.choice((person, roles_for[person])) for desk, person in desk_people.items()}
    ids = [f'PO{n}' for n in rng.sample(range(100, 1000), 16)]
    # Preserve a direct, settled control of the very same wording as each lure,
    # even when the mixed report uses an open-order mutation in another slot.
    openable = [i for i in range(8) if i not in bridged and not any(
        i // 4 == j // 4 and polarities[i] == polarities[j] for j in bridged)]
    open_slot = rng.choice(openable) if rng.random() < 0.5 else None
    candidates = sorted(rng.sample(PEOPLE, 2))
    base, unknowns, entries, slots = {}, {}, [], []

    def statement(ident, activity, person, negative, error=None):
        return claim(activity + '_reviewer',
                     f'The {activity} reviewer for order {ident} is {"not " if negative else ""}{person}.',
                     lambda w: (w['reviewer:' + ident] != person) if negative
                     else (w['reviewer:' + ident] == person),
                     CITES, quantity=lambda w: w['reviewer:' + ident], stated=person, error=error)

    for i in range(8):
        ident, twin = ids[2 * i:2 * i + 2]
        activity = 'release' if i < 4 else 'visit'
        negative = polarities[i]
        actual = rng.choice(PEOPLE)
        neighbour = PEOPLE[PEOPLE.index(actual) ^ 1]
        said = neighbour if negative else actual
        # Keep the open mutation literally a copy to another order. The open
        # pair must contain its stated person, irrespective of sentence polarity.
        if i == open_slot:
            said = rng.choice(candidates)
            actual = PEOPLE[PEOPLE.index(said) ^ 1] if negative else said
            neighbour = PEOPLE[PEOPLE.index(actual) ^ 1]
        token = actual
        if i in bridged:
            if depths[i] == 1 and rng.randrange(2):
                token = roles_for[actual]
            else:
                # Each person has one alias. If two entries use the same alias,
                # they must use the same depth; their depths are chosen by the
                # definition actually rendered, rather than conflicting edits.
                token = next(desk for desk, person in desk_people.items() if person == actual)
                desired = roles_for[actual] if depths[i] == 2 else actual
                used = any(entry[2] == token for entry in entries)
                if not used:
                    aliases[token] = desired
        entries.append((ident, activity, token))
        base['reviewer:' + ident] = actual
        true = statement(ident, activity, said, negative)
        wrong_person = actual if negative else neighbour
        error = 'misstates_derived_assignment' if i in bridged else 'swaps_staff_neighbours'
        mutations = [statement(ident, activity, wrong_person, negative, error)]
        if i == open_slot:
            entries.append((twin, activity, 'open desk'))
            unknowns['reviewer:' + twin] = candidates[:]
            mutations.append(statement(twin, activity, said, negative, 'copies_assignment_to_other_order'))
        else:
            other = rng.choice(PEOPLE)
            entries.append((twin, activity, other))
            base['reviewer:' + twin] = other
        slots.append((true, mutations))
    told = {'base': dict(base), 'unknowns': dict(unknowns)}
    for i in sorted(bridged):
        key = 'reviewer:' + ids[2 * i]
        del told['base'][key]
        told['unknowns'][key] = list(PEOPLE)
    return {'title': 'Bellweather Estates Cooperative — Allocation review',
            'sources': render(entries, appointments, aliases, candidates),
            'base': base, 'unknowns': unknowns, 'slots': slots,
            'told': told if bridged else None, 'reader': 'needs_it_stated',
            'open_claims': 1 if open_slot is not None else 0}
