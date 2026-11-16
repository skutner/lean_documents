-- Authored candidate. No evaluator reference definitions are used.
namespace Candidate
open Ledger

def active (w : World) : List Entry := w.entries.filter (fun e => e.voided == false)

def absent (w : World) (e : Entry) : Bool :=
  e.approval == .absent || (e.approval == .unknown &&
    (w.approvals.find? (fun p => p.1 == e.id)).any (fun p => !p.2))

def amountSum (es : List Entry) : Nat := es.foldr (fun e acc => e.amount + acc) 0

def maximum (es : List Entry) : Option (String × Nat) :=
  match es with
  | [] => none
  | e :: rest => some (rest.foldl (fun best next =>
      if next.amount > best.2 then (next.id, next.amount) else best) (e.id, e.amount))

def compute (w : World) (es : List Entry) (isMissing : Entry → Bool) : Summary :=
  let ms := es.filter isMissing
  { total := amountSum es
    gap := Int.ofNat (amountSum es) - w.budget
    largest := maximum es
    missingCount := ms.length
    missingAmount := amountSum ms
    recordedCount := (es.filter (fun e => e.approval == .present)).length
    populationMissing := ms.length + w.unsampledMissing }

def rules : Rule := fun w => compute w (active w) (absent w)
end Candidate
