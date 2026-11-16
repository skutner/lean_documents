-- Slip-free procurement engine for blind planted-error files: Faithful.lean with every
-- switch set to "off" and the switch plumbing removed. Evaluator-only; the planted-error
-- generator applies one named edit from fidelity_plants.py.
namespace Candidate
open Procurement

def amount (w : World) (r : Request) : Nat :=
  r.amount.getD (((w.amounts.find? (fun x => x.1 == r.id)).map Prod.snd).getD 0)

def signatures (w : World) (r : Request) : List String :=
  r.signers.filterMap fun s => match s with
    | some p => some p
    | none => (w.signers.find? (fun x => x.1 == r.id)).map Prod.snd

def rolesOn (w : World) (person : String) (day : Nat) : List Role :=
  let base := ((w.staff.find? (fun s => s.name == person)).map (fun s => s.role)).toList
  let acting := w.delegations.filterMap fun d =>
      let last := d.last.getD
        (((w.lastDays.find? (fun x => x.1 == d.id)).map Prod.snd).getD 0)
      if d.person == person && (decide (d.first <= day) && decide (day <= last))
      then some d.role else none
  base ++ acting

def policy (w : World) (other : Bool := false) : PolicyIssue :=
  (w.policies.find? (fun p => if other then p.file != w.inForce else p.file == w.inForce)).getD default

def limitsOn (w : World) (day : Nat) : Nat × Nat :=
  let current := policy w
  let original := (current.low, current.high)
  let amended := (w.amendedLow, w.amendedHigh)
  if day >= w.effective then amended else original

def convertedAmount (w : World) (r : Request) : Nat :=
  let raw := amount w r
  if r.currency == .eur then raw
  else
    let month := r.date.month
    let rate := ((w.rates.find? (fun x => x.currency == r.currency && x.month == month)).map
      (fun x => x.value)).getD 0
    raw * rate / 10000

def sameWeekGroup (a b : Request) : Bool :=
  a.requester == b.requester && a.supplier == b.supplier &&
    (a.date.ordinal - 1) / 7 == (b.date.ordinal - 1) / 7

def approving (role : Role) : Bool :=
  role == .manager || role == .director || role == .finance

def assess (w : World) : List Assessment :=
  let converted := w.requests.map (fun r => (r, convertedAmount w r))
  converted.map fun (r, euro) =>
    let assessed := (converted.filter (fun pair => sameWeekGroup r pair.1)).foldl
        (fun total pair => total + pair.2) 0
    let limits := limitsOn w r.date.ordinal
    let tier := if assessed <= limits.1 then 1 else if assessed <= limits.2 then 2 else 3
    let exempt := (policy w).exempt
    let needed := if exempt.any (fun bound => decide (assessed <= bound)) then 0
      else if tier == 1 then 1 else 2
    let counting := ((signatures w r).filter fun person =>
      person != r.requester &&
        (rolesOn w person r.date.ordinal).any approving).eraseDups
    let finance := counting.any (fun person =>
      (rolesOn w person r.date.ordinal).contains .finance)
    let wantsFinance := tier == 3
    { id := r.id, converted := euro, assessed := assessed, tier := tier,
      counting := counting.length, finance := finance,
      complies := decide (counting.length >= needed) && (finance || !wantsFinance) }

def rule : Rule := fun w => assess w

end Candidate
