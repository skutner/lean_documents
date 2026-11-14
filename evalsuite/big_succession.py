"""A harbour authority's licence correspondence file: who held which berth, at what fee, on a given date.

The dossier is the file of some fifty mooring and quay licences over twelve years: about 490 to 530 letters, file notes and
committee extracts in date order (`correspondence-1.md` to `correspondence-6.md`, two years each, 384,000 to 410,000
characters) and a short `procedure.md`. The deciding facts are single sentences inside paragraphs of ordinary
harbour business; there is no table, no identifier per fact, and no passage is cited (every statement cites all
seven files). Answering means following identities through the file: a register note names only the berth and
the date of the letter that agreed the assignment; a firm, a berth or a person is named loosely, by a short form
or a description, and is resolved by an earlier passage; fees are given relative to an earlier fee or to another
berth; a correction changes an earlier entry as if it had always read so.

Shape of the code
    One event log is drawn from `rng` by a forward simulation (`World`), which applies each event to a true history
    as it goes and keeps every berth and party free of other events while a matter about it is open, so that file
    order is effective order. One interpreter (`replay`, `Hist`, `answer`, `state_on`) turns ANY list of events into berths,
    holders and fees, with the three flawed readers as the same rules with one rule replaced. `base` is
    `{'hist': legacy_replay(events)}`; each reader's `base` is `{'hist': legacy_replay(events, reader)}`; a claim's `pred` only
    compares an answer of the history with its stated value (`matches`, `answer`). The renderer (`Renderer`) writes
    the file from the log with the phrase inventory (`INVENTORY`: eight to ten wordings for every kind of event, the
    slot patterns, the name pools), choosing for each mention of a party a full name, a short form, a description
    or a role phrase, and records which it chose. Generation of one dossier takes well under a second.

A berth is named in a deciding passage by its number, as "the berth next to berth N" (its pair) or as "the berth held
by X" (the one berth X holds at that point); a party by a full name, a short form, "the only daughter of ...", "the
buyer of the sailmaking business", "the holder of berth N" or a role phrase. Decoy passages use the words of a decision
("a temporary landing permit was granted to ...", "paid", "awarded", "assigned") for things that decide nothing; they
are events of kind `decoy` with `decides: false`, listed in the passages map. Ordinary business uses the same verbs
about contracts, planning permissions and registers of vessels, with no party or berth of the file in them.

Events (kinds of passage, each in at least eight wordings)
    grant (also the regrant of a vacant berth), fee (an amount; a change "raised by a tenth"; "the same as", "half",
    "twice" the fee of another berth; "the same as the adjoining berth"; a share of the fee of the berth it was split
    from), pair, agree, register, withdraw, refuse, death, estate, rename, merge, surrender, forfeit, renumber, split,
    join, fix (a fee figure, the assignee of an agreement, the date of a register entry), kin ("the only daughter of
    Mr Hale") and bought (the sale of a business). An agreed assignment is registered (about 56%), never registered
    (13%), refused (13%) or withdrawn (18%).

Rules (all stated in `procedure.md`; the ones the six listed rules did not settle)
    A date is read at the end of its day; "between D1 and D2" counts dates after D1 up to and including D2; vacancy
    is a holder state ("nobody") and a change to or from it is a change of holder; a merged firm is a new holder;
    a fee entry runs from the year it names for the whole of that year, the entries of a berth are written in the
    order of their years, and the fee for a year is that of the last entry running from that year or an earlier one;
    a change is applied to the previous entry of the berth; a fee given by reference is fixed on the day it is
    written, and a correction of an entry applies to every fee derived from it; a date without a year is in the year
    of the heading of its document; "now" is the day the file closes; a party is named in statements by the first
    full name the file gives it, a berth by the first number it bore. No sentence about works, weather or
    committee business establishes anything. An effective day (a death, a "known since" renaming, a corrected
    register date) may precede its letter, but never overtakes another event on the same berth or party.

The flawed readers (the true rules with exactly one rule replaced; each resolves names and descriptions as the true
reading does)
    takes_agreements_as_transfers: an agreed assignment takes effect on the date of the letter that agrees it,
        whether or not it was ever registered (withdrawn and refused applications it still honours); the register
        note is then no event.
    does_not_follow_renaming: a name or number is what is written. A renamed firm is another holder from the day of
        the renaming: an entry that makes it a holder after that day (the renderer writes a renamed firm by its
        present name in every sentence that does so) gives the holder "Tern Marine", which is not "Halyard & Sons".
        An entry is credited to the berth number it is written under, so a renumbered berth is another berth: an
        entry written under a later number is not credited to the berth first numbered so, and "now forms part
        of" ends at the last number the written joins reach.
    ignores_corrections_and_withdrawals: a correction is not read (the entry stays as first written, and what was
        derived from it with it), and an application that was withdrawn or refused is granted: the assignee holds
        from the letter that agreed it.
    A reader's timeline never runs backwards: an entry written later on a berth does not take effect before one
    written earlier (the true reading never needs this rule; `Hist.clamped` counts it, and `build` asserts it is
    zero for the true reading).
    Where a reader's value cannot be stated (a holder that is "the renamed firm", a fee it cannot find) the true
    statement is still wrong for it; the slot simply has no statement in that reader's value, and uses plain wrong
    values (or another reader's value) as its mutations.

Statements (24 per report, every kind with three or four wordings used for true and false alike)
    holder on a date, fee for a year, berths held on a date, times a berth changed holder between two dates, the
    present berth a berth forms part of, first date a party held a berth. Slots are chosen from candidates computed
    on all four histories: at least five slots per reader whose mutation states that reader's value, seven controls
    (never fewer than six) on which all three readers are right, three of them settled by a single passage, and at least one "forms part of" statement (the mutations are plain wrong values), at least
    twelve that depend on a second passage, at least eleven that a search for the full name of the thing concerned
    does not settle; a berth or party appears in at most one statement. Statements the sources leave open do not
    occur (`unknowns` is empty).

What a checker can reach (extra keys of the case, ignored by `generate.build_case`)
    `events`: the log as JSON-serialisable dicts, ids "E001"..., with kind, date (of the document), `when` (the day
        it takes effect), berths and parties concerned, its values, `effect` ('state', 'none' for an agreement, a
        withdrawal or a refusal, 'identity', 'correction') and, on an agreement, its `outcome`.
    `passages`: {file, paragraph, event_ids, role, resolves}: the 0-based index of the blank-line-separated block of
        that source file and the canonical party or berth each non-full-name mention in it resolves to.
    `parties`, `berths`, `mentions` (the form each mention took), `sentences`, `now`, `candidates`.
    `needs` on an event: ids of the passages its wording leans on (the renaming behind a later name, the renumbering
        behind a later number, the merger behind a merged firm, the kin or sale passage behind a description, the
        passages behind "the holder of berth N"); `ref` is the agreement a register note, a withdrawal or a refusal is
        about, or the entry a correction corrects. `replay` reads a subset as the whole file: an event whose needs or
        ref are not all in the subset (or themselves left out) is LEFT OUT, and so is a fee entry whose base is not
        there (see `answer`). An event that merely changes state (a death, a merger) and is itself missing is simply
        not applied: nothing in the list says it was there.
    `answer(query, events, reader=None)` is the interface of the checked-answers checker: the value of a statement
        from ANY subset of the events; None when what the statement is about (the berth's first appearance, the
        party's first naming) is not in the subset; `NONE_VALUE` ('none') when it is established that there is no
        value (nobody holds the berth, no fee in force); never raises. A party is its first full name
        (`case['names']` lists every name, so a renamed firm is right under each; a berth number is its own key with only
        itself listed, so the present number of a berth is not read as an earlier one); a flagged holder of the reader
        that ignores renaming is the name followed by " [as renamed]". The rule for what a subset lacks is in the
        docstring of `answer` (what depends on a missing referent is left out of the reading; a subset is otherwise
        the whole record, so a missing correction, death or withdrawal simply is not applied). The generator draws
        its dossiers by the reading of the FULL list that it always used (`legacy_replay`; on a full list it gives
        the histories `replay` gives, which a test compares), so the rule changes no byte of a dossier.
        `value_form(query)` is integer, date or name;
        `relevant(query, events)` gives the ids of the subject's events and of what they refer to (a count is over
        every berth); the tests check that removing any event outside `relevant` changes no answer and that
        removing some event inside it does. `verify(seed, index, fixtures=None)` is in the test file, with the
        command line `python3 tests/test_evalsuite_big_succession.py --verify SEED INDEX [FIXTURES_DIR]`.
    `state_on(events, date, reader=None)` (pure; any subset, read as `replay` reads it; the berths the subset establishes) and
    `claim['query']` on every claim, e.g. {'kind': 'holder', 'berth': 'B07', 'label': '14', 'date': '2017-03-02',
    'value': 'P03', 'party_name': None}; `answer(history, query)` re-evaluates it.

Measured on seeds 1 to 5 by `tests/test_evalsuite_big_succession.py` (SUCCESSION_VERBOSE=1 prints them)
    370,000 to 405,000 characters; 29.2 to 50% of the statements of a mixed report are false (7 to 12 of 24; the
    generator's `choose` alters at least seven, 29.2%, so a 30% floor cannot be guaranteed from here); 20 or 21 of 24
    statements depend on a second passage by the test's definition and three are settled by one passage; four or
    more fees per dossier depend on another berth's figure and three to five statements are counts over the whole
    file; a search for the full name of the thing concerned misses a deciding passage for 17 to 22 of 24, a search
    by any word of its names or numbers for 10 to 15 of 24; 22 to 42% of the deciding passages (28% over the five
    seeds, 9% in the earlier design) name their subject by a description and 38 to 48% by no word of its names;
    a filter on the deciding verbs (awarded, paid, granted, assigned, registered, licence, corrected, ...) keeps 39
    to 42% of the paragraphs, of which 58 to 68% are in the passages map, and still misses a deciding passage for 5
    to 9 of 24 statements (the earlier design kept 28% with 83% in the map); a naive script (full-name search and a
    number pattern, scored on true statements) is wrong on 16 to 20 of the 20 or 21 statements that need a second
    passage; 2.5 to 4% of the bulk sentences are verbatim repeats; 76 of the 79 lure statements of the two bundled
    readers turn on one of their two shortcuts only. The round trip also holds on 194 further seeds (6 to 199, run
    once, not kept) and `--verify` passes on the draws (7,3), (12,2), (31,5) and (48,1).

Limits
    The wordings are a finite inventory, visible in the text: an agent that reads some twenty passages can write
    about two hundred patterns and recover every event, and the descriptions (kin, sale of a business, holder of a
    berth, role phrases) resolve by simple rules; what it cannot skip is the replay of corrections, withdrawals,
    renamings and relative fees in order. Dates fall on any weekday. The three readers are fixed programs; a
    reader that errs differently is not covered. A subset is read as the whole file, so a count of berths from a subset is a
    number (the berths it establishes that the party holds); `complete` in `state_on` says whether the subset also
    mentions a berth it never establishes. A statement can have an empty needed set (a count that two events give);
    see `EXEMPT_KINDS` in the test.
"""
import datetime
import re
from fractions import Fraction

import generate
from generate import claim

READERS = ('takes_agreements_as_transfers', 'does_not_follow_renaming', 'ignores_corrections_and_withdrawals')
R_AGREE, R_NAMES, R_FIXES = READERS
VACANT = 'vacant'
TITLE = 'Harbour authority licence correspondence review'

# ---------------------------------------------------------------------------------------------------------------
# Vocabulary. Every surname, firm stem and contractor name is unique across the pools, so a short form (a title
# and a surname, or the first word of a firm's name) never names two parties.
# ---------------------------------------------------------------------------------------------------------------

GIVEN_MALE = tuple('Arthur Barnaby Cedric Desmond Edmund Felix Gerald Hugh Ivor Jasper Kenneth Lionel Magnus Neville '
                   'Oswald Percival Quentin Rupert Stanley Trevor Ulick Victor Wilfred Alaric Bertram Clement '
                   'Dominic Emrys Fergus'.split())
GIVEN_FEMALE = tuple('Agnes Beatrice Cecily Dorothea Edith Florence Gwen Harriet Ines Joan Kathleen Lilian Marjorie '
                     'Nora Olive Phyllis Rosalind Sybil Thelma Una Vera Winifred Alma Blanche Clara Dilys Elspeth '
                     'Freda Greta Hilda'.split())
SURNAMES = tuple((
    'Hale Corry Abbot Aldred Ames Ashby Atkin Bagley Barlow Bassett Beaumont Bellamy Bickley Blythe Bowden Brannock '
    'Brindle Burrell Cadman Calloway Carrow Chadwick Clegg Colby Copley Cotter Crabbe Dacre Danby Dewhurst Dunmore '
    'Eddison Elkin Fairley Farrant Fenner Garside Gaunt Gilby Goodall Haddon Hallam Hardcastle Harker Hesketh '
    'Holloway Ibbotson Jessop Kendal Kirkby Lamport Latham Lockwood Maddox Marler Mowbray Naylor Netherton Orrell '
    'Oakley Padgett Pickard Prowse Rawdon Rimmer Rowntree Sedgwick Selby Tennant Thwaite Ulyatt Vickery Wetherby '
    'Whitlock Wragg Yardley Ainsley Bunting Cheswick Draycott Eastwood Foxley Grimshaw Hartley Ingram Jarrow '
    'Kenyon Langdale Moxon Nuttall Ormerod Pemberton Quilter Ratcliffe Sowerby Tunstall Upton Wainwright').split())
FIRM_STEMS = tuple((
    'Halyard Tern Cormorant Curlew Gannet Kittiwake Fulmar Petrel Skua Plover Godwit Whimbrel Dunlin Sanderling '
    'Turnstone Merlin Osprey Harrier Kestrel Heron Egret Bittern Grebe Teal Wigeon Pintail Shoveler Eider Scoter '
    'Goldeneye Bowsprit Capstan Binnacle Cleat Fathom Keelson Lanyard Mainsail Marlin Painter Rowlock Spindrift '
    'Starboard Thwart Windlass Gunwale Bulwark Fairlead Forestay Jibstay Mizzen Quarterdeck Ratline Sheerlegs '
    'Taffrail Tholepin Topsail Vang Wherry Yardarm Ballast Bilge Avocet Brambling Chough Corncrake Dipper '
    'Fieldfare Goosander Hobby Jackdaw Kingfisher Lapwing Linnet Mallard Nuthatch Pipit Quail Raven Siskin Swift '
    'Twite Wagtail Warbler Whinchat Yellowhammer Anchorage Bollard Buoy Cringle Davit Gaff').split())
FIRM_SUFFIXES = ('& Sons', '& Co', '& Partners', '& Daughters', 'Marine', 'Brothers', 'Ltd', 'Boatyard', 'Chandlers',
                 'Fisheries', 'Shipping', 'Charters', 'Ferries', 'Trawlers', 'Yachts', 'Sailing Club', 'Rowing Club',
                 'Angling Society', 'Boat Hire', 'Marine Services')
CONTRACTORS = tuple((
    'Redshank Marsh Lowther Pennine Cragside Moorland Fellside Stonebeck Eskdale Ravenglass Whinfield Tarnside '
    'Bracken Ghyll Heathfield Linton Muncaster Nethermoor Osmotherley Pickering Rannoch Saltburn Thornton Urswick '
    'Yewdale Ashdown Brantwood Coniston').split())
TRADES = ('chandlery', 'sailmaking', 'net-making', 'ferry', 'boatbuilding', 'fish-smoking', 'rope-walk', 'coal-merchant')
RELATIONS_MALE = ('son', 'nephew', 'brother')
RELATIONS_FEMALE = ('daughter', 'niece', 'sister')
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December')
MONTHS_SHORT = tuple(m[:3] for m in MONTHS)

# a phrase for a relative change of a fee: (words that start the phrase, sign, amount phrase, numerator, denominator)
RELATIVE_UP = ('raised', 'increased', 'put up', 'advanced')
RELATIVE_DOWN = ('reduced', 'cut', 'lowered', 'brought down')
RELATIVE_SIZES = (('a tenth', 1, 10), ('one tenth', 1, 10), ('ten per cent', 1, 10), ('a fifth', 1, 5),
                  ('one fifth', 1, 5), ('twenty per cent', 1, 5), ('a quarter', 1, 4), ('one quarter', 1, 4),
                  ('twenty-five per cent', 1, 4), ('five per cent', 1, 20), ('one twentieth', 1, 20),
                  ('an eighth', 1, 8), ('one eighth', 1, 8), ('fifteen per cent', 3, 20), ('a half', 1, 2))
RELATIVE = {}
for _verbs, _sign in ((RELATIVE_UP, 1), (RELATIVE_DOWN, -1)):
    for _verb in _verbs:
        for _size, _num, _den in RELATIVE_SIZES:
            RELATIVE[f'{_verb} by {_size}'] = [_sign * _num, _den]
# a multiple of another berth's fee
FRACTIONS = {'half': [1, 2], 'three quarters': [3, 4], 'one and a half times': [3, 2], 'twice': [2, 1],
             'double': [2, 1]}
PARENT_FRACTIONS = dict(FRACTIONS, **{'the same as': [1, 1]})

ONES = ('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen '
        'seventeen eighteen nineteen').split()
TENS = 'zero ten twenty thirty forty fifty sixty seventy eighty ninety'.split()


