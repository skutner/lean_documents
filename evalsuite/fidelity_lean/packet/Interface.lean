import Std

namespace Packet

inductive Result where
  | pass | fail
  deriving BEq, DecidableEq, Repr, Inhabited

structure Certificate where
  result : Result
  -- Gregorian ordinal day, with 0001-01-01 = 1.
  testedDay : Int
  deriving BEq, DecidableEq, Repr, Inhabited

structure Unit where
  id : String
  -- None means the certificate is absent from the supplied documents.
  certificate : Option Certificate
  deriving BEq, DecidableEq, Repr, Inhabited

structure World where
  reviewDay : Int
  maxAge : Nat
  units : List Unit
  deriving BEq, DecidableEq, Repr, Inhabited

structure Dossier where
  recorded : World
  -- Independent alternatives for each absent certificate, keyed by unit ID.
  unknowns : List (String × List Certificate)
  deriving BEq, DecidableEq, Repr, Inhabited

def replaceCertificate (w : World) (id : String) (c : Certificate) : World :=
  { w with units := w.units.map (fun u =>
      if u.id == id then { u with certificate := some c } else u) }

def possibleWorlds (d : Dossier) : List World :=
  d.unknowns.foldl (fun ws entry => ws.flatMap (fun w =>
    entry.2.map (replaceCertificate w entry.1))) [d.recorded]

abbrev Rule := World → Unit → Bool
abbrev ClaimTest := World → Bool

structure Model where
  rule : Rule
  -- Report-qualified IDs, such as "correct/C01" and "mixed/C01".
  claim : String → ClaimTest

-- Implement Candidate.model : Packet.Model. Rule and claim inputs are completed
-- worlds: every unit has a certificate. Dates are on or before the review day.
-- Missing certificates are completed with finite representative alternatives;
-- representatives are test inputs, not assertions of an absent document's date.
end Packet
