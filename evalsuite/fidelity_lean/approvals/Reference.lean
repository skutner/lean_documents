-- Evaluator-only. Concatenate after Interface.lean; core Lean 4.24.0.
namespace Approvals.Reference

def role (w : World) (p : PersonId) : Role := w.roles[p]!

def signers (w : World) (r : Request) : List PersonId :=
  r.signatures.filterMap fun s => match s with
    | .missing => none
    | .named p => some p
    | .illegible => (w.resolutions.find? (fun x => x.1 == r.id)).map Prod.snd

def counting (w : World) (r : Request) : List PersonId :=
  ((signers w r).filter (fun p => p != r.requester && role w p != .analyst)).eraseDups

def meets : Rule := fun w r =>
  let c := counting w r
  if r.amount > w.limit then c.length >= 2 && c.any (fun p => role w p == .finance)
  else c.length >= 1

def boundaryInclusive : Rule := fun w r =>
  let c := counting w r
  if r.amount >= w.limit then c.length >= 2 && c.any (fun p => role w p == .finance)
  else c.length >= 1

def requesterCounts : Rule := fun w r =>
  let c := ((signers w r).filter (fun p => role w p != .analyst)).eraseDups
  if r.amount > w.limit then c.length >= 2 && c.any (fun p => role w p == .finance)
  else c.length >= 1

def financeDropped : Rule := fun w r =>
  let c := counting w r
  if r.amount > w.limit then c.length >= 2 else c.length >= 1

def distinctDropped : Rule := fun w r =>
  let c := (signers w r).filter (fun p => p != r.requester && role w p != .analyst)
  if r.amount > w.limit then c.length >= 2 && c.any (fun p => role w p == .finance)
  else c.length >= 1

inductive ClaimSpec where
  | compliance (id : String) (met : Bool)
  | tier (id : String) (two : Bool)
  | failures (value : Nat) (exact : Bool)
  | signerRole (person : PersonId) (role : Role)
  | illegible (id : String)
  | lookup (id : String) (person : PersonId) (amount : Nat)
  deriving Repr

def test (rule : Rule) (spec : ClaimSpec) (w : World) : Bool :=
  match spec with
  | .failures value exact =>
    let n := (w.requests.filter (fun r => !rule w r)).length
    if exact then n == value else n >= value
  | .signerRole p wanted => role w p == wanted
  | .compliance id met => (w.requests.find? (fun r => r.id == id)).any (fun r => rule w r == met)
  | .tier id two => (w.requests.find? (fun r => r.id == id)).any (fun r => (r.amount > w.limit) == two)
  | .illegible id => (w.requests.find? (fun r => r.id == id)).any
      (fun r => r.signatures[1]? == some .illegible)
  | .lookup id person amount => (w.requests.find? (fun r => r.id == id)).any
      (fun r => r.requester == person && r.amount == amount)
end Approvals.Reference
