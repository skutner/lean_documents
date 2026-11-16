import Std

namespace Procurement

inductive Role where
  | finance | manager | director | analyst | assistant
  deriving BEq, DecidableEq, Repr, Inhabited

inductive Currency where
  | eur | usd | gbp | chf
  deriving BEq, DecidableEq, Repr, Inhabited

structure Date where
  -- Gregorian ordinal: 0001-01-01 is 1, a Monday.
  ordinal : Nat
  -- Gregorian year * 12 + month - 1, so subtracting 1 crosses January correctly.
  month : Nat
  deriving BEq, DecidableEq, Repr, Inhabited

structure Staff where
  name : String
  role : Role
  department : String
  deriving BEq, DecidableEq, Repr, Inhabited

structure Request where
  id : String
  date : Date
  requester : String
  supplier : String
  amount : Option Nat
  currency : Currency
  -- none is an illegible signature; an empty list means no signatures.
  signers : List (Option String)
  deriving BEq, DecidableEq, Repr, Inhabited

structure Delegation where
  id : String
  person : String
  role : Role
  first : Nat
  last : Option Nat
  deriving BEq, DecidableEq, Repr, Inhabited

structure PolicyIssue where
  file : String
  issued : Nat
  low : Nat
  high : Nat
  exempt : Option Nat
  deriving BEq, DecidableEq, Repr, Inhabited

structure Rate where
  currency : Currency
  month : Nat
  -- Integer ten-thousandths of one euro per currency unit.
  value : Nat
  deriving BEq, DecidableEq, Repr, Inhabited

structure World where
  staff : List Staff
  policies : List PolicyIssue
  inForce : String
  amendmentIssued : Nat
  effective : Nat
  amendedLow : Nat
  amendedHigh : Nat
  delegations : List Delegation
  rates : List Rate
  requests : List Request
  -- Unknown amount and signature assignments are keyed by request ID.
  amounts : List (String × Nat)
  signers : List (String × String)
  -- Unknown last-day assignments are keyed by delegation ID.
  lastDays : List (String × Nat)
  deriving BEq, DecidableEq, Repr, Inhabited

structure Dossier where
  recorded : World
  amountAlternatives : List (String × List Nat)
  signerAlternatives : List (String × List String)
  lastDayAlternatives : List (String × List Nat)
  deriving BEq, DecidableEq, Repr, Inhabited

structure Assessment where
  id : String
  converted : Nat
  assessed : Nat
  tier : Nat
  counting : Nat
  finance : Bool
  complies : Bool
  deriving BEq, DecidableEq, Repr, Inhabited

abbrev Rule := World → List Assessment
abbrev ClaimTest := World → Bool

structure Model where
  rule : Rule
  -- Report-qualified IDs, for example "correct/C01" and "mixed/C01".
  claim : String → ClaimTest

-- Implement Candidate.model : Procurement.Model. Rule results preserve request
-- order. Dossier alternatives describe a Cartesian product of completed worlds;
-- recorded unreadable markers remain unchanged. Evaluator inputs have distinct
-- IDs and staff names, exactly two neutral policy files with one selected by
-- inForce, consistent ordinal/month dates, and all referenced people and rates.
-- Completed worlds assign every unknown exactly once; there is at most one
-- illegible signature per request. All alternatives are nonempty, amounts are
-- nonnegative, and dates are positive Gregorian ordinals. The interface contains
-- supplied facts and result types, not precomputed rule or claim answers.
end Procurement
