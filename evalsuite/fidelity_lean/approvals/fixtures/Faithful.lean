-- Authored fixture, adapting probe/Audit.lean's existential-pair rule.
-- Interface.lean precedes this file; FixtureClaims.lean supplies claim dispatch.
namespace Candidate
open Approvals

def role (w : World) (p : Nat) : Nat := match w.roles[p]! with
  | .finance => 0 | .manager => 1 | .director => 2 | .analyst => 3

def signatures (w : World) (r : Request) : List Nat :=
  r.signatures.filterMap fun s => match s with
    | .missing => none
    | .named p => some p
    | .illegible => (w.resolutions.find? (fun x => x.1 == r.id)).map Prod.snd

def counting (w : World) (r : Request) : List Nat :=
  (signatures w r).filter (fun p => decide (role w p < 3 ∧ p ≠ r.requester))

def meets (w : World) (r : Request) : Bool :=
  if r.amount ≤ w.limit then decide ((counting w r).length ≥ 1)
  else decide (∃ a ∈ counting w r, ∃ b ∈ counting w r,
    a ≠ b ∧ (role w a = 0 ∨ role w b = 0))
end Candidate
