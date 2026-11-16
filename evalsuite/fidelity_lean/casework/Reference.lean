-- Evaluator-only; concatenate after Interface.lean. Lean 4.24, Std only.
namespace Casework.Reference

def notices (w : World) : List Notice := w.registers[w.inForce]!

def off (w : World) (day : Date) (ignoreHolidays : Bool) : Bool :=
  (day - 1) % 7 >= 5 || (!ignoreHolidays && w.holidays.contains day)

-- In any 7*(H+2) consecutive positive ordinals there are H+2 distinct Mondays.
-- At most H are listed holidays, so at least two working days occur. This bound
-- therefore suffices both for shifting and for second-working-day receipt.
-- Exhaustion is an invariant error, never a silently substituted deadline.
def workingOnOrAfter (w : World) (day : Date) (ignoreHolidays : Bool) : Date :=
  match (List.range (7 * (w.holidays.length + 2))).find?
      (fun offset => !off w (day + offset) ignoreHolidays) with
  | some offset => day + offset
  | none => panic! "casework: impossible calendar search exhaustion"

def recordedOrResolved (w : World) (key : String) (recorded : Option Date) : Date :=
  match recorded.orElse (fun _ => (w.resolutions.find? (fun x => x.1 == key)).map Prod.snd) with
  | some day => day
  | none => panic! "casework: incomplete world"

def wrongClass : Class → Class
  | .standard => .complex
  | .complex => .standard
  | .urgent => .standard

def period (w : World) (n : Notice) (slip : String) : Class × Nat :=
  let cls := if slip == "wrong_class_period" then wrongClass n.cls else n.cls
  let original := ((w.periods.find? (fun p => p.1 == cls)).map Prod.snd).get!
  let replacement := (w.amended.find? (fun p => p.1 == cls)).map Prod.snd
  let applies := n.issued >= w.effective ||
    (slip == "applies_amendment_too_early" && n.issued + original >= w.effective)
  (cls, if slip != "ignores_amendment" && applies then replacement.getD original else original)

def evaluate (w : World) (n : Notice) (ext : Option Extension) (slip : String) : Result :=
  let (cls, days) := period w n slip
  let last := n.issued + (days - if slip == "counts_issue_day" then 1 else 0)
  let move := fun d => if slip == "ignores_nonworking_shift" then d
    else workingOnOrAfter w d (slip == "ignores_holiday")
  let basic := move last
  let counted := ext.any fun e =>
    e.decision == .granted ||
    (slip == "counts_refused_extension" && e.decision == .refused) ||
    (slip == "counts_pending_extension" && e.decision == .pending)
  let inTime := ext.any fun e =>
    e.decision != .granted || slip == "counts_void_extension" ||
      recordedOrResolved w ("decided:" ++ e.ref) e.date <= basic
  let took := slip != "ignores_extension" && counted && inTime &&
    (cls != .urgent || slip == "extends_urgent_notice")
  let extra := if took then (ext.map Extension.days).getD 0 else 0
  let deadline := if took then
      if slip == "ignores_shift_after_extension" then basic + extra else move (basic + extra)
    else basic
  let sent := recordedOrResolved w ("sent:" ++ n.id) n.sent
  let received := if n.channel != .post || slip == "ignores_postal_rule" then sent
    else if slip == "counts_calendar_days_for_post" then sent + 2
    else
      let first := workingOnOrAfter w (sent + 1) (slip == "ignores_holiday")
      workingOnOrAfter w (first + 1) (slip == "ignores_holiday")
  let late := if slip == "late_on_deadline_day" then received >= deadline else received > deadline
  let daysLate := if late then received - deadline +
      (if slip == "counts_deadline_day_in_days_late" then 1 else 0) else 0
  ⟨deadline, basic, took, deadline > last + extra, received, late, daysLate⟩

def withSlip (slip : String) : Rule := fun w ident =>
  let target := if slip == "takes_neighbouring_row" then
      ((w.neighbour.find? (fun p => p.1 == ident)).map Prod.snd).getD ident
    else ident
  let rows := if slip == "uses_draft_register" then w.registers[1 - w.inForce]! else notices w
  let n := (rows.find? (fun r => r.id == target)).get!
  evaluate w n (w.extensions.find? (fun e => e.nid == target)) slip

def applyRule : Rule := withSlip ""

inductive ClaimSpec where
  | deadlineDate (id : String) (date : Date) (exact : Bool)
  | replyTiming (id : String) (late : Bool)
  | daysLate (id : String) (days : Nat)
  | lateReplies (value : Nat) (exact : Bool)
  | shiftedDeadlines (value : Nat) (exact : Bool)
  | extensionsInEffect (value : Nat) (exact : Bool)
  | extensionEffect (id : String) (took : Bool)
  | deadlineExtreme (cls : Class) (latest : Bool) (id : String) (date : Date)
  | noticeLookup (id : String) (date : Date)
  | receiptDate (id : String) (date : Date) (exact : Bool)
  | lateRepliesByPost (value : Nat) (exact : Bool)
  deriving Repr

def bound (actual wanted : Nat) (exact : Bool) : Bool :=
  if exact then actual == wanted else actual >= wanted

def tally (rule : Rule) (w : World) (select : Notice → Bool) (field : Result → Bool) : Nat :=
  ((notices w).filter (fun n => select n && field (rule w n.id))).length

def test (rule : Rule) (spec : ClaimSpec) (w : World) : Bool :=
  match spec with
  | .deadlineDate id date exact => bound (rule w id).deadline date exact
  | .replyTiming id late => (rule w id).late == late
  | .daysLate id days => (rule w id).late && (rule w id).days == days
  | .lateReplies value exact => bound (tally rule w (fun _ => true) Result.late) value exact
  | .shiftedDeadlines value exact => bound (tally rule w (fun _ => true) Result.shifted) value exact
  | .extensionsInEffect value exact => bound (tally rule w (fun _ => true) Result.took) value exact
  | .extensionEffect id took => (rule w id).took == took
  | .deadlineExtreme cls latest id date =>
    let dated := ((notices w).filter (fun n => n.cls == cls)).map (fun n => (n.id, (rule w n.id).deadline))
    match dated with
    | [] => false
    | (_, first) :: rest =>
      let top := rest.foldl (fun best p => if latest then max best p.2 else min best p.2) first
      top == date && ((dated.filter (fun p => p.2 == top)).map Prod.fst) == [id]
  | .noticeLookup id date => ((notices w).find? (fun n => n.id == id)).any (fun n => n.issued == date)
  | .receiptDate id date exact => bound (rule w id).received date exact
  | .lateRepliesByPost value exact =>
    bound (tally rule w (fun n => n.channel == .post) Result.late) value exact
end Casework.Reference
