"""Named single-slip edits of the slip-free casework and procurement engines. Evaluator-only.

The authored fixtures of the two harder families (``fixtures/Faithful.lean``) are one engine with
every slip behind a string switch, so they name all slips and cannot be shown to an auditor.
``fixtures/Neutral.lean`` is the same engine with every switch off and the plumbing removed. A
planted rule error is that text with the one edit below, which turns one rule off exactly as
the generator's slip of the same name does. Each edit must match its target exactly once. The
planted-error generator then checks the edited engine against the generator's own output for
that slip, so an edit that drifts from the named slip fails closed.

Edits that leave a binder unused rename it with a leading underscore, because the Lean
warning gate rejects an unused variable.
"""
from __future__ import annotations

from pathlib import Path

from evalsuite.fidelity import FidelityError

ROOT = Path(__file__).with_name('fidelity_lean')

CASEWORK = {
    'wrong_class_period': [(
        '  let cls := n.cls\n',
        '  let cls : Class := match n.cls with\n'
        '    | .standard => .complex\n    | .complex => .standard\n    | .urgent => .standard\n')],
    'ignores_amendment': [(
        'def replyPeriod (w : World) (n : Notice) (cls : Class) : Nat :=\n'
        '  let old := ((w.periods.find? (fun entry => entry.1 == cls)).map Prod.snd).get!\n'
        '  if n.issued >= w.effective then\n'
        '    ((w.amended.find? (fun entry => entry.1 == cls)).map Prod.snd).getD old\n'
        '  else old\n',
        'def replyPeriod (w : World) (_n : Notice) (cls : Class) : Nat :=\n'
        '  ((w.periods.find? (fun entry => entry.1 == cls)).map Prod.snd).get!\n')],
    'applies_amendment_too_early': [(
        '  if n.issued >= w.effective then\n',
        '  if n.issued >= w.effective || n.issued + old >= w.effective then\n')],
    'counts_issue_day': [(
        '  let last := n.issued + duration\n',
        '  let last := n.issued + (duration - 1)\n')],
    'ignores_nonworking_shift': [
        ('  let basic := shift w last\n', '  let basic := last\n'),
        ('    else shift w (basic + added)\n', '    else basic + added\n')],
    'ignores_holiday': [(
        'def isWorking (w : World) (d : Date) : Bool :=\n'
        '  decide ((d - 1) % 7 < 5) && !(w.holidays.contains d)\n',
        'def isWorking (_w : World) (d : Date) : Bool :=\n'
        '  decide ((d - 1) % 7 < 5)\n')],
    'ignores_shift_after_extension': [(
        '    else shift w (basic + added)\n', '    else basic + added\n')],
    'counts_refused_extension': [('    | .refused => false\n', '    | .refused => true\n')],
    'counts_pending_extension': [('    | .pending => false\n', '    | .pending => true\n')],
    'counts_void_extension': [
        ('def extensionApplies (w : World) (cls : Class) (basic : Date) (request : Extension) : Bool :=\n',
         'def extensionApplies (_w : World) (cls : Class) (_basic : Date) (request : Extension) : Bool :=\n'),
        ('    | .granted => completeDate w request.date ("decided:" ++ request.ref) <= basic\n',
         '    | .granted => true\n')],
    'extends_urgent_notice': [
        ('def extensionApplies (w : World) (cls : Class) (basic : Date) (request : Extension) : Bool :=\n',
         'def extensionApplies (w : World) (_cls : Class) (basic : Date) (request : Extension) : Bool :=\n'),
        ('  if cls == .urgent then false\n  else match request.decision with\n',
         '  match request.decision with\n')],
    'ignores_extension': [(
        '  let request := w.extensions.find? (fun e => e.nid == n.id)\n'
        '  let valid := request.filter (fun e => extensionApplies w cls basic e)\n',
        '  let valid : Option Extension := none\n')],
    'ignores_postal_rule': [(
        '  | .post =>\n    let dayOne := shift w (posted + 1)\n    shift w (dayOne + 1)\n',
        '  | .post => posted\n')],
    'counts_calendar_days_for_post': [(
        '  | .post =>\n    let dayOne := shift w (posted + 1)\n    shift w (dayOne + 1)\n',
        '  | .post => posted + 2\n')],
    'late_on_deadline_day': [(
        '  let late := decide (deadline < received)\n', '  let late := decide (deadline <= received)\n')],
    'counts_deadline_day_in_days_late': [(
        '  let days := if !late then 0 else received - deadline\n',
        '  let days := if !late then 0 else received - deadline + 1\n')],
    'uses_draft_register': [(
        '  let row := (w.registers[w.inForce]!.find? (fun n => n.id == id)).get!\n',
        '  let row := (w.registers[1 - w.inForce]!.find? (fun n => n.id == id)).get!\n')],
    'takes_neighbouring_row': [(
        '  let row := (w.registers[w.inForce]!.find? (fun n => n.id == id)).get!\n',
        '  let searched := match w.neighbour.find? (fun pair => pair.1 == id) with\n'
        '    | some pair => pair.2\n    | none => id\n'
        '  let row := (w.registers[w.inForce]!.find? (fun n => n.id == searched)).get!\n')],
}

