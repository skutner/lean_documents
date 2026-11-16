-- Evaluator-only reference and one-rule mutations. Interface.lean precedes this.
namespace Deadlines.Reference

def version (w : World) (n : Notice) : Nat := if n.issued < w.change then 1 else 2

def period (w : World) (v : Nat) : Nat := if v == 1 then w.firstPeriod else w.secondPeriod

def granted (w : World) (n : Notice) : Bool := match n.extension with
  | .granted => true
  | .undecided => ((w.grants.find? (fun x => x.1 == n.id)).map Prod.snd).getD false
  | _ => false

def reply (w : World) (n : Notice) : Option Date := match n.reply with
  | some d => some d
  | none => ((w.replies.find? (fun x => x.1 == n.id)).map Prod.snd).getD none

def outcome (w : World) (n : Notice) (v : Nat) (d : Date) : Outcome :=
  let received := reply w n
  ⟨v, d, received.map (fun r => r - d), received.any (fun r => r > d),
    received.any (fun r => r <= d)⟩

def deadline (w : World) (n : Notice) (v : Nat) (extended : Bool) : Date :=
  n.issued + Int.ofNat (period w v + if extended then n.days else 0)

def applyRule : Rule := fun w n =>
  let v := version w n
  outcome w n v (deadline w n v (granted w n))

def wrongRuleVersion : Rule := fun w n =>
  let v := 3 - version w n
  outcome w n v (deadline w n v (granted w n))

def changeDayUsesOld : Rule := fun w n =>
  let v := if n.issued <= w.change then 1 else 2
  outcome w n v (deadline w n v (granted w n))

def wrongPeriod : Rule := fun w n =>
  let v := version w n
  outcome w n v (deadline w n v (granted w n) + 1)

def countsNoticeDay : Rule := fun w n =>
  let v := version w n
  outcome w n v (deadline w n v (granted w n) - 1)

def ignoresExtension : Rule := fun w n =>
  let v := version w n
  outcome w n v (deadline w n v false)

def refusedGranted : Rule := fun w n =>
  let v := version w n
  outcome w n v (deadline w n v (granted w n || n.extension == .refused))

def undecidedGranted : Rule := fun w n =>
  let v := version w n
  outcome w n v (deadline w n v (granted w n || n.extension == .undecided))

def deadlineIsLate : Rule := fun w n =>
  let out := applyRule w n
  { out with late := (reply w n).any (fun r => r >= out.deadline),
             onTime := (reply w n).any (fun r => r < out.deadline) }

def wrongDayCount : Rule := fun w n =>
  let out := applyRule w n
  { out with daysLate := out.daysLate.map (fun d => d + 1) }

def missingReplyIsLate : Rule := fun w n =>
  let out := applyRule w n
  { out with late := out.late || (reply w n).isNone }

inductive ClaimSpec where
  | deadline (id : String) (date : Date)
  | onTime (id : String)
  | daysLate (id : String) (days : Int)
  | version (id : String) (value : Nat)
  | lateCount (value : Nat) (exact : Bool)
  | unrecorded (id : String)
  | undecided (id : String)
  | issued (id : String) (date : Date)
  deriving Repr

def test (rule : Rule) (spec : ClaimSpec) (w : World) : Bool :=
  match spec with
  | .lateCount value exact =>
    let count := (w.notices.filter (fun n => (rule w n).late)).length
    if exact then count == value else count >= value
  | .deadline id d => (w.notices.find? (fun n => n.id == id)).any (fun n => (rule w n).deadline == d)
  | .onTime id => (w.notices.find? (fun n => n.id == id)).any (fun n => (rule w n).onTime)
  | .daysLate id d => (w.notices.find? (fun n => n.id == id)).any (fun n => (rule w n).daysLate == some d)
  | .version id v => (w.notices.find? (fun n => n.id == id)).any (fun n => (rule w n).version == v)
  | .unrecorded id => (w.notices.find? (fun n => n.id == id)).any (fun n => n.reply.isNone)
  | .undecided id => (w.notices.find? (fun n => n.id == id)).any (fun n => n.extension == .undecided)
  | .issued id d => (w.notices.find? (fun n => n.id == id)).any (fun n => n.issued == d)
end Deadlines.Reference
