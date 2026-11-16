-- Authored candidates; no aliases of reference mutations.
namespace Candidate
open Deadlines

def wrongRuleVersion : Rule := fun w n =>
  let v := if n.issued >= w.change then 1 else 2
  finish w n v (n.issued + Int.ofNat (if v == 1 then w.firstPeriod else w.secondPeriod) +
    Int.ofNat (if extended w n then n.days else 0))

def changeDayUsesOld : Rule := fun w n =>
  if n.issued == w.change then
    finish w n 1 (n.issued + Int.ofNat w.firstPeriod + Int.ofNat (if extended w n then n.days else 0))
  else applyRule w n

def wrongPeriod : Rule := fun w n =>
  let original := applyRule w n
  finish w n original.version (original.deadline + 1)

def countsNoticeDay : Rule := fun w n =>
  let original := applyRule w n
  finish w n original.version (original.deadline - 1)

def ignoresExtension : Rule := fun w n => applyRule w { n with days := 0 }

def refusedGranted : Rule := fun w n =>
  applyRule w (if n.extension == .refused then { n with extension := .granted } else n)

def undecidedGranted : Rule := fun w n =>
  applyRule w (if n.extension == .undecided then { n with extension := .granted } else n)

def deadlineIsLate : Rule := fun w n =>
  let original := applyRule w n
  { original with late := (received w n).any (fun r => r >= original.deadline),
                  onTime := (received w n).any (fun r => r < original.deadline) }

def wrongDayCount : Rule := fun w n =>
  let original := applyRule w n
  { original with daysLate := (received w n).map (fun r => r + 1 - original.deadline) }

def missingReplyIsLate : Rule := fun w n =>
  let original := applyRule w n
  { original with late := match received w n with | none => true | some _ => original.late }
end Candidate
