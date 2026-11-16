-- Authored candidate; no evaluator reference is imported.
namespace Candidate
open Packet

def meets (w : World) (u : Packet.Unit) : Bool :=
  match u.certificate with
  | none => false
  | some c => match c.result with
    | .fail => false
    | .pass => decide (c.testedDay + (w.maxAge : Int) >= w.reviewDay)
end Candidate
