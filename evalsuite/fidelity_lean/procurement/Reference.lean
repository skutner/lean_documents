-- Evaluator-only. Concatenate after Interface.lean; core Lean 4.24.0.
-- Mirrors hard_procurement.assess, including the single-flag slip semantics.
namespace Procurement.Reference

def amount (w : World) (r : Request) : Nat :=
  r.amount.getD (((w.amounts.find? (fun x => x.1 == r.id)).map Prod.snd).getD 0)

def signatures (w : World) (r : Request) : List String :=
  r.signers.filterMap fun s => match s with
    | some p => some p
    | none => (w.signers.find? (fun x => x.1 == r.id)).map Prod.snd

def rolesOn (w : World) (person : String) (day : Nat) (flag : String) : List Role :=
  let base := ((w.staff.find? (fun s => s.name == person)).map (fun s => s.role)).toList
  let acting := if flag == "delegation" then [] else
    w.delegations.filterMap fun d =>
      let last := d.last.getD
        (((w.lastDays.find? (fun x => x.1 == d.id)).map Prod.snd).getD 0)
      if d.person == person && (flag == "dates" || (decide (d.first <= day) && decide (day <= last)))
      then some d.role else none
  base ++ acting

def policy (w : World) (other : Bool := false) : PolicyIssue :=
  (w.policies.find? (fun p => if other then p.file != w.inForce else p.file == w.inForce)).getD default

def limitsOn (w : World) (day : Nat) (flag : String) : Nat × Nat :=
  let current := policy w
  let original := (current.low, current.high)
  let amended := (w.amendedLow, w.amendedHigh)
  if flag == "amendment" then original
  else if flag == "early" then
    if day >= w.amendmentIssued then amended else original
  else if day >= w.effective then amended
  else if flag == "previous" then
    let previous := policy w true
    (previous.low, previous.high)
  else original

def convertedAmount (w : World) (r : Request) (flag : String) : Nat :=
  let raw := amount w r
  if r.currency == .eur || flag == "conversion" then raw
  else
    let month := if flag == "month" then r.date.month - 1 else r.date.month
    let rate := ((w.rates.find? (fun x => x.currency == r.currency && x.month == month)).map
      (fun x => x.value)).getD 0
    raw * rate / 10000

def sameWeekGroup (a b : Request) : Bool :=
  a.requester == b.requester && a.supplier == b.supplier &&
    (a.date.ordinal - 1) / 7 == (b.date.ordinal - 1) / 7

def approving (role : Role) : Bool :=
  role == .manager || role == .director || role == .finance

def assess (w : World) (flag : String := "") : List Assessment :=
  let converted := w.requests.map (fun r => (r, convertedAmount w r flag))
  converted.map fun (r, euro) =>
    let assessed := if flag == "week" then euro else
      (converted.filter (fun pair => sameWeekGroup r pair.1)).foldl
        (fun total pair => total + pair.2) 0
    let limits := limitsOn w r.date.ordinal flag
    let tier := if flag == "exclusive" then
        if assessed < limits.1 then 1 else if assessed < limits.2 then 2 else 3
      else if assessed <= limits.1 then 1 else if assessed <= limits.2 then 2 else 3
    let exempt := (policy w (flag == "previous")).exempt
    let needed := if exempt.any (fun bound => decide (assessed <= bound)) then 0
      else if tier == 1 then 1 else 2
    let counting := ((signatures w r).filter fun person =>
      (person != r.requester || flag == "requester") &&
        (flag == "approving" || (rolesOn w person r.date.ordinal flag).any approving)).eraseDups
    let finance := counting.any (fun person =>
      (rolesOn w person r.date.ordinal flag).contains .finance)
    let wantsFinance := tier == 3 && flag != "finance"
    { id := r.id, converted := euro, assessed := assessed, tier := tier,
      counting := counting.length, finance := finance,
      complies := decide (counting.length >= needed) && (finance || !wantsFinance) }

def rule : Rule := fun w => assess w

def counts_requester_signature : Rule := fun w => assess w "requester"
def ignores_finance_requirement : Rule := fun w => assess w "finance"
def ignores_delegation : Rule := fun w => assess w "delegation"
def applies_delegation_outside_dates : Rule := fun w => assess w "dates"
def ignores_amendment : Rule := fun w => assess w "amendment"
def applies_amendment_too_early : Rule := fun w => assess w "early"
def treats_limit_as_exclusive : Rule := fun w => assess w "exclusive"
def uses_superseded_policy : Rule := fun w => assess w "previous"
def uses_wrong_month_rate : Rule := fun w => assess w "month"
def ignores_same_week_rule : Rule := fun w => assess w "week"
def ignores_conversion : Rule := fun w => assess w "conversion"
def counts_nonapproving_role : Rule := fun w => assess w "approving"

end Procurement.Reference
