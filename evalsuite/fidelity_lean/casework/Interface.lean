import Std

namespace Casework

-- Python date.toordinal: 1 is Monday, 0001-01-01; weekday = (date - 1) % 7.
abbrev Date := Nat

inductive Class where
  | standard | complex | urgent
  deriving BEq, DecidableEq, Repr, Inhabited

inductive Channel where
  | electronic | hand | post
  deriving BEq, DecidableEq, Repr, Inhabited

inductive Decision where
  | granted | refused | pending
  deriving BEq, DecidableEq, Repr, Inhabited

structure Notice where
  id : String
  issued : Date
  cls : Class
  addressee : String
  channel : Channel
  sent : Option Date
  extension : Option String
  deriving BEq, DecidableEq, Repr, Inhabited

structure Extension where
  ref : String
  nid : String
  days : Nat
  decision : Decision
  date : Option Date
  deriving BEq, DecidableEq, Repr, Inhabited

structure World where
  periods : List (Class × Nat)
  amended : List (Class × Nat)
  effective : Date
  holidays : List Date
  -- Neutral document order: register-a.md, register-b.md. Only the index selects authority.
  registers : List (List Notice)
  inForce : Nat
  extensions : List Extension
  -- Completion keys are "sent:N..." and "decided:X..."; recorded dates stay recorded.
  resolutions : List (String × Date)
  -- Explicit row-confusion pairs supplied by the evaluator, not inferred from adjacency.
  neighbour : List (String × String)
  deriving BEq, DecidableEq, Repr, Inhabited

structure Dossier where
  recorded : World
  unknowns : List (String × List Date)
  deriving BEq, DecidableEq, Repr, Inhabited

structure Result where
  deadline : Date
  basic : Date
  took : Bool
  shifted : Bool
  received : Date
  late : Bool
  days : Nat
  deriving BEq, DecidableEq, Repr, Inhabited

abbrev Rule := World → String → Result
abbrev ClaimTest := World → Bool

structure Model where
  rule : Rule
  -- Report-qualified IDs, e.g. "correct/C01" and "mixed/C01".
  claim : String → ClaimTest

-- Implement Candidate.model : Casework.Model. Evaluator worlds have exactly two
-- registers with the same unique notice IDs, inForce in {0,1}, positive ordinal
-- dates and periods, one period per class, and at most one amendment per class.
-- Extension references and notice IDs agree and are unique. Completed worlds
-- resolve every unrecorded reply and every unrecorded granted decision; pending
-- requests need no decision date. Unknowns describe the Cartesian completions.
-- All tested notice IDs and mapped neighbours exist. The neighbouring-row
-- variant is defined on mapped rows and is identity elsewhere. Generator pairs
-- have fixed facts; counterfactual worlds may complete either row explicitly.
end Casework
