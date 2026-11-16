import Std

namespace Deadlines

-- Dates are Gregorian ordinal days (0001-01-01 = 1), not YYYYMMDD numbers.
abbrev Date := Int

inductive Extension where
  | absent | granted | refused | undecided
  deriving BEq, DecidableEq, Repr, Inhabited

structure Notice where
  id : String
  issued : Date
  reply : Option Date
  extension : Extension
  days : Nat
  deriving BEq, DecidableEq, Repr, Inhabited

structure World where
  change : Date
  firstPeriod : Nat
  secondPeriod : Nat
  notices : List Notice
  grants : List (String × Bool)
  replies : List (String × Option Date)
  deriving BEq, DecidableEq, Repr, Inhabited

structure Dossier where
  recorded : World
  -- Independently choose one value from each list (Cartesian completion).
  grantChoices : List (String × List Bool)
  replyChoices : List (String × List (Option Date))
  deriving BEq, DecidableEq, Repr, Inhabited

structure Outcome where
  version : Nat
  deadline : Date
  daysLate : Option Int
  late : Bool
  onTime : Bool
  deriving BEq, DecidableEq, Repr, Inhabited

abbrev Rule := World → Notice → Outcome
abbrev ClaimTest := World → Bool

structure Model where
  rule : Rule
  -- IDs include the report variant: "correct/C01", "mixed/C01".
  claim : String → ClaimTest

-- Implement Candidate.model : Deadlines.Model.
-- Notice.reply and Notice.extension preserve recorded facts. Completed worlds
-- supply grants for undecided requests and replies for unrecorded reply dates.
-- A resolved reply of none means no reply received; a recorded none means unknown.
-- Evaluator worlds have unique notice IDs and complete resolutions, positive
-- periods, nonnegative extension days and valid ordinal dates.
end Deadlines