ROLES_ON = (
    'def rolesOn (w : World) (person : String) (day : Nat) : List Role :=\n'
    '  let base := ((w.staff.find? (fun s => s.name == person)).map (fun s => s.role)).toList\n'
    '  let acting := w.delegations.filterMap fun d =>\n'
    '      let last := d.last.getD\n'
    '        (((w.lastDays.find? (fun x => x.1 == d.id)).map Prod.snd).getD 0)\n'
    '      if d.person == person && (decide (d.first <= day) && decide (day <= last))\n'
    '      then some d.role else none\n'
    '  base ++ acting\n')
LIMITS_ON = (
    'def limitsOn (w : World) (day : Nat) : Nat × Nat :=\n'
    '  let current := policy w\n'
    '  let original := (current.low, current.high)\n'
    '  let amended := (w.amendedLow, w.amendedHigh)\n'
    '  if day >= w.effective then amended else original\n')
COUNTING = ('      person != r.requester &&\n'
            '        (rolesOn w person r.date.ordinal).any approving).eraseDups\n')

PROCUREMENT = {
    'counts_requester_signature': [(
        COUNTING, '      (rolesOn w person r.date.ordinal).any approving).eraseDups\n')],
    'ignores_finance_requirement': [
        ('    let wantsFinance := tier == 3\n', ''),
        ('      complies := decide (counting.length >= needed) && (finance || !wantsFinance) }\n',
         '      complies := decide (counting.length >= needed) }\n')],
    'ignores_delegation': [(
        ROLES_ON,
        'def rolesOn (w : World) (person : String) (_day : Nat) : List Role :=\n'
        '  ((w.staff.find? (fun s => s.name == person)).map (fun s => s.role)).toList\n')],
    'applies_delegation_outside_dates': [(
        ROLES_ON,
        'def rolesOn (w : World) (person : String) (_day : Nat) : List Role :=\n'
        '  let base := ((w.staff.find? (fun s => s.name == person)).map (fun s => s.role)).toList\n'
        '  let acting := w.delegations.filterMap fun d =>\n'
        '      if d.person == person then some d.role else none\n'
        '  base ++ acting\n')],
    'ignores_amendment': [(
        LIMITS_ON,
        'def limitsOn (w : World) (_day : Nat) : Nat × Nat :=\n'
        '  let current := policy w\n'
        '  (current.low, current.high)\n')],
    'applies_amendment_too_early': [(
        '  if day >= w.effective then amended else original\n',
        '  if day >= w.amendmentIssued then amended else original\n')],
    'treats_limit_as_exclusive': [(
        '    let tier := if assessed <= limits.1 then 1 else if assessed <= limits.2 then 2 else 3\n',
        '    let tier := if assessed < limits.1 then 1 else if assessed < limits.2 then 2 else 3\n')],
    'uses_superseded_policy': [
        (LIMITS_ON,
         'def limitsOn (w : World) (day : Nat) : Nat × Nat :=\n'
         '  let amended := (w.amendedLow, w.amendedHigh)\n'
         '  if day >= w.effective then amended\n'
         '  else\n'
         '    let previous := policy w true\n'
         '    (previous.low, previous.high)\n'),
        ('    let exempt := (policy w).exempt\n', '    let exempt := (policy w true).exempt\n')],
    'uses_wrong_month_rate': [(
        '    let month := r.date.month\n', '    let month := r.date.month - 1\n')],
    'ignores_same_week_rule': [(
        '    let assessed := (converted.filter (fun pair => sameWeekGroup r pair.1)).foldl\n'
        '        (fun total pair => total + pair.2) 0\n',
        '    let assessed := euro\n')],
    'ignores_conversion': [(
        'def convertedAmount (w : World) (r : Request) : Nat :=\n'
        '  let raw := amount w r\n'
        '  if r.currency == .eur then raw\n'
        '  else\n'
        '    let month := r.date.month\n'
        '    let rate := ((w.rates.find? (fun x => x.currency == r.currency && x.month == month)).map\n'
        '      (fun x => x.value)).getD 0\n'
        '    raw * rate / 10000\n',
        'def convertedAmount (w : World) (r : Request) : Nat :=\n  amount w r\n')],
    'counts_nonapproving_role': [(COUNTING, '      person != r.requester).eraseDups\n')],
}

PATCHES = {'casework': CASEWORK, 'procurement': PROCUREMENT}


def neutral(family: str) -> str:
    return (ROOT / family / 'fixtures' / 'Neutral.lean').read_text()


def plant(family: str, slip: str | None = None) -> str:
    """The slip-free engine, or that engine with exactly one named slip applied."""
    if family not in PATCHES:
        raise FidelityError(f'No slip-free engine for family: {family}')
    text = neutral(family)
    if slip is None:
        return text
    if slip not in PATCHES[family]:
        raise FidelityError(f'Unknown {family} slip: {slip}')
    for old, new in PATCHES[family][slip]:
        if text.count(old) != 1:
            raise FidelityError(f'{family}/{slip}: edit target occurs {text.count(old)} times, not once')
        text = text.replace(old, new)
    return text
