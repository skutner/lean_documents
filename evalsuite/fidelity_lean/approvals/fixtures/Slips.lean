-- Evaluator fixture candidates: each changes exactly one condition of the rule.
namespace Candidate
open Approvals

def distinctCounting (w : World) (r : Request) : List Nat := (counting w r).eraseDups

def boundaryInclusive : Rule := fun w r =>
  let c := distinctCounting w r
  if r.amount >= w.limit then c.length >= 2 && c.any (fun p => role w p == 0)
  else c.length >= 1

def requesterCounts : Rule := fun w r =>
  let c := ((signatures w r).filter (fun p => role w p < 3)).eraseDups
  if r.amount > w.limit then c.length >= 2 && c.any (fun p => role w p == 0)
  else c.length >= 1

def financeDropped : Rule := fun w r =>
  let c := distinctCounting w r
  if r.amount > w.limit then c.length >= 2 else c.length >= 1

def distinctDropped : Rule := fun w r =>
  let c := counting w r
  if r.amount > w.limit then c.length >= 2 && c.any (fun p => role w p == 0)
  else c.length >= 1
end Candidate
