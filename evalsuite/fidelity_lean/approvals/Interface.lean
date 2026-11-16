import Std

namespace Approvals

inductive Role where
  | finance | manager | director | analyst
  deriving BEq, DecidableEq, Repr, Inhabited

abbrev PersonId := Nat

-- A recorded signature is distinct from the identity assigned in a completed world.
inductive Signature where
  | missing
  | named (person : PersonId)
  | illegible
  deriving BEq, DecidableEq, Repr, Inhabited

structure Request where
  id : String
  requester : PersonId
  amount : Nat
  signatures : List Signature
  deriving BEq, DecidableEq, Repr, Inhabited

structure World where
  people : List String
  roles : List Role
  limit : Nat
  requests : List Request
  -- Request ID to the identity of its illegible signature, if present.
  resolutions : List (String × PersonId)
  deriving BEq, DecidableEq, Repr, Inhabited

structure Dossier where
  recorded : World
  -- All allowed identities for each illegible signature; Cartesian completion.
  unknowns : List (String × List PersonId)
  deriving BEq, DecidableEq, Repr, Inhabited

abbrev Rule := World → Request → Bool
abbrev ClaimTest := World → Bool

structure Model where
  rule : Rule
  -- Claim IDs are report-qualified, e.g. "correct/C01" and "mixed/C01".
  claim : String → ClaimTest

-- Implement `Candidate.model : Approvals.Model` in the submitted Lean file.
-- Person IDs index people and roles. Evaluator inputs use valid IDs, nonnegative
-- euro amounts, and at most one illegible signature per request. World.resolutions
-- completes all illegible signatures; Dossier.recorded need not be completed.
end Approvals