def words(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ('' if n % 10 == 0 else '-' + ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return ONES[n // 100] + ' hundred' + (' and ' + words(rest) if rest else '')
    rest = n % 1000
    head = words(n // 1000) + ' thousand'
    if rest == 0:
        return head
    return head + (' and ' if rest < 100 else ' ') + words(rest)


# ---------------------------------------------------------------------------------------------------------------
# The phrase inventory: every wording the sources and the statements use for the deciding facts. A slot is a name in
# braces; INVENTORY['slots'] says what text a slot can hold. The test parser reads only this and the rendered text.
# ---------------------------------------------------------------------------------------------------------------

_MONTH_RE = '(?:' + '|'.join(MONTHS) + ')'
_SHORT_RE = '(?:' + '|'.join(MONTHS_SHORT) + ')'
_ORD = r'\d{1,2}(?:st|nd|rd|th)'
_WORD = '(?:' + '|'.join(ONES[1:] + TENS[2:] + ['hundred', 'thousand', 'and']) + ')'
SLOTS = {
    'num': r'\d+[A-Z]?',
    'berth': r'(?:[Bb]erth \d+[A-Z]?|[Tt]he berth next to berth \d+[A-Z]?|[Tt]he berth held by [^.;:,]+?)',
    'berth2': r'\d+[A-Z]?',
    'berth3': r'\d+[A-Z]?',
    'label': r'\d+[A-Z]?',
    'year': r'\d{4}',
    'party': r'[^.;:,]+?', 'party2': r'[^.;:,]+?', 'hold1': r'[^.;:,]+?', 'hold2': r'[^.;:,]+?', 'hold': r'[^.;:,]+?',
    'name': r'[^.;:,]+?', 'old': r'[^.;:,]+?', 'trade': r'[a-z-]+', 'relation': r'[a-z]+',
    'role': r'[^.;:,]+?',
    'rel': '(?:' + '|'.join(sorted((re_ for re_ in RELATIVE), key=len, reverse=True)) + ')',
    'frac': '(?:' + '|'.join(sorted(FRACTIONS, key=len, reverse=True)) + ')',
    'pfrac': '(?:' + '|'.join(sorted(PARENT_FRACTIONS, key=len, reverse=True)) + ')',
    'amount': (r'(?:£\d{1,3}(?:,\d{3})+|£\d+|\d{1,3}(?:,\d{3})+ pounds|\d+ pounds|' + _WORD + r'(?:[ -]' + _WORD +
               r')* pounds)'),
    'count': r'(?:\d+|no|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve) berths?',
    'times': r'(?:\d+ times?|no times|once|twice|(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve) times?)',
}
_DATE = ('(?:\\d{4}-\\d{2}-\\d{2}|\\d{1,2} ' + _MONTH_RE + ' \\d{4}|' + _MONTH_RE + ' \\d{1,2}, \\d{4}|' + _ORD + ' ' +
         _MONTH_RE + ' \\d{4}|the ' + _ORD + ' of ' + _MONTH_RE + ' \\d{4}|\\d{1,2} ' + _SHORT_RE + ' \\d{4}|\\d{1,2} ' +
         _MONTH_RE + '|' + _ORD + ' ' + _MONTH_RE + ')')
for _name in ('date', 'date2', 'agreed', 'entered', 'entry', 'correct', 'd1', 'd2'):
    SLOTS[_name] = _DATE

EVENTS = {
    'grant': [
        '{party} has been granted a licence for {berth}.',
        'A licence for {berth} was granted to {party}.',
        '{berth} is let to {party} under a new licence.',
        'The Authority has let {berth} to {party}.',
        '{party} takes up {berth} under a licence granted this week.',
        'The Authority has awarded {party} the licence of {berth}.',
        'A licence for {berth} has been issued to {party}.',
        '{berth} has been allocated to {party}, who will hold it under licence.',
        'The Authority has licensed {berth} to {party}.',
        'The licence of {berth} now stands in the name of {party}.',
    ],
    'fee_abs': [
        'The annual fee for {berth} is set at {amount} from {year}.',
        'From {year} {berth} will carry an annual fee of {amount}.',
        'For {year} and after, the yearly fee on {berth} is {amount}.',
        'The fee payable each year for {berth} is fixed at {amount}, beginning with {year}.',
        '{berth} is to be charged {amount} a year from {year}.',
        'The committee fixed the annual fee of {berth} at {amount}, to apply from {year}.',
        'An annual fee of {amount} applies to {berth} starting in {year}.',
        'Starting in {year}, the licence fee for {berth} is {amount} a year.',
        'The yearly charge for the licence of {berth} becomes {amount} from {year}.',
        'From {year} the licence of {berth} costs {amount} a year.',
    ],
    'fee_rel': [
        'The annual fee for {berth} is {rel} from {year}.',
        'From {year} the fee on {berth} is {rel}.',
        'With effect from {year} the yearly fee of {berth} will be {rel}.',
        'The fee for {berth} has been {rel}, starting with {year}.',
        'The annual fee on {berth} is {rel} for {year} onwards.',
        'For {year} and later years the licence fee on {berth} is {rel}.',
        'The committee agreed that the fee of {berth} be {rel} from {year}.',
        'The existing yearly fee for {berth} is {rel}, to take effect in {year}.',
    ],
    'fee_same': [
        'The annual fee for {berth} is the same as that of berth {berth2} from {year}.',
        'From {year} {berth} pays the same yearly fee as berth {berth2}.',
        'For {year} onwards the fee on {berth} is to equal the fee on berth {berth2}.',
        'The fee of {berth} is fixed at the level of berth {berth2}, starting in {year}.',
        '{berth} is charged the same annual fee as berth {berth2}, beginning with {year}.',
        'The committee set the fee of {berth} equal to that of berth {berth2} for {year} onwards.',
        'Starting in {year}, the yearly fee on {berth} matches the fee on berth {berth2}.',
        'The licence of {berth} carries from {year} the fee that berth {berth2} carries.',
    ],
    'fee_frac': [
        'The annual fee for {berth} is {frac} the fee of berth {berth2}, from {year}.',
        'From {year} {berth} pays {frac} the yearly fee of berth {berth2}.',
        'For {year} onwards the fee on {berth} is fixed at {frac} the fee on berth {berth2}.',
        'Starting in {year}, the fee of {berth} will be {frac} what berth {berth2} pays.',
        'The committee decided that {berth} should pay {frac} the fee of berth {berth2} from {year}.',
        '{berth} is charged {frac} the annual fee of berth {berth2}, beginning with {year}.',
        'The licence fee on {berth} is to be {frac} that of berth {berth2} for {year} and after.',
        'In {year} and after, {berth} carries a fee of {frac} the fee on berth {berth2}.',
    ],
    'fee_adjoin': [
        'The annual fee for {berth} is the same as that of the adjoining berth from {year}.',
        'From {year} {berth} pays the same yearly fee as the berth adjoining it.',
        'For {year} onwards the fee on {berth} is to equal the fee on its adjoining berth.',
        'Starting in {year}, {berth} is charged what the adjoining berth is charged.',
        'The committee set the fee of {berth} equal to that of the adjoining berth for {year} onwards.',
        '{berth} is charged the same annual fee as the berth next to it, beginning with {year}.',
        'The licence of {berth} carries from {year} the fee of the berth alongside it.',
        'Starting in {year}, the fee of {berth} is the same as the fee of the berth it is paired with.',
    ],
    'fee_parent': [
        'The annual fee for {berth} is {pfrac} the fee of the berth it was split from, from {year}.',
        'From {year} {berth} pays {pfrac} the yearly fee of the berth it was split from.',
        'For {year} onwards the fee on {berth} is fixed at {pfrac} the fee of the berth from which it was split.',
        'Starting in {year}, the fee of {berth} will be {pfrac} what the berth it was split from pays.',
        'The committee decided that {berth} should pay {pfrac} the fee of its parent berth from {year}.',
        '{berth} is charged {pfrac} the annual fee of the berth it was divided from, beginning with {year}.',
        'The licence fee on {berth} is to be {pfrac} that of the berth it was split from for {year} and after.',
        'In {year} and after, {berth} carries, as its fee, {pfrac} the fee on its parent berth.',
    ],
    'pair': [
        'Berths {num} and {berth2} lie side by side and are paired as adjoining berths.',
        'Berth {num} adjoins berth {berth2}, and the two are treated as a pair.',
        'The surveyor confirms that berth {num} is the adjoining berth of berth {berth2}, and the reverse.',
        'Berths {num} and {berth2} are neighbours on the same pontoon and are recorded as adjoining each other.',
        'For the purposes of fees, berth {num} and berth {berth2} are adjoining berths.',
        'Berth {num} sits alongside berth {berth2}; each is the other\'s adjoining berth.',
        'The two berths {num} and {berth2} share a finger pontoon and count as adjoining berths.',
        'The Harbour Master records berth {num} as paired with berth {berth2}, the pair being adjoining berths.',
    ],
    'agree': [
        '{party} has agreed to assign the licence of {berth} to {party2}, and the parties have applied for the '
        'assignment to be entered in the register.',
        'By agreement between {party} and {party2}, the licence of {berth} is to pass from the former to the '
        'latter once the register has been altered, and the application to register it is enclosed.',
        '{party2} has agreed to take over {berth} from {party}, and both have applied for the change to be '
        'entered in the register.',
        'An agreement has been made under which {party} assigns the licence of {berth} to {party2}, and the '
        'application for registration accompanies this letter.',
        '{party} is to assign the licence of {berth} to {party2} by agreement, and the application to register '
        'the assignment is sent with this note.',
        'The licence of {berth} is to be assigned by {party} to {party2} under an agreement just signed, and '
        'registration has been applied for.',
        '{party} writes to say that an agreement has been signed with {party2} to assign the licence of berth '
        '{berth} to the latter, and asks for the assignment to be entered in the register.',
        'Please note the application of {party} and {party2}: the licence of {berth} is to be assigned by the '
        'first to the second, subject to its entry in the register.',
        'The assignment of {berth} from {party} to {party2} has been agreed between them, and the application '
        'to enter it in the register has been lodged.',
    ],
    'register': [
        'The assignment of {berth} agreed in the letter of {agreed} was entered in the register on {date}.',
        'On {date} the register was altered to record the assignment of {berth} agreed on {agreed}.',
        'Entry made in the register on {date}: the assignment of {berth} that was agreed on {agreed}.',
        'The Registrar entered the assignment of {berth}, agreed by letter of {agreed}, in the register on {date}.',
        'The assignment agreed on {agreed} for {berth} has now been entered in the register, the entry being '
        'dated {date}.',
        'Registration of the assignment of {berth} agreed on {agreed} was completed, and the entry bears the '
        'date {date}.',
        'The register shows, under date {date}, the assignment of {berth} that the parties agreed on {agreed}.',
        'With the entry made on {date}, the assignment of {berth} agreed on {agreed} took its place in the '
        'register.',
    ],
    'withdraw': [
        'The application to register the assignment of {berth} agreed on {agreed} has been withdrawn by the '
        'parties.',
        'The parties have withdrawn their application to enter the assignment of {berth} agreed on {agreed}.',
        'The assignment of {berth} agreed in the letter of {agreed} is not to go ahead, the application having '
        'been withdrawn.',
        'The parties have told the Registrar that they no longer wish the assignment of {berth} agreed on '
        '{agreed} to be entered.',
        'Withdrawal of the application to register the assignment of {berth} agreed on {agreed} was received '
        'this week.',
        'The application for the assignment of {berth} agreed by letter of {agreed} was withdrawn before the '
        'Registrar acted on it.',
        'Both parties asked for the application concerning {berth}, agreed on {agreed}, to be withdrawn.',
        'The application of {agreed} to register an assignment of {berth} has been withdrawn.',
    ],
    'refuse': [
        'The committee refused the application to register the assignment of {berth} agreed on {agreed}.',
        'The application to enter the assignment of {berth} agreed on {agreed} was refused by the committee.',
        'Registration of the assignment of {berth} agreed in the letter of {agreed} has been refused.',
        'The Registrar was instructed to refuse the assignment of {berth} agreed on {agreed}.',
        'The committee declined to allow the assignment of {berth} agreed by letter of {agreed}.',
        'The application of {agreed} to register an assignment of {berth} was turned down.',
        'The assignment of {berth} agreed on {agreed} is refused and is not to be entered in the register.',
        'After discussion the committee refused to register the assignment of {berth} agreed on {agreed}.',
    ],
    'death': [
        '{party} died on {date}, and {party2}, as executor, holds every licence the deceased held from that day.',
        'We record with regret that {party} died on {date}, and {party2}, the executor, takes over the licences from '
        'the date of death.',
        '{party2} writes as executor of {party}, who died on {date}, and holds the licences of the estate from that '
        'date.',
        'Following the death of {party} on {date}, the licences held pass to {party2}, the executor, from that date.',
        '{party} died on {date}, and the executor, {party2}, has notified the Authority and holds the deceased\'s '
        'licences from that day.',
        'The licences of {party}, who died on {date}, are held from the date of death by the executor, {party2}.',
        'The Authority learned this week that {party} died on {date}, and the executor is {party2}, who holds the '
        'berths concerned from that date.',
        '{party2}, executor of {party} (died {date}), has taken the licences of the estate into their name as from '
        'that day.',
    ],
    'estate': [
        'On {date} the estate was settled, and the licence of {berth} was entered in the name of {party}, the '
        'executor handing it over.',
        'The executor has handed the licence of {berth} to {party}, the entry in the register being made on '
        '{date}.',
        '{party} became the holder of {berth} on {date}, when the executor\'s holding came to an end.',
        'With the estate wound up, {berth} passed from the executor to {party} on {date}.',
        'The register shows {berth} in the name of {party} from {date}, the executor having made it over.',
        'As from {date} the licence of {berth} belongs to {party}, to whom the executor transferred it.',
        'The executor\'s holding of {berth} ended on {date}, when the licence was entered in favour of {party}.',
        'On {date} the licence of {berth} was transferred by the executor to {party} and so entered.',
    ],
    'rename': [
        '{name}, as {old} has been known since {date}, asks that its licences be entered under the new name.',
        '{old} has been known as {name} since {date}.',
        'The firm previously called {old} changed its name to {name} on {date}.',
        'Please note that, with effect from {date}, {old} trades as {name}.',
        '{name} is the new name of {old}, adopted on {date}.',
        'As from {date} the company formerly styled {old} is styled {name}.',
        '{old} became {name} on {date}, with no change in ownership.',
        'The change of style from {old} to {name} took effect on {date}.',
    ],
    'merge': [
        '{party} and {party2} merged on {date} to form {name}, which holds every licence either of them held from '
        'that day.',
        'With effect from {date}, {party} and {party2} are united as {name}, and the licences of both pass to the new '
        'company.',
        '{name} was formed on {date} by the merger of {party} and {party2}, and takes over their licences from that '
        'date.',
        'The Authority has been told that {party} and {party2} combined on {date} as {name}, which now holds the '
        'licences of both.',
        'As from {date} {party} and {party2} are one firm under the name {name}, holding all the licences they held '
        'before.',
        'On {date} {party} joined with {party2} to become {name}, and the new firm holds their licences from that day.',
        '{name} replaces {party} and {party2}, which merged on {date}, and holds their licences from the date of the '
        'merger.',
        'The merger of {party} with {party2} took effect on {date}, and the combined firm, {name}, holds both '
        'firms\' licences from then.',
    ],
    'surrender': [
        '{party} surrendered the licence of {berth} on {date}.',
        'The licence of {berth} was surrendered by {party} with effect from {date}.',
        'On {date} {party} gave up {berth}, and the berth is back in the Authority\'s hands.',
        '{party} has handed back the licence of {berth}, the surrender taking effect on {date}.',
        '{berth} was surrendered by {party} on {date} and is vacant.',
        'From {date} {berth} is vacant, {party} having surrendered the licence.',
        'The surrender by {party} of the licence of {berth} took effect on {date}.',
        '{party} wrote giving notice that the licence of {berth} ended on {date} by surrender.',
    ],
    'forfeit': [
        'The committee declared the licence of {berth} held by {party} forfeit for arrears, with effect from '
        '{date}.',
        'The licence of {berth}, held by {party}, was forfeited on {date}.',
        'Forfeiture of the licence of {berth} from {party} took effect on {date}.',
        'On {date} the Authority treated {party} as having lost the licence of {berth} through breach of its '
        'terms.',
        '{party} forfeited {berth} on {date}, and the berth is vacant.',
        'The Authority took {berth} back from {party} by forfeiture on {date}.',
        'From {date} the licence of {berth} is forfeit, {party} having failed to remedy the breach.',
        'The committee noted that {party} lost the licence of {berth} by forfeiture on {date}.',
    ],
    'renumber': [
        'Berth {num} was renumbered {berth2} on {date}.',
        'From {date} the berth formerly numbered {num} carries the number {berth2}.',
        'The Harbour Master has changed the number of berth {num} to {berth2}, with effect from {date}.',
        'On {date} berth {num} became berth {berth2} when the pontoons were renumbered.',
        'The mooring numbered {num} has been renumbered {berth2} as from {date}.',
        'As from {date} number {num} on the pontoon plan is replaced by number {berth2}, for the same berth.',
        'The renumbering of {date} gives berth {num} the new number {berth2}.',
        'Berth {num}, renumbered {berth2} on {date}, remains the same mooring.',
    ],
    'split': [
        'Berth {num} became {berth2} and {berth3} on {date}, and {hold1} holds {berth2} while {hold2} holds '
        '{berth3}.',
        'On {date} berth {num} was divided into {berth2} and {berth3}, with {berth2} held by {hold1} and {berth3} '
        'held by {hold2}.',
        'The pontoon works split berth {num} into {berth2} and {berth3} on {date}, {berth2} being held by {hold1} '
        'and {berth3} by {hold2}.',
        'From {date} there are two berths where berth {num} stood, {berth2} held by {hold1} and {berth3} held by '
        '{hold2}.',
        'Berth {num} ceased to exist as one berth on {date}, becoming {berth2}, held by {hold1}, and {berth3}, '
        'held by {hold2}.',
        'The licence position after the split of berth {num} on {date} is that {hold1} holds {berth2} and {hold2} '
        'holds {berth3}.',
        '{berth2} and {berth3} replace berth {num} from {date}, the first being held by {hold1} and the second by '
        '{hold2}.',
        'Berth {num} was split on {date} into {berth2} and {berth3}, and {berth2} is held by {hold1} while '
        '{berth3} is held by {hold2}.',
    ],
    'join': [
        'Berths {num} and {berth2} are let as one from {date}, numbered {berth3}, and {hold} holds the joined berth.',
        'From {date} berth {num} and berth {berth2} form a single berth numbered {berth3}, held by {hold}.',
        'On {date} the two berths {num} and {berth2} were joined under the number {berth3}, and the joined berth is '
        'held by {hold}.',
        'The joining of berths {num} and {berth2} took effect on {date}, the combined berth keeping the number '
        '{berth3} and being held by {hold}.',
        '{hold} holds the berth made by joining {num} and {berth2} on {date}, which is numbered {berth3}.',
        'As from {date} berths {num} and {berth2} are one berth, number {berth3}, with {hold} as holder.',
        'The pontoon between berths {num} and {berth2} was removed on {date}, and the single berth that results is '
        'numbered {berth3} and is held by {hold}.',
        'Berths {num} and {berth2} were combined on {date} into berth {berth3}, whose holder is {hold}.',
    ],
    'fix_amount': [
        'The figure given for {berth} in the fee entry of {entered} should read {amount}.',
        'Correction: the annual fee entered for {berth} on {entered} was wrong and is to be read as {amount}.',
        'The fee entry for {berth} dated {entered} contained a mistake, and it should have stated {amount}.',
        'Please correct the entry of {entered} for {berth} to {amount}, as the earlier figure was a slip.',
        'The fee shown for {berth} in the entry of {entered} is wrong and should read {amount}.',
        'A slip was made in the entry of {entered} about {berth}, whose annual fee should have been given as '
        '{amount}.',
        '{berth}: the sum written in the fee entry of {entered} is to be replaced by {amount}.',
        'The Clerk has found that the entry of {entered} for {berth} misstated the fee, and the right figure is '
        '{amount}.',
    ],
    'fix_assignee': [
        'In the agreement of {agreed} for {berth} the assignee should read {party}, not {party2}.',
        'Correction: the assignment of {berth} agreed on {agreed} was to {party}, and not to {party2} as first '
        'written.',
        'The letter of {agreed} about {berth} named {party2} as assignee in error, and the assignee is {party}.',
        'The assignee of the assignment of {berth} agreed on {agreed} is {party}, and the name {party2} was '
        'entered by mistake.',
        'The agreement of {agreed} concerning {berth} should name {party} as assignee, and {party2} was named '
        'by mistake.',
        'For {berth}, the letter of {agreed} wrongly gave {party2} as the assignee, who is in fact {party}.',
        'Please read {party} for {party2} as the assignee in the agreement of {agreed} about {berth}.',
        'The assignee under the agreement of {agreed} for {berth} was {party}, not {party2}, and the entry is '
        'corrected.',
    ],
    'fix_when': [
        'The register entry for {berth} dated {entry} should have been dated {correct}.',
        'Correction: the entry of {entry} in the register for {berth} is to be read as made on {correct}.',
        'The assignment of {berth} entered in the register on {entry} was in fact entered on {correct}.',
        'The register date of {entry} given for {berth} was a clerical error for {correct}.',
        'The date of the register entry for {berth} made on {entry} should be read as {correct}.',
        'The register entry for {berth} that bears the date {entry} was really made on {correct}.',
        'A clerical error put {entry} on the register entry for {berth}, and the entry was made on {correct}.',
        'The Registrar has corrected the date of the entry for {berth} from {entry} to {correct}.',
    ],
    'kin': [
        '{party} is the only {relation} of {party2} and will deal with the Authority for the family.',
        '{party}, the only {relation} of {party2}, called at the office this week.',
        'The Clerk met {party}, who is the only {relation} of {party2}.',
        '{party}, the only {relation} of {party2}, has been named as the person to write to.',
        'It was confirmed that {party} is the only {relation} of {party2}.',
        '{party} wrote on behalf of {party2}, whose only {relation} the writer is.',
        'The Harbour Master noted that {party} is the only {relation} of {party2}.',
        'An enquiry came from {party}, the only {relation} of {party2}.',
    ],
    'decoy': [
        'A temporary landing permit was granted to {party} for the week of the regatta.',
        '{party} was paid a small sum for the loan of a trailer during the regatta.',
        'The Authority awarded {party} a prize at the harbour open day.',
        '{party} was assigned a stall at the harbour market for the summer.',
        'A permit for one night alongside the visitors\' pontoon was issued to {party}.',
        'The register of visiting vessels was altered to show the name of {party}.',
        'Payment of the expenses claimed by {party} was approved by the committee.',
        'The minutes were corrected to show that {party} had sent apologies.',
        '{party} surrendered a set of keys to the harbour office after the works.',
        '{party} was granted leave to keep a tender alongside berth {num} for the regatta week.',
        'A visiting skipper was assigned berth {num} for one night at the invitation of {party}.',
        'The bill of {party} for repairs to the finger of berth {num} was paid in full.',
        'The Authority transferred a sum to {party} for the pontoon lights near berth {num}.',
    ],
    'bought': [
        '{party} has bought the {trade} business of {party2}.',
        'The {trade} business formerly run by {party2} was bought by {party}.',
        '{party} acquired the {trade} business from {party2}.',
        'From {party2}, {party} purchased the {trade} business.',
        '{party2} sold the {trade} business to {party}.',
        'The sale of the {trade} business by {party2} to {party} has been completed.',
        '{party} took over the {trade} business that {party2} had carried on.',
        'The {trade} business has changed hands, as {party2} sold it and {party} bought it.',
    ],
}
# A sentence that follows an event sentence in the same paragraph and names a party only by its role in that sentence.
FOLLOW_ROLES = {
    'assignee': ('the assignee', 'the incoming holder'),
    'assignor': ('the assignor', 'the outgoing holder'),
    'executor': ('the executor', 'the personal representative'),
    'deceased': ('the late holder', 'the deceased holder'),
    'firm': ('the firm', 'the renamed firm'),
    'merged': ('the merged firm', 'the new firm'),
    'holder': ('the new holder', 'the present holder'),
}
FOLLOW = [
    '{role} has been asked to confirm the details of the vessel on the mooring.',
    '{role} will be sent a copy of the harbour byelaws with the next circular.',
    '{role} has not yet returned the signed copy of the conditions of licence.',
    '{role} was reminded that the insurance certificate is due for renewal each spring.',
    '{role} may call at the office on any weekday morning to collect the new plate.',
    '{role} has been given the combination for the gate to the pontoon.',
]
# How a party may be named in a deciding passage, besides its full name or a short form.
DESCRIPTIONS = {
    'kin': 'the only {relation} of {anchor}',
    'kin_late': 'the only {relation} of the late {anchor}',
    'buyer': ('the purchaser of the {trade} business', 'the buyer of the {trade} business',
              'the new owner of the {trade} business'),
    'seller': ('the seller of the {trade} business', 'the former owner of the {trade} business'),
    'holder': ('the holder of berth {berth}', 'the licensee of berth {berth}', 'the present holder of berth {berth}',
               'the then holder of berth {berth}'),
}
STATEMENTS = {
    'holder': ['On {date}, berth {num} was held by {party}.',
               'The holder of berth {num} on {date} was {party}.',
               '{party} was the holder of berth {num} on {date}.',
               'As at {date}, the licence of berth {num} was held by {party}.'],
    'fee': ['The annual fee for berth {num} for {year} was {amount}.',
            'For {year}, berth {num} carried an annual fee of {amount}.',
            'In {year} the yearly fee on berth {num} was {amount}.'],
    'count': ['On {date}, {party} held {count}.',
              '{party} was the holder of {count} on {date}.',
              'At the end of {date}, {party} held {count}.'],
    'changes': ['Between {d1} and {d2}, the holder of berth {num} changed {times}.',
                'Berth {num} changed holder {times} between {d1} and {d2}.',
                'After {d1}, up to and including {d2}, the holder of berth {num} changed {times}.'],
    'part': ['Berth {num} now forms part of the berth presently numbered {label}.',
             'Berth {num} is today part of the berth now numbered {label}.',
             'The berth now numbered {label} includes berth {num}.'],
    'first': ['{party} first held berth {num} on {date}.',
              'The first date on which {party} held berth {num} was {date}.',
              'Berth {num} first passed into the hands of {party} on {date}.'],
}
DATE_FORMS = 8
TITLES = ('Mr', 'Ms', 'Mrs', 'Miss', 'Dr')

INVENTORY = {
    'events': EVENTS, 'follow': {'roles': FOLLOW_ROLES, 'templates': FOLLOW}, 'descriptions': DESCRIPTIONS,
    'statements': STATEMENTS, 'slots': SLOTS, 'relative': RELATIVE, 'fractions': FRACTIONS, 'parent_fractions': PARENT_FRACTIONS,
    'months': MONTHS, 'months_short': MONTHS_SHORT, 'titles': TITLES, 'given_names': GIVEN_MALE + GIVEN_FEMALE,
    'surnames': SURNAMES, 'firm_stems': FIRM_STEMS, 'firm_suffixes': FIRM_SUFFIXES, 'trades': TRADES,
    'relations': RELATIONS_MALE + RELATIONS_FEMALE, 'ones': ONES, 'tens': TENS,
}
assert not (set(SURNAMES) & set(FIRM_STEMS) & set(CONTRACTORS))
assert len(set(SURNAMES) | set(FIRM_STEMS) | set(CONTRACTORS)) == len(SURNAMES) + len(FIRM_STEMS) + len(CONTRACTORS)


# ---------------------------------------------------------------------------------------------------------------
# The interpreter. One pure function turns any list of events into the history of berths, holders and fees. The
# true reading and each flawed reader are the same rules with one rule replaced. Dates are ISO strings throughout.
# ---------------------------------------------------------------------------------------------------------------

# A bundled reader is the sum of two shortcuts; each can be run alone (to tell which one a statement turns on).
LITERAL_LABELS = (R_NAMES, 'numbers_only')
LITERAL_NAMES = (R_NAMES, 'names_only')
IGNORE_FIXES = (R_FIXES, 'fixes_only')
GRANT_CLOSED = (R_FIXES, 'withdrawals_only')
COMPONENTS = {R_NAMES: ('names_only', 'numbers_only'), R_FIXES: ('fixes_only', 'withdrawals_only')}


def _year(iso):
    return int(iso[:4])


def _base(holder):
    return holder[:-1] if isinstance(holder, str) and holder.endswith('*') else holder


def concerned(event):
    """The berths and the parties an event is about, as ids, in the order of its fields."""
    kind = event['kind']
    berths, parties = [], []
    for name in ('berth', 'other'):
        if event.get(name):
            berths.append(event[name])
    if kind == 'fee' and event['spec'].get('other'):
        berths.append(event['spec']['other'])
    for name in ('berths',):
        berths += list(event.get(name, ()))
    for child in event.get('children', ()):
        berths.append(child['berth'])
        if child['holder'] != VACANT:
            parties.append(child['holder'])
    for name in ('party', 'assignor', 'assignee', 'deceased', 'executor', 'heir', 'buyer', 'seller', 'person', 'anchor',
                 'wrong'):
        if event.get(name):
            parties.append(event[name])
    parties += list(event.get('merged', ()))
    if event['kind'] == 'join' and event['holder'] != VACANT:
        parties.append(event['holder'])
    seen_b, seen_p = [], []
    for b in berths:
        if b not in seen_b:
            seen_b.append(b)
    for p in parties:
        if p not in seen_p:
            seen_p.append(p)
    return seen_b, seen_p


class Hist:
    """The state after replaying a list of events. `reader` is None for the true reading."""

    def __init__(self, reader=None, fixids=None, closed=None, strict=False, drop_fees=False):
        self.reader, self.fixids, self.closed = reader, fixids or {}, closed or set()
        self.recs, self.agreements, self.renames, self.partner = {}, {}, {}, {}
        self.incomplete, self.counter, self.lost, self.clamped = False, 0, False, 0
        # `strict` is the reading of a subset (`replay`): what a statement is about must be in the subset (`established`,
        # `named`). `drop_fees` (the true reading's pass that decides what a subset leaves out) leaves out a fee entry
        # whose base is not in the subset and records it in `skipped`. Neither is set for the histories of a full list
        # that the generator draws by (`legacy_replay`) or for the world's and the renderer's own.
        self.strict, self.drop_fees, self.skipped, self.named = strict, drop_fees, set(), set()
        self.kept = None        # ids of the events a subset reading kept (set by `_read`)

    # -- records
    def key(self, bid, label):
        return 'L:' + label if self.reader in LITERAL_LABELS else bid

    def blank(self, k):
        record = self.recs[k] = {'k': k, 'born': None, 'ended': None, 'labels': [], 'tl': [], 'fees': [],
                                 'part_of': None, 'children': [], 'parent': None, 'end_prov': frozenset()}
        return record

    def rec(self, k, label=None, when=None):
        """The record of a berth an event refers to; one the events never established is created and marks the
        history incomplete."""
        record = self.recs.get(k)
        if record is None:
            record = self.blank(k)
            if label is not None and when is not None:
                record['labels'].append((when, label))
            if self.reader not in LITERAL_LABELS:
                self.incomplete = True
        return record

    def new(self, k, label, when):
        record = self.recs.get(k) or self.blank(k)
        record['born'] = when
        record['labels'] = [(when, label)]
        return record

    def established(self, k):
        """The berth's first appearance (its grant, or the split that made it) is in the reading. A reader that goes by
        the number as written has a berth wherever a number is written."""
        record = self.recs.get(k)
        return record is not None and (record['born'] is not None or self.reader in LITERAL_LABELS)

    def start(self, record):
        if record['born'] is not None:
            return record['born']
        return record['tl'][0][0] if record['tl'] else None

    def exists(self, k, date):
        record = self.recs.get(k)
        if record is None:
            return False
        start = self.start(record)
        return start is not None and start <= date and (record['ended'] is None or date < record['ended'])

    def label_of(self, k, date=None):
        record = self.recs[k]
        label = record['labels'][0][1] if record['labels'] else k[2:] if k.startswith('L:') else None
        for when, name in record['labels']:
            if date is None or when <= date:
                label = name
        return label

    def lit(self, party, when):
        """The holder as the reader writes it: a renamed firm is another holder to the reader that ignores renaming."""
        if self.reader in LITERAL_NAMES and party not in (None, VACANT) and any(w <= when for w in self.renames.get(party, ())):
            return party + '*'
        return party

    def hold(self, record, when, holder, prov):
        """Append a change of holder. An entry written later never takes effect before one written earlier on the
        same berth (the true reading never needs this; a reader that holds a berth wrongly can)."""
        self.counter += 1
        if record['tl'] and when < record['tl'][-1][0]:
            when = record['tl'][-1][0]
            self.clamped += 1
        record['tl'].append((when, self.counter, None if self.lost else holder, frozenset(prov)))

    def current(self, record):
        if not record['tl'] or record['ended'] is not None:
            return None, frozenset()
        return record['tl'][-1][2], record['tl'][-1][3]

    def basis(self, event):
        return {event['id']} | set(self.fixids.get(event['id'], ()))

    # -- events
    def apply(self, e, lost=False):
        """`lost`: an event this one depends on (the renaming behind a name it uses, the renumbering behind a number,
        the passage behind a description) is not in the list, so what this event sets is not established."""
        self.lost = lost
        if lost and self.reader not in LITERAL_LABELS:
            self.incomplete = True
        getattr(self, 'do_' + e['kind'])(e)
        self.lost = False

    def do_kin(self, e):
        pass

    do_bought = do_decoy = do_kin

    def do_grant(self, e):
        when = e['date']
        k = self.key(e['berth'], e['label'])
        if e.get('new'):
            record = self.new(k, e['label'], when)
        else:
            record = self.rec(k, e['label'], when)
        self.hold(record, when, self.lit(e['party'], when), self.basis(e))

    def do_fee_read(self, e):
        """A fee entry as a subset is read: an entry whose base (the previous entry of the berth, the fee of the other
        berth, of the adjoining berth or of the parent berth) is not there is left out and recorded in `skipped`. A sum
        worked out from what the subset has is kept as it comes out, whole or not, and later entries go on from it."""
        k = self.key(e['berth'], e['label'])
        record = self.recs.get(k)
        spec, year, prov = e['spec'], e['year'], self.basis(e)
        amount = None
        if spec['type'] == 'abs':
            amount = None if spec['amount'] is None else Fraction(spec['amount'])
        elif spec['type'] == 'rel':
            last = record['fees'][-1] if record is not None and record['fees'] else None
            if last is not None and last[1] is not None:
                amount = last[1] * Fraction(spec['den'] + spec['num'], spec['den'])
                prov |= last[2]
        else:
            if spec['target'] == 'berth':
                other = self.recs.get(self.key(spec['other'], spec['other_label']))
            elif spec['target'] == 'adjoin':
                other = self.recs.get(self.partner.get(k))
            else:
                other = self.recs.get(record['parent']) if record is not None else None
            found = self.fee_entry(other, year) if other is not None else None
            if found is not None and found[0] is not None:
                amount = found[0] * Fraction(spec['num'], spec['den'])
                prov |= found[1]
        if amount is None:          # the base is not there; a sum that is not whole is kept as it comes out
            self.skipped.add(e['id'])
            return
        record = self.rec(k, e['label'], e['date'])
        record['fees'].append((year, amount, frozenset(prov), e['id']))

    def do_fee(self, e):
        if self.drop_fees:
            return self.do_fee_read(e)
        k = self.key(e['berth'], e['label'])
        record = self.rec(k, e['label'], e['date'])
        spec, year, prov = e['spec'], e['year'], self.basis(e)
        if spec['type'] == 'abs':
            amount = None if spec['amount'] is None else Fraction(spec['amount'])
        elif spec['type'] == 'rel':
            last = record['fees'][-1] if record['fees'] else None
            if last is None or last[1] is None:
                amount = None
            else:
                amount = last[1] * Fraction(spec['den'] + spec['num'], spec['den'])
                prov |= last[2]
        else:
            if spec['target'] == 'berth':
                other = self.recs.get(self.key(spec['other'], spec['other_label']))
            elif spec['target'] == 'adjoin':
                other = self.recs.get(self.partner.get(k))
            else:
                other = self.recs.get(record['parent'])
            found = self.fee_entry(other, year) if other is not None else None
            if found is None or found[0] is None:
                amount = None
            else:
                amount = found[0] * Fraction(spec['num'], spec['den'])
                prov |= found[1]
        record['fees'].append((year, None if self.lost else amount, frozenset(prov), e['id']))

    def fee_entry(self, record, year):
        best = None
        for y, amount, prov, _ in record['fees']:
            if y <= year:
                best = (amount, prov)
        return best

    def do_pair(self, e):
        if self.lost:
            return
        ka, kb = (self.key(b, l) for b, l in zip(e['berths'], e['labels']))
        self.rec(ka, e['labels'][0], e['date'])
        self.rec(kb, e['labels'][1], e['date'])
        self.partner[ka], self.partner[kb] = kb, ka

    def do_agree(self, e):
        self.agreements[e['id']] = {'assignee': None if self.lost else e['assignee'], 'prov': self.basis(e)}
        closed = e['id'] in self.closed
        if (self.reader == R_AGREE and not closed) or (self.reader in GRANT_CLOSED and closed):
            record = self.rec(self.key(e['berth'], e['label']), e['label'], e['date'])
            self.hold(record, e['date'], self.lit(e['assignee'], e['date']), self.basis(e))

    def do_register(self, e):
        if self.reader == R_AGREE:
            return
        record = self.rec(self.key(e['berth'], e['label']), e['label'], e['when'])
        found = self.agreements.get(e['ref'])
        if found is None:
            self.hold(record, e['when'], None, self.basis(e))
        else:
            self.hold(record, e['when'], self.lit(found['assignee'], e['when']) if found['assignee'] else None,
                      self.basis(e) | found['prov'])

    def do_withdraw(self, e):
        pass

    do_refuse = do_withdraw

    def replace_holders(self, matches, new, when, e, keep_prov):
        for record in list(self.recs.values()):
            holder, prov = self.current(record)
            if holder is not None and _base(holder) in matches:
                self.hold(record, when, self.lit(new, when), self.basis(e) | (prov if keep_prov else frozenset()))

    def do_death(self, e):
        self.replace_holders({e['deceased']}, e['executor'], e['when'], e, True)

    def do_merge(self, e):
        self.replace_holders(set(e['merged']), e['party'], e['when'], e, True)

    def do_estate(self, e):
        record = self.rec(self.key(e['berth'], e['label']), e['label'], e['when'])
        self.hold(record, e['when'], self.lit(e['heir'], e['when']), self.basis(e))

    def do_rename(self, e):
        self.renames.setdefault(e['party'], []).append(e['when'])

    def do_surrender(self, e):
        record = self.rec(self.key(e['berth'], e['label']), e['label'], e['when'])
        self.hold(record, e['when'], VACANT, self.basis(e))

    do_forfeit = do_surrender

    def do_renumber(self, e):
        if self.reader in LITERAL_LABELS or self.lost:
            return
        record = self.rec(self.key(e['berth'], e['old']), e['old'], e['when'])
        record['labels'].append((e['when'], e['label']))
        record.setdefault('label_prov', set()).add(e['id'])

    def do_split(self, e):
        parent = self.rec(self.key(e['berth'], e['label']), e['label'], e['when'])
        parent['ended'] = e['when']
        parent['end_prov'] = self.basis(e)
        self.partner.pop(self.partner.pop(parent['k'], None), None)
        for child in e['children']:
            k = self.key(child['berth'], child['label'])
            record = self.new(k, child['label'], e['when'])
            record['parent'] = parent['k']
            parent['children'].append(k)
            holder = child['holder']
            self.hold(record, e['when'], holder if holder == VACANT else self.lit(holder, e['when']), self.basis(e))

    def do_join(self, e):
        survivor = self.key(e['survivor'], e['label'])
        record = self.rec(survivor, e['label'], e['when'])
        self.hold(record, e['when'], e['holder'] if e['holder'] == VACANT else self.lit(e['holder'], e['when']),
                  self.basis(e))
        for bid, label in zip(e['berths'], e['labels']):
            k = self.key(bid, label)
            self.partner.pop(self.partner.pop(k, None), None)
            if k != survivor:
                other = self.rec(k, label, e['when'])
                other['ended'] = e['when']
                other['part_of'] = survivor
                other['end_prov'] = self.basis(e)

    # -- queries
    def holder(self, k, date):
        if not self.exists(k, date):
            return None
        value = None
        for when, _, holder, _ in self.recs[k]['tl']:
            if when <= date:
                value = holder
        return value

    def holder_prov(self, k, date):
        found = frozenset()
        for when, _, holder, prov in self.recs[k]['tl']:
            if when <= date:
                found = prov
        return found

    def fee(self, k, year):
        record = self.recs.get(k)
        found = self.fee_entry(record, year) if record is not None else None
        if found is None or found[0] is None or found[0].denominator != 1:
            return None
        return int(found[0])

    def fee_prov(self, k, year):
        found = self.fee_entry(self.recs[k], year)
        return found[1] if found else frozenset()

    def count(self, party, date):
        if self.strict:
            # a party the subset never names is not established; of a party it names, the berths it establishes that the
            # party holds (possibly none)
            if party not in self.named:
                return None
            return sum(1 for k in self.recs if self.established(k) and self.exists(k, date) and
                       self.holder(k, date) == party)
        if self.incomplete:
            return None
        total = 0
        for k in self.recs:
            if self.exists(k, date):
                holder = self.holder(k, date)
                if holder is None:
                    return None
                total += holder == party
        return total

    def count_prov(self, party, date):
        found = set()
        for k in self.recs:
            if self.exists(k, date) and self.holder(k, date) == party:
                found |= self.holder_prov(k, date)
        return frozenset(found)

    def changes(self, k, d1, d2):
        record = self.recs.get(k)
        if record is None:
            return None
        total, previous = 0, None
        for index, (when, _, holder, _) in enumerate(record['tl']):
            if index and d1 < when <= d2:
                if previous is None or holder is None:
                    return None
                total += holder != previous
            previous = holder
        return total

    def changes_prov(self, k, d1, d2):
        found = set()
        previous = None
        for index, (when, _, holder, prov) in enumerate(self.recs[k]['tl']):
            if index and d1 < when <= d2 and holder != previous:
                found |= prov
            previous = holder
        return frozenset(found)

    def first_held(self, k, party):
        record = self.recs.get(k)
        if record is None:
            return None
        for when, _, holder, _ in record['tl']:
            if holder == party:
                return when
            if holder is None:
                return None
        return None

    def first_prov(self, k, party):
        for when, _, holder, prov in self.recs[k]['tl']:
            if holder == party:
                return prov
        return frozenset()

    def present(self, k):
        """(label, provenance) of the one present berth that the berth `k` is, or forms part of; else None."""
        if k not in self.recs:
            return None
        out, prov = set(), set()

        def walk(x):
            record = self.recs.get(x)
            # a berth the events never create is not established; in a subset, the berth a join names as the one that
            # continues is made by the join passage itself, whether or not its own grant is there
            if record is None or (record['born'] is None and self.reader not in LITERAL_LABELS and not (
                    self.strict and x != k)):
                out.add(None)
            elif record['ended'] is None:
                out.add(x)
                prov.update(record.get('label_prov', ()))
            else:
                prov.update(record['end_prov'])
                if record['part_of'] is not None:
                    walk(record['part_of'])
                elif record['children']:
                    for child in record['children']:
                        walk(child)
                else:
                    out.add(None)
        walk(k)
        if len(out) != 1 or None in out:
            return None
        return self.label_of(next(iter(out))), frozenset(prov)


def legacy_replay(events, reader=None):
    """The reading of the FULL list that the generator draws its dossiers by (`build`: the facts, the readers' facts and
    every statement it chooses), kept exactly as it was before `replay` read subsets as the whole file: an event that
    leans on a passage the list lacks sets nothing, and what it would have set stays None. On the full list it gives the
    same histories as `replay` (a test compares them); it is used there and by `legacy_answer`, nowhere else."""
    ordered = sorted(events, key=lambda e: e['seq'])
    ids = {e['id'] for e in ordered}
    fixes, fixids, closed = {}, {}, set()
    for e in ordered:
        if e['kind'] == 'fix' and reader not in IGNORE_FIXES and e['ref'] in ids:
            understood = all(n in ids for n in e.get('needs', ()))
            fixes.setdefault(e['ref'], {})[e['field']] = e['value'] if understood or e['field'] == 'when' else None
            fixids.setdefault(e['ref'], []).append(e['id'])
        if e['kind'] in ('withdraw', 'refuse'):
            closed.add(e['ref'])
    history = Hist(reader, fixids, closed)
    for e in ordered:
        if e['kind'] == 'fix':
            continue
        change = fixes.get(e['id'])
        if change:
            e = dict(e)
            for field, value in change.items():
                if field == 'amount':
                    e['spec'] = {'type': 'abs', 'amount': value}
                else:
                    e[field] = value
        history.apply(e, any(n not in ids for n in e.get('needs', ())))
    return history


def readable(ordered):
    """The events of a subset that can be read as they stand, in their order: the subset without those that lean on a
    passage it lacks or has itself had to leave out (`needs`: the renaming behind a name, the renumbering behind a
    number, the kinship, the sale of a business, the pairing, the passages behind "the holder of berth N"; `ref`: the
    agreement a register note, a withdrawal or a refusal is about, the entry a correction corrects), to a fixed point."""
    kept = list(ordered)
    while True:
        ids = {e['id'] for e in kept}
        now = [e for e in kept if all(n in ids for n in e.get('needs', ())) and (not e.get('ref') or e['ref'] in ids)]
        if len(now) == len(kept):
            return kept
        kept = now


def _read(kept, reader, drop_fees):
    """One reading of events that are all readable: corrections, closed applications, then every event in order."""
    fixes, fixids, closed = {}, {}, set()
    for e in kept:
        if e['kind'] == 'fix' and reader not in IGNORE_FIXES:
            fixes.setdefault(e['ref'], {})[e['field']] = e['value']
            fixids.setdefault(e['ref'], []).append(e['id'])
        if e['kind'] in ('withdraw', 'refuse'):
            closed.add(e['ref'])
    history = Hist(reader, fixids, closed, strict=True, drop_fees=drop_fees)
    for e in kept:
        if e['kind'] == 'fix':
            continue
        change = fixes.get(e['id'])
        if change:
            e = dict(e)
            for field, value in change.items():
                if field == 'amount':
                    e['spec'] = {'type': 'abs', 'amount': value}
                else:
                    e[field] = value
        history.apply(e)
    history.named = {p for e in kept if e['kind'] != 'decoy' for p in concerned(e)[1]}
    history.kept = {e['id'] for e in kept}      # what this reading did not leave out
    return history


def replay(events, reader=None):
    """Replay ANY subset of the events as if it were the whole correspondence file, under the procedure's own rules.
    An event whose own meaning depends on something the subset lacks is left out of the reading (`readable`; and a fee
    entry whose base, a previous entry of the berth or the fee of another, the adjoining or the parent berth, is not
    there: the true reading's pass finds these, and every reader leaves out the same ones), together with whatever
    depends on it in turn. A sum worked out from what the subset has is kept as it comes out, whole or not. What is left is read as the whole record: a correction,
    a death, a renaming, a withdrawal or a registration that is not there is simply not applied. The history says what
    it is about (`established`, `named`); `answer` gives None only where that is not in the subset."""
    kept = readable(sorted(events, key=lambda e: e['seq']))
    while True:
        truth = _read(kept, None, True)
        if not truth.skipped:
            break
        kept = readable([e for e in kept if e['id'] not in truth.skipped])
    return truth if reader is None else _read(kept, reader, False)


def depends(events):
    """{event id: {'name': [...], 'amount': [...], 'target': [...]}}: the events of this list that an event cannot be read
    without, by what they give it; the dependencies `replay` uses when it leaves an event out of a subset. Direct ones
    only (follow them to the end for the whole of what an event cannot be read without). Never raises (a list it cannot
    read gives {}).

    - `name`: what fixes how the event's paragraph names its berth or party: the `needs` passages (a renaming, a
      renumbering, a kinship, a sale, a merger, the pairing behind "the berth next to berth N", what made a party the
      holder of berth N), and, for a fee given as "the same as the adjoining berth" or as a share of "the berth it was
      split from", the pairing or the split that names that berth.
    - `amount`: what a fee is derived from: the base entry of a relative fee, the entry of another, the adjoining or the
      parent berth, or, when every entry it could take as its base leans on the same event (a grant, a pairing), that
      event. Exactly the events whose removal alone makes `replay` leave the fee out, found by reading the list without
      each event in turn; with an earlier entry to fall back on, the fee does not depend on the later one (it falls back,
      and its value changes). One that only reaches the fee through other events it already depends on is not listed: it
      is a dependency of those.
    - `target`: the agreement or entry the event is about (`ref`): a register note, a withdrawal or a refusal of an
      agreement, a correction of an entry."""
    try:
        ordered = sorted(events, key=lambda e: e['seq'])
        ids = {e['id'] for e in ordered}
        deps = {e['id']: {'name': list(dict.fromkeys(n for n in e.get('needs', ()) if n in ids)), 'amount': [],
                          'target': [e['ref']] if e.get('ref') in ids else []} for e in ordered}
        derived = [e for e in ordered if e['kind'] == 'fee' and e['spec']['type'] != 'abs']
        kept_without = {e['id']: replay([x for x in ordered if x['id'] != e['id']]).kept for e in ordered}
        for e in derived:        # the pairing or split that names the berth a fee is taken from
            target = e['spec'].get('target')
            if target == 'adjoin':
                named = [p for p in ordered if p['kind'] == 'pair' and e['berth'] in p['berths'] and p['seq'] < e['seq']][-1:]
            elif target == 'parent':
                named = [p for p in ordered if p['kind'] == 'split' and any(c['berth'] == e['berth'] for c in p['children'])][:1]
            else:
                named = []
            for p in named:
                if e['id'] not in kept_without[p['id']] and p['id'] not in deps[e['id']]['name']:
                    deps[e['id']]['name'].append(p['id'])
        probed = {}
        for e in derived:
            for d in ordered:
                if d['id'] != e['id'] and e['id'] not in kept_without[d['id']] and d['id'] not in deps[e['id']]['name']:
                    probed.setdefault(e['id'], []).append(d['id'])

        def reaches(start, goal, skip):
            seen, stack = {start}, [start]
            while stack:
                here = stack.pop()
                for nxt in deps[here]['name'] + deps[here]['target'] + probed.get(here, []):
                    if (here, nxt) == skip or nxt in seen:
                        continue
                    if nxt == goal:
                        return True
                    seen.add(nxt)
                    stack.append(nxt)
            return False
        for e_id, found in probed.items():
            for d_id in found:
                if not reaches(e_id, d_id, (e_id, d_id)):
                    deps[e_id]['amount'].append(d_id)
        return deps
    except Exception:  # noqa: BLE001 - the contract is that this never raises
        return {}


def state_on(events, date, reader=None):
    """Berths, holders and fees on `date` from ANY subset of the events (read as `replay` reads it), as a
    JSON-serialisable dict, of the berths the subset establishes (it has their grant or the split that made them).
    `holder` is a party id or 'vacant'; `fee` is the amount in pounds for the year of `date`, or None where no entry
    runs from that year or an earlier one (or the sum the subset gives is not whole pounds). `complete` is False when the subset mentions a berth it never establishes
    (those are left out of `berths`)."""
    history = replay(events, reader)
    berths = {}
    for k in sorted(history.recs):
        if history.established(k) and history.exists(k, date):
            berths[k] = {'label': history.label_of(k, date), 'holder': history.holder(k, date),
                         'fee': history.fee(k, _year(date))}
    return {'date': date, 'berths': berths, 'complete': not history.incomplete}


def _key(history, query):
    return 'L:' + query['label'] if history.reader in LITERAL_LABELS else query['berth']


NONE_VALUE = 'none'      # it is established that there is no value (nothing in force, nobody holds it)
RENAMED_SUFFIX = ' [as renamed]'
FORMS = {'holder': 'name', 'fee': 'integer', 'count': 'integer', 'changes': 'integer', 'first_held': 'date',
         'part': 'name'}


def answer_in(history, query):
    """The value a history gives for a query dict, in the history's own terms (party ids); None when not
    established, NONE_VALUE where it is established that there is none. On a history of a subset (`replay`, `strict`)
    None means that what the statement is about is not in it: the berth's first appearance, or the party's first
    naming (a count of the berths a party holds is a number, possibly 0, once the subset names the party)."""
    kind = query['kind']
    if kind == 'count':
        return history.count(query['party'], query['date'])
    k = _key(history, query)
    if history.strict:
        if not history.established(k):
            return None
        if kind == 'first_held' and query['party'] not in history.named:
            return None
    if kind == 'holder':
        return history.holder(k, query['date'])
    if kind == 'fee':
        record = history.recs.get(k)
        if record is None:
            return None
        entry = history.fee_entry(record, query['year'])
        if entry is None:
            return NONE_VALUE
        if history.strict and history.reader is None and entry[0] is not None:
            return int(entry[0]) if entry[0].denominator == 1 else entry[0]      # a sum from a subset is kept as it is
        return history.fee(k, query['year'])
    if kind == 'changes':
        return history.changes(k, query['from'], query['to'])
    if kind == 'first_held':
        found = history.first_held(k, query['party'])
        if found is None and k in history.recs and all(m[2] is not None for m in history.recs[k]['tl']):
            return NONE_VALUE
        return found
    found = history.present(k)
    return found[0] if found else None


def provenance(history, query):
    kind = query['kind']
    if kind == 'count':
        return history.count_prov(query['party'], query['date'])
    k = _key(history, query)
    if k not in history.recs:
        return frozenset()
    if kind == 'holder':
        return history.holder_prov(k, query['date'])
    if kind == 'fee':
        return history.fee_prov(k, query['year'])
    if kind == 'changes':
        return history.changes_prov(k, query['from'], query['to'])
    if kind == 'first_held':
        return history.first_prov(k, query['party'])
    found = history.present(k)
    return found[1] if found else frozenset()


def party_names(events):
    """{party id: the first full name the file gives it}, from the events given."""
    names = {}
    for e in events:
        names.update(e.get('party_names') or {})
    return names


def public_value(value, query, names):
    """A history's value in the form the key states it: a party by its first name, nobody by NONE_VALUE."""
    if value is None or query['kind'] != 'holder':
        return value
    if value == VACANT:
        return NONE_VALUE
    name = names.get(value.rstrip('*'))
    if name is None:
        return None
    return name + (RENAMED_SUFFIX if value.endswith('*') else '')


def answers(queries, events, reader=None):
    """`answer` for several queries of one subset, reading the subset once. Never raises; a query it cannot evaluate
    gives None."""
    try:
        if reader is not None and reader not in READERS:
            return [None] * len(queries)
        history = replay(events, reader)
        names = party_names(events)
    except Exception:  # noqa: BLE001 - the contract is that this never raises
        return [None] * len(queries)
    out = []
    for query in queries:
        try:
            out.append(public_value(answer_in(history, query), query, names))
        except Exception:  # noqa: BLE001
            out.append(None)
    return out


def answer(query, events, reader=None):
    """The value of a statement from ANY SUBSET of the events, read as if the subset were the whole correspondence file
    under the procedure's own rules: None when the subset does not establish it, NONE_VALUE when it establishes that
    there is no value (nobody holds the berth, no fee is in force), else the value (an int or a str; a `Fraction` for a
    fee that a subset gives as a sum that is not whole, see below). Never raises.

    The rule (the same for every reader, in its own facts; `replay` carries it out):
    - An event whose own meaning depends on something the subset lacks is LEFT OUT of the reading: a register note whose
      agreement is absent; a passage that names a party or a berth only through a description whose establishing
      passage is absent (kinship, the sale of a business, a renaming, a renumbering, a pairing, "the berth held by X");
      a fee stated by reference to a fee that is absent (the berth's previous entry, another, the adjoining or the
      parent berth's); a correction, withdrawal or refusal of an entry or application that is absent. Whatever depends on a left-out
      event is left out in turn.
    - The rest is read as the whole record: a later correction, death, renaming, withdrawal or registration that is
      absent is simply not applied (an agreement with no register note is an agreement never registered, as the
      procedure reads it; a fee with no entry is NONE_VALUE). A sum worked out from the subset is kept as it comes out,
      fraction or not (a rule of the file, such as "every sum so computed is a whole number of pounds", holds for the whole
      file and is no reason to leave an entry out), and later entries go on from it; the fee `answer` gives for it is that
      figure (a `Fraction` when it is not whole), a value that differs from the true one. That `Fraction` is not
      JSON-serialisable: a caller compares it with a stated value and does not write it. The flawed readers keep None for
      a figure that is not whole, as on the full list (they cannot state it in their own facts).
    - None only where what the statement is about cannot be computed at all: the berth's first appearance (its grant,
      or the split that made it, read as above) is absent, for a statement about that berth's holder, its fee, its
      changes of holder, what it forms part of, or the first date a party held it; or the party is never named in the
      subset, for a count of its berths or the first date it held one. A count of berths held by a party the subset
      names is a number (possibly 0); a vacant berth is NONE_VALUE.
    A reader reads the same events by its own rule; what it cannot state in its own facts on the full list (a fee it
    cannot find) stays None."""
    return answers([query], events, reader)[0]


def legacy_answer(query, events, reader=None):
    """`answer` as it was before the subset rule: a missing referent makes what depends on it None (see
    `legacy_replay`). Used by the tests to measure what the rule changed; nothing else reads it."""
    try:
        if reader is not None and reader not in READERS:
            return None
        history = legacy_replay(events, reader)
        return public_value(answer_in(history, query), query, party_names(events))
    except Exception:  # noqa: BLE001
        return None


def value_form(query):
    """How the value of a statement is written: integer, decimal, date, month, name, or ('choice', words)."""
    return FORMS[query['kind']]


IDENTITY_KINDS = ('death', 'merge', 'rename', 'kin', 'bought')


def creates(event, bid):
    """The event that makes berth `bid` appear: its grant (a new berth) or the split that made it."""
    return (event['kind'] == 'grant' and bool(event.get('new')) and event['berth'] == bid) or (
        event['kind'] == 'split' and any(c['berth'] == bid for c in event['children']))


def relevant(query, events):
    """Ids of the events of the statement's own subject and of everything they refer to: the events of the berth
    (its first appearance among them, whatever the kind of statement), of the berths it was split, joined or paired
    with or takes a fee from, the passages behind the names they use, and the events that change a holder or a name of
    any party named there. A count is over every berth."""
    by_id = {e['id']: e for e in events}
    if query['kind'] == 'count':
        return sorted(by_id)
    fee_query = query['kind'] == 'fee'
    linked = {query['berth']}
    for _ in range(3):
        for e in events:
            follows = (('split', 'pair') if fee_query else ('split', 'join', 'renumber'))
            if e['kind'] in follows or (fee_query and e['kind'] == 'fee' and e['spec'].get('other')):
                berths = concerned(e)[0]
                if linked & set(berths):
                    linked |= set(berths)
    skipped = ('agree', 'register', 'withdraw', 'refuse', 'estate', 'grant', 'surrender', 'forfeit') if fee_query \
        else ('fee', 'pair')
    chosen = {e['id'] for e in events if linked & set(concerned(e)[0]) and e['kind'] not in skipped}
    chosen |= {e['id'] for e in events if creates(e, query['berth'])}      # the berth's first appearance
    if fee_query:      # the entries a fee derives from, however long the chain of berths that give it
        try:
            chosen |= {i for i in provenance(replay(events), query) if i in by_id}
        except Exception:  # noqa: BLE001 - `relevant` never raises
            pass
    parties = {query['party']} if 'party' in query else set()
    for i in chosen:
        parties |= set(concerned(by_id[i])[1])
    while True:      # a death or merger hands a berth to another party, whose own events matter in turn
        more = {e['id'] for e in events if e['kind'] in IDENTITY_KINDS and parties & set(concerned(e)[1])} - chosen
        if not more:
            break
        chosen |= more
        for i in more:
            parties |= set(concerned(by_id[i])[1])
    frontier = list(chosen)
    while frontier:
        e = by_id[frontier.pop()]
        for i in ([e['ref']] if e.get('ref') else []) + list(e.get('needs', ())):
            if i in by_id and i not in chosen:
                chosen.add(i)
                frontier.append(i)
    return sorted(chosen)


# ---------------------------------------------------------------------------------------------------------------
# The world: one forward simulation draws the event log from `rng`. It applies every event to a true history as it
# goes, plans the matters that never end in registration, the corrections and the identity facts, and keeps each
# berth and party free of other events while a matter about it is open, so that file order is effective order.
# ---------------------------------------------------------------------------------------------------------------

def shift(iso, days):
    return (datetime.date.fromisoformat(iso) + datetime.timedelta(days=days)).isoformat()


def span_days(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


FAR = '9999-12-31'


class World:
    def __init__(self, rng):
        self.rng = rng
        year = rng.randint(2012, 2016)
        self.start = f'{year}-01-{rng.randint(6, 20):02d}'
        self.end = f'{year + 11}-12-{rng.randint(10, 28):02d}'
        self.h = Hist()
        self.log, self.gen, self.pending, self.tick = [], 0, [], 0
        self.parties, self.bfree, self.plast, self.first_use = {}, {}, {}, {}
        self.used_names, self.trades = set(), list(TRADES)
        self.next_label, self.berth_count, self.batches = 1, 0, 0
        self.kinship = set()

    # ---- cast
    def new_person(self):
        rng = self.rng
        male = rng.random() < 0.5
        given = rng.choice(GIVEN_MALE if male else GIVEN_FEMALE)
        surname = self.unused(SURNAMES)
        title = 'Mr' if male else rng.choice(('Ms', 'Mrs', 'Miss'))
        return self.add_party({'kind': 'person', 'male': male, 'title': title, 'surname': surname,
                               'names': [('0000', f'{given} {surname}')]})

    def unused(self, pool):
        free = [word for word in pool if word not in self.used_names]
        word = self.rng.choice(free)
        self.used_names.add(word)
        return word

    def firm_name(self):
        stem = self.unused(FIRM_STEMS)
        return stem, f'{stem} {self.rng.choice(FIRM_SUFFIXES)}'

    def new_firm(self):
        stem, name = self.firm_name()
        return self.add_party({'kind': 'firm', 'stem': stem, 'names': [('0000', name)]})

    def add_party(self, info):
        pid = f'P{len(self.parties) + 1:02d}'
        info.update(id=pid, alive=True)
        self.parties[pid] = info
        self.plast[pid] = self.start
        return pid

    def alive_parties(self, kind=None):
        return [p for p in sorted(self.parties) if self.parties[p]['alive'] and (kind is None or self.parties[p]['kind'] == kind)
                and p in self.first_use]

    def renamed(self, pid):
        return len(self.parties[pid]['names']) > 1

    # ---- berths
    def alive_berths(self):
        return [b for b in sorted(self.h.recs) if self.h.recs[b]['ended'] is None]

    def holder_now(self, bid):
        return self.h.current(self.h.recs[bid])[0]

    def cur_label(self, bid):
        return self.h.recs[bid]['labels'][-1][1]

    def label_for(self, bid):
        labels = [name for _, name in self.h.recs[bid]['labels']]
        if len(labels) > 1 and self.rng.random() < 0.2:
            return self.rng.choice(labels[:-1])
        return labels[-1]

    def new_batch(self):
        self.batches += 1
        return self.batches

    def free_berths(self, when, pred=None):
        out = [b for b in self.alive_berths() if self.bfree.get(b, self.start) < when and (pred is None or pred(b))]
        return out

    def pick(self, items, weight=None):
        if not items:
            return None
        if weight is None:
            return self.rng.choice(items)
        weights = [weight(item) for item in items]
        return self.rng.choices(items, weights=weights)[0]

    def berth_weight(self, bid):
        return 1 + 2 * (len(self.h.recs[bid]['labels']) > 1)

    def lock(self, bid, until):
        if until > self.bfree.get(bid, ''):
            self.bfree[bid] = until

    # ---- the log
    def emit(self, batch, event, written=None):
        self.gen += 1
        final = dict(event, id=f'T{self.gen}')
        if '_target' in event:
            final['ref'] = event['_target']['tmp']
        stored = dict(event, tmp=final['id'], batch=batch, gen=self.gen)
        if written:
            stored.update(written)
        stored.setdefault('when', stored['date'])
        final.setdefault('when', final['date'])
        if final['kind'] != 'fix':
            self.h.apply(final)
        for pid in concerned(stored)[1]:
            self.first_use.setdefault(pid, stored['date'])
            self.plast[pid] = max(self.plast[pid], stored['date'])
        self.log.append(stored)
        return stored

    def at(self, date, fn):
        self.tick += 1
        self.pending.append((date, self.tick, fn))

    def flush(self, until):
        while True:
            due = [item for item in self.pending if item[0] <= until]
            if not due:
                return
            item = min(due)
            self.pending.remove(item)
            item[2](item[0])

    # ---- fees
    def amount_near(self, last):
        rng = self.rng
        if last is None:
            return 40 * rng.randrange(20, 120)
        value = int(last * rng.uniform(0.7, 1.45)) // 40 * 40
        return max(480, value if value != last else value + 40)

    def fee_entry_value(self, bid, year):
        found = self.h.fee_entry(self.h.recs[bid], year)
        return None if found is None or found[0] is None else found[0]

    def last_from(self, bid):
        fees = self.h.recs[bid]['fees']
        return fees[-1][0] if fees else 0

    def choose_spec(self, bid, year, kinds):
        """A fee specification for `bid` from `year`, trying the kinds in the given order."""
        rng = self.rng
        record = self.h.recs[bid]
        for kind in kinds:
            if kind == 'rel' and record['fees'] and record['fees'][-1][1] is not None:
                last = record['fees'][-1][1]
                for phrase in rng.sample(sorted(RELATIVE), 12):
                    num, den = RELATIVE[phrase]
                    value = last * Fraction(den + num, den)
                    if value.denominator == 1 and 480 <= value <= 15000:
                        return {'type': 'rel', 'num': num, 'den': den}
            elif kind in ('same', 'frac'):
                others = [b for b in self.alive_berths() if b != bid and self.fee_entry_value(b, year) is not None]
                if others:
                    other = rng.choice(others)
                    value = self.fee_entry_value(other, year)
                    for phrase in ([None] if kind == 'same' else rng.sample(sorted(FRACTIONS), 3)):
                        num, den = (1, 1) if phrase is None else FRACTIONS[phrase]
                        product = value * Fraction(num, den)
                        if product.denominator == 1 and 480 <= product <= 15000:
                            return {'type': 'ref', 'target': 'berth', 'other': other,
                                    'other_label': self.label_for(other), 'num': num, 'den': den}
            elif kind == 'adjoin':
                partner = self.h.partner.get(bid)
                if partner and self.h.recs[partner]['ended'] is None and self.fee_entry_value(partner, year) is not None:
                    return {'type': 'ref', 'target': 'adjoin', 'other': None, 'other_label': None, 'num': 1, 'den': 1}
            elif kind == 'parent':
                parent = record['parent']
                value = self.fee_entry_value(parent, year) if parent else None
                if value is not None:
                    for phrase in rng.sample(sorted(PARENT_FRACTIONS), 3):
                        num, den = PARENT_FRACTIONS[phrase]
                        product = value * Fraction(num, den)
                        if product.denominator == 1 and 480 <= product <= 15000:
                            return {'type': 'ref', 'target': 'parent', 'other': None, 'other_label': None,
                                    'num': num, 'den': den}
        last = record['fees'][-1][1] if record['fees'] else None
        return {'type': 'abs', 'amount': self.amount_near(int(last) if last is not None else None)}

    def fee_batch(self, batch, bid, t, kinds, plan_fix=True):
        rng = self.rng
        year = max(_year(t) + (1 if t[5:7] >= '09' and rng.random() < 0.4 else 0), self.last_from(bid))
        spec = self.choose_spec(bid, year, kinds)
        event = {'kind': 'fee', 'berth': bid, 'label': self.label_for(bid), 'year': year, 'spec': spec,
                 'date': t, 'when': t}
        written, fix_date = None, shift(t, rng.randint(25, 380))
        if spec['type'] == 'abs' and plan_fix and fix_date < self.end and rng.random() < 0.15:
            wrong = spec['amount'] + 40 * rng.choice((-1, 1)) * rng.randint(1, 12)
            if wrong > 0:
                written = {'spec': {'type': 'abs', 'amount': wrong}}
        stored = self.emit(batch, event, written)
        if written:
            value = spec['amount']
            self.at(fix_date, lambda d, stored=stored, value=value, bid=bid: self.fix_amount(d, bid, stored, value))
        self.lock(bid, t)
        return stored

    def fix_amount(self, d, bid, stored, value):
        self.emit(self.new_batch(), {'kind': 'fix', 'field': 'amount', 'value': value, 'date': d, 'berth': bid,
                                     'label': self.label_for(bid), 'entered': stored['date'], '_target': stored})

    # ---- starting up
    def setup(self):
        rng = self.rng
        n0 = rng.randint(34, 40)
        labels = [str(i) for i in range(1, n0 + 1)]
        rng.shuffle(labels)
        dates = sorted(shift(self.start, rng.randrange(0, 440)) for _ in range(n0))
        pairs = {}
        for i in range(1, n0, 2):
            if rng.random() < 0.7:
                pairs[str(i)], pairs[str(i + 1)] = str(i + 1), str(i)
        self.next_label = n0 + 1
        self.label_bid = {}
        for label, date in zip(labels, dates):
            self.at(date, lambda d, label=label: self.initial_grant(d, label, pairs))

    def initial_grant(self, t, label, pairs):
        rng = self.rng
        batch = self.new_batch()
        pool = [p for p in self.alive_parties() if sum(self.holder_now(b) == p for b in self.alive_berths()) < 3]
        if pool and rng.random() < 0.28:
            party = rng.choice(pool)
        else:
            party = self.new_firm() if rng.random() < 0.5 else self.new_person()
        self.berth_count += 1
        bid = f'B{self.berth_count:02d}'
        self.label_bid[label] = bid
        self.emit(batch, {'kind': 'grant', 'berth': bid, 'label': label, 'party': party, 'new': True, 'date': t})
        partner = self.label_bid.get(pairs.get(label, ''))
        kinds = ['abs']
        if partner:
            self.emit(batch, {'kind': 'pair', 'berths': [partner, bid], 'labels': [pairs[label], label], 'date': t})
            if rng.random() < 0.3:
                kinds = ['adjoin', 'abs']
        spec_year = _year(t)
        event = self.choose_spec(bid, spec_year, kinds)
        stored = self.emit(batch, {'kind': 'fee', 'berth': bid, 'label': label, 'year': spec_year, 'spec': event,
                                   'date': t})
        self.lock(bid, t)
        fix_date = shift(t, rng.randint(25, 380))
        if event['type'] == 'abs' and fix_date < self.end and rng.random() < 0.12:
            wrong = event['amount'] + 40 * rng.choice((-1, 1)) * rng.randint(1, 12)
            stored['spec'] = {'type': 'abs', 'amount': wrong}
            self.at(fix_date, lambda d, stored=stored, value=event['amount'], bid=bid:
                    self.fix_amount(d, bid, stored, value))

    # ---- random events
    def assignee_for(self, assignor):
        rng = self.rng
        pool = [p for p in self.alive_parties() if p != assignor]
        renamed = [p for p in pool if self.parties[p]['kind'] == 'firm' and self.renamed(p)]
        if renamed and rng.random() < 0.5:
            return rng.choice(renamed), False
        if pool and rng.random() < 0.5:
            return rng.choice(pool), False
        return (self.new_firm() if rng.random() < 0.4 else self.new_person()), True

    def ev_assign(self, t):
        rng = self.rng
        holders = lambda b: (self.holder_now(b) not in (None, VACANT))  # noqa: E731
        bid = self.pick(self.free_berths(t, holders), self.berth_weight)
        if bid is None:
            return False
        assignor = self.holder_now(bid)
        assignee, fresh = self.assignee_for(assignor)
        if self.trades and self.parties[assignor]['kind'] == 'firm' and rng.random() < 0.2:
            assignee, fresh = self.new_firm(), True
        roll = rng.random()
        outcome = 'R' if roll < 0.56 else 'P' if roll < 0.69 else 'X' if roll < 0.82 else 'W'
        batch = self.new_batch()
        written, plan = None, None
        pool = [p for p in self.alive_parties() if p not in (assignor, assignee)]
        fix_date = shift(t, rng.randint(8, 320))
        if outcome == 'R' and pool and fix_date < self.end and rng.random() < 0.2:
            written = {'assignee': rng.choice(pool)}
            plan = 'assignee'
        elif outcome == 'R' and rng.random() < 0.2:
            plan = 'when'
        if fresh and not plan and self.trades and self.parties[assignor]['kind'] == 'firm' and \
                self.parties[assignee]['kind'] == 'firm' and rng.random() < 0.6:
            low = self.first_use.get(assignor)
            high = shift(t, -15)
            if low and span_days(low, high) > 4:
                bought = shift(low, rng.randint(2, span_days(low, high)))
                trade = self.trades.pop(rng.randrange(len(self.trades)))
                self.emit(self.new_batch(), {'kind': 'bought', 'seller': assignor, 'buyer': assignee, 'trade': trade,
                                             'date': bought})
        agree = self.emit(batch, {'kind': 'agree', 'berth': bid, 'label': self.label_for(bid), 'assignor': assignor,
                                  'assignee': assignee, 'date': t}, written)
        parties = [assignor, assignee] + ([written['assignee']] if written else [])
        if outcome == 'P':
            until = shift(t, rng.randint(14, 50))
        else:
            delay = rng.randint(14, 75)
            until = shift(t, delay)
        for p in parties:
            self.plast[p] = max(self.plast[p], shift(until, 1))
        self.lock(bid, shift(until, 1))
        if outcome == 'R':
            lag_true = rng.randint(0, min(8, delay - 6))
            true_when = shift(until, -lag_true)
            written_when = None
            if plan == 'when':
                options = [x for x in range(0, min(9, delay - 5)) if x != lag_true]
                if options:
                    written_when = shift(until, -rng.choice(options))
            self.at(until, lambda d: self.fire_register(d, bid, assignor, agree, true_when, written_when, plan))
        elif outcome in ('X', 'W'):
            self.at(until, lambda d: self.fire_close(d, bid, agree, 'refuse' if outcome == 'X' else 'withdraw'))
        if plan == 'assignee':
            self.at(fix_date, lambda d: self.fire_fix_assignee(d, bid, agree, assignee, written['assignee']))
        return True

    def fire_fix_assignee(self, d, bid, agree, true, wrong):
        self.emit(self.new_batch(), {'kind': 'fix', 'field': 'assignee', 'value': true, 'date': d, 'berth': bid,
                                       'label': agree['label'], 'party': true, 'wrong': wrong, 'agreed': agree['date'],
                                       '_target': agree})

    def fire_register(self, d, bid, assignor, agree, true_when, written_when, plan):
        if self.holder_now(bid) != assignor or self.h.recs[bid]['ended'] is not None:
            return
        event = {'kind': 'register', 'berth': bid, 'label': self.label_for(bid), 'when': true_when, 'date': d,
                 '_target': agree}
        fix_date = shift(d, self.rng.randint(8, 300))
        if fix_date >= self.end:
            written_when = None
        written = {'when': written_when} if written_when else None
        stored = self.emit(self.new_batch(), event, written)
        if written_when:
            self.at(fix_date, lambda dd: self.emit(self.new_batch(), {
                'kind': 'fix', 'field': 'when', 'value': true_when, 'date': dd, 'berth': bid,
                'label': self.label_for(bid), 'entry': written_when, '_target': stored}))
        self.lock(bid, max(d, true_when, written_when or d))

    def fire_close(self, d, bid, agree, kind):
        self.emit(self.new_batch(), {'kind': kind, 'berth': bid, 'label': self.label_for(bid), '_target': agree,
                                     'date': d})

    def ev_fee(self, t):
        rng = self.rng
        bid = self.pick(self.free_berths(t, lambda b: bool(self.h.recs[b]['fees'])), self.berth_weight)
        if bid is None:
            return False
        weights = {'abs': 0.30, 'rel': 0.30, 'same': 0.14, 'frac': 0.12, 'adjoin': 0.14}
        kinds = sorted(weights, key=lambda k: -weights[k] * rng.random())
        self.fee_batch(self.new_batch(), bid, t, kinds)
        return True

    def ev_death(self, t):
        rng = self.rng
        when = shift(t, -rng.randint(1, 16))
        eligible = []
        for p in self.alive_parties('person'):
            held = [b for b in self.alive_berths() if self.holder_now(b) == p]
            if held and self.plast[p] < when and all(self.bfree.get(b, self.start) < when for b in held):
                eligible.append((p, held))
        if not eligible:
            return False
        dead, held = rng.choice(eligible)
        executor = self.new_person()
        exe = self.parties[executor]
        if rng.random() < 0.7 and self.first_use.get(dead):
            low, high = self.first_use[dead], shift(t, -30)
            if span_days(low, high) > 6 and dead not in self.kinship:
                relation = rng.choice(RELATIONS_MALE if exe['male'] else RELATIONS_FEMALE)
                self.kinship.add(dead)
                self.emit(self.new_batch(), {'kind': 'kin', 'person': executor, 'anchor': dead, 'relation': relation,
                                             'date': shift(low, rng.randint(3, span_days(low, high)))})
        self.emit(self.new_batch(), {'kind': 'death', 'deceased': dead, 'executor': executor, 'when': when, 'date': t})
        self.parties[dead]['alive'] = False
        self.plast[dead] = FAR
        for bid in held:
            self.lock(bid, t)
            if rng.random() < 0.6:
                self.at(shift(t, rng.randint(60, 520)), lambda d, bid=bid, executor=executor:
                        self.fire_estate(d, bid, executor))
        return True

    def fire_estate(self, d, bid, executor):
        rng = self.rng
        when = shift(d, -rng.randint(0, 4))
        record = self.h.recs[bid]
        if record['ended'] is not None or self.holder_now(bid) != executor or self.bfree.get(bid, self.start) >= when:
            return
        pool = [p for p in self.alive_parties('person') if p != executor]
        heir = rng.choice(pool) if pool and rng.random() < 0.5 else self.new_person()
        self.emit(self.new_batch(), {'kind': 'estate', 'berth': bid, 'label': self.label_for(bid), 'heir': heir,
                                 'when': when, 'date': d})
        self.lock(bid, d)
        self.plast[heir] = max(self.plast[heir], d)

    def ev_rename(self, t):
        rng = self.rng
        when = shift(t, -rng.randint(0, 40))
        firms = [p for p in self.alive_parties('firm') if self.plast[p] < when]
        if not firms:
            return False
        firm = self.pick(firms)
        _, name = self.firm_name()
        self.emit(self.new_batch(), {'kind': 'rename', 'party': firm, 'name': name, 'when': when, 'date': t})
        self.parties[firm]['names'].append((when, name))
        self.plast[firm] = t
        return True

    def ev_merge(self, t):
        rng = self.rng
        when = shift(t, -rng.randint(0, 15))
        firms = [p for p in self.alive_parties('firm') if self.plast[p] < when and
                 all(self.bfree.get(b, self.start) < when for b in self.alive_berths() if self.holder_now(b) == p)]
        if len(firms) < 2:
            return False
        a, b = rng.sample(firms, 2)
        merged = self.new_firm()
        name = self.parties[merged]['names'][0][1]
        self.emit(self.new_batch(), {'kind': 'merge', 'merged': [a, b], 'party': merged, 'name': name, 'when': when,
                                 'date': t})
        for p in (a, b):
            self.parties[p]['alive'] = False
            self.plast[p] = FAR
        for bid in self.alive_berths():
            if self.holder_now(bid) == merged:
                self.lock(bid, t)
        return True

    def ev_vacate(self, t, kind):
        rng = self.rng
        when = shift(t, -rng.randint(0, 14))
        holders = lambda b: self.holder_now(b) not in (None, VACANT)  # noqa: E731
        bid = self.pick([b for b in self.free_berths(when, holders) if self.plast[self.holder_now(b)] < when])
        if bid is None:
            return False
        party = self.holder_now(bid)
        self.emit(self.new_batch(), {'kind': kind, 'berth': bid, 'label': self.label_for(bid), 'party': party, 'when': when,
                                 'date': t})
        self.lock(bid, t)
        self.plast[party] = max(self.plast[party], t)
        if rng.random() < 0.8:
            self.at(shift(t, rng.randint(40, 600)), lambda d, bid=bid: self.fire_regrant(d, bid))
        return True

    def fire_regrant(self, d, bid):
        record = self.h.recs[bid]
        if record['ended'] is not None or self.holder_now(bid) != VACANT or self.bfree.get(bid, self.start) >= d:
            return
        party, _ = self.assignee_for(None)
        batch = self.new_batch()
        self.emit(batch, {'kind': 'grant', 'berth': bid, 'label': self.label_for(bid), 'party': party, 'new': False,
                          'date': d})
        self.fee_batch(batch, bid, d, ['rel', 'abs', 'adjoin', 'same', 'frac'], plan_fix=False)
        self.lock(bid, d)
        self.plast[party] = max(self.plast[party], d)

    def ev_renumber(self, t):
        rng = self.rng
        when = shift(t, -rng.randint(0, 20))
        bid = self.pick(self.free_berths(when))
        if bid is None:
            return False
        label = self.label_for(bid)
        new = self.fresh_label()
        self.emit(self.new_batch(), {'kind': 'renumber', 'berth': bid, 'old': label, 'label': new, 'when': when, 'date': t})
        self.lock(bid, t)
        return True

    def fresh_label(self):
        self.next_label += self.rng.randint(1, 4)
        return str(self.next_label)

    def ev_split(self, t):
        rng = self.rng
        when = shift(t, -rng.randint(0, 10))
        bid = self.pick(self.free_berths(when, lambda b: self.cur_label(b).isdigit() and self.holder_now(b) is not None))
        if bid is None:
            return False
        parent_label = self.cur_label(bid)
        first, second = parent_label + 'A', parent_label + 'B'
        holder = self.holder_now(bid)
        if holder == VACANT:
            holds = [VACANT, VACANT]
        else:
            roll = rng.random()
            other = self.assignee_for(holder)[0] if roll >= 0.7 else None
            holds = [holder, holder] if roll < 0.3 else [holder, VACANT] if roll < 0.7 else [holder, other]
            if rng.random() < 0.3:
                holds.reverse()
            if any(h != VACANT and self.plast[h] >= when for h in holds):
                return False
        batch = self.new_batch()
        kids = []
        for label, held in zip((first, second), holds):
            self.berth_count += 1
            kids.append({'berth': f'B{self.berth_count:02d}', 'label': label, 'holder': held})
        self.emit(batch, {'kind': 'split', 'berth': bid, 'label': self.label_for(bid), 'children': kids, 'when': when,
                          'date': t})
        self.fee_batch(batch, kids[0]['berth'], t, ['parent', 'parent', 'abs'], plan_fix=False)
        self.fee_batch(batch, kids[1]['berth'], t, ['parent', 'same', 'abs'], plan_fix=False)
        for kid in kids:
            self.lock(kid['berth'], t)
            if kid['holder'] != VACANT:
                self.plast[kid['holder']] = max(self.plast[kid['holder']], t)
        return True

    def ev_join(self, t):
        rng = self.rng
        when = shift(t, -rng.randint(0, 10))
        free = self.free_berths(when, lambda b: self.holder_now(b) is not None)
        pairs = [(a, self.h.partner[a]) for a in free if self.h.partner.get(a) in free and a < self.h.partner[a]]
        if pairs and rng.random() < 0.7:
            x, y = rng.choice(pairs)
        elif len(free) >= 2:
            x, y = rng.sample(free, 2)
        else:
            return False
        survivor = rng.choice((x, y))
        hx, hy = self.holder_now(x), self.holder_now(y)
        if hx == hy and hx != VACANT:
            holder = hx
        else:
            roll = rng.random()
            options = [h for h in (hx, hy) if h != VACANT]
            holder = rng.choice(options) if options and roll < 0.6 else VACANT if roll < 0.8 or not options else \
                self.assignee_for(options[0])[0]
        if holder != VACANT and self.plast[holder] >= when:
            return False
        batch = self.new_batch()
        self.emit(batch, {'kind': 'join', 'berths': [x, y], 'labels': [self.label_for(x), self.label_for(y)],
                          'survivor': survivor, 'label': self.cur_label(survivor), 'holder': holder, 'when': when,
                          'date': t})
        self.fee_batch(batch, survivor, t, ['abs', 'abs', 'rel'], plan_fix=False)
        self.lock(survivor, t)
        if holder != VACANT:
            self.plast[holder] = max(self.plast[holder], t)
        return True

    def ev_decoy(self, t):
        """A passage that uses the words of a decision for something that decides nothing: a permit for a night, a
        prize, a payment for services. It touches no state and locks nothing."""
        rng = self.rng
        parties = self.alive_parties()
        if not parties:
            return False
        event = {'kind': 'decoy', 'party': rng.choice(parties), 'date': t}
        berths = self.alive_berths()
        if berths and rng.random() < 0.4:
            bid = rng.choice(berths)
            event.update(berth=bid, label=self.label_for(bid))
        self.emit(self.new_batch(), event)
        return True

    def ev_newberth(self, t):
        batch = self.new_batch()
        party, _ = self.assignee_for(None)
        label = self.fresh_label()
        self.berth_count += 1
        bid = f'B{self.berth_count:02d}'
        self.emit(batch, {'kind': 'grant', 'berth': bid, 'label': label, 'party': party, 'new': True, 'date': t})
        self.fee_batch(batch, bid, t, ['abs'], plan_fix=False)
        self.lock(bid, t)
        self.plast[party] = max(self.plast[party], t)
        return True

    def run(self):
        rng = self.rng
        self.setup()
        total = span_days(self.start, self.end) - 90
        times = sorted(shift(self.start, 70 + rng.randrange(total - 70)) for _ in range(250))
        kinds = (('fee', 0.30), ('assign', 0.32), ('death', 0.03), ('rename', 0.025), ('merge', 0.012),
                 ('surrender', 0.03), ('forfeit', 0.02), ('renumber', 0.045), ('split', 0.015), ('join', 0.02),
                 ('newberth', 0.02), ('decoy', 0.14))
        handlers = {'fee': self.ev_fee, 'assign': self.ev_assign, 'death': self.ev_death, 'rename': self.ev_rename,
                    'merge': self.ev_merge, 'surrender': lambda t: self.ev_vacate(t, 'surrender'),
                    'forfeit': lambda t: self.ev_vacate(t, 'forfeit'), 'renumber': self.ev_renumber,
                    'split': self.ev_split, 'join': self.ev_join, 'newberth': self.ev_newberth,
                    'decoy': self.ev_decoy}
        for t in times:
            self.flush(t)
            for _ in range(6):
                pick = rng.random() * sum(w for _, w in kinds)
                for name, weight in kinds:
                    if pick < weight:
                        break
                    pick -= weight
                if handlers[name](t):
                    break
        self.flush(self.end)
        return self.finish()

    def finish(self):
        ordered = sorted(self.log, key=lambda e: (e['date'], e['gen']))
        for number, event in enumerate(ordered, 1):
            event['id'] = f'E{number:03d}'
            event['seq'] = number
        for event in ordered:
            if '_target' in event:
                event['ref'] = event.pop('_target')['id']
        for event in ordered:
            event.pop('tmp', None)
            event.pop('gen', None)
        return ordered


# ---------------------------------------------------------------------------------------------------------------
# Ordinary business: the bulk of the file. A paragraph is a few sentences drawn from one topic with fresh fills. No
# bulk sentence names a party and a berth together, states a fee, or follows the wording of a deciding sentence.
# ---------------------------------------------------------------------------------------------------------------

PLACES = ('the north pontoon', 'the south pontoon', 'the Fish Quay', 'the Custom House Quay', 'the West Slip',
          'the Lime Jetty', 'the fuel berth', 'the visitors\' pontoon', 'the lock approach', 'the Mill Basin',
          'the breakwater', 'the harbour office yard', 'the boat park', 'the crane quay', 'the tidal gridiron',
          'the east wall', 'the ferry steps', 'the sand quay', 'the lifeboat slip', 'the winter storage compound')
WORKS = ('replacement of the fender piles', 'resurfacing of the slipway', 'renewal of the pontoon hinges',
         'repair of the pump-out station', 'relamping of the quay lights', 'replacement of the mooring chains',
         'dredging of the approach channel', 'repainting of the harbour railings', 'renewal of the water standpipes',
         'rebuilding of the boat-park wall', 'installation of the electric bollards', 'repair of the lifebuoy stations',
         'clearing of the outfall', 'renewal of the access gangways', 'strengthening of the quay edge',
         'replacement of the float chambers', 'fitting of new ladders', 'reconstruction of the fish-market roof')
CONTRACTOR_KINDS = ('Piling', 'Marine Works', 'Dredging', 'Electrical', 'Civil Engineering', 'Surveys', 'Scaffolding',
                    'Plant Hire', 'Painters', 'Joinery')
OFFICIALS = ('the Harbour Master', 'the Clerk', 'the Engineer', 'the Chairman', 'the Treasurer', 'the Surveyor',
             'the Registrar', 'the assistant Harbour Master', 'the Deputy Clerk')
WEATHER = ('a spell of north-easterly gales', 'persistent fog', 'heavy rain', 'a hard frost', 'a run of spring tides',
           'high winds on the Tuesday', 'a late snowfall')
CONDITIONS = ('some corrosion at the fixings', 'nothing of concern', 'loose decking boards', 'minor cracking in the '
              'concrete', 'worn shackles that should be renewed in the spring', 'weed growth but sound timbers',
              'a leaking float that has been pumped out', 'bolts that need tightening')
WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
CLUBS = ('sailing club', 'rowing club', 'angling society', 'sea cadets', 'yacht owners\' association',
         'harbour users\' group', 'lifeboat committee', 'fishermen\'s co-operative')
EVENTS_LOCAL = ('regatta', 'fish supper', 'open day', 'boat jumble', 'rowing race', 'carol service on the quay',
                'sea shanty evening', 'charity raft race')
FACILITIES = ('the showers', 'the pump-out facility', 'the crane', 'the visitors\' berths', 'the fuel pump',
              'the winter storage compound', 'the slipway', 'the laundry room')
STATUS = ('out of order until the end of the month', 'open from the first of April', 'fully booked for the week',
          'closed for repairs', 'available on payment of the usual charge', 'under review by the committee')
BODIES = ('the Coastal Heritage Fund', 'the county council', 'the Fisheries Local Action Group', 'the Harbour '
          'Improvement Trust', 'the regional development agency', 'the Lottery Community Fund')
NOISES = ('late-night music', 'engine testing', 'a generator running overnight', 'halyards slapping on a mast',
          'a barking dog', 'floodlights left on')
ADJ = ('thorough', 'cautious', 'brief', 'detailed', 'rather gloomy', 'encouraging', 'unexpectedly positive')
MONTH_NAMES = MONTHS


def _num(rng, lo, hi):
    return rng.randint(lo, hi)


def _money(rng, lo, hi, step=10):
    value = rng.randrange(lo // step, hi // step) * step
    roll = rng.random()
    if roll < 0.55:
        return f'£{value:,}'
    if roll < 0.7:
        return f'£{value}'
    if roll < 0.85:
        return f'{value:,} pounds'
    return f'{words(value)} pounds'


def _contractor(rng):
    return f'{rng.choice(CONTRACTORS)} {rng.choice(CONTRACTOR_KINDS)}'


def counted(rng, low, high, noun):
    n = rng.randint(low, high)
    return f'{words(n)} {noun}' + ('' if n == 1 else 's')


def cap1(text):
    return text[0].upper() + text[1:]


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return f'{n}th'
    return f'{n}' + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


def _when(rng):
    day = rng.randint(1, 28)
    month = rng.choice(MONTH_NAMES)
    return rng.choice((f'{day} {month}', f'{month} {day}', f'the {ordinal(day)} of {month}'))


def topic_works(rng, ctx):
    w, p = rng.choice(WORKS), rng.choice(PLACES)
    c = _contractor(rng)
    return [
        f'The quotation from {c} for the {w} at {p} came to {_money(rng, 2000, 60000)}, against an estimate of '
        f'{_money(rng, 2000, 60000)} in the last budget.',
        f'{cap1(rng.choice(OFFICIALS))} reported that the {w} at {p} was {rng.choice(("half finished", "ahead of programme", "a fortnight behind", "complete apart from snagging", "not yet started"))}.',
        f'Work on the {w} was held up by {rng.choice(WEATHER)}, and the contractor asked for {_num(rng, 3, 20)} extra working days.',
        f'{cap1(rng.choice(OFFICIALS))} inspected {p} on {rng.choice(WEEKDAYS)} and found {rng.choice(CONDITIONS)}.',
        f'{_num(rng, 2, 5)} tenders were received for the {w}, and the lowest came from {_contractor(rng)}.',
        f'The committee noted that the {w} had been finished {rng.choice(("on time", "a little late", "within the sum voted", "ahead of the autumn gales"))} and approved the final account.',
        f'A temporary closure of {p} will be needed while the {w} is carried out, and notices will be posted at the gates.',
        f'The method statement for the {w} was agreed with the {rng.choice(("fisheries officer", "navigation authority", "lifeboat crew", "fire officer"))} before any plant was brought on site.',
        f'It was agreed that {c} would be asked to price the {w} at {rng.choice(PLACES)} as an optional extra.',
        f'The retention on the {w} will be held until {rng.choice(MONTH_NAMES)}, when the defects period ends.',
    ]


def topic_weather(rng, ctx):
    p = rng.choice(PLACES)
    return [
        f'The Harbour Master recorded a surge of {rng.randint(3, 9) / 10:.1f} metres above the predicted tide on the night of {_when(rng)}, and the gangways at {p} were lifted clear as a precaution.',
        f'Wind gusting to {_num(rng, 28, 52)} knots on the {rng.choice(WEEKDAYS)} kept the visiting yachts alongside {p} for two days.',
        f'The tide gauge at {p} was recalibrated after it had read {_num(rng, 2, 14)} centimetres low for most of the winter.',
        f'Ice on the pontoon decking caused {rng.choice(("two minor falls", "one fall without injury", "no incidents"))} during the cold spell in {rng.choice(MONTH_NAMES)}.',
        f'{cap1(rng.choice(WEATHER))} meant that the weekly inspection of {p} was shortened.',
        f'The log shows {_num(rng, 14, 60)} vessels in and out on the busiest day, most of them local fishing boats.',
        f'The sounding of the approach channel showed {rng.choice(("no change", "a little shoaling off the north wall", "a build-up of silt near the lock", "deeper water than the chart"))}.',
    ]


def topic_enquiry(rng, ctx):
    p = rng.choice(PLACES)
    club = rng.choice(CLUBS)
    return [
        f'A local resident wrote to complain about {rng.choice(NOISES)} near {p} in the early hours.',
        f'The secretary of the {club} asked whether the {rng.choice(EVENTS_LOCAL)} could use {p} on {_when(rng)}.',
        f'A visiting skipper enquired about {rng.choice(FACILITIES)} and was told that it was {rng.choice(STATUS)}.',
        f'The {club} thanked the Authority for the use of {p} and sent a donation to the lifeboat fund.',
        f'The harbour office received {_num(rng, 3, 14)} letters this month about the new parking arrangements.',
        f'A schoolteacher asked whether a class of {_num(rng, 14, 32)} could be shown round the harbour on {rng.choice(WEEKDAYS)} afternoon.',
        f'An angler complained that the fishing notice at {p} had been removed, and the Harbour Master promised to replace it.',
        f'The {club} asked for the key to {rng.choice(FACILITIES)} to be left with the night watchman.',
        f'A reporter from the local paper asked for a comment on the {rng.choice(WORKS)} and was referred to the Clerk.',
    ]


def topic_committee(rng, ctx):
    return [
        f'The minutes of the previous meeting were {rng.choice(("approved", "agreed", "confirmed"))} {rng.choice(("without amendment", "with two corrections", "after a short discussion"))}.',
        f'Apologies were received from {counted(rng, 1, 4, "member")}.',
        f'The Chairman reminded members that interests must be declared before {rng.choice(("an item", "any item", "the item on " + rng.choice(WORKS)))} is taken.',
        f'The Clerk was asked to circulate the revised {rng.choice(("standing orders", "byelaws", "risk register", "mooring plan", "conditions of use"))} before the next meeting.',
        f'The committee discussed the general level of harbour {rng.choice(("charges", "dues", "costs"))} but took no decision.',
        f'It was agreed to hold the next meeting on {_when(rng)}, subject to the availability of {rng.choice(OFFICIALS)}.',
        f'{cap1(rng.choice(OFFICIALS))} presented the {rng.choice(("quarterly", "annual", "interim", "monthly"))} report, which was received.',
        f'A member asked that the minutes record the committee\'s thanks to the harbour staff for their work in {rng.choice(("the storm", "the winter", "the regatta", "the dredging", "the autumn"))}.',
        f'The committee went into closed session to consider {rng.choice(("a personnel matter", "a legal letter", "a tender", "a complaint"))} and resumed in public {words(_num(rng, 5, 40))} minutes later.',
        f'The Clerk reminded members that every assignment of a licence must be applied for in writing and entered in the register before it takes effect, as in {rng.choice(("earlier years", "the autumn", "the last report", "the procedure"))}.',
        f'Licence holders are reminded that the conditions require evidence of insurance to be shown each {rng.choice(("spring", "year", "March"))}.',
        f'The Clerk noted that annual fees fall due on the first of March and that reminders go out in {rng.choice(("January", "February"))}.',
    ]


def topic_compliance(rng, ctx):
    p = rng.choice(PLACES)
    return [
        'The insurers asked for an updated plan of the moorings and were sent the current one.',
        f'The annual inspection of the lifebuoys and flares was carried out by {_contractor(rng)} and no defects were found.',
        f'The fire extinguishers at {p} were serviced and the dates entered in the log.',
        f'The risk assessment for {p} was reviewed and {rng.choice(("no changes were needed", "two minor changes were made", "the night lighting was added to the list"))}.',
        f'The navigation authority asked for the position of the new mooring buoys to be sent to it by {_when(rng)}.',
        f'A notice to mariners about the works at {p} was issued and copied to the coastguard.',
        f'The water authority wrote to confirm that the sampling at {p} had passed.',
        f'The byelaws require vessels to show a current insurance certificate, and {rng.choice(("two", "three", "four", "five"))} reminders were sent this quarter.',
    ]


def topic_staff(rng, ctx):
    return [
        f'The assistant Harbour Master was on leave until {_when(rng)}.',
        f'A new part-time clerk started on {rng.choice(WEEKDAYS)} and is learning the filing system.',
        'The office telephone system was replaced and the old numbers were kept.',
        'The night watchman reported two attempted thefts of outboard motors and the police were informed.',
        f'The harbour launch was out of the water for {_num(rng, 3, 12)} days for its survey.',
        'The Deputy Clerk has agreed to take the minutes of the sub-committee on the quay lighting.',
        f'The mooring gang worked {_num(rng, 2, 6)} days on the winter maintenance list.',
        'Staff were reminded that visitors must sign the book at the harbour office before using the pontoons.',
    ]


def topic_environment(rng, ctx):
    p = rng.choice(PLACES)
    return [
        f'The fisheries officer asked for notice before any dredging in {rng.choice(MONTH_NAMES)}.',
        f'Seals were seen hauled out near {p}, and signs asking dog owners to keep clear were put up.',
        f'The oiled-bird report from {rng.choice(MONTH_NAMES)} was filed with the environment agency.',
        f'Samples of the dredged material were sent for analysis, and the results were {rng.choice(("clean", "within the limits", "slightly above the action level for one metal"))}.',
        f'A group of {rng.choice(("ringers", "birdwatchers", "students", "volunteers"))} asked to survey the shoreline beside {p}.',
        'The committee agreed to switch to low-energy lamps as the old fittings on the quays fail.',
    ]


def topic_letters(rng, ctx):
    p = rng.choice(PLACES)
    return [
        f'Thank you for your letter of {_when(rng)}, which I have placed before {rng.choice(OFFICIALS)}.',
        f'I am directed to acknowledge your letter about {p} and to say that the matter is being looked into.',
        f'Please quote our reference {rng.choice(("HM", "LC", "HO", "QS"))} in any further correspondence about {rng.choice(("this matter", "your enquiry", "the quay", "the arrangements"))}.',
        f'The enclosed plan shows {p} as it stands in {rng.choice(MONTH_NAMES)}.',
        f'A copy of this letter has been placed on the general file and sent to {rng.choice(OFFICIALS)}.',
        f'I should be grateful if you would let me know by {_when(rng)} whether the arrangements for {p} are convenient.',
        f'The Authority regrets the delay in replying, which was caused by {rng.choice(("the absence of the Clerk", "the storm", "the audit", "the change of telephone system", "a shortage of staff"))}.',
        f'Your remarks about {rng.choice(FACILITIES)} have been noted and will be reported at the committee meeting on {_when(rng)}.',
        f'The office will be closed on {rng.choice(WEEKDAYS)} afternoons from {_when(rng)} until {_when(rng)}.',
        f'The enclosed leaflet explains the new arrangements for {rng.choice(FACILITIES)}.',
        f'I have asked {rng.choice(OFFICIALS)} to telephone you before {_when(rng)}.',
        f'We shall write again when {rng.choice(("the survey", "the quotation", "the committee", "the contractor", "the insurers"))} has reported.',
        f'Your letter reached the office on {rng.choice(WEEKDAYS)}, the day after {_when(rng)}.',
        f'May I remind you that the harbour office is open from {_num(rng, 7, 9)} until {_num(rng, 16, 18)} on weekdays.',
    ]


def topic_finance(rng, ctx):
    return [
        f'The Treasurer reported income of {_money(rng, 20000, 190000, 50)} for the quarter and expenditure of {_money(rng, 20000, 190000, 50)}.',
        f'A grant application for the pontoon rebuild was sent to {rng.choice(BODIES)}.',
        f'Insurance premiums rose by {_num(rng, 2, 9)} per cent this year, and the broker was asked to seek other quotations.',
        f'The overdraft facility was not used during the quarter, and the balance at the bank stood at {_money(rng, 15000, 120000, 50)}.',
        f'The auditors asked for {rng.choice(("the fixed asset register", "the bank reconciliations", "the cash book", "the minutes of the finance sub-committee"))} and were sent it the same week.',
        f'The reserve for the {rng.choice(WORKS)} was topped up by {_money(rng, 2000, 20000, 50)}.',
    ]


def topic_people(rng, ctx):
    people = ctx['people']
    if not people:
        return topic_letters(rng, ctx)
    who = lambda: rng.choice(people)  # noqa: E731
    p = rng.choice(PLACES)
    return [
        f'{who()} wrote to ask when the lighting at {p} would be repaired.',
        f'{who()} telephoned the office about a loose cleat.',
        f'A parcel for {who()} was left at the harbour office and collected the next day.',
        f'{who()} asked for a copy of the winter storage rules.',
        f'The Harbour Master spoke to {who()} about the ropes trailing across the walkway.',
        f'{who()} called in to hand over a spare key for the gate.',
        f'{who()} thanked the staff for their help with the launch.',
    ]


def topic_berths(rng, ctx):
    labels = ctx['labels']
    if not labels:
        return topic_works(rng, ctx)
    lab = lambda: rng.choice(labels)  # noqa: E731
    return [
        f'The surveyor found a cracked float under the finger pontoon beside berth {lab()}.',
        f'The water standpipe nearest berth {lab()} was turned off for repair and turned on again the same afternoon.',
        f'Rubbish was found on the pontoon at berth {lab()} and cleared by the mooring gang.',
        f'The gangway lamp at berth {lab()} was replaced.',
        f'The engineer measured the finger of berth {lab()} and found it {_num(rng, 2, 9)} centimetres out of true.',
        f'Fresh anti-slip paint was applied around berth {lab()} in {rng.choice(MONTH_NAMES)}.',
        f'A loose fairlead near berth {lab()} was refixed.',
    ]



REPORT_VERBS = ('reminded members that', 'told the committee that', 'reported that', 'asked members to note that',
                'drew attention to the fact that', 'confirmed that', 'warned that', 'was glad to say that')


def matters(rng):
    return [
        f'the byelaws on speed in the harbour are due for review in {rng.choice(MONTH_NAMES)}',
        f'the lifeboat exercise will take place on {_when(rng)}',
        f'{_num(rng, 3, 40)} vessels were turned away from {rng.choice(PLACES)} during the regatta',
        f'the electricity supply to {rng.choice(PLACES)} will be cut for {_num(rng, 1, 6)} hours on {rng.choice(WEEKDAYS)}',
        f'the {rng.choice(CLUBS)} has asked to hold its annual dinner at the harbour office',
        f'the quarterly return to {rng.choice(BODIES)} is due on {_when(rng)}',
        f'the {rng.choice(WORKS)} will start on {_when(rng)} if the weather allows',
        f'the winter mooring plan for {rng.choice(PLACES)} has been circulated to the staff',
        f'{rng.choice(FACILITIES)} was used {_num(rng, 12, 400)} times last month',
        f'the new signs at {rng.choice(PLACES)} are expected from the printer by {_when(rng)}',
        f'the cost of the {rng.choice(WORKS)} has been put at {_money(rng, 2000, 60000)}',
        f'the audit of the harbour accounts will begin in {rng.choice(MONTH_NAMES)}',
        f'{_num(rng, 2, 9)} of the staff have completed the first-aid course',
        f'the harbour dues for visiting vessels were collected without incident on {_num(rng, 4, 28)} days of the month',
        f'the {rng.choice(CLUBS)} has offered volunteers for the clean-up of {rng.choice(PLACES)}',
        f'the tide tables for next year have been printed and are on sale at the office',
        f'the lease of the ice house was discussed and no offer has yet been received',
        f'the lighting at {rng.choice(PLACES)} will be tested on {_when(rng)}',
        f'the mooring ropes at {rng.choice(PLACES)} are to be inspected every {rng.choice(("month", "fortnight", "quarter"))}',
        f'the application for the {rng.choice(EVENTS_LOCAL)} on {_when(rng)} has been approved',
    ]


def extra_committee(rng, ctx):
    out = []
    for _ in range(4):
        out.append(f'{cap1(rng.choice(OFFICIALS))} {rng.choice(REPORT_VERBS)} {rng.choice(matters(rng))}.')
    out.append(f'The minutes of the meeting held on {_when(rng)} were {rng.choice(("approved", "agreed", "signed as a correct record", "taken as read"))}'
               f'{rng.choice(("", ", with one correction", ", without amendment", " after a short discussion"))}.')
    out.append(f'A member asked about the {rng.choice(WORKS)} at {rng.choice(PLACES)} and was told that {rng.choice(matters(rng))}.')
    out.append(f'The committee deferred the item on the {rng.choice(WORKS)} until its meeting in {rng.choice(MONTH_NAMES)}.')
    out.append(f'Routine business was brief, and {words(_num(rng, 2, 9))} items were taken together.')
    out.append(f'It was proposed by {rng.choice(("the Chairman", "the Vice-Chairman", "a member"))} and agreed that {rng.choice(matters(rng))}.')
    return out


def extra_works(rng, ctx):
    w, p, c = rng.choice(WORKS), rng.choice(PLACES), _contractor(rng)
    return [
        f'The Engineer measured {_num(rng, 12, 240)} metres of the quay edge at {p} and marked {_num(rng, 2, 14)} sections for repair.',
        f'{c} sent a revised programme for the {w}, showing completion in {rng.choice(MONTH_NAMES)}.',
        f'The clerk of works recorded {_num(rng, 3, 30)} operatives on site at {p} on {rng.choice(WEEKDAYS)}.',
        f'Payment of {_money(rng, 1500, 40000)} on account was certified to {c} for the {w}.',
        f'A variation of {_money(rng, 400, 9000)} was agreed for the {w} after the diver found a buried obstruction at {p}.',
        f'The {w} at {p} used {_num(rng, 4, 90)} tonnes of stone brought in by barge.',
        f'The harbour launch ferried {_num(rng, 20, 300)} bags of material across to {p} for the {w}.',
        f'The completion certificate for the {w} was signed on {_when(rng)} and the site was cleared the same week.',
    ]


def extra_people(rng, ctx):
    people = ctx['people']
    if not people:
        return []
    who = lambda: rng.choice(people)  # noqa: E731
    return [
        f'{who()} sent a note about {rng.choice(FACILITIES)} and asked for it to be put to the committee.',
        f'{who()} came into the office on {rng.choice(WEEKDAYS)} to ask whether the pontoon lights would be on over the winter.',
        f'The Harbour Master replied to {who()} that the winter mooring plan would be sent out in {rng.choice(MONTH_NAMES)}.',
        f'{who()} offered to lend a trailer for the {rng.choice(EVENTS_LOCAL)} on {_when(rng)}.',
        f'{who()} wrote to say that the rubbish bins at {rng.choice(PLACES)} had not been emptied for {_num(rng, 3, 12)} days.',
        f'The Clerk acknowledged a letter from {who()} about the new notice board.',
    ]


def extra_enquiry(rng, ctx):
    p = rng.choice(PLACES)
    return [
        f'A caller asked what time the lock opened on {rng.choice(WEEKDAYS)} and was given the tide table.',
        f'The {rng.choice(CLUBS)} asked for {words(_num(rng, 2, 9))} more dinghy racks near {p}.',
        f'A student wrote asking for the old harbour plans for a project on {rng.choice(("coastal erosion", "fishing history", "tidal energy", "the herring trade"))}.',
        f'A holidaymaker lost a {rng.choice(("phone", "camera", "wallet", "sailing jacket", "pair of binoculars"))} at {p} and the office held it for a week.',
        f'A neighbour on the quay complained about {rng.choice(NOISES)} and was assured that the matter would be raised on {rng.choice(WEEKDAYS)}.',
        f'The {rng.choice(CLUBS)} asked whether the slipway would be free on {_when(rng)} for a launch.',
        f'A fisherman asked about ice supplies for {rng.choice(MONTH_NAMES)} and was told to speak to the co-operative.',
    ]


def extra_staff(rng, ctx):
    return [
        f'The mooring gang spent {_num(rng, 2, 8)} days on {rng.choice(WORKS)} and {_num(rng, 1, 4)} on the cleaning of {rng.choice(PLACES)}.',
        f'{cap1(rng.choice(OFFICIALS))} will attend a course on harbour safety in {rng.choice(MONTH_NAMES)}.',
        f'The assistant Harbour Master took over the weekend rota for {rng.choice(MONTH_NAMES)}.',
        f'Overtime of {_num(rng, 6, 70)} hours was approved for the storm clearance at {rng.choice(PLACES)}.',
        f'The part-time clerk will work {words(_num(rng, 2, 4))} mornings a week until {_when(rng)}.',
        f'A uniform allowance was agreed for the {_num(rng, 3, 9)} harbour staff.',
    ]


def extra_compliance(rng, ctx):
    p = rng.choice(PLACES)
    return [
        f'The insurers inspected {p} on {_when(rng)} and made {counted(rng, 1, 6, "recommendation")}.',
        f'The fire officer asked for the extinguishers at {p} to be moved to the gate by {_when(rng)}.',
        f'The coastguard confirmed that the new buoys off {p} are charted from {_when(rng)}.',
        f'The environment agency asked for the dredging records for {rng.choice(MONTH_NAMES)} to be sent to its office.',
        f'A gas safety certificate for the harbour office was received on {_when(rng)}.',
        f'The annual test of the emergency pump at {p} was carried out by {_contractor(rng)} and passed.',
    ]


def extra_finance(rng, ctx):
    return [
        f'Expenditure on {rng.choice(WORKS)} in the quarter came to {_money(rng, 3000, 90000, 50)}.',
        f'The auditors raised {counted(rng, 1, 5, "point")}, none of them material.',
        f'The reserve for {rng.choice(WORKS)} was reviewed and left at {_money(rng, 5000, 80000, 50)}.',
        f'Visitor income for {rng.choice(MONTH_NAMES)} was {_money(rng, 1200, 24000, 50)}, up {_num(rng, 2, 18)} per cent on the year before.',
        f'The grant of {_money(rng, 5000, 70000, 50)} from {rng.choice(BODIES)} was received on {_when(rng)}.',
    ]


def extra_weather(rng, ctx):
    p = rng.choice(PLACES)
    return [
        f'The anemometer at {p} recorded a mean of {_num(rng, 8, 30)} knots on {_when(rng)}.',
        f'Fog closed the lock for {_num(rng, 2, 14)} hours on {rng.choice(WEEKDAYS)}.',
        f'The spring tides in {rng.choice(MONTH_NAMES)} reached {rng.randint(40, 70) / 10:.1f} metres at {p}.',
        f'Rain on {_num(rng, 4, 19)} days of the month made the painting of {p} impossible.',
    ]


def extra_verbs(rng, ctx):
    """Ordinary business in the words of a decision: awards, payments, permissions and registers that concern no
    berth and no licensee named in the file."""
    w, c = rng.choice(WORKS), _contractor(rng)
    return [
        f'The contract for the {w} was awarded to {c} after the second round of tenders.',
        f'Planning permission for the {w} was granted by the county council on {_when(rng)}.',
        f'The invoice from {c} for the {w} was paid on {_when(rng)}.',
        f'The assistant Harbour Master was assigned to the night rota for the {rng.choice(("winter", "regatta", "dredging", "spring"))}.',
        f'{_num(rng, 3, 60)} visiting vessels were registered at the harbour office in {rng.choice(MONTH_NAMES)}.',
        f'The tide table for {rng.choice(MONTH_NAMES)} was corrected and reprinted before it went on sale.',
        f'A transfer of {_money(rng, 2000, 40000, 50)} was made from the reserve to the works account.',
        f'The lease of the ice house was surrendered back to the council on {_when(rng)}.',
        'The licence of the ferry service across the estuary was renewed by the county council.',
        'The register of vessels kept at the harbour office was bound and indexed by the Deputy Clerk.',
        'The visiting charge for the lifeboat open day was waived.',
        f'The grant from {rng.choice(BODIES)} was awarded after a second round of applications.',
        f'The committee substituted the quotation of {c} for the earlier one for the {w}.',
        f'The balance of the account of {c} was paid after the final inspection of the {w}.',
        f'The post of assistant clerk was awarded to a candidate from the harbour town after interview.',
        f'Permission to hold the {rng.choice(EVENTS_LOCAL)} on the quay was granted to the {rng.choice(CLUBS)}.',
        f'The entries in the visitors\' book for {rng.choice(MONTH_NAMES)} were corrected by the night watchman.',
        f'{_money(rng, 600, 9000, 10)} was paid to the lifeboat fund from the proceeds of the open day.',
        f'The key to the fuel store was surrendered by a retiring member of staff.',
        f'A grant of {_money(rng, 2000, 30000, 50)} was awarded to the harbour museum by the county council.',
        f'The mooring chains were registered and tagged by {c} before they were laid.',
        'A permit to light a beach fire was granted to the sea cadets for the night of the regatta.',
        'The fee for the use of the crane was discussed and the matter was deferred.',
    ]


EXTRAS = {'committee': extra_committee, 'works': extra_works, 'people': extra_people, 'enquiry': extra_enquiry,
          'staff': extra_staff, 'compliance': extra_compliance, 'finance': extra_finance, 'weather': extra_weather}

TOPICS = (('works', topic_works), ('weather', topic_weather), ('enquiry', topic_enquiry),
          ('committee', topic_committee), ('compliance', topic_compliance), ('staff', topic_staff),
          ('environment', topic_environment), ('letters', topic_letters), ('finance', topic_finance),
          ('people', topic_people), ('berths', topic_berths))
TOPIC_WEIGHTS = {
    'letter': {'letters': 3, 'enquiry': 3, 'people': 2, 'weather': 1, 'works': 1.5, 'berths': 1, 'compliance': 1},
    'note': {'berths': 2, 'weather': 1.5, 'staff': 1.5, 'works': 2, 'people': 1.5, 'enquiry': 1, 'environment': 1,
             'compliance': 1},
    'minutes': {'committee': 4, 'finance': 2, 'works': 2, 'compliance': 1.5, 'staff': 1, 'environment': 1,
                'berths': 0.5},
}


def pick_topic(rng, ctx):
    weights = TOPIC_WEIGHTS[ctx['kind']]
    names = [name for name, _ in TOPICS if name in weights]
    chosen = rng.choices(names, weights=[weights[n] for n in names])[0]
    base, extra = dict(TOPICS)[chosen], EXTRAS.get(chosen)
    verbs = 0 if chosen in ('people', 'berths') else 5 if chosen in ('committee', 'works', 'finance', 'staff',
                                                                   'compliance', 'letters') else 3
    if extra is None and not verbs:
        return base
    return lambda rng, ctx: (base(rng, ctx) + (extra(rng, ctx) if extra else []) +
                             (rng.sample(extra_verbs(rng, ctx), verbs) if verbs else []))


LEADS = ('After some discussion', 'At the request of the Chairman', 'Without objection', 'For the record',
         'In view of the cost', 'Having consulted the Engineer', 'As in the previous year', 'Following the storm',
         'Before the new season', 'At the end of the item', 'On the advice of the Clerk', 'Despite the weather',
         'By a majority', 'For the time being', 'In the circumstances', 'On reflection', 'As a precaution',
         'Pending the next survey')
TAILS = (', as it had in the past', ', to the satisfaction of the members', ', which the Chairman welcomed',
         ', although opinions differed', ', and the Clerk made a note of it', ', pending a report from the Surveyor',
         ', at no extra cost to the Authority', ', as the Engineer had advised', ', in good time for the season',
         ', much as the staff had hoped', ', though not for the first time',
         ', and the matter was left there', ', to be reviewed in the spring', ', as the insurers required',
         ', with the agreement of the staff', ', as the log shows', ', and nothing more was said',
         ', which was recorded in the log', ', as usual at that time of year', ', for the third time that year',
         ', as the staff had expected', ', to nobody\'s surprise')
SAFE_STARTS = frozenset(('The', 'A', 'An', 'It', 'Apologies', 'Insurance', 'Samples', 'Seals', 'Ice', 'Rubbish', 'Fresh',
                         'Work', 'Staff', 'Please', 'Wind', 'Thank', 'Your', 'Licence', 'Notices', 'Three'))


def dress(rng, sentence, ctx):
    """The same sentence with a leading or trailing clause, so that one wording is seldom met twice."""
    if not sentence.endswith('.'):
        return sentence
    if rng.random() < 0.4 and sentence.split(' ')[0] in SAFE_STARTS:
        return f'{rng.choice(LEADS)}, {sentence[0].lower() + sentence[1:]}'
    tail = rng.choice(TAILS + (f', as agreed on {_when(rng)}', f', as it had been since {rng.choice(MONTH_NAMES)}'))
    return sentence[:-1] + tail + '.'


def bulk_paragraph(rng, ctx, target):
    """A paragraph of about `target` characters on one topic, each sentence fresh from the topic's pool."""
    topic = pick_topic(rng, ctx)
    sentences, size = [], 0
    pool = topic(rng, ctx)
    rng.shuffle(pool)
    while pool and (size < target or len(sentences) < 2):
        sentence = dress(rng, pool.pop(), ctx)
        sentences.append(sentence)
        size += len(sentence) + 1
        if not pool and size < target:
            pool = topic(rng, ctx)
            rng.shuffle(pool)
    return ' '.join(sentences)


def bulk_sentences(rng, ctx, count):
    pool = pick_topic(rng, ctx)(rng, ctx)
    rng.shuffle(pool)
    return [dress(rng, sentence, ctx) for sentence in pool[:count]]


# ---------------------------------------------------------------------------------------------------------------
# The renderer: documents in date order, each told from the event log with the phrase inventory. For every mention
# of a party it records the form it chose (full name, short form, description or role) and what it resolves to.
# ---------------------------------------------------------------------------------------------------------------

ROLES = {'grant': 'grants', 'fee': 'sets_fee', 'pair': 'pairs', 'agree': 'agrees', 'register': 'registers',
         'withdraw': 'withdraws', 'refuse': 'refuses', 'death': 'records_death', 'estate': 'passes_to_heir',
         'rename': 'renames', 'merge': 'merges', 'surrender': 'surrenders', 'forfeit': 'forfeits',
         'renumber': 'renumbers', 'split': 'splits', 'join': 'joins', 'fix': 'corrects', 'kin': 'introduces_kin',
         'bought': 'introduces_purchase', 'decoy': 'decoy_passage'}
HEADERS = {
    'letter': ('Letter received at the Harbour Office, {d}', 'Letter to the Harbour Master, {d}',
               'Letter to the Clerk to the Licensing Committee, {d}', 'Letter from the Harbour Master, {d}',
               'Letter to the Registrar, {d}', 'Letter from the Clerk to the Licensing Committee, {d}'),
    'note': ('File note by the Harbour Master, {d}', 'File note by the Registrar, {d}', 'Note for the file, {d}',
             'Telephone note by the Clerk, {d}', 'Internal memorandum, {d}', 'File note by the Deputy Clerk, {d}'),
    'minutes': ('Extract from the minutes of the Licensing Committee, meeting of {d}',
                'Extract from the minutes of the Harbour Committee, meeting of {d}',
                'Licensing sub-committee extract, meeting of {d}', 'Committee extract, meeting of {d}'),
}
DOC_KINDS = {'agree': (0.65, 0.3, 0.05), 'death': (0.6, 0.35, 0.05), 'rename': (0.8, 0.2, 0.0), 'merge': (0.7, 0.3, 0),
             'kin': (0.5, 0.5, 0), 'bought': (0.55, 0.45, 0), 'estate': (0.4, 0.4, 0.2),
             'register': (0.0, 0.55, 0.45), 'withdraw': (0.3, 0.4, 0.3), 'refuse': (0.2, 0.2, 0.6),
             'fee': (0.1, 0.3, 0.6), 'fix': (0.3, 0.4, 0.3), 'grant': (0.2, 0.3, 0.5), 'split': (0.0, 0.5, 0.5),
             'join': (0.0, 0.5, 0.5), 'surrender': (0.5, 0.4, 0.1), 'forfeit': (0.0, 0.3, 0.7),
             'renumber': (0.1, 0.5, 0.4), 'pair': (0.0, 0.7, 0.3), 'decoy': (0.4, 0.3, 0.3)}
ATTENDING = tuple(o for o in OFFICIALS if o != 'the Chairman')
DATE_WEIGHTS = (0.22, 0.1, 0.16, 0.1, 0.1, 0.08, 0.14, 0.1)


def date_text(rng, iso, doc_iso, form=None):
    year, month, day = int(iso[:4]), int(iso[5:7]), int(iso[8:])
    name, short = MONTHS[month - 1], MONTHS_SHORT[month - 1]
    if form is None:
        form = rng.choices(range(DATE_FORMS), weights=DATE_WEIGHTS)[0]
        if form >= 6 and year != int(doc_iso[:4]):
            form = rng.choice((0, 2, 3, 5))
    return {0: f'{day} {name} {year}', 1: f'{name} {day}, {year}', 2: f'{ordinal(day)} {name} {year}',
            3: f'the {ordinal(day)} of {name} {year}', 4: f'{year}-{month:02d}-{day:02d}',
            5: f'{day} {short} {year}', 6: f'{day} {name}', 7: f'{ordinal(day)} {name}'}[form]


def amount_text(rng, n):
    roll = rng.random()
    if roll < 0.4:
        return f'£{n:,}'
    if roll < 0.5:
        return f'£{n}'
    if roll < 0.65:
        return f'{n:,} pounds'
    if roll < 0.75:
        return f'{n} pounds'
    return f'{words(n)} pounds'


def sentence_case(text):
    return text[0].upper() + text[1:]


class Renderer:
    def __init__(self, rng, world, events):
        self.rng, self.parties, self.start, self.end = rng, world.parties, world.start, world.end
        self.events = events
        self.by_id = {e['id']: e for e in events}
        self.fixes, fixids, closed = {}, {}, set()
        for e in events:
            if e['kind'] == 'fix':
                self.fixes.setdefault(e['ref'], {})[e['field']] = e['value']
                fixids.setdefault(e['ref'], []).append(e['id'])
            if e['kind'] in ('withdraw', 'refuse'):
                closed.add(e['ref'])
        self.fix_ids = set(fixids) | {i for ids in fixids.values() for i in ids}
        self.hist = Hist(None, fixids, closed)
        self.intro, self.names, self.kin, self.trades, self.gone = set(), {}, {}, {}, set()
        self.doc_date = self.start
        self.para_text, self.sentence = {}, {}
        # the passages an event's wording leans on: the renaming behind a later name, the renumbering behind a later
        # number, the merger behind a merged firm, the kin or sale passage behind a description
        self.rename_for, self.merge_of, self.kin_of, self.bought_of, self.renumber_of = {}, {}, {}, {}, {}
        self.pair_for = {}
        for e in events:
            if e['kind'] == 'rename':
                self.rename_for[(e['party'], e['name'])] = e['id']
                self.rename_for[(e['party'], e['name'].split(' ')[0])] = e['id']
            elif e['kind'] == 'merge':
                self.merge_of[e['party']] = e['id']
            elif e['kind'] == 'kin':
                self.kin_of[e['person']] = e['id']
            elif e['kind'] == 'bought':
                self.bought_of[e['trade']] = e['id']
            elif e['kind'] == 'renumber':
                self.renumber_of[(e['berth'], e['label'])] = e['id']
            elif e['kind'] == 'pair':
                self.pair_for[frozenset(e['berths'])] = e['id']

    # ---- applying events in file order
    def apply(self, e):
        if e['kind'] == 'fix':
            return
        change = self.fixes.get(e['id'])
        if change:
            e = dict(e)
            for field, value in change.items():
                if field == 'amount':
                    e['spec'] = {'type': 'abs', 'amount': value}
                else:
                    e[field] = value
        self.hist.apply(e)

    # ---- parties
    def full_text(self, pid, latest=False):
        p, rng = self.parties[pid], self.rng
        if p['kind'] == 'person':
            name = p['names'][0][1]
            return f"{p['title']} {name}" if rng.random() < 0.4 else name
        known = self.names[pid]
        return known[-1] if latest or rng.random() < 0.65 else rng.choice(known)

    def short_text(self, pid, latest=False):
        p = self.parties[pid]
        if p['kind'] == 'person':
            return f"{p['title']} {p['surname']}"
        return (self.names[pid][-1] if latest else self.rng.choice(self.names[pid])).split(' ')[0]

    def anchor_text(self, pid):
        return self.short_text(pid) if self.rng.random() < 0.55 else self.full_text(pid)

    def berth_label(self, bid):
        labels = [name for _, name in self.hist.recs[bid]['labels']]
        return labels[-1] if len(labels) == 1 or self.rng.random() < 0.8 else self.rng.choice(labels[:-1])

    def holder_of(self, bid):
        record = self.hist.recs.get(bid)
        if record is None or record['ended'] is not None or not record['tl']:
            return None, frozenset()
        return record['tl'][-1][2], record['tl'][-1][3]

    def mention(self, pid, berth=None, forms=('full', 'short', 'kin', 'trade', 'holder'), latest=False):
        """Text naming party `pid` at this point of the file, and a record of what it chose, what it resolves to, and
        which other passages it leans on (`needs`). With `latest`, a renamed firm is named by its present name: used
        where the sentence makes the party a holder, which is what the reader that ignores renaming goes by."""
        text, record = self.mention_text(pid, berth, forms, latest)
        needs = [self.merge_of[pid]] if pid in self.merge_of else []
        if record['form'] in ('full', 'short'):
            name = text.split(' ', 1)[1] if self.parties[pid]['kind'] == 'person' and text.split(' ')[0] in TITLES \
                else text
            if (pid, name) in self.rename_for:
                needs.append(self.rename_for[(pid, name)])
        elif record['form'] == 'kin':
            needs.append(self.kin_of[pid])
        elif record['form'] == 'trade':
            needs.append(self.bought_of[record['trade']])
        elif record['form'] == 'holder':
            needs.extend(record['prov'])
            # the number written is a name too: a later number of the berth leans on the renumbering that gave it
            first = self.hist.recs[record['berth']]['labels'][0][1]
            if record['label'] != first and (record['berth'], record['label']) in self.renumber_of:
                needs.append(self.renumber_of[(record['berth'], record['label'])])
        record['needs'] = sorted(set(needs))
        return text, record

    def mention_text(self, pid, berth, forms, latest):
        rng = self.rng
        if pid not in self.intro:
            self.intro.add(pid)
            self.names.setdefault(pid, [self.parties[pid]['names'][0][1]])
            text = self.full_text(pid, latest)
            return text, {'form': 'full', 'party': pid, 'text': text}
        options = []
        if 'full' in forms:
            options.append(('full', 0.2))
        if 'short' in forms:
            options.append(('short', 0.22))
        if 'kin' in forms and pid in self.kin:
            options.append(('kin', 0.9))
        if 'trade' in forms:
            for trade, (buyer, seller) in sorted(self.trades.items()):
                if pid in (buyer, seller):
                    options.append((('trade', trade, 'buyer' if pid == buyer else 'seller'), 0.9))
        if 'holder' in forms and berth is not None:
            holder, prov = self.holder_of(berth)
            if holder == pid and not (prov & self.fix_ids):
                options.append((('holder', berth), 0.9))
        form = rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]
        if form == 'full':
            text = self.full_text(pid, latest)
        elif form == 'short':
            text = self.short_text(pid, latest)
        elif form == 'kin':
            anchor, relation = self.kin[pid]
            late = 'the late ' if anchor in self.gone else ''
            text = f'the only {relation} of {late}{self.anchor_text(anchor)}'
            form = 'kin'
        elif form[0] == 'trade':
            phrases = DESCRIPTIONS[form[2]]
            text = rng.choice(phrases).format(trade=form[1])
            return text, {'form': 'trade', 'party': pid, 'text': text, 'trade': form[1]}
        else:
            phrase = rng.choice(DESCRIPTIONS['holder'])
            label = self.berth_label(form[1])        # the number written; it may be a later one (draws as before)
            text = phrase.format(berth=label)
            return text, {'form': 'holder', 'party': pid, 'text': text, 'berth': form[1], 'label': label,
                          'prov': sorted(self.holder_of(form[1])[1])}
        return text, {'form': form, 'party': pid, 'text': text}

    # ---- sentences
    def say(self, key, **slots):
        return sentence_case(self.rng.choice(EVENTS[key]).format(**slots))

    def dt(self, iso):
        return date_text(self.rng, iso, self.doc_date)

    def render_event(self, e):
        """(sentences, mention records) for one event, then the event is applied to the running history."""
        sentences, records = getattr(self, 'r_' + e['kind'])(e)
        needs = {n for r in records for n in r.get('needs', ())}
        for label, bid in berth_mentions(e):
            record = self.hist.recs.get(bid)
            if record is not None and label != record['labels'][0][1] and (bid, label) in self.renumber_of:
                needs.add(self.renumber_of[(bid, label)])
        needs.discard(e['id'])
        e['needs'] = sorted(needs)
        self.apply(e)
        return sentences, records

    def after(self, e, roles):
        """Optionally a sentence that names a party only by its role in the event sentence just written."""
        if self.rng.random() > 0.3 or e['id'] in self.fix_ids:
            return [], []
        role, pid = self.rng.choice(roles)
        phrase = self.rng.choice(FOLLOW_ROLES[role])
        text = sentence_case(self.rng.choice(FOLLOW).format(role=phrase))
        return [text], [{'form': 'role', 'party': pid, 'text': phrase, 'role': role}]

    def berth_ref(self, e, allow_held=True):
        """How an event names its berth: by number, by the berth next to another, or by the one berth a party holds.
        Returns the text and the records of what it resolved. A description sets the event's `label` to the number
        the berth carries at this point of the file."""
        rng, bid = self.rng, e['berth']
        e['berth_form'] = 'label'
        record = self.hist.recs.get(bid)
        if record is None or record['ended'] is not None:
            return f"berth {e['label']}", []
        options = [('label', 0.25)]
        partner = self.hist.partner.get(bid)
        pair_id = self.pair_for.get(frozenset((bid, partner))) if partner else None
        if partner and pair_id and self.hist.recs[partner]['ended'] is None:
            options.append(('partner', 0.6))
        holder, prov = self.holder_of(bid)
        if allow_held and holder not in (None, VACANT) and not (prov & self.fix_ids) and e['kind'] != 'grant' and \
                sum(self.holder_of(b)[0] == holder for b in self.hist.recs if self.hist.recs[b]['ended'] is None) == 1 \
                and not any(self.holder_of(b)[1] & self.fix_ids for b in self.hist.recs
                            if self.hist.recs[b]['ended'] is None):
            options.append(('held', 0.7))
        form = rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]
        if form == 'label':
            return f"berth {e['label']}", []
        e['berth_form'] = form
        e['label'] = self.hist.recs[bid]['labels'][-1][1]
        if form == 'partner':
            label = self.berth_label(partner)
            text = f'the berth next to berth {label}'
            needs = [pair_id]
            first = self.hist.recs[partner]['labels'][0][1]
            if label != first and (partner, label) in self.renumber_of:
                needs.append(self.renumber_of[(partner, label)])
            return text, [{'form': 'berth_partner', 'party': None, 'berth': bid, 'text': text, 'needs': sorted(needs)}]
        party_text, party_record = self.mention(holder, forms=('full', 'short'))
        text = f'the berth held by {party_text}'
        return text, [party_record, {'form': 'berth_holder', 'party': None, 'berth': bid, 'text': text,
                                     'needs': sorted(set(prov) | set(party_record.get('needs', ())))}]

    def r_grant(self, e):
        b, brec = self.berth_ref(e)
        text, rec = self.mention(e['party'], forms=('full', 'short', 'kin', 'trade'), latest=True)
        extra, extra_rec = self.after(e, [('holder', e['party'])])
        return [self.say('grant', berth=b, party=text)] + extra, brec + [rec] + extra_rec

    def r_fee(self, e):
        rng, spec = self.rng, e['spec']
        b, brec = self.berth_ref(e)
        common = {'berth': b, 'year': str(e['year'])}
        if spec['type'] == 'abs':
            return [self.say('fee_abs', amount=amount_text(rng, spec['amount']), **common)], brec
        if spec['type'] == 'rel':
            phrase = rng.choice(sorted(p for p, v in RELATIVE.items() if v == [spec['num'], spec['den']]))
            return [self.say('fee_rel', rel=phrase, **common)], brec
        if spec['target'] == 'adjoin':
            return [self.say('fee_adjoin', **common)], brec
        if spec['target'] == 'parent':
            phrase = rng.choice(sorted(p for p, v in PARENT_FRACTIONS.items() if v == [spec['num'], spec['den']]))
            return [self.say('fee_parent', pfrac=phrase, **common)], brec
        if (spec['num'], spec['den']) == (1, 1):
            return [self.say('fee_same', berth2=spec['other_label'], **common)], brec
        phrase = rng.choice(sorted(p for p, v in FRACTIONS.items() if v == [spec['num'], spec['den']]))
        return [self.say('fee_frac', frac=phrase, berth2=spec['other_label'], **common)], brec

    def r_pair(self, e):
        return [self.say('pair', num=e['labels'][0], berth2=e['labels'][1])], []

    def r_agree(self, e):
        berth, brec = self.berth_ref(e, allow_held=False)
        a, ra = self.mention(e['assignor'], e['berth'] if e['berth_form'] == 'label' else None)
        b, rb = self.mention(e['assignee'], forms=('full', 'short', 'kin', 'trade'), latest=True)
        extra, extra_rec = self.after(e, [('assignee', e['assignee']), ('assignor', e['assignor'])])
        return [self.say('agree', berth=berth, party=a, party2=b)] + extra, brec + [ra, rb] + extra_rec

    def r_register(self, e):
        agree = self.by_id[e['ref']]
        berth, brec = self.berth_ref(e)
        text = self.say('register', berth=berth, agreed=self.dt(agree['date']), date=self.dt(e['when']))
        if e['id'] in self.fix_ids or agree['id'] in self.fix_ids:
            return [text], brec
        extra, extra_rec = self.after(e, [('assignee', agree['assignee']), ('assignor', agree['assignor'])])
        return [text] + extra, brec + extra_rec

    def r_withdraw(self, e):
        agree = self.by_id[e['ref']]
        berth, brec = self.berth_ref(e)
        return [self.say(e['kind'], berth=berth, agreed=self.dt(agree['date']))], brec

    r_refuse = r_withdraw

    def r_death(self, e):
        held = [b for b in sorted(self.hist.recs) if self.holder_of(b)[0] == e['deceased']]
        a, ra = self.mention(e['deceased'], held[0] if held else None, forms=('full', 'short', 'holder'))
        b, rb = self.mention(e['executor'], forms=('full', 'short', 'kin'))
        text = self.say('death', party=a, party2=b, date=self.dt(e['when']))
        extra, extra_rec = self.after(e, [('executor', e['executor']), ('deceased', e['deceased'])])
        self.gone.add(e['deceased'])
        return [text] + extra, [ra, rb] + extra_rec

    def r_estate(self, e):
        berth, brec = self.berth_ref(e)
        text, rec = self.mention(e['heir'], forms=('full', 'short', 'kin', 'trade'), latest=True)
        extra, extra_rec = self.after(e, [('holder', e['heir'])])
        return [self.say('estate', berth=berth, party=text, date=self.dt(e['when']))] + extra, brec + [rec] + extra_rec

    def r_rename(self, e):
        old, rec = self.mention(e['party'], forms=('full', 'short'))
        text = self.say('rename', old=old, name=e['name'], date=self.dt(e['when']))
        self.names[e['party']].append(e['name'])
        extra, extra_rec = self.after(e, [('firm', e['party'])])
        return [text] + extra, [rec] + extra_rec

    def r_merge(self, e):
        a, ra = self.mention(e['merged'][0], forms=('full', 'short'))
        b, rb = self.mention(e['merged'][1], forms=('full', 'short'))
        text = self.say('merge', party=a, party2=b, name=e['name'], date=self.dt(e['when']))
        self.intro.add(e['party'])
        self.names[e['party']] = [e['name']]
        self.gone.update(e['merged'])
        extra, extra_rec = self.after(e, [('merged', e['party'])])
        return [text] + extra, [ra, rb] + extra_rec

    def r_surrender(self, e):
        berth, brec = self.berth_ref(e, allow_held=False)
        a, ra = self.mention(e['party'], e['berth'] if e['berth_form'] == 'label' else None,
                             forms=('full', 'short', 'holder'))
        return [self.say(e['kind'], party=a, berth=berth, date=self.dt(e['when']))], brec + [ra]

    r_forfeit = r_surrender

    def r_renumber(self, e):
        return [self.say('renumber', num=e['old'], berth2=e['label'], date=self.dt(e['when']))], []

    def holder_slot(self, pid, parent):
        if pid == VACANT:
            return 'nobody', {'form': 'vacant', 'party': None, 'text': 'nobody'}
        return self.mention(pid, parent, forms=('full', 'short', 'kin', 'trade', 'holder'), latest=True)

    def r_split(self, e):
        first, second = e['children']
        h1, r1 = self.holder_slot(first['holder'], e['berth'])
        h2, r2 = self.holder_slot(second['holder'], e['berth'])
        text = self.say('split', num=e['label'], berth2=first['label'], berth3=second['label'], hold1=h1, hold2=h2,
                        date=self.dt(e['when']))
        return [text], [r1, r2]

    def r_join(self, e):
        hold, rec = self.holder_slot(e['holder'], e['berths'][self.rng.randrange(2)])
        text = self.say('join', num=e['labels'][0], berth2=e['labels'][1], berth3=e['label'], hold=hold,
                        date=self.dt(e['when']))
        return [text], [rec]

    def r_fix(self, e):
        berth, brec = self.berth_ref(e)
        if e['field'] == 'amount':
            return [self.say('fix_amount', berth=berth, entered=self.dt(e['entered']),
                             amount=amount_text(self.rng, e['value']))], brec
        if e['field'] == 'when':
            return [self.say('fix_when', berth=berth, entry=self.dt(e['entry']), correct=self.dt(e['value']))], brec
        a, ra = self.mention(e['value'], forms=('full', 'short', 'kin', 'trade'), latest=True)
        b, rb = self.mention(e['wrong'], forms=('full', 'short'))
        return [self.say('fix_assignee', berth=berth, agreed=self.dt(e['agreed']), party=a, party2=b)], brec + [ra, rb]

    def r_decoy(self, e):
        a, ra = self.mention(e['party'], forms=('full', 'short'))
        slots = {'party': a}
        if e.get('berth'):
            slots['num'] = e['label']
        template = self.rng.choice([t for t in EVENTS['decoy'] if ('{num}' in t) == bool(e.get('berth'))])
        return [sentence_case(template.format(**slots))], [ra]

    def r_kin(self, e):
        a, ra = self.mention(e['person'])
        b, rb = self.mention(e['anchor'], forms=('full', 'short'))
        self.kin[e['person']] = (e['anchor'], e['relation'])
        return [self.say('kin', party=a, party2=b, relation=e['relation'])], [ra, rb]

    def r_bought(self, e):
        a, ra = self.mention(e['buyer'])
        b, rb = self.mention(e['seller'], forms=('full', 'short'))
        self.trades[e['trade']] = (e['buyer'], e['seller'])
        return [self.say('bought', party=a, party2=b, trade=e['trade'])], [ra, rb]

    # ---- documents
    def context(self):
        labels = [r['labels'][-1][1] for k, r in sorted(self.hist.recs.items()) if r['ended'] is None and r['labels']]
        people = []
        for pid in sorted(self.intro):
            if pid in self.gone:
                continue
            p = self.parties[pid]
            if p['kind'] == 'person':
                people.append(f"{p['title']} {p['surname']}" if self.rng.random() < 0.6 else p['names'][0][1])
            else:
                people.append(self.names[pid][-1])
        return {'labels': labels, 'people': self.rng.sample(people, min(len(people), 8))}

    def render(self, total):
        rng = self.rng
        batches, order = {}, []
        for e in self.events:
            if e['batch'] not in batches:
                batches[e['batch']] = []
                order.append(e['batch'])
            batches[e['batch']].append(e)
        docs = [{'date': batches[b][0]['date'], 'events': batches[b], 'rank': 0, 'seq': batches[b][0]['seq']}
                for b in order]
        n_bulk = int(0.4 * total / 1700)
        span = span_days(self.start, self.end)
        for i in range(n_bulk):
            docs.append({'date': shift(self.start, rng.randrange(span)), 'events': [], 'rank': 1, 'seq': i})
        docs.sort(key=lambda d: (d['date'], d['rank'], d['seq']))
        weight = lambda d: 1.0 + 0.35 * (len(d['events']) - 1) if d['events'] else 1.7  # noqa: E731
        remaining, wsum = total, sum(weight(d) for d in docs)
        first_year = int(self.start[:4])
        files = {}
        passages, locations, texts = [], {}, {}
        for doc in docs:
            number = (int(doc['date'][:4]) - first_year) // 2 + 1
            name = f'correspondence-{number}.md'
            if name not in files:
                years = f'{first_year + 2 * number - 2} and {first_year + 2 * number - 1}'
                files[name] = [f'<a id="F-{number:02d}"></a>\n# Harbour authority correspondence file, part {number} '
                               f'({years})']
            blocks = files[name]
            share = remaining * weight(doc) / wsum
            used = self.compose(doc, blocks, name, share * rng.uniform(0.7, 1.3), passages, locations, texts)
            remaining -= used
            wsum -= weight(doc)
        sources = {name: '\n\n'.join(blocks) + '\n' for name, blocks in sorted(files.items())}
        return sources, passages, locations, texts

    def compose(self, doc, blocks, name, target, passages, locations, texts):
        rng = self.rng
        self.doc_date = doc['date']
        events = doc['events']
        if events:
            # the kind of document does not say whether it holds a decision: the same weights for every document
            kind = rng.choices(('letter', 'note', 'minutes'), weights=(0.4, 0.25, 0.35))[0]
        else:
            kind = rng.choices(('letter', 'note', 'minutes'), weights=(0.4, 0.25, 0.35))[0]
        header_date = date_text(rng, doc['date'], doc['date'], rng.choice((0, 0, 1, 2, 3, 5)))
        start = len(blocks)
        out = [f'### {rng.choice(HEADERS[kind]).format(d=header_date)}']
        if kind == 'letter':
            out.append(rng.choice(('Dear Sir,', 'Dear Sirs,', 'Dear Madam,')))
        elif kind == 'minutes':
            out.append(f'Present: {rng.choice(("the Chairman", "the Vice-Chairman"))} and '
                       f'{words(rng.randint(3, 11))} members, with {rng.choice(ATTENDING)} in attendance'
                       f'{rng.choice(("", "", "; apologies were received from " + words(rng.randint(2, 5)), "; " + words(rng.randint(2, 3)) + " members arrived late", "; the meeting began at " + str(rng.randint(14, 19)) + ":" + rng.choice(("00", "15", "30", "45"))))}.')
        ctx = self.context()
        ctx['kind'] = kind
        # the deciding paragraphs: the sentences of the events, split over one or two paragraphs
        decided = []
        if events:
            groups = [events]
            if len(events) > 1 and rng.random() < 0.5:
                cut = rng.randint(1, len(events) - 1)
                groups = [events[:cut], events[cut:]]
            for group in groups:
                sentences, entries = [], []
                for e in group:
                    own, records = self.render_event(e)
                    sentences.extend(own)
                    entries.append((e, records, own[0]))
                    self.sentence[e['id']] = own[0]
                decided.append((sentences, entries))
        mandatory = sum(len(b) for b in out) + sum(len(' '.join(s)) for s, _ in decided) + 40
        need = max(0, target - mandatory)
        if events:
            inside = need * rng.uniform(0.15, 0.45)
            need -= inside
            for index, (sentences, entries) in enumerate(decided):
                pre = bulk_sentences(rng, ctx, 1 if inside < 160 else rng.randint(1, 3)) if inside > 0 and \
                    rng.random() < 0.6 else []
                post = bulk_sentences(rng, ctx, rng.randint(1, 2)) if inside > 0 and rng.random() < 0.7 else []
                while len(pre) + len(sentences) + len(post) < 3:
                    post.extend(bulk_sentences(rng, ctx, 1))
                decided[index] = (pre + sentences + post, entries)
        paragraphs = []
        while need > 120:
            size = min(need, rng.uniform(250, 900))
            paragraphs.append(bulk_paragraph(rng, ctx, size))
            need -= size + 2
        split = rng.randint(0, len(paragraphs)) if events else len(paragraphs)
        out.extend(paragraphs[:split])
        for sentences, entries in decided:
            index = start + len(out)
            out.append(' '.join(sentences))
            self.para_text[(name, index)] = out[-1]
            grouped = {}
            for e, records, sentence in entries:
                grouped.setdefault(ROLES[e['kind']], []).append((e, records))
                locations[e['id']] = (name, index)
                texts[e['id']] = records
            for role, items in grouped.items():
                resolves = []
                for e, records in items:
                    resolves.extend(r for r in records if r['form'] not in ('full', 'vacant'))
                passages.append({'file': name, 'paragraph': index, 'event_ids': [e['id'] for e, _ in items],
                                 'role': role, 'resolves': resolves})
        out.extend(paragraphs[split:])
        if kind == 'letter':
            out.append(rng.choice(('Yours faithfully,', 'Yours sincerely,', 'I remain, yours faithfully,')))
        blocks.extend(out)
        return sum(len(b) + 2 for b in out)


# ---------------------------------------------------------------------------------------------------------------
# procedure.md
# ---------------------------------------------------------------------------------------------------------------

def procedure(now):
    long_now = date_text(None, now, now, 0)
    paragraphs = [
        '<a id="P-01"></a>\n# How to read the licence correspondence file',
        'This procedure states how the correspondence file of the harbour authority is read. The file holds letters, '
        'file notes and extracts from committee minutes, in the order of their dates, about the mooring and quay '
        'licences of the harbour. The statements in a report are about who held which berth, at what annual fee, '
        f'on which date. In a statement, "now" and "present" mean {long_now}, the day the file closes; no passage is '
        'dated later. Every statement in the reports is settled by the file: it is either supported or refuted.',
        '**What establishes a fact.** Only a passage that records a licence event establishes anything: a grant, an '
        'agreed assignment and what became of it, a death, the settlement of an estate, a change of name, a merger, a '
        'surrender or forfeiture, a renumbering, a split or joining of berths, a pairing of berths, a fee entry or a '
        'correction of an earlier entry, and the passages that say who a person is or what business was sold. '
        'Passages on any other matter (works, weather, complaints, enquiries, committee business, general reminders '
        'about the rules) establish nothing about holders, fees or berth numbers, even when they name a person or a '
        'berth number. A permit for a few days or one occasion (to land stores, to keep a tender alongside a berth, '
        'to use a berth for a night), a payment for services or expenses, a prize, a stall, and a correction of '
        'minutes or of the register of visiting vessels are not licence events, even when a party or a berth '
        'number is named, and even when the words granted, awarded, paid, assigned, altered or corrected are '
        'used.',
        '**Dates.** A date is read as written, in whatever form: "12 March 2016", "March 12, 2016", "12th March '
        '2016", "the 12th of March 2016", "2016-03-12" and "12 Mar 2016" are the same date. A date written without '
        'a year ("12 March", "12th March") is in the year of the date in the heading of the document it stands in. '
        'A passage may be written after the day it reports: the day reported is the day that counts. '
        'A date in a statement is read at the end of that day, so that an event that takes effect on the day is '
        'included.',
        '**Names and descriptions.** A party is a person or a firm. A party may be named by any full name it has '
        'borne (a renamed firm keeps all its earlier names as names for the same firm), by a short form (a title and '
        'the surname, "Mr Hale", or the first word of a firm\'s name, "Halyard"), or by one of these descriptions, '
        'each of which names exactly one party. "The only son (daughter, nephew, niece, brother, sister) of '
        'X" is the person whom a passage of the file introduces as the only such relation of X. "The '
        'purchaser, the buyer or the new owner of the chandlery business" (or of another named business) is the '
        'buyer, and "the seller or the former owner" the seller, in the passage that records the sale of that '
        'business; a sale '
        'of a business does not by itself pass any licence. "The holder or licensee of berth N", also "the present '
        'holder" or "the then holder", is the party who holds berth N, under these rules, at that point of the file, '
        'immediately before the event the sentence reports. Directly after a sentence that reports an event, a role '
        'phrase names the party in that role in that event: "the assignee" or "the incoming holder", "the assignor" '
        'or "the outgoing holder", "the executor" or "the personal representative", "the late holder" or "the '
        'deceased holder", "the firm" or "the renamed firm", "the merged firm" or "the new firm", "the new holder" '
        'or "the present holder". After a register note, the assignee and the assignor are those of the assignment '
        'the note refers to, as corrected. No name, short form or description is ever shared by two parties.',
        '**Describing a berth.** A passage may name a berth by its number, or as "the berth next to berth N", '
        'which is the berth paired with berth N at that point of the file, or as "the berth held by X", which is the '
        'one berth that X holds at that point, immediately before the event the sentence reports (it is used only '
        'where X holds exactly one berth). Such a description stands for the berth, whatever number it carries.',
        '**Berths.** "Berth", "mooring" and "number" in a berth reference mean the same. A berth keeps its identity '
        'when it is renumbered, and every number a berth has borne names that berth for the rest of the file; no '
        'number is ever borne by two berths. When a berth is split, it ceases to exist and the two berths named in the '
        'passage begin to exist; the passage states who holds each of the two (nobody means the berth is vacant). '
        'When two berths are joined, the berth that carries the number named in the passage is the joined berth: it '
        'continues, held by the party the passage names or by nobody, and the other berth ceases and forms part of it '
        'from then on. Two berths are adjoining when a passage says so (the pairing); a pairing ends when either berth '
        'is split or joined. "The adjoining berth", "the berth next to it", "alongside it" and "the berth it is paired '
        'with" name the partner of the berth. "The berth it was split from" and "its parent berth" name the berth that '
        'the passage splitting it divided.',
        '**Holders.** A grant takes effect on the date of the passage that records it. A berth that is surrendered or '
        'forfeited is vacant from the date given, and stays vacant until a later grant; a vacant berth has no holder. '
        '**An assignment** is agreed in a letter and takes effect only on the date on which it is entered in the '
        'register, which is the date the register note gives for the entry. The register note names the berth and '
        'the date of the letter that agreed the assignment, and the assignee is the one the agreement names. An '
        'assignment that is never entered in the register changes nothing; neither does one whose application was '
        'withdrawn or refused. Nothing else follows from the agreement alone. **A death**: from the date of death '
        'stated in the passage, the executor holds every licence that the deceased held on that day. When the estate '
        'is settled, the berth named passes to the person named from the date stated. **A change of name** is not a '
        'change of holder: the firm with its new name is the same holder. **A merger**: the merged firm is a new '
        'holder, and from the date stated it holds every licence either firm held; each of the two firms holds '
        'nothing after that date.',
        '**Corrections.** A note that corrects an earlier entry (the figure of a fee entry, the assignee of an '
        'agreement, the date of a register entry) takes effect as if that entry had always read as corrected: '
        'everything that follows from the entry follows from the corrected one, including a fee that was later '
        'stated as a change of it or as equal to it.',
        '**Fees.** An annual fee is a sum in whole pounds for a berth for a calendar year. A fee entry runs from the '
        'year it names, applies to the whole of that year and to every later year, and holds until a later entry for '
        'the same berth takes over; the entries of one berth are written in the order of their years. The annual fee '
        'of a berth for a year is that of the last entry written that runs from that year or an earlier one. An entry '
        'may give the amount, or a change of the fee given by the previous entry for the berth ("raised by a tenth", '
        '"cut by a fifth", "put up by five per cent"), or the fee of another berth: "the same as that of berth N", "half", "twice" or '
        '"three quarters" of it, "the same as that of the adjoining berth", or a share of the fee of the berth it was '
        'split from. A change is applied to the fee given by the previous entry for the berth. A fee given by '
        'reference is the other berth\'s fee for the year of the entry, as it stood on the day the entry was written, '
        'and stays at that sum even if the other berth\'s fee changes later (a correction, as above, applies as if '
        'always so). Every sum so computed is a whole number of pounds.',
        '**Names in the statements.** A party is named in a statement by the first full name under which the file '
        'names it, without a title ("Arthur Hale", "Halyard & Sons"); the firm that results from a merger is named by '
        'the name the merger passage gives it. A berth is named by the number it first bore in the file: "berth 14" '
        'means the berth that first appears as number 14, whatever it is called now, and a berth that begins by a '
        'split is named by the number the splitting passage gives it. "The berth now numbered N" is the berth that '
        'carries number N at the present.',
        '**The statements.** "Berth B was held by P on D" is true when P is the holder of B at the end of D. "The '
        'annual fee for berth B for Y was A" is true when A is the fee of B for Y under the rules above. "P held n '
        'berths on D" counts the berths that exist at the end of D and are held by P. "The holder of berth B changed '
        'n times between D1 and D2" counts the dates after D1, up to and including D2, on which the holder of B (a '
        'party, or nobody) differs from the day before, the berth existing on both days; a death, a settlement of an '
        'estate, a merger, a surrender, a forfeiture, a grant and an entry in the register each change the holder '
        'when the party differs, and a change of name or a correction of an entry made later does not. "P first held '
        'berth B on D" gives the earliest date on which P was the holder of B. "Berth B now forms part of the berth '
        'now numbered N" names the one present berth that is B itself, or that B was joined into, or that the berths '
        'B was split into are; it is asked only where there is one such berth.',
    ]
    return '\n\n'.join(paragraphs) + '\n'


PREAMBLE = ('Names in these statements follow section "Names in the statements" of the procedure: a party is named by '
            'the first full name under which the file names it, without a title, and "berth 14" means the berth '
            'that first appears in the file as number 14, whatever number it carries now. A date is read at the end '
            'of that day. The present day is the day the file closes. '
            'How a value is written: for a statement about who held a berth, a name; for a fee, a whole number of '
            'pounds; for a number of berths or a number of times, a whole number; for a date, YYYY-MM-DD; for the '
            'number a berth now carries, that number. The word none means that the sources establish that there is '
            'no value, for example that nobody held the berth.')

# ---------------------------------------------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------------------------------------------

CITES_PROCEDURE = ('procedure.md', 'P-01')


def matches(history, query):
    return answer_in(history, query) == query['value']


def party_name(parties, pid):
    return parties[pid]['names'][0][1]


def count_text(rng_style, n):
    if rng_style and n <= 12:
        return ('no berths' if n == 0 else 'one berth' if n == 1 else f'{words(n)} berths')
    return f'{n} berth' + ('' if n == 1 else 's')


def times_text(style, n):
    if style and n <= 12:
        return 'no times' if n == 0 else 'once' if n == 1 else 'twice' if n == 2 else f'{words(n)} times'
    return f'{n} time' + ('' if n == 1 else 's')


class Statements:
    """Candidate statements about the finished log, the choice of 24, and their claims."""

    def __init__(self, rng, world, events, hists, now, renderer, locations):
        self.rng, self.world, self.events, self.hists, self.now = rng, world, events, hists, now
        self.by_id = {e['id']: e for e in events}
        self.renderer, self.locations, self.record_map = renderer, locations, {}
        self.truth = hists[None]
        self.first_label = {k: r['labels'][0][1] for k, r in self.truth.recs.items()}
        self.canon = {pid: p['names'][0][1] for pid, p in world.parties.items()}

    # ---- candidates
    def query(self, kind, **fields):
        return dict(kind=kind, **fields)

    def evaluate(self, q):
        values = {name: answer_in(h, dict(q, value=None)) for name, h in self.hists.items()}
        return values

    def candidates(self):
        rng, T, now = self.rng, self.truth, self.now
        out = []
        bids = sorted(T.recs)

        def add(q):
            values = self.evaluate(q)
            truth = values[None]
            if truth is None or truth == VACANT:
                return
            readers = {name: values[name] for name in READERS}
            parts = {part: values[part] for parts in COMPONENTS.values() for part in parts}
            out.append({'q': q, 'truth': truth, 'readers': readers, 'parts': parts})

        def day(iso, k):
            return min(shift(iso, k), now)

        windows = {}
        for e in self.events:
            if e['kind'] == 'register':
                agree = self.by_id[e['ref']]
                windows.setdefault(e['berth'], []).append((agree['date'], e['when']))
            elif e['kind'] in ('withdraw', 'refuse'):
                agree = self.by_id[e['ref']]
                windows.setdefault(e['berth'], []).append((agree['date'], e['date']))
            elif e['kind'] == 'agree':
                windows.setdefault(e['berth'], []).append((e['date'], shift(e['date'], 30)))
        for bid in bids:
            record = T.recs[bid]
            first = self.first_label[bid]
            end = record['ended'] or now
            dates = set()
            for when, _, holder, _ in record['tl']:
                for k in (1, 2, 9, 30, 90):
                    if rng.random() < 0.5:
                        dates.add(day(when, k))
            for low, high in windows.get(bid, ()):
                if span_days(low, high) > 3:
                    dates.add(shift(low, rng.randint(1, span_days(low, high) - 1)))
            for d in sorted(dates):
                if T.exists(bid, d):
                    add(self.query('holder', berth=bid, label=first, date=d))
            # fees
            years = sorted({_year(day(when, 0)) for when, _, _, _ in record['tl']} |
                           {y for y, _, _, _ in record['fees']} | {_year(now)})
            for y in years:
                if record['fees'] and y >= record['fees'][0][0] and y <= _year(end):
                    add(self.query('fee', berth=bid, label=first, year=y))
            # changes
            starts = [w for w, _, _, _ in record['tl']]
            if starts:
                for _ in range(4):
                    anchor = rng.choice(starts)
                    d1 = shift(anchor, -rng.randint(1, 60))
                    d2 = shift(d1, rng.randint(100, 1100))
                    if d2 <= end and T.exists(bid, d1) and d2 < (record['ended'] or '9999'):
                        add(self.query('changes', berth=bid, label=first, **{'from': d1, 'to': d2}))
                for low, high in windows.get(bid, ())[:2]:
                    d1, d2 = shift(low, -rng.randint(1, 20)), shift(high, rng.randint(5, 60))
                    if d2 <= end and T.exists(bid, d1):
                        add(self.query('changes', berth=bid, label=first, **{'from': d1, 'to': d2}))
            add(self.query('part', berth=bid, label=first))
            seen = []
            for when, _, holder, _ in record['tl']:
                if holder not in (None, VACANT) and holder not in seen:
                    seen.append(holder)
                    add(self.query('first_held', berth=bid, label=first, party=holder))
        parties = sorted({h for r in T.recs.values() for _, _, h, _ in r['tl'] if h not in (None, VACANT)})
        for pid in parties:
            dates = set()
            for r in T.recs.values():
                for when, _, holder, _ in r['tl']:
                    if holder == pid and rng.random() < 0.6:
                        dates.add(day(when, rng.choice((1, 2, 7, 20, 60, 150))))
            for d in sorted(dates):
                add(self.query('count', party=pid, date=d))
        return out

    # ---- classification
    def classify(self, cand):
        q, events = cand['q'], self.by_id
        prov = provenance(self.truth, q)
        cand['prov'] = prov
        kinds = {events[i]['kind'] for i in prov if i in events}
        second = bool(kinds & {'register', 'death', 'merge', 'fix'})
        for i in prov:
            e = events.get(i)
            if e is None:
                continue
            if e['kind'] == 'fee' and e['spec']['type'] != 'abs':
                second = True
            if e.get('label') and 'berth' in e and e['label'] != self.first_label.get(e['berth']) and \
                    e['kind'] not in ('renumber',):
                second = True
            if e.get('berth_form', 'label') != 'label':
                second = True
        if q['kind'] == 'part' and prov:
            second = True
        party = q.get('party') or (cand['truth'] if q['kind'] == 'holder' else None)
        for i in prov:
            for record in self.record_map.get(i, ()):
                if record['form'] != 'full' and record.get('party') and record['party'] == party:
                    second = True
        cand['second'] = second
        # does searching for the full name of the thing concerned miss a deciding passage?
        if q['kind'] == 'count' or q['kind'] == 'first_held':
            needle = re.compile(re.escape(party_name(self.world.parties, q['party'])))
        else:
            needle = re.compile(r'\bberth ' + re.escape(q['label']) + r'\b')
        paragraphs = {self.locations[i] for i in prov if i in self.locations}
        cand['evader'] = any(not needle.search(self.renderer.para_text[p]) for p in paragraphs)
        cand['subjects'] = {('b', q['berth'])} if 'berth' in q else set()
        if 'party' in q:
            cand['subjects'].add(('p', q['party']))
        cand['lures'] = sorted(name for name in READERS if cand['readers'][name] != cand['truth'])
        cand['statable'] = [name for name in cand['lures'] if self.representable(q['kind'], cand['readers'][name])]
        cand['control'] = not cand['lures']
        cand['plain'] = not second and len(prov) == 1
        # which one of its shortcuts a bundled reader turns on for this statement
        cand['devices'] = {name: [part for part in COMPONENTS[name] if cand['parts'][part] != cand['truth']]
                           for name in COMPONENTS if name in cand['lures']}
        cand['single'] = all(len(devices) == 1 for devices in cand['devices'].values())
        cand['cross'] = q['kind'] == 'fee' and any(events[i]['kind'] == 'fee' and events[i]['berth'] != q['berth']
                                                   for i in prov if i in events)

    # ---- choice of 24
    def choose(self, record_map):
        rng = self.rng
        self.record_map = record_map
        pool = self.candidates()
        for cand in pool:
            self.classify(cand)
        rng.shuffle(pool)
        picked, used = [], set()

        def take(cand):
            if cand['subjects'] & used:
                return False
            picked.append(cand)
            used.update(cand['subjects'])
            return True

        kind_count = lambda k: sum(c['q']['kind'] == k for c in picked)  # noqa: E731
        limits = {'holder': 7, 'fee': 8, 'count': 5, 'changes': 4, 'part': 3, 'first_held': 4}

        def room(cand):
            return kind_count(cand['q']['kind']) < limits[cand['q']['kind']]

        def fill(name, wanted):
            have = lambda: sum(name in c['statable'] for c in picked)  # noqa: E731
            options = [c for c in pool if name in c['statable'] and c not in picked]
            options.sort(key=lambda c: (not c['second'], not c['evader'], len(c['lures'])))
            rng.shuffle(options)
            options.sort(key=lambda c: (kind_count(c['q']['kind']) >= limits[c['q']['kind']], not c['single'],
                                        not c['second']))
            for cand in options:
                if have() >= wanted:
                    break
                if room(cand):
                    take(cand)
        for name in READERS:
            fill(name, 5)
        for cand in [c for c in pool if c['cross'] and c not in picked]:
            if sum(c['cross'] for c in picked) >= 4:
                break
            if room(cand):
                take(cand)
        for cand in [c for c in pool if c['q']['kind'] == 'count' and c not in picked]:
            if kind_count('count') >= 3:
                break
            if room(cand):
                take(cand)
        for cand in [c for c in pool if c['control'] and c['plain'] and c not in picked]:
            if sum(c['plain'] for c in picked) >= 3:
                break
            if room(cand):
                take(cand)
        if not kind_count('part'):
            for cand in [c for c in pool if c['q']['kind'] == 'part' and c not in picked][:6]:
                if take(cand):
                    break
        controls = [c for c in pool if c['control'] and c not in picked]
        controls.sort(key=lambda c: not c['second'])
        wanted_controls = 7
        for cand in controls:
            if sum(c['control'] for c in picked) >= wanted_controls:
                break
            if room(cand):
                take(cand)
        rest = [c for c in pool if c not in picked]
        rest.sort(key=lambda c: (not c['second'], not c['evader']))
        for cand in rest:
            if len(picked) >= 24:
                break
            if room(cand):
                take(cand)
        if len(picked) != 24:
            return None
        checks = [sum(name in c['statable'] for c in picked) >= 4 for name in READERS]
        checks.append(sum(c['plain'] for c in picked) >= 3)
        checks.append(sum(c['control'] for c in picked) >= 6)
        checks.append(sum(c['second'] for c in picked) >= 12)
        checks.append(sum(c['evader'] for c in picked) >= 11)
        checks.append(len({c['q']['kind'] for c in picked}) >= 5 and kind_count('part') >= 1)
        checks.append(sum(c['cross'] for c in picked) >= 4 and kind_count('count') >= 3)
        bundled = [c['devices'][name] for c in picked for name in c['statable'] if name in COMPONENTS]
        checks.append(not bundled or sum(len(d) == 1 for d in bundled) >= 0.6 * len(bundled))
        return picked if all(checks) else None

    # ---- claims
    def text(self, q, value, style):
        parties = self.world.parties
        kind = q['kind']
        wording, dform = style['wording'], style['dform']
        templates = STATEMENTS[{'first_held': 'first'}.get(kind, kind)]
        template = templates[wording % len(templates)]
        slots = {'num': q.get('label'), 'date': None}
        if kind == 'holder':
            slots.update(party=party_name(parties, value), date=date_text(None, q['date'], q['date'], dform))
        elif kind == 'fee':
            slots.update(year=str(q['year']), amount=amount_text(style['amount'], value))
        elif kind == 'count':
            slots.update(party=party_name(parties, q['party']), date=date_text(None, q['date'], q['date'], dform),
                         count=count_text(style['words'], value))
        elif kind == 'changes':
            slots.update(d1=date_text(None, q['from'], q['from'], dform), d2=date_text(None, q['to'], q['to'], dform),
                         times=times_text(style['words'], value))
        elif kind == 'part':
            slots.update(label=value)
        else:
            slots.update(party=party_name(parties, q['party']), date=date_text(None, value, value, dform))
        return sentence_case(template.format(**slots))

    def wrong_values(self, cand):
        rng, q, truth = self.rng, cand['q'], cand['truth']
        kind = q['kind']
        taken = {truth} | set(cand['readers'].values())
        out = []
        if kind == 'holder':
            ever = [h for r in self.truth.recs.values() for _, _, h, _ in r['tl'] if h not in (None, VACANT)]
            record = self.truth.recs[q['berth']]
            local = [h for _, _, h, _ in record['tl'] if h not in (None, VACANT)]
            pool = sorted(set(local) | set(rng.sample(sorted(set(ever)), min(6, len(set(ever))))))
            out = [p for p in pool if p not in taken]
        elif kind == 'fee':
            for k in rng.sample(range(1, 13), 12):
                for sign in (-1, 1):
                    if truth + 40 * k * sign > 0 and truth + 40 * k * sign not in taken:
                        out.append(truth + 40 * k * sign)
        elif kind in ('count', 'changes'):
            out = [v for v in (truth + 1, truth - 1, truth + 2, truth + 3) if v >= 0 and v not in taken]
        elif kind == 'part':
            present = sorted({self.truth.label_of(k) for k, r in self.truth.recs.items() if r['ended'] is None})
            out = [p for p in present if p not in taken]
        else:
            record = self.truth.recs[q['berth']]
            dates = [w for w, _, _, _ in record['tl']] + [shift(truth, k) for k in (-90, -31, 17, 45, 120)]
            out = [d for d in dates if d not in taken and d <= self.now]
        rng.shuffle(out)
        return out[:3]

    def slot(self, cand, cites):
        rng, q = self.rng, cand['q']
        kind = q['kind']
        style = {'wording': rng.randrange(12), 'dform': rng.choice((0, 0, 1, 2, 3, 4, 5)),
                 'amount': rng, 'words': rng.random() < 0.4}   # `amount` is the random source for the amount's form

        def make(value, error=None):
            internal = dict(q, value=value)
            public = dict(q, value=public_value(value, q, self.canon))
            text = self.text(q, value, style)
            return claim('succession_' + kind, text, lambda w, query=internal: matches(w['hist'], query), cites,
                         quantity=lambda w, q=q: public_value(answer_in(w['hist'], dict(q, value=None)), q, self.canon),
                         stated=public['value'], error=error) | {
                'query': dict(public, party_name=party_name(self.world.parties, q['party']) if 'party' in q else None)}
        true = make(cand['truth'])
        if cand['control']:
            mutations = [make(v, 'wrong_value') for v in self.wrong_values(cand)]
        else:
            mutations, seen = [], set()
            for name in cand['lures']:
                value = cand['readers'][name]
                if value not in seen and self.representable(kind, value):
                    seen.add(value)
                    mutations.append(make(value, name))
            if not mutations:
                mutations = [make(v, 'wrong_value') for v in self.wrong_values(cand)]
        return true, mutations

    def representable(self, kind, value):
        if value is None or value == NONE_VALUE:
            return False
        return value in self.world.parties if kind == 'holder' else True


# ---------------------------------------------------------------------------------------------------------------
# The case
# ---------------------------------------------------------------------------------------------------------------

def berth_mentions(e):
    """(label as written, berth id) pairs of an event."""
    out = []
    kind = e['kind']
    if kind in ('pair', 'join'):
        out += list(zip(e['labels'], e['berths']))
        if kind == 'join':
            out.append((e['label'], e['survivor']))
    elif kind == 'renumber':
        out += [(e['old'], e['berth']), (e['label'], e['berth'])]
    elif kind == 'split':
        out.append((e['label'], e['berth']))
        out += [(c['label'], c['berth']) for c in e['children']]
    elif e.get('berth') and e.get('berth_form', 'label') == 'label':
        out.append((e['label'], e['berth']))
    if kind == 'fee' and e['spec'].get('other'):
        out.append((e['spec']['other_label'], e['spec']['other']))
    return out


def describe(events, passages, locations_of, parties, first_labels):
    by_id = {e['id']: e for e in events}
    for entry in passages:
        resolves = []
        for record in entry['resolves']:
            item = {'text': record['text'], 'form': record['form'], 'party': record['party'],
                    'party_name': party_name(parties, record['party']) if record['party'] else None}
            if record.get('berth'):
                item['berth'] = record['berth']
            resolves.append(item)
        for event_id in entry['event_ids']:
            for label, bid in berth_mentions(by_id[event_id]):
                if label != first_labels[bid]:
                    resolves.append({'text': label, 'form': 'later_number', 'berth': bid,
                                     'berth_name': first_labels[bid]})
        entry['resolves'] = resolves
    return passages


def events_json(events, outcomes, names):
    out = []
    for e in events:
        item = {k: v for k, v in e.items() if k != 'batch'}
        berths, parties = concerned(e)
        item['berths'], item['parties'] = berths, parties
        item['party_names'] = {p: names[p] for p in parties}
        item['decides'] = e['kind'] != 'decoy'
        item['effect'] = ('none' if e['kind'] in ('agree', 'withdraw', 'refuse', 'decoy') else
                          'identity' if e['kind'] in ('kin', 'bought', 'rename') else
                          'correction' if e['kind'] == 'fix' else 'state')
        if e['kind'] == 'agree':
            item['outcome'] = outcomes.get(e['id'], 'never_registered')
        out.append(item)
    return out


def build(rng):
    """One dossier: a harbour authority's correspondence file about some fifty berths over twelve years."""
    for attempt in range(12):
        world = World(rng)
        events = world.run()
        history = legacy_replay(events)
        now = world.end
        for bid, record in world.h.recs.items():
            assert history.holder(bid, now) == world.h.holder(bid, now), bid
            for year in range(_year(world.start), _year(now) + 1):
                assert history.fee(bid, year) == world.h.fee(bid, year), (bid, year)
        total = rng.randint(350000, 400000)
        renderer = Renderer(rng, world, events)
        sources, passages, locations, records = renderer.render(total)
        size = sum(len(text) for text in sources.values())
        if not 320000 <= size <= 440000:
            continue
        outcomes = {}
        for e in events:
            if e['kind'] == 'register':
                outcomes[e['ref']] = 'registered'
            elif e['kind'] == 'withdraw':
                outcomes[e['ref']] = 'withdrawn'
            elif e['kind'] == 'refuse':
                outcomes[e['ref']] = 'refused'
        listed = events_json(events, outcomes, {pid: p['names'][0][1] for pid, p in world.parties.items()})
        hists = {None: legacy_replay(listed)}
        for name in list(READERS) + [part for parts in COMPONENTS.values() for part in parts]:
            hists[name] = legacy_replay(listed, name)
        assert hists[None].clamped == 0, 'a berth timeline was not written in date order'
        statements = Statements(rng, world, listed, hists, now, renderer, locations)
        picked = statements.choose(records)
        if picked is None:
            continue
        cites = [CITES_PROCEDURE] + [(name, 'F-' + name[15:-3].zfill(2)) for name in sorted(sources)]
        slots = [statements.slot(c, cites) for c in picked]
        sources = dict(sorted(dict(sources, **{'procedure.md': procedure(now)}).items()))
        first = {k: r['labels'][0][1] for k, r in hists[None].recs.items()}
        known = {}
        for pid in sorted(renderer.intro):
            p = world.parties[pid]
            forms = [n for _, n in p['names']]
            if p['kind'] == 'person':
                forms += [f"{p['title']} {p['names'][0][1]}", f"{p['title']} {p['surname']}"]
            else:
                forms += [n.split(' ')[0] for _, n in p['names']]
            known[p['names'][0][1]] = list(dict.fromkeys(forms))
        for k, r in hists[None].recs.items():
            for _, label in r['labels']:        # a number is its own key: a later number is not a written form of the first
                known[label] = [label]
        parties = {pid: {'name': p['names'][0][1], 'kind': p['kind'], 'names': [n for _, n in p['names']],
                         'title': p.get('title')} for pid, p in world.parties.items() if pid in renderer.intro}
        passages = describe(listed, passages, locations, world.parties, first)
        return {'title': TITLE, 'sources': sources, 'base': {'hist': hists[None]}, 'unknowns': {}, 'slots': slots,
                'preamble': PREAMBLE,
                'readers': {name: {'base': {'hist': hists[name]}, 'unknowns': {}} for name in READERS},
                'events': listed, 'passages': passages, 'parties': parties,
                'names': known, 'berths': {k: {'first_label': first[k], 'labels': [l for _, l in hists[None].recs[k]['labels']]}
                           for k in sorted(first)},
                'sentences': renderer.sentence, 'now': now, 'mentions': {k: v for k, v in records.items()},
                'candidates': [{'query': c['q'], 'truth': c['truth'], 'lures': c['lures'], 'second': c['second'],
                                'plain': c['plain'], 'evader': c['evader'], 'deciding': sorted(c['prov'])}
                               for c in picked]}
    raise generate.GenerationError('succession: no dossier met the quotas in 12 attempts')
