-- Slip-free casework engine for blind planted-error files. It is Faithful.lean with
-- every switch set to "off" and the switch plumbing removed; the public rule is `rule`.
-- Evaluator-only: the planted-error generator applies one named edit from fidelity_plants.py.
namespace Candidate
open Casework

def isWorking (w : World) (d : Date) : Bool :=
  decide ((d - 1) % 7 < 5) && !(w.holidays.contains d)

def findWorking (w : World) : Nat → Date → Option Date
  | 0, _ => none
  | fuel + 1, d => if isWorking w d then some d
      else findWorking w fuel (d + 1)

def shift (w : World) (d : Date) : Date :=
  -- Each complete week has a distinct Monday, so the search below always succeeds.
  match findWorking w (w.holidays.length * 7 + 14) d with
  | some answer => answer
  | none => panic! "calendar search exhausted"

def completeDate (w : World) (recorded : Option Date) (key : String) : Date :=
  match recorded with
  | some d => d
  | none => match w.resolutions.find? (fun entry => entry.1 == key) with
    | some entry => entry.2
    | none => panic! "missing date completion"

def replyPeriod (w : World) (n : Notice) (cls : Class) : Nat :=
  let old := ((w.periods.find? (fun entry => entry.1 == cls)).map Prod.snd).get!
  if n.issued >= w.effective then
    ((w.amended.find? (fun entry => entry.1 == cls)).map Prod.snd).getD old
  else old

def extensionApplies (w : World) (cls : Class) (basic : Date) (request : Extension) : Bool :=
  if cls == .urgent then false
  else match request.decision with
    | .pending => false
    | .refused => false
    | .granted => completeDate w request.date ("decided:" ++ request.ref) <= basic

def delivery (w : World) (n : Notice) : Date :=
  let posted := completeDate w n.sent ("sent:" ++ n.id)
  match n.channel with
  | .electronic | .hand => posted
  | .post =>
    let dayOne := shift w (posted + 1)
    shift w (dayOne + 1)

def evaluateRow (w : World) (n : Notice) : Result :=
  let cls := n.cls
  let duration := replyPeriod w n cls
  let last := n.issued + duration
  let basic := shift w last
  let request := w.extensions.find? (fun e => e.nid == n.id)
  let valid := request.filter (fun e => extensionApplies w cls basic e)
  let took := valid.isSome
  let added := (valid.map Extension.days).getD 0
  let deadline := if !took then basic
    else shift w (basic + added)
  let received := delivery w n
  let late := decide (deadline < received)
  let days := if !late then 0 else received - deadline
  { deadline := deadline, basic := basic, took := took,
    shifted := decide (last + added < deadline), received := received, late := late, days := days }

def rule : Rule := fun w id =>
  let row := (w.registers[w.inForce]!.find? (fun n => n.id == id)).get!
  evaluateRow w row
end Candidate
