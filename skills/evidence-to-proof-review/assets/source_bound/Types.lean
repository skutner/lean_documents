/- Support types of the source-bound profile (`source-bound-v1`).

   Generated facts, derived values and claims use these types. An audit copies this file
   unchanged into `models/SourceBound/Types.lean`; agents never edit it and never write
   values of these types by hand. Nothing here authenticates a source. -/
namespace SourceBound

/-- The currencies of version 1. Each has 100 minor units. -/
inductive Currency where
  | eur | ron | usd
  deriving DecidableEq, Repr

/-- An amount of money in minor units (cents, bani) of one currency. -/
structure Money (c : Currency) where
  minor : Int
  deriving DecidableEq, Repr

/-- A Gregorian calendar date as its ordinal day: 0001-01-01 is day 1. -/
structure Date where
  ordinal : Int
  deriving DecidableEq, Repr

/-- An exact ratio. Generated values have a positive denominator and are reduced. -/
structure Ratio where
  num : Int
  den : Nat
  deriving DecidableEq, Repr

end SourceBound
