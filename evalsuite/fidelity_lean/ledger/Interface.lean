import Std

namespace Ledger

inductive Approval where
  | present | absent | unknown
  deriving BEq, DecidableEq, Repr, Inhabited

structure Entry where
  id : String
  payee : String
  -- Gregorian ordinal, with 0001-01-01 represented by 1.
  date : Nat
  ledgerAmount : Nat
  amount : Nat
  approval : Approval
  voided : Bool
  deriving BEq, DecidableEq, Repr, Inhabited

structure World where
  entries : List Entry
  budget : Int
  population : Nat
  -- Entry ID to approval-present status in this completed world.
  approvals : List (String × Bool)
  unsampledMissing : Nat
  deriving BEq, DecidableEq, Repr, Inhabited

structure Dossier where
  recorded : World
  approvalUnknowns : List (String × List Bool)
  unsampledChoices : List Nat
  deriving BEq, DecidableEq, Repr, Inhabited

structure Summary where
  total : Nat
  -- Signed sample-total minus budget.
  gap : Int
  largest : Option (String × Nat)
  missingCount : Nat
  missingAmount : Nat
  recordedCount : Nat
  populationMissing : Nat
  deriving BEq, DecidableEq, Repr, Inhabited

abbrev Rule := World → Summary
abbrev ClaimTest := World → Bool

structure Model where
  rule : Rule
  -- IDs are report-qualified, for example "correct/C01".
  claim : String → ClaimTest

-- Implement Candidate.model : Ledger.Model. Unknown approvals and unsampled
-- counts are completed by their Cartesian product. Recorded approval markers
-- remain unchanged. Evaluator worlds have distinct entry IDs, at least one
-- nonvoid entry, nonnegative amounts, and an unsampled count no greater than
-- population minus the number of nonvoid entries. Annex statements are outside
-- this interface; their independent unknowns can repeat an operational world.
end Ledger
