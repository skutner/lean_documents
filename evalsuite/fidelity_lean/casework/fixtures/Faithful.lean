-- Authored fixture engine, independent of Casework.Reference.
-- Interface.lean precedes this file; generated FixtureClaims supplies model dispatch.
namespace Candidate
open Casework

def isWorking (w : World) (ignoreHolidays : Bool) (d : Date) : Bool :=
  decide ((d - 1) % 7 < 5) && (ignoreHolidays || !(w.holidays.contains d))

def findWorking (w : World) (ignoreHolidays : Bool) : Nat → Date → Option Date
  | 0, _ => none
  | fuel + 1, d => if isWorking w ignoreHolidays d then some d
      else findWorking w ignoreHolidays fuel (d + 1)

def shift (w : World) (ignoreHolidays : Bool) (d : Date) : Date :=
  -- Each complete week has a distinct Monday. H holidays cannot block all
  -- H+2 Mondays in this window, even when every holiday is a working day.
  match findWorking w ignoreHolidays (w.holidays.length * 7 + 14) d with
  | some answer => answer
  | none => panic! "casework fixture: exhausted proven calendar bound"

def completeDate (w : World) (recorded : Option Date) (key : String) : Date :=
  match recorded with
  | some d => d
  | none => match w.resolutions.find? (fun entry => entry.1 == key) with
    | some entry => entry.2
    | none => panic! "casework fixture: missing date completion"

def effectiveClass (slip : String) (cls : Class) : Class :=
  if slip != "wrong_class_period" then cls else
    match cls with
    | .standard => .complex
    | .complex => .standard
    | .urgent => .standard

def replyPeriod (w : World) (n : Notice) (cls : Class) (slip : String) : Nat :=
  let old := ((w.periods.find? (fun entry => entry.1 == cls)).map Prod.snd).get!
  if slip == "ignores_amendment" then old
  else if n.issued >= w.effective ||
      (slip == "applies_amendment_too_early" && n.issued + old >= w.effective) then
    ((w.amended.find? (fun entry => entry.1 == cls)).map Prod.snd).getD old
  else old

def extensionApplies (w : World) (cls : Class) (basic : Date)
    (request : Extension) (slip : String) : Bool :=
  if slip == "ignores_extension" then false
  else if cls == .urgent && slip != "extends_urgent_notice" then false
  else match request.decision with
    | .pending => slip == "counts_pending_extension"
    | .refused => slip == "counts_refused_extension"
    | .granted => slip == "counts_void_extension" ||
        completeDate w request.date ("decided:" ++ request.ref) <= basic

def delivery (w : World) (n : Notice) (slip : String) : Date :=
  let posted := completeDate w n.sent ("sent:" ++ n.id)
  match n.channel with
  | .electronic | .hand => posted
  | .post =>
    if slip == "ignores_postal_rule" then posted
    else if slip == "counts_calendar_days_for_post" then posted + 2
    else
      let dayOne := shift w (slip == "ignores_holiday") (posted + 1)
      shift w (slip == "ignores_holiday") (dayOne + 1)

def evaluateRow (w : World) (n : Notice) (slip : String) : Result :=
  let cls := effectiveClass slip n.cls
  let duration := replyPeriod w n cls slip
  let last := n.issued + (duration - if slip == "counts_issue_day" then 1 else 0)
  let basic := if slip == "ignores_nonworking_shift" then last
    else shift w (slip == "ignores_holiday") last
  let request := w.extensions.find? (fun e => e.nid == n.id)
  let valid := request.filter (fun e => extensionApplies w cls basic e slip)
  let took := valid.isSome
  let added := (valid.map Extension.days).getD 0
  let deadline := if !took then basic
    else if slip == "ignores_nonworking_shift" || slip == "ignores_shift_after_extension" then basic + added
    else shift w (slip == "ignores_holiday") (basic + added)
  let received := delivery w n slip
  let late := if slip == "late_on_deadline_day" then decide (deadline <= received)
    else decide (deadline < received)
  let days := if !late then 0 else received - deadline +
    (if slip == "counts_deadline_day_in_days_late" then 1 else 0)
  { deadline := deadline, basic := basic, took := took,
    shifted := decide (last + added < deadline), received := received, late := late, days := days }

def evaluateVariant (slip : String) : Rule := fun w id =>
  let register := if slip == "uses_draft_register" then 1 - w.inForce else w.inForce
  let searched := if slip == "takes_neighbouring_row" then
      match w.neighbour.find? (fun pair => pair.1 == id) with
      | some pair => pair.2
      | none => id
    else id
  let row := (w.registers[register]!.find? (fun n => n.id == searched)).get!
  evaluateRow w row slip

def applyRule : Rule := evaluateVariant ""
end Candidate
