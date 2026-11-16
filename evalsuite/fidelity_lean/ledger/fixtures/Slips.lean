-- Each authored candidate bends one named rule, independently of Reference.
namespace Candidate
open Ledger

def includesVoided : Rule := fun w => compute w w.entries (absent w)
def supersededAmount : Rule := fun w =>
  compute w ((active w).map (fun e => { e with amount := e.ledgerAmount })) (absent w)
def unknownAsAbsent : Rule := fun w =>
  compute w (active w) (fun e => e.approval != .present)
def unknownAsPresent : Rule := fun w =>
  compute w (active w) (fun e => e.approval == .absent)
def unsampledIgnored : Rule := fun w =>
  let s := rules w
  { s with populationMissing := s.missingCount }
def extrapolatesSampleRate : Rule := fun w =>
  let es := active w
  let numerator := (es.filter (fun e => e.approval == .absent)).length * w.population
  let denominator := es.length
  let lower := numerator / denominator
  let remainder := numerator % denominator
  let up := remainder * 2 > denominator ||
    (remainder * 2 == denominator && lower % 2 != 0)
  { rules w with populationMissing := lower + if up then 1 else 0 }
def reversedDirection : Rule := fun w =>
  { rules w with gap := w.budget - Int.ofNat (amountSum (active w)) }
def arithmeticSlip : Rule := fun w =>
  let s := rules w
  { s with total := s.total + 1, gap := s.gap + 1 }
def missingSheetAsPresent : Rule := fun w =>
  { rules w with recordedCount := ((active w).filter (fun e => e.approval != .absent)).length }
def wrongEntry : Rule := fun w =>
  let least := (active w).foldr (fun e best => match best with
    | none => some (e.id, e.amount)
    | some p => if e.amount <= p.2 then some (e.id, e.amount) else best) none
  { rules w with largest := least }
end Candidate
