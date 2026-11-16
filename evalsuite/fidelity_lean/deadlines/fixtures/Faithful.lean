-- Authored candidate: independent computation, with no reference dependency.
namespace Candidate
open Deadlines

def received (w : World) (n : Notice) : Option Date :=
  n.reply.orElse (fun _ => ((w.replies.find? (fun p => p.1 == n.id)).map Prod.snd).getD none)

def extended (w : World) (n : Notice) : Bool :=
  n.extension == .granted || (n.extension == .undecided &&
    ((w.grants.find? (fun p => p.1 == n.id)).map Prod.snd).getD false)

def finish (w : World) (n : Notice) (v : Nat) (date : Date) : Outcome :=
  match received w n with
  | none => ⟨v, date, none, false, false⟩
  | some r => ⟨v, date, some (r - date), decide (date < r), decide (r <= date)⟩

def applyRule : Rule := fun w n =>
  let v := if n.issued >= w.change then 2 else 1
  let days := if v == 2 then w.secondPeriod else w.firstPeriod
  let extra := if extended w n then n.days else 0
  finish w n v (n.issued + Int.ofNat days + Int.ofNat extra)
end Candidate
